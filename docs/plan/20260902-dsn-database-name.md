# 放宽 DSN 数据库名称要求
最后修改时间: 2026-09-02 10:10:26

Review status: Accepted

## Requirement basis

- 已采纳的 Requirement：`docs/requirement/20260902-dsn-database-name.md`。
- MySQL、PostgreSQL canonical DSN 可以省略 URL database name；SQLite 仍必须给出文件或内存资源路径。
- `database export` / `database import` 没有独立资源选择参数，必须继续要求 DSN database。
- MySQL、PostgreSQL dump / restore 的目标唯一按 `--database > DSN database > 失败` 解析。配置不能作为连接身份或目标 database 的来源。
- 本计划只实施并验证上述主要行为。dump/restore 配置的长期保留或删除由主要行为验证后的独立 SpecFlow 任务重新评估，不在本计划预先决定。

## Implementation steps

1. 放宽通用 DSN 解析，并把必要性校验下沉到业务入口。
   - 修改 `src/dbtalk/database/dsn.py`，保留空值、dialect、显式 driver、port 与 SQLite 资源路径校验；删除 MySQL/PostgreSQL 的全局 database-name 校验。
   - 不为无库名 URL 补充默认 database，也不修改 URL 的 host、port、user、password 或 query 参数。
   - 保留 `ParsedDsn.database: str | None`，由下游命令对确实需要目标的情形产生操作语义明确的错误。

2. 为 JSONL transfer 恢复明确的库级边界。
   - 修改 `src/dbtalk/database/transfer.py` 的连接预检：在 dialect 匹配后，拒绝 MySQL/PostgreSQL 无 database name 的 export/import DSN；SQLite 已由解析器保证资源路径。
   - 错误必须指出 JSONL transfer 需要 DSN database，而不是回落为驱动连接错误或全局 DSN 格式错误。
   - `query`、`exec` 继续直接使用解析后的 URL，不增加 database-name 校验。

3. 统一 MySQL dump / restore 的 DSN 与目标解析。
   - 修改 `src/dbtalk/mysql/cli.py`：`mysql_connection_from_dsn` 允许 `parsed.database` 为 `None`，仍校验 MySQL dialect、host 与 user；添加 dump 的 `--database TARGET`，并将该值及 DSN database 传入 resolver。
   - 修改 `src/dbtalk/mysql/dump.py`：target 仅由 `--database` 或 DSN database 决定，缺失时以专用错误失败；默认输出文件名使用最终 target database；不得读取 `mysqldump.database`，也不得从配置取得连接身份。
   - 修改 `src/dbtalk/mysql/restore.py`：删除 `mysqlrestore.database` 参与的合并规则，target 只按 `--database > DSN database` 选择，缺失时失败；host、port、user、password 保持来自 DSN。
   - 保持已有的非连接配置读取行为。当前存量的 MySQL 配置字段不得参与 dump/restore 的连接身份或 target database 决策。

4. 统一 PostgreSQL dump / restore 的 DSN 与目标解析。
   - 修改 `src/dbtalk/postgres/cli.py`，为 dump、restore 添加 `--database TARGET`，将最终 target 传给原生 client connection 构造。
   - 修改 `src/dbtalk/postgres/client.py`，使 `PostgresConnection` 从无库名 parsed DSN 加显式 target 构造；最终选择规则固定为 `--database > DSN database > 失败`，并继续要求 PostgreSQL dialect、host、user。
   - 保持 `PostgresConnection` 内部的 database 为非空字符串，使 `.pgpass`、libpq URI、Docker socket 与 archive 自动命名均使用同一个最终 target。
   - `src/dbtalk/postgres/dump.py`、`restore.py` 保持 native command、密码脱敏、archive 预检及 Docker fallback，不从设置推导 target database。

5. 补齐命令局部校验与回归测试。
   - 修改 `tests/test_database_operations.py`：覆盖 MySQL/PostgreSQL sync 与 async 无库名 URL 可解析、SQLite 空资源仍失败、query/exec 到达连接执行边界，以及 JSONL transfer 对无库名 DSN 的专用失败。
   - 修改 `tests/test_mysql.py`：覆盖 dump、restore 的 `--database > DSN > failure`、无库名 DSN、配置 database 不参与选择、默认输出名使用最终目标以及 CLI help。
   - 修改 `tests/test_postgres.py`：覆盖 dump、restore 的同一优先级、无库名 DSN 搭配 `--database`、无 target 的专用失败、libpq URI / `.pgpass` / 默认输出名均使用最终目标，以及 CLI help。
   - 审阅现有 MySQL/PostgreSQL grant/revoke、schema 与 permission 测试；仅在它们原本依赖全局 parser 错误时补充各自已经承诺的局部 precondition 测试。

6. 同步用户与代理文档。
   - 修改 `docs/database.md`，说明 server DSN 的 database name 在 URL 语法上可选，且 export/import 例外地要求明确库名。
   - 修改 `docs/mysql.md`、`docs/postgres.md`，写明 dump/restore 的 target precedence、无库名 DSN 与 `--database` 的合法组合，以及无 target 时的失败；删除 `mysqlrestore.database` 的描述。
   - 修改用户正在重命名的 `plugins/dbtalk/skills/dbtalk/SKILL.md`、以及 `dbtalk-mysql`、`dbtalk-postgres` skill，使 agent 选择的命令和约束与 CLI 一致；保留用户已存在的 git rename，不还原路径。

## Expected files

主要任务预计修改：

- `src/dbtalk/database/dsn.py`
- `src/dbtalk/database/transfer.py`
- `src/dbtalk/mysql/cli.py`
- `src/dbtalk/mysql/dump.py`
- `src/dbtalk/mysql/restore.py`
- `src/dbtalk/postgres/cli.py`
- `src/dbtalk/postgres/client.py`
- `tests/test_database_operations.py`
- `tests/test_mysql.py`
- `tests/test_postgres.py`
- `docs/database.md`
- `docs/mysql.md`
- `docs/postgres.md`
- `plugins/dbtalk/skills/dbtalk/SKILL.md`
- `plugins/dbtalk/skills/dbtalk-mysql/SKILL.md`
- `plugins/dbtalk/skills/dbtalk-postgres/SKILL.md`

## Verification plan

1. 运行 MySQL、PostgreSQL、DSN/transfer 相关定向测试，覆盖上述优先级和错误边界。
2. 运行 `make check`，使用项目统一入口执行 Ruff format 检查、Ruff lint 与 mypy；不单独手工格式化文件。
3. 运行 `make test`，确认未被触及的数据库管理、授权、客户端回退与 transfer 行为没有回归。
4. 使用 `--help` 核对三个新增 `--database` 选项和说明；不在测试输出、日志或文档中记录真实凭据。
5. 当本地可用 MySQL、PostgreSQL 服务时，分别以无库名 DSN 执行 `query --sql 'SELECT 1'`，并对 export/import、dump/restore 的无 target 情形确认命令层错误。没有可用服务时，记录为集成验证缺口，不以虚构连接替代。

## Post-primary configuration task

主任务完成并通过 Verification 后，创建独立 Requirement/Plan 重新评估 MySQL dump/restore 配置：

- 逐项确认 `mysqldump`、`mysqlrestore` 是否仍需要输出目录、客户端选择、制品存储或其他非连接配置；不因当前连接规则改变而假定整组删除。
- 仅在后续 Requirement 被采纳后，才修改 `dbtalk.yaml`、`.env.example`、`Settings`、loader、设置测试、手工集成测试和相关文档。
- 保持 `scripts/backup_db.example.yaml` 的 target DSN 作为独立批量备份输入，除非后续任务明确改变该脚本职责。

## Assumptions and risks

- 无库名 MySQL/PostgreSQL URL 的实际默认连接行为由 driver 与服务端决定；dbtalk 只保证解析和转交，不保证任意服务器都能成功连接。
- PostgreSQL native client 的最终 database 必须同时用于 libpq URI、`.pgpass` 和自动输出名，避免连接、认证与制品命名指向不同目标。
- 不得为了通过旧测试重新引入 database 配置回退或无提示的默认库。
- 当前工作区含用户已暂存的 skill 路径 rename；实施时在新路径上更新内容，不修改其版本控制意图。

## Rollback

若实现暴露未识别的需要明确 database 的操作，只在该操作的命令边界补充局部校验和测试；不恢复 `parse_dsn` 的全局 server-database-name 拒绝，也不恢复 MySQL 配置目标回退。

## Open questions

- 暂无需要用户确认的设计决策。真实 MySQL/PostgreSQL 连通性仅取决于本地服务和凭据是否在实施时可用。
