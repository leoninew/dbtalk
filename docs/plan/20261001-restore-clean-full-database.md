# Restore 显式 Clean 完整还原计划
最后修改时间: 2026-10-01 12:20:27

Review status: Accepted
Flow mode: standard / 标准模式
Stage: Plan / 计划

## Intent basis

依据 [Intent](../intent/20261001-restore-clean-full-database.md) 及用户已确认的显式清理策略补记实施路径。本计划创建于代码和验证完成后，不追认为实施前审批；用户本轮要求记录全部标准阶段，本文描述实际采用的设计与实施步骤。

## Implementation steps

1. 理解当前 native restore 行为及边界。核对两种 CLI、restore options、客户端执行与现有测试，确认 PostgreSQL 原生 `--clean` 的对象范围和 MySQL dump 的外键检查行为。
2. 实现 PostgreSQL 独立清理阶段。在 archive 校验成功后，通过同一路径的 `psql` 对已解析目标库清理，再调用 `pg_restore`；停止向导入命令传递原生 `--clean` / `--if-exists`。
3. 实现 MySQL 显式清理。新增 CLI 与 options 的 `clean`，在输入及目标库预检后枚举目标库对象，生成临时清理 SQL，在关闭外键检查的单个会话内删除对象，然后执行原有导入。
4. 补齐回归测试。覆盖三种客户端路由、阶段顺序、目标库选择、标识符引用、凭据 argv、gzip 输入、清理失败后的停止导入和系统库保护；调整 PostgreSQL 原生命令及无效 archive 的旧断言。
5. 同步手册与 Agent skill。说明显式策略、全目标库范围、被排除表的清理、客户端依赖、权限要求及非原子性质。
6. 执行项目质量检查及隔离真实恢复实验，对照最终 diff 记录验收结果。

## Design

### PostgreSQL

- `PostgresRestoreOptions.clean` 控制独立清理阶段，`if_exists` 只为清理 SQL 的 DROP 添加 `IF EXISTS`，原有参数组合校验保留。
- `psql` 使用 `--no-psqlrc`、`ON_ERROR_STOP=1` 与 `--single-transaction`；清理失败回滚并停止后续 `pg_restore`。
- 先禁用事件触发器，避免清理 DDL 执行旧回调；extension 先于其剩余成员触发器删除，防止直接删除 extension 成员失败。
- subscription 先禁用、设置 `slot_name = NONE`，再删除当前库订阅，不触发远端 slot 删除。
- 枚举非 `pg_*` 且非 `information_schema` 的 schema，一次执行 `DROP SCHEMA ... CASCADE`，清除旧表、外键、视图、序列、函数和类型等依赖。
- 清除可能位于 schema 之外的用户 cast/access method、publication、外部数据包装器、自定义过程语言与 large object；保留 initdb 对象和 `plpgsql`。
- 重建 `public`，owner 为 `pg_database_owner`，恢复标准 comment 和 `PUBLIC` 的 USAGE；原生 dump 通常假定这个 schema 已存在。
- 使用原有 `.pgpass` 或 `PGPASSWORD` 环境传递凭据，清理与导入使用相同 libpq 目标；`--jobs` 和 owner/ACL 选项继续由 `pg_restore` 处理。

### MySQL

- `MysqlRestoreOverrides` 与 `MysqlRestoreOptions` 增加 `clean: bool = False`，不引入新的持久化配置。
- 输入解压、生命周期 SQL 预检和顶层 USE 重写仍先执行；目标库存在性探测成功后才进入 `clean` 阶段。
- 从 `information_schema.EVENTS`、`ROUTINES`、`TABLES` 按 `DATABASE()` 查询对象，以逐行 `JSON_OBJECT` 返回 kind/name，使用 JSON parser 读取。
- 生成数据库限定、正确引用反引号的 DROP；按事件、存储程序、视图、表顺序执行，全部 DROP 位于 `FOREIGN_KEY_CHECKS=0` 与恢复为 1 的同一会话中。
- 清理 SQL 写入临时文件，经现有 native client 的 stdin 导入，完成或失败后移除临时文件，原始备份不修改。
- `mysql`、`information_schema`、`performance_schema`、`sys` 拒绝显式清理；枚举或 DROP 失败就停止导入。
- 清理使用原有本机、mapped container 或 Docker fallback 目标及 `MYSQL_PWD` 环境；默认导入行为保留。

## Files to change

| 文件 | 目的 |
| --- | --- |
| `src/dbtalk/postgres/restore.py` | 独立全库对象清理、psql 路由、停止原生 clean 参数透传。 |
| `src/dbtalk/postgres/cli.py` | 更新 clean 的帮助语义。 |
| `src/dbtalk/mysql/restore.py` | clean options、系统库保护、对象枚举与临时清理 SQL、三个执行分支。 |
| `src/dbtalk/mysql/cli.py` | 增加并传递显式 `--clean`。 |
| `tests/test_postgres.py` | 更新 native argv 断言，覆盖 clean 下的无效 archive。 |
| `tests/test_restore_clean.py` | 新增两种引擎的清理回归。 |
| `docs/mysql.md`、`docs/postgres.md` | 当前 CLI 契约、清理范围及运维限制。 |
| `plugins/dbtalk/skills/dbtalk-mysql/SKILL.md`、`plugins/dbtalk/skills/dbtalk-postgres/SKILL.md` | Agent 操作边界同步。 |
| 同名 Intent、Plan、Verification | 按本轮用户要求补记标准过程文档。 |

## Verification plan

- 优先使用仓库 `make check` 与 `make test`。若本机没有 make，执行 Makefile 对应的 Ruff format/check、Mypy 和 pytest；若虚拟环境入口失效，通过 `python -m` 调用已安装模块，不安装或升级依赖。
- 聚焦测试：`tests/test_restore_clean.py`、`tests/test_postgres.py`、`tests/test_mysql.py`、`tests/test_mysql_logging.py`。
- 全仓测试与静态检查通过后，核对 `git diff --check` 和最终变更边界。
- 隔离 PostgreSQL 18：source 创建备份，target 含额外跨 schema 外键、视图、函数、extension、事件触发器、cast/access method、publication、subscription 与 large object；clean 恢复后核查旧对象删除、备份行及外键恢复。分别验证有无 `--if-exists`，保留 `jobs=2`。
- 隔离 MySQL 8.0.39：target 含额外外键表、视图、存储函数、存储过程和事件；从 gzip dump 清理恢复，核查备份对象、数据、外键及 unrelated 库的 sentinel。
- 真实测试使用无网络、无宿主机端口映射的临时容器；直接定向 mapped-container 选择器至隔离容器，其余 restore 与 native client 调用实际执行。测试后删除临时容器和制品。

## Blockers

功能实现无未解决阻塞。验证环境中 make 不可用，部分 uv 可执行入口报 `uv trampoline failed to canonicalize script path`；已使用 Makefile 的等价直接命令及 `python -m` 完成检查，具体结果见 Verification。

## Assumptions

- 沿用 PostgreSQL 18+ 的支持基线和标准 `public` schema 的原生 dump 行为，不为旧版本增加兼容分支。
- 执行账号可见并有权管理全部目标业务对象；命令不替用户提升权限。
- restore 的输入是可信备份，目标库已存在；清理不是任意 SQL 文件的数据库重建工具。
- 执行期间应处于合适的维护窗口；对象枚举不提供并发业务 DDL 的隔离保证。

## Risks

- 显式 clean 的范围扩大到备份之外，旧的排除表不会保留。
- PostgreSQL 本机路径新增 `psql` 依赖，缺失时明确失败；复杂对象还可能需要更高管理权限。
- 清理和恢复属于两个阶段，无法承诺整体原子性；MySQL 清理本身还受到非事务 DDL 的限制。
- subscription 清理解除 slot 关联，可能留下需要另行回收的远端复制资源。

## Rollback

代码层回滚本任务涉及的 restore、CLI、测试和用户/Agent 文档变更，移除 MySQL 新选项并恢复 PostgreSQL 原生 clean 映射。不恢复或撤销工作区中的其他变更。

已经执行的业务对象删除不能通过代码回滚恢复；需要管理员依据执行前备份或其他数据恢复手段处理。无迁移文件修改。

## User review notes

- 用户在明确默认行为与显式行为的区别后，选择仅显式 `--clean` 完整还原。
- 本计划依据已经实施并验证的设计补记，未伪造前置 Plan review，也未重新实施产品代码。
