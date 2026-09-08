"""Data contracts shared by JSONL database transfer components."""

from __future__ import annotations

import re
import traceback
from dataclasses import dataclass
from datetime import tzinfo
from pathlib import Path
from typing import Literal

DatabaseDriver = Literal["sqlite", "mysql", "postgresql"]
TransferMode = Literal["insert", "upsert"]

JSONScalar = None | bool | int | float | str
TypedJSONValue = dict[str, str]
JSONValue = JSONScalar | TypedJSONValue


class DatabaseTransferError(RuntimeError):
    """Raised when a database transfer cannot safely continue."""


class DatabaseOperationError(RuntimeError):
    """Raised when a generic database operation cannot safely continue."""


@dataclass(frozen=True)
class ColumnDefinition:
    """A source column declaration carried by a JSONL table record."""

    name: str
    declared_type: str


@dataclass(frozen=True)
class TableBlockHeader:
    """Metadata that starts one JSONL table block."""

    name: str
    columns: tuple[ColumnDefinition, ...]
    primary_key: tuple[str, ...]


@dataclass(frozen=True)
class TableBlock:
    """A complete table block ready for an importer."""

    header: TableBlockHeader
    rows: tuple[tuple[JSONValue, ...], ...]


@dataclass(frozen=True)
class TransferHeader:
    """The first JSONL record identifying the transfer format and source."""

    format: str
    source: DatabaseDriver


@dataclass(frozen=True)
class TransferDocument:
    """The fully validated, in-memory representation of one transfer file."""

    header: TransferHeader
    tables: tuple[TableBlock, ...]


@dataclass(frozen=True)
class TablePreview:
    """Metadata and row count collected during a streaming preflight."""

    header: TableBlockHeader
    row_count: int


@dataclass(frozen=True)
class TransferPreview:
    """A transfer header and table metadata without retaining row values."""

    header: TransferHeader
    tables: tuple[TablePreview, ...]


@dataclass(frozen=True)
class TableSchema:
    """Target table metadata used for compatibility and dependency checks."""

    name: str
    columns: tuple[ColumnDefinition, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[str, ...]


@dataclass(frozen=True)
class TransferConnection:
    """Connection input for transfer, resolved through the canonical DSN boundary."""

    driver: DatabaseDriver
    dsn: str | None = None
    dsn_env: str | None = None


@dataclass(frozen=True)
class QueryResult:
    """Database-independent query result returned by sync and async clients."""

    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class ExecutionResult:
    """Database-independent execution result."""

    row_count: int


@dataclass(frozen=True)
class ExportOptions:
    """Inputs for a database export implementation."""

    connection: TransferConnection
    output: Path
    timezone: tzinfo
    exclude_tables: tuple[str, ...] = ()
    include_tables: tuple[str, ...] = ()
    zero_datetime_as_null: bool = True


@dataclass(frozen=True)
class ImportOptions:
    """Inputs for a database import implementation."""

    connection: TransferConnection
    input: Path
    mode: TransferMode
    timezone: tzinfo
    exclude_tables: tuple[str, ...] = ()
    include_tables: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransferSummary:
    """Non-sensitive result information reported by the CLI."""

    table_count: int
    row_count: int


_SECRET_ASSIGNMENT_PATTERN = re.compile(r"(?i)(mysql_pwd=|password=|pgpassword=)[^\s]+")
_DSN_USERINFO_PATTERN = re.compile(r"(?i)((?:mysql|postgresql)(?:\+[\w.]+)?://)[^@\s]+@")
_SQLALCHEMY_NOISE_MARKERS = ("[SQL:", "[parameters:", "(Background on this error at:")


def sanitize_error_detail(message: str) -> str:
    """Remove credentials and truncate diagnostics before display or logging."""

    sanitized = _SECRET_ASSIGNMENT_PATTERN.sub(r"\1<redacted>", message)
    sanitized = _DSN_USERINFO_PATTERN.sub(r"\1<redacted>@", sanitized)
    return sanitized[:1000]


def driver_error_detail(error: BaseException) -> str:
    """Return exception type and message without SQL, parameters, or traceback."""

    text = "".join(traceback.format_exception_only(error)).strip()
    return _strip_sqlalchemy_noise(text)


def format_cli_error(error: BaseException, *, verbose: bool) -> str:
    """Render a CLI error, optionally appending sanitized exception details."""

    message = str(error)
    if not verbose:
        return message
    detail = sanitize_error_detail(_verbose_error_detail(error))
    if not detail or detail in message:
        return message
    return f"{message}: {detail}"


def _verbose_error_detail(error: BaseException) -> str:
    outer = str(error)
    for current in _exception_chain(error)[1:]:
        raw = _strip_sqlalchemy_noise(str(current))
        if not raw or raw == outer or raw in outer:
            continue
        detail = driver_error_detail(current)
        if not detail or detail == outer or detail in outer:
            continue
        return detail
    return ""


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    return chain


def _strip_sqlalchemy_noise(message: str) -> str:
    cut = len(message)
    for marker in _SQLALCHEMY_NOISE_MARKERS:
        index = message.find(marker)
        if index != -1:
            cut = min(cut, index)
    return message[:cut].strip()
