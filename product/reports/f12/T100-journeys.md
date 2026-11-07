# F12/T100 复现账号到发布和后台执行的关键旅程（GitHub #101）

**状态：`passed`**（五条旅程全部真实跑通，独立真值核对，失败/授权/重试边界有断言；
1 条已知产品缺口按 `blocked` 单独声明，1 个真实厂商模型变体按环境 `blocked`，没有把它写成通过）。

| # | journey_id | 旅程 | 状态 | 用时（旅程体，秒） | 独立真值 |
|---|---|---|---|---|---|
| 1 | `T100-J1` | 账号启动：migrate → bootstrap → 邀请 → 激活 → 登录 | `passed` | 6.297 | `schema_migrations`/`principals`/`users`/`role_bindings`/`activation_tokens`/`sessions`/`audit_events` + 真实 CLI 退出码 |
| 2 | `T100-J2` | 模型登记 → validate → `GET evaluations/{id}` → 通过（dev-stub） | `passed` | 3.937 | `model_profile_versions`/`evaluation_jobs`/`evaluation_reports`/封存 report+dataset 字节/`platform_model_calls` |
| 3 | `T100-J3` | 草稿 → validate → 真实五类评测 → 审核 → 不可变发布版本 | `passed` | 12.968 | `manifest_artifacts` 封存字节/`evaluation_*`/`publication_reviews`/`application_versions`/`applications.active_version_id`/`outbox`/`audit_events` |
| 4 | `T100-J4` | worker 终结双证据：`submitWorkerResult` + `confirmAttemptExit`（两种顺序） | `passed` | 10.641 | `runs`/`jobs`/`run_attempts`/`quota_counters`/`quota_allocations`/`run_artifacts`/封存 output 字节 |
| 5 | `T100-J5` | 后台任务：`claimBackgroundJob` → `heartbeatBackgroundJob` → `completeBackgroundJob` | `passed` | 6.140 | `background_jobs`/`background_job_artifacts`/`artifacts`/封存 summary 字节/`idempotency_keys` |
| — | `GAP-chat-input-as-run-input` | 聊天正文作为已发布 Run 输入（F08↔F04B 产品缺口） | `blocked` | — | `runs.state/error_code/output_artifact_id` + `requests` 封存输入字节 + `run_node_outputs` |

上表用时是**旅程体**耗时（`journey.duration_seconds`，不含每个测试模块一次性构建真实栈 fixture 的
约 10–15 秒）。机器可读证据：`reports/f12/T100-journeys.json`（每条旅程的
`entry_commands / entry_http / truth_sources / assertions / facts / variants / duration_seconds`，
以及 `known_gaps[]`）；本报告只摘录关键值，逐条 UUID 与逐项断言以 JSON 为准（每次运行 UUID 不同）。

## 1. 票面 AC 对照（issue #101）

| AC | 结论 | 证据 |
|---|---|---|
| 账号启动、模型验证、草稿评测发布旅程真实完成 | **passed** | J1：真实 `cmd/admin migrate`+`bootstrap`、真实 HTTP 邀请/激活/登录；J2：真实 `POST /models`+`validate`+`cmd/maintenance model_validate`+三个契约读取；J3：真实编译器 validate → 真实五类评测（normal 与 over-limit 进入 runsc；tool-failure 用错误 rootfs digest 制造真实 `sandbox_unavailable`；两个 bad-schema 在 start 节点被拒）→ 真实 submit/decision → 不可变 version。JSON `journeys[0..2]` |
| worker 终结双证据与后台处理旅程真实完成 | **passed** | J4：真实 claim + 真实 run-scoped artifact 注册 + `submitWorkerResult` + `confirmAttemptExit`，**result-first 与 exit-first 两种顺序**都恰好一次终态，另有真实 `cmd/supervisor exec --once` 变体；J5：真实 `tool_run` 处理器 + 精确的 claim/heartbeat/complete 协议（含 retry/recovery）。JSON `journeys[3..4]` |
| 各旅程保存正式 API/数据库或 stub 结果 | **passed** | 每条旅程的 HTTP 序列在 `entry_http`，命令在 `entry_commands`，独立真值在 `truth_sources`，实测值（ID、hash、状态、license/ledger 行数）在 `facts`；J2 的 stub 结果在 `facts.model_registration`/`facts.independent_truth`（`report_provider=stub`、`report_stub=true`）与 `variants[real-vendor-model]=blocked` 分报 |
| 端到端真实演示；失败、当前授权与重试/恢复边界有断言 | **passed** | 五条旅程共 56 条独立断言；每条的授权/失败/重试边界见 §3；没有用单层 mock 代替（唯一 stub 是产品自带的 `dev-stub` 模型适配器，明示为 stub） |
| 记录版本、输入/hash、命令、原始结果与环境 profile；stub/real 分报；缺条件 blocked、未运行 not_run | **passed** | §5 记录 Go/Python/PG/runsc/契约 sha/git head/迁移头；每条旅程 facts 里记录输入/输出 sha256；§6 为真实命令原始 tail；stub 只在 J2 且单列；真实厂商模型变体 `blocked`、若主机无 runsc 则 J3/J4 变体会 `blocked/not_run`（本机 runsc 可用，均已跑） |

## 2. 判定口径（沿用 ENV §5）

`passed` = 旅程真实跑通 + 真值独立读取 + 失败边界有断言 + 缺口诚实声明。本票没有为了让门禁变绿而：
绕开正式入口、直接写业务表造状态、把 200 当成功、把 `dev-stub` 当真实模型、把产品缺口写成测试写法问题。

## 3. 五条旅程逐条证据

### 3.1 T100-J1 账号启动（migrate → bootstrap → 邀请 → 激活 → 登录）

真实入口：

```text
cmd/admin migrate                                                    (真实编译二进制，exit 0，52 条迁移)
cmd/admin bootstrap --login admin --display-name "Platform Admin"    (真实编译二进制，密码走 stdin)
cmd/admin bootstrap --login admin2 ...                               (边界：第二次 bootstrap)
POST /api/v1/auth/login {admin} → GET /api/v1/auth/me
POST /api/v1/admin/users {roles:[creator]} (admin+CSRF) → 201 {principal_id, activation_token, expires_at}
POST /api/v1/auth/activate {activation_token, password} → 200 {id, state:activated}
POST /api/v1/auth/login {t100.creator-*} → 200 → GET /api/v1/auth/me
```

独立真值：

- `platform.schema_migrations`：连续链 52 条，头 `0052_handoff_scope_bounds.sql`（不是从 API 推断）。
- `platform.principals/users/role_bindings/audit_events`：恰好 1 个 admin，`role=admin`，
  `password_hash=$argon2id$v=19$m=65536,t=3,p=2$…`（明文密码不在 hash 里），audit action=`admin.bootstrap`。
- 邀请：用户行 `password_hash='!pending-activation'`、`must_change_password=true`；
  `activation_tokens.token_hash = sha256(raw token)` 且 `token_hash != raw`、`consumed_at IS NULL`；
  `role_bindings.role='creator'`；audit `user.invited` 恰好 1 条。
- 登录：`platform.sessions.token_hash = sha256(cookie)`、`csrf_hash = sha256(csrf)`、`revoked_at IS NULL`。
- 激活：`password_hash` 变 argon2id、`must_change_password=false`、`consumed_at IS NOT NULL`。

边界断言（全部真实 4xx 且**附带“没有副作用”**）：

| 边界 | 结果 | 副作用核对 |
|---|---|---|
| 第二次 `cmd/admin bootstrap` | 退出码非 0，输出含 `already exists` | principals/users 仍为 1 |
| 匿名 `POST /admin/users` | 401 `unauthenticated` | — |
| 非管理员 creator 邀请 | 403 `forbidden` | `users` 无 `should-not-exist` |
| 重复邀请同一 login | 409 `login_taken` | 仍 1 行 |
| 缺 CSRF 头的邀请 | 403 `csrf_invalid` | 未创建用户 |
| 非法角色 `superuser` | 422 `validation_failed` | 未创建用户 |
| 未知激活 token | 409 `activation_invalid` | — |
| 弱密码激活 | 422 `validation_failed` | token 未消费（`consumed_at IS NULL`、sentinel 不变），随后同一 token 用合规密码激活成功 |
| 过期激活 token | 409 `activation_expired` | `consumed_at IS NULL` |
| 复用已消费 token | 409 `activation_consumed` | — |
| 错误密码登录 | 401 `invalid_credentials` | `sessions` 行数不变 |

> **时钟型边界的声明**：`activation_expired` 分支由一条 `UPDATE platform.activation_tokens SET expires_at =
> now() - interval '1 minute'` 驱动——只把服务端截止时间移到过去，token/用户/会话全部由真实邀请 API 创建。
> 这是本旅程**唯一**的测试侧 SQL 写（另见 §3.3/§3.4 的部署种子），已写进 `journey.notes`。

### 3.2 T100-J2 模型登记 → validate → 评测读取 → 通过（dev-stub）

真实入口：

```text
POST /api/v1/models {provider:stub, model_id:stub-model-1, endpoint_ref:stub-endpoint,
                     secret_ref:stub-credential, config, data_destination:local-stub}  → 201 {id, state:pending}
POST /api/v1/models/{id}/validate (Idempotency-Key)                                     → 202 EvaluationJob(kind=model, state=queued)
cmd/maintenance run --once --kinds model_validate   (+ 并行真实 artifact_finalize 进程)
GET /api/v1/models/{id}; GET /api/v1/evaluations/{id}; GET /api/v1/evaluation-reports/{id}
```

独立真值：

- `model_profile_versions`：`validation_state=passed`、`evaluation_report_id` 绑定、
  `manifest_sha256`（本次 `81c38bce…9494`）；注册后先是 `pending`。
- `evaluation_jobs`：`state=succeeded`、`report_id` 与 API 读一致、`background_job_id` 是真实 job。
- `evaluation_reports`：`status=passed`、`manifest_sha256` 等于模型 manifest、
  `dataset_sha256` 等于**对象目录里封存 dataset 字节**的 sha256（独立重算，不用 API 返回值）。
- 封存 compatibility 文档（对象目录直接读）：`kind=model_compatibility`、`model_profile_version_id` 匹配、
  `provider=stub`、`stub=true`、`compatible=true`、`check.name=structured_output`、
  `fixture_sha256` = 报告 dataset sha。
- `platform_model_calls`：恰好 1 条 succeeded 行，绑到该 job 与模型；其 `result_artifact_id` 是适配器调用
  自己的完成产物（ready，字节非空），与兼容性报告产物不同——如实记录为两件产物。

边界断言：

| 边界 | 结果 |
|---|---|
| 非管理员 `POST /models` | 403 `forbidden` |
| 非管理员 `POST /models/{id}/validate` | 403 `forbidden`，`background_jobs` 仍 0 |
| validate 缺 `Idempotency-Key` | 400 `idempotency_key_required`，`details={header: Idempotency-Key}`，无 job |
| 同 key 重放 validate | 202 相同 body，`evaluation_jobs=1`、`background_jobs=1`（没有第二次评测） |
| 不兼容 stub 模型 | job `succeeded` 但报告 `failed`、模型 `failed`、`compatible=false`、`check.reason=structured_output`；非管理员 list 不可见 |
| 未部署的真实 provider（`openai/gpt-real-1`） | job `failed`、错误码 `provider_not_configured`、evaluation `failed`、**不写报告行** |

> **stub/real 分报**：注册、评测任务、维护进程、封存报告与三个契约读取都是真实产品代码；
> 判定值来自产品自带的 `dev-stub` 适配器，报告文档自证 `stub=true`。真实厂商模型/凭据/GPU 在本环境
> 不存在，`variants[real-vendor-model]=blocked`，**不声称**任何真实模型质量。

### 3.3 T100-J3 草稿 → validate → 真实评测 → 审核 → 不可变发布版本

真实入口：

```text
POST /api/v1/admin/users + /auth/activate + /auth/login（creator）
POST /api/v1/applications → PUT /api/v1/applications/{id}/draft (If-Match)
POST /api/v1/applications/{id}/validate                              → 200 {valid:true, errors:[]} + X-Manifest-SHA256
POST /api/v1/applications/{id}/evaluations (5-case dataset artifact)  → 202
cmd/maintenance run --once（全 kind 池）+ cmd/supervisor exec --once（每个 case 一次，WSL2/runsc）
GET /api/v1/evaluations/{id} → GET /api/v1/evaluation-reports/{report_id}
POST /api/v1/applications/{id}/publication-preview（非契约路由，仅预检读取）
POST /api/v1/applications/{id}/submit                                → 202 {id, state:submitted}
POST /api/v1/reviews/{id}/decision（非管理员 403 / 错 hash 409 / admin approve 200 → version_id）
GET /api/v1/versions/{version_id}；GET /api/v1/applications/{id}
```

独立真值：

- 编译器 manifest：`POST validate` 的 `X-Manifest-SHA256` == `platform.manifest_artifacts` 最新行
  `manifest_sha256`，且对象目录里该封存 artifact 的字节 sha256 重算一致（本次 `a35ad882…0022e`）。
- 评测快照：`execution_snapshots.manifest_sha256` == 同一个 validate hash（评测内容与审核内容同一份）。
- 五类评测（封存报告逐 case facts，非测试自述）：`sales-happy-csv run_state=succeeded`、
  `sales-bad-schema-1/-2 run_error_code=node_input_invalid`、`sales-over-limit run_error_code=row_limit_exceeded`、
  `sales-tool-failure run_error_code=sandbox_unavailable`；报告 `status=passed`。
- `publication_reviews`：`state=submitted` → `approved`；`manifest_sha256`/`report_id`/`submitted_by` 匹配；
  `snapshot.author_cases.satisfied=true` 且 categories 恰为五类；`report_status=passed`。
- `application_versions`：恰好 1 行，`version_no=1`、`review_id`、`manifest_sha256`、
  `manifest_artifact_id` == validate 产出的 manifest artifact；封存字节 hash 一致。
- `applications.active_version_id` 指向该 version；`outbox` 恰 1 条 `catalog_index/publish`；
  `audit_events` 含 `publication.approved`；`GET /versions` 与 `GET /applications` 同值。

不可变性（审核通过后真实验证）：

- 第二次 decision → 409 `review_conflict`，`details.state=approved`；
- 相同内容重新 submit → 202 返回**同一个** approved review，`review_count=1`、`version_count=1`；
- 真实编辑草稿（`PUT /draft`）再 `validate` → 新 hash（本次 `01b911e6…fae0`）≠ 已发布 hash；
  已发布 version 行与封存字节**逐字节不变**，active pointer 不变，`GET /versions/{id}` 仍是旧 hash。

边界断言：

| 边界 | 结果 |
|---|---|
| 依赖 pending 模型的草稿 validate | 200 `{valid:false}`，located `dependency_not_ready` @ `/nodes/1/config/model_profile_version`（node `analysis`），无 `X-Manifest-SHA256`、无 manifest artifact、无 version |
| 外主体 submit | 404 `resource_not_found`，review 数不变 |
| 错误 expected manifest hash | 409 `manifest_mismatch` |
| submit 缺 `Idempotency-Key` | 400 `idempotency_key_required` |
| 非管理员 decision | 403 `forbidden` |
| decision 错 hash | 409 `manifest_mismatch`，`details.reviewed_manifest_sha256` 是真实 reviewed hash |
| 已决定 review 再决定 | 409 `review_conflict` |

> **非契约路由声明**：`POST /applications/{id}/publication-preview` 是真实产品路由（`internal/api/server.go`），
> 但**不在** `contracts/openapi.json` 的 106 项内；本旅程只把它当 pre-submit 读，不把它算作契约入口。
>
> **部署种子声明**：预售工具版本（`sales_stats`，T025 deployment input）与执行槽容量（`quota_counters`）
> 是部署值，由 T025/T056 既定 helper 在 creator 存在前种入；旅程本体（应用/草稿/评测/审核/发布）全部走 HTTP。

### 3.4 T100-J4 worker 终结双证据（两种顺序）

输出 artifact 通过产品真实 run-scoped 内部路由注册：
`POST /internal/v1/runs/{id}/artifact-uploads` → 签名 PUT → `complete` → 真实
`cmd/maintenance --kinds artifact_finalize` → `POST /internal/v1/runs/{id}/artifacts`（`relation=output`）。
Run 由真实 `POST /applications/{id}/debug-runs` 创建，lease 由真实 supervisor 证书
`POST /internal/v1/jobs/claim` 取得。

**顺序 A（先 result 后 exit）**：

- 提交 `submitWorkerResult(succeeded, output_artifact_id, checkpoint_id)` → 200；`runs` 行仍是
  `state=running, phase=finalizing, finished_at=NULL`，job 仍 `leased`，attempt 有 `reported_result`
  但 `exited_at IS NULL`，执行槽仍占用（`quota_counters.used=1`）——**proposal 不是终态**。
- `confirmAttemptExit` → 200；`runs` 变 `succeeded/idle/finished_at`，`revision` 从 proposal 的 2 精确 +1 到 3，
  `output_artifact_id` 绑定；job `completed` 且 lease 清空；attempt `exited_at`+`revoked_at` 都非空；
  执行槽释放（`used=0`）；`quota_allocations` 恰好 1 条且 `released_at` 非空；
  `run_artifacts` 恰好 input+output 两条；owner `GET /runs/{id}` 与 artifact content 读回一致。
- 边界：重复相同 exit → 200 且 Run 行不变；迟到 result → 409 `run_terminal`；
  不同 exit evidence → 409 `attempt_evidence_conflict`；三者都不改终态。

**顺序 B（先 exit 后 result）**：

- `confirmAttemptExit` 先行 → `runs.state=recovery_pending`、`phase=idle`、`finished_at=NULL`、`revision=3`；
  job `held`（不是 completed）；执行槽已释放。
- 随后 `submitWorkerResult(succeeded, output_artifact_id)` → 200，Run 一次提交为 `succeeded/idle/finished_at`，
  `revision` 4、output 绑定、job `completed`、唯一 allocation 仍为 released。
- 边界：重复 exit 幂等；迟到 result → 409 `run_terminal`。

**角色与旧 token 边界**：

- `maintenance` 证书 `submitWorkerResult` → 403 `forbidden`，attempt 不写 proposal；
- `worker` 与 `maintenance` 证书 `confirmAttemptExit` → 403 `forbidden`；
- `worker` 提交 `needs_reconciliation` proposal 后 Run 仍 running/finalizing，只有 supervisor exit 才提交为
  `needs_reconciliation`（`finished_at=NULL`，非终态）；迟到 proposal → 409 `run_state_conflict`；
- 用另一 attempt 的 token 提交 → 409 `stale_attempt`，两个 attempt 都未写 proposal。

**真实执行器变体**：真实 `cmd/supervisor exec --once` 认领 start→python→end 的 Run，在 WSL2/runsc 执行，
报告 `status=executed, outcome=succeeded, run_state=succeeded`、sandbox `started=true, exit_code=0`、
cleanup `sandbox_list_matches=0`；DB 中 Run `succeeded`、1 个 attempt `exited_at+revoked_at`、job `completed`；
owner 读 4 条 node results（data/file/log）；产品 Decimal 汇总与本测试**从输入 CSV 独立计算**的
`total=250.00, 东区=200.00, 西区=50.00, order_count=3` 逐字段相等。

### 3.5 T100-J5 后台任务领取 → 心跳 → 结果

**A. 产品真实后台处理器**：

```text
POST /api/v1/tool-runs {tool: sales_stats, artifact_id: <真实上传 CSV>} (Idempotency-Key) → 202 {id,state,tool,artifact_id}
同 key 重放 → 202 相同 body，background_jobs 仍 1
cmd/maintenance run --once --kinds tool_run,artifact_finalize   （真实进程，预设工具真实 Python 子进程）
GET /api/v1/tool-runs/{job_id}；GET /api/v1/background-jobs/{job_id}；GET /api/v1/artifacts/{id}/content
```

独立真值：`background_jobs` 行 `succeeded/attempt_count=1/error_code=NULL/failure_class=NULL/result_event_id 非空/
finished_at 非空`；`background_job_artifacts` 含 `tool_summary`+`tool_artifact` 且 `artifacts.state=ready`；
对象目录 summary 字节 sha256 == artifact 行 sha256，解析出的 Decimal 汇总 == 本测试独立计算值
（`250.00 / 东区200.00 / 西区50.00 / 3`）；content 路由返回同一文档；`platform.idempotency_keys` 恰有 1 条
该 key 的 receipt（`response_code=202`）。契约读 `GET /background-jobs/{id}` 返回 `{id,kind,state,error_code}`
与 tool-runs 读一致。

**B. 精确协议（第二个真实 job，输入是负数金额 CSV）**：

```text
POST /internal/v1/background/jobs/claim {worker_id, supported_kinds:[tool_run]}      （maintenance 证书）
POST /internal/v1/background/jobs/{job_id}/heartbeat {attempt_token}
POST /internal/v1/background/jobs/{job_id}/result {attempt_token, event_id, state, error_code}
```

- `claimBackgroundJob`：200 lease；DB `state=leased/attempt_count=1/lease_token=attempt_token/worker_id` 匹配；
- 第二次 claim → 204（同一 job 不能被两个 worker 领取）；
- `heartbeatBackgroundJob`：200 `{id, state:leased}`；DB `lease_deadline` 前移、token/state 不变；
- 重试/恢复边界：`completeBackgroundJob(state=failed, error_code=tool_timeout)`（在 tool_run 固定重试表内）
  → 200 `{state:ready}`；DB `failure_class=infrastructure`、`attempt_count=1`、无 `result_event_id`、
  无 `finished_at`、lease 清空；旧 token 的 result/heartbeat 都 409 `stale_attempt`；backoff 到点后再 claim
  得到**新 token**、`attempt_count=2`；
- 终态：`completeBackgroundJob(state=failed, error_code=negative_amount)` → 200 `{state:failed}`；
  DB `failure_class=business`、`attempt_count=2`、`result_event_id`/`finished_at` 写入、lease 清空；
  `GET /tool-runs/{id}` 同值（`failed/negative_amount`）；同 event 重放 200 且行不变；不同 event → 409
  `event_conflict`；终态后 claim → 204。

**C. 公开边界**：

| 边界 | 结果 |
|---|---|
| 其他 creator（以及 admin）读 job / summary content | 404 `resource_not_found` |
| 创建缺 `Idempotency-Key` | 400 `idempotency_key_required` |
| 同 key 不同 body | 409 `idempotency_conflict`，不新建 job |
| 未知 tool | 422，`details.field=tool` |
| worker / supervisor 证书 claim 后台 job | 403 `forbidden`（只有 maintenance 容量可领） |

## 4. 已知产品缺口（显式声明，`blocked`）

**`GAP-chat-input-as-run-input`：聊天正文作为已发布 Run 输入 → `node_input_invalid`。**

- 这是 **F08↔F04B 的产品缺口，不是测试写法问题**：`POST /requests` 把 `message` 按字节封存成
  `purpose=request_input` 的 artifact（`internal/requests/submit.go:131`），同一事务把该 artifact 当作 Run 输入
  （`submit.go:646-690`）；执行器要求 Run 输入是 **JSON 对象**（`internal/presetexec/linear.go:177-187`，
  图路径 `graphrun.go:929-937`），因此第一次执行在任何 sandbox 启动前失败。
- 本次用规格内正式入口复现：`POST /conversations` → `POST /requests {message:"把这份销售明细汇总成报告",
  application_version_id:<已发布版本>}`（IMPLEMENTATION-DETAILS §5 明确这是固定版本的正式路径，**不是**绕过路由）
  → 202 `dispatched`，`platform.requests` 的输入 artifact 字节/ sha256 等于消息原文 →
  真实 `cmd/supervisor exec --once` → executor 报告
  `{"status":"failed","outcome":"refused","run_state":"failed","error_code":"node_input_invalid"}` →
  DB `runs.state=failed, error_code=node_input_invalid, output_artifact_id=NULL`，`GET /runs/{id}` 同值，
  `node-results` 为空（0 行）。原始观测在 `T100-journeys.json` 的 `known_gaps[0].observed`。
- **裁决**：该能力记 `blocked`，不计入五条 `passed` 旅程；本票**没有**用“把 JSON 任务文档当聊天消息发”
  的绕法来声称聊天正文可用（T084 的绕法只在 T084 的报告里说明）。T100 的五条旅程本身不依赖自然语言路由，
  所以整票仍为 `passed`；请求→Run 的自然语言旅程属于 **T101**，由那张票按同一口径记 `blocked`。

## 5. 独立真值怎么读的（不是从 200 推断）

| 旅程 | 外部副作用的独立读取 |
|---|---|
| J1 | psql 读 `schema_migrations/principals/users/role_bindings/activation_tokens/sessions/audit_events`；`cmd/admin bootstrap` 二次运行的退出码与输出（不读 API） |
| J2 | psql 读 model/evaluation/report/ledger 行；**对象目录**读封存 report 与 dataset 字节并重算 sha256（dataset_sha256 与报告行比对） |
| J3 | psql 读 review/version/outbox/audit/active pointer；**对象目录**读封存 manifest 字节并重算 sha256；评测 verdict 读封存报告里逐 case 的 facts（不是测试的 case 清单） |
| J4 | psql 读 runs/jobs/run_attempts/quota/run_artifacts；**对象目录**读 output 封存字节并重算 sha256；真实 executor 的 Decimal 汇总与本测试从 CSV 字节独立计算的值比对 |
| J5 | psql 读 background_jobs/background_job_artifacts/artifacts/idempotency_keys；**对象目录**读 summary 字节并重算 sha256 + Decimal 独立计算；internal 协议行状态逐列比对 |

## 6. 环境 profile（`T100-journeys.json.environment`）

| 项 | 值 |
|---|---|
| profile | `ORCHARA_PROFILE=dev-stub`（无厂商凭据/端点/GPU） |
| Go | `go version go1.27.3 windows/amd64` |
| Python / pytest | 3.12.2 / 9.0.3 |
| Node | v24.13.0（本票为 HTTP/内部协议验收，未使用页面脚本） |
| PostgreSQL | 16.4（隔离集群 `127.0.0.1:55432`；每旅程独立库，真实 `cmd/admin migrate` 0001→0052，用完 drop） |
| 迁移头 | `0052_handoff_scope_bounds.sql`（52 条连续迁移） |
| 沙箱 | WSL2 `Ubuntu-24.04` + `runsc release-20241118.0`；rootfs digest `sha256:03c3f647…12ff5` |
| 契约 | `contracts/openapi.json` sha256 `bf191b96…fc3e91`（93 paths / 106 operations，T093 已判 106/106） |
| git HEAD（记录时） | `60d9de0115c006a281050ad5e1a7445c54b9c040`（`integration/f12` tip） |
| 网络 | 仅 loopback；外部真实 HTTP，内部真实 mTLS（worker/supervisor/maintenance 客户端证书） |

## 7. 真实命令与原始 tail

> 所有真实栈套件串行运行；跑前占 `E:\Orchara\.scratch\f12\locks\realstack`，结束删除。原始日志在
> `E:\Orchara\.scratch\f12\T100\`。`tests/f01` 按 ENV §8 的既有缺陷**单独一次进程**运行。

### 7.1 本票套件

PowerShell 不会为原生命令展开 `test_t100_*.py` 通配，所以显式列出五个文件（等价于票面的
`pytest tests/f12/test_t100_*.py -q`）：

```powershell
cd E:\Orchara-wt\t100\product
$env:F12_WRITE_EVIDENCE='1'
python -m pytest tests/f12/test_t100_account.py tests/f12/test_t100_background.py `
    tests/f12/test_t100_models.py tests/f12/test_t100_publication.py `
    tests/f12/test_t100_worker_exit.py -q
```

```text
.....                                                                    [100%]
5 passed in 85.13s (0:01:25)
```

### 7.2 全量 `tests/f12`（不破坏 T093/T095/T096/T098）

```powershell
python -m pytest tests/f12 -q
```

```text
........................................................................ [ 23%]
........................................................................ [ 46%]
........................................................................ [ 69%]
........................................................................ [ 92%]
.........................                                                [100%]
313 passed in 220.60s (0:03:40)
```

### 7.3 `tests/f01`（单跑；既有 collection 冲突不改）

**结论：153 passed, 2 failed（既有红，非本票引入）。** 两条失败都是 T095 改版后的页面 marker 断言未同步
（`login.html` 的角色化跳转取代了固定 `window.location.assign('/admin')`；`apps.html` 的统一 401 出口
`handleAuthFailure` 取代了 `error.status === 401` 字面量）。本票没有改任何页面/脚本；
在**同一 task 分支上的 tracked 改动为空**（只有新增测试/报告文件），并已在
**baseline 60d9de0 detached worktree** 上逐条复跑两条失败，报错逐字相同
（日志 `E:\Orchara\.scratch\f12\T100\baseline-f01-2.log`）：

```text
FF                                                                       [100%]
FAILED tests/f01/test_t017_pages.py::test_login_page_is_served_and_posts_the_contract_path
FAILED tests/f01/test_t019_pages.py::test_the_apps_page_lists_the_visible_applications
2 failed in 26.65s
```

因此这是集成分支上 T095/T098 页面改动与 f01 marker 断言之间的漂移，归 T095/T098 的后续修正，本票按
「既有红」记录，不用 `--ignore`/放宽断言掩盖，也不越界改 T095/T098 的测试。

```powershell
python -m pytest tests/f01 -q
```

```text
.........................................................F.............. [ 46%]
...F.................................................................... [ 92%]
...........                                                              [100%]
================================== FAILURES ===================================
___________ test_login_page_is_served_and_posts_the_contract_path ____________
            "window.location.assign('/admin')",
        ):
>           assert marker in body, f"login.html is missing {marker!r}"
E           AssertionError: login.html is missing "window.location.assign('/admin')"
___________ test_the_apps_page_lists_the_visible_applications ______________
            "requireLogin(",
            "error.status === 401",
            "location.assign('/login')",
        ):
>           assert marker in body, f"apps.html is missing {marker!r}"
E           AssertionError: apps.html is missing 'error.status === 401'
=========================== short test summary info ============================
FAILED tests/f01/test_t017_pages.py::test_login_page_is_served_and_posts_the_contract_path
FAILED tests/f01/test_t019_pages.py::test_the_apps_page_lists_the_visible_applications
2 failed, 153 passed in 925.64s (0:15:25)
```

### 7.4 Go / 静态门禁

```powershell
& go build ./... ; "BUILD=$LASTEXITCODE"
& go vet ./... ; "VET=$LASTEXITCODE"
& 'E:\orchara-toolchains\go\bin\gofmt.exe' -l internal cmd tools
& go test ./...
python tools/sync_spec.py --check
```

```text
BUILD=0
VET=0
GOFMT=0          # gofmt -l internal cmd tools 输出为空
TEST=0           # go test ./...：全部 ok，无 FAIL（节选）
ok      orchara/platform/internal/api                               4.290s
ok      orchara/platform/internal/execution                         3.061s
ok      orchara/platform/internal/presetexec                        5.981s
ok      orchara/platform/internal/sandbox                           5.026s
?       orchara/platform/cmd/api                                     [no test files]
no drift: spec copy and contract snapshot match the manifest
```

## 8. 边界 / `not_run` / 未修

- **真实厂商模型**：`blocked`（无凭据/端点/GPU）。J2 只证明 registry+validation 机制与 stub 报告，
  不声称模型质量；`usage.updated` 事件仍无 emitter（T086 已记，属 F06，本票不修）。
- **无浏览器 DOM 内容**：本票全部是 HTTP + 内部 mTLS 协议验收，不涉及页面；没有用 Node+shim 冒充浏览器。
- **`node_input_invalid` 缺口**：见 §4，`blocked`，归属 F08↔F04B；本票不修产品语义。
- **部署种子**：J3/J4 的预售工具版本、J3 的执行槽容量是部署输入（`t025.seed_tool_version`、
  `t056.seed_evaluation_capacity`），在 creator 存在前写入，不是旅程步骤；每处都在 journey notes/报告声明。
- **时钟型测试写**：J1 的 activation 过期是唯一一处测试侧业务表写（只改 `expires_at`）；J5 的
  retry/stale-token 边界**没有**用时钟写，用的是真实 `tool_timeout` 可重试完成。
- **既有红不冒充**：T093 报告记录的集成分支 6 条既有失败（f00b/f03/f02a 的迁移/夹具漂移）不在本票范围。
  另外本票单跑 `tests/f01` 发现 2 条**既有红**：T095 的角色化登录跳转与统一 401 出口改变了页面字面量，
  而 f01 的两条 marker 断言未同步；已在 baseline `60d9de0` 上逐条复现（§7.3），非本票引入，也未修改。
- **非契约路由**：`/tool-runs*`、`publication-preview`、`/runs/{id}/node-results` 不在 106 项契约内；
  J3/J4/J5 用到它们时在 `entry_http`/notes 中标注，不把它们算作契约入口。契约内入口
  （`/evaluations/{id}`、`/evaluation-reports/{id}`、`/versions/{id}`、`/background-jobs/{id}`）都已逐条真实调用。
- **未运行**：无（本机 runsc 可用，J3/J4 的沙箱分支都真实跑了；真实厂商模型是 `blocked` 而不是 `not_run`）。

## 9. 变更文件与提交

新增：

- `product/tests/f12/t100_support.py`（模块级真实栈 + 证据记录器 + 封存字节/hash 真值读取）
- `product/tests/f12/journeys/j100_account.py`
- `product/tests/f12/journeys/j100_models.py`
- `product/tests/f12/journeys/j100_publication.py`
- `product/tests/f12/journeys/j100_worker_exit.py`
- `product/tests/f12/journeys/j100_background.py`
- `product/tests/f12/test_t100_account.py`
- `product/tests/f12/test_t100_models.py`
- `product/tests/f12/test_t100_publication.py`
- `product/tests/f12/test_t100_worker_exit.py`
- `product/tests/f12/test_t100_background.py`
- `product/reports/f12/T100-journeys.json`（机器可读证据，`F12_WRITE_EVIDENCE=1` 生成）
- `product/reports/f12/T100-journeys.md`（本文件）

修改：`product/PROGRESS.md`（本票小节）。

**未改**：`product/docs/spec/**`、`product/contracts/**`、`product/tools/f12_api_gate.py`、
`product/tests/f12/conftest.py`、T093/T095/T096/T098 的任何测试/页面、产品 Go 代码、迁移、页面脚本。
