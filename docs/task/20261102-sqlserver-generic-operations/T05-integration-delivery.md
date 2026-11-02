# Task / 子任务 T05：集成与交付
最后修改时间: 2026-11-02 21:32:53

## Review status / 审查状态

Accepted

## Parent and authority / 父任务与权威记录

- 总意图：[意图](../../intent/20261102-sqlserver-generic-operations.md)。
- 权威总计划：[计划](../../plan/20261102-sqlserver-generic-operations.md)；任务 ID：T05；方案修订：7。
- 总计划负责分配、依赖版本、交付审查与整体阶段授权。

## Handoff basis / 交接依据

- 主会话沿用 `T05-20261102-01`，方案修订7，基线为当前权威工作树T01-T04清理后accepted代码；用户实施及指定DemoBook实测授权沿用。
- 主会话直接记录和审查接收、进展与交付事实，不再派发。

## Goal and acceptance / 目标与验收

交付与实际实现一致的通用文档和 skill，并审查项目既有 wheel、独立二进制及跨引擎检查，覆盖 A01、A08-A10；任何未验证的平台或限制明确报告。

## Inputs and eligibility / 输入与可开始条件

T02、T03 和 T04 的交付须均经主会话 accepted，代码、前置版本和测试证据可访问；有已接受方案、实施授权及有效分配。

## Scope and files / 范围与文件

- README 中英文、`docs/database.md`、`plugins/dbtalk/skills/dbtalk/SKILL.md`；按真实构建需求调整 Makefile / 发布构建配置及必要集成测试。
- 不新增SQL Server备份、账号权限管理skill，不同步用户级plugin、不发布或提交。用户最新报告全局命令缺驱动，已按原editable来源同步当前CLI依赖并原样复测；该修复不改变安装来源或连接配置。
- 不重构发布流水线、扩展平台 / 容器交付或增加性能基准项目。

## Technical decisions / 局部技术设计

- 文档只描述已经验证的 DSN、query / exec 会话行为、脚本、类型、默认 schema、超时和内存行为；不要求独立只读账号，保留清晰的敏感信息保护。
- 使用现有 uv、wheel 和 PyInstaller 构建入口；验证原生扩展与 SQLAlchemy 动态方言在冻结产物中可加载，不以 `--help` 代替驱动验证。
- 实际只验证可用平台；其余平台依靠实际 runner 结果或记录未验证，不擅自宣称全部兼容。

## Implementation outline / 实施安排

1. 核对 T02/T03/T04 与上游交付版本、共同类型矩阵、未完成项及完整意图验收覆盖。
2. 同步 README、数据库手册和通用 skill，必要时补齐真实构建所需的最小打包调整。
3. 运行聚焦与项目检查，验证可用平台 wheel、二进制和隔离 SQL Server；汇报证据与剩余风险。

## Checks and delivery / 检查与交付

- 项目已有 `make check`、`make test`、`uv lock --check`；交付相关构建使用 `make release` / `make binary`。
- 汇合 T03/T04 制品验证真实 SQL Server 自身及与 SQLite 的代表性往返；MySQL/PostgreSQL 运行既有回归，有现成实例时补充真实跨引擎检查。核对凭据脱敏与范围，不新增全六向实库矩阵要求。
- 交付实际文件清单、版本、命令结果、未运行原因和局部审查证据；用户最新已授权整体Verification，完整结果见同名验证报告。

## Risks / 风险

- 无真实 SQL Server 或多平台 runner 时，只能如实报告相应验证缺口；可加载驱动不等同于已验证数据读写。

## Reception and progress / 接收与进展

- 2026-11-02 20:26:27：主会话核对总计划有效分配及前置交付后接收；权威根与代码根为 D:/SourceCodes/mywork/dbtalk，基线 HEAD 5708aff64c10d6c0cd3e5f947b5b0ba54a20ab8a 加当前工作树，无提交。T01/T02返工与T03/T04实库修正已审查，23项实库检查通过；现有Books仍100行，自建表清理检查为0。仅收尾文档、skill、既有构建和开发检查，保留用户暂存内容，不安装、发布、改管理功能或进入独立Verification阶段。

## Delivery and review / 交付与审查

2026-11-02 21:32：用户授权整体Verification后，主会话按dev-check复核当前暂存代码和21:23产物。Ruff/format/Mypy/锁检查通过，默认587 passed、33 skipped、5 subtests passed；SQL Server19 passed；二进制1007行gzip测试1 passed。wheel内43个Python文件逐字节匹配源码；无默认配置启动报mysql.client_image，显式提供必填配置后查询DemoBook/100。用户原目录已安装CLI的select 1仍返回1；Books100行、测试表残留0。本轮不修代码、不安装、不改暂存；[整体报告](../../verification/20261102-sqlserver-generic-operations.md)将A01记录为部分通过，任务的集成交付accepted不代表所有平台或默认wheel运行通过。以下为实施期间历史证据。

2026-11-02 21:12:07：主会话交付并复核接受当前修订7工作树，前置为同一工作树T01-T04清理后的accepted结果，无提交。早期23项实库记录属于历史版本，最终结果以下列证据为准。

- 结构：删除全部新增mssql产品模块和SQLGlot，查询复用既有SQLAlchemy连接，脚本仅在operations分批；传输共用元数据入口、表头预检、扫描、选表、外键排序、回放与参数分批。仅保留真实驱动/类型/identity/upsert差异，未引入框架、管理功能或查询防护。
- 文档：README中英文、docs/database.md和仓库通用skill均按最终行为更新；记录同步pymssql、GO、默认schema、JSONL矩阵、逐表事务、进程级超时及驱动缓冲限制，删除单SELECT说法。SpecFlow当前技术方案修订7，旧记录明确标识为历史。
- 静态：`uv run --locked --no-sync ruff check src tests scripts`、`ruff format --check src tests scripts`、`mypy src tests scripts`全部通过（Mypy 82 source files）；`uv lock --check`、`git diff --check`通过。
- 回归：`uv run --locked --no-sync pytest -q --tb=short`：587 passed、33 skipped、5 subtests passed。默认跳过包含19项SQL Server实库及14项其他外部数据库测试，不能算作已实测。
- 实库：显式设置`DBTALK_MSSQL_TEST_DSN_ENV=DBTALK_DSN_TEST1`及`DBTALK_MSSQL_TRANSFER_TEST_WRITE=1`后，运行两个mssql integration文件：19 passed。SQL Server 2016，验证查询/exec/参数/超时/不提交/GO、SQLite与SQL Server支持字段往返、identity/复合PK/并发upsert、预检和回滚、1007行gzip；Books未改，最终100行，自建测试表残留0。
- 构建：`uv build`产出`dist/pomelo_dbtalk-0.41.2-py3-none-any.whl`及sdist。按Makefile参数用PyInstaller加`--hidden-import pymssql`生成`dist/dbtalk-sqlserver-smoke.exe`，实际query返回DemoBook/100；设置`DBTALK_MSSQL_TEST_BINARY`重跑1007行gzip导入导出：1 passed。Linux/macOS未实际运行；构建中的uv-build版本及可选hidden import警告未扩展处理。
- wheel独立环境：原样CLI启动暴露既有默认配置未打包问题（settings.py与HEAD相同）：先后缺mysql.client_image、database.query_timeout_seconds。通过进程变量提供仓库已有mysql/postgres client_image、query/exec timeout及session poll interval后，隔离wheel的实际query返回DemoBook/100。该问题属于已有通用wheel配置，未修改无关配置加载逻辑；不宣称wheel开箱运行已通过。
- 用户全局CLI故障：`dbtalk -v query --dsn-env DBTALK_DSN_TEST_RDC_APP --sql 'select 1'`确认ModuleNotFoundError/pymssql。当前工具receipt为本仓库editable安装，旧metadata无pymssql；按原来源执行`uv tool install --force --editable D:/SourceCodes/mywork/dbtalk`后，pymssql为2.4.5，metadata声明正常。用户目录内原命令返回1。仅修复原有工具安装的依赖，未改DSN/目标.env/数据库数据；README补充editable依赖同步说明。
- 剩余限制：JSONL的p7/offset/generated等按当前支持矩阵拒绝；其他平台和真实MySQL/PostgreSQL迁移未实测，默认回归通过；既有wheel配置缺失如上。SQL Server通用能力和本次结构清理无待实现项，未进入额外整体Verification或发布。
