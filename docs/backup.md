# 数据库批量备份

在 `scripts/backup-db.yaml` 中配置连接和数据库，可参考 `scripts/backup-db.example.yaml`。`output_directory` 的相对路径以配置文件所在目录为基准，`../data` 指向仓库的 `data/` 目录。

连接需要设置 `enabled: true`，否则 `backup`、`test` 和 `sync` 都会跳过该连接。数据库的 `enabled` 只控制备份；连接和数据库都启用时才会备份。

`databases` 可以省略、留空、设为 `null` 或 `[]`，都表示空清单。

`test` 测试所有已启用的连接，不依赖数据库清单。`sync` 查询已启用连接中的非系统库并更新清单：新增库默认启用，已有库保留设置，已不存在的库移除。新增或移除时每个库一行日志，跳过的连接保留原配置。

在仓库根目录执行：

```bash
bash scripts/backup.sh backup
bash scripts/backup.sh backup --continue-on-error
bash scripts/backup.sh test
bash scripts/backup.sh sync
bash scripts/backup.sh clean
bash scripts/backup.sh clean --no-dry-run
```

备份按连接名和数据库分目录保存，文件名使用本地时间戳 `YYYYMMDD-HHMMSS`：

```text
data/
  leon_postgres_casdoor/
    20261001-150000.dump
    20261002-150000.dump
  think_mysql_gitea/
    20261001-150000.sql.gz
    20261002-150000.sql.gz
  manifests/
    20261001-150000.md
    20261002-150000.md
```

目录名为 `<连接名>_<数据库名>`。PostgreSQL 使用 `.dump` 自定义归档，MySQL 使用 gzip 压缩的 `.sql.gz`。已有备份会保留，同一秒内重复运行会给备份和清单的时间戳追加 `-01` 等序号。

备份清单记录成功、复用和失败的结果，文件路径相对于清单目录，不包含 DSN 凭证。

完成日志中 `targets = succeeded + failed + skipped`。未启用的数据库计入跳过，续跑时复用的备份计入成功：

```text
backup run completed targets=16 succeeded=10 failed=0 skipped=6
```

使用日志或清单文件名中的时间戳续跑：

```bash
bash scripts/backup.sh backup --resume 20261001-150000 --continue-on-error
```

续跑读取当前配置，复用该时间戳下的非空备份，重新备份空文件或缺失的文件，并更新同一份清单。

`clean` 按每个连接与数据库目录独立清理，保留最近 7 日内的备份或最近 7 个备份，满足任一条件即保留。`manifests` 目录独立遵循相同规则，保留最近 7 日内的清单或最近 7 个清单。7 日按执行时的本地时间向前推算 7×24 小时，以文件名中的时间戳为准，包含临界时刻；同一秒的文件按追加序号排序。

清理会扫描 `output_directory` 下的全部数据库目录和 `manifests` 目录，包括已停用或已从配置中移除的数据库，不连接数据库。数据库目录仅处理符合当前时间戳命名规则的非空 `.dump` 和 `.sql.gz` 文件，`manifests` 目录仅处理同样命名的非空 `.md` 文件。空文件不计入最近 7 个文件，其他文件和符号链接或目录联接会保留。

`clean` 默认只预览待删除文件，添加 `--no-dry-run` 才执行删除。可通过 `clean --config <path>` 指定备份配置，默认使用 `scripts/backup-db.yaml`。
