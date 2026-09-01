# 数据库管理
最后修改时间: 2026-08-23 15:27:52

Review status: Accepted
Flow mode: strict
Stage: Requirement

## Background

当前 `dbtalk database query` 与 `dbtalk database exec --write` 面向既有数据库中的单条 SQL 操作。`exec` 的写路径使用事务；这不适用于 PostgreSQL 的 `CREATE DATABASE` 和 `DROP DATABASE`，因为它们不能在事务块内执行。MySQL 的对应 DDL 会隐式提交，继续将其作为通用 `exec` 的隐含能力也会使数据库生命周期操作与日常查询、数据更新混在同一入口。

项目需要为 MySQL 和 PostgreSQL 的数据库生命周期操作提供可审查、方言明确且不暴露任意管理 SQL 的命令边界。

## Goal

1. 为 MySQL 与 PostgreSQL 提供列出、创建和删除数据库的第一方 CLI 管理能力。
2. 将数据库生命周期命令与 `database query`、`database exec`、JSONL transfer 以及 MySQL dump/restore 明确隔离，不引入 `exec --autocommit` 或其他将实例管理混入通用 SQL 执行的参数。
3. 按数据库方言在内部采用正确的执行模式；特别是 PostgreSQL 建库、删库不在事务块内执行。该实现细节不构成用户可配置、帮助文本或操作手册中的公开概念。
4. 通过验证和按方言引用的数据库标识符执行操作，避免调用者传入原始管理 SQL。
5. 延续统一 DSN 入口和凭据保护约定：命令接受 `--dsn` 或 `--dsn-env`，文档以 `--dsn-env` 为首选，且不在输出、日志或错误中暴露密码。

## Non-goal

- 不启动、停止、安装、升级或配置 MySQL/PostgreSQL 服务实例。
- 不管理表、schema、迁移、索引或应用内 DDL。
- 不提供任意 DDL/admin SQL 执行器、`exec --autocommit` 或多语句脚本入口。
- 不取代既有 `dbtalk mysql dump/restore` 或 JSONL export/import。
- 不在本需求中创建、删除、轮换数据库账号或授予账号权限；该范围由独立的用户管理需求负责。
- 首版不承诺删除前终止 PostgreSQL 活动连接、复制模板库、指定 MySQL charset/collation，或指定 PostgreSQL owner/encoding/locale 等高级创建选项。

## User scenarios

1. MySQL 管理员通过管理 DSN 列出实例中可见的数据库，并创建名为 `app_db` 的空数据库。
2. PostgreSQL 管理员使用连接到维护库（如 `postgres`）的管理 DSN 创建或删除另一个数据库，不必配置或理解连接事务模式。
3. 管理员删除数据库时，命令要求显式提供 `--yes`；连接错误、权限不足或数据库仍在使用时命令以非零状态退出，并保持错误信息不含凭据。
4. 自动化脚本通过 `--dsn-env` 执行已审核的创建操作，或通过 `--dsn-env --yes` 执行已审核的删除操作，而不把密码或原始 SQL 放入进程参数。

## Acceptance

- [x] CLI 提供与现有 `database query/exec` 职责分离的 MySQL、PostgreSQL 数据库管理命令层级；帮助文本清楚说明方言、管理权限和不可逆影响。
- [x] 两个方言均支持列出、创建、删除数据库；每次调用只执行一个明确的管理动作。
- [x] 创建和删除均接收独立的数据库名称参数；名称拒绝为空白、NUL、控制字符和不支持的标识符形式，内部按相应方言安全引用，不能借名称注入 SQL。
- [x] PostgreSQL 创建和删除数据库不在事务块内执行；MySQL 命令保留其原生 DDL 隐式提交语义，文档不承诺对失败操作的通用回滚。
- [x] PostgreSQL 删除目标库时，命令检测并拒绝管理 DSN 正在连接该目标库的情形，并提示使用其他维护库。
- [x] 删除命令必须要求 `--yes`，避免误删；未提供该参数时不得建立写操作。
- [x] `--dsn` 与 `--dsn-env` 保持严格二选一，继续复用已支持的 canonical MySQL/PostgreSQL SQLAlchemy DSN；帮助、错误、日志和成功输出均不泄露 DSN 密码。
- [x] 结果输出说明已完成的动作和数据库名；DDL 不以 `rows affected` 作为成功语义。
- [x] 单元测试覆盖命令参数、标识符验证、DSN 保护、确认失败、方言 SQL/执行模式与错误映射；真实 MySQL/PostgreSQL 集成测试通过显式环境条件启用。
- [x] README、数据库手册和 Codex database skill 记录新增命令、权限前提、PostgreSQL 维护库要求与 MySQL/PostgreSQL 的不可回滚边界。

## Open questions

1. 首版是否需要在 PostgreSQL 删除前提供显式 `--terminate-connections` 能力，还是先在存在活动连接时失败并由管理员自行处理？
2. 首版创建命令是否只创建默认数据库，还是需要最小范围的方言选项（MySQL charset/collation，PostgreSQL owner/template）？

## Decisions

- 数据库生命周期操作使用新命令，不向 `database exec` 增加 autocommit 或管理模式参数。
- 该需求的支持范围仅为 MySQL 与 PostgreSQL；SQLite 文件生命周期不纳入实例管理语义。
- 管理操作以结构化参数表达动作和标识符，不接受原始 DDL 文本。
- 高风险删除统一使用 `--yes` 作为非交互确认边界。
- CLI 按方言根命令组织：MySQL 管理命令位于 `dbtalk mysql`，PostgreSQL 管理命令位于 `dbtalk postgres`；不新增统一的 `dbtalk admin` 根命令。
- 公开 CLI、帮助和用户手册只描述管理动作、管理 DSN 前提、目标名称、`--yes` 与结果；不暴露或解释 autocommit、isolation level 等内部执行机制。
- MySQL 与 PostgreSQL 的数据库管理分别由其方言包拥有；不创建共享管理命令工厂或统一的 admin 领域层。

## Risk

- 数据库创建与删除要求实例级权限；不同托管服务可能限制 `CREATE DATABASE`、`DROP DATABASE` 或系统库访问。
- PostgreSQL 目标库上的现有连接会使删除失败；若未来加入断连能力，必须额外评估中断生产会话的风险。
- MySQL DDL 的隐式提交意味着网络中断或部分失败不能被本工具包装成原子回滚，应在文档与错误语义中明确。
- 管理 DSN 通常权限高，环境变量、CI secret、诊断日志和测试夹具必须继续避免泄露其凭据。

## User review notes

- 用户不认可通过新增参数把实例管理能力混入查询和更新入口；需求采用独立命令边界。
- 用户要求同时覆盖数据库管理与用户管理；二者按职责拆分为独立 Requirement 文档。
- 用户确认采用方言根命令，以保留 database、schema、instance 等方言对象模型差异。
- 用户明确要求进入 Plan 阶段；本需求视为已接受。
- 用户确认 MySQL 与 PostgreSQL 数据库管理不应混合；实现按方言包分离。
