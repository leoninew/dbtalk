"""Focused tests for the database backup helper script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml  # type: ignore[import-untyped]

from dbtalk.database.models import DatabaseOperationError
from dbtalk.settings import (
    DatabaseTransferConfig,
    DumpRestoreConfig,
    LoggingSettings,
    MySQLConfig,
    Settings,
)

SCRIPT = Path(__file__).parents[1] / "scripts" / "backup-db.py"
SPEC = importlib.util.spec_from_file_location("dbtalk_backup_db", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
backup_db: Any = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backup_db
SPEC.loader.exec_module(backup_db)


def _settings() -> Settings:
    return Settings(
        verbose=False,
        logging=LoggingSettings(level="INFO", format="%(message)s"),
        mysql=MySQLConfig(
            output_directory="data", client_image="mysql:8.0", zero_datetime_as_null=True
        ),
        database=DatabaseTransferConfig(query_timeout_seconds=30, exec_timeout_seconds=30),
        postgres=DumpRestoreConfig(output_directory="data", client_image="postgres:18"),
    )


def test_connection_test_uses_the_package_query_api_with_a_connection_timeout() -> None:
    settings = _settings()

    with patch.object(backup_db, "query_from_dsn") as query:
        assert backup_db.run_connection_test(settings, "sqlite:///", 7) is True

    assert query.call_args.args == ("sqlite:///", None, "SELECT 1")
    assert query.call_args.kwargs == {
        "timeout_seconds": 30,
        "connect_timeout_seconds": 7,
    }


def test_connection_test_returns_false_when_the_package_query_fails() -> None:
    with patch.object(
        backup_db,
        "query_from_dsn",
        side_effect=DatabaseOperationError("unreachable"),
    ):
        assert backup_db.run_connection_test(_settings(), "sqlite:///", 7) is False


def _postgres_dump_target() -> Any:
    return backup_db.BackupTarget(
        engine="postgres",
        connection="postgres.example",
        connection_name="primary_postgres",
        database="app",
        dsn="postgresql+psycopg://user:password@postgres.example/app",
        enabled=True,
    )


def test_run_dump_uses_the_postgres_package_api(tmp_path: Path) -> None:
    destination = tmp_path / "app.dump"
    destination.write_bytes(b"archive")
    connection = object()
    options = object()

    with (
        patch.object(backup_db, "postgres_connection_from_dsn", return_value=connection) as parsed,
        patch.object(backup_db, "resolve_postgres_dump_options", return_value=options) as resolve,
        patch.object(backup_db, "dump_postgres_database", return_value=destination) as dump,
    ):
        backup_db.run_dump(_settings(), _postgres_dump_target(), destination)

    parsed.assert_called_once_with(
        "postgresql+psycopg://user:password@postgres.example/app",
        None,
        target_database="app",
    )
    resolve.assert_called_once_with(_settings().postgres, connection, destination, None, ())
    dump.assert_called_once_with(options)


def test_run_dump_includes_a_sanitized_dbtalk_diagnostic(
    tmp_path: Path,
) -> None:
    with (
        patch.object(backup_db, "postgres_connection_from_dsn", return_value=object()),
        patch.object(backup_db, "resolve_postgres_dump_options", return_value=object()),
        patch.object(
            backup_db,
            "dump_postgres_database",
            side_effect=backup_db.click.ClickException(
                "Docker pg_dump failed: postgresql+psycopg://user:password@postgres.example/app"
            ),
        ),
        pytest.raises(
            backup_db.BackupError,
            match=(
                r"diagnostic=Docker pg_dump failed: "
                r"postgresql\+psycopg://<redacted>@postgres\.example/app"
            ),
        ),
    ):
        backup_db.run_dump(_settings(), _postgres_dump_target(), tmp_path / "app.dump")


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
            exclude_tables=(),
        ),
        backup_db.BackupTarget(
            engine="postgres",
            connection="postgres.example",
            connection_name="primary_postgres",
            database="audit",
            dsn="postgresql+psycopg://user:password@postgres.example/audit",
            enabled=False,
            exclude_tables=(),
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


def test_load_backup_config_reads_exclude_tables(tmp_path: Path) -> None:
    config_path = tmp_path / "backup-db.yaml"
    config_path.write_text(
        "output_directory: backups\n"
        "target_validation:\n"
        "  connection_timeout_seconds: 10\n"
        "connections:\n"
        "  - name: primary_postgres\n"
        "    dsn: 'postgresql+psycopg://user:password@postgres.example/'\n"
        "    databases:\n"
        "      - name: app\n"
        "        enabled: true\n"
        "        exclude_tables:\n"
        "          - ops_system_logs\n"
        "          - usage_logs\n",
        encoding="utf-8",
    )

    config = backup_db.load_backup_config(config_path)

    assert config.targets[0].exclude_tables == ("ops_system_logs", "usage_logs")


def test_load_backup_config_rejects_blank_exclude_tables(tmp_path: Path) -> None:
    config_path = tmp_path / "backup-db.yaml"
    config_path.write_text(
        "output_directory: backups\n"
        "target_validation:\n"
        "  connection_timeout_seconds: 10\n"
        "connections:\n"
        "  - name: primary_postgres\n"
        "    dsn: 'postgresql+psycopg://user:password@postgres.example/'\n"
        "    databases:\n"
        "      - name: app\n"
        "        enabled: true\n"
        "        exclude_tables:\n"
        "          - ' '\n",
        encoding="utf-8",
    )

    with pytest.raises(backup_db.BackupError, match="exclude_tables"):
        backup_db.load_backup_config(config_path)


def test_run_dump_passes_exclude_tables(tmp_path: Path) -> None:
    destination = tmp_path / "app.sql.gz"
    destination.write_bytes(b"archive")
    target = backup_db.BackupTarget(
        engine="mysql",
        connection="mysql.example",
        connection_name="primary_mysql",
        database="app",
        dsn="mysql+pymysql://user:password@mysql.example/app",
        enabled=True,
        exclude_tables=("ops_system_logs", "usage_logs"),
    )

    with (
        patch.object(
            backup_db,
            "mysql_connection_from_dsn",
            return_value=("mysql.example", 3306, "user", "password", "app"),
        ),
        patch.object(backup_db, "resolve_mysql_dump_options", return_value=object()) as resolve,
        patch.object(backup_db, "dump_mysql_database", return_value=destination),
    ):
        backup_db.run_dump(_settings(), target, destination)

    overrides = resolve.call_args.args[1]
    assert overrides.archive is True
    assert overrides.exclude_tables == ("ops_system_logs", "usage_logs")


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
        patch.object(backup_db, "load_backup_settings", return_value=_settings()),
        patch.object(backup_db, "run_connection_test", return_value=True) as run,
    ):
        assert backup_db.run_tests(args) == 0

    assert run.call_args_list == [
        ((_settings(), "mysql+pymysql://user:password@mysql.example:3307/", 7),),
        ((_settings(), "postgresql+psycopg://user:password@postgres.example/", 7),),
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


def test_sync_parser_accepts_a_config_path(tmp_path: Path) -> None:
    config_path = tmp_path / "backup-db.yaml"

    args = backup_db.build_parser().parse_args(["sync", "--config", str(config_path)])

    assert args.command == "sync"
    assert args.config == config_path


def test_list_connection_databases_uses_the_engine_specific_catalog() -> None:
    connection = backup_db.BackupConnection(
        name="primary_mysql",
        dsn="mysql+pymysql://user:password@mysql.example:3307/",
    )

    with patch.object(backup_db, "list_mysql_databases", return_value=("app", "mysql")) as list_db:
        assert backup_db.list_connection_databases(connection, "mysql") == ("app", "mysql")

    assert list_db.call_count == 1


def _sync_config() -> str:
    return (
        "output_directory: backups\n"
        "target_validation:\n"
        "  connection_timeout_seconds: 10\n"
        "connections:\n"
        "  - name: primary_mysql\n"
        "    dsn: 'mysql+pymysql://user:password@mysql.example:3307/'\n"
        "    databases:\n"
        "      - name: retained\n"
        "        enabled: false\n"
        "        exclude_tables:\n"
        "          - audit_log\n"
        "      - name: stale\n"
        "        enabled: true\n"
        "  - name: primary_postgres\n"
        "    dsn: 'postgresql+psycopg://user:password@postgres.example/'\n"
        "    databases:\n"
        "      - name: audit\n"
        "        enabled: false\n"
        "      - name: stale_postgres\n"
        "        enabled: true\n"
    )


def test_run_sync_adds_enabled_databases_removes_stale_entries_and_is_idempotent(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "backup-db.yaml"
    config_path.write_text(_sync_config(), encoding="utf-8")
    args = backup_db.build_parser().parse_args(["sync", "--config", str(config_path)])

    with patch.object(
        backup_db,
        "list_connection_databases",
        side_effect=(
            ("information_schema", "retained", "new_mysql", "mysql", "sys"),
            ("postgres", "template0", "audit", "new_postgres"),
        ),
    ):
        assert backup_db.run_sync(args) == 0

    synchronized = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert synchronized["connections"][0]["databases"] == [
        {"name": "retained", "enabled": False, "exclude_tables": ["audit_log"]},
        {"name": "new_mysql", "enabled": True},
    ]
    assert synchronized["connections"][1]["databases"] == [
        {"name": "audit", "enabled": False},
        {"name": "new_postgres", "enabled": True},
    ]

    before_second_sync = config_path.read_text(encoding="utf-8")
    with patch.object(
        backup_db,
        "list_connection_databases",
        side_effect=(
            ("information_schema", "retained", "new_mysql", "mysql", "sys"),
            ("postgres", "template0", "audit", "new_postgres"),
        ),
    ):
        assert backup_db.run_sync(args) == 0
    assert config_path.read_text(encoding="utf-8") == before_second_sync


def test_run_sync_does_not_write_the_config_when_a_catalog_list_fails(tmp_path: Path) -> None:
    config_path = tmp_path / "backup-db.yaml"
    config_path.write_text(_sync_config(), encoding="utf-8")
    original = config_path.read_text(encoding="utf-8")
    args = backup_db.build_parser().parse_args(["sync", "--config", str(config_path)])

    with (
        patch.object(
            backup_db,
            "list_connection_databases",
            side_effect=(("retained",), backup_db.BackupError("unreachable")),
        ),
        pytest.raises(backup_db.BackupError, match="unreachable"),
    ):
        backup_db.run_sync(args)

    assert config_path.read_text(encoding="utf-8") == original
