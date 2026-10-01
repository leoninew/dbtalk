# Restore 显式 Clean 完整还原意图
最后修改时间: 2026-10-01 12:20:27

Review status: Accepted
Flow mode: standard / 标准模式
Stage: Intent / 意图

## Record basis

本文按用户“标准记录本任务”的要求，在实现和验证完成后补记。内容依据本次会话已确认的需求、当前代码与实际验证结果；不表示实施前存在本文或发生过独立的文档审批。用户已明确选择“仅显式 --clean 时完整还原”，本轮记录覆盖标准模式的 Intent、Plan 和 Verification，省略 Spec。

## Background

PostgreSQL restore 原先把 `--clean` 直接交给 `pg_restore`。原生命令只删除 archive 中的对象；如果目标库多出的表通过外键引用待删除的表，DROP 会失败。即使解除该依赖，保留额外对象也不能使目标库的业务内容回到备份状态。

MySQL 的原生 dump 默认在导入 SQL 中关闭外键检查，通常不会以相同的 DROP 错误表现出来，但同样只重建备份内的对象。额外表、视图或存储程序会留在目标库，额外表上的外键还可能引用恢复后不再存在的数据。

用户希望从备份完整还原库内业务内容，且不会被目标库已有的额外表及外键关系阻止。用户随后明确要求这种清理仅在显式 `--clean` 时启用，两种 restore 的默认行为不改变。

## Goal

1. 将 PostgreSQL restore 的 `--clean` 收敛为先清空目标库用户业务对象，再用原生 archive 恢复。
2. 为 MySQL restore 增加相同意图的显式 `--clean`，先清理目标库当前业务对象，再导入 SQL 或 gzip SQL 备份。
3. 清理覆盖备份之外的旧对象，正确处理外键及对象依赖；对象枚举或清理失败时停止导入。
4. 保持已有目标选择、native client 执行路径、凭据传递、PostgreSQL owner/ACL 策略与并行恢复能力。
5. 同步用户手册与仓库内 Agent skill，并通过主要成功流程及真实高风险失败边界验证行为。

## Non-goal

- 不默认清空目标库，不新增持久化的清理配置，不改变 dump 格式或 dump 排除表的行为。
- 不删除或重新创建目标数据库，不改变数据库本身的属性，不管理实例级账号或授权。
- 不提供物理备份、WAL/PITR、cluster 全量恢复，也不承诺结构、数据、owner 和 ACL 在任何环境下无条件完全相同。
- 不清理其他数据库，不连接远端复制服务回收 replication slot。
- 不承诺整个 restore 的原子回滚，不安装、发布 CLI 或同步到用户级插件目录。

## User scenarios

1. 目标库新增了一张引用备份内父表的子表。管理员显式执行 restore `--clean`，旧业务对象被清理，备份中的结构、数据和约束恢复成功。
2. 备份使用 `--exclude-table` 排除了某张表。完整还原时，该表在目标库中的旧版本也被清理，恢复结果不额外保留它。
3. 管理员不提供 `--clean`，继续使用原有导入流程，不发生新增的全库对象清理。
4. 目标库对象清理权限不足或输入预检失败，命令返回失败，实际导入不会继续执行。

## Acceptance

- [ ] 两种引擎只在显式 `--clean` 时执行全目标库业务对象清理；MySQL CLI 及运行 options 完整传递该标志。
- [ ] PostgreSQL archive 校验先于清理；非系统 schema 及其对象使用 `CASCADE` 清理，备份之外的外键依赖不会阻止恢复。
- [ ] PostgreSQL 清理覆盖用户 extension、事件触发器、用户 cast/access method、publication、subscription、外部数据包装器、自定义过程语言和 large object，保留 `plpgsql` 并重建标准 `public` schema。
- [ ] MySQL 枚举目标库全部表、视图、存储函数、存储过程和事件，在同一清理会话内关闭外键检查；表上的触发器随表删除。
- [ ] 不删除目标数据库或实例级账号、授权；MySQL 对系统数据库拒绝 `--clean`。
- [ ] 三种执行路径使用同一个已解析目标库；本机、映射容器、Docker 回退均支持清理，密码不进入 command argv。
- [ ] PostgreSQL `--if-exists` 仍要求 `--clean`，作用于清理阶段；恢复继续支持原有 owner/ACL 策略及 `--jobs`。
- [ ] 清理失败时停止导入；PostgreSQL 清理使用单事务，MySQL 清理与整个 restore 的非原子性质准确写入文档。
- [ ] 单元测试和静态检查通过；隔离 PostgreSQL 18 与 MySQL 8.0.39 验证额外外键场景及备份数据、约束恢复。
- [ ] 用户手册、Agent skill 与上述当前行为一致。

## Open questions

暂无需要用户确认的未决事项。测试覆盖的实际限制和运维前提记录在 Verification，不以未验证的路径替代真实执行证据。

## Decisions

- 用户已选择“仅显式 --clean 时完整还原”，不把全库清理设为 restore 默认行为。
- “完整还原”针对本命令支持的库内逻辑业务对象；数据库本身及实例级资源保持既有边界，owner/ACL 仍受 preserve 选项控制。
- 清理依据目标库当前对象目录，不能仅依据备份对象列表，也不能仅以 DROP `CASCADE` 或关闭外键检查替代清空目标业务对象。
- 清理沿用原有客户端优先级：唯一映射容器、本机客户端、配置的 Docker client image。
- 本机 PostgreSQL `--clean` 需要 `psql` 和 `pg_restore`；不存在 `psql` 时明确失败，不静默跳过清理。

## Risk

- `--clean` 会删除备份之外的对象，包括 dump 排除的旧表；必须核对目标库和备份来源。
- 账号需要目标库全部对象的清理权限，PostgreSQL 还需重建 schema 及相关对象管理权限。
- PostgreSQL 清理成功后，后续导入失败仍可能留下部分恢复状态；MySQL DDL 还可能留下部分清理状态。
- PostgreSQL 清理 subscription 会禁用订阅并解除 slot 关联；远端 slot 的回收仍由复制管理流程负责。

## User review notes

- 用户首先指出 PostgreSQL Clean 仅 drop 特定表，非目标表的外键引用导致失败，不符合预期。
- 用户继续询问 MySQL 是否有相同问题，并要求“将备份后的库完整还原下来，不应该因为这些外键关系导致问题”。
- 用户明确选择“仅显式 --clean 时完整还原”。
- 实现与验证结果已经在会话交付后，用户要求使用 SpecFlow 标准模式记录本任务。
