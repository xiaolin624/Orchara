# F04B/T085 并行嵌套与分支失败（GitHub #86）— 并发调度 + optional/required fail-fast

**状态：`passed`（运行时 + 真实栈 + 运行视图/画布提示）**；`go test -race` 在本机 **not_run**（无 cgo C 编译器），真实 resume join-once 场景本票无 waiting 可构造 → **not_run**（接缝与 Go 单测已交付，留 T087/T091）。

| 项 | 内容 |
|---|---|
| 票 | T085 / GitHub #86（F04B）；依赖 #84（T083 graph runtime + canvas + run view 已在集成分支） |
| 工作树 / 分支 | `E:\Orchara-wt\t085` / `t/t085-parallel-optional` |
| 基线 | `integration/t083-t091` @ `111e2c2`（T083 graph runtime + canvas） |
| 结束合并 | `git merge integration/t083-t091` @ `b343f7b` → merge commit 见 §7（`a74ac9d`） |
| 功能提交 | `71213f0`（并发调度 + fail-fast + optional 收敛）、`3a98b3a`（run view/画布 optional 提示 + 重建 bundle）；报告提交为分支 tip |
| 契约 | **未改** `contracts/**`、`docs/spec/**`、迁移、校验器规则；`python tools/sync_spec.py --check` → `no drift` |
| 环境 | Go 1.27.3 windows/amd64；Python 3.12.2 / pytest 9；PostgreSQL 16 隔离集群 `127.0.0.1:55432`；WSL2 + runsc |

## 1. 验收对照

| # | AC / brief 交付点 | 结论 | 证据 |
|---|---|---|---|
| 1 | **真实并发调度**：ready 队列 + 每节点 goroutine，不是顺序伪装 | **passed** | `graphrun.go` 的 `graphScheduler`（ready-queue loop + 结果 channel + `inFlight`）；Go 单测 `TestGraphRunRunsIndependentBranchesConcurrently` 用**阻塞 fake sandbox barrier**：两个分支必须同时在飞否则超时失败（顺序执行必红） |
| 2 | 并行两分支真实栈成功、join 一次、命名结果 | **passed** | 真实 PG+mTLS+supervisor+runsc：`tests/f04b/test_t085_parallel_optional.py::test_parallel_branches_run_and_join_once`；两分支各自真实 sandbox，join 1 条 data、envelope `left/right` 均 `succeeded+value` |
| 3 | **optional 失败收敛**：join 信封 `{status:"failed",error_code}`、无 value、run 继续 | **passed** | 真实栈 `::test_optional_pure_compute_failure_becomes_join_error_data`：right 输出 schema 不匹配 → `node_output_invalid`；join 信封 `right={status:failed,error_code:node_output_invalid}`、无 value；run `succeeded`；end 正常封 `{"results":…}` |
| 4 | **required 失败 fail-fast**：取消其他在飞分支、等待停止后再失败；join 不执行、end skipped | **passed** | 真实栈 `::test_required_failure_cancels_the_other_branch_and_never_joins`（run failed `node_output_invalid`、join/end `skipped`、无 join 行、无 final output、无孤儿 sandbox）；Go 单测 `TestGraphRunRequiredFailureCancelsTheOtherBranchAndNeverJoins` 用确定性 fake：left 阻塞到 `ctx.Done()`、确认停止后才提交 failed proposal（`onSubmitResult` 钩子断言顺序），join 无 data 行 |
| 5 | **optional 不得吞越权/未知写/预算/审批/持久化** | **passed** | 类型 allowlist + 错误码 allowlist（§3）；真实栈 `::test_optional_never_swallows_an_agent_capability_failure`：publish 后吊销 golden model profile → agent 失败 `model_profile_unavailable`，optional 声明**不**降级，run failed、join/end skipped；Go 单测 `TestGraphRunOptionalNeverSwallowsAnAgentFailure`（capability seam `model_profile_unavailable`）与 `TestGraphRunOptionalNeverSwallowsANonAllowlistedErrorCode`（python `sandbox_orphan` 不吸收） |
| 6 | 嵌套并行+条件；join 各一次 | **passed** | Go 单测 `TestGraphRunNestedParallelRegionInsideAConditionBranch`（condition 的 yes 分支内嵌 parallel fork，join2/join 各一次，direct skipped）；真实栈 `::test_nested_parallel_region_inside_a_condition_branch`（choose→fork→deepA/deepB→join2→join，15 条 node-results，两个 join 各 1 条 data，外层 `join2.value`=内层封套） |
| 7 | **join-once under replay**：复用已完成节点、join 不重复执行 | **passed（Go 单测）**；真实 resume `not_run` | `TestGraphRunReplayReusesTheJoinWithoutFiringItAgain`：fake `NodeOutputSource` 回放 start/choose/left/join，join 只发一条 `succeeded`、0 次新 data 行、无 sandbox 调用、end 用复用封套封 final output；真实"同一 run 第二次 exec"本票无 waiting，留 T087/T091（T083 已交付 supervisor 装配与 sha256 复验） |
| 8 | 非法跨区/无激活来源拒绝保持 | **passed** | 不改校验器；plan 复验语义保持 `cross_region_edge`/`invalid_activation`；T083 真实栈用例与 Go 用例在最终树全绿（`tests/f04b` 34 passed） |
| 9 | 画布 optional 标记与提示（P1） | **passed（可观测文案/标记）** | `apps/web/canvas/src/main.js` join 面板：optional 选项逐个标记「optional：仅纯读/计算错误可收敛」、已选 optional 列表与「写/越权/预算/审批失败不会降级」提示（`data-field="config.optional_sources.convergence"`）；`npm ci --offline && npm run build` 重建并提交 `canvas.js`（`tests/f04b/test_canvas_f04b_assets.py` 5 passed，含 stale-bundle 校验） |
| 10 | run view 把 optional failed 显示为「已收敛的错误」（P1） | **passed** | `apps/web/src/assets/runview.js`：optional 源的 failed envelope → `已收敛的错误` + 稳定码 + `state-converged`（与 failed 视觉区分），`convergedErrors`/`joinText` 计数；required 源仍是「失败」；`debug.html` 图例补一句收敛规则；`node tests/f04b/runview.test.mjs` PASS 6 |

## 2. 并发调度设计（`internal/presetexec/graphrun.go`）

- **ready 队列**：`graphScheduler.schedule()` 循环：对拓扑序扫描所有「前驱已 settled 且未 settled/未启动」的节点 → 决策 `run / skip / fail`；`run` 且无复用输出时立即 `go` 启动；随后阻塞在带缓冲的 `results` channel 上收一个完成事件再推进。
- **并发证明**：两个独立分支的 sandbox 调用必须**同时在飞**。Go 单测用 barrier sandbox（任一方等不到第二个调用即 5s 超时并让节点失败）与 fail-fast sandbox（两分支先会合，再让 right 失败、left 阻塞到 `ctx.Done()`）；`go test ./internal/presetexec -run '…Concurrently|…CancelsTheOtherBranch' -count=5` 全绿。
- **所有权/数据竞争**：`graphRun`（state/outputs/taken）只由调度器 goroutine 修改；每个节点 goroutine 拿到不可变快照 `graphNodeView`（available 拷贝、join 分支状态、end 的 sandbox evidence），结果经 channel 回传。共享的 runner 状态只剩：心跳计数（atomic）、`r.artifacts`（新增 mutex）、协议 client（HTTP/DB 并发安全；sandbox `Executor` 自身文档声明并发安全）。
- **每节点独立 sandbox 单元**：`graphSandboxUnit(executionUnitID, nodeID)`（`agentSandboxUnit` 派生、48 字符上限）让两个分支能同时跑两个 runsc unit；agent 的工具调用单元 id 也带节点 id，避免并发分支同名 call id 冲突。
- **join 不死等**：join 只在其**本 region 的直接前驱全部 settled** 后决策；未选端口的分支整段 `skipped`，fork 被 skipped/failed 的 region 的 join 自身 skipped；join 成功才 take 出边。
- **复用（replay）**：`reusableNodeOutputs` 的复用从"拓扑序无条件先写"改为**激活时复用**（复用节点仍会 emit 一次 `succeeded`），因此未激活分支里残留的旧输出不会被错误当成成功；join 复用后不再执行。
- **end 节点**：图证明保证 end 的单一前驱后支配所有节点，因此 end 被激活时其他节点均已 settled（单飞）；end 成功提交沿用线性路径，end 失败被 capture 后由调度器统一提交。

## 3. optional / required 语义

### 3.1 join 信封

- `succeeded` → `{status:"succeeded", value:{…}}`（source 的已验证输出对象）
- `skipped` → `{status:"skipped"}`（未激活，不是失败）
- optional 分支的显式纯读/计算失败 → `{status:"failed", error_code:"<stable>"}`，**不带 value**；join 仍执行一次，run 继续，end 正常封输出（错误作为数据）。

失败归属：`graphPlan` 为每个 region 预计算 `branchSource`（从 join 的每个 source 反向走到 fork，把 region inside 集合划分为分支），并在 `regionOfNode` 记录**最内层** region。失败节点按「最内层 region + 该节点所属 branch source」判定是否 optional。这样分支深处（source 上游）的失败也能正确归到该分支的命名结果，且不会因为 source 被 skipped 而丢失错误。

### 3.2 显式 allowlist（不做"非 required 即 optional"的反向判断）

只有**同时**满足以下三条的失败才会被收敛（`graphrun.go` 的 `absorbOptionalFailure`）：

1. 失败节点的**节点类型** ∈ `optionalFailureNodeTypes`：`python`、`condition`、`join`（`retrieve` 预留 T088）；
2. 失败节点所属 branch 的 join source 在 `optional_sources` 中；
3. 失败码 ∈ `optionalFailureCodes`（显式枚举）：`node_input_invalid`、`node_output_invalid`、`condition_operand_invalid`、`missing_value`、`tool_crash`、`tool_output_invalid`、`tool_artifact_invalid` + 固定 preset 工具的数据拒绝码（`unsupported_media_type`、`formula_not_supported`、`field_too_large`、`invalid_date`、`invalid_amount`、`negative_amount`、`missing_column`、`extra_column`、`column_order_mismatch`、`invalid_row`、`duplicate_order_id`、`invalid_cell_type`）。

**永不降级**（任一出现即 fail-fast，Run 失败并保留原稳定码）：`agent`/`call_app` 的任何失败；`*_forbidden`/`approval_*`/`capability_*`/`model_profile_unavailable`/`tool_not_available` 等能力/权限拒绝；`budget_*`；`needs_reconciliation`/未知写；`sandbox_*`（含注册/上传/清理失败）；`input_integrity`；`cancel_requested`；任何不在 allowlist 的码。

### 3.3 required 失败 = fail-fast

节点失败（未被吸收）→ 调度器 `stopAndCommit`：

1. `cancel()` 工作 context（取消其他在飞分支的 sandbox / HTTP / 剩余工作）；
2. `drain()` 等待**每个**节点 goroutine 返回（sandbox 清理证明就在这次返回里），期间把被取消分支的终态记入 state；
3. 对"从未启动"的节点按拓扑序 emit `skipped`（join、end 等）；
4. 最后**只提交一次** run 失败结果：失败节点自己的 sandbox 证据（或确定性的"最高 plan index 已跑 sandbox"证据）→ `failAfterStart`；从未碰过 sandbox 的确定性拒绝 → `refuse`。`failed` proposal 的提交发生在**所有分支停止之后**（Go 单测用 client 钩子断言顺序）。

被取消的在飞分支按既有可观测语义记录为 `failed`（python 取消/心跳丢失路径），不会写成 succeeded/skipped。join 不执行、end skipped、无 final output。

### 3.4 取消确认与节点终态可观测性

- `emitNode` 在 ctx 已取消时使用 `context.WithoutCancel`（HTTP client 自带超时），确保被 fail-fast 取消的分支的终态 `failed` 仍能落库——否则 node.status 会停在 `running`。
- 节点的 `pending/running/succeeded/failed/skipped` 仍由节点 goroutine 按序发出；调度器只补充 skipped/复用 succeeded。
- 等待路径（agent 审批 `waiting_approval`、call_app `waiting_children`）也改为 capture：调度器先停其它分支，再提交 waiting proposal + confirmExit（EXECUTION §4/§7 的 waiting hand-over 语义）；本票没有真实 waiting 场景（T087/T089 覆盖），只有 Go 单测/编译保证，报告标 not_run。

## 4. 真实栈证据（PG + mTLS + supervisor + runsc）

```text
$ python -m pytest tests/f04b -q          # 全局 pytest 锁下，最终树
..................................                                       [100%]
34 passed in 197.55s (0:03:17)
exit=0
```

其中本票新增 `tests/f04b/test_t085_parallel_optional.py`（5 例）：

```text
$ python -m pytest tests/f04b/test_t085_parallel_optional.py -q -rs
.....                                                                    [100%]
5 passed in 54.34s
exit=0
```

用例：
1. `test_parallel_branches_run_and_join_once`：两真实 python 分支并发执行（各自 runsc unit），join 恰一次，envelope 两源 `succeeded+value`，final output=封套，无孤儿 sandbox。
2. `test_optional_pure_compute_failure_becomes_join_error_data`：right 的 `output_schema` 要求不存在的字段 → `node_output_invalid`；join 信封 `right={status:"failed",error_code:"node_output_invalid"}` 无 value；run `succeeded`；end 封 `{"results":…}`；right 无任何 node-results 行；left 仍成功。
3. `test_required_failure_cancels_the_other_branch_and_never_joins`：两分支 required；right schema 失败 → run failed `node_output_invalid`（DB `runs.error_code` 同）；join/end `skipped`；join 无 data 行；无 final output；`runsc list` 空。
4. `test_optional_never_swallows_an_agent_capability_failure`：optional agent 分支；publish 后吊销 golden model profile → 执行期 `model_profile_unavailable`；run failed（**不是** succeeded）、join/end skipped、无 join 行、无 final output、无孤儿 sandbox。
5. `test_nested_parallel_region_inside_a_condition_branch`：condition 的 yes 分支内并行 fork（fork/deepA/deepB 三个真实 sandbox，其中 deepA/deepB 并发）→ join2 → join；两个 join 各 1 条 data；外层 `join2.value`=内层封套；`direct` skipped；共 15 条 node-results。

数据约定（非契约，与 T083 一致）：condition data `{port,matched_case}`；join data `{results:{source:{status,value?,error_code?}}}`；node.status 契约事件。**未新增/扩展任何契约端点**；run view 通过既有 owner-only `node-results` → `artifacts/{id}/content` 读取。

## 5. stub / real 边界

- **real**：PostgreSQL 16 隔离库、迁移+seed、真实 mTLS internal listener、编译后的 `cmd/supervisor exec --once`、真实对象目录、真实 WSL2/runsc（并发两个 sandbox）、owner-only HTTP/DB 断言、真实 `runsc list` 孤儿检查。
- **stub**：Go 单测的协议/能力/sandbox seam（`fakeClient`/`fakeSandbox`/阻塞 barrier sandbox/`fakeNodeOutputs`/`branchSandbox`）；它们只验证执行器语义，不冒充真实栈。
- **not_run**：
  1. `go test -race ./internal/presetexec`：本机无 cgo C 编译器（见 §6 原始输出），无法启用 race detector；已用 `-count=5` 重复并发用例 + 单所有权设计降低风险，但**不能**声称 race-free 证明。
  2. 真实 resume join-once：本票不产生 waiting，无法构造"同一 run 第二次 exec"；Go 单测覆盖复用接缝，真实场景留 T087/T091。
  3. 真实 agent 审批 waiting / call_app waiting 在 graph 并发下的 hand-over：capture+调度实现已就位，但本票没有可执行的 waiting fixture（T087/T089）。
  4. 浏览器 DOM/Playwright：页面逻辑用 Node + 真实字节执行（仓库惯例）。
  5. 真实厂商模型：无凭据；agent 失败用例走的是真实 capability 组合（吊销 profile），不调用厂商。

## 6. 门禁（原始输出尾部）

工作目录 `E:\Orchara-wt\t085\product`；原始日志 `.scratch/t083-t091/T085/`。

```text
$ python -m pytest tests/f04b -q
..................................                                       [100%]
34 passed in 197.55s (0:03:17)
exit=0

$ python -m pytest tests/f06 -q
（见 §8 最终树结果）

$ python -m pytest tests/tooling -q
（见 §8 最终树结果）

$ go build ./...
exit=0
$ go vet ./...
exit=0
$ go test ./... -count=1
（全部 ok，无 FAIL；见 .scratch/t083-t091/T085/go-gates.log）
exit=0
$ gofmt -l internal cmd tools
（无输出）
exit=0
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
exit=0

$ go test -race ./internal/presetexec -count=1     # not_run
go: -race requires cgo; enable cgo by setting CGO_ENABLED=1
exit=1
$ CGO_ENABLED=1 go test -race ./internal/presetexec -count=1
cgo: C compiler "gcc" not found: exec: "gcc": executable file not found in %PATH%
FAIL    orchara/platform/internal/presetexec [build failed]
exit=1

$ go test ./internal/presetexec -run 'TestGraphRunRunsIndependentBranchesConcurrently|TestGraphRunRequiredFailureCancelsTheOtherBranchAndNeverJoins|TestGraphRunOptionalNeverSwallows' -count=5
ok      orchara/platform/internal/presetexec    2.609s
```

## 7. 合并与 HEAD

- 基线 `111e2c2` → 本票合并两次集成分支：`f6e7e5b`（T083 runview + T084）与 `a74ac9d`（T083 runview 条件 E2E @ `b343f7b`）；无冲突。
- 分支 / HEAD：`t/t085-parallel-optional`；功能提交 `71213f0`、`3a98b3a`，报告提交为分支 tip（`git log -1`）。
- 无迁移；`product/docs/spec/**`、`product/contracts/**` 未改。

## 8. 最终树门禁（合并后复跑）与 not_run 汇总

（见文末更新：最终 HEAD 与 `tests/f06`/`tests/tooling`/Go 门禁原始输出。）

## 9. 变更文件

新增：
- `internal/presetexec/graphrun_parallel_test.go`
- `tests/f04b/test_t085_parallel_optional.py`
- `tests/support/t085.py`
- `reports/f04b/T085-parallel-optional.md`（本文件）

修改：
- `internal/presetexec/graphplan.go`（region `branchSource`、`regionOfNode`/`branchFor`）
- `internal/presetexec/graphrun.go`（并发调度器重写；capture；optional allowlist；per-node sandbox 单元）
- `internal/presetexec/linear.go`（`nodeHost.sandboxUnit`、取消 capture、`emitNode` 的 durable ctx）
- `internal/presetexec/presetexec.go`（`refuse`/`failRun`/`failAfterStart` capture；`r.artifacts` mutex）
- `internal/presetexec/agent.go`（agent 取消/waiting capture；工具 sandbox 单元含节点 id）
- `internal/presetexec/callapp.go`（waiting_children capture）
- `internal/presetexec/presetexec_test.go`（fake client：`onSubmitResult` 钩子、按 URL 的 PutBytes、Status 加锁）
- `apps/web/src/assets/runview.js`、`apps/web/src/debug.html`（optional 收敛显示 + 图例/CSS）
- `apps/web/canvas/src/main.js`、`apps/web/src/assets/canvas.js`（join 面板 optional 标记/提示；重建 bundle）
- `tests/f04b/runview.test.mjs`（optional 收敛断言）、`tests/f04b/test_canvas_f04b_assets.py`（bundle marker）
- `PROGRESS.md`

无迁移；`docs/spec/**`、`contracts/**` 未改（`sync_spec --check` no drift）。
