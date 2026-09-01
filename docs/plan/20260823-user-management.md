# 用户管理与常规授权计划
最后修改时间: 2026-09-01 22:41:50

---
Review status: Accepted
Flow mode: standard
Stage: Plan
---

## 后续设计说明

本计划记录的是 2026-08-23 首版实现所采用的双 profile 映射，不追溯修改当时的实施或验证结论。当前 profile 设计以 [授权与权限管理计划](20260901-authorization-grant-revoke.md) 为准：`readonly`、`readwrite`、`migrator`。

`migrator` 包含 DDL、DML 与建库能力，不添加 `GRANT OPTION` 或角色管理能力。MySQL 使用全局 `CREATE ON *.*` 支持建库；PostgreSQL 使用 role 全局 `CREATEDB` 属性，撤销时设为 `NOCREATEDB`。

## Requirement basis

本计划实现已接受的用户管理 Requirement：在 `dbtalk mysql` 下提供 `user`、`grant` 与 `revoke`，在 `dbtalk postgres` 下提供 `role`、`grant` 与 `revoke`。账号生命周期与授权均使用受限、方言明确的命令，不通过通用 `database exec` 接收 SQL 文本。

standard 模式不创建独立 Spec。本计划固定首版接口、profile 映射和安全边界；用户已明确要求开始 Implementation，因此本计划为 `Accepted`。

## Design decisions

### CLI surface

- `dbtalk mysql user create|list|enable|disable|rotate-password|drop`
- `dbtalk mysql grant|revoke`
- `dbtalk postgres role create|list|enable|disable|rotate-password|drop`
- `dbtalk postgres grant|revoke`

每个命令严格要求 `--dsn` 与 `--dsn-env` 二选一。`create`、`grant` 与 `revoke` 也要求显式目标和资源；`enable`、`disable`、`rotate-password`、`drop`、`grant`、`revoke` 均要求 `--yes`。密码只通过 `--password-env NAME` 引用，不能作为命令参数值。

### Structured identifiers and resources

- MySQL user 由 `--user` 与 `--host` 标识。host 仅允许 `localhost`、单个 DNS 名称、IPv4 或 IPv6，拒绝 `%`、`_` 及其他通配符。
- PostgreSQL role 由 `--role` 标识。
- MySQL 授权必须提供 `--database`，目标为该数据库的全部对象；不提供全局、表级或列级资源。
- PostgreSQL 授权必须且只能提供 `--database` 或 `--schema`。schema profile 可作用于该 schema 当前的表与序列，但 CLI 不提供单独的 table/sequence/function 目标。
- 方言标识符通过 SQLAlchemy 当前方言的 `identifier_preparer` 引用；MySQL account 的 user/host 和密码使用 DBAPI 绑定值。PostgreSQL password DDL 使用已固定的 psycopg 驱动的 `sql.Identifier` / `sql.Literal` 安全组合，因为 PostgreSQL utility statement 不接受 DBAPI 参数占位符；不自行拼接未转义的 SQL 字面量。

### Profile mapping

Profile 名称固定为 `read-only` 与 `read-write`，不暴露任意 privilege。撤销使用与授予相同的映射。

| 方言与范围 | `read-only` | `read-write` |
| --- | --- | --- |
| MySQL database | `SELECT, SHOW VIEW` on `database.*` | `SELECT, SHOW VIEW, INSERT, UPDATE, DELETE` on `database.*` |
| PostgreSQL database | `CONNECT` | `CONNECT, TEMPORARY` |
| PostgreSQL schema | `USAGE` plus `SELECT` on all existing tables | `USAGE` plus table `SELECT, INSERT, UPDATE, DELETE` and sequence `USAGE, SELECT, UPDATE` on all existing objects |

PostgreSQL schema profile 不修改 default privileges，不自动覆盖未来创建的表或序列；文档必须说明该限制。`WITH GRANT OPTION`、DDL 权限、role membership、`PUBLIC` 目标和高危系统权限均不实现。

### Execution and safety

- 新建专用用户管理适配层，使用同步 SQLAlchemy engine 的 `AUTOCOMMIT` 连接执行 DDL/DCL，不复用通用 query/exec 的事务承诺。
- PostgreSQL role 创建和密码轮换经 psycopg 直接执行已组合的 password DDL，避免 SQLAlchemy 将密码绑定值编译为 PostgreSQL 不支持的 utility-statement `$1` 参数；psycopg 与 SQLAlchemy 异常均映射为无凭据和无密码的稳定错误。
- 在连接前校验 DSN 方言、命令参数、标识符、profile 和 `--password-env` 名称；环境变量缺失或为空时失败。
- 写操作前查询 `CURRENT_USER()`：MySQL 比较规范化的 `user@host`，PostgreSQL 比较 role 名称；不允许对当前管理身份执行启用、禁用、轮换密码、删除、授予或撤销。
- 捕获 SQLAlchemy 异常并映射为稳定、无凭据和无密码的 `DatabaseOperationError`。正常输出仅含方言、操作、目标与资源，不输出 secret 或密码哈希。

## Implementation steps

1. 新增方言内聚的领域适配和 Click 命令。
   - 新建 `src/dbtalk/mysql/user.py`，封装 MySQL DSN 解析、环境密码读取、user/host/profile/resource 校验、account 生命周期、数据库 profile 授权、当前主体保护、错误映射及 Click commands。
   - 新建 `src/dbtalk/postgres/role.py`，封装 PostgreSQL role 生命周期、database/schema profile 授权、当前主体保护、错误映射及 Click commands。
   - 该布局与并发的 `mysql/database.py`、`postgres/database.py` 一致。两个模块分别使用方言引用；MySQL 使用 bound parameters，PostgreSQL password DDL 使用 psycopg SQL composition，不以通用抽象掩盖 MySQL `user@host` 与 PostgreSQL role 的语义差异。

2. 注册命令。
   - 在现有 `src/dbtalk/mysql/cli.py` 和 `src/dbtalk/postgres/cli.py` 中只追加 user/role、grant 与 revoke 的注册，保留并发的 database lifecycle group 改动。

3. 覆盖安全与方言行为。
   - 新建 `tests/test_user_management.py`，mock SQLAlchemy engine/connection，验证 profile SQL、MySQL 参数绑定、PostgreSQL password literal composition、标识符引用、host/profile/resource 拒绝、secret 不泄露、`--yes` 前置、当前主体保护、列表字段、错误映射和 Click help。
   - 扩展 root/方言 CLI 测试，确保新命令与 dump、restore 和并发 database group 共存。

4. 更新文档与插件 metadata。
   - 更新 README、各方言手册和 Codex skill，让用户明确 user/role lifecycle、profile、资源范围、环境变量注入密码、当前主体保护、`--yes` 和 PostgreSQL existing-object 限制。
   - 如 plugin manifest 或包描述声明可用命令，则同步更新。

## Files to change

- `src/dbtalk/mysql/user.py`（新增）
- `src/dbtalk/postgres/role.py`（新增）
- `src/dbtalk/mysql/cli.py`
- `src/dbtalk/postgres/cli.py`
- `tests/test_user_management.py`（新增）
- `tests/test_cli.py`（如需要）
- `README.md`
- `docs/mysql.md`
- `docs/postgres.md`
- `docs/codex.md`
- `plugins/dbtalk/skills/mysql/SKILL.md`
- `plugins/dbtalk/skills/postgres/SKILL.md`
- `plugins/dbtalk/.codex-plugin/plugin.json`（如需要）

## Verification plan

1. 运行用户管理专项单元测试，以及受影响的现有 CLI、MySQL、PostgreSQL 和 database administration 测试。
2. 运行项目全量 pytest、Ruff、Mypy 与 `git diff --check`。
3. 在具备显式测试环境与权限时，单独执行 MySQL 和 PostgreSQL 真实服务集成；PostgreSQL 集成必须覆盖带密码的 role 创建和轮换，以及 password literal 的安全转义。否则记录为未执行，不能用 mock 测试替代真实权限语义。
4. 复查命令帮助、错误输出和日志，确认 password/password hash/完整 DSN 未出现。

## Risks and rollback

- profile 会在既有对象上改变权限，`revoke` 可能中断运行应用；命令要求 `--yes`，但不提供自动补偿。
- MySQL account host、认证插件和 PostgreSQL role/对象 owner 的服务器语义存在差异；失败必须显式暴露为操作失败，不以通用事务回滚掩盖。
- 与并发的 database lifecycle 改动共享 MySQL/PostgreSQL CLI 文件；本功能只追加注册，避免重构或覆盖该任务的模块。
- 回滚时移除 user-management module、命令注册、文档和测试即可；已创建的账号、role 与授权属于目标数据库状态，不会自动撤销。

## User review notes

- 用户将流程切换为 standard，并明确要求开始 Implementation。
- 真实 PostgreSQL 18 验证发现 utility statement 不支持 password 的 DBAPI 参数绑定；用户要求修复该问题，因此 Plan 更新为使用 psycopg 的安全 SQL composition，CLI 契约与权限边界不变。
