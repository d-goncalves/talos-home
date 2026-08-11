"""Enable and disable apps, and regenerate the apps kustomization."""

from __future__ import annotations

import click

import json

from .. import kustomize, settings
from ..console import fail, info, rule, success, warn
from ..shell import require, run, try_run

PRUNE_ANNOTATION = "kustomize.toolkit.fluxcd.io/prune"


def _live_protection(app_name: str) -> tuple[str, list[str]]:
    """Check whether the app's data objects are already prune-exempt in-cluster.

    Returns (status, unprotected) where status is "protected", "unprotected"
    or "unknown" (cluster unreachable).

    This matters because Flux reads the annotation from the *live* object, not
    from git. An object that leaves the source in the same revision that would
    have introduced its annotation never receives it, and gets pruned.
    """
    ok, _ = try_run(["kubectl", "get", "namespace", app_name, "-o", "json"],
                    capture=True, echo=False)
    if not ok:
        return "unknown", []

    unprotected: list[str] = []

    ns_raw = run(["kubectl", "get", "namespace", app_name, "-o", "json"],
                 capture=True, echo=False)
    ns = json.loads(ns_raw)
    if (ns.get("metadata", {}).get("annotations") or {}).get(PRUNE_ANNOTATION) != "disabled":
        unprotected.append(f"Namespace/{app_name}")

    pvc_ok, _ = try_run(
        ["kubectl", "get", "pvc", "-n", app_name, "-o", "json"],
        capture=True, echo=False,
    )
    if pvc_ok:
        raw = run(["kubectl", "get", "pvc", "-n", app_name, "-o", "json"],
                  capture=True, echo=False)
        for item in json.loads(raw).get("items", []):
            annotations = item.get("metadata", {}).get("annotations") or {}
            if annotations.get(PRUNE_ANNOTATION) != "disabled":
                unprotected.append(f"PersistentVolumeClaim/{item['metadata']['name']}")

    return ("protected" if not unprotected else "unprotected"), unprotected


def _sync(quiet: bool = False) -> bool:
    apps = settings.apps()

    # Catch drift both ways: a directory nobody declared, or a declaration
    # pointing at a directory that no longer exists.
    declared = {app.name for app in apps}
    present = settings.on_disk()

    for name in sorted(present - declared):
        warn(f"kubernetes/apps/{name}/ exists but is not in provision.toml — ignored")
    for name in sorted(declared - present):
        fail(f"provision.toml declares '{name}' but kubernetes/apps/{name}/ does not exist")

    changed = kustomize.write(apps)
    if not quiet:
        path = settings.kustomization_path().relative_to(settings.repo_root())
        success(f"wrote {path}" if changed else f"{path} already up to date")
    return changed


@click.group("apps")
def apps() -> None:
    """Enable or disable the apps Flux deploys."""


@apps.command("list")
def apps_list() -> None:
    """Show every app and whether it is enabled."""
    entries = settings.apps()
    width = max(len(app.name) for app in entries)

    rule("Apps")
    for app in entries:
        mark = click.style("on ", fg="green") if app.enabled else click.style("off", fg="yellow")
        click.echo(f"  {mark}  {app.name:<{width}}")

    on = sum(1 for app in entries if app.enabled)
    click.echo()
    info(f"{on} enabled, {len(entries) - on} disabled")


@apps.command("enable")
@click.argument("names", nargs=-1, required=True)
def apps_enable(names: tuple[str, ...]) -> None:
    """Enable one or more apps."""
    for name in names:
        if settings.set_enabled(name, True):
            success(f"enabled {name}")
        else:
            info(f"{name} was already enabled")
    _sync()
    info("Commit and push so Flux picks it up.")


@apps.command("disable")
@click.argument("names", nargs=-1, required=True)
@click.option(
    "--force",
    is_flag=True,
    help="Disable even if the live data objects are not yet prune-exempt.",
)
def apps_disable(names: tuple[str, ...], force: bool) -> None:
    """Disable one or more apps.

    Workloads are pruned, so CPU and memory come back. Namespaces, PVCs and PVs
    are prune-exempt, so data survives and re-enabling reattaches the same
    volumes.

    Refuses to proceed if the app's live Namespace and PVCs do not already
    carry the prune-exempt annotation. Flux reads that annotation from the
    object in the cluster, not from git, so an app removed from the source
    before its annotation was applied would have its data deleted. Run
    `provision apps protect` first.
    """
    changed = False

    for name in names:
        # Validates the name against [apps] before anything else happens.
        settings.get_app(name)

        if name == "gitea":
            warn(
                "gitea is the GitOps source of truth — Flux reads its "
                "GitRepository from it. Disabling it stops all reconciliation."
            )
            if not click.confirm("Disable it anyway?", default=False):
                continue

        status, unprotected = _live_protection(name)

        if status == "unprotected" and not force:
            warn(f"{name}: these live objects are not prune-exempt yet:")
            for item in unprotected:
                click.echo(f"      {item}")
            fail(
                "Disabling now would let Flux delete them.\n"
                "  Run `provision apps protect`, let Flux reconcile, then retry."
            )

        if status == "unknown":
            warn(f"{name}: cluster unreachable — cannot confirm prune protection")
            if not force and not click.confirm(
                "  Proceed anyway? Data loss is possible if the annotation "
                "was never applied",
                default=False,
            ):
                continue

        if settings.set_enabled(name, False):
            changed = True
            success(f"disabled {name}")
        else:
            info(f"{name} was already disabled")

    if changed:
        _sync()
        info("Commit and push so Flux prunes the workloads.")


@apps.command("protect")
@click.option("--dry-run", is_flag=True, help="Show what would be annotated.")
def apps_protect(dry_run: bool) -> None:
    """Apply the prune-exempt annotation to live Namespaces, PVCs and PVs.

    Flux reads the annotation from the cluster, not from git, so data objects
    must be annotated *before* their app is removed from the source. Flux
    applies these annotations on its own once the generated kustomization is
    committed; this command does it immediately so you do not have to wait for
    a reconcile before disabling something.
    """
    require("kubectl")

    ok, _ = try_run(["kubectl", "get", "namespaces"], capture=True, echo=False)
    if not ok:
        fail("cannot reach the cluster")

    targets = [app.name for app in settings.apps() if app.enabled]
    verb = "would annotate" if dry_run else "annotating"

    rule("Prune protection")
    for name in targets:
        exists, _ = try_run(
            ["kubectl", "get", "namespace", name], capture=True, echo=False
        )
        if not exists:
            continue

        info(f"{verb} {name}")
        if dry_run:
            continue

        for args in (
            ["namespace", name],
            ["pvc", "--all", "-n", name],
            ["pv", "--all"],
        ):
            try_run(
                ["kubectl", "annotate", *args,
                 f"{PRUNE_ANNOTATION}=disabled", "--overwrite"],
                capture=True,
                echo=False,
            )

    if dry_run:
        return
    success("annotations applied")
    info("Verify with: provision apps protect --dry-run")


@apps.command("check")
def apps_check() -> None:
    """Verify the kustomization matches provision.toml and still builds."""
    apps_config = settings.apps()
    content = kustomize.render(apps_config)
    path = settings.kustomization_path()

    current = path.read_text() if path.exists() else ""
    if current != content:
        fail("kustomization.yaml is out of date — run `provision apps sync`")
    success("kustomization.yaml matches provision.toml")

    require("kubectl")
    ok, err = try_run(
        ["kubectl", "kustomize", str(settings.apps_dir())],
        capture=True,
        echo=False,
    )
    if not ok:
        fail(f"kustomize build failed:\n{err}")
    success("kustomize build succeeds")


@apps.command("vars")
def apps_vars() -> None:
    """Check every `${VAR}` in the manifests against cluster-vars.

    Fails on a variable Flux would substitute but has no value for, and on one
    a container expands at runtime that was left unescaped. `kubectl kustomize`
    does no substitution, so a build passing proves nothing about either.
    """
    from .. import substitution

    rule("Substitution audit")

    problems = substitution.unknown_vars(settings.repo_root())

    if problems:
        for p in problems:
            click.echo(f"  {click.style('✗', fg='red')} {p}")
        click.echo()
        fail(f"{len(problems)} variable(s) Flux cannot substitute")

    success("every ${VAR} is defined in cluster-vars or escaped for the shell")


@apps.command("diff")
def apps_diff() -> None:
    """Show what `sync` would change."""
    content = kustomize.render(settings.apps())
    path = settings.kustomization_path()

    if not path.exists():
        click.echo(content)
        return

    import difflib

    diff = difflib.unified_diff(
        path.read_text().splitlines(keepends=True),
        content.splitlines(keepends=True),
        fromfile=str(path),
        tofile="generated",
    )
    output = "".join(diff)
    if not output:
        info("no changes")
        return

    for line in output.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            click.echo(click.style(line, fg="green"))
        elif line.startswith("-") and not line.startswith("---"):
            click.echo(click.style(line, fg="red"))
        else:
            click.echo(line)


@apps.command("storage")
def apps_storage() -> None:
    """Audit storage so a rebuild loses nothing.

    Fails if any PVC keeps real data on local-path, if an NFS-backed Deployment
    could run two pods at once, or if an NFS StorageClass would provision onto
    a random path that a rebuilt cluster cannot find again.
    """
    from .. import storage

    require("kubectl")
    rule("Storage audit")

    apps_build = run(["kubectl", "kustomize", str(settings.apps_dir())],
                     capture=True, echo=False)
    infra = run(["kubectl", "kustomize",
                 str(settings.repo_root() / "kubernetes" / "infrastructure")],
                capture=True, echo=False)

    problems = storage.audit(apps_build) + storage.storage_class_problems(infra)

    if problems:
        for p in problems:
            click.echo(f"  {click.style('✗', fg='red')} {p}")
        click.echo()
        fail(f"{len(problems)} storage issue(s) — a rebuild would lose data")

    success("all persistent data is on deterministic NFS paths")
    success("every NFS-backed Deployment uses Recreate")
