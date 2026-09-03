# 放宽 DSN 数据库名称要求
最后修改时间: 2026-09-02 10:05:42

Review status: Accepted

## Background

当前通用 `parse_dsn` 对全部非 SQLite URL 强制要求 `url.database`。这使没有 database name 的 MySQL、PostgreSQL DSN 在驱动尝试连接之前即被拒绝。

用户确认，本次放宽适用于所有服务端数据库类型，而不是 MySQL 专属。是否必须显式提供数据库名称应由业务操作决定；通用 URL 解析器只负责 URL 结构、dialect、driver 和端口的有效性。SQLite 的 URL database 是文件或内存资源路径，不是服务端 database name，仍需保留其资源路径校验。

## Goal

- 允许 MySQL 与 PostgreSQL 的同步、异步 canonical DSN 省略 URL 中的 database name，并原样交给对应驱动建立连接。
- 由具体命令在需要明确目标 database、默认资源回退或安全比较时验证 `parsed.database`，而不是由 `parse_dsn` 无差别拒绝。
- 支持无库名 DSN 的实例连通性及运行时默认连接上下文，例如 `database query --sql 'SELECT 1'`。
- 保持显式 driver、dialect、port、`--dsn` / `--dsn-env` 二选一、凭据脱敏和“不由 dbtalk 猜测或注入默认 database”的既有约束。

## Non-goal

- 不移除 SQLite 所需的数据库文件或内存路径。
- 不改变各数据库驱动在 URL 未提供 database 时自身的默认连接规则。
- 不为 dump、restore、授权等有目标 database 语义的命令自动选择或替换目标。
- 不引入 URL 别名、host/user/password 分散参数或兼容层。

## User scenarios

下表的“必须”仅指 **DSN URL 是否必须显式包含 database name**，不表示数据库驱动运行时不需要任何连接上下文。

| 场景 | 命令 | DSN 中必须带 database name | 条件与原因 |
| --- | --- | --- | --- |
| 通用 SQL | `database query` | 否 | 可用于实例连通性探测，如 `SELECT 1`；实际连接上下文由驱动和服务端决定。 |
| 通用 SQL | `database exec` | 否 | 同 query；具体 SQL 是否需要已选库由服务端判定。 |
| JSONL 传输 | `database export` | 是 | 未提供独立目标库参数；必须由 DSN 明确源库，避免依赖驱动默认连接库。 |
| JSONL 传输 | `database import` | 是 | 未提供独立目标库参数；必须由 DSN 明确写入目标库，避免误写驱动默认连接库。 |
| MySQL 实例管理 | `mysql schema list` | 否 | 查看实例中的 database 列表。 |
| MySQL 实例管理 | `mysql schema create` | 否 | 创建目标通过 `--name` 提供。 |
| MySQL 实例管理 | `mysql schema drop` | 否 | 删除目标通过 `--name` 提供。 |
| MySQL 账号管理 | `mysql user list/create/enable/disable/rotate-password/drop` | 否 | 操作对象为 `user@host` account，不依赖 DSN database。 |
| MySQL 权限查看 | `mysql permissions list/show` | 否 | 查询原生授权视图或 `SHOW GRANTS`。 |
| MySQL 授权 | `mysql grant` / `mysql revoke` | 条件必需 | 显式给出 `--database` 时不需要；省略时仍使用 DSN database 作为授权目标，因此必须提供。 |
| MySQL 备份 | `mysql dump` | 条件必需 | 新增 `--database`；优先级为 `--database > DSN database > 失败`。不再使用 `mysqldump.database` 配置回退。 |
| MySQL 恢复 | `mysql restore` | 条件必需 | 优先级为 `--database > DSN database > 失败`。不再使用 `mysqlrestore.database` 配置回退。 |
| PostgreSQL 通用管理 | `postgres schema list` | 否 | 只需建立维护连接。 |
| PostgreSQL 通用管理 | `postgres schema create` | 否 | 创建目标由 `--name` 提供。 |
| PostgreSQL 通用管理 | `postgres schema drop` | 是 | 必须将管理连接的 database 与删除目标比较，防止删除当前维护库。 |
| PostgreSQL role 管理 | `postgres role list/create/enable/disable/rotate-password/drop` | 否 | 操作对象为 role，不读取 `parsed.database`。 |
| PostgreSQL 权限查看 | `postgres permissions list/show` | 否 | 查询当前实际连接数据库中的原生权限。 |
| PostgreSQL 授权 | `postgres grant` / `postgres revoke` | 条件必需 | 显式给出 `--database` 或 `--schema` 时不需要；两个资源参数都省略时以 DSN database 回退，因此必须提供。 |
| PostgreSQL 备份 | `postgres dump` | 条件必需 | 新增 `--database`；优先级为 `--database > DSN database > 失败`，native client 与自动输出名使用解析后的目标。 |
| PostgreSQL 恢复 | `postgres restore` | 条件必需 | 新增 `--database`；优先级为 `--database > DSN database > 失败`，native client 使用解析后的目标。 |
| SQLite | 所有 SQLite 命令 | 不适用 | SQLite DSN 必须提供数据库文件或内存资源路径；这不是服务端 database name。 |

## Runtime configuration disposition

本次主要任务先完成 DSN 与命令目标语义：所有 MySQL/PostgreSQL dump、restore 在运行时只按 `--database > DSN database > 失败` 解析目标，不得读取 YAML 或环境变量中的目标 database。连接 host、port、user、password 同样只来自每次命令提供的 DSN。

历史 MySQL 配置的保留、收缩或删除由主任务完成后的独立 SpecFlow 任务决定。本任务不预先承诺删除任何配置字段；只要求它们不得再作为 dump/restore 的连接身份或 target database 回退来源。

| 配置 | 处置 | 当前用途与判断 | 后续改动范围 |
| --- | --- | --- | --- |
| `verbose` | 保留 | 根 CLI 的诊断输出开关，与特定数据库连接无关。 | 不变。 |
| `logging.level`、`logging.format` | 保留 | 全局日志级别和格式，与命令目标无关。 | 不变。 |
| `database.zero_datetime_as_null` | 保留 | JSONL export 对 MySQL 零日期的明确数据转换契约。 | 不变。 |
| `database.operation_timeout_seconds` | 保留 | `database query` / `database exec` 的默认单语句超时。 | 不变。 |
| `mysqldump.output_directory` | 当前保留 | 未传 `mysql dump --output` 时的制品落盘目录，不参与连接或目标库选择。 | 当前任务不改动；后续独立任务再评估 dump 是否需要其他非连接配置。 |
| `postgres.output_directory` | 保留 | 未传 PostgreSQL dump `--output` 时的制品落盘目录。 | 不变。 |
| `postgres.client_image` | 保留 | PostgreSQL 本机 client 不可用时，选择本地 Docker client image；不承载连接身份或目标。 | 不变。 |
| `mysqldump.host`、`port`、`user`、`password`、`database` | 后续评估 | 它们不能再作为本任务 dump 的连接或 target database 回退；是否仍适合承担其他 dump 配置职责尚未决定。 | 独立任务按字段重新评估，不在本任务删除。 |
| `mysqlrestore.host`、`port`、`user`、`password`、`database`（整个 `mysqlrestore` 组） | 后续评估 | 它们不能再作为本任务 restore 的连接或 target database 回退；是否保留任何非连接配置由后续任务决定。 | 独立任务按字段重新评估，不在本任务删除。 |
| `scripts/backup_db.example.yaml` 的 target DSN | 保留 | 这是批量备份脚本的每目标输入，不是 `dbtalk.yaml` 运行时回退配置；每一项都明确给出自身 DSN 和目标。 | 不迁移、不删除。 |

后续配置任务应一并审查手工集成测试所需的环境变量，但当前不预设其最终名称或迁移方案。

## Acceptance

1. `parse_dsn` 接受省略 database name 的 MySQL 和 PostgreSQL canonical DSN；仍拒绝空 DSN、不支持 dialect、缺失 explicit driver 和无效端口。
2. SQLite DSN 仍必须包含数据库路径。
3. 无库名 MySQL 和 PostgreSQL DSN 能到达连接层；驱动或服务端连接失败不再被误报为通用 DSN 格式错误。
4. query、exec 及表中标注“否”的管理命令可省略 URL database name，不再仅因其缺失而失败；`database export` 与 `database import` 仍要求 DSN 明确带有 database name。
5. MySQL/PostgreSQL dump 与 restore 都以 `--database > DSN database > 失败` 决定目标；不得以 YAML 或环境变量配置的 database 回退。MySQL dump、PostgreSQL dump 和 PostgreSQL restore 提供 `--database`。
6. 所有标注“条件必需”或“是”的命令，在业务实际需要明确目标时返回专用、可操作的错误；不依赖通用解析器报错。
7. PostgreSQL `schema drop`、MySQL/PostgreSQL dump、restore 的安全与目标约束保持不变。
8. 文档、CLI help、测试与 `dbtalk` skill 说明“database name 在 DSN 中可选，命令可另行要求明确目标”，并且不回显凭据。

## Decisions

- “URL 是否包含 database name”与“业务是否需要明确 database 目标”分离处理。
- 对 MySQL、PostgreSQL 一视同仁地放宽 URL 语法；不再把 PostgreSQL 列为全局解析层例外。
- SQLite 的 `database` 字段表达资源路径，维持其非空约束。
- 不在 dbtalk 内推导默认库；省略 URL database 后的连接选择由数据库驱动及其标准配置决定。
- dump、restore 的目标库只由 `--database` 或 DSN database 给出，优先级固定为 `--database > DSN database > 失败`；运行时配置不得参与该决策。
- 本任务不改变配置 schema；后续独立任务再评估 dump/restore 配置的长期职责、保留项和删除项。无论配置最终形态如何，连接身份和 target database 不得恢复为配置回退来源。

## Open questions

- 暂无需要用户确认的范围决策。实现阶段应在可用的本地 MySQL 与 PostgreSQL 服务上分别验证无库名 `SELECT 1`；没有对应服务时需记录为集成验证缺口。

## Risk

- 放宽解析后，所有依赖 `parsed.database` 的业务路径必须具备本地、语义化校验，避免把必要前置条件延后为模糊的驱动错误。
- PostgreSQL 无库名连接使用驱动默认数据库；对 schema 级授权、权限列举与 transfer 的实际连接上下文需用集成测试确认。
- 当前文档将 canonical DSN 都写成带 database name 的示例，实施时必须同步调整为可选段说明，避免新旧契约冲突。
