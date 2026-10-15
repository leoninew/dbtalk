"""Behavior at the shared database-scoped session boundary."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from sqlalchemy.engine import Connection

import dbtalk.database.sessions as shared
import dbtalk.mysql.session as mysql
import dbtalk.postgres.session as postgres
from dbtalk.cli import cli
from dbtalk.database.dsn import parse_dsn
from dbtalk.database.models import DatabaseOperationError
from dbtalk.database.sessions import SessionKillReport, SessionKillResult
from dbtalk.settings import SessionConfig, load_session_config, load_settings


@pytest.mark.parametrize(
    "module,dsn",
    [
        (postgres, "postgresql+psycopg://admin:private@localhost/old?sslmode=require"),
        (mysql, "mysql+pymysql://admin:private@localhost/old?charset=utf8mb4"),
    ],
)
def test_database_selection_preserves_other_connection_options(module: object, dsn: str) -> None:
    parsed = parse_dsn(dsn)
    for explicit, expected in [(None, "old"), ("app", "app")]:
        target = shared.resolve_session_target(parsed, explicit, parsed.dialect)
        assert target.database == expected
        assert target.url.password == "private"
        assert target.url.query == parsed.url.query
    bare = replace(parsed, url=parsed.url.set(database=""))
    assert shared.resolve_session_target(bare, "app", parsed.dialect).database == "app"
    for explicit in [None, "", " ", "app\n"]:
        with pytest.raises(DatabaseOperationError):
            shared.resolve_session_target(bare, explicit, parsed.dialect)


@pytest.mark.parametrize("backend", ["postgres", "mysql"])
@pytest.mark.parametrize("command", ["list", "kill"])
def test_missing_database_never_creates_an_engine(
    monkeypatch: pytest.MonkeyPatch, backend: str, command: str
) -> None:
    engine = Mock()
    monkeypatch.setattr(shared, "create_engine", engine)
    dialect = "postgresql+psycopg" if backend == "postgres" else "mysql+pymysql"
    args = [backend, "session", command, "--dsn", f"{dialect}://admin:private@localhost"]
    if command == "kill":
        args += ["--id", "12", "--yes"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 2, result.output
    assert "target database" in result.output
    engine.assert_not_called()


@pytest.mark.parametrize(
    "backend,module,dsn",
    [
        ("postgres", postgres, "postgresql+psycopg://admin:private@localhost/old"),
        ("mysql", mysql, "mysql+pymysql://admin:private@localhost/old"),
    ],
)
@pytest.mark.parametrize("verbose", [False, True])
def test_cli_target_override_and_scoped_kill(
    monkeypatch: pytest.MonkeyPatch, backend: str, module: object, dsn: str, verbose: bool
) -> None:
    operation = Mock(return_value=SessionKillReport("app", (SessionKillResult(12, "requested"),)))
    monkeypatch.setattr(module, "kill_sessions", operation)
    result = CliRunner().invoke(
        cli,
        (["-v"] if verbose else [])
        + [
            backend,
            "session",
            "kill",
            "--dsn",
            dsn,
            "--database",
            "app",
            "--id",
            "12",
            "--wait",
            "0.25",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert operation.call_args.args[0].database == "app"
    assert operation.call_args.kwargs["session_id"] == 12
    assert operation.call_args.kwargs["wait_seconds"] == 0.25
    assert "requested=1" in result.stdout
    assert ("database='app'" in result.stderr) is verbose
    assert ("session_id=12" in result.stderr) is verbose
    assert "private" not in result.output


@pytest.mark.parametrize("backend", ["postgres", "mysql"])
def test_confirmation_and_wait_are_checked_before_io(
    monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    engine = Mock()
    monkeypatch.setattr(shared, "create_engine", engine)
    base = [backend, "session", "kill", "--dsn-env", "UNSET_DSN"]
    assert CliRunner().invoke(cli, base).exit_code == 2
    for value in ["0", "-1", "nan", "inf"]:
        result = CliRunner().invoke(cli, base + ["--yes", "--wait", value])
        assert result.exit_code == 2, result.output
        assert "finite positive" in result.output
    engine.assert_not_called()
    assert CliRunner().invoke(cli, [backend, "session", "cancel"]).exit_code == 2
    assert CliRunner().invoke(cli, [backend, "session", "list", "-v"]).exit_code == 2


@pytest.mark.parametrize(
    "backend,module,dsn",
    [
        ("postgres", postgres, "postgresql+psycopg://admin:private@localhost/app"),
        ("mysql", mysql, "mysql+pymysql://admin:private@localhost/app"),
    ],
)
@pytest.mark.parametrize("verbose", [False, True])
def test_partial_results_preserve_stdout_and_root_logging(
    monkeypatch: pytest.MonkeyPatch, backend: str, module: object, dsn: str, verbose: bool
) -> None:
    report = SessionKillReport(
        "app",
        (
            SessionKillResult(11, "requested"),
            SessionKillResult(12, "failed", "permission denied"),
        ),
    )

    def operation(*args: object, **kwargs: object) -> SessionKillReport:
        shared.logger.info("session targets_selected database=%r count=%d", "app", 2)
        return report

    monkeypatch.setattr(module, "kill_sessions", operation)
    args = (["-v"] if verbose else []) + [
        backend,
        "session",
        "kill",
        "--dsn",
        dsn,
        "--wait",
        "1",
        "--yes",
    ]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1
    assert "requested=1" in result.stdout and "failed=1" in result.stdout
    assert "see results" in result.stderr
    assert ("targets_selected" in result.stderr) is verbose
    assert ("wait_seconds=1.0" in result.stderr) is verbose
    assert "private" not in result.output
    assert dsn not in result.output


def test_wait_uses_one_budget_and_the_configured_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(shared, "monotonic", lambda: now[0])
    monkeypatch.setattr(shared, "sleep", sleep)
    queried: list[tuple[int, ...]] = []

    def refresh(ids: tuple[int, ...], remaining: float) -> dict[int, shared.SessionToken | None]:
        queried.append(ids)
        return {session_id: session_id + 100 for session_id in ids}

    results = shared.wait_for_sessions(
        [SessionKillResult(1, "requested"), SessionKillResult(2, "requested")],
        {1: 101, 2: 102},
        refresh,
        wait_seconds=1,
        poll_interval_seconds=0.4,
    )
    assert [item.status for item in results] == ["timeout", "timeout"]
    assert queried == [(1, 2)] * 3
    assert sleeps == pytest.approx([0.4, 0.4, 0.2])
    assert now[0] == pytest.approx(1)


def test_wait_distinguishes_reuse_hidden_identity_and_late_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    monkeypatch.setattr(shared, "monotonic", lambda: now[0])
    result = shared.wait_for_sessions(
        [SessionKillResult(1, "requested"), SessionKillResult(2, "requested")],
        {1: 101, 2: 102},
        lambda ids, remaining: {1: 999, 2: None},
        wait_seconds=1,
        poll_interval_seconds=0.2,
    )
    assert [item.status for item in result] == ["terminated", "failed"]

    def late(ids: tuple[int, ...], remaining: float) -> dict[int, shared.SessionToken | None]:
        now[0] = 2
        return {}

    result = shared.wait_for_sessions(
        [SessionKillResult(1, "requested")],
        {1: 101},
        late,
        wait_seconds=1,
        poll_interval_seconds=0.2,
    )
    assert result[0].status == "timeout"


@pytest.mark.parametrize("raw", [None, True, 0, -1, "nan", "inf", "invalid"])
def test_session_configuration_is_validated_centrally(raw: object) -> None:
    with pytest.raises(ValueError, match="session.poll_interval_seconds"):
        load_session_config({} if raw is None else {"poll_interval_seconds": raw})


def test_session_yaml_and_env_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    assert load_settings().session.poll_interval_seconds == 0.1
    monkeypatch.setenv("DBTALK_SESSION__POLL_INTERVAL_SECONDS", "0.35")
    assert load_settings().session == SessionConfig(poll_interval_seconds=0.35)
    monkeypatch.setenv("DBTALK_SESSION__POLL_INTERVAL_SECONDS", "0")
    with pytest.raises(ValueError, match="session.poll_interval_seconds"):
        load_settings()


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_management_connection_uses_configured_io_budget_and_always_disposes(
    monkeypatch: pytest.MonkeyPatch, dialect: str
) -> None:
    settings = load_settings()
    settings = replace(
        settings,
        database=replace(settings.database, query_timeout_seconds=7, exec_timeout_seconds=9),
    )
    connection = Mock(spec=Connection)
    connection.execution_options.return_value = connection
    engine = Mock()
    engine.connect.return_value.__enter__ = Mock(return_value=connection)
    engine.connect.return_value.__exit__ = Mock(return_value=False)
    create_engine = Mock(return_value=engine)
    monkeypatch.setattr(shared, "create_engine", create_engine)
    driver = "postgresql+psycopg" if dialect == "postgresql" else "mysql+pymysql"
    with (
        pytest.raises(RuntimeError, match="body failed"),
        shared.session_connection(
            parse_dsn(f"{driver}://admin:private@localhost/app"), settings, writing=True
        ),
    ):
        raise RuntimeError("body failed")
    assert create_engine.call_args.kwargs["connect_args"]["connect_timeout"] == 9
    if dialect == "mysql":
        assert create_engine.call_args.kwargs["connect_args"]["read_timeout"] == 9
    connection.execution_options.assert_called_once_with(isolation_level="AUTOCOMMIT")
    engine.dispose.assert_called_once()
