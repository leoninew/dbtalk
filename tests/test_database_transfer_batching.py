from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine

from dbtalk.database import sqlalchemy_transfer as adapter
from dbtalk.database.dsn import ParsedDsn, dsn_from_environment
from dbtalk.database.format import TRANSFER_FORMAT, encode_value, scan_document, write_document
from dbtalk.database.models import (
    ColumnDefinition,
    DatabaseTransferError,
    ExportOptions,
    ImportOptions,
    JSONValue,
    TableBlock,
    TableBlockHeader,
    TableSchema,
    TransferConnection,
    TransferDocument,
    TransferHeader,
    TransferMode,
)
from dbtalk.database.transfer import export_database, import_database


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    engine = create_engine(f"sqlite:///{tmp_path / 'target.db'}")
    monkeypatch.setattr(adapter, "_create_engine", lambda parsed: engine)
    yield engine
    engine.dispose()


def _write_transfer(path: Path, *tables: TableBlock) -> Path:
    write_document(path, TransferDocument(TransferHeader(TRANSFER_FORMAT, "sqlite"), tables))
    return path


def _import_options(engine: Engine, path: Path, mode: TransferMode = "insert") -> ImportOptions:
    return ImportOptions(TransferConnection("sqlite", dsn=str(engine.url)), path, mode, UTC)


def _insert_calls(engine: Engine) -> list[tuple[str, Any, bool]]:
    calls: list[tuple[str, Any, bool]] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if statement.startswith("INSERT INTO"):
            calls.append((statement, parameters, executemany))

    return calls


def test_insert_executes_row_bounded_batches(engine: Engine, tmp_path: Path) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT)")
    calls = _insert_calls(engine)
    count = adapter.BATCH_SIZE + 2
    path = _write_transfer(
        tmp_path / "transfer.jsonl.gz",
        TableBlock(
            TableBlockHeader(
                "items",
                (ColumnDefinition("id", "INTEGER"), ColumnDefinition("value", "TEXT")),
                ("id",),
            ),
            tuple((index, f"value-{index}") for index in range(count)),
        ),
    )

    assert import_database(_import_options(engine, path)).row_count == count

    assert [len(parameters) for _, parameters, _ in calls] == [adapter.BATCH_SIZE, 2]
    assert all(executemany for _, _, executemany in calls)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM items").scalar() == count


def test_insert_budget_uses_utf8_and_decoded_blob_bytes(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapter, "BATCH_BYTE_BUDGET", 17)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT, payload BLOB)"
        )
    calls = _insert_calls(engine)
    blob = encode_value(b"abc", "BLOB", UTC)
    path = _write_transfer(
        tmp_path / "transfer.jsonl",
        TableBlock(
            TableBlockHeader(
                "items",
                (
                    ColumnDefinition("id", "INTEGER"),
                    ColumnDefinition("value", "TEXT"),
                    ColumnDefinition("payload", "BLOB"),
                ),
                ("id",),
            ),
            (
                (1, "\u00e9", blob),
                (2, "\u00e9", blob),
                (3, "\u00e9", blob),
                (4, "x" * 20, blob),
                (5, "\u00e9", blob),
                (6, "\u00e9", blob),
            ),
        ),
    )

    assert import_database(_import_options(engine, path)).row_count == 6

    assert [len(parameters) if many else 1 for _, parameters, many in calls] == [2, 1, 1, 2]
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT value, payload FROM items WHERE id = 1"
        ).one() == ("\u00e9", b"abc")
        assert (
            connection.exec_driver_sql("SELECT value FROM items WHERE id = 4").scalar() == "x" * 20
        )


def test_upsert_preserves_composite_key_order_across_batches(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapter, "BATCH_SIZE", 2)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE pairs(left_id INTEGER, right_id INTEGER, value TEXT, "
            "PRIMARY KEY(left_id, right_id))"
        )
        connection.exec_driver_sql("INSERT INTO pairs VALUES (1, 1, 'old')")
    calls = _insert_calls(engine)
    path = _write_transfer(
        tmp_path / "transfer.jsonl",
        TableBlock(
            TableBlockHeader(
                "pairs",
                (
                    ColumnDefinition("left_id", "INTEGER"),
                    ColumnDefinition("right_id", "INTEGER"),
                    ColumnDefinition("value", "TEXT"),
                ),
                ("left_id", "right_id"),
            ),
            ((1, 1, "first"), (1, 1, "second"), (1, 1, "third"), (1, 2, "new")),
        ),
    )

    assert import_database(_import_options(engine, path, "upsert")).row_count == 4

    assert len(calls) == 2
    assert all("ON CONFLICT (left_id, right_id) DO UPDATE" in sql for sql, _, _ in calls)
    assert all(many for _, _, many in calls)
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT * FROM pairs ORDER BY right_id"
        ).tuples().all() == [
            (1, 1, "third"),
            (1, 2, "new"),
        ]


def test_upsert_primary_key_only_counts_input_rows(engine: Engine, tmp_path: Path) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE items(id INTEGER PRIMARY KEY)")
    path = _write_transfer(
        tmp_path / "transfer.jsonl",
        TableBlock(
            TableBlockHeader("items", (ColumnDefinition("id", "INTEGER"),), ("id",)),
            ((1,), (1,), (2,)),
        ),
    )

    assert import_database(_import_options(engine, path, "upsert")).row_count == 3

    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT id FROM items ORDER BY id").tuples().all() == [
            (1,),
            (2,),
        ]


def test_preflight_converts_later_table_before_first_write(engine: Engine, tmp_path: Path) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE a_items(id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE z_flags(id INTEGER PRIMARY KEY, active BOOLEAN)")
    calls = _insert_calls(engine)
    path = _write_transfer(
        tmp_path / "transfer.jsonl",
        TableBlock(
            TableBlockHeader("a_items", (ColumnDefinition("id", "INTEGER"),), ("id",)),
            ((1,),),
        ),
        TableBlock(
            TableBlockHeader(
                "z_flags",
                (ColumnDefinition("id", "INTEGER"), ColumnDefinition("active", "INTEGER")),
                ("id",),
            ),
            ((1, 2),),
        ),
    )

    with pytest.raises(DatabaseTransferError, match="z_flags.*JSONL line.*BOOLEAN"):
        import_database(_import_options(engine, path))

    assert calls == []
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM a_items").scalar() == 0


@pytest.mark.parametrize("encoding_fails", [False, True])
def test_export_streams_and_closes_result(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, encoding_fails: bool
) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE items(id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql(
            "INSERT INTO items VALUES (?)", [(index,) for index in range(adapter.BATCH_SIZE + 1)]
        )
    results: list[Any] = []
    fetch_sizes: list[int] = []
    encoded_rows = adapter._encoded_rows

    def observe(
        result: Any,
        schema: TableSchema,
        options: ExportOptions,
        parsed: ParsedDsn,
        progress: adapter._TableProgress | None = None,
    ) -> Iterator[tuple[JSONValue, ...]]:
        results.append(result)
        assert result.context.execution_options["stream_results"] is True
        assert result.context.execution_options["yield_per"] == adapter.BATCH_SIZE
        with patch.object(result, "fetchmany", wraps=result.fetchmany) as fetch:
            try:
                yield from encoded_rows(result, schema, options, parsed, progress)
            finally:
                fetch_sizes.extend(call.args[0] for call in fetch.call_args_list)

    monkeypatch.setattr(adapter, "_encoded_rows", observe)
    if encoding_fails:

        def fail_encoding(value: object, declared_type: str, timezone: Any) -> JSONValue:
            raise DatabaseTransferError("encoding failed")

        monkeypatch.setattr(adapter, "encode_value", fail_encoding)
    output = tmp_path / "export.jsonl"
    options = ExportOptions(TransferConnection("sqlite", dsn=str(engine.url)), output, UTC)

    if encoding_fails:
        with pytest.raises(DatabaseTransferError, match="encoding failed"):
            export_database(options)
        assert not output.exists()
        assert not output.with_suffix(".jsonl.tmp").exists()
    else:
        assert export_database(options).row_count == adapter.BATCH_SIZE + 1
        assert scan_document(output).tables[0].row_count == adapter.BATCH_SIZE + 1

    assert len(results) == 1
    assert results[0].closed
    assert fetch_sizes == [adapter.BATCH_SIZE] * (1 if encoding_fails else 3)


@pytest.mark.integration
@pytest.mark.skipif(
    "DBTALK_DSN_BATCH_TEST" not in os.environ,
    reason="requires DBTALK_DSN_BATCH_TEST pointing to an isolated PostgreSQL test database",
)
def test_postgres_batch_upsert_pipeline_and_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psycopg

    parsed = dsn_from_environment("DBTALK_DSN_BATCH_TEST")
    assert parsed.dialect == "postgresql"
    schema = f"dbtalk_batch_{uuid4().hex}"
    engine = create_engine(parsed.url, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(adapter, "_create_engine", lambda dsn: engine)
    monkeypatch.setattr(adapter, "BATCH_SIZE", 2)
    calls = _insert_calls(engine)
    pipelines: list[bool] = []
    pipeline = psycopg.Connection._pipeline_nolock

    def capture_pipeline(connection: Any) -> Any:
        pipelines.append(True)
        return pipeline(connection)

    monkeypatch.setattr(psycopg.Connection, "_pipeline_nolock", capture_pipeline)
    cursors: list[Any] = []
    encoded_rows = adapter._encoded_rows

    def observe(
        result: Any,
        table: TableSchema,
        options: ExportOptions,
        dsn: ParsedDsn,
        progress: adapter._TableProgress | None = None,
    ) -> Iterator[tuple[JSONValue, ...]]:
        cursors.append(result.cursor)
        assert result.context._is_server_side
        yield from encoded_rows(result, table, options, dsn, progress)

    monkeypatch.setattr(adapter, "_encoded_rows", observe)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql("CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT)")
        path = _write_transfer(
            tmp_path / "transfer.jsonl",
            TableBlock(
                TableBlockHeader(
                    "items",
                    (ColumnDefinition("id", "INTEGER"), ColumnDefinition("value", "TEXT")),
                    ("id",),
                ),
                ((1, "first"), (1, "second"), (1, "third"), (2, "new")),
            ),
        )
        connection_options = TransferConnection("postgresql", dsn_env="DBTALK_DSN_BATCH_TEST")
        assert (
            import_database(ImportOptions(connection_options, path, "upsert", UTC)).row_count == 4
        )
        assert len(calls) == 2
        assert all(many for _, _, many in calls)
        if psycopg.pq.version() >= 140000:
            assert pipelines
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT * FROM items ORDER BY id").tuples().all() == [
                (1, "third"),
                (2, "new"),
            ]
        assert (
            export_database(
                ExportOptions(connection_options, tmp_path / "export.jsonl", UTC)
            ).row_count
            == 2
        )
        assert len(cursors) == 1
        assert cursors[0].closed
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        engine.dispose()
