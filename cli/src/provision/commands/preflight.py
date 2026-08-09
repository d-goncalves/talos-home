"""Check that everything needed to provision is in place, before touching anything."""

from __future__ import annotations

import click

from .. import onepassword as op
from .. import settings, tf
from ..console import info, rule, success, warn
from ..shell import CommandError, run, try_run, which

REQUIRED_TOOLS = {
    "talosctl": "brew install talosctl",
    "kubectl": "brew install kubectl",
    "flux": "brew install fluxcd/tap/flux",
    "helm": "brew install helm",
    "op": "brew install 1password-cli",
}


def nfs_export_problems() -> list[str]:
    """Check every configured node is allowed to mount the NAS exports.

    UNAS Pro allow-lists NFS clients per IP. A node whose address is not on the
    list is refused every share, so each NFS-backed app fails to mount with no
    hint as to why. There is no UniFi API for share ACLs, so this cannot be
    fixed automatically — but it can be caught before a provision instead of
    after.

    `showmount -e` needs no credentials, which is what makes this checkable.
    """
    import ipaddress

    cfg = settings.load()
    nas_ip = (cfg.get("nas") or {}).get("ip")
    if not nas_ip:
        return []  # [nas] is optional; nothing to check against.

    nodes = cfg.get("nodes") or {}
    wanted = {name: n["ip"] for name, n in nodes.items() if n.get("ip")}
    if not wanted:
        return []

    ok, _ = try_run(["showmount", "-e", nas_ip], capture=True, echo=False)
    if not ok:
        return [f"could not query NFS exports on {nas_ip} (is the NAS up?)"]

    raw = run(["showmount", "-e", nas_ip], capture=True, echo=False)

    allowed: set[str] = set()
    for line in raw.splitlines()[1:]:  # first line is a header
        parts = line.split()
        if len(parts) < 2:
            continue
        for client in " ".join(parts[1:]).replace(",", " ").split():
            allowed.add(client)

    if "*" in allowed or "(everyone)" in allowed:
        return []

    problems = []
    for name, ip in sorted(wanted.items()):
        if ip in allowed:
            continue
        # An export may be a subnet rather than a bare address.
        covered = False
        for entry in allowed:
            try:
                if ipaddress.ip_address(ip) in ipaddress.ip_network(entry, strict=False):
                    covered = True
                    break
            except ValueError:
                continue
        if not covered:
            problems.append(
                f"node {name} ({ip}) is not in the NAS export allow-list "
                f"[{', '.join(sorted(allowed)) or 'empty'}] — "
                f"every NFS-backed app would fail to mount"
            )
    return problems


@click.command("preflight")
def preflight() -> None:
    """Verify tooling, credentials and config before provisioning."""
    rule("Preflight")
    problems: list[str] = []

    # ── Terraform / OpenTofu ──────────────────────────────────────────────
    tf_bin = None
    for candidate in ("tofu", "terraform"):
        if which(candidate):
            tf_bin = candidate
            break

    if tf_bin:
        version = run([tf_bin, "version"], capture=True).splitlines()[0]
        success(f"{version}")
    else:
        problems.append("neither tofu nor terraform on PATH")

    # ── Other tools ───────────────────────────────────────────────────────
    for tool, hint in REQUIRED_TOOLS.items():
        if which(tool):
            success(f"{tool} found")
        else:
            problems.append(f"{tool} missing — {hint}")

    # ── 1Password ─────────────────────────────────────────────────────────
    if which("op"):
        try:
            run(["op", "account", "get"], capture=True)
            success("1Password CLI signed in")

            for item in (op.ITEM_ESO_TOKEN,):
                if op.document_exists(item):
                    success(f"1Password item present: {item}")
                else:
                    problems.append(f"1Password item missing: {item}")
        except CommandError:
            problems.append("1Password CLI not signed in — run: eval $(op signin)")

    # ── provision.toml ────────────────────────────────────────────────────
    if settings.config_path().exists():
        success("provision.toml present")

        unfilled = settings.unfilled()
        if unfilled:
            for item in unfilled:
                problems.append(f"provision.toml placeholder not filled in: {item}")
        else:
            success("provision.toml has no placeholder values left")
    else:
        problems.append("provision.toml missing at the repo root")

    if tf.initialized():
        success("terraform initialized")
    else:
        warn("terraform not initialized — `./provision init` will handle it")

    if tf.has_state():
        success("terraform state present")
    else:
        warn("no terraform state yet — this would be a fresh provision")

    # ── NAS export allow-list ─────────────────────────────────────────────
    nfs = nfs_export_problems()
    if nfs:
        problems.extend(nfs)
    else:
        success("node is allowed to mount the NAS exports")

    # ── Verdict ───────────────────────────────────────────────────────────
    click.echo()
    if problems:
        for problem in problems:
            click.echo(f"  {click.style('✗', fg='red')} {problem}")
        raise SystemExit(1)

    info("All checks passed.")
