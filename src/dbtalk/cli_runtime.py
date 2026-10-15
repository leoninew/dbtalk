"""Shared leaf-command logging, verbosity, and error rendering."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic
from typing import Any

import click

from dbtalk.context import DbtalkContext

logger = logging.getLogger("dbtalk")

# Never log arbitrary Click parameters: DSNs, SQL, and password sources are private.
LOGGED_OPTIONS = (
    "name",
    "database_name",
    "target_database",
    "schemas",
    "role_name",
    "user_name",
    "host",
    "from_role",
    "to_role",
    "profile",
    "all_hosts",
    "if_exists",
    "force",
    "clean",
    "dry_run",
    "yes",
    "include_shared",
    "input",
    "output",
)


class DbtalkCommand(click.Command):
    """Leaf command with lifecycle logs and error details controlled by the root."""

    def __init__(self, *args: Any, log_lifecycle: bool = True, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.log_lifecycle = log_lifecycle

    def invoke(self, ctx: click.Context) -> Any:
        from dbtalk.database.models import format_cli_error

        current = ctx.find_root().obj
        verbose = isinstance(current, DbtalkContext) and current.verbose
        try:
            with command_logging(ctx, enabled=verbose and self.log_lifecycle):
                return super().invoke(ctx)
        except (click.Abort, click.exceptions.Exit, click.UsageError):
            raise
        except click.ClickException as error:
            formatted = format_cli_error(error, verbose=verbose)
            if formatted == str(error):
                raise
            raise click.ClickException(formatted) from error
        except RuntimeError as error:
            raise click.ClickException(format_cli_error(error, verbose=verbose)) from error


class DbtalkGroup(click.Group):
    """Command group that gives leaf commands shared logging and error handling."""

    command_class = DbtalkCommand


DbtalkGroup.group_class = DbtalkGroup


@contextmanager
def command_logging(ctx: click.Context, *, enabled: bool) -> Iterator[None]:
    """Log one operation summary, leaving exception details to Click."""
    if not enabled:
        yield
        return

    fields = "".join(
        f" {name}={ctx.params[name]!r}"
        for name in LOGGED_OPTIONS
        if ctx.params.get(name) is not None
    )
    operation = ctx.command_path
    started = monotonic()
    logger.info("%s started%s", operation, fields)
    try:
        yield
    except (click.Abort, KeyboardInterrupt, EOFError):
        logger.warning(
            "%s cancelled%s elapsed_ms=%d", operation, fields, (monotonic() - started) * 1000
        )
        raise
    except click.exceptions.Exit as error:
        logger.log(
            logging.INFO if error.exit_code == 0 else logging.ERROR,
            "%s %s%s elapsed_ms=%d",
            operation,
            "completed" if error.exit_code == 0 else "failed",
            fields,
            (monotonic() - started) * 1000,
        )
        raise
    except Exception:
        logger.error("%s failed%s elapsed_ms=%d", operation, fields, (monotonic() - started) * 1000)
        raise
    else:
        logger.info(
            "%s completed%s elapsed_ms=%d", operation, fields, (monotonic() - started) * 1000
        )
