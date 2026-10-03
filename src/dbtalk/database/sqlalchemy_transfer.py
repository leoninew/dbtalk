"""SQLAlchemy Core adapter for SQLite, MySQL and PostgreSQL JSONL transfer."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import timedelta
from time import monotonic
from typing import Any, Literal

from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import text
from sqlalchemy.sql.elements import TextClause

from .dsn import ParsedDsn, dsn_from_environment, parse_dsn
from .format import (
    TRANSFER_FORMAT,
    encode_value,
    iter_document_tables,
    open_document_writer,
    scan_document,
)
from .models import (
    ColumnDefinition,
    DatabaseTransferError,
    ExportOptions,
    ImportOptions,
    JSONValue,
    TableBlockHeader,
    TableSchema,
    TransferConnection,
    TransferHeader,
    TransferSummary,
)
from .mysql import _mysql_time_of_day, _normalize_mysql_zero_date
from .schema import (
    order_table_names,
    select_table_schemas,
    select_transfer_preview,
    table_row_values,
    validate_import_row,
    validate_target_table,
)

logger = logging.getLogger("dbtalk")
BATCH_SIZE = 1000
BATCH_BYTE_BUDGET = 8 * 1024 * 1024
PROGRESS_INTERVAL_SECONDS = 5.0


class _TableProgress:
    def __init__(
        self,
        operation: Literal["import", "export"],
        parsed: ParsedDsn,
        table: str,
        total_rows: int | None,
        committed_rows: int,
    ) -> None:
        self.operation = operation
        self.dialect = parsed.dialect
        self.table = table
        self.total_rows = total_rows
        self.committed_rows = committed_rows
        self.rows = 0
        self.started_at = monotonic()
        self.last_report_at = self.started_at

    def advance(self, rows: int) -> None:
        self.rows += rows
        now = monotonic()
        if now - self.last_report_at >= PROGRESS_INTERVAL_SECONDS:
            self._report(now, "pending" if self.operation == "import" else "writing")

    def finish(self, status: str) -> None:
        self._report(monotonic(), status)

    def _report(self, now: float, status: str) -> None:
        elapsed = now - self.started_at
        rate = self.rows / elapsed if elapsed > 0 else 0.0
        if self.operation == "import":
            logger.info(
                "database import progress dialect=%s table=%s executed_rows=%d total_rows=%s "
                "pending_rows=%d committed_rows=%d rows_per_second=%.2f "
                "elapsed_seconds=%.2f status=%s",
                self.dialect,
                self.table,
                self.rows,
                self.total_rows,
                self.rows if status == "pending" else 0,
                self.committed_rows + (self.rows if status == "committed" else 0),
                rate,
                elapsed,
                status,
            )
        else:
            logger.info(
                "database export progress dialect=%s table=%s written_rows=%d total_rows=unknown "
                "rows_per_second=%.2f elapsed_seconds=%.2f status=%s",
                self.dialect,
                self.table,
                self.rows,
                rate,
                elapsed,
                status,
            )
        self.last_report_at = now


def export_sqlalchemy(options: ExportOptions) -> TransferSummary:
    parsed = _connection_dsn(options.connection)
    engine = _create_engine(parsed)
    logger.info("database export connecting %s", parsed.display)
    try:
        with engine.connect() as connection:
            _prepare_connection(connection, parsed, read_only=True)
            schemas = select_table_schemas(
                _load_schemas(connection),
                options.include_tables,
                options.exclude_tables,
            )
            table_count = 0
            row_count = 0
            with open_document_writer(
                options.output,
                TransferHeader(TRANSFER_FORMAT, parsed.dialect),
            ) as writer:
                for schema in _ordered_schemas(schemas):
                    header = TableBlockHeader(schema.name, schema.columns, schema.primary_key)
                    if parsed.dialect == "mysql":
                        result = connection.exec_driver_sql(_select_sql(connection, schema))
                        written = writer.write_table(
                            header, _encoded_rows(result, schema, options, parsed)
                        )
                    else:
                        progress = _TableProgress("export", parsed, schema.name, None, 0)
                        with connection.exec_driver_sql(
                            _select_sql(connection, schema),
                            execution_options={"yield_per": BATCH_SIZE, "stream_results": True},
                        ) as result:
                            written = writer.write_table(
                                header, _encoded_rows(result, schema, options, parsed, progress)
                            )
                        progress.finish("table_completed")
                    table_count += 1
                    row_count += written
                    logger.info(
                        "database export table completed dialect=%s table=%s rows=%d",
                        parsed.dialect,
                        schema.name,
                        written,
                    )
                writer.finish()
            connection.rollback()
            return TransferSummary(table_count, row_count)
    except DatabaseTransferError:
        raise
    except SQLAlchemyError as error:
        raise DatabaseTransferError(f"{parsed.dialect.capitalize()} export failed") from error
    finally:
        engine.dispose()


def import_sqlalchemy(options: ImportOptions) -> TransferSummary:
    parsed = _connection_dsn(options.connection)
    engine = _create_engine(parsed)
    logger.info("database import connecting %s mode=%s", parsed.display, options.mode)
    try:
        with engine.connect() as connection:
            _prepare_connection(connection, parsed, read_only=False)
            schemas = _load_schemas(connection)
            validated_targets: set[str] = set()

            def validate_row(
                header: TableBlockHeader, row: tuple[JSONValue, ...], line: int
            ) -> None:
                validate_import_row(row, header, options.mode, line)
                if parsed.dialect == "mysql" or header.name in options.exclude_tables:
                    return
                if options.include_tables and header.name not in options.include_tables:
                    return
                target = schemas.get(header.name)
                if target is None:
                    raise DatabaseTransferError(f"target table {header.name!r} does not exist")
                if header.name not in validated_targets:
                    validate_target_table(header, target, options.mode)
                    validated_targets.add(header.name)
                try:
                    _target_values(row, header, target, options, parsed)
                except DatabaseTransferError as error:
                    raise DatabaseTransferError(
                        f"table {header.name!r} at JSONL line {line}: {error}"
                    ) from error

            preview = scan_document(
                options.input,
                row_validator=validate_row,
            )
            if preview.header.source not in ("sqlite", "mysql", "postgresql"):
                raise DatabaseTransferError("JSONL source driver is invalid")
            selected = select_transfer_preview(
                preview,
                options.include_tables,
                options.exclude_tables,
            )
            selected_schemas: dict[str, TableSchema] = {}
            for table in selected.tables:
                target = schemas.get(table.header.name)
                if target is None:
                    raise DatabaseTransferError(
                        f"target table {table.header.name!r} does not exist"
                    )
                validate_target_table(table.header, target, options.mode)
                selected_schemas[table.header.name] = target
            ordered_names = order_table_names(
                tuple(table.header.name for table in selected.tables), schemas
            )
            if ordered_names != tuple(table.header.name for table in selected.tables):
                raise DatabaseTransferError(
                    "JSONL table order does not satisfy target foreign-key order"
                )

            connection.rollback()
            selected_names = set(ordered_names)
            table_row_counts = {table.header.name: table.row_count for table in selected.tables}
            committed_rows = 0
            expected_index = 0
            for _, table_header, rows in iter_document_tables(options.input):
                if table_header.name not in selected_names:
                    for _ in rows:
                        pass
                    continue
                if table_header.name != ordered_names[expected_index]:
                    raise DatabaseTransferError(
                        "JSONL table order changed between preflight and import"
                    )
                written = _import_table(
                    connection,
                    table_header,
                    rows,
                    selected_schemas[table_header.name],
                    options,
                    parsed,
                    total_rows=table_row_counts[table_header.name],
                    committed_rows=committed_rows,
                )
                committed_rows += written
                expected_index += 1
                logger.info(
                    "database import table completed dialect=%s table=%s rows=%d mode=%s",
                    parsed.dialect,
                    table_header.name,
                    written,
                    options.mode,
                )
            if expected_index != len(ordered_names):
                raise DatabaseTransferError("JSONL table replay is incomplete")
            _verify_database(connection, parsed)
            return TransferSummary(
                len(selected.tables), sum(table.row_count for table in selected.tables)
            )
    except DatabaseTransferError:
        raise
    except SQLAlchemyError as error:
        raise DatabaseTransferError(f"{parsed.dialect.capitalize()} import failed") from error
    finally:
        engine.dispose()


def _connection_dsn(connection: TransferConnection) -> ParsedDsn:
    if (connection.dsn is None) == (connection.dsn_env is None):
        raise DatabaseTransferError("provide exactly one of dsn or dsn_env")
    if connection.dsn is not None:
        parsed = parse_dsn(connection.dsn)
    else:
        parsed = dsn_from_environment(connection.dsn_env)
    if parsed.dialect != connection.driver:
        raise DatabaseTransferError(
            f"connection DSN dialect {parsed.dialect!r} does not match {connection.driver!r}"
        )
    return parsed


def _create_engine(parsed: ParsedDsn) -> Engine:
    try:
        from sqlalchemy import create_engine

        return create_engine(parsed.url)
    except SQLAlchemyError as error:
        raise DatabaseTransferError("database engine could not be created") from error


def _prepare_connection(connection: Connection, parsed: ParsedDsn, *, read_only: bool) -> None:
    try:
        if parsed.dialect == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        elif parsed.dialect == "mysql" and read_only:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT")
    except SQLAlchemyError as error:
        raise DatabaseTransferError("database session could not be prepared") from error


def _load_schemas(connection: Connection) -> dict[str, TableSchema]:
    inspector = inspect(connection)
    schemas: dict[str, TableSchema] = {}
    try:
        for name in sorted(inspector.get_table_names()):
            columns = tuple(
                ColumnDefinition(str(column["name"]), str(column.get("type") or ""))
                for column in inspector.get_columns(name)
            )
            primary = inspector.get_pk_constraint(name).get("constrained_columns") or []
            foreign_keys: list[str] = []
            for foreign_key in inspector.get_foreign_keys(name):
                referenced = foreign_key.get("referred_table")
                if isinstance(referenced, str) and referenced not in foreign_keys:
                    foreign_keys.append(referenced)
            schemas[name] = TableSchema(
                name=name,
                columns=columns,
                primary_key=tuple(str(column) for column in primary),
                foreign_keys=tuple(foreign_keys),
            )
    except SQLAlchemyError as error:
        raise DatabaseTransferError("database schema inspection failed") from error
    return schemas


def _ordered_schemas(schemas: dict[str, TableSchema]) -> tuple[TableSchema, ...]:
    ordered_names = order_table_names(tuple(schemas), schemas)
    return tuple(schemas[name] for name in ordered_names)


def _select_sql(connection: Connection, schema: TableSchema) -> str:
    quoted_columns = ", ".join(
        _quote_identifier(connection, column.name) for column in schema.columns
    )
    return f"SELECT {quoted_columns} FROM {_quote_identifier(connection, schema.name)}"


def _encoded_rows(
    result: Any,
    schema: TableSchema,
    options: ExportOptions,
    parsed: ParsedDsn,
    progress: _TableProgress | None = None,
) -> Iterator[tuple[JSONValue, ...]]:
    while batch := result.fetchmany(BATCH_SIZE):
        for row in batch:
            values: list[object] = []
            for value, column in zip(row, schema.columns, strict=True):
                if parsed.dialect == "mysql":
                    value = _normalize_mysql_zero_date(value, column.declared_type, options)
                    if isinstance(value, timedelta):
                        value = _mysql_time_of_day(value)
                values.append(value)
            yield tuple(
                encode_value(value, column.declared_type, options.timezone)
                for value, column in zip(values, schema.columns, strict=True)
            )
            if progress is not None:
                progress.advance(1)


def _parameter_batches(
    rows: Iterator[tuple[JSONValue, ...]],
    header: TableBlockHeader,
    target: TableSchema,
    options: ImportOptions,
    parsed: ParsedDsn,
    parameter_names: list[str],
) -> Iterator[list[dict[str, object]]]:
    batch: list[dict[str, object]] = []
    batch_bytes = 0
    for row in rows:
        values = _target_values(row, header, target, options, parsed)
        row_bytes = sum(_value_bytes(value) for value in values)
        if batch and batch_bytes + row_bytes > BATCH_BYTE_BUDGET:
            yield batch
            batch = []
            batch_bytes = 0
        batch.append(_parameters(values, parameter_names))
        batch_bytes += row_bytes
        if len(batch) >= BATCH_SIZE or batch_bytes >= BATCH_BYTE_BUDGET:
            yield batch
            batch = []
            batch_bytes = 0
    if batch:
        yield batch


def _value_bytes(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bytes):
        return len(value)
    return len(str(value).encode("utf-8"))


def _execute_batches(
    connection: Connection,
    statement: TextClause,
    batches: Iterator[list[dict[str, object]]],
    progress: _TableProgress,
) -> int:
    try:
        with connection.begin():
            for parameters in batches:
                connection.execute(statement, parameters).close()
                progress.advance(len(parameters))
                # Release the completed batch before decoding the next one.
                del parameters
    except Exception:
        progress.finish("rolled_back")
        raise
    progress.finish("committed")
    return progress.rows


def _import_table(
    connection: Connection,
    header: TableBlockHeader,
    rows: Iterator[tuple[JSONValue, ...]],
    target: TableSchema,
    options: ImportOptions,
    parsed: ParsedDsn,
    *,
    total_rows: int,
    committed_rows: int,
) -> int:
    names = [column.name for column in header.columns]
    quoted_table = _quote_identifier(connection, header.name)
    quoted_columns = ", ".join(_quote_identifier(connection, name) for name in names)
    parameter_names = [f"value_{index}" for index in range(len(names))]
    placeholders = ", ".join(f":{name}" for name in parameter_names)
    insert_sql = f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES ({placeholders})"
    insert_statement = text(insert_sql)
    primary_key = header.primary_key
    key_positions = [names.index(name) for name in primary_key]
    written = 0
    try:
        if parsed.dialect != "mysql":
            if options.mode == "upsert":
                conflict_columns = ", ".join(
                    _quote_identifier(connection, name) for name in primary_key
                )
                assignments = ", ".join(
                    f"{_quote_identifier(connection, name)} = "
                    f"excluded.{_quote_identifier(connection, name)}"
                    for name in names
                    if name not in primary_key
                )
                action = f"DO UPDATE SET {assignments}" if assignments else "DO NOTHING"
                insert_statement = text(f"{insert_sql} ON CONFLICT ({conflict_columns}) {action}")
            return _execute_batches(
                connection,
                insert_statement,
                _parameter_batches(rows, header, target, options, parsed, parameter_names),
                _TableProgress("import", parsed, header.name, total_rows, committed_rows),
            )
        with connection.begin():
            if options.mode == "insert":
                for row in rows:
                    values = _target_values(row, header, target, options, parsed)
                    connection.execute(insert_statement, _parameters(values, parameter_names))
                    written += 1
            else:
                non_primary = [name for name in names if name not in primary_key]
                update_positions = [names.index(name) for name in non_primary]
                where = " AND ".join(
                    f"{_quote_identifier(connection, name)} = :key_{index}"
                    for index, name in enumerate(primary_key)
                )
                exists_statement = text(f"SELECT 1 FROM {quoted_table} WHERE {where} LIMIT 1")
                update_statement = (
                    text(
                        f"UPDATE {quoted_table} SET "
                        + ", ".join(
                            f"{_quote_identifier(connection, name)} = :update_{index}"
                            for index, name in enumerate(non_primary)
                        )
                        + f" WHERE {where}"
                    )
                    if non_primary
                    else None
                )
                for row in rows:
                    values = _target_values(row, header, target, options, parsed)
                    keys = tuple(values[position] for position in key_positions)
                    key_parameters = {f"key_{index}": value for index, value in enumerate(keys)}
                    exists = connection.execute(exists_statement, key_parameters).first()
                    if exists is None:
                        connection.execute(insert_statement, _parameters(values, parameter_names))
                    elif update_statement is not None:
                        update_parameters = {
                            f"update_{index}": values[position]
                            for index, position in enumerate(update_positions)
                        }
                        connection.execute(
                            update_statement,
                            {**update_parameters, **key_parameters},
                        )
                    written += 1
    except SQLAlchemyError as error:
        raise DatabaseTransferError(
            f"{parsed.dialect.capitalize()} import failed for table {header.name!r}"
        ) from error
    return written


def _target_values(
    row: tuple[JSONValue, ...],
    header: TableBlockHeader,
    target: TableSchema,
    options: ImportOptions,
    parsed: ParsedDsn,
) -> tuple[object, ...]:
    default_precision = 0 if parsed.dialect == "mysql" else None
    values = table_row_values(row, header, target, options.timezone, default_precision)
    if parsed.dialect == "sqlite":
        return tuple(
            format(value, "f") if hasattr(value, "as_tuple") else value for value in values
        )
    return values


def _parameters(values: tuple[object, ...], names: list[str]) -> dict[str, object]:
    return dict(zip(names, values, strict=True))


def _quote_identifier(connection: Connection, identifier: str) -> str:
    if not identifier or "\x00" in identifier:
        raise DatabaseTransferError("database identifier is invalid")
    return connection.dialect.identifier_preparer.quote(identifier)


def _verify_database(connection: Connection, parsed: ParsedDsn) -> None:
    if parsed.dialect != "sqlite":
        return
    try:
        integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar()
        if integrity != "ok":
            raise DatabaseTransferError("SQLite integrity_check failed")
        if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
            raise DatabaseTransferError("SQLite foreign_key_check failed")
    except SQLAlchemyError as error:
        raise DatabaseTransferError("SQLite integrity verification failed") from error


__all__ = ["export_sqlalchemy", "import_sqlalchemy"]
