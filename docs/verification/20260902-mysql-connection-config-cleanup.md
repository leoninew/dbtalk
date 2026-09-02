---
Review status: Draft
Flow mode: standard
Stage: Verification
---

# MySQL 连接配置清理验证
最后修改时间: 2026-09-02 15:43:24

## Requirement alignment

- `Settings` 已收敛为 `mysql`、`postgres`、`database` 配置组。`mysql` 使用 `MySQLConfig`，`postgres` 使用共享的 `DumpRestoreConfig`；旧的 MySQL dump/restore 配置类型、loader 和 `Settings` 字段已删除。
- `dbtalk.yaml`、`.env.example` 和用户文档仅公开 MySQL/PostgreSQL 的默认 dump 目录、Docker client image，以及 MySQL 专用的零日期配置；连接身份和 target 继续由当次 DSN 与 `--database` 提供。
- MySQL Docker fallback 改为精确检查 `mysql.client_image`，不再扫描或偏好任意本地 `mysql:*` image。MySQL dump 与 restore 的 unit tests 覆盖同一 image 传递路径。
- `database.query_timeout_seconds` 与 `database.exec_timeout_seconds` 分别由 typed settings 加载和正数校验；CLI 显式 `--timeout` 仍可覆盖对应命令，operation timeout 源码常量已移除。
- `tests/test_manual_integration.py` 已删除。未执行 MySQL 或 PostgreSQL dump/restore 集成操作，以避免修改既有数据库、用户或数据。
- `plugins/dbtalk` 未修改，符合已确认的后续同步边界。

## Spec alignment

不适用：本任务为 standard 模式，未创建 Spec。

## Plan alignment

- Plan 中列出的 typed settings、MySQL/PostgreSQL dump/restore、配置样例、用户文档和相关单元测试均包含在实际 diff 中。
- Requirement 的 plugin 验收项已与既定“后续同步 `plugins/dbtalk`”决策一致化。

## Actual diff summary

- 收敛 MySQL/PostgreSQL dump/restore typed config，并将 MySQL Docker fallback 绑定到显式 image。
- 移除过时 MySQL 连接和 target 配置，迁移 MySQL 零日期设置，并拆分 query/exec timeout。
- 更新配置样例、用户文档和单元测试；删除手工 dump/restore 集成测试。

## Expected vs actual changed files

| 范围 | 结果 |
| --- | --- |
| Settings、数据库 CLI 与 operation helpers | 已按 Plan 修改。 |
| MySQL/PostgreSQL dump/restore 实现 | 已按 Plan 修改。 |
| 配置样例与活跃用户文档 | 已按 Plan 修改。 |
| 单元测试与手工集成测试删除 | 已按 Plan 修改。 |
| `plugins/dbtalk` | 未修改，符合任务边界。 |

## Acceptance checklist

- [x] 共享 `DumpRestoreConfig` 与 MySQL 专用 `MySQLConfig` 已生效。
- [x] 旧 `mysqldump` / `mysqlrestore` 类型、loader 和 settings 字段已移除。
- [x] 配置样例与环境变量样例已迁移到 `mysql.*`、query timeout、exec timeout。
- [x] dump/restore target 仍只按 `--database > DSN database > 失败` 解析。
- [x] MySQL fallback 仅检查配置 image，不扫描或 pull 其他 image。
- [x] 相关单元测试覆盖共享配置、image 传递、零日期和 timeout。
- [x] 手工 dump/restore integration test 已删除，未新增该类环境配置。
- [x] 活跃文档与 CLI help 已移除旧 MySQL 配置；plugin 同步延后。
- [x] Requirement 保留参数清单及配置边界说明。
- [x] query/exec timeout 分别集中管理，源码无共享 timeout 常量。
- [x] `make check` 与 `make test` 均已通过。

## Test results

| 命令 | 结果 |
| --- | --- |
| `make check` | 通过：Ruff format、Ruff lint、mypy 均通过。 |
| `make test` | 通过：238 passed。 |
| `uv run --locked --no-sync dbtalk query --dsn-env DBTALK_MYSQL_DSN --sql 'SELECT 1' --format json` | 通过。 |
| `uv run --locked --no-sync dbtalk query --dsn-env DBTALK_POSTGRES_DSN --sql 'SELECT 1' --format json` | 通过。 |

## Risks and incomplete items

- 本任务按约束未运行真实 dump/restore 集成测试；native client 成功路径以单元测试覆盖。

## Conclusion

核心配置清理、Docker image 收敛、query/exec timeout 拆分及只读 MySQL/PostgreSQL DSN 连通性均已验证。所有项目质量检查和单元测试均通过；Verification 保持 Draft，等待用户审阅。
