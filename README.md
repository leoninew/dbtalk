# dbtalk

[![CI](https://github.com/leoninew/pomelo-dbtalk/actions/workflows/ci.yml/badge.svg)](https://github.com/leoninew/pomelo-dbtalk/actions/workflows/ci.yml)

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

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
uv run dbtalk --help
```

## Quick start

Keep the connection string in the current directory's `.env`, then refer to it only by name:

```dotenv
DBTALK_DSN_APP=sqlite:///./data/app.db
```

```bash
uv run dbtalk query \
  --dsn-env DBTALK_DSN_APP \
  --sql 'SELECT 1 AS ok'

uv run dbtalk exec \
  --write \
  --dsn-env DBTALK_DSN_APP \
  --sql 'CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)'
```

Agents write the DSN to `.env` before the first database command. `--dsn-env DBTALK_DSN_*` reads the process environment first and only falls back to the current `.env` when that variable is absent.

## Commands

| Command | Purpose |
| --- | --- |
| `dbtalk mysql schema` | Manage MySQL schemas/databases |
| `dbtalk mysql user` | Manage MySQL users |
| `dbtalk mysql grant` / `dbtalk mysql revoke` | Grant or revoke MySQL permissions |
| `dbtalk postgres schema` | Manage PostgreSQL schemas/databases |
| `dbtalk postgres role` | Manage PostgreSQL roles |
| `dbtalk postgres grant` / `dbtalk postgres revoke` | Grant or revoke PostgreSQL permissions |
| `dbtalk mysql permissions list/show` | Inspect native MySQL permissions |
| `dbtalk postgres permissions list/show` | Inspect native PostgreSQL permissions |
| `dbtalk mysql dump/restore` | Create or restore MySQL SQL dumps |
| `dbtalk postgres dump/restore` | Create or restore PostgreSQL custom archives |
| `dbtalk query/exec` | Query or execute one SQL statement |
| `dbtalk export/import` | Transfer JSONL data |

Run `uv run dbtalk --help` or a subcommand's `--help` for full options.

## DSNs and configuration

Supported DSN examples:

```text
sqlite:///./data/app.db
mysql+pymysql://user:password@host:3306/app
postgresql+psycopg://user:password@host:5432/app
```

Every command accepts exactly one of `--dsn DSN` or `--dsn-env NAME`; `--dsn` remains available for direct integrations. Agent workflows must plan a `DBTALK_DSN_*` name, write the value to the current `.env`, and use only `--dsn-env`. `.env.local` and other dotenv variants are not loaded. Defaults are in [dbtalk.yaml](dbtalk.yaml) and can be overridden with `DBTALK_` process environment variables.

## Documentation

- [Database operations](docs/database.md): SQL, JSONL transfer, and DSN conventions
- [MySQL guide](docs/mysql.md): schemas, users, permissions, dump, and restore
- [PostgreSQL guide](docs/postgres.md): schemas, roles, permissions, dump, and restore

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
