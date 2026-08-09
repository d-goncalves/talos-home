"""Storage rules that make a cluster rebuild non-destructive.

Two invariants, both learned the hard way:

1. Anything worth keeping lives on NFS at a *deterministic* path. Dynamic NFS
   provisioning defaults to a random `pvc-<uuid>` subdirectory, so a rebuilt
   cluster mints new UUIDs and mounts empty directories while the old data sits
   orphaned. `subDir: ${pvc.metadata.namespace}-${pvc.metadata.name}` fixes that.

2. Every Deployment holding an NFS volume uses `Recreate`. Mounts are `nolock`,
   so locking is local-only and two pods writing the same SQLite file will
   corrupt it. RollingUpdate guarantees an overlap.
"""

from __future__ import annotations

import yaml

# PVCs allowed to stay on local-path, with the reason. Everything else on
# local-path is a bug: its data does not survive a rebuild.
DISPOSABLE = {
    ("monitoring", "storage-loki-0"): "logs; Loki does not support NFS",
    ("immich", "immich-valkey"): "redis cache, rebuilt on start",
    ("gitea", "gitea-shared-storage"): "unused; real data is on gitea-shared-storage-nfs",
}

# Prefix match, for volumeClaimTemplates whose names carry a StatefulSet suffix.
DISPOSABLE_PREFIXES = {
    ("monitoring", "prometheus-"): "metrics; Prometheus TSDB is unsupported on NFS",
    ("monitoring", "kube-prometheus-stack-grafana"): "dashboards are provisioned by the chart",
    ("monitoring", "storage-loki"): "logs; Loki does not support NFS",
}


def _disposable(namespace: str, name: str) -> str | None:
    if (namespace, name) in DISPOSABLE:
        return DISPOSABLE[(namespace, name)]
    for (ns, prefix), reason in DISPOSABLE_PREFIXES.items():
        if ns == namespace and name.startswith(prefix):
            return reason
    return None


def audit(rendered: str) -> list[str]:
    """Check a rendered kustomize build against both invariants."""
    docs = [d for d in yaml.safe_load_all(rendered) if d]
    problems: list[str] = []

    nfs_claims: set[tuple[str, str]] = set()

    for doc in docs:
        if doc.get("kind") != "PersistentVolumeClaim":
            continue
        meta = doc.get("metadata", {})
        ns, name = meta.get("namespace", "default"), meta.get("name", "?")
        sc = doc.get("spec", {}).get("storageClassName")

        if sc and str(sc).startswith("nfs-"):
            nfs_claims.add((ns, name))
        elif sc == "local-path":
            if not _disposable(ns, name):
                problems.append(
                    f"{ns}/{name} is on local-path and not on the disposable "
                    f"allowlist — its data would be lost on a rebuild"
                )
        elif sc is None:
            # Statically bound to a PV by volumeName; that path is explicit in
            # git, so it is already deterministic.
            nfs_claims.add((ns, name))

    for doc in docs:
        if doc.get("kind") != "Deployment":
            continue
        meta = doc.get("metadata", {})
        ns, name = meta.get("namespace", "default"), meta.get("name", "?")
        spec = doc.get("spec", {})
        volumes = (spec.get("template", {}).get("spec", {}).get("volumes") or [])
        claims = {
            v["persistentVolumeClaim"]["claimName"]
            for v in volumes
            if v.get("persistentVolumeClaim")
        }
        if not any((ns, c) in nfs_claims for c in claims):
            continue
        strategy = (spec.get("strategy") or {}).get("type", "RollingUpdate")
        if strategy != "Recreate":
            problems.append(
                f"{ns}/{name} holds an NFS volume but uses {strategy} — two pods "
                f"on a nolock mount will corrupt SQLite; use Recreate"
            )

    return problems


def storage_class_problems(rendered: str) -> list[str]:
    """Every NFS StorageClass must pin a deterministic subDir."""
    problems = []
    for doc in (d for d in yaml.safe_load_all(rendered) if d):
        if doc.get("kind") != "StorageClass":
            continue
        name = doc.get("metadata", {}).get("name", "?")
        if doc.get("provisioner") != "nfs.csi.k8s.io":
            continue
        params = doc.get("parameters") or {}
        sub = str(params.get("subDir", ""))
        if "pvc.metadata.name" not in sub:
            problems.append(
                f"StorageClass {name} has no deterministic subDir — volumes would "
                f"land on a random pvc-<uuid> path and be lost on a rebuild"
            )
        if params.get("onDelete") != "retain":
            problems.append(
                f"StorageClass {name} does not set onDelete: retain — deleting a "
                f"PVC would delete the data on the NAS"
            )
    return problems
