#!/usr/bin/env python3
"""Derive the project version from Git history without applying it by default.

Rules (x is fixed at 0):
  * y, z start at 0
  * a commit whose subject starts with "feat"  -> y += 1, z = 0
  * any other commit                           -> z += 1

``--no-dry-run`` writes the resulting version to the static ``[project].version``
field in ``pyproject.toml`` and ``dbtalk.__version__``, then refreshes
``uv.lock``.

Usage:

    uv run --locked --no-sync python scripts/version_calc.py
    uv run --locked --no-sync python scripts/version_calc.py --quiet --no-dry-run
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_FILE = REPO_ROOT / "pyproject.toml"
UV_LOCK_FILE = REPO_ROOT / "uv.lock"
PACKAGE_VERSION_FILE = REPO_ROOT / "src" / "dbtalk" / "__init__.py"
PROJECT_VERSION_RE = re.compile(rb"^(\s*version\s*=\s*\")([^\"]+)(\")")
PACKAGE_VERSION_RE = re.compile(rb"^(\s*__version__\s*=\s*\")([^\"]+)(\")", re.MULTILINE)


def run_git(*args: str) -> str:
    """Run Git in the repository and return standard output."""
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout


def run_uv(*args: str) -> None:
    """Run uv in the repository, preserving its output for the caller."""
    subprocess.run(["uv", *args], cwd=REPO_ROOT, check=True)


def is_feature(subject: str) -> bool:
    """Return whether a commit subject advances the minor version."""
    return subject.lstrip().lower().startswith("feat")


def iter_commits() -> Iterator[tuple[str, str, str]]:
    """Yield (full_hash, ISO date, subject) from oldest to newest."""
    log = run_git("log", "--reverse", "--pretty=format:%H%x1f%aI%x1f%s")
    for line in log.splitlines():
        parts = line.split("\x1f", 2)
        if len(parts) == 3:
            full_hash, date, subject = parts
            yield full_hash, date, subject


def calculate_version(*, print_history: bool = True) -> str:
    """Walk Git history and return the calculated x.y.z version."""
    major = 0
    minor = 0
    patch = 0
    saw_commit = False

    for full_hash, date, subject in iter_commits():
        saw_commit = True
        if is_feature(subject):
            minor += 1
            patch = 0
        else:
            patch += 1
        if print_history:
            print(f"{date}  {full_hash[:8]}  {subject[:50]}  {major}.{minor}.{patch}")

    if not saw_commit:
        raise RuntimeError("no commits found; cannot derive version")

    return f"{major}.{minor}.{patch}"


def prepare_project_version_update(version: str) -> tuple[str, bytes]:
    """Return the current project version and an updated pyproject.toml body."""
    raw = PYPROJECT_FILE.read_bytes()
    in_project_table = False
    offset = 0
    match_offset: int | None = None
    match: re.Match[bytes] | None = None

    for line in raw.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(b"[") and stripped.endswith(b"]"):
            in_project_table = stripped == b"[project]"
        elif in_project_table:
            candidate = PROJECT_VERSION_RE.match(line)
            if candidate is not None:
                if match is not None:
                    raise RuntimeError("found multiple version fields in [project]")
                match = candidate
                match_offset = offset
        offset += len(line)

    if match is None or match_offset is None:
        raise RuntimeError("could not find a static [project].version in pyproject.toml")

    previous = match.group(2).decode("utf-8")
    value_start = match_offset + match.start(2)
    value_end = match_offset + match.end(2)
    updated = raw[:value_start] + version.encode("utf-8") + raw[value_end:]
    return previous, updated


def prepare_package_version_update(version: str) -> tuple[str, bytes]:
    """Return the current CLI version and an updated package module body."""
    raw = PACKAGE_VERSION_FILE.read_bytes()
    match = PACKAGE_VERSION_RE.search(raw)
    if match is None:
        raise RuntimeError("could not find a static __version__ in src/dbtalk/__init__.py")

    previous = match.group(2).decode("utf-8")
    value_start, value_end = match.span(2)
    updated = raw[:value_start] + version.encode("utf-8") + raw[value_end:]
    return previous, updated


def restore_version_files(pyproject: bytes, package_version: bytes, lock: bytes) -> None:
    """Restore version metadata after an unsuccessful uv lock refresh."""
    PYPROJECT_FILE.write_bytes(pyproject)
    PACKAGE_VERSION_FILE.write_bytes(package_version)
    UV_LOCK_FILE.write_bytes(lock)


def apply_version(version: str) -> None:
    """Update package metadata and refresh uv.lock as one operation."""
    previous, updated_pyproject = prepare_project_version_update(version)
    previous_package, updated_package = prepare_package_version_update(version)
    original_pyproject = PYPROJECT_FILE.read_bytes()
    original_package = PACKAGE_VERSION_FILE.read_bytes()
    try:
        original_lock = UV_LOCK_FILE.read_bytes()
    except OSError as error:
        raise RuntimeError("version application requires an existing uv.lock") from error

    try:
        if updated_pyproject != original_pyproject:
            PYPROJECT_FILE.write_bytes(updated_pyproject)
        if updated_package != original_package:
            PACKAGE_VERSION_FILE.write_bytes(updated_package)
        run_uv("lock")
    except (OSError, RuntimeError, subprocess.CalledProcessError):
        restore_version_files(original_pyproject, original_package, original_lock)
        raise

    if previous == version:
        print(f"unchanged pyproject.toml -> project.version = {version!r}")
    else:
        print(f"updated pyproject.toml -> project.version {previous!r} -> {version!r}")
    if previous_package == version:
        print(f"unchanged src/dbtalk/__init__.py -> __version__ = {version!r}")
    else:
        print(f"updated src/dbtalk/__init__.py -> __version__ {previous_package!r} -> {version!r}")
    print("refreshed uv.lock")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Derive the dbtalk version from Git history")
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="write version metadata and refresh uv.lock",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="do not print per-commit history lines"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the version calculation command."""
    args = parse_args(argv)
    version = calculate_version(print_history=not args.quiet)
    if not args.quiet:
        print()
    print(f"version: {version}")
    if not args.dry_run:
        apply_version(version)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as error:
        sys.stderr.write(f"command failed: {error}\n")
        sys.exit(1)
    except (OSError, RuntimeError, ValueError, re.error) as error:
        sys.stderr.write(f"{error}\n")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
