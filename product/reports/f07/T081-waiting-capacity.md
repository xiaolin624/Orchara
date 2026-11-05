# F07 T081 验收：父任务满载等待时子任务仍能运行并收敛（#82）

- 任务：F07 / GitHub Issue #82（票 `T081`），分支 `t/t081-waiting-capacity`（worktree
  `E:\orchara-worktrees\t081`，基线 `integration/t068-t082` @ `a4e946d`，合并父代理修复提交
  `affc12c`（T078 迁移重编号）与后续集成（T080 父预算、T071 文档）后为 `7924139`）。
- 状态：**passed**。独立子执行池（`global/child_slots` cap 8 + `parent:<id>/child_slots` cap 3）、
  父轮转公平领取、child 终态自动收敛 hook（等待决议/resume、必需失败传播、父取消后重驱
  finalizer）、allocation 按 holder 恰好一次释放；全部以真实 PG 16.4、编译后的
  `cmd/api`/`cmd/supervisor`/`cmd/admin`、真实 HTTP/mTLS、真实 result/exit 协议和真实
  supervisor 进程验证。
- 边界（如实）：共享父预算/次数原子属 T080；顶层配额与每会话逻辑槽属 F08/T074 请求队列，
  本票不读写（§5.3）；真实 WSL2/runsc 五类评测仍 `not_run`（T078 边界不变）；浏览器 DOM
  `not_run`（未改页面）。
- 迁移：`0050_f07_waiting_capacity.sql`（worktree 当前尾号；orchestrator 目标 0054，合并时按合并
  树重编号；测试按名字断言）。ADR：`docs/adr/0032-f07-waiting-capacity.md`。未改
  `docs/spec/**`/`contracts/**`，`sync_spec --check` no drift。

## 0. 工作记录（简短）

1. 读 BRIEF、票 #82、`notes/f07-delegation.md` §3.2/§3.3/§3.4/§7/§9、T078 报告 §5/§6、
   `internal/execution/{service,store,waiting,waiting_store,cancel,children_hooks}.go`、
   `internal/children/service.go`、`internal/presetexec/callapp.go`、0016/0021/0049 迁移与
   既有测试支撑。
2. **基线缺陷**：`integration/t068-t082` @ `a4e946d` 同时存在 `0048_diagnostic_grants.sql` 与
   `0048_f07_child_runs.sql`，`internal/db.LoadFS` 对重复版本直接失败 ⇒ 任何新库 `admin migrate`
   都不可能成功。父代理在集成分支提交 `affc12c` 将 T078 迁移改为 `0049`；本分支先合并该提交
   （merge `<merge-commit>`），再使用下一个空号 `0050`。修复只重命名、字节不变。
3. TDD 切片：真实栈用例先红（子 Run 被顶层池/候选择序挡住；等待父不会自动收敛），再实现
   claim 池路由/轮转/释放、hook、Reconcile，逐片转绿。
4. 门禁、报告、PROGRESS、ADR，合并 `integration/t068-t082` 后复跑（§3）。

## 1. 交付

### 1.1 迁移 `0050_f07_waiting_capacity.sql`（非契约、加法）

| 变更 | 说明 |
|---|---|
| `platform.runs.last_child_claim_at` | 父 Run 的轮转键：该父最近一次 child claim 时间；候选查询据此把刚领取过的父排到后面 |
| `global/child_slots`（used 0, capacity 8） | 独立子执行池；child attempt 取此池，不再占 Python `execution_slots` |
| `parent:<parent_run_id>/child_slots`（cap 3） | 每父并行上限；第一次 child claim 时 `INSERT ... ON CONFLICT DO NOTHING` 懒创建（父集合开放 + `used<=capacity` CHECK 不允许事后降容） |

不新增 `run_children.state`：child 的 `runs.state` 仍是唯一权威（ADR 0025 D1）。`orchara_worker`/
`PUBLIC` 零权限；重复 migrate no-op。详见 ADR 0032 D1。

### 1.2 claim：池路由、每父上限、公平轮转（`internal/execution/{service,store}.go`）

候选查询在**同一个** `SKIP LOCKED` 查询里：

- 顶层候选 join `global/execution_slots`，child 候选 join `global/child_slots`（按
  `runs.parent_run_id` CASE 互斥）；
- child 候选再 join `parent:<parent_run_id>/child_slots`；任一池 `used >= capacity` 的候选被
  过滤，避免「不可领取的候选挡住可领取候选」；
- `ORDER BY COALESCE(parent.last_child_claim_at, '-infinity'), j.available_at, j.id`。顶层 Run
  无 parent 行，保持历史 FIFO。

claim 事务按 `global/child_slots → parent:<id>` 固定顺序锁行并 `used+1`，同事务写两条
`quota_allocations`（holder `job:<job_id>`）并写父 `last_child_claim_at`；顶层 Run 仍只写一条
`global/execution_slots` allocation，行为不变。

### 1.3 释放恰好一次：按 holder 的全部 allocation（`store.go`/`cancel_store.go`）

`releaseAllocationOnceTx` 改为按 holder 读取**所有** allocation 行（按 scope/dimension 排序
`FOR UPDATE`），逐行 `released_at IS NULL` 条件更新并对该行自己的计数 `used-1`。child attempt
的两行在一次调用内各释放一次；重复 finalize/cancel/replay 命中集合 no-op；缺行仍是
`ErrAllocationConflict`。取消路径 `releaseAllocationIfPresentTx` 同样只按 holder 判断存在性。
T038 `runs recover` 复用同一原语。

### 1.4 child 终态 hook（`internal/children/hook.go` + `execution/children_hooks.go`）

- `execution.Service.Children ChildLifecycle` 由 `api.New` 装配 `children.Service`；
  `ConfirmExit`/`SubmitResult`/`Cancel`/`ResolveUnknownOperation` 在**提交后**调用
  `OnChildTerminal(run_id)`（hook 自开事务，避免 child 行锁 → 父行锁反序）。
- 必需 child 失败/取消：对仍存活且无 cancel intent 的兄弟调用既有 T036 `Cancel`。
- 父的所有直接 child 终态后：
  - `waiting_children` → 确定性 decision（最早 required 失败 child → `failed`，否则最早终态
    child → `succeeded`），经 `ResolveChildrenWaitingTx` 存储并走同一 `maybeEnqueueResumeTx`；
  - `cancelling` → `RedriveParentAfterChildrenStoppedTx` 重驱唯一 finalizer，父只在所有 child
    停止后提交 `cancelled`；
  - `running/recovery_pending/needs_reconciliation` → 仅在已有 stored result 或 cancel intent
    且 latest attempt 已 exit 时重驱动，绝不编造 result。
- 父取消提交后 `OnParentCancelRequested` 对每个存活且带 `run_children` 收据的直接 child 调同一
  T036 `Cancel`；没有收据的合成/历史 child 保持 T036 原语义（父记录 intent 并等待），避免改变
  既有 cancel 套件。
- 崩溃窗口：`Claim` 在取任何 claim 锁前调用有界幂等 `Reconcile`，重放「必需失败未传播」与
  「无活 child 但等待/终止未收敛」两类；它与 hook 调用完全相同的 execution seam。

### 1.5 顶层配额/会话槽边界

父等待释放 Python 执行槽是既有 T035 行为（exit 后 release），本票不新增释放路径；测试断言
等待父不持有 `execution_slots` 且 `run_waitings.exit_confirmed_at` 已写。顶层配额/每会话逻辑槽
属 F08/T074 请求队列，本票未读写（§5.3）。

## 2. 测试与证据

新增 `tests/f07/test_t081_waiting_capacity.py`（6 用例）与 `tests/support/t081.py`。全部真实栈：
真实 PostgreSQL 16.4（每用例新库 + 真实 `cmd/admin migrate` + bootstrap）、编译
`cmd/api`/`cmd/supervisor`/`cmd/admin`/`cmd/maintenance`、真实 HTTP/mTLS、真实对象字节、真实
claim/result/exit 协议、真实 `cmd/supervisor exec --once` 进程；child Run 只由真实
`spawnChild` 端点创建。

| 用例 | 证明 |
|---|---|
| `test_twenty_waiting_parents_do_not_starve_the_child_pool` | 20 个父全部 `waiting_children` 且 `execution_slots=1` 被一个运行中的顶层 Run 占死；把 `child_slots` 压到 1 后 20 次真实 supervisor 执行各自领取一个 child 并 `succeeded`；全程 `execution_slots.used==1`、child 池释放回 0、父池 0；20 个父由 hook 自动得到 `children/succeeded` decision + 单一 resume job（Python 池饱和所以停在 queued） |
| `test_per_parent_parallel_limit_and_fair_claiming` | 父 A 4 个 child、父 B 1 个：真实 claim 端点先给 A（最老），随后给 B（A 刚领取 → 轮转，尽管 A 剩余 job 更老），A 的第 2/3 个照常领取，第 4 个在第 3 个释放前 204；A 的一个 child 真实 succeeded 后第 4 个才领取；`parent:<A>/child_slots` 恒 3、`parent:<B>` 1、每个 attempt 两条 allocation（global+parent）且领取时未释放 |
| `test_parent_cancel_waits_for_a_running_child_and_releases_once` | 父等待中取消（202）→ 父 `cancelling`、child 收到传播 intent 仍 `cancelling`；父 `finished_at` 为空、无 decision（取消优先）；child 真实 exit 证据后 child `cancelled`、hook 重驱 finalizer 使父 `cancelled`；数据库时钟断言 `child.finished_at <= parent.finished_at`；两条 allocation 各释放一次、双计数归零；重复 cancel/exit/child cancel 后 release ledger 与计数完全不变 |
| `test_a_required_child_failure_fails_the_parent_after_it_stopped` | 真实 supervisor 让父等待；child 的 succeeded proposal 因冻结 output schema 不合规被 finalizer 判 `failed/child_output_schema_invalid`；hook 自动写 decision `{failed, child_id}`（`decision_at >= child.finished_at`）+ 单一 resume job；下一次真实 supervisor 执行重放同一 child 并以 `child_output_schema_invalid` 失败父 Run（`finished_at >= child.finished_at`，attempts=2，仍只有 1 个 child） |
| `test_child_pool_allocation_is_released_once_on_recovery` | child attempt 两行 allocation；`sweep-leases` park 时槽保留（未证明停止）；真实 `orchara-admin runs recover` 释放一次并 requeue；再 claim 复用同一两行（`released_at` 重新为空、双计数=1）；重复 recover 被拒且 release ledger 不变 |
| `test_a_child_that_finished_before_the_parent_parks_is_reconciled` | 快 child 先终态、父随后才 park 的崩溃/竞争窗口：hook 当时看到父 running（no-op），等待决议为空；下一次真实 claim 先跑有界 `Reconcile`，从持久行产生 `children/succeeded` decision 并领取 resume job；child 池 0、Python 池 1 |

Go 单测未新增：本票改动全部是 SQL 事务/真实进程行为，纯规则（decision 选择、轮转键）已由真实栈
用例覆盖；`go test ./...` 全绿（§3.3）。

## 3. 门禁与原始输出

环境 profile：Windows 宿主机；Python 3.12.2；Go 1.27.3 windows/amd64
（`E:\orchara-toolchains\go`）；Node v24.13.0；PostgreSQL 16.4 用户态集群
`127.0.0.1:55432`（每用例 `create_database` + 真实 `cmd/admin migrate`）；`dev-stub` profile。
所有 pytest 均带 `--basetemp=E:\orchara-tmp\t081-*`（结束时删除）。

### 3.1 `pytest tests/f07`（两次背靠背）

```
run1: ....................................[100%]
      36 passed in 411.33s (0:06:51)   （最终树：含 T081 6 用例 + T080 7 用例 + T075/T078/页面）
run2: ....................................[100%]
      36 passed in 451.80s (0:07:31)   （同一最终树，背靠背第二轮）
（T081 隔离树、合并 T080 之前：29 passed in 462.36s，0:07:42）
```

### 3.2 邻接回归（执行容量/等待/取消/恢复 + 迁移/工具）

```
python -m pytest tests/tooling tests/f03/test_t029_claim.py \
  tests/f03/test_t029_concurrency.py tests/f03/test_t031_finalizer.py \
  tests/f03/test_t035_waiting.py tests/f03/test_t035_resume.py \
  tests/f03/test_t036_cancel.py -q
  65 passed in 586.21s (0:09:46)

python -m pytest tests/f03/test_t029_migration.py tests/tooling -q
  28 passed in 71.00s (0:01:10)
```

一次中间尝试因宿主 E: 盘被并发套件的 basetemp 占满而在 `go build ./cmd/supervisor` 的产物
拷贝处失败（`There is not enough space on the disk`；50 passed / 15 fixture errors，全部是磁盘
错误而非断言失败）。清理本票 basetemp（E: 释放 1.5 GB，随后其它并发套件也释放了空间）后上述
两次运行全绿。这是环境事件，如实记录。

### 3.3 Go 与契约

```
go build ./...   -> 无输出（成功）
go vet ./...     -> 无输出（成功）
go test ./...    -> 全部包 ok，无 FAIL
gofmt -l .       -> 无输出（干净）
python tools/sync_spec.py --check -> no drift: spec copy and contract snapshot match the manifest
```

## 4. AC 对照

| 验收项 | 结论 | 证据 |
|---|---|---|
| 20 父等待时独立子槽仍推进 | **passed** | §2 用例 1：20 次真实 supervisor child 执行全部 succeeded，Python 池恒占 1 |
| 每父并行上限生效且公平领取 | **passed** | §2 用例 2：第 4 个 child 204、释放后领取；A 刚领取后 B 优先 |
| 父取消/必需子失败等待子执行停止 | **passed** | §2 用例 3/4/6：数据库时钟 `child.finished_at <= parent.finished_at`；取消时父停在 cancelling 直至 child 终态；快 child 先终态时由 Reconcile 收敛 |
| allocation 不重复释放 | **passed** | §2 用例 3/4/5：成功、失败、取消、恢复、重复 replay 下 release ledger/双计数不变；按 holder 释放两行 |
| 端到端入口实际演示；失败/授权/重试边界断言 | **passed** | 全部真实 HTTP/mTLS + 编译二进制 + 真实 supervisor + 真实 PG；取消/必需失败/恢复/重放路径均有断言；无单层 mock |
| 记录版本/输入 hash/命令/原始结果/环境；stub/real 分报 | **passed** | 本报告 §3；`run_children.input_sha256` 等由 T078 冻结；本票不新增 hash 输入；stub=dev-stub 模型未参与本票用例 |
| 父等待释放 Python 槽、保留顶层配额 | **passed（切片内）** | §1.5/§2 用例 1：等待父不持有 `execution_slots`，child 走独立池；顶层/会话配额属 F08/T074(§5.3) |

## 5. `not_run` / 边界（如实）

1. **共享父预算/次数原子**：F07/T080。本票不写 `internal/budget` 语义。
2. **真实 WSL2/runsc 五类评测**：`not_run`，与 T078 相同（依赖后台评测栈与 T080 的预算原子；
   本票用 start→end 子应用真栈执行，不用沙箱）。
3. **顶层配额与每会话逻辑槽**：F08/T074 `requests` 队列；本票未读写其表/逻辑（ADR 0032 D7）。
4. **浏览器 DOM**：`not_run`；本票未改页面。
5. **全量 `pytest tests`**：BRIEF 明确不跑；已跑 f07×2、tooling、f03 claim/concurrency/exit/
   finalizer/waiting/resume/cancel 子集（§3.2）。
6. **spawn 时容量拒绝**：本票选择 claim 时取槽（D2）；spawn 仍返回 202 后父等待，容量由子池
   在 claim 排队。这是设计选择，不是缺口。
7. **父取消向 child 的传播范围**：只对带 `run_children` 收据的委派 child 传播取消；没有收据的
   合成/历史 child 保持 T036 语义（父记录 intent 并等待），既有 T036 cancel 套件因此不变。

## 6. 给后续票的接口

- `platform.runs.last_child_claim_at` 与 `global/child_slots`/`parent:<id>/child_slots`：F08/T079
  的每会话队列可复用同一轮转键/每父 scope 语义；不要新建第二套计数。
- `execution.Service.Children ChildLifecycle`（`OnChildTerminal`/`OnParentCancelRequested`/
  `Reconcile`）：任何新增的终态写入口必须在其事务**提交后**通知，或依赖 claim 前 Reconcile；
  不要在 `finalizeRunTx` 内锁父。
- `execution.ResolveChildrenWaitingTx`/`MaybeEnqueueResumeTx`/`RedriveParentAfterChildrenStoppedTx`：
  T082 的交接等待应复用同一决议/恢复/重驱接缝。
- `releaseAllocationOnceTx` 现已按 holder 释放全部行：新的多池 allocation 只需写行，不需要新的
  释放函数。
- `tests/support/t081.py`：child 池/父计数/release ledger 读模型与真实 claim/等待驱动。

## 7. 参考

- ADR：[0032 — F07/T081 waiting capacity](docs/adr/0032-f07-waiting-capacity.md)；前置 ADR 0025
  （T078 child Runs）。
- 规格：EXECUTION.md §7（:62-64 cancel/受控单元、:70 权限交集、:76 等待释放槽/独立槽/每父上限/
  公平、:78 fail-fast）；OPERATIONS.md:26/28；STATE-MACHINES.md:26-28/37；EVALUATION.md:49-50
  FAULT-08；ADR 0025 D1/D10。
- 依赖：T078 `spawnChild`/`run_children`/`call_app`；T035 waiting/resume；T036 cancel；
  T031 finalizer；T038 recovery。
