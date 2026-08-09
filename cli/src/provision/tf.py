"""Terraform / OpenTofu wrapper.

Both binaries are supported. The repo pins `required_version = ">= 1.5.0"`,
which is satisfied by Terraform 1.5.7 (the last MPL release) and by every
OpenTofu release, so neither choice is forced.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .console import fail, warn
from .shell import CommandError, run, which

# OpenTofu first: if both are installed, the newer one is more likely intended.
_CANDIDATES = ("tofu", "terraform")


def binary() -> str:
    override = os.environ.get("PROVISION_TF_BIN")
    if override:
        if which(override) is None:
            fail(f"PROVISION_TF_BIN={override} is not on PATH")
        return override

    for candidate in _CANDIDATES:
        if which(candidate):
            return candidate

    fail("neither tofu nor terraform found on PATH")


def repo_root() -> Path:
    """Locate the repo root.

    Resolving from __file__ only works for an editable install, where the
    package still lives at <repo>/cli/src/provision. Fall back to git so a
    normal `pip install .` does not silently point at site-packages.
    """
    override = os.environ.get("PROVISION_REPO_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "terraform").is_dir() and (candidate / "kubernetes").is_dir():
        return candidate

    try:
        top = run(
            ["git", "rev-parse", "--show-toplevel"],
            capture=True,
            echo=False,
        ).strip()
        if top:
            return Path(top)
    except CommandError:
        pass

    fail(
        "could not locate the repo root. Run from inside the repo, or set "
        "PROVISION_REPO_ROOT."
    )


def tf_dir() -> Path:
    path = repo_root() / "terraform"
    if not path.is_dir():
        fail(f"terraform directory not found at {path}")
    return path


def ensure_tfvars() -> None:
    """Regenerate terraform.tfvars.json from provision.toml before running.

    Imported lazily: settings imports repo_root from this module.
    """
    from . import settings

    unfilled = settings.unfilled()
    if unfilled:
        fail(
            "provision.toml still has placeholder values:\n  "
            + "\n  ".join(unfilled)
        )

    settings.write_tfvars()


def initialized() -> bool:
    return (tf_dir() / ".terraform").is_dir()


def op_env() -> dict[str, str]:
    """Environment for terraform, with 1Password auth passed through.

    The onepassword provider reads OP_SERVICE_ACCOUNT_TOKEN or OP_ACCOUNT from
    the environment and fetches the credentials itself, so the CLI never
    handles a secret value. If neither is set, fall back to the account the
    local `op` CLI is signed in to, which uses desktop biometrics.
    """
    env = dict(os.environ)

    if "OP_SERVICE_ACCOUNT_TOKEN" in env or "OP_ACCOUNT" in env:
        return env

    from . import onepassword as op

    account = op.default_account()
    if account:
        env["OP_ACCOUNT"] = account
    return env


def tf(
    *args: str,
    capture: bool = False,
    check: bool = True,
    with_secrets: bool = False,
) -> str:
    return run(
        [binary(), *args],
        cwd=tf_dir(),
        capture=capture,
        check=check,
        env=op_env() if with_secrets else None,
    )


def init(upgrade: bool = False) -> None:
    args = ["init", "-input=false"]
    if upgrade:
        args.append("-upgrade")
    tf(*args)


def ensure_init() -> None:
    if not initialized():
        warn("terraform not initialized — running init")
        init()


def outputs() -> dict:
    """Return all terraform outputs as {name: value}, unwrapping the metadata."""
    ensure_init()
    try:
        raw = tf("output", "-json", capture=True)
    except CommandError as exc:
        fail(f"could not read terraform outputs: {exc.stderr.strip() or exc}")

    if not raw.strip():
        return {}

    parsed = json.loads(raw)
    return {key: meta.get("value") for key, meta in parsed.items()}


def output(name: str, default=None):
    return outputs().get(name, default)


def require_output(name: str):
    value = output(name)
    if value in (None, ""):
        fail(
            f"terraform output '{name}' is empty. "
            "Has `provision apply` run successfully?"
        )
    return value


def state_path() -> Path:
    return tf_dir() / "terraform.tfstate"


def has_state() -> bool:
    path = state_path()
    return path.exists() and path.stat().st_size > 0
