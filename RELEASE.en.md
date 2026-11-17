# Release Notes

Organized by Git commit date. Each entry focuses on user-facing capabilities and behavior changes, with short commit hashes at the end; internal refactors, test adjustments, and release pipeline work are not listed. The current version is `0.42.0`. For command details see the [database manual](docs/database.md), [MySQL](docs/mysql.md), [PostgreSQL](docs/postgres.md), and [batch backup](docs/backup.md) guides.

Chinese version: [RELEASE.md](RELEASE.md).

## 2026-11-15

- Operational logging is now on by default: CLI lifecycle events and sanitized exception details go to stderr, while stdout keeps query results, SQL previews, and operation output so it stays safe to redirect and parse as JSON.
- The root `-v` raises the log level to `DEBUG`; level and format remain configurable through settings and `DBTALK_LOGGING__*`.

Commits: `a1d1ada`.

## 2026-11-02

- SQL Server support: `query`/`exec` reuse the existing execution flow, SQL files split on standalone `GO` lines, and no system ODBC is required.
- JSONL export and import now work across SQLite, MySQL, PostgreSQL, and SQL Server; SQL Server accepts explicit identity values, upserts on the full primary key, and preflights type and precision before import.
- Documented limits: transfer only covers the connection's default schema and cross-schema foreign keys fail explicitly; precision- or offset-sensitive types such as `datetime2(7)`, `time(7)`, and `datetimeoffset` are excluded from transfer, while `query`/`exec` are unaffected.

Commits: `2341deb`.

## 2026-10-15

- MySQL and PostgreSQL `session list/kill`: list client sessions for a database and terminate them, optionally waiting for exit with `--wait`, reporting `requested`, `terminated`, `gone`, `changed`, `failed`, `timeout`, or `not_attempted` per target.
- `schema drop --if-exists` works on MySQL and PostgreSQL; PostgreSQL also accepts `--force`, which attempts to terminate connections to the target database with `WITH (FORCE)` before dropping it.
- CLI lifecycle logging and sanitized error details are introduced, and `--verbose` moves to the root command (`dbtalk -v <command>`).
- Agent skills reuse existing `DBTALK_DSN_*` and password variables before adding missing configuration.

Commits: `ceae403`, `43e1259`, `d8e21d9`, `3ba5b9b`, `fd6f8c3`.

## 2026-10-13

- New `postgres owner transfer`: transfers business object ownership for a target database, object by object, scoped by repeatable `--schema`, excluding system schemas, built-in objects, and extension members. The plan is shown by default and only `--yes` executes it in a single transaction that rolls back entirely on failure.

Commits: `b9d6d68`.

## 2026-10-12

- New backup `clean`: retains the last seven days or the latest seven files per database directory and the `manifests` directory independently; it previews by default and only deletes with `--no-dry-run`.
- `test` and `sync` accept connections without a configured database list, discover non-system databases per connection engine, and log each addition and removal.
- Connection tests and sync discovery are bounded by the smaller of the connection and query timeouts, five seconds by default.

Commits: `7074f41`, `22b57ec`, `c570314`.

## 2026-10-04

- JSONL import is now bounded batching: input order and per-table transactions are preserved, PostgreSQL and SQLite upsert on the full primary key, and target conversions are preflighted before import.
- Export streams through a cursor; import reports pending, committed, and rolled-back progress, and a failure rolls back only the current table while previously committed tables remain.

Commits: `5a9cc18`.

## 2026-10-02

- Backup connections can be disabled entirely, in which case `backup`, `test`, and `sync` skip them; completion totals count skipped databases toward the target total.

Commits: `104c462`.

## 2026-10-01

- Target databases can be fully cleaned before restore: PostgreSQL now cleans target objects before importing, and MySQL adds `restore --clean`, which drops all tables, views, routines, and events in a cleanup session with foreign key checks disabled. MySQL DDL cannot roll back as a whole, so a failure can leave the target partially restored.
- Backup artifacts are grouped by `<connection>_<database>`, with timestamped files for one batch stored together; `--resume <timestamp>` reuses non-empty backups and updates the same manifest.

Commits: `048f9e8`, `a0adc0b`.

## 2026-09-28

- `sync --continue-on-error`: after a connection fails, the remaining connections are still processed, successful updates are kept, and the command exits as failed.

Commits: `26f9a20`.

## 2026-09-23

- New `postgres owner reassign`: previews and then runs native `REASSIGN OWNED`; when the source role owns shared objects such as databases or tablespaces, execution requires `--include-shared` in addition to `--yes`.

Commits: `7048d58`.

## 2026-09-22

- SQL runs literally when no bind parameters are provided and keeps named binding when they are, so characters such as `%` in literal values are no longer misparsed.

Commits: `45ef075`.

## 2026-09-21

- Database management failures now report a sanitized driver reason, and a missing PostgreSQL driver is called out explicitly instead of being misreported as a connection failure.
- Plugin metadata is registered with supported hosts for local discovery.

Commits: `daf0c39`, `a0cc5ef`.

## 2026-09-17

- New `exec --file` runs a UTF-8 SQL script; the CLI owns the transaction boundary, so a single script completes within one write transaction.
- Scripts are split with `sqlparse`, keeping procedure bodies and dollar-quoted functions intact while skipping comments and transaction control statements.
- New `exec --dry-run` prints the statements to run without connecting to the database or writing anything.

Commits: `9b559bb`, `cdca45d`, `12d9d9d`.

## 2026-09-14

- New backup `sync`: aligns the configured list with the non-system databases visible to the connection, enables newly discovered databases by default, preserves settings for existing ones, and removes databases that no longer exist.

Commits: `397a2c5`.

## 2026-09-11

- MySQL `user drop`, `grant`, and `revoke` accept `--all-hosts`, applying to every existing host of a username.
- PostgreSQL `grant` can specify `--database` and `--schema` together, and rejects schema-only grants when the DSN has no database, avoiding the driver default database.
- `schema drop` refuses only when the live session database matches `--name`, which makes database-free DSNs usable; a scalar `DBTALK_DATABASE` is no longer treated as the whole database mapping and fails at load time.

Commits: `9c1736e`, `6420196`, `a56a412`.

## 2026-09-09

- `dump --exclude-table` is repeatable and skips table objects entirely; `exclude_tables` in the backup configuration is passed through to dump.
- `DBTALK_*` passwords fall back to the current directory `.env` when the process variable is absent, while an empty process variable still fails; MySQL password rotation covers every existing host of a user.
- The password generator adds a `-s` option for symbols; script paths are standardized on kebab-case, and dump output streams while hiding DSN credentials.

Commits: `57114e1`, `c02adec`, `d40f7ed`.

## 2026-09-08

- `exec` always uses a write session and the `--write` option is removed; leaf-command `-v` appends a sanitized exception type and message, while default errors still show only stable stage text.
- The package publishes to PyPI as `pomelo-dbtalk` and ships a wheel plus standalone executables for Linux, macOS, and Windows.

Commits: `e1d398a`, `d7ed77e`, `b29b4e2`.

## 2026-09-03

- Agents use `--dsn-env` with `DBTALK_DSN_*` consistently: missing process variables are read from the current directory `.env`, and global dotenv configuration is disabled so connections from other directories are not loaded implicitly.

Commits: `11cd15c`.

## 2026-09-02

- MySQL and PostgreSQL DSNs without a database can run instance-level operations; JSONL transfer and native backup and restore still require an explicit target database.
- MySQL and PostgreSQL share `output_directory` and `client_image` settings; query and exec timeouts are configured separately; MySQL zero-date handling moves into MySQL settings.
- Backup connection validation uses a database-free DSN and validates each connection once, pulls the configured Docker client, and emits sanitized diagnostics.

Commits: `032bc5d`, `c0788f7`, `98c182a`.

## 2026-09-01

- Permission profiles converge on `readonly`, `readwrite`, and `migrator`; `migrator` additionally gets target database DDL and database creation privileges.
- New MySQL and PostgreSQL `permissions list/show` for native privileges; database lifecycle commands are flattened into `schema`.
- Backup supports resuming: a failed target does not affect the rest, the manifest records success, reuse, and failure, and `--resume` reuses an existing batch.
- The version is computed from Git history and synchronized atomically into package metadata and the lockfile.

Commits: `a39d5f5`, `a4bf3da`, `7a9c89a`, `7e13ad8`.

## 2026-08-31

- New backup `backup` and `test` subcommands write artifacts under a batch timestamp and record `DBTALK_*` connection checks.
- Backup manifests group by connection with nested databases; connections and databases can each be disabled, and validation and backup skip disabled entries.
- Backup connections move to layered YAML configuration with environment overrides preserved, and the local credential-bearing config file is ignored.
- PostgreSQL dump and restore reuse a container mapped to the local port, running through the container's Unix socket while keeping native client and Docker client fallbacks.

Commits: `99ca728`, `ab41dc8`, `d14e617`, `dd08e4a`, `7dd8128`.

## 2026-08-30

- New DSN-based batch database backup and safe password generation.
- JSONL import accepts portable boolean representations, so engine-specific `true`/`false` differences no longer break imports.

Commits: `526a131`.

## 2026-08-28

- MySQL dump and restore are hardened: the restore connection is separate from the target database, only dumps with `USE` statements are accepted, database creation and drop DDL is rejected, and dump files publish atomically only after success and a non-empty check.
- Restore runs preflight and import through a mapped container with the Docker client fallback preserved; logs include stage, elapsed time, and byte counts.
- MySQL rotates passwords for accounts such as `user@%`, rejects partial wildcard hosts, and allows rotating the current management account after confirmation.

Commits: `68c5f13`, `36bf3b2`, `1f18547`.

## 2026-08-26

- New local install entry point manages the plugin lifecycle; PyInstaller produces a single-file executable with bundled defaults that local configuration can override.

Commits: `e8c518c`.

## 2026-08-25

- Project automation is standardized on `uv`-based Makefile targets for dependencies, installation, checks, tests, and builds; coverage stays opt-in locally while CI keeps the coverage gate.

Commits: `74d83c3`.

## 2026-08-24

- MySQL dump prefers a uniquely matched local container, keeps the temporary Docker client fallback, and removes temporary containers afterwards.
- `DBTALK_DSN_*` reads from the current directory `.env` when the process variable is absent, with process variables always taking precedence.

Commits: `437fc09`, `efc027c`.

## 2026-08-23

- New MySQL and PostgreSQL `schema list/create/drop`; dropping requires explicit confirmation, and PostgreSQL protects the current management database from deletion.
- New MySQL user and PostgreSQL role lifecycles with `readonly` and `readwrite` permission profiles, protecting password input, destructive operations, and the current management identity.
- New PostgreSQL logical backup: custom archive dump and restore preferring the native client, with a configurable Docker fallback, archive validation, cleanup controls, parallel restore, and portable owner defaults.
- Agent plugins unify into a single native package synchronized to Claude, Codex, and Grok.

Commits: `2e74768`, `25ba731`, `e3cdd2b`, `04841f9`.

## 2026-08-22

- `query`/`exec` add configurable statement timeouts; queries use a read-only session by default and writes must be enabled explicitly.
- The distribution, module, and command unify on `dbtalk`.

Commits: `52d7b94`, `6564e67`.

## 2026-08-20

- The first release provides native MySQL dump and restore with gzip and a Docker client fallback, plus JSONL export and import for SQLite and MySQL.
- Canonical DSNs unify `query`, `exec`, `export`, `import`, `dump`, and `restore` across SQLite, MySQL, and PostgreSQL.

Commits: `26665fa`, `eb95af0`.
