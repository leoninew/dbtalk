from __future__ import annotations

import contextlib
import gzip
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import BinaryIO

import click

from dbtalk.settings import DEFAULT_MYSQL_PORT

LOCAL_MYSQL_HOSTS = frozenset({"localhost", "127.0.0.1"})
ProgressCallback = Callable[[int], None]
PROGRESS_INTERVAL_SECONDS = 0.5


def gzip_output_path(output: Path) -> Path:
    """Return the gzip path associated with a requested archive output."""
    if output.name.lower().endswith(".gz"):
        return output
    return output.with_name(f"{output.name}.gz")


def write_gzip(source: Path, output: Path) -> None:
    """Write a source file as gzip to a caller-owned temporary path."""
    if not source.is_file():
        raise click.ClickException(f"gzip source file does not exist: {source}")
    try:
        with source.open("rb") as input_stream, gzip.open(output, "wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream)
    except OSError as error:
        raise click.ClickException(f"could not write gzip backup: {error}") from error


@contextlib.contextmanager
def unpack_gzip_input(input_path: Path, required_suffix: str) -> Iterator[Path]:
    """Yield an uncompressed temporary input for a gzip path or the original path."""
    if not input_path.name.lower().endswith(".gz"):
        yield input_path
        return
    logical_name = input_path.name[: -len(".gz")]
    if not logical_name.lower().endswith(required_suffix):
        raise click.ClickException(f"gzip backup must contain a {required_suffix} file")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix="dbtalk-gzip-input-", suffix=required_suffix
    )
    temporary_path = Path(temporary_name)
    try:
        with (
            os.fdopen(descriptor, "wb") as output_stream,
            gzip.open(input_path, "rb") as input_stream,
        ):
            shutil.copyfileobj(input_stream, output_stream)
        yield temporary_path
    except (EOFError, OSError) as error:
        raise click.ClickException(f"could not read gzip backup: {error}") from error
    finally:
        with contextlib.suppress(OSError):
            temporary_path.unlink()


def mysql_client_args(executable: str, host: str, port: int, user: str, password: str) -> list[str]:
    """Build common MySQL client arguments without executing them."""
    return [
        "env",
        f"MYSQL_PWD={password}",
        executable,
        *mysql_connection_args(host, port, user),
    ]


def mysql_connection_args(host: str, port: int, user: str) -> list[str]:
    """Build connection arguments shared by MySQL client executables."""
    args: list[str] = []
    if not is_local_mysql_host(host):
        args.extend(["-h", host])
    if port != DEFAULT_MYSQL_PORT:
        args.extend(["-P", str(port)])
    args.extend(["-u", user])
    return args


def is_local_mysql_host(host: str) -> bool:
    return host.lower() in LOCAL_MYSQL_HOSTS


def docker_mysql_image(image: str) -> tuple[str | None, str]:
    """Return the configured MySQL image, pulling it when it is absent locally."""
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

    click.echo(f"Pulling configured MySQL Docker image: {image}", err=True)
    try:
        pull_result = subprocess.run(["docker", "pull", image], check=False)
    except OSError:
        return None, "Docker could not be started."
    if pull_result.returncode != 0:
        return None, f"Configured MySQL Docker image could not be pulled: {image}."
    return image, ""


def docker_mapped_mysql_container(host: str, port: int) -> str | None:
    """Return the sole running container that publishes a local MySQL port."""
    if not is_local_mysql_host(host):
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
    if is_local_mysql_host(host):
        return "host.docker.internal"
    return host


def docker_host_gateway_args(host: str) -> list[str]:
    if is_local_mysql_host(host) and platform.system() != "Windows":
        return ["--add-host", "host.docker.internal:host-gateway"]
    return []


def mysql_password_environment(password: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["MYSQL_PWD"] = password
    return environment


def run_command(
    command: list[str],
    environment: dict[str, str] | None = None,
    *,
    input_path: Path | None = None,
    progress_path: Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> subprocess.CompletedProcess[str]:
    if progress_path is not None and input_path is not None:
        raise ValueError("progress_path and input_path cannot be used together")
    try:
        if progress_callback is None:
            if input_path is None:
                return subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
            return _run_without_progress_input(command, environment, input_path)
    except OSError as error:
        raise click.ClickException(f"Could not run {command[0]}: {error}") from error

    if input_path is not None:
        return _run_with_streamed_input(command, environment, input_path, progress_callback)
    if progress_path is not None:
        return _run_with_output_progress(command, environment, progress_path, progress_callback)
    try:
        return subprocess.run(command, check=False, capture_output=True, text=True, env=environment)
    except OSError as error:
        raise click.ClickException(f"Could not run {command[0]}: {error}") from error


def _run_with_output_progress(
    command: list[str],
    environment: dict[str, str] | None,
    progress_path: Path,
    progress_callback: ProgressCallback | None,
) -> subprocess.CompletedProcess[str]:
    try:
        process = subprocess.Popen(
            command,
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except OSError as error:
        raise click.ClickException(f"Could not run {command[0]}: {error}") from error

    captured: list[tuple[bytes, bytes]] = []

    def collect_output() -> None:
        stdout, stderr = process.communicate()
        captured.append((stdout, stderr))

    waiter = threading.Thread(target=collect_output, daemon=True)
    waiter.start()
    last_size = -1
    while waiter.is_alive():
        current_size = file_size(progress_path)
        if progress_callback is not None and current_size != last_size:
            progress_callback(current_size)
            last_size = current_size
        waiter.join(PROGRESS_INTERVAL_SECONDS)
    waiter.join()
    assert process.returncode is not None
    final_size = file_size(progress_path)
    if progress_callback is not None and final_size != last_size:
        progress_callback(final_size)
    stdout, stderr = captured[0]
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        _decode_output(stdout),
        _decode_output(stderr),
    )


def _run_with_streamed_input(
    command: list[str],
    environment: dict[str, str] | None,
    input_path: Path,
    progress_callback: ProgressCallback | None,
) -> subprocess.CompletedProcess[str]:
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except OSError as error:
        raise click.ClickException(f"Could not run {command[0]}: {error}") from error

    stdout: list[bytes] = []
    stderr: list[bytes] = []
    feeder_error: list[OSError] = []

    def collect(stream: BinaryIO | None, destination: list[bytes]) -> None:
        if stream is not None:
            destination.append(stream.read())

    def feed_input() -> None:
        total = 0
        try:
            with input_path.open("rb") as input_stream:
                assert process.stdin is not None
                while chunk := input_stream.read(64 * 1024):
                    process.stdin.write(chunk)
                    process.stdin.flush()
                    total += len(chunk)
                    if progress_callback is not None:
                        progress_callback(total)
        except OSError as error:
            feeder_error.append(error)
        finally:
            if process.stdin is not None:
                with contextlib.suppress(OSError):
                    process.stdin.close()

    stdout_reader = threading.Thread(target=collect, args=(process.stdout, stdout), daemon=True)
    stderr_reader = threading.Thread(target=collect, args=(process.stderr, stderr), daemon=True)
    feeder = threading.Thread(target=feed_input, daemon=True)
    stdout_reader.start()
    stderr_reader.start()
    feeder.start()
    return_code = process.wait()
    feeder.join()
    stdout_reader.join()
    stderr_reader.join()
    if feeder_error and return_code == 0:
        feeder_exception = feeder_error[0]
        raise click.ClickException(
            f"Could not read SQL dump input: {feeder_exception}"
        ) from feeder_exception
    return subprocess.CompletedProcess(
        command,
        return_code,
        _decode_output(stdout[0] if stdout else b""),
        _decode_output(stderr[0] if stderr else b""),
    )


def _run_without_progress_input(
    command: list[str], environment: dict[str, str] | None, input_path: Path
) -> subprocess.CompletedProcess[str]:
    with input_path.open("rb") as input_stream:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            stdin=input_stream,
            env=environment,
        )


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _decode_output(value: bytes) -> str:
    return value.decode(errors="replace")


def sanitize_error_message(message: str) -> str:
    """Remove credentials and truncate subprocess diagnostics before display or logging."""
    sanitized = re.sub(r"(?i)(mysql_pwd=|password=)[^\s]+", r"\1<redacted>", message)
    sanitized = re.sub(
        r"(?i)(mysql(?:\+pymysql)?://)[^@\s]+@",
        r"\1<redacted>@",
        sanitized,
    )
    return sanitized[:1000]


def ensure_command_succeeded(result: subprocess.CompletedProcess[str], command_name: str) -> None:
    if result.returncode == 0:
        return

    detail = sanitize_error_message(result.stderr.strip() or result.stdout.strip())
    if detail:
        raise click.ClickException(f"{command_name} failed: {detail}")
    raise click.ClickException(f"{command_name} failed with exit code {result.returncode}.")


def remove_temporary_container(container_name: str, environment: dict[str, str]) -> None:
    with contextlib.suppress(OSError):
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
