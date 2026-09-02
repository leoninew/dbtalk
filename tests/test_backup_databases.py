"""Focused tests for the database backup helper script."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "backup_databases.py"
SPEC = importlib.util.spec_from_file_location("dbtalk_backup_databases", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
backup_databases: Any = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backup_databases
SPEC.loader.exec_module(backup_databases)


def test_connection_test_uses_a_connection_timeout_without_a_statement_timeout() -> None:
    completed = subprocess.CompletedProcess([], 0, "", "")

    with patch.object(backup_databases.subprocess, "run", return_value=completed) as run:
        assert backup_databases.run_connection_test("dbtalk", "sqlite:///", 7) is True

    assert run.call_args.args[0] == [
        "dbtalk",
        "query",
        "--dsn-env",
        "DBTALK_BACKUP_DSN",
        "--sql",
        "SELECT 1",
        "--connect-timeout",
        "7",
        "--format",
        "json",
    ]
    assert "timeout" not in run.call_args.kwargs


def test_run_dump_includes_a_sanitized_dbtalk_diagnostic(tmp_path: Path) -> None:
    target = backup_databases.BackupTarget(
        engine="postgres",
        connection="postgres.example",
        connection_name="primary_postgres",
        database="app",
        dsn="postgresql+psycopg://user:password@postgres.example/app",
        enabled=True,
    )
    completed = subprocess.CompletedProcess(
        [],
        1,
        "",
        "Error: Docker pg_dump failed: postgresql+psycopg://user:password@postgres.example/app",
    )

    with (
        patch.object(backup_databases.subprocess, "run", return_value=completed),
        pytest.raises(
            backup_databases.BackupError,
            match=(
                r"exit_code=1 diagnostic=Error: Docker pg_dump failed: "
                r"postgresql\+psycopg://<redacted>@postgres\.example/app"
            ),
        ),
    ):
        backup_databases.run_dump("dbtalk", target, tmp_path / "app.dump")


def test_test_parser_uses_a_connection_timeout_destination() -> None:
    args = backup_databases.build_parser().parse_args(["test", "--connect-timeout", "7"])

    assert args.connect_timeout_seconds == 7


def test_load_backup_config_builds_each_target_dsn_from_its_connection(tmp_path: Path) -> None:
    config_path = tmp_path / "backup_databases.yaml"
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

    config = backup_databases.load_backup_config(config_path)

    assert config.targets == (
        backup_databases.BackupTarget(
            engine="mysql",
            connection="mysql.example:3307",
            connection_name="primary_mysql",
            database="app",
            dsn="mysql+pymysql://user:password@mysql.example:3307/app",
            enabled=True,
        ),
        backup_databases.BackupTarget(
            engine="postgres",
            connection="postgres.example",
            connection_name="primary_postgres",
            database="audit",
            dsn="postgresql+psycopg://user:password@postgres.example/audit",
            enabled=False,
        ),
    )
    assert config.connections == (
        backup_databases.BackupConnection(
            name="primary_mysql",
            dsn="mysql+pymysql://user:password@mysql.example:3307/",
        ),
        backup_databases.BackupConnection(
            name="primary_postgres",
            dsn="postgresql+psycopg://user:password@postgres.example/",
        ),
    )


def test_run_tests_runs_once_per_connection_with_its_base_dsn(tmp_path: Path) -> None:
    config_path = tmp_path / "backup_databases.yaml"
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
    args = backup_databases.build_parser().parse_args(
        ["test", "--config", str(config_path), "--connect-timeout", "7"]
    )

    with (
        patch.object(backup_databases, "resolve_command", return_value="dbtalk"),
        patch.object(backup_databases, "run_connection_test", return_value=True) as run,
    ):
        assert backup_databases.run_tests(args) == 0

    assert run.call_args_list == [
        (("dbtalk", "mysql+pymysql://user:password@mysql.example:3307/", 7),),
        (("dbtalk", "postgresql+psycopg://user:password@postgres.example/", 7),),
    ]


def test_load_backup_config_rejects_a_connection_dsn_with_a_database(tmp_path: Path) -> None:
    config_path = tmp_path / "backup_databases.yaml"
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

    with pytest.raises(backup_databases.BackupError, match="must not include a database name"):
        backup_databases.load_backup_config(config_path)
