# F06 T041 验收：运行域受控能力代理与受控模型/工具适配

- 任务：F06 / GitHub Issue #42（票 `T041`，规划 ID F06.02）；分支 `t/t041-capabilities`
  （worktree `E:\Orchara-wt\t041`，base `2a5d565` = T040 合并）；父门禁 #38/#39。
- 状态：**`passed`**（本票范围 = 运行域 Go 能力代理：模型调用全链路 + 工具调用授权/worker
  回报；`invokeCapability` 契约端点 + 非契约 `capability-results` 端点；迁移 0025
  `operations`/`operation_attempts` 账本；owner-only 账本读取 + `/runs` 页面面板；
  Python worker 适配器与 agent 循环；真实 PostgreSQL/mTLS/HTTP/对象字节测试）。
  真实沙箱 python/agent 节点执行仍属 T042/T043；本票的测试 worker 执行真实本地 Python
  工具函数并经非契约端点回报，不是假成功。
- 边界（如实）：模型提供方是文档化 dev stub（`provider="stub"`，profile `dev-stub`），
  输出确定性来自 params，处处标注 `stub=true`；本构建没有真实厂商 adapter，真实 provider
  路径诚实失败为 `provider_not_configured`/`adapter_unavailable` 且不重试。工具在测试里由
  真实本地函数执行，但真实 Linux/WSL 沙箱与节点运行时是 T042/T043。连接器能力（T050）、
  预算结算（T046）与审批来源（T048）不在本票；`awaiting_approval` 状态存在于表/投影但本票
  没有写入者。浏览器 DOM 驱动 `/runs` 面板 `not_run`（纯 helpers 由 Node 执行）；全量
  `pytest tests` 与全量 `tests/f00b` 由 orchestrator/后续运行（Windows 管道挂起风险，本票只
  跑目标 T024 文件）。

## 1. 交付内容

### 1.1 迁移 0025（唯一新迁移）

`db/migrations/0025_run_capability_ledger.sql`：

- `platform.operations`：按 `contracts/database.sql` 的 operations 建立（run_id, logical_key,
  capability_version, capability_kind `model|tool|connector`, risk_class `pure|read|write`,
  params_sha256, params_artifact_id, state
  `prepared|awaiting_approval|authorized|dispatching|succeeded|failed|unknown`,
  provider_idempotency_key, receipt_artifact_id, dispatched_at, reconciled_by,
  reconciliation_evidence_id, UNIQUE(run_id,logical_key)）；文档化新增列 `created_at`、
  `updated_at`、`last_error_code`。
- `platform.operation_attempts`：每次**物理**派发一行（operation_id, attempt_no, state,
  attempt_token → run_attempts, result_artifact_id, error_code, tokens_in/out,
  reserved_amount numeric(20,8) NOT NULL DEFAULT 0, charged_amount, currency, price_version,
  created_at, finished_at, UNIQUE(operation_id,attempt_no)）；T041 不写
  provider_idempotency_key/reconciliation（T046/T050）。
- 权限边界与 0007–0024 相同：`REVOKE ALL ON SCHEMA platform FROM PUBLIC`，存在
  `orchara_worker` 时撤销 platform 表/序列/usage 权限。每个偏差都有 COMMENT 说明。

### 1.2 Go 能力服务 `internal/capabilities`

- `Service{DB, Store, Providers *modelproxy.Providers, ToolExecutor}`；`Invoke` / `ReportResult`
  / `Ledger`。
- **授权先于任何 provider 调用**：run 存在且 `running`（终态/queued/waiting/cancelling/
  recovery_pending/finalizing → 409 `run_not_running`；`cancel_requested_at` 非空 → 409
  `run_cancelled`）；attempt token 必须匹配未退出、未撤销、未过期且为 `jobs.current_attempt_token`
  的 `run_attempts` 行（403 `stale_attempt`）；读取
  `execution_snapshots.manifest_artifact_id` 的真实封存字节，重算 SHA-256 必须同时等于
  `runs.manifest_sha256` 与快照 digest（否则 503 `manifest_unavailable`），再解析
  `compiler.Manifest`；capability version 必须在该 manifest 的 `model_profile_versions`
  （model）或 `tool_artifacts`（tool）内（403 `capability_not_allowed`）；同时出现在两个集合
  → 409 `capability_kind_ambiguous`；connector → 422 `capability_kind_unsupported`。
- **幂等/规范参数**：params 走 RFC 8785 JCS → SHA-256；JCS 字节封存为
  `purpose=capability_params` 的 ready artifact（同时写 `run_artifacts` 链接）。operation id
  是行 id；`UNIQUE(run_id,logical_key)`；同 operation id + 同 params → 回放已存结果；同
  logical key 不同 params/id → 409 `operation_conflict`；并发重复由 `runs` 行锁 + 唯一约束
  决出一个赢家。
- **模型调用**：profile 必须 `passed` 且未撤销（422 `model_profile_unavailable`）；provider
  部署事实在任何 operation 行之前检查（`provider_not_configured`/`adapter_unavailable` 503，
  不留账本行）。状态 CAS `prepared → authorized → dispatching`，每次物理调用先插
  `operation_attempts` 再调用 provider；Go 是**唯一重试权威**：只有
  `modelproxy.TransientError` 重试，最多 2 次重试（最多 3 次物理尝试，指数退避
  20ms×attempt），每次新 attempt 行；schema/permission/validation 错误立即失败且
  attempts=1。
- **响应校验**：`tool_calls` 的 id 非空且唯一、name 必须是本次声明的工具版本、`arguments`
  必须是对象并通过该工具 `input_schema`（受限 JSON-Schema 子集：type/object/properties/
  required/additionalProperties/array/items/string/number/integer/boolean/enum）；声明
  `response_schema` 时最终 `structured` 必须存在并通过同一子集校验。校验后的平台投影
  `{text, structured?, tool_calls:[{id,name,arguments}], usage?, model_profile_version_id,
  stub}` 封存为 `purpose=receipt` 的 ready artifact，operation/attempt 才 `succeeded`，并写真实
  token 用量/预留与计费金额；缺 usage 时保留保守 `reserved_amount`。
- **工具调用**：工具版本必须 ready 且 approved，或（debug Run）ready 且属于该 Run owner；
  params 通过工具 `input_schema`；状态 `authorized → dispatching` 并插入 attempt 行，返回
  `state=dispatching` + `data.grant{operation_id, capability_version, capability_kind:"tool",
  risk_class, params_sha256, run_id, attempt_token}`。实际执行属 worker（T043）。
- **结果回报**（非契约）：`ReportResult` 校验 run/attempt 存活、operation 属于该 run 且
  dispatching、最新 attempt 的 token 就是本次 token；成功只接受 ready 且 owner 等于 run
  owner 的 artifact，或把 worker 上报的小 `data` 服务端封存为 receipt 并链接 run；失败只接受
  安全小写错误码；重复回报是幂等回放，不新增 attempt。
- **账本读取**：`Ledger(ownerID, runID)` 先证明 owner（他人/不存在同为 404
  `run_not_found`），只投影
  `{id,capability_kind,capability_version,state,attempts,error_code,receipt_artifact_id,
  tool_call_summary:[{id,name}],structured_output_keys,stub}`；不返回 params 或 receipt 正文。

### 1.3 Dev stub 的 agent 分支

`internal/modelproxy/provider.go`：params 含 `messages`/`task:"agent"` 时进入确定性 agent
分支——输出由 canonical params 派生（`text = "stub-agent:"+digest[:16]`；按
`response_schema` 生成满足该子集的 `structured`；`usage` 为真实 token 估算），始终
`stub=true`；支持文档化 `stub_tool_calls`（原样交给能力校验器接受/拒绝）与
`stub_transient_failures`（0–2，按 attempt 数返回 `TransientError`，使重试/账本路径无需真实
厂商即可测试）。既有 job 级 fixture 校验路径不变。

### 1.4 HTTP 端点

| 端点 | 契约 | 角色/授权 | 说明 |
|---|---|---|---|
| `POST /internal/v1/runs/{run_id}/capabilities` | `invokeCapability` | mTLS worker | 严格解码 `CapabilityInput`；返回 `CapabilityResult{operation_id,state,receipt_artifact_id,approval_id,data}` |
| `POST /internal/v1/runs/{run_id}/capability-results` | 非契约（T042/T043 桥） | mTLS worker | `{attempt_token,operation_id,state:succeeded\|failed,result_artifact_id?,data?,error_code?}` → `{operation_id,state,receipt_artifact_id?,error_code?}` |
| `GET /api/v1/runs/{run_id}/capabilities` | 非契约 owner 读 | session/API key `application:read` + owner | `{items:[账本投影]}`；无公共调用端点 |

错误映射为稳定 code/status：404 `run_not_found`/`operation_not_found`；403
`stale_attempt`/`capability_not_allowed`/`forbidden`；409 `run_cancelled`/`run_not_running`/
`operation_conflict`/`operation_in_progress`/`operation_not_dispatching`/
`capability_kind_ambiguous`；422 `validation_failed`/`capability_kind_unsupported`/
`model_profile_unavailable`/`tool_not_available`/`result_artifact_invalid`/
`provider_response_invalid`；503 `manifest_unavailable`/`provider_not_configured`/
`adapter_unavailable`/`provider_transient`。provider 载荷/URL/凭据永不进入响应。

### 1.5 Python worker（`worker/platform_worker/agents`）

- `adapter.py`：`PlatformChatModel`（最小可调用适配器，无 langchain_core 依赖；生产/T042
  用 mTLS 实现 `CapabilityTransport`，测试注入计数 fake）。每次 `generate` 恰好一次物理调用，
  **无隐藏重试**；保留 tool-call id/arguments；再次校验 id 唯一/非空、name 已声明、arguments
  通过工具 input_schema；平台错误映射为 `PlatformCapabilityError`（保留 code/status，不泄露
  provider 正文），畸形投影为 `PlatformValidationError`。生成新 operation id 与 logical key，
  每个 agent turn 是一次独立 capability 操作。
- `agent.py`：注入 `ModelAdapter`/`ToolExecutor` 的小循环：模型 → 再校验 tool calls → 逐个执行
  → 追加 tool 结果 → 重复至 `max_turns`；最终 `structured` 按声明 schema 校验；结果只返回
  `NodeOutcome{succeeded|failed|waiting|needs_reconciliation}`（等待映射
  `awaiting_approval`，不确定工具副作用映射 `needs_reconciliation`），绝不返回 provider 对象。
- `schema.py`：与 Go 同一受限子集的独立实现（defence in depth）。

### 1.6 页面

`/runs` 详情新增可折叠「受控调用账本（F06/T041）」面板：能力/版本/状态/物理尝试/结构化输出
键/工具调用 id+name/来源（stub·错误码），`textContent` 写入，无 params/参数/回执正文；
`runs.js` 新增纯 helpers（`runCapabilitiesPath`、`describeCapabilityOperation`、
`describeCapabilityLedger`、`capabilityStateText` 等）由 Node 执行测试。

## 2. 测试

### 2.1 Go 单元测试（先红后绿）

`internal/capabilities/params_test.go`、`service_test.go`、`internal/modelproxy/agent_test.go`：

- JSON-Schema 子集：required/additionalProperties=false/enum/array items/integer 语义；定位路径；
- canonical params：键序无关、不同参数不同 digest；
- model params 白名单：未知参数/空 messages/非法 role/超 profile token 上限/未在 manifest 的
  tool version/response_schema 非 object 全部 422 且定位字段；
- provider 响应：合法 tool call 通过；重复 id、未知工具、坏参数、缺少/未请求 structured 被拒；
- 重试分类：只有 `TransientError` 可重试；`ErrNotConfigured`/`ErrAdapterUnavailable`/
  permission 错误不可重试；
- manifest allowlist 与 kind ambiguity：both sets→409、model/tool 越界→403、connector→422；
- stub agent：确定性、stub 标记、structured 生成、hint 透传、transient 按 attempt 计数；
- safe error code 仅 `[a-z0-9_]`。

### 2.2 真实栈测试 `tests/f06/`（真实 PG + 编译二进制 + 真实 mTLS/HTTP + 真实对象字节）

`tests/f06/test_t041_capabilities.py`（12 passed）：

1. 模型调用 happy path：stub 投影、structured、receipt artifact ready、operation/attempt 各 1、
   真实 token 用量/金额、attempt_token 绑定、owner 账本 `attempts=1`/`stub=true`/结构化键；
2. 重复 operation 回放：同 receipt、attempts 仍 1；
3. logical-key 冲突（不同 id/不同参数）→ 409 `operation_conflict`，只有一条 operation；
4. tool-call 校验：未知工具/重复 id/坏参数/参数缺失 → 422 `provider_response_invalid`，每次
   attempts=1、无 receipt、attempt 无 token 用量；
5. transient 重试：`stub_transient_failures=1` → 200 succeeded，attempts=2（第一条 failed
   `provider_transient`，第二条 succeeded）；
6. 参数/清单/撤销错误：超 token/未知参数 422、随机 version 403、kind 错配 403、撤销模型 422，
   **均不产生 operation 行**；
7. 工具 grant + 真实本地函数结果回报：grant 字段/params hash、operation dispatching、
   report succeeded 后 receipt 内容等于本地函数返回值、attempts=1、重复报告幂等；
8. 工具参数 schema 拒绝 → 422 且无 operation；
9. 过期 attempt 403 `stale_attempt`；cancel 后 409 `run_cancelled` 且无行；
10. supervisor 证书调用/回报均 403 `forbidden`；
11. 结果端点：不存在 operation 404、模型 operation 报工具结果 409；
12. 账本 owner-only：他人 404 `run_not_found`。

`tests/f06/test_t041_agent_integration.py`（2 passed）：把真实 `PlatformChatModel` +
`run_agent` 接到真实 mTLS 端点：两轮 agent（第一轮 tool call，真实本地
`celsius_to_fahrenheit` 执行，第二轮结构化回答）；断言 transport 物理调用 = 2 = operation 数
= attempt 总数，账本两次均 attempts=1/stub、tool_call_summary 含 `call-weather-1/lookup`、
structured keys=`report`；清单外 version 的拒绝只发生一次物理调用、无账本行。

`tests/f06/test_t041_worker.py`（10 passed）：一次 generate 一次物理调用、op id 每轮不同、
平台错误不重试、重复 id/未知工具/坏参数不重试、fake transport 计数与 agent turns 一致、
max_turns、结构化 schema 错误不重试、needs_reconciliation、waiting、provider_not_configured。

`tests/f06/test_t041_pages.py`（3 passed）：`/runs` 面板结构 + `runs.js` markers + Node 执行
账本 helper（成功/失败/重试 2 次/空账本/状态词表/工具摘要；输出不含 params）。

既有 F06 T039 测试一并通过：`tests/f06` 合计 **42 passed**。

### 2.3 真实栈边界（stub/real 分报）

- **stub（真实执行）**：模型调用、tool-call 校验、重试账本、tool grant/结果、owner 账本通过
  真实 HTTP/PG/对象字节；stub 输出带 `stub=true`，账本投影带 `stub` 标记。
- **real（未实现，诚实失败）**：真实厂商模型 adapter/凭据解析 → 503
  `provider_not_configured`/`adapter_unavailable`，不重试，无 provider 载荷泄漏。
- 工具执行：测试 worker 执行真实本地 Python 函数并经非契约端点回报；生产沙箱/节点执行属
  T042/T043。
- 环境：真实 PostgreSQL（`127.0.0.1:55432`，临时库经 `cmd/admin migrate`）、真实编译
  `cmd/api`、真实 mTLS 证书（worker/supervisor/maintenance）、真实文件系统 object store、
  Node（页面 helpers）。Go 工具链 `E:\orchara-toolchains\go\bin\go.exe`；profile `dev-stub`。
- 版本/输入：迁移 0025；Go `go.mod` 本票无新依赖；Python 仅标准库；manifest 为 T025 金样
  （`manifest_sha256` 由服务端封存字节重算）；params 均记录 JCS SHA-256。
- 原始命令/结果：见 §3。

## 3. 门禁（原始输出尾部）

```text
$ E:\orchara-toolchains\go\bin\go.exe vet ./...
(vet exit=0, no output)

$ E:\orchara-toolchains\go\bin\go.exe test ./...
ok  	orchara/platform/internal/supervisor	1.352s
?   	orchara/platform/internal/toolruns	[no test files]
ok  	orchara/platform/internal/tools	4.663s
ok  	orchara/platform/internal/toolversions	2.263s
ok  	orchara/platform/internal/uuid	1.081s
ok  	orchara/platform/internal/workflow	1.939s
(exit=0; 24 packages ok, 0 FAIL)

$ gofmt -l internal cmd tools
(empty; exit=0)

$ python -m pytest tests/f06 -q
..........................................                               [100%]
42 passed in 143.16s (0:02:23)

$ python -m pytest tests/f04a -q   # ORCHARA_P01_PYTHON=E:\Orchara\product\probes\agent_min\.venv\Scripts\python.exe
....................................                                   [100%]
36 passed in 74.44s (0:01:14)

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 4.81s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest

$ python -m pytest tests/f00b/test_t024_model_calls.py tests/f00b/test_t024_model_validate.py -q
............                                                             [100%]
12 passed in 71.21s (0:01:11)
```

## 4. T042/T043 可用的精确接口

- 契约模型调用：`POST /internal/v1/runs/{run_id}/capabilities`（worker mTLS），body =
  `CapabilityInput`：
  `{attempt_token:uuid, operation_id:uuid, logical_key:1..200 printable,
  capability_version:uuid, params:object, capability_kind:"model"|"tool"|"connector"}`；
  200 = `CapabilityResult{operation_id,state,receipt_artifact_id|null,approval_id|null,
  data?,}`。model 的 `data` = `{text, structured?, tool_calls:[{id,name,arguments}], usage?,
  model_profile_version_id, stub}`；tool 的 `data` =
  `{grant:{operation_id,capability_version,capability_kind:"tool",risk_class,params_sha256,
  run_id,attempt_token}}`。
- 工具结果桥（非契约）：`POST /internal/v1/runs/{run_id}/capability-results`（worker mTLS），
  body `{attempt_token, operation_id, state:"succeeded"|"failed",
  result_artifact_id?|data?, error_code?}`；200
  `{operation_id,state,receipt_artifact_id|null,error_code?}`；重复回报幂等。
- owner 账本：`GET /api/v1/runs/{run_id}/capabilities` →
  `{items:[{id,capability_kind,capability_version,state,attempts,error_code|null,
  receipt_artifact_id|null,tool_call_summary:[{id,name}],structured_output_keys,stub}]}`。
- Go：`capabilities.New(db, store, providers)`；`(*Service).Invoke(ctx, runID, role, Input)
  (*Result, *capabilities.Error)`（role 必须是 `worker`，服务内再防一层）；`ReportResult(ctx,
  runID, ResultReport)`；`Ledger(ctx, ownerID, runID)`；`(*Error).Status/Code/Message/Field`；
  `modelproxy.TransientError`/`IsTransient`。
- Python：`platform_worker.agents.PlatformChatModel(transport, run_id=, attempt_token=,
  capability_version=)`，`transport.invoke(run_id, body)`；
  `run_agent(model=,tools=[DeclaredTool(name,capability_version,input_schema)],executor=,
  messages=,response_schema=,max_turns=) -> NodeOutcome`。
- 模型 params 白名单：`task:"agent"`、`messages:[{role:"system"|"user"|"assistant"|"tool",
  content}]`、`tools:[{name,capability_version}]`、`response_schema`（根 type=object）、
  `max_output_tokens`（≤ profile config，缺省 4096）；stub 专用 `stub_tool_calls`、
  `stub_transient_failures`（0..2）。
- 重试语义：transient 最多重试 2 次（≤3 次物理尝试，20ms×attempt）；部署事实
  （`provider_not_configured`/`adapter_unavailable`）与 schema/permission/validation 不重试；
  provider 响应非法 = 1 次尝试、operation failed、无 receipt。
- 迁移对象：`platform.operations`、`platform.operation_attempts`（见 §1.1）；参数 artifact
  purpose `capability_params`，回执 purpose `receipt`；run_artifacts relation
  `capability_params`/`capability_receipt`/`capability_result`。

## 5. 剩余 / not_run（如实）

- `not_run`：真实厂商模型 adapter 与凭据解析（本构建无协议 adapter；真实 provider 诚实
  503）；真实 Linux/WSL 沙箱 python/agent 节点执行（T042/T043，本票只执行真实本地 Python
  工具函数）；connector 能力（T050）；预算结算/价格版本（T046，本票只记录
  reserved/charged）；审批来源与 `awaiting_approval` 写入（T048）；浏览器 DOM 驱动 `/runs`
  （Node 只执行纯 helpers）；全量 `pytest tests`/全量 `tests/f00b`（Windows 管道挂起风险，
  本票按建议只跑目标 T024 文件）；真实 SSE/流式增量（T044，本票不实现）。
- 未 push、未关闭 issue #42；未改 `docs/spec/**`、`contracts/**`、迁移 0001–0024；工作树
  干净。提交为 5 个里程碑提交 + 1 个 worker 角色参数对齐（服务层再校验 role=worker）提交。
