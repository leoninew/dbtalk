"""Focused tests for the database backup helper script."""

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "backup-db.py"
SPEC = importlib.util.spec_from_file_location("dbtalk_backup_db", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
backup_db: Any = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backup_db
SPEC.loader.exec_module(backup_db)


def test_connection_test_uses_a_connection_timeout_without_a_statement_timeout() -> None:
    completed = subprocess.CompletedProcess([], 0, "", "")

    with patch.object(backup_db.subprocess, "run", return_value=completed) as run:
        assert backup_db.run_connection_test("dbtalk", "sqlite:///", 7) is True

    assert run.call_args.args[0] == [
        "dbtalk",
        "query",
        "--dsn-env",
        "DBTALK_DSN_BACKUP",
        "--sql",
        "SELECT 1",
        "--connect-timeout",
        "7",
        "--format",
        "json",
    ]
    assert "timeout" not in run.call_args.kwargs


class FakeDumpProcess:
    def __init__(self, output: str, error: str, returncode: int) -> None:
        self.stdout = io.StringIO(output)
        self.stderr = io.StringIO(error)
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        return None


def _postgres_dump_target() -> Any:
    return backup_db.BackupTarget(
        engine="postgres",
        connection="postgres.example",
        connection_name="primary_postgres",
        database="app",
        dsn="postgresql+psycopg://user:password@postgres.example/app",
        enabled=True,
    )


def test_run_dump_prints_child_output_immediately(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    destination = tmp_path / "app.dump"
    destination.write_bytes(b"archive")

    def fake_popen(*args: object, **kwargs: object) -> FakeDumpProcess:
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
        assert kwargs["text"] is True
        assert kwargs["bufsize"] == 1
        return FakeDumpProcess(
            "pg_dump: dumping table public.accounts\n",
            "pg_dump: reading indexes\n",
            0,
        )

    with patch.object(backup_db.subprocess, "Popen", fake_popen):
        backup_db.run_dump("dbtalk", _postgres_dump_target(), destination)

    captured = capsys.readouterr()
    assert captured.out == "pg_dump: dumping table public.accounts\n"
    assert captured.err == "pg_dump: reading indexes\n"


def test_run_dump_includes_a_sanitized_dbtalk_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stderr = (
        "Error: Docker pg_dump failed: postgresql+psycopg://user:password@postgres.example/app\n"
    )

    def fake_popen(*args: object, **kwargs: object) -> FakeDumpProcess:
        return FakeDumpProcess("", stderr, 1)

    with (
        patch.object(backup_db.subprocess, "Popen", fake_popen),
        pytest.raises(
            backup_db.BackupError,
            match=(
                r"exit_code=1 diagnostic=Error: Docker pg_dump failed: "
                r"postgresql\+psycopg://<redacted>@postgres\.example/app"
            ),
        ),
    ):
        backup_db.run_dump("dbtalk", _postgres_dump_target(), tmp_path / "app.dump")

    captured = capsys.readouterr()
    assert captured.err == (
        "Error: Docker pg_dump failed: postgresql+psycopg://<redacted>@postgres.example/app\n"
    )
    assert "password" not in captured.err


def test_test_parser_uses_a_connection_timeout_destination() -> None:
    args = backup_db.build_parser().parse_args(["test", "--connect-timeout", "7"])

    assert args.connect_timeout_seconds == 7


def test_load_backup_config_builds_each_target_dsn_from_its_connection(tmp_path: Path) -> None:
    config_path = tmp_path / "backup-db.yaml"
    config_path.write_text(
        "output_directory: backups\n"
        "target_validation:\n"
        "  connection_timeout_seconds: 10\n"
        "connections:\n"
        "  - name: primary_mysql\n"
        "    dsn: 'mysql+pymysql://user:password@mysql.example:3307/'\n"
        "    databases:\n"
        "      - name: app\n"
        "        enabled: true\n"
        "  - name: primary_postgres\n"
        "    dsn: 'postgresql+psycopg://user:password@postgres.example/'\n"
        "    databases:\n"
        "      - name: audit\n"
        "        enabled: false\n",
        encoding="utf-8",
    )

    config = backup_db.load_backup_config(config_path)

    assert config.targets == (
        backup_db.BackupTarget(
            engine="mysql",
            connection="mysql.example:3307",
            connection_name="primary_mysql",
            database="app",
            dsn="mysql+pymysql://user:password@mysql.example:3307/app",
            enabled=True,
        ),
        backup_db.BackupTarget(
            engine="postgres",
            connection="postgres.example",
            connection_name="primary_postgres",
            database="audit",
            dsn="postgresql+psycopg://user:password@postgres.example/audit",
            enabled=False,
        ),
    )
    assert config.connections == (
        backup_db.BackupConnection(
            name="primary_mysql",
            dsn="mysql+pymysql://user:password@mysql.example:3307/",
        ),
        backup_db.BackupConnection(
            name="primary_postgres",
            dsn="postgresql+psycopg://user:password@postgres.example/",
        ),
    )


def test_run_tests_runs_once_per_connection_with_its_base_dsn(tmp_path: Path) -> None:
    config_path = tmp_path / "backup-db.yaml"
    config_path.write_text(
        "output_directory: backups\n"
        "target_validation:\n"
        "  connection_timeout_seconds: 10\n"
        "connections:\n"
        "  - name: primary_mysql\n"
        "    dsn: 'mysql+pymysql://user:password@mysql.example:3307/'\n"
        "    databases:\n"
        "      - name: app\n"
        "        enabled: true\n"
        "      - name: archive\n"
        "        enabled: false\n"
        "  - name: primary_postgres\n"
        "    dsn: 'postgresql+psycopg://user:password@postgres.example/'\n"
        "    databases:\n"
        "      - name: audit\n"
        "        enabled: false\n",
        encoding="utf-8",
    )
    args = backup_db.build_parser().parse_args(
        ["test", "--config", str(config_path), "--connect-timeout", "7"]
    )

    with (
        patch.object(backup_db, "resolve_command", return_value="dbtalk"),
        patch.object(backup_db, "run_connection_test", return_value=True) as run,
    ):
        assert backup_db.run_tests(args) == 0

    assert run.call_args_list == [
        (("dbtalk", "mysql+pymysql://user:password@mysql.example:3307/", 7),),
        (("dbtalk", "postgresql+psycopg://user:password@postgres.example/", 7),),
    ]


def test_load_backup_config_rejects_a_connection_dsn_with_a_database(tmp_path: Path) -> None:
    config_path = tmp_path / "backup-db.yaml"
    config_path.write_text(
        "output_directory: backups\n"
        "target_validation:\n"
        "  connection_timeout_seconds: 10\n"
        "connections:\n"
        "  - name: primary_mysql\n"
        "    dsn: 'mysql+pymysql://user:password@mysql.example:3307/app'\n"
        "    databases:\n"
        "      - name: app\n"
        "        enabled: true\n",
        encoding="utf-8",
    )

    with pytest.raises(backup_db.BackupError, match="must not include a database name"):
        backup_db.load_backup_config(config_path)
