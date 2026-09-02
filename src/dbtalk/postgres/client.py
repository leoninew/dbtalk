"""Safe PostgreSQL native-client connection and subprocess helpers."""

from __future__ import annotations

import contextlib
import os
import platform
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import click
from sqlalchemy.engine import URL

from dbtalk.database.dsn import ParsedDsn

DEFAULT_POSTGRES_PORT = 5432
LOCAL_POSTGRES_HOSTS = frozenset({"localhost", "127.0.0.1"})


@dataclass(frozen=True)
class PostgresConnection:
    """Connection details suitable for a password-free libpq URI."""

    url: URL
    host: str
    port: int
    user: str
    password: str
    database: str

    @classmethod
    def from_parsed_dsn(
        cls,
        parsed: ParsedDsn,
        *,
        database: str | None = None,
    ) -> PostgresConnection:
        if parsed.dialect != "postgresql":
            raise ValueError("PostgreSQL dump and restore require a postgresql+psycopg DSN")
        host = parsed.host
        user = parsed.url.username
        target_database = database or parsed.database
        if not host or not user:
            raise ValueError("PostgreSQL DSN must include host and user")
        if not target_database:
            raise ValueError("PostgreSQL dump and restore require --database or a DSN database")
        return cls(
            url=parsed.url,
            host=host,
            port=parsed.port or DEFAULT_POSTGRES_PORT,
            user=user,
            password=parsed.url.password or "",
            database=target_database,
        )

    def libpq_uri(self, *, host: str | None = None, socket: bool = False) -> str:
        """Return a libpq URI with the SQLAlchemy driver and password removed."""

        return URL.create(
            drivername="postgresql",
            username=self.user,
            host=self.host if host is None else host,
            port=None if socket else self.port,
            database=self.database,
            query=self.url.query,
        ).render_as_string(hide_password=False)


@contextlib.contextmanager
def pgpass_environment(connection: PostgresConnection) -> Iterator[dict[str, str]]:
    """Yield an environment pointing libpq at a temporary password file."""

    descriptor, temporary_name = tempfile.mkstemp(prefix="dbtalk-pgpass-")
    temporary_path = Path(temporary_name)
    try:
        os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as credential_file:
            credential_file.write(
                ":".join(
                    (
                        escape_pgpass_value(connection.host),
                        str(connection.port),
                        escape_pgpass_value(connection.database),
                        escape_pgpass_value(connection.user),
                        escape_pgpass_value(connection.password),
                    )
                )
                + "\n"
            )
        environment = os.environ.copy()
        environment["PGPASSFILE"] = str(temporary_path)
        yield environment
    finally:
        with contextlib.suppress(OSError):
            temporary_path.unlink()


def escape_pgpass_value(value: str) -> str:
    """Escape one ``.pgpass`` field without allowing record injection."""

    if "\r" in value or "\n" in value:
        raise click.ClickException("PostgreSQL connection values must not contain line breaks")
    return value.replace("\\", "\\\\").replace(":", "\\:")


def docker_password_environment(connection: PostgresConnection) -> dict[str, str]:
    """Supply the PostgreSQL password to Docker without placing it in argv."""

    environment = os.environ.copy()
    environment["PGPASSWORD"] = connection.password
    return environment


def docker_postgres_image(image: str) -> tuple[str | None, str]:
    """Return the configured PostgreSQL image, pulling it when it is absent locally."""

    if shutil.which("docker") is None:
        return None, "Docker is not installed or is not on PATH."
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None, "Docker could not be started."
    if result.returncode == 0:
        return image, ""

    click.echo(f"Pulling configured PostgreSQL Docker image: {image}", err=True)
    try:
        pull_result = subprocess.run(["docker", "pull", image], check=False)
    except OSError:
        return None, "Docker could not be started."
    if pull_result.returncode != 0:
        return None, f"Configured PostgreSQL Docker image could not be pulled: {image}."
    return image, ""


def docker_mapped_postgres_container(host: str, port: int) -> str | None:
    """Return the sole running container that publishes a local PostgreSQL port."""

    if not is_local_postgres_host(host):
        return None
    try:
        listed = subprocess.run(
            [
                "docker",
                "ps",
                "--quiet",
                "--filter",
                "status=running",
                "--filter",
                f"publish={port}",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if listed.returncode != 0:
        return None

    container_ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    return container_ids[0] if len(container_ids) == 1 else None


def docker_database_host(host: str) -> str:
    """Translate a host-local PostgreSQL address for a Docker client container."""

    if is_local_postgres_host(host):
        return "host.docker.internal"
    return host


def docker_host_gateway_args(host: str) -> list[str]:
    """Provide the Docker Desktop host alias on Linux when it is required."""

    if is_local_postgres_host(host) and platform.system() != "Windows":
        return ["--add-host", "host.docker.internal:host-gateway"]
    return []


def docker_bind_mount(source: Path, destination: str, *, read_only: bool = False) -> str:
    """Build a bind-mount value while preserving spaces in the host path."""

    options = f"type=bind,src={source.resolve()},dst={destination}"
    return f"{options},readonly" if read_only else options


def is_local_postgres_host(host: str) -> bool:
    return host.lower() in LOCAL_POSTGRES_HOSTS


def run_command(
    command: list[str], environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Execute one native client command without invoking a shell."""

    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
    except OSError as error:
        raise click.ClickException(f"Could not run {command[0]}: {error}") from error


def ensure_command_succeeded(result: subprocess.CompletedProcess[str], command_name: str) -> None:
    """Convert a native client failure into a concise Click error."""

    if result.returncode == 0:
        return
    detail = result.stderr.strip() or result.stdout.strip()
    if detail:
        raise click.ClickException(f"{command_name} failed: {detail}")
    raise click.ClickException(f"{command_name} failed with exit code {result.returncode}.")
