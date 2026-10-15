"""Shared leaf-command logging, verbosity, and error rendering."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic
from typing import Any

import click

from dbtalk.context import DbtalkContext

VERBOSE_OPTION_HELP = "Show sanitized exception details and enable debug log messages."
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
    """Leaf command with lifecycle logs, a shared -v option, and safe errors."""

    def __init__(self, *args: Any, log_lifecycle: bool = True, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.log_lifecycle = log_lifecycle
        if any(param.name == "verbose" for param in self.params):
            return
        self.params.append(
            click.Option(
                param_decls=["-v", "--verbose"],
                is_flag=True,
                help=VERBOSE_OPTION_HELP,
            )
        )

    def invoke(self, ctx: click.Context) -> Any:
        from dbtalk.database.models import format_cli_error

        verbose = apply_command_verbose(ctx, bool(ctx.params.pop("verbose", False)))
        try:
            with command_logging(ctx, enabled=self.log_lifecycle):
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
    """Command group that gives leaf commands verbose handling without a group -v."""

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


def apply_command_verbose(ctx: click.Context, verbose: bool) -> bool:
    """Enable verbose logging and context for this invocation.

    Returns the effective verbose flag after combining the command option with
    process settings.
    """

    root = ctx.find_root()
    current = root.obj
    if not isinstance(current, DbtalkContext):
        return verbose
    enabled = verbose or current.verbose
    if enabled and not current.verbose:
        settings = current.settings
        logging.getLogger().setLevel(logging.DEBUG)
        root.obj = DbtalkContext(settings=settings, verbose=True)
    return enabled
