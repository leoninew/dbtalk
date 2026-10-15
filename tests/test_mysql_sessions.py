"""MySQL connection IDs, scope checks, and asynchronous exit confirmation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

import dbtalk.database.sessions as shared
import dbtalk.mysql.session as mysql
from dbtalk.database.dsn import parse_dsn
from dbtalk.database.models import DatabaseOperationError
from dbtalk.settings import load_settings


def target(session_id: int = 11, /, **changes: object) -> dict[str, object]:
    return {
        "PROCESSLIST_ID": session_id,
        "THREAD_ID": session_id + 100,
        "PROCESSLIST_DB": "app",
        "PROCESSLIST_USER": "app_user",
        "TYPE": "FOREGROUND",
        "NAME": "thread/sql/one_connection",
        "PROCESSLIST_COMMAND": "Sleep",
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

    @contextmanager
    def managed(*args: object, **kwargs: object) -> Iterator[Connection]:
        yield cast(Connection, value)

    monkeypatch.setattr(mysql, "session_connection", managed)
    monkeypatch.setattr(mysql, "configure_timeout", Mock())
    monkeypatch.setattr(mysql, "_source_ready", Mock(return_value=900))
    return value


def test_command_state_can_change_without_changing_the_connection(connection: Mock) -> None:
    connection.execute.side_effect = [
        rows(target()),
        rows(target(PROCESSLIST_COMMAND="Query")),
        Mock(),
    ]
    report = mysql.kill_sessions(
        parse_dsn("mysql+pymysql://admin:private@localhost/app"), settings=load_settings()
    )
    assert report.results[0].status == "requested"
    assert str(connection.execute.call_args.args[0]) == "KILL CONNECTION :id"
    assert connection.execute.call_args.args[1] == {"id": 11}


@pytest.mark.parametrize(
    "change",
    [
        {"PROCESSLIST_DB": "other"},
        {"THREAD_ID": 999},
        {"PROCESSLIST_COMMAND": "Binlog Dump GTID"},
        {"TYPE": "BACKGROUND"},
    ],
)
def test_scope_identity_and_system_thread_changes_prevent_kill(
    connection: Mock, change: dict[str, object]
) -> None:
    connection.execute.side_effect = [rows(target()), rows(target(**change))]
    report = mysql.kill_sessions(
        parse_dsn("mysql+pymysql://admin:private@localhost/app"), settings=load_settings()
    )
    assert report.results[0].status == "changed"
    assert not any(
        str(call.args[0]).startswith("KILL") for call in connection.execute.call_args_list
    )


def test_wait_does_not_use_the_default_database_to_decide_exit(
    connection: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [0.0]
    monkeypatch.setattr(shared, "monotonic", lambda: now[0])
    monkeypatch.setattr(shared, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    current = Mock()
    current.fetchall.return_value = [(11, 111)]
    finished = Mock()
    finished.fetchall.return_value = []
    connection.execute.side_effect = [
        rows(target()),
        rows(target()),
        Mock(),
        current,
        finished,
    ]
    report = mysql.kill_sessions(
        parse_dsn("mysql+pymysql://admin:private@localhost/app"),
        settings=load_settings(),
        wait_seconds=1,
    )
    assert report.results[0].status == "terminated"
    for call in connection.execute.call_args_list[-2:]:
        assert "PROCESSLIST_DB" not in str(call.args[0])
        assert call.args[1] == {"ids": (11,)}


def test_batch_continues_after_permission_failure(connection: Mock) -> None:
    denied = DBAPIError("private SQL", {}, Exception(1095, "private diagnostic"))
    connection.execute.side_effect = [
        rows(target(), target(12)),
        rows(target()),
        denied,
        rows(target(12)),
        Mock(),
    ]
    report = mysql.kill_sessions(
        parse_dsn("mysql+pymysql://admin:private@localhost/app"), settings=load_settings()
    )
    assert [item.status for item in report.results] == ["failed", "requested"]
    assert report.results[0].detail == "permission denied (code 1095)"


def test_unknown_thread_is_only_success_when_original_is_gone(connection: Mock) -> None:
    unknown = DBAPIError("private SQL", {}, Exception(1094, "unknown thread"))
    connection.execute.side_effect = [
        rows(target()),
        rows(target()),
        unknown,
        rows(),
    ]
    report = mysql.kill_sessions(
        parse_dsn("mysql+pymysql://admin:private@localhost/app"), settings=load_settings()
    )
    assert report.results[0].status == "gone"


def test_self_and_missing_scoped_id_are_rejected(connection: Mock) -> None:
    parsed = parse_dsn("mysql+pymysql://admin:private@localhost/app")
    with pytest.raises(DatabaseOperationError, match="current management"):
        mysql.kill_sessions(parsed, settings=load_settings(), session_id=900)
    connection.execute.assert_not_called()
    connection.execute.return_value = rows()
    with pytest.raises(DatabaseOperationError, match="target database"):
        mysql.kill_sessions(parsed, settings=load_settings(), session_id=12)
    assert connection.execute.call_args.args[1] == {"database": "app", "id": 12}


def test_disconnect_leaves_unattempted_targets_and_never_reconnects(connection: Mock) -> None:
    lost = DBAPIError(
        "private SQL", {}, Exception(2013, "connection lost"), connection_invalidated=True
    )
    connection.execute.side_effect = [
        rows(target(), target(12)),
        rows(target()),
        lost,
    ]
    report = mysql.kill_sessions(
        parse_dsn("mysql+pymysql://admin:private@localhost/app"), settings=load_settings()
    )
    assert [item.status for item in report.results] == ["failed", "not_attempted"]
    assert connection.execute.call_count == 3


def test_disabled_performance_schema_is_an_error_not_an_empty_list() -> None:
    connection = Mock(spec=Connection)
    connection.exec_driver_sql.return_value.scalar_one.return_value = 0
    with pytest.raises(DatabaseOperationError, match="requires Performance Schema"):
        mysql._source_ready(connection)
    connection.execute.assert_not_called()
