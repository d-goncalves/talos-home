"""Tests for the storage invariants that make a rebuild non-destructive."""

from __future__ import annotations

import textwrap

from provision import storage


def build(*docs: str) -> str:
    return "\n---\n".join(textwrap.dedent(d).strip() for d in docs)


PVC_NFS = """
    apiVersion: v1
    kind: PersistentVolumeClaim
    metadata: {name: sonarr-config, namespace: servarr}
    spec: {storageClassName: nfs-servarr-config}
"""

PVC_LOCAL = """
    apiVersion: v1
    kind: PersistentVolumeClaim
    metadata: {name: sonarr-config, namespace: servarr}
    spec: {storageClassName: local-path}
"""


def deploy(strategy: str | None, claim: str = "sonarr-config") -> str:
    lines = [
        "apiVersion: apps/v1",
        "kind: Deployment",
        "metadata: {name: sonarr, namespace: servarr}",
        "spec:",
    ]
    if strategy:
        lines.append(f"  strategy: {{type: {strategy}}}")
    lines += [
        "  template:",
        "    spec:",
        "      volumes:",
        "        - name: config",
        f"          persistentVolumeClaim: {{claimName: {claim}}}",
    ]
    return "\n".join(lines)


# ── local-path detection ─────────────────────────────────────────────────────

def test_local_path_pvc_is_flagged():
    problems = storage.audit(build(PVC_LOCAL))
    assert any("local-path" in p for p in problems)


def test_nfs_pvc_is_accepted():
    assert storage.audit(build(PVC_NFS, deploy("Recreate"))) == []


def test_disposable_allowlist_is_respected():
    doc = """
        apiVersion: v1
        kind: PersistentVolumeClaim
        metadata: {name: immich-valkey, namespace: immich}
        spec: {storageClassName: local-path}
    """
    assert storage.audit(build(doc)) == []


def test_prometheus_prefix_is_disposable():
    doc = """
        apiVersion: v1
        kind: PersistentVolumeClaim
        metadata: {name: prometheus-kube-prometheus-stack-db-0, namespace: monitoring}
        spec: {storageClassName: local-path}
    """
    assert storage.audit(build(doc)) == []


def test_statically_bound_pvc_is_accepted():
    """No storageClassName means a volumeName binding, whose path is explicit
    in git and therefore already deterministic."""
    doc = """
        apiVersion: v1
        kind: PersistentVolumeClaim
        metadata: {name: servarr-data, namespace: servarr}
        spec: {volumeName: servarr-data}
    """
    assert storage.audit(build(doc)) == []


# ── single-writer rule ───────────────────────────────────────────────────────

def test_rolling_update_on_nfs_is_flagged():
    problems = storage.audit(build(PVC_NFS, deploy("RollingUpdate")))
    assert any("Recreate" in p for p in problems)


def test_missing_strategy_defaults_to_rolling_and_is_flagged():
    """Kubernetes defaults to RollingUpdate, so an absent strategy is unsafe."""
    problems = storage.audit(build(PVC_NFS, deploy(None)))
    assert any("Recreate" in p for p in problems)


def test_rolling_update_without_nfs_is_fine():
    assert storage.audit(build(deploy("RollingUpdate", claim="nope"))) == []


# ── storage class rules ──────────────────────────────────────────────────────

GOOD_SC = """
    apiVersion: storage.k8s.io/v1
    kind: StorageClass
    metadata: {name: nfs-servarr-config}
    provisioner: nfs.csi.k8s.io
    parameters:
      subDir: ${pvc.metadata.namespace}-${pvc.metadata.name}
      onDelete: retain
"""


def test_deterministic_subdir_passes():
    assert storage.storage_class_problems(build(GOOD_SC)) == []


def test_random_uuid_subdir_is_flagged():
    """Without subDir the driver invents pvc-<uuid>, which a rebuilt cluster
    can never find again — the whole reason this audit exists."""
    sc = GOOD_SC.replace("      subDir: ${pvc.metadata.namespace}-${pvc.metadata.name}\n", "")
    problems = storage.storage_class_problems(build(sc))
    assert any("deterministic" in p for p in problems)


def test_ondelete_must_retain():
    sc = GOOD_SC.replace("onDelete: retain", "onDelete: delete")
    problems = storage.storage_class_problems(build(sc))
    assert any("onDelete" in p for p in problems)


def test_non_nfs_provisioner_is_ignored():
    sc = GOOD_SC.replace("nfs.csi.k8s.io", "rancher.io/local-path")
    assert storage.storage_class_problems(build(sc)) == []
