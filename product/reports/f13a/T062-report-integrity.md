# F13A T062 验收：伪造、借用、取消或未完成的报告不能通过验收

- 任务：F13A / GitHub Issue #63（票 `T062`，规划 ID F13A.04），分支
  `t/t062-report-integrity`（worktree `E:\orchara-worktrees\t062`）；基线
  `feat/f06-f04a-models-and-workflow` @ `7d0f442`；已合并
  `integration/f13a-f15a-f09-f10`（合并提交
  `e92d8bac69cd39924d8da212c6e27df7e5c264e4`，合并前 integration tip `ddaeb46`）。
- 状态：**`passed`**（范围 = 证明 T052 完成守卫在“伪造 / 借用 / 取消 / 未完成”四类
  报告上失败关闭 + 修复两个真实漏洞（G1 runner、G2 守卫）+ 管理员归属读数 + 对抗套件；
  真实 PostgreSQL 16.4、真实编译二进制、真实 HTTP/mTLS、真实封存对象字节、真实
  `cmd/maintenance run` 双池、真实 `cmd/supervisor exec --once` 执行器。无 mock、
  无跳过。）
- 边界（如实）：逐例 **用户** HTTP 读取仍属 #57（T056）；本分支没有该端点，T062 交付的是
  **admin-only** 归属读数（非 admin 在查询前 403）。评测级取消转移（
  `evaluation_jobs.cancel_requested_at` 列）不在本票范围；本票交付并证明的是 case 级真实
  取消（runner 通过真实 `POST /runs/{id}/cancel` 执行）。sales/tool 案例的真实 `succeeded`
  终态仍受 #57 的 `task_input` 绑定边界限制；本票用真实 `failed` 终态（期望失败的断言）证明
  “合法 passed 仍会被守卫接受”，不是 refuse-all。无迁移、未改 `docs/spec/**` 与
  `contracts/**`、未 push、未关闭 issue。

## 0. 工作记录（简短）

1. 按顺序读 `BRIEF.md`、`briefs/T062.md`、票面、T052 报告、完成守卫
   （`background/evaluation.go` 的 `guardEvaluationSuccess` / `validateEvaluationReportShape`
   / `guardReportProvenanceTx`）、`complete.go` 的 `guardApplicationEvaluateCompletion`
   / `verifyJobResultArtifact`、`internal/evaluation/`（dataset/assertions/report/collect）、
   `execution/evaluation_runs.go`、`api/{evaluation_submit,evaluation_runs,evaluations}.go`、
   迁移 `0035`、`tests/f13a/` + `tests/support/t052.py`，以及
   `tests/f00b/test_t026_{binding,authz,failures}` 的对抗写法与 `tests/f03/test_t036_cancel.py`。
2. 先写红测试再实现：取消案例端到端（红：报告 `passed`）→ 修 collector；
   伪造 passed 报告借真实 `queued`/`cancelled` run（红：job `succeeded`、守卫接受）→ 修守卫；
   管理员归属读数（红：404 page not found）→ 实现 + ADR。
3. 补齐对抗覆盖（A/B、四类错误绑定、借用 artifact/文档、作废 token、未完成 case），
   跑门禁、写报告/PROGRESS/ADR、按 feat/test/docs 提交；合并 integration（本票 ADR 因
   integration 已占用 0013 而重编号为 **0014**），在合并结果上重跑目标套件两次。

## 1. 发现并修复的漏洞

这两个漏洞都是**先由测试从外部证明**、再修实现；修复点的回归测试就是当初红的用例。

### 1.1 G1 —— 真实 runner 会把已取消的 case 判成 `passed`

**红证据**：`test_t062_runner.py::test_a_cancelled_case_never_passes`。一个真实 run 通过
真实 `POST /api/v1/runs/{id}/cancel` 进入 `cancelled`；dataset 声明
`run_state=cancelled` 与 `effect_count{test.send}=0`（对真实空账本成立）。T052 的
`collectCase` 只按断言判决，于是报告 `status="passed"`、案例 `status="passed"`：

```text
AssertionError: {'assertions': [{'actual': 'cancelled', 'expected': 'cancelled', 'kind': 'run_state',
'passed': True}, {'actual': '0', ...}], 'status': 'passed', ...}
assert 'passed' == 'failed'
FAILED tests/f13a/test_t062_runner.py::test_a_cancelled_case_never_passes
```

**修复**（`internal/evaluation/collect.go`）：

- 唯一完成态谓词 `CompletedRunState(state) = state ∈ {succeeded, failed}`，runner 与完成守卫
  共用；
- `collectCase` 在断言求值后先判完成态：`cancelled` → `failed` + `reason_code="run_cancelled"`；
  `recovery_pending`/`needs_reconciliation` → `failed` + `reason_code="run_unfinished"`；
  断言结果仍完整保留为证据（不伪装、不删除）；
- 另加 `TerminalRunState(succeeded, failed, cancelled)` 供管理员读数区分
  “终态”与“已完成”（cancelled 是终态但永远不能支撑 passed）。

T052 的 `test_a_predefined_cancel_is_a_real_terminal_run` 原先断言该场景为 `passed`，本票
**按 #63 有意收紧**为 `failed/run_cancelled`（不是放宽或删除断言：证据断言全部保留）。

### 1.2 G2 —— 完成守卫不读 runner 的 run 证据，自洽的伪造 passed 会被接受

**红证据**：`test_t062_guard.py::test_a_forged_passed_report_over_unfinished_or_cancelled_runs_is_refused`。
通过真实 job-scoped 桥创建两个本评测的 case run（一个留在 `queued`，一个由 owner 真实取消为
`cancelled`），再上传一份内部完全自洽的 `passed` 报告（provenance、manifest/dataset 绑定、
每条断言 `passed:true`、`facts.run_id` 都是真实 run id）。修复前：

```text
AssertionError: {'error_code': None, 'id': 'd576415b-...', 'kind': 'application_evaluate',
'state': 'succeeded'}
assert 'succeeded' == 'failed'
FAILED tests/f13a/test_t062_guard.py::test_a_forged_passed_report_over_unfinished_or_cancelled_runs_is_refused
```

**修复**（`internal/background/evaluation.go` 新增 `guardReportCaseRunsTx`，在 provenance
校验之后、写 report 行之前执行）。对 `kind=application`（真实 runner 的唯一 kind）：

1. 报告必须声明 `evaluator="orchara-platform-runner/1"`；声明旧 synthetic 占位评估器即
   `report_invalid`——堵住“降级声明占位评估器以跳过逐例证据规则”的旁路；
2. 每个带 `facts.run_id` 的案例必须命中 `platform.evaluation_case_runs(evaluation_id, case_id)`
   关联到的真实 run，且 `run.id == facts.run_id`、`run.owner_id == 评测 owner`、
   `run.execution_snapshot_id == 评测 snapshot`、`run.manifest_sha256 == 绑定 manifest`；
3. `passed` 案例额外要求：run 已完成（`CompletedRunState`）且报告记录的 `facts.run_state`
   与数据库行一致；
4. 任何不一致 → `report_binding_mismatch`：job 终态 failed、评测 failed、**不写**
   `evaluation_reports` 行；DB 错误则中止完成事务（fail closed）。

未削弱 T052 的原有绑定（manifest/dataset/dataset_artifact_id/evaluation_id、1 MiB 上限、
字节复核、严格解码、provenance 行内复读、单次插入 + 状态 CAS）；T026 的 model 合成评估路径
保持不变（`kind=model` 没有 case run，守卫跳过本规则）。

## 2. 对抗套件（`tests/f13a/`）

| 文件 / 用例 | 证明 |
|---|---|
| `test_t062_runner.py::test_a_cancelled_case_never_passes` | 真实取消的 run → 案例 `failed/run_cancelled`、报告 `failed`；根因 G1 |
| `test_t062_runner.py::test_an_incomplete_case_is_not_run_and_never_passed` | 无执行器、5s 有界案例等待（BRIEF §6.1）→ 案例 `not_run/run_timeout`、报告 `not_run`、run 仍 `queued` |
| `test_t062_runner.py::test_a_passed_report_is_accepted_over_a_real_completed_run` | 守卫非 refuse-all：真实执行并以 `tool_unsupported_graph` 完成的 run + 期望失败的断言 → 守卫接受 `passed`，报告行写入 |
| `test_t062_guard.py::test_a_forged_passed_report_over_unfinished_or_cancelled_runs_is_refused` | 真实 `queued` / `cancelled` run 上的伪造 passed → `report_binding_mismatch`、job failed、无 report 行（G2） |
| `test_t062_guard.py::test_a_report_with_a_wrong_manifest_dataset_or_evaluation_binding_is_refused` | `manifest_sha256` / `dataset_sha256` / `dataset_artifact_id` / `evaluation_id` 逐一破坏 → `report_binding_mismatch`、job failed、无 report 行 |
| `test_t062_guard.py::test_a_borrowed_report_artifact_or_document_cannot_become_another_result` | B 直接命名 A 的 ready report artifact → 422（job-scoped link）；把 A 的报告字节上传到 B 的 job → `report_binding_mismatch`；B 无 report 行 |
| `test_t062_authz.py::test_accounts_are_isolated_on_reads_and_submission` | B 读 A 的 evaluation/report → 404 且与“不存在”同码同文；B 对 A 的应用提交评测 → 404；A 借用 B 的 dataset → 422 `dataset_artifact_id` |
| `test_t062_authz.py::test_a_voided_attempt_token_cannot_submit_a_final_success` | 可重试失败 → 重试产生新 attempt；旧 token 提交伪造 passed → 409 `stale_attempt`，job/评测/report 全部不变；当前 token 再提交仍被守卫拒绝 |
| `test_t062_authz.py::test_the_administrator_attribution_read_exposes_account_and_cancellation_evidence` | 管理员归属读数：owner/login、评测/job id、逐例 `run_state`（含 `cancelled`）与 `terminal`/`completed`、snapshot/manifest/dataset hash、compiler/worker、report 行；非 admin 403、未认证 401、未知/畸形 id 404；响应无 object key/report body/案例输入 |
| `internal/evaluation/collect_test.go`（4 个 Go 单测） | collector 判决规则：cancelled 永不 passed（断言证据保留）、`recovery_pending` 永不 passed、完成的 failed run 可支撑期望失败的 passed、超时 `not_run` |

所有用例走真实入口：公开提交 → 真实 background job → 真实 mTLS claim/result → 真实
job-scoped 报告上传/complete/finalize → 真实守卫；需要执行时由真实
`cmd/supervisor exec --once` 执行真实 run。守卫没有任何 mock/stub。

## 3. 管理员归属读数（非契约面，ADR 0014）

`GET /api/v1/admin/evaluations/{evaluation_id}/attribution`（`internal/api/evaluation_attribution.go`
+ `internal/background/evaluation_attribution.go`）：

- **授权**：`admin` 角色在**任何查询之前**校验（非 admin 对任意 id 都是 403，不能探测存在性）；
  未知/畸形 id → 404 `resource_not_found`；只读，不写任何行。
- **内容**：`owner{principal_id,login}`、evaluation/job id、`application_id`、`snapshot_id`、
  `draft_revision`、`manifest_sha256`、`dataset_artifact_id`、`dataset_sha256`、
  `compiler_version`、`worker_image_digest`、`state_schema_version`、`cancel_requested_at`、
  report 行（id/status/两个 hash/result artifact id）、逐例
  `{case_id,ordinal,case_state,reason_code,run_id,run_state,run_owner_id,run_snapshot_id,
  run_error_code,terminal,completed,updated_at}`、`created_at/finished_at`。
- **不暴露**：sealed object key（`object_key`/`sealed/...`）、report body/checks、案例输入或
  dataset 内容、下载 URL/内容、凭据，或任何其他人的内容。测试对响应文本做否定断言。
- `terminal` = run 不能再转移（succeeded/failed/cancelled）；`completed` = 已完成执行
  （succeeded/failed），只有它可以支撑 passed —— 管理员能直接看到 T062 的验收规则。

## 4. 验收标准逐条

| AC | 结论 | 证据 |
|---|---|---|
| 1. 错应用/错 hash/错数据报告不能冒用 | **proven** | `test_t062_guard.py::test_a_report_with_a_wrong_manifest_dataset_or_evaluation_binding_is_refused`（4 变体逐一 `report_binding_mismatch`、job failed、无 report 行）；`test_a_borrowed_report_artifact_or_document_cannot_become_another_result`（借用 artifact 422、借用文档 `report_binding_mismatch`）；`test_accounts_are_isolated_on_reads_and_submission`（B 不能读/提交 A 的东西） |
| 2. 未完成 case 不计通过 | **proven** | runner：`test_an_incomplete_case_is_not_run_and_never_passed`（`not_run/run_timeout`，run 仍 `queued`）+ Go 单测；守卫：`test_a_forged_passed_report_over_unfinished_or_cancelled_runs_is_refused`（真实 queued run 上的伪造 passed 被拒） |
| 3. 作废 token 不能提交伪造 passed | **proven** | `test_t062_authz.py::test_a_voided_attempt_token_cannot_submit_a_final_success`：重试产生新 attempt 后旧 token → 409 `stale_attempt`；job 仍归新 token、评测仍 running、无 report 行 |
| 4. 真实端到端入口；失败、当前授权与重试/恢复边界有断言，不以单层 mock 成功代替 | **proven** | §2 全部用例走真实 REST/mTLS/PG/对象字节/maintenance/supervisor；失败边界（run `failed`、报告 `failed`）、授权边界（A/B 404/403/422、owner-only cancel）、重试边界（可重试失败→新 attempt→旧 token 失效）、取消/未完成边界、守卫接受/拒绝双侧均有断言 |
| 5. 记录版本、输入/hash、实际命令、原始结果与环境；stub/real 分报，缺条件 blocked、未运行 not_run | **proven** | §5 贴原始输出；报告文档本身含 dataset/manifest hash 与 provenance；§6 列 not_run；本票不依赖 stub（评测不调用模型，T052 已记录） |
| 附加（brief）：取消案例不能 passed | **proven** | runner 修复 + `test_a_cancelled_case_never_passes`；守卫侧伪造 passed 被拒 |
| 附加（brief）：管理员可核对账号/取消结果/manifest/dataset/compiler/worker | **proven** | §3 + `test_the_administrator_attribution_read_exposes_account_and_cancellation_evidence` |

## 5. 门禁命令与原始输出

环境：Windows PowerShell；`$env:PATH = "E:\orchara-toolchains\go\bin;$env:PATH"`；
PostgreSQL 16.4 用户态集群 `127.0.0.1:55432`（`python tools/pg_dev.py status` = running）；
Go `go1.27.3 windows/amd64`；Python 3.12.2；WSL Ubuntu-24.04 + `runsc
release-20241118.0`（本票只在正面对照里运行真实 `cmd/supervisor exec`，该 run 在沙箱启动前
以 `tool_unsupported_graph` 确定性拒绝）。

### 5.1 Go 门禁（合并结果上）

```text
$ go build ./...        -> exit 0, no output
$ go vet ./...          -> exit 0, no output
$ gofmt -l .            -> no output
$ go test ./...         -> 每个包 ok（internal/{api,background,evaluation,execution,...} 全 ok；cmd/* 无测试）
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest   (exit 0)
```

### 5.2 `python -m pytest tests/f13a -q`（合并 integration 后，两次连跑）

```text
# run 1
.........................                                                [100%]
slowest durations (setup = fresh DB + migrate + real API):
30.51s call  test_t052_runner.py::test_a_real_run_is_traced_and_never_claimed_successful
29.50s setup test_t062_authz.py::test_accounts_are_isolated_on_reads_and_submission
29.10s setup test_t052_runner.py::test_a_real_run_is_traced_and_never_claimed_successful
28.89s setup test_t062_runner.py::test_a_cancelled_case_never_passes
25.88s setup test_t052_submit.py::test_submit_returns_the_six_field_evaluation_job
25.13s setup test_t052_negatives.py::test_a_failed_hard_check_wins_over_the_quality_slot
20.79s setup test_t062_guard.py::test_a_forged_passed_report_over_unfinished_or_cancelled_runs_is_refused
11.70s call  test_t062_guard.py::test_a_report_with_a_wrong_manifest_dataset_or_evaluation_binding_is_refused
11.08s call  test_t062_authz.py::test_the_administrator_attribution_read_exposes_account_and_cancellation_evidence
9.42s call  test_t062_guard.py::test_a_borrowed_report_artifact_or_document_cannot_become_another_result
9.07s call  test_t062_authz.py::test_accounts_are_isolated_on_reads_and_submission
8.36s call  test_t062_runner.py::test_an_incomplete_case_is_not_run_and_never_passed
25 passed in 503.19s (0:08:23)

# run 2 (back to back, no code change)
.........................                                                [100%]
25 passed in 382.58s (0:06:22)
```

合并前（本票分支，同一套用例）：`25 passed in 459.91s`。两次连跑无 flaky；本票没有
依赖 <5s 窗口的断言，唯一的 5s 案例超时是**有界等待的负向控制**（无执行器时 run 保持
`queued`，不是与真实完成竞速）。

### 5.3 邻接回归

```text
$ python -m pytest tests/f00b/test_t026_binding.py tests/f00b/test_t026_failures.py \
    tests/f00b/test_t026_pool.py tests/f00b/test_t026_authz.py -q
20 passed in 258.54s
```

（T026 是完成守卫/读取的邻接套件；`kind=application` 的 synthetic 报告路径在 T026 里没有
成功用例，守卫收紧后该子集仍全绿。合并后 integration 只增加了 F09/F10 的无关代码。）

### 5.4 `python -m pytest tests/f10 -q`（BRIEF 要求跑一次，合并结果上）

```text
.........................................                                [100%]
41 passed in 468.92s (0:07:48)
```

### 5.5 `python -m pytest tests/tooling -q`

```text
.....................                                                    [100%]
21 passed in 7.93s
```

## 6. `not_run` / 未覆盖清单

- **逐例用户 HTTP 读取（T056 / #57）**：`not_run`。本分支不存在该端点；本票交付的是
  admin-only 归属读数（非 admin 403）。T056 的端点与 suite 并入 integration 后应保持 404
  语义，不做重复实现。
- **评测级取消转移**：`not_run`（不在 #63 票面/brief 的交付项）。`evaluation_jobs.cancel_requested_at`
  列已在 0035 就位并出现在归属读数里；本票证明的是 runner 的 case 级真实取消。
- **sales/tool 案例的真实 `succeeded` 终态**：`not_run`，归属 #57（案例输入不能绑定调用方
  `task_input`）。本票用真实 `failed` 终态 + 期望失败的断言证明“合法 passed 被接受”。
- **真实 judge 分数 / 模型凭据**：不适用（评测不调用模型；T052 已记录 quality 信息性）。
- **浏览器 DOM / 新页面**：本票无页面交付；管理员面是 JSON 读数。
- **全量 `pytest tests`**：`not_run`（BRIEF §3 禁止；约 67 分钟）。已跑目标套件 + 两个邻接
  套件 + tooling。
- **负载/压测生成器**：`not_run`（BRIEF 禁止无界负载）。

## 7. 已知限制与契约债务

1. **非契约面**：admin 归属读数是文档化的非契约扩展（ADR 0014），公开契约未改，
   `sync_spec --check` 干净。它出现在 `dataset_artifact_id` 等内部 id 上——这是管理员审计
   视图的刻意选择，非 admin 不可达。
2. **`kind=application` 不再接受 synthetic 评估器报告**：旧 T026 占位评估器只对
   `kind=model` 保留；application 评测由真实 runner 执行，声明占位评估器的文档一律
   `report_invalid`。T026 的 model suite 未受影响（20 passed，见 §5.3）。
3. **残余风险（记录，不在本票实现）**：守卫校验“passed 案例由本评测的已完成 run 支撑”，
   但不解析 dataset 去校验报告 case id 集合 ⊆ dataset case id 集合。当前 runner 是可信代码
   且 run 必须由真实执行器执行到完成态；若以后 handler 的信任边界收紧，可在守卫里增加
   dataset 解析交叉校验。
4. **`terminal` vs `completed`**：cancelled 是终态但不是完成执行；此前 T052 的“终态即可
   passed”假设由本票显式区分并写明。
5. **ADR 重编号**：本票原本新增 ADR 0013；合并 integration 时 T059 已占用
   `0013-f10-tabular-ingest.md`，因此本票文件与代码注释重编号为 **0014**（合并提交
   `e92d8ba` 之后的 docs 提交）。

## 8. 下一票接口

- **T056**：可直接用 `platform.evaluation_case_runs`（case_id/ordinal/run_id/state）+ 密封
  报告 artifact 的 `cases[].assertions/facts` 渲染逐例 UI；若做用户面 per-case 读，注意
  本票的 admin 面是不同授权（admin-only、403 在查询前）。
- **T065**：`terminal`/`completed` 谓词（`evaluation.TerminalRunState` /
  `CompletedRunState`）与 `progress_done` 的 rest 状态口径一致；`cancel_requested_at` 仍在
  `evaluation_jobs`，评测级取消转移仍待实现。
- **#57**：把调用方 artifact 绑定为 run 的 `task_input` 后，sales/tool 案例即可真实
  `succeeded`；守卫对 passed 的 run 证据校验无需改动（本票已用真实 failed 终态证明接受
  路径）。
- **F07**：sealed 孤儿清理；与本票无关。
