# F03 T035 验收：等待条件与唯一恢复

- 任务：F03 / GitHub Issue #36（票 `T035`，规划 ID F03.06），分支
  `feat/f05-f03-run-and-tools`
- 状态：**`passed`**（本票交付范围 = `waiting_approval`/`waiting_children` 转换 +
  唯一 `platform.run_waitings` 事实 + 合成审批/子结果 mTLS bridge + 单一幂等
  `maybe_enqueue_resume` + sealed canonical resume payload + 权限/预算/截止/取消重查 +
  运行页等待/恢复解释）。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 mTLS 内部客户端
  （supervisor/worker 证书）+ 真实 HTTP + 真实文件系统对象字节 + 真实 signed sealed 链接 +
  Node 真实执行页面逻辑；无 mock、无跳过。
- 边界（如实）：真实审批属 **F06**、真实子应用/子结果属 **F07**，本票用记录在案的非契约
  bridge 提供合成决定/子结果；取消终态属 **T036**（只记录 intent，不伪造 cancelled）；
  恢复证据/失联扫描属 **T038**；浏览器 DOM 驱动页面仍 `not_run`（与 F01/F02A/F03 既有边界相同）。
- 主要文件：
  - `db/migrations/0021_run_waitings.sql`（唯一新表 + 闭合 CHECK + 权限边界 + 列注释）
  - `internal/execution/waiting.go`（waiting 转换、合成 resolution 校验、`maybeEnqueueResumeTx`、
    owner-facing `WaitingFacts`）
  - `internal/execution/waiting_store.go`（waiting/决定/退出/恢复 job/失败收尾 SQL）
  - `internal/execution/service.go`（result 的 waiting 分支、exit 的 waiting 分支、waiting 重放、
    deadline/权限/预算失败收尾）
  - `internal/api/{execution,internal,debug}.go`（supervisor-only resolution 端点、错误码映射、
    六个非契约 waiting/resume 响应头）
  - `apps/web/src/{runs.html,assets/runs.js}`（等待条件/结果/退出/恢复任务/逻辑会话槽文案）
  - `tests/support/t035.py`、`tests/f03/test_t035_{waiting,resume,resume_refusals,migration,pages}.py`
    （5 文件 24 用例）
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`tools/pg_dev.py`，真实 `cmd/admin migrate`）
  - Python 3.12.2（`python -m pytest`）、Node 24.13.0（页面 JS 语法与行为真实执行）
  - profile `dev-stub`；对象目录在测试 `tmp_path`；部署输入沿用 T025 占位值
    `ORCHARA_WORKER_IMAGE_DIGEST=sha256:aaaa…(64)`、runtime lock =
    `probes/agent_min/requirements.lock.txt`（真实 SHA-256）
  - 冻结的 golden 执行 hash（与 T025/T027/T029/T031/T034 相同）：
    `9cc418d167377e667c77284ce7b35819c7374c0459ec90e41f4131719aef5686`
- 真实/桩分报：**全部 real**（PG/二进制/mTLS/HTTP/对象字节/Node）。测试用 SQL 只做四类种子：
  把 `started_at` 移回过去以确定性产生活跃区间（不 sleep）；短时限/0 预算/禁用 owner/取消 intent
  这些**控制事实**（对应 T036/F06/F07 尚未实现的入口）；本 Run 的 child Run 行（F07 拥有创建入口）；
  以及超时/取消后的确定性观察。迁移测试删除 0021 的 ledger 行后由**真实 `cmd/admin migrate`**
  重放该文件，观察真实 no-op 与约束仍在。

## 0. 工作记录（简短）

1. 读 NOTES、`T031-T038-BRIEFS.md` §T035、票面 #36、EXECUTION §2/§4/§7/§8、
   STATE-MACHINES §2/§3/§4、IMPLEMENTATION-DETAILS §6/§7、API §1/§3、
   T029/T031/T032/T033/T034 报告与代码、`contracts/database.sql`/`openapi.json`；
   确认分支干净、0020 为最新迁移、T034 结算 helper 可复用。
2. TDD 切片（红→绿）：
   - 先写 `tests/f03/test_t035_waiting.py`（首跑红：waiting proposal 仍停在 running）；
   - 实现迁移 0021 + waiting 转换（绿）；写 resume/decision-first/exit-first 测试
     （红：无 résumé job/无 resume_url）→ `maybeEnqueueResumeTx` + artifact 封存（绿）；
   - 写 children/foreign/live 测试（红：foreign child 被存下）→ 在 resolution 时校验 child、
     `maybeEnqueue` 内复检（绿）；
   - 写权限/预算/截止/取消拒绝测试（红：deadline 被 cancel-intent 分支抢先）→ 截止优先、
     失败收尾复用同一 job/token/allocation 收尾（绿）；
   - 写迁移测试（红：0021 不存在）→ 约束/重放（绿）；
   - 写页面 + Node 行为测试（红：无 `detail-waiting`/`describeWaitingResume`）→ Web（绿）。
3. 顺序保证：`maybeEnqueue` 固定锁序 Run→run_waitings→旧 job→attempt；旧 job 在插入 resume
   job 前完成（否则 `one_live_job_per_run` 冲突），全部在同一事务。
4. 跑门禁（§3），写本报告/PROGRESS/NOTES，按 feat/test/docs 三次提交；未推送、未关 issue、
   未改 `docs/spec/` 与 `contracts/`、未改迁移 0001–0020。

## 1. 交付内容

### 1.1 迁移 0021（唯一新迁移）

`platform.run_waitings`，每个 Run 一行（`run_id` PK）：

| 列 | 语义 |
|---|---|
| `kind` | `approval` \| `children`（CHECK）；与 Run 的 `waiting_approval`/`waiting_children` 一一对应 |
| `checkpoint_id` | 契约 waiting proposal 必填的固定 checkpoint；CHECK 非空白 |
| `decision` | 安全 JSON 文档，NULL 直到 resolution 到达；approval=`{kind,outcome∈approved/rejected/expired,reference}`，children=`{kind,outcome∈succeeded/failed,child_run_id}` |
| `decision_at` | 数据库时钟 `now()`；与 decision 成对出现（CHECK），决定文档不含客户端时间 |
| `exit_confirmed_at` | 只由 supervisor 的 exit 证据路径写入；resume 之前必须存在 |
| `resume_job_id` | UNIQUE → `platform.jobs(id)`；与 `one_live_job_per_run` 一起在 DB 层保证不可能有第二个 resume |
| `resume_artifact_id` | sealed canonical resume payload artifact（审计副本；`jobs.resume_artifact_id` 是 claim 读取处） |
| `created_at`/`updated_at` | 显式写入，无 trigger |

CHECK 闭合：kind 枚举、decision/decision_at 成对、decision 必须是 object 且 shape/outcome 与
kind 一致（`reference` 与 `child_run_id` 互斥）、`resume_job_id`/`resume_artifact_id` 只能在
`exit_confirmed_at` 之后出现。**没有** approvals/operations/children/requests/conversations 表
（F06/F07 归属）；`orchara_worker`/PUBLIC 仍零 `platform` 权限；重复 migrate no-op。

### 1.2 waiting 转换（扩展 T031 result 路径）

接受的 `waiting_approval`/`waiting_children` proposal 在同一事务里：

1. 存 proposal/`reported_at`/`checkpoint_id`（T031 已有）；
2. 调 T034 `settleAttemptActiveTx`（等待**不再累计**活跃时间；helper 幂等）；
3. Run → `waiting_approval`/`waiting_children`、`phase='waiting'`、`revision+1`；
4. job → `held`，**保留** live attempt token（attempt 仍可能活着）；
5. 插入 `run_waitings`（kind + checkpoint）；
6. 追加一条同事务 `run.status` 事件（T032 writer）。

`SubmitResult` 对已 waiting 的 Run 只接受**完全相同的 proposal** 作为幂等重放（200）；不同内容
409 `event_conflict`；其他状态 409 `run_state_conflict`。

### 1.3 exit 与顺序

- supervisor `confirmAttemptExit` 接受 `waiting_*` Run + `held` job（worker 不能自证退出，
  supervisor-only 不变）；记录 exit、释放 slot、结算、写 `exit_confirmed_at`、清掉 held job 的
  死 token，然后调用同一个 `maybe_enqueue_resume`。
- **没有 decision 时**：只记录退出事实，Run 保持 waiting、非终态，不发事件（timeline 已有
  waiting 转换），不创建 resume。
- **decision 先到**：resolution 只存决定，不创建 resume；exit 到达后同一事务创建唯一 resume。
- **exit 先到**（先 exit 后 waiting proposal）：exit 按 T031 park `recovery_pending`；随后 waiting
  proposal 把同一 Run 转成 waiting 并在同一调用补齐 `exit_confirmed_at` + resume。
- 两种顺序都只有一个 resume job/一次 allocation 释放/一次旧 job 完成/一次 token 撤销。

### 1.4 合成 resolution bridge

`POST /internal/v1/runs/{run_id}/waiting-resolution`，仅 supervisor mTLS（worker/maintenance 403）：

| 输入 | 行为 |
|---|---|
| approval `{kind,outcome∈approved/rejected/expired,reference}` | 存决定；reference 是不透明安全字符串（1..256 可打印） |
| children `{kind,outcome∈succeeded/failed,child_run_id}` | 先校验 child 属于本 Run 且终态；不满足 409 |
| 未知字段（Command/thread_id/user_id 等） | 400 `invalid_json`（严格解码） |
| 枚举/字段形状错误 | 422 `validation_failed` + `details.field` |
| Run 不存在/非法 UUID | 404 `run_not_found` |
| Run 非 waiting、kind 不匹配、已取消/终态且无匹配决定 | 409 `run_state_conflict` |
| 同一内容重复 | 200 幂等（不写、不新增事件/job） |
| 不同内容 | 409 `decision_conflict`（第一次决定权威） |
| children 的 child 不存在/他人 child/非终态 | 409 `run_state_conflict` + `details.reason` ∈ child_not_found/child_not_owned/child_not_terminal，整笔回滚 |

### 1.5 唯一恢复事务 `maybeEnqueueResumeTx`

锁 Run→run_waitings→旧 job→attempt，要求：

- `decision` + `decision_at` + `exit_confirmed_at` + 非空 `checkpoint_id`；
- Run 未取消（`cancel_requested_at IS NULL`）且非终态；
- 重新鉴权（owner `principals.disabled_at IS NULL`，缺失 fail closed）、预算
  （`budget_limit > 0`）、T034 截止（先 `settleAttemptActiveTx` 再 `checkDeadlineTx`；
  waiting 不累计，所以用已结算 total）；
- children 的 child 属于本 Run（`parent_run_id`）且 terminal；
- 不满足（缺决定/缺退出）→ 只保留事实，返回 pending；
- 取消 intent → 不 resume（T036 才有 cancelled 终态），Run 保持 waiting 非终态；
- 权限/预算/截止失败（exit 已确认）→ **不 resume**，提交
  `failed(permission_revoked|budget_exhausted|deadline_exceeded)`（无 output、job completed、
  token revoked、slot 恰好释放一次、同事务事件带 error_code）。

通过后同一事务：

1. 生成 canonical resume payload（RFC 8785 JCS）：
   `{"run_id","kind","checkpoint_id","decision":{...}}`；
2. 写入真实 sealed object，得到真实 size/sha256；插入 `purpose='resume_input'`、
   owner=Run owner、`state='ready'` 的 artifact；
3. 旧 job → completed、旧 token revoked、allocation `released_at`（T031 exit 已释放时 no-op）；
4. 插入**唯一** `jobs(kind='resume', state='ready', resume_artifact_id=artifact)`；
5. `run_waitings.resume_job_id/resume_artifact_id`、`updated_at`；
6. Run → `queued`、`phase='idle'`、`revision+1`；
7. 同事务 `run.status` 事件。

重复调用：`resume_job_id` 已存在 → 返回同一 job，不写第二行；对象存储不可用 → 503 整笔回滚。

### 1.6 resume claim 与并发

T029 的 `Claim` 已经会读取候选 job 的 `resume_artifact_id` 并 mint 真实 signed `resume_url`；
T035 只验证并补测试：lease `job_kind='resume'`、`resume_artifact_id` 指向新 artifact、
`signed resume_url` 的字节等于 canonical payload、attempt token 是**新的**；`run_attempts` 中
旧 attempt exited/revoked、新 attempt 是唯一 live；旧 job 的 allocation 保持 released 历史，
新 job 取得自己的 holder allocation（`used` 回到 1）。

锁序：T035 之后所有 Run 级转换都先锁 Run（result 提议、`maybeEnqueueResumeTx`、finalizer）；
`ConfirmExit` 原先在一条语句里按 attempt→job→run 取锁，可能与并发的 waiting resolution
（run→waiting→job→attempt）死锁，因此新增 `lockExitRunTx` 先锁 Run 再走原 exit 查询。
定向回归（15 passed）覆盖该改动；exit 的 404/409 语义与证据判定不变。

### 1.7 Web

`runs.html`/`runs.js`：详情新增“等待条件”“恢复任务”两行，数据来自六个非契约响应头：

`X-Run-Waiting-Kind`、`X-Run-Waiting-Outcome`、`X-Run-Waiting-Decided-At`、
`X-Run-Waiting-Exit-Confirmed-At`、`X-Run-Resume-Job-ID`、`X-Run-Resume-Job-State`。

页面明确：等待期间释放 worker 执行槽、逻辑会话槽与 Run 占用到终态；决定/退出/checkpoint 齐全前
“尚未创建恢复任务”；不显示恢复输入内容、恢复 URL 或任何 token；`runLifecycle` 把 waiting 作为
`executing → waiting → terminal` 的真实阶段；`shouldPollRun` 继续轮询等待中的 Run。

## 2. 验收标准逐条

| 票面 AC | 结果 | 证据 |
|---|---|---|
| 决定与退出乱序只创建一个 resume | **passed** | `test_decision_first_and_exit_first_each_create_exactly_one_resume`（两个 Run 各自恰好 1 个 resume job，先到的事实只留事实；重复 resolution/exit 幂等、同 resume job id） |
| 等待释放执行槽但保留会话槽 | **passed** | `test_exit_releases_the_execution_slot_but_keeps_the_run_non_terminal`（exit 后 `used=0`、allocation released、Run 仍 waiting 非终态）；`test_waiting_still_occupies_the_top_level_session_slot`（`one_active_top_run_per_conversation` 仍拒绝同 conversation 的第二个非终态顶层 Run） |
| 恢复重新检查权限预算 | **passed** | `test_disabled_owner_fails_the_run_with_permission_revoked`、`test_zero_budget_fails_the_run_instead_of_resuming`、`test_exceeded_deadline_fails_the_run_instead_of_resuming`（无 resume job/无 `resume_input` artifact，终态 reason 与同事务事件） |
| 通过端到端入口实际演示；失败/授权/重试边界有断言 | **passed** | 全部 24 用例走真实 PG/编译 API/mTLS/HTTP/对象字节；`test_waiting_proposal_stops_the_run_and_holds_the_job`、`test_resume_claim_carries_the_canonical_sealed_payload`、`test_children_*`、`test_cancel_intent_blocks_the_resume_and_leaves_the_run_waiting` |
| 记录版本/输入 hash/命令/原始结果/environment profile；stub/real 分报 | **passed** | 本报告顶部环境与 §3 原始结果；全部 real，无 stub |

任务书补充项：

| 要求 | 结果 |
|---|---|
| 001→0021 迁移、repeat no-op、worker denied、chain-tail 0020→0021 | `test_0021_is_recorded_once_at_the_chain_tail`、`test_0021_replays_as_a_noop`、`test_worker_and_public_get_no_access_to_run_waitings`；10 个既有迁移测试链尾断言适配 |
| waiting 状态规则与事件 | §1.2；`test_waiting_proposal_*` 断言 revision 2→3、状态/phase、held job、同事务 waiting 事件 |
| resolution 端点/错误码/幂等 | §1.4；`test_resolution_requires_a_waiting_run_of_the_same_kind`、`test_resolution_while_the_attempt_is_live_stores_only_the_decision` |
| `maybeEnqueueResume` 单事务/顺序无关/唯一 job | §1.5；两类顺序 + 重复调用用例 |
| resume claim 的 job_kind/resume_url/新 attempt/无两个 live attempt | `test_resume_claim_carries_the_canonical_sealed_payload` |
| children 归属/终态、child-first/exit-first | `test_children_resolution_requires_this_runs_terminal_child`、`test_children_exit_first_resumes_once` |
| 权限/预算/截止/取消 → 不伪造 resume | §1.5；四个 refusal 用例 |
| UI 等待 kind/结果/resume 状态/会话槽说明 + Node 测试 | `test_t035_pages.py`（4 用例，Node 执行 9 组行为断言） |

## 3. 门禁与原始结果

工作目录 `E:\Orchara\product`，分支 `feat/f05-f03-run-and-tools`。

```text
$ python -m pytest tests/f03 -q
141 passed in 877.95s (0:14:37)

$ python -m pytest tests/f03/test_t035_{waiting,resume,resume_refusals,migration,pages}.py -q
24 passed in 162.79s (0:02:42)
（waiting 4、resume 5、resume_refusals 4、migration 7、pages 4）

$ python -m pytest tests/f03/test_t029_exit.py tests/f03/test_t031_identity.py tests/f03/test_t035_waiting.py -q
15 passed in 128.66s
（exit 路径在 Run-first 锁序改动后的定向回归）

$ python -m pytest <既有 10 个迁移测试文件> -q
64 passed in 290.04s
（T023/T025/T026/T027/T028/T029/T030/T032/T033/T034 的链尾断言 0020→0021 后全绿）

$ E:\orchara-toolchains\go\bin\gofmt.exe -l .
(no output)

$ E:\orchara-toolchains\go\bin\go.exe vet ./...
(no output)

$ E:\orchara-toolchains\go\bin\go.exe test ./...
?   	orchara/platform/cmd/*	[no test files]
ok  	orchara/platform/internal/api
ok  	orchara/platform/internal/artifacts
ok  	orchara/platform/internal/background
ok  	orchara/platform/internal/background/handlers
ok  	orchara/platform/internal/catalog
ok  	orchara/platform/internal/compiler
ok  	orchara/platform/internal/config
ok  	orchara/platform/internal/db
ok  	orchara/platform/internal/execution
ok  	orchara/platform/internal/health
ok  	orchara/platform/internal/identity
ok  	orchara/platform/internal/jcs
ok  	orchara/platform/internal/modelproxy
ok  	orchara/platform/internal/objectstore
ok  	orchara/platform/internal/policy
ok  	orchara/platform/internal/presetexec
ok  	orchara/platform/internal/sandbox
ok  	orchara/platform/internal/supervisor
ok  	orchara/platform/internal/tools
ok  	orchara/platform/internal/uuid
ok  	orchara/platform/internal/workflow
（0 FAIL）

$ python tools/sync_spec.py --check
no drift detected
```

未运行（如实）：25 分钟的多套件 `python -m pytest tests` 总门禁（batch 规则，由 orchestrator 执行）；
`tests/f05` 未重跑（本票未改 T033/T030 路径，仅跑其迁移链尾断言）；浏览器 DOM 驱动（无 headless
browser，页面逻辑由 Node 真实执行）。

## 4. 契约缺口 / 取舍

1. `platform.run_waitings` 不在 `contracts/database.sql` 基线中：F06/F07 尚未建 approvals/children，
   本表是记录在案的非契约扩展（ADR 0005），与 `artifact_admissions`(0015)、
   `run_artifact_uploads`(0019) 同类。`sync_spec --check` 保持 no drift。
2. 合成 resolution 端点不是契约端点：F06/F07 接通后应删除/替换，并由真实审批/子结果写入同一
   `run_waitings` 状态机；`reference` 有意不透明，不接受 Command/thread/user 值。
3. 契约 Run schema（additionalProperties:false）没有 waiting/resume 字段：六个 `X-Run-*` 响应头是
   与 `X-Manifest-SHA256`/`X-Run-Active-Elapsed-Ms` 相同的非契约扩展，记录在此。
4. `decision` 不包含客户端时间戳：`decision_at` 由 PostgreSQL `now()` 写入（单一时钟）。
5. 取消 intent 只阻塞 resume，不提交 cancelled：T036 拥有取消状态机；本票不越过它。
6. 恢复载荷 artifact 不写 `run_artifacts` 链接：`listRunArtifacts` 因此不会把它当作用户可下载产物；
   它只由 supervisor 的 claim lease 通过 signed `resume_url` 读取。
7. 迁移的 `resume_job_id` UNIQUE 与 `jobs.one_live_job_per_run` 是真正的并发护栏；Go 的
   早期返回只是避免无谓写入。
8. 新的 `run.status` 事件复用 T032 writer：waiting 转换与 queued 恢复各一条；重复 resolution/exit
   不新增事件。

## 5. 已知限制 / not_run

- 真实 F06 审批 UI/决定、F07 子应用创建与子结果回传：`not_run`（后继 F06/F07 接入同一持久状态机）。
- T036 的 `POST /runs/{id}/cancel`、cancelling→cancelled 仲裁、unknown-operation guard：`not_run`；
  本票只读取 `cancel_requested_at`。
- T038 的失联恢复证据链/自动 re-drive：`not_run`。
- 浏览器 DOM 驱动：`not_run`（同既有票）。
- 多套件全量 pytest 门禁：`not_run`（batch 规则）。

## 6. 下一票接口（T036/T038 可依赖）

- 表：`platform.run_waitings(run_id PK, kind, checkpoint_id, decision, decision_at,
  exit_confirmed_at, resume_job_id UNIQUE, resume_artifact_id, created_at, updated_at)`。
- Go：`execution.Service.WaitingFacts(ctx, runID)`（owner-facing，无锁）、
  `execution.Service.ResolveWaiting(...)`（bridge 内部使用）、
  `settleAttemptActiveTx`（继续作为唯一结算器）。
- 状态约定：waiting Run 的 `phase='waiting'`；exit 后仍 waiting 直到 resume 或失败；
  `cancel_requested_at` 存在时 `maybe_enqueue_resume` 不动作（T036 接管终态）。
- 错误码：409 `decision_conflict`（新）；409 `run_state_conflict`（复用，children refusal 带
  `details.reason`）；404 `run_not_found`；422 `validation_failed`；400 `invalid_json`。
- 作业：恢复 job 是 `jobs.kind='resume'` + `resume_artifact_id`；旧 job 必为 completed，新 job 是
  唯一 live job；额度 holder key 继续是 `job:<job_id>`。
- 响应头：六个 `X-Run-Waiting-*`/`X-Run-Resume-*`（非契约）。
- T038 若要重新驱动：必须尊重 `run_waitings.resume_job_id` 唯一性与 waiting 状态；失败收尾沿用
  `permission_revoked`/`budget_exhausted`/`deadline_exceeded` 三个稳定 code。

## 7. 文件与提交

- `db/migrations/0021_run_waitings.sql`
- `internal/execution/{waiting.go,waiting_store.go,service.go,store.go}`
- `internal/api/{execution.go,internal.go,debug.go}`
- `apps/web/src/{runs.html,assets/runs.js}`
- `tests/support/t035.py`、`tests/f03/test_t035_{waiting,resume,resume_refusals,migration,pages}.py`、
  `tests/f03/test_t031_finalizer.py` + `tests/{f00b,f02a,f03,f05}` 的 10 个迁移链尾断言适配
- `reports/f03/T035-waiting-resume.md`、`PROGRESS.md`、`E:\orchara-notes\f05-f03\NOTES.md`
- 提交：`b39ee95` feat（迁移 + Go 服务/API + Web）、`44e9d0b` test（`tests/f03` + 适配）、docs（本报告 +
  PROGRESS + NOTES）。不推送、不关闭 issue。

## 8. 规格变更

无。`product/docs/spec/**` 与 `product/contracts/**` 未修改；`python tools/sync_spec.py --check`
no drift。发现的契约缺口（`run_waitings` 非契约表、合成 resolution 端点、六个非契约响应头、
409 `decision_conflict`）记录在 §4，由 F06/F07/契约修订统一处理。
