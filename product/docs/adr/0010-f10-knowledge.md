# ADR 0010：F10 知识摄取的存储偏差、非契约检索面与激活边界

- 状态：已接受
- 日期：2026-11-04
- 相关：F10 / T055（GitHub #56，规划 ID F10.01）；
  分支 `t/t055-knowledge-ingest`；报告 `product/reports/f10/T055-knowledge-ingest.md`；
  迁移 `product/db/migrations/0034_knowledge.sql`；
  后续：#60/T059（CSV/XLSX）、#61/T060（PDF）、#62/T061（版本重建/激活）、
  #64/T064（检索 ACL/规模）、#68/T067（索引维护/缓存）。
  先例：ADR 0005（`tool_run` 非契约桥）、ADR 0007（F06 非契约内部端点）。

## 背景

`contracts/database.sql:317-334` 固定了 `knowledge_bases` / `knowledge_documents` /
`knowledge_chunks` 三张表，`contracts/openapi.json` 固定了六个操作
（`createKnowledgeBase`、`listKnowledgeBases`、`getKnowledgeBase`、`ingestDocument`、
`listKnowledgeDocuments`、`getKnowledgeDocument`）。但契约里**没有检索操作**，
也没有"打开一个 citation"的操作；同时：

1. 契约假设 pgvector（`embedding vector`，文件头要求 `CREATE EXTENSION vector`）。
   本开发集群 PostgreSQL 16.4 只安装了 `plpgsql`，`pg_available_extensions` 没有
   `vector`，**不可安装**（T055 实施时复探；与 0006/0007/0008/0009 迁移头注释一致）。
2. 本环境没有任何 embedding provider 凭据，也没有 provider adapter。
3. T055 的切片只解析 UTF-8 txt/md；CSV/XLSX（T059）与 PDF（T060）随后扩展。
4. ROUTING-MEMORY-KNOWLEDGE §3 要求"检索在 SQL 候选阶段限制可读文档，随后排序及
   top_k"、"解析失败可重试，不把空索引标 ready"，并且每个 chunk 必须能解析到
   当前可读来源（doc/version/span）。

因此本 ADR 记录三项决定：存储/向量的环境偏差、检索与引用的非契约表面、以及
"只有完成守卫能激活 revision"的边界。

## 决策

### 1. 向量存储：`double precision[]` + 固定 384 维，不引入 pgvector

- `platform.knowledge_chunks.embedding` 为可空 `double precision[]`，
  `embedding_dimension` 显式记录维度；迁移用
  `knowledge_chunks_embedding_dimension_local CHECK (embedding IS NULL OR
  (array_length(embedding,1) = 384 AND embedding_dimension = 384))` 把 384 钉在数据库里，
  Go 常量 `embedding.Dimensions = 384` 与它一一对应。**没有** `CREATE EXTENSION vector`，
  也没有改 `contracts/**` 或 `docs/spec/**`。
- 检索 = 在 SQL 完成 ACL/当前版本过滤后，对**有界候选集**
  （`knowledge.MaxCandidates = 2000`）在可信 API 进程内做精确余弦排序，再取 top_k。
  这不是 ANN，也不承诺规模性能；**精确扫描的延迟/规模基准 `not_run`**。
- 偏差归属：**T064（#64）** 拥有检索 ACL/规模方案与是否引入 pgvector/ANN；
  **T067（#68）** 拥有索引维护与检索缓存。若未来换用 pgvector 或改变维度，属于新的
  `embedding_model_revision` + 新迁移，且检索仍只匹配当前模型 revision（本 ADR §3）。

### 2. embedding 缝：确定性 `local-hash-v1` + 真实路径 fail closed

- `internal/embedding` 是一个 Go 缝：空/`stub` 的
  `ORCHARA_EMBEDDING_PROVIDER` 选择确定性本地 hash embedder，模型 revision 固定为
  **`local-hash-v1`**（384 维、L2 归一化、SHA-256 分桶，跨进程稳定）。
- 任何其它 provider 值走真实路径；本构建没有 adapter/凭据，
  `Embed` 返回 `ErrNotConfigured`，handler 把文档失败为
  `embedding_provider_not_configured`（business，不重试、永不 ready），HTTP 读路径 503
  `provider_not_configured`。**绝不**在真实 provider 不可用时静默回退到本地向量。
- 每个 chunk 行与 citation 都记录 `embedding_model_revision`；检索只返回与当前
  service revision 一致的候选，因此本地向量与真实向量不会混用。报告把 stub 与 real
  分开记录：本票真实 provider = `blocked`（无凭据/adapter）。

### 3. 非契约检索/引用/诊断表面（ADR 0005 先例）

这些路径**不在** `contracts/openapi.json` 中；它们只在本 ADR 与 T055 报告中登记，
`python tools/sync_spec.py --check` 必须保持 no drift；契约未来若增加检索操作，
以契约替换这些形状。所有错误仍用既有信封
`{"error":{code,message,request_id,details?}}` 与既有码表
（`resource_not_found`、`validation_failed`、`insufficient_scope`、`forbidden`、
`csrf_invalid`、`precondition_failed`、`provider_not_configured`、
`object_store_unavailable`），不新造第二套 HTTP 错误词汇。

```
POST /api/v1/knowledge-bases/{knowledge_base_id}/retrieval-queries
body: {"query": "1..2000 chars", "top_k": 1..50 (default 8)}
200: {
  "items": [{
    "chunk_id": uuid, "document_id": uuid, "document_revision": int,
    "document_state": "ready", "chunk_no": int, "score": number,
    "text": "chunk body text",
    "span": {"start_char": int, "end_char": int, "start_line": int, "end_line": int},
    "citation": { ...存储的 citation 文档（见下）... }
  }],
  "next_cursor": null,
  "retrieval": {"embedding_model_revision": "local-hash-v1", "top_k": int,
                "acl_epoch": int}
}
```

- 空结果 = 200 + `items: []`，**不是错误**。
- `acl_epoch` 在本票取 `knowledge_bases.revision`（当前授权/资源版本）。T067 的缓存键
  应至少包含 principal、acl_epoch、embedding revision、query、top_k，且缓存命中仍需
  重新检查资源 tombstone（本票不加缓存表，缓存由 T067 拥有）。

```
GET /api/v1/knowledge-citations/{chunk_id}
200: {chunk_id, document_id, document_revision, document_state, chunk_no,
      text, span, citation}
```

- 每次打开都重新在 SQL 里校验：owner ACL（owner-only，管理员不例外）、知识库与文档
  tombstone、`state='ready'`、`active_revision = revision`、
  `chunk.document_revision = revision`、chunk body artifact ready 且未删除。
  任一不满足都是同一个 **404 `resource_not_found`**，不带正文、不区分"不存在/已撤销/
  旧版本/他人的"，避免枚举。这是 ROUTING §3"文档撤销后打开引用显示不可用、不继续提供
  缓存正文"的直接实现。
  （T061 起该谓词改为"活动索引版本"形状，见 ADR 0018 §4；2026-11-05 复核后它只由
  `internal/knowledge/readable_chunk.go` 的 `currentReadableChunkSQL` 定义，检索、
  引用打开与 T067 缓存命中复检共用同一片段，避免权限条件三处漂移。）

```
GET /api/v1/knowledge-documents/{document_id}/diagnostics
200: {document_id, state, revision, active_revision, parser_version,
      chunker_version, embedding_model_revision, error_code, chunk_count,
      background_job: {id, state, error_code, attempt_count} | null}
```

- 契约 `KnowledgeDocument` 是 `additionalProperties:false` 的五字段，容纳不下失败原因；
  "失败解析在页面可诊断"需要一条 owner-only 的非契约读。它只暴露安全码与计数，
  不含 object key、字节或他人数据。

### 4. 存储形状：每 chunk 一个封存 body artifact + citation

- `knowledge_chunks.body_artifact_id NOT NULL` 指向一个真实 `platform.artifacts` 行与
  真实封存对象，字节**恰好是该 chunk 的文本**；它由既有
  `upload -> complete -> artifact_finalize` 路径产生（purpose `knowledge_chunk`），
  不新增第二种上传机制。
- 每个文档 revision 的 body artifact 数量有界：`knowledge.MaxChunksPerDocument = 1000`；
  超过则文档失败为 `ingest_too_many_chunks`，不会产生无界 artifact 集合。
- `citation`（jsonb，唯一持久溯源文档）字段：
  `document_id`、`document_revision`、`chunk_no`、`span`
  （rune 字符偏移 `start_char`/`end_char` + 1-based 闭区间行号）、`offset_unit: "rune"`、
  `source_artifact_id`、`source_sha256`（源 artifact 封存字节的 SHA-256）、
  `source_media_type`、`text_normalization`（`none` 或 `utf8_bom_stripped`）、
  `parser_version`、`chunker_version`、`embedding_model_revision`。
- 独立可核验性：源 artifact 字节 hash = `source_sha256`；
  `[]rune(源文本)[start_char:end_char] == chunk 文本`；
  `text[:start_char].count("\n")+1 == start_line`。测试用这些等式而不是回读 chunk 行。

### 5. 激活边界：document 只能由完成守卫置 ready/failed

- `ingestDocument` 只写 `knowledge_documents`（`scanning`）并在同一事务里入队一个
  `document_ingest` job；handler 只解析/分块/embedding/上传 chunk body/写入暂存 chunk 行，
  **不写** `state`、`active_revision`、`error_code`。
- `internal/background.guardDocumentIngestCompletion` 是唯一激活者：同一事务内锁文档、
  校验 job 的 `expected_revision`、重读暂存 chunk 集合（数量、chunk_no 连续、citation
  与 job/document/revision/source 一致、embedding 全部 384 维且单一模型 revision、
  body artifact 全部 ready 且属 job owner），然后
  `state='ready', active_revision=revision`；任何一项不满足 → `failed` + 具体码。
- 失败语义：空解析 `ingest_empty_content`、非法 UTF-8 `ingest_invalid_encoding`、
  无解析器 `ingest_unsupported_media_type`、源不可用/被删/换版
  `ingest_source_unavailable`/`ingest_source_changed`、片段封存超时
  `ingest_chunk_artifact_timeout`（可重试，最多 3 次）、暂存不完整
  `ingest_incomplete`、citation/维度不一致 `ingest_citation_invalid`、
  超上限 `ingest_too_many_chunks`、取消 `ingest_cancelled`。**没有**任何路径能从
  空解析/失败进入 ready。
- 重试：`retryableByKind[document_ingest]` 仅
  `transient_storage_io`/`transient_internal`/`lease_expired`/`ingest_chunk_artifact_timeout`；
  其它（含未知码）一律 business，立即失败（fail safe）。failed 文档再次
  `ingestDocument` 同一 artifact 会创建 `revision+1` 与一个新 job（"解析失败可重试"），
  ready/indexing 文档的重复请求幂等返回原文档，不产生第二个 job。
- 契约 `ingestDocument` 没有 `Idempotency-Key`：去重依据是资源状态
  （同 KB + 同 source artifact → 同一 document）与
  `background_jobs UNIQUE(kind, dedup_key)`，dedup key =
  `knowledge-doc:<source_artifact_id>:<artifact_revision>:<document_id>:<document_revision>`。
  这是 T061 版本替换/重新激活 seam 的当前实现。

### 6. 解析器/分块器 seam（T059/T060 扩展点）

- `internal/knowledge.Parser` 接口（`MediaType`/`Version`/`Parse`）与闭合注册表当前只
  注册 `text/plain`（`orchara-text-parser/1`）与 `text/markdown`
  （`orchara-markdown-parser/1`）。CSV/XLSX/PDF 不注册 = 422 `validation_failed`，
  绝不"猜一个解析器"。
- `text/markdown` 的准入是 purpose-scoped 扩展：仅 `purpose=knowledge` 接受
  （`artifacts.SupportedUploadMediaTypeForPurpose`），其它 purpose 仍拒绝；字节仍经
  T028 的文本嗅探准入。
- 分块器 `orchara-paragraph-overlap-chunker/1`：段落打包到 `MaxChunkChars=1200`
  rune；超长段落按 1200 rune 硬切、尾部 `OverlapChars=200` rune 重叠并在词边界对齐；
  span 在裁剪空白后重算。常量属于 chunker revision：改动常量即新 revision。
- T059/T060 需要的是"新增 Parser + SourceSpan 映射"；T061 需要的是
  `knowledge_documents.revision`/`active_revision` 与 chunk 的 `document_revision`；
  T064 需要的是 SQL 过滤谓词与 `acl_epoch`；T067 需要的是模型 revision 与缓存键。

## 后果

- 本票交付 txt/md 的完整用户路径（上传→封存→摄取→可检索→打开引用），所有失败面可诊断；
  但检索是精确扫描、非 ANN；CSV/XLSX/PDF、版本替换、删除治理、缓存均不在本票。
- `docs/spec/**`/`contracts/**` 未改；`sync_spec --check` no drift；迁移只新增
  `0034_knowledge.sql`，0001–0033 未改。
- not_run / blocked（见报告 §5）：真实 embedding provider（无凭据/adapter，blocked）；
  精确扫描性能/规模基准（not_run，T064/T067）；浏览器 DOM 驱动页面（not_run，与既有页面
  票同一边界）；CSV/XLSX（T059）、PDF（T060）、版本重建（T061）、删除治理（F07/T058）。

## 后续更新

- 2026-11-04：T059（#60）已按 §6 的扩展点注册 CSV/XLSX 解析器
  （`orchara-csv-parser/1`、`orchara-xlsx-parser/1`），并扩展 §3 的诊断读为只读派生的
  `detail`（公式/上限的行列位置）；表格行范围、上限与失败码见 ADR 0013。§3 的检索/
  引用/诊断路径与 §5 的激活边界未变。

