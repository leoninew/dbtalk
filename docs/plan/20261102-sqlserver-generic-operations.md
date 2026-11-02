# Plan / 计划：SQL Server 通用数据库操作
最后修改时间: 2026-11-02 21:32:53

## Review status / 审查状态

Accepted

## Intent basis / 意图依据

依据 [SQL Server 通用数据库操作意图](../intent/20261102-sqlserver-generic-operations.md) 的 A01-A10。计划和各task方案Accepted；用户最新授权整体Verification，已按dev-check完成只读质量检查与功能、交付复核，结果见[验证报告](../verification/20261102-sqlserver-generic-operations.md)。SQL Server功能通过，A01为部分通过；报告审查状态Draft。

## Technical decisions / 技术设计与决策

### 驱动、DSN 与超时

- 采用 SQLAlchemy 同步方言 `mssql+pymssql`，对外 dialect / transfer source 名称为 `mssql`。增加明确的同步驱动和 Python 包依赖，更新 uv 锁；不支持 SQL Server async client，异步请求应给出明确的受控错误，不能因驱动表缺项产生内部异常。
- 保持现有 DSN 二选一及 `.env` 优先级；`query/exec` 无数据库路径时交给驱动默认数据库，`export/import` 必须显式指定数据库。保留用户变量名；按用户后续授权将当前 `.env` 的 `DBTALK_DSN_TEST1` 指向 DemoBook，不扩展迁移其他条目。
- 分别映射驱动登录和语句超时，处理连接中断和错误脱敏。`pymssql` 的 timeout / login_timeout 为进程级，不能承诺不同并发连接各自独立；同步 CLI 单次调用与 Python API 的限制均需真实验证并公开说明。
- 沿用现有调用方式，不增加自动重试或并发执行框架，尤其不得自动重放写操作。
- 用真实错误样本和无敏感的替身覆盖 URL userinfo、驱动异常中的 password / PWD 等脱敏；不把 SQL、参数和真实连接放到日志或测试输出。
- 审核官方 wheel 平台兼容性、依赖传递与冻结动态导入。2026-11-02 查询到 `pymssql` 2.4.5 的 CPython 3.12 arm64 wheel 支持 macOS 14、x86_64 wheel 支持 macOS 15，且有 Windows/Linux x86_64 wheel；2.4.6 的 macOS wheel 要求 15。具体版本范围以实际跨平台锁定与构建验证为准，不仅以本机成功为准。

### Query：沿用现有契约

按用户最新要求，`query` 与 `exec` 复用现有 SQLAlchemy 调用链：前者获取结果、不提交连接事务，后者在写事务中执行并提交。原始 SQL、参数和返回格式保持不变；SQL Server 不新增只读账号、SQL 文本分析、函数/过程限制或权限沙箱。删除 SQLGlot 依赖及查询拦截模块，清除仅验证该拦截器的测试。

SQLite/MySQL/PostgreSQL 现有数据库只读设置保持不变；SQL Server 没有对应设置，不以拒绝整个 query 或新增解析器来补齐。文档准确说明“不提交”与“数据库强制只读”的差别即可。pymssql 后续结果集消费留在现有 DatabaseSession 的结果处理中，不另建执行器。

### SQL 文件与 dry-run

- 普通 `--sql` 继续保持有参数才使用命名绑定、无参数按字面量执行；`--file` 与 `--param` 互斥。
- 执行文件时先解析已指定的 DSN 方言而不建立连接，再按 SQL Server 的 T-SQL 批次处理 `GO`，保留同批变量与过程定义作用域；执行与 dry-run 必须使用同一批次划分。`GO` 不传给服务器；在字符串、标识符或注释内的文本不能误识别为分隔符。
- 文件 dry-run 复用显式 `--dsn` / `--dsn-env`，只离线解析配置和方言，不建立数据库连接；显式参数无效或对应变量缺失时明确失败。未提供 DSN 时维持现有通用预览，要准确预览 SQL Server 批次必须提供对应 DSN。单 SQL dry-run 沿用原有原样预览，不解析 DSN 或绑定参数。不得从脚本内容或变量名猜测方言，不新增方言选项或重复的 DSN 解析包装。
- SQL Server 文件仅做独立行 GO 的批次划分，统一调用既有 DatabaseClient.execute_script，不另建 engine/cursor/事务入口。不实现 GO n 或 SQLCMD 命令；批次原样交给数据库，不拦截 EXEC、过程调用或事务关键字，脚本显式控制事务时遵循数据库语义。

### JSONL 与 schema

- 将 `mssql` 纳入 CLI、类型和 JSONL header 的受支持来源；既有数据库及格式行为保持不变。新来源对旧版本 reader 的可读性变化需在文档中明确，不引入新旧别名。
- 首版使用连接默认 schema，不自动遍历所有 schema；源或目标涉及跨 schema 外键、无法识别的表或不受支持的列时，预检失败，不省略依赖。表名称在 JSONL 中保持现有标识约定。
- 类型和特殊列遵循下列最低矩阵；T03/T04 共同使用该边界，不另行扩展类型全集。SQL Server 的 `TIMESTAMP` / `ROWVERSION` 不能按日期时间解释，也不能全局改变其他方言的 `TIMESTAMP` 含义。
- 导出仍按外键顺序、选表和临时文件原子替换；导入先完整预检、以完整主键 upsert、按表事务写入及失败回滚。SQL Server 单独实现 upsert，不使用现有非 MySQL `ON CONFLICT` 分支；并发与约束语义通过真实测试确认。
- 导入只保留一套扫描、选表、外键排序、回放、参数转换与分批主流程。各引擎共用单表元数据加载与表头校验；SQL Server 的 catalog 查询、identity 与 upsert 放在已有 `sqlalchemy_transfer.py`，值转换在 `format.py`、结构规则在 `schema.py`、纯元数据在 `models.py`。脚本分批在 `operations.py`，执行统一走 `DatabaseClient`。删除 `mssql_query.py`、`mssql_script.py`、`mssql_transfer.py`、`mssql_import.py`，不新增适配框架。
- 保持既有 `fetchmany(1000)` 分批读取、不使用 `fetchall`、原子输出和结果完整性。`pymssql` 方言不支持 SQLAlchemy 服务端游标，分批接口不证明驱动不缓冲整表；说明该限制即可，不新增硬性 RSS 上限、多规模内存基准或分页读取框架。遇到实际内存问题时记录代表性观测并评估，不自动扩大任务。

#### 最低类型与特殊列矩阵

| SQL Server 类型 / 列 | 首版边界 | 导入导出要求 |
| --- | --- | --- |
| 整数、`decimal/numeric`、`bit` | 支持 | 保持整数、十进制精度和布尔语义；目标范围或精度不兼容在预检拒绝，不经浮点转换十进制 |
| `char/varchar/nchar/nvarchar`（含 max） | 支持 | 保持文本和 Unicode；检查目标长度与编码约束，不静默截断 |
| `binary/varbinary`（含 max） | 支持 | 复用既有二进制 JSONL 编码，逐字节往返 |
| `uniqueidentifier` | 支持 | 规范 UUID 文本与目标 UUID 值之间显式转换，不依赖任意对象序列化 |
| `date`、`smalldatetime`、`datetime`、`datetime2(p)` / `time(p)` 的 p ≤ 6 | 最低日期时间范围 | 通过真实驱动核对现有 JSONL 表示和往返语义；目标精度不足或无法无损表示时预检拒绝 |
| `datetime2(7)`、`time(7)`、`datetimeoffset` | 首版不支持 | 按列元数据在读取 / 写入前拒绝，避免驱动先丢第七位或偏移信息后才检查 |
| identity | 保留显式值 | 导出普通数值；导入显式值时在同一连接按表开启 / 关闭 `IDENTITY_INSERT`，upsert 不更新 identity 列；使用者须已有所需表权限，不授予权限。缺省值沿用既有目标生成规则；失败回滚并清理连接状态 |
| computed、rowversion / timestamp | 不迁移生成值 | 导出所选表包含此类列时预检拒绝；导入制品提供此类目标列时拒绝，不静默丢列；制品未提供且目标能自动生成时可保留目标生成行为 |
| 其他未列类型（如空间、CLR、sql_variant、XML） | 首版不支持 | 预检明确失败；后续需求另行评估 |

支持项仍须遵循既有源 / 目标类型兼容校验；跨引擎不兼容不等于可以隐式转换。identity 权限不足由数据库拒绝并按表回滚，不新增通用权限审计。此矩阵限定 JSONL 传输，不限制用户经 `exec` 提交合法 SQL。

## Implementation steps / 实现步骤

1. 沿用 MySQL / PostgreSQL 的 query 使用契约，T01 先核实 SQL Server 对应实现并验证；默认 schema、复用 DSN 预览及最低类型矩阵按本修订收敛。
2. 按 T01 完成同步方言、连接及 query/exec；通过隔离开发检查并由主会话审查交付。
3. 在 T01 已接受版本上实施 T02 文件批次与 T03 JSONL 导出；共享 CLI 和测试文件在同一工作区串行写入。
4. T04 直接依赖 T01，按既有 JSONL 协议与本计划类型矩阵实施导入，可使用既有 fixture 或 SQLite 制品；共享文件仍串行修改。
5. T05 汇总跨引擎检查、wheel/二进制及文档、通用 skill；完成实现后报告，整体 Verification 需另有用户授权。

## Files to change / 需修改文件

- `pyproject.toml`、`uv.lock`：依赖与受支持 wheel；仅确有冻结构建缺项时最小调整 `Makefile` 或现有构建工作流，不重构发布流水线或扩展平台。
- `src/dbtalk/database/dsn.py`、`models.py`、`connection.py`：方言、受控错误、连接和超时；T01 为首个写入者。
- `src/dbtalk/database/operations.py`、`cli.py`：T02 脚本/dry-run；T03/T04 补充 transfer CLI 时与 T02 串行协调。
- `src/dbtalk/database/format.py`、`schema.py`、`sqlalchemy_transfer.py`、`transfer.py`：T03/T04 按现有职责与共同类型矩阵适配来源和目标；不保留新增的 SQL Server 专用产品模块。
- `tests/test_database_operations.py`、`tests/test_database_transfer*.py` 和隔离 SQL Server 集成测试：按任务分区，公共测试文件串行修改。
- `README.md`、`README.zh-CN.md`、`docs/database.md`、`plugins/dbtalk/skills/dbtalk/SKILL.md`：T05 更新已验证能力和边界；不改用户级安装。
- 同名 intent、plan 和 task 文档随实际过程更新；仅在获授权进入 Verification 后创建同名验证文档。

## Tasks and coordination / 子任务与协调

### Coordination / 协调信息

- 方案修订：7。
- 权威协调根：`D:\SourceCodes\mywork\dbtalk`；权威总计划和 task 均位于该工作区。
- 仲裁与执行会话：仅本主会话；用户已明确禁止继续向其他会话分配任务。两个原执行会话均已确认停止写入，旧分配保留为历史。
- 阶段授权：用户于2026-11-02要求实施，后续指定DemoBook.dbo.Books、更新DSN并实测，又要求仅本会话清理。全局命令DBTALK_DSN_TEST_RDC_APP的select 1缺驱动已修复。最新明确授权dev-check与整体Verification，本轮只读检查产品文件、复用已授权测试资源并更新过程文档；不安装或升级依赖、不自动修复代码、不提交、不发布、不修改既有业务数据。
- 分配前登记执行者、唯一编号、工作基线、可写 task 记录和回报方式。无有效分配前不能修改产品代码；主会话维护总计划，执行者仅更新自身 task 的接收、进展和交付记录。

### Task definitions / 任务定义表

| ID | 任务文档 | 直接前置任务与要求 | 预期交付与验收 | 意图验收归属 |
| --- | --- | --- | --- | --- |
| T01 | [同步连接与执行](../task/20261102-sqlserver-generic-operations/T01-connection-query.md) | 无；方案及实施阶段须获授权 | 驱动、DSN、与现有契约一致的 query/exec、超时及检查；主会话接受可访问代码版本 | A01-A04、A10 |
| T02 | [T-SQL 文件执行](../task/20261102-sqlserver-generic-operations/T02-tsql-script.md) | T01 accepted；使用其可访问的方言、DSN 及执行会话版本 | 可复核的批次与 dry-run 语义，完成聚焦检查 | A05、A10 |
| T03 | [JSONL 导出](../task/20261102-sqlserver-generic-operations/T03-jsonl-export.md) | T01 accepted；使用其可访问的同步驱动及连接版本 | `mssql` 来源、默认 schema、最低类型和外键预检、真实导出制品及分批完整性检查 | A06、A10 |
| T04 | [JSONL 导入](../task/20261102-sqlserver-generic-operations/T04-jsonl-import.md) | T01 accepted；使用其连接版本、既有 JSONL 协议和总计划类型矩阵 | 预检、insert/upsert、特殊列和事务；用既有 fixture 或 SQLite 制品验证 | A07、A10 |
| T05 | [集成与交付](../task/20261102-sqlserver-generic-operations/T05-integration-delivery.md) | T02、T03 与 T04 均 accepted；使用其可访问版本及前置测试证据 | 通用文档、skill、既有打包与代表性跨引擎集成检查，记录剩余风险 | A01、A08-A10 |

### Execution registry / 执行登记表

| ID | 执行状态 | 执行者 | 分配编号 | 工作基线 / 交付位置 | 仲裁结论 / 当前阻塞 |
| --- | --- | --- | --- | --- | --- |
| T01 | accepted | 主会话 | `T01-20261102-04` | 当前工作树修订7 | SQLGlot/查询拦截器已删除；常规query/exec/timeout及回滚实库通过 |
| T02 | accepted | 主会话 | `T02-20261102-02` | 当前工作树修订7及21:23收敛 | 沿用既有DSN解析，文件预览按方言分批，单SQL预览恢复原行为；聚焦及实库检查通过 |
| T03 | accepted | 主会话 | `T03-20261102-02` | 当前工作树修订7 | 接收结构整理并复核；catalog/值转换/校验按职责归位，实库导出与SQLite往返通过 |
| T04 | accepted | 主会话 | `T04-20261102-02` | 当前工作树修订7 | 全引擎共用预检和回放，分批复用；identity、类型、upsert及事务实库通过 |
| T05 | accepted | 主会话 | `T05-20261102-01` | 当前修订7暂存快照、21:23产物与本轮验证报告 | 集成与证据交付已接受；19项实库、二进制1007行gzip及CLI复测通过；A01部分通过：wheel默认配置既有缺失及Linux/macOS未测，不表示发布就绪 |

### DAG and parallelism / DAG 与并行安排

任务定义表是依赖的唯一来源：`T01 -> T02 -> T05`、`T01 -> T03 -> T05`、`T01 -> T04 -> T05`。原并行实施已结束；修订7全部由主会话顺序清理和复核，不再派发。T05最终交付以清理后结果为准，早期通过数量不作为新版本结论。

主会话负责接受每个实际交付、处理共享变更及集成，并对 A01-A10 汇总审查。每次派发前重新核对方案、用户授权、有效编号、依赖版本、检查结果及写入冲突。

### Assignment history / 分配历史

- 2026-11-02 21:23:16：用户指出新增 `resolve_optional_operation_dsn` 没有沿用现有实现。主会话对照HEAD的query/exec及dry-run后，删除包装，将原有DSN解析函数供CLI复用；仅文件预览需要方言时解析，单SQL预览恢复原行为。MySQL/PG文件预览及SQL Server GO预览有聚焦回归；72项聚焦、587项默认回归和19项实库通过，Ruff/format/Mypy通过。wheel/Windows二进制已重新构建，二进制的单SQL预览及DemoBook查询通过，Books仍100行、测试表残留0；本次为T02实现收敛，不扩展任务边界。

- 2026-11-02 21:12:07：主会话复核T05交付并接受。Windows二进制实际query返回DemoBook/100，1007行gzip导出/import upsert通过；wheel在隔离环境加载驱动并查询通过，但原有默认配置未打包需显式设置必填配置，未扩展修复。用户新报全局命令失败确认为editable源码已更新而uv tool环境缺pymssql；按当前安装来源重新执行uv tool install --force --editable，同一目录/同一DSN的select 1返回1。未改目标.env或业务数据。最终实现与证据见T05。

- 2026-11-02 21:01:00：修订7落实用户最新结构要求。T01/T02/T04沿用主会话清理编号；T03由主会话以T03-20261102-02接收并复核当前工作树，旧从会话早已停止，未发送新任务。审查最终职责、公共路径与回归后接受T01-T04：默认测试587 passed、33 skipped、5 subtests passed，SQL Server 2016实库19 passed，Ruff/格式/Mypy/锁检查通过。T05已更新文档，最终二进制传输验证正在完成。

- 2026-11-02 20:40:09：按用户最新要求撤销已结束的外部写入安排，主会话以T01-04、T02-02、T04-02接收清理，T05继续本会话收尾。两个从会话最新报告均已确认停止写入；不发送新任务。方案修订6删除查询文本防护、独立脚本执行器及重复导入编排，测试只保留当前承诺行为。

- 2026-11-02 17:54:59：T01 / `T01-20261102-01` 原拟分配从会话1；本机 `codex-cli` 0.62.0 不支持技能要求的 `--confirm-id`，正式任务未投递、未接收。已发送身份核对消息并收到回复，未发送实施授权或文件修改指令。
- 2026-11-02 17:55:59：撤销尚未投递的 T01 / `T01-20261102-01`，由主会话以 `T01-20261102-02` 接收。用户授权实施；基线为上述 HEAD 与现有工作树。先实施连接层，未确认 query 保障前必须拒绝 SQL Server query；尚不放行依赖任务。
- 2026-11-02 17:58:51：按用户说明，通过 `session list` 和 `message read` 核对两个指定 UUID 与工作目录后，使用本机版本支持的 `message send` 分别请求从会话1调查 query 机制、从会话2审查 JSONL 类型和特殊列风险；均限定只读调查、不修改文件或访问业务数据库。两条消息已排队，等待回复；这不构成产品代码 task 分配，后续实施节点仍须满足依赖后登记。
- 2026-11-02 18:02:26：两条 send 等待超时，不重发。从会话1最终报告已通过 `message read` 核对：当前条件下未找到 SQL Server 等价只读事务，A03 阻塞；本机连接层部分实现和检查记录见 T01。从会话2最后进度仍在整理结果，待最终报告；其工作未涉及共享文件。
- 2026-11-02 18:03:26：通过 `message read` 取得从会话2最终报告。离线调查确认反射丢失 SQL Server 时间精度、现有 UUID 编码和文本 INSERT identity 处理不足、现有类型兼容与跨 schema 外键信息不足；T03/T04 实施时须针对总计划既有类型矩阵解决并以隔离实例验证，调查本身不构成任务交付或实库验证。
- 2026-11-02 19:07:18：用户确认 `DBTALK_DSN_TEST1` / `DemoBook.dbo.Books` 并要求实测本次能力；主会话增加显式启用的实库测试，4 passed、1 failed。现有表仅读取，写入使用连接内临时表；query 尚未满足 A03，T01 不放行。具体命令、结果与未验证项记录于 T01。
- 2026-11-02 19:30:24：T01 修正了全拒绝、T-SQL 词法绕过和 verbose 解析错误泄露；主会话审查实际 diff、实库与聚焦结果后 accepted 连接 / query 交付，打包 / 平台验证归 T05。登记 T02 / `T02-20261102-01` 给从会话1，T03 / `T03-20261102-01` 给从会话2，用户授权沿用；文件所有权如登记表和并行安排。两者仅可在自身 task 写入接收、进展和交付事实，不改权威总计划。
- 2026-11-02 19:35:24：核对 T02/T03 接收记录后置 running。T01 打包开发检查发现 pymssql 动态导入缺失，主会话补 Makefile hidden-import 并将缺驱动异常转为受控错误；wheel 和 Windows 单文件构建通过，二进制实际连接 DemoBook 执行 Books COUNT_BIG 返回 100。其他平台尚未实测。
- 2026-11-02 19:50:45：T03 首次交付经实库审查发现 UUID/BIT 跨引擎类型缺项，返工后支持类型已实际导出并导入 SQLite、再导出；剩余往返在 T04 导入占位拒绝处失败，未冒充整体通过。主会话接受 T03 并登记 T04 / `T04-20261102-01` 给原从会话2，用户实施授权沿用，T01 前置有效、T03 写入已释放。仅可写传输模块、专用测试及自身 task；CLI/公共测试/实库测试仍归主会话。实库写入仅主会话创建并清理唯一前缀测试表，既有 Books 不变。
- 2026-11-02 19:57:53：追加只读审查发现 T01 关键字命名参数与 T02 换行 list 列误拒，另需核实普通 exec 的后续结果错误。主会话停止写连接/query，登记从会话1以 `T01-20261102-03` 限定修正，T02 原编号返工；不得修改 CLI、传输模块或主会话实库测试。T04 沿用已验证的连接/DSN接口继续实施，查询文本检查与结果消费修正不改变其接口；T05 派发前须重新检查 T01/T02 及下游集成有效性。
- 2026-11-02 20:11:55：从会话1提交 T01/T02 限定返工并释放文件，主会话核对实际代码及127项离线检查，实库关键字绑定、query/exec/timeout/GO和迟到错误回滚17项通过，重新接受两项。原exec重复键示例在修正前也会于commit时报错并回滚，修正是及时消费结果及归一错误，不能描述为已复现静默提交。传输接口不变，T03/T04基线继续有效。
- 2026-11-02 20:26:27：T04已按主会话实库发现修复空bytes绑定和DATETIME毫秒刻度；审查类型预检、identity清理、锁及实际结果后接受。最终23项SQL Server开发集成全部通过（SQL Server 2016），包含14项查询/脚本及9项传输/回滚；全字段往返包含UUID、Unicode、精确decimal、空/非空二进制、BIT、date/time/datetime2(6)、datetime与smalldatetime。T03在修改后随集成重新核对有效。登记T05主会话接收，用户要求收住函数层面工作，后续仅文档、既有构建及最终开发检查，不扩展实现。

## Verification plan / 验证计划

- 每个任务先执行对应的聚焦测试和静态检查；仓库根目录使用 `make check`、`make test`，以及 `uv lock --check`。环境无 make 时使用 Makefile 对应的锁定 uv 命令，实际结果不得用计划代替。
- 用用户指定的DemoBook DSN与自建表验证query读取结果且不提交、exec提交、参数/超时/事务回滚以及GO/dry-run；不再测试已删除的应用层拒写，不回显凭据或修改既有业务表。
- 必须验证真实 SQL Server 自身及与 SQLite 的代表性 JSONL 往返，覆盖最低类型矩阵、单/复合主键、外键、identity、预检失败及表级回滚。MySQL/PostgreSQL 运行既有回归，真实跨引擎路径在已有实例可用时补充；不为此新增全六向实库矩阵项目，缺口如实记录。
- 检查分批读取、结果完整性和失败时的原子输出，不新增多规模内存基准门槛或有界内存承诺。
- 复用 `make release`、可用目标平台的 `make binary`，检查冻结程序的实际方言加载与隔离 SQL Server smoke；多平台只报告实际 runner 结果或验证缺口，不以本机结果冒充全部平台。不扩展容器、发布平台或流水线任务，不自动发布。
- 整体验收检查A01-A10、所有有效task交付与版本、实际diff、范围偏差和未完成事项；已按用户最新授权完成并写入同名验证报告，A01保留交付缺口。

## Risks / blockers / assumptions / 风险 / 阻塞项 / 假设

- SQL Server query不提交事务，不表示数据库权限只读；文档不再声称应用层防写，也不新增防护实现。
- `pymssql` 的进程级超时、结果缓冲、TLS、驱动类型与 SQL Server 版本兼容性需隔离实测；无法满足契约时回报而非降低验收。
- SQL Server 的 `DATETIME2(7)`、rowversion、identity 及跨 schema FK 存在真实数据完整性风险；首版明确支持范围，不可隐式跳过或改写。
- 旧版本 dbtalk 的 JSONL reader 不识别 `mssql` 来源；本轮不建立兼容别名，需说明跨版本限制。
- wheel 和 PyInstaller 可用性取决于实际平台及版本；只有 `--help` 成功不足以证明动态方言驱动已打包。

## Rollback / recovery / 回滚 / 恢复

- 产品代码或依赖回退只针对本功能实际变更，保留其他工作树内容；不自动重置工作区，不修改已执行迁移，也不撤销用户指定的测试 DSN。
- 导入按现有契约逐表提交：失败表回滚，已提交前序表不自动回滚；不能把回退代码当作恢复数据。实际迁移仅在用户另行授权和确认后进行。
- 隔离验证只使用本任务明确创建或授权的测试资源；清理前核对身份和范围，绝不清理用户业务环境。

## User review notes / 用户审查记录

- 2026-11-02 21:32：用户授权整体Verification。以HEAD 5708aff64c10d6c0cd3e5f947b5b0ba54a20ab8a加当前30文件暂存变更为基线，检查期间未改产品代码、依赖或暂存区。587项默认测试、19项SQL Server实库及二进制压缩传输均通过；wheel既有默认配置问题再次复现。报告记录完整命令、跳过项、文件对齐、A01部分通过及风险，不将任务accepted状态替代发布验收。

- 2026-11-02：用户要求开始计划，接受意图范围；本计划和 task 均为 Draft，尚未授权实现或整体验证。
- 2026-11-02：按用户“更新和收缩任务”修订为 2：复用 DSN 离线预览、明确最低类型和特殊列规则、移除重试和额外性能 / 发布要求、T04 改为直接依赖 T01，T05 汇合三个操作任务。
- 2026-11-02：用户明确 SQL Server `query` 与 MySQL / PostgreSQL 能力保持一致；修订为 3，撤销只读账号前提及额外产品决策，按同一具备写权限的 DSN 验证 query 拒写与 exec 写入。具体机制待技术核实；计划仍为 Draft，任务均 unassigned，本次只更新过程文档。
- 2026-11-02：用户要求开始实施并授权主会话向两个指定会话分配、审查；接受计划和 task 方案，整体 Verification 未授权。
- 2026-11-02：用户要求解决 query 全拒绝并完成能力；修订为 4，采用 SQL Server 专用单 SELECT 检查，公开应用层检查与数据库只读事务的差异；正常 SELECT / CTE / 参数 / JSON 及常见写入拒绝的聚焦检查 28 passed，待主会话审查后放行下游。
- 2026-11-02：独立审查发现 sqlparse 的反斜杠和 # 词法会放过 T-SQL 写批次；修订为 5，改用 SQLGlot 30.x T-SQL 语法树并加入真实回归。实库与聚焦检查共 36 passed，Ruff / Mypy / 锁检查通过；应用层检查的限制保持公开。
