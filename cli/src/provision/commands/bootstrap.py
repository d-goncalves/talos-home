"""Post-provision cluster bootstrap.

This is the part of scripts/recover.sh that has to happen after the node is up:
seeding the two secrets that are never stored in git, then handing control to
Flux.
"""

from __future__ import annotations

import os

import click

from .. import onepassword as op
from .. import tf
from ..console import fail, info, rule, success, warn
from ..shell import require, run

ESO_NAMESPACE = "external-secrets"
ESO_SECRET = "onepassword-service-account-token"
FLUX_NAMESPACE = "flux-system"
CLUSTER_VARS_SECRET = "cluster-vars"


def _kubectl(*args: str, capture: bool = False, check: bool = True) -> str:
    return run(["kubectl", *args], capture=capture, check=check)


def _ensure_namespace(name: str) -> None:
    manifest = run(
        ["kubectl", "create", "namespace", name, "--dry-run=client", "-o", "yaml"],
        capture=True,
    )
    run(["kubectl", "apply", "-f", "-"], input_text=manifest, capture=True)


def _apply_secret(name: str, namespace: str, literals: dict[str, str]) -> None:
    """Create or update a secret idempotently."""
    argv = ["kubectl", "create", "secret", "generic", name, "--namespace", namespace]
    for key, value in literals.items():
        argv += ["--from-literal", f"{key}={value}"]
    argv += ["--dry-run=client", "-o", "yaml"]

    manifest = run(argv, capture=True)
    run(["kubectl", "apply", "-f", "-"], input_text=manifest, capture=True)


def _prompt_var(name: str, description: str) -> str:
    """Read a value from the environment, falling back to a prompt."""
    value = os.environ.get(name)
    if value:
        return value
    return click.prompt(f"{description} ({name})")


@click.group("bootstrap")
def bootstrap() -> None:
    """Seed cluster secrets and start Flux."""


@bootstrap.command("secrets")
def bootstrap_secrets() -> None:
    """Create the two secrets that are never stored in git.

    onepassword-service-account-token  →  ESO authenticates to 1Password
    cluster-vars                       →  Flux postBuild variable substitution
    """
    require("kubectl")
    op.ensure_signed_in()

    rule("Cluster secrets")

    # ── ESO service account token ─────────────────────────────────────────
    token = op.field(op.ITEM_ESO_TOKEN, "token")
    if not token:
        fail(
            f"could not read 'token' from 1Password item '{op.ITEM_ESO_TOKEN}' "
            f"in vault '{op.VAULT}'"
        )

    _ensure_namespace(ESO_NAMESPACE)
    _apply_secret(ESO_SECRET, ESO_NAMESPACE, {"token": token})
    success(f"{ESO_NAMESPACE}/{ESO_SECRET}")

    # ── cluster-vars ──────────────────────────────────────────────────────
    # Terraform knows the node and network values. The NAS is not
    # terraform-managed (UniFi exposes no API for UNAS shares), so those two
    # come from the environment or a prompt.
    literals = {
        "TAILNET_DOMAIN": tf.require_output("tailnet_domain"),
        "NODE_IP": tf.require_output("controlplane_ip"),
        "LAN_SUBNET": tf.require_output("lan_subnet"),
        "NAS_IP": _prompt_var("NAS_IP", "NAS IP address"),
        "NAS_DATA_PATH": _prompt_var("NAS_DATA_PATH", "NAS data base path"),
    }

    _ensure_namespace(FLUX_NAMESPACE)
    _apply_secret(CLUSTER_VARS_SECRET, FLUX_NAMESPACE, literals)
    success(f"{FLUX_NAMESPACE}/{CLUSTER_VARS_SECRET}")

    for key, value in literals.items():
        click.echo(f"    {key:<16} {value}")


@bootstrap.command("gitea")
def bootstrap_gitea() -> None:
    """Deploy Gitea via the existing helm script.

    Gitea is deliberately not a Flux HelmRelease: Flux sources its GitRepository
    from Gitea, so Flux owning Gitea would be circular. scripts/deploy-gitea.sh
    carries the accumulated workarounds (envsubst for TAILNET_DOMAIN,
    --force-conflicts for the field-manager conflict), so call it rather than
    reimplementing it here.
    """
    require("helm")
    script = tf.repo_root() / "scripts" / "deploy-gitea.sh"
    if not script.exists():
        fail(f"not found: {script}")
    run(["bash", str(script)])


@bootstrap.command("flux")
def bootstrap_flux() -> None:
    """Apply the Flux sync manifests and kustomizations."""
    require("kubectl")
    root = tf.repo_root()

    sync = root / "kubernetes" / "flux" / "flux-system" / "gotk-sync.yaml"
    kustomizations = root / "kubernetes" / "flux" / "kustomizations"

    rule("Flux")
    _kubectl("apply", "-f", str(sync))
    _kubectl("apply", "-k", str(kustomizations))
    success("Flux manifests applied")

    warn(
        "Flux syncs from Gitea over SSH, and Gitea runs on this cluster. On a "
        "cold cluster it will fail until the Tailscale operator and Gitea are "
        "up; it retries every minute, so this resolves itself."
    )


@bootstrap.command("all")
@click.pass_context
def bootstrap_all(ctx: click.Context) -> None:
    """Run secrets, then flux."""
    ctx.invoke(bootstrap_secrets)
    ctx.invoke(bootstrap_flux)

    rule("Bootstrap complete")
    info("Watch reconciliation with: provision status")
