# 授权与权限管理验证
最后修改时间: 2026-09-01 22:41:50

Review status: Accepted
Flow mode: standard
Stage: Verification

## Requirement alignment

- 已实现 `readonly`、`readwrite`、`migrator` 三个固定 profile，权限包含关系为 `migrator > readwrite > readonly`。
- `readonly` 只查询；`readwrite` 提供常规 DML，PostgreSQL schema 目标额外提供 sequence `USAGE, SELECT, UPDATE`；`migrator` 追加 DDL 和建库能力。
- MySQL 的 `migrator` 追加全局 `CREATE ON *.*`；PostgreSQL 的 `migrator` 设置 role 全局 `CREATEDB`，撤销时设为 `NOCREATEDB`。
- `grant` / `revoke` 同时支持 profile 与可重复的 `--privilege`；两种模式互斥，细粒度权限交由数据库服务端校验。
- 授权和撤销要求明确 DSN、主体和 `--yes`；资源省略时使用 DSN database。
- MySQL 与 PostgreSQL 均提供 `permissions list/show`，支持主体及 database/schema 筛选并展示原生权限结果。
- 方言 `database` 命令已迁移为 `schema`；根级通用 `query`、`exec`、`export`、`import` 已提升，旧命令不再注册。

## Plan alignment

计划中的权限模型、权限查询、schema 命令迁移、根命令扁平化、测试和文档步骤均已完成。最新的 profile 收敛和建库映射已同步至 Requirement、Plan、手册和插件 Skill。实现保留了 `src/dbtalk/database/` 作为内部实现包，但没有对外注册根级 `database` 命令，符合计划约束。

## Actual diff summary

- 将 MySQL/PostgreSQL 固定 profile 收敛为 `readonly`、`readwrite`、`migrator`，并锁定 `migrator` 的 DDL、DML、建库和撤销映射。
- MySQL `migrator` 在目标库授权外增加并撤销全局 `CREATE ON *.*`；PostgreSQL `migrator` 增加并撤销 role `CREATEDB`。
- 新增 `src/dbtalk/mysql/permissions.py` 与 `src/dbtalk/postgres/permissions.py`。
- 修正 PostgreSQL `acldefault` 的 `"char"` 类型转换，兼容实际 PostgreSQL 服务端。
- 修正 MySQL `permissions show` 的账号参数绑定，支持 `%` host 且避免 PyMySQL 格式化冲突。
- 将 MySQL/PostgreSQL 生命周期命令改名为 `schema`，并将通用命令提升到根级。
- 更新 README、手册、插件 Skill、测试和 Specflow 过程文档。
- 修复既有 `tests/test_backup_db.py` 的 Ruff 格式和动态导入模块的 Mypy 类型标注问题，使项目质量门禁可执行。

## Expected vs actual files

计划文件范围内的源代码、测试、文档和 Skill 均已覆盖。实际额外变更仅为修复同一质量门禁中发现的既有 `tests/test_backup_db.py` 格式/类型问题；未修改其业务行为或测试断言语义。

## Acceptance checklist

- [x] 三个 profile 可用，且 `migrator > readwrite > readonly`。
- [x] `readonly` 查询、`readwrite` 常规 DML（PostgreSQL 含 sequence 使用）、`migrator` DDL/DML/建库均经单元和真实服务验证。
- [x] `--profile` 与 `--privilege` 互斥；`--privilege` 支持重复传入。
- [x] DSN、主体和 `--yes` 的授权边界及资源缺省行为已覆盖。
- [x] 撤销使用请求的权限集合；MySQL 回收全局 `CREATE`，PostgreSQL 回收 `CREATEDB`，profile 重叠语义已在计划风险中记录。
- [x] MySQL/PostgreSQL `permissions list/show` 已实现原生权限查询和筛选。
- [x] 根级及方言旧 `database` 命令均未注册，`schema` 命令可用。
- [x] 输出、日志和错误不包含连接密码；MySQL `%` host 查询已验证。
- [x] 文档和插件 Skill 已同步命令、profile、权限和安全约束。

## Test results

- `make check`：通过（Ruff format、Ruff check、Mypy）。
- `make test`：通过，`226 passed, 1 skipped`；跳过项为显式 opt-in 的手工 dump 集成测试。
- 变更文件定向 `ruff format --check`：通过。
- `git diff --check`：通过。
- 本地 MySQL 管理 DSN：使用临时随机 database 与三个 account 验证 `readonly` 查询、`readwrite` 写入、`migrator` DDL/DML/建库；每个 profile 撤销后对应访问被拒绝。`migrator` 对管理员既有表执行 `GRANT` / `REVOKE` 均以非零失败。
- 本地 PostgreSQL 管理 DSN：使用临时随机 database 与三个 role 验证 `readonly` 查询、`readwrite` 使用 identity sequence 写入、`migrator` DDL/DML/建库；撤销后新建表和建库均被拒绝。对管理员拥有的既有表，migrator 的 `GRANT` 不会使目标 role 获得读取权，`REVOKE` 不会移除管理员授予的读取权。
- 集成检查未向现有业务库写入数据。

## Missed or expanded scope

无功能性遗漏。按用户要求额外处理了既有备份测试文件的格式和类型检查问题，使 `make check` 完整通过。

## Risks and incomplete items

- profile 权限在数据库侧以直接原生授权表示，数据库无法区分同一权限来自哪个 profile；撤销重叠 profile 可能影响仍依赖该权限的主体，风险已在 requirement/plan 中明确。
- PostgreSQL `CREATEDB` 是 role 全局属性，不是单个 database grant；MySQL 建库所需的 `CREATE ON *.*` 同样是实例级权限，文档已说明其方言差异。
- PostgreSQL profile 不修改 default privileges，也不提供 table、sequence、function 独立资源参数；更细粒度需求仍需审核后使用通用 `exec`。
- 手工集成测试文件仍默认跳过，需设置 `DBTALK_RUN_INTEGRATION=1` 才运行既有 dump 场景；本次权限集成已单独完成。

## Conclusion

实现与已接受的 Requirement、Plan 对齐，验收标准全部满足；质量检查、单元测试、命令面检查和 MySQL/PostgreSQL 本地权限集成检查均通过。当前没有未完成的实现项。
