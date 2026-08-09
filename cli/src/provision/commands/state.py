"""Back up and restore terraform state via 1Password.

Terraform state holds the cluster CA private key, the bootstrap tokens and the
secretbox encryption key. Losing it means losing the ability to manage the
cluster; leaking it means handing over the cluster. A 1Password document is the
same trust boundary the repo already uses for the ESO service account token and
the Tailscale auth key, so it is the natural place for it.
"""

from __future__ import annotations

import datetime as dt
import shutil

import click

from .. import onepassword as op
from .. import tf
from ..console import fail, info, success, warn


@click.group("state")
def state() -> None:
    """Back up and restore terraform state."""


@state.command("backup")
def state_backup() -> None:
    """Upload terraform.tfstate to 1Password."""
    op.ensure_signed_in()

    path = tf.state_path()
    if not tf.has_state():
        fail(f"no state to back up at {path}")

    info(f"Uploading {path.name} to 1Password › {op.VAULT} › {op.ITEM_TF_STATE}")
    op.put_document(path, op.ITEM_TF_STATE)
    success("state backed up")


@state.command("restore")
@click.option("--force", is_flag=True, help="Overwrite existing local state.")
def state_restore(force: bool) -> None:
    """Download terraform.tfstate from 1Password."""
    op.ensure_signed_in()

    path = tf.state_path()
    if tf.has_state() and not force:
        fail(f"{path} already exists — pass --force to overwrite")

    content = op.read_document(op.ITEM_TF_STATE)
    if content is None:
        fail(f"1Password document not found: {op.ITEM_TF_STATE}")

    if path.exists():
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_suffix(f".tfstate.{stamp}.bak")
        shutil.copy2(path, backup)
        warn(f"existing state copied to {backup.name}")

    path.write_text(content)
    path.chmod(0o600)
    success(f"restored {path}")


@state.command("status")
def state_status() -> None:
    """Show whether local state exists and whether a backup is present."""
    path = tf.state_path()

    if tf.has_state():
        size = path.stat().st_size
        mtime = dt.datetime.fromtimestamp(path.stat().st_mtime)
        success(f"local state: {path} ({size} bytes, modified {mtime:%Y-%m-%d %H:%M})")
    else:
        warn("local state: none")

    op.ensure_signed_in()
    if op.document_exists(op.ITEM_TF_STATE):
        success(f"1Password backup: {op.ITEM_TF_STATE}")
    else:
        warn("1Password backup: none — run `provision state backup`")
