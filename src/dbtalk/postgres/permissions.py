"""PostgreSQL native permission inspection commands."""

from __future__ import annotations

import click
from sqlalchemy import text
from sqlalchemy.engine import Connection
from tabulate import tabulate

from dbtalk.cli_runtime import DbtalkGroup
from dbtalk.database.dsn import ParsedDsn
from dbtalk.database.models import DatabaseOperationError

from .role import (
    _run_management_operation,
    _validate_identifier,
    _validate_management_dsn,
    resolve_management_dsn,
)

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group("permissions", cls=DbtalkGroup, context_settings=CONTEXT_SETTINGS)
def permissions() -> None:
    """Inspect native PostgreSQL grants."""


@permissions.command("list", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option("--role", "role_name", help="Optional PostgreSQL role filter.")
@click.option("--database", "database_name", help="Optional database filter.")
@click.option("--schema", "schema_name", help="Optional schema filter.")
def list_command(
    dsn_value: str | None,
    dsn_env: str | None,
    role_name: str | None,
    database_name: str | None,
    schema_name: str | None,
) -> None:
    """List native grants visible to the management DSN."""

    try:
        parsed = resolve_management_dsn(dsn_value, dsn_env)
        click.echo(_render(list_permissions(parsed, role_name, database_name, schema_name)))
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error


@permissions.command("show", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option("--role", "role_name", required=True, help="PostgreSQL role name.")
@click.option("--database", "database_name", help="Optional database filter.")
@click.option("--schema", "schema_name", help="Optional schema filter.")
def show_command(
    dsn_value: str | None,
    dsn_env: str | None,
    role_name: str,
    database_name: str | None,
    schema_name: str | None,
) -> None:
    """Show native grants for one PostgreSQL role."""

    try:
        parsed = resolve_management_dsn(dsn_value, dsn_env)
        click.echo(_render(show_permissions(parsed, role_name, database_name, schema_name)))
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error


def list_permissions(
    parsed: ParsedDsn,
    role_name: str | None = None,
    database_name: str | None = None,
    schema_name: str | None = None,
) -> tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]:
    """Return native database, schema, object, and CREATEDB permission rows."""

    if role_name is not None:
        _validate_identifier(role_name, "PostgreSQL role name")
    if database_name is not None:
        _validate_identifier(database_name, "PostgreSQL database name")
    if schema_name is not None:
        _validate_identifier(schema_name, "PostgreSQL schema name")
    _validate_management_dsn(parsed)

    def operation(connection: Connection) -> tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]:
        clauses: list[str] = []
        params: dict[str, object] = {}
        if role_name is not None:
            clauses.append("grantee = :role")
            params["role"] = role_name
        if database_name is not None:
            clauses.append("database_name = :database")
            params["database"] = database_name
        if schema_name is not None:
            clauses.append("schema_name = :schema")
            params["schema"] = schema_name
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        result = connection.execute(
            text(
                "SELECT object_type, database_name, schema_name, object_name, grantee, "
                "privilege_type, is_grantable "
                "FROM ("
                "SELECT 'database' AS object_type, database_entry.datname AS database_name, "
                "NULL AS schema_name, NULL AS object_name, "
                "COALESCE(grantee_role.rolname, 'PUBLIC') AS grantee, "
                "acl.privilege_type, acl.is_grantable "
                "FROM pg_catalog.pg_database AS database_entry "
                "CROSS JOIN LATERAL pg_catalog.aclexplode("
                "COALESCE(database_entry.datacl, "
                "pg_catalog.acldefault('d'::\"char\", database_entry.datdba))"
                ") AS acl "
                "LEFT JOIN pg_catalog.pg_roles AS grantee_role ON grantee_role.oid = acl.grantee "
                "UNION ALL "
                "SELECT 'schema' AS object_type, current_database() AS database_name, "
                "namespace.nspname AS schema_name, NULL AS object_name, "
                "COALESCE(grantee_role.rolname, 'PUBLIC') AS grantee, "
                "acl.privilege_type, acl.is_grantable "
                "FROM pg_catalog.pg_namespace AS namespace "
                "CROSS JOIN LATERAL pg_catalog.aclexplode("
                "COALESCE(namespace.nspacl, "
                "pg_catalog.acldefault('n'::\"char\", namespace.nspowner))"
                ") AS acl "
                "LEFT JOIN pg_catalog.pg_roles AS grantee_role ON grantee_role.oid = acl.grantee "
                "UNION ALL "
                "SELECT 'object' AS object_type, current_database() AS database_name, "
                "namespace.nspname AS schema_name, class_entry.relname AS object_name, "
                "COALESCE(grantee_role.rolname, 'PUBLIC') AS grantee, "
                "acl.privilege_type, acl.is_grantable "
                "FROM pg_catalog.pg_class AS class_entry "
                "JOIN pg_catalog.pg_namespace AS namespace "
                "ON namespace.oid = class_entry.relnamespace "
                "CROSS JOIN LATERAL pg_catalog.aclexplode("
                "COALESCE(class_entry.relacl, pg_catalog.acldefault("
                "(CASE WHEN class_entry.relkind = 'S' THEN 's' ELSE 'r' END)::\"char\", "
                "class_entry.relowner))"
                ") AS acl "
                "LEFT JOIN pg_catalog.pg_roles AS grantee_role ON grantee_role.oid = acl.grantee "
                "WHERE class_entry.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') "
                "UNION ALL "
                "SELECT 'role' AS object_type, NULL AS database_name, NULL AS schema_name, "
                "NULL AS object_name, role_entry.rolname AS grantee, 'CREATEDB' AS privilege_type, "
                "FALSE AS is_grantable "
                "FROM pg_catalog.pg_roles AS role_entry WHERE role_entry.rolcreatedb"
                f") AS native_permissions{where} "
                "ORDER BY object_type, database_name, schema_name, object_name, "
                "grantee, privilege_type"
            ),
            params,
        )
        return tuple(result.keys()), tuple(tuple(row) for row in result.fetchall())

    return _run_management_operation(parsed, operation)


def _render(result: tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]) -> str:
    columns, rows = result
    rendered = tabulate(rows, headers=columns, tablefmt="psql")
    return f"{rendered}\n(0 rows)" if not rows else rendered


__all__ = ["list_permissions", "permissions", "show_permissions"]


def show_permissions(
    parsed: ParsedDsn,
    role_name: str,
    database_name: str | None = None,
    schema_name: str | None = None,
) -> tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]:
    """Return native permission rows for one PostgreSQL role."""

    return list_permissions(parsed, role_name, database_name, schema_name)
