# 测试清理记录

日期：2026-10-15

按当前代码和公开行为审视单元测试与手动集成测试。参考用户指定的 `k12-delivery-os/.agents/skills/architecture-governance/references/backend-boundaries.md`，采用职责、资源生命周期与代表性行为的核对方式，不套用其他项目的目录结构和历史结论。

本次只修改测试及相关文档。保留此前已完成的 SQLite 内存测试与显式配置装配，不修改 TEMP/TMP、Makefile、生产实现、依赖和本地凭据。

## 清理与保留依据

| 范围 | 清理内容 | 保留的行为验证 |
| --- | --- | --- |
| `test_async_support.py` | 删除仅 `await asyncio.sleep(0)` 的模板测试。 | `test_database_operations.py` 的真实异步查询、事务、异常转换和事件循环测试。 |
| `test_cli.py` | 删除已移除的 root/dialect `database` 命令拒绝测试，以及只替换根函数并检查调用的 `main()` 转发测试。 | 当前根命令和命令组帮助、实际命令执行、verbose 与错误脱敏。 |
| `test_database_transfer_contract.py` | 删除整个文件；根 help 与 DSN 校验重复，数据类属性赋值不验证业务，export/import 参数已由真实调用覆盖，`append` 属于已移除模式。 | 根 help 收敛到 `test_cli.py`；传输参数、insert/upsert、DSN 和表筛选由实际传输测试验证。 |
| `test_database_operations.py` | 删除重复的基础 upsert 和外键 insert 往返；移除对旧 `--write/-w` 参数的拒绝断言。 | 更完整的 portable values upsert、复合主键、外键顺序、gzip/文件 CLI 往返、预检失败与表级回滚。 |
| `test_database_transfer.py` | 删除只 mock SQLAlchemy adapter 并断言转发的测试。 | 真实 adapter 的数据结果、事务、流式导出与日志行为。 |
| `test_settings.py` | 删除旧连接配置字段的缺失断言；两个重复的单字段环境覆盖用例收敛到已有的双引擎配置覆盖测试。 | YAML、环境覆盖、配置与 dotenv 的边界、冻结程序资源路径、session 配置校验。 |
| `test_unit_boundaries.py` | 删除 Click 正常转换不会产生的错误类型组合、仅服务这些组合的字典工厂和无业务目标的配置 helper 断言；合并重复的 TIME 测试。 | 时区与配置/显式 timeout 优先级保留；必填与正数配置校验移到 `test_settings.py`；TIME 小数精度移到已有专门用例。 |
| `test_session_integration.py` | 删除通过替换内部 `_read` 制造 MySQL 切库时机的用例，同时移除其 PostgreSQL 参数下永远不适用的收集项。 | 单测 `test_scope_identity_and_system_thread_changes_prevent_kill` 验证切库后拒绝发送 KILL；原生集成继续验证真实断连、回滚、权限与跨库保护。 |
| `test_database_transfer_batching.py` | 原生测试更名为 `test_postgres_batch_upsert_and_streaming`；删除对 psycopg 私有 `_pipeline_nolock` 与 SQLAlchemy 私有 context 标记的依赖。 | 保留真实批量 executemany、重复主键输入顺序与最终数据；通过公开 `psycopg.ServerCursor` 类型、游标关闭和导出文件内容验证流式读取。自动 pipeline 由驱动与 libpq 决定，不作为业务成功条件。 |

配置校验归位增加两条独立收集项后，普通测试净减少 15 项；会话集成净减少 2 项，总收集数从 475 减为 458。已有 5 个 subtest 继续保留。

## 原生集成保留范围

- PostgreSQL owner：9 项。目录中支持对象与自动传播、初始化角色重命名、扩展成员保护、不支持对象的范围、权限、事务回滚及真实 CLI 入口均属于当前承诺。事件触发器测试验证真实 DDL 失败与归属复核后的回滚，不删为普通重复单测。
- PostgreSQL JSONL：1 项。验证真实方言的重复主键批量写入和服务端流式游标，SQLite 无法代替。
- Session：PostgreSQL 与 MySQL 各 2 项，共 4 项。覆盖目标库的活跃/空闲列表、单 ID/批量终止、wait、真实客户端断连、未提交事务回滚，以及权限不足的部分失败与授权后成功。

缺少测试端点不是删除理由。以上 14 项继续显式依赖独立测试实例，不连接业务库。本轮没有配置相应端点，没有启动服务或容器，也未重新实测修改后的 PostgreSQL 原生批处理用例。此前验收文档中的实库结果保留为历史证据，不计为本轮通过项。

## 本轮验证

完成全部测试代码清理后，在项目根目录通过原有 Cygwin 入口运行：

```powershell
$dbtalkPreviousPytestAddopts = $env:PYTEST_ADDOPTS
try {
    $env:PYTEST_ADDOPTS = '--durations=15 -q'
    & 'D:/ProgramFiles/Cygwin64/Cygwin.bat' 'D:/SourceCodes/mywork/dbtalk' -c 'make check && make test'
    $dbtalkCheckExitCode = $LASTEXITCODE
} finally {
    $env:PYTEST_ADDOPTS = $dbtalkPreviousPytestAddopts
}
exit $dbtalkCheckExitCode
```

| 检查 | 结果 |
| --- | --- |
| Ruff format check | 通过，75 files already formatted。 |
| Ruff lint | 通过，All checks passed。 |
| Mypy | 通过，76 source files 无问题。 |
| pytest | 444 passed、14 skipped、5 subtests passed，5.55 秒，退出码 0。 |
| `git diff --check` / `git diff --cached --check` | 通过。 |

14 个跳过项分别为 owner 9 项、JSONL 原生批处理 1 项、session 4 项，原因均为未配置独立测试端点。清理前上一轮记录为 459 passed、16 skipped、5 subtests passed，4.48 秒；两次单次计时存在波动，本次不宣称进一步加速，清理依据是验证价值与当前行为。

耗时最长的 15 项如下，setup 与 call 分开列示：

| 秒 | 阶段 | 测试 |
| --- | --- | --- |
| 0.47 | call | `test_database_transfer.py::DatabaseTransferTests::test_cli_exports_and_imports_sqlite_data` |
| 0.40 | call | `test_database_transfer.py::DatabaseTransferTests::test_cli_export_uses_default_timestamped_output_path` |
| 0.40 | call | `test_database_operations.py::test_canonical_dsn_transfer_validation_and_environment` |
| 0.21 | setup | `test_restore_clean.py::test_mysql_clean_removes_extra_objects_with_foreign_key_checks_disabled[True-mapped]` |
| 0.18 | call | `test_password_gen.py::test_no_arguments_prints_the_same_help_as_the_help_flag` |
| 0.13 | call | `test_password_gen.py::test_rejects_a_length_that_cannot_hold_all_character_classes` |
| 0.10 | call | `test_password_gen.py::test_rejects_a_non_positive_number_of_passwords` |
| 0.09 | call | `test_password_gen.py::test_generates_the_requested_number_of_passwords` |
| 0.09 | call | `test_password_gen.py::test_generates_default_length_unescaped_dsn_password_without_symbols` |
| 0.08 | call | `test_database_transfer_batching.py::test_insert_executes_row_bounded_batches` |
| 0.08 | call | `test_password_gen.py::test_accepts_a_longer_password_length` |
| 0.05 | call | `test_database_transfer_logging.py::DatabaseTransferLoggingTests::test_sqlite_export_logs_task_schema_table_and_completion` |
| 0.05 | call | `test_backup_db.py::test_run_sync_discovers_databases_for_connections_with_empty_lists[null-True]` |
| 0.04 | call | `test_backup_db.py::test_clean_keeps_recent_files_or_the_latest_seven_per_directory[True]` |
| 0.04 | call | `test_database_transfer_batching.py::test_export_streams_and_closes_result[False]` |

最慢的三项仍保留真实文件路径/SQLite 持久化行为，未仅因耗时改成无文件验证。
