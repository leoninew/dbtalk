# PostgreSQL 两类所有权转移命令计划
最后修改时间: 2026-10-12 23:18:40

Review status: Accepted
Flow mode: standard / 标准模式
Stage: Verification / 验证

## Intent basis

依据已接受的 [Intent](../intent/20261007-postgres-owner-transfer.md)。保留原生 `postgres owner reassign`，增加独立的业务对象 `postgres owner transfer`，后者排除系统对象、extension 与成员，并核查自动 owner 传播。

本文于 2026-10-07 创建，2026-10-12 从原会话恢复。用户要求开始实施后完成了 Implementation 及开发检查；随后要求完成验证文档，进入整体 Verification，结果见同名 [Verification](../verification/20261007-postgres-owner-transfer.md)。

## Implementation baseline

- `src/dbtalk/postgres/owner.py` 已拥有 owner 命令组、`OwnershipPreview` 和 `reassign_owned()`；通过显式数据库创建 SQLAlchemy engine，在一个事务中查询 `pg_shdepend` 并执行原生命令。
- `_preview()` 只校验 role 名称存在，未识别系统角色。pinned 源角色的普通依赖未记录，导致空预览；`--include-shared` 目前只保护查询到的共享对象。
- `src/dbtalk/postgres/cli.py` 已注册 owner 命令组，新增子命令可在该组内完成，不需添加其他根入口。
- `tests/test_postgres_owner.py` 使用模拟连接和 Click runner 覆盖原生预览、确认、引用、事务及脱敏；没有业务对象目录查询或真实 ALTER 的验证。
- 当前质量入口为 Makefile 的 `check` / `test`，对应 Ruff format/check、Mypy 和 pytest；这次是 Python CLI 变更，不使用 Go / 前端检查入口。

## Implementation steps

### 1. 核实 PostgreSQL 18+ 的目录和执行行为

- 对照支持基线的 PostgreSQL 目录与实现，确定 pinned 系统角色的身份 / OID 判断，覆盖初始化角色重命名，避免以名称或 `rolsuper` 分类。
- 建立首版对象类型与 owner-bearing 目录映射，核实 schema、关系、routine、type 的 ALTER 语法和 owner 字段；核实 aggregate 身份参数、range / multirange、表行类型和数组的自动归属。
- 列明可按 owner 检查但首版不支持的目录，包括 schema 内的 operator、operator class/family、collation、conversion、text search 配置 / dictionary、extended statistics，以及无 schema 的 publication、subscription、FDW/server、language、event trigger、large object。按当前数据库和请求 schema 限定；default ACL、成员关系等非目标不作为待转移对象。
- 核实 extension membership 与附属对象关系，以及表 owner 对索引、TOAST、序列、行类型的实际传播。记录明确的边和对象类别，不对所有 `pg_depend` 关系做无差别递归。
- 核实关系对象可用的锁，以及其他 ALTER 的服务端锁与目录复核时机。若实际行为不能满足已接受的排除或原子承诺，先更新 Intent / Plan，不静默缩小范围。

产出：实施所需的系统角色判断、目录映射、身份构造、自动 owner 传播及锁策略。核实所需的写入实验仅使用临时隔离 PostgreSQL 资源。

### 2. 完善原生 reassign 路径

- 在对象依赖预览前读取源 / 目标角色身份，提前拒绝 pinned 源角色，预览与执行给出相同的可理解错误，并提示使用 `owner transfer`。
- 普通源角色继续执行原生 SQL；保留正确的 role 引用、数据库复核、单事务、错误脱敏和 engine 释放。
- 保留当前目录数量统计，为共享 database / tablespace 增加名称和当前 owner 明细；`--yes` 和 `--include-shared` 的原生确认规则不改变。
- 原生命令不接受 schema 限定，不自动切换到业务对象 ALTER。

### 3. 建立业务对象计划

- 新增同包 `owner_transfer.py`，承载目录查询、对象计划和业务执行服务；`owner.py` 负责 Click 接口及结果展示。不引入跨方言框架。
- 定义小规模的 typed 数据结构，保存目录类别 / OID、kind、schema、完整对象身份、源 owner、待执行 SQL、自动关联对象、排除原因及不支持原因。所有状态来自对象检查结果，不增加持久化配置。
- 从真实 owner 字段枚举，不依赖初始化角色缺失的 `pg_shdepend` 所有权依赖。校验数据库和角色，使用绑定参数筛选显式 schema；不存在或属于系统范围的显式 schema 报错。
- 候选集仅包含源角色持有、在请求范围的对象。识别 extension 自身、直接成员及其附属对象；排除独立系统对象，保留与业务表绑定的 TOAST 等附属影响。
- 分开标记直接 ALTER 的独立对象和随其转移的对象：各业务分区独立检查，表拥有的序列 / 行类型 / 索引、类型自动数组 / multirange 归入附属清单。
- 检查自动传播是否触及被排除的 extension 成员、其他角色对象或请求范围外对象；产生冲突时列出具体对象并阻止执行，不悄悄扩展授权范围。
- 不支持的范围内独立对象在预览中明确列出，并使 `--yes` 在任何 ALTER 前失败。未指定 schema 时检查无 schema 的其他库内 owner 对象；指定 schema 时它们明确不在范围。extension 自身分类为排除，不能误列为不支持。

### 4. 生成 SQL 并执行单事务计划

- 为首版受支持类型生成固定 ALTER 模板；schema / role / 对象名称使用方言引用或服务端结构化引用，函数、过程与 aggregate 使用服务端身份信息区分重载；类型身份须 schema 限定。
- 展示待转移、自动关联、排除及不支持清单与计划 SQL；明确空计划。预览不执行 ALTER。
- `--yes` 在一个连接和事务中生成本次计划、检查阻塞项、执行权限及目标 schema 的必要权限；若 schema 将转给目标角色，权限预检考虑该计划结果，不额外 GRANT 或提升角色属性。
- 以确定顺序处理必要 schema、独立类型和其他对象，按已核实的 owner 传播规则合并附属变化；避免对已随父对象变化的序列 / 类型重复 ALTER。
- 关系对象通过适用的事务锁保护并复核 OID、身份、owner 和 extension 关系；其他对象依赖已核实的服务端 DDL 锁及执行前后复核。对象被替换、归属冲突或排除关系变化时使事务失败。
- 执行结束核对计划中的独立及自动关联对象 owner，以及受排除对象的归属；任何差异回滚。并发新增对象不纳入原清单，也不通过重新扫描扩大执行范围。
- 失败错误包含当前对象上下文并统一脱敏，任意 DDL 错误向外传播以回滚全部计划；成功后报告独立转移数量，释放 engine。

### 5. 接入 CLI 并同步公开文档

- `owner.py` 增加 `transfer` 子命令，沿用 DSN 二选一、必需 database / 两个 role、默认预览和显式 `--yes`，增加可重复的 `--schema`。
- `transfer` 不暴露 `--include-shared`，不修改任何 database / tablespace owner。帮助和结果文案明确这是对象所有权转移，授权仍使用 `grant`。
- 更新 README 命令表、PostgreSQL 手册和仓库内 PostgreSQL skill；写明首版支持类型、不支持对象的失败行为、间接影响、系统角色边界及事务和并发限制。
- 不安装 CLI、不同步用户级 plugin、不发布制品、不修改历史阶段材料或已执行迁移。

### 6. 补齐行为测试并交付实现

- 在现有原生测试中覆盖系统角色提前拒绝、重命名后判断、普通超级用户允许、共享对象明细，以及原有参数 / 事务 / 引用 / 脱敏行为。
- 新增独立 transfer 测试，覆盖 default preview、schema 选择、初始化源角色真实 owner 枚举、extension 排除与传播冲突、不支持对象在 ALTER 前阻止执行、SQL 身份引用、成功执行及中途失败回滚。
- 对真正由 PostgreSQL 决定的 owner 传播、catalog 和权限行为准备隔离验证，避免仅用模拟 SQL 断言宣称这些行为已成立。
- 完成 Implementation 后汇报实际 diff、已运行 / 未运行检查、已知风险和范围差异。按标准模式停在实现阶段；用户要求进入 Verification 后才执行独立验收并创建同名 Verification 文档。

## Files to change

| 文件 | 预期改动 |
| --- | --- |
| `src/dbtalk/postgres/owner.py` | 原生系统角色预检、共享对象明细、transfer 子命令及预览展示。 |
| `src/dbtalk/postgres/owner_transfer.py`（新增） | 业务目录枚举、对象计划、排除与传播检查、DDL 构造、事务执行及结果复核。 |
| `tests/test_postgres_owner.py` | 原生路径回归、系统角色判断、两类入口及 CLI 确认行为。 |
| `tests/test_postgres_owner_transfer.py`（新增） | 业务计划、选择 / 排除、执行、回滚及错误行为测试。 |
| `tests/test_postgres_owner_integration.py`（按隔离环境需要新增） | 使用显式 integration 标记和环境约束的 PostgreSQL 实验，不访问真实业务 DSN。 |
| `README.md`、`docs/postgres.md` | 命令入口、区别、范围、权限和执行限制。 |
| `plugins/dbtalk/skills/dbtalk-postgres/SKILL.md` | Agent 的两类 owner 操作边界和确认规则。 |
| 本功能 Intent / Plan | 本轮迁移已接受文档、记录计划；实现核实产生范围差异时同步。 |
| 同名 Verification | 仅在进入验收阶段后创建，记录实际结果。 |

owner 组已注册，预计无需调整 `postgres/cli.py`。复用现有 DSN、校验和错误 API；若必须修改其他基础模块，先说明必要性并调整计划边界。保留无关工作树变更。

## Verification plan

### 项目检查

所有文档中的 shell 命令示例统一使用 Bash。在仓库根目录使用已有 `make check` 与 `make test`。若环境中没有 make，使用 Makefile 对应的命令：

```bash
uv run --locked --no-sync ruff format --check src tests scripts
uv run --locked --no-sync ruff check src tests scripts
uv run --locked --no-sync mypy src tests scripts
uv run --locked --no-sync pytest
```

聚焦测试使用 `tests/test_postgres_owner.py` 和新增 transfer 测试。若已安装模块的可执行入口失效，可使用同一锁定环境的 `python -m ruff` / `python -m mypy` / `python -m pytest`；不安装或升级依赖来绕过问题。完成后核对 `git diff --check` 与实际文件范围。实施阶段的实际检查结果见下文 Implementation results。

### 隔离 PostgreSQL 验证

- 优先使用已有 PostgreSQL 18+ Docker image 创建临时实例；SQLAlchemy / psycopg 需要实际可连接的独立端点，可用仅绑定回环地址的临时映射端口，并确认连接确实落在隔离实例。网络及连接方案在执行时核实，不指向已有 K12 库，也不启动或重启项目开发服务器。
- 测试 DSN 只通过当前目录临时的独立 `DBTALK_DSN_*` 引用，不回显凭据；结束后仅清理本任务创建的连接项、临时角色 / 数据库 / 容器及制品，不覆盖现有 `.env` 项或其他资源。
- 使用少量组合 fixture：源 / 目标 / 非目标角色，目标及 sentinel 库，两个业务 schema，以及含数据、索引、TOAST、serial / identity、分区、view / materialized view / foreign table、重载 routine / aggregate、自定义类型的对象。
- 在支持的临时实例中用现有 extension 或可控 fixture 验证成员排除，以及 extension schema 中非成员业务对象的处理；对真实可创建的间接传播冲突建立验证，不为不可达关系编造断言。
- 用初始化角色及其重命名场景证明原生路径的预检和业务路径的枚举；用普通用户创建的超级用户证明限制不是 `rolsuper`。
- 核对每个支持类型的代表对象及附属 owner，确认其他 role / schema、共享和系统对象保持约定范围；数据内容不因 owner 操作变化，目标角色可读代表性业务表。
- 对范围内不支持对象、实际权限失败和身份冲突核对明确失败及回滚。权限预检可覆盖的失败应在写入前拒绝；中途异常另用可控 fixture 证明先前 ALTER 回滚。
- 默认 pytest 不依赖外部服务；集成检查使用仓库已声明的 `integration` 标记和明确的隔离配置。验收记录包含 server 版本、实际调用范围、前后核对及资源清理结果。

## Blockers and technical checks

当前没有阻塞项。步骤 1 的技术核实已完成，涵盖 pinned 角色标识、aggregate / range 身份、owner 自动传播及非关系对象的锁与复核。

已使用本地现有 `postgres:18-alpine` image 完成隔离实验。本地环境缺少 Make，使用 Makefile 等价的锁定 uv 命令完成检查；pytest / mypy 可执行入口不可用，改用同一环境的 `python -m` 入口。

## Assumptions and risks

- 沿用 PostgreSQL 18+、同步 canonical DSN 和既有 identifier 校验；不引入旧版本兼容层。
- `transfer` 只改变请求范围内源 owner 的对象，不复制 ACL；目标账号的实际权限仍由 PostgreSQL 判断，工具不临时提升权限。
- 不支持对象的枚举是重要完整性边界，初始化角色不能依赖 `pg_shdepend`；遗漏一个 owner-bearing 目录可能造成错误的成功结论。
- owner 传播可能跨出直接 SQL 清单。需要把附属影响纳入预览、冲突检查和结果复核，而不是仅过滤独立对象名称。
- 事务可能持有较强 DDL 锁；业务执行需选择合适窗口，失败不能通过终止其他连接或扩大对象范围处理。
- 两次调用间的预览无法冻结数据库；并发对象替换或 extension 变化必须明确失败或核对回滚，不能宣称任意并发 DDL 下对象清单恒定。
- 不自动禁用或改写现有事件触发器；若触发器导致 DDL 失败则回滚，工具也不承诺控制用户回调产生的外部副作用。

## Rollback

实现代码回滚范围仅限本功能的 owner 模块、测试及当前用户 / Agent 文档；不回退其他工作树变更或历史阶段材料。

运行失败由单事务回滚本次计划。成功提交后的 owner 转移不能靠回滚 CLI 代码恢复；需要根据执行前保存的对象身份、原 owner 和附属影响形成新的明确操作，重新预览并确认。不自动执行反向转移。

## User review notes

- 用户已接受原设计，并明确要求标准模式、将其转为 Intent、开始 Plan。
- 2026-10-07：本 Plan 为 Draft，尚未开始产品实现或验收。
- 2026-10-12：用户要求实施原计划，Plan 标记 Accepted。权限修复 SQL 已提供给用户自行执行；产品实现和隔离开发检查由本会话完成，不修改业务库。
- 2026-10-12：用户要求完成验证文档；本轮核对实际 diff 及已有检查证据，完成整体 Verification。

## Implementation progress

- [x] 恢复已接受 Intent 及原 Plan，按本轮授权进入 Implementation。
- [x] 核实 PostgreSQL 18 的 owner 传播与对象目录。
- [x] 完善原生 reassign 并实现业务 transfer。
- [x] 补充测试、公开文档和 skill。
- [x] 运行项目检查及隔离 PostgreSQL 验证，交付实现。
- [x] 按后续授权完成整体 Verification 文档及阶段核对。

## Implementation results

- 原生 `reassign` 在目录预览前按角色 OID 识别 pinned 系统角色，兼顾初始化角色重命名与普通用户创建的超级用户；补充共享数据库 / tablespace 的名称及 owner，并收敛数据库错误信息。
- 新增 `owner transfer`：支持可重复 `--schema`、默认预览、显式 `--yes`、真实 owner 目录枚举、扩展成员排除、明确的自动 owner 传播、不支持对象阻止执行、权限预检、单事务及执行前后复核。数据库 / tablespace owner 和 ACL 不属于该命令范围。
- 核实依据包括 PostgreSQL 18 官方实现中的 `catalog.c` / `IsPinnedObject`、`tablecmds.c` / `ATExecChangeOwner`、`typecmds.c` / `AlterTypeOwnerInternal`；dbtalk 产品实现仍为 Python。
- Ruff format 和 Ruff check 通过；Mypy 检查 67 个源文件通过。完整 pytest 为 `374 passed, 11 skipped, 5 subtests passed`，跳过项包含默认不启用的 9 个 owner 集成测试。
- 设置独立的 `DBTALK_OWNER_TEST_PORT` 后，owner 聚焦测试为 `32 passed`，其中 9 个真实 PostgreSQL 集成测试覆盖各支持对象和自动归属、重命名系统角色、schema 范围、不支持对象、扩展传播冲突、非超级用户权限、事件触发器失败回滚和 CLI 预览 / 执行。
- 权限修复脚本保存在 Git 忽略的本地文件 `data/repair-k12-delivery-os-owner.sql`，已给用户自行执行。在隔离数据库验证了关系对象转移、扩展 owner 保持、目标角色读取数据，以及扩展序列冲突时写入前中止。该脚本只覆盖 `public` 中的关系对象并授予 schema USAGE / CREATE，不代替完整 transfer。
- 未连接或修改用户业务数据库，未改动 `.env`、安装 CLI、同步用户级 plugin、提交或发布。文档范围额外同步了 `README.zh-CN.md`。
- 临时容器 `dbtalk-owner-test-20261012` 已停止并自动删除，确认容器清单中不存在；没有操作其他 PostgreSQL 服务。
- 上述检查在 Implementation 阶段执行；整体对齐、验收证据及验证限制已记录于同名 [Verification](../verification/20261007-postgres-owner-transfer.md)。
