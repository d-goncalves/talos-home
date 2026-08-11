"""provision — provisioning CLI for this repo.

Two stacks live here, and they are deliberately kept apart:

    provision homelab ...   the Talos cluster, via Terraform + Flux
    provision sandbox ...   a throwaway VM, via the Proxmox API

They share the plumbing that is genuinely common — config loading, 1Password,
the console helpers, the subprocess wrapper — and nothing else. In particular
the sandbox has no Terraform state, so no sandbox command can ever plan a
change against the cluster.

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
from .commands.nas import nas
from .commands.pihole import pihole
from .commands.preflight import preflight
from .commands.sandbox import sandbox
from .commands.secrets import secrets
from .commands.state import state
from .commands.status import status
from .commands.storage_migrate import storage
from .console import error
from .proxmox import ProxmoxError
from .shell import CommandError


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="provision")
def cli() -> None:
    """Provision and manage this homelab.

    Three things share a Proxmox host and nothing else, so each gets its own
    command group, matching the directory that holds it.

    \b
    homelab   the Talos cluster            talos/
      ./provision homelab up          a machine from scratch, end to end
      ./provision homelab check       anything pending?
      ./provision homelab apps disable adventurelog

    \b
    sandbox   a throwaway VM              sandbox/
      ./provision sandbox new
      ./provision sandbox offline
      ./provision sandbox destroy

    \b
    pihole    DNS for the tailnet         pihole/
      ./provision pihole check        containers vs provision.toml
      ./provision pihole apply        converge the config
    """


@click.group("homelab")
def homelab() -> None:
    """The Talos cluster: Terraform, Flux and the apps it runs.

    \b
    From scratch:
      ./provision homelab up

    \b
    Anything pending?
      ./provision homelab check

    \b
    Free up resources on a small node:
      ./provision homelab apps disable adventurelog
    """


# Lifecycle.
homelab.add_command(up)
homelab.add_command(check)
homelab.add_command(sync)
homelab.add_command(plan)
homelab.add_command(apply)
homelab.add_command(destroy)
homelab.add_command(preflight)
homelab.add_command(output)
homelab.add_command(init)

# Groups.
homelab.add_command(apps)
homelab.add_command(secrets)
homelab.add_command(nas)
homelab.add_command(storage)
homelab.add_command(config)
homelab.add_command(bootstrap)
homelab.add_command(state)

# Read-only. `health` folded into `status --deep`: two commands that both
# answered "is the cluster ok?" was one more than anybody needed.
homelab.add_command(status)

cli.add_command(homelab)
cli.add_command(sandbox)
cli.add_command(pihole)


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
    except ProxmoxError as exc:
        # Same reasoning: the API already said what was wrong, and a traceback
        # through urllib buries it.
        error(str(exc))
        raise SystemExit(1)
    except KeyboardInterrupt:
        error("interrupted")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
