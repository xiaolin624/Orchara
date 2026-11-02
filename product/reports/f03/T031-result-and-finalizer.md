# F03 T031 验收：运行完成后等待真实退出证据再展示终态

- 任务：F03 / GitHub Issue #32（票 `T031`，规划 ID F03.03/F03.05），分支 `feat/f05-f03-run-and-tools`
- 状态：**`passed`**（本票交付范围 = result 提议 + supervisor 身份化 exit 证据 + 统一幂等 finalizer +
  运行页 finalizing/终态展示）。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 mTLS 内部客户端
  （worker/supervisor/maintenance 三种证书）+ 真实 HTTP + 真实文件系统对象字节；无 mock、无跳过。
  边界（如实）：事件/SSE 属 T032，run-bound artifact 注册属 T033，waiting/resume 属 T035，
  cancel/unknown guard 属 T036，恢复证据属 T038；浏览器 DOM 驱动页面仍 `not_run`（与
  F01/T023/T027/T029 相同边界）。
- 主要文件：
  - `internal/execution/{execution.go,store.go,service.go,service_test.go}`
    （`WorkerResult`/`OutputArtifactError`/`ExitEvidence`、`SubmitResult`、身份化 `ConfirmExit`、
    `finalizeRunTx`、children/unknown guard、proposal/finalizer SQL）
  - `internal/api/{execution.go,internal.go}`（`submitWorkerResult` 端点、角色规则、错误映射）
  - `apps/web/src/{runs.html,assets/runs.js,debug.html,assets/debug.js}`（生命周期/轮询）
  - `tests/support/{t029.py,t031.py}`、`tests/f03/test_t031_{result,finalizer,identity,pages}.py`
    （4 文件 17 用例）；T029 exit/invariants 用例按新必填证据收紧
  - 无迁移（见 §2.1）
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`，
    `GOCACHE=E:\orchara-toolchains\gocache`、`GOMODCACHE=E:\orchara-toolchains\gomodcache`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`tools/pg_dev.py`，真实 `cmd/admin migrate`）
  - Python 3.12.2（`python -m pytest`）、Node 24.13.0（页面 JS 语法与行为真实执行）
  - profile `dev-stub`；对象目录在测试 `tmp_path`；部署输入沿用 T025 占位值
    `ORCHARA_WORKER_IMAGE_DIGEST=sha256:aaaa…(64)`、runtime lock =
    `probes/agent_min/requirements.lock.txt`（真实 SHA-256）
  - 冻结的 golden 执行 hash（与 T025/T027/T029 相同）：
    `9cc418d167377e667c77284ce7b35819c7374c0459ec90e41f4131719aef5686`
- 真实/桩分报：**全部 real**（PG/二进制/mTLS/HTTP/对象字节/Node 执行页面逻辑）。测试用 SQL 播种
  output artifact 行、`run_artifacts` 链接与子 Run 行，因为 T033（run-bound artifact 注册）、
  T035（child Run 创建）尚未实现；artifact 的字节真实写在对象目录、判定在 Go 内对真实行执行。

## 0. 工作记录（简短）

1. 读 NOTES/brief/T031 票面、EXECUTION §1/§3/§4/§8、STATE-MACHINES §2/§3/§6、
   IMPLEMENTATION-DETAILS §6/§7、API §3、BACKGROUND-JOBS §1、ADR 0004 §5/§6/§38–44、
   T027/T029 报告与 `contracts/openapi.json`/`database.sql`，确认 0017 为最新迁移、树干净
   （基线 `tests/f03` 51 passed）。
2. TDD 切片：先写 proposal-only 用例（红：404）→ 实现 `submitWorkerResult`（绿）；再写身份化 exit
   证据用例并收紧 T029 exit 用例 → 实现必填身份与 recovery_pending；再写 finalizer 两序/失败/
   waiting/needs_reconciliation/artifact/角色/跨 run/children 用例 → 实现 `finalizeRunTx`。
3. 端点：`POST /internal/v1/runs/{run_id}/result`（worker|supervisor；maintenance 403）；
   `POST /internal/v1/attempts/{attempt_id}/exited` 保持 supervisor only。
4. finalizer：结果+退出两证据齐全才提交；终态一事务映射；配额 `released_at IS NULL` 恰好一次；
   children guard 真实查询 `platform.runs(parent_run_id)`；unknown guard 结构化为
   `hasUnknownOperationsTx`（T036 替换函数体）。
5. Web：runs 详情新增“执行阶段”行，finalizing 明确等待退出证据、终态前不显示成功；未终态轮询
   `GET /runs/{id}` 到终态即停；debug 页同语义。
6. 跑门禁（§3），写本报告/PROGRESS/NOTES，按 feat/test/docs 三次提交；未推送、未关 issue、
   未改 `docs/spec/` 与 `contracts/`、未改迁移 0001–0017、未新增迁移。

## 1. 交付内容

### 1.1 `SubmitResult`：结果只是提议

`POST /internal/v1/runs/{run_id}/result`（契约 operationId `submitWorkerResult`）：

- 角色：`worker` 或 `supervisor`；`maintenance` 403 `forbidden`。
- body 按契约 `WorkerResult` 严格解码（unknown key/尾随数据 → 400 `invalid_json`；
  `additionalProperties:false`），条件必填：`succeeded`→`output_artifact_id`、
  `failed`→`error_code`、`waiting_*`→`checkpoint_id`；`attempt_token`/`event_id` 必须 UUID；
  `suggested_state` 必须是契约五值。所有形状错误 422 `validation_failed` + `details.field`。
- 事务顺序：锁 run → 终态 409 `run_terminal`；非执行态（waiting/cancelling/
  needs_reconciliation）409 `run_state_conflict`；running/recovery_pending 继续 → 锁该 run 的
  attempt（跨 run token、被更新 attempt 取代、started=false requeue 后 → 409 `stale_attempt`）→
  已存 proposal 同 JSON 值重放 200、异值 409 `event_conflict` → `succeeded` 校验 output artifact
  （存在、owner=run owner、ready 且未 tombstone、在 `run_artifacts` 关联；422
  `validation_failed` + `details.code` = artifact_not_found/not_owned/not_ready/not_linked）→
  写 `run_attempts.reported_result`（整份 proposal，含 event_id）/`reported_at`/`checkpoint_id` →
  `runs.phase='finalizing'`（**仅 phase；state/revision 不变**）。
- 200 返回契约 Run（通过唯一的 `catalog.Run` 投影，owner-only 读模型；非契约
  `X-Manifest-SHA256` 头保留）。proposal-only 时是 running/finalizing；exit 已先到时同一调用完成
  finalizer，返回已提交的终态（见 §2.5）。

### 1.2 `ConfirmExit`：身份化退出证据

- evidence 必须携带 `started`（bool）、`protocol_version`、`manifest_sha256`（64 hex）、
  `image_digest`；缺失/类型/形状错误 422 `validation_failed` + `details.field`
  （`evidence.started` 等）。evidence 仍是契约的开放对象，可带 reason/exit_code 等附加键。
- 身份核对在**任何写入之前**：`protocol_version` 必须等于 claim 的 `1.0`；
  `manifest_sha256` 必须等于 run 的冻结 hash（大小写不敏感 hex）；`image_digest` 必须等于
  snapshot 的 `worker_image_digest`。任一不符 → 409 `execution_identity_mismatch`，attempt 仍
  live、job 仍 leased、run 不变、配额仍占用。
- 相同 evidence 重复提交 200 no-op；不同 evidence 409 `attempt_evidence_conflict`。
- `started=false` 保持 T029 行为：exited+revoked、释放槽位、job 回 ready、run 回 queued/idle、
  revision+1、`started_at` 不变。
- `started=true` 且已有 proposal → 同事务运行 finalizer；无 proposal → run
  `recovery_pending`/phase idle/revision+1、job held、slot 已释放；不 requeue、不猜结果。

### 1.3 `finalizeRunTx`：统一幂等 finalizer

两个入口（result 与 exit）在各自事务内调用同一个 finalizer；要求 result 与 exit 证据齐全：

- **guard**：直接子 Run（`platform.runs.parent_run_id`）存在非 succeeded/failed/cancelled 的
  活子任务时阻塞（job held、run 保持 running/finalizing、证据保留，T035/T036 接续）；unknown
  operation guard 已结构化为 `hasUnknownOperationsTx`，当前恒 false（表属 T036，替换函数体而不是
  新增第二条 finalizer 路径）。
- `waiting_approval`/`waiting_children`：只保存 proposal、job held、run 保持 running/
  phase=finalizing（T035 负责 waiting 与唯一 resume）。
- `succeeded`：同一事务写 run state=succeeded、revision+1、phase idle、`finished_at=now()`、
  `output_artifact_id`、`error_code=NULL`、job completed、token revoked、allocation 释放。
- `failed`：同上但 state=failed、`error_code=proposal.error_code`、output 为 NULL。
- `needs_reconciliation`：run state=needs_reconciliation（非终态，`finished_at` 保持 NULL）、
  phase idle、job completed、token revoked、allocation 释放；若 proposal 后 artifact 不再
  可接受则同样转 needs_reconciliation（error_code=`output_artifact_invalid`），不假装成功。
- 配额释放 `releaseAllocationOnceTx`：`released_at IS NULL` + `used-1` 同事务；重复 finalize 是
  no-op 而不是二次扣减。
- run 更新带 `WHERE state=? AND revision=?` CAS；终态/已对账的重复调用不再转移。

### 1.4 Web

- `apps/web/src/runs.html` + `assets/runs.js`：详情新增“执行阶段”行
  `executing → finalizing → terminal`；`describeRunState` 对
  `state=running, phase=finalizing` 明确输出“结果提议已保存，正在 finalizing：仍在等待
  supervisor 的退出证据（进程/沙箱/数据库排空）；终态提交前这不是成功”。`recovery_pending`、
  `needs_reconciliation`、`queued`、running 无租约、终态各有独立文案。
- 未终态时轮询 `GET /api/v1/runs/{id}`（2s/次、上限 60 次），终态即停；成功只在服务端提交终态后
  展示。所有值 `textContent`，无凭据/URL 存储。
- `debug.html` + `assets/debug.js`：同一生命周期说明与 `describeRunStage`，轮询 note 复用该
  诚实文案。

### 1.5 测试

`tests/support/t031.py`（真实 sealed output artifact/链接/子 Run 种子的 helper）+
`tests/f03/test_t031_*.py` **4 文件 17 用例**：

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `test_t031_result.py` | 2 | proposal 200 只写 phase=finalizing（DB 非终态、revision 不变、job 仍 leased、配额仍占用、attempt 仍 live）；契约 body 精确形状/条件必填/404/409 定位 |
| `test_t031_finalizer.py` | 10 | result→exit 与 exit→result 各恰好一次终态（revision 3 vs 4、一次 job 完成、一次配额释放、无重复行、output_artifact_id 暴露）；重复 result/exit 幂等、异内容 409；failed error_code 无 output；needs_reconciliation 非终态；waiting 保持 running/finalizing；artifact not_found/not_linked/not_owned/not_ready 拒绝；角色；两 run token/checkpoint 不串；live child 阻塞 |
| `test_t031_identity.py` | 1 | 错 protocol/manifest/image → 409 `execution_identity_mismatch` 且零写入；正确身份后终态 |
| `test_t031_pages.py` | 4 | `/runs`/`/debug` 生命周期与 finalizing 文案/轮询标记；Node 语法；runs.js/debug.js 行为（阶段顺序、finalizing 措辞、终态前不宣称成功、只有终态停止轮询） |

T029 收紧（预期加严，不是削弱）：`test_t029_exit.py` 全部 exit 用例改用带身份的 evidence；
缺失 `started` 由“200 hold”改为 422；`started=true` 无结果由“running/executing hold”改为
`recovery_pending`（T031 语义）；`test_t029_invariants.py` 的退出改用身份 evidence。
`internal/execution` Go 单测扩到 7 个（退出身份四字段必填/定位、WorkerResult 条件必填）。

## 2. 契约缺口与显式偏离（如实）

1. **无迁移**：契约基线已有 `run_attempts.reported_result/reported_at/checkpoint_id`；proposal 整体
   （含 `event_id`）存入 `reported_result`，不需要新列/新表。`contracts/**`、`docs/spec/**` 未改，
   `sync_spec --check` no drift。
2. **exit evidence 的非契约扩展**：契约里 `evidence` 是开放对象；T031 要求
   `started/protocol_version/manifest_sha256/image_digest` 四个键（T029 只要求 `started`，且缺失
   started 时 hold；本票收紧为必填 422）。后续契约修订应把这四个字段写进 schema。
3. **新增错误码**：`run_terminal`(409)、`event_conflict`(409)、
   `execution_identity_mismatch`(409)、`run_state_conflict`(409，T035/T036 之前的显式临时拒绝)、
   `run_not_found`(404)。422 `details` 增加 `code` 定位 artifact 原因
   （artifact_not_found/not_owned/not_ready/not_linked）。复用
   `stale_attempt`/`attempt_evidence_conflict`/`validation_failed`/`invalid_json`/`forbidden`。
4. **proposal 不改 revision**：`runs.phase='finalizing'` 是展示事实，不是 STATE-MACHINES 的聚合
   状态迁移；只有终态/回收转移 bump revision。这让 result-first 的终态只有一次 bump
   （2→3），exit-first 有 park（2→3）与终态（3→4）两次聚合转移。
5. **退出先到时的 200 可能是终态**：`submitWorkerResult` 的 200 返回提交后的真实 Run；若 exit
   证据已先到，同一调用完成 finalizer，body 是 succeeded（诚实：终态已提交）。proposal-only 的
   200 永远是 running/finalizing，绝不把未提交的成功写成 succeeded。
6. **needs_reconciliation 的 job 处置**：该非终态提交 job completed（受控执行单元已退出、不会
   再调度），token revoked、槽位释放；T036 若需要 `void`/held 语义可调整。
7. **waiting_* / children guard 的接续**：proposal 与退出都存在但 waiting 或 guard 阻塞时，job
   held、run 非终态；T035/T036 负责把 run 推进到 waiting/resume 或解除 guard。T031 不 requeue。
8. **artifact 注册缺口**：T033 的 run-bound 注册端点尚不存在，测试用 SQL 写 `run_artifacts` 链接；
   `succeeded` 的准入规则（owner/ready/未删除/已关联）在 Go 内对真实行执行。finalizer 会再次校验，
   若 proposal 后 artifact 失效则转 needs_reconciliation（不假装成功）。
9. **`reported_result` 的形状**：保存的是服务端规范化的 proposal（缺省可选键不写入）；JSONB 等值
   决定幂等重放（键序/空白不构成差异）。
10. **过期租约与恢复**：仍按 T029 的保守边界——过期 attempt 不能 exit、T031 不主动 sweep；
    `recovery_pending` 的恢复证据与重新调度属 T038。T031 只保证“退出无结果 → 挂起、不重复执行”。

## 3. 实际命令与原始结果

环境：`E:\Orchara\product`，profile `dev-stub`，隔离 PG 16.4（127.0.0.1:55432），Go 1.27.3，
Python 3.12.2，Node 24.13.0。

```text
$ python -m pytest tests/f03 -q
68 passed in 306.99s (0:05:06)

$ go test ./...
19 packages ok（internal/api、artifacts、background、background/handlers、catalog、compiler、
config、db、execution、health、identity、jcs、modelproxy、objectstore、policy、supervisor、
tools、uuid、workflow），8 个 cmd/db/audit/toolruns 包 no test files，0 FAIL，exit=0

$ go vet ./...
（无输出，exit=0）

$ gofmt -l .
（无输出）

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

说明：按批次硬约束不运行五套件/全量 `pytest tests`（由 orchestrator 在收尾运行）。本票改动只涉及
`tests/f03` 与 Go 包；未新增迁移，因此既有迁移链尾断言无需适配（T029 迁移用例仍在 68 passed 内）。
基线（改动前同命令）：`tests/f03` 51 passed in 212.61s。

## 4. 票面验收逐条

| # | 验收 | 状态 | 证据 |
|---|---|---|---|
| 1 | 错镜像/错 hash 拒绝 | 通过 | `test_t031_identity.py`：protocol=2.0 / manifest=64×f / image=别的 digest 各自 409 `execution_identity_mismatch`，attempt/job/run/配额零变化；正确身份后终态 |
| 2 | 不能用新图替旧图 | 通过 | 证据必须回显 claim 时 run manifest hash 与 snapshot worker_image_digest；不匹配在任何写入之前拒绝（同 §1.2）；started=false 的 requeue 同样要求正确身份 |
| 3 | 不同 Run 不串 checkpoint | 通过 | `test_two_runs_never_share_a_token_or_checkpoint`：A run 用 B token 409 `stale_attempt` 且两边零写入；各自 proposal 的 checkpoint_id 分别落库；B 终态不影响 A（A 仍 leased） |
| 4 | 结果先到与退出先到各仅终结一次 | 通过 | `test_t031_finalizer.py` 前两条：result→exit rev 3、exit→result rev 4，均一次终态/一次 job 完成/一次配额释放/无重复行；重复调用 no-op |
| 5 | 退出无结果转挂起 | 通过 | `test_exit_then_result...` 的 park 断言 + T029 `test_started_exit_without_a_result_parks_the_run_for_recovery`：recovery_pending、phase idle、job held、slot 释放、finished_at NULL、不 requeue |
| 6 | HTTP200 结果提议不等于 succeeded | 通过 | `test_a_result_proposal_is_stored_but_never_makes_the_run_terminal`：200 body running/finalizing、DB 非终态、revision 不变、job 仍 leased、配额仍占用；proposal-only 永不返回 succeeded |
| 7 | 通过上述端到端入口实际演示；失败、当前授权与重试/恢复边界有断言，不以单层 mock 成功代替 | 通过 | 17 个新用例全部走真实 PG + 编译 API + 真实 mTLS + 真实对象字节；角色（worker proposal / supervisor exit / maintenance 403）、artifact 拒绝、event/evidence 冲突、terminal 后迟到 result、recovery_pending 不复活均有断言；事件/SSE/cancel/recovery 明确 not_run（§5） |
| 8 | 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件标 blocked，未运行标 not_run | 通过 | 页首记录版本/环境/hash；§3 记录命令与原始结果；§2/§5 记录偏离与 not_run；本票无 blocked 项；stub/real = 全部 real（SQL 只用于 T033/T035 缺失的注册/child 播种，见页首） |

## 5. 边界与未运行（not_run）

- `not_run`：`run_events`/SSE/事件去重（T032）；本票不写任何事件行，finalizer 的“事件”只体现在
  run 状态/revision 与 proposal/exit 证据上。
- `not_run`：run-bound artifact 注册/上传/下载端点（T033）；output artifact 用 SQL 播种。
- `not_run`：waiting_approval/waiting_children 的 run 状态迁移、唯一 resume job（T035）；T031 只存
  proposal 并保持 running/finalizing。
- `not_run`：cancel 端点/cancel arbitration/unknown-operation 表（T036）；guard 扩展点已就位但
  `hasUnknownOperationsTx` 恒 false。
- `not_run`：过期租约 sweep、recovery 证据与重新领取（T038）；`recovery_pending` 只挂起。
- `not_run`：worker/supervisor 真实进程与镜像（F04A/F15A）；测试客户端就是 supervisor/worker。
- `not_run`：已发布 Run 的生产入口（F02B/F05）；本票只消费 T027 的 debug Run。
- `not_run`：浏览器 DOM 驱动页面（页面 JS 由 Node 真实执行核心逻辑）。
- `not_run`：全量 `pytest tests`（按本批硬约束，由 orchestrator 运行）。

## 6. 后续票可依赖的接口与表

- 表（无新表）：`run_attempts.reported_result`（整份 proposal，含 `event_id`）、`reported_at`、
  `checkpoint_id`；`runs.phase`（idle/executing/waiting/finalizing）、`state`、`revision`、
  `finished_at`、`output_artifact_id`、`error_code`；`jobs.state`
  （ready/leased/held/completed）；`quota_allocations.released_at`。
- Go 入口：`execution.New(db, objects, signer, baseURL, leaseTTL)`；
  `Service.SubmitResult(ctx, runID, WorkerResult) (*ResultOutcome, error)`；
  `Service.ConfirmExit(ctx, ExitRequest) (*ExitResult, error)`（evidence 需带
  `started/protocol_version/manifest_sha256/image_digest`）；
  `Service.Claim/Heartbeat/LiveLease` 不变。
- finalizer 状态语义：result+exit 齐全才提交；`succeeded`/`failed` 终态；`needs_reconciliation`
  非终态；`waiting_*` 与 children/unknown guard 阻塞时 job held、run 保持 running/finalizing。
- typed errors：`ErrRunNotFound`(404)、`ErrRunTerminal`(409)、`ErrRunStateConflict`(409)、
  `ErrEventConflict`(409)、`ErrIdentityMismatch`(409)、`ErrOutputArtifact`(422 定位)、
  `ErrStaleAttempt`(409)、`ErrEvidenceConflict`(409)、`ErrAllocationConflict`(500)。
- **T032 必须承接**：run_events 表/SSE；把 claim/result/finalizer/waiting/cancel 的状态事实同事务
  投影为 `run.status` 事件（本票的 revision 转移是事件 payload 的事实源）；result endpoint 的
  proposal 已含 event_id，可作为事件幂等键的映射依据。
- **T035 必须承接**：waiting proposal → run waiting_*、phase waiting、唯一 resume job；以及
  children guard 解除后的重新驱动（T031 不重跑 finalizer）。
- **T036 必须承接**：cancel 请求优先于未提交结果；`hasUnknownOperationsTx` 的真实查询与
  needs_reconciliation 语义；cancel 后不得 requeue。
- **T038 必须承接**：lease 过期 → recovery_pending（与 T031 的“exit 无结果”区分证据来源）、
  旧执行单元/DB 排空证据、唯一重新领取；T031 的 `recovery_pending` 不自动复活。
- **T033 必须承接**：`registerRunArtifact` 写 `run_artifacts`（T031 的 succeeded 准入依赖该链接，
  目前测试/种子代写）；output artifact 的 attempt_token 关联与 purpose 约定由 T033 固化。

## 7. 文件与提交

- `internal/execution/{execution.go,store.go,service.go,service_test.go}`
- `internal/api/{execution.go,internal.go}`
- `apps/web/src/{runs.html,assets/runs.js,debug.html,assets/debug.js}`
- `tests/support/{t029.py,t031.py}`、`tests/f03/test_t031_{result,finalizer,identity,pages}.py`、
  `tests/f03/{test_t029_exit.py,test_t029_invariants.py}`
- `reports/f03/T031-result-and-finalizer.md`、`PROGRESS.md`、`E:\orchara-notes\f05-f03\NOTES.md`
- 提交：feat（Go 服务/端点 + Web）、test（`tests/f03` + 收紧）、docs（本报告 + PROGRESS +
  NOTES）。不推送、不关闭 issue。

## 8. 规格变更

无。`product/docs/spec/**` 与 `product/contracts/**` 未修改；`python tools/sync_spec.py --check`
no drift。发现的契约缺口（evidence 身份四字段、新错误码、exit-first 时 200 可为终态、
needs_reconciliation 的 job 语义）记录在 §2，由后续契约修订统一处理。
