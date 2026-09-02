# PostgreSQL 手册

`dbtalk postgres` 使用原生 `pg_dump` 和 `pg_restore` 创建、校验和恢复单个 PostgreSQL 数据库的 custom archive，并可管理 PostgreSQL 数据库本身。它不提供物理备份、WAL/PITR、`pg_dumpall` 或 cluster 全局 role/tablespace 恢复。

```bash
uv run dbtalk postgres --help
uv run dbtalk postgres schema --help
uv run dbtalk postgres dump --help
uv run dbtalk postgres restore --help
uv run dbtalk postgres permissions --help
```

## 命令概览

| 命令 | 用途 |
| --- | --- |
| `schema list/create/drop` | 查看、创建或删除 PostgreSQL schema/database。 |
| `role list/create/enable/disable/rotate-password/drop` | 管理 role 生命周期，不授予业务权限。 |
| `grant` / `revoke` | 按 profile 或原生 `--privilege` 授予、撤销 database/schema 权限。 |
| `permissions list/show` | 查看当前 DSN 可见的原生授权，可按 role、database、schema 筛选。 |
| `dump` / `restore` | 创建或恢复单库 custom archive。 |

## DSN 与客户端

所有命令必须且只能提供一个 `--dsn DSN` 或 `--dsn-env NAME`。PostgreSQL DSN 必须使用明确的 `postgresql+psycopg://` 格式：

```bash
export APP_DSN='postgresql+psycopg://backup:password@db.example.com:5432/app?sslmode=require'
```

脚本中优先使用 `--dsn-env`，避免密码出现在进程参数中。`dbtalk` 向 native client 传递无密码的 libpq URI；本机客户端读取临时 `.pgpass`，Docker client 通过子进程环境读取密码。正常输出、日志和错误摘要不会回显密码。

PostgreSQL URL 的 database path 在语法上可省略。`schema list/create`、role 和权限查看可使用这种 DSN；`schema drop` 必须带有维护 database 以避免删除当前连接数据库。dump/restore 的目标按 `--database > DSN database > 失败` 决定；grant/revoke 未显式给出 `--database` 或 `--schema` 时仍需要 DSN database。

对于 `localhost` 或 `127.0.0.1`，若请求端口唯一对应一个运行中的 Docker PostgreSQL 容器，dump 和 restore 优先复用该容器：通过 `docker exec` 调用容器内 `pg_dump` / `pg_restore`，使用容器默认 Unix socket；dump 的 archive 通过临时文件和 `docker cp` 取回，restore 通过 `docker cp` 放入后导入并清理。未识别到唯一映射容器时，才优先使用本机 `pg_dump` / `pg_restore`；本机客户端缺失时使用配置的 Docker image：

```yaml
postgres:
  output_directory: data
  client_image: postgres:18-alpine
```

可用 `DBTALK_POSTGRES__CLIENT_IMAGE` 覆盖 image。配置 image 不在本地时，dbtalk 会打印 `docker pull` 日志并拉取该精确 image；不会安装本机客户端或根据源库版本自动选择 tag。PostgreSQL 18+ 是当前支持基线；`pg_dump` client 必须不低于源服务端 major，恢复目标通常不应低于备份来源。

`postgres` 与 `mysql` 使用同一类配置边界：只配置默认 dump 目录和 Docker client image；连接、target、制品文件路径与恢复策略仍由每次命令决定。可用 `DBTALK_POSTGRES__OUTPUT_DIRECTORY` 覆盖默认 dump 目录。

## Schema management

`schema` 子命令管理 PostgreSQL schema/database，不执行任意 SQL、不管理 role，也不替代 dump/restore。`list` 与 `create` 的管理 DSN 可以省略 database path；`drop` 必须连接到目标以外的维护 database，通常为 `postgres`。账号还需要相应的建库或删库权限。

```bash
export POSTGRES_MANAGEMENT_DSN='postgresql+psycopg://operator:password@db.example.com:5432/postgres'

uv run dbtalk postgres schema list --dsn-env POSTGRES_MANAGEMENT_DSN
uv run dbtalk postgres schema create --dsn-env POSTGRES_MANAGEMENT_DSN --name app_db
uv run dbtalk postgres schema drop --dsn-env POSTGRES_MANAGEMENT_DSN --name app_db --yes
```

`list` 输出非模板、可连接的数据库。`create` 使用服务端默认创建属性。`drop` 是不可逆操作，必须显式提供 `--yes`，且不能删除管理 DSN 正在连接的数据库。存在其他连接、权限不足或服务器策略限制时，命令会失败；首版不会主动终止其他会话。

## Dump

```bash
uv run dbtalk postgres dump \
  --dsn-env APP_DSN \
  --database app \
  --output ./data/app.dump \
  --compression-level 6
```

dump 的目标按 `--database > DSN database > 失败` 决定，并始终生成 `pg_dump --format=custom` 的 `.dump` archive。省略 `--output` 时创建 `postgres.output_directory`，并生成 `<database>-<timestamp>.dump`；已有目录同样生成时间戳文件。显式文件路径的父目录必须已经存在。

custom archive 具有 PostgreSQL 原生内部压缩，不能等同于 `.sql.gz`。不要给 `.dump` 再套 gzip；`pg_restore` 可以直接读取它。`--compression-level` 仅接受 `0` 到 `9`；省略时保留 native client 的默认 archive 压缩行为。

## Restore

```bash
uv run dbtalk postgres restore \
  --dsn-env APP_DSN \
  --database app \
  --input ./data/app.dump \
  --clean \
  --if-exists \
  --jobs 4
```

目标数据库必须已经存在，并按 `--database > DSN database > 失败` 选择。restore 会先运行 `pg_restore --list` 校验 archive；无效 archive 在连接和写入目标库前失败。

默认恢复跳过 owner 与 ACL，以支持不同账号管理的环境。需要原样恢复时，显式传入 `--preserve-owner` 和/或 `--preserve-privileges`。`--jobs` 只接受正整数，适用于 custom archive。

`--clean` 会删除 archive 将恢复的目标对象，默认关闭；`--if-exists` 只能与 `--clean` 同时使用。restore 并非整体原子操作，发生错误时目标数据库可能保留部分恢复状态。执行前必须确认目标 DSN、archive 来源和写入授权。

## Scope

常规 logical dump 包含单库中的 schema、表数据、索引、约束、序列、视图、函数和 extension 声明。它不备份 cluster 全局 role、role password、tablespace 或实例配置；跨环境恢复还要求目标实例具备兼容的 extension 与客户端版本。需要全量 cluster 灾备或时间点恢复时，应使用独立的 PostgreSQL 物理备份方案。

## Role 与授权

`dbtalk postgres role` 管理具备 `LOGIN` 的 PostgreSQL role；`dbtalk postgres grant` 和 `revoke` 与 role 命令同级。所有管理命令使用管理 DSN，且必须在 `--dsn` 和 `--dsn-env` 间二选一。

```bash
export POSTGRES_ADMIN_DSN='postgresql+psycopg://admin:password@db.example:5432/app'
export APP_PASSWORD='application-password'

uv run dbtalk postgres role create --dsn-env POSTGRES_ADMIN_DSN \
  --role app_role --password-env APP_PASSWORD
uv run dbtalk postgres grant --dsn-env POSTGRES_ADMIN_DSN \
  --role app_role --schema app --profile readwrite --yes

uv run dbtalk postgres grant --dsn-env POSTGRES_ADMIN_DSN \
  --role app_role --schema app --privilege USAGE \
  --privilege CREATE --yes
```

新 role 默认是 `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`。密码只能通过 `--password-env` 引用的环境变量输入；不会显示在命令输出、日志或错误中。

授权目标支持 database 或 schema，未指定时使用 DSN database。profile 按 `migrator > readwrite > readonly` 包含：`readonly` 提供基础只读权限；以 schema 为目标时，`readwrite` 再提供现有表的 `SELECT, INSERT, UPDATE, DELETE` 以及 sequence 的 `USAGE, SELECT, UPDATE`；`migrator` 再授予 schema `CREATE`，并在 database 目标上授予 `CREATE`，同时设置 role 的全局 `CREATEDB` 属性以允许建库。`CREATEDB` 不是某个 database/schema 上的普通授权，撤销 `migrator` 会将该 role 设为 `NOCREATEDB`。固定 profile 不添加 `GRANT OPTION` 或角色管理能力。schema profile 不修改 default privileges，因此不会自动覆盖未来创建的表或序列；migrator 必须拥有它需要 `ALTER` 或 `DROP` 的现有对象。

```bash
uv run dbtalk postgres permissions list --dsn-env POSTGRES_ADMIN_DSN
uv run dbtalk postgres permissions show --dsn-env POSTGRES_ADMIN_DSN --role app_role
```

`permissions list` 默认展示当前 DSN 可见的原生权限，可按 role、database、schema 筛选；`show` 查看一个 role，资源筛选可选。输出直接来自 PostgreSQL 原生权限查询。

不支持把 table、sequence、function 作为独立资源参数，也不支持 role membership、`WITH GRANT OPTION` 或完整 SQL 文本；细粒度 privilege 仅作为数据库服务端校验的名称传入。启用、禁用、轮换密码、删除、授权和撤销都要求 `--yes`，并拒绝修改当前管理 role；撤销 profile 可能中断应用访问。
