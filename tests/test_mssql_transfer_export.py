from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from dbtalk.database import sqlalchemy_transfer
from dbtalk.database.format import decode_value, read_document, scan_document, type_family
from dbtalk.database.models import (
    DatabaseTransferError,
    ExportOptions,
    ImportOptions,
    TransferConnection,
    TransferMode,
)
from dbtalk.database.schema import order_table_names, validate_selected_foreign_keys
from dbtalk.database.transfer import export_database, import_database, validate_connection
from tests.sqlite_helpers import MemoryDatabase

_SOURCE = TransferConnection("mssql", dsn="mssql+pymssql://example.invalid/sample")


def _column(name: str, type_name: str, **attributes: object) -> dict[str, object]:
    return {
        "name": name,
        "type_name": type_name,
        "max_length": 8,
        "precision": 18,
        "scale": 0,
        "is_nullable": True,
        "is_identity": False,
        "is_computed": False,
        "generated_always_type": 0,
        "collation_name": None,
        "is_user_defined": False,
        "default_object_id": 0,
        "code_page": None,
        **attributes,
    }


def _source(
    monkeypatch: pytest.MonkeyPatch,
    tables: dict[str, list[dict[str, object]]],
    *,
    schema: str = "dbo",
) -> tuple[MagicMock, MagicMock, MagicMock]:
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.dialect = create_engine("mssql+pymssql://").dialect
    inspector = MagicMock()
    inspector.default_schema_name = schema
    inspector.get_table_names.return_value = list(tables)
    inspector.get_pk_constraint.return_value = {"constrained_columns": ["id"]}
    inspector.get_foreign_keys.return_value = []
    monkeypatch.setattr(sqlalchemy_transfer, "inspect", lambda connection: inspector)

    def catalog(statement: Any, parameters: dict[str, str]) -> MagicMock:
        assert "sys.columns" in str(statement)
        assert parameters["schema_name"] == schema
        result = MagicMock()
        result.__enter__.return_value = result
        result.mappings.return_value = iter(tables[parameters["table_name"]])
        return result

    connection.execute.side_effect = catalog
    engine = MagicMock()
    engine.connect.return_value = connection
    monkeypatch.setattr(sqlalchemy_transfer, "_create_engine", lambda parsed: engine)
    return connection, inspector, engine


def _rows(connection: MagicMock, rows: list[tuple[object, ...]]) -> MagicMock:
    result = MagicMock()
    result.__enter__.return_value = result
    result.fetchmany.side_effect = [
        rows[index : index + 1000] for index in range(0, len(rows), 1000)
    ] + [[]]
    connection.exec_driver_sql.return_value = result
    return result


@pytest.mark.parametrize("suffix", [".jsonl", ".jsonl.gz"])
def test_export_uses_catalog_precision_and_preserves_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, suffix: str
) -> None:
    identifier = UUID("12345678-1234-5678-9abc-123456789abc")
    columns = [
        _column("id", "bigint", is_identity=True),
        _column("amount", "decimal", precision=38, scale=18),
        _column("title", "nvarchar", max_length=-1),
        _column("payload", "varbinary", max_length=-1),
        _column("identifier", "uniqueidentifier"),
        _column("enabled", "bit"),
        _column("created", "datetime2", scale=6),
        _column("clock", "time", scale=3),
        _column("birthday", "date"),
        _column("optional", "varchar", max_length=20),
    ]
    connection, inspector, engine = _source(monkeypatch, {"items": columns}, schema="report]s")
    result = _rows(
        connection,
        [
            (
                9223372036854775807,
                Decimal("12345678901234567890.123456789012345678"),
                "中文🙂",
                b"\x00\xffabc",
                identifier,
                True,
                datetime(2026, 11, 2, 10, 20, 30, 123456),
                time(10, 20, 30, 123000),
                date(2026, 11, 2),
                None,
            )
        ],
    )
    output = tmp_path / f"export{suffix}"

    summary = export_database(ExportOptions(_SOURCE, output, UTC))

    document = read_document(output)
    assert summary.table_count == 1 and summary.row_count == 1
    assert document.header.source == "mssql"
    assert [column.declared_type for column in document.tables[0].header.columns] == [
        "BIGINT",
        "DECIMAL(38,18)",
        "NVARCHAR(MAX)",
        "VARBINARY(MAX)",
        "UNIQUEIDENTIFIER",
        "BOOLEAN",
        "DATETIME2(6)",
        "TIME(3)",
        "DATE",
        "VARCHAR(20)",
    ]
    assert document.tables[0].rows[0] == (
        9223372036854775807,
        {"$type": "decimal", "value": "12345678901234567890.123456789012345678"},
        "中文🙂",
        {"$type": "blob", "base64": "AP9hYmM="},
        str(identifier),
        True,
        "2026-11-02T10:20:30.123456Z",
        "10:20:30.123000",
        "2026-11-02",
        None,
    )
    assert "FROM [report]]s].items" in connection.exec_driver_sql.call_args.args[0]
    assert connection.exec_driver_sql.call_args.kwargs["execution_options"] == {}
    inspector.get_columns.assert_not_called()
    inspector.get_table_names.assert_called_once_with(schema="report]s")
    result.fetchall.assert_not_called()
    engine.dispose.assert_called_once()
    connection.rollback.assert_called_once()


@pytest.mark.parametrize(("included", "excluded"), [(("items",), ()), ((), ("Books",))])
def test_selected_tables_only_validate_their_columns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    included: tuple[str, ...],
    excluded: tuple[str, ...],
) -> None:
    connection, _, _ = _source(
        monkeypatch,
        {
            "Books": [_column("id", "int"), _column("created", "datetime2", scale=7)],
            "items": [_column("id", "int")],
        },
    )
    _rows(connection, [(1,)])
    options = ExportOptions(
        _SOURCE,
        tmp_path / "export.jsonl",
        UTC,
        include_tables=included,
        exclude_tables=excluded,
    )
    assert export_database(options).row_count == 1
    assert connection.execute.call_count == 1
    assert connection.execute.call_args.args[1]["table_name"] == "items"


def test_export_retains_composite_primary_key_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection, inspector, _ = _source(
        monkeypatch, {"items": [_column("id", "int"), _column("tenant_id", "int")]}
    )
    inspector.get_pk_constraint.return_value = {"constrained_columns": ["tenant_id", "id"]}
    _rows(connection, [(1, 2)])
    output = tmp_path / "export.jsonl"
    export_database(ExportOptions(_SOURCE, output, UTC))
    assert read_document(output).tables[0].header.primary_key == ("tenant_id", "id")


@pytest.mark.parametrize(
    "column",
    [
        _column("created", "datetime2", scale=7),
        _column("clock", "time", scale=7),
        _column("offset", "datetimeoffset", scale=6),
        _column("version", "timestamp"),
        _column("version", "rowversion"),
        _column("calculated", "int", is_computed=True),
        _column("period", "datetime2", scale=6, generated_always_type=1),
        _column("document", "xml"),
        _column("custom", "custom_type", is_user_defined=True),
    ],
)
def test_unsupported_selected_columns_fail_before_reading_rows_or_replacing_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, column: dict[str, object]
) -> None:
    connection, _, engine = _source(monkeypatch, {"items": [_column("id", "int"), column]})
    output = tmp_path / "export.jsonl"
    output.write_text("existing artifact", encoding="utf-8")
    with pytest.raises(DatabaseTransferError, match="column"):
        export_database(ExportOptions(_SOURCE, output, UTC))
    connection.exec_driver_sql.assert_not_called()
    assert output.read_text(encoding="utf-8") == "existing artifact"
    assert not output.with_suffix(".jsonl.tmp").exists()
    engine.dispose.assert_called_once()
    assert type_family("TIMESTAMP") == "datetime"


@pytest.mark.parametrize(
    ("type_name", "attributes", "expected"),
    [
        ("tinyint", {}, "TINYINT"),
        ("smallint", {}, "SMALLINT"),
        ("int", {}, "INT"),
        ("bigint", {}, "BIGINT"),
        ("numeric", {"precision": 25, "scale": 8}, "NUMERIC(25,8)"),
        ("char", {"max_length": 10}, "CHAR(10)"),
        ("varchar", {"max_length": -1}, "VARCHAR(MAX)"),
        ("nchar", {"max_length": 20}, "NCHAR(10)"),
        ("nvarchar", {"max_length": 20}, "NVARCHAR(10)"),
        ("binary", {"max_length": 16}, "BINARY(16)"),
        ("varbinary", {"max_length": 256}, "VARBINARY(256)"),
        ("smalldatetime", {}, "SMALLDATETIME"),
        ("datetime", {}, "DATETIME"),
        ("datetime2", {"scale": 0}, "DATETIME2(0)"),
        ("time", {"scale": 6}, "TIME(6)"),
    ],
)
def test_catalog_declarations_retain_type_dimensions(
    monkeypatch: pytest.MonkeyPatch, type_name: str, attributes: dict[str, object], expected: str
) -> None:
    connection, _, _ = _source(monkeypatch, {"items": [_column("id", type_name, **attributes)]})
    schemas = sqlalchemy_transfer._load_schemas(connection)
    assert schemas["items"].columns[0].declared_type == expected


def test_foreign_keys_order_tables_and_reject_cross_schema_namesakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection, inspector, _ = _source(
        monkeypatch,
        {
            "child": [_column("id", "int")],
            "parent": [_column("id", "int")],
        },
    )
    inspector.get_foreign_keys.side_effect = lambda name, **kwargs: (
        [{"referred_table": "parent", "referred_schema": "dbo"}] if name == "child" else []
    )
    schemas = sqlalchemy_transfer._load_schemas(connection)
    assert order_table_names(tuple(schemas), schemas) == ("parent", "child")
    with pytest.raises(DatabaseTransferError, match="unselected foreign-key"):
        validate_selected_foreign_keys(sqlalchemy_transfer._load_schemas(connection, ("child",)))
    inspector.get_foreign_keys.side_effect = lambda name, **kwargs: (
        [{"referred_table": "parent", "referred_schema": "other"}] if name == "child" else []
    )
    with pytest.raises(DatabaseTransferError, match="cross-schema"):
        sqlalchemy_transfer._load_schemas(connection)


def test_export_reads_all_batches_and_artifact_imports_into_sqlite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sqlite_database: Callable[[], MemoryDatabase]
) -> None:
    connection, _, _ = _source(monkeypatch, {"items": [_column("id", "int")]})
    result = _rows(connection, [(index,) for index in range(2005)])
    output = tmp_path / "export.jsonl"
    assert export_database(ExportOptions(_SOURCE, output, UTC)).row_count == 2005
    assert scan_document(output).tables[0].row_count == 2005
    assert result.fetchmany.call_count == 4
    assert all(call.args == (1000,) for call in result.fetchmany.call_args_list)
    result.fetchall.assert_not_called()
    monkeypatch.undo()
    database = sqlite_database()
    with database.connect() as target:
        target.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
    summary = import_database(
        ImportOptions(TransferConnection("sqlite", dsn=database.dsn), output, "insert", UTC)
    )
    assert summary.row_count == 2005
    with database.connect() as target:
        assert target.execute("SELECT COUNT(*), MIN(id), MAX(id) FROM items").fetchone() == (
            2005,
            0,
            2004,
        )


@pytest.mark.parametrize("mode", ["insert", "upsert"])
def test_representative_mssql_values_import_into_sqlite_types(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sqlite_database: Callable[[], MemoryDatabase],
    mode: TransferMode,
) -> None:
    identifier = UUID("12345678-1234-5678-9abc-123456789abc")
    output = tmp_path / "representative.jsonl"
    columns = [
        _column("id", "int", is_identity=True),
        _column("uid", "uniqueidentifier"),
        _column("active", "bit"),
        _column("created", "datetime2", scale=6),
        _column("payload", "varbinary", max_length=-1),
        _column("amount", "decimal", precision=12, scale=3),
        _column("title", "nvarchar", max_length=100),
    ]
    with monkeypatch.context() as source_patch:
        connection, _, _ = _source(source_patch, {"items": columns})
        _rows(
            connection,
            [
                (
                    1,
                    identifier,
                    True,
                    datetime(2026, 11, 2, 10, 20, 30, 123456),
                    b"\x00\xffabc",
                    Decimal("123.125"),
                    "中文🙂",
                ),
                (
                    2,
                    identifier,
                    False,
                    datetime(2026, 11, 2, 10, 20, 30),
                    b"",
                    Decimal("-0.125"),
                    "",
                ),
                (3, None, None, None, None, None, None),
            ],
        )
        assert export_database(ExportOptions(_SOURCE, output, UTC)).row_count == 3
    document = read_document(output)
    assert document.tables[0].header.columns[2].declared_type == "BOOLEAN"
    assert document.tables[0].rows[0][5] == {"$type": "decimal", "value": "123.125"}
    database = sqlite_database()
    with database.connect() as target:
        target.execute(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, uid TEXT, active BOOLEAN, "
            "created DATETIME(6), payload BLOB, amount DECIMAL(12,3), title TEXT)"
        )
        if mode == "upsert":
            target.execute("INSERT INTO items (id, title) VALUES (1, 'old')")
    imported = import_database(
        ImportOptions(TransferConnection("sqlite", dsn=database.dsn), output, mode, UTC)
    )
    assert imported.row_count == 3
    with database.connect() as target:
        rows = target.execute(
            "SELECT id, uid, active, created, payload, amount, title FROM items ORDER BY id"
        ).fetchall()
    assert rows == [
        (1, str(identifier), 1, "2026-11-02 10:20:30.123456", b"\x00\xffabc", 123.125, "中文🙂"),
        (2, str(identifier), 0, "2026-11-02 10:20:30", b"", -0.125, ""),
        (3, None, None, None, None, None, None),
    ]


@pytest.mark.parametrize(("declared", "value"), [("BIT", "1"), ("BIT(8)", "10100011")])
def test_non_mssql_bit_declarations_keep_bit_string_semantics(declared: str, value: str) -> None:
    assert type_family(declared) == "unknown"
    assert decode_value(value, declared, UTC) == value


def test_driver_failure_during_export_preserves_previous_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection, _, engine = _source(monkeypatch, {"items": [_column("id", "int")]})
    result = _rows(connection, [(1,)])
    result.fetchmany.side_effect = [
        [(1,)],
        OperationalError("SELECT", None, RuntimeError("failed")),
    ]
    output = tmp_path / "export.jsonl"
    output.write_text("existing", encoding="utf-8")
    with pytest.raises(DatabaseTransferError, match="Mssql export failed"):
        export_database(ExportOptions(_SOURCE, output, UTC))
    assert output.read_text(encoding="utf-8") == "existing"
    assert not output.with_suffix(".jsonl.tmp").exists()
    engine.dispose.assert_called_once()


def test_missing_database_fails_before_connecting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine_factory = MagicMock()
    monkeypatch.setattr(sqlalchemy_transfer, "_create_engine", engine_factory)
    missing_database = TransferConnection("mssql", dsn="mssql+pymssql://example.invalid")
    with pytest.raises(DatabaseTransferError, match="database name"):
        export_database(ExportOptions(missing_database, tmp_path / "export.jsonl", UTC))
    validate_connection(_SOURCE)
    engine_factory.assert_not_called()
