# F04B/T083 运行时半 — 画布运行条件分支并只汇合已激活路径（GitHub #84）

**状态：`passed`（运行时半）**。画布/UI 半由另一个 worktree（`T083-canvas`）交付，本报告只覆盖运行时。

- 分支：`t/t083-graph-runtime`，worktree `E:\Orchara-wt\t083`，基线 `integration/t083-t091` @ `ea56392`；结束前 `git merge integration/t083-t091`（见 §6）。
- 范围：Go `internal/presetexec` 的 graph 计划/调度器、condition/join、skipped 传播、node.status 序列、run view 数据、`NodeOutputSource` 恢复接缝、`cmd/supervisor` 装配、校验层定位增强、真实栈 pytest 验收、Python LangGraph 激活证明。
- 不改 `product/docs/spec/**`、`product/contracts/**`；无迁移。既有线性执行路径（legacy/linear）选择逻辑保持不变。

## 1. 验收对照（运行时口径）

| 验收项 | 结论 | 证据 |
|---|---|---|
| 嵌套分支和并行编译一致；嵌套 condition、并行 fork 都有用例 | **passed** | 同一 DSL fixture 先过发布校验（`POST /validate` 200 valid）再由执行器原子跑通：`tests/f04b/test_t083_condition_runtime.py::test_publish_accepts_the_structured_region_fixtures`（branch/parallel/nested 三 fixture）、`::test_nested_conditions_converge_at_each_join`；Go 侧复用 40 例 P05 corpus（`validate_corpus_test.go`，含 `condition-nested-parallel`、`parallel-two-branch`） |
| join 一次且不死等；未选分支不得进入 all-of 屏障 | **passed** | 真实栈：join 的 node data 行恰 1 条、`node.status join succeeded` 恰 1 次（`test_condition_true…`/`test_parallel_fork…`/`test_nested…`）；LangGraph 真图证明 join 调用计数=1 且带超时完成（`test_t083_langgraph_regions.py`） |
| 非法跨区/无激活来源拒绝：`POST /validate` 稳定码+定位；debug 422 带定位；运行期无激活来源 → `invalid_activation` | **passed** | `/validate` 200 + `cross_region_edge` + `pointer=/edges/10` + `node_id=choose2` + 消息含 `choose2->escapee`；debug 422 `validation_failed` 同样定位且零 Run/零 job；运行期 required 源全 skipped → join failed `invalid_activation`，run `error_code=invalid_activation`（`test_cross_region…`、`test_join_without_an_activated_required_source…`） |
| 真实端到端：真实 PG + Go 二进制 + mTLS + supervisor + runsc，从 REST 提交 debug run，断言 SSE/node.status、节点结果、最终输出 | **passed** | `tests/f04b` 14 passed；每个执行用例均从真实 draft/debug API 提交、真实 `cmd/supervisor exec --once` 领取执行、真实 run-scoped 产物路由、owner-only node-results + `GET /runs/{id}/events` SSE 重放；见 §4 |
| 跳过分支可见：node.status 中未选分支为 `skipped`，join 结果封套 `status=skipped` | **passed** | `right: ["skipped"]`（无 pending/running）、join envelope `{"right":{"status":"skipped"}}` 且无 `value`；SSE 重放序列与 DB `run_events` 一致 |
| 记录版本/输入 hash/命令/原始结果/环境；stub/real 分报 | **passed** | §4/§5；真实执行是真实 sandbox，模型/能力接缝为 dev-stub 或 Go fake seam 的用例单独标注 |

补充（brief §6/§8）：

| 项 | 结论 | 证据 |
|---|---|---|
| 校验层 targeted 用例（edge 指针的 `node_id`、端点消息） | **passed** | `internal/catalog/draft_test.go::TestCheckExecutableResolvesANodePointerToItsID`（`/edges/<i>` → source）；`nodeIDAt`/`edgeEndpointsAt` 实现；不改 rules.json |
| Python LangGraph 激活/skipped/join 次数证明（P2） | **passed** | `worker/platform_worker/compiler/regions.py` + `tests/f04b/test_t083_langgraph_regions.py`（真实 `langgraph 1.1.6`，stub adapter 记录调用；四类fixture + 超时防死等 + 未配对 join 拒绝） |
| 恢复接缝（P1） | **passed（接口+真实 supervisor 装配+Go 单测）**；真实二次 exec 冒烟 **not_run** | `presetexec.Deps.NodeOutputs`、`cmd/supervisor` 读取 `platform.run_node_outputs(kind='data')`+`artifacts(object_key,sha256)`+对象根并复验 hash；`graphrun_test.go::TestGraphRunReusesCompletedNodeOutputsWithoutReExecuting`；真实 resume 场景需可重复 claim（本票无 waiting，见 §5） |

## 2. 设计决定（后续 T085–T091 必须知道）

### 2.1 graph 路径选择与区域证明（`internal/presetexec/graphplan.go`）

- `execute()` 选择顺序变为：**含 `condition` 或 `join` → graph 计划/调度**；否则维持原来的 `legacyPreferred` → T033 legacy / `buildLinearPlan` → linear。这样“单 python 节点 + `input#/file`”的旧图只要带 region 就不会被 legacy 路径截走（有 Go 测试 `TestGraphRunKeepsTheLegacyLinearPathOutOfGraphDefinitions`）。
- 执行器**不复制校验器全表**，但重证调度所依赖的结构：唯一 start/end、DAG、双向可达、非 join 入度 ≤1（`merge_without_join`）、fork/join 双向配对（`invalid_region`）、condition 端口恰为 cases∪{default_port}（`bad_port`）、支配/后支配求 inside 集合、跨区边（`cross_region_edge`，定位 `/edges/<i>`）、join 直接前驱=required∪optional 且互斥无重复（`bad_join_sources`）、区域只能严格嵌套。
- 区域成员**不持久化**，每次从冻结 definition 重算（回答 D-f04b Q2 的本票口径）。
- 本票 graph 路径可执行节点：`start/end/python/agent/call_app/condition/join`；`model/retrieve/approval` 按类型给稳定码 `node_unsupported_model` / `node_unsupported_retrieve` / `node_unsupported_approval`（未知类型仍 `tool_unsupported_graph`）——T086/T087/T088 直接替换。

### 2.2 调度器激活与跳过（`internal/presetexec/graphrun.go`）

- 状态枚举保留 `pending/running/succeeded/failed/skipped/waiting`；本票不产生 `waiting`。
- 边 taken：普通节点成功后**所有**出边 taken；condition 只 taken 选中 port 的出边；join 成功后所有出边 taken。
- 普通节点唯一入边未被 taken → 只 emit `skipped`（不进入 pending/running），并沿拓扑自然传播到整段分支；**嵌套 region 的节点与被跳过的嵌套 join 也 skipped**。
- join：入边 settled 后，若其 region 的 fork 已 skipped → join 自身 skipped；否则要求**至少一条入边 taken 且至少一个被激活的 required source**（D-f04b F6），不满足 → join `failed` + `invalid_activation` + run 同码。`optional` 源本票只解析/保留、按 skipped 记录，可选失败语义属 T085。
- 失败：failed 节点 emit `failed`，拓扑序对尚未 settled 的节点 emit `skipped`，再提交 failed result（沿用线性 `skipRemaining` 的可观测语义）。取消/超时/心跳丢失/沙箱错误沿用线性节点代码的错误码与提交顺序。
- join 每次 region 激活恰执行一次：一次 pending/running/succeeded、一条 data artifact、一条 node-results 行。

### 2.3 run view 数据约定（画布半消费；非契约）

- `node.status`：每个节点按拓扑序 `pending → running → succeeded|failed`，未选分支只发一条 `skipped`；payload 仍严格 `{node_id,state,attempt_no}`。
- condition 节点 data artifact：`{"port":"<selected>","matched_case":<0-based index>|"default"}`（label `port`）。该输出同时进入 join 封套（如果 condition 作为 branch source 被引用）。
- join 节点 data artifact：`{"results":{"<source_id>":{"status":"succeeded|skipped","value":{...}}|{"status":"skipped"}}}`（label `results`）；succeeded 才有 `value`。`end` 节点按 `output_schema` 封 final output（本票 fixture 为 `{"results":<envelope>}`）。
- run 失败时 `runs.error_code` = 节点稳定码（`invalid_activation`、`node_input_invalid`、`tool_crash` 等，走既有 finalize 行为）。
- python 节点 data 行 label 仍为 `data`、文件用真实文件名、日志 `log`（与 T042 一致）。
- 不新增契约端点；画布读现有 owner-only `GET /runs/{id}/node-results`、`GET /artifacts/{id}/content`、SSE `GET /runs/{id}/events`。

### 2.4 稳定码

复用校验器 vocabulary：`bad_config/bad_edge/duplicate_id/start_end_count/cycle/unreachable_node/cannot_reach_end/merge_without_join/invalid_region/bad_port/cross_region_edge/bad_join_sources/missing_value`。新增并文档化：
- `condition_operand_invalid`：condition 运算元 JSON 类型不符（如对金额字符串做 gt）。
- `invalid_activation`：join 无激活 required source（spec 已有名字）。
- `node_unsupported_model` / `node_unsupported_retrieve` / `node_unsupported_approval`：按类型的确定性拒绝。
- 保留 `node_input_invalid`/`node_output_invalid`（指向定义路径 `/nodes/<i>/inputs/<name>`、`/nodes/<i>/config/cases/<j>` 等；错误消息携带定位，owner API 仍不返回指针）。

### 2.5 恢复接缝（T087/T091 的入口）

- `presetexec.Deps.NodeOutputs NodeOutputSource`：`NodeData(ctx, runID) (map[nodeID][]byte, error)`。
- graph 开始读取本 run 已完成的 `data` 节点输出：直接复用、**只 emit `succeeded`**（不重复 pending/running），不重复外部调用；join 用复用值。解码后仍按节点 `output_schema` 复验，损坏/不匹配/读取失败 → 该节点重新执行（记日志），绝不使用未验证值。
- supervisor 装配：`cmd/supervisor/exec.go::dbAccess.NodeData` 只读 DSN 查 `platform.run_node_outputs(kind='data')` join `platform.artifacts(state='ready', not deleted)`，用 `objectstore.Open(object_key)` 从对象根读取，**逐字节复算 sha256 与 artifacts.sha256 比对**，不匹配则跳过；不复用 `DownloadURL`（只允许 input）。
- 本票不实现 approval/child 等待；真实“同一 run 第二次 exec”冒烟不做（见 §5）。

### 2.6 节点执行复用（重构边界）

- `runStartNode` → `executeStartNode(node, definition, inputs)`；`runPythonNode` → `executePythonNode(node, available, inputs, nodeHost{failed,skip})`（data/file/log、错误码、refuse/fail 顺序与线性完全一致）；`runEndNode` → `executeEndNode(definition, node, available, lastSandbox, report)`。
- agent/call_app 以“单节点 linear view”（`graphLinearView`）复用既有受控循环/委派代码：共享 `available`（graph 全部已成功输出）、`scopeIDs`（本 run 密封节点集合，agent 上下文授权面）、`skip` 钩子；`linearRun` 新增可选字段 `available/skip/scopeIDs`，线性路径为 nil 时行为不变。
- 所有节点产物仍走 `uploadNodeArtifact` + `RecordNodeResults`；`join`/`condition` 的 data artifact 与本 run final output 均先 register 后 emit succeeded。

### 2.7 校验层增强（不改规则）

- `catalog.nodeIDAt` 对 `/edges/<i>...` 返回该边 source 节点（契约 `node_id?` 字段允许），`cross_region_edge`/`bad_port` 因此同时有 edge 指针和可高亮节点。
- `catalog.executableDocument` 对边级错误在 message 追加 `source->target`（如 `choose2->escapee`）；`rules.json` 未改、`sync_spec --check` no drift。

## 3. 环境与版本

| 项 | 值 |
|---|---|
| Go | `go1.27.3 windows/amd64`；`GOCACHE=E:\orchara-toolchains\gocache`，`GOMODCACHE=E:\orchara-toolchains\gomodcache` |
| PostgreSQL | 16，隔离集群 `127.0.0.1:55432`（`tests/support/stack.py` 每用例新库） |
| Python | 3.12.2；pytest 9.0.3；`langgraph 1.1.6` |
| Sandbox | WSL2 Ubuntu-24.04 + runsc `release-20241118.0`，rootfs digest `sha256:03c3f647…412ff5` |
| 模型 | 仅 `ORCHARA_PROFILE=dev-stub`；本票 E2E 不调用模型（python 分支），agent graph 单测用 Go fake capability seam（stub 层），真实厂商模型 not_run |
| 基线/HEAD | 见 §6（合并后 HEAD） |

输入与版本 hash（真实 E2E）：
- 本次 run 的 task_input CSV（`tests/support/t033.py::SALES_CSV`，113 bytes）：`sha256=f226b1fef2d09026714dfbc29a01f1439d1353ba0bfb4a98fd3531db1fc612da`；
  run 的 sealed input JSON 是 `{"requires_approval":true|false}` / `{"mode":"deep"}` / `{}`（canonical JSON，随 debug 提交封存）。
- manifest 版本：`orchara-execution-manifest/1.0`、`compiler_version=orchara-compiler/1.0.0`；`POST /validate` 返回的
  `X-Manifest-SHA256` 在 branch/parallel/nested 三 fixture 上均断言为 64 hex（同一 draft revision 的冻结字节）。
- sandbox rootfs digest：`sha256:03c3f647d1d6e68173bfa9df84b87e5d6162d351bfdd55e4c3b208c0b5412ff5`；runsc `release-20241118.0`。

## 4. 证据：命令与原始输出

工作目录 `E:\Orchara-wt\t083\product`。原始日志：`E:\Orchara\.scratch\t083-t091\T083\`（`go-gates.log`、`pytest-gates.log`、`f04b.log`）。

### 4.1 新验收套件（真实栈）

```powershell
python -m pytest tests/f04b -q
```
```text
..............                                                           [100%]
14 passed in 110.23s (0:01:50)
```

用例清单（`tests/f04b/`）：
- `test_t083_condition_runtime.py`（真实 PG+mTLS+supervisor+runsc）：
  1. `test_condition_true_runs_only_the_selected_branch_and_join_once`：true → left 执行，right 仅 skipped；condition data `{port:"yes",matched_case:0}`；join envelope `left succeeded+value/summary`、`right skipped`；final output 封套；node.status 6 节点序列；SSE 重放一致；join succeeded 恰 1 次；无孤儿 sandbox。
  2. `test_condition_default_port_runs_the_no_branch`：false → 默认 port，`{port:"no",matched_case:"default"}`。
  3. `test_parallel_fork_runs_both_branches_and_joins_once`：start 双分支都执行，join 一次，两源都 succeeded。
  4. `test_nested_conditions_converge_at_each_join`：mode=deep → 内层 join2 一次（deep succeeded/shallow skipped），外层 join 一次（join2 value=内层封套/direct skipped）。
  5. `test_join_without_an_activated_required_source_fails_closed`：required=[left], optional=[right]，走 no → `invalid_activation`；join failed、end skipped、run failed、无 final output、无 join 行。
  6. `test_cross_region_edge_is_located_on_validate_and_debug`：`/validate` `cross_region_edge` `/edges/10` `choose2` 消息 `choose2->escapee`；debug 422 同定位；零 Run/零 job。
  7. `test_publish_accepts_the_structured_region_fixtures`：branch/parallel/nested 均 valid + `X-Manifest-SHA256` 64 hex。
- `test_t083_langgraph_regions.py`（真实 LangGraph，无 DB）：7 passed，1.36s；含独立 P05 Python 校验器先验 + 激活集合/skipped 集合/join 计数 + 超时防死等。

### 4.2 Go 单测（stub seam）

```powershell
go test ./internal/presetexec ./internal/workflow ./internal/catalog
```
```text
ok  orchara/platform/internal/presetexec
ok  orchara/platform/internal/workflow
ok  orchara/platform/internal/catalog
```

新增 Go 测试要点：
- `graphplan_test.go`：区域证明（flat/nested/parallel）、跨区边定位 `/edges/10`、配对/源覆盖/合并/环/按类型拒绝、condition 全运算语义（无强转、数值仅 float、in/exists/缺失值、未绑定定位）。
- `graphrun_test.go`：条件 true/default、并行、join 一次、fail-closed `invalid_activation`、失败跳过、恢复接缝复用（0 次 sandbox）、legacy 路由不被截走。
- `graph_nodes_test.go`：graph 路径 agent（受控单轮 + join 取到 agent 输出）与 call_app（一次 spawn/一次 child 读取 + join 取到 child 输出）。

### 4.3 回归门禁（原始输出见 `.scratch/t083-t091/T083/pytest-gates.log` 与 `go-gates.log`）

```powershell
python -m pytest tests/f06 -q      # F04A 线性/agent 回归
python -m pytest tests/f04a -q
python -m pytest tests/tooling -q
go build ./... ; go vet ./... ; go test ./...
gofmt -l internal cmd tools
python tools/sync_spec.py --check
```
结果：见 §6 的“门禁”原始输出尾部。

## 5. stub / real 与边界（not_run / partial）

- **real**：PostgreSQL 16、迁移+bootstrap、真实 mTLS internal listener、编译后的 `cmd/supervisor`/`api`/`maintenance` 二进制、真实对象目录与签名链接、真实 WSL2/runsc sandbox、真实 SSE、owner-only 读取。
- **stub**：Go 单测的协议/能力/sandbox seam（`fakeClient`/`fakeSandbox`/`fakeFacts`/`agentScript`）；LangGraph 证明的记录型 stub adapter。均未冒充生产验收。
- **not_run**：
  - 真实“同一 run 第二次 exec 复用已完成节点”冒烟：本票不产生 waiting，无法构造可重复 claim 的 resume 场景；接缝接口+supervisor 装配+Go 单测已完成，留给 T087/T091 在真实等待后补测。
  - 真实厂商模型路径（无凭据）；画布/UI 半（另一个 worktree）。
  - `optional` 分支失败收敛/失败隔离并发执行（T085）；本票 only 记录 optional、按 required-activation 规则 fail-closed。
- **partial**：无（运行时口径内）；T085–T091 的扩展点已按 §2 预留。

## 6. 合并、门禁原始输出与 HEAD

（见文末更新；`.scratch/t083-t091/T083-REPORT.md` 与 `product/PROGRESS.md` 有同样摘要。）

## 7. 变更文件

新增：
- `internal/presetexec/graphplan.go`、`graphrun.go`、`condition.go`
- `internal/presetexec/graphplan_test.go`、`graphrun_test.go`、`graph_nodes_test.go`
- `tests/f04b/conftest.py`、`tests/f04b/test_t083_condition_runtime.py`、`tests/f04b/test_t083_langgraph_regions.py`
- `tests/support/t083.py`
- `worker/platform_worker/compiler/regions.py`
- `reports/f04b/T083-condition-branching.md`（本文件）

修改：
- `internal/presetexec/presetexec.go`（graph 路由、`Deps.NodeOutputs`、`manifestEdge.Port`）
- `internal/presetexec/linear.go`（节点执行函数参数化 + `linearRun` 的 `available/skip/scopeIDs`）
- `internal/presetexec/agent.go`、`callapp.go`、`agentcontext.go`（graph 视图钩子）
- `internal/presetexec/plan.go`（`planBinding.Literal` 的 `omitempty`，仅影响内存往返）
- `cmd/supervisor/exec.go`（`NodeData` 恢复接缝装配）
- `internal/catalog/draft.go`、`draft_test.go`（edge 指针 `node_id` + 端点消息）
- `tests/support/t083.py`（新增，非修改）
- `PROGRESS.md`

无迁移；`docs/spec/**`、`contracts/**` 未改（`python tools/sync_spec.py --check` → `no drift`）。
