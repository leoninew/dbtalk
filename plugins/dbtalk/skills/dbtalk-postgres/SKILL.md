---
name: dbtalk-postgres
description: 通过 `DBTALK_DSN_*` 使用 dbtalk postgres 管理 PostgreSQL schema/database、role、profile 授权和客户端会话，或创建和恢复单库 custom archive。
---

# dbtalk PostgreSQL

使用 `dbtalk postgres` 管理 PostgreSQL schema/database、role、profile 授权和客户端会话，或处理单个数据库的 native logical dump 和 restore。它创建并消费 `pg_dump --format=custom` archive，不用于 JSONL 数据传输、物理备份、WAL/PITR 或 tablespace。要求发布安装的 `dbtalk` 可执行文件位于 `PATH` 中。

先确认可用参数：

```bash
dbtalk postgres dump --help
dbtalk postgres restore --help
dbtalk postgres schema --help
```

## Agent connection workflow

连接使用 canonical `postgresql+psycopg://user:password@host:5432/database`；database path 可省略。CLI 兼容 `--dsn DSN` 与 `--dsn-env NAME` 二选一，但**代理执行时只可使用 `--dsn-env`，绝不传 `--dsn`**。按操作范围复用 `DBTALK_DSN_APP`（dump 或 restore）、`DBTALK_DSN_POSTGRES_MANAGEMENT`（schema）或 `DBTALK_DSN_POSTGRES_ADMIN`（role、grant、revoke、permissions、session）。可引用已有当前目录 `.env`；用户要求在命令文件中直接声明变量时，使用进程环境变量，不新建 dotenv、不覆盖已有密码。

对于 `--dsn-env DBTALK_DSN_*`，dbtalk 优先使用同名进程环境变量；变量不存在时才读取当前工作目录 `.env` 的同名值。进程变量存在但为空会失败而不回退；非 `DBTALK_DSN_*` 名称不读取 dotenv，且 CLI 不加载 `.env.local`、其他 dotenv 变体或父目录 dotenv 文件。不得猜测凭据，也不得将实际 DSN 写入 `.env.example`、命令行、日志、skill 或 archive 文件名：

```dotenv
DBTALK_DSN_APP=postgresql+psycopg://backup:password@db.example.com:5432/app
DBTALK_DSN_POSTGRES_MANAGEMENT=postgresql+psycopg://operator:password@db.example.com:5432/postgres
DBTALK_DSN_POSTGRES_ADMIN=postgresql+psycopg://admin:password@db.example.com:5432/app
DBTALK_POSTGRES_APP_PASSWORD=change-me
```

当 DSN 指向本机 `localhost` 或 `127.0.0.1` 且请求端口唯一对应一个运行中的 Docker PostgreSQL 容器时，dump/restore 直接复用该容器，通过 `docker exec` 使用容器内默认 Unix socket；dump 通过临时文件和 `docker cp` 取回 archive，restore 通过 `docker cp` 放入后导入并清理。未识别到唯一映射容器时，才优先使用本机 `pg_dump` / `pg_restore`；本机客户端缺失时，只能使用 `postgres.client_image` 配置的本地 Docker image，默认 `postgres:18`；不会安装客户端、拉取 image 或自动重试。必要时通过 `DBTALK_POSTGRES__CLIENT_IMAGE` 配置与源服务端兼容的更高 major image。

## Database management

数据库生命周期操作使用 `dbtalk postgres schema`，与 query/exec、role 管理和 dump/restore 分离。管理 DSN 可省略 database path，并使用具有建库或删库权限的账号。`schema drop` 以 `--name` 为删除目标，连接后检查当前会话数据库。

```bash
dbtalk postgres schema list --dsn-env DBTALK_DSN_POSTGRES_MANAGEMENT
dbtalk postgres schema create --dsn-env DBTALK_DSN_POSTGRES_MANAGEMENT --name app_db
dbtalk postgres schema drop --dsn-env DBTALK_DSN_POSTGRES_MANAGEMENT --name app_db --yes
dbtalk postgres schema drop --dsn-env DBTALK_DSN_POSTGRES_MANAGEMENT --name app_db --if-exists --yes
dbtalk postgres schema drop --dsn-env DBTALK_DSN_POSTGRES_MANAGEMENT --name app_db --if-exists --force --yes
```

先执行 `list` 核对目标。删除不可逆，只有用户明确授权删除指定目标时才传入 `--yes`；不能删除当前会话正在连接的数据库。默认不终止其他会话，连接占用时如实报告失败；不猜测目标、不执行任意 SQL、不创建或管理 role。

`schema drop --if-exists` 使用原生 `DROP DATABASE IF EXISTS`，仅忽略目标库不存在；默认不忽略。该选项仍要求删除授权和 `--yes`，仍拒绝删除当前连接库，连接、权限和其他会话占用等错误照常失败。

`schema drop --force` 使用原生 `WITH (FORCE)` 尝试终止目标库已有连接后删除，可与 `--if-exists` 组合。只有用户授权删除目标并处理中断其占用连接时才使用，仍要求 `--yes`，仍拒绝删除当前连接库。会话终止权限不足、预备事务、活跃逻辑复制槽或订阅仍可能导致失败；如实报告，不自动提权、逐会话终止或重试。该选项仅属于 PostgreSQL `schema drop`。

## Session management

先用 `session list` 核对目标。两条命令均按 `--database > DSN database > 失败` 解析；显式参数同时覆盖连接数据库，账号必须能连接目标库。即使指定 `--id`，也必须具有目标数据库。列表只含目标库的普通客户端，排除自身及系统进程，空结果为 `(0 rows)`。

以下 Bash 示例在文件内声明变量，不创建 `.env`；只使用用户提供的连接值：

```bash
export DBTALK_DSN_POSTGRES_ADMIN='postgresql+psycopg://operator:password@127.0.0.1:5432/postgres'
DBTALK_DATABASE='app'
DBTALK_SESSION_ID='12345'
dbtalk postgres session list --dsn-env DBTALK_DSN_POSTGRES_ADMIN --database "$DBTALK_DATABASE"
dbtalk postgres session kill --dsn-env DBTALK_DSN_POSTGRES_ADMIN \
  --database "$DBTALK_DATABASE" --id "$DBTALK_SESSION_ID" --wait 5 --yes
```

只有用户授权中断目标连接时才传 `--yes`；省略 `--id` 会处理目标库快照内全部匹配客户端，单 ID 也不能跨库。发送前复核 PID、连接开始时间、用户和范围。完整统计信息通常需要超级用户或 `pg_read_all_stats`；终止其他角色需要成员资格或 `pg_signal_backend`，超级用户会话另受原生限制，身份不可见时拒绝盲目终止。不自动 GRANT、提权或重试。

省略 `--wait` 不轮询，`requested` 仅表示请求返回；可选有限正数 `--wait SECONDS` 使用整批共享的退出确认预算，确认原身份消失才是 `terminated`；`gone` 表示原目标已先行退出。`changed`、`failed`、`timeout`、`not_attempted` 会保留逐目标结果并返回失败；空批量成功，显式 ID 未匹配失败。连接失效不重连或重发，不将部分成功报告成整批成功。

轮询由必需配置 `session.poll_interval_seconds` 控制（随附 YAML 为 `0.1`，可用 `DBTALK_SESSION__POLL_INTERVAL_SECONDS` 覆盖）；查询与执行超时复用 `database.query_timeout_seconds`、`database.exec_timeout_seconds`。等待不限制整条命令墙钟时间，也不保证连接池不重连或库内已无连接。终止会中断会话及其未提交事务；请求不可撤销。不提供 `session cancel`；日志仅用根 `dbtalk -v postgres session ...` 开启。

## Dump

```bash
dbtalk postgres dump --dsn-env DBTALK_DSN_APP --database app --output ./data/app.dump
dbtalk postgres dump --dsn-env DBTALK_DSN_APP --database app --compression-level 6
dbtalk postgres dump --dsn-env DBTALK_DSN_APP --database app \
  --exclude-table ops_system_logs --exclude-table usage_logs
```

dump 需要明确 target database，按 `--database > DSN database > 失败` 决定，并只输出 custom `.dump` archive，默认在 `postgres.output_directory` 中生成带时间戳的文件。archive 已使用 PostgreSQL 原生内部压缩；不要使用 MySQL 的 `.sql.gz` 语义或另行 gzip。可重复的 `--exclude-table NAME` 映射为 `pg_dump --exclude-table=NAME`，跳过该表对象；不预检表是否存在。restore 不会建回被排除的表。该选项与 JSONL `--exclude-table` 同名，但走 native dump。

## Restore

restore 会修改目标数据库。只有 target database、输入 archive 来源和写入授权均已明确时才能执行；target 按 `--database > DSN database > 失败` 决定：

```bash
dbtalk postgres restore --dsn-env DBTALK_DSN_APP --database app --input ./data/app.dump
```

目标数据库必须已经存在。默认跳过 archive 中的 owner 和 ACL；需要保留时传入 `--preserve-owner`、`--preserve-privileges`。完整还原已有库时显式指定 `--clean`：先清空全部非系统 schema 和用户对象，包括备份中没有的表，再恢复 archive；标准 `public` schema 会重建，数据库本身和实例级账号、授权保留。dump 排除的旧表也会删除。清理用户 extension、事件触发器、用户 cast/access method、publication、subscription、外部数据包装器、自定义过程语言和 large object；订阅清理不连接远端回收 replication slot。执行账号必须具有全部目标对象的清理权限。本机 `--clean` 同时需要 `psql` 和 `pg_restore`；容器路径使用容器内客户端。`--if-exists` 只能与 `--clean` 一起使用，为清理阶段的 DROP 添加 `IF EXISTS`。custom archive 可使用 `--jobs N` 并行恢复，`N` 必须为正整数。

restore 在写入前用 `pg_restore --list` 校验 archive。即使启用 fail-fast，restore 也不能整体回滚；完成后确认命令成功退出，并按需检查代表性对象和数据。

## Owner 转移

`dbtalk postgres owner reassign` 使用原生 `REASSIGN OWNED` 转移指定 role 的所有对象；默认预览，显式 `--yes` 才执行。操作必须给出 `--database`、`--from-role`、`--to-role`，并使用 `.env` 的 `DBTALK_DSN_POSTGRES_ADMIN`；执行前核对预览中的当前数据库对象和 cluster 共享对象（数据库、表空间等）。它**不是**单个 schema 或表的权限授予；grant migrator 也不会改变 owner。不得在仅授权某些表或某个 schema 时使用。目标 role 必须与源 role 不同。变更前必须确认目标数据库、两个 role、可能受影响的共享对象和写入授权。预览中出现共享对象时，加 `--yes` 也不会直接执行；只有明确接受跨库共享对象变更并追加 `--include-shared` 才能执行。

```bash
dbtalk postgres owner reassign --dsn-env DBTALK_DSN_POSTGRES_ADMIN \
  --database app --from-role old_owner --to-role new_owner
dbtalk postgres owner reassign --dsn-env DBTALK_DSN_POSTGRES_ADMIN \
  --database app --from-role old_owner --to-role new_owner --yes
```

原生 `reassign` 不能以 initdb 的 `postgres` 或其他内置系统角色为源，重命名仍不能绕过；增加 `--include-shared` 不解决这一限制。需要转移业务对象时使用 `owner transfer`：

```bash
dbtalk postgres owner transfer --dsn-env DBTALK_DSN_POSTGRES_ADMIN \
  --database app --schema public --from-role postgres --to-role app_role
dbtalk postgres owner transfer --dsn-env DBTALK_DSN_POSTGRES_ADMIN \
  --database app --schema public --from-role postgres --to-role app_role --yes
```

`transfer` 必须明确 database、源/目标 role 与写入授权；默认预览，核对直接对象、自动附属对象、排除项、阻塞项和 SQL 后才执行。`--schema` 可重复；省略时检查当前库全部非系统 schema 和无 schema 的库内对象，不推断用户只授权了 public。支持 schema、普通/分区/外部表、view/materialized view、sequence、function/procedure/aggregate、enum/domain/composite/range；索引、TOAST、行类型、自动数组/multirange、表拥有的序列随父对象转移。系统对象、extension 及其成员和自动附属对象排除；自动传播会影响扩展成员时拒绝整个计划。范围内未支持的对象也阻止执行，不能将它们忽略后报告完成。

`transfer` 不改变 database/tablespace owner，没有 `--include-shared`；不授予 ACL、role membership、角色属性或 default privileges，不替代 `grant migrator`。执行者和目标角色需具有 PostgreSQL 要求的权限，命令不自动提升或补授。整份计划使用一个事务，失败回滚；执行前后复核对象身份、归属和扩展关系，但预览不冻结两次调用之间的并发 DDL。不得自动改用 `reassign`、扩大 schema 范围、禁用事件触发器或终止其他会话。

## Role 与授权

先查看命令帮助：

```bash
dbtalk postgres role --help
dbtalk postgres grant --help
dbtalk postgres revoke --help
dbtalk postgres permissions --help
```

role 管理和 grant/revoke 使用管理 DSN。新 role 默认没有超级用户、建库、建 role、复制或绕过 RLS 能力；密码只能通过 `--password-env NAME` 引用。`--password-env` 的 `DBTALK_*` 名称先读进程环境，变量不存在时才读当前目录 `.env`；进程变量存在但为空会失败且不回退，非 `DBTALK_*` 名称不读 dotenv。grant/revoke 的 `--database` 可与 `--schema` 同时提供：只传 `--database` 时授权该库；只传 `--schema` 时必须有 DSN database，并在该库上授权 schema；两者同时传入时连接到 `--database`，再对该库中的 `--schema` 授权。两者都省略时使用 DSN database。支持 `readonly`、`readwrite`、`migrator` profile，或可重复的 `--privilege NAME`，两者互斥。权限层级为 `migrator > readwrite > readonly`：`readonly` 只读，schema 目标的 `readwrite` 用于常规应用增删改查和 sequence 使用，`migrator` 再加入 DDL，并设置 role 的全局 `CREATEDB` 属性以允许建库。撤销 `migrator` 会设为 `NOCREATEDB`。固定 profile 不添加 `GRANT OPTION` 或角色管理能力。细粒度 privilege 不由 dbtalk allowlist 过滤，是否可授权由 PostgreSQL 服务端决定。

`permissions list/show` 使用 PostgreSQL 原生权限查询；list 默认显示当前 DSN 可见结果，并支持 role、database、schema 筛选，show 要求 role 并支持资源筛选。

schema profile 仅作用于当前对象，不能替代 default privileges。执行启用、禁用、轮换密码、删除、授权或撤销前，必须确认目标和写入授权，并传入 `--yes`。不要修改当前管理 role，也不要把 profile 扩展为 `WITH GRANT OPTION` 或高危系统权限。
