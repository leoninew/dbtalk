# 授权与权限管理
最后修改时间: 2026-09-01 12:06:49

Review status: Accepted
Flow mode: standard
Stage: Requirement

## Background

当前 MySQL 与 PostgreSQL 都提供独立的 `grant` / `revoke` 命令，但命令只支持固定的 `read-only` 与 `read-write` profile。固定 profile 能覆盖常规应用访问，却无法表达少量、零散的权限需求；另一方面，使用 `dbtalk database exec --write` 执行额外 `GRANT` / `REVOKE` SQL 会绕过权限命令的主体、资源和操作审计语义。

常规权限操作应集中在 `grant` / `revoke`：主体由 `user` / `role` 命令管理，schema/database 由 `schema` 命令管理，`grant` / `revoke` 只负责把权限授予或撤销给明确主体。通用 `database exec` 不作为常规权限管理入口；仅对权限命令未提供专门语义的其他管理 SQL 保留受审核的兜底用途。

## Goal

1. 扩展 MySQL 与 PostgreSQL 的 `grant` / `revoke`，同时支持：
   - 粗粒度、稳定且可审计的常规 profile，例如 `read-only`、`ddl`、`dml`、`read-write`；
   - 由数据库服务端校验的细粒度单项权限，满足零散授权和撤销需求。
2. 保持职责边界：
   - `user` / `role` 只管理登录主体及其生命周期；
   - `grant` / `revoke` 只处理权限；
   - `schema` 命令只处理 MySQL schema/database 与 PostgreSQL schema/database 管理；
   - `database exec` 不作为常规权限管理入口；对于 `grant/revoke` 明确不覆盖的超细粒度对象权限，允许作为管理员审核后的兜底入口。
3. 对 MySQL 与 PostgreSQL 分别表达其主体、资源和原生权限差异，不用一个隐藏方言语义的通用权限模型。
4. 所有目标类型和主体参数均结构化校验；细粒度权限名称作为结构化参数传递，不接受原始 `GRANT` / `REVOKE` SQL 或 SQL 片段。
5. 保留现有安全边界：管理 DSN 明确提供、写操作需要 `--yes`、profile 集合由工具固定维护、输出不泄露凭据。

## Non-goal

- 不通过 `database exec` 绕过常规 profile 或权限变更；权限命令之外的特殊管理 SQL 是否执行，由管理员审核并由数据库自身权限决定。
- 不提供超级用户、系统级高危权限、`WITH GRANT OPTION`、role membership 或代理身份管理能力；细粒度权限名称不由 dbtalk allowlist 限制。
- 不在本需求中管理用户/role 的创建、删除、启用、禁用或密码轮换。
- 不自动修改 PostgreSQL default privileges，除非后续明确增加独立需求。
- 不承诺跨数据库方言完全一致的权限名称或授权效果。

## User scenarios

1. 管理员为 PostgreSQL role 授予 schema 级只读 profile：

   ```bash
   dbtalk postgres grant \
     --dsn-env POSTGRES_ADMIN_DSN \
     --role app_role \
     --schema app \
     --profile read-only \
     --yes
   ```

2. 管理员为 PostgreSQL role 授予 schema `CREATE`，不执行通用 SQL：

   ```bash
   dbtalk postgres grant \
     --dsn-env POSTGRES_ADMIN_DSN \
     --role app_role \
     --schema app \
     --privilege create \
     --yes
   ```

3. 管理员撤销同一项零散权限时使用 `revoke` 的相同结构化参数；撤销不得误删其他 profile 或无关权限。
4. MySQL 管理员向精确的 `user@host` 授予 database 级 profile 或细粒度单项权限；具体权限是否可授予由 MySQL 服务端判断。
5. 管理员使用 `permissions list` / `permissions show` 查看主体当前权限、来源 profile 和细粒度授权，结果不包含凭据。
6. 自动化系统重复执行已审核的 profile 或单项授权；命令输出主体、目标和权限摘要，但不输出密码、DSN 或未请求的权限文本。

## Permission model

### Profile

Profile 是由工具维护的固定权限集合，不允许调用者修改集合内容。至少需要评估以下常规 profile：

- `read-only`：最小只读权限；
- `ddl`：包含 `read-only`，并增加创建或修改数据库对象所需的 DDL 权限；
- `read-write`：包含 `ddl`，并增加常规 DML 权限，但不包含 `CREATE DATABASE`；
- `dml`：包含 `read-write`，并增加 `CREATE DATABASE` 能力；本次变更不承诺旧命令或旧行为的历史兼容。

Profile 按权限集合严格组织为 `dml > read-write > ddl > read-only`，表示前者包含后者的全部权限并增加额外权限。其中 `read-write` 是不含 `CREATE DATABASE` 能力的 `dml` 版本，但仍包含完整的 `ddl` 权限。

`grant` / `revoke` 的 profile 与 privilege 模式互斥：一次调用要么指定 `--profile PROFILE`，要么重复指定 `--privilege NAME`。这样撤销 profile 时不会产生“是否同时撤销额外单项权限”的歧义。

### Fine-grained privilege

细粒度权限不由 dbtalk 维护 allowlist。调用者提供结构化权限名称，工具生成对应方言的授权语句；是否允许该权限由当前管理 DSN 在数据库服务端决定。调用者仍不能传入 SQL 片段、逗号分隔 privilege 字符串或任意完整 `GRANT` / `REVOKE` 语句。

目标原则：每条授权或撤销命令都必须给出一个明确 DSN 和目标 role/user；目标 schema/database 为可选参数，未提供时在当前 DSN 指向的 schema/database 上处理。主体和资源的方言参数仍保持差异：PostgreSQL 使用 `--role` 与可选的 `--database` / `--schema`，MySQL 使用 `--user`、`--host` 与可选的 `--database`。

原生权限名称由数据库校验；dbtalk 不预先拦截当前数据库能够合法处理的细粒度权限。授权失败时直接报告数据库返回的非敏感错误。

常规 profile 的权限集合仍由 dbtalk 固定维护：

- PostgreSQL profile 目标为 database/schema；`dml` 包含数据库原生的建库能力，`read-write` 不包含该能力；具体由数据库方言和管理 DSN 映射到对应原生权限或角色属性；更细的对象级权限可以通过无 allowlist 的细粒度模式传给数据库；
- MySQL profile 目标为 database；细粒度权限由数据库自身权限校验。

## Acceptance

- [ ] MySQL 与 PostgreSQL 均提供同级的 `grant` / `revoke` 权限命令，帮助文本列出 profile 与细粒度权限模式。
- [ ] `grant` / `revoke` 支持 `read-only`、`ddl`、`read-write`、`dml` profile；权限集合严格按 `dml > read-write > ddl > read-only` 包含，`read-write` 是不含 `CREATE DATABASE` 的 `dml` 版本，但包含 `ddl`。
- [ ] profile 模式与 `--privilege` 模式互斥；`--privilege` 可重复指定，不由 dbtalk 维护 privilege allowlist，具体合法性由数据库服务端校验。
- [ ] 每条授权/撤销命令必须提供明确 DSN 和目标 role/user；目标 schema/database 可选，未提供时使用当前 DSN 指向的 schema/database。PostgreSQL 使用 database/schema 资源参数，MySQL 使用 database 资源参数。
- [ ] 撤销 profile 或单项权限只作用于请求的权限集合，不隐式撤销其他 profile 或额外单项授权。
- [ ] 权限命令不接受原始 `GRANT` / `REVOKE` SQL；超出权限命令专门语义的特殊 SQL 才能由管理员审核后单独执行。
- [ ] 提供统一的 `permissions list` / `permissions show` 权限查看命令；查看直接使用 MySQL/PostgreSQL 原生权限查询，原生命令返回什么就展示什么，并确保不泄露连接凭据。
- [ ] 所有 grant/revoke 写操作要求 `--yes`；确认失败、主体不存在、目标不存在、权限不足或参数冲突时不执行写入，并返回稳定且非敏感的错误。
- [ ] MySQL 精确校验 `--user`、`--host`、`--database`；PostgreSQL 精确校验 `--role` 及 `--database` / `--schema`。
- [ ] `--dsn` 与 `--dsn-env` 严格二选一；DSN 密码、密码哈希和其他凭据不出现在帮助、日志、错误或成功输出中。
- [ ] 单元测试覆盖 profile 映射、数据库原生细粒度权限透传、模式互斥、重复 privilege、撤销范围、方言 SQL 构造、确认边界和错误映射。
- [ ] 文档列出 MySQL/PostgreSQL 的命令参数、profile 权限集合、细粒度权限由数据库校验的行为、目标范围和不支持的高危能力。

## Open questions

暂无需要用户确认的未决事项。

## Decisions

- 常规权限操作统一由 `grant` / `revoke` 负责；不再用 `database exec --write` 补充其已覆盖的 profile 或权限。
- 细粒度 privilege 不由 dbtalk allowlist 控制；只要当前 DSN 在数据库侧有权执行，工具就生成并执行对应授权语句。
- 每条授权语句都必须绑定明确 DSN 和目标 role/user；目标 schema/database 可省略，省略时使用当前 DSN 指向的资源。
- `user` / `role` 负责主体管理，不隐式授予权限；`schema` 负责 MySQL schema/database 与 PostgreSQL schema/database 管理，不承担授权逻辑。
- 授权模型采用“固定 profile + 由数据库服务端校验的细粒度 privilege”双层结构。
- 正式 profile 包括 `read-only`、`ddl`、`read-write` 和 `dml`，严格按 `dml > read-write > ddl > read-only` 的权限集合包含关系组织；`read-write` 是不含 `CREATE DATABASE` 能力的 `dml` 版本，但包含完整的 `ddl` 权限，且本次变更不做历史行为兼容承诺。
- `dml` 授予数据库原生的建库能力，`read-write` 不授予该能力；数据库/schema 生命周期仍由 `schema` 命令管理。
- `--profile` 与 `--privilege` 一次调用互斥；细粒度权限可通过重复 `--privilege` 指定。
- 增加统一的 `permissions list/show` 权限查看能力：`list` 默认展示当前 DSN 可见权限，可按主体和 schema/database 筛选；`show` 要求主体，资源筛选可选；两者直接展示数据库原生权限查询结果。
- 以 `schema` 命令替代旧的 `database` 命令；MySQL 与 PostgreSQL 均通过该命令管理 schema/database，旧 `database` 命令移除。
- MySQL 与 PostgreSQL 保留各自主体和资源参数，不强行统一 `user@host` 与 PostgreSQL role。
- 所有 grant/revoke 变更要求显式 `--yes`，并继续遵守 DSN 和凭据保护约定。

## Risk

- profile 与细粒度权限叠加后，撤销语义若设计不清，可能误撤销应用仍依赖的权限或造成权限残留。
- 不维护 privilege allowlist 会把高风险判断交给数据库管理权限；管理 DSN 一旦权限过大，任意细粒度授权都可能扩大访问范围。
- MySQL 与 PostgreSQL 同名权限的语义不同，必须逐方言定义映射并用测试锁定。
- PostgreSQL 未来对象的权限继承涉及 default privileges；当前 profile 只作用于已有对象时，文档必须明确边界。
- 高风险细粒度权限（如 `drop`、`alter`、`execute`）若开放过宽，可能绕过最小权限原则。

## User review notes

- 用户要求使用 Specflow 标准模式记录任务，当前停留在 Requirement，不开始实现。
- 用户要求命令职责清晰：`database` 管理 schema/数据库对象，`grant/revoke` 处理权限，`user/role` 处理用户管理。
- 用户明确认为权限应由 `grant/revoke` 统一处理，不应通过 `grant` 基本 profile 后再用 `exec` 补权限。
- 用户要求同时满足零散 grant/revoke 需求和粗粒度常规 profile（只读、读写、DML 等）能力。
- 用户确认新增正式 `dml` profile，并增加独立 `ddl` profile。
- 用户确认 `read-write` 保持当前 DML 语义，本次变更不做历史兼容。
- 用户确认 `--profile` 与 `--privilege` 互斥。
- 用户确认 PostgreSQL 不下沉到 table/sequence 等对象级细粒度，过细粒度权限由 `database exec` 兜底。
- 用户要求增加 `grant/revoke list/show` 权限查看命令。
- 用户确认采用“大权限包含小权限”的 profile 组织方式，`dml` 包含 `ddl`。
- 用户确认细粒度 privilege 不由 dbtalk allowlist 控制，只要当前 DSN 有权即可执行；授权/撤销必须指定 DSN 和目标 role/user，目标 schema/database 缺省时使用当前 DSN 指向的资源。
- 用户确认使用统一的 `permissions list/show` 查看权限，直接展示数据库原生命令查询结果。
- 用户确认将 `database` 命令改为 `schema` 命令，MySQL 与 PostgreSQL 均使用该命令管理 schema/database，并移除旧 `database` 命令；权限仍由 `grant/revoke` 管理。
- 用户确认 `read-write` 不包含建库权限（`CREATE DATABASE`）。
- 用户采纳 `permissions list/show` 的参数建议：`list` 默认展示当前 DSN 可见权限并支持主体、schema/database 筛选；`show` 要求主体，资源筛选可选。
- 用户进一步明确 profile 严格按 `dml > read-write > ddl > read-only` 包含，`read-write` 是不含 `CREATE DATABASE` 能力但包含 `ddl` 的 `dml` 版本。
