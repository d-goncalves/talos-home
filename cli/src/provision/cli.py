"""provision — provisioning CLI for the talos-home cluster.

Deliberately a thin dispatcher over terraform, talosctl, kubectl, flux and op.
The infrastructure logic lives in terraform/; this exists to sequence it and to
handle the parts terraform cannot own (secret seeding, Flux handoff, state
backup).

provision.toml at the repo root is the single source of truth for everything
non-secret. Secrets live in 1Password and are read directly by Terraform and by
External Secrets Operator, never by this CLI.
"""

from __future__ import annotations

import os

import click

from .commands.apps import apps
from .commands.bootstrap import bootstrap
from .commands.config import config
from .commands.lifecycle import apply, check, destroy, init, output, plan, sync, up
from .commands.preflight import preflight
from .commands.secrets import secrets
from .commands.state import state
from .commands.status import health, status
from .console import error
from .shell import CommandError


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="provision")
def cli() -> None:
    """Provision and manage the talos-home cluster.

    \b
    A machine from scratch:
      ./provision up

    \b
    Anything pending?
      ./provision check

    \b
    Free up resources on a small node:
      ./provision apps disable adventurelog
    """


# Lifecycle, flattened to the top level.
cli.add_command(up)
cli.add_command(check)
cli.add_command(sync)
cli.add_command(plan)
cli.add_command(apply)
cli.add_command(destroy)
cli.add_command(init)
cli.add_command(output)
cli.add_command(preflight)

# Groups.
cli.add_command(apps)
cli.add_command(secrets)
cli.add_command(config)
cli.add_command(bootstrap)
cli.add_command(state)

# Read-only.
cli.add_command(status)
cli.add_command(health)


def main() -> None:
    # The ./provision shim runs us as `python -m provision`, which would
    # otherwise show up as the program name throughout the help text.
    try:
        cli(prog_name=os.environ.get("PROVISION_PROG") or None)
    except CommandError as exc:
        # A failing terraform/kubectl/op has already printed its own error to
        # the terminal. A Python traceback on top of that is noise that buries
        # the message the user actually needs.
        error(f"{exc.argv[0]} exited {exc.returncode}")
        raise SystemExit(exc.returncode)
    except KeyboardInterrupt:
        error("interrupted")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
