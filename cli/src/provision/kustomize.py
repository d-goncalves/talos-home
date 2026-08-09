"""Generate kubernetes/apps/kustomization.yaml from provision.toml."""

from __future__ import annotations

from . import settings
from .settings import App

HEADER = """\
# GENERATED FILE — do not edit by hand.
#
# Source of truth: provision.toml at the repo root, [apps] table.
# Regenerate with: provision sync
#
# Disabled apps are omitted from `resources`. Both Flux Kustomizations run with
# prune: true, so Flux deletes their workloads and the memory comes back.
#
# The patches at the bottom mark every Namespace, PersistentVolumeClaim and
# PersistentVolume as prune-exempt. Without them, pruning an app would delete
# its Namespace, and namespace deletion cascades to PVCs regardless of the
# Retain reclaim policy — the app would come back empty on re-enable, with its
# data orphaned in a stale NFS subdirectory.
#
# ORDERING: Flux reads that annotation from the object in the CLUSTER, not from
# git. An object must already carry it before it leaves this file, or it gets
# pruned anyway. `provision apps disable` enforces this; `provision apps protect`
# applies the annotations immediately instead of waiting for a reconcile.
"""

# Applied to every storage-bearing object in the build. metadata.name is
# required for the merge to parse but is ignored: `target` decides what is
# patched.
#
# prune: disabled  — survives an app being disabled, so data is kept.
# ssa: IfNotPresent — create on a fresh cluster, then never update.
#
# The ssa policy is what keeps Flux from fighting over an immutable spec. A
# bound PVC has volumeName set by kube-controller-manager, but git does not
# specify it; kustomize-controller owned that field and kept trying to clear
# it, which the API server rejects ("spec is immutable after creation"). One
# such PVC failed the dry-run and took the whole apps Kustomization down with
# it. IfNotPresent still creates PVCs on a rebuild, so recovery is unaffected.
#
# Namespaces keep normal reconcile behaviour — only their pruning is disabled.
PRUNE_PROTECTED_KINDS = ("Namespace", "PersistentVolumeClaim", "PersistentVolume")

# Kinds whose spec is immutable once bound, so Flux must not try to update them.
CREATE_ONLY_KINDS = ("PersistentVolumeClaim", "PersistentVolume")


def _prune_protection() -> str:
    blocks = ["patches:"]
    for kind in PRUNE_PROTECTED_KINDS:
        annotations = ["          kustomize.toolkit.fluxcd.io/prune: disabled"]
        if kind in CREATE_ONLY_KINDS:
            annotations.append("          kustomize.toolkit.fluxcd.io/ssa: IfNotPresent")
        joined = "\n".join(annotations)
        blocks.append(
            f"""  - target:
      kind: {kind}
    patch: |-
      apiVersion: v1
      kind: {kind}
      metadata:
        name: prune-protection
        annotations:
{joined}"""
        )
    return "\n".join(blocks)


def render(apps: list[App]) -> str:
    enabled = [app for app in apps if app.enabled]
    disabled = [app for app in apps if not app.enabled]

    lines = [HEADER]
    lines.append("apiVersion: kustomize.config.k8s.io/v1beta1")
    lines.append("kind: Kustomization")
    lines.append("resources:")

    for app in enabled:
        lines.append(f"  - {app.name}/")

    if disabled:
        lines.append("")
        lines.append("# Disabled in provision.toml — workloads pruned, data retained:")
        for app in disabled:
            lines.append(f"#   {app.name}")

    lines.append("")
    lines.append(_prune_protection())
    lines.append("")

    return "\n".join(lines)


def write(apps: list[App]) -> bool:
    """Write the kustomization. Returns True when it changed."""
    content = render(apps)
    path = settings.kustomization_path()

    if path.exists() and path.read_text() == content:
        return False

    path.write_text(content)
    return True
