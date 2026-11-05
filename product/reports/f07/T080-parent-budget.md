# F07 T080 验收：并行子调用只消耗同一份父预算（#81）

- 任务：F07 / GitHub Issue #81（票 `T080`），分支 `t/t080-parent-budget`（worktree
  `E:\orchara-worktrees\t080`，基线 `integration/t068-t082` @ `affc12c`，含 T078 的 child Run
  委托与 `platform.run_children`、T075 固定子应用校验、T074 请求/运行创建）。
- 状态：**passed**。范围 = 并行委派仍受父 Run 的**费用/次数/深度**约束、超限原因回到父 Run、
  恢复与备用模型不重置额度。child Run 协议（`spawnChild` 四字段 body、202 `Run`）不变；金额全程
  十进制（`numeric(20,8)` + `big.Int`），无 float 列、无第二个账本。
- 边界（如实）：child 终态的**自动**恢复 hook 与独立 child 槽属 T081；本票父收敛用例与 T078 一样
  使用既有 T035 `waiting-resolution` bridge 提供 children 决议（生产自动路径未实现，报告 §5 明示）。
  浏览器 DOM、真实厂商模型未跑（dev-stub），全量 `pytest tests` 按 BRIEF 不跑。
- 迁移：**无**。复用 0028 的 `platform.usage_ledger`、0014 的 `runs.parent_run_id` 与 0049 的
  `platform.run_children`；T078 的 0048 在集成分支因与 T073 撞号已由 `affc12c` 顺延为 0049，
  本分支直接合入该修复，不新增 schema。ADR：`docs/adr/0031-f07-parent-budget.md`。
  未改 `docs/spec/**`/`contracts/**`，`sync_spec --check` no drift。

## 0. 工作记录（简短）

1. 读 BRIEF、票 #81、`notes/f07-delegation.md` §3.5/§4/§6/§10、T078 报告与 ADR 0025、`spawnChild`
   契约、`internal/budget`、`internal/children`、`internal/capabilities`（预留/结算/备用）、
   `internal/presetexec`（call_app 拒绝码 → 父 Run 错误码）与既有测试支撑。
2. 以真实栈确认基线缺口：T078 的 spawn 用**未加根锁**的私有递归读取根额度；父额度耗尽仍可
   新建 child（延迟到 child 第一次模型调用才失败）；除 T078 的 Go 单测外没有真实栈证据证明
   并行 child 的预留/次数/深度共享同一父额度，也没有恢复/备用不重置额度的证据。
3. TDD：先写 `tests/f07/test_t080_parent_budget.py`（7 用例）+ `tests/support/t080.py`；确认
   “零额度仍 spawn”为红（202 vs 429），再实现 Go seam 与准入；随后逐片转绿并复跑。
4. 门禁、报告、PROGRESS、ADR；合并 `integration/t068-t082` 后复跑（§3）。

## 1. 交付

### 1.1 `internal/budget`：`LockAllowanceTx`（只读共享额度 seam）

```go
type Allowance struct { RootRunID, Limit, Currency, Charged, Outstanding, Remaining string; Overrun bool }
func (l *Ledger) LockAllowanceTx(ctx context.Context, tx *sql.Tx, runID string) (*Allowance, error)
```

与 `ReserveTx` 完全相同的顶层祖先解析与根 Run 行 `SELECT … FOR UPDATE`，然后 `ledgerTotalsTx` +
`addAmounts`/`remainingOf`：`Remaining = limit-(charged+outstanding)` 钳零，`Overrun` 单独标记。
不写任何行、不建第二条记账路径；并用既有 `CompareAmounts`（`big.Int` 单位）比较，无 float。

### 1.2 `internal/children.Spawn`：委派时原子冻结共享额度

顺序保持 T078 的 parent→version→封存→child，只把额度段前移并加锁：

1. 父 Run `FOR UPDATE` → live attempt → **幂等预读**（重放先于额度读取，恢复中的父任务不会被
   等待期间花掉的预算搁浅）；
2. 版本/闭包/mapping/input schema 校验 → 深度/次数（仍是父锁内按 `run_children` 行与祖先链计算）；
3. **`LockAllowanceTx`**：在根 Run 行锁内读同一份额度；
   - 剩余为 `0` → `429 budget_exhausted`，`details.remaining_amount/currency`，整笔回滚（不写
     Run/收据/输入 artifact；封存输入在该判定之后，因此拒绝不产生孤儿对象）；
   - 否则 child `runs.budget_limit/currency` = 根 Run 的 `budget_limit/currency`（同一份额度；
     取代 T078 的私有递归读）；
4. 封存最小映射输入 → 子版本 published snapshot + child Run + queued 事件 + start job + input
   link + `run_children` 收据。

没有新增 per-child 预算行/预留信封：那会在根账本里把同一笔真实花费重复计入（信封 + 逐次预留），
或迫使账本分叉出子账本，违反“同一份父预算”与切片边界（ADR 0031 D1）。实际费用仍由 child 的每次
模型调用在 `ReserveTx` 里按 `budget_root_run_id` 预留/结算，并发由根 Run 行锁串行化。

### 1.3 超限面（全部是稳定平台码，回到父 Run）

| 场景 | 面 |
|---|---|
| 父额度无剩余 | spawn 429 `budget_exhausted` → 父 Run `error_code=budget_exhausted` |
| child 次数用尽 | spawn 429 `child_calls_exceeded`（`used`/`max_child_calls`）→ 父 Run 同码 |
| 运行期深度越界 | spawn 422 `child_depth_exceeded`（`depth`/`max_child_depth`）→ 父 Run 同码 |
| child 自己的模型预留放不下 | child Run `failed`/`budget_exhausted`（无预留行）→ required-child 收敛后父 Run 同码 |

`presetexec/call_app` 的既有 `childFailureCode` 已把内部 API 的稳定码映射为父 Run 错误码；本次未
改执行器。

### 1.4 恢复与备用

- 恢复只处理 job/attempt/slot；`orchara-admin runs recover` 前后 `usage_ledger` 行逐行相同、
  根剩余相同、child 的 `runs.budget_limit` 相同；重 claim 后同尺寸预留仍 429。
- 备用模型（`stub-unavailable-primary` + 兼容备用 profile）在 child 内真实执行：主模型失败尝试
  各自释放自己的预留，备用在同根预留/结算，只有 1 次 charge，剩余 = `limit - charge`。

## 2. 测试与证据

新增 `tests/f07/test_t080_parent_budget.py`（7 用例）与 `tests/support/t080.py`；全部真实栈：真实
PG 16.4、编译 `cmd/api`/`cmd/admin`/`cmd/maintenance`/`cmd/supervisor`、真实 HTTP + worker mTLS、
真实 `spawnChild`、真实 supervisor 执行、真实对象字节、真实 `orchara-admin runs recover`。并发门是
真实 PostgreSQL 行锁（`tests/support/t046.RunRowGate`：一个真实 psql 会话对预算根 Run 行持
`FOR UPDATE`，并轮询 `pg_stat_activity` 确认所有请求真的在同一决策点等待后再放锁），不以 sleep
定胜负。

| 用例 | 证明 |
|---|---|
| `test_concurrent_child_model_calls_cannot_exceed_the_shared_allowance` | 同一父的两个 child 各自 claim 后并发发起真实模型调用（根行锁门）：恰好一个 200、一个 429 `budget_exhausted`（required= 预留、remaining<required、currency=USD）；loser 零 `usage_ledger` 行、零 attempt，winner 的 reserve/release/charge 全部 `budget_root_run_id`=父 Run；`usage_ledger.amount`/`runs.budget_limit` 为 `numeric(20,8)`，金额文本 `^\d+\.\d{8}$` |
| `test_concurrent_spawns_consume_the_child_call_allowance_atomically` | 父 Run 行锁门下 3 个并发 `spawnChild`（`max_child_calls=2`）：恰好 2×202 + 1×429 `child_calls_exceeded`（used=2/max=2）；`run_children` 2 行、`runs` 恰 +2、`run_input` artifact 恰 +2，loser 的 logical key 无行；每个 child 的 `budget_limit/currency` = 父 Run 的共享额度、depth=1；spawn 不写账本行 |
| `test_depth_is_measured_from_the_real_ancestor_chain` | root→child（depth1，合法）→ 从 child 再委派 grandchild：真实祖先链 depth=2 > 该 Run 冻结的 `max_child_depth=1` → 422 `child_depth_exceeded`（details depth=2/max=1），零 Run/收据/artifact 行 |
| `test_a_parent_with_no_remaining_allowance_refuses_a_new_child` | 父 `budget_limit=0`：新建 child 429 `budget_exhausted`（remaining=0、currency=USD），零 `run_children`、零账本行 |
| `test_an_unaffordable_child_returns_budget_exhausted_to_the_parent` | 父额度 1 单位（`0.00000001`）：spawn 允许且 child 冻结同额度；真实 supervisor 执行 child → 模型预留 429 → child `failed`/`budget_exhausted`（无预留行、无 output）；T035 bridge 决议 → 父 resume → 重放同一 child 读取失败原因 → 父 `failed`/`budget_exhausted`，`run.status` 事件同码；无第二个 child |
| `test_recovery_preserves_the_consumed_allowance` | child 真实模型调用结算后：`force lease expiry` → 真实 `runs sweep-leases` → 真实 `runs recover`：账本逐行/根剩余/child 冻结上限不变、`run.status` 回到 `queued`；重 claim 后同尺寸预留 429、remaining=`limit-charge`，无第二次 charge |
| `test_a_child_fallback_is_not_a_new_allowance` | child 内主模型不可用 + 兼容备用：备用成功（receipt 的 `fallback.profile_version_id`），全部 reserve/release/charge 落在同一父 Run 根；reserve=release≥2、charge=1、outstanding=0、remaining=`limit-charge` |

Go 单测：`internal/budget/decimal_test.go`（既有，单位/比较/格式化）保持全绿；新的 `LockAllowanceTx`
与 spawn 准入是 DB 事务行为，由上述真实栈用例直接覆盖（与 T046/T078 的覆盖方式一致，未用 mock）。

## 3. 门禁与原始输出

环境 profile：Windows 宿主机；Python 3.12.2；Go 1.27.3 windows/amd64
（`E:\orchara-toolchains\go`）；Node v24.13.0（本票未用页面逻辑）；PostgreSQL 16.4 用户态集群
`127.0.0.1:55432`（每用例 `stack.create_database` + 真实 `cmd/admin migrate`，迁移链 0001–0049
连续）；`dev-stub` profile。所有 pytest 均带 `--basetemp=E:\orchara-tmp\t080-*`（报告末已删除）。

### 3.1 `pytest tests/f07`（最终树，两轮背靠背）

```
run1 (--basetemp=E:\orchara-tmp\t080-13):
..............................                                           [100%]
30 passed in 442.55s (0:07:22)

run2 (--basetemp=E:\orchara-tmp\t080-14):
..............................                                           [100%]
30 passed in 384.54s (0:06:24)
```

（30 = T075 12 + T078 11 + T080 7。开发迭代中 T080 单文件曾 `7 passed in 129.01s`；最终树以其后
的两次全量 f07 为准。）

### 3.2 邻接回归

```
pytest tests/tooling -q --basetemp=E:\orchara-tmp\t080-10
  21 passed in 6.81s

pytest tests/f06/test_t046_budget.py tests/f06/test_t046_pages.py -q --basetemp=E:\orchara-tmp\t080-11
  12 passed in 150.23s (0:02:30)

pytest tests/f03/test_t029_claim.py tests/f03/test_t035_waiting.py tests/f03/test_t036_cancel.py -q --basetemp=E:\orchara-tmp\t080-12
  26 passed in 398.49s (0:06:38)
```

### 3.3 Go 与契约（最终树）

```
go build ./...     -> 退出码 0，无输出
go vet ./...       -> 退出码 0，无输出
go test ./...      -> 全部包 ok（internal/{budget,children,capabilities,execution,presetexec,api} 等），无 FAIL
gofmt -l .         -> 无输出（干净）
python tools/sync_spec.py --check -> no drift: spec copy and contract snapshot match the manifest
```

### 3.4 合并

基线 `affc12c`（含 orchestrator 的 0048→0049 修复）；`git merge integration/t068-t082` 在门禁时
为 `Already up to date`。合并/最终提交见 PROGRESS 行与本报告的提交说明；最终树 `git status --short`
为空，basetemp 已删除。

## 4. AC 对照

| 验收项 | 结论 | 证据 |
|---|---|---|
| 费用/次数/深度按父预算原子计算 | **passed** | 费用：根 Run 行锁下的并发预留（§2 用例 1）+ 委派时 `LockAllowanceTx` 冻结共享额度；次数：父锁内 `run_children` 行数（用例 2）；深度：父锁内祖先链测量（用例 3）；金额列 `numeric(20,8)`，无 float |
| 并发 child 不超预留；不能预留时有类型化码回父、无部分预留泄漏 | **passed** | 用例 1（恰好一个 429 `budget_exhausted`；loser 零账本行/零 attempt）；用例 4（零额度 spawn 429）；用例 5（child 模型预留 429 → child/父同码，无预留行）；Go 侧拒绝在整笔事务中回滚 |
| 恢复和备用不重置额度 | **passed** | 用例 6（真实 `runs recover`：账本逐行/根剩余/冻结上限不变，后续预留仍 429，无二次 charge）；用例 7（备用同根、1 次 charge） |
| 超限结果能回到父任务 | **passed** | 用例 2/3/4 的 spawn 拒绝码即父执行器错误码（`presetexec/call_app.childFailureCode`，T078 单测覆盖映射）；用例 5 真实 supervisor 下 required-child 失败以 `budget_exhausted` 收敛父 Run，`run.status` 事件同码；重放不重建 child |
| 原 child Run 协议不变；金额不用 float | **passed** | `contracts/openapi.json` 未改（`sync_spec` no drift）；spawn body/响应/状态码不变；§2 用例 1 断言 money 列类型与金额文本 |
| 复用既有账本与 `run_children`，不新增第二记账路径/float 列 | **passed** | 无迁移；`internal/budget` 仅加只读 seam；`usage_ledger` 是唯一记账；ADR 0031 D1 记录拒绝 per-child 信封的理由 |
| 端到端演示 + 失败/授权/重试边界断言 | **passed** | §2 全部 7 用例：真实 mTLS spawn、真实 supervisor 执行、真实 admin recover、真实并发门；失败码/无部分写入/重放不重建均有断言 |
| 记录版本/输入 hash/命令/原始结果/环境；stub/real 分报 | **passed** | 本报告 §3；`run_children.input_sha256`/schema sha（T078）保持不变；stub=dev-stub 模型，其余全部 real |

## 5. `not_run` / 边界（如实）

1. **child 终态的自动恢复 hook / 独立 child 槽 / 每父并行上限**：F07/T081。用例 5 的父收敛使用既有
   T035 bridge 提供 children 决议；生产自动路径仍未实现（与 T078 报告一致）。
2. **浏览器 DOM**：not_run；本票无页面改动（父 Run 的错误码/状态与 T046 预算面板是既有 UI）。
3. **真实厂商模型**：沿用 dev-stub；费用/预留路径与 T046 相同，未对真实账单做实验。
4. **全量 `pytest tests`**：BRIEF 明确不跑；已跑 f07×2、tooling、f06 T046 预算、f03 claim/waiting/cancel。
5. **迁移**：无新迁移；T078 的 0048→0049 重编号由集成分支 `affc12c` 提供，本分支只合入该修复。

## 6. 给后续票的接口

- `budget.Ledger.LockAllowanceTx`：只读共享额度（根 Run 行锁 + 十进制 remaining/overrun）。后续票
  若要在委派/等待前做额度判断，用这个 seam，不要再写私有递归读。
- `children.Spawn` 的锁序固定为 parent→（version/seal）→**budget root**→child 写入；T081 的
  child 终态 hook 若也要动预算根，必须保持“run 行 → 根行”的顺序，或在终态提交后独立事务执行。
- `run_children` 收据/`depth` 与 `runs.budget_limit`（= 根额度）是 T081 等待/取消收敛的输入；
  本票不新增状态列、不建 child 槽。
- 超限码：`budget_exhausted`（spawn 与 child 内模型预留）、`child_calls_exceeded`、
  `child_depth_exceeded`；父执行器已把它们映射为父 Run 的 `error_code`。
- 无新契约/REST 表面：`spawnChild` 不变；`budget_exhausted` 的 429 是契约既有状态码。

## 7. 参考

- ADR：[0031 — F07/T080 并行子调用共享同一份父预算](docs/adr/0031-f07-parent-budget.md)
- 契约：`spawnChild`（`contracts/openapi.json`）；`usage_ledger`（0028，`budget_root_run_id` 顶层祖先）；
  EXECUTION.md:66（每次模型调用预留/结算、多并发共享父预算）、:7（创建 Run 时同事务固定预算）、
  :72（父调用键幂等）；IMPLEMENTATION-DETAILS.md:69（恢复不能重新获得一整份预算/轮数）；
  OPERATIONS.md:28（子调用深度 3/总数 12/每父并行 3）。
- 依赖：T046 预算账本/结算/备用、T078 child Run/`run_children`/`call_app` 拒绝码映射、
  T075 固定子应用闭包、T035 waiting bridge（T081 替换）。
