# Task / 子任务 T01：同步连接与查询执行
最后修改时间: 2026-11-02 21:01:00

## Review status / 审查状态

Accepted

## Parent and authority / 父任务与权威记录

- 总意图：[意图](../../intent/20261102-sqlserver-generic-operations.md)。
- 权威总计划：[计划](../../plan/20261102-sqlserver-generic-operations.md)；任务 ID：T01；方案修订：7。
- 任务依赖、执行者、授权和有效分配编号以总计划为准；方案状态不表示交付已接受。

## Handoff basis / 交接依据

- 当前清理：主会话，编号 `T01-20261102-04`，修订7；沿用用户实施和实库授权及当前工作树，其他会话已停止，不再派发。
- 可写记录位置为本文的接收、进展与交付章节；默认在执行会话输出可复制摘要，由用户转交主会话。

## Goal and acceptance / 目标与验收

交付可由 uv 安装的同步 `mssql+pymssql` 连接、`query` 和 `exec --sql`，覆盖 A01-A04、A10。复用现有查询与写事务，不要求独立只读账号；验证 DSN、参数、结果、事务、超时及错误脱敏，不新增 SQL 防护。

## Inputs and eligibility / 输入与可开始条件

无直接任务前置。必须先接受总计划及本方案，并获得本任务实施授权和有效分配；需核实隔离 SQL Server 及具备写权限的测试 DSN 可用性。

## Scope and files / 范围与文件

- `pyproject.toml`、`uv.lock`、`database/dsn.py`、`models.py`、`connection.py` 及相关聚焦测试。
- 既有通用执行入口仅作必要适配；不实现 SQL 文件、JSONL 传输、数据库管理、异步驱动或用户配置迁移。
- 不自动安装已发布 CLI、同步用户级 skill 或访问业务连接。

## Technical decisions / 局部技术设计

- 新方言仅支持同步 `mssql+pymssql`，数据库名规则和环境变量优先级沿用总计划；async 调用明确拒绝。
- SQL Server query 使用现有连接读取结果且不提交；exec 使用现有写事务提交。删除 SQLGlot 和查询检查，不设专用查询模块。其他引擎保持原有数据库只读会话。
- 分别验证 `login_timeout`、`timeout`、连接失败与异常链；记录驱动进程级超时对并发客户端的限制，扩展凭据脱敏。不增加自动重试或并发执行框架。
- 选定既有发布平台可用的官方 wheel 范围；任何额外运行时系统 ODBC 依赖不符合目标。

## Implementation outline / 实施安排

1. 核对平台 wheel、锁文件与 DSN、同步/异步边界。
2. 添加同步驱动解析、会话与错误处理，接入参数、连接和语句超时。
3. 在隔离 SQL Server 使用同一 DSN 验证查询、参数与输出、query 不提交、exec 写入及失败回滚；保留可供 T02/T03/T04 使用的代码版本和证据。

## Checks and delivery / 检查与交付

- 聚焦 DSN、CLI、同步会话、超时和脱敏测试；数据库原生行为使用已授权的隔离实例验证。
- 检查不同 DSN、空库名、`.env` 优先级、Unicode/特殊字符凭据、未安装驱动及 async 拒绝；不打印真实连接。
- 向主会话交付可访问版本、命令结果、前提和未验证项；实际交付按总计划审查。

## Risks / 风险

- SQL Server query 不提交不等于数据库强制只读；驱动 timeout 进程级影响及 wheel 平台范围需公开说明。

## Reception and progress / 接收与进展

2026-11-02 21:01：主会话接收并完成当前清理。交付当前工作树 connection/dsn/models、依赖和测试；删除专用查询过滤器及其测试，保留必要的后续结果消费和超时处理。默认587项回归与SQL Server19项集成通过，Ruff/格式/Mypy/锁检查通过；主会话审查接受。以下均为已被修订7替代的历史记录，旧拒写要求和SQLGlot方案不再适用。

2026-11-02 19:59:17：从会话1核对总计划 19:57:53 的有效返工登记后接收 T01-20261102-03。仅修复关键字命名绑定误拒与 exec / execute_literal 后续结果消费，允许修改 connection.py、mssql_query.py、专用测试及本文；T02 同步修复换行 list 列，仍使用 T02-20261102-01。保持原始 SQL、参数绑定、同 DSN 和既有接口；不改 CLI / operations / 传输 / 公共测试 / 主会话实库测试，不提交或修改暂存。协议沿用已读 SpecFlow 与 task-coordination；主会话负责新增实库回归，本会话先离线验证并交付。

2026-11-02 20:09:14：已核对 pymssql 2.4.5 官方 `_mssql.pyx` 的 get_result / nextresult / format_and_run_query：无行结果会继续读取直到行结果或结束；首个行结果之后需 nextresult，否则下一次执行会 cancel 尚未消费结果。此次修复针对仍保有 cursor 的 SQLAlchemy MSSQL 结果消费后续行及结果集；不是只在已有返回后增加检查或假定提交会报错。已完成本地带真实事务的 SQLite 游标适配回归，主会话负责真实 SQL Server 新回归。

2026-11-02 17:55:59：主会话接收 `T01-20261102-02`，方案修订 3，用户授权开始实施；工作基线 HEAD `5708aff64c10d6c0cd3e5f947b5b0ba54a20ab8a` 与现有暂存过程文档，无任务前置。先实施连接层；SQL Server query 在保障机制确认前保持关闭，不将局部交付视为完成。

2026-11-02 18:02:26：增加 `pymssql` 依赖和 uv 锁、同步 DSN / 方言识别、async 明确拒绝和登录 / 语句超时参数；query 在会话准备阶段暂时直接拒绝，不能视为实现 A03。通过 `uv lock --check`、Ruff、Mypy、操作测试 36 项和全量 pytest（445 passed、14 skipped、5 subtests passed）；本机方言加载成功，未连接 SQL Server 或验证实际写入和打包。

从会话1核对官方资料：SQL Server 的 `BEGIN TRANSACTION` 没有 `READ ONLY` 选项，事务隔离级别也不阻止本事务写入；`pymssql.read_only=True` 仅为连接意图。可读副本、数据库只读和权限上下文切换均需要额外部署或权限前提。当前没有找到符合 A03 所有条件的受支持机制；等待主会话与用户确定处理，不降低验收，T01 不交付或放行下游。调查未连接数据库，相关资料可见 SQL Server 官方 BEGIN TRANSACTION、SET TRANSACTION ISOLATION LEVEL 文档及 pymssql 2.4.5 文档。

2026-11-02 18:03:26：审查发现同步 DSN 放开会使 Python 传输入口提前接受 `mssql`；在 `database/transfer.py` 增加明确拒绝，并添加回归测试，待 T03/T04 实现时移除。这是防止未完成适配器被调用的必要临时保护。复查 `uv lock --check`、Ruff、Mypy 通过，全量 pytest 为 446 passed、14 skipped、5 subtests passed；仍未验证 SQL Server 实库。

2026-11-02 19:07:18：用户指定并确认使用 `DBTALK_DSN_TEST1` 的 `DemoBook.dbo.Books`，要求测试本次 SQL Server 能力。新增 `tests/test_mssql_integration.py`，通过进程变量 `DBTALK_MSSQL_TEST_DSN_ENV=DBTALK_DSN_TEST1` 显式启用；未配置时跳过，不自动连接数据库。未修改 Books 等现有表，写入仅限连接内临时表。

- 实库命令：`uv run --locked --no-sync pytest tests/test_mssql_integration.py -q --tb=short`，结果 4 passed、1 failed。
- 通过：本次 DSN / 驱动连接正确指向 DemoBook；参数绑定和中文往返；Books 最多三行的 UUID / 文本 / datetime 返回类型；真实 CLI exec 的带参 SELECT；临时表创建 / 插入 / 删除，以及重复主键异常后的事务回滚（核对同一服务器会话，临时表创建亦回滚）；1 秒语句超时中止 3 秒 WAITFOR。
- 失败：真实 CLI query 对 Books 的合法 SELECT 返回 `mssql query is unavailable until read-only protection is verified`，A03 仍未完成；测试保留正常查询应成功的断言，不掩盖实现缺口。
- 最新质量检查：Ruff format / check 与 Mypy 全部通过（77 个源文件）；`uv lock --check` 通过。登录超时错误路径、进程级并发超时、凭据异常脱敏、打包和 JSONL 往返尚未在实库验证。
- 本次属于实施内开发检查，不表示整体 Verification 或 T01 交付 accepted；下游依赖仍不放行。

2026-11-02 19:27:17：按用户要求修复 query，全拒绝分支已移除。首次 sqlparse 单查询检查被从会话1指出真实 T-SQL 字符串和 # 词法绕过，未接受交付；现改为 SQLGlot 30.x T-SQL 语法树、禁止 Command 回退及原 SQL 解析错误泄露，支持 SELECT / CTE / UNION / 参数 / table / JSON，拒绝写入、INTO、sequence、多语句和远程执行入口。执行仍使用原 SQL，不做方言转写。25 项校验单测与 11 项实库检查全部通过（36 passed）；Ruff、Mypy、锁检查通过。原有 Books 数据未修改，事务实测仅使用连接内临时表。

## Delivery and review / 交付与审查

2026-11-02 19:30:24：`T01-20261102-02` 交付同一工作树当前连接 / DSN / query 代码，uv 依赖为 pymssql 2.4.5、SQLGlot 30.27.0。从会话1独立审查发现的词法绕过已由 T-SQL 解析取代修正；verbose 仍遍历被抑制异常的缺陷已修正，补充真实 format_cli_error 路径的 ParseError / TokenError 脱敏与 MSSQL/PWD 脱敏测试。查询 / CLI 聚焦 43 passed，前次实库与解析 36 passed，Ruff / Mypy / 锁检查通过。A02-A04 的同步连接、普通查询、拒写、exec、事务和语句超时有证据；未验证平台构建归 T05，未宣称数据库强制只读或任意 CLR 副作用隔离。主会话审查后接受用于下游的交付。完整工作树无提交，保留用户暂存内容；现有 Books 数据未修改。后续共享文件由总计划协调。

### 2026-11-02 20:09:14 / T01-20261102-03 / 限定返工交付待审查

- 基线：权威根同一工作树 19:57 已审查版本；方案修订5。未提交、建分支或改暂存，未访问业务数据库。
- 实际变更：connection.py、mssql_query.py、tests/test_mssql_query.py，新增 tests/test_mssql_execution.py；共用消费函数同步由 T02 的 mssql_script.py 引用。未改 CLI / operations / 传输 / 公共测试 / 主会话实库测试；接口签名保持稳定。
- 参数：SQLGlot 子解析器在占位符位置识别紧邻冒号的完整命名 token，允许 from/select/where/values 等关键字名。只修改校验解析行为，传给 SQLAlchemy 的原 SQL 和参数字典不变。真实 SQLAlchemy 绑定到本地 SQLite 的替身查询已验证关键字名及含引号 / DROP 文本的参数值按字面值返回；原有字符串、引用标识符、DML / INTO / 多语句拒绝回归通过。
- 结果消费：DatabaseSession.execute / execute_literal 在 MSSQL 行结果仍保留 cursor 时调用 `_consume_mssql_results`，逐批 fetchmany / nextset，关闭结果后才返回。原生 DBAPI 错误在此转为受控执行 / 超时错误，外层事务收到异常并回滚。无行结果仍使用驱动 rowcount，其他方言保持既有行为；与文件执行共用 helper，依赖方向 mssql_script → connection → mssql_query，无模块循环。
- 本地证据：execute 参数路径、execute_literal 路径在第一或第二结果集之后失败，前序 INSERT 均回滚；execute_script 的后续结果错误也回滚；普通 INSERT / UPDATE rowcount 与 SELECT 执行后的连接复用通过。此为 SQLite 事务 + 自定义多结果游标适配，不能代替 SQL Server 实测。
- 检查：`uv run --locked --no-sync pytest tests/test_mssql_query.py tests/test_mssql_script.py tests/test_mssql_execution.py tests/test_database_operations.py -q` 为 **127 passed**。针对 connection.py、mssql_query.py、mssql_script.py 及上述三个 MSSQL 专用测试文件运行 `ruff check`、`ruff format --check`、`mypy`，全部通过（6 files）；本任务 `git diff --check` 通过。
- 可访问快照 / SHA256：connection.py `E2C84578B88C1692F37C316BD980C22D9FB9BE96CE62D7766F3BC0FBF30588EA`；mssql_query.py `CF0E772BA21368B5B66B10AC1075EC3F563D324CDEC03904EE4D1F102F0E21A5`；tests/test_mssql_execution.py `B7C49686842F83C68C9C76D4D414824B18485DB13581C04D688B6E41BF9F88AC`。
- 范围偏差：无；共享结果消费属已授权复用，未增加其他功能。未运行项：真实 SQL Server 后续结果错误和关键字绑定回归，由主会话准备执行；未运行全量测试、构建、发布或整体 Verification。
- 待主会话：对单条 EXEC（过程先返回 SELECT、随后重复主键）及 execute_script 的多结果错误验证失败和前序写入回滚，并验证 :from 实际绑定；审查后决定 accepted 或返工。T04 接口不变，T05 放行前需使用本修正重新核对集成。
- 本次写入已结束，释放 connection / query / 专用测试写入权；CLI 一直由主会话独占。本记录不自行改变总登记。

### 2026-11-02 20:13:25 / T01-20261102-03 / 追加证据与校准

- 主会话追加实库证据：原实现的 UPDATE 自建表 → SELECT → 重复主键 INSERT 样例在 commit 阶段抛出 `database transaction failed`，literal 参数化两例均已验证前序 UPDATE 回滚。此样例不是静默提交，先前审查对可能性的提示不得当成实际数据提交证据。本次修复目标是完整消费结果、将晚期执行错误在返回 / 提交前归一化，以及保持正常 exec 行数行为。
- 主会话另报告旧实现拒绝 CAST(:from AS INT)。当前 20:09 交付的占位符修正版已离线接受 CAST(:from AS INT)、TRY_CAST(:where AS INT)、CONVERT(INT, :values)，本轮无需再改产品代码；补充相应回归，并在实际 SQLAlchemy → 本地 SQLite 绑定替身中验证 CAST 内的四种关键字名及原始参数值。
- 正常多结果 exec 新增成功路径覆盖：消费两个 SELECT 结果后返回 0 affected rows，后续 UPDATE 可正常执行，不将读取行数计作写入数。写入计数仅累计驱动实际暴露的非行结果 rowcount；pymssql 不暴露的中间 DML 计数不推测或伪造，不能声称逐语句总写入数完整。
- 新检查：`uv run --locked --no-sync pytest tests/test_mssql_query.py tests/test_mssql_execution.py -q` 为 45 passed；这两个测试文件 Ruff / format / Mypy 均通过。前次其他脚本与公共操作检查结果不变。本轮只改这两个专用测试及本文，没有修改主会话实库文件或再次连接数据库。
- tests/test_mssql_execution.py 最新 SHA256 为 `27A4935255A459B4DAC6F82F2BEBE02180967D4F67C4774ACBD792E5254E334C`；产品代码指纹沿用 20:09 记录。待主会话用最新产品文件运行新实库回归并审查最终结果；本次写入再次结束。
