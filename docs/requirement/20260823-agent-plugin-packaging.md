# 跨宿主 Agent 插件打包与分发
最后修改时间: 2026-08-23 19:06:08

Review status: Accepted
Flow mode: light
Stage: Requirement

## Background

`dbtalk` 需要作为独立 plugin 供 Codex、Claude 与 Grok 使用，并让三个宿主共享同一份数据库操作 skill。skill 不能依赖仓库开发态的 `uv run dbtalk`，而应调用发布安装且位于 `PATH` 的 CLI。

本地发布还需要安装 CLI，并调用各宿主的原生 plugin 管理器管理仓库中的 marketplace。发布器原有的宿主适配逻辑先写入再发现宿主是否可用，并通过文本包含判断状态，无法可靠区分同名前缀的 marketplace 或 plugin。

## Goal

1. 使用一份共享的 `plugins/dbtalk/skills/` 发布 MySQL、PostgreSQL 与通用数据库工作流，避免宿主间说明漂移。
2. 提供 Codex、Claude 所需的 manifest 与 marketplace，并让 Grok 使用已验证的 Claude-compatible manifest 和 `.agents` marketplace 发现同一 plugin。
3. 令 plugin skill 调用发布安装且位于 `PATH` 的 `dbtalk` CLI；仓库开发说明仍可单独使用 `uv run dbtalk`。
4. 通过 `make install` 依次执行 plugin 预检、CLI 安装与 plugin 应用，使用单一 `scripts/install.py plugin` 子命令同步 Claude、Codex 与 Grok 的原生 plugin 生命周期。
5. 在首次写入前完成来源、宿主选择与状态预检；自动模式跳过缺失的部分宿主，全部缺失时失败且不写入用户目录。
6. 支持指定宿主、`--strict` 与 `--dry-run`，使用宿主 CLI 的 JSON 状态精确匹配并在写入后验证结果。

## Non-goal

- 不修改 MySQL、PostgreSQL 或通用 database CLI 的命令面、数据库行为或安全边界。
- 不为每个宿主复制 skill，不创建未经验证的 `.grok-plugin` 格式，也不向各宿主 home 直接发布独立 skill 镜像。
- 不接入远程 marketplace、发布注册中心，或承诺跨宿主写入的全局事务。

## User scenarios

1. Codex、Claude 或 Grok 使用同一仓库插件时，均能读取相同的数据库工作流与安全限制。
2. 发布安装完成后，任一宿主触发 skill 均通过 `PATH` 中的 `dbtalk` 运行命令；仓库开发说明仍可使用 `uv run dbtalk`。
3. 维护者执行 `make install` 时，可用宿主完成原生 plugin 安装或更新，缺失宿主会被报告为跳过。
4. 维护者执行 `python scripts/install.py plugin apply --dry-run` 时，可看到各宿主的真实操作计划，且不修改本机状态。
5. 维护者显式选择宿主或使用 `--strict` 时，任何所需宿主缺失会在 CLI 安装和快照发布前失败。

## Acceptance

- [ ] `plugins/dbtalk` 同时包含有效的 Codex 与 Claude plugin manifest，并共享同一 `skills/` 目录。
- [ ] Codex/Grok 的 `.agents/plugins/marketplace.json` 与 Claude 的 `.claude-plugin/marketplace.json` 都指向 `./plugins/dbtalk`。
- [ ] Grok 可验证该插件目录，且 Claude 可严格验证 manifest、marketplace 与完整插件目录。
- [ ] 三个 plugin skill 不包含 `uv run dbtalk`，并明确 `dbtalk` 必须位于 `PATH`。
- [ ] 原有方言隔离与高危操作 `--yes` 安全指引保持不变。
- [ ] 自动化测试覆盖 manifests、marketplace source path、shared skill 调用约束和发布同步器。
- [ ] `install.py plugin apply` 在写入宿主前执行来源、宿主和插件状态预检。
- [ ] 自动模式在全部宿主缺失时返回非零，且不会调用宿主 CLI 写操作；部分缺失宿主仅报告 skip。
- [ ] Claude、Codex、Grok 的 marketplace 与 plugin 状态使用 JSON 的精确记录匹配，覆盖名称前缀碰撞。
- [ ] 明确宿主选择、`--strict` 与 `--dry-run` 在预检阶段生效；dry-run 不写入，且以仓库 marketplace 制定计划。
- [ ] `make install` 严格依次执行 `plugin check`、强制安装 CLI、`plugin apply`，不复制 plugin 或 marketplace。

## Open questions

暂无需要用户确认的未决事项。

## Decisions

- 采用一份共享 skill 载荷，由各宿主 manifest 和 marketplace 发现；不为每个宿主复制 skills。
- Claude 使用 `.claude-plugin/plugin.json` 及仓库根目录 `.claude-plugin/marketplace.json`；Grok 使用已验证的 Claude manifest 兼容性与 `.agents/plugins/marketplace.json`。
- plugin skill 只记录发布态 `dbtalk` 调用；开发态 `uv run dbtalk` 保留在项目与插件说明中，而不写入 skill。
- 以规范源提供的完整 `scripts/install.py` 引擎替换旧发布适配器；项目内 vendored 副本只修改顶部项目配置区，不读取私有 JSON、不依赖共享 Python 包，也不创建 `install_config.py` 或其他同步器。
- `Makefile` 的 `install` 目标严格依次执行 plugin 预检、`uv tool install --editable . --force`、plugin 应用；`install.py` 只提供显式的 plugin 子命令、预检与宿主同步，直接使用仓库 marketplace。marketplace 名称保持 `dbtalk-local`。

## Risk

- plugin 不携带 CLI 二进制或 Python 环境；`uv` 或安装后的 `dbtalk` 不可用时，发布应明确失败。
- 三个宿主的 plugin schema 与 JSON 接口可能持续演进；实现以当前本地 CLI 和自动化测试为约束。
- 某个宿主命令失败时，已完成的宿主不会自动回滚；发布结果必须逐宿主报告。

## User review notes

- 用户要求先将现有 skill 改造为 Codex、Claude、Grok 可用的插件制品，再实现 release 能力。
- 用户要求采用 light 模式记录该任务，并要求使用共享同步模型替换既有分发逻辑。
- 用户要求将同步器合并到 `scripts/install.py`，不保留独立 `scripts/plugin_sync.py`。
