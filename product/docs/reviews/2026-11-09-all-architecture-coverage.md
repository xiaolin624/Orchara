# C1–C6 六项架构改进：覆盖、删除审计与集成证据（#159 / ALL.01）

日期：2026-11-09。票：[#159](https://github.com/xiaolin624/Orchara/issues/159)（父 [#160](https://github.com/xiaolin624/Orchara/issues/160)）。
分支：`t/all.01-integration`，基于集成分支 `integration/architecture-c1-c6` tip `5fa2eea`。
最终提交/合并 tip：见文末 §9（全部套件均在该代码树上执行；tip 由后续仅回填元数据的文档提交写入）。

本表是六项改进的最终覆盖入口：把原审核报告的每一项、发布总规格的 89 条用户故事、22 个工作切片
（C1 复用 `#138–#141`，新增 `#142–#158`，整合 `#159`）、实现代码、集成合并提交、真实命令与原始输出
逐项对应起来。未运行、blocked 与 partial 单列在本表与 §8，不用本地通过替代真实供应商、生产部署或
远端集成。

## 0. 来源与权威

- 原审核归档：`product/docs/reviews/source/2026-11-08-architecture-review.html`
  （SHA-256 `a5e76e29d584bbd6bf27e517e1b347f1113328a41d9b0e6af0a7f7730e675fb5`）与同目录
  `2026-11-08-architecture-review.json`（SHA-256 `807b5783ef37006ef7a0a5591e424f682950487ef71b3e76643516feefe507da`）。
  两份均为原报告原样归档，不是完成状态。
- 需求/决策：`product/docs/reviews/2026-11-08-all-architecture-grilling.md`（Q1–Q8）。
- 设计/删除目标：`product/docs/reviews/2026-11-08-all-architecture-design.md`。
- 总规格（89 条故事、Implementation/Testing Decisions、Out of Scope）：
  `product/docs/reviews/all-architecture-tickets/published-spec.md`（`## User Stories` 内 89 条，已核对编号 1–89）。
- 拆票/依赖/故事映射：`product/docs/reviews/all-architecture-tickets/plan.json`
  （每票 `stories` 字段；并集为 1–89，无缺号）。
- 产品进度：`product/PROGRESS.md`；逐票报告与原始输出：`product/reports/architecture/`。

## 1. 六项覆盖表

| 项（原报告项） | 设计 / 删除目标 | 规格 + 验收场景 | 票 / 依赖 | 代码 + 集成提交 | 实际命令 / 原始报告 | 限制（`not_run` / `blocked` / `partial`） |
|---|---|---|---|---|---|---|
| **C1 规格同步与漂移**（原 `spec`，Strong） | 保留既有 `check` / `sync` 两个操作；将内容清单、来源/双副本/基线比较、受保护切换、进程互斥、回滚与显式恢复收进同一 module；删除“调用者自行记住同步顺序”的知识。设计 §C1，设计总表 C1 行。 | 故事 **1–13**；#137 的 42 条 C1 故事继续有效；验收场景：源新增/修改/删除、双副本与基线漂移、保护切换、进程中断后可重复恢复、协作进程互斥、CRLF/二进制与路径限制不放宽（#138–#141 AC）。 | **#138**（无）→ **#139**（blocked by #138）→ **#140**、**#141**（均 blocked by #139）；复用 #137 父票，不新建重复票。 | `product/tools/sync_spec.py`；`product/tests/tooling/test_sync_spec*.py`（C1.01–C1.04 四组新增）；集成提交 **b53c91c**(#138)、**a713f44**(#139)、**60dc1b0**(#140)、**0a79686**(#141)。 | `pytest tests/tooling`：87 → 112 → 140 → 120 passed（各票时点，含新增 23/25/28/8）；ALL.01 最终 **148 passed in 186.19s**；`tools/sync_spec.py --check` 每票与最终均 exit 0（`no drift…`）；变异探针分别 18/2/10/1/2/1 failed 后恢复，未留在提交中；WSL 标准库探针 22/22（非 pytest）。报告 `C1.01-check.md`、`C1.02-sync-protection.md`、`C1.03-interruption.md`、`C1.04-locking.md` 及各自 `C1.0x-*.txt`。 | `partial`：WSL/Linux 完整 pytest `not_run`（WSL 无 pytest）；真实物理断电、设备损坏、远程/网络/跨卷文件系统、真实 OS ACL `not_run`；票内未跑全量 `pytest tests`。 |
| **C2 模型尝试生命周期**（原 `model`，Strong） | 外部保留 `Invoke`/`Stream`，私有 `modelAttemptSession` + 统一尝试驱动持有候选、预留、attempt、重试/fallback、取消、关闭与结算；同步完整响应与流式 generation 保留各自内容处理。删除 `dispatchStream`/`dispatchStreamFallback`、旧 `dispatchModelFallback` 与四套独立生命周期循环，只留统一驱动的单一 `for attempt`。设计 §C2。 | 故事 **14–29**；验收场景：冻结版本/部署候选门禁、拒绝不为未派发调用伪造 attempt、真实派发前原子预留、最多 3 主 + 1 兼容 fallback、权限/Schema/工具参数拒绝不重试、派发前/退避/调用中/消费者停止/退避取消、失败预留先释放再评估 fallback、fallback 兼容与固定版本、按实际候选 price version 结算、完整响应后校验失败仍结算真实 usage、缺 usage/业务取消规则不变、每 attempt 独立 generation、清理失败可观察且 final 投递失败不撤销已封存成功。 | **#146**（无）→ **#147**（blocked by #146）→ **#148**（blocked by #147）。 | `product/internal/capabilities/{attempt.go,stream.go,service.go,fallback.go,params.go,errors.go,attempt_cleanup_test.go}`；`product/internal/api/stream.go`；新增 `tests/f06/test_c201_model_attempts.py`、`test_c202_model_stream.py`、`test_c203_cancel_cleanup.py`。集成提交 **107afaf** + 刷新 **8946bb9**(#146)、**7ae1d40**(#147)、**06147f4** + 刷新 **ce428e7**/**d9699d0**(#148)。 | 新增真实栈：C2.01 **4 passed**、C2.02 **8 passed**、C2.03 **7 passed**；回归 T041/T044/T045/T046/T122：**59** → **63** → **66 passed**；ALL.01 交叉模块 **19 passed in 115.82s**；`go test ./...`（39 包 ok）、`go vet`、`gofmt`、`sync_spec --check` 干净。报告 `C2.01-model-complete.md`、`C2.02-model-stream.md`、`C2.03-cancel-cleanup.md` 及 `C2.0x-*.txt`。 | `blocked`/`not_run`：真实厂商 provider 无端点/凭据/配额，未调用；同步 HTTP “退避中取消”确定性窗口、`ctx` 存活但 emit 失败的真实 socket 场景、`go test -race`（本机 Go 无 cgo）、跨父子 run 并发预算 e2e 均 `not_run`；浏览器不适用。 |
| **C3 可读片段与正文完整性**（原 `knowledge`，Worth exploring） | owner 限定的可读片段 module：候选、单片段打开、缓存复检三个操作共用当前权限谓词、权威 `sha256`+`size_bytes` 与同次读取字节验证；删除 `readChunkText`、`validateCachedItems`、`cacheHitReadFailureReason`、`cacheDiscardDecision`、`matchesSealedBody`、`cacheBodySHA256` 及三入口重复拼装。设计 §C3。 | 故事 **30–44**；验收场景：检索/引用/缓存同一权限与完整性；同长度替换、截断、size 不符、缺失/非法 metadata、对象缺失均不返回正文；删除/撤权先于内容服务；重建中/失败后旧活动索引仍可读、激活使旧引用失效；打开已有引用不要求当前 embedding、新检索要求当前 revision；坏候选跳过且部分/空不缓存；缓存瞬态 miss 保留、确定性损坏丢弃；回答返回前逐引用复检；cache 不绕过 embedding 可用性检查。 | **#149**（无）→ **#150**（blocked by #149）→ **#151**（blocked by #150）。 | `product/internal/knowledge/{reader.go,service.go,readable_chunk.go,cache_store.go,cache.go,answer.go}`；新增 `tests/f10/test_c301_verified_retrieval.py`、`test_c302_verified_reads.py`、`test_c303_knowledge_consistency.py`；`tests/f13b/test_t113_cache_comparison.py`；`tests/f11` 删除/一致性子集。集成提交 **b52b8e6**(#149)、**6703f6a**(#150)、**1eb0281**(#151)。 | 新增真实栈：C3.01 **8 passed**（`f10` 96）、C3.02 **10 passed**（受影响子集 50）、C3.03 **8 passed**（`f10` 114）；T113 **6 passed**（三票各自）、T109 `fault_09` **1 passed**；ALL.01：`pytest tests/f10` **114 passed, 1 warning in 804.44s**、`pytest tests/f11` **85 passed in 648.44s**、T113 **6 passed in 56.16s**；`go test ./internal/knowledge ./internal/api`、build/vet/gofmt/sync 干净。报告 `C3.01-verified-retrieval.md`、`C3.02-verified-reads.md`、`C3.03-knowledge-consistency.md` 及 `C3.0x-*.txt`。 | `blocked`/`not_run`：真实 embedding provider 无凭据/adapter；真实模型生成质量、ANN/pgvector 规模、物理 cache 行清理、Windows 文件 open/独占语义的跨平台可移植性 `not_run`；T113 committed evidence JSON 未重写。 |
| **C4 显式节点执行上下文**（原 `nodes`，Worth exploring） | Agent 与 call_app 用显式 `nodeExecution` 输入与 typed `nodeOutcome` 复用，图/线性调度器各自从权威调度事实构造输入并唯一提交；删除 `graphLinearView`、`linearRun.available`、`linearRun.skip`、`graphNodeView.start` 与重复 `graphNodeAttempt.started`；保留其他节点仍在使用的 capture 与调度器内部记录。设计 §C4。 | 故事 **45–56**；验收场景：线性/图同一节点业务实现、显式允许输入与封存事实、并行 namespace 稳定、固定子应用版本与显式绑定、同一版本两次逻辑调用不误去重、节点成功需已验证登记产物、节点只返回局部成功/失败/取消/等待、图取消/失败排空已启动兄弟后唯一提交、waiting 保留 checkpoint/证据、optional/skipped/join 规则不变、恢复复用有效封存输出不虚构完成节点、图执行不再依赖假线性视图。 | **#152**（无）→ **#153**（blocked by #152）→ **#154**（blocked by #153）。 | `product/internal/presetexec/{nodeexec.go,agent.go,agentcontext.go,callapp.go,linear.go,graphrun.go,presetexec.go,nodeexec_test.go,scheduler_regression_test.go}`；新增 `tests/f04b/test_c4_02_callapp_context.py`、`test_c4_03_scheduler_regressions.py`。集成提交 **5dbe372**(#152)、**6e64648**(#153)、**9e37e60**(#154)。 | Go：`go test ./internal/presetexec` **132** → **137** → **142 passed / 0 fail**，`go test ./...` 39 包 ok；真实栈：C4.01 **29 passed**（f06 t043 6、t047 2；f04b t085 6、t089 7、t091 8）+ LangGraph 语义证明 **7 passed**（非生产执行，单列，不计入 29）；C4.02 **38 passed** + 语义证明 7；C4.03 **62 passed** + 语义证明 7；ALL.01 交叉模块 **1 failed, 3 passed in 72.67s**（历史；隔离复跑 1 failed in 30.85s，见 §7 注）→ 修复分支 `t/fix-c4-cancel-timing` 复验 **5 passed**（C4.03 3 passed ×3、C4.02 2 passed）；第二次两轴复核（`t/c1c6-review-fixes-2`）强化为显式 drain 契约后复验 **C4.03 3 passed ×5**、C4 交叉模块 **5 passed**（`REVIEW-C4-run1..5.txt`、`REVIEW-C4-focus.txt`）。报告 `C4.01-agent-context.md`、`C4.02-callapp-context.md`、`C4.03-remove-linear-view.md` 及 `C4.0x-raw/`。 | `not_run`/`blocked`：`go test -race`（本机 Go 无 cgo）；真实厂商 adapter；真实栈 Agent `awaiting_approval`（固定工具非写风险）、图 Agent + 平台取消 + 并发兄弟组合；call_app 自身平台取消无可观察调用点（finalizer/attempt-exit 权威，T114 语义未改）；浏览器 DOM 不适用。 |
| **C5 共享测试能力**（原 `tests`，Strong） | 按真实能力组织 support：规范 Stack（真实 API/PG/对象目录/证书/binaries/签名）、一次真实 supervisor 执行 + finalizer 生命周期、运行事实观察；删除 `T024Stack`/`T029Stack`/`T031Stack`/`T033Stack` 票号别名链、`run_linear_once`、纯转发与泛化再导出。票专用 fixture/图/断言保留。设计 §C5。 | 故事 **57–66**；验收场景：一个按能力命名的 Stack、一次执行不重复进程/finalizer 顺序、既有进程生命周期与 fixture 隔离不变、环境覆盖只进目标子进程、启动/超时/异常/清理路径回收进程、supervisor report 不等于已提交 Run、DB/SSE/封存事实共享读取而期望独立、票专用场景保留、迁移后临时别名删除且全仓无调用者、真实执行证据不靠收集成功。 | **#142**（无）→ **#143**（blocked by C5.01）→ **#144**（blocked by C5.01）→ **#145**（blocked by C5.01–C5.03）。 | `product/tests/support/runtime_stack.py`、`execution_support.py`、`run_observations.py`；迁移 27/30/21 个调用者文件；删除 14 个支持模块中 55 项兼容入口（5 Stack 别名、11 一次执行转发、39 观察再导出/转发）。提交：交接 **e27c6fb**（含 #142/#143 实现；两者无独立集成合并提交）、**0db1b2a** + 刷新 **5fc4ae7**(#144)、**3eec0c2**(#145)。 | C5.01：2 → **13 passed**，收集 2696；C5.02：**8 passed** + **2 passed**，收集 2696 → 2698；C5.03：46 passed/1 failed（外来并发 runsc 孤儿断言），隔离复跑 **8 passed**，最终合并后 **28 passed**，收集 2755；C5.04：19 项 18 passed（1 项取消计时抖动，隔离 1 + 清理 5 passed）、t087/t089/t043 25、t047/t049/t063/t084 12、合并后 32 项 31 passed、收集 2767 → 2829；ALL.01：AST 契约 **0 callers / 0 definitions**（``callers=0 definitions=0`、exit 0`）。报告 `C5.01-stack.md`、`C5.02-execution.md`、`C5.03-run-observations.md`、`C5.04-support-contract.md` 及同目录原始输出。 | `partial`/`not_run`：票内未跑全量 `pytest tests`；F12 `j100_*`/`j101_*` 完整旅程与 `test_t111_linux_isolation.py` 未运行（仅收集与调用点迁移）；取消计时敏感断言在并行负载下曾失败并隔离通过，不宣称任意负载稳定；未用真实供应商模型、无真实浏览器；C5.03 未改 Go 代码、未跑 Go 测试。 |
| **C6 页面共享状态与恢复**（原 `web`，Worth exploring） | 深化既有 `runstream.js` 为显式只读 `OrcharaRunState`（`view`/`failure`/`correct` + 页面投影），页面统一经 `runstate-guard.js` 一次完整依赖检查；删除 14 个旧 `runStream*` 全局、4 个页内 `*RunStateDependencyUnavailable()` helper 与三页重复 fallback；DOM/请求生命周期留页面 adapter。设计 §C6。 | 故事 **67–83**；验收场景：三页同一 Run 事实解释、finalizing 是 phase、202 取消是意图、unknown 待核对、dispatched 后继续观察、终态/子运行/产物一起判断、410 丢旧 cursor 只走安全同源快照、迟到响应不覆盖新选择、认证/缺资源/瞬态保持、共享脚本缺失时“运行状态暂不可用”并暂停受影响观察/操作、导航/重载/独立功能保留、harness 加载真实共享字节、删除全部重复 fallback、浏览器故障证据与 Node 逻辑证据分开。 | **#155**（无）→ **#156**、**#157**（均 blocked by #155）→ **#158**（blocked by #156/#157）。 | `product/apps/web/src/assets/{runstream.js,runstate-guard.js,chat.js,runview.js,runs.js,debug.js}`；`chat.html`/`runs.html`/`debug.html`；`product/internal/api/server.go`；`deploy/dev/smoke.py`；37 个 harness/工具按 runstream→guard→page 顺序加载。集成提交 **7e7432c**(#155)、**2792808**(#156)、**84b696a**(#157)、**1e7aac9**(#158)。 | C6.01 **24 passed**（11 Node + 3 真实服务 + 10 Chrome）；C6.02 **19 passed**（16 Node + 2 API + 1 Chrome）+ 回归 43/10/13，唯一红为既有 f03/T032；C6.03 **20 passed**（12 + 1 + 7）；C6.04 **16 passed** + C6.01–C6.03 **62 passed** + f12 **44** + Chrome 3 页 × {正常,404,语法错误} 9 case；第二次两轴复核（`t/c1c6-review-fixes-2`）迁移页内 `isRunTerminal`/`runIsTerminal`/`shouldPollRun`/`shouldPollRequest` 至 `OrcharaRunState` 并扩展仓库级删除契约（仍 16 条，9 Chrome），复验 C6.04 16 passed、C6.01–03 62 passed、f12 页面/栈 44、f03 页面 1 failed/29 passed（既有红）、f04b+f08 17、runview 12、f02a/t027 6（`REVIEW-C6-*.txt`；`REVIEW-FIX-summary.md`）；ALL.01：`pytest tests/f12` **525 passed in 370.25s**；仓库级搜索证据见 §6。报告 `C6.01-page-dependency-guard.md`、`C6.02-runstate-view.md`、`C6.03-runs-recovery.md`、`C6.04-page-contract.md` 及 `C6.0x-browser-evidence.json`。 | `partial`：真实浏览器只有 Chrome 157 headless + Playwright（无其它浏览器/真实布局/媒体查询/EventSource 断线）；故障用 `page.route` 注入，未物理删除部署资源或制造生产半发布；真实厂商模型未调用；既有 `/assets/nav.js` 404 与 f03/T032 `location.search` 既有红未修（未计为通过）。 |
| **ALL.01 集成与覆盖**（#159；故事 1、13、84–89） | 逐项回溯原报告与删除目标；运行整合门禁与受影响套件；区分本地/已提交/远端集成与未运行条件；回填六项覆盖表与进度。 | 故事 **1、13、84–89**；AC：六项及全部细化场景映射证据、C1 复用不重写 #137、整合检查、旧生命周期/裸读/假视图/票号转发/页面重复规则确认清理、逐票命令与原始结果、覆盖表与进度更新且未闭环项不消失。 | **#159**；blocked by #140、#141、#145、#148、#151、#154、#158。 | 本文件、`product/reports/architecture/ALL.01-integration.md`、`ALL.01-*.txt`、`ALL.01-deletion-audit.ps1`；PROGRESS 归一化与最终交付/限制节。 | 见 §7：Go、sync、tooling、f10、f11、f12、C2/C4/T113 交叉模块；删除审计见 §6；覆盖表见 §1–§5。 | `passed`（C4 取消计时测试修复后复验：C4.03 3 passed ×3、C4 交叉模块 5 passed；原两次失败保留为历史，见 §7 注）；完整 `pytest tests`（见 §8，记录已运行范围与未运行套件）、真实供应商、生产部署、非 Chrome 浏览器、远程/网络文件系统、物理断电 `not_run`。 |

## 2. 原审核报告逐项追溯

原报告 6 个候选（`spec`/`model`/`knowledge`/`nodes`/`tests`/`web`）与 5 项原始观察全部有落点；
没有原报告项被静默丢弃。

| 原报告项 | 原问题 / 证据 | 原删除测试 / 保留边界 | 设计落点 | 规格 / 票 | 代码 / 最终证据 | 未闭环 |
|---|---|---|---|---|---|---|
| `spec`（Strong，local-substitutable） | 源已存在文件的内容修改/删除不进入检查分支；检查只覆盖 manifest ↔ 副本。 | 删除同步顺序知识；比较规则不可删。ADR 0001。 | 设计 §C1 | 故事 1–13；#138–#141 | `tools/sync_spec.py`；§1 C1 行全部证据 | WSL 完整套件、真实断电/远程 FS `not_run` |
| `model`（Strong，mock） | 四条路径重复候选、预留、attempt、失败关闭与 fallback；同步/流式取消与清理规则分散。 | 删除重复编排，不删除预留/关闭规则；ADR 0035；job 与 run 账本不合并。 | 设计 §C2 | 故事 14–29；#146–#148 | `internal/capabilities/attempt.go` 等；§1 C2 行 | 真实厂商 `blocked`；`-race` 等 `not_run` |
| `knowledge`（Worth exploring，local-substitutable） | `readChunkText` 几乎只包 Open/ReadAll；检索读正文未做哈希/大小比较；缓存命中另有 `matchesSealedBody` 深校验。 | 删除裸读取；保留缓存命中复检与 ADR 0015/0018 活动索引政策。 | 设计 §C3 | 故事 30–44；#149–#151 | `internal/knowledge/reader.go`、`readable_chunk.go`；§1 C3 行 | 真实 embedding `blocked`；规模/模型质量 `not_run` |
| `nodes`（Worth exploring，ports & adapters） | 图调度器构造 `graphLinearView` 伪造 linearPlan、复制 available/scopeIDs 并注入空 skip；节点兼容 nil fallback/图覆盖。 | 删除“伪装成线性运行”的 interface 知识；保留共享 Agent/call_app 执行；ADR 0034。 | 设计 §C4 | 故事 45–56；#152–#154 | `internal/presetexec/nodeexec.go` 等；§1 C4 行 | `-race`、真实厂商、部分真实栈组合 `not_run` |
| `tests`（Strong，local-substitutable） | `T033Stack → T031Stack → T029Stack → T024Stack` 别名链；`t083.run_once`/`t086.run_once` 转发；`t091` 批量转出口。 | 删除别名、一次执行转发与观察再导出；保留真实进程/finalize/退出证据 module。ADR 0006。 | 设计 §C5 | 故事 57–66；#142–#145 | `tests/support/{runtime_stack,execution_support,run_observations}.py`；§1 C5 行 + §6 AST 0/0 | 全量 pytest、j100/j101、t111 `not_run` |
| `web`（Worth exploring，in-process） | `chat.js` 旧三态 fallback、`runs.js` 重复 `streamFailure`/snapshot fallback；`runstream.js` 集中规则但页面可绕过。 | 删除第二套状态解释与旧全局导出；保留 DOM/节点表/页面 adapter。 | 设计 §C6 | 故事 67–83；#155–#158 | `runstream.js`/`runstate-guard.js` + 三页；§1 C6 行 + §6 删除审计 | 仅 Chrome/Playwright；无生产半发布；既有 nav.js 404 |

原报告“observations”复核（原始值来自归档 JSON `observations`）：

| 原观察 | 原始值（基线 `6141328`） | 最终复核 |
|---|---|---|
| `go_test` | exit 0, cached packages | ALL.01 `go test ./...`：39 包 `ok`、0 fail、exit 0；`go build`/`go vet` exit 0；`gofmt -l internal cmd tools` 空 |
| `tooling_canvas` | 31 passed in 12.01s | ALL.01 `pytest tests/tooling`：**148 passed in 186.19s**（C1 四票新增后从 31 → 148） |
| `t096_pages` | 10 passed in 0.95s | ALL.01 `pytest tests/f12`：**525 passed in 370.25s**（含 C6 页面/栈套件与 T096） |
| `sync_spec` | exit 0 no drift | ALL.01 `tools/sync_spec.py --check`：`no drift: current source, both copies and synchronized baseline agree`，exit 0 |
| `source_drift_reproduction` | `clean: []`、`modified_existing_upstream_docs_and_contract: []`、`new_upstream_file: upstream_new` | C1.01 新增 23 项覆盖源内容修改/删除/新增与双副本/基线；C1.02 保护同步；C1.03 中断恢复；C1.04 并发锁。详见 `C1.01-check.md`–`C1.04-locking.md` |

## 3. 89 条用户故事覆盖

89 条故事已逐编号核对（`published-spec.md` `## User Stories`，编号 1–89 连续）；`plan.json` 各票
`stories` 字段的并集恰为 1–89，无缺号、无孤立故事。按项分组与主要票映射：

| 故事 | 项 | 票（`plan.json`） | 覆盖主题 |
|---|---|---|---|
| 1、13、84–89 | 整体/整合 | #159 | 六项可追溯、C1 复用、真实阻塞边、本地/提交/远端分离、未运行条件、最终整合无孤儿兼容层 |
| 2–5、12 | C1 | #138 | 检查源/双副本/基线、发现修改与删除、不可核实即失败 |
| 6–8、12 | C1 | #139 | 有效删除传播、关键来源缺失拒绝、准备并验证后替换、失败保留完整副本 |
| 9–10 | C1 | #140 | 中断检测与显式重跑恢复、恢复可重试且保留无效证据 |
| 11–12 | C1 | #141 | 协作进程互斥、路径限制与既有规范化语义保持 |
| 14–19、23–27 | C2 | #146 | 同步/流式共用尝试规则、候选门禁、无幽灵 attempt、原子预留、三主一备、拒绝立即停止、fallback 可用性/兼容、实际费率结算、完整响应后消费保留 |
| 20–22、27、29 | C2 | #147/#148 | 派发前/退避/调用中取消、消费者失败停止、每次流式尝试独立 generation、清理可观察、final 投递失败不撤销成功 |
| 28–29 | C2 | #147 | 仅完整工具调用可作为最终答案；失败草稿不拼接 |
| 30–34、37–44 | C3 | #149/#150 | 三路统一权限与正文验证、同长度替换/截断/size 不符/非法 metadata、删除与撤权、引用打开不依赖当前 embedding、缓存瞬态/确定性分类、回答前复检、最终答案不入检索缓存 |
| 35–36、44 | C3 | #151 | 重建中/失败后旧活动索引可读、激活使旧引用失效、回答返回前来源失效 |
| 45–47、50–55 | C4 | #152 | Agent 图/线性共享执行、显式输入与封存事实、并行 namespace 稳定、节点成功需登记产物、局部决定、排空/唯一提交、waiting checkpoint、optional/skipped/join、恢复复用 |
| 48–49 | C4 | #153 | 固定发布版本与显式绑定、同版本两次逻辑调用独立 |
| 51–56 | C4 | #154 | 调度器权威、取消/失败排空、恢复不虚构完成节点、删除假线性视图 |
| 57–62、66 | C5 | #142/#143 | 规范 Stack、一次真实执行、进程生命周期与清理、环境覆盖隔离、supervisor report ≠ Run 终态、真实执行证据 |
| 63–66 | C5 | #144/#145 | DB/SSE/封存事实共享读取、独立期望、票专用场景保留、临时别名最终删除 |
| 67–71、76–77、82 | C6 | #156 | chat/runview 统一投影、finalizing/取消意图/unknown、dispatched 后观察、迟到响应、认证/缺资源/瞬态保持、删除重复 fallback |
| 67–70、72–77、82 | C6 | #157 | 运行详情状态/子运行/产物/410 快照恢复、安全同源快照、过期 cursor 丢弃、迟到响应 |
| 78–81、83 | C6 | #155/#158 | 依赖缺失提示与暂停、导航/重载保留、harness 真实共享字节、浏览器证据与 Node 证据分离、删除重复 fallback |
| 82–83 | C6 | #158 | 全部页面/运行视图重复 fallback 清零、真实浏览器资产故障与 Node 逻辑分开记录 |

## 4. 工作切片、合并提交与逐票证据

22 个工作切片全部有代码/测试与报告落点。`#142`/`#143` 的实现随交接提交 `e27c6fb`
（`refactor/architecture-c1-c5-handoff-20261109`，本程序基线）进入集成分支，没有单独的集成侧
merge 提交；其余票的集成侧合并提交如下（`git merge-base --is-ancestor <sha> HEAD` 全部 exit 0）。
同一票名下的“刷新”合并在括号内单列。

| 票 | 代码 | 依赖（`plan.json`） | 集成合并提交 | 逐票报告 | 状态 / 限制 |
|---|---|---|---|---|---|
| #138 | C1.01 | — | `b53c91c` | `C1.01-check.md` | passed；WSL 完整 pytest `not_run` |
| #139 | C1.02 | #138 | `a713f44` | `C1.02-sync-protection.md` | passed；真实断电/ACL `not_run` |
| #140 | C1.03 | #139 | `60dc1b0` | `C1.03-interruption.md` | passed；断电/远程 FS `not_run` |
| #141 | C1.04 | #139 | `0a79686` | `C1.04-locking.md` | passed；NFS/9p、断电 `not_run` |
| #142 | C5.01 | — | `e27c6fb`（交接提交） | `C5.01-stack.md` | passed；全量 pytest `not_run` |
| #143 | C5.02 | C5.01 | `e27c6fb`（交接提交） | `C5.02-execution.md` | passed；初跑 skipped/收集失败原样保留 |
| #144 | C5.03 | C5.01 | `0db1b2a`（刷新 `5fc4ae7`） | `C5.03-run-observations.md` | passed；一次外来 runsc 孤儿断言失败，隔离通过 |
| #145 | C5.04 | C5.01–C5.03 | `3eec0c2` | `C5.04-support-contract.md` | passed；j100/j101/t111 `not_run` |
| #146 | C2.01 | — | `107afaf`（刷新 `8946bb9`） | `C2.01-model-complete.md` | passed；真实厂商 `not_run` |
| #147 | C2.02 | C2.01 | `7ae1d40` | `C2.02-model-stream.md` | passed；真实厂商 `not_run` |
| #148 | C2.03 | C2.02 | `06147f4`（刷新 `ce428e7`/`d9699d0`） | `C2.03-cancel-cleanup.md` | passed；真实厂商 `not_run` |
| #149 | C3.01 | — | `b52b8e6` | `C3.01-verified-retrieval.md` | passed（本票范围） |
| #150 | C3.02 | C3.01 | `6703f6a` | `C3.02-verified-reads.md` | passed |
| #151 | C3.03 | C3.02 | `1eb0281` | `C3.03-knowledge-consistency.md` | passed |
| #152 | C4.01 | — | `5dbe372` | `C4.01-agent-context.md` | passed；真实栈 29 + 语义证明 7（非生产） |
| #153 | C4.02 | C4.01 | `6e64648` | `C4.02-callapp-context.md` | passed |
| #154 | C4.03 | C4.02 | `9e37e60` | `C4.03-remove-linear-view.md` | passed |
| #155 | C6.01 | — | `7e7432c` | `C6.01-page-dependency-guard.md` | passed（本票范围） |
| #156 | C6.02 | C6.01 | `2792808` | `C6.02-runstate-view.md` | passed；f03/T032 既有红 |
| #157 | C6.03 | C6.01 | `84b696a` | `C6.03-runs-recovery.md` | passed；f03/T032 既有红 |
| #158 | C6.04 | C6.02/C6.03 | `1e7aac9` | `C6.04-page-contract.md` | passed；Chrome-only；f03/T032 既有红 |
| #159 | ALL.01 | #140/#141/#145/#148/#151/#154/#158 | 本分支（见 §9）+ C4 修复分支 `t/fix-c4-cancel-timing` | `ALL.01-integration.md`、`FIX-C4-cancel-timing.md`、本文件 | `passed`（**scoped local integration verification**：§7 定义的影响完整套件 + 交叉模块 + Go/sync 门禁；**不是**全量 `pytest tests`；C4 取消计时修复后复验 C4.03 3 passed ×5、C4 交叉模块 5 passed；原失败保留为历史，§7 注）；其余门禁/套件通过 |

## 5. 规格/验收场景与原始报告项的对应原则

- 总规格的 Implementation Decisions 按 C1–C6 与“整体”分组（`published-spec.md`），本文件 §1 的
  “设计/删除目标”列逐项对应；Testing Decisions 的“通过最高既有 seam 断言外部可观察结果”在每票
  报告中以真实 HTTP/PG/对象字节/supervisor/浏览器证据落实，未以 helper 名称或调用次数替代。
- Out of Scope 六条（不扩产品功能、不改政策、不合并账本、不新增通用执行计划/观察框架、不以削弱
  断言修复测试、不以本地验收声称真实供应商/生产/物理故障）在 §8 的限制表中逐条体现。
- 原报告 6 个候选的 `deletion` 字段与 design 的 deletion test 一致：C1 比较规则、C2 预留/关闭规则、
  C3 命中复检、C4 共享执行、C5 真实执行 module、C6 共享状态 module 均保留；其外围别名/转发/假视图/
  第二套 fallback 均已删除（§6）。

## 6. 删除目标审计（仓库级）

执行环境：最终代码树（`5fa2eea`），Windows 11，`git grep`（tracked files）。完整命令、分类与
逐行匹配见 `product/reports/architecture/ALL.01-deletion-audit.txt`；可复跑脚本
`ALL.01-deletion-audit.ps1`。

分类：**production** = `product/{internal,cmd,apps,tools,deploy,contracts,migrations}`、根
`tools/deploy/contracts/examples`；**tests** = `product/tests`；**historical** = `product/reports`、
`PROGRESS.md`、`product/docs`、根 `docs/research/*.md`（报告/ADR/规格文字，不是代码）。

| 组 | 目标符号 | production | tests | historical（说明） |
|---|---|---|---|---|
| C2 | `dispatchStream`、`dispatchStreamFallback`、`dispatchModelFallback` | **0 / 0 / 0** | 0 / 0 / 0 | 14 / 13 / 0（历史报告与设计文字） |
| C2 | `dispatchModel`、`dispatchFallback`（统一驱动内的内容处理/fallback 派发） | 3 / 4（`service.go` 内容处理入口；`attempt.go` session 方法；非旧生命周期循环） | 0 / 0 | 11 / 13 |
| C3 | `readChunkText`、`validateCachedItems`、`cacheHitReadFailureReason`、`cacheDiscardDecision`、`matchesSealedBody`、`cacheBodySHA256` | **0 / 0 / 0 / 0 / 0 / 0** | 0 / 0 / 0 / 0 / 0 / 0 | 33 / 21 / 14 / 16 / 19 / 13 |
| C4 | `graphLinearView`、`linearRun.available`、`linearRun.skip`、`graphNodeView.start`、`graphNodeAttempt.started` | **0 / 0 / 0 / 0 / 0** | 0 / 0 / 0 / 0 / 0 | 39 / 11 / 5 / 9 / 7 |
| C5 | `T024Stack`、`T029Stack`、`T031Stack`、`T033Stack`、`run_linear_once` | **0 / 0 / 0 / 0 / 0** | 0 / 0 / 0 / 0 / 0 | 9 / 8 / 8 / 8 / 8 |
| C6 | `runStream*` 旧全局、`*RunStateDependencyUnavailable` helper | **0 / 0** | 0 / 2（仅 `tests/f12/test_c6_04_page_contract.py` 的“断言其不存在”契约测试） | 93 / 18 |

补充证明：

- C2 旧生命周期循环：`internal/capabilities` 仅存统一驱动的一个
  `for attempt := 1; attempt <= maxPhysicalAttempts; attempt++`（`attempt.go:274`）；`dispatchModel`
  为同一私有 session 的同步完整响应内容处理（`service.go:1038`），`dispatchFallback` 为
  `modelAttemptSession.dispatchFallback`（`attempt.go:428`）。
- C5 AST 契约：`python reports/architecture/C5.04-contract-check.py --phase all` →
  `callers=0`、`definitions=0`、exit 0（`C5.04-contract-check.py` 覆盖 14 个支持模块的 55 项删除
  契约）。仓库级 `git grep` 同名目标亦为 production/tests 0（上表）。
- 历史 prose 命中是设计/报告/ADR 的记录（例如设计文档明确列出待删符号），不是残留实现；本审计
  不把历史文字当代码失败，也不因历史文字存在而放宽“production/tests 为 0”的结论。

## 7. 集成门禁与最终套件（`product` 目录，ALL.01）

环境：Windows 11（NT 10.0.26200）、`D:\Python\python.exe` Python 3.12.2 / pytest 9.0.3、
Go `E:\orchara-toolchains\go\bin\go.exe`（共享 `GOCACHE`/`GOMODCACHE`）、隔离 PostgreSQL
`127.0.0.1:55432`、WSL2 `Ubuntu-24.04` + runsc。所有 pytest 经 `E:\Orchara-notes\test.ps1`。

**“受影响完整套件 / 产品集成回归”的确定含义（不是 full-suite）：** tooling **148**、f10
**114**、f11 **85**、f12 **525**、C2 交叉模块 **19**、C4 交叉模块修复后 **5**、T113 **6**、
Go `go test ./...` **39 包 ok**、`go build`/`go vet`/`gofmt`/`sync_spec --check`。完整
`pytest tests` 仍为 **`not_run`**；`#159` 的 `passed` 只指 **scoped local integration
verification**（上述范围），不是全量产品验收，也不称 full-suite。

**六项自身状态（不写“六项全部 passed”）：** C1 `partial`（Linux pytest/断电/ACL `not_run`）、
C2 `partial`（真实厂商 `blocked`/`not_run`）、C3 `partial`（真实 embedding/模型/规模
`not_run`）、C4 `partial`（`-race`/部分真实组合 `not_run`）、C5 `partial`（全量 pytest/
journeys/Linux 隔离 `not_run`）、C6 `partial`（仅 Chrome/`page.route` 故障注入）。

| 检查 | 命令 | 结果 | 原始输出 |
|---|---|---|---|
| Go 测试 | `go test ./...` | **39 包 ok / 0 fail / exit 0（13 个 no-test 包）** | `ALL.01-go-test.txt` |
| Go 构建 | `go build ./...` | exit 0 | `ALL.01-go-build.txt` |
| Go vet | `go vet ./...` | exit 0 | `ALL.01-go-vet.txt` |
| 格式 | `gofmt -l internal cmd tools` | 空输出、exit 0 | `ALL.01-gofmt.txt` |
| 规格同步门禁 | `python -X utf8 tools/sync_spec.py --check` | no drift、exit 0 | `ALL.01-sync-check.txt` |
| 工具/契约 | `pytest tests/tooling` | **148 passed in 186.19s** | `ALL.01-pytest-tooling.txt` |
| 知识全量 | `pytest tests/f10` | **114 passed, 1 warning in 804.44s** | `ALL.01-pytest-f10-full.txt` |
| 删除/一致性 | `pytest tests/f11` | **85 passed in 648.44s** | `ALL.01-pytest-f11.txt` |
| 页面/栈 | `pytest tests/f12` | **525 passed in 370.25s** | `ALL.01-pytest-f12.txt` |
| C2 交叉模块 | `pytest tests/f06/test_c201_model_attempts.py tests/f06/test_c202_model_stream.py tests/f06/test_c203_cancel_cleanup.py` | **19 passed in 115.82s** | `ALL.01-pytest-c2-focus.txt` |
| C4 交叉模块 | `pytest tests/f04b/test_c4_02_callapp_context.py tests/f04b/test_c4_03_scheduler_regressions.py` | 原 **1 failed, 3 passed in 72.67s**（历史；隔离复跑 1 failed in 30.85s，见 §7 注）→ 第一次修复 **5 passed in 77.15s**；第二次复核强化 drain 契约后 C4.03 **3 passed ×5**、focus **5 passed** | 历史 `ALL.01-pytest-c4-focus.txt`；第一次 `FIX-C4-*`；第二次 `REVIEW-C4-run1..5.txt`、`REVIEW-C4-focus.txt` |
| C3 缓存对照 | `pytest tests/f13b/test_t113_cache_comparison.py` | **6 passed in 56.16s** | `ALL.01-pytest-t113.txt` |
| 既有红复核 | `pytest tests/f03/test_t032_pages.py -q` | **1 failed, 4 passed in 27.51s**（既有红，不计通过） | `ALL.01-f03-t032-preexisting.txt` |

**C4 交叉模块失败说明（历史记录，保留原文；当时的运行不计为通过）：** `tests/f04b/test_c4_03_scheduler_regressions.py::test_graph_cancellation_drains_the_started_sibling_and_commits_one_exit`
首次运行失败于 `exit_evidence["started"] is True`（`sandbox=None`），隔离复跑失败于
`exit_evidence["sandbox_started"] is True`；两次都是取消意图在 runsc 取得首个 sandbox
execution unit/PID 之前写入（测试在首心跳后写取消），属已知的冷启动取消计时问题（C4.03
合并记录 §8 已标注同类竞态）。未修改、未跳过、未放宽该断言；原始输出
`ALL.01-pytest-c4-focus.txt`（`1 failed, 3 passed in 72.67s`）与
`ALL.01-pytest-c4-focus-rerun.txt`（`1 failed in 30.85s`）。建议修复：等首个 sandbox
execution unit/PID 出现后再写取消意图（或等价确定性触发器），由复查后的实现者承接；本票
不自行改测试以取得绿色。

**C4 修复复验（2026-11-09，分支 `t/fix-c4-cancel-timing`，基线 `108fa26`；仅改真实栈测试）：**
开始排空用例以真实 `Popen` supervisor 启动，有界轮询 `run_attempts.execution_unit_id` 与
`t033.runsc_list()`（sandbox id 按 `NewSandboxID` 从执行单元派生），等两个分支的 runsc 容器
都真的出现在列表后，才用 plain SQL 写 `cancel_requested_at`，随后解析 report 并保留
`started`/`sandbox_started`/唯一 attempt+exit/`reported_result IS NULL`/join+end skipped/
无 orphan 的权威断言；旧首心跳触发器场景保留为独立用例，不要求 `started=true`。
被排空兄弟的 `node.status` 终态提交若与 fail-fast 取消竞态会被中止（supervisor 记录
`... was not recorded: ... context canceled`），事件投影可停留 `running`；这是可观测性
投影的既有边界，不是 Run/attempt/exit 权威（确定性终态由 Go seam
`TestGraphRunCancellationDrainsStartedSiblings` 覆盖）。**第二次复核更正：原“只放宽为
‘两分支都启动 + 至少一个终态 + 至少一个 failed’”是弱化，已改为显式 drain 契约**——两分支
都启动；每个分支终态（`failed`/`succeeded`）或非终态但其 per-node runsc 容器/工作区确实
消失；至少一个 `failed`；join/end 各一次 `skipped`；无 join 结果；1 attempt/1 exit；
`reported_result IS NULL`；无 orphan。开始排空触发器要求两个分支容器 `STATUS=running` 且
PID>0 后才写取消；**不再断言 `exit_evidence.started`/`sandbox_started` 为 true**——该字段是
supervisor 认证时间戳，取消心跳可能在 `sandbox.Start` 返回前落地，真实启动证据是
pre-cancel running 容器 + 事后无 orphan（`REVIEW-FIX-summary.md` §B）。复验：C4.03
**3 passed ×5**（38.62/42.35/41.49/41.12/40.53s）、C4 交叉模块 **5 passed in 74.37s**；
Go/sync 门禁全部 exit 0。记录 `FIX-C4-cancel-timing.md`、`REVIEW-C4-*.txt`。原失败日志未改写。


## 8. 残留限制与未运行条件（诚实清单）

| 条件 | 状态 | 影响范围 / 说明 |
|---|---|---|
| 真实供应商模型（端点/凭据/配额/外发批准） | **blocked / not_run** | C2、C3 模型/embedding 相关；全部证据来自 dev stub 与本地 loopback fake HTTP provider；ADR 0035 未改 |
| 生产部署与远端集成 | **not_run** | 全部代码与证据仅在本地分支；未 push、未部署；`integration/architecture-c1-c6` 只在本机工作树验证 |
| 浏览器 | **partial**（仅 Chrome 157 headless + Playwright） | C6；未覆盖其它浏览器、真实布局/媒体查询、真实 EventSource 断线；故障用 `page.route` 注入 |
| 文件系统/平台 | **partial**（Windows NTFS + WSL2 本地文件系统） | C1 锁/中断、C3 瞬态；NFS/9p、跨卷、只读挂载、真实断电/设备损坏 `not_run` |
| 完整 `pytest tests` | **not_run** | 本票按 BRIEF §6/§7 运行**上述确定含义**的影响完整套件（tooling/f10/f11/f12 + C2/C4/T113 交叉模块 + Go/sync 门禁）；其余 f00b/f02b/f03/f04/f05/f06/f07/f08/f09/f13b 各票已跑目标套件，未在本树重跑全量；该范围**不称/不当 full-suite** |
| `go test -race` | **not_run** | 本机 Go 无 cgo（`-race requires cgo`）；并发正确性由真实栈与 `-count=10` 佐证 |
| C4 取消排空的 `node.status` 投影 | **已知观测竞态**（非验收缺陷） | 被 fail-fast 取消的兄弟分支，其终态提交若在取消落地时在途会被中止（`... was not recorded: ... context canceled`），投影可停留 `running`；权威终态由唯一 exit/join+end skipped/无结果与 Go seam `TestGraphRunCancellationDrainsStartedSiblings` 覆盖 |
| 物理断电/半发布/GPU/真实远程知识库 | **not_run** | 设计明确不在本程序范围；C1 只保证可检测/可恢复的事务记录，不承诺断电持久性 |
| 既有页面缺陷 | 已知、未修 | `/assets/nav.js` 无静态路由（真实 404）；f03/T032 `location.search` 断言与 T095 实现冲突（pristine 基线可复现）。均未计为通过、未跳过 |
| 证据历史中的本地开发凭据 | HEAD 脱敏；本次新增证据亦在提交前脱敏 | 见 `SECURITY-redaction-note.md`；`ALL.01-pytest-c4-focus*.txt`、`ALL.01-f03-t032-preexisting.txt`、`REVIEW-*.txt` 中的本机 DSN/一次性签名已替换为 `<REDACTED>`；历史提交未改写。**安全说明：分支仅本地，含脱敏前凭据历史；不要 push；合并/发布只用 `git merge --squash`（或 squash/cherry-pick 最终树）。** |

## 9. 合并与最终 tip

- 程序基线：`5fa2eea`（`integration/architecture-c1-c6`，ALL.01 分支 `t/all.01-integration` 起点）。
  ALL.01 开始前 `git merge-base --is-ancestor integration/architecture-c1-c6 HEAD` = 0，集成分支
  未再前进（`git rev-list --count 5fa2eea..integration/architecture-c1-c6` = 0）。
- 本票证据提交（本文件、`ALL.01-integration.md`、`ALL.01-*` 原始输出、PROGRESS 归一化）：
  **`0be7ff2`**（完整 SHA 见 `git log`）。其后的文档提交仅回填本 tip 并把新增文件统一为仓库的
  LF 行尾约定，不改变代码、测试结果或断言。
- 逐票合并提交：见 §4；`#142`/`#143` 为交接提交 `e27c6fb`，无独立集成 merge。
- C4 修复：后续分支 `t/fix-c4-cancel-timing`（worktree `E:\Orchara-wt\FIX`，基于集成 tip
  `108fa26`）只改 `tests/f04b/test_c4_03_scheduler_regressions.py` 与本组记录；证据
  `FIX-C4-cancel-timing.md`、`FIX-C4-*.txt`。原 C4 失败日志保留为历史，未改写。
- 远端状态：**未 push、未创建 PR、未关闭/编辑任何 Issue**；`#159` 与各叶子票由 orchestrator 在
  代码评审后关闭。

## 10. 维护者签收检查

- [x] 原报告 6 个候选 + 5 项 observations 全部映射（§2）。
- [x] 89 条故事编号连续且 `plan.json` 故事并集 = 1–89（§3）。
- [x] 22 个工作切片均有合并提交/交接提交、报告与状态（§4）。
- [x] 六项删除目标 production/tests 均为 0；历史 prose 单独统计（§6）。
- [x] 六项叶子验收与 ALL.01 集成门禁在最终代码树运行并留存原始输出；C4 交叉模块原失败已单列并
      保留历史，第二次复核强化 drain 契约后复验 C4.03 3 passed ×5、C4 交叉模块 5 passed（§7）。
- [x] 六项自身按实际限制记为 `partial`；`#159` 的 `passed` 明确限定为 scoped local
      integration verification，完整 `pytest tests` 仍 `not_run`（§7/§8）。
- [x] 未运行/blocked/partial 与既有红单列，未写成通过（§8）。
- [ ] orchestrator：先合并 C4 修复分支 `t/fix-c4-cancel-timing`，再合并本分支并回填最终 merge tip，
      关闭 #138–#159，并确认 §8 的远端/供应商条件仍按 blocked/not_run 记录。
