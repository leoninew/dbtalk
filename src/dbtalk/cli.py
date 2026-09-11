"""Click root command and process lifecycle."""

from __future__ import annotations

import click

from dbtalk import __version__
from dbtalk.cli_runtime import DbtalkGroup
from dbtalk.commands import mysql, postgres
from dbtalk.context import DbtalkContext
from dbtalk.database.cli import database as database_operations
from dbtalk.logging_config import configure_logging
from dbtalk.settings import load_settings

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(
    cls=DbtalkGroup,
    context_settings=CONTEXT_SETTINGS,
    invoke_without_command=True,
)
@click.version_option(version=__version__, prog_name="dbtalk")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Run database backup, restore, transfer, query, and execution operations."""
    try:
        settings = load_settings()
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    configure_logging(
        settings.logging.level,
        settings.logging.format,
        settings.verbose,
    )
    ctx.obj = DbtalkContext(settings=settings, verbose=settings.verbose)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


cli.add_command(mysql)
cli.add_command(postgres)
for _name, _command in database_operations.commands.items():
    cli.add_command(_command, name=_name)


def main() -> None:
    """Invoke the command-line application."""
    cli()
