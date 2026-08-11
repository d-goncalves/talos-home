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


# Privileges the stack actually exercises. Snapshot ones are listed separately
# because only `sandbox reset` needs them — everything else works without.
PVE_PRIVILEGES = (
    "VM.Allocate", "VM.Audit", "VM.Clone", "VM.PowerMgmt", "VM.Migrate",
    "VM.Config.CDROM", "VM.Config.CPU", "VM.Config.Cloudinit", "VM.Config.Disk",
    "VM.Config.HWType", "VM.Config.Memory", "VM.Config.Network",
    "VM.Config.Options", "VM.GuestAgent.Audit",
    "Datastore.Allocate", "Datastore.AllocateSpace",
    "Datastore.AllocateTemplate", "Datastore.Audit",
    "Sys.Audit", "Sys.Modify",
)
PVE_PRIVILEGES_SANDBOX = ("VM.Snapshot", "VM.Snapshot.Rollback")


def proxmox_problems() -> tuple[list[str], list[str]]:
    """Check Proxmox is set up the way the stack assumes.

    None of this is Terraform-managed — the token, its role, the storage
    content types and host SSH are all prepared by hand before a first run. Each
    one otherwise surfaces as a failure partway through an apply, which is a bad
    place to discover that a role is missing one privilege.

    Returns (problems, warnings).
    """
    from ..proxmox import ProxmoxError, connect

    cfg = settings.load()
    pve = cfg.get("proxmox") or {}
    problems: list[str] = []
    warnings: list[str] = []

    try:
        api = connect()
        version = api.get("/version")["version"]
    except (ProxmoxError, SystemExit) as exc:
        return [f"cannot reach the Proxmox API at {pve.get('endpoint')}: {exc}"], []
    success(f"Proxmox {version} reachable")

    # ── Token privileges ──────────────────────────────────────────────────
    try:
        granted = {k for k, v in (api.get("/access/permissions") or {}).get("/", {}).items() if v}
    except ProxmoxError as exc:
        return [f"could not read token privileges: {exc}"], []

    missing = [p for p in PVE_PRIVILEGES if p not in granted]
    if missing:
        problems.append(
            "Proxmox token is missing privileges: " + ", ".join(missing)
            + " — see docs/proxmox.md"
        )
    else:
        success(f"Proxmox token has the {len(PVE_PRIVILEGES)} privileges the cluster needs")

    missing_snap = [p for p in PVE_PRIVILEGES_SANDBOX if p not in granted]
    if missing_snap:
        warnings.append(
            "token lacks " + ", ".join(missing_snap) + " — `sandbox reset` will not work"
        )

    # ── Storage content types ─────────────────────────────────────────────
    node = pve.get("node_name", "pve")
    wanted = {pve.get("iso_datastore_id", "local"): {"iso"}}
    for body in (cfg.get("nodes") or {}).values():
        if body.get("datastore_id"):
            wanted.setdefault(body["datastore_id"], set()).add("images")

    for store, needs in wanted.items():
        try:
            content = set((api.get(f"/nodes/{node}/storage/{store}/status") or {}).get("content", "").split(","))
        except ProxmoxError:
            problems.append(f"Proxmox storage '{store}' not found on node '{node}'")
            continue
        gap = needs - content
        if gap:
            problems.append(
                f"Proxmox storage '{store}' does not allow {', '.join(sorted(gap))} "
                f"(has: {', '.join(sorted(c for c in content if c))})"
            )
        else:
            success(f"storage '{store}' allows {', '.join(sorted(needs))}")

    # ── Sandbox extras: warn, never block ─────────────────────────────────
    if cfg.get("sandbox"):
        store = "local"
        try:
            content = set((api.get(f"/nodes/{node}/storage/{store}/status") or {}).get("content", "").split(","))
        except ProxmoxError:
            content = set()
        for kind, why in (("import", "`sandbox template` cannot import the cloud image"),
                          ("snippets", "the sandbox console will have no autologin")):
            if kind not in content:
                warnings.append(
                    f"storage '{store}' does not allow '{kind}' — {why}. Fix with: "
                    f"pvesm set {store} --content {','.join(sorted(content | {kind}))}"
                )

    # ── Host SSH ──────────────────────────────────────────────────────────
    host = (pve.get("endpoint") or "").split("//")[-1].split(":")[0]
    user = pve.get("ssh_username", "root")
    if host:
        ok, _ = try_run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
             f"{user}@{host}", "true"], capture=True, echo=False)
        if ok:
            success(f"ssh {user}@{host} works")
        else:
            keys = run(["ssh-add", "-L"], capture=True, echo=False, check=False)
            loaded = len([k for k in keys.splitlines() if k.startswith(("ssh-", "ecdsa-", "sk-"))])
            problems.append(
                f"cannot ssh {user}@{host} — Terraform needs it for disk import, "
                f"and the sandbox for its cloud-init snippet "
                f"({loaded} key(s) in the agent; if that looks short, unlock 1Password)"
            )

    return problems, warnings


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

    # ── Proxmox ───────────────────────────────────────────────────────────
    warnings: list[str] = []
    if which("op"):
        pve_problems, warnings = proxmox_problems()
        problems.extend(pve_problems)
    else:
        warn("skipping Proxmox checks — the API token is read from 1Password")

    # ── NAS export allow-list ─────────────────────────────────────────────
    nfs = nfs_export_problems()
    if nfs:
        problems.extend(nfs)
    else:
        success("node is allowed to mount the NAS exports")

    # ── Verdict ───────────────────────────────────────────────────────────
    click.echo()
    for item in warnings:
        click.echo(f"  {click.style('!', fg='yellow')} {item}")
    if problems:
        for problem in problems:
            click.echo(f"  {click.style('✗', fg='red')} {problem}")
        raise SystemExit(1)

    info("All checks passed.")
