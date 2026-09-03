"""Tests for dbtalk configuration sources."""

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from dbtalk.settings import DumpRestoreConfig, MySQLConfig, load_settings

DEFAULT_SETTINGS = """verbose: false
logging:
  level: INFO
  format: "%(asctime)s %(levelname)s %(name)s: %(message)s"
mysql:
  output_directory: data
  client_image: mysql:8.0.39
  zero_datetime_as_null: true
database:
  query_timeout_seconds: 30
  exec_timeout_seconds: 30
postgres:
  output_directory: data
  client_image: postgres:18-alpine
"""


@pytest.fixture(autouse=True)
def isolate_cli_environment() -> Iterator[None]:
    """Keep DBTALK configuration environment variables isolated across tests."""
    original = {key: value for key, value in os.environ.items() if key.startswith("DBTALK_")}
    clear_cli_environment()
    try:
        yield
    finally:
        clear_cli_environment()
        os.environ.update(original)


def test_loads_yaml_settings(tmp_path: Path) -> None:
    write_settings(tmp_path)

    settings = load_settings(tmp_path)

    assert settings.mysql.output_directory == "data"
    assert settings.mysql.client_image == "mysql:8.0.39"
    assert type(settings.mysql) is MySQLConfig
    assert isinstance(settings.mysql, DumpRestoreConfig)
    assert type(settings.postgres) is DumpRestoreConfig
    assert settings.mysql.zero_datetime_as_null is True
    assert not hasattr(settings.postgres, "zero_datetime_as_null")
    assert not hasattr(settings.database, "zero_datetime_as_null")
    assert settings.database.query_timeout_seconds == 30
    assert settings.database.exec_timeout_seconds == 30
    assert settings.postgres.output_directory == "data"
    assert settings.postgres.client_image == "postgres:18-alpine"
    assert settings.logging.level == "INFO"
    assert settings.logging.format == "%(asctime)s %(levelname)s %(name)s: %(message)s"


def test_mysql_dump_restore_settings_do_not_expose_connection_or_target_fields(
    tmp_path: Path,
) -> None:
    write_settings(tmp_path)
    (tmp_path / "dbtalk.yaml").write_text(
        (tmp_path / "dbtalk.yaml")
        .read_text(encoding="utf-8")
        .replace(
            "  client_image: mysql:8.0.39\n",
            "  client_image: mysql:8.0.39\n  host: db.example.test\n  port: 3307\n"
            "  user: backup\n  password: secret\n  database: app\n",
        ),
        encoding="utf-8",
    )

    settings = load_settings(tmp_path)

    assert not hasattr(settings.mysql, "host")
    assert not hasattr(settings.mysql, "port")
    assert not hasattr(settings.mysql, "user")
    assert not hasattr(settings.mysql, "password")
    assert not hasattr(settings.mysql, "database")


def test_settings_do_not_load_dotenv_files(tmp_path: Path) -> None:
    write_settings(tmp_path)
    for dotenv_name in (".env", ".env.local"):
        (tmp_path / dotenv_name).write_text(
            "DBTALK_MYSQL__CLIENT_IMAGE=dotenv.example/mysql:9\n",
            encoding="utf-8",
        )

    settings = load_settings(tmp_path)

    assert settings.mysql.client_image == "mysql:8.0.39"


def test_os_environment_overrides_yaml(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    write_settings(tmp_path)
    monkeypatch.setenv("DBTALK_MYSQL__CLIENT_IMAGE", "os.example/mysql:9")

    settings = load_settings(tmp_path)

    assert settings.mysql.client_image == "os.example/mysql:9"


def test_dump_restore_directory_and_image_can_be_overridden(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    write_settings(tmp_path)
    monkeypatch.setenv("DBTALK_MYSQL__OUTPUT_DIRECTORY", "backups/mysql")
    monkeypatch.setenv("DBTALK_MYSQL__CLIENT_IMAGE", "registry.example/mysql:9")
    monkeypatch.setenv("DBTALK_MYSQL__ZERO_DATETIME_AS_NULL", "false")
    monkeypatch.setenv("DBTALK_POSTGRES__OUTPUT_DIRECTORY", "backups/postgres")
    monkeypatch.setenv("DBTALK_POSTGRES__CLIENT_IMAGE", "registry.example/postgres:19")

    settings = load_settings(tmp_path)

    assert settings.mysql.output_directory == "backups/mysql"
    assert settings.mysql.client_image == "registry.example/mysql:9"
    assert settings.mysql.zero_datetime_as_null is False
    assert settings.postgres.output_directory == "backups/postgres"
    assert settings.postgres.client_image == "registry.example/postgres:19"


def test_database_query_and_exec_timeouts_can_be_overridden(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    write_settings(tmp_path)
    monkeypatch.setenv("DBTALK_DATABASE__QUERY_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("DBTALK_DATABASE__EXEC_TIMEOUT_SECONDS", "45")

    settings = load_settings(tmp_path)

    assert settings.database.query_timeout_seconds == 15
    assert settings.database.exec_timeout_seconds == 45


def test_postgres_client_image_can_be_overridden(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    write_settings(tmp_path)
    monkeypatch.setenv("DBTALK_POSTGRES__CLIENT_IMAGE", "registry.example/postgres:19")

    settings = load_settings(tmp_path)

    assert settings.postgres.client_image == "registry.example/postgres:19"


def test_frozen_binary_loads_embedded_config_and_executable_overrides(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    bundle_root = tmp_path / "bundle"
    executable_root = tmp_path / "release"
    bundle_root.mkdir()
    executable_root.mkdir()
    write_settings(bundle_root)
    (bundle_root / "dbtalk.yaml").write_text(
        DEFAULT_SETTINGS.replace("query_timeout_seconds: 30", "query_timeout_seconds: 61"),
        encoding="utf-8",
    )
    (executable_root / "dbtalk.yaml").write_text(
        "mysql:\n  client_image: executable.example/mysql:9\n",
        encoding="utf-8",
    )
    executable = executable_root / "dbtalk.exe"
    executable.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setenv("DBTALK_POSTGRES__CLIENT_IMAGE", "os.example/postgres:19")

    settings = load_settings()

    assert settings.mysql.client_image == "executable.example/mysql:9"
    assert settings.database.query_timeout_seconds == 61
    assert settings.database.exec_timeout_seconds == 30
    assert settings.postgres.client_image == "os.example/postgres:19"


def write_settings(path: Path) -> None:
    path.joinpath("dbtalk.yaml").write_text(DEFAULT_SETTINGS, encoding="utf-8")


def clear_cli_environment() -> None:
    for key in list(os.environ):
        if key.startswith("DBTALK_"):
            del os.environ[key]
