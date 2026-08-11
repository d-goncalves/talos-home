"""Run the Ansible playbook that configures Pi-hole.

Terraform declares the containers; Ansible configures what runs inside them.
This command is a thin wrapper: it regenerates the inventory from
provision.toml first, so a stale host list cannot quietly configure the wrong
set of machines, then hands over to ansible-playbook.

It deliberately does not touch the admin password. The playbook reads that
from 1Password itself, so no secret passes through this process, its argv, or
its environment.
"""

from __future__ import annotations

import click

from .. import settings, tf
from ..console import fail, info, rule, success
from ..shell import require, run


def _terraform(*args: str) -> None:
    """Run terraform in the Pi-hole root, not the homelab one.

    Separate state, deliberately. Nothing irreplaceable lives in it — the only
    resource is the tailnet nameserver list — so unlike homelab there is no
    backup-to-1Password step to forget.
    """
    settings.write_pihole_tfvars()
    tf.tf(*args, with_secrets=True, cwd=settings.pihole_terraform_dir())


@click.group("pihole")
def pihole() -> None:
    """DNS for the tailnet: the Pi-hole containers and their config.

    \b
      ./provision pihole check          containers vs provision.toml
      ./provision pihole apply --check  config drift, changes nothing
      ./provision pihole apply          converge

    The tailnet nameserver list is Terraform's, in this project's own root
    with its own state: `./provision pihole dns`. See pihole/README.md.
    """


@pihole.command("apply")
@click.option("--check", is_flag=True, help="Report drift without changing anything.")
@click.option("--limit", default=None, help="Restrict to one host from the group.")
def pihole_apply(check: bool, limit: str | None) -> None:
    """Converge every declared Pi-hole on ansible/group_vars/piholes.yml."""
    hosts = settings.pihole_hosts()
    if not hosts:
        fail("provision.toml has no enabled [pihole] section")

    require("ansible-playbook", "brew install ansible")
    require("op", "the playbook reads the admin password from 1Password")

    rule("Pi-hole drift" if check else "Pi-hole")

    if settings.write_inventory():
        info("regenerated ansible/inventory.yml")

    argv = ["ansible-playbook", "site.yml"]
    if check:
        # --diff too: a check run that will not say what it would change
        # answers the less useful half of the question.
        argv += ["--check", "--diff"]
    if limit:
        argv += ["--limit", limit]

    run(argv, cwd=settings.ansible_dir())

    if check:
        success("drift check complete")
    else:
        success(f"converged {len(hosts)} Pi-hole host(s)")


# Fields worth comparing. Deliberately not everything: the point is to catch a
# container that has drifted from what is written down, not to enumerate PVE.
_SPEC = (
    ("hostname", "name", str),
    ("memory", "memory", int),
    ("swap", "swap", int),
    ("cores", "cores", int),
)


@pihole.command("check")
def pihole_check() -> None:
    """Compare the live containers against provision.toml.

    Reports drift; never corrects it. These containers are deliberately not
    Terraform-managed — a rebuilt container gets a new Tailscale identity, and
    every device loses DNS until the nameserver list is updated to match. The
    provider also cannot express the raw lxc.* entries that pass /dev/net/tun
    through, which is what tailscaled needs to run at all.

    So the container spec is declared, and this checks reality against it,
    rather than an apply being one confirmation away from taking DNS down.
    """
    from .. import proxmox

    hosts = settings.pihole_hosts()
    if not hosts:
        fail("provision.toml has no enabled [pihole] section")

    rule("Pi-hole containers")
    px = proxmox.connect()
    problems: list[str] = []

    # px.exists() looks at QEMU only; these are LXC containers.
    live = {int(c["vmid"]) for c in px.get(f"/nodes/{px.node}/lxc")}

    for host in hosts:
        if host.vmid not in live:
            problems.append(f"{host.name}: no container {host.vmid} on the node")
            continue

        cfg = px.get(f"/nodes/{px.node}/lxc/{host.vmid}/config")

        for key, attr, cast in _SPEC:
            want = getattr(host, attr)
            got = cfg.get(key)
            if got is None or cast(got) != cast(want):
                problems.append(
                    f"{host.name}: {key} is {got!r}, provision.toml says {want!r}"
                )

        net = cfg.get("net0", "")
        if f"ip={host.ip}/" not in net:
            current = next(
                (p.split("=", 1)[1] for p in net.split(",") if p.startswith("ip=")),
                "unset",
            )
            problems.append(
                f"{host.name}: net0 ip is {current}, provision.toml says {host.ip}"
            )

        # Without this the container has no /dev/net/tun, tailscaled cannot
        # start, and the address every device resolves against goes away.
        raw = str(cfg.get("lxc", ""))
        if "/dev/net/tun" not in raw:
            problems.append(
                f"{host.name}: no /dev/net/tun passthrough — tailscaled cannot run"
            )

        if str(cfg.get("onboot", "0")) != "1":
            problems.append(f"{host.name}: onboot is not set — DNS would not survive a host reboot")

    if problems:
        for item in problems:
            click.echo(f"  {click.style('✗', fg='red')} {item}")
        click.echo()
        fail(f"{len(problems)} container(s) differ from provision.toml")

    success(f"{len(hosts)} container(s) match provision.toml")


@pihole.group("dns")
def pihole_dns() -> None:
    """The tailnet nameserver list — Terraform, with its own state.

    Separate from the container config above: this is what every device
    resolves against, so an empty or wrong list takes DNS down everywhere at
    once, including away from home.
    """


@pihole_dns.command("plan")
def pihole_dns_plan() -> None:
    """Show what would change."""
    require("terraform")
    _terraform("plan")


@pihole_dns.command("apply")
@click.option("--auto-approve", is_flag=True, help="Skip the interactive approval.")
def pihole_dns_apply(auto_approve: bool) -> None:
    """Publish the nameserver list. Read the plan first."""
    require("terraform")
    args = ["apply"]
    if auto_approve:
        args.append("-auto-approve")
    _terraform(*args)


@pihole_dns.command("init")
@click.option("--upgrade", is_flag=True, help="Allow provider version upgrades.")
def pihole_dns_init(upgrade: bool) -> None:
    """Initialize the Pi-hole terraform working directory."""
    require("terraform")
    args = ["init", "-input=false"]
    if upgrade:
        args.append("-upgrade")
    _terraform(*args)
    success("initialized")
