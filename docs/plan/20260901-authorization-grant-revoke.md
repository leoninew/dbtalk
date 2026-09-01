# 授权与权限管理计划
最后修改时间: 2026-09-01 22:41:50

Review status: Accepted
Flow mode: standard
Stage: Plan

## Requirement basis

本计划实现已接受的 [授权与权限管理需求](../requirement/20260901-authorization-grant-revoke.md)。需求已确认：

- 权限统一由 `grant` / `revoke` 处理，主体生命周期由 `user` / `role` 处理。
- profile 固定为 `readonly`、`readwrite`、`migrator`，按 `migrator > readwrite > readonly` 包含；`migrator` 包含 DDL、DML 和建库能力，但不添加 `GRANT OPTION` 或角色管理能力。
- 细粒度 `--privilege` 不由 dbtalk allowlist 预先限制，由数据库服务端校验；`--profile` 与 `--privilege` 互斥。
- 授权/撤销必须提供明确 DSN 和目标主体；目标 schema/database 可省略，省略时使用当前 DSN 指向的资源。
- 增加统一的 `permissions list/show`，直接展示 MySQL/PostgreSQL 原生权限查询结果。
- MySQL 与 PostgreSQL 的 `database` 命令改为 `schema`，旧方言 `database` 命令移除；权限仍由 `grant/revoke` 管理。
- 根级通用 `database` 命令组移除，其 `query`、`exec`、`export`、`import` 子命令提升为一级命令。

## Design decisions

### CLI surface

- `dbtalk mysql schema list|create|drop`
- `dbtalk postgres schema list|create|drop`
- `dbtalk mysql user ...`
- `dbtalk postgres role ...`
- `dbtalk mysql grant|revoke`
- `dbtalk postgres grant|revoke`
- `dbtalk mysql permissions list|show`
- `dbtalk postgres permissions list|show`

- `dbtalk query`
- `dbtalk exec`
- `dbtalk export`
- `dbtalk import`

根级 `query`、`exec`、`export`、`import` 用于通用 SQL 和 JSONL 传输；不承担常规权限授权。根级 `database` 命令组移除。方言根命令下也不再注册 `database` 生命周期命令。

### Grant/revoke parameters

两种方言的授权和撤销均要求一个明确 DSN、目标主体和 `--yes`。profile 与 privilege 二选一：

```text
--profile readonly|readwrite|migrator
或
--privilege NAME（可重复）
```

资源参数可选；省略时使用当前 DSN 的 database/schema。PostgreSQL 保留 `--role` 以及可选的 `--database` / `--schema`；MySQL 保留 `--user`、`--host` 以及可选的 `--database`。

细粒度 privilege 以单项结构化参数传入，由工具按方言安全引用主体和资源并生成原生授权语句；不接受完整 SQL、逗号分隔 privilege 字符串或 SQL 片段。数据库拒绝未知或无权 privilege 时，映射为不泄露凭据的稳定错误。

### Profile mapping

实现以权限集合包含关系为准：`readonly` 是基础集合，`readwrite` 在其上增加常规 DML，`migrator` 在其上增加 DDL 和建库能力。具体原生映射按方言实现并测试锁定：

- MySQL：数据库对象授权使用 `database.*`；`readonly` 为 `SELECT, SHOW VIEW`，`readwrite` 追加 `INSERT, UPDATE, DELETE`，`migrator` 追加目标库 DDL，并额外执行全局 `CREATE ON *.*` 以允许建库。MySQL 不将建库与对象创建分离，该权限是实例级能力。
- PostgreSQL：database/schema profile 使用对应的 `CONNECT`、schema `USAGE`、DDL `CREATE`、现有表/序列 DML 权限；`migrator` 额外执行 `ALTER ROLE ... CREATEDB`，撤销时执行 `NOCREATEDB`。`CREATEDB` 是 role 全局属性，不是普通 database/schema grant。

三个 profile 都不授予 `GRANT OPTION` 或角色管理能力。

Profile 的撤销只撤销该 profile 映射的权限；不得因为撤销较小 profile 而删除由更大 profile 或独立 privilege 授予的权限。若数据库无法区分权限来源，Plan 实施时必须选择可审计的直接映射策略并记录残留/重叠语义。

### Permissions list/show

新增独立的 `permissions` 命令组，不放在 `grant` 或 `revoke` 下：

- `permissions list`：默认展示当前 DSN 可见的全部原生权限；支持可选主体与 schema/database 筛选。
- `permissions show`：要求 `--role` 或 `--user`，资源筛选可选；展示该主体在当前 DSN 可见范围内的原生权限。

查询直接调用 MySQL `SHOW GRANTS` 或 PostgreSQL `information_schema` / `pg_catalog` 权限查询。输出保留原生列和格式，不强行统一字段；错误摘要和日志不得泄露 DSN 密码或其他凭据。

### Schema command migration and root command flattening

将 `src/dbtalk/mysql/database.py`、`src/dbtalk/postgres/database.py` 的公开 Click group 改为 `schema`，并同步模块、导出符号、帮助文本、测试和文档。删除 MySQL/PostgreSQL 方言根命令下旧的 `database` 注册。同时拆除根级通用 `database` Click group，将其四个子命令直接注册到根 CLI；内部 `src/dbtalk/database/` 包可继续保留作为实现模块。

## Implementation steps

1. 扩展权限领域模型和 SQL 生成。
   - 为 MySQL 与 PostgreSQL 增加 profile 枚举、包含关系和 `--privilege` 重复参数解析。
   - 允许资源缺省并从 DSN 推导当前 database/schema。
   - 生成 grant/revoke 原生语句，保留主体/资源安全引用和 `--yes` 前置确认。
   - 处理 profile 重叠、撤销范围及 PostgreSQL `CREATEDB` / MySQL 建库权限映射。

2. 增加权限查询命令。
   - 新增方言权限查询模块（建议 `src/dbtalk/mysql/permissions.py`、`src/dbtalk/postgres/permissions.py`）。
   - 实现 `permissions list/show` 的 Click 参数、原生查询、可选筛选和原样输出。
   - 在方言 CLI 注册 `permissions`，补充稳定的非敏感错误映射。

3. 迁移 schema 管理命令。
   - 将 MySQL/PostgreSQL 方言 database lifecycle group 重命名为 `schema`，并移除旧方言 `database` 命令注册。
   - 将通用 `query`、`exec`、`export`、`import` 从 `database` group 提升为根级命令，移除根级 `database` group。
   - 更新内部导入、测试名称、帮助文本和命令输出；保留 `src/dbtalk/database/` 内部实现包。

4. 更新测试。
    - 扩展 `tests/test_user_management.py`：三个 profile 的方言映射、MySQL 全局 `CREATE`、PostgreSQL `CREATEDB`/`NOCREATEDB`、细粒度 privilege 透传、资源缺省、模式互斥、撤销范围和数据库错误。
   - 新增权限查询测试：list/show 参数、筛选、原生结果透传、凭据脱敏和错误映射。
    - 更新 `tests/test_database_administration.py`、`tests/test_cli.py`、数据库 transfer/operation 测试及相关帮助断言，验证根级 `query/exec/export/import`、`schema` 命令和旧方言/root `database` 命令移除。

5. 更新文档与插件 Skill。
    - 更新 README、`docs/mysql.md`、`docs/postgres.md`、`docs/codex.md` 和 dbtalk skills 的命令列表、profile 层级、细粒度 privilege、权限查询、schema 命令迁移和 DSN/确认要求。
   - 明确根级 `exec` 不是常规授权入口；超出权限命令专门语义的特殊 SQL 仍需管理员审核。

## Files to change

- `src/dbtalk/mysql/user.py`
- `src/dbtalk/postgres/role.py`
- `src/dbtalk/mysql/permissions.py`（新增）
- `src/dbtalk/postgres/permissions.py`（新增）
- `src/dbtalk/mysql/database.py`（重命名或迁移为 `schema.py`）
- `src/dbtalk/postgres/database.py`（重命名或迁移为 `schema.py`）
- `src/dbtalk/mysql/cli.py`
- `src/dbtalk/postgres/cli.py`
- `src/dbtalk/cli.py`
- `src/dbtalk/database/cli.py`
- `src/dbtalk/commands/database.py`
- `tests/test_user_management.py`
- `tests/test_database_administration.py`
- `tests/test_cli.py`
- `tests/test_database_transfer_contract.py`
- `tests/test_database_operations.py`
- `README.md`
- `docs/mysql.md`
- `docs/postgres.md`
- `docs/codex.md`
- `plugins/dbtalk/skills/dbtalk-mysql/SKILL.md`
- `plugins/dbtalk/skills/dbtalk-postgres/SKILL.md`

## Verification plan

1. 运行权限和命令迁移专项测试，再运行完整 `pytest`。
2. 使用项目入口运行 `make check`，覆盖 Ruff、Mypy；执行 `git diff --check` 检查文档和源码空白。
3. 在显式准备的 MySQL/PostgreSQL 环境中验证三个 profile 的原生权限、`migrator` 建库和撤销后的失败、资源缺省、细粒度 privilege 服务端校验，以及 `permissions list/show` 的原生输出；未具备环境时如实记录未执行。
4. 复查所有帮助、日志和错误输出，确认没有完整 DSN、密码或密码哈希；确认根级 `query/exec/export/import` 可用，且 `dbtalk database`、`dbtalk mysql database`、`dbtalk postgres database` 均已移除。

## Risks and rollback

- PostgreSQL `CREATEDB` 是 role 属性而非普通 database/schema grant；将其纳入 `migrator` 会影响主体全局建库能力，撤销 `migrator` 会直接设为 `NOCREATEDB`。
- MySQL 创建 database 需要全局 `CREATE ON *.*`，其影响超出目标 database；不能将该 profile 用作普通应用账号。
- 细粒度 privilege 不由 dbtalk allowlist 限制，管理 DSN 的权限决定实际风险；错误输入可能触发数据库拒绝或高权限授权，必须保留 `--yes` 和非原始 SQL 参数边界。
- profile 叠加后的撤销可能与数据库无法区分授权来源冲突；需要测试并记录直接撤销的实际语义。
- 方言 `database`→`schema` 是破坏性 CLI 变更；回滚需恢复旧命令注册和文档，同时不自动回滚已执行的授权或 schema 变更。

## Blockers and assumptions

- 假设用户接受移除 `dbtalk database`、`dbtalk mysql database` 与 `dbtalk postgres database`，将通用操作提升为根级命令。
- 假设 `migrator` 的实例级建库能力适合受控迁移账号；普通应用使用 `readwrite`。
- 当前没有实现阻塞项；上述建库权限语义是高风险实现假设，应在真实数据库验证前确认。

## User review notes

- 用户确认进入下一阶段；本计划已进入 Implementation。
- 本计划根据 Requirement 中已接受的 profile 层级、细粒度权限、schema 命令迁移和 permissions 查询方案编制。
- 用户确认将根级 `dbtalk database` 的 `query`、`exec`、`export`、`import` 提升为一级命令，并移除 `database` 命令组；内部实现包可保留。
- 用户明确要求开始 Implementation；本轮完成计划范围内的代码、测试与文档改动后停在实施阶段，等待后续 Verification 指令。
- Implementation 已完成代码、测试、文档和本地只读集成检查；尚未进入 Verification 阶段。
- 用户明确要求完成 Specflow Verification；已完成验证并生成对应 verification 文档。
- 用户最终将 profile 收敛为 `readonly`、`readwrite`、`migrator`，并明确 `migrator` 需要建库能力且不添加 `GRANT OPTION`。
