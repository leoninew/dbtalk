"""JSONL v1 parsing, writing and portable database-value conversion."""

from __future__ import annotations

import base64
import contextlib
import gzip
import json
import math
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, tzinfo
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO, cast
from uuid import UUID

from .models import (
    ColumnDefinition,
    ColumnMetadata,
    DatabaseTransferError,
    JSONValue,
    TableBlock,
    TableBlockHeader,
    TablePreview,
    TransferDocument,
    TransferHeader,
    TransferPreview,
)

TRANSFER_FORMAT = "dbtalk.database-transfer/v1"
MAX_DATETIME_FRACTIONAL_PRECISION = 6
_MSSQL_INTEGER_BOUNDS = {
    "tinyint": (0, 255),
    "smallint": (-32768, 32767),
    "int": (-2147483648, 2147483647),
    "bigint": (-9223372036854775808, 9223372036854775807),
}
_FRACTIONAL_SECOND = re.compile(r"(?<=\d{2}:\d{2}:\d{2})\.(?P<value>\d+)")
_GO_DATETIME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2}) "
    r"(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d+))? "
    r"(?P<offset>[+-]\d{4}) [A-Za-z][A-Za-z0-9_+/-]*$"
)


def normalize_mssql_export_value(value: object, column: ColumnDefinition) -> object:
    if value is None:
        return None
    if column.declared_type == "UNIQUEIDENTIFIER":
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, str):
            try:
                return str(UUID(value))
            except ValueError:
                pass
        raise DatabaseTransferError(f"mssql column {column.name!r} contains an invalid UUID")
    if column.declared_type == "BOOLEAN":
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        raise DatabaseTransferError(f"mssql column {column.name!r} contains an invalid boolean")
    if column.declared_type.startswith(("DECIMAL(", "NUMERIC(")) and not isinstance(
        value, (Decimal, int)
    ):
        raise DatabaseTransferError(f"mssql column {column.name!r} contains a non-exact decimal")
    return value


def decode_mssql_value(value: JSONValue, column: ColumnMetadata, timezone: tzinfo) -> object:
    if value is None:
        if not column.nullable or column.identity:
            raise DatabaseTransferError("NULL is not allowed in the target column")
        return None
    kind = column.type_name
    try:
        decoded = decode_value(value, "BOOLEAN" if kind == "bit" else kind.upper(), timezone)
    except (ValueError, OverflowError):
        raise DatabaseTransferError("value is outside the target representation") from None
    if kind in _MSSQL_INTEGER_BOUNDS:
        if isinstance(decoded, bool):
            return int(decoded)
        if not isinstance(decoded, (int, float, Decimal)) or not Decimal(str(decoded)).is_finite():
            raise DatabaseTransferError("integer input is invalid")
        number = int(decoded)
        minimum, maximum = _MSSQL_INTEGER_BOUNDS[kind]
        if number != decoded or not minimum <= number <= maximum:
            raise DatabaseTransferError("integer exceeds target range or has fractional digits")
        return number
    if kind in {"numeric", "decimal"}:
        return _mssql_decimal_value(decoded, column)
    if kind == "uniqueidentifier":
        if not isinstance(decoded, str):
            raise DatabaseTransferError("UUID input must be text")
        try:
            return UUID(decoded)
        except ValueError:
            raise DatabaseTransferError("UUID input is invalid") from None
    if kind in {"char", "varchar", "nchar", "nvarchar"}:
        return _mssql_text_value(decoded, column)
    if kind in {"binary", "varbinary"}:
        if not isinstance(decoded, bytes):
            raise DatabaseTransferError("binary input must use the BLOB encoding")
        if column.max_length != -1 and len(decoded) > column.max_length:
            raise DatabaseTransferError("binary input exceeds target length")
        if kind == "binary" and len(decoded) != column.max_length:
            raise DatabaseTransferError("fixed-width binary input would require padding")
        return decoded
    if kind in {"date", "time", "datetime", "datetime2", "smalldatetime"}:
        return _mssql_temporal_value(decoded, column)
    return decoded


def _mssql_decimal_value(value: object, column: ColumnMetadata) -> Decimal:
    if not isinstance(value, (int, Decimal)):
        raise DatabaseTransferError("decimal input must be exact")
    number = Decimal(value)
    if not number.is_finite():
        raise DatabaseTransferError("decimal input must be finite")
    if not number:
        return number
    parts = number.as_tuple()
    assert isinstance(parts.exponent, int)
    digits = list(parts.digits)
    exponent = parts.exponent
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if exponent < -column.scale or max(len(digits) + exponent, 0) > column.precision - column.scale:
        raise DatabaseTransferError("decimal exceeds target precision or scale")
    return number


def _mssql_text_value(value: object, column: ColumnMetadata) -> str:
    if not isinstance(value, str):
        raise DatabaseTransferError("text input must be a string")
    if column.type_name in {"nchar", "nvarchar"}:
        encoding = "utf-16-le"
    elif column.code_page:
        encoding = f"cp{column.code_page}"
    else:
        raise DatabaseTransferError("target text code page is unavailable")
    try:
        encoded = value.encode(encoding)
        if encoded.decode(encoding) != value:
            raise DatabaseTransferError("text cannot round-trip through the target encoding")
    except (UnicodeError, LookupError):
        raise DatabaseTransferError("text cannot be represented in the target encoding") from None
    if column.max_length != -1 and len(encoded) > column.max_length:
        raise DatabaseTransferError("text exceeds target length")
    if column.type_name in {"char", "nchar"} and len(encoded) != column.max_length:
        raise DatabaseTransferError("fixed-width text would require padding")
    return value


def _mssql_temporal_value(value: object, column: ColumnMetadata) -> str:
    if not isinstance(value, str):
        raise DatabaseTransferError("temporal input must be a string")
    fraction = _FRACTIONAL_SECOND.search(value)
    digits = fraction.group("value") if fraction is not None else ""
    precision = column.scale if column.type_name in {"datetime2", "time"} else 6
    if any(digit != "0" for digit in digits[precision:]):
        raise DatabaseTransferError("temporal input exceeds target precision")
    try:
        if column.type_name == "date":
            return date.fromisoformat(value).isoformat()
        if column.type_name == "time":
            clock = time.fromisoformat(value)
            if clock.tzinfo is not None:
                raise DatabaseTransferError("time offsets are not supported")
            return clock.isoformat()
        instant = datetime.fromisoformat(value)
        if instant.tzinfo is not None:
            raise DatabaseTransferError("datetime input was not normalized")
        if column.type_name == "smalldatetime" and (
            not date(1900, 1, 1) <= instant.date() <= date(2079, 6, 6)
            or instant.second
            or instant.microsecond
        ):
            raise DatabaseTransferError("datetime is not representable as smalldatetime")
        if column.type_name == "datetime":
            milliseconds, remaining = divmod(instant.microsecond, 1000)
            if instant.year < 1753 or remaining or milliseconds % 10 not in {0, 3, 7}:
                raise DatabaseTransferError(
                    "datetime is not representable at target range or precision"
                )
            return instant.isoformat(sep="T", timespec="milliseconds")
        return instant.isoformat(sep="T")
    except (ValueError, OverflowError):
        raise DatabaseTransferError("temporal input is outside the target range") from None


def gzip_output_path(output: Path) -> Path:
    """Return the gzip path associated with a requested archive output."""
    if output.name.lower().endswith(".gz"):
        return output
    return output.with_name(f"{output.name}.gz")


def _is_gzip_path(path: Path) -> bool:
    return path.name.lower().endswith(".gz")


def read_document(input_path: Path) -> TransferDocument:
    """Open, parse and fully validate a JSONL transfer file."""

    try:
        if _is_gzip_path(input_path):
            with gzip.open(input_path, "rt", encoding="utf-8", newline="") as stream:
                header, tables = read_jsonl(stream)
        else:
            with input_path.open("r", encoding="utf-8", newline="") as stream:
                header, tables = read_jsonl(stream)
    except (EOFError, OSError, UnicodeError) as error:
        raise DatabaseTransferError(f"could not read JSONL transfer file: {error}") from error
    return TransferDocument(header=header, tables=tables)


def read_jsonl(stream: TextIO) -> tuple[TransferHeader, tuple[TableBlock, ...]]:
    """Read and validate the ordered JSONL v1 transfer records."""

    reader = _JsonlReader()
    for line_number, raw_line in enumerate(stream, start=1):
        reader.consume(_parse_record(raw_line, line_number), line_number)
    return reader.finish()


@contextmanager
def open_document_writer(output: Path, header: TransferHeader) -> Iterator[JsonlStreamWriter]:
    """Open an atomic JSONL writer for incrementally generated table rows."""

    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.ExitStack() as stack:
            if _is_gzip_path(output):
                stream = stack.enter_context(
                    gzip.open(temporary, "wt", encoding="utf-8", newline="\n")
                )
            else:
                stream = stack.enter_context(temporary.open("w", encoding="utf-8", newline="\n"))
            writer = JsonlStreamWriter(stream, header)
            writer.write_header()
            yield writer
        temporary.replace(output)
    except (OSError, DatabaseTransferError) as error:
        with contextlib.suppress(OSError):
            temporary.unlink()
        if isinstance(error, DatabaseTransferError):
            raise
        raise DatabaseTransferError(f"could not write JSONL transfer file: {error}") from error
    except Exception:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


class JsonlStreamWriter:
    """Write validated JSONL table blocks without retaining their rows."""

    def __init__(self, stream: TextIO, header: TransferHeader) -> None:
        _validate_header(header)
        self._stream = stream
        self._header = header
        self.table_count = 0
        self.row_count = 0
        self._table_names: set[str] = set()
        self._finished = False

    def write_header(self) -> None:
        _write_record(
            self._stream,
            {
                "kind": "header",
                "format": self._header.format,
                "source": self._header.source,
            },
        )

    def write_table(
        self,
        header: TableBlockHeader,
        rows: Iterator[tuple[JSONValue, ...]],
    ) -> int:
        if self._finished:
            raise DatabaseTransferError("JSONL writer is already finished")
        _validate_table_header(header)
        if header.name in self._table_names:
            raise DatabaseTransferError("JSONL transfer file has duplicate table blocks")
        self._table_names.add(header.name)
        _write_table_header(self._stream, header)
        row_count = 0
        for row in rows:
            _validate_table_row(row, header, 0)
            _write_record(self._stream, {"kind": "row", "values": list(row)})
            row_count += 1
        _write_record(self._stream, {"kind": "end", "rows": row_count})
        self.table_count += 1
        self.row_count += row_count
        return row_count

    def finish(self) -> None:
        self._finished = True


def scan_document(  # noqa: PLR0912
    input_path: Path,
    row_validator: Callable[[TableBlockHeader, tuple[JSONValue, ...], int], None] | None = None,
    *,
    table_validator: Callable[[TableBlockHeader], None] | None = None,
) -> TransferPreview:
    """Scan a JSONL file once, retaining only headers and row counts."""

    try:
        with _open_input(input_path) as stream:
            line_iterator = enumerate(stream, start=1)
            header_line, raw_header = next(line_iterator, (0, ""))
            if not raw_header:
                raise DatabaseTransferError("JSONL transfer file is empty")
            header = _parse_header(_parse_record(raw_header, header_line), header_line)
            tables: list[TablePreview] = []
            names: set[str] = set()
            while True:
                line_number, raw_line = next(line_iterator, (0, ""))
                if not raw_line:
                    break
                record = _parse_record(raw_line, line_number)
                if record.get("kind") != "table":
                    raise DatabaseTransferError(
                        f"JSONL line {line_number} must start a table block"
                    )
                table_header = _parse_table_header(record, line_number)
                if table_header.name in names:
                    raise DatabaseTransferError("JSONL transfer file has duplicate table blocks")
                names.add(table_header.name)
                if table_validator is not None:
                    table_validator(table_header)
                row_count = 0
                while True:
                    row_line, raw_row = next(line_iterator, (0, ""))
                    if not raw_row:
                        raise DatabaseTransferError(
                            f"table block {table_header.name!r} is missing its end"
                        )
                    row_record = _parse_record(raw_row, row_line)
                    kind = row_record.get("kind")
                    if kind == "row":
                        row = _parse_row(row_record, table_header, row_line)
                        if row_validator is not None:
                            row_validator(table_header, row, row_line)
                        row_count += 1
                    elif kind == "end":
                        if not _valid_row_count(row_record.get("rows"), row_count):
                            raise DatabaseTransferError(
                                f"JSONL end record at line {row_line} has an invalid row count"
                            )
                        tables.append(TablePreview(table_header, row_count))
                        break
                    else:
                        raise DatabaseTransferError(
                            f"JSONL line {row_line} has an invalid table block record"
                        )
            return TransferPreview(header=header, tables=tuple(tables))
    except (EOFError, OSError, UnicodeError) as error:
        raise DatabaseTransferError(f"could not read JSONL transfer file: {error}") from error


def iter_document_tables(
    input_path: Path,
) -> Iterator[tuple[TransferHeader, TableBlockHeader, Iterator[tuple[JSONValue, ...]]]]:
    """Replay table blocks from a JSONL file one table at a time."""

    try:
        with _open_input(input_path) as stream:
            line_iterator = enumerate(stream, start=1)
            header_line, raw_header = next(line_iterator, (0, ""))
            if not raw_header:
                raise DatabaseTransferError("JSONL transfer file is empty")
            header = _parse_header(_parse_record(raw_header, header_line), header_line)
            names: set[str] = set()
            while True:
                line_number, raw_line = next(line_iterator, (0, ""))
                if not raw_line:
                    return
                record = _parse_record(raw_line, line_number)
                if record.get("kind") != "table":
                    raise DatabaseTransferError(
                        f"JSONL line {line_number} must start a table block"
                    )
                table_header = _parse_table_header(record, line_number)
                if table_header.name in names:
                    raise DatabaseTransferError("JSONL transfer file has duplicate table blocks")
                names.add(table_header.name)
                yield header, table_header, _iter_rows(line_iterator, table_header)
    except (EOFError, OSError, UnicodeError) as error:
        raise DatabaseTransferError(f"could not read JSONL transfer file: {error}") from error


@contextmanager
def _open_input(input_path: Path) -> Iterator[TextIO]:
    if _is_gzip_path(input_path):
        with gzip.open(input_path, "rt", encoding="utf-8", newline="") as stream:
            yield stream
    else:
        with input_path.open("r", encoding="utf-8", newline="") as stream:
            yield stream


def _iter_rows(
    line_iterator: Iterator[tuple[int, str]], header: TableBlockHeader
) -> Iterator[tuple[JSONValue, ...]]:
    row_count = 0
    while True:
        line_number, raw_line = next(line_iterator, (0, ""))
        if not raw_line:
            raise DatabaseTransferError(f"table block {header.name!r} is missing its end")
        record = _parse_record(raw_line, line_number)
        kind = record.get("kind")
        if kind == "row":
            row = _parse_row(record, header, line_number)
            row_count += 1
            yield row
        elif kind == "end":
            if not _valid_row_count(record.get("rows"), row_count):
                raise DatabaseTransferError(
                    f"JSONL end record at line {line_number} has an invalid row count"
                )
            return
        else:
            raise DatabaseTransferError(
                f"JSONL line {line_number} has an invalid table block record"
            )


def _parse_row(
    record: dict[str, object], header: TableBlockHeader, line_number: int
) -> tuple[JSONValue, ...]:
    values = record.get("values")
    if not isinstance(values, list):
        raise DatabaseTransferError(f"JSONL row at line {line_number} must contain a values array")
    if len(values) != len(header.columns):
        raise DatabaseTransferError(
            f"JSONL row at line {line_number} has the wrong number of values"
        )
    row = tuple(_parse_json_value(value, line_number) for value in values)
    _validate_declared_row(row, header, line_number)
    return row


def write_document(output: Path, document: TransferDocument) -> None:
    """Write one document atomically without exposing its contents in output."""

    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        if _is_gzip_path(output):
            with gzip.open(temporary, "wt", encoding="utf-8", newline="\n") as stream:
                write_jsonl(stream, document.header, document.tables)
        else:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                write_jsonl(stream, document.header, document.tables)
        temporary.replace(output)
    except (OSError, DatabaseTransferError) as error:
        with contextlib.suppress(OSError):
            temporary.unlink()
        if isinstance(error, DatabaseTransferError):
            raise
        raise DatabaseTransferError(f"could not write JSONL transfer file: {error}") from error


def write_jsonl(stream: TextIO, header: TransferHeader, tables: tuple[TableBlock, ...]) -> None:
    """Write a validated JSONL v1 transfer stream."""

    _validate_header(header)
    _validate_unique_table_names(tables)
    _write_record(
        stream,
        {"kind": "header", "format": header.format, "source": header.source},
    )
    for table in tables:
        _validate_table_block(table)
        _write_table_header(stream, table.header)
        for row in table.rows:
            _write_record(stream, {"kind": "row", "values": list(row)})
        _write_record(stream, {"kind": "end", "rows": len(table.rows)})


def encode_value(value: object, declared_type: str, source_timezone: tzinfo) -> JSONValue:
    """Encode one database value using the stable JSONL representation."""

    encoded = _encode_json_value(value)
    return _encode_declared_value(encoded, value, declared_type, source_timezone)


def decode_value(
    value: JSONValue,
    declared_type: str,
    target_timezone: tzinfo,
    datetime_precision: int | None = None,
) -> object:
    """Decode one JSONL value for parameter binding in a target database."""

    decoded = _decode_typed_value(value)
    return _decode_declared_value(
        decoded,
        declared_type,
        target_timezone,
        datetime_precision,
    )


def normalize_datetime(value: str, assumed_timezone: tzinfo) -> str:
    """Render a datetime value as a UTC ISO 8601 instant with ``Z``."""

    parsed = _parse_datetime(value)
    resolved = parsed.value
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=assumed_timezone)
    return _format_datetime(
        resolved.astimezone(UTC).replace(tzinfo=None),
        parsed.fractional_second,
        separator="T",
        suffix="Z",
    )


def format_datetime_for_database(
    value: str,
    target_timezone: tzinfo,
    datetime_precision: int | None = None,
) -> str:
    """Render a JSONL instant as a target database's timezone-less wall time."""

    parsed = _parse_datetime(value)
    resolved = parsed.value
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=target_timezone)
    return _format_datetime(
        resolved.astimezone(target_timezone).replace(tzinfo=None),
        parsed.fractional_second,
        separator=" ",
        precision=datetime_precision,
    )


def type_family(declared_type: str) -> str:
    """Map database declarations to portable families."""

    normalized = declared_type.upper().strip()
    if normalized == "UNIQUEIDENTIFIER":
        return "text"
    for family, tokens in _TYPE_FAMILY_RULES:
        if any(token in normalized for token in tokens):
            return family
    if _matches_exact_type(normalized, "DATE"):
        return "date"
    if _matches_exact_type(normalized, "TIME"):
        return "time"
    return "unknown"


_TYPE_FAMILY_RULES = (
    ("datetime", ("TIMESTAMP", "DATETIME")),
    ("blob", ("BLOB", "BINARY", "BYTEA")),
    ("boolean", ("BOOL",)),
    ("decimal", ("DECIMAL", "NUMERIC", "FIXED")),
    ("integer", ("INT", "SERIAL")),
    ("real", ("REAL", "FLOAT", "DOUBLE")),
    ("text", ("CHAR", "TEXT", "CLOB", "VARCHAR", "JSON", "ENUM", "SET")),
)


@dataclass
class _JsonlReader:
    header: TransferHeader | None = None
    tables: list[TableBlock] = field(default_factory=list)
    current_header: TableBlockHeader | None = None
    current_rows: list[tuple[JSONValue, ...]] = field(default_factory=list)

    def consume(self, record: dict[str, object], line_number: int) -> None:
        """Consume a single parsed record in the only valid record order."""

        if self.header is None:
            self._consume_header(record, line_number)
            return
        kind = record.get("kind")
        if kind == "table":
            self._start_table(record, line_number)
        elif kind == "row":
            self._add_row(record, line_number)
        elif kind == "end":
            self._finish_table(record, line_number)
        else:
            raise DatabaseTransferError(f"JSONL line {line_number} has an unknown record kind")

    def finish(self) -> tuple[TransferHeader, tuple[TableBlock, ...]]:
        """Complete parsing after the reader has seen all input lines."""

        if self.header is None:
            raise DatabaseTransferError("JSONL transfer file is empty")
        if self.current_header is not None:
            raise DatabaseTransferError(
                f"table block {self.current_header.name!r} is missing its end"
            )
        _validate_unique_table_names(self.tables)
        return self.header, tuple(self.tables)

    def _consume_header(self, record: dict[str, object], line_number: int) -> None:
        if record.get("kind") != "header":
            raise DatabaseTransferError("the first JSONL record must be a header")
        self.header = _parse_header(record, line_number)

    def _start_table(self, record: dict[str, object], line_number: int) -> None:
        if self.current_header is not None:
            raise DatabaseTransferError(
                f"table block {self.current_header.name!r} is missing its end record"
            )
        self.current_header = _parse_table_header(record, line_number)
        self.current_rows = []

    def _add_row(self, record: dict[str, object], line_number: int) -> None:
        if self.current_header is None:
            raise DatabaseTransferError(f"JSONL row at line {line_number} is outside a table block")
        values = record.get("values")
        if not isinstance(values, list):
            raise DatabaseTransferError(
                f"JSONL row at line {line_number} must contain a values array"
            )
        if len(values) != len(self.current_header.columns):
            raise DatabaseTransferError(
                f"JSONL row at line {line_number} has the wrong number of values"
            )
        row = tuple(_parse_json_value(value, line_number) for value in values)
        _validate_declared_row(row, self.current_header, line_number)
        self.current_rows.append(row)

    def _finish_table(self, record: dict[str, object], line_number: int) -> None:
        if self.current_header is None:
            raise DatabaseTransferError(
                f"JSONL end record at line {line_number} is outside a table block"
            )
        row_count = record.get("rows")
        if not _valid_row_count(row_count, len(self.current_rows)):
            raise DatabaseTransferError(
                f"JSONL end record at line {line_number} has an invalid row count"
            )
        self.tables.append(TableBlock(self.current_header, tuple(self.current_rows)))
        self.current_header = None
        self.current_rows = []


def _parse_record(raw_line: str, line_number: int) -> dict[str, object]:
    if not raw_line.strip():
        raise DatabaseTransferError(f"JSONL line {line_number} must not be blank")
    try:
        parsed = json.loads(raw_line, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as error:
        raise DatabaseTransferError(f"invalid JSONL at line {line_number}") from error
    if not isinstance(parsed, dict):
        raise DatabaseTransferError(f"JSONL line {line_number} must be an object")
    return cast(dict[str, object], parsed)


def _parse_header(record: dict[str, object], line_number: int) -> TransferHeader:
    source = record.get("source")
    if record.get("format") != TRANSFER_FORMAT or source not in (
        "sqlite",
        "mysql",
        "postgresql",
        "mssql",
    ):
        raise DatabaseTransferError(f"JSONL header at line {line_number} is invalid")
    return TransferHeader(format=TRANSFER_FORMAT, source=source)


def _parse_table_header(record: dict[str, object], line_number: int) -> TableBlockHeader:
    name = record.get("name")
    columns_value = record.get("columns")
    primary_key_value = record.get("primary_key")
    _validate_identifier(name, "table", line_number)
    if not isinstance(columns_value, list) or not columns_value:
        raise DatabaseTransferError(f"JSONL table record at line {line_number} has no columns")
    columns = tuple(_parse_column(value, line_number) for value in columns_value)
    _validate_columns(columns, line_number)
    primary_key = _parse_primary_key(primary_key_value, columns, line_number)
    return TableBlockHeader(cast(str, name), columns, primary_key)


def _parse_column(value: object, line_number: int) -> ColumnDefinition:
    if not isinstance(value, dict):
        raise DatabaseTransferError(
            f"JSONL table record at line {line_number} has an invalid column"
        )
    column = cast(dict[str, object], value)
    name = column.get("name")
    declared_type = column.get("declared_type")
    _validate_identifier(name, "column", line_number)
    if not isinstance(declared_type, str):
        raise DatabaseTransferError(
            f"JSONL table record at line {line_number} has an invalid column"
        )
    return ColumnDefinition(cast(str, name), declared_type)


def _parse_primary_key(
    value: object, columns: tuple[ColumnDefinition, ...], line_number: int
) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise DatabaseTransferError(
            f"JSONL table record at line {line_number} has an invalid primary key"
        )
    primary_key = tuple(cast(list[str], value))
    column_names = {column.name for column in columns}
    if len(set(primary_key)) != len(primary_key) or not set(primary_key).issubset(column_names):
        raise DatabaseTransferError(
            f"JSONL table record at line {line_number} has an invalid primary key"
        )
    return primary_key


def _parse_json_value(value: object, line_number: int) -> JSONValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and not math.isfinite(value):
            raise DatabaseTransferError(f"JSONL row at line {line_number} has an invalid number")
        return value
    if not isinstance(value, dict):
        raise DatabaseTransferError(f"JSONL row at line {line_number} has an invalid value")
    return _parse_typed_value(cast(dict[str, object], value), line_number)


def _parse_typed_value(value: dict[str, object], line_number: int) -> JSONValue:
    tag = value.get("$type")
    if tag == "blob":
        return _parse_blob(value, line_number)
    if tag == "decimal":
        return _parse_decimal(value, line_number)
    raise DatabaseTransferError(f"JSONL row at line {line_number} has an invalid typed value")


def _parse_blob(value: dict[str, object], line_number: int) -> JSONValue:
    encoded = value.get("base64")
    if not isinstance(encoded, str):
        raise DatabaseTransferError(f"JSONL row at line {line_number} has an invalid BLOB")
    try:
        base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise DatabaseTransferError(
            f"JSONL row at line {line_number} has an invalid BLOB"
        ) from error
    return {"$type": "blob", "base64": encoded}


def _parse_decimal(value: dict[str, object], line_number: int) -> JSONValue:
    decimal_value = value.get("value")
    if not isinstance(decimal_value, str):
        raise DatabaseTransferError(f"JSONL row at line {line_number} has an invalid Decimal")
    try:
        parsed = Decimal(decimal_value)
    except InvalidOperation as error:
        raise DatabaseTransferError(
            f"JSONL row at line {line_number} has an invalid Decimal"
        ) from error
    if not parsed.is_finite():
        raise DatabaseTransferError(f"JSONL row at line {line_number} has an invalid Decimal")
    return {"$type": "decimal", "value": decimal_value}


def _encode_json_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DatabaseTransferError("database value contains a non-finite float")
        return value
    if isinstance(value, Decimal):
        return _decimal_tag(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {
            "$type": "blob",
            "base64": base64.b64encode(bytes(value)).decode("ascii"),
        }
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    raise DatabaseTransferError("database value has an unsupported type")


def _encode_declared_value(
    encoded: JSONValue,
    value: object,
    declared_type: str,
    source_timezone: tzinfo,
) -> JSONValue:
    if encoded is None:
        return None
    family = type_family(declared_type)
    if family == "decimal":
        return _decimal_tag(value)
    if family == "datetime":
        return normalize_datetime(_require_string(encoded, "datetime"), source_timezone)
    if family == "date":
        _parse_date(_require_string(encoded, "DATE"))
    if family == "time":
        _parse_time(_require_string(encoded, "TIME"))
    if family == "blob" and not _is_type_tag(encoded, "blob"):
        raise DatabaseTransferError("BLOB column contains a non-BLOB value")
    return encoded


def _decode_typed_value(value: JSONValue) -> object:
    if not isinstance(value, dict):
        return value
    tag = value.get("$type")
    if tag == "blob":
        encoded = value.get("base64")
        if isinstance(encoded, str):
            return base64.b64decode(encoded, validate=True)
    if tag == "decimal":
        decimal_value = value.get("value")
        if isinstance(decimal_value, str):
            return Decimal(decimal_value)
    raise DatabaseTransferError("JSONL typed value has an unsupported type")


def _decode_declared_value(
    value: object,
    declared_type: str,
    target_timezone: tzinfo,
    datetime_precision: int | None,
) -> object:
    if value is None:
        return None
    family = type_family(declared_type)
    if family == "datetime":
        return format_datetime_for_database(
            _require_string(value, "datetime"),
            target_timezone,
            datetime_precision,
        )
    if family == "date":
        _parse_date(_require_string(value, "DATE"))
    if family == "time":
        _parse_time(_require_string(value, "TIME"))
    if family == "boolean":
        return _decode_boolean(value)
    return value


def _decimal_tag(value: object) -> JSONValue:
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as error:
        raise DatabaseTransferError("DECIMAL column contains an invalid value") from error
    if not decimal_value.is_finite():
        raise DatabaseTransferError("DECIMAL column contains a non-finite value")
    return {"$type": "decimal", "value": format(decimal_value, "f")}


def _decode_boolean(value: object) -> bool:
    """Normalize portable JSON boolean representations for database binding."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"0", "false", "f", "no", "n"}:
            return False
        if normalized in {"1", "true", "t", "yes", "y"}:
            return True
    raise DatabaseTransferError("BOOLEAN column contains an invalid boolean value")


def _validate_declared_row(
    row: tuple[JSONValue, ...], header: TableBlockHeader, line_number: int
) -> None:
    for value, column in zip(row, header.columns, strict=True):
        _validate_declared_value(value, column.declared_type, line_number)


def _validate_declared_value(value: JSONValue, declared_type: str, line_number: int) -> None:
    if value is None:
        return
    family = type_family(declared_type)
    if family == "datetime":
        _parse_datetime(_require_string(value, "datetime", line_number))
    elif family == "date":
        _parse_date(_require_string(value, "DATE", line_number))
    elif family == "time":
        _parse_time(_require_string(value, "TIME", line_number))
    elif family in {"blob", "decimal"} and not _is_type_tag(value, family):
        raise DatabaseTransferError(
            f"JSONL row at line {line_number} has an invalid {family.upper()}"
        )


def _require_string(value: object, label: str, line_number: int | None = None) -> str:
    if isinstance(value, str):
        return value
    if line_number is None:
        raise DatabaseTransferError(f"{label} column contains a non-{label} value")
    raise DatabaseTransferError(f"JSONL row at line {line_number} has an invalid {label}")


def _is_type_tag(value: JSONValue, tag: str) -> bool:
    return isinstance(value, dict) and value.get("$type") == tag


@dataclass(frozen=True)
class _ParsedDatetime:
    value: datetime
    fractional_second: str


def _parse_datetime(value: str) -> _ParsedDatetime:
    parsed = _parse_iso_datetime(value)
    if parsed is not None:
        return parsed
    parsed = _parse_go_datetime(value)
    if parsed is not None:
        return parsed
    raise DatabaseTransferError("JSONL datetime value is not ISO 8601")


def _parse_iso_datetime(value: str) -> _ParsedDatetime | None:
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        if "T" not in normalized and " " not in normalized:
            return None
        fraction = _fractional_second(normalized)
        return _ParsedDatetime(
            datetime.fromisoformat(_python_datetime_value(normalized, fraction)),
            fraction,
        )
    except ValueError:
        return None


def _parse_go_datetime(value: str) -> _ParsedDatetime | None:
    match = _GO_DATETIME.fullmatch(value)
    if match is None:
        return None
    fraction = match.group("fraction") or ""
    offset = match.group("offset")
    normalized_offset = f"{offset[:3]}:{offset[3:]}"
    normalized = f"{match.group('date')}T{match.group('time')}"
    if fraction:
        normalized += f".{fraction}"
    normalized += normalized_offset
    try:
        return _ParsedDatetime(
            datetime.fromisoformat(_python_datetime_value(normalized, fraction)),
            fraction,
        )
    except ValueError:
        return None


def _python_datetime_value(value: str, fraction: str) -> str:
    if len(fraction) <= MAX_DATETIME_FRACTIONAL_PRECISION:
        return value
    match = _FRACTIONAL_SECOND.search(value)
    if match is None:
        raise DatabaseTransferError("datetime fractional seconds cannot be parsed")
    return (
        value[: match.start("value")]
        + fraction[:MAX_DATETIME_FRACTIONAL_PRECISION]
        + value[match.end("value") :]
    )


def _fractional_second(value: str) -> str:
    match = _FRACTIONAL_SECOND.search(value)
    return match.group("value") if match is not None else ""


def _format_datetime(
    value: datetime,
    fractional_second: str,
    *,
    separator: str,
    suffix: str = "",
    precision: int | None = None,
) -> str:
    fraction = fractional_second if precision is None else fractional_second[:precision]
    formatted = value.isoformat(sep=separator, timespec="seconds")
    return f"{formatted}.{fraction}{suffix}" if fraction else f"{formatted}{suffix}"


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise DatabaseTransferError("JSONL DATE value is not ISO 8601") from error


def _parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as error:
        raise DatabaseTransferError("JSONL TIME value is not ISO 8601") from error


def _validate_header(header: TransferHeader) -> None:
    if header.format != TRANSFER_FORMAT or header.source not in (
        "sqlite",
        "mysql",
        "postgresql",
        "mssql",
    ):
        raise DatabaseTransferError("transfer header is invalid")


def _validate_table_block(table: TableBlock) -> None:
    header = table.header
    _validate_table_header(header)
    for row in table.rows:
        _validate_table_row(row, header, 0)


def _validate_table_header(header: TableBlockHeader) -> None:
    _validate_identifier(header.name, "table", 0)
    _validate_columns(header.columns, 0)
    _parse_primary_key(list(header.primary_key), header.columns, 0)


def _validate_table_row(
    row: tuple[JSONValue, ...], header: TableBlockHeader, line_number: int
) -> None:
    if len(row) != len(header.columns):
        raise DatabaseTransferError(
            f"JSONL row at line {line_number} has the wrong number of values"
        )
    for value in row:
        _parse_json_value(value, line_number)
    _validate_declared_row(row, header, line_number)


def _validate_columns(columns: tuple[ColumnDefinition, ...], line_number: int) -> None:
    if len({column.name for column in columns}) != len(columns):
        raise DatabaseTransferError(
            f"JSONL table record at line {line_number} has duplicate columns"
        )


def _validate_identifier(value: object, label: str, line_number: int) -> None:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise DatabaseTransferError(
            f"JSONL {label} record at line {line_number} has an invalid name"
        )


def _validate_unique_table_names(
    tables: list[TableBlock] | tuple[TableBlock, ...],
) -> None:
    names = [table.header.name for table in tables]
    if len(set(names)) != len(names):
        raise DatabaseTransferError("JSONL transfer file has duplicate table blocks")


def _write_table_header(stream: TextIO, header: TableBlockHeader) -> None:
    _write_record(
        stream,
        {
            "kind": "table",
            "name": header.name,
            "columns": [
                {"name": column.name, "declared_type": column.declared_type}
                for column in header.columns
            ],
            "primary_key": list(header.primary_key),
        },
    )


def _write_record(stream: TextIO, record: dict[str, object]) -> None:
    stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    stream.write("\n")


def _valid_row_count(value: object, actual: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == actual


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON numeric constant is invalid: {value}")


def _matches_exact_type(normalized: str, name: str) -> bool:
    return normalized == name or normalized.startswith((f"{name} ", f"{name}("))
