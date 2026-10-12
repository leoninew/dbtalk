# PostgreSQL 两类所有权转移命令意图
最后修改时间: 2026-10-12 23:18:40

Review status: Accepted
Flow mode: standard / 标准模式
Stage: Intent / 意图

## Background

用户要求两个独立命令：一个保留 PostgreSQL 原生命令语义，另一个转移业务对象所有权并排除系统及扩展成员。用户已接受本文范围，并要求将原设计文档转为标准模式的 Intent，进入 Plan。实施不重复操作已修复的本地数据库。

现有 `owner reassign` 使用 `REASSIGN OWNED`，预览依赖 `pg_shdepend`。PostgreSQL 不记录对 pinned 系统角色的普通依赖，并拒绝以这些角色为源执行 `REASSIGN OWNED`。因此本地 `postgres` 持有 77 张业务表时，现有预览仍为空。这个限制针对系统角色，并非所有超级用户。

本地实例已用限定清单的 `ALTER TABLE ... OWNER TO ...` 完成修复，证明业务对象可以从 `postgres` 转移。业务表的索引、TOAST 存储等关联对象随表转移，并不需要直接修改系统目录。

两个命令都转移 owner，不提供 ACL 复制、grant/revoke、角色成员关系或 default privileges 迁移；现有 `postgres grant` 继续负责授权。

## Goal

- 保留 `postgres owner reassign` 的原生语义，提前识别不支持的系统源角色，纠正误导性的空预览。
- 提供独立的 `postgres owner transfer`，转移明确数据库 / schema 范围内的业务对象所有权，支持以初始化角色为源。
- 排除系统对象、extension 及其成员；同时核查自动 owner 传播，保证排除规则覆盖实际执行影响。
- 预览展示对象及计划 SQL，显式 `--yes` 后在单个事务中执行，失败完整回滚且错误脱敏。

## Non-goal

- 不复制或撤销 ACL，不修改 default privileges、角色成员关系或账号属性。
- 不修改 `transfer` 范围外的 database / tablespace owner 或其他数据库对象。
- 不执行任意 SQL，不为原生命令增加 schema 限定，不引入自动切换执行策略的兼容路径。
- 不修改已修复的 K12 数据库，不发布或安装 CLI，不调整其他方言和备份脚本。

## User scenarios

1. 管理员使用原生 `reassign` 查看普通源角色的当前库及共享对象，再按范围显式确认执行。
2. 管理员使用 `transfer` 从 `postgres` 转移业务表及其他受支持对象，同时保持系统和扩展归属。
3. 管理员通过重复 `--schema` 限定业务范围，先核对待转移、附属、排除和不支持的对象，再执行事务内转移。

## 命令边界

| 项目 | `postgres owner reassign` | `postgres owner transfer` |
| --- | --- | --- |
| 实现 | PostgreSQL 原生 `REASSIGN OWNED` | 按计划执行逐对象 `ALTER ... OWNER TO ...` |
| 当前数据库 | 源角色在该库的原生转移范围 | 该库中源角色持有的业务对象 |
| schema 筛选 | 不支持，原生命令不能限定 schema | 可重复的 `--schema`；省略时检查全部非系统 schema |
| 系统角色作为源 | 提前拒绝并指向 `owner transfer` | 支持，包括初始化角色 `postgres` |
| 系统对象 | 遵循原生语义 | 排除内置系统对象 |
| extension | 遵循原生语义 | 排除 extension 本身、成员及其附属对象 |
| database / tablespace owner | 可能影响 cluster 共享对象，须 `--include-shared` | 不改变任何 database 或 tablespace owner |
| 默认行为 | 预览 | 预览对象清单与计划 SQL |
| 写入 | 显式 `--yes`，共享对象另行确认 | 显式 `--yes`，整份计划在一个事务内执行 |

`transfer` 的 database 参数只确定连接和检查范围，不代表转移 database 自身的 owner。需要单独改变某个 database owner 时，继续使用明确目标的 `ALTER DATABASE ... OWNER TO ...`。

## 参数与使用

两个命令沿用 `--dsn` / `--dsn-env` 二选一、必需的 `--database`、`--from-role`、`--to-role` 和 `--yes`。Agent 继续使用当前目录已有的 `DBTALK_DSN_*` 与 `--dsn-env`，不回显凭据。

原生命令保留 `--include-shared`。新命令只增加可重复的 `--schema`，不提供改变排除规则或允许共享对象的开关，也不接收任意 SQL。

```bash
# 原生命令：先预览，再按实际共享对象范围确认执行。
dbtalk postgres owner reassign --dsn-env DBTALK_DSN_POSTGRES_ADMIN \
  --database app --from-role old_owner --to-role new_owner

# 新命令：省略 --schema 时检查当前库的全部业务 schema。
dbtalk postgres owner transfer --dsn-env DBTALK_DSN_LEON_K12_DELIVERY_OS \
  --database k12_delivery_os --from-role postgres --to-role k12_delivery_os

# 限定 public，只转移源角色持有的对象。
dbtalk postgres owner transfer --dsn-env DBTALK_DSN_LEON_K12_DELIVERY_OS \
  --database k12_delivery_os --schema public \
  --from-role postgres --to-role k12_delivery_os --yes
```

显式指定的 schema 必须存在且属于允许的业务范围。系统 schema 参数应报错，不能给出空计划并成功执行。源和目标 role 必须存在且不同；连接后核对 `current_database()`。

## 原生命令调整

- 在查询依赖预览前识别 PostgreSQL 18+ 的初始化及内置系统角色。识别依据角色身份 / OID，不能仅判断名称 `postgres` 或属性 `rolsuper`；初始化角色重命名后仍应识别，用户自行创建的超级用户不应被一概拒绝。
- 预览和执行均对这类源角色报出可理解的错误，说明原生命令不支持，并给出 `owner transfer` 的用途，不输出误导性的零对象预览。
- 普通角色继续使用原生命令。明确展示 database / tablespace 等共享对象的名称和数量，保留 `--include-shared` 的显式确认。
- PostgreSQL 原生命令决定实际范围，不把 schema 筛选或系统对象排除参数加入该命令。

## 业务对象计划

### 对象范围

首版支持 schema、普通及分区表、外部表、view、materialized view、sequence、function、procedure、aggregate，以及自定义 enum、domain、composite、range 类型。

所有候选对象都必须由 `--from-role` 持有。`public` 是允许的业务 schema；其 owner 若为 `pg_database_owner`，源角色为 `postgres` 时不会强行转移 schema。其他角色持有的对象不转移。

索引、表行类型、自动数组 / multirange 类型、TOAST 对象和表拥有的序列应归入所属对象的自动影响范围，避免生成不可独立执行或重复的 ALTER。分区表及其业务分区分别纳入检查，不能假定父表的 owner 变更会覆盖所有分区。

无 schema 的其他独立库内对象（如 publication、large object），以及首版未实现的业务对象类型，不默认为已处理。未指定 `--schema` 的数据库范围预览需列出这些对象，并阻止执行；指定 schema 时它们明确不在请求范围。后续按实际需求扩展对象类型。

### 排除规则

1. 排除 `pg_*`、`information_schema` 等系统 schema 中的独立对象，及其他内置系统对象；不以源角色是否为超级用户判断对象是否属于业务。
2. 用 `pg_depend` 的 extension membership 关系识别成员，排除 extension 本身、成员及其自动关联对象。不能只按名称或 extension 所在 schema 判断；同一 schema 中的非成员业务对象仍按规则检查。
3. 在生成计划时分析自动 owner 传播。如果一个业务对象的 ALTER 会间接改变被排除的 extension 成员，报告冲突并阻止执行；仅从 SQL 清单删除该成员不能保证排除有效。
4. 自动随业务表转移的 TOAST、索引和行类型属于该表的存储与类型组成，应显示为附属影响，不当作可独立转移的系统对象。
5. 不改变数据库、表空间或其他数据库中的对象，不直接 UPDATE PostgreSQL 系统目录。

### 预览与执行

- 从 `pg_namespace`、`pg_class`、`pg_proc`、`pg_type` 等真实 owner 字段建立候选清单；业务对象计划不依赖 `pg_shdepend` 的 owner 记录，因此可处理初始化角色。
- 计划条目保存目录类别、OID、schema、对象标识、源 owner、附属影响和计划 SQL；输出待转移、被排除及不支持的对象和原因。空计划明确输出无需转移。
- 标识符沿用方言 identifier preparer 或 PostgreSQL 的结构化引用；函数 / 过程 / aggregate 使用服务端身份参数与对象标识处理重载，类型使用完整 schema 限定，不拼接调用方原始 SQL。
- 执行账号权限和目标角色的必要权限在预检及实际 DDL 中核验，不自动授予权限、创建角色或临时提升账号属性。目标 schema 将按计划转给目标 role 时，预检应考虑该计划的结果。
- 使用本次调用在同一事务内生成的计划：先处理必要的 schema，再处理独立对象及其依赖顺序。结合对象锁重新核验身份和 owner，发现对象消失、替换或归属冲突时回滚。
- 并发新增对象不自动扩大已生成的计划；结果核对已执行计划中的对象，不承诺此前预览与后续执行之间数据库没有变化。
- 任意 ALTER 失败时整个事务回滚，并保留对象上下文及清洗后的错误；成功输出明确说明已转移的独立对象数量。

## 代码与文档边界

- `src/dbtalk/postgres/owner.py` 保留 owner 命令组及原生路径，增加明确的 transfer 入口；对象计划可放入同包的 `owner_transfer.py`，避免将大量目录查询和 DDL 构造塞入 CLI handler。
- 复用现有 DSN 解析、标识符校验、SQLAlchemy engine 生命周期、事务及 `DatabaseOperationError` 脱敏约定，不新增跨方言管理框架。
- 按实际实现同步 README、`docs/postgres.md` 及仓库内 `plugins/dbtalk/skills/dbtalk-postgres/SKILL.md`。实现前不将草案命令写成已发布能力，不自动安装或发布 CLI。
- 保留已有无关工作树改动；不重新修改本地 K12 数据库、不调整备份脚本。

## Acceptance

- 两个命令的参数与作用范围符合本文边界；默认只预览，`--yes` 才写入；`transfer` 支持可重复的 `--schema`。
- 原生路径按系统角色身份拒绝 pinned 源角色，不将用户创建的超级用户误判为不支持；共享对象仍须单独确认。
- 业务路径支持本文列出的首版对象类型，正确处理重载、附属对象和自动 owner 传播；排除系统、extension 和成员，未支持的范围内对象阻止执行。
- 核对数据库、角色、schema 和执行权限；错误或并发身份冲突导致整份计划回滚，连接凭据不回显。
- 用户文档与 Agent skill 说明两个命令的差异，不将 owner 转移与授权混用；项目检查和隔离 PostgreSQL 实验提供证据。

实现后运行 Makefile 已有 Ruff format/check、Mypy 与 pytest 入口；缺少 make 时执行其等价命令。所有文档中的 shell 命令示例统一使用 Bash。

单元验证重点是命令分流、默认只预览、必需参数、标识符引用、排除原因、计划执行和失败回滚。隔离 PostgreSQL 18+ 实验至少证明：

1. 初始化角色及其重命名后的身份被原生命令提前拒绝，业务 transfer 能正确发现其表；普通用户创建的超级用户不被按属性拒绝。
2. 表、分区、独立 / 表拥有的序列、view、重载函数与自定义类型按计划转移，关联索引 / TOAST / 行类型正确随 owner 变化。
3. 系统对象、extension 成员及其附属对象、其他 schema 或 role 的对象、共享对象均保持约定范围；间接触及 extension 成员的冲突阻止写入。
4. 中途权限不足导致整份计划回滚，且错误不泄露 DSN；成功后目标角色可访问代表性表。

隔离实验不以已修复的 K12 数据库作为写入测试目标。

## Open questions

暂无需要用户确认的意图未决事项。系统角色标识、各类 ALTER 的附属 owner 传播、对象锁和不支持对象目录的完整性属于实施前技术核实项，由 Plan 安排；若核实结果要求改变本文承诺，先同步修改文档。

## Risks and assumptions

- 沿用当前 PostgreSQL 18+ 支持基线，不新增旧版本兼容分支；执行账号具备相应管理权限。
- 表的 owner 变更可能传播到索引、TOAST、行类型及拥有的序列；extension 排除必须检查这类间接影响。
- PostgreSQL 的 owner-bearing 对象目录多于首版支持类型；特别是初始化角色无法通过普通共享依赖完整枚举，不能将遗漏对象视为转移成功。
- 预览为目录快照，单次事务中的对象锁与复核不能保证两次调用之间没有并发 DDL；执行范围以本次计划为准。
- 成功提交后旧 owner 的隐式权限变化已生效，代码回滚不会自动恢复数据库所有权。

## User review notes

- 用户已接受两类命令、首版对象范围及排除策略。
- 用户本轮要求切换标准模式，将原 `docs/spec/20261007-postgres-owner-transfer.md` 移为本文，并开始 Plan；本轮只调整阶段文档，不进入实现。
- 2026-10-12：从原会话恢复本文及 Plan。用户要求开始实施计划；业务库权限修复命令由用户自行执行，本轮不连接或修改 K12 数据库。
- 2026-10-12：用户要求完成验证文档；整体结果见同名 [Verification](../verification/20261007-postgres-owner-transfer.md)，本轮沿用已通过的开发检查及隔离实验记录完成核对。
