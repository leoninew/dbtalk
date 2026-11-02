# 数据库操作手册

`dbtalk query/exec` 执行 SQL，`dbtalk export/import` 用 JSONL 在既有 SQLite、MySQL、PostgreSQL 与 SQL Server schema 之间传输表数据。连接使用 SQLAlchemy 2.x DSN；CLI 接受 `--dsn DSN` 或 `--dsn-env NAME`，Python API 接受 DSN 字符串或环境变量名。

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
mssql+pymssql://user:password@host:1433/app
```

DSN 必须显式指定 driver（SQLite 除外）。Agent 只用 `--dsn-env` 引用已有 `DBTALK_DSN_*`，示例变量名需替换为实际名称。连接选择和新增规则见 [Agent 操作边界](codex.md#操作边界)。

MySQL、PostgreSQL 与 SQL Server URL 的末尾 database path 在语法上可省略，例如 `mysql+pymysql://user:password@host:3306/`。`query` 与 `exec` 可将这种 URL 原样交给 driver；`export` 与 `import` 必须在 DSN 中明确带有 database name，避免读写 driver 默认库。SQLite 的 database path 始终必填。

## Query / Exec

SQL 使用 SQLAlchemy named bind 参数。参数格式为可重复的 `NAME=JSON_VALUE`，字符串需要使用 JSON 字符串表示。两个命令都有 `--timeout` / `-t`，单位为秒，只接受正整数。`query` 省略时使用 `database.query_timeout_seconds`，`exec` 省略时使用 `database.exec_timeout_seconds`，两者默认均为 `30`。该 timeout 针对单条语句（SQL Server 文件为单个批次）。需要限制建立 MySQL、PostgreSQL 或 SQL Server 连接的时间时，可在单次调用传入 `--connect-timeout SECONDS`；它不设置 SQL deadline，也没有全局默认值。

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

`query` 默认输出 `table`，也可使用 `--format json`。JSON 输出包含 `columns`、`rows` 和 `row_count`；日期时间、Decimal 和 BLOB 会转换为 JSON-safe 值。`query` 读取结果且不提交事务，`exec` 成功后提交并输出影响行数。SQLite、MySQL、PostgreSQL 的 `query` 另有数据库只读会话设置；所有引擎均不分析查询 SQL 文本。`--sql` 在传入 `--param` 时使用 SQLAlchemy 命名绑定；未传参数时按字面量执行，不会识别 SQL 字符串中的 `:name` 或 `:null`。`--file` 读取 UTF-8 脚本，使用同一写事务执行，不能与 `--sql` 或 `--param` 同时使用。非 SQL Server 文件沿用现有语句拆分并忽略独立的 `START TRANSACTION` / `COMMIT` / `BEGIN` / `ROLLBACK`。`--dry-run` 仅预览，不连接数据库、不写入；文件预览指定 DSN 时会离线解析以选择方言，单 SQL 预览沿用原有行为，不解析 DSN 或参数。

SQL Server 使用 `mssql+pymssql://`，仅支持同步客户端，uv 安装的驱动 wheel 无需系统 ODBC；不提供异步客户端或线程包装。`query` 和 `exec` 复用现有 SQLAlchemy 连接及事务调用，没有专用查询过滤器。SQL Server 查询连接退出时回滚未提交事务，但这不构成数据库权限只读。`pymssql` 的 `timeout` / `login_timeout` 设置有进程级影响，不承诺同进程并发连接的独立超时。

SQL Server 文件按独立行 `GO` 分批，保留批次内变量、分号和过程体，所有批次在同一连接执行。暂不支持 `GO n` 和 SQLCMD 指令。脚本显式写出的事务语句原样交给数据库，其提交或回滚遵循数据库语义。`exec` 会消费后续结果以传播批次错误，但混合 DML/查询批次的影响行数受驱动暴露的信息限制。执行和 dry-run 必须提供相同的 SQL Server DSN 才会使用同一分批方式；无 DSN 的 dry-run 沿用通用语句预览。

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
| `--source sqlite\|mysql\|postgresql\|mssql` | 必填，必须与 DSN dialect 一致。 |
| `--dsn DSN` / `--dsn-env NAME` | 必须二选一；MySQL/PostgreSQL/SQL Server DSN 必须明确带有 database name。 |
| `--output FILE_OR_DIRECTORY` | 可选的 JSONL 输出文件，或已有的输出目录。 |
| `--tz IANA_NAME` | 无时区日期时间的解释时区，默认 `UTC`。 |
| `--include-table NAME` | 限定导出的表，可重复指定。 |
| `--exclude-table NAME` | 排除表，可重复指定。 |
| `--archive` | 写入 gzip 压缩 JSONL。 |

表按外键父表到子表顺序写入。只选择子表而未选择所需父表，或遇到外键环时会预检失败；工具不会自动创建 schema、补充表或关闭外键检查。大表固定按每批 1000 行循环读取，不使用 `fetchall()`。

PostgreSQL export 显式启用数据库端流式游标，SQLite 使用本地游标分批取数；结果集在完成或失败时关闭。行数据逐行编码并写入临时文件，全部完成后原子替换输出制品。

省略 `--output` 时创建当前目录的 `data/` 并生成 `<source>-<timestamp>.jsonl`。传入 `--archive` 时生成 `.jsonl.gz`。显式 `--output` 指向已有目录时，在该目录生成时间戳文件；其他路径视为文件路径，父目录必须已经存在。

## Import

import 将 JSONL 制品写入既有目标 schema；执行前核对目标连接及写入授权：

```bash
uv run dbtalk import \
  --target mysql \
  --dsn-env DBTALK_DSN_TARGET \
  --input ./data/transfer.jsonl.gz \
  --mode insert
```

| 选项 | 说明 |
| --- | --- |
| `--target sqlite\|mysql\|postgresql\|mssql` | 必填，必须与 DSN dialect 一致。 |
| `--dsn DSN` / `--dsn-env NAME` | 必须二选一；MySQL/PostgreSQL/SQL Server DSN 必须明确带有 database name。 |
| `--input FILE` | 必填的 JSONL 或 gzip JSONL 输入文件。 |
| `--mode insert|upsert` | 必填的写入模式。 |
| `--tz IANA_NAME` | 写入无时区日期时间时的时区，默认 `UTC`。 |
| `--include-table NAME` | 限定导入表，可重复指定。 |
| `--exclude-table NAME` | 排除表，可重复指定。 |

写入前会扫描整个制品，校验结构、目标表、列类型、完整主键和外键顺序。`upsert` 命中主键时更新，未命中时插入；不会清空表，也不使用 `REPLACE` 或 `DELETE`。每个表块是一个事务，失败表块回滚，已提交的前序表块不会自动回滚。SQLite 导入后还会执行完整性和外键检查。

PostgreSQL、SQLite、SQL Server 的首遍预检还会对选中数据执行目标类型转换检查；第二遍回放时转换并分批绑定参数。每批最多 1000 行，并按转换后的参数负载估算 8 MiB 字节预算；超预算的单行独立执行。预算不等于进程内存硬上限，最大单行仍会影响内存。批次不提交事务，整表成功才提交。PostgreSQL 通过 psycopg 3 的 `executemany` 在驱动支持时自动使用 pipeline。

PostgreSQL、SQLite 的 `upsert` 使用完整主键作为原生 `ON CONFLICT` 的冲突目标，按文件顺序执行，包括重复主键；只更新文件提供的非主键列。原生 upsert 会先尝试 INSERT，因此采用数据库的 INSERT/冲突处理约束和触发器语义。例如目标表额外存在无默认值的 NOT NULL 列，而 JSONL 未提供该列时，即使主键已有记录也可能失败。MySQL 保持原有执行方式。

## Transfer 进度日志

PostgreSQL、SQLite、SQL Server 在处理循环中约每 5 秒报告表级进度，并在表完成或回滚时报告最终状态。日志沿用现有文本格式与日志级别，写入 stderr，不改变 stdout 的命令结果；没有后台心跳线程，正在等待数据库响应时不会额外发出进度。

导入日志的 `executed_rows`、`total_rows` 和 `pending_rows` 针对当前表，`committed_rows` 为本次任务已提交的输入行总数。`status=pending` 表示执行成功但尚待表事务提交；`committed` 表示整表已提交；`rolled_back` 表示当前表回滚，待提交数清零，前序表已提交数保留。行数按成功执行批次的输入记录统计，不使用数据库影响行数。

导出日志报告当前表的 `written_rows`，`total_rows=unknown`，不额外查询全表行数。`table_completed` 表示表块写出完成；任务完成日志在输出制品原子替换后发出。两种进度均包含 `rows_per_second` 和 `elapsed_seconds`，按当前表的处理时间计算。

## JSONL 类型

BLOB 使用 base64 type tag，DECIMAL 使用 decimal type tag。无时区日期时间按 `--tz` 解释并规范化为 UTC ISO 8601 字符串。MySQL 零日期可由 `mysql.zero_datetime_as_null` 默认转换为 JSON `null`；设为 `false` 时遇到零日期会失败。PostgreSQL 普通表、主键、外键、常见类型和 JSONL transfer 由 SQLAlchemy Inspector 和对应 dialect 处理。导入到 `BOOLEAN` 列时，JSON 的 `true`/`false`、数值 `0`/`1` 及常见布尔文本会规范化为目标驱动可绑定的 Boolean；其他值会在写入前失败。

## SQL Server JSONL

使用 `--source mssql` / `--target mssql`。传输仅处理连接默认 schema，跨 schema 外键会明确失败。先选表再校验类型；其他未选表的类型不会阻止操作。JSONL header 的来源为 `mssql`，旧版 reader 不识别该来源，须使用支持此来源的版本。

| 类型 / 列 | 支持范围 |
| --- | --- |
| tinyint/smallint/int/bigint、decimal/numeric、bit | 保持整数与十进制精度；bit 在制品中声明为 BOOLEAN。导入检查范围与精度。 |
| char/varchar/nchar/nvarchar（含 max） | 保持文本与 Unicode，检查目标长度和编码。 |
| binary/varbinary（含 max） | base64 编码，保留空值与空二进制的区别；固定长度 binary 不自动补齐。 |
| uniqueidentifier | 使用规范 UUID 文本，导入转换为 UUID 值。 |
| date、smalldatetime、datetime、datetime2(p)/time(p)，p ≤ 6 | 检查目标范围与精度；smalldatetime 为整分钟，datetime 为 SQL Server 毫秒刻度。 |
| datetime2(7)、time(7)、datetimeoffset | 暂不支持 JSONL 传输，元数据预检失败，避免驱动丢失精度或偏移。 |
| identity | 显式值在同连接中按表启闭 IDENTITY_INSERT；upsert 不更新 identity 列。省略时由目标生成。 |
| computed、rowversion/timestamp、generated always | 导出所选表含此类列时拒绝；导入不接受显式生成值，省略时由目标生成。 |
| 其他类型（例如 XML、空间、CLR、sql_variant） | 暂不支持，预检明确失败。 |

insert 使用分批参数；upsert 在表事务内按完整主键，以 `UPDLOCK, HOLDLOCK` 查询后执行更新或插入，按输入顺序处理。导入不建表、不授予 identity 权限，失败表回滚并清理 IDENTITY_INSERT 状态；已提交的前序表保留。

导出复用 `fetchmany(1000)` 和临时文件原子替换。pymssql 不支持 SQLAlchemy 服务端游标，分批读取不承诺驱动不缓冲结果集。上述类型限制仅适用于 JSONL，不限制用户通过 query/exec 提交 SQL。
