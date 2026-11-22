"""Command-line interface. Only the `version` command exists until the CLI package lands."""

from __future__ import annotations

import typer

from kestrel import __version__

app = typer.Typer(help="Assistant: governed, cited answers from approved documents.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Assistant command line."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(f"kestrel {__version__}")
