"""Focused tests for the database backup helper script."""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import replace
from datetime import datetime
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


def _backup_run_config(output_directory: Path) -> Any:
    postgres = _postgres_dump_target()
    return backup_db.BackupConfig(
        output_directory=output_directory,
        target_validation_connection_timeout_seconds=10,
        connections=(),
        targets=(
            postgres,
            replace(postgres, connection_name="replica_postgres"),
            replace(
                postgres,
                engine="mysql",
                connection_name="primary_mysql",
                dsn="mysql+pymysql://user:password@mysql.example/app",
            ),
            replace(postgres, database="disabled", enabled=False),
        ),
    )


def test_backups_group_databases_and_preserve_previous_runs_in_the_same_second(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    output_directory = tmp_path / "backups"
    config = _backup_run_config(output_directory)
    args = backup_db.build_parser().parse_args(["backup"])

    def dump(settings: Settings, target: Any, destination: Path) -> None:
        destination.write_bytes(target.connection_name.encode())

    with (
        patch.object(backup_db, "load_backup_config", return_value=config),
        patch.object(backup_db, "load_backup_settings", return_value=_settings()),
        patch.object(backup_db, "datetime") as clock,
        patch.object(backup_db, "run_dump", side_effect=dump) as run_dump,
        caplog.at_level(logging.INFO),
    ):
        clock.now.return_value = datetime(2026, 10, 1, 15, 0, 0)
        assert backup_db.run_backups(args) == 0
        assert backup_db.run_backups(args) == 0

    assert run_dump.call_count == 6
    assert (
        caplog.messages.count("backup run completed targets=4 succeeded=3 failed=0 skipped=1") == 2
    )
    for timestamp in ("20261001-150000", "20261001-150000-01"):
        assert (
            output_directory / "primary_postgres_app" / f"{timestamp}.dump"
        ).read_bytes() == b"primary_postgres"
        assert (
            output_directory / "replica_postgres_app" / f"{timestamp}.dump"
        ).read_bytes() == b"replica_postgres"
        assert (
            output_directory / "primary_mysql_app" / f"{timestamp}.sql.gz"
        ).read_bytes() == b"primary_mysql"
        manifest_path = output_directory / "manifests" / f"{timestamp}.md"
        manifest = manifest_path.read_text(encoding="utf-8")
        assert f"`../primary_postgres_app/{timestamp}.dump`" in manifest
        assert f"`../primary_mysql_app/{timestamp}.sql.gz`" in manifest
        assert "Successful backups: `3`" in manifest
        assert "password" not in manifest
    assert {path.name for path in output_directory.iterdir()} == {
        "primary_postgres_app",
        "replica_postgres_app",
        "primary_mysql_app",
        "manifests",
    }


def test_resume_reuses_non_empty_backups_and_retries_empty_or_missing_files(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = _backup_run_config(tmp_path)
    postgres_backup = tmp_path / "primary_postgres_app" / "20261001-150000.dump"
    postgres_backup.parent.mkdir()
    postgres_backup.write_bytes(b"existing archive")
    mysql_backup = tmp_path / "primary_mysql_app" / "20261001-150000.sql.gz"
    mysql_backup.parent.mkdir()
    mysql_backup.touch()
    manifest_path = tmp_path / "manifests" / "20261001-150000.md"
    manifest_path.parent.mkdir()
    manifest_path.write_text("previous manifest", encoding="utf-8")
    args = backup_db.build_parser().parse_args(["backup", "--resume", "20261001-150000"])

    def dump(settings: Settings, target: Any, destination: Path) -> None:
        destination.write_bytes(b"new archive")

    with (
        patch.object(backup_db, "load_backup_config", return_value=config),
        patch.object(backup_db, "load_backup_settings", return_value=_settings()),
        patch.object(backup_db, "run_dump", side_effect=dump) as run_dump,
        caplog.at_level(logging.INFO),
    ):
        assert backup_db.run_backups(args) == 0

    assert run_dump.call_count == 2
    assert "backup run completed targets=4 succeeded=3 failed=0 skipped=1" in caplog.messages
    assert postgres_backup.read_bytes() == b"existing archive"
    assert mysql_backup.read_bytes() == b"new archive"
    assert (tmp_path / "replica_postgres_app" / "20261001-150000.dump").read_bytes() == (
        b"new archive"
    )
    manifest = manifest_path.read_text(encoding="utf-8")
    assert "| `app` | Reused | `../primary_postgres_app/20261001-150000.dump`" in manifest
    assert "| `app` | Succeeded | `../primary_mysql_app/20261001-150000.sql.gz`" in manifest
    assert "Successful backups: `3`" in manifest
    assert list(manifest_path.parent.iterdir()) == [manifest_path]


def test_continue_on_error_writes_the_manifest_and_remaining_database_backups(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = _backup_run_config(tmp_path)
    args = backup_db.build_parser().parse_args(["backup", "--continue-on-error"])

    def dump(settings: Settings, target: Any, destination: Path) -> None:
        if target.connection_name == "primary_postgres":
            raise backup_db.BackupError("unreachable")
        destination.write_bytes(b"archive")

    with (
        patch.object(backup_db, "load_backup_config", return_value=config),
        patch.object(backup_db, "load_backup_settings", return_value=_settings()),
        patch.object(backup_db, "datetime") as clock,
        patch.object(backup_db, "run_dump", side_effect=dump),
        caplog.at_level(logging.INFO),
    ):
        clock.now.return_value = datetime(2026, 10, 1, 15, 0, 0)
        assert backup_db.run_backups(args) == 1

    assert "backup run completed targets=4 succeeded=2 failed=1 skipped=1" in caplog.messages
    assert (tmp_path / "replica_postgres_app" / "20261001-150000.dump").is_file()
    assert (tmp_path / "primary_mysql_app" / "20261001-150000.sql.gz").is_file()
    manifest = (tmp_path / "manifests" / "20261001-150000.md").read_text(encoding="utf-8")
    assert "| `app` | Failed |" in manifest
    assert "`unreachable`" in manifest
    assert "Successful backups: `2`" in manifest
    assert "Failed backups: `1`" in manifest


def test_a_new_run_preserves_backups_left_by_an_interrupted_run(tmp_path: Path) -> None:
    config = _backup_run_config(tmp_path)
    args = backup_db.build_parser().parse_args(["backup"])

    def interrupted_dump(settings: Settings, target: Any, destination: Path) -> None:
        if target.connection_name == "replica_postgres":
            raise backup_db.BackupError("interrupted")
        destination.write_bytes(b"original archive")

    def completed_dump(settings: Settings, target: Any, destination: Path) -> None:
        destination.write_bytes(b"next archive")

    with (
        patch.object(backup_db, "load_backup_config", return_value=config),
        patch.object(backup_db, "load_backup_settings", return_value=_settings()),
        patch.object(backup_db, "datetime") as clock,
    ):
        clock.now.return_value = datetime(2026, 10, 1, 15, 0, 0)
        with (
            patch.object(backup_db, "run_dump", side_effect=interrupted_dump),
            pytest.raises(backup_db.BackupError, match="interrupted"),
        ):
            backup_db.run_backups(args)
        assert not (tmp_path / "manifests").exists()
        with patch.object(backup_db, "run_dump", side_effect=completed_dump):
            assert backup_db.run_backups(args) == 0

    assert (tmp_path / "primary_postgres_app" / "20261001-150000.dump").read_bytes() == (
        b"original archive"
    )
    assert (tmp_path / "primary_postgres_app" / "20261001-150000-01.dump").read_bytes() == (
        b"next archive"
    )
    assert (tmp_path / "manifests" / "20261001-150000-01.md").is_file()


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
        "    enabled: true\n"
        "    dsn: 'mysql+pymysql://user:password@mysql.example:3307/'\n"
        "    databases:\n"
        "      - name: app\n"
        "        enabled: true\n"
        "  - name: primary_postgres\n"
        "    enabled: true\n"
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
            enabled=True,
        ),
        backup_db.BackupConnection(
            name="primary_postgres",
            dsn="postgresql+psycopg://user:password@postgres.example/",
            enabled=True,
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
        "    enabled: true\n"
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
        "    enabled: true\n"
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
        "    enabled: true\n"
        "    dsn: 'mysql+pymysql://user:password@mysql.example:3307/'\n"
        "    databases:\n"
        "      - name: app\n"
        "        enabled: true\n"
        "      - name: archive\n"
        "        enabled: false\n"
        "  - name: primary_postgres\n"
        "    enabled: true\n"
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
        "    enabled: true\n"
        "    dsn: 'mysql+pymysql://user:password@mysql.example:3307/app'\n"
        "    databases:\n"
        "      - name: app\n"
        "        enabled: true\n",
        encoding="utf-8",
    )

    with pytest.raises(backup_db.BackupError, match="must not include a database name"):
        backup_db.load_backup_config(config_path)


def test_sync_parser_accepts_a_config_path_and_continue_on_error(tmp_path: Path) -> None:
    config_path = tmp_path / "backup-db.yaml"

    args = backup_db.build_parser().parse_args(["sync", "-c", "--config", str(config_path)])

    assert args.command == "sync"
    assert args.config == config_path
    assert args.continue_on_error is True


def test_list_connection_databases_uses_the_engine_specific_catalog() -> None:
    connection = backup_db.BackupConnection(
        name="primary_mysql",
        dsn="mysql+pymysql://user:password@mysql.example:3307/",
        enabled=True,
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
        "    enabled: true\n"
        "    dsn: 'mysql+pymysql://user:password@mysql.example:3307/'\n"
        "    databases:\n"
        "      - name: retained\n"
        "        enabled: false\n"
        "        exclude_tables:\n"
        "          - audit_log\n"
        "      - name: stale\n"
        "        enabled: true\n"
        "  - name: primary_postgres\n"
        "    enabled: true\n"
        "    dsn: 'postgresql+psycopg://user:password@postgres.example/'\n"
        "    databases:\n"
        "      - name: audit\n"
        "        enabled: false\n"
        "      - name: stale_postgres\n"
        "        enabled: true\n"
    )


@pytest.mark.parametrize("command", ["backup", "test", "sync"])
@pytest.mark.parametrize("connection_enabled", [False, None])
def test_commands_skip_connections_that_are_not_enabled(
    tmp_path: Path, command: str, connection_enabled: bool | None
) -> None:
    config_path = tmp_path / "backup-db.yaml"
    config = yaml.safe_load(_sync_config())
    disabled_connection = config["connections"][1]
    if connection_enabled is None:
        del disabled_connection["enabled"]
    else:
        disabled_connection["enabled"] = connection_enabled
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    loaded = backup_db.load_backup_config(config_path)
    assert loaded.connections[0].enabled is True
    assert loaded.connections[1].enabled is False
    assert [target.database for target in loaded.targets if target.enabled] == ["stale"]
    args = backup_db.build_parser().parse_args([command])
    args.config = config_path

    def dump(settings: Settings, target: Any, destination: Path) -> None:
        destination.write_bytes(b"archive")

    runner = {
        "backup": backup_db.run_backups,
        "test": backup_db.run_tests,
        "sync": backup_db.run_sync,
    }[command]
    with (
        patch.object(backup_db, "load_backup_settings", return_value=_settings()),
        patch.object(backup_db, "run_dump", side_effect=dump) as run_dump,
        patch.object(backup_db, "run_connection_test", return_value=True) as run_test,
        patch.object(
            backup_db,
            "list_connection_databases",
            return_value=("retained", "stale", "new_mysql"),
        ) as list_databases,
    ):
        assert runner(args) == 0

    assert run_dump.call_count + run_test.call_count + list_databases.call_count == 1
    if command == "backup":
        assert run_dump.call_args.args[1].connection_name == "primary_mysql"
        assert {path.name for path in loaded.output_directory.iterdir()} == {
            "primary_mysql_stale",
            "manifests",
        }
    elif command == "test":
        run_test.assert_called_once_with(_settings(), loaded.connections[0].dsn, 10)
    else:
        list_databases.assert_called_once_with(loaded.connections[0], "mysql")
        synchronized = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert synchronized["connections"][1] == disabled_connection
        assert synchronized["connections"][0]["databases"][-1] == {
            "name": "new_mysql",
            "enabled": True,
        }


@pytest.mark.parametrize("command", ["backup", "test", "sync"])
def test_commands_succeed_without_database_operations_when_all_connections_are_disabled(
    tmp_path: Path, command: str
) -> None:
    config_path = tmp_path / "backup-db.yaml"
    config = yaml.safe_load(_sync_config())
    for connection in config["connections"]:
        connection["enabled"] = False
    original = yaml.safe_dump(config)
    config_path.write_text(original, encoding="utf-8")
    args = backup_db.build_parser().parse_args([command])
    args.config = config_path
    runner = {
        "backup": backup_db.run_backups,
        "test": backup_db.run_tests,
        "sync": backup_db.run_sync,
    }[command]
    with (
        patch.object(backup_db, "load_backup_settings") as settings,
        patch.object(backup_db, "run_dump") as run_dump,
        patch.object(backup_db, "run_connection_test") as run_test,
        patch.object(backup_db, "list_connection_databases") as list_databases,
    ):
        assert runner(args) == 0

    settings.assert_not_called()
    run_dump.assert_not_called()
    run_test.assert_not_called()
    list_databases.assert_not_called()
    assert config_path.read_text(encoding="utf-8") == original


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


def test_run_sync_continues_after_a_catalog_failure_and_writes_successful_changes(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "backup-db.yaml"
    config_path.write_text(_sync_config(), encoding="utf-8")
    args = backup_db.build_parser().parse_args(["sync", "-c", "--config", str(config_path)])

    with patch.object(
        backup_db,
        "list_connection_databases",
        side_effect=(backup_db.BackupError("unreachable"), ("audit", "new_postgres")),
    ) as list_databases:
        assert backup_db.run_sync(args) == 1

    assert list_databases.call_count == 2
    synchronized = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert synchronized["connections"][0]["databases"] == [
        {"name": "retained", "enabled": False, "exclude_tables": ["audit_log"]},
        {"name": "stale", "enabled": True},
    ]
    assert synchronized["connections"][1]["databases"] == [
        {"name": "audit", "enabled": False},
        {"name": "new_postgres", "enabled": True},
    ]


def test_run_sync_continue_on_error_returns_failure_without_rewriting_unchanged_config(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "backup-db.yaml"
    config_path.write_text(_sync_config(), encoding="utf-8")
    original = config_path.read_text(encoding="utf-8")
    args = backup_db.build_parser().parse_args(["sync", "-c", "--config", str(config_path)])

    with patch.object(
        backup_db,
        "list_connection_databases",
        side_effect=(backup_db.BackupError("unreachable"), ("audit", "stale_postgres")),
    ):
        assert backup_db.run_sync(args) == 1

    assert config_path.read_text(encoding="utf-8") == original
