---
name: dbtalk-database
description: 使用 dbtalk query/exec/export/import 通过明确 DSN 执行通用 SQL 及 JSONL 数据导入导出。
---

# dbtalk Database Operations

要求发布安装的 `dbtalk` 可执行文件位于 `PATH` 中。

所有 `dbtalk query`、`dbtalk exec`、`dbtalk export` 和 `dbtalk import` 命令都必须接收一个完整 DSN，或接收 `--dsn-env NAME` 从环境变量读取 DSN；二者不能同时提供，也不能省略。不要使用 `sqlite-file`、`--sqlite-path`、`--mysql-dsn-env` 或无 driver 的 DSN。对于 `--dsn-env DBTALK_*`，dbtalk 先使用同名进程环境变量；变量不存在时才从当前目录 `.env` 读取同名值。非 `DBTALK_*` 名称仅从进程环境读取。

代理可在用户已提供或明确授权的 DSN 范围内创建或更新当前目录、Git 已忽略的 `.env`，例如 `DBTALK_APP_DSN=...`，再使用 `--dsn-env DBTALK_APP_DSN`。不得猜测凭据，不得把 DSN 写入 `.env.example`、日志、命令参数、输出或 Git 提交。

支持的 canonical DSN：

```text
sqlite:///./data/app.db
mysql+pymysql://user:password@host:3306/database
postgresql+psycopg://user:password@host:5432/database
```

异步 Python API 使用对应的 `sqlite+aiosqlite`、`mysql+asyncmy` 和 `postgresql+psycopg` driver。CLI 使用同步执行路径。

## Query / Exec

```bash
dbtalk query \
  --dsn 'sqlite:///./data/app.db' \
  --timeout 30 \
  --sql 'SELECT id, name FROM users WHERE id = :id' \
  --param id=1 \
  --format json

export DBTALK_APP_DSN='sqlite:///./data/app.db'
dbtalk exec \
  --write \
  --dsn-env DBTALK_APP_DSN \
  --timeout 30 \
  --sql 'UPDATE users SET name = :name WHERE id = :id' \
  --param 'name="Ada"' \
  --param id=1
```

`query` 默认输出表格，`--format json` 输出 `columns`、`rows`、`row_count`。它使用数据库只读会话，不分析 SQL 文本；写入或 DDL 会由数据库拒绝。`exec` 默认使用只读会话，传入 `--write` 或 `-w` 后才切换为写会话；实际是否为写入由数据库判断。参数格式为可重复的 `NAME=JSON_VALUE`，两条命令都只执行一条 SQL。

`--timeout` / `-t` 以秒限制单条 query/exec，只接受正整数。省略时使用 `database.operation_timeout_seconds` 配置（默认 30），可用 `DBTALK_DATABASE__OPERATION_TIMEOUT_SECONDS` 覆盖。不要为超时而改写 SQL；SQLite、PostgreSQL 和 MySQL 分别使用其原生会话或驱动机制。MySQL 写入超时后不保证非事务性语句或隐式提交 DDL 的完整回滚。

## JSONL Transfer

```bash
export DBTALK_SOURCE_DSN='postgresql+psycopg://user:password@host:5432/source_db'
dbtalk export \
  --source postgresql \
  --dsn-env DBTALK_SOURCE_DSN \
  --output ./data/source.jsonl

dbtalk import \
  --target postgresql \
  --dsn 'postgresql+psycopg://user:password@host:5432/target_db' \
  --input ./data/source.jsonl \
  --mode upsert
```

SQLite、MySQL、PostgreSQL 的 export/import 均通过 SQLAlchemy Core adapter 执行；不要根据数据库类型切换到另一套 transfer API。目标 schema 必须预先存在，`upsert` 需要完整主键，工具不会创建 schema。
