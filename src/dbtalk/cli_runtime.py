"""Click command classes that inject leaf-command verbose handling."""

from __future__ import annotations

from typing import Any

import click

from dbtalk.context import DbtalkContext
from dbtalk.logging_config import configure_logging

VERBOSE_OPTION_HELP = "Show sanitized exception details and enable debug log messages."


class DbtalkCommand(click.Command):
    """Leaf command with a shared -v option and verbose error rendering."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
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
        configure_logging(settings.logging.level, settings.logging.format, True)
        root.obj = DbtalkContext(settings=settings, verbose=True)
    return enabled
