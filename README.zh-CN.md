# dbtalk

[![CI](https://github.com/leoninew/pomelo-dbtalk/actions/workflows/ci.yml/badge.svg)](https://github.com/leoninew/pomelo-dbtalk/actions/workflows/ci.yml)

`dbtalk` 是一个面向大模型和 AI Agent 的数据库命令行工具，支持 SQLite、MySQL 和 PostgreSQL 的 SQL 操作、JSONL 数据传输、权限管理与逻辑备份。

[English](README.md)

## 功能

- 使用 `query` 和 `exec` 执行 SQL，支持参数绑定和超时控制
- 在 SQLite、MySQL、PostgreSQL 之间导出和导入 JSONL 数据
- 管理 MySQL schema、user 和 PostgreSQL schema、role
- 使用 `grant`、`revoke` 管理权限 profile，使用 `permissions list/show` 查看原生权限
- 创建和恢复 MySQL SQL dump、PostgreSQL custom archive
- 为 Codex、Claude 和 Grok Agent 提供数据库操作 skills

## 安装

需要 Python 3.12 或更高版本，以及 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync --all-groups
uv run dbtalk --help
```

## 快速开始

将连接信息保存到当前目录 `.env`，命令中只引用变量名：

```dotenv
DBTALK_DSN_APP=sqlite:///./data/app.db
```

```bash
uv run dbtalk query \
  --dsn-env DBTALK_DSN_APP \
  --sql 'SELECT 1 AS ok'

uv run dbtalk exec \
  --write \
  --dsn-env DBTALK_DSN_APP \
  --sql 'CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)'
```

Agent 在第一条数据库命令前将 DSN 写入 `.env`。`--dsn-env DBTALK_DSN_*` 优先读取进程环境变量，只有变量不存在时才从当前目录 `.env` 回退读取。

## 命令

| 命令 | 用途 |
| --- | --- |
| `dbtalk mysql schema` | 管理 MySQL schema/database |
| `dbtalk mysql user` | 管理 MySQL 用户 |
| `dbtalk mysql grant` / `dbtalk mysql revoke` | 授予或撤销 MySQL 权限 |
| `dbtalk postgres schema` | 管理 PostgreSQL schema/database |
| `dbtalk postgres role` | 管理 PostgreSQL role |
| `dbtalk postgres grant` / `dbtalk postgres revoke` | 授予或撤销 PostgreSQL 权限 |
| `dbtalk mysql permissions list/show` | 查看 MySQL 原生权限 |
| `dbtalk postgres permissions list/show` | 查看 PostgreSQL 原生权限 |
| `dbtalk mysql dump/restore` | 处理 MySQL SQL dump |
| `dbtalk postgres dump/restore` | 处理 PostgreSQL custom archive |
| `dbtalk query/exec` | 查询或执行单条 SQL |
| `dbtalk export/import` | 传输 JSONL 数据 |

运行 `uv run dbtalk --help` 或子命令的 `--help` 查看完整参数。

## DSN 和配置

支持的 DSN 示例：

```text
sqlite:///./data/app.db
mysql+pymysql://user:password@host:3306/app
postgresql+psycopg://user:password@host:5432/app
```

每个命令必须在 `--dsn DSN` 和 `--dsn-env NAME` 中选择一个；`--dsn` 保留给直接集成。Agent 必须先规划 `DBTALK_DSN_*` 名称，将 DSN 写入当前目录 `.env`，随后只使用 `--dsn-env`。不会加载 `.env.local` 或其他 dotenv 变体。默认配置位于 [dbtalk.yaml](dbtalk.yaml)，可使用 `DBTALK_` 前缀的进程环境变量覆盖。

## 文档

- [数据库操作](docs/database.md)：SQL、JSONL 导入导出和 DSN 约定
- [MySQL 手册](docs/mysql.md)：schema、用户、权限、dump 和 restore
- [PostgreSQL 手册](docs/postgres.md)：schema、role、权限、dump 和 restore

## Agent 集成

仓库包含一个可供 Codex、Claude 和 Grok 使用的 `dbtalk` plugin，提供三个 skills：

- `dbtalk`：通用 SQL 和 JSONL 数据传输
- `dbtalk-mysql`：MySQL schema、用户、权限和 dump/restore
- `dbtalk-postgres`：PostgreSQL schema、role、权限和 dump/restore

安装或同步本地 plugin：

```bash
make install
```

详细的安装方式、参数约束和安全边界见 [Agent 插件与 Skills](docs/codex.md)。

## 开发

```bash
make deps
make check
make test
make release
```

`make check` 执行 Ruff 和 Mypy；`make test` 运行测试套件。依赖真实数据库的集成测试默认跳过。

## 许可证

本项目采用 Apache License 2.0，详见 [LICENSE](LICENSE)。

## Docker

```bash
docker build -t dbtalk .
docker run --rm dbtalk --help
```
