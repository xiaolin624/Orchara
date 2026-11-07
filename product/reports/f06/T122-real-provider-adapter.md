# T122 — 真实厂商模型 adapter（OpenAI-compatible chat completions）

- **票**：GitHub #135（T122，epic F06）；分支 `t/t122-real-provider-adapter`
- **状态**：`partial`
  - 协议 adapter、Go 协议测试、配置接线、产品路径 e2e（fake provider）**已执行**；
  - **真实厂商 end-to-end：`blocked`**（本机无获批厂商端点/凭据/配额/外发批准，见 §8）。
- **环境 profile**：`dev-stub`（API 进程显式携带受控模型端点）；PostgreSQL 16.4 隔离集群
  `127.0.0.1:55432`；Go 1.27.3；Python 3.12.2；Node 24.13.0。无 GPU、无真实浏览器 DOM。
- **切片边界**：只实现一个通用协议（OpenAI-compatible chat completions）；不引厂商 SDK；
  `provider=stub` 与 stub 通路行为不变；不新增管理页面、契约端点或迁移。

## 1. 实现范围

| 文件 | 变更 |
|---|---|
| `product/internal/modelproxy/openai.go`（新） | chat-completions adapter：`Available` 目的地/协议/凭据门禁，`Call`（一次物理 HTTP），`Stream`（SSE 映射），错误分类，canonical `Output`，价格→`ChargedUnits` |
| `product/internal/modelproxy/real.go` | `realProvider` 增加 catalog 字段；`Available` 从无条件 `ErrAdapterUnavailable` 改为真实门禁；`Call` 走 adapter |
| `product/internal/modelproxy/provider.go` | `Candidate.Config` 的注册配置由 adapter 读取；`Providers` 移除未用字段 |
| `product/internal/modelproxy/modelproxy.go` / `stream.go` | 包级过时注释（"无 vendor adapter"）改为 T122 事实 |
| `product/internal/config/config.go` | `dev-stub`/`capacity-stub` 下显式 `ORCHARA_MODEL_ENDPOINT`+`ORCHARA_MODEL_SECRET_REF` 成对读出；半配置拒绝启动；`real-model` 语义不变 |
| `product/cmd/api/main.go` | 先构建 deployer catalog，再 `NewProviders(...).WithCatalog(catalog)` |
| `product/internal/capabilities/{service,stream}.go` | 构造 `modelproxy.Candidate` 时带上 `Config`（capability + fallback + stream 路径）；错误码映射 |
| `product/internal/capabilities/errors.go` | 新增 `provider_destination_not_approved`、`provider_response_too_large` |
| `product/internal/api/jobdata.go` | T024 验证路径：transient/目的地/状态拒绝的稳定错误码映射（不再落入 500 internal） |
| `product/internal/background/handlers/model_validate.go` | 新错误码 → 业务失败（不重试）或基础设施失败（有界重试），保持固定重试表 |
| `product/internal/modelproxy/openai_test.go`（新） | 19 条协议测试（本地 `httptest` fake HTTP provider） |
| `product/internal/modelproxy/stream_test.go` | 更新 T044 时代"真实 provider 流式必 adapter_unavailable"的断言（见 §9） |
| `product/internal/config/config_test.go` | stub profile → 显式端点 3 条测试 |
| `product/tests/support/t122.py`（新） | 真实栈 helper：deployer catalog env、模型登记 SQL、loopback fake provider（JSON/SSE/半段/错误状态） |
| `product/tests/f06/test_t122_real_provider.py`（新） | 11 条真实栈 e2e |

未改 `product/docs/spec/**`、`product/contracts/**`、数据库迁移；未改其它票的断言（除 §9 记录的
一条被本票语义取代的 T044 断言）。

## 2. 协议与映射决策

### 2.1 端点与请求

| 决策 | 规则 |
|---|---|
| 端点来源 | **只有部署配置** `ORCHARA_MODEL_ENDPOINT`；候选行的 `endpoint_ref` 只是"已批准目的地引用"，永不当作 URL |
| URL 拼接 | `endpoint` 去掉尾部 `/` 后：已以 `/chat/completions` 结尾 → 原样使用；否则追加 `/chat/completions`（e2e/单测都断言不重复拼接） |
| 方法/头 | `POST`；`Content-Type: application/json`；`Accept: application/json`（流式 `text/event-stream`）；`Authorization: Bearer <secret>`（凭据只出现在这一处） |
| 重定向 | 不跟随（3xx = 终止性 provider 拒绝，code `provider_redirected`） |
| 转义重试 | `req.GetBody = nil`，禁止 Go transport 重放该 POST；adapter 自身无任何重试循环 |
| 超时 | 每次物理请求：注册 `config.timeout_ms`（夹在 50ms..10min），缺省 30s；调用方 ctx 取消原样返回 |
| 上限 | 响应体 8 MiB（流式按整条流累计，单行 1 MiB）；超限终止、不截断解析 |
| User-Agent | 不设置（不泄露内部组件信息） |

### 2.2 请求字段映射

| 平台 params / 注册配置 | 出站字段 | 说明 |
|---|---|---|
| `candidate.model_id` | `model` | 登记模型 id 原样发送 |
| `params.messages[]`（`role`+`content`） | `messages[]` | 关闭词表：仅 system/user/assistant/tool + content |
| `params.tools[]`（`name`+`capability_version`） | `tools[]`（`type=function`, `function.name`） | 平台 params 只声明工具名、不带 schema；线协议用最小 `{"type":"object","additionalProperties":true}`，**返回的 arguments 由 capability 校验器按登记的 tool input_schema 复核**；`capability_version` 不外发 |
| `params.response_schema` | `response_format={"type":"json_schema","json_schema":{"name":"orchara_response","schema":...}}` | 未设 `strict`（平台 schema 子集不保证 OpenAI strict 的全部约束）；平台随后再校验 `structured` |
| fixture 调用（无 messages） | `response_format={"type":"json_object"}` | job-scoped 验证 / routing control 的服务器自建 prompt |
| `params.max_output_tokens` → 缺省 `config.max_output_tokens` | `max_tokens` | 只发正值 |
| `config.temperature` | `temperature` | 仅登记时发送 |
| 流式 | `stream=true`、`stream_options={"include_usage":true}` | usage-only 帧被映射到 `usage` |

### 2.3 响应映射（→ `ProviderResponse`）

| 线上字段 | 平台字段 | 说明 |
|---|---|---|
| `choices[0].message.content` | `Data["text"]` | null → `""` |
| `choices[0].message.tool_calls[].{id,function.name,function.arguments}` | `Data["tool_calls"]` = `[{id,name,arguments}]` | `arguments` 是 JSON 字符串：能解析为 JSON 值则原样放入；解析失败保留**原始字符串**，让 capability 校验器以 `provider_response_invalid` 拒绝（不重试、不伪造对象）；空串 → `{}` |
| content 为 JSON 对象且请求了 `response_schema`（或 fixture 调用） | `Data["structured"]` | 解析失败则**不带** `structured`，由既有校验器 422 拒绝并结算 usage（不重试） |
| `usage.prompt_tokens` / `usage.completion_tokens` | `Data["usage"]={input_tokens,output_tokens}` + `TokensIn/TokensOut` | 缺字段/负值 → 不带 usage（保留 T046 的 `usage_missing` 保守 reservation） |
| `usage` + `config.price` | `ChargedUnits`（1e-8 货币单位） | 复用 `internal/budget.ParsePrice/ActualCharge` 的 decimal 口径；无价格/价格无效 → 0，tokens 仍如实记账 |
| （adapter 生成） | `Data["provider"]`/`Data["model_id"]`/`Data["stub"]=false` | 真实调用永不带 stub 标签 |
| `Data` 的 RFC 8785 canonical 字节 | `Output` | 即 `modelproxy.Invoke` 封存的 artifact 字节；e2e 断言 artifact sha256 == sha256(bytes) |

### 2.4 job-scoped fixture 模式（无 messages）

平台有两种"服务器自建 prompt + fixture 字节"的调用：T024 兼容性验证（`task=model_compatibility`）与
F08 routing control（`task=route`）。adapter 把 fixture 作为 user prompt、附固定 system 指令发送：
- `model_compatibility`：要求返回一个 JSON 对象；返回对象 → `Data["structured"]` 且
  `Data["check"]={"name":<fixture 的 check>,"passed":true,"reason":""}`；非对象 →
  `check.passed=false, reason="structured_output"`（业务 verdict，不是 job 失败）。
- `route`：把返回对象里的 `decision`/`candidate_version_id`/`reason` 提升到 `Data` 顶层，
  既有 routing `parseDecision` 无需改动即可读取；模型给不出对象时不伪造决定。
- 既无 messages 又无 fixture → 终止性 `ErrCallInvalid`（`provider_call_invalid`）。

## 3. 错误分类

| 条件 | Go 类型 | 稳定 code | 是否重试 | 备注 |
|---|---|---|---|---|
| HTTP 429 | `*TransientError` | `provider_transient` | 是（≤3 物理尝试，既有 `maxPhysicalAttempts`） | |
| HTTP 408 / 5xx | `*TransientError` | `provider_transient` | 是 | 408 是显式超时状态 |
| 网络错误 / 请求超时 | `*TransientError` | `provider_transient` | 是 | 不把错误文本（可能含 URL）放进 error |
| 调用方 ctx 取消 | `context.Canceled`（原样） | — | 否 | 不被误标为 transient |
| HTTP 401/403 | `*ProviderStatusError` | `provider_permission_denied` | 否 | |
| HTTP 404 | `*ProviderStatusError` | `provider_model_not_found` | 否 | |
| 其它 4xx（含 400/422） | `*ProviderStatusError` | `provider_request_rejected` | 否 | |
| 3xx 重定向 | `*ProviderStatusError` | `provider_redirected` | 否 | 不跟随 |
| 200 但非法 JSON / 非对象 / 无 choices / 非 SSE 流 | `ErrResponseInvalid` | `provider_response_invalid` | 否 | |
| 响应超过 8 MiB（含流式累计/单行） | `ErrResponseTooLarge` | `provider_response_too_large` | 否 | |
| SSE `error` 帧 / EOF 早于 finish/[DONE] / 读中断 | `*TransientError` | `provider_transient` | 是 | 返回空 `ProviderResponse`，**绝不返回部分结果** |
| SSE 数据帧非法 JSON | `ErrResponseInvalid` | `provider_response_invalid` | 否 | |
| `emit` 返回错误（消费者停止） | 原样返回该错误 | — | 否 | 立即停止读取 |
| 未配置端点 / 凭据不可解析 | `ErrNotConfigured` | `provider_not_configured` | 否 | 预检，**不产生 ledger 行** |
| 未批准目的地（未登记 slug / URL / 非法 endpoint） | `ErrDestinationUnapproved` | `provider_destination_not_approved` | 否 | 预检，不产生 ledger 行 |
| 协议不是 `chat_compatible`（含缺省） | `ErrAdapterUnavailable` | `adapter_unavailable` | 否 | 预检，不产生 ledger 行 |

`provider=stub` 候选仍只走 stub（`Providers.For` 语义未改）；stub profile 下真实候选只在显式
配置端点+凭据时才可能 dispatch。

## 4. 配置接线（最小改动）

1. `config.Load`：`dev-stub`/`capacity-stub` 下若显式设置了 `ORCHARA_MODEL_ENDPOINT` 或
   `ORCHARA_MODEL_SECRET_REF`，两者必须**成对**存在，端点不得带 `?`/`#`，secret ref 必须是
   `env:`/`file:` 形态；否则启动失败并指名变量。`cfg.Stub()` 仍为 true（stub 记账语义不变）。
   `real-model` 分支语义不变（缺端点仍报错）。
2. `cmd/api`：先 `modelregistry.FromEnv`（原有 fail-closed catalog），再
   `NewProviders(...).WithCatalog(catalog)`；adapter 用 catalog 批准 `endpoint_ref`/`secret_ref`，
   并把 catalog 凭据条目 `env` 指向的部署环境变量解析为凭据值（凭据值本身从不进 catalog）。
3. capability 路径把 `ModelProfile.ConfigJSON` 传给 `modelproxy.Candidate.Config`（primary、
   fallback、stream、stream fallback），adapter 因此能按登记 `protocol/timeout/temperature/price`
   映射；T024/路由路径原本就带 Config。

## 5. 逐 AC 对照与证据

> 所有命令都从 `E:\Orchara-wt\t122\product` 运行；原始日志在
> `E:\Orchara\.scratch\f13b\T122\`。`stub` 与 `real` 分栏：本票的 "real" 指**真实 provider
> 协议路径 + 本地 fake HTTP provider**；真实厂商运行见 §8 `blocked`。

### AC1 — Available 不再 adapter_unavailable；Call 发一次 HTTP 并映射文本/结构化/tool_calls/usage

- 实现：`real.go:Available` + `openai.go:Call/decodeChatCompletion/responseFrom`。
- Go 协议测试（`go-modelproxy-t122.log`）：
  ```
  --- PASS: TestAvailableRequiresAnApprovedDestination (0.00s)
  --- PASS: TestAvailableRequiresTheChatCompletionsProtocol (0.00s)
  --- PASS: TestAvailableRequiresAConfiguredEndpointAndResolvableCredential (0.00s)
  --- PASS: TestCallMapsTextUsageAndSealsCanonicalBytes (0.10s)
  --- PASS: TestCallMapsToolCallsAndStructuredOutput (0.00s)
  --- PASS: TestCallFixtureModeProducesACompatibilityCheckAndRouteDecision (0.01s)
  ```
- 产品路径 e2e（真实栈 + fake provider；`pytest-t122.log`）：
  ```
  PASSED tests/f06/test_t122_real_provider.py::test_chat_completion_is_dispatched_metered_and_sealed
  PASSED tests/f06/test_t122_real_provider.py::test_tool_calls_and_structured_output_are_mapped
  PASSED tests/f06/test_t122_real_provider.py::test_admin_registered_real_model_validates_through_the_provider
  11 passed in 132.51s (0:02:12)
  ```
  断言内容：fake provider 恰好收到 1 次 `POST /v1/chat/completions`，请求体含登记
  `model_id`=`fake-vendor-model-1` 与 messages，`Authorization: Bearer <secret>`；返回文本 →
  capability `data.text`；11/7 tokens 入账（attempt tokens_in/out、settlement_state=settled、
  charged_amount=0.00002500）；tool_calls 与 `structured` 经平台校验后封存；receipt artifact
  sha256 与封存字节一致；管理员登记（catalog 引用）→ validate → maintenance → 真实 fake
  provider 调用 → `compatible: true, stub: false`。

### AC2 — 流式 delta/finish/error 映射；半段 transient、不拼接残缺结果

- 实现：`openai.go:Stream/readStream/completeStreamCalls`；`capabilities/stream.go` 原逻辑复用
  （error 帧可重试、新 generation 不拼接旧文本、只封存完成结果）。
- Go 协议测试：
  ```
  --- PASS: TestStreamMapsDeltasFinishUsageAndToolCallFragments (0.00s)
  --- PASS: TestStreamRefusesANonSSEAnswer (0.00s)
  --- PASS: TestStreamHalfwayFailureIsTransientAndReturnsNoPartialResponse (0.01s)
  --- PASS: TestStreamErrorFrameIsTransient (0.00s)
  --- PASS: TestStreamStopsWhenTheConsumerStops (0.00s)
  --- PASS: TestStreamCancellationIsTheCallerCancellation (0.00s)
  ```
- 产品路径 e2e：
  ```
  PASSED tests/f06/test_t122_real_provider.py::test_model_stream_maps_deltas_and_seals_once
  PASSED tests/f06/test_t122_real_provider.py::test_half_stream_is_transient_and_never_seals_a_partial_answer
  ```
  断言内容：`model-stream` 真实 SSE 返回 2 个 delta + 1 个 final，delta 拼接 == final text；
  usage 9/4 入账；一次成功 attempt 与一个 receipt。半段流（连接在 finish/[DONE] 前断开）→
  `error` 帧 `provider_transient` + `retryable=true`，重试以新 generation 从头开始，最终文本只含
  第二次流的内容（`partial-*` 不出现），attempts = failed → succeeded，receipt 仅 1 个。

### AC3 — 429/5xx/超时 → TransientError（复用 ≤3 次物理重试）；4xx/schema/权限不重试

- 复用既有 `capabilities.maxPhysicalAttempts = 3` 与兼容 fallback（未改其策略）。
- Go 协议测试：
  ```
  --- PASS: TestCallClassifiesProviderStatuses (0.20s)
  --- PASS: TestCallTimeoutIsTransient (0.05s)
  --- PASS: TestCallCancellationIsNotReportedAsATransientProviderFailure (0.00s)
  --- PASS: TestCallRefusesMalformedOversizedAndRedirectedAnswers (0.23s)
  ```
- 产品路径 e2e：
  ```
  PASSED tests/f06/test_t122_real_provider.py::test_429_is_retried_within_the_three_attempt_ceiling
  PASSED tests/f06/test_t122_real_provider.py::test_5xx_exhausts_the_bounded_retries_and_fails_honestly
  PASSED tests/f06/test_t122_real_provider.py::test_401_is_not_retried_and_never_echoes_the_credential
  ```
  断言内容：429、429、200 → fake provider 恰好 3 次请求，attempts 1/2 failed
  (`provider_transient`) + attempt 3 succeeded 并计费；503 持续 → 3 次请求 / 3 次失败 attempt，
  operation failed `provider_transient`，无 receipt artifact；401 → **仅 1 次**请求、1 次失败
  attempt（`provider_permission_denied`），不重试。

### AC4 — 凭据只在 Go 解析；不外泄；未配置 → provider_not_configured；未批准目的地 → 拒绝

- 实现：catalog/`env:`/`file:` 解析只在 `resolveCredential`；`Authorization` 是唯一出站位置；
  错误、`Data`、`Output`、ledger、日志均无明文（e2e 断言 API 日志、响应体、receipt、ledger、
  fake provider 除 Authorization 外的所有 header 都不含 secret）。
- 证据：
  ```
  PASSED tests/f06/test_t122_real_provider.py::test_unregistered_destination_and_unresolvable_credential_fail_closed
  PASSED tests/f06/test_t122_real_provider.py::test_missing_endpoint_is_provider_not_configured_without_a_ledger_row
  PASSED tests/f06/test_t122_real_provider.py::test_401_is_not_retried_and_never_echoes_the_credential
  ```
  - 未登记 `endpoint_ref` → 503 `provider_destination_not_approved`，**0 个 operation 行、0 次
    HTTP 请求**；注册但环境变量未设置的凭据 → 503 `provider_not_configured`，同样 0 行 0 请求；
    `protocol=embedding_compatible` → 503 `adapter_unavailable`，0 行 0 请求；缺
    `ORCHARA_MODEL_ENDPOINT` 的第二套真实栈 → 503 `provider_not_configured`，0 行 0 请求。
  - 凭据只在 fake provider 的 `Authorization` header 出现一次；e2e 断言响应体、ledger JSON、
    receipt 文档、API 进程日志、fake provider 除 Authorization 外的所有 header 均不含 secret
    （401 用例的 fake 错误体故意回显 secret，也未进入任何可观测面）。

### AC5 — 本地 fake HTTP provider 协议测试覆盖 正常/tool_calls/usage/429/5xx/半段流/超时/非法 JSON/超大响应

| 场景 | Go 协议测试 | 真实栈 e2e |
|---|---|---|
| 正常文本 | `TestCallMapsTextUsageAndSealsCanonicalBytes` | `test_chat_completion_is_dispatched_metered_and_sealed` |
| tool_calls | `TestCallMapsToolCallsAndStructuredOutput` | `test_tool_calls_and_structured_output_are_mapped` |
| usage | 同上（11/7、5/3） | 同上（ledger/attempt 断言） |
| 429 | `TestCallClassifiesProviderStatuses` | `test_429_is_retried_within_the_three_attempt_ceiling` |
| 5xx | 同上 | `test_5xx_exhausts_the_bounded_retries_and_fails_honestly` |
| 半段流 | `TestStreamHalfwayFailureIsTransientAndReturnsNoPartialResponse` | `test_half_stream_is_transient_and_never_seals_a_partial_answer` |
| 超时 | `TestCallTimeoutIsTransient` | （Go 协议层覆盖；真实栈不重复注入 50ms 级慢请求）|
| 非法 JSON | `TestCallRefusesMalformedOversizedAndRedirectedAnswers` | `test_malformed_and_oversized_answers_are_terminal` |
| 超大响应 | 同上（8 MiB+1） | 同上 |

上表 11 条真实栈 e2e 在同一次 `pytest tests/f06/test_t122_real_provider.py -q -rA` 中
**全部 PASSED**（132.51s）。

### AC6 — 记录版本/输入 hash/命令/原始结果/环境 profile；stub/real 分报；真实厂商 blocked

- 本报告 §8、§9；原始日志目录 `E:\Orchara\.scratch\f13b\T122\`；e2e 断言 receipt artifact 的
  `sha256` 与封存字节一致、`params_sha256` 为 64 hex、attempt 记录 tokens/charge/price_version。
- e2e 首个用例打印的可重放证据行（`pytest-t122.log`，`-rA` 捕获）：
  ```
  T122-EVIDENCE {"charged_amount": "0.00002500",
    "git_head": "ae1af91926019fbf6d9a78601d62322351228cc4",
    "model_id": "fake-vendor-model-1",
    "model_profile_version_id": "22222222-2222-4222-8222-222222222222",
    "params_sha256": "ed59ed7c047dd96d7f5c17df85d827230d1ba185e46eef40a4aa901a2a84546a",
    "price_version": "price-v1:22222222-2222-4222-8222-222222222222:in=1.00:out=2.00:USD",
    "profile": "dev-stub + explicit ORCHARA_MODEL_ENDPOINT (loopback fake provider)",
    "provider": "openai-compatible",
    "receipt_artifact_sha256": "f332165a26f3caf682f25b048ded043dd30ef2775ebea6f3d6e8cf8c2988b882",
    "receipt_size_bytes": 169, "tokens_in": 11, "tokens_out": 7}
  ```
  注：`git_head` 是证据运行时的分支基点（`ae1af91`）；T122 的改动当时是同一 worktree 的未提交
  变更，提交后的树内容与运行内容一致（见 §6 门禁的最终提交）。

## 6. 门禁

从 `E:\Orchara-wt\t122\product` 运行；原始日志在 `E:\Orchara\.scratch\f13b\T122\`。

```
$ go build ./...
build exit=0
$ go vet ./...
vet exit=0
$ go test ./...
(exit 0；0 个 FAIL 行；完整输出 go-test-final.log)
$ gofmt -l internal cmd tools
(empty: all files are gofmt-clean)
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

```
$ python -m pytest tests/f06/test_t122_real_provider.py -q -rA
11 passed in 132.51s (0:02:12)
$ python -m pytest tests/f06 -q
184 passed in 1398.11s (0:23:18)
```

真实栈占用：`locks\realstack\t122.owner.txt`（02:10:11–02:35:59，**1548s = 25.8 min ≤ 30 min**，
覆盖 T122 e2e + 全量 f06 串行）。按批次 §2.1 的新协议，本次运行与 T114 的 `pytest tests/f03`
同处 correctness 窗口；184 条全绿，**无需隔离重跑**，证据即本次并发运行（`lock-context.txt`）。

### 6.1 集成分支 merge 后复跑（最终树）

- merge：`integration/t113-t114-t122` @ `22eb645`（T114 已合入）→ merge commit `1ab71c7`
  （`Merge branch 'integration/t113-t114-t122' into t/t122-real-provider-adapter`）。
- 冲突解决（只保留双方，不裁剪任何一方）：`cmd/api/main.go`（T122 的 `WithCatalog` + T114 的
  `SetStubServiceDelay` 并存）、`internal/config/config_test.go`（T122 3 条 + T114 1 条测试并存）、
  `PROGRESS.md`（T122 与 T114 小节并存）。`config.go`/`provider.go`/`modelproxy_test.go` 自动合并。
- 合并后最终树门禁（`go-*-merged.log`）：
  ```
  go build ./...   exit=0
  go vet ./...     exit=0
  go test ./...    exit=0（0 个 FAIL 行）
  ```
- 合并后真实栈复跑（同一天，最终树）：
  ```
  $ python -m pytest tests/f06/test_t122_real_provider.py -q
  11 passed in 99.20s (0:01:39)
  $ python -m pytest tests/f06 -q
  184 passed in 1390.38s (0:23:10)
  ```
  合并后真实栈占用 1499s = 25.0 min（≤30 min），`t122-merge.owner.txt` 由运行者删除。

## 7. 复现命令

```powershell
$env:GOCACHE='E:\orchara-toolchains\gocache'; $env:GOMODCACHE='E:\orchara-toolchains\gomodcache'
$env:PATH="E:\orchara-toolchains\go\bin;$env:PATH"
# 从 E:\Orchara-wt\t122\product：
go test ./internal/modelproxy -run 'TestAvailable|TestCall|TestStream|TestTheAdapterDoesNotChange' -v
go test ./internal/config -run 'TestStubProfiles|TestRealModelRequires' -v
python -m pytest tests/f06/test_t122_real_provider.py -q   # 需 realstack 锁
```

## 8. 诚实边界（blocked / not_run）

- **真实厂商 end-to-end：`blocked`**。缺：获批的真实厂商端点（`base_url`）、厂商凭据
  （`ORCHARA_CREDENTIAL_REFS` 里某条目的 `env` 值）、配额/预算与外发批准。解除条件：
  部署方登记 `ORCHARA_MODEL_ENDPOINTS`/`ORCHARA_CREDENTIAL_REFS`、导出凭据环境变量、
  设置 `ORCHARA_MODEL_ENDPOINT`，再以 `dev-stub`（显式端点）或 `real-model` profile 跑同样的
  e2e 断言；本报告**没有**用 fake provider 冒充厂商通过。
- `not_run`：真实厂商的 429/流式/工具调用行为；真实厂商的配额与账单对账；T112/#113 的真实模型
  小并发（另需外部凭据/配额，本票不能解除）。
- `stub` 分界：`provider=stub` 的 capability/stream/ledger 行为与本票前一致（既有 F06 测试回归）；
  本票的 "real" 证据全部来自本地 fake HTTP provider，报告中不混写。

## 9. 被本票取代的既有断言（诚实记录）

- `internal/modelproxy/stream_test.go: TestRealStreamingIsAdapterUnavailable` 断言"真实 provider
  流式必定 `adapter_unavailable`"，这是 T044 时代"本 build 无 vendor adapter"的断言，与 AC2
  （真实流式 adapter）直接冲突。本票将其替换为
  `TestRealStreamingFailsClosedWithoutAnEndpoint`：adapter 可被选中，但未部署端点时 `Stream`
  仍 fail closed 返回 `ErrNotConfigured`，且不再返回 `adapter_unavailable`。这是**语义变更**，
  不是弱化断言；stub 流式断言未动。
- 未改其它票的断言；`provider=stub` 全部路径行为不变。
