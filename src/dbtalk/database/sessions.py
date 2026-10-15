"""Database-scoped session inputs, connection lifecycle, results, and exit polling."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from math import ceil, isfinite
from time import monotonic, sleep
from typing import Literal
from unicodedata import category

import click
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, create_engine
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from dbtalk.settings import Settings

from .dsn import ParsedDsn, dsn_from_environment, parse_dsn
from .models import DatabaseOperationError, QueryResult
from .operations import render_query

logger = logging.getLogger("dbtalk")

type SessionToken = datetime | int
type KillStatus = Literal[
    "requested", "terminated", "gone", "changed", "failed", "timeout", "not_attempted"
]
_SUCCESS = {"requested", "terminated", "gone"}


@dataclass(frozen=True)
class SessionKillResult:
    session_id: int
    status: KillStatus
    detail: str = ""


@dataclass(frozen=True)
class SessionKillReport:
    database: str
    results: tuple[SessionKillResult, ...]

    @property
    def successful(self) -> bool:
        return all(result.status in _SUCCESS for result in self.results)


def resolve_session_target(parsed: ParsedDsn, database: str | None, dialect: str) -> ParsedDsn:
    if parsed.dialect != dialect or parsed.async_mode:
        raise DatabaseOperationError(f"session management requires a synchronous {dialect} DSN")
    target = parsed.database if database is None else database
    if target is None:
        raise DatabaseOperationError("provide the target database with --database or in the DSN")
    if not target.strip() or any(category(char).startswith("C") for char in target):
        raise DatabaseOperationError(
            "target database must not be blank or contain control characters"
        )
    return replace(parsed, url=parsed.url.set(database=target))


def command_session_target(
    dsn: str | None, dsn_env: str | None, database: str | None, dialect: str
) -> ParsedDsn:
    try:
        if (dsn is None) == (dsn_env is None):
            raise DatabaseOperationError("provide exactly one of --dsn or --dsn-env")
        parsed = parse_dsn(dsn) if dsn is not None else dsn_from_environment(dsn_env)
        return resolve_session_target(parsed, database, dialect)
    except DatabaseOperationError as error:
        raise click.UsageError(str(error)) from error


def validate_kill_options(session_id: int | None, wait_seconds: float | None) -> None:
    if session_id is not None and (isinstance(session_id, bool) or session_id <= 0):
        raise DatabaseOperationError("session ID must be a positive integer")
    if wait_seconds is not None and (not isfinite(wait_seconds) or wait_seconds <= 0):
        raise DatabaseOperationError("--wait must be a finite positive number of seconds")


def wait_option(
    ctx: click.Context, parameter: click.Parameter, value: float | None
) -> float | None:
    try:
        validate_kill_options(None, value)
    except DatabaseOperationError as error:
        raise click.BadParameter(str(error), ctx=ctx, param=parameter) from error
    return value


@contextmanager
def session_connection(
    parsed: ParsedDsn, settings: Settings, *, writing: bool = False
) -> Iterator[Connection]:
    budget = (
        settings.database.exec_timeout_seconds
        if writing
        else settings.database.query_timeout_seconds
    )
    connect_args = {"connect_timeout": budget}
    if parsed.dialect == "mysql":
        connect_args.update(read_timeout=budget, write_timeout=budget)
    engine: Engine | None = None
    try:
        engine = create_engine(parsed.url, connect_args=connect_args, hide_parameters=True)
        with engine.connect() as connection:
            yield connection.execution_options(isolation_level="AUTOCOMMIT")
    except SQLAlchemyError as error:
        raise DatabaseOperationError(f"{parsed.dialect} session management failed") from error
    finally:
        if engine is not None:
            engine.dispose()


def configure_timeout(connection: Connection, seconds: float) -> None:
    milliseconds = max(1, ceil(seconds * 1000))
    if connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT set_config('statement_timeout', :timeout, false)"),
            {"timeout": f"{milliseconds}ms"},
        ).scalar_one()
    else:
        connection.exec_driver_sql(f"SET SESSION max_execution_time = {milliseconds}")


def connection_lost(connection: Connection, error: SQLAlchemyError) -> bool:
    return connection.invalidated or (
        isinstance(error, DBAPIError) and error.connection_invalidated
    )


def failure_detail(error: SQLAlchemyError) -> str:
    code: str | int | None = None
    if isinstance(error, DBAPIError) and error.orig is not None:
        candidate = getattr(error.orig, "sqlstate", None)
        if isinstance(candidate, str) and len(candidate) == 5 and candidate.isalnum():
            code = candidate
        elif error.orig.args and isinstance(error.orig.args[0], int):
            code = error.orig.args[0]
    if code in ("42501", 1095, 1142, 1227):
        return f"permission denied (code {code})"
    return "database request failed" if code is None else f"database request failed (code {code})"


def wait_for_sessions(
    results: list[SessionKillResult],
    identities: dict[int, SessionToken],
    refresh: Callable[[tuple[int, ...], float], dict[int, SessionToken | None]],
    *,
    wait_seconds: float,
    poll_interval_seconds: float,
) -> list[SessionKillResult]:
    """Confirm only original identities, using one shared exit-confirmation budget."""
    pending = dict(identities)
    updates: dict[int, SessionKillResult] = {}
    deadline = monotonic() + wait_seconds
    while pending:
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        try:
            current = refresh(tuple(pending), remaining)
        except SQLAlchemyError as error:
            status: KillStatus = "timeout" if monotonic() >= deadline else "failed"
            detail = "exit confirmation timed out" if status == "timeout" else failure_detail(error)
            for session_id in pending:
                updates[session_id] = SessionKillResult(session_id, status, detail)
            pending.clear()
            break
        if monotonic() >= deadline:
            break
        for session_id, identity in tuple(pending.items()):
            if session_id in current and current[session_id] is None:
                updates[session_id] = SessionKillResult(
                    session_id, "failed", "session identity is no longer visible"
                )
                del pending[session_id]
            elif current.get(session_id) != identity:
                updates[session_id] = SessionKillResult(session_id, "terminated")
                del pending[session_id]
        if pending:
            remaining = deadline - monotonic()
            if remaining > 0:
                sleep(min(poll_interval_seconds, remaining))
    for session_id in pending:
        updates[session_id] = SessionKillResult(
            session_id, "timeout", "exit confirmation timed out"
        )
    return [updates.get(result.session_id, result) for result in results]


def finish_report(database: str, results: list[SessionKillResult]) -> SessionKillReport:
    for result in results:
        logger.log(
            logging.INFO if result.status in _SUCCESS else logging.ERROR,
            "session result database=%r session_id=%d status=%s",
            database,
            result.session_id,
            result.status,
        )
    return SessionKillReport(database, tuple(results))


def render_kill_report(report: SessionKillReport) -> str:
    table = render_query(
        QueryResult(
            columns=("id", "status", "detail"),
            rows=tuple((item.session_id, item.status, item.detail) for item in report.results),
        ),
        "table",
    )
    counts: dict[str, int] = {}
    for result in report.results:
        counts[result.status] = counts.get(result.status, 0) + 1
    summary = ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
    return (
        f"Session termination results for database: {report.database}\n{table}\n"
        f"Matched: {len(report.results)}"
        + (f"; {summary}" if summary else "; no matching client sessions")
    )
