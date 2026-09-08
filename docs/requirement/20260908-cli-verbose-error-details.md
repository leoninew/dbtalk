---
Review status: Accepted
Flow mode: standard
Stage: Requirement
---

# 命令级 verbose 与 exec 写会话
最后修改时间: 2026-09-08 13:30:42

## Background

当前 CLI 把 SQLAlchemy / 驱动异常包装成稳定、无敏感信息的阶段错误，例如 `Error: database execution failed`。`from error` 保留了原因链，但 Click 只打印包装后的一句话，操作者和 Agent 无法判断是缺表、无权限、SQL 被拒绝，还是连接失败。

根命令现有 `-v/--verbose` 只把日志升到 DEBUG，且必须写在子命令之前。这不是本次想要的合同：根命令不提供 `-v`，改由各可执行子命令自己接受 `-v`。

`exec` 的字面含义就是执行 SQL，却额外要求 `--write` / `-w` 才进入写会话；漏掉该开关时，写入被只读会话拒绝，错误仍是 `database execution failed`，看起来像命令坏了。`query` 继续承担只读查询。

MySQL dump/restore 的 native client 失败已经把清洗后的 stderr 拼进 Click 错误；query/exec、schema/user/role、JSONL export/import 等走 SQLAlchemy 的路径没有同等待遇。

历史需求 `20260822-database-operation-safeguards` 规定 `exec` 默认只读、`--write` 才可写。本次直接收敛到新合同，不为 `--write` 或根 `-v` 保留兼容层。

## Goal

1. 根 `dbtalk` group 删除 `-v/--verbose`。`dbtalk -v ...` 为未知选项。
2. 每个可执行叶子命令接受可选 `-v/--verbose`。命令行 `-v` 与配置 `verbose` / `DBTALK_VERBOSE` 是同一开关：任一处开启即视为该次调用 verbose。
3. 默认错误文本保持现有稳定、无敏感信息的阶段描述。verbose 开启后，在同一条 `Error:` 中追加清洗后的异常细节（异常类型与消息）。
4. verbose 将该次调用的日志升到 DEBUG；不把完整 Python traceback 作为 CLI 错误正文。
5. `exec` 固定使用写会话，删除 `--write` / `-w` 及 Python API 的 `allow_write`。`query` 仍使用只读会话。
6. 手册、`--help` 和 agent skill 同步：子命令 `-v` 查看失败细节；`exec` 不再出现 `--write`。

## Non-goal

- 不改变 `query` 的只读会话、timeout、DSN 约定或“不解析 SQL 文本”的策略。
- 不在成功路径增加业务输出；成功时 verbose 只多 DEBUG 日志。
- 不把 SQLAlchemy 的 `[SQL:]`、`[parameters:]` 或绑定参数写入错误文本。
- 不新增 `--debug` / `--trace`，不把 `-v` 让给 mysqldump/pg_dump 的 native verbose。
- 不把 verbose 设为 Agent 默认必传。
- 不回写或迁移历史 SpecFlow 文档；只更新活文档、skill 和测试。
- 不为已删除的根 `-v` 或 `exec --write` 提供别名、警告或双路径。

## User scenarios

| 场景 | 输入 | 预期行为 |
| --- | --- | --- |
| 默认失败 | `dbtalk exec --dsn-env DBTALK_DSN_ORBIT --sql "update application set name = 'Nginx' where code = 'nginx'"` | 走写会话。若数据库失败，stderr 为稳定阶段错误，例如 `Error: database execution failed`。不出现口令、DSN userinfo、绑定参数。 |
| 子命令 verbose 失败 | 同上并加 `-v`：`dbtalk exec -v --dsn-env DBTALK_DSN_ORBIT --sql "..."` | 同一阶段前缀后追加清洗后的异常细节，形态为 `Error: <阶段错误>: <ExceptionType>: <消息>`。不把某一种数据库错误写成固定文案。仍不出现 `secret`、`password=` 明文、`[parameters:]`。 |
| 根命令无 -v | `dbtalk -v exec ...` | 非 0 退出；未知选项 `-v`。`dbtalk --help` 不列出 `-v`。 |
| 配置开启 verbose | `DBTALK_VERBOSE=true` 或 `verbose: true`，命令行不传 `-v` | 该次调用视为 verbose：DEBUG 日志 + 详细错误。 |
| exec 写入 | `dbtalk exec --dsn-env DBTALK_DSN_APP --sql "UPDATE ..."` | 无需 `--write`；写会话执行，成功则输出影响行数。 |
| query 写入被拒 | `dbtalk query --dsn-env DBTALK_DSN_APP --sql "UPDATE ..."` | 只读会话拒绝写入。默认 `database query failed`；`-v` 时追加只读会话拒绝对应的异常细节。 |
| 误传旧开关 | `dbtalk exec --write ...` 或 `dbtalk exec -w ...` | 未知选项，不执行 SQL。 |
| dump/restore 已含 stderr | `mysql dump` / `postgres restore` 失败 | 默认继续使用现有清洗后的 native 错误；命令上的 `-v` 额外打开 DEBUG 日志，不重复堆叠同一段 stderr。 |
| 帮助 | `dbtalk exec --help`、`dbtalk mysql dump --help` | 叶子命令列出 `-v/--verbose`；`exec --help` 不再出现 `--write` / `-w`。 |

## Acceptance

- `dbtalk --help` 与根 group 解析均无 `-v/--verbose`。
- 所有可 invoke 的叶子命令（`query`/`exec`/`export`/`import`，以及 `mysql`/`postgres` 下的 schema、user/role、grant/revoke、permissions、dump/restore）都接受 `-v/--verbose`。中间 group（`mysql`、`postgres`、`schema`、`user`、`role`、`permissions`）不提供 `-v`。
- 未开启 verbose 时，SQLAlchemy 包装错误的用户可见文本不包含驱动原文、SQL 文本或绑定参数；现有“错误必须脱敏”的测试仍然成立。
- 开启 verbose 时，SQLAlchemy 包装错误的用户可见文本包含清洗后的异常细节（异常类型与消息），且仍不含口令、DSN userinfo 和 `[parameters:]`。
- verbose 错误文本保留阶段前缀，便于区分 connection / query / execution / management / export / import。
- 命令行 `-v` 在子命令解析后即对该次调用生效，包括 DEBUG 日志；不能因为日志在根 group 里提前配置而让 `dbtalk exec -v` 只改错误文本、不改日志。
- `exec` CLI 与 `execute_from_dsn` / `execute_from_environment` 不再接受 `allow_write` / `--write` / `-w`；执行路径固定为写会话。
- `query` 与 `query_from_dsn` 仍走只读会话；不检查 SQL 关键词。
- 单元测试覆盖：默认隐藏原因、verbose 展示清洗后的异常类型与消息、DSN/password 脱敏、叶子命令 `-v`、根 `-v` 被拒绝、`exec` 无 `--write` 可写入、`query` 写入被拒、误传 `--write` 失败。
- 活文档 `docs/database.md`、`README`/`README.zh-CN.md` 以及 `plugins/dbtalk` skill 删除 `--write`，并说明子命令 `-v` 用于查看失败细节。
- 通过项目入口 `make check` 与 `make test`。

## Open questions

暂无需要用户确认的未决事项。若希望 verbose 额外打印完整 traceback，或让 Agent skill 默认带 `-v`，作为后续增量，不纳入本次。

## Decisions

- verbose 只出现在叶子命令上，不出现在根命令或中间 group。用户写 `dbtalk exec -v`、`dbtalk mysql dump -v`，不写 `dbtalk -v exec` 或 `dbtalk mysql -v dump`。
- 失败细节复用现有 `-v/--verbose` 语义，不新发明开关。`verbose` 配置与 `DBTALK_VERBOSE` 保留为进程级默认，不经由根 CLI 选项打开。
- 默认错误保持阶段级稳定文本；verbose 追加 `traceback.format_exception_only` 形式的异常细节，并去掉 SQLAlchemy 的 SQL 与 parameters 段。不把 `orig` 抽成某一种特定错误文案。
- 清洗规则与 dump/restore 一致并覆盖 PostgreSQL DSN：`password=`、`mysql_pwd=`、`PGPASSWORD=` 以及 `mysql+...://user:pass@` / `postgresql+...://user:pass@` 形式的 userinfo。
- 实现上集中渲染错误，而不是给每个 handler 复制一份 verbose 分支；Plan 再定如何把 `-v` 注入全部叶子命令，以及如何在子命令解析后配置日志。
- `query` 只读、`exec` 可写，用命令本身区分会话模式，不再用 `--write`。Python API 同步删除 `allow_write`，不为旧参数留默认值或别名。
- 历史 safeguards 文档保留当时决策；当前行为以本需求为准。

## Risk

- 去掉 `exec --write` 后，误把 DML/DDL 打到 `exec` 会直接写库。这是有意收敛：只读查询必须走 `query`。
- 即使清洗，verbose 仍可能带出表名、库名、账号名、SQLSTATE。这是诊断所需，默认模式继续隐藏。
- 给全部叶子命令注入 `-v` 时，必须避免与现有选项冲突，也要避免只改了 `query`/`exec` 而 mysql/postgres 子树漏掉。
- 日志目前在根 group 回调里配置。`-v` 下沉后若不及早重配 logging，会出现“错误变详细但 DEBUG 日志没开”的分裂行为。
- 现有测试用 `SQLAlchemyError("mysql+pymysql://admin:secret@...")` 断言 `secret` 不出现。verbose 路径必须继续脱敏。
- 活文档、skill、CLI 测试大量写有 `--write` 与 `dbtalk -v`；漏改会造成合同分裂。

## User review notes

用户原问题是 `dbtalk exec --write --dsn-env DBTALK_DSN_ORBIT` 只得到 `Error: database execution failed`。用户要求按 SpecFlow 标准模式开始任务，并为各命令增加可选 `-v` 展示失败细节。随后确认：根命令没有 `-v`；`exec` 本身就是执行，移除 `-w/--write`。实现中进一步确认：`-v` 展示清洗后的异常细节，而不是某一种特定数据库错误文案。

