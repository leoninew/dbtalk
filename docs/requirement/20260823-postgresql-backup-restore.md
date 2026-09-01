# PostgreSQL 单库逻辑备份与还原需求
最后修改时间: 2026-08-31 15:52:55

---
Review status: Accepted
Flow mode: standard
Stage: Requirement
---

## Background

`dbtalk` 已提供 `dbtalk mysql dump` 和 `dbtalk mysql restore`，以原生 MySQL 客户端创建和恢复单库 SQL 备份。PostgreSQL 目前只支持通用 SQL 操作和 JSONL 数据传输；JSONL 传输要求目标 schema 预先存在，且不覆盖 PostgreSQL 原生逻辑备份的对象和恢复语义。

项目需要为单个 PostgreSQL 数据库提供常规、可移植的逻辑备份与还原能力。该能力应保持与 MySQL 命令一致的 DSN、输出路径、凭据保护和错误处理原则，同时保留 PostgreSQL archive 的原生语义。

## Goal

1. 新增 `dbtalk postgres dump`，使用原生 `pg_dump` 创建单个 PostgreSQL 数据库的逻辑备份。
2. 新增 `dbtalk postgres restore`，使用原生 `pg_restore` 恢复该备份到明确指定的既有目标数据库。
3. dump 默认生成 `pg_dump --format=custom` 的 custom archive；默认输出为 `data/<database>-<timestamp>.dump`。
4. custom archive 使用 PostgreSQL 原生 archive 内部压缩；不对 `.dump` 再套 gzip，也不将 MySQL 的 `--archive` 语义复用到该默认格式。
5. dump 和 restore 均通过完整的 `postgresql+psycopg://` DSN 或 `--dsn-env` 提供连接；密码不得出现在子进程参数、正常输出、日志或错误摘要中。
6. 提供受控的恢复选项，至少覆盖显式清理既有对象、清理时忽略不存在对象、所有权/权限策略和 custom archive 的并行恢复。
7. 复用项目现有 MySQL 命令的输出路径约定：省略 `--output` 时创建配置的输出目录；已有目录生成时间戳文件；其他路径视为文件路径且父目录必须存在。
8. 优先使用本机 PostgreSQL 客户端；本机客户端缺失时，回退到配置指定且本地已存在的 PostgreSQL 18+ Docker image。不会自动安装客户端或拉取 image。
9. 当 DSN 指向本机地址且请求端口唯一映射到运行中的 PostgreSQL Docker 容器时，优先复用该容器执行 dump/restore，使用容器默认 Unix socket；未识别到唯一映射容器时才按客户端回退规则执行。

## Non-goal

- 不实现 `pg_basebackup`、WAL 归档、时间点恢复（PITR）、流复制或任何物理备份能力。
- 不执行 `pg_dumpall`，不备份或恢复 role、role password、tablespace、实例配置和其他 cluster 全局对象。
- 第一版不支持 directory、tar 或 plain SQL 备份格式，不支持为 custom archive 再额外生成 `.gz` 文件。
- 不在第一版支持按 schema/table 选择、仅 schema、仅数据、section、触发器禁用、指定 restore role 或任意原生客户端参数透传。
- 不默认创建或删除目标数据库；不尝试把 custom archive 中的源数据库名重写为其他目标数据库名。
- 不自动拉取 Docker image、安装 PostgreSQL 客户端或根据服务端版本猜测 Docker image。
- 不修改既有 `dbtalk mysql` 或 JSONL export/import 的行为与格式。

## User scenarios

1. 管理员通过 `--dsn-env` 为一个应用 PostgreSQL 数据库创建 `.dump`，并得到最终输出文件路径与非敏感结果摘要。
2. 管理员向一个已存在、明确指定的目标数据库恢复 `.dump`；默认不删除目标中的对象，需显式选择清理选项才允许覆盖恢复。
3. 管理员将备份恢复到不同账号管理的环境，选择跳过 archive 中的 owner 和权限定义，避免 role 不存在导致恢复失败。
4. 管理员针对较大的 custom archive 指定恢复并发数，以 `pg_restore` 的原生并行能力加快恢复。
5. 调用方传入损坏、非 PostgreSQL archive、路径不存在或格式不受支持的输入时，命令在写入前明确失败。

## Acceptance

- [ ] `dbtalk postgres dump` 与 `dbtalk postgres restore` 只接受且必须接受一个 `--dsn DSN` 或 `--dsn-env NAME`；DSN 必须是明确的 `postgresql+psycopg://` 形式。
- [ ] 无本机映射容器时 dump 优先调用本机 `pg_dump`，以 custom format 写入 `.dump`；默认文件名和 `--output` 目录/文件判定与现有 MySQL dump 一致。
- [ ] 本机 `pg_dump` 或 `pg_restore` 缺失时，仅使用配置的、已经存在的 PostgreSQL Docker image 作为对应客户端回退；Docker 不可用、image 缺失或回退失败时返回清晰错误并清理临时容器和制品。
- [ ] 本机 `localhost`/`127.0.0.1` 端口唯一映射到运行中 PostgreSQL 容器时，dump/restore 及 archive 预检均通过该容器的 `docker exec` 执行，使用默认 Unix socket；没有唯一映射时才使用本机客户端或临时 Docker client。
- [ ] Docker fallback 支持配置 PostgreSQL client image，默认使用 PostgreSQL 18 image；用户负责配置与源服务端兼容的更高 major image。
- [ ] custom archive 直接作为 `pg_restore` 输入；不需要也不接受外层 `.gz` 包装。
- [ ] dump 提供受限的 native archive 压缩级别控制；未指定时使用 PostgreSQL 客户端的默认 archive 压缩行为。
- [ ] restore 在执行前验证输入文件存在且可由 `pg_restore` 识别；格式或 archive 读取失败时不开始目标数据库恢复。
- [ ] restore 默认不传递清理选项；仅在用户显式指定时使用 `--clean`，且 `--if-exists` 仅可与清理语义组合使用。
- [ ] restore 默认跳过 owner 和 ACL/privilege 定义，支持显式 preserve 模式，以及为 custom archive 设置正整数恢复并发数；不支持的输入格式或并发值明确失败。
- [ ] restore 发生 native client 错误时返回非零状态，并采用 fail-fast 语义；文档明确恢复并非整体原子操作，失败后目标数据库可能处于部分恢复状态。
- [ ] 子进程命令参数、CLI 输出、日志和错误信息不包含 DSN 密码；临时凭据制品在命令结束后清理。
- [ ] 单元测试覆盖命令构造、输出路径、custom archive 输入校验、危险选项约束、凭据脱敏和本机客户端缺失时的诊断；真实 PostgreSQL 测试仅在显式提供环境时执行。
- [ ] README、PostgreSQL 使用手册和 Codex skill 说明命令、custom archive 语义、恢复风险、客户端依赖和未覆盖的 cluster 全局对象。

## Open questions

暂无需要用户确认的未决事项。Docker 回退、owner/ACL 默认策略和版本基线已在 Decisions 中固定。

## Decisions

- 范围限定为单数据库逻辑备份/还原，底层工具为 `pg_dump` 与 `pg_restore`；不采用 SQLAlchemy 或 JSONL 作为备份实现。
- 默认格式采用 custom archive（`-Fc`）。它是供 `pg_restore` 直接读取的 PostgreSQL 二进制 archive，具有原生内部压缩，不能等同于 MySQL 的 gzip SQL 文件。
- MySQL 的 `--archive` 仅表示输出 `.sql.gz`，不适用于默认 PostgreSQL custom archive；不在本功能中引入同名的误导性开关。
- 目标数据库必须已存在，且由 restore 的 DSN 明确指定。`pg_dump -C` / `pg_restore -C` 会按 archive 内的源数据库名创建数据库，不能安全地等价为“恢复到 DSN 指定的另一目标库”，故不纳入第一版。
- `--clean` 是显式的破坏性恢复选项，默认关闭；它清理对象而非删除数据库。
- 对本机端口唯一映射的运行中 PostgreSQL 容器，复用容器内原生客户端和默认 Unix socket；archive 通过临时文件复制进出容器并在完成后清理。无唯一映射时仍使用本机客户端，再回退到配置 image。
- Docker fallback 使用配置的本地 PostgreSQL 18+ client image，默认值为 `postgres:18`；无本机映射容器时本机客户端优先。image 不存在时失败而不自动拉取，用户可在配置中设置与源库兼容的更高 major image。
- restore 默认使用 `--no-owner --no-privileges`，并通过显式 preserve 模式恢复对应 archive 定义。
- 第一版以 PostgreSQL 18+ 为主流版本基线；不为旧 major 版本设计兼容分支。`pg_dump` client 仍必须不低于源服务端 major，恢复目标通常不应低于备份来源。
- PostgreSQL 的函数、视图、索引、约束、序列和扩展声明属于常规逻辑 dump 对象，不单独模仿 MySQL 的 routine/event 开关。role 和 tablespace 属于 cluster 全局对象，明确排除。
- 不接受原生参数透传，避免绕过凭据保护、破坏性选项审核和跨版本兼容约束。

## Risk

- `pg_dump` 和 `pg_restore` 是外部客户端依赖；客户端缺失时 Docker fallback 仍要求 Docker 可用且已存在与源服务端兼容的 image。客户端版本不兼容或权限不足时必须给出可诊断错误，不能伪装成应用层错误。
- custom archive 通常已压缩，但大库仍可能占用大量磁盘、网络和恢复时间；恢复并发还会提高目标实例负载。
- 含 extension 的数据库要求目标实例安装兼容的 extension；role、ACL、owner 和 tablespace 不具备无条件跨环境可移植性。
- `--clean` 或 archive 中的 DDL/DML 会改变目标数据库；即使 fail-fast，也无法承诺所有对象在失败后自动恢复到执行前状态。
- 备份客户端和目标数据库的 PostgreSQL major version 组合需要在实现与集成验证时覆盖；不能只以单元测试推断真实 archive 兼容性。

## User review notes

- 用户明确排除物理备份与还原，仅需要常规、通用的单库逻辑备份与还原。
- 用户要求参考现有 MySQL dump/restore 的受控 CLI 设计，而不是暴露 `pg_dump` 的全部参数。
- 用户确认 custom archive 是默认方向，并采纳“默认 `.dump` 不再额外 gzip”的理解。
- 用户要求继续聚焦 PostgreSQL dump/restore，并明确进入 Plan 阶段；未决项转入 Plan 的假设和风险，不阻塞计划草拟。
- 用户确认 Docker 回退，要求基于 PostgreSQL 18+ 的较新版本实现并支持 image 配置；确认 restore 默认跳过 owner/ACL；当前 PostgreSQL 使用版本为 18+，不涉及旧 major 兼容。
- 用户要求按 MySQL 的本机 Docker 容器复用规则修复 PostgreSQL dump/restore。
