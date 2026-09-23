"""Tests for explicit PostgreSQL object ownership reassignment."""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner
from sqlalchemy.dialects.postgresql import dialect as _PostgreSQLDialect
from sqlalchemy.exc import SQLAlchemyError

import dbtalk.postgres.owner as postgres_owner
from dbtalk.cli import cli
from dbtalk.database.dsn import parse_dsn
from dbtalk.database.models import DatabaseOperationError
from dbtalk.postgres.owner import OwnershipPreview

PostgreSQLDialect: Any = _PostgreSQLDialect


class Result:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def scalar_one(self) -> object:
        return self.rows[0][0]

    def all(self) -> list[tuple[object, ...]]:
        return self.rows

    def scalars(self) -> ScalarResult:
        return ScalarResult([row[0] for row in self.rows])


class ScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows


class Connection:
    def __init__(self) -> None:
        self.dialect = PostgreSQLDialect()
        self.statements: list[str] = []
        self.parameters: list[dict[str, object]] = []
        self.committed = False
        self.rolled_back = False
        self.fail_reassign = False
        self.database = "app"
        self.roles = [("source",), ("target",)]
        self.shared_objects: list[tuple[object, ...]] = [("pg_database", 1)]

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, error_type: object, *_: object) -> None:
        if error_type is not None:
            self.rolled_back = True
        else:
            self.committed = True

    def begin(self) -> Connection:
        return self

    def exec_driver_sql(self, statement: str) -> Result:
        self.statements.append(statement)
        if statement.startswith("REASSIGN") and self.fail_reassign:
            raise SQLAlchemyError("postgresql+psycopg://admin:secret@db.example/app")
        return Result([(self.database,)])

    def execute(self, statement: object, parameters: dict[str, object]) -> Result:
        self.statements.append(str(statement))
        self.parameters.append(parameters)
        if "SELECT rolname" in str(statement):
            return Result(list(self.roles))
        if "dbid = 0" in str(statement):
            return Result(self.shared_objects)
        return Result([("pg_class", 3)])


class Engine:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.disposed = False
        self.connected_database: str | None = None

    def connect(self) -> Connection:
        return self.connection

    def dispose(self) -> None:
        self.disposed = True


def parsed() -> Any:
    return parse_dsn("postgresql+psycopg://admin:secret@db.example/postgres")


def test_reassign_previews_without_writing_and_reports_shared_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = Connection()
    engine = Engine(connection)

    def create_engine(url: Any) -> Engine:
        engine.connected_database = url.database
        return engine

    monkeypatch.setattr(postgres_owner, "create_engine", create_engine)

    result = postgres_owner.reassign_owned(parsed(), "app", "source", "target")

    assert result == OwnershipPreview("app", (("pg_class", 3),), (("pg_database", 1),))
    assert engine.connected_database == "app"
    assert not any(statement.startswith("REASSIGN") for statement in connection.statements)
    assert connection.parameters == [
        {"source": "source", "target": "target"},
        {"source": "source"},
        {"source": "source"},
    ]
    assert engine.disposed


def test_reassign_executes_native_sql_in_one_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = Connection()
    engine = Engine(connection)
    monkeypatch.setattr(postgres_owner, "create_engine", lambda _: engine)

    postgres_owner.reassign_owned(
        parsed(), "app", "source", "target", execute=True, include_shared=True
    )

    assert connection.statements[-1] == "REASSIGN OWNED BY source TO target"
    assert connection.committed
    assert engine.disposed


@pytest.mark.parametrize("mismatch", ["database", "role"])
def test_reassign_refuses_changed_target_before_writing(
    monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    connection = Connection()
    if mismatch == "database":
        connection.database = "other"
    else:
        connection.roles = [("source",)]
    monkeypatch.setattr(postgres_owner, "create_engine", lambda _: Engine(connection))

    with pytest.raises(
        DatabaseOperationError, match="database does not match|both PostgreSQL roles"
    ):
        postgres_owner.reassign_owned(
            parsed(), "app", "source", "target", execute=True, include_shared=True
        )

    assert not any(statement.startswith("REASSIGN") for statement in connection.statements)
    assert connection.rolled_back


def test_reassign_rolls_back_and_redacts_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = Connection()
    connection.fail_reassign = True
    engine = Engine(connection)
    monkeypatch.setattr(postgres_owner, "create_engine", lambda _: engine)

    with pytest.raises(DatabaseOperationError, match="ownership reassignment failed") as error:
        postgres_owner.reassign_owned(
            parsed(), "app", "source", "target", execute=True, include_shared=True
        )

    assert "secret" not in str(error.value)
    assert connection.rolled_back
    assert engine.disposed


def test_owner_cli_previews_by_default_and_executes_only_with_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[bool, bool]] = []
    preview = OwnershipPreview("app", (("pg_class", 3),), (("pg_database", 1),))
    monkeypatch.setattr(postgres_owner, "resolve_management_dsn", lambda *_: parsed())

    def reassign(*_: object, execute: bool, include_shared: bool) -> OwnershipPreview:
        calls.append((execute, include_shared))
        return preview

    monkeypatch.setattr(postgres_owner, "reassign_owned", reassign)
    options = [
        "postgres",
        "owner",
        "reassign",
        "--dsn-env",
        "DBTALK_DSN_POSTGRES_ADMIN",
        "--database",
        "app",
        "--from-role",
        "source",
        "--to-role",
        "target",
    ]
    runner = CliRunner()

    dry_run = runner.invoke(cli, options)
    execute = runner.invoke(cli, [*options, "--yes"])

    assert dry_run.exit_code == 0, dry_run.output
    assert execute.exit_code == 0, execute.output
    assert calls == [(False, False), (True, False)]
    assert "shared (cluster-wide)" in dry_run.output
    assert "Preview only" in dry_run.output
    assert "ownership reassigned" in execute.output


def test_owner_cli_requires_database_and_valid_identifiers() -> None:
    runner = CliRunner()
    options = ["postgres", "owner", "reassign", "--dsn-env", "MISSING_DSN"]
    missing = runner.invoke(cli, options)
    assert missing.exit_code != 0
    assert "--database" in missing.output

    with pytest.raises(DatabaseOperationError, match="roles must differ"):
        postgres_owner.reassign_owned(parsed(), "app", "same", "same")
    with pytest.raises(DatabaseOperationError, match="role name is invalid"):
        postgres_owner.reassign_owned(parsed(), "app", "bad role", "target")


def test_owner_help_exposes_explicit_scope() -> None:
    result = CliRunner().invoke(cli, ["postgres", "owner", "reassign", "--help"])
    assert result.exit_code == 0, result.output
    assert {"--database", "--from-role", "--to-role", "--yes", "--include-shared"} <= set(
        result.output.split()
    )


def test_reassign_requires_explicit_shared_object_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = Connection()
    monkeypatch.setattr(postgres_owner, "create_engine", lambda _: Engine(connection))

    with pytest.raises(DatabaseOperationError, match="--include-shared is required"):
        postgres_owner.reassign_owned(parsed(), "app", "source", "target", execute=True)

    assert not any(statement.startswith("REASSIGN") for statement in connection.statements)
    assert connection.rolled_back


def test_reassign_proceeds_without_shared_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = Connection()
    connection.shared_objects = []
    monkeypatch.setattr(postgres_owner, "create_engine", lambda _: Engine(connection))

    postgres_owner.reassign_owned(parsed(), "app", "source", "target", execute=True)

    assert connection.statements[-1] == "REASSIGN OWNED BY source TO target"


def test_reassign_quotes_case_sensitive_role_names(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = Connection()
    connection.roles = [("SourceRole",), ("TargetRole",)]
    connection.shared_objects = []
    monkeypatch.setattr(postgres_owner, "create_engine", lambda _: Engine(connection))

    postgres_owner.reassign_owned(parsed(), "app", "SourceRole", "TargetRole", execute=True)

    assert connection.statements[-1] == 'REASSIGN OWNED BY "SourceRole" TO "TargetRole"'
