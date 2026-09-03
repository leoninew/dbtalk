from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from sqlalchemy.dialects.mysql import dialect as MysqlDialect
from sqlalchemy.dialects.postgresql import dialect as PostgreSQLDialect
from sqlalchemy.exc import SQLAlchemyError

import dbtalk.mysql.database as mysql_database
import dbtalk.postgres.database as postgres_database
from dbtalk.cli import cli
from dbtalk.database.dsn import ParsedDsn, parse_dsn
from dbtalk.database.models import DatabaseOperationError


class FakeResult:
    def __init__(self, rows: list[tuple[str]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[str]]:
        return self._rows


class FakeConnection:
    def __init__(self, dialect: Any, rows: list[tuple[str]] | None = None) -> None:
        self.dialect = dialect
        self.rows = rows or []
        self.execution_options_value: dict[str, object] | None = None
        self.statements: list[str] = []
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
        return FakeResult(self.rows)


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.disposed = False

    def connect(self) -> FakeConnection:
        return self.connection

    def dispose(self) -> None:
        self.disposed = True


def parsed_mysql() -> ParsedDsn:
    return parse_dsn("mysql+pymysql://admin:secret@db.example/mysql")


def parsed_postgresql() -> ParsedDsn:
    return parse_dsn("postgresql+psycopg://admin:secret@db.example/postgres")


@pytest.mark.parametrize(
    ("module", "parsed", "dialect", "rows", "expected_sql"),
    [
        (mysql_database, parsed_mysql(), MysqlDialect(), [("zeta",), ("alpha",)], "SHOW DATABASES"),
        (
            postgres_database,
            parsed_postgresql(),
            PostgreSQLDialect(),  # type: ignore[no-untyped-call]
            [("zeta",), ("alpha",)],
            "SELECT datname FROM pg_database WHERE NOT datistemplate AND datallowconn "
            "ORDER BY datname",
        ),
    ],
)
def test_list_databases_uses_dialect_sql_and_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    parsed: ParsedDsn,
    dialect: Any,
    rows: list[tuple[str]],
    expected_sql: str,
) -> None:
    connection = FakeConnection(dialect, rows)
    engine = FakeEngine(connection)
    monkeypatch.setattr(module, "create_engine", lambda _: engine)

    assert module.list_databases(parsed) == ("alpha", "zeta")
    assert connection.statements == [expected_sql]
    assert connection.execution_options_value == {"isolation_level": "AUTOCOMMIT"}
    assert engine.disposed


@pytest.mark.parametrize(
    ("module", "parsed", "dialect", "operation", "prefix"),
    [
        (
            mysql_database,
            parsed_mysql(),
            MysqlDialect(),
            mysql_database.create_database,
            "CREATE DATABASE",
        ),
        (
            postgres_database,
            parsed_postgresql(),
            PostgreSQLDialect(),  # type: ignore[no-untyped-call]
            postgres_database.drop_database,
            "DROP DATABASE",
        ),
    ],
)
def test_database_lifecycle_operations_quote_names(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    parsed: ParsedDsn,
    dialect: Any,
    operation: Callable[[ParsedDsn, str], None],
    prefix: str,
) -> None:
    name = 'app "quoted" name'
    connection = FakeConnection(dialect)
    engine = FakeEngine(connection)
    monkeypatch.setattr(module, "create_engine", lambda _: engine)

    operation(parsed, name)

    assert connection.statements == [f"{prefix} {dialect.identifier_preparer.quote(name)}"]
    assert engine.disposed


def test_postgresql_cannot_drop_the_connected_database(monkeypatch: pytest.MonkeyPatch) -> None:
    create_engine = Mock()
    monkeypatch.setattr(postgres_database, "create_engine", create_engine)

    with pytest.raises(DatabaseOperationError, match="different maintenance database"):
        postgres_database.drop_database(parsed_postgresql(), "postgres")

    create_engine.assert_not_called()


def test_postgresql_drop_requires_a_maintenance_database(monkeypatch: pytest.MonkeyPatch) -> None:
    create_engine = Mock()
    monkeypatch.setattr(postgres_database, "create_engine", create_engine)

    with pytest.raises(DatabaseOperationError, match="maintenance database"):
        postgres_database.drop_database(
            parse_dsn("postgresql+psycopg://admin:secret@db.example/"), "app"
        )

    create_engine.assert_not_called()


@pytest.mark.parametrize("name", ["", "   ", "bad\x00name", "bad\nname"])
def test_database_name_validation_happens_before_connecting(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    create_engine = Mock()
    monkeypatch.setattr(mysql_database, "create_engine", create_engine)

    with pytest.raises(DatabaseOperationError, match="database name"):
        mysql_database.create_database(parsed_mysql(), name)

    create_engine.assert_not_called()


@pytest.mark.parametrize(
    ("module", "parsed", "expected_error"),
    [
        (mysql_database, parsed_mysql(), "MySQL database management failed"),
        (postgres_database, parsed_postgresql(), "PostgreSQL database management failed"),
    ],
)
def test_management_errors_are_redacted_and_dispose_engines(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    parsed: ParsedDsn,
    expected_error: str,
) -> None:
    connection = FakeConnection(MysqlDialect())
    connection.error = SQLAlchemyError("mysql+pymysql://admin:secret@db.example/mysql")
    engine = FakeEngine(connection)
    monkeypatch.setattr(module, "create_engine", lambda _: engine)

    with pytest.raises(DatabaseOperationError, match=expected_error) as error:
        module.list_databases(parsed)

    assert "secret" not in str(error.value)
    assert engine.disposed


def test_management_dsn_resolution_is_strict_and_dialect_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(DatabaseOperationError, match="exactly one"):
        mysql_database.resolve_management_dsn(None, None)
    with pytest.raises(DatabaseOperationError, match="MySQL database management requires"):
        mysql_database.resolve_management_dsn(
            "postgresql+psycopg://admin:secret@db.example/postgres", None
        )

    monkeypatch.setenv(
        "DBTALK_DSN_MYSQL_MANAGEMENT", "mysql+pymysql://admin:secret@db.example/mysql"
    )
    assert (
        mysql_database.resolve_management_dsn(None, "DBTALK_DSN_MYSQL_MANAGEMENT").dialect
        == "mysql"
    )


@pytest.mark.parametrize("group", ["mysql", "postgres"])
def test_database_management_help_exposes_no_transaction_options(group: str) -> None:
    result = CliRunner().invoke(cli, [group, "schema", "--help"])

    assert result.exit_code == 0, result.output
    assert {"create", "drop", "list"} <= set(result.output.split())
    assert "--autocommit" not in result.output
    assert "isolation" not in result.output.lower()


def test_mysql_list_command_renders_database_names(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = parsed_mysql()
    monkeypatch.setattr(mysql_database, "resolve_management_dsn", lambda *_: parsed)
    monkeypatch.setattr(mysql_database, "list_databases", lambda _: ("app", "system"))

    result = CliRunner().invoke(
        cli, ["mysql", "schema", "list", "--dsn-env", "MYSQL_MANAGEMENT_DSN"]
    )

    assert result.exit_code == 0, result.output
    assert "database" in result.output
    assert "app" in result.output
    assert "system" in result.output


def test_postgresql_create_and_drop_commands_report_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = parsed_postgresql()
    created: list[str] = []
    dropped: list[str] = []
    monkeypatch.setattr(postgres_database, "resolve_management_dsn", lambda *_: parsed)
    monkeypatch.setattr(postgres_database, "create_database", lambda _, name: created.append(name))
    monkeypatch.setattr(postgres_database, "drop_database", lambda _, name: dropped.append(name))
    runner = CliRunner()

    create = runner.invoke(
        cli,
        ["postgres", "schema", "create", "--dsn-env", "POSTGRES_MANAGEMENT_DSN", "--name", "app"],
    )
    drop = runner.invoke(
        cli,
        [
            "postgres",
            "schema",
            "drop",
            "--dsn-env",
            "POSTGRES_MANAGEMENT_DSN",
            "--name",
            "app",
            "--yes",
        ],
    )

    assert create.exit_code == 0, create.output
    assert drop.exit_code == 0, drop.output
    assert created == ["app"]
    assert dropped == ["app"]
    assert "Database created: app" in create.output
    assert "Database dropped: app" in drop.output


def test_drop_requires_yes_before_resolving_a_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve_dsn = Mock()
    monkeypatch.setattr(mysql_database, "resolve_management_dsn", resolve_dsn)

    result = CliRunner().invoke(
        cli,
        [
            "mysql",
            "schema",
            "drop",
            "--dsn",
            "mysql+pymysql://admin:secret@db.example/mysql",
            "--name",
            "app",
        ],
    )

    assert result.exit_code != 0
    assert "--yes is required" in result.output
    assert "secret" not in result.output
    resolve_dsn.assert_not_called()


def test_mysql_cli_errors_hide_dsn_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mysql_database,
        "list_databases",
        Mock(side_effect=DatabaseOperationError("MySQL database management failed")),
    )

    result = CliRunner().invoke(
        cli,
        ["mysql", "schema", "list", "--dsn", "mysql+pymysql://admin:secret@db.example/mysql"],
    )

    assert result.exit_code != 0
    assert "MySQL database management failed" in result.output
    assert "secret" not in result.output
