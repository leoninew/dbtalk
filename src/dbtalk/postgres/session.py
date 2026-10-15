"""PostgreSQL database-scoped client session management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

import click
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.exc import SQLAlchemyError

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
    "datname = :database AND backend_type = 'client backend' AND pid <> pg_backend_pid()"
)
_METADATA_SQL = (
    "SELECT pid, datname, usesysid, backend_start, backend_type FROM pg_catalog.pg_stat_activity"
)


@dataclass(frozen=True)
class _Session:
    session_id: int
    database: str | None
    user_id: int | None
    started: datetime | None
    kind: str | None


def _record(row: RowMapping) -> _Session:
    return _Session(
        int(row["pid"]), row["datname"], row["usesysid"], row["backend_start"], row["backend_type"]
    )


CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group("session", cls=DbtalkGroup, context_settings=CONTEXT_SETTINGS)
def session() -> None:
    """List or terminate client sessions in one database."""


@session.command("list", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete postgresql SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the DSN.")
@click.option("--database", help="Target database. Defaults to the DSN database.")
@click.pass_context
def list_command(
    ctx: click.Context, dsn_value: str | None, dsn_env: str | None, database: str | None
) -> None:
    parsed = command_session_target(dsn_value, dsn_env, database, "postgresql")
    try:
        click.echo(
            render_query(list_sessions(parsed, settings=dbtalk_context(ctx).settings), "table")
        )
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error


@session.command("kill", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete postgresql SQLAlchemy-style DSN.")
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
    parsed = command_session_target(dsn_value, dsn_env, database, "postgresql")
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


def list_sessions(
    parsed: ParsedDsn, *, settings: Settings, database: str | None = None
) -> QueryResult:
    parsed = resolve_session_target(parsed, database, "postgresql")
    with session_connection(parsed, settings) as connection:
        configure_timeout(connection, settings.database.query_timeout_seconds)
        result = connection.execute(
            text(
                "SELECT pid AS id, datname AS database, usename AS user, "
                "client_addr::text AS client, state, application_name, backend_start, "
                "wait_event_type, wait_event FROM pg_catalog.pg_stat_activity WHERE "
                + _CLIENT_WHERE
                + " ORDER BY pid"
            ),
            {"database": parsed.database},
        )
        rows = tuple(tuple(row) for row in result.fetchall())
        logger.info("session targets_selected database=%r count=%d", parsed.database, len(rows))
        return QueryResult(tuple(result.keys()), rows)


def _read(connection: Connection, session_id: int) -> _Session | None:
    row = (
        connection.execute(text(_METADATA_SQL + " WHERE pid = :id"), {"id": session_id})
        .mappings()
        .first()
    )
    return None if row is None else _record(row)


def _kill_one(connection: Connection, target: _Session, settings: Settings) -> SessionKillResult:
    configure_timeout(connection, settings.database.query_timeout_seconds)
    current = _read(connection, target.session_id)
    if current is None:
        return SessionKillResult(target.session_id, "gone")
    if current != target:
        return SessionKillResult(target.session_id, "changed", "session identity or scope changed")
    if target.started is None or target.user_id is None:
        return SessionKillResult(target.session_id, "failed", "session identity is not visible")
    configure_timeout(connection, settings.database.exec_timeout_seconds)
    accepted = connection.execute(
        text(
            "SELECT pg_terminate_backend(pid) FROM pg_catalog.pg_stat_activity "
            "WHERE pid = :id AND backend_start = :started AND datname = :database "
            "AND usesysid = :user_id AND backend_type = 'client backend' "
            "AND pid <> pg_backend_pid()"
        ),
        {
            "id": target.session_id,
            "started": target.started,
            "database": target.database,
            "user_id": target.user_id,
        },
    ).scalar()
    if accepted:
        return SessionKillResult(target.session_id, "requested")
    configure_timeout(connection, settings.database.query_timeout_seconds)
    current = _read(connection, target.session_id)
    if current is None:
        return SessionKillResult(target.session_id, "gone")
    if current != target:
        return SessionKillResult(target.session_id, "changed", "session identity or scope changed")
    return SessionKillResult(target.session_id, "failed", "termination signal was not accepted")


def _identities(
    connection: Connection, ids: tuple[int, ...], remaining: float, settings: Settings
) -> dict[int, SessionToken | None]:
    configure_timeout(connection, min(settings.database.query_timeout_seconds, remaining))
    result = connection.execute(
        text(
            "SELECT pid, backend_start FROM pg_catalog.pg_stat_activity WHERE pid IN :ids"
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
    parsed = resolve_session_target(parsed, database, "postgresql")
    validate_kill_options(session_id, wait_seconds)
    results: list[SessionKillResult] = []
    identities: dict[int, SessionToken] = {}
    with session_connection(parsed, settings, writing=True) as connection:
        configure_timeout(connection, settings.database.query_timeout_seconds)
        own_id = int(connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one())
        if session_id == own_id:
            raise DatabaseOperationError("cannot terminate the current management session")
        query = _METADATA_SQL + " WHERE " + _CLIENT_WHERE
        parameters: dict[str, object] = {"database": parsed.database}
        if session_id is not None:
            query += " AND pid = :id"
            parameters["id"] = session_id
        targets = [
            _record(row)
            for row in connection.execute(text(query + " ORDER BY pid"), parameters).mappings()
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
            if result.status == "requested" and target.started is not None:
                identities[target.session_id] = target.started
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
