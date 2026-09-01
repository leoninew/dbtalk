# 数据库管理规格
最后修改时间: 2026-08-23 15:27:52

Review status: Accepted
Flow mode: strict
Stage: Spec

## Requirement basis

本规格基于已接受的[数据库管理需求](../requirement/20260823-database-management.md)。目标是分别在 `dbtalk mysql` 和 `dbtalk postgres` 下提供 database list/create/drop；实例生命周期操作不能进入通用 query/exec 入口，也不能要求用户理解内部事务控制。

## Overview

每个方言包拥有独立的 Click `database` subgroup 及其服务实现：

```text
dbtalk mysql database    -> dbtalk.mysql.database
dbtalk postgres database -> dbtalk.postgres.database
```

两个实现共享既有 canonical DSN 解析与受控错误模型，但不共享管理命令工厂、方言 SQL 或对象模型。公开命令只接受管理 DSN、数据库名称及删除确认，不接受任意 SQL、事务参数或创建属性覆盖。

## Interfaces

| 方言 | 命令 | 输入 | 成功输出 | 安全约束 |
| --- | --- | --- | --- | --- |
| MySQL | `database list` | `--dsn` 或 `--dsn-env` | 可见数据库名表格 | 管理 DSN 指向既有数据库。 |
| MySQL | `database create` | DSN、`--name` | 已创建的数据库名 | 名称先校验并按方言引用。 |
| MySQL | `database drop` | DSN、`--name`、`--yes` | 已删除的数据库名 | 缺少 `--yes` 时不连接。 |
| PostgreSQL | `database list` | `--dsn` 或 `--dsn-env` | 非模板、可连接数据库表格 | 使用可作为维护连接的管理 DSN。 |
| PostgreSQL | `database create` | DSN、`--name` | 已创建的数据库名 | 不向用户暴露内部执行模式。 |
| PostgreSQL | `database drop` | DSN、`--name`、`--yes` | 已删除的数据库名 | 拒绝删除管理 DSN 当前 database。 |

所有 DSN 选项严格二选一。`--name` 是单一结构化 identifier，拒绝空白、NUL 和控制字符；不得拼接调用方提供的原始 SQL。错误、帮助和成功输出不得回显 DSN 密码。

## Design decisions

1. 命令以方言根命令为边界。MySQL 的 database 与 PostgreSQL 的 database 不合并到 `admin` 根命令，以保留 database、schema 与 instance 等对象语义差异。
2. 每个方言模块自行解析和核验 DSN、校验名称、创建短生命周期 SQLAlchemy engine、构造 SQL、映射异常并释放资源。只复用 `parse_dsn`、`dsn_from_environment` 和 `DatabaseOperationError`。
3. 标识符用连接方言的 identifier preparer 引用，而不是 SQL bind parameter。服务端继续判定名称长度、保留字、权限和版本约束。
4. PostgreSQL database DDL 使用不进入事务块的内部连接执行方式；MySQL 保持其原生 DDL 语义。这是实现细节，不成为 CLI 参数、help 或用户文档内容。
5. `drop` 为唯一高危首版操作，统一要求 `--yes`。门卫检查位于 DSN 解析和连接创建之前。
6. PostgreSQL 删除先比较目标名称与管理 DSN 的 database，相同则拒绝。首版不终止其他连接，保留服务端对活动连接、权限和托管策略的错误语义。

## Affected components

- `src/dbtalk/mysql/database.py` 与 `src/dbtalk/postgres/database.py`：方言管理服务及 Click 适配。
- `src/dbtalk/mysql/cli.py` 与 `src/dbtalk/postgres/cli.py`：仅注册各自的 `database` subgroup。
- `tests/test_database_administration.py`：命令、SQL、引用、执行方式、脱敏和安全门卫测试。
- README、方言手册、Codex 文档和两个方言 skill：公开命令与操作边界说明。

## Alternatives

- 向 `database exec` 新增 `--autocommit`：拒绝。它把高权限生命周期 DDL 与 DML 混入同一入口，并暴露不需要用户控制的执行细节。
- 新增统一 `dbtalk admin`：拒绝。它会掩盖 MySQL 与 PostgreSQL 对 database、schema 和 instance 的差异，且会引入缺乏明确所有者的共享适配层。
- 支持任意 DDL、`IF EXISTS`、强制断连或创建属性：拒绝。首版只提供三个可审查动作，避免扩大高危范围。

## Risks

- 管理 DSN 需要实例级权限；托管服务可能限制 create/drop 或系统库访问。
- PostgreSQL 删除可因活动连接失败；强制断连是独立的高风险能力。
- MySQL 和 PostgreSQL 的 identifier 限制不同，工具只负责输入安全与引用，不能替代服务端校验。

## User review notes

- 用户要求独立命令而非为通用 SQL 增加 autocommit 参数。
- 用户确认使用方言根命令，并要求高危删除显式传入 `--yes`。
- 用户要求补齐并暂存严格模式的过程文档；本 Spec 据已完成实现和验证回填，和实际交付边界一致。
