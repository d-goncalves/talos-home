"""Write kubeconfig and talosconfig from terraform outputs."""

from __future__ import annotations

import os
from pathlib import Path

import click

from .. import tf
from ..console import info, success, warn
from ..shell import run


def _write(path: Path, content: str, force: bool) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists() and not force:
        warn(f"{path} exists — skipping (use --force to overwrite)")
        return False

    # These files carry cluster admin credentials.
    path.write_text(content)
    os.chmod(path, 0o600)
    success(f"wrote {path}")
    return True


@click.group("config")
def config() -> None:
    """Manage local kubeconfig and talosconfig."""


@config.command("write")
@click.option("--force", is_flag=True, help="Overwrite existing files.")
@click.option(
    "--kubeconfig",
    "kubeconfig_path",
    default=lambda: os.environ.get("KUBECONFIG", str(Path.home() / ".kube" / "config")),
    help="Destination kubeconfig path.",
)
@click.option(
    "--talosconfig",
    "talosconfig_path",
    default=str(Path.home() / ".talos" / "config"),
    help="Destination talosconfig path.",
)
def config_write(force: bool, kubeconfig_path: str, talosconfig_path: str) -> None:
    """Write both configs from terraform state.

    This replaces the recover.sh step that pulled talosconfig out of a
    1Password document: terraform is now the source of truth for it.
    """
    outs = tf.outputs()

    _write(Path(talosconfig_path), tf.require_output("talosconfig"), force)
    _write(Path(kubeconfig_path), tf.require_output("kubeconfig"), force)

    endpoint = outs.get("controlplane_ip")
    if endpoint:
        info("Pointing talosctl at the controlplane")
        run(["talosctl", "--talosconfig", talosconfig_path, "config", "endpoint", endpoint])
        run(["talosctl", "--talosconfig", talosconfig_path, "config", "node", endpoint])


@config.command("show")
@click.argument("which", type=click.Choice(["kubeconfig", "talosconfig"]))
def config_show(which: str) -> None:
    """Print a config to stdout."""
    click.echo(tf.require_output(which))
