"""NAS reachability checks.

Two levels, because they catch different failures:

  check   from the workstation — is this node's IP on the export allow-list?
  verify  from inside the cluster — can the CSI driver actually provision?

`verify` is the one that must gate app deployment. An app that starts without
its NFS volume does not fail cleanly: it initialises an empty database on
local storage, or writes to a path that later gets shadowed by a mount. Both
leave you worse off than not deploying at all.
"""

from __future__ import annotations

import json
import time

import click

from .. import settings
from ..console import fail, info, rule, success, warn
from ..shell import require, run, try_run

CHECK_NAMESPACE = "default"
CHECK_PVC = "provision-nas-check"


def _storage_classes() -> list[str]:
    ok, _ = try_run(["kubectl", "get", "sc", "-o", "json"], capture=True, echo=False)
    if not ok:
        return []
    raw = run(["kubectl", "get", "sc", "-o", "json"], capture=True, echo=False)
    return [
        item["metadata"]["name"]
        for item in json.loads(raw).get("items", [])
        if item.get("provisioner") == "nfs.csi.k8s.io"
    ]


def _cleanup(storage_class: str) -> None:
    """Remove the probe PVC and the PV it bound, which Retain leaves behind."""
    ok, out = try_run(
        ["kubectl", "get", "pvc", CHECK_PVC, "-n", CHECK_NAMESPACE,
         "-o", "jsonpath={.spec.volumeName}"],
        capture=True, echo=False,
    )
    pv = out.strip() if ok else ""

    try_run(
        ["kubectl", "delete", "pvc", CHECK_PVC, "-n", CHECK_NAMESPACE,
         "--ignore-not-found", "--timeout=60s"],
        capture=True, echo=False,
    )
    # reclaimPolicy is Retain on every nfs class here, so the PV outlives the
    # claim and would accumulate on each run.
    if pv:
        try_run(["kubectl", "delete", "pv", pv, "--ignore-not-found", "--timeout=60s"],
                capture=True, echo=False)


@click.group("nas")
def nas() -> None:
    """Check that the cluster can reach the NAS."""


@click.command("verify")
@click.option("--storage-class", default=None, help="StorageClass to probe.")
@click.option("--timeout", default=120, help="Seconds to wait for the PVC to bind.")
def nas_verify(storage_class: str | None, timeout: int) -> None:
    """Prove the CSI driver can provision on the NAS, by binding a real PVC.

    A dynamically provisioned NFS PVC only reaches Bound once the driver has
    created its subdirectory on the share, so a successful bind proves the node
    is permitted, the export is mounted and the share is writable — all the
    things that silently break app data if they are not true.
    """
    require("kubectl")

    classes = _storage_classes()
    if not classes:
        fail("no nfs.csi.k8s.io StorageClasses found — is the CSI driver deployed?")

    chosen = storage_class or sorted(classes)[0]
    if chosen not in classes:
        fail(f"unknown StorageClass '{chosen}'. Available: {', '.join(sorted(classes))}")

    rule("NAS write test")
    info(f"provisioning a 1Mi PVC on {chosen}")

    _cleanup(chosen)

    manifest = f"""apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {CHECK_PVC}
  namespace: {CHECK_NAMESPACE}
  annotations:
    kustomize.toolkit.fluxcd.io/prune: disabled
spec:
  storageClassName: {chosen}
  accessModes: [ReadWriteMany]
  resources:
    requests:
      storage: 1Mi
"""
    ok, err = try_run(["kubectl", "apply", "-f", "-"],
                      input_text=manifest, capture=True, echo=False)
    if not ok:
        fail(f"could not create the probe PVC: {err.strip()}")

    deadline = time.time() + timeout
    phase = ""
    while time.time() < deadline:
        _, phase = try_run(
            ["kubectl", "get", "pvc", CHECK_PVC, "-n", CHECK_NAMESPACE,
             "-o", "jsonpath={.status.phase}"],
            capture=True, echo=False,
        )
        phase = phase.strip()
        if phase == "Bound":
            break
        time.sleep(3)

    if phase != "Bound":
        _, events = try_run(
            ["kubectl", "get", "events", "-n", CHECK_NAMESPACE,
             "--field-selector", f"involvedObject.name={CHECK_PVC}",
             "-o", "jsonpath={.items[-3:].message}"],
            capture=True, echo=False,
        )
        _cleanup(chosen)
        fail(
            f"PVC did not bind within {timeout}s (phase={phase or 'unknown'}).\n"
            f"  {events.strip()}\n"
            "  The cluster cannot write to the NAS. Do NOT deploy apps."
        )

    _cleanup(chosen)
    success(f"NAS is writable via {chosen}")


@click.command("check")
def nas_check() -> None:
    """Check the export allow-list from the workstation, without the cluster."""
    from .preflight import nfs_export_problems

    cfg = settings.load()
    nas_ip = (cfg.get("nas") or {}).get("ip")
    if not nas_ip:
        fail("provision.toml has no [nas].ip")

    rule("NAS exports")
    require("showmount")
    run(["showmount", "-e", nas_ip], echo=False, check=False)

    problems = nfs_export_problems()
    if problems:
        for p in problems:
            warn(p)
        raise SystemExit(1)
    success("every configured node is on the export allow-list")


nas.add_command(nas_verify)
nas.add_command(nas_check)
