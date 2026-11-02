# Task / 子任务 T04：SQL Server JSONL 导入
最后修改时间: 2026-11-02 21:01:00

## Review status / 审查状态

Accepted

## Parent and authority / 父任务与权威记录

- 总意图：[意图](../../intent/20261102-sqlserver-generic-operations.md)。
- 权威总计划：[计划](../../plan/20261102-sqlserver-generic-operations.md)；任务 ID：T04；方案修订：7。
- 依赖、分配编号和接受结论以总计划为准。

## Handoff basis / 交接依据

- 有效分配 `T04-20261102-02`，主会话执行，计划修订7；使用当前工作树T01与T03清理版本，用户要求本会话收尾。
- 主会话直接维护执行和审查事实，不再派发。

## Goal and acceptance / 目标与验收

交付 SQL Server 的 JSONL 目标导入，覆盖 A07、A10。`insert` 和完整主键 `upsert` 都在预检后写入，遵守表级事务、外键顺序及精度和特殊列边界。

## Inputs and eligibility / 输入与可开始条件

T01 的连接代码版本须被主会话 accepted 且可访问；以既有 JSONL 协议、总计划最低类型矩阵和既有 fixture / SQLite 制品为输入。计划与任务方案已接受，有实施授权和有效分配；不要求 T03 先完成。

## Scope and files / 范围与文件

- 在包含 T01 的协调基线上修改 `database/sqlalchemy_transfer.py`、`schema.py`、`format.py`、CLI 及相关测试；删除专用导入模块，不新增适配框架。
- 不创建目标 schema、删除现有数据、关闭外键约束或修改其他数据库的导入语义。

## Technical decisions / 局部技术设计

- 单独实现 SQL Server 的主键匹配 upsert，不沿用不支持的 `ON CONFLICT`；明确复合主键、重复输入、并发冲突、约束和触发器语义。
- 遵循总计划最低类型矩阵；高精度 / 偏移时间及未覆盖类型在预检拒绝，不静默丢列或截断精度。制品提供 computed / rowversion 目标列时拒绝；未提供且目标能生成时保留目标生成行为。
- 保留显式 identity 值：同一连接按表管理 `IDENTITY_INSERT`，upsert 不更新 identity 列；缺省值沿用既有目标生成规则。使用者须已有所需表权限，数据库拒绝时回滚并清理连接状态，不增加授权或权限审计功能。
- 全量预检、选表、目标元数据加载、外键排序、回放和参数分批共用既有流程；SQL Server只保留identity和upsert等必要写入差异。每表单事务，失败表回滚，前序已提交表保留。拒绝未覆盖schema/外键。

## Implementation outline / 实施安排

1. 基于既有 JSONL 协议与总计划矩阵适配目标类型、特殊列和主键策略，建立明确的预检边界。
2. 实现 SQL Server insert/upsert 和表事务，验证参数绑定、资源释放及错误脱敏。
3. 使用既有 fixture 和 SQLite 产生的 JSONL 验证隔离 SQL Server 导入，核对失败前后状态；SQL Server 自身往返由 T05 汇合 T03 制品验证。

## Checks and delivery / 检查与交付

- 聚焦单/复合主键、新增/更新、重复键、外键顺序、identity、自动生成列、Unicode、日期精度和导入失败回滚。
- 验证 SQLite 来源和既有 MySQL/PostgreSQL 回归；真实跨引擎实例可用时补充检查，记录实际覆盖、隔离实例版本和未验证项，不新建全六向实库矩阵。
- 交付可访问代码版本、使用的 T01 基线与 fixture / SQLite 制品版本、检查和风险供主会话审查。

## Risks / 风险

- `IDENTITY_INSERT` 的连接级状态、计算列、rowversion 和并发 upsert 会影响准确性；要验证失败清理和预检边界。

## Reception and progress / 接收与进展

- 2026-11-02 21:01：主会话完成T04-20261102-02并审查接受。当前工作树删除MssqlTarget包装和重复导入编排，所有引擎共用表头预检、单表元数据与行转换；分批使用同一1000行/8MiB函数。支持类型、空二进制、identity、复合PK、并发upsert和逐表回滚在19项实库集成中通过，默认587项回归及静态检查通过。以下专用模块、旧版本和哈希仅为历史；以修订7现有职责划分为准。

- 2026-11-02 19:52:09：从会话2接收 `T04-20261102-01`，核对权威总计划修订5的 assigned 登记、用户实施/协调授权及 T01 accepted。权威根与代码根均 `D:/SourceCodes/mywork/dbtalk`；基线 HEAD `5708aff64c10d6c0cd3e5f947b5b0ba54a20ab8a` 加当前 T01/T03 accepted 工作树，主会话已报告真实 T03→SQLite→JSONL 检查成功。
- 独占传输模块、新导入模块/专用测试及本 task；CLI、旧公共测试和实库集成测试由主会话维护。沿用主会话实库执行分工，不自行访问业务数据库。实施全量预检、目标类型边界、identity 会话清理及逐表 insert/upsert 事务，补充离线聚焦测试后交付并释放共享文件；不提交/建分支/安装/进入整体 Verification。
- 2026-11-02 20:06:36：新 MSSQL 导入路径已接入，移除目标临时拒绝；catalog 补充默认值与排序规则 code page，JSONL scanner 增加表头校验回调，空表同样预检。离线专用与既有传输回归 **118 passed、1 skipped**；覆盖 1002 行分批 insert、复合 PK upsert/重复输入、非主键 identity 不更新、生成列省略、全量预检、实际 SQLite 事务回滚、identity OFF 失败后连接失效、保留原始写失败及先前表提交。正在完成边界复核和全仓质量检查；实库仍由主会话运行。MSSQL SQL 由测试适配到 SQLite 只用于验证事务，不把它等同于 SQL Server 锁语义验证。
- 2026-11-02 20:18:12：按主会话集成补充沿用有效分配，移除导入放行后已无用途的 validate_connection.operation 参数及其调用/专用测试参数，恢复统一 validate_connection(connection)，不保留兼容空参数。CLI 已由主会话接管，本次不修改 CLI。
- 2026-11-02 20:19:59：按主会话真实 DATETIME 样例继续返工同一分配；以 FreeTDS/pymssql 实际返回的每10毫秒 .000/.003/.007 毫秒呈现校验，不接受推算的 .003333/.006666 微秒值。只修正既有 DATETIME 边界与专用测试，不扩展类型矩阵，实库测试仍由主会话维护执行。

## Delivery and review / 交付与审查

### T04-20261102-01 / 2026-11-02 20:14:50 交付待审查

- **实现完成并释放共享传输文件，停止写入，待主会话审查实测。** 当前工作树可直接调用 MSSQL `import_database` 的 insert/upsert；移除公共入口及内部适配器的 import 占位拒绝。授权范围内代码无未完成项；真实 SQL Server 验证按派发约定由主会话执行，未擅自连接业务数据库或进入整体 Verification。
- 新增 `src/dbtalk/database/mssql_import.py`：默认 schema 下选中目标表的 catalog 预检与导入。JSONL scanner 增加可选 table_validator，在读取行前验证每个表头（空表同样覆盖），同时逐行校验整个文件；全部预检和 FK 顺序检查完成后 rollback 只读元数据事务，再按表开启写事务。重放时重新校验值并检查表头/顺序/行数，复用现有 JSONL 协议和流式迭代。
- 元数据补充默认值和 `COLLATIONPROPERTY(..., 'CodePage')`。保留源/目标类型族兼容校验；目标真实 scale/precision 决定日期、整数和 decimal 可表示性。拒绝源显式 p7/datetimeoffset 以及目标 p7/offset/矩阵外类型；不经过 float 处理 decimal；UUID 字符串显式转 UUID，BIT 使用 BOOLEAN 语义；NVARCHAR 按 UTF-16 单元、VARCHAR 按真实 code page 检查字节长度及编码可逆性；二进制检查长度，固定宽度字符串/二进制需已有完整长度，拒绝隐式 padding。
- 日期时间不使用通用截断函数的精度参数。datetime2/time 检查非零超额小数位，smalldatetime 检查分钟刻度及 1900–2079-06-06 范围，datetime 检查 1753 下限及 1/300 秒刻度（接受驱动微秒表示和 SQL Server 毫秒显示表示），绑定时使用 ISO 字符串避免 pymssql 默认 datetime 参数截到毫秒。真实 legacy datetime/smalldatetime 结果仍需主会话实库核对。
- insert 在同一表事务内按 1000 行或 8 MiB 参数预算分批；upsert 按完整、非 NULL 主键执行 `SELECT ... WITH (UPDLOCK, HOLDLOCK)`，再 INSERT 或 UPDATE，保持输入顺序和重复键行为，不依赖驱动 rowcount 判断是否存在，不重试写入。更新集合排除主键及全部 identity（含非主键 identity）。
- 显式 identity 在同连接按表 ON，写入后 OFF 成功才提交；任何写入/转换/OFF 失败均回滚该表，再清理 OFF；清理失败则 invalidate 连接，并保留原始写失败作为错误原因。identity 缺省仅在 insert 且目标可生成时允许；upsert 仍要求完整 PK。computed/rowversion/系统生成列若制品提供则拒绝，省略则交给目标生成；省略普通列只允许 nullable/default/identity。缺省和显式 NULL 不混同。先前已成功提交表在后续表失败时保留。
- 其他必要修正：`_create_engine` 捕获 ImportError，缺 pymssql 等驱动为受控 DatabaseTransferError；T03 专用测试删除临时 import 拒绝断言并补充 catalog 新字段。未改 CLI、旧公共测试、主会话实库测试、其他数据库逻辑、总计划或其他 task；未变更暂存、提交、分支或依赖安装。
- 实际文件：新 `mssql_import.py`、`mssql_transfer.py`（catalog 字段）、`format.py`（可选表头回调）、`sqlalchemy_transfer.py`（分派与受控缺驱动）、`transfer.py`（移除拒绝）、新 `tests/test_mssql_transfer_import.py`、既有本执行者专用 `tests/test_mssql_transfer_export.py` 及本 task。schema.py / models.py 本次无需新增修改。
- 检查：`uv run --locked --no-sync pytest tests/test_mssql_transfer_import.py tests/test_mssql_transfer_export.py tests/test_database_transfer.py tests/test_database_transfer_batching.py tests/test_database_transfer_logging.py -q` → **123 passed, 1 skipped**。跳过为原有 PostgreSQL 外库测试未配置。新增 50 个测试案例，覆盖目标类型边界、空表元数据拒绝、完整主键、缺省生成、分批、复合主键锁谓词与重复输入、identity 排除更新、失败清理/失效、实际 SQLite 事务回滚及前序表提交保留。
- 检查：`uv run --locked --no-sync ruff format --check src tests scripts` → 86 files already formatted；`uv run --locked --no-sync mypy src tests scripts` → 87 source files passed；本次七个产品/测试文件 Ruff、相关 git diff --check 与 `uv lock --check` 均通过。全仓 Ruff 在 20:12 检查时仅主会话并行维护的 `tests/test_mssql_transfer_integration.py:85` E501、`:220` SIM117 两项未通过；未擅改该文件，主会话集成后重跑。
- 证据边界：专用测试将写入 SQL 适配至真实 SQLite 事务以核对 commit/rollback 数据效果，并用真实 SQLAlchemy MSSQL 方言检查引号及锁 SQL；没有把替身 SQL/SQLite 当作 SQL Server 并发、identity 或驱动往返通过。所有 SQL Server 实库往返、并发重复 PK、legacy 日期和会话清理检查由主会话按其已维护的测试执行；无真实数据库制品新增到仓库。
- 交付版本：基线为接收记录中的 HEAD 加 T01/T03 accepted 工作树；当前权威根工作树的以下内容哈希可供核对，仅只读计算未创建 Git 对象/提交。范围偏差：无。待主会话处理：复核实际 diff、执行已授权实库集成、处理其测试文件的 Ruff 项并仲裁 accepted；本会话等待审查。

| 文件 | 交付 Git blob 内容哈希 |
| --- | --- |
| `src/dbtalk/database/mssql_import.py` | `7c86dcb0ccd4b059ea4c829899bce5a14334c660` |
| `src/dbtalk/database/mssql_transfer.py` | `1abf71d8bd172c79c66d1de976295a943b7cc56d` |
| `src/dbtalk/database/format.py` | `905996a3301f8d82db859433fe92ce44ced46a34` |
| `src/dbtalk/database/sqlalchemy_transfer.py` | `e676fd0666629839a08ddeb34812982486b246f0` |
| `src/dbtalk/database/transfer.py` | `031c1c9820fd22976e4650e58a4dbc564400317d` |
| `tests/test_mssql_transfer_import.py` | `e903d0b3c14d676f7849422ba026d0ef19e87b20` |
| `tests/test_mssql_transfer_export.py` | `9f116766be51f302f2db32e0370ba64a0955938f` |

### T04-20261102-01 / 2026-11-02 20:18:52 集成参数清理补交付

- 已移除 validate_connection.operation 参数、typing.Literal 导入及 export 调用中的参数，T03 专用测试统一调用 validate_connection(connection)，没有兼容空参数。仅变更 transfer.py、专用导出测试及本 task，CLI 与旧公共测试保持主会话所有权。
- 本次两文件 Ruff format/check、Mypy 通过；同一传输聚焦回归再次 **123 passed、1 skipped**，跳过原因同前。扫描 src/tests 时只剩主会话维护的 `tests/test_database_operations.py:113` 仍传 operation="export"，需主会话按约定更新；产品代码及本执行者测试已无残留。
- 当前 transfer.py blob 为 `d3fc9bc6885d062337d6bb09d2ebf8bec28b7cfb`，专用导出测试为 `7d1403d2721857457e10f59a9088faa94e9218f2`，替代上次对应哈希。其余交付沿用。已再次释放共享文件，停止写入，待主会话审查。

### T04-20261102-01 / 2026-11-02 20:21:13 DATETIME 表示修正补交付

- DATETIME 仅接受实际驱动可无损往返的整毫秒，末位为 0/3/7（每10ms循环），保留1753下限；绑定为 ISO 毫秒字符串。移除推算 ticks/3333 微秒双重表示逻辑，拒绝 .003333 和会被舍入的 .006。此项替代首次交付记录中“接受驱动微秒和显示两种表示”的描述，不再声称 FreeTDS 返回 3333/6666 微秒。
- 专用正向案例覆盖 .000/.003000/.007000/.123000，失败边界覆盖 .003333/.006000；不扩大最低类型矩阵。主会话维护真实 datetime/smalldatetime 样例，本会话未连接数据库。
- 两文件 Ruff format/check、Mypy 通过；同一传输聚焦回归 **127 passed、1 skipped**。新 mssql_import.py blob `184b65ae1699068d9e26482f66864ca4e797c2eb`，专用导入测试 blob `7a5f28713ce4a73a22914eebf4d8f8e36faa6921`，替代前述对应哈希。
- 修正完成并再次释放共享文件，停止写入，待主会话审查实库结果。

### T04-20261102-01 / 2026-11-02 20:24:25 空二进制绑定修正补交付

- 主会话首轮实库回报 6 pass / 3 fail，失败共同原因为 pymssql 把空 bytes 绑定为 varchar，SQL Server 对 varbinary 拒绝隐式转换；并发 identity upsert、预检及表回滚已由主会话实测通过。本次按相同分配返工，未自行连接实库。
- INSERT/UPDATE/主键谓词共用一个列感知 binding 生成器：BINARY/VARBINARY 参数统一 `CAST(:value AS VARBINARY(MAX))`，包括空 bytes、非空 bytes 和 NULL，未改 JSONL 二进制编码，也未只对某个 fixture 特判。其余类型保持既有参数绑定。
- 整数 target_value 已有显式 `isinstance(decoded, bool) -> int(decoded)` 分支；本次新增 BOOLEAN 来源→INT 完整预检/写入测试，核对 False/True 绑定类型确为 int 0/1，不会进入 Decimal(str(bool))。未以异常兜底替代明确转换。
- 新增二进制 PK/数据列路径：初始 INSERT、空主键匹配后 UPDATE 清空 payload、新主键 INSERT 空 payload；同时断言三种 SQL 位置都采用 CAST，并在 SQLite 事务适配器验证最终 bytes。该测试只适配 SQL 语法，不冒充真实 pymssql 结果，主会话需重跑先前三个失败实库案例。
- 两文件 Ruff format/check、Mypy 通过；传输聚焦回归 **129 passed、1 skipped**（原有 PostgreSQL 外库测试）。mssql_import.py blob `a2b297e008ede10fc21efbc49e4d13f4bfdc2cb4`，专用导入测试 blob `48998ceacfab2a715649b107a27f06c43ccdcf19`，替代上次对应哈希。
- 当前返工完成并释放共享文件，停止写入，待主会话审查实库重跑；不修改主会话实库测试或其他文件。

### T04-20261102-01 / 2026-11-02 20:25:50 DATETIME 精度约定确认

- 当前代码精确要求 microsecond 可整除1000、对应毫秒末位为0/3/7，不接受 .003333/.006666，也不将外部 datetime2/SQLite 的高精度值改写后接收。补充 .006666 明确拒绝案例；保留 .000/.003/.007/.123 正向案例。源 DATETIME 的 .003/.007 及 SMALLDATETIME 分钟列的真实全字段往返由主会话新增实库样例验证。
- 空 bytes 的所有位置 CAST 修正继续保留，产品代码哈希仍为 `a2b297e008ede10fc21efbc49e4d13f4bfdc2cb4`；专用导入测试新哈希 `aaff8b27165544f6f3e7d5cc0e15680dd5760f6e`。同一聚焦回归 **130 passed、1 skipped**；本次格式、Ruff、Mypy 通过。
- 补交付完成并再次释放文件，停止写入，等待主会话实库全字段比较及最终审查。
