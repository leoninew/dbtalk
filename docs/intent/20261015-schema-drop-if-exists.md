# Intent / 意图：数据库删除支持 --if-exists 与 PostgreSQL --force
最后修改时间: 2026-10-15 09:09:11

## Review status / 审查状态

Accepted

## Background / 背景

MySQL、PostgreSQL 的 `schema drop` 原先执行普通 `DROP DATABASE`，前一轮已增加 `--if-exists`，使本地 `drop && create` 脚本在目标不存在时可以继续建库。本轮用户实际执行删除 `casdoor` 时遇到 4 个其他会话占用，要求考虑该问题；`IF EXISTS` 无法处理连接占用，需要 PostgreSQL 原生的显式 `FORCE` 选项。

## Goals / 目标

- 为 `dbtalk mysql schema drop`、`dbtalk postgres schema drop` 增加显式 `--if-exists`，目标不存在时成功结束，允许重置脚本继续创建数据库。
- 为 PostgreSQL 的 `schema drop` 增加显式 `--force`，尝试终止目标库已有连接后删除；可与 `--if-exists` 组合使用。
- 不传选项时继续执行普通删除；保留显式确认、当前连接库保护、标识符引用和错误处理。
- 同步用户手册、仓库内 Agent skill 和本地重置文档；本地已填密码、DSN、项目配置保持原样。

## Non-goals / 非目标

不扩展 user/role 删除、create、restore 或 MySQL 强制删除，不默认终止连接，不实现逐会话终止或自动重试，不创建 reset 入口，不安装发布，不实际删除任何业务数据库。

## User stories / scenarios / 用户故事 / 场景

- 重置已有库时正常删除；第一次初始化或重跑中断脚本时，目标库不存在也能继续建库。
- 未传 `--if-exists` 时，缺失目标仍由服务器报错；传入选项也不能跳过权限、连接和其他删除失败。
- PostgreSQL 本地重置遇到业务应用连接占用时，可显式指定 `--force`；不指定时保留原有占用报错。

## Acceptance criteria / 验收标准

- A01：两个 CLI 均展示并接收 `--if-exists`；默认生成普通 DROP，显式启用时生成原生 `DROP DATABASE IF EXISTS`。
- A02：显式启用时仍要求 `--yes`，仍拒绝删除当前连接库；标识符引用、AUTOCOMMIT 和真实错误传播保持有效。
- A03：帮助、用户手册及仓库内 Agent skill 说明默认与显式行为；本地脚本两个 drop 使用新选项，全部已有配置原样保留。
- A04：相关回归测试及仓库既有 Ruff、Mypy、pytest 检查通过，并记录实际检查边界。
- A05：PostgreSQL CLI 展示并接收 `--force`；与 `--if-exists` 的四种组合生成正确原生 SQL，强制选项不绕过 `--yes` 或当前连接库保护，原生删除失败继续传播。手册、Agent skill 及本地 PostgreSQL 重置命令说明该选项会终止目标连接。

## Workload assessment / 工作量评估

工作量小：两个数据库生命周期模块、一份已有参数化测试、两份手册与两份 Agent skill，以及本地脚本；本轮追加只影响 PostgreSQL 路径。复杂度低：两个显式布尔选项分别映射原生 SQL。风险主要是意外吞掉异常、绕过已有删除保护，或在未指定 force 时中断连接，需要重点回归。

结论：保持单任务。两个方言共享同一接口约定和测试入口，拆分会增加协调成本；不引入新抽象或共享执行层。审查重点为 CLI 到 SQL 的参数贯通、两类保护以及未启用选项时的行为。

## Candidate tasks and dependencies / 候选子任务与依赖

不适用。

## Open questions / 待定问题

暂无需要用户确认的未决事项。根据会话上下文，drop 范围为 MySQL、PostgreSQL 的 `schema drop`。

## Decisions / 决策

- 使用原生 `IF EXISTS`，不额外查询目标是否存在，不捕获后忽略数据库异常。
- 新选项默认关闭；仅显式启用时使用能涵盖目标不存在情况的成功提示。
- PostgreSQL 强制删除使用原生 `WITH (FORCE)`，不查询、循环终止或自动重试其他会话；默认关闭。

## Risks / assumptions / 风险 / 假设

- `--if-exists` 仅跳过目标缺失；其他会话占用只有显式 `--force` 才尝试处理。管理 DSN 不可连接、终止权限不足、预备事务、活跃逻辑复制槽或订阅仍可能导致失败。
- 开发检查使用模拟连接核对原生 SQL 和错误路径，不以此声称完成真实数据库实验。

## User review notes / 用户审查记录

- 2026-10-15：用户明确要求“$specflow:specflow 添加 drop 的 --if-exists 支持”。依据该实现请求补齐意图与计划并推进开发；Accepted 表示当前授权覆盖的前置方案，不声称已完成独立逐阶段审查或整体验收。
- 2026-10-15：用户提供 `ObjectInUse` 和 4 个其他会话占用的实际错误并要求考虑该问题，扩展当前删除功能为显式 PostgreSQL `--force`；只修改实现与文档，不实际终止业务连接或删库。
