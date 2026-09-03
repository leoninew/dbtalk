# 跨宿主 Agent 插件打包验证
最后修改时间: 2026-08-23 16:24:18

Review status: Accepted
Flow mode: light
Stage: Verification

## Requirement alignment

实现以 `plugins/dbtalk/skills/` 作为唯一 skill 载荷，保留 Codex manifest 与 `.agents` marketplace，并新增 Claude manifest 和 Claude marketplace。Grok 的本地 validator 成功识别该插件目录中的 Claude-compatible manifest 与共享 skill。所有 plugin skill 改为调用 `dbtalk`，而非 `uv run dbtalk`；插件 README 和 Agent 文档明确发布安装的 CLI 必须位于 `PATH`。

## Spec and plan alignment

light 模式不创建独立 Spec 或 Plan。实际实现按 Requirement 的 Decisions、Non-goal 与 Acceptance 逐项核对。

## Actual diff summary

- 新增 `plugins/dbtalk/.claude-plugin/plugin.json` 和仓库根目录 `.claude-plugin/marketplace.json`，为 Claude 提供 plugin 与 marketplace 入口。
- 新增 `plugins/dbtalk/README.md`，记录共享载荷、三类宿主的发现方式和 CLI 前置条件。
- 更新三个 shared skill，移除 `uv run dbtalk` 前缀，保留既有方言边界、凭据与 `--yes` 安全要求。
- 更新 `docs/codex.md`，将发布范围说明扩展为 Codex、Claude 与 Grok。
- 更新 Codex manifest 的缓存版本，按 skill 名称重命名共享 skill 目录，并将规范源的完整同步引擎复制到 `scripts/release.py` 和 `scripts/test_release.py`；删除 `release_config.py` 与旧外部引擎测试，校验 manifests、marketplace source path、名称契约、原生 CLI 计划和独立 skill 叶子目录边界。

## Expected vs actual files

| Requirement 范围 | 实际文件 |
| --- | --- |
| Codex/Claude manifests | `plugins/dbtalk/.codex-plugin/plugin.json`、`plugins/dbtalk/.claude-plugin/plugin.json` |
| Codex/Grok 与 Claude marketplace | `.agents/plugins/marketplace.json`、`.claude-plugin/marketplace.json` |
| 共享 skills | `plugins/dbtalk/skills/dbtalk/SKILL.md`、`dbtalk-mysql/SKILL.md`、`dbtalk-postgres/SKILL.md` |
| 使用与维护说明 | `plugins/dbtalk/README.md`、`docs/codex.md` |
| 防漂移测试 | `tests/test_plugin_packaging.py` |

实际变更与 Requirement 一致。`.agents/plugins/marketplace.json` 原本已包含正确的 `dbtalk` source path，因此本次未修改其内容；测试仍覆盖该约束。项目没有 standalone skill，plugin 内的 shared skill 仅通过 native plugin 发现。

## Acceptance checklist

- [x] `plugins/dbtalk` 同时包含有效的 Codex 与 Claude plugin manifest，并共用 `skills/`。
- [x] 两个 marketplace 均将 `dbtalk` 指向 `./plugins/dbtalk`。
- [x] Claude 严格校验与 Grok plugin validator 均通过。
- [x] 三个 shared skill 已移除 `uv run dbtalk`，并要求 `dbtalk` 位于 `PATH`。
- [x] 数据库管理的方言隔离、凭据处理和 `--yes` 删除确认指引在 skill 更新后仍保留。
- [x] `tests/test_plugin_packaging.py` 覆盖 manifests、marketplace source path 和 skill CLI 前缀约束。

## Test results

| Command | Result |
| --- | --- |
| `python .../plugin-creator/scripts/validate_plugin.py plugins/dbtalk` | Passed。 |
| `claude plugin validate --strict plugins/dbtalk/.claude-plugin/plugin.json` | Passed。 |
| `claude plugin validate --strict .claude-plugin/marketplace.json` | Passed。 |
| `claude plugin validate --strict plugins/dbtalk` | Passed。 |
| `grok plugin validate plugins/dbtalk` | Passed；识别 1 个 skill directory。 |
| `quick_validate.py`（三个 skill） | Passed。 |
| `uv run pytest` | Passed，170 passed，1 skipped，coverage 90.04%。 |
| `python scripts/test_release.py` | Passed，10 tests。 |
| `uv run ruff format --check .` | Passed；vendored release scripts 按规范源保留并由项目配置排除。 |
| `uv run ruff check .` | Passed；vendored release scripts 按规范源保留并由项目配置排除。 |
| `python scripts/release.py plugin check --json` | Passed；三个本机 CLI 均完成只读预检，无写入。 |
| `python scripts/release.py plugin apply --dry-run --json` | Passed；三个宿主均生成计划，无写入。 |
| `claude plugin validate --strict plugins/dbtalk/.claude-plugin/plugin.json` | Passed。 |
| `claude plugin validate --strict .claude-plugin/marketplace.json` | Passed。 |
| `claude plugin validate --strict plugins/dbtalk` | Passed。 |
| `grok plugin validate plugins/dbtalk` | Passed；识别 1 个 skill directory。 |
| `git diff --check` | Passed。 |

## Scope deviation

无。未安装 CLI、未向任一用户目录复制插件、未执行真实 `claude plugin install` 或 `grok plugin install`，均符合本任务的 Non-goal，并留待 release 能力处理。

## Risks and incomplete items

- 尚未在真实用户 Home 执行 install/update；应在干净目标环境验证 `dbtalk` 安装、`PATH` 生效、Codex/Claude/Grok 的实际安装、更新和新会话加载流程。
- plugin schema 属于外部宿主接口；升级三类 CLI 时应重新运行本次记录的 validator 与结构测试。

## Conclusion

跨宿主插件制品、共享 skill 调用边界和共享发布引擎入口已符合 Requirement。Codex、Claude、Grok 的只读预检、本地验证器以及项目专项测试均通过；真实安装仍需在明确授权的目标环境执行。
