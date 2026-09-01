---
name: dbtalk-postgres
description: 使用 dbtalk postgres 管理 PostgreSQL schema/database、role、profile 授权，或创建和恢复单库 custom archive。用户要求 PostgreSQL schema/database create/drop/list、role、授权、backup、dump、restore 时使用。
---

# dbtalk PostgreSQL

使用 `dbtalk postgres` 管理 PostgreSQL schema/database、role、profile 授权，或处理单个数据库的 native logical dump 和 restore。它创建并消费 `pg_dump --format=custom` archive，不用于 JSONL 数据传输、物理备份、WAL/PITR 或 tablespace。要求发布安装的 `dbtalk` 可执行文件位于 `PATH` 中。

先确认可用参数：

```bash
dbtalk postgres dump --help
dbtalk postgres restore --help
dbtalk postgres schema --help
```

## 连接与客户端

连接必须通过完整的 `--dsn DSN` 或 `--dsn-env NAME` 二选一提供，并使用 `postgresql+psycopg://user:password@host:5432/database`。脚本中把 DSN 保存到环境变量。对于 `--dsn-env DBTALK_*`，dbtalk 优先使用同名进程环境变量；变量不存在时才读取当前目录、Git 已忽略 `.env` 的同名值。代理在用户已提供或明确授权 DSN 时可创建或更新 `.env` 中的 `DBTALK_*` 条目，但不得猜测凭据或将它写入 `.env.example`、命令行、日志、skill、Git 提交或 archive 文件名：

```bash
export DBTALK_APP_DSN='postgresql+psycopg://backup:password@db.example.com:5432/app'
```

当 DSN 指向本机 `localhost` 或 `127.0.0.1` 且请求端口唯一对应一个运行中的 Docker PostgreSQL 容器时，dump/restore 直接复用该容器，通过 `docker exec` 使用容器内默认 Unix socket；dump 通过临时文件和 `docker cp` 取回 archive，restore 通过 `docker cp` 放入后导入并清理。未识别到唯一映射容器时，才优先使用本机 `pg_dump` / `pg_restore`；本机客户端缺失时，只能使用 `postgres.client_image` 配置的本地 Docker image，默认 `postgres:18`；不会安装客户端、拉取 image 或自动重试。必要时通过 `DBTALK_POSTGRES__CLIENT_IMAGE` 配置与源服务端兼容的更高 major image。

## Database management

数据库生命周期操作使用 `dbtalk postgres schema`，与 query/exec、role 管理和 dump/restore 分离。管理 DSN 必须连接到目标以外的维护库，通常为 `postgres`，并使用具有建库或删库权限的账号。

```bash
dbtalk postgres schema list --dsn-env DBTALK_POSTGRES_MANAGEMENT_DSN
dbtalk postgres schema create --dsn-env DBTALK_POSTGRES_MANAGEMENT_DSN --name app_db
dbtalk postgres schema drop --dsn-env DBTALK_POSTGRES_MANAGEMENT_DSN --name app_db --yes
```

先执行 `list` 核对目标。删除不可逆，只有用户明确授权删除指定目标时才传入 `--yes`；不能删除管理 DSN 正在连接的数据库。存在其他连接时应如实报告失败，不主动终止其他会话；不猜测目标、不执行任意 SQL、不创建或管理 role。

## Dump

```bash
dbtalk postgres dump --dsn-env DBTALK_APP_DSN --output ./data/app.dump
dbtalk postgres dump --dsn-env DBTALK_APP_DSN --compression-level 6
```

dump 只输出 custom `.dump` archive，默认在 `postgres.output_directory` 中生成带时间戳的文件。archive 已使用 PostgreSQL 原生内部压缩；不要使用 MySQL 的 `.sql.gz` 语义或另行 gzip。

## Restore

restore 会修改目标数据库。只有目标 DSN、输入 archive 来源和写入授权均已明确时才能执行：

```bash
dbtalk postgres restore --dsn-env DBTALK_APP_DSN --input ./data/app.dump
```

目标数据库必须已经存在。默认跳过 archive 中的 owner 和 ACL；需要保留时传入 `--preserve-owner`、`--preserve-privileges`。`--clean` 会删除目标对象，必须显式指定；`--if-exists` 只能与 `--clean` 一起使用。custom archive 可使用 `--jobs N` 并行恢复，`N` 必须为正整数。

restore 在写入前用 `pg_restore --list` 校验 archive。即使启用 fail-fast，restore 也不能整体回滚；完成后确认命令成功退出，并按需检查代表性对象和数据。

## Role 与授权

先查看命令帮助：

```bash
dbtalk postgres role --help
dbtalk postgres grant --help
dbtalk postgres revoke --help
dbtalk postgres permissions --help
```

role 管理和 grant/revoke 使用管理 DSN。新 role 默认没有超级用户、建库、建 role、复制或绕过 RLS 能力；密码只能通过 `--password-env NAME` 引用。grant/revoke 的 `--database` 与 `--schema` 最多提供一个，省略时使用 DSN database；支持 `readonly`、`readwrite`、`migrator` profile，或可重复的 `--privilege NAME`，两者互斥。权限层级为 `migrator > readwrite > readonly`：`readonly` 只读，schema 目标的 `readwrite` 用于常规应用增删改查和 sequence 使用，`migrator` 再加入 DDL，并设置 role 的全局 `CREATEDB` 属性以允许建库。撤销 `migrator` 会设为 `NOCREATEDB`。固定 profile 不添加 `GRANT OPTION` 或角色管理能力。细粒度 privilege 不由 dbtalk allowlist 过滤，是否可授权由 PostgreSQL 服务端决定。

`permissions list/show` 使用 PostgreSQL 原生权限查询；list 默认显示当前 DSN 可见结果，并支持 role、database、schema 筛选，show 要求 role 并支持资源筛选。

schema profile 仅作用于当前对象，不能替代 default privileges。执行启用、禁用、轮换密码、删除、授权或撤销前，必须确认目标和写入授权，并传入 `--yes`。不要修改当前管理 role，也不要把 profile 扩展为 `WITH GRANT OPTION` 或高危系统权限。
