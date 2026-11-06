# F04B/T085 并行嵌套与分支失败（GitHub #86）— 并发调度 + optional/required fail-fast

**状态：`passed`（运行时 + 真实栈 + 运行视图/画布提示）**；`go test -race` 在本机 **not_run**（无 cgo C 编译器）。真实 resume join-once 与 optional 发布校验由第三轮复核补齐（§10），不再是 `not_run` / 延迟项。

| 项 | 内容 |
|---|---|
| 票 | T085 / GitHub #86（F04B）；依赖 #84（T083 graph runtime + canvas + run view 已在集成分支） |
| 工作树 / 分支 | `E:\Orchara-wt\t085` / `t/t085-parallel-optional` |
| 基线 | `integration/t083-t091` @ `111e2c2`（T083 graph runtime + canvas） |
| 结束合并 | `git merge integration/t083-t091` 三次：`f6e7e5b`（T083 runview+T084）、`a74ac9d`（T083 runview 条件 E2E @ `b343f7b`）、`982a1b0`（T086 model node @ `f21f09a`，冲突见 §7） |
| 功能提交 | `71213f0`（并发调度 + fail-fast + optional 收敛）、`3a98b3a`（run view/画布 optional 提示 + 重建 bundle）、`5935afd`（graph agent 视图携带 run inputs + 测试）、`78d09db`（F05 sandbox 竞态：启动失败不再被伪装成租约丢失）；报告提交为分支 tip |
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
| 7 | **join-once under replay**：复用已完成节点、join 不重复执行 | **passed（Go 单测 + 真实两次 exec）** | Go：`TestGraphRunReplayReusesTheJoinWithoutFiringItAgain`（fake `NodeOutputSource`）仍保留为单测。真实进程：T087 的 `test_a_real_resume_reuses_the_join_and_never_fires_it_twice`（`start → left/right(python) → join → approval → end`）第一次 exec 发布 1 条 join data，第二次 exec 恢复后 join 只发复用 `succeeded`、data artifact 逐字段不变、end 用同一封套；见 §10 |
| 8 | 非法跨区/无激活来源拒绝保持 | **passed** | 不改校验器；plan 复验语义保持 `cross_region_edge`/`invalid_activation`；T083 真实栈用例与 Go 用例在最终树全绿（`tests/f04b` 34 passed） |
| 9 | 画布 optional 标记与提示（P1） | **passed（可观测文案/标记）** | `apps/web/canvas/src/main.js` join 面板：optional 选项逐个标记「optional：仅纯读/计算错误可收敛」、已选 optional 列表与「写/越权/预算/审批失败不会降级」提示（`data-field="config.optional_sources.convergence"`）；`npm ci --offline && npm run build` 重建并提交 `canvas.js`（`tests/f04b/test_canvas_f04b_assets.py` 5 passed，含 stale-bundle 校验） |
| 10 | run view 把 optional failed 显示为「已收敛的错误」（P1） | **passed** | `apps/web/src/assets/runview.js`：optional 源的 failed envelope → `已收敛的错误` + 稳定码 + `state-converged`（与 failed 视觉区分），`convergedErrors`/`joinText` 计数；required 源仍是「失败」；`debug.html` 图例补一句收敛规则；`node tests/f04b/runview.test.mjs` PASS 6 |
| 11 | **optional 发布校验**：拒绝非纯读/计算 source（agent/call_app/model 等） | **passed**（第三轮复核补齐，§10） | Go `internal/workflow/validate.go` 与 P05 `probes/p05_dsl/go/validate.go`/`python/validate.py` 在 join source 校验中拒绝非 `python/condition/join/retrieve` 的 `optional_sources`（`bad_join_sources` 定位 `/nodes/<i>/config/optional_sources`）；P05 语料重建（43 例）含 model/agent/call_app 三个负例；真实 `POST /validate` + debug 提交 422 且零 Run/Job：`test_publish_refuses_an_optional_agent_source` |

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
  2. ~~真实 resume join-once：本票不产生 waiting … 留 T087/T091。~~ 已由第三轮复核的真实两次 exec 用例关闭（§10）。
  3. 真实 agent 审批 waiting / call_app waiting 在 graph 并发下的 hand-over：capture+调度实现已就位，由 T087/T089 的真实 waiting 场景覆盖。
  4. 浏览器 DOM/Playwright：页面逻辑用 Node + 真实字节执行（仓库惯例）。
  5. 真实厂商模型：无凭据；agent 失败用例走的是真实 capability 组合（吊销 profile），不调用厂商。

## 6. 门禁（原始输出尾部）

工作目录 `E:\Orchara-wt\t085\product`；原始日志 `.scratch/t083-t091/T085/`（`go-gates.log`、`pytest-*-gate.log`、`pytest-f04b-final.log`）。

```text
$ python -m pytest tests/f04b -q          # 最终树（含 sandbox 修复），全局 pytest 锁
..................................                                       [100%]
34 passed in 162.35s (0:02:42)
exit=0

$ python -m pytest tests/f04b/test_t085_parallel_optional.py -q -rs
.....                                                                    [100%]
5 passed in 54.34s
exit=0

$ python -m pytest tests/f05 -q           # sandbox 修复回归，全局 pytest 锁
........................................................................ [ 48%]
........................................................................ [ 96%]
.....                                                                    [100%]
149 passed in 795.61s (0:13:15)
exit=0

$ python -m pytest tests/f06 -q           # 全局 pytest 锁
FAILED tests/f06/test_t047_context.py::test_budgeted_context_truncates_verbose_text_and_keeps_protected_facts
1 failed, 172 passed in 881.33s (0:14:41)
exit=1
# 该失败是环境性的 sandbox 启动失败被旧 executor 竞态伪装成"租约丢失"（见 §6.1）；
# 修复后单测重跑该用例：
$ python -m pytest tests/f06/test_t047_context.py::test_budgeted_context_truncates_verbose_text_and_keeps_protected_facts -q
1 passed in 25.64s
exit=0

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 6.79s
exit=0

$ go build ./...
exit=0
$ go vet ./...
exit=0
$ go test ./... -count=1
（全部 ok，无 FAIL；原始输出 .scratch/t083-t091/T085/go-gates.log）
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

### 6.1 `tests/f06` 的 1 例失败：sandbox 启动失败被旧竞态伪装（已修）

失败报告（真实栈，`test_parallel_branches_run_and_join_once` 一次、`test_t047_context` 一次，同一根因）：

```text
sandbox: {'sandbox_id': 'exec-ai-72640-05ce4ff2-a83eb8410', 'started': False,
          'exit_code': 0, 'duration_ms': 277, 'cleanup': {'sandbox_list_matches': 0, 'pid': 0, ...}}
error:    the attempt lease was lost while the sandbox ran: ... Post ".../heartbeat": context canceled
```

`started=false`/`pid=0`/277ms 证明 unit 根本没有启动（驱动拒绝或 WSL 启动失败），但 executor 在
`runner.Run` 返回后执行 `cancelRun()`，把仍在飞的 heartbeat HTTP 请求取消掉，该错误被记录成
`controlErr` → 归类为 `ErrHeartbeat`（租约丢失），真实启动错误被掩盖。修复（`78d09db`，
`internal/sandbox/executor.go`）：进程结果返回后先置 `runnerDone`，此后观察到的 heartbeat **错误**视为
"我们自己的取消"而不再记录（迟到的 `CancelRequested` 仍然生效，保持 EXECUTION §6"工具已退出后的迟到取消
仍不得发布成功"）。`internal/sandbox/sandbox_test.go::TestExecutorDoesNotMaskAPreStartRefusalAsALeaseLoss`
用"第二次 heartbeat 阻塞到 ctx 取消 + 驱动拒绝"确定性复现；`tests/f05` 149 passed 回归。

## 7. 合并与 HEAD

- 基线 `111e2c2` → 本票合并集成分支：`f6e7e5b`（T083 runview + T084）、`a74ac9d`（T083 runview 条件 E2E @ `b343f7b`）、`982a1b0`（T086 model node @ `f21f09a`）、`723760b`（集成 tip `15db973`：T087 approval + T088 retrieve + T089 call_app + 集成方对并发调度器的适配）。
- T086 冲突解决：`graphrun.go` 的 `model` 分派并入并发调度器（`executeGraphModel(ctx, node, view, attempt)`），`modelnode.go` 改为读不可变 `graphNodeView`、失败/取消/waiting 走 `graphNodeAttempt` capture（等价于 T086 的单次生成、schema 独立失败、非重试语义）；`PROGRESS.md` 两侧小节都保留；T086 改了画布 source，**重建并提交** `canvas.js`/`canvas.css`。合并后 `go build/vet/test ./...` 全绿、Node 纯模块测试（runview/canvas/model）全绿。
- T087/T088/T089 合并后语义修复（`9a302f2`）：跳过事实改为**每次 attempt 只发布一次、在 attempt 得出终局时发布**（成功时在 end 节点提交结果之前；失败/取消时在提交之前；**waiting hand-over 不发布**，未到达的节点保持 unsettled，与线性等待语义一致）。这是并发调度器与 T087/T089 等待/恢复观测语义的冲突点，修复后 T087 三例、T089 等待/恢复用例通过（见 §8.2）。
- 分支 / HEAD：`t/t085-parallel-optional`；功能提交 `71213f0`、`3a98b3a`、`5935afd`、`78d09db`、`9a302f2`，合并 `982a1b0`、`723760b`，报告提交为分支 tip（`git log -1`）。
- 无迁移；`product/docs/spec/**`、`product/contracts/**` 未改。

## 8. 最终树门禁状态

最终代码树（含 `78d09db`、T086 合并）上：`tests/f04b` **43 passed in 239.44s**（合并 T086 后，含 T085 5 例、
T083 全回归、T086 model-node 9 例，见 §8.1）、`tests/f05` **149 passed**、`tests/tooling` **21 passed**、
`go build/vet/test ./...` 全绿、`gofmt` 干净、`sync_spec --check` no drift；
`tests/f06` **172 passed / 1 failed**（§6.1 的环境性 sandbox 启动失败，修复后该用例单测重跑 **1 passed**）。
按编排者队列要求（T088/T089 使用共享栈），本轮之后未再次占用共享栈；合并 T086 后的 `tests/f04b` 复跑与原始输出见 §8.1。

### 8.1 合并 T086 后的复跑

```text
$ python -m pytest tests/f04b -q          # 合并 T086（model node）后、全局 pytest 锁、最终树
...........................................                              [100%]
43 passed in 239.44s (0:03:59)
exit=0
```

43 = 合并前 34 + T086 的 model-node 用例 9（真实栈单次生成/schema 独立失败/prompt/等待/复用）。
合并后另跑：`node tests/f04b/{runview,canvas_f04b,canvas_model}.test.mjs` → PASS 6 / PASS 9 / PASS 4；
`pytest tests/f04b/{test_canvas_f04b_assets,test_t086_model_canvas,test_t083_canvas_validate}.py` → 12 passed；
`go build/vet/test ./...` 全绿、`gofmt` 空、`sync_spec --check` no drift。

### 8.2 合并 T087/T088/T089 后的复核（`9a302f2`）

```text
$ python -m pytest tests/f04b/test_t085_parallel_optional.py tests/f04b/test_t083_condition_runtime.py \
    tests/f04b/test_t087_approval_node.py tests/f04b/test_t089_callapp.py tests/f04b/test_t086_model_node.py -q
1 failed, 30 passed in 376.48s (0:06:16)
# 唯一失败：tests/f04b/test_t089_callapp.py::test_parallel_call_apps_create_two_independent_children_and_join_both
#   断言 children 行的 parent_invocation_key == ["call_app:a","call_app:b"]，真实并发下 DB 返回 ["b","a"]
#   （两个并行 call_app 子调用同时创建，顺序不是契约；测试应排序/按集合比较，非调度器缺陷）。
```

- T087 三例（审批等待→恢复、拒绝、两次等待后汇合）**全部通过**（`9a302f2` 之前失败于 waiting attempt 多发的 `skipped`）。
- T089 的等待/恢复与 required-child 失败用例通过；仅剩上述**顺序断言**（并发创建顺序非确定）。
- T085 5 例、T083 condition runtime 7 例、T086 model-node 9 例：**全部通过**。
- 另：合并后的完整 `tests/f04b` 曾为 62 passed/13 failed；其中 T088 的 6 例失败是 `compiler_unconfigured`（`ORCHARA_WORKER_IMAGE_DIGEST` 未在该套件的 stack fixture 环境里注入，属 T088 环境/夹具问题），`test_t083_runview.py::test_the_real_run_view...` 是集成侧测试自身 bug（`node_results` 被包成 `{items:…}` 后又按 list 迭代 → `TypeError`）；两者都不在 T085 代码路径。集成方已在 15db973 合并本票调度器，建议由其收口上述两处。

## 9. 变更文件

新增：
- `internal/presetexec/graphrun_parallel_test.go`
- `tests/f04b/test_t085_parallel_optional.py`
- `tests/support/t085.py`
- `reports/f04b/T085-parallel-optional.md`（本文件）

修改：
- `internal/presetexec/graphplan.go`（region `branchSource`、`regionOfNode`/`branchFor`；与 T086 的 model 计划兼容）
- `internal/presetexec/graphrun.go`（并发调度器重写；capture；optional allowlist；per-node sandbox 单元；T086 `model` 分派）
- `internal/presetexec/modelnode.go`（合并后适配并发调度器：不可变 view + capture；语义不变）
- `internal/presetexec/linear.go`（`nodeHost.sandboxUnit`、取消 capture、`emitNode` 的 durable ctx）
- `internal/presetexec/presetexec.go`（`refuse`/`failRun`/`failAfterStart` capture；`r.artifacts` mutex）
- `internal/presetexec/agent.go`（agent 取消/waiting capture；工具 sandbox 单元含节点 id）
- `internal/presetexec/callapp.go`（waiting_children capture）
- `internal/presetexec/presetexec_test.go`（fake client：`onSubmitResult` 钩子、按 URL 的 PutBytes、Status 加锁）
- `internal/sandbox/executor.go`、`internal/sandbox/sandbox_test.go`（§6.1 的 F05 竞态修复 + 确定性回归测试）
- `apps/web/src/assets/runview.js`、`apps/web/src/debug.html`（optional 收敛显示 + 图例/CSS）
- `apps/web/canvas/src/main.js`、`apps/web/src/assets/canvas.js`/`canvas.css`（join 面板 optional 标记/提示；合并 T086 后重建 bundle）
- `tests/f04b/runview.test.mjs`（optional 收敛断言）、`tests/f04b/test_canvas_f04b_assets.py`（bundle marker）
- `PROGRESS.md`

## 10. 第三轮复核修复（2026-11-06）

第三轮复核把本票两个延迟项收口，未改运行时收敛语义：

1. **optional 发布校验**（Go 生产校验器 + P05 双语言校验器 + 语料同步）
   - `internal/workflow/validate.go`、`probes/p05_dsl/go/validate.go`、`probes/p05_dsl/python/validate.py`
     在 join source 校验中新增：`optional_sources` 指向的节点类型必须 ∈
     `{python, condition, join, retrieve}`（与运行时 `optionalFailureNodeTypes` 同一集合）；
     否则 `bad_join_sources`，定位 `/nodes/<i>/config/optional_sources`。
   - P05 `build_corpus.py` 重建 `corpus.json`：原 `parallel-optional-source` 的右分支改为
     pure python compute 节点；新增 `optional-model-source`、`optional-agent-source`、
     `optional-call-app-source` 三个负例（共 43 例）。
   - 真实 HTTP 发布门禁：`test_publish_refuses_an_optional_agent_source`（`POST /validate` `valid:false`，
     debug 提交 422 `validation_failed`，零 Run/零 Job）。
   - 运行时测试 fixture 调整：`parallel_agent_definition` 与 T091 `agent_optional` 变体把 optional
     source 改为 agent 下游的 pure python guard；agent capability 失败仍在 branch 内，运行时 allowlist
     仍不降级（`test_optional_never_swallows_an_agent_capability_failure`、T091 capability-failure 用例通过）。
2. **真实进程重放 join-once**
   - `tests/f04b/test_t087_approval_node.py::test_a_real_resume_reuses_the_join_and_never_fires_it_twice`：
     两次真实 `exec --once`，join data artifact 逐字段不变，join 每 attempt 至多一次 succeeded。
   - Go fake-seam 用例保留为快速单测，不再是唯一证据。

门禁（本轮实测）：
```text
$ python -m pytest tests/f04b/test_t085_parallel_optional.py -q
6 passed in 67.37s (final tree)
$ python -m pytest tests/f04b/test_t083_canvas_validate.py -q
3 passed in 24.58s
$ python -m pytest tests/f04b/test_t091_ten_node_workflow.py -q
8 passed in 87.75s
$ python -m pytest tests/p05 tests/f02a/test_t023_invalid_draft.py -q
113 passed in 26.69s
$ go test ./... -count=1        # all packages ok
$ gofmt -l internal cmd tools   # empty
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest

$ python -m pytest tests/f04b -q
96 passed in 625.94s (0:10:25)
```

96 = 原 91 + T085 发布负例 1 + T087 自动过期/真实 join 重放 2 + 复审 P1 的 sweeper/decision
两条故障回滚重试 2；T085 的发布校验/运行时用例在最终树保持通过。

无迁移；`docs/spec/**`、`contracts/**` 未改（`sync_spec --check` no drift）。
