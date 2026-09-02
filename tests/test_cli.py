"""CLI behavior tests that do not require external services."""

import click
import pytest
from click.testing import CliRunner
from pytest import MonkeyPatch

from dbtalk import cli as cli_module
from dbtalk.cli import cli, main
from dbtalk.context import dbtalk_context


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
