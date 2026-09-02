"""MySQL value compatibility helpers used by the SQLAlchemy transfer adapter."""

from __future__ import annotations

import re
from datetime import timedelta

from .format import encode_value
from .models import DatabaseTransferError, ExportOptions, JSONValue

_MYSQL_ZERO_DATE = re.compile(r"0000-00-00(?: 00:00:00(?:\.\d{1,6})?)?")
_MYSQL_ZERO_DATE_TYPES = frozenset({"DATE", "DATETIME", "TIMESTAMP"})


def _encode_mysql_value(value: object, declared_type: str, options: ExportOptions) -> JSONValue:
    value = _normalize_mysql_zero_date(value, declared_type, options)
    if isinstance(value, timedelta):
        value = _mysql_time_of_day(value)
    return encode_value(value, declared_type, options.timezone)


def _normalize_mysql_zero_date(value: object, declared_type: str, options: ExportOptions) -> object:
    """Normalize MySQL zero dates only when the export policy allows it."""

    base_type = declared_type.upper().strip().split("(", maxsplit=1)[0].strip()
    if (
        not isinstance(value, str)
        or base_type not in _MYSQL_ZERO_DATE_TYPES
        or _MYSQL_ZERO_DATE.fullmatch(value) is None
    ):
        return value
    if options.zero_datetime_as_null:
        return None
    raise DatabaseTransferError(
        "MySQL zero date cannot be exported while mysql.zero_datetime_as_null is disabled"
    )


def _mysql_time_of_day(value: timedelta) -> str:
    """Convert a MySQL TIME duration into an ISO time-of-day string."""

    if value < timedelta() or value >= timedelta(days=1):
        raise DatabaseTransferError("MySQL TIME value is outside the ISO 8601 time-of-day range")
    total_seconds = value.seconds
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    formatted = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    if value.microseconds:
        return f"{formatted}.{value.microseconds:06d}".rstrip("0")
    return formatted


__all__ = ["_encode_mysql_value", "_mysql_time_of_day", "_normalize_mysql_zero_date"]
