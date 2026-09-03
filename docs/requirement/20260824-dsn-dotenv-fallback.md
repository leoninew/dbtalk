# DSN dotenv 回退
最后修改时间: 2026-08-24 16:48:48

Review status: Accepted
Flow mode: light
Stage: Requirement

## Background

`--dsn-env` 当前只从进程环境读取 DSN。项目已经忽略当前目录 `.env`，但该文件不能作为 `DBTALK_DSN_*` DSN 名称的本地安全凭据来源，导致自动化代理无法在不暴露命令行密码的前提下完成连接配置。

## Goal

1. 当 `--dsn-env` 引用的名称以 `DBTALK_` 开头时，在当前工作目录 `.env` 中查找同名值。
2. 已存在的进程环境变量始终优先于 `.env`，包括空值；空值仍按既有规则视为未设置。
3. dbtalk skills 要求代理将 DSN 写入当前目录 `.env` 的 `DBTALK_*` 条目，并在后续命令中只使用 `--dsn-env`。

## Non-goal

- 不全局加载 `.env`、不改写 `os.environ`，也不读取父目录的 dotenv 文件。
- 不改变非 `DBTALK_*` `--dsn-env` 名称的行为。
- 不将凭据写入 Git、日志、命令参数或示例输出。

## User scenarios

1. 用户运行 `--dsn-env DBTALK_DSN_APP`，进程未设置该变量，工具从当前目录 `.env` 的 `DBTALK_DSN_APP=...` 读取 DSN。
2. CI 或调用进程已设置 `DBTALK_DSN_APP`，即使当前目录 `.env` 有不同值，仍使用进程中的值。
3. 代理拿到 DSN 后将其写入当前目录 `.env`，再使用 `--dsn-env`，不得将 DSN 作为 CLI 参数或输出内容。

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
- skill 要求代理将 DSN 写入当前目录 `.env`，并在后续命令中只使用 `--dsn-env`。

## Risk

- `.env` 是明文凭据文件，应限制在受控工作目录；本变更不提供加密、secret manager 集成或跨目录查找。
