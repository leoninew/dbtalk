"""MySQL native permission inspection commands."""

from __future__ import annotations

import click
from sqlalchemy import text
from sqlalchemy.engine import Connection
from tabulate import tabulate

from dbtalk.cli_runtime import DbtalkGroup
from dbtalk.database.dsn import ParsedDsn
from dbtalk.database.models import DatabaseOperationError

from .user import (
    _run_management_operation,
    _validate_account,
    _validate_management_dsn,
    _validate_resource_name,
    resolve_management_dsn,
)

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group("permissions", cls=DbtalkGroup, context_settings=CONTEXT_SETTINGS)
def permissions() -> None:
    """Inspect native MySQL grants."""


@permissions.command("list", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete MySQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the MySQL DSN.")
@click.option("--user", "user_name", help="Optional MySQL account name filter.")
@click.option("--host", help="Exact MySQL account host filter.")
@click.option("--database", "database_name", help="Optional database filter.")
def list_command(
    dsn_value: str | None,
    dsn_env: str | None,
    user_name: str | None,
    host: str | None,
    database_name: str | None,
) -> None:
    """List native grants visible to the management DSN."""

    try:
        parsed = resolve_management_dsn(dsn_value, dsn_env)
        click.echo(_render(list_permissions(parsed, user_name, host, database_name)))
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error


@permissions.command("show", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete MySQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the MySQL DSN.")
@click.option("--user", "user_name", required=True, help="MySQL account name.")
@click.option("--host", required=True, help="Exact MySQL account host.")
@click.option("--database", "database_name", help="Optional database filter.")
def show_command(
    dsn_value: str | None,
    dsn_env: str | None,
    user_name: str,
    host: str,
    database_name: str | None,
) -> None:
    """Show native grants for one MySQL account."""

    try:
        parsed = resolve_management_dsn(dsn_value, dsn_env)
        click.echo(_render(show_permissions(parsed, user_name, host, database_name)))
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error


def list_permissions(
    parsed: ParsedDsn,
    user_name: str | None = None,
    host: str | None = None,
    database_name: str | None = None,
) -> tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]:
    """Return native global and schema privilege rows, optionally filtered."""

    if user_name is not None and host is None:
        raise DatabaseOperationError("--host is required with --user")
    if host is not None and user_name is None:
        raise DatabaseOperationError("--user is required with --host")
    if user_name is not None and host is not None:
        _validate_account(user_name, host)
    if database_name is not None:
        _validate_resource_name(database_name, "database name")
    _validate_management_dsn(parsed)

    def operation(connection: Connection) -> tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]:
        clauses: list[str] = []
        params: dict[str, object] = {}
        if user_name is not None:
            clauses.append("grantee = :grantee")
            params["grantee"] = f"'{user_name}'@'{host}'"
        if database_name is not None:
            clauses.append("database_name = :database")
            params["database"] = database_name
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        result = connection.execute(
            text(
                "SELECT scope, grantee, database_name, privilege_type, is_grantable "
                "FROM ("
                "SELECT 'global' AS scope, GRANTEE AS grantee, NULL AS database_name, "
                "PRIVILEGE_TYPE AS privilege_type, IS_GRANTABLE AS is_grantable "
                "FROM information_schema.USER_PRIVILEGES "
                "UNION ALL "
                "SELECT 'database' AS scope, GRANTEE AS grantee, TABLE_SCHEMA AS database_name, "
                "PRIVILEGE_TYPE AS privilege_type, IS_GRANTABLE AS is_grantable "
                "FROM information_schema.SCHEMA_PRIVILEGES"
                f") AS native_permissions{where} "
                "ORDER BY scope, grantee, database_name, privilege_type"
            ),
            params,
        )
        return tuple(result.keys()), tuple(tuple(row) for row in result.fetchall())

    return _run_management_operation(parsed, operation)


def show_permissions(
    parsed: ParsedDsn,
    user_name: str,
    host: str,
    database_name: str | None = None,
) -> tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]:
    """Return SHOW GRANTS output for one account."""

    _validate_account(user_name, host)
    if database_name is not None:
        return list_permissions(parsed, user_name, host, database_name)
    _validate_management_dsn(parsed)

    def operation(connection: Connection) -> tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]:
        # Bind account components separately so PyMySQL does not treat a literal `%` host
        # as a Python formatting marker while still keeping the account structured.
        result = connection.execute(
            text("SHOW GRANTS FOR :user@:host"),
            {"user": user_name, "host": host},
        )
        return tuple(result.keys()), tuple(tuple(row) for row in result.fetchall())

    return _run_management_operation(parsed, operation)


def _render(result: tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]) -> str:
    columns, rows = result
    rendered = tabulate(rows, headers=columns, tablefmt="psql")
    return f"{rendered}\n(0 rows)" if not rows else rendered


__all__ = ["list_permissions", "permissions", "show_permissions"]
