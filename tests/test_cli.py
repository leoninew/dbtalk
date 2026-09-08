"""CLI behavior tests that do not require external services."""

import logging

import click
import pytest
from click.testing import CliRunner
from pytest import MonkeyPatch
from sqlalchemy.exc import SQLAlchemyError

from dbtalk import cli as cli_module
from dbtalk.cli import cli, main
from dbtalk.context import dbtalk_context
from dbtalk.database.models import (
    DatabaseOperationError,
    driver_error_detail,
    format_cli_error,
    sanitize_error_detail,
)


def test_help_lists_root_and_dialect_commands() -> None:
    result = CliRunner().invoke(cli, ["--help"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "mysql" in result.output
    assert "postgres" in result.output
    assert "query" in result.output
    assert "export" in result.output


def test_root_command_displays_help() -> None:
    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    assert "Usage: cli" in result.output


def test_command_group_help_is_available() -> None:
    runner = CliRunner()
    mysql_result = runner.invoke(cli, ["mysql", "--help"])
    postgres_result = runner.invoke(cli, ["postgres", "--help"])
    query_result = runner.invoke(cli, ["query", "--help"])

    assert mysql_result.exit_code == 0, mysql_result.output
    assert "dump" in mysql_result.output
    assert "restore" in mysql_result.output
    assert postgres_result.exit_code == 0, postgres_result.output
    assert "dump" in postgres_result.output
    assert "restore" in postgres_result.output
    assert query_result.exit_code == 0, query_result.output
    assert "--sql" in query_result.output


def test_removed_database_command_is_rejected() -> None:
    result = CliRunner().invoke(cli, ["database", "--help"])

    assert result.exit_code != 0
    assert "No such command 'database'" in result.output


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
def test_removed_dialect_database_command_is_rejected(dialect: str) -> None:
    result = CliRunner().invoke(cli, [dialect, "database", "--help"])

    assert result.exit_code != 0
    assert "No such command 'database'" in result.output


def test_context_requires_root_initialization() -> None:
    context = click.Context(click.Command("dbtalk"))

    with pytest.raises(RuntimeError, match="CLI context was not initialized"):
        dbtalk_context(context)


def test_main_invokes_root_command(monkeypatch: MonkeyPatch) -> None:
    called = False

    def fake_cli() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli_module, "cli", fake_cli)
    main()

    assert called


def test_verbose_option_is_on_leaf_commands_not_groups() -> None:
    runner = CliRunner()
    root = runner.invoke(cli, ["--help"])
    mysql = runner.invoke(cli, ["mysql", "--help"])
    exec_help = runner.invoke(cli, ["exec", "--help"])
    dump_help = runner.invoke(cli, ["mysql", "dump", "--help"])
    grant_help = runner.invoke(cli, ["mysql", "grant", "--help"])

    assert root.exit_code == 0, root.output
    assert "-v, --verbose" not in root.output
    assert mysql.exit_code == 0, mysql.output
    assert "-v, --verbose" not in mysql.output
    assert exec_help.exit_code == 0, exec_help.output
    assert "-v, --verbose" in exec_help.output
    assert "--write" not in exec_help.output
    assert dump_help.exit_code == 0, dump_help.output
    assert "-v, --verbose" in dump_help.output
    assert grant_help.exit_code == 0, grant_help.output
    assert "-v, --verbose" in grant_help.output


def test_leaf_verbose_enables_debug_logging() -> None:
    runner = CliRunner()
    quiet = runner.invoke(cli, ["exec", "--dsn", "sqlite:///:memory:", "--sql", "SELECT 1"])
    assert quiet.exit_code == 0, quiet.output
    assert logging.getLogger().level == logging.INFO

    verbose = runner.invoke(
        cli,
        ["exec", "-v", "--dsn", "sqlite:///:memory:", "--sql", "SELECT 1"],
    )
    assert verbose.exit_code == 0, verbose.output
    assert logging.getLogger().level == logging.DEBUG


def test_sanitize_error_detail_redacts_password_assignments_and_dsn_userinfo() -> None:
    message = (
        "PGPASSWORD=secret password=secret mysql_pwd=secret "
        "mysql+pymysql://admin:secret@db.example/app "
        "postgresql+psycopg://admin:secret@db.example/app"
    )
    sanitized = sanitize_error_detail(message)
    assert "secret" not in sanitized
    assert sanitized.count("<redacted>") >= 5


class _DriverError(Exception):
    pass


class _StatementError(Exception):
    def __init__(self, message: str, orig: BaseException | None = None) -> None:
        super().__init__(message)
        self.orig = orig


def test_driver_error_detail_includes_exception_type_and_strips_sqlalchemy_noise() -> None:
    wrapped = SQLAlchemyError(
        "(sqlite3.OperationalError) missing relation\n"
        "[SQL: UPDATE application SET name=?]\n"
        "[parameters: ('Nginx',)]\n"
        "(Background on this error at: https://sqlalche.me/e/20/e3q8)"
    )
    detail = driver_error_detail(wrapped)
    assert detail.startswith("sqlalchemy.exc.SQLAlchemyError:")
    assert "[SQL:" not in detail
    assert "[parameters:]" not in detail
    assert "Background on this error" not in detail


def test_format_cli_error_appends_exception_details_only_when_verbose() -> None:
    cause = SQLAlchemyError(
        "(sqlite3.OperationalError) missing relation\n"
        "[SQL: UPDATE application SET name=?]\n"
        "[parameters: ('Nginx',)]"
    )
    error = DatabaseOperationError("database execution failed")
    error.__cause__ = cause
    assert format_cli_error(error, verbose=False) == "database execution failed"
    verbose = format_cli_error(error, verbose=True)
    assert verbose.startswith("database execution failed: sqlalchemy.exc.SQLAlchemyError:")
    assert "[SQL:" not in verbose
    assert "[parameters:]" not in verbose


def test_format_cli_error_does_not_render_orig_as_a_specific_error() -> None:
    cause = _StatementError(
        "(sqlite3.OperationalError) missing relation\n"
        "[SQL: UPDATE t SET c=?]\n"
        "[parameters: ('x',)]",
        orig=_DriverError("no such table: application"),
    )
    error = DatabaseOperationError("database execution failed")
    error.__cause__ = cause
    verbose = format_cli_error(error, verbose=True)
    assert verbose.startswith("database execution failed:")
    assert "StatementError" in verbose
    assert "[parameters:]" not in verbose
    assert verbose != "database execution failed: no such table: application"


def test_format_cli_error_redacts_secrets_and_skips_duplicate_detail() -> None:
    secret_cause = SQLAlchemyError("mysql+pymysql://admin:secret@db.example/app")
    wrapped = DatabaseOperationError("database execution failed")
    wrapped.__cause__ = secret_cause
    verbose = format_cli_error(wrapped, verbose=True)
    assert "database execution failed" in verbose
    assert "secret" not in verbose
    assert "<redacted>" in verbose

    dump_error = click.ClickException("mysqldump failed: Access denied")
    dump_error.__cause__ = _DriverError("Access denied")
    assert format_cli_error(dump_error, verbose=True) == "mysqldump failed: Access denied"
