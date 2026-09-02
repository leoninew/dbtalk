# MySQL 手册

`dbtalk mysql` 用于管理 MySQL 数据库，以及创建和还原原生 SQL dump。每个子命令都必须接受且只接受一个 canonical `--dsn DSN` 或 `--dsn-env NAME`。

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
| `user list/create/enable/disable/rotate-password/drop` | 管理账号生命周期，不授予业务权限。 |
| `grant` / `revoke` | 按 profile 或原生 `--privilege` 授予、撤销权限。 |
| `permissions list/show` | 查看当前 DSN 可见的原生授权，可按主体和 database 筛选。 |
| `dump` / `restore` | 创建或恢复原生 SQL dump。 |

## DSN

MySQL 命令只接受明确的 `mysql+pymysql://` DSN：

```bash
uv run dbtalk mysql dump \
  --dsn 'mysql+pymysql://user:password@host:3306/app' \
  --output ./data/app.sql

export APP_DSN='mysql+pymysql://user:password@host:3306/app'
uv run dbtalk mysql restore \
  --dsn-env APP_DSN \
  --input ./data/app.sql.gz
```

`mysql://`、`postgresql://`、`postgres://`、Go 风格 DSN 和 host/user/password/database 分散参数都不是 canonical DSN。MySQL URL 的 database path 可省略；dump/restore 的目标随后按 `--database > DSN database > 失败` 决定。完整 DSN 可能包含密码；脚本中优先使用 `--dsn-env`。密码只通过 `MYSQL_PWD` 传递给原生客户端，正常输出不会回显密码。

## Schema management

`schema` 子命令管理 MySQL schema/database 本身，不执行任意 SQL、不管理账号，也不替代 dump/restore。DSN 可省略 database path；所用账号需要相应的 MySQL 数据库管理权限。

```bash
export MYSQL_MANAGEMENT_DSN='mysql+pymysql://operator:password@db.example.com:3306/mysql'

uv run dbtalk mysql schema list --dsn-env MYSQL_MANAGEMENT_DSN
uv run dbtalk mysql schema create --dsn-env MYSQL_MANAGEMENT_DSN --name app_db
uv run dbtalk mysql schema drop --dsn-env MYSQL_MANAGEMENT_DSN --name app_db --yes
```

`list` 输出可见数据库名。`create` 使用服务端默认创建属性。`drop` 是不可逆操作，必须显式提供 `--yes`；失败时命令只报告动作失败，不会输出 DSN 密码。MySQL 的名称、权限和 DDL 行为由服务器决定，执行前确认目标名称和连接账号。

## Dump

```bash
uv run dbtalk mysql dump \
  --dsn 'mysql+pymysql://user:password@host:3306/' \
  --database app \
  --output ./data/app.sql \
  --skip-definer \
  --archive
```

| 选项 | 说明 |
| --- | --- |
| `--dsn DSN` / `--dsn-env NAME` | 必须二选一的 MySQL DSN。 |
| `--database TARGET` | 要导出的 database；优先于 DSN database。 |
| `--output FILE_OR_DIRECTORY` | SQL 制品输出文件或已有输出目录。 |
| `--archive` | 写入 gzip 压缩 dump。 |
| `--skip-definer` | 将原生 `mysqldump --skip-definer` 透传给客户端。默认保留 `DEFINER`。 |

dump 的目标按 `--database > DSN database > 失败` 决定。dump 固定使用 `-B` 保留顶层 `USE`，并始终传递 `--no-create-db`，不会生成 `CREATE DATABASE` 或 `DROP DATABASE`。`--skip-definer` 不通过 `sed` 或其他文本替换实现；客户端不支持该参数时直接失败。

省略 `--output` 时使用配置中的 `mysql.output_directory`（默认 `data/`），生成 `<database>-<timestamp>.sql`。同秒已有文件时追加稳定序号，不覆盖已有制品。dump 先写同目录临时文件，成功且非空后才发布；`--archive` 的 gzip 文件也遵循相同规则。

## Restore

```bash
uv run dbtalk mysql restore \
  --dsn 'mysql+pymysql://operator:password@host:3306/maintenance' \
  --database app \
  --input ./data/app.sql.gz
```

| 选项 | 说明 |
| --- | --- |
| `--dsn DSN` / `--dsn-env NAME` | 必须二选一的 MySQL DSN。 |
| `--database TARGET` | 已存在的恢复目标库；优先于 DSN database。 |
| `--input FILE` | 必填的 SQL 或 gzip 压缩 SQL 输入文件。 |

restore 的目标按 `--database > DSN database > 失败` 决定。目标库必须在 restore 前由独立的 `dbtalk mysql schema create` 或其他明确流程创建。restore 会先检查目标库存在，再拒绝输入中的 `CREATE DATABASE` 或 `DROP DATABASE`，并只将顶层 `USE` 重写为目标库；不会修改原始输入文件。restore 可能覆盖或删除现有数据，执行前确认目标连接、输入来源和写入授权。

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

```bash
export MYSQL_ADMIN_DSN='mysql+pymysql://admin:password@db.example:3306/app'
export APP_PASSWORD='application-password'

uv run dbtalk mysql user create --dsn-env MYSQL_ADMIN_DSN \
  --user app_user --host app.example --password-env APP_PASSWORD
uv run dbtalk mysql grant --dsn-env MYSQL_ADMIN_DSN \
  --user app_user --host app.example --database app --profile readwrite --yes

uv run dbtalk mysql grant --dsn-env MYSQL_ADMIN_DSN \
  --user app_user --host app.example --privilege SELECT \
  --privilege UPDATE --yes
```

MySQL user 必须显式提供一个精确 host：`localhost`、单个 DNS 名称、IPv4 或 IPv6。`%`、`_` 和其他通配 host 均被拒绝。密码只能通过 `--password-env` 引用的环境变量输入；不会显示在命令输出、日志或错误中。

授权目标 database 可省略，省略时使用 DSN database。profile 按 `migrator > readwrite > readonly` 包含：`readonly` 授予 `SELECT, SHOW VIEW`；`readwrite` 再授予 `INSERT, UPDATE, DELETE`；`migrator` 再授予目标 database 上的 DDL，以及创建 database 所需的全局 `CREATE ON *.*`。MySQL 无法将建库的 `CREATE` 与对象 `CREATE` 分离为两种权限，因此 `migrator` 的建库能力是实例级能力；只应授予受控迁移账号。固定 profile 不包含 `GRANT`、`REVOKE`、`GRANT OPTION` 或其他权限管理能力。也可重复指定 `--privilege NAME` 使用数据库服务端支持的细粒度权限；它与 `--profile` 互斥。

```bash
uv run dbtalk mysql permissions list --dsn-env MYSQL_ADMIN_DSN
uv run dbtalk mysql permissions show --dsn-env MYSQL_ADMIN_DSN --user app_user --host app.example
```

`permissions list` 默认展示当前 DSN 可见的原生权限，可按账号和 database 筛选；`show` 查看一个精确的 `user@host`。输出直接来自 MySQL 原生权限查询。不支持 `WITH GRANT OPTION`、代理身份或超级用户管理。

启用、禁用、轮换密码、删除、授权和撤销都要求 `--yes`，并拒绝修改当前管理 account。创建和授权不会隐式赋予其他权限；撤销 profile 可能中断使用该 account 的应用连接。
