# 用户管理与常规授权验证
最后修改时间: 2026-08-23 15:30:31

Review status: Accepted
Flow mode: standard
Stage: Verification

## Requirement alignment

实现提供了方言分离的 MySQL account 与 PostgreSQL login role 生命周期命令，以及同级的固定 profile `grant` / `revoke` 命令。密码入口仅为 `--password-env`，高危动作均要求 `--yes`，并拒绝修改当前管理身份。PostgreSQL 的资源范围限制为 database 或 schema，schema profile 仅作用于当前对象。

真实 PostgreSQL 18.6 验证确认：`role create` 与 `role rotate-password` 均可使用 `--password-env` 设置密码，且包含单引号和反斜杠的密码能安全完成创建、轮换和实际登录。

## Spec and plan alignment

standard 模式没有独立 Spec。实际模块布局、命令注册、固定 profile、标识符验证、DSN 二选一、错误脱敏和当前管理身份保护均与 Accepted Plan 一致。

初次验证发现 PostgreSQL 不接受在 `CREATE ROLE ... PASSWORD` 或 `ALTER ROLE ... PASSWORD` utility statement 中的 DBAPI 参数占位符。实现已改为通过已固定的 psycopg 驱动安全组合 `sql.Identifier` 和 `sql.Literal`，直接执行 password DDL；PostgreSQL 18.6 实例验证了该组合的创建、轮换、特殊字符转义和认证语义。CLI、环境变量密码入口、脱敏与权限边界保持不变。

## Actual diff summary

- 新增 `src/dbtalk/mysql/user.py` 与 `src/dbtalk/postgres/role.py`，分别实现账号/role 生命周期、固定 profile 授权、输入校验、当前身份保护、错误映射和非敏感列表输出。
- PostgreSQL password DDL 通过 psycopg SQL composition 执行，并将 psycopg 与 SQLAlchemy 异常统一映射为不含凭据和密码的 `DatabaseOperationError`；不再向 utility statement 传递 DBAPI 参数占位符。
- 在方言 CLI 注册 `mysql user`、`postgres role` 以及两个方言根下的 `grant`、`revoke`。
- 新增 `tests/test_user_management.py`，覆盖 CLI 契约、profile SQL、标识符与 host 校验、环境变量密码入口、`--yes`、当前身份保护、错误脱敏和 help。
- 更新 README、MySQL/PostgreSQL 手册、Codex 文档、两个 skill 与 plugin manifest，说明命令边界、profile、安全确认和 PostgreSQL schema 的 existing-object 限制。

## Expected vs actual files

Plan 中预期的 user/role 模块、方言 CLI 注册、专项测试、README、两份手册、Codex 文档、方言 skill 和 plugin manifest 均已新增或修改。`tests/test_cli.py` 未修改，因为专项测试覆盖了新增 Click 命令。

工作区还包含并发数据库生命周期管理的模块、测试与过程文档；它们不属于本次用户管理验证结论。共享的 README、方言手册、Codex 文档与 CLI 文件同时包含两项功能的追加改动，已按功能边界分别核对。

## Acceptance checklist

- [x] MySQL account 与 PostgreSQL role 均使用独立、方言明确的 CLI，授权命令与生命周期命令同级。
- [x] 授权只接受结构化 database/schema 资源和 `read-only` / `read-write` profile，不接受原始权限 SQL。
- [x] `--dsn` / `--dsn-env` 严格二选一；密码只从有效且非空的 `--password-env` 读取，错误输出不回显密码。
- [x] `enable`、`disable`、`drop`、`grant`、`revoke` 需要 `--yes`，且 PostgreSQL 拒绝修改当前管理 role。
- [x] PostgreSQL database 与 schema 的 `read-write` profile 在真实服务中完成了授予、实际读写与撤销验证。
- [x] PostgreSQL role 列表、禁用、启用和删除在真实服务中通过验证。
- [x] PostgreSQL 在真实 18.6 服务上创建具有密码的 login role，并可使用该密码登录。
- [x] PostgreSQL 在真实 18.6 服务上轮换包含单引号和反斜杠的密码；旧密码失效，新密码可登录。
- [ ] MySQL 真实账户/权限集成验证尚未执行。

## Test results

| Command or action | Result |
| --- | --- |
| `uv run pytest` | 165 passed, 1 skipped；总覆盖率 90.04%。 |
| `uv run ruff check .` | Passed。 |
| `uv run ruff format --check .` | 88 files already formatted。 |
| `uv run mypy src tests` | Success: no issues found in 57 source files。 |
| `git diff --check` | Passed。 |
| `dbtalk postgres role --help`、`dbtalk postgres grant --help` | Passed，公开参数与受限命令面符合计划。 |

## PostgreSQL 18 integration

在用户授权的本机 PostgreSQL 实例的 `k12_force` database 上执行验证。连接凭据仅在当前 shell 进程的环境变量中使用，验证输出、过程文档和代码均未记录密码或完整 DSN。管理身份为 `postgres`，实际确认其拥有 `SUPERUSER` 与 `CREATEROLE`，因此具备测试所需权限。

1. 实例版本为 PostgreSQL `18.6`。验证驱动预先创建唯一临时 schema、表和 identity sequence，随后由 `dbtalk postgres role create --password-env` 创建唯一临时 role；创建密码同时含有单引号和反斜杠。
2. 以新建 role 的原始密码实际连接 `k12_force` 成功；查询 `pg_roles` 确认其具备 `LOGIN`，且没有 superuser、createdb、createrole、replication 或 bypassrls 属性。
3. 真实 CLI 的 `role list`、无 `--yes` 的拒绝、database `read-write` grant/revoke、schema `read-write` grant/revoke、`role disable`、`role enable`、当前管理 role 保护和 `role drop` 均通过。
4. 被授予 schema profile 的临时 role 成功以自身密码连接并读取表、插入数据及使用 identity sequence；撤销后 `USAGE`、表 DML/SELECT 和 sequence 权限均为 false。
5. `role rotate-password` 使用另一组包含单引号和反斜杠的密码成功执行；原密码认证失败，新密码认证成功。
6. 验证结束后临时 schema（含表和数据）及临时 role 均已删除；未修改既有 role、schema 或业务数据。

## Risks and incomplete items

- PostgreSQL password DDL 现在依赖项目已支持的 psycopg driver；未来若更换驱动或改用异步连接，必须重新验证 utility statement、password literal 和错误脱敏语义。
- 单元测试覆盖 psycopg SQL composition、特殊字符转义和底层 driver connection 缺失的防御路径；本次真实 PostgreSQL 18.6 验证覆盖了创建、轮换与认证语义。
- 未提供 MySQL 管理实例，MySQL 的真实 create/rotate/grant/revoke 仍只有单元测试覆盖。
- PostgreSQL schema profile 不修改 default privileges，未来对象不会自动继承授权；这是已接受的首版限制。
- `revoke` 和密码轮换会影响运行中的应用连接，且不提供自动补偿。

## Conclusion

需求、计划和实际 diff 对齐。自动化回归、静态检查以及 PostgreSQL 18.6 的完整 role 生命周期与授权集成均通过，包含特殊字符密码的创建、轮换与认证。用户管理功能满足 PostgreSQL 验收条件；MySQL 真实实例验证仍为未执行项，不影响本次 PostgreSQL 结论。
