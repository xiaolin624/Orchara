# F12 第二轮复核修复报告（页面发现 F1–F4）

- 批次：F12 第二轮独立复核（Standards + Spec）的**页面**发现修复
- 被复核范围：`1cdff9c...e3aa1e1`（审查者的页面发现）
- 修复提交（由另一实现者提交）：`83307b1 fix(f12): close the second review's page findings`
- 本轮分支：`t/f12-review2`；worktree `E:\Orchara-wt\review2`；集成基线 `integration/f12` @ `e3aa1e1`
- 本轮改动：`product/apps/web/src/assets/{runs,debug,personal-data}.js`、
  `product/tests/f12/test_t096_review2.py`、`product/tests/f12/test_t098_review2.py`、
  `product/tests/f11/test_t073_pages.py`（旧断言随已修行为更新）、`product/reports/f12/**`、`product/PROGRESS.md`
- **未改** `product/docs/spec/**`、`product/contracts/**`、迁移；没有回退 `83307b1` 的任何修复
- 真实栈串行锁：`E:\Orchara\.scratch\f12\locks\realstack` 由本工作树持有至收尾删除
- 工具版本：Python 3.12.2 / pytest 9.0.3 / Node v24.13.0 / Go 1.27.3

## 0. 手段声明（硬性，先读）

- **无 Playwright / 无真实浏览器 DOM。** 本报告的四条页面证据全部来自 Node 执行
  `apps/web/src/assets/` 里**已提交的真实字节**（`runs.js`、`debug.js`、`personal-data.js`，
  以及 `debug.html`/`runs.html` 的真实 id 集合），DOM/fetch 只是最小手写 shim。它证明真实脚本
  字节、真实请求路径/请求头、真实渲染文本与真实模块状态；**不证明**浏览器事件循环、真实
  `EventSource`、DOM 布局、表单控件的浏览器内语义或真实网卡断线。
- **红证据来自独立 detached worktree。** 在 `e3aa1e1`（修复提交的父提交）上新建
  `E:\Orchara-wt\review2-red`，只把本轮新增的两个测试文件复制进去（**不改任何源码**），
  5 条用例全红；另把 `83307b1` 的 `debug.js` 单独放进该 worktree，复现本轮追加修正的
  `ReferenceError`。红证据 worktree 收尾已 `git worktree remove --force` 删除。
- **这 5 条页面用例不启动真实栈**（不连 PostgreSQL/API）；真实栈只在 §5 全量门禁中使用。
- **不是真实厂商模型**：门禁使用 `ORCHARA_PROFILE=dev-stub`；本报告不涉及模型调用。

## 0.1 结论总表

| 发现 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F1 诊断授权空选择 = 授权全部对象 | Standards P1 | **已修 + 回归测试** | 空选择在请求构造处 `throw` 且页面 `#grant-status` 显示原因；非空选择总是发送等于选择的 scope 头 |
| F1 追加：页面勾选后无法提交 | （测试发现的旧缺陷） | **已修** | 复选框值原为 `artifact:<uuid>`，构造器期望裸 uuid；改为携带 artifact id，页面单选/多选都真实产生 POST |
| F2 重建中/失败资料被说成不可检索 | Standards P2 | **已修 + 回归测试** | `indexing`/`failed` 均说明“旧的仍可能是检索来源”并区分“首次”；删掉两句原假话 |
| F3 调试页 410 后无法重建快照 | Spec P2 | **已修 + 回归测试** | 410 重建改由调用页提供 `renderSnapshot`；无 renderer 且无 `/runs` 锚点时明确报错、不取快照、不抛异常；`debug.js` 传自己的 renderer |
| F3 追加：`debug.js` renderer 引用未定义变量 | （测试发现的修复缺陷） | **已修** | `draftRevision` 未定义 → renderer 真正执行时 `ReferenceError`；改为页面已有的 `frozenRevision` |
| F4 迟到快照覆盖新选择 | Spec P2 | **已修 + 回归测试** | await 之后按 generation 判定丢弃迟到快照：不渲染、不追加时间线、不覆盖 `displayedRun(ID)` |
| F11/T073 旧断言 | 受影响既有测试 | **已更新** | 旧测试断言“空选择省略头”（正是本轮要消除的行为），改为断言抛错 |

## 1. F1（Standards P1）：诊断授权空选择会扩大为整个 Run

### 出处

- 服务端兼容规则：`product/internal/diagnostics/grants.go:8-10` 明确写着非契约头
  `X-Diagnostic-Object-Scope` **缺失时推导该 Run 的当前全部对象**；`:395-423`
  （`resolveObjectScope` 的 `len(requested) == 0` 分支）用 SQL 选出该 Run 关联的**所有**
  artifacts 并写入授权快照。
- 规格依据：`product/docs/spec/UX.md:35`（诊断授权要选对象范围）、
  `product/docs/spec/DATA-POLICY.md:7`（读取正文需用户显式授权）。
- 旧页面（`e3aa1e1`）：`apps/web/src/assets/personal-data.js:359-363` 在 `objects.length === 0`
  时返回 `headers: {}`；页面提交函数 `:785-810` 把这个空头 POST 出去。
- 即“默认不勾选”实际等于“授权该 Run 的全部当前对象”，与 UI 的暗示相反。

### 复现

在 `e3aa1e1` worktree 上执行本轮新增用例：

```text
FAILED tests/f12/test_t098_review2.py::test_an_empty_diagnostic_grant_selection_is_refused_and_never_sent
```

Node 报告中的关键字段（真实执行）：

```json
{"empty_helper_threw":false,"empty_helper_error":null,
 "empty_api_calls":[{"path":"/api/v1/diagnostic-grants","options":{...}},
                    {"path":"/api/v1/diagnostic-grants","options":{}}],
 "empty_csrf_calls":1}
```

即：空选择没有抛错，页面还真的取了 CSRF 并发出（随后列表）请求。

### 修法（`83307b1`）

- `apps/web/src/assets/personal-data.js:367-378`：`objects.length === 0` 时
  `throw new Error('请至少勾选一个诊断对象：…全部对象纳入授权。')`；
  非空时**无条件**设置 `X-Diagnostic-Object-Scope = diagnosticObjectScopeHeader(objects)`。
- 页面已有 try/catch 把该错误写进 `#grant-status`（`personal-data.js:807-809`），不会静默。

### 本轮追加修正（测试发现的旧缺陷）

页面的复选框原值来自 `diagnosticObjectEntry(item.id)`（`artifact:<uuid>`），但
`diagnosticGrantCreateRequest` 会对每个 entry 再调用一次 `diagnosticObjectEntry`（期望裸 uuid），
因此**只要用户勾选任何对象，页面就显示 “artifact id 必须是 UUID” 且不发请求**。
证据（`83307b1` 的 `personal-data.js`，其余源码同）：Node 报告
`single_page_status: "artifact id 必须是 UUID"`、`single_page_posts: []`、`csrf_calls: 0`
（红日志 `E:\Orchara\.scratch\f12\review2-scratch\red-f1-userfix2.log`）。
修法：`personal-data.js:716` 的复选框值改为 `String(item.id || '')`（可见 label 仍显示
`artifact:<uuid>`）。这不是回退 `83307b1`，而是让 F1 的“请求头等于选择”在真实页面路径上成立。

### 红→绿测试

- 测试：`tests/f12/test_t098_review2.py::test_an_empty_diagnostic_grant_selection_is_refused_and_never_sent`
- 覆盖：① 空选择 → 抛错且无 fetch/api/csrf；② 单选/多选 → 请求头存在、等于选择、payload 路径正确；
  ③ 页面提交路径把原因显示到 `#grant-status`；④ 勾选后页面真实产生 POST（含两个对象的 header）。
- 红：在 `e3aa1e1` worktree → `FAILED`（上面的字段）。
- 绿（当前 HEAD）：

```text
tests/f12/test_t098_review2.py::test_an_empty_diagnostic_grant_selection_is_refused_and_never_sent PASSED [ 80%]
```

真实断言值（当前 HEAD 的 Node 报告）：

```json
{"empty_helper_threw": true,
 "empty_helper_error": "请至少勾选一个诊断对象：不勾选时服务端会按兼容规则把该运行的全部对象纳入授权。",
 "single_header": "artifact:33333333-3333-4333-8333-333333333333",
 "multi_header": "artifact:33333333-3333-4333-8333-333333333333,artifact:44444444-4444-4444-8444-444444444444",
 "empty_api_calls": [], "empty_fetch_calls": [], "empty_csrf_calls": 0,
 "single_page_posts": [{"path": "/api/v1/diagnostic-grants",
                        "headers": {"X-Diagnostic-Object-Scope": "artifact:33333333-..."},
                        "body": {"run_id": "11111111-...", "grantee_id": "22222222-...",
                                 "purposes": ["troubleshooting"], "expires_at": "2098-12-31T16:00:00.000Z"}}]}
```

## 2. F2（Standards P2）：重建中/失败的资料被错误描述为不可检索

### 出处

- `product/docs/adr/0018-f10-index-revisions.md:26`（“重建进行中/失败但旧索引仍可读”）、
  `:58-59`（旧索引在被取代前保持 `ready` 且可读；失败的重建保留旧活动索引）。
- 旧文案（`e3aa1e1`）：`apps/web/src/assets/personal-data.js:183`
  “索引正在构建：**完成前检索为空**，不是‘没有资料’的最终结论。”；`:189`
  “摄取失败：文档不会被标记为可检索，**也不会返回旧正文**。”
- 两句都否认了“旧活动索引仍可读”这一 ADR 事实。

### 复现

在 `e3aa1e1` 上执行新用例：

```text
FAILED tests/f12/test_t098_review2.py::test_rebuild_and_failure_notes_keep_the_previous_index_readable
E  AssertionError: ('indexing', '索引正在构建：完成前检索为空，不是“没有资料”的最终结论。')
E  assert '完成前检索为空' not in '索引正在构建：完成前检...“没有资料”的最终结论。'
```

### 修法

`personal-data.js:185`（`indexing`）与 `:194`（`failed`）改为：

- `indexing`：“索引正在构建：若该资料此前已就绪，旧的活动索引在切换前仍是检索来源；
  首次索引在完成前没有可检索内容（没有旧索引可回退）。”
- `failed`：“摄取失败：新索引未激活。若该资料此前已有已就绪索引，旧的仍在检索来源中
  （失败不会清空它）；首次索引失败且从未就绪的资料没有可检索内容。”

两句都提到 `旧` 索引与 `首次` 索引，且不再出现 `完成前检索为空` / `不会返回旧正文` 字面。
**注意**：`83307b1` 的原文案是“首次索引完成前检索为空”，语义上已限定“首次”，但字面仍含
复核要求消除的短语 `完成前检索为空`；本轮按要求改写（见 §6 异议 1）。

### 红→绿测试

- 测试：`tests/f12/test_t098_review2.py::test_rebuild_and_failure_notes_keep_the_previous_index_readable`
- 断言：Node 执行真实字节得到 7 个状态的 `documentReadout().note`；`indexing`/`failed` 不含
  `完成前检索为空`、`不会返回旧正文`，且都含 `旧`、`索引`、`首次`；其它状态文案不回归。
- 红：`e3aa1e1` → 见上。
- 绿：

```text
tests/f12/test_t098_review2.py::test_rebuild_and_failure_notes_keep_the_previous_index_readable PASSED [100%]
```

## 3. F3（Spec P2）：调试页收到 410 后无法重建快照

### 出处

- 旧 `apps/web/src/assets/runs.js:2026`（`e3aa1e1`）：410 分支读取快照后**固定**调用
  `showRunDetail(result.payload, facts)`；该函数从 `:1547` 起无空值保护地写
  `#detail-id`、`#detail-state` 等锚点。
- `apps/web/src/debug.html`（真实字节）**没有** `detail-id` 等 `/runs` 详情锚点（其时间线
  锚点是 `run-timeline` / `run-timeline-status`），所以调试页读完 410 快照后仍抛
  `TypeError: Cannot set properties of null`。
- 旧 `runs.js:1993` 的签名没有 renderer 选项，调用页无法接管视图。

### 复现

在没有 `#detail-id` 的 DOM（`debug.html` 形状）上驱动真实 `runs.js` 的 410 分支：

```text
FAILED tests/f12/test_t096_review2.py::test_a_410_without_detail_anchors_does_not_throw_and_uses_the_renderer
E  AssertionError: {'no_renderer': {'status': "事件流读取失败：Cannot set properties of null (setting 'textContent')" ...},
                     'with_renderer': {'rendered': [], ...}}
```

即：异常被 `beginRunTimeline` 末尾的 `.catch` 吞成一条“事件流读取失败”，页面没有任何可用重建。

### 修法（`83307b1` + 本轮一处修正）

- `runs.js:1994-1999` 新增 `pageHasRunDetailAnchors()`（真实查 `#detail-id`）。
- `runs.js:2001-2029`：`rebuildRunTimelineFromSnapshot(..., options)` 接受
  `options.renderSnapshot`；没有 renderer 且页面无 `#detail-id` 时写明确状态并返回 null，
  **此时不读取快照**（`renderSnapshot === null` 的检查在 `apiFull` 之前）。
- `runs.js:2124-2136`：`beginRunTimeline` 把 `elements.renderSnapshot` 与 generation 判定
  `isCancelled` 传给重建函数。
- `debug.js:414-428`：调试页传入自己的 `renderSnapshot`（用快照 `beginRunView` 重建节点视图并写状态）。

### 本轮追加修正：renderer 引用了未定义的 `draftRevision`

`83307b1` 的 `debug.js:419` 写 `draftRevision: draftRevision`，但 `initDebug()` 作用域里只有
`frozenRevision`，没有 `draftRevision` 绑定。静态 `node --check` 不报错，但 renderer **真正执行**
时必抛 `ReferenceError`（也就是说调试页的 410 仍不可用）。证据：把 `83307b1` 的 `debug.js` 放进
`e3aa1e1` worktree 后执行本用例：

```text
FAILED tests/f12/test_t096_review2.py::test_the_debug_page_passes_its_own_snapshot_renderer_to_the_timeline
E  AssertionError: ... 
E  assert 'ReferenceError: draftRevision is not defined\n    at Object.renderSnapshot (debug.js:419:32)' is None
```

修法：`debug.js:422` 改为 `draftRevision: frozenRevision`（提交前已由 `frozenRevision = payload.draft_revision`
固定，见 `debug.js:383`）。

### 红→绿测试

1. `tests/f12/test_t096_review2.py::test_a_410_without_detail_anchors_does_not_throw_and_uses_the_renderer`
   - 无 `#detail-id`；无 renderer → 状态含“没有 Run 快照渲染函数/不重建”，`api_full_calls == []`，
     时间线为空，无异常。
   - 同一 DOM + 传入 renderer → renderer 被调用一次、快照路径正确、时间线追加保留窗口说明、
     `dataset.snapshotRebuilt == "true"`。
   - 红：`e3aa1e1` 上 `TypeError`（见上）；绿：PASSED。
2. `tests/f12/test_t096_review2.py::test_the_debug_page_passes_its_own_snapshot_renderer_to_the_timeline`
   - 按 `debug.html` 真实 id 集合搭 DOM，执行**真实 `debug.js` 字节**，走真实提交路径触发
     `beginRunTimeline`；断言调用参数含 `renderSnapshot`，并**实际执行**该 renderer：
     无异常、返回快照 Run、`beginRunView` 被再次调用且 `draftRevision=3`、状态含 410/快照。
   - 红：`e3aa1e1` 上无 `renderSnapshot`；`83307b1` 上 `ReferenceError`；绿：PASSED。

真实绿 tail：

```text
tests/f12/test_t096_review2.py::test_a_410_without_detail_anchors_does_not_throw_and_uses_the_renderer PASSED [ 20%]
tests/f12/test_t096_review2.py::test_the_debug_page_passes_its_own_snapshot_renderer_to_the_timeline PASSED [ 40%]
```

## 4. F4（Spec P2）：迟到的快照覆盖新选择

### 出处

- 旧 `apps/web/src/assets/runs.js:2012`（`e3aa1e1`）：`await apiFull(snapshotPath)` 之后没有任何
  校验；`:2026` 直接 `showRunDetail` 并往 `list` 追加重建行。
- 审查者复现：等 A 快照时切到 B → A 返回后 `selected=B / displayed=A`。

### 复现（本轮 harness 与旧代码输出）

在 `runs.html` 的真实锚点上驱动真实 `runs.js`：`loadRunDetail(A)` 的 410 快照请求保持挂起 →
`loadRunDetail(B)` 完成（B 的 detail + B 的 410 重建）→ 再解析 A 的迟到快照。

`e3aa1e1` 红：

```text
FAILED tests/f12/test_t096_review2.py::test_a_late_410_snapshot_never_overwrites_the_newer_run_selection
E  AssertionError: {... 'detail_id': 'aaaaaaaa-...', 'detail_phase': 'executing',
E                    'probe_run_id': 'aaaaaaaa-...', ...}
```

即 late A 把 `#detail-id`、`displayedRun` 都覆盖成 A，并追加了第二行时间线。

### 修法

`runs.js:2047-2051`：在 `await apiFull` 之后、任何渲染/追加/状态写入之前检查
`options.isCancelled`；过期快照直接返回 null。`isCancelled` 来自 `beginRunTimeline` 内部的
generation 闭包（`runs.js:2086`），第二次 `beginRunTimeline` 必然 +1。

### 红→绿测试

- 测试：`tests/f12/test_t096_review2.py::test_a_late_410_snapshot_never_overwrites_the_newer_run_selection`
- 断言（迟到 A 解析后）：`displayedRunID`/`displayedRun.id`/`#detail-id` 仍是 B；`#detail-phase`
  仍是 B 的 `finalizing`；时间线仍只有 B 的一行且文本不变；状态文本不变且含 B 的快照路径；
  迟到 A **没有调用 renderer**（`renders_after == []`）。
- 红：`e3aa1e1` → 上面的 AssertionError；绿：

```text
tests/f12/test_t096_review2.py::test_a_late_410_snapshot_never_overwrites_the_newer_run_selection PASSED [ 60%]
```

## 5. 红→绿总证据（真实命令与 tail）

### 5.1 红：修复回退后 5 条全失败（独立 worktree，源码未改）

```powershell
cd E:\Orchara-wt\review2
git worktree add --detach E:\Orchara-wt\review2-red e3aa1e1
Copy-Item product\tests\f12\test_t096_review2.py E:\Orchara-wt\review2-red\product\tests\f12\
Copy-Item product\tests\f12\test_t098_review2.py E:\Orchara-wt\review2-red\product\tests\f12\
cd E:\Orchara-wt\review2-red\product
python -m pytest tests/f12/test_t096_review2.py tests/f12/test_t098_review2.py -q
```

真实 tail：

```text
FAILED tests/f12/test_t096_review2.py::test_a_410_without_detail_anchors_does_not_throw_and_uses_the_renderer
FAILED tests/f12/test_t096_review2.py::test_the_debug_page_passes_its_own_snapshot_renderer_to_the_timeline
FAILED tests/f12/test_t096_review2.py::test_a_late_410_snapshot_never_overwrites_the_newer_run_selection
FAILED tests/f12/test_t098_review2.py::test_an_empty_diagnostic_grant_selection_is_refused_and_never_sent
FAILED tests/f12/test_t098_review2.py::test_rebuild_and_failure_notes_keep_the_previous_index_readable
5 failed in 1.38s
```

红 worktree 已删除（`git worktree remove --force E:\Orchara-wt\review2-red`）。

### 5.2 绿：当前 HEAD

```powershell
cd E:\Orchara-wt\review2\product
python -m pytest tests/f12/test_t096_review2.py tests/f12/test_t098_review2.py -v
```

真实 tail：

```text
tests/f12/test_t096_review2.py::test_a_410_without_detail_anchors_does_not_throw_and_uses_the_renderer PASSED [ 20%]
tests/f12/test_t096_review2.py::test_the_debug_page_passes_its_own_snapshot_renderer_to_the_timeline PASSED [ 40%]
tests/f12/test_t096_review2.py::test_a_late_410_snapshot_never_overwrites_the_newer_run_selection PASSED [ 60%]
tests/f12/test_t098_review2.py::test_an_empty_diagnostic_grant_selection_is_refused_and_never_sent PASSED [ 80%]
tests/f12/test_t098_review2.py::test_rebuild_and_failure_notes_keep_the_previous_index_readable PASSED [100%]
5 passed in 0.79s
```

### 5.3 受影响的既有测试

```powershell
python -m pytest tests/f11/test_t073_pages.py::test_the_diagnostic_grant_helpers_execute_under_node `
  tests/f12/test_t098_mobile_pages.py tests/f12/test_t096_pages.py -q
# 19 passed in 1.82s
```

`tests/f11/test_t073_pages.py:165-171` 原本断言“空选择省略头”（正是 F1 要消除的行为），已改成
断言抛错；同一处 `subprocess.run` 补 `encoding="utf-8", errors="replace"`（否则 Node 的中文
断言信息在 Windows 默认 GBK 解码下会变成 `stderr=None` 的二次错误）。

## 6. 对既有修复的异议/调整（逐条带证据）

1. **F1 文案仍含目标短语（已调整）。** `83307b1` 的 `indexing` note 是
   “首次索引完成前检索为空”：语义已限定“首次”，但字面仍含复核要求消除的 `完成前检索为空`。
   本轮改为“首次索引在完成前没有可检索内容”，既保留“首次才为空”的事实，也通过字面回归。
   证据：`e3aa1e1` 原文案与 `83307b1` 文案对比；新用例在两者上的红/绿。
2. **F1 页面选择路径仍不可用（已追加修正）。** 复选框 `box.value = entry`（`artifact:<uuid>`）
   与 `diagnosticGrantCreateRequest` 的裸 uuid 契约冲突；`83307b1` 上用户勾选后
   `#grant-status = "artifact id 必须是 UUID"`、0 POST。这是 F11/T073 起就存在的旧缺陷，
   不是 `83307b1` 引入的；但它使 F1 的“非空选择”无法端到端成立，故修 `personal-data.js:716`。
   证据：红日志 `red-f1-userfix2.log`（真实 Node 报告）。
3. **F3 renderer 变量名错（已追加修正）。** `debug.js:419` 的 `draftRevision` 在页面作用域不存在；
   `node --check` 通过但 renderer 一执行就 `ReferenceError`。改 `frozenRevision`（证据见 §3）。
4. **F11/T073 旧测试（已更新）。** 旧断言是“空选择 → 无头”，与本轮安全结论相反；
   已改为 `assert.throws(/至少勾选一个诊断对象/)`，并给该测试的 Node 子进程补 UTF-8 解码。
5. **F4 未发现异议。** generation 判定位置正确（await 之后、副作用之前），且原有
   `pollRunDetail` 的 `displayedRunID` 守卫继续覆盖轮询路径。

## 7. 残余风险与 not_run（不掩盖）

- **不是浏览器**：没有 Playwright/真实 DOM；CSS、真实 `EventSource`、浏览器表单控件语义、
  事件冒泡与真实网络断线均 `not_run`。本报告只证明 committed bytes + shim 下的行为。
- **410 harness 用 fetch 410 响应**模拟事件流过期；真实服务器 410 的端到端页面证据仍由
  `tests/f12/test_t096_stack.py::test_real_410_drives_the_page_snapshot_rebuild` 提供（未改）。
- **F1 的页面路径测试不连真实 API**：它断言真实请求形状与“无请求”；真实服务端对
  `X-Diagnostic-Object-Scope` 的解析/存储由 F11/T073 的既有真实栈用例覆盖（未改）。
- **`#detail-id` 的有无由真实 `debug.html`/`runs.html` 字节驱动**，但 harness 的 DOM 仍是手写 shim；
  若未来页面重命名锚点，静态 id 提取会随之变化，测试会失败而不是静默通过。
- **f12 全量**包含真实栈用例（PostgreSQL/mTLS/对象目录）；本轮门禁真实 tail 见 §8。
- 本报告未重跑 `tests/f11` 全量；只重跑受影响的 T073 Node 用例。跨 epic 的既有红（f03/f02a/f00b）
  与本轮无关，未触碰。

## 8. 门禁（真实 tail）

合并集成分支（完成前）：

```powershell
cd E:\Orchara-wt\review2
git merge integration/f12
# Already up to date.
```

F12 全量（真实栈串行，持有 `E:\Orchara\.scratch\f12\locks\realstack`；最终提交树重跑）：

```powershell
cd E:\Orchara-wt\review2\product
python -m pytest tests/f12 -q
# ........................................................................ [ 96%]
# ..............                                                           [100%]
# 446 passed in 245.06s (0:04:05)
```

（基线 441 passed；新增 5 条，无既有用例退化。）

### 8.1 其余门禁（真跑，同一最终提交树）

```powershell
python -m pytest tests/f01 -q
# ........................................................................ [ 43%]
# ........................................................................ [ 87%]
# .....................                                                    [100%]
# 165 passed in 737.24s (0:12:17)
```

（F01 基线 165 passed，不退化；单跑，不与 f12 并发。）

```powershell
$env:PATH = "E:\orchara-toolchains\go\bin;" + $env:PATH
$env:GOCACHE = "E:\orchara-toolchains\gocache"
$env:GOMODCACHE = "E:\orchara-toolchains\gomodcache"
go build ./...        # exit 0（无输出）
go vet ./...          # exit 0（无输出）
gofmt -l internal cmd tools   # 空输出，exit 0
go test ./...         # exit 0；tail：
# ok  	orchara/platform/internal/toolversions	2.030s
# ok  	orchara/platform/internal/uuid	(cached)
# ok  	orchara/platform/internal/workflow	1.799s
# ?   	orchara/platform/probes/f10_pdf_compare/go_baseline	[no test files]
# ?   	orchara/platform/probes/f15a_objectstore/localfs_probe	[no test files]
```

```powershell
python tools/sync_spec.py --check
# no drift: spec copy and contract snapshot match the manifest
node --check apps/web/src/assets/runs.js        # exit 0
node --check apps/web/src/assets/debug.js       # exit 0
node --check apps/web/src/assets/personal-data.js  # exit 0
```

真实栈锁 `E:\Orchara\.scratch\f12\locks\realstack` 在 f12/f01 门禁期间由本工作树持有，
收尾删除。

## 9. 最终状态

- 分支 `t/f12-review2`；`git merge integration/f12` → `Already up to date.`（集成分支 tip 仍为
  `e3aa1e1`）；本轮修复+测试+文档提交见 `git log`。
- 最终门禁在提交 `ca498e0`（代码/测试/报告）的树上重跑：`tests/f12` **446 passed in 245.06s
  (0:04:05)**、`tests/f01` **165 passed in 737.24s (0:12:17)**、Go 四项全 0/空、
  `sync_spec --check` no drift、三条 `node --check` exit 0。
- 其后只更新本报告与 `PROGRESS.md` 的 tail 文本（不改代码、测试或断言），并重跑
  `tests/f12/test_report_encoding.py`（1 passed）确认报告编码。
