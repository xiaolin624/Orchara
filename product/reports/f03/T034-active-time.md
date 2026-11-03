# F03 T034 验收：跨恢复尝试累计活跃耗时并在截止时停止

- 任务：F03 / GitHub Issue #35（票 `T034`，规划 ID F03.05/F03.06），分支
  `feat/f05-f03-run-and-tools`
- 状态：**`passed`**（本票交付范围 = 跨 attempt 累计活跃耗时 + 幂等结算 + 等待/finalizing
  不计时 + 冻结活跃时限 + claim/heartbeat/result 三处截止检查 + 退出后由 T031 finalizer 提交
  `failed(deadline_exceeded)` + 运行页累计时钟/超时原因）。真实 PostgreSQL 16.4 + 真实编译二进制 +
  真实 mTLS 内部客户端（worker/supervisor/maintenance 三种证书）+ 真实 HTTP + 真实文件系统对象
  字节；无 mock、无跳过。边界（如实）：等待/resume 状态迁移属 T035，取消端点/取消仲裁属 T036，
  恢复扫描属 T038；到期后的能力/注册检查按设计由后续切片通过同一 helper 接入；浏览器 DOM 驱动
  页面仍 `not_run`（与 F01/T023/T027/T029/T031/T032/T033 相同边界）。
- 主要文件：
  - `db/migrations/0020_active_time_and_deadline.sql`（`runs.max_active_ms`、
    `run_attempts.active_ms`/`active_settled_at`、既有 Run 的 policy 回填、两个 CHECK、列注释）
  - `internal/execution/active.go`（唯一结算/投影/截止 helper：`settleAttemptActiveTx`、
    `deadlineStateOf`、`checkDeadlineTx`、`markDeadlineIntentTx`、`activeTimeFacts`、
    `deadlineExceeded`/`projectedActiveMS`）+ `active_test.go`（2 个纯规则单测）
  - `internal/execution/{store.go,service.go,execution.go}`（claim 拒绝已超时 queued Run、
    heartbeat/result/exit/finalizer 结算与截止、`ErrDeadlineExceeded`、queued 期限失败的
    job void/事件）
  - `internal/catalog/debug.go`（debug Run 创建时从冻结 policy 写 `max_active_ms`，不猜）+
    `debug_test.go`
  - `internal/api/{debug.go,execution.go}`（非契约 active 响应头、409 `deadline_exceeded` 映射）
  - `apps/web/src/{runs.html,assets/runs.js}`（累计活跃/上限、超时原因、一个时钟文案）
  - `tests/support/t034.py`、`tests/f03/test_t034_{migration,accounting,deadline,pages}.py`
    （4 文件 18 用例）
  - 既有测试适配（见 §1.6/§2.10）：T027/T029/T032/T033/T023/T025/T026/T028/T030 的迁移链尾断言；
    T027 的 run 列集合、两个已由 T029 完成的 attempt 外键、以及 T032 之后的 `next_event_seq`
    陈旧断言
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`，
    `GOCACHE=E:\orchara-toolchains\gocache`、`GOMODCACHE=E:\orchara-toolchains\gomodcache`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`tools/pg_dev.py`，真实 `cmd/admin migrate`）
  - Python 3.12.2（`python -m pytest`）、Node 24.13.0（页面 JS 语法与行为真实执行）
  - profile `dev-stub`；对象目录在测试 `tmp_path`；部署输入沿用 T025 占位值
    `ORCHARA_WORKER_IMAGE_DIGEST=sha256:aaaa…(64)`、runtime lock =
    `probes/agent_min/requirements.lock.txt`（真实 SHA-256）
  - 冻结的 golden 执行 hash（与 T025/T027/T029/T031 相同）：
    `9cc418d167377e667c77284ce7b35819c7374c0459ec90e41f4131719aef5686`
- 真实/桩分报：**全部 real**（PG/二进制/mTLS/HTTP/对象字节/Node 执行页面逻辑）。测试用 SQL 只做
  三类种子：把 `started_at` 移回过去以确定性产生区间（不 sleep）、为固定起见的短时限改写
  `max_active_ms`、以及 T031 已有的 output artifact/child Run 种子（分别属 T033/T035，尚未实现的
  注册/child 创建入口）。迁移回填测试删除 0020 的 ledger 行后由**真实 `cmd/admin migrate`** 重放
  该文件，观察真实回填，而不是断言 SQL 文本。

## 0. 工作记录（简短）

1. 读 NOTES/T034-DESIGN/票面、IMPLEMENTATION-DETAILS §8、EXECUTION §1/§6/§8、
   STATE-MACHINES §2/§6、WORKFLOW-SPEC §1、T029/T031/T032/T033 报告与
   `contracts/{openapi.json,workflow.schema.json}`、`contracts/database.sql`，确认分支干净、
   0019 为最新迁移、`runs.active_elapsed_ms` 已存在（0014）。
2. TDD 切片（红→绿）：
   - 先写 `deadlineExceeded`/`projectedActiveMS` 的 Go 纯规则测试（编译红）→ `active.go`（绿）；
   - 写迁移形状/回填测试（首跑红：0020 不存在/语法错误）→ 修 migration（绿）；
   - 写累计/幂等/等待/finalizing 测试（红：无列/无结算）→ 结算 helper + 四处接线（绿）；
   - 写截止三入口/退出/claim 拒绝/NULL 语义测试（红：无 409/无 intent）→ 实现（绿）；
   - 写页面 + Node 行为测试（红：无 detail-active/describeActiveClock）→ Web（绿）。
3. 结算唯一入口：`settleAttemptActiveTx` 在**每个离开 executing 的事务**里调用（result 提议、
   exit、finalizer、等待/取消/恢复的后续调用点），`active_settled_at IS NULL` 是幂等键；
   `runs.active_elapsed_ms` 只加一次。投影规则 `settled + (phase=executing && live attempt ?
   now-started_at : 0)`，时间全部由 PostgreSQL `now()` 计算。
4. 截止：`max_active_ms` 在 debug Run 创建时从冻结 policy 写入；claim 拒绝已超时 queued Run；
   heartbeat 记录 intent（`cancel_requested_at` 一次 + `error_code`）并返回
   `cancel_requested=true`；result 提议在结算后 at/over 则 409 `deadline_exceeded` 且不落库；
   exit 后复用 `finalizeRunTx` 提交 `failed(deadline_exceeded)`（无 output、slot 恰好释放一次）。
5. Web：`/runs` 详情新增“累计活跃 / 上限”“超时原因”两行，读取三个非契约响应头；两次 attempt 显示
   为一个累计时钟（不显示每次尝试的独立计时）。
6. 跑门禁（§3），写本报告/PROGRESS/NOTES，按 feat/test/docs 三次提交；未推送、未关 issue、
   未改 `docs/spec/` 与 `contracts/`、未改迁移 0001–0019、未新增 0020 之外的迁移。

## 1. 交付内容

### 1.1 迁移 0020（唯一新迁移）

- `platform.runs.max_active_ms bigint`：冻结的活跃时限（毫秒）。`NULL` = unknown/no deadline，
  **绝不解释为 0**；非 NULL 时有 `CHECK (max_active_ms IS NULL OR max_active_ms > 0)`。
- `platform.run_attempts.active_ms bigint NOT NULL DEFAULT 0` +
  `active_settled_at timestamptz`：一次 attempt 的已结算区间与其幂等标记；
  `CHECK (active_ms >= 0)`。
- 回填：只对 `max_active_ms IS NULL` 且 snapshot JSONB
  `definition.policy.max_active_seconds` 是**1..3600 的整数**的 Run 写 `seconds*1000`；
  非整数/字符串/越界/缺 policy 一律保持 NULL（未知不是 0）。已存在的显式值不回写。
- 无新表、无新权限、无 trigger；`orchara_worker`/PUBLIC 仍无 `platform` 权限。重复 `migrate`
  no-op；回填可重放（`WHERE max_active_ms IS NULL`）。
- 不伪造历史：迁移前的 attempt 保持 `active_ms=0`/未结算，下次转换时真实结算；迁移前已终态的
  Run 的 `active_elapsed_ms` 保持 0（当时没有区间记录）。

### 1.2 结算（唯一 helper，和离开 executing 的转换同事务）

`settleAttemptActiveTx(ctx, tx, runID)`：

1. 对 `WHERE run_id=$1 AND active_settled_at IS NULL` 的执行 attempt 计算
   `active_ms = GREATEST(0, floor(extract(epoch FROM (now()-started_at))*1000)::bigint)`，
   同时写 `active_settled_at=now()`；返回本次 delta（重复调用为 0）。
2. 把**同一 delta** 加到 `runs.active_elapsed_ms`，返回累计值。

调用点（都在已持有 Run/attempt 行锁的事务内）：
`SubmitResult`（proposal/finalizing）、`ConfirmExit`（started=false requeue、started=true park、
result+exit finalize）、`finalizeRunTx`（终态/needs_reconciliation，幂等重放）。
T035/T036 的 waiting/cancelling 转换必须调用同一个 helper（见 §6）；T034 不新增第二个结算器。

规则：
- 新 attempt 的 `active_ms` 从 0/未结算开始，但 `runs.active_elapsed_ms` 保留累计值：
  **不重置、不重新获得完整时限**。
- `phase` 不是 executing（waiting/finalizing/cancelling/idle/终态）时不加 live 区间；
  已结算的 attempt 即使仍未 exit（本票的“截止后拒绝结果”路径）也不再重复计时。
- `revision` 不因结算而 bump；只有既有状态转换各自 bump。

### 1.3 投影（UI 与截止共用一个规则）

`projected = runs.active_elapsed_ms + (phase='executing' 且有未结算 live attempt ?
now()-attempt.started_at : 0)`；`hasLive` 由 SQL 的 `active_settled_at IS NULL` 决定，
live 毫秒由 PostgreSQL `now()` 计算，Go 从不比较墙钟。NULL `max_active_ms` ⇒ `Over=false`。

### 1.4 截止行为

- **创建**：debug Run 从冻结 snapshot 的 `policy.max_active_seconds` 写 `max_active_ms`
  （`wholeSeconds` 拒绝分数/字符串/0/越界；缺失由 P05 validator 与 debugPolicy 双重拒绝）。
- **claim**：候选已 `active_elapsed_ms >= max_active_ms` 时**不发放租约**；同一事务提交
  `failed(deadline_exceeded)`（无 output、`finished_at`、revision+1、intent 记录）、把从未运行的
  job 置 `void`（STATE-MACHINES §3）、写 `run.status` 事件，然后 204。下一次 claim 可继续处理其他
  job（测试证明不会卡住）。
- **heartbeat**：先锁 Run 再锁 attempt（与 result/finalizer 的 run→attempt 顺序一致），按投影
  判定；at/over 时同事务记录 intent（`cancel_requested_at=COALESCE(..., now())` 一次，
  `error_code` 仅在 NULL 时置 `deadline_exceeded`），**仍续租**，reply
  `cancel_requested=true`，workers 因此可停下沙箱并提交退出证据。T036 之后取消优先级由它细化。
- **result**：先结算再判定；at/over 时同一事务记录 intent、**不保存 proposal**、返回 409
  `deadline_exceeded`。重复提交仍是 409（不产生 late success）。
- **exit**：结算后 at/over（或已有 intent）时复用 `finalizeRunTx` 提交
  `failed(deadline_exceeded)`、`output_artifact_id=NULL`、phase idle、finished、job completed、
  token revoked、allocation `released_at` 恰好一次、同事务 `run.status` 事件。无 result 也可终态；
  started=false 的启动失败若已超时同样直接失败而不是 requeue。
- **exit guard**：到期终态仍走 T031 的 `finalizerBlockedTx`（live child / unknown operations）。
  被阻塞时 intent 保留、job held、Run 由调用方 park 到 `recovery_pending`（非终态、不猜测），
  T035/T038 接续；debug Run 无 child，正常路径直接终态。
- **T036 边界（写入报告，避免误解）**：本票的 deadline 是**pre-cancel** 版本；T036 拥有取消
  端点、cancelling 状态与取消优先级。T034 只记录与拒绝新结果，不提交 `cancelled`。

### 1.5 读接口与 UI

- `GET /api/v1/runs/{id}` 追加三个**非契约**响应头（Run schema 是 additionalProperties:false）：
  - `X-Run-Active-Elapsed-Ms`：投影累计毫秒（含 executing 的 live 部分）；
  - `X-Run-Max-Active-Ms`：已知时限；NULL 时**省略**（不是 0）；
  - `X-Run-Deadline-Exceeded: true`：已记录 deadline intent 或投影 at/over。
- `/runs` 详情新增 `detail-active` / `detail-deadline`：
  “跨尝试累计活跃 X 秒（ms） / 上限 Y 秒（ms）”“超时原因：… deadline_exceeded … 不发布输出
  产物”；NULL 上限显示“未知（NULL = 未设置/无截止，不等于 0）”。多次 attempt 只显示一个累计
  时钟（`oneClockNote`），不渲染每次尝试的独立计时/百分比/预计完成时间。全部 `textContent`。
- 页面明确：契约 Run 没有这些字段，数值来自非契约响应头；页面不调用/伪造 T036 不存在的取消接口。

### 1.6 测试

`tests/support/t034.py` + `tests/f03/test_t034_*.py` **4 文件 18 用例**：

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `test_t034_migration.py` | 5 | 0020 链尾/重放 no-op；`runs.max_active_ms` 可空 + 正值 CHECK（0/-1 拒绝）；`run_attempts.active_ms` NOT NULL DEFAULT 0 + 非负 CHECK、新建 attempt 未结算；worker/PUBLIC 零权限；**真实回填**（900→900000、120→120000、显式 5000 不回写、无 policy/分数/越界/字符串→NULL，删除 ledger 后真实 `cmd/admin migrate` 重放） |
| `test_t034_accounting.py` | 4 | 两次 attempt 5s+2s 累计为 7s，第二次 claim 不重置（claim 前后 run 累计不变、新 attempt 0/未结算）；settle 两次幂等（重复 exit 整行相等、配额仍 0）；finalizing 不累计（把 started_at 再移 20s，头/心跳都不变）；waiting 不累计也不能触发截止（phase 由 T035 将写入的形状，SQL 设 waiting → 20s 仍不超时；切回 executing 立即超时） |
| `test_t034_deadline.py` | 5 | heartbeat 到期：intent 一次、`cancel_requested=true`、续租、result 409 `deadline_exceeded` 且不落库/不终态、重复仍 409、响应头数字/原因、exit 后 failed 无 output/一次释放/终态事件；无 heartbeat 直接 exit 也解析截止；claim 拒绝已超时 queued Run（204、failed、job void、无 attempt/allocation、下一次 claim 正常）；NULL max 无截止（20s 仍 cancel=false，随后正常 failed 保留自己的 error_code）；live child 阻塞到期终态（recovery_pending、intent 保留、job held、child 仍 running） |
| `test_t034_pages.py` | 4 | `/runs` 页面两个新行 + 三个头名 + 一个时钟文案；真实 Run 的 active 头（2s 结算、900000 上限）与到期 intent 的 true 头；Node 执行 `describeActiveClock`/`formatActiveSeconds`（7.0s/30.0s、单时钟、deadline_exceeded 原因、NULL≠0、无百分比）；asset `node --check` |

既有测试适配（**收紧/对齐，不削弱**）：
- 9 个迁移链尾文件（T023/T025/T026/T027/T028/T029/T030/T032/T033）从 0019 尾部推进到 0020：
  `range(1,21)`、`0020_active_time_and_deadline.sql`、必要时后移的负索引；T027 的 runs 列集合
  增加 `max_active_ms`，T029 的 run_attempts 列集合增加 `active_ms`/`active_settled_at`。
- **跨套件既有陈旧断言（T034 验证时发现，非本票行为变更）**：
  - `tests/f02a/test_t027_debug.py` 仍断言 debug 创建后 `next_event_seq == 1`，与 T032 的
    “创建即写 queued run.status 事件（seq 1）”矛盾 → 改为 2，并新增
    `max_active_ms == 900000`（T034 创建时冻结 policy 的正向断言）。
  - `tests/f02a/test_t027_migration.py` 仍把 `jobs.current_attempt_token` /
    `run_artifacts.attempt_token` 当“无 FK 的延期列”，与 T029 的 0016 矛盾 → 改为断言两列
    **已**指向 `platform.run_attempts`；`runs.request_id`/`conversation_id` 仍按 F05/F07 延期。
  - `tests/support/t027.py::run_row` 增加 `max_active_ms` 列（两个引用该 helper 的相等断言随之一致）。
  这些改动只让测试描述当前链的真实形状；没有删除任何正向断言。

## 2. 契约缺口与显式偏离（如实）

1. **契约 DDL 缺口**：`contracts/database.sql` 没有 `runs.max_active_ms` 与
   `run_attempts.active_ms/active_settled_at`。0020 是**非契约扩展**（与 0016 的配额/attempt
   列同类）；`contracts/**`/`docs/spec/**` 未改，`sync_spec --check` no drift。后续契约修订应
   把三列写进基线。
2. **非契约响应头**：Run schema 无 active 字段，故用
   `X-Run-Active-Elapsed-Ms`/`X-Run-Max-Active-Ms`/`X-Run-Deadline-Exceeded`
   （同 `X-Manifest-SHA256`、`X-Run-Lease-Deadline`、`X-Run-Execution-Unit` 先例）。列表
   `GET /api/v1/runs` 的每一项无法携带 per-item 头，故累计时钟只在详情（`/runs` 页详情面板,
   `GET /runs/{id}`）展示；列表行不伪造。
3. **新错误码**：result 提交 at/over → 409 `deadline_exceeded`（Error.code 是自由字符串，
   非契约枚举）。claim 的“已超时 queued Run”仍返回 204，但这是唯一会改变状态的 204：Run
   变 failed、job 变 void（已写入 claim 注释与报告）。
4. **NULL ≠ 0**：`max_active_ms` NULL 表示 unknown/no deadline；回填与 debug 创建都拒绝猜测。
5. **pre-cancel 语义**：T034 记录 `cancel_requested_at`（一次）与 `error_code='deadline_exceeded'`
   并拒绝新结果，但**不**把 Run 置 `cancelling`、不提交 `cancelled`。T036 细化取消优先级
   （用户取消 vs 到期）与 cancelling 状态；T034 的到期终态复用 T031 唯一 finalizer，不 fork。
6. **结算与拒绝的时序**：result 提交先结算再判截止。因此一次被 409 拒绝的提议会让该 attempt 的
   区间在拒绝事务中结算（停止继续计时），Run 仍保持 running/executing 直到 supervisor 退出。
   这是“到点即停止新工作”的诚实实现，避免停机延迟继续燃烧时限。
7. **到期 + guard 阻塞**：live child/unknown operation 阻塞终态时，ConfirmExit 把 Run park 到
   `recovery_pending`（intent/error_code 保留、job held、slot 释放），而不是停在“executing 但
   没有 live attempt”。T035/T038 负责解除与重驱动。
8. **能力/注册检查**：设计写明“later capability/registration via one helper”；本票只接入
   claim/heartbeat/result。T033 的 run-scoped artifact 上传/登记在到期后仍按旧规则（run 仍
   executing）接受——这是已记录的边界，后续切片应调用同一 `checkDeadlineTx`。
9. **历史不可造**：迁移前已终态 Run 的 `active_elapsed_ms=0`；没有逐 attempt 历史可回填。
   迁移后在执行中的 attempt 会在下一次转换时结算（含迁移前已流逝的时间）。
10. **既有测试适配**：见 §1.6；其中两项是 T034 验证时发现的**先前票遗留**陈旧断言
    （T032 的 `next_event_seq`、T029 的两个 attempt FK），不是本票行为造成的回归。

## 3. 实际命令与原始结果

环境：`E:\Orchara\product`，profile `dev-stub`，隔离 PG 16.4（127.0.0.1:55432），Go 1.27.3，
Python 3.12.2，Node 24.13.0。

```text
$ python -m pytest tests/f03 -q
117 passed in 711.54s (0:11:51)

$ python -m pytest tests/f05 -q            # 真实 supervisor/沙箱路径回归（额外）
106 passed in 448.12s (0:07:28)

$ go test ./...
21 packages ok（internal/api、artifacts、background、background/handlers、catalog、compiler、
config、db、execution、health、identity、jcs、modelproxy、objectstore、policy、presetexec、
sandbox、supervisor、tools、uuid、workflow），8 个 no test files，0 FAIL，exit=0

$ go vet ./...
（无输出，exit=0）

$ gofmt -l .
（无输出）

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

说明：按批次硬约束不运行五套件/全量 `pytest tests`（由 orchestrator 在收尾运行）。本票改动涉及
`tests/f03`（18 个新用例）、`tests/f05`（回归）与多个套件的迁移链/陈旧断言适配；因此额外跑了
`tests/f05` 与跨套件目标文件（T023/T025/T026/T027/T028/T030/T033 迁移 + T027 debug/idempotency/
immutability + T033 pages），均绿。基线（改动前同命令）：`tests/f03` 99 passed。

## 4. 票面验收逐条

| # | 验收 | 状态 | 证据 |
|---|---|---|---|
| 1 | 跨尝试活跃时间不重置 | 通过 | `test_two_attempts_accumulate_and_the_second_claim_does_not_reset`：attempt1 5s + attempt2 2s → `run.active_elapsed_ms` 7s，第二次 claim 前后累计不变、新 attempt 0/未结算；头/详情一致 |
| 2 | 审批/child 等待不计活跃耗时 | 通过 | `test_waiting_does_not_accrue_and_cannot_trigger_the_deadline`（T035 将写入的 waiting phase：live 20s 仍 0、短时限不触发）+ `test_finalizing_does_not_accrue`（settle 后再挂 20s，头/心跳都不变）；真实 child 等待的 run 状态迁移属 T035（§5） |
| 3 | 达到截止时间停止新调用并走退出 guard | 通过 | heartbeat：intent 一次 + `cancel_requested=true`；result：409 `deadline_exceeded` 且不落库；exit：复用 T031 finalizer 提交 failed（无 output、slot 一次、事件）；claim：拒发租约并 failed/void；`test_a_live_child_blocks_the_deadline_terminal_and_parks_the_run` 证明仍走 children guard |
| 4 | 通过上述端到端入口实际演示；失败、当前授权与重试/恢复边界有断言，不以单层 mock 成功代替 | 通过 | 18 个新用例全部走真实 PG + 编译 API + 真实 mTLS + 真实对象字节；角色规则未变（worker/supervisor/maintenance 的 claim/heartbeat/result/exit 权限由既有用例覆盖）；迟到/重复/冲突/恢复挂起/无 output 均有断言；无 mock |
| 5 | 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件标 blocked，未运行标 not_run | 通过 | 页首记录版本/环境/golden hash；§3 命令与原始结果；§1.6 真实/种子分报；§2/§5 背离与 not_run；本票无 blocked 项 |

## 5. 边界与未运行（not_run）

- `not_run`：真正 waiting_approval/waiting_children 状态迁移与唯一 resume job（T035）；本票只用
  SQL 写 T035 将写的 `phase='waiting'` 并证明不计时。
- `not_run`：取消端点、cancelling 状态、用户取消与到期取消的优先级仲裁、unknown-operation 表
  （T036）；T034 的 deadline 是 pre-cancel、`hasUnknownOperationsTx` 仍恒 false。
- `not_run`：过期租约 sweep、恢复证据与重新领取（T038）；到期 + guard 阻塞只 park。
- `not_run`：到期后的能力调用/工具注册门禁（设计写明 later capability/registration via one
  helper）；本票只接 claim/heartbeat/result。
- `not_run`：真实 supervisor 进程对 result 409 `deadline_exceeded` 的显式处理；正常路径是
  heartbeat 的 `cancel_requested` → sandbox 终止 → exit（T033 已覆盖），结果竞态下的 409 由
  T038 的恢复兜底（T033 supervisor 在 result 提交失败时不补 exit）。
- `not_run`：已发布 Run 的生产创建入口（F02B/F05）；本票只消费 debug Run。
- `not_run`：浏览器 DOM 驱动页面（页面 JS 由 Node 真实执行核心逻辑）。
- `not_run`：全量 `pytest tests`（按本批硬约束，由 orchestrator 运行）。

## 6. 后续票可依赖的接口与表

- 表：`runs.max_active_ms`（bigint NULL=unknown）、`runs.active_elapsed_ms`（累计）、
  `run_attempts.active_ms` / `active_settled_at`（每 attempt 一次结算）；`runs.error_code` 可为
  `deadline_exceeded`（终态前作为 intent）；`runs.cancel_requested_at` 是控制意图。
- Go：`execution.DeadlineErrorCode`；`Service.ActiveTime(ctx, runID)`；
  `deadlineExceeded`/`projectedActiveMS`/`deadlineStateOf`；`settleAttemptActiveTx`、
  `checkDeadlineTx`、`markDeadlineIntentTx`（同包内可复用）；`ErrDeadlineExceeded`（409）。
- **T035 必须承接**：waiting/cancelling 转换在同一事务调用 `settleAttemptActiveTx`；resume 的新
  attempt 从累计值继续（不得清 `active_elapsed_ms`/`max_active_ms`）；waiting 不累计的前提是
  转入 waiting 前完成结算。到期 + live child 的 park 状态由 T035/T036 重驱动。
- **T036 必须承接**：用户取消与 deadline 的优先级（本票先写 `cancel_requested_at`/error_code，
  以 COALESCE/仅 NULL 赋值实现“一次”）；cancelling 状态转换同样调用结算 helper；`error_code` 已
  为 `deadline_exceeded` 的 Run 在退出后不得被改写成 succeeded；unknown guard 扩展
  `hasUnknownOperationsTx`；到期后的能力/注册门禁接 `checkDeadlineTx`。
- **T038 必须承接**：`recovery_pending` 的到期 Run（guard 阻塞/无 result）在恢复证据齐全后仍应
  以 deadline 语义收尾或人工核对；恢复不得重置累计/时限；结算 helper 是唯一写入者。
- **T033 已知边界**：run-scoped artifact 上传/登记在到期后仍按“run 仍 executing”接受；后续切片
  接同一 helper 收紧。

## 7. 文件与提交

- `db/migrations/0020_active_time_and_deadline.sql`
- `internal/execution/{active.go,active_test.go,store.go,service.go,execution.go}`
- `internal/catalog/{debug.go,debug_test.go}`
- `internal/api/{debug.go,execution.go}`
- `apps/web/src/{runs.html,assets/runs.js}`
- `tests/support/{t034.py,t027.py}`、`tests/f03/test_t034_{migration,accounting,deadline,pages}.py`、
  `tests/{f02a,f00b,f05,f03}` 的链尾/陈旧断言适配（§1.6）
- `reports/f03/T034-active-time.md`、`PROGRESS.md`、`E:\orchara-notes\f05-f03\NOTES.md`
- 提交：feat（迁移 + Go 服务/API + Web）、test（`tests/f03` + 适配）、docs（本报告 + PROGRESS +
  NOTES）。不推送、不关闭 issue。

## 8. 规格变更

无。`product/docs/spec/**` 与 `product/contracts/**` 未修改；`python tools/sync_spec.py --check`
no drift。发现的契约缺口（三个新列、三个非契约响应头、409 `deadline_exceeded`、claim 的
“改变状态的 204”、NULL 语义）记录在 §2，由后续契约修订统一处理。
