# F04A/T042 — 运行固定 Python 节点并查看结构数据与文件（issue #43）

**状态：`passed`**（范围 = 线性 `start → python → end` 调试运行、绑定解析、固定预设工具的真实
WSL2/runsc 执行、每节点 data/文件/日志的 provenance 与 owner 只读接口、`/runs` 节点结果面板）。
分支 `t/t042-python-node`，worktree `E:\Orchara-wt\t042`，基线 `ae4c95a`（T041 已合并）。
本票不实现 agent 节点执行（T043），不 push、不关闭 issue，不改 `docs/spec/**`、`contracts/**`、
迁移 0001–0025，只新增迁移 0026。

## 1. 验收对照（issue #43 / T042）

| 验收项 | 结论 | 证据 |
|---|---|---|
| Python data、文件和日志分别可查看 | 通过 | `GET /api/v1/runs/{id}/node-results` 返回 4 行（data 1、file 2、log 1）；data 内容 = 规范化 `output.json.data`；文件字节逐个 round-trip；日志以 `purpose=log` 独立登记、不出现在 `listRunArtifacts`，owner 通过 `GET /artifacts/{id}/content` 读取；`tests/f06/test_t042_python_node.py::test_linear_run_exposes_data_files_and_logs` |
| 结构不符、路径越界与旧凭据被拒 | 通过 | 节点 `output_schema` 不匹配 → `node_output_invalid`（未登记 data/文件、无成功 proposal）；`definition.input_schema` 不匹配与不可解析绑定 → `node_input_invalid`（沙箱不启动）；旧/外部 attempt token → 409 `stale_attempt`，活 attempt 命名非本 run artifact → 404 `artifact_not_found`；越界/绝对路径/`..`/符号链接文件由 sandbox 协议层（`ParseOutputDocument` + 驱动 `find -type f ! -type l`）拒绝，Go 单测覆盖 |
| 原始工具输出不直接成为已授权 artifact | 通过 | 线性路径从不上传原始 `output.json`；data = JCS(`output.json.data`)，文件 = 逐个 admit 的 artifact，日志 = 截断 stdout/stderr；三者都先走 create/complete/register（finalize guard 置 ready），之后才写 `run_node_outputs` provenance；测试断言所有列出的 artifact JSON 都不是 `{data,artifacts}` 原始文档 |
| 端到端入口实际演示；失败/授权/重试边界有断言 | 通过 | 真实 PG + 迁移 0026 + 编译二进制 + 真实 mTLS + 真实对象字节 + 真实 WSL2/runsc `release-20241118.0`；失败（schema/绑定/输入/取消）、旧凭据、外域 artifact、legacy 回归均有断言；无单层 mock 成功 |
| 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报 | 通过 | 见 §5；真实沙箱与真实 stack 为 real，Go 单测 fake 仅覆盖执行器协议缝（stub 层），未混报 |

## 2. 交付内容

### 2.1 迁移 0026（仅 provenance）

`product/db/migrations/0026_run_node_outputs.sql` 新建 `platform.run_node_outputs`：
`(run_id uuid FK runs ON DELETE CASCADE, node_id text, kind text CHECK data|file|log,
artifact_id uuid FK artifacts, label text, created_at timestamptz, PK(run_id,node_id,kind,artifact_id))`。
artifact 行仍是内容权威，`run_artifact_uploads` 仍是 run/attempt/purpose 的预约证明，
`run_artifacts` 仍是 run 链接；本表只记录“哪个节点以哪一类产生了哪个已登记 artifact”，
不含内容、对象 key、token。0007–0025 的 `platform` 权限边界原样保留（worker/PUBLIC 无权限）。
迁移注释记录 5 条偏差（非契约表、无 ON DELETE 子句由 F07 接管、kind 封闭、label 有界、
不重复 attempt token）。

### 2.2 线性计划与执行（`internal/presetexec`）

- `plan.go`：`buildLinearPlan` 只接受恰好 3 个节点（1 start、1 python、1 end）与恰好 2 条
  链式边；分支/汇合/多 python/其他节点类型 → `tool_unsupported_graph`；`agent` 节点 →
  `agent_node_not_supported`（T043 的替换点）；python 节点必须引用固定预设工具
  `11111111-1111-4111-8111-111111111111`，否则 `tool_unsupported_tool`。
- 绑定：`{"literal": ...}` / `{"ref":"input#/..."}`（start 输出别名）/ `{"ref":"node:<id>#/..."}`
  （上游输出），RFC 6901 JSON Pointer；缺失/类型不符 → 定位到 `<node>.<input>` 的
  `node_input_invalid`。python 节点的 `file` 绑定必须解析为 artifact 事实
  （artifact_id/media_type，可选 sha256/size_bytes），且必须命中本 run 的 linked input；
  客户端路径/裸 id/不匹配事实一律拒绝，DB 事实永远优先。
- 受限 schema 子集：`type/properties/required/additionalProperties/items/enum`；类型检查含
  integer 与 number 区分；start 校验 `definition.input_schema`，python 校验节点
  `output_schema`，end 校验 `definition.output_schema`。未知 type keyword fail closed。
- 执行：start 读取 lease 的 run-input signed link（`runs.input_artifact_id`，purpose
  `run_input`），通过后把“恰好一个 linked task_input 的服务器事实”放到文档化的
  `artifact` 键；python 下载 linked input（size/sha 校验）→ `/workspace/input.json`
  （`{tool,file,artifact}` 三键，工具协议封闭）→ 真实沙箱（固定 argv/uid 65534/no network）
  → 严格解析 `output.json` → 节点 schema 校验 → admit 文件 → 登记 data/文件/日志 →
  上报 node results；end 用绑定映射 `definition.output_schema`，校验后封存
  `final_output` 并提交成功 proposal，最后提交退出证据。
- 事件：每个节点按 `pending → running → succeeded|failed|skipped` 各提交一个
  `node.status` 事件（payload 恰好 `{node_id,state,attempt_no}`，无正文）；取消/失败节点
  不提交成功，后续节点 `skipped`。`attempt_no` 由 supervisor 的可信 DB 读身份按
  run 内 attempt 序号派生（契约没有该列）。
- 取消/超时/心跳：完全复用 T033/T036 的 runner 控制；取消在下一节点前停止且不提交成功。
- Legacy 兼容（重要）：封存的 golden 定义是 `start/python/agent/end`。当 manifest 恰有一个
  预设 python 节点时走原 T033 单 python 路径，行为逐字节保持不变（不执行/不校验 agent
  节点）；没有可运行预设 python 节点的 agent 图才以 `agent_node_not_supported` 拒绝。
  这样 T033/T037 既有测试与真实运行全部保持，同时 T043 有明确的替换点。

### 2.3 节点结果接口（非契约，已在本报告与 ADR 0007 记录）

**内部报告（worker/supervisor mTLS + attempt token）**

```http
POST /internal/v1/runs/{run_id}/node-results
{
  "attempt_token": "<live attempt token>",
  "results": [
    {"node_id": "stats", "kind": "data|file|log", "artifact_id": "<uuid>", "label": "data|summary.json|log"}
  ]
}
→ 200 {"run_id": "...", "recorded": <newly inserted rows>}
```
规则：`resolveScope` 证明 live attempt（run 必须 running）；每个 artifact 必须 ready、
属于该 run/attempt（`run_artifact_uploads`）、且 purpose 与 kind 匹配
（data/file→`node_output`，log→`log`，所以日志永远不能报成 data）；一次 1–64 行；
insert-once + `ON CONFLICT DO NOTHING`（幂等重放 `recorded=0`）；错误映射沿用
`writeRunArtifactError`（409 `stale_attempt`/`run_terminal`/`run_state_conflict`/
`artifact_not_ready`，404 `artifact_not_found`，422 `validation_failed`）。

**Owner 只读（session/API key，`application:read`，getRun 的 404 规则）**

```http
GET /api/v1/runs/{run_id}/node-results
→ 200 {
  "items": [
    {"node_id":"stats","kind":"data","artifact_id":"...","label":"data",
     "media_type":"application/json","size_bytes":106,"sha256":"<64 hex>"}
  ],
  "next_cursor": null,
  "generated_at": "2026-11-03T..."
}
```
不含内容、对象 key 或签名 URL；内容仍走既有 `GET /api/v1/artifacts/{id}/content`
与 `/download`（同一 owner 校验）。日志同样经 content 读取，页面再做 4096 字符截断。

### 2.4 `/runs` 节点结果面板

`apps/web/src/runs.html` 增加可折叠 `#node-results-panel`（标题“节点结果（F04A/T042：data /
文件 / 日志）”）；`apps/web/src/assets/runs.js` 增加纯函数
`runNodeResultsPath`、`artifactContentPath`、`nodeResultKindText`、`nodeStatusText`、
`formatBytes`、`capLogText`、`prettyJSONText`、`describeNodeResultItem`、
`describeNodeResults`，以及 DOM 装配 `renderNodeResults`、`loadNodeResults`、
`recordNodeStatus`、`fetchArtifactText`、`downloadNodeArtifact`。面板按节点显示状态
（等待/运行中/成功/失败/已跳过），并用 `resultText` 明确区分“有结果/无结果”与“失败”；
data 点击后从 content 端点加载并 pretty-print；文件显示 label/类型/大小/下载；日志点击后
加载并截断显示。SSE 的 `node.status` 事件会实时更新状态。页面不使用 localStorage/
sessionStorage，不显示 object key。

## 3. 阶段接口（T043 / T049 可直接使用）

- **执行器协议缝**（`internal/presetexec.Client`）新增两个方法，`cmd/supervisor` 的
  `execClient` 已实现（worker mTLS）：
  - `SubmitNodeStatus(ctx, runID, attemptToken, nodeID, state string) error`
  - `RecordNodeResults(ctx, runID, attemptToken string, results []NodeResult) error`
    （`NodeResult{NodeID,Kind,ArtifactID,Label}`）
- **T043 agent 节点**：`plan.go` 的 `agent` 分支是唯一拒绝点（`CodeAgentNodeUnsupported =
  "agent_node_not_supported"`）；实现时把 agent 加入 `linearPlan`、替换该分支并在
  `executeLinear` 中插入 agent 步骤，即可复用 `start.py`/`runPythonNode`/`runEndNode`
  的绑定、事件、artifact 登记与结果上报。`linearPlan` 只接受 3 节点，T043 需要放宽为
  “start → [python|agent]… → end”时改 `buildLinearPlan` 的节点/边枚举即可。
- **T049 UI**：`describeNodeResults(body, statuses, runState)` 与
  `describeNodeResultItem` 是纯函数（Node 直接执行）；`runNodeResultsPath` /
  `artifactContentPath` 是路径权威；面板 id（`node-results-panel/status/nodes`）已固定。
- **数据库**：`platform.run_node_outputs(run_id,node_id,kind,artifact_id,label,created_at)`；
  owner 读取按 `created_at,node_id,kind,artifact_id` 排序；F07 删除 artifact 时必须同删该行。

## 4. 测试（真实栈）

`tests/f06/test_t042_python_node.py`（6 用例，真实 PG 迁移到 0026、真实 API/mTLS、真实对象
字节、真实 WSL2/runsc、真实 maintenance finalize 进程）：

1. `test_linear_run_exposes_data_files_and_logs`：成功链；node-results 4 行、data 内容、
   两个文件字节、日志独立且不在 artifact 列表、final_output、provenance purpose/state、
   9 个 node.status 事件（每节点 pending/running/succeeded，`attempt_no=1`）、owner-only 404、
   无沙箱残留。
2. `test_node_output_schema_violation_fails_without_registering_data`：`node_output_invalid`；
   无 node result 行、无 final_output、stats `failed`、end `skipped`。
3. `test_invalid_run_input_and_unresolved_binding_are_refused_before_the_sandbox`：
   `node_input_invalid` 两种来源，沙箱未启动。
4. `test_node_results_report_refuses_stale_and_foreign_rows`：活 attempt + 外域 artifact → 404
   `artifact_not_found`；外部 token → 409 `stale_attempt`；无行落库。
5. `test_cancel_stops_the_chain_without_a_success_or_node_result`：取消后 stats `failed`、
   end `skipped`、无 node result、无输出 artifact、无沙箱残留。
6. `test_legacy_golden_agent_graph_still_runs_the_single_python_flow`：golden 图（含 agent）
   仍走 legacy 单 python 路径，summary/CSV/log 不变且不产生 node result/事件。

`tests/f06/test_t042_pages.py`（3 用例）：页面结构、runs.js 客户端 marker、Node 执行纯函数
（路径编码、日志截断、JSON pretty、分组、“无结果” vs “失败”、legacy 空结果文案）。

`tests/f06/test_t042_migration.py`（2 用例）：0026 列/主键/外键/CHECK/ON DELETE CASCADE、
worker 权限边界。

Go 单测：`internal/presetexec/{plan_test.go,linear_test.go}`（计划拒绝矩阵、Pointer 解析、
schema 子集、线性成功链的上传 purpose/注册/node results/事件、schema 违规、输入违规、
未采集 artifact 拒绝、legacy 兼容、agent-only 拒绝、恶意 artifact 名）；`internal/execution/
noderesults_test.go`（kind/node/label/重复行的封闭校验、purpose 映射、label 上界）。

## 5. 版本 / 输入 / 命令 / 环境 profile

- 分支 `t/t042-python-node`，基线 `ae4c95a`，提交：`d784ca2`（迁移 0026 + provenance API）、
  `2e49bd9`（线性计划/执行 + 事件 + 集成测试）、`0ced4e6`（面板 + 页面测试 + 迁移测试）、
  本报告提交（见 `git log`）。
- Go：`E:\orchara-toolchains\go\bin\go.exe`，`GOCACHE=E:\orchara-toolchains\gocache`，
  `GOMODCACHE=E:\orchara-toolchains\gomodcache`；Python 3.12（`python -m pytest`）。
- 沙箱：WSL2 `Ubuntu-24.04`，`runsc release-20241118.0`，rootfs digest
  `sha256:03c3f647d1d6e68173bfa9df84b87e5d6162d351bfdd55e4c3b208c0b5412ff5`；固定预设工具
  `sales_stats`（`worker/platform_worker/nodes/sales_stats.py`，工具版本
  `11111111-1111-4111-8111-111111111111`）。
- 输入：`tests/support/t033.py` 的公开 sales CSV（UTF-8，3 行，250.00）与其期望 summary；
  run input `{"source":"t042"}`；data artifact 的 sha256 由真实上传/complete 记录。
- 实际命令（原始输出见 §6）：`go vet ./...`、`go test ./...`、`gofmt -l internal cmd tools`、
  `python -m pytest tests/f05 -q`、`tests/f03`、`tests/f06`、`tests/tooling`、
  `python tools/sync_spec.py --check`。

## 6. 门禁原始输出

> 2026-11-03 最终复跑；下列为原始尾部/计数。f05 必须独占运行：与 f03 并行时
> `ALTER ROLE orchara_worker PASSWORD` 会因并发更新同一 role 报
> ``tuple concurrently updated``，这是测试环境并发冲突，不是产品失败（单独复跑通过）。

```text
go vet ./...
（无输出，exit 0）
exit=0

go test ./...
ok  	orchara/platform/internal/toolversions	(cached)
ok  	orchara/platform/internal/uuid	(cached)
ok  	orchara/platform/internal/workflow	(cached)
exit=0；24 个 ok、0 FAIL、9 个 no test files

gofmt -l internal cmd tools
（空输出）

pytest tests/f05 -q
149 passed in 620.50s (0:10:20)

pytest tests/f03 -q
203 passed in 943.01s (0:15:43)

pytest tests/f06 -q
53 passed in 163.80s (0:02:43)

pytest tests/tooling -q
21 passed in 5.10s

python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
exit=0
```

## 7. stub / real 分报

- **real**：PostgreSQL（迁移 0026）、编译后的 `cmd/api`/`cmd/supervisor`/`cmd/maintenance`、
  mTLS internal listener、对象存储与 HMAC 签名链接、WSL2 + runsc 真沙箱、真实上传/完成/
  finalize/register、真实 SSE 事件入库、真实 sandbox cleanup 证据。
- **stub / fake（仅 Go 单测）**：`internal/presetexec` 的 fake Client/Inputs/Sandbox 用于
  枚举执行器协议缝的拒绝与成功路径；它不产生任何产品状态，也不替代真实栈测试。
- **未使用 stub 的产品路径**：node-results 端点、迁移、UI 逻辑均无 mock 成功。

## 8. not_run / blocked / 已知偏差

- **not_run**：浏览器 DOM/交互自动化（点击、折叠、滚动）；全量 `pytest tests`（由
  orchestrator 汇总）；全量 `tests/f00b`（按 brief 明令不跑，Windows 管道挂起风险）；
  T043 agent 节点真实执行（本票明确拒绝，见 §3）；T049 的后续 UI 扩展。
- **未单独 e2e 的项**：线性链的 `timeout` 分支没有独立真实沙箱用例；它与 legacy 超时共用
  同一 `sandbox.ErrTimeout` 分支，legacy 超时由 `tests/f05/test_t033_sandbox_run.py` 覆盖，
  线性取消（同一 runner 控制路径）已真实 e2e；报告不把它写成已单独运行。
  T037 工具构建/审核路径未改，回归由 `tests/f05` 门禁覆盖。
- **偏差 1**：节点 data artifact 用 RFC 8785 JCS 规范化 `output.json.data`（文档要求
  “canonical”），因此字节不等于工具 pretty-print 的 summary.json；内容等价由测试断言。
- **偏差 2**：`node.status` 的 `attempt_no` 由 supervisor 的 DB 读身份按 run 内 attempt
  序号派生（契约无该列）；不允许调用方自报。
- **偏差 3**：legacy 兼容规则（§2.2）：golden agent 图在恰有一个预设 python 节点时仍走原
  路径；只有没有可运行预设 python 的 agent 图才 `agent_node_not_supported`。这是保持 T033/
  T037 既有测试与真实行为的必要条件，T043 落地后删除该兼容分支。
- **偏差 4**：两个 node-results 端点非契约；已在 ADR 0007 追加记录，不改 `contracts/**`。
- **偏差 5（测试维护）**：`tests/f05` 的 4 个、`tests/f03` 的 6 个“迁移链尾部”断言在基线
  `ae4c95a` 就已过期（T041 已加入 0025，断言仍硬编码到 0024 且长度 24；基线即红）。本票把它们
  更新为实际链条 `0001..0026`（0025 capability ledger、0026 node outputs），没有削弱任何行为
  断言；这是让 `pytest tests/f05`/`tests/f03` 门禁为绿的必需修复。
- **未做**：未 push、未关闭 issue #43；未修改 `docs/spec/**`、`contracts/**`、迁移 0001–0025。
