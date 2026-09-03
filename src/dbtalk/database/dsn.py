"""SQLAlchemy-style DSN parsing and safe connection metadata."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from dotenv import dotenv_values
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from .models import DatabaseDriver, DatabaseOperationError

SYNC_DRIVERS = {
    "sqlite": "sqlite",
    "mysql": "mysql+pymysql",
    "postgresql": "postgresql+psycopg",
}
ASYNC_DRIVERS = {
    "sqlite": "sqlite+aiosqlite",
    "mysql": "mysql+asyncmy",
    "postgresql": "postgresql+psycopg",
}
DOTENV_DSN_PREFIX = "DBTALK_DSN_"


@dataclass(frozen=True)
class ParsedDsn:
    """Validated SQLAlchemy URL and its supported database family."""

    url: URL
    dialect: DatabaseDriver
    async_mode: bool

    @property
    def display(self) -> str:
        """Return a password-redacted URL for logs and diagnostics."""

        return self.url.render_as_string(hide_password=True)

    @property
    def database(self) -> str | None:
        return self.url.database or None

    @property
    def host(self) -> str | None:
        return self.url.host

    @property
    def port(self) -> int | None:
        try:
            return self.url.port
        except ValueError as error:
            raise DatabaseOperationError("DSN port is invalid") from error


def parse_dsn(value: str, *, async_mode: bool = False) -> ParsedDsn:
    """Parse and normalize a supported SQLAlchemy-style DSN."""

    if not isinstance(value, str) or not value.strip():
        raise DatabaseOperationError("DSN must not be empty")
    try:
        url = make_url(value)
    except (ArgumentError, ValueError) as error:
        raise DatabaseOperationError("DSN is invalid") from error

    driver_name = url.drivername
    backend, separator, driver = driver_name.partition("+")
    if backend not in SYNC_DRIVERS:
        raise DatabaseOperationError(f"unsupported database dialect: {backend}")
    dialect = cast(DatabaseDriver, backend)
    if not separator:
        if dialect != "sqlite":
            raise DatabaseOperationError(
                f"{dialect} DSN must specify an explicit driver; use {SYNC_DRIVERS[dialect]}://"
            )
        driver = "aiosqlite" if async_mode else "pysqlite"
    _validate_driver(dialect, driver, async_mode=async_mode)
    if dialect == "sqlite" and not url.database:
        raise DatabaseOperationError("sqlite DSN must include a database path")
    _validate_port(url)

    target_driver = (ASYNC_DRIVERS if async_mode else SYNC_DRIVERS)[dialect]
    if url.drivername != target_driver:
        url = url.set(drivername=target_driver)
    return ParsedDsn(url=url, dialect=dialect, async_mode=async_mode)


def dsn_from_environment(environment_name: str | None, *, async_mode: bool = False) -> ParsedDsn:
    """Load a DSN from the environment or the current directory's ``.env`` file."""

    if not environment_name:
        raise DatabaseOperationError("--dsn-env is required")
    value = os.environ.get(environment_name)
    if value is None and environment_name.startswith(DOTENV_DSN_PREFIX):
        value = dotenv_values(Path.cwd() / ".env").get(environment_name)
    if not value:
        raise DatabaseOperationError("DSN environment variable is not set")
    return parse_dsn(value, async_mode=async_mode)


def sqlite_dsn(path: Path) -> str:
    """Create a canonical SQLite DSN from a filesystem path."""

    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise DatabaseOperationError(f"SQLite database does not exist: {resolved}")
    if resolved.is_dir():
        raise DatabaseOperationError(f"SQLite database path is a directory: {resolved}")
    return str(URL.create("sqlite", database=str(resolved)))


def dsn_metadata(parsed: ParsedDsn) -> dict[str, str | int | None]:
    """Return non-sensitive connection metadata for logs."""

    return {
        "dialect": parsed.dialect,
        "host": parsed.host,
        "port": parsed.port,
        "database": parsed.database,
    }


def _validate_driver(dialect: DatabaseDriver, driver: str, *, async_mode: bool) -> None:
    allowed = {
        "sqlite": {"aiosqlite"} if async_mode else {"pysqlite"},
        "mysql": {"asyncmy"} if async_mode else {"pymysql"},
        "postgresql": {"psycopg"},
    }[dialect]
    if driver not in allowed:
        raise DatabaseOperationError(
            f"unsupported {dialect} driver {driver!r}; "
            f"supported drivers are {', '.join(sorted(allowed))}"
        )


def _validate_port(url: URL) -> None:
    try:
        port = url.port
    except ValueError as error:
        raise DatabaseOperationError("DSN port is invalid") from error
    if port is not None and not 1 <= port <= 65535:
        raise DatabaseOperationError("DSN port must be between 1 and 65535")
