# F08 T079 验收：自然语言需求通过选择或澄清进入唯一主运行

- 任务：F08 / GitHub Issue #80（票 `T079`，规划 ID F08.03/F08.04），分支
  `t/t079-router-interaction`（worktree `E:\orchara-worktrees\t079`）；父门禁 F08。
- 状态：**`passed`**（本票范围 = `replyInteraction` + 授权候选检索 + 受记账的控制模型建议 +
  服务端候选/输入/授权校验 + interaction（input/selection）创建与 expiration + 精确 409
  `interaction_closed` + 每请求 3 次控制预算与 degrade + `route_request` 持久 job handler
  与失败守卫 + `/chat` 的候选/提问答复界面 + Node 可执行纯函数；真实 PostgreSQL 16.4、
  真实编译二进制、真实 cookie+CSRF HTTP、真实 maintenance/mTLS、真实封存对象字节、
  Node 24。）
- 边界（如实）：**真实 vendor 模型 adapter 未编译（F06 范围）**；本票的控制模型是
  `model_profile_versions` 中真实注册、`validation_state=passed`、`config.route_control=true`
  的 dev-stub 候选（`stub: true`），因此模型调用/账本/服务端校验全链路真实，而“真实厂商
  模型质量” `not_run`。候选检索是授权 SQL + 字面/全文混合，不宣称语义召回或中文分词质量。
  必填输入判定是“每项 required_inputs 需要一个显式附件”的保守确定性规则。跨应用交接 UI 属
  T082；子 Run 属 T078；T082 所需的 queue/interaction 接缝已保留。浏览器 DOM 驱动页面仍
  `not_run`（与 T023/T027/T033/T074 同一边界）；全量 `tests` 未运行。
- 迁移：`db/migrations/0050_router_interactions.sql`（合并后基线 tail 为 0049；本票原编号 0046 与 T072 的 `0046_deletion_copy_ledger.sql` 撞号，最终 merge 时按 BRIEF §5 重编号为 0050；测试按 slug 断言，不 pin 编号）。
- 决策：`docs/adr/0029-t079-router-interactions.md`（非契约候选快照/决策表/选中列、控制模型
  解析、预算口径、handler 协议与过期语义）。契约与 `docs/spec/**` 未改动，
  `python tools/sync_spec.py --check` 无漂移。

## 0. 工作记录（简短）

1. 读票面、`BRIEF.md`、`notes/f08-conversations.md` §5（状态机 + 精确 409 谓词）、§6（router
   设计/预算/相关性/correlation/handler 协议）、§3.4（model-call 边界）、§4（数据模型）、
   §10（测试计划）、§11（复用接缝）；打开 `internal/requests/`、`internal/modelproxy/`、
   `internal/background/{scope,complete,job}`、`internal/catalog/{index,publication}.go`、
   `internal/api/{server,internal,jobdata}.go`、`tests/support/{stack,f08,t077,t024}.py`、
   `0044/0045` 迁移与契约 `replyInteraction`/`Request`/`InteractionReply`。
2. TDD 垂直切片（先写失败测试、再最小实现）：
   - 迁移 0050（候选快照/决策/selected 列 + 零授权）→ 4 个迁移测试；
   - 单候选 + 输入满足 → 唯一 Run（真实 published version、真实 route handler、真实账本）；
   - 缺输入 → 不建 Run；答复补附件 → 恰好一个 Run；
   - 多候选 → `awaiting_selection`，全部候选可见，显式 `candidate_version_id` 才派发；
   - 模型建议集合外版本 → `failed(route_candidate_invalid)`，零 Run；
   - 伪造答复候选 → 422 且 interaction 仍可答复；
   - 3 次控制调用封顶 → 第 4 轮 degrade（失败 / 选择）且无第 4 个账本行；
   - 重复答复同 key 重放、异 key 409、仍只有一个 Run；
   - 过期答复 409 `interaction_closed`、同事务 expired+failed、释放槽推进下一项；
   - 无候选 → `no_matching_application` 且零模型调用；无分数/概率；
   - 路由 job 终止失败 → completion guard 失败请求并推进队列；
   - 页面结构 + Node 纯函数（候选序号、缺字段、答复体/幂等 key）。
3. 跑门禁（§3）：`go build/vet/test`、`gofmt -l`、`sync_spec --check`、`tests/f08` 两次背靠背
   （54 passed）、`tests/tooling`、`tests/f11`；写本报告/ADR 0029/PROGRESS 行；未推送、未关
   issue、未改契约/规格；最终合并 `integration/t068-t082`。

## 1. 交付内容

### 1.1 数据模型（`0050_router_interactions.sql`）

- `platform.request_routing_candidates`：每轮候选快照；`rank` 是 1 基**序号**（无
  score/probability 列），`capability_card` + `card_sha256`，主键 `(request_id, version_id)`。
- `platform.request_routing_decisions`：每轮决策（`select|clarify|no_match|explicit_version|
  degraded`），`selected_version_id`、`model_call_id`（关联受记账账本）、`raw_decision`，主键
  `(request_id, attempt)`。
- `platform.requests.selected_version_id`：用户显式候选答复，恢复轮消费；不复用
  `requested_version_id`（固定版本路径会直接派发，绕过输入校验）。
- 均不在 `contracts/database.sql`；`orchara_worker`/PUBLIC 零授权；重复 migrate no-op。

### 1.2 `internal/routing`（本票发布的复用接缝）

- `candidatesTx`：ACL 谓词 + archived/revoked/非空卡 + 用户文本词项/全文匹配在**同一条 SQL、
  LIMIT 之前**；`rank` 为 ordinal；上限 8；中文 2/3-gram 字面匹配不宣称分词质量。
- `Advance(ctx, jobID, requestID, jobOwnerID)`：一轮 routing 的 prepare → control → commit：
  - 锁会话/请求（与 T074 相同锁序）；重试先查已有 Run/pending interaction；
  - 显式候选答复 → 复验候选快照 + 当前 ACL/发布 + 必填输入后派发，不再调用模型
    （`explicit_version`）；
  - 预算 = 该请求所有 `route_request` job 的 `platform_model_calls` 行数；≥3 时写 `degraded`
    行：快照 ≥2 → `awaiting_selection`（新 interaction），否则 `failed(route_budget_exhausted)`；
  - 候选快照先落库再调用控制模型；模型只解析三类建议；`select` 必须命中快照，否则
    `failed(route_candidate_invalid)`；
  - 服务端策略：0 候选 → `no_matching_application`；>1 → 一律 `awaiting_selection`（模型选择
    不落地，全部候选可见）；=1 → 模型 clarify 或必填输入缺失 → `awaiting_input`，否则唯一
    合法自动派发；
  - 失败同事务 `AdvanceConversationQueueTx` 推进下一排队请求；interaction 提案封存为
    `interaction_proposal` artifact；correlation id 与 15 分钟 TTL 由 T074 单源复用。
- `DispatchVersionTx` 复用 T074 的 published Run 创建路径（snapshot/job/status/输入闭包）。

### 1.3 `internal/requests`：答复与 interaction 投影

- `ParseReply`：`InteractionReply` additionalProperties:false + `anyOf` 三选一 + 长度/UUID/
  ≤5 附件；canonical SHA-256 digest。
- `ReplyInteraction`：路径级 `Idempotency-Key` receipt（同体重放、异体 409）；候选先校验
  **候选快照成员**与当前 runnable，附件真实校验；精确 CAS
  `state='pending' AND expires_at > now()`；0 行时回滚 receipt，再在独立事务判定：
  - 仍 pending 但过期 → interaction `expired` + 请求 `failed(interaction_expired)` + 推进队列
    → 409 `interaction_closed`（不写 receipt，重复 key 仍 409）；
  - 已 answered/invalidated 或请求离开 awaiting → 409 `interaction_closed`（零写入）；
  - 成功 → 请求回 `routing`、追加答复附件、记录显式候选、入队新 route job
    （`requestID:reply:<interactionID>` dedup）。
- `readPendingInteraction` 现在读取封存的 proposal，投影到契约唯一开放对象
  `Request.interaction.proposal`（question/candidates/missing_fields；无分数）。

### 1.4 控制模型与账本（`internal/modelproxy`）

- `RouteControlCandidate`：按 `model_profile_versions.config->>'route_control'='true'` 解析
  最新 `passed`、未撤销候选（部署注册，handler/用户不能指定 model id）；`ControlCandidate`
  复用 endpoint/secret 引用校验。
- 调用经既有 `platform_model_calls`：`background_job_id=route_request job`，`request_id` 保持
  NULL（0011 XOR CHECK），结果封存 artifact 后才 `succeeded`；失败物理尝试也计入预算。
- dev-stub 路由分支：`task=route` 时从服务端 prompt 确定性回答——消息里出现的 UUID 原样
  建议（用于 AC1 集合外建议），否则选第一个授权候选，否则 `no_match`；输出 `stub:true`，
  仅 dev-stub/capacity-stub + `provider=stub` 可达。

### 1.5 后台 job 协议、失败守卫与页面

- `KindRouteRequest` handler（`handlers/route_request.go`）：只提交 lease，调用
  `POST /internal/v1/background/jobs/{job_id}/route`；服务端完成候选/模型/校验/写入。
  **偏离 notes §6.6 两方法草案**：用户正文与候选都不交给 handler（JobScope 文档要求 handler
  不读写业务表），因此没有可伪造的 decision 入参；ADR 0029 §7 记录理由。
- 重试表新增 `route_request`（transient storage/internal/lease）；瞬态模型失败 → handler
  `transient_internal` 有界重试；确定性拒绝已在请求上提交 → job succeeded。
- completion guard：job **终止失败**（重试表耗尽/协议拒绝）且请求仍在 `routing` → 请求
  `failed(route_failed)` + 审计 + 通过注入的 `AdvanceConversationQueueTx` 释放槽；会被重试的
  失败不提前改写请求。
- `POST /api/v1/requests/{request_id}/reply`（session+CSRF、`application:write`、必需
  Idempotency-Key）→ 202 `Request`，沿用 `X-Request-*` 事实头。
- `/chat` + `/assets/chat.js`：交互面板显示提问/缺失字段/候选（序号仅为候选顺序）、过期提示、
  候选单选 + 补充说明 + 答复按钮；Node 纯函数 `requestReplyPath`/`describeInteraction`/
  `candidateLines`/`interactionMode`/`interactionReplyBody`/`newInteractionAttempt`/
  `interactionReplyHeaders`。

## 2. 验收标准（逐条，全部由已执行测试证明）

| AC | 结论 | 证据（真实执行） |
|---|---|---|
| 伪候选拒绝 | passed | `test_t079_interaction.py::test_a_model_suggestion_outside_the_authorized_candidate_set_is_refused`：真实 published 但不在候选集合的版本被 dev-stub 建议 → 请求 `failed(route_candidate_invalid)`、零 Run、零 interaction、候选快照只有真实候选、决策保留被拒建议供审计；`test_a_forged_reply_candidate_is_refused_and_the_interaction_stays_answerable`：答复集合外版本 422 `candidate_not_in_snapshot`、interaction 仍可答；Go `TestParseDecisionAcceptsOnlyTheThreeDocumentedShapes` 覆盖类型拒绝 |
| 缺输入不建 Run | passed | `test_t079_routing.py::test_missing_input_creates_no_run_until_the_reply_supplies_it`：`awaiting_input` 零 Run、proposal 列出 2 个 missing_fields、`Request.interaction.proposal` 同源；答复 2 个真实上传 artifact 后真实 route handler 恰好建 1 个 Run 且输入闭包含两附件；`test_t079_interaction.py::test_a_forged_reply_candidate...` 另证拒绝不建 Run |
| 超过预算转选择/补充或失败 | passed | `test_the_third_control_call_is_the_last_and_the_budget_fails_typed`：3 轮真实调用后第 4 轮 `failed(route_budget_exhausted)`、账本恰 3 行、重跑 handler 仍 3 行且零 Run；`test_budget_exhaustion_degrades_to_selection_without_a_fourth_call`：2 候选时第 4 轮 degrade 为新的 `awaiting_selection`（双候选可见），随后显式选择成功派发且仍无第 4 次调用 |
| 不把分数当已校准概率 | passed | `test_candidates_and_proposals_carry_ordinal_ranks_not_probabilities`：selection proposal rank `[1,2]`、proposal/decision raw/`GET /requests/{id}` 文本均无 score/probability/confidence/calibrated；页面 Node 测试断言候选行只有“候选 N”；Go `TestProposalsCarryOrdinalRanksAndNoScores`；迁移测试断言表内无 score/probability 列 |
| 重复答复不双建 Run | passed | `test_two_replies_never_create_two_runs_and_the_second_is_closed`：同 key 同体重放 202 同 body，异 key 同 interaction 409 `interaction_closed`，最终恰 1 个 Run 且绑定所选版本；`runs.request_id` UNIQUE 为数据库兜底 |
| 过期回复 409 interaction_closed | passed | `test_an_expired_reply_is_409_and_releases_the_conversation_slot`：`expires_at=now()-5s`（≥5s 余量，不睡眠）→ 409 `interaction_closed`、interaction `expired`、请求 `failed(interaction_expired)`、零 Run、同 key 重试仍 409 且 reply receipt 0 行；同会话下一 queued 请求被推进为 `routing` |
| 多候选不偷偷选最近似 | passed | `test_multiple_candidates_are_all_visible_and_only_an_explicit_choice_dispatches`：两真实候选全部写入快照/proposal（rank 1/2），服务端不派发；显式选择与模型建议不同的候选后，恢复轮不调模型、Run 绑定用户所选版本 |
| 端到端入口/失败/授权/重试边界 | passed | 全部 AC 经真实 REST（session+CSRF）+ 真实 maintenance/mTLS + 真实持久层；`test_a_terminal_route_failure...` 证明 job 终止失败不困住会话槽（请求 `failed(route_failed)`、下一项推进）；`test_a_refusal_committed_by_the_router...` 证明服务端已提交的拒绝不被 guard 改写；`test_no_authorized_candidate...` 零候选零模型调用；`test_t079_migration` 覆盖 schema/权限边界 |
| 记录版本/输入/hash/命令/原始结果/profile | passed | §3 原始输出；profile `dev-stub`；Run/版本/封存输入/候选 card hash/账本行均在测试中断言；真实 vendor 模型 `not_run`（F06 adapter 未编译） |
| 页面 | passed | `test_t079_pages.py`：真实编译 API 服务 `/chat` 与 `/assets/chat.js` 结构；Node 24 执行 chat.js 断言路径/候选序号/缺失字段/答复体/幂等 key 与“无概率” |

## 3. 门禁（原始输出）

环境：Windows PowerShell；Go 工具链 `E:\orchara-toolchains\go`；PostgreSQL 16.4
（`127.0.0.1:55432`，user-space cluster）；profile `dev-stub`；Node 24.13.0。

```
$ go build ./...
exit 0

$ go vet ./...
exit 0

$ gofmt -l .
（空）

$ go test ./...
ok  orchara/platform/internal/...   （全部包 ok；含新增 internal/routing 单测）

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

```
$ python -m pytest tests/f08 -q --basetemp=E:\orchara-tmp\t079-20
54 passed in 949.35s (0:15:49)

$ python -m pytest tests/f08 -q --basetemp=E:\orchara-tmp\t079-21
54 passed in 842.00s (0:14:01)   （与第一轮背靠背）

$ python -m pytest tests/tooling -q --basetemp=E:\orchara-tmp\t079-22
21 passed in 5.91s

$ python -m pytest tests/f11 -q --basetemp=E:\orchara-tmp\t079-23
22 passed in 273.67s (0:04:33)   （含 conversation 删除线）
```

## 4. not_run / 限制

- 真实 vendor 模型 adapter/凭据：F06 范围；本批 `modelproxy` 只编译 dev-stub 与 fail-closed
  的真实路径。故“真实模型质量/真实 provider 路由”`not_run`；控制模型仍是真实注册候选 +
  真实账本 + 服务端校验。
- 语义/向量召回与中文分词质量：T064/T067 范围；本票只做授权 SQL + 字面/全文候选。
- 必填输入按“每项 required_inputs 一个附件”计数，消息正文不抽取类型化值（ADR 0029）。
- 控制预算是物理尝试数（失败尝试也计一行），不是“成功调用数”（ADR 0029）。
- 浏览器 DOM、SSE、全量 `pytest tests`；T082 交接 UI、T078 子 Run。
- 未推送、未创建/关闭 GitHub issue、未修改 `docs/spec/**` 或 `contracts/**`。

## 5. 给下一票的接口（T082/T078）

- `internal/requests`：`ReplyInteraction` / `ParseReply` / `ReplyPath` /
  `Request.interaction.proposal` 投影 / `AdvanceConversationQueueTx` / `DispatchVersionTx`。
  T082 可在同一 interaction/queue 接缝上加交接确认，不必另建队列。
- `internal/routing`：`Advance(ctx, jobID, requestID, jobOwnerID)`、`Candidate`、
  `MaxCandidates=8`、`ControlBudget=3`、稳定 error codes
  （`no_matching_application`/`route_output_invalid`/`route_candidate_invalid`/
  `route_budget_exhausted`/`route_model_unconfigured`/`selected_version_invalid`）。
- 后台协议：`POST /internal/v1/background/jobs/{job_id}/route`（maintenance mTLS、live lease）；
  `background.JobScope.Route`；`route_request` retry 表；completion guard 保证失败不困槽。
- 版本选择事实：`requests.selected_version_id`（显式候选答复，恢复轮消费）；
  `request_routing_candidates`/`request_routing_decisions` 供审计与 T082 展示；
  `runs.request_id` UNIQUE 是“一请求一主 Run”的数据库事实。
- 页面纯函数：`requestReplyPath`、`describeInteraction`、`candidateLines`、`interactionMode`、
  `interactionReplyBody`、`newInteractionAttempt`、`interactionReplyHeaders`。
- 迁移/ADR：`0050_router_interactions.sql`（因 T072 占用 0046 而重编号）、ADR 0029；测试按 slug 断言。
