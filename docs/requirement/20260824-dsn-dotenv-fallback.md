# DSN dotenv 回退
最后修改时间: 2026-08-24 16:48:48

Review status: Accepted
Flow mode: light
Stage: Requirement

## Background

`--dsn-env` 当前只从进程环境读取 DSN。项目已经忽略当前目录 `.env`，但该文件不能作为 `DBTALK_*` DSN 名称的本地安全凭据来源，导致自动化代理无法在不暴露命令行密码的前提下完成连接配置。

## Goal

1. 当 `--dsn-env` 引用的名称以 `DBTALK_` 开头时，在当前工作目录 `.env` 中查找同名值。
2. 已存在的进程环境变量始终优先于 `.env`，包括空值；空值仍按既有规则视为未设置。
3. dbtalk skills 明确允许代理在用户提供或授权的 DSN 范围内写入当前目录 `.env` 的 `DBTALK_*` 条目。

## Non-goal

- 不全局加载 `.env`、不改写 `os.environ`，也不读取父目录的 dotenv 文件。
- 不改变非 `DBTALK_*` `--dsn-env` 名称的行为。
- 不修改 `.env.local` 的 Dynaconf 配置加载规则，也不将凭据写入 Git、日志、命令参数或示例输出。

## User scenarios

1. 用户运行 `--dsn-env DBTALK_APP_DSN`，进程未设置该变量，工具从当前目录 `.env` 的 `DBTALK_APP_DSN=...` 读取 DSN。
2. CI 或调用进程已设置 `DBTALK_APP_DSN`，即使当前目录 `.env` 有不同值，仍使用进程中的值。
3. 代理在用户提供已授权 DSN 后可将其写入当前目录、已忽略的 `.env`，再使用 `--dsn-env`，但不得将 DSN 作为 CLI 参数、输出或提交内容。

## Acceptance

- [ ] `dsn_from_environment` 仅为 `DBTALK_*` 名称从 `Path.cwd() / ".env"` 读取同名值。
- [ ] 进程环境变量优先；进程中存在空值时不回退到 `.env`。
- [ ] `.env` 缺失、条目缺失或为空时保持稳定、非敏感的现有错误语义。
- [ ] 非 `DBTALK_*` 名称不会触发 dotenv 文件读取。
- [ ] database、MySQL、PostgreSQL skills 说明安全的 `.env` 写入与使用约束。
- [ ] 单元测试覆盖读取、优先级、范围和错误边界。

## Open questions

暂无需要用户确认的未决事项。

## Decisions

- 使用 `python-dotenv` 的 `dotenv_values` 一次性读取，不使用会改变全局进程环境的 `load_dotenv`。
- dotenv 回退仅适用于 `DBTALK_` 前缀，以保持现有任意环境变量名调用者的兼容性。
- `.env` 作为本地凭据文件，持续由 `.gitignore` 排除；skill 只允许写入用户提供或明确授权的 DSN。

## Risk

- `.env` 是明文凭据文件，应限制在受控工作目录；本变更不提供加密、secret manager 集成或跨目录查找。
