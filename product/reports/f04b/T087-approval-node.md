# F04B/T087 人工审批节点：服务端业务审批 + 重启后继续原决定（GitHub #88）

**状态：`passed`**（运行时 + 等待桥 + 执行器 + 画布 + 真实栈验收已交付；票面“审批过期后终止”曾因
没有自动过期 sweeper 标 `partial`，第三轮复核已补齐 API 后台 sweeper 与真实等待自动过期用例，见 §7）。
全量 `tests/f06` 在共享栈争用下未重跑（`not_run`，见 §5），审批/等待相关子集已通过（§4.2）。

- 分支：`t/t087-approval-node`，worktree `E:\Orchara-wt\t087`，基线 `integration/t083-t091` @ `491cb78`；
  结束前 merge 集成分支（`f21f09a`）→ 合并提交 **`b086419`**；本报告与 `PROGRESS.md` 追加提交后的 HEAD 见
  `E:\Orchara\.scratch\t083-t091\T087-REPORT.md`。
- 范围：`internal/approvals` 的 node approval（服务端提案/幂等/审批）、`internal/execution` 的等待桥
  `approval:<approval_id>`、`internal/presetexec` 的 graph approval 节点、`cmd/supervisor` 的 worker mTLS 实现、
  画布 approval palette/panel/export、审批页 title/节点渲染。
- **不改** `product/docs/spec/**`、`product/contracts/**`、迁移；无新迁移（`platform.approvals` 已存在，
  `operation_id` 可空，`UNIQUE(run_id,node_key,params_sha256)` 已够用）。

## 1. 验收对照

| 票面验收项 | 结论 | 证据 |
|---|---|---|
| 创作者在画布加入人工等待，使用者在审批页决定后恢复同运行 | **passed** | 真实栈：`start → approve1(approval) → end`，第一次 `cmd/supervisor exec --once` 真实领取得 Run `waiting_approval`、approval `pending`、事件 `approval.required`；approver 通过 `POST /api/v1/approvals/{id}/decision` 批准；唯一 resume job 生成；第二次 exec（新进程）同一 Run 恢复并 `succeeded`、最终输出 `{approval_id, decision}`。`tests/f04b/test_t087_approval_node.py::test_approved_node_approval_resumes_the_same_run_once` |
| 通用审批不能授权后续未知外部写 | **passed** | 同一真实 Run 内：节点审批批准后 `platform.operations` 仍为 0；随后真实 write-risk connector capability 调用仍返回 `awaiting_approval`，并创建**自己的** operation + operation 级 approval（`operation_id` 非空、`node_key != node:approve1`）；节点审批行始终 `operation_id IS NULL`。`::test_a_node_approval_never_authorises_a_later_write_operation` |
| 重放不重复审批（同一 proposal 复用原行） | **passed** | `UNIQUE(run_id,node_key,params_sha256)`：同一 attempt 内二次 prepare 返回同一 id、approvals 恰 1 行、`approval.required` 恰 1 条；恢复后的第二次 exec 再次 prepare 同一 proposal 仍返回原行（approvals 恰 1 条、resume job 恰 1 个）。`::test_node_approval_is_a_server_built_business_decision_and_replays_idempotently`、`::test_approved_node_approval_resumes_the_same_run_once` |
| 审批拒绝后终止 | **passed** | reject → 200 `rejected`；Run `failed` + `error_code=approval_rejected`；无 resume job、无 output artifact、无 operations/usage；再次 exec `idle`（无 resume 可继续）。`::test_rejected_node_approval_terminates_the_run_without_a_resume` |
| 审批过期后终止 | **passed**（第三轮复核补齐自动 sweeper） | 两条真实栈路径：① 决定路径：把 `expires_at` 置为过去 → 决定 409 `approval_expired`；approval `expired`；Run `failed` + `approval_expired`；无 resume/output/operations（`::test_expired_node_approval_terminates_the_run_and_refuses_the_decision`）。② 自动路径：停在 `waiting_approval` 后把 deadline 置为过去，**不调用任何 decision**，API 进程的后台 sweeper 到期自动把 approval 置 `expired`、Run 置 `failed approval_expired`，waiting decision 为 `expired`、无 resume job/output（`::test_a_parked_approval_expires_automatically_without_a_decision`，fast-sweep 栈）。 |
| 两次审批（T091 预演） | **passed** | `start → a1 → a2 → end`：两次等待、两次决定、第三次 exec 汇合 `succeeded`；2 条独立 approval（均 `operation_id NULL`）、2 个不同 resume job、a1/a2 各 1 条 data artifact、最终输出含两个 approval id。`::test_two_approvals_wait_twice_and_converge_on_one_run` |
| 参数变化不复用旧决定 | **passed** | 同 node 不同 proposal → 新 approval id/新 `params_sha256`（2 行，旧行保持 approved）；重放新 proposal 不新增第 3 行；用旧 hash 决定新 approval → 409 `approval_params_mismatch`，状态仍 pending。`::test_a_changed_proposal_is_a_new_approval_and_the_old_decision_does_not_apply` |
| 真实端到端（真实 PG/mTLS/supervisor/API，无 mock 通过） | **passed** | 真实 PostgreSQL 16 隔离库、编译 `cmd/api`/`cmd/supervisor`、真实 mTLS internal listener、真实对象目录封存、真实 F03/T035 waiting/resume 事务、真实审批 API。§4 原始输出 |
| 失败/授权边界断言；版本/hash/命令/环境记录；stub/real 分报 | **passed** | 拒绝/过期/参数冲突/非 approver 404/授权边界均有断言；§3 版本与输入 hash；§4 原始输出；Go 单测 seam 标为 stub，真实栈单独列 |

票面 AC 中“model 输出 object 且 Schema 失败独立”属 **T086**（已合并，`reports/f04b/T086-model-node.md`）；
本票不重复实现，也未把 T086 的 E2E 计入本票证据。

## 2. 语义与设计决定

### 2.1 节点审批是业务决定，不是 capability 授权（A5/A10/N-A11）

- 行：`platform.approvals`，`operation_id IS NULL`，`node_key = "node:<node_id>"`，`approver_id = run owner`
  （服务账号则其 responsible human，复用 `approverForOwnerTx`）。
- **不**创建 `platform.operations` 行、**不**写 capability grant、**不**改 capability proxy 的授权判断；
  后续每个写工具仍走自己的 operation 审批（真实栈证明见 §4.3 用例 6）。
- proposal 由服务端从运行事实构建并封存：run、sealed definition 节点、`manifest_sha256`、`snapshot_id/sha256`、
  node config 的 title、以及节点绑定的 `proposal` 输入对象；`params_sha256 = sha256(JCS(proposal))`。
  worker 不提供决定，也不能伪造标题。
- 幂等键 = `(run_id, node_key, params_sha256)`；同一 canonical proposal 的重放返回原行（任何状态）；**proposal
  变化 = 不同 params_sha256 = 新审批行**，旧决定绝不复用。这与票面 §3.1 “不同 params → 409”的备选表述冲突，
  按 schema 的 UNIQUE 实现并在此文档化：`approval_params_mismatch` 只用于决定路径（决定的 hash 必须等于该行
  `params_sha256`），变更 proposal 不复用旧决定才是 A4/N-A12 的实质要求。
- 状态机不变：`pending → approved|rejected|expired|invalidated`；重复同决定幂等，相反决定 409；取消/终态由既有
  `invalidateRunApprovalsTx` 失效。

### 2.2 等待桥（`internal/execution/approval_bridge.go`）

- 新 checkpoint 形态 `approval:<approval_id>`：`adoptApprovalDecisionTx` 按 id + run + `operation_id IS NULL`
  读取已提交决定，写入 waiting decision；`capability:<operation_id>` 分支逐字保留（既有 F06 行为不变）。
- `approvalDecisionRefusesTx` 本就按 id 查真实 approvals 行：节点审批 rejected/expired → `maybeEnqueueResumeTx`
  以稳定码 `approval_rejected`/`approval_expired` 终止 Run；不创建 resume。
- resume 仍由唯一 `maybeEnqueueResumeTx` 创建；payload 只含 run/kind/checkpoint/决定引用（不新增任意
  Command/thread/user id）。

### 2.3 执行器（`internal/presetexec/approval.go`）

- graph plan：`approval` 加入 `graphNodeTypes` 与 `hasGraphConstructs` 触发；`parseApprovalConfig` 复验闭集
  `{title, expires_seconds(60..86400)}` 与必需 `proposal` 输入（literal object 或合法 ref）→ 失败 `bad_config`。
- 执行：解析 binding（非对象 → `node_input_invalid`）→ `Client.PrepareNodeApproval`（worker mTLS + live attempt）
  → `pending`：提交 `waiting_approval` + `checkpoint="approval:<id>"` + `error_code=approval_required`，
  ConfirmExit 后**不继续**；`approved`：输出
  `{approval_id, decision:"approved", decided_at, params_sha256, proposal}`，登记 node data artifact 并继续；
  `rejected`/`expired`/`invalidated` → 节点/Run 以同名稳定码失败（`approval_invalidated` 新，文档化）。
- 恢复：重启后再次 `PrepareNodeApproval` 返回同一 approval；已 approved 直接输出、不重复等待。上游已完成
  节点的 data artifact 由 T083 recovery seam 复用（本票第一次以真实 resume 验证：两次审批时 a1 只发布 1 条
  data artifact，第三次 exec 复用而非重跑）。
- 稳定码：`approval_rejected`、`approval_expired`（spec 已有）、`approval_invalidated`（新，文档化）、
  `approval_required`（等待中的 error_code）、`approval_prepare_failed`（平台拒绝 prepare/replay 时的防御码）、
  `node_input_invalid`。node 输出契约见 §2.4。

### 2.4 node approval 封存文档与节点输出（非契约）

`approval_proposal` artifact 内容（服务端构建，JCS 封存）：

```json
{"approval_version":1,"kind":"node_approval","run_id":"…","node_id":"approve1",
 "node_key":"node:approve1","title":"Approve the order","proposal":{…},
 "params_sha256":"…","manifest_sha256":"…","snapshot_id":"…","snapshot_sha256":"…",
 "definition_nodes":["start","approve1","end"],"expires_at":"…"}
```

approval 节点的 data artifact（approved 后）：

```json
{"approval_id":"…","decision":"approved","decided_at":"…","params_sha256":"…","proposal":{…}}
```

审批页从同一 artifact-content ACL 读取该文档，显示 title/节点/来源（§2.6），不改契约字段
（`Approval` 仍只有 id/run_id/state/proposal_artifact_id/params_sha256/expires_at）。

### 2.5 契约与代码扩展点

- 唯一新内部路由：`POST /internal/v1/runs/{run_id}/node-approvals`（worker mTLS + attempt_token，非契约；
  返回 `{id,state,expires_at,params_sha256,proposal_artifact_id,decided_at}`）。
- `internal/execution` 新增导出 `AppendApprovalRequiredTx`，在插入 approval 的同一事务写 `approval.required`
  `{approval_id,expires_at}`（每个真实新增 approval 恰 1 条；重放不重复）。事件类型本就在 event.schema.json。
- 服务端复用 `GET /api/v1/approvals/{id}` 与 `POST …/decision`：`operation_id` 扫描改为 `sql.NullString`，
  `Approval.NodeKey` 新增；`Decide` 对 node approval 跳过 operation/policy 检查与 operations 更新
  （approve/reject/expire/invalidate 均不再触达不存在的 operation）。

### 2.6 画布与审批页

- 画布：palette `approval`（可重复）、`NODE_LABELS.approval='人工审批'`、默认
  `config={title:'',expires_seconds:3600}` 与 `inputs.proposal={literal:{}}`；面板含 title（必填/200 字符提示）、
  expires_seconds（60..86400）、proposal 来源绑定或 JSON 字面量编辑器（非对象/非法 JSON 就地报错），并明示
  “审批只代表业务决定，不授权后续外部写”；export allowlist `approval: ['title','expires_seconds']`；bundle 重建提交。
- 审批页：`describeProposal` 识别 `kind=node_approval`，渲染真实 title、`node:<id>`、proposal 对象、来源
  （定义节点/snapshot/manifest）；批准确认文案对节点审批改为“只是业务决定、后续写仍各自需要 operation 审批”；
  capability 提案渲染逐字不变（T050 页面测试保持通过）。

### 2.7 修复的既有测试缺陷（与本票产品行为无关）

`tests/f04b/test_t083_runview.py` 的条件/join run-view 真实栈用例在集成分支 `491cb78` 上就有两处测试代码错误
（该用例在 graph runtime 合并前一直 skip，从未真正跑过）：把 `t083.node_results` 返回的 items 列表当成
`{"items": [...]}` body，且 run-view harness 需要 body 形状。已最小修复（列表迭代 + `{"items": node_results}`），
`tests/f04b/test_t083_runview.py` 7 passed；不改变任何产品断言。

### 2.8 自动过期 sweeper（第三轮复核补齐）

- `internal/approvals/expiry.go::ExpireDue`：只扫描 T087 node approval
  （`operation_id IS NULL`，每轮最多 100 条 `state='pending' AND expires_at <= now()`），逐条在
  `Run -> approval` 锁序下重读；截止时间由数据库 `now()` 判定，不信任进程时钟。到期行置
  `expired`（`decided_by=NULL`、`decision_key='sweeper:expired'`）。F06 capability approval 保持既有
  decision/lazy 边界，不归本 sweeper。
- 只有 waiting checkpoint 真正指向该 approval（`approval:<id>`）时才交接；更早 proposal 遗留的
  pending approval 只过期自身，不会错误终止正在等待另一个 approval 的 Run。
- **原子交接（复审 P1 修复）**：置 `expired` 后不提交再调用 `resolveWaiting`，而是在**同一事务**内调用
  `internal/execution.ResolveExpiredNodeApprovalTx`：写入 `outcome=expired` 的 waiting decision，并驱动
  既有 `maybeEnqueueResumeTx`；exit 已确认时同事务把 Run 置 `failed approval_expired`，exit 未到时只持久化
  决定，后续 `ConfirmExit` 接管。任何一步失败（含等待更新故障）都回滚整次过期，下一轮 sweeper 仍看到
  `pending` 并重试，不会留下“approval 已 expired、waiting decision 为空”的死状态。
- `cmd/api` 启动定时器（`ORCHARA_APPROVAL_SWEEP_INTERVAL`，默认 30s，范围 10ms..5m）；每轮独立事务，
  `FOR UPDATE` 重读与决定路径串行化，重复/多实例扫描是幂等的。`internal/api.Server.ExpireDueApprovals`
  只暴露一次确定性 pass，定时器生命周期归 `cmd/api`。测试中 fast-sweep 栈用 100ms，其余真实栈用
  5m test-only override 避免与 decision 路径用例竞速。

## 3. 环境、版本与输入 hash

| 项 | 值 |
|---|---|
| Go | `go1.27.3 windows/amd64`；`GOCACHE=E:\orchara-toolchains\gocache`，`GOMODCACHE=E:\orchara-toolchains\gomodcache` |
| PostgreSQL | 16（隔离集群 `127.0.0.1:55432`，`tests/support/stack.py` 每用例新库，迁移链 0001→0052） |
| Python | 3.12.2；pytest 9.0.3 |
| Sandbox | WSL2 Ubuntu-24.04 + runsc（真实栈用例启动真实 `cmd/supervisor`；审批 identity 图本身不启动沙箱，`no_orphan_evidence().runsc_list == []`） |
| 模型 | 本票不调用模型（无 model 节点）；dev-stub 不参与本票证据 |
| 节点审批输入 hash（真实栈） | proposal `{"subject":"order-42","amount":"1250.00"}` → `params_sha256` = 独立 Python JCS 计算值，与 DB/artifact 一致（用例 1 断言）；manifest/snapshot hash 由真实编译器生成并封存在 proposal 文档中 |
| 分支/HEAD | 见 `.scratch/t083-t091/T087-REPORT.md`（合并 `integration/t083-t091` @ `f21f09a` → `b086419` + 文档提交） |

## 4. 证据：命令与原始输出

工作目录 `E:\Orchara-wt\t087\product`。原始日志：`E:\Orchara\.scratch\t083-t091\T087\`。
所有真实栈 pytest 均在全局锁 `E:\Orchara\.scratch\t083-t091\pytest.lock` 下串行执行（拿锁→跑→删锁）。

### 4.1 本票套件（真实栈 + 画布，合并前）

```powershell
python -m pytest tests/f04b/test_t087_approval_node.py tests/f04b/test_t087_canvas.py -q
```
```text
.........                                                                [100%]
9 passed in 57.79s
```

逐用例首次通过（同一实现，用于定位）：
```text
test_node_approval_is_a_server_built_business_decision_and_replays_idempotently  1 passed in 13.35s
test_approved_node_approval_resumes_the_same_run_once                            1 passed in 21.60s
test_rejected_node_approval_… / test_expired_node_approval_…                     2 passed in 19.17s
test_two_approvals_wait_twice_and_converge_on_one_run                            1 passed in 18.30s
test_a_changed_proposal_is_a_new_approval_…                                      1 passed（与下一用例同批）
test_a_node_approval_never_authorises_a_later_write_operation                    1 passed in 13.50s
```

### 4.2 f06 审批/等待回归子集

```powershell
python -m pytest tests/f06/test_t050_approval.py tests/f06/test_t050_pages.py tests/f06/test_t043_agent_node.py -q
```
第一次运行：`1 failed, 26 passed in 209.46s`，失败用例
`test_agent_final_output_schema_failure_is_located_and_never_succeeds` 报
`the attempt lease was lost while the sandbox ran … heartbeat: context canceled`（runsc/sandbox 资源争用特征）；
**隔离重跑该用例** `1 passed in 23.74s`；整套子集复跑：
```text
...........................                                              [100%]
27 passed in 178.74s
```
`tests/f06` 全量未重跑：共享 WSL/runsc 栈被其它 agent 的套件连续占用，且编排者明确要求不重跑全套
（见 §5 `not_run`）。

### 4.3 合并前 f04b 全量

```powershell
python -m pytest tests/f04b -q
```
```text
......................................                                   [100%]
38 passed in 153.16s (0:02:33)
```
（38 = T083 14 + T083 run-view 7 + T084/T086 等既有 + 本票 9；含 4.7 的两处 T083 run-view 测试修复。）

### 4.4 Go

```powershell
go build ./...; go vet ./...; go test ./...
```
合并后结果：`go build`/`go vet` 无输出（exit 0）；`go test ./...` 全部包 `ok`（无 `FAIL`），
`internal/presetexec` 含 T083/T086/T087 单测。`gofmt -l internal cmd tools` 空输出。
`python tools/sync_spec.py --check` → `no drift: spec copy and contract snapshot match the manifest`。

### 4.5 画布模块（Node，真实字节）

```powershell
node tests/f04b/canvas_f04b.test.mjs        # PASS 9 canvas F04B module tests
node tests/f04b/canvas_model.test.mjs       # PASS 4 canvas T086 model tests
node tests/f04b/t087_canvas.test.mjs        # ok 1..6（approval palette/config/round-trip/export allowlist）
python -m pytest tests/f04b/test_canvas_f04b_assets.py tests/f04b/test_t086_model_canvas.py tests/f04b/test_t087_canvas.py -q
```
```text
...........                                                              [100%]
11 passed in 2.11s
```

### 4.6 Go 单测（stub seam，明确标注）

`internal/presetexec/approval_test.go`（6 个用例，client/sandbox 为 fake seam，**非**生产验收）：

```text
--- PASS: TestGraphRunApprovalNodeParksAtTheServerApprovalWithTheFixedCheckpoint
--- PASS: TestGraphRunApprovedNodePublishesTheDecisionAndContinues
--- PASS: TestGraphRunRejectedNodeApprovalFailsWithoutPublishingOutput
--- PASS: TestGraphRunExpiredNodeApprovalFailsWithTheExpiredCode
--- PASS: TestGraphRunApprovalNodeRejectsAnInvalidConfigOrMissingProposal (missing_title/expiry_out_of_range/missing_proposal)
--- PASS: TestGraphRunApprovalNodeWithAChangedProposalAsksAgain
ok  orchara/platform/internal/presetexec
```

### 4.7 合并后门禁（本票分支 HEAD）

- `go build ./...`、`go vet ./...`、`go test ./...`：全部通过；`gofmt -l internal cmd tools` 空。
- `python tools/sync_spec.py --check`：no drift。
- 画布：上述 3 个 Node 模块测试 + 11 个画布 pytest 全部通过（合并后重跑）。
- 合并后真实栈（短锁窗口，只跑合并最相关的 3 个用例：服务端幂等、批准恢复、两次审批）：

```powershell
python -m pytest tests/f04b/test_t087_approval_node.py::test_node_approval_is_a_server_built_business_decision_and_replays_idempotently \
  tests/f04b/test_t087_approval_node.py::test_approved_node_approval_resumes_the_same_run_once \
  tests/f04b/test_t087_approval_node.py::test_two_approvals_wait_twice_and_converge_on_one_run -q
```
```text
...                                                                      [100%]
3 passed in 58.54s
```

- **合并后全量 `tests/f04b` / `tests/f06` 未重跑**：合并窗口内全局 pytest 锁被 T086/T088/T089/merger 的真实栈
  套件连续占用；按编排者指示不重跑长套件，合并后以关键 3 用例真实栈 + Go 全量 + 画布全量收口。

## 5. stub / real 与边界（not_run / partial）

- **real**：PostgreSQL 16 隔离库与真实迁移/seed；编译 `cmd/api`、`cmd/supervisor`、`cmd/maintenance`；真实
  mTLS internal listener（worker/supervisor 两种角色）；真实对象目录与封存 proposal artifact；真实 admin
  connector registry 与 capability proxy；真实审批决策 API；真实 F03/T035 waiting/resume（`ConfirmExit` →
  `maybeEnqueueResumeTx`）；owner-only artifact/SSE 读取；两次真实 `exec --once` 恢复同一 Run。
- **stub**：仅 `internal/presetexec/approval_test.go` 的 client/sandbox seam（已单列）。本票真实栈用例没有
  mocked 的审批/决定/resume/写授权。
- **not_run**：
  - 全量 `pytest tests/f06`（共享栈争用，见 §4.2；审批/等待相关子集 27 passed）。
  - 真实浏览器 DOM：画布/审批页逻辑用 Node 执行真实字节（无浏览器驱动，环境现状）。
  - 审批后的写工具“未知结果/needs_reconciliation”语义（spec 归 EXECUTION §5/T051，不在本票切片）。
  - 真实厂商模型（本票无模型节点，且本机无凭据）。
- **partial**：无。票面“审批过期后终止”由 §7 的自动 sweeper + 两条真实栈路径补齐；此前记录的
  `partial` 是当时没有后台扫描，现已关闭。其余票面项 `passed`。

## 6. 变更文件

新增：
- `internal/approvals/node.go`
- `internal/presetexec/approval.go`、`approval_test.go`
- `tests/support/t087.py`
- `tests/f04b/test_t087_approval_node.py`、`tests/f04b/test_t087_canvas.py`、`tests/f04b/t087_canvas.test.mjs`
- `reports/f04b/T087-approval-node.md`（本文件）

修改：
- `internal/approvals/{service.go,query.go,decision.go}`（`operation_id` NULL 支持、`NodeKey`、node approval 决定路径）
- `internal/execution/{events.go,approval_bridge.go}`（`approval.required` 事件；`approval:<id>` 等待桥）
- `internal/api/{approvals.go,internal.go}`（node-approvals worker 路由）
- `cmd/supervisor/client.go`（`PrepareNodeApproval` worker mTLS 实现）
- `internal/presetexec/{presetexec.go,graphplan.go,graphrun.go,graphplan_test.go,presetexec_test.go}`
  （Client 接口、approval 类型/触发/计划/配置复验、fake seam）
- `apps/web/canvas/src/{lib/dsl.js,lib/export.js,main.js,canvas.css}` + 重建
  `apps/web/src/assets/{canvas.js,canvas.css}`
- `apps/web/src/assets/approvals.js`（节点审批 title/节点/来源与确认文案）
- `tests/f04b/test_t083_runview.py`（§2.7 的两处既有测试缺陷修复）
- `PROGRESS.md`

无迁移；无 `docs/spec/**`、`contracts/**` 改动（`sync_spec --check` no drift）。

## 7. 第三轮复核修复（2026-11-06）

第三轮复核指出 T087 仍缺“到期后自动终止”的真实路径。本轮补 `internal/approvals/expiry.go`、
`cmd/api` 定时器与配置，并把“真实进程重放后的 join-once”落在同一真实栈套件：

- `test_a_parked_approval_expires_automatically_without_a_decision`：真实 supervisor 停在
  `waiting_approval`，把 deadline 置为过去后不调用 decision；API 的 100ms sweeper 把 approval 置
  `expired`、Run 置 `failed approval_expired`，waiting decision 为 `expired`，无 resume job/output；
  事后人工决定仍 409 `approval_expired`。
- `test_a_real_resume_reuses_the_join_and_never_fires_it_twice`：`start -> left/right(python) -> join ->
  approve1 -> end`；第一次 exec 真实跑两个 runsc 分支并发布 1 条 join data，停在审批；第二次 exec
  恢复后 join 只发一条复用 `succeeded`、join data artifact 逐字段不变、end 使用同一封套；join 每
  attempt 至多一次 succeeded。
- **复审 P1（过期与交接非原子）**：`expireOne` 现在在审批行置 `expired` 后、提交前调用
  `execution.ResolveExpiredNodeApprovalTx`，在同事务内写 waiting decision 并驱动 resume/fail；任何一步
  失败回滚整个过期，下一轮重试。真实故障注入用例
  `test_an_expiry_handoff_failure_rolls_back_and_is_retried`：等待更新触发器报错时 approval 仍
  `pending`、Run 仍 waiting、decision 为空；移除故障后下一轮原子完成 expired/failed。详见
  `reports/review/REVIEW3-FIXES.md` §6。
- `tests/f04b/test_t087_approval_node.py` 本文件全量 **11 passed in 87.78s**（最终树；含自动过期、
  故障回滚重试、真实 join 重放）。
- Go：`TestWaitingReferencesApproval`（checkpoint 归属）、config 的
  `TestApprovalSweepIntervalDefaultsAndBounds`；`go test ./...` 全绿。
- 全量 `pytest tests/f04b`：**95 passed in 577.68s (0:09:37)**（最终树；含复审 P1
  故障回滚重试用例）。
