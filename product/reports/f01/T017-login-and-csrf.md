# F01 T017 验收：登录、刷新身份与安全登出

- 任务：F01 / GitHub Issue #18（`T017`），分支 `feat/f01-identity`
- 状态：**passed**（真实 PostgreSQL 16.4 + 真实编译 Go 二进制 + 真实 HTTP，无 mock、无跳过）
- 代码提交：
  - `f86b1e5`（`feat(f01): login, logout, identity refresh and CSRF enforcement`）
  - `64944b9`（`test(f01): cover the T017 login, CSRF, rate-limit and page boundaries`）
  - `952e9d1`（`test(f01): drop two unused fixture imports from the T017 session-boundary tests`）
  - 本报告与 `PROGRESS.md` 在其后的 `docs(f01): ...` 提交中
- 主要文件：
  - `internal/identity/csrf.go`（新增：CSRF 派生与常量时间校验）
  - `internal/identity/session.go`（`Login` 改用派生 CSRF；`Authenticate` 返回凭据方式 / CSRF / 会话哈希；新增 `Logout`）
  - `internal/identity/service.go`（`AuthMethod`；`Identity` 增加 `Method`/`CSRFToken`/`CSRFHash`/`SessionHash`）
  - `internal/api/authz.go`（新增：统一授权助手 `authorize` + 纯判定 `checkAuthorization` + `authenticate`）
  - `internal/api/loginlimit.go`（新增：进程内登录限流器，时钟可注入）
  - `internal/api/server.go`（`POST /auth/logout`、`GET /auth/me`、登录限流接线、invite 改用 `authorize`、`GET /login`）
  - `internal/api/cookies.go`（`clearedSessionCookie`）
  - `apps/web/src/login.html`（新增）、`admin.html`、`assets/app.js`、`index.html`、`activate.html`（只加导航）
  - `internal/identity/csrf_test.go`、`internal/api/{authz,loginlimit,cookies}_test.go`
  - `tests/f01/t017_stack.py` + 5 个 `test_t017_*.py`（26 个测试）
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`E:\orchara-pg`）
  - Python 3.12.2；未新增任何依赖（`go.mod` 未改）
  - 环境 profile：`dev-stub`（同 T015；见第 4 节 real/stub 分报）
  - F01 基线：T015 后 `product/tests` = 411 passed；本票后 437 passed（新增 26，无回归）

## 1. 交付内容

### 1.1 CSRF 基础：令牌与会话令牌绑定（不新增存储、不改迁移）

T015 只存 `sessions.csrf_hash`，并在登录时返回一个随机 CSRF token；页面刷新后
没有任何办法再次拿到可用的 CSRF token（T015 报告 §6.4 把这个缺口留给 T017）。
本票按既定决策用**派生**而不是**新增存储**闭合：

- `identity.DeriveCSRFToken(rawSessionToken) = base64url(HMAC-SHA256(key = 原始会话 token,
  message = "orchara.csrf.v1"))`。HMAC 是标准 PRF，没有自创密码学。
- `identity.Login` 现在返回派生值，`sessions.csrf_hash` 仍是 `identity.HashToken(csrf_token)`
  （SHA-256 hex）——T015 的“存储哈希 == 返回令牌的哈希”测试保持绿色。
- `identity.Authenticate` 从呈现的 Cookie 重新派生 `csrftoken`，并读出存储的
  `sessions.csrf_hash`；因此 `GET /auth/me` 不依赖任何进程内记忆或浏览器存储即可把
  同一个 token 交回。
- 校验：`identity.VerifyCSRFToken(storedHash, presented)` 比较
  `HashToken(X-CSRF-Token)` 与 `sessions.csrf_hash`，用 `subtle.ConstantTimeCompare`；
  空存储哈希（未来的 API key）与空提交都永不通过。比较的始终是哈希，从不是原始值。
- 派生值与会话一一绑定：同一账号的两个会话得到两个不同 token，A 会话的 token 无法通过
  B 会话的校验（Go 单测 + 集成测试，见 §2）。

为什么这仍然有防护意义：会话 Cookie 是 HttpOnly 的，跨站 JavaScript 既读不到会话
token，也读不到由它派生的 CSRF token；而跨站表单提交无法设置 `X-CSRF-Token` 请求头。
代价是“拿到会话 Cookie 的一方能推导 CSRF token”，即 CSRF 令牌不防 Cookie 窃取/XSS，
只防跨站伪造；这与“只存哈希、不新增第二份明文”的既有设计一致，已在此明示。

### 1.2 HTTP 端点

- `POST /api/v1/auth/logout`（会话 Cookie + 合法 `X-CSRF-Token`）：
  - 204，无响应体，`Cache-Control: no-store`；
  - `UPDATE platform.sessions SET revoked_at = now() WHERE token_hash = $1 AND revoked_at IS NULL`，
    只吊销这一条会话（同一账号的其它会话仍可用）；同事务写 `auth.logout` 审计；
  - `Set-Cookie: platform_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0;
    Expires=Thu, 01 Jan 1970 00:00:00 GMT`（`MaxAge=-1` + 过去时间）；
  - 缺少/伪造 CSRF → 403 `csrf_invalid`，会话**仍然有效**（有 DB 与 /auth/me 断言）；
  - 缺少/未知/过期/已吊销会话 → 401 `unauthenticated` / `session_expired` /
    `session_revoked`（先认证后 CSRF，伪造请求不会因为“未认证”而漏掉 CSRF 语义）。
- `GET /api/v1/auth/me`：200 `IdentityView {principal_id, kind, roles, csrf_token,
  expires_at}`，字段集合恰好这五个；`Cache-Control: no-store`；`csrf_token` 只对会话
  Cookie 非空，其它凭据（T020 的 API key）返回 `null`；角色为 `[]` 而不是 `null`。
  401 `unauthenticated`/`session_expired`/`session_revoked`，403 `principal_disabled`。
- 登录保持 T015 行为：相同错误码、未知账号与错误密码走同一个 argon2id dummy 校验、
  `auth.login` 审计不变。
- 请求日志规则不变：只有 method、无 query 的 path、status、duration、request_id；
  匿名、失败、429 日志行不出现 body/Cookie/Authorization/token（T015 的日志正则断言仍绿）。

### 1.3 单一授权助手（`requireIdentity` → `authorize`）

`internal/api/authz.go`：

```go
func (s *Server) authorize(w http.ResponseWriter, r *http.Request, requiredRole string) (*identity.Identity, bool)
```

顺序固定为：**认证 → CSRF → 角色**。

- `authenticate` 是唯一解析凭据的地方，返回带 `identity.AuthMethod` 的 `Identity`；
  T020 只需在这里加一个 `Authorization: Bearer` 分支，所有 handler 的调用点不变。
- `checkAuthorization(caller, method, presentedCSRF, requiredRole)` 是**纯函数**：
  不安全方法（GET/HEAD/OPTIONS/TRACE 之外全部）且凭据是 Cookie 时校验 CSRF，然后检查角色；
  先用 CSRF 再用角色，伪造请求只被告知“伪造”，不泄露调用者的授权情况。
  因为默认“非安全方法都要 CSRF”，新增写端点不会漏掉保护。
- 邀请端点改为 `s.authorize(w, r, "admin")`；登出、`/auth/me` 用 `s.authorize(w, r, "")`。
  `/auth/login`、`/auth/activate` 按契约是未认证端点，没有会话也就没有 CSRF 可查。
- `identityViewFor(caller)` 把 `Identity` 投影成契约 `IdentityView`（纯函数，Go 单测固定
  了逐字节 JSON，含 API key 的 `"csrf_token":null` 与 `"roles":[]`）。

### 1.4 登录限流

- 键 =（登录名小写去空白，`r.RemoteAddr` 的主机部分）。**不用** `X-Forwarded-For`
  之类的客户端可伪造头；能自选键的限流等于没有限流。
- 5 次失败 / 60 秒固定窗口；第 6 次（即使密码正确）→ 429 `rate_limited` +
  `Retry-After: 60` + `Cache-Control: no-store`，且不创建会话。
- 只有 `invalid_credentials` 计入失败；`invalid_json` 不是凭据尝试，
  `principal_disabled` 是密码已验证正确，均不计入。登录成功清除该键计数。
- 窗口实现为固定窗口（不是滑动窗口）：第一次失败开启窗口，窗口内累计到 5 即拦；
  窗口结束后自动放行，下一次失败开启新窗口。
- 时钟可注入（`newLoginLimiter(limit, window, now)`），Go 单测用假时钟证明窗口到期，
  全程不 sleep 60 秒。
- 内存卫生：超过阈值时清理已过期窗口，超过上限时淘汰任意条目（淘汰只会让守卫更宽松，
  不会误伤合法调用）。进程内、单实例——多实例部署时每个进程各自计数（见 §7）。

### 1.5 页面

- `apps/web/src/login.html`（新增，`GET /login`）：完整登录表单，POST
  `/api/v1/auth/login`，成功跳 `/admin`，`invalid_credentials`、`rate_limited`
  （含“被限流期间正确密码也会被拒”）与 `principal_disabled` 有可见文案，
  指向 `/activate` 的链接；不读地址栏、不进 storage。
- `apps/web/src/admin.html`：加载即 `requireLogin()` → `GET /auth/me`（只靠 Cookie），
  渲染 principal id 与角色；401 → `location.assign('/login')`；邀请写请求的
  `X-CSRF-Token` 来自 `csrf()`（即当前会话身份）；新增可见“退出登录”按钮，
  POST `/auth/logout` 成功后跳 `/login`；T015 的邀请表单、一次性 token 文本域与
  “只显示一次”提示全部保留（页面标题也未改，`ORCHARA_WEB_DIR` 测试依赖它）。
- `apps/web/src/assets/app.js`：新增 `identity(force)`（按页缓存 `/auth/me`）、
  `csrf()`、`requireLogin()`、`logout()`；保留 `api()` 与 `escapeHTML`；
  无框架、无构建步骤；token/CSRF 不进 URL、query、localStorage 或 sessionStorage
  （服务端 JS/HTML 断言直接 grep 这些字符串，见 §2）。
- `index.html`、`activate.html` 只新增 `/login` 导航链接，原有字符串与 `/admin`、
  `/activate`、`/apps` 链接不变（`tests/f00` 与 T015 页面回归测试仍绿）。

### 1.6 测试与 CI

- Go 单测（不碰数据库）：
  - CSRF 派生：用 **Python `hmac`/`hashlib` 独立算出的固定向量**核对 Go 实现（避免自证），
    另证确定性、每会话不同、32 字节 base64url、哈希比较的 6 种边界。
  - 授权：14 条决策矩阵（安全/不安全方法、Cookie/API key、角色、CSRF 先于角色）。
  - `IdentityView` 的逐字节 JSON 形状（含 API key 的 null/[]）。
  - Cookie 清除属性（`Max-Age=0`、1970 Expires、Path、HttpOnly、Secure 跟随 profile）。
  - 限流：第 5 次触发、不同登录/不同地址互不影响、键小写、窗口到期（注入时钟）、
    成功清零、过期清理与上限淘汰。
- 集成（`tests/f01/test_t017_*.py`，26 个）：真实临时库 + `cmd/admin migrate/bootstrap`
  + 真实 `cmd/api` 进程 + 真实 HTTP；见 §2 的逐条映射。
- `tools/ci_checks.py` 未改动：非 quick 的 `integration-tests` 已包含 `product/tests/f01`。

## 2. 验收标准逐条对照

| # | 验收 | 证据测试 |
|---|---|---|
| 1 | 登录登出及刷新身份闭环 | `test_t017_login_refresh.py::test_login_sets_a_safe_cookie_and_a_refresh_restores_the_identity`（Cookie `HttpOnly`/`SameSite=Lax`/`Path=/`/dev-stub 无 `Secure`；**只带 Cookie 的全新客户端**得到同一 principal、kind、roles、expires_at 与**同一个 CSRF token**）、`test_each_session_gets_its_own_csrf_token_and_each_is_restored`、`test_me_rejects_a_missing_or_unknown_session_cookie`；登出：`test_t017_csrf_and_logout.py::test_logout_revokes_only_that_session_and_clears_the_cookie`（204、无响应体、`Max-Age=0`/1970 `Expires`、只吊销本会话、另一会话仍 200、旧 Cookie 值 401 `session_revoked`）、`test_logout_without_a_session_is_unauthenticated` |
| 2 | 伪造 CSRF / 过期会话拒绝 | CSRF：`test_t017_csrf_and_logout.py::test_invite_without_a_csrf_header_is_refused_and_creates_nothing`、`test_invite_with_a_wrong_csrf_token_is_refused`（空值/乱值/43 字节假值/**直接提交存储的哈希** 4 个参数）、`test_a_csrf_token_from_another_session_is_refused`、`test_logout_without_a_valid_csrf_token_leaves_the_session_alive`——每个都断言 `csrf_invalid` 403 且 principals/users 行数不变；过期/吊销/禁用：`test_t017_session_boundaries.py::test_an_expired_session_is_rejected_on_a_read_and_on_a_write`（crafted `expires_at = now() - 1 minute`，`/auth/me` 与写请求都 401 `session_expired` 且无新行）、`test_a_revoked_session_is_rejected_on_a_read_and_on_a_write`（401 `session_revoked`）、`test_a_disabled_principal_is_rejected_on_a_read_and_on_a_write`（403 `principal_disabled`，且会话行未被连带吊销）；Go 单测 `TestCheckAuthorizationMatrix`/`TestUnsafeMethodsRequiringCSRF`/`TestVerifyCSRFTokenComparesStoredHashes` |
| 3 | 浏览器不会显示其他用户身份 | `test_t017_session_boundaries.py::test_two_signed_in_users_never_see_each_others_identity`（A、B 同时登录、重复轮询：A 的 Cookie 永远返回 A 的 principal/roles/CSRF，B 同理，双方响应体互不含对方 principal id；A 的 CSRF 配 B 的 Cookie → 403 `csrf_invalid` 且无新 principal，B 用自己的 token 仍 201）、`test_t017_login_refresh.py::test_me_does_not_leak_a_login_a_display_name_or_a_password_hash`（响应体无任何人的 login/display_name/password_hash，字段集合恰好五个）；页面侧只渲染 `/auth/me` 返回的身份，见 `test_t017_pages.py` |
| 4 | 通过上述端到端入口实际演示；失败、当前授权与重试/恢复边界有断言，不以单层 mock 成功代替 | 上述 26 个集成测试全部走真实二进制 + 真实 PostgreSQL + 真实 HTTP；失败与恢复边界额外覆盖：CSRF 失败后会话仍可用（`test_logout_without_a_valid_csrf_token_leaves_the_session_alive`、`test_invite_without_a_csrf_header...` 之后的 `/auth/me` 200）、限流“连正确密码也拒绝”与“不同登录不受影响/成功清零/大小写同键”（`test_t017_rate_limit.py` 4 个）、窗口到期只由注入时钟的 Go 单测证明（`TestLoginLimiterWindowExpiry`，不 sleep）；当前授权边界：非管理员带正确 CSRF 仍 403 `forbidden`（T015 `test_invite_requires_an_admin_session` 保持绿）、CSRF 校验先于角色（`TestCheckAuthorizationMatrix`） |
| 5 | 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件 blocked、未跑 not_run | 本报告 §1、§3、§4、§7 |

## 3. 实际命令与原始结果（本机实测，提交 `952e9d1` 上）

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
ok  	orchara/platform/internal/api	1.786s
ok  	orchara/platform/internal/config	0.487s
ok  	orchara/platform/internal/db	0.901s
ok  	orchara/platform/internal/health	1.533s
ok  	orchara/platform/internal/identity	1.912s
ok  	orchara/platform/internal/supervisor	0.689s
TEST_EXIT=0
```

```text
$ cd E:\Orchara
$ python -m pytest product/tests/f01 -q
...............................................................          [100%]
63 passed in 190.96s (0:03:10)
```

> T015 原有 37 个全绿，本票新增 26 个，共 63。

```text
$ cd E:\Orchara
$ python -m pytest product/tests -q
........................................................................ [ 16%]
........................................................................ [ 32%]
........................................................................ [ 49%]
........................................................................ [ 65%]
........................................................................ [ 82%]
........................................................................ [ 98%]
.....                                                                    [100%]
437 passed in 402.33s (0:06:42)
```

> 门禁前基线：`product/tests` = 411 passed（T015 后）。本票净增 26，全部真实执行、无 skip。

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
  "duration_seconds": 17.9,
  "verdict": "ok",
  "ok": true
}
CI_EXIT=0
```

新增 Go 单测的原始输出（节选，`-v` 的子用例行已省略）：

```text
$ cd E:\Orchara\product
$ go test -count=1 ./internal/api ./internal/identity -run "CSRF|LoginLimiter|Authorization|Cleared|IdentityView" -v
--- PASS: TestCheckAuthorizationMatrix (0.00s)
--- PASS: TestUnsafeMethodsRequiringCSRF (0.00s)
--- PASS: TestIdentityViewCarriesCSRFOnlyForACookieSession (0.00s)
--- PASS: TestClearedSessionCookieDeletesTheBrowserCookie (0.00s)
--- PASS: TestLoginLimiterTripsOnTheFifthFailure (0.00s)
--- PASS: TestLoginLimiterIsPerLoginAndPerPeerAddress (0.00s)
--- PASS: TestLoginLimiterKeyLowercasesTheLogin (0.00s)
--- PASS: TestLoginLimiterWindowExpiry (0.00s)
--- PASS: TestLoginLimiterSuccessClearsTheCounter (0.00s)
--- PASS: TestLoginLimiterPrunesFinishedWindows (0.00s)
PASS
ok  	orchara/platform/internal/api	1.111s
--- PASS: TestDeriveCSRFTokenMatchesAnIndependentVector (0.00s)
--- PASS: TestDeriveCSRFTokenIsDeterministicAndBoundToItsSession (0.00s)
--- PASS: TestVerifyCSRFTokenComparesStoredHashes (0.00s)
PASS
ok  	orchara/platform/internal/identity	0.757s
EXIT=0
```

> `TestCheckAuthorizationMatrix` 有 14 个子用例，`TestVerifyCSRFTokenComparesStoredHashes` 有 6 个；
> 子用例名逐条出现在 `-v` 输出中，全部 PASS。

## 4. 实测结果（real vs stub）

- **real**：PostgreSQL 16.4（真实行、真实 `now()` 过期判定、crafted UPDATE 构造过期/吊销/禁用）、
  真实编译的 `cmd/admin` 与 `cmd/api`、真实 TCP HTTP、真实进程日志捕获（T015 的秘密不外泄
  断言继续在 T017 代码上通过）。
- **stub**：环境 profile 为 `dev-stub`，只表示这是开发环境（无真实模型调用、无 Linux 沙箱）；
  本票不涉及模型，没有 stub 业务结论。密码哈希、会话、CSRF、限流全部真实实现与真实运行。
- **not_run**：见 §7。

## 5. 设计要点补充

- 派生而非存储：`GET /auth/me` 的“刷新后仍可用”由 HMAC 派生保证，数据库仍然只有
  `sessions.csrf_hash` 一列，不需要迁移，也不存在第二份明文。
- 比较的是哈希：`HashToken(header)` 与 `sessions.csrf_hash` 常量时间比较；把
  `sessions.csrf_hash` 本身当 header 提交也在集成测试中被拒绝（`test_invite_with_a_wrong_csrf_token_is_refused`
  的第四个参数）。
- “先认证、再 CSRF、最后角色”：未认证 → 401 由 `authenticate` 决定；已认证但 CSRF 失败 →
  403 `csrf_invalid`；CSRF 通过但角色不足 → 403 `forbidden`。`/auth/me` 是安全方法，
  不需要 CSRF，也不会返回别人数据。
- 登出只吊销一行：`WHERE token_hash = $1 AND revoked_at IS NULL`；重复登出（旧 Cookie）
  在认证阶段就被 `session_revoked` 拒绝，不会走到 UPDATE。
- 限流在 argon2id 之前：被限流的请求不消耗一次昂贵的密码校验，也就不构成放大攻击面。
- Cookie 清除必须属性齐全（尤其 `Path=/`），否则浏览器保留旧 Cookie，表现为“登出无效”；
  Go 单测固定了 `Max-Age=0`、1970 `Expires`、`Path=/`、`HttpOnly`、`Secure` 跟随 profile。

## 6. 与本票要求 / DESIGN.md 的偏差（及原因）

1. **CSRF 由“第二个随机 token”改为 HMAC 派生**：这是本票 prompt 的既定决策，替换 DESIGN §5
   的“CSRF token 是第二个原始 token”；T015 报告 §6.4 登记的缺口由此闭合。数据库零变更。
2. **`Identity` 增加内部字段**（`Method`/`CSRFToken`/`CSRFHash`/`SessionHash`）：DESIGN §5 只
   描述 principal/kind/roles/expires；T015 报告 §6.4 已指出该结构无法支撑 `/auth/me`。
   这些字段只在进程内使用，从不出现在任何 JSON 中（`IdentityView` 由纯函数显式投影）。
3. **登出写 `auth.logout` 审计**：本票未要求，但与 T015 的 `auth.login` 一致，用于身份操作
   可追溯；metadata 为空对象，不含任何秘密。
4. **限流只计 `invalid_credentials`**：`invalid_json` 不是一次凭据尝试；`principal_disabled`
   已通过密码校验，计入会把“密码正确但账号被禁用”的情况变成可被他人触发的锁定。已在 §1.4 说明。
5. **`Retry-After` 固定为窗口长度 60 秒**，而不是剩余秒数：本票要求 `Retry-After: 60`，且
   固定值不会给出“还剩几秒”的时序信息，客户端按它重试一定已经放行。
6. **授权助手是纯函数 + 薄壳**（`checkAuthorization` / `authorize`）：本票要求把
   `requireIdentity` 重构成“可要求角色、可为 Cookie 不安全方法要求 CSRF”的单一助手；
   拆成纯判定便于不依赖数据库/套接字地覆盖规则，handler 只负责写契约错误。
7. **`identityViewFor` 为 API key 预留 `csrf_token: null` / `roles: []`**：T020 才有 API key
   认证，这里是分支 + Go 单测先行；没有实现任何 API key 逻辑。
8. **限流是固定窗口而非滑动窗口**：第一次失败开启 60 秒窗口，窗口内第 5 次失败即拦到窗口
   结束。固定窗口的边界效应（窗口末尾集中尝试）在单进程内存限流里可接受，且行为更可预测、
   可被注入时钟的 Go 单测逐项证明；滑动窗口/分布式配额属 F15B。

## 7. 限制 / not_run

- **限流是单进程内存态**：多实例/多副本部署时每个进程独立计数，攻击者可通过分散到不同实例
  绕过；`Retry-After` 也只是本进程窗口。分布式配额属 F15B，本票不宣称具备。
- **限流键用 `r.RemoteAddr`**：直连部署正确；在反向代理后面所有客户端共享代理地址，需信任
  代理配置（trusted proxy）才能取真实客户端 IP，属部署/后续票范围。刻意不使用可伪造头。
- **CSRF 令牌不轮换**：会话生命周期内稳定（派生自会话 token）；轮换策略（如敏感操作后换 token）
  不在 F01 范围。
- **会话不会被动清理**：过期/吊销行保留在 `sessions`，清理任务不在本票；`/auth/me` 用数据库
  `now()` 判定过期，不依赖行删除。
- **浏览器级验证未跑**：没有真实/无头浏览器；页面断言是服务端 HTML/JS 的内容与行为断言
  （表单、契约路径、状态元素、401 跳转逻辑、token 不进 URL/storage）。HCI 级验收不在本票。
- **`Secure` Cookie 的 HTTP 级实测**：`linux-sandbox`/`real-model` 启动 API 需要模型端点配置，
  集成测试仍以 `dev-stub` 运行；Secure 属性由 Go 单测 `TestSecureCookieFollowsTheProfile`
  与 `TestClearedSessionCookieDeletesTheBrowserCookie` 覆盖。
- **argon2id 时序/时序攻击测量**：未做统计测量；限流在哈希之前，减小了在线爆破的算力放大。
- **API key 登出语义未定**：当前只有 Cookie 会话；`POST /auth/logout` 对未来的非 Cookie 凭据
  只清 Cookie 并返回 204（没有可吊销的服务端会话），T020 决定 key 撤销是否复用该端点。
- **未推送、未关闭 issue**（由主代理完成）。

## 8. T019 需要知道 / 不能破坏的接口

- **授权助手签名**（T019 直接复用，不要另写一套）：
  `func (s *Server) authorize(w http.ResponseWriter, r *http.Request, requiredRole string) (*identity.Identity, bool)`。
  返回的 `*identity.Identity` 带 `PrincipalID`/`Kind`/`Roles`/`ExpiresAt`/`Method`；
  `ok == false` 时响应已写好，handler 直接 `return`。
  - 管理员端点：`s.authorize(w, r, "admin")`（`POST /admin/grants`、`DELETE /admin/grants/{id}`、
    `PUT /admin/users/{id}/roles`、`POST /admin/service-accounts`、`POST /admin/api-keys`、
    `PATCH /admin/principals/{id}`）。
  - 普通读：`s.authorize(w, r, "")`（`GET /applications`、`GET /applications/{id}`）。
  - 规则顺序固定为认证 → CSRF → 角色；不要在 handler 里提前 `decodeJSON` 再鉴权，
    否则伪造请求会先被解析（当前 T015 邀请端点的顺序已统一为“先 authorize 再读 body”）。
- **bearer API key 如何插入**（T020）：只改 `internal/api/authz.go` 的 `authenticate`——
  先看 `Authorization: Bearer`，成功则构造
  `&identity.Identity{PrincipalID, Kind, Roles, ExpiresAt, Method: identity.AuthMethodAPIKey}`，
  失败按 DESIGN 返回 401 `invalid_key` / 403 `responsible_disabled`；Cookie 分支保持不动。
  `checkAuthorization` 已对 `AuthMethodAPIKey` 免 CSRF（Go 单测固定），
  `identityViewFor` 已为其返回 `csrf_token: null`、`roles: []`。
  `POST /auth/logout` 对非 Cookie 凭据只清 Cookie（见 §7 最后一条）。
- **CSRF**：任何新的写端点只要走 `authorize` 就自动强制 CSRF；不要绕过它直接调 `authenticate`，
  否则该端点没有 CSRF 保护。`X-CSRF-Token` 的校验始终是 `HashToken(header)` 与
  `sessions.csrf_hash` 的比较。
- **`identity.Identity` 的 CSRF 语义**：`CSRFToken` 是供 `/auth/me` 交回的明文（派生值），
  `CSRFHash` 是库里的哈希，`SessionHash` 是 Cookie 的 SHA-256（用于登出定位会话）。
  不要把这三个字段序列化进任何响应。
- **`identity.Service.Logout(ctx, sessionHash, principalID) (bool, error)`**：只吊销一行；
  返回 `false` 表示已经吊销/不存在，不当作错误。
- **限流器**：`loginLimiter` 是 `Server` 字段，只在 `handleLogin` 中使用；新增的认证入口
  （如 T020 的 key 认证）没有限流，若需要应另建键空间。
- **页面**：`/login` 已由 `api.Register` 注册；新增页面沿用 `s.staticPage("<file>")` 常量路径。
  `admin.html` 的标题字符串 `Orchara 管理台（F01 身份）` 被
  `tests/f01/test_t015_pages.py::test_pages_are_served_from_orchara_web_dir` 依赖，不要改。
  `admin.html` 必须保留 `id="signin-form"`、`id="invite-form"`、`id="auth-status"`、
  `id="invite-status"`、`id="invite-result"`、`id="activation-token"` 与“只显示一次”。
- **测试基线**：`product/tests/f01` 现在 63 passed（T015 37 + T017 26）；T019 追加时不得改动
  `t017_stack.py` 的公开助手语义（`Stack`/`Session`/`sign_in`/`session_for`/`invite`/
  `invite_and_activate`/`client_with_cookie`），可直接复用。
- **不要改 `0006_identity_core.sql` 或任何 `000x` 迁移**；本票没有新迁移。`docs/spec/` 与
  `contracts/` 仍是只读，`sync_spec.py --check` 必须继续 `no drift`。

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
