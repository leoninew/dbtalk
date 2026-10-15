# Plan / 计划：数据库删除支持 --if-exists
最后修改时间: 2026-10-15 09:02:50

## Review status / 审查状态

Accepted

## Intent basis / 意图依据

依据 [Intent](../intent/20261015-schema-drop-if-exists.md) 的 A01–A04。用户本轮直接要求添加功能，补齐前置记录后进入 Implementation；必要开发检查属于实施交付，整体 Verification 按 SpecFlow 后续阶段授权推进。

## Technical decisions / 技术设计与决策

- 两个 `drop` Click 入口增加布尔选项 `--if-exists`，默认关闭，始终要求 `--yes`。
- 两个 `drop_database()` 增加 keyword-only 参数 `if_exists: bool = False`，传入私有 `_drop_database()`；保持各方言既有管理连接和 AUTOCOMMIT。
- 先按原有逻辑探测当前连接数据库并拒绝同名目标，再根据选项生成 `DROP DATABASE [IF EXISTS] <quoted name>`；由服务器处理缺失目标，不查询存在性，不吞异常。
- 默认成功输出保持原样；显式启用时输出 `Database dropped if it existed: <name>`，不宣称缺失目标实际被删除。
- 扩展已有参数化测试覆盖两个方言和选项两种取值，并核对确认要求、当前连接库保护、名称引用与删除阶段异常传播。
- 手册和仓库内两个 Agent skill 说明该选项仅忽略目标库不存在；本地脚本两个 drop 增加选项，以修改前后全部 export 行及项目配置块完全一致为检查条件。

## Implementation steps / 实现步骤

1. 完成两个方言的 CLI、操作函数和 SQL 参数贯通。
2. 在既有测试入口补充该行为及真实风险边界，执行聚焦测试。
3. 更新手册、仓库内 Agent skill 以及本地重置命令。
4. 执行既有质量入口及完整测试，审查 diff，在本文记录实际结果、范围偏差和限制。

## Files to change / 需修改文件

- `src/dbtalk/mysql/database.py`、`src/dbtalk/postgres/database.py`。
- `tests/test_database_administration.py`。
- `docs/mysql.md`、`docs/postgres.md`。
- `plugins/dbtalk/skills/dbtalk-mysql/SKILL.md`、`plugins/dbtalk/skills/dbtalk-postgres/SKILL.md`。
- 本功能 Intent、Plan；Implementation 不额外创建实现文档。
- 工作区外本地文档 `D:\Documents\documents\Obsidian\解决方案\dbtalk\数据库重置.md`，仅更新重置命令及对应说明，保留全部已有凭据配置。

## Tasks and coordination / 子任务与协调

不适用；主会话顺序实施，不拆 task、不分配子 agent。

## Verification plan / 验证计划

- 聚焦检查：`uv run --locked --no-sync python -m pytest tests/test_database_administration.py`，覆盖 A01、A02。
- 质量入口：优先 `make check`；当前 PowerShell 无 make，执行 Makefile 对应的 `uv run --locked --no-sync python -m ruff format --check src tests scripts`、`python -m ruff check src tests scripts`、`python -m mypy src tests scripts`（均使用同一 uv 前缀）。
- 完整测试：Makefile `make test` 的等价命令 `uv run --locked --no-sync python -m pytest`。
- `git diff --check`、实际 diff 审查，以及本地文档全部 export 行和项目配置块前后一致性检查，覆盖 A03、A04。
- 不安装依赖，不连接现有业务库；真实数据库实验不属于此次必要开发检查。

## Risks / blockers / assumptions / 风险 / 阻塞项 / 假设

无已知阻塞。`--if-exists` 不会使连接失败、权限不足或数据库占用变成成功；本地脚本仍通过 `&&` 停止这些错误后的建库。整体 Verification 尚未进入。

## Rollback / recovery / 回滚 / 恢复

回退本次限定范围内的代码、测试和说明即可撤销功能；不操作用户数据库，不回退用户已有本地凭据或其他工作区变更。

## User review notes / 用户审查记录

- 2026-10-15：依据用户添加功能的明确请求，补齐计划后实施；不将方案 Accepted 记为实现交付或整体验收已接受。

## Implementation results / 实施结果

### Changes / 实际变更

- 两个 CLI、操作函数及原生 SQL 已贯通 `if_exists`；默认关闭，启用时不预查存在性，不忽略其他异常。
- 测试覆盖 CLI 到 SQL 的默认及显式行为、特殊名称引用、AUTOCOMMIT、两种方言的确认要求和当前连接库保护，以及删除阶段异常继续传播。
- 两份手册与两个仓库内 Agent skill 已同步；实际产品与测试修改文件符合预期，共 7 个已跟踪文件，另新增本功能 Intent、Plan。
- 本地重置文档两个 drop 已加入 `--if-exists`，保留 `&&`。以写入前最新版本核对 11 行 export 和 4 组项目配置块，内容逐项完全一致；写入前另核对源文件未被并发修改，写入后复读验证一致。

### Development checks / 开发检查

工作目录均为仓库根目录 `D:\SourceCodes\mywork\dbtalk`。

| 实际命令 | 结果 |
| --- | --- |
| `uv run --locked --no-sync python -m pytest tests/test_database_administration.py` | 35 passed。 |
| `uv run --locked --no-sync python -m ruff format --check src tests scripts` | 66 files already formatted。 |
| `uv run --locked --no-sync python -m ruff check src tests scripts` | All checks passed。 |
| `uv run --locked --no-sync python -m mypy src tests scripts` | 67 source files，无问题。 |
| `uv run --locked --no-sync python -m pytest` | 386 passed，10 skipped；跳过项需外部 PostgreSQL JSONL 或 owner 测试环境。 |
| `uv run --locked --no-sync python -m dbtalk mysql schema drop --help` | 成功展示 `--if-exists`。 |
| `uv run --locked --no-sync python -m dbtalk postgres schema drop --help` | 成功展示 `--if-exists`。 |
| `dbtalk mysql schema drop --help`、`dbtalk postgres schema drop --help` | PATH 中的本地 CLI 均已展示新选项，无需重新安装。 |
| `git diff --check`、实际代码 diff 审查 | 通过，当前连接库检查仍先于 DROP，未新增异常忽略或存在性查询。 |

当前 PowerShell 未找到 `make`，按计划执行 Makefile 等价命令；未安装工具或升级依赖。

### Deviations and limitations / 偏差与限制

- 本地文档在实施期间由外部更新，首次保护检查发现配置块数量与旧记录不同，因此未写入；随后读取最新版本，仅添加两个选项并保留当时全部配置，不恢复已由外部移除的配置。
- 产品范围无偏差；未执行真实数据库实验，模拟连接证明 SQL 生成与错误传播，缺失库语义由原生 `IF EXISTS` 提供。
- Implementation 及必要开发检查已完成；尚未进入整体 Verification，未创建整体报告，不将本记录视为用户已接受实现交付。
