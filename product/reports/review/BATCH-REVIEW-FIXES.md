# 批次复核修复报告 — `integration/t083-t091`（T083–T091 / GitHub #84–#92）

- 任务：按 `E:\Orchara\.scratch\t083-t091\briefs\REVIEW-FIXES.md` 修复两轴复核
  （`.scratch/t083-t091/REVIEW-FINDINGS.md`）在批次收尾树的 P1/文档项。**不做大重构**；
  架构性 judgement 项记录在 §3。
- 分支：`t/batch-review-fixes`（worktree `E:\Orchara-wt\review-fixes`），基线
  `integration/t083-t091` @ `bd4d791`；收尾前 merge `refs/heads/integration/t083-t091`（见 §4）。
- 约束：未改 `contracts/**`、`docs/spec/**`、迁移、凭据；`sync_spec --check` no drift。
  行为改动仅 T088 输出收窄与 C.1–C.3 的等价重构；没有放宽/跳过任何断言，没有重写既有证据。

## 1 逐条映射（finding → fix → 回归）

| # | 复核发现 | 级别 | 修复 | 证据 |
|---|---|---|---|---|
| A1 | `PROGRESS.md` T084 写“门禁 pytest tests/f08”，未记录 final-tree 全量是 `not_run`（证据诚实性硬规则 #5） | P1 | 改为明确 final-tree 全量 `not_run`（共享 WSL/runsc 栈争用），并写出实际分套件数字：`test_t084_routing_eval.py` 1 passed in 84.91s、`test_t084_pages.py` 2 passed in 10.34s、`test_t084_corpus.py`+`test_t084_states.py` 7 passed in 46.93s；第一次合并树完整套件 79 passed in 948.05s 保真引用 | `PROGRESS.md` T084 小节 |
| A2 | T086/T083 小节把门禁写成“原始 tail 见报告 §6”，没有实际结果 | P1 | T086 写入 9 passed in 44.74s / 18 passed in 127.72s / 9 passed in 1.06s + not_run 说明；T083 写入 14 passed in 110.23s、f04a 36 passed in 85.31s、tooling 21 passed、go exit 0、sync no drift、全量 f06 `not_run` 与当次 gofmt 原始事实 | `PROGRESS.md` T083/T086 小节 |
| A3 | T087“审批过期后终止”缺自动 sweeper，却被写成完整 passed | P1 | T087 报告/PROGRESS 状态改为 **`partial`**：无后台过期扫描；只有 decision 路径观察过期（409 `approval_expired`，Run 随即 failed）；等待中的 Run 不会仅因时间终止 | `reports/f04b/T087-approval-node.md`（状态行、AC 表、§5）、`PROGRESS.md` T087 小节 |
| B1 | 生产图执行是 Go，spec 写“LangGraph 负责图执行”，无 ADR 记录偏离 | P1 | 新增 ADR 0034：记录 Go `internal/presetexec` 是生产运行时、Python LangGraph 是独立证明、非契约节点输出/内部路由、以及 T084/T085/T087/T088 的已知限制 | `docs/adr/0034-f04b-go-graph-runtime-and-noncontract-node-outputs.md` |
| C1 | `CodeNodeUnsupportedRetrieve/Approval` + switch 分支已不可达（Speculative Generality） | P1 | 删除两个常量与按类型 switch；未知类型统一 `tool_unsupported_graph`；顶部注释与 runview 中两条已不可能出现的 unsupported 提示一并收敛 | `internal/presetexec/graphplan.go`、`apps/web/src/assets/runview.js`；`go test ./internal/presetexec` |
| C2 | `failGraphNodeBeforeSandbox`/`failGraphNodeAfterSandbox` 函数体相同（Middle Man） | P1 | 合并为单一 `failGraphNode` 并注释说明两者行为相同；37 个调用点改名 | `internal/presetexec/{graphrun,modelnode,approval}.go`；`go build/vet/test` |
| C3 | 5 处节点执行器重复 `canonicalNodeDocument → uploadNodeArtifact → RecordNodeResults → emitNode(succeeded)` | P1 | 抽出 `publishNodeData(ctx,node,output,label,document,operation)`；condition/join/model/retrieve/approval 全部调用；label、错误码、step 顺序不变（失败时仍 `failGraphNode` + 同码） | `internal/presetexec/graphrun.go`（helper）+ 5 个节点文件；`go test ./internal/presetexec ./internal/workflow` |
| C4 | T088 节点输出含完整 `text` 与向量 `score`，违反 R3/N-R10（N-R10：不把 chunk body/向量分数暴露为平台字段；R3 要求短摘要） | P1 | 节点输出收窄：items 改 `{chunk_id,document_id,document_revision,document_state,chunk_no,span,excerpt,citation}`，citations 改 `{chunk_id,document_id,document_revision,span,excerpt}`；`excerpt` = 正文前 500 字符（按 rune，不切断多字节字符）；**无完整 text、无 score**。新增 Go 单测证明长文截断与 score 不出现；真实栈断言 `excerpt == 封印 body[:500]` 且无 `text`/`score`；内部 worker 路由仍保留 text/score 仅用于服务端排序（非节点数据） | `internal/presetexec/retrieve.go`、`retrieve_test.go`、`tests/support/t088.py`、`tests/f04b/test_t088_retrieve.py`、`apps/web/canvas/src/lib/dsl.js`（+重建 bundle）、`tests/f04b/{canvas_t088.test.mjs,runview_t088.test.mjs}`、`apps/web/src/assets/runview.js`、`reports/f04b/T088-retrieve-node.md` |
| C5 | `runview.js:508` 的 `*/function` 缺换行；`tests/f08/routing/corpus.py` 重复 `auto_dispatch` guard | P1 | 补换行；删除第二条永远被第一条更强断言蕴含的 guard，并注释保留语义 | `apps/web/src/assets/runview.js`、`tests/f08/routing/corpus.py`；`node --check`、`pytest tests/f08/test_t084_corpus.py` |
| C6（可选） | `tests/support/t088.py::node_states` 重复实现 | P2 | 改为委托 `t083.node_states`（与 `t089.node_states` 同一公共 helper）；不做大规模测试去重 | `tests/support/t088.py` |

## 2 未做项（judgement / 架构）与理由

以下复核项按 brief“不要大重构”的原则**保留并记录**，不计为已修复：

1. **新增节点类型要改 7 处（Shotgun Surgery）**：节点类型闭集分布在 plan 类型表、dispatch、
   config 复验、画布 palette/panel/export、run view。真正的解法是节点类型注册表（类型→解析器/
   执行器/画布元数据），会动 `graphplan`/`graphrun` 的公共结构并影响 10 类节点与全部画布测试。
   本批是收尾修复，不做；已在 ADR 0034 Consequences 记录为后续重构项。
2. **测试 helper 去重**（`_definition` 在 t083/t085/t089 重复；4 个 canvas 测试重复同一套
   node --check/marker/rebuild）：只做了 brief 明确允许的 `node_states` 复用；其余保留。理由是
   这些 helper 的差异（fixture 形状、真实栈依赖）比重复本身更重要，统一会扩大回归面。
3. **`approval.OperationID != ""` 作为 node approval 判别（Primitive Obsession）**：类型化
   approval kind 需要迁移/契约枚举或新领域类型，超出本批“不改迁移/契约”的约束；保留并记录。
4. **发布校验拒绝非纯读/计算的 `optional_sources`**（复核 P1 #5：agent/call_app/model 可被发布为
   optional，但运行时永不收敛）：需要同时改 Go validator、Python 校验器与 P05 corpus 哈希 pin
   （corpus 重建），属行为+契约校验改动，且 brief 未列入本批范围。当前行为是 **fail closed**
   （运行时不会把写/能力/未知错误降级为 optional 成功），画布有提示；已在 ADR 0034 §4 记录为
   已知限制与后续发布期拒绝项。
5. **runview.js 按类型增长字段/渲染（Divergent Change）与 retrieve `top_k` 三层校验**：保留。
   `top_k` 的三层校验是防御性的（契约→manifest 复验→执行期），不是死代码；runview 的类型分支
   在本批只做了 T088 字段收窄与换行修复，渲染重构不在范围内。
6. **T091**：其分支 `t/t091-ten-node-graph` 在收尾前由编排者合并进集成分支（`93ddc6b`，
   merge `344c139`），本分支已 merge 并重跑 T091 的真实栈 acceptance（8 passed in 144.09s）
   作为本次重构的回归；T091 自身的 `pytest tests/f04b` 89 passed / `tests/f06` 173 passed
   证据在 `reports/f04b/T091-ten-node-workflow.md` 与 PROGRESS。本报告不冒充重跑 T091 的全量套件。

## 3 门禁与回归证据

### 3.1 本分支最终树（合并集成分支后）

（见 §3.3 的最终命令与原始 tail。）

### 3.2 行为改动的最小证据

- **T088 收窄（Go seam）**：`TestRetrieveOutputExcerptsLongChunksWithoutTheVectorScore`
  （长正文只留 500 rune、无 `score`/`text`）与既有
  `TestGraphRunRetrievePublishesCitationsAndPassesThemDownstream`（items/citations key 集合、
  `excerpt == "north 1234"`）。
- **T088 收窄（真实栈）**：`tests/f04b/test_t088_retrieve.py` 断言 `item["excerpt"] == db_body[:500]`、
  `"text" not in item and "score" not in item`，并保留 owner-only citation 路由返回完整正文的
  独立真值断言。
- **T088 收窄（页面/画布）**：runview 只读 `citation.excerpt`（新增“只带 text 的旧 artifact 不渲染
  正文”用例）；画布模板不再提供 `text`/`score` 键（canvas_t088 断言缺失），bundle 由
  `npm ci --offline && npm run build` 从源码重建并提交。
- **C1–C3 等价重构**：`go test ./internal/presetexec ./internal/workflow` 全绿；无行为断言改动
  （既有 graph/approval/model/retrieve 单测全部通过）。

### 3.3 最终门禁（原始 tail）

在合并 `refs/heads/integration/t083-t091` @ `93ddc6b`（含 T091 `344c139`）后的最终树、全局
pytest 锁下（无其它 pytest 进程时取得/释放 `E:\Orchara\.scratch\t083-t091\pytest.lock`）：

```text
# 受影响真实栈文件一次跑（T088 retrieve + T083 runview + T086 model）
$ python -m pytest tests/f04b/test_t088_retrieve.py \
                   tests/f04b/test_t083_runview.py \
                   tests/f04b/test_t086_model_node.py -q
..................                                                       [100%]
18 passed in 178.05s (0:02:58)

# 合并 T091 后的集成回归（其 acceptance 图经过本次 publishNodeData/失败 helper 重构）
$ python -m pytest tests/f04b/test_t091_ten_node_workflow.py -q
........                                                                 [100%]
8 passed in 144.09s (0:02:24)

# 无真实栈的受影响模块 / 语料（画布 bundle 逐字节重建校验在 node_modules 存在时执行）
$ python -m pytest tests/f04b/test_t088_canvas.py tests/f04b/test_t088_runview.py \
                   tests/f04b/test_canvas_f04b_assets.py tests/f08/test_t084_corpus.py \
                   tests/f04b/test_t091_canvas.py tests/f04b/test_t091_runview.py -q
21 passed in 3.37s

$ go build ./...          # exit 0（无输出）
$ go vet ./...            # exit 0（无输出）
$ go test ./...           # exit 0
$ gofmt -l internal cmd tools   # 无输出
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
$ node --check apps/web/src/assets/runview.js   # exit 0
```

Go 单测（`go test ./internal/presetexec ./internal/workflow`）含新增
`TestRetrieveOutputExcerptsLongChunksWithoutTheVectorScore`，以及全部既有 graph/approval/model/
retrieve/condition/join 用例；无断言被放宽或跳过。原始日志：
`E:\Orchara\.scratch\t083-t091\REVIEW-FIXES\`（`pytest-affected-final.log`、
`pytest-t091-regression.log`、`go-*.log`、`gofmt.log`、`sync-spec.log`、`node-check.log`）。

## 4 合并与 HEAD

- 收尾分支：`t/batch-review-fixes`（worktree `E:\Orchara-wt\review-fixes`），基线
  `integration/t083-t091` @ `bd4d791`。
- 提交：`50d095f`（C.1–C.3 重构）→ `6a3195a`（T088 输出收窄 + C.5/C.6）→
  `fbac878`（PROGRESS 诚实性/ADR 0034/T087 partial/T088 报告与测试修正）。
- 收尾 merge：`git merge refs/heads/integration/t083-t091` @ `93ddc6b`（merge T091
  `t/t091-ten-node-graph` @ `344c139`）→ **`593554c`**。唯一冲突是 `product/PROGRESS.md`
  （T091 小节 vs 批次完成记录）：保留两者（T091 小节在前、批次记录在后），随后补丁 T091 行
  为已合并状态并在合并顺序中加入 `93ddc6b`。`runview.js` 自动合并（T091 的审批行 + 本次
  excerpt 收窄/换行/错误提示）后 `node --check` 与 Node 模块测试均通过。
- 最终代码/证据 HEAD：`d4ffaa8`（含 T091 merge `593554c` 与全部修复提交）；本报告与
  PROGRESS 的钉数文档提交在其后（`git log -1` 为最终 HEAD）。
- 合并后重跑：§3.3 的全部门禁；T091 的十类真实栈 acceptance 8 passed（证明本次重构未破坏其
  approval/model/join 路径）。

## 5 诚实边界

- 本报告只覆盖 brief 的 A/B/C/D 项；§2 的 judgement 项确实是**未修**，不是“等价完成”。
- T084 的 final-tree 全量 `tests/f08`、T086/T083 的全量 f04b/f06、T088 的全量 `tests/f10`
  仍是 `not_run`（共享真实栈争用），报告/ PROGRESS 已按实际改写。
- T087 是 `partial`（无自动过期 sweeper）；T090 是 `blocked`（无模型凭据/端点/权重/GPU）。
- 真实验收依赖：PostgreSQL 16 隔离集群、编译后的 `cmd/api`/`cmd/supervisor`/`cmd/maintenance`、
  真实 mTLS、真实对象目录、WSL2+runsc、dev-stub 模型；浏览器 DOM 仍未驱动。
