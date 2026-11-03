"""`griddesk` command line. Subcommands are added as features land."""

from __future__ import annotations

import typer

from griddesk import __version__

app = typer.Typer(
    help="GridDesk: utility customer-ops agent and its evaluation harness.", no_args_is_help=True
)


@app.callback()
def main() -> None:
    """GridDesk command line."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
