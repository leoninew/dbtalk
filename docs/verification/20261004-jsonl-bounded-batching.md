# JSONL 有界批处理优化验证
最后修改时间: 2026-10-04 01:16:11

Review status: Accepted
Flow mode: standard / 标准模式
Stage: Verification / 验证

## Intent alignment

依据已接受的 [意图](../requirement/20261004-jsonl-bounded-batching.md) 核对：PostgreSQL、SQLite 的 JSONL 导入采用有界参数批次，upsert 以完整主键执行原生冲突处理；导出显式启用流式读取并关闭结果集，日志区分待提交与整表提交。目标类型转换已纳入首遍预检，继续保留 JSONL v1、gzip、输入顺序、双遍读取和单表事务。

沿用本功能已有的 `docs/requirement/` 意图文档，不迁移文档位置。

## Spec alignment

标准模式未创建独立 Spec，按已接受的意图与计划核对。

## Plan alignment

依据 [计划](../plan/20261004-jsonl-bounded-batching.md)，实际实现保持在现有 SQLAlchemy Core adapter 内。批次上限为 1000 行及估算参数负载 8 MiB，超预算单行独立执行；批次执行不提交事务，整表成功后才累计已提交行数。PostgreSQL、SQLite 采用单行 SQL 加参数列表，保留重复主键的输入顺序；MySQL 执行路径未纳入优化。

导出执行选项仅作用于表数据 SELECT；进度使用 `monotonic()` 和约 5 秒的循环检查，没有后台心跳线程。计划中的 PostgreSQL 聚焦集成测试已在隔离临时 schema 中通过，并另有同一备份的原生 CLI 实库导入证据。

## Actual diff summary

- `src/dbtalk/database/sqlalchemy_transfer.py`：目标转换预检、按行数与字节预算生成参数批次、批量 insert/upsert、流式结果集及提交/回滚进度日志。
- `tests/test_database_transfer_batching.py`：新增批次预算、类型转换、重复主键顺序、主键列-only upsert、预检和流式结果集测试，以及 PostgreSQL pipeline/服务端游标集成测试。
- `tests/test_database_transfer_logging.py`：补充间隔、输入行计数、提交状态和跨批次整表回滚验证。
- `docs/database.md`：说明批次预算、原生 upsert 约束/触发器语义、流式读取及进度字段。
- 同名意图、计划及本文档：记录本功能的范围、实现约定和验收证据。

## Expected vs actual changed files

| 计划范围 | 实际改动 | 结果 |
| --- | --- | --- |
| SQLAlchemy Core adapter | `src/dbtalk/database/sqlalchemy_transfer.py` | 对齐 |
| 传输回归与批处理测试 | 新增 `tests/test_database_transfer_batching.py`；原有 `tests/test_database_transfer.py` 直接复用，未修改 | 对齐 |
| 进度与事务日志测试 | `tests/test_database_transfer_logging.py` | 对齐 |
| 使用手册 | `docs/database.md` | 对齐 |
| SpecFlow 过程文档 | 同名意图、计划、验证文档 | 对齐 |
| CLI、配置、JSONL 格式、依赖及 MySQL 执行实现 | 本功能未变更 | 对齐 |

工作区另有 `backup.sql` 和 `tests/test_mysql.py` 的变更，不作为本功能实现或验收依据；提交范围由最终暂存区决定。

## Acceptance checklist

- [x] PostgreSQL、SQLite insert 以有界参数列表执行，保留目标类型转换；测试覆盖行数预算、UTF-8/BLOB 字节预算及超预算单行。
- [x] upsert 使用完整主键，批内和跨批重复主键按输入顺序处理，只有主键列时正常计数；SQLite 测试与 PostgreSQL 实库聚焦测试通过。
- [x] 第一遍预检包含选中数据的目标转换，后续表转换失败时首表尚未写入。
- [x] 多批次处于同一表事务，后续批次约束失败时整表回滚，前序表已提交数据保留；日志状态与事务结果对应。
- [x] export 显式分批取数，正常结束和编码失败时关闭结果集；PostgreSQL 集成测试确认实际服务端游标并检查关闭状态。
- [x] 进度日志报告行数、吞吐率和耗时，区分 pending、committed、rolled_back；实际 CLI 导入出现周期进度与最终提交日志。
- [x] 项目质量检查、既有回归、聚焦测试和 PostgreSQL 集成验证通过。

## Test results

以下为本次会话已经执行的结果。用户随后明确要求不再运行集成测试；本轮文档收尾与提交不重跑测试。

| 命令 | 已执行结果 |
| --- | --- |
| `uv run --locked --no-sync pytest tests/test_database_transfer.py tests/test_database_transfer_contract.py tests/test_database_transfer_batching.py tests/test_database_transfer_logging.py` | 43 passed，1 skipped，0.69 秒；跳过项为须显式提供连接的 PostgreSQL 集成测试 |
| `make check` | Ruff format：63 files already formatted；Ruff check：All checks passed；Mypy：64 source files 无问题 |
| `make test` | 339 passed，1 skipped，2.09 秒；同一 PostgreSQL 集成测试默认跳过 |
| `uv run --env-file .env --locked --no-sync pytest tests/test_database_transfer_batching.py::test_postgres_batch_upsert_pipeline_and_streaming -m integration -v` | 1 passed，23.10 秒；重复主键批量 upsert、executemany、实际 pipeline、服务端游标及结果集关闭均通过 |
| `git diff --check` | 验证检查时通过 |

集成测试使用 `.env` 中的 `DBTALK_DSN_BATCH_TEST` 连接 `k12_delivery_os_test1`，创建随机命名的 `dbtalk_batch_*` schema，测试结束在 `finally` 中删除自身 schema。未使用业务表执行聚焦 upsert 测试。

## PostgreSQL CLI integration evidence

2026-10-04 使用已安装的 `dbtalk 0.30.5` CLI；其 editable 路径加载本仓库实现。目标数据库与账号均为 `k12_delivery_os_test1`，已配置 migrator，数据库、`public` 和 56 张表均由该账号拥有。

复用 `data/k12-delivery-sync-20261003-223011/source-ordered.jsonl.gz`，共 20,891,327 字节，SHA-256 为 `95628bcce48d437cbc04def47d79ed074c5a15339ba1206b0ea009be66e6b240`。schema 通过应用既有 Alembic CLI 离线生成 SQL，再由 `dbtalk exec` 执行到 `past_exam_banks_20261002`；清理新库迁移种子数据后，使用原生 import 写入：

```bash
/usr/bin/time -lp dbtalk import \
  --target postgresql \
  --dsn-env DBTALK_DSN_TARGET \
  --input data/k12-delivery-sync-20261003-223011/source-ordered.jsonl.gz \
  --mode insert
```

| 指标 | 实测结果 |
| --- | --- |
| 命令退出状态 | 0 |
| 导入总量 | 56 张表，117,609 行 |
| 逐表行数核对 | 全部与既有 source manifest 匹配，无差异 |
| 总耗时 | 143.93 秒，包含 schema inspection、完整预检和导入；不含建库、建 schema 与事后核验 |
| 上次同一备份原生导入 | 约 4028 秒；本次约快 28 倍 |
| `mineru_parse_result_members` | 87,653 行，39.72 秒；上次约 50 分钟 |
| 平均吞吐率 | 约 817 行/秒 |
| 峰值 RSS | 95,617,024 字节，约 91.19 MiB |

原始日志与逐表记录保存在本地、Git 忽略的 `data/k12-delivery-test1-20261004-010207/import.log` 和 `benchmark.json`；连接凭据仅在 `.env` 中，未写入过程文档。

## Missed or expanded scope

没有本功能范围扩张；未新增 CLI/config 参数、传输格式、跨表事务、并行导入或 MySQL 优化。实库准备仅用于用户明确指定的新测试库，应用迁移文件未修改。

## Risks and limits

- 大备份实库验收采用逐表行数核对，未对 `test1` 执行逐行内容哈希比对；原生 upsert 与游标行为由独立聚焦集成测试验证。
- 加速倍数来自同一备份的一次新旧实测，不作为不同网络和负载环境的固定性能承诺；8 MiB 是参数负载估算预算，不是 RSS 硬上限。
- 原生 upsert 的 INSERT 约束与触发器语义，以及双遍读取期间制品保持稳定的前提，继续适用。
- 聚焦集成测试通过并完成自身 `finally` 清理；随后附加的清理核查查询返回 `Error: database query failed`，未重跑，该查询未作为验收依据。

## Incomplete items and conclusion

本功能的实现、质量检查、回归和 PostgreSQL 集成验收均有通过证据，无已知未完成的实现项。用户已接受实库集成结果并要求完成验证文档；标准模式验证阶段完成。未重新运行用户已要求停止的集成测试。

## User review notes

- 用户要求用 `dbtalk postgres` 创建 `k12_delivery_os_test1`、配置 migrator 与所有权，并复用已有备份测试新版原生 import。
- 用户接受 56 张表、117,609 行全部计数匹配及约 28 倍提速结果，要求完成 SpecFlow 验证文档。
- 用户明确表示集成测试已经验证，无须再跑，并授权提交。
