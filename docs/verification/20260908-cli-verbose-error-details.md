---
Review status: Accepted
Flow mode: standard
Stage: Verification
---

# 命令级 verbose 与 exec 写会话验证
最后修改时间: 2026-09-08 13:41:53

## Requirement alignment / 需求对齐

已对照已接受的 `docs/requirement/20260908-cli-verbose-error-details.md` 核对：

- 根 `dbtalk` group 已删除 `-v/--verbose`。`dbtalk --help` 不列出该选项；`dbtalk -v exec ...` 以未知选项退出。
- 叶子命令通过 `DbtalkCommand` 注入 `-v/--verbose`；中间 group 使用 `DbtalkGroup`，帮助中不出现 `-v`。
- 命令行 `-v` 与 `settings.verbose` / `DBTALK_VERBOSE` 合并为同一次调用的 verbose 开关。
- 默认错误仍是稳定阶段文案，例如 `database execution failed`；verbose 在同一条 `Error:` 后追加清洗后的异常类型与消息，不把某一种数据库错误写成固定文案，也不输出完整 traceback。
- `exec` 固定写会话，已删除 `--write` / `-w` 和 Python API `allow_write`。`query` 仍走只读会话。
- `docs/database.md`、README 本任务相关段落和 `plugins/dbtalk/skills/dbtalk/SKILL.md` 已同步：叶子命令 `-v` 查看失败细节；`exec` 示例不再出现 `--write`。

## Spec alignment / 规格对齐

不适用。本次为 standard / 标准模式，未创建独立 Spec。

## Plan alignment / 计划对齐

已对照已接受的 `docs/plan/20260908-cli-verbose-error-details.md` 核对：

- 错误清洗与格式化落在 `src/dbtalk/database/models.py`：`sanitize_error_detail`、`driver_error_detail`、`format_cli_error`；verbose 细节使用 `traceback.format_exception_only`，并剥离 `[SQL:]`、`[parameters:]` 与 SQLAlchemy background 链接。
- `src/dbtalk/mysql/client.py` 的 `sanitize_error_message` 转调共享实现，并覆盖 PostgreSQL `PGPASSWORD=` 脱敏。
- 新增 `src/dbtalk/cli_runtime.py`：叶子命令弹出 `verbose` 后再 invoke，避免把 `-v` 传给业务回调；`format_cli_error` 在 `invoke()` 内导入，避开循环依赖。
- 根命令删除 `-v`；`database` / `mysql` / `postgres` 及 nested group 使用 `DbtalkGroup`；独立 `grant` / `revoke` 使用 `DbtalkCommand`。
- `execute_from_dsn` / `execute_from_environment` 删除 `allow_write`，内部固定 `read_only=False`。
- 帮助、query/exec 写边界、verbose 失败细节与配置开关测试已落地。`tests/test_mysql_logging.py` 未改：共享 sanitizer 的默认脱敏仍由既有测试覆盖，verbose 路径由新增 CLI 测试覆盖。

## Actual diff summary / 实际差异摘要

- 新增共享 Click runtime：叶子命令自动带 `-v`，失败时按 verbose 重写 Click 错误文本，并在首次开启时把日志升到 DEBUG。
- 共享错误清洗：异常类型 + 消息；剥离 SQLAlchemy SQL/参数噪声；脱敏 `password=` / `mysql_pwd=` / `PGPASSWORD=` 与 DSN userinfo。
- `exec` 成为写命令：CLI 与 Python API 都不再接受写开关。
- 测试覆盖根命令无 `-v`、叶子命令有 `-v`、`exec --write`/`-w` 为未知选项、默认失败不含驱动细节、verbose 含 `OperationalError` 且不含 `secret` / `[parameters:]`、`DBTALK_VERBOSE=true` 等价开启。
- 活文档与 skill 去掉 `exec --write`，并说明叶子命令 `-v`。

## Expected vs actual changed files / 预期与实际修改文件对比

Plan 预期的源码、活文档和 skill 均已修改。实际额外文件：

- `src/dbtalk/cli_runtime.py`：Plan 已列为新增，实际已落地。
- `tests/test_cli.py`：Plan 把帮助与 sanitizer 断言写在 `tests/test_database_operations.py`；实现把帮助、错误格式化和 DEBUG 日志测试放在 CLI 测试，属于计划内测试职责拆分，不是产品范围扩大。
- SpecFlow 过程文档 `docs/requirement|plan|verification/20260908-cli-verbose-error-details.md`：实现与验证记录，不改变产品行为。

Plan 可选修改 `tests/test_mysql_logging.py` 未发生，共享 sanitizer 转调后既有 dump/restore 脱敏断言仍适用。

以下工作区改动不属于本任务，未纳入本 Verification 的通过结论：

- `Makefile`、`docs/codex.md`、`pyproject.toml`、`uv.lock`、`src/dbtalk/__init__.py`
- `scripts/version_calc.py` / `scripts/version-calc.py`、`tests/test_version_calc.py`
- `.github/workflows/release-binaries.yml`
- `docs/requirement|plan/20260908-pypi-publish.md`
- `README.md` / `README.zh-CN.md` 中的 `make pypi` 文档 hunk

## Acceptance criteria checklist / 验收标准检查清单

- [x] 根命令无 `-v`：`dbtalk --help` 不列出 `-v/--verbose`；`dbtalk -v exec --help` 退出码 2，错误为 `No such option '-v'`。
- [x] 中间 group 无 `-v`：`dbtalk mysql --help` 不列出 `-v/--verbose`。
- [x] 叶子命令有 `-v`：`dbtalk exec --help`、`dbtalk mysql dump --help` 均列出 `-v, --verbose`。
- [x] `exec --help` 不再出现 `--write` / `-w`。
- [x] 默认失败只保留阶段错误；verbose 追加 `ExceptionType: message`，不把 `orig` 抽成特定文案，不含 traceback、`[SQL:]`、`[parameters:]` 和口令明文。
- [x] 命令行 `-v` 或 `DBTALK_VERBOSE=true` 均可开启详细错误；叶子 `-v` 会把 logger 升到 DEBUG。
- [x] `exec` 无额外开关即可 UPDATE；`query` 的 UPDATE 仍失败且默认输出不含 `attempt to write a readonly database`。
- [x] 不为已删除的根 `-v` 或 `exec --write` 提供别名、警告或双路径。
- [x] 活文档与 skill 与源码语义一致；本任务相关段落不再示范 `exec --write` 或 `dbtalk -v`。

## Test / command results / 测试 / 命令结果

工作目录：`D:\SourceCodes\mywork\pomelo-dbtalk`

当前 Windows 环境没有可用的 `make`，按 Makefile 目标等价执行：

- `uv run --locked --no-sync ruff format --check src tests scripts`：通过，58 files already formatted。
- `uv run --locked --no-sync ruff check src tests scripts`：通过，All checks passed。
- `uv run --locked --no-sync mypy src tests scripts`：通过，no issues found in 59 source files。
- `uv run --locked --no-sync pytest -q`：通过，`240 passed, 5 subtests passed in 2.47s`。
- `uv run --locked --no-sync dbtalk --help`：无 `-v/--verbose`。
- `uv run --locked --no-sync dbtalk exec --help`：有 `-v/--verbose`，无 `--write`。
- `uv run --locked --no-sync dbtalk mysql --help`：无 `-v/--verbose`。
- `uv run --locked --no-sync dbtalk mysql dump --help`：有 `-v/--verbose`。
- `uv run --locked --no-sync dbtalk -v exec --help`：退出码 2，`Error: No such option '-v'.`
- `git diff --check`（本任务源码、测试、database 文档与 skill）：通过。

未连真实 ORBIT/MySQL/PostgreSQL 做集成写入，符合 Plan。

## Missed or expanded scope / 遗漏或扩大的范围

- 没有扩大产品范围。测试文件从 Plan 中的 `test_database_operations.py` 拆出一部分到 `test_cli.py`，覆盖帮助、sanitizer 和 DEBUG 日志。
- README 双语文件同时含有无关的 PyPI 发布说明，验证结论只覆盖 `--write` 删除与叶子命令 `-v` 说明。
- 未把 verbose 设为 Agent 默认必传，也未给 mysqldump/pg_dump 让出 `-v`。

## Risks / 风险

- `exec` 现在直接写库；误用会修改数据。只读必须走 `query`。这是需求内的有意收敛。
- verbose 仍可能暴露表名、库名、账号名、SQLSTATE；默认模式继续隐藏驱动细节。
- 清洗依赖字符串规则。若驱动把口令放进非 `password=` / `mysql_pwd=` / `PGPASSWORD=` / DSN userinfo 形态，verbose 仍可能泄漏；测试已覆盖当前合同中的这几类。

## Incomplete items / 未完成事项

无。本任务产品合同已实现，项目检查与相关测试已通过。提交时必须把 PyPI 发布相关文件留在工作区，不与本任务混提。

## Conclusion / 结论

Requirement 与 Plan 的验收项已通过：根命令无 `-v`，叶子命令有 `-v`，默认错误保持阶段文案，verbose 追加清洗后的异常类型与消息，`exec` 固定写会话。项目质量检查、全量单元测试、CLI help 抽样和活文档/skill 契约均通过。
