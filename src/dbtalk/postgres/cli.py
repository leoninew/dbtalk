"""Click commands for PostgreSQL native logical backups."""

from __future__ import annotations

from pathlib import Path

import click

from dbtalk.context import dbtalk_context
from dbtalk.database.dsn import dsn_from_environment, parse_dsn
from dbtalk.database.models import DatabaseOperationError
from dbtalk.settings import Settings

from .client import PostgresConnection
from .database import schema as schema_management
from .dump import PostgresDumpOptions, dump_database, resolve_dump_options
from .permissions import permissions
from .restore import PostgresRestoreOptions, restore_database
from .role import grant_command, revoke_command, role

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

__all__ = [
    "PostgresDumpOptions",
    "PostgresRestoreOptions",
    "dump_database",
    "postgres",
    "postgres_connection_from_dsn",
    "restore_database",
]


@click.group("postgres", context_settings=CONTEXT_SETTINGS)
def postgres() -> None:
    """Run PostgreSQL custom archive dump and restore operations."""


postgres.add_command(schema_management)
postgres.add_command(role)
postgres.add_command(grant_command)
postgres.add_command(revoke_command)
postgres.add_command(permissions)


@postgres.command("dump", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option(
    "--database",
    "target_database",
    metavar="TARGET",
    help="Database to dump. Defaults to the DSN database.",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    help="Custom archive output file or existing directory. Defaults to postgres.output_directory.",
)
@click.option(
    "--compression-level",
    type=click.IntRange(0, 9),
    help="Native custom archive compression level. Uses the client default when omitted.",
)
@click.pass_context
def dump_command(
    ctx: click.Context,
    dsn_value: str | None,
    dsn_env: str | None,
    target_database: str | None,
    output: Path | None,
    compression_level: int | None,
) -> None:
    """Export one PostgreSQL database as a custom archive."""

    settings = context_settings(ctx)
    connection = postgres_connection_from_dsn(
        dsn_value,
        dsn_env,
        target_database=target_database,
    )
    options = resolve_dump_options(
        settings.postgres,
        connection,
        output,
        compression_level,
    )
    completed_output = dump_database(options)
    click.echo(f"PostgreSQL dump written to {completed_output}")


@postgres.command("restore", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option(
    "--database",
    "target_database",
    metavar="TARGET",
    help="Existing database to receive the archive. Defaults to the DSN database.",
)
@click.option(
    "--input",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="PostgreSQL custom archive input path.",
)
@click.option("--clean", is_flag=True, help="Drop target objects before restoring them.")
@click.option(
    "--if-exists",
    is_flag=True,
    help="Ignore missing objects while using --clean.",
)
@click.option(
    "--preserve-owner",
    is_flag=True,
    help="Restore archive owner definitions instead of skipping them.",
)
@click.option(
    "--preserve-privileges",
    is_flag=True,
    help="Restore archive ACL definitions instead of skipping them.",
)
@click.option("--jobs", type=click.IntRange(min=1), help="Parallel pg_restore jobs.")
@click.pass_context
def restore_command(
    ctx: click.Context,
    dsn_value: str | None,
    dsn_env: str | None,
    target_database: str | None,
    input: Path,
    clean: bool,
    if_exists: bool,
    preserve_owner: bool,
    preserve_privileges: bool,
    jobs: int | None,
) -> None:
    """Restore one PostgreSQL custom archive into an existing database."""

    if if_exists and not clean:
        raise click.UsageError("--if-exists requires --clean")
    settings = context_settings(ctx)
    connection = postgres_connection_from_dsn(
        dsn_value,
        dsn_env,
        target_database=target_database,
    )
    restored_input = restore_database(
        PostgresRestoreOptions(
            connection=connection,
            input=input,
            client_image=settings.postgres.client_image,
            clean=clean,
            if_exists=if_exists,
            preserve_owner=preserve_owner,
            preserve_privileges=preserve_privileges,
            jobs=jobs,
        )
    )
    click.echo(f"PostgreSQL dump restored from {restored_input}")


def context_settings(ctx: click.Context) -> Settings:
    return dbtalk_context(ctx).settings


def postgres_connection_from_dsn(
    dsn: str | None,
    dsn_env: str | None,
    *,
    target_database: str | None = None,
) -> PostgresConnection:
    """Resolve one explicit PostgreSQL DSN into native client connection details."""

    if (dsn is None) == (dsn_env is None):
        raise click.UsageError("provide exactly one of --dsn or --dsn-env")
    try:
        parsed = parse_dsn(dsn) if dsn is not None else dsn_from_environment(dsn_env)
    except DatabaseOperationError as error:
        raise click.UsageError(str(error)) from error
    try:
        return PostgresConnection.from_parsed_dsn(parsed, database=target_database)
    except ValueError as error:
        raise click.UsageError(str(error)) from error
