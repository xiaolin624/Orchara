# F12/T095 跨角色刷新与撤权后界面保持真实授权状态（GitHub #96）

**状态：`passed`**（交付可重放的 REST + 页面行为判定手段、真实运行证据与诚实裁决）。

> **手段声明（必读）**：本票**没有 Playwright/Chromium，也没有真实浏览器 DOM**。页面行为证据是
> 「Node 执行仓库里**已提交的真实 JS 字节** + 最小 DOM/fetch shim」：`assets/app.js`、新增
> `assets/nav.js`、以及各页面 HTML 里的真实内联 `<script>`（测试从提交的 HTML 中抽取执行）。
> 它证明：导航集合随身份变化、刷新重读 `/auth/me`、401/403 分支、按角色隐藏/显示入口、
> 真实请求形状；它**不证明** CSS 布局、断点、真实事件冒泡或浏览器缓存行为。REST 证据则全部是
> 真实 HTTP 打到真实 `cmd/api`（真实 PostgreSQL 16 隔离库 + mTLS internal listener + 真实对象目录），
> 不是 mock、不是进程内调用。

- 分支：`t/t095-role-truthful-ui`；worktree `E:\Orchara-wt\t095`；基线 `integration/f12` @ `497f4e8`
  （= T093 合并后的 tip）。结束前 `git merge integration/f12`（结果见 §10）。
- 范围：新增 `product/apps/web/src/assets/nav.js`（导航/角色门禁单一来源）；`assets/app.js` 的统一
  401/403 出口；22 个 HTML 的 nav 接线与 `data-requires-role` 门禁；apps/versions/models/login/chat/
  runs/debug/publish/evaluate/editor/admin/knowledge/personal-data 的页面级接线；
  `product/tests/f12/t095_support.py` + 两个测试文件（35 个用例）。
- **不改**：`product/docs/spec/**`、`product/contracts/**`、`product/tools/f12_api_gate.py`、
  `product/tests/f12/conftest.py`（T093 的门禁 session fixture 未被复用）。
- **不改** T096 拥有的 `assets/runview.js`、`assets/runs.js`、`assets/chat.js` 行为逻辑；本票对
  `/chat?version=` 与 `/runs?run=` 两个死链只做 **目标页 HTML 的最小接线**（见 §6.4），三个文件零改动。
- **服务端鉴权链未改**（`product/internal/api/authz.go:90-152`）：经 `authorize`/`requireScope`/
  `requireApplicationWrite` 的强制完整。**“隐藏入口不能代替鉴权”在服务端成立**；本票修的是界面
  真实性与可达性，不是补鉴权。§3 的负例表是从外部客户端**绕过隐藏、直接调 REST** 得到的拒绝证据。

## 1. 逐 AC 对照（issue #96 原文）

| 票面验收项 | 结论 | 证据 |
|---|---|---|
| **隐藏入口不能代替鉴权** | **passed** | 两个方向都证明。方向 A（服务端独立强制）：§3 的 20 行「入口→端点→负例主体→状态码」全部由真实 HTTP 拒绝（管理台 8 行 × 非管理员、创作 7 行 × 只读被授权者、个人数据 3 行 × 服务凭据、未认证 2 行），断言 403/401 的**稳定 `error.code`** 而不只是状态码；`tests/f12/test_t095_role_entrypoints.py::test_each_hidden_entry_is_refused_by_the_server`（20 条 parametrize）。方向 B（UI 该隐藏而没隐藏也是误导）：`/apps` 不再给只读者渲染「编辑草稿/调试」链接（versions 链接保留，因为读版本是 owner-or-grant 的合法操作）；新建应用、从模板创建、debug 提交表单、publish 预览+提交、evaluate 提交、editor 保存表单全部改为 `data-requires-role="creator" hidden`（默认关闭，仅当前身份确有 creator 角色时由 nav.js 打开）。`tests/f12/test_t095_page_roles.py::test_a_non_creator_sees_no_creator_entry_and_no_dead_click_link`、`::test_every_page_loads_the_single_navigation_source` |
| **列表详情分页一致** | **passed（本票范围内；未修项见 §6）** | REST：`GET /applications` 的 `items + next_cursor` 用 `limit=1` 真翻两页，两页不重叠、末页 `next_cursor=null`，单资源 `GET /applications/{id}` 与列表项**逐字段相等**（不是把列表项当详情复用），`limit=101/0/abc` → `422 validation_failed` 且 `details.field=limit`（T093 已统一的契约行为，本票直接复用其修好后的行为，未重复改服务端）。UI：models 页原来“算了 `next_cursor` 只说‘还有下一页’但没有按钮”，versions 页原来丢弃 `next_cursor`，personal-data 清单原来只取每个列表第一页，knowledge 页知识库列表只取第一页——四处都改为跟随 `items/next_cursor`；`tests/f12/test_t095_page_roles.py::test_the_models_page_follows_next_cursor_instead_of_only_announcing_it`、`::test_the_versions_page_follows_next_cursor_instead_of_silently_dropping_it`、`::test_personal_data_follows_next_cursor_for_its_inventory`。非契约列表 `/runs/{id}/children`、`/node-results` 恒 `next_cursor:null`、不可翻页（T093/T078 已证），其 UI 属 T096 的 runs.js，本票不改（§6.2） |
| **主体禁用和权限更新后 UI 不误导** | **passed** | 服务端真实行为（§3.1/§5.1）：禁用同一事务撤销会话/key，旧 Cookie 下一次读是 `401 session_revoked`，旧 key 是 `401 invalid_key`，登录尝试是 `403 principal_disabled`；启用不恢复旧凭据。角色更新后同一会话**下一次请求**即按新角色判定（先 403 → 提升后 201 → 降级后 403）。UI：`app.js` 新增统一 `handleAuthFailure`（401、403 `principal_disabled`/`responsible_disabled`）→ 清空所有 `[data-auth-content]` 区域并跳 `/login`（带 `?error=session_revoked|session_expired|principal_disabled` 让登录页给明确文案），`apiFull` 对**任何**数据请求统一走这个出口，所以没有写 401 分支的页面（如 approvals）也不会把拒绝读当成功；刷新是新的页面加载 → 重新 `GET /auth/me`（同一页只读一次，identity 缓存按页生命周期，不做 localStorage）；403 角色拒绝（`forbidden`/`creator_role_required`）不跳登录而是触发 `nav.js refresh()` 重读身份并重渲染，页面当场收敛到新角色。`tests/f12/test_t095_page_roles.py::test_navigation_is_rendered_per_role_and_the_page_re_reads_identity_on_refresh`、`::test_a_401_or_disabled_principal_lands_on_login_and_never_keeps_stale_rows`、`::test_a_refused_creator_action_converges_the_page_to_the_current_identity`、`::test_the_login_page_explains_a_disabled_principal_and_routes_by_role` |
| **通过上述端到端入口实际演示；失败、当前授权与重试/恢复边界有断言，不以单层 mock 代替** | **passed（Web 侧手段边界见首段）** | REST 侧：真实 `cmd/api` 进程/真实 PG/真实 HTTP（§3、§7.1）；页面侧：Node 执行提交字节 + 最小 shim（§4、§7.2）。失败/边界断言：未认证 401、无角色 403、被禁用后 401/403+登录 403、撤权后 404、翻页游标、`limit` 越界 422、401/403 后清空旧行、禁用原因文案、假退出（logout 真的发 POST）。重试/恢复边界：启用**不**恢复旧 cookie/key（断言非 200）；角色提升/降级往返；撤权后列表/详情同时不可见。唯一没有做的演示是“真实浏览器渲染”，已在首段与 §8 声明 `not_run` |
| **记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件标 blocked，未运行标 not_run** | **passed** | §8 版本/环境（Go 1.27.3、Python 3.12.2、Node v24.13.0、PostgreSQL 16.4、`ORCHARA_PROFILE=dev-stub`、契约 sha256 `bf191b…`）；§7 真实命令 + 原始 tail；stub/real 分报：本票页面测试是 “stub 的 DOM/fetch shim + real committed bytes”，REST 是 real stack；`not_run` 逐项列出（§8.1）。没有 `blocked` 项 |

## 2. 判定手段与可重放入口

| 层 | 入口 | 真值来源 |
|---|---|---|
| REST 入口表 | `python -m pytest tests/f12/test_t095_role_entrypoints.py -q` | 模块级 fixture 自建 pristine 栈（`t095_support.py`：独立库 + 迁移 + bootstrap + 编译 `cmd/api` + 真实 mTLS + 对象目录）；**不复用** `f12_stack`/`f12_gate`（T093 门禁会真实建 Run/领租约，会污染观察） |
| 页面行为 | `python -m pytest tests/f12/test_t095_page_roles.py -q` | Node 执行 `apps/web/src/assets/app.js`、`assets/nav.js`、页面 HTML 的内联脚本；DOM/fetch 是手写最小 shim（`HARNESS`，约 400 行），记录真实请求路径/方法并断言 |
| 静态完整性 | 同上文件里的 `test_every_page_loads_the_single_navigation_source` | 22 个 HTML 都挂 `/assets/nav.js`；login/activate/index 声明 `data-auth="optional"`；apps 的 creator 门禁与 `data-auth-content`；chat 的 logout 标记 |
| 语法门禁 | `node --check` + 内联脚本抽取检查 | 每个改动脚本单独 `node --check`；22 个 HTML 的 15 个内联脚本块抽取后逐个 `node --check` |

## 3. 入口 ↔ 后端端点 ↔ 负例主体 ↔ 实际状态码（真实栈实测）

主体：`reader`（`roles=["user"]`，对共享应用有 read grant）、`service_key`（服务账号的
`application:read` key）、`anonymous`（无 Cookie）、`admin`（仅用于建立 fixture）。全部走真实 HTTP；
`csrf` 是各 session 的真实 `/auth/me` token。**结果列是 2026-11-06 本次运行的实际观测**。

### 3.1 管理台入口 × 非管理员（server 侧独立 403）

| 入口 | 方法 + 端点 | 负例主体 | 观测 |
|---|---|---|---|
| 管理台·邀请用户 | `POST /api/v1/admin/users` | reader | `403 forbidden` |
| 管理台·创建授权 | `POST /api/v1/admin/grants` | reader | `403 forbidden` |
| 管理台·修改角色 | `PUT /api/v1/admin/users/{me}/roles` | reader（改自己也不行） | `403 forbidden` |
| 管理台·创建服务账号 | `POST /api/v1/admin/service-accounts` | reader | `403 forbidden` |
| 管理台·创建 API key | `POST /api/v1/admin/api-keys` | reader | `403 forbidden` |
| 管理台·禁用主体 | `PATCH /api/v1/admin/principals/{creator}` | reader | `403 forbidden` |
| 管理台·撤销授权 | `DELETE /api/v1/admin/grants/{reader_grant}` | reader | `403 forbidden` |
| 管理台·模型端点目录 | `GET /api/v1/admin/model-endpoints` | reader | `403 forbidden` |

### 3.2 创作入口 × 只有读授权的使用者（server 侧独立 403）

| 入口 | 方法 + 端点 | 负例主体 | 观测 |
|---|---|---|---|
| 创作·新建应用 | `POST /api/v1/applications` | reader | `403 creator_role_required` |
| 创作·保存草稿 | `PUT /api/v1/applications/{app}/draft` | reader（有 read grant） | `403 creator_role_required`（角色先于 owner 检查） |
| 创作·校验应用 | `POST .../validate` | reader | `403 creator_role_required` |
| 创作·私有调试 | `POST .../debug-runs` | reader | `403 creator_role_required` |
| 创作·发布预览 | `POST .../publication-preview` | reader | `403 creator_role_required` |
| 创作·提交发布 | `POST .../submit` | reader | `403 creator_role_required` |
| 创作·提交评测 | `POST .../evaluations` | reader | `403 creator_role_required` |

### 3.3 个人数据入口 × 服务账号、未认证入口

| 入口 | 方法 + 端点 | 负例主体 | 观测 |
|---|---|---|---|
| 个人数据·记忆列表 | `GET /api/v1/memories` | service_key | `403 personal_scope_forbidden` |
| 个人数据·诊断授权列表 | `GET /api/v1/diagnostic-grants` | service_key | `403 personal_scope_forbidden` |
| 个人数据·创建诊断授权 | `POST /api/v1/diagnostic-grants` | service_key | `403 personal_scope_forbidden` |
| 未认证·应用列表 | `GET /api/v1/applications` | anonymous | `401 unauthenticated` |
| 未认证·管理端点 | `GET /api/v1/admin/model-endpoints` | anonymous | `401 unauthenticated` |

### 3.4 撤权 / 禁用 / 角色更新的“下一次请求”真值（AC3 服务端半边）

| 场景 | 操作 | 下一次请求的观测 |
|---|---|---|
| 禁用人类主体 | admin `PATCH /admin/principals/{id} {disabled:true}` → 200 `state=disabled` | 旧 session `GET /auth/me` → `401 session_revoked`；旧 session `GET /applications` → `401 session_revoked`；`POST /auth/login` → `403 principal_disabled` |
| 禁用服务主体 | admin `PATCH` 该服务 principal → 200 | 其旧 API key → `401 invalid_key`（同事务撤销 key，见 §5.2 说明） |
| 启用（不恢复旧凭据） | admin `PATCH {disabled:false}` → 200 | 旧 session/key 仍非 200（401/403）；新 `POST /auth/login` → 200 |
| 角色提升 | admin `PUT /admin/users/{id}/roles ["user","creator"]` → 200 `state=updated` | 同一活会话 `GET /auth/me` → roles 含 creator；`POST /applications` → `201` |
| 角色降级 | admin `PUT ... ["user"]` → 200 | 同一会话 `/auth/me` → roles 仅 user；`POST /applications` → `403 creator_role_required` |
| 撤销 read grant | admin `DELETE /admin/grants/{id}` → `204` | 被授权者 `GET /applications/{app}` → `404 resource_not_found`（不可枚举）；列表不再含该应用 |

## 4. 页面行为断言（Node 执行已提交真实字节 + 最小 DOM/fetch shim）

| # | 断言 | 用例 |
|---|---|---|
| 4.1 | 不同角色得到不同导航：admin 有「管理台」；creator 的入口标签是「应用与创作」；普通 user 是「应用（只读）」且**没有**管理台；service principal 看不到个人数据/应用/管理台；匿名只有 状态/登录/激活账号 | `test_navigation_is_rendered_per_role_and_the_page_re_reads_identity_on_refresh` |
| 4.2 | 刷新重读身份：两次独立页面加载（两个 vm context）拿到不同 `/auth/me` 结果 → 导航不同；**每次页面加载只请求一次** `/auth/me`（不是 0 次、不是多次）；合法身份不产生跳转 | 同上 |
| 4.3 | 401 → 只跳一次 `/login`；403 `principal_disabled` → `/login?error=principal_disabled`；两种情况都先清空已渲染的旧行（预置 stale row 断言被清掉）并显示失败态 | `test_a_401_or_disabled_principal_lands_on_login_and_never_keeps_stale_rows` |
| 4.4 | 会话仍有效但**数据请求**被 403 `principal_disabled` 拒绝：先成功渲染 1 行 → 下一次读被拒 → 旧行被清空、跳转带原因，不呈现“读取成功” | 同上 |
| 4.5 | 401 `session_revoked`（禁用后 cookie 的真实码）→ `/login?error=session_revoked`；登录页把该原因显示为「会话已被撤销」 | 同上 + `test_the_login_page_explains_a_disabled_principal_and_routes_by_role` |
| 4.6 | 非 creator 的 `/apps`：创建卡/模板卡隐藏；应用行**没有** edit/debug 链接、保留 versions 链接；creator 三者都有 | `test_a_non_creator_sees_no_creator_entry_and_no_dead_click_link` |
| 4.7 | 会话内角色被撤后，下一次被拒绝的创建动作**不跳登录**（403 不是会话死亡），页面显示 API 稳定码、创建卡随即隐藏、导航标签变回「应用（只读）」——无需手工刷新 | `test_a_refused_creator_action_converges_the_page_to_the_current_identity` |
| 4.8 | 导航里的「退出」真的调用 `POST /api/v1/auth/logout`（一次）并阻止默认跳转，随后才去 `/login`（修掉 chat.html 原来的假退出） | `test_the_logout_link_revokes_the_session_instead_of_just_linking_to_login` |
| 4.9 | versions 页：`next_cursor` 非空 → 出现「下一页」，点击后带 `cursor` 请求并追加第二页，末页隐藏按钮 | `test_the_versions_page_follows_next_cursor_instead_of_silently_dropping_it` |
| 4.10 | models 页：原来只提示“还有下一页”不给控件 → 现在出现按钮，点击带 `cursor`、追加、末页隐藏 | `test_the_models_page_follows_next_cursor_instead_of_only_announcing_it` |
| 4.11 | personal-data 的 `collectAllPages` 跟完 `next_cursor`（两页都进清单），游标请求形状正确 | `test_personal_data_follows_next_cursor_for_its_inventory` |
| 4.12 | 登录页 `?error=principal_disabled` 显示「这个账号已被禁用」；普通 user 登录后落 `/apps` 而不是 `/admin`（非管理员不再落到“所有面板都藏起来”的管理台） | `test_the_login_page_explains_a_disabled_principal_and_routes_by_role` |
| 4.13 | 22 个 HTML 都加载单一导航源 `nav.js`；可选认证页声明 `data-auth="optional"`；apps 的 creator 门禁/`data-auth-content`；chat 的 logout 标记 | `test_every_page_loads_the_single_navigation_source` |

## 5. 审计发现并修（修前 → 修后 → 覆盖）

### 5.1 导航是 22 份手写静态列表 → 单一角色化导航源

- **修前**：每页手写同一组 `<a>`，不查角色；`/login` 登录后固定跳 `/admin`，普通用户落在面板全隐藏的管理台；
  `chat.html:17` 的“退出”只是 `href="/login"`，从未调用 `logout()`。
- **修后**：`assets/nav.js` 按 `/auth/me` 的 `roles`/`kind` 生成唯一导航（管理台仅 admin；`/apps` 标签
  creator→「应用与创作」、否则「应用（只读）」；service kind 不显示人类入口；匿名=状态/登录/激活），
  22 页统一挂载；退出锚被 `nav.js` 拦截并走真实 `logout()`；登录成功先 `identity(true)` 再按角色跳转。
- **覆盖**：§4.1/4.2/4.8/4.12/4.13。

### 5.2 401/403 分散且 403 不跳登录 → 统一出口 + 清空旧数据

- **修前**：`app.js` 只在 401 跳登录；403 `principal_disabled` 不跳、不清 identity 缓存、各页当普通加载失败；
  `approvals.js` 完全没有 401 分支；页面在失败后仍保留上一次成功渲染的行。
- **修后**：`apiFull` 对非 auth 端点的 401/403 auth failure 统一调用 `handleAuthFailure`（一次性、清空
  `[data-auth-content]`、跳 `/login` 或带原因的登录页）；`requireLogin` 复用同一出口；404/403 角色拒绝
  留在页面并触发 nav 重读身份。所有主要列表/详情容器标记 `data-auth-content`（apps/versions/models/
  runs/approvals/memories/personal-data/tools/catalog/knowledge/chat/reviews/evaluations/debug/evaluate/
  attachments/admin 的一次性令牌与 key）。
- **覆盖**：§4.3–4.5、§4.7。

### 5.3 该隐藏而没隐藏（UI 误导，AC1 反向）

- **修前**：`/apps` 对每个可见应用无条件渲染编辑/调试/版本链接；新建/模板按钮对任何登录者可见；
  debug/publish/evaluate/editor 的提交表单对无 creator 角色者可见（点击才 403）。
- **修后**：上述全部改为 `data-requires-role="creator" hidden`，由 `nav.js` 只对确有 creator 角色的身份
  打开；`/apps` 的应用行按角色决定是否创建 edit/debug 链接（versions 保留）。README 文案也同步写明
  “需要 creator，服务端会 403”。
- **覆盖**：§4.6。

### 5.4 分页控件缺失/被丢弃

- **修前**：`models.html` 算了 `nextCursor` 但只显示“还有下一页”文字；`versions.html` 只 `page.items`
  丢弃 `next_cursor`；`personal-data.js` 对记忆/知识库/文档各只取第一页；`knowledge.html` 知识库列表只取第一页。
- **修后**：四处跟随 `items + next_cursor`（models/versions 真按钮；personal-data 用 `collectAllPages`
  取完整清单；knowledge 循环游标）。
- **覆盖**：§4.9–4.11 + REST §3.4 的分页/详情一致性。

### 5.5 假退出与两个死链

- **修前**：`chat.html:17` 退出不撤销会话；`catalog.js` 的 `/chat?version=` 目标页不读参数；
  `chat.js` 的 `/runs?run=` 目标页不读参数。
- **修后**：退出走 `nav.js` 的 logout 接线；**目标页 HTML** 增加最小内联接线：chat 从
  `location.search` 读 `version`（UUID 校验后写入真实输入框），runs 读 `run`（UUID 校验后调用既有
  `loadRunDetail`）。**没有改** `chat.js`/`runs.js`/`runview.js`（T096 所有权）。
- **覆盖**：§4.8 + `tests/f12/test_t095_page_roles.py` 的静态断言；死链的端到端点击行为未在真实浏览器里跑
  （`not_run`，见 §8）。

## 6. 未修（归属其它 epic / 明确不在本票范围）

### 6.1 契约缺口（如实记录，不补）

- 没有 `listGrants` / `getGrant`；`updatePrincipal` body 只有 `{disabled,reason}`；没有 `listApiKeys`
  （T019/T020/T021 报告已记）。因此管理台的“授权/角色/服务账号/API key”只能按 id 操作，本票未新增契约端点。
- 403 `principal_disabled` 在**浏览器 cookie 路径**上实际上不可达：禁用在同一事务先撤销会话，解析器先命中
  撤销（`internal/identity/session.go:158-166`），观测到 `401 session_revoked`；key 路径同理先命中
  `invalid_key`。该 403 分支是防御性分支，UI 仍按要求处理（Node 用例用服务端形状的 403 证明页面行为），
  服务端可达的 `403 principal_disabled` 入口是 **登录 POST**（登录页文案）。这不是本票要改的鉴权语义。

### 6.2 归属 T096（runs.js/chat.js 行为逻辑）

- `/runs/{id}/children`、`/node-results` 是非契约列表（恒 `next_cursor:null`、忽略 cursor/limit）；
  runs 页的产物列表（`runs.js:1618-1643`）只取第一页且无下一页控件；chat 的会话/消息列表（`chat.js:646-737`）
  只取第一页；chat 队列的 `dispatched/finalizing` 口径。**本票不改这些文件**，留给 T096 的断线/状态票统一处理。
- 合并提示（T096 若也动这两个 HTML，按以下行号核对；**JS 文件零改动**）：
  - `chat.html`：`17`（logout `data-orchara-logout`）、`32`（`conversation-list` 的 `data-auth-content`）、
    `128`（nav.js 引入）、`134-147`（读 `?version=` 并预填 `#chat-version-id` 的内联脚本）。
  - `runs.html`：`98`、`103`（`data-auth-content`）、`245`（nav.js 引入）、
    `248-265`（读 `?run=` 并调用既有 `loadRunDetail` 的内联脚本）。

### 6.3 既有功能缺口（F10/F11 等）

- knowledge 页没有“已有文档列表”（只有刚摄取的一条）；`/personal-data` 没有会话列表/删除入口、
  没有文件列表（契约无 `GET /artifacts`）；这些是 F10/F11 的功能缺口，不是 T095 的界面真实性缺陷，未新增页面。

### 6.4 分页仍未处理的页面（明确记录）

- `/runs` 的 artifacts 列表与 `/chat` 会话/消息列表（T096 所有权，见 6.2）。
- `evaluations`、`reviews` 的列表已有 `next` 按钮（既有实现）；未逐一重跑其翻页交互（无行为改动）。
- `/debug`、`/evaluate` 的宽表布局与小屏样式归 T098；本票未改 CSS。

## 7. 真实命令与原始 tail（2026-11-06，本机）

### 7.1 T095 新套件（真实栈；串行，占 `locks/realstack`）

```powershell
cd E:\Orchara-wt\t095\product
python -m pytest tests/f12/test_t095_role_entrypoints.py -q
python -m pytest tests/f12/test_t095_page_roles.py -q
```

```
.........................                                                [100%]
25 passed in 22.35s
```

```
..........                                                               [100%]
10 passed in 1.24s
```

### 7.2 全量 F12 套件（串行；必须仍全绿，含 T093 的 242 passed）

```powershell
python -m pytest tests/f12 -q
```

```
........................................................................ [ 25%]
........................................................................ [ 51%]
........................................................................ [ 77%]
.............................................................            [100%]
277 passed in 60.08s (0:01:00)
```

（277 = T093 既有 242 + T095 新增 35；本次运行先串行占 `locks/realstack`，pytest 退出码 0。）

### 7.3 既有受影响套件（Node 页面测试，无栈）

```powershell
python -m pytest tests/f08/test_postreview_pages.py tests/f04b/test_t083_runview.py -q
```

```
..............................                                           [100%]
30 passed in 50.30s
```

（同一次运行还包含 `tests/tooling` 的 21 passed；`tests/f08/test_postreview_pages.py` + `tests/f04b/test_t083_runview.py` 共 9 passed。）

### 7.4 Go / 静态 / 语法门禁

```powershell
& $go build ./...
& $go vet ./...
& $go test ./...
& 'E:\orchara-toolchains\go\bin\gofmt.exe' -l internal cmd tools
python tools/sync_spec.py --check
node --check apps/web/src/assets/nav.js
node --check apps/web/src/assets/app.js
node --check apps/web/src/assets/personal-data.js
python E:\Orchara\.scratch\f12\T095\check_inline_scripts.py   # 22 页 15 个内联块逐个 node --check
```

```
build:
build_exit=0
vet_exit=0
ok  	orchara/platform/internal/uuid	(cached)
ok  	orchara/platform/internal/workflow	(cached)
?   	orchara/platform/probes/f10_pdf_compare/go_baseline	[no test files]
?   	orchara/platform/probes/f15a_objectstore/localfs_probe	[no test files]
test_exit=0
gofmt:
gofmt_exit=0
```

`python tools/sync_spec.py --check`：

```
no drift: spec copy and contract snapshot match the manifest
```

`node --check`：nav=0 / app=0 / personal-data=0；内联抽取检查：

```
inline blocks checked: 15
all inline scripts parse
```

## 8. 版本、输入/hash、环境 profile 与边界

### 8.1 版本与 hash

| 项 | 值 |
|---|---|
| 分支 / worktree | `t/t095-role-truthful-ui` / `E:\Orchara-wt\t095` |
| 基线 | `integration/f12` @ `497f4e8`（T093 merge） |
| 契约快照 | `contracts/openapi.json` sha256 `bf191b9697f99b362bd61c287964724637e1281153232699aacf582157fc3e91`（93 paths / 106 operations；**未改**） |
| 任务图 | `contracts/task-graph.json` sha256 `e58465e9705e6398eb0e87f8529263c61793756376247cd71d8c63e6beaedcf6`（**未改**） |
| Go / Python / Node / PG | Go 1.27.3 / Python 3.12.2 / Node v24.13.0 / PostgreSQL 16.4（隔离集群 `127.0.0.1:55432`） |
| 环境 profile | `ORCHARA_PROFILE=dev-stub`（无真实厂商模型/凭据）；`ORCHARA_RUN_EVENT_RETENTION=24h`（默认，本票不依赖） |
| 测试栈 | 每个 fixture 自建独立数据库（`orchara_t095_*`），真实 `cmd/api` + mTLS internal listener + 对象目录；结束后 drop |

### 8.2 `not_run`（没有真实执行的，不写成通过）

- **真实浏览器 DOM / Playwright / Chromium**：环境无此能力（ENV.md §1）；页面结论来自 Node + 最小 shim。
  因此 CSS 断点、真实事件冒泡、浏览器前进/后退缓存、真实 `window.location` 导航均 `not_run`。
- **死链修复的真实点击端到端**：`/chat?version=` 与 `/runs?run=` 只做了“目标页读参数”的静态/内联接线，
  未在真实浏览器里点过；Node 页面测试也未执行 chat.js/runs.js（T096 所有权），标记 `not_run`。
- **SSE**：T095 的页面路径不消费 SSE（T096 范围）；本票没有 SSE 用例，`not_run`。
- **403 `principal_disabled` 的浏览器 cookie 真实路径**：正常禁用流程不可达（见 §6.1）；页面行为用
  服务端形状的 403 在 shim 里证明，REST 侧只证明登录 POST 的 403。
- **移动端/响应式**：T098 范围，本票未改 CSS、未测小屏，`not_run`。
- **真实厂商模型**：dev-stub only；本票不调用模型，不影响界面授权结论。

### 8.3 `blocked`

- 无。本票所需的真实 PG、编译器、Node、mTLS 证书均可用。

## 9. 复跑与合并

- 结束前 `git merge integration/f12`：见 §10；若集成分支仍是 `497f4e8`，merge 为 **Already up to date**，
  最终树与门禁树一致（报告在 §10 记录实际输出）。
- 复跑命令与 §7 相同；提交拆分：`feat(f12)` 页面/脚本 → `test(f12)` 用例 → `docs(f12)` 报告/PROGRESS。

## 10. 实际合并结果与交付 HEAD

```powershell
cd E:\Orchara-wt\t095
git rev-parse integration/f12      # 497f4e8206ca9251e8d66667c5e2095f4944a971
git merge integration/f12          # Already up to date.
git rev-parse HEAD                 # 代码/测试提交 2d0871fb9ef9c77d2c5f1e33681d2648ff5ee7cc
```

- 集成分支 tip 仍是 `497f4e8`（T093 合并后的基线），因此 merge 是 **Already up to date**：
  §7 的 `pytest tests/f12` **277 passed** 与 Go/静态门禁就是在最终树上跑的；本次 merge 没有引入新提交。
- merge 后在交付 HEAD 的同一代码树上按要求**再复跑**一次全量 F12（串行占锁）：

```
........................................................................ [ 25%]
........................................................................ [ 51%]
........................................................................ [ 77%]
.............................................................            [100%]
277 passed in 66.16s (0:01:06)
```

  日志：`E:\Orchara\.scratch\f12\T095\pytest-tests-f12-final.log`；退出码 0。
- 提交（合并前）：`e8107fe feat(f12): render role-truthful navigation and session-failure state` +
  `2d0871f test(f12): execute the T095 entry table and committed page bytes` +
  本报告/PROGRESS 的 `docs(f12)` 提交（交付 HEAD 见 `E:\Orchara\.scratch\f12\T095-REPORT.md` 与
  `git log -1`）。
- 工作树干净（`git status --short` 空）后交给编排者在 `integration/f12` 合并。
