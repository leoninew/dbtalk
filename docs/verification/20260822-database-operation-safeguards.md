# 数据库 query/exec 安全边界验证
最后修改时间: 2026-08-22 22:01:50

Review status: Accepted

## Requirement alignment

已按 `docs/requirement/20260822-database-operation-safeguards.md` 验证：

- `query` 和 `exec` 均支持正整数 `--timeout/-t`，未指定时读取 `database.operation_timeout_seconds`。
- `query` 和不带 `--write/-w` 的 `exec` 在只读会话中执行；带 `--write/-w` 的 `exec` 切换到写会话。
- 实现通过会话权限而非 SQL AST、DML 关键词或 allowlist 判断写入意图。
- SQLite 长语句的超时中断、只读拒绝写入、写会话执行更新以及配置覆盖均有自动化测试。

## Spec and plan alignment

轻量模式未创建独立 spec，按 requirement 核对。既有计划文档 `docs/plan/20260820-unified-dsn-database-operations.md` 已同步为最终命令语义：`exec` 默认只读，`--write/-w` 切换到写会话，超时默认值来自配置。

## Actual diff summary

- `src/dbtalk/database/cli.py`：增加 timeout 参数与写会话开关，解析配置默认值。
- `src/dbtalk/database/connection.py`、`operations.py`：实现只读会话、语句超时和写会话切换。
- `src/dbtalk/settings.py`、`dbtalk.yaml`、`.env.example`：增加 `database.operation_timeout_seconds` 配置与环境变量覆盖。
- `tests/`：覆盖配置、CLI 授权、SQLite 只读拒绝和超时中断。
- `docs/database.md`、Codex Skill 及计划文档：同步命令和安全约束。

## Expected vs actual files

需求预期修改 CLI、连接层、设置、测试、手册和 Skill；实际修改均在该范围内。额外更新既有计划文档，用于纠正其中与最终会话授权语义不一致的说明。

## Acceptance checklist

- [x] `query`、`exec` 提供 `--timeout/-t`，且显式值优先于配置。
- [x] 无 `--write/-w` 的 `exec` 可执行只读 SQL，写入由数据库只读会话拒绝。
- [x] `exec --write/-w` 可执行写操作。
- [x] `query` 在 SQLite 中不能写入，未引入 SQL 分类逻辑。
- [x] SQLite 查询超时后返回稳定的超时错误并清理会话状态。
- [x] CLI 手册、Skill、设置示例与实现一致。

## Test results

| Command | Result |
| --- | --- |
| `uv run pytest` | 99 passed, 1 skipped；总覆盖率 91.3%，达到 90% 阈值。 |
| `uv run ruff format --check .` | 63 files already formatted。 |
| `uv run ruff check .` | Passed。 |
| `uv run mypy src tests` | 44 source files，无类型问题。 |
| `uv run dbtalk --help` | Passed。 |
| `uv run dbtalk database query --help` | 显示 `--timeout/-t` 及配置默认值来源。 |
| `uv run dbtalk database exec --help` | 显示只读默认和 `--write/-w` 写会话语义。 |
| `git diff --check` / `git diff --cached --check` | Passed。 |

## SQLite backup validation

使用 `dbtalk database query` 在只读会话中验证 `D:\SourceCodes\mywork\pomelo-orbit\data\db\pomelo-orbit.db.pre-jsonl-20260819-210804.bak`：

- schema 元数据查询返回 46 张表，`schema_version` 为 169。
- `PRAGMA integrity_check` 返回 `ok`。
- 操作前后的 SHA-256 均为 `4F29B897494F49875D2D1CB1B5D1D5B0B02EEA19C21516B8098524AEB68168E1`，确认验证未修改备份文件。

## Scope and risks

- 项目中需要真实外部服务的集成测试按默认约定跳过：`tests/test_manual_integration.py` 需要设置 `DBTALK_RUN_INTEGRATION=1`。未对真实 MySQL、PostgreSQL 服务执行超时验证。
- MySQL 写入超时后，非事务性语句和隐式提交 DDL 仍遵循 MySQL 原生语义，不能承诺完整回滚。
- 当前暂存区与工作区在两份过程文档上存在差异：计划文档的最终语义修正和本需求文档的时间戳/决策更新尚未暂存。自动检查基于工作区最新内容执行，未修改任何 Git 暂存状态。

## Incomplete items

无阻塞项。真实 MySQL/PostgreSQL 服务集成验证仍可在具备凭据和测试环境时补充。

## Conclusion

本地自动验证通过，需求验收标准已满足；保留外部数据库集成验证与 MySQL 原生事务语义风险。
