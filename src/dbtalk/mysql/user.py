"""MySQL account lifecycle and profile/privilege authorization commands."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Literal
from unicodedata import category

import click
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError
from tabulate import tabulate

from dbtalk.database.dsn import ParsedDsn, dsn_from_environment, parse_dsn
from dbtalk.database.models import DatabaseOperationError

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}
Profile = Literal["readonly", "readwrite", "migrator"]
_USER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.$-]{0,31}$")
_HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$"
)
_ENVIRONMENT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class MysqlUserRecord:
    """Non-sensitive MySQL account fields suitable for listing."""

    user: str
    host: str
    locked: bool


@click.group("user", context_settings=CONTEXT_SETTINGS)
def user() -> None:
    """Manage MySQL accounts."""


@user.command("list", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete MySQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the MySQL DSN.")
def list_command(dsn_value: str | None, dsn_env: str | None) -> None:
    """List non-sensitive MySQL account fields."""

    try:
        click.echo(_render_users(list_users(resolve_management_dsn(dsn_value, dsn_env))))
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error


@user.command("create", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete MySQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the MySQL DSN.")
@click.option("--user", "user_name", required=True, help="MySQL account name.")
@click.option("--host", required=True, help="Exact MySQL account host.")
@click.option("--password-env", required=True, help="Environment variable containing the password.")
def create_command(
    dsn_value: str | None,
    dsn_env: str | None,
    user_name: str,
    host: str,
    password_env: str,
) -> None:
    """Create one minimally privileged MySQL account."""

    try:
        create_user(resolve_management_dsn(dsn_value, dsn_env), user_name, host, password_env)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"MySQL user created: {_display_account(user_name, host)}")


@user.command("enable", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete MySQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the MySQL DSN.")
@click.option("--user", "user_name", required=True, help="MySQL account name.")
@click.option("--host", required=True, help="Exact MySQL account host.")
@click.option("--yes", is_flag=True, help="Confirm enabling the account.")
def enable_command(
    dsn_value: str | None,
    dsn_env: str | None,
    user_name: str,
    host: str,
    yes: bool,
) -> None:
    """Enable one MySQL account."""

    _require_yes(yes, "enable a MySQL user")
    try:
        enable_user(resolve_management_dsn(dsn_value, dsn_env), user_name, host)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"MySQL user enabled: {_display_account(user_name, host)}")


@user.command("disable", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete MySQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the MySQL DSN.")
@click.option("--user", "user_name", required=True, help="MySQL account name.")
@click.option("--host", required=True, help="Exact MySQL account host.")
@click.option("--yes", is_flag=True, help="Confirm disabling the account.")
def disable_command(
    dsn_value: str | None,
    dsn_env: str | None,
    user_name: str,
    host: str,
    yes: bool,
) -> None:
    """Disable one MySQL account."""

    _require_yes(yes, "disable a MySQL user")
    try:
        disable_user(resolve_management_dsn(dsn_value, dsn_env), user_name, host)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"MySQL user disabled: {_display_account(user_name, host)}")


@user.command("rotate-password", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete MySQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the MySQL DSN.")
@click.option("--user", "user_name", required=True, help="MySQL account name.")
@click.option("--host", required=True, help="Exact MySQL account host.")
@click.option(
    "--password-env", required=True, help="Environment variable containing the new password."
)
@click.option("--yes", is_flag=True, help="Confirm rotating the password.")
def rotate_password_command(
    dsn_value: str | None,
    dsn_env: str | None,
    user_name: str,
    host: str,
    password_env: str,
    yes: bool,
) -> None:
    """Rotate one MySQL account password."""

    _require_yes(yes, "rotate a MySQL user password")
    try:
        rotate_user_password(
            resolve_management_dsn(dsn_value, dsn_env), user_name, host, password_env
        )
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"MySQL user password rotated: {_display_account(user_name, host)}")


@user.command("drop", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete MySQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the MySQL DSN.")
@click.option("--user", "user_name", required=True, help="MySQL account name.")
@click.option("--host", required=True, help="Exact MySQL account host.")
@click.option("--yes", is_flag=True, help="Confirm deleting the account.")
def drop_command(
    dsn_value: str | None,
    dsn_env: str | None,
    user_name: str,
    host: str,
    yes: bool,
) -> None:
    """Delete one MySQL account."""

    _require_yes(yes, "drop a MySQL user")
    try:
        drop_user(resolve_management_dsn(dsn_value, dsn_env), user_name, host)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"MySQL user dropped: {_display_account(user_name, host)}")


@click.command("grant", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete MySQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the MySQL DSN.")
@click.option("--user", "user_name", required=True, help="MySQL account name.")
@click.option("--host", required=True, help="Exact MySQL account host.")
@click.option(
    "--database",
    "database_name",
    help="Database authorization target. Defaults to the DSN database.",
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
    user_name: str,
    host: str,
    database_name: str | None,
    profile: str | None,
    privileges: tuple[str, ...],
    yes: bool,
) -> None:
    """Grant one MySQL profile or native privilege set."""

    _require_yes(yes, "grant MySQL privileges")
    try:
        parsed = resolve_management_dsn(dsn_value, dsn_env)
        if profile is not None and privileges:
            raise DatabaseOperationError("--profile and --privilege are mutually exclusive")
        if profile is None and not privileges:
            raise DatabaseOperationError("provide --profile or at least one --privilege")
        if profile is not None:
            grant_profile(parsed, user_name, host, database_name, _profile(profile))
        else:
            grant_privileges(parsed, user_name, host, database_name, privileges)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    account = _display_account(user_name, host)
    target = database_name or parsed.database or "DSN database"
    detail = f"profile {profile}" if profile is not None else f"privileges {', '.join(privileges)}"
    click.echo(f"MySQL {detail} granted on {target} to {account}")


@click.command("revoke", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete MySQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the MySQL DSN.")
@click.option("--user", "user_name", required=True, help="MySQL account name.")
@click.option("--host", required=True, help="Exact MySQL account host.")
@click.option(
    "--database",
    "database_name",
    help="Database authorization target. Defaults to the DSN database.",
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
    user_name: str,
    host: str,
    database_name: str | None,
    profile: str | None,
    privileges: tuple[str, ...],
    yes: bool,
) -> None:
    """Revoke one MySQL profile or native privilege set."""

    _require_yes(yes, "revoke MySQL privileges")
    try:
        parsed = resolve_management_dsn(dsn_value, dsn_env)
        if profile is not None and privileges:
            raise DatabaseOperationError("--profile and --privilege are mutually exclusive")
        if profile is None and not privileges:
            raise DatabaseOperationError("provide --profile or at least one --privilege")
        if profile is not None:
            revoke_profile(parsed, user_name, host, database_name, _profile(profile))
        else:
            revoke_privileges(parsed, user_name, host, database_name, privileges)
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    account = _display_account(user_name, host)
    target = database_name or parsed.database or "DSN database"
    detail = f"profile {profile}" if profile is not None else f"privileges {', '.join(privileges)}"
    click.echo(f"MySQL {detail} revoked on {target} from {account}")


def resolve_management_dsn(dsn: str | None, environment_name: str | None) -> ParsedDsn:
    """Resolve one explicit synchronous MySQL management DSN."""

    if (dsn is None) == (environment_name is None):
        raise DatabaseOperationError("provide exactly one of --dsn or --dsn-env")
    parsed = parse_dsn(dsn) if dsn is not None else dsn_from_environment(environment_name)
    if parsed.dialect != "mysql":
        raise DatabaseOperationError("MySQL user management requires a mysql DSN")
    return parsed


def list_users(parsed: ParsedDsn) -> tuple[MysqlUserRecord, ...]:
    """Return non-sensitive MySQL account metadata in stable order."""

    _validate_management_dsn(parsed)

    def operation(connection: Connection) -> tuple[MysqlUserRecord, ...]:
        rows = connection.exec_driver_sql(
            "SELECT User, Host, account_locked FROM mysql.user ORDER BY User, Host"
        ).fetchall()
        return tuple(
            MysqlUserRecord(str(row[0]), str(row[1]), str(row[2]).upper() == "Y") for row in rows
        )

    return _run_management_operation(parsed, operation)


def create_user(parsed: ParsedDsn, user_name: str, host: str, password_env: str) -> None:
    """Create one minimally privileged MySQL account."""

    _validate_management_dsn(parsed)
    _validate_account(user_name, host)
    password = _password_from_environment(password_env)
    _run_management_operation(
        parsed,
        lambda connection: connection.execute(
            text("CREATE USER :user@:host IDENTIFIED BY :password"),
            {"user": user_name, "host": host, "password": password},
        ),
    )


def enable_user(parsed: ParsedDsn, user_name: str, host: str) -> None:
    """Unlock one MySQL account after protecting the current administrator."""

    _alter_user(parsed, user_name, host, "ACCOUNT UNLOCK")


def disable_user(parsed: ParsedDsn, user_name: str, host: str) -> None:
    """Lock one MySQL account after protecting the current administrator."""

    _alter_user(parsed, user_name, host, "ACCOUNT LOCK")


def rotate_user_password(parsed: ParsedDsn, user_name: str, host: str, password_env: str) -> None:
    """Set one MySQL account password from an environment variable."""

    _validate_management_dsn(parsed)
    _validate_account(user_name, host)
    password = _password_from_environment(password_env)

    def operation(connection: Connection) -> None:
        connection.execute(
            text("ALTER USER :user@:host IDENTIFIED BY :password"),
            {"user": user_name, "host": host, "password": password},
        )

    _run_management_operation(parsed, operation)


def drop_user(parsed: ParsedDsn, user_name: str, host: str) -> None:
    """Drop one MySQL account after protecting the current administrator."""

    _validate_management_dsn(parsed)
    _validate_account(user_name, host)

    def operation(connection: Connection) -> None:
        _reject_current_account(connection, user_name, host)
        connection.execute(text("DROP USER :user@:host"), {"user": user_name, "host": host})

    _run_management_operation(parsed, operation)


def grant_profile(
    parsed: ParsedDsn,
    user_name: str,
    host: str,
    database_name: str | None,
    profile: Profile,
) -> None:
    """Grant one fixed MySQL profile to an account."""

    _change_profile(parsed, user_name, host, database_name, profile, action="GRANT")


def revoke_profile(
    parsed: ParsedDsn,
    user_name: str,
    host: str,
    database_name: str | None,
    profile: Profile,
) -> None:
    """Revoke one fixed MySQL profile from an account."""

    _change_profile(parsed, user_name, host, database_name, profile, action="REVOKE")


def grant_privileges(
    parsed: ParsedDsn,
    user_name: str,
    host: str,
    database_name: str | None,
    privileges: tuple[str, ...],
) -> None:
    _change_privileges(parsed, user_name, host, database_name, privileges, action="GRANT")


def revoke_privileges(
    parsed: ParsedDsn,
    user_name: str,
    host: str,
    database_name: str | None,
    privileges: tuple[str, ...],
) -> None:
    _change_privileges(parsed, user_name, host, database_name, privileges, action="REVOKE")


def _change_privileges(
    parsed: ParsedDsn,
    user_name: str,
    host: str,
    database_name: str | None,
    privileges: tuple[str, ...],
    *,
    action: Literal["GRANT", "REVOKE"],
) -> None:
    _validate_management_dsn(parsed)
    _validate_account(user_name, host)
    target_database = database_name or parsed.database
    if target_database is None:
        raise DatabaseOperationError("database name is required when the DSN has no database")
    _validate_resource_name(target_database, "database name")
    if not privileges:
        raise DatabaseOperationError("at least one privilege is required")
    normalized = tuple(_normalize_privilege(value) for value in privileges)

    def operation(connection: Connection) -> None:
        _reject_current_account(connection, user_name, host)
        quoted_database = connection.dialect.identifier_preparer.quote(target_database)
        direction = "TO" if action == "GRANT" else "FROM"
        statement = (
            f"{action} {', '.join(normalized)} ON {quoted_database}.* {direction} :user@:host"
        )
        connection.execute(text(statement), {"user": user_name, "host": host})

    _run_management_operation(parsed, operation)


def _alter_user(parsed: ParsedDsn, user_name: str, host: str, action: str) -> None:
    _validate_management_dsn(parsed)
    _validate_account(user_name, host)

    def operation(connection: Connection) -> None:
        _reject_current_account(connection, user_name, host)
        connection.execute(
            text(f"ALTER USER :user@:host {action}"), {"user": user_name, "host": host}
        )

    _run_management_operation(parsed, operation)


def _change_profile(
    parsed: ParsedDsn,
    user_name: str,
    host: str,
    database_name: str | None,
    profile: Profile,
    *,
    action: Literal["GRANT", "REVOKE"],
) -> None:
    _validate_management_dsn(parsed)
    _validate_account(user_name, host)
    profile = _profile(profile)
    target_database = database_name or parsed.database
    if target_database is None:
        raise DatabaseOperationError("database name is required when the DSN has no database")
    _validate_resource_name(target_database, "database name")
    privileges = _profile_privileges(profile)

    def operation(connection: Connection) -> None:
        _reject_current_account(connection, user_name, host)
        quoted_database = connection.dialect.identifier_preparer.quote(target_database)
        statement = f"{action} {privileges} ON {quoted_database}.* "
        statement += "TO :user@:host" if action == "GRANT" else "FROM :user@:host"
        connection.execute(text(statement), {"user": user_name, "host": host})
        if profile == "migrator":
            global_statement = f"{action} CREATE ON *.* "
            global_statement += "TO :user@:host" if action == "GRANT" else "FROM :user@:host"
            connection.execute(text(global_statement), {"user": user_name, "host": host})

    _run_management_operation(parsed, operation)


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
    except SQLAlchemyError as error:
        raise DatabaseOperationError("MySQL user management failed") from error
    finally:
        if engine is not None:
            engine.dispose()


def _validate_management_dsn(parsed: ParsedDsn) -> None:
    if parsed.async_mode or parsed.dialect != "mysql":
        raise DatabaseOperationError("MySQL user management requires a synchronous mysql DSN")


def _validate_account(user_name: str, host: str) -> None:
    if not isinstance(user_name, str) or not _USER_PATTERN.fullmatch(user_name):
        raise DatabaseOperationError("MySQL user name is invalid")
    _validate_host(host)


def _validate_host(host: str) -> None:
    if not isinstance(host, str) or not host:
        raise DatabaseOperationError("MySQL user host is invalid")
    if (
        ("%" in host and host != "%")
        or "_" in host
        or any(category(character).startswith("C") for character in host)
    ):
        raise DatabaseOperationError(
            "MySQL user host must not contain wildcard or control characters"
        )
    if host in {"localhost", "%"}:
        return
    try:
        ip_address(host)
    except ValueError:
        if not _HOST_PATTERN.fullmatch(host):
            raise DatabaseOperationError("MySQL user host is invalid") from None


def _validate_resource_name(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DatabaseOperationError(f"{label} must not be blank")
    if any(character == "\x00" or category(character).startswith("C") for character in value):
        raise DatabaseOperationError(f"{label} must not contain control characters")


def _password_from_environment(environment_name: str) -> str:
    if not isinstance(environment_name, str) or not _ENVIRONMENT_PATTERN.fullmatch(
        environment_name
    ):
        raise DatabaseOperationError("password environment variable name is invalid")
    password = os.environ.get(environment_name)
    if not password:
        raise DatabaseOperationError("password environment variable is not set or is empty")
    return password


def _reject_current_account(connection: Connection, user_name: str, host: str) -> None:
    current_account = str(connection.exec_driver_sql("SELECT CURRENT_USER()").scalar_one())
    if current_account == _display_account(user_name, host):
        raise DatabaseOperationError("cannot change the current MySQL management account")


def _profile(profile: str) -> Profile:
    if profile in {"readonly", "readwrite", "migrator"}:
        return profile  # type: ignore[return-value]
    raise DatabaseOperationError("authorization profile is invalid")


def _profile_privileges(profile: Profile) -> str:
    profiles = {
        "readonly": "SELECT, SHOW VIEW",
        "readwrite": "SELECT, SHOW VIEW, INSERT, UPDATE, DELETE",
        "migrator": (
            "SELECT, SHOW VIEW, CREATE, ALTER, DROP, INDEX, CREATE VIEW, TRIGGER, "
            "INSERT, UPDATE, DELETE"
        ),
    }
    return profiles[profile]


def _normalize_privilege(value: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 _]*", value)
    ):
        raise DatabaseOperationError("privilege name is invalid")
    return value.upper()


def _require_yes(yes: bool, action: str) -> None:
    if not yes:
        raise click.UsageError(f"--yes is required to {action}")


def _display_account(user_name: str, host: str) -> str:
    return f"{user_name}@{host}"


def _render_users(users: tuple[MysqlUserRecord, ...]) -> str:
    rows = ((record.user, record.host, "yes" if record.locked else "no") for record in users)
    rendered = tabulate(rows, headers=("user", "host", "locked"), tablefmt="psql")
    return f"{rendered}\n(0 rows)" if not users else rendered


__all__ = [
    "MysqlUserRecord",
    "create_user",
    "disable_user",
    "drop_user",
    "enable_user",
    "grant_command",
    "grant_privileges",
    "grant_profile",
    "list_users",
    "resolve_management_dsn",
    "revoke_command",
    "revoke_privileges",
    "revoke_profile",
    "rotate_user_password",
    "user",
]
