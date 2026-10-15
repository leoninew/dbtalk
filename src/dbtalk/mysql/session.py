"""MySQL database-scoped client session management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import click
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from dbtalk.cli_runtime import DbtalkGroup
from dbtalk.context import dbtalk_context
from dbtalk.database.dsn import ParsedDsn
from dbtalk.database.models import DatabaseOperationError, QueryResult
from dbtalk.database.operations import render_query
from dbtalk.database.sessions import (
    SessionKillReport,
    SessionKillResult,
    SessionToken,
    command_session_target,
    configure_timeout,
    connection_lost,
    failure_detail,
    finish_report,
    logger,
    render_kill_report,
    resolve_session_target,
    session_connection,
    validate_kill_options,
    wait_for_sessions,
    wait_option,
)
from dbtalk.settings import Settings

_CLIENT_WHERE = (
    "PROCESSLIST_DB = :database AND TYPE = 'FOREGROUND' "
    "AND NAME = 'thread/sql/one_connection' AND PROCESSLIST_ID IS NOT NULL "
    "AND PROCESSLIST_ID <> CONNECTION_ID() "
    "AND PROCESSLIST_USER NOT IN ('system user', 'event_scheduler') "
    "AND (PROCESSLIST_COMMAND IS NULL OR PROCESSLIST_COMMAND NOT IN "
    "('Binlog Dump', 'Binlog Dump GTID', 'Daemon', 'Connect Out'))"
)
_METADATA_SQL = (
    "SELECT PROCESSLIST_ID, THREAD_ID, PROCESSLIST_DB, PROCESSLIST_USER, TYPE, NAME, "
    "PROCESSLIST_COMMAND FROM performance_schema.threads"
)
_SYSTEM_COMMANDS = {"Binlog Dump", "Binlog Dump GTID", "Daemon", "Connect Out"}


@dataclass(frozen=True)
class _Session:
    session_id: int
    thread_id: int
    database: str | None
    user: str | None
    thread_type: str
    name: str
    command: str | None

    @property
    def client(self) -> bool:
        return (
            self.thread_type == "FOREGROUND"
            and self.name == "thread/sql/one_connection"
            and self.user is not None
            and self.user not in {"system user", "event_scheduler"}
            and self.command not in _SYSTEM_COMMANDS
        )


def _record(row: RowMapping) -> _Session:
    return _Session(
        int(row["PROCESSLIST_ID"]),
        int(row["THREAD_ID"]),
        row["PROCESSLIST_DB"],
        row["PROCESSLIST_USER"],
        row["TYPE"],
        row["NAME"],
        row["PROCESSLIST_COMMAND"],
    )


CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group("session", cls=DbtalkGroup, context_settings=CONTEXT_SETTINGS)
def session() -> None:
    """List or terminate client sessions in one database."""


@session.command("list", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete mysql SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the DSN.")
@click.option("--database", help="Target database. Defaults to the DSN database.")
@click.pass_context
def list_command(
    ctx: click.Context, dsn_value: str | None, dsn_env: str | None, database: str | None
) -> None:
    parsed = command_session_target(dsn_value, dsn_env, database, "mysql")
    try:
        click.echo(
            render_query(list_sessions(parsed, settings=dbtalk_context(ctx).settings), "table")
        )
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error


@session.command("kill", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete mysql SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the DSN.")
@click.option("--database", help="Target database. Defaults to the DSN database.")
@click.option(
    "--id", "session_id", type=click.IntRange(min=1), help="One session in the target database."
)
@click.option(
    "--wait",
    "wait_seconds",
    type=float,
    metavar="SECONDS",
    callback=wait_option,
    help="Wait up to SECONDS for exit.",
)
@click.option("--yes", is_flag=True, help="Confirm client connection termination.")
@click.pass_context
def kill_command(
    ctx: click.Context,
    dsn_value: str | None,
    dsn_env: str | None,
    database: str | None,
    session_id: int | None,
    wait_seconds: float | None,
    yes: bool,
) -> None:
    if not yes:
        raise click.UsageError("--yes is required to terminate sessions")
    parsed = command_session_target(dsn_value, dsn_env, database, "mysql")
    try:
        report = kill_sessions(
            parsed,
            settings=dbtalk_context(ctx).settings,
            session_id=session_id,
            wait_seconds=wait_seconds,
        )
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(render_kill_report(report))
    if not report.successful:
        raise click.ClickException("some sessions were not terminated; see results")


def _read(connection: Connection, session_id: int) -> _Session | None:
    row = (
        connection.execute(text(_METADATA_SQL + " WHERE PROCESSLIST_ID = :id"), {"id": session_id})
        .mappings()
        .first()
    )
    return None if row is None else _record(row)


def _source_ready(connection: Connection) -> int:
    if not connection.exec_driver_sql("SELECT @@performance_schema").scalar_one():
        raise DatabaseOperationError("MySQL session management requires Performance Schema")
    own_id = int(connection.exec_driver_sql("SELECT CONNECTION_ID()").scalar_one())
    if _read(connection, own_id) is None:
        raise DatabaseOperationError("current MySQL thread identity is not visible")
    return own_id


def list_sessions(
    parsed: ParsedDsn, *, settings: Settings, database: str | None = None
) -> QueryResult:
    parsed = resolve_session_target(parsed, database, "mysql")
    with session_connection(parsed, settings) as connection:
        configure_timeout(connection, settings.database.query_timeout_seconds)
        _source_ready(connection)
        result = connection.execute(
            text(
                "SELECT PROCESSLIST_ID AS id, PROCESSLIST_DB AS `database`, "
                "PROCESSLIST_USER AS `user`, PROCESSLIST_HOST AS client, "
                "PROCESSLIST_STATE AS state, PROCESSLIST_COMMAND AS command, "
                "PROCESSLIST_TIME AS state_seconds FROM performance_schema.threads WHERE "
                + _CLIENT_WHERE
                + " ORDER BY PROCESSLIST_ID"
            ),
            {"database": parsed.database},
        )
        rows = tuple(tuple(row) for row in result.fetchall())
        logger.info("session targets_selected database=%r count=%d", parsed.database, len(rows))
        return QueryResult(tuple(result.keys()), rows)


def _kill_one(connection: Connection, target: _Session, settings: Settings) -> SessionKillResult:
    configure_timeout(connection, settings.database.query_timeout_seconds)
    current = _read(connection, target.session_id)
    if current is None:
        return SessionKillResult(target.session_id, "gone")
    if (
        current.thread_id != target.thread_id
        or current.database != target.database
        or current.user != target.user
        or not current.client
    ):
        return SessionKillResult(target.session_id, "changed", "session identity or scope changed")
    configure_timeout(connection, settings.database.exec_timeout_seconds)
    try:
        connection.execute(text("KILL CONNECTION :id"), {"id": target.session_id})
    except DBAPIError as error:
        if (
            not connection_lost(connection, error)
            and error.orig is not None
            and error.orig.args
            and error.orig.args[0] == 1094
        ):
            configure_timeout(connection, settings.database.query_timeout_seconds)
            current = _read(connection, target.session_id)
            if current is None or current.thread_id != target.thread_id:
                return SessionKillResult(target.session_id, "gone")
        raise
    return SessionKillResult(target.session_id, "requested")


def _identities(
    connection: Connection, ids: tuple[int, ...], remaining: float, settings: Settings
) -> dict[int, SessionToken | None]:
    configure_timeout(connection, min(settings.database.query_timeout_seconds, remaining))
    result = connection.execute(
        text(
            "SELECT PROCESSLIST_ID, THREAD_ID FROM performance_schema.threads "
            "WHERE PROCESSLIST_ID IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    )
    return {int(row[0]): row[1] for row in result.fetchall()}


def kill_sessions(
    parsed: ParsedDsn,
    *,
    settings: Settings,
    database: str | None = None,
    session_id: int | None = None,
    wait_seconds: float | None = None,
) -> SessionKillReport:
    parsed = resolve_session_target(parsed, database, "mysql")
    validate_kill_options(session_id, wait_seconds)
    results: list[SessionKillResult] = []
    identities: dict[int, SessionToken] = {}
    with session_connection(parsed, settings, writing=True) as connection:
        configure_timeout(connection, settings.database.query_timeout_seconds)
        own_id = _source_ready(connection)
        if session_id == own_id:
            raise DatabaseOperationError("cannot terminate the current management session")
        query = _METADATA_SQL + " WHERE " + _CLIENT_WHERE
        parameters: dict[str, object] = {"database": parsed.database}
        if session_id is not None:
            query += " AND PROCESSLIST_ID = :id"
            parameters["id"] = session_id
        targets = [
            _record(row)
            for row in connection.execute(
                text(query + " ORDER BY PROCESSLIST_ID"), parameters
            ).mappings()
        ]
        if session_id is not None and not targets:
            raise DatabaseOperationError("session not found in the target database or not visible")
        logger.info("session targets_selected database=%r count=%d", parsed.database, len(targets))
        healthy = True
        for target in targets:
            if not healthy:
                results.append(
                    SessionKillResult(target.session_id, "not_attempted", "connection lost")
                )
                continue
            try:
                result = _kill_one(connection, target, settings)
            except SQLAlchemyError as error:
                healthy = not connection_lost(connection, error)
                detail = failure_detail(error)
                if not healthy:
                    detail += "; connection lost, request outcome may be unknown"
                result = SessionKillResult(target.session_id, "failed", detail)
            results.append(result)
            if result.status == "requested":
                identities[target.session_id] = target.thread_id
        if wait_seconds is not None and identities:
            if healthy:
                results = wait_for_sessions(
                    results,
                    identities,
                    lambda ids, remaining: _identities(connection, ids, remaining, settings),
                    wait_seconds=wait_seconds,
                    poll_interval_seconds=settings.session.poll_interval_seconds,
                )
            else:
                results = [
                    SessionKillResult(
                        item.session_id, "failed", "connection lost; exit not confirmed"
                    )
                    if item.status == "requested"
                    else item
                    for item in results
                ]
    return finish_report(cast(str, parsed.database), results)
