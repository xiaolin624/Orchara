# ADR 0021 — F08 conversation/request aggregate, the logical slot and non-contract facts

- 状态：accepted
- 日期：2026-11-05
- 责任票：T074（GitHub #75，父 F08）；复用 ADR 0020（F11/T069 删除基础）
- 影响范围：`internal/requests`、`internal/api/{server,requests}.go`、
  `internal/catalog/{publication,versions}.go`、`db/migrations/0044_conversations_and_requests.sql`、
  `apps/web/src/{chat.html,assets/chat.js}`

## 背景

契约（`contracts/openapi.json`）定义了 `createConversation`/`listConversations`/
`getConversation`/`submitRequest`/`getRequest`/`cancelRequest`/`listMessages`，但没有实现；
`platform.conversations`、`platform.requests`、`platform.interactions` 也不存在。0014 只留下
`runs.request_id`（UNIQUE）与 `runs.conversation_id` 两个无 FK 列。T069 已实现共享删除基础，
但把 conversation 的权威留给本票（`tests/f11` 的 `not-a-uuid` 404 用例）。

规格同时要求：同会话最早未完成请求占逻辑槽；普通新请求排队；显式版本绕过模型匹配但保留
Request/队列/权限；用户能看清处理版本；新应用不得读旧内部历史/记忆；删除会话立即不可读。
契约的 `Request`/`Conversation` 是 `additionalProperties:false`，没有处理版本、队列位置或
conversation id 的字段；`cancelRequest` 返回 `Accepted` 而不是 `Request`。

## 决策

1. **逻辑槽由两个数据库事实 + 会话行锁共同保证，而不是一个索引。**
   `one_active_top_run_per_conversation`（0014，非终态顶层 Run）与
   `one_active_request_per_conversation`（0044，`routing|awaiting_input|awaiting_selection`）
   分别覆盖 Run 已分派和请求尚未分派两半。绝不为 `state='dispatched'` 建部分唯一索引：
   STATE-MACHINES.md §17 规定 Run 终止后请求仍是 `dispatched`，那种索引会让会话在第一次运行后
   永久卡死。`submitRequest`/`cancelRequest`/删除会话都先 `SELECT … FOR UPDATE` 锁会话行，因此
   并发提交的失败者是 `queued`（或 404），而不是第二个槽持有者。
2. **`Request.run_id` 是连接得出的，不是第二个事实。** `runs.request_id` 已 UNIQUE，
   `requests` 不存 `run_id` 列，避免两处写入不一致。`request.runs` 的终态由请求状态判断：
   `dispatched` + 终态 Run = 已完成，不再占槽。
3. **固定版本路径复用 `catalog.ResolveRunnableVersionTx`。** 事务内对 version/application 取
   `FOR SHARE`：并发撤销要么先提交（本票看到 `revoked_at` 并拒绝），要么等 Run 插入提交后
   再撤销（历史保留冻结引用）。拒绝码为 `409 version_revoked`、`409 version_archived`、
   `409 version_unavailable`、`404 resource_not_found`（不可见）。事务内还校验
   `manifest_artifact_id` 的 ready 状态与哈希，再复制 `execution_snapshots`/`runs`。
   T070 的撤销路径应复用同一读取和同一错误码。
4. **契约封闭 schema 之外的 UI 事实走文档化响应头**（ADR 0004 的 `X-Manifest-SHA256`
   先例）：`X-Request-Conversation-Id`、`X-Request-Application-Version-Id`、
   `X-Request-Application-Version-No`、`X-Request-Queue-Position`、
   `X-Request-Interaction-Id`、`X-Request-Correlation-Id`。不新增 JSON 字段、不改契约、
   `sync_spec --check` 保持无漂移。
5. **非契约列/表**（均在 0044 与报告里列为偏差）：
   - `requests.artifact_ids uuid[]`：契约 `RequestInput.artifact_ids` 的持久化集合，使 Run
     的输入闭包在“提交时没有 Run（排队）”的情况下仍可精确重放；
   - `requests.requested_version_id`：显式版本；
   - `requests.control_calls`（≤3）：T079 的控制模型预算，本票只建列；
   - `platform.request_handoffs`：确认人、确认时间、来源 Run、摘要 artifact、来源版本；
   - `interactions`：本票建表并只读 pending 投影/取消时失效；T079 写行。
6. **交接确认是服务端最小范围校验。** 契约的 `confirmed: const true` 只是客户端形状；
   服务端要求非空摘要、至少一个来源 Run、每个来源 Run 属于调用者且属于同一会话、每个附件
   属于调用者且 ready，摘要单独封存为 `handoff_summary` artifact 并只链接到新 Run。旧会话历史
   是用户可见投影，永不作为 Run 输入；个人记忆按 `(owner, application)` 召回，不跨应用。
7. **惰性推进是本票的确定性机制。** `AdvanceConversationQueueTx` 是唯一接缝：提交、取消、
   以及**排队请求的读取**都会在会话行锁内推进队列并惰性过期 interaction（`interaction_expired`、
   释放槽）。这样 Run 通过既有执行取消路径结束后，下一次轮询就能推进下一项，不依赖定时器。
   Run 终态的统一 finalizer 钩子属于 F03/T079：T079 的路由/派发路径必须调用同一接缝。
8. **消息是投影，不是第二份正文。** `listMessages` 从 `requests`/`runs`/`interactions`
   投影：request 指请求输入 artifact，result 指 Run 输出，status 无正文，interaction 指
   proposal artifact；正文始终经 artifact 鉴权读取。
9. **会话删除接回 T069 基础。** `requests.DeletionTarget` 在同一事务里 tombstone
   `conversations.deleted_at`、递增 revision、取消 queued/routing/awaiting 请求并失效 pending
   interaction；非终态 Run 交给 foundation 既有的 T036 取消扇出；7 个 scope 的 details 由
   conversation 的真实 request/run/artifact/handoff 行填充，不伪造“已清理”。

## 后果

- 后续票直接复用：`internal/requests.Service`（`CreateConversation`/`Submit`/`GetRequest`/
  `Cancel`/`ListMessages`/`AdvanceConversationQueueTx`）与 `DeletionTarget`；
  `catalog.ResolveRunnableVersion(Tx)`、`catalog.RunPolicy`；`RequestFacts` 的响应头约定。
- T079 必须：写 interaction 行、在路由/派发后调用 `AdvanceConversationQueueTx`、在 Run 终态
  路径调用同一接缝（或在 finalizer 钩子里），并复用 `version_revoked` 等拒绝码。
- T077/T082 必须把 conversation/request 的读取接受为 404（删除后立即不可读），交接摘要
  artifact 继承 T071/T072 的清理 scope。
- 已知边界：没有会话列表到请求列表的契约端点，页面从 `listMessages` 的 request id 反查
  `getRequest`；轮询在 `dispatched` 停止（状态稳定），处理版本由响应头给出。
