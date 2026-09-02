"""Synchronous and asynchronous SQLAlchemy database clients."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass
from math import ceil
from time import monotonic
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import URL, Connection, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from .dsn import ParsedDsn, parse_dsn
from .models import DatabaseOperationError, ExecutionResult, QueryResult


@dataclass(frozen=True)
class _StatementDeadline:
    """Track a client-side deadline for timeout error normalization."""

    timeout_seconds: float
    started_at: float

    @classmethod
    def start(cls, timeout_seconds: float) -> _StatementDeadline:
        return cls(timeout_seconds=timeout_seconds, started_at=monotonic())

    @property
    def expired(self) -> bool:
        return monotonic() - self.started_at >= self.timeout_seconds


class DatabaseSession:
    """A transaction-aware wrapper around one synchronous SQLAlchemy connection."""

    def __init__(
        self,
        connection: Connection,
        *,
        deadline: _StatementDeadline | None = None,
    ) -> None:
        self._connection = connection
        self._deadline = deadline

    def query(self, statement: str, parameters: Mapping[str, object] | None = None) -> QueryResult:
        try:
            result = self._connection.execute(text(statement), dict(parameters or {}))
            columns = tuple(str(key) for key in result.keys())  # noqa: SIM118
            rows = tuple(tuple(row) for row in result.fetchall())
        except SQLAlchemyError as error:
            if self._deadline is not None and self._deadline.expired:
                raise DatabaseOperationError("database query timed out") from error
            raise DatabaseOperationError("database query failed") from error
        return QueryResult(columns=columns, rows=rows)

    def execute(
        self, statement: str, parameters: Mapping[str, object] | None = None
    ) -> ExecutionResult:
        try:
            result = self._connection.execute(text(statement), dict(parameters or {}))
        except SQLAlchemyError as error:
            if self._deadline is not None and self._deadline.expired:
                raise DatabaseOperationError("database execution timed out") from error
            raise DatabaseOperationError("database execution failed") from error
        return ExecutionResult(row_count=max(result.rowcount, 0))


class DatabaseClient:
    """Public synchronous database API backed by a SQLAlchemy engine."""

    def __init__(
        self,
        dsn: str | URL | ParsedDsn,
        *,
        timeout_seconds: float | None = None,
        connect_timeout_seconds: float | None = None,
    ) -> None:
        parsed = dsn if isinstance(dsn, ParsedDsn) else parse_dsn(_dsn_string(dsn))
        if parsed.async_mode:
            raise DatabaseOperationError("an async DSN cannot create a sync client")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise DatabaseOperationError("database timeout must be greater than zero")
        if connect_timeout_seconds is not None and connect_timeout_seconds <= 0:
            raise DatabaseOperationError("database connection timeout must be greater than zero")
        self.dsn = parsed
        self._timeout_seconds = timeout_seconds
        try:
            self._engine = create_engine(
                parsed.url,
                **_engine_options(parsed, timeout_seconds, connect_timeout_seconds),
            )
        except SQLAlchemyError as error:
            raise DatabaseOperationError("database engine could not be created") from error

    @property
    def dialect(self) -> str:
        return self.dsn.dialect

    @contextmanager
    def connect(self) -> Iterator[DatabaseSession]:
        try:
            with self._engine.connect() as connection:
                yield DatabaseSession(connection)
        except DatabaseOperationError:
            raise
        except SQLAlchemyError as error:
            raise DatabaseOperationError("database connection failed") from error

    @contextmanager
    def transaction(self) -> Iterator[DatabaseSession]:
        try:
            with self._engine.begin() as connection:
                yield DatabaseSession(connection)
        except DatabaseOperationError:
            raise
        except SQLAlchemyError as error:
            raise DatabaseOperationError("database transaction failed") from error

    def query(self, statement: str, parameters: Mapping[str, object] | None = None) -> QueryResult:
        with self._query_session() as session:
            return session.query(statement, parameters)

    def execute(
        self,
        statement: str,
        parameters: Mapping[str, object] | None = None,
        *,
        read_only: bool = False,
    ) -> ExecutionResult:
        session_context = self._query_session() if read_only else self._execution_session()
        with session_context as session:
            return session.execute(statement, parameters)

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> DatabaseClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def _query_session(self) -> Iterator[DatabaseSession]:
        """Open a read-only, timeout-bound session without inspecting SQL text."""

        try:
            with (
                self._engine.connect() as connection,
                _prepared_session(
                    connection,
                    self.dsn,
                    timeout_seconds=self._timeout_seconds,
                    read_only=True,
                ) as session,
            ):
                yield session
        except DatabaseOperationError:
            raise
        except SQLAlchemyError as error:
            raise DatabaseOperationError("database connection failed") from error

    @contextmanager
    def _execution_session(self) -> Iterator[DatabaseSession]:
        """Open a write-capable, timeout-bound transaction session."""

        try:
            with (
                self._engine.begin() as connection,
                _prepared_session(
                    connection,
                    self.dsn,
                    timeout_seconds=self._timeout_seconds,
                    read_only=False,
                ) as session,
            ):
                yield session
        except DatabaseOperationError:
            raise
        except SQLAlchemyError as error:
            raise DatabaseOperationError("database transaction failed") from error


class AsyncDatabaseSession:
    """A transaction-aware wrapper around one asynchronous SQLAlchemy connection."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def query(
        self, statement: str, parameters: Mapping[str, object] | None = None
    ) -> QueryResult:
        try:
            result = await self._connection.execute(text(statement), dict(parameters or {}))
            columns = tuple(str(key) for key in result.keys())  # noqa: SIM118
            rows = tuple(tuple(row) for row in result.fetchall())
        except SQLAlchemyError as error:
            raise DatabaseOperationError("database query failed") from error
        return QueryResult(columns=columns, rows=rows)

    async def execute(
        self, statement: str, parameters: Mapping[str, object] | None = None
    ) -> ExecutionResult:
        try:
            result = await self._connection.execute(text(statement), dict(parameters or {}))
        except SQLAlchemyError as error:
            raise DatabaseOperationError("database execution failed") from error
        return ExecutionResult(row_count=max(result.rowcount, 0))


class AsyncDatabaseClient:
    """Public asynchronous database API backed by a SQLAlchemy async engine."""

    def __init__(self, dsn: str | URL | ParsedDsn) -> None:
        parsed = dsn if isinstance(dsn, ParsedDsn) else parse_dsn(_dsn_string(dsn), async_mode=True)
        if not parsed.async_mode:
            raise DatabaseOperationError("an async client requires an async DSN")
        self.dsn = parsed
        try:
            self._engine: AsyncEngine = create_async_engine(parsed.url)
        except SQLAlchemyError as error:
            raise DatabaseOperationError("async database engine could not be created") from error

    @property
    def dialect(self) -> str:
        return self.dsn.dialect

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[AsyncDatabaseSession]:
        try:
            async with self._engine.connect() as connection:
                yield AsyncDatabaseSession(connection)
        except DatabaseOperationError:
            raise
        except SQLAlchemyError as error:
            raise DatabaseOperationError("database connection failed") from error

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncDatabaseSession]:
        try:
            async with self._engine.begin() as connection:
                yield AsyncDatabaseSession(connection)
        except DatabaseOperationError:
            raise
        except SQLAlchemyError as error:
            raise DatabaseOperationError("database transaction failed") from error

    async def query(
        self, statement: str, parameters: Mapping[str, object] | None = None
    ) -> QueryResult:
        async with self.connect() as session:
            return await session.query(statement, parameters)

    async def execute(
        self, statement: str, parameters: Mapping[str, object] | None = None
    ) -> ExecutionResult:
        async with self.transaction() as session:
            return await session.execute(statement, parameters)

    async def close(self) -> None:
        await self._engine.dispose()

    async def __aenter__(self) -> AsyncDatabaseClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


@contextmanager
def _prepared_session(
    connection: Connection,
    parsed: ParsedDsn,
    *,
    timeout_seconds: float | None,
    read_only: bool,
) -> Iterator[DatabaseSession]:
    """Apply connection-level safeguards without deriving intent from SQL text."""

    deadline = _StatementDeadline.start(timeout_seconds) if timeout_seconds is not None else None
    sqlite_state: tuple[int, Any] | None = None
    try:
        if read_only:
            _begin_read_only(connection, parsed)
        if deadline is not None:
            sqlite_state = _configure_timeout(connection, parsed, deadline)
        yield DatabaseSession(connection, deadline=deadline)
    except DatabaseOperationError:
        raise
    except SQLAlchemyError as error:
        raise DatabaseOperationError("database session could not be prepared") from error
    finally:
        if sqlite_state is not None:
            _clear_sqlite_timeout(connection, sqlite_state)
        if read_only and parsed.dialect == "sqlite":
            _clear_sqlite_read_only(connection)


def _begin_read_only(connection: Connection, parsed: ParsedDsn) -> None:
    if parsed.dialect == "sqlite":
        connection.exec_driver_sql("PRAGMA query_only = ON")
    elif parsed.dialect == "mysql":
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        connection.exec_driver_sql("START TRANSACTION READ ONLY")
    elif parsed.dialect == "postgresql":
        connection.exec_driver_sql("BEGIN TRANSACTION READ ONLY")


def _configure_timeout(
    connection: Connection,
    parsed: ParsedDsn,
    deadline: _StatementDeadline,
) -> tuple[int, Any] | None:
    milliseconds = max(1, ceil(deadline.timeout_seconds * 1000))
    if parsed.dialect == "sqlite":
        previous_busy_timeout = int(connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one())
        raw_connection = _sqlite_driver_connection(connection)
        raw_connection.set_progress_handler(lambda: int(deadline.expired), 1_000)
        connection.exec_driver_sql(f"PRAGMA busy_timeout = {milliseconds}")
        return previous_busy_timeout, raw_connection
    if parsed.dialect == "mysql":
        connection.exec_driver_sql(f"SET SESSION max_execution_time = {milliseconds}")
    elif parsed.dialect == "postgresql":
        connection.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": f"{milliseconds}ms"},
        )
    return None


def _clear_sqlite_timeout(connection: Connection, state: tuple[int, Any]) -> None:
    previous_busy_timeout, raw_connection = state
    raw_connection.set_progress_handler(None, 0)
    with suppress(SQLAlchemyError):
        connection.exec_driver_sql(f"PRAGMA busy_timeout = {previous_busy_timeout}")


def _clear_sqlite_read_only(connection: Connection) -> None:
    try:
        connection.rollback()
        connection.exec_driver_sql("PRAGMA query_only = OFF")
    except SQLAlchemyError:
        pass


def _sqlite_driver_connection(connection: Connection) -> Any:
    raw_connection = connection.connection.driver_connection
    if not hasattr(raw_connection, "set_progress_handler"):
        raise DatabaseOperationError("SQLite driver does not support statement timeouts")
    return raw_connection


def _engine_options(
    parsed: ParsedDsn,
    timeout_seconds: float | None,
    connect_timeout_seconds: float | None,
) -> dict[str, object]:
    connect_args: dict[str, int] = {}
    if parsed.dialect == "mysql" and timeout_seconds is not None:
        statement_timeout = max(1, ceil(timeout_seconds))
        connect_args["read_timeout"] = statement_timeout
        connect_args["write_timeout"] = statement_timeout
    if parsed.dialect in {"mysql", "postgresql"} and connect_timeout_seconds is not None:
        connect_args["connect_timeout"] = max(1, ceil(connect_timeout_seconds))
    if not connect_args:
        return {}
    return {"connect_args": connect_args}


def create_client(
    dsn: str | URL,
    *,
    timeout_seconds: float | None = None,
    connect_timeout_seconds: float | None = None,
) -> DatabaseClient:
    """Create the public sync client from a DSN."""

    return DatabaseClient(
        dsn,
        timeout_seconds=timeout_seconds,
        connect_timeout_seconds=connect_timeout_seconds,
    )


def create_async_client(dsn: str | URL) -> AsyncDatabaseClient:
    """Create the public async client from a DSN."""

    return AsyncDatabaseClient(dsn)


def _dsn_string(dsn: str | URL) -> str:
    if isinstance(dsn, URL):
        return dsn.render_as_string(hide_password=False)
    return dsn


__all__ = [
    "AsyncDatabaseClient",
    "AsyncDatabaseSession",
    "DatabaseClient",
    "DatabaseSession",
    "create_async_client",
    "create_client",
]
