# F01 T019 验收：管理员授予或撤销某个用户的资源访问

- 任务：F01 / GitHub Issue #20（`T019`），分支 `feat/f01-identity`
- 状态：**passed**（真实 PostgreSQL 16.4 + 真实编译 Go 二进制 + 真实 HTTP；无 mock、无跳过）
- 代码提交：
  - `4493a68`（`feat(f01): resource grants, policy epoch cache and the applications read model`）
  - `aba8820`（`test(f01): cover the T019 visibility, grant, pagination, role and page boundaries`）
  - `665f71c`（`test(f01): prove the authorization tables deny the worker and PUBLIC`）
  - 本报告与 `PROGRESS.md` 在其后的 `docs(f01): ...` 提交中
- 主要文件：
  - `db/migrations/0007_authorization.sql`（新增：`policy_epochs`、`resource_grants`、`applications` + 权限拒绝）
  - `internal/policy/{policy,grants}.go`（新增：默认拒绝的 `Authorizer`、epoch 缓存、Grant/Revoke/Bump、校验）
  - `internal/catalog/catalog.go`（新增：applications 读模型，ACL 在分页前的同一个 SQL WHERE 里）
  - `internal/identity/roles.go`（新增：`SetRoles`、last-admin 规则、`roles.updated` 审计、epoch bump）
  - `internal/audit/audit.go`（新增：从 `identity.RecordAudit` 搬出的审计助手，供 identity/policy 共用）
  - `internal/uuid/uuid.go`（新增：HTTP 契约 UUID 形状校验，不引入依赖）
  - `internal/api/{grants,roles,applications}.go`、`internal/api/server.go`（5 条端点、429/422/404/409 映射、`/apps` 路由）
  - `apps/web/src/apps.html`（新增）、`apps/web/src/admin.html`（授权/撤销/角色表单）
  - `internal/{policy,catalog,identity,api,uuid}/*_test.go`、`tests/f01/t019_stack.py`、`tests/f01/test_t019_*.py`（31 个集成测试）
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`E:\orchara-pg`）
  - Python 3.12.2；**未新增任何依赖**（`go.mod` 未改）
  - 环境 profile：`dev-stub`（同 T015/T017；见第 4 节 real/stub 分报）
  - 基线：T017 后 `product/tests` = 437 passed、`tests/f01` = 63 passed；
    本票后 `product/tests` = 468 passed、`tests/f01` = 94 passed（新增 31，无回归、无跳过）

## 1. 交付内容

### 1.1 迁移 `0007_authorization.sql`

三张表，全部 `platform.` 限定，`checkpoint` schema 不出现、无 vector DDL：

- `platform.policy_epochs(scope text PRIMARY KEY, revision bigint NOT NULL CHECK (revision >= 0))`，
  幂等 `INSERT ... ON CONFLICT DO NOTHING` 播种 `('acl', 0)`。缺行时授权检查 **fail closed**
  （`ErrEpochMissing`），不猜默认值。
- `platform.resource_grants(id uuid PK, principal_id uuid NOT NULL REFERENCES platform.principals,
  resource_type text NOT NULL, resource_id uuid NOT NULL, action text NOT NULL, expires_at timestamptz,
  CONSTRAINT resource_grants_tuple UNIQUE(principal_id, resource_type, resource_id, action))`
  + 索引 `grants_resource (resource_type, resource_id, action)`。
  唯一约束**显式命名**，这样重复授权可以在不解析驱动消息的情况下报 `409 grant_exists`。
- `platform.applications(id uuid PK, owner_id uuid NOT NULL REFERENCES platform.principals,
  name text NOT NULL, active_version_id uuid, archived_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now())` + 索引 `applications_owner (owner_id)`。
  表头与 `COMMENT ON COLUMN` 都写明：`active_version_id` 的外键在 F02A 的
  `application_versions` 落地时再补，F01 只拥有这个读模型。
- 权限边界与 0006 一致：`REVOKE ALL ON SCHEMA platform FROM PUBLIC`；若 `orchara_worker`
  存在，再次 `REVOKE ALL ON ALL TABLES/ALL SEQUENCES IN SCHEMA platform` 并撤销 schema
  USAGE，因此新表同样不可被运行角色读写（`test_t019` 未弱化 `test_t015_privileges.py`
  的既有断言；workers 权限测试仍全绿）。

### 1.2 `policy.Authorizer`：默认拒绝 + 每次 epoch + 可过期缓存

```go
func (a *Authorizer) Allow(ctx, subject PrincipalRef, resource ResourceRef, action string) (bool, error)
```

顺序是安全相关的：

1. **所有权优先**：`resource.OwnerID != "" && subject.ID == OwnerID` 直接允许，不读策略表，
   也不依赖数据库可用性；空 owner 表示“没有所有权路径”，继续走授权。
2. 空主体/空资源/空动作直接拒绝（不花费一次策略读）。
3. **每次调用**读 `policy_epochs.acl`（`EpochReads` 计数可观测）；读错误或缺行 → `(false, error)`，
   **fail closed**。
4. 进程内决策缓存键 `(principal_id, resource_type, resource_id, action, epoch)`；
   - 命中 deny → 直接拒绝；
   - 命中 allow → 用注入时钟复核该 grant 的 `expires_at`；已过期则**丢弃命中并重新查库**，
     过期 allow 永远不会被继续提供；
   - 未命中 → 查 `resource_grants`，条件 `(expires_at IS NULL OR expires_at > $clock)`，
     结果连同 `expires_at` 缓存到该 epoch。
5. 缓存有上限（默认 10000）：超限先删除旧 epoch 条目，仍超限则整体清空——缓存只是优化，
   清空只影响延迟，不影响任何判定。

`Stats()` 暴露 `EpochReads/GrantQueries/CacheHits/CacheMisses/Entries`，Go 单测用它证明
“缓存确实被使用”，而不是只证明结果正确。注入时钟是 `Authorizer.Now`（`Clock()` 供 handler
做同样的“必须未来”校验）。

### 1.3 授权/撤销：写、epoch、审计同一事务

- `Grant(ctx, actorID, params) (*GrantResult{ID, Revision}, error)`：
  校验（纯函数 `ValidateGrant`：UUID 形状、`resource_type=application`、`action=read`、
  `expires_at` 必须未来）→ 同一事务内检查被授权 principal 存在/未禁用、application 存在且未归档
  → `INSERT ... RETURNING id`（唯一冲突 → `ErrGrantExists`）→ 审计 `grant.created`
  （`principal_id/resource_type/resource_id/action[/expires_at]`，无秘密）→ `Bump` → 提交。
- `Revoke(ctx, actorID, grantID) (revision, error)`：`DELETE ... RETURNING` 元组（无行 →
  `ErrGrantNotFound`）→ `audit.Record("grant.revoked")` → `Bump` → 提交。
- `func Bump(ctx, tx *sql.Tx) (int64, error)`：
  `UPDATE platform.policy_epochs SET revision = revision + 1 WHERE scope='acl' RETURNING revision`。
  角色修改、T021 的禁用都必须在自己的事务里调用它，缓存才不可能跨过一次撤销。

`policy_epochs` 的 revision 因此是“授权状态写次数”的单调计数：本票测试断言
grant + revoke 恰好 +2、角色 PUT 恰好 +1。

### 1.4 `catalog`：先 ACL 过滤，再游标/分页

`ListVisible(ctx, principalID, cursor, limit)` 的可见性谓词和游标/LIMIT 在**同一条 SQL 的
WHERE** 里：

```sql
WHERE a.archived_at IS NULL
  AND (a.owner_id = $1 OR EXISTS (
        SELECT 1 FROM platform.resource_grants g
         WHERE g.principal_id = $1 AND g.resource_type = 'application'
           AND g.resource_id = a.id AND g.action = 'read'
           AND (g.expires_at IS NULL OR g.expires_at > now())))
  AND ($2::timestamptz IS NULL OR (a.created_at, a.id) < ($2::timestamptz, $3::uuid))
ORDER BY a.created_at DESC, a.id DESC
LIMIT $4
```

- 稳定 keyset：`(created_at DESC, id DESC)`；取 `limit+1` 判断是否还有下一页，`next_cursor`
  取**最后一条返回行**的键，保证下一页严格从它之后开始。
- 游标：`base64url(RFC3339Nano(created_at) + "|" + id)`，raw base64url（无 `+/=`）；
  解析失败（坏 base64、段数不对、坏时间、坏 UUID）→ 类型化 `ErrInvalidCursor` → 400。
- limit：缺省 20，1..100；非数字/越界/空白 → `ErrInvalidLimit` → 422（`details.field=limit`）。
  不做静默截断。
- `GetVisible` 取行（排除 archived）后用 `policy.Authorizer.Allow` 判定 owner/read grant；
  不存在、已归档、无权三种情况都返回同一个 `ErrNotVisible`，HTTP 一律 404。
- F02A 拥有 `createApplication`，所以测试直接用 psql 播种 `platform.applications` 真行；
  这是本票记录在案的既有边界（见 §6.1、§7）。

### 1.5 角色更新与 last-admin

`identity.SetRoles(ctx, actorID, principalID, roles)`：

1. `normalizeRoles` 复用 T015 的规则：非空、唯一、`admin|creator|user`，否则 422（字段 `roles`）。
2. 事务内先 `pg_advisory_xact_lock(<ROLESET1>)` 串行化角色变更；读目标 principal：
   不存在 → `ErrPrincipalNotFound`(404)，`kind=service` → 422（字段 `principal_id`）。
3. 只有“当前是 admin 且新集合不含 admin”才统计**其它**启用中的人类管理员；为 0 → 409 `last_admin`。
   两个并发降级因此不可能都成功（第二个在同一把锁之后看到剩余 0）。
4. `DELETE` 旧绑定 + 插入新绑定 → `roles.updated` 审计（`old_roles`/`new_roles`）→ `policy.Bump` → 提交。
5. 返回 `{id, state:"updated"}`；epoch 在成功响应前已经提交，下一个请求立即生效。

### 1.6 HTTP 端点与错误码

| 端点 | 授权 | 成功 | 失败 |
|---|---|---|---|
| `POST /api/v1/admin/grants` | `authorize(w,r,"admin")`（会话→CSRF→角色） | 201 `{id,state:"granted"}` | 422 `validation_failed`（details.field）、404 `principal_not_found` / `resource_not_found`、409 `grant_exists` |
| `DELETE /api/v1/admin/grants/{grant_id}` | 同上 | 204（无 body，no-store） | 404 `grant_not_found` |
| `PUT /api/v1/admin/users/{principal_id}/roles` | 同上 | 200 `{id,state:"updated"}` | 422 `validation_failed`、404 `principal_not_found`、409 `last_admin` |
| `GET /api/v1/applications?cursor&limit` | `authorize(w,r,"")` | 200 `{items:[{id,name,active_version_id}],next_cursor}` | 400 `invalid_cursor`、422 `validation_failed`（field=limit） |
| `GET /api/v1/applications/{application_id}` | 同上 | 200 `Application` | 404 `resource_not_found`（缺失与无权同一响应） |

- 所有写端点都**先** `authorize` 再 `decodeJSON`：伪造请求连 body 都不会被解析；CSRF 由
  T017 的统一助手强制，本票没有新增任何绕过路径。
- 没有任何 admin/role 旁路：`GET /applications` 只看 owner/grant，管理员没有 grant 时列表为空、
  详情 404（有行为测试）。
- 2026-11 的契约只有 `grantResource`/`revokeResourceGrant`/`setUserRoles`/`listApplications`/
  `getApplication`；不存在 `listGrants`，所以撤销依赖创建响应返回的 grant id（见 §7）。
- 畸形 UUID 的处理：请求体/查询参数中的 UUID 形状错误 → 422（字段可指认）；路径里的
  `grant_id`/`application_id`/`principal_id` 形状错误 → 对应 404（一个畸形 id 不可能命中行，
  404 也避免枚举）。

### 1.7 页面

- `apps/web/src/apps.html`（`GET /apps`）：`GET /api/v1/applications` 渲染名称/id/active version，
  空态文案明确“没有可见应用”，`next_cursor` 驱动“下一页”，`requireLogin()` + 401 跳 `/login`；
  用 `textContent` 构造节点，token/CSRF 不进入 URL/query/storage。
- `apps/web/src/admin.html`：新增授权表单（principal id、资源 id、`read` 下拉、可选 RFC3339 过期，
  POST `/api/v1/admin/grants`，显示返回的 grant id）、撤销表单（grant id → DELETE
  `/api/v1/admin/grants/{id}`）、角色表单（principal id + 角色勾选 → PUT
  `/api/v1/admin/users/{id}/roles`）；每个写操作都 `csrf: await csrf()`，都有 ok/bad 可见反馈；
  所有 T015/T017 的 id、标题与“只显示一次”提示保持原样。表单只对 admin 显示，但**授权仍由
  服务端**判定。
- `assets/app.js` 未改：现有 `api/identity/csrf/requireLogin/logout/escapeHTML` 已足够，本票
  不新增框架/构建步骤。

### 1.8 审计包

`identity.RecordAudit` 搬移为 `internal/audit.Record`（DESIGN §5 的命名）。原因：本票的
`identity.SetRoles` 需要 `policy.Bump`，而 `policy.Grant/Revoke` 需要写审计；若 policy 反过来
import identity 就成环。审计包只依赖 `database/sql`，事件 id 用 PostgreSQL 内建
`gen_random_uuid()`。`identity` 内 5 处调用点已改为 `audit.Record`，行为不变。

## 2. 验收标准逐条对照

| # | 验收（Ticket / DESIGN §4） | 证据测试 |
|---|---|---|
| 1 | **A 不能枚举 B 正文** | `test_t019_visibility.py::test_a_cannot_enumerate_b_applications`（A 的列表只有自己的行；A GET B 的 id → 404 且响应不含 B 的 name/id；B 对称）、`test_missing_and_unauthorized_are_indistinguishable`（不存在与无权用同一个 code/message，逐字节对比去掉 request_id 后相同）、`test_an_archived_application_is_invisible_even_to_its_owner` |
| 2 | **撤销后下一调用拒绝** | `test_t019_grants.py::test_a_grant_appears_on_the_next_read_and_a_revoke_denies_the_next_one`（201→列表含/详情 200→204→同一进程内列表排除/详情 404；epoch 恰好 +2；`grant.created`/`grant.revoked` 审计存在且元数据正确）、`test_the_second_read_is_answered_from_the_in_process_decision_cache`（在**不 bump epoch** 的情况下把库中 grant 改成已过期：第一个进程仍 200 → 证明缓存命中；同一数据库上另起一个 API 进程 → 404，隔离出“是缓存答的”） |
| 3 | **管理员/作者不自动读取使用者内容** | `test_t019_visibility.py::test_an_administrator_is_not_an_automatic_content_reader`（bootstrap admin 的列表为空，GET 他人应用 404）、`test_an_author_cannot_read_another_creators_application`（两位 creator 互相 404/列表排除） |
| 4 | 端到端入口实际演示；失败、当前授权与重试/恢复边界有断言，不以单层 mock 成功代替 | 31 个集成测试全部真实 PG + 真实二进制 + 真实 HTTP；失败/边界：`test_t019_validation.py` 的 11 类拒绝 + 每种拒绝前后 `state()` 快照相等（grants/epoch/audit/role_bindings）、重复授权 409 不 bump、未知 grant 撤销 404、坏 cursor 400、坏/越界 limit 422、非管理员 403、缺 CSRF 403 且无行变化；`test_t019_pagination.py` 先 ACL 后分页（interleaved + `limit=1` 无空页）；`test_t019_roles.py::test_two_concurrent_demotions_leave_an_administrator`（真实 psql 持有 advisory lock，两个真 HTTP 并发降级 → 恰好一个 200、一个 409 `last_admin`，最终仍有 1 个启用中的人类管理员）；`test_t019_grants.py::test_an_expiring_grant_is_not_served_from_cache_after_its_expiry`（grant 过期后同一 epoch 内下一次读取 404） |
| 5 | 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件 blocked、未跑 not_run | 本报告 §1、§3、§4、§7；迁移文件与代码 hash 见上面的 commit；环境 profile `dev-stub` 明示 |
| A1 | 迁移 0007：三表 + epoch 播种 + worker/PUBLIC 权限拒绝 | `test_t019_privileges.py::test_the_worker_cannot_write_the_authorization_tables`（3 表 × INSERT/UPDATE/DELETE 全部 permission denied，拒绝后无行）、`test_the_worker_holds_no_recorded_privilege_on_the_new_tables`、`test_public_has_no_privilege_on_the_platform_tables`（`aclexplode`/`acldefault` 证明 grantee=0 无权限）、`test_the_worker_keeps_its_own_bookkeeping_access`（对照） |
| D1 | `POST /admin/grants` 契约与校验 | `test_t019_grants.py`（201 `{id,state}`）、`test_t019_validation.py::test_every_bad_grant_is_refused_with_its_field_and_changes_nothing`（未知/禁用 grantee、未知/归档资源、坏 action/type、过期/坏 expires_at 的精确 code 与 `details.field`） |
| D2 | `DELETE /admin/grants/{id}` | `test_t019_grants.py::test_a_grant_appears...`（204、无 body、no-store）、`test_t019_validation.py::test_an_unknown_or_malformed_grant_cannot_be_revoked` |
| D3 | `PUT /admin/users/{id}/roles` | `test_t019_roles.py` 全文件：promote 后同一会话可调 admin 端点、demote 后 403；last_admin 409；service principal 422；审计 old/new；epoch +1 |
| D4 | `GET /applications` 列表 | `test_t019_pagination.py`（默认 20、limit=100、interleaved 过滤、游标往返）、`test_t019_validation.py::test_cursor_and_limit_are_validated`、`test_t019_pages.py` |
| D5 | `GET /applications/{id}` | `test_t019_visibility.py`（200/404 同响应） |
| D6 | Go 单测：cursor/limit/authorizer/role | `internal/catalog`（5 个）、`internal/policy`（13 个）、`internal/identity/roles_test.go`（3 个）、`internal/api/grants_test.go`（4 个）、`internal/uuid`（2 个），见 §3 |

## 3. 实际命令与原始结果（test 提交 `665f71c` 上，docs 提交前）

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
ok  	orchara/platform/internal/api	2.093s
?   	orchara/platform/internal/audit	[no test files]
ok  	orchara/platform/internal/catalog	1.270s
ok  	orchara/platform/internal/config	0.494s
ok  	orchara/platform/internal/db	1.047s
ok  	orchara/platform/internal/health	1.843s
ok  	orchara/platform/internal/identity	2.265s
ok  	orchara/platform/internal/policy	1.708s
ok  	orchara/platform/internal/supervisor	0.846s
ok  	orchara/platform/internal/uuid	0.663s
TEST_EXIT=0
```

```text
$ cd E:\Orchara
$ python -m pytest product/tests/f01 -q
........................................................................ [ 76%]
......................                                                   [100%]
94 passed in 386.57s (0:06:26)
```

> T015/T017 的 63 个全部保持绿色，本票新增 31 个（`test_t019_*` 7 个文件），共 94。

```text
$ cd E:\Orchara
$ python -m pytest product/tests -q
........................................................................ [ 15%]
........................................................................ [ 30%]
........................................................................ [ 46%]
........................................................................ [ 61%]
........................................................................ [ 76%]
........................................................................ [ 92%]
....................................                                     [100%]
468 passed in 624.69s (0:10:24)
```

> 门禁前基线：T017 后 437 passed。本票净增 31，全部真实执行、无 skip。

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
  "duration_seconds": 17.6,
  "verdict": "ok",
  "ok": true
}
CI_EXIT=0
```

新增 Go 单测的原始输出（节选，子用例行已省略；另有 21 个子用例：校验矩阵 9 + 6，last-admin 6）：

```text
$ cd E:\Orchara\product
$ go test -count=1 -v ./internal/policy ./internal/catalog ./internal/uuid ./internal/api ./internal/identity
--- PASS: TestOwnerIsAlwaysAllowedWithoutAPolicyLookup (0.00s)
--- PASS: TestAnEmptyOwnerIsNotAnOwnershipPath (0.00s)
--- PASS: TestACachedAllowIsServedWithinOneEpoch (0.00s)
--- PASS: TestAnEpochBumpInvalidatesTheCachedAllowImmediately (0.00s)
--- PASS: TestAnExpiredCachedAllowIsNotServed (0.00s)
--- PASS: TestACachedDenyIsReportedWithoutAQuery (0.00s)
--- PASS: TestAMissingPolicyEpochFailsClosed (0.00s)
--- PASS: TestAStoreErrorFailsClosed (0.00s)
--- PASS: TestAnIncompleteReferenceIsDeniedWithoutALookup (0.00s)
--- PASS: TestTheCacheDoesNotGrowWithoutBoundAcrossEpochs (0.00s)
--- PASS: TestValidateGrantAcceptsTheContractTuple (0.00s)
--- PASS: TestValidateGrantReportsTheOffendingField (0.00s)
--- PASS: TestTheGrantVocabularyIsExtendableButClosed (0.00s)
ok  	orchara/platform/internal/policy	1.041s
--- PASS: TestParseLimitDefaultsToTwenty (0.00s)
--- PASS: TestParseLimitAcceptsTheContractRangeOnly (0.00s)
--- PASS: TestEncodeCursorIsOpaqueAndRoundTrips (0.00s)
--- PASS: TestDecodeCursorRejectsMalformedValues (0.00s)
--- PASS: TestDecodeCursorRequiresTheCanonicalUUIDForm (0.00s)
ok  	orchara/platform/internal/catalog	0.717s
--- PASS: TestValidAcceptsTheCanonicalForm (0.00s)
--- PASS: TestValidRejectsEverythingElse (0.00s)
ok  	orchara/platform/internal/uuid	0.431s
--- PASS: TestGrantParamsAcceptsTheContractBody (0.00s)
--- PASS: TestGrantParamsReportsTheOffendingField (0.00s)
--- PASS: TestApplicationViewIsExactlyTheContractSchema (0.00s)
--- PASS: TestApplicationListUsesAnArrayAndANullableCursor (0.00s)
ok  	orchara/platform/internal/api	1.637s
--- PASS: TestNormalizeRolesEnforcesTheBindableVocabulary (0.00s)
--- PASS: TestNormalizeRolesReportsTheOffendingField (0.00s)
--- PASS: TestRemovingLastAdminIsRefusedOnlyWhenNobodyElseRemains (0.00s)
ok  	orchara/platform/internal/identity	0.765s
```

## 4. 实测结果（real vs stub）

- **real**：PostgreSQL 16.4（真实行、真实唯一约束冲突、真实 `now()` 与真实事务/回滚）、
  真实编译的 `cmd/admin` 与 `cmd/api`、真实 TCP HTTP；并发测试用真实 psql 会话持有
  `pg_advisory_lock`、两个真实 HTTP 客户端并发写。
  - 缓存证明不是断言内部字段：第二个 API 进程（同一个数据库、无缓存）对同一请求返回 404，
    而第一个进程在库中 grant 已过期、epoch 未变时仍返回 200——只有缓存能解释这个差异。
  - 迁移从空库执行并记录 7 个文件；`platform.policy_epochs` 播种 `('acl',0)`；
    `test_t019_privileges.py` 用真实 `orchara_worker` 连接证明三张新表上的
    INSERT/UPDATE/DELETE 全部 `permission denied`、无任何已记录表权限、PUBLIC 无权限，
    并以“同一连接仍能写 `app.workers`”作对照。
- **stub**：环境 profile 为 `dev-stub`，只表示开发环境（无真实模型调用、无 Linux 沙箱）；
  本票不涉及模型，没有 stub 业务结论。授权、事务、审计、缓存、页面全部真实实现与真实运行。
- **not_run**：见 §7。

## 5. 设计要点

- **默认拒绝**没有“管理员后门”：`Allow` 只有 owner 与 grant 两条路径；管理端点用角色，
  资源读取不用角色。这正是 SECURITY §1 的“管理 ACL 不等于正文任意读”。
- **epoch 是缓存失效的唯一机制**：每次 `Allow` 先读 `acl.revision`；grant/revoke/角色修改在
  写事务里 `revision+1`。因此缓存键天然包含 epoch，撤销提交后的下一个请求不可能命中旧键。
  读 epoch 失败/缺行 → 拒绝（fail closed），不会回退到“上次看到的 epoch”。
- **过期 grant 的缓存语义**：缓存中的 allow 携带该 grant 的 `expires_at`；每次命中都用注入时钟
  复核，过期即视为 miss 并重新查库。查库也用同一个注入时刻作为 `$clock` 参数，保证“缓存判定”
  与“数据库判定”在同一时钟下一致（单测用假时钟，不 sleep）。
- **先 ACL 后分页**：可见性谓词与游标/LIMIT 在同一条 SQL；测试把“最新的行不可见”作为第一个
  页面，若实现先用 OFFSET/LIMIT 再过滤会直接返回空页，当前实现不会。
- **last-admin 用 advisory lock 而不是先 COUNT 再写**：并发降级被串行化，第二个事务在锁后
  重新计数；测试让两个请求先完成认证再一起进入事务，从而真正命中“剩余 0”的分支。
- **写路径顺序固定**：认证 → CSRF → 角色 → 读 body → 校验 → 事务；拒绝发生在任何写之前，
  测试用“拒绝前后四张表计数/epoch 相等”证明。

## 6. 与本票要求 / DESIGN.md 的偏差（及原因）

1. **`applications` 行由测试直接播种**：F02A 才拥有 `createApplication`，F01 的契约里没有创建
   应用的操作。测试用 psql 插入真实的 `platform.applications` 行（真实表、真实 FK、真实
   约束），不是 mock；`t019_stack.seed_application` 的 docstring 与 §7 都记录这一点。
2. **新增 `internal/audit` 包并删掉 `identity.RecordAudit`**：DESIGN §5 的命名是
   `audit.Record(...)`；T015 报告 §6.3 已把“需要独立 audit 包时是一次纯搬移”登记为可接受的
   后续动作。本票必须搬移，否则会出现 `identity → policy`（Bump）与 `policy → identity`
   （审计）的 import 环。事件 id 从 Go 侧 UUID 改为数据库内建 `gen_random_uuid()`（PG 16），
   语义不变。
3. **新增 `internal/uuid`（约 40 行）**：API 的请求体/路径、catalog 的游标都要校验 UUID 形状。
   放 `policy` 会让一个纯授权包承担通用解析；引第三方 UUID 库又违反“不新增依赖”的约束。
4. **grant/revoke 写路径放在 `policy` 包**：本票的 epoch 事务与授权判定必须共享一套错误语义；
   `Grant/Revoke/Bump` 与 `Authorizer` 同包，API 只做 HTTP 映射。业务校验（资源是否存在）
   也在同一事务里，避免 TOCTOU。
5. **单资源读取用 `Authorizer`，列表用 SQL ACL**：本票要求列表“ACL 在同一个 WHERE 里”，
   所以列表不能走缓存；详情走 `Authorizer` 才能证明 epoch/缓存语义在真实读路径上生效。
   两者都默认拒绝，结果一致。
6. **`expires_at` 用注入时钟比较，而不是数据库 `now()`**：本票明确要求“缓存 allow 必须带
   `expires_at` 并用注入时钟复核”，Go 单测需要假时钟。因此 `Authorizer` 的查库条件也把
   同一时刻作为参数传入。代价：列表 SQL 仍用数据库 `now()`，两处时钟在进程与数据库严重
   漂移时可能短暂不一致（单机 dev/生产同一时钟；见 §7）。
7. **畸形路径 UUID 返回 404 而不是 422**：`grant_id`/`application_id`/`principal_id` 是路径参数，
   畸形值不可能命中任何行；用与“不存在”相同的 404 保持“不可枚举”。请求体/查询参数仍是 422
   （字段可指认）。
8. **角色未变化时也 bump epoch**：每次成功的角色 PUT 都是一个授权状态写，本票测试按“每次写
   +1”断言；避免“相同集合就跳过”带来的两种语义。
9. **页面只做了服务端内容/行为断言**：没有浏览器或 JS 引擎执行（与 T015/T017 相同限制）。

## 7. 限制 / not_run

- **没有浏览器级验证**：`/apps` 与 admin 页面断言的是服务端返回的 HTML/JS 内容（表单 id、
  契约路径、CSRF 使用、401 跳转、空态与游标逻辑）以及同一 API 的行为测试；不执行 JavaScript，
  不截图，不做 HCI 验收。
- **没有 `listGrants`/`getGrant` 契约**：撤销必须使用创建响应返回的 grant id。admin 页把
  id 显示在页面上；离开页面后无法从 API 重新列出 grant——这是契约缺口，不是本票遗漏
  （openapi 只有 `grantResource`/`revokeResourceGrant`）。
- **F01 ACL 词汇表只有 `application`/`read`**：其它 `resource_type`/`action` 按设计返回 422；
  未知资源类型没有通用存在性探测。扩展点已在 `policy.ResourceTypes`/`Actions`。
- **列表 SQL 用数据库 `now()` 判断 grant 过期，`Authorizer` 用进程时钟**（§6.6）：单机部署
  两者同源；多实例时钟漂移时，列表与详情可能在过期边界短暂不一致。真正的统一时钟策略
  （数据库时间作为唯一权威）留给后续票。
- **决策缓存是单进程的**：多实例部署时每个实例各自缓存，但每次判定都读数据库里的 epoch，
  所以撤销仍会在下一次请求生效；“跨实例共享缓存/失效广播”不需要也没有实现。缓存无定时清理，
  只在达到上限时按 epoch 淘汰。
- **列表分页在大数据量下的性能未测**：只有 `applications_owner` 与 `grants_resource` 索引，
  `(created_at, id)` 排序在 F01 规模下可接受；大规模 keyset 索引与 EXPLAIN 验证留给目录/检索票。
- **并发测试只覆盖 2 个并发降级**：未做 N>2、事务中途崩溃、连接池耗尽等压测。
- **审计保留/轮转**：`audit_events` 只追加，没有保留期或归档任务（F01 范围外）。
- **没有推送、没有关闭 issue**（由主代理完成）。
- **没有对 `real-model`/`linux-sandbox` profile 做 HTTP 级实测**：集成测试仍以 `dev-stub`
  profile 运行；Cookie `Secure` 行为由 T015/T017 的 Go 单测覆盖，本票未新增 profile 相关代码。

## 8. T020 需要知道 / 不能破坏

- **授权助手签名不变**：`func (s *Server) authorize(w http.ResponseWriter, r *http.Request, requiredRole string) (*identity.Identity, bool)`。
  T020 的 bearer key 只加在 `internal/api/authz.go` 的 `authenticate`：先看
  `Authorization: Bearer`，Cookie 分支不动；`checkAuthorization` 已对 `AuthMethodAPIKey`
  免 CSRF，`identityViewFor` 已返回 `csrf_token: null`。
- **作用域与 ACL 的顺序**（DESIGN T020）：scope 缺失 → 403 `insufficient_scope`；scope 有但
  ACL 无 → 404（禁止枚举）。请用 `policy.Authorizer.Allow`/`catalog.Reader`，不要自己写
  第二套可见性 SQL；`catalog.ListVisible/GetVisible` 已把默认拒绝与“无枚举”固定。
- **服务账号读列表**：用服务 principal 自己的 id 调 `ListVisible`，**不要**替换成负责人 id；
  负责人只是治理归属，不传递正文权限。
- **epoch 语义**：任何改变授权状态的写（T020 的 key 撤销/scope 变化若被定义为授权状态、
  T021 的禁用/启用）都必须与业务写在同一个事务里调用 `policy.Bump(ctx, tx)`；不要直接
  `UPDATE policy_epochs`，也不要绕过它。缓存 allow 只在同一 epoch 内有效。
- **不要修改 `0007_authorization.sql`**（checksum 不可变；已应用的库会响亮失败）。F02A 的
  `application_versions` 外键要用**新迁移**补。`policy_epochs('acl')` 的播种行必须保留，
  删除它会 fail closed（所有非 owner 读取被拒绝）。
- **认证读路径**：`GET /api/v1/applications` 与 `/api/v1/applications/{id}` 目前只接受
  会话凭据；T020 打开 bearer 后这两条会自然接受 key（因为都走 `authorize`），不要为 key
  另开路由或另写 handler。
- **不要破坏 admin 页面 id**：`id="grants-panel"`、`id="grant-form"`、`id="grant-principal"`、
  `id="grant-resource"`、`id="grant-action"`、`id="grant-expires"`、`id="grant-status"`、
  `id="grant-result"`、`id="grant-id"`、`id="revoke-form"`、`id="revoke-grant-id"`、
  `id="revoke-status"`、`id="roles-panel"`、`id="roles-form"`、`id="roles-principal"`、
  `id="roles-status"`，以及 T015/T017 的 `id="signin-form"`、`id="invite-form"`、
  `id="auth-status"`、`id="identity-*"`、`id="invite-*"`、`id="activation-token"`、
  标题 `Orchara 管理台（F01 身份）` 与“只显示一次”。
- **测试基线**：`product/tests/f01` 现在 94 passed（T015 37 + T017 26 + T019 31）。
  `t019_stack.py` 提供 `seed_application`、`seed_applications_for_series`、`grant_read`、
  `revoke_grant`、`set_roles`、`list_applications`、`get_application`、`epoch_revision`、
  `audit_events`、`enabled_human_admins`、`role_bindings`、`hold_roles_lock`、
  `wait_for_advisory_lock`；T020 可直接复用，不要改 `t017_stack.py` 的既有助手语义。
- **审计助手现在是 `internal/audit.Record`**（`identity.RecordAudit` 已删除）。签名：
  `Record(ctx, tx, actorID *string, action, resourceType string, resourceID *string, metadata map[string]any)`；
  metadata 永不包含秘密。
- **`identity.SetRoles` 的锁**：`roles_updated` 的并发安全依赖 `pg_advisory_xact_lock` 的
  `ROLESET1` 键；T021 的禁用/启用若也要参与“最后一个管理员”判定，需复用同一把锁或证明
  两者不会互相破坏（T021 报告必须写明）。

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
