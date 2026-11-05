# ADR 0018：F10 版本化知识索引、激活事务与非契约重建/进度面

- 状态：已接受
- 日期：2026-11-04
- 编号：合并 `integration/f13a-f15a-f09-f10` 时 T056 已占用 ADR 0017
  （evaluation case results），本票 ADR 让号为 **0018**（分支上曾临时写作 0017，
  合并后统一为 0018）。
- 相关：F10 / T061（GitHub #62，规划 ID F10.03）；分支 `t/t061-index-revisions`；
  报告 `product/reports/f10/T061-index-revisions.md`；
  迁移 `product/db/migrations/0039_index_revisions.sql`、
  复核修复 `product/db/migrations/0041_ingest_upgrade_recovery.sql`（§6）；
  基础：T055（#56）的摄取链/完成守卫/非契约检索与诊断面（ADR 0010）、
  T059（#60，ADR 0013）、T060（#61，ADR 0016）、T064（#64，ADR 0012）、
  T067（#68，ADR 0015）；先例：ADR 0005/0007（非契约面）。
- 后续：F07 删除治理的物理清理、索引历史保留策略、真实 embedding provider adapter。

## 背景

票面要求：管理员更新资料或换 embedding 模型后重建索引并看到真实进度；旧源 revision
的迟到任务**不能覆盖新版本，也不能复活已删除文档**；换模型不能混向量；失败可重试。
T055 的切片只有一条文档状态权威和一个 `active_revision` 指针：

- chunk 身份是 `(document_id, document_revision, chunk_no)`，同一源 revision 换模型
  重建会与旧向量冲突，无法把两套向量分开保存；
- 完成守卫只核对期望的文档 revision，没有"这次构建属于哪个索引版本"的事实；
- 无法表达"重建进行中/失败但旧索引仍可读"的独立状态，重建失败会清空 `active_revision`；
- 没有可观察的构建版本/模型/进度/失败原因。

## 决策

### 1. 三类 revision 分离，激活指针独立

新增 `platform.knowledge_index_versions`（非契约 additive 表）：
`(document_id, version, document_revision, embedding_model_revision,
embedding_dimension, state, chunk_count, parser_version, chunker_version,
error_code, activated_at, superseded_at, created_at, updated_at)`。

- `knowledge_documents.revision` = **源 revision**（只有替换源 artifact 才 +1）；
- `knowledge_index_versions.version` = 每文档的**索引构建版本**，一行对应一次
  `(源 revision, embedding 模型 revision)` 构建尝试；
- `knowledge_chunks.index_version_id` = **chunk 集所属索引**；chunk 身份从
  `(document_id, document_revision, chunk_no)` 改为
  `(index_version_id, chunk_no)`，因此同一源 revision 的两套向量永不冲突；
- `knowledge_documents.active_index_id` = **唯一激活指针**；
  `active_embedding_model_revision` 冗余记录活动索引的模型 revision；
  `active_revision` 保留为活动索引的源 revision（T055 语义与 ready 约束不变）；
- `background_jobs.expected_index_version_id` 把一次 `document_ingest` job 绑定到
  一个索引版本，守卫据它核对暂存集合与激活决定。

迁移对已存在数据做一次性 upgrade backfill：当前 `ready` 文档得到一个
`version=1` 的活动索引行，现有 chunk 挂到该索引，文档指针指向它。新库上为空操作。
唯一破坏性语句是删除旧 chunk 唯一约束（由新身份取代），其余为 additive。

`state` 生命周期：`building -> ready -> superseded` 或 `building -> failed`。
同一文档同一源 revision 只允许一个 `building`（partial unique index）；**新的源
revision 可以在旧 job 运行时开始构建**，这正是激活守卫要裁决的竞争。

旧索引在被取代前保持 `ready` 且可读：重建期间/失败后旧活动索引仍是检索来源，
这是"失败的重建保留旧活动索引"的结构基础。

### 2. 激活事务（唯一写指针者）

`internal/background.guardDocumentIngestCompletion` 仍是非契约内部完成守卫，
在同一事务中：

1. `SELECT ... FROM knowledge_documents WHERE id=$1 FOR UPDATE`；行不存在/已
   tombstone/`deleting|deleted` → job 失败 `source_deleted`，索引版本失败，
   **不激活、不改文档**；
2. 读 `FOR UPDATE` 的索引版本：必须属于该文档、仍 `building`；期望源 revision
   必须等于文档当前 `revision`，且索引行的 `document_revision` 也等于它；
   否则索引版本置 `failed(stale_revision)`、job 失败，文档不动；
3. 成功提议还要求文档仍 `indexing`；重读暂存 chunk 集合：非空、
   `chunk_no 0..n-1` 连续、citation 的 document/revision/source 一致、
   embedding 非空且维度等于索引版本固定维度、模型 revision 全部等于索引版本、
   body artifact 全部 ready 且属 job owner；任一不满足 → 具体失败码；
4. 全部通过：索引版本 `ready`（写真实 `chunk_count`/`chunker_version`/`activated_at`），
   文档 `state=ready`、`active_index_id` 指向它、`active_revision` = 该索引源
   revision、`active_embedding_model_revision` = 该索引模型，旧活动索引置
   `superseded`。指针移动与索引状态在同一事务，不存在"部分激活"。

不满足激活条件时只写索引版本的失败码；文档的 `active_index_id/active_revision/
active_embedding_model_revision` **保持不变**。可重试失败（基础设施码）连索引版本
都不改，job 回到 ready 等下一次尝试。取消 = `ingest_cancelled` 终态失败。

### 3. 重建入口与非契约进度读

契约没有 reindex 操作，新增已登记的非契约入口（ADR 0005/0007 先例；
`contracts/**`/`docs/spec/**` 未改，`sync_spec --check` 保持 no drift）：

```
POST /api/v1/knowledge-documents/{document_id}/reindex
body: {} 或 {"source_artifact_id": uuid}   （owner-only，CSRF/scope 同既有面）
202: {document_id, state, revision, index_version_id, index_version,
      embedding_model_revision, job_id}
```

- 不传源 artifact：以当前源 revision + 当前部署的 embedding 模型创建**新索引版本**
  和 job；同一源构建进行中 → 409 `index_build_in_progress`；
- 传入新的、owner 可见且 ready 的源 artifact：源 revision +1、
  `source_artifact_id` 更新、创建新索引版本；正在运行的旧 job 不会被取消，而是由
  激活事务在完成时以 `stale_revision` 拒绝。这是票面"管理员更新版本后重建"的路径；
- 重新提交同一文档（`ingestDocument`）在活动索引模型 revision 与当前部署模型不同时
  也走新索引版本（同源 revision），否则保持 T055 的幂等返回；失败文档重试仍可提交。

进度是**持久化事实的投影**，不是伪造百分比：既有
`GET /knowledge-documents/{id}/diagnostics` 增加 additive
`active_index`、`active_embedding_model_revision`、`indexes[]`；每项含
`version/document_revision/embedding_model_revision/embedding_dimension/state/stage/
chunk_count/error_code/active/activated_at/superseded_at/background_job{state,
error_code,attempt_count}`。`stage` 只由 state + 是否已有暂存 chunk + job 状态派生
（queued/building/staging/complete/failed/superseded），没有百分比。

### 4. 换 embedding 模型：独立索引 + 检索只读活动索引

- 部署配置 `ORCHARA_EMBEDDING_MODEL` 可选择两个**已编译的确定性 dev-stub revision**：
  `local-hash-v1`（默认）与 `local-hash-v2`（不同 SHA-256 字节取桶/符号，仍是 384 维、
  跨进程稳定）。这不是语义模型，报告如实标注；未知 stub revision fail closed，
  不会用 v1 向量冒充 v2。真实 provider 仍无 adapter/凭据，`ErrNotConfigured`
  且绝不回退 stub（继承 T055/T067 边界）。
- 换模型 = 新索引版本（同源 revision 也可以是新源 revision），旧 chunk 集留在旧
  索引版本；检索 SQL join `active_index_id`，要求
  `iv.state='ready' AND kc.index_version_id=iv.id AND
  kc.embedding_model_revision=iv.embedding_model_revision=当前查询模型`，
  只对活动索引的候选打分。旧部署进程用旧模型查询时得到空结果而不是混排向量。
- 非契约检索/回答响应增加 additive
  `retrieval.index = {embedding_model_revision, active_indexes, excluded_indexes}`：
  写明本回答实际读取的活动索引模型 revision，以及因模型不同被排除的活动索引数
  （`excluded_indexes>0` 表示部署模型已切换但尚未重建，不会静默混用）。
- T067 缓存键的 `source_revision_digest` 加入 `active_index_id` 与
  `active_embedding_model_revision`；命中复检 SQL 与检索/引用使用同一活动索引谓词。
  缓存仍只是派生状态，激活切换后旧键不可达。

### 5. 失败可重试与旧索引保留

- 非可重试失败：索引版本 `failed(error_code)`；文档 `state=failed`+`error_code`，
  但活动指针/活动 revision/活动模型**不变**，旧索引继续可读；页面/诊断显示失败版本。
- 再次 `reindex`（或失败文档同源 `ingestDocument`）创建新索引版本，旧失败保留为历史；
  成功激活后旧活动索引变 `superseded`。有活动索引的失败重试**不再** bump 源 revision
  （源未变）；从未激活的初始失败仍按 T055 语义 bump 源 revision。
- 可重试基础设施失败不写索引/文档状态，job 回 ready。

### 6. 升级恢复：0039 之前排队的摄取尝试不得永久卡住（2026-11-05 复核修复）

外部复核发现：0039 之前入队/在跑的 `document_ingest` job 没有
`expected_index_version_id`，而 0039 只回填了已经 `ready` 的文档。升级后这类 job 被
完成守卫以 `stale_revision` 拒绝、**却不改文档**，于是文档永远停在 `scanning`/`indexing`；
同一 artifact 重新摄取命中 T055 幂等分支返回同一卡住文档，同源重建又被
`index_build_in_progress` 拒绝——文档再也无法摄取，违背 #62 的"失败可重新摄取"。

**决策：迁移把这类尝试"失败掉"，而不是给它们新建索引版本再绑定。**

- 为什么不是绑定：同一文档可能有多条历史重试 job；它们的 `expected_revision` 可能已过期；
  两条 job 绑到同一个 building 索引版本会把两套暂存 chunk 写进同一版本。失败掉是确定的，
  且恰好落在"失败可重试"的既有语义上。
- `db/migrations/0041_ingest_upgrade_recovery.sql`（幂等、可重跑、不复活删除）：
  1. 无索引绑定的非终态（`ready`/`leased`）`document_ingest` job →
     `failed/ingest_index_unbound`（`failure_class=business`、清租约、写 `finished_at`）；
  2. 这些 job 的文档若仍处 `uploaded`/`scanning`/`indexing` 且未删除 →
     `failed/ingest_index_unbound`。`ready` 文档、以及任何 `deleted_at` 非空的文档都不动。
- 运行时自愈（不依赖迁移是否跑过）：
  - 完成守卫遇到无绑定、索引版本缺失、索引版本属于其它文档或已非 `building` 的尝试，
    以 `ingest_index_unbound` / `stale_revision` 结束 job，并（在没有其它 live 构建时）
    把文档置 `failed`，下一次摄取即可新建 revision/索引版本；
  - `CreateDocument`/`ReindexDocument`（以及 `insertIndexVersionTx` 的"每源 revision 一个
    building"前置检查）用 `background.LiveDocumentBuildTx` 判定"构建是否真的在跑"：
    非终态 job + 同一文档同一源 revision 且仍 `building` 的索引版本。没有 live 构建时，
    放弃的索引版本置 `failed/ingest_build_abandoned`、文档置 failed，然后照常开始新构建；
    **有** live 构建才 409 `index_build_in_progress`。所以"文档在 scanning"不再等于
    "有构建在跑"。
- 新增稳定失败码：`ingest_index_unbound`（pre-0039 job 无索引绑定）、
  `ingest_build_abandoned`（构建已无 live job 的文档被回收）。两者都是 business，
  不可重试，页面/诊断可见；都不是契约错误码（`background_jobs.error_code` 本来就不在契约里）。
- 边界：完成守卫仍是唯一写 `ready`/`active_index_id`/`active_revision` 的地方；恢复路径只写
  `failed`（且只对 `uploaded`/`scanning`/`indexing`），从不把文档变成 ready。租约耗尽
  （sweep 以 `lease_expired` 终结 job、无文档写入者）留下的 `scanning` 文档由下一次摄取
  自愈；sweep 本身仍不写文档状态（不在本轮复核范围）。
- 证据：`tests/f10/test_t061_upgrade_recovery.py`（真实 pre-0039 schema → 真实 queued job →
  真实 `admin migrate` → 真实 maintenance/HTTP 重摄取到 `ready`；迁移后仍存活的 legacy job
  由真实 handler/守卫结束；无 live job 自愈；live 构建仍幂等、仍 409）。

## 后果

- 交付：独立 revision 存储、真实后台重建、带守卫的激活切换、迟到任务拒绝
  （`stale_revision`/`source_deleted`）、失败保留旧索引且可重试、换模型不混向量、
  页面显示索引版本/模型/进度/失败/活动切换。
- 历史索引与 chunk 行保留作审计与旧活动索引读取；**物理清理/保留策略不在本票**
  （F07/后续运维票）。失败的构建可能留下暂存 chunk 行（`index_version_id` 指向失败
  版本，检索不可见），清理同样属 F07。
- 本票验证了真实进程竞争：work-only maintenance 挂起 handler（等待被扣住的
  artifact_finalize 池）→ 真实 reindex 推进源 revision 或写 tombstone → 放开 finalize
  池让旧 handler 真正完成 → 激活事务拒绝。详见报告。
- 未做/边界：真实 embedding provider（blocked，无凭据/adapter）；ANN/pgvector 与
  规模基准（继承 T064/T067 not_run）；浏览器 DOM 驱动（not_run，与既有页面票同边界）；
  删除治理 job（F07）；索引的物理 GC（后续）；`excluded_indexes` 只统计有活动索引的
  文档，不代替“缺索引”诊断。
- 契约：`contracts/**`、`docs/spec/**` 未改；新增迁移 `0039_index_revisions.sql` 与
  复核修复的 `0041_ingest_upgrade_recovery.sql`（只修数据，无新 schema 对象）、
  非契约 `reindex` 入口与 additive 诊断/检索元数据；`sync_spec --check` no drift。
