# 数据库 query/exec 安全边界
最后修改时间: 2026-08-22 21:58:10

Review status: Accepted

## Background

`dbtalk database query` 与 `exec` 当前直接执行单条 SQL，缺少统一的语句超时和显式写入授权。

## Goal

- 为 `query` 和 `exec` 提供以秒为单位的可配置语句超时，默认值由 `database.operation_timeout_seconds` 集中管理（默认 30 秒）。
- `exec` 默认使用只读会话；显式提供 `--write` 或 `-w` 时才切换为写会话。
- `query` 在数据库会话中使用只读模式，不依赖 SQL 文本解析或 DML 关键词识别。

## Non-goal

- 不为 JSONL `export` / `import`、MySQL dump / restore 增加相同选项。
- 不引入 SQL AST、语句分类器或 SQL allowlist。
- 不变更数据源配置模型或数据库支持范围。

## User scenarios

- 操作者查询数据时，命令默认使用只读数据库会话，并可用 `--timeout 10` 限制最长执行时间。
- 操作者执行更新、DDL 或其他写操作时，必须显式添加 `--write` 或 `-w`。
- 超时后命令以稳定、无敏感信息的错误信息退出。

## Acceptance

- `database query` 和 `database exec` 都显示并接受 `--timeout/-t`；省略时读取 `database.operation_timeout_seconds`，且显式值只接受正整数。
- 不带 `--write/-w` 的 `database exec` 可执行只读 SQL，但数据库拒绝其中的写入；带该参数时可执行单条写入 SQL。
- `database query` 不能在 SQLite 中写入；实现不检查 SQL 关键词或解析 SQL。
- SQLite 查询和执行可在超时后中断，并对用户报告超时。
- CLI 手册与 Codex Skill 反映新参数和安全约束。

## Open questions

暂无需要用户确认的未决事项。

## Decisions

- 超时仅作用于 query/exec 单条语句，单位为秒；配置默认值为 `database.operation_timeout_seconds`。
- 显式 `--timeout/-t` 优先于 `database.operation_timeout_seconds`；`exec` 的会话模式由 `--write/-w` 决定，不依赖 SQL 分类。
- 超时采用各数据库/驱动的会话或连接级能力，不使用线程强杀。
- `query` 和 `exec` 的会话模式代替 DML 检测：二者默认只读，`exec --write` 显式切换为写会话。

## Risk

- MySQL 的客户端中断在非事务性语句上无法承诺回滚；命令会保留数据库原生事务语义并在手册中说明。
