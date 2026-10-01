"""Regression checks for full target cleanup before native restores."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from dbtalk.cli import cli
from dbtalk.database.dsn import parse_dsn
from dbtalk.mysql.restore import MysqlRestoreOptions
from dbtalk.mysql.restore import restore_database as restore_mysql
from dbtalk.postgres.client import PostgresConnection
from dbtalk.postgres.restore import PostgresRestoreOptions
from dbtalk.postgres.restore import restore_database as restore_postgres


@pytest.mark.parametrize("client", ["local", "mapped", "docker"])
@pytest.mark.parametrize("if_exists", [False, True])
def test_postgres_clean_runs_after_validation_and_before_restore(
    tmp_path: Path, client: str, if_exists: bool
) -> None:
    input_path = tmp_path / "backup.dump"
    input_path.write_bytes(b"PGDMP")
    options = PostgresRestoreOptions(
        connection=PostgresConnection.from_parsed_dsn(
            parse_dsn("postgresql+psycopg://backup:secret@localhost/source"), database="target"
        ),
        input=input_path,
        client_image="postgres:18",
        clean=True,
        if_exists=if_exists,
        jobs=2,
    )
    commands: list[list[str]] = []

    def capture(
        command: list[str], environment: dict[str, str] | None = None
    ) -> CompletedProcess[str]:
        commands.append(command)
        assert "secret" not in " ".join(command)
        if "psql" in command:
            assert environment is not None
            assert "--single-transaction" in command
            assert "ON_ERROR_STOP=1" in command
            if client == "local":
                assert Path(environment["PGPASSFILE"]).is_file()
            else:
                assert environment["PGPASSWORD"] == "secret"
        return CompletedProcess(command, 0, "", "")

    with (
        patch(
            "dbtalk.postgres.restore.docker_mapped_postgres_container",
            return_value="postgres-test" if client == "mapped" else None,
        ),
        patch(
            "dbtalk.postgres.restore.shutil.which",
            return_value=None if client == "docker" else "native-client",
        ),
        patch("dbtalk.postgres.restore.docker_postgres_image", return_value=("postgres:18", "")),
        patch("dbtalk.postgres.restore.run_command", side_effect=capture),
    ):
        assert restore_postgres(options) == input_path.resolve()

    validation = next(i for i, command in enumerate(commands) if "--list" in command)
    cleanup = next(i for i, command in enumerate(commands) if "psql" in command)
    restore = next(
        i for i, command in enumerate(commands) if "pg_restore" in command and "--dbname" in command
    )
    assert validation < cleanup < restore
    sql = commands[cleanup][-1]
    assert "pg_catalog.pg_namespace" in sql
    assert "left(nspname, 3) <> 'pg_'" in sql
    assert "nspname <> 'information_schema'" in sql
    assert " CASCADE" in sql
    assert "CREATE SCHEMA public AUTHORIZATION pg_database_owner" in sql
    assert ("DROP SCHEMA IF EXISTS" in sql) == if_exists
    assert "--clean" not in commands[restore]
    assert "--if-exists" not in commands[restore]
    assert "--jobs" in commands[restore]
    uri = (
        "postgresql://backup@/target"
        if client == "mapped"
        else "postgresql://backup@localhost:5432/target"
    )
    if client == "docker":
        uri = "postgresql://backup@host.docker.internal:5432/target"
    assert uri in commands[cleanup]
    assert uri in commands[restore]


def test_postgres_cleanup_failure_stops_restore(tmp_path: Path) -> None:
    input_path = tmp_path / "backup.dump"
    input_path.write_bytes(b"PGDMP")
    options = PostgresRestoreOptions(
        connection=PostgresConnection.from_parsed_dsn(
            parse_dsn("postgresql+psycopg://backup:secret@db.example/target")
        ),
        input=input_path,
        client_image="postgres:18",
        clean=True,
    )
    with (
        patch("dbtalk.postgres.restore.shutil.which", return_value="native-client"),
        patch(
            "dbtalk.postgres.restore.run_command",
            side_effect=[
                CompletedProcess([], 0, "", ""),
                CompletedProcess([], 1, "", "permission denied for schema extra"),
            ],
        ) as run,
        pytest.raises(click.ClickException, match="target cleanup failed.*permission denied"),
    ):
        restore_postgres(options)
    assert run.call_count == 2


@pytest.mark.parametrize("client", ["local", "mapped", "docker"])
@pytest.mark.parametrize("compressed", [False, True])
def test_mysql_clean_removes_extra_objects_with_foreign_key_checks_disabled(
    tmp_path: Path, client: str, compressed: bool
) -> None:
    original = b"USE `source`;\nSELECT 1;\n"
    input_path = tmp_path / ("backup.sql.gz" if compressed else "backup.sql")
    input_path.write_bytes(gzip.compress(original) if compressed else original)
    original_file = input_path.read_bytes()
    options = MysqlRestoreOptions(
        host="localhost",
        port=3306,
        user="backup",
        password="secret",
        input=input_path,
        database="target`db",
        client_image="mysql:8.0.39",
        clean=True,
    )
    objects = [
        ("EVENT", "old_event"),
        ("FUNCTION", "old_function"),
        ("PROCEDURE", "old_procedure"),
        ("VIEW", "old_view"),
        ("TABLE", "extra_child"),
        ("TABLE", "parent`table"),
    ]
    commands: list[list[str]] = []
    inputs: list[tuple[Path, str]] = []

    def capture(
        command: list[str],
        environment: dict[str, str],
        *,
        input_path: Path | None = None,
        **_: object,
    ) -> CompletedProcess[str]:
        commands.append(command)
        assert environment["MYSQL_PWD"] == "secret"
        assert "secret" not in " ".join(command)
        if "--execute" in command and "JSON_OBJECT" in command[-1]:
            assert "DATABASE()" in command[-1]
            assert "--raw" in command
            output = "\n".join(json.dumps({"kind": kind, "name": name}) for kind, name in objects)
            return CompletedProcess(command, 0, output, "")
        if input_path is not None:
            inputs.append((input_path, input_path.read_text(encoding="utf-8")))
        return CompletedProcess(command, 0, "", "")

    with (
        patch(
            "dbtalk.mysql.restore.docker_mapped_mysql_container",
            return_value="mysql-test" if client == "mapped" else None,
        ),
        patch(
            "dbtalk.mysql.restore.shutil.which",
            return_value=None if client == "docker" else "native-client",
        ),
        patch("dbtalk.mysql.restore.docker_mysql_image", return_value=("mysql:8.0.39", "")),
        patch("dbtalk.mysql.restore.run_command", side_effect=capture),
        patch("dbtalk.mysql.restore.remove_temporary_container"),
    ):
        assert restore_mysql(options) == input_path.resolve()

    assert len(commands) == 4
    assert commands[0][-1] == "SELECT 1"
    assert len(inputs) == 2
    cleanup_path, cleanup_sql = inputs[0]
    assert cleanup_sql.startswith("SET FOREIGN_KEY_CHECKS=0;\n")
    assert cleanup_sql.endswith("SET FOREIGN_KEY_CHECKS=1;\n")
    for kind, name in objects:
        assert f"DROP {kind} `target``db`.`{name.replace('`', '``')}`;" in cleanup_sql
    assert inputs[1][1] == "USE `target``db`;\nSELECT 1;\n"
    assert not cleanup_path.exists()
    assert input_path.read_bytes() == original_file
    if client == "mapped":
        assert commands[1][:6] == ["docker", "exec", "-i", "--env", "MYSQL_PWD", "mysql-test"]
        assert "-h" not in commands[2]
    elif client == "docker":
        assert "host.docker.internal" in commands[1]
        assert "-i" in commands[2]


def test_mysql_cleanup_failure_stops_restore(tmp_path: Path) -> None:
    input_path = tmp_path / "backup.sql"
    input_path.write_text("SELECT 1;\n", encoding="utf-8")
    options = MysqlRestoreOptions(
        host="db.example",
        port=3306,
        user="backup",
        password="secret",
        input=input_path,
        database="target",
        clean=True,
    )
    with (
        patch("dbtalk.mysql.restore.docker_mapped_mysql_container", return_value=None),
        patch("dbtalk.mysql.restore.shutil.which", return_value="native-client"),
        patch(
            "dbtalk.mysql.restore.run_command",
            side_effect=[
                CompletedProcess([], 0, "", ""),
                CompletedProcess([], 0, '{"kind":"TABLE","name":"extra_child"}\n', ""),
                CompletedProcess([], 1, "", "DROP command denied"),
            ],
        ) as run,
        pytest.raises(click.ClickException, match="target cleanup failed.*DROP command denied"),
    ):
        restore_mysql(options)
    assert run.call_count == 3
    assert not run.call_args.kwargs["input_path"].exists()


@pytest.mark.parametrize("database", ["mysql", "information_schema", "performance_schema", "sys"])
def test_mysql_clean_refuses_system_databases(tmp_path: Path, database: str) -> None:
    input_path = tmp_path / "backup.sql"
    input_path.write_text("SELECT 1;\n", encoding="utf-8")
    options = MysqlRestoreOptions(
        host="localhost",
        port=3306,
        user="root",
        password="secret",
        input=input_path,
        database=database,
        clean=True,
    )
    with (
        patch("dbtalk.mysql.restore.run_command") as run,
        pytest.raises(click.ClickException, match="system database"),
    ):
        restore_mysql(options)
    run.assert_not_called()


@pytest.mark.parametrize("clean", [False, True])
def test_mysql_cli_only_cleans_when_requested(tmp_path: Path, clean: bool) -> None:
    input_path = tmp_path / "backup.sql"
    input_path.write_text("SELECT 1;\n", encoding="utf-8")
    args = [
        "mysql",
        "restore",
        "--dsn",
        "mysql+pymysql://backup:secret@db.example/source",
        "--database",
        "target",
        "--input",
        str(input_path),
    ]
    if clean:
        args.append("--clean")
    with patch("dbtalk.mysql.cli.restore_database", return_value=input_path) as restore:
        result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert restore.call_args.args[0].clean == clean
    assert restore.call_args.args[0].database == "target"
