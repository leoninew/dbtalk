# Task / 子任务 T03：SQL Server JSONL 导出
最后修改时间: 2026-11-02 21:01:00

## Review status / 审查状态

Accepted

## Parent and authority / 父任务与权威记录

- 总意图：[意图](../../intent/20261102-sqlserver-generic-operations.md)。
- 权威总计划：[计划](../../plan/20261102-sqlserver-generic-operations.md)；任务 ID：T03；方案修订：7。
- 总计划是依赖、分配和仲裁的唯一权威记录。

## Handoff basis / 交接依据

- 有效分配 `T03-20261102-02`，执行者主会话；总计划修订7，基线为当前工作树和已清理T01，用户要求本会话整理，不再派发。
- 本文由主会话直接记录接收、进展及交付。

## Goal and acceptance / 目标与验收

交付 SQL Server 默认 schema 的 JSONL 导出及来源元数据适配，覆盖 A06、A10；支持既有表选择、外键顺序和原子输出，为 T05 往返集成提供真实制品。与 T04 共用总计划类型矩阵，不作为其开工前置。

## Inputs and eligibility / 输入与可开始条件

T01 驱动和连接交付须经主会话 accepted 且版本可访问；默认 schema 范围、计划、任务方案和实施授权均已确认。

## Scope and files / 范围与文件

- `database/cli.py`、`models.py`、`format.py`、`schema.py`、`sqlalchemy_transfer.py` 及测试；元数据、转换、校验按现有职责归位，删除专用导出模块。
- `cli.py`、格式、元数据及测试文件与 T02/T04 串行修改；本任务按既有 JSONL 协议和总计划矩阵适配导出，不实现 SQL Server 目标导入。

## Technical decisions / 局部技术设计

- 增加 `mssql` JSONL 来源与 CLI 选择，保留既有数据库语义；默认 schema 内探测表、列、完整主键及外键，跨 schema 依赖不能静默忽略。
- 类型遵循总计划最低矩阵：保留 identity 数值，支持列逐值往返；按元数据拒绝第七位时间精度、datetimeoffset、生成列及其他未覆盖类型，不能在驱动丢失精度后才检查，也不能静默省略列。
- 保持已有限表、`fetchmany(1000)` 分批、进度和原子文件语义，不使用 `fetchall`。公开驱动无服务端游标的限制，不新增硬性内存上限、分页方案或多规模内存基准。

## Implementation outline / 实施安排

1. 按总计划矩阵适配 SQL Server 元数据、类型和列 / 外键预检，扩展 JSONL 来源校验。
2. 实现可复核的导出与筛选，确保失败不覆盖有效制品；为 T05 产出可重复生成的隔离测试制品。
3. 用真实 SQL Server 验证常见类型、主外键、选表、压缩、分批结果完整性和原子输出。

## Checks and delivery / 检查与交付

- 测试 source DSN 必须带数据库、header 来源、精度、未知类型、跨 schema FK、外键顺序及旧方言回归。
- 交付可访问的代码版本、隔离测试制品位置、分批与结果完整性证据、检查结果和剩余限制；不记录业务数据或凭据。仅遇实际内存问题时补充代表性观测。

## Risks / 风险

- SQLAlchemy 方言无服务端游标，`fetchmany` 不等同于驱动级有界内存；类型与特殊列须遵循共同矩阵，由 T05 核对与 T04 的一致性。

## Reception and progress / 接收与进展

- 2026-11-02 21:01：主会话接收T03-20261102-02并完成结构清理。交付当前工作树的共用元数据加载、格式转换、结构规则与导出路径；不保留MssqlColumn行为对象或专用模块。默认587项回归、SQL Server19项集成均通过，实际包含默认schema/支持类型/SQLite往返/Books p7拒绝；静态检查通过，主会话审查接受。以下旧分配、模块路径和哈希仅保留为历史，不是当前实现依据。

- 2026-11-02 19:46:04：按主会话 review 沿用 `T03-20261102-01` 返工。主会话已实测支持类型导出及 Books p7 拒绝，发现 MSSQL→SQLite 的 UNIQUEIDENTIFIER/BIT 兼容缺失。本次补充 UNIQUEIDENTIFIER 文本族及 MSSQL 专属布尔声明规范化，使用 TEXT/BOOLEAN/DATETIME(6)/BLOB/DECIMAL 的真实 SQLite 目标做代表性类型回归；不改变其他引擎 BIT/bit string 行为，不访问实库。完成后释放传输文件，等待 T04 正式分配。
- 2026-11-02 19:32:19：从会话2（01a19bab-f096-7df1-aa33-2378453da6a9）接收 `T03-20261102-01`；核对总计划修订5及 T01 accepted，实施授权来自主会话本次正式派发及其记录的用户授权。权威根与代码根均为 `D:/SourceCodes/mywork/dbtalk`；基线 HEAD `5708aff64c10d6c0cd3e5f947b5b0ba54a20ab8a` 加当前工作树 T01 连接/query 与依赖变更。保留全部已有暂存和未暂存内容，不提交、不建分支。
- 本次仅写传输模块、新 MSSQL 导出模块、新专用测试及自身 task；CLI 由主会话集成，不改其他执行者文件。目标 MSSQL 导入继续拒绝。复用已完成只读风险调查，以真实 catalog 元数据解决精度丢失，在选中表级拒绝特殊列及跨 schema 外键；不访问业务数据库。完成聚焦单测及静态质量检查后交付主会话审查，实库由主会话后续执行。
- 2026-11-02 19:38:25：已实现 catalog 精度读取、选表后预检、schema 限定 SELECT、UUID/decimal/BIT 值处理及 mssql 来源 JSONL；新专用测试与既有传输回归合计 67 passed、1 skipped（原有 PostgreSQL 集成测试未配置）。新代码 Ruff 通过；首次全量 Mypy 发现本任务测试中方言构造类型问题（正在修正），另有并行 T02 的 `mssql_script.py:162` unreachable，不修改其文件。待完成检查并记录交付。CLI 后续须将 export 预校验改为 `validate_connection(connection, operation="export")`，默认校验仍拒绝 mssql 目标；既有 `test_database_operations.py:113` 断言旧的 transfer 拒绝文案需由主会话更新为 import 文案。

## Delivery and review / 交付与审查

### T03-20261102-01 / 2026-11-02 19:43:27 交付待审查

- 实际变更：新增 `src/dbtalk/database/mssql_transfer.py`，用参数化 `sys.columns/sys.tables/sys.schemas/sys.types` 读取真实精度、长度、生成属性及 identity 等元数据；默认 schema 内先选表再读取列，只预检选中表。拒绝 p7、datetimeoffset、computed、rowversion/timestamp、用户自定义及矩阵外类型，亦拒绝系统生成列；跨 schema FK 明确失败，同 schema FK 沿用既有排序与选表依赖检查。
- `TableSchema` 仅新增可选 `schema_name`，MSSQL SELECT 分别引用 schema/table，JSONL 表名不变。`DATETIME2(p)`、`TIME(p)`、DECIMAL precision/scale、Unicode 长度与 MAX 声明保留；UUID 显式规范化字符串，BIT 保留布尔，decimal 拒绝浮点输入，identity 普通数值照常导出。全局 TIMESTAMP 语义、旧方言类型与转换逻辑未改。
- `format.py` reader/writer 及传输适配器接受 mssql 来源；export 校验允许 MSSQL 且仍要求数据库路径，MSSQL 目标导入在公共入口与内部适配器仍明确拒绝。MSSQL 不设置 stream_results/yield_per，复用 fetchmany(1000)、进度和原子输出。
- 变更文件：上述新模块、`sqlalchemy_transfer.py`、`format.py`、`models.py` 的 TableSchema 字段、`transfer.py`、新 `tests/test_mssql_transfer_export.py` 及本 task。`schema.py` 无需改动。models.py 中既有脱敏/query 修正属于 T01 基线，本任务未改动。未修改 CLI/operations/query/connection/dsn/依赖、旧公共测试、其他 task 或总计划；未触碰暂存、分支或提交。
- 检查：`uv run --locked --no-sync pytest tests/test_mssql_transfer_export.py tests/test_database_transfer.py tests/test_database_transfer_batching.py tests/test_database_transfer_logging.py -q` → **69 passed, 1 skipped**。跳过为既有 PostgreSQL 专用实库测试未配置，不读取业务 DSN。新测试通过真实文件 reader/writer 验证 plain/gzip、UUID/decimal/Unicode/binary/日期时间、复合主键顺序、include/exclude 隔离不支持表、p7/生成列读取前拒绝、跨 schema FK、异常原子保留；模拟源 2005 行分批导出后实际导入 SQLite 并检查总数及首尾 ID。
- 检查：六个本次产品/测试文件的 Ruff format --check、全仓 `uv run --locked --no-sync ruff check src tests scripts`、六文件 Mypy、`uv lock --check` 及本次跟踪文件的 `git diff --check` 均通过。使用 Makefile 同等 uv 命令，无安装/升级。全仓 Ruff format --check 在检查时因并行工作区 `operations.py:134` 与 `test_mssql_integration.py:158` 格式未通过；全仓 Mypy 最新结果为并行 `tests/test_mssql_script.py:172,208,209` 三项类型错误。本任务范围检查通过，未擅改其他执行者文件；全仓检查须主会话集成后重跑。
- 实库与制品：未连接真实 SQL Server，真实驱动往返、真实 catalog 结果及真实导出制品由主会话按派发约定后续验证；当前是使用真实 SQLAlchemy 方言、DBAPI/Inspector 替身和 SQLite 的离线证据，不冒充 SQL Server 实库通过。测试制品由 pytest tmp_path 创建，可通过上述测试重复生成，未加入仓库业务数据。
- 主会话待处理：CLI 增加 mssql source 选择，并把 export 的 `validate_connection(connection)` 改为 `validate_connection(connection, operation="export")`；旧 `tests/test_database_operations.py:113` 的 transfer 全拒绝文案断言需改为 import 拒绝，并保持导入未放行。此处由主会话按派发约定修改，不构成本任务越权修改。
- T04 交接：可复用 `load_mssql_columns` / `MssqlColumn` 的 catalog 信息及 TableSchema.schema_name；`export_definition` 是导出专用拒绝规则，不能直接用于允许省略生成列的目标导入。当前 header 保留原生 BIT/UNIQUEIDENTIFIER 声明，未扩大既有跨引擎类型兼容策略；目标精度/编码/UUID/identity/upsert 仍由 T04 实施。MSSQL 源到 SQLite 的代表性整数路径已检查，完整最低矩阵跨引擎往返未验证。
- 剩余限制：fetchmany 不代表驱动有界内存；catalog 使用 SQL Server 的 generated_always_type，需支持该字段的服务器（SQL Server 2016+），更老版本未承诺验证。未进入整体 Verification。范围偏差：无；待仲裁事项：主会话审查实现、完成分工内 CLI/旧测试集成及实库检查后决定是否接受。

交付位置为权威根当前工作树；前置为接收记录的 T01 accepted 工作树。下列 Git blob 哈希仅通过 `git hash-object` 只读计算，未写入 Git 对象或创建提交，可用于主会话核对本次交付：

| 文件 | 交付内容哈希 |
| --- | --- |
| `src/dbtalk/database/mssql_transfer.py` | `58ab08a2155851362da882e468ff1b96b2a15b87` |
| `src/dbtalk/database/sqlalchemy_transfer.py` | `548f5904591d44e0d4146fa5e29c3c37d94b8093` |
| `src/dbtalk/database/format.py` | `2eb8cad0974b11269e33b79dc9b58fa750147a9a` |
| `src/dbtalk/database/models.py` | `44063f9b3f9789377b210ed3172f0ad420fd1101` |
| `src/dbtalk/database/transfer.py` | `ff7d78735d9d2a701870bdb5461cfa141bd6fafb` |
| `tests/test_mssql_transfer_export.py` | `53fbd7d1b4491914fa544283c1f2067be2241e91` |

### T03-20261102-01 / 2026-11-02 19:49:25 Review 返工交付

- 主会话已回报真实 SQL Server 支持类型导出成功、Books datetime2(7) 被正确拒绝；本会话仅引用该 review 结果，未连接实库。返工原因是跨引擎导入时 UNIQUEIDENTIFIER/BIT 仍属 unknown，SQLite 类型反射亦不能把 UNIQUEIDENTIFIER 视作 UUID。
- 修正 `format.type_family`：仅将 SQL Server 专属 `UNIQUEIDENTIFIER` 归入文本族，使显式规范化后的 UUID 字符串可进入 TEXT/CHAR/VARCHAR 目标；保持 PostgreSQL `UUID` 等既有兼容规则。
- SQL Server BIT 在 `MssqlColumn.export_definition` 中规范化为 JSONL 的 `BOOLEAN` 声明，值仍由 MSSQL 专用转换保持 bool；catalog 中的真实 `type_name=bit` 保留。没有全局把裸 BIT 或 BIT(n) 改成 boolean，避免误改 PostgreSQL/MySQL 位串语义。此处替代上次交付“header 保留原生 BIT”的说明；主会话须重新导出制品再复测，旧临时制品不增加兼容层。
- 新增代表性 MSSQL→SQLite **insert/upsert** 测试：用模拟 MSSQL DBAPI 导出真实 JSONL，再导入实际 SQLite 表（uid TEXT、active BOOLEAN、created DATETIME(6)、payload BLOB、amount DECIMAL(12,3)、title TEXT），逐值检查 UUID、true/false、六位时间、二进制、可精确表示的 Decimal、Unicode 和 NULL；已有导出测试继续覆盖 38 位 Decimal 标签精度。新增非 MSSQL BIT/BIT(8) 位串语义保护检查。
- 命令：`uv run --locked --no-sync pytest tests/test_mssql_transfer_export.py tests/test_database_transfer.py tests/test_database_transfer_batching.py tests/test_database_transfer_logging.py -q` → **73 passed, 1 skipped**（原有 PostgreSQL 外库测试）。本次三文件 Ruff format/check、Mypy 与相关 git diff --check 通过。未重跑真实 SQL Server，由主会话在重新生成制品后复测；未进入整体 Verification。
- 变更仅 `mssql_transfer.py`、`format.py`、`tests/test_mssql_transfer_export.py` 及本 task；未写其他共享文件。T04 后续应使用真实 catalog 类型与 BOOLEAN 来源声明进行目标兼容校验。无新增范围偏差或需用户决策事项。
- 本次授权返工完成，**传输文件已释放，停止写入，待主会话审查及 T04 正式分配**。其余交付文件沿用上次快照与主会话已做集成；下列三文件哈希替代上次相应记录：

| 文件 | 返工交付内容哈希 |
| --- | --- |
| `src/dbtalk/database/mssql_transfer.py` | `98b81e7a2b8d84e7134e19ab2bb90ed52ca791a6` |
| `src/dbtalk/database/format.py` | `783b1080c3321fbf11d43a42cf74c01177e723fa` |
| `tests/test_mssql_transfer_export.py` | `6e5438088b3be23ae19d5fb730e93f63a8ddca77` |
