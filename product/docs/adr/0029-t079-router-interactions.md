# ADR 0029 — F08/T079 路由控制面：候选快照、受记账的控制调用与 interaction 答复

- 状态：accepted
- 日期：2026-11-05
- 责任票：T079（GitHub #80，父 F08）；复用 ADR 0023（T074 会话/请求/队列）、
  ADR 0022（T070 撤销）、ADR 0026（T077 授权目录）
- 影响范围：`db/migrations/0050_router_interactions.sql`、`internal/routing/`、
  `internal/requests/{reply,store,submit}.go`、`internal/modelproxy/{routecontrol,provider}.go`、
  `internal/background/{scope,job,handlers/route_request}.go`、`internal/api/{route,requests,server}.go`、
  `cmd/maintenance/scope.go`、`apps/web/src/{chat.html,assets/chat.js}`

## 背景

契约的 `replyInteraction` 是 `POST /api/v1/requests/{request_id}/reply`（必需
`Idempotency-Key`，body `InteractionReply{interaction_id, message?, candidate_version_id?,
artifact_ids?}`，`anyOf` 三选一，`additionalProperties:false`），返回 202 `Request`。
`Request.state` 是七值封闭枚举，`Request.interaction` 是唯一开放对象。规格要求：

- 先取得当前可读且未撤销的已发布版本，再得到小候选集；不能先对全库生成候选再隐藏
  （ROUTING §5）；
- 模型只输出 `select(candidate_id)` / `clarify(question,missing_fields)` /
  `no_match(reason)`，服务端检查输出类型、候选成员、应用可用性与必填输入（ROUTING §7）；
- 模型分数未经校准不当作概率（ROUTING §7）；
- 一次新需求只创建一个主 Run（ROUTING §11）；
- 每请求默认最多 3 次控制调用，路由在 Run 之前、不能塞进只接受 `run_id` 的账本
  （IMPLEMENTATION-DETAILS §45）；
- 重试先查已有 interaction/Run（§43）；过期释放会话槽、旧答复 409
  `interaction_closed`（STATE-MACHINES §19）。

## 决策

1. **候选集是授权 SQL 的产物，先落快照再调模型。** `routing.candidatesTx` 把
   `catalog.ApplicationReadPredicate`（owner 或未过期 read grant）、
   `applications.archived_at IS NULL`、`application_versions.revoked_at IS NULL`、非空能力卡
   和用户文本的**有界字面词项/全文**匹配放进同一条 WHERE，再 `ORDER BY match_rank,
   version_no DESC, id DESC LIMIT 8`；ACL 在 LIMIT 之前，不会先全库生成再隐藏。词项：
   空白/标点切分 + 中文 2/3-gram 字面 `ILIKE`（转义 `%`/`_`），另加 `simple` 全文；中文
   分词质量不宣称（T064/T067 的后续范围）。结果先写
   `platform.request_routing_candidates`（`rank` 是 1 基序号，`card_sha256` 校验能力卡），
   再调用控制模型；`rank` 是整数序号，表里**没有** score/probability/confidence 列。
2. **模型只建议，服务端决定。** 控制调用返回 `decision` 三类之一；`select` 的
   `candidate_version_id` 必须命中本请求候选快照，否则 `failed(route_candidate_invalid)`，
   零 Run、零 interaction。服务端策略独立于模型：候选数为 0 → `no_matching_application`；
   候选数 >1 → 一律 `awaiting_selection`（全部候选可见，模型的选择不落地）；候选数 =1 →
   若模型 `clarify` 或声明的必填输入未满足 → `awaiting_input`，否则唯一合法自动派发。
   输入判定是保守的确定性规则：能力卡 `required_inputs` 的每一项需要一个显式附件
   （`requests.artifact_ids`）；消息文本补充不算类型化输入。该限制在报告写明。
3. **控制调用走既有 modelproxy 账本，归属 route job。** 新增
   `modelproxy.RouteControlCandidate`：部署在 `model_profile_versions.config` 里以
   `route_control: true` 标记一个 **passed、未撤销** 候选（与普通模型候选同一注册表），
   服务端每次按数据库解析；handler/用户都不能指定 model id。调用以
   `background_job_id = route_request job` 写 `platform_model_calls`（`request_id` 保持
   NULL，满足 0011 的 XOR CHECK；请求经 `background_jobs.target_id` 关联）。账本行在调用前
   写入、provider 应答后封存结果 artifact 才置 `succeeded`；失败的物理尝试也是一行，因此
   **计入 3 次预算**（报告声明该口径）。
4. **预算是账本计数，degrade 不再调用模型。** 预算 = 该请求所有 `route_request` job 的
   `platform_model_calls` 行数；`requests.control_calls` 只是可观测镜像（DB CHECK ≤3）。
   每轮 routing 在调模型前检查：≥3 时写一条 `degraded` 决策行——候选快照 ≥2 则创建
   `selection` interaction（用户仍可显式选择），否则 `failed(route_budget_exhausted)`；
   绝不发第四次调用。重试同一轮用稳定 logical key 重放已记录结果，不重复计费；不同轮次
   用新 job（dedup key `requestID:reply:<interactionID>`）与新 logical key。
5. **interaction 带 correlation id、提案 artifact 与过期语义。** 每次 interaction 生成
   `correlation_id`，提案以 sealed artifact（`purpose=interaction_proposal`）保存
   question/缺失字段/候选（六字段能力卡投影，无分数），`Request.interaction.proposal` 从该
   artifact 读取（契约唯一开放对象，不新增契约字段）。TTL 复用 T074 的 15 分钟
   `requests.InteractionTTL`（规格无数字；单一来源避免两包漂移）。过期由 T074 的
   `AdvanceConversationQueueTx` 惰性处理，答复路径用精确 CAS：
   `state='pending' AND expires_at > now()`；CAS 失败后**不写 receipt**，仍 pending 但过期则
   同事务置 `expired` + 请求 `failed(interaction_expired)` 并推进下一排队请求，返回 409
   `interaction_closed`。
6. **答复不双建 Run，三条防线。** (a) interaction CAS 只成功一次；(b) 相同
   `Idempotency-Key` 重放已提交的 202 body（不同 body 409 `idempotency_conflict`），失败/关闭
   的答复不落 receipt；(c) `runs.request_id` UNIQUE（0014）。答复只把请求移回 `routing` 并
   入队一个新 route job；显式 `candidate_version_id` 记入
   `requests.selected_version_id`，恢复轮**不再调用模型**，但必须重新命中候选快照 + 当前
   ACL/发布状态 + 必填输入后才派发用户选中的版本（`explicit_version` 决策）。
7. **route_request handler 是真实的租约执行者，但业务判定在服务端。**
   `background.JobScope.Route` → `POST /internal/v1/background/jobs/{job_id}/route`
   （maintenance mTLS + live attempt token），服务端执行一轮：锁会话/请求 → 重试守卫
   （已有 Run/pending interaction 则返回原结果）→ 显式选择/预算/候选/控制调用 → 复验 →
   提交唯一 interaction 或 Run 或类型化失败。**偏离 notes §6.6 的两方法草案**：用户正文与候选
   都不交给 handler（JobScope 文档要求 handler 不读写业务表），因此没有
   `RouteInput/RouteDecision` 两个可伪造的入参；handler 只提交租约，服务端读模型结果
   artifact 并校验。瞬态模型失败映射 `transient_internal` 由重试表约束；确定性拒绝已在
   请求上提交并返回 200。路由在后台 job 执行（非同步）：保留 T074 的排队/恢复语义，进程
   失联由 job 租约恢复，请求不会永久卡 `routing`。
8. **迁移 0050 的非契约偏差与边界。** 新增
   `request_routing_candidates`、`request_routing_decisions` 与
   `requests.selected_version_id`，均不在 `contracts/database.sql`；0011 的
   `platform_model_calls.request_id` 仍只作延迟 FK 的一半（XOR 下不双写），决策行用
   `model_call_id` 关联账本。重复 migrate 是 no-op；`orchara_worker`/PUBLIC 零授权；不触碰
   F11 删除表结构与知识索引。

## 后果

- T082 可复用 `requests/*` 的 interaction/queue 接缝与 `Request.interaction.proposal`，但
  跨应用交接记录/确认属于 T082；本票不建交接表。
- T078 子 Run 必须走 `requests.DispatchVersionTx` 的 Run 插入半边并自行设置
  `parent_run_id`/`parent_invocation_key`；**不得**调用
  `AdvanceConversationQueueTx`（子 Run 不占逻辑会话槽）。
- F06 接上真实控制模型时只需注册一个 `route_control: true` 的 passed 候选；本批没有
  vendor adapter，真实 provider 仍 fail closed，dev-stub 分支仅在 dev-stub/capacity-stub
  profile 且候选 `provider=stub` 时可达，输出 `stub: true`。
- 已知限制：输入判定按“每项必填输入一个附件”计数，不支持从消息正文抽取值；候选检索是
  字面/全文混合，不宣称语义召回或中文分词质量；控制模型失败次数计入预算（3 次是物理尝试
  上限）；同步等待模型不在本票（后台 job 轮询语义沿用 T074 页面轮询）。
