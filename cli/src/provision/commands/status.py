"""Cluster status and health."""

from __future__ import annotations

import click

from .. import tf
from ..console import rule, warn
from ..shell import require, run, try_run


@click.command("status")
@click.option("--wide", is_flag=True, help="Include pod and PVC detail.")
@click.option("--deep", is_flag=True, help="Also run talosctl health (slower).")
def status(wide: bool, deep: bool) -> None:
    """Show node, Flux and workload state."""
    require("kubectl")

    rule("Nodes")
    ok, _ = try_run(["kubectl", "get", "nodes", "-o", "wide"], echo=False)
    if not ok:
        warn("could not reach the Kubernetes API")
        warn("if the node is wedged, check Talos directly: talosctl health")
        return

    run(["kubectl", "get", "nodes", "-o", "wide"], echo=False)

    rule("Flux kustomizations")
    run(
        ["kubectl", "get", "kustomization", "-n", "flux-system"],
        echo=False,
        check=False,
    )

    rule("Not ready")
    # Long-dead Error pods accumulate here for months; --field-selector keeps
    # the output to things that are actually not running right now.
    run(
        [
            "kubectl", "get", "pods", "--all-namespaces",
            "--field-selector=status.phase!=Running,status.phase!=Succeeded",
        ],
        echo=False,
        check=False,
    )

    if wide:
        rule("PersistentVolumeClaims")
        run(["kubectl", "get", "pvc", "--all-namespaces"], echo=False, check=False)

    if deep:
        rule("Talos health")
        _talos_health()


def _talos_health() -> None:
    """Ask Talos itself, rather than the Kubernetes API on top of it.

    Worth reaching for when kubectl answers but something underneath is wrong —
    etcd unhappy, a service crash-looping — since the API server can look fine
    while the node is degraded.
    """
    require("talosctl")
    endpoint = tf.output("controlplane_ip")

    argv = ["talosctl", "health"]
    if endpoint:
        argv += ["--nodes", endpoint, "--endpoints", endpoint]
    run(argv, check=False)
