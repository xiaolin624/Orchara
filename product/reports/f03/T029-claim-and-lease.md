# F03 T029 验收：提交运行并只让合格执行者在配额内领取

- 任务：F03 / GitHub Issue #30（票 `T029`，规划 ID F03.01/F03.02），分支 `feat/f05-f03-run-and-tools`
- 状态：**`passed`**（本票范围 = **领取/租约/心跳/退出证据记录 + 只读 Run 列表 + 运行页面**）。
  真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 mTLS 客户端证书 + 真实 HTTP + 真实文件系统对象字节 +
  真实 HMAC 签名 sealed GET 链接 + 真实并发请求；无 mock、无跳过。
- 边界（如实）：result 提议、`run_events`、统一 finalizer、resume、cancel、recovery_pending 与
  终态、SSE 属 T031–T038；本票的退出端点只做证据记录、撤销 token、释放配额与
  “未启动则回到队列 / 已启动则挂起 job”；发布版 Run 入口属 F02B/F05，本票消费 T027 的 debug Run。
- 主要文件：
  - `db/migrations/0016_execution_claims_and_quota.sql`（`run_attempts`、`quota_counters`、
    `quota_allocations`；补齐 T027 延期的两个 attempt 外键；一个 run 至多一个活跃 attempt 的部分唯一索引；
    租约索引；`global/execution_slots` 种子 0/2）
  - `internal/execution/{execution.go,store.go,service.go,service_test.go}`（store + service：
    claim / heartbeat / attempt-exit 原语、typed errors、`compiler.VerifyManifestBytes`、真实 sealed 链接）
  - `internal/api/{execution.go,runs.go,server.go,internal.go,debug.go}`（3 个内部 mTLS 端点、
    `GET /api/v1/runs`、Run 详情上的真实租约响应头）
  - `internal/catalog/debug.go`（抽取共用的 Run 扫描器 + owner-only `ListRuns`）
  - `internal/config/config.go`、`cmd/api/main.go`（`ORCHARA_EXECUTION_LEASE_TTL`，默认 30s）
  - `apps/web/src/{runs.html,assets/runs.js,debug.html,assets/debug.js,apps.html}`（运行列表/详情、
    调试页真实租约、apps/debug 入口）
  - `tests/support/{t029.py,fixtures.py}`、`tests/f03/test_t029_*.py`（9 文件 51 用例）
  - `tests/f00b/test_t026_migration.py`、`tests/f02a/test_t0{23,25,27}_migration.py`（迁移链尾
    0015 → 0016 的预期适配）
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`tools/pg_dev.py`）
  - Python 3.12.2（`python -m pytest`）、Node 24.13.0（页面 JS 语法与行为真实执行）
  - profile `dev-stub`；对象目录在测试 `tmp_path`；HMAC 签名 key 与 mTLS 证书每测试随机生成、
    不落日志/仓库
- 真实/桩分报：**全部 real**。没有 stub 代码路径参与验收。测试里的直接 SQL 仅用于三类真实事实：
  (a) 对抗性破坏（删除/篡改已封存 manifest 字节、把输入 artifact 改成未 ready、注入一次
  attempt INSERT 失败触发器）；(b) 时间推进（把租约改到过去、写 `cancel_requested_at`）；
  (c) schema 不变量（重复 job/attempt/会话 run 行）。每一次断言的业务结果都由真实 API/真实守卫/
  真实数据库产生。

## 0. 工作记录（简短）

1. 读共享 NOTES、`T029-BRIEF.md`、票面、`product/AGENTS.md`、PROGRESS 的 T027/T028 段、F03 规格
   （EXECUTION §1–3/§8、STATE-MACHINES §2–3/§6、IMPLEMENTATION-DETAILS §6–8、API §1/§3/§6）、
   ADR 0004 §4/§5/§38–44、T027 报告 §6、T028 报告 §6、OpenAPI（claimJob/heartbeat/
   confirmAttemptExit/getRun/listRuns 与 Lease/Heartbeat/HeartbeatReply/Run/Accepted）、
   `contracts/database.sql` 的 `run_attempts`/`quota_*`/`runs`/`jobs`/`run_artifacts`；
   确认分支、干净树、0015 为最新迁移。
2. 先写迁移测试（红：0016 不存在、6 失败）→ 写 `0016_execution_claims_and_quota.sql` → 7 用例绿；
   同步适配四个既有迁移链尾断言（0015 → 0016）。
3. 写真实栈 claim happy-path 测试（红：内部端点 404）→ 实现 `internal/execution` 的 store/service、
   三个内部路由、`Deps.ExecutionLeaseTTL` 与 `GET /runs/{id}` 的真实租约响应头 → 绿。
4. 写 heartbeat 9 用例（扩展租约/取消意图/409 三分支/角色/checkpoint_connections）→ 绿；
   写 attempt-exit 10 用例（started=false 回队列、幂等、冲突 409、started=true 挂起、重领取、
   角色、形状）→ 一处真 bug（`started` 缺省被当成 false）修复后绿。
5. 写 listRuns 3、并发 3、事务原子性 5、DB 不变量 3 用例 → 绿；实现 runs 页面、debug 页真实租约、
   apps/debug 入口与 Node 行为测试（5 用例）→ 绿。
6. 跑门禁、写本报告 / `PROGRESS.md` / NOTES，按 feat/test/docs 提交；不推送、不关闭 issue、
   未修改 `docs/spec/**` 与 `contracts/**`。TDD 诚实记录：claim 有过真实的红→绿；heartbeat/exit
   与 claim 在同一个 service 切片里实现，其首个测试运行发生在实现之后（exit 的首轮确实抓到一个
   默认值 bug），其余文件均为先写测试再修实现。

## 1. 交付内容

### 1.1 迁移 `0016_execution_claims_and_quota.sql`

- 按 `contracts/database.sql` 基线创建三表：
  - `platform.run_attempts(token PK, job_id FK, run_id FK, worker_id, execution_unit_id,
    lease_deadline, heartbeat_at, started_at default now(), exited_at, exit_evidence jsonb,
    checkpoint_connections jsonb default '[]', revoked_at, reported_result jsonb, reported_at,
    checkpoint_id)`；
  - `platform.quota_counters(scope, dimension, used, capacity, PK(scope,dimension),
    CHECK used>=0, capacity>=0, used<=capacity)`；
  - `platform.quota_allocations(id PK, scope, dimension, holder_key, released_at,
    FK(scope,dimension), UNIQUE(scope,dimension,holder_key))`。
- 补齐 T027 明确延期给 F03 的两个外键（guarded `ADD CONSTRAINT`）：
  `jobs_current_attempt_token_fkey`、`run_artifacts_attempt_token_fkey`。迁移在 0014 的列/COMMENT
  基础上完成引用，重复 migrate 为 no-op。
- 显式偏离（迁移头逐条写明 owner）：
  1. `run_attempts_one_live_per_run`：`run_attempts(run_id) WHERE exited_at IS NULL AND revoked_at IS NULL`
     的部分唯一索引——契约只保证 token 唯一；一个 run 可以有多条历史 attempt，但至多一条活跃。
  2. 两个运行索引：`run_attempts_live_lease(lease_deadline)`（T031 的过期扫描用）与
     `run_attempts_live_job(job_id)`（心跳按 job 查）。都带同一 live 谓词。
  3. `exit_evidence` 必须是 JSON object、`checkpoint_connections` 必须是 JSON array 的 CHECK
     （契约 body 已经固定这两个形状）。
  4. 种子 `('global','execution_slots',0,2)` `ON CONFLICT DO NOTHING`：容量是部署 knob
     （运维 UPDATE；测试也 UPDATE），重放不覆盖既有容量；缺失该行时 claim fail closed 为 503，
     而不是把“没有配置”解释成 0 容量。
- 边界：不建 requests/conversations/node_attempts/operations/approvals/run_events/usage_ledger；
  不写 `reported_result`；`orchara_worker`/PUBLIC 仍无 `platform` schema/表/序列权限。

### 1.2 `internal/execution`（store + service）

- 单一数据库时钟：租约截止、心跳时间、是否过期、退出时间全部由 SQL `now()` 判定/写入；Go 只把
  数据库读回的 timestamptz 格式化进响应，不做“进程墙钟 vs 租约”的比较。`claim` 的
  `lease_deadline` 由 `INSERT ... RETURNING lease_deadline` 读回，避免“Go 先算一个不会与库一致的
  截止时间”。
- `Claim` 一个事务内按固定顺序：
  1. `SELECT ... FROM jobs j JOIN runs r JOIN execution_snapshots s WHERE j.state='ready'
     AND j.available_at<=now() AND r.state='queued' AND r.deleted_at IS NULL
     AND s.compiler_version = ANY($1) ORDER BY j.available_at,j.id LIMIT 1
     FOR UPDATE OF j, r SKIP LOCKED`；
  2. `SELECT used,capacity FROM quota_counters ... FOR UPDATE`；`used>=capacity` → 回滚，204；
  3. `UPDATE quota_counters SET used=used+1 WHERE used<capacity RETURNING used`（原子性检查在 UPDATE
     自身），`used` 不变则 204；
  4. 取/复用 holder 分配：`INSERT ... ON CONFLICT (scope,dimension,holder_key)
     DO UPDATE SET released_at=NULL WHERE released_at IS NOT NULL RETURNING id`（契约唯一键禁止同一
     holder 的第二行历史；释放后重领取是“重新武装”同一行）；
  5. 插入 `run_attempts`（新 uuid token、`now()+ttl`、worker/execution-unit、heartbeat）；
  6. `jobs` CAS `ready→leased` + `current_attempt_token`；`runs` CAS `queued→running`、phase
     `executing`、`started_at=COALESCE(started_at, now())`、`revision+1`；
  7. 读 snapshot 的 `manifest_artifact_id` 行 → `compiler.VerifyManifestBytes`（只信密封字节，绝不
     重序列化 JSONB 投影）→ 为 manifest 与 run.input_artifact 签发真实短时 sealed GET 链接；
  8. commit。任何一步失败都由 deferred rollback 整笔撤回；返回 `nil` 表示没有兼容 job 或没有容量，
     HTTP 204 且数据库零变化。
- 错误建模（`errors.Is/As`）：`ValidationError{Field}`（422 `details.field`）、`ErrStaleAttempt`
  （409 `stale_attempt`）、`ErrAttemptNotFound`（404）、`ErrEvidenceConflict`（409
  `attempt_evidence_conflict`）、`ErrObjectStoreUnavailable` / `ErrObjectSigningUnavailable` /
  `ErrQuotaUnavailable`（503）、`ErrAllocationConflict`（内部不变量，500）。
- `Heartbeat`：按 `token + path run_id` 取尝试，且 `jobs.current_attempt_token` 必须仍是该 token；
  已退出/已撤销/租约已过期/`execution_unit_id` 不匹配 → 409 `stale_attempt`。续租在 UPDATE 里再次
  用 `lease_deadline > now()` 判定。`checkpoint_connections` 仅当 body 提供时校验并持久化：
  数组、条目只能是 `{pid>0, backend_start RFC3339, application_name string}`、≤32 条；缺省字段
  不覆盖既有值（T031/T032 用 `backend_start` 核实会话排空）。
- `ConfirmExit`：`FOR UPDATE OF a,j,r` 载入 attempt/job/run；unit 必须匹配；已退出时 JSONB 比较
  evidence：相同 → 200 幂等（不重复释放），不同 → 409；已撤销/租约过期/job 非 leased/run 非 running
  → 409（cancel/恢复竞争归 T031/T038，不越权写）。记录 `exited_at + exit_evidence`、`revoked_at`；
  `released_at IS NULL` 谓词保证配额只减一次（`used>0` 防负）；`started=false` → job `ready`、
  run `queued`/phase `idle`/`revision+1`（`started_at` 不动）；其他 evidence（含缺省 `started`）→
  job `held`、run 保持非终态，等 T031 finalizer。

### 1.3 内部 mTLS API（`/internal/v1`）

| operationId | 端点 | 角色 | 成功 | 说明 |
|---|---|---|---|---|
| `claimJob` | `POST /internal/v1/jobs/claim` | supervisor only | 200 `Lease`；204 无 body | body 严格校验 `worker_id`/`supported_compilers`（非空、唯一、可打印）/`execution_unit_id`；无兼容 job 或无容量 → 204 且零变化；字节/签名不可用 → 503 且无租约 |
| `heartbeat` | `POST /internal/v1/runs/{run_id}/heartbeat` | supervisor **或** worker | 200 `HeartbeatReply` | maintenance 403；token 必须绑定该 run/job 且 live；扩展租约、持久化 checkpoint_connections、返回真实 `lease_deadline`/`cancel_requested`/`policy_revision`/`budget_remaining` |
| `confirmAttemptExit` | `POST /internal/v1/attempts/{attempt_id}/exited` | supervisor only | 200 `Accepted`（`{id: attempt_id, state: "exited"}`） | worker/maintenance 403；unit 不匹配/过期/撤销/非 live → 409；重复相同 evidence 幂等 200，冲突 409 |

- `Lease` 是契约的 14 字段（`additionalProperties:false`）：`protocol_version` 恒 `"1.0"`，
  `manifest` 是 snapshot 的 JSONB 投影，`job_kind` start/resume（本票只产生 start；
  `resume_artifact_id`/`resume_url` 对 start 为 `null`，resume 分支代码已按契约签发，留给 T031）。
- `HeartbeatReply` 四字段：`lease_deadline`（DB 新值）、`cancel_requested`（`runs.cancel_requested_at
  IS NOT NULL`）、`policy_revision`（`policy_epochs(scope='acl').revision`，缺失即 500）、
  `budget_remaining`（`runs.budget_limit::text`；**usage ledger 属后续票**，本票明确返回整个 run
  预算而不是伪造已花费数字）。

### 1.4 配额模型与“过期谁负责”

- 维度只有一个：`('global','execution_slots')`。一次 claim 占一个槽；一个活跃 attempt 恰有一个
  holder 分配 `job:<job_id>`；一次 exit 释放该 holder（`released_at=now()` + `used-1`，同事务、
  `released_at IS NULL` 只命中一次）。释放后同一 job 再被领取时复用同一分配行（重新置 NULL），
  因此契约的 `UNIQUE(scope,dimension,holder_key)` 与“历史恰好一行”同时成立。
- **容量耗尽**：claim 先选一个候选 job，再在计数器行锁下判断 `used<capacity`；没有容量时整笔
  回滚（2026 并发测试：容量 1 时 10 并发恰好 1 个 200、1 个 attempt、1 个 allocation、used=1）。
- **租约过期不自动回收（本票明确不实现）**：规格 EXECUTION §3 要求“失联先挂起”，不能因为心跳
  超时自动并发接管。T029 的心跳/退出对过期租约一律 409 `stale_attempt`，claim 也不会抢一个
  `leased` job；过期 attempt 的 `quota_allocations` 仍保持占用。把过期尝试转为
  `recovery_pending`/撤销旧 token/核对旧连接并释放槽位是 **T031 的 recovery/finalizer**；本票不写
  `runs.recovery_pending`、不做 sweep、不做自动 takeover。这是刻意保留的保守恢复边界。

### 1.5 公开 Run 列表与详情

- `GET /api/v1/runs`（`listRuns`）：owner-only（无 grant 路径；管理员只看自己的），
  `WHERE owner_id=... AND deleted_at IS NULL` 与 `(created_at,id) < cursor` 和 `LIMIT` 同在一条
  SQL（过滤在分页前），`ORDER BY created_at DESC, id DESC`，opaque cursor 复用
  `EncodeCursor/DecodeCursor`；`{items,next_cursor}` 精确契约；limit 省略=20、非法=422
  `validation_failed`（`details.field=limit`）、坏 cursor=400 `invalid_cursor`；API key 需
  `application:read`。`getRun` 规则不变（他人/管理员/未知 404）。
- 复用 T027 `catalog.Run` 投影：把 `GetRun` 的扫描抽成 `runColumns + scanRun`，`ListRuns` 共用，
  没有第二份 14/10 字段投影。
- 详情响应新增**非契约**头 `X-Run-Attempt-Started-At` / `X-Run-Lease-Deadline`（仅当尝试存活）：
  Run schema 没有租约字段，用户要看到“领取/租约的真实事实”而不能被伪造进度欺骗；attempt token
  是凭据，**绝不**出现在公开读里。

### 1.6 Web

- 新页面 `GET /runs` + `GET /assets/runs.js`：owner-only 列表（服务端 cursor 分页）、点击加载
  `GET /api/v1/runs/{id}`，显示 state/phase/kind/revision/snapshot id、
  `X-Manifest-SHA256`、以及运行中的 `started_at`/`lease_deadline`；没有租约事实时明确写
  “服务器没有返回租约事实，本页不伪造进度”。全部 `textContent`，无 HTML 拼接用户内容、无
  localStorage/sessionStorage/查询串凭据。
- 调试页 `debug.html`/`debug.js`：新增 phase 与“领取/租约”行、`/runs` 导航；轮询结果若带租约头
  就展示真实时间。apps 页新增 `/runs` 导航。

### 1.7 测试（`tests/f03`，全部真实栈）

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `test_t029_migration.py` | 7 | 链尾 0016、三表列/类型/默认值、两个补齐 FK + 反例、活跃 attempt 唯一索引行为、租约索引、配额种子/容量 knob/CHECK/唯一 holder、worker/PUBLIC 拒绝、重复 migrate no-op |
| `test_t029_claim.py` | 6 | 200 Lease 14 字段逐项；attempt/job/run/revision=2/配额真实；manifest_url/input_url 下载精确密封字节；`X-Run-Lease-Deadline`；不兼容 compiler 204 零行；已租 job 不再发放；worker/maintenance 403；body 定位校验与 400；公开 listener 404 |
| `test_t029_heartbeat.py` | 9 | 租约延长（先 SQL 改到 5s 再续）、heartbeat_at、DB 截止时间一致；cancel_requested 真实写；过期/撤销/错 run/未知 token/错 unit 409 且零变化；worker 可、maintenance 403；`policy_revision`=DB epoch；`budget_remaining`=run 预算串；checkpoint_connections 持久化/形状 422/缺省不覆盖；body UUID/字段校验 |
| `test_t029_exit.py` | 10 | started=false：exited+revoked、job ready、run queued/phase idle、revision+1、started_at 不变、used=0、allocation released、无终态；相同 evidence 幂等（不同 key 顺序）不二次释放；冲突 409 零变化；started=true/缺省 started → job held、run 非终态、slot 释放；重领取成功且复用同一 allocation；worker 403；unit 不匹配/未知/畸形/过期 409/404 且不落证据；evidence 形状 422/400；exit 后心跳 409 |
| `test_t029_list_runs.py` | 3 | 三 run 稳定顺序与 opaque cursor 两页；第二 owner 只见自己；管理员与无 run 的普通用户空列表；详情 404；limit 非法 422、坏 cursor 400、缺省 200；API key scope 403/200 |
| `test_t029_concurrency.py` | 3 | 容量 1 + 10 并发 → 恰 1 个 200/1 attempt/1 allocation/used=1，另一 run 仍 queued、job ready；容量 2 + 两 run → 2 个 200、两 run 各 1 attempt、used=2；单 run 10 并发 → 恰 1 attempt |
| `test_t029_atomicity.py` | 5 | 删除密封 manifest / 篡改字节 / 输入 artifact 未 ready / 无签名 key（第二个真实 API 进程）/ attempt INSERT 触发器强制失败 → 全部 503/500 且零 attempt/零 allocation/used=0/job ready/run queued rev1；移除故障后可正常领取 |
| `test_t029_invariants.py` | 3 | 同一 run 两个 live job 不可能（ready/held 与 leased 并存均拒绝；completed 可）；同 conversation 两个活跃顶层 run 不可能、终态后放行、child run 不受索引约束；同 run 两个 live attempt 不可能，exited 后可再插入 |
| `test_t029_pages.py` | 5 | `/runs` 页面结构与链接、runs.js 资产标记、apps/debug 入口与 phase/lease 标记、Node 语法检查（runs/debug/apps）、runs.js Node 行为（路径编码、投影、响应头读取、queued/running 有/无租约/终态文案且无百分比） |
| `internal/execution/service_test.go` | 4（含子测试） | claim body 归一化/定位错误、checkpoint_connections 形状与归一化、只有显式 `started:false` 才释放、LeaseTTL 归一化与 BaseURL |

## 2. 票面验收逐条

| # | 验收 | 状态 | 证据 |
|---|---|---|---|
| 1 | 同请求最多一顶层 Run | 通过（既有约束 + 本票不破坏） | `runs.request_id UNIQUE` 自 0014；T029 的 claim 只允许 `run.state='queued'` 且 `one_live_job_per_run` 阻止一个 run 两个 live job（`test_t029_invariants.py`）；并发测试证明一个 run 最多一个 attempt |
| 2 | 同会话不双活 | 通过 | `one_active_top_run_per_conversation` 真实行测试：第二个活跃顶层 run 被拒、终态后放行、child run 被部分索引排除（`test_t029_invariants.py`） |
| 3 | 事务失败不遗留半个 job/事件 | 通过（本票不写事件） | 5 个原子性用例（字节缺失/篡改/输入未 ready/无签名 key/触发器失败）断言零 attempt、零 allocation、used=0、job ready、run queued rev1；事件写入属 T031，本票不产生事件 |
| 4 | 普通 worker 不能领取任务 | 通过 | worker 与 maintenance 证书 claim → 403 `forbidden`，job 仍 ready、零 attempt（`test_t029_claim.py`）；worker 也不能自证退出（`test_t029_exit.py`）；maintenance 不能心跳（`test_t029_heartbeat.py`） |
| 5 | 并发领取不超发 | 通过 | 容量 1 × 10 并发恰 1 个 200；容量 2 × 两 run 恰 2 个且不同 run；单 run × 10 并发恰 1 attempt（`test_t029_concurrency.py`） |
| 6 | 启动失败有未启动证据 | 通过 | `evidence.started=false` → `run_attempts.exited_at/exit_evidence/revoked_at` 持久化、job 回 ready、run 回 queued/phase idle、`started_at` 不变、quota used=0、重复幂等；重领取成功（`test_t029_exit.py`） |
| 7 | 通过端到端入口实际演示；失败、当前授权与重试/恢复边界有断言，不以单层 mock 成功代替 | 通过 | 全部 51 用例走真实 PG + 真实编译二进制 + 真实 mTLS/HTTP + 真实对象字节；授权 403/404/409、容量 204、失败回滚、重领取边界均有断言；恢复/recovery 明确未实现（§5） |
| 8 | 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件标 blocked，未运行标 not_run | 通过 | 本报告页首记录版本/环境；§3 记录命令与原始结果；§4/§5 记录偏离与 not_run；本票无 blocked 项；stub/real = 全部 real |

## 3. 实际命令与原始结果

环境：`E:\Orchara\product`，profile `dev-stub`，隔离 PG 16.4（127.0.0.1:55432），Go 1.27.3，
Python 3.12.2，Node 24.13.0。

```text
$ python -m pytest tests/f03 -q
51 passed in 192.52s (0:03:12)

$ go test ./...
18 packages ok（internal/api、artifacts、background、background/handlers、catalog、compiler、
config、db、execution、health、identity、jcs、modelproxy、objectstore、policy、supervisor、uuid、
workflow），6 个 cmd/db 包 no test files，0 FAIL

$ go vet ./...
（无输出，exit 0）

$ gofmt -l .
（无输出）

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

说明：按批次硬约束不运行五套件/全量 `pytest tests`（由 orchestrator 在收尾运行）。本票额外只运行了
`tests/f03` 与 Go 门禁；既有迁移链尾断言的适配仅做了静态编译检查（`python -m py_compile`），其真实
运行由 orchestrator 的更大套件覆盖。

## 4. 契约缺口与显式偏离（如实）

1. **Run 详情新增非契约响应头** `X-Run-Attempt-Started-At`、`X-Run-Lease-Deadline`（仅尝试存活时）。
   `Run` schema 没有租约字段，而用户需要看到领取/租约的事实而不是伪造进度。与 `X-Manifest-SHA256`
   同一先例（ADR 0004 §42）；后续契约修订应把租约事实变成正式字段或提供读模型。attempt token 永不
   通过公开读暴露。
2. **新增错误码**：`attempt_not_found`(404，exit 未知/畸形 attempt)、
   `attempt_evidence_conflict`(409，已退出 attempt 的不同 evidence)、
   `execution_capacity_unavailable`(503，配额计数器行缺失)。复用 `stale_attempt`(409)、
   `object_store_unavailable`/`object_signing_unavailable`(503)、`validation_failed`(422,
   `details.field`)、`invalid_json`(400)、`forbidden`(403)、`insufficient_scope`(403)、
   `resource_not_found`(404)、`invalid_cursor`(400)、`internal_error`(500)。
3. **`Accepted` 的取值契约未定义**：exit 返回 `{id: attempt_id, state: "exited"}`（与 T022 心跳/
   结果返回 job 的 Accepted 同一习惯：id 是路径资源、state 是本次操作后的稳定事实）。
4. **`budget_remaining` = `runs.budget_limit`**（十进制字符串，DB `numeric::text`）。usage ledger、
   预留/结算属后续票；本报告明确这是“整个 run 预算”而不是伪造的剩余额。
5. **配额分配行复用**：契约 `UNIQUE(scope,dimension,holder_key)` 不允许同一 holder 的第二行历史，
   因此重新领取时 `released_at=NULL` 重新武装原行，而不是 INSERT 新行；`released_at` 因此只表达
   “当前是否持有”，不是逐次租约审计账本。
6. **claim 只选 `run.state='queued'`**：resume job（waiting_approval/waiting_children）的领取与
   resume payload 属 T031；本票实现了 `resume_artifact_id/resume_url` 的签发分支但不会产生
   resume job（标注 not_run）。
7. **`started_at` 不因“未启动”回退**：claim 在第一次领取时写 `started_at`，started=false 退出时
   按票面“保持 unchanged”不修改它；“未启动”的权威证据是持久化的 `exit_evidence`，不是时间列。
8. **过期租约的回收/释放属 T031**（见 §1.4）：本票不写 recovery_pending、不 sweep、不自动
   takeover；过期后心跳/退出均 409。
9. **部署 knob**：新增 `ORCHARA_EXECUTION_LEASE_TTL`（默认 30s，允许 1s–1h）。测试使用默认值；
   非默认值仅由 config 解析路径覆盖（见 §5）。
10. **测试专用直接 SQL/触发器**：只用于破坏字节、推进 DB 时间、写 cancel 意图、验证 schema 不变量
    和强制 INSERT 失败；不替代任何业务端点。

## 5. 边界与未运行（not_run）

- `not_run`：worker/supervisor 真实进程与镜像（F04A/F15A 才构建；测试客户端就是“supervisor”，
  真实 mTLS/HTTP/DB 都是真的）。
- `not_run`：result 提议、`run_attempts.reported_result`/`reported_at`/`checkpoint_id` 写入、
  `run_events`/SSE、统一 finalizer、resume job、终态 `succeeded/failed/cancelled`、cancel 端点、
  `recovery_pending`、过期租约 sweep 与槽位自动回收（T031–T038）。
- `not_run`：已发布 Run 的生产入口（F05/F02B）；本票只消费 T027 的 debug Run。
- `not_run`：`run_artifacts.attempt_token` 的 output/attempt 关系写入（外键已就位，写入属后续票）。
- `not_run`：非 `global/execution_slots` 的配额维度、按用户公平队列（EXECUTION §7 的后续工作）。
- `not_run`：`ORCHARA_EXECUTION_LEASE_TTL` 非默认值的真实栈行为（配置解析与归一化有 Go 单测）。
- `not_run`：浏览器 DOM 驱动页面（与 F01/T023/T027 相同限制；页面 JS 由 Node 真实执行核心逻辑）。
- `not_run`：全量 `pytest tests`（按本批硬约束，由 orchestrator 运行）。

## 6. 后续票可依赖的接口与表

T030/T031/T032/T038 可直接依赖：

- 表：`platform.run_attempts`（token PK；`job_id/run_id/worker_id/execution_unit_id/
  lease_deadline/heartbeat_at/started_at/exited_at/exit_evidence/revoked_at/
  checkpoint_connections/reported_result/reported_at/checkpoint_id`）；
  `platform.quota_counters('global','execution_slots',used,capacity)`；
  `platform.quota_allocations(holder_key='job:<job_id>', released_at)`；
  两个补齐的外键 `jobs_current_attempt_token_fkey`/`run_artifacts_attempt_token_fkey`；
  部分唯一索引 `run_attempts_one_live_per_run`。
- Go 入口：`execution.New(db, objects, signer, baseURL, leaseTTL)`；
  `Service.Claim(ctx, ClaimRequest)` → `(*Lease, error)`（nil,nil=204）；
  `Service.Heartbeat(ctx, HeartbeatRequest)` → `(*HeartbeatReply, error)`；
  `Service.ConfirmExit(ctx, ExitRequest)` → `(*ExitResult, error)`；
  `Service.LiveLease(ctx, runID)` → 非秘密租约事实。typed errors 见 §1.2。
- 内部端点/错误：§1.3 与 §4.2。角色规则：claim/exit 只 supervisor；heartbeat supervisor|worker；
  maintenance 全部 403。
- `Lease` 的 `resume_artifact_id`/`resume_url` 已按契约签发（仅当 job.resume_artifact_id 非空）；
  T031 建 resume job 后无需改协议。
- **T031 必须承接**：result 提议持久化、退出与结果的统一幂等 finalizer（任一先到只存证据）、
  terminal/cancel 事件、过期租约的保守恢复（`recovery_pending`，证明旧执行单元/DB 在途写排空后
  才撤销旧 token 并释放槽位；`releaseAllocationTx` 的 `released_at IS NULL` 保证重复释放幂等）、
  `run_artifacts.attempt_token` 的 attempt/output 关系。T038 承接 cancel 仲裁：本票只把
  `cancel_requested_at` 作为心跳事实返回，不阻止 started=false 的 requeue。
- 公开读：`GET /api/v1/runs`（owner-only、`items/next_cursor`）与 `GET /api/v1/runs/{run_id}`
  的非契约租约头；`catalog.Run` 投影与 cursor 助手可复用。

## 7. 文件与提交

- `db/migrations/0016_execution_claims_and_quota.sql`
- `internal/execution/{execution.go,store.go,service.go,service_test.go}`
- `internal/api/{execution.go,runs.go,server.go,internal.go,debug.go}`
- `internal/catalog/debug.go`、`internal/config/config.go`、`cmd/api/main.go`
- `apps/web/src/{runs.html,assets/runs.js,debug.html,assets/debug.js,apps.html}`
- `tests/support/{t029.py,fixtures.py}`、`tests/f03/` 9 文件 51 用例
- `tests/f00b/test_t026_migration.py`、`tests/f02a/test_t0{23,25,27}_migration.py`
- `reports/f03/T029-claim-and-lease.md`、`PROGRESS.md`、`E:\orchara-notes\f05-f03\NOTES.md`
- 提交：feat（迁移 + Go 服务/端点 + Web）、test（`tests/f03` + 链尾适配）、docs（本报告 +
  PROGRESS + NOTES）。不推送、不关闭 issue。

## 8. 规格变更

无。`product/docs/spec/**` 与 `product/contracts/**` 未修改；`python tools/sync_spec.py --check`
no drift。发现的契约缺口（租约事实无字段、Accepted 取值未定义、新错误码、budget_remaining 语义）
记录在 §4，由后续契约修订统一处理。

## 第二次独立复核修复（2026-11-03）

- **缺陷**：领取只校验 Run/job/snapshot 形状，不复查 owner 当前状态；管理员禁用 owner 后，
  已排队 Run 仍会被 claim 成 running（违反 EXECUTION §1/§8“执行仍复查当前撤销状态”）。
- **修复**：`Service.Claim` 在同一事务开头调用 `failDisabledOwnerRunsTx`：把 disabled owner 的
  `queued` Run 置 `failed(owner_disabled)`、`revision+1`、`finished_at`，void 其 never-run job，
  同事务写 `run.status` 事件；候选查询同时 `JOIN principals ... disabled_at IS NULL`。无 lease
  的返回路径（无候选/配额满）也会提交这些失败，避免被 deferred rollback 撤销。
- **证明**：`tests/f03/test_t029_claim.py::test_a_disabled_owner_is_never_leased_and_the_run_fails_closed`
  （真实 PATCH 禁用 owner → claim 204、Run failed/owner_disabled、job void、零 attempt、配额 used=0）。
- 状态仍为 `passed`；该检查只覆盖 owner 禁用，发布版 ACL/授权快照属 F07/F02B。
