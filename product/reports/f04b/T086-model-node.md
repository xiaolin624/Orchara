# F04B/T086 — 配置单次模型节点并验证结构输出（GitHub #87）

**状态：`passed`**（运行时 + 画布；真实栈用例 1–5 已跑，见 §4；未实现/未执行的项在 §5 明确标 `not_run`）。

- 分支：`t/t086-model-node`，worktree `E:\Orchara-wt\t086`；基线 `integration/t083-t091` @ `111e2c2`，开发中已 merge 集成分支 tip（见 §6）。
- 范围：Go `internal/presetexec` 的 graph 计划/调度器新增 `model` 节点（一次受控生成、严格 `{{name}}` prompt、平台侧 `output_schema` 校验、稳定错误码、恢复接缝复用）、`internal/capabilities` 的 model `task` 词表扩展（`model`/`agent` 并存）、画布 `model` 调色板/面板/导出白名单、真实栈 pytest 与画布 Node 测试。
- 不改 `product/docs/spec/**`、`product/contracts/**`；无迁移；不新增 `response_schema` config 键。

## 1. 验收对照

| 票面验收 | 结论 | 证据 |
|---|---|---|
| 创作者在模型节点面板选**固定 profile** 和**响应 Schema**，运行后得到包装输出或**独立校验错误** | **passed** | 画布 `model` 节点：profile 选择器只列 `validation_state=passed`（非 admin 的 `GET /api/v1/models` 本身已隐藏未通过/已撤销），`response_mode` 选择，响应 Schema 复用 `node.output_schema` 编辑器；`reports` §2.7、`tests/f04b/canvas_model.test.mjs`（4 例）、`tests/f04b/test_t086_model_canvas.py`。运行时：json 模式输出 `structured` 对象、text 模式包装 `{text}`，两者都由平台按 `node.output_schema` 复验（§2.4） |
| 切片：model 为**一次受控生成**；approval 复用原 proposal 并 interrupt；普通审批不授权任意写工具（后半属 T087） | **passed（本票半）** | 每次节点执行恰好一次 `InvokeCapability(kind="model")`，params 只有 `task/messages[/response_schema]`，不声明 tools、不进入循环、无隐藏重试（Go 用例断言能力调用恰 1 次；真实栈断言 `platform.operations` 恰 1 条、`operation_attempts` 恰 1 条）。approval 语义本票**不引入**：`awaiting_approval` 若出现按既有 waiting 语义停车（`capability:<operation_id>` checkpoint），文档化且 Go 单测覆盖；审批决定/拒绝/过期属 T087 `not_run` |
| 验收：model 输出 object 且 Schema 失败独立 | **passed** | 真实栈 `test_schema_failure_is_independent_located_and_never_retried`：run failed、`error_code=node_output_invalid`、无节点 data artifact、end skipped、`platform.operations` 恰 1 条且无重试；Go `TestGraphRunModelSchemaFailureIsIndependentAndNonRetryable` |
| 验收：重放不重复审批；审批拒绝/过期后终止（后两条 T087 覆盖，本票在 model 语境下不得引入审批） | **本票 not_run（T087 范围）** | 本票 model 节点无审批、不产生 waiting；「重放不重复调用」以恢复接缝 Go 单测 + 真实栈 operation 回执断言覆盖（§2.6）；审批拒绝/过期真实栈留给 T087 |
| 每次调用经 capability proxy：operation_id、logical key、预算预留/结算、usage 事件；不直接调用供应商 | **passed（ledger；`usage.updated` 事件本构建未实现 → `not_run`）** | 真实栈断言 operation（`model:analyse:1`、`capability_version`=固定 profile、receipt 非空）与 `usage_ledger` 三行（reserve/release/charge，金额 0 的 stub price）；provider 是 `ORCHARA_PROFILE=dev-stub` 的 stub（receipt `stub:true`），执行器从不直连厂商。`usage.updated` SSE 事件类型在契约中存在，但本构建没有 emitter（`grep` 无实现），故只断言 ledger 事实 |

## 2. 设计决定（T087–T091 需要知道的）

### 2.1 graph 路径与计划（`internal/presetexec/graphplan.go`）

- `hasGraphConstructs` 现在对 `condition`、`join`、**`model`** 返回 true：模型定义即使没有分支也必须走 graph 调度器，否则会被 legacy/linear 路径以 `tool_unsupported_graph` 拒绝。
- `graphNodeTypes` 加入 `model`；`node_unsupported_model` 稳定码随之移除（`graphplan_test.go` 的按类型拒绝用例改用仍不支持的 `retrieve`）。
- 计划阶段解析并复验 model 的**闭集 config**（`parseModelConfig`）：恰好 `{model_profile_version,prompt_template,response_mode}`；缺键/多键（含 `response_schema`）/非 UUID（含 `latest`）/非法 response_mode/非 `type=object` 的 output_schema → 计划拒绝 `bad_config`，不产生任何 node.status / capability 调用。
- model 节点不参与 region 证明（不是 fork/join），但普通节点规则照旧：唯一入边、可达、可到 end。

### 2.2 prompt：严格 `{{name}}`，只读已绑定输入（`internal/presetexec/modelnode.go`）

- 只做字面替换：`{{name}}`（允许 `{{ name }}` 空白）。字符串按原文插入；对象/数组/数字/布尔/null 用确定性 JSON 呈现。
- 其他一律节点失败 `node_prompt_invalid`（指针 `/nodes/<i>/config/prompt_template`）：未绑定名字、属性链 `{{a.b}}`、表达式、空占位符 `{{ }}`、未闭合 `{{`、多余的 `}}`。不执行表达式/eval/属性链/循环/环境读取。
- 为什么不是计划期拒绝：票面要求“未知占位符/属性链 → **节点失败**并定位”。publish 校验的正则只匹配严格 `{{name}}`，所以 `{{task.amount}}` 能通过发布校验、由执行器在节点内拒绝（真实栈用例 4 证明）。渲染后的 prompt 超过 capability 的 1 MiB 消息上限时 `node_input_invalid`（在创建 operation 之前）。

### 2.3 一次受控生成，经 capability proxy

一次 `Client.InvokeCapability(kind="model")`：

- `OperationID = deterministicOperationID(run_id, "model:<node>:1")`，`LogicalKey = "model:<node>:1"`，`CapabilityVersion = model_profile_version`；
- `Params = {"task":"model","messages":[{"role":"user","content":<rendered prompt>}]}`，json 模式再加 `"response_schema": node.output_schema`；**text 模式不发送 response_schema**；
- 不声明 tools、不写 tool 消息、不进入第二轮；供应商可用性重试/兼容备用由 capability proxy 的既有策略负责（有 operation/attempt 回执与预算账本），执行器从不自行重试；
- 调用前执行器用 `Deps.Facts.ModelProfile` 复验“passed 且未撤销”（`model_profile_unavailable`，零 operation）；capability proxy 在 invoke 时再复验同一事实 + manifest 冻结成员资格。

### 2.4 输出与 Schema 失败独立

- text：`{"text": <data.text>}`；json：`data.structured`（必须是 object）。
- 两种模式都由**平台**按 `node.output_schema` 校验：失败 → `node_output_invalid`，消息带定位的字段指针（如 `#/summary`），节点 `failed`、不登记 data artifact、run 不得 succeeded、不重试、不换模型修 JSON、不降级为 optional/skipped。
- 真实栈用例 3 用 text 模式演示“平台独立校验”：text 模式不向能力层发送 response_schema，因此 capability 调用成功（operation state=succeeded），而节点自己的 schema 要求 stub 不会产出的 `summary` → 独立 `node_output_invalid`。
- Go 单测另覆盖 json 模式下 capability 返回 `provider_response_invalid` 的映射：provider 答案违反的就是节点自己的 response schema，因此同样归为 `node_output_invalid`（非重试）。
- 成功才登记一条 `kind=data, label=data` 的节点 data artifact（T083 数据约定），并进入 graph 的 `available` 命名空间供 join/end 按节点 id 引用。

### 2.5 稳定码

复用：`bad_config`（计划期 config 复验）、`node_input_invalid`、`node_output_invalid`、`model_profile_unavailable`、`budget_exhausted` 等 capability 既有码、`sandbox_failed`（登记失败，沿用既有语义）。
新增并在此文档化：

- `node_prompt_invalid`：模板超出严格 `{{name}}`（未绑定/属性链/表达式/不配对花括号/超长 prompt 之外的模板问题）。
- `node_capability_failed`：无法映射到更具体既有码的 capability 传输/状态失败。
- `node_capability_unknown`：capability 返回 `unknown`（既不成功也不失败，必须显式失败）。

`awaiting_approval`：按既有 waiting 语义提交 `waiting_approval` proposal（`checkpoint_id=capability:<operation_id>`、`error_code=approval_required`）并确认退出；本票的 model 节点是 pure 调用，不应出现，文档化。

### 2.6 恢复接缝

- `Deps.NodeOutputs`（T083 已装配 supervisor）对 graph 所有节点生效：已完成 model 节点的 data artifact 解码后仍按 `output_schema` 复验，通过则**只 emit succeeded**、不重复调用 capability；失败/不匹配则重新执行（日志记录）。
- Go `TestGraphRunModelReusesACompletedOutputWithoutCallingTheModel` 断言 0 次 capability 调用、复用值进入 end 输出；真实栈以“恰 1 条 operation/attempt + 回执”证明一次调用，并留 T087/T091 真实 resume 场景。

### 2.7 画布（`apps/web/canvas`）

- `NODE_TYPES`/`NODE_LABELS`/`REPEATABLE_NODE_TYPES` 增加 `model`（标签「模型」，可多个）；`defaultNode('model') = {model_profile_version:'', prompt_template:'', response_mode:'json'}`；`MODEL_RESPONSE_MODES=['json','text']`。
- 面板：profile 选择器（只列 passed；与 agent 面板同一来源 `GET /api/v1/models`）、`prompt_template` 文本域（提示只允许 `{{name}}` 取已绑定输入）+ 每个已绑定输入的「插入变量 `{{name}}`」按钮、`response_mode` 选择、**响应 Schema = 现有 `output_schema` 编辑器**、输入映射。面板不出现 `response_schema` config 字段。
- 导出白名单：`CONFIG_ALLOWLIST.model = ['model_profile_version','prompt_template','response_mode']`；`response_schema`/未知键被丢弃并在 `dropped` 中报告；凭据类键仍整体拒绝。
- 运行视图：T083 run-view 已按 node-results 的 `data` artifact 渲染 JSON/text，model 节点沿用该约定，未新造面板。
- 改动了 `apps/web/canvas/src`，因此重建并提交 `apps/web/src/assets/canvas.{js,css}`（`npm ci --offline && npm run build`）。

### 2.8 capability model `task` 词表

`internal/capabilities/params.go` 原先只接受 `task:"agent"`；T086 的 model 节点发送 `task:"model"`，因此词表扩展为 `agent|model`（仍是闭集，其他值仍 422）。dev-stub 对带 `messages` 的调用走同一确定性 agent 分支；回执仍标注 `stub:true`。

## 3. 环境与版本

| 项 | 值 |
|---|---|
| Go | `go1.27.3 windows/amd64`；`GOCACHE=E:\orchara-toolchains\gocache`，`GOMODCACHE=E:\orchara-toolchains\gomodcache` |
| PostgreSQL | 16，隔离集群 `127.0.0.1:55432`（`tests/support/stack.py` 每用例新库） |
| Python | 3.12.2；pytest 9.0.3 |
| Sandbox | WSL2 Ubuntu-24.04 + runsc（model-only 运行不启动 sandbox unit；`supervisor_exec_env` 仍需 guest rootfs digest） |
| 模型 | 仅 `ORCHARA_PROFILE=dev-stub`（`internal/modelproxy/provider.go`），无真实厂商凭据 |
| Node | v24.13.0；canvas `npm ci --offline`（esbuild 0.28.2，43 packages） |

## 4. 证据：命令与原始输出

工作目录 `E:\Orchara-wt\t086\product`。原始日志：`E:\Orchara\.scratch\t083-t091\T086\`（`pytest-t086.log`、`pytest-f06-minimal.log`、`go-build-vet.log`、`go-test.log`、`gofmt.log`、`sync-spec.log`、`node-canvas-model.log`、`node-canvas-f04a.log`、`pytest-pure-canvas.log`）。

### 4.1 Go 单测（stub seam）

```powershell
go build ./...
go vet ./...
go test ./...
gofmt -l internal cmd tools
python tools/sync_spec.py --check
```
```text
build-exit=0
vet-exit=0
（go test ./... 全部 ok，末尾）
ok  	orchara/platform/internal/workflow	(cached)
?   	orchara/platform/probes/f10_pdf_compare/go_baseline	[no test files]
?   	orchara/platform/probes/f15a_objectstore/localfs_probe	[no test files]
go-test-exit=0
gofmt-exit=0
no drift: spec copy and contract snapshot match the manifest
sync-exit=0
```

新增 Go 测试（`internal/presetexec/modelnode_test.go`）：

- `TestBuildGraphPlanAcceptsModelNodesAndSelectsTheGraphPath`
- `TestBuildGraphPlanRefusesModelConfigsOutsideTheClosedSet`（缺键/多键（含 `response_schema`）/非 UUID/非法 response_mode/非 object schema）
- `TestGraphRunModelConfigRefusalHappensBeforeAnyNodeEvent`
- `TestGraphRunModelJSONRequestsTheOutputSchemaOnceAndPublishesStructuredOutput`（一次调用、operation id、task/messages/response_schema、data artifact、node.status）
- `TestGraphRunModelTextWrapsTheProviderTextAndDoesNotRequestASchema`
- `TestGraphRunModelSchemaFailureIsIndependentAndNonRetryable`（无 data artifact、无 final output、恰一次调用、failed proposal）
- `TestGraphRunModelPromptRejectsUnknownAndExpressionPlaceholders` / `TestGraphRunModelPromptRendersOnlyBoundInputs`
- `TestGraphRunModelProfileUnavailableFailsBeforeAnyCall`（pending/revoked/no facts，零调用）
- `TestGraphRunModelCapabilityStatesMapToStableCodes`（failed/unknown/`provider_response_invalid`/`model_profile_unavailable`/未编码传输）
- `TestGraphRunModelAwaitingApprovalParksTheAttemptWithoutSuccess`
- `TestGraphRunModelReusesACompletedOutputWithoutCallingTheModel`

### 4.2 真实栈 pytest（票内 5 例 + 画布 4 例）

```powershell
python -m pytest tests/f04b/test_t086_model_node.py tests/f04b/test_t086_model_canvas.py -q
```
```text
.........                                                                [100%]
9 passed in 44.74s
```

真实栈用例（真实 PG+mTLS+supervisor+capability proxy，dev-stub provider）：

1. `test_json_model_node_publishes_a_validated_object_and_a_budget_ledger`
2. `test_text_mode_wraps_the_provider_text_and_validates_it`
3. `test_schema_failure_is_independent_located_and_never_retried`
4. `test_prompt_uses_only_bound_inputs_and_rejects_expression_placeholders`
5. `test_a_revoked_profile_is_refused_before_any_call`

### 4.3 回归（最小集）

```powershell
python -m pytest tests/f06/test_t041_capabilities.py tests/f06/test_t043_agent_node.py -q
```
```text
..................                                                       [100%]
18 passed in 127.72s (0:02:07)
```

capability proxy（含 params 闭集/预算/回执）与 agent 节点（`task:"agent"` 路径 + 真实 sandbox 工具调用）在真实栈上仍通过，证明 `task` 词表扩展没有改变 agent 行为。

画布纯模块与资产（无浏览器、无 DB）：

```text
node tests/f04b/canvas_model.test.mjs        → PASS 4 canvas T086 model tests
node tests/f04a/canvas_modules.test.mjs      → PASS 8 canvas module tests
pytest tests/f04a/test_canvas_assets.py tests/f04b/test_t086_model_canvas.py -q
                                             → 9 passed in 1.06s
```

## 5. stub / real 与边界（not_run / partial）

- **real**：PostgreSQL 16、迁移+bootstrap、真实 mTLS internal listener、编译后的 `cmd/api`/`cmd/supervisor`、真实对象目录与签名链接、真实 capability proxy（operation/attempt/usage ledger/密封 receipt 与 params）、owner-only node-results/SSE/artifact 读取、真实 workflow 编译器与 debug 提交。
- **stub**：模型 provider 是文档化 dev-stub（`stub:true`、确定性输出）；Go 单测的协议/能力 seam（`fakeClient`/`fakeFacts`/`fakeNodeOutputs`）；画布纯模块由 Node 直接执行真实源字节。均未冒充真实厂商或浏览器验收。
- **not_run**：
  - 真实厂商模型调用（本机无凭据）；provider 可用性重试/兼容备用的大套件（F06/T045/T046 已有证据，本票只在 model 语境下断言“无隐藏重试/仅一次 operation”）。
  - `usage.updated` SSE 事件：本构建无 emitter（`grep` 无实现），票面要求的 usage 事实以 `platform.usage_ledger` 行断言；事件投影留待实现该 emitter 的票。
  - approval 的拒绝/过期/重放真实场景（T087）；本票 model 语境不引入审批，`awaiting_approval` 仅 Go 单测覆盖 waiting 语义。
  - 浏览器 DOM/Playwright 交互（本批测试栈无浏览器驱动；画布以构建产物 + 纯模块 Node 测试 + bundle marker 验收）。
  - 真实“同一 run 第二次 exec 复用 model 节点”冒烟：本票无 waiting，无法构造可重复 claim 的 resume；接口/装配/Go 单测已完成，留给 T087/T091。
- **partial**：无（运行时+画布口径内）。

## 6. 合并、门禁原始输出与 HEAD

- 分支 `t/t086-model-node`，worktree `E:\Orchara-wt\t086`；基线 `integration/t083-t091` @ `111e2c2`。
- 开发中合并集成分支 tip `0deeab9`（T084 合并后）→ merge commit **`8ec146b`**，无冲突；`product/` 中 T084 改动不涉及本票文件。
- 最终 HEAD：见 `T086-REPORT.md` / 提交记录（本报告与 PROGRESS 在同一提交）。
- 合并后门禁（原始 tail 见 §4）：

| 命令 | 结果 |
|---|---|
| `go build ./...` / `go vet ./...` | exit 0 |
| `go test ./...` | 全部 ok，exit 0 |
| `gofmt -l internal cmd tools` | 空 |
| `python tools/sync_spec.py --check` | `no drift: spec copy and contract snapshot match the manifest` |
| `pytest tests/f04b/test_t086_model_node.py tests/f04b/test_t086_model_canvas.py -q` | `9 passed in 44.74s` |
| `pytest tests/f06/test_t041_capabilities.py tests/f06/test_t043_agent_node.py -q` | `18 passed in 127.72s` |
| `pytest tests/f04a/test_canvas_assets.py tests/f04b/test_t086_model_canvas.py -q`（纯，无 DB） | `9 passed in 1.06s` |

- **完整 `pytest tests/f04b -q` / 完整 `pytest tests/f06 -q` 本票 not_run（诚实口径）**：批内共享 WSL/runsc+PG 测试栈同一时刻只允许一个真实栈 pytest（编排者的全局锁 `E:\Orchara\.scratch\t083-t091\pytest.lock`）。本票第一次跑真实栈时 8/9 通过、唯一失败是测试里写错了既有码字面量（`model_unavailable` → 实际 `model_profile_unavailable`），修正后整文件 9 passed；随后长套件（其他 agent 的 f04b/f06，单次占锁 30+ 分钟）持续占用栈，编排者要求“只跑本票文件+最小回归”。因此完整 f04b/f06 留给集成 merger 在集成分支复跑；本票的最小回归选择了直接受影响的 capability proxy 与 agent 节点用例并记录在 §4.3。

## 7. 变更文件

新增：
- `internal/presetexec/modelnode.go`、`modelnode_test.go`
- `tests/support/t086.py`、`tests/f04b/test_t086_model_node.py`、`tests/f04b/canvas_model.test.mjs`、`tests/f04b/test_t086_model_canvas.py`
- `reports/f04b/T086-model-node.md`（本文件）

修改：
- `internal/presetexec/graphplan.go`（graph 触发、`model` 类型、closed config 复验、移除 `node_unsupported_model`）
- `internal/presetexec/graphrun.go`（model 分发）
- `internal/presetexec/graphplan_test.go`、`graphrun_test.go`（model 已支持后的按类型拒绝/路径选择用例）
- `internal/capabilities/params.go`（model task 词表）
- `apps/web/canvas/src/{lib/dsl.js,lib/export.js,main.js,canvas.css}`
- `apps/web/src/assets/canvas.js`、`canvas.css`（重新构建提交）
- `PROGRESS.md`

无迁移；`docs/spec/**`、`contracts/**` 未改（`python tools/sync_spec.py --check` → `no drift`）。
