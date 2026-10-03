# JSONL 有界批处理优化
最后修改时间: 2026-10-04 00:41:05

Review status: Accepted
Flow mode: standard / 标准模式
Stage: Intent / 意图

## Background

现有 JSONL 传输统一经过 `src/dbtalk/database/sqlalchemy_transfer.py`。导入采用整文件预检和逐表回放的双遍读取，但 insert 每行执行一次，upsert 每行先查询主键再写入；导出已有 `fetchmany(1000)`，尚未显式启用驱动流式游标。日志目前主要报告任务与表的开始、完成。

## Goal

1. 在现有 SQLAlchemy Core adapter 内，为 PostgreSQL、SQLite 实现按行数和字节预算控制的批量 insert，减少逐行调用和 PostgreSQL 数据库往返。
2. 为 PostgreSQL、SQLite 使用按完整主键匹配的原生 `ON CONFLICT`，批量执行保持文件中的行顺序，包括重复主键。
3. 显式配置流式 export，按批取数、逐行编码，并关闭结果集。
4. 定时报告处理行数、总行数、吞吐率和耗时，明确导入数据尚待提交或已经提交。
5. 保持 JSONL v1、gzip、现有类型转换、完整预检、文件顺序和单表事务；将可提前发现的目标类型转换错误纳入预检。

## Non-goal

- MySQL 优化、native dump/restore、schema 修改。
- 新的 CLI/config 参数、传输格式、断点续传、跨表事务、并行导入或独立性能测试平台。
- 将所有数据库约束、触发器或并发场景扩展为专项设计与测试。

## Acceptance

- [ ] PostgreSQL、SQLite insert 通过有界参数列表执行，保持现有转换结果。
- [ ] 两种目标的 upsert 使用完整主键冲突目标，重复主键按输入顺序处理。
- [ ] 第一遍预检完成后才开始写入，包括选中数据的目标类型转换检查。
- [ ] 多个批次仍属于同一表事务；表内失败回滚整表，前序已提交表保留。
- [ ] PostgreSQL export 使用数据库端流式游标；SQLite 按批读取；两者正常结束和失败时均关闭结果集。
- [ ] 日志提供进度、吞吐率和耗时，不把执行成功当成提交成功。
- [ ] 现有 JSONL/gzip 传输测试和项目质量检查通过，并补充证明本次优化有效的最小验证。

## User Scenarios

- 管理员导入大型 JSONL 制品时，以受预算控制的批次执行 insert/upsert，过程中能看到待提交与已提交进度。
- 管理员导出大表时，持续分批取数并写出 JSONL，能看到处理速率和耗时。

## Open Questions

暂无需要用户确认的未决事项。具体内部预算和验证方式见计划草稿。

## Decisions

- 本任务仅优化 PostgreSQL、SQLite，MySQL 保持现有执行路径。
- 用户接受原生 upsert 的约束及触发器语义。`ON CONFLICT` 先尝试 INSERT，其行为可能与原先先查询再 UPDATE 不同；使用手册需明确该边界。
- 批量采用单行参数化语句加参数列表，保留输入顺序，不合并或去重输入记录。
- 字节预算控制当前批次参数负载，不承诺进程内存的固定硬上限。
- 导入总行数使用预检统计；导出进行时总行数未知，不为日志增加全表计数查询。

## Risk And Assumptions

- 原生 upsert 需要输入满足数据库 INSERT 路径的要求，尤其是目标额外必填列。
- psycopg 3 自动 pipeline 的实际效果由驱动和 libpq 环境决定。
- 双遍读取沿用输入文件在执行期间保持稳定的前提。

## User Review Notes

- 用户提出有界批量 insert/upsert、流式 export 和进度日志目标。
- 用户明确排除 MySQL，接受背景梳理中的其他建议并要求开始计划。
- 用户选择采用原生 upsert 语义并写入文档。
- 用户要求保持优化任务聚焦，避免过度扩展边界场景。
