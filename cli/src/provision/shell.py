"""Thin subprocess wrapper.

Everything the CLI does is orchestration of terraform, talosctl, kubectl, flux
and op. Keeping the logic in those tools rather than reimplementing it in
Python is deliberate: the CLI should be a dispatcher, not a second source of
truth.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import click

from .console import fail


class CommandError(RuntimeError):
    def __init__(self, argv: list[str], returncode: int, stderr: str = "") -> None:
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"{argv[0]} exited {returncode}")


def which(tool: str) -> str | None:
    return shutil.which(tool)


def require(tool: str, hint: str = "") -> str:
    path = which(tool)
    if path is None:
        suffix = f" ({hint})" if hint else ""
        fail(f"{tool} not found on PATH{suffix}")
    return path


def run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    check: bool = True,
    echo: bool = True,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> str:
    """Run a command. Returns stdout when capture=True, otherwise "".

    Streams straight to the terminal by default so long-running terraform and
    talosctl output is visible as it happens.
    """
    if echo and not capture:
        click.echo(click.style(f"  $ {' '.join(argv)}", dim=True))

    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        input=input_text,
        capture_output=capture,
        text=True,
        check=False,
    )

    if check and result.returncode != 0:
        raise CommandError(argv, result.returncode, result.stderr or "")

    return (result.stdout or "") if capture else ""


def try_run(argv: list[str], **kwargs) -> tuple[bool, str]:
    """Run a command, returning (ok, output) instead of raising."""
    try:
        out = run(argv, check=True, **kwargs)
        return True, out
    except CommandError as exc:
        return False, exc.stderr
