"""Central logging setup for the CLI process."""

from __future__ import annotations

import logging


def configure_logging(level: str, log_format: str) -> None:
    """Configure CLI diagnostics without replacing existing logging handlers."""
    logging.basicConfig(level=level, format=log_format)
