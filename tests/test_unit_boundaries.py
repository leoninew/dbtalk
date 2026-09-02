"""Unit coverage for boundary conditions without external database services."""

from __future__ import annotations

import gzip
import io
import subprocess
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import click
import pytest

import dbtalk.database.cli as database_cli
import dbtalk.database.format as transfer_format
import dbtalk.database.mysql as mysql_transfer
import dbtalk.database.schema as schema
import dbtalk.mysql.client as mysql_client
import dbtalk.settings as dbtalk_settings
from dbtalk.context import DbtalkContext
from dbtalk.database.models import (
    ColumnDefinition,
    DatabaseTransferError,
    TableBlock,
    TableBlockHeader,
    TableSchema,
    TransferDocument,
    TransferHeader,
)


def test_format_round_trips_declared_values_and_datetime_variants() -> None:
    assert transfer_format.encode_value(Decimal("12.340"), "DECIMAL(10, 3)", UTC) == {
        "$type": "decimal",
        "value": "12.340",
    }
    assert transfer_format.encode_value(b"\x00\xff", "BLOB", UTC) == {
        "$type": "blob",
        "base64": "AP8=",
    }
    assert transfer_format.encode_value(date(2026, 8, 20), "DATE", UTC) == "2026-08-20"
    assert transfer_format.encode_value(time(1, 2, 3), "TIME", UTC) == "01:02:03"
    assert (
        transfer_format.encode_value(datetime(2026, 8, 20, 8, 0, 1, 123456), "DATETIME(6)", UTC)
        == "2026-08-20T08:00:01.123456Z"
    )
    assert transfer_format.decode_value(
        {"$type": "decimal", "value": "12.340"}, "DECIMAL", UTC
    ) == Decimal("12.340")
    assert (
        transfer_format.decode_value({"$type": "blob", "base64": "AP8="}, "BLOB", UTC)
        == b"\x00\xff"
    )
    assert (
        transfer_format.normalize_datetime("2026-08-20 08:00:01.1234567 +0800 CST", UTC)
        == "2026-08-20T00:00:01.1234567Z"
    )
    assert (
        transfer_format.format_datetime_for_database(
            "2026-08-20T08:00:01.123456Z", UTC, datetime_precision=3
        )
        == "2026-08-20 08:00:01.123"
    )
    assert transfer_format.type_family("UUID") == "unknown"
    assert transfer_format.type_family("BOOLEAN") == "boolean"


def test_format_rejects_invalid_declared_values() -> None:
    with pytest.raises(DatabaseTransferError, match="non-finite"):
        transfer_format.encode_value(float("nan"), "REAL", UTC)
    with pytest.raises(DatabaseTransferError, match="unsupported type"):
        transfer_format.encode_value(object(), "TEXT", UTC)
    with pytest.raises(DatabaseTransferError, match="BLOB column"):
        transfer_format.encode_value("text", "BLOB", UTC)
    with pytest.raises(DatabaseTransferError, match="DATE value"):
        transfer_format.decode_value("not-a-date", "DATE", UTC)
    with pytest.raises(DatabaseTransferError, match="TIME value"):
        transfer_format.decode_value("not-a-time", "TIME", UTC)
    with pytest.raises(DatabaseTransferError, match="datetime value"):
        transfer_format.normalize_datetime("not-a-datetime", UTC)


def test_jsonl_streaming_document_round_trip_and_validation(tmp_path: Path) -> None:
    document = _document()
    output = tmp_path / "nested" / "transfer.jsonl.gz"

    transfer_format.write_document(output, document)

    assert transfer_format.read_document(output) == document
    preview = transfer_format.scan_document(output)
    assert preview.tables[0].row_count == 1
    tables = transfer_format.iter_document_tables(output)
    header, table_header, rows = next(tables)
    assert header == document.header
    assert list(rows) == list(document.tables[0].rows)
    assert table_header == document.tables[0].header
    with pytest.raises(StopIteration):
        next(tables)

    with pytest.raises(DatabaseTransferError, match="already finished"):
        stream = io.StringIO()
        writer = transfer_format.JsonlStreamWriter(stream, document.header)
        writer.finish()
        writer.write_table(document.tables[0].header, iter(document.tables[0].rows))

    with pytest.raises(DatabaseTransferError, match="first JSONL record"):
        transfer_format.read_jsonl(io.StringIO('{"kind":"table"}\n'))
    with pytest.raises(DatabaseTransferError, match="outside a table block"):
        transfer_format.read_jsonl(
            io.StringIO(
                '{"kind":"header","format":"dbtalk.database-transfer/v1","source":"sqlite"}\n'
                '{"kind":"row","values":[]}\n'
            )
        )
    with pytest.raises(DatabaseTransferError, match="invalid BLOB"):
        transfer_format.read_jsonl(
            io.StringIO(
                '{"kind":"header","format":"dbtalk.database-transfer/v1","source":"sqlite"}\n'
                '{"kind":"table","name":"items","columns":[{"name":"data",'
                '"declared_type":"BLOB"}],"primary_key":[]}\n'
                '{"kind":"row","values":[{"$type":"blob","base64":"!"}]}\n'
                '{"kind":"end","rows":1}\n'
            )
        )


def test_jsonl_io_and_streaming_writer_cleanup_paths(tmp_path: Path) -> None:
    document = _document()
    output = tmp_path / "transfer.jsonl"

    assert transfer_format.gzip_output_path(tmp_path / "transfer.jsonl.gz") == (
        tmp_path / "transfer.jsonl.gz"
    )
    transfer_format.write_document(output, document)
    assert transfer_format.read_document(output) == document
    with pytest.raises(DatabaseTransferError, match="could not read"):
        transfer_format.read_document(tmp_path / "missing.jsonl")

    stream = io.StringIO()
    writer = transfer_format.JsonlStreamWriter(stream, document.header)
    writer.write_header()
    assert writer.write_table(document.tables[0].header, iter(document.tables[0].rows)) == 1
    assert writer.table_count == 1
    assert writer.row_count == 1
    with pytest.raises(DatabaseTransferError, match="duplicate"):
        writer.write_table(document.tables[0].header, iter(document.tables[0].rows))

    with (
        pytest.raises(DatabaseTransferError, match="transfer header"),
        transfer_format.open_document_writer(
            tmp_path / "invalid.jsonl", TransferHeader("invalid", "sqlite")
        ),
    ):
        pass
    with (
        pytest.raises(ValueError, match="interrupted"),
        transfer_format.open_document_writer(tmp_path / "interrupted.jsonl", document.header),
    ):
        raise ValueError("interrupted")
    assert not (tmp_path / "interrupted.jsonl").exists()


def test_jsonl_rejects_malformed_records_and_streaming_blocks(tmp_path: Path) -> None:
    header = '{"kind":"header","format":"dbtalk.database-transfer/v1","source":"sqlite"}\n'
    table = (
        '{"kind":"table","name":"items","columns":[{"name":"id",'
        '"declared_type":"INTEGER"}],"primary_key":["id"]}\n'
    )
    error_cases = (
        ("\n", "must not be blank"),
        ("[]\n", "must be an object"),
        ('{"kind":"header","format":"invalid","source":"sqlite"}\n', "header"),
        (header + '{"kind":"table","name":"items","columns":[],"primary_key":[]}\n', "no columns"),
        (
            header + '{"kind":"table","name":"items","columns":[{"name":"id",'
            '"declared_type":"INTEGER"},{"name":"id","declared_type":"INTEGER"}],'
            '"primary_key":[]}\n',
            "duplicate columns",
        ),
        (header + table + '{"kind":"row","values":"not-a-list"}\n', "values array"),
        (header + table + '{"kind":"row","values":[1]}\n', "missing its end"),
        (header + table + '{"kind":"end","rows":true}\n', "invalid row count"),
    )
    for content, message in error_cases:
        with pytest.raises(DatabaseTransferError, match=message):
            transfer_format.read_jsonl(io.StringIO(content))

    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text(header + table + '{"kind":"bad"}\n', encoding="utf-8")
    with pytest.raises(DatabaseTransferError, match="invalid table block"):
        transfer_format.scan_document(invalid)
    tables = transfer_format.iter_document_tables(invalid)
    _, _, rows = next(tables)
    with pytest.raises(DatabaseTransferError, match="invalid table block"):
        list(rows)


def test_mysql_client_file_and_command_helpers(tmp_path: Path) -> None:
    source = tmp_path / "source.sql"
    source.write_text("SELECT 1;", encoding="utf-8")
    compressed = mysql_client.gzip_output_path(tmp_path / "backup.sql")
    mysql_client.write_gzip(source, compressed)
    with gzip.open(compressed, "rt", encoding="utf-8") as stream:
        assert stream.read() == "SELECT 1;"
    with mysql_client.unpack_gzip_input(compressed, ".sql") as restored:
        assert restored.read_text(encoding="utf-8") == "SELECT 1;"
    with mysql_client.unpack_gzip_input(source, ".sql") as unchanged:
        assert unchanged == source
    with pytest.raises(click.ClickException, match="does not exist"):
        mysql_client.write_gzip(tmp_path / "missing.sql", compressed)
    with (
        pytest.raises(click.ClickException, match="must contain"),
        mysql_client.unpack_gzip_input(tmp_path / "backup.jsonl.gz", ".sql"),
    ):
        pass
    corrupted = tmp_path / "corrupted.sql.gz"
    corrupted.write_text("not gzip", encoding="utf-8")
    with (
        pytest.raises(click.ClickException, match="could not read gzip"),
        mysql_client.unpack_gzip_input(corrupted, ".sql"),
    ):
        pass

    assert mysql_client.mysql_connection_args("localhost", 3306, "root") == ["-u", "root"]
    assert mysql_client.mysql_connection_args("db.example", 3307, "admin") == [
        "-h",
        "db.example",
        "-P",
        "3307",
        "-u",
        "admin",
    ]
    assert mysql_client.mysql_client_args("mysql", "127.0.0.1", 3306, "root", "secret") == [
        "env",
        "MYSQL_PWD=secret",
        "mysql",
        "-u",
        "root",
    ]
    assert mysql_client.docker_database_host("localhost") == "host.docker.internal"
    assert mysql_client.docker_database_host("db.example") == "db.example"
    assert mysql_client.docker_host_gateway_args("db.example") == []
    with patch("dbtalk.mysql.client.platform.system", return_value="Linux"):
        assert mysql_client.docker_host_gateway_args("localhost") == [
            "--add-host",
            "host.docker.internal:host-gateway",
        ]

    success = subprocess.CompletedProcess([], 0, "", "")
    with patch("dbtalk.mysql.client.subprocess.run", return_value=success) as run:
        assert mysql_client.run_command(["mysql"], {"MYSQL_PWD": "secret"}) == success
        assert mysql_client.run_command(["mysql"], input_path=source) == success
    assert run.call_count == 2
    with (
        patch("dbtalk.mysql.client.subprocess.run", side_effect=OSError("missing")),
        pytest.raises(click.ClickException, match="Could not run mysql"),
    ):
        mysql_client.run_command(["mysql"])
    mysql_client.ensure_command_succeeded(success, "mysql")
    with pytest.raises(click.ClickException, match="details"):
        mysql_client.ensure_command_succeeded(
            subprocess.CompletedProcess([], 1, "", "details"), "mysql"
        )
    with pytest.raises(click.ClickException, match="exit code 2"):
        mysql_client.ensure_command_succeeded(subprocess.CompletedProcess([], 2, "", ""), "mysql")


def test_mysql_client_docker_image_check_and_cleanup() -> None:
    image = "registry.example/mysql:8.0.39"
    with patch("dbtalk.mysql.client.shutil.which", return_value=None):
        assert mysql_client.docker_mysql_image(image) == (
            None,
            "Docker is not installed or is not on PATH.",
        )
    with (
        patch("dbtalk.mysql.client.shutil.which", return_value="docker"),
        patch("dbtalk.mysql.client.subprocess.run", side_effect=OSError),
    ):
        assert mysql_client.docker_mysql_image(image) == (None, "Docker could not be started.")
    with (
        patch("dbtalk.mysql.client.shutil.which", return_value="docker"),
        patch(
            "dbtalk.mysql.client.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "", ""),
        ),
    ):
        assert mysql_client.docker_mysql_image(image) == (
            None,
            f"Configured MySQL Docker image is not available locally: {image}.",
        )
    with (
        patch("dbtalk.mysql.client.shutil.which", return_value="docker"),
        patch(
            "dbtalk.mysql.client.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ),
    ):
        assert mysql_client.docker_mysql_image(image) == (image, "")
    with (
        patch("dbtalk.mysql.client.shutil.which", return_value="docker"),
        patch(
            "dbtalk.mysql.client.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "", ""),
        ),
    ):
        assert mysql_client.docker_mysql_image(image) == (
            None,
            f"Configured MySQL Docker image is not available locally: {image}.",
        )
    with patch("dbtalk.mysql.client.subprocess.run") as remove:
        mysql_client.remove_temporary_container("dbtalk-test", {"MYSQL_PWD": "secret"})
    remove.assert_called_once()


def test_schema_selection_order_validation_and_value_conversion() -> None:
    parent_header = _header("parent")
    child_header = _header("child")
    parent = _schema("parent")
    child = _schema("child", foreign_keys=("parent",))
    schemas = {"child": child, "parent": parent}
    parent_block = TableBlock(parent_header, ((1, "Ada"),))
    child_block = TableBlock(child_header, ((2, "Grace"),))

    schema.validate_target_table(parent_header, parent, "insert")
    assert schema.select_table_names(schemas, (), ("child",), "source") == ("parent",)
    assert schema.select_table_schemas(schemas, ("parent",), ()) == {"parent": parent}
    assert schema.order_table_names(("child", "parent"), schemas) == ("parent", "child")
    assert schema.order_table_blocks((child_block, parent_block), schemas) == (
        parent_block,
        child_block,
    )
    assert schema.table_block_values(parent_block, parent, UTC, None) == ((1, "Ada"),)
    assert schema.compatible_types("INTEGER", "DECIMAL")
    assert not schema.compatible_types("DATE", "TEXT")

    with pytest.raises(DatabaseTransferError, match="missing column"):
        schema.validate_target_table(
            TableBlockHeader("parent", (ColumnDefinition("missing", "TEXT"),), ()), parent, "insert"
        )
    with pytest.raises(DatabaseTransferError, match="primary key"):
        schema.validate_target_table(
            TableBlockHeader("parent", parent_header.columns, ()), parent, "insert"
        )
    with pytest.raises(DatabaseTransferError, match="NULL primary key"):
        schema.validate_import_rows(TableBlock(parent_header, ((None, "Ada"),)), "upsert")
    with pytest.raises(DatabaseTransferError, match="do not exist"):
        schema.select_table_names(schemas, ("missing",), (), "source")
    with pytest.raises(DatabaseTransferError, match="selected table set is empty"):
        schema.select_table_names(schemas, ("parent",), ("parent",), "source")
    with pytest.raises(DatabaseTransferError, match="unselected"):
        schema.select_table_schemas(schemas, ("child",), ())
    with pytest.raises(DatabaseTransferError, match="cycle"):
        schema.order_table_names(
            ("left", "right"),
            {
                "left": _schema("left", foreign_keys=("right",)),
                "right": _schema("right", foreign_keys=("left",)),
            },
        )


def test_database_cli_option_guards_and_settings_validation() -> None:
    with pytest.raises(click.BadParameter, match="unknown IANA timezone"):
        database_cli.parse_timezone("Not/A_Timezone")
    with pytest.raises(click.UsageError, match="must be sqlite or mysql"):
        database_cli.connection_from_options("postgres")
    with pytest.raises(click.UsageError, match="exactly one"):
        database_cli.connection_from_options("sqlite")
    context = click.Context(click.Command("dbtalk"))
    context.obj = DbtalkContext(
        settings=dbtalk_settings.Settings(
            verbose=False,
            logging=dbtalk_settings.LoggingSettings(level="INFO", format="%(message)s"),
            mysql=dbtalk_settings.MySQLConfig(
                output_directory="data",
                client_image="mysql:8.0.39",
                zero_datetime_as_null=True,
            ),
            database=dbtalk_settings.DatabaseTransferConfig(
                query_timeout_seconds=15,
                exec_timeout_seconds=45,
            ),
            postgres=dbtalk_settings.DumpRestoreConfig(
                output_directory="data",
                client_image="postgres:18-alpine",
            ),
        ),
        verbose=False,
    )
    assert database_cli.query_timeout_from_context(context, None) == 15
    assert database_cli.exec_timeout_from_context(context, None) == 45
    assert database_cli.query_timeout_from_context(context, 5) == 5
    assert database_cli.exec_timeout_from_context(context, 10) == 10
    with pytest.raises(RuntimeError, match="valid source"):
        database_cli.export_command_arguments({})
    with pytest.raises(RuntimeError, match="output path"):
        database_cli.export_command_arguments(_export_options(output="transfer.jsonl"))
    assert database_cli.export_command_arguments(_export_options(output=None)).output_path is None
    with pytest.raises(RuntimeError, match="valid DSN"):
        database_cli.export_command_arguments(_export_options(dsn_value=1))
    with pytest.raises(RuntimeError, match="valid DSN variable"):
        database_cli.export_command_arguments(_export_options(dsn_env=1))
    with pytest.raises(RuntimeError, match="valid timezone"):
        database_cli.export_command_arguments(_export_options(timezone_name=1))
    with pytest.raises(RuntimeError, match="valid included"):
        database_cli.export_command_arguments(_export_options(include_tables=(1,)))
    with pytest.raises(RuntimeError, match="valid excluded"):
        database_cli.export_command_arguments(_export_options(exclude_tables=(1,)))
    with pytest.raises(RuntimeError, match="valid archive"):
        database_cli.export_command_arguments(_export_options(archive="true"))
    with pytest.raises(RuntimeError, match="valid target"):
        database_cli.import_command_arguments({})
    with pytest.raises(RuntimeError, match="input path"):
        database_cli.import_command_arguments(_import_options(input_path="transfer.jsonl"))
    with pytest.raises(RuntimeError, match="valid import mode"):
        database_cli.import_command_arguments(_import_options(mode="replace"))
    with pytest.raises(RuntimeError, match="valid DSN"):
        database_cli.import_command_arguments(_import_options(dsn_value=1))
    with pytest.raises(RuntimeError, match="valid DSN variable"):
        database_cli.import_command_arguments(_import_options(dsn_env=1))
    with pytest.raises(RuntimeError, match="valid timezone"):
        database_cli.import_command_arguments(_import_options(timezone_name=1))
    with pytest.raises(RuntimeError, match="valid included"):
        database_cli.import_command_arguments(_import_options(include_tables=(1,)))
    with pytest.raises(RuntimeError, match="valid excluded"):
        database_cli.import_command_arguments(_import_options(exclude_tables=(1,)))

    assert database_cli.zero_datetime_as_null_from_context(click.Context(click.Command("dbtalk")))
    assert dbtalk_settings.bool_config("yes")
    assert not dbtalk_settings.bool_config("off")
    assert dbtalk_settings.int_config("3307") == 3307
    assert dbtalk_settings.mapping_config(None) == {}
    with pytest.raises(ValueError, match="database.query_timeout_seconds"):
        dbtalk_settings.load_database_transfer_config(
            {"query_timeout_seconds": 0, "exec_timeout_seconds": 30}
        )
    with pytest.raises(ValueError, match="database.exec_timeout_seconds"):
        dbtalk_settings.load_database_transfer_config(
            {"query_timeout_seconds": 30, "exec_timeout_seconds": 0}
        )
    with pytest.raises(ValueError, match="database.query_timeout_seconds"):
        dbtalk_settings.load_database_transfer_config({"exec_timeout_seconds": 30})
    with pytest.raises(ValueError, match="mysql.output_directory"):
        dbtalk_settings.load_dump_restore_config(
            {"output_directory": "  "},
            group="mysql",
        )
    with pytest.raises(ValueError, match="postgres.client_image"):
        dbtalk_settings.load_dump_restore_config(
            {"client_image": "  "},
            group="postgres",
        )
    assert not dbtalk_settings.load_mysql_config(
        {
            "output_directory": "data",
            "client_image": "mysql:8.0.39",
            "zero_datetime_as_null": "false",
        }
    ).zero_datetime_as_null


def test_database_export_output_path_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with patch("dbtalk.database.cli.datetime") as mocked_datetime:
        mocked_datetime.now.return_value = datetime(2026, 8, 20, 15, 45, 0)
        default_output = database_cli.resolve_export_output("sqlite", None, archive=False)
        output_directory = tmp_path / "exports"
        output_directory.mkdir()
        directory_output = database_cli.resolve_export_output(
            "mysql", output_directory, archive=True
        )

    assert default_output == Path.cwd() / "data" / "sqlite-20260820-154500.jsonl"
    assert default_output.parent.is_dir()
    assert directory_output == output_directory / "mysql-20260820-154500.jsonl.gz"
    assert (
        database_cli.resolve_export_output("sqlite", tmp_path / "transfer.jsonl", archive=False)
        == tmp_path / "transfer.jsonl"
    )
    with pytest.raises(click.ClickException, match="Export output directory does not exist"):
        database_cli.resolve_export_output(
            "sqlite", tmp_path / "missing" / "transfer.jsonl", archive=False
        )


def test_mysql_value_helpers_cover_invalid_boundaries() -> None:
    assert mysql_transfer._mysql_time_of_day(timedelta(hours=1, minutes=2, seconds=3)) == "01:02:03"
    assert mysql_transfer._mysql_time_of_day(timedelta(microseconds=120000)) == "00:00:00.12"
    with pytest.raises(DatabaseTransferError, match="outside"):
        mysql_transfer._mysql_time_of_day(timedelta(days=1))


def _document() -> TransferDocument:
    header = _header("items")
    return TransferDocument(
        TransferHeader("dbtalk.database-transfer/v1", "sqlite"),
        (TableBlock(header, ((1, "Ada"),)),),
    )


def _header(name: str) -> TableBlockHeader:
    return TableBlockHeader(
        name,
        (ColumnDefinition("id", "INTEGER"), ColumnDefinition("name", "TEXT")),
        ("id",),
    )


def _schema(name: str, foreign_keys: tuple[str, ...] = ()) -> TableSchema:
    return TableSchema(name, _header(name).columns, ("id",), foreign_keys)


def _export_options(**overrides: object) -> dict[str, object]:
    options: dict[str, object] = {
        "source": "sqlite",
        "output": Path("transfer.jsonl"),
        "dsn_value": None,
        "dsn_env": None,
        "timezone_name": "UTC",
        "include_tables": (),
        "exclude_tables": (),
        "archive": False,
    }
    options.update(overrides)
    return options


def _import_options(**overrides: object) -> dict[str, object]:
    options: dict[str, object] = {
        "target": "sqlite",
        "input_path": Path("transfer.jsonl"),
        "mode": "insert",
        "dsn_value": None,
        "dsn_env": None,
        "timezone_name": "UTC",
        "include_tables": (),
        "exclude_tables": (),
    }
    options.update(overrides)
    return options
