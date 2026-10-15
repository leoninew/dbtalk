# 数据库操作手册

`dbtalk query`、`dbtalk exec` 提供通用 SQL，`dbtalk export`、`dbtalk import` 用 JSONL 在既有 SQLite、MySQL 与 PostgreSQL schema 之间传输表数据。所有连接入口都使用明确的 SQLAlchemy 2.x DSN：CLI 接受二选一的 `--dsn DSN` 或 `--dsn-env NAME`，Python API 接受 DSN 字符串或环境变量名。`--dsn` 保留给直接集成；Agent 必须将 DSN 规划为当前目录 `.env` 中的 `DBTALK_DSN_*`，并且只使用 `--dsn-env`。不会根据数据库类型猜测 driver。

```bash
uv run dbtalk --help
uv run dbtalk export --help
uv run dbtalk import --help
uv run dbtalk query --help
uv run dbtalk exec --help
```

## DSN 约定

支持的 DSN 必须使用以下明确形式：

```text
sqlite:///./data/app.db
sqlite:////absolute/path/app.db
mysql+pymysql://user:password@host:3306/app
mysql+asyncmy://user:password@host:3306/app
postgresql+psycopg://user:password@host:5432/app
```

`mysql://`、`postgres://`、`postgresql://`、Go 风格 `user:password@tcp(...)` 和数据库类型专用文件参数都不属于 canonical DSN，命令会拒绝。Agent 将 DSN 写入当前目录 `.env` 的 `DBTALK_DSN_*` 条目，避免出现在进程参数中；`.env.local` 或其他 dotenv 变体不会被加载。

MySQL 与 PostgreSQL URL 的末尾 database path 在语法上可省略，例如 `mysql+pymysql://user:password@host:3306/`。`query` 与 `exec` 可将这种 URL 原样交给 driver；`export` 与 `import` 必须在 DSN 中明确带有 database name，避免读写 driver 默认库。SQLite 的 database path 始终必填。

## Query / Exec

SQL 使用 SQLAlchemy named bind 参数。参数格式为可重复的 `NAME=JSON_VALUE`，字符串需要使用 JSON 字符串表示。两个命令都有 `--timeout` / `-t`，单位为秒，只接受正整数。`query` 省略时使用 `database.query_timeout_seconds`，`exec` 省略时使用 `database.exec_timeout_seconds`，两者默认均为 `30`。该 timeout 仅针对当前单条语句。需要限制建立 MySQL 或 PostgreSQL 连接的时间时，可在单次调用传入 `--connect-timeout SECONDS`；它不设置 SQL deadline，也没有全局默认值。

先将应用 DSN 写入当前目录 `.env`：

```dotenv
DBTALK_DSN_APP=sqlite:///./data/app.db
```

```bash
uv run dbtalk query \
  --dsn-env DBTALK_DSN_APP \
  --sql 'SELECT id, name FROM users WHERE id = :id' \
  --param id=1 \
  --format table

uv run dbtalk query \
  --dsn-env DBTALK_DSN_APP \
  --sql 'SELECT id, name FROM users WHERE id = :id' \
  --param id=1 \
  --format json

uv run dbtalk exec \
  --dsn-env DBTALK_DSN_APP \
  --sql 'UPDATE users SET name = :name WHERE id = :id' \
  --param 'name="Ada"' \
  --param id=1

uv run dbtalk exec \
  --dsn-env DBTALK_DSN_APP \
  --file ./data/update.sql

uv run dbtalk exec \
  --dsn-env DBTALK_DSN_APP \
  --file ./data/update.sql \
  --dry-run
```

`query` 默认输出 `table`，也可使用 `--format json`。JSON 输出包含 `columns`、`rows` 和 `row_count`；日期时间、Decimal 和 BLOB 会转换为 JSON-safe 值。`query` 会开启数据库的只读会话；它不分析 SQL 文本，因此写入、DDL 或其他被数据库认定为写入的语句会由数据库拒绝。`exec` 使用写会话执行 SQL，并输出影响行数；它不识别 DML，实际能否写入由数据库授权决定。只读查询必须走 `query`。`--sql` 在传入 `--param` 时使用 SQLAlchemy 命名绑定；未传参数时按字面量执行，不会识别 SQL 字符串中的 `:name` 或 `:null`。`--file` 读取 UTF-8 脚本，按字面量在同一写事务中执行其中的语句，忽略脚本里的 `START TRANSACTION` / `COMMIT` / `BEGIN` / `ROLLBACK`，且不能与 `--sql` 或 `--param` 同时使用。`--dry-run` 只打印将要执行的每条 SQL，不连接数据库、不写入。

根命令接受 `-v/--verbose`，例如 `dbtalk -v exec ...`。开启后按配置级别输出 stderr 操作日志，并在失败时追加清洗后的异常细节（异常类型与消息，不含 traceback、SQL 文本或绑定参数）。默认不输出 logging 日志，但仍显示普通错误提示；子命令及中间 group 不提供 `-v`。详见[操作日志](logging.md)。

超时使用数据库或驱动的原生能力：SQLite 使用 progress handler 和 `busy_timeout`，PostgreSQL 使用事务本地 `statement_timeout`，MySQL 使用 `max_execution_time`，并配置 PyMySQL 读写 socket 超时。MySQL 对超时的写语句会中断当前连接；事务型表会由数据库回滚未提交事务，但非事务性语句和带隐式提交的 DDL 仍遵循 MySQL 原生语义，不能承诺完整回滚。

默认超时配置位于 `dbtalk.yaml`，可由环境变量覆盖：

```yaml
database:
  query_timeout_seconds: 30
  exec_timeout_seconds: 30
```

```bash
export DBTALK_DATABASE__QUERY_TIMEOUT_SECONDS='15'
export DBTALK_DATABASE__EXEC_TIMEOUT_SECONDS='45'
```

同步 Python API 为 `DatabaseClient`，异步 API 为 `AsyncDatabaseClient`；异步 API 会按 async driver 建立 SQLAlchemy async engine，不向调用方暴露原生 DBAPI 连接。

## Export

export 读取选定源 schema，将表元数据和行数据写入一个 JSONL 文件：

为传输源单独规划变量名，并写入当前目录 `.env`：

```dotenv
DBTALK_DSN_SOURCE=postgresql+psycopg://user:password@host:5432/source_db
```

```bash
uv run dbtalk export \
  --source postgresql \
  --dsn-env DBTALK_DSN_SOURCE \
  --output ./data/transfer.jsonl \
  --include-table users \
  --exclude-table audit_log
```

| 选项 | 说明 |
| --- | --- |
| `--source sqlite|mysql|postgresql` | 必填，必须与 DSN dialect 一致。 |
| `--dsn DSN` / `--dsn-env NAME` | 必须二选一；MySQL/PostgreSQL DSN 必须明确带有 database name。 |
| `--output FILE_OR_DIRECTORY` | 可选的 JSONL 输出文件，或已有的输出目录。 |
| `--tz IANA_NAME` | 无时区日期时间的解释时区，默认 `UTC`。 |
| `--include-table NAME` | 限定导出的表，可重复指定。 |
| `--exclude-table NAME` | 排除表，可重复指定。 |
| `--archive` | 写入 gzip 压缩 JSONL。 |

表按外键父表到子表顺序写入。只选择子表而未选择所需父表，或遇到外键环时会预检失败；工具不会自动创建 schema、补充表或关闭外键检查。大表固定按每批 1000 行循环读取，不使用 `fetchall()`。

PostgreSQL export 显式启用数据库端流式游标，SQLite 使用本地游标分批取数；结果集在完成或失败时关闭。行数据逐行编码并写入临时文件，全部完成后原子替换输出制品。

省略 `--output` 时创建当前目录的 `data/` 并生成 `<source>-<timestamp>.jsonl`。传入 `--archive` 时生成 `.jsonl.gz`。显式 `--output` 指向已有目录时，在该目录生成时间戳文件；其他路径视为文件路径，父目录必须已经存在。

## Import

import 将 JSONL 制品写入既有目标 schema：

为传输目标使用独立名称，不能复用来源 DSN：

```dotenv
DBTALK_DSN_TARGET=mysql+pymysql://user:password@host:3306/target_db
```

```bash
uv run dbtalk import \
  --target mysql \
  --dsn-env DBTALK_DSN_TARGET \
  --input ./data/transfer.jsonl.gz \
  --mode insert
```

| 选项 | 说明 |
| --- | --- |
| `--target sqlite|mysql|postgresql` | 必填，必须与 DSN dialect 一致。 |
| `--dsn DSN` / `--dsn-env NAME` | 必须二选一；MySQL/PostgreSQL DSN 必须明确带有 database name。 |
| `--input FILE` | 必填的 JSONL 或 gzip JSONL 输入文件。 |
| `--mode insert|upsert` | 必填的写入模式。 |
| `--tz IANA_NAME` | 写入无时区日期时间时的时区，默认 `UTC`。 |
| `--include-table NAME` | 限定导入表，可重复指定。 |
| `--exclude-table NAME` | 排除表，可重复指定。 |

写入前会扫描整个制品，校验结构、目标表、列类型、完整主键和外键顺序。`upsert` 命中主键时更新，未命中时插入；不会清空表，也不使用 `REPLACE` 或 `DELETE`。每个表块是一个事务，失败表块回滚，已提交的前序表块不会自动回滚。SQLite 导入后还会执行完整性和外键检查。

PostgreSQL、SQLite 的首遍预检还会对选中数据执行目标类型转换检查；第二遍回放时转换并批量绑定参数。每批最多 1000 行，并按转换后的参数负载估算 8 MiB 字节预算；超预算的单行独立执行。预算不等于进程内存硬上限，最大单行仍会影响内存。批次不提交事务，整表成功才提交。PostgreSQL 通过 psycopg 3 的 `executemany` 在驱动支持时自动使用 pipeline。

两种目标的 `upsert` 使用完整主键作为原生 `ON CONFLICT` 的冲突目标，按文件顺序执行，包括重复主键；只更新文件提供的非主键列。原生 upsert 会先尝试 INSERT，因此采用数据库的 INSERT/冲突处理约束和触发器语义。例如目标表额外存在无默认值的 NOT NULL 列，而 JSONL 未提供该列时，即使主键已有记录也可能失败。MySQL 保持原有执行方式。

## Transfer 进度日志

PostgreSQL、SQLite 在处理循环中约每 5 秒报告表级进度，并在表完成或回滚时报告最终状态。日志沿用现有文本格式与日志级别，写入 stderr，不改变 stdout 的命令结果；没有后台心跳线程，正在等待数据库响应时不会额外发出进度。

导入日志的 `executed_rows`、`total_rows` 和 `pending_rows` 针对当前表，`committed_rows` 为本次任务已提交的输入行总数。`status=pending` 表示执行成功但尚待表事务提交；`committed` 表示整表已提交；`rolled_back` 表示当前表回滚，待提交数清零，前序表已提交数保留。行数按成功执行批次的输入记录统计，不使用数据库影响行数。

导出日志报告当前表的 `written_rows`，`total_rows=unknown`，不额外查询全表行数。`table_completed` 表示表块写出完成；任务完成日志在输出制品原子替换后发出。两种进度均包含 `rows_per_second` 和 `elapsed_seconds`，按当前表的处理时间计算。

## JSONL 类型

BLOB 使用 base64 type tag，DECIMAL 使用 decimal type tag。无时区日期时间按 `--tz` 解释并规范化为 UTC ISO 8601 字符串。MySQL 零日期可由 `mysql.zero_datetime_as_null` 默认转换为 JSON `null`；设为 `false` 时遇到零日期会失败。PostgreSQL 普通表、主键、外键、常见类型和 JSONL transfer 由 SQLAlchemy Inspector 和对应 dialect 处理。导入到 `BOOLEAN` 列时，JSON 的 `true`/`false`、数值 `0`/`1` 及常见布尔文本会规范化为目标驱动可绑定的 Boolean；其他值会在写入前失败。
