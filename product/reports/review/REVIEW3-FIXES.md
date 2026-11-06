# 第三轮复核修复 — T085 optional 发布校验 / T087 自动过期 / 真实 replay join-once（2026-11-06）

- 仓库：`E:\Orchara`；分支：`integration/t083-t091`；复核范围：`ea56392..8ce1d82`。
- 本轮只修三项：T087 审批自动过期、T085 `optional_sources` 发布校验、真实进程重放后的 join-once。
- 未动：T090 模型/训练、DSH 模型接入、浏览器 DOM、T084 聊天输入形状、纯代码风格重构。
- 未改 `contracts/**`、`docs/spec/**`、迁移、凭据；`python tools/sync_spec.py --check` no drift。
- 复审 P1（过期与等待交接非原子）的修复与回归见 §6。

## 1. 逐条映射

| # | 复核发现 | 修复 | 回归证据 |
|---|---|---|---|
| 1 | **T087 审批自动过期缺失**：停在 `waiting_approval` 的 Run 只有在下一次 decision 时才观察过期，不会仅因时间自动终止。 | 新增 `internal/approvals/expiry.go::ExpireDue`：按数据库时钟扫描 due pending **node** approvals（`operation_id IS NULL`），逐条在 `Run -> approval` 锁序下重读并置 `expired`（`decided_by=NULL`、`decision_key='sweeper:expired'`），并在**同一事务**内调用 `execution.ResolveExpiredNodeApprovalTx` 写入过期等待决定、驱动既有 resume 事务（§6）。F06 capability approvals 保持既有 decision/lazy 边界，不归本 sweeper。`cmd/api` 启动定时器（`ORCHARA_APPROVAL_SWEEP_INTERVAL`，默认 30s，范围 10ms..5m），`internal/api.Server.ExpireDueApprovals` 暴露一次确定性 pass。 | Go：`TestWaitingReferencesApproval`（stale approval 不解析当前 wait）、`TestApprovalSweepIntervalDefaultsAndBounds`。真实栈：`test_a_parked_approval_expires_automatically_without_a_decision`（真实 supervisor 停泊、置 past、不调用 decision；API 100ms sweeper 自动 failed `approval_expired`，waiting decision `expired`，无 resume/output；事后决定 409）；故障回滚重试用例 `test_an_expiry_handoff_failure_rolls_back_and_is_retried`（§6）。 |
| 2 | **T085 optional 发布校验缺失**：画布/发布允许 `optional_sources` 指向 agent/call_app/model，运行时不会收敛，只有画布提示。 | Go 生产校验器 `internal/workflow/validate.go`、P05 Go/Python 校验器新增：optional source 节点类型必须 ∈ `{python, condition, join, retrieve}`（与运行时 `optionalFailureNodeTypes` 同一集合），否则 `bad_join_sources` 定位 `/nodes/<i>/config/optional_sources`。P05 `build_corpus.py` 重建 corpus：原 optional 正例改为 pure python source；新增 model/agent/call_app 三个负例（43 例）。受影响真实栈 fixture 改为“optional source = agent 下游的 pure python guard”，保持 agent capability 失败不降级的运行时证明。 | P05：`pytest tests/p05` **111 passed in 7.17s**（Go/Python 共享语料）；`internal/workflow` corpus replay 通过。真实 HTTP：`test_publish_refuses_an_optional_agent_source`（`POST /validate` `valid:false` + 定位；debug 提交 422，零 Run/Job）。运行时回归：T085 agent 失败用例、T091 capability-failure 用例通过。 |
| 3 | **T083/T085 replay join-once 只有 fake-seam**：真实“同一 run 第二次 exec，join 只执行一次”未跑。 | 新增真实栈 `test_a_real_resume_reuses_the_join_and_never_fires_it_twice`：`start -> left/right(python) -> join -> approve1(approval) -> end`；第一次 exec 跑两个真实 runsc 分支并发布 1 条 join data，停在审批；第二次 exec 恢复后 join 只发复用 `succeeded`，join data artifact 逐字段不变，end 使用同一封套。Go fake-seam 用例保留为快速单测，不再是唯一证据。 | `test_a_real_resume_reuses_the_join_and_never_fires_it_twice`（两次真实 `exec --once`）；join 每 attempt 至多一次 succeeded，data 行恰 1 条。 |

## 2. 设计与边界

### 2.1 T087 sweeper

- **数据库时钟**是唯一权威：候选查询和锁内重查都用 `now() >= expires_at`，不信任进程时间。
- **锁序**与 decision 相同（Run -> approval），因此 sweeper 与人工决定串行化；锁内重读保证“人工先到”时 sweeper 不做任何写入。
- **stale approval**：只过期行，不解析 Run 当前的另一个 wait（`waitingReferencesApproval` 对比
  `approval:<id>`；capability wait 不归本 sweeper）。这是 changed-proposal 场景的安全边界。
- **原子交接**：审批行 `expired`、waiting decision、以及（exit 已确认时的）Run 终态调用
  `execution.ResolveExpiredNodeApprovalTx` 在**同一个数据库事务**内提交。任何一步失败（含故障注入）
  都回滚整个过期，下一轮扫描仍看到 `pending` 并重试；不会产生“expired 但 waiting decision 为空”的
  不可恢复状态（§6）。
- **等待结果尚未落地**：过期决定在同一事务持久化；`adoptApprovalDecisionTx` 在 waiting result 到达时接管
  并失败 Run，与既有 decision-before-waiting 顺序一致。
- **幂等/多实例**：每行独立事务 + `UPDATE ... WHERE state='pending'`；重复扫描和多个 API 实例竞争都收敛到一次过期。
- 配置默认 30s；测试 fast-sweep 栈使用 100ms，其余真实栈用 5m test-only override，避免与
  decision 路径到期用例竞速。

### 2.2 T085 发布校验

- 允许集合只包含运行时 `optionalFailureNodeTypes` 的同一集合（`python/condition/join/retrieve`），
  避免“发布允许、运行时 fail closed”的两套语义。
- approval 也不在允许集合内：它不是纯读/计算，optional 收敛其拒绝会吞掉人工业务决定。
- 生产校验器与 P05 独立实现保持 byte-identical verdict；corpus 重建后 Go/Python parity 全绿。
- 运行时“agent/call_app 失败永不降级”的证明保留：optional source 是下游 pure python guard，
  失败节点仍是 agent，`optionalFailureNodeTypes["agent"] == false`。

### 2.3 join-once 真实重放

- graph 把 join 放在 approval 之前：第一次 exec 完成 fork/join 后停泊；第二次 exec 从 resume 恢复，
  `reusableNodeOutputs` 复用 join 的已验证 data artifact。
- 断言不是“状态看起来成功”，而是：join data artifact 行集合前后逐字段相同、left/right data 各 1 条、
  join 每 attempt 至多一次 succeeded、final output 使用同一 join envelope。

## 3. 门禁（本轮实测）

工作目录 `E:\Orchara\product`。Go 环境：
`GOCACHE=E:\orchara-toolchains\gocache`、`GOMODCACHE=E:\orchara-toolchains\gomodcache`，
`PATH` 含 `E:\orchara-toolchains\go\bin`。

```text
$ python -m pytest tests/f04b/test_t085_parallel_optional.py -q
6 passed in 67.37s (final tree)

$ python -m pytest tests/f04b/test_t087_approval_node.py -q
12 passed in 105.43s

$ python -m pytest tests/f04b/test_t091_ten_node_workflow.py -q
8 passed in 87.75s

$ python -m pytest tests/f04b/test_t083_condition_runtime.py -q
7 passed in 63.86s

$ python -m pytest tests/f04b/test_t083_canvas_validate.py -q
3 passed in 24.58s

$ python -m pytest tests/p05 tests/f02a/test_t023_invalid_draft.py -q
113 passed in 26.69s

$ go build ./...            # exit 0
$ go vet ./...              # exit 0
$ go test ./... -count=1    # all packages ok
$ gofmt -l internal cmd tools   # empty
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest

$ python -m pytest tests/f04b -q
96 passed in 625.94s (0:10:25)
```

（96 = 原 91 + 本轮的 T085 发布负例 1 + T087 自动过期/真实 join 重放 2 + 复审 P1 的
sweeper/decision 两条故障回滚重试 2。该全量运行在最终树上执行，覆盖同事务交接修复。）

## 4. 变更文件

新增：
- `product/internal/approvals/expiry.go`
- `product/internal/approvals/expiry_test.go`
- `product/reports/review/REVIEW3-FIXES.md`（本文件）

修改（实现）：
- `product/internal/config/config.go`、`config_test.go`（sweep interval 配置/校验）
- `product/internal/api/server.go`（一次 sweep pass）
- `product/cmd/api/main.go`（定时器生命周期）
- `product/internal/execution/{waiting.go,approval_bridge.go}`（`ResolveExpiredNodeApprovalTx` 原子交接及边界注释）
- `product/internal/workflow/validate.go`（optional source 类型校验）
- `product/probes/p05_dsl/{build_corpus.py,corpus.json,go/validate.go,python/validate.py}`

修改（测试/fixture/证据）：
- `product/tests/support/{fixtures.py,t085.py,t087.py,t091.py}`
- `product/tests/f04b/{test_t083_canvas_validate.py,test_t085_parallel_optional.py,test_t087_approval_node.py,test_t091_ten_node_workflow.py}`
- `product/reports/f04b/{T083-condition-branching.md,T085-parallel-optional.md,T087-approval-node.md}`
- `product/docs/adr/0034-f04b-go-graph-runtime-and-noncontract-node-outputs.md`
- `product/reports/review/BATCH-REVIEW-FIXES.md`
- `product/PROGRESS.md`

## 5. 未做/边界

- 全量 `tests/f06`、`tests/f10`、`tests/f08` 未重跑；本轮只跑受影响套件、真实栈关键套件与 `tests/p05`。
- `go test -race` 仍因本机无 cgo C 编译器 `not_run`。
- 真实厂商模型/嵌入、浏览器 DOM 仍不在本轮范围。
- T090 模型/训练、DSH 模型接入、T084 聊天输入形状未改。

## 6. 复审 P1 修复：过期与等待交接改为同事务

### 6.1 缺陷

第一版 `ExpireDue` 在审批事务提交后才调用 `resolveWaiting`。若进程在两步之间退出，或等待结算发生
瞬时数据库错误，approval 已经是 `expired`，而后续 sweeper 只扫描 `pending`，会永久跳过该行；Run 停在
`waiting_approval`、waiting decision 为空，迟到的人工决定也只返回 `409 approval_expired`。这违反
`STATE-MACHINES.md` 的同事务事件/job 要求和 `IMPLEMENTATION-DETAILS.md:57` 的过期终止语义。

### 6.2 修复

- `internal/execution/waiting.go` 新增 `ResolveExpiredNodeApprovalTx(ctx, tx, runID, approvalID)`：
  在调用方事务内写入 `outcome=expired` 的 waiting decision，并调用既有 `maybeEnqueueResumeTx`；
  已确认 exit 时在同一事务把 Run 置 `failed approval_expired`，exit 未到时只持久化决定，后续
  `ConfirmExit` 接管。
- `internal/approvals/expiry.go::expireOne` 在把 approval 置 `expired` 后、`tx.Commit()` 前调用该
  方法；任何错误（包括注入的等待更新故障）都会回滚 approval 过期，下一轮 sweeper 重试。
- `internal/approvals/decision.go::Decide` 的过期分支对 node approval 复用同一原子交接：交接失败时
  回滚并返回 500，而不是先提交 `expired` 再留下空等待；F06 capability approval 的 decision/lazy 路径不变。
- 该方法是幂等的：同 outcome 的已存 decision 会继续驱动 resume；不同 decision 不覆盖；Run 已离开
  waiting/cancelling 时安全返回；waiting 尚未落地时只持久化决定，后续 adopt 接管。
- F06 capability approval 不归此 sweeper，行为不变。

### 6.3 回归

新增两个真实栈故障注入用例：

- `test_an_expiry_handoff_failure_rolls_back_and_is_retried`：安装
  `run_waitings.decision` 更新触发器使**sweeper**交接事务失败 → 等到 API 日志出现注入错误后断言
  approval 仍 `pending`、`decided_at` 为空、Run 仍 `waiting_approval`、waiting decision 为空；移除
  故障后下一轮 sweeper 原子完成 `expired` + Run failed + waiting decision `expired`，无 resume job/output。
- `test_a_decision_path_expiry_handoff_failure_also_rolls_back`：同一触发器下调用
  `POST /approvals/{id}/decision`，交接失败返回 500，approval 仍 `pending`、Run 仍 waiting、decision
  为空；移除故障后 sweeper 完成过期。证明 decision 路径也不会留下死 `expired` 行。

最终树门禁：
```text
$ python -m pytest tests/f04b/test_t087_approval_node.py -q
12 passed in 105.43s

$ go build ./...; go vet ./...; go test ./...
# 全绿
$ gofmt -l internal cmd tools
# empty
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest

$ python -m pytest tests/f04b -q
96 passed in 625.94s (0:10:25)
```
