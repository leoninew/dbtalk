"""Typed Click context shared by command modules."""

from __future__ import annotations

from dataclasses import dataclass

import click

from dbtalk.settings import Settings


@dataclass(frozen=True)
class DbtalkContext:
    settings: Settings


def dbtalk_context(ctx: click.Context) -> DbtalkContext:
    """Return the root context object or fail with a clear programming error."""
    value = ctx.find_root().obj
    if not isinstance(value, DbtalkContext):
        raise RuntimeError("CLI context was not initialized")
    return value
