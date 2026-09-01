"""PostgreSQL role lifecycle and profile/privilege authorization commands."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from unicodedata import category

import click
from psycopg import Error as PsycopgError
from psycopg import sql
from sqlalchemy.engine import Connection, Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError
from tabulate import tabulate

from dbtalk.database.dsn import ParsedDsn, dsn_from_environment, parse_dsn
from dbtalk.database.models import DatabaseOperationError

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}
Profile = Literal["readonly", "readwrite", "migrator"]
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
_ENVIRONMENT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class PostgreSQLRoleRecord:
    """Non-sensitive PostgreSQL role fields suitable for listing."""

    name: str
    login: bool
    createdb: bool
    createrole: bool


@click.group("role", context_settings=CONTEXT_SETTINGS)
def role() -> None:
    """Manage PostgreSQL roles."""


@role.command("list", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
def list_command(dsn_value: str | None, dsn_env: str | None) -> None:
    """List non-sensitive PostgreSQL role fields."""

    try:
        click.echo(_render_roles(list_roles(resolve_management_dsn(dsn_value, dsn_env))))
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error


@role.command("create", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option("--role", "role_name", required=True, help="PostgreSQL role name.")
@click.option("--password-env", required=True, help="Environment variable containing the password.")
def create_command(
    dsn_value: str | None,
    dsn_env: str | None,
    role_name: str,
    password_env: str,
) -> None:
    """Create one minimally privileged login role."""

    try:
        create_role(resolve_management_dsn(dsn_value, dsn_env), role_name, password_env)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"PostgreSQL role created: {role_name}")


@role.command("enable", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option("--role", "role_name", required=True, help="PostgreSQL role name.")
@click.option("--yes", is_flag=True, help="Confirm enabling the role.")
def enable_command(
    dsn_value: str | None,
    dsn_env: str | None,
    role_name: str,
    yes: bool,
) -> None:
    """Enable one PostgreSQL login role."""

    _require_yes(yes, "enable a PostgreSQL role")
    try:
        enable_role(resolve_management_dsn(dsn_value, dsn_env), role_name)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"PostgreSQL role enabled: {role_name}")


@role.command("disable", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option("--role", "role_name", required=True, help="PostgreSQL role name.")
@click.option("--yes", is_flag=True, help="Confirm disabling the role.")
def disable_command(
    dsn_value: str | None,
    dsn_env: str | None,
    role_name: str,
    yes: bool,
) -> None:
    """Disable one PostgreSQL login role."""

    _require_yes(yes, "disable a PostgreSQL role")
    try:
        disable_role(resolve_management_dsn(dsn_value, dsn_env), role_name)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"PostgreSQL role disabled: {role_name}")


@role.command("rotate-password", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option("--role", "role_name", required=True, help="PostgreSQL role name.")
@click.option(
    "--password-env", required=True, help="Environment variable containing the new password."
)
@click.option("--yes", is_flag=True, help="Confirm rotating the password.")
def rotate_password_command(
    dsn_value: str | None,
    dsn_env: str | None,
    role_name: str,
    password_env: str,
    yes: bool,
) -> None:
    """Rotate one PostgreSQL role password."""

    _require_yes(yes, "rotate a PostgreSQL role password")
    try:
        rotate_role_password(resolve_management_dsn(dsn_value, dsn_env), role_name, password_env)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"PostgreSQL role password rotated: {role_name}")


@role.command("drop", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option("--role", "role_name", required=True, help="PostgreSQL role name.")
@click.option("--yes", is_flag=True, help="Confirm deleting the role.")
def drop_command(
    dsn_value: str | None,
    dsn_env: str | None,
    role_name: str,
    yes: bool,
) -> None:
    """Delete one PostgreSQL role."""

    _require_yes(yes, "drop a PostgreSQL role")
    try:
        drop_role(resolve_management_dsn(dsn_value, dsn_env), role_name)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"PostgreSQL role dropped: {role_name}")


@click.command("grant", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option("--role", "role_name", required=True, help="PostgreSQL role name.")
@click.option(
    "--database",
    "database_name",
    help="PostgreSQL database authorization target. Defaults to the DSN database.",
)
@click.option(
    "--schema",
    "schema_name",
    help="PostgreSQL schema authorization target. Defaults to the DSN database.",
)
@click.option(
    "--profile",
    type=click.Choice(("readonly", "readwrite", "migrator"), case_sensitive=True),
    help="Fixed authorization profile. Mutually exclusive with --privilege.",
)
@click.option(
    "--privilege",
    "privileges",
    multiple=True,
    help=(
        "One native privilege name. Repeat for multiple privileges; mutually exclusive "
        "with --profile."
    ),
)
@click.option("--yes", is_flag=True, help="Confirm granting privileges.")
def grant_command(
    dsn_value: str | None,
    dsn_env: str | None,
    role_name: str,
    database_name: str | None,
    schema_name: str | None,
    profile: str | None,
    privileges: tuple[str, ...],
    yes: bool,
) -> None:
    """Grant one PostgreSQL profile or native privilege set."""

    _require_yes(yes, "grant PostgreSQL privileges")
    try:
        parsed = resolve_management_dsn(dsn_value, dsn_env)
        resource = _resource(database_name, schema_name, parsed)
        if profile is not None and privileges:
            raise DatabaseOperationError("--profile and --privilege are mutually exclusive")
        if profile is None and not privileges:
            raise DatabaseOperationError("provide --profile or at least one --privilege")
        if profile is not None:
            grant_profile(parsed, role_name, resource, _profile(profile))
        else:
            grant_privileges(parsed, role_name, resource, privileges)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        f"PostgreSQL {'profile ' + profile if profile else 'privileges ' + ', '.join(privileges)} "
        f"granted on {resource[0]} {resource[1]} to {role_name}"
    )


@click.command("revoke", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option("--role", "role_name", required=True, help="PostgreSQL role name.")
@click.option(
    "--database",
    "database_name",
    help="PostgreSQL database authorization target. Defaults to the DSN database.",
)
@click.option(
    "--schema",
    "schema_name",
    help="PostgreSQL schema authorization target. Defaults to the DSN database.",
)
@click.option(
    "--profile",
    type=click.Choice(("readonly", "readwrite", "migrator"), case_sensitive=True),
    help="Fixed authorization profile. Mutually exclusive with --privilege.",
)
@click.option(
    "--privilege",
    "privileges",
    multiple=True,
    help=(
        "One native privilege name. Repeat for multiple privileges; mutually exclusive "
        "with --profile."
    ),
)
@click.option("--yes", is_flag=True, help="Confirm revoking privileges.")
def revoke_command(
    dsn_value: str | None,
    dsn_env: str | None,
    role_name: str,
    database_name: str | None,
    schema_name: str | None,
    profile: str | None,
    privileges: tuple[str, ...],
    yes: bool,
) -> None:
    """Revoke one PostgreSQL profile or native privilege set."""

    _require_yes(yes, "revoke PostgreSQL privileges")
    try:
        parsed = resolve_management_dsn(dsn_value, dsn_env)
        resource = _resource(database_name, schema_name, parsed)
        if profile is not None and privileges:
            raise DatabaseOperationError("--profile and --privilege are mutually exclusive")
        if profile is None and not privileges:
            raise DatabaseOperationError("provide --profile or at least one --privilege")
        if profile is not None:
            revoke_profile(parsed, role_name, resource, _profile(profile))
        else:
            revoke_privileges(parsed, role_name, resource, privileges)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        f"PostgreSQL {'profile ' + profile if profile else 'privileges ' + ', '.join(privileges)} "
        f"revoked on {resource[0]} {resource[1]} from {role_name}"
    )


def resolve_management_dsn(dsn: str | None, environment_name: str | None) -> ParsedDsn:
    """Resolve one explicit synchronous PostgreSQL management DSN."""

    if (dsn is None) == (environment_name is None):
        raise DatabaseOperationError("provide exactly one of --dsn or --dsn-env")
    parsed = parse_dsn(dsn) if dsn is not None else dsn_from_environment(environment_name)
    if parsed.dialect != "postgresql":
        raise DatabaseOperationError("PostgreSQL role management requires a postgresql DSN")
    return parsed


def list_roles(parsed: ParsedDsn) -> tuple[PostgreSQLRoleRecord, ...]:
    """Return non-sensitive PostgreSQL role metadata in stable order."""

    _validate_management_dsn(parsed)

    def operation(connection: Connection) -> tuple[PostgreSQLRoleRecord, ...]:
        rows = connection.exec_driver_sql(
            "SELECT rolname, rolcanlogin, rolcreatedb, rolcreaterole FROM pg_roles ORDER BY rolname"
        ).fetchall()
        return tuple(
            PostgreSQLRoleRecord(str(row[0]), bool(row[1]), bool(row[2]), bool(row[3]))
            for row in rows
        )

    return _run_management_operation(parsed, operation)


def create_role(parsed: ParsedDsn, role_name: str, password_env: str) -> None:
    """Create one minimally privileged PostgreSQL login role."""

    _validate_management_dsn(parsed)
    _validate_identifier(role_name, "PostgreSQL role name")
    password = _password_from_environment(password_env)

    def operation(connection: Connection) -> None:
        _execute_password_ddl(
            connection,
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Identifier(role_name), sql.Literal(password)),
        )

    _run_management_operation(parsed, operation)


def enable_role(parsed: ParsedDsn, role_name: str) -> None:
    """Enable one PostgreSQL login role after protecting the current administrator."""

    _alter_role(parsed, role_name, "LOGIN")


def disable_role(parsed: ParsedDsn, role_name: str) -> None:
    """Disable one PostgreSQL login role after protecting the current administrator."""

    _alter_role(parsed, role_name, "NOLOGIN")


def rotate_role_password(parsed: ParsedDsn, role_name: str, password_env: str) -> None:
    """Set one PostgreSQL role password from an environment variable."""

    _validate_management_dsn(parsed)
    _validate_identifier(role_name, "PostgreSQL role name")
    password = _password_from_environment(password_env)

    def operation(connection: Connection) -> None:
        _reject_current_role(connection, role_name)
        _execute_password_ddl(
            connection,
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(role_name), sql.Literal(password)
            ),
        )

    _run_management_operation(parsed, operation)


def drop_role(parsed: ParsedDsn, role_name: str) -> None:
    """Drop one PostgreSQL role after protecting the current administrator."""

    _validate_management_dsn(parsed)
    _validate_identifier(role_name, "PostgreSQL role name")

    def operation(connection: Connection) -> None:
        _reject_current_role(connection, role_name)
        connection.exec_driver_sql(f"DROP ROLE {_quote_identifier(connection, role_name)}")

    _run_management_operation(parsed, operation)


def grant_profile(
    parsed: ParsedDsn, role_name: str, resource: tuple[str, str], profile: Profile
) -> None:
    """Grant one fixed PostgreSQL profile to a role."""

    _change_profile(parsed, role_name, resource, profile, action="GRANT")


def revoke_profile(
    parsed: ParsedDsn, role_name: str, resource: tuple[str, str], profile: Profile
) -> None:
    """Revoke one fixed PostgreSQL profile from a role."""

    _change_profile(parsed, role_name, resource, profile, action="REVOKE")


def grant_privileges(
    parsed: ParsedDsn,
    role_name: str,
    resource: tuple[str, str],
    privileges: tuple[str, ...],
) -> None:
    _change_privileges(parsed, role_name, resource, privileges, action="GRANT")


def revoke_privileges(
    parsed: ParsedDsn,
    role_name: str,
    resource: tuple[str, str],
    privileges: tuple[str, ...],
) -> None:
    _change_privileges(parsed, role_name, resource, privileges, action="REVOKE")


def _alter_role(parsed: ParsedDsn, role_name: str, action: Literal["LOGIN", "NOLOGIN"]) -> None:
    _validate_management_dsn(parsed)
    _validate_identifier(role_name, "PostgreSQL role name")

    def operation(connection: Connection) -> None:
        _reject_current_role(connection, role_name)
        connection.exec_driver_sql(
            f"ALTER ROLE {_quote_identifier(connection, role_name)} {action}"
        )

    _run_management_operation(parsed, operation)


def _change_profile(
    parsed: ParsedDsn,
    role_name: str,
    resource: tuple[str, str],
    profile: Profile,
    *,
    action: Literal["GRANT", "REVOKE"],
) -> None:
    _validate_management_dsn(parsed)
    _validate_identifier(role_name, "PostgreSQL role name")
    profile = _profile(profile)
    resource_type, resource_name = resource
    _validate_identifier(resource_name, f"PostgreSQL {resource_type} name")

    def operation(connection: Connection) -> None:
        _reject_current_role(connection, role_name)
        quoted_role = _quote_identifier(connection, role_name)
        quoted_resource = _quote_identifier(connection, resource_name)
        for statement in _profile_statements(
            resource_type, quoted_resource, quoted_role, profile, action
        ):
            connection.exec_driver_sql(statement)
        if profile == "migrator":
            connection.exec_driver_sql(
                f"ALTER ROLE {quoted_role} {'CREATEDB' if action == 'GRANT' else 'NOCREATEDB'}"
            )

    _run_management_operation(parsed, operation)


def _change_privileges(
    parsed: ParsedDsn,
    role_name: str,
    resource: tuple[str, str],
    privileges: tuple[str, ...],
    *,
    action: Literal["GRANT", "REVOKE"],
) -> None:
    _validate_management_dsn(parsed)
    _validate_identifier(role_name, "PostgreSQL role name")
    if not privileges:
        raise DatabaseOperationError("at least one privilege is required")
    resource_type, resource_name = resource
    _validate_identifier(resource_name, f"PostgreSQL {resource_type} name")
    normalized = tuple(_normalize_privilege(value) for value in privileges)

    def operation(connection: Connection) -> None:
        _reject_current_role(connection, role_name)
        quoted_role = _quote_identifier(connection, role_name)
        quoted_resource = _quote_identifier(connection, resource_name)
        direction = f"TO {quoted_role}" if action == "GRANT" else f"FROM {quoted_role}"
        object_type = "DATABASE" if resource_type == "database" else "SCHEMA"
        connection.exec_driver_sql(
            f"{action} {', '.join(normalized)} ON {object_type} {quoted_resource} {direction}"
        )

    _run_management_operation(parsed, operation)


def _profile_statements(
    resource_type: str,
    quoted_resource: str,
    quoted_role: str,
    profile: Profile,
    action: Literal["GRANT", "REVOKE"],
) -> tuple[str, ...]:
    direction = f"TO {quoted_role}" if action == "GRANT" else f"FROM {quoted_role}"
    if resource_type == "database":
        privileges = {
            "readonly": "CONNECT",
            "readwrite": "CONNECT",
            "migrator": "CONNECT, CREATE",
        }[profile]
        return (f"{action} {privileges} ON DATABASE {quoted_resource} {direction}",)
    if resource_type != "schema":
        raise DatabaseOperationError("PostgreSQL authorization resource is invalid")
    statements = [f"{action} USAGE ON SCHEMA {quoted_resource} {direction}"]
    if profile == "migrator":
        statements.append(f"{action} CREATE ON SCHEMA {quoted_resource} {direction}")
    if profile == "readonly":
        statements.append(f"{action} SELECT ON ALL TABLES IN SCHEMA {quoted_resource} {direction}")
    else:
        statements.append(
            f"{action} SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
            f"IN SCHEMA {quoted_resource} {direction}"
        )
        statements.append(
            f"{action} USAGE, SELECT, UPDATE ON ALL SEQUENCES "
            f"IN SCHEMA {quoted_resource} {direction}"
        )
    return tuple(statements)


def _run_management_operation[OperationResult](
    parsed: ParsedDsn,
    operation: Callable[[Connection], OperationResult],
) -> OperationResult:
    engine: Engine | None = None
    try:
        engine = create_engine(parsed.url)
        with engine.connect() as connection:
            return operation(connection.execution_options(isolation_level="AUTOCOMMIT"))
    except DatabaseOperationError:
        raise
    except (PsycopgError, SQLAlchemyError) as error:
        raise DatabaseOperationError("PostgreSQL role management failed") from error
    finally:
        if engine is not None:
            engine.dispose()


def _validate_management_dsn(parsed: ParsedDsn) -> None:
    if parsed.async_mode or parsed.dialect != "postgresql":
        raise DatabaseOperationError(
            "PostgreSQL role management requires a synchronous postgresql DSN"
        )


def _validate_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise DatabaseOperationError(f"{label} is invalid")
    if any(category(character).startswith("C") for character in value):
        raise DatabaseOperationError(f"{label} is invalid")


def _password_from_environment(environment_name: str) -> str:
    if not isinstance(environment_name, str) or not _ENVIRONMENT_PATTERN.fullmatch(
        environment_name
    ):
        raise DatabaseOperationError("password environment variable name is invalid")
    password = os.environ.get(environment_name)
    if not password:
        raise DatabaseOperationError("password environment variable is not set or is empty")
    return password


def _resource(
    database_name: str | None,
    schema_name: str | None,
    parsed: ParsedDsn,
) -> tuple[str, str]:
    if database_name is not None and schema_name is not None:
        raise DatabaseOperationError("provide at most one of --database or --schema")
    if database_name is None and schema_name is None:
        if parsed.database is None:
            raise DatabaseOperationError("database or schema resource is required")
        return "database", parsed.database
    return (
        ("database", database_name) if database_name is not None else ("schema", schema_name or "")
    )


def _reject_current_role(connection: Connection, role_name: str) -> None:
    current_role = str(connection.exec_driver_sql("SELECT CURRENT_USER").scalar_one())
    if current_role == role_name:
        raise DatabaseOperationError("cannot change the current PostgreSQL management role")


def _profile(profile: str) -> Profile:
    if profile in {"readonly", "readwrite", "migrator"}:
        return profile  # type: ignore[return-value]
    raise DatabaseOperationError("authorization profile is invalid")


def _normalize_privilege(value: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 _]*", value)
    ):
        raise DatabaseOperationError("privilege name is invalid")
    return value.upper()


def _quote_identifier(connection: Connection, value: str) -> str:
    return connection.dialect.identifier_preparer.quote(value)


def _execute_password_ddl(connection: Connection, statement: sql.Composable) -> None:
    """Run password DDL through psycopg's safe SQL composition API."""

    driver_connection = connection.connection.driver_connection
    if driver_connection is None:
        raise DatabaseOperationError("PostgreSQL role management failed")
    driver_connection.execute(statement)


def _require_yes(yes: bool, action: str) -> None:
    if not yes:
        raise click.UsageError(f"--yes is required to {action}")


def _render_roles(roles: tuple[PostgreSQLRoleRecord, ...]) -> str:
    rows = (
        (
            record.name,
            "yes" if record.login else "no",
            "yes" if record.createdb else "no",
            "yes" if record.createrole else "no",
        )
        for record in roles
    )
    rendered = tabulate(rows, headers=("role", "login", "createdb", "createrole"), tablefmt="psql")
    return f"{rendered}\n(0 rows)" if not roles else rendered


__all__ = [
    "PostgreSQLRoleRecord",
    "create_role",
    "disable_role",
    "drop_role",
    "enable_role",
    "grant_command",
    "grant_privileges",
    "grant_profile",
    "list_roles",
    "resolve_management_dsn",
    "revoke_command",
    "revoke_privileges",
    "revoke_profile",
    "role",
    "rotate_role_password",
]
