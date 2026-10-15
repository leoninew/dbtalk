# Intent / 意图：数据库删除支持 --if-exists
最后修改时间: 2026-10-15 09:02:00

## Review status / 审查状态

Accepted

## Background / 背景

MySQL、PostgreSQL 的 `schema drop` 当前执行普通 `DROP DATABASE`。本地重置脚本使用 `drop && create`，目标库不存在时删除失败，后续建库无法执行。两种数据库均提供原生 `DROP DATABASE IF EXISTS`。

## Goals / 目标

- 为 `dbtalk mysql schema drop`、`dbtalk postgres schema drop` 增加显式 `--if-exists`，目标不存在时成功结束，允许重置脚本继续创建数据库。
- 不传选项时继续执行普通删除；保留显式确认、当前连接库保护、标识符引用和错误处理。
- 同步用户手册、仓库内 Agent skill 和本地重置文档；本地已填密码、DSN、项目配置保持原样。

## Non-goals / 非目标

不扩展 user/role 删除、create、restore 或其他命令，不自动终止连接，不创建 reset 入口，不安装发布，不实际删除任何业务数据库。

## User stories / scenarios / 用户故事 / 场景

- 重置已有库时正常删除；第一次初始化或重跑中断脚本时，目标库不存在也能继续建库。
- 未传 `--if-exists` 时，缺失目标仍由服务器报错；传入选项也不能跳过权限、连接和其他删除失败。

## Acceptance criteria / 验收标准

- A01：两个 CLI 均展示并接收 `--if-exists`；默认生成普通 DROP，显式启用时生成原生 `DROP DATABASE IF EXISTS`。
- A02：显式启用时仍要求 `--yes`，仍拒绝删除当前连接库；标识符引用、AUTOCOMMIT 和真实错误传播保持有效。
- A03：帮助、用户手册及仓库内 Agent skill 说明默认与显式行为；本地脚本两个 drop 使用新选项，全部已有配置原样保留。
- A04：相关回归测试及仓库既有 Ruff、Mypy、pytest 检查通过，并记录实际检查边界。

## Workload assessment / 工作量评估

工作量小：两个对称的数据库生命周期模块、一份已有参数化测试、两份手册与两份 Agent skill，以及本地脚本。复杂度低：只增加一个布尔选项，由原生 SQL 决定目标不存在时的行为。风险主要是意外吞掉其他异常或绕过已有删除保护，需要重点回归。

结论：保持单任务。两个方言共享同一接口约定和测试入口，拆分会增加协调成本；不引入新抽象或共享执行层。审查重点为 CLI 到 SQL 的参数贯通、两类保护以及未启用选项时的行为。

## Candidate tasks and dependencies / 候选子任务与依赖

不适用。

## Open questions / 待定问题

暂无需要用户确认的未决事项。根据会话上下文，drop 范围为 MySQL、PostgreSQL 的 `schema drop`。

## Decisions / 决策

- 使用原生 `IF EXISTS`，不额外查询目标是否存在，不捕获后忽略数据库异常。
- 新选项默认关闭；仅显式启用时使用能涵盖目标不存在情况的成功提示。

## Risks / assumptions / 风险 / 假设

- 仅目标库缺失可跳过；管理 DSN 本身不可连接、权限不足和 PostgreSQL 的其他会话占用仍会失败。
- 开发检查使用模拟连接核对原生 SQL 和错误路径，不以此声称完成真实数据库实验。

## User review notes / 用户审查记录

- 2026-10-15：用户明确要求“$specflow:specflow 添加 drop 的 --if-exists 支持”。依据该实现请求补齐意图与计划并推进开发；Accepted 表示当前授权覆盖的前置方案，不声称已完成独立逐阶段审查或整体验收。
