from __future__ import annotations

import contextlib
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import click

from dbtalk.settings import DumpRestoreConfig

from .client import (
    docker_database_host,
    docker_host_gateway_args,
    docker_mapped_mysql_container,
    docker_mysql_image,
    ensure_command_succeeded,
    file_size,
    mysql_connection_args,
    mysql_password_environment,
    remove_temporary_container,
    run_command,
    sanitize_error_message,
    unpack_gzip_input,
)

MYSQL_USE_STATEMENT = re.compile(
    rb"^\s*USE\s+(?:`(?:``|[^`])+`|[A-Za-z0-9_$]+)\s*;\s*$", re.IGNORECASE
)
logger = logging.getLogger("dbtalk")
ProgressCallback = Callable[[int], None]


class SqlLifecycleScanner:
    """Find database lifecycle statements without interpreting quoted SQL text."""

    _IDENTIFIER_BYTES = frozenset(
        b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$"
    )

    def __init__(self) -> None:
        self._state = "normal"
        self._quote_return_state = "normal"
        self._escaped = False
        self._executable_comment = False
        self._comment_marker_pending = False
        self._block_star_pending = False
        self._token = bytearray()
        self._previous_token = b""
        self.found_database_ddl = False

    def feed(self, data: bytes) -> None:
        index = 0
        while index < len(data):
            byte = data[index]
            next_byte = data[index + 1] if index + 1 < len(data) else None

            if self._state == "line_comment":
                if byte in (10, 13):
                    self._state = "normal"
                index += 1
                continue

            if self._state == "block_comment":
                if self._comment_marker_pending:
                    self._comment_marker_pending = False
                    self._executable_comment = byte == 33
                    if not self._executable_comment:
                        index += 1
                        continue
                if self._block_star_pending:
                    if byte == 47:
                        self._state = "normal"
                        self._executable_comment = False
                        self._block_star_pending = False
                        index += 1
                        continue
                    self._block_star_pending = False
                if byte == 42 and next_byte == 47:
                    self._state = "normal"
                    self._executable_comment = False
                    index += 2
                    continue
                if byte == 42:
                    self._block_star_pending = True
                if self._executable_comment:
                    self._consume_normal_byte(byte, in_executable_comment=True)
                    if byte == 59:
                        self._previous_token = b""
                index += 1
                continue

            if self._state in {"single_quote", "double_quote", "backtick"}:
                if self._escaped:
                    self._escaped = False
                    index += 1
                    continue
                if byte == 92:
                    self._escaped = True
                    index += 1
                    continue
                if self._state == "backtick" and byte == 96 and next_byte == 96:
                    index += 2
                    continue
                if (
                    (self._state == "single_quote" and byte == 39)
                    or (self._state == "double_quote" and byte == 34)
                    or (self._state == "backtick" and byte == 96)
                ):
                    self._state = self._quote_return_state
                index += 1
                continue

            if byte == 47 and next_byte == 42:
                self._flush_token()
                self._state = "block_comment"
                self._executable_comment = False
                self._comment_marker_pending = True
                index += 2
                continue
            if (
                byte == 45
                and next_byte == 45
                and (index + 2 >= len(data) or data[index + 2] in b" \t\r\n\x0b\x0c")
            ):
                self._flush_token()
                self._state = "line_comment"
                index += 2
                continue
            if byte == 35:
                self._flush_token()
                self._state = "line_comment"
                index += 1
                continue

            self._consume_normal_byte(byte)
            if byte == 59:
                self._previous_token = b""
            index += 1

    def finish(self) -> None:
        """Flush a final token when the input has no trailing separator."""
        self._flush_token()

    def _consume_normal_byte(self, byte: int, *, in_executable_comment: bool = False) -> None:
        if byte in self._IDENTIFIER_BYTES:
            self._token.append(byte)
            return
        self._flush_token()
        if byte == 39:
            self._state = "single_quote"
        elif byte == 34:
            self._state = "double_quote"
        elif byte == 96:
            self._state = "backtick"
        if self._state in {"single_quote", "double_quote", "backtick"}:
            self._quote_return_state = "block_comment" if in_executable_comment else "normal"

    def _flush_token(self) -> None:
        if not self._token:
            return
        token = bytes(self._token).lower()
        if token in {b"create", b"drop"}:
            self._previous_token = token
        elif token == b"database" and self._previous_token in {b"create", b"drop"}:
            self.found_database_ddl = True
            self._previous_token = token
        else:
            self._previous_token = token
        self._token.clear()


@dataclass(frozen=True)
class MysqlRestoreOptions:
    host: str
    port: int
    user: str
    password: str
    input: Path
    database: str | None = None
    client_image: str = ""


@dataclass(frozen=True)
class MysqlRestoreOverrides:
    host: str
    port: int
    user: str
    password: str
    input: Path
    target_database: str | None
    dsn_database: str | None


def mysql_restore_command_args(options: MysqlRestoreOptions) -> list[str]:
    """Build mysql restore arguments without password or input redirection."""
    args = [
        "mysql",
        *mysql_connection_args(options.host, options.port, options.user),
    ]
    if options.database:
        args.extend(["--database", options.database])
    return args


def mysql_restore_args(options: MysqlRestoreOptions) -> list[str]:
    """Build the mysql restore argument vector without input redirection."""
    return [
        "env",
        f"MYSQL_PWD={options.password}",
        *mysql_restore_command_args(options),
    ]


def generate_restore_command(options: MysqlRestoreOptions) -> str:
    """Return a shell-safe mysql restore command for the supplied options."""
    command = shlex.join(mysql_restore_args(options))
    return f"{command} < {shlex.quote(str(options.input))}"


def resolve_restore_options(
    config: DumpRestoreConfig,
    overrides: MysqlRestoreOverrides,
) -> MysqlRestoreOptions:
    database = overrides.target_database or overrides.dsn_database
    missing = [
        name
        for name, value in (
            ("user", overrides.user),
            ("password", overrides.password),
        )
        if not value
    ]
    if not database:
        missing.append("database")
    if missing:
        names = ", ".join(missing)
        raise click.ClickException(
            f"Missing MySQL restore values: {names}. Provide user and password in the DSN, "
            "and provide the target database with --database or in the DSN."
        )
    assert database is not None

    return MysqlRestoreOptions(
        host=overrides.host,
        port=overrides.port,
        user=overrides.user,
        password=overrides.password,
        input=overrides.input,
        database=database,
        client_image=config.client_image,
    )


def restore_database(options: MysqlRestoreOptions) -> Path:
    """Import a SQL dump with a local mysql client or Docker MySQL image."""
    input_path = options.input.resolve()
    if not input_path.is_file():
        raise click.ClickException(f"SQL dump input file does not exist: {input_path}")
    if not options.database:
        raise click.ClickException("Restore target database is required")

    started_at = time.monotonic()
    stage = "prepare"
    last_progress_at = started_at - 1
    last_progress_bytes = -1

    def report_progress(bytes_count: int, *, force: bool = False) -> None:
        nonlocal last_progress_at, last_progress_bytes
        now = time.monotonic()
        if not force and bytes_count == last_progress_bytes:
            return
        if not force and now - last_progress_at < 1:
            return
        logger.info(
            "mysql restore progress stage=%s elapsed_ms=%d bytes=%d",
            stage,
            elapsed_ms(started_at),
            bytes_count,
        )
        last_progress_at = now
        last_progress_bytes = bytes_count

    logger.info(
        "mysql restore started stage=%s input=%s database=%s",
        stage,
        input_path,
        options.database,
    )
    rebased_input: Path | None = None
    try:
        with unpack_gzip_input(input_path, ".sql") as extracted_input:
            stage = "preflight"
            rebased_input = rebase_dump_input(extracted_input, options.database)
            restore_input = rebased_input or extracted_input
            input_bytes = file_size(restore_input)
            report_progress(input_bytes, force=True)
            container_id = docker_mapped_mysql_container(options.host, options.port)
            if container_id is not None:
                verify_target_database_with_mapped_container(options, container_id)
                stage = "restore"
                restore_with_mapped_container(
                    options,
                    restore_input,
                    container_id,
                    progress_callback=report_progress,
                )
            elif shutil.which("mysql") is not None:
                verify_target_database_with_local_client(options)
                stage = "restore"
                restore_with_local_client(options, restore_input, progress_callback=report_progress)
            else:
                image, reason = docker_mysql_image(options.client_image)
                if image is None:
                    raise click.ClickException(f"mysql is not available. {reason}")
                verify_target_database_with_docker(options, image)
                stage = "restore"
                restore_with_docker(
                    options,
                    restore_input,
                    image,
                    progress_callback=report_progress,
                )
            report_progress(input_bytes, force=True)
            logger.info(
                "mysql restore completed stage=%s elapsed_ms=%d bytes=%d input=%s",
                stage,
                elapsed_ms(started_at),
                input_bytes,
                input_path,
            )
            return input_path
    except Exception as error:
        logger.error(
            "mysql restore failed stage=%s elapsed_ms=%d error=%s",
            stage,
            elapsed_ms(started_at),
            sanitize_error_message(str(error)),
            exc_info=False,
        )
        raise
    finally:
        if rebased_input is not None:
            with contextlib.suppress(OSError):
                rebased_input.unlink()


def rebase_dump_input(input_path: Path, database: str) -> Path | None:
    """Return a temporary dump redirected to an existing target database."""
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix="dbtalk-mysql-restore-", suffix=".sql"
    )
    temporary_path = Path(temporary_name)
    replacement = f"USE `{database.replace('`', '``')}`;".encode()
    replaced = False
    scanner = SqlLifecycleScanner()

    try:
        with (
            os.fdopen(file_descriptor, "wb") as destination,
            input_path.open("rb") as source,
        ):
            for line in source:
                line_ending = (
                    b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
                )
                statement = line.removesuffix(b"\r\n").removesuffix(b"\n")
                scanner.feed(line)
                if scanner.found_database_ddl:
                    raise click.ClickException(
                        "SQL dump contains CREATE DATABASE or DROP DATABASE; "
                        "database lifecycle statements are not allowed during restore"
                    )
                if MYSQL_USE_STATEMENT.fullmatch(statement):
                    destination.write(replacement + line_ending)
                    replaced = True
                else:
                    destination.write(line)
            scanner.finish()
            if scanner.found_database_ddl:
                raise click.ClickException(
                    "SQL dump contains CREATE DATABASE or DROP DATABASE; "
                    "database lifecycle statements are not allowed during restore"
                )
    except click.ClickException:
        with contextlib.suppress(OSError):
            temporary_path.unlink()
        raise
    except OSError as error:
        with contextlib.suppress(OSError):
            temporary_path.unlink()
        raise click.ClickException(
            f"Could not prepare SQL dump for target database: {error}"
        ) from error

    if replaced:
        return temporary_path

    temporary_path.unlink()
    return None


def restore_with_local_client(
    options: MysqlRestoreOptions,
    input_path: Path,
    *,
    progress_callback: ProgressCallback | None = None,
) -> None:
    result = run_command(
        mysql_restore_command_args(options),
        mysql_password_environment(options.password),
        input_path=input_path,
        progress_callback=progress_callback,
    )
    ensure_command_succeeded(result, "mysql restore")


def restore_with_mapped_container(
    options: MysqlRestoreOptions,
    input_path: Path,
    container_id: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Stream a SQL dump into the MySQL client inside the mapped database container."""
    command = [
        "docker",
        "exec",
        "-i",
        "--env",
        "MYSQL_PWD",
        container_id,
        *mysql_restore_command_args(options),
    ]
    result = run_command(
        command,
        mysql_password_environment(options.password),
        input_path=input_path,
        progress_callback=progress_callback,
    )
    ensure_command_succeeded(result, "Container mysql restore")


def restore_with_docker(
    options: MysqlRestoreOptions,
    input_path: Path,
    image: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Stream a SQL dump into a temporary Docker MySQL client container."""
    container_name = f"dbtalk-mysql-restore-{uuid.uuid4().hex}"
    container_options = MysqlRestoreOptions(
        host=docker_database_host(options.host),
        port=options.port,
        user=options.user,
        password=options.password,
        input=input_path,
        database=options.database,
        client_image=options.client_image,
    )
    command = ["docker", "run", "-i", "--name", container_name]
    command.extend(docker_host_gateway_args(options.host))
    command.extend(
        [
            "--env",
            "MYSQL_PWD",
            "--entrypoint",
            "mysql",
            image,
            *mysql_restore_command_args(container_options)[1:],
        ]
    )
    environment = mysql_password_environment(options.password)

    try:
        result = run_command(
            command,
            environment,
            input_path=input_path,
            progress_callback=progress_callback,
        )
        ensure_command_succeeded(result, "Docker mysql restore")
    finally:
        remove_temporary_container(container_name, environment)


def verify_target_database_with_local_client(options: MysqlRestoreOptions) -> None:
    result = run_command(
        [
            *mysql_restore_command_args(options),
            "--batch",
            "--skip-column-names",
            "--execute",
            "SELECT 1",
        ],
        mysql_password_environment(options.password),
    )
    ensure_target_database_succeeded(result, options.database or "")


def verify_target_database_with_mapped_container(
    options: MysqlRestoreOptions, container_id: str
) -> None:
    command = [
        "docker",
        "exec",
        "--env",
        "MYSQL_PWD",
        container_id,
        *mysql_restore_command_args(options),
        "--batch",
        "--skip-column-names",
        "--execute",
        "SELECT 1",
    ]
    result = run_command(command, mysql_password_environment(options.password))
    ensure_target_database_succeeded(result, options.database or "")


def verify_target_database_with_docker(options: MysqlRestoreOptions, image: str) -> None:
    container_options = MysqlRestoreOptions(
        host=docker_database_host(options.host),
        port=options.port,
        user=options.user,
        password=options.password,
        input=options.input,
        database=options.database,
        client_image=options.client_image,
    )
    command = ["docker", "run", "--rm"]
    command.extend(docker_host_gateway_args(options.host))
    command.extend(
        [
            "--env",
            "MYSQL_PWD",
            "--entrypoint",
            "mysql",
            image,
            *mysql_restore_command_args(container_options)[1:],
            "--batch",
            "--skip-column-names",
            "--execute",
            "SELECT 1",
        ]
    )
    result = run_command(command, mysql_password_environment(options.password))
    ensure_target_database_succeeded(result, options.database or "")


def ensure_target_database_succeeded(
    result: subprocess.CompletedProcess[str], database: str
) -> None:
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout or "").lower()
    if "unknown database" in detail:
        raise click.ClickException(f"Restore target database does not exist: {database}")
    ensure_command_succeeded(result, "restore target database preflight")


def elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)
