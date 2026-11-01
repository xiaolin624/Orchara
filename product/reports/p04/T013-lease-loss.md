# P04 T013 验收：模拟租约失联而不启动第二个执行者

- 任务：P04 / GitHub Issue #14（`T013`）
- 状态：**passed**
- 代码：`db/migrations/0004_fence_tokens.sql`、`probes/p04_lease/lease_probe.py`
- 证据：[`lease-probe.json`](lease-probe.json)、[`lease-events.jsonl`](lease-events.jsonl)
- 测试：`pytest product/tests/p04 -q` → **30 passed**（本票新增 14：10 单元 + 4 真机集成）
- 环境：Windows；Python 3.12.2；PostgreSQL 16.4（`tools/pg_dev.py`，`127.0.0.1:55432`）；
  guest `Ubuntu-24.04`（WSL）；基线提交 `c0a677c`（本票改动尚未提交）
- 输入哈希（SHA-256）：
  - `0004_fence_tokens.sql` = `dccf33d9f3104574a00c52b446be4c5c6436e50bf840f0c6a62e243a21e97f7b`
  - `lease_probe.py` = `1ab0a799a2efc5c75d403b4ae729780ea4819f88e780d96e9e9840a87555a1d1`
  - `test_lease_loss.py` = `8585467143fa345440259c42bb0cbae5cd0f542fc8f84fdaf2e1da35699c4a6f`
  - `lease-events.jsonl` = `6a08dabd2388bea58fbd53035a63c5402be694500da35b4b6f5e45eda66f72b3`

## 1. 场景

本票复现的是产生"双执行"的那条路径：

1. attempt 1 持有租约 token 并心跳；
2. 心跳消失（模拟网络分区）；
3. 控制面把 worker 判为 stale，run 进入 `recovery_pending`；
4. **旧进程还活着**时请求新的 attempt。

探针在第 4 步拒绝启动，并记录拒绝理由。拒绝依据是**进程存活证据**，不是租约到期：
attempt 启动时记录 `/proc/<pid>/stat` 第 22 字段（start ticks），之后区分"仍在运行"、
"已退出"和"PID 被复用"。PID 复用不算旧进程（既不能永久阻塞恢复，也不能去杀这个号码的新进程）。

旧进程消失后，新 attempt 用 fencing token 接管：`app.supersede_token()` 用一条
`UPDATE ... WHERE superseded_at IS NULL RETURNING` 做 compare-and-swap，两个并发 attempt
只有一个能赢并成为 active。之后旧 token 的能力调用得到 `token_superseded`，新 token 得到
`ok`，不存在的 token 得到 `token_unknown`。

## 2. 数据库侧（产品钩子）

`app.lease_tokens`（`token` 主键、`worker_id`、`run_id`、`attempt`、进程身份、`issued_at`、
`superseded_at`、`superseded_by`）：

| 约束/函数 | 作用 |
|---|---|
| 部分唯一索引 `lease_tokens_one_active_per_run` | 一个 run 至多一个未失效 token，由数据库保证 |
| `app.capability_status(token)` | `ok` / `token_superseded` / `token_unknown`，供能力调用拒绝旧凭据 |
| `app.issue_token(...)` | 首个 attempt；已有 active token 时返回 `won=false`，不留第二个活凭据 |
| `app.supersede_token(...)` | 单条条件 UPDATE 完成交接；输家得不到新 token |

并发验证（`test_racing_supersessions_produce_exactly_one_winner`）：两个线程同时用同一个旧
token 调 `supersede_token`，结果恰好 **1 个 `won=true`、1 个 `won=false`**，账本恰好
2 条 token、1 条 active。

## 3. 实测时序（`lease-events.jsonl`，17 条）

```text
 1 attempt_started            attempt=1 pid=454 start_ticks=30395
 2 capability_allowed         token1 ok（断网前）
 3 heartbeat_lost             模拟网络分区，最后一次心跳已过期
 4 worker_declared_stale      runs_parked=1，run_state=recovery_pending
 5 attempt_requested          run_state=recovery_pending，记录 pid/start_ticks
 6 attempt_blocked            reason="old worker process 454 is still running (pid + start ticks match)"
 7 old_process_exited         观测 observed_start_ticks=null，verdict=exited
 8 attempt_requested          run_state=recovery_pending
 9 attempt_allowed            reason="old process exited; pid + start ticks are no longer present"
10 token_superseded           attempt 1 -> 2，won=true（tok_341175ccf7fc -> tok_16975856bc2d）
11 new_attempt_started        attempt=2 worker=t013-worker-2
12 capability_rejected        token1 -> token_superseded
13 capability_allowed         token2 -> ok
14 capability_rejected        unknown token -> token_unknown
15 attempt_requested          run 已回到 running
16 attempt_blocked            新 attempt 已开始，再次请求被拒（不会起第三个执行者）
17 recovery_completed         blocked_attempts=2，final_attempt=2
```

`event_order.ok = true`：sequence 严格递增、`monotonic_ns` 严格递增、`at` 不回退，
`heartbeat_lost → … → recovery_completed` 顺序检查通过。

## 4. 断言（`lease-probe.json`）

| 断言 | 结果 |
|---|---|
| 旧进程存活时新 attempt 被拒 | true |
| 被拒时没有签发/撤销任何 token（仍 1 条 active） | true |
| 旧进程退出后新 attempt 被允许 | true |
| 交接只产生一个赢家 | true |
| 旧 token 被拒（`token_superseded`） | true |
| 新 token 被接受（`ok`） | true |
| 未知 token 被拒（`token_unknown`） | true |
| 恢复后恰好 1 条 active token | true |
| 新 attempt 开始后再次请求被拒 | true |
| 阻断理由被写入时序记录 | true |
| 事件时序校验通过 | true |
| 第一次请求时进程确为 `still_running` | true |

## 5. 复现命令

```powershell
# 全量场景 + 生成报告工件（17 条事件，全部断言 true，退出码 0）
python product/probes/p04_lease/lease_probe.py simulate `
  --run run-t013-lease-loss --worker t013-worker --seconds 120 `
  --events product/reports/p04/lease-events.jsonl `
  --out product/reports/p04/lease-probe.json

# 本票测试：10 单元 + 4 真机集成
python -m pytest product/tests/p04/test_lease_loss.py -q     # 14 passed

# P04 全部测试（原有 16 + 新增 14）
python -m pytest product/tests/p04 -q                          # 30 passed

# Go 侧：本票没有 Go 改动。除并发 P05 未完成的未跟踪包外全部构建通过
go -C product build ./internal/... ./cmd/...                   # exit 0
# 其余包（排除另一票进行中的 probes/p05_dsl/go）也 exit 0；见边界说明
```

全树 `go -C product build ./...` 在本次运行中失败，原因是**另一票**未跟踪的
`product/probes/p05_dsl/go` 缺 `main`（`function main is undeclared in the main package`），
与本票无关，未改动该目录。

CLI 子命令：`migrate` / `issue` / `call` / `attempt` / `simulate` / `events`。
`issue` 输出原始 token（调用方需要）；事件日志与报告只记录 `tok_<sha256 前 12 位>` 指纹，
`lease-probe.json` 经检查不含任何原始 token。

## 6. 覆盖的验收条件

- [x] 旧进程存活时新尝试不启动（真机进程 + `still_running` 阻断，账本不变）
- [x] 旧 token 的新能力调用被拒（`token_superseded`；新 token `ok`；未知 token `token_unknown`）
- [x] 断网恢复有时序记录（17 条 JSONL，单调 sequence / monotonic clock / 不回退的墙钟）
- [x] 端到端入口实际演示（`simulate` CLI + JSONL + JSON 工件 + 真机集成测试）
- [x] 记录版本、输入哈希、命令、原始结果与环境 profile

## 7. 边界（未证明的部分）

- **Go supervisor 尚未接 token**：`internal/supervisor` 仍只做"park 到 `recovery_pending`，
  从不重启"；探针用相同 SQL 复现该状态转移并在事件里标为
  `source: probe SQL mirroring internal/supervisor`。把"确认退出→撤销旧 token→发新租约"
  接进 Go 监督循环是后续工作。
- **能力校验不是原子写围栏**：`capability_status` 是单次状态判定；check 与受保护写之间仍有
  竞态。真正的 checkpoint 三表 fencing（同一事务加锁）见
  `docs/spec/research/execution-fencing-options.md`，本票未实现。
- **PID 复用只做单元验证**：真实内核 PID 回收不可控，没有强行制造复用；真机集成覆盖
  "仍在运行"与"已退出"两种观测。
- **外部副作用不可召回**：token 失效不能撤回旧进程已经发出的外部请求。
- **沙箱/在途写排空不在本票**：由 T016 的 drain gate 覆盖；本票只证明进程证据与 token 交接。
- **权限授予未包含在迁移里**：探针以 dev owner 运行；部署时需按最小权限单独授权运行时角色
  调用 `app.capability_status` 与 token 函数，现有迁移与其它票一样未向 `orchara_worker` 发 GRANT。
- **全树 Go 门禁被并发工作阻塞（非本票）**：`product/probes/p05_dsl/go` 是另一票未跟踪的
  进行中文件，缺 `main`；本票无 Go 改动，`./internal/...`、`./cmd/...` 及其余包构建通过。
