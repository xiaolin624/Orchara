# ALL.01 / #159：集成六项架构改进并闭环原报告覆盖与证据

状态：**`passed`**——本地实现/验证完成。C4 交叉模块原先的两次失败保留为历史（未改写原始
日志，见 §6 注），已由后续修复分支 `t/fix-c4-cancel-timing`（基于集成分支 tip `108fa26`）
修复并复验：C4.03 真实栈模块 **3 passed** 连续 3 次（含 `-x` 首跑），C4 交叉模块
（C4.02+C4.03）**5 passed**，`go build`/`go vet`/`go test ./internal/presetexec/`/
`go test ./...`/`gofmt`/`sync_spec --check` 全部 exit 0（见 §6 修复复验）。代码在
`t/all.01-integration`（基于 `integration/architecture-c1-c6` tip `5fa2eea`）；C4 修复在
后续分支，由 orchestrator 合并。**未 push、未创建 PR、未关闭或编辑任何 Issue**；由
orchestrator 评审后合并/关闭。

## 1. 交付物

| 交付物 | 路径 | 说明 |
|---|---|---|
| 六项覆盖表 | `docs/reviews/2026-11-09-all-architecture-coverage.md` | 原报告 6 项 + 5 项观察、89 条故事、22 个工作切片、删除目标、代码/提交、命令/原始报告、限制 |
| PROGRESS 归一化 | `PROGRESS.md` `## C1–C6 架构改进（2026-11-09）` | C1 复用票 4 行保留；新增 18 票（#142–#159）一行一票；修复 C4.01 计数口径等 |
| 交付/限制记录 | `PROGRESS.md` `## C1–C6 已交付 / 仍有限制（2026-11-09）` | 六项状态、最终 tip、诚实限制、本地分支/凭据说明 |
| 删除审计 | `reports/architecture/ALL.01-deletion-audit.ps1`、`ALL.01-deletion-audit.txt` | 仓库级 production/tests/historical 分类与匹配数 |
| 集成检查原始输出 | `reports/architecture/ALL.01-*.txt` | Go、sync、tooling、f10/f11/f12、C2/C4/T113 交叉模块、T032 既有红 |
| 本报告 | `reports/architecture/ALL.01-integration.md` | 覆盖矩阵、审计命令/结果、套件结果、限制、提交/合并映射、#159 PROGRESS 行 |

## 2. 基线与环境

- 工作树：`E:\Orchara-wt\C159`，分支 `t/all.01-integration`；起点 `5fa2eea`
  （`integration/architecture-c1-c6` tip，ALL.01 开始前 `git rev-list --count 5fa2eea..integration/architecture-c1-c6` = 0；
  `git merge-base --is-ancestor integration/architecture-c1-c6 HEAD` exit 0）。
- 环境：Windows 11（NT 10.0.26200）；`D:\Python\python.exe` Python 3.12.2 / pytest 9.0.3；
  Go `E:\orchara-toolchains\go\bin\go.exe`（`GOCACHE`/`GOMODCACHE` 共享）；
  PostgreSQL 隔离集群 `127.0.0.1:55432`；WSL2 `Ubuntu-24.04` + runsc；Chrome 157（页面票既有）。
- 所有 pytest 经 `E:\Orchara-notes\test.ps1`（只从 `E:\Orchara\product\.env.local` 读取
  `ORCHARA_PG_PASSWORD`，不打印）。未打印/提交任何凭据。

## 3. 覆盖矩阵（摘要）

完整表见 `docs/reviews/2026-11-09-all-architecture-coverage.md` §1–§3。六项结论：

| 项 | 原报告项 | 设计/删除目标 | 规格故事 | 票 | 代码/证据 | 状态 |
|---|---|---|---|---|---|---|
| C1 | `spec` | 既有 check/sync；删除调用者同步顺序知识 | 1–13 | #138–#141 | `tools/sync_spec.py` + tooling 套件 | passed（本地）；WSL 完整套件/真实断电 `not_run` |
| C2 | `model` | 私有统一尝试驱动；删除 `dispatchStream*` 与四套旧循环 | 14–29 | #146–#148 | `internal/capabilities/attempt.go` 等 | passed（本地）；真实厂商 `blocked` |
| C3 | `knowledge` | owner 限定 reader + 权威 sha256/size；删除六个裸读/重复校验符号 | 30–44 | #149–#151 | `internal/knowledge/reader.go`、`readable_chunk.go` | passed（本地）；真实 embedding `blocked` |
| C4 | `nodes` | 显式 NodeExecution + typed outcome；删除假线性视图 | 45–56 | #152–#154 | `internal/presetexec/nodeexec.go` 等 | passed（本地）；`-race`/真实厂商 `not_run` |
| C5 | `tests` | 能力 Stack/一次执行/事实观察；删除票号别名、`run_linear_once`、纯转发 | 57–66 | #142–#145 | `tests/support/{runtime_stack,execution_support,run_observations}.py` | passed（本地）；j100/j101/t111 `not_run` |
| C6 | `web` | 显式 `OrcharaRunState` + guard；删除 14 个旧全局、页内 helper、重复 fallback | 67–83 | #155–#158 | `runstream.js`、`runstate-guard.js`、三页 | passed（本地）；仅 Chrome/Playwright |
| ALL.01 | 整合 | 覆盖/删除/门禁/进度闭环 | 1、13、84–89 | #159 | 本报告 + 覆盖表 + 原始输出 | `passed`（C4 取消计时用例修复后复验：C4.03 3 passed ×3、C4 交叉模块 5 passed；原两次失败保留为历史，见 §6 注） |

89 条故事：`plan.json` 各票 `stories` 并集 = 1–89（无缺号）；覆盖表 §3 按故事区间列出主题与票。

## 4. 叶子票与合并审计

验证命令（在 `E:\Orchara-wt\C159`）：

```powershell
git merge-base --is-ancestor <merge-sha> HEAD   # 每票逐一，exit 0
git log -1 --format='%h %s' <merge-sha>
git diff --name-only <merge-sha>^1 <merge-sha>  # 合并带入的代码/测试
```

结果：`#138–#158` 的 21 张叶子票全部在集成分支祖先中（21/21 exit 0）。`#142`/`#143` 的实现随
交接提交 `e27c6fb` 进入（无独立集成侧 merge）；其余 19 票的集成合并提交如下；“刷新”合并单列。

| 票 | 集成合并提交 | 刷新提交 | 报告 |
|---|---|---|---|
| #138 C1.01 | `b53c91c` | — | `C1.01-check.md` |
| #139 C1.02 | `a713f44` | — | `C1.02-sync-protection.md` |
| #140 C1.03 | `60dc1b0` | — | `C1.03-interruption.md` |
| #141 C1.04 | `0a79686` | — | `C1.04-locking.md` |
| #142 C5.01 | `e27c6fb`（交接提交） | — | `C5.01-stack.md` |
| #143 C5.02 | `e27c6fb`（交接提交） | — | `C5.02-execution.md` |
| #144 C5.03 | `0db1b2a` | `5fc4ae7` | `C5.03-run-observations.md` |
| #145 C5.04 | `3eec0c2` | — | `C5.04-support-contract.md` |
| #146 C2.01 | `107afaf` | `8946bb9` | `C2.01-model-complete.md` |
| #147 C2.02 | `7ae1d40` | — | `C2.02-model-stream.md` |
| #148 C2.03 | `06147f4` | `ce428e7`、`d9699d0` | `C2.03-cancel-cleanup.md` |
| #149 C3.01 | `b52b8e6` | — | `C3.01-verified-retrieval.md` |
| #150 C3.02 | `6703f6a` | — | `C3.02-verified-reads.md` |
| #151 C3.03 | `1eb0281` | — | `C3.03-knowledge-consistency.md` |
| #152 C4.01 | `5dbe372` | — | `C4.01-agent-context.md` |
| #153 C4.02 | `6e64648` | 票内 `d5b6b4b`/`67aa068` 等 | `C4.02-callapp-context.md` |
| #154 C4.03 | `9e37e60` | — | `C4.03-remove-linear-view.md` |
| #155 C6.01 | `7e7432c` | — | `C6.01-page-dependency-guard.md` |
| #156 C6.02 | `2792808` | — | `C6.02-runstate-view.md` |
| #157 C6.03 | `84b696a` | — | `C6.03-runs-recovery.md` |
| #158 C6.04 | `1e7aac9` | — | `C6.04-page-contract.md` |

## 5. 删除目标审计

执行于最终代码树（`5fa2eea`，最终 docs 提交前代码未变）。脚本
`reports/architecture/ALL.01-deletion-audit.ps1`，原始输出 `ALL.01-deletion-audit.txt`。
分类：production = `product/{internal,cmd,apps,tools,deploy,contracts,migrations}` +
根 `tools/deploy/contracts/examples`；tests = `product/tests`；historical =
`product/reports`、`PROGRESS.md`、`product/docs`、根 `docs/research/*.md`（报告/规格文字）。

关键命令（可复跑）：

```powershell
git grep -n -E 'dispatchStream|dispatchStreamFallback' -- product
git grep -n -E 'readChunkText|validateCachedItems|cacheHitReadFailureReason|cacheDiscardDecision|matchesSealedBody|cacheBodySHA256' -- product
git grep -n -E 'graphLinearView|linearRun\.available|linearRun\.skip|graphNodeView\.start|graphNodeAttempt\.started' -- product
git grep -n -E 'T024Stack|T029Stack|T031Stack|T033Stack|run_linear_once' -- product
git grep -n -E 'runStream[A-Za-z]*|RunStateDependencyUnavailable' -- product
D:\Python\python.exe -X utf8 reports/architecture/C5.04-contract-check.py --phase all
```

| 组 | production | tests | historical prose | 说明 |
|---|---|---|---|---|
| C2 `dispatchStream`/`dispatchStreamFallback`/`dispatchModelFallback` | 0/0/0 | 0/0/0 | 14/13/0 | 旧生命周期循环已删；`internal/capabilities` 仅剩 `attempt.go:274` 单一 `for attempt` |
| C2 `dispatchModel`/`dispatchFallback`（保留） | 3/4 | 0/0 | 11/13 | 前者是统一 session 的同步内容处理入口，后者是 session 的 fallback 派发；均非旧编排 |
| C3 六个裸读/重复校验符号 | 0/0/0/0/0/0 | 0 | 33/21/14/16/19/13 | reader + 权威 sha256/size 统一 |
| C4 假线性视图五个符号 | 0/0/0/0/0 | 0 | 39/11/5/9/7 | 图/线性调度事实显式化 |
| C5 票号 Stack 与 `run_linear_once` | 0/0/0/0/0 | 0 | 9/8/8/8/8 | AST 契约 `callers=0 definitions=0`（exit 0） |
| C6 `runStream*` / `*RunStateDependencyUnavailable` | 0/0 | 0/2 | 93/18 | 2 处 tests 命中是 `test_c6_04_page_contract.py` 的“断言其不存在”契约测试 |

历史 prose 命中为设计/报告/ADR 的迁移记录（例如设计文档列出待删符号），不是残留代码；本审计不以
历史文字否定 production/tests 为 0 的结论，也不把历史文字当作实现证据。C5 的纯转发删除由
`C5.04-contract-check.py` 的 55 项 AST 契约复核（三阶段：119 调用/55 定义 → 0/55 → 0/0，最终 0/0）。

## 6. 集成检查结果

环境与原始输出见 §2 与 `reports/architecture/ALL.01-*.txt`。所有 pytest 经 `test.ps1`。

| 检查 | 结果 | 原始输出 |
|---|---|---|
| `go test ./...` | **39 包 ok、0 fail、exit 0**（13 个 no-test 包） | `ALL.01-go-test.txt` |
| `go build ./...` | exit 0 | `ALL.01-go-build.txt` |
| `go vet ./...` | exit 0 | `ALL.01-go-vet.txt` |
| `gofmt -l internal cmd tools` | 空输出、exit 0 | `ALL.01-gofmt.txt` |
| `tools/sync_spec.py --check` | `no drift…`、exit 0 | `ALL.01-sync-check.txt` |
| `pytest tests/tooling` | **148 passed in 186.19s** | `ALL.01-pytest-tooling.txt` |
| `pytest tests/f10` | **114 passed, 1 warning in 804.44s**（warning 为既有 T060 对比测试的子进程 reader 线程 UnicodeDecodeError 警告，不影响断言；C3.03 时点同为 114） | `ALL.01-pytest-f10-full.txt` |
| `pytest tests/f11` | **85 passed in 648.44s** | `ALL.01-pytest-f11.txt` |
| `pytest tests/f12` | **525 passed in 370.25s**（含 C6 页面/栈套件、T096、C6.01–C6.04） | `ALL.01-pytest-f12.txt` |
| C2 交叉模块 `tests/f06/test_c20{1,2,3}_*.py` | **19 passed in 115.82s** | `ALL.01-pytest-c2-focus.txt` |
| C4 交叉模块 `tests/f04b/test_c4_0{2,3}_*.py` | **原 1 failed, 3 passed in 72.67s（历史，保留）；第一次修复后 5 passed in 77.15s；第二次复核强化 drain 断言后 C4.03 3 passed ×5 + focus 5 passed**（C4.03：38.62/42.35/41.49/41.12/40.53s） | 历史：`ALL.01-pytest-c4-focus.txt`、`ALL.01-pytest-c4-focus-rerun.txt`；第一次修复：`FIX-C4-*`；第二次：`REVIEW-C4-run1..5.txt`、`REVIEW-C4-focus.txt` |
| `tests/f13b/test_t113_cache_comparison.py` | **6 passed in 56.16s** | `ALL.01-pytest-t113.txt` |
| f03/T032 既有红 | **1 failed, 4 passed in 27.51s**（pristine `ce428e7`/`8cc19da` 等基线可复现；不计为通过、未修、未跳过） | `ALL.01-f03-t032-preexisting.txt` |
| REVIEW-2 C6 页面状态回归 | C6.04 **16 passed**（9 Chrome）、C6.01–C6.03 **62 passed**、f12 页面/栈 **44 passed**、f03 页面 **1 failed/29 passed**（既有 T032 红）、f04b/t083+f08 **17 passed**、runview **12 passed**、f02a/t027 **6 passed** | `REVIEW-C6.04-suite.txt`、`REVIEW-C6.01-03-suites.txt`、`REVIEW-C6-f12-pages-stacks.txt`、`REVIEW-C6-f03-pages.txt`、`REVIEW-C6-f04b-f08-pages.txt`、`REVIEW-C6-f04b-runview.txt`、`REVIEW-C6-f02a-t027-pages.txt` |

**C4 交叉模块失败说明（历史记录，保留原文；当时的运行不计为通过）：** `tests/f04b/test_c4_03_scheduler_regressions.py::test_graph_cancellation_drains_the_started_sibling_and_commits_one_exit`
首次运行失败于 `exit_evidence["started"] is True`（`sandbox=None`），隔离复跑失败于
`exit_evidence["sandbox_started"] is True`；两次都是取消意图在 runsc 取得首个 sandbox
execution unit/PID 之前写入（测试在首心跳后写取消），属已知的冷启动取消计时问题（C4.03
合并记录 §8 已标注同类竞态）。未修改、未跳过该失败；后续复核确认原断言本身需要
强化（不是“未改动”），见 §6.1。原始输出
`ALL.01-pytest-c4-focus.txt`（`1 failed, 3 passed in 72.67s`）与
`ALL.01-pytest-c4-focus-rerun.txt`（`1 failed in 30.85s`）。建议修复：等首个 sandbox
execution unit/PID 出现后再写取消意图（或等价确定性触发器），由复查后的实现者承接；本票
不自行改测试以取得绿色。

**C4 修复复验（2026-11-09，分支 `t/fix-c4-cancel-timing`，基于集成 tip `108fa26`；仅改测试，
未改产品代码）：** 开始排空用例改为真实 `Popen` supervisor + 有界轮询
`run_attempts.execution_unit_id` 与 `t033.runsc_list()`（sandbox id 按
`internal/sandbox/sandbox.go::NewSandboxID` 从执行单元派生），等**两个分支**的 runsc 容器
`STATUS=running` 且 PID>0 后，才用 plain SQL 写 `platform.runs.cancel_requested_at`（T036 同列），
随后解析 report 并保留唯一 attempt+exit/`reported_result IS NULL`/join+end skipped/无 orphan
的权威断言。输入改用合法的 9,999 行 XLSX 以把 sandbox 存活窗口拉长。旧的首心跳
触发器场景保留为独立用例 `test_graph_cancellation_from_the_first_heartbeat_commits_one_honest_exit`，
只断言两种到达时序共同的诚实契约（不要求 `started=true`）。复验：C4.03 模块
**3 passed** ×3（`FIX-C4-run1-x.txt` 46.91s、`run2` 41.51s、`run3` 47.43s），C4 交叉模块
**5 passed in 77.15s**（`FIX-C4-focus.txt`），C4.02 单独 **2 passed**（`FIX-C4-c4-02.txt`）；
`go build ./...`/`go vet ./...`/`go test ./internal/presetexec/`（ok 14.268s）/
`go test ./...`（39 包 ok / 0 FAIL）/`gofmt -l internal cmd tools`/`sync_spec --check`
全部 exit 0（`FIX-C4-*.txt`）。修复记录与残余 flake 风险见
`reports/architecture/FIX-C4-cancel-timing.md`。原失败日志未改写，作为历史保留。

### 6.1 第二次两轴复核对 C4 断言的修正（本分支 `t/c1c6-review-fixes-2`）

复核确认 `_assert_cancelled_run_and_attempt` 的“两分支都启动 + 至少一个终态 + 至少一个
failed”是弱化，且 `exit_evidence.started` 本身在“取消心跳先于 `sandbox.Start` 返回”的交错
里并不可靠。本分支改为显式 drain 契约（两分支启动；每个分支终态或非终态但其 runsc
容器/工作区确实消失；至少一个 failed；join/end 各一次 skipped；无 join 结果；1 attempt/1
exit；`reported_result IS NULL`；无 orphan），并**不再**在开始排空用例断言
`started`/`sandbox_started` 为 true。真实“已启动”证据 = pre-cancel 观察到两个分支容器
`running` + 事后容器/工作区消失；确定性终态由 Go seam 覆盖。复验 C4.03 模块 **3 passed ×5**、
C4 交叉模块 **5 passed**（`REVIEW-C4-run1..5.txt`、`REVIEW-C4-focus.txt`）。详见
`REVIEW-FIX-summary.md` §B。


**“受影响完整套件 / 产品集成回归”在本报告中有确定含义（不是全量 pytest）：** tooling **148**、
f10 **114**、f11 **85**、f12 **525**、C2 交叉模块 **19**、C4 交叉模块修复后 **5**、T113 **6**、
Go `go test ./...` **39 包 ok**、`go build`/`go vet`/`gofmt`/`sync_spec --check`。完整
`pytest tests` 仍为 **`not_run`**（f00b/f02b/f03/f04/f05/f06/f07/f08/f09/f13b 的其它模块未在本树
全量重跑）；因此 §8 的 `passed` 只指 **scoped local integration verification**，不是全量产品
验收，也不把上述范围称/当作 full-suite。

**REVIEW-2 修复验证（本分支 `t/c1c6-review-fixes-2`）：** C6.04 **16 passed**（含 9 个真实
Chrome）、C6.01–C6.03 **62 passed**、f12 页面/栈 **44 passed**、f03 页面 **1 failed/29 passed**
（唯一失败 = 既有 T032 红，未计通过）、f04b/t083+f08 页面 **17 passed**、f04b runview
**12 passed**、f02a/t027 页面 **6 passed**、C4.03 模块 **3 passed ×5**、C4.02+C4.03 focus
**5 passed**（原始输出 `REVIEW-C6-*.txt`、`REVIEW-C4-*.txt`）。

## 7. 残留限制（not_run / blocked / partial）

- **真实供应商模型**：`blocked`/`not_run`（C2、C3 embedding/model；ADR 0035 未改；证据均为 dev stub
  与本地 loopback fake HTTP provider）。
- **生产部署/远端集成**：`not_run`。代码仅在本地分支，未 push/部署；`#159` 关闭后远端仍不含本分支。
- **浏览器**：`partial`。仅 Chrome 157 headless + Playwright；无其它浏览器/真实布局/媒体查询/
  EventSource 断线；C6 故障用 `page.route` 注入，未物理删除部署资源或制造生产半发布。
- **文件系统/平台**：`partial`。Windows NTFS + WSL2 本地文件系统；NFS/9p、跨卷、只读挂载、
  真实断电/设备损坏 `not_run`（C1 只保证可检测/可恢复的事务记录）。
- **完整 `pytest tests`**：`not_run`（范围见 §6）。
- **`go test -race`**：`not_run`（本机 Go 无 cgo）。
- **C4 取消排空的 node.status 观测竞态**：被 fail-fast 取消的兄弟分支，其终态
  `node.status` 提交若在取消落地时在途会被中止（supervisor 记录 `... was not recorded:
  ... context canceled`），事件投影可停留在 `running`。这是可观测性投影的既有边界，不是
  Run/attempt/exit 权威决定；确定性终态由 Go seam
  `TestGraphRunCancellationDrainsStartedSiblings` 覆盖（§6 修复复验）。
- **既有页面缺陷**：`/assets/nav.js` 无静态路由（真实 404）；f03/T032 `location.search` 断言与
  T095 实现冲突。均未修、未跳过、未计为通过。
- **证据历史凭据**：C5/C6 部分原始输出曾含本机开发 PG 口令与一次性签名值，已在 HEAD 的
  `5fa2eea` 脱敏（见 `SECURITY-redaction-note.md`）。本次新增的 `ALL.01-pytest-c4-focus*.txt`
  与 `ALL.01-f03-t032-preexisting.txt` 曾打印 `Stack` 的本机开发 DSN 与一次性 `t024-signing-*`，
  已在提交前用通用模式替换为 `<REDACTED>` / `<REDACTED-SIGNING-KEY>`（`ALL.01-redact-evidence.py`、
  `ALL.01-fix-evidence-encoding.py`、`REVIEW-redact-evidence.py`）。**安全说明：本分支仅本地，
  历史提交仍含脱敏前的本机开发凭据历史；不要 push；如需合并/发布，只用 `git merge --squash`
  或 squash/cherry-pick 最终树。** 保留 `reports/architecture/SECURITY-redaction-note.md`，
  不重写历史。

### 7.1 六项实际状态（第二次复核对齐，不写“六项全部 passed”）

| 项 | 实际状态 | 未闭环/限制 |
|---|---|---|
| C1 | `partial` | WSL/Linux 完整 pytest、真实物理断电/设备损坏、真实 OS ACL、远程/跨卷 FS `not_run` |
| C2 | `partial` | 真实厂商 provider 端点/凭据/配额 `blocked`；真实 socket 退避取消、`-race` `not_run` |
| C3 | `partial` | 真实 embedding provider/模型质量/ANN 规模 `blocked`/`not_run`；物理 cache 清理 `not_run` |
| C4 | `partial` | `go test -race`、部分真实栈组合（Agent awaiting_approval、图 Agent+取消+并发兄弟）`not_run`；call_app 自身平台取消无可观察点 |
| C5 | `partial` | 完整 `pytest tests`、F12 j100/j101 旅程、`test_t111_linux_isolation.py` `not_run` |
| C6 | `partial` | 仅 Chrome 157 headless + Playwright；故障用 `page.route` 注入，未物理删除部署资源；非 Chrome 浏览器/真实布局/EventSource 断线 `not_run` |
| ALL.01 | `passed`（**仅限 scoped local integration verification**，范围见 §6） | 完整 pytest、真实供应商、生产部署、远程 FS `not_run` |

## 8. 分支、提交与验收结论

- 分支：`t/all.01-integration`；起点 `5fa2eea`；证据提交：**`0be7ff2`**（本报告、覆盖表、
  `ALL.01-*` 原始输出与 PROGRESS 归一化）；其后的文档提交仅回填本 tip、统一新增文件为仓库的
  LF 行尾约定，不改变代码、测试结果或断言；分支 HEAD 见 `git log -1`。
- 本票提交内容：覆盖表、本报告、`ALL.01-*` 原始输出与审计脚本、PROGRESS 归一化与交付/限制节。
- C4 失败修复：后续分支 `t/fix-c4-cancel-timing`（worktree `E:\Orchara-wt\FIX`，基于集成
  tip `108fa26`）只改 `tests/f04b/test_c4_03_scheduler_regressions.py` 与相应记录；修复
  记录 `reports/architecture/FIX-C4-cancel-timing.md`，原始输出 `FIX-C4-*.txt`。
- 验收结论：六项叶子均已在集成分支合并，删除目标在 production/tests 清零；**“通过”只指
  §6 定义的 scoped local integration verification**（受影响完整套件 + 交叉模块 + Go/sync 门禁），
  不是全量 `pytest tests`，也不是生产/真实供应商验收。C4 交叉模块原先的两次失败保留为历史
  （§6 注）；第二次复核把弱化的终态断言改为显式 drain 契约，并复验 C4.03 **3 passed ×5**、
  C4 交叉模块 **5 passed**（§6.1）。因此 ALL.01 本地集成状态为 **`passed`（scoped）**；
  六项自身按 §7.1 如实记录为 `partial`。`#159` 的最终关闭与远端合并由 orchestrator 执行；
  不得把该 `passed` 写成“六项全部通过/全量通过”。

## 9. 可直接追加的 PROGRESS 表行（#159，5 列）

```markdown
| #159 | #160 | ALL.01 集成六项架构改进并闭环原报告覆盖与证据 | passed（**scoped local integration verification**：§6 定义的影响完整套件 + 交叉模块 + Go/sync 门禁；**不是**全量 `pytest tests`；C4 取消计时用例两次修复后复验 C4.03 3 passed ×5、C4 交叉模块 5 passed；原失败保留为历史） | 覆盖表 `docs/reviews/2026-11-09-all-architecture-coverage.md`（原报告 6 项 + 5 观察、89 条故事、22 个工作切片逐项映射）；本报告 `reports/architecture/ALL.01-integration.md`；修复记录 `reports/architecture/FIX-C4-cancel-timing.md`、`REVIEW-FIX-summary.md`；删除审计 `ALL.01-deletion-audit.txt`（C2/C3/C4/C5 目标 production/tests 全 0，C6 仅 2 处“断言其不存在”契约测试；C5 AST 0 callers/0 definitions）；集成门禁 `go test ./...` 39 包 ok、`go build`/`go vet`/`gofmt`/`sync_spec --check` 干净，`pytest tests/tooling` 148、`tests/f10` 114、`tests/f11` 85、`tests/f12` 525、C2 交叉模块 19、C4 交叉模块原 1 failed/3 passed（历史，见 §6 注）→ 修复后 5 passed（`FIX-C4-focus.txt`；C4.03 3 passed ×5，`REVIEW-C4-run1..5.txt`）、T113 6；T032 既有红 1 failed/4 passed；REVIEW-2：C6.04 16 passed（9 Chrome）、C6.01–03 62 passed、f12 页面/栈 44、f03 页面 1 failed/29 passed、f04b+f08 17、runview 12、f02a/t027 6；限制：完整 `pytest tests`、真实供应商、生产部署、非 Chrome 浏览器、远程/网络文件系统 `not_run`；C4 被排空兄弟的 node.status 投影可停留 running，`exit_evidence.started` 是认证时间戳、开始排空用例不断言（可观测性竞态，权威终态由 Go seam 覆盖）；分支仅本地且历史含脱敏前凭据，**禁止直接 push，仅用 `git merge --squash`/squash 最终树**。 |
```

## 10. orchestrator 待办

1. 先合并 C4 修复分支 `t/fix-c4-cancel-timing`（或把其测试改动带入 ALL.01 合并），再合并
   `t/all.01-integration` 到 `integration/architecture-c1-c6`（或按发布流程 squash）；回填最终
   merge tip 到覆盖表 §9 与本报告 §8。
2. 关闭 `#138–#159`（本分支未关闭/编辑任何 Issue）；确认 `#142`/`#143` 的交接提交说明。
3. 确认 §7/§7.1 的 `blocked`/`not_run`/`partial` 条件不被关闭动作抹去；**不要直接把本分支
   历史 push 到远端**（含旧凭据），如需远端发布只用 `git merge --squash`（或 squash/cherry-pick
   最终树）；保留 `SECURITY-redaction-note.md`，不重写历史。
4. 如需完整 `pytest tests` 与真实供应商/生产验收，另立环境或票；本票不声称这些条件通过。
