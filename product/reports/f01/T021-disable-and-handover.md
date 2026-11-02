# F01 T021 验收：禁用人员并安全交接未完成待办

- 任务：F01 / GitHub Issue #22（`T021`），分支 `feat/f01-identity`（HEAD `7334106` + 本票提交）
- 状态：**passed**（真实 PostgreSQL 16.4 + 真实编译 Go 二进制 + 真实 HTTP；无 mock、无跳过）
- 代码提交：
  - `f019365`（`feat(f01): disable principals with immediate credential revocation and handover`）
  - `53da337`（`test(f01): cover the T021 disable, last-admin, handover, audit and page boundaries`）
  - 本报告与 `PROGRESS.md` 在其后的 `docs(f01): ...` 提交中
- 主要文件：
  - `internal/identity/disable.go`（新增：`SetDisabled`、last-admin 纯规则、reason 校验、状态幂等判定）
  - `internal/identity/handover.go`（新增：`Handover`、三个调用方主体校验的纯规则、逐账号审计）
  - `internal/identity/roles.go`（只改 `rolesLockKey` 注释：它现在是角色/禁用启用/交接共用的锁）
  - `internal/api/principals.go`（新增：`PATCH /api/v1/admin/principals/{principal_id}`）
  - `internal/api/server.go`（注册上述路由）
  - `cmd/admin/main.go`（新增 `handover` 子命令、`usageError`/exit 2、用法文本）
  - `apps/web/src/admin.html`（禁用/启用面板、契约路径、CSRF、ok/bad 反馈）
  - `internal/identity/{disable,handover}_test.go`、`tests/f01/t021_stack.py`、`tests/f01/test_t021_*.py`（18 个集成测试）
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`E:\orchara-pg`）
  - Python 3.12.2
  - **未新增任何依赖**（`go.mod`/`go.sum` 未改）
  - 环境 profile：`dev-stub`（同 T015/T017/T019/T020；见 §4 real/stub 分报）
  - **未新增迁移**：`principals.disabled_at`、`sessions.revoked_at`、`api_keys.revoked_at`、`policy_epochs` 全部已存在；本票不碰 0005–0008
  - 基线：T020 后 `product/tests` = 498 passed、`tests/f01` = 124 passed；
    本票后 `product/tests` = 516 passed、`tests/f01` = 142 passed（新增 18，无回归、无跳过）

## 1. 交付内容

### 1.1 禁用 / 启用：`identity.SetDisabled`

```go
func (s *Service) SetDisabled(ctx, actorID, principalID string, disabled bool, reason string) (*PrincipalStateChange, error)
type PrincipalStateChange struct {
    PrincipalID     string
    State           string // "disabled" | "enabled"
    Changed         bool   // false = 幂等 no-op（无审计、无 epoch）
    SessionsRevoked int64
    KeysRevoked     int64
    Revision        int64
}
```

一个事务，进事务先取 `pg_advisory_xact_lock(rolesLockKey)`——**与 `SetRoles`、`Handover` 是同一把
`ROLESET1` 锁**（常量原样复用，注释已更新；没有第二把锁）。因此禁用/启用、角色降级、责任交接
互相串行，不可能两边都看到“还有另一个管理员”而同时成功。

事务内的顺序：

1. 读 principal（`kind`、`disabled_at`）与角色绑定；不存在/畸形 id → `ErrPrincipalNotFound`（HTTP 404）；
2. **幂等 no-op 判定**（纯函数 `disableChangesState`）：请求状态与库中状态相同 → 直接返回
   `{id,state}`，**不写审计、不 bump epoch、不撤销任何行**；事务回滚（本来也没有写入）；
3. 禁用且目标是“当前持 admin 的人类”时，在锁内数**其它**启用中的人类管理员
   （`kind='user' AND disabled_at IS NULL AND id <> $1`）；纯规则
   `disablingLastAdmin(kind, currentlyDisabled, roles, otherEnabledAdmins)` 为真 → `ErrLastAdmin`
   （HTTP 409 `last_admin`），什么都不改。自禁用同样受此保护；
4. 禁用（任何 kind）：
   - `UPDATE platform.sessions SET revoked_at=now() WHERE principal_id=$1 AND revoked_at IS NULL`，
     记录影响行数；
   - `UPDATE platform.api_keys SET revoked_at=now() WHERE revoked_at IS NULL AND (principal_id=$1
     OR principal_id IN (SELECT id FROM platform.principals WHERE responsible_user_id=$1))`，
     即**本人的 key + 本人负责的所有服务账号的 key**，记录影响行数；
   - `UPDATE platform.principals SET disabled_at=now() WHERE id=$1 AND disabled_at IS NULL`（CAS；
     锁保证同一主体不会被写两次）；
   - `audit.Record("principal.disabled", resource_type="principal", resource_id=$1,
     metadata={reason, sessions_revoked, keys_revoked})`；
5. 启用：`UPDATE platform.principals SET disabled_at=NULL WHERE id=$1 AND disabled_at IS NOT NULL`，
   `audit.Record("principal.enabled", metadata={reason})`；**已撤销的会话/密钥保持撤销**，不恢复；
6. 真实状态变化后在同一事务里 `policy.Bump(ctx, tx)`，提交。

principal 行**永不删除**：`audit_events.actor_id/resource_id` 的外键与历史因此始终可解析。

### 1.2 责任交接：`cmd/admin handover --from <uuid> --to <uuid> --by <uuid>`

- 连接信息只用 `ORCHARA_PROFILE` + `ORCHARA_DB_DSN`（`config.LoadDatabase(config.FromEnv())`），
  **DSN 永不出现在 argv**；成功/失败输出都不含任何密码、会话、key 或哈希。
- 命令行先做形状校验：三个 flag 必填、必须是 UUID；缺失/畸形/未知 flag → `usageError`，**exit 2**。
- 语义校验在**一个事务、同一把 ROLESET1 锁内**（`identity.Handover`）：
  1. 三个 principal 必须存在（否则 `--from/--to/--by: principal not found`，exit 1）；
  2. `--from` 必须是人类用户（`kind='user'` 且有 users 行）、**可以已禁用**；
  3. `--to` 必须是**启用中的人类管理员**（`kind='user'`、有 users 行、`disabled_at IS NULL`、
     持 `admin`）——即已经有权管理服务账号的人；
  4. `--by` 必须是**启用中的人类管理员**，并作为审计 actor；
  5. `--from != --to`。
- 更新用一条 data-modifying CTE：
  ```sql
  WITH moved AS (
      UPDATE platform.principals SET responsible_user_id = $2
       WHERE responsible_user_id = $1 AND kind = 'service'
      RETURNING id, display_name
  )
  SELECT id, display_name FROM moved ORDER BY display_name, id
  ```
  然后为每个账号写一条 `service_account.handover`（actor=`by`，resource_type=`principal`，
  resource_id=账号 id，metadata=`{from,to}`）。
- **不改任何别的东西**：不写角色绑定、不写/删 resource grant、不动 key（已撤销的保持撤销）。
  责任归属只是治理链接，不是正文权限，所以交接不可能扩大任何人的权限。
- 输出：`handover: N service account(s) reassigned from <from> to <to>` + 每个账号一行
  `<id>\t<display name>`；0 个账号也 exit 0。拒绝时 stdout 为空、stderr 给出原因、exit 1，数据库不变。
- **交接不 bump policy epoch**：ACL 决策键是 `(principal, resource, action, epoch)`，责任归属不是
  任何缓存判定的输入；key 认证每次读库。若未来引入依赖 responsibility 的缓存判定，交接必须改为
  同事务 bump（已写入 §8 复核提示）。

### 1.3 HTTP 契约

| 端点 | 授权 | 成功 | 失败 |
|---|---|---|---|
| `PATCH /api/v1/admin/principals/{principal_id}` `{disabled, reason}` | `authorize(w,r,"admin")`：会话→CSRF→角色；服务 key 无角色 → 403 | 200 `{id,state:"disabled"\|"enabled"}`（`Cache-Control: no-store`） | 422 `validation_failed`（`details.field`=reason/disabled）、409 `last_admin`、404 `principal_not_found`（畸形与不存在同一响应）、400 `invalid_json`、403 `csrf_invalid`/`forbidden`、401 |

- 顺序固定：`authorize`（认证 → CSRF → 角色）→ 路径 UUID 形状 → 读 body → 事务。伪造/越权请求
  在读 body、写任何行之前就被拒；`tests/f01/test_t021_validation.py` 逐类断言拒绝前后
  `disabled_at`/epoch/审计行数不变。
- `disabled` 在 Go 里是 `*bool`：**缺失返回 422（field=disabled）**，不会因为 bool 零值是 false
  而把 `{}` 当成“启用”（见 §6.1）。
- `reason` trim 后为空 → 422（field=reason）；写审计的是 trim 后的值。
- 路径中畸形 UUID 与合法但不存在同为 404 `principal_not_found`，响应不含主体名/登录名（不可枚举）。

### 1.4 页面 `apps/web/src/admin.html`

新增 `id="principals-panel"`（仅管理员可见，`principalsPanel.hidden = !isAdmin`）：

- 表单 `id="disable-form"`：`disable-principal`（principal id）、`disable-state`（禁用/启用 select）、
  `disable-reason`（必填，说明会写入审计）→ `PATCH /api/v1/admin/principals/{id}`，
  `csrf: await csrf()`，body `{disabled, reason}`；
- `id="disable-status"` 显示成功（`已禁用/已启用 principal ...`）或失败
  （`[error.code] error.message`，因此 `last_admin`、`principal_not_found` 在页面上可见）；
  401 → `window.location.assign('/login')`；
- 说明文字明确：禁用立即撤销会话与 key、启用不恢复已撤销凭据、principal 行永不删除、
  至少保留一个启用中的人类管理员；
- T015/T017/T019/T020 的 id、标题 `Orchara 管理台（F01 身份）`、字符串“只显示一次”全部保留；
  页面不使用 URL/storage 保存任何秘密。

### 1.5 测试

Go 单测（不碰数据库，`internal/identity/{disable,handover}_test.go`）：

- `TestDisableStateIdempotency`：4 个子用例（真正变化 vs 两个方向的 no-op）；
- `TestDisablingLastAdminMatrix`：8 个子用例（唯一管理员、有其他管理员、已禁用、非 admin、
  无角色、service principal、已禁用的 service principal）；
- `TestDisableReasonIsRequiredAndTrimmed`：空/空白/制表符拒绝并指认 `reason` 字段，合法值被 trim；
- `TestHandoverTargetValidationMatrix`：10 个子用例（合法、from 已禁用、from==to、to 非 admin、
  to 已禁用、to 是 service、by 非 admin、by 已禁用、by 是 service、from 是 service）。

集成测试（`tests/f01/test_t021_*.py`，18 个，真实临时库 + `cmd/admin migrate/bootstrap` + 真实
`cmd/api` 进程 + 真实 HTTP/psql，逐条对应见 §2）：

| 文件 | 测试 |
|---|---|
| `test_t021_disable_credentials.py` | `test_disabling_a_person_kills_the_session_and_the_service_key_immediately`、`test_enabling_restores_login_with_a_new_session_but_not_the_old_credentials` |
| `test_t021_last_admin.py` | `test_the_only_administrator_cannot_disable_itself`、`test_two_admins_one_can_be_disabled_but_never_the_last`、`test_two_concurrent_self_disables_leave_exactly_one_administrator` |
| `test_t021_validation.py` | `test_disabling_an_already_disabled_principal_is_a_no_op`、`test_enabling_an_already_enabled_principal_is_a_no_op`、`test_each_real_state_change_bumps_the_epoch_exactly_once`、`test_validation_and_authorization_refusals_change_nothing` |
| `test_t021_audit.py` | `test_audit_keeps_resolving_the_original_subject_after_disable` |
| `test_t021_service_accounts.py` | `test_disabling_a_service_account_revokes_its_keys_and_enabling_does_not_restore_them`、`test_enabling_a_service_account_whose_responsible_user_is_disabled_is_allowed_but_unusable` |
| `test_t021_handover.py` | `test_a_handover_moves_responsibility_without_expanding_any_authority`、`test_handover_refusals_exit_one_and_change_nothing`、`test_handover_usage_and_flag_errors_exit_two`、`test_handover_with_no_service_accounts_succeeds_with_zero` |
| `test_t021_pages.py` | `test_admin_page_has_the_disable_form_and_feedback`、`test_the_existing_admin_sections_survive_the_t021_additions` |

### 1.6 输入 / hash

代码（SHA-256，与本报告同一次提交内容一致）：

```text
97da6989bc4f12337d25cbe5a4104fa3897796d4271b6803baf8e8592cb1dfca  internal/identity/disable.go
7458b854a7218a1f37cfb0a19ae1622d9684350b83b6b5e17b1ea615e970f18d  internal/identity/handover.go
2048c94779711ba2e7f5928783337efb9ecd1782bd088fc123a8a71e9e0797fa  internal/api/principals.go
a3421468d0164ed27c9e3aa505470157e5657085749b90f5b52bddd5080cfcca  cmd/admin/main.go
856f65aed9fea91d89a7c3a6469028f1b7970aeadb4b75482e0fbbebabc25459  apps/web/src/admin.html
602d5d1aa837e0676fff970d258a43b5d082c8e2d919b06c724589b86bdc5ad5  internal/identity/disable_test.go
d72c7da2fe6ac285a4abed1499c13f03b9852f26db5e6a7c590cbd72e071ae85  internal/identity/handover_test.go
```

迁移 hash 无变化：本票没有新迁移，0005–0008 未编辑。

复核后补（2026-11-02）给 `apps/web/src/admin.html` 的 9 个表单加了 `method="post"`；该文件当前
SHA-256 为 `262753dd6243e815f48b21fa930e14526b5182acc95a1532f13c98e1658cd059`。其余文件未变，
本票的禁用/交接代码（`disable.go`、`handover.go`）未编辑。

### 1.7 复核后补（2026-11-02）：交接与创建设置的时序

复核发现服务账号创建读取负责人 `disabled_at` 时没有取 `ROLESET1`，因此“创建 paused → 禁用 R
→ `handover --from R --to A --by A` → 创建 resume”会留下仍以已禁用的 R 为负责人的服务账号；
交接已经提交，不会回溯移动它。修法在 `internal/identity/apikeys.go::CreateServiceAccount`：
事务开头取同一把 `pg_advisory_xact_lock("ROLESET1")`，负责人检查 + INSERT + 审计都在锁内，所以
创建要么先提交（随后的 handover 会移动它），要么看到 R 已禁用而 422 拒绝。交接本身的语义
（只改 `responsible_user_id`、逐账号审计、不动角色/授权/key）不变。证明测试：
`test_t020_issuance_race.py::test_a_service_account_created_across_a_handover_is_moved_by_it`
（并发创建与“禁用 + handover”；断言新账号负责人是 A，且再以 R 为负责人创建被 422 拒绝）。
无锁实现下新账号的负责人仍是 R（真实库上实测失败）。本票原有验收表与结论不变。

## 2. 验收标准逐条对照

Ticket 验收（`T021.md`）：

| # | 验收 | 证据测试 |
|---|---|---|
| 1 | **禁用后旧凭据失效** | `test_t021_disable_credentials.py::test_disabling_a_person_kills_the_session_and_the_service_key_immediately`（两个真实浏览器会话 + 本人负责的服务账号 key：禁用后旧 cookie 401 `session_revoked`（读与写都断言）、登录 403 `principal_disabled`、key 401 `invalid_key`；`sessions`/`api_keys` 行 `revoked_at` 非空，principal 行仍在且 `disabled_at` 非空；审计计数=2/1）；`test_t021_service_accounts.py::test_disabling_a_service_account_revokes_its_keys_and_enabling_does_not_restore_them`（服务主体本体的 key 全部撤销） |
| 2 | **待办交接不扩权限** | `test_t021_handover.py::test_a_handover_moves_responsibility_without_expanding_any_authority`（X 被禁用 → SA key 401；`handover --from X --to Y --by Z` exit 0、count 1、responsible=Y、审计 actor=Z/metadata from/to；Y 的 `role_bindings` 与 `resource_grants` 逐字节相同、SA 的 key 行（scopes/revoked_at）相同；Y 仍 404 且列表为空；新签发的 SA key 因 ACL 属于 SA 而可用）；`test_handover_refusals_exit_one_and_change_nothing`（to 非 admin、to 已禁用、by 非 admin、未知 id、from==to、to 为 service → exit 1、stdout 空、stderr 有原因、responsibility/审计/授权快照逐字节不变）；`test_handover_with_no_service_accounts_succeeds_with_zero`（0 个账号 exit 0、无审计） |
| 3 | **审计仍可定位原主体** | `test_t021_audit.py::test_audit_keeps_resolving_the_original_subject_after_disable`（禁用前后凡 actor/resource 指向 X 的审计行，经 `audit_events LEFT JOIN principals/users` 仍解析出 X 的 login 与 display name；禁用前 `auth.login` 的 actor/resource 保持 X；禁用事件 actor=管理员、resource=X；所有审计 metadata 与 API 日志都不含 session/key/password 哈希、`orchara_` 前缀或 `$argon2id$`） |
| 4 | 通过端到端入口实际演示；失败、当前授权与重试/恢复边界有断言，不以单层 mock 成功代替 | 18 个集成测试全部真实 PG + 真实编译二进制 + 真实 HTTP/psql。**失败边界**：每类拒绝都断言行/epoch/审计不变（`test_t021_validation.py::test_validation_and_authorization_refusals_change_nothing`、handover 拒绝测试）。**当前授权边界**：非管理员 403、服务 key 403、缺 CSRF 403、最后一管理员 409、未知/畸形 id 404。**重试**：重复禁用/启用 200 且无第二条审计/第二次 bump（`test_disabling_an_already_disabled_principal_is_a_no_op`、`test_enabling_an_already_enabled_principal_is_a_no_op`、`test_each_real_state_change_bumps_the_epoch_exactly_once`）。**恢复**：启用后可再次登录拿到新会话，旧 cookie 仍 `session_revoked`，旧 key 仍 `invalid_key`，必须重新签发（`test_enabling_restores_login_with_a_new_session_but_not_the_old_credentials`）。**并发**：两个真实 HTTP 客户端先认证、由真实 psql 会话持有同一把 `ROLESET1` 锁，两个并发自禁用恰好一个 200、一个 409 `last_admin`，最后剩一个启用中的人类管理员（`test_two_concurrent_self_disables_leave_exactly_one_administrator`，镜像 T019 的并发测试手法） |
| 5 | 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件标 blocked，未运行标 not_run | 本报告 §1、§3、§4、§7；代码/迁移 hash 见 §1.6；环境 profile `dev-stub` 明示；未执行项见 §7 |

本票 prompt 的 A–F：

| # | 要求 | 证据 |
|---|---|---|
| A1 | `authorize(w,r,"admin")` 先于一切；服务 key 403；reason trim 非空否则 422 field reason；畸形/缺失/未知路径 UUID → 404 | `test_t021_validation.py::test_validation_and_authorization_refusals_change_nothing`（422×3、404×2、403 non-admin、403 服务 key、403 缺 CSRF，全部无状态变化） |
| A2 | 同一事务 + 同一 `ROLESET1` 锁；幂等 no-op 无审计无 bump；最后一管理员 409；自禁用另有人时可成功 | `test_t021_last_admin.py` 全部 3 个 + `test_t021_validation.py` 的 epoch/no-op 测试 + concurrency |
| A3 | 禁用撤销该人会话 + 本人 key + 其负责服务账号的 key；`disabled_at`；`policy.Bump`；审计 `principal.disabled` 元数据 reason/sessions_revoked/keys_revoked（永不含秘密） | `test_t021_disable_credentials.py`（2 会话 + 1 个负责的 SA key；计数 2/1）；`test_t021_audit.py`（无秘密） |
| A4 | 启用清 `disabled_at`、bump、审计 `principal.enabled`；凭据不恢复 | `test_t021_disable_credentials.py::test_enabling_...`；`test_t021_service_accounts.py`（服务主体 key 不恢复） |
| A5 | 服务主体也可禁用/启用（无 last-admin 规则）；其 key 撤销；负责人被禁用时启用服务主体允许但不可用（文档化） | `test_t021_service_accounts.py` 全部 2 个（含 craft 一条未撤销 key → 403 `responsible_disabled`） |
| B | `handover` 用 env DSN、无秘密输出；事务内 ROLESET1 锁校验三个主体；from 可禁用；to/by 必须是启用中的人类 admin；只改 responsible_user_id；逐账号审计；改不了别的；输出 count+账号行；拒绝 exit 1、usage/flag exit 2 | `test_t021_handover.py` 全部 4 个 |
| C | 不改变 authorize/bearer/CSRF/scope/logout 语义；禁用后 `/auth/me` 401 或登录 403；既有 admin 页面 id/标题保留 | 既有 T015/T017/T019/T020 全部测试仍绿（`tests/f01` 142 passed）；`test_t021_pages.py::test_the_existing_admin_sections_survive_the_t021_additions`；本票未改 `internal/api/authz.go` 与 `internal/api/cookies.go` |
| D | admin 页面禁用/启用表单、PATCH、ok/bad 反馈（last_admin/principal_not_found 可见）、写操作统一 CSRF、保留全部既有 id/标题/字符串 | `test_t021_pages.py` 全部 2 个 + 既有 `test_t015_pages.py`/`test_t019_pages.py`/`test_t020_pages.py` |
| E1–E8 | §E 的 8 组场景 | 对应上面的 18 个集成测试（逐条映射见 §1.5 与本表） |
| E9 | Go 单测：last-admin 纯规则、状态幂等、交接目标校验、提取出的判定助手 | `internal/identity/disable_test.go`、`handover_test.go`（4 个 Test、22 个子用例，原始输出见 §3） |
| F | 报告 + PROGRESS（F01 5/5） | 本文件与 `product/PROGRESS.md` |

## 3. 实际命令与原始结果（在内容与 feat/test 提交一致的工作树上运行；docs 提交前）

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
ok  	orchara/platform/internal/api	1.991s
?   	orchara/platform/internal/audit	[no test files]
ok  	orchara/platform/internal/catalog	1.657s
ok  	orchara/platform/internal/config	0.675s
ok  	orchara/platform/internal/db	1.140s
ok  	orchara/platform/internal/health	2.054s
ok  	orchara/platform/internal/identity	2.538s
ok  	orchara/platform/internal/policy	1.444s
ok  	orchara/platform/internal/supervisor	0.900s
ok  	orchara/platform/internal/uuid	0.468s
TEST_EXIT=0
```

```text
$ cd E:\Orchara
$ python -m pytest product/tests/f01 -q
........................................................................ [ 50%]
......................................................................   [100%]
142 passed in 641.87s (0:10:41)
```

> T015/T017/T019/T020 的 124 个全部保持绿色，本票新增 18 个，共 142。

```text
$ cd E:\Orchara
$ python -m pytest product/tests -q
........................................................................ [ 13%]
........................................................................ [ 27%]
........................................................................ [ 41%]
........................................................................ [ 55%]
........................................................................ [ 69%]
........................................................................ [ 83%]
........................................................................ [ 97%]
............                                                             [100%]
516 passed in 894.12s (0:14:54)
```

> 门禁前基线：T020 后 498 passed。本票净增 18，全部真实执行、无 skip。

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
  "duration_seconds": 19.2,
  "verdict": "ok",
  "ok": true
}
CI_EXIT=0
```

新增 Go 单测的原始输出（4 个 Test、22 个子用例；子用例行已省略）：

```text
$ cd E:\Orchara\product
$ go test -count=1 -v -run "TestDisableStateIdempotency|TestDisablingLastAdminMatrix|TestDisableReasonIsRequiredAndTrimmed|TestHandoverTargetValidationMatrix" ./internal/identity
--- PASS: TestDisableStateIdempotency (0.00s)
--- PASS: TestDisablingLastAdminMatrix (0.00s)
--- PASS: TestDisableReasonIsRequiredAndTrimmed (0.00s)
--- PASS: TestHandoverTargetValidationMatrix (0.00s)
ok  	orchara/platform/internal/identity	0.767s
UNIT_EXIT=0
```

```text
$ cd E:\Orchara
$ python -m pytest product/tests/f01/test_t021_disable_credentials.py product/tests/f01/test_t021_validation.py product/tests/f01/test_t021_handover.py -q
..........                                                               [100%]
10 passed in 70.91s (0:01:10)
```

> 这三个文件在 142/516 全量跑之后做过 import 顺序/局部 import 的清理（不改变断言），
> 因此单独重跑确认仍绿；其余 `test_t021_*` 由上一条 142 的原始记录覆盖。

## 4. 实测结果（real vs stub）

- **real**：PostgreSQL 16.4（真实行、真实 `now()`、真实唯一/CHECK/FK、真实事务回滚与
  `pg_advisory_xact_lock` 排队）、真实编译的 `cmd/admin`/`cmd/api`、真实 TCP HTTP、真实 psql
  会话持有锁制造并发窗口；禁用后的会话/密钥状态直接从真实表读回；审计解析用真实 JOIN；
  秘密泄漏检查读取真实库哈希与真实子进程日志文件。
- **stub**：环境 profile 为 `dev-stub`，只表示这是开发环境（无真实模型调用、无 Linux 沙箱）；
  本票不涉及模型，没有 stub 业务结论。禁用/启用、交接、锁、审计全部真实实现与真实运行。
- **not_run**：见 §7。

## 5. 设计要点

- **一把锁**：`SetRoles`、`SetDisabled`、`Handover` 共用 `pg_advisory_xact_lock(ROLESET1)`。
  这是本票并发正确性的核心：两个并发自禁用若各自先 `COUNT` 再写，可能都看到“还有一个管理员”；
  串行化后第二个在锁内重新计数为 0。集成测试用真实 psql 会话持有同一把锁（`hold_roles_lock`）
  让两个真实 HTTP 请求先完成认证再一起排队，从而真正命中“剩 0”分支，而不是靠时序碰巧。
- **先幂等、后写**：no-op 判定在读角色/计数之前返回，重复请求既不会产生第二条审计，也不会
  二次 bump；这也让“重试”成为安全的客户端行为。
- **撤销是数据层的**：禁用不依赖“认证时再查负责人是否禁用”这一兜底（T020 的
  `responsible_disabled` 仍在，用于旁路构造的状态），而是把 `sessions.revoked_at` 与
  `api_keys.revoked_at` 真正写掉。因此禁用后最直接的观测是 401 `session_revoked` /
  401 `invalid_key`，而不是 403。
- **启用不恢复凭据**：这是刻意的 fail-closed。会话行保留为 revoked、key 行保留 revoked_at；
  重新启用只恢复“可以重新登录/可以重新签发 key”的能力。测试同时验证旧 cookie 仍 401、
  旧 key 仍 401、新 key 可用。
- **交接只动治理归属**：`responsible_user_id` 既不是角色也不是 ACL；不写 role_bindings、
  不写/删 grant、不动 key，因此交接在结构上就不可能扩权。测试用逐字节快照证明这一点。
- **审计主体不删除**：`principals` 行永久保留，`audit_events` 的外键与历史可解析；
  禁用事件的 actor 是操作管理员、resource 是被禁用主体。
- **写路径顺序**：认证 → CSRF → 角色 → 路径形状 → body → 事务；拒绝发生在任何写之前。
- **CLI 退出码分层**：命令行/flag/形状错误 = 2（不可能指向主体的输入）；对数据库状态的语义拒绝
  = 1（主体不存在、目标非管理员、目标被禁用、from==to）；成功 = 0。这样脚本可以区分
  “命令打错了”和“现在不允许”。

## 6. 与本票要求 / DESIGN.md 的偏差（及原因）

1. **`disabled` 缺失 → 422（field=disabled）**：OpenAPI 把 `disabled` 列为 required，
   但 Go 的 `bool` 零值是 `false`；若直接用 `bool`，`{}` 会被解释成“启用”。本票用 `*bool`
   显式区分缺失并 422。prompt 只写了 reason 的 422，这是为“不静默启用”而加的严格化。
2. **`handover` 的畸形 UUID → exit 2（usage），合法但不存在 → exit 1**：prompt 固定了
   “unknown ids → exit 1”与“usage/flag errors → exit 2”，但没有规定畸形 id。本票按
   “畸形 id 永远不可能指向主体、属于命令行错误”归入 2，并在用法文本中说明。合法的
   但不存在的 id 仍按 prompt exit 1。
3. **`--from` 必须是人类用户**（prompt 只要求“三个主体存在”）：`responsible_user_id` 的
   schema 语义只允许人类（T015 的 CHECK 与 T020 的创建路径），service principal 不可能是
   “负责人”。若静默返回 0 个账号，会把操作者的错误当成成功；拒绝并给出原因更安全。
4. **只有 `handover` 引入 exit 2**：`migrate`/`bootstrap` 的既有退出码保持原样，避免改变
   已被测试固定的行为（它们只断言非零）。
5. **禁用审计是聚合计数**：metadata 只有 `reason/sessions_revoked/keys_revoked`（prompt 指定），
   不逐个列出被撤销的 key id；逐账号可定位的是 `handover` 审计。若审计需要逐 key 追溯，
   应在后续票扩展（见 §8）。
6. **交接不 bump policy epoch**：责任归属不参与 ACL/角色决策，key 认证每次读库，没有会被
   旧判定击中的缓存；prompt 也要求“不改任何别的东西”。若未来有依赖 responsibility 的缓存，
   必须改为同事务 bump。
7. **未新增迁移**：所需列都已存在；没有编辑 0005–0008，也没有 0009。
8. **页面测试不执行 JavaScript**：与 T015/T017/T019/T020 相同的限制，只断言服务端 HTML 内容
   与行为（表单 id、契约路径、PATCH、CSRF、反馈元素、无 URL/storage 泄漏）。两轴复核后，
   共享 `assets/app.js` 已由 `tests/f01/test_f01_app_js.py` 在 Node 下真实执行，页面内联脚本
   也逐个通过 `node --check`；DOM/浏览器交互仍未运行。
9. **“未完成待办”为什么是服务账号责任、为什么只能 CLI**：F01 可交接的“未完成待办”定义为
   **服务账号责任**（`responsible_user_id`），因为审批/待办实体属于 **F06 的 approvals**，
   F01 没有也不应伪造审批数据模型；因此本票的交接范围只到服务账号，不是遗漏了审批。
   `updatePrincipal`（PATCH `/admin/principals/{id}`）的契约 body 只有 `{disabled, reason}`，
   **没有 successor 字段**，所以责任交接无法通过该 HTTP 端点表达，只能由
   `cmd/admin handover --from --to --by` 完成。这两条依赖关系是显式的：若 F06 定义 successor
   字段或审批交接，应在新票扩展契约，而不是回填 F01。

## 7. 限制 / not_run

- **没有 approvals 模块（F06）**：F01 唯一可交付的“未完成待办”是**服务账号责任**；审批/待办项
  的交接要等 F06 的 approvals 数据模型。本票没有伪造审批实体，也没有实现审批交接。
- **UI 没有交接入口**：契约 `updatePrincipal` 的 body 只有 `{disabled, reason}`，没有 successor
  字段，所以交接只能通过 `cmd/admin handover`。这是契约边界，不是页面遗漏。
- **禁用不会自动改责任归属**：禁用 X 会撤销 X 负责的 SA 的 key，但 SA 仍挂在 X 名下；在显式
  `handover` 之前无法为它签发新 key（负责人已禁用会被服务层拒绝）。这正是“先禁用、再交接”的
  运维顺序，但不会自动发生。
- **幂等 no-op 不“补撤销”旁路状态**：如果有人用 SQL 直接构造了“主体已禁用但会话/密钥未撤销”
  的行，再次发送 `disabled=true` 不会补撤销（幂等规则优先）；不过认证侧仍会因主体禁用返回
  403 `principal_disabled`，安全结论不变。重复请求不是修复动作，修复应通过一次性状态变更或
  直接运维处理。这是本票已知的语义弱点，列入 §8 供复核。
- **并发只覆盖 2 个请求**：没有 N>2、连接池耗尽、锁等待超时、跨实例时钟漂移的压测；
  与 T019 相同的边界。
- **锁等待没有显式超时**：`pg_advisory_xact_lock` 会一直等；HTTP 请求的取消由客户端/服务器
  超时决定。长事务持锁时禁用/交接会排队，没有自定义超时或重试提示。
- **审计 `reason` 是自由文本**：操作者可以输入任意内容（平台自身不会写密码/key/哈希）；
  没有敏感词过滤或长度上限（body 上限 1 MiB）。审计保留/轮转仍不在 F01（同前票）。
- **没有逐 key 的禁用审计**：见 §6.5。
- **没有浏览器级验证**、没有真实邮件/外部通知（交接靠人工知会）、没有推送、没有关闭 issue。
- **服务主体启用后仍不可用**：负责人被禁用时，允许启用服务主体（状态一致），但签发 key 会 422、
  认证会 403 `responsible_disabled`；必须先交接负责人或启用负责人。已由测试固定并在此文档化。

## 8. 两轴复核应重点看的地方（已知弱点）

1. **`ROLESET1` 锁的覆盖面**：新增的 `SetDisabled`/`Handover` 是否真的在所有会改变
   “谁可管理安装/谁负责服务账号”的路径上都取了同一把锁；未来新增的角色/责任写路径必须继续
   复用 `rolesLockKey`，否则 last-admin 规则会重新出现竞态。
2. **no-op 语义 vs 修复语义**（§7 第 4 条）：重复 `disabled=true` 不会补撤销旁路构造的凭据。
   若复核认为“重复禁用应当是自愈动作”，需要把 no-op 分支改成“仍然撤销未撤销的凭据但不再
   bump/审计”——但这会违反本票明确要求的“no audit row and no epoch bump”幂等语义，需产品裁决。
3. **`--to`/`--by` 要求启用中的人类管理员** 是否足够：交接不会把任何权限交给 `--to`，但会改变
   “谁能给这个 SA 签 key”的治理归属；复核应确认“admin 才能接手”与 SECURITY §1 的
   “未完成待办交接需审计”一致。
4. **审计粒度**：禁用只有聚合计数，交接逐账号；若事故复盘需要“哪把 key 何时被谁撤销”，
   当前只有 key 行上的 `revoked_at`，没有 per-key 审计（T020 的显式撤销才有 `api_key.revoked`）。
5. **`principal.disabled` 的自禁用语义**：actor == resource 是允许的（有另一个管理员时）；
   复核应确认审计上能区分“自禁用”与“他人禁用”（当前靠 actor/resource 比较即可）。
6. **锁等待与请求超时**（§7）：没有超时/重试策略，长事务下禁用会阻塞。
7. **reason 自由文本**（§7）：可被写入敏感内容；平台自身不写凭据。

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
