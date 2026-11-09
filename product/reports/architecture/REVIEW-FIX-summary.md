# REVIEW-2 修复记录：C1–C6 集成分支第二次两轴复核（#159）

- 分支：`t/c1c6-review-fixes-2`；worktree `E:\Orchara-wt\REVIEW2`；基线
  `89474ee`（`integration/architecture-c1-c6` tip，含 18 张叶子票与 C4 取消计时修复）。
- 范围：只修复核发现（A–D）；不改业务契约/迁移/生成源；不 push、不建 PR、不关闭 Issue。
- 结论：A/B/C 已修复并有可复跑证据；D 作为 accepted residual smell 记录，不做广泛重构。

## A. C6 规格缺口：页面级 Run 终态/轮询重复解释

复核发现第一次 C6.04 删除证明漏掉了页面里的 Run 终态/轮询定义。本分支迁移：

| 文件 | 删除的页内定义 | 迁移到 |
|---|---|---|
| `apps/web/src/assets/runs.js` | `isRunTerminal`、`shouldPollRun` | 7 处改用 `OrcharaRunState.isTerminal`（sandbox/recovery/merge/timeline/event/detail poll ×2）；详情轮询上限仍由页面 `RUN_POLL_MAX` 拥有 |
| `apps/web/src/assets/debug.js` | `runIsTerminal`、`shouldPollRun` | `describeRunStage` 改为消费 `OrcharaRunState.view` 的 `terminal/stage/phase`（只保留页面文案）；轮询用 `OrcharaRunState.shouldPoll('dispatched', runState, polls, DEBUG_MAX_POLLS)`，慢观察/停止用 `isTerminal` |
| `apps/web/src/assets/chat.js` | `shouldPollRequest`（设计删除表“仅 Request 轮询”） | 实时轮询只走 `shouldPollHandling` → `OrcharaRunState.shouldPoll`；Request 7 态呈现/队列/终态表保留 |

保留的页面职责：DOM 渲染、节点表/节点错误解释、Request 7 态文案、SSE/轮询生命周期、
输入形状与浏览器能力探测。共享模块没有新增 transport/DOM。

C6.04 仓库级契约扩展（仍是同一 16 条套件，没有新增测试函数）：

- `test_no_legacy_global_reference_remains_in_code` 用 `^\s*function\s+(...)\s*\(` 扫描全代码树，
  断言 `isRunTerminal`/`runIsTerminal`/`shouldPollRun`/`shouldPollRequest` 定义不存在；
  删除名称以拼接形式写入测试，断言字符串不会被该正则命中。
- `test_pages_have_no_page_local_dependency_helper_or_typeof_fallback` 额外断言
  `runs.js` 至少 7 个 `OrcharaRunState.isTerminal` 调用、`debug.js` 的
  `describeRunStage` 消费 `OrcharaRunState.view` 且轮询消费 `shouldPoll`、
  `chat.js` 消费共享 `shouldPoll`。
- 受影响 harness 的断言改为“页内 helper 不存在 + 行为来自共享接口”
  （`tests/f02a/test_t027_pages.py`、`tests/f03/test_t029/031/035/036/038_pages.py`、
  `tests/f08/test_t074_pages.py`、`tests/support/t096.py`、`tests/f12/test_t096_pages.py`）。
  旧“dispatched=false”的 Request-only 断言按设计 C6 的“dispatched 后继续观察 Run”
  改为共享规则断言，属契约修正，不是放宽。

验收（原始输出在 `reports/architecture/REVIEW-C6-*.txt`）：

| 命令 | 结果 |
|---|---|
| `pytest tests/f12/test_c6_04_page_contract.py -q -p no:cacheprovider`（`C604_WRITE_EVIDENCE=1`） | **16 passed in 29.90s**（含 9 个真实 Chrome） |
| `pytest tests/f12/test_c6_01_dependency.py tests/f12/test_c6_02_runstate_view.py tests/f12/test_c6_03_runs_recovery.py` | **62 passed in 74.28s** |
| f12 页面/栈（t095 page_roles、t096 pages/review2/stack、t098 mobile/stack） | **44 passed in 82.98s** |
| f03 页面（t029/031/032/034/035/036/038） | **1 failed, 29 passed in 157.87s**；唯一失败 = 既有 T032 `location.search` 红（pristine 基线可复现，未修、未计通过） |
| f04b/t083 + f08 页面 | **17 passed in 70.69s** |
| f04b runview（含 3 个 `.mjs`） | **12 passed in 36.69s** |
| f02a/t027 页面（改过的 harness） | **6 passed in 29.58s** |

## B. C4 测试弱化：恢复并强化 drain 契约

复核指出 `_assert_cancelled_run_and_attempt` 把“每分支终态”降级为“两分支启动 + 至少一个终态
+ 至少一个 failed”。本分支改为显式 drain 契约：

- 两分支均 `pending,running`（路由/启动事实）；
- 每个分支：终态（`failed`/`succeeded`）**或**非终态（`running`）且该分支 runsc 容器/工作区
  确实消失——用测试内 `NewSandboxID`/`graphSandboxUnit` 复算的 per-node sandbox id 断言不在
  `runsc list` 且不在 sandbox state root；全局 `runsc list` 为空；
- 至少一个 `failed`；join/end 各恰好一个 `skipped`；无 join node result；
- 恰好一个 attempt、一次 exit；`reported_result IS NULL`；无孤儿。

开始排空用例的触发器也收紧：Popen supervisor 启动后，有界轮询要求**两个分支容器
`STATUS=running` 且 PID>0**（不只是出现在 `runsc list`）后才写取消意图。这样“已启动兄弟被
排空”由观察到的运行容器 + 事后容器/工作区消失直接证明。

**不再断言 `exit_evidence["started"]`/`["sandbox_started"]` 为 true（开始排空用例）。**
复核确认该字段是 supervisor 的认证时间戳：取消心跳可能在 `sandbox.Start` 返回前被观察到，
于是被真实排空的 unit 仍会以 `started=false` 写入 exit evidence；该字段不是“没有 unit 运行”
的证明。真实“已启动”证据是 pre-cancel 的 running-container 快照 + 事后无 orphan；确定性
终态决定由 Go seam `TestGraphRunCancellationDrainsStartedSiblings` 覆盖。首心跳用例保留原有
诚实契约（`started`/`sandbox_started` 为 bool 且 `sandbox_started ⇒ started`，不要求 true）。

被排空兄弟的终态 `node.status` 投影仍可能丢失（supervisor 记录
`node.status right=failed was not recorded: ... context canceled`），因此投影不是该分支的权威；
文档已在 `FIX-C4-cancel-timing.md`、`C4.03-remove-linear-view.md`、`ALL.01-integration.md`
中改为描述强化后的断言与两种取消形状，不再声称“原断言未变”。

验收（原始输出 `REVIEW-C4-*.txt`）：

| 命令 | 结果 |
|---|---|
| C4.03 模块 ×5（第 1 次带 `-x`） | **每次 3 passed**：38.62s / 42.35s / 41.49s / 41.12s / 40.53s |
| C4.02+C4.03 focus | **5 passed in 74.37s** |

历史：`REVIEW-C4-run3-red.txt` 保留强化过程中的一次 `started=false` 失败（旧断言，
未通过）；它促成了上述“不要断言 started 字段”的修正，未改写。

## C. 文档/状态过度声明

- `PROGRESS.md` #157/#158 行删除/更正“未改 PROGRESS.md”的陈旧说法（PROGRESS 行由集成合并
  提交写入），保留既有证据且追加 review-2 更正说明。
- `ALL.01-integration.md`/覆盖表 §7 明确定义“受影响完整套件 / 产品集成回归”= tooling 148、
  f10 114、f11 85、f12 525、C2 交叉 19、C4 交叉（修复后 5）、T113 6、Go 39 包、sync gate；
  **完整 `pytest tests` 仍为 `not_run`**，该名称不再被当作全量套件。
- 收口节按实际状态改写：C1 `partial`（Linux pytest/断电/ACL `not_run`）、C2 `partial`
  （真实厂商 `blocked`/`not_run`）、C3 `partial`（真实 embedding/模型/规模 `not_run`）、
  C4 `partial`（`-race`/部分真实组合 `not_run`）、C5 `partial`（全量 pytest/journeys/Linux
  隔离 `not_run`）、C6 `partial`（仅 Chrome / `page.route` 故障注入）。不再写“六项全部
  passed”；`#159` 的 `passed` 明确限定为上述 **scoped local integration verification**。
- 安全说明明确：分支仅本地，历史提交含脱敏前的本机凭据；**不要 push**；如需远端发布只用
  `git merge --squash` 或 squash/cherry-pick 最终树；保留
  `SECURITY-redaction-note.md`，不重写历史。

## D. 接受残留 smell（不做广泛重构）

- `internal/presetexec/linear.go` 的 `runLinearAgentNode`/`runLinearCallAppNode` 仍有相近的
  “构造 nodeExecution → 映射 outcome → 唯一提交”形状。它们已经共享 `nodeExecution`/
  `nodeOutcome` 语义，进一步抽取会触及线性调度器的提交路径与错误映射；本复核只修发现，
  不做该重构。记为 accepted residual smell，后续若有专门票再做。
- 其它页面专属职责（节点表、Request 7 态、DOM、轮询生命周期）按设计保留。

## 限制（继续诚实记录）

- 完整 `pytest tests` `not_run`；`tests/tooling` 本轮未重跑（ALL.01 集成树 148 passed，
  本分支未触碰 tooling 代码）。
- `go test -race` `not_run`（本机 Go 无 cgo）；真实厂商模型/embedding、生产部署、
  非 Chrome 浏览器、远程/网络文件系统 `not_run`/`blocked`。
- f03/T032 `location.search` 既有红未修、未跳过、未计通过。
- 本分支全部证据在提交前扫描/脱敏：无 DSN、`ORCHARA_PG_PASSWORD`、`t024-signing-*`
  或其它凭据进入 `REVIEW-*` 日志（见 `REVIEW-redact-evidence.py` 与提交后的
  `REVIEW-redaction-check.txt`）。

## Go/同步门禁

| 检查 | 结果 | 原始输出 |
|---|---|---|
| `go test ./internal/presetexec/ ./internal/api/ -count=1` | `ok` 两个包、exit 0 | `REVIEW-go-test.txt` |
| `go build ./...` | exit 0 | `REVIEW-go-build.txt` |
| `go vet ./...` | exit 0 | `REVIEW-go-vet.txt` |
| `gofmt -l internal cmd tools` | 空输出、exit 0 | `REVIEW-gofmt.txt` |
| `python -X utf8 tools/sync_spec.py --check` | `no drift…`、exit 0 | `REVIEW-sync-check.txt` |
