"""SQLAlchemy Core adapter for JSONL database transfer."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import suppress
from datetime import timedelta
from time import monotonic
from typing import Any, Literal

from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import text
from sqlalchemy.sql.elements import TextClause

from .dsn import ParsedDsn, dsn_from_environment, parse_dsn
from .format import (
    TRANSFER_FORMAT,
    encode_value,
    iter_document_tables,
    normalize_mssql_export_value,
    open_document_writer,
    scan_document,
)
from .models import (
    ColumnDefinition,
    ColumnMetadata,
    DatabaseTransferError,
    ExportOptions,
    ImportOptions,
    JSONValue,
    TableBlockHeader,
    TableSchema,
    TransferConnection,
    TransferHeader,
    TransferMode,
    TransferSummary,
)
from .mysql import _mysql_time_of_day, _normalize_mysql_zero_date
from .schema import (
    mssql_column_definition,
    order_table_names,
    select_table_names,
    select_transfer_preview,
    table_row_values,
    validate_export_table,
    validate_import_row,
    validate_selected_foreign_keys,
    validate_target_table,
)

logger = logging.getLogger("dbtalk")
BATCH_SIZE = 1000
BATCH_BYTE_BUDGET = 8 * 1024 * 1024
PROGRESS_INTERVAL_SECONDS = 5.0
_MSSQL_COLUMNS = text("""
SELECT col.name, typ.name AS type_name, col.max_length, col.precision, col.scale,
       col.is_nullable, col.is_identity, col.is_computed, col.generated_always_type,
       col.collation_name, typ.is_user_defined, col.default_object_id,
       CONVERT(int, COLLATIONPROPERTY(col.collation_name, 'CodePage')) AS code_page
FROM sys.columns AS col
JOIN sys.tables AS tbl ON tbl.object_id = col.object_id
JOIN sys.schemas AS sch ON sch.schema_id = tbl.schema_id
JOIN sys.types AS typ ON typ.user_type_id = col.user_type_id
WHERE sch.name = :schema_name AND tbl.name = :table_name
ORDER BY col.column_id
""")


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
            schemas = _load_schemas(connection, options.include_tables, options.exclude_tables)
            validate_selected_foreign_keys(schemas)
            for schema in schemas.values():
                validate_export_table(schema)
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
                        execution_options = (
                            {}
                            if parsed.dialect == "mssql"
                            else {"yield_per": BATCH_SIZE, "stream_results": True}
                        )
                        with connection.exec_driver_sql(
                            _select_sql(connection, schema),
                            execution_options=execution_options,
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
            schemas: dict[str, TableSchema] = {}
            inspector = inspect(connection)
            schema_name = inspector.default_schema_name if parsed.dialect == "mssql" else None
            available_tables = set(inspector.get_table_names(schema=schema_name))

            def validate_table(header: TableBlockHeader) -> None:
                if header.name in options.exclude_tables:
                    return
                if options.include_tables and header.name not in options.include_tables:
                    return
                if header.name not in available_tables:
                    raise DatabaseTransferError(f"target table {header.name!r} does not exist")
                target = _load_table_schema(connection, inspector, header.name)
                validate_target_table(header, target, options.mode)
                schemas[header.name] = target

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
                try:
                    _target_values(row, header, target, options, parsed)
                except DatabaseTransferError as error:
                    raise DatabaseTransferError(
                        f"table {header.name!r} at JSONL line {line}: {error}"
                    ) from error

            preview = scan_document(
                options.input,
                row_validator=validate_row,
                table_validator=validate_table,
            )
            if preview.header.source not in ("sqlite", "mysql", "postgresql", "mssql"):
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
            table_headers = {table.header.name: table.header for table in selected.tables}
            committed_rows = 0
            expected_index = 0
            for _, table_header, rows in iter_document_tables(options.input):
                if table_header.name not in selected_names:
                    for _ in rows:
                        pass
                    continue
                if (
                    expected_index >= len(ordered_names)
                    or table_header.name != ordered_names[expected_index]
                    or table_header != table_headers[table_header.name]
                ):
                    raise DatabaseTransferError(
                        "JSONL metadata changed between preflight and import"
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
    except (SQLAlchemyError, ImportError) as error:
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


def _load_schemas(
    connection: Connection,
    include_tables: tuple[str, ...] = (),
    exclude_tables: tuple[str, ...] = (),
) -> dict[str, TableSchema]:
    inspector = inspect(connection)
    try:
        schema = inspector.default_schema_name if connection.dialect.name == "mssql" else None
        names = select_table_names(
            dict.fromkeys(sorted(inspector.get_table_names(schema=schema))),
            include_tables,
            exclude_tables,
            "source database",
        )
        return {name: _load_table_schema(connection, inspector, name) for name in names}
    except SQLAlchemyError as error:
        raise DatabaseTransferError("database schema inspection failed") from error


def _load_table_schema(connection: Connection, inspector: Inspector, name: str) -> TableSchema:
    is_mssql = connection.dialect.name == "mssql"
    schema = inspector.default_schema_name if is_mssql else None
    metadata: tuple[ColumnMetadata, ...] = ()
    if is_mssql:
        if not isinstance(schema, str) or not schema:
            raise DatabaseTransferError("mssql default schema could not be determined")
        with connection.execute(
            _MSSQL_COLUMNS, {"schema_name": schema, "table_name": name}
        ) as result:
            metadata = tuple(
                ColumnMetadata(
                    name=row["name"],
                    type_name=row["type_name"].lower(),
                    max_length=row["max_length"],
                    precision=row["precision"],
                    scale=row["scale"],
                    nullable=bool(row["is_nullable"]),
                    identity=bool(row["is_identity"]),
                    computed=bool(row["is_computed"]),
                    generated_always=bool(row["generated_always_type"]),
                    collation=row["collation_name"],
                    user_defined=bool(row["is_user_defined"]),
                    has_default=bool(row["default_object_id"]),
                    code_page=row["code_page"],
                )
                for row in result.mappings()
            )
        if not metadata:
            raise DatabaseTransferError(f"mssql table {name!r} has no visible column metadata")
        columns = tuple(mssql_column_definition(column, name) for column in metadata)
    else:
        columns = tuple(
            ColumnDefinition(str(column["name"]), str(column.get("type") or ""))
            for column in inspector.get_columns(name, schema=schema)
        )
    primary = tuple(
        inspector.get_pk_constraint(name, schema=schema).get("constrained_columns") or ()
    )
    if is_mssql and not set(primary).issubset(column.name for column in columns):
        raise DatabaseTransferError(f"mssql table {name!r} has incomplete primary-key metadata")
    foreign_keys: list[str] = []
    for foreign in inspector.get_foreign_keys(name, schema=schema):
        if is_mssql and foreign.get("referred_schema") not in (None, schema):
            raise DatabaseTransferError(f"mssql table {name!r} has a cross-schema foreign key")
        parent = foreign.get("referred_table")
        if is_mssql and (not isinstance(parent, str) or not parent):
            raise DatabaseTransferError(f"mssql table {name!r} has an unidentified foreign key")
        if isinstance(parent, str) and parent not in foreign_keys:
            foreign_keys.append(parent)
    return TableSchema(
        name,
        columns,
        primary,
        tuple(foreign_keys),
        schema_name=schema,
        dialect="mssql" if is_mssql else None,
        column_metadata=metadata,
    )


def _ordered_schemas(schemas: dict[str, TableSchema]) -> tuple[TableSchema, ...]:
    ordered_names = order_table_names(tuple(schemas), schemas)
    return tuple(schemas[name] for name in ordered_names)


def _select_sql(connection: Connection, schema: TableSchema) -> str:
    quoted_columns = ", ".join(
        _quote_identifier(connection, column.name) for column in schema.columns
    )
    quoted_table = _quote_identifier(connection, schema.name)
    if schema.schema_name is not None:
        quoted_table = f"{_quote_identifier(connection, schema.schema_name)}.{quoted_table}"
    return f"SELECT {quoted_columns} FROM {quoted_table}"


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
                elif parsed.dialect == "mssql":
                    value = normalize_mssql_export_value(value, column)
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
    parameter_names = [f"value_{index}" for index in range(len(names))]
    if parsed.dialect == "mssql":
        return _import_mssql_table(
            connection,
            options.mode,
            header,
            _parameter_batches(rows, header, target, options, parsed, parameter_names),
            target,
            total_rows,
            _TableProgress("import", parsed, header.name, total_rows, committed_rows),
        )
    quoted_table = _quote_identifier(connection, header.name)
    quoted_columns = ", ".join(_quote_identifier(connection, name) for name in names)
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


def _import_mssql_table(
    connection: Connection,
    mode: TransferMode,
    header: TableBlockHeader,
    batches: Iterator[list[dict[str, object]]],
    target: TableSchema,
    expected_rows: int,
    progress: _TableProgress,
) -> int:
    quote = connection.dialect.identifier_preparer.quote
    if target.schema_name is None:
        raise DatabaseTransferError("mssql target schema is missing")
    table = f"{quote(target.schema_name)}.{quote(header.name)}"
    names = [column.name for column in header.columns]
    columns = {column.name: column for column in target.column_metadata}
    parameters = {name: f"value_{index}" for index, name in enumerate(names)}

    def binding(name: str) -> str:
        parameter = f":{parameters[name]}"
        return (
            f"CAST({parameter} AS VARBINARY(MAX))"
            if columns[name].type_name in {"binary", "varbinary"}
            else parameter
        )

    insert = text(
        f"INSERT INTO {table} ({', '.join(quote(name) for name in names)}) "
        f"VALUES ({', '.join(binding(name) for name in names)})"
    )
    updated = [
        name for name in names if name not in target.primary_key and not columns[name].identity
    ]
    exists = None
    update = None
    if mode == "upsert":
        predicate = " AND ".join(f"{quote(name)} = {binding(name)}" for name in target.primary_key)
        exists = text(f"SELECT 1 FROM {table} WITH (UPDLOCK, HOLDLOCK) WHERE {predicate}")
        if updated:
            update = text(
                f"UPDATE {table} SET "
                + ", ".join(f"{quote(name)} = {binding(name)}" for name in updated)
                + f" WHERE {predicate}"
            )
    identity = any(columns[name].identity for name in names)
    identity_enabled = False
    written = 0
    try:
        with connection.begin():
            if identity:
                identity_enabled = True
                connection.exec_driver_sql(f"SET IDENTITY_INSERT {table} ON").close()
            for batch in batches:
                if exists is not None:
                    for bound in batch:
                        with connection.execute(exists, bound) as result:
                            found = result.first() is not None
                        if not found:
                            connection.execute(insert, bound).close()
                        elif update is not None:
                            connection.execute(update, bound).close()
                else:
                    connection.execute(insert, batch).close()
                progress.advance(len(batch))
                written += len(batch)
                del batch
            if written != expected_rows:
                raise DatabaseTransferError("JSONL row count changed between preflight and import")
            if identity:
                connection.exec_driver_sql(f"SET IDENTITY_INSERT {table} OFF").close()
                identity_enabled = False
    except BaseException:
        if identity_enabled:
            try:
                connection.exec_driver_sql(f"SET IDENTITY_INSERT {table} OFF").close()
                connection.rollback()
            except Exception:
                with suppress(Exception):
                    connection.invalidate()
        progress.finish("rolled_back")
        raise
    progress.finish("committed")
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
