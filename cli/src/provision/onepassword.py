"""1Password CLI helpers.

Item names match what the cluster already expects, so this stays compatible
with the existing recover.sh flow and the ExternalSecret definitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .console import fail
from .shell import CommandError, require, run

VAULT = "Server Infrastructure"

ITEM_ESO_TOKEN = "1Password Service Account - talos-home"
ITEM_TF_STATE = "Terraform State - talos-home"
# Note: the old "Talos - talosconfig" document is no longer read. talosconfig
# now comes from terraform state via `./provision config write`.
ITEM_PROXMOX = "Proxmox API - talos-home"
# Pre-existing item; its username is the OAuth client ID and its password the
# tskey-client-… secret, which is exactly what the provider wants.
ITEM_TAILSCALE_OAUTH = "Tailscale Talos"
ITEM_UNIFI = "UniFi Local Admin - talos-home"


@dataclass(frozen=True)
class TfSecret:
    """A credential Terraform reads from 1Password itself.

    The CLI never handles these values. terraform/secrets.tf declares
    `onepassword_item` data sources and the provider fetches them at plan time;
    these entries exist so `provision secrets check` can tell you what is
    missing before an apply fails halfway through.
    """

    item: str
    fields: tuple[str, ...]
    required: bool = True
    hint: str = ""


TF_SECRETS: tuple[TfSecret, ...] = (
    TfSecret(
        ITEM_PROXMOX,
        ("password",),
        hint="Proxmox → Datacenter → API Tokens. Store USER@REALM!TOKENID=UUID as the password.",
    ),
    TfSecret(
        ITEM_TAILSCALE_OAUTH,
        ("username", "password"),
        hint="OAuth client with the auth_keys scope; username = client ID, password = client secret",
    ),
    TfSecret(
        ITEM_UNIFI,
        ("username", "password"),
        required=False,
        hint="Controller-local account only (Limited Admin, Local Access Only)",
    ),
)


def default_account() -> str | None:
    """Account shorthand the local `op` CLI is signed in to, if any."""
    try:
        raw = run(["op", "account", "list", "--format=json"], capture=True)
    except CommandError:
        return None

    import json

    try:
        accounts = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return None

    if not accounts:
        return None
    return accounts[0].get("url") or accounts[0].get("account_uuid")


def ensure_signed_in() -> None:
    require("op", "install with: brew install 1password-cli")
    try:
        run(["op", "account", "get"], capture=True)
    except CommandError:
        fail("1Password CLI is not signed in. Run: eval $(op signin)")


def field(item: str, name: str, vault: str = VAULT) -> str | None:
    """Read a single field. Returns None when the item or field is missing."""
    try:
        value = run(
            ["op", "item", "get", item, "--vault", vault, "--fields", name, "--reveal"],
            capture=True,
        )
    except CommandError:
        return None
    return value.strip() or None


def read_document(item: str, vault: str = VAULT) -> str | None:
    try:
        return run(["op", "document", "get", item, "--vault", vault], capture=True)
    except CommandError:
        return None


def document_exists(item: str, vault: str = VAULT) -> bool:
    try:
        run(["op", "item", "get", item, "--vault", vault], capture=True)
        return True
    except CommandError:
        return False


def put_document(path: Path, item: str, vault: str = VAULT) -> None:
    """Create the document, or replace it if it already exists."""
    if document_exists(item, vault):
        run(
            ["op", "document", "edit", item, str(path), "--vault", vault],
            capture=True,
        )
    else:
        run(
            [
                "op", "document", "create", str(path),
                "--title", item,
                "--vault", vault,
            ],
            capture=True,
        )
