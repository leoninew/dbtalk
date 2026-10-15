# Plan / 计划：PostgreSQL 与 MySQL 会话查看和终止
最后修改时间: 2026-10-15 11:55:10

## Review status / 审查状态

Accepted

## Intent basis / 意图依据

依据 [Intent](../intent/20261015-session-management.md) 的 A01–A09。计划已 Accepted，实现及必要开发检查完成；用户确认“验证有效”并要求完成整体 Verification，验收结果见同名 [Verification](../verification/20261015-session-management.md)。

用户已明确两类命令均按 `--database > DSN database > 失败` 解析目标，增加可选 `--wait`，不实现 `session cancel`。用户追加要求沿用现有配置系统，运行策略不得在业务代码写死。

研究基线为 `d8e21d9` 及本功能意图。项目当前为 Python / Click / SQLAlchemy，配置由 `settings.py` 通过 Dynaconf 集中加载，检查入口为 Makefile；无新增运行时依赖需求。

## Technical decisions / 技术设计与决策

### 1. 命令与输入契约

在两个方言下各注册 `session` 命令组，首版只提供：

```text
dbtalk postgres session list [--database NAME] (--dsn DSN | --dsn-env NAME)
dbtalk postgres session kill [--database NAME] [--id ID] [--wait SECONDS] --yes (--dsn DSN | --dsn-env NAME)
dbtalk mysql session list [--database NAME] (--dsn DSN | --dsn-env NAME)
dbtalk mysql session kill [--database NAME] [--id ID] [--wait SECONDS] --yes (--dsn DSN | --dsn-env NAME)
```

以上为参数契约示意，括号和竖线不作为 shell 命令执行。Agent 和文档示例沿用 `--dsn-env` 引用进程变量；不新建 dotenv、不修改本地已有密码。

- `--database` 省略时才读取 `ParsedDsn.database`。显式值为空白、含控制字符或无效时直接失败，不回退 DSN；保留有效名字本身，不擅自 trim 或改写大小写。
- 两种来源都缺失、DSN 方言错误、异步 MySQL DSN 或其他输入无效时，在建立 engine 和查询会话之前失败。不使用 Python `assert` 表达用户输入校验。
- `--id` 首版为单个正整数，进一步限制目标数据库内的会话；不提供重复 ID、通配符、实例级 `--all` 或 `--query` 模式。
- `kill` 未提供 `--yes` 返回 Click 参数错误，不做预览、不发送终止请求。用户通过 `list` 查看目标。
- `--wait SECONDS` 只用于 `kill`，接受有限、严格大于零的秒数，可使用小数。省略表示不主动等待；零、负数、NaN 和无穷值均失败。
- 不注册 `session cancel`。子命令和中间 group 不增加 `-v/--verbose`。

### 2. 目标数据库与管理连接

目标解析和连接目标保持一致：使用 `parsed.url.set(database=target_database)` 构造本次操作 URL，保留 host、端口、用户名、密码与连接查询参数，符合现有 dump/restore 的显式 database 覆盖行为。管理 DSN 原指向 `postgres` 或 `mysql` 时，显式 `--database app` 后连接到 `app`，会话查询和终止也只针对 `app`。

因此，执行者需能连接目标数据库，目标必须存在；连接失败按现有错误边界报告，不自动换到维护库重试。无 database 的 DSN 配合显式参数可构造有效连接；两者都没有时不能借用驱动默认数据库继续执行。

两个方言沿用现有 SQLAlchemy engine / connection 生命周期，使用 `AUTOCOMMIT`，避免长期事务造成会话信息快照陈旧；在 `finally` 关闭连接并 dispose engine。不改变既有 `DatabaseClient` 的公开 API，也不修改已有 schema drop 的实现。

数据库名使用绑定参数进行等值筛选，不解释为模式。MySQL 的 `PROCESSLIST_DB` 是当前默认数据库，不能据此保证已找到目标库的全部访问者或锁持有者；这是两条命令共同遵循的范围定义。

### 3. 配置来源与校验

新增一个跨方言共用的 typed `SessionConfig`，挂到 `Settings.session`。调节项由既有 Dynaconf YAML 与 `DBTALK_*` 覆盖体系提供：

| 配置 / 参数 | 来源与用途 |
| --- | --- |
| `session.poll_interval_seconds` | 在 `dbtalk.yaml` 配置初始值 `0.1`；`DBTALK_SESSION__POLL_INTERVAL_SECONDS` 可覆盖。控制等待阶段的轮询间隔。 |
| `database.query_timeout_seconds` | 复用已有配置及对应环境变量；用于列表、身份复核和等待期间的查询预算。 |
| `database.exec_timeout_seconds` | 复用已有配置及对应环境变量；用于发送终止请求的执行 / 驱动响应预算。 |
| `--wait SECONDS` | 本次命令等待退出的总预算；省略不等待，不新增会隐式启用等待的全局默认值。 |

`session.poll_interval_seconds` 必须在集中加载入口配置，解析为有限正数；拒绝缺失、布尔值、非数值、零、负数及非有限值。错误包含完整配置键。业务函数只接收校验后的配置，不读取环境变量，不使用 `getattr` / `dict.get` 添加备用默认值。

初始轮询值只出现在 YAML 和对应测试配置中，不在命令模块、函数默认参数或轮询循环中重复写 `0.1`。沿用项目“配置缺失即启动失败”的约定，同步维护完整测试配置和直接构造 `Settings` 的 fixture，不增加旧配置兼容兜底。

SQLAlchemy 管理连接从现有 query / exec 配置设置相应驱动及服务端超时：PostgreSQL 使用会话 `statement_timeout`，MySQL 查询使用 `max_execution_time`，终止请求的响应使用驱动 read/write timeout。新连接的连接预算复用该操作对应的现有超时配置，不另写固定秒数。等待阶段查询还需受剩余等待预算约束，在服务端支持范围内取较小值。

### 4. 列表数据与会话身份

首版沿用现有表格输出，不额外增加 JSON 格式或 SQL 文本展示。共同展示 `id`、`database`、`user`、`client`、`state`，按 ID 稳定排序，空结果沿用 `(0 rows)` 约定。保留有用的方言字段，避免把不同时间指标强行归为同一含义：

| 引擎 | 查询来源及范围 | 补充显示 / 内部身份 |
| --- | --- | --- |
| PostgreSQL | `pg_catalog.pg_stat_activity`，`datname = :database`、`backend_type = 'client backend'`，排除 `pg_backend_pid()`。 | 显示 `application_name`、`backend_start`、`wait_event_type` / `wait_event`；内部记录 `(pid, backend_start)` 及用户标识。 |
| MySQL | `performance_schema.threads`，`PROCESSLIST_DB = :database`、`TYPE = 'FOREGROUND'`、有效 `PROCESSLIST_ID`，排除 `CONNECTION_ID()` 及明确的系统 / 复制线程。 | 显示 `PROCESSLIST_COMMAND`、`PROCESSLIST_TIME`；内部记录 `(PROCESSLIST_ID, THREAD_ID)`，绝不把 `THREAD_ID` 传给 KILL。 |

MySQL 使用 threads 表是为了同时获取连接 ID 和用于复核的内部线程 ID，避免仅凭可复用的连接 ID 行动。内部 / 复制排除除 foreground 条件外，还按原生线程名称、系统用户和 `Binlog Dump` / `Binlog Dump GTID` / `Daemon` 等系统命令核查，不把复制连接当作常规客户端连接。

列表和批量选择复用同一套客户端筛选条件。对显式 `--id`，同样应用目标数据库及客户端范围约束；没有匹配时返回“目标数据库内未找到该会话或不可见”，不扩大到其他数据库执行。直接指向本次管理连接或可识别的系统线程时明确拒绝。

### 5. 可见性与原生权限

- PostgreSQL 的部分活动字段可能因权限不足为空。完整查看通常需要超级用户或 `pg_read_all_stats`；终止其他 role 的会话还需相应成员资格或 `pg_signal_backend`，超级用户会话由原生权限规则限制。身份关键字段不可见时拒绝盲目终止。
- MySQL 选用 `performance_schema.threads` 后，表的 SELECT 权限决定可见性；与 SHOW PROCESSLIST 不同，该表不会自动按 PROCESS 权限隐藏其他用户。文档准确说明本实现所需的 SELECT 权限，不额外声称 PROCESS 是查询该表的前提。
- MySQL 终止他人连接通常需 `CONNECTION_ADMIN`；目标为 `SYSTEM_USER` 时还受额外权限约束。不依赖旧 SUPER 权限建立兼容路径。
- MySQL 先核对 Performance Schema 已启用且当前连接的线程身份可读。源不可用、查询权限不足或查询失败时明确失败，不能解释为零会话。
- 不自动 GRANT、切换账户或提权；输出只承诺当前查询来源实际可见的客户端集合，不保证实例或数据库已经没有其他连接。

### 6. 终止过程与批量结果

1. 验证输入和确认参数，加载配置并连接已解析目标库，取得本次管理连接 ID。
2. 按数据库范围读取一次目标快照；提供 `--id` 时只选择该会话。批量快照为空时输出无需终止并成功结束；显式 ID 不存在则失败。
3. 按 ID 稳定顺序处理快照中的会话，每次发送前重新读取身份、数据库及客户端类型。已退出记为 `gone`；身份被替换或范围变化记为 `changed`，不终止替换后的会话。
4. PostgreSQL 用绑定的 `pid`、`backend_start`、数据库及用户约束调用 `pg_terminate_backend(pid)`，不使用它的逐目标等待 timeout。返回 false 或匹配消失时复查，区分目标已退出和实际失败。
5. MySQL 复核 `PROCESSLIST_ID / THREAD_ID`、数据库和线程类型后，执行绑定正整数的 `KILL CONNECTION`。遇到原生“未知线程”时复核身份，确认为原目标已不存在才记 `gone`。
6. 原生权限等单目标错误记录后继续处理其余快照目标；连接失效等全局错误停止发送，剩余目标记为 `not_attempted`，不自动重连或重发状态不明的请求。
7. 如果提供 `--wait`，仅对成功提交请求的原身份执行退出确认；最后输出每个目标的状态和汇总，再决定退出码。原始 SQL、完整 DSN 和绑定值不进入结果错误详情。

| 最终状态 | 含义 | 退出码影响 |
| --- | --- | --- |
| `requested` | 原生终止请求成功返回，未启用退出等待。 | 成功，不能宣称已经退出。 |
| `terminated` | 已请求终止，等待查询确认原身份不再存在。 | 成功。 |
| `gone` | 发送前或发送时已确认原身份自然退出，无需继续发送。 | 成功，不计为本命令已终止。 |
| `changed` | 发送前身份、数据库范围或客户端类型改变。 | 整体返回 1。 |
| `failed` | 权限、元数据不可确认、请求失败或请求结果不明。 | 整体返回 1；结果不明不能理解为“未产生影响”。 |
| `timeout` | 已请求，等待预算耗尽仍不能确认退出。 | 整体返回 1。 |
| `not_attempted` | 因连接失效等全局故障尚未发送。 | 整体返回 1。 |

输入 / 确认错误沿用 Click 的参数错误退出码 2；全部正常及零批量匹配返回 0。每行只有一个最终状态，汇总计数不把 requested 与 terminated 重复累计。失败报告保留已发生的成功或不确定结果，不声称整批可回滚。

### 7. 可选等待的统一规则

- 所有目标各发送至多一次。最后一条终止请求返回后，若有待确认身份，使用 `monotonic()` 建立本次命令的单一等待截止时间；`--wait` 不按会话数量相乘。
- 先立即查询全部待确认 ID，随后按配置间隔轮询；sleep 不超过剩余预算。每轮批量读取身份，避免按会话逐个查询和逐个等待。
- 退出判断依据原身份是否仍存在。复查不能继续使用“数据库 / 用户 / 普通客户端”过滤后视图来判断消失：尤其 MySQL 会话切换默认数据库后，仍是同一会话，不能误报退出。
- ID 已复用但身份 token 不同，表示原目标已不存在；等待不会对复用后的连接再次发送终止请求。PostgreSQL 若身份字段因权限变化无法读取，应报告不可确认，不能当作已退出。
- 在截止时间前完成的查询确认退出，才记为 `terminated`；预算耗尽的待确认身份记 `timeout`。查询自身超时或连接失效保留已确认结果，未确认项报告对应失败原因。
- 本参数限制的是退出确认阶段，不是整条命令从建连开始的硬截止时间。连接、发送请求、数据库响应及网络错误还受各自配置控制；已有阻塞 I/O 受驱动精度和服务端支持限制，不承诺绝对墙钟硬中断。
- 连接池后来建立的新会话不加入本次集合；中断等待不撤销已发送的终止请求。

### 8. 代码组织与日志

在 `src/dbtalk/database/sessions.py` 仅共享目标解析、结果结构和简单的等待预算 / 表格辅助；原生 SQL、会话身份及执行留在各方言的 `session.py`。不引入后端注册表、通用管理接口或策略框架。已有 `QueryResult` / `render_query(..., "table")` 可用于表格输出。

根命令和 `DbtalkGroup` 保持统一生命周期日志。日志白名单增加 `session_id`、`wait_seconds`；方言操作补充解析后的目标数据库、选中数量和目标结果，保证 DSN 回退的数据库也有记录。不重复配置 handler 或生成第二组生命周期日志，不记录 SQL、密码、原始异常全文或完整 DSN。失败仍通过现有 `DatabaseOperationError`、Click 和脱敏错误链呈现。

## Implementation steps / 实现步骤

1. **集中配置与公共契约**：加入 YAML 调节项、typed SessionConfig、集中校验和覆盖测试；完成目标解析及结果类型。完成条件为无代码备用默认值、四种数据库来源组合及有效性检查均有证据。
2. **PostgreSQL 适配**：完成列表、范围内选中、身份复核、原生终止和等待。证明 AUTOCOMMIT、自身 / 系统排除与数据库约束有效。
3. **MySQL 适配**：完成 threads 查询、两种 ID 分工、系统 / 复制排除、KILL 和等待。证明默认数据库切换不会伪造退出，权限 / 源不可用能明确失败。
4. **CLI、结果与日志集成**：注册两个组；贯通 --yes、--id、--wait；输出各目标状态和正确退出码；保留根 verbose 行为。
5. **文档与开发检查**：同步手册、README、日志说明和仓库内两份方言 skill；运行聚焦检查、完整质量入口及已准备好的隔离实验，记录实际结果、未运行项和偏差。
6. **实施交付**：在本计划登记实际变更、检查及限制，交付实现结果。整体 Verification 文档按后续阶段授权创建，不以开发检查自动替代整体验收。

## Files to change / 需修改文件

| 范围 | 预期文件 |
| --- | --- |
| 配置 | `dbtalk.yaml`、`src/dbtalk/settings.py`。 |
| 公共辅助 | 新增 `src/dbtalk/database/sessions.py`；不改 DatabaseClient 公开接口。 |
| PostgreSQL | 新增 `src/dbtalk/postgres/session.py`；修改 `src/dbtalk/postgres/cli.py` 注册。 |
| MySQL | 新增 `src/dbtalk/mysql/session.py`；修改 `src/dbtalk/mysql/cli.py` 注册。 |
| 日志 | `src/dbtalk/cli_runtime.py` 的安全参数白名单。 |
| 聚焦测试 | 新增 `tests/test_sessions.py`、`tests/test_postgres_sessions.py`、`tests/test_mysql_sessions.py`、`tests/test_session_integration.py`；扩展 `tests/test_cli_logging.py`、`tests/test_settings.py`。 |
| 配置 fixture | `tests/test_backup_db.py`、`tests/test_unit_boundaries.py` 中直接构造 Settings 的位置，以及实际受影响的完整配置样例。 |
| 使用说明 | `README.md`、`README.zh-CN.md`、`docs/postgres.md`、`docs/mysql.md`、`docs/logging.md`。 |
| 仓库内 skill | `plugins/dbtalk/skills/dbtalk-postgres/SKILL.md`、`plugins/dbtalk/skills/dbtalk-mysql/SKILL.md`。 |
| 过程记录 | 本功能 Intent、Plan；后续授权 Verification 时创建同名报告。 |

实际实现与检查记录见末尾 Implementation progress。原计划未包含外部 Obsidian 文档，其后续更新已由用户追加授权，见 Verification progress；已有凭据和业务数据库不在修改范围。

## Tasks and coordination / 子任务与协调

不适用。保持单任务，由主会话顺序实施、集成与审查，不创建 task 或分配登记，不启动子 agent。上述实施步骤共用本计划的契约与总体验收。

## Verification plan / 验证计划

### 开发检查与验收映射

| 意图验收 | 最小有效证据 |
| --- | --- |
| A01–A02 | 两种引擎的数据库覆盖 / 回退 / 缺失、无效显式值、错误 DSN 和按 ID 仍需 database；失败前不创建 engine。 |
| A03 | 活跃和空闲连接、排序、空列表、基本字段、权限可见性，以及系统和自身的列表排除。 |
| A04 | 指定 ID 位于其他库、身份替换、MySQL 默认库变化、系统 / 复制与自身排除；检查未向错误目标发送原生终止。 |
| A05 | 缺少 --yes 无请求；单目标权限失败及批量部分成功；连接失效后不自动重试、剩余目标未发送；结果汇总及退出码。 |
| A06 | 未指定时不轮询；正小数输入；立即退出、自然消失、共享等待预算、轮询间隔来自配置、超时、复用 ID 与字段不可读；用受控时钟验证，不靠长时间 sleep 或概率性真库超时。 |
| A07 | 默认日志静默、根 -v 启用、子命令拒绝 -v；DSN 回退目标、会话 ID、等待参数和结果可见，密码 / DSN / SQL 不泄露，stdout 可正常读取。 |
| A08 | 四个 CLI help 与实际注册一致，不存在 cancel；手册和 skill 准确说明配置、范围、权限、等待与部分失败。 |
| A09 | YAML 值及环境变量覆盖实际改变 typed 配置和轮询行为；缺失 / 非法数值集中失败；已有 fixture 和完整配置更新。 |

优先使用 Makefile 的 `make check`、`make test`。当前 PowerShell 沿用等价命令，均在仓库根目录执行：

```powershell
uv run --locked --no-sync python -m pytest tests/test_sessions.py tests/test_postgres_sessions.py tests/test_mysql_sessions.py tests/test_settings.py tests/test_cli_logging.py
uv run --locked --no-sync python -m ruff format --check src tests scripts
uv run --locked --no-sync python -m ruff check src tests scripts
uv run --locked --no-sync python -m mypy src tests scripts
uv run --locked --no-sync python -m pytest
git diff --check
```

测试聚焦承诺行为与真实风险，避免仅重复 SQL 字符串或穷举无关负向输入。计划阶段未运行实现检查；实施阶段的实际命令与结果单独登记于末尾，不将开发检查自动视作整体 Verification。

### 隔离数据库实验

新增 opt-in 集成入口，分别通过 `DBTALK_SESSION_TEST_POSTGRES_DSN`、`DBTALK_SESSION_TEST_MYSQL_DSN` 引用独立测试端点；使用进程变量，不创建 dotenv。无对应端点时显式 skip，不能将 skip 记为真实退出验证通过。沿用 PostgreSQL 18+ 与仓库当前 MySQL 8.0.39 基线。

实施时只使用明确准备的测试实例，建立唯一命名的目标库、另一个哨兵库和临时测试账户。至少验证：

1. 活跃与空闲会话均可列出；--database 覆盖 DSN，或省略时按 DSN database 工作。
2. 按 ID 及数据库批量终止后，--wait 确认原会话退出；其他库哨兵连接和本次管理连接仍可用。
3. PostgreSQL pg_signal_backend 与 MySQL CONNECTION_ADMIN 的允许 / 拒绝场景符合原生权限，拒绝不会被报告为成功。
4. MySQL 选择后切换默认库时停止对范围已改变的目标发送请求；等待期间切库仍不视作退出。相关竞争由同步控制验证，无法稳定制造的边界使用受控单元测试补充，不增加偶发失败实验。
5. 客户端实际收到断开，代表性的未提交事务遵循数据库原生终止行为。清理仅涉及本实验创建的对象和连接。

不在 Plan 阶段探测或创建测试实例，不自动使用用户已有业务 DSN 作为集成端点。已有独立端点可用时在实施授权内运行必要开发实验；若尚未准备，实施交付明确列为未运行项。

## Risks / blockers / assumptions / 风险 / 阻塞项 / 假设

- 暂无需要用户决定的实施阻塞项。方案已 Accepted；配置初始值、时间单位和错误语义已按本文实现。
- 新 session 配置键为必需项，已有完整自定义配置需补充该节；同步样例，不以代码默认值掩盖缺失。
- 目标连接方式要求执行者具有目标库 CONNECT / 访问能力；不因只读元数据可跨库查询而增加维护库回退。
- 会话终止会影响业务和事务，批量操作无法整体撤销；成功或网络响应丢失后的实际效果不可通过自动重试推断。
- PostgreSQL PID 与时间、MySQL 连接 ID 与线程 ID 能降低误终止风险，但原生接口不能把所有“复核 + 发送”完全原子化，尤其 MySQL KILL 只接受连接 ID；不承诺完全消除竞争。
- MySQL threads 查询有其 SELECT 可见性规则；受限环境可能禁止访问 Performance Schema，此时按明确不支持 / 权限失败报告，不加替代查询路径。
- --wait 限制退出确认预算，不能保证连接池不重连、数据库锁全释放或网络故障时严格按秒硬中断。
- 开发检查及隔离数据库实测已完成，证据见末尾；常规测试的环境跳过项与单独的真实实验分别登记。整体 Verification 已获用户授权，结果见同名报告。

## Rollback / recovery / 回滚 / 恢复

如需撤回功能，仅回退本次命令、配置及其配套测试 / 文档的限定 diff，不重置其他用户改动、不修改已有凭据。

代码或配置回退不能恢复已终止的会话、撤销已发生的事务处理，也不能修复数据库原生非事务操作的部分效果。连接池重建和业务恢复由实际服务按自身机制处理，CLI 不自动重启应用或重复终止。

## References / 参考依据

- [配置加载](../../src/dbtalk/settings.py)、[默认配置](../../dbtalk.yaml)、[Makefile](../../Makefile)。
- [现有 CLI 日志边界](../../src/dbtalk/cli_runtime.py)、[表格渲染](../../src/dbtalk/database/operations.py)。
- [PostgreSQL 18 活动信息与可见性](https://www.postgresql.org/docs/18/monitoring-stats.html)。
- [PostgreSQL 18 会话终止与 timeout 语义](https://www.postgresql.org/docs/18/functions-admin.html)。
- [MySQL 8.0 threads 表与权限](https://docs.oracle.com/cd/E17952_01/mysql-8.0-en/performance-schema-threads-table.html)。
- [MySQL 8.0 KILL CONNECTION 与异步退出](https://docs.oracle.com/cd/E17952_01/mysql-8.0-en/kill.html)。

## User review notes / 用户审查记录

- 2026-10-15：用户要求“开始计划”，接受前置意图，授权整理 Plan；尚未授权进入本功能 Implementation。
- 2026-10-15：用户要求“我们有配置系统，不要写死在代码里”；轮询间隔纳入 typed / YAML / 环境变量配置，查询和执行超时复用已有配置。
- 2026-10-15：用户要求“开始实现”，接受本计划并授权实施及必要开发检查；保持单任务，不启动子 agent。
- 2026-10-15：用户确认“验证有效”并要求完成验证文档，接受实现交付并进入整体 Verification；用户追加提供 cygwin.bat 及 make check/test 的 uv trampoline 失败输出，授权范围内修复质量入口。

## Implementation progress / 实施进展

- 2026-10-15 10:43:24：开始实施，基线 d8e21d9 与本功能已接受的意图和计划。先完成配置、目标解析和结果契约，再依次完成两个方言、CLI 与验证；尚无实现交付结论。

### 实际交付

- PostgreSQL / MySQL 已注册 `session list`、`session kill`。目标数据库优先级、连接目标覆盖、范围内单 ID、客户端及自身排除、`--yes`、可选共享预算 `--wait` 均按计划实现，未提供 cancel 或子命令 verbose。
- PostgreSQL 使用 PID / backend_start 身份复核及有约束的 pg_terminate_backend；MySQL 使用 PROCESSLIST_ID / THREAD_ID 分工及 KILL CONNECTION，发送前复核范围，退出确认不依赖默认数据库过滤。
- 逐目标报告七类互斥状态、部分成功及失败退出码；单目标权限失败继续处理，连接失效后停止剩余请求且不重发。等待使用单一 monotonic 截止时间、批量身份查询及配置轮询间隔。
- YAML / typed Settings 新增必需 `session.poll_interval_seconds`，集中校验并支持 `DBTALK_SESSION__POLL_INTERVAL_SECONDS`；查询和执行预算复用既有 database 配置。完整配置样例及直接构造 Settings 的 fixture 已同步。
- 根命令日志白名单加入 database、session_id、wait_seconds；操作函数记录实际数据库、数量及结果，默认静默行为保留。已同步双语 README、两份引擎手册、日志说明和仓库内两份方言 skill。新增命令示例直接声明变量，未新建 dotenv、修改已有密码或外部 Obsidian 文件。
- 实施交付时为相对 d8e21d9 的工作区 diff，新增文件当时保持未跟踪状态，agent 未暂存、提交、安装、同步本地插件或发布。进入 Verification 时已存在用户暂存的功能变更，本轮保留其暂存状态。

### 实际文件与范围差异

配置、CLI 注册、公共模块、两个方言模块、配置 fixture、README、引擎手册、日志手册及仓库内 skill 均落在预期范围。新增四份聚焦测试文件为 `test_sessions.py`、`test_postgres_sessions.py`、`test_mysql_sessions.py`、`test_session_integration.py`。

两项局部组织调整：公共 sessions 模块同时容纳窄范围的连接生命周期、超时配置和安全错误编码，避免两个方言重复实现；原生 SQL 与身份规则仍在各方言中。计划对 `tests/test_cli_logging.py` 的新增覆盖集中放在 `tests/test_sessions.py`，该既有文件未修改，原有日志测试仍全部通过。没有引入通用后端框架、兼容路径或运行时依赖。

### 开发检查结果

| 命令 / 范围 | 实际结果 |
| --- | --- |
| `uv run --locked --no-sync python -m ruff format --check src tests scripts` | 通过，74 个文件符合格式。 |
| `uv run --locked --no-sync python -m ruff check src tests scripts` | 通过。 |
| `uv run --locked --no-sync python -m mypy src tests scripts` | 通过，检查 75 个源文件。 |
| `uv run --locked --no-sync python -m pytest -q` | 460 passed、16 skipped，另有 5 subtests passed。 |
| 四个 session 子命令 `--help` | 均通过，kill 显示 `--wait SECONDS`，不提供子命令 verbose 或 cancel。 |
| `git diff --check` | 通过。 |

常规 pytest 的 16 个 skip 包括既有批量传输 / owner 集成的 10 项端点条件，以及新增 session 集成的 6 项端点条件；对应测试端点未保留在常驻进程环境。新增 session 集成另在临时端点单独实际执行，见下文，不将常规 skip 视为通过。

### 原生数据库实验

只使用本机已存在的 `postgres:18-alpine`、`mysql:8.0.39` 镜像创建本任务唯一命名的临时容器，映射随机 localhost 端口，随机凭据仅传给测试进程。数据目录使用 tmpfs；PostgreSQL 的第一次磁盘初始化未在启动预算内完成，改用临时内存目录后两个实例均正常就绪。未连接既有业务端点、未拉取镜像、未操作用户开发服务。

在同时提供两个 `DBTALK_SESSION_TEST_*_DSN` 的进程内执行：

```powershell
python -m pytest tests/test_session_integration.py -q --tb=short
```

结果为 **5 passed、1 skipped，2.95 秒**；skip 是 MySQL 切库实验在 PostgreSQL 参数下不适用。两个引擎分别证明：活跃 / 空闲会话及稳定列表、覆盖 DSN 数据库、跨库 ID 拒绝、单 ID 与批量退出确认、其他库哨兵仍可查询、真实客户端收到断连、未提交插入回滚、空列表与空批量，以及受限账号部分失败、授予原生终止权限后成功。MySQL 另以同步控制证明发送前切换默认数据库会报告 changed 并保留连接。所有测试对象和本任务容器已清理。

等待超时、单一总预算、不可见身份、ID 复用、连接失效不重试和 MySQL 等待不按默认库判断退出由受控单元测试证明，未使用概率性真实超时实验。未测试实际系统 / 复制进程终止，也未声称枚举全部访问者、无竞争窗口或生产网络环境的硬截止保证。

### 实施结论与后续范围

本次授权的实现及必要开发检查完成，实现交付已由用户确认有效。仍适用上文列明的原生权限、MySQL 默认库范围、KILL 竞争窗口、连接池重建和等待 I/O 精度限制。整体 Verification 已按后续用户授权进入，结论与本轮检查见同名报告。

## Verification progress / 验证进展

- 用户补充要求更新的外部 PostgreSQL / MySQL 常用操作文档已完成，新增连接查看、单 ID / 批量终止、等待和配置说明；变量直接声明在文件中，原有声明的 SHA-256 校验值一致，包括 DSN 和密码。六段命令已按 Click 参数解析核对，未执行数据库操作。外部文件不进入仓库 diff。
- 用户随后确认功能有效并要求完成验证记录。已按 Intent A01–A09 核对代码、测试、仓库文档和后续授权的外部说明，保持单任务，不新增 task。
- Windows uv trampoline 错误已定位：mypy.exe / pytest.exe 内嵌的解释器仍在原 pomelo-dbtalk 目录，该路径已不存在。先前临时采用 python -m 绕过并通过检查（460 passed、16 skipped，55.76 秒）；用户要求修复环境并恢复 Makefile，最终不保留该绕过变更。
- 已恢复原始 Makefile，以原 Python 3.12.10 执行 `uv sync --locked --all-groups --python <原基础解释器> --no-python-downloads --offline` 重建 .venv。uv.lock 的 SHA-256 及全部 41 个包的版本与重建前一致，新启动器引用当前 dbtalk 目录；mypy、pytest 和 dbtalk 的直接版本命令均成功。最终原始 make check/test 的结果以 Verification 为准。
- 先前单独运行的原生 session 实验为 5 passed、1 个不适用 skip；环境修复未重复启动数据库实例，也未改变会话功能实现。整体验证结论 Accepted，详见同名报告。
