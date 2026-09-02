from __future__ import annotations

import contextlib
import logging
import os
import shlex
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import click

from dbtalk.settings import MySQLDumpConfig

from .client import (
    docker_database_host,
    docker_host_gateway_args,
    docker_mapped_mysql_container,
    docker_mysql_image,
    ensure_command_succeeded,
    file_size,
    gzip_output_path,
    is_local_mysql_host,
    mysql_connection_args,
    mysql_password_environment,
    remove_temporary_container,
    run_command,
    sanitize_error_message,
    write_gzip,
)

DOCKER_DUMP_PATH = "/tmp/dbtalk-dump.sql"
logger = logging.getLogger("dbtalk")
ProgressCallback = Callable[[int], None]


@dataclass(frozen=True)
class MysqlDumpOptions:
    host: str
    port: int
    user: str
    password: str
    database: str
    output: Path
    archive: bool = False
    skip_definer: bool = False
    automatic_output: bool = False


@dataclass(frozen=True)
class MysqlDumpOverrides:
    host: str
    port: int
    user: str
    password: str
    target_database: str | None
    dsn_database: str | None
    output: Path | None
    archive: bool = False
    skip_definer: bool = False


def mysqldump_command_args(
    options: MysqlDumpOptions, output: Path | str, *, compress: bool | None = None
) -> list[str]:
    """Build a mysqldump command vector without a password environment value."""
    args = ["mysqldump"]
    if compress is None:
        compress = not is_local_mysql_host(options.host)
    if compress:
        args.append("-C")
    args.extend(
        [
            *mysql_connection_args(options.host, options.port, options.user),
            "-B",
            options.database,
        ]
    )
    args.append("--no-create-db")
    if options.skip_definer:
        args.append("--skip-definer")
    args.extend(
        [
            "-R",
            "-E",
            "--set-gtid-purged=OFF",
            "--skip-lock-tables",
            "-r",
            str(output),
        ]
    )
    return args


def mysqldump_args(options: MysqlDumpOptions) -> list[str]:
    """Build the mysqldump argument vector without executing it."""
    return [
        "env",
        f"MYSQL_PWD={options.password}",
        *mysqldump_command_args(options, options.output),
    ]


def generate_dump_command(options: MysqlDumpOptions) -> str:
    """Return a shell-safe mysqldump command for the supplied options."""
    return shlex.join(mysqldump_args(options))


def default_dump_output(
    database: str,
    now: datetime | None = None,
    *,
    output_directory: str | Path,
    archive: bool = False,
) -> Path:
    """Create the configured dump directory and return a timestamped path."""
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    directory = Path(output_directory)
    if not directory.is_absolute():
        directory = Path.cwd() / directory
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{database}-{timestamp}.sql"
    return _next_available_output(output, archive=archive)


def resolve_dump_options(
    config: MySQLDumpConfig,
    overrides: MysqlDumpOverrides,
) -> MysqlDumpOptions:
    database = overrides.target_database or overrides.dsn_database
    missing = [
        name
        for name, value in (
            ("user", overrides.user),
            ("password", overrides.password),
            ("database", database),
        )
        if not value
    ]
    if missing:
        names = ", ".join(missing)
        raise click.ClickException(
            f"Missing MySQL dump values: {names}. Provide user and password in the DSN, "
            "and provide the target database with --database or in the DSN."
        )
    assert database is not None

    automatic_output = overrides.output is None or overrides.output.is_dir()
    return MysqlDumpOptions(
        host=overrides.host,
        port=overrides.port,
        user=overrides.user,
        password=overrides.password,
        database=database,
        output=(
            default_dump_output(
                database,
                output_directory=(
                    overrides.output if overrides.output is not None else config.output_directory
                ),
                archive=overrides.archive,
            )
            if overrides.output is None or overrides.output.is_dir()
            else overrides.output
        ),
        archive=overrides.archive,
        skip_definer=overrides.skip_definer,
        automatic_output=automatic_output,
    )


def dump_database(options: MysqlDumpOptions) -> Path:
    """Export a database with a local client or a local Docker MySQL image."""
    output = options.output.resolve()
    if not output.parent.is_dir():
        raise click.ClickException(f"Dump output directory does not exist: {output.parent}")

    final_output = gzip_output_path(output) if options.archive else output
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
            "mysql dump progress stage=%s elapsed_ms=%d bytes=%d",
            stage,
            elapsed_ms(started_at),
            bytes_count,
        )
        last_progress_at = now
        last_progress_bytes = bytes_count

    logger.info(
        "mysql dump started stage=%s output=%s",
        stage,
        final_output,
    )
    temporary_sql: Path | None = None
    temporary_archive: Path | None = None
    try:
        temporary_sql = temporary_path(output.parent, ".sql")
        stage = "dump"
        dump_database_file(options, temporary_sql, progress_callback=report_progress)
        ensure_nonempty(temporary_sql, "mysqldump")
        report_progress(file_size(temporary_sql), force=True)
        publish_source = temporary_sql
        if options.archive:
            stage = "compress"
            temporary_archive = temporary_path(output.parent, ".sql.gz")
            write_gzip(temporary_sql, temporary_archive)
            ensure_nonempty(temporary_archive, "gzip backup")
            report_progress(file_size(temporary_archive), force=True)
            publish_source = temporary_archive
        stage = "publish"
        published_output = publish_dump(
            publish_source,
            final_output,
            automatic=options.automatic_output,
        )
        output_bytes = file_size(published_output)
        if output_bytes <= 0:
            raise click.ClickException("published dump is empty")
        report_progress(output_bytes, force=True)
        logger.info(
            "mysql dump completed stage=%s elapsed_ms=%d bytes=%d output=%s",
            stage,
            elapsed_ms(started_at),
            output_bytes,
            published_output,
        )
        return published_output
    except Exception as error:
        logger.error(
            "mysql dump failed stage=%s elapsed_ms=%d error=%s",
            stage,
            elapsed_ms(started_at),
            sanitize_error_message(str(error)),
            exc_info=False,
        )
        raise
    finally:
        if temporary_sql is not None:
            with contextlib.suppress(OSError):
                temporary_sql.unlink()
        if temporary_archive is not None:
            with contextlib.suppress(OSError):
                temporary_archive.unlink()


def dump_database_file(
    options: MysqlDumpOptions,
    output: Path,
    *,
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Write an uncompressed SQL dump to the supplied path."""
    container_id = docker_mapped_mysql_container(options.host, options.port)
    if container_id is not None:
        dump_with_mapped_container(
            options,
            output,
            container_id,
            progress_callback=progress_callback,
        )
        return

    if shutil.which("mysqldump") is not None:
        dump_with_local_client(options, output, progress_callback=progress_callback)
        return

    image, reason = docker_mysql_image()
    if image is None:
        raise click.ClickException(f"mysqldump is not available. {reason}")

    dump_with_docker(options, output, image, progress_callback=progress_callback)


def dump_with_local_client(
    options: MysqlDumpOptions,
    output: Path,
    *,
    progress_callback: ProgressCallback | None = None,
) -> None:
    result = run_command(
        mysqldump_command_args(options, output),
        mysql_password_environment(options.password),
        progress_path=output if progress_callback is not None else None,
        progress_callback=progress_callback,
    )
    ensure_command_succeeded(result, "mysqldump")


def dump_with_mapped_container(
    options: MysqlDumpOptions,
    output: Path,
    container_id: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Run mysqldump through the database container's default Unix socket."""
    container_output = f"/tmp/dbtalk-dump-{uuid.uuid4().hex}.sql"
    environment = mysql_password_environment(options.password)
    command = [
        "docker",
        "exec",
        "--env",
        "MYSQL_PWD",
        container_id,
        *mysqldump_command_args(options, container_output, compress=False),
    ]
    try:
        if progress_callback is not None:
            progress_callback(-1)
        ensure_command_succeeded(run_command(command, environment), "Container mysqldump")
        copy_command = ["docker", "cp", f"{container_id}:{container_output}", str(output)]
        ensure_command_succeeded(run_command(copy_command, environment), "Container dump copy")
        if progress_callback is not None:
            progress_callback(file_size(output))
    finally:
        with contextlib.suppress(OSError):
            run_command(["docker", "exec", container_id, "rm", "-f", container_output])


def dump_with_docker(
    options: MysqlDumpOptions,
    output: Path,
    image: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Run mysqldump in a temporary container and copy the result to the host."""
    container_name = f"dbtalk-mysqldump-{uuid.uuid4().hex}"
    container_options = MysqlDumpOptions(
        host=docker_database_host(options.host),
        port=options.port,
        user=options.user,
        password=options.password,
        database=options.database,
        output=Path(DOCKER_DUMP_PATH),
        archive=False,
        skip_definer=options.skip_definer,
    )
    command = ["docker", "run", "--name", container_name]
    command.extend(docker_host_gateway_args(options.host))
    command.extend(
        [
            "--env",
            "MYSQL_PWD",
            "--entrypoint",
            "mysqldump",
            image,
            *mysqldump_command_args(
                container_options,
                DOCKER_DUMP_PATH,
                compress=not is_local_mysql_host(options.host),
            )[1:],
        ]
    )
    environment = mysql_password_environment(options.password)

    try:
        if progress_callback is not None:
            progress_callback(-1)
        ensure_command_succeeded(run_command(command, environment), "Docker mysqldump")
        copy_command = [
            "docker",
            "cp",
            f"{container_name}:{DOCKER_DUMP_PATH}",
            str(output),
        ]
        ensure_command_succeeded(run_command(copy_command, environment), "Docker dump copy")
        if progress_callback is not None:
            progress_callback(file_size(output))
    finally:
        remove_temporary_container(container_name, environment)


def elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


def temporary_path(directory: Path, suffix: str) -> Path:
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=".dbtalk-mysqldump-", suffix=suffix, dir=directory
        )
    except OSError as error:
        raise click.ClickException(f"Could not create temporary dump file: {error}") from error
    os.close(descriptor)
    return Path(name)


def ensure_nonempty(path: Path, source_name: str) -> None:
    if not path.is_file() or file_size(path) <= 0:
        raise click.ClickException(f"{source_name} completed without writing a non-empty dump")


def _next_available_output(output: Path, *, archive: bool) -> Path:
    candidate = gzip_output_path(output) if archive else output
    sequence = 1
    while candidate.exists():
        candidate = _sequenced_output(output, sequence)
        if archive:
            candidate = gzip_output_path(candidate)
        sequence += 1
    return candidate


def publish_dump(source: Path, output: Path, *, automatic: bool) -> Path:
    if not automatic:
        try:
            source.replace(output)
        except OSError as error:
            raise click.ClickException(f"Could not publish dump: {error}") from error
        return output

    candidate = output
    sequence = 1
    while True:
        try:
            os.link(source, candidate)
            source.unlink()
            return candidate
        except FileExistsError:
            candidate = _sequenced_output(output, sequence)
            sequence += 1
        except OSError as error:
            raise click.ClickException(
                f"Could not publish dump without overwriting: {error}"
            ) from error


def _sequenced_output(output: Path, sequence: int) -> Path:
    if output.name.lower().endswith(".sql.gz"):
        return output.with_name(f"{output.name[: -len('.sql.gz')]}-{sequence}.sql.gz")
    return output.with_name(f"{output.stem}-{sequence}{output.suffix}")
