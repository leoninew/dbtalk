---
name: dbtalk-mysql
description: 使用 dbtalk mysql 管理 MySQL schema/database、user、profile 授权，或导出和恢复 mysqldump SQL 文件。用户要求 MySQL schema/database create/drop/list、用户、授权、backup、dump、restore 或导入 .sql 时使用。
---

# dbtalk MySQL

使用 `dbtalk mysql` 管理 MySQL schema/database、user、profile 授权，或处理原生 SQL dump 和 restore。要求发布安装的 `dbtalk` 可执行文件位于 `PATH` 中。

## Dump 执行语义

`dbtalk mysql dump` 对调用机的 `localhost` 或 `127.0.0.1` 先检查请求端口是否唯一对应一个运行中的 Docker 容器；若是，直接在该容器中运行 `mysqldump`，默认通过 Unix socket 连接，并将 SQL 文件复制到调用机指定的输出路径。未识别到唯一映射容器时，才调用当前机器上的 `mysqldump`；本机客户端也不可用时，再回退到该机器已有的 Docker `mysql` 镜像。MySQL 服务器不需要安装 dbtalk。

DSN 的 host 从当前执行机解释。`localhost` 或 `127.0.0.1` 指向当前执行机本身；只有 Docker 明确显示该端口唯一映射到运行中容器时，才使用该容器内的 socket 连接。一次 SQL dump 请求只授权执行该 dump；不应为远端 DSN 更换命令执行位置、猜测容器或安排其他连接方式。连接或客户端失败时，报告 dbtalk 的失败结果并停止。

不要安装客户端、拉取镜像或替换为其他备份工具。

## 配置与凭据

从项目根目录的 `dbtalk.yaml` 读取默认配置。设置 `DBTALK_ENVKEY=local` 时，`.env.local` 中的 `DBTALK_*` 值覆盖 YAML。数据库连接必须通过完整的 `--dsn DSN` 或 `--dsn-env NAME` 二选一提供，不再使用 host、port、user、password、database 分散参数。支持的 MySQL DSN 是 `mysql+pymysql://user:password@host:3306/database`。将含凭据的 DSN 保存到环境变量，或在用户已提供或明确授权该 DSN 时写入当前目录、Git 已忽略的 `.env` 中的 `DBTALK_*` 条目。`--dsn-env DBTALK_*` 优先使用同名进程环境变量；变量不存在时才读取当前目录 `.env`。不得猜测凭据，也不要将 DSN 写入命令行、日志、文档、`.env.example` 或提交记录：

```env
DBTALK_APP_DSN=mysql+pymysql://user:password@host:3306/database
```

## Database management

数据库生命周期操作使用 `dbtalk mysql schema`，与 query/exec、账号管理和 dump/restore 分离。管理 DSN 必须指向一个已有 MySQL 数据库，并使用具有相应数据库管理权限的账号。

```bash
dbtalk mysql schema list --dsn-env DBTALK_MYSQL_MANAGEMENT_DSN
dbtalk mysql schema create --dsn-env DBTALK_MYSQL_MANAGEMENT_DSN --name app_db
dbtalk mysql schema drop --dsn-env DBTALK_MYSQL_MANAGEMENT_DSN --name app_db --yes
```

先执行 `list` 核对目标。创建后只报告数据库名。删除不可逆，只有用户明确授权删除指定目标时才传入 `--yes`；不猜测目标、不执行任意 SQL、不创建或管理账号。

## Dump

dump 需要完整的 MySQL DSN。未传 `--output` 时，输出默认为当前目录的 `data/<database>-<timestamp>.sql`，并会自动创建配置的输出目录。显式传入 `--output` 且路径是已有目录时，也会在其中生成同样的时间戳文件。其他路径视为文件，父目录必须已存在；不存在的路径不会被推断为目录或自动创建。

`dump` 是 MySQL 原生 SQL 备份入口；不要用 `dbtalk export` 代替它。只有用户同时要求 JSONL 数据导出时，才额外运行 `dbtalk export`。

```bash
dbtalk mysql dump --dsn-env DBTALK_APP_DSN --output ./data/app.sql
dbtalk mysql dump --dsn-env DBTALK_APP_DSN --output ./data/app.sql --archive
```

`--archive` 写入 `.sql.gz`；输出路径没有 `.gz` 后缀时会自动追加。dump 固定保留顶层 `USE` 并传递 `--no-create-db`，不会生成 `CREATE DATABASE` 或 `DROP DATABASE`。`--skip-definer` 是显式可选项，只向原生 `mysqldump` 传递该参数；默认保留 `DEFINER`，客户端不支持时直接失败。非本机 MySQL host 使用连接压缩；`localhost` 与 `127.0.0.1` 不使用该参数。

dump 先写同目录临时文件，成功且非空后才发布；客户端、复制、压缩或校验失败时清理临时文件，不替换已有最终制品。默认时间戳文件发生同秒冲突时追加稳定序号，不覆盖已有制品。完成后确认 CLI 输出的最终文件存在且大小合理。备份文件属于业务数据，放在被 Git 忽略的 `data/` 或其他受控目录中。

## Restore

restore 需要已有的 `.sql` 或 `.sql.gz` 文件，以及完整的 MySQL DSN。目标库必须已存在；restore 不会创建或删除数据库。

```bash
dbtalk mysql restore --dsn-env DBTALK_APP_DSN --database app --input ./data/app-20260820-120000.sql.gz
```

`--database TARGET` 优先于 `mysqlrestore.database` 和 DSN database。DSN 仍必须包含 database，用于连接维护库；目标库由 restore 前的只读探测确认存在。输入中的 `CREATE DATABASE` 或 `DROP DATABASE` 会在导入客户端启动前被拒绝，不能依靠 restore 执行数据库生命周期操作。存在的顶层 `USE` 只在临时输入中重写为目标库，原始 dump 文件不会修改。

restore 会覆盖或删除 dump 中同名表及数据，不能整体回滚。完成后确认命令成功退出，并按需检查目标表数或代表性数据。dump/restore 的 stderr 生命周期日志包含阶段、`elapsed_ms` 和字节数；stdout 只输出最终路径或结果摘要。

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

user 管理和 grant/revoke 需要完整管理 DSN。密码只能通过 `--password-env NAME` 引用，不得作为 CLI 值或输出内容。MySQL user 必须提供精确的 `--user` 和 `--host`；允许 `localhost`、单个 DNS 名称、IPv4、IPv6，以及字面量 `%` 账号 host。`%` 仅表示数据库中已存在的精确 `user@%` 账号，不得扩展为其他模式；仍不允许包含 `_` 或部分通配符的 host。

grant/revoke 支持 `read-only`、`ddl`、`read-write`、`dml` profile，或可重复的 `--privilege NAME`；两者互斥。权限层级为 `dml > read-write > ddl > read-only`，其中 `read-write` 包含 `ddl` 和常规 DML，但不包含建库能力，`dml` 才增加建库所需权限。`--database` 可省略，默认使用 DSN database。执行启用、禁用、轮换密码、删除、授权或撤销前，必须确认目标、资源、profile/privilege 和写入权限，并传入 `--yes`。`permissions list/show` 可查看当前 DSN 可见的原生授权，并支持主体和 database 筛选。不要传入完整 SQL；细粒度 privilege 由 MySQL 服务端决定是否允许。
