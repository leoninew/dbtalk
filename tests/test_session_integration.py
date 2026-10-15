"""Opt-in native session tests; endpoints must be disposable, isolated test instances."""

from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from time import monotonic, sleep
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

import dbtalk.mysql.session as mysql
import dbtalk.postgres.session as postgres
from dbtalk.database.dsn import ParsedDsn, parse_dsn
from dbtalk.database.models import DatabaseOperationError, QueryResult
from dbtalk.database.sessions import SessionKillReport
from dbtalk.settings import Settings, load_settings

pytestmark = pytest.mark.integration


@dataclass
class Instance:
    parsed: ParsedDsn
    settings: Settings
    admin: Connection
    database: str
    sentinel: str
    operator: str
    other: str
    password: str
    engines: list[Engine] = field(default_factory=list)
    clients: list[Connection] = field(default_factory=list)

    def connect(
        self, database: str, *, user: str | None = None, autocommit: bool = True
    ) -> Connection:
        url = self.parsed.url.set(database=database)
        if user is not None:
            url = url.set(username=user, password=self.password)
        engine = create_engine(url, poolclass=NullPool, hide_parameters=True)
        self.engines.append(engine)
        connection = engine.connect()
        self.clients.append(connection)
        return (
            connection.execution_options(isolation_level="AUTOCOMMIT") if autocommit else connection
        )

    def target(self, user: str | None = None) -> ParsedDsn:
        url = self.parsed.url.set(database=self.sentinel)
        if user is not None:
            url = url.set(username=user, password=self.password)
        return parse_dsn(url.render_as_string(hide_password=False))

    def list(self) -> QueryResult:
        operation = (
            postgres.list_sessions if self.parsed.dialect == "postgresql" else mysql.list_sessions
        )
        return operation(self.target(), settings=self.settings, database=self.database)

    def kill(self, *, session_id: int | None = None, user: str | None = None) -> SessionKillReport:
        operation = (
            postgres.kill_sessions if self.parsed.dialect == "postgresql" else mysql.kill_sessions
        )
        return operation(
            self.target(user),
            settings=self.settings,
            database=self.database,
            session_id=session_id,
            wait_seconds=5,
        )

    def session_id(self, connection: Connection) -> int:
        function = "pg_backend_pid()" if self.parsed.dialect == "postgresql" else "CONNECTION_ID()"
        return int(connection.exec_driver_sql(f"SELECT {function}").scalar_one())


@pytest.fixture(params=["postgresql", "mysql"])
def instance(request: pytest.FixtureRequest) -> Iterator[Instance]:
    dialect = str(request.param)
    variable = (
        "DBTALK_SESSION_TEST_POSTGRES_DSN"
        if dialect == "postgresql"
        else "DBTALK_SESSION_TEST_MYSQL_DSN"
    )
    dsn = os.environ.get(variable)
    if not dsn:
        pytest.skip(f"{variable} is not configured (requires an isolated test instance)")
    parsed = parse_dsn(dsn)
    assert parsed.dialect == dialect and not parsed.async_mode
    suffix = uuid4().hex[:12]
    names = [f"dbtalk_st_{suffix}_{kind}" for kind in ("app", "sentinel", "op", "other")]
    engine = create_engine(parsed.url, poolclass=NullPool, hide_parameters=True)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as admin:
        case = Instance(
            parsed, load_settings(), admin, names[0], names[1], names[2], names[3], uuid4().hex
        )
        databases: list[str] = []
        accounts: list[str] = []
        try:
            for name in (case.database, case.sentinel):
                admin.exec_driver_sql(f"CREATE DATABASE {name}")
                databases.append(name)
            for name in (case.operator, case.other):
                if dialect == "postgresql":
                    admin.exec_driver_sql(f"CREATE ROLE {name} LOGIN PASSWORD '{case.password}'")
                    accounts.append(name)
                else:
                    admin.exec_driver_sql(
                        f"CREATE USER '{name}'@'%%' IDENTIFIED BY '{case.password}'"
                    )
                    accounts.append(name)
                    admin.exec_driver_sql(f"GRANT SELECT ON {case.database}.* TO '{name}'@'%%'")
                    admin.exec_driver_sql(f"GRANT SELECT ON {case.sentinel}.* TO '{name}'@'%%'")
            if dialect == "postgresql":
                admin.exec_driver_sql(f"GRANT pg_read_all_stats TO {case.operator}")
            else:
                admin.exec_driver_sql(
                    f"GRANT SELECT ON performance_schema.threads TO '{case.operator}'@'%%'"
                )
            yield case
        finally:
            for connection in case.clients:
                with suppress(SQLAlchemyError):
                    connection.close()
            for client_engine in case.engines:
                client_engine.dispose()
            for name in reversed(databases):
                clause = " WITH (FORCE)" if dialect == "postgresql" else ""
                admin.exec_driver_sql(f"DROP DATABASE {name}{clause}")
            for name in reversed(accounts):
                command = (
                    f"DROP ROLE {name}" if dialect == "postgresql" else f"DROP USER '{name}'@'%%'"
                )
                admin.exec_driver_sql(command)
    engine.dispose()


def assert_disconnected(connection: Connection) -> None:
    with pytest.raises(SQLAlchemyError):
        connection.exec_driver_sql("SELECT 1")


def test_native_list_scoped_kill_wait_and_rollback(instance: Instance) -> None:
    setup = instance.connect(instance.database)
    setup.exec_driver_sql("CREATE TABLE uncommitted_probe (id INTEGER PRIMARY KEY)")
    setup.close()
    sentinel = instance.connect(instance.sentinel)
    foreign_id = instance.session_id(sentinel)
    idle = instance.connect(instance.database)
    idle_id = instance.session_id(idle)
    active = instance.connect(instance.database, autocommit=False)
    active_id = instance.session_id(active)
    active.exec_driver_sql("INSERT INTO uncommitted_probe VALUES (1)")

    def run_query() -> None:
        with pytest.raises(SQLAlchemyError):
            function = "pg_sleep(20)" if instance.parsed.dialect == "postgresql" else "SLEEP(20)"
            active.exec_driver_sql(f"SELECT {function}")

    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(run_query)
        deadline = monotonic() + 5
        while True:
            listing = instance.list()
            records = {
                cast(int, row[0]): dict(zip(listing.columns, row, strict=True))
                for row in listing.rows
            }
            assert list(records) == sorted(records)
            assert set(records) == {idle_id, active_id}
            assert {row["database"] for row in records.values()} == {instance.database}
            if (
                records[active_id]["state"] == "active"
                or records[active_id].get("command") == "Query"
            ):
                break
            assert monotonic() < deadline, "test client did not enter its active query"
            sleep(0.02)
        with pytest.raises(DatabaseOperationError, match="target database"):
            instance.kill(session_id=foreign_id)
        assert sentinel.exec_driver_sql("SELECT 1").scalar_one() == 1
        report = instance.kill(session_id=idle_id)
        assert [(row.session_id, row.status) for row in report.results] == [(idle_id, "terminated")]
        assert_disconnected(idle)
        report = instance.kill()
        assert [(row.session_id, row.status) for row in report.results] == [
            (active_id, "terminated")
        ]
        running.result(timeout=5)
    observer = instance.connect(instance.database)
    assert observer.exec_driver_sql("SELECT count(*) FROM uncommitted_probe").scalar_one() == 0
    observer.close()
    assert sentinel.exec_driver_sql("SELECT 1").scalar_one() == 1
    assert instance.list().rows == ()
    assert instance.kill().results == ()


def test_native_permission_failure_preserves_partial_results(instance: Instance) -> None:
    own = instance.connect(instance.database, user=instance.operator)
    own_id = instance.session_id(own)
    other = instance.connect(instance.database, user=instance.other)
    other_id = instance.session_id(other)
    report = instance.kill(user=instance.operator)
    statuses = {row.session_id: row.status for row in report.results}
    assert statuses == {own_id: "terminated", other_id: "failed"}
    assert not report.successful
    assert_disconnected(own)
    assert other.exec_driver_sql("SELECT 1").scalar_one() == 1
    if instance.parsed.dialect == "postgresql":
        instance.admin.exec_driver_sql(f"GRANT pg_signal_backend TO {instance.operator}")
    else:
        instance.admin.exec_driver_sql(
            f"GRANT CONNECTION_ADMIN ON *.* TO '{instance.operator}'@'%%'"
        )
    report = instance.kill(session_id=other_id, user=instance.operator)
    assert report.successful and report.results[0].status == "terminated"
    assert_disconnected(other)
