from __future__ import annotations

from pathlib import Path

import click

from dbtalk.context import dbtalk_context
from dbtalk.database.dsn import dsn_from_environment, parse_dsn
from dbtalk.database.models import DatabaseOperationError
from dbtalk.settings import Settings

from .client import mysql_client_args, mysql_connection_args
from .database import schema as schema_management
from .dump import (
    MysqlDumpOptions,
    MysqlDumpOverrides,
    default_dump_output,
    dump_database,
    generate_dump_command,
    mysqldump_args,
    resolve_dump_options,
)
from .permissions import permissions
from .restore import (
    MysqlRestoreOptions,
    MysqlRestoreOverrides,
    generate_restore_command,
    mysql_restore_args,
    resolve_restore_options,
    restore_database,
)
from .user import grant_command, revoke_command, user

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

__all__ = [
    "MysqlDumpOptions",
    "MysqlDumpOverrides",
    "MysqlRestoreOptions",
    "MysqlRestoreOverrides",
    "default_dump_output",
    "dump_database",
    "generate_dump_command",
    "generate_restore_command",
    "mysql",
    "mysql_client_args",
    "mysql_connection_args",
    "mysql_restore_args",
    "mysqldump_args",
    "resolve_dump_options",
    "resolve_restore_options",
    "restore_database",
]


@click.group("mysql", context_settings=CONTEXT_SETTINGS)
def mysql() -> None:
    """Run MySQL dump and restore operations."""


mysql.add_command(schema_management)
mysql.add_command(user)
mysql.add_command(grant_command)
mysql.add_command(revoke_command)
mysql.add_command(permissions)


@mysql.command("dump", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete MySQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the MySQL DSN.")
@click.option(
    "--database",
    "target_database",
    metavar="TARGET",
    help="Database to dump. Defaults to the DSN database.",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    help=(
        "SQL dump output file or existing directory. Defaults to a timestamped file "
        "in mysql.output_directory."
    ),
)
@click.option(
    "--skip-definer",
    is_flag=True,
    help="Pass --skip-definer to mysqldump.",
)
@click.option(
    "--archive",
    "archive",
    is_flag=True,
    help="Write the SQL dump as a gzip file.",
)
@click.pass_context
def dump_command(  # noqa: PLR0913 - Click passes one argument for each CLI option.
    ctx: click.Context,
    dsn_value: str | None,
    dsn_env: str | None,
    target_database: str | None,
    output: Path | None,
    archive: bool,
    skip_definer: bool,
) -> None:
    """Export a MySQL database."""
    settings = context_settings(ctx)
    host, port, user, password, dsn_database = mysql_connection_from_dsn(dsn_value, dsn_env)
    options = resolve_dump_options(
        settings.mysql,
        MysqlDumpOverrides(
            host=host,
            port=port,
            user=user,
            password=password,
            target_database=target_database,
            dsn_database=dsn_database,
            output=output,
            archive=archive,
            skip_definer=skip_definer,
        ),
    )
    completed_output = dump_database(options)
    click.echo(f"MySQL dump written to {completed_output}")


@mysql.command("restore", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete MySQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the MySQL DSN.")
@click.option(
    "--input",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="SQL dump input path.",
)
@click.option(
    "--database",
    "target_database",
    metavar="TARGET",
    help=("Existing database to receive the dump. Defaults to the DSN database."),
)
@click.pass_context
def restore_command(  # noqa: PLR0913 - Click passes one argument for each CLI option.
    ctx: click.Context,
    dsn_value: str | None,
    dsn_env: str | None,
    input: Path,
    target_database: str | None,
) -> None:
    """Import a MySQL dump."""
    settings = context_settings(ctx)
    host, port, user, password, dsn_database = mysql_connection_from_dsn(dsn_value, dsn_env)
    options = resolve_restore_options(
        settings.mysql,
        MysqlRestoreOverrides(
            host=host,
            port=port,
            user=user,
            password=password,
            input=input,
            target_database=target_database,
            dsn_database=dsn_database,
        ),
    )
    restored_input = restore_database(options)
    click.echo(f"MySQL dump restored from {restored_input}")


def context_settings(ctx: click.Context) -> Settings:
    return dbtalk_context(ctx).settings


def mysql_connection_from_dsn(
    dsn: str | None,
    dsn_env: str | None,
) -> tuple[str, int, str, str, str | None]:
    """Resolve one explicit MySQL DSN into native client connection fields."""

    if (dsn is None) == (dsn_env is None):
        raise click.UsageError("provide exactly one of --dsn or --dsn-env")
    try:
        parsed = parse_dsn(dsn) if dsn is not None else dsn_from_environment(dsn_env)
    except DatabaseOperationError as error:
        raise click.UsageError(str(error)) from error
    if parsed.dialect != "mysql":
        raise click.UsageError("MySQL dump and restore require a mysql+pymysql DSN")
    host = parsed.host
    user = parsed.url.username
    database = parsed.database
    if not host or not user:
        raise click.UsageError("MySQL DSN must include host and user")
    return host, parsed.port or 3306, user, parsed.url.password or "", database
