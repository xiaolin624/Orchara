# ADR 0030 — F08/T082 跨应用交接：服务端最小摘要预览、确认门禁与固定版本切换

- 状态：accepted
- 日期：2026-11-05
- 责任票：T082（GitHub #83，父 F08）；复用 ADR 0023（T074 会话/请求/队列）、ADR 0022（T070 撤销）、ADR 0029（T079 路由与 interaction）
- 影响范围：`internal/requests/{handoff,submit,service}.go`、`internal/api/{requests,server}.go`、
  `db/migrations/0051_handoff_scope_bounds.sql`、`apps/web/src/{chat.html,assets/chat.js}`、`tests/f08/`

## 背景

契约的 `RequestInput.handoff` 只有四个成员（`source_run_ids`、`artifact_ids`、`summary`、
`confirmed: const true`），T074 已实现服务端确认（来源 Run 属于调用者且同会话、附件 ready、
摘要非空且 ≤8000 字），但摘要是**客户端自由文本**。规格要求切换应用时展示“将交接的摘要/附件”、
由用户确认新增敏感交接范围，且新应用不得自动获得旧内部历史或个人记忆
（`ROUTING-MEMORY-KNOWLEDGE.md` §15）。自由文本摘要无法证明“最小范围”：客户端可以把整段旧
请求正文或个人记忆正文塞进 8000 字里，服务端只能检查长度。

同票还要求显式版本切换不伪造路由：`application_version_id` 绕过模型匹配但保留 Request、队列、
权限与发布状态校验；用户能在请求面看到处理版本。

## 决策

1. **预览是非契约只读端点。** `POST /api/v1/conversations/{conversation_id}/handoff-preview`
   接收 `{source_run_ids:[uuid], artifact_ids:[uuid]}`，返回服务端生成的
   `{conversation_id, from_version_id, source_run_ids, artifact_ids, summary, summary_sha256,
   source_runs[], artifacts[], limits}`。它只读、写零行、不需要 `Idempotency-Key`；cookie 会话
   仍走 CSRF，API key 仍要求 `application:read`。契约的 `Request`/`RequestInput`/`Handoff`
   一个字段都不加（`additionalProperties:false` 保持诚实），`sync_spec --check` 无漂移。
2. **摘要只有一个生成器，只读元数据。** `buildHandoffSummary` 使用应用名、版本号、Run id、
   Run 创建时间、附件 purpose/media type/size/sha256 前缀；**不读**任何消息正文、Run 输出正文
   或记忆正文。每个字段按 rune 上限裁剪，摘要硬上限 2000 rune，选择上限为 5 个来源 Run、
   5 个附件；因此预览天然有界且可复算（无时间戳/随机 token），重复调用字节一致。
3. **提交必须携带服务端预览原文。** `Submit` 在封存前用**同一**读取/生成逻辑重新计算预览：
   `handoff.summary` 与重算摘要不一致 → `422 handoff_scope_exceeded`
   （`details.field=handoff.summary`, `reason=summary_not_server_generated`）。客户端因此不能
   把“完整对话/记忆正文”当作已确认摘要提交。数量越界同样是 `422 handoff_scope_exceeded`
   （`source_run_ids`/`artifact_ids`），来源 Run 缺失/他人/跨会话仍是 T074 的
   `422 handoff_source_unknown`，`confirmed:false` 仍是 `422 handoff_not_confirmed`。
4. **附件范围 = 来源 Run 的产出 ∪ 新请求显式附件。** 允许的交接附件是 `run_artifacts` 中
   `relation <> 'input'` 的来源产出，或用户已在本次 `RequestInput.artifact_ids` 里显式选择的
   附件；其它已授权 artifact 也拒绝（`422 handoff_scope_exceeded` + `artifact_ids`）。来源 Run
   的 `input` 链接正是旧请求封存正文，永不作为交接附件。
5. **Run 输入闭包补上确认的来源产出。** T074 的 `runInputArtifactIDsTx` 原先只并
   `requests.artifact_ids` 与 summary artifact；本票把它扩为
   message ∪ `requests.artifact_ids` ∪ `request_handoffs.artifact_ids` ∪ summary artifact。
   这样“用户勾选的来源产出”才真的进入新 Run，而不是只写在交接记录里。
6. **固定版本切换保持既有派发路径。** 显式 `application_version_id` 仍走
   `catalog.ResolveRunnableVersion(Tx)` + `requests.DispatchVersionTx`：提交事务直接建 published
   snapshot + Run + start job，**不**入 `route_request` job、不写候选/决策、不调控制模型；
   排队时由 `AdvanceConversationQueueTx` 在获得逻辑槽后走同一派发；撤销 409、不可见 404。
   本票不修改 `internal/routing`。
7. **数据库回填最小范围的硬边界。** `0051_handoff_scope_bounds.sql` 给
   `platform.request_handoffs` 加两个具名 CHECK：`cardinality(source_run_ids) BETWEEN 1 AND 5`、
   `cardinality(artifact_ids) BETWEEN 0 AND 5`。重复 migrate 是 no-op；worker/PUBLIC 仍零权限。
   这是 0030 记录的非契约偏差（表本身是 T074 的非契约表）。
8. **页面只确认服务端预览。** `chat.html` 增加“读取来源 Run 的产出附件 → 生成交接预览 →
   确认”的面板；预览摘要是只读 `<pre>`，`handoff-summary` 仅保存展示副本，确认框未勾选时
   本地即以 `handoff_not_confirmed` 拒绝。纯函数
   `handoffPreviewPath/handoffPreviewBody/describeHandoffPreview/handoffFromPreview/
   handoffArtifactOptions` 由 Node 执行；处理版本仍来自 `X-Request-Application-Version-Id/No`
   响应头（T074 约定），页面 `describeHandling` 显示 `vN 正在处理/已处理`。

## 后果

- T074 的隔离不变量保留，但成功交接的摘要来源改为预览：`test_t074_isolation.py` 的用例改为
  先调用预览再提交（同一断言集，不削弱隔离性）。这是 T082 对 T074 交接合同的有意收紧，见报告。
- 后续票可复用：`requests.HandoffPreview`/`ParseHandoffPreview`、
  `CodeHandoffScopeExceeded`、`MaxHandoffSummaryRunes=2000`、预览端点；F11 的删除传播覆盖
  `request_handoffs` 的 summary/artifact 引用不变。
- 已知限制：预览摘要只含元数据，不含运行结果正文；可交接附件限于来源产出与新请求显式附件；
  预览端点没有分页/批量；页面不驱动浏览器 DOM（Node 纯函数 + 真实 HTTP 结构测试边界不变）。
