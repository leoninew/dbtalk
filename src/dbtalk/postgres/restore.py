"""PostgreSQL custom archive restore execution."""

from __future__ import annotations

import contextlib
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath

import click

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

# Native dumps assume initdb's public schema already exists.
POSTGRES_CLEAN_SQL = """
DO $dbtalk_clean$
DECLARE
    object_name text;
    schema_names text;
BEGIN
    -- Stop DDL callbacks before dropping extensions and their member triggers.
    FOR object_name IN SELECT evtname FROM pg_catalog.pg_event_trigger LOOP
        EXECUTE format('ALTER EVENT TRIGGER %I DISABLE', object_name);
    END LOOP;
    FOR object_name IN SELECT subname FROM pg_catalog.pg_subscription
        WHERE subdbid = (SELECT oid FROM pg_catalog.pg_database
                         WHERE datname = current_database()) LOOP
        EXECUTE format('ALTER SUBSCRIPTION %I DISABLE', object_name);
        EXECUTE format('ALTER SUBSCRIPTION %I SET (slot_name = NONE)', object_name);
        EXECUTE format('DROP SUBSCRIPTION {if_exists}%I', object_name);
    END LOOP;
    FOR object_name IN SELECT extname FROM pg_catalog.pg_extension
        WHERE extname <> 'plpgsql' LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = object_name) THEN
            EXECUTE format('DROP EXTENSION {if_exists}%I CASCADE', object_name);
        END IF;
    END LOOP;
    FOR object_name IN SELECT evtname FROM pg_catalog.pg_event_trigger LOOP
        EXECUTE format('DROP EVENT TRIGGER {if_exists}%I CASCADE', object_name);
    END LOOP;
    SELECT string_agg(format('%I', nspname), ', ') INTO schema_names
        FROM pg_catalog.pg_namespace
        WHERE left(nspname, 3) <> 'pg_' AND nspname <> 'information_schema';
    IF schema_names IS NOT NULL THEN
        EXECUTE 'DROP SCHEMA {if_exists}' || schema_names || ' CASCADE';
    END IF;
    -- User casts and access methods can depend only on built-in objects.
    FOR object_name IN SELECT format('DROP CAST {if_exists}(%s AS %s) CASCADE',
        castsource::regtype, casttarget::regtype) FROM pg_catalog.pg_cast WHERE oid >= 16384 LOOP
        EXECUTE object_name;
    END LOOP;
    FOR object_name IN SELECT amname FROM pg_catalog.pg_am WHERE oid >= 16384 LOOP
        EXECUTE format('DROP ACCESS METHOD {if_exists}%I CASCADE', object_name);
    END LOOP;
    FOR object_name IN SELECT pubname FROM pg_catalog.pg_publication LOOP
        EXECUTE format('DROP PUBLICATION {if_exists}%I CASCADE', object_name);
    END LOOP;
    FOR object_name IN SELECT fdwname FROM pg_catalog.pg_foreign_data_wrapper LOOP
        EXECUTE format('DROP FOREIGN DATA WRAPPER {if_exists}%I CASCADE', object_name);
    END LOOP;
    FOR object_name IN SELECT lanname FROM pg_catalog.pg_language
        WHERE lanispl AND lanname <> 'plpgsql' LOOP
        EXECUTE format('DROP LANGUAGE {if_exists}%I CASCADE', object_name);
    END LOOP;
    PERFORM pg_catalog.lo_unlink(oid) FROM pg_catalog.pg_largeobject_metadata;
END;
$dbtalk_clean$;
CREATE SCHEMA public AUTHORIZATION pg_database_owner;
COMMENT ON SCHEMA public IS 'standard public schema';
GRANT USAGE ON SCHEMA public TO PUBLIC;
"""


@dataclass(frozen=True)
class PostgresRestoreOptions:
    connection: PostgresConnection
    input: Path
    client_image: str
    clean: bool = False
    if_exists: bool = False
    preserve_owner: bool = False
    preserve_privileges: bool = False
    jobs: int | None = None


def pg_restore_command_args(
    options: PostgresRestoreOptions,
    input_path: PurePath,
    *,
    docker: bool = False,
    mapped_container: bool = False,
) -> list[str]:
    """Build a password-free ``pg_restore`` command vector."""

    host: str | None
    if mapped_container:
        host = ""
    else:
        host = docker_database_host(options.connection.host) if docker else None
    args = [
        "pg_restore",
        "--dbname",
        options.connection.libpq_uri(host=host, socket=mapped_container),
        "--exit-on-error",
    ]
    if not options.preserve_owner:
        args.append("--no-owner")
    if not options.preserve_privileges:
        args.append("--no-privileges")
    if options.jobs is not None:
        args.extend(["--jobs", str(options.jobs)])
    args.append(str(input_path))
    return args


def restore_database(options: PostgresRestoreOptions) -> Path:
    """Validate and restore one PostgreSQL custom archive."""

    if options.if_exists and not options.clean:
        raise click.ClickException("--if-exists requires --clean")
    input_path = options.input.resolve()
    if not input_path.is_file():
        raise click.ClickException(f"PostgreSQL dump input file does not exist: {input_path}")
    container_id = docker_mapped_postgres_container(
        options.connection.host, options.connection.port
    )
    _validate_archive(options, input_path, container_id=container_id)
    if container_id is not None:
        _restore_with_mapped_container(options, input_path, container_id)
        return input_path
    if shutil.which("pg_restore") is not None:
        _restore_with_local_client(options, input_path)
        return input_path
    image, reason = docker_postgres_image(options.client_image)
    if image is None:
        raise click.ClickException(f"pg_restore is not available. {reason}")
    _restore_with_docker(options, input_path, image)
    return input_path


def _validate_archive(
    options: PostgresRestoreOptions,
    input_path: Path,
    *,
    container_id: str | None = None,
) -> None:
    if container_id is not None:
        _validate_archive_with_mapped_container(options, input_path, container_id)
        return
    if shutil.which("pg_restore") is not None:
        result = run_command(["pg_restore", "--list", str(input_path)])
        ensure_command_succeeded(result, "pg_restore archive validation")
        return
    image, reason = docker_postgres_image(options.client_image)
    if image is None:
        raise click.ClickException(f"pg_restore is not available. {reason}")
    docker_input = PurePosixPath("/backup") / input_path.name
    command = [
        "docker",
        "run",
        "--rm",
        "--mount",
        docker_bind_mount(input_path.parent, "/backup", read_only=True),
        "--entrypoint",
        "pg_restore",
        image,
        "--list",
        str(docker_input),
    ]
    result = run_command(command)
    ensure_command_succeeded(result, "Docker pg_restore archive validation")


def _restore_with_local_client(options: PostgresRestoreOptions, input_path: Path) -> None:
    with pgpass_environment(options.connection) as environment:
        if options.clean:
            if shutil.which("psql") is None:
                raise click.ClickException("psql is required for PostgreSQL restore --clean")
            _clean_target_database(options, environment)
        result = run_command(pg_restore_command_args(options, input_path), environment)
    ensure_command_succeeded(result, "pg_restore")


def _validate_archive_with_mapped_container(
    options: PostgresRestoreOptions, input_path: Path, container_id: str
) -> None:
    container_input = f"/tmp/dbtalk-pg-restore-{uuid.uuid4().hex}.dump"
    environment = docker_password_environment(options.connection)
    try:
        copy_command = ["docker", "cp", str(input_path), f"{container_id}:{container_input}"]
        ensure_command_succeeded(run_command(copy_command, environment), "Container archive copy")
        command = [
            "docker",
            "exec",
            "--env",
            "PGPASSWORD",
            container_id,
            "pg_restore",
            "--list",
            container_input,
        ]
        result = run_command(command, environment)
        ensure_command_succeeded(result, "Container pg_restore archive validation")
    finally:
        with contextlib.suppress(OSError):
            run_command(["docker", "exec", container_id, "rm", "-f", container_input])


def _restore_with_mapped_container(
    options: PostgresRestoreOptions, input_path: Path, container_id: str
) -> None:
    """Run pg_restore inside the mapped database container over its Unix socket."""

    container_input = f"/tmp/dbtalk-pg-restore-{uuid.uuid4().hex}.dump"
    environment = docker_password_environment(options.connection)
    try:
        copy_command = ["docker", "cp", str(input_path), f"{container_id}:{container_input}"]
        ensure_command_succeeded(run_command(copy_command, environment), "Container archive copy")
        if options.clean:
            _clean_target_database(options, environment, container_id=container_id)
        command = [
            "docker",
            "exec",
            "--env",
            "PGPASSWORD",
            container_id,
            *pg_restore_command_args(
                options,
                PurePosixPath(container_input),
                mapped_container=True,
            ),
        ]
        result = run_command(command, environment)
        ensure_command_succeeded(result, "Container pg_restore")
    finally:
        with contextlib.suppress(OSError):
            run_command(["docker", "exec", container_id, "rm", "-f", container_input])


def _restore_with_docker(
    options: PostgresRestoreOptions,
    input_path: Path,
    image: str,
) -> None:
    environment = docker_password_environment(options.connection)
    if options.clean:
        _clean_target_database(options, environment, image=image)
    docker_input = PurePosixPath("/backup") / input_path.name
    command = ["docker", "run", "--rm"]
    command.extend(docker_host_gateway_args(options.connection.host))
    command.extend(
        [
            "--env",
            "PGPASSWORD",
            "--mount",
            docker_bind_mount(input_path.parent, "/backup", read_only=True),
            "--entrypoint",
            "pg_restore",
            image,
            *pg_restore_command_args(options, docker_input, docker=True)[1:],
        ]
    )
    result = run_command(command, environment)
    ensure_command_succeeded(result, "Docker pg_restore")


def _clean_target_database(
    options: PostgresRestoreOptions,
    environment: dict[str, str],
    *,
    container_id: str | None = None,
    image: str | None = None,
) -> None:
    host = "" if container_id is not None else None
    if image is not None:
        host = docker_database_host(options.connection.host)
    args = [
        "psql",
        "--dbname",
        options.connection.libpq_uri(host=host, socket=container_id is not None),
        "--no-psqlrc",
        "--set",
        "ON_ERROR_STOP=1",
        "--single-transaction",
        "--command",
        POSTGRES_CLEAN_SQL.replace("{if_exists}", "IF EXISTS " if options.if_exists else ""),
    ]
    if container_id is not None:
        command = ["docker", "exec", "--env", "PGPASSWORD", container_id, *args]
    elif image is not None:
        command = [
            "docker",
            "run",
            "--rm",
            *docker_host_gateway_args(options.connection.host),
            "--env",
            "PGPASSWORD",
            "--entrypoint",
            "psql",
            image,
            *args[1:],
        ]
    else:
        command = args
    ensure_command_succeeded(run_command(command, environment), "PostgreSQL target cleanup")
