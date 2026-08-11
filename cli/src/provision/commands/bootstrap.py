"""Post-provision cluster bootstrap.

This is the part of talos/scripts/recover.sh that has to happen after the node is up:
seeding the two secrets that are never stored in git, then handing control to
Flux.
"""

from __future__ import annotations

import json
import os
import time

import click

from .. import onepassword as op
from .. import settings, substitution
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

    # This dict is the only writer of cluster-vars, and substitution.py lints
    # the manifests against its key set. Drift between them means either the
    # lint rejects a variable that does in fact resolve, or it passes one that
    # takes the whole Kustomization down on the next reconcile.
    assert set(literals) == substitution.CLUSTER_VARS, (
        "cluster-vars keys have drifted from substitution.CLUSTER_VARS: "
        f"{set(literals) ^ substitution.CLUSTER_VARS}"
    )

    _ensure_namespace(FLUX_NAMESPACE)
    _apply_secret(CLUSTER_VARS_SECRET, FLUX_NAMESPACE, literals)

    # ── Flux's deploy key for the Gitea GitRepository ──────────────────────
    # Not in git and not derivable: without it the GitRepository fails with
    # "secrets \"flux-system\" not found" and nothing reconciles at all.
    identity = op.field(op.ITEM_FLUX_SSH, "identity")
    known_hosts = op.field(op.ITEM_FLUX_SSH, "known_hosts")
    if identity and known_hosts:
        public = op.field(op.ITEM_FLUX_SSH, "identity.pub") or ""
        _apply_secret("flux-system", FLUX_NAMESPACE, {
            "identity": identity,
            "identity.pub": public,
            "known_hosts": known_hosts,
        })
        success(f"{FLUX_NAMESPACE}/flux-system (Gitea deploy key)")
    else:
        warn(
            f"1Password item '{op.ITEM_FLUX_SSH}' has no identity/known_hosts — "
            "Flux will not be able to pull from Gitea."
        )
        warn("Capture it from a working cluster with:")
        warn("  kubectl get secret -n flux-system flux-system -o jsonpath='{.data.identity}' | base64 -d")
    success(f"{FLUX_NAMESPACE}/{CLUSTER_VARS_SECRET}")

    for key, value in literals.items():
        click.echo(f"    {key:<16} {value}")


@bootstrap.command("gitea")
def bootstrap_gitea() -> None:
    """Deploy Gitea via the existing helm script.

    Gitea is deliberately not a Flux HelmRelease: Flux sources its GitRepository
    from Gitea, so Flux owning Gitea would be circular. talos/scripts/deploy-gitea.sh
    carries the accumulated workarounds (envsubst for TAILNET_DOMAIN,
    --force-conflicts for the field-manager conflict), so call it rather than
    reimplementing it here.
    """
    require("helm")

    if _gitea_running():
        info("gitea is already running")
        return

    # Its namespace, PVC and ExternalSecret live in the apps overlay, which
    # Flux cannot deliver yet — Flux reads from the Gitea this is deploying.
    rule("Gitea prerequisites")
    _ensure_namespace("gitea")
    _apply_direct(settings.kubernetes_dir() / "apps" / "gitea",
                  kinds={"PersistentVolumeClaim", "Service", "ExternalSecret"})

    script = settings.scripts_dir() / "deploy-gitea.sh"
    if not script.exists():
        fail(f"not found: {script}")
    rule("Gitea")
    run(["bash", str(script)])


@bootstrap.command("flux")
@click.option("--controllers-only", is_flag=True, help="Install the controllers, not the sync.")
@click.option("--sync-only", is_flag=True, help="Point Flux at the repo; assume controllers exist.")
def bootstrap_flux(controllers_only: bool, sync_only: bool) -> None:
    """Install the Flux controllers and point them at the repo.

    Split in two because the order matters on a cold cluster: the controllers
    have to exist before anything can reconcile, but the sync cannot succeed
    until Gitea is serving the repo it reads from.
    """
    require("kubectl")

    rule("Flux")
    if not sync_only:
        components = settings.kubernetes_dir() / "flux" / "flux-system" / "gotk-components.yaml"
        _kubectl("apply", "-f", str(components))
        info("waiting for the controllers")
        for _ in range(60):
            ok, ready = try_run(
                ["kubectl", "get", "deploy", "-n", FLUX_NAMESPACE,
                 "-o", "jsonpath={.items[*].status.readyReplicas}"],
                capture=True, echo=False)
            if ok and len([x for x in ready.split() if x == "1"]) >= 4:
                break
            time.sleep(5)
        success("Flux controllers running")

    if not controllers_only:
        _kubectl("apply", "-f",
                 str(settings.kubernetes_dir() / "flux" / "flux-system" / "gotk-sync.yaml"))
        _kubectl("apply", "-k", str(settings.kubernetes_dir() / "flux" / "kustomizations"))
        success("Flux pointed at the repo")


def _warn_if_worktree_differs() -> None:
    """Warn when the working tree is not what Flux will pull.

    The cold-start path applies manifests from the local checkout, but Flux
    reconciles whatever is committed on the tracked branch. If those differ —
    uncommitted edits, or an unmerged branch — Flux immediately tries to revert
    what bootstrap just applied, and for immutable objects like a StorageClass
    it cannot: "parameters: field is immutable", and the Kustomization stops.
    """
    ok, branch = try_run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                         capture=True, echo=False)
    if not ok:
        return
    branch = branch.strip()

    tracked = "master"
    if branch != tracked:
        warn(f"on branch '{branch}', but Flux reconciles '{tracked}'")
        warn("bootstrap applies the working tree; Flux will apply " + tracked)
        warn("anything immutable that differs between them will wedge the Kustomization")

    dirty, out = try_run(["git", "status", "--porcelain"], capture=True, echo=False)
    if dirty and out.strip():
        warn(f"{len(out.strip().splitlines())} uncommitted change(s) — Flux will not see them")


def _wait_cluster_ready(timeout: int = 600) -> bool:
    """Block until the API serves and a node is registered and Ready.

    terraform returns as soon as the bootstrap RPC succeeds, but etcd and the
    apiserver are still coming up and kubelet has not registered the node yet.
    Bootstrapping into that window fails on the first kubectl call with a bare
    "kubectl exited 1", which looks like a real error and is not.
    """
    info("waiting for the cluster to accept work")
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok, out = try_run(
            ["kubectl", "get", "nodes",
             "-o", "jsonpath={.items[*].status.conditions[?(@.type=='Ready')].status}"],
            capture=True, echo=False)
        if ok and "True" in out:
            success("cluster is ready")
            return True
        time.sleep(10)
    return False


def _substitutions() -> dict:
    """The values Flux would substitute via postBuild, read from cluster-vars."""
    ok, raw = try_run(
        ["kubectl", "get", "secret", "-n", FLUX_NAMESPACE, CLUSTER_VARS_SECRET,
         "-o", "json"], capture=True, echo=False)
    if not ok:
        return {}
    import base64
    return {
        k: base64.b64decode(v).decode()
        for k, v in (json.loads(raw).get("data") or {}).items()
    }


def _apply_direct(path, kinds: set[str] | None = None) -> bool:
    """Apply an overlay without Flux, doing the postBuild substitution here.

    Needed only for a cold start: Flux sources from Gitea, and on a new cluster
    Gitea does not exist yet, so the things Gitea itself depends on — the CSI
    driver, External Secrets, its own namespace and PVC — cannot come from Flux.
    """
    import yaml as _yaml

    text = run(["kubectl", "kustomize", str(path)], capture=True, echo=False)
    for key, value in _substitutions().items():
        text = text.replace("${" + key + "}", value)
    # Flux renders $${x} as a literal ${x}; nothing else will do it here.
    text = text.replace("$${", "${")

    if kinds:
        docs = [d for d in _yaml.safe_load_all(text) if d and d.get("kind") in kinds]
        if not docs:
            return True
        text = "\n---\n".join(_yaml.safe_dump(d) for d in docs)

    ok, err = try_run(["kubectl", "apply", "-f", "-"], input_text=text,
                      capture=True, echo=False)
    if ok:
        return True

    lines = [line for line in err.splitlines() if line.strip()]
    real = [line for line in lines if not line.lstrip().startswith("Warning:")]

    if not real:
        # Nothing but warnings. The coredns ConfigMap produces one every single
        # time — Talos creates it as an inline manifest, so it can never carry
        # the last-applied-configuration annotation kubectl wants — and calling
        # that a failed apply sends you hunting for a problem that is not
        # there. Worse, it trains you to ignore the line that matters when an
        # apply really does fail.
        return True

    # The real cause is usually the last thing kubectl says, not the first:
    # truncating the head of stderr showed the coredns warning and hid it.
    warn(f"apply of {path.name} failed:")
    for line in real[-8:]:
        warn(f"  {line[:200]}")
    return False


def _gitea_running() -> bool:
    ok, out = try_run(
        ["kubectl", "get", "deploy", "-n", "gitea", "gitea",
         "-o", "jsonpath={.status.readyReplicas}"], capture=True, echo=False)
    return ok and out.strip() not in ("", "0")


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
    """Bring a cluster from empty to fully reconciling.

    The order exists because of one circular dependency: Flux reads the repo
    from Gitea, and Gitea runs on the cluster Flux is building. So the things
    Gitea needs — the CSI driver for its volume, External Secrets for its admin
    credential — are applied directly first, Gitea is deployed, and only then is
    Flux pointed at the repo. From that moment Flux owns everything.

    Every step is idempotent; on a cluster that already has Gitea the direct
    applies are skipped.
    """
    from .nas import nas_verify

    _warn_if_worktree_differs()

    if not _wait_cluster_ready():
        fail("cluster did not become ready — nothing was changed")

    ctx.invoke(bootstrap_secrets)

    # 1. Controllers only. helm-controller has to exist before any HelmRelease.
    ctx.invoke(bootstrap_flux, controllers_only=True, sync_only=False)

    # 2. Infrastructure, applied directly because Flux has no source yet:
    #    the CSI driver, External Secrets and the Tailscale operator.
    if not _gitea_running():
        rule("Infrastructure (direct, pre-Gitea)")
        _apply_direct(settings.kubernetes_dir() / "flux" / "repositories")
        _apply_direct(settings.kubernetes_dir() / "infrastructure")
        info("waiting for the CSI driver and External Secrets")
        for _ in range(90):
            ok, out = try_run(
                ["kubectl", "get", "helmrelease", "-A",
                 "-o", "jsonpath={.items[*].status.conditions[?(@.type=='Ready')].status}"],
                capture=True, echo=False)
            if ok and out.count("True") >= 2:
                break
            time.sleep(10)
        _apply_direct(settings.kubernetes_dir() / "infrastructure" / "external-secrets" / "store")

    # 3. Gitea, so Flux has something to read.
    ctx.invoke(bootstrap_gitea)

    # 4. Now Flux can sync. Hold apps back until the NAS is proven.
    suspended = False
    if not skip_nas_gate:
        rule("Gating apps on NAS availability")
        suspended = _suspend_apps()

    ctx.invoke(bootstrap_flux, controllers_only=False, sync_only=True)

    if skip_nas_gate:
        rule("Bootstrap complete")
        warn("NAS gate skipped — apps may start without their storage")
        return

    if not suspended:
        for _ in range(30):
            if _suspend_apps():
                suspended = True
                break
            time.sleep(2)

    info("Waiting for infrastructure to reconcile")
    if not _wait_kustomization("infrastructure", timeout=600):
        fail("infrastructure did not become Ready — apps left suspended")

    try:
        ctx.invoke(nas_verify, storage_class=None, timeout=180)
    except SystemExit:
        warn("apps remain SUSPENDED — nothing deployed against bad storage")
        warn("fix NAS access, then: flux resume kustomization apps")
        raise

    if suspended:
        info("NAS verified — releasing apps")
        run(["flux", "resume", "kustomization", "apps"], check=False)

    rule("Bootstrap complete")
    info("Watch reconciliation with: ./provision status")
