---
name: dbtalk-mysql
description: 通过 `DBTALK_DSN_*` 使用 dbtalk mysql 管理 MySQL schema/database、user、profile 授权和客户端会话，或导出和恢复 mysqldump SQL 文件。
---

# dbtalk MySQL

使用 `dbtalk mysql` 管理 MySQL schema/database、user、profile 授权和客户端会话，或处理原生 SQL dump 和 restore。要求发布安装的 `dbtalk` 可执行文件位于 `PATH` 中。

## Dump 执行语义

`dbtalk mysql dump` 对调用机的 `localhost` 或 `127.0.0.1` 先检查请求端口是否唯一对应一个运行中的 Docker 容器；若是，直接在该容器中运行 `mysqldump`，默认通过 Unix socket 连接，并将 SQL 文件复制到调用机指定的输出路径。未识别到唯一映射容器时，才调用当前机器上的 `mysqldump`；本机客户端也不可用时，再回退到该机器已有的 Docker `mysql` 镜像。MySQL 服务器不需要安装 dbtalk。

DSN 的 host 从当前执行机解释。`localhost` 或 `127.0.0.1` 指向当前执行机本身；只有 Docker 明确显示该端口唯一映射到运行中容器时，才使用该容器内的 socket 连接。一次 SQL dump 请求只授权执行该 dump；不应为远端 DSN 更换命令执行位置、猜测容器或安排其他连接方式。连接或客户端失败时，报告 dbtalk 的失败结果并停止。

不要安装客户端、拉取镜像或替换为其他备份工具。

## Agent connection workflow

配置来自 `dbtalk.yaml`，可由 `DBTALK_*` 进程变量覆盖。Agent 只使用 `--dsn-env NAME`，连接格式为 `mysql+pymysql://`。

1. 优先使用用户指定的变量；否则从进程环境或当前目录 `.env` 中，按服务器、目标库和账号权限选择已有 MySQL 连接。
2. DSN 和密码变量均保留原名，将示例名替换为实际名称，不复制连接或创建别名。目标库使用命令支持的 `--database` 指定。
3. 无适用连接时，用用户提供或授权配置的连接信息向当前目录 `.env` 新增 `DBTALK_DSN_*` 条目，保留已有内容。目标或凭据不明时询问用户。

同名进程变量优先；仅在变量不存在时，`DBTALK_DSN_*` 才从当前目录 `.env` 读取。空值报错，不搜索其他 dotenv 文件。用户要求在脚本中声明变量时，使用进程变量，不写入 dotenv。

不输出真实 DSN 或密码，也不将其写入命令参数、日志、文档或 `.env.example`。配置示例：

```dotenv
DBTALK_DSN_APP=mysql+pymysql://user:password@host:3306/database
DBTALK_DSN_MYSQL_MANAGEMENT=mysql+pymysql://operator:password@host:3306/mysql
DBTALK_DSN_MYSQL_ADMIN=mysql+pymysql://admin:password@host:3306/app
DBTALK_MYSQL_ROOT_PASSWORD=change-me
```

## Database management

`dbtalk mysql schema` 管理数据库，账号需有相应权限。DSN 可省略 database path；`schema drop` 拒绝删除当前连接库。

```bash
dbtalk mysql schema list --dsn-env DBTALK_DSN_MYSQL_MANAGEMENT
dbtalk mysql schema create --dsn-env DBTALK_DSN_MYSQL_MANAGEMENT --name app_db
dbtalk mysql schema drop --dsn-env DBTALK_DSN_MYSQL_MANAGEMENT --name app_db --yes
dbtalk mysql schema drop --dsn-env DBTALK_DSN_MYSQL_MANAGEMENT --name app_db --if-exists --yes
```

先执行 `list` 核对目标。创建后只报告数据库名。删除不可逆，只有用户明确授权删除指定目标时才传入 `--yes`；不能删除当前会话正在连接的数据库。不猜测目标、不执行任意 SQL、不创建或管理账号。

`schema drop --if-exists` 使用原生 `DROP DATABASE IF EXISTS`，仅忽略目标库不存在；默认不忽略。该选项仍要求删除授权和 `--yes`，仍拒绝删除当前连接库，连接、权限等其他错误照常失败。

## Session management

先用 `session list` 核对目标。两条命令均按 `--database > DSN database > 失败` 解析；显式参数同时覆盖连接数据库，账号必须能访问目标库。即使指定 `--id`，也必须具有目标数据库。

按用户要求在脚本中声明连接的示例；已有变量时省略 DSN 声明：

```bash
export DBTALK_DSN_MYSQL_ADMIN='mysql+pymysql://operator:password@127.0.0.1:3306/mysql'
DBTALK_DATABASE='app'
DBTALK_SESSION_ID='12345'
dbtalk mysql session list --dsn-env DBTALK_DSN_MYSQL_ADMIN --database "$DBTALK_DATABASE"
dbtalk mysql session kill --dsn-env DBTALK_DSN_MYSQL_ADMIN \
  --database "$DBTALK_DATABASE" --id "$DBTALK_SESSION_ID" --wait 5 --yes
```

列表来自 `performance_schema.threads`，只含默认库匹配的普通前台客户端，排除自身及明确系统 / 复制线程，空结果为 `(0 rows)`。默认数据库不代表访问范围：默认库不同、以全限定表名访问目标库的连接不会列入。不能由空结果断言无访问者或锁持有者。

要求启用 Performance Schema 并具有 threads 表 SELECT 权限；该表不按 PROCESS 权限过滤其他用户行。源未启用、不可读或自身身份不可见会明确失败。终止其他账号需要 `CONNECTION_ADMIN`，`SYSTEM_USER` 账号还有原生限制。不自动授权或使用备用数据源。

只有用户授权中断目标连接时才传 `--yes`；省略 `--id` 会处理目标库快照内全部匹配客户端，单 ID 也不能跨库。`--id` 使用连接 ID，内部 THREAD_ID 只用于身份复核，不传给 KILL。发送前数据库或身份变化会跳过；等待时切库不视作退出。原生 KILL 仅接受连接 ID，复核与发送间仍可能发生竞争。

省略 `--wait` 不轮询，`requested` 仅表示请求返回；可选有限正数 `--wait SECONDS` 使用整批共享的退出确认预算，确认原身份消失才是 `terminated`；`gone` 表示原目标已先行退出。`changed`、`failed`、`timeout`、`not_attempted` 会保留逐目标结果并返回失败；空批量成功，显式 ID 未匹配失败。连接失效不重连或重发。

轮询由必需配置 `session.poll_interval_seconds` 控制（随附 YAML 为 `0.1`，可用 `DBTALK_SESSION__POLL_INTERVAL_SECONDS` 覆盖）；查询与执行超时复用 `database.query_timeout_seconds`、`database.exec_timeout_seconds`。等待不限制整条命令墙钟时间，也不保证连接池不重连。终止会话会回滚事务表的未提交事务，非事务影响不保证撤销。不提供 `session cancel`；日志仅用根 `dbtalk -v mysql session ...` 开启。

## Dump

dump 需要 MySQL DSN 和明确 target database，按 `--database > DSN database > 失败` 决定。未传 `--output` 时，输出默认为当前目录的 `data/<database>-<timestamp>.sql`，并会自动创建配置的输出目录。显式传入 `--output` 且路径是已有目录时，也会在其中生成同样的时间戳文件。其他路径视为文件，父目录必须已存在；不存在的路径不会被推断为目录或自动创建。

`dump` 是 MySQL 原生 SQL 备份入口；不要用 `dbtalk export` 代替它。只有用户同时要求 JSONL 数据导出时，才额外运行 `dbtalk export`。

```bash
dbtalk mysql dump --dsn-env DBTALK_DSN_APP --database app --output ./data/app.sql
dbtalk mysql dump --dsn-env DBTALK_DSN_APP --database app --output ./data/app.sql --archive
dbtalk mysql dump --dsn-env DBTALK_DSN_APP --database app --exclude-table audit_log
```

`--archive` 写入 `.sql.gz`；输出路径没有 `.gz` 后缀时会自动追加。dump 固定保留顶层 `USE` 并传递 `--no-create-db`，不会生成 `CREATE DATABASE` 或 `DROP DATABASE`。`--skip-definer` 是显式可选项，只向原生 `mysqldump` 传递该参数；默认保留 `DEFINER`，客户端不支持时直接失败。可重复的 `--exclude-table NAME` 跳过该表对象，映射为 `mysqldump --ignore-table=<目标库>.NAME`；不预检表是否存在，未知名称由 native client 处理。restore 不会建回被排除的表。非本机 MySQL host 使用连接压缩；`localhost` 与 `127.0.0.1` 不使用该参数。

dump 先写同目录临时文件，成功且非空后才发布；客户端、复制、压缩或校验失败时清理临时文件，不替换已有最终制品。默认时间戳文件发生同秒冲突时追加稳定序号，不覆盖已有制品。完成后确认 CLI 输出的最终文件存在且大小合理。备份文件属于业务数据，放在被 Git 忽略的 `data/` 或其他受控目录中。

## Restore

restore 需要已有的 `.sql` 或 `.sql.gz` 文件、MySQL DSN 和明确 target database，按 `--database > DSN database > 失败` 决定。目标库必须已存在；restore 不会创建或删除数据库。

```bash
dbtalk mysql restore --dsn-env DBTALK_DSN_APP --database app --input ./data/app-20260820-120000.sql.gz
```

`--database TARGET` 优先于 DSN database；无库名 DSN 必须显式提供该选项。目标库由 restore 前的只读探测确认存在。输入中的 `CREATE DATABASE` 或 `DROP DATABASE` 会在导入客户端启动前被拒绝，不能依靠 restore 执行数据库生命周期操作。存在的顶层 `USE` 只在临时输入中重写为目标库，原始 dump 文件不会修改。

restore 会覆盖或删除 dump 中同名表及数据，不能整体回滚。完成后确认命令成功退出，并按需检查目标表数或代表性数据。dump/restore 的 stderr 生命周期日志包含阶段、`elapsed_ms` 和字节数；stdout 只输出最终路径或结果摘要。

完整还原已有目标库时显式指定 `--clean`：先关闭清理会话的外键检查，删除目标库的全部表、视图、存储函数、存储过程和事件，再导入备份。备份之外的对象及 dump 排除的旧表也会删除；数据库本身、账号和授权保留。禁止对 MySQL 系统库使用该选项，执行账号必须具有目标库对象管理权限。输入预检、对象枚举或清理失败时停止导入；MySQL 清理和恢复不能整体回滚。未指定 `--clean` 时保留原生 dump 导入行为。

当 DSN 指向本机且请求端口唯一对应一个运行中的 Docker 容器时，restore 的目标库预检和实际导入都在该容器内通过 `docker exec` 使用默认 Unix socket；只有未识别到唯一映射容器时，才使用宿主机 `mysql` 或临时 Docker client fallback。

## 运行路径与故障处理

- 密码只通过子进程环境变量 `MYSQL_PWD` 传递，输出中不得回显。
- 已识别的本机端口映射使用目标数据库容器内的默认 socket，不传 `-h` 或 `-P`；Docker 临时客户端回退连接本机数据库时仍使用 `host.docker.internal`。
- Docker daemon 不可用或本地没有 `mysql` 镜像时，如实报告并停止；不要自动重试、拉取镜像、创建目标库或停止其他容器。
- dump 和 restore 没有内建超时。大文件操作仅按用户指定频率检查状态，不要中断仍在执行的 restore。

## User 与授权

先查看命令帮助：

```bash
dbtalk mysql user --help
dbtalk mysql grant --help
dbtalk mysql revoke --help
dbtalk mysql permissions --help
```

user 管理和 grant/revoke 需要 canonical 管理 DSN；user 操作可省略 database path，grant/revoke 未传 `--database` 时仍需要 DSN database。密码只能通过 `--password-env NAME` 引用，不得作为 CLI 值或输出内容。`--password-env` 的 `DBTALK_*` 名称先读进程环境，变量不存在时才读当前目录 `.env`；进程变量存在但为空会失败且不回退，非 `DBTALK_*` 名称不读 dotenv。创建、启用和禁用必须提供精确的 `--user` 和 `--host`；允许 `localhost`、单个 DNS 名称、IPv4、IPv6，以及字面量 `%` 账号 host。`password`、`user drop`、`grant` 和 `revoke` 使用 `--host` 或 `--all-hosts` 二者之一：`--host` 只改一个精确账号，`--all-hosts` 作用于该用户名下已存在的每个 host，没有匹配账号时失败。`%` 仅表示数据库中已存在的精确 `user@%` 账号，不得扩展为其他模式；仍不允许包含 `_` 或部分通配符的 host。

grant/revoke 支持 `readonly`、`readwrite`、`migrator` profile，或可重复的 `--privilege NAME`；两者互斥。权限层级为 `migrator > readwrite > readonly`：`readonly` 只读，`readwrite` 用于常规应用增删改查，`migrator` 再加入目标 database 的 DDL 和建库所需全局 `CREATE ON *.*`。MySQL 不区分建库 `CREATE` 与对象 `CREATE`，因此该能力为实例级能力，只用于受控迁移账号。固定 profile 不包含 `GRANT`、`REVOKE` 或 `GRANT OPTION`。`--database` 可省略，默认使用 DSN database。执行启用、禁用、轮换密码、删除、授权或撤销前，必须确认目标、资源、profile/privilege 和写入权限，并传入 `--yes`。`permissions list/show` 可查看当前 DSN 可见的原生授权，并支持主体和 database 筛选。不要传入完整 SQL；细粒度 privilege 由 MySQL 服务端决定是否允许。
