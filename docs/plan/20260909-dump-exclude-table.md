---
Review status: Accepted
Flow mode: standard
Stage: Plan
---

# dump `--exclude-table` 与备份脚本接入计划
最后修改时间: 2026-09-09 13:07:17

## Requirement basis

- 已接受的 Requirement：`docs/requirement/20260909-dump-exclude-table.md`。
- `mysql dump` 与 `postgres dump` 增加可重复 `--exclude-table NAME`；未指定时整库行为不变。
- 基于对象排除，映射 native：`mysqldump --ignore-table=<database>.<table>`、`pg_dump --exclude-table=<table>`。不使用 `--exclude-table-data`。
- 不查询目录确认表存在。只拒绝空名、空白名和控制字符；其余交给 native client。
- `scripts/backup-db.py` 读取每个 database 的 `exclude_tables`，传给 dump。`preflite_postgres.sub2api` 排除 `ops_system_logs` 与 `usage_logs`。
- 本地 client、mapped container、Docker fallback 使用同一组排除参数。
- 不新增 `--include-table`，不改 JSONL、restore CLI、dump 格式或 client 选择顺序。

## Design

### CLI 与 options

`dbtalk mysql dump` 与 `dbtalk postgres dump` 增加：

```text
--exclude-table NAME   # multiple=True
```

解析后得到 `tuple[str, ...]`。每项 `strip()` 后必须非空且不含 NUL/控制字符；否则 `click.ClickException` / dump 层失败，不启动 native client。不做 identifer 字符集收紧，以免挡住合法的 `schema.table`（PostgreSQL）或含 `$` 的 MySQL 名。不查表是否存在，不按 schema 展开，不把 `*` 当成 dbtalk glob。

`MysqlDumpOverrides` / `MysqlDumpOptions` 与 `PostgresDumpOptions` 增加 `exclude_tables: tuple[str, ...] = ()`。`resolve_dump_options` 校验名称后写入 options。三条执行路径都已经调用 `mysqldump_command_args` / `pg_dump_command_args`，只改这两个组参函数即可。

### Native 参数

MySQL，对每个排除名追加：

```text
--ignore-table=<target-database>.<name>
```

`<target-database>` 使用已解析的 dump 目标库（`--database > DSN database`）。调用方只写表名。名称里若已含 `.`，仍按「目标库.传入名」拼接，结果由 `mysqldump` 解释。

PostgreSQL，对每个排除名追加：

```text
--exclude-table=<name>
```

未限定 schema 时沿用 `pg_dump` 对未限定名的匹配。不在 dbtalk 里加 `public.`。

未指定排除时 argv 不含上述开关。排除名可出现在 dump 命令 argv（不是密钥）；密码仍只走 `MYSQL_PWD` / `PGPASSWORD` / `.pgpass`。

### backup 脚本

`BackupTarget` 增加 `exclude_tables: tuple[str, ...]`。`load_backup_config` 读取每个 database 的可选 `exclude_tables`：缺省或空列表为 `()`；必须是字符串列表，元素同样拒绝空白/控制字符。

`run_dump` 对每个排除名追加 `--exclude-table NAME`。日志里的 `dbtalk command=` 包含这些参数，输出路径仍只打文件名。

`scripts/backup-db.yaml`：

```yaml
    - name: sub2api
      enabled: true
      exclude_tables:
        - ops_system_logs
        - usage_logs
```

`scripts/backup-db.example.yaml` 用占位符展示该字段，不含真实 DSN。

### 文档

`docs/mysql.md`、`docs/postgres.md`、`plugins/dbtalk/skills/dbtalk-mysql/SKILL.md`、`plugins/dbtalk/skills/dbtalk-postgres/SKILL.md` 说明：dump `--exclude-table` 跳过表对象；与 JSONL 同名但走 native client；未知表不在 dbtalk 预检；restore 不会建回被排除表。

## Implementation steps

1. PostgreSQL dump 排除参数。
   - 修改 `src/dbtalk/postgres/dump.py`：`PostgresDumpOptions` 增加 `exclude_tables`；`resolve_dump_options` 接收并校验；`pg_dump_command_args` 追加 `--exclude-table=NAME`。
   - 修改 `src/dbtalk/postgres/cli.py`：可重复 `--exclude-table`，传入 resolver。
   - 修改 `tests/test_postgres.py`：无排除 argv 不变；单/多排除出现在 command vector；空白名失败且不调用 native。

2. MySQL dump 排除参数。
   - 修改 `src/dbtalk/mysql/dump.py`：`MysqlDumpOptions` / `MysqlDumpOverrides` 增加 `exclude_tables`；resolver 校验；`mysqldump_command_args` 追加 `--ignore-table=<database>.<name>`。
   - 修改 `src/dbtalk/mysql/cli.py`：可重复 `--exclude-table`。
   - 修改 `tests/test_mysql.py`：现有整库 command 断言仍不含 ignore-table；覆盖拼接、多表、空白名。

3. backup 脚本与 YAML。
   - 修改 `scripts/backup-db.py`：解析 `exclude_tables`，`run_dump` 传 `--exclude-table`。
   - 修改 `scripts/backup-db.yaml`：`preflite_postgres.sub2api` 排除两张大表。
   - 修改 `scripts/backup-db.example.yaml`：示例字段。
   - 修改 `tests/test_backup_db.py`：配置加载与 dump 命令包含排除表；未配置时不传该参数。

4. 活文档与 skill。
   - 修改 `docs/mysql.md`、`docs/postgres.md`、两个 dump skill。

## Verification plan

- `make check`
- `make test`，至少覆盖 `tests/test_mysql.py`、`tests/test_postgres.py`、`tests/test_backup_db.py`
- 用 `--help` 确认 mysql/postgres dump 列出 `--exclude-table`
- 不在本计划要求对远端 `sub2api` 做真实 dump；真实备份由用户在实现后决定是否重跑

## Blockers

无。

## Assumptions

- `pg_dump --exclude-table=ops_system_logs` 足以匹配 `public.ops_system_logs`，无需在 YAML 写 schema 限定名。
- `mysqldump --ignore-table` 对不存在的 `database.table` 不导致失败；拼写错误时该表仍会被 dump，这是已接受风险。

## Risks

- restore 后缺表，依赖 migration 或应用容错。
- YAML 表名写错时 native 可能静默忽略，大表仍会 COPY。
- PostgreSQL 未限定名若匹配多个 schema 中的同名表，会被全部排除；当前目标库只有 `public`。

## Rollback

删除 `--exclude-table` 相关参数与 YAML 字段，dump 恢复整库。无数据迁移，无兼容层。

## User review notes

- 2026-09-09：Requirement 已按对象排除、不预检表存在进入 Plan。
