# 用户管理
最后修改时间: 2026-09-01 22:41:50

Review status: Accepted
Flow mode: standard
Stage: Requirement

## 后续设计说明

本记录保留 2026-08-23 首版用户管理需求的事实与验收边界。授权 profile 已由后续的 [授权与权限管理需求](20260901-authorization-grant-revoke.md) 取代，不应将本记录中的双 profile 设计视为当前接口。

当前固定 profile 为 `readonly`、`readwrite`、`migrator`：前两者分别用于查询和常规应用读写；`migrator` 包含 DDL、DML 与建库能力，不添加 `GRANT OPTION` 或角色管理能力。建库在 MySQL 映射为全局 `CREATE ON *.*`，在 PostgreSQL 映射为 role 的全局 `CREATEDB` 属性。

## Background

当前项目可通过 `database exec --write` 执行单条 SQL，但该接口面向已有数据库的数据操作，不适合作为账号管理入口。将 `CREATE USER`、`ALTER USER`、`DROP USER` 或权限授予操作交给原始 SQL，会使密码容易出现在进程参数或脚本中，也无法表达 MySQL `user@host` 与 PostgreSQL role、登录能力和成员关系之间的模型差异。

项目需要受限、可审查的 MySQL 账号和 PostgreSQL role 管理能力，并与通用 query/exec 和数据库生命周期管理保持清晰隔离。

## Goal

1. 为 MySQL 账号和 PostgreSQL 登录 role 提供独立的创建、列出、启用、禁用、轮换密码和删除命令。
2. 以方言明确的 CLI 和数据模型保留 MySQL `username@host`、PostgreSQL role 的差异，不用一个会掩盖权限语义的通用 `user` SQL 接口。
3. 密码只能通过环境变量名等安全引用输入，不能作为普通 CLI 参数、命令输出、日志、错误或测试断言内容。
4. 默认以最小权限创建登录主体；高权限属性、跨账号权限和不可逆删除均须有显式、可审查的边界。
5. 在首版提供常规的授权与撤销能力；授权命令与 MySQL account、PostgreSQL role 生命周期命令同级，且不作为创建主体的隐式副作用。

## Non-goal

- 不通过 `database exec`、原始 SQL、交互式 SQL shell 或多语句脚本管理用户。
- 不管理云厂商 IAM、LDAP、Kerberos、证书认证、操作系统账户或数据库服务启动配置。
- 不在首版暴露 MySQL `SUPER`/全局权限、PostgreSQL `SUPERUSER`/`REPLICATION`/`BYPASSRLS` 等高危能力。
- 不为 MySQL 账号默认为 `%` 主机，不将密码回显给调用者，也不自动生成后保存密码。
- 不在首版实现列级权限、行级安全策略、PostgreSQL role membership、`WITH GRANT OPTION`、MySQL `PROXY` 或任意未在 allowlist 中的权限表达式。

## User scenarios

1. MySQL 管理员创建一个仅允许来自明确主机名或 IP 的应用账号，密码由 `--password-env` 引用的环境变量提供；命令不会显示密码。
2. PostgreSQL 管理员创建一个具备登录能力、默认无高危属性的应用 role，并可通过相同的安全输入机制轮换密码。
3. 管理员列出可见账号或 role 的非敏感属性，以核对是否存在和是否允许登录，而无需读取密码哈希。
4. 管理员删除账号或 role 时以目标身份的显式确认完成操作；尝试删除当前连接管理员或自身必要访问边界时命令在写入前拒绝。
5. 自动化系统使用管理 DSN 和环境变量中的密码执行已审核的账号创建与密码轮换，正常输出仅包含动作、主体名称与方言。
6. MySQL 管理员向明确的 `username@host` 主体授予或撤销其应用所需的常规数据库权限；命令将主体、资源范围和权限集合分别显式输入，不接受原始 `GRANT`/`REVOKE` 文本。
7. PostgreSQL 管理员向明确的 role 授予或撤销常规权限；命令与 role 生命周期命令同级，输出不包含密码、密码哈希或未授权的对象定义。

## Account lifecycle

1. **Provision / 创建**：管理员创建一个可登录主体。MySQL 主体由明确的 `username` 与 `host` 组成；PostgreSQL 主体是具有 `LOGIN` 的 role。创建不默认赋予系统级特权，也不将密码输出给调用者。
2. **Authorize / 授权**：主体在获得应用访问前被授予所需的最小数据库权限。首版提供常规 `grant` 与 `revoke`，作为方言根命令下与 account/role 生命周期同级的命令；创建主体不隐式授权，权限、资源和目标始终显式输入并受 allowlist 约束。
3. **Observe / 检查**：管理员可以列出主体及其非敏感状态，例如 MySQL host、账号锁定状态，或 PostgreSQL 的登录能力；输出不包含密码、密码哈希或 DSN 凭据。
4. **Rotate / 轮换**：管理员从新的 `--password-env` 值设置密码。该动作可能立即使旧凭据失效并中断应用，因此属于高危操作，必须显式提供 `--yes`；应用侧的密钥同步与回滚编排不由本工具自动处理。
5. **Suspend / 禁用**：出现安全事件、迁移或下线准备时，管理员可禁用登录但保留身份和授权，用于可逆地阻断访问。MySQL 使用账户锁定语义，PostgreSQL 使用 `NOLOGIN` 语义；此动作同样要求 `--yes`。
6. **Resume / 启用**：管理员可重新启用此前禁用的主体。该动作恢复其既有认证能力，必须显式提供 `--yes`；不隐式创建密码、恢复已删除的权限或验证应用是否已准备好重新连接。
7. **Retire / 删除**：确认主体已停用且不再被依赖后删除。删除可能影响运行中的连接和依赖对象，必须要求 `--yes`；删除后不承诺恢复或自动重建授权。

## Acceptance

- [ ] CLI 为 MySQL 与 PostgreSQL 提供独立于 `database query/exec` 的账号管理命令，并在帮助中说明其分别管理 MySQL account 与 PostgreSQL role。
- [ ] MySQL 与 PostgreSQL 均提供与 account/role 生命周期命令同级的 `grant` 与 `revoke` 命令；命令不通过创建主体、默认权限或通用 `database exec` 隐式完成授权。
- [ ] MySQL 支持创建、列出、启用、禁用、轮换密码和删除账号；创建账号必须显式指定用户名与一个 `localhost`、单个 DNS 名称、IPv4 或 IPv6 host，首版拒绝 `%` 和其他 host 通配符。
- [ ] PostgreSQL 支持创建、列出、启用、禁用、轮换密码和删除登录 role；创建时默认不授予超级用户、复制、绕过 RLS、建库或建角色等特权。
- [ ] 创建和轮换密码只接受环境变量名称形式的密码输入；环境变量缺失、为空或无效时在执行前失败，任何成功/失败输出、异常链、日志与测试快照均不包含密码值或密码哈希。
- [ ] 授权与撤销只接受结构化的目标主体、资源范围和 allowlist 权限；不接受原始 `GRANT`/`REVOKE` SQL、未审查 privilege 名称或任意权限表达式，创建主体不会隐式授予业务权限。
- [ ] MySQL 授权目标限于单个数据库；PostgreSQL 授权目标限于 database 或 schema，不接受首版之外的 table、sequence 或 function 目标。
- [ ] 首版授权仅提供 `read-only` 与 `read-write` 两个固定 profile；profile 到各方言原生权限的映射在 Spec 中定义，不向调用者暴露任意 privilege 字符串。
- [ ] 用户名、host、role 名等标识符有方言适配的结构化校验与安全引用，不能通过参数注入 SQL；命令不接受原始 `CREATE USER`、`ALTER ROLE` 或 `GRANT` 文本。
- [ ] 启用、禁用、轮换密码、删除、授权和撤销均属于高危操作，必须要求 `--yes`；未提供该参数时不得建立写操作。确认失败、目标不存在、权限不足或目标为当前管理身份时返回稳定的非敏感错误且不执行该动作。
- [ ] 两个方言均通过 `--dsn` 或 `--dsn-env` 严格二选一提供管理 DSN，文档与示例首选 `--dsn-env`；DSN 密码不出现在帮助、日志、错误或命令输出中。
- [ ] 账号管理动作按方言使用正确的事务/autocommit 语义，失败后不声称通用回滚或原子性。
- [ ] 列表使用固定的非敏感字段：MySQL 为 username、host、account locked 状态；PostgreSQL 为 role name、login、createdb 与 createrole 状态；不输出认证插件、密码哈希、连接 DSN 或其他凭据。
- [ ] 单元测试覆盖参数契约、环境变量读取、密码脱敏、身份与 host 验证、删除确认、当前主体保护、方言 SQL 构造和错误映射；真实服务测试由显式环境条件控制。
- [ ] 文档说明所需数据库权限、MySQL host 语义、PostgreSQL role 语义、密码注入方式、删除风险和首版不支持的高危能力。

## Open questions

暂无需要用户确认的未决事项。

## Decisions

- 用户和 role 管理必须使用新命令，不向 `database exec` 增加管理参数。
- MySQL account 与 PostgreSQL role 不强行收敛为一个屏蔽方言差异的抽象；CLI、验证和文档应明确其语义。
- 密码以环境变量名输入，不允许密码明文作为 CLI 值。
- 首版默认最小权限，不提供高危系统级权限。
- 账号生命周期与数据库生命周期保持独立 Requirement；常规授权与撤销纳入首版，但不得成为创建主体的隐式副作用。
- 授权与撤销作为 `dbtalk mysql`、`dbtalk postgres` 下与 account/role 生命周期同级的命令，不增加通用 `dbtalk admin` 根命令。
- 首版授权资源范围为 MySQL 单个数据库，以及 PostgreSQL database 或 schema；table、sequence 和 function 目标不在首版范围内。
- 首版授权模型固定为 `read-only` 与 `read-write` profile；该历史设计已由后续的 `readonly`、`readwrite`、`migrator` 模型取代。
- MySQL 公开 CLI 使用 `user`，PostgreSQL 使用 `role`；两端均使用同级的 `grant` 与 `revoke`。
- MySQL 首版仅接受单个精确 host，不接受 `%`、`_` 或其他通配 host，避免在创建时扩大连接来源。
- 列表仅输出固定的账号定位与登录能力字段，不输出密码哈希、认证插件或凭据关联信息。
- 启用、禁用、密码轮换和删除属于高危账号动作，统一使用 `--yes` 作为非交互确认边界。
- CLI 按方言根命令组织：MySQL account 管理位于 `dbtalk mysql`，PostgreSQL role 管理位于 `dbtalk postgres`，不新增统一的 `dbtalk admin` 根命令。

## Risk

- 管理 DSN 和密码环境变量均为高敏感信息；错误处理、日志、子进程调用和测试输出必须持续验证脱敏。
- MySQL 与 PostgreSQL 的账户、密码策略、认证插件和权限模型差异很大；错误地抽象会导致权限被意外放大或产生不可移植行为。
- 删除正在被应用使用的账号、轮换未同步到应用的密码或撤销连接权限都会导致服务中断；命令需要明确确认边界和可操作的失败信息，但不能自动补偿。
- "当前连接管理员" 的可靠识别依赖方言查询和代理/代入身份语义，需在实现阶段为每个方言单独验证。

## User review notes

- 用户要求记录用户管理需求，并要求不要将管理能力与查询、更新等日常数据库操作混在一起。
- 用户要求数据库管理和用户管理作为两个独立需求进行跟踪。
- 用户确认采用方言根命令，以保留 MySQL account 与 PostgreSQL role 的不同对象模型。
- 用户确认首版必须包含常规授权能力，且授权命令与用户/role 生命周期命令同级。
- 用户确认 MySQL 数据库级、PostgreSQL database/schema 级资源范围，`read-only` / `read-write` profile，及 MySQL `user` 命令命名。
- 用户要求切换到 standard 模式并开始 Implementation；standard 模式不创建独立 Spec，设计决策在 Plan 中固定。
