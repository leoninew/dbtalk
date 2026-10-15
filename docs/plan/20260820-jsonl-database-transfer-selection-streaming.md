# JSONL 数据库传输选择与流式处理计划
最后修改时间: 2026-08-20 15:40:52

---
Review status: Accepted
Flow mode: standard
Stage: Plan
---

## 迁移说明

本文档随 JSONL 数据传输能力迁移至 `dbtalk`。其中列出的实现路径、文档位置、skill 位置和验证入口均以本项目为准；同名 Verification 文档只保留源项目的历史结果。

## Basis

本计划实施已接受的 `docs/requirement/20260820-jsonl-database-transfer-selection-streaming.md`，并采用已更新的 MySQL 与 JSONL Requirement 中的统一输出路径契约。任务以 JSONL 传输为主；对 `dbtalk mysql dump` 的改动仅限输出路径解析，不改变原生客户端、SQL 或 restore 语义。

## Scope and CLI contract

保持现有命令层级与参数：

```text
dbtalk mysql dump/restore
dbtalk database export --source sqlite|mysql [--output <file-or-existing-directory>] [connection] [--include-table <table>]... [--exclude-table <table>]... [--archive]
dbtalk database import --target sqlite|mysql --input <file> --mode insert|upsert [connection] [--include-table <table>]... [--exclude-table <table>]...
```

- 未指定 `--include-table` 时，候选集合为源数据库普通表（export）或 JSONL 文件中的表（import）。
- 指定 include 时，候选集合只包含 include 名称；随后从候选集合中移除 exclude，exclude 优先。
- include/exclude 的未知名称、空名称、NUL 名称以及最终为空的集合在写入前失败。
- 选中表依赖未选中的父表时直接失败；本任务不自动补齐父表、不查询目标库判断其是否已有记录。
- 批量读取固定使用内部默认常量 `1000`，不新增 CLI/config 配置。
- `mysql dump` 和 `database export` 均按同一规则解析输出：未传 `--output` 时在当前目录 `data/`（MySQL 保留其可配置默认目录）创建带时间戳制品；传入已有目录时在该目录生成同类制品；其余路径为文件且父目录必须存在。`--archive` 在生成文件名或显式文件路径上追加 `.gz`。

## Implementation steps

1. 统一导出输出路径并扩展表集合选择模型和 CLI。
   - 在 `src/dbtalk/database/cli.py` 中使 export 的 `--output` 可选；增加可测试的默认命名与路径解析逻辑。默认输出为 `data/<source>-<timestamp>.jsonl`，已有目录同样生成该格式，非目录路径验证父目录存在。
   - 在 `src/dbtalk/mysql/dump.py` 中将已有目录形式的 `--output` 解析为 `<database>-<timestamp>.sql`，保持省略 `--output` 时的配置目录默认值；非目录路径仍按文件路径验证父目录。
   - 在 `src/dbtalk/database/cli.py` 的 export/import Click 参数中加入可重复的 `--include-table`，传入 `ExportOptions`/`ImportOptions`。
   - 在 `src/dbtalk/database/models.py` 为两种 options 增加 `include_tables`，同步更新所有调用方和测试，不增加旧 options 形态的适配分支。
   - 将选择逻辑集中到 `src/dbtalk/database/schema.py`：先验证 include/exclude 名称，再计算候选集合和最终集合；exclude 优先；最终集合为空时报 `DatabaseTransferError`。
   - 在日志和 CLI 成功输出中报告最终表数，避免把“源库全表数”误当为实际传输范围。

2. 增加外键集合预检和稳定顺序。
   - 复用现有 `TableSchema.foreign_keys` 图和 `order_table_blocks`，保证父表先、子表后及稳定排序。
   - 在 export 端过滤 schema 后检查每个选中表的 foreign key parent 是否仍在选中集合；外部依赖直接失败。
   - 在 import 端读取文件表头后，使用目标 schema 执行同样的集合内依赖检查；外部依赖、集合内环在任何目标写入前失败。
   - 保留 SQLite/MySQL 的外键检查开启，不用关闭检查绕过顺序；同表自引用沿用现有规则并在测试中明确边界。

3. 重构 JSONL writer 为批量流式导出。
   - 在 `database/format.py` 增加面向流的文档 writer/record writer，保留现有 JSONL v1 记录格式、gzip 输出和临时文件原子替换。
   - 在 SQLite adapter 中用只读连接的 cursor `fetchmany(1000)` 循环读取每张表，逐行编码并写入 `table`/`row`/`end` 记录；不调用 `fetchall()` 读取业务表。
   - 在 MySQL adapter 中直接使用服务器端/流式 cursor 的 `fetchmany(1000)` 循环，逐批编码写入；不回退到 `fetchall()`。
   - 表排序先完成，但表行不整体缓存；每张表只保留元数据和当前批次。
   - 导出异常时清理临时文件并回滚/关闭源连接，目标输出不暴露半成品。

4. 重构 JSONL reader 为双遍流式导入。
   - 第一遍打开 `.jsonl` 或 `.jsonl.gz`，逐记录验证 header、表块结构、include/exclude 选择、行长度、type tag、`end.rows`、源列元数据、目标 schema、主键、类型、外键集合和表顺序；只累计每表计数和必要元数据，不保留所有行。
   - 第一遍完成后关闭输入流；若任何错误，目标库完全不写入。
   - 第二遍重新打开同一输入文件，按预检确定的父表优先顺序逐表读取；每张表只缓存当前写入批次或当前表所需的最小状态，并在单表事务内执行现有 `insert`/`upsert`。
   - 为保证第二遍按目标顺序消费记录，采用与 JSONL 现有有序表块契约一致的顺序；若过滤后需要重排，则在第一遍建立轻量表名顺序并在第二遍拒绝顺序不符的制品，而不把整份文档加载内存。
   - 保留现有表级提交、失败回滚和 SQLite 完整性检查；输出已成功表与失败表的非敏感摘要。生产导入路径直接采用新 reader，不保留旧的整文档导入适配分支。

5. 调整测试、文档和 skill。
   - 更新 `tests/test_database_transfer.py`、`tests/test_cli.py`、`tests/test_database_transfer_logging.py`，覆盖 include/exclude、空集合、未知表、冲突优先级、外部 FK 预检、FK 顺序和 `.jsonl.gz` 双遍导入。
   - 增加 fake cursor 的 `fetchmany(1000)` 行为测试，并断言 SQLite/MySQL export 不使用 `fetchall()` 读取业务数据。
   - 增加大数据模拟测试，验证内存模型只保留批次而非整表；不要求真实大库。
   - 更新 `docs/usage/database.md`、`skills/database-transfer/SKILL.md`（如该 skill 已存在则沿用其位置），说明默认全表、include/exclude 规则、1000 条批量、外键预检、双遍读取和表级事务。
   - 更新本需求对应的 Verification 文档，记录需求对齐、实际 diff、测试和未完成项。

## Files to change

- `src/dbtalk/database/__init__.py`
- `src/dbtalk/database/cli.py`
- `src/dbtalk/database/models.py`
- `src/dbtalk/database/schema.py`
- `src/dbtalk/database/format.py`
- `src/dbtalk/database/sqlite.py`
- `src/dbtalk/database/mysql.py`
- `src/dbtalk/database/transfer.py`
- `src/dbtalk/mysql/__init__.py`
- `src/dbtalk/mysql/cli.py`
- `src/dbtalk/mysql/client.py`
- `src/dbtalk/mysql/dump.py`
- `src/dbtalk/mysql/restore.py`
- `tests/test_database_transfer.py`
- `tests/test_cli.py`
- `tests/test_database_transfer_logging.py`
- `tests/test_mysql.py`
- `docs/database.md`
- `plugins/dbtalk/skills/database/SKILL.md`
- `docs/verification/20260820-jsonl-database-transfer-selection-streaming.md`

## Verification plan

1. 运行现有数据库传输、MySQL、配置测试，确认 gzip、零日期、insert/upsert 和原生 mysql 命令不回归。
2. 验证两种导出在省略 `--output` 时创建默认 `data/` 及带时间戳制品；验证已有目录、已有文件和不存在文件路径的分支，并确认不存在路径不被推断为目录。
3. 使用临时 SQLite schema 验证：默认全表、include、exclude、include/exclude 冲突、未知表和空集合。
4. 使用父子表、外部父表和循环 FK schema 验证：父表优先、外部依赖预检失败、循环预检失败，且目标在写入前保持不变。
5. 使用 fake SQLite/MySQL cursors 验证每批最多 1000 行、持续取数、没有业务表 `fetchall()`，并验证导出临时文件原子替换。
6. 使用 `.jsonl` 与 `.jsonl.gz` 输入进行双遍导入测试，断言第一遍错误时没有目标写入，第二遍按表级事务执行。
7. 运行项目约定的 `pytest`、`ruff`、`mypy` 和 `git diff --check`；全量检查若受既有未涉及文件影响，单独记录范围。

## Assumptions

- JSONL v1 文件的表块顺序由导出器确定，导入器不为已有制品重写记录顺序。
- “外部父表依赖”统一按安全失败处理，不跨数据库查询目标记录，也不自动扩展传输集合。
- 批量大小 1000 是第一版内部常量；后续若有实际性能需求再评估配置化。
- 表级事务保持既有语义，双遍读取只保证预检先于首次写入，不提供跨表原子回滚。

## Risks

- 双遍读取要求输入文件在两遍之间保持可读且内容不变；文件被替换或截断时第二遍必须通过校验失败，不得静默导入不同内容。
- MySQL 驱动的 cursor 选择会影响服务器端内存和连接事务行为，需要用 fake 测试和可用连接验证实际批量语义。
- 外部 FK 依赖一律阻止可能降低部分表迁移的灵活性，但能避免把完整性错误留给目标写入阶段。
- 过滤后的 JSONL 与目标 schema 的表集合可能不一致；错误信息必须明确是制品范围、目标缺表还是外部依赖。

## Rollback

本任务不修改 schema 或原始数据库结构。代码回退即可恢复旧的全表 JSONL 行为；已产生的 JSONL 制品仍按其自身格式处理。任何已写入目标表的数据只能由操作者依照现有数据库备份或显式操作处理。

## User review notes

- 用户确认批量默认值为 1000 条。
- 用户要求解释并采用双遍读取：第一遍预检、第二遍重新读取并写入。
- 用户确认外部 FK 问题通过预检阻止，不自动补齐或推断。
- 用户要求不做旧接口适配或兼容回退，直接切换到新的表集合和流式读写契约。
- 用户确认两个导出命令的输出路径按已存在目录与文件路径分流，不根据后缀推断目录。
