# F12/T098 手机完成对话、审批和个人数据控制（GitHub #99）

> **复核后重写说明（2026-11-07）**：本文件原来是 UTF-8 被按 GBK 解码后再存回的产物，
> 带 BOM 且含 276 个私用区（PUA）字符；`gbk` / `gb18030` / `cp1252` / `latin-1` 反解均不可逆，
> 原文已丢失。本报告依据**当前已提交代码与测试**重写：`tests/f12/test_t098_mobile_pages.py`、
> `tests/f12/test_t098_stack.py`、`apps/web/src/assets/mobile.css`、六个页面的接线、
> `PROGRESS.md` 的 T098 小节，以及 `reports/f12/T101-journeys.json` 中 citation 相关的真实记录。
> 重写不改变任何代码或测试，只恢复可读、可复核的证据文档。编码自检见 §5。

- 状态：**`passed`**（手段真实 + 覆盖可复核 + 裁决诚实；“小屏完整画布编辑”按票面明确不做）。
- 分支：`t/t098-mobile-personal-data`；worktree `E:\Orchara-wt\t098`；基线 `integration/f12` @ `1ba9d01`；
  代码+测试 HEAD `b2d3089`（`bb3b10e feat(f12)` → `b2d3089 test(f12)`）。
- 范围：`apps/web/src/assets/mobile.css`、`/chat` `/approvals` `/runs` `/personal-data` `/memories`
  `/knowledge` 六个页面及其脚本接线、`tests/f12/test_t098_{mobile_pages,stack}.py`；Go 零改动。
- **不改** `product/docs/spec/**`、`product/contracts/**`、迁移；不新增契约字段。

## 0. 手段声明（硬性，先读）

- **不是 Playwright / 不是真实浏览器 DOM。** 页面结论来自 Node 执行仓库里**已提交的真实字节**
  （`assets/approvals.js`、`chat.js`、`memories.js`、`personal-data.js`、`knowledge.js`、`knowledge.html`
  的内联脚本）+ 最小 DOM/fetch shim。它证明真实脚本字节、真实请求形状、真实渲染文本、状态/按钮行为；
  **不证明** CSS 像素布局、媒体查询在真实视口中的取值、触控手势、`safe-area`、滚动惯性或屏幕阅读器。
- **真实栈**：独立 PostgreSQL 16.4 数据库（`127.0.0.1:55432`，每个栈自建库）+ 真实迁移/bootstrap +
  编译的 `cmd/api` + 真实 internal mTLS（worker/supervisor/maintenance 证书）+ 真实对象目录 +
  真实 `cmd/maintenance`（artifact finalize / document_ingest）。所有 HTTP 断言走 loopback socket，
  不 import handler、不 mock 服务。
- **审批过期对比桩**：本票 API 进程使用 `ORCHARA_APPROVAL_SWEEP_INTERVAL=100ms`（测试窗口），
  生产默认 30s；100ms 只是让“node 审批被后台 sweeper 自动过期”在测试里可观察，不改变语义。
- **不是真实厂商模型**：`ORCHARA_PROFILE=dev-stub`，无厂商凭据/端点/GPU；本票不调用模型。
- **物理删除未完成**：删除的“立即停止访问”是真实断言；“异步物理清理/备份期限”只显示服务端删除任务的
  真实状态，不声称字节已删除（F07 清理债务，见 §6）。

## 1. 逐 AC 对照（issue #99）

| # | 票面 AC | 结论 | 证据（真实执行） |
|---|---|---|---|
| 1 | 冲突/过期/无结果有反馈 | **passed** | 真实栈 5 类：① 记忆陈旧 `If-Match` → `412 precondition_failed`，`details.current_etag="1"`、`current_revision=1`，正文未变；用当前 ETag 重试 → `200 revision=2`。② 同一 `Idempotency-Key` + 不同 body → `409 idempotency_conflict`，库里仍只有 1 条请求。③ 交互到期后答复 → `409 interaction_closed`，请求 `failed(interaction_expired)`、无 Run。④ 无匹配应用 → `failed(no_matching_application)`、无候选、无 Run。⑤ 空知识库 → 检索 `200 items=[]`、问答 `200 no_results`（不是错误）。页面：真实 412 喂给 `memories.js::preconditionPlan` → “版本冲突…静默覆盖”文案；真实 409 喂给 `chat.js::describeSubmitFailure` → `reuseKey=false`；真实失败请求喂给 `describeRequestFailure` → “过期…重新提交/未找到匹配的应用…应用目录”；空检索 → “空结果不是错误 + 下一步”。审批过期见下一条 |
| 2 | 撤销引用不能读缓存 | **passed** | 真实全链路：上传 → finalize → ingest → `ready` → 检索 → `GET /api/v1/knowledge-citations/{chunk_id}` **200 正文** → `DELETE /api/v1/knowledge-documents/{id}` **202** `{id,state,resource_id}`（tombstone 在响应前提交）→ 同一引用 **404 `resource_not_found`**（响应无 `text`/`citation`）→ 同一检索空。页面：真实 404 喂给 `knowledge.js::citationOpenFailure` → `clearBody=true`、“页面不会回退此前读到的正文/重新检索”；`knowledge.html` 内联脚本在 404 时清空 `#citation-text`、`data-citation-available=false`，并断言**没有**残留上一次正文。另覆盖 403 分支 |
| 3 | 个人数据控制（候选默认未决、诊断默认不选、删除状态真实） | **passed** | 真实栈：记忆删除 `202` 后下一次 `GET` 404、列表空、job `200 pending` + remaining scopes；候选 `201` 五字段 `pending` → 列表**无预选择** → 显式 decision `200 confirmed` + 单条记忆；capability 审批过期后 0.6s（≥6 次 sweep）仍 `pending`，decision 才 `409 approval_expired`；node 审批（真实 prepare + waiting + exit）过期后由 100ms sweeper 自动变 `expired(decision_key=sweeper:expired)`，Run `failed(approval_expired)`，迟到决定 409。页面：`memories.js` 展示来源/交互封存/保存范围（应用）/状态/过期，`dataset.selection="none"`、无勾选控件、无全选、点击才发决定；`personal-data.js` 诊断对象**默认不勾选**；文档 7 个摄取状态 + 真实失败码（`ingest_empty_content` 等）+ 下一步；删除同时展示“立即停止访问”（tombstone/epoch，202 前生效）与“异步物理清理/备份期限” + 真实删除任务状态/剩余 scopes，`GET /deletions/{id}` 可读 |
| 4 | 手机端完成对话/审批/结果/个人数据（响应式布局） | **partial（结构/样式规则真实，像素渲染 not_run）** | `mobile.css` 在 720px / 480px 两档：导航换行、44px 触控目标（`:67`、`:267`、`:335`）、`dl`/`.row` 单列、16px 输入（`:347`）、`.table-scroll` 可聚焦横向滚动（`:212`、`:221`、`:226`）；六个页面均在页面自有样式之后挂载（`chat.html:8`、`approvals.html:31`、`knowledge.html:36`、`memories.html:31`、`personal-data.html:29`、`runs.html:29`）。测试用真实 committed 字节断言接线与结构（`tests/f12/test_t098_mobile_pages.py:55`、`:86`、`:99`），但 CSS 在真实视口的取值、触控/手势/横竖屏/`safe-area`/屏幕阅读器 `not_run` |

## 2. 测试与真实命令

```powershell
cd E:\Orchara-wt\review-f12\product
python -m pytest tests/f12/test_t098_mobile_pages.py tests/f12/test_t098_stack.py -q
```

本次复核在同一提交树上重跑的原始 tail：

```text
..............                                                           [100%]
14 passed in 28.38s
```

- `tests/f12/test_t098_mobile_pages.py` **8 passed**：真实 committed bytes + DOM/fetch shim。
  - `test_mobile_stylesheet_is_linked_after_each_pages_own_style`（`:55`）
  - `test_the_wide_ledger_table_scrolls_instead_of_overflowing_the_phone`（`:86`）
  - `test_each_phone_journey_state_that_small_screens_keep_is_named_in_the_page`（`:99`）
  - `test_the_approval_page_explains_both_expiry_mechanisms_and_never_revives`（`:323`）
  - `test_chat_reports_conflict_expiry_and_no_result_with_a_next_step`（`:423`）
  - `test_memory_candidates_show_source_and_save_scope_and_default_to_no_choice`（`:606`）
  - `test_personal_data_shows_ingestion_states_and_never_grant_selection_by_default`（`:814`）
  - `test_a_revoked_citation_is_cleared_and_never_served_from_the_previous_body`（`:1025`）
- `tests/f12/test_t098_stack.py` **6 passed**（真实 PG/mTLS/maintenance/http）：
  - `test_a_stale_if_match_is_a_located_conflict_and_never_overwrites`（`:225`）
  - `test_the_same_request_key_with_a_different_body_is_a_explicit_409`（`:276`）
  - `test_an_expired_interaction_and_a_no_match_request_are_failed_and_explained`（`:323`）
  - `test_a_deleted_document_stops_the_citation_immediately_and_shows_the_job`（`:402`）
  - `test_capability_and_node_approvals_expire_by_different_mechanisms`（`:505`）
  - `test_memory_deletion_is_immediate_and_a_candidate_is_never_pre_decided`（`:619`）

交叉证据：真实 citation 生命周期也由 `reports/f12/T101-journeys.json` 的 J4 记录独立复核
（上传 → finalize → ingest → ready → 检索 → 删除后 404 不回退），两条路径结论一致。

## 3. 可验证 / 不可验证分栏

| 类别 | 已验证（有真实执行） | 不可验证 / `not_run` |
|---|---|---|
| 服务端冲突/过期/无结果 | 412、409 idempotency、409 interaction_closed、failed(no_matching_application)、空检索/问答 | 真实厂商模型导致的超时/限流（dev-stub 无厂商） |
| 审批过期 | capability 惰性过期 vs node 30s sweeper 自动过期（测试窗口 100ms），迟到决定 409 | 生产 30s 窗口的端到端计时（测试只证明机制，不证明 30s 常数） |
| 引用撤销 | 202 tombstone → 404、无正文回退、检索空 | 缓存层/浏览器 HTTP 缓存的真实行为（服务端 no-store 已由 T093 门禁覆盖） |
| 删除 | 立即禁读、job 状态/剩余 scopes、备份期限展示 | 物理副本删除、备份到期（F07）；删除任务不轮询到 completed |
| 个人数据 | 候选默认未决、诊断默认不选、保存范围/来源/状态/失败码 | 真实浏览器表单控件的隐含提交行为（Node shim 只覆盖页面脚本） |
| 小屏布局 | CSS 规则、媒体查询存在、页面挂载顺序、结构类名 | 真实视口像素、触控手势、`safe-area`、屏幕阅读器、横竖屏切换 |
| 小屏画布编辑 | 页面明确声明“不承诺小屏完整画布编辑” | `/editor` `/debug` 的小屏编辑（票面明确不做） |

## 4. 编码与可读性自检

本报告重写后为 **UTF-8（无 BOM）**，不含 U+FFFD、不含 U+E000–U+F8FF 私用区字符。复核命令：

```powershell
python -m pytest tests/f12/test_report_encoding.py -q
```

该测试扫描 `reports/f12/**/*.{md,json}`：BOM、U+FFFD、PUA 任一出现即失败。
同一次全目录扫描还发现**不属于本票**的两处历史编码问题，已在 `REVIEW-FIXES.md` 记录、未修改
（合并协议禁止从本票改其它票的 `reports/`）：

- `reports/f13a/T065-progress-and-failure-classes.md`：文件开头 BOM；
- `reports/f15a/runs.json`：包含 U+FFFD 替换字符。

## 5. 未修 / 归属其它 epic

- 删除后的物理清理、备份期限与 completed 轮询：F07/T071/T072。
- `MemoryCandidate` 契约没有 `source_run_id`（`additionalProperties:false`），页面不编造具体 Run id：
  F09 / 契约治理。
- 诊断授权仍按 run UUID 手输：F11/T073。
- 真实厂商模型：F14/T090（`blocked`：无凭据/端点/GPU）。
- 聊天正文当 Run 输入的既有 `node_input_invalid` 缺口：F08↔F04B，未在本票绕过。
- `/editor`、`/debug` 的小屏完整画布编辑：票面明确不做。

## 6. 结论

T098 的四条手机端旅程在**可执行手段**（真实栈 + Node 执行真实页面字节）范围内成立：冲突/过期/无结果
都有真实服务端事实与页面反馈，引用撤销后不回退缓存，个人数据控制默认不代替用户做选择，删除的
“立即停止访问”与“异步物理清理”被分开陈述。像素级移动端渲染与真机交互仍是 `not_run`，没有被写成通过。
本报告为编码事故后的重写版；内容依据当前代码、测试与真实运行 tail，而不是已丢失的旧文本。

## 7. 第二轮复核后修正（2026-11-07，F1/F2）

> 本节是**第二轮独立复核后**的补充，不删除也不改写上面的旧结论；旧结论在当时的代码/测试
> 范围内仍然成立，本节记录其后修正的部分。

第二轮复核对 `1cdff9c...e3aa1e1` 提了两条页面发现，修复提交 `83307b1`；本轮补齐红→绿测试并
追加两处修正（逐条出处、复现、红/绿 tail 见 `reports/f12/REVIEW2-FIXES.md` §1/§2/§6）：

- **F1（Standards P1）诊断授权“默认不勾选”实际授权整个 Run。**
  `internal/diagnostics/grants.go:8-10,395-423` 在缺少 `X-Diagnostic-Object-Scope` 时推导该 Run
  的全部对象；旧页面空选择时省略该头。现在 `personal-data.js:367-378` 在空选择时抛错，
  页面把原因写进 `#grant-status`，非空选择总是发送等于选择的头。
  另修：复选框值原本是 `artifact:<uuid>` 而构造器期望裸 uuid（`personal-data.js:716`），
  否则用户一勾选就得到“artifact id 必须是 UUID”且 0 请求。
- **F2（Standards P2）重建中/失败的资料被说成不可检索。**
  ADR 0018:26,58-59 规定重建期间/失败后旧活动索引仍可读；旧文案却写“完成前检索为空”、
  “不会返回旧正文”。`personal-data.js:185,194` 已改为同时说明“旧的仍是检索来源”与“首次索引才为空”。

本轮新增测试（Node 执行 **已提交真实字节** + 最小 DOM/fetch shim，不是 Playwright）：

```text
tests/f12/test_t098_review2.py::test_an_empty_diagnostic_grant_selection_is_refused_and_never_sent PASSED
tests/f12/test_t098_review2.py::test_rebuild_and_failure_notes_keep_the_previous_index_readable PASSED
5 passed in 0.79s          # 连同 T096 的两条 F3/F4 用例
```

红证据：在 `e3aa1e1` 的独立 detached worktree 上（只拷入新测试、不改源码）两条都失败，
其中空选择报告 `empty_helper_threw=false`、`empty_csrf_calls=1`；旧 `indexing` note 命中
`完成前检索为空`（tail 见 `REVIEW2-FIXES.md` §5.1）。受影响的
`tests/f11/test_t073_pages.py:165-171` 旧断言“空选择省略头”已随行为更新为断言抛错。

边界不变：仍是 Node 执行真实字节，不是真实浏览器；诊断授权的真实服务端解析由 F11/T073
既有真实栈用例覆盖，本轮未改其接口/存储，也未改 `docs/spec/**` 与 `contracts/**`。
