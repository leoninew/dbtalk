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
from dbtalk.database.dsn import dsn_from_environment, dsn_metadata, parse_dsn, sqlite_dsn
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
    render_query,
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
            "--write",
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


def test_query_and_exec_cli_require_explicit_write_and_support_timeouts(tmp_path: Path) -> None:
    path = tmp_path / "safeguards.db"
    create_database(path)
    dsn = f"sqlite:///{path.as_posix()}"
    runner = CliRunner()

    missing_write = runner.invoke(
        cli,
        [
            "exec",
            "--dsn",
            dsn,
            "--sql",
            "UPDATE users SET name = 'Grace' WHERE id = 1",
        ],
    )
    assert missing_write.exit_code != 0
    assert "database execution failed" in missing_write.output

    read_only_exec = runner.invoke(
        cli,
        [
            "exec",
            "--dsn",
            dsn,
            "--sql",
            "SELECT name FROM users WHERE id = 1",
        ],
    )
    assert read_only_exec.exit_code == 0, read_only_exec.output

    query_write = runner.invoke(
        cli,
        [
            "query",
            "--dsn",
            dsn,
            "--timeout",
            "1",
            "--sql",
            "UPDATE users SET name = 'Grace' WHERE id = 1",
        ],
    )
    assert query_write.exit_code != 0
    assert "database query failed" in query_write.output

    execution = runner.invoke(
        cli,
        [
            "exec",
            "-w",
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
