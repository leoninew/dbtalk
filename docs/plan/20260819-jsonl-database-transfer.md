# JSONL 数据库数据传输计划
最后修改时间: 2026-08-20 15:36:51

Review status: Accepted

## Basis

本计划实施已接受的 `docs/requirement/20260819-jsonl-database-transfer.md`。`dbtalk` 是 SQLite/MySQL 全库数据传输、JSONL 格式、数据库值转换、文档与通用 skill 的唯一所有者。它不会取代既有的 MySQL 原生 SQL dump/restore。

调用方通过 `dbtalk` CLI 进行跨项目调用，而不依赖其 Python library API。本计划中的实现路径、配置前缀、格式标识和验证入口均已调整为本项目约定。

## CLI Contract

新增 `dbtalk database` Click 命令组，独立于 `dbtalk mysql dump` / `restore`：

```text
dbtalk database export --source sqlite|mysql [--output <file-or-existing-directory>] [connection] [--exclude-table <table>]... [--tz <IANA timezone>]
dbtalk database import --target sqlite|mysql --input <file.jsonl> --mode insert|upsert [connection] [--exclude-table <table>]... [--tz <IANA timezone>]
```

省略 export 的 `--output` 时，创建当前目录 `data/` 并生成 `<source>-<timestamp>.jsonl`；已有目录路径生成同类制品。其他路径视为文件，父目录必须已经存在。

- SQLite 连接使用必填 `--sqlite-path`；连接信息仅用于本次命令，不复用 `mysqldump` / `mysqlrestore` 配置组。
- 为避免密码出现在进程参数中，MySQL 连接使用 `--mysql-dsn-env <ENV_NAME>` 从指定环境变量读取 DSN；DSN 解析至少兼容 Go 风格 MySQL DSN，并支持标准 `mysql://` 形式。不得把 DSN 明文写入日志或 skill 示例。
- `--tz` 默认 `UTC`，接收 IANA time zone 名称。导出时它解释源库中无偏移日期时间；导入时它既解释输入中仍无偏移的日期时间，也决定带 `Z`/显式偏移的 instant 在目标无时区 temporal 列中渲染的 wall-clock。它不改变 instant；`DATE` 与 `TIME` 不进行时区转换。
- `insert` 对每行执行普通插入，任何约束冲突都失败并回滚当前表块；`upsert` 必须按完整主键或联合主键更新既有行、插入新行，绝不清空表。
- `--exclude-table` 可在导出和导入命令中重复指定；未指定时处理全部表，指定的表在读取/预检前被过滤，排除项不存在时失败。
- 命令只报告路径、表数、行数和安全错误，绝不输出密码或 JSONL 数据内容。

## JSONL Contract

JSONL v1 使用有序记录，UTF-8 且每行一个 JSON object：

```json
{"kind":"header","format":"dbtalk.database-transfer/v1","source":"sqlite"}
{"kind":"table","name":"users","columns":[{"name":"id","declared_type":"INTEGER"},{"name":"created_at","declared_type":"DATETIME"}],"primary_key":["id"]}
{"kind":"row","values":[1,"2026-08-19T07:36:56Z"]}
{"kind":"end","rows":1}
```

- `header` 只能出现一次且位于首行；`table` 开始一个表块，`row` 只属于当前表块，`end` 结束该表块并声明实际行数。
- `columns` 以源表列顺序记录列名和声明类型；主键信息仅用于目标预检与冲突处理，绝不用于创建 schema。
- JSON 原生 `null`、string、number、boolean 直接保存；BLOB 与 Decimal 采用带明确 type tag 的 object。`DATE`、`TIME` 与日期时间均保存为 ISO 8601 string，日期时间规范为带 `Z` 的 UTC instant。
- 每个表块在写入前先校验完整结构、列唯一性、行长度、type tag 和 `end.rows`，整个输入也必须先完成 schema 与主键预检，之后才可以写入任一表块。

## Implementation Steps

1. 增加独立的数据库传输模块与运行时依赖。
   - 在 `src/dbtalk/database/` 新建数据库传输核心与 Click adapter；核心包含 JSONL reader/writer、表块模型、值编码/解码、时区处理、标识符校验、SQLite/MySQL driver adapter 与通用导入策略。
   - 将 PyMySQL 作为 MySQL 数据传输的显式运行时依赖，并更新 `uv.lock`；为 strict mypy 添加必要的类型依赖或局部类型协议。
   - 不从 `__init__.py` 暴露跨项目稳定 Python API，公开且长期稳定的边界是 `dbtalk database` CLI。

2. 实现 SQLite/MySQL metadata、全库导出和 JSONL 写入。
   - SQLite 使用只读连接、读事务、`PRAGMA integrity_check` 与 `foreign_key_check`；枚举普通表、列声明、主键及外键关系。
   - MySQL 从 `information_schema` 读取普通表、列声明、主键与外键关系，并在一致读事务中导出。
   - 按父表先于子表的依赖顺序输出表块；同表的稳定行序不擅自按非主键重排。对无法以表块顺序表达的跨表循环，在导出或导入预检中明确报错；同表自引用保持源行顺序。
   - 仅依据源列声明 `DATE`、`TIME`、`DATETIME`、`TIMESTAMP` 做 temporal 转换。无偏移日期时间按 `--tz` 解释后规范为 UTC `Z`；普通文本即使形似日期也不得改写。

3. 实现导入预检、表级事务与两个冲突策略。
   - 在数据库写入前完整读取并校验 JSONL，并验证每个目标表/列存在、表块主键与目标完整主键一致、`upsert` 所有主键列存在；无主键表拒绝 `upsert`，可用 `insert`。
   - 以目标 schema 的外键依赖顺序处理表块，并以每个表块为一个事务：失败时只回滚当前块，成功块保留并输出完成状态。前序成功块不会被宣称可整体回滚。
   - `insert` 使用 SQLite 与 MySQL 的普通 `INSERT`，约束冲突必须失败。`upsert` 以完整主键先匹配更新、未匹配再插入的方式实现，避免 MySQL 任意 unique key 冲突被误当作主键覆盖；不使用 `DELETE`、`REPLACE` 或清空表逻辑。
   - SQLite 在所有表块结束后保持 foreign keys 开启并运行完整性检查。MySQL 保持外键检查开启，不靠关闭检查掩盖顺序或完整性问题；遇到不可排序的跨表外键循环，在写入前失败。
   - MySQL temporal 目标列按精度安全格式化；日期时间先解析为 instant，再按导入 `--tz` 渲染为目标无时区列所需的值，确保默认 UTC 写入。显式偏移值的 instant 不被 `--tz` 改变，只改变最终 wall-clock 表示。

4. 接入 CLI、文档与通用 skill。
   - 在 `src/dbtalk/cli.py` 注册 `database` 命令组，补齐 `--help`、连接参数校验和非敏感错误输出。
   - 增加 `docs/database.md`，更新 README、`docs/mysql.md`（说明此命令不复用 MySQL dump/restore 配置）。
   - 新增 `plugins/dbtalk/skills/database/SKILL.md`，明确 target schema 必须预先初始化、`insert`/`upsert`、`--tz`、表级事务、敏感文件、不得把服务闭包当作通用功能。

5. 增加针对格式、驱动和 CLI 的定向测试并完成本机发布验证。
   - 用临时 SQLite 数据库覆盖全库导出、JSONL 表块、BLOB/Decimal/temporal 值、无偏移时区、日期/时间原样传输、insert、upsert、复合主键、缺失/不一致主键、缺表/缺列、当前表块回滚和导入后的完整性检查。
   - 用 MySQL fake connection/cursor 覆盖 information_schema metadata、参数化写入、insert/upsert 语句、时间精度和错误路径；不依赖真实 MySQL 服务。
   - 用 Click `CliRunner` 验证参数、help、错误输出和路径输出，不在测试日志写入连接密码或传输数据。
   - 执行 `make check`、`make test`、`uv run dbtalk database --help`、`make package`，确认项目 CLI、打包产物与 skill 可用。

## Files To Change

- `src/dbtalk/database/cli.py`、`transfer.py`、`models.py`、`format.py`、`schema.py`、`sqlite.py` 与 `mysql.py`。
- `src/dbtalk/cli.py`、`pyproject.toml`、`uv.lock`。
- `tests/test_database_transfer.py`、`tests/test_cli.py`、`tests/test_database_transfer_logging.py` 及必要的 settings 测试。
- `README.md`、`docs/database.md`、`docs/mysql.md`。
- `plugins/dbtalk/skills/database/SKILL.md` 和必要的 plugin metadata。
- `docs/requirement/20260819-jsonl-database-transfer.md`、本 Plan，后续 Verification 文档。

## Verification Plan

1. 导出临时 SQLite 数据库，逐行解析 JSONL，断言 header、表块、列声明、主键、行数、BLOB/Decimal 和 ISO 8601 值。
2. 将同一 JSONL 分别导入兼容的 SQLite schema，验证 insert 在冲突时失败、upsert 更新完整主键命中的行、复合主键和无主键错误。
3. 验证所有预检错误在第一个表块写入前报告；验证一个后续表块失败只回滚其自身事务并保留之前已提交表块。
4. 使用 fake MySQL 断言 metadata 查询、参数绑定、普通 `INSERT`、主键匹配更新与插入路径，以及 UTC/精度格式化。
5. 执行 `make check`、`make test`、CLI help 和 `make release` 后的外部 CLI/skill 可用性检查。

## Assumptions

- 当前阶段不追求海量数据；输入可在导入预检时完整读取，表块可在单个事务内执行。
- MySQL 目标 schema 和 IANA `--tz` 解析环境均已准备；本任务不自动创建数据库或安装客户端。
- `insert` 的普通插入语义遵循各数据库约束行为；任何约束冲突都应明确失败，而不是静默跳过。

## Risks

1. SQLite 的声明类型是应用约定而不是严格类型系统；未声明 temporal 类型的文本不能安全地自动规范为日期时间。
2. MySQL 与 SQLite 的额外 unique constraint 语义不同，因此 upsert 不使用 MySQL 的通用 duplicate-key shortcut，而采用完整主键匹配后更新/插入以保持需求含义。
3. 表块提交允许长传输局部成功；错误报告必须列出已提交的表及失败表，调用方据此处置。
4. JSONL 包含明文业务数据和可能的密钥；生成的临时/输出文件不能提交或在 CLI 输出中展开。

## Rollback

该任务不修改任何应用 schema 或数据迁移。部署前可通过回退 dbtalk 代码、重新构建或重新安装上一版本 CLI/skill；已实际导入的数据只能由操作者基于目标数据库备份或显式反向操作恢复，工具不提供隐式删除回滚。

## User Review Notes

- 调用方采用 CLI 作为与 dbtalk 的接口边界。
