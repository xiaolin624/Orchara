# F06/T046 — 共享十进制预算预留与实际用量结算

- Ticket: T046 / GitHub issue #47（父 F06「并行模型调用共享预算并按实际用量结算」）
- Branch: `t/t046-budget`，base `2e78893`（T045 已合入）
- 实现提交：`f73222f`（迁移 0028 + `internal/budget` 账本）、`803a11d`（capability 集成 + owner 投影 + 页面 + 测试）、
  `1581150`（fallback 预算门禁前先释放失败尝试的预留；对账幂等返回已记录金额）
- 证据提交：本报告/`PROGRESS.md`/ADR D13
- 状态：**passed**（范围见 §7；真实厂商 adapter / 浏览器 DOM / 全量套件为 `not_run`）

## 1. 范围与设计

一次 Run 树共享一份十进制预算。`platform.usage_ledger` 是唯一权威；
`runs.budget_limit`/`runs.currency` 是预算根上的限额与币种；
`operation_attempts` 仍是每次物理调用的投影，但不是金额的唯一记录。

账本算术（`internal/budget` 与迁移注释一致）：

```
outstanding = sum(reserve) - sum(release)
charged     = sum(charge)
consumed    = charged + outstanding
remaining   = max(0, budget_limit - consumed)
overrun     = consumed > budget_limit
```

- **原子预留**：`ReserveTx` 在预算根 run 行上 `SELECT ... FOR UPDATE`（`budget_root_run_id`
  由 `runs.parent_run_id` 递归解析，F04A debug run 自为根），同一事务内比较
  `budget_limit - consumed` 后插入 `reserve`。并发调用因此串行；放不下返回 typed
  `ExhaustedError`，HTTP 映射 429 `budget_exhausted`，details 带
  `remaining_amount/required_amount/currency`，**不写 attempt、不调 provider**。
- **实际结算**：provider 返回 usage 时，按 profile 的 price version 计算
  `charge = tokens_in*in_per_million/1e6 + tokens_out*out_per_million/1e6`（半进位），
  同一事务写 `charge` + 该预留的整笔 `release`，并更新 attempt 的
  `charged_amount/tokens/price_version/settlement_state='settled'`。
- **失败/取消**：`release` 整笔预留，`settlement_state='released'`，不产生 charge。
- **缺 usage**：provider 成功但没有 usage 对象时，保留保守预留，attempt 记
  `settlement_state='usage_missing'`、`charged_amount=NULL`；maintenance-only
  `POST /internal/v1/runs/{id}/capability-settlements` 后续对账（charge + release，
  幂等，重复对账 `already_settled=true`）；worker 不能自报费用（403）。
- **十进制/价格版本**：`math/big.Rat` + 非负十进制字符串；预留向上取整（保守），实际费用
  半进位，`numeric(20,8)` 溢出拒绝而非静默舍入。价格版本为
  `price-v1:<profile_id>:in=<输入价/百万>:out=<输出价/百万>:<currency>`。
  profile 未配置 `price` 时是零价 dev stub（预留/费用为 0，纯调用不耗尽预算）；
  配置了与 run 不同的币种 → 422 `budget_currency_mismatch`。
- **重试/备用/流式**：每次物理 attempt（含 3 次主重试、1 次 fallback、每个流式 generation）
  各自 `reserve → release|settle`，全部落在同一预算根；失败 attempt 先释放，fallback 的
  预算门禁看到的是真正空闲的额度；fallback 不是新预算。
- **回放/恢复**：复用 T041 的 operation idempotency（operation id + logical key + params hash）；
  回放直接返回已 sealed 结果，不经过 `beginModelAttempt`，因此不二次预留、不二次收费；
  `entry_key` 是第二道幂等防线。
- **预算拒绝与产物**：拒绝发生在 provider 之前；operation 记为 `failed` +
  `last_error_code='budget_exhausted'`，此前已 succeeded 的 operation/receipt artifact
  保持 `ready` 且 owner 账本仍可见。
- **owner 投影/页面**：`GET /api/v1/runs/{id}/capabilities` 增加
  `budget{budget_limit,reserved_amount,charged_amount,remaining_amount,currency,overrun}`；
  `/runs` 增 `#budget-status` 与 `describeRunBudget`/`budgetStatus` 纯 helper，十进制串原样显示；
  `overrun=true` 时明确渲染“已超支：运行不能被呈现为成功”。

### 迁移与边界

- 新迁移 `db/migrations/0028_usage_ledger.sql`：创建契约表 `platform.usage_ledger`
  （契约列逐字，含 `entry_key UNIQUE`、`kind` CHECK、`amount numeric(20,8)>=0`、
  `tokens_in/out`、`price_version`、`created_at`），索引 `(run_id,created_at)`、
  `operation_id`、`budget_root_run_id`；加法列
  `operation_attempts.settlement_state`（`none|reserved|settled|released|usage_missing`）；
  与 0007–0027 相同的 `orchara_worker`/PUBLIC 权限边界；所有偏差写进表/列 COMMENT。
- 未改 `docs/spec/`、`contracts/`、迁移 0001–0027；未 push、未关 issue。

## 2. 对 T047/T050 的精确接口

实现位置：`product/internal/budget`（包名 `budget`）。

```go
type Ledger struct{ DB *sql.DB }
func New(db *sql.DB) *Ledger

type Price struct { Currency, InputPerMillion, OutputPerMillion string }
func ZeroPrice(currency string) Price
func ParsePrice(price Price) (Price, error)
func PriceVersion(profileVersionID string, price Price) string
func EstimateReservation(price Price, inputTokens, maxOutputTokens int64) (string, error) // ceil 8dp
func ActualCharge(price Price, tokensIn, tokensOut int64) (string, error)                 // half-up 8dp
func ValidAmount(value string) bool
func CompareAmounts(left, right string) (int, error)
func AddAmounts/SubtractAmounts(...)

type ReserveRequest struct {
    RunID, OperationID, AttemptKey string
    Amount, Currency, PriceVersion string
    TokensIn, TokensOut            *int64
}
type Reservation struct { EntryKey, RunID, BudgetRootRunID, Amount, Currency, PriceVersion, RemainingBefore string }
func (l *Ledger) Reserve(ctx, ReserveRequest) (*Reservation, error)
func (l *Ledger) ReserveTx(ctx, *sql.Tx, ReserveRequest) (*Reservation, error)

type Settlement struct { AttemptKey, Amount, Currency, PriceVersion string; TokensIn, TokensOut *int64 }
type SettlementResult struct { ReservedAmount, ChargedAmount, ReleasedAmount, Currency string; AlreadySettled bool }
func (l *Ledger) Settle(ctx, Settlement) (*SettlementResult, error)
func (l *Ledger) SettleTx(ctx, *sql.Tx, Settlement) (*SettlementResult, error)

func (l *Ledger) Release(ctx, attemptKey string) (*ReleaseResult, error)
func (l *Ledger) ReleaseTx(ctx, *sql.Tx, attemptKey string) (*ReleaseResult, error)

type Totals struct { BudgetRootRunID, BudgetLimit, Currency, Reserved, Charged, Released, Remaining string; Overrun bool }
func (l *Ledger) Totals(ctx, runID string) (*Totals, error)
func AttemptKey(operationID string, attemptNo int) string // "<operation>#<attempt>"
```

错误（用于 typed 映射，不要用字符串匹配）：
`*budget.ExhaustedError{Required,Remaining,Currency}`、
`*budget.CurrencyMismatchError{RunCurrency,PriceCurrency}`、
`*budget.NoReservationError{AttemptKey}`、`budget.ErrEntryConflict`、`budget.ErrSettled`。

capability 层已提供可复用的公开入口（T047/T050 不应复制价格/预留逻辑）：

```go
// internal/capabilities
func (s *Service) ReconcileUsage(ctx, role, runID string, ReconcileRequest) (*reconcileResult, *Error)
type ReconcileRequest struct { OperationID string; AttemptNo int; Amount, PriceVersion string; TokensIn/Out *int64 }
type LedgerBudget struct { BudgetLimit, Reserved, Charged, Remaining, Currency string; Overrun bool }
func (s *Service) BudgetFacts(ctx, ownerID, runID string) (*LedgerBudget, *Error)
```

内部非契约端点（maintenance mTLS，权限 `maintenance`）：
`POST /internal/v1/runs/{run_id}/capability-settlements`
body `{operation_id, attempt_no, amount, price_version?, tokens_in?, tokens_out?}`。

**T047/T050 约定**：任何新的计费路径必须走 `ReserveTx`/`SettleTx`/`ReleaseTx` 并在
`platform.operations` 下产生 `platform.usage_ledger` 行；不要直接改
`operation_attempts.reserved_amount/charged_amount` 作为金额权威；新币种不得混入同一 run
（预算根币种固定）；新计费种类若需要“非预留的已知费用”，应新增 `charge` 行而不是新表。

## 3. 测试与证据（真实栈，非 mock）

`tests/support/t046.py` 提供：价格/预留/费用的独立 Decimal 真值、账本读取、attempt 投影、
maintenance 对账调用，以及 **DB 侧并发门** `RunRowGate`：真实 `psql` 会话持
`platform.runs` 行 `FOR UPDATE`，两个真实 HTTP 请求在线程中同时发出，测试轮询
`pg_stat_activity` 直到两者都 `wait_event_type='Lock'` 再提交，**不用 sleep 决定胜负**。

`tests/f06/test_t046_budget.py`（7 用例）：

1. `test_concurrent_calls_cannot_exceed_the_parent_budget` — 预算恰为一份预留；两个并发调用
   经 DB 行锁门，一个 200、一个 429 `budget_exhausted`（remaining=0），账本只有 1 条 reserve
   且 `sum(reserve)=budget`；输家 operation failed、0 个 attempt。
2. `test_actual_usage_settles_price_version_and_releases_the_reserve` — reserve/charge/release
   三行、金额与 attempt 投影精确相等，price version、tokens 与 owner `budget` 投影一致。
3. `test_missing_usage_is_retained_and_later_reconciled` — `stub-usage-missing*` 成功但无 usage：
   只保留 reserve；worker 对账 403；maintenance 对账 charge+release、attempt settled；
   重复对账幂等；所有金额 >= 0。
4. `test_retries_and_fallback_share_the_same_parent_budget` — 主 3 次失败各自 release，
   兼容 fallback 成功 charge；4 reserve/4 release/1 charge，全部同一 `budget_root_run_id`。
5. `test_replay_does_not_reserve_or_charge_twice` — 同一 operation 回放仍只有 1 reserve/1 charge。
6. `test_budget_refusal_keeps_already_confirmed_artifacts` — 第一次成功 artifact 保持 ready；
   第二次 429，operation failed budget_exhausted、0 attempt、0 ledger 行。
7. `test_large_price_and_token_counts_stay_exact` — 价格 `999999999999.99999999`/百万、
   max_output=1,000,000：reserve 精确等于 numeric(20,8) 上限值，charge 与剩余额度无浮点漂移。

`tests/f06/test_t046_pages.py`（3 用例）：编译后的 `/runs` 与 `/assets/runs.js` 含
`#budget-status`/`describeRunBudget`/`budgetStatus`；Node 执行 helper 验证十进制串原样显示、
overrun 标记“已超支”、缺 budget 显示不可用。

回归调整（测试代码，语义随 T046 演进）：
- `tests/support/t041.py`：owner 账本响应允许 `budget` 键；attempt 投影读 `settlement_state`；
  新增 `ledger_budget`。
- `tests/f06/test_t045_fallback.py::test_budget_exhaustion_blocks_the_fallback`：T046 起预算按真实
  价格版本预留；备用 profile 改为高价（其保守预留放不进 0.01 预算），主 profile 的 3 次尝试
  仍真实发生并返回 `provider_transient`，保持 T045 原意。

## 4. stub / real 分报

- **真实**：PostgreSQL 0001–0028 迁移链、编译 `cmd/api`、真实 worker/maintenance mTLS
  证书、真实 HTTP/SSE、真实对象存储字节、真实 run 行锁并发门（两个并发 HTTP 调用）。
- **stub**：模型 provider 仍是 `provider/config.ProfileDevStub` 的确定性 dev stub；usage 与故障
  都由注册的 `model_id` 前缀驱动（新增 `stub-usage-missing*` 用于缺 usage 路径）。
- **not_run**：真实厂商 adapter 的 usage/计费、浏览器 DOM 驱动、跨父/子 run 预算的真实
  e2e（递归解析预算根在代码与迁移层有证据，但没有真实子 run 场景）、全量 `pytest tests`
  与 f00b/f03/f05。

## 5. 门禁

环境：Windows，PG 127.0.0.1:55432，`GOCACHE=E:\orchara-toolchains\gocache`，
`GOMODCACHE=E:\orchara-toolchains\gomodcache`，Go `E:\orchara-toolchains\go\bin\go.exe`。
每个 Python 套件串行运行（不同时跑两个套件）。

```text
go vet ./...            -> exit 0（无输出）
go test ./...           -> 25 ok / 0 FAIL / 9 no test files（新增 internal/budget 包）
gofmt -l internal cmd tools -> 空（0 行）
pytest tests/f06 -q     -> 106 passed in 523.91s (0:08:43)
pytest tests/f02a -q    -> 104 passed in 689.15s (0:11:29)
pytest tests/tooling -q -> 21 passed in 6.15s
python tools/sync_spec.py --check -> no drift: spec copy and contract snapshot match the manifest
```

`tests/f06` 包含 T046 的 10 个用例（7 真实栈 + 3 页面/Node），并回归通过 T039–T045 的既有用例。
第一次全量 f06 运行出现过一次 `test_t043_agent_node.py::test_agent_chain_runs_the_preset_tool_and_validates_the_final_output`
失败（T043 sandbox/lease 计时 flake，T044 报告已记录同类现象）；该文件隔离重跑 1 passed，
最终全量重跑 106 passed。

## 6. 未运行/边界

- 真实厂商模型 adapter（含真实 usage 对象/价格）仍未编译；仅 stub 计费路径端到端。
- 浏览器 DOM 驱动未运行；页面投影以编译后资产 + Node 纯 helper 验证。
- 全量 `pytest tests`、`tests/f00b`、`tests/f03`、`tests/f05` 按本票门禁未跑。
- 跨 run 父子预算只有代码/迁移层验证，没有真实子 run 并发 e2e。
- 未 push、未关 issue；未改 docs/spec/contracts；迁移只新增 0028。
