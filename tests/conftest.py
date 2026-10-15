"""Test composition: explicit settings and owned SQLite memory databases."""

import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack

import pytest

from dbtalk import cli as cli_module
from dbtalk.settings import (
    DatabaseTransferConfig,
    DumpRestoreConfig,
    LoggingSettings,
    MySQLConfig,
    SessionConfig,
    Settings,
    load_settings,
)
from tests.sqlite_helpers import MemoryDatabase, memory_database


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        logging=LoggingSettings(
            level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s"
        ),
        mysql=MySQLConfig(
            output_directory="data", client_image="mysql:8.0.39", zero_datetime_as_null=True
        ),
        database=DatabaseTransferConfig(query_timeout_seconds=30, exec_timeout_seconds=30),
        postgres=DumpRestoreConfig(output_directory="data", client_image="postgres:18-alpine"),
        session=SessionConfig(poll_interval_seconds=0.1),
    )


@pytest.fixture(autouse=True)
def cli_settings(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    # Most command tests exercise CLI behavior with already validated configuration.
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)


@pytest.fixture
def real_cli_settings(cli_settings: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt into real configuration loading when testing its CLI integration."""
    for name in os.environ:
        if name.startswith("DBTALK_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(cli_module, "load_settings", load_settings)


@pytest.fixture
def sqlite_database() -> Iterator[Callable[[], MemoryDatabase]]:
    with ExitStack() as stack:

        def create() -> MemoryDatabase:
            return stack.enter_context(memory_database())

        yield create
