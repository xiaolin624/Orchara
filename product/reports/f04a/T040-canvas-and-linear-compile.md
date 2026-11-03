# T040 / issue #41 — F04A 线性四节点编译器 + Vue Flow 画布

- 票：T040（GitHub issue #41，父 F04A；规划 ID F04A.01 + F04A.02）
- 分支：`t/t040-canvas`，worktree `E:\Orchara-wt\t040`；基线 `ae53679`（T039 合并后）
- 集成分支：`feat/f06-f04a-models-and-workflow`（本 worktree 执行 `git merge` 时为
  already up to date，未编辑主 checkout）
- 状态：**passed**（范围 = Go 权威校验器之上的 Python 防御性线性编译器 + LangGraph
  stub-adapter 图证明 + 真实 Vue Flow 画布；生产 Run 运行时仍是 Go，真实 python/agent
  节点执行属 T042/T043）
- 证据：本文件；`docs/adr/0007-f06-f04a-model-registry-boundaries.md` 的 T040 追加
  （D4/D5）；`PROGRESS.md` 的 T040 行与章节；共享语料
  `tests/f04a/corpus/linear_f04a.json`
- 契约/规格/迁移：**零改动**（未改 `contracts/**`、`docs/spec/**`、迁移 0001–0024；
  `sync_spec --check` no drift）

## 1. stub / real 分界（诚实声明）

| 能力 | 本票实际做到 | 未做到 / 去向 |
|---|---|---|
| Python 校验 F04A 线性子集 | 真实 Python 模块逐用例执行，与 Go 权威共享语料对账 `(verdict, code, pointer)` | 十节点全量规则不复制；分支/region 属 F04B |
| LangGraph 线性图 | 真实 `langgraph.graph.StateGraph`（P01 锁定 env，langgraph 1.2.4）按声明边执行 | **不是生产 worker**；生产 Run 仍由 Go supervisor/线性执行器承担（T042/T043） |
| 节点执行 | 确定性 **stub adapter**：start 回显应用输入，python/agent 按 output_schema 产出，end 映射应用输出 | 真实 python 沙箱、真实 agent 能力代理、文件/日志/预算属 T042/T043 |
| 画布 | 真实 Vue 3 + Vue Flow 应用，提交的 esbuild bundle；Node 直接执行纯模块 | 浏览器 DOM/拖拽交互未做自动化（not_run） |
| 后端 | 复用 `POST /validate`（`ExecutableError` 已含 `node_id`）；新增 `/assets/canvas.{js,css}` | 无新执行端点、无模型/工具真实调用 |

## 2. 交付物

### 2.1 Python 编译器（`product/worker/platform_worker/compiler/`）

- `validate.py`：只覆盖 `start → python → agent → end` 的防御性重校验。五个阶段与 Go
  同序：document/schema → identity → DAG（cycle/unreachable/cannot_reach_end/
  merge_without_join）→ F04A 线性形状 → references/required outputs。共享用例的
  reason/pointer 与 Go 完全一致；F04A 之外的拒绝使用稳定码：
  `unsupported_node_type`、`linear_order`、`not_linear_chain`、`unsupported_port`、
  `unsupported_subapp`、`bad_output_mapping`。输出 wire 形状对齐 Go `WireResult`
  （`verdict`、`input_sha256`、`problems[{code,pointer}]`，valid 时给
  `data_canonical`/`data_sha256`）。
- `jcs.py`：从 `probes/jcs/python/jcs.py` **复制进产品模块**（RFC 8785；运行时不 import
  `probes/`）。
- `graph.py`：`LinearGraph` 先校验再构 `StateGraph`；order/bindings 只来自声明边；
  `NodeContext(run_id,node_id,invocation_path,inputs,output_schema)`；
  `NodeOutcome.succeeded/failed/waiting/needs_reconciliation`；`NodeRecord`/`RunResult`；
  失败、等待、需对账都短路后续节点；每个节点返回值按 output_schema 检查，end 的输出按
  文档 `output_schema` 检查。
- `stub.py`：`StubStartAdapter`、`StubPythonAdapter`、`StubAgentAdapter`、`StubEndAdapter`，
  `stub_adapters()` 可按节点 id 或节点类型覆盖；完全确定性。
- 共享语料：22 例 = 16 shared（1 valid + 15 invalid，Go/Python 双方断言）+ 6
  f04a_only（Python 额外拒绝，Go 不执行这些断言）。

### 2.2 Vue Flow 画布（`product/apps/web/canvas/`）

- 依赖锁定：`vue 3.5.44`、`@vue-flow/core 1.48.2`、`@vue-flow/background 1.3.2`、
  `@vue-flow/controls 1.1.3`、`esbuild 0.28.2`；`package-lock.json` 提交；
  `node_modules/` 忽略。
- 纯模块（Node 直接跑）：
  - `src/lib/dsl.js`：DSL↔图、布局、链序、连线规则（自环/重复/多出/多入/端节点/环）、
    增删节点、四节点/三边检查；
  - `src/lib/sources.js`：合法变量来源 = `input#/...` + **上游**节点 output_schema 属性，
    排除自身/下游；类型与分组；
  - `src/lib/errors.js`：`{node_id,code,pointer,message}` → 节点 id + 配置字段键，
    文档级错误不硬猜节点；
  - `src/lib/export.js`：只导出 `{definition, layout}`，递归拒绝
    secret/token/credential/memory/run input 类字段。
- `src/main.js`：Vue 3 render function + Vue Flow；palette 只有四类节点；连线被拒时显示
  中文原因 + 稳定码；配置面板按类型：
  - start：应用输入 schema（name/type/required）；
  - python：`GET /api/v1/tools` 中 **ready+approved** 的工具版本，展示工具 in/out schema，
    输入映射，输出 schema；
  - agent：`GET /api/v1/models` 中 **passed** 的模型，instructions、max_turns、允许的
    ready+approved 工具版本、空 subapp 列表、输入映射、输出 schema；
  - end：应用输出 schema + 从合法上游字段映射，必填输出未映射时提示；
  - 画布是主编辑器；原 JSON 文本域折叠在 `<details id="json-view">` 中，与画布同步。
- 校验：`POST /api/v1/applications/{id}/validate` + CSRF；`valid:false` 时把每条错误映射到
  节点/字段，选中节点并聚焦对应控件，错误列表可点击跳转；成功显示
  `X-Manifest-SHA256`。
- 保存/冲突：仍由 `editor.js` 的 `PUT /draft` + `If-Match` 执行；画布变化同步到文本域，
  因此 ETag/412/428/重载/下载本地副本与调试入口保持 T023 行为；ETag/CSRF 不进 URL/存储。

### 2.3 页面与路由

- `apps/web/src/editor.html`：加载 `/assets/canvas.css`、`/assets/app.js`、
  `/assets/canvas.js`、`/assets/editor.js`；`#canvas-root` 为主编辑区；保留
  `#draft-form`/`#draft-definition`/`#draft-layout`/`#draft-save`/`#conflict-*`/
  `#debug-entry`。
- `internal/api/server.go`：新增 `GET /assets/canvas.js`、`GET /assets/canvas.css`
  （`staticPage`，`Cache-Control: no-store`；路径编译期常量）。

## 3. 构建画布 bundle

```powershell
cd product/apps/web/canvas
npm ci
npm run build
# -> apps/web/src/assets/canvas.js (IIFE, window.OrcharaCanvas)
# -> apps/web/src/assets/canvas.css
```

测试/运行**不需要 npm**：bundle 与 CSS 已提交，`node --check`、Node 纯模块测试和真实
HTTP 服务都直接使用提交产物。

## 4. AC 对照

| AC | 证据 | 结果 |
|---|---|---|
| 真实 Vue 3 + Vue Flow 应用、提交 bundle、可复现构建 | `apps/web/canvas/*`、`assets/canvas.{js,css}`、`test_canvas_assets.py`（依赖精确版本、lock、build.mjs、bundle 语法、含 mount、HTML 结构、Node 模块） | passed |
| 四类节点、拖拽/连接/删除、只允许单链并解释拒绝原因 | `dsl.js` 连线规则 + Node 8 用例；`main.js` palette/config/Delete；错误码 `self_loop/duplicate_edge/outgoing_exists/incoming_exists/end_has_no_output/start_has_no_input/cycle` | passed（DOM 交互 not_run） |
| 布局独立保存并精确 round-trip | `dsl.js` 布局函数 + Node 用例；真实 HTTP 用例保存 `{x:280,y:...}` 小数坐标、GET 后逐字节相等 | passed |
| start/python/agent/end 配置面板 + 工具/模型选择器 + 合法变量来源 | `main.js` 面板 + `sources.js`/`errors.js`；工具/模型从真实 `GET /tools`、`GET /models` 读取并按 ready+approved / passed 过滤 | passed（选择器 DOM not_run） |
| 校验错误定位到节点/字段并可跳转 | `errors.js` + `main.js`；真实 HTTP 用例断言 `bad_pointer/missing_value/bad_config` 的 `node_id`+pointer；共享语料断言 Go/Python 同 pointer | passed |
| 成功后显示 `X-Manifest-SHA256` 并保留草稿 | `main.js`；`test_valid_canvas_definition_returns_the_manifest_hash`（64 hex）、draft 未变 | passed |
| 导出只含固定 capability UUID/config，无秘密/运行输入/记忆 | `export.js` + Node 用例；真实 HTTP 用例断言草稿只有三字段、配置只有固定版本/指令/turn | passed |
| Python 与 Go 在共享线性语料一致 | `internal/workflow/f04a_corpus_test.go` + `tests/f04a/test_validate.py` 对同一 JSON 的 `verdict/code/pointer` 断言 | passed |
| LangGraph 线性执行 + 确定性 + 失败/等待短路 + 非线拒绝 | `graph.py`/`stub.py` + `test_graph.py`（真实 P01 venv；4 节点顺序、两次相同、failed 停链、waiting 归一、输出 schema 拒绝、9 类非线拒绝） | passed |
| 真实 PG + 二进制 + HTTP 集成 | `test_t040_canvas_integration.py` 7 用例：页面/资产、草稿 round-trip、manifest hash、三类定位错误、pending model + unapproved/unready tool、非 owner/creator 拒绝、导出无敏感内容 | passed |
| 门禁 | 见 §5 | passed |

## 5. 命令与原始结果（tail）

环境：Windows；Go `go1.27.3 windows/amd64`（`E:\orchara-toolchains\go\bin\go.exe`）；
Python 3.12.2；Node v24.13.0；P01 venv langgraph 1.2.4（本 worktree 未重建成功，门禁通过
`ORCHARA_P01_PYTHON` 指向主 checkout 的 P01 venv——见 §7）。

```text
$ go vet ./...
VET_EXIT=0

$ go test ./...
（23 个 ok 包、9 个 no test files、0 FAIL）
TEST_EXIT=0

$ gofmt -l .
gofmt: empty

$ python -m pytest tests/f04a tests/f02a -q
140 passed in 557.80s (0:09:17)
# f04a 36 = validate 17 + graph 7 + assets 5 + integration 7；f02a 104（T039 基线）

$ python -m pytest tests/tooling -q
21 passed in 5.23s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
SYNC_EXIT=0

$ node tests/f04a/canvas_modules.test.mjs
PASS 8 canvas module tests

$ node --check apps/web/src/assets/canvas.js
exit=0
```

Node 纯模块 8 项：DSL↔图/布局 round-trip、四节点线性、合法来源、连线拒绝、分支检测、
增删节点、错误映射、导出清洗。

## 6. T042/T043 可依赖的接口

Python：

- `platform_worker.compiler.validate.validate(document) -> ValidationResult`；`Problem.code/
  pointer`；`result.as_wire()` 与 Go `workflow.WireResult` 同形。防御性拒绝列表见 §2.1。
- `platform_worker.compiler.graph`：
  - `build_linear_graph(definition) -> LinearGraph`（先校验，非线抛
    `LinearGraphError(code,pointer)`）；
  - `LinearGraph.run(run_inputs, adapters, run_id=...) -> RunResult`；
  - `NodeAdapter.invoke(node, NodeContext) -> NodeOutcome`；
  - `NodeContext(run_id, node_id, invocation_path, inputs, output_schema)`；
  - `NodeOutcome.succeeded(output)` / `.failed(code,message)` / `.waiting(ref)` /
    `.needs_reconciliation(operation)`；
  - `RunResult(status, outputs, records, failure_code, failure_node_id, message)`，
    `NodeRecord(node_id,node_type,status,inputs,output,code,message)`。
- `stub_adapters()` 的映射按节点 id 优先、再按类型；真实适配器可用同形对象替换。
- T042 的真实 python 节点只需实现 `NodeAdapter` 并在 `adapters` 中覆盖 `"python"`；
  T043 的 agent 同理覆盖 `"agent"`，两者无需改图结构/校验器。

Go/HTTP：

- `POST /api/v1/applications/{id}/validate` 响应继续是
  `{valid, errors:[{node_id?,code,pointer,message}]}`，成功头 `X-Manifest-SHA256`；
  `catalog.ExecutableError` 未改字段。
- Draft 仍是 `{definition, layout, capability_card}`；layout 独立、不参与 manifest 哈希。
- 静态资产 `GET /assets/canvas.js`、`GET /assets/canvas.css` 已由服务端提供；
  页面仍只通过既有 draft/validate 端点工作。
- 依赖就绪错误码可依赖：`dependency_not_ready`（pending/failed model、unapproved 或
  non-ready tool），pointer 指向 `config.tool_version` / `config.model_profile_version`。

## 7. 偏差与 not_run（诚实清单）

- **生产运行时边界**：D11 已记录 LangGraph 不作为本批 Run 运行时；生产 Run 仍是 Go
  supervisor/线性执行器（T042/T043）。本票的 LangGraph 执行是 stub-adapter 证明，不是生产
  worker。
- **P01 venv**：worktree 内 `probes/agent_min/.venv` 不在 git 中；本 worktree 尝试用锁文件
  重建时因环境 TLS CA bundle 路径问题失败（pip 报 `Could not find a suitable TLS CA
  certificate bundle`），随后删除了空 venv。门禁使用
  `ORCHARA_P01_PYTHON=E:\Orchara\product\probes\agent_min\.venv\Scripts\python.exe`
  （主 checkout 的 P01 锁定 env，langgraph 1.2.4）；合并后主 checkout 直接拥有该 venv。
  未伪造 skip：缺失时 `pytest.fail` 给出精确消息。
- **浏览器 DOM**：未用浏览器驱动拖拽/表单/焦点；画布行为通过 Node 纯模块 + 提交 bundle
  语法 + 真实 HTTP 页面/资产断言证明。这是与 T023 一致的 not_run。
- **未运行**：全量 `pytest tests`（按简报由 orchestrator 跑，且 `tests/f00b` 在本机可能卡
  Windows pipe）；真实模型/工具执行、真实 python 沙箱、生产 LangGraph worker 均
  not_run（属 T042/T043）。
- 未 push、未关闭 issue。

## 8. 提交

| 顺序 | commit | 内容 |
|---|---|---|
| 1 | `0b9f938` | Python 线性校验器 + LangGraph stub 图 + 共享语料 + Go/Python 对账测试 |
| 2 | `e9a7826` | 画布纯模块（dsl/sources/errors/export）+ Node 测试 + 依赖锁 |
| 3 | `b2cfd44` | Vue 画布应用、提交的 esbuild bundle、页面接线、`/assets` 路由 |
| — | `5441cac` | 修正：end 节点按文档 output_schema 校验（应用输出），非自身空 schema |
| 4 | `b75ff76` | 真实 HTTP/PG 集成测试（round-trip、定位错误、依赖门禁、ACL、导出） |
| — | `64ca3db` | 修正：节点文案通过 Vue Flow 的 `data.label` 渲染 |
| 5 | （本文件提交） | 报告 + PROGRESS + ADR 0007 追加 |

`git status --short` 在文档提交后应为空；未推送、未关闭 issue。
