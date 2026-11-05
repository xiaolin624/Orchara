# ADR 0032 — F07/T081: the independent child pool, fair claiming and waiting convergence

- 状态：accepted（T081 #82 实现记录）
- 日期：2026-11-05
- 范围：F07 父任务等待/子任务容量；T080 的共享父预算与 T079/T082 的路由/交接在此之上扩展，
  不另建调度路径。

## 背景

T078 交付了唯一 child Run 创建路径 `spawnChild` 与 `platform.run_children` 委派收据，并明确
把 child 终态自动恢复、独立子槽、每父并行上限留给 T081（ADR 0025 D1/D10、T078 报告 §5）。
本票要证明的验收是：

1. 20 个父任务全部停在 `waiting_children`、Python 执行槽被占满时，子任务仍能领取、执行并收敛
   （EVALUATION.md FAULT-08；OPERATIONS.md:26「子任务8」）；
2. 每父并行上限 3 生效，且公平领取（一个父不能垄断子池；OPERATIONS.md:28；
   EXECUTION.md:76「队列公平按用户轮转」）；
3. 父取消 / 必需子失败只在所有受控单元停止后收敛（EXECUTION.md:62-64、:78；
   STATE-MACHINES.md:28/37）；
4. quota allocation 在成功/失败/取消/恢复路径都只释放一次（EXECUTION.md:46-48 的
   allocation 唯一键幂等规则）。

已有 0016 的 `quota_counters`/`quota_allocations`、0021 的 `run_waitings`、
`execution/{commit,releaseAllocationOnceTx,maybeEnqueueResumeTx}` 与 T036 cancel 状态机。
缺口是：child attempt 与顶层 Run 抢同一个 `global/execution_slots`；没有每父上限/轮转键；
child 终态没有任何父侧 hook（T078 测试只能手工调 T035 bridge）。

## 决策

### D1. 迁移 `0050_f07_waiting_capacity.sql`（非契约、加法）

- `platform.runs.last_child_claim_at timestamptz`：公平领取的轮转键，写在**父** Run 上，
  记录该父最近一次消费 child claim 的时间。
- 种子 `global/child_slots`，capacity 8。child attempt 取这个池，不再取
  `global/execution_slots`；20 个等待父任务在退出时已经释放各自的 Python 槽，因此子池与顶层
  池互不挤占。
- 每父计数 `parent:<parent_run_id>/child_slots`，capacity 3，**在第一次 child claim 时懒创建**
  （`INSERT ... ON CONFLICT DO NOTHING`）。父集合是开放的，迁移无法预置；`quota_counters` 的
  CHECK（`used <= capacity`）要求 capacity 不能事后下调，所以懒创建 + 定量写入是唯一稳定方式。
- 不新增 `run_children.state` 列：child 自己的 `runs.state` 仍是唯一权威（ADR 0025 D1）。

### D2. 槽在 claim 取得，不在 spawn

spawn 取得会与「父必须能先看到子已创建、再等待」冲突：spawn 拒绝会让父节点在等待前失败，
而「并行」是运行中 attempt 的属性。spawn 仍执行 T078 的闭包/ACL/深度/次数门禁；capacity 是
执行容量，由 claim 原子取得。候选查询按 `runs.parent_run_id` 路由到对应池并过滤已满的池，
因此不会出现「选中一个不可领取的候选 → 204 挡住另一个可领取候选」的饥饿。

### D3. 公平领取：父轮转 + 池过滤

候选查询新增：

- `LEFT JOIN quota_counters qc`（顶层→`execution_slots`，child→`child_slots`）与
  `LEFT JOIN quota_counters pq`（`parent:<parent_run_id>/child_slots`），两者都用
  `used IS NULL OR used < capacity` 过滤；
- `ORDER BY COALESCE(parent.last_child_claim_at, '-infinity'), j.available_at, j.id`：
  刚领取过一个 child 的父排到所有「未领取/更早领取」的父之后；顶层 Run 没有 parent 行，
  保持原 FIFO。

claim 事务按 `global/child_slots` → `parent:<id>/child_slots` 的固定顺序锁计数行并各 `+1`，
同一事务写两条 `quota_allocations`（holder `job:<job_id>`）并更新父的 `last_child_claim_at`。
顶层 Run 的行为完全不变（仍是一条 `global/execution_slots` allocation）。

### D4. 释放：按 holder 释放全部 allocation 行

`releaseAllocationOnceTx` 从「固定 scope/dimension 的一行」改为「该 holder 的所有行，按
(scope,dimension) 排序 `FOR UPDATE`」，逐行执行 `released_at IS NULL` 条件更新并对其自己的
计数 `used-1`。这样 child attempt 的两行在一次调用里各释放一次；重复 finalize 命中的
`released_at` 已是集合的 no-op；取消路径的 `releaseAllocationIfPresentTx` 同样只按 holder
判断存在性。没有第二个释放系统。T038 recovery 的 `recover` 也是同一原语，因此 child 的
两行在恢复路径同样只释放一次。

### D5. child 终态 hook：提交后、独立幂等事务

`execution.Service.Children`（接口 `ChildLifecycle`）由 `api.New` 装配到 `children.Service`。
`ConfirmExit` / `SubmitResult` / `Cancel` / `ResolveUnknownOperation` 在**事务提交后**通知
`OnChildTerminal(child_run_id)`；hook 自己开事务。理由：在 `finalizeRunTx` 内锁父会与 spawn 的
parent→child 锁序相反（T078 §5 的锁序），是 F07 最危险的死锁点。

hook 的语义：

1. 只有存在 `run_children` 收据且 child 已终态的 Run 才会被处理；
2. 必需 child 失败/取消 → 先对每个仍存活、尚未记录 cancel intent 的兄弟调用既有 T036
   `Cancel`（fail-fast；EXECUTION.md:78）；父 Run 本身不在这里提交失败；
3. 当父的**所有**直接 child 都已终态：
   - `waiting_children`：从收据中确定性地产生唯一 decision（最早的 required 失败 child →
     `failed`，否则最早的终态 child → `succeeded`），经 `execution.ResolveChildrenWaitingTx`
     存储并调用同一 `maybeEnqueueResumeTx`；resume 后执行器用同一 `spawnChild` 重放读回 child
     结果/失败码，父以 `child_output_schema_invalid` 等稳定码收敛；
   - `cancelling`：经 `execution.RedriveParentAfterChildrenStoppedTx` 重新调用唯一 finalizer
     （cancel 胜出），父明确在所有 child 停止后才提交 `cancelled`；
   - `running/recovery_pending/needs_reconciliation`：仅当已有 stored result 或 cancel intent
     且 latest attempt 已 exit 时重驱动 finalizer，绝不替执行器编 result。
4. decision 只在 cancel intent 不存在时写入；取消永远优先。

父取消时 `Cancel` 提交后调用 `OnParentCancelRequested`：对每个仍存活、且存在 `run_children`
收据的直接 child 调用同一 T036 `Cancel`（它是受控单元，必须停止），因此父的 `cancelled` 只在
child 退出证据到达后由 hook 的 re-drive 提交。没有委派收据的合成/历史 child（T035/T036 测试
fixture）保持 T036 原有语义：父记录 intent 并等待，由该 child 自己的控制器决定；这样不改变
既有 cancel 套件的行为。

### D6. 崩溃窗口：claim 前有界 Reconcile

hook 是提交后事务，进程可能在 child 终态提交与 hook 之间崩溃。`execution.Service.Claim` 在
取得任何 claim 锁**之前**调用 `ChildLifecycle.Reconcile`（有界 LIMIT、幂等）：重放
「必需 child 已失败但兄弟还没有 cancel intent」和「父已无活 child 但等待/终止仍待收敛」两类。
它不是第二个调度器：只调用与 hook 完全相同的两个 execution seam，并受既有状态机约束。

### D7. 边界：顶层配额与逻辑会话槽

父等待时释放 Python 执行槽是既有 T035 行为（`ConfirmExit` 记录 exit 后
`releaseAllocationOnceTx("job:<id>")`），本票不新增释放路径；测试断言等待父不持有
`execution_slots`、`run_waitings.exit_confirmed_at` 已写。顶层配额/每会话逻辑槽属于 F08/T074
的请求队列，T081 不读写它（报告 §5 明示）。

## 影响

- 迁移 `0050_f07_waiting_capacity.sql`（分支树下一个空号；orchestrator 目标 0054，最终合并按
  合并树重编号；测试按名字断言本迁移，不 pin 版本号）。
- 代码：`internal/execution/{execution,store,service,cancel,cancel_store,children_hooks}.go`、
  `internal/children/hook.go`、`internal/api/server.go`；无非契约 REST 变更。
- 兼容：顶层 claim/释放行为不变；`quota_allocations` 对顶层仍只有一行；f03 既有
  claim/finalizer/waiting/cancel 套件复跑通过。
- 后续：T080 共享父预算仍以 `usage_ledger.budget_root_run_id` 为唯一账本；T082 交接可复用
  `run_children.io_mapping` 与「不默认传父历史」的封存输入；页面 child 时间线不需要改动。

## 备选与取舍

- **spawn 时占槽**：父在等待前就会因容量被 429 拒绝，无法表达「并行」语义；且与 attempt 槽
  双计数。拒绝。
- **让 child attempt 继续用 `execution_slots`**：20 个等待父 + 顶层池饱和时子任务必然饿死，
  直接违反验收 1。拒绝。
- **按 root_run_id 建每父计数**：`每父` 是委派者本身；直接父 id 更精确且与 spawn 的父锁一致。
  选直接父，报告记录。
- **在 finalizer 内直接锁父/写 decision**：锁序反转（child→parent vs parent→child）。拒绝。
- **新增 `run_children.state`/第二 child 状态机**：与 ADR 0025 D1 的「child 的 runs.state 唯
  一权威」冲突，且需要持续对齐。拒绝。
- **为恢复新建 maintenance 进程/pool**：本票用 claim 前有界 Reconcile + 提交后 hook；独立后台
  进程会扩大本票重启面，且崩溃窗口已有幂等重放入口。若后续需要跨进程常驻 sweep，可复用
  同一 `ChildLifecycle.Reconcile`。
