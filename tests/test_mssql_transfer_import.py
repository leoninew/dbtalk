from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import sqlalchemy
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from dbtalk.database import sqlalchemy_transfer
from dbtalk.database.dsn import parse_dsn
from dbtalk.database.format import TRANSFER_FORMAT, decode_mssql_value, write_document
from dbtalk.database.models import (
    ColumnDefinition,
    ColumnMetadata,
    DatabaseTransferError,
    ImportOptions,
    JSONValue,
    TableBlock,
    TableBlockHeader,
    TransferDocument,
    TransferHeader,
    TransferMode,
)
from dbtalk.database.transfer import import_database
from tests.test_mssql_transfer_export import _SOURCE, _column, _source


@dataclass
class TargetHarness:
    connection: MagicMock
    inspector: MagicMock
    actual: Connection
    events: list[str]


@contextmanager
def _target(
    monkeypatch: pytest.MonkeyPatch,
    tables: dict[str, list[dict[str, object]]],
    ddl: tuple[str, ...],
) -> Iterator[TargetHarness]:
    connection, inspector, _ = _source(monkeypatch, tables)
    monkeypatch.setattr(sqlalchemy_transfer, "inspect", lambda connection: inspector)
    engine = create_engine("sqlite://")
    events: list[str] = []
    with engine.connect() as actual:
        with actual.begin():
            for statement in ddl:
                actual.exec_driver_sql(statement)
        catalog = connection.execute.side_effect

        def execute(statement: Any, parameters: Any = None) -> Any:
            sql = str(statement)
            if "sys.columns" in sql:
                return catalog(statement, parameters)
            events.append(sql)
            sql = sql.replace("dbo.", "").replace(" WITH (UPDLOCK, HOLDLOCK)", "")
            sql = sql.replace(" AS VARBINARY(MAX)", " AS BLOB")
            return actual.execute(text(sql), parameters)

        @contextmanager
        def begin() -> Iterator[None]:
            events.append("begin")
            try:
                with actual.begin():
                    yield
            except BaseException:
                events.append("rollback")
                raise
            events.append("commit")

        def rollback() -> None:
            actual.rollback()
            events.append("rollback")

        def session_sql(statement: str) -> MagicMock:
            events.append(statement)
            return MagicMock()

        connection.execute.side_effect = execute
        connection.begin.side_effect = begin
        connection.rollback.side_effect = rollback
        connection.exec_driver_sql.side_effect = session_sql
        connection.invalidate.side_effect = lambda: events.append("invalidate")
        yield TargetHarness(connection, inspector, actual, events)
    engine.dispose()


def _block(
    rows: tuple[tuple[JSONValue, ...], ...],
    *,
    name: str = "items",
    columns: tuple[tuple[str, str], ...] = (("id", "INTEGER"), ("label", "TEXT")),
    primary: tuple[str, ...] = ("id",),
) -> TableBlock:
    return TableBlock(
        TableBlockHeader(name, tuple(ColumnDefinition(*column) for column in columns), primary),
        rows,
    )


def _options(path: Path, *blocks: TableBlock, mode: TransferMode = "insert") -> ImportOptions:
    write_document(path, TransferDocument(TransferHeader(TRANSFER_FORMAT, "sqlite"), blocks))
    return ImportOptions(_SOURCE, path, mode, UTC)


def test_identity_insert_batches_and_clears_state_before_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    columns = [
        _column("id", "int", is_identity=True, is_nullable=False),
        _column("label", "nvarchar", max_length=40),
    ]
    with _target(
        monkeypatch,
        {"items": columns},
        ("CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT)",),
    ) as target:
        options = _options(
            tmp_path / "input.jsonl", _block(tuple((index, "value") for index in range(1002)))
        )
        assert import_database(options).row_count == 1002
        assert target.actual.exec_driver_sql("SELECT COUNT(*) FROM items").scalar() == 1002
        assert sum(event.startswith("INSERT") for event in target.events) == 2
        assert (
            target.events.index("SET IDENTITY_INSERT dbo.items ON")
            < target.events.index("SET IDENTITY_INSERT dbo.items OFF")
            < target.events.index("commit")
        )
        target.connection.invalidate.assert_not_called()


def test_upsert_locks_complete_key_and_does_not_update_non_key_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    columns = [
        _column("id", "int"),
        _column("tenant", "int"),
        _column("serial", "int", is_identity=True),
        _column("label", "nvarchar", max_length=40),
    ]
    ddl = (
        "CREATE TABLE items(id INTEGER, tenant INTEGER, serial INTEGER, "
        "label TEXT, PRIMARY KEY(tenant,id))",
        "INSERT INTO items VALUES (1,7,10,'old')",
    )
    with _target(monkeypatch, {"items": columns}, ddl) as target:
        target.inspector.get_pk_constraint.return_value = {"constrained_columns": ["tenant", "id"]}
        block = _block(
            ((1, 7, 99, "changed"), (2, 7, 20, "added"), (2, 7, 20, "last")),
            columns=(
                ("id", "INTEGER"),
                ("tenant", "INTEGER"),
                ("serial", "INTEGER"),
                ("label", "TEXT"),
            ),
            primary=("tenant", "id"),
        )
        assert (
            import_database(_options(tmp_path / "upsert.jsonl", block, mode="upsert")).row_count
            == 3
        )
        assert [
            tuple(row) for row in target.actual.exec_driver_sql("SELECT * FROM items ORDER BY id")
        ] == [
            (1, 7, 10, "changed"),
            (2, 7, 20, "last"),
        ]
        locked = [event for event in target.events if "UPDLOCK" in event]
        assert len(locked) == 3
        assert all("tenant = :value_1 AND id = :value_0" in event for event in locked)
        assert all("serial" not in event for event in target.events if event.startswith("UPDATE"))


def test_full_preflight_rejects_late_invalid_value_before_any_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    columns = [_column("id", "int", is_identity=True), _column("label", "nvarchar", max_length=4)]
    with _target(
        monkeypatch,
        {"items": columns},
        ("CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT)",),
    ) as target:
        options = _options(tmp_path / "invalid.jsonl", _block(((1, "ok"), (2, "too long"))))
        with pytest.raises(DatabaseTransferError, match="JSONL line.*target length"):
            import_database(options)
        assert "begin" not in target.events
        assert target.actual.exec_driver_sql("SELECT COUNT(*) FROM items").scalar() == 0
        target.connection.exec_driver_sql.assert_not_called()


def test_binary_values_cast_in_insert_update_and_primary_key_predicate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    columns = [
        _column("id", "varbinary", max_length=20, is_nullable=False),
        _column("payload", "varbinary", max_length=-1),
        _column("label", "nvarchar", max_length=40),
    ]
    empty = {"$type": "blob", "base64": ""}
    nonempty = {"$type": "blob", "base64": "AP8="}
    declarations = (("id", "BLOB"), ("payload", "BLOB"), ("label", "TEXT"))
    ddl = ("CREATE TABLE items(id BLOB PRIMARY KEY, payload BLOB, label TEXT)",)
    with _target(monkeypatch, {"items": columns}, ddl) as target:
        initial = _block(((empty, nonempty, "initial"),), columns=declarations)
        assert import_database(_options(tmp_path / "binary.jsonl", initial)).row_count == 1
        changed = _block(
            ((empty, empty, "cleared"), (nonempty, empty, "new")), columns=declarations
        )
        assert (
            import_database(_options(tmp_path / "binary.jsonl", changed, mode="upsert")).row_count
            == 2
        )
        assert [
            tuple(row) for row in target.actual.exec_driver_sql("SELECT * FROM items ORDER BY id")
        ] == [
            (b"", b"", "cleared"),
            (b"\x00\xff", b"", "new"),
        ]
        inserts = [event for event in target.events if event.startswith("INSERT")]
        assert all(
            "CAST(:value_0 AS VARBINARY(MAX))" in event
            and "CAST(:value_1 AS VARBINARY(MAX))" in event
            for event in inserts
        )
        queries = [event for event in target.events if "UPDLOCK" in event]
        assert all("id = CAST(:value_0 AS VARBINARY(MAX))" in event for event in queries)
        updates = [event for event in target.events if event.startswith("UPDATE")]
        assert len(updates) == 1
        assert "payload = CAST(:value_1 AS VARBINARY(MAX))" in updates[0]
        assert "WHERE id = CAST(:value_0 AS VARBINARY(MAX))" in updates[0]


def test_boolean_source_values_bind_as_int_zero_and_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    columns = [_column("id", "int"), _column("enabled", "int")]
    with _target(
        monkeypatch,
        {"items": columns},
        ("CREATE TABLE items(id INTEGER PRIMARY KEY, enabled INTEGER)",),
    ) as target:
        block = _block(((1, False), (2, True)), columns=(("id", "INTEGER"), ("enabled", "BOOLEAN")))
        assert import_database(_options(tmp_path / "boolean.jsonl", block)).row_count == 2
        assert [
            tuple(row) for row in target.actual.exec_driver_sql("SELECT * FROM items ORDER BY id")
        ] == [(1, 0), (2, 1)]
        writes = [
            call.args[1]
            for call in target.connection.execute.call_args_list
            if str(call.args[0]).startswith("INSERT")
        ]
        assert [row["value_1"] for row in writes[0]] == [0, 1]
        assert all(type(row["value_1"]) is int for row in writes[0])


def test_missing_identity_and_generated_columns_use_target_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    columns = [
        _column("id", "int", is_identity=True, is_nullable=False),
        _column("label", "nvarchar", max_length=40),
        _column("version", "timestamp", is_nullable=False),
        _column("calculated", "int", is_computed=True, is_nullable=False),
        _column("defaulted", "int", is_nullable=False, default_object_id=123),
    ]
    ddl = (
        "CREATE TABLE items(id INTEGER PRIMARY KEY, label TEXT, version BLOB DEFAULT X'01', "
        "calculated INTEGER GENERATED ALWAYS AS (length(label)), "
        "defaulted INTEGER NOT NULL DEFAULT 7)",
    )
    with _target(monkeypatch, {"items": columns}, ddl) as target:
        block = _block((("value",),), columns=(("label", "TEXT"),), primary=())
        assert import_database(_options(tmp_path / "generated.jsonl", block)).row_count == 1
        assert target.actual.exec_driver_sql("SELECT * FROM items").first() == (
            1,
            "value",
            b"\x01",
            5,
            7,
        )
        target.connection.exec_driver_sql.assert_not_called()


@pytest.mark.parametrize(
    "column",
    [
        _column("extra", "timestamp"),
        _column("extra", "int", is_computed=True),
        _column("extra", "datetime2", scale=7),
        _column("extra", "time", scale=7),
        _column("extra", "datetimeoffset"),
    ],
)
def test_empty_table_preflight_rejects_provided_generated_or_unsupported_columns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, column: dict[str, object]
) -> None:
    with _target(
        monkeypatch,
        {"items": [_column("id", "int"), column]},
        ("CREATE TABLE items(id INTEGER PRIMARY KEY)",),
    ) as target:
        block = _block((), columns=(("id", "INTEGER"), ("extra", "TEXT")))
        with pytest.raises(DatabaseTransferError):
            import_database(_options(tmp_path / "empty.jsonl", block))
        assert "begin" not in target.events


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_failed_table_rolls_back_and_cleans_or_invalidates_identity_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cleanup_fails: bool
) -> None:
    columns = [_column("id", "int", is_identity=True), _column("label", "nvarchar", max_length=40)]
    with _target(
        monkeypatch, {"items": columns}, ("CREATE TABLE items(id INTEGER PRIMARY KEY, label TEXT)",)
    ) as target:
        original = target.connection.exec_driver_sql.side_effect

        def session_sql(statement: str) -> MagicMock:
            result: MagicMock = original(statement)
            if cleanup_fails and statement.endswith(" OFF"):
                raise OperationalError("cleanup", None, RuntimeError("cleanup failed"))
            return result

        target.connection.exec_driver_sql.side_effect = session_sql
        with pytest.raises(DatabaseTransferError, match="Mssql import failed") as caught:
            import_database(
                _options(tmp_path / "duplicate.jsonl", _block(((1, "first"), (1, "duplicate"))))
            )
        assert isinstance(caught.value.__cause__, sqlalchemy.exc.IntegrityError)
        assert target.actual.exec_driver_sql("SELECT COUNT(*) FROM items").scalar() == 0
        assert target.events[-2:] == (
            ["SET IDENTITY_INSERT dbo.items OFF", "invalidate"]
            if cleanup_fails
            else ["SET IDENTITY_INSERT dbo.items OFF", "rollback"]
        )
        assert sum(event.startswith("INSERT") for event in target.events) == 1
        assert "commit" not in target.events


def test_off_failure_rolls_back_and_invalidates_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    columns = [_column("id", "int", is_identity=True), _column("label", "nvarchar", max_length=40)]
    with _target(
        monkeypatch, {"items": columns}, ("CREATE TABLE items(id INTEGER PRIMARY KEY, label TEXT)",)
    ) as target:

        def session_sql(statement: str) -> MagicMock:
            target.events.append(statement)
            if statement.endswith(" OFF"):
                raise OperationalError("cleanup", None, RuntimeError("cleanup failed"))
            return MagicMock()

        target.connection.exec_driver_sql.side_effect = session_sql
        with pytest.raises(DatabaseTransferError):
            import_database(_options(tmp_path / "off.jsonl", _block(((1, "value"),))))
        assert target.actual.exec_driver_sql("SELECT COUNT(*) FROM items").scalar() == 0
        target.connection.invalidate.assert_called_once()


def test_prior_table_remains_committed_when_next_table_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    columns = [_column("id", "int"), _column("label", "nvarchar", max_length=40)]
    ddl = (
        "CREATE TABLE parent(id INTEGER PRIMARY KEY, label TEXT)",
        "CREATE TABLE child(id INTEGER PRIMARY KEY, label TEXT)",
    )
    with _target(monkeypatch, {"parent": columns, "child": columns}, ddl) as target:
        target.inspector.get_foreign_keys.side_effect = lambda name, **kwargs: (
            [{"referred_schema": "dbo", "referred_table": "parent"}] if name == "child" else []
        )
        options = _options(
            tmp_path / "tables.jsonl",
            _block(((1, "parent"),), name="parent"),
            _block(((1, "first"), (1, "duplicate")), name="child"),
        )
        with pytest.raises(DatabaseTransferError):
            import_database(options)
        assert target.actual.exec_driver_sql("SELECT COUNT(*) FROM parent").scalar() == 1
        assert target.actual.exec_driver_sql("SELECT COUNT(*) FROM child").scalar() == 0


def test_cross_schema_dependency_fails_before_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _target(
        monkeypatch,
        {"items": [_column("id", "int"), _column("label", "nvarchar", max_length=40)]},
        ("CREATE TABLE items(id INTEGER PRIMARY KEY, label TEXT)",),
    ) as target:
        target.inspector.get_foreign_keys.return_value = [
            {"referred_table": "items", "referred_schema": "other"}
        ]
        with pytest.raises(DatabaseTransferError, match="cross-schema"):
            import_database(_options(tmp_path / "fk.jsonl", _block(((1, "value"),))))
        assert "begin" not in target.events


@pytest.mark.parametrize("missing_key", [False, True])
def test_upsert_requires_all_non_null_primary_key_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing_key: bool
) -> None:
    columns = [_column("id", "int", is_identity=True), _column("label", "nvarchar", max_length=40)]
    with _target(
        monkeypatch, {"items": columns}, ("CREATE TABLE items(id INTEGER PRIMARY KEY, label TEXT)",)
    ) as target:
        block = (
            _block((("value",),), columns=(("label", "TEXT"),), primary=())
            if missing_key
            else _block(((None, "value"),))
        )
        with pytest.raises(DatabaseTransferError, match="primary key"):
            import_database(_options(tmp_path / "key.jsonl", block, mode="upsert"))
        assert "begin" not in target.events


@pytest.mark.parametrize("source_type", ["DATETIME2(7)", "DATETIMEOFFSET(6)"])
def test_unsupported_source_temporal_metadata_fails_for_empty_tables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source_type: str
) -> None:
    columns = [_column("id", "int"), _column("created", "datetime2", scale=6)]
    with _target(
        monkeypatch,
        {"items": columns},
        ("CREATE TABLE items(id INTEGER PRIMARY KEY, created TEXT)",),
    ) as target:
        block = _block((), columns=(("id", "INTEGER"), ("created", source_type)))
        with pytest.raises(DatabaseTransferError, match="source column"):
            import_database(_options(tmp_path / "source-type.jsonl", block))
        assert "begin" not in target.events


_BASE_COLUMN = ColumnMetadata("value", "int", 4, 10, 0, True, False, False, False, None, False)


@pytest.mark.parametrize(
    ("column", "raw", "expected"),
    [
        (replace(_BASE_COLUMN, type_name="tinyint"), 255, 255),
        (replace(_BASE_COLUMN, type_name="bigint"), 9223372036854775807, 9223372036854775807),
        (
            replace(_BASE_COLUMN, type_name="decimal", precision=38, scale=18),
            {"$type": "decimal", "value": "12345678901234567890.123456789012345678"},
            Decimal("12345678901234567890.123456789012345678"),
        ),
        (
            replace(_BASE_COLUMN, type_name="decimal", precision=5, scale=2),
            {"$type": "decimal", "value": "1.23000"},
            Decimal("1.23000"),
        ),
        (
            replace(_BASE_COLUMN, type_name="uniqueidentifier"),
            "12345678-1234-5678-9abc-123456789abc",
            UUID("12345678-1234-5678-9abc-123456789abc"),
        ),
        (replace(_BASE_COLUMN, type_name="bit"), False, False),
        (replace(_BASE_COLUMN, type_name="nvarchar", max_length=4), "🙂", "🙂"),
        (replace(_BASE_COLUMN, type_name="varchar", max_length=1, code_page=1252), "é", "é"),
        (replace(_BASE_COLUMN, type_name="varchar", max_length=4, code_page=65001), "🙂", "🙂"),
        (
            replace(_BASE_COLUMN, type_name="varbinary", max_length=2),
            {"$type": "blob", "base64": "AP8="},
            b"\x00\xff",
        ),
        (
            replace(_BASE_COLUMN, type_name="datetime2", scale=6),
            "2026-01-02T03:04:05.123456Z",
            "2026-01-02T03:04:05.123456",
        ),
        (replace(_BASE_COLUMN, type_name="time", scale=6), "12:34:56.123456", "12:34:56.123456"),
        (
            replace(_BASE_COLUMN, type_name="datetime"),
            "2026-01-02T03:04:05Z",
            "2026-01-02T03:04:05.000",
        ),
        (
            replace(_BASE_COLUMN, type_name="datetime"),
            "2026-01-02T03:04:05.003000Z",
            "2026-01-02T03:04:05.003",
        ),
        (
            replace(_BASE_COLUMN, type_name="datetime"),
            "2026-01-02T03:04:05.007000Z",
            "2026-01-02T03:04:05.007",
        ),
        (
            replace(_BASE_COLUMN, type_name="datetime"),
            "2026-01-02T03:04:05.123000Z",
            "2026-01-02T03:04:05.123",
        ),
        (
            replace(_BASE_COLUMN, type_name="smalldatetime"),
            "2026-01-02T03:04:00Z",
            "2026-01-02T03:04:00",
        ),
    ],
)
def test_target_values_preserve_supported_precision_and_encodings(
    column: ColumnMetadata, raw: JSONValue, expected: object
) -> None:
    assert decode_mssql_value(raw, column, UTC) == expected


@pytest.mark.parametrize(
    ("column", "raw"),
    [
        (replace(_BASE_COLUMN, type_name="tinyint"), -1),
        (_BASE_COLUMN, 2147483648),
        (_BASE_COLUMN, 1.5),
        (
            replace(_BASE_COLUMN, type_name="decimal", precision=5, scale=2),
            {"$type": "decimal", "value": "1000.00"},
        ),
        (
            replace(_BASE_COLUMN, type_name="decimal", precision=5, scale=2),
            {"$type": "decimal", "value": "1.001"},
        ),
        (replace(_BASE_COLUMN, type_name="decimal", precision=5, scale=2), 0.1),
        (replace(_BASE_COLUMN, type_name="uniqueidentifier"), "not-a-uuid"),
        (replace(_BASE_COLUMN, type_name="nvarchar", max_length=2), "🙂"),
        (replace(_BASE_COLUMN, type_name="varchar", max_length=20, code_page=1252), "中文"),
        (
            replace(_BASE_COLUMN, type_name="varbinary", max_length=1),
            {"$type": "blob", "base64": "AP8="},
        ),
        (
            replace(_BASE_COLUMN, type_name="binary", max_length=4),
            {"$type": "blob", "base64": "AP8="},
        ),
        (replace(_BASE_COLUMN, type_name="datetime2", scale=3), "2026-01-02T03:04:05.123456Z"),
        (replace(_BASE_COLUMN, type_name="time", scale=3), "12:34:56.123456"),
        (replace(_BASE_COLUMN, type_name="datetime"), "1700-01-02T03:04:05Z"),
        (replace(_BASE_COLUMN, type_name="datetime"), "2026-01-02T03:04:05.003333Z"),
        (replace(_BASE_COLUMN, type_name="datetime"), "2026-01-02T03:04:05.006666Z"),
        (replace(_BASE_COLUMN, type_name="datetime"), "2026-01-02T03:04:05.006000Z"),
        (replace(_BASE_COLUMN, type_name="smalldatetime"), "2026-01-02T03:04:30Z"),
        (replace(_BASE_COLUMN, nullable=False), None),
    ],
)
def test_target_values_reject_actual_loss_boundaries(
    column: ColumnMetadata, raw: JSONValue
) -> None:
    with pytest.raises(DatabaseTransferError):
        decode_mssql_value(raw, column, UTC)


def test_missing_driver_is_a_controlled_transfer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = MagicMock(side_effect=ModuleNotFoundError("pymssql"))
    monkeypatch.setattr(sqlalchemy, "create_engine", factory)
    with pytest.raises(DatabaseTransferError, match="engine could not be created"):
        sqlalchemy_transfer._create_engine(parse_dsn(_SOURCE.dsn or ""))
