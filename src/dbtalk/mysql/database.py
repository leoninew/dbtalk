"""MySQL database lifecycle commands and operations."""

from __future__ import annotations

from collections.abc import Callable
from unicodedata import category

import click
from sqlalchemy.engine import Connection, Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError
from tabulate import tabulate

from dbtalk.cli_runtime import DbtalkGroup
from dbtalk.database.dsn import ParsedDsn, dsn_from_environment, parse_dsn
from dbtalk.database.models import DatabaseOperationError

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group("schema", cls=DbtalkGroup, context_settings=CONTEXT_SETTINGS)
def schema() -> None:
    """Manage MySQL schemas and databases."""


@schema.command("list", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete MySQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the MySQL DSN.")
def list_command(dsn_value: str | None, dsn_env: str | None) -> None:
    """List visible MySQL databases."""

    try:
        click.echo(
            _render_database_names(list_databases(resolve_management_dsn(dsn_value, dsn_env)))
        )
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error


@schema.command("create", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete MySQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the MySQL DSN.")
@click.option("--name", required=True, help="Database name.")
def create_command(dsn_value: str | None, dsn_env: str | None, name: str) -> None:
    """Create one MySQL database."""

    try:
        create_database(resolve_management_dsn(dsn_value, dsn_env), name)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Database created: {name}")


@schema.command("drop", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete MySQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the MySQL DSN.")
@click.option("--name", required=True, help="Database name.")
@click.option("--yes", is_flag=True, help="Confirm database deletion.")
def drop_command(dsn_value: str | None, dsn_env: str | None, name: str, yes: bool) -> None:
    """Delete one MySQL database."""

    if not yes:
        raise click.UsageError("--yes is required to drop a database")
    try:
        drop_database(resolve_management_dsn(dsn_value, dsn_env), name)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Database dropped: {name}")


def resolve_management_dsn(dsn: str | None, environment_name: str | None) -> ParsedDsn:
    """Resolve one explicit synchronous MySQL management DSN."""

    if (dsn is None) == (environment_name is None):
        raise DatabaseOperationError("provide exactly one of --dsn or --dsn-env")
    parsed = parse_dsn(dsn) if dsn is not None else dsn_from_environment(environment_name)
    if parsed.dialect != "mysql":
        raise DatabaseOperationError("MySQL database management requires a mysql DSN")
    return parsed


def list_databases(parsed: ParsedDsn) -> tuple[str, ...]:
    """Return visible MySQL database names in stable order."""

    _validate_management_dsn(parsed)
    return _run_management_operation(
        parsed,
        lambda connection: tuple(
            sorted(str(row[0]) for row in connection.exec_driver_sql("SHOW DATABASES").fetchall())
        ),
    )


def create_database(parsed: ParsedDsn, name: str) -> None:
    """Create one MySQL database using server defaults."""

    _validate_management_dsn(parsed)
    _validate_database_name(name)
    _run_management_operation(
        parsed,
        lambda connection: connection.exec_driver_sql(
            f"CREATE DATABASE {_quote_database_name(connection, name)}"
        ),
    )


def drop_database(parsed: ParsedDsn, name: str) -> None:
    """Drop one MySQL database after explicit CLI confirmation."""

    _validate_management_dsn(parsed)
    _validate_database_name(name)
    _run_management_operation(parsed, lambda connection: _drop_database(connection, name))


def _drop_database(connection: Connection, name: str) -> None:
    current = _connected_database_name(connection)
    if current is not None and current == name:
        raise DatabaseOperationError(
            "MySQL database deletion cannot target the currently connected database"
        )
    connection.exec_driver_sql(f"DROP DATABASE {_quote_database_name(connection, name)}")


def _connected_database_name(connection: Connection) -> str | None:
    value = connection.exec_driver_sql("SELECT DATABASE()").scalar()
    if value is None:
        return None
    current = str(value).strip()
    return current or None


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
        raise DatabaseOperationError("MySQL database management failed") from error
    finally:
        if engine is not None:
            engine.dispose()


def _validate_management_dsn(parsed: ParsedDsn) -> None:
    if parsed.async_mode or parsed.dialect != "mysql":
        raise DatabaseOperationError("MySQL database management requires a synchronous mysql DSN")


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
