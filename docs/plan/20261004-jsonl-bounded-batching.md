# JSONL 有界批处理优化计划
最后修改时间: 2026-10-04 00:43:19

Review status: Accepted
Flow mode: standard / 标准模式
Stage: Plan / 计划

## Basis And Scope

依据 [已接受需求](../requirement/20261004-jsonl-bounded-batching.md)，仅优化现有 SQLAlchemy Core adapter 的 PostgreSQL、SQLite 路径。复用 `scan_document`、`iter_document_tables`、`_target_values`、JSONL writer 和现有单表事务，不新增传输层或绕过 SQLAlchemy 使用原生 DBAPI。

## Implementation Steps

1. **将目标类型转换纳入首遍预检。**
   - 扩展 adapter 传给 `scan_document` 的行校验回调；复用原有行校验，对选中表的数据调用现有目标转换入口，转换结果立即丢弃。
   - 目标 schema 校验沿用现有规则，确保转换前列映射已校验；空表仍通过表头预检。
   - 首遍继续扫描完整文件，只保存元数据与计数；选表规则、外键顺序检查、第二遍回放流程保持现有约定。

2. **实现有界批量 insert。**
   - 在 adapter 内增加参数批次生成逻辑，流式读取、转换，并保留输入顺序。
   - 行数上限沿用 `1000`；新增参数负载字节预算 `8 MiB`，两者均为内部常量。追加下一行会超过任一预算时，先执行当前批次，再开始新批次。
   - 字节估算以转换后的绑定值为基础：文本按 UTF-8 长度、BLOB 按字节长度，数值按稳定文本表示估算。该预算不等于 Python 对象或驱动内存占用。
   - 单行超过字节预算时独立执行该行，避免因预算新增合法制品的拒绝条件；内存仍可能受最大单行影响。
   - 使用现有单行参数化 `text()` 语句，调用 `connection.execute(statement, parameters_list)`；PostgreSQL 由 psycopg 3 `executemany` 自动使用可用的 pipeline。
   - 所有批次放在已有 `connection.begin()` 内，整表结束才提交。

3. **实现按主键匹配的原生批量 upsert。**
   - 在单行 INSERT 语句后构造 `ON CONFLICT (<完整主键>) DO UPDATE`，只更新文件提供的非主键列；只有主键列时使用相同冲突目标的 `DO NOTHING`。
   - 复用 insert 的参数批次和执行入口，顺序执行单行语句，不合并成多行 VALUES，也不对重复主键去重。
   - PostgreSQL、SQLite 直接采用原生方案，MySQL 继续使用现有执行路径。
   - 接受并在手册中说明原生 INSERT/冲突处理的约束及触发器语义，不增设自动回退路径。

4. **显式启用流式 export 并管理结果集。**
   - 将 `yield_per=1000` / `stream_results` 限定于 PostgreSQL、SQLite 的数据 SELECT；避免影响 schema inspection 和后续语句。
   - PostgreSQL 使用 SQLAlchemy psycopg dialect 的数据库端游标；SQLite 使用本地游标分批取数。
   - 保留 `fetchmany(1000)`、逐行编码、gzip 和临时文件原子替换；通过结果集 context manager 或 `try/finally` 确保 `close()`。

5. **补充进度日志并同步文档。**
   - 复用 `logging.getLogger("dbtalk")` 和现有文本日志，不修改集中日志配置或 stdout 命令结果。
   - 使用 `time.monotonic()`，在处理循环中检查约 `5 秒` 的内部间隔，并在表完成或回滚时报告最终状态；不引入后台日志线程。
   - 导入报告当前表、执行成功的输入行数、预检总行数、待提交行数、任务已提交行数、rows/s 和耗时。按输入行计数，不使用数据库 `rowcount`。
   - 表事务成功退出后才累计已提交行数；失败时报告该表回滚，待提交数清零，前序表已提交数保留。
   - 导出报告已写行数、rows/s 和耗时，总行数显示未知；表完成与制品原子替换完成使用各自的完成日志。
   - 更新使用手册的批次预算、流式读取、原生 upsert 和进度日志说明。

## Files To Change

| 文件 | 目的 |
| --- | --- |
| `src/dbtalk/database/sqlalchemy_transfer.py` | 首遍转换校验、批次生成、insert/upsert 执行、流式结果集和进度日志。 |
| `tests/test_database_transfer.py` | 复用现有类型转换、预检、gzip 和表事务回归。 |
| `tests/test_database_transfer_batching.py` | 新增聚焦批次执行、输入顺序及流式取数的测试。 |
| `tests/test_database_transfer_logging.py` | 进度字段、提交与回滚状态。 |
| `docs/database.md` | 当前使用约定及优化边界。 |

原则上无需修改 CLI、options、格式解析器、schema 模型、依赖版本或 MySQL 实现；实现时确有必要的局部调整应在交付时说明。

## Verification Plan

验证聚焦以下五组行为，不穷举数据库边界：

1. **批量与转换：** 正常 insert 和宽行/BLOB 数据分别触发行数、字节预算，确认参数列表执行且转换结果不变。
2. **upsert 顺序：** 完整联合主键、批内及跨批重复主键按文件顺序更新；只含主键的输入正常处理。
3. **预检与事务：** 后续表存在目标转换错误时首表不写入；已执行多个批次后发生真实约束冲突时当前整表回滚，前序已提交表保留。
4. **流式 export：** 显式执行选项、分批取数、逐行写出，以及成功/编码失败时关闭结果集。
5. **日志：** 用可控时钟确认间隔、行数和吞吐率；提交前后及回滚时的状态与实际事务一致。

先运行聚焦测试，再运行仓库质量入口：

```bash
uv run --locked --no-sync pytest tests/test_database_transfer.py tests/test_database_transfer_contract.py tests/test_database_transfer_batching.py tests/test_database_transfer_logging.py
make check
make test
git diff --check
```

SQLite 使用临时数据库实际验证。PostgreSQL 增补一个标记为 integration 的聚焦流程，在隔离测试库验证重复主键批量 upsert、psycopg 的 executemany/pipeline 路径和流式游标；有可用测试环境时运行，缺少环境时明确记录未验证项。通过 SQLAlchemy 执行事件与实际游标确认执行方式，不以固定加速倍数作为验收条件。

## Assumptions And Risks

- 本计划采用内部默认值 `1000 行 / 8 MiB / 5 秒`，不新增配置。超预算单行独立执行，批次预算不作为进程内存硬上限。
- 原生 upsert 的约束及触发器语义已获用户确认，文档需同步。
- PostgreSQL 驱动能力与真实性能需要实际环境验证；单元测试只证明执行路径与功能行为。
- 暂无需要用户决定的阻塞项。

## Rollback

本次不修改 schema 或 JSONL 格式。代码回退可恢复此前执行方式；数据仍遵循现有单表事务语义，回退代码不会撤销此前已提交的表。

## User Review Notes

- 用户要求进入计划阶段，范围限定 PostgreSQL、SQLite。
- 用户接受原生 upsert 的数据库语义，要求明确写入文档。
- 用户要求聚焦优化目标，不扩展大量边界场景。本文据此限定实现和验证范围。
- 用户明确要求开始实现，计划据此标记为 Accepted。
