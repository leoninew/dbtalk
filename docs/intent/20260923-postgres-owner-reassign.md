# PostgreSQL 对象所有权转移意图
最后修改时间: 2026-09-23 10:25:01

Review status: Accepted
Flow mode: light / 轻量模式
Current stage: Intent / 意图（已接受）

## Background

`dbtalk postgres grant --profile migrator` 可以授予数据库及 schema 范围的权限，却不会改变已有对象的 owner。K12 测试库曾由 `k12_ai_test` 拥有，但已有表由 `k12_ai` 拥有，导致 `k12_ai_test` 读取 `alembic_version` 时权限不足；临时使用受限的 `ALTER TABLE ... OWNER TO ...` 处理了该实例。希望在 `dbtalk` 中提供可复用、可预览的 PostgreSQL 原生所有权转移能力。

## Goal

增加清晰区分于 `grant/revoke` 的 `dbtalk postgres owner reassign` 命令，对明确指定的源 role、目标 role 和当前数据库执行 PostgreSQL 原生 `REASSIGN OWNED`。默认只预览对象范围，执行须显式确认；如源 role 拥有数据库或表空间等共享对象，须另行确认其跨库影响。操作失败须显式报错，不输出 DSN 凭据。

## Non-goal

- 不添加 PostgreSQL role 创建、密码轮换、通用 SQL 或迁移执行功能。
- 不把 `migrator` profile 当作 owner 转移，也不提供假装仅限制一个 schema/表的原生 `REASSIGN OWNED` 参数。
- 不修改或重新迁移 K12 数据库，不安装/发布 CLI，也不更改其他方言。

## User scenarios

1. 管理员指定数据库和两个 role，先查看当前库及共享对象的受影响范围，不发生数据库写入。
2. 确认影响范围后显式执行，迁移账号成为原 role 所有对象的新 owner。
3. 预览发现共享对象时，管理员需要明确接受共享对象的影响，避免误转移其他数据库或表空间的所有权。

## Acceptance

- 命令要求管理 DSN、`--database`、`--from-role`、`--to-role`；拒绝相同或非法 role、错误数据库及不存在的 role。
- 默认预览，不运行 `REASSIGN OWNED`；`--yes` 才允许写入；存在共享对象时还要求 `--include-shared`。
- 执行使用 PostgreSQL 原生命令和事务，正确引用 role 标识符；错误清洗后不泄露连接凭据。
- 单元测试覆盖预览、执行、错误路径、共享对象确认和 CLI 帮助；项目测试及静态检查通过；隔离 PostgreSQL 验证表和数据库 owner 的实际变化并清理资源。
- 同步 README、PostgreSQL 手册及插件 skill 的命令入口、范围和确认规则。

## Open questions

- 暂无必须在 Intent 阶段决定的问题。预览统计只提供执行前快照，不保证并发变更后完全一致；后续验证应核查这一限制是否已准确告知使用者。

## Decisions

- 使用独立 `owner reassign` 操作而非扩展 `grant`；原生命令决定对象范围，显式展示并保护 cluster 共享对象。
- 与现有插件约定一致，Agent 通过 `.env` 中的 `DBTALK_DSN_POSTGRES_ADMIN` 与 `--dsn-env` 使用命令，不把 DSN 或密码放到参数或日志里。

## Risk

- `REASSIGN OWNED` 会覆盖当前库中源 role 拥有的全部对象，且可能改变数据库、表空间等共享对象归属；误执行影响超过单表修复。
- 转移后旧 owner 不再享有由对象所有权带来的权限；不同数据库中的非共享对象仍需分别处理。
- 当前工作树已存在本功能代码、测试和文档改动，且曾运行单元/静态及隔离 PostgreSQL 检查。这些属于先前执行事实，不表示本草稿获得审查接受；本轮只新建 Intent，不新增产品代码、verification 文档或执行验证阶段。
