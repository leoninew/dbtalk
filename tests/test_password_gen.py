from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "password-gen.py"
DSN_SAFE_SYMBOLS = "-._~"


def run_script(*arguments: str) -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.rstrip("\r\n")


def run_script_lines(*arguments: str) -> list[str]:
    return run_script(*arguments).splitlines()


def test_no_arguments_prints_the_same_help_as_the_help_flag() -> None:
    no_arguments = subprocess.run(
        [sys.executable, str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
    )
    help_flag = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert no_arguments.returncode == 0
    assert no_arguments.stdout == help_flag.stdout
    assert no_arguments.stderr == help_flag.stderr == ""


def test_generates_default_length_unescaped_dsn_password_without_symbols() -> None:
    password = run_script("--length", "8")

    assert len(password) == 8
    assert any(character.islower() for character in password)
    assert any(character.isupper() for character in password)
    assert any(character.isdigit() for character in password)
    assert password.isalnum()


def test_accepts_a_longer_password_length() -> None:
    password = run_script("-l", "24", "-s")

    assert len(password) == 24
    assert any(character in DSN_SAFE_SYMBOLS for character in password)


def test_generates_the_requested_number_of_passwords() -> None:
    passwords = run_script_lines("--length", "12", "-n", "3")

    assert len(passwords) == 3
    assert all(len(password) == 12 for password in passwords)


def test_rejects_a_non_positive_number_of_passwords() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--number", "0"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "number must be at least 1" in result.stderr


def test_rejects_a_length_that_cannot_hold_all_character_classes() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--length", "3", "--symbols"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "length must be at least 4" in result.stderr
