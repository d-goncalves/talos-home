"""Move existing data onto the deterministic NFS paths.

Three phases, deliberately separate so the irreversible one stands alone:

  plan       (default) show exactly what would move; changes nothing
  --execute  scale apps down, then rename/copy data into place. Additive:
             after this the data exists in BOTH the old and new locations.
  --finalize delete the old PVCs and StorageClasses so Flux can recreate them.
             THIS is the destructive step: local-path PVCs use
             reclaimPolicy Delete, so removing one erases the node-disk copy.

Running plan → execute → verify → finalize means there is always a moment where
you can stop and still have every byte.
"""

from __future__ import annotations

import json
import time

import click

from .. import settings
from ..console import fail, info, rule, success, warn
from ..shell import require, run, try_run

MIGRATION_NS = "storage-migration"
# Kept small and boring: it only needs mv and cp.
IMAGE = "busybox:1.36"


def _kget(*args: str) -> dict:
    return json.loads(run(["kubectl", "get", *args, "-o", "json"],
                          capture=True, echo=False))


def _nas() -> tuple[str, str]:
    cfg = settings.load()
    nas = cfg.get("nas") or {}
    if not nas.get("ip") or not nas.get("data_path"):
        fail("provision.toml needs [nas].ip and [nas].data_path")
    return nas["ip"], nas["data_path"].rstrip("/")


def _desired_subdir(namespace: str, name: str) -> str:
    """Must match subDir in the StorageClasses exactly."""
    return f"{namespace}-{name}"


def build_plan() -> dict:
    """Work out what has to move, by comparing live state to the target."""
    nas_ip, data_path = _nas()

    # share path per storage class, from the live cluster (already substituted)
    classes = {}
    for sc in _kget("sc")["items"]:
        if sc.get("provisioner") != "nfs.csi.k8s.io":
            continue
        params = sc.get("parameters") or {}
        classes[sc["metadata"]["name"]] = params.get("share", "")

    pvs = {}
    for pv in _kget("pv")["items"]:
        claim = pv["spec"].get("claimRef") or {}
        attrs = (pv["spec"].get("csi") or {}).get("volumeAttributes") or {}
        if claim.get("name"):
            pvs[(claim.get("namespace"), claim["name"])] = {
                "pv": pv["metadata"]["name"],
                "subdir": attrs.get("subdir", ""),
                "share": attrs.get("share", ""),
            }

    renames, copies, already = [], [], []

    for pvc in _kget("pvc", "-A")["items"]:
        ns = pvc["metadata"]["namespace"]
        name = pvc["metadata"]["name"]
        sc = pvc["spec"].get("storageClassName")
        want = _desired_subdir(ns, name)
        live = pvs.get((ns, name), {})

        if sc and sc.startswith("nfs-"):
            current = live.get("subdir", "")
            if not current or current == want:
                already.append(f"{ns}/{name}")
            elif current.startswith("pvc-"):
                renames.append({
                    "namespace": ns, "pvc": name,
                    "share": live.get("share") or classes.get(sc, ""),
                    "from": current, "to": want,
                })
        elif sc == "local-path":
            # Only the ones the repo now points at NFS need copying; anything
            # still declared local-path in git is intentionally disposable.
            target_sc = _target_class(ns, name)
            if target_sc:
                copies.append({
                    "namespace": ns, "pvc": name,
                    "share": classes.get(target_sc, f"{data_path}/{target_sc[4:]}"),
                    "to": want, "storage_class": target_sc,
                })

    return {
        "nas_ip": nas_ip,
        "renames": renames,
        "copies": copies,
        "already": already,
    }


def _target_class(namespace: str, name: str) -> str | None:
    """The storage class the repo now declares for this PVC, if it changed."""
    import yaml

    rendered = run(["kubectl", "kustomize", str(settings.apps_dir())],
                   capture=True, echo=False)
    for doc in yaml.safe_load_all(rendered):
        if not doc or doc.get("kind") != "PersistentVolumeClaim":
            continue
        meta = doc.get("metadata", {})
        if meta.get("namespace") == namespace and meta.get("name") == name:
            sc = doc["spec"].get("storageClassName")
            return sc if sc and sc.startswith("nfs-") else None
    return None


def _owners(namespace: str, pvc: str) -> list[tuple[str, str]]:
    """Workloads mounting this PVC, as (kind, name)."""
    out = []
    for kind in ("deploy", "statefulset"):
        ok, _ = try_run(["kubectl", "get", kind, "-n", namespace, "-o", "json"],
                        capture=True, echo=False)
        if not ok:
            continue
        for item in _kget(kind, "-n", namespace)["items"]:
            spec = item["spec"]["template"]["spec"]
            claims = {
                v["persistentVolumeClaim"]["claimName"]
                for v in (spec.get("volumes") or [])
                if v.get("persistentVolumeClaim")
            }
            if pvc in claims:
                out.append((kind, item["metadata"]["name"]))
    return out


def _job(name: str, nas_ip: str, share: str, script: str,
         src_pvc: str | None = None) -> str:
    src_mount = ""
    src_volume = ""
    if src_pvc:
        src_mount = "\n            - name: src\n              mountPath: /src\n              readOnly: true"
        src_volume = f"\n        - name: src\n          persistentVolumeClaim:\n            claimName: {src_pvc}"

    return f"""apiVersion: batch/v1
kind: Job
metadata:
  name: {name}
  namespace: {MIGRATION_NS}
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 600
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: {IMAGE}
          command: ["/bin/sh", "-c"]
          args:
            - |
{script}
          volumeMounts:
            - name: share
              mountPath: /share{src_mount}
      volumes:
        - name: share
          nfs:
            server: {nas_ip}
            path: {share}{src_volume}
"""


def _run_job(manifest: str, name: str, timeout: int = 1800) -> bool:
    try_run(["kubectl", "delete", "job", "-n", MIGRATION_NS, name,
             "--ignore-not-found"], capture=True, echo=False)
    ok, err = try_run(["kubectl", "apply", "-f", "-"],
                      input_text=manifest, capture=True, echo=False)
    if not ok:
        warn(f"could not create job {name}: {err.strip()}")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        _, succeeded = try_run(
            ["kubectl", "get", "job", "-n", MIGRATION_NS, name,
             "-o", "jsonpath={.status.succeeded}"], capture=True, echo=False)
        _, failed = try_run(
            ["kubectl", "get", "job", "-n", MIGRATION_NS, name,
             "-o", "jsonpath={.status.failed}"], capture=True, echo=False)
        if succeeded.strip() == "1":
            return True
        if failed.strip() and failed.strip() != "0":
            logs = run(["kubectl", "logs", "-n", MIGRATION_NS, f"job/{name}",
                        "--tail=20"], capture=True, check=False, echo=False)
            warn(f"job {name} failed:\n{logs}")
            return False
        time.sleep(5)
    warn(f"job {name} timed out")
    return False


@click.group("storage")
def storage() -> None:
    """Audit and migrate persistent data."""


@storage.command("migrate")
@click.option("--execute", is_flag=True, help="Scale apps down and move data (additive).")
@click.option("--finalize", is_flag=True, help="Delete old PVCs and StorageClasses (destructive).")
@click.option("--yes", is_flag=True, help="Skip confirmations.")
def storage_migrate(execute: bool, finalize: bool, yes: bool) -> None:
    """Move data onto deterministic NFS paths. Dry-run unless told otherwise."""
    require("kubectl")
    if execute and finalize:
        fail("run --execute first, verify, then --finalize separately")

    plan = build_plan()

    rule("Migration plan")
    if plan["already"]:
        info(f"{len(plan['already'])} volume(s) already on the right path")

    if plan["renames"]:
        click.echo()
        click.echo("  Rename on the NAS (instant, same filesystem):")
        for r in plan["renames"]:
            click.echo(f"    {r['namespace']}/{r['pvc']}")
            click.echo(click.style(f"      {r['share']}/{r['from']}", dim=True))
            click.echo(click.style(f"   →  {r['share']}/{r['to']}", dim=True))

    if plan["copies"]:
        click.echo()
        click.echo("  Copy local-path → NAS (app stopped during copy):")
        for c in plan["copies"]:
            owners = _owners(c["namespace"], c["pvc"])
            who = ", ".join(f"{k}/{n}" for k, n in owners) or "no workload"
            click.echo(f"    {c['namespace']}/{c['pvc']}  [{who}]")
            click.echo(click.style(f"   →  {c['share']}/{c['to']}", dim=True))

    if not plan["renames"] and not plan["copies"]:
        click.echo()
        success("nothing to migrate")
        return

    if not execute and not finalize:
        click.echo()
        warn("DRY RUN — nothing changed.")
        info("Move the data with:  ./provision storage migrate --execute")
        return

    if finalize:
        _finalize(plan, yes)
        return

    _execute(plan, yes)


def _execute(plan: dict, yes: bool) -> None:
    click.echo()
    warn("Apps that own migrating volumes will be scaled to zero.")
    warn("Nothing is deleted in this phase: data ends up in both places.")
    if not yes and not click.confirm("  Proceed?", default=False):
        fail("aborted")

    run(["kubectl", "create", "namespace", MIGRATION_NS,
         "--dry-run=client", "-o", "yaml"], capture=True, echo=False)
    manifest = run(["kubectl", "create", "namespace", MIGRATION_NS,
                    "--dry-run=client", "-o", "yaml"], capture=True, echo=False)
    try_run(["kubectl", "apply", "-f", "-"], input_text=manifest,
            capture=True, echo=False)

    # ── scale down anything holding a volume we are about to touch ──────────
    # Renames count: moving a directory out from under a live NFS mount leaves
    # the pod holding a filehandle to a path that no longer resolves.
    scaled: list[tuple[str, str, str, int]] = []
    seen: set[tuple[str, str, str]] = set()
    for c in plan["copies"] + plan["renames"]:
        for kind, name in _owners(c["namespace"], c["pvc"]):
            if (c["namespace"], kind, name) in seen:
                continue
            seen.add((c["namespace"], kind, name))
            _, replicas = try_run(
                ["kubectl", "get", kind, "-n", c["namespace"], name,
                 "-o", "jsonpath={.spec.replicas}"], capture=True, echo=False)
            scaled.append((c["namespace"], kind, name, int(replicas.strip() or 1)))
            run(["kubectl", "scale", kind, "-n", c["namespace"], name,
                 "--replicas=0"], check=False)

    if scaled:
        info("waiting for pods to terminate")
        time.sleep(20)

    ok_all = True

    # ── renames ─────────────────────────────────────────────────────────────
    for i, r in enumerate(plan["renames"]):
        script = (
            f'              set -e\n'
            f'              if [ -d "/share/{r["to"]}" ]; then\n'
            f'                echo "target exists, nothing to do"; exit 0; fi\n'
            f'              test -d "/share/{r["from"]}"\n'
            f'              mv "/share/{r["from"]}" "/share/{r["to"]}"\n'
            f'              ls -la "/share/{r["to"]}" | head -5\n'
        )
        name = f"rename-{i}-{r['pvc'][:30]}".lower().replace("_", "-")
        info(f"renaming {r['namespace']}/{r['pvc']}")
        if not _run_job(_job(name, plan["nas_ip"], r["share"], script), name):
            ok_all = False

    # ── copies ──────────────────────────────────────────────────────────────
    for i, c in enumerate(plan["copies"]):
        script = (
            f'              set -e\n'
            f'              mkdir -p "/share/{c["to"]}"\n'
            f'              cp -a /src/. "/share/{c["to"]}/"\n'
            f'              echo "--- copied ---"\n'
            f'              du -sh "/share/{c["to"]}" || true\n'
        )
        name = f"copy-{i}-{c['pvc'][:30]}".lower().replace("_", "-")
        info(f"copying {c['namespace']}/{c['pvc']}")
        job = _job(name, plan["nas_ip"], c["share"], script, src_pvc=c["pvc"])
        # The source PVC lives in the app namespace, so the job must too.
        job = job.replace(f"namespace: {MIGRATION_NS}", f"namespace: {c['namespace']}")
        try_run(["kubectl", "delete", "job", "-n", c["namespace"], name,
                 "--ignore-not-found"], capture=True, echo=False)
        ok, err = try_run(["kubectl", "apply", "-f", "-"], input_text=job,
                          capture=True, echo=False)
        if not ok:
            warn(f"could not create job: {err.strip()}"); ok_all = False; continue

        deadline = time.time() + 1800
        done = False
        while time.time() < deadline:
            _, s = try_run(["kubectl", "get", "job", "-n", c["namespace"], name,
                            "-o", "jsonpath={.status.succeeded}"],
                           capture=True, echo=False)
            _, f_ = try_run(["kubectl", "get", "job", "-n", c["namespace"], name,
                             "-o", "jsonpath={.status.failed}"],
                            capture=True, echo=False)
            if s.strip() == "1":
                done = True; break
            if f_.strip() and f_.strip() != "0":
                warn(run(["kubectl", "logs", "-n", c["namespace"], f"job/{name}",
                          "--tail=20"], capture=True, check=False, echo=False))
                break
            time.sleep(5)
        if not done:
            ok_all = False

    # ── bring the apps back ─────────────────────────────────────────────────
    for ns, kind, name, replicas in scaled:
        run(["kubectl", "scale", kind, "-n", ns, name,
             f"--replicas={replicas}"], check=False)

    click.echo()
    if not ok_all:
        fail("some jobs failed — nothing was deleted, fix and re-run --execute")
    success("data copied; originals untouched")
    info("Verify the apps, then: ./provision storage migrate --finalize")


def _finalize(plan: dict, yes: bool) -> None:
    click.echo()
    warn("DESTRUCTIVE. This deletes:")
    warn(f"  {len(plan['copies'])} local-path PVCs — reclaimPolicy Delete "
         "erases the node-disk copy")
    warn("  every nfs- StorageClass, so Flux recreates them with the new parameters")
    warn("Only do this after confirming the apps work on the copied data.")
    if not yes:
        typed = click.prompt("  Type 'delete' to confirm", default="", show_default=False)
        if typed != "delete":
            fail("aborted")

    for c in plan["copies"]:
        for kind, name in _owners(c["namespace"], c["pvc"]):
            run(["kubectl", "scale", kind, "-n", c["namespace"], name,
                 "--replicas=0"], check=False)
    time.sleep(15)

    for c in plan["copies"]:
        run(["kubectl", "delete", "pvc", "-n", c["namespace"], c["pvc"],
             "--ignore-not-found", "--timeout=120s"], check=False)

    for sc in _kget("sc")["items"]:
        if sc.get("provisioner") == "nfs.csi.k8s.io":
            run(["kubectl", "delete", "sc", sc["metadata"]["name"],
                 "--ignore-not-found"], check=False)

    success("old PVCs and StorageClasses removed")
    info("Merge the branch and push; Flux recreates everything on the new paths.")


storage.add_command(storage_migrate)
