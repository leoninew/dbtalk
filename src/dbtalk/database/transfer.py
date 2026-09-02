"""Public API and driver dispatch for SQLite/MySQL JSONL data transfer."""

from __future__ import annotations

import logging

from .dsn import dsn_from_environment, parse_dsn
from .format import (
    TRANSFER_FORMAT,
    decode_value,
    encode_value,
    format_datetime_for_database,
    iter_document_tables,
    normalize_datetime,
    read_jsonl,
    scan_document,
    type_family,
    write_document,
    write_jsonl,
)
from .models import (
    ColumnDefinition,
    DatabaseDriver,
    DatabaseOperationError,
    DatabaseTransferError,
    ExportOptions,
    ImportOptions,
    JSONScalar,
    JSONValue,
    TableBlock,
    TableBlockHeader,
    TablePreview,
    TableSchema,
    TransferConnection,
    TransferDocument,
    TransferHeader,
    TransferMode,
    TransferPreview,
    TransferSummary,
    TypedJSONValue,
)
from .schema import (
    compatible_types,
    order_table_blocks,
    table_block_values,
    validate_import_rows,
    validate_target_table,
)
from .sqlalchemy_transfer import export_sqlalchemy, import_sqlalchemy

logger = logging.getLogger("dbtalk")


def export_database(options: ExportOptions) -> TransferSummary:
    """Export a database through the adapter selected by the source driver."""

    logger.info(
        "database export started driver=%s output=%s excluded=%d",
        options.connection.driver,
        options.output.resolve(),
        len(options.exclude_tables),
    )
    try:
        validate_connection(options.connection)
        summary = export_sqlalchemy(options)
    except DatabaseTransferError as error:
        logger.error(
            "database export failed driver=%s output=%s error=%s",
            options.connection.driver,
            options.output.resolve(),
            error,
            exc_info=False,
        )
        raise
    logger.info(
        "database export completed driver=%s output=%s tables=%d rows=%d",
        options.connection.driver,
        options.output.resolve(),
        summary.table_count,
        summary.row_count,
    )
    return summary


def import_database(options: ImportOptions) -> TransferSummary:
    """Preflight and import one JSONL transfer document."""

    logger.info(
        "database import started driver=%s input=%s mode=%s excluded=%d",
        options.connection.driver,
        options.input.resolve(),
        options.mode,
        len(options.exclude_tables),
    )
    try:
        validate_connection(options.connection)
        summary = import_sqlalchemy(options)
    except DatabaseTransferError as error:
        logger.error(
            "database import failed driver=%s input=%s mode=%s error=%s",
            options.connection.driver,
            options.input.resolve(),
            options.mode,
            error,
            exc_info=False,
        )
        raise
    logger.info(
        "database import completed driver=%s input=%s mode=%s tables=%d rows=%d",
        options.connection.driver,
        options.input.resolve(),
        options.mode,
        summary.table_count,
        summary.row_count,
    )
    return summary


def validate_connection(connection: TransferConnection) -> None:
    """Validate canonical DSN input before the SQLAlchemy transfer adapter runs."""

    if (connection.dsn is None) == (connection.dsn_env is None):
        raise DatabaseTransferError("provide exactly one of dsn or dsn_env")
    try:
        parsed = (
            parse_dsn(connection.dsn)
            if connection.dsn
            else dsn_from_environment(connection.dsn_env)
        )
    except DatabaseOperationError as error:
        raise DatabaseTransferError(str(error)) from error
    if parsed.dialect != connection.driver:
        raise DatabaseTransferError(
            f"connection DSN dialect {parsed.dialect!r} does not match {connection.driver!r}"
        )
    if not parsed.database:
        raise DatabaseTransferError("JSONL database transfer requires a database name in the DSN")


__all__ = [
    "TRANSFER_FORMAT",
    "ColumnDefinition",
    "DatabaseDriver",
    "DatabaseTransferError",
    "DatabaseOperationError",
    "ExportOptions",
    "ImportOptions",
    "JSONScalar",
    "JSONValue",
    "TableBlock",
    "TableBlockHeader",
    "TablePreview",
    "TableSchema",
    "TransferConnection",
    "TransferDocument",
    "TransferHeader",
    "TransferMode",
    "TransferPreview",
    "TransferSummary",
    "TypedJSONValue",
    "compatible_types",
    "decode_value",
    "encode_value",
    "export_database",
    "format_datetime_for_database",
    "import_database",
    "iter_document_tables",
    "normalize_datetime",
    "order_table_blocks",
    "read_jsonl",
    "scan_document",
    "table_block_values",
    "type_family",
    "validate_connection",
    "validate_import_rows",
    "validate_target_table",
    "write_document",
    "write_jsonl",
]
