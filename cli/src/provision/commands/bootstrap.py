"""Post-provision cluster bootstrap.

This is the part of scripts/recover.sh that has to happen after the node is up:
seeding the two secrets that are never stored in git, then handing control to
Flux.
"""

from __future__ import annotations

import os
import time

import click

from .. import onepassword as op
from .. import settings, tf
from ..console import fail, info, rule, success, warn
from ..shell import require, run, try_run

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


def _prompt_var(name: str, description: str, default: str | None = None) -> str:
    """Resolve a value: environment first, then provision.toml, then a prompt."""
    value = os.environ.get(name)
    if value:
        return value
    if default:
        return default
    return click.prompt(f"{description} ({name})")


def _require_cfg(cfg: dict, table: str, key: str) -> str:
    value = (cfg.get(table) or {}).get(key)
    if not value:
        fail(f"provision.toml: [{table}].{key} is not set")
    return value


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

    # ── Resolve config, and check we are pointed at the right cluster ─────
    # Values come from provision.toml, not from terraform outputs. The outputs
    # are derived from this same file, so reading the source works identically
    # for a terraform-provisioned cluster and is the only thing that works for
    # one that predates terraform — a hand-built cluster has no state to read.
    cfg = settings.load()
    nodes = cfg.get("nodes") or {}
    controlplanes = sorted(
        name for name, n in nodes.items()
        if n.get("machine_type", "controlplane") == "controlplane"
    )
    if not controlplanes:
        fail("provision.toml declares no controlplane node")

    node_ip = nodes[controlplanes[0]]["ip"]

    # provision.toml describes the cluster it provisions, which is not
    # necessarily the cluster kubectl points at. Check before mutating
    # anything, and before prompting for values that may be discarded.
    ok, current = try_run(
        ["kubectl", "get", "nodes", "-o",
         "jsonpath={.items[*].status.addresses[?(@.type=='InternalIP')].address}"],
        capture=True,
        echo=False,
    )
    if ok and current.strip() and node_ip not in current.split():
        warn(
            f"provision.toml describes node {node_ip}, but the current kube "
            f"context is node(s) {current.strip()}."
        )
        if not click.confirm("  Seed this cluster anyway?", default=False):
            fail("aborted — point kubectl at the right cluster, or fix provision.toml")

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
    # The NAS is not terraform-managed (no UniFi API exposes UNAS shares), but
    # its address and data path live in provision.toml so this does not have to
    # prompt. Environment variables still win, for a one-off override.
    nas = cfg.get("nas") or {}
    literals = {
        "TAILNET_DOMAIN": _require_cfg(cfg, "cluster", "tailnet_domain"),
        "NODE_IP": node_ip,
        "LAN_SUBNET": _require_cfg(cfg, "network", "lan_subnet"),
        "NAS_IP": _prompt_var("NAS_IP", "NAS IP address", nas.get("ip")),
        "NAS_DATA_PATH": _prompt_var(
            "NAS_DATA_PATH", "NAS data base path", nas.get("data_path")
        ),
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


def _suspend_apps() -> bool:
    """Hold the apps Kustomization. Returns False if it does not exist yet."""
    ok, _ = try_run(["kubectl", "get", "kustomization", "apps", "-n", FLUX_NAMESPACE],
                    capture=True, echo=False)
    if not ok:
        return False
    run(["flux", "suspend", "kustomization", "apps"], check=False)
    return True


def _wait_kustomization(name: str, timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, ready = try_run(
            ["kubectl", "get", "kustomization", name, "-n", FLUX_NAMESPACE,
             "-o", "jsonpath={.status.conditions[?(@.type=='Ready')].status}"],
            capture=True, echo=False,
        )
        if ready.strip() == "True":
            return True
        time.sleep(10)
    return False


@bootstrap.command("all")
@click.option(
    "--skip-nas-gate",
    is_flag=True,
    help="Deploy apps without first proving the NAS is writable.",
)
@click.pass_context
def bootstrap_all(ctx: click.Context, skip_nas_gate: bool) -> None:
    """Seed secrets, bring up infrastructure, prove the NAS works, then apps.

    Apps are held back until the cluster has demonstrably written to the NAS.
    An app that starts without its NFS volume does not fail cleanly — it
    initialises an empty database, or writes to a directory that a later mount
    hides. Both are worse than the app simply not starting.
    """
    from .nas import nas_verify

    ctx.invoke(bootstrap_secrets)

    if skip_nas_gate:
        warn("NAS gate skipped — apps may start without their storage")
        ctx.invoke(bootstrap_flux)
        rule("Bootstrap complete")
        return

    rule("Gating apps on NAS availability")
    suspended = _suspend_apps()

    ctx.invoke(bootstrap_flux)

    # Suspend again: on a cold cluster the Kustomization only exists after the
    # manifests above are applied, so the first attempt was a no-op.
    if not suspended:
        for _ in range(30):
            if _suspend_apps():
                suspended = True
                break
            time.sleep(2)

    info("Waiting for infrastructure (CSI driver and storage classes)")
    if not _wait_kustomization("infrastructure", timeout=600):
        fail("infrastructure did not become Ready — apps left suspended")

    try:
        ctx.invoke(nas_verify, storage_class=None, timeout=180)
    except SystemExit:
        warn("apps remain SUSPENDED — nothing has been deployed against bad storage")
        warn("fix NAS access, then: flux resume kustomization apps")
        raise

    if suspended:
        info("NAS verified — releasing apps")
        run(["flux", "resume", "kustomization", "apps"], check=False)

    rule("Bootstrap complete")
    info("Watch reconciliation with: ./provision status")
