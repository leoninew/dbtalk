---
Review status: Accepted
Flow mode: standard
Stage: Verification
---

# dump `--exclude-table` 与备份脚本接入验证
最后修改时间: 2026-09-09 13:25:30

## Requirement alignment / 需求对齐

已对照已接受的 `docs/requirement/20260909-dump-exclude-table.md` 核对：

- `dbtalk mysql dump` 与 `dbtalk postgres dump` 均提供可重复 `--exclude-table`；未指定时 native argv 不含排除开关。
- PostgreSQL 传递 `pg_dump --exclude-table=NAME`；MySQL 传递 `mysqldump --ignore-table=<目标库>.NAME`。排除基于表对象（无 DDL、无数据），不使用 `--exclude-table-data`。
- 不查询目录确认表存在。空名、空白名和控制字符在启动 native client 前失败；未知表名原样交给 native client。
- `scripts/backup-db.py` 读取每个 database 的可选 `exclude_tables`，调用 dump 时逐个传 `--exclude-table`。
- 本地 `scripts/backup-db.yaml` 的 `preflite_postgres.sub2api` 排除 `ops_system_logs` 与 `usage_logs`。该文件被 `scripts/.gitignore` 忽略，不进仓库；`scripts/backup-db.example.yaml` 用占位符展示该字段。
- `docs/mysql.md`、`docs/postgres.md` 与 mysql/postgres dump skill 已说明 dump 排除语义、与 JSONL 同名不同路径、restore 不重建被排除表。

## Spec alignment / 规格对齐

不适用。本次为 standard / 标准模式，未创建独立 Spec。

## Plan alignment / 计划对齐

已对照已接受的 `docs/plan/20260909-dump-exclude-table.md` 核对：

- `PostgresDumpOptions` / `resolve_dump_options` / `pg_dump_command_args` 与 postgres dump CLI 已接入 `exclude_tables`。
- `MysqlDumpOptions` / `MysqlDumpOverrides` / `mysqldump_command_args` / Docker fallback 的 `container_options` 与 mysql dump CLI 已接入同一组排除参数。
- `BackupTarget.exclude_tables`、YAML 解析与 `run_dump` 传参已落地。
- 单元测试覆盖无排除 argv 不变、多表排除、空白名失败、MySQL `database.table` 拼接、YAML 加载与 dump 命令传参。
- 活文档与 skill 已按计划更新。Plan 未要求本验证阶段再对远端 `sub2api` 做一次 dump；用户在实现后自行重跑备份并确认成功。

## Actual diff summary / 实际差异摘要

- mysql/postgres dump 增加可重复 `--exclude-table`，分别映射 native `--ignore-table=<database>.<table>` 与 `--exclude-table=<table>`。
- 排除名只做空白/控制字符校验，不查表是否存在。
- backup 脚本按 database 读取 `exclude_tables` 并传给 dump；example YAML 给出字段示例。
- 测试、手册和 skill 同步该契约。

## Expected vs actual changed files / 预期与实际修改文件对比

Plan 预期的源码、测试、example YAML、活文档和 skill 均已修改：

- `src/dbtalk/postgres/dump.py`、`src/dbtalk/postgres/cli.py`
- `src/dbtalk/mysql/dump.py`、`src/dbtalk/mysql/cli.py`
- `scripts/backup-db.py`、`scripts/backup-db.example.yaml`
- `tests/test_postgres.py`、`tests/test_mysql.py`、`tests/test_backup_db.py`
- `docs/mysql.md`、`docs/postgres.md`
- `plugins/dbtalk/skills/dbtalk-mysql/SKILL.md`、`plugins/dbtalk/skills/dbtalk-postgres/SKILL.md`

实际额外文件：

- SpecFlow 过程文档 `docs/requirement|plan|verification/20260909-dump-exclude-table.md`：不改变产品行为。
- 本地 `scripts/backup-db.yaml`：含真实 DSN，被 gitignore；`preflite_postgres.sub2api` 的排除列表只存在于本机配置。

工作区未纳入本任务：

- `backup.sql`：根目录未跟踪文件，与本次 dump 排除无关。

## Acceptance criteria checklist / 验收标准检查清单

- [x] `dbtalk mysql dump --help` 与 `dbtalk postgres dump --help` 列出 `--exclude-table`。
- [x] 未指定排除时，既有整库 dump command 断言不含 `--ignore-table` / `--exclude-table`。
- [x] PostgreSQL 多排除生成 `--exclude-table=ops_system_logs` 与 `--exclude-table=usage_logs`。
- [x] MySQL 多排除生成 `--ignore-table=example.ops_system_logs` 与 `--ignore-table=example.usage_logs`。
- [x] 空白/控制字符排除名在 resolver 失败，不进入 native client。
- [x] backup YAML 可解析 `exclude_tables`；`run_dump` 把名称作为重复 `--exclude-table` 传入。
- [x] example YAML 展示 `exclude_tables` 占位字段，不含真实 DSN。
- [x] 活文档与 skill 说明对象排除、不预检未知表、restore 不重建被排除表。
- [x] 用户确认已执行备份且无问题。`data/20260909-033213/backup-manifest.md`：成功 7、失败 0；`preflite_postgres-sub2api.dump` 为 Succeeded，4,107,363 bytes（前一日同库整库 dump 为 15,595,556 bytes）。

## Test / command results / 测试 / 命令结果

工作目录：`D:\SourceCodes\mywork\pomelo-dbtalk`

当前 Windows 环境按 Makefile 目标等价执行：

- `uv run --locked --no-sync ruff format --check src tests scripts`：通过，58 files already formatted。
- `uv run --locked --no-sync ruff check src tests scripts`：通过。
- `uv run --locked --no-sync mypy src tests scripts`：通过，59 source files。
- `uv run --locked --no-sync pytest tests/test_mysql.py tests/test_postgres.py tests/test_backup_db.py tests/test_mysql_logging.py`：86 passed。
- `uv run --locked --no-sync pytest`：258 passed。
- `uv run --locked --no-sync dbtalk mysql dump --help`：列出 `--exclude-table`。
- `uv run --locked --no-sync dbtalk postgres dump --help`：列出 `--exclude-table`。

真实 `sub2api` dump 由用户在实现后执行；本验证根据用户声明与 `data/20260909-033213/backup-manifest.md` 记录结果，未再发起一次远端 dump。

## Missed or expanded scope / 范围偏差

未发现产品范围扩大。JSONL `--exclude-table`、restore CLI、dump 格式和 client 选择顺序未改。未新增 `--include-table`。

## Risks / 风险

- restore 用该 archive 时没有 `ops_system_logs` / `usage_logs`，依赖 migration 或应用容错。
- `scripts/backup-db.yaml` 被 gitignore，其他机器不会自动带上这两张表的排除列表，需按 example 自行配置。
- 未知排除名不在 dbtalk 预检；拼写错误时 native 可能静默忽略，大表仍会被 COPY。

## Incomplete items / 未完成项

无。用户已确认备份成功。

## Conclusion / 结论

需求与计划范围内的 dump `--exclude-table`、backup YAML 接入和文档同步已完成。单元测试、lint/typecheck 与 help 通过。用户重跑备份成功，`sub2api` dump 体积从约 15.6 MB 降到约 4.1 MB，与排除两张大表一致。可以交付。
