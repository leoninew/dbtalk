"""PostgreSQL session termination and identity checks without external services."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

import dbtalk.postgres.session as postgres
from dbtalk.database.dsn import parse_dsn
from dbtalk.database.models import DatabaseOperationError
from dbtalk.settings import load_settings

STARTED = datetime(2026, 10, 15, tzinfo=UTC)


def target(session_id: int = 11, **changes: object) -> dict[str, object]:
    return {
        "pid": session_id,
        "datname": "app",
        "usesysid": 10,
        "backend_start": STARTED,
        "backend_type": "client backend",
        **changes,
    }


def rows(*values: dict[str, object]) -> MagicMock:
    result = MagicMock()
    result.mappings.return_value.__iter__.return_value = iter(values)
    result.mappings.return_value.first.return_value = values[0] if values else None
    return result


@pytest.fixture
def connection(monkeypatch: pytest.MonkeyPatch) -> Mock:
    value = Mock(spec=Connection)
    value.invalidated = False
    value.exec_driver_sql.return_value.scalar_one.return_value = 900

    @contextmanager
    def managed(*args: object, **kwargs: object) -> Iterator[Connection]:
        yield cast(Connection, value)

    monkeypatch.setattr(postgres, "session_connection", managed)
    monkeypatch.setattr(postgres, "configure_timeout", Mock())
    return value


def test_batch_keeps_success_before_a_permission_failure(connection: Mock) -> None:
    class Denied(Exception):
        sqlstate = "42501"

    denied = DBAPIError("private SQL", {}, Denied("private diagnostic"))
    connection.execute.side_effect = [
        rows(target(), target(12)),
        rows(target()),
        Mock(scalar=Mock(return_value=True)),
        rows(target(12)),
        denied,
    ]
    report = postgres.kill_sessions(
        parse_dsn("postgresql+psycopg://admin:private@localhost/app"), settings=load_settings()
    )
    assert [item.status for item in report.results] == ["requested", "failed"]
    assert not report.successful
    assert report.results[1].detail == "permission denied (code 42501)"
    native = [
        call
        for call in connection.execute.call_args_list
        if "pg_terminate_backend" in str(call.args[0])
    ]
    assert [call.args[1]["id"] for call in native] == [11, 12]
    assert native[0].args[1]["database"] == "app"
    assert native[0].args[1]["started"] == STARTED
    assert "private diagnostic" not in str(report)


def test_changed_identity_and_already_gone_are_not_signalled(connection: Mock) -> None:
    connection.execute.side_effect = [
        rows(target(), target(12)),
        rows(target(backend_start=STARTED + timedelta(seconds=1))),
        rows(),
    ]
    report = postgres.kill_sessions(
        parse_dsn("postgresql+psycopg://admin:private@localhost/app"), settings=load_settings()
    )
    assert [item.status for item in report.results] == ["changed", "gone"]
    assert not any(
        "pg_terminate_backend" in str(call.args[0]) for call in connection.execute.call_args_list
    )


def test_self_and_id_not_in_target_database_are_rejected(connection: Mock) -> None:
    parsed = parse_dsn("postgresql+psycopg://admin:private@localhost/app")
    with pytest.raises(DatabaseOperationError, match="current management"):
        postgres.kill_sessions(parsed, settings=load_settings(), session_id=900)
    connection.execute.assert_not_called()
    connection.execute.return_value = rows()
    with pytest.raises(DatabaseOperationError, match="target database"):
        postgres.kill_sessions(parsed, settings=load_settings(), session_id=12)
    assert connection.execute.call_args.args[1] == {"database": "app", "id": 12}


def test_wait_confirms_original_identities_in_one_unscoped_query(connection: Mock) -> None:
    finished = Mock()
    finished.fetchall.return_value = []
    connection.execute.side_effect = [
        rows(target(), target(12)),
        rows(target()),
        Mock(scalar=Mock(return_value=True)),
        rows(target(12)),
        Mock(scalar=Mock(return_value=True)),
        finished,
    ]
    report = postgres.kill_sessions(
        parse_dsn("postgresql+psycopg://admin:private@localhost/app"),
        settings=load_settings(),
        wait_seconds=1,
    )
    assert [item.status for item in report.results] == ["terminated", "terminated"]
    statement, parameters = connection.execute.call_args.args
    assert parameters == {"ids": (11, 12)}
    assert "datname" not in str(statement)
    assert "backend_type" not in str(statement)


def test_invisible_identity_is_not_blindly_terminated(connection: Mock) -> None:
    hidden = target(backend_start=None)
    connection.execute.side_effect = [rows(hidden), rows(hidden)]
    report = postgres.kill_sessions(
        parse_dsn("postgresql+psycopg://admin:private@localhost/app"), settings=load_settings()
    )
    assert report.results[0].status == "failed"
    assert "not visible" in report.results[0].detail
    assert not any(
        "pg_terminate_backend" in str(call.args[0]) for call in connection.execute.call_args_list
    )


def test_disconnect_does_not_retry_or_signal_remaining_targets(connection: Mock) -> None:
    lost = DBAPIError("private SQL", {}, Exception("disconnected"), connection_invalidated=True)
    connection.execute.side_effect = [
        rows(target(), target(12), target(13)),
        rows(target()),
        Mock(scalar=Mock(return_value=True)),
        rows(target(12)),
        lost,
    ]
    report = postgres.kill_sessions(
        parse_dsn("postgresql+psycopg://admin:private@localhost/app"),
        settings=load_settings(),
        wait_seconds=1,
    )
    assert [item.status for item in report.results] == ["failed", "failed", "not_attempted"]
    assert connection.execute.call_count == 5


@pytest.mark.parametrize("still_present,expected", [(True, "failed"), (False, "gone")])
def test_failed_signal_is_rechecked(connection: Mock, still_present: bool, expected: str) -> None:
    connection.execute.side_effect = [
        rows(target()),
        rows(target()),
        Mock(scalar=Mock(return_value=False)),
        rows(target()) if still_present else rows(),
    ]
    report = postgres.kill_sessions(
        parse_dsn("postgresql+psycopg://admin:private@localhost/app"), settings=load_settings()
    )
    assert report.results[0].status == expected
