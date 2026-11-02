# Intent / 意图：SQL Server 通用数据库操作
最后修改时间: 2026-11-02 21:32:53

## Review status / 审查状态

Accepted

## Background / 背景

用户需要通过 dbtalk 通用 skill 操作 SQL Server，并要求 Python 依赖由 uv 管理，不要求用户另行安装系统 ODBC 驱动。现有 dbtalk 基于 SQLAlchemy，通用操作仅支持 SQLite、MySQL 和 PostgreSQL；SQL Server 的查询、T-SQL 脚本及 JSONL 迁移尚未实现。

本会话已评估 `pymssql`。官方 wheel 包含 FreeTDS，在适用平台可由 uv 安装；该驱动提供同步 DB-API，SQLAlchemy 的方言为 `mssql+pymssql`。最初仅做临时 uv 环境调查；后续用户已授权实施和 DemoBook 实测，依赖和锁文件已更新，具体检查证据记入 task。该方言不支持服务端游标，不承诺驱动内存有界。

## Goals / 目标

- 为 SQL Server 提供通用 `query`、`exec`、`export` 和 `import`，使用已有 `DBTALK_DSN_*` / `--dsn-env` 工作流。
- 支持 SQL 文件和 dry-run，以及 JSONL 的 `insert`、按完整主键的 `upsert`；保留已有目标 schema、预检、外键顺序及表级事务等约束。
- 保持 `query` 与现有同步 MySQL / PostgreSQL 的使用契约一致，并正确记录超时、数据类型和驱动限制；不以弱化保障或静默丢失数据冒充兼容。
- 覆盖仓库已有 uv wheel、独立二进制与相关文档、通用 skill 的交付边界。

## Non-goals / 非目标

- 不增加 SQL Server 备份/恢复、数据库管理、账号、权限、会话管理或独立数据库管理 skill。
- 不增加 SQL Server 异步 Python 客户端，不引入系统 ODBC 或 `pyodbc` 作为运行前提。
- 不迁移或修改用户既有业务表数据；用户明确指定的 `DBTALK_DSN_TEST1` / `DemoBook.dbo.Books` 用于只读检查，写入测试仅使用连接临时表和唯一前缀自建表并清理。用户指定的当前 `.env` 条目已改为 DemoBook，不扩展修改其他配置或用户级 skill。用户后续报告全局CLI缺驱动，已按原editable来源同步依赖并复测；本轮Verification不再安装或升级依赖。
- 不扩展到多个 SQL Server schema 的自动发现与迁移；首版拟使用连接的默认 schema，与当前 Inspector 的默认范围一致。跨 schema 外键或不受支持的对象不能静默忽略。
- 不新增 dry-run 方言参数、自动重试、SQLCMD 全量兼容、分页读取框架或内存基准项目；不扩展发布平台或重构发布流水线。
- 不新增 `mssql_*.py` 产品模块或独立适配框架；按现有连接、脚本、格式、结构校验和传输职责整合，同类操作共用流程。

## User stories / scenarios / 用户故事 / 场景

- Agent 用已有 SQL Server DSN 通过 `query` 查询；需要写入时通过同一 DSN 的 `exec` 执行，实际权限由数据库决定。
- 用户执行含 T-SQL 批次的文件，先 dry-run 核对实际执行单元，再在写事务中执行。
- 用户通过 JSONL 在 SQL Server 与既有支持的数据库之间迁移既有表，预先核对目标和写入权限。
- 用户在受支持的 Python 平台通过 uv 安装并使用功能，无需另行配置系统 ODBC。

## Acceptance criteria / 验收标准

- A01：`pymssql` 通过项目依赖和锁文件安装；受支持平台的 wheel 与已有独立二进制交付方式得到验证，不要求系统 ODBC。
- A02：`mssql+pymssql` 支持同步 DSN 和既有 `--dsn-env` / 当前目录 `.env` 优先级；传输必须显式指定数据库，错误和日志不泄露凭据。
- A03：`query` 复用既有 SQLAlchemy 查询调用和结果读取，支持同一 DSN、绑定参数和既有输出，不提交查询连接的事务；不解析 SQL、不承诺应用层拒绝 DML/DDL，不要求只读账号。`exec` 复用既有写事务并提交。
- A04：`exec --sql`、参数、事务、失败回滚及单语句超时按已确认契约工作；`pymssql` 的进程级超时影响有明确边界。
- A05：`exec --file` 正确处理受支持的 T-SQL 批次和变量作用域；文件 dry-run 离线解析显式提供的 DSN，展示与执行一致的批次，不连接数据库；无 DSN 时保留通用预览，不能可靠处理的脚本明确失败。单 SQL dry-run 保持原样预览，不解析 DSN 或参数。
- A06：SQL Server 导出符合既有 JSONL 格式、表选择、最低类型范围、主外键顺序及错误处理契约；保持 `fetchmany(1000)` 分批与原子输出，不声称服务端流式或固定内存上限。
- A07：SQL Server 导入支持 `insert` 和完整主键 `upsert`，在写入前预检并按表提交或回滚；特殊列和不支持的类型不能静默丢失或截断。
- A08：README、数据库手册和仓库内通用 skill 说明 DSN、query / exec 行为、数据范围及已验证限制；不新增数据库管理 skill。
- A09：针对实际风险运行聚焦检查、项目质量检查和隔离数据库集成验证；现有三种数据库行为不回归，未验证项明确报告。
- A10：修改范围限于通用能力和必要交付支持；除用户指定测试 DSN 更新外，不改动其他数据库管理行为或配置。

## Workload assessment / 工作量评估

工作量较大：除同步连接、SQL 执行外，还包括有状态的 T-SQL 批次、跨方言 JSONL 类型和导入语义、打包及真实数据库验证。风险集中在 SQL Server query 的会话实现、超时全局状态、身份列、精度、外键以及大表缓冲。review 需要同时查看 DSN、执行会话、脚本解析、JSONL 协议和目标表事务，而不只是驱动声明。

结论：保留五个 task。通用连接、脚本、导出和导入各有可单独检查的交付；导出与导入共同遵循既有 JSONL 协议和总计划类型矩阵，导入可用既有制品独立验证，不以导出完成为前置。它们共享 CLI、类型和测试文件，由总计划协调串行写入与集成。

## Candidate tasks and dependencies / 候选子任务与依赖

- T01：同步连接、DSN、查询与单 SQL 执行；为后续操作提供可验证的连接和会话行为，归属 A01-A04。
- T02：T-SQL 文件批次与 dry-run，依赖 T01 的 SQL Server 方言及连接契约，归属 A05。
- T03：JSONL 导出、类型及表元数据，依赖 T01；确定可复用的 SQL Server JSONL 来源契约，归属 A06。
- T04：JSONL 导入与 upsert，依赖 T01，使用既有 JSONL 制品和总计划类型矩阵，归属 A07。
- T05：公开文档、既有打包验证、代表性跨引擎集成与总验收证据，依赖 T02、T03 和 T04，归属 A01、A08-A10。

## Open questions / 待定问题

- 无额外产品决策。按用户最新要求删除 SQL 文本防护和重复执行路径，主会话独立完成清理及必要回归。

## Decisions / 决策

- 驱动采用 `pymssql`，SQLAlchemy 使用 `mssql+pymssql`；不增加 `sqlserver://` 兼容别名或系统 ODBC 依赖。
- 用户已授权产品实施、DemoBook实测及全局CLI缺驱动修复；协作已按用户要求收回本会话。用户最新明确要求使用dev-check完成SpecFlow验证，现进入整体Verification；提交和发布未授权。
- 用户明确排除备份、账号权限等数据库专用管理功能；通用 skill 的 JSONL `export/import` 保留在本次目标中。
- 按既有默认 schema 范围规划；dry-run 复用显式 `--dsn` / `--dsn-env` 离线解析，不增加新入口。类型支持以总计划最低矩阵为准，不追求全类型迁移。
- SQL Server `query` 支持原始 SQL 和既有参数/输出，通过现有连接读取结果；`exec` 使用现有写事务。删除 SQLGlot 与专用查询拦截器，不引入函数、过程或 SQL 权限沙箱。文件执行与普通 exec 共用客户端，传输共用既有扫描、排序、回放和分批流程。

## Risks / assumptions / 风险 / 假设

- SQL Server 查询连接不自动提交，不代表数据库强制只读；实际 SQL 能力由数据库和连接权限决定，不新增文本防护来模拟权限。
- `pymssql` 的连接和语句超时设置可能影响同进程其他连接；同步 CLI 与公开 Python API 的并发约束需要分别说明和验证。
- 官方 wheel 的平台基线随版本变化；已查到 2.4.6 的 Python 3.12 macOS wheel 要求 macOS 15，而现有 arm64 发布 runner 为 macOS 14。选版和锁定须覆盖现有发布平台。
- 已在授权的 DemoBook 使用自建测试表核对驱动和迁移路径；SQL Server 原生时间精度、空二进制等发现须修复后重跑。其他操作系统尚未实测，不将 Windows 结果扩大为全平台通过。

## User review notes / 用户审查记录

- 2026-11-02 21:32：用户明确要求使用best-practices:dev-check完成specflow:specflow验证。检查、实库及产物复核已执行，详见[验证报告](../verification/20261102-sqlserver-generic-operations.md)。SQL Server功能检查通过，A01仍有wheel默认配置与未测平台缺口，不将验证完成等同于全项通过。

- 2026-11-02 21:01：用户进一步指出按引擎堆放 `mssql_*.py` 不符合项目结构。修订7将必要差异归入既有职责，移除查询拦截器及全部新增 SQL Server 产品模块；保留实际驱动、类型、identity 和事务差异。当前主会话负责全部清理和回归，不再派发。

- 2026-11-02 20:40：用户指出 SQL 语法防护和抽象偏离意图，要求不再分配其他会话，由本会话清理。当前方案修订6据此撤销应用层拒写要求，统一既有 SQLAlchemy 调用与传输流程；以下早期记录保留为历史，不作为当前实现依据。

- 2026-11-02：用户要求使用 SpecFlow 记录 SQL Server 通用操作任务并开始计划；因此接受上述意图范围并进入 Plan。具体只读保障和默认 schema 前提保留为计划审查事项。
- 2026-11-02：用户要求更新和收缩任务；已收敛 DSN 预览入口、最低类型范围、任务依赖和验证规模。仅只读契约仍需明确决策；本次更新不代表接受该契约或授权实施。
- 2026-11-02：用户明确 query 能力与 MySQL / PostgreSQL 保持一致；撤销额外只读账号前提及产品决策，具体实现和实库验证保留为技术工作，本次不授权实施。
- 2026-11-02：用户要求解决实测中 query 一律拒绝的问题并完成能力；采用明确限定的 SQL Server 应用层单 SELECT 检查，正常查询及写入拦截聚焦检查通过，具体实现差异写入文档，不宣称数据库强制只读。
