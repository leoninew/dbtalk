---
name: dbtalk
description: 使用当前目录 `.env` 中的 `DBTALK_DSN_*` 和 `dbtalk query/exec/export/import` 执行通用 SQL 及 JSONL 数据导入导出。
---

# dbtalk Database Operations

要求发布安装的 `dbtalk` 可执行文件位于 `PATH` 中。

## Agent DSN workflow

Agent 只使用 `--dsn-env NAME`：

1. 优先使用用户指定的变量；否则从进程环境或当前目录 `.env` 中，按引擎、服务器、目标库和账号权限选择已有 `DBTALK_DSN_*`。
2. 保留原名，将示例名替换为实际名称，不复制连接或创建别名。传输时分别核对源端和目标端。
3. 无适用连接时，用用户提供或授权配置的连接信息向当前目录 `.env` 新增 `DBTALK_DSN_*` 条目，保留已有内容。目标或凭据不明时询问用户。

同名进程变量优先；仅在变量不存在时，`DBTALK_DSN_*` 才从当前目录 `.env` 读取。空值报错，不搜索其他 dotenv 文件。用户要求在脚本中声明变量时，使用进程变量，不写入 dotenv。

不输出真实 DSN 或密码，也不将其写入命令参数、日志、文档或 `.env.example`。

CLI 使用以下 DSN 格式：

```text
sqlite:///./data/app.db
mysql+pymysql://user:password@host:3306/database
postgresql+psycopg://user:password@host:5432/database
mssql+pymssql://user:password@host:1433/database
```

MySQL、PostgreSQL 与 SQL Server URL 可以省略末尾 database path。`query` / `exec` 可以使用无库名 DSN；`export` / `import` 必须在 DSN 中明确带有 database name。SQLite 的 database path 始终必填。

## Query / Exec

```bash
dbtalk query \
  --dsn-env DBTALK_DSN_APP \
  --timeout 30 \
  --sql 'SELECT id, name FROM users WHERE id = :id' \
  --param id=1 \
  --format json

dbtalk exec \
  --dsn-env DBTALK_DSN_APP \
  --timeout 30 \
  --sql 'UPDATE users SET name = :name WHERE id = :id' \
  --param 'name="Ada"' \
  --param id=1

dbtalk exec \
  --dsn-env DBTALK_DSN_APP \
  --timeout 120 \
  --file ./data/update.sql

dbtalk exec \
  --dsn-env DBTALK_DSN_APP \
  --file ./data/update.sql \
  --dry-run
```

`query` 默认输出表格，`--format json` 输出 `columns`、`rows`、`row_count`。SQLite/MySQL/PostgreSQL 使用数据库只读会话，不分析 SQL 文本；写入或 DDL 由数据库拒绝。SQL Server 的差异见下段。`exec` 使用写会话；实际能否写入由数据库判断。参数格式为可重复的 `NAME=JSON_VALUE`。`--sql` 在传入 `--param` 时使用命名绑定；未传参数时按字面量执行，不会识别 SQL 字符串中的 `:name` 或 `:null`。执行 SQL 文件时用 `--file`，不要用 `mysql restore`，也不要把文件内容塞进 `--sql`。`--file` 按字面量执行脚本中的语句，不能与 `--sql` 或 `--param` 同时使用。`--dry-run` 只打印每条 SQL，不执行。默认不输出 logging 日志。需要操作日志和清洗后的异常细节时，在根命令提供 `-v/--verbose`，例如 `dbtalk -v query ...`；日志写入 stderr，子命令不接受该开关。

SQL Server 使用同步 `mssql+pymssql://`，无需系统 ODBC。`query` 读取结果且不提交，`exec` 成功后提交；两者复用现有 SQLAlchemy 调用，不进行 SQL 语法过滤。SQL Server 未设置数据库强制只读。驱动的语句和登录超时有进程级影响，不支持异步客户端。

SQL Server 文件按独立行 `GO` 分批，同一连接执行，保留批次内变量和过程体；不支持 `GO n` 或 SQLCMD。显式事务 SQL 原样交给数据库。dry-run 应提供执行时相同的 DSN，以选择相同分批规则；它只做离线预览。混合 DML/查询批次的影响行数受驱动暴露的信息限制。

`--timeout` / `-t` 为单条语句的超时秒数，须为正整数；默认分别读取 `database.query_timeout_seconds` / `database.exec_timeout_seconds`（均为 30）。MySQL 的非事务性写入和隐式提交 DDL 不保证完整回滚。

## JSONL Transfer

```bash
dbtalk export \
  --source postgresql \
  --dsn-env DBTALK_DSN_SOURCE \
  --output ./data/source.jsonl

dbtalk import \
  --target postgresql \
  --dsn-env DBTALK_DSN_TARGET \
  --input ./data/source.jsonl \
  --mode upsert
```

目标 schema 必须已存在；`upsert` 需要完整主键。

`--source` / `--target` 支持 `sqlite`、`mysql`、`postgresql`、`mssql`。SQL Server 仅使用连接默认 schema，跨 schema 外键失败。支持整数、decimal/numeric、bit、字符、二进制、UUID、date/smalldatetime/datetime 以及精度不超过 6 的 datetime2/time；datetime2(7)、time(7)、datetimeoffset 和其他未支持类型在元数据预检失败。该限制仅针对 JSONL。旧版 reader 不识别 `mssql` 来源。

导出所选表含 computed、rowversion/timestamp 或 generated always 列时失败。导入不能提供这些生成列，省略时保留目标生成行为；identity 显式值使用 IDENTITY_INSERT，须已有表权限。upsert 按完整主键加锁后更新或插入，不更新 identity。导入先预检，按表事务提交；失败表回滚，前序表保留。选表使用可重复的 `--include-table` / `--exclude-table`，压缩导出使用 `--archive`。pymssql 分批读取不保证驱动不缓冲整个结果集。
