# Agent 插件与 Skills

本项目将 `dbtalk` 作为本地 Agent 插件发布，并让 Codex、Claude 与 Grok 共用同一份数据库操作 skill。Codex 使用 [`plugins/dbtalk/.codex-plugin/plugin.json`](../plugins/dbtalk/.codex-plugin/plugin.json)，Claude 使用 [`plugins/dbtalk/.claude-plugin/plugin.json`](../plugins/dbtalk/.claude-plugin/plugin.json)，Grok 使用已验证兼容的 Claude manifest，直接从 `plugins/dbtalk` 目录安装并信任，不经 marketplace。Codex 市场清单位于 [`.agents/plugins/marketplace.json`](../.agents/plugins/marketplace.json)，Claude 市场清单位于 [`.claude-plugin/marketplace.json`](../.claude-plugin/marketplace.json)。

插件不包含独立的数据库实现，所有 skill 均调用发布安装的 `dbtalk` CLI。使用插件前，确保 `dbtalk` 位于 `PATH`；在本仓库开发时可继续使用 `uv run dbtalk`：

```bash
uv sync --all-groups
```

## 已发布 Skills

| Skill | 适用任务 | 调用命令 | 参考手册 |
| --- | --- | --- | --- |
| [`dbtalk`](../plugins/dbtalk/skills/dbtalk/SKILL.md) | 通用 query/exec，以及 SQLite、MySQL、PostgreSQL 间 JSONL 导出/导入。 | `dbtalk query/exec/export/import` | [数据库手册](database.md) |
| [`dbtalk-mysql`](../plugins/dbtalk/skills/dbtalk-mysql/SKILL.md) | MySQL schema、user、权限、backup、dump、restore 或导入 `.sql`。 | `dbtalk mysql` | [MySQL 手册](mysql.md) |
| [`dbtalk-postgres`](../plugins/dbtalk/skills/dbtalk-postgres/SKILL.md) | PostgreSQL schema、role、权限、单库逻辑备份或恢复 custom archive。 | `dbtalk postgres` | [PostgreSQL 手册](postgres.md) |

## 操作边界

Agent 先通过 `--help` 确认参数，连接只使用 `--dsn-env NAME`：

1. 优先使用用户指定的变量；否则从进程环境或当前目录 `.env` 中，按引擎、服务器、目标库和账号权限选择已有 `DBTALK_DSN_*`。
2. DSN 和密码变量均保留原名，将示例名替换为实际名称，不复制连接或创建别名。传输时分别核对源端和目标端；目标库使用命令支持的 `--database` 指定。
3. 无适用连接时，用用户提供或授权配置的连接信息向当前目录 `.env` 新增 `DBTALK_DSN_*` 条目，保留已有内容。目标或凭据不明时询问用户。

同名进程变量优先；仅在变量不存在时，`DBTALK_DSN_*` 才从当前目录 `.env` 读取。空值报错，不搜索其他 dotenv 文件。配置来自 `dbtalk.yaml` 和 `DBTALK_*` 进程变量。用户要求在脚本中声明变量时，使用进程变量，不写入 dotenv。

不输出真实 DSN 或密码，也不将其写入命令参数、日志、文档、制品或 `.env.example`。

`dbtalk import` 要求目标 schema 已存在；执行前核对目标库、制品来源和写入授权。

两个方言的 user/role、grant/revoke 命令均使用管理 DSN 和结构化参数，不接收原始 SQL。密码仅可通过 `--password-env` 引用；`DBTALK_*` 名称在进程变量不存在时读取当前目录 `.env`。MySQL `password`、`user drop`、`grant` 和 `revoke` 使用 `--host` 或 `--all-hosts` 二者之一。启用、禁用、轮换、删除、授权和撤销需要 `--yes`，且不能修改当前管理身份。

## 本地安装与发布

在项目根目录运行 `make install` 会严格依次执行 plugin 预检、`uv tool install --editable . --force`、plugin 应用。plugin 应用通过可用的 Codex、Claude、Grok CLI 使用各自的原生 plugin 管理器更新 plugin。Codex 与 Claude 使用 marketplace 名称 `dbtalk-local`；Grok 直接安装仓库的 `plugins/dbtalk` 目录。自动模式跳过缺失的部分宿主，但三个宿主均缺失时会在 CLI 安装和宿主写操作前失败。`install.py plugin apply --dry-run` 可在不写入环境的情况下执行只读预检并预览操作；可使用 `--codex` 选择宿主，或使用 `--strict` 要求三个宿主都可用。项目内脚本自包含同步引擎，不读取额外 JSON 配置，也不写入宿主的 cache、credentials、主配置或普通 `skills/` 镜像目录。

`scripts/install.py` 只提供显式的 `plugin check|list|apply|remove` 子命令；无参数或只指定 `plugin` 时显示帮助。

`make release` 仅执行 `uv build`，构建 source 和 wheel 发布产物；它不安装 CLI，也不修改 agent plugin，也不上传到 PyPI。`make pypi` 用 `uv version --short` 读取 `version-calc.py` 写入的 `pyproject.toml` `[project].version`，只上传对应的 `dist/dbtalk-<version>.tar.gz` 与 `dist/dbtalk-<version>-py3-none-any.whl`；读不到版本或对应制品缺失时在上传前失败。它不导入 `dbtalk` 包。凭据从当前目录 `.env` 的 `TWINE_USERNAME` / `TWINE_PASSWORD` 读取，不写入仓库。
