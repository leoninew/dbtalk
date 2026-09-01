# 数据库管理验证
最后修改时间: 2026-08-23 15:27:52

Review status: Accepted
Flow mode: strict
Stage: Verification

## Requirement alignment

已验证 MySQL 与 PostgreSQL 的数据库生命周期管理保持方言分离：

- `dbtalk mysql database` 和 `dbtalk postgres database` 各自提供 `list`、`create`、`drop`，不新增 `admin` 根命令，也不混入 `database query/exec`。
- `create` / `drop` 只接受结构化 `--name`；名称在连接前拒绝空白、NUL 和控制字符，并使用各方言的 identifier preparer 引用。
- `drop` 需要 `--yes`，缺失时在解析管理 DSN 前退出；公开 help 不包含 transaction mode、isolation level 或 `--autocommit` 选项。
- PostgreSQL 阻止删除管理 DSN 当前连接的 database；操作方必须连接到其他维护库。
- 错误映射、CLI 输出和测试断言均不回显 DSN 密码；文档和 Codex skills 以 `--dsn-env` 为优先示例。

## Spec and plan alignment

已补齐并接受[数据库管理规格](../spec/20260823-database-management.md)，其设计决策与已接受的 Plan 和实际实现一致：

- 数据库管理位于 `src/dbtalk/mysql/database.py` 与 `src/dbtalk/postgres/database.py`，分别拥有方言 SQL、生命周期和 Click `database` subgroup；未保留共享 admin 服务或命令工厂。
- 内部实现采用适合 PostgreSQL 数据库 DDL 的连接执行方式，但该细节没有成为 CLI、手册或 skill 的公开参数和操作要求。
- PostgreSQL 删除当前连接 database 的拒绝、未实现主动终止其他连接、默认创建属性和 `--yes` 删除门卫均与 Plan 一致。

## Actual diff summary

- 新增 `src/dbtalk/mysql/database.py` 与 `src/dbtalk/postgres/database.py`，实现各自独立的 list/create/drop、DSN 方言校验、标识符验证、安全引用、错误映射和资源释放。
- 在 `src/dbtalk/mysql/cli.py` 与 `src/dbtalk/postgres/cli.py` 分别注册方言内的 `database` subgroup。
- 新增 `tests/test_database_administration.py`，覆盖两种方言的 SQL、标识符引用、连接释放、错误脱敏、PostgreSQL 维护库限制、`--yes` 前置检查和公开 help。
- 更新 README、MySQL/PostgreSQL 手册、Codex 文档及两个方言 skill，说明管理命令、DSN、`--yes`、PostgreSQL 维护库约束与职责边界。

## Expected vs actual files

Plan 预期的 MySQL/PostgreSQL database 模块、方言 CLI 注册、数据库管理测试、README、两份手册、Codex 文档和两个 skill 均已修改或新增。`tests/test_cli.py` 无需修改，因为专项 CLI 合同测试已覆盖新增命令。

当前工作区还包含另一会话并行创建的 MySQL user、PostgreSQL role、grant/revoke、其过程文档和测试。这些变更不属于数据库管理需求，未纳入本次 diff 对齐或验证结论。

## Acceptance checklist

- [x] 两种方言均有独立的 `database list/create/drop` 命令。
- [x] 不存在 `admin` 根命令、共享管理 CLI 工厂或 `database exec` 管理模式。
- [x] `--name` 是结构化数据库标识符，控制字符和空白名称在连接前被拒绝，名称按方言引用。
- [x] PostgreSQL 建库和删库可在真实服务中执行，且公开 CLI 不要求用户配置或理解内部连接执行机制。
- [x] PostgreSQL 不能删除管理 DSN 正在连接的 database。
- [x] `drop` 无 `--yes` 时不解析 DSN 或建立连接；成功路径不使用 `rows affected` 作为结果。
- [x] DSN 二选一、方言不匹配和错误脱敏均有自动化测试。
- [x] 面向用户的帮助、手册和 skills 仅暴露管理动作、DSN、名称和删除确认。

## Test results

| Command or action | Result |
| --- | --- |
| `uv run pytest --no-cov tests/test_database_administration.py tests/test_cli.py` | 24 passed。 |
| `uv run ruff check`（数据库管理文件） | Passed。 |
| `uv run ruff format --check`（数据库管理文件） | 5 files already formatted。 |
| `uv run mypy`（数据库管理模块与测试） | 3 source files，无类型问题。 |
| `git diff --check` | Passed。 |
| 实施期间 `uv run pytest` | 138 passed, 1 skipped；总覆盖率 90.2%。 |

当前 Windows 环境没有 `make`，因此以 Makefile 中定义的等价 `uv run` 检查完成自动验证。全量 pytest 在并行 user/role 源文件出现前完成；本阶段仅对数据库管理范围执行当前工作区的专项回归。

## PostgreSQL integration

在用户授权的本机 PostgreSQL 实例上完成真实生命周期验证。提供的连接仅在当前 shell 进程环境变量中使用，并按项目 canonical DSN 约定以 `postgresql+psycopg://` driver 形式连接；验证输出、文档和日志未记录凭据。

1. 使用指向既有 `k12_force` database 的管理连接执行 `dbtalk postgres database list`，确认连接和数据库列表命令成功。
2. 创建随机临时数据库 `dbtalk_verify_20260823151014_233cb57f`；未对 `k12_force` 的 schema 或数据执行写入。
3. 再次执行 `list`，确认临时数据库存在。
4. 使用 `dbtalk postgres database drop --yes` 删除该临时数据库；命令成功，清理确认完成。

## Risks and incomplete items

- 未提供隔离 MySQL 管理实例，因此 MySQL 的真实 `create/list/drop` 集成验证未执行；其方言 SQL 与安全边界由单元测试覆盖。
- PostgreSQL 删除遇到其他连接、权限或托管服务策略时会按服务端语义失败；首版不会主动终止会话。
- 数据库删除不可逆。真实验证仅创建和删除带随机后缀的临时库，未操作用户现有 database。
- 并行的 user/role/grant 功能需由其所属会话独立完成测试与 Verification，不能与本功能合并判断。

## Conclusion

数据库管理需求、计划和实际 diff 对齐。自动检查、公开 CLI 契约以及用户授权 PostgreSQL 实例上的 create/list/drop/cleanup 均通过；没有阻止数据库管理功能交付的问题。MySQL 真实实例验证保留为后续可选验证项。
