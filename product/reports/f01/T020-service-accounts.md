# F01 T020 验收：创建服务账号并用可撤销 Key 调用授权 API

- 任务：F01 / GitHub Issue #21（`T020`），分支 `feat/f01-identity`（HEAD `d0735af` + 本票提交）
- 状态：**passed**（真实 PostgreSQL 16.4 + 真实编译 Go 二进制 + 真实 HTTP；无 mock、无跳过）
- 代码提交：
  - `49d1877`（`feat(f01): service accounts, scoped API keys and bearer authentication`）
  - `e6eb3e8`（`test(f01): cover the T020 service-account, key, scope, ACL and page boundaries`）
  - 本报告与 `PROGRESS.md` 在其后的 `docs(f01): ...` 提交中
- 主要文件：
  - `db/migrations/0008_api_keys.sql`（新增：`platform.api_keys` + principal 索引 + worker/PUBLIC 权限拒绝）
  - `internal/identity/apikeys.go`（新增：scope 注册表、服务账号创建、key 创建/撤销/认证、凭据判定）
  - `internal/identity/service.go`（`Identity` 增加内部字段 `Scopes`/`KeyID`，不序列化）
  - `internal/api/apikeys.go`（新增：3 个 admin 端点 + `GET /api/v1/memories` 的个人数据边界）
  - `internal/api/authz.go`（`Authorization: Bearer` 分支、严格头解析、`requireScope`）
  - `internal/api/applications.go`（列表/详情在 catalog 之前检查 scope）、`internal/api/server.go`（路由、logout 只接受会话）
  - `apps/web/src/admin.html`（服务账号与 API key 表单、一次性明文、撤销表单）
  - `internal/{identity,api}/*_test.go`、`tests/f01/t020_stack.py`、`tests/f01/test_t020_*.py`（30 个集成测试）
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`E:\orchara-pg`）
  - Python 3.12.2；**未新增任何依赖**（`go.mod` 未改，`golang.org/x/crypto` 等仍只有既有依赖）
  - 环境 profile：`dev-stub`（同 T015/T017/T019；见第 4 节 real/stub 分报）
  - 基线：T019 后 `product/tests` = 468 passed、`tests/f01` = 94 passed；
    本票后 `product/tests` = 498 passed、`tests/f01` = 124 passed（新增 30，无回归、无跳过）

## 1. 交付内容

### 1.1 迁移 `0008_api_keys.sql`

- `platform.api_keys(id uuid PK, principal_id uuid NOT NULL REFERENCES platform.principals,
  key_hash text NOT NULL UNIQUE, scopes text[] NOT NULL CHECK (cardinality(scopes) > 0),
  expires_at timestamptz NOT NULL, revoked_at timestamptz)` + 索引 `api_keys_principal (principal_id)`。
  语句全部 schema 限定；没有 vector 列、没有 `checkpoint` DDL；不修改 0005/0006/0007。
- 权限边界与 0006/0007 相同：`REVOKE ALL ON SCHEMA platform FROM PUBLIC`；若 `orchara_worker`
  存在，再次撤销其 `platform` 全部表/序列权限与 schema USAGE。因此运行角色既读不到 key 哈希，
  也不能伪造/撤销 key。
- 文件头与 `COMMENT ON TABLE` 明确：**只有 service principal 可以持有 key**；这条跨表规则
  （`api_keys.principal_id` 必须指向 `principals.kind='service'`）无法用普通 FK/CHECK 表达，
  在 `identity.CreateAPIKey` 中强制，并由 `test_t020_api_keys.py` 的真实 HTTP 测试证明。
- `scopes` 的词汇表固定在代码（F01 恰好 `application:read`），数据库只保证非空；
  `key_hash` 只存 `SHA-256(secret)` 的小写 hex，明文只出现在创建响应一次。
- 迁移文件哈希：`0008_api_keys.sql` = `17f8582bbb5bbb0f0692b6e9066dab7918b2dfe053bf5a1fc6479a2883d3175e`
  （与 `platform.schema_migrations.sha256` 的记录值一致）；`internal/identity/apikeys.go` =
  `8cc846084145728c825330c2e7711333c9e56638641a4d2fe11f87c88697396d`；
  `internal/api/apikeys.go` = `9e4c6cd7da74a66d094662c3f4c757655e1accc8f85e3ba2c7c3a426f212a982`。

### 1.2 身份服务 `internal/identity/apikeys.go`

```go
func (s *Service) CreateServiceAccount(ctx, actorID, displayName, responsibleUserID string) (string, error)
func (s *Service) CreateAPIKey(ctx, actorID, principalID string, scopes []string, expiresAt time.Time) (*APIKeySecret, error)
func (s *Service) RevokeAPIKey(ctx, actorID, keyID string) (string, error)
func (s *Service) AuthenticateAPIKey(ctx, presented string) (*Identity, error)
```

- **scope 注册表**：`ScopeApplicationRead = "application:read"`，`knownScopes` 恰好一项。
  `normalizeScopes` 拒绝空集合、空白项、重复项、未知项（422 `validation_failed` 字段 `scopes`）；
  `personal:*` 与 `memory:*` 在未知检查之前命中 `PersonalScopeError`（`errors.Is(..., ErrPersonalScope)`），
  由 HTTP 映射成专属 422 `personal_scope_forbidden`。即使以后注册表扩展，个人域前缀仍然先被拒绝。
- **创建服务账号**（一个事务）：校验 display name（422 字段 `display_name`）、responsible 的 UUID 形状
  （422 字段 `responsible_user_id`）；事务内查 responsible：不存在 → `ErrPrincipalNotFound`（404
  `principal_not_found`），不是 `kind='user'` 或没有 `users` 行 → 422 字段 `responsible_user_id`，
  已禁用 → `ErrPrincipalDisabled`（HTTP 映射 422）；随后 `INSERT platform.principals(kind='service',
  responsible_user_id)` + `audit.Record("service_account.created")`。服务账号没有 `users` 行、没有角色，
  因此不可能登录。
- **创建 key**（一个事务）：目标必须是已存在的 service principal（不存在 → 404；人类 → 422 字段
  `principal_id`；目标已禁用 → 422），其 responsible human 必须仍存在且启用（否则 422 字段
  `principal_id`）；scope 校验见上；`expires_at` 必须严格晚于 `Service.Now()`（注入时钟，422 字段
  `expires_at`）；secret = `orchara_` + base64url(32 随机字节)，只存 `HashToken(secret)`；
  `INSERT platform.api_keys` + `audit.Record("api_key.created", resource_type="api_key",
  metadata={principal_id, scopes, expires_at})`，metadata 永不含 secret/hash。响应
  `201 {id, secret}`，`Cache-Control: no-store`。
- **撤销**：`UPDATE ... SET revoked_at=now() WHERE id=$1 AND revoked_at IS NULL RETURNING principal_id`；
  已撤销再删 = 204 且**不写第二条审计**；未知/畸形 id → `ErrAPIKeyNotFound`（404 `key_not_found`）；
  成功时同一事务写 `api_key.revoked`。返回 owner principal id。
- **认证**：按 `SHA-256(presented)` join `principals`（以及负责人的 `principals` 行）一条查询；
  纯函数 `apiKeyFailure` 固定判定顺序：
  1. revoked 或 expired → `ErrInvalidKey`（401 `invalid_key`，与未知 key 完全不可区分）；
  2. 非 service principal 或 principal 已禁用 → `ErrPrincipalDisabled`（403 `principal_disabled`）；
  3. service 的 responsible human 不存在或已禁用 → `ErrResponsibleDisabled`（403 `responsible_disabled`）。
  成功返回 `Method=AuthMethodAPIKey`、`Kind="service"`、`Roles=nil`、`ExpiresAt=key.expires_at`、
  `Scopes`、`KeyID`；`CSRFToken`/`CSRFHash`/`SessionHash` 为空。因此 `/auth/me` 的
  `IdentityView` 天然是 `roles:[]`、`csrf_token:null`、`expires_at` 为 key 过期时间。
- `Identity` 新增内部字段 `Scopes`/`KeyID`：`IdentityView` 仍是显式投影，任何内部字段不会出现在
  响应里（Go 单测固定了 JSON 形状）。

### 1.3 HTTP 层

- **bearer 只在 `authenticate` 一个入口**：`Authorization` 头存在时优先且权威。
  `parseBearerHeader` 只接受**恰好一个** `Bearer <secret>`（scheme 大小写不敏感，secret 不允许空白；
  重复头、缺 scheme、双空格、附加单词均视为畸形）；畸形 → 401 `invalid_key`，**绝不回退 Cookie**；
  没有 `Authorization` 头时 Cookie 路径逐字不变。key 认证错误映射：
  `invalid_key` 401 / `principal_disabled` 403 / `responsible_disabled` 403。
- **scope 先于 ACL**：`GET /api/v1/applications` 与 `GET /api/v1/applications/{application_id}` 在
  `authorize` 之后、调用 `catalog` 之前执行 `requireScope(caller, "application:read")`；只有
  `AuthMethodAPIKey` 受约束，会话不受影响。缺 scope → 403 `insufficient_scope`；有 scope 无 ACL →
  catalog 的 404（不可枚举）；服务账号始终用**自己的** principal id 调 `ListVisible/GetVisible`。
- **新增端点**（全部 `authorize(...,"admin")` 先于读 body；服务 key 无角色 → 403 `forbidden`）：

  | 端点 | 成功 | 失败 |
  |---|---|---|
  | `POST /api/v1/admin/service-accounts` `{display_name,responsible_user_id}` | 201 `{id,state:"active"}` | 422 `validation_failed`（details.field）、404 `principal_not_found` |
  | `POST /api/v1/admin/api-keys` `{principal_id,scopes[],expires_at}` | 201 `{id,secret}` | 422 `personal_scope_forbidden` / `validation_failed`、404 `principal_not_found` |
  | `DELETE /api/v1/admin/api-keys/{key_id}` | 204（无 body、no-store；重复删除仍 204） | 404 `key_not_found` |
  | `GET /api/v1/memories?limit` | 人类会话 200 `{items:[],next_cursor:null}` | 服务凭据 403 `personal_scope_forbidden`；坏 limit 422 |

- **`POST /api/v1/auth/logout` 只接受会话 Cookie**：bearer 调用返回 401 `unauthenticated`（没有
  可吊销的服务端会话；key 生命周期属于 admin 撤销端点），不发 `Set-Cookie`、不碰会话行；
  Cookie + CSRF 的行为逐字不变。
- **`GET /api/v1/memories`**（`authorize(w,r,"")`）：`caller.Kind=="service"` 或
  `Method==AuthMethodAPIKey` 时在**解析 limit、接触任何存储之前**返回 403 `personal_scope_forbidden`；
  人类会话返回诚实的空授权结果（个人记忆存储属于 F09；本票不伪造行、不返回 501）。
  `limit` 缺省 20、范围 1..100；present-but-blank（`?limit=`）也按畸形处理 → 422 字段 `limit`；
  `cursor`/`application_id` 被接受但忽略。
- 请求日志规则未变：只记 method、无 query 的 path、status、duration、request_id；本票新增的
  secret、Authorization 头、body 均不进入日志。

### 1.4 页面

- `apps/web/src/admin.html` 在既有卡片之后新增：
  - 服务账号卡片：`id="service-account-form"`（`sa-display-name`、`sa-responsible`）→
    POST `/api/v1/admin/service-accounts`，显示返回的 `sa-principal-id`，并自动填入下面的 key 表单；
  - API key 卡片：`id="api-key-form"`（principal id、`application:read` 复选框、RFC3339 过期时间）→
    POST `/api/v1/admin/api-keys`；一次性明文写入 `readonly` 的 `id="api-key-secret"` textarea，
    并显示“明文只显示一次”提示与 key id；
  - 撤销表单：`id="api-key-revoke-form"`（`revoke-api-key-id`）→ DELETE `/api/v1/admin/api-keys/{id}`。
  - 三个写操作都 `csrf: await csrf()`、都有 ok/bad 可见状态、401 都跳 `/login`。
  - T015/T017/T019 的 id、标题与“只显示一次”字符串全部保留；页面不使用 URL/storage 保存 secret。
- `assets/app.js` 不需要新助手：现有 `api/identity/csrf/requireLogin/logout` 已足够，本票未改；
  没有框架、没有构建步骤。

### 1.5 测试

- Go 单测（不碰数据库）：
  - `internal/identity/apikeys_test.go`：scope 词汇表、空/未知/重复、personal/memory 拒绝（含 typed error）、
    过期时间矩阵（now/past/zero/one-tick）、secret 形状（前缀、43 字符 base64url、64 hex 哈希、唯一性）、
    `Identity.HasScope`、`apiKeyFailure` 的 10 条判定矩阵（含“invalid 优先于 disabled”）。
  - `internal/api/bearer_test.go`：14 条 bearer 头解析矩阵（有效/大小写/空白/畸形/重复头/缺省）、
    `requireScope` 5 条矩阵（会话不受限、key 有/无 scope）。
- 集成（`tests/f01/test_t020_*.py`，30 个）：真实临时库 + `cmd/admin migrate/bootstrap` + 真实
  `cmd/api` 进程 + 真实 HTTP；逐条对应见第 2 节。

## 2. 验收标准逐条对照

Ticket 验收（`T020.md`）：

| # | 验收 | 证据测试 |
|---|---|---|
| 1 | **scope 足够而 ACL 不足仍拒绝** | `test_t020_scope_and_acl.py::test_a_read_key_needs_both_scope_and_a_grant_and_a_revoke_is_immediate`（无 grant：列表空、详情 404；grant 后可见；revoke 后同一进程下一次立即不可见）、`test_a_read_key_without_an_acl_grant_gets_404`；反方向：`test_scope_is_checked_before_the_acl`（库中 craft `application:write` key 即使持有 read grant 也 403 `insufficient_scope`） |
| 2 | **禁止借负责人个人记忆** | `test_t020_personal_boundary.py::test_a_service_key_cannot_read_personal_memories`（普通 key、`personal:read`、`memory:read`、混合 scope 全部 403 `personal_scope_forbidden`）、`test_a_human_gets_an_honest_empty_memory_list`（人类 200 空列表；limit 校验；匿名 401）；`test_t020_scope_and_acl.py::test_a_service_account_never_inherits_its_responsible_persons_apps`（负责人能读自己的应用，服务账号不授权时列表空/详情 404） |
| 3 | **Key 撤销和负责人禁用立即生效** | `test_t020_revocation.py::test_a_revoked_key_is_refused_on_the_next_call`（204 → 下一次 401 `invalid_key`）、`test_an_expired_key_is_refused`（craft UPDATE → 401）、`test_disabling_the_responsible_user_immediately_denies_a_valid_key`（只禁用负责人、key 行未撤销 → 下一次 403 `responsible_disabled`）、`test_disabling_the_service_principal_rejects_the_key`（403 `principal_disabled`）；撤销幂等/未知：`test_t020_api_keys.py::test_revocation_is_idempotent_audited_and_typed`（二次 DELETE 204 且不新增审计；未知/畸形 404 `key_not_found`） |
| 4 | 通过上述端到端入口实际演示；失败、当前授权与重试/恢复边界有断言，不以单层 mock 成功代替 | 30 个集成测试全部真实 PG + 真实二进制 + 真实 HTTP；失败边界见 `test_t020_service_accounts.py`、`test_t020_api_keys.py` 的每类拒绝都断言“行数/审计不变”；认证边界：`test_t020_bearer_auth.py`（无 Cookie、无 Set-Cookie、无 session 行、畸形头不回退 Cookie、logout 401）；恢复边界：撤销/过期/负责人禁用后 key 不可能再通过，再创建的新 key 正常（`test_logout_refuses...` 尾部） |
| 5 | 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件标 blocked，未运行标 not_run | 本报告 §1、§3、§4、§7；迁移/代码 hash 见上面的 commit；环境 profile `dev-stub` 明示；未执行项见 §7 |

本票 prompt 的 A–E：

| # | 要求 | 证据 |
|---|---|---|
| A1 | 迁移 0008 的列/索引/非空 scope/哈希唯一 | `test_t020_migrations.py::test_the_api_keys_table_matches_the_contract`（列、PK、唯一、FK、CHECK、索引；空 scopes 与重复 hash 被 PostgreSQL 拒绝） |
| A2 | 新表对 worker/PUBLIC 无权限 | `test_t020_migrations.py::test_the_worker_and_public_get_nothing_from_the_key_table`（INSERT/UPDATE/DELETE 全部 `permission denied`、无已记录 grant、PUBLIC 无权限，并以 `app.workers` 作对照） |
| B1 | 服务账号创建/校验/审计/不可登录 | `test_t020_service_accounts.py` 全部 4 个 |
| B2 | key 一次性明文、只存 SHA-256、scope/过期/目标校验、审计 | `test_t020_api_keys.py::test_an_admin_issues_a_one_time_key_for_a_service_account`、`test_key_creation_validation_is_typed_and_creates_nothing`、`test_key_creation_requires_an_admin_session_and_csrf` |
| B3 | bearer 认证、scope、撤销、负责人禁用 | `test_t020_bearer_auth.py`、`test_t020_scope_and_acl.py`、`test_t020_revocation.py` |
| C1 | logout 只接受会话；memories 个人边界 | `test_t020_bearer_auth.py::test_logout_refuses_a_bearer_credential_and_the_key_survives`、`test_t020_personal_boundary.py` |
| C2 | admin 路由对服务 key 全部 403 | `test_t020_bearer_auth.py::test_admin_endpoints_refuse_a_service_key`（6 条路由） |
| D1 | admin 页面服务账号/key/撤销/一次性提示/CSRF | `test_t020_pages.py` 全部 3 个 |
| E1 | Go 单测：scope/生成/头解析/requireScope/负责人判定 | `internal/identity/apikeys_test.go`、`internal/api/bearer_test.go` 的 9 个 Test 函数（第 3 节原始输出） |
| E2 | 无 secret 进入 DB/审计/日志/URL | `test_t020_secrets.py::test_the_secret_never_reaches_the_database_logs_audit_or_a_url` |

## 3. 实际命令与原始结果（test 提交后、docs 提交前）

```text
$ cd E:\Orchara\product
$ go vet ./...
（无输出）
VET_EXIT=0

$ go test -count=1 ./...
?   	orchara/platform/cmd/admin	[no test files]
?   	orchara/platform/cmd/api	[no test files]
?   	orchara/platform/cmd/maintenance	[no test files]
?   	orchara/platform/cmd/supervisor	[no test files]
?   	orchara/platform/cmd/worker	[no test files]
?   	orchara/platform/db	[no test files]
ok  	orchara/platform/internal/api	1.809s
?   	orchara/platform/internal/audit	[no test files]
ok  	orchara/platform/internal/catalog	1.293s
ok  	orchara/platform/internal/config	0.916s
ok  	orchara/platform/internal/db	1.080s
ok  	orchara/platform/internal/health	1.816s
ok  	orchara/platform/internal/identity	2.378s
ok  	orchara/platform/internal/policy	1.624s
ok  	orchara/platform/internal/supervisor	0.503s
ok  	orchara/platform/internal/uuid	0.706s
TEST_EXIT=0
```

```text
$ cd E:\Orchara
$ python -m pytest product/tests/f01 -q
........................................................................ [ 58%]
....................................................                     [100%]
124 passed in 525.04s (0:08:45)
```

> T015/T017/T019 的 94 个全部保持绿色，本票新增 30 个（`test_t020_*` 9 个文件），共 124。

```text
$ cd E:\Orchara
$ python -m pytest product/tests -q
........................................................................ [ 14%]
........................................................................ [ 28%]
........................................................................ [ 43%]
........................................................................ [ 57%]
........................................................................ [ 72%]
........................................................................ [ 86%]
..................................................................       [100%]
498 passed in 714.32s (0:11:54)
```

> 门禁前基线：T019 后 468 passed。本票净增 30，全部真实执行、无 skip。

```text
$ cd E:\Orchara
$ python product/tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
SYNC_EXIT=0

$ python product/tools/ci_checks.py --quick --json
{
  "checks": [
    {
      "name": "spec-drift",
      "status": "passed",
      "detail": ""
    },
    {
      "name": "python-tests-fast",
      "status": "passed",
      "detail": ""
    }
  ],
  "passed": 2,
  "skipped": 0,
  "failed": [],
  "skipped_names": [],
  "duration_seconds": 21.1,
  "verdict": "ok",
  "ok": true
}
CI_EXIT=0
```

新增 Go 单测的原始输出（节选，子用例行已省略；共 9 个 Test 函数、41 个子用例）：

```text
$ cd E:\Orchara\product
$ go test -count=1 -v -run "TestNormalizeScopes|TestPersonalAndMemory|TestAPIKeyExpiry|TestNewAPIKeySecret|TestIdentityHasScope|TestAPIKeyFailureMatrix|TestParseBearerHeader|TestRequireScopeMatrix" ./internal/identity ./internal/api
--- PASS: TestPersonalAndMemoryScopesAreRefusedAsPersonalData (0.00s)
--- PASS: TestAPIKeyExpiryMustBeStrictlyInTheFuture (0.00s)
--- PASS: TestNewAPIKeySecretHasTheContractShape (0.00s)
--- PASS: TestIdentityHasScopeOnlyForItsOwnScopes (0.00s)
--- PASS: TestAPIKeyFailureMatrix (0.00s)
--- PASS: TestParseBearerHeaderAcceptsExactlyOneWellFormedHeader (0.00s)
--- PASS: TestRequireScopeMatrix (0.00s)
ok  	orchara/platform/internal/identity	0.834s
ok  	orchara/platform/internal/api	1.091s
EXIT=0
```

## 4. 实测结果（real vs stub）

- **real**：PostgreSQL 16.4（真实行、真实唯一/CHECK/FK 约束、真实 `now()` 过期判定、真实事务）、
  真实编译的 `cmd/admin` 与 `cmd/api`、真实 TCP HTTP；撤销/负责人禁用用 crafted `UPDATE` 构造
  “key 未撤销但已不可用”的真实状态，再走真实认证路径；secret 检查读取真实数据库行与真实子进程日志文件。
- **stub**：环境 profile 为 `dev-stub`，只表示开发环境（无真实模型调用、无 Linux 沙箱）；
  本票不涉及模型，没有 stub 业务结论。服务账号、key、scope、ACL、审计全部真实实现与真实运行。
- **not_run**：见 §7。

## 5. 设计要点

- **两道门都必须过**：key 的 scope 只回答“这类动作被允许”，具体资源仍由 `policy.Authorizer` /
  `catalog` 的 ACL 决定；scope 在 catalog 之前检查，所以“有 read grant 的 write key”仍是 403，
  不会因为 ACL 而扩大 scope。
- **认证即读库**：`AuthenticateAPIKey` 每次请求按哈希查 key 行，撤销/过期/禁用没有缓存窗口。
  与 T019 的 ACL 缓存不同，key 生命周期没有“下一次 epoch”延迟，因此本票**不需要** `policy.Bump`：
  key 创建/撤销不改变任何 grant/角色缓存键，认证本身也不会被缓存。T021 的禁用会改变授权状态，
  仍必须在其事务里调用 `policy.Bump`（见 §8）。
- **判定顺序是安全语义**：unknown/revoked/expired 同为一个 401，避免 key 枚举；principal 禁用先于
  负责人禁用；负责人禁用产生独立 403 `responsible_disabled`，让“负责人离职/禁用立即生效”可观察、
  可断言，同时 T021 仍应把 key 行真正撤销（见 §8）。
- **secret 只出现一次**：生成在内存→响应体；数据库只有 SHA-256；审计只记 id/scopes/expires_at；
  日志只记 method/path/status/duration/request_id。测试同时检查 DB、audit、log 与 URL。
- **strict bearer**：`Authorization` 存在即权威，畸形不回退 Cookie，避免“带错头反而以更高权限
  的 Cookie 执行”。重复 Authorization 头也拒绝（歧义头不可信）。
- **`/memories` 的诚实边界**：F09 之前返回 200 空列表而不是伪造/501；服务凭据在解析 limit 与
  接触存储之前 403。

## 6. 与本票要求 / DESIGN.md 的偏差（及原因）

1. **个人 scope 的拒绝先于未知 scope 检查**：`personal:*`/`memory:*` 得到专属
   `personal_scope_forbidden`，即使它同时不在已知注册表中。原因：这是本票的**核心安全边界**，
   不应被通用 422 掩盖；DESIGN §9 T020 也明确 personal scope 用 422 `personal_scope_forbidden`。
2. **目标 service principal 已禁用时创建 key 返回 422**（prompt 只要求负责人不可用 422）：
   给已禁用账号发新凭据没有意义，且会让“禁用”看起来可被绕过；拒绝是更严格的 fail closed，
   列入此处以免隐藏行为。
3. **畸形 body UUID 是 422，缺失/合法但不存在是 404**：沿用 T019 的约定（body/query 形状错误可指认
   字段 → 422；合法 id 不存在 → 404）；路径中的畸形 `key_id` 与“不存在”同为 404，避免枚举。
4. **畸形 `Authorization` 头返回 401 `invalid_key` 而不是 `unauthenticated`**：它是在尝试 bearer
   凭据；与未知 key 同一错误码，不泄露“格式 vs 存在”的差异。
5. **`?limit=`（present-but-blank）按畸形 422 处理**：`url.Values.Get` 无法区分“缺省”与“空值”，
   本票用 `Query().Has("limit")` 区分；`cursor`/`application_id` 按 prompt 忽略。
6. **key 创建/撤销不 bump `policy_epochs`**：见 §5 的缓存论证；本票没有任何缓存的 key 决策。
   若后续给认证加决策缓存，撤销必须改为同事务 bump。
7. **页面只做了服务端内容/行为断言**：没有浏览器或 JS 引擎执行（与 T015/T017/T019 相同限制）。

## 7. 限制 / not_run

- **没有浏览器级验证**：`/admin` 的断言是服务端 HTML/JS 内容与行为断言（表单 id、契约路径、
  CSRF 使用、一次性明文 textarea、撤销表单、401 跳转、无 storage/URL 泄露）；不执行 JavaScript、
  不截图、不做 HCI 验收。
- **F01 只暴露一个 scope**：`application:read`。`application:write` 等写 scope 要到 F02A 应用写入
  路径才可加入注册表；当前创建 key 会拒绝未知 scope。
- **`/memories` 的存储未实现**：F09 拥有个人记忆；本票只交付“服务账号不可读”的强制边界和
  人类会话的诚实空结果，`cursor`/`application_id` 被接受但忽略。
- **key 只能属于 service principal**：人类仍用会话 Cookie；没有“人类 API key”路径（契约也未要求）。
- **没有 `listApiKeys`/`getApiKey` 契约**：撤销依赖创建响应返回的 key id；页面会显示它，
  离开页面后无法从 API 重新列出 key。这是契约缺口，不是本票遗漏。
- **没有 key 轮换/续期**：只能新建再撤销旧 key；过期时间不可修改。
- **bearer 认证没有限流**：T017 的 5 次/60 秒限流只属于 `/auth/login`；key 校验是 SHA-256 查询，
  没有算力放大，但暴力尝试的速率限制/配额仍属 F15B。
- **key 认证查询未做压测**：每次请求一条 `key_hash` 唯一索引查询 + 两个 principal join；
  大表下的连接池/延迟未测。
- **审计保留/轮转**：`audit_events` 只追加，没有保留期或归档任务（F01 范围外）。
- **无真实邮件/外部通知**：服务账号负责人交接（T021 `handover`）与通知不在本票。
- **没有推送、没有关闭 issue**（由主代理完成）。

## 8. T021 需要知道 / 不能破坏

- **`identity.Service` 方法签名（T021 直接复用）**：
  - `CreateServiceAccount(ctx context.Context, actorID, displayName, responsibleUserID string) (string, error)`
  - `CreateAPIKey(ctx context.Context, actorID, principalID string, scopes []string, expiresAt time.Time) (*APIKeySecret, error)`
  - `RevokeAPIKey(ctx context.Context, actorID, keyID string) (string, error)`
  - `AuthenticateAPIKey(ctx context.Context, presented string) (*Identity, error)`
  - `Identity` 的内部字段：`Scopes []string`、`KeyID string`（不要序列化）。
  - `ErrPersonalScope`、`ErrInvalidKey`、`ErrAPIKeyNotFound`、`ErrResponsibleDisabled` 是 API 映射依赖的
    sentinel，不要改语义；`apiKeyFailure` 的顺序（invalid → principal_disabled → responsible_disabled）
    已被 Go 单测与集成测试固定。
- **T021 的禁用必须在同一事务里撤销 key**：T020 只保证“负责人禁用后下一次认证 403”；SECURITY §1
  还要求 key 实际撤销。建议（在 `identity` 的禁用事务中，使用与现有 `policy.Bump` 相同的事务）：
  ```sql
  UPDATE platform.api_keys SET revoked_at = now()
   WHERE revoked_at IS NULL
     AND (principal_id = $1
          OR principal_id IN (SELECT id FROM platform.principals WHERE responsible_user_id = $1))
  RETURNING id;
  ```
  即：禁用某人 → 撤销其本人的 key + 其负责的所有服务账号的 key；随后 `policy.Bump(ctx, tx)`
  （T021 也改变授权状态）并审计。T020 的 `test_disabling_the_responsible_user_immediately_denies_a_valid_key`
  使用 crafted `UPDATE` 只禁用负责人、**不撤销 key**，因此仍会、也应继续命中
  “key 有效但负责人禁用 → 403 `responsible_disabled`”这一分支；T021 通过自己的 disable API
  撤销 key 后，新增的禁用流程测试应断言下一次调用 401 `invalid_key`（因为 key 已被撤销）。
  两种测试覆盖的是不同状态，不要让 T021 为了实现前者而绕过后者的撤销。
- **`policy.Bump` 与 `ROLESET1`**：T020 没有改 `identity.SetRoles` 的 `rolesLockKey`
  （`"ROLESET1"`）与 `policy.Bump`。T021 的“最后一个启用人类管理员不能被禁用”必须与角色降级
  串行化（复用同一把 `pg_advisory_xact_lock(rolesLockKey)`，或证明两者不可能互相破坏），
  并在禁用/启用事务里调用 `policy.Bump`。
- **不要修改 `0008_api_keys.sql`**（checksum 不可变；已应用的库会响亮失败）。T021 若需要新表/列
  请用新迁移（0009）。
- **不要破坏 admin 页面 id**：T020 新增的 `id="service-accounts-panel"`、`id="service-account-form"`、
  `id="sa-display-name"`、`id="sa-responsible"`、`id="sa-status"`、`id="sa-result"`、
  `id="sa-principal-id"`、`id="api-keys-panel"`、`id="api-key-form"`、`id="api-key-principal"`、
  `id="api-key-scope-application-read"`、`id="api-key-expires"`、`id="api-key-status"`、
  `id="api-key-result"`、`id="api-key-secret"`、`id="api-key-id"`、`id="api-key-revoke-form"`、
  `id="revoke-api-key-id"`、`id="api-key-revoke-status"`；以及 T015/T017/T019 的既有 id、
  标题 `Orchara 管理台（F01 身份）` 与“只显示一次”。
- **不要改 `internal/api/authz.go` 的插入点**：bearer 只在 `authenticate`；任何新桥接不得绕过
  `authorize`（CSRF/角色顺序）或 `requireScope`（scope 先于 ACL）。会话不受 scope 限制的行为已被
  `test_a_human_session_is_not_scope_limited` 固定。
- **`POST /api/v1/auth/logout` 对 bearer 的 401** 已被测试固定；T021 不要把它改成“撤销 key 并 204”。
- **测试基线**：`product/tests/f01` 现在 124 passed（T015 37 + T017 26 + T019 31 + T020 30）。
  `t020_stack.py` 提供 `create_service_account(_or_fail)`、`create_api_key(_or_fail)`、`revoke_api_key`、
  `bearer_client`、`api_keys`、`seed_api_key_row`、`expire_api_key`、`future_rfc3339`/`past_rfc3339`；
  T021 可直接复用，不要改 `t017_stack.py`/`t019_stack.py` 的既有助手语义。
- **审计助手**仍是 `audit.Record(ctx, tx, actorID *string, action, resourceType string, resourceID *string, metadata map[string]any)`；
  T021 的 disable/enable 审计 metadata 可包含 reason，但绝不能包含密码/secret/key hash。

## 9. 复现命令

```powershell
cd E:\Orchara\product
$env:GOCACHE='E:\orchara-toolchains\gocache'; $env:GOMODCACHE='E:\orchara-toolchains\gomodcache'
& E:\orchara-toolchains\go\bin\go.exe vet ./...
& E:\orchara-toolchains\go\bin\go.exe test -count=1 ./...

cd E:\Orchara
$env:PYTHONIOENCODING='utf-8'
python -m pytest product/tests/f01 -q
python -m pytest product/tests -q
python product/tools/sync_spec.py --check
python product/tools/ci_checks.py --quick --json
```
