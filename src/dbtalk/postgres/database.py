"""PostgreSQL database lifecycle commands and operations."""

from __future__ import annotations

from collections.abc import Callable
from unicodedata import category

import click
from sqlalchemy.engine import Connection, Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError
from tabulate import tabulate

from dbtalk.database.dsn import ParsedDsn, dsn_from_environment, parse_dsn
from dbtalk.database.models import DatabaseOperationError

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group("schema", context_settings=CONTEXT_SETTINGS)
def schema() -> None:
    """Manage PostgreSQL schemas and databases."""


@schema.command("list", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
def list_command(dsn_value: str | None, dsn_env: str | None) -> None:
    """List visible PostgreSQL databases."""

    try:
        click.echo(
            _render_database_names(list_databases(resolve_management_dsn(dsn_value, dsn_env)))
        )
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error


@schema.command("create", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option("--name", required=True, help="Database name.")
def create_command(dsn_value: str | None, dsn_env: str | None, name: str) -> None:
    """Create one PostgreSQL database."""

    try:
        create_database(resolve_management_dsn(dsn_value, dsn_env), name)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Database created: {name}")


@schema.command("drop", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option("--name", required=True, help="Database name.")
@click.option("--yes", is_flag=True, help="Confirm database deletion.")
def drop_command(dsn_value: str | None, dsn_env: str | None, name: str, yes: bool) -> None:
    """Delete one PostgreSQL database."""

    if not yes:
        raise click.UsageError("--yes is required to drop a database")
    try:
        drop_database(resolve_management_dsn(dsn_value, dsn_env), name)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Database dropped: {name}")


def resolve_management_dsn(dsn: str | None, environment_name: str | None) -> ParsedDsn:
    """Resolve one explicit synchronous PostgreSQL management DSN."""

    if (dsn is None) == (environment_name is None):
        raise DatabaseOperationError("provide exactly one of --dsn or --dsn-env")
    parsed = parse_dsn(dsn) if dsn is not None else dsn_from_environment(environment_name)
    if parsed.dialect != "postgresql":
        raise DatabaseOperationError("PostgreSQL database management requires a postgresql DSN")
    return parsed


def list_databases(parsed: ParsedDsn) -> tuple[str, ...]:
    """Return non-template, connectable PostgreSQL database names."""

    _validate_management_dsn(parsed)
    return _run_management_operation(
        parsed,
        lambda connection: tuple(
            sorted(
                str(row[0])
                for row in connection.exec_driver_sql(
                    "SELECT datname FROM pg_database "
                    "WHERE NOT datistemplate AND datallowconn "
                    "ORDER BY datname"
                ).fetchall()
            )
        ),
    )


def create_database(parsed: ParsedDsn, name: str) -> None:
    """Create one PostgreSQL database using server defaults."""

    _validate_management_dsn(parsed)
    _validate_database_name(name)
    _run_management_operation(
        parsed,
        lambda connection: connection.exec_driver_sql(
            f"CREATE DATABASE {_quote_database_name(connection, name)}"
        ),
    )


def drop_database(parsed: ParsedDsn, name: str) -> None:
    """Drop one PostgreSQL database after explicit CLI confirmation."""

    _validate_management_dsn(parsed)
    _validate_database_name(name)
    if parsed.database is None:
        raise DatabaseOperationError(
            "PostgreSQL database deletion requires a DSN connected to a maintenance database"
        )
    if parsed.database == name:
        raise DatabaseOperationError(
            "PostgreSQL database deletion requires a DSN connected to a different "
            "maintenance database"
        )
    _run_management_operation(
        parsed,
        lambda connection: connection.exec_driver_sql(
            f"DROP DATABASE {_quote_database_name(connection, name)}"
        ),
    )


def _run_management_operation[Result](
    parsed: ParsedDsn,
    operation: Callable[[Connection], Result],
) -> Result:
    engine: Engine | None = None
    try:
        engine = create_engine(parsed.url)
        with engine.connect() as connection:
            return operation(connection.execution_options(isolation_level="AUTOCOMMIT"))
    except SQLAlchemyError as error:
        raise DatabaseOperationError("PostgreSQL database management failed") from error
    finally:
        if engine is not None:
            engine.dispose()


def _validate_management_dsn(parsed: ParsedDsn) -> None:
    if parsed.async_mode or parsed.dialect != "postgresql":
        raise DatabaseOperationError(
            "PostgreSQL database management requires a synchronous postgresql DSN"
        )


def _validate_database_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip():
        raise DatabaseOperationError("database name must not be blank")
    if any(character == "\x00" or category(character).startswith("C") for character in name):
        raise DatabaseOperationError("database name must not contain control characters")


def _quote_database_name(connection: Connection, name: str) -> str:
    return connection.dialect.identifier_preparer.quote(name)


def _render_database_names(names: tuple[str, ...]) -> str:
    rendered = tabulate(((name,) for name in names), headers=("database",), tablefmt="psql")
    return f"{rendered}\n(0 rows)" if not names else rendered


__all__ = [
    "create_database",
    "schema",
    "drop_database",
    "list_databases",
    "resolve_management_dsn",
]

database = schema
