"""Opt-in SQL Server smoke tests using Books and connection-local temporary tables."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest
from click.testing import CliRunner

from dbtalk.cli import cli
from dbtalk.database.connection import DatabaseClient
from dbtalk.database.dsn import ParsedDsn, dsn_from_environment
from dbtalk.database.models import DatabaseOperationError

pytestmark = pytest.mark.integration


@pytest.fixture
def mssql_connection() -> tuple[str, ParsedDsn]:
    variable = os.environ.get("DBTALK_MSSQL_TEST_DSN_ENV")
    if not variable:
        pytest.skip("set DBTALK_MSSQL_TEST_DSN_ENV to an authorized SQL Server DSN variable")
    parsed = dsn_from_environment(variable)
    assert parsed.dialect == "mssql" and parsed.database
    return variable, parsed


def test_mssql_books_and_bound_parameters(mssql_connection: tuple[str, ParsedDsn]) -> None:
    _, parsed = mssql_connection
    with (
        DatabaseClient(parsed, connect_timeout_seconds=8, timeout_seconds=15) as client,
        client.connect() as session,
    ):
        result = session.query(
            "SELECT DB_NAME(), CAST(:number AS INT), CAST(:label AS NVARCHAR(30))",
            {"number": 7, "label": "SQL Server 测试"},
        )
        assert str(result.rows[0][0]).casefold() == str(parsed.database).casefold()
        assert result.rows[0][1:] == (7, "SQL Server 测试")
        books = session.query(
            "SELECT TOP (3) [Id], [Name], [CreationTime] FROM [dbo].[Books] ORDER BY [Id]"
        )
        assert 0 < len(books.rows) <= 3
        assert all(isinstance(row[0], UUID) for row in books.rows)
        assert all(isinstance(row[1], str) for row in books.rows)
        assert all(isinstance(row[2], datetime) for row in books.rows)


def test_mssql_exec_cli_read_statement(mssql_connection: tuple[str, ParsedDsn]) -> None:
    variable, _ = mssql_connection
    result = CliRunner().invoke(
        cli,
        [
            "exec",
            "--dsn-env",
            variable,
            "--sql",
            "SELECT COUNT_BIG(*) FROM [dbo].[Books] WHERE [Name] <> :name",
            "--param",
            'name="dbtalk-smoke-sentinel"',
        ],
    )
    assert result.exit_code == 0, result.output
    assert "rows affected" in result.output


def test_mssql_query_cte_and_parameters(mssql_connection: tuple[str, ParsedDsn]) -> None:
    _, parsed = mssql_connection
    with DatabaseClient(parsed, connect_timeout_seconds=8, timeout_seconds=15) as client:
        result = client.query(
            "WITH books AS (SELECT [Name] FROM [dbo].[Books]) "
            "SELECT COUNT_BIG(*) AS total FROM books WHERE [Name] <> :name",
            {"name": "dbtalk-smoke-sentinel"},
        )
        assert isinstance(result.rows[0][0], int) and result.rows[0][0] > 0


def test_mssql_query_keyword_parameter(mssql_connection: tuple[str, ParsedDsn]) -> None:
    _, parsed = mssql_connection
    with DatabaseClient(parsed, connect_timeout_seconds=8, timeout_seconds=15) as client:
        assert client.query("SELECT CAST(:from AS INT)", {"from": 7}).rows == ((7,),)


def test_mssql_temporary_table_transaction(mssql_connection: tuple[str, ParsedDsn]) -> None:
    _, parsed = mssql_connection
    with DatabaseClient(parsed, connect_timeout_seconds=8, timeout_seconds=15) as client:
        result = client.execute_script(
            (
                "CREATE TABLE #dbtalk_smoke (id INT PRIMARY KEY, label NVARCHAR(30))",
                "INSERT INTO #dbtalk_smoke VALUES (1, N'test')",
                "DROP TABLE #dbtalk_smoke",
            )
        )
        assert result.row_count == 1
        with client.connect() as session:
            session_id = session.query("SELECT @@SPID").rows[0][0]
        with pytest.raises(DatabaseOperationError, match="database execution failed"):
            client.execute_script(
                (
                    "CREATE TABLE #dbtalk_rollback (id INT PRIMARY KEY)",
                    "INSERT INTO #dbtalk_rollback VALUES (1)",
                    "INSERT INTO #dbtalk_rollback VALUES (1)",
                )
            )
        with client.connect() as session:
            assert session.query("SELECT @@SPID").rows == ((session_id,),)
            assert session.query("SELECT OBJECT_ID('tempdb..#dbtalk_rollback')").rows == ((None,),)


def test_mssql_statement_timeout(mssql_connection: tuple[str, ParsedDsn]) -> None:
    _, parsed = mssql_connection
    with (
        DatabaseClient(parsed, connect_timeout_seconds=8, timeout_seconds=1) as client,
        pytest.raises(DatabaseOperationError, match="database execution timed out"),
    ):
        client.execute("WAITFOR DELAY '00:00:03'")


def test_mssql_exec_consumes_results(mssql_connection: tuple[str, ParsedDsn]) -> None:
    _, parsed = mssql_connection
    with DatabaseClient(parsed, timeout_seconds=15) as client, client.transaction() as session:
        session.execute_literal("CREATE TABLE #dbtalk_results (id INT PRIMARY KEY)")
        session.execute(
            "INSERT INTO #dbtalk_results VALUES (:first); SELECT 1 AS intermediate; "
            "INSERT INTO #dbtalk_results VALUES (:second);",
            {"first": 1, "second": 2},
        )
        assert session.query("SELECT COUNT_BIG(*) FROM #dbtalk_results").rows == ((2,),)
        session.execute_literal("DROP TABLE #dbtalk_results")


def test_mssql_query_cli_reads_books(mssql_connection: tuple[str, ParsedDsn]) -> None:
    variable, _ = mssql_connection
    result = CliRunner().invoke(
        cli,
        [
            "query",
            "--dsn-env",
            variable,
            "--sql",
            "SELECT TOP (1) [Id] FROM [dbo].[Books] ORDER BY [Id]",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["row_count"] == 1
    assert UUID(payload["rows"][0]["Id"])


def test_mssql_exec_file_batches(mssql_connection: tuple[str, ParsedDsn], tmp_path: Path) -> None:
    variable, _ = mssql_connection
    script = tmp_path / "batches.sql"
    script.write_text(
        "CREATE TABLE #dbtalk_file (id INT PRIMARY KEY, label NVARCHAR(30));\nGO\n"
        "DECLARE @label NVARCHAR(30) = N'批次测试';\n"
        "INSERT INTO #dbtalk_file VALUES (1, @label);\nGO\n"
        "DROP TABLE #dbtalk_file;\nGO\n"
        "CREATE PROCEDURE #dbtalk_file_proc AS\nBEGIN\nSELECT 1; SELECT 2;\nEND;\nGO\n"
        "DROP PROCEDURE #dbtalk_file_proc;\nGO\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    arguments = ["exec", "--dsn-env", variable, "--file", str(script)]
    preview = runner.invoke(cli, [*arguments, "--dry-run"])
    assert preview.exit_code == 0, preview.output
    assert "DECLARE @label" in preview.output
    executed = runner.invoke(cli, arguments)
    assert executed.exit_code == 0, executed.output
    assert "1 rows affected" in executed.output
