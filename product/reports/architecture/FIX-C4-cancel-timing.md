# FIX-C4 — 修复 C4.03 真实栈取消计时竞态（取消排空已启动兄弟）

- 分支：`t/fix-c4-cancel-timing`；worktree `E:\Orchara-wt\FIX`；基线
  `108fa26`（`integration/architecture-c1-c6` tip，post-#159，18 张叶子票已全部合并）。
- 范围：**只改真实栈测试**，不改产品代码、Go 执行器、迁移、契约或规格。
- 结论：**passed**。C4.03 真实栈模块连续 3 次 `3 passed`（含 `-x` 首跑），
  C4 交叉模块（C4.02+C4.03）`5 passed`；`go build ./...`、`go vet ./...`、
  `go test ./internal/presetexec/`、`go test ./...`、`gofmt -l internal cmd tools`、
  `sync_spec --check` 全部 exit 0。原始失败日志保持原样作为历史，未改写。
- 未 push、未创建/合并 PR、未编辑或关闭 GitHub Issue。

## 1. 根因（红证据）

`ALL.01-integration.md` §6 记录的两个失败（原始输出
`ALL.01-pytest-c4-focus.txt`、`ALL.01-pytest-c4-focus-rerun.txt`，未改写）：

- 首跑失败于 `assert evidence["started"] is True`（`sandbox=None`）；
- 隔离复跑失败于 `assert evidence["sandbox_started"] is True`。

机制：旧测试用 test-owned SQL 触发器在 attempt 的**第一个 heartbeat** 写
`cancel_requested_at`。按 `internal/sandbox/executor.go`，第一个 heartbeat 发生在
`runsc` 启动之前；下一次 50ms ticker 才观察到取消。WSL/runsc 冷启动时，取消可能在
任何 sandbox unit/PID 出现前就被观察到（`SandboxStarted=false`）；暖机时又可能在
两个 sandbox 已完成之后才被观察到（`cleanup pid=0`，证据仍是 `started=false`）。
因此旧触发器既不能确定性覆盖“已启动兄弟被排空”，也不能保证 `started=true`。

修复过程中还观察到真实竞态：被 scheduler fail-fast 取消的兄弟分支，其终态
`node.status` 提交若正好在取消落地时在途，会被中止并在 supervisor 日志留下
`node.status right=failed was not recorded: ... context canceled`，事件投影停留在
`running`。`emitNode` 自述“observability, not authority”：该投影丢失不影响调度器
唯一 exit/join+end skipped/无结果的权威决定，确定性终态由 Go seam
`TestGraphRunCancellationDrainsStartedSiblings` 覆盖。第一次修复曾把“每个分支都必须有
failed/succeeded 终态”降级为“两分支都启动 + 至少一个分支有终态 + 至少一个 failed”；
**第二次两轴复核认定该写法是弱化，已替换为显式 drain 契约**（见 §1.1），不再保留弱化版本。

## 1.1 第二次两轴复核：强化 drain 断言（`t/c1c6-review-fixes-2`）

复核指出 `_assert_cancelled_run_and_attempt` 的“两分支都启动 + 至少一个终态 + 至少一个
failed”是弱化。现改为显式 drain 契约：两分支启动；每个分支终态（`failed`/`succeeded`）
或非终态（`running`）但其 per-node runsc 容器/工作区确实消失（per-node sandbox id
不在 `runsc list`/state root，且全局 `runsc list` 为空）；至少一个 `failed`；join/end
各一次 `skipped`；无 join 结果；1 attempt/1 exit；`reported_result IS NULL`；无 orphan。
触发器也收紧为两个分支容器 `STATUS=running` 且 PID>0 后才写取消意图。

**刻意不再断言** `exit_evidence["started"]`/`["sandbox_started"]` 为 true：它们是 supervisor
认证时间戳，取消心跳若在 `sandbox.Start` 返回前落地，被真实排空的 unit 也会写
`started=false`；该字段不是“没有 unit 运行”的证明。真实启动证据是 pre-cancel 的 running
容器快照 + 事后无 orphan；确定性终态由 Go seam 覆盖。两种取消形状（确定性排空 = 观察两
running 容器后取消；首心跳旧场景 = 只断言共同诚实契约）均保留。复验：C4.03 **3 passed ×5**、
C4.02+C4.03 **5 passed**（`REVIEW-C4-run1..5.txt`、`REVIEW-C4-focus.txt`）。

## 2. 修复内容（`tests/f04b/test_c4_03_scheduler_regressions.py`）

1. **`test_graph_cancellation_drains_the_started_sibling_and_commits_one_exit`**
   改为确定性序列：
   - 真实 `cmd/supervisor exec --once` 以 `subprocess.Popen` 启动（同一 binary/env/
     `FinalizeWorker` 生命周期，与 `execution.execute_once`/`run_supervisor_exec` 一致）；
   - 有界轮询（90s，50ms 间隔）：先等 `platform.run_attempts.execution_unit_id`，
     再等 `t033.runsc_list()` 同时列出**两个分支**的 runsc 容器。sandbox id 由
     `internal/sandbox/sandbox.go::NewSandboxID` 的规则在测试内复算：执行单元
     `graphSandboxUnit` = `execution_unit_id[:48-len(suffix)] + "-a" + sha1("graph:<node>")[:8]`，
     再做 lowercase/`-_:./`→`-`/trim/48 截断；超时失败信息包含 attempts、
     execution_unit_id、期望 id、已观察分支与最近一次 `runsc list`；
   - 只有两个容器都出现后才写 plain SQL
     `UPDATE platform.runs SET cancel_requested_at = now() ...`
     （`t029.set_cancel_requested`，T036 同列；无产品触发器），随后
     `communicate()` 解析 supervisor report；
   - 保留全部权威断言：report `cancelled`/`cancel_requested`/`run_state=cancelled`、
     Run `cancelled` + `cancel_requested_at` + 无 output artifact、两分支均
     `pending,running`、join/end 各一次 `skipped`、无 join node result、
     1 attempt/1 exit、`reported_result IS NULL`；**第二次复核后**每个分支要么终态
     （`failed`/`succeeded`），要么非终态但其 per-node runsc 容器/工作区确实消失
     （per-node sandbox id 不在 `runsc list`/state root，且全局 `runsc list` 为空）。
     **不再断言 `exit_evidence["started"]`/`["sandbox_started"]` 为 true**（见 §1.1）。
2. **`test_graph_cancellation_from_the_first_heartbeat_commits_one_honest_exit`**（新增）
   保留旧触发器场景（`t042.request_cancel_after_first_heartbeat`），断言两种到达时序
   共同的诚实契约：cancelled、1 attempt/1 exit、无结果、join/end skipped、无孤儿；
   不要求 `started=true`，只要求 `started`/`sandbox_started` 为 bool 且
   `sandbox_started ⇒ started`。
3. **输入**：给开始排空测试一个合法的 9,999 数据行 XLSX（固定 preset 工具上限
   10,000 行）。三行公共样例的 sandbox 生命周期（暖机约 0.4–0.6s）短于一次
   `runsc list` WSL 往返 + SQL 取消的延迟；大工作簿把两个 sandbox 的存活窗口拉长到
   ~1.5–2s，使“观察到两个在跑的容器后再取消”成为真正的确定性触发器，而不是固定
   sleep。它是同一条真实路径（真实上传/finalize、真实 runsc、真实 preset 工具），
   不改变被测语义。

测试内复算的 id 映射、Popen/轮询/解析与共享断言 helper 都在同一测试文件；其他测试
文件未改动。

## 3. 命令与结果（本机实际执行）

环境：Windows 11；`D:\Python\python.exe` 3.12.2 / pytest 9.0.3，经
`E:\Orchara-notes\test.ps1` 从 `product` 运行（隔离 PostgreSQL 127.0.0.1:55432、
WSL2 Ubuntu-24.04 + runsc）；Go `E:\orchara-toolchains\go\bin\go.exe`
（共享 GOCACHE/GOMODCACHE）。

| 命令 | 结果 | 原始输出 |
|---|---|---|
| `pytest tests/f04b/test_c4_03_scheduler_regressions.py -x -q -p no:cacheprovider` | **3 passed in 46.91s**，exit 0 | `FIX-C4-run1-x.txt` |
| 同上（完整复跑 2） | **3 passed in 41.51s**，exit 0 | `FIX-C4-run2.txt` |
| 同上（完整复跑 3） | **3 passed in 47.43s**，exit 0 | `FIX-C4-run3.txt` |
| `pytest tests/f04b/test_c4_02_callapp_context.py tests/f04b/test_c4_03_scheduler_regressions.py -q -p no:cacheprovider` | **5 passed in 77.15s**，exit 0 | `FIX-C4-focus.txt` |
| `pytest tests/f04b/test_c4_02_callapp_context.py -q -p no:cacheprovider`（单独） | **2 passed in 32.70s**，exit 0 | `FIX-C4-c4-02.txt` |
| `go build ./...` | exit 0，无输出 | `FIX-C4-go-build.txt` |
| `go vet ./...` | exit 0，无输出 | `FIX-C4-go-vet.txt` |
| `go test ./internal/presetexec/ -count=1` | `ok orchara/platform/internal/presetexec 14.268s`，exit 0 | `FIX-C4-go-test-presetexec.txt` |
| `go test ./... -count=1`（附加） | **39 包 ok / 0 FAIL**，exit 0 | `FIX-C4-go-test-all.txt` |
| `gofmt -l internal cmd tools` | 空输出，exit 0 | `FIX-C4-gofmt.txt` |
| `python -X utf8 tools/sync_spec.py --check` | `no drift: current source, both copies and synchronized baseline agree`，exit 0 | `FIX-C4-sync-check.txt` |

退出码汇总：`FIX-C4-gates-exit-codes.txt`。所有 pytest 都走真实 PostgreSQL、真实
`cmd/supervisor exec --once`、真实 mTLS 与真实 WSL2/runsc；没有 skip/弱化产品权威断言。

## 4. 确定性论证与残余 flake 风险

- 旧触发器失败的两个方向（取消早于 sandbox、取消晚于 sandbox 完成）都被消除：
  开始排空用例只在两个分支的 runsc 容器都实际存在后写取消意图，因此至少一个
  sandbox 会被心跳观察到并被杀死，`started`/`sandbox_started` 为证据事实。
- 残余风险 1（低）：B 分支的终态 `node.status` 可能在 fail-fast 取消落地时在途而被
  中止，事件投影停留 `running`。这是产品已声明的“observability, not authority”；
  第二次复核已把断言改为：每分支终态或非终态但其 runsc 容器/工作区确实消失，且至少
  一个 failed；权威决定仍由唯一 exit/join+end skipped/无结果断言。
- 残余风险 1b（已由第二次复核修正）：`exit_evidence.started` 是 supervisor 认证时间戳，
  取消心跳可能在 `sandbox.Start` 返回前被观察到，于是被真实排空的 unit 仍写
  `started=false`。因此开始排空用例不再断言该字段；真实启动证据改为 pre-cancel 观察到
  两个分支容器 `running` + 事后无 orphan。首心跳用例保留 `started` 为 bool 且
  `sandbox_started ⇒ started` 的诚实契约。
- 残余风险 2（低）：轮询要求两个容器同时出现在一次 `runsc list` 快照里。两分支
  同时 fork、下载同一输入，10k 行工作簿的 sandbox 存活窗口约 1.5–2s，远大于
  ~170ms 的 WSL 往返；若宿主极端负载导致窗口错过，失败信息会明确列出期望/已观察
  的 sandbox id（90s 上限），不会误报为断言通过。
- 残余风险 3（低）：若机器负载使 SQL 取消 + 心跳观察超过分支剩余生命周期，两个分支
  可能先正常结束，随后 join/end 运行，测试会在 `join == ["skipped"]` 处失败（不会
  假绿）。本轮 4 次真实栈运行（3 次模块 + 1 次 focus）均未出现。
- 首心跳旧场景测试接受 `started=false` 或 `true` 两种诚实结果，不会因 WSL 冷暖机
  差异翻转；确定性终态由 Go seam 测试承担。

## 5. 变更文件

| 文件 | 变化 |
|---|---|
| `product/tests/f04b/test_c4_03_scheduler_regressions.py` | +268/−45：新增 Popen/poll/parse helpers、10k 行 XLSX 输入、`NewSandboxID`/`graphSandboxUnit` 测试内复算；重写开始排空用例为“观察到两个 runsc 容器后写取消意图”；新增首心跳诚实契约用例；共享取消断言 helper 保留全部权威断言并记录 node.status 观测竞态。 |
| `product/reports/architecture/ALL.01-integration.md` | 状态 `partial → passed`；C4 失败说明保留为历史并追加修复复验。 |
| `product/docs/reviews/2026-11-09-all-architecture-coverage.md` | C4/ALL.01 结果、限制与签收检查更新，原失败保留为历史。 |
| `product/PROGRESS.md` | `#159` 行与最终交付节 `partial → passed`，保留全部 not_run/blocked 限制。 |
| `product/reports/architecture/FIX-C4-*.txt` | 本次修复的原始证据（见 §3）。 |
| 本文件 | 修复记录。 |

## 6. 未做 / 限制（如实）

- 全量 `pytest tests` 未在本分支重跑（沿用 ALL.01 范围；本修复只触碰 C4 真实栈模块，
  已跑 C4.02+C4.03+Go 全包）。
- `go test -race` `not_run`（本机 Go 无 cgo）。
- 真实厂商模型/embedding、生产部署、非 Chrome 浏览器、远程/网络文件系统继续
  `not_run`/`blocked`（同 ALL.01 §7）。
- 新证据文件已在提交前扫描：无本机 DSN、`ORCHARA_PG_PASSWORD`、`t024-signing-*` 或
  其它凭据；调试期含凭据的临时日志已删除，未进入提交。原始失败日志
  `ALL.01-pytest-c4-focus*.txt` 未改写（其历史脱敏见 `SECURITY-redaction-note.md`）。
