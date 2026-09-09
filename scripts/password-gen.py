#!/usr/bin/env python3
"""Generate a random password that is safe to use unescaped in a URL DSN."""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from collections.abc import Sequence

DEFAULT_LENGTH = 8
LOWERCASE = string.ascii_lowercase
UPPERCASE = string.ascii_uppercase
DIGITS = string.digits
# RFC 3986 unreserved symbols stay unchanged in Python and Go URL DSNs.
DSN_SAFE_SYMBOLS = "-._~"
REQUIRED_CHARSETS = (LOWERCASE, UPPERCASE, DIGITS)


def generate_password(length: int, *, include_symbols: bool) -> str:
    """Return a cryptographically secure password with every required character class."""

    required_charsets = (
        (*REQUIRED_CHARSETS, DSN_SAFE_SYMBOLS) if include_symbols else REQUIRED_CHARSETS
    )
    password_alphabet = "".join(required_charsets)
    minimum_length = len(required_charsets)
    if length < minimum_length:
        raise ValueError(f"password length must be at least {minimum_length}")

    characters = [secrets.choice(charset) for charset in required_charsets]
    characters.extend(secrets.choice(password_alphabet) for _ in range(length - len(characters)))
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a password containing only URL-unreserved characters for Python and Go DSNs."
        )
    )
    parser.add_argument(
        "-l",
        "--length",
        type=int,
        default=DEFAULT_LENGTH,
        help=f"Password length (default: {DEFAULT_LENGTH}; minimum: {len(REQUIRED_CHARSETS)}).",
    )
    parser.add_argument(
        "-n",
        "--number",
        type=int,
        default=1,
        help="Number of passwords to generate (default: 1; minimum: 1).",
    )
    parser.add_argument(
        "-s",
        "--symbols",
        action="store_true",
        help="Require one or more URL-unreserved symbols: -._~.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = sys.argv[1:] if argv is None else list(argv)
    if not arguments:
        parser.print_help()
        return 0

    args = parser.parse_args(arguments)
    minimum_length = len(REQUIRED_CHARSETS) + int(args.symbols)
    if args.length < minimum_length:
        parser.error(f"length must be at least {minimum_length}")
    if args.number < 1:
        parser.error("number must be at least 1")

    for _ in range(args.number):
        print(generate_password(args.length, include_symbols=args.symbols))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
