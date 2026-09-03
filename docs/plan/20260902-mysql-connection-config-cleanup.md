---
Review status: Accepted
Flow mode: standard
Stage: Plan
---

# 收敛 dump/restore 配置计划
最后修改时间: 2026-09-02 15:05:07

## Requirement basis

- 已接受的 Requirement：`docs/requirement/20260902-mysql-connection-config-cleanup.md`。
- MySQL 与 PostgreSQL 的 dump/restore 使用同一配置契约：每个引擎配置组都保存 `output_directory` 与 `client_image`；连接、target、输入/输出文件路径和执行策略都必须由单次 CLI 调用提供。
- 两个配置组的共享字段使用 `DumpRestoreConfig(output_directory, client_image)` 与参数化 loader；MySQL 的 `MySQLConfig` 在其上追加只供 MySQL JSONL export 使用的 `zero_datetime_as_null`。
- MySQL 旧 `mysqldump`、`mysqlrestore` 组及所有 host、port、user、password、database 配置直接删除。新 MySQL group 为 `mysql`，默认 `client_image` 为 `mysql:8.0.39`，MySQL 零日期配置也归入该组。
- PostgreSQL 保留既有 `postgres.output_directory` 与 `postgres.client_image` 的用户可见配置值和运行行为，仅复用 shared config type/loader。
- MySQL 和 PostgreSQL Docker fallback 都保持 `mapped container -> local client -> configured image` 的顺序；配置 image 不在本地时执行 `docker pull` 并将拉取日志输出到终端，不扫描候选 image。
- 删除 dump/restore 手工集成测试，仅保留并补齐单元测试；不得以真实服务测试替代该约束，也不得修改已有数据库、用户或数据。
- `plugins/dbtalk` 的同步不属于本任务；源码、配置样例、用户文档和测试先收敛，plugin 后续按用户指令单独同步。
- query/exec 的单条 SQL timeout 分别进入根 `Settings`。MySQL 进度日志采样和 tooling 外部 CLI timeout 不进入应用设置。
- `scripts/backup_db.py` 及其 YAML 独立于 `dbtalk` 主配置和应用业务。

## Time policy design

`dbtalk.yaml` 与 `.env.example` 只声明 query 与 exec 各自的 timeout：

| 配置 | YAML 默认值 | 环境变量 | 代码消费者 | 校验 |
| --- | --- | --- | --- | --- |
| `database.query_timeout_seconds` | `30` | `DBTALK_DATABASE__QUERY_TIMEOUT_SECONDS` | `database query`。 | 正整数。 |
| `database.exec_timeout_seconds` | `30` | `DBTALK_DATABASE__EXEC_TIMEOUT_SECONDS` | `database exec`。 | 正整数。 |

不新增 MySQL/PostgreSQL dump/restore command timeout：当前它们没有时间上限，新增默认上限会改变大库备份/恢复行为，需独立需求决定。MySQL 进度日志采样、tooling 外部 CLI timeout、SQLite progress handler 的 `1_000` 检查步长、MySQL/PostgreSQL 默认端口等不属于应用设置。

## Implementation steps

1. 收敛 typed settings 与默认值。
   - 修改 `src/dbtalk/settings.py`，不在 Python 源码中定义 client image 默认值；默认 image 仅由 `dbtalk.yaml` 提供。
   - 用 `DumpRestoreConfig(output_directory, client_image)` 替换 `MySQLDumpConfig`、`MySQLRestoreConfig` 与 `PostgresConfig`；`Settings.postgres` 直接使用该类型，`Settings.mysql` 使用追加 `zero_datetime_as_null` 的 `MySQLConfig`。
   - 用一个参数化 loader 读取 `mysql` / `postgres` 的共享字段并统一校验 `output_directory`、`client_image` 非空；MySQL loader 追加读取 `zero_datetime_as_null`。Dynaconf 的 `DBTALK_<GROUP>__OUTPUT_DIRECTORY`、`DBTALK_<GROUP>__CLIENT_IMAGE` 覆盖必须直接生效，零日期覆盖使用 `DBTALK_MYSQL__ZERO_DATETIME_AS_NULL`。
   - 删除 MySQL host、port、user、password、database 的默认值、校验和 loader；不添加别名、旧键迁移或 unknown-key startup validation。
   - 保持 PostgreSQL 当前默认 image 来源和值不变；共享 loader 只消除重复代码，不改变 PostgreSQL 的配置语义。

2. 将 MySQL Docker fallback 绑定到共享配置。
   - 修改 `src/dbtalk/mysql/dump.py`，使 resolver 接收 `DumpRestoreConfig`，并把 `client_image` 传入 dump execution options；默认输出目录继续仅用于未传 `--output` 的 dump。
   - 修改 `src/dbtalk/mysql/restore.py`，让 restore resolver 或 runtime options 接收相同的 `client_image`，从而 restore 不再绕过 settings。
   - 修改 `src/dbtalk/mysql/cli.py`，dump 和 restore 都从 `Settings.mysql` 取得同一 config；DSN 与 `--database` 的连接/target 解析保持不变。
   - 修改 `src/dbtalk/mysql/client.py`，将 `docker_mysql_image()` 改为接收期望 image 并以精确本地 inspect 校验；image 不存在时输出拉取日志并执行 `docker pull`，拉取失败时明确失败；删除 `mysql:*` 扫描、`mysql:latest` 偏好与“第一项”选择。
   - 保持 MySQL mapped container、本机 client、密码环境变量、SQL 预检、gzip 解压、进度日志和 native command vector 不变。

3. 将 PostgreSQL 改为共享 config type，保持行为不变。
   - 修改 `src/dbtalk/postgres/dump.py` 的 type import 与 resolver 注解，使用 `DumpRestoreConfig`。
   - `src/dbtalk/postgres/cli.py`、`dump.py`、`restore.py` 继续使用 `Settings.postgres.output_directory` / `client_image`，不改变 `pg_dump`、`pg_restore`、archive 校验或 Docker fallback 执行顺序。
   - 不为两种数据库的 native command 构造引入通用执行层；共享范围严格限制在 config type、loader 和配置约束。

4. 更新配置样例和用户文档。
   - 修改 `dbtalk.yaml` 与 `.env.example`：删除 `mysqldump` / `mysqlrestore` 以及所有连接字段，新增 `mysql.output_directory: data`、`mysql.client_image: mysql:8.0.39`、`mysql.zero_datetime_as_null: true` 和对应 `DBTALK_MYSQL__*` 环境变量；移除 `database.zero_datetime_as_null`。
   - 修改 `docs/mysql.md`：将默认输出目录引用改为 `mysql.output_directory`，说明映射容器和本机 client 均不可用时使用 `mysql.client_image`，本地缺失时拉取该 image 并打印日志；不扫描其他 image；给出与 `postgres` 对称的配置示例。
   - 修改 `docs/postgres.md`：只在必要处表述共享的目录/image 配置契约，保留现有 PostgreSQL values、client compatibility 说明和命令示例。
   - 不修改历史 Requirement/Plan/Verification，也不修改 `plugins/dbtalk`。

5. 重写受影响的单元测试并移除手工集成测试。
   - 修改 `tests/test_settings.py`：以 `mysql` / `postgres` 配置组构建样例，覆盖 YAML、dotenv、进程环境优先级、`mysql:8.0.39` 默认值、MySQL 零日期配置、共享 dump/restore base type，以及空目录/image 的拒绝。
   - 修改 `tests/test_unit_boundaries.py`：用参数化 loader 的错误边界替换旧 MySQL host/port 校验；测试 MySQL Docker image 的精确 inspect 成功与失败，不再测试 image 列表扫描或 `latest` 偏好。
   - 修改 `tests/test_mysql.py`：用 `DumpRestoreConfig` 替换旧 config fixture；覆盖 dump、restore 均将 `mysql.client_image` 用于 Docker fallback，本地缺失时拉取该 image，且不选择其他本地 `mysql:*` image。
   - 修改 `tests/test_postgres.py`：用 `DumpRestoreConfig` 替换 `PostgresConfig` 构造，保持 PostgreSQL dump/restore 行为断言。
   - 删除 `tests/test_manual_integration.py`；不添加新的 dump/restore 集成环境变量、数据库或用户。

6. 收敛时间策略配置并删除源码默认。
   - 修改 `src/dbtalk/settings.py`：删除共享 operation timeout 常量；loader 要求 `database.query_timeout_seconds` 与 `database.exec_timeout_seconds` 存在并分别集中校验。
   - 修改 `src/dbtalk/database/cli.py` 与 `src/dbtalk/database/operations.py`：query 和 exec 各自从 typed settings 或显式 `--timeout` 取得 timeout；Python API 不再带有 `30` 秒默认参数。
   - 修改 `dbtalk.yaml`、`.env.example` 与用户文档，分别列出 query/exec 配置、值、范围和环境变量。

## Expected files

- `src/dbtalk/settings.py`
- `src/dbtalk/database/cli.py`
- `src/dbtalk/database/mysql.py`
- `src/dbtalk/database/operations.py`
- `src/dbtalk/mysql/cli.py`
- `src/dbtalk/mysql/dump.py`
- `src/dbtalk/mysql/restore.py`
- `src/dbtalk/mysql/client.py`
- `src/dbtalk/postgres/dump.py`
- `dbtalk.yaml`
- `.env.example`
- `scripts/backup_db.py`
- `scripts/backup_db.yaml`
- `scripts/backup_db.example.yaml`
- `docs/mysql.md`
- `docs/database.md`
- `docs/postgres.md`
- `tests/test_database_transfer.py`
- `tests/test_settings.py`
- `tests/test_unit_boundaries.py`
- `tests/test_backup_db.py`
- `tests/test_mysql.py`
- `tests/test_postgres.py`
- `tests/test_manual_integration.py` (删除)

## Verification plan

1. 运行相关定向单元测试，核对 shared config、两个引擎的 image propagation、MySQL 零日期配置、query/exec 各自 timeout、旧 key 不再被 typed settings 读取，以及 target 仍只来自 `--database > DSN database > 失败`。
2. 运行 `make check`，使用项目统一入口执行 Ruff format 检查、Ruff lint 与 mypy；不单独手工格式化文件。
3. 运行 `make test`，确认全量单元测试通过且手工 integration test 文件已删除。
4. 检查 `dbtalk.yaml`、`.env.example`、`docs/mysql.md`、`docs/database.md` 与 `docs/postgres.md`：只公开 query/exec 各自 timeout、两引擎对等的目录/image 配置和 MySQL 专用零日期配置，不出现 MySQL 连接或 target 配置，不记录真实凭据。
5. 不执行真实 dump 或 restore 集成测试；验证不创建、修改或删除任何既有数据库、用户或数据。

## Risks and rollback

- 移除旧 MySQL 组后，仍在本地配置中保留旧键的用户会因 Dynaconf unknown-key 行为而看似提供了值，但 typed settings 不再读取它们；本任务不新增全局 schema validation 或兼容迁移。
- MySQL Docker fallback 从自动发现改为精确 image 后，只有非默认 image 的环境需显式配置 `mysql.client_image`；明确失败优于不稳定地选中列表中的任意 image。
- shared config type 的重命名会影响直接构造旧 config 或读取旧 `Settings` 字段的测试与调用点；全仓搜索、mypy 和全量测试是回归防线。
- 如需回退，只恢复上一版应用代码和配置样例；不新增旧键兼容层、不恢复连接/target config，也不回退已确定的 DSN target precedence。

## Open questions

暂无需要用户确认的未决事项。
