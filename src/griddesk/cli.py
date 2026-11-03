"""`griddesk` command line. Subcommands are added as features land."""

from __future__ import annotations

import typer

from griddesk import __version__

app = typer.Typer(
    help="GridDesk: utility customer-ops agent and its evaluation harness.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,  # locals can hold secrets
)


@app.callback()
def main() -> None:
    """GridDesk command line."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


db_app = typer.Typer(help="Database commands.", no_args_is_help=True)
app.add_typer(db_app, name="db")


@db_app.command("seed")
def db_seed(seed: int = typer.Option(20261015, help="Random seed for the generator.")) -> None:
    """Replace the local database contents with freshly generated synthetic data."""
    from griddesk.db.seed import generate, load
    from griddesk.db.session import get_engine

    counts = load(get_engine(), generate(seed))
    for table, n in counts.items():
        if n:
            typer.echo(f"{table:22s} {n:>7,}")


if __name__ == "__main__":
    app()
