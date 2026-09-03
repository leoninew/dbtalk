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

三个 skill 只在其职责范围内选择命令：原生 MySQL SQL 备份与还原使用 `dbtalk-mysql`；PostgreSQL custom archive 使用 `dbtalk-postgres`；通用 SQL 操作和跨库数据传输使用 `dbtalk`。它们不会替代 CLI 的参数校验或配置加载。

## 操作边界

skills 会先通过相应的 `--help` 确认可用参数。CLI 保留 `--dsn DSN` 与 `--dsn-env NAME` 的二选一契约，但代理执行只能使用 `--dsn-env`：拿到 DSN 后、第一条 dbtalk 命令前，按范围将 DSN 写入当前目录 `.env`，单应用库为 `DBTALK_DSN_APP`，传输为 `DBTALK_DSN_SOURCE` 与 `DBTALK_DSN_TARGET`，数据库生命周期为方言对应的 `DBTALK_DSN_<DIALECT>_MANAGEMENT`，账号或 role 管理为 `DBTALK_DSN_<DIALECT>_ADMIN`。同一任务复用这些名称，不能传 `--dsn`、`export` DSN、内联 DSN 环境变量或 PowerShell `$env:` DSN。`--dsn-env DBTALK_DSN_*` 先读取同名进程环境变量；只有变量不存在时，才读取当前工作目录 `.env` 的同名值。进程变量存在但为空会失败而不回退，其他名称不会读取 dotenv。CLI 不加载 `.env.local`、其他 dotenv 变体或父目录 dotenv 文件；常规配置只来自 `dbtalk.yaml` 和 `DBTALK_*` 进程环境变量。凭据不能出现在命令行、日志、JSONL 制品或 `.env.example` 中。

`dbtalk-mysql` 的 dump/restore 使用本机客户端，或回退到本机已有 Docker `mysql` 镜像；数据库管理命令不调用这些客户端。`dbtalk` 仅传输既有 schema 的数据；导入前必须明确目标库、制品来源和写入授权。

`dbtalk-postgres` 对本机 DSN 的唯一端口映射容器优先复用其 `docker exec` 和默认 Unix socket；未识别到唯一映射容器时使用本机 `pg_dump` / `pg_restore`，缺失时只使用配置的本地 PostgreSQL Docker image（默认 `postgres:18`）；不会拉取 image。它只处理 custom archive，不等同于 PostgreSQL 的物理备份或 JSONL 数据传输。`dbtalk-postgres schema` 独立处理 PostgreSQL database 生命周期，不调用 `pg_dump` / `pg_restore`。

两个方言的 user/role、grant/revoke 命令均使用管理 DSN 和结构化参数，不接收原始 SQL。密码仅可通过 `--password-env` 引用；启用、禁用、轮换、删除、授权和撤销需要 `--yes`，且不能修改当前管理身份。

命令行为、配置字段和参数以 [CLI 手册](../README.md#commands) 与运行时 `--help` 为准；skill 文件用于让 Codex 在合适的任务中选择并安全地运行这些命令。

## 本地安装与发布

在项目根目录运行 `make install` 会严格依次执行 plugin 预检、`uv tool install --editable . --force`、plugin 应用。plugin 应用通过可用的 Codex、Claude、Grok CLI 使用各自的原生 plugin 管理器更新 plugin。Codex 与 Claude 使用 marketplace 名称 `dbtalk-local`；Grok 直接安装仓库的 `plugins/dbtalk` 目录。自动模式跳过缺失的部分宿主，但三个宿主均缺失时会在 CLI 安装和宿主写操作前失败。`install.py plugin apply --dry-run` 可在不写入环境的情况下执行只读预检并预览操作；可使用 `--codex` 选择宿主，或使用 `--strict` 要求三个宿主都可用。项目内脚本自包含同步引擎，不读取额外 JSON 配置，也不写入宿主的 cache、credentials、主配置或普通 `skills/` 镜像目录。

`scripts/install.py` 只提供显式的 `plugin check|list|apply|remove` 子命令；无参数或只指定 `plugin` 时显示帮助。

`make release` 仅执行 `uv build`，构建 source 和 wheel 发布产物；它不安装 CLI，也不修改 agent plugin。
