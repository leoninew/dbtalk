# dbtalk

[![CI](https://github.com/leoninew/pomelo-dbtalk/actions/workflows/ci.yml/badge.svg)](https://github.com/leoninew/pomelo-dbtalk/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pomelo-dbtalk)](https://pypi.org/project/pomelo-dbtalk/)
[![GitHub Release](https://img.shields.io/github/v/release/leoninew/pomelo-dbtalk)](https://github.com/leoninew/pomelo-dbtalk/releases)

`dbtalk` is an LLM-ready database CLI for SQL operations, JSONL data transfer, permissions, and logical backups across SQLite, MySQL, and PostgreSQL.

[中文文档](README.zh-CN.md)

## Features

- Run SQL with `query` and `exec`, including bound parameters and timeouts
- Transfer JSONL data between SQLite, MySQL, and PostgreSQL
- Manage MySQL schemas and users, and PostgreSQL schemas and roles
- Grant and revoke permission profiles; inspect native permissions with `permissions list/show`
- Create and restore MySQL SQL dumps and PostgreSQL custom archives
- Provide database-focused skills for Codex, Claude, and Grok agents

## Installation

Requires Python 3.12+. Install the published CLI with:

```bash
uv tool install pomelo-dbtalk
dbtalk --help
```

The package is published on [PyPI](https://pypi.org/project/pomelo-dbtalk/). Each version is also available from [GitHub Releases](https://github.com/leoninew/pomelo-dbtalk/releases) as a wheel and standalone binaries for Linux x86_64, macOS (Apple Silicon and Intel), and Windows x86_64.

To use a standalone binary, download the matching asset from a release, make it executable on Linux or macOS, then run it directly:

```bash
chmod +x dbtalk-linux-x86_64
./dbtalk-linux-x86_64 --help
```

From a checkout, use [uv](https://docs.astral.sh/uv/):

```bash
uv sync --all-groups
uv run dbtalk --help
```

## Quick start

Reuse an existing `DBTALK_DSN_*` variable from the process environment or current `.env`. Replace the example name below; add a connection only if needed.

```dotenv
DBTALK_DSN_APP=sqlite:///./data/app.db
```

```bash
uv run dbtalk query \
  --dsn-env DBTALK_DSN_APP \
  --sql 'SELECT 1 AS ok'

uv run dbtalk exec \
  --dsn-env DBTALK_DSN_APP \
  --sql 'CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)'

uv run dbtalk exec \
  --dsn-env DBTALK_DSN_APP \
  --file ./data/notes.sql

uv run dbtalk exec \
  --dsn-env DBTALK_DSN_APP \
  --file ./data/notes.sql \
  --dry-run
```

`query` is read-only; `exec` runs a write-capable session. Use `dbtalk -v <command>` or `dbtalk --verbose <command>` to enable operation logs on stderr and sanitized exception details. Logging is silent by default.

## Commands

| Command | Purpose |
| --- | --- |
| `dbtalk mysql schema` | Manage MySQL schemas/databases |
| `dbtalk mysql user` | Manage MySQL users |
| `dbtalk mysql grant` / `dbtalk mysql revoke` | Grant or revoke MySQL permissions |
| `dbtalk postgres schema` | Manage PostgreSQL schemas/databases |
| `dbtalk postgres role` | Manage PostgreSQL roles |
| `dbtalk postgres grant` / `dbtalk postgres revoke` | Grant or revoke PostgreSQL permissions |
| `dbtalk postgres owner reassign` | Preview or transfer PostgreSQL role-owned objects |
| `dbtalk postgres owner transfer` | Transfer business objects within database/schema scope, excluding system and extension objects |
| `dbtalk mysql permissions list/show` | Inspect native MySQL permissions |
| `dbtalk postgres permissions list/show` | Inspect native PostgreSQL permissions |
| `dbtalk mysql session list/kill` | List or terminate client sessions in one database |
| `dbtalk postgres session list/kill` | List or terminate client sessions in one database |
| `dbtalk mysql dump/restore` | Create or restore MySQL SQL dumps |
| `dbtalk postgres dump/restore` | Create or restore PostgreSQL custom archives |
| `dbtalk query/exec` | Query or execute SQL; `exec --file` runs a UTF-8 SQL script |
| `dbtalk export/import` | Transfer JSONL data |

Run `uv run dbtalk --help` or a subcommand's `--help` for full options.

## DSNs and configuration

Supported DSN examples:

```text
sqlite:///./data/app.db
mysql+pymysql://user:password@host:3306/app
postgresql+psycopg://user:password@host:5432/app
```

Every command accepts exactly one of `--dsn DSN` or `--dsn-env NAME`; `--dsn` remains available for direct integrations. Agent workflows use only `--dsn-env` with a `DBTALK_DSN_*` name. Process variables take precedence over the current `.env`; session examples declare variables directly in the command file. `.env.local` and other dotenv variants are not loaded. Defaults are in [dbtalk.yaml](dbtalk.yaml) and can be overridden with `DBTALK_` process environment variables.

Session commands require a database: `--database` overrides the DSN database and the connection target; missing both fails before connecting. `session kill --yes` targets one `--id` or the current database's matching client sessions. Add `--wait SECONDS` to confirm exit within one shared batch budget; omit it to return after sending requests. Logs require root `dbtalk -v`. See the engine guides for permissions, results, and MySQL's default-database scope.

`session.poll_interval_seconds` is required in custom configuration (bundled value: `0.1`); override it with `DBTALK_SESSION__POLL_INTERVAL_SECONDS`. It must be finite and positive. Session queries and termination requests reuse `database.query_timeout_seconds` and `database.exec_timeout_seconds`.

## Documentation

- [Database operations](docs/database.md): SQL, JSONL transfer, and DSN conventions
- [MySQL guide](docs/mysql.md): schemas, users, permissions, sessions, dump, and restore
- [PostgreSQL guide](docs/postgres.md): schemas, roles, permissions, sessions, dump, and restore
- [Batch backups](docs/backup.md): configured backups, database directories, and resume
- [Operation logs](docs/logging.md): coverage, levels, output streams, and redaction

## Agent integration

The repository ships a `dbtalk` plugin for Codex, Claude, and Grok with three skills:

- `dbtalk`: SQL and JSONL data transfer
- `dbtalk-mysql`: MySQL schemas, users, permissions, and dump/restore
- `dbtalk-postgres`: PostgreSQL schemas, roles, permissions, and dump/restore

Install or synchronize the local plugin with:

```bash
make install
```

See [Agent plugin and skills](docs/codex.md) for installation details, parameter constraints, and safety boundaries.

## Development

```bash
make deps
make check
make test
make release
```

`make check` runs Ruff and Mypy. `make test` runs the test suite; integration tests requiring a real database are skipped by default.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).

## Docker

```bash
docker build -t dbtalk .
docker run --rm dbtalk --help
```
