from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from click.testing import CliRunner
from sqlalchemy.dialects.mysql import dialect as mysql_dialect
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from dbtalk.cli import cli
from dbtalk.database.connection import (
    AsyncDatabaseClient,
    AsyncDatabaseSession,
    DatabaseClient,
    DatabaseSession,
    _engine_options,
    create_async_client,
    create_client,
)
from dbtalk.database.dsn import (
    dsn_from_environment,
    dsn_metadata,
    parse_dsn,
    password_from_environment,
    sqlite_dsn,
)
from dbtalk.database.models import (
    ColumnDefinition,
    DatabaseDriver,
    DatabaseOperationError,
    DatabaseTransferError,
    ExportOptions,
    ImportOptions,
    TableBlockHeader,
    TableSchema,
    TransferConnection,
)
from dbtalk.database.operations import (
    execute_from_environment,
    json_safe_value,
    parse_parameters,
    query_from_environment,
    read_sql_file,
    render_query,
    sql_script_statements,
)
from dbtalk.database.sqlalchemy_transfer import (
    _encoded_rows,
    _prepare_connection,
    _quote_identifier,
    _select_sql,
    _target_values,
    _verify_database,
)
from dbtalk.database.transfer import export_database, import_database, validate_connection


def create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, active BOOLEAN)")
    connection.execute("INSERT INTO users VALUES (1, 'Ada', 1)")
    connection.commit()
    connection.close()


def test_dsn_parses_supported_sync_and_async_urls() -> None:
    parsed = parse_dsn("mysql+pymysql://user:secret@db.example:3307/app")
    assert parsed.url.drivername == "mysql+pymysql"
    assert parsed.display == "mysql+pymysql://user:***@db.example:3307/app"
    assert parse_dsn(
        "mysql+asyncmy://user:secret@db.example/app", async_mode=True
    ).url.drivername == ("mysql+asyncmy")
    assert parse_dsn("postgresql+psycopg://user:secret@db.example/app").url.drivername == (
        "postgresql+psycopg"
    )
    assert parse_dsn("mysql+pymysql://user:secret@db.example/").database is None
    assert parse_dsn("mysql+asyncmy://user:secret@db.example/", async_mode=True).database is None
    assert parse_dsn("postgresql+psycopg://user:secret@db.example/").database is None
    assert (
        parse_dsn("postgresql+psycopg://user:secret@db.example/", async_mode=True).database is None
    )
    assert parse_dsn("sqlite:///data.db", async_mode=True).url.drivername == "sqlite+aiosqlite"


def test_dsn_rejects_invalid_or_unsupported_values() -> None:
    with pytest.raises(DatabaseOperationError, match="must not be empty"):
        parse_dsn("")
    with pytest.raises(DatabaseOperationError, match="unsupported database dialect"):
        parse_dsn("oracle+oracledb://user:pass@host/app")
    with pytest.raises(DatabaseOperationError, match="sqlite DSN"):
        parse_dsn("sqlite://")
    with pytest.raises(DatabaseOperationError, match="environment variable"):
        dsn_from_environment("DBTALK_DSN_MISSING")
    with pytest.raises(DatabaseOperationError, match="DSN is invalid"):
        parse_dsn("mysql+pymysql://user:pass@host:bad/app")


def test_dsn_requires_explicit_driver_and_validates_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = parse_dsn("postgresql+psycopg://user:secret@db.example:5432/app")
    assert parsed.dialect == "postgresql"
    assert parsed.url.drivername == "postgresql+psycopg"
    assert dsn_metadata(parsed) == {
        "dialect": "postgresql",
        "host": "db.example",
        "port": 5432,
        "database": "app",
    }
    with pytest.raises(DatabaseOperationError, match="explicit driver"):
        parse_dsn("postgresql://user:secret@db.example/app")
    with pytest.raises(DatabaseOperationError, match="unsupported database dialect: postgres"):
        parse_dsn("postgres://user:secret@db.example/app")
    with pytest.raises(DatabaseOperationError, match="explicit driver"):
        parse_dsn("mysql://user:secret@db.example/app")
    with pytest.raises(DatabaseOperationError, match="unsupported sqlite driver"):
        parse_dsn("sqlite+foo:///tmp/app.db")
    with pytest.raises(DatabaseOperationError, match="between 1 and 65535"):
        parse_dsn("mysql+pymysql://user:pass@host:0/app")
    with pytest.raises(DatabaseOperationError, match="--dsn-env is required"):
        dsn_from_environment(None)
    monkeypatch.delenv("DBTALK_DSN_MISSING", raising=False)


def test_dsn_from_environment_reads_current_dotenv_for_dbtalk_dsn_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "DBTALK_DSN_APP=sqlite:///:memory:\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DBTALK_DSN_APP", raising=False)

    assert dsn_from_environment("DBTALK_DSN_APP").url.drivername == "sqlite"


def test_dsn_from_environment_ignores_dotenv_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env.local").write_text(
        "DBTALK_DSN_APP=sqlite:///:memory:\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DBTALK_DSN_APP", raising=False)

    with pytest.raises(DatabaseOperationError, match="environment variable is not set"):
        dsn_from_environment("DBTALK_DSN_APP")


def test_dsn_from_environment_prefers_process_value_over_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "DBTALK_DSN_APP=postgresql+psycopg://user:secret@dotenv.example/app\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DBTALK_DSN_APP", "sqlite:///:memory:")

    assert dsn_from_environment("DBTALK_DSN_APP").dialect == "sqlite"


def test_dsn_from_environment_does_not_fallback_for_empty_or_non_dsn_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "DBTALK_DSN_APP=sqlite:///:memory:\nAPP_DSN=sqlite:///:memory:\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DBTALK_DSN_APP", "")
    monkeypatch.delenv("APP_DSN", raising=False)

    with pytest.raises(DatabaseOperationError, match="environment variable is not set"):
        dsn_from_environment("DBTALK_DSN_APP")
    with pytest.raises(DatabaseOperationError, match="environment variable is not set"):
        dsn_from_environment("APP_DSN")


def test_password_from_environment_reads_current_dotenv_for_dbtalk_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "DBTALK_MYSQL_ROOT_PASSWORD=from-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DBTALK_MYSQL_ROOT_PASSWORD", raising=False)

    assert password_from_environment("DBTALK_MYSQL_ROOT_PASSWORD") == "from-dotenv"


def test_password_from_environment_ignores_dotenv_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env.local").write_text(
        "DBTALK_MYSQL_ROOT_PASSWORD=from-local\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DBTALK_MYSQL_ROOT_PASSWORD", raising=False)

    with pytest.raises(DatabaseOperationError, match="password environment"):
        password_from_environment("DBTALK_MYSQL_ROOT_PASSWORD")


def test_password_from_environment_prefers_process_value_over_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "DBTALK_MYSQL_ROOT_PASSWORD=from-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DBTALK_MYSQL_ROOT_PASSWORD", "from-process")

    assert password_from_environment("DBTALK_MYSQL_ROOT_PASSWORD") == "from-process"


def test_password_from_environment_does_not_fallback_for_empty_or_non_dbtalk_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "DBTALK_MYSQL_ROOT_PASSWORD=from-dotenv\nAPP_PASSWORD=from-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DBTALK_MYSQL_ROOT_PASSWORD", "")
    monkeypatch.delenv("APP_PASSWORD", raising=False)

    with pytest.raises(DatabaseOperationError, match="password environment"):
        password_from_environment("DBTALK_MYSQL_ROOT_PASSWORD")
    with pytest.raises(DatabaseOperationError, match="password environment"):
        password_from_environment("APP_PASSWORD")


def test_database_clients_accept_urls_and_reject_wrong_async_mode(tmp_path: Path) -> None:
    path = tmp_path / "client-url.db"
    create_database(path)
    with create_client(make_url(f"sqlite:///{path.as_posix()}")) as client:
        assert client.dialect == "sqlite"

    async_parsed = parse_dsn("sqlite:///:memory:", async_mode=True)
    with pytest.raises(DatabaseOperationError, match="async DSN"):
        DatabaseClient(async_parsed)
    sync_parsed = parse_dsn("sqlite:///:memory:")
    with pytest.raises(DatabaseOperationError, match="requires an async DSN"):
        AsyncDatabaseClient(sync_parsed)


def test_database_session_maps_sqlalchemy_errors() -> None:
    class FailingConnection:
        def execute(self, *_: object) -> Any:
            raise SQLAlchemyError("secret database details")

    session = DatabaseSession(cast(Any, FailingConnection()))
    with pytest.raises(DatabaseOperationError, match="database query failed"):
        session.query("SELECT 1")
    with pytest.raises(DatabaseOperationError, match="database execution failed"):
        session.execute("UPDATE users SET name = :name", {"name": "secret"})


@pytest.mark.asyncio
async def test_async_session_maps_sqlalchemy_errors_and_context_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingConnection:
        async def execute(self, *_: object) -> Any:
            raise SQLAlchemyError("secret database details")

    session = AsyncDatabaseSession(cast(Any, FailingConnection()))
    with pytest.raises(DatabaseOperationError, match="database query failed"):
        await session.query("SELECT 1")
    with pytest.raises(DatabaseOperationError, match="database execution failed"):
        await session.execute("UPDATE users SET name = :name", {"name": "secret"})

    client = create_async_client(make_url("sqlite:///:memory:"))
    try:

        def raise_connection_error(_: Any) -> Any:
            raise SQLAlchemyError("secret")

        monkeypatch.setattr(type(client._engine), "connect", raise_connection_error)
        with pytest.raises(DatabaseOperationError, match="database connection failed"):
            async with client.connect():
                pass
    finally:
        await client.close()


def test_sync_client_context_manager_maps_engine_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DatabaseClient("sqlite:///:memory:")
    try:
        monkeypatch.setattr(
            client._engine,
            "connect",
            lambda: (_ for _ in ()).throw(SQLAlchemyError("secret")),
        )
        with (
            pytest.raises(DatabaseOperationError, match="database connection failed"),
            client.connect(),
        ):
            pass
        monkeypatch.setattr(
            client._engine,
            "begin",
            lambda: (_ for _ in ()).throw(SQLAlchemyError("secret")),
        )
        with (
            pytest.raises(DatabaseOperationError, match="database transaction failed"),
            client.transaction(),
        ):
            pass
    finally:
        client.close()


def test_sync_client_query_exec_and_transaction_rollback(tmp_path: Path) -> None:
    path = tmp_path / "query.db"
    create_database(path)
    client = DatabaseClient(f"sqlite:///{path.as_posix()}")
    try:
        result = client.query("SELECT id, name FROM users WHERE id = :id", {"id": 1})
        assert result.columns == ("id", "name")
        assert result.rows == ((1, "Ada"),)
        assert (
            client.execute(
                "UPDATE users SET name = :name WHERE id = :id", {"name": "Grace", "id": 1}
            ).row_count
            == 1
        )
        with pytest.raises(RuntimeError), client.transaction() as transaction:
            transaction.execute("UPDATE users SET name = :name", {"name": "bad"})
            raise RuntimeError("rollback")
        assert client.query("SELECT name FROM users").rows == (("Grace",),)
        with pytest.raises(DatabaseOperationError, match="query failed"):
            client.query("SELECT missing FROM users")
    finally:
        client.close()


@pytest.mark.asyncio
async def test_async_client_query_exec_and_transaction(tmp_path: Path) -> None:
    path = tmp_path / "async.db"
    create_database(path)
    client = AsyncDatabaseClient(f"sqlite:///{path.as_posix()}")
    try:
        assert (await client.query("SELECT name FROM users")).rows == (("Ada",),)
        assert (
            await client.execute(
                "UPDATE users SET active = :active WHERE id = :id",
                {"active": 0, "id": 1},
            )
        ).row_count == 1
        with pytest.raises(DatabaseOperationError, match="query failed"):
            await client.query("SELECT missing FROM users")
    finally:
        await client.close()


def test_query_rendering_and_parameter_parsing() -> None:
    with DatabaseClient("sqlite:///:memory:") as client:
        result = client.query("SELECT 1 AS id, NULL AS value")
    assert "NULL" in render_query(result, "table")
    payload = json.loads(render_query(result, "json"))
    assert payload == {
        "columns": ["id", "value"],
        "rows": [{"id": 1, "value": None}],
        "row_count": 1,
    }
    assert parse_parameters(("id=1", 'name="Ada"', "active=true")) == {
        "id": 1,
        "name": "Ada",
        "active": True,
    }
    with pytest.raises(DatabaseOperationError, match="valid JSON"):
        parse_parameters(("name=Ada",))
    with pytest.raises(DatabaseOperationError, match="NAME=JSON_VALUE"):
        parse_parameters(("invalid",))
    with pytest.raises(DatabaseOperationError, match="duplicate"):
        parse_parameters(("id=1", "id=2"))
    with pytest.raises(DatabaseOperationError, match="table or json"):
        render_query(result, "csv")


def test_json_safe_value_encodes_database_values() -> None:
    assert json_safe_value(None) is None
    assert json_safe_value(Decimal("1.20")) == "1.20"
    assert json_safe_value(date(2026, 8, 20)) == "2026-08-20"
    assert json_safe_value(datetime(2026, 8, 20, 8, 0)) == "2026-08-20T08:00:00"
    assert json_safe_value(time(8, 0)) == "08:00:00"
    assert json_safe_value(b"\x00\xff") == {
        "type": "base64",
        "value": base64.b64encode(b"\x00\xff").decode("ascii"),
    }
    assert json_safe_value({"nested": [Decimal("2")]}) == {"nested": ["2"]}
    assert json_safe_value(object())


def test_canonical_dsn_transfer_round_trip_and_upsert(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    connection.executemany("INSERT INTO users VALUES (?, ?)", [(1, "Ada"), (2, "Grace")])
    connection.commit()
    connection.close()
    connection = sqlite3.connect(target)
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("INSERT INTO users VALUES (1, 'old')")
    connection.commit()
    connection.close()

    source_dsn = f"sqlite:///{source.as_posix()}"
    target_dsn = f"sqlite:///{target.as_posix()}"
    transfer_path = tmp_path / "transfer.jsonl"
    exported = export_database(
        ExportOptions(
            connection=TransferConnection("sqlite", dsn=source_dsn),
            output=transfer_path,
            timezone=UTC,
        )
    )
    imported = import_database(
        ImportOptions(
            connection=TransferConnection("sqlite", dsn=target_dsn),
            input=transfer_path,
            mode="upsert",
            timezone=UTC,
        )
    )
    assert (exported.table_count, exported.row_count) == (1, 2)
    assert (imported.table_count, imported.row_count) == (1, 2)
    connection = sqlite3.connect(target)
    assert connection.execute("SELECT * FROM users ORDER BY id").fetchall() == [
        (1, "Ada"),
        (2, "Grace"),
    ]
    connection.close()


def test_canonical_dsn_transfer_validation_and_environment(tmp_path: Path) -> None:
    path = tmp_path / "database.db"
    create_database(path)
    dsn = sqlite_dsn(path)
    assert dsn.startswith("sqlite:///")
    assert parse_dsn(dsn).database is not None
    with pytest.raises(DatabaseOperationError, match="does not exist"):
        sqlite_dsn(tmp_path / "missing.db")
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(DatabaseOperationError, match="directory"):
        sqlite_dsn(directory)
    with pytest.raises(DatabaseTransferError, match="exactly one"):
        validate_connection(TransferConnection("postgresql"))
    with pytest.raises(DatabaseTransferError, match="does not match"):
        validate_connection(TransferConnection("sqlite", dsn="postgresql+psycopg://u:p@h/db"))
    database_free_connections: tuple[tuple[DatabaseDriver, str], ...] = (
        ("mysql", "mysql+pymysql://u:p@h/"),
        ("postgresql", "postgresql+psycopg://u:p@h/"),
    )
    for driver, database_free_dsn in database_free_connections:
        with pytest.raises(DatabaseTransferError, match="transfer requires a database name"):
            validate_connection(TransferConnection(driver, dsn=database_free_dsn))
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv("DBTALK_DSN_CANONICAL", dsn)
        assert dsn_from_environment("DBTALK_DSN_CANONICAL").dialect == "sqlite"
        assert query_from_environment(
            "DBTALK_DSN_CANONICAL",
            "SELECT 1",
            timeout_seconds=30,
        ).rows == ((1,),)
        assert (
            execute_from_environment(
                "DBTALK_DSN_CANONICAL",
                "CREATE TABLE another (id INTEGER)",
                timeout_seconds=30,
            ).row_count
            == 0
        )
    finally:
        monkeypatch.undo()


def test_canonical_transfer_preserves_foreign_key_order_and_insert_mode(tmp_path: Path) -> None:
    source = tmp_path / "source-fk.db"
    target = tmp_path / "target-fk.db"
    for path in (source, target):
        connection = sqlite3.connect(path)
        connection.executescript(
            "CREATE TABLE parent (id INTEGER PRIMARY KEY, name TEXT);"
            "CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL, "
            "note TEXT, FOREIGN KEY(parent_id) REFERENCES parent(id));"
        )
        connection.commit()
        connection.close()
    connection = sqlite3.connect(source)
    connection.executemany("INSERT INTO parent VALUES (?, ?)", [(1, "Ada")])
    connection.executemany("INSERT INTO child VALUES (?, ?, ?)", [(10, 1, "child")])
    connection.commit()
    connection.close()

    transfer_path = tmp_path / "fk-transfer.jsonl"
    assert (
        export_database(
            ExportOptions(
                connection=TransferConnection("sqlite", dsn=f"sqlite:///{source.as_posix()}"),
                output=transfer_path,
                timezone=UTC,
            )
        ).row_count
        == 2
    )
    assert (
        import_database(
            ImportOptions(
                connection=TransferConnection("sqlite", dsn=f"sqlite:///{target.as_posix()}"),
                input=transfer_path,
                mode="insert",
                timezone=UTC,
            )
        ).row_count
        == 2
    )
    connection = sqlite3.connect(target)
    assert connection.execute("SELECT COUNT(*) FROM child").fetchone() == (1,)
    connection.close()


def test_sqlalchemy_transfer_covers_dialect_boundaries() -> None:
    class DriverConnection:
        def __init__(self, dialect: Any) -> None:
            self.dialect = dialect
            self.statements: list[str] = []

        def exec_driver_sql(self, statement: str) -> None:
            self.statements.append(statement)

    mysql_connection = DriverConnection(mysql_dialect())
    _prepare_connection(
        cast(Any, mysql_connection),
        parse_dsn("mysql+pymysql://user:pass@host/app"),
        read_only=True,
    )
    assert mysql_connection.statements == [
        "SET TRANSACTION READ ONLY",
        "START TRANSACTION WITH CONSISTENT SNAPSHOT",
    ]

    postgresql_dialect_factory: Any = postgresql_dialect
    postgres_connection = DriverConnection(postgresql_dialect_factory())
    schema = TableSchema(
        "order details",
        (ColumnDefinition("order id", "INTEGER"),),
        ("order id",),
        (),
    )
    assert _select_sql(cast(Any, postgres_connection), schema) == (
        'SELECT "order id" FROM "order details"'
    )
    assert _quote_identifier(cast(Any, postgres_connection), "order id") == '"order id"'
    with pytest.raises(DatabaseTransferError, match="identifier is invalid"):
        _quote_identifier(cast(Any, postgres_connection), "bad\x00name")

    header = TableBlockHeader("orders", (ColumnDefinition("id", "INTEGER"),), ("id",))
    options = ImportOptions(
        connection=TransferConnection("postgresql", dsn="postgresql+psycopg://user:pass@host/app"),
        input=Path("transfer.jsonl"),
        mode="insert",
        timezone=UTC,
    )
    assert _target_values(
        (1,),
        header,
        TableSchema("orders", header.columns, ("id",), ()),
        options,
        parse_dsn("postgresql+psycopg://user:pass@host/app"),
    ) == (1,)
    boolean_header = TableBlockHeader(
        "projects", (ColumnDefinition("is_active", "TINYINT(1)"),), ("is_active",)
    )
    assert _target_values(
        (1,),
        boolean_header,
        TableSchema("projects", (ColumnDefinition("is_active", "BOOLEAN"),), ("is_active",), ()),
        options,
        parse_dsn("postgresql+psycopg://user:pass@host/app"),
    ) == (True,)
    _verify_database(
        cast(Any, postgres_connection),
        parse_dsn("postgresql+psycopg://user:pass@host/app"),
    )

    class BatchResult:
        def __init__(self) -> None:
            self.batches: list[list[tuple[object, ...]]] = [
                [(timedelta(hours=1, minutes=2, seconds=3, microseconds=400000),)],
                [],
            ]

        def fetchmany(self, _: int) -> list[tuple[object, ...]]:
            return self.batches.pop(0)

    encoded = list(
        _encoded_rows(
            BatchResult(),
            TableSchema("events", (ColumnDefinition("duration", "TIME"),), (), ()),
            ExportOptions(
                connection=TransferConnection("mysql", dsn="mysql+pymysql://user:pass@host/app"),
                output=Path("transfer.jsonl"),
                timezone=UTC,
            ),
            parse_dsn("mysql+pymysql://user:pass@host/app"),
        )
    )
    assert encoded == [("01:02:03.4",)]


def test_sql_script_statements_skips_comments_and_transaction_control() -> None:
    statements = sql_script_statements(
        """
        /* header */
        START TRANSACTION;
        -- cleanup
        DELETE FROM users WHERE id = 1;
        INSERT INTO users (id, name) VALUES (1, '{"optional":true,"note":"a;b"}');
        COMMIT;
        """
    )
    assert len(statements) == 2
    assert statements[0].endswith("DELETE FROM users WHERE id = 1")
    assert "optional" in statements[1]
    assert "a;b" in statements[1]


def test_sql_script_statements_keeps_procedure_body_and_dollar_quotes() -> None:
    statements = sql_script_statements(
        """
        CREATE FUNCTION add_one(i integer) RETURNS integer AS $$
        BEGIN
            RETURN i + 1;
        END;
        $$ LANGUAGE plpgsql;
        SELECT add_one(1);
        """
    )
    assert len(statements) == 2
    assert statements[0].startswith("CREATE FUNCTION add_one")
    assert "RETURN i + 1;" in statements[0]
    assert statements[1] == "SELECT add_one(1)"


def test_read_sql_file_rejects_missing_empty_and_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sql"
    with pytest.raises(DatabaseOperationError, match="SQL file does not exist"):
        read_sql_file(missing)
    empty = tmp_path / "empty.sql"
    empty.write_text(" \n", encoding="utf-8")
    with pytest.raises(DatabaseOperationError, match="SQL file is empty"):
        read_sql_file(empty)
    with pytest.raises(DatabaseOperationError, match="SQL file path is a directory"):
        read_sql_file(tmp_path)


def test_exec_file_runs_literal_script_in_one_transaction(tmp_path: Path) -> None:
    path = tmp_path / "cli.db"
    create_database(path)
    dsn = f"sqlite:///{path.as_posix()}"
    runner = CliRunner()
    script = tmp_path / "update.sql"
    script.write_text(
        """
        START TRANSACTION;
        UPDATE users SET name = '{"optional":true}' WHERE id = 1;
        COMMIT;
        """,
        encoding="utf-8",
    )

    result = runner.invoke(cli, ["exec", "--dsn", dsn, "--file", str(script)])
    assert result.exit_code == 0, result.output
    assert "1 rows affected" in result.output

    query = runner.invoke(
        cli,
        ["query", "--dsn", dsn, "--sql", "SELECT name FROM users WHERE id = 1", "--format", "json"],
    )
    assert query.exit_code == 0, query.output
    assert json.loads(query.output)["rows"] == [{"name": '{"optional":true}'}]


def test_exec_dry_run_prints_sql_without_executing(tmp_path: Path) -> None:
    path = tmp_path / "dry-run.db"
    create_database(path)
    dsn = f"sqlite:///{path.as_posix()}"
    runner = CliRunner()
    script = tmp_path / "update.sql"
    script.write_text(
        "UPDATE users SET name = 'Grace' WHERE id = 1;\n"
        "UPDATE users SET active = 0 WHERE id = 1;\n",
        encoding="utf-8",
    )

    file_preview = runner.invoke(cli, ["exec", "--dsn", dsn, "--file", str(script), "--dry-run"])
    assert file_preview.exit_code == 0, file_preview.output
    assert "UPDATE users SET name = 'Grace' WHERE id = 1;" in file_preview.output
    assert "UPDATE users SET active = 0 WHERE id = 1;" in file_preview.output
    assert "SQL dry-run completed (2 statements)" in file_preview.output
    assert "rows affected" not in file_preview.output

    sql_preview = runner.invoke(
        cli,
        [
            "exec",
            "--dsn",
            dsn,
            "--sql",
            "UPDATE users SET name = 'Grace' WHERE id = 1",
            "--dry-run",
        ],
    )
    assert sql_preview.exit_code == 0, sql_preview.output
    assert "UPDATE users SET name = 'Grace' WHERE id = 1;" in sql_preview.output
    assert "SQL dry-run completed (1 statements)" in sql_preview.output

    query = runner.invoke(
        cli,
        [
            "query",
            "--dsn",
            dsn,
            "--sql",
            "SELECT name, active FROM users WHERE id = 1",
            "--format",
            "json",
        ],
    )
    assert query.exit_code == 0, query.output
    assert json.loads(query.output)["rows"] == [{"name": "Ada", "active": 1}]


def test_exec_file_rolls_back_when_a_later_statement_fails(tmp_path: Path) -> None:
    path = tmp_path / "rollback.db"
    create_database(path)
    dsn = f"sqlite:///{path.as_posix()}"
    script = tmp_path / "bad.sql"
    script.write_text(
        "UPDATE users SET name = 'Grace' WHERE id = 1;\nUPDATE missing SET name = 1;\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(cli, ["exec", "--dsn", dsn, "--file", str(script)])
    assert result.exit_code != 0
    assert "database execution failed" in result.output

    query = CliRunner().invoke(
        cli,
        ["query", "--dsn", dsn, "--sql", "SELECT name FROM users WHERE id = 1", "--format", "json"],
    )
    assert query.exit_code == 0, query.output
    assert json.loads(query.output)["rows"] == [{"name": "Ada"}]


def test_exec_requires_sql_or_file_exclusively(tmp_path: Path) -> None:
    path = tmp_path / "cli.db"
    create_database(path)
    dsn = f"sqlite:///{path.as_posix()}"
    runner = CliRunner()
    script = tmp_path / "ok.sql"
    script.write_text("UPDATE users SET name = 'Grace' WHERE id = 1;", encoding="utf-8")

    neither = runner.invoke(cli, ["exec", "--dsn", dsn])
    assert neither.exit_code != 0
    assert "provide exactly one of --sql or --file" in neither.output

    both = runner.invoke(
        cli,
        ["exec", "--dsn", dsn, "--sql", "SELECT 1", "--file", str(script)],
    )
    assert both.exit_code != 0
    assert "provide exactly one of --sql or --file" in both.output

    with_param = runner.invoke(
        cli,
        ["exec", "--dsn", dsn, "--file", str(script), "--param", "id=1"],
    )
    assert with_param.exit_code != 0
    assert "--param cannot be used with --file" in with_param.output

    comments_only = tmp_path / "comments.sql"
    comments_only.write_text("-- only a comment\n", encoding="utf-8")
    no_statements = runner.invoke(cli, ["exec", "--dsn", dsn, "--file", str(comments_only)])
    assert no_statements.exit_code != 0
    assert "SQL file contains no executable statements" in no_statements.output


def test_query_and_exec_cli_use_dsn_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cli.db"
    create_database(path)
    monkeypatch.setenv("DBTALK_DSN_QUERY", f"sqlite:///{path.as_posix()}")
    runner = CliRunner()

    query = runner.invoke(
        cli,
        [
            "query",
            "--dsn-env",
            "DBTALK_DSN_QUERY",
            "--sql",
            "SELECT name FROM users WHERE id = :id",
            "--param",
            "id=1",
            "--format",
            "json",
        ],
    )
    assert query.exit_code == 0, query.output
    assert json.loads(query.output)["rows"] == [{"name": "Ada"}]

    direct_query = runner.invoke(
        cli,
        [
            "query",
            "--dsn",
            f"sqlite:///{path.as_posix()}",
            "--sql",
            "SELECT name FROM users WHERE id = :id",
            "--param",
            "id=1",
        ],
    )
    assert direct_query.exit_code == 0, direct_query.output
    assert "Ada" in direct_query.output

    execution = runner.invoke(
        cli,
        [
            "exec",
            "--dsn-env",
            "DBTALK_DSN_QUERY",
            "--sql",
            "UPDATE users SET name = :name WHERE id = :id",
            "--param",
            'name="Grace"',
            "--param",
            "id=1",
        ],
    )
    assert execution.exit_code == 0, execution.output
    assert "1 rows affected" in execution.output


def test_query_and_exec_cli_session_modes_and_timeouts(tmp_path: Path) -> None:
    path = tmp_path / "safeguards.db"
    create_database(path)
    dsn = f"sqlite:///{path.as_posix()}"
    runner = CliRunner()

    execution = runner.invoke(
        cli,
        [
            "exec",
            "--dsn",
            dsn,
            "--timeout",
            "1",
            "--sql",
            "UPDATE users SET name = 'Grace' WHERE id = 1",
        ],
    )
    assert execution.exit_code == 0, execution.output
    assert "1 rows affected" in execution.output

    readable_exec = runner.invoke(
        cli,
        [
            "exec",
            "--dsn",
            dsn,
            "--sql",
            "SELECT name FROM users WHERE id = 1",
        ],
    )
    assert readable_exec.exit_code == 0, readable_exec.output

    query_write = runner.invoke(
        cli,
        [
            "query",
            "--dsn",
            dsn,
            "--timeout",
            "1",
            "--sql",
            "UPDATE users SET name = 'Ada' WHERE id = 1",
        ],
    )
    assert query_write.exit_code != 0
    assert "database query failed" in query_write.output
    assert "attempt to write a readonly database" not in query_write.output

    verbose_query = runner.invoke(
        cli,
        [
            "query",
            "-v",
            "--dsn",
            dsn,
            "--sql",
            "UPDATE users SET name = 'Ada' WHERE id = 1",
        ],
    )
    assert verbose_query.exit_code != 0
    assert "database query failed" in verbose_query.output
    assert "sqlalchemy.exc.OperationalError" in verbose_query.output

    for extra in ("--write", "-w"):
        rejected = runner.invoke(
            cli,
            [
                "exec",
                extra,
                "--dsn",
                dsn,
                "--sql",
                "UPDATE users SET name = 'Ada' WHERE id = 1",
            ],
        )
        assert rejected.exit_code != 0
        assert "No such option" in rejected.output

    invalid_timeout = runner.invoke(
        cli,
        [
            "query",
            "--dsn",
            dsn,
            "--timeout",
            "0",
            "--sql",
            "SELECT 1",
        ],
    )
    assert invalid_timeout.exit_code != 0
    assert "x>=1" in invalid_timeout.output


def test_exec_verbose_shows_sanitized_exception_details(tmp_path: Path) -> None:
    path = tmp_path / "verbose.db"
    create_database(path)
    dsn = f"sqlite:///{path.as_posix()}"
    runner = CliRunner()
    sql = "UPDATE missing SET name = :name WHERE id = :id"

    default = runner.invoke(
        cli,
        ["exec", "--dsn", dsn, "--sql", sql, "--param", 'name="secret"', "--param", "id=1"],
    )
    assert default.exit_code != 0
    assert "database execution failed" in default.output
    assert "OperationalError" not in default.output
    assert "secret" not in default.output
    assert "[parameters:]" not in default.output

    verbose = runner.invoke(
        cli,
        [
            "exec",
            "-v",
            "--dsn",
            dsn,
            "--sql",
            sql,
            "--param",
            'name="secret"',
            "--param",
            "id=1",
        ],
    )
    assert verbose.exit_code != 0
    assert "database execution failed" in verbose.output
    assert "sqlalchemy.exc.OperationalError" in verbose.output
    assert "secret" not in verbose.output
    assert "[parameters:]" not in verbose.output
    assert "[SQL:" not in verbose.output

    root_verbose = runner.invoke(
        cli,
        ["-v", "exec", "--dsn", dsn, "--sql", "SELECT 1"],
    )
    assert root_verbose.exit_code != 0
    assert "No such option" in root_verbose.output
    assert "-v" in root_verbose.output


def test_settings_verbose_shows_exception_details_without_command_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings-verbose.db"
    create_database(path)
    monkeypatch.setenv("DBTALK_VERBOSE", "true")
    result = CliRunner().invoke(
        cli,
        [
            "exec",
            "--dsn",
            f"sqlite:///{path.as_posix()}",
            "--sql",
            "UPDATE missing SET name = 1",
        ],
    )
    assert result.exit_code != 0
    assert "database execution failed" in result.output
    assert "sqlalchemy.exc.OperationalError" in result.output


def test_sqlite_statement_timeout_interrupts_query_and_cleans_up(tmp_path: Path) -> None:
    path = tmp_path / "timeout.db"
    create_database(path)
    slow_query = (
        "WITH RECURSIVE sequence(value) AS (VALUES(1) UNION ALL "
        "SELECT value + 1 FROM sequence WHERE value < 100000000) "
        "SELECT sum(value) FROM sequence"
    )
    with DatabaseClient(f"sqlite:///{path.as_posix()}", timeout_seconds=0.01) as client:
        with pytest.raises(DatabaseOperationError, match="query timed out"):
            client.query(slow_query)
        assert client.query("SELECT name FROM users WHERE id = 1").rows == (("Ada",),)


def test_engine_options_keep_statement_and_connection_timeouts_separate() -> None:
    mysql = parse_dsn("mysql+pymysql://user:password@host:3306/app")
    postgres = parse_dsn("postgresql+psycopg://user:password@host:5432/app")

    assert _engine_options(mysql, 30, 7) == {
        "connect_args": {"read_timeout": 30, "write_timeout": 30, "connect_timeout": 7}
    }
    assert _engine_options(postgres, 30, 7) == {"connect_args": {"connect_timeout": 7}}


def test_async_client_does_not_block_event_loop(tmp_path: Path) -> None:
    path = tmp_path / "async-event.db"
    create_database(path)

    async def run() -> tuple[tuple[object, ...], tuple[object, ...]]:
        client = AsyncDatabaseClient(f"sqlite:///{path.as_posix()}")
        try:
            first = (await client.query("SELECT id FROM users")).rows
            await asyncio.sleep(0)
            second = (await client.query("SELECT active FROM users")).rows
            return first, second
        finally:
            await client.close()

    assert asyncio.run(run()) == (((1,),), ((1,),))
