# F01 T015 验收：从空数据库建立管理员并邀请激活用户

- 任务：F01 / GitHub Issue #16（`T015`），分支 `feat/f01-identity`
- 状态：**passed**（真实 PostgreSQL 16.4 + 真实编译 Go 二进制 + 真实 HTTP，无 mock、无跳过）
- 代码提交：`bf5cc65`（`feat(f01): ...`）；本报告与 `PROGRESS.md` 在其后的
  `docs(f01): ...` 提交中
- 主要文件：
  - `db/embed.go`、`db/migrations/0006_identity_core.sql`、`db/migrations/0005_worker_grants.sql`（修复）
  - `internal/db/migrate.go`（迁移执行器）
  - `cmd/admin/main.go`（`migrate` / `bootstrap`）
  - `internal/identity/`（argon2id、token、bootstrap、邀请、激活、会话、审计）
  - `internal/api/`（错误封套、请求日志、Cookie、三个端点、页面路由）
  - `cmd/api/main.go`（接线）
  - `apps/web/src/admin.html`、`activate.html`、`assets/app.js`、`index.html`（只加导航）
  - `tests/f01/conftest.py` + `test_t015_*.py`（37 个测试）
  - `tools/ci_checks.py`（非 quick 的 integration-tests 纳入 f01）、`tools/pg_dev.py`（公开 `psql_binary()`）
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`E:\orchara-pg`，owner `orchara_owner`，受限角色 `orchara_worker`）
  - Python 3.12.2；`golang.org/x/crypto v0.58.0`（argon2id）、`golang.org/x/term v0.47.0`、
    `github.com/jackc/pgx/v5 v5.12.0`；未引入其他依赖（`go mod tidy` 后两项为直接依赖）
  - 环境 profile：`dev-stub`（迁移、bootstrap、API 测试均用它；见第 4 节 real/stub 分报）

## 1. 交付内容

### 1.1 迁移链（从空库可执行）

- `db/embed.go`：`//go:embed migrations/*.sql`，包名 `migrations`。
- `internal/db/migrate.go`：
  - `platform.schema_migrations(version text PK, name text not null, sha256 text not null,
    applied_at timestamptz not null default now())`，执行器在第一个文件前创建（同时
    `CREATE SCHEMA IF NOT EXISTS platform`）；
  - 每个文件一个事务：失败文件整体回滚且不记账；
  - 已应用文件按 SHA-256 校验，改动即报错（不可变历史）；已记录版本从目录消失也报错；
  - 会话级 advisory lock（`pg_advisory_lock`，专用连接）串行化并发执行器；
  - 全部应用后输出 `no pending migrations`，退出码 0；
  - 错误信息包含文件名与 SQL 原因（例如 `migration 0002_drill_bad.sql failed: ERROR: relation "platform.missing_relation" does not exist`）。
- `cmd/admin migrate`：只从 `ORCHARA_PROFILE` + `ORCHARA_DB_DSN` 读连接，argv 上没有 DSN；
  `--migrations-dir` 仅用于测试/演练（默认使用嵌入链）。
- `db/migrations/0006_identity_core.sql`：`platform` schema + principals / users /
  activation_tokens / role_bindings / sessions / audit_events，含 kind↔responsible 约束、
  responsible_user FK（users 建表之后添加）、login/token_hash 唯一、sessions 两个索引、
  audit 两个索引；`REVOKE ALL ON SCHEMA platform FROM PUBLIC`，若 `orchara_worker` 存在则
  撤销其在 `platform` 上的全部表权限与 USAGE。文件头声明：`checkpoint` schema 由
  langgraph-checkpoint-postgres 独立管理、本迁移不建不改；无 vector 列、无 `CREATE EXTENSION vector`。
- **0005 修复（必须点名）**：原 `GRANT SELECT, INSERT ON app.operations TO orchara_worker`
  在空库上必然失败（该表尚不存在，链无法从零开始）。改为 DO 块，仅在
  `app.operations` 存在时授权；语义不变，链现在可以从空库跑完 0001→0006。

当前迁移文件与 SHA-256（即 `platform.schema_migrations.sha256` 的记录值）：

| 文件 | sha256 |
|---|---|
| `0001_worker_lease.sql` | `9f034bfec890b1cf7715489ba4b6e113dfa44685b1de69d465b134a1a7d9f603` |
| `0002_artifacts.sql` | `5d3497378b1161578f5e18d37c14bd8c75abf317e8f20b25549293a85ed9ba24` |
| `0003_drain.sql` | `ad9d0ec7264563d28a6b12a6eedbec67f98735c45fb142e51cbbfa3fbe419b9b` |
| `0004_fence_tokens.sql` | `dccf33d9f3104574a00c52b446be4c5c6436e50bf840f0c6a62e243a21e97f7b` |
| `0005_worker_grants.sql` | `af35035f6dff08e33fb487d1e93457482fbc6d25e601806931d66a660dca76f2` |
| `0006_identity_core.sql` | `14e6feeb7034264894dacd07e3d8cbe7e461355919af49dc537236ca04bc2301` |

### 1.2 `cmd/admin bootstrap`

- 密码只从 stdin：stdin 是终端时用 `x/term.ReadPassword` 隐藏输入；管道时读一行（不回显、不落日志）。
- 少于 12 位拒绝；从不回显密码；成功只打印 principal id 与 login。
- 单事务：`pg_advisory_xact_lock` → “是否已有启用的真人管理员” → 创建 principal + user +
  `admin` role binding + audit，一次提交。并发两个 bootstrap 只有一个成功（有测试）。
- 没有公开的 HTTP bootstrap 入口。

### 1.3 身份服务 `internal/identity`

- 密码：argon2id，PHC `$argon2id$v=19$m=65536,t=3,p=2$<salt>$<hash>`，16B salt、32B key，
  `subtle.ConstantTimeCompare`；任何非 `$argon2id$` 开头值（含哨兵 `!pending-activation`）
  永不通过；参数不同则登录成功后重新哈希（best-effort）。
- Token/密钥：32 字节 `crypto/rand`，base64url；只存 SHA-256 hex。会话 Cookie 保存原始
  token，CSRF token 的 SHA-256 存在 `sessions.csrf_hash`。
- 邀请：`users.password_hash='!pending-activation'`、`must_change_password=true`；
  activation token 24h 过期、只存哈希。
- 激活：先校验密码（弱密码/与 login 相同 → 422 且**不消费** token），再用单条条件 UPDATE
  `SET consumed_at=now() WHERE token_hash=$1 AND consumed_at IS NULL AND expires_at>now()`
  做 CAS；同一事务写新密码并置 `must_change_password=false`。
- 会话：24h；解析时由数据库 `now()` 判定过期；`revoked_at`/`disabled_at` 分别返回
  `session_revoked`/`principal_disabled`；角色每次从 `role_bindings` 重新读取。
- 审计：`identity.RecordAudit(ctx, tx, actorID, action, resourceType, resourceID, metadata)`，
  metadata 只放 login/roles 等非秘密字段。T015 写入 `admin.bootstrap`、`user.invited`、
  `user.activated`、`auth.login`。
- 未知 login 与错误密码走同一个错误码，且都消耗一次 argon2id 校验（dummy hash）。

### 1.4 HTTP `internal/api` + `cmd/api`

- `POST /api/v1/auth/login`：200 `{principal_id,csrf_token,expires_at}` + `Set-Cookie:
  platform_session=...; Path=/; HttpOnly; SameSite=Lax[; Secure]`；401 `invalid_credentials`、
  403 `principal_disabled`、400 `invalid_json`。
- `POST /api/v1/admin/users`（需 admin 会话）：201 `{principal_id,activation_token,expires_at}`；
  401 `unauthenticated`、403 `forbidden`、409 `login_taken`、422 `validation_failed`（含 `details.field`）。
- `POST /api/v1/auth/activate`：200 `{id,state:"activated"}`；409 `activation_consumed` /
  `activation_expired` / `activation_invalid`；422 `validation_failed`；400 `invalid_json`。
- 统一错误封套 `{"error":{"code","message","request_id"[,"details"]}}`；回显
  `X-Request-ID`；认证响应一律 `Cache-Control: no-store`。
- Cookie `Secure`：`linux-sandbox`/`real-model` 开，`dev-stub`/`capacity-stub` 关，未知
  profile fail-safe 开（Go 单测 `TestSecureCookieFollowsTheProfile`）。
- 请求日志只含 method、无 query 的 path、status、duration、request_id；不记录 body、
  Cookie、Authorization 或 token（Go 单测 + 集成测试双向断言）。客户端传入的
  `X-Request-ID` 只接受短 ASCII，防止换行伪造日志行。
- 修复了一个由测试暴露的真实缺陷：handler 在读取 body 前返回 401/403 时，Go 关闭
  `Connection: close` 请求会带上未读字节，Windows 客户端收到 TCP RST。现在中间件在
  handler 返回后、连接关闭前排空剩余请求体，客户端能正常读到错误响应。
- 保持 F00 行为：`/`、`/healthz`、`/readyz`、`/health/live`、`/health/ready`、`/api/status`
  不变；`/` 页面原有字符串全部保留，只新增 `/admin`、`/activate`、`/apps` 三个链接。
- 从 `ORCHARA_WEB_DIR` 提供 `/admin`、`/activate`、`/assets/app.js`（文件名是常量，无路径穿越）。

### 1.5 页面

- `admin.html`：登录块（POST `/api/v1/auth/login`）+ 邀请表单（login、display_name、roles）
  POST `/api/v1/admin/users`；一次性 token 只写进 `<textarea readonly>` 并标注“只显示一次”；
  登录/邀请各有可见状态元素（成功 `ok`、失败 `bad`，显示 API 的 `code` 与 `message`）；
  后续写请求自动带 `X-CSRF-Token`（T017 打开强制校验后页面无需再改）。
- `activate.html`：token `<textarea>`、密码 + 确认、POST `/api/v1/auth/activate`；
  成功与失败状态可见；token 只从请求体提交，不读地址栏、不入 storage、不进 URL。
- `assets/app.js`：`api(path, {method, body, csrf})`（JSON、解析 `{error}` 抛出带 `code`
  的错误、不跟随重定向）+ `escapeHTML`；无框架、无构建步骤。

### 1.6 测试与 CI

- `tests/f01/conftest.py`：会话级编译 `cmd/api`、`cmd/admin`；`fresh_database` 用
  `CREATE DATABASE` 建临时库并在 teardown `DROP ... WITH (FORCE)`；`api_server(dsn)`
  在空闲端口启动真实进程并把 stdout+stderr 落盘；cookie 感知、不跟随重定向的 HTTP 客户端；
  直接执行 SQL 的 psql 助手（密码只走 `PGPASSWORD`，不上 argv）。
- 37 个测试覆盖 7 条验收（见第 2 节），失败路径、当前授权边界、重试/恢复边界都有断言。
- `tools/ci_checks.py` 的非 quick `integration-tests` 已加入 `product/tests/f01`，
  一条命令即覆盖 F01；`--quick` 路径未改动（仍是 spec-drift + tooling/p01，快速且不依赖数据库）。

## 2. 验收标准逐条对照

| # | 验收 | 证据测试 |
|---|---|---|
| 1 | 真实临时 PG 从空库迁移成功 | `test_t015_migrations.py::test_migrate_from_empty_database_records_the_chain`（`CREATE DATABASE` + 6 个文件入账 + 身份表存在） |
| 2 | 重复执行和失败迁移可解释 | `test_rerun_reports_no_pending_and_changes_nothing`、`test_failing_migration_rolls_back_and_is_not_recorded`（非零退出、含文件名与 SQL 原因、前序保留、失败文件不记账、partial DDL 回滚）、`test_modified_applied_migration_fails_on_checksum`、`test_concurrent_migrate_runners_record_the_chain_once`、`test_migrate_refuses_without_a_database_dsn`、`test_migrate_rejects_an_unknown_profile` |
| 3 | worker 角色不能改业务表 | `test_t015_privileges.py::test_worker_cannot_write_any_platform_table`（6 张表 × INSERT/UPDATE/DELETE 全部 permission denied）、`test_worker_cannot_create_objects_in_the_platform_schema`、`test_worker_has_no_recorded_privileges_on_platform`、`test_worker_keeps_its_own_bookkeeping_access`（对照组） |
| 4 | 重复 bootstrap 拒绝 | `test_t015_bootstrap.py::test_bootstrap_creates_exactly_one_audited_admin`（1 principal/user/admin binding/audit row，密码为 argon2id 且不在输出中）、`test_second_bootstrap_is_refused`、`test_weak_password_is_refused_without_creating_anyone`、`test_concurrent_bootstraps_leave_exactly_one_admin`；密码不进 API 日志由 `test_t015_secrets.py::test_no_secret_reaches_the_api_log_or_a_request_url` 证明（该密码正是 bootstrap 时设置的密码，随后用于登录） |
| 5 | 过期/重复激活拒绝 | `test_t015_identity_api.py::test_activation_succeeds_once_and_the_user_can_then_log_in`（成功一次、复用 409 `activation_consumed`、之后可登录）、`test_expired_activation_is_rejected`（crafted UPDATE 过期 → 409 `activation_expired`）、`test_unknown_activation_token_is_rejected`、`test_password_validation_does_not_consume_the_token`（弱/同 login 密码 422 且 token 仍可用）；邀请/鉴权边界见 `test_admin_invite_returns_a_one_time_token_that_is_only_hashed_at_rest`、`test_duplicate_login_is_a_conflict`、`test_invalid_roles_are_rejected[*]`、`test_invite_requires_an_admin_session`（401/403）、`test_login_returns_a_session_cookie_and_a_hashed_csrf_token`、`test_disabled_principal_cannot_log_in` |
| 6 | 密码与令牌不进日志或查询 URL | `test_t015_secrets.py::test_no_secret_reaches_the_api_log_or_a_request_url`（整段流程捕获 API stdout/stderr：管理员/受邀者密码、错误密码、activation token、两个 session cookie、两个 csrf token、header marker、query marker 均不出现；请求路径无 token/无 query；日志行只有 method/path/status/duration/request_id）、`test_the_log_file_does_not_contain_the_database_password` |
| 7 | 端到端入口实际演示，失败/授权/恢复边界有断言 | 第 1–6 条全部由真实二进制 + 真实 PostgreSQL + 真实 HTTP 的 `tests/f01` 覆盖；页面见 `test_t015_pages.py` 的 6 个测试（表单、端点、可见状态元素、token 不在 URL/storage、`ORCHARA_WEB_DIR` 生效、`/` 回归） |
| 8 | 记录版本、输入/hash、实际命令、原始结果与 profile；stub/real 分报 | 本报告第 1、3、4、7 节 |

## 3. 实际命令与原始结果（本机实测）

```text
$ cd E:\Orchara\product
$ go vet ./...
（无输出）
VET_EXIT=0

$ go build ./...
（无输出）
BUILD_EXIT=0

$ go test ./...
?   	orchara/platform/cmd/admin	[no test files]
?   	orchara/platform/cmd/api	[no test files]
?   	orchara/platform/cmd/maintenance	[no test files]
?   	orchara/platform/cmd/supervisor	[no test files]
?   	orchara/platform/cmd/worker	[no test files]
?   	orchara/platform/db	[no test files]
ok  	orchara/platform/internal/api	1.085s
ok  	orchara/platform/internal/config	(cached)
ok  	orchara/platform/internal/db	(cached)
ok  	orchara/platform/internal/health	(cached)
ok  	orchara/platform/internal/identity	(cached)
ok  	orchara/platform/internal/supervisor	(cached)
TEST_EXIT=0
```

```text
$ cd E:\Orchara
$ python -m pytest product/tests/f01 -q
.....................................                                    [100%]
37 passed in 103.44s (0:01:43)
```

```text
$ cd E:\Orchara
$ python -m pytest product/tests -q
........................................................................ [ 17%]
........................................................................ [ 35%]
........................................................................ [ 52%]
........................................................................ [ 70%]
........................................................................ [ 87%]
...................................................                      [100%]
411 passed in 322.39s (0:05:22)
```

> 门禁前基线：`product/tests` = 374 passed（本票新增 37 个，全部真实执行、无 skip）。

```text
$ cd E:\Orchara
$ python product/tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
SYNC_EXIT=0

$ python product/tools/ci_checks.py --quick --json
{
  "checks": [
    {"name": "spec-drift", "status": "passed", "detail": ""},
    {"name": "python-tests-fast", "status": "passed", "detail": ""}
  ],
  "passed": 2,
  "skipped": 0,
  "failed": [],
  "skipped_names": [],
  "duration_seconds": 18.1,
  "verdict": "ok",
  "ok": true
}
CI_EXIT=0
```

### 迁移/管理命令的原始输出（临时库，`dev-stub`）

```text
=== admin migrate（空库，第一次） ===
orchara-admin 2026/11/01 15:26:33 applied 0001_worker_lease.sql
orchara-admin 2026/11/01 15:26:33 applied 0002_artifacts.sql
orchara-admin 2026/11/01 15:26:33 applied 0003_drain.sql
orchara-admin 2026/11/01 15:26:33 applied 0004_fence_tokens.sql
orchara-admin 2026/11/01 15:26:33 applied 0005_worker_grants.sql
orchara-admin 2026/11/01 15:26:33 applied 0006_identity_core.sql
orchara-admin 2026/11/01 15:26:33 6 migration(s) applied
exit: 0

=== admin migrate（第二次） ===
orchara-admin 2026/11/01 15:26:33 no pending migrations
exit: 0

=== 记账（version name sha256 前 12 位） ===
0001 0001_worker_lease.sql 9f034bfec890
0002 0002_artifacts.sql 5d3497378b11
0003 0003_drain.sql ad9d0ec72645
0004 0004_fence_tokens.sql dccf33d9f310
0005 0005_worker_grants.sql af35035f6dff
0006 0006_identity_core.sql 14e6feeb7034

=== bootstrap（管道输入密码） ===
bootstrapped principal d22f0d65-9ca9-41e1-b6da-a82d04b5ac34 login=admin
exit: 0

=== 第二次 bootstrap ===
orchara-admin: an enabled human administrator already exists
exit: 1

=== worker 写入 platform（应拒绝） ===
ERROR:  permission denied for schema platform
LINE 1: INSERT INTO platform.principals (id, kind, display_name) VAL...
                    ^
exit: 1

=== worker 写自己的 app.workers（对照组，应允许） ===
exit: 0
```

失败迁移与校验和漂移的原始输出在测试断言中逐字核对：

```text
migration 0002_drill_bad.sql failed: ERROR: relation "platform.missing_relation"
does not exist (SQLSTATE 42P01)
migration 0001_drill_one.sql was modified after it was applied: checksum mismatch
(recorded <old sha256>, file <new sha256>); applied migrations are immutable
```

## 4. 实测结果（real vs stub）

- **real**：PostgreSQL 16.4（真实持久化、真实 `CREATE DATABASE`、真实权限检查）、
  `cmd/admin` / `cmd/api` 真实编译二进制、真实 TCP HTTP、真实进程、真实文件日志捕获。
- **stub**：环境 profile 为 `dev-stub`，仅表示这是开发环境（无真实模型调用与 Linux 沙箱）；
  本票不涉及模型，故没有 stub 业务结论。密码哈希、迁移、权限、HTTP 全部是真实实现与真实运行。
- **not_run**：见第 7 节。

## 5. 设计要点补充

- 迁移执行器在应用任何文件前先建 `platform` 与记账表：否则“从空库”时 0001 只有 `app`
  schema，记账表无处可放。0006 里仍是 `CREATE SCHEMA IF NOT EXISTS platform`（幂等）。
- `platform.schema_migrations` 的 checksum 是**文件字节**的 SHA-256，不做换行归一化：
  迁移文件一旦应用就不可改，这是刻意选择的“响亮失败”，避免与环境漂移。
- worker 的拒绝来自数据库授权（无 USAGE、无表权限），不是应用层判断；对照组证明同一
  连接仍能写 `app.workers`，所以“拒绝”不是“连不上”。
- 登录对未知账号与错误密码返回同一错误码并都做一次 argon2id 校验；禁用账号只在密码正确
  后才返回 403，避免账号枚举。
- 会话过期判定用数据库 `now()`，不依赖进程时钟；CSRF 只存哈希。

## 6. 与 DESIGN.md 的偏差（及原因）

1. **T015 含登录基础设施**：合同里的 admin 端点要求 `sessionCookie`，所以本票实现了
   `POST /auth/login`、会话签发与 CSRF 哈希存储。完整 login/logout/refresh/CSRF 强制/
   登录限流生命周期属 T017；本票未做 CSRF 强制与限流（页面与测试已提前发送
   `X-CSRF-Token`，T017 打开强制后不需要改页面）。
2. **`applications`、`resource_grants`、`policy_epochs`、`api_keys` 未创建**：分别属
   T019/T020；0007/0008 由它们交付。本票只交付 DESIGN §3.1 的六张身份表。
3. **审计助手的包位置**：DESIGN §5 写作 `audit.Record(...)`，但 §2 的包清单没有
   `internal/audit`。实现为 `identity.RecordAudit`，签名与语义一致（tx、actor、action、
   resource_type、resource_id、metadata），T019/T020 可直接复用。若后续需要独立 audit 包，
   是一次纯搬移。
4. **`Identity` 不携带 CSRF 明文**：DESIGN §5 规定只存 `sessions.csrf_hash`，因此从会话
   解析出的 `Identity` 只有 principal/kind/roles/expires。T017 的 `GET /auth/me` 要求返回
   `csrf_token`，需要在 T017 决定策略（例如 /me 时轮换 csrf 并返回新值，或使用双提交
   Cookie）。这是 DESIGN 未闭环的一处，本票没有擅自新增存储。
5. **`cmd/admin` 的配置加载**：新增 `config.LoadDatabase`（只要求 profile + DSN），而不是
   复用 API 的 `config.Load`。原因：`real-model` 的迁移/初始化不应因为缺少模型端点而失败；
   环境变量仍是同样的 `ORCHARA_PROFILE` + `ORCHARA_DB_DSN`，且未知 profile 仍拒绝。
6. **bootstrap 不禁止“密码等于 login”**：DESIGN 对 bootstrap 只规定最短 12 位；激活路径
   才明确要求密码不得等于 login。若需要统一，属后续小改动。
7. **登录写 `auth.login` 审计**：DESIGN 只点名 bootstrap/invite/activate 的审计；登录审计
   只记录 login（非秘密），用于身份操作可追溯。如认为噪声过大，可在 T017 调整。
8. **新增 `pg_dev.psql_binary()`**：让新测试用公开助手而不是私有 `_tool`（与上一轮复核的
   卫生要求一致）。
9. **`tools/ci_checks.py`**（主代理追加要求）：非 quick 的 `integration-tests` 现在同时跑
   f00/p02/f01；`--quick` 保持不变，仍快速且不碰数据库。
10. **`0005_worker_grants.sql` 的一处修复**：见第 1.1 节，不修则空库迁移不可能成功。

## 7. 限制 / not_run

- **CSRF 强制**：T015 不强制；`sessions.csrf_hash` 已存储，`X-CSRF-Token` 已被接收但在
  T017 前不作为拒绝依据。
- **登出 / `/auth/me` / 刷新**：未实现，属 T017。
- **登录限流**（5 次/60s，内存单实例）：未实现，属 T017。
- **浏览器级验证**：未跑真实浏览器/无头浏览器；页面断言是 HTTP 内容 + JS 源码行为断言
  （表单 id、端点、状态元素、token 不在 URL/storage）。HCI 级验收不在本票。
- **Secure Cookie 的 HTTP 级实测**：`linux-sandbox`/`real-model` 启动 API 需要模型端点配置，
  集成测试未覆盖；Cookie 属性由 Go 单测 `TestSecureCookieFollowsTheProfile` 证明。
- **argon2id 时序**：实现了 dummy verify 与常量时间比较，但没有做统计意义上的时序测量。
- **并发迁移**：用两个真实进程验证 advisory lock；未做 N>2 或崩溃中断的压测。
- **RLS**：本票不宣称 PostgreSQL 全表 RLS；拒绝来自角色授权，符合 DATA-MODEL 的首版口径。
- **密码重置 / 遗忘密码**：不在 F01 范围，未实现。
- 未推送、未关闭 issue（由主代理完成）。

## 8. T017 需要知道 / 不能破坏的接口

- `identity.Service.Authenticate(ctx, rawCookie)` 已返回 `Identity{PrincipalID, Kind, Roles,
  ExpiresAt}` 并区分 `unauthenticated` / `session_expired` / `session_revoked` /
  `principal_disabled`；T017 的中间件可直接复用。
- CSRF 校验应比较 `identity.HashToken(header)` 与 `sessions.csrf_hash`（常量时间），
  不要比较原始值；登录响应里的 `csrf_token` 是唯一一次明文交付。
- 页面已发送 `X-CSRF-Token`，测试的 admin 写请求也都带 `csrf=`；打开强制校验后
  `test_t015_identity_api.py` 应保持绿色。
- **不要修改 `0006_identity_core.sql`**（checksum 不可变；已有数据库会响亮失败）。
  T017 不需要新表；若发现需要，属于设计缺口，应按流程报告而不是改历史迁移。
- `cmd/api/main.go` 的接线是 `api.New(identityService, cfg.WebDir, cfg.Profile, logger).Register(mux)`；
  新增路由请在同一处注册，并保持 `/`、`/health*`、`/api/status` 行为与 `index.html` 字符串。
- 请求日志中间件已排空未读 body；若为 SSE/流式上传改造，需要重新评估该行为。
- `tests/f01/conftest.py` 的 `api_server(dsn)` 工厂与 `fresh_database` 可被 T017 直接复用。

## 9. 复现命令

```powershell
cd E:\Orchara\product
$env:GOCACHE='E:\orchara-toolchains\gocache'; $env:GOMODCACHE='E:\orchara-toolchains\gomodcache'
& E:\orchara-toolchains\go\bin\go.exe vet ./...
& E:\orchara-toolchains\go\bin\go.exe test ./...

cd E:\Orchara
$env:PYTHONIOENCODING='utf-8'
python -m pytest product/tests/f01 -q
python -m pytest product/tests -q
python product/tools/sync_spec.py --check
python product/tools/ci_checks.py --quick --json
```
