# F03 T036 验收：取消、cancelling 与未知操作守卫

- 任务：F03 / GitHub Issue #37（票 `T036`，规划 ID F03.04），分支
  `feat/f05-f03-run-and-tools`
- 状态：**`passed`**（本票交付范围 = owner-only 幂等取消端点 + `cancelling` 状态 +
  cancel/unknown/deadline 优先级的单一 finalizer + 未解决 unknown-operation ledger 与
  worker/supervisor mTLS bridge + waiting 取消不恢复 + 运行页取消/正在停止 vs 已停止 +
  终态不回翻）。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 mTLS 内部客户端
  （supervisor/worker 证书）+ 真实 HTTP + 真实文件系统对象字节 + 真实 signed sealed 链接 +
  Node 真实执行页面逻辑；无 mock、无跳过。
- 边界（如实）：真实外部操作/回执属 **F06**，本票的 unknown 端点只是记录在案的非契约
  bridge（与 T030 tool-run、T033 run-scoped artifacts、T035 waiting-resolution 同一边界）；
  安全恢复/核对后的重驱属 **T038**；浏览器 DOM 驱动页面仍 `not_run`（与 F01/F02A/F03
  既有边界相同）。
- 主要文件：
  - `db/migrations/0022_run_unknown_operations.sql`（唯一新表 + `run_waitings.cancel_requested_at`
    + 未解决 partial index + 权限边界 + 列注释）
  - `internal/execution/cancel.go`（`Cancel`、unknown record/resolve、resolve 后 finalizer 重驱、
    安全 reason allowlist）
  - `internal/execution/cancel_store.go`（cancel/job/waiting/ledger SQL、live attempt 校验）
  - `internal/execution/service.go`（`finalizeRunTx` 优先级扩展、ConfirmExit 的 cancelling 路径、
    started=false 取消走 void job）
  - `internal/execution/{store,waiting,waiting_store,runartifacts}.go`（unknown guard 查询、
    waiting cancel marker、receipt/query 在 cancelling 仍可用）
  - `internal/api/{cancel.go,execution.go,server.go,internal.go}`（public cancelRun + 两个
    internal unknown bridge + 错误码映射）
  - `apps/web/src/{runs.html,assets/runs.js}`（取消按钮/取消行/终态原因/终态单调/生命周期）
  - `tests/support/t036.py`、`tests/f03/test_t036_{cancel,unknown,migration,pages}.py`
    （6 文件 29 用例）
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`tools/pg_dev.py`，真实 `cmd/admin migrate`）
  - Python 3.12.2（`python -m pytest`）、Node 24.13.0（页面 JS 语法与行为真实执行）
  - profile `dev-stub`；对象目录在测试 `tmp_path`；部署输入沿用 T025 占位值
    `ORCHARA_WORKER_IMAGE_DIGEST=sha256:aaaa…(64)`、runtime lock =
    `probes/agent_min/requirements.lock.txt`（真实 SHA-256）
  - 冻结的 golden 执行 hash（与 T025/T027/T029/T031/T034/T035 相同）：
    `9cc418d167377e667c77284ce7b35819c7374c0459ec90e41f4131719aef5686`
  - 迁移链尾：`0022_run_unknown_operations.sql`（0001→0022 全链可迁移、重复 migrate no-op）
- 真实/桩分报：**全部 real**（PG/二进制/mTLS/HTTP/对象字节/Node）。测试用 SQL 只做控制/种子：
  运行中的 attempt/child Run/snapshot 行（发布/子应用入口属 F02B/F07），以及为 API-key
  正路径播种一个 service-principal 拥有的 queued Run（service account 的公开 Run 入口属
  F05/F02B）。迁移测试删除 0022 的 ledger 行后由**真实 `cmd/admin migrate`** 重放，观察真实
  no-op 与约束仍在。

## 0. 工作记录（简短）

1. 读 NOTES（T029–T035 决策）、`T031-T038-BRIEFS.md` §T036、票面 #37、EXECUTION §3/§5/§6、
   STATE-MACHINES §2/§3/§4/§6、IMPLEMENTATION-DETAILS §6/§7、API §1/§2、`contracts/openapi.json`
   的 `cancelRun`/`Run`、`contracts/database.sql` 的 runs/jobs/run_attempts、
   T029/T031/T033/T034/T035 代码与报告；确认分支干净、0021 为最新迁移、T031 的
   `hasUnknownOperationsTx` 是唯一 guard 扩展点。
2. TDD 切片（红→绿，按票面入口逐条）：
   - 先写迁移测试（红：0022 不存在）→ 0022 + run_waitings marker（绿）；
   - 写 queued 取消（红：`POST /cancel` 404）→ cancel 端点 + 立即 cancelled（绿）；
   - 写 running 取消（红：state 无 cancelling、result 未被拒）→ cancelling 转换 + 拒绝矩阵
     + exit 后 cancelled（绿）；
   - 写 result+exit/cancel 竞争与 deadline 优先级（红：cancel 被 deadline/成功结果覆盖）→
     `finalizeRunTx` 单一优先级（绿）；
   - 写 waiting 取消/decision 前后/resume 后取消（红：仍会 enqueue resume）→ waiting marker +
     `maybeEnqueueResumeTx` inert（绿）；
   - 写 unknown record/resolve + needs_reconciliation + resolve-last 重驱（红：guard 恒 false）→
     ledger + bridge + 重驱（绿）；
   - 写页面/Node 行为测试（红：无 cancel 面板/迟到快照可翻回）→ Web（绿）。
3. 顺序保证：所有 cancel/unknown 入口锁 Run→waiting→job/attempt，与 result/exit/resume
   同序；cancel 与 result/finalizer 的竞争由 Run 行锁串行化，终态由 CAS + 唯一 finalizer 提交。
4. 跑门禁（§3），写本报告/PROGRESS/NOTES，按 feat/test/docs 三次提交；未推送、未关 issue、
   未改 `docs/spec/` 与 `contracts/`、未改迁移 0001–0021、未开始 T037/T038。

## 1. 交付内容

### 1.1 迁移 0022（唯一新迁移）

`platform.run_unknown_operations`：

| 列 | 语义 |
|---|---|
| `id` | uuid PK（服务端生成） |
| `run_id` | NOT NULL → `platform.runs(id)` |
| `operation_id` | NOT NULL；F06 真实 operations 的逻辑 id（外部身份，暂不加 FK） |
| `reason` | NOT NULL 安全 code；Go allowlist（migration 只做非空/≤128 形状约束） |
| `created_at` | 数据库时钟首次报告 |
| `resolved_at` | NULL=未解决；finalizer 的唯一 guard predicate |

`UNIQUE(run_id, operation_id)`（同一 identity 重报幂等、不同 reason 冲突）；partial index
`(run_id) WHERE resolved_at IS NULL`（guard 热路径）。另加 `platform.run_waitings.cancel_requested_at
timestamptz`：取消等待时保留全部等待证据，只让 resume 事务 inert。

**没有** `runs.cancel_reason`/`cancelled_by`：owner-only 端点 + `runs.cancel_requested_at` +
已提交的 `cancelled` 状态就是持久事实；契约 Run 没有字段消费额外 actor/reason，增加列只会
重复状态机（迁移头已记录）。**没有** operations/approvals/requests 表（F06/F07 归属）。
`orchara_worker`/PUBLIC 仍零 `platform` 权限；重复 migrate no-op。

### 1.2 `POST /api/v1/runs/{run_id}/cancel`（cancelRun）

owner-only（管理员/他人/未知/畸形/已删除一律 404 `resource_not_found`，同 getRun 不可见规则）；
浏览器会话需 CSRF，API key 需 `application:write`；202 契约 Run；幂等。

| Run 事实 | 行为 |
|---|---|
| 已终态（succeeded/failed/cancelled） | 202 当前 Run，不写行、不加事件 |
| `cancel_requested_at` 已存在（含 T034 deadline intent） | 202 当前 Run，不重写时间戳、不加事件（first write wins） |
| 无 live attempt 且 job ready/held/completed/void/nil | 立即 `cancelled`：phase idle、finished_at、revision+1、job `void`（completed 保持）、waiting 行标记、allocation 若存在则释放一次、同事务一条 `run.status` |
| 无 live attempt 但存在未解决 unknown | 立即 `needs_reconciliation`（`error_code=unknown_operations`）、phase idle、finished 保持 NULL、job `held`、waiting 行标记、同事务事件；resolve 最后一个 unknown 后重入 finalizer 提交 cancelled |
| 有 live attempt（running/finalizing/waiting） | 记录 intent（`COALESCE(...,now())`）、state `cancelling`、**phase 保持原值**（枚举无 cancelling）、revision+1、同事务事件 |
| job `leased` 但无 live attempt（撤销未退出） | 不冒充已停止：走 cancelling，交给 exit/T038 |

`cancel_requested_at` 是控制意图，不表示外部写入被撤销；202 不表示已停止。

### 1.3 单一 finalizer 的固定优先级

扩展 T031 `finalizeRunTx`（不 fork），result+exit 齐全后按顺序：

1. **live child Run**：阻塞一切终态。cancel 保持 `cancelling` + job held；deadline 仍按 T034
   park `recovery_pending`（不伪造 failed/cancelled）。
2. **未解决 unknown**：优先于 deadline/cancel/proposal → `needs_reconciliation` +
   `error_code=unknown_operations`；job completed、token revoked、allocation 释放一次、同事务事件。
3. **已记录/已达 deadline**：`failed(deadline_exceeded)`（T034 语义保留，deadline 优先于
   cancel）；仍走上面两个 guard。
4. **`cancel_requested_at`**：`cancelled` 胜未提交的 success/failed/waiting proposal；output
   artifact 为 NULL、error_code 清空；started=false 的取消 exit 将 job `void`。
5. 否则按 proposal 提交 succeeded/failed/needs_reconciliation 或保持 waiting。

`cancelled` 的 job 是 completed（真实执行过）或 void（从未运行）；token 撤销、allocation
`released_at IS NULL` 下恰好一次、revision+1、同事务 `run.status`。终态后重复 finalize/exit/
cancel 都是 no-op；终态绝不因迟到消息改变。

### 1.4 unknown-operation bridge（F06 前的非契约记录面）

| 端点 | 角色 / 凭据 | 行为 |
|---|---|---|
| `POST /internal/v1/runs/{run_id}/unknown-operations` `{attempt_token, operation_id, reason}` | worker \| supervisor；run 的 **live** attempt token | 记录一次；同 id+reason 200 replay、不同 reason 409 `event_conflict`、非法 reason 422 `validation_failed`（`details.field=reason`）、terminal 409 `run_terminal`、foreign/stale token 409 `stale_attempt` |
| `POST /internal/v1/runs/{run_id}/unknown-operations/{operation_id}/resolve` `{attempt_token}` | worker 需 live token；supervisor 可在 exit 后 | 标记 resolved 一次（幂等 200）、不删除行；terminal 409 `run_terminal` 绝不复活；未知 operation 404 `resource_not_found` |

`hasUnknownOperationsTx`（T031 的命名扩展点）现在查询未解决行。解析最后一个 unknown 且 run 是
cancelling/needs_reconciliation/finalizing、最新 attempt 已 exit、无 live child 时，重入同一
`finalizeRunTx`，使 Run 恰好一次提交 cancelled；若仍有 live attempt 则只清 guard，exit 后照常收尾。

reason allowlist（Go 侧闭合，F06 可无迁移扩展）：`dispatch_timeout`、`transport_error`、
`worker_crash`、`receipt_missing`、`provider_timeout`、`cancellation_race`；绝不接受 free text、
provider body、URL 或凭据。

### 1.5 waiting / resume 集成（T035）

- 取消时同一事务写 `run_waitings.cancel_requested_at`；等待的 checkpoint/decision/exit 证据全部保留。
- `maybeEnqueueResumeTx` 对该 marker inert：即使 decision 与 exit 已在竞态中到达也不创建 resume。
- 取消发生在 waiting 且 attempt 仍 live → 先 `cancelling`；waiting-resolution 在 cancelling
  被 409 `run_state_conflict` 拒绝；supervisor exit 后 finalizer 提交 cancelled，不生成 resume。
- 取消发生在已经 resume 排队之后 → 立即 cancelled，resume job 置 `void`，claim 不会再领取它。
- 取消发生在 decision 已存但尚未 exit → 只记决定；取消后 exit 走 cancelled，decision 成为
  证据而不是 resume 触发条件。

### 1.6 拒绝 / 允许矩阵（cancelling）

| 调用 | 结果 |
|---|---|
| heartbeat | 200，`cancel_requested=true`（仍续租） |
| confirm exit | 接受；终态由 finalizer 提交 |
| submit result | 409 `run_state_conflict`（不存 proposal） |
| register artifact / create run upload | 409 `run_state_conflict` |
| waiting-resolution | 409 `run_state_conflict` |
| complete 已 in-flight upload / artifact status / download | 允许（receipt/query 仍可用） |
| 终态后的 result | 409 `run_terminal` |
| 终态后的 exit（同证据/异证据） | 200 no-op / 409 `attempt_evidence_conflict`，无状态变化 |
| 终态后的 unknown record/resolve | 409 `run_terminal`，不复活 |

### 1.7 Web

`/runs` 详情新增“取消 / 终态原因”行、owner 取消按钮与状态行：

- `queued` 说明取消立即生效；`cancelling` 显示“正在停止”，明确不是成功；
- `cancelled` 显示“已停止”，并明确不表示撤销已发送的外部写入；`failed/succeeded` 显示终态原因
  （`error_code`）；
- `runLifecycle` 增加 `executing → cancelling → terminal`；`shouldPollRun` 继续轮询 cancelling；
- `mergeRunSnapshot` 让已提交终态单调：迟到/陈旧的 poll 快照或事件不会把终态翻回非终态；
- 按钮点击走真实 `POST /cancel`（CSRF），202 后重新读取详情，不自行猜测终态；重复点击幂等；
- 沙箱面板停止/已停止措辞同步，删除 T033 时期“取消未实现/SQL 运维路径”的说明。

## 2. 验收标准逐条（票面 #37）

| AC | 状态 | 证据 |
|---|---|---|
| 取消意图立即阻止新能力调用 | **passed** | cancelling 时 result/registration/create-upload/waiting-resolution 409 `run_state_conflict`；heartbeat 继续并 `cancel_requested=true`；`tests/f03/test_t036_cancel.py` 与 unknown 用例 |
| 所有受控单元退出后才 cancelled | **passed** | waiting/running 取消在 supervisor exit 后才提交 cancelled；live child 阻塞保持 cancelling；started=false 取消 job void；exit 前的 cancel 不写 finished_at |
| 迟到结果或重投不能复活终态 | **passed** | 终态后 result 409 `run_terminal`、同证据 exit 200 no-op、异证据 409、unknown record/resolve 409；`mergeRunSnapshot` 页面层单调；事件/rev 不增 |
| 存在 unknown 时转核对而非声称撤销 | **passed** | unknown 存在时 cancel/finalizer 提交 `needs_reconciliation(unknown_operations)`、job held/completed、非 finished；resolve 最后一个后才 cancelled；resolved 行不再阻塞 |
| 通过端到端入口实际演示；失败/授权/恢复边界有断言 | **passed** | 真实 owner session/CSRF、真实 API key scope、真实 mTLS worker/supervisor、真实 PG 行与事件；权限 404/403、deadline 优先级、live child、waiting/deadline 边界均有断言；未用单层 mock 代替 |
| 记录版本、输入/hash、命令、原始结果与 profile；stub/real 分报、缺条件 blocked、未运行 not_run | **passed** | 本报告 §3 原始门禁；环境与 golden hash 在上方；全部 real；F06 operations、T038 重驱、浏览器 DOM、多套件 25 分钟门禁如实列 §5 `not_run` |

## 3. 门禁与原始结果

工作目录 `E:\Orchara\product`；PG 隔离集群 `127.0.0.1:55432`；Go 环境由 `tools/toolchain.py`
固定（`GOCACHE=E:\orchara-toolchains\gocache`、`GOMODCACHE=E:\orchara-toolchains\gomodcache`）。

```text
$ python -m pytest tests/f03 -q
170 passed in 745.67s (0:12:25)

$ go vet ./...
(no output)                                     exit=0

$ go test ./...
?   	orchara/platform/cmd/admin	[no test files]
?   	orchara/platform/cmd/api	[no test files]
?   	orchara/platform/cmd/maintenance	[no test files]
?   	orchara/platform/cmd/sandboxprobe	[no test files]
?   	orchara/platform/cmd/supervisor	[no test files]
?   	orchara/platform/cmd/worker	[no test files]
?   	orchara/platform/db	[no test files]
ok  	orchara/platform/internal/api	3.033s
ok  	orchara/platform/internal/artifacts	1.682s
?   	orchara/platform/internal/audit	[no test files]
ok  	orchara/platform/internal/background	1.456s
ok  	orchara/platform/internal/background/handlers	2.860s
ok  	orchara/platform/internal/catalog	1.952s
ok  	orchara/platform/internal/compiler	(cached)
ok  	orchara/platform/internal/config	0.884s
ok  	orchara/platform/internal/db	(cached)
ok  	orchara/platform/internal/execution	2.313s
ok  	orchara/platform/internal/health	(cached)
ok  	orchara/platform/internal/identity	(cached)
ok  	orchara/platform/internal/jcs	(cached)
ok  	orchara/platform/internal/modelproxy	2.259s
ok  	orchara/platform/internal/objectstore	(cached)
ok  	orchara/platform/internal/policy	(cached)
ok  	orchara/platform/internal/presetexec	2.096s
ok  	orchara/platform/internal/sandbox	4.017s
ok  	orchara/platform/internal/supervisor	(cached)
?   	orchara/platform/internal/toolruns	[no test files]
ok  	orchara/platform/internal/tools	4.588s
ok  	orchara/platform/internal/uuid	(cached)
ok  	orchara/platform/internal/workflow	(cached)
exit=0

$ gofmt -l .
(no output)                                     exit=0

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

本票新增用例：`tests/support/t036.py` + `tests/f03/test_t036_{cancel,unknown,migration,pages}.py`
**6 文件 29 用例**（cancel 14、unknown 6、migration 5、pages 4）。迁移链适配 11 个既有测试
0021→0022（T023/T025/T026/T027/T028/T029/T030/T032/T033/T034/T035 的链尾断言），并按 T036
取代 T033 的取消 interim 断言（`recovery_pending` → 允许 `cancelled`）。**没有**运行 25 分钟
多套件 `pytest tests` 门禁（批次规则：orchestrator 运行）；`tests/f05` 的沙箱取消用例未在本票
重跑（WSL2/runsc 环境依赖，T033 已记录），其断言已按 T036 行为更新。

## 4. 契约缺口 / 取舍

1. **unknown-operation 端点是非契约扩展**：`contracts/openapi.json` 没有该路径；F06 拥有真实
   operations/回执。文档记录在迁移头、此报告与 NOTES（ADR 0005 模式）。
2. **run.error_code 新增稳定值 `unknown_operations`**：不是 HTTP 错误码，而是 Run 的核对原因。
   HTTP 只复用 `resource_not_found`/`run_terminal`/`run_state_conflict`/`stale_attempt`/
   `event_conflict`/`validation_failed`/`forbidden`，没有新增顶层 code。
3. **无 `runs.cancel_reason`/`cancelled_by`**：见 §1.1；取消人就是已认证 owner，时间在
   `cancel_requested_at`，终态原因由 state 表达。后续 F07/F09 若要审计 actor，可加自己的表。
4. **`run_waitings.cancel_requested_at` 是 0021 表的新增列**：不删除等待证据；F06/F07 仍可用
   decision/checkpoint/exit 行做审计。
5. **deadline vs cancel 的仲裁顺序**：T034 已观察/记录的 deadline（`error_code=deadline_exceeded`
   或 finalizer 时 projected ≥ max）优先 → `failed(deadline_exceeded)`；尚未被任何 T034 入口观察
   的排队 Run 若先收到用户取消，则立即 `cancelled`（取消事务先到）。两种顺序都有真实测试
   （`test_recorded_deadline_keeps_its_failed_outcome_over_a_later_cancel`、
   `test_a_queued_cancel_racing_ahead_of_the_deadline_commits_cancelled`）。
6. **cancelling 保留 phase**：契约 phase enum 只有 idle/executing/waiting/finalizing；cancelling
   语义由 state 表达。最终 cancelled 一律 phase idle。
7. **receipt/query 例外**：cancelling 下已 in-flight 的 upload complete、artifact status/download
   仍允许；新 execution-bearing 调用被拒绝。这是 EXECUTION §6 “拒绝新能力调用”的边界。
8. **child 阻塞后无自动重驱**：取消的 Run 若被 live child 阻塞，保持 cancelling + job held；
   子 Run 终态后由 T038 的重驱/恢复路径（或后续 ticket）再次调用 finalizer。本票不新增后台扫描。

## 5. 已知限制 / not_run

- **真实 F06 operations/回执**：本票只有未解决 ledger 与 bridge；真实写工具/审批/回执查询属 F06。
- **T038**：recovery_pending 的恢复证据、live child 完成后的自动重驱、核对后的 requeue、
  租约过期扫描未实现；本票对 `recovery_pending` 的立即取消只覆盖“无 live attempt”的情形。
- **边缘状态**：直接 SQL 给一个从未有 attempt 的 queued Run 播种 unknown 行再取消，只会进入
  needs_reconciliation 且没有 finalizer 输入可重驱（bridge 的正常路径要求 live attempt，
  不会产生该状态；T038 可收敛）。
- **浏览器 DOM 驱动**：页面逻辑用 Node 执行 + 真实 HTTP 结构断言；无 headless browser 自动化
  （与本批其他票一致）。
- **API-key 正路径的 Run 生产者**：测试用 SQL 为 service principal 播种 queued Run；service
  account 的公开 Run 入口属 F05/F02B。凭据/端点本身是真实的。
- **25 分钟多套件 `pytest tests` 门禁**：`not_run`（批次规则，orchestrator 运行）。
- **T037/T038**：未开始（未做工具构建/恢复证据）。

## 6. 下一票接口（T038/F06 可依赖）

- `execution.Service.Cancel(ctx, callerID, runID)`：owner-only 事务裁决；返回 `{RunID,OwnerID}`，
  API 用 `catalog.GetRun` 读契约 Run。
- `execution.Service.RecordUnknownOperation(ctx, runID, {AttemptToken,OperationID,Reason})` 与
  `ResolveUnknownOperation(ctx, runID, operationID, attemptToken, internalRole)`：F06 可复用或用
  自己的表替换；`hasUnknownOperationsTx` 是 finalizer 唯一 guard 入口。
- `finalizeRunTx` 新输入 `NeverStarted`，`RunState` 接受 `cancelling`/`needs_reconciliation`
  （resolve-last 重驱）；T038 的重驱应复用同一函数，不要新增第二个 finalizer。
- `platform.run_unknown_operations`（run_id/operation_id/reason/resolved_at）与
  `platform.run_waitings.cancel_requested_at` 是 durable facts；T038 的核对结果应写
  `resolved_at`（一次）而不是删除行。
- 错误/原因码：Run.error_code `unknown_operations`；HTTP 复用 `run_state_conflict`/
  `run_terminal`/`stale_attempt`/`event_conflict`/`resource_not_found`。
- Web：`cancelRunPath`、`canCancelRun`、`describeRunCancel`、`mergeRunSnapshot` 可被后续页面复用；
  `mergeRunSnapshot` 的终态单调规则必须保留。

## 7. 文件与提交

- `db/migrations/0022_run_unknown_operations.sql`
- `internal/execution/{cancel.go,cancel_store.go,service.go,store.go,waiting.go,waiting_store.go,runartifacts.go,execution.go}`
- `internal/api/{cancel.go,execution.go,server.go,internal.go,runartifacts.go}`
- `apps/web/src/{runs.html,assets/runs.js}`
- `tests/support/t036.py`
- `tests/f03/test_t036_{cancel,unknown,migration,pages}.py`
- 迁移链/T033 适配：`tests/f00b/test_t026_migration.py`、`tests/f02a/test_t023_migration.py`、
  `tests/f02a/test_t025_migration.py`、`tests/f02a/test_t027_migration.py`、
  `tests/f03/test_t029_migration.py`、`tests/f03/test_t032_migration.py`、
  `tests/f03/test_t034_migration.py`、`tests/f03/test_t035_migration.py`、
  `tests/f05/test_t028_migration.py`、`tests/f05/test_t030_tool_run_migration.py`、
  `tests/f05/test_t033_migration.py`、`tests/f05/test_t033_pages.py`、
  `tests/f05/test_t033_sandbox_run.py`
- 提交：`f78e7ee` feat（迁移 + Go 服务/API + Web）、`b931ab6` test（`tests/f03` + 适配）、
  docs（本报告 + PROGRESS）；未推送、未关 issue、未改 `docs/spec/**`/`contracts/**`。

## 8. 规格变更

无。`docs/spec/**` 与 `contracts/**` 未修改；`python tools/sync_spec.py --check` = no drift。
