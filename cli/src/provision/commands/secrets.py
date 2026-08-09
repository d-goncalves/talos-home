"""Inspect the 1Password entries the cluster depends on.

Nothing here prints a secret value, and the CLI never passes one to Terraform.
Terraform reads them itself through the onepassword provider (see
terraform/secrets.tf); these commands exist so a missing entry surfaces before
an apply fails halfway through.
"""

from __future__ import annotations

import click

from .. import onepassword as op
from ..console import fail, info, rule, success, warn

# Items ESO reads directly, in-cluster. Listed so `secrets check --all` covers
# the whole surface, not just what Terraform needs.
ESO_ITEMS = (
    (op.ITEM_ESO_TOKEN, "External Secrets Operator → 1Password auth"),
    ("Gitea", "Gitea admin credential"),
    ("servarr", "WireGuard key, qBittorrent password, *arr API keys"),
    ("AdventureLog - talos-home", "Django admin, secret key, postgres password"),
    ("Immich - talos-home", "Postgres password"),
    ("Outline - talos-home", "Secret key, utils secret, postgres, OIDC"),
    ("Grafana - talos-home", "Grafana admin password"),
)


@click.group("secrets")
def secrets() -> None:
    """Inspect the 1Password entries this cluster depends on."""


@secrets.command("list")
def secrets_list() -> None:
    """Show every 1Password entry the stack expects, and what reads it."""
    rule("Read by Terraform (onepassword provider, at plan time)")
    for secret in op.TF_SECRETS:
        flag = "" if secret.required else click.style("  [optional]", dim=True)
        click.echo(f"  {secret.item}{flag}")
        click.echo(click.style(f"      fields: {', '.join(secret.fields)}", dim=True))
        if secret.hint:
            click.echo(click.style(f"      {secret.hint}", dim=True))

    rule("Read by External Secrets Operator, in-cluster")
    for item, purpose in ESO_ITEMS:
        click.echo(f"  {item}")
        click.echo(click.style(f"      {purpose}", dim=True))

    rule("Written by the CLI")
    click.echo(f"  {op.ITEM_TF_STATE}")
    click.echo(click.style("      Terraform state backup (provision state backup)", dim=True))

    click.echo()
    info(f"All in the '{op.VAULT}' vault. No credential is stored in this repo.")


@secrets.command("check")
@click.option("--all", "check_all", is_flag=True, help="Also check the ESO items.")
def secrets_check(check_all: bool) -> None:
    """Verify the required 1Password entries exist. Never prints values."""
    op.ensure_signed_in()

    rule("Read by Terraform")
    problems: list[str] = []

    for secret in op.TF_SECRETS:
        missing = [f for f in secret.fields if not op.field(secret.item, f)]

        if not missing:
            success(f"{secret.item}  ({', '.join(secret.fields)})")
        elif secret.required:
            problems.append(secret.item)
            click.echo(
                f"  {click.style('✗', fg='red')} {secret.item} — "
                f"missing: {', '.join(missing)}"
            )
            if secret.hint:
                click.echo(click.style(f"      {secret.hint}", dim=True))
        else:
            warn(f"{secret.item} (optional, not set)")

    if check_all:
        rule("Read by External Secrets Operator")
        for item, purpose in ESO_ITEMS:
            if op.document_exists(item):
                success(item)
            else:
                click.echo(f"  {click.style('✗', fg='red')} {item} — {purpose}")
                problems.append(item)

    click.echo()
    if problems:
        fail(f"{len(problems)} missing item{'' if len(problems) == 1 else 's'}")
    success("all required secrets present")
