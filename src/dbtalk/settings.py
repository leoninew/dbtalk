"""Typed Dynaconf settings for database operations."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dynaconf import Dynaconf

DEFAULT_MYSQL_PORT = 3306
ENV_PREFIX = "DBTALK"
ENV_SELECTOR = f"{ENV_PREFIX}_ENVKEY"


@dataclass(frozen=True)
class LoggingSettings:
    level: str
    format: str


@dataclass(frozen=True)
class DumpRestoreConfig:
    output_directory: str
    client_image: str


@dataclass(frozen=True)
class MySQLConfig(DumpRestoreConfig):
    zero_datetime_as_null: bool


@dataclass(frozen=True)
class DatabaseTransferConfig:
    query_timeout_seconds: int
    exec_timeout_seconds: int


@dataclass(frozen=True)
class Settings:
    verbose: bool
    logging: LoggingSettings
    mysql: MySQLConfig
    database: DatabaseTransferConfig
    postgres: DumpRestoreConfig


def default_project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def load_settings(project_root: Path | None = None) -> Settings:
    """Load YAML, a selected dotenv file, and ``DBTALK_*`` overrides."""
    root = (project_root or default_project_root()).resolve()
    environment = os.environ.get(ENV_SELECTOR)
    dotenv_path = root / f".env.{environment}" if environment else None
    settings_files = bundled_settings_files() if project_root is None else []
    config_path = root / "dbtalk.yaml"
    if config_path.is_file() or not settings_files:
        settings_files.append(config_path)
    config = Dynaconf(
        settings_files=[str(path) for path in settings_files],
        envvar_prefix=ENV_PREFIX,
        load_dotenv=dotenv_path is not None,
        dotenv_path=str(dotenv_path) if dotenv_path else None,
        environments=False,
    )
    return Settings(
        verbose=bool_config(config.get("verbose", False)),
        logging=load_logging_settings(config.get("logging")),
        mysql=load_mysql_config(config.get("mysql")),
        database=load_database_transfer_config(config.get("database")),
        postgres=load_dump_restore_config(
            config.get("postgres"),
            group="postgres",
        ),
    )


def bundled_settings_files() -> list[Path]:
    """Return the default configuration embedded by PyInstaller, when present."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or not isinstance(bundle_root, str):
        return []
    config_path = Path(bundle_root) / "dbtalk.yaml"
    return [config_path] if config_path.is_file() else []


def mapping_config(value: Any) -> Mapping[str, object]:
    if value is None:
        return {}
    assert isinstance(value, Mapping)
    return value


def int_config(value: object) -> int:
    if isinstance(value, int):
        return value
    assert isinstance(value, str)
    return int(value)


def bool_config(value: object) -> bool:
    if isinstance(value, bool):
        return value
    assert isinstance(value, str)
    return value.lower() in {"1", "true", "yes", "on"}


def load_logging_settings(value: Any) -> LoggingSettings:
    config = mapping_config(value)
    level = config.get("level", "INFO")
    log_format = config.get("format", "%(asctime)s %(levelname)s %(name)s: %(message)s")
    assert isinstance(level, str)
    assert isinstance(log_format, str)
    return LoggingSettings(level=level, format=log_format)


def load_dump_restore_config(
    value: Any,
    *,
    group: str,
) -> DumpRestoreConfig:
    config = mapping_config(value)
    output_directory = config.get("output_directory", "data")
    client_image = config.get("client_image")
    if not isinstance(output_directory, str) or not output_directory.strip():
        raise ValueError(f"{group}.output_directory must not be empty")
    if not isinstance(client_image, str) or not client_image.strip():
        raise ValueError(f"{group}.client_image must not be empty")
    return DumpRestoreConfig(
        output_directory=output_directory,
        client_image=client_image,
    )


def load_mysql_config(value: Any) -> MySQLConfig:
    config = mapping_config(value)
    dump_restore = load_dump_restore_config(config, group="mysql")
    return MySQLConfig(
        output_directory=dump_restore.output_directory,
        client_image=dump_restore.client_image,
        zero_datetime_as_null=bool_config(config.get("zero_datetime_as_null", True)),
    )


def load_database_transfer_config(value: Any) -> DatabaseTransferConfig:
    config = mapping_config(value)
    return DatabaseTransferConfig(
        query_timeout_seconds=load_positive_seconds(config, "query_timeout_seconds"),
        exec_timeout_seconds=load_positive_seconds(config, "exec_timeout_seconds"),
    )


def load_positive_seconds(config: Mapping[str, object], key: str) -> int:
    config_key = f"database.{key}"
    raw_timeout_seconds = config.get(key)
    if raw_timeout_seconds is None:
        raise ValueError(f"{config_key} must be configured")
    try:
        timeout_seconds = int_config(raw_timeout_seconds)
    except (AssertionError, ValueError) as error:
        raise ValueError(f"{config_key} must be a positive integer") from error
    if timeout_seconds <= 0:
        raise ValueError(f"{config_key} must be greater than zero")
    return timeout_seconds
