"""Unit tests for PostgreSQL native logical backup commands."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from dbtalk.cli import cli as main_command
from dbtalk.database.dsn import parse_dsn
from dbtalk.postgres.cli import postgres_connection_from_dsn
from dbtalk.postgres.client import (
    PostgresConnection,
    docker_bind_mount,
    docker_database_host,
    docker_host_gateway_args,
    docker_mapped_postgres_container,
    docker_postgres_image,
    ensure_command_succeeded,
    escape_pgpass_value,
    pgpass_environment,
)
from dbtalk.postgres.dump import (
    PostgresDumpOptions,
    default_dump_output,
    dump_database,
    pg_dump_command_args,
    resolve_dump_options,
)
from dbtalk.postgres.restore import (
    PostgresRestoreOptions,
    pg_restore_command_args,
    restore_database,
)
from dbtalk.settings import DumpRestoreConfig


def connection() -> PostgresConnection:
    return PostgresConnection.from_parsed_dsn(
        parse_dsn("postgresql+psycopg://backup:secret@db.example.test:5433/app?sslmode=require")
    )


def dump_options(output: Path) -> PostgresDumpOptions:
    return PostgresDumpOptions(
        connection=connection(),
        output=output,
        client_image="postgres:18",
    )


def restore_options(input_path: Path) -> PostgresRestoreOptions:
    return PostgresRestoreOptions(
        connection=connection(),
        input=input_path,
        client_image="postgres:18",
    )


def test_connection_builds_a_password_free_libpq_uri() -> None:
    native_connection = connection()

    assert (
        native_connection.libpq_uri()
        == "postgresql://backup@db.example.test:5433/app?sslmode=require"
    )
    assert "secret" not in native_connection.libpq_uri()
    assert "psycopg" not in native_connection.libpq_uri()
    assert (
        native_connection.libpq_uri(host="", socket=True)
        == "postgresql://backup@/app?sslmode=require"
    )


def test_connection_uses_an_explicit_target_for_a_database_free_dsn() -> None:
    native_connection = PostgresConnection.from_parsed_dsn(
        parse_dsn("postgresql+psycopg://backup:secret@db.example.test:5433/?sslmode=require"),
        database="target_database",
    )

    assert native_connection.database == "target_database"
    assert (
        native_connection.libpq_uri()
        == "postgresql://backup@db.example.test:5433/target_database?sslmode=require"
    )
    with pytest.raises(ValueError, match="--database or a DSN database"):
        PostgresConnection.from_parsed_dsn(
            parse_dsn("postgresql+psycopg://backup:secret@db.example.test:5433/")
        )


def test_postgres_connection_rejects_another_database_dialect() -> None:
    parsed = parse_dsn("mysql+pymysql://backup:secret@db.example.test/app")

    with pytest.raises(ValueError, match="PostgreSQL dump"):
        PostgresConnection.from_parsed_dsn(parsed)


def test_pgpass_environment_escapes_values_and_cleans_up() -> None:
    escaped_connection = replace(
        connection(),
        user="backup:user",
        password=r"se\\cret:word",
    )
    temporary_path: Path | None = None

    with pgpass_environment(escaped_connection) as environment:
        temporary_path = Path(environment["PGPASSFILE"])
        assert temporary_path.is_file()
        assert temporary_path.read_text(encoding="utf-8") == (
            "db.example.test:5433:app:backup\\:user:se\\\\\\\\cret\\:word\n"
        )

    assert temporary_path is not None
    assert not temporary_path.exists()


def test_pgpass_rejects_record_injection() -> None:
    with pytest.raises(click.ClickException, match="line breaks"):
        escape_pgpass_value("bad\nvalue")


def test_pg_dump_command_uses_custom_format_and_optional_compression() -> None:
    options = replace(dump_options(Path("backup.dump")), compression_level=6)

    command = pg_dump_command_args(options, Path("backup.dump"))

    assert command == [
        "pg_dump",
        "--format=custom",
        "--file",
        "backup.dump",
        "--dbname",
        "postgresql://backup@db.example.test:5433/app?sslmode=require",
        "--compress=6",
    ]
    assert "secret" not in command


def test_default_dump_output_and_resolution_follow_directory_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    configured = default_dump_output(
        "app",
        datetime(2026, 8, 23, 12, 34, 56),
        output_directory="data",
    )
    target_directory = tmp_path / "exports"
    target_directory.mkdir()
    resolved = resolve_dump_options(
        DumpRestoreConfig(output_directory="data", client_image="postgres:18"),
        connection(),
        target_directory,
        None,
    )

    assert configured == tmp_path / "data" / "app-20260823-123456.dump"
    assert configured.parent.is_dir()
    assert resolved.output.parent == target_directory
    assert resolved.output.suffix == ".dump"


def test_dump_uses_a_local_client_and_removes_credential_file(tmp_path: Path) -> None:
    output = tmp_path / "backup.dump"
    options = dump_options(output)
    credential_path: Path | None = None

    def write_archive(command: list[str], environment: dict[str, str]) -> CompletedProcess[str]:
        nonlocal credential_path
        credential_path = Path(environment["PGPASSFILE"])
        assert credential_path.is_file()
        assert "secret" not in command
        Path(command[command.index("--file") + 1]).write_bytes(b"PGDMP")
        return CompletedProcess(command, 0, "", "")

    with (
        patch("dbtalk.postgres.dump.shutil.which", return_value="/usr/bin/pg_dump"),
        patch("dbtalk.postgres.dump.run_command", side_effect=write_archive),
    ):
        completed = dump_database(options)

    assert completed == output.resolve()
    assert output.read_bytes() == b"PGDMP"
    assert credential_path is not None
    assert not credential_path.exists()


def test_dump_uses_the_configured_docker_image_when_local_client_is_missing(
    tmp_path: Path,
) -> None:
    output = tmp_path / "backup.dump"
    options = dump_options(output)
    captured: dict[str, object] = {}

    def write_archive(command: list[str], environment: dict[str, str]) -> CompletedProcess[str]:
        captured["command"] = command
        captured["environment"] = environment
        docker_output = Path(command[command.index("--file") + 1])
        (output.parent / docker_output.name).write_bytes(b"PGDMP")
        return CompletedProcess(command, 0, "", "")

    with (
        patch("dbtalk.postgres.dump.shutil.which", return_value=None),
        patch("dbtalk.postgres.dump.docker_postgres_image", return_value=("postgres:18", "")),
        patch("dbtalk.postgres.dump.run_command", side_effect=write_archive),
    ):
        completed = dump_database(options)

    command = captured["command"]
    environment = captured["environment"]
    assert isinstance(command, list)
    assert isinstance(environment, dict)
    assert completed == output.resolve()
    assert "postgres:18" in command
    assert "--env" in command
    assert "PGPASSWORD" in command
    assert "secret" not in command
    assert environment["PGPASSWORD"] == "secret"
    assert any("dst=/backup" in value for value in command)
    assert "/backup/" in command[command.index("--file") + 1]


def test_dump_reuses_a_mapped_postgres_container_before_other_clients(tmp_path: Path) -> None:
    output = tmp_path / "backup.dump"
    options = replace(
        dump_options(output),
        connection=replace(connection(), host="localhost", port=5432, database="app"),
    )
    calls: list[list[str]] = []

    def capture(command: list[str], *_: object, **__: object) -> CompletedProcess[str]:
        calls.append(command)
        if len(command) > 1 and command[1] == "cp":
            Path(command[-1]).write_bytes(b"PGDMP")
        return CompletedProcess(command, 0, "", "")

    with (
        patch(
            "dbtalk.postgres.dump.docker_mapped_postgres_container",
            return_value="postgres-server",
        ) as mapped,
        patch("dbtalk.postgres.dump.shutil.which", return_value="/usr/bin/pg_dump") as which,
        patch("dbtalk.postgres.dump.docker_postgres_image") as image,
        patch("dbtalk.postgres.dump.run_command", side_effect=capture),
    ):
        assert dump_database(options) == output.resolve()

    mapped.assert_called_once_with("localhost", 5432)
    which.assert_not_called()
    image.assert_not_called()
    assert calls[0][:5] == ["docker", "exec", "--env", "PGPASSWORD", "postgres-server"]
    assert "postgresql://backup@/app?sslmode=require" in calls[0]
    assert "host.docker.internal" not in calls[0]
    assert calls[1][0:2] == ["docker", "cp"]
    assert calls[2][:4] == ["docker", "exec", "postgres-server", "rm"]


def test_dump_reports_missing_local_and_docker_clients(tmp_path: Path) -> None:
    with (
        patch("dbtalk.postgres.dump.shutil.which", return_value=None),
        patch(
            "dbtalk.postgres.dump.docker_postgres_image",
            return_value=(None, "Docker is not installed or is not on PATH."),
        ),
        pytest.raises(click.ClickException, match="pg_dump is not available"),
    ):
        dump_database(dump_options(tmp_path / "backup.dump"))


def test_dump_rejects_a_missing_explicit_output_parent(tmp_path: Path) -> None:
    options = dump_options(tmp_path / "missing" / "backup.dump")

    with pytest.raises(click.ClickException, match="output directory does not exist"):
        dump_database(options)


def test_docker_helpers_use_host_gateway_and_configured_image(
    tmp_path: Path,
) -> None:
    assert docker_database_host("localhost") == "host.docker.internal"
    assert docker_database_host("db.example.test") == "db.example.test"
    assert "src=" in docker_bind_mount(tmp_path, "/backup")
    assert docker_bind_mount(tmp_path, "/backup", read_only=True).endswith(",readonly")

    with (
        patch("dbtalk.postgres.client.shutil.which", return_value="docker"),
        patch(
            "dbtalk.postgres.client.subprocess.run",
            return_value=CompletedProcess([], 0, "", ""),
        ),
    ):
        assert docker_postgres_image("registry.example/postgres:18") == (
            "registry.example/postgres:18",
            "",
        )

    image = "registry.example/postgres:18"
    with (
        patch("dbtalk.postgres.client.shutil.which", return_value="docker"),
        patch(
            "dbtalk.postgres.client.subprocess.run",
            side_effect=[
                CompletedProcess([], 1, "", ""),
                CompletedProcess([], 0, "", ""),
            ],
        ) as run,
        patch("dbtalk.postgres.client.click.echo") as echo,
    ):
        assert docker_postgres_image(image) == (image, "")
    assert run.call_args_list == [
        (
            (["docker", "image", "inspect", image],),
            {"check": False, "capture_output": True, "text": True},
        ),
        ((["docker", "pull", image],), {"check": False}),
    ]
    echo.assert_called_once_with(f"Pulling configured PostgreSQL Docker image: {image}", err=True)

    with (
        patch("dbtalk.postgres.client.shutil.which", return_value="docker"),
        patch(
            "dbtalk.postgres.client.subprocess.run",
            side_effect=[
                CompletedProcess([], 1, "", ""),
                CompletedProcess([], 1, "", ""),
            ],
        ) as run,
        patch("dbtalk.postgres.client.click.echo") as echo,
    ):
        assert docker_postgres_image(image) == (
            None,
            f"Configured PostgreSQL Docker image could not be pulled: {image}.",
        )
    assert run.call_args.args[0] == ["docker", "pull", image]
    echo.assert_called_once_with(f"Pulling configured PostgreSQL Docker image: {image}", err=True)

    assert docker_host_gateway_args("db.example.test") == []


def test_pg_restore_command_defaults_to_portable_restore_options() -> None:
    options = PostgresRestoreOptions(
        connection=connection(),
        input=Path("backup.dump"),
        client_image="postgres:18",
        clean=True,
        if_exists=True,
        jobs=4,
    )

    command = pg_restore_command_args(options, Path("backup.dump"))

    assert command == [
        "pg_restore",
        "--dbname",
        "postgresql://backup@db.example.test:5433/app?sslmode=require",
        "--exit-on-error",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "--jobs",
        "4",
        "backup.dump",
    ]


def test_restore_validates_then_uses_a_local_client(tmp_path: Path) -> None:
    input_path = tmp_path / "backup.dump"
    input_path.write_bytes(b"PGDMP")
    calls: list[tuple[list[str], dict[str, str] | None]] = []
    credential_path: Path | None = None

    def capture(
        command: list[str], environment: dict[str, str] | None = None
    ) -> CompletedProcess[str]:
        nonlocal credential_path
        calls.append((command, environment))
        if environment is not None:
            credential_path = Path(environment["PGPASSFILE"])
            assert credential_path.is_file()
        return CompletedProcess(command, 0, "", "")

    with (
        patch("dbtalk.postgres.restore.shutil.which", return_value="/usr/bin/pg_restore"),
        patch("dbtalk.postgres.restore.run_command", side_effect=capture),
    ):
        restored = restore_database(restore_options(input_path))

    assert restored == input_path.resolve()
    assert calls[0][0] == ["pg_restore", "--list", str(input_path.resolve())]
    assert "--no-owner" in calls[1][0]
    assert "--no-privileges" in calls[1][0]
    assert credential_path is not None
    assert not credential_path.exists()


def test_restore_rejects_if_exists_without_clean(tmp_path: Path) -> None:
    input_path = tmp_path / "backup.dump"
    input_path.write_bytes(b"PGDMP")
    options = replace(restore_options(input_path), if_exists=True)

    with pytest.raises(click.ClickException, match="requires --clean"):
        restore_database(options)


def test_restore_uses_docker_for_validation_and_restore(tmp_path: Path) -> None:
    input_path = tmp_path / "backup.dump"
    input_path.write_bytes(b"PGDMP")
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def capture(
        command: list[str], environment: dict[str, str] | None = None
    ) -> CompletedProcess[str]:
        calls.append((command, environment))
        return CompletedProcess(command, 0, "", "")

    with (
        patch("dbtalk.postgres.restore.shutil.which", return_value=None),
        patch("dbtalk.postgres.restore.docker_postgres_image", return_value=("postgres:18", "")),
        patch("dbtalk.postgres.restore.run_command", side_effect=capture),
    ):
        restored = restore_database(restore_options(input_path))

    assert restored == input_path.resolve()
    assert "--list" in calls[0][0]
    assert calls[0][1] is None
    assert "--env" in calls[1][0]
    assert "PGPASSWORD" in calls[1][0]
    assert "/backup/" in calls[1][0][-1]
    assert calls[1][1] is not None
    assert calls[1][1]["PGPASSWORD"] == "secret"
    assert "secret" not in calls[1][0]


def test_restore_reuses_a_mapped_postgres_container_for_validation_and_restore(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "backup.dump"
    input_path.write_bytes(b"PGDMP")
    options = replace(
        restore_options(input_path),
        connection=replace(connection(), host="127.0.0.1", port=5432, database="app"),
    )
    calls: list[list[str]] = []

    def capture(command: list[str], *_: object, **__: object) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 0, "", "")

    with (
        patch(
            "dbtalk.postgres.restore.docker_mapped_postgres_container",
            return_value="postgres-server",
        ) as mapped,
        patch("dbtalk.postgres.restore.shutil.which", return_value="/usr/bin/pg_restore") as which,
        patch("dbtalk.postgres.restore.docker_postgres_image") as image,
        patch("dbtalk.postgres.restore.run_command", side_effect=capture),
    ):
        assert restore_database(options) == input_path.resolve()

    mapped.assert_called_once_with("127.0.0.1", 5432)
    which.assert_not_called()
    image.assert_not_called()
    assert calls[0][0:2] == ["docker", "cp"]
    assert calls[1][:6] == [
        "docker",
        "exec",
        "--env",
        "PGPASSWORD",
        "postgres-server",
        "pg_restore",
    ]
    assert "--list" in calls[1]
    assert "postgresql://backup@/app?sslmode=require" in calls[4]
    assert "host.docker.internal" not in calls[4]
    assert calls[5][0:4] == ["docker", "exec", "postgres-server", "rm"]


def test_docker_mapped_postgres_container_requires_one_local_running_container() -> None:
    with patch(
        "dbtalk.postgres.client.subprocess.run",
        return_value=CompletedProcess([], 0, "one\ntwo\n", ""),
    ) as run:
        assert docker_mapped_postgres_container("localhost", 5432) is None
    assert run.call_args.args[0][-1] == "publish=5432"


def test_restore_reports_an_invalid_custom_archive_before_writing(tmp_path: Path) -> None:
    input_path = tmp_path / "backup.dump"
    input_path.write_bytes(b"not-an-archive")

    with (
        patch("dbtalk.postgres.restore.shutil.which", return_value="/usr/bin/pg_restore"),
        patch(
            "dbtalk.postgres.restore.run_command",
            return_value=CompletedProcess([], 1, "", "invalid archive"),
        ),
        pytest.raises(click.ClickException, match="archive validation failed"),
    ):
        restore_database(restore_options(input_path))


def test_command_failure_includes_non_sensitive_client_diagnostic() -> None:
    with pytest.raises(click.ClickException, match="pg_dump failed: invalid archive"):
        ensure_command_succeeded(CompletedProcess([], 1, "", "invalid archive"), "pg_dump")


def test_cli_uses_postgres_dsn_and_rejects_invalid_restore_flags(tmp_path: Path) -> None:
    runner = CliRunner()
    input_path = tmp_path / "backup.dump"
    input_path.write_bytes(b"PGDMP")

    with patch(
        "dbtalk.postgres.cli.dump_database",
        side_effect=lambda options: options.output.resolve(),
    ) as dump:
        result = runner.invoke(
            main_command,
            [
                "postgres",
                "dump",
                "--dsn",
                "postgresql+psycopg://backup:secret@localhost/",
                "--database",
                "target_database",
                "--compression-level",
                "4",
                "--output",
                str(tmp_path / "backup.dump"),
            ],
        )

    assert result.exit_code == 0, result.output
    assert "PostgreSQL dump written to" in result.output
    assert dump.call_args.args[0].compression_level == 4
    assert dump.call_args.args[0].connection.database == "target_database"

    with patch(
        "dbtalk.postgres.cli.restore_database",
        side_effect=lambda options: options.input.resolve(),
    ) as restore:
        restore_result = runner.invoke(
            main_command,
            [
                "postgres",
                "restore",
                "--dsn",
                "postgresql+psycopg://backup:secret@localhost/",
                "--database",
                "restore_target",
                "--input",
                str(input_path),
            ],
        )

    assert restore_result.exit_code == 0, restore_result.output
    assert restore.call_args.args[0].connection.database == "restore_target"

    invalid_result = runner.invoke(
        main_command,
        [
            "postgres",
            "restore",
            "--dsn",
            "postgresql+psycopg://backup:secret@localhost/app",
            "--input",
            str(input_path),
            "--if-exists",
        ],
    )
    assert invalid_result.exit_code == 2
    assert "--if-exists requires --clean" in invalid_result.output

    for command in ("dump", "restore"):
        help_result = runner.invoke(main_command, ["postgres", command, "--help"])
        assert help_result.exit_code == 0, help_result.output
        assert "--database" in help_result.output


def test_cli_requires_a_canonical_postgres_dsn() -> None:
    with pytest.raises(click.UsageError, match="postgresql\\+psycopg"):
        postgres_connection_from_dsn("postgresql://backup:secret@localhost/app", None)


def test_cli_requires_a_target_for_a_database_free_postgres_dsn() -> None:
    with pytest.raises(click.UsageError, match="--database or a DSN database"):
        postgres_connection_from_dsn("postgresql+psycopg://backup:secret@localhost/", None)


def test_pgpass_environment_does_not_mutate_process_environment() -> None:
    previous = os.environ.get("PGPASSFILE")
    with pgpass_environment(connection()) as environment:
        assert environment["PGPASSFILE"] != previous
    assert os.environ.get("PGPASSFILE") == previous
