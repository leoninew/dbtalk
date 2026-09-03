---
Review status: Accepted
Flow mode: standard
Stage: Verification
---

# MySQL dump/restore 生产可用性改进验证
最后修改时间: 2026-08-28 12:57:22

## Requirement alignment

本次验证对照已接受的 Requirement，确认 dump 与 restore 的公开契约已分离：dump 只保留顶层 `USE`，不生成数据库生命周期 DDL；restore 使用独立的 `--database TARGET`，目标库必须预先存在，并在导入前拒绝输入中的 `CREATE/DROP DATABASE`；`--skip-definer` 只透传原生客户端参数；dump 制品和生命周期日志按计划实现。

## Plan alignment

standard 模式不单独创建 Spec，本次按已接受的 Plan 验证。Plan 中的 CLI 契约、临时制品发布、restore 目标预检、DDL 安全边界、日志、文档和测试任务均已有对应实现或测试覆盖。

## Actual diff summary

实际变更集中在 MySQL dump/restore、配置契约、CLI 文档、plugin skill 和测试：

- `src/dbtalk/mysql/cli.py`、`dump.py`、`restore.py`、`client.py`：新增 restore 目标库边界、`--skip-definer`、临时制品发布、进度和生命周期日志，并共享本机映射容器发现与直连路径。
- `src/dbtalk/settings.py`、`dbtalk.yaml`、`.env.example`：同步 dump/restore 配置契约。
- `docs/mysql.md`、`plugins/dbtalk/skills/dbtalk-mysql/SKILL.md`：同步公开用法和安全边界。
- `tests/test_mysql.py`、`tests/test_mysql_logging.py`、`tests/test_settings.py`：覆盖参数、路径、失败保护、目标库、DDL 拒绝和日志行为。

未修改 PostgreSQL、JSONL transfer 或数据库生命周期实现。工作区中已有的未跟踪 `backup.sql` 不属于本次变更。

## Expected vs actual changed files

Plan 预期修改的源码、配置、文档和测试文件均已覆盖；另外新增本 Verification 文档。没有发现超出计划范围的产品代码文件。

## Acceptance checklist

- [x] dump help 暴露 `--skip-definer`，不再暴露 create/drop dump 选项；restore help 暴露必填 `--input` 和 `--database TARGET`。
- [x] dump 使用 `-B` 和 `--no-create-db`；真实 dump 含 1 条 `USE`，未发现 `CREATE DATABASE` 或 `DROP DATABASE`。
- [x] restore 使用独立目标库；真实流程从 `pomelo_orbit` 恢复到新建的 `dbtalk_it_01569b415855`，目标库未覆盖已有库。
- [x] restore 目标库预检、`USE` 重写、原始输入保护和数据库生命周期 DDL 拒绝由定向测试覆盖。
- [x] 本机端口唯一映射到 Docker MySQL 容器时，dump/restore 的预检和 native client 均通过该容器 Unix socket；无映射时保留 Docker fallback，由定向测试覆盖。
- [x] dump 默认保留 `DEFINER`，`--skip-definer` 在三条客户端路径的透传和不支持参数失败由测试覆盖。
- [x] 普通 SQL/gzip 临时文件、非空检查、自动命名冲突、原子发布和失败清理由测试覆盖。
- [x] dump/restore 生命周期日志包含阶段、耗时和字节数；敏感信息脱敏由测试覆盖。
- [x] CLI 文档、plugin skill、配置和测试契约已同步。

## Test results

工作目录：`D:\SourceCodes\mywork\pomelo-dbtalk`

- `make check`：通过。Ruff format 检查 58 个文件，Ruff lint 通过，mypy 检查 58 个文件无问题。
- `make test`：通过，`189 passed, 1 skipped`。跳过项是仓库原有的手工集成入口，因未设置 `DBTALK_RUN_INTEGRATION=1`。
- `uv run --locked --no-sync pytest tests/test_mysql.py tests/test_mysql_logging.py tests/test_settings.py tests/test_unit_boundaries.py -q`：通过，`61 passed, 5 subtests passed`。
- `uv run dbtalk mysql dump --help` 与 `uv run dbtalk mysql restore --help`：通过，CLI 参数符合新契约。
- `git diff --check`：通过。

## Real MySQL integration

使用本地 Docker MySQL 容器 `mysql:8.0.39` 的宿主机映射 `127.0.0.1:3306`，root 通过环境变量方式提供凭据。

- 源库 `pomelo_orbit` 导出前后均为 44 张表，源库表空间统计均为 `2310144` bytes。
- 默认 dump 成功，制品为 `186439` bytes，包含 1 条顶层 `USE`，未包含数据库生命周期 DDL。
- 通过 `dbtalk mysql database create` 创建唯一目标库 `dbtalk_it_01569b415855`，随后通过 `dbtalk mysql restore --database` 导入成功。
- 目标库校验为 44 张表、`2359296` bytes；已有源库和其他已有库未执行写操作。
- 对本地映射容器执行 `--skip-definer` 时，容器中的 `mysqldump` 明确返回 `unknown option '--skip-definer'`；dbtalk 返回非零且没有发布残缺制品，符合不支持时不静默降级的要求。成功透传由单元测试覆盖。

## Missed or expanded scope

仓库全量测试中的手工集成函数仍按默认配置跳过；此前已用独立实际 CLI 流程完成本地 3306 dump、create、restore 和校验。由于本地客户端不支持 `--skip-definer`，未能验证该选项成功生成制品，仅验证了清晰失败和无制品发布行为。

本次新增的 mapped-container restore 已由定向测试覆盖；由于当前验证进程没有 `DBTALK_DSN_IT` 且 `.env` 未提供该变量，尚未重新执行新增代码的真实 Docker restore。宿主机仍未安装 `mysql`/`mysqldump`。

## Risks and incomplete items

- restore 仍遵循 MySQL 原生 DDL/DML 的部分失败语义，不承诺整体事务回滚。
- `--skip-definer` 的成功使用依赖运行时 `mysqldump` 版本；当前本地 MySQL 8.0.39 客户端不支持该参数。
- 本次创建的 `dbtalk_it_01569b415855` 和 dump 制品保留用于复查，不属于已有数据库，未覆盖已有数据。

## Conclusion

Requirement 和 Plan 的主要验收项已通过，源码质量检查、全量单元测试和定向测试均成功；本机 Docker mapped-container restore 的真实复测待补充 DSN 后执行。此前的本地 MySQL 3306 端到端 dump/create/restore 记录仍保留，但不替代本次新增路径的真实复测。
