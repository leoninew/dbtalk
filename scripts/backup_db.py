#!/usr/bin/env python3
"""Sequentially dump databases declared in a Dynaconf YAML configuration."""

from __future__ import annotations

import argparse
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from dynaconf import Dynaconf
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "backup_db.yaml"
ENV_PREFIX = "DBTALK"
Engine = Literal["mysql", "postgres"]
DSN_CREDENTIALS_PATTERN = re.compile(
    r"(?i)((?:mysql(?:\+pymysql)?|postgresql(?:\+psycopg)?):\/\/)[^@\s]+@"
)


@dataclass(frozen=True)
class BackupTarget:
    engine: Engine
    connection: str
    connection_name: str
    database: str
    dsn: str
    enabled: bool


@dataclass(frozen=True)
class BackupConnection:
    name: str
    dsn: str


@dataclass(frozen=True)
class BackupConfig:
    output_directory: Path
    target_validation_connection_timeout_seconds: int
    connections: tuple[BackupConnection, ...]
    targets: tuple[BackupTarget, ...]


@dataclass(frozen=True)
class BackupArtifact:
    target: BackupTarget
    destination: Path
    size_bytes: int
    status: Literal["Succeeded", "Reused"] = "Succeeded"


@dataclass(frozen=True)
class BackupFailure:
    target: BackupTarget
    reason: str


class BackupError(RuntimeError):
    """Raised when the backup run cannot safely continue."""


def positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Back up databases or test configured database connections."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser(
        "backup",
        help="Dump the databases declared in a YAML configuration.",
        description="Sequentially dump the databases declared in a YAML configuration.",
    )
    backup_parser.set_defaults(
        config=DEFAULT_CONFIG_PATH,
        dbtalk_command="dbtalk",
    )
    backup_parser.add_argument(
        "-c",
        "--continue-on-error",
        action="store_true",
        help="Continue remaining backups after an individual backup fails.",
    )
    backup_parser.add_argument(
        "-r",
        "--resume",
        type=Path,
        metavar="DIRECTORY",
        help="Reuse non-empty backups from an existing batch directory.",
    )

    test_parser = subparsers.add_parser(
        "test",
        help="Test each configured database connection.",
        description="Test each configured database connection.",
    )
    test_parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Backup YAML path (default: scripts/backup_db.yaml).",
    )
    test_parser.add_argument(
        "--dbtalk-command",
        default="dbtalk",
        help="dbtalk executable or path (default: dbtalk).",
    )
    test_parser.add_argument(
        "--connect-timeout",
        dest="connect_timeout_seconds",
        type=positive_integer,
        default=None,
        help=(
            "Maximum database connection time for each connection test in seconds. "
            "Overrides target_validation.connection_timeout_seconds in the backup YAML."
        ),
    )
    return parser


def _mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise BackupError(f"{context} must be a YAML mapping with string keys")
    return dict(value)


def _list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise BackupError(f"{context} must be a YAML list")
    return value


def _required_string(values: Mapping[str, object], key: str, context: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BackupError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def _connection_url(values: Mapping[str, object], context: str) -> URL:
    raw_dsn = _required_string(values, "dsn", context)
    try:
        url = make_url(raw_dsn)
    except (ArgumentError, ValueError) as error:
        raise BackupError(f"{context}.dsn must be a valid SQLAlchemy URL") from error
    if url.database:
        raise BackupError(f"{context}.dsn must not include a database name")
    return url


def _connection_engine(url: URL, context: str) -> Engine:
    if url.drivername == "mysql+pymysql":
        return "mysql"
    if url.drivername == "postgresql+psycopg":
        return "postgres"
    raise BackupError(
        f"{context}.dsn must use mysql+pymysql or postgresql+psycopg, got {url.drivername!r}"
    )


def _connection_address(url: URL) -> str:
    host = url.host or "<unknown>"
    return f"{host}:{url.port}" if url.port is not None else host


def _target_dsn(url: URL, database: str) -> str:
    return url.set(database=database).render_as_string(hide_password=False)


def _subprocess_failure_detail(result: subprocess.CompletedProcess[str]) -> str:
    detail = result.stderr.strip() or result.stdout.strip()
    if not detail:
        return ""
    normalized = " ".join(detail.split())
    return DSN_CREDENTIALS_PATTERN.sub(r"\1<redacted>@", normalized)[:1000]


def _parse_enabled(values: Mapping[str, object], context: str) -> bool:
    value = values.get("enabled", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    raise BackupError(f"{context}.enabled must be a boolean")


def _positive_config_integer(value: object, context: str) -> int:
    if isinstance(value, bool):
        raise BackupError(f"{context} must be a positive integer")
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        try:
            return positive_integer(value)
        except argparse.ArgumentTypeError as error:
            raise BackupError(f"{context} must be a positive integer") from error
    raise BackupError(f"{context} must be a positive integer")


def load_backup_config(config_path: Path) -> BackupConfig:
    config_path = config_path.expanduser().resolve()
    if not config_path.is_file():
        raise BackupError(f"could not read backup config: {config_path}")

    try:
        dynaconf = Dynaconf(
            settings_files=[str(config_path)],
            envvar_prefix=ENV_PREFIX,
            environments=False,
        )
        config = {
            str(key).lower(): value for key, value in _mapping(dynaconf.to_dict(), "config").items()
        }
    except (OSError, ValueError, TypeError) as exc:
        raise BackupError(f"could not load backup config: {config_path}") from exc

    output_value = _required_string(config, "output_directory", "config")
    output_directory = Path(output_value)
    if not output_directory.is_absolute():
        output_directory = config_path.parent / output_directory
    target_validation_config = _mapping(
        config.get("target_validation"),
        "config.target_validation",
    )
    target_validation_connection_timeout_seconds = _positive_config_integer(
        target_validation_config.get("connection_timeout_seconds"),
        "config.target_validation.connection_timeout_seconds",
    )

    connections = _list(config.get("connections"), "config.connections")
    if not connections:
        raise BackupError("config.connections must not be empty")

    backup_connections: list[BackupConnection] = []
    targets: list[BackupTarget] = []
    for connection_index, raw_connection in enumerate(connections, 1):
        connection_context = f"config.connections[{connection_index}]"
        connection_config = _mapping(raw_connection, connection_context)
        connection_name = _required_string(connection_config, "name", connection_context)
        connection_url = _connection_url(connection_config, connection_context)
        engine = _connection_engine(connection_url, connection_context)
        address = _connection_address(connection_url)
        backup_connections.append(
            BackupConnection(
                name=connection_name,
                dsn=connection_url.render_as_string(hide_password=False),
            )
        )
        raw_databases = connection_config.get("databases")
        if not isinstance(raw_databases, list) or not raw_databases:
            raise BackupError(f"{connection_context}.databases must be a non-empty list")

        for index, raw_database in enumerate(raw_databases, 1):
            database_context = f"{connection_context}.databases[{index}]"
            database_config = _mapping(raw_database, database_context)
            database = _required_string(database_config, "name", database_context)
            targets.append(
                BackupTarget(
                    engine=engine,
                    connection=address,
                    connection_name=connection_name,
                    database=database,
                    dsn=_target_dsn(connection_url, database),
                    enabled=_parse_enabled(database_config, database_context),
                )
            )

    if not targets:
        raise BackupError("backup config contains no databases")

    return BackupConfig(
        output_directory=output_directory.resolve(),
        target_validation_connection_timeout_seconds=target_validation_connection_timeout_seconds,
        connections=tuple(backup_connections),
        targets=tuple(targets),
    )


def require_dsns(targets: Sequence[BackupTarget]) -> None:
    for target in targets:
        try:
            dsn_database = make_url(target.dsn).database
        except Exception as exc:
            raise BackupError(
                f"invalid DSN for {target.connection_name}.{target.database}"
            ) from exc
        if dsn_database != target.database:
            raise BackupError(
                f"DSN for {target.connection_name}.{target.database} targets database "
                f"{dsn_database or '<none>'}, expected {target.database}"
            )


def resolve_command(command: str) -> str:
    resolved = shutil.which(command)
    if resolved:
        # Let subprocess resolve PATH commands so logs retain the configured name.
        return command

    command_path = Path(command)
    if command_path.is_file():
        return str(command_path.resolve())
    raise BackupError(f"dbtalk executable was not found: {command}")


def safe_component(value: str) -> str:
    component = "".join(
        character if character.isalnum() or character in "-_." else "-" for character in value
    )
    return component.strip("-") or "unknown"


def display_path(path: Path) -> str:
    """Render a path relative to the repository for concise logs and manifests."""

    resolved_path = path.resolve()
    try:
        relative = os.path.relpath(resolved_path, REPOSITORY_ROOT)
    except ValueError:
        return resolved_path.as_posix()
    return Path(relative).as_posix()


def batch_directory(output_directory: Path, batch_timestamp: str) -> Path:
    candidate = output_directory / batch_timestamp
    sequence = 1
    while candidate.exists():
        candidate = output_directory / f"{batch_timestamp}-{sequence:02d}"
        sequence += 1
    return candidate


def output_filename(target: BackupTarget) -> str:
    extension = ".dump" if target.engine == "postgres" else ".sql.gz"
    stem = "-".join((safe_component(target.connection_name), safe_component(target.database)))
    return f"{stem}{extension}"


def output_path(target: BackupTarget, output_directory: Path) -> Path:
    extension = ".dump" if target.engine == "postgres" else ".sql.gz"
    stem = "-".join((safe_component(target.connection_name), safe_component(target.database)))
    filename = output_filename(target)
    candidate = output_directory / filename
    sequence = 1
    while candidate.exists():
        candidate = output_directory / f"{stem}-{sequence:02d}{extension}"
        sequence += 1
    return candidate


def reusable_backup_path(target: BackupTarget, output_directory: Path) -> Path | None:
    candidate = output_directory / output_filename(target)
    if candidate.is_file() and candidate.stat().st_size > 0:
        return candidate
    return None


def manifest_path(output_directory: Path) -> Path:
    candidate = output_directory / "backup-manifest.md"
    sequence = 1
    while candidate.exists():
        candidate = output_directory / f"backup-manifest-{sequence:02d}.md"
        sequence += 1
    return candidate


def markdown_value(value: str) -> str:
    escaped = value.replace("`", "'")
    return f"`{escaped}`"


def write_manifest(
    config_path: Path,
    results: Sequence[BackupArtifact | BackupFailure],
    batch_directory: Path,
    batch_timestamp: str,
    *,
    replace_existing: bool = False,
) -> Path:
    destination = batch_directory / "backup-manifest.md"
    if not replace_existing:
        destination = manifest_path(batch_directory)
    successful_backups = sum(isinstance(result, BackupArtifact) for result in results)
    failed_backups = len(results) - successful_backups
    lines = [
        "# Database Backup Manifest",
        "",
        f"- Generated at (local): `{batch_timestamp}`",
        f"- Configuration: `{display_path(config_path)}`",
        f"- Output directory: `{display_path(batch_directory)}`",
        f"- Successful backups: `{successful_backups}`",
        f"- Failed backups: `{failed_backups}`",
        "",
        "DSN values are intentionally omitted from this manifest.",
        "",
    ]
    grouped_results: dict[str, list[BackupArtifact | BackupFailure]] = {}
    for result in results:
        grouped_results.setdefault(result.target.connection_name, []).append(result)

    for connection_index, (connection_name, connection_results) in enumerate(
        grouped_results.items(), 1
    ):
        connection_target = connection_results[0].target
        lines.extend(
            [
                f"## {connection_index}. {markdown_value(connection_name)}",
                "",
                f"{markdown_value(connection_target.engine)} at "
                f"{markdown_value(connection_target.connection)}",
                "",
                "| Database | Status | Backup file | Format | Size | Error |",
                "| --- | --- | --- | --- | ---: | --- |",
            ]
        )
        for result in connection_results:
            target = result.target
            format_name = (
                "PostgreSQL custom archive"
                if target.engine == "postgres"
                else "MySQL SQL dump (gzip)"
            )
            if isinstance(result, BackupArtifact):
                relative_output = result.destination.relative_to(batch_directory).as_posix()
                lines.append(
                    f"| {markdown_value(target.database)} | "
                    f"{result.status} | {markdown_value(relative_output)} | {format_name} | "
                    f"{result.size_bytes:,} bytes | - |"
                )
            else:
                lines.append(
                    f"| {markdown_value(target.database)} | Failed | - | {format_name} | - | "
                    f"{markdown_value(result.reason)} |"
                )
        lines.append("")

    try:
        destination.write_text("\n".join(lines), encoding="utf-8")
    except OSError as exc:
        raise BackupError(f"could not write backup manifest: {destination}") from exc
    return destination


def run_dump(
    dbtalk: str,
    target: BackupTarget,
    destination: Path,
    environment: Mapping[str, str] | None = None,
) -> None:
    dsn_env = "DBTALK_DSN_BACKUP"
    command = [
        dbtalk,
        target.engine,
        "dump",
        "--dsn-env",
        dsn_env,
        "--output",
        str(destination),
    ]
    if target.engine == "mysql":
        command.append("--archive")

    log_command = command.copy()
    log_command[log_command.index("--output") + 1] = destination.name
    logging.info("dbtalk command=%s", shlex.join(log_command))
    child_environment = dict(environment) if environment is not None else os.environ.copy()
    child_environment[dsn_env] = target.dsn
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=child_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = _subprocess_failure_detail(result)
        diagnostic = f" diagnostic={detail}" if detail else ""
        raise BackupError(
            "dbtalk dump failed "
            f"engine={target.engine} connection={target.connection} "
            f"database={target.database} exit_code={result.returncode}{diagnostic}"
        )
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise BackupError(f"dbtalk dump produced no usable file: {destination.name}")


def run_connection_test(
    dbtalk: str,
    dsn: str,
    connection_timeout_seconds: int,
    environment: Mapping[str, str] | None = None,
) -> bool:
    dsn_env = "DBTALK_DSN_BACKUP"
    command = [
        dbtalk,
        "query",
        "--dsn-env",
        dsn_env,
        "--sql",
        "SELECT 1",
        "--connect-timeout",
        str(connection_timeout_seconds),
        "--format",
        "json",
    ]
    logging.info("dbtalk command=%s", shlex.join(command))
    child_environment = dict(environment) if environment is not None else os.environ.copy()
    child_environment[dsn_env] = dsn
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=child_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def run_backups(args: argparse.Namespace) -> int:
    backup_config = load_backup_config(args.config)
    batch_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    resume_directory = getattr(args, "resume", None)
    if resume_directory is not None:
        batch_output_directory = resume_directory.expanduser().resolve()
        if not batch_output_directory.is_dir():
            raise BackupError(f"resume directory does not exist: {batch_output_directory}")
    else:
        batch_output_directory = batch_directory(backup_config.output_directory, batch_timestamp)
    total = len(backup_config.targets)

    logging.info(
        "backup run started targets=%d config=%s output_dir=%s batch_dir=%s timestamp=%s",
        total,
        args.config.expanduser().resolve(),
        backup_config.output_directory,
        batch_output_directory,
        batch_timestamp,
    )

    dbtalk = None
    enabled_targets = tuple(target for target in backup_config.targets if target.enabled)
    if not args.continue_on_error:
        require_dsns(enabled_targets)
    dbtalk = resolve_command(args.dbtalk_command)
    batch_output_directory.mkdir(parents=True, exist_ok=True)

    results: list[BackupArtifact | BackupFailure] = []
    for index, target in enumerate(backup_config.targets, 1):
        reusable_backup = (
            reusable_backup_path(target, batch_output_directory)
            if resume_directory is not None
            else None
        )
        destination = (
            reusable_backup
            if reusable_backup is not None
            else (
                batch_output_directory / output_filename(target)
                if resume_directory is not None
                else output_path(target, batch_output_directory)
            )
        )
        logging.info(
            "backup planned index=%d/%d engine=%s connection=%s database=%s output=%s",
            index,
            total,
            target.engine,
            target.connection,
            target.database,
            destination.name,
        )
        if not target.enabled:
            logging.info(
                "backup skipped index=%d/%d connection=%s database=%s enabled=%s",
                index,
                total,
                target.connection_name,
                target.database,
                target.enabled,
            )
            continue

        if reusable_backup is not None:
            artifact = BackupArtifact(
                target=target,
                destination=reusable_backup,
                size_bytes=reusable_backup.stat().st_size,
                status="Reused",
            )
            results.append(artifact)
            logging.info(
                "backup reused index=%d/%d engine=%s database=%s bytes=%d output=%s",
                index,
                total,
                target.engine,
                target.database,
                artifact.size_bytes,
                artifact.destination.name,
            )
            continue

        logging.info(
            "backup started index=%d/%d engine=%s database=%s",
            index,
            total,
            target.engine,
            target.database,
        )
        assert dbtalk is not None
        try:
            if args.continue_on_error:
                require_dsns((target,))
            started_at = time.perf_counter()
            run_dump(dbtalk, target, destination)
            duration_seconds = time.perf_counter() - started_at
        except (BackupError, OSError) as exc:
            if not args.continue_on_error:
                raise
            results.append(BackupFailure(target=target, reason=str(exc)))
            logging.error(
                "backup failed index=%d/%d engine=%s connection=%s database=%s error=%s",
                index,
                total,
                target.engine,
                target.connection_name,
                target.database,
                exc,
            )
            continue
        artifact = BackupArtifact(
            target=target,
            destination=destination,
            size_bytes=destination.stat().st_size,
        )
        results.append(artifact)
        logging.info(
            "backup completed index=%d/%d engine=%s database=%s bytes=%d duration_seconds=%.3f",
            index,
            total,
            target.engine,
            target.database,
            artifact.size_bytes,
            duration_seconds,
        )

    manifest = write_manifest(
        args.config.expanduser().resolve(),
        results,
        batch_output_directory,
        batch_timestamp,
        replace_existing=resume_directory is not None,
    )
    successful_backups = sum(isinstance(result, BackupArtifact) for result in results)
    failed_backups = len(results) - successful_backups
    logging.info("backup manifest written path=%s", manifest)
    logging.info(
        "backup run completed targets=%d succeeded=%d failed=%d",
        total,
        successful_backups,
        failed_backups,
    )
    return 0 if not any(isinstance(result, BackupFailure) for result in results) else 1


def run_tests(args: argparse.Namespace) -> int:
    backup_config = load_backup_config(args.config)
    dbtalk = resolve_command(args.dbtalk_command)
    connection_timeout_seconds = (
        args.connect_timeout_seconds
        if args.connect_timeout_seconds is not None
        else backup_config.target_validation_connection_timeout_seconds
    )

    total = len(backup_config.connections)
    passed = 0
    logging.info(
        "connection test run started connections=%d connection_timeout_seconds=%d",
        total,
        connection_timeout_seconds,
    )
    for index, connection in enumerate(backup_config.connections, 1):
        logging.info(
            "connection test started index=%d/%d connection=%s",
            index,
            total,
            connection.name,
        )
        started_at = time.perf_counter()
        succeeded = run_connection_test(dbtalk, connection.dsn, connection_timeout_seconds)
        duration_seconds = time.perf_counter() - started_at
        if succeeded:
            passed += 1
            logging.info(
                "connection test passed index=%d/%d connection=%s duration_seconds=%.3f",
                index,
                total,
                connection.name,
                duration_seconds,
            )
        else:
            logging.error(
                "connection test failed index=%d/%d connection=%s duration_seconds=%.3f",
                index,
                total,
                connection.name,
                duration_seconds,
            )

    failed = total - passed
    logging.info(
        "connection test run completed connections=%d passed=%d failed=%d", total, passed, failed
    )
    return 0 if failed == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    args = build_parser().parse_args(argv)
    try:
        if args.command == "backup":
            return run_backups(args)
        if args.command == "test":
            return run_tests(args)
        raise BackupError(f"unsupported command: {args.command}")
    except BackupError:
        logging.exception("%s run failed", args.command)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
