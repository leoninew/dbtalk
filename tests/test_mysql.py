from __future__ import annotations

import gzip
import unittest
from datetime import datetime
from pathlib import Path
from subprocess import CompletedProcess
from typing import cast
from unittest.mock import ANY, patch

import click
from click.testing import CliRunner

from dbtalk.cli import cli as main_command
from dbtalk.mysql.cli import (
    MysqlDumpOptions,
    MysqlDumpOverrides,
    MysqlRestoreOptions,
    MysqlRestoreOverrides,
    default_dump_output,
    dump_database,
    generate_dump_command,
    generate_restore_command,
    mysql_restore_args,
    mysqldump_args,
    resolve_dump_options,
    resolve_restore_options,
    restore_database,
)
from dbtalk.mysql.client import docker_mapped_mysql_container
from dbtalk.settings import MySQLDumpConfig


class MysqlCommandTests(unittest.TestCase):
    def test_mysqldump_args_uses_defaults_without_host_or_port(self) -> None:
        options = MysqlDumpOptions(
            host="localhost",
            port=3306,
            user="root",
            password="secret",
            database="example",
            output=Path("backup.sql"),
        )

        self.assertEqual(
            mysqldump_args(options),
            [
                "env",
                "MYSQL_PWD=secret",
                "mysqldump",
                "-u",
                "root",
                "-B",
                "example",
                "--no-create-db",
                "-R",
                "-E",
                "--set-gtid-purged=OFF",
                "--skip-lock-tables",
                "-r",
                "backup.sql",
            ],
        )

    def test_generate_dump_command_quotes_sensitive_and_space_containing_values(
        self,
    ) -> None:
        options = MysqlDumpOptions(
            host="db.example.com",
            port=3307,
            user="backup user",
            password="p@ss word$",
            database="example",
            output=Path("backup file.sql"),
            skip_definer=True,
        )

        self.assertEqual(
            generate_dump_command(options),
            "env 'MYSQL_PWD=p@ss word$' mysqldump -C -h db.example.com -P 3307 "
            "-u 'backup user' -B example --no-create-db --skip-definer -R -E "
            "--set-gtid-purged=OFF --skip-lock-tables -r 'backup file.sql'",
        )

    def test_dump_database_runs_local_mysqldump_when_available(self) -> None:
        options = MysqlDumpOptions(
            host="db.example.com",
            port=3307,
            user="backup",
            password="secret",
            database="example",
            output=Path("backup.sql"),
            skip_definer=True,
        )

        def write_dump(
            command: list[str], environment: dict[str, str], **_: object
        ) -> CompletedProcess[str]:
            Path(command[-1]).write_text("SELECT 1;\n", encoding="utf-8")
            return CompletedProcess(command, 0, "", "")

        with (
            patch(
                "dbtalk.mysql.dump.shutil.which",
                return_value="/usr/bin/mysqldump",
            ),
            patch(
                "dbtalk.mysql.dump.run_command",
                side_effect=write_dump,
            ) as run,
        ):
            output = dump_database(options)

        self.assertEqual(output, Path("backup.sql").resolve())
        command = run.call_args.args[0]
        self.assertEqual(
            command,
            [
                "mysqldump",
                "-C",
                "-h",
                "db.example.com",
                "-P",
                "3307",
                "-u",
                "backup",
                "-B",
                "example",
                "--no-create-db",
                "--skip-definer",
                "-R",
                "-E",
                "--set-gtid-purged=OFF",
                "--skip-lock-tables",
                "-r",
                ANY,
            ],
        )
        self.assertEqual(run.call_args.args[1]["MYSQL_PWD"], "secret")

    def test_dump_database_uses_local_docker_mysql_image_as_fallback(self) -> None:
        options = MysqlDumpOptions(
            host="localhost",
            port=3306,
            user="root",
            password="secret",
            database="example",
            output=Path("backup.sql"),
            skip_definer=True,
        )
        success = CompletedProcess([], 0, "", "")

        def run_docker(
            command: list[str],
            environment: dict[str, str] | None = None,
            **_: object,
        ) -> CompletedProcess[str]:
            if command[1] == "cp":
                Path(command[-1]).write_text("SELECT 1;\n", encoding="utf-8")
            return success

        with (
            patch("dbtalk.mysql.dump.shutil.which", return_value=None),
            patch("dbtalk.mysql.dump.docker_mapped_mysql_container", return_value=None),
            patch(
                "dbtalk.mysql.dump.docker_mysql_image",
                return_value=("mysql:8.4", ""),
            ),
            patch(
                "dbtalk.mysql.dump.run_command",
                side_effect=run_docker,
            ) as run,
            patch("dbtalk.mysql.dump.remove_temporary_container"),
        ):
            output = dump_database(options)

        self.assertEqual(output, Path("backup.sql").resolve())
        docker_run = run.call_args_list[0].args[0]
        self.assertEqual(docker_run[:3], ["docker", "run", "--name"])
        self.assertIn("mysql:8.4", docker_run)
        self.assertIn("host.docker.internal", docker_run)
        self.assertNotIn("-C", docker_run)
        self.assertIn("--skip-definer", docker_run)
        self.assertNotIn("secret", docker_run)
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "docker",
                "cp",
                ANY,
                ANY,
            ],
        )
        self.assertEqual(run.call_count, 2)

    def test_dump_database_writes_gzip_output_when_archive_is_enabled(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            options = MysqlDumpOptions(
                host="localhost",
                port=3306,
                user="root",
                password="secret",
                database="example",
                output=Path("backup.sql"),
                archive=True,
            )

            def write_dump(
                command: list[str], environment: dict[str, str], **_: object
            ) -> CompletedProcess[str]:
                self.assertEqual(environment["MYSQL_PWD"], "secret")
                Path(command[-1]).write_text("SELECT 1;\n", encoding="utf-8")
                return CompletedProcess(command, 0, "", "")

            with (
                patch(
                    "dbtalk.mysql.dump.docker_mapped_mysql_container",
                    return_value=None,
                ),
                patch(
                    "dbtalk.mysql.dump.shutil.which",
                    return_value="/usr/bin/mysqldump",
                ),
                patch("dbtalk.mysql.dump.run_command", side_effect=write_dump),
            ):
                output = dump_database(options)

            self.assertEqual(output, Path("backup.sql.gz").resolve())
            with gzip.open(output, "rt", encoding="utf-8") as compressed:
                self.assertEqual(compressed.read(), "SELECT 1;\n")

    def test_dump_database_uses_mapped_mysql_container_before_other_clients(self) -> None:
        options = MysqlDumpOptions(
            host="127.0.0.1",
            port=3306,
            user="root",
            password="secret",
            database="example",
            output=Path("backup.sql"),
            skip_definer=True,
        )
        success = CompletedProcess([], 0, "", "")

        def run_docker(
            command: list[str],
            environment: dict[str, str] | None = None,
            **_: object,
        ) -> CompletedProcess[str]:
            if command[1] == "cp":
                Path(command[-1]).write_text("SELECT 1;\n", encoding="utf-8")
            return success

        with (
            patch(
                "dbtalk.mysql.dump.docker_mapped_mysql_container",
                return_value="mysql-server",
            ),
            patch("dbtalk.mysql.dump.shutil.which", return_value="/usr/bin/mysqldump") as which,
            patch(
                "dbtalk.mysql.dump.run_command",
                side_effect=run_docker,
            ) as run,
        ):
            output = dump_database(options)

        self.assertEqual(output, Path("backup.sql").resolve())
        which.assert_not_called()
        dump_command = run.call_args_list[0].args[0]
        self.assertEqual(
            dump_command[:7],
            [
                "docker",
                "exec",
                "--env",
                "MYSQL_PWD",
                "mysql-server",
                "mysqldump",
                "-u",
            ],
        )
        self.assertNotIn("-h", dump_command)
        self.assertNotIn("-P", dump_command)
        self.assertIn("--skip-definer", dump_command)
        self.assertEqual(run.call_args_list[0].args[1]["MYSQL_PWD"], "secret")
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["docker", "cp", ANY, ANY],
        )
        self.assertEqual(
            run.call_args_list[2].args[0][:5],
            ["docker", "exec", "mysql-server", "rm", "-f"],
        )

    def test_docker_mapped_mysql_container_matches_the_requested_host_port(self) -> None:
        listed = CompletedProcess([], 0, "mysql-server\n", "")
        with (
            patch("dbtalk.mysql.client.subprocess.run", return_value=listed),
        ):
            container_id = docker_mapped_mysql_container("localhost", 3306)

        self.assertEqual(container_id, "mysql-server")

    def test_docker_mapped_mysql_container_rejects_ambiguous_container_matches(self) -> None:
        listed = CompletedProcess([], 0, "mysql-one\nmysql-two\n", "")

        with (
            patch("dbtalk.mysql.client.subprocess.run", return_value=listed) as run,
        ):
            container_id = docker_mapped_mysql_container("localhost", 3306)

        self.assertIsNone(container_id)
        self.assertEqual(run.call_count, 1)

    def test_docker_mapped_mysql_container_handles_a_docker_start_failure(self) -> None:
        with (
            patch("dbtalk.mysql.client.subprocess.run", side_effect=OSError("unavailable")),
        ):
            container_id = docker_mapped_mysql_container("localhost", 3306)

        self.assertIsNone(container_id)

    def test_cli_dumps_to_current_directory_when_output_is_omitted(self) -> None:
        runner = CliRunner()

        with (
            runner.isolated_filesystem(),
            patch(
                "dbtalk.mysql.cli.dump_database",
                side_effect=lambda options: options.output,
            ) as dump,
        ):
            Path("backups").mkdir()
            result = runner.invoke(
                main_command,
                [
                    "mysql",
                    "dump",
                    "--dsn",
                    "mysql+pymysql://backup:secret@localhost/example",
                ],
            )
            default_output = dump.call_args.args[0].output
            self.assertTrue(default_output.parent.is_dir())

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertRegex(
            result.output,
            r"MySQL dump written to .+data[\\/]example-\d{8}-\d{6}\.sql\n",
        )
        self.assertRegex(
            default_output.name,
            r"example-\d{8}-\d{6}\.sql",
        )

    def test_cli_uses_explicit_dump_dsn(self) -> None:
        runner = CliRunner()

        with (
            runner.isolated_filesystem(),
            patch(
                "dbtalk.mysql.cli.dump_database",
                side_effect=lambda options: options.output,
            ) as dump,
        ):
            Path("backups").mkdir()
            result = runner.invoke(
                main_command,
                [
                    "mysql",
                    "dump",
                    "--dsn",
                    "mysql+pymysql://cli-user:configured-password@db.example.test:3307/configured-database",
                    "--skip-definer",
                    "--output",
                    "backups",
                ],
            )
            options = dump.call_args.args[0]

            self.assertEqual(options.host, "db.example.test")
            self.assertEqual(options.port, 3307)
            self.assertEqual(options.user, "cli-user")
            self.assertEqual(options.password, "configured-password")
            self.assertEqual(options.database, "configured-database")
            self.assertTrue(options.skip_definer)
            self.assertEqual(options.output.parent, Path.cwd() / "backups")

        self.assertEqual(result.exit_code, 0, result.output)

    def test_default_dump_output_uses_database_and_timestamp(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            output = default_dump_output(
                "example",
                datetime(2026, 8, 19, 10, 37, 5),
                output_directory="data",
            )

            self.assertEqual(
                output,
                Path.cwd() / "data" / "example-20260819-103705.sql",
            )
            self.assertTrue(output.parent.is_dir())

    def test_default_dump_output_adds_a_sequence_for_existing_files(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            Path("data").mkdir()
            Path("data/example-20260819-103705.sql").write_text("old", encoding="utf-8")
            Path("data/example-20260819-103705.sql.gz").write_bytes(b"old")

            self.assertEqual(
                default_dump_output(
                    "example",
                    datetime(2026, 8, 19, 10, 37, 5),
                    output_directory="data",
                ),
                Path.cwd() / "data" / "example-20260819-103705-1.sql",
            )
            self.assertEqual(
                default_dump_output(
                    "example",
                    datetime(2026, 8, 19, 10, 37, 5),
                    output_directory="data",
                    archive=True,
                ),
                Path.cwd() / "data" / "example-20260819-103705-1.sql.gz",
            )

    def test_explicit_dump_directory_uses_timestamped_default_name(self) -> None:
        config = MySQLDumpConfig(
            host="localhost",
            port=3306,
            user="root",
            password="secret",
            database="example",
            output_directory="data",
        )
        runner = CliRunner()

        with (
            runner.isolated_filesystem(),
            patch("dbtalk.mysql.dump.datetime") as mocked_datetime,
        ):
            Path("exports").mkdir()
            mocked_datetime.now.return_value = datetime(2026, 8, 20, 15, 45, 0)
            options = resolve_dump_options(
                config,
                MysqlDumpOverrides(
                    host="localhost",
                    port=3306,
                    user="root",
                    password="secret",
                    target_database="example",
                    dsn_database=None,
                    output=Path("exports"),
                    archive=True,
                ),
            )

            self.assertEqual(
                options.output,
                Path.cwd() / "exports" / "example-20260820-154500.sql.gz",
            )

    def test_cli_accepts_an_existing_dump_directory(self) -> None:
        runner = CliRunner()

        with (
            runner.isolated_filesystem(),
            patch(
                "dbtalk.mysql.cli.dump_database",
                side_effect=lambda options: options.output,
            ) as dump,
        ):
            Path("exports").mkdir()
            result = runner.invoke(
                main_command,
                [
                    "mysql",
                    "dump",
                    "--dsn",
                    "mysql+pymysql://root:secret@localhost/example",
                    "--output",
                    "exports",
                ],
            )

            output = dump.call_args.args[0].output
            self.assertEqual(output.parent, Path.cwd() / "exports")
            self.assertTrue(output.name.startswith("example-"))
            self.assertEqual(output.suffix, ".sql")

        self.assertEqual(result.exit_code, 0, result.output)

    def test_dump_rejects_explicit_file_with_missing_parent_directory(self) -> None:
        options = MysqlDumpOptions(
            host="localhost",
            port=3306,
            user="root",
            password="secret",
            database="example",
            output=Path("missing") / "backup.sql",
        )

        with self.assertRaisesRegex(click.ClickException, "output directory does not exist"):
            dump_database(options)

    def test_dump_failure_preserves_existing_output_and_cleans_temporary_file(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            output = Path("backup.sql")
            output.write_text("known-good\n", encoding="utf-8")
            options = MysqlDumpOptions(
                host="localhost",
                port=3306,
                user="root",
                password="secret",
                database="example",
                output=output,
            )

            def fail_dump(
                command: list[str], environment: dict[str, str], **_: object
            ) -> CompletedProcess[str]:
                Path(command[-1]).write_text("partial\n", encoding="utf-8")
                return CompletedProcess(command, 1, "", "network failure")

            with (
                patch("dbtalk.mysql.dump.docker_mapped_mysql_container", return_value=None),
                patch("dbtalk.mysql.dump.shutil.which", return_value="/usr/bin/mysqldump"),
                patch("dbtalk.mysql.dump.run_command", side_effect=fail_dump),
                self.assertRaisesRegex(click.ClickException, "network failure"),
            ):
                dump_database(options)

            self.assertEqual(output.read_text(encoding="utf-8"), "known-good\n")
            self.assertEqual(list(Path.cwd().glob(".dbtalk-mysqldump-*")), [])

    def test_automatic_dump_publish_does_not_overwrite_a_concurrent_output(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            output = Path("backup.sql")
            options = MysqlDumpOptions(
                host="localhost",
                port=3306,
                user="root",
                password="secret",
                database="example",
                output=output,
                automatic_output=True,
            )

            def write_dump(
                command: list[str], environment: dict[str, str], **_: object
            ) -> CompletedProcess[str]:
                Path(command[-1]).write_text("SELECT 1;\n", encoding="utf-8")
                output.write_text("written by another task\n", encoding="utf-8")
                return CompletedProcess(command, 0, "", "")

            with (
                patch("dbtalk.mysql.dump.docker_mapped_mysql_container", return_value=None),
                patch("dbtalk.mysql.dump.shutil.which", return_value="/usr/bin/mysqldump"),
                patch("dbtalk.mysql.dump.run_command", side_effect=write_dump),
            ):
                published = dump_database(options)

            self.assertEqual(published, Path("backup-1.sql").resolve())
            self.assertEqual(output.read_text(encoding="utf-8"), "written by another task\n")
            self.assertEqual(published.read_text(encoding="utf-8"), "SELECT 1;\n")

    def test_dump_rejects_empty_native_output(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            options = MysqlDumpOptions(
                host="localhost",
                port=3306,
                user="root",
                password="secret",
                database="example",
                output=Path("backup.sql"),
            )

            with (
                patch("dbtalk.mysql.dump.docker_mapped_mysql_container", return_value=None),
                patch("dbtalk.mysql.dump.shutil.which", return_value="/usr/bin/mysqldump"),
                patch(
                    "dbtalk.mysql.dump.run_command",
                    return_value=CompletedProcess([], 0, "", ""),
                ),
                self.assertRaisesRegex(click.ClickException, "non-empty dump"),
            ):
                dump_database(options)

            self.assertFalse(Path("backup.sql").exists())

    def test_dump_does_not_fallback_when_skip_definer_is_unsupported(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            options = MysqlDumpOptions(
                host="localhost",
                port=3306,
                user="root",
                password="secret",
                database="example",
                output=Path("backup.sql"),
                skip_definer=True,
            )

            with (
                patch("dbtalk.mysql.dump.docker_mapped_mysql_container", return_value=None),
                patch("dbtalk.mysql.dump.shutil.which", return_value="/usr/bin/mysqldump"),
                patch(
                    "dbtalk.mysql.dump.run_command",
                    return_value=CompletedProcess(
                        [], 1, "", "mysqldump: unknown option '--skip-definer'"
                    ),
                ),
                self.assertRaisesRegex(click.ClickException, "unknown option"),
            ):
                dump_database(options)

            self.assertFalse(Path("backup.sql").exists())

    def test_resolve_dump_options_uses_dsn_values_and_target_precedence(self) -> None:
        config = MySQLDumpConfig(
            host="db.example.test",
            port=3307,
            user="backup",
            password="test-password",
            database="app",
            output_directory="backups/mysql",
        )
        runner = CliRunner()

        with runner.isolated_filesystem():
            configured = resolve_dump_options(
                config,
                MysqlDumpOverrides(
                    host="dsn.example.test",
                    port=3310,
                    user="dsn-user",
                    password="dsn-password",
                    target_database=None,
                    dsn_database="dsn_database",
                    output=None,
                ),
            )
            overridden = resolve_dump_options(
                config,
                MysqlDumpOverrides(
                    host="dsn.example.test",
                    port=3310,
                    user="dsn-user",
                    password="dsn-password",
                    target_database="target_database",
                    dsn_database="dsn_database",
                    output=Path("explicit.sql"),
                    skip_definer=True,
                ),
            )

            self.assertEqual(configured.host, "dsn.example.test")
            self.assertEqual(configured.port, 3310)
            self.assertEqual(configured.user, "dsn-user")
            self.assertEqual(configured.password, "dsn-password")
            self.assertEqual(configured.database, "dsn_database")
            self.assertTrue(configured.output.parent.is_dir())
            self.assertEqual(configured.output.parent, Path.cwd() / "backups" / "mysql")
            self.assertTrue(configured.output.name.startswith("dsn_database-"))

        self.assertEqual(overridden.host, "dsn.example.test")
        self.assertEqual(overridden.port, 3310)
        self.assertEqual(overridden.user, "dsn-user")
        self.assertEqual(overridden.password, "dsn-password")
        self.assertEqual(overridden.database, "target_database")
        self.assertEqual(overridden.output, Path("explicit.sql"))
        self.assertTrue(overridden.skip_definer)

    def test_resolve_dump_options_rejects_missing_credentials(self) -> None:
        config = MySQLDumpConfig(
            host="localhost",
            port=3306,
            user="configured-user",
            password="configured-password",
            database="configured_database",
            output_directory="data",
        )

        with self.assertRaisesRegex(
            click.ClickException,
            "Missing MySQL dump values: database",
        ):
            resolve_dump_options(
                config,
                MysqlDumpOverrides(
                    host="localhost",
                    port=3306,
                    user="dsn-user",
                    password="dsn-password",
                    target_database=None,
                    dsn_database=None,
                    output=None,
                ),
            )

    def test_dump_database_explains_when_no_execution_path_is_available(self) -> None:
        options = MysqlDumpOptions(
            host="localhost",
            port=3306,
            user="root",
            password="secret",
            database="example",
            output=Path("backup.sql"),
        )

        message = "mysqldump is not available. Docker is not installed or is not on PATH."
        with (
            patch("dbtalk.mysql.dump.shutil.which", return_value=None),
            patch("dbtalk.mysql.dump.docker_mapped_mysql_container", return_value=None),
            patch(
                "dbtalk.mysql.dump.docker_mysql_image",
                return_value=(None, "Docker is not installed or is not on PATH."),
            ),
            self.assertRaisesRegex(
                click.ClickException,
                message,
            ),
        ):
            dump_database(options)

    def test_mysql_restore_args_uses_defaults_without_host_or_port(self) -> None:
        options = MysqlRestoreOptions(
            host="127.0.0.1",
            port=3306,
            user="root",
            password="secret",
            input=Path("backup.sql"),
        )

        self.assertEqual(
            mysql_restore_args(options),
            ["env", "MYSQL_PWD=secret", "mysql", "-u", "root"],
        )

    def test_restore_database_runs_local_mysql_when_available(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            input_path = Path("backup.sql")
            input_path.write_text("SELECT 1;\n", encoding="utf-8")
            options = MysqlRestoreOptions(
                host="db.example.com",
                port=3307,
                user="restore-user",
                password="secret",
                input=input_path,
                database="target_database",
            )
            with (
                patch(
                    "dbtalk.mysql.restore.shutil.which",
                    return_value="/usr/bin/mysql",
                ),
                patch("dbtalk.mysql.restore.docker_mapped_mysql_container", return_value=None),
                patch(
                    "dbtalk.mysql.restore.run_command",
                    return_value=CompletedProcess([], 0, "", ""),
                ) as run,
            ):
                restored_input = restore_database(options)

            self.assertEqual(restored_input, input_path.resolve())
            command = run.call_args_list[1].args[0]
            self.assertEqual(
                command,
                [
                    "mysql",
                    "-h",
                    "db.example.com",
                    "-P",
                    "3307",
                    "-u",
                    "restore-user",
                    "--database",
                    "target_database",
                ],
            )
            self.assertEqual(run.call_args_list[1].args[1]["MYSQL_PWD"], "secret")
            self.assertEqual(
                run.call_args_list[1].kwargs["input_path"],
                input_path.resolve(),
            )

    def test_restore_database_reads_gzip_input(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            input_path = Path("backup.sql.gz")
            with gzip.open(input_path, "wt", encoding="utf-8") as compressed:
                compressed.write("SELECT 1;\n")
            options = MysqlRestoreOptions(
                host="localhost",
                port=3306,
                user="root",
                password="secret",
                input=input_path,
                database="target_database",
            )
            captured_input: Path | None = None

            def read_dump(
                command: list[str],
                environment: dict[str, str],
                *,
                input_path: Path | None = None,
                **_: object,
            ) -> CompletedProcess[str]:
                nonlocal captured_input
                if input_path is None:
                    self.assertEqual(
                        command,
                        [
                            "mysql",
                            "-u",
                            "root",
                            "--database",
                            "target_database",
                            "--batch",
                            "--skip-column-names",
                            "--execute",
                            "SELECT 1",
                        ],
                    )
                    return CompletedProcess(command, 0, "", "")
                self.assertEqual(
                    command,
                    ["mysql", "-u", "root", "--database", "target_database"],
                )
                self.assertEqual(environment["MYSQL_PWD"], "secret")
                self.assertEqual(input_path.read_text(encoding="utf-8"), "SELECT 1;\n")
                captured_input = input_path
                return CompletedProcess(command, 0, "", "")

            with (
                patch(
                    "dbtalk.mysql.restore.shutil.which",
                    return_value="/usr/bin/mysql",
                ),
                patch("dbtalk.mysql.restore.docker_mapped_mysql_container", return_value=None),
                patch("dbtalk.mysql.restore.run_command", side_effect=read_dump),
            ):
                restored = restore_database(options)

            self.assertEqual(restored, input_path.resolve())
            assert captured_input is not None
            self.assertFalse(captured_input.exists())

    def test_restore_database_rebases_dump_use_statement_for_target_database(
        self,
    ) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            input_path = Path("backup.sql")
            source = "USE `source`;\nCREATE TABLE example (id int);\n"
            input_path.write_text(source, encoding="utf-8")
            options = MysqlRestoreOptions(
                host="localhost",
                port=3306,
                user="restore-user",
                password="secret",
                input=input_path,
                database="target_database",
            )
            captured: dict[str, object] = {}

            def capture_input(
                command: list[str],
                environment: dict[str, str],
                *,
                input_path: Path | None = None,
                **_: object,
            ) -> CompletedProcess[str]:
                if input_path is None:
                    return CompletedProcess(command, 0, "", "")
                captured["command"] = command
                captured["environment"] = environment
                captured["input"] = input_path.read_text(encoding="utf-8")
                captured["input_path"] = input_path
                return CompletedProcess([], 0, "", "")

            with (
                patch(
                    "dbtalk.mysql.restore.shutil.which",
                    return_value="/usr/bin/mysql",
                ),
                patch("dbtalk.mysql.restore.docker_mapped_mysql_container", return_value=None),
                patch(
                    "dbtalk.mysql.restore.run_command",
                    side_effect=capture_input,
                ),
            ):
                restore_database(options)

            self.assertEqual(input_path.read_text(encoding="utf-8"), source)
            self.assertEqual(
                captured["command"],
                ["mysql", "-u", "restore-user", "--database", "target_database"],
            )
            environment = cast(dict[str, str], captured["environment"])
            self.assertEqual(environment["MYSQL_PWD"], "secret")
            self.assertEqual(
                captured["input"],
                "USE `target_database`;\nCREATE TABLE example (id int);\n",
            )
            prepared_path = cast(Path, captured["input_path"])
            self.assertFalse(prepared_path.exists())

    def test_restore_rejects_database_lifecycle_ddl_before_client(self) -> None:
        for statement in (
            "CREATE DATABASE `source`;",
            "DROP DATABASE IF EXISTS `source`;",
            "CREATE\nDATABASE `source`;",
            "/* comment */ CREATE /* another comment */ DATABASE `source`;",
            "/*!40100 DROP DATABASE IF EXISTS `source` */;",
        ):
            with self.subTest(statement=statement):
                runner = CliRunner()
                with runner.isolated_filesystem():
                    input_path = Path("backup.sql")
                    input_path.write_text(f"{statement}\nSELECT 1;\n", encoding="utf-8")
                    options = MysqlRestoreOptions(
                        host="localhost",
                        port=3306,
                        user="restore-user",
                        password="secret",
                        input=input_path,
                        database="target_database",
                    )
                    with (
                        patch("dbtalk.mysql.restore.shutil.which", return_value="/usr/bin/mysql"),
                        patch(
                            "dbtalk.mysql.restore.docker_mapped_mysql_container",
                            return_value=None,
                        ),
                        patch("dbtalk.mysql.restore.run_command") as run,
                        self.assertRaisesRegex(
                            click.ClickException,
                            "database lifecycle statements are not allowed",
                        ),
                    ):
                        restore_database(options)
                    run.assert_not_called()

    def test_restore_does_not_reject_lifecycle_words_in_comments_or_strings(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            input_path = Path("backup.sql")
            input_path.write_text(
                "/* CREATE DATABASE `source`; */\nSELECT 'DROP DATABASE `source`';\n",
                encoding="utf-8",
            )
            options = MysqlRestoreOptions(
                host="localhost",
                port=3306,
                user="restore-user",
                password="secret",
                input=input_path,
                database="target_database",
            )

            with (
                patch("dbtalk.mysql.restore.shutil.which", return_value="/usr/bin/mysql"),
                patch("dbtalk.mysql.restore.docker_mapped_mysql_container", return_value=None),
                patch(
                    "dbtalk.mysql.restore.run_command",
                    return_value=CompletedProcess([], 0, "", ""),
                ) as run,
            ):
                restore_database(options)

            self.assertEqual(run.call_count, 2)

    def test_restore_database_uses_mapped_mysql_container_before_other_clients(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            input_path = Path("backup.sql")
            input_path.write_text("SELECT 1;\n", encoding="utf-8")
            options = MysqlRestoreOptions(
                host="localhost",
                port=3306,
                user="root",
                password="secret",
                input=input_path,
                database="target_database",
            )
            success = CompletedProcess([], 0, "", "")

            with (
                patch(
                    "dbtalk.mysql.restore.docker_mapped_mysql_container",
                    return_value="mysql-server",
                ) as mapped,
                patch("dbtalk.mysql.restore.shutil.which", return_value="/usr/bin/mysql") as which,
                patch("dbtalk.mysql.restore.docker_mysql_image") as image,
                patch("dbtalk.mysql.restore.run_command", return_value=success) as run,
            ):
                restored_input = restore_database(options)

            self.assertEqual(restored_input, input_path.resolve())
            mapped.assert_called_once_with("localhost", 3306)
            which.assert_not_called()
            image.assert_not_called()
            probe_command = run.call_args_list[0].args[0]
            self.assertEqual(
                probe_command,
                [
                    "docker",
                    "exec",
                    "--env",
                    "MYSQL_PWD",
                    "mysql-server",
                    "mysql",
                    "-u",
                    "root",
                    "--database",
                    "target_database",
                    "--batch",
                    "--skip-column-names",
                    "--execute",
                    "SELECT 1",
                ],
            )
            restore_command = run.call_args_list[1].args[0]
            self.assertEqual(
                restore_command,
                [
                    "docker",
                    "exec",
                    "-i",
                    "--env",
                    "MYSQL_PWD",
                    "mysql-server",
                    "mysql",
                    "-u",
                    "root",
                    "--database",
                    "target_database",
                ],
            )
            self.assertNotIn("host.docker.internal", restore_command)
            self.assertNotIn("secret", restore_command)
            self.assertEqual(run.call_args_list[1].args[1]["MYSQL_PWD"], "secret")
            self.assertEqual(run.call_args_list[1].kwargs["input_path"], input_path.resolve())
            self.assertEqual(run.call_count, 2)

    def test_restore_rejects_missing_target_database_before_import(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            input_path = Path("backup.sql")
            input_path.write_text("SELECT 1;\n", encoding="utf-8")
            options = MysqlRestoreOptions(
                host="db.example.test",
                port=3307,
                user="restore-user",
                password="secret",
                input=input_path,
                database="missing_database",
            )
            missing = CompletedProcess(
                [],
                1,
                "",
                "ERROR 1049 (42000): Unknown database 'missing_database'",
            )

            with (
                patch("dbtalk.mysql.restore.shutil.which", return_value="/usr/bin/mysql"),
                patch("dbtalk.mysql.restore.run_command", return_value=missing) as run,
                self.assertRaisesRegex(
                    click.ClickException,
                    "Restore target database does not exist: missing_database",
                ),
            ):
                restore_database(options)

            self.assertEqual(run.call_count, 1)

    def test_restore_database_uses_local_docker_mysql_image_as_fallback(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            input_path = Path("backup.sql")
            input_path.write_text("SELECT 1;\n", encoding="utf-8")
            options = MysqlRestoreOptions(
                host="localhost",
                port=3306,
                user="root",
                password="secret",
                input=input_path,
                database="target_database",
            )
            success = CompletedProcess([], 0, "", "")

            with (
                patch("dbtalk.mysql.restore.shutil.which", return_value=None),
                patch("dbtalk.mysql.restore.docker_mapped_mysql_container", return_value=None),
                patch(
                    "dbtalk.mysql.restore.docker_mysql_image",
                    return_value=("mysql:8.4", ""),
                ),
                patch(
                    "dbtalk.mysql.restore.run_command",
                    return_value=success,
                ) as run,
                patch("dbtalk.mysql.restore.remove_temporary_container"),
            ):
                restored_input = restore_database(options)

            self.assertEqual(restored_input, input_path.resolve())
            probe_command = run.call_args_list[0].args[0]
            self.assertEqual(probe_command[:3], ["docker", "run", "--rm"])
            docker_run = run.call_args_list[1].args[0]
            self.assertEqual(docker_run[:5], ["docker", "run", "-i", "--name", ANY])
            self.assertIn("mysql:8.4", docker_run)
            self.assertIn("host.docker.internal", docker_run)
            self.assertIn("target_database", docker_run)
            self.assertNotIn("secret", docker_run)
            self.assertEqual(run.call_args_list[1].args[1]["MYSQL_PWD"], "secret")
            self.assertEqual(
                run.call_args_list[1].kwargs["input_path"],
                input_path.resolve(),
            )
            self.assertEqual(run.call_count, 2)

    def test_restore_database_reports_missing_execution_path(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            input_path = Path("backup.sql")
            input_path.write_text("SELECT 1;\n", encoding="utf-8")
            options = MysqlRestoreOptions(
                host="localhost",
                port=3306,
                user="root",
                password="secret",
                input=input_path,
                database="target_database",
            )

            message = "mysql is not available. Docker is not installed or is not on PATH."
            with (
                patch("dbtalk.mysql.restore.shutil.which", return_value=None),
                patch("dbtalk.mysql.restore.docker_mapped_mysql_container", return_value=None),
                patch(
                    "dbtalk.mysql.restore.docker_mysql_image",
                    return_value=(None, "Docker is not installed or is not on PATH."),
                ),
                self.assertRaisesRegex(
                    click.ClickException,
                    message,
                ),
            ):
                restore_database(options)

    def test_generate_restore_command_quotes_input_path(self) -> None:
        options = MysqlRestoreOptions(
            host="db.example.com",
            port=3307,
            user="backup user",
            password="p@ss word$",
            input=Path("backup file.sql"),
        )

        self.assertEqual(
            generate_restore_command(options),
            "env 'MYSQL_PWD=p@ss word$' mysql -h db.example.com -P 3307 "
            "-u 'backup user' < 'backup file.sql'",
        )

    def test_resolve_restore_options_uses_dsn_values_and_target_precedence(self) -> None:
        options = resolve_restore_options(
            MysqlRestoreOverrides(
                host="dsn.example.test",
                port=3308,
                user="dsn-user",
                password="dsn-password",
                input=Path("backup.sql"),
                target_database="target_database",
                dsn_database="dsn_database",
            )
        )

        self.assertEqual(options.host, "dsn.example.test")
        self.assertEqual(options.port, 3308)
        self.assertEqual(options.user, "dsn-user")
        self.assertEqual(options.password, "dsn-password")
        self.assertEqual(options.database, "target_database")
        self.assertEqual(options.input, Path("backup.sql"))

    def test_resolve_restore_options_falls_back_to_dsn_database(self) -> None:
        options = resolve_restore_options(
            MysqlRestoreOverrides(
                host="dsn.example.test",
                port=3306,
                user="restore-user",
                password="dsn-password",
                input=Path("backup.sql"),
                target_database=None,
                dsn_database="dsn_database",
            )
        )

        self.assertEqual(options.database, "dsn_database")

    def test_resolve_restore_options_requires_a_target_database(self) -> None:
        with self.assertRaisesRegex(click.ClickException, "Missing MySQL restore values: database"):
            resolve_restore_options(
                MysqlRestoreOverrides(
                    host="dsn.example.test",
                    port=3306,
                    user="restore-user",
                    password="dsn-password",
                    input=Path("backup.sql"),
                    target_database=None,
                    dsn_database=None,
                )
            )

    def test_cli_dump_uses_an_explicit_target_for_a_database_free_dsn(self) -> None:
        runner = CliRunner()

        with (
            runner.isolated_filesystem(),
            patch(
                "dbtalk.mysql.cli.dump_database",
                side_effect=lambda options: options.output.resolve(),
            ) as dump,
        ):
            result = runner.invoke(
                main_command,
                [
                    "mysql",
                    "dump",
                    "--dsn",
                    "mysql+pymysql://dump-user:dump-password@db.example.test:3307/",
                    "--database",
                    "target_database",
                    "--output",
                    "backup.sql",
                ],
            )
            options = dump.call_args.args[0]

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(options.host, "db.example.test")
        self.assertEqual(options.port, 3307)
        self.assertEqual(options.user, "dump-user")
        self.assertEqual(options.password, "dump-password")
        self.assertEqual(options.database, "target_database")
        for command in ("dump", "restore"):
            help_result = runner.invoke(main_command, ["mysql", command, "--help"])
            self.assertEqual(help_result.exit_code, 0, help_result.output)
            self.assertIn("--database", help_result.output)

    def test_cli_restores_input_with_explicit_dsn(self) -> None:
        runner = CliRunner()

        with (
            runner.isolated_filesystem(),
            patch(
                "dbtalk.mysql.cli.restore_database",
                side_effect=lambda options: options.input.resolve(),
            ) as restore,
        ):
            input_path = Path("backup.sql")
            input_path.write_text("SELECT 1;\n", encoding="utf-8")
            result = runner.invoke(
                main_command,
                [
                    "mysql",
                    "restore",
                    "--dsn",
                    "mysql+pymysql://cli-user:configured-password@db.example.test:3307/",
                    "--input",
                    "backup.sql",
                    "--database",
                    "target_database",
                ],
            )
            options = restore.call_args.args[0]
            restored_path = input_path.resolve()

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.output,
            f"MySQL dump restored from {restored_path}\n",
        )
        self.assertEqual(options.host, "db.example.test")
        self.assertEqual(options.port, 3307)
        self.assertEqual(options.user, "cli-user")
        self.assertEqual(options.password, "configured-password")
        self.assertEqual(options.database, "target_database")
        self.assertEqual(options.input, input_path)

    def test_cli_restore_requires_an_existing_input_file(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(
                main_command,
                [
                    "mysql",
                    "restore",
                    "--dsn",
                    "mysql+pymysql://root:secret@localhost/example",
                    "--input",
                    "missing.sql",
                ],
            )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("does not exist", result.output)
