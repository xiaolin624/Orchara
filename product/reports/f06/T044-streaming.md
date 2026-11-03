# T044 / issue #45 — 流式代次、半流失败与残缺工具调用

状态：**`passed`（本票范围内）**。真实栈证据来自真实 PostgreSQL（迁移 0001–0026）、编译后的
`cmd/api`、真实 worker mTLS、真实对象字节与真实公网 owner HTTP/SSE。模型提供方是唯一可用的
dev stub（`provider=stub`，每帧/回执 `stub=true`）；真实厂商 adapter 仍未编译，流式路径对真实
provider 诚实返回 `adapter_unavailable`，绝不伪造。

工作树 `E:\Orchara-wt\t044`，分支 `t/t044-streaming`，base `5eb3ee8`（T043 merge）。
未 push、未关闭 issue、未修改 `docs/spec`、`contracts/`、迁移 0001–0026；本票**没有新增迁移**
（generation id 复用 `platform.operation_attempts.id`，无需新列）。

## 1. 交付内容

### 1.1 provider 流式 seam（`internal/modelproxy`）

- `type StreamProvider interface { Provider; Stream(ctx, CallRequest, emit func(Chunk) error) (ProviderResponse, error) }`；
  `Chunk{Type:"delta"|"tool_delta", Text, ToolCalls []ToolCallDelta}`，
  `ToolCallDelta{Index, ID, Name, ArgumentsFragment}`。
- `(*Providers).StreamingFor(*Candidate) (StreamProvider, error)`：stub profile + `provider="stub"`
  才返回 stub；真实 candidate 返回 `ErrAdapterUnavailable`（`adapter_unavailable`），从不 fake。
- dev stub 的 `Stream` 先用与 `Call` 完全相同的确定性逻辑算出完整答案，再从 canonical params 切块：
  `stub_stream_chunks`（1..64，默认 3）、`stub_stream_fail_after`（N 块后返回
  `*TransientError`）、`stub_stream_fail_attempts`（默认 1，即只有第一代次失败、重试成功）、
  `stub_stream_incomplete_tool_call`（发一个 arguments 片段不是完整 JSON 的 tool_delta）、
  `stub_stream_delay_ms`（0..2000，供真人预览/断连测试）。参数已加入 T041 的 closed 白名单
  并在 `validateModelParams` 校验。
- 文本切分按 rune 确定性完成，`concat(chunks) == final text` 由测试断言。

### 1.2 能力流与代次语义（`internal/capabilities`）

- `(*Service).Stream(ctx, runID, role string, input Input, emit func(Frame) error) (*Result, error)`：
  - 与 `Invoke` 完全相同的 worker 角色、live attempt、manifest、params、operation/logical-key
    幂等规则；`capability_kind != "model"` 在写任何账本行前 422 `capability_kind_unsupported`。
  - 每次物理流尝试复用 `beginModelAttempt` 写一行 `operation_attempts`；该行 `id` 即帧的
    `generation_id`（UUID，持久可追溯），`operation_id` 为逻辑 operation。
  - 帧类型 `delta|tool_delta|final|error`，恒带 `operation_id` + `generation_id`。
  - 半流失败：attempt failed、operation 由 retry 路径回到 authorized 或最终 failed；
    typed transient 且还有尝试 → 发 `error{retryable:true}` 并开**新 generation** 从头流；
    旧代次文本/工具片段不拼接，final 只由成功代次的完整内容构建。
  - tool-call 按 index 缓冲 fragments；仅 id/name 非空且 arguments 片段能解析为完整 JSON
    object 的调用进入 final；残缺/畸形调用被丢弃、永不执行。幸存调用仍走 T041 的
    id 唯一/非空、name 在 manifest 声明、arguments 通过 input_schema、structured 输出 schema 校验。
  - 成功：校验“流式草稿 == provider 完整 text”，用成功代次完整内容 + 缓冲 tool calls 调
    `finishModelSuccess`，**只封一份** receipt、**只结算一次** attempt；final 帧携带
    `{text,structured?,tool_calls,usage,model_profile_version_id,stub}`、`receipt_artifact_id`、
    `usage`。
  - 客户端断开/取消：emit 失败或 ctx 取消 → 用 `context.WithoutCancel` + 5s 超时把 attempt/
    operation 记为 failed（`stream_cancelled`），绝不提交成功；该清理不会因请求 ctx 取消而丢账。
  - 幂等 replay：已成功 operation 只发一个 final（带成功 attempt 的 id 作为 generation），
    不再调用 provider、不新开 attempt。
- 帧结构：`Frame{Type, OperationID, GenerationID, Text, ToolCall{Index,ID,Name,ArgumentsFragment},
  Data, ReceiptArtifactID, Usage, Retryable, Error{Code,Message}}`；`isTerminalStreamFrame`
  以 final 或非 retryable error 为终态。

### 1.3 契约 SSE 端点（`internal/api`）

- `POST /internal/v1/runs/{run_id}/model-stream`（contract `streamModelCall`，worker mTLS）：
  body = 既有 `CapabilityInput`；响应 `text/event-stream`，`event:` 为帧类型、`data:` 为上述
  JSON。授权失败发生在第一帧之前 → 返回真实 JSON status/code（403/404/409/422/503）；一旦
  已开始 SSE，错误以 `error` 帧结束且不写第二个 JSON body。帧在写出前发布到预览 hub。
- 持久 Run SSE `GET /api/v1/runs/{id}/events` 未改动；测试断言流式调用前后 `platform.run_events`
  行数不变（delta 不是 run event，不持久）。

### 1.4 owner 临时预览（非契约）

- `GET /api/v1/runs/{run_id}/model-preview`：session/API key + `application:read`，
  可见性严格等于 getRun（未知/格式错/已删/非 owner 同为 404 `resource_not_found`）。
- 进程内 `previewHub`：按 run 的 ring buffer（256 帧、60s TTL、每客户端 64 帧队列），
  **不持久、不写 run_events**；单进程结构（多进程 fan-out 未实现，报告 §5 标注）。
- 新 generation 到达时清空该 run 的旧缓冲（旧 delta 不会与新代次并排/拼接）；`final` 或
  非 retryable `error` 后缓冲标记 terminal，预览回放后关闭；retryable error 不关闭，下一
  generation 替换草稿。run 已终态且无缓冲 → 404（临时预览不是历史，持久快照/事件是权威）。

### 1.5 页面（`apps/web/src`）

- 新增共享 `assets/stream-preview.js`：`previewPath`、`emptyPreviewState`、
  `parsePreviewEvent`、`applyPreviewFrame`、`describePreview`、`renderPreviewPanel`、
  `beginModelPreview`。纯 helper 由 Node 执行测试；浏览器 glue 使用 `EventSource`。
- `/debug`（`#run-preview`）与 `/runs`（`#detail-preview`）用真实临时预览面板替换
  “未实现”占位文案；显示 generation/operation、流式文本、工具片段“待定、未执行”，
  新 generation 整块丢弃旧草稿，final/error 后关闭并指向持久 Run 快照/事件。
- 页面只用 `textContent`/`replaceChildren`，无 `innerHTML`、无 localStorage/sessionStorage、
  无额外 fetch。

### 1.6 Python worker 流式路径

- `worker/platform_worker/agents/adapter.py`：
  `StreamingCapabilityTransport.stream(run_id, body, on_frame)` 协议；
  `PlatformChatModel.stream_generate(...)` 每次 generate **一条物理流、无隐藏重试**；
  校验每帧 operation/generation/type；新 generation 重置草稿；转发全部帧给预览；
  只有 final 的 `data` 经 `_validate_generation`（T041 同规则）后才返回 `Generation`；
  无 final → `stream_incomplete`；终态 error → 平台码；草稿与 final text 不一致 → 校验错误。
- `agent.py`：`stream_agent(...)` 用 `_StreamingLoopModel` 把 `stream_generate` 接到既有
  `run_agent` 循环；只有 final 的完整 tool calls 能被 executor 执行，残缺片段只出现在预览帧。
- Go agent loop（T043）仍使用非流式 `invokeCapability`，本票未改其语义（边界如实记录）。

## 2. 测试

### 2.1 Go 单元（先红后绿）

- `internal/modelproxy/stream_test.go`（6）：stub 确定性/分块/拼接一致；第一代次 N 块后
  transient 失败、第二代次完整；完整 tool-call delta；残缺 tool-call delta 不是完整 object；
  real provider `ErrAdapterUnavailable`；real profile 下 stub 不可流。
- `internal/capabilities/stream_test.go`（5）：代次文本/工具缓冲；残缺调用丢弃；两代次缓冲
  永不混合；非 object arguments 不完成调用；终态帧分类（retryable error 非终态）。
- `internal/api/preview_test.go`（3）：新 generation 整块替换旧缓冲；retryable error 不关闭、
  非 retryable error 关闭；TTL 后不重放。

### 2.2 真实栈 `tests/f06/`（真实 PG + 编译二进制 + mTLS/HTTP/SSE + 对象字节）

`test_t044_streaming.py`（11）+ `test_t044_pages.py`（4）+ `test_t044_python.py`（7）共
**22 passed**（真实 PG + 编译二进制 + mTLS/HTTP/SSE + 对象字节 + Node + P01 Python）。

`test_t044_streaming.py`（11）：

1. 单代次帧序 `delta×3 + final`；每帧 operation/generation；final text = 拼接 delta；
   attempts=1、operation succeeded、receipt 与 attempt 互指、对象字节 text 一致；
   run_events 不增长；owner 账本 1 attempt/stub。
2. `stub_stream_fail_after=1`：gen1 delta → retryable error → gen2 delta×3 → final；
   两 generation id 不同；final 只等于 gen2 文本、不等于 gen1+gen2；attempts=[failed,
   succeeded]、恰好一份 receipt、gen2 id = succeeded attempt id。
3. 残缺 tool_delta：final `tool_calls=[]`、receipt 无调用、账本无调用、无工具 operation。
4. 完整 tool_delta：final/receipt/账本均为 `[call-1 lookup {city:Paris}]`。
5. 过期 attempt token 403 `stale_attempt`、supervisor cert 403 `forbidden`、
   `capability_kind=tool` 422 `capability_kind_unsupported`，均无 operation 行。
6. 取消后的 run 409 `run_cancelled`，无 operation 行。
7. 真实 TLS socket 读到第一帧后 RST 断开：attempt failed + `stream_cancelled`、operation failed、
   无 receipt（绝不成功）。
8. owner 预览（半流失败后）：只回放当前 gen2 帧（3 delta + final）并关闭；
   非 owner 404。
9. 并发 preview 线程 + 真实延迟流：预览收到与 model-stream 相同的帧序与 final。
10. run 已终态且无预览缓冲：404 `resource_not_found`。
11. 重放已失败的 operation：只回放 `error` 帧、无 final、attempt 数不变（幂等不伪造成功）。

`test_t044_pages.py`（4）：`/debug`、`/runs` 静态面板与共享资产；`stream-preview.js` 无持久化
/无 innerHTML/无 fetch；Node 执行 helper：URL 编码、拒绝坏帧、新旧代次整体替换（`old` 不拼入
`new`）、残缺工具只 pending 未执行、final 权威替换并关闭、非 retryable error 终态。

`test_t044_python.py`（7）：旧代次丢弃、final 唯一来源；`stream_agent` 只执行 final 的
完整调用（partial 只转发不执行）、每 generate 一条流；缺 generation id 拒绝；无 final 失败；
草稿/final 不一致拒绝；未声明工具拒绝；transport 失败一次且 typed。

### 2.3 回归

- `tests/f06` 全量（含 T039/T041/T042/T043 与本票）见 §3 原始尾部。首两次全量运行各出现一次
  与本票无关的沙箱/租约计时 flake（T043 cancel、T042 python node），对应测试单独重跑均通过；
  以下尾部为最终全量运行。
- `tests/f04a` 36 passed（使用 P01 locked interpreter，见 §3/§5）。
- `tests/tooling` 21 passed；`sync_spec --check` no drift。

## 3. 门禁（原始输出尾部）

环境：`GOCACHE=E:\orchara-toolchains\gocache`、`GOMODCACHE=E:\orchara-toolchains\gomodcache`、
Go `E:\orchara-toolchains\go\bin\go.exe`、PG 127.0.0.1:55432、`ORCHARA_P01_PYTHON=E:\Orchara\product\probes\agent_min\.venv\Scripts\python.exe`。

```text
> go vet ./...
vet exit: 0
(no output)

> go test ./... -count=1
ok  	orchara/platform/internal/models	...
ok  	orchara/platform/internal/modelproxy	0.7s
ok  	orchara/platform/internal/objectstore	...
ok  	orchara/platform/internal/policy	...
...
ok lines: 24
FAIL lines: 0
test exit: 0

> gofmt -l internal cmd tools
(empty)

> python -m pytest tests/f04a -q
36 passed in 78.06s (0:01:18)

> python -m pytest tests/tooling -q
21 passed in 6.20s

> python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
sync exit: 0
```

```text
> python -m pytest tests/f06 -q
1 failed, 83 passed in 409.40s (0:06:49)
  FAILED tests/f06/test_t043_agent_node.py::test_agent_turn_limit_fails_without_a_final_output
  → "the attempt lease was lost while the sandbox ran" / heartbeat context canceled
    （T042/T043 既有 supervisor 心跳/租约计时 flake，与本票改动路径无关；隔离重跑通过）

> python -m pytest tests/f06/test_t043_agent_node.py -q
6 passed in 84.26s (0:01:24)

> python -m pytest tests/f06/test_t044_streaming.py tests/f06/test_t044_pages.py \
                     tests/f06/test_t044_python.py -q
22 passed in 81.02s (0:01:21)
```

## 4. T045/T046 可用的精确接口

### 4.1 契约 HTTP（worker mTLS）

```text
POST /internal/v1/runs/{run_id}/model-stream      (operationId streamModelCall)
  body = CapabilityInput{attempt_token, operation_id, logical_key,
                         capability_version, params, capability_kind:"model"}
  200 text/event-stream
  event: delta      data:{type:"delta", operation_id, generation_id, text}
  event: tool_delta data:{type:"tool_delta", operation_id, generation_id,
                          tool_call:{index,id?,name?,arguments_fragment?}}
  event: error      data:{type:"error", operation_id, generation_id,
                          retryable?:true,
                          error:{code,message}}
  event: final      data:{type:"final", operation_id, generation_id,
                          data:{text, structured?, tool_calls:[{id,name,arguments}],
                                usage?, model_profile_version_id, stub},
                          receipt_artifact_id, usage?}
```

- 终态 = `final` 或非 retryable `error`；retryable error 后会出现**新 generation_id** 的帧，
  消费方必须丢弃旧 generation 的草稿。
- 授权/幂等错误在首帧前仍为 JSON `{error:{code,message,request_id}}`（403 stale_attempt/
  forbidden、404 run_not_found、409 run_cancelled/operation_*、422 validation_failed/
  capability_kind_unsupported/model_profile_unavailable、503 provider_*）。
- 流式调用的 operation/attempt 与 T041 相同：`platform.operations` 一行、物理尝试
  `platform.operation_attempts` 每代一行，成功仅一份 `purpose=receipt` artifact。

### 4.2 owner 预览（非契约）

```text
GET /api/v1/runs/{run_id}/model-preview   (session/API key, application:read, owner=getRun)
  200 text/event-stream，帧格式与 model-stream 完全相同
  - 先回放当前 generation 的缓冲，再推 live 帧
  - 新 generation 清空旧缓冲；final/非 retryable error 后关闭
  - run 终态且无缓冲 → 404 resource_not_found
  - 单进程 ring buffer：256 帧 / 60s TTL / 每客户端 64 帧；不持久、不写 run_events
```

### 4.3 Go

```go
modelproxy.StreamProvider.Stream(ctx, CallRequest, emit func(Chunk) error) (ProviderResponse, error)
modelproxy.Chunk{Type, Text, ToolCalls []ToolCallDelta}
modelproxy.ToolCallDelta{Index, ID, Name, ArgumentsFragment}
modelproxy.Providers.StreamingFor(*Candidate) (StreamProvider, error)

capabilities.(*Service).Stream(ctx, runID, role string, Input, emit func(Frame) error) (*Result, error)
capabilities.Frame{Type, OperationID, GenerationID, Text, ToolCall *ToolCallFrame,
                   Data, ReceiptArtifactID, Usage, Retryable, Error *StreamError}
capabilities.CodeStreamCancelled = "stream_cancelled"
```

### 4.4 Python

```python
PlatformChatModel(transport, run_id=, attempt_token=, capability_version=)
  .stream_generate(*, messages, tools=None, response_schema=None, max_output_tokens=None,
                   logical_key=None, operation_id=None, on_frame=None) -> Generation
StreamingCapabilityTransport.stream(run_id, body, on_frame) -> None   # 一条物理流，无重试
stream_agent(*, model, tools, executor, messages, response_schema=None,
             max_turns=4, on_frame=None) -> NodeOutcome
```

### 4.5 页面 helper

`stream-preview.js`：`previewPath(runID)`、`emptyPreviewState()`、
`parsePreviewEvent(eventName, data)`、`applyPreviewFrame(state, frame)`、
`describePreview(state)`、`beginModelPreview(runID, {panelID})`。

## 5. stub / real 分界与 not_run（如实）

- **stub（本票全部通过证据）**：`provider="stub"`、`stub=true` 的 dev-stub 流；
  所有 delta/final/tool 片段、重试、残缺调用、预览、断连测试都基于它。没有任何真实厂商
  响应被使用或伪造。
- **real（未实现，诚实失败）**：真实 vendor adapter 仍未编译。真实 candidate 的
  `Available` 在 T041 已按 `provider_not_configured`/`adapter_unavailable` 失败；T044 的
  `StreamingFor` 同样返回 `adapter_unavailable`，不产生账本行、不返回假流。
- `not_run`：
  - 真实厂商模型流式 adapter 与凭据解析（本构建无协议 adapter）。
  - 浏览器 DOM 驱动（`beginModelPreview` 的 EventSource 渲染）；本票仅 Node 执行纯 helper，
    与本仓库 T041/T042/T043 的页面边界一致。
  - 多进程/多副本预览 fan-out（当前明确为单进程 in-process hub）。
  - 全量 `pytest tests` 与全量 `tests/f00b`（Windows 管道挂起风险，按 brief 不跑）。
  - `tests/f05`/`tests/f03` 全量未跑（按 brief 不跑；本票未改其代码路径，仅 `internal/api`
    新增路由/共享 capability service 的 `beginModelAttempt` 返回值改动，已由 `go test ./...`
    与 `tests/f06` 覆盖）。
  - T045/T046 的预算结算、真实模型切换与 UI 深度集成不在本票。
- 已知边界：`beginModelAttempt` 改为返回 attempt id（仅内部调用点更新）；T041 的
  `finishModelSuccess` 复用（未改其 receipt 形状）；持久 Run SSE 与 run_events 未改。
