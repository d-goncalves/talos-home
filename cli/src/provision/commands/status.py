"""Cluster status and health."""

from __future__ import annotations

import click

from .. import tf
from ..console import rule, warn
from ..shell import require, run, try_run


@click.command("status")
@click.option("--wide", is_flag=True, help="Include pod and PVC detail.")
def status(wide: bool) -> None:
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


@click.command("health")
def health() -> None:
    """Run talosctl health against the cluster."""
    require("talosctl")
    endpoint = tf.output("controlplane_ip")

    argv = ["talosctl", "health"]
    if endpoint:
        argv += ["--nodes", endpoint, "--endpoints", endpoint]
    run(argv, check=False)
