# F03 T032 验收：断开事件流后从持久时间线继续查看运行

- 任务：F03 / GitHub Issue #33（票 `T032`，规划 ID F03.04），分支 `feat/f05-f03-run-and-tools`
- 状态：**`passed`**（本票交付范围 = **持久 `platform.run_events` + 每个已提交 Run 转换的同事务
  `run.status` 投影 + worker `node.status` 批量事件 + owner-only SSE 回放/去重/保留窗口/心跳/终态关闭 +
  运行页与调试页持久时间线**）。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 mTLS 内部客户端
  （worker/supervisor/maintenance 证书）+ 真实 HTTP/SSE 连接（含中途断开与 Last-Event-ID 重连）+
  Node 真实执行页面逻辑；无 mock、无跳过。
- 主要文件：
  - `db/migrations/0018_run_events.sql`（新表/索引/CHECK/权限墙）
  - `internal/execution/events.go`（`AppendRunStatusTx`、worker 事件校验/提交、
    `ListEventsAfter`/`EventReplayFacts`、事件封套）
  - `internal/execution/{execution.go,service.go,service_test.go}`（claim/result/finalizer/requeue/park 同事务投影；
    Service.Logger）
  - `internal/api/events.go`（`submitWorkerEvents` + SSE）、`internal/api/{server.go,internal.go,middleware.go}`
  - `internal/config/config.go`（三个 `ORCHARA_RUN_EVENT_*` 部署 knob）
  - `internal/catalog/debug.go`（debug Run 创建事务写第一条 `queued` 事件）
  - `apps/web/src/{runs.html,assets/runs.js,debug.html,assets/debug.js}`（持久时间线/去重/重连/410 快照/无伪造预览）
  - `tests/support/t032.py`、`tests/f03/test_t032_*.py`（4 文件 31 用例）
  - 既有迁移链尾断言 0017→0018（f00b/f02a/f05/f03 六处，属预期的链尾适配）
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`，
    `GOCACHE=E:\orchara-toolchains\gocache`、`GOMODCACHE=E:\orchara-toolchains\gomodcache`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`tools/pg_dev.py`，真实 `cmd/admin migrate`）
  - Python 3.12.2（`python -m pytest`）、Node 24.13.0（页面 JS 语法与行为真实执行）
  - profile `dev-stub`；对象目录在测试 `tmp_path`；部署输入沿用 T025 占位值
    `ORCHARA_WORKER_IMAGE_DIGEST=sha256:aaaa…(64)`、runtime lock =
    `probes/agent_min/requirements.lock.txt`（真实 SHA-256）
  - 事件流测试 knob：`ORCHARA_RUN_EVENT_RETENTION=1h`、
    `ORCHARA_RUN_EVENT_POLL_INTERVAL=20ms`、`ORCHARA_RUN_EVENT_HEARTBEAT_INTERVAL=100ms`；
    410 用例另起一个同库 API 进程，`ORCHARA_RUN_EVENT_RETENTION=50ms`
  - 冻结的 golden 执行 hash（与 T025/T027/T029/T031 相同）：
    `9cc418d167377e667c77284ce7b35819c7374c0459ec90e41f4131719aef5686`
- 真实/桩分报：**全部 real**（PG/二进制/mTLS/HTTP/SSE/Node）。SQL 只用于读取断言、既有 T031
  output artifact 种子与两处故障注入 trigger；事件端点写入全部经真实 HTTP/mTLS。

## 0. 工作记录（简短）

1. 读 NOTES/brief/T032 票面、API §3/§4、EXECUTION §2、IMPLEMENTATION-DETAILS §7、STATE-MACHINES
   §1/§2/§6、`contracts/event.schema.json`/`openapi.json`/`database.sql`、`examples/event.json`、
   T031 报告与现有 execution/api/catalog 代码，确认 0017 为最新迁移、树干净（基线 `tests/f03` 68 passed）。
2. TDD 切片：先写迁移/事件端点用例（红：0018 缺失、`/events` 404）→ 实现迁移 + 同事务事件 +
   `submitWorkerEvents`（绿）；再写 SSE 用例（红：500/no frames）→ 实现 SSE（含 request-logging
   Flusher 转发）→ 绿；最后写页面用例（红：缺 marker/helper）→ 实现 fetch+ReadableStream 时间线 → 绿。
3. 迁移：`run_events` 契约基线 + 显式 replay/retention 索引 + 类型/JSON 对象/revision CHECK；
   worker/PUBLIC 权限墙与 0007–0017 一致。
4. 同事务：debug 创建、claim、proposal、finalizer（含 needs_reconciliation）、recovery_pending、
   started=false requeue 全部调用同一个 `AppendRunStatusTx`；事件插入失败用真实 PG trigger 证明业务
   转换回滚、无孤儿事件。
5. `submitWorkerEvents`：live token 绑定 run/job/lease；batch 1..100；只收 node.status 且 payload
   精确三键；event_id 幂等（同内容不占 seq，异内容/跨 run 409 `event_conflict`）；终态 409
   `run_terminal`。
6. SSE：owner-only、`Last-Event-ID` 回放、15s 注释心跳（测试 knob）、poll PostgreSQL、保留窗口
   410 + `details.snapshot_url`、回放终态后关闭；真实连接中途断开后带 Last-Event-ID 重连完成。
7. Web：runs/debug 页复用 `beginRunTimeline`；按 seq 去重排序、重连/终态文案、410 快照提示；
   `data-preview="not_run"` 明确模型 delta 属 F06，不渲染伪造正文。
8. 跑门禁（§3），写本报告/PROGRESS/NOTES，按 feat/test/docs 三次提交；未推送、未关 issue、
   未改 `docs/spec/` 与 `contracts/`、未改迁移 0001–0017。

## 1. 交付内容

### 1.1 迁移 0018

`platform.run_events`：`run_id uuid NOT NULL REFERENCES platform.runs(id)`、`seq bigint NOT NULL
CHECK(seq>0)`、`event_id uuid NOT NULL UNIQUE`、`type text NOT NULL`、`revision bigint NOT NULL
CHECK(revision>0)`、`payload jsonb NOT NULL`（CHECK 为 JSON 对象）、`created_at timestamptz NOT NULL
DEFAULT now()`、PK `(run_id,seq)`。

索引：`run_events_replay (run_id,seq)`（命名回放路径；PK 已提供同一顺序，见 §2.1）、
`run_events_created_at (created_at)`（保留窗口年龄扫描；实际清理属 F07）。事件类型限定契约五值：
`run.status`、`node.status`、`approval.required`、`artifact.ready`、`usage.updated`。
`orchara_worker`/PUBLIC 仍无 `platform` 权限。

### 1.2 同事务 run.status 投影

`execution.AppendRunStatusTx(ctx, tx, runID)` 是唯一控制器事件写入器：`SELECT … FROM platform.runs
FOR UPDATE` 读 state/error_code/revision/next_event_seq → 以新 uuid 与**转换后** revision 插入
`run.status` → `next_event_seq+1`。payload 恰好 `{state, error_code?}`（event.schema 的
`additionalProperties:false`）。

调用点（全部与业务转换同事务，失败即整体回滚）：

| 转换 | payload | revision |
|---|---|---|
| debug Run 创建（`catalog/debug.go`） | `{"state":"queued"}` | 1 |
| claim（T029） | `{"state":"running"}` | 2 |
| result 提议（T031） | `{"state":"running"}`（phase=finalizing 无事件字段） | 2（不变） |
| finalizer 终态 | `{"state":"succeeded"}` / `{"state":"failed","error_code":…}` | +1 |
| finalizer needs_reconciliation | `{"state":"needs_reconciliation"}`（+error_code） | +1 |
| exit 无结果恢复挂起 | `{"state":"recovery_pending"}` | +1 |
| started=false requeue | `{"state":"queued"}` | +1 |

result 提议只为**首次** proposal 发事件；幂等重放不再发；recovery_pending 上补 result 不改 phase，
不发 proposal 事件，由随后终态事件解释。waiting_* 在 T031 中不改变 Run state（T035 才迁移），
因此不伪造 waiting 事件。

### 1.3 `POST /internal/v1/runs/{run_id}/events`（submitWorkerEvents）

- 角色：`worker` 或 `supervisor`；`maintenance` 403。
- body：`{attempt_token, events:[{event_id,local_sequence,type,payload}]}`，严格解码
  （unknown/trailing → 400 `invalid_json`），batch 1..100。
- live token：必须属于该 run 的当前 attempt（`jobs.current_attempt_token` 同名、job leased、
  `exited_at/revoked_at IS NULL`、`lease_deadline > now()`）；跨 run/随机/过期 → 409 `stale_attempt`。
- 只接受 `node.status`；payload 精确 `{node_id:string, state:string, attempt_no:int>=1}`。
  content/body/delta/text 等额外键 → 422 `validation_failed` + `details.field=events[i].payload.<key>`；
  缺键/类型错误同样定位。`local_sequence` 必须 ≥1，但只作诊断：缺口/重复写进程日志，不落库、
  不参与展示顺序（请求数组顺序即提交顺序）。
- `event_id` 幂等：同 run + 同 type + JSONB 等值 payload → 200 no-op，不占 seq；异内容或同 id
  跨 run → 409 `event_conflict`；新事件按数组顺序各分配一个 seq（`next_event_seq`，Run 行锁）。
- 终态 run → 409 `run_terminal`（即使 token 已 revoke，先判 run 再判 token）；非执行态 →
  409 `run_state_conflict`。整批全有或全无。
- 200 返回契约 `Accepted {id: run_id, state: "accepted"}`。

### 1.4 `GET /api/v1/runs/{run_id}/events`（runEvents，SSE）

- owner-only，与 getRun 完全相同：他人/管理员/未知/已删除 → 404 `resource_not_found`；
  API key 需 `application:read`。
- `Last-Event-ID`（可选，`^\d+$`）：非法/重复 header → 400 `validation_failed` +
  `details.field=Last-Event-ID`；回放 `seq > Last-Event-ID`，按 seq 升序。
- 帧：`id: <seq>`、`event: <type>`、`data: <event.schema 封套>`；封套含
  `schema_version=1.0/event_id/run_id/seq/revision/type/created_at/payload`，payload 原样返回。
- 15s 空闲注释心跳 `: heartbeat`（部署 knob，默认 15s）；不增加业务 seq，无 id/event/data。
- 空闲轮询 PostgreSQL（本批无 Redis）；Redis 只可能是唤醒提示，不作为真实性来源。
- 回放边界事件早于保留窗口（默认 24h，`ORCHARA_RUN_EVENT_RETENTION`）→ 410
  `event_cursor_expired` + `details.snapshot_url=/api/v1/runs/{id}`；cursor 已在最新 seq 时不误报。
- 回放到终态并追平后关闭（不伪造“还在继续”）。
- 事件 payload 只有元数据；正文/内容绝不进入事件。

### 1.5 Web 时间线

- `runs.html`/`runs.js`：详情选择后 `beginRunTimeline` 用 `fetch` + `ReadableStream` 读 SSE
  （不用 EventSource，因而能观察 410 并显式发送 Last-Event-ID）。纯函数
  `runEventsPath/parseSSEFrame/parseSSEBuffer/runEventFromFrame/applyRunEvent/nextLastEventID/
  describeRunEvent/timelineTerminalState/streamFailure/streamRunTimeline` 全部由 Node 真实执行测试。
- 去重：同 seq 只渲染一次，乱序按 seq 排序；断线显示“按 Last-Event-ID=<n> 重连…”；410 显示
  snapshot_url 并停止；终态显示“已回放到已提交终态 <state>；事件流关闭”。
- `describeRunEvent` 只读 schema 允许的 state/error_code/node_id/state/attempt_no；payload 的
  content/body 永不渲染。running 事件明确“finalizing 尚未成功”，阶段仍以 `GET /runs/{id}` 为准。
- `debug.html`/`debug.js` 加载同一 `runs.js` 并复用 `beginRunTimeline`。
- 两页都有 `data-preview="not_run"`：模型流式 delta 预览属 F06，本票不实现、不伪造预览正文。

### 1.6 测试

`tests/support/t032.py` + `tests/f03/test_t032_*.py` **4 文件 31 用例**：

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `test_t032_migration.py` | 6 | 0018 链尾/重放；列/类型/PK/UNIQUE/FK；类型/payload/revision/seq 约束；replay+retention 索引；worker/PUBLIC 权限墙 |
| `test_t032_events.py` | 13 | debug 创建/claim/result/finalizer/requeue/park/needs_reconciliation 每转换恰一事件且 payload 正确；node.status 接受；重复 id 不占 seq（计数不变/一行）；同 id 异 payload 409；跨 run 同 id 409；批量连续 seq + 请求顺序；local_sequence 仅诊断；空批/超 100/坏 id/local_seq/type/payload 额外键全部 422 定位；live token/角色/404；两 run seq 独立；终态拒迟到事件；PG trigger 证明事件失败回滚 claim、debug 创建在 job 步失败回滚 run+事件 |
| `test_t032_sse.py` | 7 | 封套/Content-Type/Cache-Control；Last-Event-ID 只回放更新帧；终态后 EOF 且 id 单调；心跳是注释且不占 seq；cursor 在最新不 410；50ms 窗口 410 + snapshot_url（同库第二 API 进程）；owner-only/非法 header/404；真实流式中途断开 + Last-Event-ID 重连 + 终态收尾 |
| `test_t032_pages.py` | 5 | `/runs` 时间线 marker/预览 not_run；`runs.js` helper marker；`/debug` 复用时间线；页面/资产 Node 语法；Node 行为（分块帧重组、注释、seq 去重/排序、终态、410/404/500 状态、payload 正文不渲染） |

T029/T031 行为未削弱：既有 68 用例全绿，新增事件断言只读取新增行。六处既有迁移链尾断言按惯例
0017→0018 适配（f00b T026、f02a T023/T025/T027、f05 T028/T030、f03 T029）。

## 2. 契约缺口与显式偏离（如实）

1. **`run_events_replay` 与主键重叠**：PK `(run_id,seq)` 已提供同一访问路径；票面要求“run+seq 回放
   索引”，因此保留命名索引并在迁移头注释说明它是显式回放契约（未来 PK 变更仍保留此路径）。
   额外开销是被写路径索引，可接受；F07 若清理可重新评估。
2. **`details.snapshot_url` 是契约缺口**：冻结的 `Error` schema 没有 snapshot_url 字段；410 的
   该字段、以及 `event_cursor_expired` 错误码均为本票新增，应由后续契约修订吸收。
3. **phase 不进事件 payload**：`event.schema.json` 的 `run.status` 只有 state/error_code，
   `additionalProperties:false`。因此 result 提议事件只能是 `{"state":"running"}`；finalizing 由
   owner-only `GET /runs/{id}` 的 phase 表达（UI 明确这一点），不发明字段。
4. **`local_sequence` 不持久化**：契约基线 `run_events` 没有该列；它是 worker 的诊断序号。
   本票只做批内缺口/重复日志（run id + 序号，不含 token），不把它作为展示顺序。若后续需要
   “尝试日志”持久化，由 T033/F04A 的 attempt 事件模型承接。
5. **保留窗口只读不删**：0018 加 created_at 索引 + 410 语义，但本票不删除任何事件；实际
   retention purge 属 F07（删除模型）。这也是“重新连接不会静默丢历史”的保守选择。
6. **SSE 不用 EventSource**：页面用 fetch+ReadableStream，以便显式 Last-Event-ID、观察 410、
   停止重连；浏览器不具备流式读取时页面退回“只显示 Run 快照，不伪造时间线”。
7. **心跳/轮询/窗口是部署 knob**：默认 15s/500ms/24h；测试用 100ms/20ms/50ms 观察真实行为。
   每个值都经 `config.Load` 校验；它们不是请求参数，worker/客户端不能自选。
8. **request-logging 中间件转发 Flush**：`statusRecorder` 原先不实现 `http.Flusher`，SSE 会 500。
   本票在中间件加 `Flush()` 转发（状态码/日志语义不变），这是 SSE 能通过真实 HTTP 的必要修复。
9. **worker 事件只收 node.status**：契约 `WorkerEvent.type` enum 只有 node.status；run/approval/
   artifact/usage 事件由 Go 控制器派生。后续类型在其 ticket 内以新迁移扩展 CHECK 与校验。
10. **事件写入范围**：finalizer 内的 `AppendRunStatusTx` 只在真正提交 transition 后调用；
    waiting/guard-blocked/重复 finalize 不产生事件。started=false requeue 与 recovery_pending
    也按“已提交转换”投影，避免时间线缺一段真实状态。

## 3. 实际命令与原始结果

环境：`E:\Orchara\product`，profile `dev-stub`，隔离 PG 16.4（127.0.0.1:55432），Go 1.27.3，
Python 3.12.2，Node 24.13.0。

```text
$ python -m pytest tests/f03 -q
99 passed in 420.83s (0:07:00)

$ go test ./...
19 packages ok（internal/api、artifacts、background、background/handlers、catalog、compiler、
config、db、execution、health、identity、jcs、modelproxy、objectstore、policy、supervisor、tools、
uuid、workflow），8 个 cmd/db/audit/toolruns 包 no test files，0 FAIL，exit=0

$ go vet ./...
（无输出，exit=0）

$ gofmt -l .
（无输出）

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

说明：按批次硬约束不运行五套件/全量 `pytest tests`（由 orchestrator 收尾运行）。基线（改动前
同命令）：`tests/f03` 68 passed。新增 31 用例 = 迁移 6 + 事件 13 + SSE 7 + 页面 5。

## 4. 票面验收逐条

| # | 验收 | 状态 | 证据 |
|---|---|---|---|
| 1 | 重复事件不占 seq | 通过 | `test_t032_events.py`：同 event_id+同内容（local_sequence 改变也算同内容）→ 200、`next_event_seq` 不变、仍一行；异 payload → 409 `event_conflict` 且计数不变 |
| 2 | Last-Event-ID 回放去重 | 通过 | `test_t032_sse.py`：`Last-Event-ID=1` 只收到 seq2；终态回放 1..4 无重复、id 单调；Node 测 `applyRunEvent` 对重复 seq no-op |
| 3 | 越保留窗口返回 410 和快照 | 通过 | 同库第二 API 进程 retention=50ms，sleep 0.2s 后无 cursor/`Last-Event-ID:0` → 410 `event_cursor_expired`，`details.snapshot_url=/api/v1/runs/{id}`；最新 cursor 不误报 |
| 4 | 正文不进入元数据事件 | 通过 | `node.status` payload 精确三键，content/body/delta 422 定位；`run.status` payload 仅 state/error_code；`describeRunEvent` 不渲染 payload 正文；SQL 断言 payload 无正文键 |
| 5 | 通过端到端入口实际演示；失败、授权与重试/恢复边界有断言 | 通过 | 真实 PG + 编译 API + 真实 mTLS + 真实 SSE 流（含断开/重连）；角色 403、跨 run/随机 token 409、终态 409、410 快照、事件插入失败回滚业务转换、debug 创建失败无孤儿事件均有断言；T033/T035/T036/T038/F06 明确 not_run |
| 6 | 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件标 blocked，未运行标 not_run | 通过 | 页首记录版本/环境/hash；§3 原始命令/结果；§2/§5 偏离与 not_run；本票无 blocked；stub/real = 全部 real（SQL 仅断言/既有种子/故障注入） |

## 5. 边界与未运行（not_run）

- `not_run`：模型流式 delta 临时预览（F06）；UI 明确 `data-preview="not_run"`，不渲染伪造正文。
- `not_run`：run-bound artifact 注册、worker 上传/下载（T033）；`artifact.ready` 事件留给其控制器。
- `not_run`：waiting_approval/waiting_children 状态迁移与唯一 resume job（T035）；T032 的 finalizer
  对 waiting proposal 不伪造状态事件。
- `not_run`：cancel 端点、cancelling 状态与 unknown-operation guard（T036）；`hasUnknownOperationsTx`
  仍恒 false。
- `not_run`：过期租约 sweep、恢复证据与重新领取（T038）；`recovery_pending` 只挂起并投影。
- `not_run`：真实 worker/supervisor 进程与沙箱（F04A/F15A/T033）；测试客户端即 supervisor/worker。
- `not_run`：保留窗口的实际删除/清理（F07）；本票提供窗口语义与索引。
- `not_run`：浏览器 DOM 驱动页面（页面 JS 由 Node 真实执行核心逻辑）。
- `not_run`：全量 `pytest tests`（按本批硬约束，由 orchestrator 运行）。

## 6. 后续票可依赖的接口与表

- 表：`platform.run_events(run_id,seq,event_id,type,revision,payload,created_at)`，PK `(run_id,seq)`，
  `event_id` 全局唯一，type 限契约五值，payload 必须 JSON 对象；`runs.next_event_seq` 是唯一分配器。
- Go：
  - `execution.AppendRunStatusTx(ctx, tx, runID)`：在任何已提交 Run state 转换后、同一事务内调用；
    不要再写第二份 event SQL。
  - `execution.Service.SubmitWorkerEvents(ctx, runID, WorkerEventsRequest)`；
    `EventReplayFacts(ctx, runID, after, retention)`、`ListEventsAfter(ctx, runID, after, limit)`；
    `Service.Logger` 接收 local_sequence 诊断。
  - `execution.Event` 即 `event.schema.json` 封套（payload `json.RawMessage`）。
- SSE 语义：owner-only 404；`Last-Event-ID` 是 run-local seq；心跳是注释帧；410
  `event_cursor_expired` + `details.snapshot_url`；终态追平后关闭；retention/poll/heartbeat 由
  `ORCHARA_RUN_EVENT_*` 配置。
- **T033 必须承接**：若需要 `artifact.ready` 等新控制器事件，在 `internal/execution/events.go` 用
  同一锁/seq 分配器增加 payload allowlist 与自己 ticket 的迁移（0018 的 type CHECK 已含契约五值）；
  run-bound 注册端点不要直接 INSERT run_events。
- **T035 必须承接**：waiting_* 状态迁移必须在同一事务调用 `AppendRunStatusTx`；resume job 创建也要
  在同一事务；不要伪造 waiting 事件为 running。
- **T036 必须承接**：cancelling/cancelled 迁移与终态仲裁使用同一 helper；取消后 late event 仍应
  409 `run_terminal`/`run_state_conflict`。
- **T038 必须承接**：恢复重排/重新领取的每个状态转换同样投影；`recovery_pending` 事件已存在，
  恢复后应产生新状态事件而不是改写历史。

## 7. 文件与提交

- `db/migrations/0018_run_events.sql`
- `internal/execution/{events.go,execution.go,service.go,service_test.go}`
- `internal/api/{events.go,server.go,internal.go,middleware.go}`
- `internal/config/{config.go,config_test.go}`
- `internal/catalog/debug.go`
- `apps/web/src/{runs.html,assets/runs.js,debug.html,assets/debug.js}`
- `tests/support/{t032.py,fixtures.py}`、`tests/f03/test_t032_{migration,events,sse,pages}.py`、
  `tests/f03/test_t029_migration.py`、`tests/{f00b,f02a,f05}/test_t0*_migration.py`（链尾适配）
- `reports/f03/T032-run-events.md`、`PROGRESS.md`、`E:\orchara-notes\f05-f03\NOTES.md`
- 提交：feat（迁移 + Go 事件/SSE + Web）、test（`tests/f03` + 链尾适配 + support）、
  docs（本报告 + PROGRESS + NOTES）。不推送、不关闭 issue。

## 8. 规格变更

无。`product/docs/spec/**` 与 `product/contracts/**` 未修改；`python tools/sync_spec.py --check`
no drift。发现的契约缺口（`details.snapshot_url`、`event_cursor_expired`、run.status 无 phase、
local_sequence 无持久列）记录在 §2，由后续契约修订统一处理。
