# 统一 DSN 与通用数据库操作
最后修改时间: 2026-08-22 21:22:40

---
Review status: Accepted
Flow mode: standard
Stage: Plan
---

## Basis

本计划实施已接受的 [Requirement](../requirement/20260820-unified-dsn-database-operations.md)。变更范围跨越依赖、连接抽象、CLI、JSONL transfer、三种数据库 dialect、同步/异步 API 和测试。

现有 `dbtalk mysql dump/restore` 仍是原生客户端封装，本计划不把它迁移到 SQLAlchemy；统一 DSN 首先服务于 `dbtalk database` 和公开数据库操作 API。

## Scope and CLI contract

### Canonical DSN

同步 API 和 CLI 接受 SQLAlchemy 风格 DSN：

```text
sqlite:///./data/app.db
sqlite:////absolute/path/app.db
mysql+pymysql://user:password@host:3306/app
postgresql+psycopg://user:password@host:5432/app
```

CLI 的所有数据库命令都接受 `--dsn DSN` 与 `--dsn-env NAME` 二选一。推荐使用 `--dsn-env` 避免密码出现在进程参数中；公开 Python API 接收调用方已安全取得的 DSN 字符串。

异步 Python API 使用同一逻辑 DSN，通过 SQLAlchemy async engine 选择 async driver：

```text
sqlite+aiosqlite:///./data/app.db
mysql+asyncmy://user:password@host:3306/app
postgresql+psycopg://user:password@host:5432/app
```

DSN 模块负责解析、明确 dialect/driver 校验、同步/异步 driver 选择和脱敏展示，不接受别名、默认 MySQL/PostgreSQL driver 或文件路径参数，也不向日志或异常传播密码。

### New CLI commands

```text
dbtalk database query \
  --dsn-env APP_DSN \
  --sql "SELECT id, name FROM users WHERE id = :id" \
  --param id=1 \
  --format table|json

dbtalk database exec \
  --write \
  --dsn-env APP_DSN \
  --timeout 30 \
  --sql "UPDATE users SET name = :name WHERE id = :id" \
  --param name='"Ada"' \
  --param id=1
```

- `query --format` 只接受 `table` 和 `json`，默认 `table`。
- 参数使用可重复的 `NAME=JSON_VALUE`；支持 JSON 的字符串、数字、布尔值和 `null`，避免所有数据被错误地当作字符串传给数据库。
- SQL 使用 SQLAlchemy named bind 参数；第一版只执行一条 SQL，不支持多语句脚本或模板变量。
- `table` 使用 `tabulate` 输出，NULL 显示为 `NULL`，无结果保留列头并报告 0 行。
- `json` 输出稳定 envelope：`columns`、`rows`、`row_count`；日期时间、Decimal、bytes 等值通过统一 JSON-safe 编码输出。
- `exec` 默认使用只读会话；传入 `--write` / `-w` 后切换为写会话。成功输出影响行数；失败返回非零状态，错误信息不包含 DSN 或参数值。
- `query` 和 `exec` 都支持 `--timeout` / `-t`，省略时读取 `database.operation_timeout_seconds`（默认 30），只接受正整数，并限制单条 SQL 语句的执行时间。
- CLI 使用同步 engine；异步能力只通过 Python API 暴露，不增加 `--async` 开关。

### Existing transfer commands

保留现有一级和二级命令：

```text
dbtalk database export --source sqlite|mysql|postgresql ...
dbtalk database import --target sqlite|mysql|postgresql ...
```

统一入口为 `--dsn DSN` 或 `--dsn-env NAME`，不保留 `--sqlite-path`、`--mysql-dsn-env` 或其他数据库类型专用参数。`--source`/`--target` 继续显式保留，且必须与 DSN dialect 一致。传输 Python API 的连接模型只保留 DSN 或 DSN 环境变量。

## Implementation steps

1. 固定依赖和基础类型。
   - 在 `pyproject.toml` 增加 SQLAlchemy 2.x、`psycopg[binary]`、`aiosqlite`、`asyncmy` 和 `tabulate`；保留现有 `pymysql` 作为 MySQL 同步 driver。
   - 更新 `uv.lock`，确认 Python 3.12 约束、类型检查和打包依赖一致。
   - 在 `models.py` 增加 `postgresql` driver、DSN 连接模型、统一查询/执行结果模型和参数类型。

2. 建立统一 DSN 和 engine 工厂。
   - 新增 `src/dbtalk/database/dsn.py`：使用 SQLAlchemy `make_url`/`URL` 解析 DSN，只允许明确的 driver，支持 async driver 选择、环境变量读取和脱敏摘要。
   - 新增 `src/dbtalk/database/connection.py`：封装 sync `Engine`/`Connection` 与 async `AsyncEngine`/`AsyncConnection`，提供连接生命周期、事务上下文和统一异常映射。
   - 新增 `DatabaseClient` / `AsyncDatabaseClient` 或等价协议，公开 `query`、`execute`、`transaction`，返回不暴露 DBAPI 原生连接类型的结果模型。
   - 使用 SQLAlchemy `text()` 和 named bind 参数，禁止字符串拼接用户值；标识符仍由内部 schema 逻辑使用 dialect preparer 安全引用。

3. 实现通用操作的结果和 CLI 层。
   - 新增 `src/dbtalk/database/operations.py`，负责参数解析、query/exec 编排、JSON-safe 值转换、table/json 渲染和非敏感错误转换。
   - 扩展 `src/dbtalk/database/cli.py`，增加 `query` 与 `exec` 命令、`--dsn`/`--dsn-env`、`--sql`、重复 `--param` 和 query 的 `--format`。
   - 在 `src/dbtalk/commands/database.py` 和根 help 文案中接入新命令，保持现有命令组结构。

4. 将 JSONL transfer 迁移到 SQLAlchemy 连接边界。
   - 新增 `src/dbtalk/database/sqlalchemy_transfer.py` 或同等 adapter，使用 SQLAlchemy Inspector 读取普通表、列、主键和外键，统一生成 `TableSchema`。
   - 迁移 SQLite/MySQL 的导出、导入和 schema 预检，保持 1000 行批量读取、临时文件原子替换、双遍导入和单表事务。
   - 为 SQLite 保留并通过 SQLAlchemy 执行外键/完整性检查；为 MySQL 保留一致性只读导出和 `zero_datetime_as_null` 行为。
   - 增加 PostgreSQL adapter 行为：普通表 introspection、类型族映射、外键拓扑排序、流式导出、`insert` 和主键 `upsert`。使用 PostgreSQL dialect 的 `ON CONFLICT`，不关闭外键检查。
   - 使用 SQLAlchemy dialect-specific insert/upsert 构造，避免继续维护三套 placeholder 和引号规则。
   - 保留现有 `format.py` JSONL v1 和 `schema.py` 的业务校验；仅将数据库连接、schema 来源和 SQL 执行替换为统一 adapter。

5. 迁移 transfer CLI 到 canonical DSN。
   - 将 `TransferConnection` 从 driver-specific path/env 字段改为 DSN 规范；增加 PostgreSQL 选择。
   - `database export/import` 只接收 `--dsn` 或 `--dsn-env`，命令层不转换 path 或 driver-specific 参数。
   - 更新 `validate_connection`、成功日志和错误信息，只输出 dialect、host、port、database 等脱敏元数据。
   - 保持 `src/dbtalk/mysql/dump.py`、`restore.py`、`client.py` 的原生客户端路径不变；必要时只复用独立的 DSN 脱敏/解析工具，不让 SQLAlchemy 成为其运行依赖。

6. 实现同步/异步测试和跨 dialect 契约。
   - 新增 DSN 解析、driver 规范化、脱敏、invalid URL 和环境变量测试。
   - 使用临时 SQLite 文件验证 sync/async query、exec、参数类型、空结果、JSON/table 输出和事务回滚；async 测试使用 `pytest-asyncio` 与 `aiosqlite`。
   - 使用 SQLAlchemy dialect 编译或 mock connection 测试 MySQL/PostgreSQL 的参数、identifier、upsert 和类型分支，不要求每次测试启动外部服务。
   - 更新现有 transfer 测试，覆盖 DSN 连接模型、SQLite/MySQL 回归、PostgreSQL schema/type/FK 逻辑以及 `--dsn`/`--dsn-env` 二选一校验。
   - 增加可选集成 marker：通过 `DBTALK_MYSQL_DSN`、`DBTALK_POSTGRESQL_DSN` 显式启用；未配置时跳过而非失败。

7. 更新用户文档和 Codex skill。
   - 更新 `README.md`、`docs/database.md`，说明 DSN、query/exec、输出格式、参数化和凭据策略。
   - 更新 `docs/codex.md` 与 `plugins/dbtalk/skills/database/SKILL.md`，明确何时使用 query/exec、transfer 与原生 MySQL dump/restore 的边界。
   - 在文档中分别说明 sync API、async API 和 async driver 依赖；不把真实密码放入示例。

## Files to change

预计新增或修改：

- `pyproject.toml`
- `uv.lock`
- `src/dbtalk/database/models.py`
- `src/dbtalk/database/dsn.py`
- `src/dbtalk/database/connection.py`
- `src/dbtalk/database/operations.py`
- `src/dbtalk/database/sqlalchemy_transfer.py`
- `src/dbtalk/database/cli.py`
- `src/dbtalk/database/transfer.py`
- `src/dbtalk/database/schema.py`
- `src/dbtalk/database/mysql.py`
- `src/dbtalk/commands/database.py`
- `tests/test_database_dsn.py`
- `tests/test_database_operations.py`
- `tests/test_database_cli.py`
- `tests/test_database_transfer.py`
- `tests/test_database_transfer_contract.py`
- `tests/test_database_transfer_logging.py`
- `tests/test_unit_boundaries.py`
- `README.md`
- `docs/database.md`
- `docs/codex.md`
- `plugins/dbtalk/skills/database/SKILL.md`

如迁移后旧 SQLite/MySQL adapter 不再有独立职责，可在实现阶段删除其原生连接代码；不删除 `mysql/dump.py`、`mysql/restore.py` 的原生客户端实现。

## Verification plan

1. 依赖与静态检查：`uv sync --all-groups --locked`、`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy src tests`。
2. CLI 契约：验证 root/database help、query/exec 参数校验、默认 table、json envelope、参数解析、非零错误状态和敏感信息不出现在输出。
3. sync/async SQLite：验证 query、exec、参数绑定、事务提交/回滚、NULL、日期时间、bytes、空结果和并发 async 调用不直接阻塞事件循环。
4. dialect 契约：验证 SQLite、MySQL、PostgreSQL DSN 解析、sync/async driver 选择、identifier quoting、insert/upsert SQL 编译和 schema metadata 转换。
5. JSONL 回归：运行现有 SQLite/MySQL 全量 transfer、gzip、零日期、include/exclude、外键顺序、双遍导入和单表事务测试。
6. PostgreSQL 集成：在显式提供 `DBTALK_POSTGRESQL_DSN` 时验证普通表、主键/联合主键、外键、常见类型、insert/upsert 和跨库 JSONL；无服务环境明确记录跳过原因。
7. MySQL 集成：在已有测试条件下验证当前 export/import；原生 dump/restore 单独运行现有测试，确保不依赖 SQLAlchemy。
8. 项目级验证：`uv run pytest`、`git diff --check`、`uv build`，确认覆盖率仍达到项目 90% 门槛。

## Blockers

- 当前没有必须用户先提供的外部服务；SQLite 可覆盖大部分 sync/async 契约。
- PostgreSQL/MySQL 的真实集成验证依赖本机服务或用户显式提供 DSN，属于可选验证条件，不阻塞单元实现。

## Assumptions

- 所有数据库命令以 `--dsn` 与 `--dsn-env` 二选一作为统一连接入口；不保留 path 或 driver-specific 参数。
- `--param NAME=JSON_VALUE` 是第一版 query/exec 参数契约；不支持多语句和参数文件。
- CLI 使用同步 engine；异步支持通过公开 Python API 完成，不要求 CLI 运行在 event loop 中。
- SQLAlchemy、同步/异步 DBAPI 驱动和 `tabulate` 随主依赖安装，以保证声明支持的数据库开箱可用；若锁文件或部署体积出现实际约束，再拆分 optional extras 并记录范围变化。
- `dbtalk mysql dump/restore` 也接受统一的 MySQL DSN，解析后仍调用原生客户端；不再接受 host、port、user、password、database 分散参数。

## Risks

- SQLAlchemy Inspector 在三种数据库返回的类型和 FK metadata 不完全一致，需避免把 dialect-specific 类型误判为 JSONL 不兼容。
- SQLAlchemy stream result、MySQL consistent snapshot 和 async driver 的连接生命周期可能影响内存使用与事务边界，需通过 fake 和可选集成测试确认。
- 原生 MySQL dump/restore 需要将 canonical DSN 转换为客户端参数；转换只能发生在原生客户端适配边界。
- `exec` 是直接写操作入口，文档、skill 和输出必须明确其可能覆盖数据的风险；不自动添加确认提示，以保持脚本可用性。

## Rollback

- 代码回退即可恢复当前 SQLite/MySQL 传输实现；原有 JSONL v1 制品不变。
- 新增依赖和 CLI 子命令可独立移除；不修改任何数据库 schema 或原始 dump 文件。
- 已执行的 `exec`、JSONL import 或 MySQL restore 产生的数据库变更不具备自动回滚能力，调用方需依赖数据库备份或显式恢复操作。

## User review notes

- 用户要求使用 standard 流程并进入 Plan 阶段。
- 用户确认 query/exec CLI、table/json 输出和默认 table。
- 用户要求考虑 async；本计划将其落在统一 Python API 和 SQLAlchemy async engine，不增加 CLI async 开关。
- PostgreSQL 默认按 Psycopg 3 方案实现。
- 用户确认 export/import/dump/restore/query/exec 全部统一接受完整 `--dsn` 或 `--dsn-env`，不保留别名和数据库类型专用参数。
