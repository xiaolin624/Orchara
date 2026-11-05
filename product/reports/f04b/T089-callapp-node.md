# F04B/T089 在画布调用固定子应用并映射结果（GitHub #90）

- 状态：**`passed`**（画布 call_app 节点/面板 + graph 路径真实栈 E2E；见 §4 的 stub/real 分界与 not_run/not_applicable）
- 分支：`t/t089-callapp-node`，worktree `E:\Orchara-wt\t089`；基线 `integration/t083-t091` @ `b343f7b`
- HEAD：`aced554`（= 集成分支 tip，含协调者的 T089 merge；文档提交在本文件写入后再 +1）
- 范围：`apps/web/canvas/src/**` + 提交产物 `apps/web/src/assets/canvas.{js,css}` + `tests/f04b` 真实栈验收；**不改 Go 运行时**（T083 已把 call_app 接入 graph 路径）、不改 `contracts/**`、`docs/spec/**`、迁移
- 原始日志：`E:\Orchara\.scratch\t083-t091\T089\`（`pytest-t089.log` 首次运行暴露恢复接缝未启用、`pytest-t089-rerun2.log` T086 合并树 7+15 passed、`pytest-t089-integrated.log` 最终合并树 7 passed）

## 1. AC 对照

| 验收项（票面/§2） | 结论 | 证据 |
|---|---|---|
| 创作者选择固定子版本并在变量面板映射 I/O | **passed** | 画布 palette 新增 `call_app`（`dsl.js`：类型/标签「子应用调用」/可重复/`defaultNode={config:{application_version:''}}`/位置）；`main.js` 面板：`GET /applications?limit=100` → `GET /applications/{id}/versions?limit=100` 固定版本选择、显示 version id + manifest sha256、按子版本冻结 `input_schema` 逐字段映射（literal/input/node 三种绑定）、子版本 `output_schema` 只读、父节点 `output_schema` 可编辑（默认取子版本）；`lib/callapp.js` 纯逻辑 + Node 用例 1–12，pytest `test_t089_callapp_canvas.py` 4 passed |
| 本地提示映射缺失/类型不匹配；服务端校验仍是权威 | **passed** | `callAppProblems` 返回 `call_app.missing_required_input` / `unknown_input` / `type_mismatch` / `illegal_source` / `version` / 输出细化提示（Node 用例 11）；真实栈 `test_mapping_errors_are_located_by_the_publish_gate` 断言服务端 `io_mapping_type_mismatch` `/nodes/2/inputs/source`、`io_mapping_missing_field` `/nodes/{2,3}/inputs/region` 与 debug 422（零 Run/零 job 语义沿用 T083 用例） |
| 运行后看到独立 child 和原结构结果 | **passed** | 单 child E2E：父 `waiting_children` → child 独立 Run（`kind=published`、独立 snapshot/thread）→ 终态钩子产生唯一 resume → 父 succeeded；父节点 data artifact = 子结构化结果 `{"echo":"alpha"}`，标签 `child-run:<child id>`；`GET /runs/{id}/children` 1 条；join 封套 `{child: succeeded+value, direct: skipped}`；最终输出=join 封套 |
| 并发状态不串扰 / 相同 child 并发不串 thread | **passed** | 并行两个 call_app（同版本、不同 node id）：两个 child Run，invocation key `call_app:a`/`call_app:b`，不同 `checkpoint_thread_id`/`execution_snapshot_id`/`output_artifact_id`，结果分别 `alpha`/`beta`，join 两源独立 succeeded |
| 同 child 两次调用不去重（按 app id） | **passed** | 顺序两个 call_app 节点同版本：receipt key `call_app:first`/`call_app:second`，两个 child Run、两个 thread、结果 `first`/`second` |
| 节点等待按交接协议释放槽 | **passed** | `test_the_waiting_handover_releases_and_reoccupies_the_execution_slot`：第一次 exec 后 parent `waiting_children`、`exit_confirmed_at` 非空、resume 尚不存在、start job `held`、`global/execution_slots` allocation `released_at` 非空、counter used=0；child 执行只占用独立 child pool（global+parent 两行）；child 终态后 decision+唯一 resume job（`decision_at >= child.finished_at`），queue 期间不占 Python 槽；resume claim 重新占用 1 个 execution slot，终态释放一次；`assert_slot_consistent` 全绿 |
| 子失败传播（required） | **passed** | child 经真实 finalizer 判失败（`child_output_schema_invalid`）→ 钩子决定 `{outcome:failed, child_run_id}` → resume 后父节点 failed、run `error_code=child_output_schema_invalid`、无第二个 child、end/join skipped |
| 映射错误定位（执行器防御） | **passed（执行器非沙箱防御）** | 校验层权威：`/nodes/<i>/inputs/<name>`（上表）；执行器防御：发布校验无法看到的运行期值缺失 → `node_input_invalid`，supervisor report 定位 `child.source`，无 child Run 产生 |
| 不暴露框架内部对象为公共字段 | **passed** | 父节点 data 文档仅子结构化结果；断言不含 `thread`/`checkpoint_thread_id`/`namespace`/`messages`/`subgraph`；平台引用通过 `GET /runs/{id}/children`（Run 契约字段）与 node-results `label=child-run:<id>` 暴露 |
| 引用保留 doc/version/span | **not_applicable** | call_app 节点无文档/切片引用（不配置 KB、不产生 citation）；本票的“版本保留”由固定子版本证明：receipt `child_version_id` + child Run `application_version_id` + `manifest_sha256`，执行后不变 |
| 固定版本、不接受 latest / 动态未发布 id | **passed** | 画布选择器只列已发布版本（UUID 过滤，Node 用例 3）；服务端 spawn 拒绝非闭包/未发布版本（F07/T078 既有证据），本票未改动 |
| 端到端入口实际演示；失败/授权/重试边界有断言 | **passed（本票范围）** | 真实 PG + 编译 API + mTLS + `cmd/supervisor exec --once` + 真实对象存储；失败传播、唯一 resume、无第二 child、槽释放均有断言；授权面沿用 F07（`child_not_visible` 等既有套件），本票未改鉴权/幂等键语义 |

## 2. 实现要点

### 2.1 画布（`apps/web/canvas/src`）

- `lib/dsl.js`：`NODE_TYPES`/`NODE_LABELS`/`REPEATABLE_NODE_TYPES` 增加 `call_app`（标签「子应用调用」，可重复添加；与 T086 的 `model` 合并后 palette 为 start/python/agent/model/call_app/condition/join/end）；`defaultNode('call_app')` = `{id,type,inputs:{},output_schema:{type:object,properties:{}},config:{application_version:''}}`；`DEFAULT_POSITIONS` 增加位置。图谱往返按 id 保留 config/inputs/output_schema（既有 `graphToDefinition`），call_app 只有一个隐式 in/out handle。
- `lib/callapp.js`（新）：固定版本选择/映射的纯逻辑 —— `callAppApplicationOptions`、`callAppVersionOptions`（只接受 UUID，残缺/`latest` 行丢弃）、`callAppChildSchemas`（只读冻结 manifest 的 `definition.input_schema/output_schema`）、`callAppInputRows`（literal/input/node 三种绑定 → 行）、`callAppOutputRows`、`callAppBinding`（校验字面量 JSON、input pointer、`node:<id>#/...`）、`callAppNodeFromForm`、`callAppPanelModel`、`callAppPrefillFromNode`、`callAppProblems`（缺失必填、未知字段、静态类型不符、非法来源、输出细化）、`callAppOutputSchemaFor`。
  - 说明：这些是 `apps/web/src/assets/editor.js` 中 call_app sidebar 纯逻辑的**复制**而非 import —— editor.js 是经典脚本（不是 ESM），且 editor 侧边栏行为必须保持不变；画布侧只新增面板所需函数。editor.js/editor.html 未改。
- `main.js`：
  - palette 自动出现 `call_app`；选中节点渲染 `renderCallAppPanel`；挂载时 `loadCallAppApplications`，选中节点时按 pinned version 读取 `GET /api/v1/versions/{id}` 并缓存冻结 schema（`state.callApp.byNode`）；
  - 面板：应用选择 → 已发布版本选择（固定 UUID；展示 version id/manifest sha256）→ 输入映射行（`callApp.<node>.inputs.<field>.mode|.value|.literal`，行容器带 `inputs.<field>` 供服务端错误 focus）→ 子版本输出 Schema 只读 → 「使用子版本输出 Schema」+ 父输出 Schema 编辑器 → 本地提示（`callApp.problems`）；
  - 与 T086/T087/T088 的面板并列渲染（合并冲突已解决）。
- `lib/export.js`：正向 allowlist 增加 `call_app: ['application_version']`；未知 config 键（例如 `latest`）进入 `dropped`，secret/endpoint 类键仍直接拒绝。
- `canvas.css`：新增 `.callapp-row*` 与只读 schema 样式。
- 产物：`apps/web/src/assets/canvas.js` / `canvas.css` 由 `npm ci --offline && npm run build` 重建并提交；合并 T086 → 冲突解决后从合并源重建；合并 T087/T088 → 协调者在集成分支重建，本 worktree 的 `test_canvas_f04b_assets.py` 重建逐字节校验通过（见 §3.4）。

### 2.2 运行时（复用 T083 graph 路径，无 Go 改动）

- graph 路径的 `executeGraphCallApp` → `runCallAppNode`（T083/T078 既有）已满足本票：`call_app` 节点固定版本 `config.application_version`；`LogicalKey = "call_app:" + node.ID`（由节点 id 派生 → 同 child 并行/顺序两次调用天然不同 key）；`spawnChild` 平台协议；child 未终态 → `waiting_children` 提案 + confirm exit；resume 重放同一 logical key → 返回原 child Run 并 adopt 结构化结果；required 子失败 → 父节点/run 取 child 稳定码；无 parent thread 复用（每次 spawn 新建 child `checkpoint_thread_id`）。
- 本票以**真实栈测试**证明上述语义并**未修改** `internal/**`、`contracts/**`、`docs/spec/**`、迁移；`python tools/sync_spec.py --check` → `no drift`。
- 关键环境事实：恢复接缝 `presetexec.Deps.NodeOutputs` → `cmd/supervisor` 的 `dbAccess.NodeData` 从 `ORCHARA_OBJECT_DIR` 打开 sealed 对象并复算 sha256；T033 的 `supervisor_exec_env` 未设置该部署变量，缺省时 seam 以 “no object store” 静默退化为重新执行已完成节点。T089 的 exec helper 显式传入（与 API 部署同一对象根），否则“adopt 原 child / 复用已完成节点”不可观测。建议后续把 `ORCHARA_OBJECT_DIR` 补进共享 helper/部署清单。

### 2.3 测试

- `tests/f04b/callapp_canvas.test.mjs`（Node，纯模块，12 用例）：palette/default、图谱往返、应用/版本选项、冻结 schema、输入行三模式、输出行、`callAppBinding` 负例、`callAppNodeFromForm`、panel model、prefill、本地映射提示、export allowlist。
- `tests/f04b/test_t089_callapp_canvas.py`（pytest）：`node --check` bundle、bundle marker（call_app/固定子版本/映射/allowlist 文案）、Node 模块 `PASS 12`、可选重建逐字节校验（`node_modules` 存在时才跑，finally 还原）。
- `tests/support/t089.py`：真实栈 fixture —— `single_child_definition`（condition yes→child / no→direct(skipped)→join）、`parallel_definition`（fork→a/b→join）、`same_child_twice_definition`（yes→first→second→join）、`defective_binding_definition`、`mapping_type_definition`、`mapping_missing_definition`、`strict_child_definition` 与执行/读取助手（含恢复接缝的 `ORCHARA_OBJECT_DIR` 注入）。
- `tests/f04b/test_t089_callapp.py`（真实栈，7 用例）：单 child 等待/恢复 E2E、并行两 child、同 child 两次、required 子失败传播、执行槽释放/重新占用、发布门禁映射定位、执行器运行期绑定防御。

## 3. 证据：命令与原始输出

### 3.1 画布纯模块 + 提交产物（无需数据库）

```powershell
cd E:\Orchara-wt\t089\product
node tests/f04b/callapp_canvas.test.mjs
```
```text
ok 1 - palette offers call_app with the fixed-version-only default node
...
ok 12 - export allowlist keeps only application_version for call_app
PASS 12 call_app canvas module tests
```

```powershell
cd E:\Orchara-wt\t089\product\apps\web\canvas
npm ci --offline
npm run build
```
```text
added 43 packages, and audited 44 packages in 7s
built ..\src\assets\canvas.js and canvas.css
  ..\src\assets\canvas.js   776.5kb      (T086 合并后 810,667 B)
  ..\src\assets\canvas.css   14.8kb      (15,363 B)
Done in 63ms
```

最终提交产物（合并 T087/T088 后，从集成源重建）：`canvas.js` SHA256 `983E125D338447030358150BC91DD78C68C1363FB9F947224813EBDD24C854E5`（810,667 B）；`canvas.css` SHA256 `AD7EE2463EFAD2656A7ED2A97EBD19EF28C62A8F205DDE22C968118AA4ECB1AE`（15,363 B）。

### 3.2 画布 pytest（含重建逐字节校验）

```powershell
python -m pytest tests/f04b/test_t089_callapp_canvas.py -q
# 4 passed in 0.68s
python -m pytest tests/f04b/test_t089_callapp_canvas.py tests/f04b/test_t086_model_canvas.py `
  tests/f04b/test_t087_canvas.py tests/f04b/test_t088_canvas.py `
  tests/f04b/test_canvas_f04b_assets.py tests/f04a/test_canvas_assets.py -q
# 23 passed in 3.83s   （含 test_the_committed_bundle_matches_a_fresh_build：node_modules 存在，未 skip）
```

### 3.3 真实栈验收（全局 pytest 锁下串行）

第一次运行（T086 合并树，暴露恢复接缝未启用）：
```text
3 failed, 4 passed in 67.28s
E  choose: ['pending','running','succeeded','pending','running','succeeded']  # 未复用已完成节点
```
修正 `ORCHARA_OBJECT_DIR` 注入与复用后的 node.status 断言后：
```powershell
python -m pytest tests/f04b/test_t089_callapp.py -q
```
```text
.......                                                                  [100%]
7 passed in 89.36s (0:01:29)
```
定向回归（T078 子协议 + T083 graph 条件/join 调度）：
```powershell
python -m pytest tests/f07/test_t078_child_runs.py tests/f04b/test_t083_condition_runtime.py -q
```
```text
...............                                                          [100%]
15 passed in 131.63s (0:02:11)
```
合并 T087/T088 + 协调者 T089 merge（`aced554`）后的最终真实栈重跑：
```text
=== integrated T089 acceptance at 2026-11-06T01:47:27 ===
.......                                                                  [100%]
7 passed in 93.25s (0:01:33)
```
三次真实栈运行均在 `New-Item E:\Orchara\.scratch\t083-t091\pytest.lock` 全局锁下串行执行，结束后 `lock_removed=True`；期间与其他票（T085/T086/T087/T088/T090）的套件互斥。

### 3.4 合并与冲突

- `git merge integration/t083-t091`（`f21f09a`，含 T086/T090）：冲突仅 `canvas/src/lib/dsl.js`（palette/defaultNode/positions）、`canvas/src/main.js`（panel dispatch）、生成产物 `canvas.js`；解析为同时保留 `model` 与 `call_app`（palette 顺序 start/python/agent/model/call_app/condition/join/end），并从合并源 `npm run build` 重建两个产物；合并提交 `00826aa`。
- `git merge integration/t083-t091`（`aced554`，协调者已合入 T089 + T087/T088）：快进。合并后画布 23 passed（§3.2，含“committed bundle == fresh build”）；Go 门禁在合并树重跑全绿（§3.5）。

### 3.5 Go / 规格门禁（合并树）

```powershell
cd E:\Orchara-wt\t089\product
go version        # go1.27.3 windows/amd64
go build ./...    # BUILD_EXIT=0
go vet ./...      # VET_EXIT=0
go test ./...     # GOTEST_EXIT=0（全部包 ok，含 presetexec/execution/children/workflow）
gofmt -l internal cmd tools   # 无输出，GOFMT_END=0
python tools/sync_spec.py --check
```
```text
no drift: spec copy and contract snapshot match the manifest
```

## 4. stub / real 与边界（not_run / not_applicable / partial）

- **real**：PostgreSQL 16 隔离集群（`tests/support/stack.py` 每用例新库）、迁移+bootstrap、编译后的 `cmd/api`/`cmd/supervisor`/`cmd/admin`/`cmd/maintenance`、真实 mTLS internal listener、真实对象目录（sealed mapped input / child output / node data artifacts）、真实 `spawnChild` 契约端点、真实 run_children / waiting / quota / jobs 行、真实 F07/T081 child-terminal hook 与 F03/T035 resume 事务、owner-only `GET /runs/{id}/children`、node-results、`/artifacts/{id}/content`。
- **stub / fixture（明确非生产）**：
  - child version 用 T075 fixture 形状：`POST /validate` + 逐字节 canonical manifest + version 行 + dependency 边；**跳过的是** F02B 发布评审事务（T075/T078 已记录，同批沿用）。
  - required 子失败用 T078/T081 先例：真实 finalizer 拒绝测试提供的输出字节（`child_output_schema_invalid`），不是 mock child。
  - 画布无浏览器 DOM（沿用 F04A/F04B 边界）：`main.js` 面板由 `node --check` + bundle marker + 纯模块测试保证；浏览器交互 not_run。
- **not_run**：
  - 浏览器真实 DOM 交互（Vue Flow 拖拽/点击/面板输入）——测试栈无浏览器自动化；面板逻辑抽到 `lib/callapp.js` 由 Node 执行，页面接线由 bundle marker + 重建逐字节校验保证。
  - 真实厂商模型路径（本票不需要；agent 子应用入口属 F07）。
  - 完整 `pytest tests/f04b` / `tests/f07` 全量套件由本票单独执行：未跑（时长/共享栈排队）；本票执行了**本文件 7 用例 + T078/T083 定向回归 15 用例**，并在合并树重跑本文件；全量套件留给集成门禁。
  - 真实 410/取消等 T083 既有边界不在本票范围。
- **not_applicable**：
  - `引用保留 doc/version/span`：call_app 无文档/切片引用；版本保留用固定子版本/manifest hash 断言。
  - `optional 子失败`（T085 语义）：`spawnChild` 契约 body 无 required/optional 字段，delegation receipt 固定 `required=true`；可选子失败在本版本不可表达 → not_applicable（不是“未测”）。

## 5. 合并、门禁与 HEAD

| 项 | 值 |
|---|---|
| 分支 / worktree | `t/t089-callapp-node` / `E:\Orchara-wt\t089` |
| 基线 | `integration/t083-t091` @ `b343f7b` |
| 合并提交 | `00826aa`（T086/T090，冲突解决 + bundle 重建）、快进到 `aced554`（T087/T088/协调者 T089 merge） |
| HEAD | `aced554`（文档提交后为下一提交） |
| 迁移 | 无 |
| 未改 | `internal/**`、`cmd/**`、`contracts/**`、`docs/spec/**`、`db/migrations/**`、`apps/web/src/assets/editor.js`、`runs.js`、`debug.js` |

门禁汇总：画布 4 + 23 passed（合并树）；真实栈 7 passed（T086 树）+ 15 passed 定向回归 + 7 passed（集成树）；`go build/vet/test ./...`、`gofmt -l`、`sync_spec --check` 全绿。**未发现需回退的失败**。

## 6. 变更文件

新增：
- `apps/web/canvas/src/lib/callapp.js`
- `tests/f04b/callapp_canvas.test.mjs`
- `tests/f04b/test_t089_callapp_canvas.py`
- `tests/f04b/test_t089_callapp.py`
- `tests/support/t089.py`
- `reports/f04b/T089-callapp-node.md`（本文件）

修改：
- `apps/web/canvas/src/lib/dsl.js`（call_app palette/default node/位置；与 T086 model 合并）
- `apps/web/canvas/src/lib/export.js`（call_app allowlist）
- `apps/web/canvas/src/main.js`（call_app 面板/异步版本读取/选中钩子；与 T086/T087/T088 面板并列）
- `apps/web/canvas/src/canvas.css`（call_app 面板样式）
- `apps/web/src/assets/canvas.js`、`apps/web/src/assets/canvas.css`（从合并源重建并提交）
- `PROGRESS.md`（小节）
