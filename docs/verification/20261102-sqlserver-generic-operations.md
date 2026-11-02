# Verification / 验证：SQL Server 通用数据库操作
最后修改时间: 2026-11-02 21:32:53

## Review status / 审查状态

Draft

验证已执行完成，报告待审查。SQL Server通用功能与开发检查通过；A01交付验收部分通过，存在已复现的既有wheel默认配置缺失，以及Linux/macOS未验证项。此结论不表示全平台发布就绪。

## Baseline and authorization / 基线与授权

- 用户本轮明确要求使用best-practices:dev-check完成specflow:specflow验证。沿用先前DemoBook测试及RDC_APP的select 1授权；仅本主会话执行。
- 仓库：`D:/SourceCodes/mywork/dbtalk`；HEAD：`5708aff64c10d6c0cd3e5f947b5b0ba54a20ab8a`。开始验证时30个文件变更已由用户暂存；检查期间不改产品、测试、依赖或index，只更新本功能过程文档。
- 验证开始的`git diff --cached --binary` SHA-256：`158ee0df339ab335d3c4bcf2e4136dd8667b65892701c313cb5f0c16b8c785b4`，用于核对本次交付快照；不是新提交。
- 本机Windows、Python 3.12；锁定项目环境：pymssql 2.4.5、SQLAlchemy 2.0.52、pytest 9.1.1、Ruff 0.16.3、Mypy 2.3.1。未安装或升级依赖，未格式化或修复代码，未发布。

## Intent alignment / 意图对齐

依据[意图](../intent/20261102-sqlserver-generic-operations.md)的A01-A10：支持同步SQL Server query/exec、SQL文件与JSONL export/import，使用既有DSN工作流；不增加SQL Server管理命令、备份/恢复、账号授权或异步封装。

查询、写事务与现有SQLAlchemy调用一致，未增加查询文本防护；JSONL按当前最低类型矩阵、默认schema、选表、外键顺序及逐表事务执行。用户最新对结构和原有行为的要求已纳入当前验收：无新增mssql产品模块，无可选DSN解析包装，单SQL dry-run不解析DSN或参数。

## Plan alignment / 计划对齐

依据[计划修订7](../plan/20261102-sqlserver-generic-operations.md)及21:23收敛：

- `connection.py`沿用查询连接与写事务，仅补驱动超时、后续结果消费和受控错误。
- `operations.py`沿用现有脚本入口，SQL Server仅增加GO批次解析；原DSN解析供CLI复用。文件预览提供DSN时离线解析方言，未提供时仍用通用预览。
- 元数据、值转换、结构规则分别放入既有`models.py`、`format.py`、`schema.py`；导出导入共用`sqlalchemy_transfer.py`主流程，identity/upsert等保留必要方言分支。
- 依赖由uv声明与锁定；Makefile只补pymssql的PyInstaller动态导入。README、手册和仓库通用skill公开类型、同步驱动和超时限制。

## Task deliveries and integration / 子任务交付与集成

全部使用上述同一暂存快照，不引用旧从会话模块或历史通过数量替代当前结果。

| ID / 有效编号 | 当前交付位置 | 必要检查与集成核对 | 验收归属与结果 |
| --- | --- | --- | --- |
| T01 / T01-20261102-04 | 依赖、dsn/models/connection及执行测试 | 原始SQL与命名参数、CTE、JSON、超时、事务、结果消费；无SQLGlot或查询拦截器。实际query/exec成功 | A02-A04、A10通过；A01驱动部分通过 |
| T02 / T02-20261102-02 | operations/cli及脚本测试 | 同一DatabaseClient执行；GO、变量、过程体、dry-run、失败回滚通过；原有MySQL/PG预览回归通过 | A05、A10通过 |
| T03 / T03-20261102-02 | 既有传输、格式、结构模块及导出测试 | 选表、catalog精度、主外键、分批、gzip、原子输出、Books p7拒绝；与T04实际往返 | A06、A10通过 |
| T04 / T04-20261102-02 | 同一传输主流程及导入测试 | 预检、insert/upsert、identity、复合PK、重复输入、并发、逐表回滚、生成列省略通过 | A07、A10通过 |
| T05 / T05-20261102-01 | 文档、skill、Makefile与dist产物 | wheel源码一致、Windows实际query及1007行压缩传输通过；wheel默认配置失败明确记录 | A08-A10通过；A01部分通过 |

依赖保持`T01 -> T02/T03/T04 -> T05`；各下游使用清理后的同一代码。21:23的DSN/预览修正已进入wheel，43个Python源文件逐字节比较无差异；本轮实库与二进制传输复测覆盖最终集成。T05集成证据交付已接受，不等同于A01全项通过。

## Actual diff summary / 实际差异摘要

- 9个既有`src/dbtalk/database`模块发生变更，没有新增产品模块；query/exec复用现有路径，JSONL增加mssql来源与必要方言处理。
- 更新pyproject/uv.lock和Makefile；pymssql为2.4.5，不依赖系统ODBC，不再引入SQLGlot。
- 更新一个原有操作测试文件，新增六个SQL Server测试文件；测试替身用于行为单测，真实锁、identity、驱动精度和事务由实库测试验证。
- 更新README中英文、数据库手册和通用skill；七份本功能意图/计划/task记录与实际交付对应。本轮新增验证报告，并同步意图、计划和T05的阶段记录。

## Expected vs actual changed files / 预期与实际修改文件对比

| 计划范围 | 实际文件 | 核对 |
| --- | --- | --- |
| 驱动、连接与模型 | pyproject.toml、uv.lock、database/dsn.py、connection.py、models.py | 对齐 |
| SQL及CLI | database/operations.py、cli.py | 对齐；没有独立执行器或可选DSN包装 |
| JSONL | database/format.py、schema.py、sqlalchemy_transfer.py、transfer.py | 对齐；mssql专用产品文件已移除 |
| 测试 | test_database_operations.py和六个test_mssql*.py | 对齐；原有三引擎测试参与全量回归 |
| 打包与公开说明 | Makefile、README.md、README.zh-CN.md、docs/database.md、plugins/dbtalk/skills/dbtalk/SKILL.md | 对齐 |
| 过程记录 | 同名intent/plan、T01-T05、本文 | 对齐 |
| 管理功能、配置加载、用户业务数据 | 无产品改动 | settings.py与HEAD相同；现有表仅读取 |

## Acceptance criteria checklist / 验收标准检查清单

| 验收 | 结果 | 证据和范围 |
| --- | --- | --- |
| A01 uv依赖与交付 | **部分通过** | 锁、wheel依赖及Windows原生驱动通过；Windows二进制实际读写通过。wheel缺默认配置，原样启动失败；Linux/macOS未测 |
| A02 DSN与错误 | 通过 | 既有.env优先级及DSN单测、缺库名传输预检、async受控拒绝、脱敏测试；用户原目录全局命令返回1 |
| A03 query/exec | 通过 | 同DSN参数/CTE/结果读取；自建表UPDATE OUTPUT经query读取后未提交，exec提交。无SQL语法防护 |
| A04 执行/超时/回滚 | 通过 | 原生1秒timeout、临时表事务、后续结果错误导致回滚；进程级超时和影响行数限制已说明 |
| A05 文件与预览 | 通过 | GO/变量/过程体、无连接预览、文件回滚；单SQL预览原行为、MySQL/PG文件预览均有回归 |
| A06 导出 | 通过 | 默认schema/选表/主外键/类型、2005行分批单测、1007行gzip实库、失败不替换制品；p7列在读数据前拒绝 |
| A07 导入 | 通过 | SQLite与SQL Server完整字段往返、insert/upsert、并发完整PK、identity、二进制/Unicode/decimal/日期、预检与表回滚 |
| A08 文档与skill | 通过 | 对照当前实现；无单SELECT主张，说明JSONL类型矩阵、驱动和事务限制，无新增管理skill |
| A09 质量与集成 | 通过（限实际环境） | Ruff/格式/Mypy、默认587项、实库19项及二进制复测通过；14项其他外部数据库测试未运行 |
| A10 范围 | 通过 | 没有管理功能扩展。开发期按用户新报故障同步过editable依赖；本轮未安装或改代码、配置、暂存 |

## Test / command results / 测试与命令结果

项目入口为Makefile的`check`、`test`与CI中的`uv lock --check`。当前PowerShell的PATH无make，因此使用Makefile已声明的锁定uv命令，不临时安装工具。除注明外，cwd均为`D:/SourceCodes/mywork/dbtalk`。

| 本轮实际命令 | 类别 | 结果 |
| --- | --- | --- |
| `uv run --locked --no-sync ruff format --check src tests scripts` | 格式只读检查 | 通过，81 files already formatted |
| `uv run --locked --no-sync ruff check src tests scripts` | 静态检查 | 通过，All checks passed |
| `uv run --locked --no-sync mypy src tests scripts` | 类型检查 | 通过，82 source files |
| `uv lock --check` | 依赖锁校验 | 通过，43 packages |
| `uv run --locked --no-sync pytest -q --tb=short` | 默认回归 | 587 passed、33 skipped、5 subtests passed，12.76秒 |
| `uv run --locked --no-sync pytest tests/test_mssql_integration.py tests/test_mssql_transfer_integration.py -q --tb=short --show-capture=no` | SQL Server实库 | 19 passed，15.09秒；显式启用下述两个变量 |
| `uv run --locked --no-sync pytest tests/test_mssql_transfer_integration.py::test_mssql_cli_archive_batches -q --tb=short --show-capture=no` | Windows二进制实库 | 1 passed，10.45秒；另设DBTALK_MSSQL_TEST_BINARY，1007行gzip实际export/import upsert |
| `git diff --check HEAD` | 差异空白检查 | 通过 |
| `dist/dbtalk-sqlserver-smoke.exe query --dsn-env DBTALK_DSN_TEST1 --sql 'SELECT DB_NAME() AS database_name, COUNT_BIG(*) AS books FROM dbo.Books' --format json` | 二进制查询 | DemoBook、100 |
| `dist/dbtalk-sqlserver-smoke.exe exec --dsn invalid --sql 'SELECT :value' --param invalid --dry-run` | 原有单SQL预览行为 | 原样输出SQL，退出0，不解析DSN或参数 |
| `uv run --locked --no-sync dbtalk query --dsn-env DBTALK_DSN_TEST1 --sql "SELECT COUNT_BIG(*) AS remaining_test_tables FROM sys.tables WHERE name LIKE 'dbtalk_transfer[_]%'" --format json` | 清理核对 | 0 |
| `dbtalk query --dsn-env DBTALK_DSN_TEST_RDC_APP --sql 'select 1'` | 用户原命令复测 | 返回1，退出0；cwd为`D:/SourceCodes/mingyuan/rdc_mks_aid` |

实库变量：`DBTALK_MSSQL_TEST_DSN_ENV=DBTALK_DSN_TEST1`、`DBTALK_MSSQL_TRANSFER_TEST_WRITE=1`；二进制变量指向仓库`dist/dbtalk-sqlserver-smoke.exe`。每次执行后恢复原进程变量。写入只发生在连接临时表与唯一前缀的fixture自建表，finally清理；没有修改Books或用户业务表。

默认跳过33项中，19项SQL Server检查已单独显式运行；其余14项是PostgreSQL批处理1项、PostgreSQL owner 9项、MySQL/PostgreSQL session 4项，缺少各自显式配置的隔离端点，未运行。没有把默认跳过计入通过，也没有把SQLite替身当作MySQL/PostgreSQL实库验证。

### Artifact checks / 产物核对

本轮复用21:22-21:23最后一次构建的产物，不重复安装依赖或构建。此前`uv build`与Makefile等效PyInstaller命令已成功，本轮检查实际产物内容与运行结果：

| 产物 | SHA-256 |
| --- | --- |
| dist/pomelo_dbtalk-0.41.2-py3-none-any.whl | `8a41506ea59c13ae0e2e722531d5820f806587ef55740a8d0f96103556a4e700` |
| dist/dbtalk-sqlserver-smoke.exe | `c048241ed03e87271123bffeb0c5406dc4c6e7fe784a156851e75d20eeb3098f` |

通过现有项目Python读取zip：wheel内43个Python文件逐字节匹配`src/dbtalk`，无mismatch；METADATA声明`pymssql>=2.4.5,<2.4.6`，但没有dbtalk.yaml。

用`uv run --locked --no-sync python -I -c <只读检查脚本>`将wheel置于sys.path首位，并断言dbtalk.__file__来自wheel，再通过CliRunner执行同一DemoBook查询；依赖复用现有.venv，未安装包：

- 不提供默认配置：退出1，`Error: mysql.client_image must not be empty`，**失败**。
- 在该进程中提供仓库已有mysql/postgres client_image、query/exec timeout与session poll interval配置：退出0，DemoBook/100，**通过**。执行后恢复变量。
- 该检查验证wheel中的代码与驱动可运行，不冒充全新环境安装验证。实施阶段已有隔离uv环境结果，见T05；本轮按dev-check限制不重复安装。

settings.py与HEAD无差异，构建配置仅新增pymssql依赖和二进制hidden-import；默认YAML位于包目录之外，配置定位逻辑未随wheel携带它。该问题是既有通用wheel配置缺口，仍实质影响开箱启动，不能因非本次引入而将A01标为全通过。

## Missed or expanded scope / 遗漏或扩大的范围

- 原有查询语法防护、独立SQL Server执行/导入模块和可选DSN包装已在实施中撤销；本轮核对源码、依赖和公开文档无残留。
- 文件dry-run在提供DSN时离线解析方言，因此无效DSN会失败；这是计划明确的文件预览变化，不能笼统声称所有旧dry-run行为不变。单SQL预览已恢复原行为。
- 共享传输流程改为先选表再加载元数据、表头阶段校验，空表同样预检；MySQL/PG特定写入逻辑保留，已有回归通过，未扩大为全六向实库矩阵。
- 用户全局工具缺pymssql已在实施阶段按原editable来源同步并修复；本轮只复测。没有对用户级plugin、管理命令、业务数据或暂存区作改动。

## Risks / 风险

- SQL Server query不提交不等于数据库强制只读，显式事务SQL仍由数据库执行；未建立只读账号或SQL权限沙箱。
- pymssql仅同步，timeout/login_timeout具有进程级影响；fetchmany不证明驱动不缓冲结果集。混合DML/查询批次的影响行数取决于驱动可见结果。
- JSONL支持矩阵以当前计划为准；datetime2(7)、time(7)、datetimeoffset及未支持类型预检失败，生成列不可显式迁移，跨schema外键不支持。Books可查询，但其datetime2(7)使整表JSONL导出被拒绝。
- SQL Server实测版本为2016；catalog依赖generated_always_type，未宣称更早服务器版本兼容。Linux/macOS构建与运行未实际执行。

## Incomplete items / 未完成事项

1. wheel的默认配置交付缺口尚未修复；本轮是只读开发检查和验证，不擅自修改配置加载或发布机制。
2. Linux/macOS实际runner验证未进行；真实MySQL/PostgreSQL迁移不在本轮已执行证据内，14项已有外部数据库测试仍跳过。
3. 不收集覆盖率、不启动服务、不发布、不提交或暂存；这些不属于本轮检查入口和授权。

## Conclusion / 结论

整体验证工作完成：代码质量、SQL Server通用功能、既有默认回归、Windows二进制运行和用户实际命令通过；A02-A10在报告明确的环境范围内满足要求。A01部分通过，保留wheel默认配置缺失和未测平台两项交付缺口。没有发现需要额外SQL抽象、查询防护或管理功能来完成当前功能的理由；不宣称所有交付条件均已通过。
