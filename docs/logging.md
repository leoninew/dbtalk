# 操作日志

`dbtalk` 默认不输出 logging 日志。只有在根命令提供 `-v/--verbose` 时，才启用标准库 `logging` 的文本诊断日志，默认级别为 `INFO`。CLI 的日志写入 stderr，stdout 保留查询结果、表格、SQL 预览和操作结果，方便重定向或解析 JSON：

```bash
dbtalk -v query --dsn-env DBTALK_DSN_APP --sql 'SELECT 1 AS ok' --format json \
  > result.json 2> operation.log
```

## 记录范围

| 操作 | 记录方式 |
| --- | --- |
| MySQL/PostgreSQL 建库、删库、账号和权限管理、权限查询 | CLI 边界记录开始、完成或失败，以及库名、账号、权限 profile 等必要字段 |
| PostgreSQL 所有权转移 | 同上，记录来源/目标 role、database/schema 和 `yes`；`yes=False` 表示仅预览 |
| MySQL/PostgreSQL `session list/kill` | CLI 记录生命周期、显式 database、session_id、wait_seconds 和 yes；操作函数记录解析后的数据库、选中数量及各目标最终状态 |
| 通用 `query` / `exec` | CLI 边界记录开始、完成或失败；`exec` 记录 `dry_run` |
| PostgreSQL dump/restore | CLI 边界记录开始、完成或失败，以及显式指定的目标库、文件和清理选项；Docker 镜像拉取使用 INFO 日志 |
| MySQL dump/restore | 沿用操作函数中的开始、阶段进度、完成和失败日志 |
| JSONL export/import | 沿用操作函数中的开始、连接、表进度、提交/回滚及完成/失败日志 |
| `scripts/backup-db.py`、`scripts/install.py` | 沿用脚本入口配置及已有关键过程日志；这些独立脚本的诊断日志写入 stdout |

CLI 通用记录包含命令路径，结束时包含 `elapsed_ms`；删库会记录 `if_exists`、`force` 和 `yes`。MySQL dump/restore 和 JSONL 传输已有过程日志，CLI 不重复生成一组开始/结束日志。`--help` 和进入命令回调前的参数解析错误不生成操作日志。

会话结果中的 `requested` 只表示请求成功返回，`terminated` 表示等待确认原身份消失；部分失败会保留各目标状态并记录命令失败。日志不包含查询文本、绑定身份值或原始数据库异常。会话命令也只接受根命令的 `-v/--verbose`。

`INFO` 记录开始、完成及汇总，`WARNING` 记录取消，`ERROR` 记录操作失败。通用失败日志只记录操作摘要，具体错误继续由 Click 呈现，避免重复输出异常堆栈。完成表示命令成功返回；预览、dry-run 和 `--if-exists` 跳过的操作应结合相应字段与 stdout 判断。

## 配置与脱敏

`-v/--verbose` 仅由根命令接受，放在子命令之前，例如 `dbtalk -v postgres schema list ...`。它开启操作日志和脱敏错误详情；子命令及中间 group 不提供该开关。未提供时，连同 WARNING/ERROR 级别在内的 logging 日志均不输出，命令结果与普通 `Error:` 提示照常显示。

开启后的日志格式和级别由 [dbtalk.yaml](../dbtalk.yaml) 的 `logging` 节及 `DBTALK_LOGGING__LEVEL`、`DBTALK_LOGGING__FORMAT` 环境变量控制。默认 `INFO`；设置 `DEBUG` 可增加调试消息，设置 `WARNING` 可隐藏正常开始/完成日志。配置文件不再提供 `verbose`，`DBTALK_VERBOSE` 也不开启日志或详细错误。

CLI 在根入口按该次调用集中配置日志，业务模块不初始化日志，不重复安装 handler。直接调用 Python API 时，日志配置由调用方负责；通用命令的操作摘要属于 CLI，MySQL 备份还原与 JSONL 传输的过程日志也覆盖对应 Python API。

操作摘要只从明确允许的选项中提取标识和状态，不遍历输出所有命令参数，不记录密码、密码环境变量来源、完整 DSN、SQL、绑定参数或数据行。字符串标识使用转义表示，避免换行打断日志。JSONL 连接日志中的 DSN 隐藏密码并省略全部查询参数；实际连接 URL 保持完整。密码生成脚本和 SQL dry-run 的 stdout 是用户请求的结果，不复制进日志。
