# Batch database backups

Configure connections and databases in `scripts/backup-db.yaml`, using
`scripts/backup-db.example.yaml` as the template. Relative `output_directory`
values resolve from the configuration file's directory; `../data` selects the
repository's `data/` directory. Only databases with `enabled: true` are backed up.

Each connection must also have `enabled: true` to be processed by `backup`,
`test`, or `sync`. Connections with `enabled: false` or no `enabled` field are
skipped by all three commands. Database-level `enabled` controls backups only;
both the connection and database must be enabled for a database to be backed up.
`sync` preserves the database configuration of skipped connections.

Run from the repository root:

```bash
bash scripts/backup.sh backup
bash scripts/backup.sh backup --continue-on-error
```

Backups are grouped by connection name and database. Each run uses a local
timestamp in `YYYYMMDD-HHMMSS` format:

```text
data/
  leon_postgres_casdoor/
    20261001-150000.dump
    20261002-150000.dump
  think_mysql_gitea/
    20261001-150000.sql.gz
    20261002-150000.sql.gz
  manifests/
    20261001-150000.md
    20261002-150000.md
```

Database directories use `<name>_<database>`, where `name` is the configured
connection name. PostgreSQL uses custom `.dump` archives; MySQL uses gzip SQL
`.sql.gz` files. Existing backups are preserved. Runs sharing the same second
append a sequence such as `-01` to the timestamp across backups and the manifest.

Manifests list successful, reused, and failed backups, with backup paths relative
to the manifest directory. DSN credentials are omitted.

The completion log includes skipped databases, so `targets` equals
`succeeded + failed + skipped`. Databases disabled by either the connection or
database setting count as skipped; resumed backups that are reused count as
successful. For example:

```text
backup run completed targets=16 succeeded=10 failed=0 skipped=6
```

Resume a run using its timestamp from the logs or manifest filename:

```bash
bash scripts/backup.sh backup --resume 20261001-150000 --continue-on-error
```

Resume reuses non-empty backups for that timestamp, retries empty or missing
files, and rewrites the same manifest. It reads the current configuration.
