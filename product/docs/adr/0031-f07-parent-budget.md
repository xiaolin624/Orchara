# ADR 0031 — F07/T080: 并行子调用共享同一份父预算

- 状态：accepted（T080 #81 实现记录）
- 日期：2026-11-05
- 范围：F07 委派子运行的预算/次数/深度原子性。T078 的 `spawnChild` 协议、`platform.run_children`
  收据与 `internal/budget` 账本均不变；本 ADR 只固定“一份父预算”如何被并发子调用共享，以及
  委派入口如何原子地读取它。

## 背景

`contracts/openapi.json` 的 `spawnChild` 没有预算字段；`runs.budget_limit` 是每个 Run 的冻结
上限，而 `platform.usage_ledger` 的 `budget_root_run_id` 才是记账权威（0028：`root = run_id`
沿 `runs.parent_run_id` 的最顶层祖先）。既有 T046 已实现“每次模型调用先预留、回执后结算”，
并发预留由预算根 Run 行的 `FOR UPDATE` 串行化。T078 已让所有 child Run 的模型调用把
`budget_root_run_id` 落到同一个顶层 Run，并把 sub-app 的深度/次数在父 Run 行锁内按行计算。

T080 需要回答三个未被显式固定、且很容易被实现成“第二份预算”的问题：

1. 委派时 child Run 的 `budget_limit` 应该是什么？
2. 父预算耗尽时，新建 child 是否应该被拒绝，用什么错误码返回父任务？
3. 恢复/备用模型如何保证不重置额度？

## 决策

### D1. 一份账本、一份额度：不建 per-child 预算/预留信封

child Run 不获得独立预算行，也没有 spawn 时的金额预留信封。所有费用仍由每一次物理模型调用在
`internal/budget.ReserveTx` 里按 `budget_root_run_id` 预留/结算；`runs.budget_limit`（含 child 行）
只是冻结的显示上限，账本是权威。理由：若 spawn 先为 child 预留一份“信封”，同一笔真实花费会在
根账本里被信封与逐次预留重复计入（或需要把 per-child 信封再拆成子账本），这正是“同一份父预算”
要禁止的分叉；本票的切片边界也明确要求复用既有账本、不新增第二条记账路径。

### D2. 委派时从预算根原子冻结额度（`budget.Ledger.LockAllowanceTx`）

`internal/budget` 新增只读 seam `LockAllowanceTx(ctx, tx, runID) (*Allowance, error)`：它复用
`ReserveTx` 完全相同的根解析与 `SELECT … FOR UPDATE`，在同一事务里重算
`limit - (Σcharge + Σreserve − Σrelease)`（`numeric`/`big.Int`，永不用 float）。`children.Spawn`
在父 Run 行锁之后、封存输入之前调用它：

- child Run 的 `budget_limit`/`currency` = 根 Run 的 `budget_limit`/`currency`（同一份额度；
  不再是 T078 里较早的、未加根锁的私有递归读）；
- 根额度剩余为 `0` 时拒绝新建 child：429 `budget_exhausted`（`details.remaining_amount`/`currency`），
  由 `presetexec/call_app` 映射为父 Run 的稳定错误码。这是一次“准入”判断而不是金额预留：
  它是保守规则（即使子应用全是零价模型，父额度耗尽也不再新建 child），换取父任务立即看到超限原因；
- 幂等重放发生在额度读取之前：已存在的 child 在恢复时原样返回，父任务不会因为等待期间预算被
  花掉而被自己的重放卡住。

### D3. 次数/深度仍是行，不是计数器；原子性来自父 Run 行锁

- 次数：`count(*) FROM platform.run_children WHERE parent_run_id=$1` 与父 Run 冻结
  `policy.max_child_calls` 比较；行数是权威，因此恢复/重试不重置次数。
- 深度：`depth = 1 + ancestors(parent_run_id)` 与父 Run 冻结 `policy.max_child_depth` 比较；
  嵌套委派在真实祖先链上测量，不能靠子版本自己的声明放宽。
- 两者都在父 Run 行 `FOR UPDATE` 的事务内 check-then-insert，因此并发 spawn 只能有 N 个成功，
  拒绝的那次不写 Run/收据/输入 artifact。

### D4. 恢复与备用不重置额度

- 恢复（`orchara-admin runs recover` / `sweep-leases`）只处理 job/attempt/slot，从不写
  `usage_ledger`；重新 claim 后 `runs.budget_limit` 与账本行逐字节不变，下一次预留在同一根账本
  上重新计算，已花费的额度仍然不可用。
- 备用模型复用 T045/T046 的同一路径：备用的预留/结算同样落在 `budget_root_run_id`，失败的主
  尝试各自释放自己的预留，只有一次实际 charge。备用不是新预算。

### D5. 错误面

| 场景 | 面 |
|---|---|
| 父额度无剩余 | `spawnChild` 429 `budget_exhausted`（`remaining_amount`/`currency`）→ 父 Run `error_code=budget_exhausted` |
| 子调用次数用尽 | 429 `child_calls_exceeded`（`used`/`max_child_calls`）→ 父 Run 同码 |
| 运行期深度越界 | 422 `child_depth_exceeded`（`depth`/`max_child_depth`）→ 父 Run 同码 |
| child 自己的模型预留放不下 | child Run `error_code=budget_exhausted`（无预留行）→ required-child 收敛后父 Run 同码 |

## 影响

- 无迁移：只复用 `runs.budget_limit`、`platform.usage_ledger`、`platform.run_children`；
  T078 的迁移在集成分支由 `affc12c` 顺延为 `0049`，本票不新增 schema。
- 无新契约：`spawnChild` body/响应不变；`budget_exhausted` 的 429 是契约已列出的状态码。
- `internal/budget` 的根行锁现在是委派入口/模型预留在同一棵树上的唯一串行点：并发上限是吞吐量
  上限而非正确性缺陷（notes §10 Risk 3c），本票不做“优化”。
- 零价子应用在父额度耗尽后也会被 spawn 拒绝（D2 的保守准入），是刻意的产品取舍。

## 备选与取舍

- **spawn 时为 child 预留一份 `budget_limit` 信封**：会与逐次预留重复计入根账本，或迫使账本
  分叉出子账本；违反“同一份父预算”与“不新增记账路径”的切片边界，拒绝。
- **每个 child 复制一份父 `budget_limit` 但放宽到 `remaining`**：仍无法界定并发总和，且 child
  的 `runs.budget_limit` 从来不是权威；用 `LockAllowanceTx` 冻结同一个共享上限更诚实。
- **把计数/深度做成 Redis/内存计数器**：恢复/重试会重置，违反 IMPLEMENTATION-DETAILS.md:69；拒绝。
