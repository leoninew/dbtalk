# Verification / 验证：PostgreSQL 与 MySQL 会话查看和终止
最后修改时间: 2026-10-15 12:17:19

## Review status / 审查状态

Accepted

用户已确认“验证有效”，并要求完成本报告。本文结合用户确认、当前代码及文档核对、实施阶段隔离实验和本轮 Cygwin 质量检查形成验收结论；不将用户确认扩展为未说明环境或场景的实测证据。

后续测试清理删除了通过替换内部 `_read` 制造切库时机的重复集成用例，切库保护仍由 MySQL 单元测试覆盖，原生会话集成现为两种引擎各两项。下方保留验收时的历史命令、数量与证据；当前结果见[测试清理记录](20261015-test-suite-cleanup.md)。

## Intent alignment / 意图对齐

依据已接受的 [Intent](../intent/20261015-session-management.md) A01–A09，两种引擎均提供独立 `session list`、`session kill`，按 `--database > DSN database > 失败` 解析目标。显式参数同时覆盖连接库，按 ID 终止仍受数据库范围约束；不允许遗漏数据库后扩大到实例范围。

终止必须提供 `--yes`，可选 `--wait SECONDS` 使用整批共享的退出确认预算。结果区分请求成功、退出确认、自然消失、身份变化、失败、超时和未尝试，保留部分成功。轮询复用集中配置体系，日志仅由根命令 verbose 开启。不提供 cancel、自动提权、自动重连重发或追杀新连接的行为。

## Plan alignment / 计划对齐

依据已接受的 [Plan](../plan/20261015-session-management.md)，六个实施步骤均已完成：集中配置与公共契约、PostgreSQL 适配、MySQL 适配、CLI / 结果 / 日志、文档与开发检查，以及实施交付记录。

PostgreSQL 从 pg_stat_activity 读取普通客户端，以 PID / backend_start 及数据库、用户复核，受约束调用 pg_terminate_backend。MySQL 使用 performance_schema.threads，连接 ID 用于 KILL，THREAD_ID 用于身份复核；等待阶段不再按默认数据库过滤原身份。连接采用 AUTOCOMMIT 并关闭、dispose，未修改 DatabaseClient 公开接口或 schema drop 行为。

两项既有组织调整可接受：公共模块同时承载窄范围的连接、超时与错误编码辅助；日志新增覆盖集中在 test_sessions.py，未修改原定的 test_cli_logging.py，后者原有测试仍通过。用户在验证阶段报告的质量入口失败已定位到迁移后遗留的虚拟环境启动器，按用户要求重建 .venv 并保留原始 Makefile，见范围差异。

## Task deliveries and integration / 子任务交付与集成

不适用。本功能始终为单任务，主会话顺序实现和核对，未创建 task、分配记录或子 agent。

实施验收基线为 `d8e21d9` 加当时的工作区实际文件，核对包含用户已暂存的功能变更。功能现已位于提交 `3ba5b9b`；本轮仅补充环境重建、检查结果及备份清理记录。Makefile 与实施基线一致，重建的本地 .venv 不进入 Git diff；agent 未执行暂存、提交或发布。

## Actual diff summary / 实际差异摘要

- 新增公共 sessions 模块：目标解析、输入校验、连接生命周期、超时、结果模型、共享等待预算及表格输出。
- 新增 PostgreSQL / MySQL session 模块并注册命令，完成客户端筛选、身份复核、原生终止、批量部分失败和退出确认。
- settings.py / dbtalk.yaml 增加必需的 SessionConfig / session.poll_interval_seconds，并同步完整配置 fixture；日志白名单增加 database、session_id、wait_seconds。
- 新增三份聚焦测试和一份 opt-in 集成测试，同步双语 README、两份引擎手册、日志说明及仓库内两份方言 skill。
- 附加修复本地开发环境：恢复原始 Makefile 并重建 .venv，使现有 mypy / pytest 可执行入口使用当前目录的解释器；无 Makefile 或锁文件变更。
- 同名 Intent / Plan 更新阶段与用户确认，本报告记录整体验收；用户追加要求的两份 Obsidian 常用操作文档已另行更新。

## Expected vs actual changed files / 预期与实际修改文件对比

| 计划边界 | 实际文件 | 核对结果 |
| --- | --- | --- |
| 集中配置及 fixture | dbtalk.yaml、src/dbtalk/settings.py、tests/test_settings.py、tests/test_backup_db.py、tests/test_unit_boundaries.py | 对齐，必需配置无业务默认值兜底。 |
| 公共辅助 | 新增 src/dbtalk/database/sessions.py | 扩展包含连接、超时和安全错误编码，仍为窄范围函数。 |
| 两种方言 | 新增 postgres/session.py、mysql/session.py；修改对应 cli.py | 对齐，原生 SQL 和身份规则留在各方言。 |
| CLI 日志 | src/dbtalk/cli_runtime.py | 对齐，复用根 verbose 与生命周期记录。 |
| 行为及集成测试 | 新增 tests/test_sessions.py、test_postgres_sessions.py、test_mysql_sessions.py、test_session_integration.py | 对齐；日志新增检查放在 test_sessions.py，既有 test_cli_logging.py 未修改。 |
| 用户与 Agent 文档 | README.md、README.zh-CN.md、docs/postgres.md、docs/mysql.md、docs/logging.md、两份仓库内方言 SKILL.md | 对齐，新增示例直接声明变量。 |
| SpecFlow 过程 | 同名 Intent、Plan、Verification | 对齐，无 task。 |
| 开发环境修复 | 重建 Git 忽略的本地 .venv | 用户明确授权；Makefile、uv.lock 及工具版本均保留原样。 |
| 外部常用操作文档 | Obsidian 下 PostgreSQL常用操作.md、MySQL常用操作.md | 用户后续明确授权；不属于 Git diff，详情见下文。 |

## Acceptance criteria checklist / 验收标准检查清单

| 验收项 | 实际证据 | 结果 |
| --- | --- | --- |
| A01：两种引擎命令及四种数据库来源组合 | test_sessions.py 目标解析 / CLI 覆盖，四个 help；原生实验以维护库 DSN 显式选择业务库。 | 通过。 |
| A02：缺失或无效目标在连接前失败，ID 不绕过数据库 | CLI 缺失目标测试断言未创建 engine；公共解析拒绝空白 / 控制字符、错误方言及异步 DSN。 | 通过。 |
| A03：限定范围的客户端列表，含空闲及基本字段 | 两种引擎原生实验同时列出活跃 / 空闲连接，验证数据库、稳定排序、自身排除和空列表；SQL 与权限说明已核对。 | 通过。 |
| A04：数据库范围、ID、自身 / 系统排除及执行前复核 | 原生跨库 ID 拒绝且哨兵连接仍可查询；单元覆盖自身、身份变化及 MySQL 系统分类变化；MySQL 原生切库实验保留已变更连接。 | 通过；未实际终止系统 / 复制进程。 |
| A05：必需确认、原生权限、自然退出与部分失败 | 无 yes 不连接；两引擎受限账号终止自身成功、他人失败，授予原生终止权限后成功；单元覆盖 gone、连接失效停止发送、汇总和退出码。 | 通过。 |
| A06：可选 wait、一次总预算、真实退出、超时及不追杀新连接 | 原生单 ID / 批量 wait 确认真实断连及未提交插入回滚；受控时钟覆盖总预算、间隔、超时、晚到查询、复用 ID / 不可见身份，MySQL 确认查询不以默认库过滤。 | 通过；真实网络硬截止不在承诺范围。 |
| A07：根 verbose 日志与 stdout / 凭据边界 | test_sessions.py 默认 / verbose、database / session_id / wait_seconds、部分失败和敏感值检查；既有 test_cli_logging.py 通过；输出代码复核。 | 通过。 |
| A08：无 cancel、文档及 skill 对齐、检查和隔离实验 | CLI 拒绝 cancel / 子命令 v；仓库及外部文档核对，四个 help 和本报告分列的检查 / 原生实验。 | 通过。 |
| A09：集中配置、YAML / 环境覆盖及既有超时复用 | typed 配置校验拒绝缺失 / 非法值，YAML 与环境覆盖测试通过；受控轮询及连接预算测试，代码核对无轮询备用默认值。 | 通过。 |

## Test / command results / 测试 / 命令结果

项目为 Python 3.12 / Click / SQLAlchemy，现有质量入口为 Makefile 的 check/test，工具为 Ruff、Mypy、pytest，依赖通过 uv 锁定。本轮使用用户提供的 Cygwin 启动入口实际执行标准 target：

```powershell
& 'D:/ProgramFiles/Cygwin64/Cygwin.bat' 'D:/SourceCodes/mywork/dbtalk' -c 'make check && make test'
```

| 本轮命令 / 操作 | 实际结果 |
| --- | --- |
| 重建前 `uv run --locked --no-sync mypy --version` / `pytest --version` | 均复现 `uv trampoline failed to canonicalize script path`；启动器失败，未进入工具检查。 |
| 启动器路径核对 | 旧 mypy.exe / pytest.exe 内嵌 D:\SourceCodes\mywork\pomelo-dbtalk\.venv\Scripts\python.exe，文件不存在；重建后均引用当前 dbtalk 目录的有效解释器。 |
| `uv sync --locked --all-groups --python <原基础解释器> --no-python-downloads --offline` | 使用本机 Python 3.12.10 和 uv 缓存重新安装 41 个包，11.17 秒；不下载解释器，不访问网络。 |
| 环境 / 依赖一致性 | uv.lock 的 SHA-256 与重建前一致，41 个包的名称及版本完全一致；Makefile 与 HEAD 无 diff。 |
| 重建后直接启动器检查 | `uv run --locked --no-sync mypy --version`、`pytest --version`、`dbtalk --version` 均成功：2.3.1、9.1.1、0.35.0。 |
| 修复后 `make check` 的 Ruff format | 通过，74 files already formatted。 |
| 修复后 `make check` 的 Ruff check | 通过，All checks passed。 |
| 修复后 `make check` 的 Mypy | 通过，75 source files 无问题。 |
| 修复后 `make test` | 476 项收集，460 passed、16 skipped，51.06 秒；target 退出码 0。 |
| 旧环境清理 | 按用户要求删除旧 .venv 备份；仓库原备份路径和临时移动路径均已不存在，当前 .venv 保留。 |
| `git diff HEAD --check`、`git diff --cached --check` | 通过，覆盖当前工作区与原有暂存内容。 |
| 同名三份过程文档及链接核对 | 状态、阶段、模板章节和本地引用一致。 |

根因是原项目目录 pomelo-dbtalk 改为 dbtalk 后沿用 .venv，Windows 启动器内嵌的绝对解释器路径失效。先前临时改用 python -m 能绕过旧启动器，但用户明确要求修复环境。最终已撤回 Makefile 修改，在当前路径按原解释器和锁文件重建环境；保留原始工具入口、UV_RUN、fix、cov 及检查范围，不升级依赖。

16 个跳过项为既有批量传输 1 项、owner 集成 9 项及新增 session 集成 6 项，均因未提供独立端点而跳过；不将跳过等同数据库行为验证。本轮未重新创建测试数据库或容器，使用本会话实施阶段已完成的独立原生证据：

| 实施阶段证据 | 实际结果与边界 |
| --- | --- |
| 四个 session 子命令 help | 均退出 0；kill 显示 wait SECONDS，无 cancel 或子命令 verbose。 |
| `python -m pytest tests/test_session_integration.py -q --tb=short`，两个 session 测试 DSN 指向独立端点 | 5 passed、1 skipped，2.95 秒；跳过的是 PostgreSQL 参数下不适用的 MySQL 切库实验。 |
| 原生测试实例 | 本机已有 postgres:18-alpine、mysql:8.0.39，随机 localhost 端口、随机测试凭据及 tmpfs；临时对象和容器均已清理。 |
| 覆盖行为 | 活跃 / 空闲列表、覆盖 DSN、跨库保护、单 ID / 批量退出、真实断连、事务回滚、空结果、受限账号部分失败及原生权限授予后成功；MySQL 额外覆盖发送前切库。 |
| 用户操作确认 | 用户明确表示“验证有效”；未提供独立环境、命令或输出，故不据此新增具体场景结论。 |

原生实验细节及首次 PostgreSQL 初始化超时后的隔离启动安排保留在 Plan；环境修复只重建本地 .venv、恢复 Makefile 并更新过程记录，未修改会话功能实现。

## Missed or expanded scope / 遗漏或扩大的范围

无已确认的功能性遗漏。公共连接 / 超时辅助和日志测试位置调整不改变契约，已说明于计划对齐。

用户追加的外部说明位于 `D:\Documents\documents\Obsidian\解决方案\dbtalk\PostgreSQL常用操作.md`、同目录 `MySQL常用操作.md`：增加 session list、按 ID / 批量 kill、可选 wait、权限与配置语义，沿用文件内变量声明。编辑前后原有 export 声明的 SHA-256 校验值一致，包含 DSN、密码和原有变量；六段新增命令通过 Click 参数解析核对，未为文档检查执行数据库操作。

本地环境重建来自用户报告的实际启动失败及随后明确授权。重建使用既有 Python 和锁定依赖；检查通过后，按用户“不用备份”的要求已删除旧环境备份，仅保留新 .venv。最终无 Makefile、锁文件或包版本变更，不扩大业务功能或引入兼容分支。报告保留完整功能验收证据；本轮未执行暂存、提交、安装用户级 CLI、同步插件或发布。

## Risks / 风险

- MySQL 默认数据库不代表连接的完整访问范围，列表为空或批量成功不能证明目标库无访问者或锁持有者；Performance Schema 的 SELECT 可见性及原生终止权限仍适用。
- 目标库必须存在，账号必须能连接；身份关键字段不可见时拒绝盲目终止。新必需配置 session.poll_interval_seconds 须由自定义完整配置提供。
- 原生终止不可整体回滚；连接失效时请求结果可能不明。发送前复核降低竞争风险，但 MySQL KILL 仅接受连接 ID，不能完全消除复核与发送间的竞争。
- wait 只限制退出确认阶段，受已有阻塞 I/O、驱动及服务端精度限制，不保证墙钟硬中断，也不保证连接池不重连或数据库持续无连接。
- 原生实测基线为 PostgreSQL 18 和 MySQL 8.0.39；其他版本、实际复制 / 系统进程及生产网络故障未另作实验。

## Incomplete items / 未完成事项

本次约定范围无阻塞性未完成事项或检查失败。默认不启用的外部端点测试及上述边界已明确，不列为未完成产品需求；未启动额外安装、发布或业务数据库操作。

## Conclusion / 结论

整体验证通过，状态为 Accepted。Intent A01–A09 与当前交付对齐，用户已确认功能有效；标准 Cygwin make check/test 的启动问题已修复并通过，实施阶段的两种原生数据库实验为真实退出与权限行为提供独立证据。剩余限制已在手册、skill、外部常用操作文档和本报告中明确，无待用户决定的验收阻塞项。
