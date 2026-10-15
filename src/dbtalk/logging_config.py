"""Central logging setup for the CLI process."""

from __future__ import annotations

import logging
from logging.config import dictConfig


def configure_logging(level: str, log_format: str, verbose: bool) -> None:
    """Configure process logging once, keeping command output on stdout."""
    resolved_level: int | str = logging.DEBUG if verbose else level
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"console": {"format": log_format}},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "console",
                    "stream": "ext://sys.stderr",
                }
            },
            "root": {"level": resolved_level, "handlers": ["console"]},
        }
    )
