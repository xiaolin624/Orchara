# F12 复核修复报告（A–K）

- 批次：F12 复核修复（两轴 code review 的 Standards + Spec 发现）
- 分支：`t/f12-review-fixes`（worktree `E:\Orchara-wt\review-f12`）
- 基线：`integration/f12` @ `cacfc44`
- 工作目录：`E:\Orchara-wt\review-f12\product`
- 范围：`product/tests/f12/**`、`product/tests/f01/**`、`product/tools/**`、`product/internal/api/**`、
  `product/cmd/api/main.go`、`product/apps/web/src/**`、`product/reports/f12/**`、`product/PROGRESS.md`
- **未改** `product/docs/spec/**`、`product/contracts/**`、`product/tools/f12_api_gate.py` 的业务判据
  （只把 `implemented` 的判据按复核 G 拆成 route/functional 两类；没有改契约、迁移、产品业务逻辑）。
- 真实栈全程串行：`E:\Orchara\.scratch\f12\locks\realstack` 由本工作树持有，收尾删除。

## 0. 结论总表

| 条目 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| A 明文凭据进证据 | P0 | **已修** | 三个证据 JSON 在源头脱敏后重新生成；扫描回归测试先红后绿；历史保留但已失效，不改写 |
| B `not_run` 被改写成 `passed` | P0 | **已修** | 删掉隐式改写；`passed` 必须显式 `mark_passed()` 且至少一条断言；空旅程保持 `not_run` |
| C F01 查询参数守卫假通过 | P1 | **已修** | 单/双引号、`searchParams.get`、变量 key 全部覆盖；无法提取 key 即失败（fail closed） |
| D F01 源码字面量冒充行为 | P1 | **已修** | Node 执行 **served** `app.js` 真实字节：401 导航一次、清空 `[data-auth-content]` |
| E1 `nav.js` 重复导出 | P2 | **已修** | 删除 `items`，只保留 `navItemsFor`；无调用点 |
| E2 重复状态/错误分类器 | P2 | **保留** | 页面总先加载 `runstream.js`，但 20+ 个既有 Node 页面测试单独加载 `runs.js/chat.js/runview.js`，删除会破坏既有行为 |
| E3 真实栈 fixture 四处复制 | P2 | **不修** | 已知重复，重构风险大于收益，见 §E |
| E4 门禁 60/106 与源码正则 | P2 | **记录为已声明边界** | 不在本票扩大改写；新口径见 G/H 与 T093 报告 §5.6 |
| G T093 把 route-only 算 implemented | P0 | **已修** | `route_registered` / `functional_probe` / `route_only` 分开；报告改为 route-registered 106/106、functional 60/106 |
| H T093 授权记录未验内容隔离 | P0 | **已修** | 18 个 list/get 真实断言“另一主体看不到 creator id”；13 条无夹具明确 `not_run` |
| I T098 报告乱码 | P1 | **已修** | 按代码/测试重写；新增编码扫描；报告 118 行、UTF-8 无 BOM、PUA=0、U+FFFD=0 |
| J T101 AC2 用“分别证明”冒充“同时满足” | P1 | **改判 partial** | 没有三合一外部写；不声称满足，也不谎称产品不能表达；剩余验证写清 |
| K `ContractErrors` 套到页面 mux | P2 | **已修** | 公开 mux 只在 `/api/` 前缀套封套；Go 单测固定 `GET /no-such-page` 仍普通 404 |

## A. P0 — 已提交证据泄漏明文凭据

### 发现

- `reports/f12/T101-journeys.json:841`（当时的行号）含一次性测试 API key 明文：
  `"secret":"orchara_gATwXXrqGSnsivZO-TKlZhrsVT-hexcFHkVyO5Upop4"`；
- 同文件 `:316 / :371 / :386` 的请求日志 `detail` 含 `x-connector-secret` 头值
  （`t101-connector-secret-5c2e8a`）；
- `reports/f12/T093-api-conformance.json` 2 处、`T100-journeys.json` 1 处有**形状命中**
  （T093 的两处实际是 `ORCHARA_RUN_EVENT_RETENTION` 环境变量名，不是凭据；T100 的是
  测试库名 `orchara_t100_…`。按复核要求统一走脱敏规则，见下）。
- 违反 `product/AGENTS.md`「凭据不进仓库、日志、报告或测试夹具」和 `API.md` §1「明文仅显示一次」。

### 影响面

- 这是**一次性测试 key**：所属数据库由测试栈创建后已 `drop`，key 行/服务账号/负责人均已销毁，
  该字符串**不可再认证**；它不是生产凭据，也没有出现在任何部署配置里。
- 但“已失效”不等于“可以留在仓库”：它仍是可被扫描器/复制粘贴误用的凭据形状，且违反硬规则。

### 修法（源头脱敏，不是手改字面量）

1. 新增 `product/tools/evidence_redaction.py`（T100/T101 记录器与门禁共用，零依赖）：
   - `orchara_[A-Za-z0-9_\-]{8,}` → `orchara_<redacted>`；
   - 敏感成员名（`secret|token|password|api_key|authorization|cookie` 及 `*_secret/_token/_password/_api_key`
     家族、`x-connector-secret`/`x-api-key`/`set-cookie` 等）的值 → `<redacted>`；
   - 请求头 `x-connector-secret` / `authorization` / `cookie` / `x-api-key` 的值 → `<redacted>`，
     包括 detail 字符串里**转义 JSON** 的形态；`Bearer <token>`、`platform_session=<value>` 也覆盖；
   - `detail` 先按 JSON 解析再递归脱敏（解决转义引号歧义），合法 JSON 之外才退回文本正则。
2. 源头接线：
   - `tests/f12/t101_support.py`：`_short()`、`Journey.record()`、`Journey.persist()` 脱敏；
   - `tests/f12/t100_support.py`：`JourneyLog.records()/write()` 脱敏；
   - `tools/f12_api_gate.py`：`_report()` 返回前 `redact_value(report)`（**不改判据逻辑**）；
   - `tests/f12/journeys/j101_service_accounts.py:105`：`key_response.text` 改为
     `t101_support.redact_value(key_response.json)`，不再把一次性明文当 detail。
3. 三个证据 JSON **重新生成**（不是手写替换）：
   - `T093-api-conformance.json`：`F12_WRITE_EVIDENCE=1 pytest tests/f12/test_operations.py`；
   - `T100-journeys.json`：`F12_WRITE_EVIDENCE=1 pytest tests/f12/test_t100_{account,background,models,publication,worker_exit}.py`；
   - `T101-journeys.json`：`T101_WRITE_EVIDENCE=1 pytest tests/f12/test_t101_journeys.py`。

### 脱敏后证据里实际出现的形状

`T101-journeys.json`（重新生成后）：

- 原 `:841` 位置现在是 `"detail": "{\"id\": \"b591c5c7-…\", \"secret\": \"<redacted>\"}"`；
- 原 `:316/:371/:386` 位置现在是 `"x-connector-secret": "<redacted>"`（header 名保留、值替换）；
- 文件里所有 `orchara_` 长 key 形状为 0（扫描通过）。

`T100-journeys.json` 的 `environment.database` 从 `orchara_t100_<hex>` 变为 `orchara_<redacted>`
（统一规则也覆盖了测试库名；它不是凭据，但这就是“至少覆盖 8+ 字符 orchara_ 形状”的结果）。
`T093-api-conformance.json` 的 `ORCHARA_RUN_EVENT_RETENTION` 仍按原样保留（大写环境变量名不匹配
小写 `orchara_` key 规则；扫描器按大小写敏感匹配真实 key 前缀）。证据文件不再含
`x-connector-secret` 的值、`"secret": "<非 redacted>"`、`orchara_<20+ key 字符>`。

### 红 → 绿证据

- `tests/f12/test_evidence_redaction.py::test_committed_f12_evidence_contains_no_credential_shapes`
  在修复前对旧 `T101-journeys.json` **失败**（8 处命中；对 `git show integration/f12:...T101-journeys.json`
  的复现见 `E:\orcharga-review-red-evidence.log`），修复/重生成后通过：`4 passed in 0.11s`。
- 同文件 `test_the_scanner_detects_the_planted_leak_shapes` 用临时样例串证明扫描规则真的会红
  （api-key / connector-header / credential-member 三条规则都命中），避免“扫描器永远绿”。
- `test_redaction_removes_every_planted_leak_shape` 证明脱敏后三条规则都归零且幂等。

### 历史处理（如实记录，不改写）

`git` 历史里仍保留该已失效字符串（`product/reports/f12/T101-journeys.json` 的旧提交）。
**不建议也不执行 history rewrite**：该分支已推送，issue tracker 引用了提交 SHA；改写会让引用失效，
且泄露的是一次性、所属库已 drop、不可再认证的测试 key。若组织要求清除历史，建议单独走
“仓库历史清理 + 通知所有 clone”的流程，而不是在本次修复分支里 `filter-repo`。

## B. P0 — 旅程状态把 `not_run` 改写成 `passed`

### 发现

- `tests/f12/t101_support.py:137` 默认 `status="not_run"`，`:244-246` 在 `journey()` 正常退出时
  `if status=="not_run": status="passed"`；
- `tests/f12/t100_support.py:215-216` 在 `JourneyHandle.__exit__` 正常退出时无条件 `passed`。
- 违反 `product/AGENTS.md` 硬规则 1（未跑写 `not_run`）与 `TESTING-AND-DATASETS.md` §7
  （空验/跳过/`not_run` 不得计 passed）。

### 修法

> 旧规则的假绿复现（空断言 + 正常退出 → `passed`）见 `E:\orcharga-review-red-evidence.log`。

- 删除两处隐式改写；正常退出不改变状态。
- `Journey.mark_passed()` / `JourneyHandle.mark_passed()`：显式设置 `passed`，且要求
  `len(assertions) > 0`，否则 `AssertionError`；已 `blocked/failed` 的旅程不会被覆盖。
- `Journey.record()/persist()` 与 `JourneyLog.records()/write()` 强制校验：
  `status=="passed"` 而断言为空 → 抛错（不是静默降级）。
- 5 个 T100 测试在 `run_*_journey()` 之后显式 `journey.mark_passed()`；5 个 T101 测试在
  `j101_*.run()` 之后显式 `record.mark_passed()`；J1b 先 `record.block(...)`，`mark_passed`
  不覆盖 blocked。
- 回归测试 `tests/f12/test_journey_status.py`（10 passed）：
  - 空 T101/T100 旅程保持 `not_run`；
  - 无断言的 `mark_passed()` 抛错；
  - 手工把 status 置 `passed` 但无断言，`record()/persist()` 抛错；
  - 有断言 + `mark_passed()` → `passed` 且 `assertion_count>0`；
  - blocked 不被改写；
  - 记录器写出的证据文本已脱敏。

### 真绿复核

- T100 证据运行：`5 passed in 69.56s`；6 条 journey 记录状态
  `T100-J1..J5 passed`（断言 14/10/10/9/13），`GAP-chat-input-as-run-input blocked`。
- T101 证据运行：`5 passed in 74.29s`；6 条 journey 记录
  `J1 passed(16) / J1b blocked(5) / J2 passed(34) / J3 passed(28) / J4 passed(19) / J5 passed(24)`。
- 这些 `passed` 来自旅程体断言后的显式 `mark_passed()`，不是 `__exit__` 的默认值。

## C. P1 — F01 查询参数守卫“假通过”

> 旧实现的假绿复现（双引号 / `searchParams` / 变量 key 都提取到空集合 → PASS）见
> `E:\orcharga-review-red-evidence.log`；新守卫对这些样例全部抛错。

### 发现

- `tests/f01/test_t017_pages.py:39-43`、`test_t019_pages.py:40-44`、`test_t020_pages.py:39-43`
  的守卫只匹配单引号 `URLSearchParams(...).get('x')`，且不再禁止 `location.search`；
  双引号、`new URL(...).searchParams`、变量 key 都会让 `queried` 为空集合 → 通过；
  注释里「unrecognised parameter still fails」是假的。

### 修法

- 守卫集中到 `tests/f01/conftest.py::assert_no_token_in_url_or_storage`，三份测试改为导入；
- `query_parameter_keys()`：
  - 提取 `.get('x')` 与 `.get("x")`（单/双引号）；
  - 提取 `new URLSearchParams("a=b")` 的 query key、`new URLSearchParams({a: …})` 的对象 key；
  - `new URLSearchParams(window.location.search)` / `.searchParams` 视为地址栏读取；
  - **fail closed**：地址栏读取但一个 key 都提取不到（变量 key、`location.hash` 等未知参数）→ 断言失败；
  - 提取到的 key 必须 ⊆ `{error, run, version}`；
  - 保留 `localStorage`/`sessionStorage`、`?token=`、`?csrf` 禁令；
  - `new URLSearchParams()` 后接 `.set(...)`（models 页本地构造分页 query）不算地址栏读取，
    仍允许，避免把“本地构造 query”误判为“读 URL”。
- 新增 `tests/f01/test_page_query_guard.py`（9 passed），用双引号与 `searchParams.get` 样例证明会红：
  `test_double_quoted_get_is_extracted_and_allowed`、
  `test_search_params_get_is_extracted_and_allowed`、
  `test_a_double_quoted_unrecognised_key_fails`、
  `test_a_search_params_style_token_fails`、
  `test_a_key_held_in_a_variable_fails_closed`、
  `test_an_unrecognised_urlsearchparams_argument_fails_closed`。

## D. P1 — F01 用源码字面量冒充行为断言

### 发现

- `tests/f01/test_t019_pages.py:80-84` 断言 served `app.js` 里含 `error.status === 401`、
  `[data-auth-content]`；死常量也能通过。

### 修法

- 删除这三条源码字面量断言；
- 新增 `test_the_served_app_js_navigates_once_and_clears_auth_content`：
  - 用 `pages_server.http.get("/assets/app.js")` 取 **served bytes**，写入临时文件；
  - Node + 最小 `window`/`document`/`fetch` shim（先例 `tests/f12/test_t095_page_roles.py`、
    `tests/f04b/runview.test.mjs`）执行该字节；
  - 构造 401 `session_revoked`，断言：
    (a) 导航到 `/login?error=session_revoked`；
    (b) 两个 `[data-auth-content]` 区域的子节点/`value` 被清空且 `hidden=true`；
    (c) 再走 `handleApiAuthFailure('/api/v1/applications', …)` 与第二次 `handleAuthFailure`
        → 仍然只导航一次；
  - 403 `principal_disabled` 不产生第二次导航（per-page `authFailureHandled`）。
- 结果：`1 passed in 6.41s`（含真实 API 栈启动）。这不是 Playwright/真实浏览器，但执行的是
  served 真实字节，不再是“源码里有这个字符串”。

## E. P2 — 判断类问题

### E1 `nav.js` 重复导出（已修）

`apps/web/src/assets/nav.js:226-237` 同时导出 `items: navItemsFor` 与 `navItemsFor: navItemsFor`；
全库没有 `OrcharaNav.items` 调用点。删除 `items`，保留 `navItemsFor`，`node --check` 通过。

### E2 重复状态/错误分类器（保留，说明理由）

- `runs.js:1364-1396`（`streamFailure` 的 fallback）、`chat.js:437-466`（`describeHandling` 的
  `runStreamHandlingText` fallback）、`runview.js:1304-1309`（`runStreamCancelAcceptedText` fallback）
  在**页面**上确实不可达：`/runs`、`/chat`、`/debug` 都先加载 `runstream.js`。
- 但大量既有 Node 页面测试**单独**加载 `runs.js`/`chat.js`/`runview.js`（如
  `tests/f03/test_t029_pages.py`、`tests/f03/test_t032_pages.py`、`tests/f04b/runview*.test.mjs`），
  删除 fallback 会让这些测试的隔离 harness 直接 `ReferenceError`。复核要求“不要为了删而破坏行为”，
  因此**保留**并在本报告说明：浏览器分支不可达，隔离测试分支可达。
- 若要清理，建议先统一页面 harness 都加载 `runstream.js`，再单独一票删除；不在本次修复中做。

### E3 真实栈 fixture 四处复制（不修，记录）

`tests/f12/conftest.py`、`t095_support.py`、`t100_support.py`、`t101_support.py` 各自复制了
“迁移 + bootstrap + mTLS + 签名密钥 + 对象目录 + API 启动”的真实栈构造。它们有意的差异
（T093 门禁会先跑 gate；T100 模块级栈；T101 每测试独立栈）使合并成一处需要参数化所有生命周期，
**重构风险大于收益**；记为已知重复，不在本票动。

### E4 门禁 60/106 与源码正则（记录为已声明边界）

原门禁只有 60/106 个 operation 有 normal-path 探针，`implemented` 依赖
`scan_route_registry()` 的源码正则。G 已把“route 存在”与“functional 验证”分开，
T093 报告 §5.6 明确写了四条边界：功能覆盖 60/106、源码正则权威、夹具有限、跨主体 18/31。
本票没有为了凑 106 而给剩余 operation 补假探针，也没有把源码扫描改成更脆弱的运行时推断。

## G. P0 — T093 的「106/106 implemented」有一部分是空的

### 发现

- 46/106 个 operation 的 `normal_path` 是 `null`；
- `implemented = route_registered and runtime_reached`，而 `runtime_reached` 只表示“匿名探针拿到的
  不是 mux 的纯 404/405”；`getVersion` 因此被标 `implemented: true`，证据只有
  `{"probe":"anonymous","status":401}`；
- 变异测试只验证“未注册路径被判 missing”，没有验证“已注册但不可用”。

### 修法（只改门禁判据的输出分类，不改产品/契约）

- `_operation_record()` 新增 `runtime_reached`、`route_only`；`implemented` 初始为 `False`；
- `_finalize_implementation()` 在 normal probes 之后：
  - `functional_probe` = 该 operation 自己的 normal-path（必须有 `ok` 且 status ∈ 契约成功状态）；
  - `implementation_status` ∈ `functional | route_only | probe_failed | missing`；
  - `implemented` 只对 `functional` 为 true；
  - `verdict` ∈ `pass | route_only | fail`。
- summary 新增 `route_registered`、`functional_probe`、`functional_coverage`、
  `route_only`/`route_only_operations`、`probe_failed`/`probe_failed_operations`；
  `implemented` = functional 数；verdict 在无失败但覆盖不完整时为 `route_only`；CLI 退出码 3。
- 报告 `reports/f12/T093-api-conformance.md` 已改：
  - 顶部从“passed 106/106”改为“判定手段 passed；完整首版门禁裁决 route_only；
    route-registered 106/106、functional 60/106、route_only 46”；
  - AC 表、§2、§5.1 按 epic 表、§5.2 计数、§5.5 route_only 清单、§5.6 已声明边界、
    §7.2/§7.3 的 summary/tail/退出码全部更新；
  - 不再说“每条记录都有 403/404”（见 H）。
- 新增变异测试：
  `test_a_registered_but_always_401_operation_is_not_counted_as_implemented`
  用 `getVersion` 证明“已注册 + 匿名 401”必须 `route_only`，`implemented=False`，
  `functional_probe.checked=False`；现有注入未注册 operation 的变异仍 `missing` + `verdict=failed`。

### 结果

重新生成证据后：`route_registered=106`、`functional_probe=60`、`route_only=46`、
`probe_failed=0`、`missing=[]`、`verdict=route_only`。T093 报告 §5.5 按名单列出 46 条，
不再用“路由存在”冒充实现。

### 未补的 46 条（理由）

它们需要发布版本、审批、评测报告、后台 job、ingress 文档等夹具，或会改变现有 normal 探针顺序；
本票不扩大改写门禁。按复核要求如实计 `route_only` 并按 epic 列出，而不是强凑 106 个假探针。

## H. P0 — 授权记录未验跨主体内容隔离

### 发现

- 对无路径参数的自助 list，`wrong_principal` 期望 200，`authorization_ok = all(probe.ok)`；
- `listRuns`/`listApplications`/… 的 wrong_principal 只记 `200 ok=True`，从未断言正文里没有
  另一个主体的资源；
- 报告却说“每条都有 403/404”。

### 修法

- 新 pass `_run_cross_principal_isolation()` 在 fixtures 构建后、normal probes 删除资源前运行；
- 18 个 list/get operation 真实调用 plain member：
  - list：断言 creator 的 fixture id 不在 `items[].id` 也不在正文；记录 `member_status`、
    `items_returned`、`foreign_id_visible`、`isolation_ok`；
  - detail：断言 member 得到 403/404 且正文不含 creator id；记录同样的字段；
  - 列表：`listApplications`、`listRuns`、`listConversations`、`listMemories`、`listKnowledgeBases`；
  - 详情：`getApplication`、`getDraft`、`listVersions`、`getRun`、`listRunArtifacts`、
    `getConversation`、`listMessages`、`getMemory`、`getKnowledgeBase`、`listKnowledgeDocuments`、
    `getArtifact`、`readArtifactContent`、`downloadArtifact`。
- 13 条无夹具读操作写 `not_run` + 原因（`getVersion`、`getTool`、`getApproval`、`getRequest`、
  `getPublicationReview`、`getEvaluation`、`getEvaluationReport`、`getKnowledgeDocument`、
  `getBackgroundJob`、`getDeletion`、`listApprovals`、`listMemoryCandidates`、`runEvents`）。
- summary 新增 `cross_principal_isolation: {checked, checked_operations, not_run, failures}`；
  失败进入 verdict。
- `tests/f12/test_operations.py` 新增 106 个 parametrized 断言：
  每个 operation 都有 `cross_principal_isolation` 字段；applicable+checked 必须
  `isolation_ok=True`；applicable+unchecked 必须有 `reason`；summary 至少 18 条 checked 且 0 失败。
- `tests/f12/test_cross_principal.py` 补了 `listKnowledgeBases` 的独立真实断言。
- T093 报告 §5.5/§5.6 按新口径说明：无路径参数的自助 list 的 wrong_principal 是 200，
  隔离由内容断言证明，不再说“每条都有 403/404”。

### 结果

`cross_principal_isolation.checked=18`、`not_run=13`、`failures=[]`；每条记录都有
member 状态/items 数/foreign_id_visible/isolation_ok。

## I. P1 — T098 报告乱码

### 发现

`reports/f12/T098-mobile-and-personal-data.md` 是 BOM + UTF-8→GBK 解码后的文本：276 个私用区字符，
`gbk`/`gb18030`/`cp1252`/`latin-1` 反解均不可逆（原文丢失）。

### 修法

- 按现有代码与测试重写报告（118 行，UTF-8 无 BOM，PUA=0，U+FFFD=0）：
  - 顶部写明“复核后重写，原文件编码损坏不可恢复，结论依据当前代码/测试”；
  - 逐 AC 对照（4 条）、真实命令 + 原始 tail（`14 passed in 28.38s`）、可验证/不可验证分栏；
  - 引用具体文件行号（`mobile.css`、六个页面、14 个测试）；
  - 未修项归到 F07/F09/F11/F14。
- 新增 `tests/f12/test_report_encoding.py`：扫描 `reports/f12/**/*.{md,json}`，BOM / U+FFFD / PUA
  任一出现即失败。修复前对旧 T098 报告为红，重写后为绿。
- 全目录顺带发现两处**其它票**的历史编码问题（未修改，合并协议禁止从本票改其它票的 reports/）：
  `reports/f13a/T065-progress-and-failure-classes.md` 开头 BOM；
  `reports/f15a/runs.json` 含 U+FFFD。已在 §I 记录，建议对应票自行修复。

## J. P1 — T101 AC2「scope / ACL / 人类批准同时满足」被替代

### 发现

- J5 是**读**操作：scope + read grant + 负责人启用，没有审批；
- J2 有**人类审批**，但走 owner 浏览器会话，没有 API-key scope；
- 没有任何一个外部写同时满足三者，原报告却称“满足”。

### 修法（按可行性二选一，选 2 并如实）

- T101 状态从 `passed` 改为 **`partial`**；AC2 结论改为 **partial（未满足）**；
- `j101_service_accounts.py` 新增 `record.note("ac2", {...})`，写清：
  - 已分别证明的三件事；
  - **未证明**“同一外部写同时满足三者”；
  - **不声称是产品缺口**：`internal/approvals/service.go:187-207` 的 `approverForOwnerTx`
    会把 service-owned Run 的审批人解析为 `responsible_user_id`，`/api/v1/requests` 与
    `createConversation` 的契约 security 允许 `apiKey`，`internal/api/requests.go` 只要求
    `application:read/write` scope——产品代码路径看起来支持；
  - 剩余 `not_run`：发布一个含 approval-gated write 节点的版本，用带 scope+grant 的服务 key 提交，
    负责人批准，断言只发生一次物理写。
- T101 报告 §2/§5 重写；`REVIEW-FIXES.md`（本文件）同步；PROGRESS T101 小节改判。

### 最终判定

**T101 AC2 = `partial`（未满足）**。这不是 F12 范围外的新产品缺口，也不是 F06 的已知限制；
它是本票尚未构造的三合一 E2E 覆盖缺口。

## K. P2 — `ContractErrors` 越界到页面 mux

### 发现

`cmd/api/main.go:221` 把 `ContractErrors` 套在公开 mux 上，页面 404/405 从纯文本变成 JSON 封套；
#94 的范围是 REST/SSE，页面不是契约面。

### 修法

- `internal/api/errors.go` 新增 `ContractErrorsFor(prefixes, next)`：只有路径前缀命中才套封套；
- `cmd/api/main.go` 公开 mux 改为 `ContractErrorsFor([]string{"/api/"}, mux)`；内部 mux 保持
  `ContractErrors`（内部监听器全是契约/健康路由）；
- `internal/api/errors_test.go` 新增两条 Go 测试：
  `TestContractErrorsForOnlyWrapsAPIPaths`（页面 404 仍 `text/plain`，`/api/v1/no-such` 是
  `{error:{code,message,request_id}}` 封套）、`TestContractErrorsForDoesNotRewritePageMethodMismatch`
  （页面 405 仍是普通 405）。

### 结果

Go 测试通过；T093 的 error_envelope 不变量仍只对 `/api/` 路径生效，页面行为恢复原状。

## 全量门禁（本次复核修复的真实 tail）

> 以下 tail 来自 `E:\orcharga-review-*.log`；最终合并后会在同一棵树上复跑。

### tests/f12

```text
........................................................................ [ 16%]
........................................................................ [ 32%]
........................................................................ [ 49%]
........................................................................ [ 65%]
........................................................................ [ 81%]
........................................................................ [ 98%]
........                                                                 [100%]
441 passed in 232.18s (0:03:52)
```

（基线 `integration/f12` 为 318 passed；本次新增 T093 分类/隔离 107 条、状态 10 条、脱敏 4 条、
编码 1 条，并保留既有全部用例。）

### tests/f01

`165 passed in 754.53s (0:12:34)`（基线 155 passed；本次修复新增 `test_page_query_guard.py` 9 条与
`test_the_served_app_js_navigates_once_and_clears_auth_content` 1 条）。

### Go / 静态

```text
BUILD=0
VET=0
TEST=0
gofmt -l internal cmd tools  → 空（无输出）
no drift: spec copy and contract snapshot match the manifest
```

## 最终门禁（合并前，同一棵工作树）

```text
python -m pytest tests/f12 -q
........................................................................ [ 16%]
........................................................................ [ 32%]
........................................................................ [ 48%]
........................................................................ [ 65%]
........................................................................ [ 81%]
........................................................................ [ 97%]
.........                                                                [100%]
441 passed in 232.18s (0:03:52)
```

```text
python -m pytest tests/f01 -q
........................................................................ [ 43%]
........................................................................ [ 87%]
.....................                                                    [100%]
165 passed in 754.53s (0:12:34)
```

```text
go build ./...        → BUILD=0
go vet ./...          → VET=0
go test ./...         → TEST=0（全部 ok / no test files，无 FAIL）
gofmt -l internal cmd tools → 空
python tools/sync_spec.py --check → no drift: spec copy and contract snapshot match the manifest
node --check apps/web/src/assets/nav.js → exit 0
```

证据运行：

```text
F12_WRITE_EVIDENCE=1 pytest tests/f12/test_operations.py -q       → 319 passed in 26.44s
F12_WRITE_EVIDENCE=1 pytest tests/f12/test_t100_{...}.py -q       → 5 passed in 69.56s
T101_WRITE_EVIDENCE=1 pytest tests/f12/test_t101_journeys.py -q   → 5 passed in 72.18s
pytest tests/f12/test_t098_mobile_pages.py tests/f12/test_t098_stack.py -q → 14 passed in 28.38s
```

## 残余风险

1. **历史仍含失效 key 字符串**：见 A；不改写历史是有意选择。
2. **T093 functional 覆盖 60/106**：46 条 route_only 已声明；不把它们当通过。
3. **跨主体隔离 18/31**：13 条读操作无第二个主体的资源夹具，明确 `not_run`。
4. **T101 AC2 partial**：三合一外部写未构造；不声称产品缺口。
5. **E2 fallback 保留**：浏览器不可达但隔离 Node 测试可达；删除需先统一 harness。
6. **T098 像素级移动端渲染 not_run**：Node+shim 只证明脚本/结构，不证明真实布局/触控。
7. **其它票报告编码**：`reports/f13a/T065...md` 的 BOM、`reports/f15a/runs.json` 的 U+FFFD
   已记录未修，避免越界。
