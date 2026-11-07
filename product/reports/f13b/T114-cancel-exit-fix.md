# F13B/T114 验收缺陷修复：取消执行中的 Run 后 worker 未确认退出（CANCEL-RACE-SLOT-LEAK）

- **状态**：passed（真实栈红→绿 + Go 单测 + 门禁；`not_run` 边界见 §7）。
- **分支**：`t/cancel-exit-fix`。基于 `22eb645`（T114 已并入的集成分支 tip），结束时 merge
  `integration/t113-t114-t122`（`a9dae57`：T114 配额表述勘误，仅文档）→ merge commit `8943aa0`。
  HEAD 即该分支 tip（本报告为最后一次 docs 提交）。
- **提交**：`cfcb868` test（先红：`cancel_exit_test.go` + `tests/f13b/test_t114_cancel_exit.py`）、
  `3413273` fix（`internal/presetexec/presetexec.go`）、`20ff04b` docs（本报告 + PROGRESS）、
  `8943aa0` merge；merge 后验证复跑见 §5。
- **缺陷来源**：T114（GitHub #115）验收 AC4 真实观测（`reports/f13b/T114-capacity.md` §3.8/§4）：
  饱和窗口内取消 6 个 executing Run，trial-1/2/3 分别有 **2/4/6** 个停在 `cancelling`、attempt 未退出、
  `finished_at` 缺失、slot 泄漏，直到租约 sweep 才变成 `recovery_pending`。
- **环境**：PostgreSQL 16.4 `127.0.0.1:55432`（隔离库，每用例新库）、Go 1.27.3、Python 3.12.2、
  profile `capacity-stub`（stub provider，**替身**服务时间 5s 旋钮，仅本缺陷复现使用；不是厂商延迟）、
  真实 `cmd/supervisor exec --once` OS 进程、真实公开 cancel API 与内部 mTLS 协议。

## 1. 缺陷与根因

**触发链（T114 观测，本次真实栈复现一致）**：

1. 执行中的 Run（graph：`start -> analyse(model, stub) -> end`）由真实 supervisor claim 并进入
   `executeGraphModel`，`InvokeCapability` 已在途（`platform.operations.state=dispatching`）；
2. `POST /api/v1/runs/{id}/cancel` → **202**，`runs.state=cancelling`、`cancel_requested_at` 落库
   （执行服务 `Cancel` 的 live-attempt 分支，`internal/execution/cancel.go`）；
3. 替身模型调用返回成功后，节点 `publishNodeData` → `uploadNodeArtifact` → `CreateUpload` 被
   `RunArtifactStore.resolveScope` 以「run 非 executing」拒绝 →
   **409 `run_state_conflict`**（`internal/execution/runartifacts.go:230`，API 映射
   `internal/api/runartifacts.go:230`）；
4. 图调度器 `commitFailure` 交回 `refuse`，失败提案 `SubmitResult` 又因 `runs.state=cancelling`
   被 **409 `run_state_conflict`** 拒绝（`internal/execution/service.go:977-979`，API 映射
   `internal/api/execution.go:359`）；
5. **缺陷点**：`internal/presetexec/presetexec.go` 的失败路径在 `SubmitResult` 报错后**直接 return**，
   从未调用 `ConfirmExit`：
   - `refuse`（修复前 L801-808）；
   - `failRun`（修复前 L838-842）；
   - `failAfterStart`（修复前 L866-871，artifact 注册/失败提案被拒时走这里）。

`ConfirmExit`（`internal/execution/service.go:455`）是唯一记录 attempt 退出并再次驱动统一 finalizer
（`finalizeRunTx`，`service.go:701`）的写入口；finalizer 的优先级是
**live child > unknown > deadline > cancel > proposal**。**没有 exit 就没有 cancel 终态**：Run 停在
`cancelling`、无 `finished_at`、attempt 保持 live、`platform.quota_counters.used` 不释放。

**为什么 T036 的确定性探针是绿的**：claim → 公开 cancel → 主动 `exit` → `cancelled` 证明 finalizer/API
语义正确；缺陷只在 worker 的失败路径，即「提案被拒后没有 exit」这一跳。

## 2. 修复（最小、统一）

新增 `internal/presetexec/presetexec.go::endRefusedProposal`，三条失败路径统一走它：

- **提案被拒后仍确认 attempt exit**（`ConfirmExit`，token 仍 live 时）：finalizer 按既有优先级提交
  `cancelled`（或 deadline/unknown 的既有结果）并释放 slot；
- exit 成功且平台提交 `cancelled` → 执行器报告 `outcome=cancelled, code=cancel_requested`，
  `Status=executed`（取消不是 worker 错误，supervisor 退出码 0）；
- exit 成功但 Run 不是 `cancelled`（deadline/终态/waiting 等）→ 报告 `failed` + 可读诊断
  （原失败码 + 提案被拒原因 + "attempt exit was confirmed"）；
- exit 被拒（`run_terminal`/`stale_attempt`/token 已死）→ 安全返回：只尝试一次，不重试、不 panic，
  错误文本同时含提案拒绝码与 exit 拒绝码，且**不含 attempt token**（由单测断言）。

**未改动**：finalizer 优先级、`Cancel`/`ConfirmExit`/`SubmitResult` 语义、`waiting`/`recovery_pending`
语义、契约、`docs/spec/**`、迁移。附带收益：`SubmitResult` 因 **deadline**（`deadline_exceeded`）被拒的
同一形态也被修复——此前同样直接 return、留下 live attempt；现在 exit 后 finalizer 提交
`failed(deadline_exceeded)` 并释放 slot。

## 3. 红→绿证据

### 3.1 Go 单测（seam：`Run()` 的失败路径 × 契约拒绝码）

新增 `internal/presetexec/cancel_exit_test.go`：4 条失败路径（`refuse` / `failRun` / `failAfterStart` /
graph 模型节点发布被拒）× 3 个拒绝码（`run_state_conflict` / `run_terminal` / `stale_attempt`），
断言 `ConfirmExit` **恰好 1 次**、evidence `started=true`、finalizer 提交 `cancelled` 后报告
`outcome=cancelled` 且不记录提案；另有「succeeded 提案先被拒」「exit 也被拒（stale token）」两条。
底层的 fake client 只加脚本化 `submitErr`/`exitErr`，未改任何既有断言。

- 红（`go-unit-red.log`，工作树临时恢复 `22eb645` 的 `presetexec.go`）：
  `--- FAIL: TestRefusedProposalStillConfirmsTheAttemptExit/... (exits=[] ...)`, `FAIL ... 2.206s`；
- 绿（`go-unit-green.log`）：`--- PASS` ×3 顶层用例，`ok orchara/platform/internal/presetexec 1.936s`。

### 3.2 真实栈回归（`tests/f13b/test_t114_cancel_exit.py`）

一条真实栈用例：公开 REST 播种 owner + model 应用（`capacity-stub`，`ORCHARA_CAPACITY_STUB_SERVICE_DELAY=5s`），
提交 debug Run，启动真实 `cmd/supervisor exec --once`，**等到 `platform.operations` 出现
`dispatching`**（证明 worker 正在 capability 调用内）后调用公开 cancel，再等待 worker 结束与终态。同一用例
还包含 T036 对照：queued Run 取消（intent==stop，无 attempt），以及取消后迟到 result 必须 409
`run_terminal` 且不复活。测试本身不跑 sandbox（model-only 图），但启动真实产品进程，属真实栈窗口。

**红（修复前，`realstack-red.log`）**：

- run id **`e5b01a5c-3891-465e-83fd-175ae5a89a79`**；
- cancel：`202`，`state_after_cancel=cancelling`，intent `2026-11-08T02:43:16.607086+08:00`；
- 在途 operation：cancel 时 `dispatching`，之后 `succeeded`（证明取消命中真实在途调用，而不是排空后的空档）；
- worker 报告：`status=failed, outcome=error, code=sandbox_failed,
  error="the failed proposal was refused: internal API 409 run_state_conflict: the run is not executing; ..."`；
- Run：`state_after_worker=cancelling`、**`finished_at=null`**；attempt `exited_at=null`（live）；
  **`execution_slots_used=1`**；
- `1 failed in 79.77s (0:01:19)`。

**绿（修复后，同一用例，`realstack-green.log` / `realstack-green-evidence.log`）**：

- 真实栈窗口内 `1 passed in 32.47s`；带 `-s` 的证据复跑 `1 passed in 35.81s`，同一入口同一断言。
- run id **`6f79e93b-e02c-44a6-a60a-4a2ed95dc3e6`**：
  - 在途 operation：cancel 时 `dispatching`，之后 `succeeded`（同样证明取消命中真实在途调用）；
  - cancel：`202`、`state_after_cancel=cancelling`、intent `2026-11-08 02:51:10.298562+08`；
  - Run：`state_after_worker=cancelled`、`finished_at=2026-11-08 02:51:15.152585+08`；
  - attempt：`exited_at == revoked_at == finished_at`（attempt 已退出）；
  - **`execution_slots_used=0`**；
  - supervisor：`returncode=0`、`status=executed`、`outcome=cancelled`、`run_state=cancelled`、
    `error_code=cancel_requested`，stderr 落日志
    `the sandbox_failed proposal was refused (409 run_state_conflict: ...); confirming the attempt exit`；
  - `intent_to_stop_s = 4.854`（5s 在途替身调用返回后立即确认退出；取消不能被"中断在途调用"实现，
    停顿时间由替身服务时间决定）；
  - 取消后新增 worker artifact = 0；迟到 result `409 run_terminal` 且终态仍 `cancelled`（用例内断言）。
- 决定性差异：修复前同一真实栈、同一入口（`capacity-stub` + 5s 在途调用）→ `cancelling` + slot 1；
  修复后 → `cancelled` + `finished_at` + attempt exited + slot 0。

## 4. 回归语义（未放宽）

- **finalizer 优先级未重排**：`cancel > proposal` 由既有 `finalizeRunTx` 执行；修复只补上 exit 事实。
- **迟到 result**：真实栈用例在终态后用真实 worker mTLS `submit_result` → **409 `run_terminal`**，
  `runs.state` 不变；T036 既有套件同样覆盖。
- **取消后不得新增产物/登记**：真实栈用例断言 `cancel_requested_at` 之后没有 worker 注册的
  `final_output`/`node_output`/`log` artifact（capability proxy 自身的 params/receipt seal 不算 worker 登记）。
- **queued 取消**：`cancel_requested_at == finished_at`、无 attempt（intent==stop），未变。
- **`waiting`/`recovery_pending` 语义**：未改；`pytest tests/f03/test_t036_cancel.py` **15 passed**。
- 既有断言未修改（Go 全包 `go test ./...` 0；T036 全绿）。

## 5. 门禁

| 门禁（从 `product/`） | 结果 |
|---|---|
| `go build ./...` | 0 |
| `go vet ./...` | 0 |
| `go test ./...` | 0（`presetexec` ok 5.114s，全部包 ok） |
| `gofmt -l internal cmd tools` | 空 |
| `python -m pytest tests/f13b/test_t114_cancel_exit.py -q`（修复后） | **1 passed in 32.47s**（窗口内）；带 `-s` 证据复跑 **1 passed in 35.81s** |
| 同一用例在修复前（红） | **1 failed in 79.77s**（run `e5b01a5c-...` 停 `cancelling`、`finished_at` NULL、slot=1） |
| `python -m pytest tests/f03/test_t036_cancel.py -q` | **15 passed in 158.66s** |
| `python -m pytest tests/f13b/test_t114_capacity.py -q`（短 profile） | **2 passed, 1 skipped in 155.31s**（短 profile + 已提交证据校验；full 用例按设计 skip） |
| `python tools/sync_spec.py --check` | `no drift` |

修复后二进制在真实栈窗口内一次通过；红/绿是同一测试、同一 `capacity-stub` 5s 在途入口，仅生产
`presetexec.go` 不同（红跑显式 checkout `22eb645` 的文件，随后恢复 HEAD）。

**原始 tail**（完整日志见 §5 末尾路径）：

```text
# go-gates.log
build exit=0
vet exit=0
ok  	orchara/platform/internal/presetexec	5.114s
test exit=0
gofmt exit=0            # gofmt -l internal cmd tools 输出为空

# realstack-red.log（修复前）
1 failed in 79.77s (0:01:19)
pytest exit=1

# realstack-green.log（修复后）
.                                                                        [100%]
1 passed in 32.47s
pytest exit=0

# realstack-t036.log
...............                                                          [100%]
15 passed in 158.66s (0:02:38)
pytest exit=0

# realstack-capacity-short.log
.s.                                                                      [100%]
2 passed, 1 skipped in 155.31s (0:02:35)
pytest exit=0
```

**merge 后复跑**（`git merge integration/t113-t114-t122` → merge commit `8943aa0`；merge diff 仅
`product/PROGRESS.md` 与 `reports/f13b/T114-capacity.md`，无代码/测试改动）：

```text
# go-gates-postmerge.log
build exit=0 / vet exit=0 / test exit=0 / gofmt exit=0（空）
no drift: spec copy and contract snapshot match the manifest

# postmerge-cancel-exit.log
1 passed in 36.26s
pytest exit=0
# run 8d95d2af-31b7-4484-ac30-a608f212231b: cancelled, finished_at, attempt exited,
# execution_slots_used=0, intent_to_stop_s=5.266, executor outcome=cancelled

# postmerge-t036.log
15 passed in 151.39s (0:02:31)
pytest exit=0
```

`test_t114_capacity.py` 短 profile 在 merge 前同一代码上通过；merge 未触及容量/执行路径，未再复跑
（`not_run`：merge 后未重复容量门禁）。

原始日志（仓库外）：`E:\Orchara\.scratch\f13b\cancel-fix\`：
`go-gates.log`、`go-unit-red.log`、`go-unit-green.log`、`realstack-red.log`、`realstack-green.log`、
`realstack-green-evidence.log`、`realstack-t036.log`、`realstack-capacity-short.log`、
`run-realstack-window.ps1`。

## 6. AC 对照（T114 缺陷修复）

| 要求 | 结果 |
|---|---|
| 红：执行中取消命中在途 capability → 卡 `cancelling`、无 `finished_at`、attempt live、slot 未释放 | 真实栈红跑：run `e5b01a5c-...` 全部观测到（§3.2） |
| 绿：同一入口 → 终态 `cancelled` + `finished_at` + attempt exited + slot 归零 | 真实栈绿跑通过（§3.2） |
| 所有 `SubmitResult`/能力上报被拒的失败路径统一「确认 exit 或证明 token 失效」 | `endRefusedProposal` 三个 helper 统一；Go 单测矩阵覆盖 3 个拒绝码 |
| 迟到 result 409 `run_terminal` 且不复活 | 真实栈用例 + T036 套件 |
| 取消后不得新增产物/登记 | 真实栈用例断言 0 |
| finalizer 优先级不重排 / 不改契约、规格、迁移 | 未改 |
| 不把租约 sweep → `recovery_pending` 当修复 | 修复走 cancel 语义；sweep 未参与本用例（无租约过期） |

## 7. 边界与 `not_run`

- **exit 确认本身的瞬时失败**（transport 5xx / `database_writes_in_flight` 之外的 409）：修复只保证
  「提案被拒后仍调用一次 exit」，不为 exit 增加有界重试；若 exit 请求本身瞬断，仍可能留 live attempt
  到租约过期（`not_run`：未构造该故障注入；exec 协议当前不发 `checkpoint_connections`，
  `database_writes_in_flight` 守卫在本栈不可达）。
- **取消与 claim/exit 的并发窗口**（cancel 恰在 claim 事务/exit 提交之间）：由既有 T036/T038 语义覆盖，
  本票未新增注入（`not_run`）；本次只修「提案被拒后 worker 返回」。
- **`waiting_approval`/`waiting_children` 期间取消**：T036 既有套件覆盖（15 passed），本票未改。
- **unknown operations / 人工 reconciliation**：未涉及（model-only 图无 external write），`not_run`。
- **F13B/T107 FAULT-05**（旧 supervisor 租约过期后提案被拒场景）：本窗口与 T122 merge 的 f06 套件并发
  （ENV-B2 §2.1 两路并发；该用例读 WSL rootfs digest，属独占类），故记为 `not_run`；影响面分析：
  其断言 `report.status == failed` 且报告含 `stale_attempt`，修复后的组合诊断仍满足（exit 被拒 →
  `status=failed`、文本含 `stale_attempt`），但未在本票真实执行。
- **真实厂商模型 / 真实容量机器 / GPU / 多机**：`blocked`（无凭据/端点），沿用 T114 边界；本缺陷复现
  仅用已交付的 `capacity-stub` 替身服务时间旋钮。
