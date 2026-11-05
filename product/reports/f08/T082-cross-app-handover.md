# F08 T082 验收：切换处理应用前确认最少交接信息

- 任务：F08 / GitHub Issue #83（票 `T082`，规划 ID F08.05），分支 `t/t082-cross-app-handover`
  （worktree `E:\orchara-worktrees\t082`）；基线 `integration/t068-t082` @ `d24cacb`，父门禁 F08。
- 状态：**`passed`**（本票范围 = 服务端最小交接预览 + 摘要/附件确认门禁 + 来源产出进入新 Run
  输入闭包 + 固定版本切换零路由/零控制调用 + 撤销/外部版本拒绝 + 队列/槽语义 + `/chat`
  交接预览面板与 Node 可执行纯函数；真实 PostgreSQL 16.4、编译二进制、真实 cookie+CSRF HTTP、
  真实封存对象字节、Node 24。模型路由内部、子 Run/预算、发布/撤销/删除实现不在本票范围。）
- 边界（如实）：交接摘要只含元数据（应用名/版本号/Run id/时间/附件 purpose/type/size/sha 前缀），
  **不含**运行结果正文；页面不驱动浏览器 DOM（与 T023/T027/T033/T074/T079 同一边界）；全量
  `pytest tests` 未运行（BRIEF §3 明确禁止）。
- 迁移：`db/migrations/0051_handoff_scope_bounds.sql`（本树 tail 为 0050；T076/T081 合并时会按
  BRIEF §5 重编号；测试按 slug `_handoff_scope_bounds.sql` 断言，不 pin 编号）。
- 决策：`docs/adr/0030-t082-cross-app-handover.md`（非契约预览端点、单一元数据摘要生成器、
  提交必须携带服务端预览原文、附件范围、Run 输入闭包扩展、数据库硬边界、页面确认语义）。
  契约与 `docs/spec/**` 未改动，`python tools/sync_spec.py --check` 无漂移。
- 最终合并：`integration/t068-t082` 合并时点 `<待填>`，合并提交 **`<待填>`**。

## 0. 工作记录（简短）

1. 读票面、`BRIEF.md`、`notes/f08-conversations.md` §8（Handover design）、§5（状态机/队列）、
   §6（router）、§9（UI）、§11（复用接口）；打开 `internal/requests/{service,submit,store,reply}.go`、
   `internal/routing/advance.go`、`internal/catalog/{publication,versions}.go`、
   `internal/api/{requests,server}.go`、`apps/web/src/{chat.html,assets/chat.js}`、
   `tests/support/{f08,t077,t079}.py`、`0044` 迁移与契约 `RequestInput.handoff`。
2. TDD 垂直切片（先写失败测试、再最小实现）：
   - 固定版本切换：显式版本提交即建 published Run；零 `route_request` job、零候选/决策、零控制
     模型调用；排队切换在上一 Run 终止后由 `AdvanceConversationQueueTx` 直接派发；
   - 权限：不可见版本 404、真实 read grant 后允许且 Run owner=调用者、撤 grant 后再次 404、
     撤销版本 409；
   - 预览：真实 published 来源 Run + 真实 ready 附件 → 服务端摘要只含元数据、可复算、≤2000 rune；
     6 个来源/附件 `handoff_scope_exceeded`；外部/跨会话 Run `handoff_source_unknown`；
     外来附件 `artifact_not_visible`；删除会话 404；
   - 门禁：未确认 `handoff_not_confirmed`、手写转录/记忆正文/篡改摘要 `handoff_scope_exceeded`、
     范围外附件 `handoff_scope_exceeded`、6 个 id 越界、同 key 重放/异体 409；
   - 隔离：新 Run 输入闭包 = 新消息 + 显式附件 + 确认来源产出 + 摘要；所有封存字节无旧
     历史/记忆 marker；记忆列表按 `(owner, application)` 隔离（旧应用仍可见自己的，新应用只见自己的）；
   - 页面：`/chat` 交接面板结构 + Node 执行预览 body/摘要投影/确认对象/附件选择/处理版本。
3. 跑门禁（§3）：`tests/f08` 两次背靠背、`tests/tooling`、`go build/vet/test`、`gofmt -l`、
   `sync_spec --check`；写本报告/ADR 0030/PROGRESS 行；未推送、未关 issue、未改契约/规格。

## 1. 交付内容

### 1.1 服务端最小交接预览（非契约，ADR 0030）

- `internal/requests/handoff.go`
  - `POST /api/v1/conversations/{conversation_id}/handoff-preview`（`internal/api/requests.go`
    `handleHandoffPreview`，`server.go` 注册）：只读、写零行、无 Idempotency-Key；cookie 会话
    仍要求 CSRF，API key 仍要求 `application:read`。
  - `ParseHandoffPreview`：封闭成员 `{source_run_ids, artifact_ids}`；UUID/去重/上限；
    超过 5 个来源 Run 或 5 个附件 → `422 handoff_scope_exceeded`（`details.field`）。
  - `HandoffPreview`：校验会话 owner 存活、来源 Run 属于调用者且同会话且绑定 published 版本
    （否则 `422 handoff_source_unknown`）、附件 owner/ready/sealed（T074 共享
    `checkArtifacts`），返回
    `{conversation_id, from_version_id, source_run_ids, artifact_ids, summary, summary_sha256,
    source_runs[], artifacts[], limits}`；`limits` 固定
    `{max_source_runs:5, max_artifacts:5, max_summary_runes:2000}`。
  - `buildHandoffSummary`：唯一摘要生成器，只读**元数据**（应用名、版本号、Run id/创建时间、
    附件 purpose/media type/size/sha256 前缀）；字段按 rune 裁剪、总长硬上限 2000 rune；
    无时间戳/随机值，重复调用字节一致。
- `Submit` 门禁（`submit.go` + `verifyHandoffScope`）：
  - 摘要非空（T074 原语义）后，用同一读取/生成逻辑重算预览；
  - 附件范围 = 来源 Run 的 `run_artifacts.relation <> 'input'`（来源产出）∪ 本次
    `RequestInput.artifact_ids`；越界 → `422 handoff_scope_exceeded` + `details.artifact_ids`；
  - `handoff.summary` 与重算摘要不一致 → `422 handoff_scope_exceeded`
    （`details.field=handoff.summary`, `reason=summary_not_server_generated`）；
  - 数量越界在解析阶段即 `422 handoff_scope_exceeded`；`confirmed:false` 仍
    `422 handoff_not_confirmed`；外来/跨会话来源 Run 仍 `422 handoff_source_unknown`。
- `runInputArtifactIDsTx` 扩展：新 Run 输入闭包 = 消息 artifact ∪ `requests.artifact_ids` ∪
  `request_handoffs.artifact_ids` ∪ summary artifact；测试证明“只经交接确认、不在新请求
  artifact_ids 里的来源产出”确实进入新 Run 封存输入。

### 1.2 固定版本切换（证明无需伪造路由）

- 复用 `catalog.ResolveRunnableVersion(Tx)` + `requests.DispatchVersionTx`，未修改
  `internal/routing`：提交事务直接创建 published snapshot + Run + `start` job；
  无 `route_request` job、`request_routing_candidates`/`decisions` 零行、
  `platform_model_calls` 零行。
- 处理版本：`X-Request-Application-Version-Id` / `-No` 在提交 202 与 `GET /requests/{id}`
  返回；排队时无该头（页面显示“尚无处理应用”）。真实 v2 来源证明返回的是版本 2 而非应用级猜测。

### 1.3 队列/切换语义

- 一个会话同时只有 1 个 live 顶层 Run：A 运行中提交 B/C 固定版本 → B/C `queued` 且零 Run；
  A 的 Run 经既有 `POST /runs/{id}/cancel` 终止后，读 B 触发 `AdvanceConversationQueueTx` →
  B 直接派发到自己的固定版本，C 仍 `queued`；B 终止后 C 同样推进。每一步都断言
  `live_top_runs` 恰为 1 且请求/运行行是权威事实。

### 1.4 数据库硬边界（`0051_handoff_scope_bounds.sql`）

- `platform.request_handoffs` 新增具名 CHECK：
  `request_handoffs_source_run_ids_bound`（`cardinality(source_run_ids) BETWEEN 1 AND 5`）、
  `request_handoffs_artifact_ids_bound`（`cardinality(artifact_ids) BETWEEN 0 AND 5`）。
- 重复 `admin migrate` 为 no-op；worker/PUBLIC 零授权；真实 INSERT 6 个 id 被数据库拒绝，
  5 个 id 被接受。

### 1.5 页面（`chat.html` / `assets/chat.js`）

- 交接区：来源 Run 输入 → “读取来源 Run 的产出附件”（真实 `GET /runs/{id}/artifacts`，
  排除 `relation=input`）→ 勾选附件 → “生成交接预览” → 只读预览摘要/范围提示 →
  确认框。未生成预览或未确认时本地拒绝提交；提交体只来自服务端预览
  （`handoffFromPreview`），摘要不可编辑。
- 处理版本：“处理版本：<span id=chat-handling>” + `describeHandling` / `requestFacts`
  读取 `X-Request-Application-Version-Id/-No`。
- 纯函数：`handoffPreviewPath`、`handoffPreviewBody`、`describeHandoffPreview`、
  `handoffFromPreview`、`handoffArtifactOptions`、`handoffSourceRunIDs`、`runArtifactsPath`；
  T074 的 `handoffBody` 保留（既有 Node 测试）。
- 页面不写 `localStorage`/`sessionStorage`，不嵌入 object key/凭据。

## 2. 验收逐条

| AC | 结果 | 证据（真实执行） |
|---|---|---|
| 固定版本测试不需伪造路由 | **passed** | `test_a_fixed_version_switch_creates_the_run_directly_and_keeps_the_queue`：A/B/C 三次提交均零 `route_request` job、零候选/决策、零 `platform_model_calls`；Run 绑定显式版本 |
| 新应用不读旧内部历史/记忆 | **passed** | `test_the_new_application_never_reads_the_old_history_or_memory`：新 Run 输入闭包恰为 {新消息, 显式附件, 确认摘要}；读回每个封存字节，旧请求 marker/旧记忆 marker 均不存在；记忆按应用隔离；`test_the_confirmed_handoff_artifact_reaches_the_new_run`：仅经交接确认的来源产出进入新 Run |
| 用户可看清处理者版本 | **passed** | 同一切换测试断言 202/200 的 `X-Request-Application-Version-Id/-No`（真实 v2），排队时无该头；`test_chat_js_handover_helpers_execute_under_node` 执行 `describeHandling`/`requestFacts`；页面结构断言 `id="chat-handling"` 与“处理版本” |
| 失败/当前授权/重试/恢复边界有断言 | **passed** | `test_a_fixed_version_switch_keeps_the_callers_permissions`：不可见 404、read grant 后允许且 Run owner=调用者、撤 grant 后 404、撤销 409 且零新请求/运行；`test_a_confirmed_server_preview_is_required_for_the_switch`：未确认/篡改摘要/转录/记忆正文/范围外附件/越界均 located 422，同 key 异体 409、同体重放同一请求；`test_the_handover_preview_is_a_bounded_server_summary`：外部 Run/跨会话/外部附件/删除会话 refusal |
| 记录版本、输入/hash、命令、原始结果与环境 profile | **passed** | 本报告 §3；预览 `summary_sha256` 与服务端重算一致；封存 artifact 行记录 `sha256/size`；stub/real 分报见 §4（本票无模型调用） |

## 3. 门禁命令与原始输出

环境：Windows PowerShell；`$env:PATH="E:\orchara-toolchains\go\bin;..."`，
`GOCACHE=E:\orchara-toolchains\gocache`，`GOMODCACHE=E:\orchara-toolchains\gomodcache`；
PostgreSQL 16.4 用户态集群 `127.0.0.1:55432`（`tools/pg_dev.py status`）；Go 1.27.3；
Node 24；每轮 `pytest` 使用独立 `--basetemp=E:\orchara-tmp\t082-*`。

```text
$ python -m pytest tests/f08 -q --basetemp=E:\orchara-tmp\t082-full1
.................................................................        [100%]
65 passed in 825.37s (0:13:45)
```

```text
$ python -m pytest tests/f08 -q --basetemp=E:\orchara-tmp\t082-full2
.................................................................        [100%]
65 passed in 799.41s (0:13:19)
```

```text
$ python -m pytest tests/tooling tests/f11/test_t071_conversation.py -q \
    --basetemp=E:\orchara-tmp\t082-adjacent
......................                                                   [100%]
22 passed in 27.70s
```

```text
$ go build ./...            -> exit 0
$ go vet ./...              -> exit 0
$ go test ./...             -> exit 0 (37 "ok" packages, 0 FAIL)
$ gofmt -l .                -> clean (no output)
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

## 4. not_run / 边界

- `not_run`：全量 `pytest tests`（BRIEF §3 禁止，>60 分钟）；浏览器 DOM 驱动（与既有票同一边界，
  页面逻辑用 Node 执行纯函数 + 真实 HTTP 结构测试）；真实模型路由/控制预算内部（T079 范围，
  本票固定版本路径刻意零模型调用）；子 Run/预算/等待（T078/T080/T081）；发布/撤销/删除实现
  （T068/T070/T069/T071，本票只消费其现有行为）；真实厂商模型（本票无模型调用）。
- **测试变更披露**：`tests/f08/test_t074_isolation.py` 的成功交接改为“先调用 T082 预览、再提交
  预览摘要”。T074 的隔离断言集不变；这是 T082 对交接摘要的有意收紧（自由文本摘要无法证明
  “最小范围”），见 ADR 0030 §决策 3。T074 的 refusal 用例与页面用例未改动。
- 已知限制：预览摘要只含元数据（不含运行结果正文）；交接附件范围限于来源产出 + 新请求显式附件；
  预览端点无分页/批量；来源 Run 必须绑定 published 版本（debug Run 不在会话中，天然不适用）。

## 5. 复用接口（给后续票）

- `requests.Service.HandoffPreview` / `ParseHandoffPreview`；错误码
  `handoff_scope_exceeded` / `handoff_source_unknown` / `handoff_not_confirmed`；
  常量 `MaxHandoffSourceRuns=5`、`MaxHandoffArtifacts=5`、`MaxHandoffSummaryRunes=2000`。
- 非契约 `POST /api/v1/conversations/{id}/handoff-preview` 响应形状与 `limits`（ADR 0030）。
- `requests.DispatchVersionTx` 的固定版本语义不变；`runInputArtifactIDsTx` 现在包含
  `request_handoffs.artifact_ids`，T079 路由派发共用该闭包。
- `platform.request_handoffs` 的数据库边界（0051）与 T074 的删除传播 scope 不变。
- 页面纯函数 `handoffPreviewPath/handoffPreviewBody/describeHandoffPreview/handoffFromPreview/
  handoffArtifactOptions` 可被后续目录/请求页复用。

## 6. 风险与自查

- 严格“摘要必须等于服务端预览”会拒绝任何未走预览的客户端；这是产品选择的确认语义，
  T074 的旧自由文本行为不再可用（报告已披露测试迁移）。
- 预览与提交之间来源 Run/附件若被删除，提交会以 located 422/404 拒绝；没有为交接做跨事务
  快照表（本票范围外，删除传播仍由 F11 承担）。
- `handoffArtifactFacts` 在 `checkArtifacts` 与读取之间若行消失会返回 500（内部一致性错误，
  非用户输入可触发）；与 T074 的 artifact 预检风格一致。
- 数据库 CHECK 与 Go 上限重复是有意的 defense-in-depth；若后续票放宽上限，两处必须同时改。
