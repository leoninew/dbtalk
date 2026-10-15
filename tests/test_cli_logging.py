"""Observable logging behavior at the CLI operation boundary."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

import dbtalk.postgres.cli as postgres_cli
import dbtalk.postgres.database as postgres_database
import dbtalk.postgres.owner as postgres_owner
import dbtalk.postgres.role as postgres_role
from dbtalk.cli import cli
from dbtalk.database.dsn import parse_dsn, password_from_environment, sqlite_dsn
from dbtalk.database.models import DatabaseOperationError
from dbtalk.postgres.owner import OwnershipPreview


def test_query_keeps_json_on_stdout_and_omits_sql_and_parameters_from_logs() -> None:
    statement = "SELECT :private_value AS value"
    result = CliRunner().invoke(
        cli,
        [
            "-v",
            "query",
            "--dsn",
            "sqlite:///:memory:",
            "--sql",
            statement,
            "--param",
            'private_value="private-result"',
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["rows"] == [{"value": "private-result"}]
    assert result.stderr.count("cli query started") == 1
    assert result.stderr.count("cli query completed") == 1
    assert "elapsed_ms=" in result.stderr
    for private in (statement, "private_value", "private-result", "sqlite://"):
        assert private not in result.stderr


@pytest.mark.parametrize("verbose", [[], ["-v"]])
def test_failed_exec_logs_one_failure_without_sql_or_values(verbose: list[str]) -> None:
    statement = "INSERT INTO missing_table VALUES (:private_value)"
    result = CliRunner().invoke(
        cli,
        [
            *verbose,
            "exec",
            "--dsn",
            "sqlite:///:memory:",
            "--sql",
            statement,
            "--param",
            'private_value="private-input"',
        ],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.count("cli exec started") == (1 if verbose else 0)
    assert result.stderr.count("cli exec failed") == (1 if verbose else 0)
    if verbose:
        assert "ERROR" in result.stderr
    else:
        assert result.stderr == "Error: database execution failed\n"
    assert "database execution failed" in result.stderr
    assert "completed" not in result.stderr
    for private in (statement, "private_value", "private-input", "[SQL:", "[parameters:"):
        assert private not in result.stderr


def test_dry_run_logs_preview_flag_without_logging_preview_sql() -> None:
    statement = "UPDATE accounts SET password = 'private-password'"
    result = CliRunner().invoke(
        cli, ["-v", "exec", "--dsn", "sqlite:///:memory:", "--sql", statement, "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert statement in result.stdout
    assert "SQL dry-run completed" in result.stdout
    assert "dry_run=True" in result.stderr
    assert "private-password" not in result.stderr
    assert statement not in result.stderr


@pytest.mark.parametrize(
    ("error", "status", "exit_code"),
    [
        (None, "completed", 0),
        (DatabaseOperationError("database is in use"), "failed", 1),
        (KeyboardInterrupt(), "cancelled", 1),
    ],
)
def test_drop_logs_target_flags_and_terminal_status_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException | None,
    status: str,
    exit_code: int,
) -> None:
    operation = Mock(side_effect=error)
    monkeypatch.setattr(postgres_database, "drop_database", operation)
    result = CliRunner().invoke(
        cli,
        [
            "-v",
            "postgres",
            "schema",
            "drop",
            "--dsn",
            "postgresql+psycopg://admin:private-password@db.example/postgres",
            "--name",
            "app",
            "--if-exists",
            "--force",
            "--yes",
        ],
    )

    assert result.exit_code == exit_code, result.output
    operation.assert_called_once()
    assert result.stderr.count("cli postgres schema drop started") == 1
    assert result.stderr.count(f"cli postgres schema drop {status}") == 1
    for field in ("name='app'", "force=True", "if_exists=True", "yes=True", "elapsed_ms="):
        assert field in result.stderr
    assert "private-password" not in result.stderr
    assert "postgresql+psycopg://" not in result.stderr
    if error is None:
        assert result.stdout == "Database dropped if it existed: app\n"
    else:
        assert "cli postgres schema drop completed" not in result.stderr
        assert result.stdout == ""
        assert ("WARNING" if status == "cancelled" else "ERROR") in result.stderr


def test_password_rotation_logs_role_without_password_or_environment_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DBTALK_ROTATED_PASSWORD", "private-new-password")
    operation = Mock()
    monkeypatch.setattr(postgres_role, "rotate_role_password", operation)
    result = CliRunner().invoke(
        cli,
        [
            "-v",
            "postgres",
            "role",
            "password",
            "--dsn",
            "postgresql+psycopg://admin:private-admin-password@db.example/postgres",
            "--role",
            "app",
            "--password-env",
            "DBTALK_ROTATED_PASSWORD",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    operation.assert_called_once()
    assert password_from_environment(operation.call_args.args[2]) == "private-new-password"
    assert "role_name='app'" in result.stderr
    assert "cli postgres role password completed" in result.stderr
    for private in ("private-new-password", "private-admin-password", "DBTALK_ROTATED_PASSWORD"):
        assert private not in result.stderr


def test_ownership_preview_logs_execution_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    operation = Mock(return_value=OwnershipPreview("app", (), (), ()))
    monkeypatch.setattr(postgres_owner, "reassign_owned", operation)
    result = CliRunner().invoke(
        cli,
        [
            "-v",
            "postgres",
            "owner",
            "reassign",
            "--dsn",
            "postgresql+psycopg://admin:secret@db.example/postgres",
            "--database",
            "app",
            "--from-role",
            "old_owner",
            "--to-role",
            "new_owner",
        ],
    )

    assert result.exit_code == 0, result.output
    assert operation.call_args.kwargs["execute"] is False
    assert "Preview only" in result.stdout
    for field in (
        "database_name='app'",
        "from_role='old_owner'",
        "to_role='new_owner'",
        "yes=False",
    ):
        assert field in result.stderr
    assert "cli postgres owner reassign completed" in result.stderr


@pytest.mark.parametrize("command", ["dump", "restore"])
def test_postgres_backup_logs_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str
) -> None:
    archive = tmp_path / "app.dump"
    archive.touch()
    monkeypatch.setattr(postgres_cli, f"{command}_database", Mock(return_value=archive))
    result = CliRunner().invoke(
        cli,
        [
            "-v",
            "postgres",
            command,
            "--dsn",
            "postgresql+psycopg://admin:private-password@db.example/app",
            "--database",
            "app",
            "--output" if command == "dump" else "--input",
            str(archive),
        ],
    )

    assert result.exit_code == 0, result.output
    assert str(archive) in result.stdout
    assert result.stderr.count(f"cli postgres {command} started") == 1
    assert result.stderr.count(f"cli postgres {command} completed") == 1
    assert "target_database='app'" in result.stderr
    assert "private-password" not in result.stderr


@pytest.mark.parametrize("verbose", [[], ["-v"], ["--verbose"]])
def test_transfer_logging_is_opt_in_without_duplicate_events(
    tmp_path: Path, verbose: list[str]
) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    archive = tmp_path / "transfer.jsonl"
    for database in (source, target):
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY)")
            if database == source:
                connection.execute("INSERT INTO items VALUES (1)")
    runner = CliRunner()
    for command, database, direction, file_option in (
        ("export", source, "--source", "--output"),
        ("import", target, "--target", "--input"),
    ):
        arguments = [
            *verbose,
            command,
            direction,
            "sqlite",
            "--dsn",
            sqlite_dsn(database),
            file_option,
            str(archive),
        ]
        if command == "import":
            arguments.extend(["--mode", "insert"])
        result = runner.invoke(
            cli,
            arguments,
        )

        assert result.exit_code == 0, result.output
        assert result.stderr.count(f"database {command} started") == (1 if verbose else 0)
        assert result.stderr.count(f"database {command} completed") == (1 if verbose else 0)
        assert f"cli {command} started" not in result.stderr
        if not verbose:
            assert result.stderr == ""
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT id FROM items").fetchall() == [(1,)]


def test_logging_respects_format_level_and_verbose_without_duplicate_records() -> None:
    runner = CliRunner()
    for verbose, level, expected_lines in (
        ([], "INFO", 0),
        (["-v"], "INFO", 2),
        ([], "DEBUG", 0),
        (["-v"], "WARNING", 0),
        (["--verbose"], "INFO", 2),
        ([], "INFO", 0),
    ):
        result = runner.invoke(
            cli,
            [*verbose, "query", "--dsn", "sqlite:///:memory:", "--sql", "SELECT 1"],
            env={
                "DBTALK_LOGGING__LEVEL": level,
                "DBTALK_LOGGING__FORMAT": "%(levelname)s|%(message)s",
            },
        )

        assert result.exit_code == 0, result.output
        lines = result.stderr.splitlines()
        assert len(lines) == expected_lines
        assert all(line.startswith("INFO|cli query ") for line in lines)


@pytest.mark.parametrize("verbose", [[], ["-v"]])
def test_transfer_failure_logs_are_also_opt_in(tmp_path: Path, verbose: list[str]) -> None:
    archive = tmp_path / "invalid.jsonl"
    archive.write_text("invalid JSON\n", encoding="utf-8")
    target = tmp_path / "target.db"
    target.touch()
    result = CliRunner().invoke(
        cli,
        [
            *verbose,
            "import",
            "--target",
            "sqlite",
            "--dsn",
            sqlite_dsn(target),
            "--input",
            str(archive),
            "--mode",
            "insert",
        ],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: invalid JSONL at line 1" in result.stderr
    assert result.stderr.count("database import failed") == (1 if verbose else 0)
    if not verbose:
        assert result.stderr == "Error: invalid JSONL at line 1\n"


def test_dsn_display_omits_sensitive_query_without_changing_connection_url() -> None:
    parsed = parse_dsn(
        "postgresql+psycopg://app:private-password@db.example/app"
        "?sslpassword=private-key-password&token=private-token&sslmode=require"
    )

    assert parsed.display == "postgresql+psycopg://app:***@db.example/app"
    assert parsed.url.password == "private-password"
    assert parsed.url.query["sslpassword"] == "private-key-password"
    assert parsed.url.query["token"] == "private-token"
