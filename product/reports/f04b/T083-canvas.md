# T083 画布创作半 / GitHub #84（F04B）— condition/join 画布配置与端口

- 状态：**passed**（画布创作半；运行视图与执行语义属其它票，见「边界与 not_run」）
- 分支：`t/t083-canvas`（worktree `E:\Orchara-wt\t083-canvas`），基线 `integration/t083-t091` @ `ea56392`
- HEAD：`9fd08d2`（实现 + 提交产物；其后仅有 PROGRESS/报告文档提交）。结束前 `git merge integration/t083-t091` → `Already up to date`（集成 tip 仍 `ea56392`），无需再解决产物冲突。
- 环境：Node v24.13.0 / npm 11.13.0；Python 3.12.2 + pytest；PostgreSQL 隔离集群 `127.0.0.1:55432`；离线 npm 缓存 `E:\caches\npm`
- 范围：只改 `apps/web/canvas/src/**`、提交产物 `apps/web/src/assets/canvas.{js,css}`、画布相关测试；未改 Go、`internal/**`、`contracts/**`、`docs/spec/**`、`editor.js`/`debug.js`/`runs.js`。

## 1. AC 对照

| AC（票面） | 结果 | 证据 |
|---|---|---|
| 创作者能在画布上配置比较条件和配对 join（palette → 节点 → 配置面板），配置能保存/往返不丢 | **passed** | `dsl.js` 新增 condition/join 节点、端口 handle 与 config 合并；`main.js` 增加 palette 与两组面板。Node 用例 1–3 用 P05 `condition-branch-approval` / `parallel-two-branch` 断言 graph↔definition 往返后 `edges`（含 `port`）逐字节相等、config 全保留；真实 HTTP 用例把同一 fixture 存成 draft 后 GET 回来断言 `definition` 相等且 `choose` 的两条出边仍为 `yes`/`no`。 |
| 非法跨区边/端口/配对错误有定位与修复提示（不是裸 422） | **passed（数据/文案层；浏览器交互 not_run）** | `regionProblems(definition)` 按 Go 校验阶段 2–4 的检测顺序镜像规则，返回 `{code,message,pointer,nodeId,edgeIndex}`；`errors.js` 把 `/edges/<i>` 解析为 `{edgeIndex,source,target,edgeId}` 并给中文 `hint`；`main.js` 错误项显示 `code + message + source→target + 修复:…`，点击给边加 `orchara-edge-invalid` 并滚动。Node 用例 6 对 13 个 P05 fixture 断言 code+pointer 与参考实现（Go/Python 同一规则的参考实现运行结果）一致；HTTP 用例断言服务端返回 `cross_region_edge` `pointer=/edges/10`、`bad_port` `/edges/1` 与 `/nodes/1/config/cases`、`bad_join_sources` `/nodes/4/config/required_sources`。 |
| 画布产生的 definition 能被服务端 validate 接受/拒绝并定位 | **passed** | 真实 HTTP：condition / parallel / nested 三个 P05 fixture（model/approval 换成已 seed 的 python 节点，使依赖阶段不遮蔽区域判定）均 `valid:true` + `X-Manifest-SHA256`；跨区/端口/join 来源三个非法 fixture 均 `valid:false` 且 pointer 可定位。 |
| 嵌套条件/并行编辑可用（同一套 condition/join 两次） | **passed（数据模型与面板；交互 not_run）** | `condition-nested-parallel` 经 graph↔definition 往返后 9 个节点齐全（拓扑序不截断）、config 保留；`regionProblems` 对它返回 `[]`；HTTP validate `valid:true`。面板对任意数量的 condition/join 节点重复渲染（palette 可多次添加）。 |
| 导出按白名单、不可注入未展示能力（P1） | **passed** | `export.js` 按节点类型 allowlist 重建 definition；未知类型/未知 config 键丢弃并在返回值 `dropped[{path,reason}]` 报告；secret/endpoint/run_input 仍直接拒绝（`ExportRefused`）。Node 用例 8 覆盖正例、未知 config、未知节点类型、顶层多余键、`api_key`、`endpoint_ref`。 |
| 真实 HTTP validate 用例（P1） | **passed** | `tests/f04b/test_t083_canvas_validate.py`：3 passed（见 §4）。 |

## 2. 实现要点

### 2.1 `apps/web/canvas/src/lib/dsl.js`

- `NODE_TYPES = ['start','python','agent','condition','join','end']`；`NODE_LABELS` 增加「条件分支 / 并行汇合」；`REPEATABLE_NODE_TYPES`（python/agent/condition/join 可多个，start/end 唯一）。
- `defaultNode`：condition `{join_id:'',default_port:'no',cases:[{input:'',op:'eq',value:true,port:'yes'}]}`；join `{mode:'all_active',fork_id:'',required_sources:[],optional_sources:[]}`。
- `definitionToGraph`：节点 `data.nodeType/data.config`；condition 生成命名 source handle（`cases[].port ∪ default_port` 去重，`data.ports`）；join 单 target handle `in` 并带 `data.sourceLabels`；边写 `sourceHandle=port`、`data.port`、`label`、`targetHandle='in'`。普通节点只有隐式单 handle。
- `graphToDefinition`：从 `edge.data.port`（条件边回退 `sourceHandle`）恢复 `edge.port`，空则省略；node config/inputs/output_schema 按 id 深拷贝合并；节点顺序为拓扑序（Kahn，cycle 时回退声明序，绝不丢节点）；join 的 required/optional sources 会剔除已删除节点（悬空清理）。
- `connectionProblem/canConnect`：通用 DAG 规则 —— self/duplicate/cycle、end 无出/start 无入；只有 join 可多入（否则 `merge_without_join`）；condition 可多出且每 port 唯一（`bad_port`/`port_exists`/`no_free_port`，未指定时按 cases 顺序+default_port 自动选下一个空闲端口）；普通节点多出必须由其配对 join 的 `fork_id` 声明（否则 `fork_requires_join`）。
- `regionProblems(definition)`（替换 `linearChainProblems`/`definitionProblems`）：镜像 Go 阶段 2–4：duplicate_id/unknown_endpoint/start_end_count → cycle/unreachable_node/cannot_reach_end/merge_without_join → invalid_region（双向配对、普通 fork 声明）→ bad_port（条件端口对齐、非条件边带 port）→ cross_region_edge（dom/pdom 计算 region 内部集合）→ bad_join_sources（直接前驱覆盖且互斥）。阶段 5（引用/类型/missing_value）不本地判定，服务端权威。中文消息包含节点/边两端与修复方向。
- 导出 `conditionPorts`、`edgePortOf`、`edgeIdFor`、`regionPairs`、`branchNodeIds`、`dominanceOf` 供 sources/errors/main 复用。

### 2.2 `apps/web/canvas/src/lib/sources.js`

- 合法来源 = 应用输入 + 支配当前节点的上游输出（图完整时用 dom；编辑中回退 ancestor）+ 可见 join 的封套：`node:<join>#/results`、`node:<join>#/results/<source>`、`node:<join>#/results/<source>/value`。
- region 内部的分支节点不再直接暴露（只能经 join 按来源读取）；join 自身也不暴露其分支输入。
- 不可支配的上游（某些路径会 skipped）不提供，避免选出会变成 `missing_value` 的引用；`sourceType` 支持 join 封套路径。

### 2.3 `apps/web/canvas/src/lib/errors.js`

- `/edges/<i>` → `{edgeIndex, source, target, edgeId, documentLevel:false}`，`nodeId=source`；condition 出边 field 为 `edge.port`，其它为 `edge`；越界边保持文档级 `edges`。
- `fixHintForCode`：`cross_region_edge` / `bad_port` / `bad_join_sources` / `merge_without_join` / `invalid_region` / `fork_requires_join` / `port_exists` / `no_free_port` / `missing_value` 等中文修复提示，未知码有兜底。

### 2.4 `apps/web/canvas/src/lib/export.js`

- `CONFIG_ALLOWLIST`：start/end `{}`；python `tool_version`；agent 五个键；condition `join_id/default_port/cases`；join `mode/fork_id/required_sources/optional_sources`。cases 项只保留 `input/op/value/port`。
- 顶层键、节点键、边键也按契约允许集重建；未知项进入 `dropped`（确定性顺序）；有丢弃时导出对象才多出 `dropped` 键，干净导出仍是 `{definition, layout}`。
- 负例仍抛 `ExportRefused`（secret/token/password/api_key/credential/bearer/oauth/memory/personal/run_input/endpoint）。

### 2.5 `apps/web/canvas/src/main.js` + `canvas.css`

- Palette 六类；start/end 已存在时禁用，condition/join/python/agent 可重复添加，id 唯一。
- 自定义节点渲染（Vue Flow `node-default` 插槽 + `Handle`）：condition 每个端口一个命名 source handle 与标签，join 显示来源标签与单 target handle。
- condition 面板（稳定 `data-field`）：`config.join_id`（候选 join，选择后同步 join.fork_id）、`config.default_port`、`config.cases.<i>.input/op/value/port`、上移/下移/删除、添加 case、端口预览 `config.ports`（已连线标记）、输入映射（合法来源）；value 为 JSON 文本，非法时仅提示不提交。
- join 面板：`config.mode` 只读 `all_active`、`config.fork_id`（候选=至少有出边的非 end/join 节点，选择后若是 condition 则同步其 join_id）、`config.required_sources` / `config.optional_sources` 多选且互斥、覆盖情况提示 `config.join_sources.coverage`。**UX 决策**：候选包含“已有 1 条出边”的节点而不只出度>1——否则普通 fork 的第二条出边会被连线规则拒绝而无法先声明 fork（brief 的“出度>1”在这个规则下是鸡生蛋问题）。
- 边单击 → `edge.port` 输入（仅 condition 源），修改即写回 `edge.data.port` 并重建 definition，非法端口立即在状态栏提示；`edge.remove` 删除连接。
- validate：服务端错误优先；API 不可用/请求失败时展示 `regionProblems` 本地提示（明确“以服务端为准”）；点击节点错误选中节点并 focus `data-field`，点击边错误高亮 `orchara-edge-invalid` 并滚动画布。
- 导出：走 `exportPayload`，状态栏报告按白名单丢弃的字段数。
- 构建：`build.mjs` 增加 `charset:'utf8'`，提交产物中的中文面板文案可读（便于 marker 测试/评审）。

## 3. 提交产物（bundle）

构建命令（worktree 内）：

```powershell
cd E:\Orchara-wt\t083-canvas\product\apps\web\canvas
npm ci --offline   # added 43 packages in 4s
npm run build      # built ..\src\assets\canvas.js and canvas.css; 751.2kb / 14.2kb
```

| 产物 | 改前 SHA256 | 改后 SHA256 |
|---|---|---|
| `apps/web/src/assets/canvas.js` | `4915DE6947CBFFD6D83F8D787F3C660007860A02626FB418DD6CC145832E56EB` | `618410A7B351BFCD9EAE2398387EB483052EE30A556913A860016E1E8D944D83` |
| `apps/web/src/assets/canvas.css` | `167AAC85368FD454F12418C7C0A5200C58241397B397D476B1D5F548661AC165` | `34BA780F41E48CE98A5B3BD75002F3D14EB6E14C80EB75BCE6D895D2A251279B` |

`python -m pytest tests/f04b -q` 中的重建校验（`node_modules` 存在时）会把 bundle 重新构建到原路径、逐字节比较并在 `finally` 还原，通过；未提交手写产物。

## 4. 门禁（真实命令 + 原始输出 tail）

```powershell
cd E:\Orchara-wt\t083-canvas\product
node --check apps/web/src/assets/canvas.js
node tests/f04b/canvas_f04b.test.mjs
python -m pytest tests/f04b -q
python -m pytest tests/f04a -q
python tools/sync_spec.py --check
```

- `node --check`：exit 0（无输出）。
- `node tests/f04b/canvas_f04b.test.mjs`：

```text
ok 1 - palette offers condition/join with Chinese labels and repeatable node ids
...
ok 9 - validation errors locate edges and carry Chinese repair hints
PASS 9 canvas F04B module tests
```

- `python -m pytest tests/f04b -q`：

```text
........                                                                 [100%]
8 passed in 30.60s
```

- `python -m pytest tests/f04b/test_t083_canvas_validate.py -q`：

```text
...                                                                      [100%]
3 passed in 29.66s
```

- `python -m pytest tests/f04a -q`（`ORCHARA_P01_PYTHON` 指向主仓 P01 venv）：

```text
....................................                                     [100%]
36 passed in 85.31s (0:01:25)
```

- `python tools/sync_spec.py --check`：

```text
no drift: spec copy and contract snapshot match the manifest
```

说明：本次未改 Go/`internal`/`contracts`，`go build/vet/test` 不属于本票门禁；`f04a` 的 P01 LangGraph 解释器通过 `ORCHARA_P01_PYTHON=E:\Orchara\product\probes\agent_min\.venv\Scripts\python.exe` 指定（本 worktree 未复制该 gitignored venv）。

## 5. 回归：F04A 既有画布测试的调整

F04B 有意替换线性链语义，`tests/f04a/canvas_modules.test.mjs` 做了最小同步（仍 8 个用例、`PASS 8`）：

- `linearChainProblems` → `regionProblems(definition)`（无环/单 start/end 的本地规则）。
- 连接码：`outgoing_exists`/`incoming_exists` → `merge_without_join`（非 join 多入）与 `fork_requires_join`（未声明 fork 的多出）。
- `addNode`：同类型多个现在允许（python/agent/condition/join），start/end 仍唯一。
- `/edges/<i>` 错误从文档级改为边级（nodeId=source），`documentLevelErrors` 期望值相应变化。

F04A 其它页面/资产/HTTP 用例未改。

## 6. 测试清单

`product/tests/f04b/canvas_f04b.test.mjs`（Node ESM，直接执行 `canvas/src/lib/*.js` 真实字节；fixture 全部来自 `probes/p05_dsl/corpus.json`）：

1. palette/labels/defaults/重复 id；
2. condition 端口 + graph↔definition 往返（`condition-branch-approval`，含 model/approval 未知类型 config 保留）；
3. parallel fork/join 往返 + 嵌套拓扑序（`parallel-two-branch`、`condition-nested-parallel`）；
4. 连接规则（自动端口/端口重复/无空闲端口/bad_port/join 多入/非 join 多入/fork 需声明/删除清理 join sources）；
5. 7 个合法 P05 fixture 的 `regionProblems == []`；
6. 12 个非法 P05 fixture 的 code+pointer 精确匹配参考实现（`merge_without_join`、`invalid_region`×2、`bad_port`×2、`cross_region_edge`、`bad_join_sources`、`cycle`、`unreachable_node`+`cannot_reach_end`、`cannot_reach_end`、`start_end_count`、`duplicate_id`+`unknown_endpoint`、`unknown_endpoint`），以及边级 `edgeIndex/nodeId`；阶段 5 的 `skipped-branch-reference` 明确断言本地返回 `[]`（服务端权威）；
7. sources：join 封套按来源、join 前分支输出被隐藏、join 自身不可读分支、嵌套 join 下游按 `outer_join#/results/inner_join/value`；
8. export allowlist 正/负例与 `dropped` 报告；
9. errors 边定位 + 中文 hint。

`product/tests/f04b/test_canvas_f04b_assets.py`（pytest 包装）：bundle `node --check`、condition/join 面板/边高亮 marker、依赖 pin/lock、`node canvas_f04b.test.mjs` 断言 `PASS 9`、`node_modules` 存在时重建并逐字节比较（否则 skip）。

`product/tests/f04b/test_t083_canvas_validate.py`（真实 HTTP + PostgreSQL + 真实 API 进程）：condition 端口往返 + validate、parallel/optional/nested 三 fixture validate、跨区/端口/join 来源定位。依赖阶段先于 DSL 阶段，因此 fixture 的 model/approval 节点换成 seed 的 ready+approved python 工具版本；runner 不可用，未提交 debug-run（执行器当前拒绝 F04B 图）。

## 7. 边界与 not_run

- **浏览器 DOM 交互 not_run**：仓库测试栈没有浏览器自动化（`tests/requirements-test.lock.txt` 无 Playwright，缓存无 jsdom/happy-dom）。`main.js` 的 Vue/DOM 接线由 `npm run build` + `node --check` + bundle marker + 纯模块 Node 测试保证；点击/拖拽/焦点/高亮滚动本身未由真实浏览器驱动。与 F04A/T040 的既有边界一致。
- **运行 F04B 图 not_run**：执行器（`internal/presetexec`）目前拒绝 condition/join，运行视图属另一子任务。本票只证明“画布可创作 + 服务端 validate 接受/定位”。
- **本地规则不是安全边界**：`regionProblems` 只镜像阶段 2–4 做提示；不阻止保存（draft save 本就不校验 DSL），服务端仍复验一切。
- **导出白名单只覆盖本票六类节点**：从服务端草稿加载的 `model/approval/retrieve/call_app` 节点配置在导出时会被丢弃并计入 `dropped`（不丢保存路径，只有导出下载受影响）；T091 负责扩展为十类，届时改 `CONFIG_ALLOWLIST` 与用例即可。
- `dropped` 键仅在发生丢弃时出现在导出对象里，干净导出仍是精确的 `{definition, layout}`。
- 未实现/未做：端口选择模态、更细 region 可视化、run-view 面板（另一子任务）；按父代理决定未新增任何 fetch surface，也未改 `contracts/openapi.json`。
- 未添加 `editor.html` 的 `canvas-run-root`（运行视图分支负责，避免冲突）。

## 8. 风险

1. `regionProblems` 与 Go 校验阶段 2–4 是“同规则两份实现”，未来若 Go 增加阶段 4 规则会漂移；当前以 P05 全量 region fixture + 真实 HTTP pointer 断言约束。
2. 自定义 Vue Flow 节点（命名 handle）没有浏览器测试；升级 Vue Flow 时需人工冒烟。
3. 导出白名单对未知节点类型的“丢弃并报告”在 T091 扩展前属于有意的中间态，跨票合并时须保留 `CONFIG_ALLOWLIST` 扩展点。
4. `main.js` 的边索引映射依赖 `flowEdges` 与 `definition.edges` 同序（`definitionToGraph`/`graphToDefinition` 都按数组顺序处理）；若未来在画布内排序边，需要改为稳定 `edgeId` 查找。
