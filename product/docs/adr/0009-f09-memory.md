# ADR 0009：F09/T054 个人长期记忆的候选/确认边界

- 状态：已接受（T054 主体；T058 增补见 §11；T063 增补见 §12）
- 日期：2026-11-04（T058 增补：2026-11-04；T063 增补：2026-11-04）
- 相关：F09 / T054（GitHub #55，规划 ID F09.01）、F09 / T058（GitHub #59，
  修订/删除停止旧内容召回）与 F09 / T063（GitHub #64，运行时按上下文预算召回）；
  分支 `t/t054-memory-candidates` / `t/t058-memory-revision` / `t/t063-runtime-recall`；
  报告 `product/reports/f09/T054-memory-candidates.md`、
  `product/reports/f09/T058-memory-revision-and-delete.md`、
  `product/reports/f09/T063-runtime-recall.md`；后续：
  T067（#67，Mem0 对照与检索缓存）；
  F07（删除治理，物理副本清理）与 F10/T055（知识库，ADR 0010）另有自己的
  ADR/债务，本文件不覆盖。
- 契约：`contracts/openapi.json`（T054 五个 + T058 三个 operation）、
  `contracts/database.sql:297-316`（表）与 `:356-362`（deletion_jobs）；
  `docs/spec/**` 与 `contracts/**` 未修改，`tools/sync_spec.py --check` 保持 no drift。

## 背景

T054 的产品行为是：候选页显示来源与应用范围、可确认/拒绝/等待过期；只有确认后才保存
个人长期记忆，重复确认幂等。契约已固定两张表（`memory_candidates`、`memories`）和五个
operation（`createMemory`、`listMemories`、`proposeMemoryCandidate`、
`listMemoryCandidates`、`decideMemoryCandidate`），但产品此前只有 F01 的“诚实空列表”
（`internal/api/apikeys.go` 旧 `handleListMemories`），没有任何存储。

实施前必须把若干容易做错的边界固定下来，否则会与 F01 的服务账号边界、F06 的审批幂等/
过期语义或 T058 的 ETag 设计冲突。本 ADR 记录这些决定；数据库列级偏离写在迁移 0033 的
头部注释里（house 约定），不重复。

## 决策

### 1. 个人记忆端点对服务账号一律 403 `personal_scope_forbidden`

契约在这五个 operation 上声明了 `apiKey`，但 `IMPLEMENTATION-DETAILS.md` §1、
`DATA-POLICY.md` 与 F01/T020 的既有边界都要求“个人长期记忆只属于人类”。因此
`POST/GET /memories`、`GET /memory-candidates`、`POST .../decision`
（以及 T058 将加的 PATCH/DELETE）在**读取 body 之前、任何存储调用之前**拒绝服务凭据：
`caller.Kind == "service" || caller.Method == apiKey` → 403 `personal_scope_forbidden`。
API key 连 `memory:*`/`personal:*` scope 都无法被签发（`internal/identity/apikeys.go`），
所以不存在“带 scope 的服务账号”这一绕过路径。

结构性兜底：`memory_candidates.owner_id`/`decided_by`、`memories.owner_id`/`confirmed_by`
都引用 `platform.users(principal_id)`，服务主体没有 users 行，即使未来某个写入者忘了
API 层拒绝，数据库也无法把服务账号写成所有者或确认者。

这是对契约 `apiKey` 声明的**刻意偏离**，属 T054/F09 负责，记录在此与本票报告。

### 2. 正文保存为 sealed artifact（保留契约 `body_artifact_id NOT NULL`）

`ROUTING-MEMORY-KNOWLEDGE.md` §2 允许“正文 artifact / 受保护文本”。本票选择契约原样：
候选与记忆的正文都是 `platform.artifacts` 里一个 `ready` 的 sealed 对象，
`purpose` 分别为 `memory_candidate` / `memory_body`，`media_type=text/plain; charset=utf-8`，
`sha256` 为正文 UTF-8 字节的摘要；写入用 `objectstore.PutSealed` 预分配 key，
事务回滚时删除对象，因此不存在“行在、字节不在”的已提交状态。

- 没有采用 `body_text` 列：那会偏离契约 `body_artifact_id NOT NULL`，并让“正文”出现
  两个权威（列 + 对象）；现有服务端封存路径（`internal/approvals`、`internal/modelregistry`）
  已经是真实可用的 seam，不需要新建 artifact 服务。
- **确认复用候选的 artifact**：`memories.body_artifact_id = memory_candidates.body_artifact_id`。
  同一份个人正文只有一个内容权威、只有一份字节；重复确认因此既没有第二行也没有第二个对象。
  `Upsert` 语义由 `UNIQUE (candidate_id)` 保证。确认前会重新读取并校验候选正文
  （摘要不符/对象缺失 → 503 `object_store_unavailable`，候选保持 pending），
  所以已提交的记忆不会指向读不出的字节。
- `createMemory`（用户明确“记住”）没有候选，自己封存一个 `memory_body` 对象。

### 3. `proposeMemoryCandidate` 只接受 worker 内部角色

契约 `x-internal-role: "worker"`。实现沿用既有 worker-only 内部端点（capability、run
artifact）的写法：`requireInternalRole(InternalRoleWorker)`，supervisor/maintenance
证书 403 `forbidden`；attempt token 必须等于该 run 的 live lease
（`run_attempts` + `jobs.current_attempt_token`），否则 403 `stale_attempt`。

服务端只从 run 推导事实，**不接受 body 里的 application_id（契约也没有这个字段）**：
`runs.execution_snapshot_id → execution_snapshots.application_id` 是应用范围，
`runs.owner_id` 是所有者。run 必须 `running`（409 `run_not_running`）、未取消
（409 `run_cancelled`）、owner 是未禁用人类（403 `personal_scope_forbidden` /
`permission_revoked`）。

### 4. 候选有效期：可配置 7 天 + 服务端惰性过期

`ORCHARA_MEMORY_CANDIDATE_TTL`（`internal/config`，默认 7 天，允许 1 分钟–365 天；
非法值拒绝启动）只决定**写入时**的 `expires_at`。过期判定永远由服务端按存储的
`expires_at` 做，和 TTL 配置无关：

- 列表读：先 `UPDATE ... SET state='expired' WHERE state='pending' AND expires_at<=now()`，
  投影再兜一次 `CASE WHEN pending AND expires_at<=now() THEN 'expired'`；
- 决定：行锁（`FOR UPDATE`）后重新判过期，过期则持久化 expired 并返回 409
  `memory_candidate_expired`；确认/拒绝对 expired 都不生效。

没有后台过期清扫进程（未运行，属后续运维票）；惰性迁移 + 决定时复检已使边界真实，
测试用“只差 `expires_at` 的两个候选”证明服务端确实读这个字段，而不是只看 state。

### 5. 决定幂等：状态机幂等 + `platform.idempotency_keys` 收据

契约要求 `Idempotency-Key` 必填（`minLength 1`）。实现分两层：

1. **状态机幂等**（AC1 的来源）：confirm 在已 confirmed 的候选上返回**同一条记忆**
   （`memories.candidate_id` UNIQUE + 查询已存在行），不写第二行、不建第二个对象；
   重复 reject 返回同样的 `{rejected, null}`。
2. **收据**：key 作用域 = `(principal_id, "POST", "/api/v1/memory-candidates/<id>/decision", key)`，
   `request_sha256` = JCS({"candidate_id","decision"}) 的 SHA-256，响应体存入
   `response_json`（与 `debugDraft`/`model_validate` 同一张表、同一套“先 claim 后回放”时序）。
   同 key 同决定 → 回放已存 body；同 key 相反决定 → 409 `idempotency_conflict`；
   决定被拒（过期/状态冲突/503）→ 收据随事务回滚，失败的决定不会抑制正当重试。

错误码：沿用既有词表（`resource_not_found`、`validation_failed`、`forbidden`、
`stale_attempt`、`run_not_running`、`run_cancelled`、`personal_scope_forbidden`、
`idempotency_conflict`、`object_store_unavailable`、`invalid_cursor`），只为“决定与当前
状态冲突”新增三个聚合码 `memory_candidate_expired` / `memory_candidate_rejected` /
`memory_candidate_confirmed`，与审批聚合的 `approval_expired`/`approval_not_pending`
先例一致。缺失 `Idempotency-Key` → 422 `validation_failed` + `details.field`
（与 T050 决定一致），空 `limit` → 422（沿用 F01 的“存在但为空即非法”规则），
坏 cursor → 400 `invalid_cursor`。

`createMemory` 没有 `Idempotency-Key`（契约无此参数），因此重复 POST 是两次“记住”意图，
会创建两条不同记忆；候选确认路径才是本票要求的幂等入口。

### 6. 记忆缓存 epoch：复用 `platform.policy_epochs`，不新建表

`policy_epochs(scope text PK, revision)` 是通用表。确认与直接写入在同一事务内执行
`INSERT ... ON CONFLICT DO UPDATE revision = revision + 1`，scope 为
`memory:<owner_id>:<application_id>`（小写）。授权器只读它自己的 `acl` scope，
所以该行对授权是惰性的，只作为**失效 seam**：T058 的更新/删除、T063 的召回缓存、
T067 的 Mem0 映射都以它为版本。没有任何读路径把它当作权限证据。

### 7. 来源：单一链 `memories.candidate_id → memory_candidates.source_run_id`

不在 `memories` 上加“来源 run”列：那会是第二个可分歧的来源权威（0026 记录过同一条
理由）。直接 `createMemory` 没有 run 来源（`candidate_id IS NULL`），来源就是“用户明确
记住”。候选的 `source_run_id` 由服务端从路径 run 写入，worker 不能伪造。

### 8. 记忆表结构为后续票预留的 seam

- `revision bigint NOT NULL DEFAULT 1`：T058 的强 ETag/CAS 从 1 开始，无需回填；
- `deleted_at`（契约列，本票不用）：T058 删除 + T067 tombstone 的落点；
- `memories_owner_recent` / `memories_scope` 索引：T058/T063 的 owner 读与 active 集读；
- `memories.candidate_id` 唯一：确认恰好一条记忆的结构保证；
- `internal/context.MemorySource` 仍是 `NoMemorySource`：**候选永不可召回**，
  确认后的召回属 T063；本票没有把任何候选正文接入上下文；
- `platform.memory_embeddings` **不在本票创建**：运行时召回与索引属 T063（#64），
  且本集群 PostgreSQL 16.4 无 pgvector（0009 已记录同一环境边界），
  建一张平台写不了的向量表只会变成死 schema。

### 9. 共享与管理员可见性：本票不扩展

个人记忆按 `owner_id + application_id` 隔离；列表在 SQL 的同一 WHERE 里过滤 owner，
管理员没有豁免（别人的记忆/候选对他表现为空列表或 404，不泄露存在性）。
`internal/policy.ResourceTypes` 不新增 memory 共享类型；共享/授权延期到后续票，
作为已知债务记录在报告里。

### 10. 页面显示“来源”的边界

`/memories` 页面显示候选来源、应用范围、过期时间、状态与确认/拒绝动作，以及当前记忆列表
（含 revision）。契约 `MemoryCandidate` 没有 `run_id` 字段（`additionalProperties:false`），
所以页面不伪造来源 run id，而是显示“来源：Run 内提出的记忆候选（服务端封存；确认前
不可召回）”这一来源类别 + 应用范围；真实 `source_run_id` 是存储事实，供 T058/T063 使用。
没有为此新增非契约端点。页面全部 `textContent`，不保留凭据或正文。

### 11. T058 增补：强 ETag 修订、删除墓碑与失效时序

T058（#59）在 0033 的 `revision`/`deleted_at`/`policy_epochs` seam 上补齐
`getMemory`/`updateMemory`/`deleteMemory`，不引入第二个记忆存储，也不改契约
（`deletion_jobs` 是契约表，见 §11.6）。

1. **强 ETag = revision。** 复用 `catalog.FormatETag/ParseETag`（草稿 PUT 的同一套强校验器）
   与 `precondition_required`/`precondition_failed` 词表，ETag 形如 `"2"`。契约只要求
   “detail 读取带 ETag、PATCH 必须带 If-Match”；弱校验 `W/"1"`、列表、`*`、未加引号数字
   与多值头一律按“陈旧/乱码”处理（412），不做宽松解析，因为宽松解析会让“乱码”与“陈旧”
   无法区分。
2. **写序固定：** 404（owner-only，含已删除/他人/管理员/未知/畸形 id）→ 428（缺失
   If-Match）→ 412（陈旧/乱码，`details.current_etag` + `current_revision`）→ 422
   （body `{text, confirmed:true}`，`additionalProperties:false` 由共享 decoder 拒绝）→
   行锁 + CAS 写入。越权者学不到存在性，陈旧写者拿到可恢复的事实，body 校验不会掩盖
   前置条件。
3. **更新 = 新正文 artifact + revision+1 + 同事务 epoch。** 旧 artifact 不物理删除（F07），
   `memories.body_artifact_id` 只指当前修订；因此旧修订的正文/embedding 都不可能被读成
   “当前”。并发同 ETag 恰一胜者（`FOR UPDATE` + `WHERE revision = $claimed`），
   失败者得到带新 ETag 的 412，绝不 last-writer-wins。
4. **删除 = 同步失效 + 异步清理。** 在回答 202 的同一事务里：`deleted_at=now()`、
   `revision+1`、写 `deletion_jobs`（`pending` + `remaining_scopes`）、bump
   `memory:<owner>:<application>` epoch，然后才提交并答 202。此后 GET/PATCH 为 404、
   列表不再包含；T063 将来按 `(memory_id, memory_revision)` 建的 embedding 条目因
   revision 前进而永不“当前”。**tombstone 是幂等键**：重复/并发 DELETE 返回同一 job，
   不再 bump revision/epoch；`UNIQUE(resource_type, resource_id, deletion_revision)`
   是结构兜底。
5. **`deletion_jobs` 是最小切片，债务明确：** T058 只写 `pending`，**不注册**
   `delete_content` handler，没有 lease/retry/completion，也不物理删除对象/artifact 行；
   完整删除治理属 F07。`remaining_scopes` 形状（`object` / `artifact` / `embedding` /
   `memory_row` 的 JSON 对象数组）写在 0036 迁移头与这里。更新残留的旧 `memory_body`
   artifact 没有 job：F07/T063 的清扫判据是“`purpose='memory_body'` 且不被任何
   `memories.body_artifact_id` 引用”，不需要新列。
6. **数据模型：** 迁移 `0036_memory_revision_and_deletion.sql`（最终编号；基线分支上曾为
   0034，合并时 T055 先占 0034、T052 再占 0035，故重编号为 0036）只新增契约
   `deletion_jobs`（`owner_id → principals`、四态 CHECK、`remaining_scopes` 为 array、
   `UNIQUE(resource_type, resource_id, deletion_revision)`）；`platform.memories` 一个列
   也不加，重复 migrate no-op，worker/PUBLIC 零 `platform` 权限。
7. **召回/检索缓存 seam：** 权威失效事实是 `memories.revision` + `deleted_at` +
   `policy_epochs(memory:owner:app)`。T063/T067 的 embedding/缓存条目必须把
   `memory_revision` 放进键并同时校验 `deleted_at IS NULL` 且
   `memory_revision = memories.revision`（或把 epoch 放进缓存键），否则不得服务；
   本票不创建 `memory_embeddings`（本集群无 pgvector，T063 拥有）。
8. **页面：** 编辑先 `GET /memories/{id}` 取 ETag，保存带 `If-Match` 与精确 body；412
   显示“版本冲突”并采用 `details.current_etag`，只有用户显式点“用最新版本重试”才再次写入
   （不再静默覆盖，也不丢用户输入）；删除显示 deletion job 的真实状态，明确“旧内容已停止
   召回”和“物理副本清理由删除治理异步执行”，不声称副本已删除。全部 `textContent`，
   纯函数由 Node 执行。
9. **服务账号边界不变：** PATCH/DELETE 与 T054 的五个端点一样，在任何存储访问前
   403 `personal_scope_forbidden`；`owner_id` 仍引用 `platform.users`。
10. **共享与管理员可见性不变：** 仍是 owner-only，管理员对他人记忆是 404（§9 的债务
    继续有效，T058 不扩展 `policy.ResourceTypes`）。

### 12. T063 增补：运行时召回、记忆预算与来源跟踪

T063（#64）在 T054/T058 的存储与失效 seam 上接上运行时召回，仍然没有第二个记忆存储、
没有新迁移、没有新契约端点。

1. **召回源 = PostgreSQL 当前行，不是缓存。** `internal/memory.RecallSource` 每次调用
   直接查 `platform.memories`：`owner_id`、`application_id` 与 `LIMIT` 在同一条 WHERE；
   `deleted_at IS NULL`（T058 墓碑）；`JOIN platform.artifacts` 只接受 `state='ready'`
   且未删除的当前 `body_artifact_id`；`LEFT JOIN memory_candidates` 只为来源链
   `candidate_id -> source_run_id`。候选（pending/expired/confirmed）永远不是召回源；
   记忆本身没有过期列，过期是候选生命周期事实。正文逐条从 sealed 对象读取并校验
   sha256；**一条读不出即整体失败关闭**（trace `memory_state=unavailable`、不注入任何
   记忆），绝不返回“部分记忆”——部分视图与“用户从未说过”不可区分。
   `RecallRowLimit=200` 只防止病态账户让单轮读取无界；真正的取舍在上下文预算。
2. **范围来自 run 身份，不来自 manifest/body。** 监督进程用可信只读数据库身份读
   `runs.owner_id` + `execution_snapshots.application_id`（`presetexec.RunIdentity`），
   传给上下文 builder 的 `Scope`；缺失或读取失败时召回源拒绝空 scope（fail closed），
   trace 报 unavailable，运行本身继续（个人记忆是可选上下文，不因它失败整个 run）。
3. **记忆预算 = 上下文预算的四分之一。** `budget = context_window - max_output_tokens`，
   `memory_budget = budget / 4`（`agentcontext.MemoryBudgetDivisor`）；其余预算留给本次
   run 的授权输入、历史与工具结果，记忆不能先挤掉它们。选择规则是可解释的确定性规则：
   `confirmed_at DESC, id DESC` 的“新近度即相关度”（本构建没有 pgvector 嵌入打分；
   检索打分属 T067），按新近度依次加入直到编码后的消息超过子预算；被放弃的行按
   **最低相关度优先**写入 `truncations[]`，`reason="optional_memory"`，并带
   `memory_id`/`memory_revision`/`tokens_removed`。`memory_state` 取值
   `not_run|empty|included|truncated|unavailable`；`memory_tokens` 是实际花费，
   `memory_budget_tokens` 是使用的子预算。过预算因此是可观察的，不是静默丢弃。
4. **来源逐条可解释。** 每条被选中的记忆是一个 `sources[]` 条目（`kind="memory"`，
   `label="memory:<id>"`），带 `memory_id`、`memory_revision`、`memory_confirmed_at`
   （RFC3339 UTC）、`memory_candidate_id`、`memory_source_run_id` 与边际 token 数；
   trace 只有标识/原因/计数，**没有记忆正文**。读取面沿用 T047 的
   `GET /api/v1/runs/{run_id}/node-results` 定位 `context-trace` + 普通 owner-only
   artifact content 路由，不新增端点、不改 `/runs` 页面。
5. **用户否定/审批参数永不截断。** 记忆正文按固定词表（`memoryProtectedMarkers`：
   `must not`/`do not`/`never`/`exclude`/`approval`/`不要`/`不得`/`禁止`/`审批` 等，
   ASCII 按词边界匹配，避免 `whenever` 命中 `never`）标记为受保护：选择时优先纳入，
   预算截断永远不丢；整个上下文超预算时按条丢“非受保护”记忆（最低相关度优先），
   只剩受保护记忆仍放不下则走既有 `context_budget_exhausted`（与受保护最小输入同一
   规则，绝不静默裁剪）。这是与 §2 的 protected-key 规则对齐的封闭词表，新增标记必须
   同时加测试；不从模型摘要推断。
6. **生产接点只有一个。** `internal/presetexec/agent.go` 的 `agentcontext.Build` 调用是
   唯一注入点；`presetexec.Deps.Memory` + `Deps.RunIdentity` 由
   `cmd/supervisor exec` 组合根注入（`memory.NewRecallSource(db, objectstore.Open(
   ORCHARA_OBJECT_DIR))`）。`ORCHARA_OBJECT_DIR` 在监督进程是可选启动项：未配置时
   召回读得到行但读不到字节，trace 报 unavailable，绝不伪造文本；配置后与 API 共用
   同一个 sealed 根。没有缓存，因此 T058 的
   `policy_epochs(memory:<owner>:<app>)` 失效 seam 不会被跳过，只会供 T067 的检索缓存
   使用。
7. **没有新迁移。** 0033 的 `memories_scope(owner_id, application_id) WHERE deleted_at
   IS NULL` 已覆盖召回查询；`platform.memory_embeddings` 仍**不创建**（PG 16 无
   pgvector；本票不写嵌入，检索打分/缓存属 T067）。迁移号不被本票占用。
8. **propose 身份问题（T054 记录）核实结论：** F04A 执行器并非“只持 supervisor 证书”
   ——`cmd/supervisor exec` 同时持有 supervisor 与 worker 两套 mTLS 身份，能力调用
   （`InvokeCapability`）与 `RecordNodeResults` 都用 `worker` 容量 + `x-internal-role:
   worker` 发起；T063 的真实 run 正是通过该 worker-only 路由记录 trace 并成功的。因此
   worker-only 的 `proposeMemoryCandidate` 对一个接入 agent loop 的生产者**有可用传输
   身份**，不需要拆分 supervisor/worker 进程。本票未实现“自动抽取并 propose”的产品
   行为（属 F04A 抽取策略；票面明确不默认从完整聊天抽取永久事实），故 producer 本身
   是 `not_run`，但身份前提已验证而非假设。

## 影响

- `contracts/**`、`docs/spec/**`、`tools/sync_spec.py --check` 不受影响；
- 迁移 0033（T054）新增两张契约表 + 列级偏离（头部注释逐条列出）；迁移 0036（T058）新增
  契约 `deletion_jobs` 最小切片；重复 migrate no-op，`orchara_worker`/PUBLIC 零
  `platform` 权限；
- 新包 `internal/memory`（候选/记忆存储与服务）、`internal/api/memory.go`（T054 五个 +
  T058 三个 handler）、页面 `apps/web/src/memories.{html,js}`；
- F01 的“诚实空列表”被真实读模型替换：`GET /api/v1/memories` 现在校验
  `cursor`/`application_id`，`tests/f01/test_t020_personal_boundary.py` 同步更新为真实边界；
- T058 后 `GET /memories/{id}`、`PATCH`、`DELETE` 是正式入口；`/memories` 页面可展示
  ETag 冲突与删除状态；
- T063 后 agent 节点的上下文会按预算召回当前用户+应用的已确认记忆：新增
  `internal/memory/recall.go`（`RecallSource`）、`presetexec.Deps.{Memory,RunIdentity}`、
  `internal/config.SupervisorExecConfig.ObjectDir`，trace 增加
  `memory_tokens`/`memory_budget_tokens` 与逐条 memory source/truncation 字段；
  `tests/f06/test_t047_context.py` 对无记忆 owner 的期望由 `not_run` 更新为 `empty`。

## 未做（如实）

- 浏览器 DOM 驱动：`not_run`（页面纯函数由 Node 执行）；
- 后台过期清扫进程：`not_run`（惰性迁移 + 决定复检已真实生效）；
- Mem0 对照与检索缓存（T067）：T063 不做嵌入检索，只留
  `(memory_id, memory_revision)` + `policy_epochs` 失效 seam；
- 自动记忆抽取与对 agent loop 的 propose 接入：`not_run`（F04A 抽取策略；T063 只证明
  worker-only propose 的传输身份可用，票面明确不默认从完整聊天抽取永久事实）；
- 物理副本清理与删除作业的 claim/retry/completion（F07）：T058 只写 `pending` job，
  不注册 `delete_content` handler，不删除对象/artifact 行；债务记录在迁移 0036 头、
  §11.5 与报告 §6；
- `memory_embeddings`（无 pgvector）：T063 不创建，检索打分/缓存属 T067；
- 记忆共享/授权：不实现，债务记录在报告 §6。
