# F06/T047 — 授权上下文与受保护事实（issue #48）

**状态：`passed`**（范围 = agent 节点每轮模型输入只含本次 Run 的已解析授权来源；预算 =
`context_window - max_output_tokens` 的确定性估算；超限按“低相关历史 → 旧工具结果细节 →
冗长上游文本”截断；用户否定/来源引用/审批参数/精确金额统计逐字保留并进入 trace；摘要仅展示、
绝不作为事实来源；F09 个人记忆存储不存在 → `not_run`，不注入假记忆；trace 作为 agent 节点的
`context-trace` 文件行持久化，`/runs` 面板展示来源/截断原因/受保护键名）。分支
`t/t047-context`，worktree `E:\Orchara-wt\t047`，基线 `2b2532c`（T046 合并）。
本票不 push、不关闭 issue，未改 `docs/spec/**`、`contracts/**`、迁移 0001–0028，未新增迁移。

## 1. 验收对照（issue #48 / T047）

| 验收项 | 结论 | 证据 |
|---|---|---|
| 输入仅含当前授权来源 | 通过 | `internal/context` 的 `Scope{NodeIDs,ArtifactIDs}` 由本次 sealed plan 的 start/python/agent 节点派生；每个 `ResolvedInput` 必须带来源（literal/start/python/tool），节点或 artifact 不在 scope → typed `context_source_unauthorized`，不会 best-effort 包含；memory 行必须同 owner+application，否则同样拒绝。Go 单测 `TestAuthorizedSourcesOnly`、`TestConfirmedMemoryIsIncludedOnlyForTheSameOwnerAndApplication`；真实栈只出现本 run 的 `start/stats/analysis` 节点 id，`tests/f06/test_t047_context.py` |
| 超限优先减少低相关历史/检索 | 通过 | 固定顺序：先丢弃低相关/旧历史与仅展示摘要，再压缩旧工具结果细节（保留 protected 分支），再截短非 protected 的上游文本；`Trace.Truncations` 的 reason 顺序有断言（`TestOverLimitOrderAndProtectedFactsSurvive`、`TestHistoryWithoutProtectedFactsIsDroppedBeforeTheInputIsTouched`）；真实栈强制小窗口观察到 `verbose_upstream_text`（`test_t047_context.py`） |
| 用户否定条件、来源和审批参数不丢 | 通过 | 固定 protected 键表：`exclude/must_not/negative*/negations`、`sources/citations/document_id(s)/references`、`approval/proposal/target(s)/params`、`amount(s)/total/currency/quantity/price/count/stats` 等；匹配键整枝逐字保留，截断只作用于非 protected 分支。Go 单测断言 bounded input 仍含 `"exclude":["refunds"]`、`"sources":["doc-1"]`、`"approval":{...}`；真实栈 model params 仍含 `"total":"250.00"`、`"order_count":3` |
| 摘要不用作授权或金额计算 | 通过 | `HistoryMessage.Summary=true` 的消息**不进入模型输入**、不扫描 protected、不参与任何事实派生；`Trace.DisplayOnlySummaryCount` 记录数量。Go 单测注入声称 `"amount":"999999.00"` 的摘要，模型输入中不存在该串，权威 `100.00` 仍在且 protected 只有真实路径。本构建没有摘要生成器，摘要 seam 仅有 builder 侧文档/测试，未接模型生成（见 `not_run`） |
| 端到端入口实际演示；失败、当前授权与重试/恢复边界有断言，不以单层 mock 成功代替 | 通过 | 真实 PG + 编译 `cmd/api`/`cmd/supervisor` + 真实 mTLS + 真实对象字节 + 真实 WSL2/runsc python/tool 节点；`test_t047_context.py::test_budgeted_context_truncates_verbose_text_and_keeps_protected_facts` 走真实 debug 提交 → supervisor → capability → sandbox，读取**封存的模型 params artifact** 证明模型实际看到的内容；`test_protected_minimum_over_budget_is_a_stable_context_failure` 证明 protected 最小值放不下时稳定失败、零 operation、analysis failed/end skipped |
| 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件标 blocked，未运行标 not_run | 通过 | 见 §5/§6/§7 |

## 2. 交付内容

### 2.1 授权上下文 builder（`internal/context/context.go`，包名 `agentcontext`）

- 输入：`Request{Scope, System, Inputs []ResolvedInput, History []HistoryMessage, Tools []DeclaredTool,
  Model ModelConfig, Memory MemorySource}`；输出：`Output{Messages []map[string]any, Trace Trace}`。
- `Scope` 由 executor 从 sealed plan 派生（`agentContextScope`：start/python/agent 节点 id，
  artifact 白名单为空），builder 对每个来源做类型化复核；`literal` 只允许不带 node/artifact。
- 预算：`budget = ContextWindow - MaxOutputTokens`，模型配置缺字段时用文档化默认
  `128000/4096`（`presetexec.DefaultAgentContextWindow/DefaultAgentMaxOutputTokens`）。
  估算启发式固定为 `ceil(UTF-8 bytes / 4)`，每条消息再加 4 tokens 框架开销；不使用 provider
  tokenizer、时钟、随机数或网络，因此重试/重放构造同一输入（`TestBuildIsDeterministic`）。
- 截断顺序（与 brief 一致）：
  1. 仅展示摘要与低相关/旧历史 chunk（`low_relevance_history` / `display_only_summary`）；
  2. 旧工具结果细节：保留全部 protected 分支，丢弃非 protected 细节（`old_tool_detail`）；
  3. 冗长上游文本：按 1024→256→64→0 字节逐级缩短非 protected 字符串，protected 键整枝跳过
     （`verbose_upstream_text`）；
  4. 最后手段（顺序不变）：最新工具细节、可选记忆、仅展示/剩余历史、输入骨架；仍放不下 →
     typed `context_budget_exhausted`。**protected 分支在所有步骤中逐字保留**，不会静默截断。
- `protectedMinimum` 先计算：system + 仅 protected 分支的当前输入；超过预算立即
  `context_budget_exhausted`，不进入任何截断尝试。
- Trace：`sources`（kind/node_id/artifact_id/label/estimated_tokens/protected）、`truncations`
  （source/reason/tokens_removed）、`protected`（排序后的点路径键名）、`estimated_tokens`、
  `budget_tokens`、`reserved_output_tokens`、`memory_state`、`display_only_summary_count`；
  **不含任何消息正文、金额原文、记忆文本或 secret**（`TestTraceShapeNamesSourcesAndBudgetWithoutContent`）。
- 稳定错误码：`context_invalid`、`context_budget_exhausted`、`context_source_unauthorized`；
  agent 循环把 builder 的 typed code 原样作为 run `error_code`（`contextFailureCode`）。

### 2.2 F09 记忆 seam（无存储，不造假）

- `MemorySource` 接口：`ConfirmedMemories(ctx, ownerID, applicationID) ([]Memory, error)`。
- 生产实现 `NoMemorySource{}` 永远返回空：本批次 F09 个人记忆存储不存在，trace 记
  `memory_state="not_run"`，模型输入没有任何 `confirmed_memory`；这不是“stub 假记忆”，而是
  诚实的“无存储”。
- 若未来 F09 接入：只消费 `State=="active" && Confirmed` 且 owner+application 完全匹配的行；
  外 owner/外应用行 → `context_source_unauthorized`（不静默过滤成部分记忆）；后端错误 →
  `unavailable` 且不注入（fail closed）。
- 本票 Go 单测覆盖 included/empty/not_run/unavailable/foreign 五种状态；真实栈断言
  `memory_state=="not_run"` 且无 memory 消息。

### 2.3 Agent 循环集成（`internal/presetexec/agent.go`、`agentcontext.go`）

- 每轮模型调用前重建消息：`Build(scope, system=instructions+平台约束, authorizedInputs,
  history(tool messages), declaredTools, modelBudget, NoMemorySource{})`；模型轮次不携带
  executor 手拼的任意 map。
- `resolvedAgentInputs` 从 sealed `node.Inputs` 的 ref 派生来源：`input#`→start、
  `node:<python>#`→python、其它 node→tool、literal→literal；标签排序保证确定性。
- `ModelFact` 增 `ContextWindow/MaxOutputTokens`；supervisor 从
  `platform.model_profile_versions.config` 的 `context_window`/`max_output_tokens` 只读解析，
  缺省为 0 时 executor 用默认值（trace 记录实际预算）。
- 成功时上传/登记一个 `kind=file` 节点行：label `context-trace`、media
  `application/json`、内容 JCS 文档 `{version:1,node_id,turns:[Trace]}`；与最终输出 data 行、
  工具 file/log 行一起 `node-results` 上报。失败路径（含 `context_budget_exhausted`）不产生
  trace 文件行（见 §8 偏差 2）。
- 未复制 T046 预算/金额逻辑：`internal/budget`、`usage_ledger`、`operation_attempts` 金额列
  一行未改；本票只计算“模型输入 token 容量”，不是费用账本，也不做预留/结算。

### 2.4 `/runs` 上下文跟踪面板

- `runs.html` 在 Agent 面板内新增 `#agent-context-status` / `#agent-context-trace`，标题
  “上下文跟踪（F06/T047：授权来源 / 截断 / 受保护事实）”。
- `runs.js` 纯函数 `contextTraceRowOf` / `describeContextTrace` / `contextTruncationReasonText`，
  渲染 `budget`、授权来源、截断原因与移除 token 数、受保护键路径、`memory_state`（not_run 时
  明示 F09 未实现）；`renderContextTrace` 通过既有 owner-only artifact content 路由读取
  `context-trace` 文件，不用新接口、不显示正文。
- Node 执行测试：`tests/f06/test_t047_pages.py` 3 用例（页面结构、runs.js marker、纯函数输出与
  “无 trace 行”诚实降级）；浏览器 DOM 驱动仍 `not_run`。

### 2.5 迁移/契约

- **无迁移**：复用 T042 的 `platform.run_node_outputs` 与既有 artifact/content/节点结果端点；
  无新表/新列。
- 未改 `docs/spec/**`、`contracts/**`、迁移 0001–0028；`sync_spec --check` no drift。
  trace 文档与 `context-trace` label 是非契约的节点产物约定，已写入本报告与 ADR 0007 追加。

## 3. T050 可直接使用的确切接口

- **Go builder**（`orchara/platform/internal/context`，package `agentcontext`）：
  - `Build(ctx context.Context, req Request) (Output, error)`
  - `Request{Scope Scope; System string; Inputs []ResolvedInput; History []HistoryMessage;
    Tools []DeclaredTool; Model ModelConfig; Memory MemorySource}`
  - `Scope{RunID, OwnerID, ApplicationID string; NodeIDs, ArtifactIDs []string}`（空 = 无授权）
  - `ResolvedInput{Label string; Source Source; Value any}`；
    `Source{Kind, NodeID, ArtifactID, Label string}`，Kind ∈ `literal|start|python|tool|memory`
  - `HistoryMessage{Role string; Content any; Source Source; Relevance int; Summary bool}`；
    `Summary=true` 的消息永不进入模型输入
  - `ModelConfig{ContextWindow, MaxOutputTokens int64}`；`MemorySource` / `NoMemorySource{}`
  - `Output{Messages []map[string]any /* role,content */, Trace Trace}`
  - `Trace{Sources []TraceSource; Truncations []Truncation; Protected []string;
    EstimatedTokens, BudgetTokens, ReservedOutput int; MemoryState string;
    DisplayOnlySummaryCount int}`（JSON 字段见 §2.1）
  - 错误码常量：`CodeInvalid="context_invalid"`、`CodeBudgetExhausted="context_budget_exhausted"`、
    `CodeSourceUnauthorized="context_source_unauthorized"`
- **Agent 侧**：`presetexec.ModelFact{ID,ValidationState,Revoked,ContextWindow,MaxOutputTokens}`；
  `presetexec.DefaultAgentContextWindow=128000`、`DefaultAgentMaxOutputTokens=4096`；
  `agentContextScope(plan,node)`、`resolvedAgentInputs(plan,node,bindings)`、
  `modelContextBudget(fact)`、`contextFailureCode(err)`。
- **持久化约定**：agent 节点 `run_node_outputs` 中新增 `kind=file`、`label=context-trace`，
  artifact purpose `node_output`、media `application/json`；JCS 文档
  `{"version":1,"node_id":"<agent>","turns":[<Trace>,...]}`，每轮一个 Trace。
  前端可复用 `contextTraceRowOf`/`describeContextTrace`。
- **模型配置**：`platform.model_profile_versions.config.context_window` /
  `config.max_output_tokens` 是预算来源；缺省 0 → executor 默认 128000/4096，并在 trace 的
  `budget_tokens`/`reserved_output_tokens` 中记录实际值。

## 4. 测试

### 4.1 Go 单测（`internal/context`，11 个顶层 test）

- 确定性估算（空/ASCII/多字节）、authorized source 白名单与 foreign artifact、trace 形状/
  无正文泄漏/预算字段、Build 确定性、超限顺序与 protected 逐字存活、摘要仅展示且不改变金额、
  工具细节压缩保留 protected、protected 最小值超预算 typed failure、非法 model config/非 JSON
  tool result、记忆 included/foreign/empty/not_run/unavailable、低相关历史优先丢弃。

### 4.2 Go 回归

- `internal/presetexec` agent 单测更新：agent 节点结果 4→5 行（新增 context-trace file 行），
  file 2→3；新增断言 trace 上传内容含 `turns/protected/memory_state=not_run/budget_tokens`，
  且不含 `250.00` 或最终输出正文。
- 全仓 `go test ./...` 通过（见 §6）。

### 4.3 真实栈（`tests/f06/test_t047_context.py`，2 用例）

1. `test_budgeted_context_truncates_verbose_text_and_keeps_protected_facts`：
   模型 profile 配置 3072/1024（预算 2048），agent 绑定 `stats` 与 `start` 全量（约 13 KB 的
   `source`）；真实 run 成功；trace 文件为 1 行、`turns>=1`、`budget=2048/reserved=1024`、
   `memory_state=not_run`、`protected` 含 `stats`、截断 reason 含 `verbose_upstream_text`、
   source kind 含 python/start；trace 文本无 `250.00`/长输入原文；**封存 model params** 的
   user 消息含 `"total":"250.00"`、`"order_count":3` 与 `...[truncated]`，不含完整 13 KB 输入；
   最后一轮 model params 的 tool 消息仍含精确 `total=250.00`；来源节点只在本 run 的
   `{start,stats,analysis}` 内；无 `confirmed_memory`。
2. `test_protected_minimum_over_budget_is_a_stable_context_failure`：配置 200/100（预算 100，
   小于 system+protected 最小值）；run failed、`error_code=context_budget_exhausted`、
   0 operation、无 analysis 行/final artifact、analysis `pending→running→failed`、end `skipped`。

### 4.4 页面（`tests/f06/test_t047_pages.py`，3 用例）

- `/runs` 结构含上下文跟踪 marker；`/assets/runs.js` 暴露
  `describeContextTrace`/`contextTraceRowOf`/`renderContextTrace`/`context-trace`，并复用
  `fetchArtifactText`/`artifactContentPath`；Node 执行纯函数：来源、截断原因（含中文映射）、
  受保护路径、预算、`memory_state=not_run`、无 trace 行/null 的诚实降级。

### 4.5 回归

- `test_t043_agent_node.py` 9 用例、`test_t043_pages.py` 3 用例通过（T043 仅更新节点结果
  行数 8→9、analysis kind/file 集合加入 `context-trace`，其它断言未削弱）。
- `test_t044_streaming.py`/`test_t044_pages.py`/`test_t044_python.py`、
  `test_t046_budget.py`/`test_t046_pages.py` 共 32 用例通过。
- 未跑全量 f05/f03（本票未触碰），符合 brief。

## 5. 版本 / 输入 / 命令 / 环境 profile

- 分支 `t/t047-context`，基线 `2b2532c`（T046 合并）。代码提交：
  `37c0966`（builder + 单测）、`c1dae1f`（agent 集成/trace/UI/T047 测试 + T043 期望更新）；
  证据提交见 §9。
- Go：`E:\orchara-toolchains\go\bin\go.exe`（go1.27.3），
  `GOCACHE=E:\orchara-toolchains\gocache`、`GOMODCACHE=E:\orchara-toolchains\gomodcache`。
- Python 3.12（`python -m pytest`）；PostgreSQL 127.0.0.1:55432；真实 WSL2/runsc
  `release-20241118.0`（python 节点与 agent 工具调用各一次真实执行）。
- 模型：T041 dev stub（`provider=stub`，`stub=true`，确定性）；golden model
  `22222222-2222-4222-8222-222222222222`；T047 测试在 run 入队后把 profile config 改为小窗口
  （3072/1024 或 200/100），因此预算/截断在**真实请求**上发生。
- 输入：T043 的公开 sales CSV（3 行，250.00）；长 `source` 字符串用于触发上游文本截断。

## 6. 门禁原始输出

> 2026-11-04 最终复跑，全部顺序执行（未并行跑套件）；按 brief 未跑 f05/f03。
> 环境：`GOCACHE=E:\orchara-toolchains\gocache`、`GOMODCACHE=E:\orchara-toolchains\gomodcache`；
> `tests/f04a` 使用主 checkout 的 P01 locked interpreter（本 worktree 未复制 `.venv`）。

```text
$ E:\orchara-toolchains\go\bin\go.exe vet ./...
（无输出，exit 0）

$ E:\orchara-toolchains\go\bin\go.exe test ./...
ok  	orchara/platform/internal/context	...
ok  	orchara/platform/internal/presetexec	...
（26 个包 ok、0 FAIL、9 个 no test files，exit 0；尾部 6 行见下）
ok  	orchara/platform/internal/supervisor	(cached)
?   	orchara/platform/internal/toolruns	[no test files]
ok  	orchara/platform/internal/tools	(cached)
ok  	orchara/platform/internal/toolversions	(cached)
ok  	orchara/platform/internal/uuid	(cached)
ok  	orchara/platform/internal/workflow	(cached)

$ gofmt -l internal cmd tools
（空输出，exit 0）

$ python -m pytest tests/f06 -q
111 passed in 564.98s (0:09:24)

$ $env:ORCHARA_P01_PYTHON='E:\Orchara\product\probes\agent_min\.venv\Scripts\python.exe'
$ python -m pytest tests/f04a -q
36 passed in 64.76s (0:01:04)

$ python -m pytest tests/tooling -q
21 passed in 5.70s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

## 7. stub / real 分报

- **real**：PostgreSQL、编译后的 `cmd/api`/`cmd/supervisor`、mTLS internal listener、对象存储与
  owner-only artifact content、WSL2+runsc 真实 python/tool 执行、真实 capability 账本与
  `run_node_outputs`、真实 sealed model params artifact。
- **stub**：模型提供方仍是 T041 文档化 dev stub；stub 的 structured/tool-call 由确定性规则生成，
  仍经 T041 服务端校验与 agent 节点二次校验。**不是真实模型决策**。
- **fake（仅 Go 单测）**：`internal/context` 的 memory fake、`internal/presetexec` 的脚本化
  capability/sandbox seam；不产生产品状态，也不替代真实栈测试。

## 8. not_run / 偏差 / 已知限制

- **not_run（F09 个人记忆）**：本批次无个人记忆存储；生产 `NoMemorySource` 返回空，trace
  `memory_state="not_run"`，无 memory 消息。记忆 included/foreign/empty/unavailable 的语义只有
  Go 单测 seam 证据，**真实 F09 存储/读取未运行**。
- **not_run（摘要生成）**：本构建没有“把旧历史总结成摘要”的生成器；只实现了“摘要即使存在也
  仅展示、绝不进入模型输入/事实派生”的 builder 契约与测试。真实摘要生成/展示链路未运行。
- **not_run（真实厂商 adapter）**：真实 provider/真实 tokenizer 未编译；预算使用文档化
  byte/4 启发式，不是厂商 tokenizer。
- **not_run（真实栈 foreign source 注入）**：真实执行路径没有“塞入别的 run 数据”的入口，因此
  foreign node/artifact/记忆拒绝只有 Go 单测 seam 证据；真实栈只断言来源 id 属于本 run。
- **not_run（浏览器 DOM 驱动）**、全量 `pytest tests`、`tests/f00b`、f05/f03 全套。
- **偏差 1（T043 行数）**：新增 `context-trace` file 行使 agent 节点结果 4→5、全链 8→9；T043
  测试相应更新，data 恰好 1 行、日志/文件语义未变。
- **偏差 2（trace 只在成功时持久化）**：`context_budget_exhausted` 失败发生在首次模型调用前，
  当前失败路径不产生 trace 文件行（真实栈用 run error_code + node.status 断言稳定失败）。
  brief 的“persist/return trace with node result”在成功路径满足；失败路径的 trace 持久化
  列为后续增强（不做假成功，也不把失败写成有 trace）。
- **偏差 3（默认预算）**：profile config 缺 `context_window/max_output_tokens` 时使用
  128000/4096 默认值并在 trace 记录；这是文档化 fallback，不是“未配置即无上限”。
- **未做**：未 push、未关闭 issue #48；未修改 `docs/spec/**`、`contracts/**`、迁移 0001–0028；
  未新增迁移。

## 9. 提交与最终门禁

- 提交：`37c0966`（builder + 单测）、`c1dae1f`（agent 集成/trace/UI/T047 测试/T043 期望）、
  本报告/PROGRESS/ADR 追加提交（docs）。
- 最终门禁（本报告提交前的最后一次顺序复跑）：

```text
$ E:\orchara-toolchains\go\bin\go.exe vet ./...
（无输出，exit 0）

$ E:\orchara-toolchains\go\bin\go.exe test ./...
（26 ok、0 FAIL、9 no-test，exit 0）

$ gofmt -l internal cmd tools
（空输出）

$ python -m pytest tests/f06 -q
111 passed in 564.98s (0:09:24)

$ $env:ORCHARA_P01_PYTHON='E:\Orchara\product\probes\agent_min\.venv\Scripts\python.exe'
$ python -m pytest tests/f04a -q
36 passed in 64.76s (0:01:04)

$ python -m pytest tests/tooling -q
21 passed in 5.70s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

- 本票新增/更新的测试计数：`internal/context` 11 个顶层 Go test；`tests/f06/test_t047_context.py`
  2 passed、`test_t047_pages.py` 3 passed；`tests/f06` 全量 111 passed（含 T039–T046 回归）；
  T043 6+3、T044 10+4+7、T046 7+3 均绿。
- **not_run**：真实厂商 adapter/真实 tokenizer、F09 个人记忆存储与真实记忆消费、真实摘要生成
  链路、真实栈 foreign source 注入（只有 Go seam）、浏览器 DOM、全量 `pytest tests`/f00b、
  f05/f03 全套。未 push、未关闭 issue #48。
