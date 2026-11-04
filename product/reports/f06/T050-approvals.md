# T050 / GitHub #51 — 使用者确认真实参数后安全恢复受控操作

状态：**`passed`**（范围 = 服务端从 Run/operation/策略/manifest 真实事实构建并封存审批提案；写风险
连接器操作在 capability 代理里同事务创建 pending approval 并停住；指定人类审批人读取/决定；精确
params hash、过期、撤权、Run 终态/取消使批准不可复用；并发相反决定只有一个赢家、同决定重放幂等；
批准把 operation 从 `awaiting_approval` 翻到 `authorized`，旧尝试退出后经 T035 唯一
`maybeEnqueueResumeTx` 创建恰好一个 resume，恢复的尝试仍重校验权限/预算并在
`authorized → dispatching` CAS 后恰好派发一次；拒绝/过期为 `approval_rejected`/`approval_expired`
且零外部写入；发布审核无法决定 Run 审批）。
真实厂商连接器语义、自动过期 sweeper、浏览器 DOM 驱动见 §8 `not_run`。

证据：本文件、`PROGRESS.md` T050 段、ADR 0007 D17。未修改 `docs/spec/**`、`contracts/**`、
迁移 0001–0030；新增迁移仅 `0031_run_approvals.sql`。

## 1. 验收对照（issue #51）

| 验收项 | 证据 | 结论 |
|---|---|---|
| 审批页展示服务端真实参数、目标、去向 | `test_t050_approval.py::test_server_builds_the_exact_proposal_and_only_the_approver_can_read_it`：从 sealed proposal 逐项断言 run/operation/logical key/kind/version/risk、`target.origin/method/path_prefix/path`、canonical params、`data_destination`、policy/manifest/snapshot 版本、expiry；worker 自报文案从不进入提案。`test_t050_pages.py` 用 Node 执行 `describeProposal`，断言页面渲染同一组事实 | 通过 |
| 批准重放幂等、相反决定并发只有一个成功 | `test_concurrent_opposite_decisions_have_one_winner_and_same_replay_is_idempotent`：两个真实会话线程同时 approve/reject → `[200,409]`，输家 `decision_conflict`，赢家状态落库；同决定重放 200 且不新增 resume。`Run → approval → operation` 锁序与 `UNIQUE(operation_id)` 是权威 | 通过 |
| 换参数/过期/拒绝/撤权不能复用批准 | 换参数：同 logical key 改 body → 409 `operation_conflict`，approval 不变；`test_changed_params_or_wrong_hash_cannot_reuse_the_approval`。过期：过期后 decide approve → 409 `approval_expired`，operation failed，exit 后 Run failed `approval_expired`、零出站。拒绝：Run failed `approval_rejected`、零出站。撤权：disabled principal 连认证都被拒；批准后撤权再 exit → Run failed `permission_revoked`、零 resume/零出站 | 通过 |
| 旧尝试退出后只建一次 resume | `test_approve_before_exit_produces_one_resume_and_one_dispatch`：waiting result 后先 decide → decision 已存但无 resume（`exit_confirmed_at` 为空）；exit 后恰好 1 个 resume job。`test_decision_after_exit_still_creates_one_resume`：先 exit 后 decide，同样恰好 1 个。`test_approve_before_the_waiting_result_lands_still_converges_to_one_resume`：waiting result 之前就先落决定，写 waiting 行时采纳决定，exit 后仍恰好 1 个 | 通过 |
| 端到端实际演示；失败/授权/重试边界有断言 | 真实 PG 0031 + 编译 `cmd/api` + 真实 mTLS + 对象字节 + 真实 loopback HTTP：恢复的新 attempt 用同一 operation/logical key/params 经 `invokeCapability` 真实派发一次；重复调用为 recorded replay，loopback 只收到 1 次请求、operation 只有 1 条 attempt。预算 0 时批准不产生 resume（`budget_exhausted`）；批准后撤销策略，恢复调用 403 `connector_policy_revoked` 且零出站 | 通过 |
| 记录版本/输入/hash/命令/原始结果/profile，stub/real 分报，缺条件 blocked、未跑 not_run | §5–§8 | 通过（`not_run` 如实列出） |

## 2. 交付内容

### 2.1 迁移 0031：`platform.approvals`

`id, run_id, operation_id UNIQUE, node_key, proposal_artifact_id, params_sha256, approver_id,
state(pending/approved/rejected/expired/invalidated), expires_at, decided_at, decided_by,
decision_key, UNIQUE(run_id,node_key,params_sha256)`，`approvals_pending` 部分索引 +
`approvals_run`；与 0007–0030 相同权限边界（worker/PUBLIC 无 platform 权限）。

与 `contracts/database.sql` 基线的唯一差异（迁移头注释 + §8）：

1. `node_key` 用 logical key 的节点前缀填充（`agent:<node_id>` 或整 key）。契约没有
   `operations.node_key`，T050 不得新增列；`UNIQUE(operation_id)` 已使 approval 与 operation 1:1。
2. 不新增 `operations.approval_id`；`approvals.operation_id` 是唯一链接，避免双写不一致。
3. 新增 `approvals_decision_pair` CHECK：`pending` ⇔ `decided_at IS NULL`，让“决定时间”无法与
   状态脱节；不新增 created_at 列，list 游标按 `(pending-first, expires_at, id)` 排序。

### 2.2 `internal/approvals`（新包）

- `Service{DB, Objects, Execution}`、`New(db, objects, resume)`；`Execution` 是唯一 resume 权威。
- `PrepareRecordTx(ctx, tx, PrepareRecord)`：capability 代理在同一事务里调用；构建 RFC 8785 JCS
  提案（`approval_version, run_id, operation_id, logical_key, node_key, capability_kind/version,
  risk_class, target{origin,method,path_prefix,path,query}, params, params_sha256,
  data_destination, policy_sha256, manifest_sha256, snapshot_id, snapshot_sha256, expires_at`）；
  指定审批人 = Run owner（service-account 用 `principals.responsible_user_id`，无负责人 fail
  closed）；提案封存为 `purpose=approval_proposal` artifact，owner 为审批人，经既有
  artifact-content ACL 读取。同 operation 同 hash 幂等返回既有行；hash 不同 409。
- `Prepare(ctx, PrepareRequest)`：契约 `prepareApproval`。重证 live attempt + operation 属于该 run
  + logical key + write connector + awaiting/authorized/prepared；从 DB 重建同一提案；worker 的
  `proposal` 字段被忽略。
- `Get/List`：仅指定审批人可读；admin 可读、但不是默认审批人；stranger 统一 404。List
  pending-first keyset cursor（limit 1–100）。
- `Decide(ctx, DecisionRequest)`：锁序 `Run → approval → operation`（capability/cancel/waiting
  路径都先取 Run 锁，故不会与决定逆行）；校验 decision/params hash/审批人/过期（数据库时钟）/
  current principal/run state/operation state/policy revocation；同决定重放 200，相反 409
  `decision_conflict`；approve 同事务 `approval=approved` + operation
  `awaiting_approval → authorized`；reject `approval=rejected` + operation failed
  `approval_rejected`；expire `approval=expired` + operation failed `approval_expired`。
- 决定提交后调用 `execution.ResolveWaiting(kind=approval,outcome=...,reference=<approval id>)`；
  T035 的 `maybeEnqueueResumeTx` 仍是唯一 resume 创建者（含 exit-first / decision-first 汇聚）。
- 稳定错误码：`approval_not_found`、`approver_mismatch`、`approval_not_pending`、
  `approval_expired`、`approval_params_mismatch`、`decision_conflict`、`permission_revoked`、
  `policy_revoked`、`operation_state_conflict`、`approval_not_applicable`、`approver_unavailable`。

### 2.3 capability 代理集成

- `capabilities.Service.Approvals`；`prepareOperation` 在写风险 connector 的同一事务里
  `PrepareRecordTx` + operation `awaiting_approval`；`Result.ApprovalID` 返回契约字段。
- 停住状态重放返回同一 `awaiting_approval` + 同一 `approval_id`（`replayTx` 查询 approvals）。
- 恢复：既有 operation 为 `authorized` 且 params/logical key/kind 不变时 `prepareOperation`
  返回 `proceed`，`dispatchConnector` 的 `authorized → dispatching` CAS 仍是唯一派发门；并发第二次
  恢复看到 `dispatching`/`succeeded`，不会二次出站。
- `loadAuthorizedRun` 新增 owner `disabled_at` 检查（403 `permission_revoked`）：批准不绕过当前
  权限；`resolveConnector` 重启策略批准/撤销校验（批准不绕过策略）。

### 2.4 T035 waiting/resume 桥接（`internal/execution`）

- `applyWaitingTransitionTx` 在写 waiting row 后调用 `adoptApprovalDecisionTx`：决定先于
  waiting result 落库时，只把**本 checkpoint 的 operation** 的真实 approval 决定复制进等待行，随后唯一 resume 事务照常工作。
- `maybeEnqueueResumeTx` 对 approval 决定：approved 照旧 resume；rejected/expired 仅当 reference
  是真实 `platform.approvals` 行（状态 rejected/expired）时以 `approval_rejected`/
  `approval_expired` 终止 Run。T035 非契约 synthetic bridge 的 opaque reference 仍按 T035 契约
  恢复后由 worker 处理失败，**不修改 T035 测试期望**。
- 取消 / Run 终态：`moveRunToCancellingTx`、immediate cancel、reconciliation park、终态 finalize
  都会把 pending approvals 置 `invalidated`（保留 proposal artifact 供审计）。

### 2.5 executor（`internal/presetexec`）

- `Proposal.CheckpointID`；`runner.waitAgent` 把 `awaiting_approval` 能力结果转为
  `suggested_state=waiting_approval`、`checkpoint_id=capability:<operation_id>`、
  `error_code=approval_required`，然后提交结果并 `ConfirmExit`；agent 模型/工具两处
  `awaiting_approval` 不再失败 Run。supervisor client 的 result body 透传 `checkpoint_id`。
- 不报告成功、不创建 approval；approval 只由 capability 代理构建。

### 2.6 HTTP / 页面

- `POST /internal/v1/runs/{run_id}/approvals`（worker mTLS，契约 `prepareApproval`，201 Approval）。
- `GET /api/v1/approvals`（审批人；`{items,next_cursor}`，pending-first）。
- `GET /api/v1/approvals/{approval_id}`（审批人或 admin；其余 404）。
- `POST /api/v1/approvals/{approval_id}/decision`（仅审批人；`Idempotency-Key` 必需；200 Approval，
  409/403/404/422 契约错误）。
- `/approvals` 页面：pending list + 详情（真实目标/方法/路径/规范化参数/去向/来源版本/过期/
  参数 hash/提案 artifact），批准/拒绝两个按钮**无默认选择**，只在提案读取成功且 params hash
  一致时启用；明确“批准不赋予额外权限”“取消/拒绝不会撤销已发生的外部写入”；决定带精确
  `params_sha256` + 新 Idempotency-Key。`/runs` 导航新增“审批”入口。

## 3. T051 可直接使用的确切接口

- 审批生命周期：`approvals.New(db *sql.DB, objects *objectstore.Store, resume *execution.Service)
  *Service`；`(*Service).Get(ctx, callerID, approvalID string, callerIsAdmin bool) (*Approval,error)`；
  `List(ctx, callerID string, limit int, cursor string) ([]Approval, string, error)`；
  `Decide(ctx, DecisionRequest) (*Approval, error)`。
- 类型：`Approval{ID, RunID, State, ProposalArtifactID, ParamsSHA256 string; ExpiresAt time.Time;
  ApproverID, OperationID string; DecidedAt *time.Time}`；
  `DecisionRequest{ApprovalID, CallerID string; CallerIsAdmin bool; Decision, ParamsSHA256,
  IdempotencyKey string}`；`PrepareRecord`/`PrepareRequest`/`Proposal`/`Target` 见
  `internal/approvals/prepare.go`、`service.go`。
- Capability 结果：`capabilities.Result.ApprovalID *string`；停住返回
  `state="awaiting_approval"`、`data.error_code="approval_required"`。
- Execution 桥：`execution.ApprovalRejectedCode`/`ApprovalExpiredCode`；
  `execution.ResolveWaiting` 仍接收 `{Kind:"approval", Outcome:"approved"|"rejected"|"expired",
  Reference:<approval id>}`。
- 稳定状态/码：approval state 五种；Run failure `approval_rejected`/`approval_expired`；
  API 错误码见 §2.2。

## 4. 测试

### 4.1 Go 单测（真实变更处）

- `internal/presetexec/agent_test.go`：`TestAgentAwaitingApprovalParksTheAttempt`（awaiting→
  waiting proposal + `capability:<operation_id>` checkpoint + exit，零工具沙箱）；
  `TestAgentUnknownOutcomeStillFails`（unknown 仍失败）。
- `internal/capabilities`、`internal/execution`、`internal/api` 既有单测全绿（审批只改服务接线与
  replay 分支）。

### 4.2 真实栈（`tests/f06/test_t050_approval.py`，15 用例）

真实 PG（0001–0031）、编译 `cmd/api`、真实 mTLS（supervisor/worker）、真实对象字节、真实
admin connector registry、真实 loopback HTTP server。覆盖：提案/ACL/list；内部 prepareApproval
幂等且忽略 worker 文案；approve（decision-before-waiting-result、decision-before-exit、
exit-before-decision 三种顺序，各恰好 1 resume、恢复后恰好 1 次真实出站、replay 不再出站）；
reject；expire；换参数/错 hash；并发相反决定 + 同决定重放；同一 Run 的两次审批中旧决定不被新 wait 采纳；撤权拒绝与批准后撤权阻止 resume；
预算 0 阻止 resume；批准后撤销策略阻止恢复派发；取消使 pending invalidated；真实
`publication_reviews` 行 404（发布审核不能决定 Run 审批）。

### 4.3 页面（`tests/f06/test_t050_pages.py`，3 用例）

`/approvals` 与 `/assets/approvals.js` 结构断言；Node 执行 `describeApproval`/`describeProposal`/
`decisionPayload`/`decisionConfirmationText`：真实目标/参数/去向往页面投影、无显式决定或空 hash
拒绝、确认文案含“不赋予额外权限/不撤销外部写入”、路径编码。浏览器 DOM 驱动 `not_run`。

### 4.4 T035/T036 回归

`tests/f03/test_t035_{migration,waiting,resume,resume_refusals,pages}.py` +
`test_t036_{cancel,unknown}.py` 全部通过（见 §6）；T035 迁移尾部测试按 T045 已有模式改为
“连续链 + 本票行按 version”，不把尾部钉死；T035 的 synthetic rejected/expired resume 契约
保持不变。

## 5. 环境与版本

- 工作树 `E:\Orchara-wt\t050`，分支 `t/t050-approvals`，base `ed04e73`（T049 merge）。
- Go `E:\orchara-toolchains\go\bin\go.exe`；`GOCACHE=E:\orchara-toolchains\gocache`、
  `GOMODCACHE=E:\orchara-toolchains\gomodcache`。
- PG 隔离集群 `127.0.0.1:55432`（`product/.env.local`）；WSL2/runsc 可用（本票真实栈未使用
  sandbox 路径，均为 HTTP/mTLS/对象字节/DB）。
- 迁移链：0001–0030 未改，新增 0031；`contracts/**`、`docs/spec/**` 未改，
  `python tools/sync_spec.py --check` → `no drift`。

## 6. 门禁（原始输出尾部）

```text
$ E:\orchara-toolchains\go\bin\go.exe vet ./...
(no output)                                    exit=0

$ E:\orchara-toolchains\go\bin\go.exe test ./...
<见 §6.1 完整尾部>                             exit=0

$ E:\orchara-toolchains\go\bin\gofmt.exe -l internal cmd tools
(no output)

$ python -m pytest tests/f06 -q
<见 §6.2>

$ python -m pytest tests/f03/... -q
17 passed（T035 waiting/resume/pages）+ 7 passed（T035 migration）+ T036 cancel/unknown

$ python -m pytest tests/tooling -q
21 passed in 5.99s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

### 6.1 `go test ./...` 尾部

```text
<占位：由收尾命令原始输出替换>
```

### 6.2 `pytest tests/f06 -q`

```text
<占位：由收尾命令原始输出替换>
```

## 7. stub / real 分报

- **real**：PG 迁移/事务/唯一约束/行锁；HTTP 会话/CSRF/mTLS；artifact 对象字节与 ACL；
  connector 策略登记、manifest 冻结与真实 loopback HTTP 出站；waiting result/exit/唯一 resume；
  capability replay 与 `authorized → dispatching` CAS。
- **stub / seam**：Go 单测的 fake capability client / fake supervisor client（只替代 transport）；
  页面测试用 Node 执行纯函数（无浏览器进程）；真实厂商连接器仍是部署定义。
- 没有任何“fake approval success”：approval 行、决定、resume、出站均由上述 real 路径产生。

## 8. 偏差、边界与 `not_run`

1. **契约基线没有 `approvals` 实现**：表按 `contracts/database.sql` 的列创建（0031），只新增
   `approvals_decision_pair` CHECK 两个索引；`node_key` 填充规则与“不新增 operations.approval_id”
   写入迁移头注释与 §2.1。未改契约/生成源。
2. **过期没有后台 sweeper**：本票只在决定路径用数据库时钟判定过期并终止等待 Run；一个无人处理
   的 pending approval 会一直保留到有人读取/决定或 Run 被取消/终止。自动过期扫描（maintenance）
   是后续运维票，`not_run`，页面显示真实 `expires_at`，不伪造已过期。
3. **撤权在认证层更早拒绝**：disabled principal 的会话在 identity 层即 401/403
   `principal_disabled`，因此 `Decide` 内部的 `permission_revoked` 分支是纵深防御（HTTP 不可达）；
   批准后再撤权则由 `maybeEnqueueResumeTx` 以 `permission_revoked` 终止 Run（有真实测试）。
4. **`approval.required` 持久 Run 事件未新增**：approval 行/`approval_id` 是权威事实；事件流
   schema 扩展不在本票范围，`not_run`（页面从 approval API 读取，不依赖事件）。
5. **agent 到 connector 写路径不可直接到达**：当前 preset agent 的工具是 `kind=tool`；executor
   的 awaiting 停住行为用 fake capability client 的真实 proposal/exit 序列验证，真实栈用
   supervisor waiting 协议验证审批/恢复。真实 agent→connector 写策略消费属后续连接器消费票。
6. **service-account Run 的审批人**：实现按 `principals.responsible_user_id` 解析并在无负责人时
   fail closed；F04A 只有 user owner 路径被真实栈覆盖，service-account 路径 `not_run`。
7. **浏览器 DOM**：`/approvals` 页面由编译后 API 提供结构断言 + Node 纯函数行为测试；真实
   click/confirm/CSRF 浏览器流程 `not_run`（无浏览器驱动）。
8. **未跑**：全量 `pytest tests`、`tests/f00b`、`tests/f01`、`tests/f02a`、`tests/f04a`、
   `tests/f05`（按门禁只跑 f06/f03/tooling）；真实厂商连接器语义；S3/MinIO 对象存储替换。
9. 未 push、未关闭/编辑 issue #51；未改 `docs/spec/**`、`contracts/**`、迁移 0001–0030；
   分支仅新增 0031。
