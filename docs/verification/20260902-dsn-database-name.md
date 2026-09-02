---
Review status: Accepted
Flow mode: standard
Stage: Verification
---

# 放宽 DSN 数据库名称要求验证
最后修改时间: 2026-09-02 13:08:08

## Requirement alignment

已对照已接受的 `docs/requirement/20260902-dsn-database-name.md` 验证以下行为：

- `parse_dsn` 对 MySQL、PostgreSQL 不再全局要求 URL database name，并继续保留 SQLite 资源路径、显式 driver、dialect 与端口校验。
- JSONL transfer 在命令边界要求 MySQL/PostgreSQL DSN 明确带有 database name，避免依赖 driver 默认库。
- MySQL/PostgreSQL dump 与 restore 的 target 只按 `--database > DSN database > 失败` 解析；连接身份只来自 DSN，不从配置回退。
- PostgreSQL database drop 在无 database path 的管理 DSN 上返回专用的维护库错误，避免安全比较被跳过。
- CLI help、用户文档和 `plugins/dbtalk` skill 已表达“URL 可省略 database，但具体命令可能要求明确 target”的约束，且未记录真实凭据。

## Plan alignment

standard 模式不创建单独的 Spec，本次按已接受的 Plan 核对。

- Plan 中的 DSN parser、transfer、MySQL、PostgreSQL、文档、skill 和测试文件均有对应实际变更。
- `src/dbtalk/postgres/database.py` 与 `tests/test_database_administration.py` 是 Plan 第 5 步要求审阅并补充的 PostgreSQL `schema drop` 局部安全校验，属于计划内增量。
- Plan 的真实服务验证要求已在现有 `.env` 的 `DBTALK_MYSQL_DSN`、`DBTALK_POSTGRES_DSN` 上完成；两个实际 DSN 都省略 URL database path。

## Actual diff summary

- `src/dbtalk/database/dsn.py` 允许 MySQL/PostgreSQL 的 `ParsedDsn.database` 为 `None`，SQLite 仍要求资源路径。
- `src/dbtalk/database/transfer.py` 将 database-name 前置条件收敛到 JSONL transfer。
- `src/dbtalk/mysql/cli.py`、`dump.py`、`restore.py` 将连接身份固定为 DSN，并以 `--database > DSN database > 失败` 解析 dump/restore target；MySQL dump 新增 `--database`。
- `src/dbtalk/postgres/cli.py`、`client.py` 为 dump/restore 新增 `--database`，并保证 native client URI、`.pgpass` 与输出命名使用同一最终 target。
- `src/dbtalk/postgres/database.py` 在 drop 前要求维护 database。
- 测试覆盖无库名 sync/async DSN、transfer 拒绝、MySQL/PostgreSQL target precedence、无 target 错误和 PostgreSQL drop 安全条件。
- `docs/database.md`、`docs/mysql.md`、`docs/postgres.md` 与三个相关 plugin skill 已同步公开契约。

## Expected vs actual changed files

Plan 预期的核心源码、测试、用户文档和 plugin skill 均已修改。`src/dbtalk/postgres/database.py`、`tests/test_database_administration.py` 是局部安全校验的计划内补充。

以下工作区改动不属于本任务，未纳入本 Verification 的通过结论：

- `Makefile`
- `scripts/version_calc.py`
- `tests/test_version_calc.py`
- 未跟踪的 `docs/requirement/20260902-mysql-connection-config-cleanup.md` 配置清理任务草稿

## Acceptance checklist

- [x] MySQL/PostgreSQL canonical DSN 可省略 database path；SQLite 空资源路径仍由测试拒绝。
- [x] 真实 MySQL 与 PostgreSQL 无库名 DSN 均通过 `dbtalk query --dsn-env ... --sql 'SELECT 1 AS reachable'` 返回 `reachable=1`。
- [x] 四条真实 CLI 路径 `mysql/postgresql export/import` 对无库名 DSN 均在 transfer 前置校验中返回 `JSONL database transfer requires a database name in the DSN`。
- [x] MySQL/PostgreSQL dump 与 restore 对无库名 DSN 且未传 `--database` 均返回非零的缺少 database target 错误；未执行 native client 或写入目标库。
- [x] MySQL dump、PostgreSQL dump、PostgreSQL restore 的 `--help` 均展示 `--database TARGET`，并说明默认使用 DSN database。
- [x] MySQL/PostgreSQL target precedence、配置不参与 target、最终 PostgreSQL target 在 native client 连接数据中的一致性由单元测试覆盖。
- [x] PostgreSQL `schema drop` 在无维护 database 的 DSN 上被局部安全校验拒绝。
- [x] 文档和 plugin skill 与源码语义一致，不回显真实 DSN 或密码。

## Test results

工作目录：`D:\SourceCodes\mywork\pomelo-dbtalk`

- `make check`：通过。Ruff format 检查 58 个文件，Ruff lint 通过，mypy 在 59 个 source files 中无问题。
- `make test`：通过，`237 passed, 1 skipped`。跳过项是默认未启用的 `tests/test_manual_integration.py`，原因是未设置 `DBTALK_RUN_INTEGRATION=1`；本次真实服务验证不依赖它。
- `uv run --locked --no-sync dbtalk mysql dump --help`、`uv run --locked --no-sync dbtalk postgres dump --help`、`uv run --locked --no-sync dbtalk postgres restore --help`：均通过，并显示 `--database TARGET`。
- `git diff --check HEAD`：通过。

## Real integration

使用当前目录 `.env` 中的 `DBTALK_MYSQL_DSN` 与 `DBTALK_POSTGRES_DSN`，命令通过 `--dsn-env` 加载；未在输出、日志或文档中回显 DSN。

- 两个 DSN 的 database path 都为空。`dbtalk query` 分别在 MySQL 与 PostgreSQL 上执行 `SELECT 1 AS reachable`，均返回 `reachable=1`。
- MySQL 与 PostgreSQL 的 `query` 走源码中的 read-only transaction；本次未执行 `exec --write`、import、restore、schema/user/role/permissions 管理或任何 DDL/DML。
- 对两个 DSN 分别执行 JSONL export/import 前置验证，四个命令均因缺少 database name 被拒绝；临时 input 文件在验证后已删除。
- 对两个 DSN 分别执行 dump/restore 的无 target 前置验证，四个命令均因缺少 database target 被拒绝；没有生成 dump 制品、调用 native client 或连接写入目标。
- 已有 database、schema、数据、user、role 和权限均未修改。

## Missed or expanded scope

未对现有服务执行携带 `--database` 的完整 dump 或 restore 成功路径：restore 会写入目标库，明确不符合本次“已有库和用户不可修改”的验证约束；完整 target precedence、native command 构造和 restore 行为由单元测试覆盖。JSONL import 成功路径同样未在共享库上执行。

本次没有扩展产品代码范围。真实集成验证使用了用户提供的两个 `.env` DSN；这比 Plan 中“服务可用时”的最低要求更具体，但仅执行只读和预置错误路径。

## Risks and incomplete items

- 无库名 DSN 的实际默认连接 database 仍由 MySQL、PostgreSQL driver 与服务端决定；dbtalk 不推导默认库。本次两个服务均接受该连接方式，但其他部署仍取决于其服务端配置。
- 未验证真实 native dump/restore 成功路径，以避免对已有库执行潜在的长时间读取或任何 restore 写入；其核心 target 与安全行为保留单元测试覆盖。
- 无关的 `Makefile`、版本计算和配置清理草稿仍在工作区，提交时应与本任务 diff 拆分。

## Conclusion

Requirement 与 Plan 的验收项已通过：无库名 MySQL/PostgreSQL DSN 可用于真实只读连接；需要明确 database 的 transfer、dump 与 restore 在命令边界给出专用失败；项目统一检查、全量测试、CLI help 和文档契约均通过。真实服务验证未修改任何已有 database 或用户。
