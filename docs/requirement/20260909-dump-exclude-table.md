---
Review status: Accepted
Flow mode: standard
Stage: Requirement
---

# dump `--exclude-table` 与备份脚本接入
最后修改时间: 2026-09-09 13:04:34

## Background

`dbtalk mysql dump` 与 `dbtalk postgres dump` 目前只能整库导出。JSONL `export/import` 已有可重复的 `--exclude-table`，但那是表数据搬运，不能生成 MySQL `.sql`/`.sql.gz` 或 PostgreSQL custom `.dump`，也接不进 `scripts/backup-db.py`。

`preflite_postgres` 上的 `sub2api` 走本机 `127.0.0.1:5433` SSH 隧道。整库 dump 在 `COPY public.ops_system_logs` 时因长连接被掐掉失败。当前该库约 243 MB，其中 `ops_system_logs` 153 MB、`usage_logs` 41 MB。备份需要跳过这两张大表，同时让 MySQL/PostgreSQL dump CLI 具备对称的排除能力。

原生客户端已有对应开关：`mysqldump --ignore-table=database.table`、`pg_dump --exclude-table=pattern`。dbtalk 尚未露出。

## Goal

1. `dbtalk mysql dump` 与 `dbtalk postgres dump` 增加可重复的 `--exclude-table NAME`。未指定时行为与现在相同：整库 dump。
2. 排除项交给对应 native client，不在 dbtalk 里重写 dump SQL 或 archive。本地 client、mapped container、Docker fallback 三条路径都传递同一组排除参数。
3. `scripts/backup-db.py` 从 YAML 读取每个 database 的排除表，并传给 `dbtalk <engine> dump`。
4. 在 `scripts/backup-db.yaml` 的 `preflite_postgres.sub2api` 排除 `ops_system_logs` 与 `usage_logs`；example YAML 给出字段示例。
5. 同步 CLI help、`docs/mysql.md`、`docs/postgres.md`、dbtalk mysql/postgres skill，以及 backup 脚本相关测试。

## Non-goal

- 不新增 dump `--include-table`。
- 不给 JSONL `export/import` 改语义或别名。
- 不改变 dump 格式、压缩、目标库解析、`--skip-definer`、restore CLI，或 mapped container / Docker fallback 的选择顺序。
- 不在本任务实现行级 WHERE、按时间清理日志表、增量备份或远端物理备份。
- 不把排除做成 dbtalk 全局配置；连接与排除仍由每次 dump 命令或 backup YAML 提供。
- 不为 `--exclude-table` 增加 glob/`*` 模式 API；名称按精确表名处理。
- 不在 restore 时自动建回被排除的表。
- 不查询 `information_schema` / `pg_class` 判断排除表是否存在；未知名称交给 native client 处理。

## User scenarios

1. 运维执行 `dbtalk postgres dump --dsn-env DBTALK_DSN_APP --exclude-table ops_system_logs --exclude-table usage_logs`，得到的 custom archive 不含这两张表；其余对象仍按现有整库 dump 语义进入 archive。
2. 运维对 MySQL 使用同样的 `--exclude-table` 重复参数；`mysqldump` 收到带目标库前缀的 `--ignore-table`。
3. 未传 `--exclude-table` 时，mysql/postgres dump 与当前整库行为一致。
4. `scripts/backup-db.py backup` 读取 YAML 中 `preflite_postgres` / `sub2api` 的排除表，调用 postgres dump 时带上这两张表；其他未配置排除的 database 仍整库备份。
5. 排除表名为空、只含空白或含控制字符时，dump 在启动 native client 前失败。表是否存在不预检；未知名称原样传给 native client，由其成功或报错。

## Acceptance

- `dbtalk mysql dump --help` 与 `dbtalk postgres dump --help` 展示可重复的 `--exclude-table`。
- 未指定排除时，native 命令行不含 `--ignore-table` / `--exclude-table`，现有 dump 测试与行为保持。
- 指定排除时：PostgreSQL 为每个名称传递 `pg_dump --exclude-table=NAME`；MySQL 为每个名称传递 `mysqldump --ignore-table=<target-database>.NAME`。名称不得出现在 argv 以外的密码或 DSN 中。
- mapped container 与 Docker fallback 与本地 client 使用同一组排除参数。
- 空字符串、只含空白、含 NUL/控制字符的排除名在调用 native client 前失败。不查询目录确认表存在；未知表名原样交给 `mysqldump` / `pg_dump`，失败时使用现有 native 错误路径。
- `scripts/backup-db.py` 为每个 database 解析可选 `exclude_tables` 字符串列表；缺省视为空。调用 dump 时按列表逐个传 `--exclude-table`。
- `scripts/backup-db.yaml` 中 `preflite_postgres` 的 `sub2api` 排除 `ops_system_logs` 和 `usage_logs`。`scripts/backup-db.example.yaml` 展示该字段，不含真实 DSN。
- 单元测试覆盖：无排除、单排除、多排除、空白/控制字符名称、MySQL `database.table` 拼接、不因未知表名在 dbtalk 层失败、backup 脚本把 YAML 排除传给 dump 命令。不把真实密码写入断言。
- 活文档与 skill 说明 dump 排除语义、与 JSONL `--exclude-table` 的差异（dump 走 native client，restore 不会重建被排除表），以及 backup YAML 字段。
- 通过项目入口 `make check` 与针对 dump/backup 的 `make test` 相关用例。

## Open questions

暂无需要用户确认的未决事项。

## Decisions

- 命令名使用 `--exclude-table`，与 JSONL 一致；不引入 `--ignore-table` CLI 别名。
- 基于对象排除：跳过该表的 DDL 与数据。不使用 `pg_dump --exclude-table-data`，也不为 MySQL 做两趟 schema-only dump。
- 不预检表是否存在。dbtalk 只拒绝空名、空白名和控制字符；其余名称原样交给 native client，成功或失败由其决定。
- MySQL 由 dbtalk 用 dump 目标库名拼接 `--ignore-table=<database>.<table>`，调用方只写表名。
- PostgreSQL 传递 `--exclude-table=<table>`；调用方写 `ops_system_logs` 这种未限定 schema 的表名时，按 `pg_dump` 对未限定名的匹配处理。
- backup YAML 字段为每个 database 下的 `exclude_tables` 列表，不是全局 dump 配置。
- 不在本任务做 `--include-table`、行过滤或 JSONL 变更。
- 项目处于活跃开发期，不保留旧 dump CLI 兼容层。

## Risk

- 跳过表对象后，用该 archive restore 的库没有 `ops_system_logs` / `usage_logs`。若应用启动时假定表已存在且不会跑 migration，restore 后的环境会缺表。
- SSH 隧道上其余表仍可能慢，但去掉 153 MB + 41 MB 后风险显著下降。
- 若选择 native 对未知排除名静默成功，YAML 拼写错误会再次打到大表。

## User review notes

- 2026-09-09：确认基于对象排除（无 DDL、无数据）。
- 2026-09-09：确认不检查表是否存在；参数交给 native client，由其响应。
