# F10 T061 验收：更新资料或 embedding 后不激活陈旧索引

- 任务：F10 / GitHub Issue #62（票 `T061`，规划 ID F10.03），分支
  `t/t061-index-revisions`（worktree `E:\orchara-worktrees\t061`）；开工时自
  integration tip `9841f19` 切出并立即合入集成最新 tip（含 T060 的迁移/ADR 重编号），
  merge commit **`275776c`**；收尾再合入 `integration/f13a-f15a-f09-f10`（见 §3）。
- 状态：**`passed`**（本票范围 = 独立 document/index/chunk revision 的存储层、
  **非契约重建入口** `POST /api/v1/knowledge-documents/{id}/reindex`、带真实进度的
  诊断读、**唯一激活事务**（`FOR UPDATE` 重读源行/索引行，要求源 revision 未变、
  仍 `indexing`、无 tombstone、索引版本仍 `building`）、迟到任务拒绝
  （`stale_revision`/`source_deleted`）、失败保留旧活动索引且可重试、换 embedding
  模型不混向量、`/knowledge` 页面显示索引版本/模型/进度/失败/活动切换）。
  真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP/mTLS + 真实封存对象字节 +
  真实 maintenance 进程（含 work-only/finalize-only 双进程挂起旧 handler 的真实竞争）
  + Node 执行页面逻辑；无 mock、无跳过。
- 边界（如实）：真实 embedding provider **blocked**（继承 T055/T067：无凭据、构建无
  adapter，fail closed；换模型用两个已编译的确定性 dev-stub revision
  `local-hash-v1`/`local-hash-v2` 做真实配置切换）；浏览器 DOM 驱动页面 **not_run**
  （与既有页面票同一边界）；历史索引/失败暂存 chunk 的物理清理 **not_run**（F07/后续
  运维）；ANN/pgvector/规模基准继承 T064/T067 **not_run**；全量 `pytest tests`
  （≈67 分钟）按 BRIEF §3 不在本票运行，由 orchestrator 合并后执行。
- 契约偏差：契约没有重建/索引版本操作，新增非契约 `reindex` 入口、诊断 addditive
  `active_index/active_embedding_model_revision/indexes[]`、检索/回答 addditive
  `retrieval.index`；迁移 `0039_index_revisions.sql` 也是非契约 additive 表/列（唯一破坏性
  语句是旧 chunk 唯一约束被新的 `(index_version_id, chunk_no)` 身份取代）。
  全部记录在 **ADR 0017**；`contracts/**`/`docs/spec/**` 未改，`sync_spec --check`
  保持 no drift。

## 0. 工作记录（简短）

1. 读 `BRIEF.md`、`briefs/T061.md`、票面 #62、T055/T059/T060/T067 报告、
   ADR 0010/0013/0015/0016、`internal/knowledge`（service/parse/chunk/cache）、
   `internal/background`（job/enqueue/claim/complete/document_ingest 守门）、
   `cmd/maintenance`（pool 划分、binding payload）、`internal/api/knowledge.go`、
   `0034_knowledge.sql`/`0037`/`0038` 迁移、`tests/f10` + `tests/support/{t055,t067}.py`
   与页面 Node 执行方式；确认 integration 已把 T060 的迁移/ADR 重编号到 `0038`/`0016`，
   故本票使用迁移 `0039`、ADR `0017`。
2. TDD 垂直切片（每片先红后绿）：
   - 迁移 0039 + schema 约束（链连续、单 building/源 revision、chunk 新身份、FK；
     另加真实 upgrade backfill 用例：先迁到 0038，插入旧 ready 文档/块，再迁 0039）；
   - `internal/embedding` 的第二确定性 stub revision `local-hash-v2`（不同向量空间、
     同 384 维；未知 stub revision fail closed）→ Go 单测；
   - 重建入口 + 索引版本创建 + job 绑定 + handler 按索引版本暂存 + 激活守卫
     → 真实 HTTP/maintenance：新源 revision 激活新索引、旧索引 superseded；
   - 换模型（第二个真实 API/maintenance 进程 `local-hash-v2`）→ 两个索引版本/两套
     chunk 共存、检索只读活动索引、旧部署返回空并报告 excluded；
   - 真实 provider 失败重建 → 旧活动索引仍可读、失败码可诊断、重试成功；
   - 真实延迟竞争：work-only maintenance 挂起旧 handler（等待被扣住的 finalize 池）→
     真实 reindex 推进源 revision / 写 F07 形状 tombstone → 放开 finalize 池让旧 handler
     真正完成 → 守卫拒绝 `stale_revision`/`source_deleted`，`activated_at` 保持 NULL；
   - 页面结构 + Node 执行 `describeIndexes`/`describeRetrieval`/`reindexDocumentPath`；
   - 既有测试预期的必要更新（见 §7）并两次连跑 `tests/f10`。
3. 跑门禁（§3），写本报告/`PROGRESS.md`/ADR 0017；按 feat/test/docs 提交；合并
   integration 后复跑；未 push、未关 issue、未改 `docs/spec/**`/`contracts/**`。

## 1. 交付内容

### 1.1 迁移 `0039_index_revisions.sql`

- `platform.knowledge_index_versions`：`document_id→knowledge_documents`、
  `version`（每文档单调，`UNIQUE(document_id,version)`）、`document_revision`、
  `embedding_model_revision`、`embedding_dimension`、`state`
  （`building/ready/failed/superseded`）、`chunk_count`、`parser/chunker_version`、
  `error_code`、`created/updated/activated/superseded_at`；`failed⇒error_code`、
  `ready⇒activated_at` 两个 CHECK；`(document_id,document_revision)` 的 partial unique
  `knowledge_index_versions_one_building` 只允许每个源 revision 一个进行中构建
  （新源 revision 可以与旧 job 并行构建，由激活守卫裁决）。
- `knowledge_documents.active_index_id`（唯一激活指针，FK 到索引版本）与
  `active_embedding_model_revision`；`state='ready'⇒active_index_id IS NOT NULL` 的约束
  在 backfill **之后**添加（否则升级库已有 ready 行会违反）。`active_revision` 保留为
  活动索引的源 revision（T055 语义不变）。
- `knowledge_chunks.index_version_id`（additive，FK）；**chunk 身份**由
  `UNIQUE(document_id,document_revision,chunk_no)` 改为
  `UNIQUE(index_version_id,chunk_no)`（partial），因此同一源 revision 的两套模型向量
  可以共存且永不互相覆盖。旧约束是本迁移唯一删除项，理由写在头注释。
- `background_jobs.expected_index_version_id`：一个 `document_ingest` job 绑定恰好一个
  索引版本；守卫据此核对暂存集合与激活决定。
- **upgrade backfill**：迁移前已 `ready` 的文档得到一个 `version=1` 的活动索引行
  （模型优先取文档列，其次取 chunk 的最小模型 revision，最后回退到当时唯一可用的
  `local-hash-v1`），现有 chunk 挂到该索引，文档指针指向它；重复 `admin migrate`
  no-op；`orchara_worker`/PUBLIC 零授权。
- 链连续：`0001..0039`（集成 tip 的 0037 缓存 + 0038 PDF admission + 本票 0039）。

### 1.2 重建入口与索引版本创建（`internal/knowledge/index_versions.go`）

- `ReindexDocument`（owner-only）：锁文档行 → 无源变更时拒绝同一源的重复构建
  （409 `index_build_in_progress`），有**新的源 artifact** 时将其作为新源 revision
  （revision+1、更新 source/parser）并允许新索引版本与旧的进行中 job 并存 →
  以当前部署的 embedding model revision + 固定维度创建 `building` 索引版本 →
  同事务入队一个 `document_ingest` job（dedup key `knowledge-index:<index_id>`，
  `expected_revision`、`expected_index_version_id` 都写库）→ 返回 202
  `{document_id,state,revision,index_version_id,index_version,embedding_model_revision,job_id}`。
- `ingestDocument` 复用同一 seam：ready 文档在活动索引模型 != 当前部署模型时创建新索引
  版本（同源 revision）；失败文档重试创建新索引版本；从未激活过的失败文档仍按 T055
  语义 bump 源 revision。ready/indexing 的重复提交保持幂等。
- 进度读：`GET /knowledge-documents/{id}/diagnostics` additive
  `active_index_id/active_index/active_embedding_model_revision/indexes[]`；每个索引版本
  返回 version、document_revision、embedding model revision/dimension、state、由
  state+chunk_count+job 状态派生的 `stage`（queued/building/staging/complete/failed/
  superseded）、真实 `chunk_count`、失败码、`active`、激活/取代时间、最近 job 的
  state/error_code/attempt_count。**没有伪造百分比**。

### 1.3 激活事务（`internal/background/document_ingest.go`，唯一写指针者）

同一事务内：
1. `SELECT state, revision, deleted_at, active_index_id FROM knowledge_documents
   WHERE id=$1 FOR UPDATE`：行不存在/已 tombstone/`deleting|deleted` →
   索引版本 failed `source_deleted`、job failed，**文档与指针不动**；
2. `SELECT ... FROM knowledge_index_versions WHERE id=$1 FOR UPDATE`：必须属于该文档、
   仍 `building`；`job.expected_revision` 与文档/索引行的 `document_revision` 必须一致，
   否则索引版本 failed `stale_revision`、job failed、文档不动；
3. 成功还要求文档仍 `indexing`；重读该索引版本的 chunk 集合（非空、`chunk_no 0..n-1`
   连续、citation 的 document/revision/source 一致、embedding 非空且维度=索引固定维度、
   模型 revision 全部=索引模型、body artifact 全部 ready 且属 job owner）；任一不满足 →
   具体失败码；
4. 全部通过：索引版本 `ready`（真实 chunk_count/chunker_version/activated_at），文档
   `state=ready`、`active_index_id` 指向它、`active_revision`=该索引源 revision、
   `active_embedding_model_revision`=该索引模型，旧活动索引置 `superseded`；同事务，
   无部分激活。
- 失败：非可重试码把索引版本置 failed、文档置 failed，但**保留**活动指针/active
  revision/模型（旧活动索引继续可读）；可重试基础设施码两者都不动，job 回 ready；
  取消=`ingest_cancelled` 终态。
- handler 端：绑定返回 index_version_id/model revision/dimension；若部署模型与 job 创建时
  不一致 → `embedding_model_changed`（business，绝不把新模型向量写进旧索引）；
  `StageChunks` 以 index version 为替换/唯一身份，永不跨索引覆盖。

### 1.4 换模型不混向量（`internal/embedding` + 检索）

- `local-hash-v1`（默认）与 `local-hash-v2` 是两个**已编译的确定性 dev-stub revision**
  （不同 SHA-256 字节取桶/符号，同 384 维、跨进程稳定）；`ORCHARA_EMBEDDING_PROVIDER=stub`
  + `ORCHARA_EMBEDDING_MODEL=local-hash-v2` 是真实部署配置切换。未知 stub revision
  fail closed，不静默用 v1；真实 provider 仍 `ErrNotConfigured`，绝不回退 stub。
- 检索 SQL 只读文档的 `active_index_id`：`iv.state='ready'`、
  `kc.index_version_id=iv.id`、`kc.document_revision=iv.document_revision`、
  `iv.embedding_model_revision=kc.embedding_model_revision=当前查询模型`；旧索引版本的
  chunk 即使 body 仍 ready 也不参与候选。
- 非契约检索/回答元数据新增 additive
  `retrieval.index={embedding_model_revision,active_indexes,excluded_indexes}`：写明本次
  实际使用的活动索引模型 revision，以及因模型不同被排除的活动索引数。旧部署进程用旧
  模型查询时得到**空结果**且 `excluded_indexes=1`，不会混排。
- T067 缓存键的 source digest 加入 `active_index_id/active_embedding_model_revision`；
  命中复检 SQL 与检索/引用使用同一活动索引谓词，激活切换后旧键不可达。

### 1.5 失败可重试与旧索引保留

- 真实 provider 不可用导致的重建失败：索引版本 failed
  `embedding_provider_not_configured`，文档 failed，但活动指针仍是旧索引；旧索引在
  `local-hash-v1` 部署下继续可检索；再次 `reindex` 创建新版本并成功激活，旧失败作为
  历史保留（`failed` 不消失）。有活动索引的失败重试不 bump 源 revision。
- 页面/诊断对失败版本显示 stage=failed、error_code、job state/attempt。

### 1.6 `/knowledge` 页面

`apps/web/src/knowledge.html` + `assets/knowledge.js`：文档面板下新增"索引版本、进度与
激活切换"区（当前激活索引、每版本 version/model/state/stage/chunk_count/任务尝试/失败码、
active 标记、重建按钮）；`reindexDocumentPath`/`buildReindexBody`；检索元数据显示实际
活动索引模型与 excluded 数；全部 `textContent`，无浏览器存储/object key。

## 2. AC 逐条

| # | 验收标准 | 结果 | 证据 |
|---|---|---|---|
| 1 | 换模型不混向量 | **proven** | `tests/f10/test_t061_index_versions.py::test_switching_the_embedding_model_keeps_two_vector_spaces_apart`：第二个真实 API/maintenance 进程以 `local-hash-v2` 重提交同一源文档；DB 中同一 `document_revision=1` 有两个索引版本（v1 superseded / v2 ready），两套 chunk 的 `index_version_id` 不同、模型 revision 分别为 v1/v2、均 384 维；v2 部署检索只返回 v2 chunk 并报告 `retrieval.index.embedding_model_revision=local-hash-v2`；旧 v1 部署返回空 items、`active_indexes=0/excluded_indexes=1`；旧 chunk citation 404，新 citation 200 且 citation 模型为 v2。Go 单测 `TestLocalHashV2IsADistinctDeterministicStubRevision`/`TestUnknownStubModelRevisionFailsClosed` |
| 2 | 旧摄取结果不能覆盖新版本或复活删除 | **proven（真实竞争）** | `test_t061_races.py::test_a_parked_old_job_cannot_activate_after_the_source_revision_moved`：work-only maintenance 已上传旧版本 chunk（真实 parked）、等待被扣住的 finalize 池；期间通过真实 reindex 把源推进到 revision 3；放开 finalize 后旧 handler 真正 stage 完并提交成功，但守卫拒绝：索引 v2 `failed/stale_revision`、`activated_at IS NULL`、job `failed/business`，旧 job 产生的 chunk 行真实存在；最终只有 v3 激活，检索只返回 v3 正文（无 BETA 标记）。`::test_a_deleted_document_is_never_resurrected_by_a_parked_old_job`：同样挂起后写 F07 形状 tombstone，旧 handler 完成后索引 v2 `failed/source_deleted`、`activated_at NULL`，文档保持 `deleted`+`deleted_at`，GET/检索/全部 citation 404，deleted 文档 reindex 404，未复活 |
| 3 | 失败可重新摄取且重试成功 | **proven** | `test_t061_index_versions.py::test_a_failed_rebuild_keeps_the_previous_index_readable_and_can_be_retried`：真实 provider 部署（无 adapter）的重建以 `embedding_provider_not_configured` 失败，旧活动索引仍 `ready` 且检索仍返回旧 chunk；同一文档再次 reindex（默认 v1 部署）成功激活 v3，v2 失败历史保留。T055 既有失败重试/空解析重试用例仍绿 |
| 4 | 真实端到端入口；失败、当前授权与重试/恢复边界有断言，不以单层 mock 成功代替 | **proven** | 所有新用例走真实 `cmd/api` + 真实 `cmd/maintenance`（含 work/finalize 分离进程）+ 真实封存字节/真实对象文件；`test_reindex_is_owner_only_csrf_protected_and_refuses_a_duplicate_build`：他人/管理员 404、缺 CSRF 403、同源重复构建 409、请求本身不激活指针；失败重建与迟到竞争见上；页面结构 + Node 执行 helper |
| 5 | 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件 blocked，未运行 not_run | **proven（含显式 blocked/not_run）** | 本报告 §3/§4/§5：Go/Python/Node/PG 版本、迁移名、命令与原始输出、真实进程日志路径；`local-hash-v2` 是 dev-stub（不是真实模型），真实 provider blocked；浏览器 DOM、历史索引 GC、全量 pytest 均显式 not_run |

## 3. 门禁命令与原始输出

环境：Windows PowerShell；Go（`E:\orchara-toolchains\go\bin`）、Python 3.12.2、
Node v24.13.0、PostgreSQL 16.4（`127.0.0.1:55432`，`python tools/pg_dev.py status`）；
`product/.env.local` 提供本地口令，报告不含任何凭据。

<!-- GATES -->

## 4. 真实栈、输入与版本

- 真实 PostgreSQL 16.4：每个用例新建临时库，真实 `cmd/admin migrate`（0001→0039，
  链连续；重复 migrate no-op）；真实 `cmd/api` 二进制（真实 HMAC 签名对象链接、
  文件系统 `staged/`/`sealed/` 字节、internal mTLS）；真实 `cmd/maintenance` 进程：
  常规双池；换模型用例用第二个真实 API + 第二个 maintenance（同一 DB/对象目录、
  自己的端口/证书、`ORCHARA_EMBEDDING_MODEL=local-hash-v2`）；竞争用例用 work-only
  (`--kinds document_ingest`) 与 finalize-only (`--kinds artifact_finalize`) 两个真实进程。
- 输入：`tests/support/t055.py::SAMPLE_DOCUMENT`、本票 `CONTENT_V2/CONTENT_V3` 与
  `REVISED_DOCUMENT` 确定性 UTF-8；源 artifact 的 SHA-256 由测试端计算；索引/ chunk
  citation 与封存对象、真实 chunk 行独立核对；upgrade 用例先以 `--migrations-dir`
  只迁到 0038，插入真实 artifact/chunk 行后再完整迁移。
- 版本：`local-hash-v1`（默认 dev-stub）、`local-hash-v2`（第二确定性 dev-stub）、
  `orchara-text-parser/1`、`orchara-paragraph-overlap-chunker/1`、迁移 0039、
  缓存键版本 `knowledge-source-revisions/2`；真实 embedding provider blocked。
- 时序裕量（BRIEF §6.1）：竞争用例不使用亚秒断言；parked 检测只等待"handler 已上传
  任一 chunk body"（handler 在 finalize 池被扣住时不可能完成），源 revision 推进/删除
  与 finalize 放行都在 handler 的真实 60s 等待窗口内（本轮实测两个竞争用例分别约
  45s/35s 含初始摄取）。没有"短睡眠后断言无副作用"的检查，所有结论从权威行读取
  （`activated_at`/state/error_code/`deleted_at`）。

## 5. stub / real、blocked、not_run（分开记录）

| 项 | 结论 | 具体条件 |
|---|---|---|
| 版本化索引/激活事务/迟到竞争 | **real executed** | 真实 PG 行 + 真实编译二进制 + 真实 HTTP/mTLS + 真实 maintenance 挂起/放行；见 §2 |
| 换 embedding 模型 | **real executed（dev-stub 配置切换）** | `local-hash-v2` 是确定性本地 stub，不是语义模型；两个真实进程/两个索引版本/两套向量，检索只读活动索引 |
| 真实 embedding provider | **blocked** | 继承 T055/T067：无凭据、构建无 adapter；重试用例把它作为终端失败路径（`embedding_provider_not_configured`），从不回退 stub |
| 历史索引/失败暂存 chunk 物理清理 | **not_run / 后续** | 本票保留它们作审计；清理/保留策略属 F07 或后续运维票 |
| ANN/pgvector/规模性能 | **not_run / 后续** | 继承 T064/T067 边界；本票只改索引选择，不改精确扫描/候选上限 |
| 浏览器 DOM 驱动页面 | **not_run** | 只做 `GET /knowledge` 结构与 Node 执行纯 helper（与既有页面票同边界） |
| 全量 `pytest tests`（≈67 分钟） | **not_run（orchestrator）** | BRIEF §3：本票跑 `tests/f10` 两次背靠背 + `tests/tooling` + Go 门禁 |
| upgrade backfill | **real executed** | `test_t061_migration.py::test_the_upgrade_backfills_a_ready_document_into_an_active_index`：0038 库 + 旧 ready 文档/块 → 0039 backfill 后指针/索引/ chunk 归属正确 |

## 6. 下一票接口（复用点）

- 表：`platform.knowledge_index_versions`（非契约，additive）；关键列
  `document_id/version/document_revision/embedding_model_revision/embedding_dimension/
  state/chunk_count/error_code/activated_at/superseded_at`；
  `knowledge_documents.active_index_id`/`active_embedding_model_revision`；
  `knowledge_chunks.index_version_id`（身份 `(index_version_id, chunk_no)`）；
  `background_jobs.expected_index_version_id`。
- Go：`knowledge.Service.ReindexDocument`、`knowledge.IndexVersion`（`Stage()`）、
  `knowledge.RetrievalIndexInfo`、`knowledge.ErrIndexBuildInProgress`/
  `ErrStaleRevision`、`insertIndexVersionTx`/`enqueueIndexBuildTx`（同包测试可复用）；
  `background.CodeStaleRevision`/`CodeEmbeddingModelChanged`；
  `background.DocumentIngestBinding.IndexVersionID/EmbeddingModelRevision/EmbeddingDimension`；
  `embedding.StubModelRevisionV2`。
- HTTP：非契约 `POST /api/v1/knowledge-documents/{id}/reindex`（ADR 0017）；诊断 additive
  `active_index/active_embedding_model_revision/indexes[]`；检索/回答 additive
  `retrieval.index`；错误码 `index_build_in_progress`（409）与 `stale_revision`（内部 409）。
- 守卫契约：只有 `guardDocumentIngestCompletion` 能移动 `active_index_id`；新后台 kind/
  新索引维护写路径必须复用它，否则会绕过 revision/tombstone/model/dimension 检查。
- 缓存：T067 `source_revision_digest` 已含 active index id/model；任何新的激活/失效 seam
  必须同样进入该 digest 与命中复检 SQL。

## 7. 与既有票/契约的关系（如实）

1. **无契约改动**：`contracts/**`、`docs/spec/**` 未改；`sync_spec --check` no drift。
   非契约面（reindex、诊断/检索 additive 元数据）记录在 ADR 0017，沿用 ADR 0005/0007/0010
   先例。
2. **合并集成分支**：开工时集成的 T060 已把 PDF 迁移/ADR 重编号为 0038/0016，本票迁移为
   `0039_index_revisions.sql`、ADR 为 0017；收尾再次合入 integration（如有新 tip）见 §3。
3. **既有测试的预期更新（3 处，diff 可见，非放宽）**：
   - `tests/f10/test_t055_migration.py`：旧 chunk 唯一约束 `knowledge_chunks_unique_no`
     按新身份被替换，断言改为 `knowledge_chunks_index_version_no` + 旧约束不存在；
   - `tests/f10/test_t055_ingestion.py`：retrieval 元数据精确集合加入 T061 additive
     `index`（T067 已对 `cache` 做过同类更新）；
   - `tests/f10/test_t067_cache.py`：原来用裸 SQL "模拟 T061 激活切换"，现在调用
     `t061.activate_staged_index_version` 写入与守卫相同的索引版本/指针行（语义不变，
     旧断言保留）。
4. **T055 语义保持**：初始失败文档仍 revision+1 重试、失败文档 active_revision 为 NULL；
   已激活文档在重建期间/失败后旧索引仍可读（新语义），检索/引用/回答/缓存均改为
   "活动索引版本"谓词。
5. **唯一破坏性迁移语句**：删除旧 chunk 唯一约束并由新唯一索引取代；其余为 additive，
   且 upgrade backfill 在添加 ready 约束之前执行（有真实升级用例）。

## 8. 参考

- ADR：`product/docs/adr/0017-f10-index-revisions.md`（本票）；`0010`/`0013`/`0015`/`0016`
  （F10 基础/表格/缓存/PDF）。
- 基础：`product/reports/f10/T055-knowledge-ingest.md`、
  `T059-tabular-ingest.md`、`T060-pdf-ingest.md`、`T067-retrieval-cache.md`。
