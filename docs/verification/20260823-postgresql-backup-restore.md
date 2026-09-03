# PostgreSQL 单库逻辑备份与还原验证
最后修改时间: 2026-08-31 17:57:29

---
Review status: Accepted
Flow mode: standard
Stage: Verification
---

## Requirement alignment

本次增量实现继续落实已接受的 PostgreSQL 单库逻辑备份与还原需求，并补齐与 MySQL 对齐的本机 Docker 容器复用规则：

- 对 `localhost`/`127.0.0.1` 且端口唯一映射到运行中 PostgreSQL 容器的 DSN，dump/restore 优先使用该容器内的原生 `pg_dump`/`pg_restore`。
- mapped 路径使用容器默认 Unix socket，不经过 `host.docker.internal`；无唯一映射容器时才回到本机客户端，再回退到配置的本地 Docker image。
- mapped dump 使用容器临时 archive 和 `docker cp` 取回；mapped restore 在容器内完成 archive `pg_restore --list` 预检和实际恢复，并在成功或失败后清理临时 archive。
- 原有 custom archive、owner/ACL、clean、jobs、DSN 凭据保护和 Docker fallback 语义保持不变。

## Plan alignment

- 已更新历史 Requirement 和 Plan，明确 mapped-container 复用是 PostgreSQL 的客户端选择规则。
- `src/dbtalk/postgres/client.py` 新增 `docker_mapped_postgres_container()`，按运行状态和发布端口只接受唯一容器。
- `src/dbtalk/postgres/dump.py` 和 `restore.py` 分别增加 mapped dump/restore 实现；mapped 模式的 libpq URI 不含外部 host/port，使用 Unix socket。
- 原有未命中 mapped 容器时的本机客户端和临时 Docker image fallback 保留。
- standard 模式不创建独立 Spec；按已接受 Requirement 和 Plan 验证。

## Actual diff summary

- 修改 PostgreSQL client、dump、restore，实现本机端口映射容器探测和复用。
- 增加 mapped dump/restore、archive 复制、临时文件清理和 socket URI 回归测试。
- 更新 PostgreSQL Requirement、Plan、手册、Codex 边界和 skill，使过程文档与实现一致。

## Expected vs actual files

本次增量实际修改文件：

- `src/dbtalk/postgres/client.py`
- `src/dbtalk/postgres/dump.py`
- `src/dbtalk/postgres/restore.py`
- `tests/test_postgres.py`
- `docs/requirement/20260823-postgresql-backup-restore.md`
- `docs/plan/20260823-postgresql-backup-restore.md`
- `docs/postgres.md`
- `docs/codex.md`
- `plugins/dbtalk/skills/dbtalk-postgres/SKILL.md`
- `docs/verification/20260823-postgresql-backup-restore.md`

这些文件均属于本次 PostgreSQL 容器复用范围；未修改 MySQL 或 JSONL 产品逻辑。

## Acceptance checklist

- [x] 本机 mapped PostgreSQL 容器按 `docker ps --filter status=running --filter publish=<port>` 探测，只有唯一结果才进入复用路径。
- [x] mapped dump 和 restore 均通过 `docker exec` 使用容器内原生客户端和默认 Unix socket。
- [x] mapped restore 的 archive 预检和实际恢复均在已有容器内执行，临时 archive 在 finally 路径清理。
- [x] 无 mapped 容器时保留本机客户端优先、配置 Docker image fallback 的既有顺序。
- [x] mapped 命令 argv 不包含 `host.docker.internal` 或密码；Docker 密码仅通过 `PGPASSWORD` 环境传递。
- [x] custom archive、输入校验、clean/if-exists、owner/ACL 和 jobs 行为保持通过。
- [x] Requirement、Plan、手册、Codex 文档和 PostgreSQL skill 已同步更新。

## Test results

### Automated checks

以下检查通过：

```text
uv run --locked --no-sync pytest -q                              210 passed, 1 skipped, 5 subtests passed
uv run --locked --no-sync ruff check src tests                   passed
uv run --locked --no-sync mypy src tests                         passed
uv lock --check                                                  passed
uv build                                                          passed
uv run --locked --no-sync python scripts/install.py plugin check  passed
git diff --check                                                  passed
```

CLI 合同检查通过：`postgres dump --help` 展示 custom archive 和 compression 参数，`postgres restore --help` 展示 `--clean`、`--if-exists`、owner/ACL 和 `--jobs`。

本次触碰的 Python 文件通过 Ruff format check。全仓库 format check 仍报告既有的 `tests/test_backup_db.py` 格式差异；该文件与本次变更无关，因此未修改。

### Mapped-container coverage

新增单测覆盖：

- mapped dump 优先于宿主机 `pg_dump` 和临时 Docker image；
- mapped restore 的 archive copy、容器内 `pg_restore --list`、实际恢复和清理；
- mapped URI 不使用 `host.docker.internal`，且只在唯一端口映射容器时命中。

当前 Docker daemon 的只读检查发现一个 `postgres:18` 容器发布 `127.0.0.1:5432`，容器内 `pg_dump`/`pg_restore` 版本为 18.6。未使用未知凭据执行真实恢复写入。

### PostgreSQL integration

未执行真实 mapped dump/restore 集成：当前没有用户明确提供的 PostgreSQL DSN、目标数据库和凭据。为避免在未知目标库上产生写入，未从容器或其他配置猜测认证信息。历史 verification 中已有 PostgreSQL 16.15 和 18.6 的 Docker fallback 集成记录，但不替代本次 mapped-container 真实写入验证。

## Missed or expanded scope

- 本次只补齐已有 PostgreSQL dump/restore 的容器复用路径，没有引入新的备份格式、数据库生命周期操作或 MySQL 行为变更。
- 未执行真实 mapped PostgreSQL restore；这是外部 DSN/凭据条件缺失，不是单元实现失败。

## Risks and incomplete items

- `docker ps` 只按运行状态和发布端口判断唯一容器，不额外验证镜像类型；唯一映射到非 PostgreSQL 容器时，容器内客户端命令会显式失败，不会静默切换到其他目标。
- mapped restore 使用 `docker cp` 把 archive 放入数据库容器，容器临时目录需要可写空间；清理失败会被抑制，但主命令错误仍会返回。
- restore 仍遵循 `pg_restore` 的部分失败语义，不能整体回滚；`--clean` 仍是破坏性选项。

## Conclusion

Requirement、Plan 与实际 diff 一致。PostgreSQL dump/restore 的 mapped-container 复用已实现并由定向和全量自动检查覆盖；所有自动检查通过。真实 mapped PostgreSQL 写入集成因缺少明确 DSN/凭据未执行，属于交付前需要用户在目标环境补充的验证项。
