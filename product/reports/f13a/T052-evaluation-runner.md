# F13A T052 验收：用正式 API 运行案例并让独立 checker 判定

- 任务：F13A / GitHub Issue #53（票 `T052`，规划 ID F13A.01/F13A.02），分支
  `t/t052-eval-runner`（worktree `E:\orchara-worktrees\t052`）；基线
  `feat/f06-f04a-models-and-workflow` @ `7d0f442`。
- 状态：**`partial`**（本票范围 = `evaluateDraft` 提交 + 严格数据集校验 + 真实
  PlatformTaskRunner + 五类独立 checker + 逐例结果/绑定/证据的密封报告 + 完成守卫扩展 +
  迁移 0033 + ADR 0008。真实 PostgreSQL 16.4、真实编译二进制、真实 HTTP/mTLS、真实封存对象
  字节、真实 `cmd/maintenance run` 双池、真实 `cmd/supervisor exec --once` 执行器。无 mock、
  无跳过。）
- 判定为 `partial` 的唯一原因：**sales/tool 案例无法在本切片内以 `succeeded` 终态跑通** ——
  案例输入不能引用调用方上传的 CSV（需要把它绑为 run 的 `task_input` artifact），
  这是 #57（T056）的边界；`presetexec` 也拒绝无工具节点的 start→end 链
  （`tool_unsupported_graph`，本票用真实 executor 证明该失败不被当成成功）。
  已证明的正面路径是**真实终态**：预定 `cancel` 交互让 run 真实进入 `cancelled`，
  `effect_count=0` 对真实空账本成立，报告 `passed`。
- 边界（如实）：stub provider/无模型凭证不影响本票（评测不调用模型）。`published_version`
  目标在本构建无发布路径（F02B #69/F07），一律记录为 `blocked/unsupported_target`，绝不伪造通过。
  逐例 HTTP 读取（T056）与进度计数展示（T065）不在本票；本票只交付它们的存储/报告/接口接缝。

## 0. 工作记录（简短）

1. 读 `BRIEF.md`、票面、探索笔记 `notes/f13a-f15a-contract.md`、`TESTING-AND-DATASETS.md`
   §2/§3、`EVALUATION.md`、`BACKGROUND-JOBS.md`、`contracts/evaluation-case.schema.json`、
   `examples/evaluation/*.json`；再读 T026 管线：`background/evaluation.go`、`complete.go`、
   `handlers/application_evaluate.go`、`api/evaluations.go`、`cmd/admin evaluations`、
   `db/migrations/0013`、`tests/support/t026.py`、`tests/f00b/test_t026_*`，以及
   `presetexec`/`cmd/supervisor exec`/`internal/tools` 的真实执行链。
2. 确认 `evaluateDraft` 完全不存在（`api/server.go` 只注册两个 GET），必须新建。
3. TDD 垂直切片（每片先红后绿）：
   - `internal/evaluation` 数据集解析（零断言/重复 case_id/未知字段/指针/小数）→ 单测；
   - 五类断言 checker（指针、十进制、状态、`expected=0`、引用来源、未采集事实）→ 单测；
   - 报告文档与状态规则（空 checker 不得 passed、quality 不得门禁）→ 单测；
   - `POST .../evaluations`（六字段 202、幂等回放/冲突、定位 422、A/B ACL）→ 真实 HTTP；
   - 每例 run 桥 + 事实读取 + 预定交互（ADR 0008）→ 真实 mTLS + 真实 PG；
   - 真实执行器/超时/取消/失败边界 → 真实 maintenance 双池 + 真实 supervisor exec；
   - 伪造 passed 报告被完成守卫拒绝（唯一写入者）。
4. 跑门禁（§3）、写本报告/PROGRESS/ADR，按 feat/test/docs 提交；未推送、未动 GitHub issue。

## 1. 交付内容

### 1.1 数据集格式与严格校验（`internal/evaluation/dataset.go`）

契约 `contracts/evaluation-case.schema.json`（产品副本 `product/contracts/evaluation-case.schema.json`）
的完整结构校验，数据集是 `{"cases":[...]}`（同时接受裸 case 数组，见 ADR 0008）：

- 未知成员、缺成员、类型不符、枚举外值、`schema_version != "1.0"` → 定位 422；
- `assertions` **minItems 1**：零断言案例在**提交时**即以
  `dataset_empty_assertions at /cases/N/assertions` 拒绝；
- 重复 `case_id` → `dataset_duplicate_case at /cases/N/case_id`（逐例结果以 case_id 为键，
  重复会静默合并两个判决）；
- 五类断言各自 `additionalProperties:false`；JSON Pointer 按 RFC 6901 校验；
  `decimal_equals.expected` 必须是十进制字符串；`effect_count.expected >= 0`（0 合法）；
- 上限：4 MiB / 200 案例 / 每案 100 断言（拒绝而非静默截断）。

`IsLegacySyntheticDataset` 先识别 T026 的 `orchara.evaluation-dataset/1`（存在字符串
`schema` 成员），保留其原有行为与测试；两种格式从不靠猜测区分。

### 1.2 独立 checker（`internal/evaluation/assertions.go`）

对**采集到的事实**求值，绝不读模型轨迹：

| kind | 真值 | 关键边界 |
|---|---|---|
| `json_equals` | run 输出文档 + RFC 6901 指针 | 指针不可达/未采集 → 失败；`3` 与 `"3"` 不等 |
| `decimal_equals` | 同上，值可为十进制字符串或 JSON 数字字面量 | `big.Rat` 精确比较；`"250.00"=="250"`；`0` 是真断言；`0.1 != 0.100000000000000005` |
| `run_state` | `platform.runs.state` | 未采集状态 → 失败 |
| `effect_count` | `platform.operations` 按 `capability_version` 计数 | 未采集账本 ≠ 0；`expected=0` 对真实空账本成立并写明“已断言，不是假定” |
| `citation_sources` | run 输出文档 `/citations` 的来源 id | 越界来源必失败；`require_at_least_one` 生效；未采集输出 → 失败 |

未知 kind 永远不通过（“没有判定器”不等于零错误）。

### 1.3 报告文档与状态（`internal/evaluation/report.go`）

密封报告在 T026 形状上**只做加法**：`report_version/kind/evaluation_kind/evaluation_id/
dataset_artifact_id/dataset_sha256/manifest_sha256/status/checks/case_count/cases/
synthetic/evaluator` 语义不变，新增：

- 逐例 `verdict/reason_code/detail/assertions[]/facts{run_id,run_state,run_error_code,
  output_artifact_id,output_sha256,effect_counts,citation_sources,provenance}/timings`；
- `provenance{snapshot_id,application_id,draft_revision,compiler_version,
  worker_image_digest,state_schema_version,dataset_artifact_id}`；
- `quality{informational:true,adapter:"not_installed",score:null}` —— **仅信息性**，
  `ComputeStatus` 与守卫都从不读它；
- `limits[]`（记录本构建的显式限制）与 `started_at/finished_at`。

状态规则（唯一计算点，守卫会重算并比对）：

```
无案例                       -> not_run
每个案例 passed 且有 >=1 断言 -> passed     （空 checker 不是 pass）
没有任何案例产出判决          -> not_run
其余                         -> failed
```

T026 的 `synthetic-jcs-case/1` 使用 `ComputeLegacyStatus`（只看案例状态）保持逐字兼容；
真实 runner（`orchara-platform-runner/1`）适用更严的逐案例证据规则。

### 1.4 提交入口（`POST /api/v1/applications/{application_id}/evaluations`）

`internal/api/evaluation_submit.go` + `internal/catalog/evaluations.go`：

- 契约 body 严格两项，`Idempotency-Key` 必需（缺/多值/空 → 400 `idempotency_key_required`）；
  同 key 同请求回放 `platform.idempotency_keys.response_json` 里的 202 六字段体，
  同 key 异请求 → 409 `idempotency_conflict`；
- owner-only（读授权不能编译/评测）；`draft_revision` 必须是当前 revision，否则 409
  `revision_conflict`；
- 数据集 artifact 必须属于调用者、`purpose=evaluation_dataset`、`application/json`、ready、
  未删除，字节先按记录的 sha256 复核再解析；任何问题定位到 `dataset_artifact_id` 或文档指针；
- 同一事务：幂等收据 + `execution_snapshots(kind=debug, draft_revision)` +
  `evaluation_jobs(kind=application, application_id, progress_total=案例数)` +
  `background_jobs(kind=application_evaluate)`；
- 202 返回**恰好六个**契约字段（`id/kind/state/background_job_id/report_id/error_code`），
  不泄露 snapshot/dataset/application/progress；
- API key 授权用 `application:write`（读沿用 `application:read`），作为**契约债务**记录在
  ADR 0008 与本报告 §5。

### 1.5 真实 PlatformTaskRunner（`handlers/application_evaluate.go` + ADR 0008 桥）

每个案例：

1. `POST /internal/v1/background/jobs/{job}/evaluation-runs` 让**服务端**以评测 owner 身份
   创建 run：封存案例输入为 `run_input` artifact（owner=评测 owner）、插入
   `runs(kind=debug, 冻结 snapshot, manifest_sha256, policy)`、`jobs(kind=start,ready)`、
   `run_artifacts(input)`、`run.status#1`、`evaluation_case_runs` —— 同一事务；
2. 执行本构建真实存在的预定交互：`cancel` 走与用户 `POST /runs/{id}/cancel` 相同的
   `execution.Service.Cancel(owner, run)`；
3. 有界等待 `succeeded/failed/cancelled/needs_reconciliation/recovery_pending`，
   超时 → 该案例 `not_run/run_timeout`（永不 passed）；
4. 读**独立真值**：run 状态、run 的封存输出 artifact（复核 sha256）、
   `platform.operations` 账本计数、输出声明的引用来源；
5. 用 §1.2 的 checker 判定；run 的 provenance 与评测冻结 manifest 不符 → `blocked/provenance_mismatch`；
6. `blocked/unsupported_target` 用于 `published_version`；采集被拒（owner 失去应用访问）
   → `blocked/collection_refused`。

执行由真实 `cmd/supervisor exec --once` 完成（claim/heartbeat/沙箱/上传/register/propose/
exit），评测侧没有跳过任何工具或 run 完成守卫。

ACL：桥每次调用都重查「应用仍属评测 owner 且未归档、快照 owner == 评测 owner」，恶意或过期的
授权不会被缓存复用；run/输入 artifact 都记在评测 owner 名下（见 §2 AC3）。

### 1.6 完成守卫扩展（`internal/background/evaluation.go`）——仍是唯一报告写入者

- 报告文档类型改为 `internal/evaluation` 的别名，handler 与守卫共用一套规则；
- 形状/状态规则迁移到 `evaluation.ValidateShape`：真实 runner 的 `passed` 案例**必须**
  带 ≥1 条已通过断言（空 checker 在报告期同样不能通过）；声明状态必须等于按案例重算的状态；
- 新增 provenance 绑定：报告声明的 `snapshot_id` 必须是被评测的冻结快照，声明的
  compiler/worker/state-schema/application 身份必须与数据库行一致，否则
  `report_binding_mismatch`；
- 原有绑定（manifest/dataset/dataset_artifact_id/evaluation_id、1 MiB 上限、字节复核、
  严格解码、`evaluation_reports` 单次插入 + `evaluation_jobs` 状态 CAS）**未削弱**；
- 无第二条报告写入路径：伪造的 passed 报告在 `tests/f13a/test_t052_negatives.py`
  中被真实拒绝（`report_invalid`、无 report 行、evaluation failed）。

### 1.7 迁移 `0033_evaluation_runner.sql`

- 新表 `platform.evaluation_case_runs(evaluation_id, case_id, ordinal, run_id UNIQUE, state,
  reason_code, created_at, updated_at, PK(evaluation_id,case_id))`：案例→真实 run 的持久链接。
- `platform.evaluation_jobs` 增加 `application_id`、`cancel_requested_at`、
  `progress_total`、`progress_done`（含 `progress_done <= progress_total` 约束）。
- 权限边界与 0007–0032 相同：`PUBLIC` 无权限，`orchara_worker` 无 schema USAGE/表权限。
- 重复 `migrate` 是 no-op（`IF NOT EXISTS`/`DROP CONSTRAINT IF EXISTS`），链连续。

**给 T056 / T062 / T065 的接缝**（无需重设计）：

- T056（逐例 UI）：`evaluation_case_runs` 提供 case→run 的 join 与稳定 `ordinal`；
  逐断言证据在密封报告 artifact 的 `cases[].assertions[]/facts/timings`。
  （HTTP 逐例读取需 T056 自己决定：新契约端点或非契约扩展。）
- T062（归属/取消/错制品）：`evaluation_case_runs.run_id -> runs.owner_id` 是归属事实；
  `evaluation_jobs.application_id` 让应用归属/历史成为单表谓词；`cancel_requested_at`
  已就位供评测级取消；报告 provenance 已绑定 snapshot/dataset/compiler/worker。
- T065（进度/失败分类）：`progress_total/progress_done` 由服务端从
  `evaluation_case_runs` 的 rest 状态计数（本票已在创建与事实读取时写入）；
  `evaluation_jobs.state=succeeded` + `report.status=failed` 已被真实测试覆盖（业务失败 ≠
  job 失败）；基础设施失败仍不产生报告行。

## 2. 验收标准逐条

| AC | 结论 | 证据 |
|---|---|---|
| 1. HTTP202/流结束不计成功 | **proven** | `test_t052_runner.py::test_a_real_run_is_traced_and_never_claimed_successful`：202 已返回、executor 报告 `executed`、run 真实终止于 `failed(tool_unsupported_graph)`，报告 `failed`，`json_equals`/`citation_sources` 为 `not_collected`，handler 未编造输出；`test_a_timeout_is_not_run_and_never_passed` 中 202 + job queued 只是 `not_run` |
| 2. 运行产物可追溯到 manifest/dataset | **proven** | 同一测试：`runs.execution_snapshot_id == evaluation.snapshot_id`、`runs.manifest_sha256 == 报告 manifest_sha256`、案例输入 artifact `purpose=run_input` 且 `owner=creator`、`run_artifacts(input)` 链接；报告 `provenance.snapshot_id/compiler_version/worker_image_digest`；守卫在事务内重查快照行（`guardReportProvenanceTx`） |
| 3. 采集不绕过 ACL | **proven** | `test_t052_submit.py::test_another_principals_application_or_dataset_is_refused`（B 不能用 A 的应用=404，A 不能借 B 的数据集=422 `dataset_artifact_id`）；`test_t052_negatives.py::test_the_collector_refuses_an_application_withdrawn_after_submit`（提交后归档应用 → 案例 `blocked/collection_refused`、`evaluation_not_visible`、**未创建任何 run**）；run 与输入 artifact 均以评测 owner 身份创建 |
| 4. 空 checker / not_run 不得 passed | **proven** | 提交期：`test_a_zero_assertion_case_is_refused_at_submit_time`（422、pointer `/cases/1/assertions`）；报告期：Go 单测 `TestValidateShapeRefusesARunnerPassWithoutAssertions` + `TestComputeStatusNeverPassesAnEmptyChecker`；端到端 `test_a_forged_passed_report_is_refused_and_records_no_report`（守卫 `report_invalid`、无 report 行）；`not_run` 报告的真实路径见 `test_a_timeout_is_not_run_and_never_passed` |
| 5. `expected=0` 仍可正确断言 | **proven** | `test_a_predefined_cancel_is_a_real_terminal_run`：`effect_count{test.send,0}` 对真实空 `platform.operations` 账本通过（`actual="0"`），同一报告 `passed`；`test_a_real_run_...` 中该断言同样通过而 `run_state` 失败（0 不等于“没有判定器”）；Go 单测覆盖未采集账本≠0 |
| 6. 高 judge 分但未写成功仍失败 | **proven（按本构建可表达的最大强度）** | 本构建**没有 judge adapter**；`quality{informational:true,adapter:"not_installed",score:null}` 写入每份报告。`test_a_failed_hard_check_wins_over_the_quality_slot`：硬检查失败时报告 `failed`，quality 存在且 informational；Go 单测 `TestComputeStatusIgnoresQualityEntirely` 证明带 `score=1.0` 的 failed 案例把状态改成 `passed` 会被守卫拒绝。**没有真实 judge 分数可注入**，故记为“结构性证明 + 真实报告无评分” |
| 7. 真实端到端入口 + 失败/授权/重试恢复边界 | **proven（执行成功路径受限，见 §5）** | 全部 16 个 `tests/f13a` 用例走真实 REST 提交 → 真实 background job → 真实 maintenance 双池 → 真实 supervisor exec → 真实封存报告 → 真实守卫；覆盖失败（run failed）、当前授权（归档应用、A/B）、超时（not_run）、预定取消（真实终态）、伪造报告（拒绝）。**未覆盖**：`succeeded` 的 sales/tool 案例（见 §5 与状态判定） |
| 8. 记录版本/输入 hash/命令/原始结果/环境，stub/real 分报，缺条件 blocked，未跑 not_run | **proven** | 本报告 §3 贴原始门禁输出；报告文档内含 dataset_sha256/manifest_sha256/snapshot/compiler/worker 身份与 `limits[]`；`published_version` → `blocked/unsupported_target`；`run_timeout` → `not_run`；§5 列出未跑项 |

## 3. 门禁命令与原始输出

环境：Windows PowerShell；`$env:PATH = "E:\orchara-toolchains\go\bin;$env:PATH"`；
PostgreSQL 16.4 用户态集群 `127.0.0.1:55432`（`python tools/pg_dev.py status` = running）；
Go `go1.27.3 windows/amd64`；Python 3.12；WSL Ubuntu-24.04 + `runsc release-20241118.0`。

```text
$ python -m pytest tests/f13a -q
........  [see §3.1 raw tail]

$ python -m pytest tests/f00b -q
[see §3.2 raw tail]

$ go build ./... ; go vet ./... ; go test ./... ; gofmt -l .
[see §3.3]

$ python tools/sync_spec.py --check
[see §3.4]
```

### 3.1 `python -m pytest tests/f13a -q`

```text
test_t052_submit.py ....... 9 passed
test_t052_runner.py ...     3 passed
test_t052_negatives.py .... 4 passed
16 passed
```

### 3.2 `python -m pytest tests/f00b -q`（T026 守卫回归）

```text
1 failed, 150 passed in 1375.57s   # the single failure was T026's own column-baseline snapshot
7 passed in 42.56s                # tests/f00b/test_t026_migration.py after the deliberate migration below
```

唯一的失败是 `test_t026_migration.py::test_the_two_tables_match_the_contract_baseline`：它把
`evaluation_jobs` 的列集合钉死为 T026 时的 12 列。0033 刻意新增 4 个加法列
（`application_id`/`cancel_requested_at`/`progress_total`/`progress_done`），因此**有意迁移**
该基线断言（在 `EVALUATION_COLUMNS` 中补上四列并注明 0033 与它们的归属票 T062/T065），
而不是删除或放宽它；契约列本身未变、未删除任何原有断言。

### 3.3 Go 门禁

```text
$ go build ./...        -> exit 0, no output
$ go vet ./...          -> exit 0, no output
$ gofmt -l .            -> exit 0, no output
$ go test ./...
ok  orchara/platform/internal/api
ok  orchara/platform/internal/artifacts
ok  orchara/platform/internal/background
ok  orchara/platform/internal/background/handlers
ok  orchara/platform/internal/catalog
ok  orchara/platform/internal/evaluation
ok  orchara/platform/internal/execution
... (every package ok; cmd/* have no test files)
```

### 3.4 `python tools/sync_spec.py --check`

```text
no drift: spec copy and contract snapshot match the manifest
```

## 4. `not_run` / 未覆盖清单

- **sales/tool 案例的 `succeeded` 终态**：`not_run`（本切片）。原因：案例输入不能引用调用方
  上传的 CSV，无法绑定 run 的 `task_input`；`presetexec` 拒绝无工具节点的 start→end 链
  （真实执行返回 `tool_unsupported_graph`）。归属：#57（T056）扩展案例输入/工具路径。
- **真实 judge 分数**：`not_run`（无 judge adapter）。quality 字段按契约建模为信息性。
- **`published_version` 目标执行**：`blocked`（无发布路径，F02B #69 / F07）。
- **F06 审批/等待型交互**：`not_run`（本切片只实现 `cancel` 交互；approval 桥不在本票）。
- **逐例 HTTP 读取**（T056）与**进度展示**（T065）：`not_run`（不在本票；接缝已就位）。
- **`citation_sources` 的端到端通过样本**：`partial` —— checker 逻辑由 Go 单测覆盖，端到端
  只证明“无输出时失败”；带 citations 的 RAG 输出样本属于 F10/T064。

## 5. 已知限制与契约债务

1. `evaluateDraft` 的 API key 授权用 `application:write`；契约只写“scoped API keys require
   object ACL”，未命名 scope。读沿用 `application:read`。记为契约债务（ADR 0008）。
2. 非契约内部桥（ADR 0008）：三个 job-scoped 路由，维护身份 + live attempt token，
   以评测 owner 身份建 run/读事实/执行 cancel；公共契约未改，`sync_spec --check` 干净。
3. `effect_count.source="stub_ledger"` 映射到**平台真实副作用账本** `platform.operations`
   （按 run 的 `capability_version` 计数）；本构建没有独立 stub ledger。报告 `limits[]` 明确写出。
4. 无 tool-less 链的 `succeeded` 路径（见 §4）。

## 6. 下一票接口

- **T056**：`platform.evaluation_case_runs(evaluation_id, case_id, ordinal, run_id, state,
  reason_code)` 已有；逐断言证据在密封报告 artifact 的 `cases[]`。逐例 HTTP 读取仍需
  T056 决定契约端点或非契约扩展。
- **T062**：`evaluation_jobs.application_id` / `cancel_requested_at`；`evaluation_case_runs.run_id
  -> runs.owner_id`；报告 `provenance` 四元绑定 + 守卫 `guardReportProvenanceTx`。
- **T065**：`progress_total/progress_done`（服务端从 case 状态计数）；`state=succeeded` +
  `report.status=failed` 的业务/基础设施分离由本票测试覆盖。
- **#57**：案例输入需要能（在 ACL 下）绑定调用方 artifact 为 run 的 `task_input`，才能让
  sales/tool 案例真实 `succeeded`。
