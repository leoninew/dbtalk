# DSN dotenv 回退
最后修改时间: 2026-08-24 16:57:55

Review status: Accepted
Flow mode: light
Stage: Verification

## Requirement 对齐

已对照 [Requirement](../requirement/20260824-dsn-dotenv-fallback.md) 验证：`--dsn-env DBTALK_*` 支持从当前目录 `.env` 回退读取，进程环境优先，非 `DBTALK_*` 名称维持原行为；三个 dbtalk skills 已说明安全的 `.env` 使用约束。

## 实际 diff 摘要

- `src/dbtalk/database/dsn.py` 使用 `python-dotenv` 的 `dotenv_values` 定向读取 `Path.cwd() / ".env"`，不修改 `os.environ`。
- `tests/test_database_operations.py` 增加 dotenv 读取、进程优先、空值和非 `DBTALK_*` 范围测试。
- `plugins/dbtalk/skills/dbtalk-database/SKILL.md`、`dbtalk-mysql/SKILL.md`、`dbtalk-postgres/SKILL.md` 增加 `.env` 写入授权、`DBTALK_*` 命名及凭据保护说明，并统一示例。
- 新增本验证文档；未创建或修改真实 `.env` 文件。

## 预期与实际文件

预期文件为 DSN 实现、相关单元测试和三个 dbtalk skills；实际改动与预期一致，另包含本需求记录和本验证记录。未发现无关源码或配置改动。

## 验收清单

- [x] `DBTALK_*` 名称在进程变量缺失时从当前目录 `.env` 读取。
- [x] 进程环境变量优先；进程中存在空值时不回退到 `.env`。
- [x] `.env` 缺失、条目缺失或为空时保持 `DSN environment variable is not set` 错误。
- [x] 非 `DBTALK_*` 名称不会从 `.env` 读取。
- [x] database、MySQL、PostgreSQL skills 均说明经授权后写入被 Git 忽略的 `.env`，并禁止猜测、回显和提交凭据。
- [x] 单元测试覆盖读取、优先级、范围和错误边界。

## 命令结果

- `uv run pytest -q`: `173 passed, 1 skipped`，分支覆盖率 `90.06%`。
- `uv run pytest tests/test_database_operations.py tests/test_plugin_packaging.py --no-cov -q`: `27 passed`。
- `uv run python scripts/test_release.py`: `10 tests`, `OK`。
- `uv run ruff check .`: 通过。
- `uv run ruff format --check .`: `94 files already formatted`。
- `uv run mypy`: 通过。
- `git diff --check`: 通过。

跳过项为既有手工集成测试，原因是未设置 `DBTALK_RUN_INTEGRATION=1`；本变更不要求真实 MySQL 或 PostgreSQL 服务。

## 范围偏差与风险

无超出需求的源码范围。`.env` 仍是明文凭据文件，必须保持在受控目录并依赖现有 `.gitignore`；本变更不提供加密存储、secret manager 或跨目录 dotenv 搜索。未验证真实外部数据库连接。

## 结论

所有需求验收项均满足，自动化检查通过，可交付。
