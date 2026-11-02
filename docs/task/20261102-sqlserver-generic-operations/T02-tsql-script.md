# Task / 子任务 T02：T-SQL 文件执行与预览
最后修改时间: 2026-11-02 21:24:20

## Review status / 审查状态

Accepted

## Parent and authority / 父任务与权威记录

- 总意图：[意图](../../intent/20261102-sqlserver-generic-operations.md)。
- 权威总计划：[计划](../../plan/20261102-sqlserver-generic-operations.md)；任务 ID：T02；方案修订：7。
- 依赖和有效分配由总计划维护，方案 Accepted 不代表已交付。

## Handoff basis / 交接依据

- 有效分配：T02-20261102-02；执行者：主会话；总计划修订7，用户已授权在本会话完成清理及DemoBook实测。
- 不提交、不建分支、不改暂存，现有表仅读取；不再联系其他执行者。
- 基线：权威根 `D:/SourceCodes/mywork/dbtalk`，HEAD `5708aff64c10d6c0cd3e5f947b5b0ba54a20ab8a` 加当前同一工作树 T01 accepted 的 DSN / query / 同步执行代码。
- 协议入口：SpecFlow SKILL.md 及 references/task-coordination.md；主会话直接维护本文与总计划。

## Goal and acceptance / 目标与验收

交付按 T-SQL 批次执行的 `exec --file` 和无数据库连接的对应 dry-run，覆盖 A05、A10；同批变量和过程定义作用域正确，不能安全处理的脚本在写入前明确失败。

## Inputs and eligibility / 输入与可开始条件

T01 的同步方言、DSN 与会话交付须经主会话 accepted 且版本可访问；总计划及本方案已接受，用户已授权实施并登记有效分配。

## Scope and files / 范围与文件

- `database/operations.py`、`database/cli.py` 及相关操作测试；与 T03/T04 对 `cli.py` 的写入串行协调。
- 不改变其他数据库脚本语义或扩展查询、JSONL 功能。

## Technical decisions / 局部技术设计

- 基于方言划分批次并保证执行与 dry-run 同源；识别独立行 `GO`，不将字面量、标识符和注释中的 `GO` 当成分隔符。
- 文件 dry-run 离线解析显式 `--dsn` / `--dsn-env`，与执行共用方言及批次逻辑；显式 DSN 无效或变量缺失时失败，不建立数据库连接。无 DSN 保留通用预览，SQL Server 准确预览须提供对应 DSN，不新增方言选项或按脚本猜测。单 SQL dry-run 沿用原有直接预览，不解析 DSN 或参数。
- 首版仅支持普通独立行 `GO`；拒绝 `GO n` 和 SQLCMD 指令。批次原样交给既有 DatabaseClient.execute_script，不检查 EXEC 或事务关键字，显式事务遵循数据库语义。分批逻辑放在 operations.py，删除专用模块和独立engine执行器。

## Implementation outline / 实施安排

1. 确定可处理的批次语法及对不支持语法的预检，保持默认数据库行为不变。
2. 统一 SQL Server 的文件执行与 dry-run 划分；明确事务、异常和执行单元展示。
3. 用隔离数据库和不连接数据库的 dry-run 测试核对变量作用域、过程批次、注释 / 字符串以及失败回滚。

## Checks and delivery / 检查与交付

- 覆盖原有 SQLite/MySQL/PostgreSQL 文件测试、`--file` / `--param` 互斥和事务控制回归。
- 用连接替身证明 dry-run 无连接；覆盖显式 DSN / 环境变量的离线方言解析、错误参数、无 DSN 通用预览，以及预览与执行批次一致性。真实 SQL Server 验证批次及回滚，交付受支持语法及可访问版本。

## Risks / 风险

- T-SQL 脚本的 `GO`、模块定义和显式事务均有批次上下文；未经语义验证的分号拆分会改变行为。

## Reception and progress / 接收与进展

- 2026-11-02 21:23：按用户要求对照HEAD现有query/exec和dry-run，删除新加的可选DSN包装，直接复用原必填解析函数；单SQL预览恢复既有语义，文件预览仅在提供DSN时解析方言。MySQL/PG文件预览与SQL Server GO预览经聚焦测试验证，72 passed；默认全量587 passed、33 skipped、5 subtests passed，SQL Server实库19 passed，Ruff/format/Mypy通过。wheel/Windows二进制已重建，二进制的单SQL预览和DemoBook查询通过，沿用当前主会话编号。

- 2026-11-02 21:01：主会话完成T02-20261102-02清理并审查接受。当前工作树operations/cli/connection共用脚本与执行入口；测试改为公共sql_script_statements。默认587项回归和SQL Server19项集成通过，包括GO、变量、临时过程、dry-run及后续错误回滚。以下为已被修订7替代的历史记录，专用执行器与过程/事务过滤不再适用。

- 2026-11-02 19:59:17：按主会话新授权恢复 T02-20261102-01 限定返工，仅修改 mssql_script.py 与专用测试，修正换行 list 列误认 SQLCMD；与 T01-20261102-03 协调共用结果消费实现。不恢复 CLI / operations 写入权，不改主会话实库测试和其他 task。

- 2026-11-02 19:32:18：从会话1已核对总计划修订5、有效编号 T02-20261102-01、用户实施授权及 T01 accepted 工作树，正式接收。独占 operations.py、database/cli.py、新 T-SQL 脚本模块和专用测试；不改公共测试、查询和传输模块、总计划或其他 task。
- 实施顺序：先实现 T-SQL 词法安全的 GO 批次划分和事务预检，再统一执行与显式 DSN 的离线预览，最后执行聚焦 Ruff / Mypy / 回归测试。实库检查仅在有明确隔离资源授权时进行；当前不连接数据库。
- 2026-11-02 19:40:33：已完成新 mssql_script 模块及 operations / CLI 接入。SQLGlot tokenizer 会对 GO、END 等命令合并后续文本，因此批次扫描使用限定的 T-SQL 词法（单引号、双引号、方括号、转义重复定界符、嵌套块注释）；不使用 sqlparse 拆分 T-SQL。执行及显式 DSN dry-run 共用 mssql_script_batches，未指定 DSN 的预览仍走原通用逻辑。
- 驱动检查发现 pymssql 后续结果集的错误可在 nextset() 才抛出；现有 execute_literal 只读取首个 rowcount，不适合直接复用多语句批次。新模块复用 T01 的连接参数与超时映射，通过 SQLAlchemy engine.begin() 包住所有批次，逐批 fetchmany / nextset 后才提交；不修改 connection.py。将用本地 SQLite 游标适配替身验证后续失败使前序写入回滚，实库行为仍待主会话授权隔离验证。
- 首版事务预检拒绝 COMMIT / ROLLBACK、BEGIN / SAVE TRAN(SACTION)、SET IMPLICIT_TRANSACTIONS；EXEC / EXECUTE 调用因可隐藏事务控制而明确拒绝（模块的 EXECUTE AS 子句除外），需执行此类 SQL 使用 exec --sql。这是保守脚本支持边界，不声称可隔离触发器或其他数据库内部代码的副作用。
- 2026-11-02 19:47:31：实现与聚焦检查完成，交付待主会话审查。补充拒绝不带 EXEC 的批次起始过程调用、SQLCMD 命令和占位变量（含字符串 / 标识符中的占位变量）；普通 DML / DDL、DECLARE、BEGIN / IF / WHILE 等常见批次入口明确支持。不支持的入口不会降级执行。未使用 GO n，不提供 SQLCMD 替换或过程内部事务审计。

## Delivery and review / 交付与审查

### 2026-11-02 19:47:31 / T02-20261102-01 / 交付待审查

- 执行者：从会话1；方案修订5；前置基线为上述同一工作树 T01 accepted 版本，HEAD 未改变。未提交、未建分支、未修改暂存。
- 实际修改：`src/dbtalk/database/mssql_script.py`（新增）、`operations.py`、`cli.py`、`tests/test_mssql_script.py`（新增）、本 task。未改查询模块、connection、models、传输文件、旧公共测试、总计划或其他 task。
- 交付位置：`D:/SourceCodes/mywork/dbtalk` 当前工作树；无分支或提交。主会话可直接 review 三个产品文件与新专用测试；当前执行者已结束这些文件的写入，CLI 可移交主会话集成 T03。

#### 行为与验收证据

- SQL Server 文件先离线解析 DSN，再进行完整预检和批次划分；所有批次通过后才创建引擎。GO 只作为独立行分隔符，可带注释；同批 DECLARE / SELECT、模块定义 BEGIN / END 均按原始批次保留，不按分号重写。
- 字符串、方括号 / 双引号标识符、单行 / 嵌套块注释中的 GO 不分批；反斜杠不转义单引号，# 不作为注释；分隔符两侧跨行注释保持完整。无实际语句的批次跳过，未闭合词法明确报错且不回显 SQL。
- GO n、SQLCMD、显式事务控制及不透明过程调用在引擎创建前拒绝。普通 BEGIN / END 块和过程定义不误当事务 BEGIN；带显式事务控制的过程定义也保守拒绝。单独 `exec --sql` 的执行和绑定逻辑保持原样。
- SQL Server 多批次使用一个 engine.begin() 写事务，复用 T01 的 pymssql 登录 / 语句超时参数；每个批次分批消费结果并调用 nextset，确保后续结果错误在提交前传播。SQLite 游标适配替身已证明正常提交、后续批次失败回滚、后续结果集报错回滚。受影响行数按驱动可用的非查询结果 rowcount 累计，不承诺 pymssql 对多语句批次提供逐语句完整计数。
- 显式 `--dsn` / `--dsn-env` dry-run 完全离线，预览与实际执行使用同一批次函数；用 GO 展示批次边界并报告 batches。无 DSN 保持原 statements 预览；无效 DSN / 缺失环境变量 / 双 DSN 参数明确失败。`--file` / `--param` 互斥保留。

#### 检查命令与实际结果

工作目录均为权威根，使用现有 Makefile 对应的锁定 uv 工具入口，仅检查本任务范围：

- `uv run --locked --no-sync ruff check src/dbtalk/database/mssql_script.py src/dbtalk/database/operations.py src/dbtalk/database/cli.py tests/test_mssql_script.py`：通过。
- `uv run --locked --no-sync ruff format --check src/dbtalk/database/mssql_script.py src/dbtalk/database/operations.py src/dbtalk/database/cli.py tests/test_mssql_script.py`：4 files already formatted。
- `uv run --locked --no-sync mypy src/dbtalk/database/mssql_script.py src/dbtalk/database/operations.py src/dbtalk/database/cli.py tests/test_mssql_script.py`：4 files，无问题。
- 首次 `uv run --locked --no-sync pytest tests/test_mssql_script.py tests/test_database_operations.py -q`：75 passed、1 failed。失败仅为旧公共测试 `test_mssql_transfer_rejects_unimplemented_adapter` 仍断言 `mssql JSONL transfer is not supported yet`，并行 T03 已改为 `mssql JSONL import is not supported yet`；未修改该公共测试。
- 补充支持边界用例后，`uv run --locked --no-sync pytest tests/test_mssql_script.py tests/test_database_operations.py -q -k 'not mssql_transfer_rejects_unimplemented_adapter'`：80 passed、1 deselected。覆盖本任务全部新测试和其余既有通用操作回归。
- 本任务已跟踪文件 `git diff --check`：通过。

#### 交付指纹 / SHA256

| 文件 | SHA256 |
| --- | --- |
| src/dbtalk/database/mssql_script.py | 4A5C2AE7A64366DD94BF12E1860B6FDB415DA8C1AD26A06A44B1BFB0265C78C9 |
| src/dbtalk/database/operations.py | D6632D5E90702C9456C7F1658DFB4FF1045A6CE14AA9201EFAE1B9CFACA03126 |
| src/dbtalk/database/cli.py | B58E15F22835D0203F10291F145F413FC34378B366A3A7E49A63DCCBF0AF85E0 |
| tests/test_mssql_script.py | 1A2501F26A94163BBDB10CA00A067D32986FFDB12C4BB4A2A7AACACE651FCAA8 |

#### 未运行项、范围偏差与待处理

- 未连接任何业务数据库；未运行真实 SQL Server 文件批次、过程定义和回滚集成验证。本次仅获得本地开发检查授权，尚无明确隔离 SQL Server 资源可供从会话使用。A05 的真实服务器部分仍待主会话安排，不能以 SQLite 替身替代实库通过。
- 没有安装工具、发布、运行全量检查或进入整体 Verification。依赖未改动。
- 实现选择说明：因后续结果集错误风险，在新模块增加专用批次执行器而非直接调用现有 execute_script；仅复用内部连接选项与期限工具，不改 T01 文件。为保证直接脚本控制不跨越托管事务，保守拒绝 EXEC、隐式过程调用及未知批次入口；主会话审查时需确认并在 T05 用户文档说明这一支持边界。
- 下游影响：CLI / operations 文件写入权现在可交还主会话；T03/T04 不需调用新脚本接口。旧公共传输测试需由主会话随 T03 接入统一更新。
- 待仲裁：审查当前代码、上述脚本边界及测试证据，并安排授权的 SQL Server 实库验证；由主会话决定 accepted 或返工。本记录不自行修改总登记状态。

### 2026-11-02 19:50:13 / T02-20261102-01 / 收尾与写入权移交

- 按主会话审查意见，仅将 mssql_script.create_engine 的异常捕获改为 `(SQLAlchemyError, ImportError)`，与 T01 保持一致。未改 CLI、operations 或其他产品范围。
- 本地缺驱动替身检查通过：create_engine 抛 ModuleNotFoundError 时转换为 DatabaseOperationError，正文为 `database engine could not be created`，保留 ImportError cause，未尝试连接数据库。
- 收尾检查：`uv run --locked --no-sync ruff check src/dbtalk/database/mssql_script.py`、对应 `ruff format --check`、`mypy` 均通过；`uv run --locked --no-sync pytest tests/test_mssql_script.py -q` 为 45 passed。上方脚本模块指纹已更新。
- 主会话回报的追加证据（从会话未重复连接实库）：query + script + integration 为 86 passed；`tests/test_mssql_transfer_integration.py::test_mssql_file_late_error_rolls_back` 通过，自建表跨 GO 前序 UPDATE、后续 SELECT 后重复主键均全部回滚。旧公共测试已由主会话随导出支持更新，对应公共测试 36 passed。此前公共文案失败及晚期结果错误的实库验证缺口已由这次回报补齐；不将未说明的其他实库场景自动标为通过。
- 明确释放 CLI、operations、新脚本模块及专用测试的写入权，主会话可立即接管 CLI 集成。从会话自此不继续写文件；后续如需 query / script 审查，仅只读进行。本 task 交付供主会话决定最终登记状态。

### 2026-11-02 20:09:14 / T02-20261102-01 / 限定返工交付待审查

- 按 19:57:53 总计划登记和新授权完成返工；本次只修改 mssql_script.py、tests/test_mssql_script.py 与本文。CLI / operations 没有恢复写入。
- 移除将裸 LIST 列名当作 SQLCMD 命令的规则，其他裸词命令要求独占该行；明确的冒号 / shell 命令前缀继续拒绝。新增 `SELECT\n list, name\nFROM dbo.Items` 及单独一行 list 列的正常路径回归，原有 SQLCMD / GO n / 事务控制拒绝用例保持通过。
- 按 T01 同次返工授权复用 connection.py 中 `_consume_mssql_results` 和游标 Protocol，删除本模块重复的消费循环。批次执行仍保持单事务、全部结果消费及后续错误回滚；依赖不循环，不改对外接口。
- 检查与 T01 联合执行：查询、脚本、新执行测试及旧公共操作测试 **127 passed**；涉及的6个文件 Ruff / format / Mypy 全通过，diff 空白检查通过。命令详见 T01-20261102-03 本次交付记录。
- mssql_script.py 最新 SHA256：`5FC37003A1E0C8D3D6ED1BC0D9A938781F2894F1B1631FD6167EFAFF6E4FEFC1`。同一权威工作树直接可审查；不提交、不改暂存。
- 未运行真实 SQL Server；主会话此前的真实文件晚期错误回滚证据保留，建议随共用 helper 修正重新运行该回归。未扩大 SQLCMD 或过程调用支持范围，其他范围偏差无。
- 本次写入已结束，脚本模块及专用测试写入权交还主会话。CLI / operations 始终保持已释放；等待主会话审查决定登记状态。
