---
Review status: Accepted
Flow mode: standard
Stage: Plan
---

# 命令级 verbose 与 exec 写会话计划
最后修改时间: 2026-09-08 13:30:42

## Requirement basis

- 已接受的 Requirement：`docs/requirement/20260908-cli-verbose-error-details.md`。
- 根 `dbtalk` group 删除 `-v/--verbose`；`dbtalk -v ...` 为未知选项。
- 所有可 invoke 的叶子命令接受 `-v/--verbose`。中间 group 不提供 `-v`。
- 命令行 `-v` 与 `verbose` / `DBTALK_VERBOSE` 是同一开关；开启后该次调用升 DEBUG 日志，并在 `Error:` 中追加清洗后的异常细节。
- 默认错误保持阶段级稳定文本，不含驱动原文、SQL、绑定参数和口令。
- `exec` 固定写会话，删除 `--write` / `-w` 与 Python API `allow_write`。`query` 仍只读。
- 不为根 `-v` 或 `--write` 保留别名、警告或双路径。历史 safeguards 文档不回写。

## Design

### Click 注入

不要给每个 handler 增加 `verbose` 参数。新增 `src/dbtalk/cli_runtime.py`：

- `DbtalkCommand(click.Command)`：注入 `-v/--verbose` is_flag；在 `invoke` 里从 `ctx.params` 弹出 `verbose`，避免 callback 签名变化。
- `DbtalkGroup(click.Group)`：`command_class = DbtalkCommand`，`group_class = DbtalkGroup`。Group 自身不声明 `-v`。
- 独立 `@click.command`（MySQL/PostgreSQL 的 `grant`/`revoke`）显式 `cls=DbtalkCommand`。
- 现有 `@click.group(...)` 全部加 `cls=DbtalkGroup`，包括根 `cli`、`database`、`mysql`/`postgres` 及其 `schema`/`user`/`role`/`permissions`。

叶子命令帮助因此自动列出 `-v`；`dbtalk --help`、`dbtalk mysql --help` 不列出 `-v`。

### verbose 生效时机

根回调只读取 settings：

```text
configure_logging(settings.logging.level, settings.logging.format, settings.verbose)
ctx.obj = DbtalkContext(settings=settings, verbose=settings.verbose)
```

子命令 `-v` 在 `DbtalkCommand.invoke` 中与 `settings.verbose` 做或运算。若开启且当前 context 还不是 verbose：替换 root `ctx.obj` 为 `verbose=True` 的新 `DbtalkContext`，并再次 `configure_logging(..., verbose=True)`（现有 `force=True`）。保证 `dbtalk exec -v` 同时打开 DEBUG 日志和详细错误。

### 错误渲染

在 `src/dbtalk/database/models.py` 增加集中函数，供 CLI 与 mysql 清洗复用：

- `sanitize_error_detail(message)`：覆盖 `password=`、`mysql_pwd=`、`PGPASSWORD=`，以及 `mysql+...://user:pass@` / `postgresql+...://user:pass@` userinfo；截断到 1000 字符。
- `driver_error_detail(error)`：用 `traceback.format_exception_only` 生成异常类型与消息，去掉 SQLAlchemy `[SQL:]`、`[parameters:]`、`(Background on this error at:` 段；不优先抽取 `orig`。
- `format_cli_error(error, *, verbose)`：默认返回 `str(error)`；verbose 时沿 `__cause__` 追加清洗后的异常细节。若异常消息已包含在现有文本中（dump/restore native stderr），不重复拼接。

`DbtalkCommand.invoke` 捕获 callback 抛出的 `ClickException`（不含 `UsageError`/`Abort`）以及未转换的 `DatabaseOperationError`/`DatabaseTransferError`/`RuntimeError`，按当前 verbose 格式化后抛出 `click.ClickException`。各 handler 现有 `raise click.ClickException(str(error)) from error` 可保留，由 command 层统一补细节。

`mysql.client.sanitize_error_message` 改为调用 `sanitize_error_detail`，保持 dump/restore 现有脱敏测试。不改 dump/restore 默认已包含 stderr 的合同。

包装点（`database execution failed` 等）继续只放阶段前缀，不把驱动原文写进异常消息；细节只在 verbose 渲染时出现。这样默认路径的脱敏测试不必改断言语义。

### exec 写会话

- 删除 `exec` 的 `--write`/`-w`。
- `execute_from_dsn` / `execute_from_environment` 删除 `allow_write`，内部固定 `client.execute(..., read_only=False)`。
- `DatabaseClient.execute` 的 `read_only` 参数保留给内部 query/exec 分流；`query_from_dsn` 仍走只读会话。
- 不为旧开关留默认值、别名或 deprecation warning。

## Implementation steps

1. 错误清洗与格式化。
   - 修改 `src/dbtalk/database/models.py`：加入 `sanitize_error_detail`、`driver_error_detail`、`format_cli_error`。
   - 修改 `src/dbtalk/mysql/client.py`：`sanitize_error_message` 转调共享实现。
   - 新增单元测试：异常类型与消息、SQL/parameters 剥离、DSN/password 脱敏、verbose 开关、已含细节时不重复拼接、不把 orig 当作特定错误文案。

2. Click runtime 与根命令去 `-v`。
   - 新增 `src/dbtalk/cli_runtime.py`：`DbtalkCommand`、`DbtalkGroup`、`apply_command_verbose`。
   - 修改 `src/dbtalk/cli.py`：根 group 使用 `cls=DbtalkGroup`，删除根 `-v` 参数；日志与 context 只跟 `settings.verbose`。
   - 修改全部叶子命令所属 group/command 声明，使叶子命令带 `-v`、中间 group 不带。涉及：`src/dbtalk/database/cli.py`、`src/dbtalk/mysql/cli.py`、`src/dbtalk/mysql/database.py`、`src/dbtalk/mysql/user.py`、`src/dbtalk/mysql/permissions.py`、`src/dbtalk/postgres/cli.py`、`src/dbtalk/postgres/database.py`、`src/dbtalk/postgres/role.py`、`src/dbtalk/postgres/permissions.py`。

3. 删除 exec 写开关。
   - 修改 `src/dbtalk/database/cli.py`：去掉 `--write`/`-w` 与 `write_enabled`。
   - 修改 `src/dbtalk/database/operations.py`：去掉 `allow_write`，exec 固定写会话。

4. 测试。
   - 修改 `tests/test_database_operations.py`：无 `--write` 的 `exec` 可 UPDATE；`query` 的 UPDATE 仍失败；`exec --write` / `exec -w` 为未知选项；`dbtalk -v exec` 失败；`dbtalk exec -v` 在 SQL 失败时输出阶段前缀加异常类型与清洗后消息，且不含 `[parameters:]` 与口令。
   - 修改或新增帮助断言：`dbtalk --help` 无 `-v`；`dbtalk exec --help` 有 `-v`、无 `--write`；至少一个 mysql/postgres 叶子命令帮助有 `-v`。
   - 保持 `tests/test_mysql_logging.py`、administration/user management 的 `secret` 脱敏断言在默认路径成立；如覆盖 verbose，另断言 `<redacted>` 且无 `secret`。

5. 活文档与 skill。
   - 修改 `docs/database.md`、`README.md`、`README.zh-CN.md`、`plugins/dbtalk/skills/dbtalk/SKILL.md`：exec 示例去掉 `--write`；说明 `query` 只读、`exec` 可写；说明叶子命令 `-v` 查看清洗后的失败原因；不要写 `dbtalk -v`。
   - 不修改历史 Requirement/Plan/Verification。

## Expected files

- `src/dbtalk/cli_runtime.py`（新增）
- `src/dbtalk/cli.py`
- `src/dbtalk/database/models.py`
- `src/dbtalk/database/cli.py`
- `src/dbtalk/database/operations.py`
- `src/dbtalk/mysql/client.py`
- `src/dbtalk/mysql/cli.py`
- `src/dbtalk/mysql/database.py`
- `src/dbtalk/mysql/user.py`
- `src/dbtalk/mysql/permissions.py`
- `src/dbtalk/postgres/cli.py`
- `src/dbtalk/postgres/database.py`
- `src/dbtalk/postgres/role.py`
- `src/dbtalk/postgres/permissions.py`
- `tests/test_database_operations.py`
- `tests/test_mysql_logging.py`（仅在共享 sanitizer 行为需对齐时）
- `docs/database.md`
- `README.md`
- `README.zh-CN.md`
- `plugins/dbtalk/skills/dbtalk/SKILL.md`

## Verification plan

1. 定向跑 query/exec、错误脱敏、帮助文本相关单元测试。
2. `make check`：项目统一 Ruff format/lint 与 mypy。
3. `make test`：全量单元测试。
4. 人工核对：`dbtalk --help` 无 `-v`；`dbtalk exec --help` 有 `-v`、无 `--write`；文档与 skill 不再出现 `exec --write` 或根 `-v`。
5. 不连真实 ORBIT/MySQL/PostgreSQL 做集成写入。

## Risks and rollback

- `exec` 去掉确认开关后，误用 `exec` 会直接写库。只读必须走 `query`。这是需求内的有意收敛。
- verbose 仍可能暴露表名、库名、账号名、SQLSTATE；默认模式继续隐藏。清洗失败会把口令打到 stderr，必须用含 `secret` 的 SQLAlchemy 消息覆盖 verbose 路径。
- Click 在根 group 里先配置 logging。若忘记在子命令 `-v` 后 `configure_logging`，会出现错误变详细但 DEBUG 未开。
- 给全部叶子命令注入 `-v` 时，漏改独立 `grant`/`revoke` 或某个 nested group 会导致帮助和行为不一致。
- 如需回退，恢复本任务改动即可；不增加兼容层。

## Open questions

暂无需要用户确认的未决事项。

## User review notes

用户要求进入计划 / Plan。需求已按用户确认收敛：根命令无 `-v`；叶子命令有 `-v`；`exec` 删除 `--write`/`-w`。实现中确认 verbose 渲染异常细节，而不是抽取 `orig` 作为特定错误文案。
