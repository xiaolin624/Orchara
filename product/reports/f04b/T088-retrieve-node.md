# F04B/T088 — 把知识检索节点的真实引用传给后续步骤（GitHub #89）

**状态：`passed`（运行时 retrieve + 内部 ACL 检索路由 + 画布面板 + 运行视图引用链接；真实栈验收见 §4）。
`pytest tests/f10` 全量回归未在本票最后窗口重跑（共享栈串行，见 §5 的 not_run 说明）。**

- 分支：`t/t088-retrieve-node`，worktree `E:\Orchara-wt\t088`，基线 `integration/t083-t091` @ `111e2c2`；
  进行中两次 merge 集成分支（`b343f7b`→`f21f09a`），最终 HEAD 见 §6。
- 范围：Go `internal/presetexec` 的 retrieve 节点、新的非契约内部检索路由（`internal/api`、`internal/knowledge`、
  `internal/execution`、`cmd/supervisor` 的 mTLS 客户端）、画布 palette/面板/导出白名单、调试运行视图的引用链接。
- 不改 `product/docs/spec/**`、`product/contracts/**`；无迁移。`sync_spec --check` → `no drift`（§4.5）。
- 未把知识服务内联进 supervisor：执行器只经 Client seam 调内部路由，服务端用 `runs.owner_id` 与
  `knowledge.Service.Query` 的 ACL SQL 过滤。

## 1. 验收对照（票面 AC + 本票改写项）

| 验收项 | 结论 | 证据 |
|---|---|---|
| 引用保留 doc/version/span（item 与 citations 投影都保留 `document_id`/`document_revision`/`span`，`citation` 原样 JSON） | **passed** | `tests/f04b/test_t088_retrieve.py::test_retrieve_returns_real_citations_and_passes_them_downstream`：item 与 DB `knowledge_chunks` 行的 `document_id`/`document_revision`/`chunk_no`/`span`/`citation` 逐项相等，`excerpt` 与封印 chunk body 的前 500 字符相等且不含完整正文/`score`；`GET /knowledge-citations/{chunk_id}` 返回同一来源的完整正文 |
| 在画布选知识库和合法字段、运行后可打开真实来源；无结果/撤权按契约 | **passed** | 画布：`tests/f04b/test_t088_canvas.py`（`canvas_t088.test.mjs` PASS 6）；运行视图：`tests/f04b/test_t088_runview.py`（`runview_t088.test.mjs` PASS 6）打开真实 citation/404 不可用；真实栈无结果/撤权用例见 §4.1 |
| 相同 child 并发不串 thread → **not_applicable**（retrieve 无 child），改写为「两个并行 retrieve 节点不串结果」 | **passed（改写口径）** | 真实栈 `test_two_parallel_retrieve_nodes_keep_their_own_documents`（两分支各自文档/引用，join 信封两源独立）+ Go `TestGraphRunTwoRetrieveNodesEachKeepTheirOwnResults` |
| 节点等待按交接协议释放槽 → **not_applicable**（retrieve 不等待、不产生 `waiting`） | not_applicable | retrieve 不提交 `waiting_children`，不占 child 槽；supervisor 在执行完 retrieve 后继续拓扑序，无等待交接 |
| retrieve 使用平台 ACL 检索；不暴露框架内部对象为公共字段 | **passed** | 内部路由用 attempt_token 复证 live attempt，owner 取 `runs.owner_id`（body 无 owner）；真实栈 `test_the_internal_route_derives_the_owner_and_counts_the_acl`（foreign KB 静默排除、`requested/authorized`、403/409/422）+ `test_a_foreign_knowledge_base_never_reaches_another_owners_run`；输出 key 集合恰为约定、无 object key/signed URL |
| 失败、当前授权与重试/恢复边界有断言，不以单层 mock 成功代替 | **passed** | 真实 PG+API+mTLS+supervisor + 真实知识摄取（上传→finalize→document_ingest→ready）；`retrieve_unavailable`/`retrieve_denied`（Go 接缝）与 `node_input_invalid`（Go）断言；撤权后旧 citation 404 + 检索空结果（真实栈）；同 run 第二次 exec 复用 retrieve 输出 not_run（见 §5） |
| 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报 | **passed** | §3 环境/版本、§4 raw tails、§5 stub/real 分界；`pytest tests/f10` 全量未重跑（§5） |

## 2. 设计决定

### 2.1 内部检索路由（非契约）

`POST /internal/v1/runs/{run_id}/retrieval`（`internal/api/retrieval.go`，注册在
`internal/api/internal.go::RegisterInternal`；仅内部 mTLS listener）：

- body：`{attempt_token, knowledge_base_ids:[uuid], query:string, top_k:int}`。worker mTLS + `X-Internal-Role: worker`
  （`RequireInternalMTLS` 已固定该规则；supervisor 证书 403）。
- **owner 来源**：`execution.RunArtifactStore.RunOwner` 复用与 run artifact 路由相同的 live-lease 语句
  （`run_attempts` ⨝ `runs` ⨝ `jobs.current_attempt_token`，未 exited/revoked 且租约未过期），返回 `runs.owner_id`。
  body 没有 owner 字段；换 token 或换 run 都只会得到 `409 stale_attempt`。
- **ACL**：`knowledge.Service.QueryBases` 对每个 KB 调既有 `Query`（SQL 先做 owner ACL/tombstone/active index/
  embedding revision/body artifact 过滤，再在进程内排序）。`ErrNotFound`（不存在/非 owner/已删）静默跳过并计数，
  响应只含 `requested`/`authorized`，不回显不可见 KB id；全部不可见 = 200 + 空 `items`。
- **合并排序**：多 KB items 合并按 `score DESC`、稳定 tie-break `chunk_id ASC`，截断 top_k（Go 单测
  `TestMergeChunkViewsOrdersByScoreThenChunkID`）。
- **响应字段**：`items[{chunk_id,document_id,document_revision,document_state,chunk_no,span,text,score,citation}]` +
  `requested`/`authorized`；不含 embedding、object key、signed URL、DSN。
- **错误映射**：非法 JSON `400 invalid_json`；语义体 `422 validation_failed`（field 定位）；非 live attempt
  `409 stale_attempt`；embedding/object store 未配置 `503`；空结果 `200`。复用 `writeRunArtifactError`/
  `writeKnowledgeFailure` 的既有词汇。
- 非契约：不改 `contracts/openapi.json`；代码注释与本报告为文档面。

### 2.2 执行器 retrieve 节点（`internal/presetexec/retrieve.go`）

- `graphplan.go`：`graphNodeTypes` 加 `retrieve`；`hasGraphConstructs` 识别 retrieve（无 region 也走 graph 路径）；
  config 复验 `{knowledge_base_ids: uuid[] min1 unique, top_k:1..20}` 且拒绝未知键/内联 `query`
  （`DisallowUnknownFields`，`bad_config` + `/nodes/<i>/config` 定位）。T083 的 `node_unsupported_retrieve` 已移除。
- **query 输入**：节点 `inputs` 必须绑定 `query` 且为字符串（运行期 `resolveNodeBindings`）；缺失/非 string/
  空串 → `node_input_invalid`，消息定位 `/nodes/<i>/inputs/query`。
- **执行**：`Client.Retrieve`（`cmd/supervisor/client.go`，worker mTLS）调用内部路由；supervisor 从不 import
  knowledge service。
- **输出**（非契约约定，文档化；批次复核修复后已收窄）：`{items:[{chunk_id,document_id,document_revision,
  document_state,chunk_no,span,excerpt,citation}], citations:[{chunk_id,document_id,document_revision,
  span,excerpt}], count}`；`excerpt` 是 chunk 正文的前 500 字符（按 rune 截断，不切断多字节字符），
  **节点输出不含完整 chunk 正文、不含向量 `score`**（R3 要求短摘要、N-R10 禁止把 chunk body/向量分数当平台字段；
  完整正文只能经 owner-only `GET /api/v1/knowledge-citations/{chunk_id}` 读取）；`citation`/`span` 来自存储 citation
  的原样 JSON。内部 worker 检索路由仍返回 text/score 供服务端排序/合并，它们不进入节点数据。输出在登记前按节点
  `output_schema` 复验（`node_output_invalid`）。
- **artifact/事件**：data artifact label `results`，`node.status` pending→running→succeeded；失败码
  `node_input_invalid`、`node_output_invalid`、`retrieve_unavailable`（503/网络/非 403 拒绝，新增并文档化）、
  `retrieve_denied`（服务端 403，仅调用被拒绝时；全部 KB 不可见不是 denied）。
- **无结果**：`{"items":[],"citations":[],"count":0}`，节点 succeeded（不是 failed）。
- **并发**：graph 调度逐节点执行，两个 retrieve 节点各自发起调用、各自持有输出；无共享 map/状态。
- 不缓存正文：引用点击走既有 owner-only `GET /api/v1/knowledge-citations/{chunk_id}`，撤权/删除后 404。

### 2.3 画布（`apps/web/canvas/src`，bundle 已重建并提交）

- `lib/dsl.js`：`NODE_TYPES`/`NODE_LABELS`/`REPEATABLE_NODE_TYPES` 加 `retrieve`（标签「知识检索」）；
  `defaultNode('retrieve')` = `{config:{knowledge_base_ids:[],top_k:5}}`；新增 `retrieveOutputSchema()`（items/
  citations/count 的受限子集模板，一键填入）。
- `main.js`：新增 retrieve 面板 —— KB 多选（`GET /api/v1/knowledge-bases?limit=100`，owner 可见；不可见已选 id
  保留并以文案说明运行期 ACL 会排除）、`top_k` 1..20、`query` 来源选择器（合法 string 来源）、输出 schema 编辑 +
  「填入 retrieve 输出模板」；`loadCatalogs` 同时加载知识库。
- `lib/export.js`：allowlist 增 `retrieve: ['knowledge_base_ids','top_k']`；内联 `query`/未知键照旧丢弃并报告
  `dropped[{path,reason}]`。
- `lib/sources.js`：无需改算法；模板把 `items`/`citations` 声明为数组后，选择器即可选
  `node:<id>#/citations`、`#/items`、`#/count`（对象/数组字段本就可选）。**array item 固定索引选择未实现**
  （见 §5 边界）。

### 2.4 运行视图（T083 runview 已合并）

- `apps/web/src/assets/runview.js`：retrieve 行的 data artifact（`{items,citations,count}`）被预取；
  渲染「检索 N 段 · 引用 M 条」（或「无结果」）与每条 citation 的链接
  `GET /api/v1/knowledge-citations/{chunk_id}`；点击时 fetch 真实来源：200 展开存储正文与 doc/version/span，
  404 显示「该引用已不可用（权限撤销或文档已删除），未提供缓存正文」且不显示旧正文；`retrieve_unavailable`/
  `retrieve_denied` 有稳定码说明。
- `debug.html`：引用链接/状态样式。

## 3. 环境与版本

| 项 | 值 |
|---|---|
| Go | `go1.27.3 windows/amd64`；`GOCACHE=E:\orchara-toolchains\gocache`，`GOMODCACHE=E:\orchara-toolchains\gomodcache` |
| PostgreSQL | 16，隔离集群 `127.0.0.1:55432`（`tests/support/stack.py` 每用例新库） |
| Python | 3.12.2；pytest 9.0.3 |
| Node | v24.13.0；canvas bundle 由 `npm ci` + `npm run build` 重建（esbuild 已提交产物） |
| Sandbox | WSL2 Ubuntu-24.04 + runsc；retrieve 用例本身不启动 sandbox（supervisor 启动时仍校验 sandbox profile） |
| 模型/嵌入 | 仅 `dev-stub`；知识检索用部署默认确定性 `local-hash-v1` 嵌入（无真实厂商凭据） |
| 输入/hash | 知识源 `SAMPLE_DOCUMENT`（6073 bytes）`sha256=373653c453ae1b1d2d06bc5c0ce432193b1a56627d2f19201e2fb5e40b81b61d`；查询见 §4；manifest `orchara-execution-manifest/1.0`，`compiler_version=orchara-compiler/1.0.0` |

## 4. 证据：命令与原始输出

工作目录 `E:\Orchara-wt\t088\product`。原始日志：`E:\Orchara\.scratch\t083-t091\T088\`。

### 4.1 真实栈验收

工作目录 `E:\Orchara-wt\t088\product`；在全局 pytest 锁下、确认无其它 pytest 进程后运行
（原始日志 `E:\Orchara\.scratch\t083-t091\T088\pytest-t088-2.log`、
`.../pytest-t088-final.log`）：

```powershell
# 合并 T086/T090 的树（f194aea）
python -m pytest tests/f04b/test_t088_retrieve.py -q
......                                                                   [100%]
6 passed in 51.63s

# 合并 T085（并发调度器）/T087（approval）/T089 后的最终树（a6cdf8e）复跑
python -m pytest tests/f04b/test_t088_retrieve.py -q
......                                                                   [100%]
6 passed in 78.47s (0:01:18)

# 再并入集成 tip 1cf7637（T085 skipped-facts 修复 + 各票证据合并）后的最终树（8a13ee2）
python -m pytest tests/f04b/test_t088_retrieve.py -q
......                                                                   [100%]
6 passed in 74.49s (0:01:14)
```

每个用例的真实链路：真实 draft 保存（Go workflow 编译器）→ 真实 debug 提交（封印输入）→ 编译后的
`cmd/supervisor exec --once` 领取 attempt → worker mTLS 调新内部检索路由 → API 从 `runs.owner_id`
取 owner 并调 `knowledge.Service.Query`（真实 PostgreSQL 行）→ 节点 data artifact + node.status →
owner-only node-results / citation 路由读取。

| 用例 | 断言要点 |
|---|---|
| `test_retrieve_returns_real_citations_and_passes_them_downstream` | items/citations/count 精确形状；item 8 字段、citations 5 字段；与 DB chunk 行 + 存储 citation 逐字段相等；`excerpt` 与封印 chunk body 的前 500 字符相等，且输出不含完整 `text`/`score`；`GET /knowledge-citations/{chunk_id}` 返回完整来源；end 下游拿到相同 citations；node.status 序列；输出无 object key/signed URL；发布校验 `valid:true` |
| `test_retrieve_without_any_retrievable_chunk_succeeds_with_empty_items` | 空候选集 → `{"items":[],"citations":[],"count":0}`，节点/run succeeded，final output 空数组 |
| `test_a_foreign_knowledge_base_never_reaches_another_owners_run` | A 的 run 同时命名 A/B 的 KB：只出 A 文档，B 的 document/chunk id 不出现在序列化输出；只命名 B：空结果且 succeeded |
| `test_the_internal_route_derives_the_owner_and_counts_the_acl` | 直接 mTLS 调路由：`requested=2/authorized=1`；items 只含 A 文档；score 降序；全 foreign → `requested=2/authorized=0` 空列表；supervisor 证书 403；坏 token 409 `stale_attempt`；空/重复 KB、空 query、top_k 0/21 → 422 + field 定位 |
| `test_revoked_knowledge_returns_nothing_and_the_old_citation_is_unavailable` | 撤权后旧 citation 路由 404；run 检索为空且 succeeded；输出不含旧 chunk id |
| `test_two_parallel_retrieve_nodes_keep_their_own_documents` | 两个并行 retrieve 各自只含本文档与本文档引用；join 信封两源 value 独立；三个节点 pending→running→succeeded |

补充回归（同一合并树、锁下）：`tests/f04b/test_t083_runview.py` **7 passed**（run-view condition/join/skipped
真实 E2E 与页面/资产检查；该次运行中只有 T088 文件因当时 fixture 用错 stack 而失败，随后修复见 §5）。

### 4.2 Go 单测（stub 接缝，单独分报）

```powershell
go test ./internal/presetexec -run Retrieve -v      # 11 tests
go test ./internal/knowledge -run MergeChunkViews -v # 3 tests
go test ./internal/workflow -run Retrieve -v         # 2 tests
```
```text
=== RUN   TestParseRetrieveConfigAcceptsTheContractShape
--- PASS: TestParseRetrieveConfigAcceptsTheContractShape (0.00s)
=== RUN   TestParseRetrieveConfigRejectsBadConfigs
--- PASS: TestParseRetrieveConfigRejectsBadConfigs (0.00s)
=== RUN   TestBuildGraphPlanAcceptsRetrieveNodes
--- PASS: TestBuildGraphPlanAcceptsRetrieveNodes (0.00s)
=== RUN   TestBuildGraphPlanRejectsABadRetrieveConfig
--- PASS: TestBuildGraphPlanRejectsABadRetrieveConfig (0.00s)
=== RUN   TestGraphRunRetrievePublishesCitationsAndPassesThemDownstream
--- PASS: TestGraphRunRetrievePublishesCitationsAndPassesThemDownstream (0.02s)
--- PASS: TestGraphRunRetrieveWithoutResultsStillSucceeds (0.02s)
--- PASS: TestGraphRunRetrieveMissingQueryIsALocatedNodeInputFailure (0.00s)
--- PASS: TestGraphRunRetrieveNonStringQueryIsNodeInputInvalid (0.00s)
--- PASS: TestGraphRunRetrieveServerFailuresUseTheStableCodes (0.00s)
--- PASS: TestGraphRunRetrieveValidatesItsOutputSchema (0.00s)
--- PASS: TestGraphRunTwoRetrieveNodesEachKeepTheirOwnResults (0.04s)
PASS
ok  	orchara/platform/internal/presetexec	1.371s
=== RUN   TestMergeChunkViewsOrdersByScoreThenChunkID
=== RUN   TestMergeChunkViewsKeepsTopKAndDeduplicates
=== RUN   TestMergeChunkViewsIsAnEmptyListWithoutItems
PASS
ok  	orchara/platform/internal/knowledge	1.126s
=== RUN   TestValidateAcceptsTheRetrieveNode
=== RUN   TestValidateRejectsABadRetrieveConfig
PASS
ok  	orchara/platform/internal/workflow	(cached)
```
（接缝说明：`fakeClient.Retrieve` 是测试脚本 seam，sandbox 是 fake；这些只证明节点语义，**不是**生产验收；
真实链路在 §4.1。）

### 4.3 画布/运行视图模块测试（Node 执行已提交字节）

```powershell
python -m pytest tests/f04b/test_t088_canvas.py tests/f04b/test_t088_runview.py tests/f04b/test_canvas_f04b_assets.py -q
```
```text
...........                                                              [100%]
11 passed in 1.23s
```
其中：
```text
PASS 6 canvas T088 module tests
PASS 6 runview T088 tests
PASS 9 canvas F04B module tests      # T083 画布回归（allowlist 用例已改为 mystery 未知类型）
```
`tests/f04b/test_canvas_f04b_assets.py::test_the_committed_bundle_matches_a_fresh_build` 在本 worktree
（`apps/web/canvas/node_modules` 已 `npm ci`）实际重建 `canvas.js`/`canvas.css` 并逐字节比对通过后还原。

### 4.4 回归门禁

在合并树 `f194aea`（含 T086 model/T090）与最终树 `a6cdf8e`（含 T085 并发调度器/T087/T089）上：

```powershell
python -m pytest tests/f04b/test_t083_runview.py -q          # f194aea，与 T088 同一次锁内运行
python -m pytest tests/f04b/test_t088_canvas.py tests/f04b/test_t088_runview.py `
                 tests/f04b/test_t086_model_canvas.py tests/f04b/test_canvas_f04b_assets.py -q
python -m pytest tests/f04b/test_t088_canvas.py tests/f04b/test_t088_runview.py `
                 tests/f04b/test_canvas_f04b_assets.py -q     # 最终树 a6cdf8e
go build ./... ; go vet ./... ; go test ./...
```
```text
7 passed (test_t083_runview.py，真实 condition/join/skipped E2E)
15 passed in 2.38s (canvas/runview/asset 模块测试，f194aea)
11 passed in 2.28s (T088 canvas/runview/asset 模块测试，a6cdf8e)
go build/vet 干净；go test ./... 全部 ok（最终树 a6cdf8e）
```

**not_run**：`python -m pytest tests/f10 -q` 全量回归。原因：真实栈必须串行（同一 WSL/runsc state root +
共享 PostgreSQL 集群，编排者全局锁）；本票最后窗口的锁被其它票的真实栈套件连续占用，未再取得可信用运行窗口。
影响面评估：本票对 `internal/knowledge` 只**新增** `QueryBases`（内部调用既有 `Query`，不改 `service.go`/API 路由），
知识摄取/检索/answer 路径未改；T088 真实栈用例本身即走真实知识摄取与 `Query`，覆盖了该接缝。

### 4.5 契约/格式门禁

```powershell
gofmt -l internal cmd tools
python tools/sync_spec.py --check
```
```text
（gofmt 无输出，exit 0）
no drift: spec copy and contract snapshot match the manifest
```

## 5. stub / real 与边界（not_run / partial）

- **real（生产验收）**：PostgreSQL 16 + 真实迁移/bootstrap；编译后的 `cmd/api`（真实 mTLS internal listener、
  真实对象目录/HMAC 签名链接）、`cmd/supervisor exec --once`、`cmd/maintenance`（document_ingest 与
  artifact_finalize 两类真实池）；真实知识摄取（上传→finalize→document_ingest→ready）、真实 chunk/citation 行；
  owner-only node-results / citation 路由；调试运行视图资产由真实 `cmd/api` 页面加载（T083 已有页面用例）。
- **dev-stub / seam（单独分报，不冒充生产）**：
  - 知识检索的嵌入是部署的确定性 `local-hash-v1`（无厂商嵌入凭据）；**没有真实厂商模型调用**（retrieve 不需要模型）。
  - Go 单测用 `fakeClient.Retrieve`/`fakeClient`/`fakeSandbox`/`fakeInputs` 脚本接缝：只证明节点语义、码与输出形状。
  - 画布/运行视图逻辑用 Node `vm` 执行已提交的真实字节 + 最小 DOM/fetch shim；**浏览器 DOM 未驱动**。
  - **测试栈修正（证据诚实）**：首轮真实栈运行用的是 `t055_stack`（只有知识服务环境），debug 提交因缺
    `ORCHARA_WORKER_IMAGE_DIGEST` 得到 `422 compiler_unconfigured`（5 例）与一个 helper 把 UUID 字符串拆成字符
    导致的 `bad_config`（并行例）。修正为官方 `t033_stack`（含 T025 编译器环境）+ helper 归一化 KB id 后，
    同一套用例在锁下 `6 passed in 51.63s`（§4.1）；知识摄取仍走 `support/t055` 的真实 maintenance 流程。
- **not_run**：
  - 浏览器/Playwright 交互（拖拽、点击引用后的真实渲染）——测试栈无浏览器，沿用 F04A/T083 边界；页面逻辑由真实字节 +
    Node shim + 静态 marker 证明。
  - 真实厂商 embedding provider（无凭据）；检索缓存细节（T067 已覆盖，T088 不重复大套件）。
  - 「同一 run 第二次 exec 复用已完成 retrieve 输出」的真实冒烟：T088 不产生 waiting/可重复 claim 场景；恢复接缝
    (`Deps.NodeOutputs`) 由 T083 Go 单测与 supervisor 装配覆盖，未在本票重复。
  - `pytest tests/f10` 全量回归：见 §4.4（若未取得可信结果会如实标 not_run/partial）。
- **边界（实现口径）**：
  - 「无结果」指候选集为空（空 KB/文档撤权或删除）。平台检索**没有相关性阈值**：有可读 chunk 时总返回 top-k
    （F10 既有语义，T088 不改变）；因此「与文档无关的 query」在非空 KB 上仍会有 ranked items，测试用候选集为空
    的真实场景验证「空数组 + succeeded」。
  - 平台检索没有 `score` 阈值参数；`score` 是 cosine 分数，只用于服务端/内部路由排序，**不再是节点输出字段**
    （批次复核修复后节点只暴露 `excerpt`）。内部路由的 `items[].score` 是 worker mTLS 数据面，不是公开契约字段。
  - `sources.js` 支持整段数组字段（`node:<id>#/citations`、`#/items`）；**未实现** array item 固定索引选择器
    （票面允许不实现；不做未文档化的隐式索引）。
  - 运行视图表头仍为「分支 / join 来源」（T083 已冻结的 6 列）；retrieve 引用渲染在同一列，未改表头/列数以免
    破坏 T083 页面契约用例。
  - 非契约面（内部路由、node 输出、node data artifact 约定、citation 链接）不写入 `contracts/**`。

## 6. 合并、门禁原始输出与 HEAD

- 分支：`t/t088-retrieve-node`，worktree `E:\Orchara-wt\t088`。
- 开发期 merge：`integration/t083-t091` @ `b343f7b` → 本分支 `39a3099`；@ `f21f09a`（T086 model + T090）→
  `f194aea`；@ `4851c4f`（T085 并发调度器、T087 approval、T089 call_app、T091）→ `a6cdf8e`；@ `1cf7637`
  （T085 skipped-facts 修复 + 各票证据合并）→ `8a13ee2`（均无冲突：T087/T089/T085 已基于含本票 retrieve 的
  集成分支开发）。更早一次冲突解决：`graphNodeTypes`/`hasGraphConstructs`/dispatch/config 复验同时保留 `model`
  与 `retrieve`，画布 palette/labels/positions/panels/export allowlist 同样两者并存，已提交 bundle 由合并后源码
  重建；T083 的 unsupported-type 用例改用后续剩余的未支持类型。
- 本分支最终代码/证据 HEAD：`8a13ee2`（`Merge branch 'integration/t083-t091'`）。本文件与 `PROGRESS.md` 的最终
  钉数 docs 提交在其后；集成分支已由编排者先行合并本分支（`7b1becd`），最终 HEAD 由父再次 merge。
- 门禁（最终合并树 `8a13ee2`，T085/T087/T089/T091 已并入）原始结果：

```text
# 锁下、确认无其它 pytest 进程后（日志 T088/pytest-t088-final2.log）
python -m pytest tests/f04b/test_t088_retrieve.py -q
......                                                                   [100%]
6 passed in 74.49s (0:01:14)

# 合并 T086/T090 的树（f194aea）上已跑过的回归（日志 T088/pytest-t088.log / pytest-t088-2.log）
python -m pytest tests/f04b/test_t083_runview.py -q
7 passed

python -m pytest tests/f04b/test_t088_canvas.py tests/f04b/test_t088_runview.py \
                 tests/f04b/test_t086_model_canvas.py tests/f04b/test_canvas_f04b_assets.py -q
15 passed in 2.38s

# 最终树 a6cdf8e
python -m pytest tests/f04b/test_t088_canvas.py tests/f04b/test_t088_runview.py \
                 tests/f04b/test_canvas_f04b_assets.py -q
11 passed in 2.28s

go build ./...          # exit 0
go vet ./...            # exit 0
go test ./...           # 全部 ok，exit 0
gofmt -l internal cmd tools   # 无输出
python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
node tests/f04b/canvas_t088.test.mjs    -> PASS 6 canvas T088 module tests
node tests/f04b/canvas_f04b.test.mjs    -> PASS 9 canvas F04B module tests
node tests/f04b/canvas_model.test.mjs   -> PASS 4 canvas T086 model tests
node tests/f04b/callapp_canvas.test.mjs -> PASS 12 call_app canvas module tests
node tests/f04b/t087_canvas.test.mjs    -> ok 1..6, exit 0
node tests/f04b/runview.test.mjs        -> PASS 6 runview tests
node tests/f04b/runview_t088.test.mjs   -> PASS 6 runview T088 tests
```
- **未跑**：`pytest tests/f10` 全量（原因见 §4.4/§5）。

### 6.1 批次复核修复（2026-11-06）：节点输出收窄

批次两轴复核发现节点 data 输出含完整 chunk `text` 与向量 `score`，与 R3（短摘要）/N-R10
（不暴露 chunk body/向量分数）冲突。修复在收尾分支 `t/batch-review-fixes` 完成（`6a3195a`）：

- `retrieveOutputDocument`：items 改为 `{chunk_id,document_id,document_revision,document_state,
  chunk_no,span,excerpt,citation}`，citations 改为 `{chunk_id,document_id,document_revision,span,
  excerpt}`；`excerpt` = 正文前 500 字符（按 rune 截断）；**不再输出 `text`/`score`**。
  内部 worker 检索路由保持 `text/score`（服务端排序/合并用，worker mTLS 数据面，不是节点数据），
  测试用单独的 `INTERNAL_ITEM_KEYS` 钉住，避免把两个面混为一谈。
- 消费者同步：`runview.js` 只读 `citation.excerpt`（新增“只有 text 的旧 artifact 不渲染正文”用例）；
  画布 `retrieveOutputSchema` 不再提供 `text`/`score`，bundle 由 `npm ci --offline && npm run build`
  重建提交；`reports/review/BATCH-REVIEW-FIXES.md` 记录 finding→fix→证据。

收尾分支合并 `refs/heads/integration/t083-t091`（`93ddc6b`，含 T091）后的门禁：

```text
python -m pytest tests/f04b/test_t088_retrieve.py \
                 tests/f04b/test_t083_runview.py \
                 tests/f04b/test_t086_model_node.py -q
..................                                                       [100%]
18 passed in 178.05s (0:02:58)          # 全局 pytest 锁；真实 PG+mTLS+supervisor+知识摄取

python -m pytest tests/f04b/test_t088_canvas.py tests/f04b/test_t088_runview.py \
                 tests/f04b/test_canvas_f04b_assets.py tests/f08/test_t084_corpus.py \
                 tests/f04b/test_t091_canvas.py tests/f04b/test_t091_runview.py -q
21 passed in 3.37s

python -m pytest tests/f04b/test_t091_ten_node_workflow.py -q
8 passed in 144.09s (0:02:24)           # T091 真实栈回归（approval/model/join 路径经过本次重构）

go build ./... ; go vet ./... ; go test ./...     # exit 0
gofmt -l internal cmd tools                       # 无输出
python tools/sync_spec.py --check                 # no drift
node --check apps/web/src/assets/runview.js       # exit 0
```

Go 侧新增回归 `TestRetrieveOutputExcerptsLongChunksWithoutTheVectorScore`（长正文只留 500 rune、
无 score/text）与既有 `TestGraphRunRetrievePublishesCitationsAndPassesThemDownstream`（key 集合 +
`excerpt == "north 1234"`）在 `go test ./internal/presetexec` 中通过。

## 7. 变更文件

新增：
- `internal/api/retrieval.go`（非契约内部检索路由 + body 校验/错误映射）
- `internal/knowledge/query_bases.go`、`query_bases_test.go`（多 KB 委托 `Query` + ACL 计数 + score/chunk_id 合并）
- `internal/presetexec/retrieve.go`、`retrieve_test.go`（retrieve 节点、config 复验、Client 协议类型、输出投影）
- `internal/workflow/retrieve_test.go`（发布校验接受 retrieve 定义 / 拒绝空 KB 列表）
- `tests/support/t088.py`（真实栈 definitions/ingestion/run/mTLS 直连路由 helper）
- `tests/f04b/test_t088_retrieve.py`（6 个真实栈验收用例）
- `tests/f04b/test_t088_canvas.py`、`canvas_t088.test.mjs`（画布 retrieve 面板/模板/导出/来源/素材）
- `tests/f04b/test_t088_runview.py`、`runview_t088.test.mjs`（运行视图引用链接/不可用/码提示）
- `reports/f04b/T088-retrieve-node.md`（本文件）

修改：
- `internal/presetexec/graphplan.go`（`graphNodeTypes`/`hasGraphConstructs`/retrieve config 复验；移除
  `node_unsupported_retrieve`）、`graphplan_test.go`
- `internal/presetexec/graphrun.go`（dispatch 加 retrieve）、`presetexec.go`（Client 接口 + `Retrieve` 方法）
- `internal/presetexec/presetexec_test.go`（fake retrieve seam）
- `internal/execution/runartifacts.go`（`RunOwner`：live attempt → runs.owner_id 的可信身份读）
- `internal/api/internal.go`（注册内部路由）
- `cmd/supervisor/client.go`（worker mTLS 的 `Retrieve` 实现）
- `apps/web/canvas/src/lib/dsl.js`（palette/default config/`retrieveOutputSchema`/默认位置）、
  `lib/export.js`（allowlist）、`main.js`（retrieve 面板 + KB 目录加载）
- `apps/web/src/assets/canvas.js`（重新构建产物）
- `apps/web/src/assets/runview.js`、`apps/web/src/debug.html`（retrieve 引用链接/不可用提示/样式）
- `tests/f04b/canvas_f04b.test.mjs`（T083 export 用例的「未知节点类型」改为真正的未知类型 `mystery`：
  retrieve 已是已知类型）
- `PROGRESS.md`

无迁移；`docs/spec/**`、`contracts/**` 未改（`sync_spec --check` no drift）。
