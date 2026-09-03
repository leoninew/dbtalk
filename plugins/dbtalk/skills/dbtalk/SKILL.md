---
name: dbtalk
description: 使用当前目录 `.env` 中的 `DBTALK_DSN_*` 和 `dbtalk query/exec/export/import` 执行通用 SQL 及 JSONL 数据导入导出。
---

# dbtalk Database Operations

要求发布安装的 `dbtalk` 可执行文件位于 `PATH` 中。

## Agent DSN workflow

CLI 兼容 `--dsn DSN` 和 `--dsn-env NAME` 二选一；**代理执行时只可使用 `--dsn-env`，绝不传 `--dsn`**。拿到 DSN 后，在第一条 `dbtalk` 命令前执行以下流程：

1. 根据操作范围规划稳定的变量名：单个应用库使用 `DBTALK_DSN_APP`；JSONL 传输使用 `DBTALK_DSN_SOURCE` 与 `DBTALK_DSN_TARGET`；数据库生命周期操作使用 `DBTALK_DSN_MYSQL_MANAGEMENT` 或 `DBTALK_DSN_POSTGRES_MANAGEMENT`；账号或 role 操作使用 `DBTALK_DSN_MYSQL_ADMIN` 或 `DBTALK_DSN_POSTGRES_ADMIN`。
2. 将 DSN 写入当前工作目录 `.env`，并在整个任务中复用这些名称。
3. 后续每条 `dbtalk query`、`dbtalk exec`、`dbtalk export`、`dbtalk import` 命令均只传对应的 `--dsn-env DBTALK_DSN_*`。

不得使用 `export`、内联环境变量赋值或 PowerShell `$env:` 为 DSN 赋值；不得猜测凭据，也不得把实际 DSN 写入 `.env.example`、命令参数、日志或输出。`--dsn-env DBTALK_DSN_*` 先读取同名进程环境变量；该变量不存在时才读取当前目录 `.env` 的同名值。进程变量存在但为空会失败而不回退；非 `DBTALK_DSN_*` 名称不读取 dotenv。CLI 不加载 `.env.local`、其他 dotenv 变体或父目录 dotenv 文件。

所有命令要求 canonical DSN。不要使用 `sqlite-file`、`--sqlite-path`、`--mysql-dsn-env` 或无 driver 的 DSN。支持的 canonical DSN：

```text
sqlite:///./data/app.db
mysql+pymysql://user:password@host:3306/database
postgresql+psycopg://user:password@host:5432/database
```

异步 Python API 使用对应的 `sqlite+aiosqlite`、`mysql+asyncmy` 和 `postgresql+psycopg` driver。CLI 使用同步执行路径。

MySQL 与 PostgreSQL URL 可以省略末尾 database path。`query` / `exec` 可以使用无库名 DSN；`export` / `import` 必须在 DSN 中明确带有 database name。SQLite 的 database path 始终必填。

## Query / Exec

先在当前目录 `.env` 中计划并保存连接：

```dotenv
DBTALK_DSN_APP=sqlite:///./data/app.db
```

```bash
dbtalk query \
  --dsn-env DBTALK_DSN_APP \
  --timeout 30 \
  --sql 'SELECT id, name FROM users WHERE id = :id' \
  --param id=1 \
  --format json

dbtalk exec \
  --write \
  --dsn-env DBTALK_DSN_APP \
  --timeout 30 \
  --sql 'UPDATE users SET name = :name WHERE id = :id' \
  --param 'name="Ada"' \
  --param id=1
```

`query` 默认输出表格，`--format json` 输出 `columns`、`rows`、`row_count`。它使用数据库只读会话，不分析 SQL 文本；写入或 DDL 会由数据库拒绝。`exec` 默认使用只读会话，传入 `--write` 或 `-w` 后才切换为写会话；实际是否为写入由数据库判断。参数格式为可重复的 `NAME=JSON_VALUE`，两条命令都只执行一条 SQL。

`--timeout` / `-t` 以秒限制单条 query/exec，只接受正整数。省略时使用 `database.operation_timeout_seconds` 配置（默认 30），可用 `DBTALK_DATABASE__OPERATION_TIMEOUT_SECONDS` 覆盖。不要为超时而改写 SQL；SQLite、PostgreSQL 和 MySQL 分别使用其原生会话或驱动机制。MySQL 写入超时后不保证非事务性语句或隐式提交 DDL 的完整回滚。

## JSONL Transfer

为两个端点分别命名，写入同一当前目录 `.env`：

```dotenv
DBTALK_DSN_SOURCE=postgresql+psycopg://user:password@host:5432/source_db
DBTALK_DSN_TARGET=postgresql+psycopg://user:password@host:5432/target_db
```

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

SQLite、MySQL、PostgreSQL 的 export/import 均通过 SQLAlchemy Core adapter 执行；不要根据数据库类型切换到另一套 transfer API。目标 schema 必须预先存在，`upsert` 需要完整主键，工具不会创建 schema。