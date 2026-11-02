"""Opt-in transfer tests confined to uniquely named, self-created SQL Server tables."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from uuid import uuid4

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from dbtalk.cli import cli
from dbtalk.database.connection import DatabaseClient
from dbtalk.database.dsn import dsn_from_environment
from dbtalk.database.format import read_jsonl, scan_document, write_document
from dbtalk.database.models import (
    DatabaseOperationError,
    DatabaseTransferError,
    ExportOptions,
    ImportOptions,
    TableBlock,
    TransferConnection,
    TransferDocument,
)
from dbtalk.database.operations import execute_sql_file_from_dsn
from dbtalk.database.transfer import export_database, import_database

pytestmark = pytest.mark.integration


@dataclass
class TransferInstance:
    connection: TransferConnection
    engine: Engine
    parent: str
    child: str

    @property
    def tables(self) -> tuple[str, str]:
        return self.parent, self.child


@pytest.fixture
def transfer_instance() -> Iterator[TransferInstance]:
    variable = os.environ.get("DBTALK_MSSQL_TEST_DSN_ENV")
    if not variable or os.environ.get("DBTALK_MSSQL_TRANSFER_TEST_WRITE") != "1":
        pytest.skip("enable the authorized MSSQL DSN and self-created-table transfer tests")
    parsed = dsn_from_environment(variable)
    assert parsed.dialect == "mssql" and parsed.database
    engine = create_engine(parsed.url, connect_args={"login_timeout": 8, "timeout": 30})
    prefix = f"dbtalk_transfer_{uuid4().hex[:12]}"
    parent, child = f"{prefix}_parent", f"{prefix}_child"
    created: list[str] = []
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE TABLE [dbo].[{parent}] ("
                    "id INT IDENTITY(1,1) PRIMARY KEY, label NVARCHAR(60) NOT NULL, "
                    "amount DECIMAL(12,4) NOT NULL, uid UNIQUEIDENTIFIER NOT NULL, "
                    "payload VARBINARY(20) NOT NULL, created DATETIME2(6) NOT NULL, "
                    "active BIT NOT NULL, day DATE NOT NULL, clock TIME(6) NOT NULL, "
                    "tick DATETIME NOT NULL, minute SMALLDATETIME NOT NULL)"
                )
            )
            created.append(parent)
            connection.execute(
                text(
                    f"CREATE TABLE [dbo].[{child}] (id INT NOT NULL, parent_id INT NOT NULL "
                    f"REFERENCES [dbo].[{parent}](id), label NVARCHAR(60) NOT NULL, "
                    "PRIMARY KEY (id, parent_id))"
                )
            )
            created.append(child)
            connection.execute(
                text(
                    f"INSERT INTO [dbo].[{parent}] "
                    "(label, amount, uid, payload, created, active, day, clock, tick, minute) "
                    "VALUES "
                    "(N'测试甲', 12.3400, '11111111-1111-1111-1111-111111111111', 0x0012FF, "
                    "'2026-01-02T03:04:05.123456', 1, '2026-01-02', '12:34:56.123456', "
                    "'2026-01-02T03:04:05.003', '2026-01-02T03:04:00'), "
                    "(N'测试乙', -0.1250, '22222222-2222-2222-2222-222222222222', 0x, "
                    "'2026-02-03T04:05:06.654321', 0, '2026-02-03', '01:02:03.654321', "
                    "'2026-02-03T04:05:06.007', '2026-02-03T04:05:00')"
                )
            )
            connection.execute(
                text(f"INSERT INTO [dbo].[{child}] VALUES (1,1,N'子项一'),(2,2,N'子项二')")
            )
        yield TransferInstance(TransferConnection("mssql", dsn_env=variable), engine, parent, child)
    finally:
        with engine.begin() as connection:
            for table in reversed(created):
                connection.execute(
                    text(
                        f"IF OBJECT_ID(N'dbo.{table}', N'U') IS NOT NULL DROP TABLE [dbo].[{table}]"
                    )
                )
        engine.dispose()


def _sqlite_target(path: Path, instance: TransferInstance) -> TransferConnection:
    url = f"sqlite:///{path.as_posix()}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    f'CREATE TABLE "{instance.parent}" ('
                    "id INTEGER PRIMARY KEY, label NVARCHAR(60) NOT NULL, "
                    "amount DECIMAL(12,4) NOT NULL, uid TEXT NOT NULL, "
                    "payload BLOB NOT NULL, created DATETIME(6) NOT NULL, "
                    "active BOOLEAN NOT NULL, day DATE NOT NULL, clock TIME(6) NOT NULL, "
                    "tick DATETIME NOT NULL, minute DATETIME NOT NULL)"
                )
            )
            connection.execute(
                text(
                    f'CREATE TABLE "{instance.child}" (id INTEGER NOT NULL, parent_id INTEGER '
                    f'NOT NULL REFERENCES "{instance.parent}"(id), label NVARCHAR(60) NOT NULL, '
                    "PRIMARY KEY (id, parent_id))"
                )
            )
    finally:
        engine.dispose()
    return TransferConnection("sqlite", dsn=url)


def test_mssql_sqlite_round_trip(transfer_instance: TransferInstance, tmp_path: Path) -> None:
    instance = transfer_instance
    with instance.engine.connect() as connection:
        before = connection.execute(
            text(f"SELECT * FROM [dbo].[{instance.parent}] ORDER BY id")
        ).all()
    exported = tmp_path / "mssql.jsonl"
    summary = export_database(
        ExportOptions(instance.connection, exported, UTC, include_tables=instance.tables)
    )
    assert summary.table_count == 2 and summary.row_count == 4
    sqlite = _sqlite_target(tmp_path / "roundtrip.db", instance)
    imported = import_database(ImportOptions(sqlite, exported, "insert", UTC))
    assert imported.row_count == 4
    sqlite_export = tmp_path / "sqlite.jsonl"
    assert export_database(ExportOptions(sqlite, sqlite_export, UTC)).row_count == 4
    with instance.engine.begin() as connection:
        connection.execute(text(f"DELETE FROM [dbo].[{instance.child}]"))
        connection.execute(text(f"DELETE FROM [dbo].[{instance.parent}]"))
    assert (
        import_database(ImportOptions(instance.connection, sqlite_export, "insert", UTC)).row_count
        == 4
    )
    assert (
        import_database(ImportOptions(instance.connection, sqlite_export, "upsert", UTC)).row_count
        == 4
    )
    with instance.engine.connect() as connection:
        assert (
            connection.execute(
                text(f"SELECT COUNT_BIG(*) FROM [dbo].[{instance.parent}]")
            ).scalar_one()
            == 2
        )
        assert (
            connection.execute(
                text(f"SELECT COUNT_BIG(*) FROM [dbo].[{instance.child}]")
            ).scalar_one()
            == 2
        )
        assert (
            connection.execute(
                text(f"SELECT label FROM [dbo].[{instance.parent}] WHERE id=1")
            ).scalar_one()
            == "测试甲"
        )
        after = connection.execute(
            text(f"SELECT * FROM [dbo].[{instance.parent}] ORDER BY id")
        ).all()
        assert after == before


def test_mssql_file_late_error_rolls_back(
    transfer_instance: TransferInstance, tmp_path: Path
) -> None:
    instance = transfer_instance
    script = tmp_path / "rollback.sql"
    script.write_text(
        f"UPDATE [dbo].[{instance.parent}] SET label=N'changed' WHERE id=1;\nGO\n"
        f"SELECT 1; INSERT INTO [dbo].[{instance.child}] VALUES (1,1,N'duplicate');\nGO\n",
        encoding="utf-8",
    )
    with pytest.raises(DatabaseOperationError, match="database execution failed"):
        execute_sql_file_from_dsn(None, instance.connection.dsn_env, script, timeout_seconds=15)
    with instance.engine.connect() as connection:
        assert (
            connection.execute(
                text(f"SELECT label FROM [dbo].[{instance.parent}] WHERE id=1")
            ).scalar_one()
            == "测试甲"
        )


@pytest.mark.parametrize("literal", [False, True])
def test_mssql_exec_late_error_rolls_back(
    transfer_instance: TransferInstance, literal: bool
) -> None:
    instance = transfer_instance
    parsed = dsn_from_environment(instance.connection.dsn_env)
    label = "N'changed'" if literal else ":label"
    statement = (
        f"UPDATE [dbo].[{instance.parent}] SET label={label} WHERE id=1; "
        f"SELECT 1; INSERT INTO [dbo].[{instance.child}] VALUES (1,1,N'duplicate');"
    )
    with (
        DatabaseClient(parsed, timeout_seconds=15) as client,
        pytest.raises(DatabaseOperationError, match="database (execution|transaction) failed"),
    ):
        if literal:
            client.execute_script((statement,))
        else:
            client.execute(statement, {"label": "changed"})
    with instance.engine.connect() as connection:
        assert (
            connection.execute(
                text(f"SELECT label FROM [dbo].[{instance.parent}] WHERE id=1")
            ).scalar_one()
            == "测试甲"
        )


def test_mssql_query_reads_results_without_committing(transfer_instance: TransferInstance) -> None:
    instance = transfer_instance
    parsed = dsn_from_environment(instance.connection.dsn_env)
    with DatabaseClient(parsed, timeout_seconds=15) as client:
        result = client.query(
            f"UPDATE [dbo].[{instance.parent}] SET label=:label OUTPUT inserted.label WHERE id=1",
            {"label": "transaction probe"},
        )
        assert result.rows == (("transaction probe",),)
    with instance.engine.connect() as connection:
        assert (
            connection.execute(
                text(f"SELECT label FROM [dbo].[{instance.parent}] WHERE id=1")
            ).scalar_one()
            == "测试甲"
        )


def test_mssql_concurrent_identity_upsert(
    transfer_instance: TransferInstance, tmp_path: Path
) -> None:
    instance = transfer_instance
    output = tmp_path / "upsert.jsonl"
    export_database(
        ExportOptions(instance.connection, output, UTC, include_tables=(instance.parent,))
    )
    with output.open(encoding="utf-8") as stream:
        header, tables = read_jsonl(stream)
    original = tables[0]
    first = next(row for row in original.rows if row[0] == 1)
    existing = (1, "更新文本", *first[2:])
    added = (3, "新增文本", *first[2:])
    write_document(
        output,
        TransferDocument(header, (TableBlock(original.header, (existing, added, added)),)),
    )
    options = ImportOptions(instance.connection, output, "upsert", UTC)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(import_database, (options, options)))
    assert [result.row_count for result in results] == [3, 3]
    with instance.engine.connect() as connection:
        assert (
            connection.execute(
                text(f"SELECT label FROM [dbo].[{instance.parent}] WHERE id=1")
            ).scalar_one()
            == "更新文本"
        )
        assert (
            connection.execute(
                text(f"SELECT COUNT_BIG(*) FROM [dbo].[{instance.parent}]")
            ).scalar_one()
            == 3
        )


def test_mssql_cli_archive_batches(transfer_instance: TransferInstance, tmp_path: Path) -> None:
    instance = transfer_instance
    with instance.engine.begin() as connection:
        connection.execute(
            text(
                "WITH numbers AS (SELECT 3 AS id UNION ALL "
                "SELECT id + 1 FROM numbers WHERE id < 1005) "
                f"INSERT INTO [dbo].[{instance.child}] "
                "SELECT id, 1, N'分批记录' FROM numbers OPTION (MAXRECURSION 0)"
            )
        )
    output = tmp_path / "archive.jsonl.gz"
    assert instance.connection.dsn_env is not None
    arguments = [
        "export",
        "--source",
        "mssql",
        "--dsn-env",
        instance.connection.dsn_env,
        "--output",
        str(output),
        "--archive",
        "--include-table",
        instance.parent,
        "--include-table",
        instance.child,
    ]
    binary = os.environ.get("DBTALK_MSSQL_TEST_BINARY")

    def invoke(arguments: list[str]) -> None:
        if binary:
            result = subprocess.run(
                [binary, *arguments], capture_output=True, text=True, timeout=60, check=False
            )
            assert result.returncode == 0, result.stderr
        else:
            invoked = CliRunner().invoke(cli, arguments)
            assert invoked.exit_code == 0, invoked.output

    invoke(arguments)
    assert sum(table.row_count for table in scan_document(output).tables) == 1007
    invoke(
        [
            "import",
            "--target",
            "mssql",
            "--dsn-env",
            instance.connection.dsn_env,
            "--input",
            str(output),
            "--mode",
            "upsert",
        ]
    )
    with instance.engine.connect() as connection:
        assert (
            connection.execute(
                text(f"SELECT COUNT_BIG(*) FROM [dbo].[{instance.child}]")
            ).scalar_one()
            == 1005
        )


def test_mssql_import_preflight_and_table_rollback(
    transfer_instance: TransferInstance, tmp_path: Path
) -> None:
    instance = transfer_instance
    output = tmp_path / "failure.jsonl"
    export_database(ExportOptions(instance.connection, output, UTC, include_tables=instance.tables))
    with output.open(encoding="utf-8") as stream:
        header, tables = read_jsonl(stream)
    parent, child = tables
    original = next(row for row in parent.rows if row[0] == 1)
    changed = (1, "must not change", *original[2:])
    invalid_child = (*child.rows[0][:2], "x" * 61)
    write_document(
        output,
        TransferDocument(
            header,
            (
                TableBlock(parent.header, (changed,)),
                TableBlock(child.header, (invalid_child,)),
            ),
        ),
    )
    with pytest.raises(DatabaseTransferError):
        import_database(ImportOptions(instance.connection, output, "upsert", UTC))
    with instance.engine.connect() as connection:
        assert (
            connection.execute(
                text(f"SELECT label FROM [dbo].[{instance.parent}] WHERE id=1")
            ).scalar_one()
            == "测试甲"
        )
    new = (3, "rolled back", *original[2:])
    write_document(output, TransferDocument(header, (TableBlock(parent.header, (new, original)),)))
    with pytest.raises(DatabaseTransferError):
        import_database(ImportOptions(instance.connection, output, "insert", UTC))
    with instance.engine.connect() as connection:
        assert (
            connection.execute(
                text(f"SELECT COUNT_BIG(*) FROM [dbo].[{instance.parent}] WHERE id=3")
            ).scalar_one()
            == 0
        )
    write_document(output, TransferDocument(header, (TableBlock(parent.header, (new,)),)))
    assert import_database(ImportOptions(instance.connection, output, "insert", UTC)).row_count == 1


def test_mssql_import_preserves_omitted_generated_columns(
    transfer_instance: TransferInstance, tmp_path: Path
) -> None:
    instance = transfer_instance
    output = tmp_path / "generated.jsonl"
    export_database(
        ExportOptions(instance.connection, output, UTC, include_tables=(instance.parent,))
    )
    with instance.engine.begin() as connection:
        connection.execute(
            text(
                f"ALTER TABLE [dbo].[{instance.parent}] "
                "ADD calculated AS (id + 1), version ROWVERSION"
            )
        )
    assert import_database(ImportOptions(instance.connection, output, "upsert", UTC)).row_count == 2
    with instance.engine.connect() as connection:
        assert connection.execute(
            text(
                f"SELECT calculated, DATALENGTH(version) FROM [dbo].[{instance.parent}] WHERE id=1"
            )
        ).one() == (2, 8)


def test_mssql_export_rejects_books_precision(tmp_path: Path) -> None:
    variable = os.environ.get("DBTALK_MSSQL_TEST_DSN_ENV")
    if not variable:
        pytest.skip("set DBTALK_MSSQL_TEST_DSN_ENV to the authorized DemoBook DSN")
    output = tmp_path / "unsupported.jsonl"
    with pytest.raises(DatabaseTransferError, match="(?i)precision|datetime2|unsupported|scale"):
        export_database(
            ExportOptions(
                TransferConnection("mssql", dsn_env=variable),
                output,
                UTC,
                include_tables=("Users", "Books"),
            )
        )
    assert not output.exists()
