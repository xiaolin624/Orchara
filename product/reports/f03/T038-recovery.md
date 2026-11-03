# F03 T038 验收：保守恢复（挂起待证据，审计后单次重驱）

- 任务：F03 / GitHub Issue #39（票 `T038`，规划 ID F03.07），分支
  `feat/f05-f03-run-and-tools`
- 状态：**`passed`**（本票交付范围 = 租约超期扫描只挂起 + 受审计证据命令 +
  一次事务内的单次重排 + owner-facing recovery 事实/UI；无自动接管、无 token 交换）。
  真实 PostgreSQL 16.4 + 真实编译二进制（`cmd/admin`/`cmd/api`）+ 真实 mTLS 内部客户端
  （supervisor/worker 证书）+ 真实 HTTP + 真实文件系统对象字节 + **真实打开中的
  `pg_stat_activity` 会话/事务** + Node 真实执行页面逻辑；无 mock、无跳过。
- 边界（如实）：`cancelling`/`waiting_*` 的 Run **不**被本票的 sweep 挂起（见 §1.6/§5），
  它们需要 live supervisor 的 exit 证据或后续 cancel-recovery 路径；F04A 的真实
  supervisor/worker 进程与自动 reaper 属后续票；巡检定时器（cron/定时任务）未接线，命令由运维/
  调度系统调用；`run_recovery_evidence` 的第二次合法恢复 cycle 记录 `resumed_job_id=NULL`
  （数据库 UNIQUE 只允许一个 evidence 行命名同一 job；见 §1.1 偏离 3）；浏览器 DOM 驱动仍
  `not_run`（与 F01/F02A/F03 既有边界相同）。
- 主要文件：
  - `db/migrations/0024_run_recovery_evidence.sql`（唯一新表 + `runs.recovery_reason/
    recovery_since` + 配对/闭合 CHECK + 权限边界 + 列注释）
  - `internal/execution/recovery.go`（证据闭合形状/JCS SHA-256、sweep、recover 全部检查 +
    单事务、owner-facing `RecoveryFacts`、拒绝码）
  - `internal/execution/recovery_store.go`（sweep/recover/actor/evidence SQL、行锁复检）
  - `internal/execution/{store,service,cancel_store}.go`（T031 park 写 `exit_without_result`；
    离开 `recovery_pending` 时清 recovery 列）
  - `cmd/admin/runs.go`、`cmd/admin/main.go`（`runs sweep-leases` / `runs recover`，单 JSON
    输出、exit 0/1/2、不打印 token/DSN）
  - `internal/api/debug.go`（`X-Run-Recovery-*` 非契约响应头）
  - `apps/web/src/{runs.html,assets/runs.js}`（恢复面板：原因/挂起时间/证据/运维命令；
    无伪造恢复按钮；终态不回翻）
  - `tests/support/t038.py`、`tests/f03/test_t038_{migration,sweep,recover,recovery_refusals,pages}.py`
  - 迁移链尾适配 0023→0024：12 个既有迁移测试（f00b/f02a/f03/f05）
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`product/tools/toolchain.py` 解析的 go.exe）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`tools/pg_dev.py`），真实
    `cmd/admin migrate` 迁移新建库
  - Python 3.12.2（`python -m pytest`）；Node v24.13.0（页面 JS 语法与行为真实执行）
  - profile `dev-stub`；对象目录在测试 `tmp_path`；部署输入沿用 T025 占位值
    `ORCHARA_WORKER_IMAGE_DIGEST=sha256:aaaa…`(64)，runtime lock =
    `probes/agent_min/requirements.lock.txt`（真实 SHA-256）
  - 冻结 golden 执行 hash（与 T025/T027/T029/T031/T034/T035/T036 相同）：
    `9cc418d167377e667c77284ce7b35819c7374c0459ec90e41f4131719aef5686`
  - 迁移链尾：`0024_run_recovery_evidence.sql`；0001→0024 全链可迁移、重复 migrate no-op
- 真实 / 档分报：**全部 real**（PG/编译二进制/mTLS/HTTP/对象字节/真实 psql 事务/Node）。测试用 SQL
  只做控制/种子（把 `run_attempts.heartbeat_at/lease_deadline` 移到过去、删掉一个 attempt 以
  覆盖“无 attempt”边界、标记 artifact 已删），所有业务转换都由**真实命令/真实 API**执行。

## 0. 工作记录（简短）

1. 读 NOTES（T029–T037 决策）、`T038-DESIGN.md`、票面 #39、EXECUTION §3/§8、STATE-MACHINES
   §2/§3/§6、IMPLEMENTATION-DETAILS §6、SECURITY §5/§6、OPERATIONS、API §3；读 T029/T031/
   T033/T034/T035/T036 代码与报告、`internal/compiler.VerifyManifestBytes`、`internal/audit`、
   `cmd/admin`/`cmd/maintenance` 命令风格、`internal/api/{runs,debug}.go`、`apps/web/src/runs.*`、
   `tests/support/{t029,t031,t034,t035,t036}.py`。确认分支干净（HEAD `32931c9`）、0023 为最新迁移。
2. TDD 切片（红→绿）：
   - Go 单测（红：`parseRecoveryEvidence`/`RecoveryRefusal`/`validateRecoveryGrace` 未定义）→
     证据闭合形状 + JCS SHA-256 + grace 边界（绿）；
   - 迁移测试（红：0024 不存在/权限/约束未定义）→ `0024_run_recovery_evidence.sql`（绿）；
   - sweep 测试（红：`runs sweep-leases` 未接线/不 park）→ `internal/execution/recovery.go` +
     `cmd/admin/runs.go`（绿）；
   - recover 测试（红：`runs recover` 未接线/不重排）→ 全检查 + 单事务 + 证据/审计（绿）；
   - 拒绝矩阵（红：活 worker 未拒绝、在途 DB 写未拒绝、缺制品未拒绝）→ 各拒绝码（绿）；
   - 页面 Node 行为测试（红：无 `describeRunRecovery`/无恢复头）→ `runs.html`/`runs.js`（绿）。
3. 顺序保证：sweep/recover 都按包内统一锁序 Run → attempt → job；recover 的慢检查（manifest
   字节、输入哈希、pg_stat_activity）在事务外做一遍，进入事务后在行锁下**重读并复检**所有关键事实
   （状态/revision/heartbeat/exited/artifact 行状态/在途连接），任何漂移整笔回滚。
4. 跑门禁（§3），写本报告/PROGRESS/NOTES，按 feat/test/docs 三次提交；未推送、未关 issue、
   未改 `docs/spec/` 与 `contracts/`、未改迁移 0001–0023、未开始 F04A/F06/F07。

## 1. 交付内容

### 1.1 迁移 0024（唯一新表 + 两个 Run 列）

`platform.run_recovery_evidence`：

| 列 | 语义 |
|---|---|
| `id` | uuid PK（可信命令生成） |
| `run_id` | NOT NULL → `platform.runs(id)` |
| `attempt_token` | NULL → `platform.run_attempts(token)`；被证明死亡的旧 attempt（token 仍是凭据，只读接口从不暴露） |
| `evidence` | NOT NULL jsonb，`jsonb_typeof='object'` CHECK；闭合形状的运维证据（见 §1.3） |
| `evidence_sha256` | NOT NULL，`^[0-9a-f]{64}$` CHECK；证据 RFC 8785 JCS 规范字节的 SHA-256（与 JSONB 键序/空白无关） |
| `recorded_by` | NOT NULL → `platform.principals(id)`；真实管理员（`--actor-login` 或 bootstrap admin） |
| `recorded_at` | NOT NULL DEFAULT now()（数据库时钟） |
| `resumed_job_id` | UNIQUE → `platform.jobs(id)`；本次重新武装的 job（“同一 job 只能被一条证据命名”的数据库兜底） |

另加 `CONSTRAINT run_recovery_evidence_run_attempt_key UNIQUE (run_id, attempt_token)`
（每个 attempt 最多一条证据，重复命令无法记录/重排两次）与索引
`run_recovery_evidence_run (run_id, recorded_at DESC)`。

`platform.runs.recovery_reason text` / `platform.runs.recovery_since timestamptz`：
NULL 表示未挂起；`runs_recovery_pair_check` 要求两者同时为 NULL/同时有值；
`runs_recovery_reason_check` 闭合为 `lease_expired|exit_without_result`。
契约 Run 投影没有 recovery 字段，故它们是非契约扩展（ADR 0005），由
`X-Run-Recovery-*` 响应头展示。

**有意偏离 contracts/database.sql 与 owner**（迁移头完整记录）：
1. `platform.run_recovery_evidence` 不在契约基线中：F04A 将由真实 supervisor 产出同一事实，
   本表是运维的持久记录；F04A 可改用内部端点复用本表。
2. `runs.recovery_reason/recovery_since` 为新列；不建 recovery 状态机表——STATE-MACHINES §2
   已有 `recovery_pending → queued|failed|cancelled|needs_reconciliation`，证据行 + 两列就是
   该转换背后的事实。
3. **`resumed_job_id` UNIQUE 与第二次合法恢复 cycle 的张力**：requeue 复用同一个 job id，因此同一
   job 的第二次 recovery cycle 不能再插入一条命名它的证据行。实现为：第一次 cycle 写入
   `resumed_job_id=job`；之后的 cycle 写入 `NULL`（NULL 不冲突），而“同一 attempt 只能被恢复
   一次”由 `UNIQUE(run_id, attempt_token)` + run state/revision CAS + `one_live_job_per_run`
   保证。若未来契约要求逐 cycle 关联，可在后续迁移把约束改为 `UNIQUE(run_id, resumed_job_id)`
   并让 requeue 生成新 job。
4. 不建 `runs.recovery_operator` 列：`recorded_by` + `platform.audit_events` 已经记录 actor。

`orchara_worker`/PUBLIC 仍零 `platform` 权限；重复 migrate no-op。
`sync_spec --check` no drift（只比较生成契约文件）。

### 1.2 `cmd/admin runs sweep-leases [--grace 60s] [--dry-run]`

扫描 `run_attempts` 中 `exited_at IS NULL AND revoked_at IS NULL AND
lease_deadline < now() - grace` 且 Run `state='running'`、未删除、**无 cancel intent** 的
attempt（最多 100 个/次），逐条在**独立事务**里 `FOR UPDATE OF a, j, r` 复检后：
先以 `lease_deadline`（最后可证明的活动终点，不是 park 时刻）结算该 attempt 的活跃区间
（`settleAttemptActiveUntilTx`），再 `runs.state='recovery_pending'`、`runs.phase='idle'`、
`revision+1`、`recovery_reason='lease_expired'`、`recovery_since=now()`；job → `held`（清
`current_attempt_token`）；**不释放 allocation**、**不新增 attempt**、**不换 token**；同事务
`run.status` 事件（`AppendRunStatusTx`）+ `platform.audit_events`（action
`run.recovery_parked`，actor NULL = 系统动作，metadata 只含 reason/job/unit/lease/grace，
绝不含 token）。`--dry-run` 只报告 `would_park`，零写入。退出码 0 成功、2 用法、1 数据库错误。

T031 的“exit 无 result”park 现在写 `recovery_reason='exit_without_result'`（同一个
`parkRunForRecoveryTx`，新增 reason 参数）；两类原因都走同一个 recover 检查。

### 1.3 `cmd/admin runs recover --run UUID --evidence FILE [--check-only] [--actor-login LOGIN] [--grace 60s]`

逐项检查（全部对真实 DB/对象存储）：

| 检查 | 拒绝码（exit 1，stdout JSON `status=refused`） |
|---|---|
| Run 存在且 `recovery_pending` | `run_not_found` / `not_recovery_pending`；已有证据且已离开 park → `already_recovered` |
| 有 cancel intent | `cancel_requested`（T036 取消/退出路径拥有该转换，恢复绝不重排被取消的 Run） |
| 恰好一个可恢复 attempt（无 attempt → 文档化的 `needs_reconciliation` 路径） | `needs_reconciliation`；多于一个 live attempt → `live_attempt_conflict` |
| 证据 `execution_unit_id` 与 attempt 一致 | `evidence_unit_mismatch` |
| 旧执行未被证明存活：attempt 已 exited（supervisor 证据）或 heartbeat 早于 grace | `old_execution_still_live` |
| job 恰好一个 held/leased | `job_unavailable` |
| 冻结 manifest artifact：存在/ready/未删/快照 hash 一致/size 一致，`compiler.VerifyManifestBytes` 通过，manifest 的 worker image digest 与快照一致 | `artifact_unavailable` / `manifest_hash_mismatch` / `manifest_invalid` / `execution_identity_mismatch` |
| run input artifact：存在/ready/未删/sha+size 与实际 sealed 字节一致 | `input_deleted` / `input_hash_mismatch` |
| DB 已排空：`checkpoint_connections` 每个 pid+backend_start 均不在 `pg_stat_activity`（PID 复用不误判） | `database_writes_in_flight`（无法解析/查询也 fail closed） |
| 证据形状/必需性 | `evidence_required` / `evidence_invalid` |
| 真实管理员 | `actor_not_found` |

证据文件是闭合形状（未知顶层/嵌套键、自由文本“它死了”一律拒绝）：

```json
{"evidence_kind":"operator_recovery","execution_unit_id":"...",
 "observed_at":"RFC3339","process":{"pid":4123,"command":"ps ...",
 "result":"absent|terminated","observed":"..."},
 "sandbox":{"sandbox_id":"...","command":"runsc list",
 "result":"absent|deleted|stopped"},
 "db_drained":{"command":"...","result":"no_active_backends"},"notes":"..."}
```

仅 `notes` 与 `db_drained` 可选；所有字符串有界；`process.result`/`sandbox.result` 是闭合枚举；
写入的 `evidence_sha256` 是**规范化文档**（`observed_at` 统一为 UTC 微秒）的 JCS 规范字节
SHA-256。`--check-only` 执行全部检查、报告 `checks[]`、零写入。

成功后**一个事务**：记录 evidence 行（`recorded_by`=真实管理员，`resumed_job_id`=job）→
`settleAttemptActiveTx`（T034 唯一结算 helper）→ 旧 attempt `exited_at/exit_evidence/revoked_at`
（COALESCE 保留 supervisor 已写的证据）→ job `ready`（同 kind、`resume_artifact_id` 保留）→
run `queued`/phase idle/revision+1/清 recovery 列（CAS state+revision）→
`releaseAllocationOnceTx`（旧槽恰好释放一次）→ 同事务 `run.status` 事件 → audit
`run.recovery_recovered`（actor=该管理员，metadata 含证据 sha/job/unit/reason）。重复命令在
`recovery_pending` 之外 → `already_recovered`，数据库 UNIQUE(run,attempt) 让它不可能写第二行；
claim 只选 queued，新 attempt 拿到新 token，旧 token 的迟到 result/exit 被
`stale_attempt`/`attempt_evidence_conflict` 拒绝。

### 1.4 API / UI

- `GET /api/v1/runs/{id}` 新增非契约头：`X-Run-Recovery-Reason`、
  `X-Run-Recovery-Since`、`X-Run-Recovery-Evidence-At`、
  `X-Run-Recovery-Evidence-SHA256`（只读投影；attempt token、证据文档本身、对象键都不出现）。
- `/runs` 页新增恢复提示与详情行 `detail-recovery`：显示 `recovery_pending`、原因
  （`lease_expired` / `exit_without_result`）、挂起时间、是否已有受审计证据（含 sha256），
  以及运维命令 `orchara-admin runs sweep-leases --grace 60s [--dry-run]` /
  `orchara-admin runs recover --run <id> --evidence <file> [--check-only]`；明确“不会自动换
  token/不会创建第二个执行者”，**没有恢复按钮**；`mergeRunSnapshot` 保证终态不被迟到的
  recovery 快照翻回；`runLifecycle` 增加 `recovery_pending` 阶段。

### 1.5 与既有票的接口保持

- T031 `parkRunForRecoveryTx` 语义不变（仍 `held`/释放槽/不 requeue），只多了 reason 两列；
  T031 的 started=false requeue 路径不写 recovery 列。
- T032 事件：park 与 recover 的转换都**同事务**投影一条 `run.status`（`recovery_pending` /
  `queued`）。
- T034 结算：sweep 在 park 事务用 `settleAttemptActiveUntilTx`（同一 helper 的显式终点变体）
  按 `lease_deadline` 结算；recover 只在 attempt 仍未结算时调用 `settleAttemptActiveTx`，
  已结算即 no-op。没有第二条结算路径。
- T036：离开 `recovery_pending` 的既有转换（finalizer/立即取消/reconciliation）现在清 recovery
  列（同事务）；cancel intent 在 recover 中一律拒绝，取消语义不被恢复复活。
- T035：requeue 保留 job kind 与 `resume_artifact_id`（真实 resume job 的用例验证 claim 仍返回
  `job_kind=resume` 与签名 `resume_url`）。

### 1.6 明确的边界（不是隐式假设）

- sweep 只挂起 `state='running'` 且无 `cancel_requested_at` 的 Run。`cancelling`/`waiting_*`
  不在扫描范围：STATE-MACHINES §2 没有 `waiting_* → recovery_pending` 边，而重排一个已有
  cancel intent 的 Run 会复活取消。这类“监督者已死且处于 cancelling/waiting”的情形需要
  后续 cancel-recovery/真实 supervisor 路径（见 §5）。
- 巡检是**命令**不是常驻定时器：没有新增 cron/后台轮询进程（F04A 的 supervisor 落地后可调度）。
- recover 的 `needs_reconciliation` 分支只拒绝并打印文档化路径，不替运维做终态决定。

## 2. 验收标准逐条

| # | 验收项（票面 + 设计） | 状态 | 证据 |
|---|---|---|---|
| 1 | 活旧 worker 不出现第二执行 | 通过 | `test_live_old_worker_is_not_replaced_and_recover_refuses`：仅过期 lease（heartbeat 新）→ sweep park、claim 204、recover 拒绝 `old_execution_still_live`；attempt 仍 live、job held、allocation 未释放、无证据/新 attempt；`test_expired_lease_is_parked_without_requeue_or_token_swap` 断言 token 未换、park 后 claim 204 |
| 2 | DB 仍写时不恢复 | 通过 | `test_an_inflight_checkpoint_transaction_blocks_recovery_until_it_commits`：真实 psql 事务的 pid/backend_start 经真实 heartbeat 记录，recover 拒绝 `database_writes_in_flight`（消息含 pid）；事务提交/关闭后同一命令成功 |
| 3 | 缺制品/已删输入明确拒绝 | 通过 | `test_missing_manifest_object_refuses_artifact_unavailable`、`test_deleted_input_refuses_input_deleted`、`test_tampered_manifest_bytes_are_a_hash_refusal`（`manifest_hash_mismatch`）；三者均零写入（仍 park、job held、无证据） |
| 4 | 通过端到端入口实际演示；失败/授权/重试恢复边界有断言 | 通过 | 真实 `cmd/admin` 二进制 + 真实 PG/对象字节/mTLS/HTTP/Node，27 个新用例；拒绝矩阵覆盖 `already_recovered`/`not_recovery_pending`/`cancel_requested`/`evidence_*`/`needs_reconciliation`/`actor_not_found`/用法 exit 2；一次重排后旧 token result 409 `stale_attempt`、exit 409 `attempt_evidence_conflict` |
| 5 | 记录版本、输入/hash、实际命令、原始结果与环境；stub/real 分报、not_run 标注 | 通过 | 本报告 §0/§3/§5 记录环境、命令、计数与 not_run；全部用例 real，无 stub 通过 |

## 3. 证据（实际命令 + 原始结果）

所有命令在 `E:\Orchara\product`（Go 由 `product/tools/toolchain.py` 解析；pytest 用
`python -m pytest`）。

| 命令 | 结果 |
|---|---|
| `go test ./...` | **22 ok / 9 no-test / 0 FAIL**（`internal/execution` 含新增 3 个 recovery 单测） |
| `go vet ./...` | exit 0，无输出 |
| `gofmt -l .` | 无输出 |
| `python tools/sync_spec.py --check` | `no drift: spec copy and contract snapshot match the manifest` |
| `python -m pytest tests/f03/test_t038_migration.py -q` | 5 passed |
| `python -m pytest tests/f03/test_t038_sweep.py -q` | 5 passed |
| `python -m pytest tests/f03/test_t038_recover.py -q` | 7 passed |
| `python -m pytest tests/f03/test_t038_recovery_refusals.py -q` | 6 passed |
| `python -m pytest tests/f03/test_t038_pages.py -q` | 4 passed |
| `python -m pytest tests/f03 -q` | **197 passed in 980.65s**（T038 前 170 + 本票 27） |
| 定向回归：T029 exit、T031 finalizer、T032 events、T034 accounting/deadline、T035 waiting/resume、T036 cancel/unknown + 12 个迁移链文件 | **149 passed**（636.69s；首轮 148 passed + T027 的 `runs` 基线列集按 0024 文档扩展后重跑 10 passed） |
| `python -m pytest tests/f05/test_t033_pages.py -q`（runs.js 回归） | 3 passed |

### 3.1 门禁原始结果

- `tests/f03` 全目录：**197 passed in 980.65s**（T038 前 170 + 本票 27）。本票未运行 25 分钟的多套件
  `pytest tests` 门禁（批次规则，由 orchestrator 运行）。
- 定向回归（21 个文件）全部通过（149 passed），证明 T029–T037 行为未被破坏：T031 的 park 断言、
  T032 的 park 事件、T034 的 deadline park、T035 的 waiting/resume、T036 的 cancel/unknown
  以及 12 个迁移链尾断言（0023→0024）。`tests/f05/test_t033_pages.py` 3 passed（runs.js 未破坏
  沙箱面板）。

## 4. 契约缺口 / 偏离（文档化）

1. **恢复命令不是 HTTP API**：票面/设计只要求运维命令 + owner-facing 事实；`cmd/admin runs
   sweep-leases|recover` 是交付接口，stdout 单 JSON、exit 0/1/2。若后续需要“管理员页触发”，
   应在 F09/管理台另开票，而不是把 operator 证据塞进公开 REST。
2. **`X-Run-Recovery-*` 非契约响应头**：契约 Run schema 是 additionalProperties:false 且无
   recovery 字段，沿用 `X-Manifest-SHA256` / `X-Run-*` 先例。未来契约修订可把
   `recovery_reason/recovery_since` 纳入 Run。
3. **`resumed_job_id` UNIQUE 与第二次 cycle 的偏离**见 §1.1 偏离 3。
4. **`exit_evidence` 复用**：sweep park 的 attempt 从未有 supervisor exit；recover 把运维证据
   文档写进 `exit_evidence`（已 exited 的 T031 park 用 COALESCE 保留 supervisor 原证据）。文档含
   `evidence_kind=operator_recovery` 自我标识；读取方不会把它误当身份证据。
5. **sweep 只处理 running**（§1.6）。

## 5. not_run / partial / 后续

- `not_run`：常驻租约巡检（cron/调度器）未接线；`cancelling`/`waiting_*` 的失联恢复路径未实现
  （需要真实 supervisor 或 cancel-recovery 决策）；真实 F04A supervisor/worker 进程尚未产出这些
  事实（本票由命令/测试模拟运维证据，但 DB/对象/mTLS/HTTP 全部真实）；浏览器 DOM 驱动
  （页面逻辑由 Node 真实执行，Playwright 等仍 `not_run`）；25 分钟多套件 `pytest tests` 门禁
  （批次规则，由 orchestrator 跑）。
- `partial`：无（票面范围全部交付并验证）。
- 后续票接口：
  - **F04A**：真实 supervisor 在 lease 失效/宿主关机时提供可审计的进程/沙箱/DB 排空证据；
    可复用 `platform.run_recovery_evidence` 与 `execution.Recover` 的锁序/检查，不需要新协议。
  - **F09/F07**：管理台/运维页可读取恢复证据元数据（不发正文），以及 `cancelling` 失联的
    终态仲裁。
  - **F06**：真实 operations/receipts 与 recovery 的 unknown-operation 优先级交互（当前
    recover 对 cancel intent 拒绝，未知外部写仍由 T036 的 ledger 守卫）。

## 6. 下一票接口（给别人接的）

- `execution.Service.SweepExpiredLeases(ctx, LeaseSweepRequest) (*LeaseSweepReport, error)`：
  只 park，不 requeue；`--dry-run` 零写入。
- `execution.Service.Recover(ctx, RecoveryRequest) (*RecoveryReport, error)`：拒绝为
  `*execution.RecoveryRefusal{Code, Message}`；成功报告含 `evidence_sha256`/`resumed_job_id`/
  `recorded_by`/`recorded_at`/`checks[]`。
- `execution.Service.RecoveryFacts(ctx, runID) (*RecoveryFacts, error)`：owner detail 的非契约
  投影（reason/since/evidence 时间与 hash/resumed job）。
- 迁移常量：`RecoveryReasonLeaseExpired` / `RecoveryReasonExitWithoutResult`；拒绝码常量
  `RecoveryCode*`（见 §1.3 表）。
- `platform.run_recovery_evidence` / `runs.recovery_reason|recovery_since`（非契约扩展，
  迁移 0024）。

## 7. 复核修复（2026-11-03）

**发现 1：park 未结算，recover 把整个停机窗口计入 T034 活跃时间。** sweep 只把 Run 置
`recovery_pending` 而未调用结算器，recover 事务里的 `settleAttemptActiveTx` 以 `now()` 结算，
于是 `now - started_at`（含 park 之后的全部窗口）被加进 `runs.active_elapsed_ms`。后果是恢复后
的 Run 可能立刻达到 `max_active_ms` 并在下一次 claim 被判 `deadline_exceeded`，页面累计时钟也会
跳变。

**修复：**
- `parkExpiredLease` 在同一事务、同一行锁内调用新增的
  `settleAttemptActiveUntilTx(ctx, tx, runID, lease_deadline)`：活跃区间只到**最后可证明的活动
  终点** `lease_deadline`，`active_settled_at=now()` 记录结算时刻；park 本身不再计时。
- `Recover` 读取 attempt 的 `active_settled_at`，已结算时跳过结算调用（no-op），绝不补记已停机
  窗口；未结算的旧 attempt（例如迁移前遗留）仍走同一个 helper。
- sweep 报告/审计中的 `lease_deadline` 文本不变；只新增内部 typed 时间字段用于结算。

**回归测试：**
`tests/f03/test_t038_recover.py::test_park_settles_active_time_at_the_lease_deadline`
（真实 PG/真实对象/真实 admin 命令）把 attempt 起点移到 10s 前、lease deadline 设为 5s 前、
`max_active_ms=7000`：park 后断言 attempt 已结算且 `active_ms`/`runs.active_elapsed_ms` 都在
`[4000, 7000)`；recover 后累计值不变；下一次 claim 成功（state=running、`error_code` 为空）。
修复前该用例在“park 后 `active_settled_at IS NULL`”处失败，恢复后 claim 会被
`deadline_exceeded` 拒绝。

**发现 2：恢复证据的审计链在后续周期丢失。** `resumed_job_id` 原为 UNIQUE，requeue 又复用同一
job，于是写入器在第二个周期把该列存成 NULL：审计行无法回答“这次恢复重排了哪个 job”。

**修复（新迁移 0024 就地修正，未改 0001–0023 语义）：** 去掉 `resumed_job_id` 的 UNIQUE（保留
FK），`insertRecoveryEvidenceTx` 每个周期都写 requeue 的 job id；once-per-attempt 仍由
`UNIQUE(run_id, attempt_token)` 保证。迁移注释/COMMENT 如实改为“每周期一条可读链接”。

**回归测试：**
- `tests/f03/test_t038_recover.py::test_a_second_recovery_cycle_names_the_same_requeued_job`：
  同一 Run 连续两轮 claim→过期→sweep→recover，两行证据的 `resumed_job_id` 都等于同一 job，
  attempt_token 分别是两轮 token；修复前第二行因 UNIQUE/写入器 NULL 失败。
- `tests/f03/test_t038_migration.py::test_recovery_evidence_identity_and_safe_values_are_enforced`
  改为断言不同 attempt 可命名同一 job（两行）且同 attempt 仍被 UNIQUE 拒绝；
  `test_0024_replays_as_a_noop` 断言不再存在 `run_recovery_evidence_resumed_job_id_key`。
