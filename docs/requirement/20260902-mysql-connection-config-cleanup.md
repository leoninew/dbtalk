---
Review status: Accepted
Flow mode: standard
Stage: Requirement
---

# 清理并收敛 MySQL dump/restore 配置
最后修改时间: 2026-09-02 15:20:00

## Background

已完成实现阶段的 `20260902-dsn-database-name` 任务将 MySQL dump/restore 的连接身份和目标 database 统一为每次命令提供的 DSN 与 `--database`：目标优先级固定为 `--database > DSN database > 失败`，运行时 YAML 或环境变量不再参与连接或目标选择。

但 `src/dbtalk/settings.py`、`dbtalk.yaml` 和 `.env.example` 仍公开 `mysqldump.host`、`port`、`user`、`password`、`database`，以及整个 `mysqlrestore` 配置组。它们已不再构成命令行为，却继续让用户误以为 dump/restore 可以从全局配置取得连接或恢复目标。

MySQL 与 PostgreSQL 的 dump/restore 都有两项跨调用的运行环境配置：默认 dump 制品目录和 Docker native client image。前者只被 dump 使用；后者同时被 dump 与 restore 的 Docker fallback 使用。PostgreSQL 已通过 `postgres.output_directory` 与 `postgres.client_image` 表达这一模型；MySQL 当前没有 image 配置：当未命中已映射容器且本机 client 不可用时，它扫描本地 `mysql:*` images，优先 `mysql:latest`，否则取列表第一项。该行为不可预测，也无法让用户选择与服务端版本兼容的 client。

两引擎的 dump/restore 配置契约相同，只是值和 native client 参数不同。因此本任务将把旧 `mysqldump` 与 `mysqlrestore` 组直接收敛为 `mysql` 组：`mysql.output_directory` 和 `mysql.client_image`；这两个字段与 `postgres` 共享 `DumpRestoreConfig(output_directory, client_image)` 及参数化 loader。MySQL JSONL export 的零日期规范化仅适用于 MySQL，因此 `zero_datetime_as_null` 同时归入 `mysql` 组，由 `MySQLConfig` 扩展共享 dump/restore 字段。使用 `mysqldump` 作为组名会让 restore 读取一个语义不符的配置；配置组仍按引擎分别保存值，但不再为相同字段、校验和 fallback 契约维护两套实现。

## Goal

1. 从应用的 typed settings、官方配置样例、环境变量样例、测试和用户文档中删除 MySQL dump/restore 的连接与目标 database 配置。
2. 采用跨引擎的共享 dump/restore 配置契约：MySQL 与 PostgreSQL 均使用 `DumpRestoreConfig(output_directory, client_image)` 的字段和校验。MySQL 的 `MySQLConfig` 在此基础上追加 MySQL 专用 `zero_datetime_as_null`；PostgreSQL 继续使用 `postgres.output_directory`、`postgres.client_image`。
3. 删除整个 `mysqldump`、`mysqlrestore` 旧配置组及其 loader、类型和 `Settings` 字段；以一个参数化 loader 加载共享 dump/restore 字段，不再保留 `MySQLDumpConfig`、`MySQLRestoreConfig` 或 `PostgresConfig` 的重复定义。
4. 令 MySQL 与 PostgreSQL Docker fallback 使用配置的精确 image，不再扫描或偏好本地 `mysql:*` images；配置 image 不在本地时输出拉取日志并执行 `docker pull`。
5. 删除仅服务于 MySQL dump/restore 的 opt-in 手工集成测试；行为由单元测试覆盖，不再为它维护连接或 target 输入。
6. 不改变已确定的命令约束：MySQL dump/restore 的连接只来自 `--dsn` 或 `--dsn-env`，目标只来自 `--database` 或 DSN database。
7. `database query` 与 `database exec` 的单条 SQL timeout 必须分别由 typed settings 管理，Python 源码不得为这些配置保留数值默认或平行常量；单次 CLI 显式输入可覆盖各自命令的设置。

## Non-goal

- 不设计远端 dump store、保留策略、对象存储或备份调度。
- 不改变 MySQL dump/restore 的 native command、容器/本机 client 优先级、输出命名或 archive 行为；仅将 Docker fallback 的 image 选择改为显式配置。
- 不改变 PostgreSQL 的配置值或 dump/restore 运行行为；仅将其现有的重复 typed config 和 loader 收敛到共享实现。`database.query_timeout_seconds`、`database.exec_timeout_seconds`、日志和根 `verbose` 配置保持不变。
- `scripts/backup_db.py` 及其 YAML 独立于 `dbtalk` 主配置和应用业务。
- 不为已删除字段提供别名、迁移、兼容回退或默认连接身份。
- 不修改历史 Requirement、Plan、Verification 文档；它们保留当时的决策记录。

## User scenarios

| 场景 | 输入 | 预期行为 |
| --- | --- | --- |
| MySQL dump | `mysql dump --dsn-env DBTALK_DSN_APP`，可选 `--database` | 连接完全来自 `DBTALK_DSN_APP`；省略 `--database` 时只使用 DSN database 作为 target。`dbtalk.yaml` 不提供 host、user、password 或 database 回退。 |
| MySQL restore | `mysql restore --dsn-env DBTALK_DSN_APP --input backup.sql`，可选 `--database` | 连接和目标解析与 dump 相同；不存在 `mysqlrestore` 配置组。 |
| 默认 dump 输出 | 未提供 `--output` | 使用 `mysql.output_directory` 作为本地输出目录。 |
| MySQL Docker fallback | 未命中已映射容器且本机没有 `mysqldump` / `mysql` | dump 和 restore 都使用 `mysql.client_image`；本地缺失时输出拉取日志并拉取该精确 image，不扫描其他 `mysql:*` image。 |
| PostgreSQL Docker fallback | 未命中已映射容器且本机没有 `pg_dump` / `pg_restore` | dump 和 restore 都使用 `postgres.client_image`；本地缺失时输出拉取日志并拉取该精确 image。 |
| dump/restore 自动化覆盖 | 单元测试 | 覆盖 DSN 连接身份、`--database > DSN database > 失败`、无 target 错误、native command 构造和输出目录；不再维护手工 dump/restore 集成测试。 |

## Backup and restore parameter inventory

本清单记录当前 CLI、运行时对象和原生客户端实际使用的参数，用于区分“本次操作输入”与“可持久化的应用配置”。本任务的代码变更范围为 MySQL 配置收敛；PostgreSQL 条目是同一配置边界的对照，不在本任务中修改。

### MySQL

| 操作 | CLI 参数 | 分类 | 当前用途 | 全局配置结论 |
| --- | --- | --- | --- | --- |
| dump / restore | `--dsn`、`--dsn-env` | 连接身份 | 提供 host、port、user、password 和可选 database。二者必须恰好提供一个。 | 不配置；每次调用显式提供。 |
| dump / restore | `--database TARGET` | 目标 | 覆盖 DSN 中的 database；目标解析固定为 `--database > DSN database > 失败`。 | 不配置；目标必须由本次调用决定。 |
| dump | `--output PATH` | 制品路径 | 指定 SQL 输出文件；未传时使用默认命名。 | 文件路径不配置；保留默认目录 `mysql.output_directory`。 |
| dump | `--archive` | 执行策略 | dump 完成后在本地 gzip 压缩。 | 保持显式 CLI 开关。 |
| dump | `--skip-definer` | 执行策略 | 生成 SQL 时去除 definer。 | 保持显式 CLI 开关。 |
| restore | `--input FILE` | 制品输入 | 指定 SQL 或 gzip 归档文件。 | 不配置；必须由本次恢复指定。 |
| dump / restore | 无 CLI 参数 | 客户端后备 | 本机 client 不可用时运行 Docker native client。 | 配置为共享的 `mysql.client_image`。 |

MySQL runtime 参数为 `MysqlDumpOptions(host, port, user, password, database, output, archive, skip_definer, automatic_output)` 与 `MysqlRestoreOptions(host, port, user, password, input, database)`。它们由本次 DSN、CLI 参数及默认输出目录解析得到，不是持久连接配置。实施后 dump/restore 的 Docker fallback 从共享 `DumpRestoreConfig.client_image` 取得 image，但不将 image 暴露为每次 CLI 参数。

| 操作 | 原生客户端调用 |
| --- | --- |
| dump | `mysqldump [-C] [-h HOST] [-P PORT] -u USER -B DATABASE --no-create-db [--skip-definer] -R -E --set-gtid-purged=OFF --skip-lock-tables -r OUTPUT` |
| restore | `mysql [-h HOST] [-P PORT] -u USER --database DATABASE < INPUT` |

MySQL 密码仅通过 `MYSQL_PWD` 环境变量传递，不进入 argv。`--archive` 是 dbtalk 的本地压缩行为，不是 `mysqldump` 原生参数；gzip restore 会先解压到临时文件。restore 会拒绝 SQL 中的 `CREATE DATABASE` / `DROP DATABASE`，并将顶层 `USE` 重写为已解析的目标库。Docker fallback 使用 `mysql.client_image` 的精确 image；本地缺失时输出 `docker pull` 日志并拉取该 image。

### PostgreSQL

| 操作 | CLI 参数 | 分类 | 当前用途 | 全局配置结论 |
| --- | --- | --- | --- | --- |
| dump / restore | `--dsn`、`--dsn-env` | 连接身份 | 提供 host、port、user、password、database 与 libpq query（例如 `sslmode`）。二者必须恰好提供一个。 | 不配置；每次调用显式提供。 |
| dump / restore | `--database TARGET` | 目标 | 覆盖 DSN 中的 database；省略时使用 DSN database；两者都没有时失败。 | 不配置；目标必须由本次调用决定。 |
| dump | `--output PATH` | 制品路径 | 指定 custom archive 输出文件或目录。 | 文件路径不配置；保留默认目录 `postgres.output_directory`。 |
| dump | `--compression-level 0..9` | 执行策略 | 传递给 native custom archive 的压缩级别；省略时使用 `pg_dump` 默认值。 | 保持显式 CLI 参数。 |
| restore | `--input FILE` | 制品输入 | 指定 PostgreSQL custom archive。 | 不配置；必须由本次恢复指定。 |
| restore | `--clean` | 执行策略 | 恢复前删除目标库中与 archive 对应的对象。 | 保持显式 CLI 开关。 |
| restore | `--if-exists` | 执行策略 | 与 `--clean` 一起忽略待删除对象不存在的情形；单独使用会失败。 | 保持显式 CLI 开关。 |
| restore | `--preserve-owner` | 执行策略 | 恢复 archive owner；默认传 `--no-owner`。 | 保持显式 CLI 开关。 |
| restore | `--preserve-privileges` | 执行策略 | 恢复 archive ACL；默认传 `--no-privileges`。 | 保持显式 CLI 开关。 |
| restore | `--jobs N` | 执行策略 | 传递给 `pg_restore` 的并行 job 数，最小为 1。 | 保持显式 CLI 参数。 |

PostgreSQL runtime 参数为 `PostgresDumpOptions(connection, output, client_image, compression_level)` 与 `PostgresRestoreOptions(connection, input, client_image, clean, if_exists, preserve_owner, preserve_privileges, jobs)`。其中 `connection` 含 host、port、user、password、database 和 DSN query；它是本次调用的连接状态，不是全局连接配置。

| 操作 | 原生客户端调用 |
| --- | --- |
| dump | `pg_dump --format=custom --file OUTPUT --dbname LIBPQ_URI [--compress=0..9]` |
| restore | `pg_restore --dbname LIBPQ_URI --exit-on-error [--clean] [--if-exists] [--no-owner] [--no-privileges] [--jobs N] INPUT` |
| restore 预检 | `pg_restore --list INPUT` |

本地执行时 PostgreSQL 密码写入临时 `PGPASSFILE`，Docker fallback 时通过 `PGPASSWORD` 环境变量传递，均不进入 argv。`postgres.client_image` 是在本地没有 `pg_dump` / `pg_restore` 且未复用已映射 PostgreSQL 容器时所需的 Docker client image，因此保留为应用配置；它不是连接、目标或恢复策略。

## Cross-engine configuration rule

| 配置类别 | MySQL | PostgreSQL | 结论 |
| --- | --- | --- | --- |
| 默认 dump 制品目录 | `mysql.output_directory` | `postgres.output_directory` | 两者都保留。它决定未传 `--output` 时的本地制品位置，不决定单次输出文件名。 |
| Docker native client image | `mysql.client_image` | `postgres.client_image` | 两者都保留。它只在已映射容器和本机 client 均不可用时使用；本地缺失时输出拉取日志并拉取该精确 image。 |
| JSONL 零日期规范化 | `mysql.zero_datetime_as_null` | 不适用 | 只由 MySQL export 的 `DATE`、`DATETIME`、`TIMESTAMP` 编码路径读取。 |
| host、port、user、password、database | 不配置 | 不配置 | 全部来自每次调用的 DSN，database 还可由本次 `--database` 覆盖。 |
| input / output 文件路径 | 不配置 | 不配置 | 属于本次备份或恢复的制品。 |
| archive、skip-definer、compression-level、clean、if-exists、owner/ACL、jobs | 不配置 | 不配置 | 都是影响单次制品或恢复破坏范围的显式 CLI 策略。 |

`mysql` 与 `postgres` 分别持有不同配置值，但都绑定同一个 `DumpRestoreConfig`。共享的范围仅限 typed config、非空校验和 Docker fallback image 解析；MySQL 的 `mysqldump` / `mysql` 命令参数和 PostgreSQL 的 `pg_dump` / `pg_restore` 命令参数仍是各自引擎的实现。

## Time policy configuration

只有 `database query` 与 `database exec` 的单条 SQL deadline 是 `dbtalk` 应用运行设置。协议默认端口、格式版本、MySQL 进度日志采样间隔、tooling 外部 CLI 等实现或开发工具细节不进入 `dbtalk` 设置。两个 timeout 由唯一的 `Settings` loader 在启动时读取并校验，业务代码只能消费 typed settings，不能再定义数值默认。

| 配置 | 默认值 | 生效范围 | 覆盖方式 |
| --- | --- | --- | --- |
| `database.query_timeout_seconds` | `30` | `database query` 的单条 SQL deadline；MySQL 的 driver I/O timeout 同步使用该值。 | `DBTALK_DATABASE__QUERY_TIMEOUT_SECONDS` 或 `database query --timeout`。 |
| `database.exec_timeout_seconds` | `30` | `database exec` 的单条 SQL deadline；MySQL 的 driver I/O timeout 同步使用该值。 | `DBTALK_DATABASE__EXEC_TIMEOUT_SECONDS` 或 `database exec --timeout`。 |

当前 dump 子进程没有时间上限，因此本任务不凭空引入新的 dump/restore command timeout 配置。

## Configuration disposition

| 配置 | 处置 | 原因 |
| --- | --- | --- |
| `mysqldump.host`、`port`、`user`、`password`、`database` | 删除 | 连接和 target 必须由本次命令的 DSN 与 `--database` 提供。 |
| `mysqldump.output_directory` | 迁移为 `mysql.output_directory` | 保留其默认制品目录职责，同时去除只覆盖 dump 的旧配置组名。 |
| `mysqlrestore.*` | 删除整个组 | 连接和 target 无法作为配置；Docker client image 改由共享的 `mysql.client_image` 提供。 |
| `mysql.output_directory` | 新增并保留 | 未传 dump `--output` 时的默认本地制品目录。 |
| `mysql.client_image` | 新增并保留 | dump/restore Docker fallback 的精确 native client image；本地缺失时拉取该 image。 |
| `mysql.zero_datetime_as_null` | 迁移并保留 | MySQL JSONL export 对完整零日期的明确转换契约；不适用于 PostgreSQL 或 SQLite。 |
| `verbose`、`logging.*` | 保留 | 全局诊断行为，与数据库连接无关。 |
| `database.query_timeout_seconds` | 保留 | 是 `database query` 的默认单语句超时。 |
| `database.exec_timeout_seconds` | 保留 | 是 `database exec` 的默认单语句超时。 |
| `postgres.output_directory` | 保留 | PostgreSQL 默认 dump 制品目录；与 `mysql.output_directory` 对等。 |
| `postgres.client_image` | 保留 | PostgreSQL dump/restore Docker fallback image；与 `mysql.client_image` 对等。 |

## Acceptance

1. `DumpRestoreConfig` 只包含 `output_directory` 与 `client_image`；`Settings.postgres` 直接使用该类型，`Settings.mysql` 使用继承它的 `MySQLConfig` 并追加 `zero_datetime_as_null`。参数化 loader 对两个配置组执行共享字段的同一非空校验。
2. `MySQLDumpConfig`、`MySQLRestoreConfig`、`PostgresConfig`、`load_mysql_dump_config`、`load_mysql_restore_config`、`load_postgres_config`、`Settings.mysqldump` 和 `Settings.mysqlrestore` 被删除；应用运行不再读取任何旧 MySQL 组值。
3. `dbtalk.yaml` 与 `.env.example` 公开 `mysql.output_directory`、`mysql.client_image`、`mysql.zero_datetime_as_null`，对应环境变量为 `DBTALK_MYSQL__OUTPUT_DIRECTORY`、`DBTALK_MYSQL__CLIENT_IMAGE`、`DBTALK_MYSQL__ZERO_DATETIME_AS_NULL`；不再公开 `DBTALK_DATABASE__ZERO_DATETIME_AS_NULL`、`DBTALK_MYSQLDUMP__*` 或 `DBTALK_MYSQLRESTORE__*`。
4. `mysql dump` 和 `mysql restore` 的连接身份及 target 不受 YAML、dotenv 或进程环境中的已删除配置字段影响；它们继续只按 `--dsn` / `--dsn-env` 和 `--database > DSN database > 失败` 工作。
5. 未命中已映射容器且本机 client 不可用时，MySQL 与 PostgreSQL dump 和 restore 都使用各自的 configured image；本地缺失时输出拉取日志并 pull 该 image；不得扫描或选择其他 `mysql:*` image。
6. `tests/test_settings.py`、`tests/test_unit_boundaries.py`、MySQL 与 PostgreSQL dump/restore 测试不再构造或断言已删除字段或重复配置类型；覆盖两个引擎的目录/image 配置覆盖、MySQL 零日期配置覆盖，以及各自 dump/restore 使用同一精确 fallback image。
7. 删除 `tests/test_manual_integration.py`；不新增 `DBTALK_DSN_IT_MYSQL`、target database 环境变量或其他仅用于 dump/restore 的手工集成测试配置。
8. 活跃用户文档和 CLI help 不再描述被删除的 MySQL dump/restore 配置；历史过程文档不为此改写。`plugins/dbtalk` 的同步不属于本任务。
9. 实现后通过项目统一入口运行 `make check` 与 `make test`；不单独手工格式化文件。
10. Requirement 保留 MySQL 与 PostgreSQL 的 dump/restore CLI、runtime、native client 参数清单，并明确区分本次操作输入、执行策略和持久化配置边界。
11. `database.query_timeout_seconds` 与 `database.exec_timeout_seconds` 只在 `dbtalk.yaml` / 对应环境变量中定义默认值，typed loader 分别执行正数校验；源码不再定义共享的 operation timeout 常量。

## Open questions

暂无需要用户确认的未决事项。

Dynaconf 对未被 typed loader 读取的旧 YAML 或环境变量键可能保持静默。该行为不构成兼容支持：本任务不再公开、加载或使用这些字段，也不引入全局未知配置键校验。若将来需要对任意拼写错误或历史键 startup fail，应作为独立的配置 schema 校验任务处理，避免把本次 MySQL 清理扩大为全局配置行为变更。

## Decisions

- dump/restore 的连接配置、制品存储配置和 client 环境配置分离：删除连接和 target，保留默认目录与 client image。
- `mysql` 与 `postgres` 两个引擎配置组共享 `DumpRestoreConfig(output_directory, client_image)`；`mysql` 通过 `MySQLConfig` 追加仅由 MySQL JSONL export 使用的 `zero_datetime_as_null`。不再保留仅限 dump 命名的 `mysqldump` 或空的 `mysqlrestore` 组，也不保留重复的 `PostgresConfig` 类型。
- MySQL Docker fallback 的 image 必须显式、稳定且对 dump/restore 一致；保留现有“mapped container -> 本机 client -> Docker fallback”执行优先级，不自动拉取 image。
- `output_directory` 与 `client_image` 是 MySQL/PostgreSQL 对等的跨调用环境配置，字段、校验与 fallback 契约共用一套实现；配置值和 native command 参数才按引擎区分。不将任一引擎的连接、target 或执行策略写入全局配置。
- MySQL 的默认 Docker client image 为 `mysql:8.0.39`；必要时由 `mysql.client_image` 显式覆盖。PostgreSQL 的既有配置值和默认策略不在本任务中重定。
- 不提供兼容别名、旧值迁移、运行时 warning 或 target 回退；当前活跃开发阶段直接收敛到显式 DSN 设计。
- 历史过程文档记录旧设计，不为配置删除回写；仅更新活跃用户文档、配置样例和测试。
- PostgreSQL 的 `output_directory` 与 `client_image` 继续保留；其连接、target、输入/输出文件和备份/恢复策略也沿用逐次 CLI 输入，不扩展为全局默认值。本任务只记录这一边界，不修改 PostgreSQL 配置或行为。
- query 与 exec 的 SQL deadline 分别作为应用设置，二者没有共享默认项；不为 MySQL 进度日志或 tooling 外部 CLI 新增应用配置。

## Risk

- CLI 不加载 `.env.local`；运行时配置只能通过 `dbtalk.yaml` 和 `DBTALK_*` 进程环境变量提供。官方样例、文档和测试必须明确这一输入边界。
- settings 数据类收敛会影响所有直接构造 `MySQLDumpConfig`、`MySQLRestoreConfig`、`PostgresConfig` 或读取旧 `Settings` 字段的调用点；实施前必须完成全仓搜索并以类型检查与测试验证。
- 将 MySQL fallback 改为精确 image 后，原先仅安装非默认 `mysql:*` image 的环境会得到“配置 image 不存在”的明确失败，而不再隐式选中偶然存在的 image；用户须通过 `mysql.client_image` 显式选择兼容版本。
- 删除手工集成测试后，native dump/restore 与目标解析的真实服务成功路径只保留现有单元测试覆盖；本任务不创建测试库、不要求测试凭据，也不对已有库执行 dump/restore。

## User review notes

用户确认：配置删除应作为独立 SpecFlow 任务完成；dump store 目录应保留并在本任务结束后再讨论更广泛的存储设计。连接 host、port、user、password、database 没有保留意义。移除 dump/restore 手工集成测试，保留单元测试覆盖。用户要求使用项目统一 `make check` / `make test` 入口，不进行额外手工格式化。用户要求将 MySQL 与 PostgreSQL dump/restore 的完整参数清单、native client 映射和配置边界纳入本 Requirement，作为后续配置保留/删除决策的依据。用户进一步确认：目录与 client image 的配置逻辑是一套，MySQL 与 PostgreSQL 仅配置值和 native client 参数不同。两者都保留 `output_directory` 与 `client_image`，并共享同一个 typed config/loader；其余参数保持每次 CLI 输入。用户进一步确认：MySQL 零日期规范化只对 MySQL 生效，配置移至 `mysql.zero_datetime_as_null`；默认 MySQL Docker client image 固定为 `mysql:8.0.39`。用户进一步确认：query 与 exec timeout 分别进入 `dbtalk` 设置；MySQL 进度日志采样与 tooling 外部 CLI timeout 均不纳入 `dbtalk` 设置。
