# F09 T063 验收：运行时按上下文预算召回当前应用的已确认记忆

- 任务：F09 / GitHub Issue #64（票 `T063`，规划 ID F09.03），分支
  `t/t063-runtime-recall`（worktree `E:\orchara-worktrees\t063`）；父门禁 #53–#68
  集成批次（`integration/f13a-f15a-f09-f10`，基线 `ddaeb46`）；合并提交 `0c8a995`
  （父 `a36c81d` = 本票 docs 提交；父 `eb30e1e` = 集成 tip/T067 检索缓存）。
- 状态：**`passed`**（本票范围 = 真实 PostgreSQL 的 `MemorySource` + 确定性预算取舍 +
  唯一生产接点的真实 run 证明 + 按条来源/截断 trace + 失败关闭与恢复断言 +
  propose 身份核实）。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP/mTLS +
  真实 sealed 对象字节 + 真实 WSL2/runsc 沙箱 + 真实 claim/result/exit 协议；无 mock、
  无跳过。模型供应商仍是既有文档化 dev stub（stub，见 §5 分报）。
- 边界（如实）：检索打分/嵌入与检索缓存、`memory_embeddings` 属 **T067**；从聊天自动
  抽取记忆并接入 propose 属 **F04A 抽取策略**（本票只证明 worker-only propose 的传输
  身份可用，producer 本身 `not_run`）；物理副本清理属 **F07**；浏览器 DOM 驱动 `not_run`
  （与既有页面票同一边界）；全量 `pytest tests` 由 orchestrator 在合并后运行。

## 0. 工作记录（简短）

1. 读 BRIEF、`briefs/T063.md`、票面、T054/T058 报告、`internal/memory/**`、
   `internal/context/context.go` 与其 `build_test.go`、迁移 0033/0036、ADR 0009、
   `internal/presetexec/{agent.go,agentcontext.go,presetexec.go}`、
   `cmd/supervisor/exec.go`、`internal/api/{internal.go,noderesults.go}`、
   `internal/execution/noderesults.go`，以及 T043/T047 的真实 run 夹具
   （`tests/support/{t033,t041,t043,t054,t058}.py`、`tests/f06/test_t047_context.py`）。
   先确认 seam 已存在：`MemorySource`/`Memory`/`NoMemorySource`、
   `memory_state`/`optional_memory` trace、`GET /api/v1/runs/{run_id}/node-results`
   暴露的 `context-trace` artifact、唯一注入点 `presetexec/agent.go`。
2. TDD 垂直切片（每片先红后绿）：
   - 上下文选择（Go 单测，红色 = `Memory.Revision`/`memory_id`/`memory_tokens` 等字段
     尚不存在）：记忆预算 = 模型预算 1/4、`confirmed_at DESC, id DESC` 排序、超预算按
     最低相关度先截断（`optional_memory` + memory id/revision/tokens）、用户否定/审批
     记忆受保护不截断、整体上下文压力按条丢弃、同一请求两次构建逐字节一致；
   - 真实召回（Python 真实栈，红色 = 首轮 `memory_state=not_run`，且 run 本身成功）：
     本用户+本应用四个来源不同的记忆被包含；已删/待定/过期/其他应用/其他用户/旧修订
     不出现；`memory_state=included`、`memory_tokens>0`、逐条 source 带
     id/revision/confirmed_at/candidate/source run；over-budget 子预算 512 下否定记忆
     保留、其余按条截断；sealed 字节缺失 → `unavailable` 且不注入文本，恢复字节后下一
     次 run 正常召回；他人不可读 node-results（404）；propose 的 supervisor/maintenance
     403、worker 201；
   - 既有 F06/T047 行为随之更新：无记忆 owner 的 `memory_state` 由 `not_run` 变为
     `empty`（存储已接入且真实读过），该文件 2 用例复跑通过。
3. 跑门禁（§4），写 ADR 0009 §12、本报告、PROGRESS；按 feat/test/docs 提交；先合并
   `integration/f13a-f15a-f09-f10` 再在合并树上复跑；未 push、未关 issue、未改
   `docs/spec/**`/`contracts/**`。

## 1. 交付内容

### 1.1 `internal/memory/recall.go`：真实 PostgreSQL 召回源

- `RecallSource{DB, Objects}` / `NewRecallSource` 实现
  `agentcontext.MemorySource.ConfirmedMemories(ctx, ownerID, applicationID)`；
- 单条 SQL、单条 WHERE：`owner_id = $1`、`application_id = $2`、`deleted_at IS NULL`、
  `JOIN artifacts` 只取 `state='ready' AND deleted_at IS NULL` 的当前
  `body_artifact_id`，`LEFT JOIN memory_candidates` 只取来源链
  `candidate_id → source_run_id`；`ORDER BY confirmed_at DESC, id DESC LIMIT 200`
  （`RecallRowLimit`，防止病态账户让单轮读取无界；真正的取舍在上下文预算）；
- 保护感知的越界扫描（RV3 复审修复，见 §9）：新近 200 行仍照旧读取；该页恰好满 200 行
  时，再按 `(confirmed_at, id)` keyset 以 `RecallScanBatch=200` 行一批、最多
  `RecallScanRowLimit=2000` 行（10 批）扫描同 owner+application 的旧行，命中
  `agentcontext.IsProtectionSensitive` 的行加入返回集；一条旧正文读不出仍整体 fail
  closed。返回值由 `[]Memory` 扩为 `MemoryRead{Rows, Scan}`，把扫描边界带回 trace；
- 候选永远不是召回源（pending/expired 没有 `memories` 行；confirmed 只经由它创建的
  memory 行读取）；记忆本身没有过期列，过期是候选生命周期事实；
- 每条正文用 `readSealedBody`（从 T054/T058 的 `Service.readBody` 抽出，行为逐字节
  不变）读取并校验 sha256；**一条读不出 → 整体返回错误**，上下文 builder 记
  `memory_state=unavailable` 且不注入任何记忆（部分记忆视图与“用户没说过”不可区分）；
- scope 空/UUID 非法 → 错误（fail closed），绝不退化成“读全部”。

### 1.2 `internal/context`：确定性记忆预算与逐条来源

- `Memory` 扩展 `Revision`/`ConfirmedAt`/`CandidateID`/`SourceRunID`（只进 trace，
  不进模型文本）；`Trace` 新增 `memory_tokens`、`memory_budget_tokens`；`TraceSource`
  新增 `memory_id`/`memory_revision`/`memory_confirmed_at`/`memory_candidate_id`/
  `memory_source_run_id`；`Truncation` 新增 `memory_id`/`memory_revision`；
  `MemoryState` 新增 `truncated`；
- 选择规则可解释：记忆子预算 = `budget / 4`（`MemoryBudgetDivisor`），新近度即相关度
  （本构建无 pgvector 嵌入打分；检索打分属 T067），按 `confirmed_at DESC, id DESC`
  依次纳入直到编码后消息超过子预算；未纳入行按**最低相关度优先**写入
  `truncations[]`（`reason="optional_memory"`、memory id/revision、tokens_removed）；
  `memory_tokens` 是实际花费，`memory_budget_tokens` 是使用的子预算；
- 预算算术是线性的：每行只 canonical 一次，消息由
  `{"confirmed_memory":[` + 逗号连接的 item + `]}` 精确构造（有 pinning 单测断言与
  `canonical({"confirmed_memory":[...]})` 逐字节一致，含引号/换行/多字节文本），
  选择与边际 token 计算均为 O(n)，不会因 200 行上限拖慢每次模型轮次；
- 用户否定/审批参数：固定封闭词表 `memoryProtectedMarkers`（`must not`/`do not`/
  `never`/`exclude`/`approval`/`不要`/`不得`/`禁止`/`审批` 等；ASCII 按词边界匹配，
  避免 `whenever` 命中 `never`）标记为受保护；选择时优先纳入、预算截断永不丢；
  整体上下文超预算时按条丢非受保护记忆（最低相关度优先），只剩受保护记忆仍放不下则
  走既有 `context_budget_exhausted`（与受保护最小输入同一条规则，绝不静默裁剪）；
  该词表经 `IsProtectionSensitive` 导出并与召回源共用（§9），不复制第二份；
- trace 只有标识/原因/计数/键路径，**没有记忆正文**（§2 AC3 用例断言）。

### 1.3 唯一生产接点与运行身份

- `presetexec.Deps` 新增 `Memory agentcontext.MemorySource` 与
  `RunIdentity RunIdentity`；`RunScope{OwnerID, ApplicationID}` 由
  `cmd/supervisor exec` 的只读数据库身份读
  `runs.owner_id + execution_snapshots.application_id`，只用于记忆 scope，不来自
  manifest/body；
- `internal/presetexec/agent.go` 的 `agentcontext.Build` 调用仍是唯一注入点：
  `Memory: memorySource`，`Scope.OwnerID/ApplicationID` 由身份补齐；身份读取失败时
  只降级召回（trace `unavailable`），不让可选上下文失败整个 run；
- `internal/config.SupervisorExecConfig.ObjectDir` 新增（`ORCHARA_OBJECT_DIR`，可选）：
  与 API 共用同一 sealed 根；未配置时召回读得到行但读不到字节 → `unavailable`，
  绝不伪造文本；`cmd/supervisor exec` 用 `memory.NewRecallSource(db, objects)` 装配。

### 1.4 没有新迁移、没有新端点

- 0033 的 `memories_scope(owner_id, application_id) WHERE deleted_at IS NULL` 已覆盖
  召回查询；`memory_embeddings` 仍不创建（PG 16.4 无 pgvector，T067 拥有）；
  迁移号未被本票占用；
- 读取面沿用既有 `GET /api/v1/runs/{run_id}/node-results` 定位 `context-trace` +
  普通 owner-only artifact content 路由；未新增/修改契约端点，未改 `/runs` 页面。

### 1.5 T054 的 propose 身份问题（核实结论，不掩盖）

- 事实核实：F04A 执行器并非“只持 supervisor 证书”。`cmd/supervisor exec` 同时持有
  supervisor 与 worker 两套 mTLS 身份；`InvokeCapability` 与 `RecordNodeResults`
  都用 `worker` 容量 + `x-internal-role: worker` 发起。T063 的真实 run 正是经该
  worker-only 的 node-results 路由写入 trace 并成功，可执行证据在 §2 AC4；
- 新增真实测试记录“精确拒绝”：用 supervisor/maintenance 角色调用
  `POST /internal/v1/runs/{run_id}/memory-candidates` → 403 `forbidden`；worker 角色
  → 201，且服务端从 run 推导的 application 与 run 的 snapshot 一致；
- 结论：worker-only propose 对“接入 agent loop 的生产者”**已有可用传输身份**，不需要
  拆分 supervisor/worker 进程；本票**未实现**自动抽取并 propose 的产品行为（属 F04A
  抽取策略，且票面明确不默认从完整聊天抽取永久事实），故 producer 自身标 `not_run`，
  不是 `blocked`。ADR 0009 §12.8 记录同一结论。

## 2. AC 核对

| AC | 结果 | 证据（真实栈，非 stub） |
|---|---|---|
| 1. 只召回本用户本应用已确认 active 记忆 | proven | `test_the_agent_recalls_only_this_users_this_applications_current_confirmed_memories`：同一 owner 在本应用用真实 `POST /memories` + 候选确认写入 4 条记忆；trace 的 memory sources 集合恰为这 4 个 memory id；同一 owner 另一应用、另一 owner 的记忆（真实 API 写入）不在 sources、不在 trace、不在 sealed 模型请求；memory rows 全部 `deleted_at IS NULL`（RecallSource 查询条件），`state=active`/`confirmed` 由存储事实保证 |
| 2. 未确认/过期/已删/其他应用内容不进模型 | proven | 同一用例：pending 候选、`state=expired` 候选、真实 `DELETE` tombstone 的记忆、另一应用、另一用户、以及被 PATCH 更新前的旧修订文本，全部断言不出现在 `sources`/trace/模型请求中；更新后文本出现（current revision only）；读取路径是真实 `POST/PATCH/DELETE` + 真实候选确认 |
| 3. 来源及 token 取舍可说明 | proven | trace 每条 memory source 带 `memory_id`/`memory_revision`（与 PG 行一致）/`memory_confirmed_at`（与 PG `confirmed_at` 同瞬）/`memory_candidate_id`+`memory_source_run_id`（候选确认来源链）与边际 token 数；`memory_tokens`/`memory_budget_tokens` 记录花费与子预算；`test_over_budget_recall_truncates_lowest_relevance_memories_and_keeps_negations`：3072−1024 预算下 `memory_budget_tokens=512`、2 条被纳入、5 条按最低相关度先截断（首条=最旧 bulk），每条 truncation 带 memory id/revision/tokens_removed；trace 文本泄漏由 `assert_no_content_leak` 断言不存在。RV3 复审修复追加（§9）：`memory_scan{scanned,limit,protected_found,truncated}` 与 `memory_scan_truncated`（带 scanned/scan_limit）记录越界扫描边界，见上述两个新用例 |
| 4. 真实端到端入口 + 失败/授权/重试恢复边界，非单层 mock | proven | 真实 `debugDraft` 提交 → 真实 `cmd/supervisor exec --once` claim → 真实 python/agent 节点（WSL2/runsc + T041 capability 账本）→ 真实 result/exit；失败关闭：删除 sealed 字节后 `memory_state=unavailable`、`memory_tokens=0`、无 memory 消息/文本；恢复：原字节写回后**下一次真实 run** `included` 且正文出现；当前授权：另一用户读 `GET /runs/{id}/node-results` → 404 `resource_not_found`；propose：supervisor/maintenance 403、worker 201；无单层 mock |
| 5. 记录版本/hash/命令/原始结果/profile；stub/real 分报 | proven（本报告 §4/§5） | 基线/合并提交、输入 SHA-256 前 16 位、环境 profile、逐条门禁原始尾巴在 §4/§5；stub/real 分报：模型供应商为既有 dev stub（未改），其余全部 real；无 `blocked`/`not_run` 项被写成通过（§6 单列） |

## 3. 后续票 seam

| 票 | 本票已留的接口 |
|---|---|
| T067（#67） | `RecallSource` 每次直接读 PG，不缓存；Trace 的 `memory_id`+`memory_revision` 与 T058 的 `policy_epochs('memory:<owner>:<app>')` 一起作为检索缓存的键/守卫；`memory_embeddings` 仍未创建（无 pgvector）；预算常量与 `memory_tokens` 可直接复用 |
| F07（删除治理） | `domain` 一致：tombstone 立即停召回；sealed 字节被清理后召回 fail closed（`unavailable`），F07 修复/恢复字节后下一轮读取即恢复（本票已真实验证该恢复路径）；`deletion_jobs.remaining_scopes` 的 `embedding` scope 仍由 T067 消费 |
| F04A（producer） | `presetexec.Deps.RunIdentity` + `RunScope` 已把 run 可信 scope 暴露给 agent loop；worker-only propose 的传输身份已验证（worker 容量 + `x-internal-role: worker`）；自动抽取策略与 propose 调用仍属 F04A |
| 页面/读面 | `GET /api/v1/runs/{run_id}/node-results` + artifact content 是 trace 的正式读面；本票未加端点/页面 |

## 4. 门禁命令与原始输出

环境：Windows 10 + PowerShell 7；`go version go1.27.3 windows/amd64`；
`Python 3.12.2`；`node v24.13.0`；PostgreSQL **16.4**（用户态集群 `127.0.0.1:55432`）；
真实 WSL2 `Ubuntu-24.04` + runsc 沙箱。主机在本批集成期间同时运行多个实现/测试作业
（orchestrator 曾为释放 CPU 终止一个 pytest 进程），耗时只能作为负载参考。

```text
$ go build ./...
(no output, exit 0)

$ go vet ./...
(no output, exit 0)

$ go test ./...
(all packages ok; no FAIL; exit 0)

$ gofmt -l .
(no output, exit 0)

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest   (exit 0)

$ python -m pytest tests/f09 -q          # 本票树（合并前）首个整跑，环境抖动
1 failed, 50 passed in 622.54s (0:10:22)
  # 失败：test_over_budget_recall_... 的沙箱 sandbox_failed
  # （"heartbeat: context canceled"），记忆断言之前；见 §6 环境噪声

$ python -m pytest tests/f09 -q          # 本票树 背靠背第 1 轮（绿）
51 passed in 671.02s (0:11:11)

$ python -m pytest tests/f09 -q          # 本票树 背靠背第 2 轮（绿）
51 passed in 700.60s (0:11:40)

$ python -m pytest tests/f09 -q          # 合并后最终树（0c8a995）第 1 轮
51 passed in 511.80s (0:08:31)

$ python -m pytest tests/f09 -q          # 合并后最终树 第 2 轮（环境抖动）
1 failed, 50 passed in 569.44s (0:09:29)
  # 失败：test_missing_memory_bytes_... 的沙箱 sandbox_failed；单独复跑通过

$ python -m pytest tests/f09 -q          # 合并后最终树 第 3 轮（背靠背绿）
51 passed in 643.37s (0:10:43)

$ python -m pytest tests/f10 -q          # 共享文件可能被触碰的邻接套件（合并后）
57 passed in 679.35s (0:11:19)

$ python -m pytest tests/f06/test_t047_context.py -q   # 行为更新后的邻接回归（合并后）
2 passed in 48.14s
  # 合并前该文件首次运行 1 个用例遇同类 sandbox_failed，单独复跑通过

$ python -m pytest tests/tooling -q
21 passed in 7.25s
```

套内细分：`tests/f09` = T054 25 + T058 22 + T063 4（`test_t063_recall.py`）；
T063 的 4 个用例含 4 次真实 agent run（其中恢复用例 2 次）与 1 次真实 claim/propose。
确定性说明（BRIEF §6.1）：记忆选择只依赖存储的 `confirmed_at`/`id` 与预算算术，
不依赖墙钟窗口；over-budget 断言的 token 阈值有 >100 token 余量，不押边界；测试无
sleep；合并后同一棵树上得到两轮绿色整跑（511.80s / 643.37s），中间一轮的
1 个 `sandbox_failed` 是本批共享 WSL2/runsc 沙箱的负载抖动（§6），不是记忆断言失败。

### 合并（BRIEF §7.6）

```text
$ git merge integration/f13a-f15a-f09-f10
合并提交 0c8a995（父 a36c81d = 本票 docs 提交；父 eb30e1e = 集成 tip/T067 检索缓存）
自动合并，无冲突；本票文件与 T067 的 knowledge/检索缓存文件不重叠

# 合并后的最终树
$ python -m pytest tests/f09 -q
51 passed in 511.80s (0:08:31)
$ python -m pytest tests/f09 -q
1 failed, 50 passed in 569.44s (0:09:29)   # sandbox_failed 抖动，见 §6
$ python -m pytest tests/f09 -q
51 passed in 643.37s (0:10:43)
$ python -m pytest tests/f10 -q
57 passed in 679.35s (0:11:19)
$ python -m pytest tests/f06/test_t047_context.py -q
2 passed in 48.14s
$ go build ./... ; go vet ./... ; gofmt -l . ; go test ./... ; python tools/sync_spec.py --check
全部 exit 0；gofmt 空；“no drift: spec copy and contract snapshot match the manifest”
```

## 5. 输入与版本

| 输入 | 版本 / SHA-256（前 16 位） |
|---|---|
| 基线 / 集成 tip | `ddaeb46`；合并时集成 tip `eb30e1e`（T067 检索缓存） |
| `internal/context/context.go` | `AECD1B00A4890AFA…` |
| `internal/context/build_test.go` | `2201E0364D5DFD90…` |
| `internal/memory/recall.go` | `6C3CD88575AADF73…` |
| `internal/memory/service.go` | `13ADD1B036DDE5D6…` |
| `internal/presetexec/agent.go` / `presetexec.go` | `494A9E46AF9DCD63…` / `0F72EFE95F02315B…` |
| `cmd/supervisor/exec.go` | `68C2D21FC41E3EC7…` |
| `internal/config/supervisorexec.go` | `4D14887A08068C20…` |
| `tests/f09/test_t063_recall.py` / `tests/support/t063.py` | `D8D31094961E5E08…` / `36337CABC791D5D9…` |
| `tests/f06/test_t047_context.py` | `1EA7F6FA5A104527…` |
| `docs/adr/0009-f09-memory.md`（§12 增补） | `04E921C5FB0613A5…` |
| 迁移 | **none**（本票不新增迁移；0033/0036 未改） |

真实/stub 分报：**记忆存储、对象字节、PG、二进制、mTLS、HTTP、沙箱、claim/result/exit
全部 real**；唯一 stub 是既有文档化的 dev 模型供应商（`provider=stub`，T041 报告已声明），
本票的召回证明不依赖模型生成内容，只依赖封存的真实请求参数。本票没有因缺条件而
`blocked` 的项。

## 6. `not_run` / 债务（如实）

| 项 | 原因 / owning ticket |
|---|---|
| 从聊天自动抽取记忆并调用 propose | **F04A 抽取策略**；本票只验证 worker-only propose 的传输身份（403/201）并保持召回只读，票面明确不默认抽取永久事实 |
| 检索打分/嵌入、`memory_embeddings`、检索缓存 | **T067**；本集群 PG 16.4 无 pgvector，本票用可解释的新近度，不建死 schema |
| 物理副本清理 / `delete_content` / deletion job 生命周期 | **F07**；T063 依赖 T058 的 tombstone 立即停召回，并在字节缺失时 fail closed |
| 浏览器 DOM 驱动 / `/runs` 页面渲染新字段 | `not_run`，与 T023/T027/T029/T039/T047/T054/T058 同一边界；trace 的正式读面是 node-results + artifact content，未改页面 |
| 真实厂商模型/供应商 | 沿用既有 dev stub；本票不改变模型层 |
| 全量 `pytest tests`（约 67 分钟） | BRIEF §3 明令不跑；由 orchestrator 在合并后运行 |

**观察到的环境噪声（如实记录，非本票断言）**：本票开发期间主机同时运行多个
implementer 的套件（orchestrator 曾为释放 CPU 终止过一个 pytest 进程）。在此负载下出现
四次 `sandbox_failed`（`"heartbeat: context canceled"` / lease lost）：三次在 T063
（include、over-budget、missing-bytes 三个不同用例各一次），一次在既有 F06 的
`test_t047_context.py`（本票只改其中一个断言）；四次都发生在 WSL2/runsc 沙箱内、
记忆断言之前，对应用例单独复跑或下一轮整跑均通过。这是本批已知的共享沙箱 lease 抖动：
`reports/f04a/T043-agent-node.md`（`sandbox_failed` heartbeat context canceled，单独复跑
通过）与 `reports/f06/T048-connectors.md` §4.4（"依赖 WSL2/runsc 的用例在两次全量运行中
分别出现一次 `sandbox_failed`（lease lost），两者单独重跑均通过，判定为沙箱环境抖动"）
记录了同类现象。本票未修改沙箱/心跳路径，也没有依赖亚秒/墙钟窗口的断言；同一合并树的
两轮绿色整跑（511.80s / 643.37s）与失败用例复跑结果见 §4。若 CI 在无并发负载的主机上
仍复现，应按 F05 沙箱缺陷另行处理，而不是隐藏在本票的通过结论里。

## 7. 下一票接口

- Go：`memory.NewRecallSource(db, *objectstore.Store)`、`memory.RecallSource`、
  `memory.RecallRowLimit=200`、`memory.RecallScanBatch=200`、
  `memory.RecallScanBatches=10`、`memory.RecallScanRowLimit=2000`（§9）；
  `presetexec.Deps.{Memory,RunIdentity}`、
  `presetexec.RunScope{OwnerID,ApplicationID}`；
  `config.SupervisorExecConfig.ObjectDir`（`ORCHARA_OBJECT_DIR`，可选）；
  `agentcontext.MemoryBudgetDivisor=4`、`MemoryTruncated`、
  `MemoryRead{Rows,Scan}`/`MemoryScan`、`IsProtectionSensitive`、
  `Memory.Revision/ConfirmedAt/CandidateID/SourceRunID`、
  `Trace.MemoryTokens/MemoryBudgetTokens/MemoryScan`、逐条 memory
  source/truncation 字段（含 `memory_scan_truncated` 的 scanned/scan_limit）；
- 表/迁移：无新表、无新迁移；读取 `platform.memories` 当前行（`memories_scope` 索引）；
- HTTP：无新端点；trace 经 `GET /api/v1/runs/{run_id}/node-results` + owner-only
  artifact content 读取；
- 失效 seam：`memories.revision` + `deleted_at` + `policy_epochs('memory:<owner>:<app>')`
  仍是 T067 缓存键/守卫的权威事实（本票每次直接读 PG，不缓存）。

## 8. ADR

`product/docs/adr/0009-f09-memory.md` §12（T063 增补：召回源=PG 当前行、run 身份 scope、
预算 1/4 与最低相关度截断、逐条来源 trace、否定/审批保护、唯一接点与
`ORCHARA_OBJECT_DIR`、无迁移、propose 身份核实结论、未做项）；§12.9（RV3 复审修复：
保护感知的有界越界扫描）。`tools/sync_spec.py --check` 保持 no drift。

## 9. RV3 复审修复：保护感知的越界扫描（2026-11-05）

复审确认的 P2 缺陷：§1.1 的召回只按新近度读 `RecallRowLimit=200` 行。第 201 行及更早的
用户否定（例如“禁止外发”）在上下文 builder 的保护检测之前就被排除，trace 没有记录，
模型可能把用户明确说过的约束当成不存在——违反 `ROUTING-MEMORY-KNOWLEDGE.md` §2
“截断记录来源和原因，不能丢掉审批参数或用户否定条件”。

修复（详情见 ADR 0009 §12.9）：

1. **共享词表**：`internal/context.IsProtectionSensitive` 导出 builder 原有的封闭标记表；
   `internal/context` 的 protected 判定与 `internal/memory` 的越界扫描都调用它。
2. **有界越界扫描**：新近 200 行不变；满页时按 `(confirmed_at, id)` keyset 以
   `RecallScanBatch=200` 行一批、最多 `RecallScanRowLimit=2000` 行（`RecallScanBatches=10`）
   扫描同 owner+application 的旧行，命中保护标记的行加入返回集；旧正文读不出仍整体
   fail closed。
3. **接口承载边界**：`agentcontext.MemorySource.ConfirmedMemories` 返回
   `MemoryRead{Rows []Memory, Scan MemoryScan}`；`MemoryScan{scanned, limit,
   protected_found, truncated}` 只含计数。`NoMemorySource` 返回零值 `MemoryRead{}`，
   builder 的 `not_run` 分支不调用它，不伪造行/扫描。
4. **trace 不沉默**：触顶时 `turns[].memory_scan` 记录计数，且
   `turns[].truncations[]` 追加
   `{"source":"memory_scan","reason":"memory_scan_truncated","tokens_removed":0,
   "scanned":N,"scan_limit":2000}`；预算截断仍是 `optional_memory`，语义不变。
   硬上界处若不越界就无法证明“scope 已穷尽”，因此 `truncated=true` 是保守报告：
   显式承认不完整好过假装完整。

验证（真实 PG 16.4 + 真实编译二进制 + 真实 sealed 字节 + 真实 claim/result/exit +
真实 WSL2/runsc；模型仍是既有 dev stub；无 mock、无跳过）：

```text
$ python -m pytest tests/f09/test_t063_recall.py -q -k "protection_scan_finds"
1 passed in 60.88s

$ python -m pytest tests/f09/test_t063_recall.py -q -k "hard_bound"
1 passed in 106.17s

# 红灯证据（扫描被临时短路时，同一用例失败）：
#   assert {} == {'scanned': 1, 'limit': 2000, 'protected_found': 1, 'truncated': False}
#   FAILED ...test_protection_scan_finds... (1 failed in 39.22s)

$ python -m pytest tests/f09 -q          # 本树第 1 轮（默认 pytest 临时目录）
69 passed in 656.20s

$ python -m pytest tests/f09 -q          # 第 2 轮（默认 pytest 临时目录；主机并发）
3 failed, 66 passed in 648.07s
  # 失败：两个新用例在 submit 的签名上传 PUT 收到 500（对象目录已被并发 pytest 会话
  # 清理），既有 missing-memory 用例的 setup 找不到 orchara-admin.exe，另有
  # sandbox_failed。pytest 默认只保留最近 3 个 pytest-<N> 临时目录，本批主机同时在跑
  # 多个 agent 的 pytest 会话；这是主机临时目录竞争 + WSL/runsc 抖动，不是断言失败

$ python -m pytest tests/f09 -q          # 第 3 轮（独立 TMP/TEMP 隔离并发会话清理）
69 passed in 513.24s

$ python -m pytest tests/f09 -q          # 第 4 轮（背靠背，独立 TMP/TEMP）
69 passed in 503.26s

$ python -m pytest tests/f06/test_t047_context.py -q
2 passed in 35.24s

$ python -m pytest tests/tooling -q
21 passed in 6.01s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

确定性（BRIEF §6.1）：新用例不依赖墙钟窗口（记忆序由 PG 存储的 `confirmed_at`/`id` 决定，
`newest_memory_ids` 用同一排序独立复核）；token 断言只要求 `0 < memory_tokens <= 512`，
不押边界；无 sleep。第 3/4 轮为了避开并发 pytest 会话清理默认临时目录（pytest 保留最近
3 个 `pytest-<N>`），每轮用独立 `TMP`/`TEMP` 运行同一命令；测试代码与命令本体未改。

新增用例（`tests/f09/test_t063_recall.py`）：

- `test_protection_scan_finds_a_negation_beyond_the_recency_cap_and_the_budget_keeps_it`：
  201 条真实记忆（最早一条为“禁止外发客户名单”）；PG 侧 `newest_memory_ids` 独立证明
  该行在 200 行新近页之外；真实 run 的 trace 为
  `memory_scan={"scanned":1,"limit":2000,"protected_found":1,"truncated":false}`，
  否定进入 `sources[]` 且 `protected=true`，512-token 子预算下被保留（更新行按
  `optional_memory` 截断），同用户另一应用与另一用户的同标记记忆不在 sources/模型请求。
- `test_protection_scan_stops_at_its_hard_bound_and_the_trace_says_so`：2201 条真实记忆
  （200 新近 + 2001 旧行，最旧一条为否定）；扫描读满 2000 行触顶，
  `memory_scan.truncated=true`，`truncations[]` 恰有一条 `memory_scan_truncated`
  （`scanned=scan_limit=2000`、`tokens_removed=0`）；越界否定不在 sources、不在模型
  请求；run 本身成功。

Go 侧（`internal/context/build_test.go`）：
`TestProtectionSensitivityIsOneSharedVocabulary` 与
`TestMemoryScanRecordsOlderProtectedRowsAndTheHardBound` 覆盖 builder 的 `memory_scan`
报告、`memory_scan_truncated` 条目（含 scanned/scan_limit 与 0 token）以及
“无扫描时不发明字段”；既有 T063 四用例与 F06/T047 用例保持通过。

边界（如实）：扫描硬上界 2000 行/10 批是刻意的产品权衡——遇到上界只会显式报告
`memory_scan_truncated`，不会为了完整性无限读取历史；越界扫描不做嵌入/评分
（属 T067），也没有缓存（每次直接读 PG 当前行）。`memory_scan` 的 `scanned` 只数新近页
之外的旧行，不改变既有 `memory_tokens`/`memory_budget_tokens` 语义。
