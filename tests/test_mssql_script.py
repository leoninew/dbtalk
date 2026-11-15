from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from dbtalk.cli import cli
from dbtalk.database import connection as database_connection
from dbtalk.database import operations
from dbtalk.database.models import DatabaseOperationError, ExecutionResult, format_cli_error
from dbtalk.database.operations import execute_sql_file_from_dsn, sql_script_statements

MSSQL_DSN = "mssql+pymssql://unused:unused@invalid.example/db"


def mssql_script_batches(script: str) -> tuple[str, ...]:
    return sql_script_statements(script, dialect="mssql")


def test_batches_preserve_variable_and_procedure_scope() -> None:
    variable = "DECLARE @number int = 2;\nSELECT @number;"
    procedure = "CREATE PROCEDURE dbo.example AS\nBEGIN\nSELECT 1; SELECT 2;\nEND;"
    script = f"{variable}\nGO\n{procedure}\nGO\n"
    assert mssql_script_batches(script) == (variable, procedure)


@pytest.mark.parametrize(
    "body",
    [
        "SELECT 'first\nGO\nlast';",
        "SELECT N'first ''quoted''\nGO\nlast';",
        "SELECT [first]]part\nGO\nlast] FROM dbo.example;",
        'SELECT "first""part\nGO\nlast" FROM dbo.example;',
        "/* outer\n/* GO */\nGO\n*/ SELECT 1;",
        "-- GO\nSELECT 1;",
        "SELECT '\\' AS path; SELECT 1 AS #;",
        "SELECT 'COMMIT; EXEC proc; :r file; GO 2' AS example;",
        "SELECT\n    list, name\nFROM dbo.Items;",
        "SELECT\n    list\nFROM dbo.Items;",
        "EXEC dbo.example;",
        "SELECT N'$(literal)';",
        "BEGIN TRANSACTION; SELECT 1; COMMIT;",
    ],
)
def test_batches_keep_go_and_controls_inside_quoted_values_and_comments(body: str) -> None:
    assert mssql_script_batches(f"{body}\nGO\nSELECT 2;") == (body, "SELECT 2;")


def test_separator_comments_remain_complete_in_their_batches() -> None:
    script = "SELECT 1;\r\ngo /* next\r\ncomment */\r\nSELECT 2;\r\nGO -- tail"
    assert mssql_script_batches(script) == (
        "SELECT 1;",
        "/* next\r\ncomment */\r\nSELECT 2;",
    )
    assert mssql_script_batches("/* previous\ncomment */ GO\nSELECT 1") == ("SELECT 1",)
    assert mssql_script_batches("-- only comment\nGO\n/* block */\n;") == ()


@pytest.mark.parametrize(
    "script",
    [
        "SELECT 1;\nGO 2",
        "SELECT 1;\nGO -1",
        "SELECT 1;\nGO;",
        ":r included.sql",
        ":setvar Name Value",
        "!! echo shell",
        "SELECT $(Name);",
        "SELECT 'unterminated",
        "SELECT [unterminated",
        "SELECT 1; /* unterminated",
    ],
)
def test_unsupported_scripts_fail_before_engine_creation(
    script: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_engine(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("preflight must finish before creating an engine")

    monkeypatch.setattr(database_connection, "create_engine", forbidden_engine)
    path = tmp_path / "unsupported.sql"
    path.write_text(script, encoding="utf-8")
    with pytest.raises(DatabaseOperationError, match="mssql SQL"):
        execute_sql_file_from_dsn(MSSQL_DSN, None, path, timeout_seconds=10)
    preview = CliRunner().invoke(
        cli, ["exec", "--dsn", MSSQL_DSN, "--file", str(path), "--dry-run"]
    )
    assert preview.exit_code != 0
    assert "mssql SQL" in preview.output


def test_lexical_errors_do_not_echo_script() -> None:
    with pytest.raises(DatabaseOperationError) as captured:
        mssql_script_batches("SELECT 'private_script_sentinel")
    assert "private_script_sentinel" not in format_cli_error(captured.value)


def test_dry_run_matches_execution_batches_without_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "batch.sql"
    path.write_text("DECLARE @id int=1; SELECT @id;\nGO\nSELECT 2;", encoding="utf-8")
    executed: list[tuple[str, ...]] = []

    def capture_batches(batches: tuple[str, ...]) -> ExecutionResult:
        executed.append(batches)
        return ExecutionResult(row_count=0)

    client_factory = MagicMock()
    client_factory.return_value.__enter__.return_value.execute_script.side_effect = capture_batches
    monkeypatch.setattr(operations, "DatabaseClient", client_factory)
    monkeypatch.setenv("DBTALK_T02_PREVIEW", MSSQL_DSN)
    runner = CliRunner()
    for options in (["--dsn", MSSQL_DSN], ["--dsn-env", "DBTALK_T02_PREVIEW"]):
        client_factory.reset_mock()
        preview = runner.invoke(cli, ["exec", *options, "--file", str(path), "--dry-run"])
        assert preview.exit_code == 0, preview.output
        client_factory.assert_not_called()
        assert "SQL dry-run completed (2 batches)" in preview.output
        execution = runner.invoke(cli, ["exec", *options, "--file", str(path)])
        assert execution.exit_code == 0, execution.output
        preview_sql = preview.output.split("SQL dry-run completed", 1)[0]
        assert mssql_script_batches(preview_sql) == executed[-1]
    assert executed == [("DECLARE @id int=1; SELECT @id;", "SELECT 2;")] * 2


@pytest.mark.parametrize(
    "options",
    [
        ["--dsn", "invalid"],
        ["--dsn-env", "DBTALK_T02_MISSING"],
        ["--dsn", MSSQL_DSN, "--dsn-env", "DBTALK_T02_OTHER"],
    ],
)
def test_file_dry_run_validates_explicit_dsn_offline(
    options: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DBTALK_T02_MISSING", raising=False)
    path = tmp_path / "preview.sql"
    path.write_text("SELECT 1;", encoding="utf-8")
    result = CliRunner().invoke(cli, ["exec", *options, "--file", str(path), "--dry-run"])
    assert result.exit_code != 0
    assert "SQL dry-run completed" not in result.output


def test_preview_without_dsn_keeps_generic_behavior(tmp_path: Path) -> None:
    path = tmp_path / "generic.sql"
    script = "BEGIN;\nSELECT 1;\nSELECT 2;\nCOMMIT;"
    path.write_text(script, encoding="utf-8")
    assert sql_script_statements(script) == ("SELECT 1", "SELECT 2")
    result = CliRunner().invoke(cli, ["exec", "--file", str(path), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "SQL dry-run completed (2 statements)" in result.output
    assert "BEGIN" not in result.output and "COMMIT" not in result.output


@pytest.mark.parametrize("failure", [False, True])
def test_all_batches_share_the_existing_client_transaction(
    failure: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "transaction.db"
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE records (id INTEGER PRIMARY KEY)")
    engine_calls = []

    def local_engine(*args: Any, **kwargs: Any) -> Engine:
        engine_calls.append(kwargs)
        return engine

    monkeypatch.setattr(database_connection, "create_engine", local_engine)
    path = tmp_path / "transaction.sql"
    second = (
        "INSERT INTO missing_table VALUES (2);" if failure else "INSERT INTO records VALUES (2);"
    )
    path.write_text(f"INSERT INTO records VALUES (1);\nGO\n{second}", encoding="utf-8")
    if not failure:
        result = execute_sql_file_from_dsn(
            MSSQL_DSN, None, path, timeout_seconds=10, connect_timeout_seconds=5
        )
        assert result.row_count == 2
    else:
        with pytest.raises(DatabaseOperationError, match="database execution failed"):
            execute_sql_file_from_dsn(
                MSSQL_DSN, None, path, timeout_seconds=10, connect_timeout_seconds=5
            )
    with engine.connect() as connection:
        actual = [
            tuple(row) for row in connection.exec_driver_sql("SELECT id FROM records ORDER BY id")
        ]
        assert actual == ([(1,), (2,)] if not failure else [])
    assert engine_calls == [{"connect_args": {"timeout": 10, "login_timeout": 5}}]
