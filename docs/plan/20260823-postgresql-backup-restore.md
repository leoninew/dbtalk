# PostgreSQL 单库逻辑备份与还原计划
最后修改时间: 2026-08-31 15:52:55

---
Review status: Accepted
Flow mode: standard
Stage: Plan
---

## Requirement basis

本计划落实已接受的 [PostgreSQL 单库逻辑备份与还原需求](../requirement/20260823-postgresql-backup-restore.md)：新增 `dbtalk postgres dump` / `restore`，以 `pg_dump` / `pg_restore` 处理一个既有 PostgreSQL 数据库。默认输出 custom archive `.dump`，不支持物理备份、cluster 全局对象或 JSONL 以外的跨功能改造。

standard 模式不单独创建 Spec；接口和实施决策在本计划中固定，待本计划被用户接受后再进入 Implementation。

## Plan assumptions

1. DSN 指向 `localhost`/`127.0.0.1` 且端口唯一映射到运行中 PostgreSQL 容器时，优先复用该容器内的 native client 和默认 Unix socket；未识别到唯一映射容器时才优先使用本机 `pg_dump`/`pg_restore`，客户端缺失再回退到 `postgres.client_image` 配置的本地 Docker image，默认 `postgres:18`。实现只检查本地 image，不自动拉取或根据服务端版本猜测 tag。用户可为更高 major 源库配置兼容 image。
2. restore 默认传递 `--no-owner --no-privileges`，以优先支持不同账号和环境间的恢复；命令提供显式的 preserve 模式后才恢复 archive 中的 owner/ACL。role 和 tablespace 仍不在 dump 范围内。
3. 第一版以 PostgreSQL 18+ 为主流版本基线，不实现旧 major 兼容分支。命令和文档仍保留原生兼容约束：`pg_dump` client 不得低于源服务端 major，目标服务端通常不应低于备份来源。
4. dump 的 `--compression-level` 仅接受 `0..9`，未传入时不添加压缩参数，保留所安装 PostgreSQL 客户端的 custom archive 默认行为。

## Implementation steps

1. 建立 PostgreSQL 命令和配置入口。
   - 在 `src/dbtalk/commands/postgres.py`、`src/dbtalk/commands/__init__.py` 和 `src/dbtalk/cli.py` 注册 `dbtalk postgres` Click group，并更新根命令说明。
   - 新建 `src/dbtalk/postgres/` 包，以 `cli.py` 承担 Click 适配，以 `dump.py`、`restore.py` 和 `client.py` 承担选项、原生命令和子进程边界。
   - 新增 `postgres` typed config，包含默认输出目录和 `client_image`（默认 `postgres:18`）；更新 `src/dbtalk/settings.py`、`dbtalk.yaml` 和 `.env.example`。连接信息不写入配置，始终由 `--dsn` 或 `--dsn-env` 提供。

2. 实现 PostgreSQL DSN 到 native client 的安全映射。
   - 复用 `dbtalk.database.dsn.parse_dsn` / `dsn_from_environment`，只接受 canonical `postgresql+psycopg://` DSN，并要求 host、user、database 有效。
   - 从 SQLAlchemy `URL` 构造无密码的 libpq `--dbname` URI，保留非敏感的主机、端口、数据库、用户和 query 参数；不得把 `+psycopg` driver 名或密码传给 `pg_dump` / `pg_restore`。
   - 本机客户端路径为每次命令生成最小权限的临时 `.pgpass` 文件，将其路径通过 `PGPASSFILE` 注入子进程；正确转义 `\\` 与 `:`，并在成功、失败和异常路径清理。Docker 路径通过继承的子进程环境及 `--env PGPASSWORD` 注入密码值，命令行只出现变量名而不包含密码。两条路径的 stderr/stdout 都经统一错误映射处理，不回显 DSN 或凭据。
   - 对本机端口唯一映射的运行中 PostgreSQL 容器，使用 `docker ps --filter publish=<port>` 探测并以 `docker exec` 调用容器内客户端；mapped dump/restore 的 archive 通过临时文件和 `docker cp` 传输，完成后清理容器内临时文件。
   - 无唯一映射容器时，Docker fallback 复用 MySQL 的宿主机地址映射规则：本机地址改为 `host.docker.internal`，非 Windows 系统附加 host-gateway 映射。备份输出目录和恢复输入文件以 bind mount 传入临时容器，不留持久容器。

3. 实现 `postgres dump`。
   - CLI 参数为 `--dsn` / `--dsn-env`、`--output` 和 `--compression-level`；严格要求前两者二选一，compression level 使用 Click 范围校验。
   - 省略输出时创建 `postgres.output_directory`，生成 `<database>-<timestamp>.dump`；已有目录同样生成时间戳文件，显式文件路径的父目录必须已存在。
   - 使用 `pg_dump --format=custom --file <sibling-temp-file> --dbname <redacted-uri>`；仅在用户指定时加入 native compression level。成功后原子替换为最终 `.dump`，失败后删除临时文件，避免把不完整备份伪装成可恢复制品。
   - 先探测 mapped container；命中时用 `docker exec` 和容器临时 archive，未命中且 `pg_dump` 不在 PATH 时才检查 Docker CLI 和 `postgres.client_image` 的本地存在性；使用 `docker run --rm --entrypoint pg_dump` 加 bind mount 写入同一个临时输出路径。Docker 不可用、image 缺失或命令失败时返回固定、可操作的诊断；不安装客户端、不拉取 image、不重试。

4. 实现 `postgres restore`。
   - CLI 参数为 `--dsn` / `--dsn-env`、`--input`、`--clean`、与 clean 绑定的 `--if-exists`、owner/ACL preserve 模式，以及正整数 `--jobs`。
   - 先以 `pg_restore --list <input>` 验证 custom archive；该检查必须在连接目标数据库和执行写入前完成。
   - 使用 `pg_restore --dbname <redacted-uri> --exit-on-error` 执行恢复；按选项附加 `--clean`、`--if-exists`、`--no-owner`、`--no-privileges` 和 `--jobs`。`--if-exists` 未同时启用 clean 时由 CLI 拒绝。
   - 默认跳过 owner/ACL；preserve 模式只改变对应 `pg_restore` 选项，不创建缺失 role。先探测 mapped container；命中时将 archive 临时复制到该容器，以容器内 `pg_restore --list` 完成预检并用默认 Unix socket 恢复，成功或失败后删除临时 archive。本机无 mapped container 且 `pg_restore` 缺失时，才以同一个配置 image 运行 `docker run --rm --entrypoint pg_restore`，将 archive bind mount 为只读文件；Docker 环境的 DSN、凭据和 hostname 映射遵循第 2 步。无论成功或失败，CLI 都只输出输入路径和非敏感结果，文档明确说明 restore 可能留下部分恢复状态。

5. 补充测试、文档和 Codex skill。
   - 新增 `tests/test_postgres.py`：命令构造、DSN/凭据脱敏、`.pgpass` 转义和清理、默认/显式输出、临时输出原子替换、archive 预检、mapped container 复用、clean 约束、owner/ACL 策略、jobs 校验及客户端缺失诊断。
   - 扩展 `tests/test_settings.py`，覆盖 `postgres` YAML、dotenv 和环境变量覆盖；按需更新 root CLI help 的断言。
   - 更新 `README.md`、新建 `docs/postgres.md`、更新 `docs/codex.md` 和插件描述；新增 `plugins/dbtalk/skills/postgres/SKILL.md`，限定为 native PostgreSQL logical dump/restore，明确写入前确认、client compatibility、custom archive 与破坏性 restore 规则。

## Files to change

- `src/dbtalk/cli.py`
- `src/dbtalk/commands/__init__.py`
- `src/dbtalk/commands/postgres.py`（新增）
- `src/dbtalk/postgres/__init__.py`（新增）
- `src/dbtalk/postgres/cli.py`（新增）
- `src/dbtalk/postgres/client.py`（新增）
- `src/dbtalk/postgres/dump.py`（新增）
- `src/dbtalk/postgres/restore.py`（新增）
- `src/dbtalk/settings.py`
- `dbtalk.yaml`
- `.env.example`
- `tests/test_postgres.py`（新增）
- `tests/test_settings.py`
- `README.md`
- `docs/postgres.md`（新增）
- `docs/codex.md`
- `plugins/dbtalk/.codex-plugin/plugin.json`
- `plugins/dbtalk/skills/postgres/SKILL.md`（新增）

## Verification plan

1. 运行 `uv run dbtalk postgres dump --help` 和 `uv run dbtalk postgres restore --help`，确认公开参数、危险选项文案和 DSN 二选一契约。
2. 运行 `uv run pytest tests/test_postgres.py tests/test_settings.py`，再执行全量 `make check` 与 `make test`，保持仓库的 Ruff、Mypy、覆盖率门槛和既有 MySQL 回归。
3. 运行 `git diff --check`，确认文档、skill、配置示例和插件清单与实际 CLI 一致。
4. 仅在用户提供可写的 PostgreSQL 18+ 测试 DSN，且本机有兼容客户端或配置 image 已在本地时，执行一次 dump、`pg_restore --list` 和恢复至预先创建的临时目标库，核对代表性 schema/data、清理临时制品和目标库。未提供环境时记录为未执行的集成验证，不伪造通过结果。

## Blockers and risks

- 当前工作环境没有 `pg_dump` 或 `pg_restore`。Docker fallback 仍依赖 Docker daemon 和配置 image，因此本地真实 archive 兼容性需要该 image 与外部 PostgreSQL 18+ 测试环境。
- `--clean` 会删除目标库对象，恢复失败不保证回滚。实现不得因提高便利性而默认启用或隐式添加该选项。
- `.pgpass` 的临时文件权限和 Windows 行为需要专门测试；清理失败只能作为受控诊断，不能在日志中暴露文件内容或密码。
- `pg_restore --jobs` 只适用于支持并行的 custom archive；实现需对输入格式和数值约束做预检并返回清晰错误。
- Docker fallback 依赖 bind mount、Docker Desktop 宿主机映射和配置 image 的 client compatibility；这些路径必须通过 mock 测试和可选集成测试分别验证。

## Rollback

本功能为新增命令、配置和文档，不迁移已有数据或修改 MySQL/JSONL 行为。若部署后需要撤销，移除 `postgres` 命令注册及对应新模块、配置和文档即可；已经创建的 `.dump` 文件是用户数据，不由应用自动删除。

## User review notes

- 用户确认不实现物理备份/还原，并要求优先 PostgreSQL 单库 dump/restore。
- 用户要求将当前 JSONL PostgreSQL export/import 与 native PostgreSQL backup/restore 保持职责分离。
- 用户确认 Docker 回退、PostgreSQL 18+ 基线和默认跳过 owner/ACL，并要求开始 Implementation。
- 用户要求继续历史实现并对齐 MySQL 的本机 Docker PostgreSQL 容器复用规则。
