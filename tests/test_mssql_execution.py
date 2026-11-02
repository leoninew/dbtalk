from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, NoReturn

import pytest
from sqlalchemy import create_engine

from dbtalk.database.connection import DatabaseClient, DatabaseSession
from dbtalk.database.models import DatabaseOperationError, sanitize_error_detail


class _PendingResultCursor(sqlite3.Cursor):
    def nextset(self) -> bool | None:
        if self.description and self.description[0][0] == "late_error":
            raise sqlite3.IntegrityError("failure after the first result set")
        if self.description and self.description[0][0] == "first_result":
            self.execute("SELECT 2 AS late_error")
            return True
        if self.description and self.description[0][0] == "first_success":
            self.execute("SELECT 2 AS second_success")
            return True
        return None


class _PendingResultConnection(sqlite3.Connection):
    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        return super().cursor(factory=_PendingResultCursor)


@pytest.fixture
def execution_client() -> Iterator[DatabaseClient]:
    with DatabaseClient("sqlite:///:memory:") as client:
        client._engine.dispose()
        client._engine = create_engine(
            "sqlite:///:memory:", connect_args={"factory": _PendingResultConnection}
        )
        client._engine.dialect.name = "mssql"
        client.execute("CREATE TABLE records (id INTEGER PRIMARY KEY)")
        yield client


@pytest.mark.parametrize("literal", [False, True])
@pytest.mark.parametrize("result_alias", ["late_error", "first_result"])
def test_late_result_error_rolls_back_before_execution_returns(
    execution_client: DatabaseClient, literal: bool, result_alias: str
) -> None:
    with (
        pytest.raises(DatabaseOperationError, match="database execution failed"),
        execution_client.transaction() as session,
    ):
        session.execute("INSERT INTO records VALUES (:id)", {"id": 1})
        if literal:
            session.execute_literal(f"SELECT 1 AS {result_alias}")
        else:
            session.execute(f"SELECT :number AS {result_alias}", {"number": 1})
    assert execution_client.query("SELECT id FROM records").rows == ()


def test_execute_script_rolls_back_after_a_late_result_error(
    execution_client: DatabaseClient,
) -> None:
    with pytest.raises(DatabaseOperationError, match="database execution failed"):
        execution_client.execute_script(
            ("INSERT INTO records VALUES (1)", "SELECT 1 AS first_result")
        )
    assert execution_client.query("SELECT id FROM records").rows == ()


def test_execute_preserves_normal_counts_and_consumes_read_results(
    execution_client: DatabaseClient,
) -> None:
    assert execution_client.execute("INSERT INTO records VALUES (:id)", {"id": 1}).row_count == 1
    assert execution_client.execute("SELECT id FROM records").row_count == 0
    assert execution_client.execute("SELECT 1 AS first_success").row_count == 0
    assert execution_client.execute("UPDATE records SET id=2 WHERE id=1").row_count == 1
    assert execution_client.query("SELECT id FROM records").rows == ((2,),)


@pytest.mark.parametrize("name", ["from", "select", "where", "values"])
def test_keyword_named_bind_preserves_sql_and_parameter_value(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with DatabaseClient("sqlite:///:memory:") as local_client:

        @contextmanager
        def local_query_session(client: DatabaseClient) -> Iterator[DatabaseSession]:
            with local_client.connect() as session:
                yield session

        monkeypatch.setattr(DatabaseClient, "_query_session", local_query_session)
        with DatabaseClient("mssql+pymssql://unused:unused@invalid.example/db") as client:
            value = "literal'; DROP TABLE records; --"
            result = client.query(f"SELECT CAST(:{name} AS TEXT) AS value", {name: value})
            assert result.rows == ((value,),)


def test_mssql_driver_errors_redact_credentials() -> None:
    detail = sanitize_error_detail(
        'mssql+pymssql://user:private_password@host/app PWD="private password";Database=app'
    )
    assert "private_password" not in detail
    assert "private password" not in detail


def test_mssql_missing_driver_returns_controlled_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_driver(*args: object, **kwargs: object) -> NoReturn:
        raise ModuleNotFoundError("No module named 'pymssql'")

    monkeypatch.setattr("dbtalk.database.connection.create_engine", missing_driver)
    with pytest.raises(DatabaseOperationError, match="database engine could not be created"):
        DatabaseClient("mssql+pymssql://unused:unused@invalid.example/db")
