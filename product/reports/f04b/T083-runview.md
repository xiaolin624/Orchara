# F04B / T083（运行视图半）— 调试页节点状态表：条件端口、skipped、join 信封、run 失败/取消

| 项 | 内容 |
|---|---|
| 票 | T083 / GitHub #84（F04B 运行视图半）；运行时半分见 `reports/f04b/T083-condition-branching.md`，画布创作半分为 `t/t083-canvas` |
| 工作树 / 分支 | `E:\Orchara-wt\t083-runview` / `t/t083-runview` |
| 基线 | `integration/t083-t091` @ `ea56392` |
| 结束时合并 | `integration/t083-t091` @ `111e2c2`（含 T083 graph runtime + canvas），合并提交 **`a050e2f`** |
| 本半最终 HEAD | `a050e2f` |
| 状态 | **`partial`**：页面 + 纯函数 + 页面结构/DOM 测试 + 真实线性 Run 的 HTTP E2E 通过；**条件/join/skipped 的真实执行 E2E `not_run`**（原因见 §7，测试已就绪且 `skipif` 在合并后为假） |
| 契约 | **未改** `contracts/**`、`docs/spec/**`、OpenAPI。条件端口 / join 结果使用既有**非契约 node data artifact** 约定（§2） |

本半交付的是**从画布进入的调试页**（`/apps/{id}/debug`）上的“节点状态”表：用已有提交路径创建 debug Run
后，按草稿定义顺序显示每个节点的状态、attempt、错误码，条件节点显示选中端口，join 节点显示每个 source 的
status 与 value 摘要，未选分支显示 `skipped`（与 `failed` 视觉区分），运行终态显示 state/`error_code` 并可请求
取消。页面脚本是**不打包**的静态资产，测试执行**真实 JS 字节**。

## 1. 票面 AC 对照（运行视图口径）

| # | AC（brief §2 / 优先级） | 状态 | 证据 |
|---|---|---|---|
| 1 | 用当前草稿 revision 创建 debug Run（复用 `debug.js` 现有提交路径：`POST .../debug-runs` + `Idempotency-Key` + 输入 JSON/附件 id） | **passed** | 未新增提交路径；`debug.js` 提交成功后启动 run view。真实线性 Run E2E 走同一路径（`tests/f04b/test_t083_runview.py::test_the_real_linear_run_view_...`） |
| 2 | 节点状态表（节点 id/类型/状态/attempt/错误码，按定义/拓扑序），不是事件流水 | **passed** | `buildRunViewModel`（拓扑序 Kahn，定义索引破平；节点只增不减）+ `debug.html` 表格；`runview.test.mjs` 6 条 + DOM harness 断言 6 行与 `attempt`/错误码 |
| 3 | 条件节点行显示选中端口（读条件 data artifact 的 `port`/`matched_case`） | **passed（渲染层）** / 真实条件 Run `not_run` | 用 P05 `branch-approval` 真实定义 + 真实形状 artifact 的单测（`端口 no（默认分支）`、`第 1 个 case`）；DOM harness 断言页面单元格。真实条件执行 E2E §7 |
| 4 | join 行显示每个 source 的 `status` 与 value 摘要（截断、可展开） | **passed（渲染层）** / 真实 join Run `not_run` | 单测：顺序取 `required_sources`+`optional_sources`，`approve=已跳过`、`direct=成功`、值摘要 ≤120 字符、完整值在 `<details>`；`error_code` 显示在 source 行。真实执行 E2E §7 |
| 5 | 未选分支显示 `skipped`（来自 SSE），与 `failed` 视觉区分 | **passed（渲染/聚合层）** / 真实 skipped 事件 `not_run` | `aggregateNodeStates` 对重复/乱序/重放事件保持 `skipped`，`state-skipped` vs `state-failed` 类与图例；DOM harness 断言 badge 文案“已跳过”、无 `state-failed`；真实事件 E2E §7 |
| 6 | 运行终态显示 state + `error_code`；取消按钮（`POST /runs/{id}/cancel`，202 仅表意图）与 `/runs` 链接 | **passed** | `formatRunError`（稳定码 + 人话 + 已知码提示；202 永不写成 stopped）；DOM harness 断言 `POST` + CSRF 头来源 + “已接受取消意图” + `cancelling` 可再次请求 + 终态禁用按钮；`/runs` 链接在 HTML |
| 7 | 断线重连 `Last-Event-ID`；410 显示既有快照/说明，不伪造历史 | **partial** | 页面复用 `runs.js` 既有 SSE 消费器（`Last-Event-ID` 续传、seq 去重、410 snapshot URL 文案），本次仅给 `beginRunTimeline` 增加可选 `onEvent` 钩子。表格在 410/事件缺失时只用持久 node-results 推断并标注「由节点结果推断」；**未跑**真实 410 窗口过期 E2E（见 §7） |

补充（与运行时半分的数据约定对齐）：`node.status` 是契约事件；条件 `{port,matched_case}` 与 join
`{results:{source:{status,value,error_code}}}` 是 **node data artifact** 的既有非契约约定；读取链是
`GET /api/v1/runs/{id}/node-results`（非契约、owner-only）找 `kind="data"` 的 artifact → 
`GET /api/v1/artifacts/{id}/content`（契约、owner-only）。页面不解析 artifact 正文以外的敏感内容。

## 2. 数据面（非契约，显式记录）

- `node.status`（契约 SSE）：`{node_id,state,attempt_no}`，state ∈ pending/running/succeeded/failed/skipped
  （`waiting` 为 T087 预留，聚合器已识别但不产生）。
- condition 节点 data artifact：`{"port":"yes|no|…","matched_case":0|1|…|"default"}`（运行时报告同款约定）。
- join 节点 data artifact：`{"results":{"<source>":{"status":"succeeded|failed|skipped","value":{…},"error_code":"…"}}}`。
- run 失败码：`GET /api/v1/runs/{id}` 的 `error_code`；`node.status` 没有 error_code 字段（契约缺口），
  页面只在 **state=failed 的行** 上显示 run 的稳定码，不把它贴到别的节点。
- **未新增/未扩展任何 OpenAPI 形状**；新增的只有 `GET /assets/runview.js` 静态资产路由（页面脚本，非 API）。

## 3. 实现

### 3.1 新增 `product/apps/web/src/assets/runview.js`（不打包，页面直接加载）

纯函数（Node 可执行，见 §5）：
- `aggregateNodeStates(events)`：按 attempt 优先、同 attempt 按状态秩（failed > succeeded > skipped > running >
  waiting > pending）折叠；乱序/重复/Last-Event-ID 重放幂等；忽略未知 state/缺失 node_id/非 node.status；
  `error_code` 仅对 failed 状态保留，新 attempt 覆盖旧码。
- `buildRunViewModel(definition, nodeStates, nodeResults, artifactContents, run)`：
  - 行顺序 = 拓扑序（Kahn，定义索引破平），循环/缺边定义回退定义序；定义外节点**追加**且标 `（定义中不存在）`；
  - 状态来源分层：已提交事件 → 有 data 结果则 `succeeded` 且标 `inferred`/「由节点结果推断」→ 终态 Run 无事实为
    `unknown`（“无状态事件”）→ 运行中未收到事件为 `pending`（“等待”，不伪造成功）；
  - 条件行：`port`/`matched_case` → `端口 <port>（第 N 个 case / 默认分支）`；
  - join 行：按 `required_sources`+`optional_sources` 排序 source，`status`/`statusText`/`value` 截断摘要（120 字符）/
    完整值（`<details>`，上限 4000 字符）/`error_code`/单行文本；
  - 计数与摘要行；只有 owner 可见内容。
- `formatRunError(run)`：state/稳定码/severity/人话/已知码 hint；succeeded、failed(带/不带码)、cancelled、
  recovery_pending、needs_reconciliation、cancelling、queued/running/waiting_*、未知 state 全覆盖；202 永不称 stopped。
- `runViewCanCancel`/`runViewCancelLabel`/路径构造（cancel/node-results/artifact content/run/events，均 encodeURIComponent）。
- 页面接线：`beginRunView({runID,applicationID,draftRevision,run})` 每 700ms 轮询 Run 快照 + node-results，
  终态后做一次持久结果补扫即停止；只对 condition/join 节点的 data artifact 各取一次内容并缓存；
  `runViewOnEvent(events,event)` 接收时间线已去重事件（run.status 更新 state/error_code）；取消按钮
  `POST /runs/{id}/cancel`（CSRF）；所有单元格 `textContent` 渲染，无注入。

### 3.2 `debug.html`

新增 `#runview-panel` 卡片：`#runview-status`、`#runview-run-state`、`#runview-error`、`#runview-summary`、
`#run-node-table`（6 列：节点/类型/状态/attempt/分支或 join 来源/错误码）、`#run-node-rows`、`#runview-legend`
（“已跳过=未选分支，不是失败”+ 非契约数据面说明）、`#runview-cancel`、`#runview-cancel-status`、`#runview-runs-link`；
CSS 区分 `state-skipped`（灰、⏭、降透明）与 `state-failed`（红、✕、加粗）；脚本顺序
`app.js → runview.js → stream-preview.js → runs.js → debug.js`；保留 F02A 提交面与 `run-panel` 原样。

### 3.3 `debug.js` / `runs.js`（各一处增量钩子）

- `debug.js`：提交成功后 `beginRunView({runID, applicationID, draftRevision, run})`；把时间线 `onEvent` 转给
  `runViewOnEvent`（页面仍只有**一个** SSE 消费器）。
- `runs.js`：`beginRunTimeline` 支持可选 `elements.onEvent(events,event)`；未传时行为完全不变（既有页面零影响）。

### 3.4 `internal/api/server.go`

新增 `GET /assets/runview.js` 静态路由（与 `/assets/debug.js` 同款 `staticPage`），无 API 形状变化。

## 4. 测试

- `product/tests/f04b/runview.test.mjs`（Node ESM，**执行 runview.js 真实字节**，`vm` 上下文加载；fixture 为真实
  `docs/spec/examples/{branch-approval,parallel}/workflow.json`）：6 条用例覆盖乱序/重复/重放、latest attempt、
  skipped 独立、定义乱序拓扑排序、定义外节点追加、unknown/inferred/pending 分层、条件端口（default 与第 1 个 case）、
  join 信封顺序/摘要/错误码、run 错误文案、取消可用性与路径编码。
- `product/tests/f04b/test_t083_runview.py`：
  - Node 包装器（node 缺失 skip，沿用 f04a 惯例，断言 `PASS 6 runview tests`）；
  - `node --check` runview.js/debug.js 与 debug.html 内联脚本；
  - 页面结构断言（表格/端口/join 列、图例、取消、脚本顺序、无 localStorage/sessionStorage/location.search）；
  - 真实 HTTP 静态资产（编译后的 `cmd/api` 返回 `/apps/{id}/debug` 与 `/assets/runview.js`）；
  - **vm + 最小 DOM shim** 驱动真实 `beginRunView`/`runViewOnEvent`/取消处理器：断言表格行、端口、join、skipped 视觉、
    取消 POST/CSRF/202 文案、终态停止轮询、410 式回退的「推断/无状态事件」并显示 `invalid_activation` + 提示；
  - **真实线性 Run 的 HTTP E2E**（已执行）：真实 PG + 编译 API + mTLS + supervisor + WSL2/runsc 的
    `start → python(stats) → end`；断言 owner HTTP 读到的 run/node-results/data artifact 经真实 runview.js 渲染为
    3 行 succeeded、`stats.artifactId` 等于真实 data artifact、`formatRunError` 为 ok；
  - **真实条件分支 E2E**（已写好，当前 `not_run`，见 §7）：复用 `tests/support/t083.py` 的
    `branch_definition()`/`queue_graph_run`/`run_once`/`node_data_document`/SSE 回放，断言 `port=yes/matched_case=0`、
    `right skipped`、join 信封、`left=成功`/`right=已跳过`、join `succeeded` 恰好一次。

## 5. 门禁（真实命令与原始输出尾部）

环境：Windows；Go 1.27.3（`E:\orchara-toolchains\go`）；Node v24.13.0；Python 3.12.2；PostgreSQL 隔离集群
`127.0.0.1:55432`（`tools/pg_dev.py`，本机 `.env.local`，不提交）；WSL2 + runsc（线性 E2E 使用）。

```text
$ node --check apps/web/src/assets/debug.js
exit=0
$ node --check apps/web/src/assets/runview.js
exit=0
$ node --check apps/web/src/assets/runs.js
exit=0
$ node tests/f04b/runview.test.mjs
ok 1 - aggregateNodeStates takes the latest attempt and never regresses a terminal state
ok 2 - aggregateNodeStates keeps skipped distinct and ignores malformed frames
ok 3 - buildRunViewModel renders one row per definition node in topological order
ok 4 - buildRunViewModel carries run error codes and honest unknown states
ok 5 - buildRunViewModel reads the selected port and the join envelope from data artifacts
ok 6 - formatRunError names the stable code and never turns intent into success
PASS 6 runview tests
exit=0
$ python -m pytest tests/f04b -q          # 合并前（运行时未合入时）
......s                                                                  [100%]
6 passed, 1 skipped in 46.34s
exit=0
```

`python -m pytest tests/f04b -q` 的 6 passed 中含**真实线性 Run 的 HTTP E2E**；`s` = 条件分支 E2E（当时
`internal/presetexec/graphplan.go` 尚不在集成分支，`skipif` 原因写明）。

```text
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
exit=0
$ go build ./...
exit=0
$ go test ./internal/api
ok  	orchara/platform/internal/api	(cached)
exit=0
$ gofmt -l internal cmd tools
（空）
exit=0
```

合并 `integration/t083-t091`（运行时分 + 画布分，tip `111e2c2`）后复跑上述 fast/Go 门禁：全部同上（`graphplan.go`
已存在，条件 E2E 的 `skipif` 变为假）。合并后**未**再启动 pytest —— 收到编排者“另一个 agent 的 `pytest tests/f08`
正在使用共享 WSL/runsc 栈，请不要并发启动 pytest”的指示；条件 E2E 因此在本次交付中记为 `not_run`（§7），
可以随时在空闲栈上直接执行（无 skip）。

其他回归（本次改动前/后相关页面套件，均在无并发时单跑）：

```text
$ python -m pytest tests/f06/test_t044_pages.py -q
4 passed in 22.27s
$ python -m pytest tests/f03/test_t029_pages.py tests/f03/test_t031_pages.py tests/f03/test_t032_pages.py -q
14 passed in 114.30s
$ python -m pytest tests/f02a -q          # 见 §7：受并发 pytest 争用影响，未取得可信的全绿结果
```

## 6. 环境与合并

- 分支 / HEAD：`t/t083-runview` @ `a050e2f`（合并提交；父提交 `30be9ff` 为运行视图半最终功能提交）。
- 合并：`git merge integration/t083-t091` 唯一冲突 `product/tests/f04b/conftest.py`（add/add，双方各自新增）；
  采用集成分支版本（其中包含 worker 路径与 f04b 说明），保留本半的 `runview.test.mjs` / `test_t083_runview.py`；
  合并后树干净。
- 无迁移、无 `contracts/**`/`docs/spec/**` 改动、`sync_spec --check` no drift。

## 7. 边界 / not_run / 风险

**not_run（明确）**

1. **真实条件/join/skipped 执行 E2E**：测试 `test_the_real_run_view_selects_the_port_and_skips_the_unselected_branch`
   已写好并在运行时合入后具备执行条件，但按编排者要求（`pytest tests/f08` 正在占用共享 WSL/runsc 栈，禁止并发
   pytest）本次未运行。运行命令（空闲栈上）：
   `python -m pytest tests/f04b/test_t083_runview.py -q`；预期无 skip。作为替代证据：同一渲染路径已用真实 P05
   定义 + 真实 artifact 形状（单测）与实时 DOM harness 覆盖；运行时半的同类真实执行证据在
   `reports/f04b/T083-condition-branching.md`（真实 runsc）。
2. **真实 410 保留窗口过期 E2E**：需要短 retention 栈并真实过期事件；页面 SSE 410 处理复用既有 T032 客户端
   （未改语义），表格回退逻辑用 410 场景的等价数据在单测/DOM harness 中断言“由节点结果推断/无状态事件”，
   未跑真实过期。
3. **真实取消 E2E**：取消按钮的请求形状/202 意图语义/DOM 状态已测；真实 `POST /runs/{id}/cancel` 的终态提交
   由 T036 既有真实测试覆盖，本半未重复。
4. **浏览器 DOM（Playwright）**：P2 可选、非门禁，未驱动；页面真实字节由 Node + 最小 DOM shim 执行
   （仓库既有页面测试惯例）。
5. **真实失败节点 E2E**：`state=failed` 行显示 run `error_code` 的单测/页面测试已覆盖；真实失败 run 经 run-view
   未跑（`status` 事件不含 error_code 的契约缺口见风险 1）。

**风险 / 设计取舍**

- `node.status` 无 `error_code`：失败节点的稳定码只能取自 run 快照，属**归因启发式**（只贴到 failed 行）；
  若将来契约给 node.status 加 error_code，聚合器已优先使用 payload 字段。
- 节点表按**当前草稿定义**排序与显示（页面没有执行快照读取端点）；提交后若草稿被改，定义外的真实节点会被追加并
  标注，定义内但未执行的节点显示“无状态事件”，不会伪造成功。
- 运行中每 700ms 一次 `GET /runs/{id}` + `GET /runs/{id}/node-results`，终态后停止；图很大时是 O(节点数) 的
  artifact 读取（每个 condition/join artifact 只取一次），未做批量端点（那会扩契约，超出本半范围）。
- 若运行时将来改变 data artifact 约定（`port`/`results` 形状），页面只会**不显示**端口/join 列，不会显示错误数据。
- 依赖既有 SSE 客户端：410 时表格依赖持久 node-results 推断；若 node-results 也不可用，行显示“无状态事件”，
  不会回退到事件流猜测。
