"""Terminal output helpers, matching the style of homelab/scripts/recover.sh."""

from __future__ import annotations

import sys

import click


def info(message: str) -> None:
    click.echo(f"{click.style('==>', fg='green')} {message}")


def warn(message: str) -> None:
    click.echo(f"{click.style('==>', fg='yellow')} {message}")


def success(message: str) -> None:
    click.echo(f"{click.style('✓', fg='green')} {message}")


def error(message: str) -> None:
    click.echo(f"{click.style('✗', fg='red')} {message}", err=True)


def fail(message: str, code: int = 1) -> None:
    """Print an error and exit."""
    error(message)
    sys.exit(code)


def rule(title: str = "") -> None:
    width = 63
    if title:
        click.echo(f"\n{'━' * width}\n  {click.style(title, bold=True)}")
    else:
        click.echo("━" * width)
