# T106 / GitHub #107 故障注入：审批重启、换参、写后丢回执

- **状态：`passed`**（真实栈上 FAULT-01/02/03 三个故障全部注入并通过；真实供应商/真实模型 `not_run`）
- 分支：`t/t106-approval-faults`；worktree `E:\Orchara-wt\t106`
- 基线：`integration/f13b` @ `c132e8e`；完成前依次 merge 推进后的集成 tip：
  `f234e72`（T104/T105）→ `cb20f85`；`1ce0d65`（T107 + 已合并的 T106）→ `18e2bd4`；
  `d593c1e`（T107 最终证据/环境，仅 docs）→ `7edd475`。`PROGRESS.md` 冲突保留
  T104/T105/T106/T107 四节。
- 证据生成时的 HEAD：`cb20f85`（证据 JSON 的 `product_commit` 字段即此值；其后只追加了 T107 的测试/文档、
  两次 docs-only merge 与本票文档，T106 代码未变）
- 最终代码树与门禁树一致：`git diff 18e2bd4..7edd475 -- product/tests product/internal product/cmd
  product/tools product/db product/contracts` 为空；f13b/f12 的 186/447 全绿结果适用于最终代码树。
- 交付（全部为新增文件，未改动 `docs/spec/**`、`contracts/**`、迁移或既有票测试断言）：
  - `product/tests/f13b/test_t106_approval_faults.py`：3 条真实栈测试（FAULT-01/02/03），共 53 条断言
  - `product/tests/f13b/fault_support.py`：`Fault` 记录（注入点/输入/预期/实际/独立账本/命令）、
    真实进程树 kill / `tasklist` / `pg_stat_activity` 真值、`F13B_WRITE_EVIDENCE=1` 才写回的证据 writer
  - `product/tests/f13b/t106_worker.py`：FAULT-01 的真实 worker OS 进程（真实 mTLS capability、
    waiting/exit 协议、真实 PostgreSQL 会话）
  - `product/reports/f13b/T106-faults.json`：机器可读证据（3 个故障记录 + input/seed/hash + 独立账本）
  - 本报告 + `product/PROGRESS.md` 小节
- 原始日志：`E:\Orchara\.scratch\f13b\T106\`（`gate-f13b.log`、`gate-f12.log`、`gate-go-*.log`、
  `gate-gofmt.log`、`gate-sync-spec.log`、`t106-targeted.log`、`evidence-run.log`）

## 1. 环境与 stub/real 分界

| 项 | 实际使用 | 说明 |
|---|---|---|
| PostgreSQL | **16.4** `127.0.0.1:55432` | 每个 stack 一条一次性数据库；真实 migrate 全链 + bootstrap |
| API | **编译后的 `cmd/api`** | 真实内部 mTLS 监听、真实 object store、HMAC sealed links |
| supervisor | **编译后的 `cmd/supervisor exec --once`** | 真实一次性 OS 进程（生产同形态；非常驻 daemon） |
| worker | **真实 OS 子进程** | `tests/f13b/t106_worker.py` 通过真实 mTLS 调 capability/waiting/exit |
| connector 端点 | **真实 loopback HTTP server** | 真实 TCP 连接；端点自己的请求日志是独立外部账本 |
| 模型 | `ORCHARA_PROFILE=dev-stub` | 本票不需要模型；无厂商凭据/端点 |
| 沙箱 | 未使用 | 本票不跑图节点，不需要 WSL2/runsc |

**worker 角色边界（显式披露，不是隐藏的 mock）**：产品图执行器没有 connector 节点（T101 J6b 已记录），
因此 connector capability 调用只能由 worker mTLS 角色发出（T048/T050/T051 的同一文档化 seam）。
本票用一个真实 OS worker 进程执行该角色；egress guard、审批服务、capability 服务、数据库行、HTTP 连接
全部是真实产品代码，没有任何返回值 mock。

**`not_run`**：真实第三方 connector（回环 HTTP 只是部署端点的替身）、真实厂商模型（dev-stub，无凭据）、
真实浏览器 DOM、常驻 supervisor daemon（只用生产 one-shot `exec --once`）。

## 2. 三个真实注入点（一页）

| 故障 | 注入位置（进程/行/连接） | 触发方式 |
|---|---|---|
| FAULT-01 | 真实 worker OS 进程 `tests/f13b/t106_worker.py`（已提交 waiting + exit，run=`waiting_approval`，approval=`pending`）；`tests/f13b/fault_support.py::kill_process_tree` → Windows `taskkill /F /T` 杀掉进程树（Python worker + 其空闲 psql 会话） | 真杀进程；随后启动真实 `cmd/supervisor exec --once`，人工批准后新 worker 进程 claim 唯一 resume job 并用**同一 operation id / logical key / params** 重放 |
| FAULT-02 | `internal/capabilities/service.go:689-700`（`prepareOperation` 比较已存 operation 的 `params_sha256` 与新规范摘要 → 409 `operation_conflict`）；`internal/approvals/decision.go:148-151`（决定携带的 `params_sha256` ≠ approval → 409 `approval_params_mismatch`） | 真实 mTLS capability 调用 + owner decision HTTP；换参重放/错 hash 决定/陌生人/admin/取消后迟到决定 |
| FAULT-03 | `tests/support/t051.py:39-70` 的 `_DropHandler._handle`：读完完整请求体（端点真的收到并应用写）后 `socket.shutdown(SHUT_RDWR)`，不写响应；产品 `internal/capabilities/connectors.go:240-275` `dispatchConnector` 观察到传输错误 → `markConnectorUnknown`（`:559-616`） | 真实 HTTP 断连；重复 invoke 不重发；`POST /api/v1/operations/{id}/reconcile` + 真实 evidence artifact 结案 |

## 3. FAULT-01：审批等待时杀掉真实 worker，重启后原审批恢复、operation id 不变

### 3.1 注入点与步骤

1. 真实 worker A（`python tests/f13b/t106_worker.py prepare <spec>`）claim queued start job，
   用真实 mTLS 发出一次 write-risk connector capability（固定 `operation_id`），得到
   `awaiting_approval`；提交 T035 `waiting_approval` 结果并 confirm exit（run 停在 `waiting_approval`，
   approval `pending`）；随后持有一条真实 PostgreSQL 会话（空闲 `psql`，`application_name=t106-fault01-worker-a`）。
2. **真杀**：`taskkill /F /T /PID <worker-a>`（记录 PID、OS 启动时间、taskkill 原始输出、Popen 退出码、
   `tasklist` 复查、`pg_stat_activity` 前后）。taskkill 输出明确列出被终止的子进程（持会话的 psql）。
3. 启动真实 `cmd/supervisor exec --once` 进程（同一 DB + mTLS API）：因为审批仍未决定、没有 resume job，
   它必须返回 `{"status":"idle"}` 并退出 0；同时断言 run/approval/operation 不变、0 次外部写。
4. owner 以精确 `params_sha256` 批准原 approval → 恰 1 个 resume job、operation `authorized`。
5. 新 worker B（真实进程）claim 该 resume job，用**同一 operation id / logical key / params** 重放，
   写成功，提交 succeeded result + exit，run `succeeded`。

### 3.2 输入 / seed / hash

- seed：`t106-fault01`；logical key：`t106:fault01:write`；operation id 在注入前生成并在 A/B 两进程复用。
- params：`{"path": "/v1/orders", "body": {"amount": 1250, "currency": "CNY"}}`；
  规范字节与 sha256 由测试独立计算（`fault_support.canonical_params`），并断言与产品
  `platform.operations.params_sha256` 相等：`99287ef0aec379832ae14e371d7d9ddaffc4904d37e05bf6db58f02c3755a86e`。
- 证据 JSON 记录 policy id、`policy_sha256`、connector origin、run id、approval id。

### 3.3 独立账本（实际数字）

- **connector 端点自己的请求日志**：批准前 **0** 次；resume 后 **1** 次物理 `POST /v1/orders`
  （请求头中的 `x-connector-secret` 已脱敏），body `{"amount": 1250, "currency": "CNY"}`。
- **PostgreSQL**：`platform.operations` 只有 1 行且 id 恒为原 operation id，终态 `succeeded`；
  `platform.operation_attempts` 恰 1 行 `succeeded`；`platform.approvals` 只有 1 行，id/proposal/
  `params_sha256`/approver 全程不变，仅 `pending → approved`；`platform.jobs` 的 resume job 恰 1 个，
  终态 `completed`；`platform.runs` 终态 `succeeded`。
- **进程表**：worker A PID 64504 被 taskkill 后 `tasklist` 不再存在（Popen exit=1）；
  `pg_stat_activity` 中 `t106-fault01-worker-a` 会话由 1 条变为 0 条；真实 supervisor PID 66868 返回
  `status=idle`、exit 0；worker B PID 53328 退出码 0。

### 3.4 预注册预期 vs 实际

| 预期 | 实际 | 断言 |
|---|---|---|
| 被杀的进程消失、其 PG 会话消失 | taskkill 0、`present=false`、会话 1→0 | 2 |
| approval 行不被替换（同 id/proposal/hash/approver） | 同一行，仅状态变 approved | 3 |
| `platform.operations` 同一 operation id | 同一 id，一行，succeeded | 3 |
| 恰 1 个 resume job，且只在人工决定后出现 | 决定前 0，决定后 1，终态 completed | 3 |
| 批准前 0 次写、批准后恰 1 次 | 0 → 1 | 3 |
| 同一次运行（run succeeded、一次 attempt） | run succeeded，attempt 1 行 succeeded | 2 |

**裁决：`passed`**（20 条断言）。说明：真正被 kill 的是承载 connector capability 的 worker OS 进程；
真实 supervisor 进程在 kill 后被真实启动并正确 idle（它没有 connector 节点，不能替 worker 发写）。
这两件事在证据里分开记录，互不冒充。

## 4. FAULT-02：参数改动后旧批准不可复用（外加审批边界）

### 4.1 注入点与步骤

1. 精确 params 写入 parked → owner 以精确 `params_sha256` 批准 → confirm exit → 恰 1 个 resume job。
2. 用 resumed lease 以**同一 operation id / logical key、改动后的 params** 重放 →
   `409 operation_conflict`，0 次外部写，operation 仍 `authorized`，approval 行不变、attempt 未增加。
3. 同一 operation id 换 logical key + 改动 params → 同样 `409`，0 写（旧批准不能授权新调用）。
4. 用**原始精确 params** 重放 → `succeeded`，端点恰 1 次写（拒绝没有消耗批准）。
5. 第二个写操作（call-2）parked 后：
   - 错误 `params_sha256` 的决定 → `409 approval_params_mismatch`，approval 仍 pending，0 写；
   - 陌生人决定/读取 → `404`；admin 决定 → `403 approver_mismatch`，approval 仍 pending，0 写；
   - 取消 run → approval `invalidated`；迟到决定 → `409`，0 写。

### 4.2 输入 / hash

- 原 params sha256（独立计算 == 产品）：`99287ef0…55a86e`。
- 改动 params：`{"path": "/v1/orders", "body": {"amount": 999999, "currency": "CNY"}}`，
  独立 sha256 与原值不同（证据 JSON 同时记录两者规范字节与 hash）。

### 4.3 独立账本（实际数字）

- 端点请求日志：换参拒绝期间 **0**；全测试结束 **1**（仅精确参数那次）。
- PostgreSQL：`platform.operations` 两条（call-1 succeeded；call-2 awaiting_approval→cancel 后未写），
  call-1 attempts 恰 1 行 succeeded；`platform.approvals` 两行（call-1 approved，call-2 invalidated）；
  run 因显式 cancel 进入 `cancelling`。
- HTTP 错误码：`operation_conflict` / `approval_params_mismatch` / `404` / `403` / `409`。

### 4.4 边界断言（本票证据中重新出现）

- 陌生人：`GET /approvals/{id}` → 404；decision → 404（不泄露存在性）。
- admin：decision → 403 `approver_mismatch`（admin 不是指定审批人）。
- 取消后迟到决定：approval `invalidated`，decision → 409，0 次写。

**裁决：`passed`**（18 条断言）。

## 5. FAULT-03：写成功但回执丢失 → unknown，不自动补发，核对后用证据结案

### 5.1 注入点与步骤

1. T051 流程 parked → owner 批准 → resume lease；端点 `respond_drop()`。
2. 真实 capability 调用发出写；端点读完请求体（**写已发生**）后关闭 TCP、不回响应；产品记录
   `operations.state='unknown'`、`receipt_artifact_id IS NULL`、
   `last_error_code=connector_transport_error`、attempt 仍 `dispatching`、
   `run_unknown_operations` 未解决；capability 返回 `{state:"unknown"}`（**不是** succeeded/failed）。
3. 重复 invoke 同 operation/logical key → 仍 `unknown`，端点请求数保持 **1**（无自动重发）。
4. 同 key 换 params → `409 operation_conflict`，请求数仍 1。
5. worker 提交 succeeded result + 真实 output artifact 后 exit；unknown guard 仍把 run 停在
   `needs_reconciliation`（不是 succeeded）。
6. owner 用真实 evidence artifact + `POST /api/v1/operations/{id}/reconcile`
   （`confirmed_success`，Idempotency-Key）结案 → operation `succeeded`、receipt/evidence 为该 artifact、
   run `succeeded`；物理 attempt 仍 **1**、端点请求仍 **1**；同 key 重放 reconcile 幂等且不重发。

### 5.2 输入 / seed / hash

- seed：`t106-fault03`；params 同上，独立 sha256 与产品 `operations.params_sha256` 相等。
- 证据 artifact：真实 object bytes + `platform.artifacts` ready 行（`7a52ae39-bae8-4b14-859e-8173c65e6e53`）。

### 5.3 独立账本（实际数字）

- **端点自己的请求日志**：恰 **1** 次请求（写已应用），断连后无第二次。
- **PostgreSQL**：`platform.operations` 1 行 unknown→succeeded；
  `platform.operation_attempts` 恰 **1** 行（状态 `dispatching`，从不变成第二次尝试）；
  `run_unknown_operations.resolved_at` 在结案后非空；run 终态 `succeeded`；
  owner ledger 的 `attempts == 1`、`requires_reconciliation` 结案后为 false。
- 时间线：`run_at_unknown=running` → `run_parked=needs_reconciliation` → `run_final=succeeded`。

### 5.4 预期 vs 实际

| 预期 | 实际 |
|---|---|
| 端点收到 1 次写 | 1 次 |
| operation `unknown`、无 receipt | 是（receipt NULL，unknown 行未解决） |
| 重复 invoke 不重发 | 是（请求数保持 1） |
| unknown 期间 run 不是 succeeded | 是（running → needs_reconciliation） |
| evidence + reconcile 结案 succeeded | 是（receipt == evidence artifact） |
| 物理写仍为 1 | attempt 1 行、端点 1 次 |

**裁决：`passed`**（15 条断言）。

## 6. AC 对照（issue #107）

| AC | 证据 |
|---|---|
| AC1 三个故障的实际注入点和输入可复现 | §2 表格 + 每故障的 `injection_point` / `input`（seed、params、独立 hash、operation/logical key）+ `commands`（HTTP 调用、taskkill、supervisor 命令），证据 JSON 与原始日志可重放 |
| AC2 状态/进程/数据库与独立外部账本给出预期 | 每条故障都有 endpoint 请求日志、PG 行、进程表/`pg_stat_activity` 三类独立真值（§3.3/§4.3/§5.3） |
| AC3 严重越权和重复写为零，unknown 不冒充成功 | FAULT-02 陌生人 404/admin 403/迟到 409/换参 409 且全部 0 写；FAULT-03 unknown 期间 run 非 succeeded、重复 invoke 不重发、结案后仍 1 次写 |
| AC4 端到端演示 + 失败/授权/重试恢复边界断言，不以单层 mock 成功代替 | 真实进程 kill + 真实 supervisor 进程 + 真实 mTLS + 真实 PG + 真实 TCP 断连；53 条断言覆盖 kill/重启/审批/拒绝/恢复/核对 |
| AC5 版本/输入 hash/命令/原始结果/profile；stub/real 分报，缺条件 blocked、未运行 not_run | 证据 JSON 顶部 `environment`、`product_commit`、每故障 input/hash/commands/expected/observed/ledger；§1 与 §9 明确 stub/real 与 `not_run` |

## 7. 门禁（本票最终代码，真实 tail）

合并前（分支 `eb45cd7`）：

```text
$ python -m pytest tests/f13b/test_t106_approval_faults.py -q --basetemp=...   # F13B_WRITE_EVIDENCE=1
...                                                                      [100%]
3 passed in 51.53s

$ python -m pytest tests/f13b -q
...........................................                              [100%]
43 passed in 103.79s

$ python -m pytest tests/f12 -q
........................................................................ [ 16%]
........................................................................ [ 32%]
........................................................................ [ 48%]
........................................................................ [ 64%]
........................................................................ [ 80%]
........................................................................ [ 96%]
...............                                                          [100%]
447 passed in 385.14s (0:06:25)
```

合并 `integration/f13b`（`f234e72`，含 T104/T105）后（merge commit `cb20f85`）：

```text
$ python -m pytest tests/f13b -q
........................................................................ [ 39%]
........................................................................ [ 79%]
......................................                                   [100%]
182 passed in 148.45s (0:02:28)

$ python -m pytest tests/f12 -q
........................................................................ [ 16%]
........................................................................ [ 32%] ... [ 96%]
...............                                                          [100%]
447 passed in 488.01s (0:08:08)

$ python -m pytest tests/f13b/test_t106_approval_faults.py -q --basetemp=...   # 证据重生成
...                                                                      [100%]
3 passed in 64.16s
```

再 merge `integration/f13b` @ `1ce0d65`（T107 + 已合并的 T106）后（merge commit `18e2bd4`）：

```text
$ python -m pytest tests/f13b -q
........................................................................ [ 38%]
........................................................................ [ 77%]
..........................................                               [100%]
186 passed in 211.65s (0:03:31)

$ python -m pytest tests/f12 -q
........................................................................ [ 96%]
...............                                                          [100%]
447 passed in 311.84s (0:05:11)
```

最后一次 merge 的是集成 docs-only 变更（`d593c1e`：仅 `PROGRESS.md` + T107 报告）→ `7edd475`；
`git diff 18e2bd4..7edd475 -- product/tests product/internal product/cmd product/tools product/db
product/contracts` 为空，故真实栈结果不因该 merge 改变。宿主上当时 T108 正占用 realstack 锁，
未重复真实栈套件；在 `7edd475` 上复跑非真实栈门禁：

```text
$ go build ./...   # exit 0
$ go vet ./...     # exit 0
$ go test ./...    # exit 0
$ gofmt -l internal cmd tools   # 空输出
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

三次均：

```text
$ go build ./...          # exit 0（无输出）
$ go vet ./...            # exit 0（无输出）
$ go test ./...           # exit 0；全部包 ok / [no test files]
$ gofmt -l internal cmd tools   # 空输出，exit 0
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

证据重跑（`F13B_WRITE_EVIDENCE=1`）只在该显式环境下重写 `reports/f13b/T106-faults.json`；普通
pytest 运行不写。写入前用 `tools/evidence_redaction.find_violations` 扫描，当前 0 命中
（`x-connector-secret` 记录为 `<redacted>`）。

## 8. 剩余风险 / 未覆盖

1. **真实供应商/模型 `not_run`**：connector 端点是本机回环 HTTP，模型是 dev-stub；没有任何厂商凭据或端点。
   本票证明的是平台故障语义（审批绑定、进程/状态持久性、unknown 与核对），不是供应商行为。
2. **worker 角色 seam**：图执行器没有 connector 节点（T101 J6b），因此 connector capability 由真实 worker
   OS 进程发出（T048/T050/T051 同一 seam）。真实 supervisor 进程在 FAULT-01 中被真实重启并 idle；
   它本身不能代替 worker 发 connector 调用。该边界不写成"图自己触发写"。
3. **FAULT-01 的 DB 会话证据形态**：Windows 上 `client_connection_check_interval` 必须为 0，
   正在执行 `pg_sleep` 的 backend 不会察觉客户端断开；因此持会话的 psql 以**空闲**方式挂起，
   kill 进程树后 backend 立即看到 EOF。杀的是承载会话的真实 worker 进程树，`taskkill /T` 输出与
   `pg_stat_activity` 前后快照都在证据 JSON 里。
4. **常驻 supervisor**：产品入口是 one-shot `cmd/supervisor exec --once`；本票没有常驻 daemon 形态的
   故障注入（`not_run`，不冒充）。
5. **同宿主并发**：宿主上同期还有其它 F13B 真实栈任务；门禁耗时高于空载，但结果全绿且未使用锁外共享状态。
