"""Central logging setup for the CLI process."""

from __future__ import annotations

from logging.config import dictConfig


def configure_logging(level: str, log_format: str, verbose: bool) -> None:
    """Enable stderr logging only for an explicitly verbose CLI invocation."""
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
                if verbose
                else {"class": "logging.NullHandler"}
            },
            "root": {"level": level, "handlers": ["console"]},
        }
    )
