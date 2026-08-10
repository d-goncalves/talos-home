"""Move existing data onto the deterministic NFS paths.

Three phases, deliberately separate so the irreversible one stands alone:

  plan       (default) show exactly what would move; changes nothing
  --execute  scale apps down, COPY data into the new paths, verify, scale back
             up. Strictly additive: nothing is moved or deleted, the live PVs
             still point at the old paths, and the apps come back working on
             them. An earlier version used `mv` here, which silently broke
             every app the moment its directory moved out from under the mount.
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

# Jobs run in default rather than a dedicated namespace. Creating one adds a
# failure mode for no benefit — and namespace *deletion* on this cluster hangs
# on a stale metrics.k8s.io APIService, so the namespace would linger anyway.
MIGRATION_NS = "default"
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

    nfs_copies, copies, already = [], [], []

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
                # Copied, never moved: the live PV still points at `current`,
                # so the app keeps working until --finalize swaps it over.
                nfs_copies.append({
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
        "nfs_copies": nfs_copies,
        "copies": copies,
        "already": already,
    }


def _wait_pods_gone(namespace: str, pvc: str, timeout: int = 300) -> bool:
    """Block until no pod in the namespace still mounts this PVC.

    A fixed sleep is not enough. SQLite checkpoints and removes its -wal and
    -shm files as it shuts down, so a copy that starts too early sees them in
    the directory listing and then fails to stat them — and, worse, could
    capture a database mid-checkpoint. Wait for the writer to actually be gone.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok, raw = try_run(["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
                          capture=True, echo=False)
        if not ok:
            return True
        holders = 0
        for pod in json.loads(raw).get("items", []):
            if pod["status"].get("phase") in ("Succeeded", "Failed"):
                continue
            claims = {
                v["persistentVolumeClaim"]["claimName"]
                for v in (pod["spec"].get("volumes") or [])
                if v.get("persistentVolumeClaim")
            }
            if pvc in claims:
                holders += 1
        if holders == 0:
            return True
        time.sleep(3)
    return False


def _ensure_shares(nas_ip: str, data_path: str, shares: set[str]) -> bool:
    """Create the parent share directories on the NAS.

    The CSI driver creates the *subdirectory* under a share, but the share
    itself must already exist — an NFS mount of a missing path just fails with
    "No such file or directory". Classes that had never been used had no
    directory, which is what stalled the first migration attempt.
    """
    names = sorted({s.rstrip("/").rsplit("/", 1)[-1] for s in shares if s})
    if not names:
        return True
    mk = "\n".join(f'              mkdir -p "/root/{n}"' for n in names)
    script = "              set -e\n" + mk + '\n              echo "shares ready"\n'
    job = _job("ensure-shares", nas_ip, data_path, script)
    info(f"ensuring {len(names)} share directories exist on the NAS")
    return _run_job(job, "ensure-shares", timeout=300)


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
@click.option(
    "--only",
    default=None,
    help="Restrict to one namespace. Prove the flow on something cheap first.",
)
def storage_migrate(execute: bool, finalize: bool, yes: bool, only: str | None) -> None:
    """Move data onto deterministic NFS paths. Dry-run unless told otherwise."""
    require("kubectl")
    if execute and finalize:
        fail("run --execute first, verify, then --finalize separately")

    plan = build_plan()

    if only:
        plan["nfs_copies"] = [c for c in plan["nfs_copies"] if c["namespace"] == only]
        plan["copies"] = [c for c in plan["copies"] if c["namespace"] == only]
        plan["already"] = [a for a in plan["already"] if a.startswith(f"{only}/")]
        info(f"restricted to namespace '{only}'")

    rule("Migration plan")
    if plan["already"]:
        info(f"{len(plan['already'])} volume(s) already on the right path")

    if plan["nfs_copies"]:
        click.echo()
        click.echo("  Copy within the NAS (old path left intact):")
        for r in plan["nfs_copies"]:
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

    if not plan["nfs_copies"] and not plan["copies"]:
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

    _, data_path = _nas()
    shares = {c["share"] for c in plan["copies"] + plan["nfs_copies"]}
    if not _ensure_shares(plan["nas_ip"], data_path, shares):
        fail("could not create the share directories — nothing was changed")

    # ── scale down anything holding a volume we are about to touch ──────────
    # Renames count: moving a directory out from under a live NFS mount leaves
    # the pod holding a filehandle to a path that no longer resolves.
    scaled: list[tuple[str, str, str, int]] = []
    seen: set[tuple[str, str, str]] = set()
    for c in plan["copies"] + plan["nfs_copies"]:
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

    # Wait for every writer to actually exit, per volume.
    for c in plan["copies"] + plan["nfs_copies"]:
        if not _wait_pods_gone(c["namespace"], c["pvc"]):
            warn(f"pods still hold {c['namespace']}/{c['pvc']} — skipping to stay safe")
            fail("could not quiesce all writers; nothing was deleted")

    ok_all = True

    # ── copy NFS volumes to their deterministic path ────────────────────────
    for i, r in enumerate(plan["nfs_copies"]):
        script = (
            f'              set -e\n'
            f'              test -d "/share/{r["from"]}"\n'
            f'              mkdir -p "/share/{r["to"]}"\n'
            f'              cp -a "/share/{r["from"]}/." "/share/{r["to"]}/"\n'
            f'              find "/share/{r["to"]}" -type d -exec chmod 2770 {{}} + 2>/dev/null || true\n'
            f'              find "/share/{r["to"]}" -type f -exec chmod 660 {{}} + 2>/dev/null || true\n'
            f'              src=$(find "/share/{r["from"]}" | wc -l)\n'
            f'              dst=$(find "/share/{r["to"]}" | wc -l)\n'
            f'              echo "entries: src=$src dst=$dst"\n'
            f'              [ "$src" -eq "$dst" ] || {{ echo "MISMATCH"; exit 1; }}\n'
            f'              echo "verified"\n'
        )
        name = f"nfscopy-{i}-{r['pvc'][:26]}".lower().replace("_", "-")
        info(f"copying {r['namespace']}/{r['pvc']} (old path left intact)")
        if not _run_job(_job(name, plan["nas_ip"], r["share"], script), name):
            ok_all = False

    # ── copies ──────────────────────────────────────────────────────────────
    for i, c in enumerate(plan["copies"]):
        script = (
            f'              set -e\n'
            f'              mkdir -p "/share/{c["to"]}"\n'
            f'              cp -a /src/. "/share/{c["to"]}/"\n'
            f'              find "/share/{c["to"]}" -type d -exec chmod 2770 {{}} + 2>/dev/null || true\n'
            f'              find "/share/{c["to"]}" -type f -exec chmod 660 {{}} + 2>/dev/null || true\n'
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
