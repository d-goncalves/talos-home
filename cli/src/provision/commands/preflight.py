"""Check that everything needed to provision is in place, before touching anything."""

from __future__ import annotations

import click

from .. import onepassword as op
from .. import settings, tf
from ..console import info, rule, success, warn
from ..shell import CommandError, run, which

REQUIRED_TOOLS = {
    "talosctl": "brew install talosctl",
    "kubectl": "brew install kubectl",
    "flux": "brew install fluxcd/tap/flux",
    "helm": "brew install helm",
    "op": "brew install 1password-cli",
}


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

    # ── Verdict ───────────────────────────────────────────────────────────
    click.echo()
    if problems:
        for problem in problems:
            click.echo(f"  {click.style('✗', fg='red')} {problem}")
        raise SystemExit(1)

    info("All checks passed.")
