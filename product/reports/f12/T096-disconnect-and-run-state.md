# F12/T096 浏览器断线与父子等待时仍能解释真实运行状态（GitHub #97）

- 状态：**`passed`**（本票交付“真实的运行状态解释手段 + 真实栈运行证据 + 诚实裁决”；不伪造首版门禁之外的产品能力）
- 分支：`t/t096-run-state-truthful-ui`，worktree `E:\Orchara-wt\t096`
- 基线：`integration/f12` @ `497f4e8`（T093 合并 tip；`merge-base --is-ancestor` 退出码 0）
- 交付 HEAD：`398ce47`（代码+测试+server 静态路由）；文档提交紧随其后，最终 HEAD 见
  `E:\Orchara\.scratch\f12\T096-REPORT.md`
- 合并：`git merge integration/f12` → **Already up to date**（集成分支仍为 `497f4e8`；合并后重跑门禁，见 §8/§11）
- 环境 profile：`ORCHARA_PROFILE=dev-stub`；无真实厂商模型/凭据；无 Playwright/Chromium

## 0. 手段声明（硬性，先读）

- **不是 Playwright / 不是真实浏览器 DOM。** 页面行为用 Node 执行仓库里**已提交的真实字节**
  （`apps/web/src/assets/runstream.js`、`runs.js`、`chat.js`、`runview.js`、`debug.js`、`attachments.js`）
  加最小 DOM/fetch shim（先例 `tests/f08/test_postreview_pages.py`、`tests/f04b/test_t083_runview.py`）。
  它证明"真实脚本字节 + 真实请求形状 + 渲染文本"，**不证明**浏览器布局、媒体查询、真实 `EventSource`
  行为或真实网卡断线。
- **不是物理网卡断线。** “断线”= 关闭真实 HTTP 连接 + 重新带 `Last-Event-ID` 连接（T032 已用真实
  `http.client` 做过）；“窗口过期”= 真实 `cmd/api` 进程把 `ORCHARA_RUN_EVENT_RETENTION` 压到 `50ms`
  后返回的真实 `410`，不是构造的 JSON。
- **真实栈**：独立 PostgreSQL 16.4 库（`127.0.0.1:55432`，`tests/support/stack.py` 每栈自建库）+
  真实编译 `cmd/api` + 真实 internal mTLS + 真实对象目录 + 编译的 `cmd/supervisor`/`cmd/maintenance`。
  所有 API/SSE 断言都走 loopback socket，不 import handler。
- 真实模型路径 `not_run`：本票不调用模型供应商；只使用 `dev-stub`。

## 1. 逐 AC 对照

| # | 票面 AC | 结论 | 证据（真实执行） |
|---|---|---|---|
| 1 | 取消中不显示已撤销外部写 | **passed** | 真实 Run：claim 后 owner `POST /runs/{id}/cancel` → `202 state=cancelling phase=executing revision=3`；真实快照 + 真实 SSE 帧 `{"state":"cancelling"}` 交给已提交的 `runstream.js`/`runs.js`/`chat.js`：文案为“正在停止（cancelling）…不会撤回已经发生的外部写入”，`json.dumps(page)` 中**不存在** `已撤销` 子串；`cancelled` 终态是“已停止…不表示撤回”，不是“已撤销”。见 §5.2 / `test_t096_stack.py::test_cancelling_copy_never_claims_the_external_write_was_rolled_back` |
| 2 | finalizing 与终态不同 | **passed** | 用 supervisor 两步接口构造真实窗口：`POST /internal/v1/runs/{id}/result`（结果提议已提交）后、`POST /internal/v1/attempts/{id}/exited`（退出证据未到）前，`GET /runs/{id}` 为 `state=running phase=finalizing revision=2 output_artifact_id=null`；同刻 SSE 帧只有 `{"state":"running"}`、**没有 `phase` 键**；页面 `truth.terminal=false / is_success=false / is_failure=false`，文案“正在收尾（finalizing）…等待退出证据…不是成功，也不是失败”；退出证据到后变为 `failed/phase=idle` 且页面才 `is_failure=true`。见 §5.1 |
| 3 | 父子运行 / 文件 ready 状态可解释 | **passed** | 真实父 Run `waiting_children/phase=waiting` + 真实子 Run `queued`（owner-only `/runs/{parent}/children`，`next_cursor:null`）：页面输出“等待子任务（已完成 0/1，1 个子运行尚未终态）…父 Run 在子结果未回前不会显示成功”，`parentMayShowSuccess=false`；真实 `cmd/supervisor exec --once` 执行子 Run 为 `succeeded` 后父 Run 自动回到 `queued`，子列表刷新为 `1/1`，父仍 `parentMayShowSuccess=false`（父未终态）。文件：真实 ready artifact 在 Run 未终态时被标记“未完成运行产物”（`finalOutput=false`），`succeeded` 终态写入 `output_artifact_id` 后才标记“最终产物”；`uploading/scanning/ready/quarantined/deleted` 都有独立文案。见 §6 |
| 4 | 端到端真实入口演示；失败、授权、重试/恢复边界有断言 | **passed** | 入口是正式 Web/REST/SSE：`GET /runs/{id}/events`（session cookie）、`GET /runs/{id}`、`GET /runs/{id}/children`、`GET /runs/{id}/artifacts`、`POST /runs/{id}/cancel`，以及 supervisor mTLS 的 result/exit。失败：410 快照修正失败分支、401/404 不重试、5xx 才重试都有断言；授权：children/artifacts/events 走 owner-only（本票复用 T093/T032 的 404 规则，未放宽）；恢复：recovery_pending/needs_reconciliation 在映射表中不是失败，`cancelling` 二次取消幂等。见 §4、§5、§7 |
| 5 | 记录版本/输入 hash/命令/原始结果/profile；stub/real 分报；缺条件 blocked，未运行 not_run | **passed（含明确的 not_run）** | 版本与命令见 §2/§8；原始 HTTP body 见 §5/§6（`T096-EVIDENCE` JSON）；`dev-stub` 与真实模型路径分报（后者 `not_run`）；无浏览器 DOM/无物理断网/无 `artifact.ready` emitter 均如实记录在 §9/§10，不冒充通过 |

## 2. 环境与版本

| 项 | 实际值 |
|---|---|
| Go | `go version go1.27.3 windows/amd64` |
| Python | `Python 3.12.2`（pytest 9） |
| Node | `v24.13.0` |
| PostgreSQL | 16.4，隔离集群 `127.0.0.1:55432`（`tools/pg_dev.py status` → `running: true`） |
| profile | `ORCHARA_PROFILE=dev-stub`（无厂商模型/凭据/GPU） |
| 栈 shape | 真实 `cmd/api` + 真实 internal mTLS（supervisor/worker/maintenance 证书）+ 真实对象目录 + `cmd/supervisor`/`cmd/maintenance` 子进程 |
| 输入 | 测试生成的真实 debug Run（golden draft，`{"text":"hello"}`）；子 Run 为真实 published 版本 `start→end`；artifact 为真实上传/最终化的 `{"result":"ok"}`（15 B，sha256 由服务端记录） |
| 事件保留窗口（410 分支） | 第二个 `cmd/api` 进程 over 同一数据库，`ORCHARA_RUN_EVENT_RETENTION=50ms`、poll 20ms、heartbeat 50ms |

## 3. `state` / `phase` → UI 文案 → 可操作项 映射表

实现位置：`apps/web/src/assets/runstream.js`（`RUNSTREAM_STATE_LABELS` / `RUNSTREAM_PHASE_LABELS` /
`runStreamTruth`）。`state` 10 值与 `phase` 4 值均**从只读契约**
（`contracts/openapi.json` 的 `Run.state`/`Run.phase` 枚举）读取并由 Node 用例逐值断言非空，见
`test_t096_pages.py::test_every_contract_state_and_phase_has_an_explicit_ui_label`。

| `state` | `phase` | UI 文案（label） | tone/终态 | 可操作项（actions） | 关键语义 |
|---|---|---|---|---|---|
| `queued` | `idle` | 尚未被领取（queued） | pending / 否 | 取消 Run；刷新快照 | 取消立即生效，不创建恢复任务 |
| `running` | `executing` | 执行中（running） | pending / 否 | 查看事件时间线；取消 Run | 不伪造进度 |
| `running` | **`finalizing`** | **正在收尾（finalizing）** | pending / 否；**isSuccess=false, isFailure=false** | 刷新快照（phase 只在 Run 快照里，SSE 的 `run.status` 没有 phase）；查看事件时间线 | 结果只是提议，等待 supervisor 退出证据；**不是成功也不是失败** |
| `waiting_approval` | `waiting` | 等待审批（waiting_approval） | pending / 否 | 前往审批队列；刷新快照 | 决定+退出证据前不创建恢复任务 |
| `waiting_children` | `waiting` | 等待子任务（waiting_children） | pending / 否 | 查看子运行列表（N/M）；刷新快照 | 子结果未回前父不显示成功 |
| `cancelling` | 保留原 phase（如 `executing`） | **正在停止（cancelling）** | pending / 否 | 刷新快照（state/phase）；再次请求取消（幂等） | **不得出现“已撤销”**；`externalWritesNote`=“不会撤回/撤销已经发生的外部写入” |
| `recovery_pending` | `idle` | 等待恢复（recovery_pending） | pending / 否 | 查看恢复原因与证据；刷新快照 | 不自动换 token、不自动重排；不显示为失败 |
| `needs_reconciliation` | `idle` | 等待人工核对（needs_reconciliation） | pending / 否 | 核对未知外部写并提交证据；刷新快照 | 不显示为成功或失败，不声称外部写已撤销 |
| `succeeded` | `idle` | 已成功（succeeded） | ok / **是** | 查看最终产物（`output_artifact_id`）；查看受控调用账本 | 唯一 `isSuccess=true` |
| `failed` | `idle` | 已失败（failed） | bad / **是** | 查看失败节点与 `error_code`；查看受控调用账本 | 唯一 `isFailure=true`；终态不复活 |
| `cancelled` | `idle` | 已停止（cancelled） | — / **是** | 查看最终快照 | “已停止 ≠ 已撤回”；不表示撤回已发生的外部写 |
| 未知值 | 任意 | 未知状态（`<value>`） | — / 否 | 刷新快照 | 服务端新值不猜测 |

Phase 标签：`idle` 空闲（idle）、`executing` 执行中（executing）、`waiting` 等待中（waiting）、
`finalizing` 收尾中（finalizing，等待退出证据）。

补充（`/chat` 页同一映射，`runStreamHandlingText`）：`dispatched` 之后不再停止轮询；Run 非终态时每
800ms 继续读 `GET /runs/{id}`（上限 100 次），终态才停。`finalizing` 显示“正在收尾…尚未成功”；
`cancelling` 显示“正在停止…不会撤回”；`waiting_children` 显示“等待子任务”；`needs_reconciliation`
显示“等待人工核对…不给出成功结论”。

### 3.1 文件 readiness → UI 文案

`apps/web/src/assets/attachments.js::artifactStateNote` + `runstream.js::runStreamArtifactReadout`。
DB CHECK 枚举来自只读 `contracts/database.sql:51`（`uploading/scanning/ready/quarantined/deleted`）。

| 文件状态 / 运行状态 | UI 文案 | 可作任务输入？ | 可称最终产物？ |
|---|---|---|---|
| `uploading` | 上传暂存中：字节尚未完成 | 否 | 否 |
| `scanning` | 后台正在扫描真实字节：ready 之前不能作为任务输入 | 否 | 否 |
| `ready` + Run 非终态 | **未完成运行产物**：即使已 ready 也不是最终成功产物 | 是（ready 才可） | **否** |
| `ready` + Run `succeeded` + `id==output_artifact_id` | **最终产物**：由 succeeded 终态提交 | 是 | **是** |
| `ready` + Run 终态但非 `output_artifact_id` | 节点/中间产物（未声明为最终产物） | 是 | 否 |
| `quarantined` | 已被隔离：不可下载，也不可作为任务输入 | 否 | 否 |
| `deleted` | 已删除：立即不可下载，也不能作为任务输入 | 否 | 否 |

## 4. 断线与 410：页面真的拉快照并重建

### 4.1 服务端事实（先在报告里写清：SSE 基建是完整的）

`product/internal/api/events.go:136-143` 已实现 `410 event_cursor_expired` +
`details.snapshot_url=/api/v1/runs/<id>`（契约 `Error.details` 可扩展）；`Last-Event-ID` 严格解析在
`events.go:149-174`；seq > after 回放在 `events.go:649-668`；心跳是注释帧不占 seq。T093 报告把该分支
记 `not_run` 并明确交给本票；本票用低保留窗口栈把它跑出来（服务端曾由 T032 覆盖，本票补**页面行为 +
真实 HTTP body 到真实页面字节**的端到端证据）。

### 4.2 真实 410 原始结果（`T096-EVIDENCE 410`，取自真实 HTTP，去掉可变 uuid）

```json
{"body": {"error": {"code": "event_cursor_expired",
  "details": {"snapshot_url": "/api/v1/runs/<run-id>"},
  "message": "the replay cursor is older than the run-event retention window; fetch the current run snapshot",
  "request_id": "<request-id>"}},
 "retention": "50ms", "status": 410, "snapshot_url_status": 200,
 "snapshot": {"state": "queued", "phase": "idle", "revision": 1, "output_artifact_id": null}}
```

原始命令（栈内第二进程）：`api_server(dsn, extra_env={"ORCHARA_RUN_EVENT_RETENTION": "50ms", ...})` →
`time.sleep(0.2)` → `GET /api/v1/runs/{id}/events` with `Last-Event-ID: 0`。

### 4.3 页面行为（真实 body + 已提交字节）

`test_t096_stack.py::test_real_410_drives_the_page_snapshot_rebuild` 把真实 410 body 与真实
`GET /api/v1/runs/{id}` body 注入 `runstream.js`：

- `runStreamFailure(410, body)` → `kind="cursor_expired"`、`retry=false`、
  `snapshotPath == "/api/v1/runs/<id>"`；
- `runStreamCorrectFromSnapshot` **真的调用** `fetchSnapshot(snapshotPath)`（断言
  `fetched_path == /api/v1/runs/<id>`），返回 `applied=true`、`historyLost=true`、`resumeCursor=""`，
  `run.id` 为真实 Run id；
- `/runs` 页 DOM 行为（`test_t096_pages.py::test_runs_page_fetches_the_snapshot_and_rebuilds_on_410`）：
  事件流只请求 **1 次**（不静默重连）、随后 `apiFull("/api/v1/runs/<id>")` **1 次**，
  `#detail-state=running`、`#detail-phase=finalizing`、`#detail-note` 含 finalizing、
  `#detail-timeline-status` 含“410/快照”、时间线出现“保留窗口…未回放旧帧”，
  `dataset.snapshotRebuilt="true"`。
- 不安全 URL 防御：`https://evil.example/...` 与 `/api/v1/runs/../admin` 都不会被跟随，回退到
  `/api/v1/runs/<自己的 run id>`。

## 5. finalizing 与 cancelling 的真实窗口

### 5.1 finalizing（结果提议已提交、退出证据未到）

真实调用序列（supervisor/worker mTLS）：

```
POST /internal/v1/runs/{id}/result   {attempt_token, event_id, suggested_state:"failed", error_code:"tool_crashed"} → 200
GET  /api/v1/runs/{id}               → state=running, phase=finalizing, revision=2, output_artifact_id=null
GET  /api/v1/runs/{id}/events (Last-Event-ID: 2) → id=3, event=run.status, data.payload={"state":"running"}  (无 phase)
POST /internal/v1/attempts/{token}/exited (证据齐全) → GET /runs/{id} → state=failed, phase=idle
```

`T096-EVIDENCE finalizing`（原文，省略长 uuid）：

```json
{"snapshot": {"state": "running", "phase": "finalizing", "revision": 2,
              "output_artifact_id": null, "error_code": null},
 "sse_frame": {"id": "3", "event": "run.status", "payload": {"state": "running"}, "has_phase": false},
 "page": {"truth_label": "正在收尾（finalizing）", "terminal": false,
          "is_success": false, "is_failure": false,
          "lifecycle_current": "finalizing",
          "runs_state_note": "结果提议已保存，正在 finalizing：仍在等待 supervisor 的退出证据（进程/沙箱/数据库排空）；终态提交前这不是成功。",
          "chat_handling": "v3 正在收尾（finalizing）：结果提议已保存，等待 supervisor 退出证据，尚未成功"}}
```

结论：`finalizing` 只存在于 `Run.phase`；`run.status` 事件不含 phase，页面按“事件→重取
`GET /runs/{id}` 读 phase”处理；`finalizing` 不显示为 succeeded/failed。

### 5.2 cancelling（取消意图已写、执行单元未停）

真实调用序列：claim → owner `POST /api/v1/runs/{id}/cancel` → `202`；快照
`state=cancelling phase=executing revision=3`；SSE `Last-Event-ID:2` 的下一帧 `id=3`
`payload={"state":"cancelling"}`（无 phase）；`confirm_exit` 后 `state=cancelled phase=idle`。

`T096-EVIDENCE cancelling`（原文）：

```json
{"cancel_status": 202,
 "snapshot": {"state": "cancelling", "phase": "executing", "revision": 3, "output_artifact_id": null},
 "sse_frame": {"id": "3", "payload": {"state": "cancelling"}, "has_phase": false},
 "page": {"truth_label": "正在停止（cancelling）", "terminal": false,
          "external_writes_note": "不会撤回/撤销已经发生的外部写入；只有 committed cancelled 才证明执行单元停止。",
          "runs_state_note": "正在停止（cancelling）：取消意图已记录，平台已拒绝新的结果/登记/上传/审批调用，等待执行单元退出后由 finalizer 提交 cancelled（或存在未知外部写时转 needs_reconciliation）。",
          "runs_cancel_panel": "正在停止（cancelling）：取消意图已记录；等待受控执行单元退出，终态提交前本页不会显示成功。",
          "cancel_accepted": "已接受取消意图（cancelling）：正在停止，等待受控执行单元退出；不会撤回已经发生的外部写入。",
          "chat_handling": "v3 正在停止（cancelling）：不会撤回已经发生的外部写入，终态以服务端提交为准"}}
```

断言：页面 JSON 中**没有** `已撤销`；`cancelling` 与 `cancelled` 都有“不会撤回/不表示撤回”的显式说明；
`needs_reconciliation` 的取消回执也不声称已撤销。

## 6. 父子等待与文件 ready

### 6.1 父 `waiting_children`

真实构造（F07 既有 helper，真实 API/mTLS）：创建真实 published 子版本 → 父应用（`call_app`）→
debug 父 Run → claim → `POST /internal/v1/runs/{parent}/children`（真实子 Run）→ 父提交
`waiting_children` 提议并 confirm exit。

`T096-EVIDENCE children-waiting`：

```json
{"parent": {"state": "waiting_children", "phase": "waiting", "output_artifact_id": null},
 "children": {"next_cursor": null,
   "items": [{"state": "queued", "phase": "idle", "output_artifact_id": null, "kind": "published"}],
   "generated_at": "2026-11-06T11:50:03Z"},
 "summary": {"total": 1, "completed": 0, "waiting": 1,
   "line": "等待子任务（已完成 0/1，1 个子运行尚未终态）：父 Run 在子结果未回前不会显示成功。",
   "parentMayShowSuccess": false, "parentLine": "父 Run 处于 waiting_children（phase=waiting）。"}}
```

真实执行子 Run（`cmd/supervisor exec --once`，`start→end`）后：

```json
{"child_exec_report": {"status": "executed", "outcome": "succeeded"},
 "parent": {"state": "queued", "phase": "idle"},
 "children": {"items": [{"state": "succeeded", "output_artifact_id": "<artifact>"}]},
 "summary": {"total": 1, "completed": 1, "waiting": 0,
   "line": "子任务已全部进入终态（1/1，失败/取消 0 个）。",
   "parentMayShowSuccess": false}}
```

页面规则：`parentMayShowSuccess = 父 state==succeeded && 所有子终态 && 无 failed/cancelled 子`；
父重新排队不是成功，仍显示等待自身终态。`/runs` 详情在 `waiting_children/waiting_approval` 轮询时
**每次刷新子运行面板**（`pollRunDetail` 新增），不是只加载一次。

### 6.2 文件 ready / 部分产物

真实 worker 上传 → finalize → 注册 `relation=output`，Run 仍 `running/executing` 时：

```json
{"run": {"state": "running", "phase": "executing", "output_artifact_id": null},
 "artifacts": {"items": [{"state": "ready", "media_type": "application/json", "size_bytes": 15}]},
 "readout": {"ready": true, "finalOutput": false, "unfinishedRunArtifact": true,
   "usableAsInput": true,
   "label": "未完成运行产物（运行 state=running 未终态）：即使产物已 ready 也不是最终成功产物"}}
```

提交 `succeeded` 结果 + 退出证据后：

```json
{"run": {"state": "succeeded", "phase": "idle", "output_artifact_id": "<artifact>"},
 "readout": {"ready": true, "finalOutput": true, "unfinishedRunArtifact": false,
   "label": "最终产物（output_artifact_id）：由 succeeded 终态提交"}}
```

`artifacts.js` 的 `uploading/scanning/ready/quarantined/deleted` 文案与 `deleted` 终止轮询也纳入
Node 用例（`test_file_readiness_vocabulary_is_explainable`）。

## 7. 授权与失败/重试边界

- 事件流 / Run / 子运行 / 产物都是 **owner-only 404**（`events.go:106-116`、`children.go:99-117`、
  `runartifacts.go:170-204`、`debug.go:193-210`）；本票没有放宽，测试全部使用 owner session。
- 事件流 401 停止并提示重新登录，404 停止（Run 不存在或不属于你），5xx 才带同一
  `Last-Event-ID` 重试；410 **不重试**，改走快照。
- 快照重建失败（网络/错误）→ 页面状态显示“410 快照读取失败 … 停止时间线，不静默续传”，
  不显示伪造历史。
- 取消：`queued` 立即 `cancelled`（不创建恢复任务）；有活动执行单元的 Run 进入 `cancelling`，
  重复取消幂等；`needs_reconciliation` 回执明确“等待人工核对，不会声称外部写已撤销”。
- `recovery_pending` 不自动换 token/不自动重排；页面只显示恢复原因与证据入口。

## 8. 门禁命令与原始 tail

以下命令都在 `E:\Orchara-wt\t096\product` 下执行；真实栈执行期间持有
`E:\Orchara\.scratch\f12\locks\realstack`，结束删除。

### 8.1 本票新套件

```powershell
python -m pytest tests/f12/test_t096_pages.py tests/f12/test_t096_stack.py -q
# 17 passed in 57.04s

python -m pytest tests/f12/test_t096_stack.py -q      # 真实栈单独复跑
# 7 passed in 70.13s (0:01:10)

python -m pytest tests/f12/test_t096_pages.py -q
# 10 passed in 1.35s
```

### 8.2 全量 F12（T093 的 242 不能被破坏）

```powershell
python -m pytest tests/f12 -q
# 258 passed in 103.07s (0:01:43)          # 加入静态路由用例前
# 259 passed in 111.41s (0:01:51)          # 合并 integration/f12 后重跑（已含 T096 全部 17 条）
```

### 8.3 Go / 静态

```powershell
go build ./...
# exit 0（无输出）
go vet ./...
# exit 0（无输出）
gofmt -l internal cmd tools
# 空输出
go test ./...
# exit 0；尾部：
# ok  	orchara/platform/internal/tools	4.812s
# ok  	orchara/platform/internal/toolversions	2.760s
# ok  	orchara/platform/internal/uuid	1.235s
# ok  	orchara/platform/internal/workflow	2.235s
# ...
python tools/sync_spec.py --check
# no drift: spec copy and contract snapshot match the manifest
node --check apps/web/src/assets/runstream.js   # 以及 runs/chat/runview/debug/attachments
# 全部 exit 0
```

### 8.4 受影响既有套件（真实栈串行）

见 §11（`tests/f03` 的 2 条为 T093 已记录的既有红，同名同因；`tests/f07`、`tests/f08/test_postreview_pages.py` 全绿）。

### 8.5 合并与合并后重跑

```powershell
git -C E:\Orchara-wt\t096 merge integration/f12
# Already up to date.   （集成分支仍为 497f4e8，无新提交需要合并）
```

合并后重跑：`pytest tests/f12` → **259 passed in 111.41s**；`go build ./...`/`go vet ./...`/
`go test ./...` 全 exit 0；`gofmt -l internal cmd tools` 空；`sync_spec --check` → no drift。
本票 17 条新用例在同一命令内全绿。

## 9. 修了（本票范围，红→绿）

| # | 问题（已核实缺口） | 修复 | 红→绿证据 |
|---|---|---|---|
| 1 | `/runs` 收到 410 只显示 `snapshot_url`，不抓取（`runs.js:1351-1360`） | `beginRunTimeline` 接收 `streamRunTimeline` 返回的 `cursor_expired`，`rebuildRunTimelineFromSnapshot` **真实 GET 快照**并 `showRunDetail` 重建、标记时间线、丢弃旧游标 | `test_runs_page_fetches_the_snapshot_and_rebuilds_on_410`（红：页面测试先写，未实现时 fetch 记录为空）；真实 410 用例 §4.3 |
| 2 | `finalizing` 只在 `phase`，chat 页不读 phase/SSE、dispatched 后停止轮询（`chat.js:134-143,341-358,953-973`） | chat 改用 `shouldPollHandling`（dispatched 后继续观察 Run 到终态）+ `runStreamHandlingText`（finalizing/cancelling/waiting/needs_reconciliation） | `test_chat_page_keeps_observing_the_run_after_dispatched`（红：`shouldPollHandling` 不存在）；真实栈 finalizing 用例 |
| 3 | `cancelling`/`needs_reconciliation` 的取消回执可能被读成“已撤销” | 统一 `runStreamCancelAcceptedText`；“已接受取消意图…不会撤回已经发生的外部写入”；needs_reconciliation 明确“等待人工核对” | `test_cancelling_copy_never_claims_external_writes_were_rolled_back`；真实栈 cancelling 用例（页面 JSON 无“已撤销”） |
| 4 | 子运行面板只加载一次（`runs.js:2032`） | `pollRunDetail` 在 `waiting_children`/`waiting_approval` 时**每次轮询刷新子面板**；终态也刷新 | 真实栈 children 用例（0/1 → 1/1 刷新）；§6.1 |
| 5 | 产物列表只在进入/终态加载，且不辨最终产物（`runs.js:2029,2007`） | 非终态轮询也刷新产物；`runStreamArtifactReadout` 标“未完成运行产物/最终产物/中间产物”；`output_artifact_id` 由 Run 快照提供（不改 `runSummary`，避免破坏既有断言） | `test_parent_child_waiting_and_partial_artifacts_are_explainable` + 真实栈 artifact 用例 |
| 6 | `/debug` 60s 后停止轮询（`debug.js:249-250,326-346`） | 60s 快速预算后改为 **10s 慢速继续观察**，不再显示“已停止轮询” | 静态行为守卫 `test_debug_page_long_runs_are_not_declared_stopped` + 既有 `tests/f02a` debug 行为测试保持绿 |
| 7 | `state`/`phase` 没有统一映射 | 新增 `assets/runstream.js`（纯逻辑；Node 直测），`/runs`、`/debug`、`/chat` 加载它；`/runs` 的“一句话”用 truth.detail + actions | `test_every_contract_state_and_phase_has_an_explicit_ui_label` 等 10 条页面用例 |
| 8 | 文件状态只有零散文案，`deleted` 还会一直轮询 | `artifactStateNote`（uploading/scanning/ready/quarantined/deleted）+ `deleted` 终止轮询 | `test_file_readiness_vocabulary_is_explainable` |

## 10. 未修（归属其它 epic，附证据）

| 缺口 | 证据 | 归属 |
|---|---|---|
| `artifact.ready` / `usage.updated` 契约有、**无 emitter** | `product/internal/**` 只有 `run.status`/`approval.required`/`node.status` 三类写入；`E-sse-and-runstate.md` §4.2 | F03/F04B（事件词表） |
| `run.status` payload 不含 `phase`/等待种类/child 信息 | `contracts/event.schema.json:57-79`（只读契约）；页面只能额外 `GET /runs/{id}` 读 phase | 契约治理/事件 epic |
| `GET /runs/{id}/children`、`/node-results` 非契约、恒 `next_cursor:null`、无 cursor/limit | `internal/api/children.go:92-96,125-129`；OpenAPI 无该 path | T093 已记录 93 条非契约面；F07 范围 |
| artifacts 列表不返回 `relation`/`purpose`/`created_at`，只列已注册 ready 的产物 | `catalog/runartifacts.go:32-39,80-95` | F05/T033 范围 |
| 审批过期两套语义（capability lazy vs node sweeper 30s） | `E-domain-reports.md` §9 风险 9 | F06/F04B |
| Run 内容删除没有公开端点 | OpenAPI 无 `DELETE /runs/{id}`；`platform.runs.deleted_at` 只内部过滤 | 产品缺口/后续 epic |
| 真实厂商模型/凭据/GPU | 本环境只有 `dev-stub` | F14/T090 |
| `debug.html` 节点表按当前草稿渲染（非执行快照） | `E-web-ui.md` §4.3；无快照读端点 | F04B |

## 11. 受影响既有套件（真实栈，串行）

执行时持有 `E:\Orchara\.scratch\f12\locks\realstack`，结束删除。三套全部是**整套**真实栈运行，
没有 `--ignore` / `-k` / 删用例。

```powershell
python -m pytest tests/f03 -q
# ...................................F.................................... [ 35%]
# ......................................................F................. [ 70%]
# ...........................................................              [100%]
# FAILED tests/f03/test_t029_invariants.py::test_one_active_top_level_run_per_conversation
# FAILED tests/f03/test_t035_migration.py::test_waiting_still_occupies_the_top_level_session_slot
# 2 failed, 201 passed in 1362.47s (0:22:42)
```

这 2 条失败与 T093 §6.2 记录的**既有红同名同因**：
`runs_conversation_id_fkey` / `runs_request_id_fkey`（F08/T074 迁移给 `runs.conversation_id`/
`runs.request_id` 加了 FK，f03 旧夹具仍直接 INSERT 随机 id）；T093 已在 baseline `1cdff9c` 的
detached worktree 逐条复现同样报错。本票 diff 不触碰 `db/migrations/**`、`internal/execution/**`
的夹具相关代码，也不触碰这两个用例；未用任何手段掩盖。

```powershell
python -m pytest tests/f07 -q
# ....................................                                     [100%]
# 36 passed in 311.95s (0:05:11)

python -m pytest tests/f08/test_postreview_pages.py -q
# ..                                                                       [100%]
# 2 passed in 0.36s
```

`tests/f07` 覆盖子运行/等待容量（本票新增子面板刷新与 N/M 文案的接口基础），36 条全绿；
`tests/f08/test_postreview_pages.py` 覆盖 chat 页真实字节（本票改动的 `chat.js` 兼容旧行为），2 条全绿。
另有纯 Node 的 `node tests/f04b/runview.test.mjs` → `PASS 6 runview tests`、`runview_t088` 7 条、
`runview_t091` 4 条全绿（本票改了 `runview.js` 的 finalizing/取消文案）。

## 12. 边界与 not_run（诚实清单）

- **无 Playwright/真实浏览器 DOM**：页面行为 = Node 执行已提交字节 + 最小 DOM/fetch shim；
  浏览器布局、媒体查询、真实 `EventSource`（本票用 fetch+ReadableStream，与既有 `runs.js` 一致）
  均 `not_run`。
- **无物理网卡断线**：断线是关闭真实 HTTP 连接 + 显式 `Last-Event-ID` 重连；410 是真实服务器响应。
- **无真实 SSE 中途超过保留窗口的二次判定**：`events.go` 只在建流时判 410；已建流不会因 24h 到期被踢
  （`E-sse-and-runstate.md` §1.4）→ 本票不伪造该行为。
- **`artifact.ready` 无 emitter**：文件 ready 只能轮询 `GET /artifacts/{id}` / `GET /runs/{id}/artifacts`；
  页面没有假装收到 ready 事件。
- **`debug.js` 的慢速续轮询**由源码守卫测试 + 既有 `tests/f02a` 行为测试覆盖；其 closure 内部的分支
  没有单独的页面级驱动（该页 poll 是 `initDebugPage` 闭包）。
- **真实模型路径** `not_run`（无凭据/端点/GPU）。
- **非契约路由**（`/runs/{id}/children`、`/node-results`）被页面使用，但不在 106 项 OpenAPI 内；
  本报告不把它们计入“契约内可解释”。

## 13. 裁决

T096 的三条用户可见 AC（取消中不显示已撤销、finalizing 与终态不同、父子/文件 ready 可解释）在
**真实栈 + 已提交页面字节**上均有执行型证据；410 保留窗口分支（T093 的 `not_run`）已由本票用
`ORCHARA_RUN_EVENT_RETENTION=50ms` 的真实栈跑出，并证明页面会拉快照、重建且不静默续传。未修的缺口
全部属于其它 epic 或环境边界，已如实列在 §10/§12，没有写成通过。

## 14. 第二轮复核后修正（2026-11-07，F3/F4）

> 本节是**第二轮独立复核后**的补充；§1–§13 的旧结论不删除，在其代码/测试范围内仍成立。
> 逐条出处、复现、红/绿 tail 见 `reports/f12/REVIEW2-FIXES.md` §3/§4/§6。

第二轮复核对 `1cdff9c...e3aa1e1` 提了两条页面发现，修复提交 `83307b1`；本轮补红→绿测试，
并修掉 renderer 里一个会让修复在真实执行时失效的变量错误：

- **F3（Spec P2）调试页 410 后无法重建快照。**
  旧 `runs.js:2026` 在 410 分支固定调用 `showRunDetail`，而 `debug.html` 没有 `#detail-id` 等
  `/runs` 详情锚点，读到快照后抛 `TypeError`。现在 `runs.js:1994-2029` 由调用页提供
  `options.renderSnapshot`：没有 renderer 且页面无锚点时明确报错、**不读取快照**；
  `debug.js:414-428` 传入自己的 renderer（`beginRunView` 重建节点视图）。
  **追加修正**：`debug.js:419` 原写未定义的 `draftRevision`（`initDebug` 作用域只有
  `frozenRevision`），`node --check` 能过、renderer 一执行就 `ReferenceError`；已改为
  `frozenRevision`（`:422`）。没有这一改，F3 的“调试页能恢复”仍然不成立。
- **F4（Spec P2）迟到的快照覆盖新选择。**
  旧 `runs.js:2012` `await apiFull(snapshotPath)` 之后没有校验；等 A 快照时切到 B，A 返回后会把
  `displayedRun(ID)`/详情覆盖成 A 并追加一行。现在 `runs.js:2047-2051` 在 await 之后、
  任何渲染/追加/状态写入之前用 `isCancelled`（`beginRunTimeline` 的 generation）丢弃过期快照。

本轮新增测试（Node 执行 **已提交真实字节**；`debug.js` 按真实 `debug.html` id 集合搭 shim；
不是 Playwright）：

```text
tests/f12/test_t096_review2.py::test_a_410_without_detail_anchors_does_not_throw_and_uses_the_renderer PASSED
tests/f12/test_t096_review2.py::test_the_debug_page_passes_its_own_snapshot_renderer_to_the_timeline PASSED
tests/f12/test_t096_review2.py::test_a_late_410_snapshot_never_overwrites_the_newer_run_selection PASSED
5 passed in 0.79s          # 连同 T098 的 F1/F2 两条
```

红证据：`e3aa1e1` 独立 detached worktree（只拷入新测试、不改源码）三条全红，其中
410 无锚点页的状态被旧 `.catch` 吞成“事件流读取失败：Cannot set properties of null”，
迟到快照用例的 `detail_id` 变成 A；`83307b1` 的 `debug.js` 单独复现
`ReferenceError: draftRevision is not defined at debug.js:419`（tail 见
`REVIEW2-FIXES.md` §5.1）。§9 第 1 条“410 真重建”对 `/runs` 仍然成立；本轮补的是
`/debug` 的 renderer 契约与“迟到快照不得覆盖新选择”。

边界不变：仍无浏览器 DOM/真实 `EventSource`；410 由 harness 的 fetch 410 响应驱动，
真实服务器 410 的端到端页面证据仍由 `test_t096_stack.py::test_real_410_drives_the_page_snapshot_rebuild`
提供，本轮未改该用例。
