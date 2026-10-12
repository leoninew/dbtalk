# PostgreSQL 两类所有权转移命令验证
最后修改时间: 2026-10-12 23:18:40

Review status: Accepted
Flow mode: standard / 标准模式
Stage: Verification / 验证

## Intent alignment

依据已接受的 [Intent](../intent/20261007-postgres-owner-transfer.md) 核对：保留原生 `postgres owner reassign`，增加独立的 `postgres owner transfer`。前者提前拒绝 pinned 系统源角色并补充共享对象明细；后者按数据库 / schema 范围从真实 owner 字段枚举业务对象，支持初始化角色作为源，排除系统对象、extension 及其成员和自动附属对象。

两条命令默认只预览，显式 `--yes` 才写入。`transfer` 展示对象、自动影响、排除项、阻塞项与计划 SQL，在单个事务中执行并核对结果；不改变 database / tablespace owner，不复制 ACL、成员关系或 default privileges。用户业务数据库未被本会话连接或修改，权限修复命令由用户自行执行。

## Plan alignment

依据已接受的 [Plan](../plan/20261007-postgres-owner-transfer.md) 核对六个实施步骤：PostgreSQL 18 目录及自动 owner 传播已核实，原生路径完善、业务对象计划、事务执行、CLI / 文档和测试均已完成。`owner.py` 保持 CLI 与原生操作边界，新增 `owner_transfer.py` 承载目录及业务执行服务，复用现有 DSN、标识符、错误脱敏和 SQLAlchemy 生命周期约定。

技术核实参照 PostgreSQL 18 的 `IsPinnedObject`、`ATExecChangeOwner` 和 `AlterTypeOwnerInternal`。只建模已核实的自动 owner 传播边，不将任意对象依赖视为 owner 传播。关系对象使用适用的事务锁，其他对象结合服务端 DDL 锁与执行前后目录复核；不承诺冻结所有并发 DDL。

## Task deliveries and integration

不适用。本功能未建立 task 或分配子 agent；实现及整体核对均在同一会话完成。验收基线为本功能当前工作区相对于 `HEAD` 的 diff，包含已暂存变更；未执行提交或发布。

## Actual diff summary

- `src/dbtalk/postgres/owner.py`：原生系统角色预检、共享 database / tablespace 的名称及 owner、错误信息收敛，以及 transfer 子命令和计划展示。
- `src/dbtalk/postgres/owner_transfer.py`：typed 对象计划、真实 owner 目录枚举、schema 范围、扩展保护、自动影响及阻塞项、DDL 构造、权限预检、事务锁与执行前后复核。
- `tests/test_postgres_owner.py`、新增 `test_postgres_owner_transfer.py` 和 `test_postgres_owner_integration.py`：原生回归、业务计划 / CLI 及 9 项 opt-in PostgreSQL 集成测试。
- `README.md`、`README.zh-CN.md`、`docs/postgres.md`、仓库内 PostgreSQL skill：同步两类命令、支持范围、权限和执行边界；shell 示例统一使用 Bash。
- 同名 Intent / Plan：恢复原会话中已接受的设计与计划，记录实施、开发检查及进入 Verification；本文新增整体验收记录。

## Expected vs actual changed files

| 预期边界 | 实际修改 | 结果 |
| --- | --- | --- |
| 原生 owner 与业务 transfer | 修改 `owner.py`，新增 `owner_transfer.py` | 对齐；owner 命令组已注册，无需修改 `postgres/cli.py` |
| 行为测试及隔离 PostgreSQL 实验 | 修改原生测试，新增 transfer 单元测试和 integration 文件 | 对齐 |
| 用户与 Agent 文档 | README、PostgreSQL 手册、PostgreSQL skill，并同步中文 README | 中文 README 为必要的文档同步 |
| Intent / Plan / Verification | 同名三份文档 | 对齐 |
| 临时权限修复材料 | Git 忽略的 `data/repair-k12-delivery-os-owner.sql` | 本地辅助文件，不作为产品功能或发布资产 |
| 其他方言、备份、迁移、业务库、安装发布 | 无变更或操作 | 对齐 |

## Acceptance checklist

| 验收项 | 证据 | 结果 |
| --- | --- | --- |
| 两类命令分开；必需 database / 两个 role；默认预览、显式执行；transfer 可重复 schema 参数 | CLI 单元测试及真实 CLI dotenv / 双 schema 集成测试 | 通过 |
| 原生拒绝系统角色，重命名后仍识别，普通用户创建的超级用户允许；保留共享对象确认 | 原生单元测试及角色重命名 / 普通超级用户集成测试 | 通过 |
| 首版对象类型、重载及正确引用 | 支持类型组合 fixture 含引号表名、重载 function、procedure、普通及 ordered-set aggregate、enum / domain / composite / range | 通过 |
| 自动 owner 传播不重复 ALTER；分区分别处理 | 集成核对表、分区、serial / identity、索引、TOAST、行类型、数组 / multirange 和独立 sequence | 通过 |
| 排除系统和扩展成员，保持其他 owner / schema 及共享对象边界 | 计划单元测试、同 schema 扩展 / 业务对象 fixture、系统 schema 与 database / tablespace owner 前后核对 | 通过 |
| 自动传播触及扩展成员时阻止写入 | extension-owned serial sequence 集成测试 | 通过 |
| 范围内不支持对象明确阻塞；schema 范围省略无 schema 对象 | operator / publication 计划测试，publication 数据库 / schema 范围集成测试 | 通过 |
| 执行及目标角色权限预检，非超级用户可按权限转移 | 缺少目标 schema CREATE 时写入前失败；非超级用户转移 schema / 表并由目标角色读取数据 | 通过 |
| 任意 DDL 失败整体回滚，归属 / 扩展关系变化明确失败 | 事件触发器拒绝最后一条 ALTER 后核对此前 owner 恢复；DDL 中新增 extension membership 后 owner 和 membership 一并回滚 | 通过 |
| 身份复核、错误上下文与凭据清洗 | 执行路径核对 OID / identity / owner / extension / 自动传播边；原生错误脱敏测试和 transfer 的统一错误 API | 通过；并发压力实验不在本次证据中 |
| 数据内容与目标访问、空计划 | 支持类型 fixture 保持文本内容及函数返回值，目标角色读写权限按 PostgreSQL 判断；重复执行得到空计划 | 通过 |
| 文档与 skill 说明 owner 和授权的区别，静态检查与回归提供证据 | 公开文档 diff、Ruff / Mypy / pytest 和本文命令记录 | 通过 |

## Command evidence

以下质量检查和数据库实验已在本会话的 Implementation 阶段实际执行。本轮仅补齐验收文档与核对 diff，没有重新运行测试、创建数据库或启动容器；不将历史记录写成本轮新运行结果。

| 命令 / 操作 | 实际结果 |
| --- | --- |
| `uv run --locked --no-sync ruff format --check src tests scripts` | 66 files already formatted |
| `uv run --locked --no-sync ruff check src tests scripts` | All checks passed |
| `uv run --locked --no-sync python -m mypy src tests scripts` | Success: no issues found in 67 source files |
| `uv run --locked --no-sync python -m pytest -q` | 374 passed, 11 skipped, 5 subtests passed |
| 启用隔离端口后的 owner 聚焦测试，见下方 Bash 命令 | 32 passed，包含 9 项真实 PostgreSQL 集成测试 |
| `uv run --locked --no-sync dbtalk postgres owner transfer --help` | 退出码 0；参数及 scope 与文档一致 |
| `make check` / `make test` | 本地没有 make，执行了 Makefile 对应的锁定 uv 命令；pytest / mypy 使用同一环境的 `python -m` 入口 |
| 隔离 PostgreSQL 18 | 使用已有 `postgres:18-alpine` image，端点仅绑定回环地址；fixture 校验 `server_version_num >= 180000` |
| 本地修复 SQL 的隔离实验 | 关系对象及关联对象 owner 转移、hstore owner 保持、目标读取数据通过；扩展拥有的序列冲突在写入前中止 |
| 临时资源清理 | 仅停止并自动删除 `dbtalk-owner-test-20261012`；清理后容器清单中不存在该容器 |

完整回归中的 11 个跳过项包括 9 个默认不启用的 owner 集成测试、既有 symlink 测试和既有依赖外部数据库的 JSONL 测试；owner 集成测试已通过独立聚焦运行，不把默认跳过视为数据库行为验证。完整回归后 `_inventory` 的等价推导式简化由最终静态检查及 32 个 owner 聚焦测试验证。

聚焦运行的 Bash 等价命令如下，端口必须指向独立准备的 PostgreSQL 18 trust-auth 测试实例。原临时实例已清理，此命令不是对当前端点可用性的承诺：

```bash
DBTALK_OWNER_TEST_PORT=6277 uv run --locked --no-sync python -m pytest -q \
  tests/test_postgres_owner.py \
  tests/test_postgres_owner_transfer.py \
  tests/test_postgres_owner_integration.py
```

`DBTALK_OWNER_TEST_PORT` 是非凭据测试配置；集成 fixture 用 SQLAlchemy 的结构化 URL 创建本地连接，不读取业务 DSN。只有真实 CLI dotenv 测试在 pytest 临时目录写入独立的 `.env`，未修改仓库 `.env`。

## Scope deviations

- 无产品功能范围扩张。额外同步 `README.zh-CN.md`，并准备本地、Git 忽略的权限修复 SQL；后者只覆盖 `public` 的关系对象并授予 schema USAGE / CREATE，不能当作完整 transfer 的实现。
- 隔离 service fixture 使用非凭据端口配置和结构化 URL，取代 Plan 最初描述的统一临时 dotenv DSN；真实 CLI dotenv 解析另有集成测试。未暴露凭据或连接用户业务库。
- 权限不足由预检在写入前拒绝；中途回滚使用真实事件触发器 DDL 失败验证，未人为制造不可达的权限状态。
- 未修改其他方言、备份脚本、已执行迁移、用户级 plugin；未安装、提交或发布。

## Risks and limits

- 支持基线为 PostgreSQL 18+；真实实验使用 PostgreSQL 18，未验证其他 major 版本。范围内不支持的 owner 对象会阻止执行。
- 预览不能冻结两次调用之间的目录。当前证据覆盖 DDL 中的扩展关系变化，未进行任意并发对象替换或高负载 DDL 的压力实验；执行前后复核不扩张已生成计划。
- owner 转移会持有 DDL 锁，已有事件触发器仍会运行；数据库事务不能撤销用户回调的外部副作用。
- `transfer` 不配置 ACL 或 default privileges。成功提交后需另行明确操作才能恢复原 owner，不自动反向转移。
- 业务库权限修复及发布后 CLI 的安装可用性未验收；本次结论仅针对当前源码和隔离实验。

## Incomplete items and conclusion

本功能约定的实现、公开文档、质量检查、回归及隔离 PostgreSQL 验收都有对应证据，无已知未完成的实现项。用户要求完成验证文档后，本轮核对当前 diff 并完成标准模式的 Verification 记录；测试与数据库实验沿用本会话实际通过记录，未重复操作业务数据库。

## User review notes

- 2026-10-12：用户要求实施恢复的计划，并明确权限修复命令由其自行执行。
- 2026-10-12：用户要求所有资料文档使用 Bash，相关示例及流程文档已统一。
- 2026-10-12：用户要求“完成验证文档”，本轮进入整体 Verification，完成意图 / 计划对齐、验收证据、范围差异和风险记录。
