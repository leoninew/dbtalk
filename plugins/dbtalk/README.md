# dbtalk agent plugin

此目录以一份共享的 `skills/` 发布 dbtalk 工作流：Codex 使用 `.codex-plugin/plugin.json`，Claude 使用 `.claude-plugin/plugin.json`，Grok 直接安装并信任此 Claude-compatible plugin package。

插件只提供任务选择与安全操作指引；实际操作由发布安装的 `dbtalk` 命令执行。使用前确保 `dbtalk` 位于 `PATH`。在本仓库开发时，命令可写为 `uv run dbtalk`，但不要把这一开发态前缀写入 plugin skill。

市场清单位于仓库根目录：Codex 使用 `.agents/plugins/marketplace.json`，Claude 使用 `.claude-plugin/marketplace.json`。两份清单都将 plugin source 精确指向 `./plugins/dbtalk`；Grok 不依赖 marketplace，由原生 CLI 直接安装该 package。

从项目根目录运行 `make install` 会先校验仓库中的插件包和两个 marketplace，再通过已安装的宿主 CLI 更新插件。Codex、Claude、Grok 缺失时会被独立跳过；插件安装状态由各宿主原生 plugin 管理器维护。发布脚本不把这三份 shared skill 复制到宿主的普通 `skills/` 目录。
