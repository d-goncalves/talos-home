"""Terraform lifecycle: init, plan, apply, destroy, plus the whole cold path."""

from __future__ import annotations

import click

from .. import kustomize, settings, tf
from ..console import fail, info, rule, success, warn
from ..shell import CommandError


@click.command("sync")
def sync() -> None:
    """Regenerate everything provision.toml drives.

    Writes terraform/terraform.tfvars.json and
    kubernetes/apps/kustomization.yaml. Safe to run any time.
    """
    unfilled = settings.unfilled()
    if unfilled:
        warn("provision.toml still has placeholder values:")
        for item in unfilled:
            click.echo(f"      {item}")

    apps = settings.apps()
    declared = {app.name for app in apps}
    present = settings.on_disk()

    for name in sorted(present - declared):
        warn(f"kubernetes/apps/{name}/ exists but is not in [apps] — ignored")
    for name in sorted(declared - present):
        fail(f"[apps] declares '{name}' but kubernetes/apps/{name}/ does not exist")

    if kustomize.write(apps):
        success("wrote kubernetes/apps/kustomization.yaml")
    else:
        info("kubernetes/apps/kustomization.yaml already up to date")

    if not unfilled:
        if settings.write_tfvars():
            success("wrote terraform/terraform.tfvars.json")
        else:
            info("terraform/terraform.tfvars.json already up to date")


@click.command("init")
@click.option("--upgrade", is_flag=True, help="Allow provider version upgrades.")
def init(upgrade: bool) -> None:
    """Initialize the terraform working directory."""
    tf.init(upgrade=upgrade)
    success("initialized")


@click.command("plan")
@click.option("--out", "out_file", default=None, help="Write the plan to a file.")
def plan(out_file: str | None) -> None:
    """Show what would change."""
    tf.ensure_tfvars()
    tf.ensure_init()
    args = ["plan"]
    if out_file:
        # Plan files embed the same secrets as state; keep them inside the
        # gitignored terraform directory.
        args += ["-out", out_file]
    tf.tf(*args, with_secrets=True)


@click.command("check")
def check() -> None:
    """Report whether there is anything to apply.

    Exits 0 when the cluster matches the config, 2 when changes are pending.
    Safe to run on a schedule as a drift check.
    """
    tf.ensure_tfvars()
    tf.ensure_init()

    rule("Drift check")

    if not tf.has_state():
        warn("no terraform state — nothing has been provisioned yet")
        info("Run: provision up")
        raise SystemExit(2)

    try:
        tf.tf("plan", "-detailed-exitcode", "-input=false", with_secrets=True)
    except CommandError as exc:
        # -detailed-exitcode: 0 = no changes, 1 = error, 2 = changes pending.
        if exc.returncode == 2:
            click.echo()
            warn("changes are pending — run `provision apply`")
            raise SystemExit(2)
        fail(f"terraform plan failed: {exc.stderr.strip() or exc}")

    click.echo()
    success("up to date — nothing to apply")


@click.command("apply")
@click.option("--auto-approve", is_flag=True, help="Skip the interactive approval.")
def apply(auto_approve: bool) -> None:
    """Provision or converge the cluster.

    On a cold provision the Talos config apply waits for the VM to reach
    maintenance mode after Proxmox boots it from the factory ISO. That step can
    sit for a couple of minutes before it connects; the resource timeout is
    10 minutes.
    """
    tf.ensure_tfvars()
    tf.ensure_init()

    args = ["apply"]
    if auto_approve:
        args.append("-auto-approve")
    tf.tf(*args, with_secrets=True)

    rule("Provisioned")
    outs = tf.outputs()
    for key in ("cluster_name", "cluster_endpoint", "schematic_id"):
        if key in outs:
            click.echo(f"  {key:<18} {outs[key]}")


@click.command("up")
@click.option("--auto-approve", is_flag=True, help="Skip every interactive approval.")
@click.option("--skip-bootstrap", is_flag=True, help="Stop after writing local configs.")
@click.pass_context
def up(ctx: click.Context, auto_approve: bool, skip_bootstrap: bool) -> None:
    """Bring up a machine from scratch, end to end.

    \b
    Runs, in order:
      1. preflight      tools, 1Password, provision.toml
      2. secrets check  the credentials Terraform will read
      3. sync           regenerate tfvars and the apps kustomization
      4. apply          Proxmox VM, Talos, Tailscale, DHCP reservation
      5. config write   ~/.talos/config and ~/.kube/config
      6. bootstrap all  cluster secrets, then Flux
      7. state backup   terraform state to 1Password

    Every step is idempotent, so this is also the resume path after a failure
    partway through.
    """
    from .bootstrap import bootstrap_all
    from .config import config_write
    from .preflight import preflight
    from .secrets import secrets_check
    from .state import state_backup

    rule("1/7  Preflight")
    ctx.invoke(preflight)

    rule("2/7  Secrets")
    ctx.invoke(secrets_check, check_all=False)

    rule("3/7  Sync")
    ctx.invoke(sync)

    rule("4/7  Provision")
    ctx.invoke(apply, auto_approve=auto_approve)

    rule("5/7  Local config")
    ctx.invoke(config_write, force=True)

    if skip_bootstrap:
        warn("skipping bootstrap as requested")
    else:
        rule("6/7  Bootstrap")
        ctx.invoke(bootstrap_all)

    rule("7/7  State backup")
    ctx.invoke(state_backup)

    rule("Cluster is up")
    info("Watch reconciliation with: provision status")


@click.command("destroy")
@click.option("--auto-approve", is_flag=True, help="Skip the interactive approval.")
def destroy(auto_approve: bool) -> None:
    """Tear down the cluster. Requires typing the cluster name to confirm."""
    tf.ensure_init()

    cluster = tf.output("cluster_name") or "unknown"
    warn(f"This destroys the Talos cluster '{cluster}' and its Proxmox VM.")
    warn("NFS-backed app data on the NAS survives. local-path data does not:")
    warn("  Prometheus metrics, Grafana dashboards, Uptime Kuma monitors.")

    if not auto_approve:
        typed = click.prompt("Type the cluster name to confirm", default="", show_default=False)
        if typed != cluster:
            fail("name did not match — aborting")

    tf.tf("destroy", "-auto-approve", with_secrets=True)
    success("destroyed")


@click.command("output")
@click.argument("name", required=False)
def output(name: str | None) -> None:
    """Show terraform outputs."""
    if name:
        click.echo(tf.require_output(name))
        return

    for key, value in tf.outputs().items():
        # kubeconfig/talosconfig are sensitive and multi-line; do not dump them.
        if key in ("kubeconfig", "talosconfig"):
            value = "<sensitive — use `provision config write`>"
        click.echo(f"{key:<20} {value}")
