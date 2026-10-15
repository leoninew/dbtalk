# MySQL 手册

`dbtalk mysql` 用于管理 MySQL 数据库，以及创建和还原原生 SQL dump。每个子命令都必须接受且只接受一个 canonical `--dsn DSN` 或 `--dsn-env NAME`；Agent 工作流只使用 `--dsn-env`。

```bash
uv run dbtalk mysql --help
uv run dbtalk mysql schema --help
uv run dbtalk mysql dump --help
uv run dbtalk mysql restore --help
uv run dbtalk mysql permissions --help
```

## 命令概览

| 命令 | 用途 |
| --- | --- |
| `schema list/create/drop` | 查看、创建或删除 MySQL schema/database。 |
| `user list/create/enable/disable/password/drop` | 管理账号生命周期，不授予业务权限。 |
| `grant` / `revoke` | 按 profile 或原生 `--privilege` 授予、撤销权限。 |
| `permissions list/show` | 查看当前 DSN 可见的原生授权，可按主体和 database 筛选。 |
| `session list/kill` | 查看或终止一个数据库内的客户端会话，可选择等待退出。 |
| `dump` / `restore` | 创建或恢复原生 SQL dump。 |

## DSN

MySQL 命令只接受明确的 `mysql+pymysql://` DSN：

按用途将 DSN 写入当前目录 `.env`：

```dotenv
DBTALK_DSN_APP=mysql+pymysql://user:password@host:3306/app
```

```bash
uv run dbtalk mysql dump \
  --dsn-env DBTALK_DSN_APP \
  --output ./data/app.sql

uv run dbtalk mysql restore \
  --dsn-env DBTALK_DSN_APP \
  --input ./data/app.sql.gz
```

`mysql://`、`postgresql://`、`postgres://`、Go 风格 DSN 和 host/user/password/database 分散参数都不是 canonical DSN。MySQL URL 的 database path 可省略；dump/restore 的目标随后按 `--database > DSN database > 失败` 决定。完整 DSN 可能包含密码；Agent 将它写入当前目录 `.env` 后只传 `--dsn-env`。密码只通过 `MYSQL_PWD` 传递给原生客户端，正常输出不会回显密码。

## Schema management

`schema` 子命令管理 MySQL schema/database 本身，不执行任意 SQL、不管理账号，也不替代 dump/restore。DSN 可省略 database path；所用账号需要相应的 MySQL 数据库管理权限。

```dotenv
DBTALK_DSN_MYSQL_MANAGEMENT=mysql+pymysql://operator:password@db.example.com:3306/mysql
```

```bash
uv run dbtalk mysql schema list --dsn-env DBTALK_DSN_MYSQL_MANAGEMENT
uv run dbtalk mysql schema create --dsn-env DBTALK_DSN_MYSQL_MANAGEMENT --name app_db
uv run dbtalk mysql schema drop --dsn-env DBTALK_DSN_MYSQL_MANAGEMENT --name app_db --yes
uv run dbtalk mysql schema drop --dsn-env DBTALK_DSN_MYSQL_MANAGEMENT --name app_db --if-exists --yes
```

`list` 输出可见数据库名。`create` 使用服务端默认创建属性。`drop` 是不可逆操作，必须显式提供 `--yes`，以 `--name` 为删除目标，连接后读取当前会话数据库；若与 `--name` 相同则拒绝。失败时命令只报告动作失败，不会输出 DSN 密码。MySQL 的名称、权限和 DDL 行为由服务器决定，执行前确认目标名称和连接账号。

`drop --if-exists` 使用原生 `DROP DATABASE IF EXISTS`，目标库不存在时也成功，适合 `drop && create` 重置流程；不传该选项时，目标不存在仍会失败。该选项仍要求 `--yes`，仍拒绝删除当前连接库，连接或权限等其他错误照常失败。

## 会话管理

`session list` 和 `session kill` 按 `--database > DSN database > 失败` 确定目标。显式参数同时覆盖管理连接的数据库，执行账号必须能访问目标库；两者都没有时在建立连接前失败，包括指定 `--id` 的情况。

以下为 Bash 示例，变量直接声明在命令文件中，替换占位值后执行：

```bash
export DBTALK_DSN_MYSQL_ADMIN='mysql+pymysql://operator:password@127.0.0.1:3306/mysql'
DBTALK_DATABASE='app'
DBTALK_SESSION_ID='12345'

dbtalk mysql session list --dsn-env DBTALK_DSN_MYSQL_ADMIN --database "$DBTALK_DATABASE"
dbtalk mysql session kill --dsn-env DBTALK_DSN_MYSQL_ADMIN \
  --database "$DBTALK_DATABASE" --id "$DBTALK_SESSION_ID" --wait 5 --yes
# 省略 --id：终止目标库当前匹配的客户端集合
dbtalk mysql session kill --dsn-env DBTALK_DSN_MYSQL_ADMIN \
  --database "$DBTALK_DATABASE" --wait 5 --yes
```

列表来自 `performance_schema.threads`，按连接 ID 排序，显示 database、user、client、state、command 和 `state_seconds`。仅选择默认数据库匹配的普通前台客户端，排除本次管理连接及明确的系统、复制线程；空结果为 `(0 rows)`。**默认数据库不是访问范围**：默认库为其他库、通过全限定名访问目标库的连接不会列入，也不能据此断言目标库没有其他访问者或锁持有者。

需要启用 Performance Schema，并具有 `performance_schema.threads` 的 SELECT 权限；该表与 SHOW PROCESSLIST 不同，不按 PROCESS 权限过滤其他用户的行。源未启用、不可读或当前线程身份不可见时明确失败。终止自己账号的其他连接通常不需额外终止权限；终止其他账号需要 `CONNECTION_ADMIN`，对 `SYSTEM_USER` 账号还需相应权限。命令不会自动授权或切换数据源。

`kill` 必须提供 `--yes`。`--id` 使用连接 ID（`PROCESSLIST_ID`），仍受数据库及普通客户端范围限制。发送前复核内部 `THREAD_ID`、数据库和用户，范围改变时跳过；MySQL 原生 KILL 只接受连接 ID，复核与发送间仍有竞争窗口。等待阶段按原身份确认，切换默认库不会被误报为退出。

省略 `--wait` 时不轮询，`requested` 只表示请求成功返回。可选 `--wait SECONDS` 接受有限正数和小数，发送结束后整批共享一个退出确认预算；这是等待阶段预算，不是整条命令的硬超时。新增或连接池重建的会话不加入快照，不提供 `session cancel`。

| 状态 | 含义 |
| --- | --- |
| `requested` | 请求成功返回，未等待退出。 |
| `terminated` | 等待确认原身份已消失。 |
| `gone` | 请求前或发送时确认原目标已经退出。 |
| `changed` | 发送前身份或范围变化，未向变更后的目标发送。 |
| `failed` | 权限、请求或身份确认失败；网络故障时请求可能已生效。 |
| `timeout` | 已请求，但等待预算内未确认退出。 |
| `not_attempted` | 连接失效后尚未发送的目标。 |

逐目标输出保留部分成功；前三种状态和空批量结果返回 0，其余状态返回 1，参数或确认缺失返回 2。显式 ID 未匹配时失败。连接失效后不重连或重发请求。KILL CONNECTION 按 MySQL 原生规则中断整个会话；事务表的未提交事务回滚，已经产生的非事务影响无法撤销。

等待轮询使用必需配置 `session.poll_interval_seconds`，随附 YAML 为 `0.1`，可由 `DBTALK_SESSION__POLL_INTERVAL_SECONDS` 覆盖，加载时集中校验为有限正数。查询与终止响应复用 `database.query_timeout_seconds`、`database.exec_timeout_seconds`；查询使用 `max_execution_time`，连接 read/write timeout 使用操作预算。服务端超时能力及已有阻塞 I/O 受原生实现限制，不承诺网络故障下严格按秒中断。仅根 `dbtalk -v mysql session ...` 开启日志。

## Dump

```bash
uv run dbtalk mysql dump \
  --dsn-env DBTALK_DSN_APP \
  --database app \
  --output ./data/app.sql \
  --skip-definer \
  --archive \
  --exclude-table audit_log
```

| 选项 | 说明 |
| --- | --- |
| `--dsn DSN` / `--dsn-env NAME` | 必须二选一的 MySQL DSN。 |
| `--database TARGET` | 要导出的 database；优先于 DSN database。 |
| `--output FILE_OR_DIRECTORY` | SQL 制品输出文件或已有输出目录。 |
| `--archive` | 写入 gzip 压缩 dump。 |
| `--skip-definer` | 将原生 `mysqldump --skip-definer` 透传给客户端。默认保留 `DEFINER`。 |
| `--exclude-table NAME` | 可重复。跳过该表对象（无 DDL、无数据），映射为 `mysqldump --ignore-table=<目标库>.NAME`。不预检表是否存在。 |

dump 的目标按 `--database > DSN database > 失败` 决定。dump 固定使用 `-B` 保留顶层 `USE`，并始终传递 `--no-create-db`，不会生成 `CREATE DATABASE` 或 `DROP DATABASE`。`--skip-definer` 不通过 `sed` 或其他文本替换实现；客户端不支持该参数时直接失败。`--exclude-table` 与 JSONL 同名，但走 native client：restore 不会建回被排除的表；未知表名交给 `mysqldump` 处理。

省略 `--output` 时使用配置中的 `mysql.output_directory`（默认 `data/`），生成 `<database>-<timestamp>.sql`。同秒已有文件时追加稳定序号，不覆盖已有制品。dump 先写同目录临时文件，成功且非空后才发布；`--archive` 的 gzip 文件也遵循相同规则。

## Restore

```bash
uv run dbtalk mysql restore \
  --dsn-env DBTALK_DSN_APP \
  --database app \
  --input ./data/app.sql.gz
```

| 选项 | 说明 |
| --- | --- |
| `--dsn DSN` / `--dsn-env NAME` | 必须二选一的 MySQL DSN。 |
| `--database TARGET` | 已存在的恢复目标库；优先于 DSN database。 |
| `--input FILE` | 必填的 SQL 或 gzip 压缩 SQL 输入文件。 |
| `--clean` | 先清空目标库的所有表、视图、存储函数、存储过程和事件，再导入备份。默认关闭。 |

restore 的目标按 `--database > DSN database > 失败` 决定。目标库必须在 restore 前由独立的 `dbtalk mysql schema create` 或其他明确流程创建。restore 会先检查目标库存在，再拒绝输入中的 `CREATE DATABASE` 或 `DROP DATABASE`，并只将顶层 `USE` 重写为目标库；不会修改原始输入文件。restore 可能覆盖或删除现有数据，执行前确认目标连接、输入来源和写入授权。

完整还原已有目标库时显式添加 `--clean`。输入预检和目标库探测成功后，restore 枚举目标库当前对象，并在同一个清理会话中关闭 `FOREIGN_KEY_CHECKS`，删除全部表、视图、存储程序和事件（表上的触发器随表删除），包括备份中没有的对象；随后导入备份中的定义和数据。目标数据库本身的属性、账号和授权保留，禁止对 MySQL 系统库使用 `--clean`。未指定该选项时，仍按原生 SQL dump 导入，备份之外的旧对象可能保留。

清理与导入都要求执行账号具有相应的目标库对象管理权限；对象枚举或清理失败时停止导入。MySQL DDL 无法整体回滚，清理或导入失败时目标库可能处于部分清理或恢复状态。`--clean` 不会保留 dump 排除的旧表。

dump 和 restore 会在 stderr 输出 `started`、`progress`、`completed` 和 `failed` 生命周期日志，包含阶段、耗时和可测量字节数；stdout 只输出最终路径或结果摘要。日志和错误不会输出密码、完整含密码 DSN 或 SQL 内容。

## 客户端与故障处理

对于使用 `localhost` 或 `127.0.0.1` 的 DSN，若请求端口唯一对应一个运行中的 Docker 容器，dump 和 restore 都直接在该数据库容器中执行原生 MySQL 客户端，通过默认 Unix socket 连接，不经过 `host.docker.internal`。dump 完成后将制品复制到请求的宿主机路径，restore 通过 stdin 将输入流送入该容器。未识别到唯一映射容器时，才使用本机客户端；本机客户端也不可用时，使用配置的 Docker image：

```yaml
mysql:
  output_directory: data
  client_image: mysql:8.0.39
  zero_datetime_as_null: true
```

可通过 `DBTALK_MYSQL__OUTPUT_DIRECTORY`、`DBTALK_MYSQL__CLIENT_IMAGE` 和 `DBTALK_MYSQL__ZERO_DATETIME_AS_NULL` 覆盖这些值。前两个值不提供连接、target 或恢复策略；host、port、user、password、database 始终来自本次 DSN 和可选的 `--database`。`zero_datetime_as_null` 只用于 JSONL `database export` 中 MySQL `DATE`、`DATETIME`、`TIMESTAMP` 的完整零日期：默认转为 JSON `null`，设为 `false` 时明确失败。配置 image 不在本地时，dbtalk 会打印 `docker pull` 日志并拉取该精确 image；不会扫描其他 `mysql:*` image、创建数据库或猜测容器。

## 用户与授权

`dbtalk mysql user` 管理 MySQL `username@host` account；`dbtalk mysql grant` 和 `revoke` 与 user 命令同级。所有管理命令使用管理 DSN，且必须在 `--dsn` 和 `--dsn-env` 间二选一。管理 DSN 可省略 database path；grant/revoke 未显式给出 `--database` 时仍需要 DSN database 作为资源目标。

```dotenv
DBTALK_DSN_MYSQL_ADMIN=mysql+pymysql://admin:password@db.example:3306/app
DBTALK_MYSQL_APP_PASSWORD=change-me
DBTALK_MYSQL_ROOT_PASSWORD=change-me
```

```bash
uv run dbtalk mysql user create --dsn-env DBTALK_DSN_MYSQL_ADMIN \
  --user app_user --host app.example --password-env DBTALK_MYSQL_APP_PASSWORD
uv run dbtalk mysql user password --dsn-env DBTALK_DSN_MYSQL_ADMIN \
  --user root --all-hosts --password-env DBTALK_MYSQL_ROOT_PASSWORD --yes
uv run dbtalk mysql grant --dsn-env DBTALK_DSN_MYSQL_ADMIN \
  --user app_user --host app.example --database app --profile readwrite --yes

uv run dbtalk mysql grant --dsn-env DBTALK_DSN_MYSQL_ADMIN \
  --user app_user --host app.example --privilege SELECT \
  --privilege UPDATE --yes
```

创建、启用和禁用必须提供精确 `--host`：`localhost`、单个 DNS 名称、IPv4、IPv6，或字面量 `%`。`password`、`user drop`、`grant` 和 `revoke` 使用 `--host` 或 `--all-hosts` 二者之一；`--all-hosts` 会按现有账号作用于该用户名下的每个 host，没有匹配账号时失败。密码只能通过 `--password-env` 引用；`DBTALK_*` 名称在进程变量不存在时读取当前目录 `.env`，进程变量存在但为空会失败且不回退。非 `DBTALK_*` 名称不读取 dotenv。密码不会显示在命令输出、日志或错误中。

授权目标 database 可省略，省略时使用 DSN database。profile 按 `migrator > readwrite > readonly` 包含：`readonly` 授予 `SELECT, SHOW VIEW`；`readwrite` 再授予 `INSERT, UPDATE, DELETE`；`migrator` 再授予目标 database 上的 DDL，以及创建 database 所需的全局 `CREATE ON *.*`。MySQL 无法将建库的 `CREATE` 与对象 `CREATE` 分离为两种权限，因此 `migrator` 的建库能力是实例级能力；只应授予受控迁移账号。固定 profile 不包含 `GRANT`、`REVOKE`、`GRANT OPTION` 或其他权限管理能力。也可重复指定 `--privilege NAME` 使用数据库服务端支持的细粒度权限；它与 `--profile` 互斥。

```bash
uv run dbtalk mysql permissions list --dsn-env DBTALK_DSN_MYSQL_ADMIN
uv run dbtalk mysql permissions show --dsn-env DBTALK_DSN_MYSQL_ADMIN --user app_user --host app.example
```

`permissions list` 默认展示当前 DSN 可见的原生权限，可按账号和 database 筛选；`show` 查看一个精确的 `user@host`。输出直接来自 MySQL 原生权限查询。不支持 `WITH GRANT OPTION`、代理身份或超级用户管理。

启用、禁用、轮换密码、删除、授权和撤销都要求 `--yes`，并拒绝修改当前管理 account。创建和授权不会隐式赋予其他权限；撤销 profile 可能中断使用该 account 的应用连接。
