"""Generic query/exec operations and CLI result rendering."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlparse
from sqlparse import tokens as sql_tokens
from tabulate import tabulate

from .connection import DatabaseClient
from .dsn import ParsedDsn, dsn_from_environment, parse_dsn
from .models import DatabaseOperationError, ExecutionResult, QueryResult

_TRANSACTION_CONTROL = re.compile(
    r"^(?:START\s+TRANSACTION|BEGIN(?:\s+TRANSACTION)?|COMMIT|ROLLBACK)(?:\s+WORK)?$",
    re.IGNORECASE,
)


def parse_parameters(values: tuple[str, ...]) -> dict[str, object]:
    """Parse repeated ``NAME=JSON_VALUE`` CLI parameters."""

    parameters: dict[str, object] = {}
    for value in values:
        name, separator, raw_value = value.partition("=")
        if not separator or not name or "\x00" in name:
            raise DatabaseOperationError("parameters must use NAME=JSON_VALUE")
        if name in parameters:
            raise DatabaseOperationError(f"duplicate parameter name: {name}")
        try:
            parameters[name] = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise DatabaseOperationError(
                f"parameter {name!r} must contain a valid JSON value"
            ) from error
    return parameters


def query_from_environment(
    environment_name: str,
    statement: str,
    parameters: Mapping[str, object] | None = None,
    *,
    timeout_seconds: int,
    connect_timeout_seconds: int | None = None,
) -> QueryResult:
    return query_from_dsn(
        None,
        environment_name,
        statement,
        parameters,
        timeout_seconds=timeout_seconds,
        connect_timeout_seconds=connect_timeout_seconds,
    )


def query_from_dsn(
    dsn: str | None,
    environment_name: str | None,
    statement: str,
    parameters: Mapping[str, object] | None = None,
    *,
    timeout_seconds: int,
    connect_timeout_seconds: int | None = None,
) -> QueryResult:
    parsed = _resolve_operation_dsn(dsn, environment_name)
    with DatabaseClient(
        parsed,
        timeout_seconds=timeout_seconds,
        connect_timeout_seconds=connect_timeout_seconds,
    ) as client:
        return client.query(statement, parameters)


def execute_from_environment(
    environment_name: str,
    statement: str,
    parameters: Mapping[str, object] | None = None,
    *,
    timeout_seconds: int,
    connect_timeout_seconds: int | None = None,
) -> ExecutionResult:
    return execute_from_dsn(
        None,
        environment_name,
        statement,
        parameters,
        timeout_seconds=timeout_seconds,
        connect_timeout_seconds=connect_timeout_seconds,
    )


def execute_from_dsn(
    dsn: str | None,
    environment_name: str | None,
    statement: str,
    parameters: Mapping[str, object] | None = None,
    *,
    timeout_seconds: int,
    connect_timeout_seconds: int | None = None,
) -> ExecutionResult:
    parsed = _resolve_operation_dsn(dsn, environment_name)
    with DatabaseClient(
        parsed,
        timeout_seconds=timeout_seconds,
        connect_timeout_seconds=connect_timeout_seconds,
    ) as client:
        return client.execute(statement, parameters, read_only=False)


def read_sql_file(path: Path) -> str:
    if not path.exists():
        raise DatabaseOperationError("SQL file does not exist")
    if path.is_dir():
        raise DatabaseOperationError("SQL file path is a directory")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise DatabaseOperationError("SQL file is not valid UTF-8") from error
    if not content.strip():
        raise DatabaseOperationError("SQL file is empty")
    return content


def sql_script_statements(script: str) -> tuple[str, ...]:
    statements: list[str] = []
    for raw in sqlparse.split(script, strip_semicolon=True):
        statement = raw.strip()
        if not statement:
            continue
        significant = _significant_sql(statement)
        if not significant or _TRANSACTION_CONTROL.fullmatch(significant) is not None:
            continue
        statements.append(statement)
    return tuple(statements)


def execute_sql_file_from_dsn(
    dsn: str | None,
    environment_name: str | None,
    path: Path,
    *,
    timeout_seconds: int,
    connect_timeout_seconds: int | None = None,
) -> ExecutionResult:
    statements = sql_script_statements(read_sql_file(path))
    if not statements:
        raise DatabaseOperationError("SQL file contains no executable statements")
    parsed = _resolve_operation_dsn(dsn, environment_name)
    with DatabaseClient(
        parsed,
        timeout_seconds=timeout_seconds,
        connect_timeout_seconds=connect_timeout_seconds,
    ) as client:
        return client.execute_script(statements)


def _significant_sql(statement: str) -> str:
    parts: list[str] = []
    for ttype, value in sqlparse.lexer.tokenize(statement):
        if ttype in sql_tokens.Comment or ttype in (sql_tokens.Whitespace, sql_tokens.Newline):
            continue
        parts.append(value)
    return " ".join(parts).strip().rstrip(";").strip()


def _resolve_operation_dsn(dsn: str | None, environment_name: str | None) -> ParsedDsn:
    if (dsn is None) == (environment_name is None):
        raise DatabaseOperationError("provide exactly one of --dsn or --dsn-env")
    return parse_dsn(dsn) if dsn is not None else dsn_from_environment(environment_name)


def render_query(result: QueryResult, output_format: str) -> str:
    """Render a query result using the stable table or JSON contract."""

    if output_format == "table":
        rendered = tabulate(
            result.rows,
            headers=result.columns,
            tablefmt="psql",
            missingval="NULL",
        )
        return f"{rendered}\n(0 rows)" if not result.rows else rendered
    if output_format == "json":
        payload = {
            "columns": list(result.columns),
            "rows": [
                {
                    column: json_safe_value(value)
                    for column, value in zip(result.columns, row, strict=True)
                }
                for row in result.rows
            ],
            "row_count": result.row_count,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    raise DatabaseOperationError("query format must be table or json")


def json_safe_value(value: object) -> object:
    """Convert common database values into deterministic JSON-compatible values."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {
            "type": "base64",
            "value": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, Mapping):
        return {str(key): json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_value(item) for item in value]
    return str(value)


def json_default(value: object) -> Any:
    """JSON encoder hook for callers that serialize arbitrary result values."""

    return json_safe_value(value)


__all__ = [
    "execute_from_dsn",
    "execute_from_environment",
    "json_default",
    "json_safe_value",
    "parse_parameters",
    "query_from_environment",
    "query_from_dsn",
    "render_query",
]
