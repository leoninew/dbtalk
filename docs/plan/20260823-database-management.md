# 数据库管理计划
最后修改时间: 2026-08-23 15:27:52

Review status: Accepted
Flow mode: strict
Stage: Plan

## Requirement basis

本计划落实已接受的[数据库管理需求](../requirement/20260823-database-management.md)与[数据库管理规格](../spec/20260823-database-management.md)：为 MySQL 与 PostgreSQL 增加数据库列出、创建和删除命令。管理能力必须与通用 `database query/exec` 隔离，使用方言根命令、结构化标识符、内部 autocommit 和 `--yes` 删除确认。

规格文档补齐了严格模式所需的设计审查记录；本计划固定实现接口、方言差异和验证策略，并与实际实现保持一致。

## Plan assumptions

1. 公共命令形态固定为以下命令；不新增 `dbtalk admin` 根命令，也不向 `database exec` 添加 autocommit 或管理模式参数。

   ```text
   dbtalk mysql database list   --dsn | --dsn-env
   dbtalk mysql database create --dsn | --dsn-env --name NAME
   dbtalk mysql database drop   --dsn | --dsn-env --name NAME --yes

   dbtalk postgres database list   --dsn | --dsn-env
   dbtalk postgres database create --dsn | --dsn-env --name NAME
   dbtalk postgres database drop   --dsn | --dsn-env --name NAME --yes
   ```

2. 首版创建数据库时只使用各方言的默认创建属性；不提供 MySQL charset/collation、PostgreSQL owner/template/encoding/locale 参数，也不提供 `IF [NOT] EXISTS`、强制删除或多语句执行。
3. 所有管理命令复用 canonical SQLAlchemy DSN 的 `--dsn`/`--dsn-env` 严格二选一契约。MySQL DSN 可指向任意已有管理库；PostgreSQL DSN 必须指向维护库或其他非目标数据库，通常为 `postgres`。
4. `--name` 仅表达一个数据库标识符：实现拒绝空白、NUL 和控制字符，并通过连接方言的 identifier preparer 引用，不把名称作为 SQL bind parameter 或原始 SQL 片段。数据库自身继续负责长度、保留字和服务端版本限制的判定。
5. `drop` 是唯一首版需要 `--yes` 的数据库生命周期动作。未提供该 flag 时，CLI 在解析与校验阶段失败，不创建数据库连接或执行写 SQL。创建与列出不要求确认。
6. PostgreSQL 首版在存在其他活动连接或权限不足时直接返回受控错误；不实现 `--terminate-connections`。这保留安全边界并避免静默中断生产连接。
7. `AUTOCOMMIT`、isolation level 与事务启动方式仅属于实现和测试层。公开 CLI、`--help`、README、手册和 Codex skill 不出现对应选项或要求用户理解其语义。

## Implementation steps

1. 在方言包内建立数据库生命周期服务。
   - 新增 `src/dbtalk/mysql/database.py` 与 `src/dbtalk/postgres/database.py`，分别拥有命令、DSN 解析、方言核验、数据库名称验证、安全引用、engine 生命周期和非敏感错误映射；不复用面向 DML 的 `DatabaseClient.execute`，也不创建共享 admin 服务或 Click 工厂。
   - 两个模块各自定义 `list_databases`、`create_database` 与 `drop_database`，返回数据库名称集合或完成结果；服务不接受调用方提供的 SQL 文本。
   - 两个模块仅复用 `parse_dsn` / `dsn_from_environment` 与现有 `DatabaseOperationError`。它们各自拒绝 SQLite、异步 DSN 和不匹配的方言 DSN；错误消息只描述操作类别与非敏感原因，不包含 URL、密码或原始 DBAPI 异常。

2. 为管理 DDL 设计明确的方言执行路径。
   - 每个操作创建短生命周期 SQLAlchemy engine，并在连接层显式使用 `AUTOCOMMIT` isolation level；无论成功、SQL 失败或参数失败都 dispose engine。这样 PostgreSQL 的 `CREATE/DROP DATABASE` 不会进入事务块，MySQL DDL 也保留其原生隐式提交语义。
   - 使用 connection dialect 的 `identifier_preparer.quote` 构造单个、已验证的数据库标识符；值参数和标识符不混用。复用 `sqlalchemy_transfer._quote_identifier` 的安全原则，但不依赖该私有 transfer helper。
   - MySQL 使用方言专用的列出、创建和删除 SQL；PostgreSQL 使用 `pg_database` catalog 读取非模板、可连接的数据库，并使用其对应的创建和删除 SQL。两种方言的列表均按名称稳定排序，CLI 统一渲染为单列 `database` 表格。
   - 在 PostgreSQL 删除前，将目标名称与管理 DSN 的 `ParsedDsn.database` 比较；相同则在建立 DDL 会话前拒绝并提示连接维护库。创建、删除成功输出动作和数据库名，不输出 `rows affected`。

3. 将生命周期服务挂接到现有方言根命令。
   - 在 `src/dbtalk/mysql/cli.py` 和 `src/dbtalk/postgres/cli.py` 中分别注册各自方言包提供的 `database` Click subgroup；每个方言模块自行实现 `list`、`create`、`drop` 适配、`--yes` 门卫检查和安全结果输出。
   - 每个子命令独立声明 `--dsn`、`--dsn-env`；`create` / `drop` 声明必填 `--name`，`drop` 声明 `--yes` flag。帮助文本只说明管理权限、PostgreSQL 维护库限制、删除确认和动作结果，不暴露事务或 driver 实现细节。
   - 保持现有 `dbtalk mysql dump/restore`、`dbtalk postgres dump/restore` 与根命令注册不变。用户管理功能未来会向相同根命令增加不同 subgroup；本功能只拥有 `database` subgroup，避免定义 user/role/grant 命令或共享其领域模型。

4. 增加数据库管理的单元与 CLI 合同测试。
   - 新增 `tests/test_database_administration.py`，以 fake/mocked SQLAlchemy engine 或 connection 测试 MySQL/PostgreSQL 的 list/create/drop SQL、autocommit 设置、标识符引用、异常映射和 dispose 清理。
   - 覆盖 DSN 二选一、方言不匹配、缺失环境变量、空白/NUL/控制字符名称、名称中的引用字符，以及所有错误输出不含 DSN 或密码。
   - 用 `CliRunner` 验证六个公开命令的 help、参数、稳定结果输出和无 `--yes` 时的前置失败；断言失败路径不实例化管理服务。覆盖 PostgreSQL 删除当前 DSN database 的拒绝分支。
   - 按需扩展 `tests/test_cli.py` 的根命令与 subgroup help 断言；不修改现有 MySQL/PostgreSQL backup 或 generic query/exec 的行为断言。

5. 更新用户文档与 Codex skill。
   - 更新 `README.md` 的命令概览和管理示例，默认使用 `--dsn-env`，并明确示例账号需要实例级权限。
   - 更新 `docs/mysql.md` 与 `docs/postgres.md`，分别说明命令、管理 DSN、对象语义、PostgreSQL 维护库要求、`--yes` 与服务端错误；不把 schema、account 或事务/driver 实现细节写成用户需要操作的内容。
   - 更新 `docs/codex.md`、`plugins/dbtalk/skills/mysql/SKILL.md` 与 `plugins/dbtalk/skills/postgres/SKILL.md`，让自动化调用方先列出、再创建或以 `--yes` 删除，并避免在命令参数中传入 DSN 密码。

## Files to change

- `src/dbtalk/mysql/database.py`（新增）
- `src/dbtalk/postgres/database.py`（新增）
- `src/dbtalk/mysql/cli.py`
- `src/dbtalk/postgres/cli.py`
- `tests/test_database_administration.py`（新增）
- `tests/test_cli.py`（按需）
- `README.md`
- `docs/mysql.md`
- `docs/postgres.md`
- `docs/codex.md`
- `plugins/dbtalk/skills/mysql/SKILL.md`
- `plugins/dbtalk/skills/postgres/SKILL.md`

## Verification plan

1. 运行六个 `--help` 命令，核对方言根命令、`--dsn`/`--dsn-env`、`--name`、`--yes` 和危险操作文案，且不存在 `--autocommit`、isolation level 或事务模式等内部实现选项。
2. 运行 `uv run pytest tests/test_database_administration.py tests/test_cli.py`，确认 SQL 构造、autocommit、DSN/名称边界、删除门卫和脱敏契约。
3. 运行项目约定的 `make check`、`make test` 与 `git diff --check`，确认类型、lint、覆盖率和既有 query/exec、MySQL/PostgreSQL backup/restore 没有回归。
4. 仅当用户提供分别指向隔离 MySQL 与 PostgreSQL 测试实例的可写管理员 DSN 时，创建带随机后缀的临时数据库、列出并核对、使用 `--yes` 删除。PostgreSQL 管理 DSN 必须连接 `postgres` 或其他维护库；测试必须在 finally 清理临时库。未提供环境时将真实集成验证记录为未执行，不伪造结果。

## Blockers and risks

- 当前没有已授权的 MySQL/PostgreSQL 管理测试 DSN；SQLAlchemy mock 测试不能替代真实服务器对 DDL、权限和 catalog 可见性的验证。
- PostgreSQL `DROP DATABASE` 会因其他连接、复制或权限策略失败；首版不终止连接，错误提示必须指导管理员使用另一个维护连接处理。
- MySQL 与 PostgreSQL 的数据库名称长度、字符集和保留字约束不同。实现应安全引用并映射数据库错误，而不是维护一套可能滞后的跨方言 allowlist。
- `src/dbtalk/mysql/cli.py` 与 `src/dbtalk/postgres/cli.py` 是用户管理功能同样会扩展的共享注册点。实施前需与该功能协调 imports 和 Click subgroup 注册，避免覆盖对方修改。

## Rollback

本功能仅新增管理服务、方言子命令、测试和文档，不修改既有数据或配置。若需撤回，移除 `database` subgroup 注册、对应服务与文档即可；已经由用户成功创建或删除的数据库遵循数据库服务的原生语义，不由 dbtalk 自动恢复。

## User review notes

- 用户确认数据库生命周期与查询、更新操作分离，并使用方言根命令。
- 用户要求高危操作使用 `--yes`；数据库管理首版仅将其用于删除。
- 用户在另一会话推进用户管理；本计划不包含 user、role、password 或 grant/revoke 的实现。
- 用户明确要求从 Requirement 直接进入 Plan；后续补齐严格模式所需的 Spec，未决事项与风险保留到实现前处理。
- 用户明确要求进入 Implementation；本计划视为已接受。
- 用户要求 MySQL 与 PostgreSQL 数据库管理按方言包分离，不引入统一 admin 命令或共享管理适配层。
