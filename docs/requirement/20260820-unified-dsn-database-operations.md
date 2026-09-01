# 统一 DSN 与通用数据库操作
最后修改时间: 2026-08-22 21:21:46

---
Review status: Accepted
Flow mode: standard
Stage: Requirement
---

## Background

当前 `dbtalk database export/import` 将 SQLite 文件路径和 MySQL DSN 环境变量分别建模，并在适配器内部直接依赖 `sqlite3` 与 `PyMySQL`。MySQL DSN 还由项目自行解析，导致新增数据库类型需要复制连接、事务、参数绑定和 schema 读取逻辑。

项目需要引入统一的 SQLAlchemy 2.x 风格 DSN，并通过第三方数据库驱动为 SQLite、MySQL 和 PostgreSQL 提供一致的连接与数据操作边界，降低后续扩展数据库类型和通用操作的成本。

## Goal

1. 使用 SQLAlchemy 2.x Core 作为数据库连接、事务、参数绑定和结果访问的统一抽象，不引入 ORM。
2. 统一接受明确的 SQLAlchemy 风格 DSN，至少覆盖：
   - `sqlite:///./data/app.db`
   - `sqlite:////absolute/path/app.db`
   - `mysql+pymysql://user:password@host:3306/app`
   - `postgresql+psycopg://user:password@host:5432/app`
3. 通过官方或主流 DBAPI 驱动实现 SQLite、MySQL、PostgreSQL 支持；数据库差异集中在 dialect/adapter 边界，不向调用方暴露不同连接对象和参数占位符规则。
4. 为对外调用提供统一的 DSN 驱动数据库接口，覆盖同步和异步连接生命周期、参数化 SQL 执行、查询结果访问和事务边界。
5. 增加 `dbtalk database query` 与 `dbtalk database exec` 子命令。`query` 支持 `table` 和 `json` 两种输出格式，默认使用 `table`；`exec` 输出受影响行数和执行状态。
6. 将现有 JSONL `export/import` 的 SQLite、MySQL 连接和执行路径迁移到统一连接抽象，并增加 PostgreSQL 端支持。
 7. 保持 `dbtalk mysql dump/restore` 使用原生 `mysqldump`/`mysql` 客户端；SQL 备份内容和 restore 语义不因本需求改为 SQLAlchemy 执行。

## Non-goal

- 不在本需求中引入 ORM、Model 映射、Repository 自动生成或应用迁移系统。
- 不创建、修改或删除目标数据库 schema；JSONL transfer 仍要求目标 schema 预先存在。
- 不把原生 MySQL dump/restore 改造为 SQLAlchemy 导出或导入。
- 不默认增加复杂的行级过滤、分页 DSL、批量任务调度或连接池管理配置。
- `query`/`exec` CLI 第一版不承诺多语句脚本、交互式 shell、SQL 文件变量模板或任意 Python 表达式参数。
- 不在日志、异常文本、JSONL 制品或进程参数中泄露 DSN 密码。

## User scenarios

1. 调用方使用一个 SQLAlchemy 风格 DSN 连接 SQLite、MySQL 或 PostgreSQL，而不需要分别构造 `sqlite_path`、MySQL 主机参数或专用连接对象。
2. 调用方使用统一接口执行带参数的 SQL，并以统一的数据结构读取列名、行值和影响行数。
3. 调用方在统一事务上下文中执行多条写操作，异常时回滚，成功时提交。
4. 管理员通过 `dbtalk database query` 使用 DSN 执行查询，默认得到适合终端查看的表格，也可以选择 JSON 输出供脚本消费。
5. 管理员通过 `dbtalk database exec` 使用参数化 SQL 执行写操作，并获得非敏感的影响行数结果。
6. 异步调用方使用相同的 DSN 连接异步 engine，执行查询、写入和事务，而不直接操作各 DBAPI 的 async connection 类型。
7. 管理员使用 JSONL export/import 在 SQLite、MySQL 和 PostgreSQL 之间传输既有表数据；数据库方言差异由内部 adapter 处理。
8. 管理员为 export/import/query/exec 以及 MySQL dump/restore 提供完整 DSN，或使用 `--dsn-env` 指向环境变量；每条命令必须明确二选一，不允许按数据库类型猜测连接参数。

## Acceptance

- [ ] SQLAlchemy 风格 DSN 能被统一解析和校验，支持 SQLite、MySQL 和 PostgreSQL 的明确 dialect/driver 形式；无 driver 的 MySQL/PostgreSQL URL、别名、Go 风格 DSN、缺失数据库名或无效 SQLite 路径均被拒绝，并给出非敏感错误。
- [ ] 项目依赖包含 SQLAlchemy 2.x、同步 MySQL DBAPI 驱动、同步 PostgreSQL `psycopg` 3 驱动，以及 SQLite/MySQL/PostgreSQL 的异步驱动；SQLite 不额外要求外部数据库服务。
- [ ] 对外统一接口不要求调用方导入 `sqlite3.Connection`、`pymysql.Connection` 或 PostgreSQL 驱动的原生连接类型。
- [ ] 统一同步和异步接口支持参数化执行、查询结果列信息和行值访问、影响行数、提交、回滚及上下文关闭；异步接口不阻塞事件循环。
- [ ] `dbtalk database query` 接受完整 DSN 或 DSN 环境变量、SQL 和参数，`--format table|json` 默认 `table`；`json` 输出稳定的列名和 JSON 可编码值，`table` 对 NULL、空结果和宽列有明确表现。
- [ ] `dbtalk database exec` 接受完整 DSN 或 DSN 环境变量、单条参数化 SQL 和参数，成功输出影响行数；执行失败返回非零状态且不泄露 DSN 或参数中的敏感值。
- [ ] JSONL export/import 的 SQLite、MySQL 现有成功路径、`insert`/`upsert`、表级事务、外键检查、类型编码和 gzip 行为保持不变。
- [ ] JSONL transfer 能使用统一 DSN 表达源端和目标端；PostgreSQL 端至少支持普通表 schema 读取、外键顺序、导出、导入和现有 JSONL 类型兼容校验。
- [ ] MySQL、PostgreSQL 连接参数和 DSN 不出现在正常日志、错误摘要和 CLI 帮助示例中；密码不通过普通命令参数传递。
- [ ] 现有 CLI 的 `dbtalk mysql dump/restore` 行为保持兼容，且不依赖 SQLAlchemy 才能运行。
- [ ] 单元测试覆盖 DSN、同步/异步统一接口、参数绑定、事务回滚和三种 dialect 的 SQL/schema 差异；真实 MySQL/PostgreSQL 测试继续通过显式环境条件控制，未提供服务时跳过而非失败。
- [ ] 文档说明 DSN 格式、驱动依赖、凭据注入方式、统一接口边界和 PostgreSQL 的已支持范围。

## Open questions

暂无需要用户确认的未决事项。参数格式、依赖安装方式和所有命令的 DSN 入口已按 Decisions 固定。

## Decisions

- 统一 DSN 语义以 SQLAlchemy 2.x URL 为准，不继续扩展项目自定义 Go DSN 解析作为公共格式。
- 数据库执行抽象使用 SQLAlchemy Core；原生连接仅允许隐藏在 dialect/adapter 内部。
- PostgreSQL 采用 Psycopg 3，使用 `postgresql+psycopg://`；异步模式由 SQLAlchemy async engine 配合对应 async driver 实现。
- 同步和异步接口保持相同的明确 DSN、SQL、参数和结果语义；调用方根据 sync/async API 选择执行模型。
- export/import/query/exec/dump/restore 都通过 `--dsn` 或 `--dsn-env` 明确提供 DSN，不保留数据库类型专用文件或 DSN 参数。
- `query --format table` 是默认终端输出，`--format json` 是脚本消费格式。
- 原生 MySQL dump/restore 与通用数据操作保持职责分离。
- 含凭据的 DSN 默认通过环境变量名注入 CLI；日志和错误只允许输出脱敏后的 dialect、host、port 和 database 元数据。

## Risk

- SQLAlchemy 的统一接口不能消除 PostgreSQL、MySQL、SQLite 的类型、DDL、placeholder 和事务差异；schema introspection、upsert 和日期/二进制编码仍需按 dialect 验证。
- 同一逻辑 DSN 在同步和异步模式下可能需要不同的 SQLAlchemy driver 名称，需定义清晰的 DSN driver 选择或规范化规则，避免调用方感知过多方言细节。
- 把现有传输适配器迁移到 SQLAlchemy 可能改变游标流式读取、MySQL 一致性读和 SQLite 外键检查行为，需要针对回归风险增加 fake engine/connection 测试和可选集成测试。
- PostgreSQL 驱动是否主依赖会影响安装体积、部署环境和锁文件；需要在用户确认后固定。
- 直接允许 `--dsn` 可能使密码暴露在进程列表中，因此即使支持该入口也应将 `--dsn-env` 作为文档和 skill 的默认方式。

## User review notes

- 用户确认第一批通用 CLI 为 `query` 和 `exec`。
- 用户确认 `query` 支持 `table` / `json` 两种格式，默认 `table`。
- 用户要求考虑同步和异步数据库操作能力。
- PostgreSQL 驱动选择由项目按主流方案确定，当前采用 Psycopg 3 作为默认方案。
- 用户明确要求进入 Plan 阶段；未决的参数格式、旧入口兼容、异步依赖和 dump/restore DSN 支持按 Plan 中的实施假设推进，并在实现前后保留为风险记录。
- 用户进一步确认：所有数据库命令统一接受完整 DSN 或 DSN 环境变量；不保留 DSN 别名或数据库类型专用参数。
