# .agents/

Antigravity（Gemini）的 workspace 级配置目录，随仓库提交以供团队共享。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `plugins.json` | 向 Antigravity 注册 plugin 包路径。当前将 `plugins/dbtalk` 声明为 plugin 来源，Antigravity 启动时自动发现其中的 `plugin.json` 和 `skills/`。 |
| `plugins/marketplace.json` | Codex 的 local marketplace 清单，与 Antigravity 无关。声明 `dbtalk` 插件的本地源路径，供 `codex plugin add` 使用。 |

## 各宿主发现路径汇总

| 宿主 | 配置入口 |
| --- | --- |
| **Antigravity** | `.agents/plugins.json` → `plugins/dbtalk/plugin.json` + `plugins/dbtalk/skills/` |
| **Codex** | `.agents/plugins/marketplace.json` → `plugins/dbtalk/.codex-plugin/plugin.json` |
| **Claude Code** | `.claude-plugin/marketplace.json` → `plugins/dbtalk/.claude-plugin/plugin.json` |
| **Grok** | 直接安装 `plugins/dbtalk`（复用 `.claude-plugin/plugin.json`） |
