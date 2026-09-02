"""PostgreSQL custom archive dump execution."""

from __future__ import annotations

import contextlib
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath, PurePosixPath

import click

from dbtalk.settings import DumpRestoreConfig

from .client import (
    PostgresConnection,
    docker_bind_mount,
    docker_database_host,
    docker_host_gateway_args,
    docker_mapped_postgres_container,
    docker_password_environment,
    docker_postgres_image,
    ensure_command_succeeded,
    pgpass_environment,
    run_command,
)


@dataclass(frozen=True)
class PostgresDumpOptions:
    connection: PostgresConnection
    output: Path
    client_image: str
    compression_level: int | None = None


def default_dump_output(
    database: str,
    now: datetime | None = None,
    *,
    output_directory: str | Path,
) -> Path:
    """Create the configured output directory and return a custom archive path."""

    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    directory = Path(output_directory)
    if not directory.is_absolute():
        directory = Path.cwd() / directory
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{database}-{timestamp}.dump"


def resolve_dump_options(
    config: DumpRestoreConfig,
    connection: PostgresConnection,
    output: Path | None,
    compression_level: int | None,
) -> PostgresDumpOptions:
    """Resolve CLI output and compression settings into dump options."""

    resolved_output = (
        default_dump_output(connection.database, output_directory=config.output_directory)
        if output is None or output.is_dir()
        else output
    )
    if output is not None and output.is_dir():
        resolved_output = default_dump_output(
            connection.database,
            output_directory=output,
        )
    return PostgresDumpOptions(
        connection=connection,
        output=resolved_output,
        client_image=config.client_image,
        compression_level=compression_level,
    )


def pg_dump_command_args(
    options: PostgresDumpOptions,
    output: PurePath,
    *,
    docker: bool = False,
    mapped_container: bool = False,
) -> list[str]:
    """Build a password-free ``pg_dump`` command vector."""

    host: str | None
    if mapped_container:
        host = ""
    else:
        host = docker_database_host(options.connection.host) if docker else None
    args = [
        "pg_dump",
        "--format=custom",
        "--file",
        str(output),
        "--dbname",
        options.connection.libpq_uri(host=host, socket=mapped_container),
    ]
    if options.compression_level is not None:
        args.append(f"--compress={options.compression_level}")
    return args


def dump_database(options: PostgresDumpOptions) -> Path:
    """Write one PostgreSQL custom archive with a local or Docker client."""

    output = options.output.resolve()
    if not output.parent.is_dir():
        raise click.ClickException(f"Dump output directory does not exist: {output.parent}")
    temporary_output = output.parent / f".dbtalk-pgdump-{uuid.uuid4().hex}.dump"
    try:
        container_id = docker_mapped_postgres_container(
            options.connection.host, options.connection.port
        )
        if container_id is not None:
            _dump_with_mapped_container(options, temporary_output, container_id)
        elif shutil.which("pg_dump") is not None:
            _dump_with_local_client(options, temporary_output)
        else:
            image, reason = docker_postgres_image(options.client_image)
            if image is None:
                raise click.ClickException(f"pg_dump is not available. {reason}")
            _dump_with_docker(options, temporary_output, image)
        if not temporary_output.is_file():
            raise click.ClickException("pg_dump completed without writing a custom archive")
        temporary_output.replace(output)
        return output
    finally:
        with contextlib.suppress(OSError):
            temporary_output.unlink()


def _dump_with_local_client(options: PostgresDumpOptions, output: Path) -> None:
    with pgpass_environment(options.connection) as environment:
        result = run_command(pg_dump_command_args(options, output), environment)
    ensure_command_succeeded(result, "pg_dump")


def _dump_with_mapped_container(
    options: PostgresDumpOptions, output: Path, container_id: str
) -> None:
    """Run pg_dump through the mapped database container's Unix socket."""

    container_output = f"/tmp/dbtalk-pgdump-{uuid.uuid4().hex}.dump"
    environment = docker_password_environment(options.connection)
    command = [
        "docker",
        "exec",
        "--env",
        "PGPASSWORD",
        container_id,
        *pg_dump_command_args(
            options,
            PurePosixPath(container_output),
            mapped_container=True,
        ),
    ]
    try:
        ensure_command_succeeded(run_command(command, environment), "Container pg_dump")
        copy_command = ["docker", "cp", f"{container_id}:{container_output}", str(output)]
        ensure_command_succeeded(run_command(copy_command, environment), "Container dump copy")
    finally:
        with contextlib.suppress(OSError):
            run_command(["docker", "exec", container_id, "rm", "-f", container_output])


def _dump_with_docker(options: PostgresDumpOptions, output: Path, image: str) -> None:
    docker_output = PurePosixPath("/backup") / output.name
    command = ["docker", "run", "--rm"]
    command.extend(docker_host_gateway_args(options.connection.host))
    command.extend(
        [
            "--env",
            "PGPASSWORD",
            "--mount",
            docker_bind_mount(output.parent, "/backup"),
            "--entrypoint",
            "pg_dump",
            image,
            *pg_dump_command_args(options, docker_output, docker=True)[1:],
        ]
    )
    result = run_command(command, docker_password_environment(options.connection))
    ensure_command_succeeded(result, "Docker pg_dump")
