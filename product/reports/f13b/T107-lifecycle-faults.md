# T107（GitHub #108）故障注入：旧图、活旧进程、取消与事件竞争（FAULT-04/05/06/10）

状态：**passed（真实栈证据）**。四条故障都打到真实产品协议/持久层：真实编译 manifest、
真实 PostgreSQL 行与事务、真实对象字节、真实 `cmd/supervisor exec --once` 进程及其
`pg_stat_activity` 会话、真实 cancel/claim/events/waiting-resolution HTTP(mTLS) 调用。
模型仍只有 `ORCHARA_PROFILE=dev-stub`；真实供应商、生产部署升级、网络分区与浏览器 UI
见 §7 `not_run`。

- 分支 / worktree：`t/t107-lifecycle-faults` / `E:\Orchara-wt\t107`
- 基线：`integration/f13b` @ `834ca68`；结束前 merge `integration/f13b` @ `f234e72`（T105），
  合并提交 `db94214`，其上重跑全部门禁；证据生成树 `e904e0c`（含一条仅改证据字段的 test 提交）。
- 环境：PostgreSQL 16.4 `127.0.0.1:55432`；Go 1.27.3；Python 3.12.2；
  WSL2 Ubuntu-24.04 + runsc release-20241118.0（rootfs sha256 `03c3f647…`）；模型 dev-stub。
- 交付：
  - `product/tests/f13b/test_t107_lifecycle_faults.py`（4 条真实栈故障用例）
  - `product/tests/support/t107.py`（故障证据记录器 + Windows 真实进程探针 + `pg_stat_activity` 只读模型）
  - `product/reports/f13b/T107-faults.json`（`F13B_WRITE_EVIDENCE=1` 生成，4 条 fault 记录）
  - 本文件；原始 tail 在 `E:\Orchara\.scratch\f13b\T107\`
- 未修改 `docs/spec/**`、`contracts/**`、迁移与既有票测试断言；未改产品 Go/Python 代码。

## 1. 结论（先读）

1. **FAULT-04 passed**：v1 Run 的封存 manifest 与仓库 checked-in golden 字节逐字节相同
   （sha256 `9cc418d1…`）。经真实 submit/review/approve 事务发布 v1、v2（v2 sha256
   `eac3a5db…`）后，v1 Run 两次领取/恢复都返回 **v1** 的 manifest sha、signed URL 字节与
   execution snapshot；删掉 v1 制品对象后 `runs recover` 明确拒绝 `artifact_unavailable`，
   Run 保持 `recovery_pending`，v2 对象完好但从未被回退使用。
2. **FAULT-05 passed**：真实 `cmd/supervisor exec --once`（pid 64356）真实领取 attempt
   （token 前缀 `8420f63a`）；OS 级 `NtSuspendProcess` 冻结后，其真实 PostgreSQL backend
   （pid 50836，`application_name=t107-old-worker-db-070d38e8bc`）仍在 `pg_stat_activity`。
   真实租约过期 + `sweep-leases --grace 1s` → `recovery_pending(lease_expired)`、job `held`、
   allocation 未释放、claim 204、attempt 仍只有 1 个。恢复进程后它的 exit confirmation 被
   真实 `409 stale_attempt` 拒绝（exec report `status=failed`），Run 仍 parked、事件数不变；
   等进程退出并留下真实退出证据后才 `recover` → `queued`，再次 claim 才产生第 2 个 attempt。
3. **FAULT-06 passed**：真实 cancel 后终态 `cancelled`（revision 4）在迟到 result/重复
   result/旧 event 重放/新 event/重复 claim/同 evidence exit/新上传/新 capability/外部
   owner 读/终态 recover 之后**完全不变**：seq 1..5 连续，无新事件、无新 attempt、
   capability operations=0、run artifacts 不增。
4. **FAULT-10 passed**：同 event_id 重放（含 batch 重放）不消耗 seq；真实终态 failed 后
   迟到事件 409 `run_terminal`，seq 1..6 连续。两个 supervisor mTLS 会话 barrier 并发
   approve/reject：恰一个 200、一个 `409 decision_conflict`，DB 只有 1 条决定（本轮
   approved/`t107-race-approve`）；exit 后恰好 1 个 resume job，相反决定重放仍 409。

## 2. 逐 AC / 票面要求

| 票面（issue #108） | 结论 | 证据 |
|---|---|---|
| FAULT-04：发布 v2 后恢复 v1，加载 v1 镜像/图；不存在则明确拒绝 | **passed** | 两次 claim 的 `manifest_sha256`/manifest URL 字节/snapshot 全为 v1（`9cc418d1…`）；删除 v1 对象后 `artifact_unavailable`，Run 保持 `recovery_pending`，v2 对象仍完好 |
| FAULT-05：租约过期但旧 worker 仍存活 → recovery_pending，无第二执行 | **passed** | 真实 supervisor pid + 真实 PG backend 在 parked 时仍存活；claim 204、attempt 1 个；旧进程 exit 被 `409 stale_attempt` 拒绝；旧进程退出后才 recover/新 attempt |
| FAULT-06：取消后迟到事件/队列重投 → 不复活、不重发新调用 | **passed** | 11 种迟到投递全拒绝/幂等，terminal row 与 events 逐字节不变；seq 1..5 连续；capability ops=0 |
| FAULT-10：重复事件/审批并发相反决定 → 一次提交，seq 连续且终态单调 | **passed** | 并发决定 [200,409 `decision_conflict`]，DB 1 条；重复 event 不增 seq，终态 failed 后 409 `run_terminal`；事件 seq 1..6/1..4 连续、无倒退 |
| 负边界：未授权主体读/决定 404、已终态后恢复拒绝码 | **passed** | 外部 owner 读 404；worker 证书决定 403 `forbidden`；未知 run 决定 404 `run_not_found`；终态 Run `recover` → `not_recovery_pending` |

## 3. FAULT-04：发布 v2 后恢复 v1

**注入点。** v1 debug Run 在 v2 存在之前创建（真实 `debugDraft` 编译，snapshot
`6c228602-d753-4220-b6e3-9013687d248f`，manifest artifact
`a98e116c-ee42-408d-a19f-50017d7aefeb`）；随后通过真实 `POST /applications/{id}/submit`
+ `POST /reviews/{id}/decision` 发布 v1（version `de908b2d-…`）与 v2
（version `30f4bb78-…`，v2 manifest artifact `d9a91cd9-…`）；最后从真实对象目录删除
v1 的 sealed manifest 对象，再执行 `orchara-admin runs recover`。

**输入 / hash。**
v1 封存字节 == `tests/snapshots/sales/manifest.jcs`，sha256
`9cc418d167377e667c77284ce7b35819c7374c0459ec90e41f4131719aef5686`；
v2 sha256 `eac3a5dbb1a09dfbf69510b7edc609363f98af0a10d8274b196889c5634ecf10`。
Run `079aad75-7fe9-48b7-89af-ee10c656231f` 的 `runs.manifest_sha256` 与
execution snapshot 均为 v1。

**独立真值。** 仓库 checked-in golden 字节与 sha；`platform.runs` /
`platform.execution_snapshots` / `platform.application_versions` 原始行；对象目录字节的
SHA-256 重算；`runs recover` 的 JSON refusal。

**预期 vs 实际。**

| 步骤 | 预期 | 实际 |
|---|---|---|
| 第一次 claim | manifest sha/URL 字节/snapshot = v1，不是 v2 | `9cc418d1…` / `9cc418d1…` / `6c228602-…`；v2 字节 `eac3a5db…` 未出现 |
| lease 过期 + sweep | `recovery_pending` | parked=1，state=`recovery_pending` |
| 带证据 recover #1 | `recovered`、manifest_artifact check passed | 10 项 checks 全 passed，state=`queued` |
| 第二次 claim | 新 attempt 仍加载 v1 字节 | manifest sha/字节仍 v1，token 变化 |
| 删除 v1 对象 + recover #2 | 明确拒绝，不能回退 v2 | `refused` / `artifact_unavailable`，state 仍 `recovery_pending`，evidence 行数不增，v2 对象仍完整 |

## 4. FAULT-05：活旧 supervisor + 过期租约

**注入点。** 真实 `cmd/supervisor exec --once`（`--execution-unit-id
t107-old-worker-c33c28fcd6`）领取真实 attempt（token 前缀 `8420f63a`）后，测试用
Windows `NtSuspendProcess` 冻结该真实进程（心跳停止，进程与 PG 会话仍在）；随后
`UPDATE platform.run_attempts SET lease_deadline = now() - 120s`（等价于租约到期/时钟前跳），
并由真实 `orchara-admin runs sweep-leases --grace 1s` 执行 park；最后 `NtResumeProcess`
让旧进程继续跑，它的下一次 heartbeat/exit 即真实“旧进程继续写”。

**独立真值。**
- 进程：pid 64356，`OpenProcess`+`GetExitCodeProcess == STILL_ACTIVE`，creation time
  `1794050156.718189` 前后一致；恢复后进程自行退出。
- 会话：`pg_stat_activity` backend pid 50836，`application_name=t107-old-worker-db-070d38e8bc`，
  `backend_start=2026-11-07T11:15:56.878350Z`；parked 时仍在，进程退出后为空。
- `sweep-leases` JSON：`scanned=1, parked=1, action=parked`，并注明“不 requeue、不换 token、
  不产生第二执行器”。
- 旧进程自己的 exec report：`status=failed`，错误为
  “the attempt lease was lost while the sandbox ran: … internal API 409 stale_attempt:
  the attempt token does not own a live lease on this run”。
- 独立 heartbeat 复证：同一旧 token → `409 stale_attempt`。

**预期 vs 实际。** parked 后 state=`recovery_pending`、reason=`lease_expired`、job=`held`、
allocation 未释放、attempt 仍 live 且只有 1 个；claim 返回 204（无第二执行）。旧进程
存活期间无新事件（7→8 只多出 sweep 的 recovery_pending 投影）；其写入被 409 拒绝；
进程退出后才 recover → `queued`，再 claim 才出现 attempt #2。

## 5. FAULT-06：取消后的所有迟到投递

**注入点。** 真实 `POST /api/v1/runs/{id}/cancel`（running attempt）→ `cancelling`；
真实 supervisor exit 提交 `cancelled`（revision 4、seq 1..5）；随后逐项重放：
迟到 result、同一 result body 重投、取消前 event（`cfcad160-…`, seq 3）重放、新 event、
重复 claim（队列重投）、同 evidence exit、不同 evidence exit、新 artifact upload、
新 model capability、外部 owner 读、对终态 Run 执行 `runs recover`。

**预期 vs 实际。** 终态 Run 行、revision、`next_event_seq`、5 条事件在全部投递前后逐字段
相同；状态链 `queued→running→cancelling→cancelled`。

| 迟到投递 | 实际 |
|---|---|
| 新 result / 同 result 重投 | 409 `run_terminal` / 409 `run_terminal` |
| 旧 event 重放 / 新 event | 409 `run_terminal` / 409 `run_terminal` |
| 重复 claim（队列重投） | 204，无 attempt |
| 同 evidence exit / 不同 evidence | 200 幂等 / 409 `attempt_evidence_conflict` |
| 新上传 / 新 capability | 409 `stale_attempt` / 403 `stale_attempt`；capability operations=0 |
| 外部 owner 读 / 终态 recover | 404 `resource_not_found` / exit 1 `not_recovery_pending` |

取消意图时间 `2026-11-07 19:16:19.212252+08`；终态 finished_at
`19:16:19.500006+08`；attempt 数 1；quota used=0。

## 6. FAULT-10：重复事件 + 并发相反决定

**注入点。** 一个真实 running Run 上提交 node.status `7904d511-…`，用同一 event_id 重放
（含 batch 重放），再真实 failed 终态后重放；另一个 Run 停在真实 `waiting_approval`，两个
独立 supervisor mTLS 连接用 `threading.Barrier` 并发提交 approved（`t107-race-approve`）
与 rejected（`t107-race-reject`）。端点：`POST /internal/v1/runs/{id}/events` 与
`POST /internal/v1/runs/{id}/waiting-resolution`。

**独立真值。** `platform.run_waitings`（run `c106d1f7-…`）：只有 1 行；decision
`{"kind":"approval","outcome":"approved","reference":"t107-race-approve"}`，
`decision_at=19:16:31.268719+08`；exit 后 `resume_job_id=b68a60e4-…`、
resume artifact `f5b38ca0-…`，`resume_jobs` 恰 1 个。`platform.run_events`：
event Run seq 1..6，终态 `failed`；waiting Run seq 1..4（`queued→running→waiting_approval→queued`）。

**预期 vs 实际。**
- 并发决定：[200, 409]；loser 稳定码 `decision_conflict`；DB 决定 1 条，重放赢家 200、
  重放相反决定 409，决定与 decision_at 不被改写。
- 重复事件：同 id 重放不新增行/不消耗 seq；终态后重放 → 409 `run_terminal`，seq/row 不变。
- 终态单调：`queued→running→running→failed` / `queued→running→waiting_approval→queued`
  均无回退；terminal 之后不再出现任何非 terminal 状态。
- 负边界：worker 证书决定 403 `forbidden`；未知 run 404 `run_not_found`；跨 owner 读 404。

> 说明：本批次没有真实外部写 connector；这里的“决定 → 外写”按平台当前语义核对为
> **恰好一次 resume 作业/一次恢复**（拒绝决定同样生成用于让图得知拒绝的唯一 resume），
> 不宣称真实供应商写次数。外部写审批属 T106 范围。

## 7. 真实 vs stub / 未运行

| 层次 | 是 | 不是 |
|---|---|---|
| stack | 一次性 PostgreSQL 16.4（真实迁移链）、编译 `cmd/api`、真实对象目录、HMAC signed link、真实 mTLS、真实 `cmd/admin` / `cmd/maintenance` / `cmd/supervisor` | 不是生产部署；不是共享长期数据库 |
| manifest / 对象 | 真实 compiler 封存字节（v1 == checked-in golden JCS） | 不是手写 manifest |
| FAULT-05 进程 | 真实 OS 进程 + 真实 PG backend + 真实 WSL2/runsc sandbox（rootfs digest 有记录） | “失联”注入是 OS suspend，不是网络分区/真机关机 |
| 审批 | 真实 waiting 行/决定 CAS/唯一 resume；两个真实 mTLS 连接 | T035 文档化非契约 bridge，不是 F06 审批 UI |
| v2 发布 | 真实 submit/review/approve 事务、真实 v2 manifest/version/review 行 | 五类评测报告用 T068 既有 fixture（真实 evaluation/case run 行 + seeded 终态），非真实 runner 执行 |
| 模型 | dev-stub | 真实供应商质量/延迟/费用 |

`not_run`：真实厂商模型与真实外部写供应商；生产部署上的 v1→v2 升级恢复；网络分区/
宿主机失联下的恢复（本票用 OS 挂起真实进程）；浏览器 DOM/SSE UI；多机 supervisor 竞争。

## 8. 命令与原始 tail（cwd=`product/`）

真实栈互斥：`E:\Orchara\.scratch\f13b\locks\realstack\t107.lock`（运行前创建，结束后删除）。
以下 tail 为 merge 后树 `db94214` 上的门禁；证据最后一轮在 `e904e0c` 树上重跑 4 条用例。

```text
$ python -m pytest tests/f13b/test_t107_lifecycle_faults.py -q
....                                                                     [100%]
4 passed in 76.85s (0:01:16)

$ python -m pytest tests/f13b -q
........................................................................ [ 39%]
........................................................................ [ 78%]
.......................................                                  [100%]
183 passed in 192.65s (0:03:12)

$ python -m pytest tests/f12 -q
........................................................................ [ 48%]
........................................................................ [ 64%]
........................................................................ [ 80%]
........................................................................ [ 96%]
...............                                                          [100%]
447 passed in 374.23s (0:06:14)

$ go build ./...            # exit 0（无输出）
$ go vet ./...              # exit 0（无输出）
$ go test ./...             # exit 0；39 ok / 0 FAIL / 12 no test files
$ gofmt.exe -l internal cmd tools   # 空，exit 0
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

证据最后一轮（`F13B_WRITE_EVIDENCE=1`，HEAD `e904e0c`）：

```text
$ python -m pytest tests/f13b/test_t107_lifecycle_faults.py -q
....                                                                     [100%]
4 passed in 59.60s
```

合并：`git merge integration/f13b` → merge commit `db94214`（无冲突；PROGRESS 双段未冲突）。

## 9. 残余风险 / 边界

- FAULT-05 的“旧进程活着”证据来自 OS suspend + 同一 PID/creation time + 真实 PG backend；
  它不等价于“宿主失联”，也不证明跨机场景；产品规则本身仍是“无法证明旧单元退出就保持
  `recovery_pending`”，本票没有放宽该规则。
- `sweep-leases`/`recover` 是运维 CLI（T038 已文档化），不是公开 HTTP；FAULT-04/05 的
  拒绝码来自 CLI JSON。`X-Run-Recovery-*` 头未在本票重复断言。
- FAULT-04 的 v2 发布使用 T068 既有“真实 review 事务 + seeded 终态 case 报告”fixture；
  manifest/version/review/approve 行与对象字节真实，但 case 执行不是本票证据。
- 并发决定只跑了 2 个连接/一轮（barrier）；未做多轮压力重复。T032/T035 已有单测与重复
  幂等覆盖，本票补充的是并发相反决定与真实终态后重放。
- 事件 seq 断言是环境内单次运行的数据库事实；没有跨进程长期 soak。
