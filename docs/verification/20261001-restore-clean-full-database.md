# Restore 显式 Clean 完整还原验证
最后修改时间: 2026-10-01 12:25:48

Review status: Draft
Flow mode: standard / 标准模式
Stage: Verification / 验证

## Record basis

依据 [Intent](../intent/20261001-restore-clean-full-database.md)、[Plan](../plan/20261001-restore-clean-full-database.md)、当前 diff 和上一轮实际验证输出补记。本轮只创建过程文档并核对 diff，不重新执行产品测试、启动数据库或安装发布。

Intent 和 Plan 记录已确认的范围及用户要求记录的后续阶段；本文为新建验证记录，尚未得到用户的文档审查接受，保留 `Draft`。这不改变已经完成的实现及自动验证事实。

## Intent alignment

两种引擎均仅在显式 `--clean` 时清空目标库业务对象再导入，MySQL 已增加选项并贯通 options。PostgreSQL 的清理不再受 archive 对象列表限制，额外表的外键由 schema 级联清理处理；MySQL 根据当前目录删除额外对象，在单个清理会话内关闭外键检查。目标数据库本身、实例级账号和授权保留，默认 restore 行为不变。

PostgreSQL 完整还原仍使用 custom archive；MySQL 仍导入 SQL/gzip SQL。未修改 dump 参数、备份格式、JSONL transfer、配置加载、迁移或安装发布逻辑。

## Spec alignment

不适用。用户选择 standard，未创建独立 Spec；技术设计记录于 Plan。

## Plan alignment

- 实现了两种引擎独立的目标清理，三个既有客户端分支继续使用相同目标和凭据通道。
- PostgreSQL 输入校验先执行，清理使用 `psql` 单事务和 fail-fast，随后使用原有 `pg_restore` owner/ACL 和 jobs 选项导入。
- MySQL 输入预检、目标库探测、JSON 对象枚举、临时清理 SQL 和导入按计划顺序执行；清理错误停止导入。
- 已补齐聚焦单元测试、手册和 Agent skill；上一轮完成全仓检查及两种引擎的隔离真实恢复。
- Plan 属于实施后记录，不声称此前已经逐阶段审查或按前置书面计划执行。

## Actual diff summary

- `src/dbtalk/postgres/restore.py`：新增清理 SQL 和 psql 命令路由，清除非系统 schema 与库内用户对象，恢复标准 public，保留原有 archive 校验及导入流程。
- `src/dbtalk/postgres/cli.py`：更新 `--clean` 帮助为全目标用户对象清理。
- `src/dbtalk/mysql/restore.py`：增加 clean options、系统库拒绝、当前对象 JSON 枚举、临时清理 SQL 与三个路由的清理阶段。
- `src/dbtalk/mysql/cli.py`：公开并传递显式 `--clean`。
- `tests/test_postgres.py`：不再断言原生 clean 参数透传，并将无效 archive 测试扩展到 clean 开启的情况。
- `tests/test_restore_clean.py`：新增 20 个参数化测试实例，覆盖客户端分支、阶段顺序、目标与凭据、gzip、标识符引用、清理失败停止导入、系统库保护与 CLI 显式选择。
- `docs/mysql.md`、`docs/postgres.md`、两个引擎的仓库内 `SKILL.md`：同步清理语义、依赖、权限与失败后状态。
- 本轮新增同名 Intent、Plan、Verification 三份过程记录；本轮未修改上述产品文件。

## Expected vs actual changed files

| 预期边界 | 实际文件 | 结果 |
| --- | --- | --- |
| PostgreSQL restore 与 CLI | `src/dbtalk/postgres/restore.py`、`src/dbtalk/postgres/cli.py` | 对齐 |
| MySQL restore 与 CLI | `src/dbtalk/mysql/restore.py`、`src/dbtalk/mysql/cli.py` | 对齐 |
| 相关测试 | `tests/test_postgres.py`、新增 `tests/test_restore_clean.py` | 对齐 |
| 用户手册 | `docs/mysql.md`、`docs/postgres.md` | 对齐 |
| 仓库内 Agent skill | `plugins/dbtalk/skills/dbtalk-mysql/SKILL.md`、`plugins/dbtalk/skills/dbtalk-postgres/SKILL.md` | 对齐 |
| 标准阶段文档 | 新增 `20261001-restore-clean-full-database.md` 的 Intent、Plan、Verification | 对齐 |
| dump、迁移、安装、发布、现有业务数据库 | 本任务无修改 | 对齐 |

当前工作区另有已暂存的 `scripts/install.py` 和 `tests/test_install.py` 既有改动，不属于本任务；未修改或撤销。任务范围共 13 个新增或修改文件，不以只显示已跟踪文件的 `git diff --stat` 遗漏新增回归测试和过程文档。

## Acceptance checklist

- [x] 仅显式 clean 才全库清理；MySQL CLI true/false 传递及原有默认导入流程由单元测试确认。
- [x] PostgreSQL archive 校验在清理前，清理在导入前；三种路径及 `if_exists` 组合由单元测试覆盖，额外外键场景由隔离 PostgreSQL 实测。
- [x] PostgreSQL 标准 public 重建以及 extra schema、extension、extension 成员事件触发器、用户 cast/access method、publication、subscription、large object 的删除有真实验证记录；备份行和外键恢复成功。
- [x] MySQL 表、视图、函数、过程、事件和额外外键表被清理；gzip 备份中的对象、数据和外键恢复有真实验证记录。
- [x] 清理保留目标数据库及实例级管理边界；MySQL 系统库拒绝由单元测试覆盖，unrelated 数据库 sentinel 在实测后仍为原值。
- [x] 三种路由使用已解析目标、正确引用标识符，密码不进入 argv；单元测试检查命令、URI 和环境。
- [x] PostgreSQL `if_exists` 组合校验保留，导入不再接收原生 clean 参数；实测 clean 有无 if_exists 均可成功，`jobs=2` 工作。
- [x] 两种引擎清理失败停止导入由单元测试确认；psql 单事务参数和非整体原子性经代码与文档核对。
- [x] 聚焦测试、全仓测试、格式、Ruff、Mypy 全部通过；隔离真实恢复通过。
- [x] 用户手册与仓库内 skill 说明清理全部目标对象、备份排除表、psql 依赖及权限和恢复风险。

## Test results

以下是上一轮实施过程中实际运行并收集的结果，本轮未重跑。工作目录均为仓库根目录 `D:\SourceCodes\mywork\dbtalk`。

| 命令 / 操作 | 结果与范围 |
| --- | --- |
| `uv run --locked --no-sync python -m pytest tests/test_restore_clean.py tests/test_postgres.py tests/test_mysql.py tests/test_mysql_logging.py` | 97 passed。 |
| `uv run --locked --no-sync python -m pytest` | 最终 317 passed；全仓测试，包含工作区既有安装测试，并非全部属于 Restore。 |
| `uv run --locked --no-sync ruff format --check src tests scripts` | 最终 62 files already formatted。 |
| `uv run --locked --no-sync ruff check src tests scripts` | All checks passed。 |
| `uv run --locked --no-sync python -m mypy src tests scripts` | Success: no issues found in 63 source files。 |
| `git diff --check` | 最终产品变更检查通过；本轮文档另外检查。 |
| `make check` / `make test` | 当前 PowerShell 与 Git Bash 中未找到 make，未执行 target；执行上述 Makefile 对应命令。 |
| 原始 `uv run ... pytest` / `uv run ... mypy` 入口 | 报 `uv trampoline failed to canonicalize script path`，随后改为已安装模块的 `python -m` 并通过。 |
| 隔离 PostgreSQL 18 实测 | 完整清理并恢复通过，包含 extension 成员事件触发器及 schema 外 cast/access method 的补验。 |
| 隔离 MySQL 8.0.39 实测 | 完整清理并恢复 gzip dump 通过，额外对象被删除，其他数据库数据保留。 |

本轮文档检查结果：`git diff --check` 通过；另以 PowerShell 逐份检查三份新增文档的大标题与紧随其后的时间字段、standard 模式、Review status、相对链接目标及行末空白，全部通过。该检查包含尚未跟踪的新文档，不依赖 `git diff --check` 覆盖它们。

### Real database evidence

真实实验通过临时 Python 脚本调用当前 restore 实现，以 `docker run --detach --rm --network none` 创建隔离容器，未发布宿主机端口。将 mapped-container 选择器定向至该测试容器后，archive 校验、对象清理、native 导入及 Docker 文件操作均实际执行。实验完成后容器、临时备份及脚本已删除；脚本未纳入仓库。

PostgreSQL source 备份包含父表、子表及 business schema 数据；target 预置引用父表的额外表、跨 schema 引用和旧元数据。恢复后父表值为 `1:backup`、子表值为 `10:1`、business 数据为 `saved`，旧 schema、hstore、事件触发器、用户 cast/access method、publication、subscription 和 large object 查询均为 0，外键约束数为 1。随后使用 `if_exists=True` 再恢复，子表行数仍为 1，两次恢复都使用 `jobs=2`。

MySQL gzip 备份包含父子表、视图、函数、过程和事件；target 预置额外外键表及旧对象。恢复后父表值为 `1:backup`、子表值为 `10:1`、saved view 值为 `backup`、saved function 返回 7；target 的表/视图总数为 3、routine 数为 2、event 数为 1、外键数为 1，unrelated 库 sentinel 保持 42。

MySQL 实验最初把初始化临时服务可连接误判为最终就绪，遇到 socket 不存在；调整为确认 PID 1 已运行最终 mysqld 后再连接，最终实测通过。该失败属于隔离脚本的服务就绪判断，未据此改动产品客户端或服务器。

## Missed or expanded scope

- 从 PostgreSQL archive 内对象清理扩大到目标库用户对象，是用户明确要求的完整还原语义；MySQL 新 clean 选项属于同一确认范围。
- PostgreSQL 对 schema 外元数据的清理、标准 public 重建和 extension 成员事件触发器的顺序处理，是落实该语义需要的实现细节。
- 没有重写既有备份或迁移，也没有操作现有业务库、发布 CLI 或执行用户级插件同步。
- 本轮仅补记过程文档，不将书面计划追认为此前审批事实。

## Risks and verification limits

- `--clean` 删除备份之外的对象和被排除的旧表；账号必须拥有相应权限，实际执行还需核对目标库与备份来源。
- PostgreSQL 本机 clean 额外需要 psql；清理可在事务内回滚，清理后的导入仍不是整体原子操作。MySQL DDL 失败可能留下部分清理或恢复状态。
- 实测使用管理账号验证成功路径；权限错误后的停止导入由 mock 测试证明，未在真实库新增清理失败回滚实验。
- 本机和 Docker client fallback 的清理路由由 mock 单元测试验证；真实恢复使用定向的 mapped-container 路径，未覆盖自动端口发现或真实远程 TCP/TLS 连接。
- 本次未新增跨账号 preserve owner/ACL 的真实恢复验证，沿用既有策略和参数实现；也未验证在线并发 DDL 的行为。
- subscription 清理不回收远端 slot；远端复制资源需独立运维。本次验证的是 connect=false、无 slot 的隔离订阅。
- 手工真实实验脚本已清理，仓库保留单元回归；后续需要重新做真实恢复验证时应重新准备隔离环境。

## Incomplete items and conclusion

当前已确认范围的代码、单元测试、活文档和隔离真实恢复均已完成，无已知未完成实现项。上述限制是验证覆盖和执行前提，不把未测试的环境描述为已通过。

本轮完成标准模式过程补记，自动验收结论沿用上一轮证据；Verification 文档仍为 Draft，等待用户审查记录。未暂存、提交或推送本任务变更。
