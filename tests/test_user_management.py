"""Tests for constrained MySQL user and PostgreSQL role management."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from sqlalchemy.dialects.mysql import dialect as MysqlDialect
from sqlalchemy.dialects.postgresql import dialect as _PostgreSQLDialect
from sqlalchemy.exc import SQLAlchemyError

import dbtalk.mysql.user as mysql_user
import dbtalk.postgres.role as postgres_role
from dbtalk.cli import cli
from dbtalk.database.dsn import ParsedDsn, parse_dsn
from dbtalk.database.models import DatabaseOperationError

PostgreSQLDialect: Any = _PostgreSQLDialect


class FakeResult:
    def __init__(self, rows: list[tuple[object, ...]] | None = None, scalar: str = "admin") -> None:
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows

    def scalar_one(self) -> str:
        return self._scalar


class FakePsycopgConnection:
    def __init__(self, statements: list[str]) -> None:
        self.statements = statements

    def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement.as_string(None))
        return FakeResult()


class FakeConnectionFairy:
    def __init__(self, statements: list[str]) -> None:
        self.driver_connection: FakePsycopgConnection | None = FakePsycopgConnection(statements)


class FakeConnection:
    def __init__(
        self,
        dialect: Any,
        *,
        rows: list[tuple[object, ...]] | None = None,
        current_identity: str = "admin",
    ) -> None:
        self.dialect = dialect
        self.rows = rows or []
        self.current_identity = current_identity
        self.execution_options_value: dict[str, object] | None = None
        self.statements: list[str] = []
        self.parameters: list[dict[str, object]] = []
        self.connection = FakeConnectionFairy(self.statements)
        self.error: SQLAlchemyError | None = None

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execution_options(self, **options: object) -> FakeConnection:
        self.execution_options_value = options
        return self

    def exec_driver_sql(self, statement: str) -> FakeResult:
        self.statements.append(statement)
        if self.error is not None:
            raise self.error
        if statement.startswith("SELECT CURRENT_USER"):
            return FakeResult(scalar=self.current_identity)
        return FakeResult(self.rows)

    def execute(self, statement: object, parameters: dict[str, object]) -> FakeResult:
        self.statements.append(str(statement))
        self.parameters.append(parameters)
        if self.error is not None:
            raise self.error
        return FakeResult()


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.disposed = False

    def connect(self) -> FakeConnection:
        return self.connection

    def dispose(self) -> None:
        self.disposed = True


def mysql_dsn() -> ParsedDsn:
    return parse_dsn("mysql+pymysql://admin:secret@db.example/app")


def postgresql_dsn() -> ParsedDsn:
    return parse_dsn("postgresql+psycopg://admin:secret@db.example/app")


def test_mysql_create_binds_password_and_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(MysqlDialect())
    engine = FakeEngine(connection)
    monkeypatch.setattr(mysql_user, "create_engine", lambda _: engine)
    monkeypatch.setenv("MYSQL_USER_PASSWORD", "test-password")

    mysql_user.create_user(mysql_dsn(), "app_user", "app.example", "MYSQL_USER_PASSWORD")

    assert connection.execution_options_value == {"isolation_level": "AUTOCOMMIT"}
    assert connection.statements == ["CREATE USER :user@:host IDENTIFIED BY :password"]
    assert set(connection.parameters[0]) == {"user", "host", "password"}
    assert engine.disposed


def test_mysql_profile_quotes_database_and_protects_current_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(MysqlDialect(), current_identity="admin@localhost")
    engine = FakeEngine(connection)
    monkeypatch.setattr(mysql_user, "create_engine", lambda _: engine)

    mysql_user.grant_profile(mysql_dsn(), "app_user", "app.example", 'app "quoted"', "migrator")

    assert connection.statements[0] == "SELECT CURRENT_USER()"
    assert connection.statements[1] == (
        "GRANT SELECT, SHOW VIEW, CREATE, ALTER, DROP, INDEX, CREATE VIEW, TRIGGER, "
        'INSERT, UPDATE, DELETE ON `app "quoted"`.* TO :user@:host'
    )
    assert connection.statements[2] == "GRANT CREATE ON *.* TO :user@:host"
    assert connection.parameters == [
        {"user": "app_user", "host": "app.example"},
        {"user": "app_user", "host": "app.example"},
    ]

    protected_connection = FakeConnection(MysqlDialect(), current_identity="admin@localhost")
    protected_engine = FakeEngine(protected_connection)
    monkeypatch.setattr(mysql_user, "create_engine", lambda _: protected_engine)

    with pytest.raises(DatabaseOperationError, match="current MySQL"):
        mysql_user.revoke_profile(mysql_dsn(), "admin", "localhost", "app", "readonly")

    assert protected_connection.statements == ["SELECT CURRENT_USER()"]


@pytest.mark.parametrize("host", ["api_%", "bad host", "bad\nname"])
def test_mysql_host_validation_happens_before_connecting(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    create_engine = Mock()
    monkeypatch.setattr(mysql_user, "create_engine", create_engine)

    with pytest.raises(DatabaseOperationError, match="host"):
        mysql_user.disable_user(mysql_dsn(), "app_user", host)

    create_engine.assert_not_called()


def test_mysql_literal_percent_host_is_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(MysqlDialect(), current_identity="root@%")
    monkeypatch.setattr(mysql_user, "create_engine", lambda _: FakeEngine(connection))
    monkeypatch.setenv("MYSQL_USER_PASSWORD", "test-password")

    mysql_user.rotate_user_password(mysql_dsn(), "root", "%", "MYSQL_USER_PASSWORD")

    assert connection.statements == [
        "ALTER USER :user@:host IDENTIFIED BY :password",
    ]
    assert connection.parameters == [{"user": "root", "host": "%", "password": "test-password"}]


def test_mysql_list_exposes_only_non_sensitive_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection(MysqlDialect(), rows=[("app", "api.example", "Y")])
    monkeypatch.setattr(mysql_user, "create_engine", lambda _: FakeEngine(connection))

    users = mysql_user.list_users(mysql_dsn())

    assert users == (mysql_user.MysqlUserRecord("app", "api.example", True),)
    assert connection.statements == [
        "SELECT User, Host, account_locked FROM mysql.user ORDER BY User, Host"
    ]
    assert "password" not in mysql_user._render_users(users).lower()


def test_mysql_high_risk_cli_requires_yes_before_dsn_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_dsn = Mock()
    monkeypatch.setattr(mysql_user, "resolve_management_dsn", resolve_dsn)

    result = CliRunner().invoke(
        cli,
        [
            "mysql",
            "grant",
            "--dsn",
            "mysql+pymysql://admin:secret@db.example/app",
            "--user",
            "app_user",
            "--host",
            "app.example",
            "--database",
            "app",
            "--profile",
            "readonly",
        ],
    )

    assert result.exit_code != 0
    assert "--yes is required" in result.output
    assert "secret" not in result.output
    resolve_dsn.assert_not_called()


def test_postgresql_create_uses_minimum_login_attributes_and_psycopg_password_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(PostgreSQLDialect())
    engine = FakeEngine(connection)
    monkeypatch.setattr(postgres_role, "create_engine", lambda _: engine)
    monkeypatch.setenv("POSTGRES_ROLE_PASSWORD", "test-password")

    postgres_role.create_role(postgresql_dsn(), "app_role", "POSTGRES_ROLE_PASSWORD")

    assert connection.execution_options_value == {"isolation_level": "AUTOCOMMIT"}
    assert connection.statements == [
        'CREATE ROLE "app_role" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
        "NOREPLICATION NOBYPASSRLS PASSWORD 'test-password'"
    ]
    assert connection.parameters == []
    assert engine.disposed


def test_postgresql_password_ddl_escapes_special_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(PostgreSQLDialect(), current_identity="admin")
    monkeypatch.setattr(postgres_role, "create_engine", lambda _: FakeEngine(connection))
    monkeypatch.setenv("POSTGRES_ROLE_PASSWORD", "pa'ss\\word")

    postgres_role.rotate_role_password(postgresql_dsn(), "app_role", "POSTGRES_ROLE_PASSWORD")

    assert connection.statements == [
        "SELECT CURRENT_USER",
        "ALTER ROLE \"app_role\" PASSWORD  E'pa''ss\\\\word'",
    ]


def test_postgresql_password_ddl_requires_a_driver_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(PostgreSQLDialect())
    connection.connection.driver_connection = None
    monkeypatch.setattr(postgres_role, "create_engine", lambda _: FakeEngine(connection))
    monkeypatch.setenv("POSTGRES_ROLE_PASSWORD", "test-password")

    with pytest.raises(DatabaseOperationError, match="PostgreSQL role management failed"):
        postgres_role.create_role(postgresql_dsn(), "app_role", "POSTGRES_ROLE_PASSWORD")


def test_postgresql_schema_migrator_profile_targets_existing_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(PostgreSQLDialect(), current_identity="admin")
    monkeypatch.setattr(postgres_role, "create_engine", lambda _: FakeEngine(connection))

    postgres_role.grant_profile(postgresql_dsn(), "app_role", ("schema", "app"), "migrator")

    assert connection.statements == [
        "SELECT CURRENT_USER",
        "GRANT USAGE ON SCHEMA app TO app_role",
        "GRANT CREATE ON SCHEMA app TO app_role",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO app_role",
        "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA app TO app_role",
        "ALTER ROLE app_role CREATEDB",
    ]


def test_postgresql_schema_readwrite_profile_targets_existing_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(PostgreSQLDialect(), current_identity="admin")
    monkeypatch.setattr(postgres_role, "create_engine", lambda _: FakeEngine(connection))

    postgres_role.grant_profile(postgresql_dsn(), "app_role", ("schema", "app"), "readwrite")

    assert connection.statements == [
        "SELECT CURRENT_USER",
        "GRANT USAGE ON SCHEMA app TO app_role",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO app_role",
        "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA app TO app_role",
    ]


@pytest.mark.parametrize(
    ("profile", "expected", "expected_global"),
    [
        ("readonly", "GRANT SELECT, SHOW VIEW ON app.* TO :user@:host", None),
        (
            "readwrite",
            "GRANT SELECT, SHOW VIEW, INSERT, UPDATE, DELETE ON app.* TO :user@:host",
            None,
        ),
        (
            "migrator",
            "GRANT SELECT, SHOW VIEW, CREATE, ALTER, DROP, INDEX, CREATE VIEW, TRIGGER, "
            "INSERT, UPDATE, DELETE ON app.* TO :user@:host",
            "GRANT CREATE ON *.* TO :user@:host",
        ),
    ],
)
def test_mysql_profiles_follow_hierarchy(
    monkeypatch: pytest.MonkeyPatch, profile: str, expected: str, expected_global: str | None
) -> None:
    connection = FakeConnection(MysqlDialect(), current_identity="admin@localhost")
    monkeypatch.setattr(mysql_user, "create_engine", lambda _: FakeEngine(connection))

    mysql_user.grant_profile(mysql_dsn(), "app_user", "app.example", "app", profile)  # type: ignore[arg-type]

    expected_statements = ["SELECT CURRENT_USER()", expected]
    if expected_global is not None:
        expected_statements.append(expected_global)
    assert connection.statements == expected_statements


def test_postgresql_migrator_profile_and_current_role_protection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(PostgreSQLDialect(), current_identity="admin")
    monkeypatch.setattr(postgres_role, "create_engine", lambda _: FakeEngine(connection))

    postgres_role.revoke_profile(postgresql_dsn(), "app_role", ("database", "app"), "migrator")

    assert connection.statements == [
        "SELECT CURRENT_USER",
        "REVOKE CONNECT, CREATE ON DATABASE app FROM app_role",
        "ALTER ROLE app_role NOCREATEDB",
    ]

    protected_connection = FakeConnection(PostgreSQLDialect(), current_identity="admin")
    monkeypatch.setattr(
        postgres_role,
        "create_engine",
        lambda _: FakeEngine(protected_connection),
    )

    with pytest.raises(DatabaseOperationError, match="current PostgreSQL"):
        postgres_role.disable_role(postgresql_dsn(), "admin")

    assert protected_connection.statements == ["SELECT CURRENT_USER"]


def test_postgresql_resource_validation_and_list_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(DatabaseOperationError, match="at most one"):
        postgres_role._resource("app", "public", postgresql_dsn())

    connection = FakeConnection(PostgreSQLDialect(), rows=[("app_role", True, False, False)])
    monkeypatch.setattr(postgres_role, "create_engine", lambda _: FakeEngine(connection))

    roles = postgres_role.list_roles(postgresql_dsn())

    assert roles == (postgres_role.PostgreSQLRoleRecord("app_role", True, False, False),)
    assert connection.statements == [
        "SELECT rolname, rolcanlogin, rolcreatedb, rolcreaterole FROM pg_roles ORDER BY rolname"
    ]
    assert "password" not in postgres_role._render_roles(roles).lower()


def test_grant_privilege_modes_are_mutually_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "mysql",
            "grant",
            "--dsn",
            "mysql+pymysql://admin:secret@db.example/app",
            "--user",
            "app_user",
            "--host",
            "app.example",
            "--profile",
            "readonly",
            "--privilege",
            "SELECT",
            "--yes",
        ],
    )

    assert result.exit_code != 0
    assert "mutually exclusive" in result.output
    assert "secret" not in result.output


def test_repeated_privileges_dispatch_and_default_database(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = mysql_dsn()
    grant = Mock()
    monkeypatch.setattr(mysql_user, "resolve_management_dsn", lambda *_: parsed)
    monkeypatch.setattr(mysql_user, "grant_privileges", grant)

    result = CliRunner().invoke(
        cli,
        [
            "mysql",
            "grant",
            "--dsn-env",
            "MYSQL_ADMIN_DSN",
            "--user",
            "app_user",
            "--host",
            "app.example",
            "--privilege",
            "SELECT",
            "--privilege",
            "UPDATE",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    grant.assert_called_once_with(parsed, "app_user", "app.example", None, ("SELECT", "UPDATE"))


def test_postgresql_high_risk_cli_requires_yes_without_exposing_dsn() -> None:
    result = CliRunner().invoke(
        cli,
        [
            "postgres",
            "role",
            "drop",
            "--dsn",
            "postgresql+psycopg://admin:secret@db.example/app",
            "--role",
            "app_role",
        ],
    )

    assert result.exit_code != 0
    assert "--yes is required" in result.output
    assert "secret" not in result.output


def test_user_management_errors_are_redacted_and_dispose_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(MysqlDialect())
    connection.error = SQLAlchemyError("mysql+pymysql://admin:secret@db.example/app")
    engine = FakeEngine(connection)
    monkeypatch.setattr(mysql_user, "create_engine", lambda _: engine)

    with pytest.raises(DatabaseOperationError, match="MySQL user management failed") as error:
        mysql_user.list_users(mysql_dsn())

    assert "secret" not in str(error.value)
    assert engine.disposed


def test_mysql_user_cli_dispatches_all_lifecycle_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = mysql_dsn()
    create = Mock()
    enable = Mock()
    disable = Mock()
    rotate = Mock()
    drop = Mock()
    monkeypatch.setattr(mysql_user, "resolve_management_dsn", lambda *_: parsed)
    monkeypatch.setattr(mysql_user, "list_users", lambda _: ())
    monkeypatch.setattr(mysql_user, "create_user", create)
    monkeypatch.setattr(mysql_user, "enable_user", enable)
    monkeypatch.setattr(mysql_user, "disable_user", disable)
    monkeypatch.setattr(mysql_user, "rotate_user_password", rotate)
    monkeypatch.setattr(mysql_user, "drop_user", drop)
    runner = CliRunner()
    common = ["--dsn-env", "MYSQL_ADMIN_DSN", "--user", "app_user", "--host", "app.example"]

    list_result = runner.invoke(cli, ["mysql", "user", "list", "--dsn-env", "MYSQL_ADMIN_DSN"])
    create_result = runner.invoke(
        cli,
        ["mysql", "user", "create", *common, "--password-env", "APP_PASSWORD"],
    )
    enable_result = runner.invoke(cli, ["mysql", "user", "enable", *common, "--yes"])
    disable_result = runner.invoke(cli, ["mysql", "user", "disable", *common, "--yes"])
    rotate_result = runner.invoke(
        cli,
        [
            "mysql",
            "user",
            "rotate-password",
            *common,
            "--password-env",
            "APP_PASSWORD",
            "--yes",
        ],
    )
    drop_result = runner.invoke(cli, ["mysql", "user", "drop", *common, "--yes"])

    for result in (
        list_result,
        create_result,
        enable_result,
        disable_result,
        rotate_result,
        drop_result,
    ):
        assert result.exit_code == 0, result.output
    create.assert_called_once_with(parsed, "app_user", "app.example", "APP_PASSWORD")
    enable.assert_called_once_with(parsed, "app_user", "app.example")
    disable.assert_called_once_with(parsed, "app_user", "app.example")
    rotate.assert_called_once_with(parsed, "app_user", "app.example", "APP_PASSWORD")
    drop.assert_called_once_with(parsed, "app_user", "app.example")


def test_mysql_grant_and_revoke_cli_dispatch_profile_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = mysql_dsn()
    grant = Mock()
    revoke = Mock()
    monkeypatch.setattr(mysql_user, "resolve_management_dsn", lambda *_: parsed)
    monkeypatch.setattr(mysql_user, "grant_profile", grant)
    monkeypatch.setattr(mysql_user, "revoke_profile", revoke)
    runner = CliRunner()
    options = [
        "--dsn-env",
        "MYSQL_ADMIN_DSN",
        "--user",
        "app_user",
        "--host",
        "app.example",
        "--database",
        "app",
        "--profile",
        "readonly",
        "--yes",
    ]

    granted = runner.invoke(cli, ["mysql", "grant", *options])
    revoked = runner.invoke(cli, ["mysql", "revoke", *options])

    assert granted.exit_code == 0, granted.output
    assert revoked.exit_code == 0, revoked.output
    grant.assert_called_once_with(parsed, "app_user", "app.example", "app", "readonly")
    revoke.assert_called_once_with(parsed, "app_user", "app.example", "app", "readonly")


def test_mysql_direct_lifecycle_operations_and_validation_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(MysqlDialect(), current_identity="admin@localhost")
    monkeypatch.setattr(mysql_user, "create_engine", lambda _: FakeEngine(connection))
    monkeypatch.setenv("MYSQL_USER_PASSWORD", "test-password")

    mysql_user.enable_user(mysql_dsn(), "app_user", "127.0.0.1")
    mysql_user.disable_user(mysql_dsn(), "app_user", "localhost")
    mysql_user.rotate_user_password(mysql_dsn(), "app_user", "app.example", "MYSQL_USER_PASSWORD")
    mysql_user.drop_user(mysql_dsn(), "app_user", "app.example")
    mysql_user.revoke_profile(mysql_dsn(), "app_user", "app.example", "app", "readonly")

    assert "ALTER USER :user@:host ACCOUNT UNLOCK" in connection.statements
    assert "ALTER USER :user@:host ACCOUNT LOCK" in connection.statements
    assert "ALTER USER :user@:host IDENTIFIED BY :password" in connection.statements
    assert "DROP USER :user@:host" in connection.statements
    assert "REVOKE SELECT, SHOW VIEW ON app.* FROM :user@:host" in connection.statements

    with pytest.raises(DatabaseOperationError, match="password environment"):
        mysql_user.create_user(mysql_dsn(), "app_user", "localhost", "MISSING_PASSWORD")
    with pytest.raises(DatabaseOperationError, match="user name"):
        mysql_user.disable_user(mysql_dsn(), "bad name", "localhost")
    with pytest.raises(DatabaseOperationError, match="database name"):
        mysql_user.grant_profile(
            parse_dsn("mysql+pymysql://admin:secret@db.example"),
            "app_user",
            "localhost",
            None,
            "readonly",
        )
    with pytest.raises(DatabaseOperationError, match="profile"):
        mysql_user._profile("admin")


def test_postgresql_role_cli_dispatches_all_lifecycle_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = postgresql_dsn()
    create = Mock()
    enable = Mock()
    disable = Mock()
    rotate = Mock()
    drop = Mock()
    monkeypatch.setattr(postgres_role, "resolve_management_dsn", lambda *_: parsed)
    monkeypatch.setattr(postgres_role, "list_roles", lambda _: ())
    monkeypatch.setattr(postgres_role, "create_role", create)
    monkeypatch.setattr(postgres_role, "enable_role", enable)
    monkeypatch.setattr(postgres_role, "disable_role", disable)
    monkeypatch.setattr(postgres_role, "rotate_role_password", rotate)
    monkeypatch.setattr(postgres_role, "drop_role", drop)
    runner = CliRunner()
    common = ["--dsn-env", "POSTGRES_ADMIN_DSN", "--role", "app_role"]

    list_result = runner.invoke(
        cli, ["postgres", "role", "list", "--dsn-env", "POSTGRES_ADMIN_DSN"]
    )
    create_result = runner.invoke(
        cli,
        ["postgres", "role", "create", *common, "--password-env", "APP_PASSWORD"],
    )
    enable_result = runner.invoke(cli, ["postgres", "role", "enable", *common, "--yes"])
    disable_result = runner.invoke(cli, ["postgres", "role", "disable", *common, "--yes"])
    rotate_result = runner.invoke(
        cli,
        [
            "postgres",
            "role",
            "rotate-password",
            *common,
            "--password-env",
            "APP_PASSWORD",
            "--yes",
        ],
    )
    drop_result = runner.invoke(cli, ["postgres", "role", "drop", *common, "--yes"])

    for result in (
        list_result,
        create_result,
        enable_result,
        disable_result,
        rotate_result,
        drop_result,
    ):
        assert result.exit_code == 0, result.output
    create.assert_called_once_with(parsed, "app_role", "APP_PASSWORD")
    enable.assert_called_once_with(parsed, "app_role")
    disable.assert_called_once_with(parsed, "app_role")
    rotate.assert_called_once_with(parsed, "app_role", "APP_PASSWORD")
    drop.assert_called_once_with(parsed, "app_role")


def test_postgresql_grant_and_revoke_cli_dispatch_profile_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = postgresql_dsn()
    grant = Mock()
    revoke = Mock()
    monkeypatch.setattr(postgres_role, "resolve_management_dsn", lambda *_: parsed)
    monkeypatch.setattr(postgres_role, "grant_profile", grant)
    monkeypatch.setattr(postgres_role, "revoke_profile", revoke)
    runner = CliRunner()
    options = [
        "--dsn-env",
        "POSTGRES_ADMIN_DSN",
        "--role",
        "app_role",
        "--schema",
        "app",
        "--profile",
        "readonly",
        "--yes",
    ]

    granted = runner.invoke(cli, ["postgres", "grant", *options])
    revoked = runner.invoke(cli, ["postgres", "revoke", *options])

    assert granted.exit_code == 0, granted.output
    assert revoked.exit_code == 0, revoked.output
    grant.assert_called_once_with(parsed, "app_role", ("schema", "app"), "readonly")
    revoke.assert_called_once_with(parsed, "app_role", ("schema", "app"), "readonly")


def test_postgresql_direct_lifecycle_operations_and_validation_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(PostgreSQLDialect(), current_identity="admin")
    monkeypatch.setattr(postgres_role, "create_engine", lambda _: FakeEngine(connection))
    monkeypatch.setenv("POSTGRES_ROLE_PASSWORD", "test-password")

    postgres_role.enable_role(postgresql_dsn(), "app_role")
    postgres_role.disable_role(postgresql_dsn(), "app_role")
    postgres_role.rotate_role_password(postgresql_dsn(), "app_role", "POSTGRES_ROLE_PASSWORD")
    postgres_role.drop_role(postgresql_dsn(), "app_role")
    postgres_role.revoke_profile(postgresql_dsn(), "app_role", ("schema", "app"), "readonly")

    assert "ALTER ROLE app_role LOGIN" in connection.statements
    assert "ALTER ROLE app_role NOLOGIN" in connection.statements
    assert "ALTER ROLE \"app_role\" PASSWORD 'test-password'" in connection.statements
    assert "DROP ROLE app_role" in connection.statements
    assert "REVOKE SELECT ON ALL TABLES IN SCHEMA app FROM app_role" in connection.statements

    with pytest.raises(DatabaseOperationError, match="role name"):
        postgres_role.disable_role(postgresql_dsn(), "bad role")
    with pytest.raises(DatabaseOperationError, match="password environment"):
        postgres_role.create_role(postgresql_dsn(), "app_role", "MISSING_PASSWORD")
    with pytest.raises(DatabaseOperationError, match="profile"):
        postgres_role._profile("owner")
    with pytest.raises(DatabaseOperationError, match="resource"):
        postgres_role._profile_statements("table", "app", "app_role", "readonly", "GRANT")


def test_postgresql_errors_are_redacted_and_role_dsn_is_dialect_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(PostgreSQLDialect())
    connection.error = SQLAlchemyError("postgresql+psycopg://admin:secret@db.example/app")
    engine = FakeEngine(connection)
    monkeypatch.setattr(postgres_role, "create_engine", lambda _: engine)

    with pytest.raises(DatabaseOperationError, match="PostgreSQL role management failed") as error:
        postgres_role.list_roles(postgresql_dsn())

    assert "secret" not in str(error.value)
    assert engine.disposed
    with pytest.raises(DatabaseOperationError, match="PostgreSQL role management requires"):
        postgres_role.resolve_management_dsn("mysql+pymysql://admin:secret@db.example/app", None)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["mysql", "user", "--help"], {"create", "disable", "rotate-password"}),
        (["postgres", "role", "--help"], {"create", "disable", "rotate-password"}),
        (["mysql", "grant", "--help"], {"--database", "--profile", "--yes"}),
        (["postgres", "grant", "--help"], {"--database", "--schema", "--profile"}),
    ],
)
def test_user_management_help_exposes_constrained_commands(
    command: list[str], expected: set[str]
) -> None:
    result = CliRunner().invoke(cli, command)

    assert result.exit_code == 0, result.output
    assert expected <= set(result.output.split())
    assert "--password " not in result.output
