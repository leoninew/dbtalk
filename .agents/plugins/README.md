# .agents/plugins/

Codex 的 local marketplace 配置目录，与 Antigravity 无关。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `marketplace.json` | Codex local marketplace 清单。声明名称 `dbtalk-local`，将 `dbtalk` 插件的本地源指向 `./plugins/dbtalk`，供 `codex plugin add dbtalk@dbtalk-local` 安装。 |

## 注意

- 此目录不是 Antigravity 的 plugin 注册目录。Antigravity 的注册文件是上级的 `plugins.json`。
- 不要在此目录放置 `plugin.json`——Codex marketplace 识别的是 `marketplace.json` 格式，而非 plugin manifest 格式。
