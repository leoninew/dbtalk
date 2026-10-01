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
| `role list/create/enable/disable/password/drop` | 管理 role 生命周期，不授予业务权限。 |
| `grant` / `revoke` | 按 profile 或原生 `--privilege` 授予、撤销 database/schema 权限。 |
| `permissions list/show` | 查看当前 DSN 可见的原生授权，可按 role、database、schema 筛选。 |
| `owner reassign` | 预览或转移一个 role 在当前库及共享对象上的所有权。 |
| `dump` / `restore` | 创建或恢复单库 custom archive。 |

## DSN 与客户端

所有命令必须且只能提供一个 `--dsn DSN` 或 `--dsn-env NAME`。PostgreSQL DSN 必须使用明确的 `postgresql+psycopg://` 格式。`--dsn` 保留给直接集成；Agent 将 DSN 写入当前目录 `.env` 的 `DBTALK_DSN_*`，并只使用 `--dsn-env`：

```dotenv
DBTALK_DSN_APP=postgresql+psycopg://backup:password@db.example.com:5432/app?sslmode=require
```

`dbtalk` 向 native client 传递无密码的 libpq URI；本机客户端读取临时 `.pgpass`，Docker client 通过子进程环境读取密码。正常输出、日志和错误摘要不会回显密码。`.env.local` 或其他 dotenv 变体不会被加载。

PostgreSQL URL 的 database path 在语法上可省略。`schema list/create/drop`、role 和权限查看可使用这种 DSN。`schema drop` 以 `--name` 为删除目标，连接后检查当前会话数据库，不能删除当前正在连接的数据库。dump/restore 的目标按 `--database > DSN database > 失败` 决定。grant/revoke 在只授权 database 或两者都省略时，未给出 `--database` 则回退 DSN database；`--schema` 授权必须能确定所在库：`--database` 与 DSN database 二选一，可同时提供 `--database` 和 `--schema`，此时连接到 `--database` 再对 `--schema` 授权。

对于 `localhost` 或 `127.0.0.1`，若请求端口唯一对应一个运行中的 Docker PostgreSQL 容器，dump 和 restore 优先复用该容器：通过 `docker exec` 调用容器内 `pg_dump` / `pg_restore`，使用容器默认 Unix socket；dump 的 archive 通过临时文件和 `docker cp` 取回，restore 通过 `docker cp` 放入后导入并清理。未识别到唯一映射容器时，才优先使用本机 `pg_dump` / `pg_restore`；本机客户端缺失时使用配置的 Docker image：

```yaml
postgres:
  output_directory: data
  client_image: postgres:18-alpine
```

可用 `DBTALK_POSTGRES__CLIENT_IMAGE` 覆盖 image。配置 image 不在本地时，dbtalk 会打印 `docker pull` 日志并拉取该精确 image；不会安装本机客户端或根据源库版本自动选择 tag。PostgreSQL 18+ 是当前支持基线；`pg_dump` client 必须不低于源服务端 major，恢复目标通常不应低于备份来源。

`postgres` 与 `mysql` 使用同一类配置边界：只配置默认 dump 目录和 Docker client image；连接、target、制品文件路径与恢复策略仍由每次命令决定。可用 `DBTALK_POSTGRES__OUTPUT_DIRECTORY` 覆盖默认 dump 目录。

## Object ownership

`owner reassign` 默认只预览。必须提供目标 database、原 role、新 role 和管理 DSN；`--yes` 才执行 PostgreSQL 原生 `REASSIGN OWNED`：

```bash
uv run dbtalk postgres owner reassign \
  --dsn-env DBTALK_DSN_POSTGRES_ADMIN \
  --database app --from-role old_owner --to-role new_owner
uv run dbtalk postgres owner reassign \
  --dsn-env DBTALK_DSN_POSTGRES_ADMIN \
  --database app --from-role old_owner --to-role new_owner --yes
# 预览含共享对象时，还需在执行命令中加入 --include-shared
```

DSN 连接到 `--database` 指定的库。操作会转移该 role 在当前库拥有的所有对象，同时可能转移它拥有的数据库、表空间等共享对象；不能限制在某个 schema 或某类表。预览按目录统计当前库及共享对象，但不是执行后状态的保证；执行前须核对共享对象，确认身份和目标库。若源 role 拥有共享对象，除 `--yes` 外还必须加 `--include-shared`，否则拒绝执行；这个选项不会缩小原生命令的范围。原生命令要求执行者具备源和目标 role 的成员资格（超级用户可执行）。转移不会撤销旧 role 从其他对象获得的授权，也不重写 default privileges。若只转某些表，应改用明确范围的 `ALTER TABLE ... OWNER TO ...`，不要用本命令。
## Schema management

`schema` 子命令管理 PostgreSQL schema/database，不执行任意 SQL、不管理 role，也不替代 dump/restore。`list`、`create` 与 `drop` 的管理 DSN 都可以省略 database path。`drop` 以 `--name` 为删除目标，连接后读取当前会话数据库；若与 `--name` 相同则拒绝。账号还需要相应的建库或删库权限。

```dotenv
DBTALK_DSN_POSTGRES_MANAGEMENT=postgresql+psycopg://operator:password@db.example.com:5432/postgres
```

```bash
uv run dbtalk postgres schema list --dsn-env DBTALK_DSN_POSTGRES_MANAGEMENT
uv run dbtalk postgres schema create --dsn-env DBTALK_DSN_POSTGRES_MANAGEMENT --name app_db
uv run dbtalk postgres schema drop --dsn-env DBTALK_DSN_POSTGRES_MANAGEMENT --name app_db --yes
```

`list` 输出非模板、可连接的数据库。`create` 使用服务端默认创建属性。`drop` 是不可逆操作，必须显式提供 `--yes`，且不能删除当前会话正在连接的数据库。存在其他连接、权限不足或服务器策略限制时，命令会失败；首版不会主动终止其他会话。

## Dump

```bash
uv run dbtalk postgres dump \
  --dsn-env DBTALK_DSN_APP \
  --database app \
  --output ./data/app.dump \
  --compression-level 6 \
  --exclude-table ops_system_logs \
  --exclude-table usage_logs
```

dump 的目标按 `--database > DSN database > 失败` 决定，并始终生成 `pg_dump --format=custom` 的 `.dump` archive。省略 `--output` 时创建 `postgres.output_directory`，并生成 `<database>-<timestamp>.dump`；已有目录同样生成时间戳文件。显式文件路径的父目录必须已经存在。可重复的 `--exclude-table NAME` 映射为 `pg_dump --exclude-table=NAME`，跳过该表对象（无 DDL、无数据）。不预检表是否存在；未知名称由 `pg_dump` 处理。restore 不会建回被排除的表。该选项与 JSONL `--exclude-table` 同名，但走 native dump，不是表数据搬运。

custom archive 具有 PostgreSQL 原生内部压缩，不能等同于 `.sql.gz`。不要给 `.dump` 再套 gzip；`pg_restore` 可以直接读取它。`--compression-level` 仅接受 `0` 到 `9`；省略时保留 native client 的默认 archive 压缩行为。

## Restore

```bash
uv run dbtalk postgres restore \
  --dsn-env DBTALK_DSN_APP \
  --database app \
  --input ./data/app.dump \
  --clean \
  --if-exists \
  --jobs 4
```

目标数据库必须已经存在，并按 `--database > DSN database > 失败` 选择。restore 会先运行 `pg_restore --list` 校验 archive；无效 archive 在连接和写入目标库前失败。

默认恢复跳过 owner 与 ACL，以支持不同账号管理的环境。需要原样恢复时，显式传入 `--preserve-owner` 和/或 `--preserve-privileges`。`--jobs` 只接受正整数，适用于 custom archive。

`--clean` 默认关闭。显式启用时，在 archive 校验成功后，先清空目标库的所有非系统 schema 及其业务对象，包括 archive 中没有的表，再恢复 archive。清理使用 `CASCADE` 处理外键和其他依赖，同时移除用户 extension（保留 `plpgsql`）、事件触发器、用户 cast/access method、publication、subscription、外部数据包装器、自定义过程语言和 large object。清理订阅时先禁用并解除 replication slot 关联，不连接远端删除 slot；远端 slot 的回收由复制管理流程负责。标准 `public` schema 会按 PostgreSQL 18 的初始属性重建，以支持原生 archive 的恢复方式。目标数据库本身、系统 schema、实例级 role 和授权不会删除。

本机路径的 `--clean` 需要同时安装 `psql` 和 `pg_restore`；映射容器和 Docker client 使用对应容器内的 `psql`。执行账号必须具有清理目标库全部用户对象及重建 schema 的权限。`--if-exists` 只能与 `--clean` 同时使用，为清理阶段的 DROP 添加 `IF EXISTS`；清理与导入不再透传原生 `pg_restore --clean`。

PostgreSQL 清理阶段在一个事务内执行，清理失败时回滚并停止导入；整个 restore 并非整体原子操作，导入发生错误时目标数据库可能保留部分恢复状态。`--clean` 不会保留 dump 排除的旧表。执行前必须确认目标 DSN、archive 来源和写入授权。

## Scope

常规 logical dump 包含单库中的 schema、表数据、索引、约束、序列、视图、函数和 extension 声明。它不备份 cluster 全局 role、role password、tablespace 或实例配置；跨环境恢复还要求目标实例具备兼容的 extension 与客户端版本。需要全量 cluster 灾备或时间点恢复时，应使用独立的 PostgreSQL 物理备份方案。

## Role 与授权

`dbtalk postgres role` 管理具备 `LOGIN` 的 PostgreSQL role；`dbtalk postgres grant` 和 `revoke` 与 role 命令同级。所有管理命令使用管理 DSN，且必须在 `--dsn` 和 `--dsn-env` 间二选一。

```dotenv
DBTALK_DSN_POSTGRES_ADMIN=postgresql+psycopg://admin:password@db.example:5432/app
DBTALK_POSTGRES_APP_PASSWORD=change-me
```

```bash
uv run dbtalk postgres role create --dsn-env DBTALK_DSN_POSTGRES_ADMIN \
  --role app_role --password-env DBTALK_POSTGRES_APP_PASSWORD
uv run dbtalk postgres grant --dsn-env DBTALK_DSN_POSTGRES_ADMIN \
  --role app_role --schema app --profile readwrite --yes

uv run dbtalk postgres grant --dsn-env DBTALK_DSN_POSTGRES_ADMIN \
  --role app_role --database app --schema public --profile migrator --yes

uv run dbtalk postgres grant --dsn-env DBTALK_DSN_POSTGRES_ADMIN \
  --role app_role --schema app --privilege USAGE \
  --privilege CREATE --yes
```

新 role 默认是 `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`。密码只能通过 `--password-env` 引用；`DBTALK_*` 名称在进程变量不存在时读取当前目录 `.env`，进程变量存在但为空会失败且不回退。非 `DBTALK_*` 名称不读取 dotenv。密码不会显示在命令输出、日志或错误中。

授权目标可以是 database 或 schema。只传 `--database` 时授权该库；只传 `--schema` 时使用 DSN database 作为连接库；两者同时传入时连接到 `--database`，再对该库中的 `--schema` 授权。未指定 `--database` 和 `--schema` 时使用 DSN database。profile 按 `migrator > readwrite > readonly` 包含：`readonly` 提供基础只读权限；以 schema 为目标时，`readwrite` 再提供现有表的 `SELECT, INSERT, UPDATE, DELETE` 以及 sequence 的 `USAGE, SELECT, UPDATE`；`migrator` 再授予 schema `CREATE`，并在 database 目标上授予 `CREATE`，同时设置 role 的全局 `CREATEDB` 属性以允许建库。`CREATEDB` 不是某个 database/schema 上的普通授权，撤销 `migrator` 会将该 role 设为 `NOCREATEDB`。固定 profile 不添加 `GRANT OPTION` 或角色管理能力。schema profile 不修改 default privileges，因此不会自动覆盖未来创建的表或序列；migrator 必须拥有它需要 `ALTER` 或 `DROP` 的现有对象。

```bash
uv run dbtalk postgres permissions list --dsn-env DBTALK_DSN_POSTGRES_ADMIN
uv run dbtalk postgres permissions show --dsn-env DBTALK_DSN_POSTGRES_ADMIN --role app_role
```

`permissions list` 默认展示当前 DSN 可见的原生权限，可按 role、database、schema 筛选；`show` 查看一个 role，资源筛选可选。输出直接来自 PostgreSQL 原生权限查询。

不支持把 table、sequence、function 作为独立资源参数，也不支持 role membership、`WITH GRANT OPTION` 或完整 SQL 文本；细粒度 privilege 仅作为数据库服务端校验的名称传入。启用、禁用、轮换密码、删除、授权和撤销都要求 `--yes`，并拒绝修改当前管理 role；撤销 profile 可能中断应用访问。
