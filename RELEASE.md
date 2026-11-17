# 变更记录

按 Git 提交日期整理。每条聚焦业务能力与行为变化，具体提交以短哈希附后；内部重构、测试调整与发布流水线不单列。当前版本 `0.42.0`，命令参数见 [数据库手册](docs/database.md)、[MySQL](docs/mysql.md)、[PostgreSQL](docs/postgres.md) 和[批量备份](docs/backup.md)。

英文版：[RELEASE.en.md](RELEASE.en.md)。

## 2026-11-15

- 操作日志改为默认开启：CLI 生命周期与脱敏后的异常细节写入 stderr，stdout 保留查询结果、SQL 预览和操作输出，便于重定向与 JSON 解析。
- 根命令 `-v` 将日志级别提升到 `DEBUG`；日志级别和格式仍可由配置与 `DBTALK_LOGGING__*` 调整。

对应提交：`a1d1ada`。

## 2026-11-02

- 新增 SQL Server 支持：`query`/`exec` 复用既有执行流程，SQL 文件按独立行 `GO` 分批，无需系统 ODBC。
- 可在 SQLite、MySQL、PostgreSQL、SQL Server 之间导出导入 JSONL；SQL Server 支持显式 identity 值、按完整主键 upsert，以及导入前的类型与精度预检。
- 明确限制：传输只处理连接默认 schema，跨 schema 外键明确失败；`datetime2(7)`、`time(7)`、`datetimeoffset` 等精度或偏移敏感类型不参与传输，`query`/`exec` 不受这些类型限制。

对应提交：`2341deb`。

## 2026-10-15

- 新增 MySQL、PostgreSQL 的 `session list/kill`：按数据库列出客户端会话并可终止，支持可选 `--wait` 等待退出，逐目标返回 `requested`、`terminated`、`gone`、`changed`、`failed`、`timeout`、`not_attempted`。
- `schema drop --if-exists` 支持 MySQL 与 PostgreSQL；PostgreSQL 另有 `--force`，以 `WITH (FORCE)` 尝试终止目标库连接后删除。
- 新增 CLI 生命周期日志和脱敏错误细节，`--verbose` 收敛为根命令选项（`dbtalk -v <命令>`）。
- Agent skill 优先复用已有的 `DBTALK_DSN_*` 与密码变量，再补充缺失配置。

对应提交：`ceae403`、`43e1259`、`d8e21d9`、`3ba5b9b`、`fd6f8c3`。

## 2026-10-13

- 新增 `postgres owner transfer`：按目标数据库与可重复的 `--schema` 逐对象转移业务对象所有权，排除系统 schema、内置对象和 extension 成员；默认只展示计划，`--yes` 才在单个事务内执行并在失败时整体回滚。

对应提交：`b9d6d68`。

## 2026-10-12

- 新增备份 `clean`：按数据库目录与 `manifests` 目录独立保留最近 7 天或最近 7 个文件，默认只预览，`--no-dry-run` 才删除。
- `test` 与 `sync` 支持未配置数据库清单的连接，按连接引擎发现非系统库并逐项记录新增与移除。
- 连接测试和同步发现统一受连接超时与查询超时中较小值限制，默认 5 秒。

对应提交：`7074f41`、`22b57ec`、`c570314`。

## 2026-10-04

- JSONL 导入改为有界批处理：保持输入顺序和表级事务，PostgreSQL、SQLite 按完整主键 upsert，导入前预检目标类型转换。
- 导出改为游标流式读取；导入报告待处理、已提交和回滚进度，失败只回滚当前表，已提交的前序表保留。

对应提交：`5a9cc18`。

## 2026-10-02

- 备份连接可整体禁用，`backup`、`test` 和 `sync` 均跳过该连接；完成日志把跳过的数据库计入目标总数。

对应提交：`104c462`。

## 2026-10-01

- 还原前可完整清理目标库：PostgreSQL 改为清理目标对象后导入，MySQL 新增 `restore --clean`，在关闭外键检查的清理会话中删除全部表、视图、存储程序和事件。MySQL DDL 无法整体回滚，失败时目标库可能处于部分状态。
- 备份产物按 `<连接名>_<数据库名>` 分目录，同一批次的时间戳文件集中存放；续跑使用 `--resume <timestamp>`，复用非空备份并更新同一份清单。

对应提交：`048f9e8`、`a0adc0b`。

## 2026-09-28

- `sync --continue-on-error`：单个连接失败后继续处理其余连接，保留成功更新并以失败状态结束。

对应提交：`26f9a20`。

## 2026-09-23

- 新增 `postgres owner reassign`：预览后执行原生 `REASSIGN OWNED`；源角色持有数据库、表空间等共享对象时，除 `--yes` 外还需 `--include-shared` 才执行。

对应提交：`7048d58`。

## 2026-09-22

- 未提供绑定参数时按字面执行 SQL，提供参数时保持命名绑定，避免字面量中的 `%` 等字符被误解析。

对应提交：`45ef075`。

## 2026-09-21

- 数据库管理操作失败时返回脱敏后的驱动原因；缺少 PostgreSQL 驱动时给出明确提示，不再误报连接失败。
- 插件元数据注册到受支持的宿主，便于本地发现。

对应提交：`daf0c39`、`a0cc5ef`。

## 2026-09-17

- 新增 `exec --file`，执行 UTF-8 SQL 脚本；脚本级事务控制由 CLI 接管，单个脚本在一个写事务内完成。
- 脚本按 `sqlparse` 切分，存储过程体与美元引用函数保持完整，注释和事务控制语句跳过。
- 新增 `exec --dry-run`，只打印待执行语句，不连接数据库也不写入。

对应提交：`9b559bb`、`cdca45d`、`12d9d9d`。

## 2026-09-14

- 新增备份 `sync`：把配置清单与连接可见的非系统库对齐，新增库默认启用，已有库保留设置，已不存在的库移除。

对应提交：`397a2c5`。

## 2026-09-11

- MySQL `user drop`、`grant`、`revoke` 支持 `--all-hosts`，按用户名作用于每个现有 host。
- PostgreSQL `grant` 可同时指定 `--database` 与 `--schema`；DSN 不含数据库时拒绝纯 schema 授权，避免落到驱动默认库。
- `schema drop` 仅在当前会话库与 `--name` 相同时拒绝，便于无库 DSN 使用；标量 `DBTALK_DATABASE` 不再被当作完整数据库映射，加载时直接报错。

对应提交：`9c1736e`、`6420196`、`a56a412`。

## 2026-09-09

- `dump --exclude-table` 可重复指定，按表对象整体跳过；备份配置中的 `exclude_tables` 会透传到 dump。
- `DBTALK_*` 密码在进程变量缺失时读取当前目录 `.env`，进程变量存在但为空仍失败；MySQL 密码轮换支持用户名下的全部现有 host。
- 密码生成脚本增加包含符号的 `-s` 选项；脚本路径统一为 kebab-case，dump 输出改为流式并隐藏 DSN 凭据。

对应提交：`57114e1`、`c02adec`、`d40f7ed`。

## 2026-09-08

- `exec` 固定使用写入会话，移除 `--write`；叶子命令的 `-v` 追加脱敏后的异常类型与消息，默认错误仍只显示稳定的阶段信息。
- 包以 `pomelo-dbtalk` 发布到 PyPI，同时提供 wheel 与 Linux、macOS、Windows 独立可执行文件。

对应提交：`e1d398a`、`d7ed77e`、`b29b4e2`。

## 2026-09-03

- Agent 统一使用 `--dsn-env` 与 `DBTALK_DSN_*`：进程变量缺失时读取当前目录 `.env`，并关闭全局 dotenv 配置，避免隐式加载其他目录的连接。

对应提交：`11cd15c`。

## 2026-09-02

- 允许不带 database 的 MySQL、PostgreSQL DSN 执行实例级操作；JSONL 传输与原生备份还原仍要求明确目标库。
- MySQL 与 PostgreSQL 共用 `output_directory` 与 `client_image` 配置；查询超时与执行超时分开配置；MySQL 零日期处理归入 MySQL 配置。
- 备份连接校验使用无库 DSN 且每个连接只验证一次，按配置拉取 Docker client 并输出脱敏诊断。

对应提交：`032bc5d`、`c0788f7`、`98c182a`。

## 2026-09-01

- 权限 profile 收敛为 `readonly`、`readwrite`、`migrator`；`migrator` 额外具备目标库 DDL 与建库权限。
- 新增 MySQL、PostgreSQL `permissions list/show` 查看原生权限；数据库生命周期命令统一为 `schema`。
- 备份支持续跑：单个目标失败不影响其余目标，清单记录成功、复用和失败，`--resume` 复用已有批次。
- 版本号由 Git 历史计算，并原子同步到包元数据与锁文件。

对应提交：`a39d5f5`、`a4bf3da`、`7a9c89a`、`7e13ad8`。

## 2026-08-31

- 新增备份 `backup` 与 `test` 子命令，按批次时间戳输出产物并记录 `DBTALK_*` 连接检查。
- 备份清单按连接分组并保留嵌套数据库；连接与数据库可分别禁用，校验和备份都会跳过禁用项。
- 备份连接改为分层 YAML 配置，保留环境变量覆盖，并忽略本地含凭据的配置文件。
- PostgreSQL dump/restore 复用映射到本地端口的容器，经容器 Unix socket 执行，保留本机客户端与 Docker client 回退。

对应提交：`99ca728`、`ab41dc8`、`d14e617`、`dd08e4a`、`7dd8128`。

## 2026-08-30

- 新增基于 DSN 的数据库批量备份与安全密码生成。
- JSONL 导入接受可移植的布尔值表示，避免不同引擎的 `true`/`false` 差异导致导入失败。

对应提交：`526a131`。

## 2026-08-28

- MySQL dump/restore 加固：还原连接与目标库分离，只接受 `USE` 形式的 dump，拒绝建库删库 DDL，dump 文件成功且非空后才原子发布。
- restore 通过映射容器执行预检与导入，保留 Docker client 回退；日志包含阶段、耗时和字节数。
- MySQL 支持 `user@%` 等账号的密码轮换，拒绝部分通配 host；确认后可轮换当前管理账号。

对应提交：`68c5f13`、`36bf3b2`、`1f18547`。

## 2026-08-26

- 新增本地安装入口统一管理插件生命周期；提供 PyInstaller 单文件可执行文件，并允许本地配置覆盖内置默认值。

对应提交：`e8c518c`。

## 2026-08-25

- 项目自动化统一为基于 `uv` 的 Makefile 目标，覆盖依赖、安装、检查、测试与构建；本地覆盖率保持可选，CI 保留覆盖率门槛。

对应提交：`74d83c3`。

## 2026-08-24

- MySQL dump 优先复用唯一匹配的本地容器，保留临时 Docker client 回退，并在完成后清理临时容器。
- `DBTALK_DSN_*` 在进程变量缺失时从当前目录 `.env` 读取，进程变量始终优先。

对应提交：`437fc09`、`efc027c`。

## 2026-08-23

- 新增 MySQL、PostgreSQL `schema list/create/drop`，删除需显式确认；PostgreSQL 保护当前管理库不被删除。
- 新增 MySQL user 与 PostgreSQL role 生命周期，配套 `readonly`、`readwrite` 授权 profile，保护密码输入、破坏性操作和当前管理身份。
- 新增 PostgreSQL 逻辑备份：custom archive dump/restore，优先本机客户端，可配置 Docker 回退，支持归档校验、清理开关、并行还原与可移植 owner 默认值。
- Agent 插件统一为单一原生包，同步 Claude、Codex 与 Grok。

对应提交：`2e74768`、`25ba731`、`e3cdd2b`、`04841f9`。

## 2026-08-22

- `query`/`exec` 增加可配置语句超时；查询默认使用只读会话，写入需显式开启。
- 包名、模块与命令统一为 `dbtalk`。

对应提交：`52d7b94`、`6564e67`。

## 2026-08-20

- 首个版本提供 MySQL 原生 dump/restore，支持 gzip 与 Docker client 回退，以及 SQLite、MySQL 的 JSONL 导出导入。
- 以规范 DSN 统一 SQLite、MySQL、PostgreSQL 的 `query`、`exec`、`export`、`import`、`dump`、`restore`。

对应提交：`26665fa`、`eb95af0`。
