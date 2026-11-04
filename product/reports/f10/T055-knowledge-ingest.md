# F10 T055 验收：上传文本资料后检索到可追溯片段

- 任务：F10 / GitHub Issue #56（票 `T055`，规划 ID F10.01），分支
  `t/t055-knowledge-ingest`（worktree `E:\orchara-worktrees\t055`）；父门禁 #53–#68
  集成批次。
- 状态：**`passed`**（本票范围 = `createKnowledgeBase` / `listKnowledgeBases` /
  `getKnowledgeBase` / `ingestDocument` / `listKnowledgeDocuments` /
  `getKnowledgeDocument`，txt/md 的真实受控摄取、`document_ingest` 后台 kind 的
  handler + 完成守卫，以及 ADR 0010 记录的非契约检索/引用/诊断面 +
  `/knowledge` 页面 + 真实栈测试）。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实
  HTTP/mTLS + 真实封存对象字节 + 真实 maintenance 双池进程 + Node 执行页面逻辑；
  无 mock、无跳过。
- 边界（如实）：真实 embedding provider **blocked**（本环境无凭据、构建无 adapter，
  两条路径都 fail closed，见 §5）；精确扫描的规模/延迟基准 **not_run**（T064/T067）；
  浏览器 DOM 驱动页面 **not_run**（与 T023/T027/T029/T031/T032/T033/T037/T039 同一边界）；
  CSV/XLSX（#60/T059）、PDF（#61/T060）、版本重建/激活切换（#62/T061）、
  检索缓存（#68/T067）、删除治理（F07/T058）不在本票。
- 契约偏差：`embedding double precision[]` 固定 384 维（pgvector 不可安装），
  `text/markdown` 仅对 `purpose=knowledge` 准入，检索/引用/诊断是非契约路径；
  均记录在 ADR 0010，`contracts/**`/`docs/spec/**` 未改，`sync_spec --check` no drift。

## 0. 工作记录（简短）

1. 读 `BRIEF.md`、票面 #56、`ROUTING-MEMORY-KNOWLEDGE.md` §3–§4、`BACKGROUND-JOBS.md`
   §2–§3、`contracts/database.sql:317-334`、探索笔记 `notes/f09-f10-contract.md`
   §4a/§4b/§4c；核对既有缝：`internal/artifacts`（upload→finalize→ready、sealed 读）、
   `internal/background`（kind 白名单、retry 表、完成守卫、JobScope）、
   `cmd/maintenance`（mTLS client、双池）、`internal/api`（authz/CSRF/scope、错误信封）、
   `0015`/`0017` 迁移写法、`tests/support/{stack,fixtures,t026,t028}.py`、
   `tests/f06/test_t039_pages.py` 的 Node 页面执行方式。
2. TDD 垂直切片（每片先红后绿）：
   - 迁移 0034 + 约束（链连续、384 CHECK、worker 零授权）→ 真实 migrate；
   - `internal/embedding`（确定性/维度/真实路径 fail closed）与
     `internal/knowledge`（txt/md 解析、BOM、空/非法 UTF-8、确定性分块、span 回切）
     → Go 单测；
   - KB CRUD/游标/owner-only（A/B/admin 404）/CSRF/API-key scope → 真实 HTTP；
   - txt/md 上传→finalize→`ingestDocument`→真实 maintenance→ready→检索→citation
     独立真值（源 hash + rune 切片 + 行号 + body 字节）→ 真实栈；
   - 当前 revision/tombstone 过滤、撤销后 citation 404、他人/管理员 404；
   - 空解析/非法 UTF-8 永不 ready、失败可诊断、可重试失败→重试成功、租约丢失→恢复；
   - `/knowledge` 页面结构 + Node 执行 `knowledge.js`（路径/请求体/引用一致性/
     失败码/空结果）。
3. 跑门禁（§3），写本报告/PROGRESS/ADR 0010；按 feat/test/docs 提交；未推送、未关 issue、
   未改 `docs/spec/**`/`contracts/**`、只新增迁移 0034（0001–0033 未改）。

## 1. 交付内容

### 1.1 迁移 `0034_knowledge.sql`

- `platform.knowledge_bases`：契约列 + `created_at`；owner-only 索引。
- `platform.knowledge_documents`：契约列 + 本票 seam：
  `active_revision`（T061 激活指针，`ready ⇒ active_revision = revision`）、
  `chunker_version`、`embedding_model_revision`、`error_code`（只由守卫写）、
  `updated_at`；`state` 用契约枚举 CHECK。
- `platform.knowledge_chunks`：契约列 + `embedding_dimension`、`created_at`；
  `UNIQUE(document_id,document_revision,chunk_no)`；
  `CHECK (embedding 为空 或 (array_length=384 AND dimension=384))`。
- **偏差**：契约的 `embedding vector` 在本集群不可实现（PostgreSQL 16.4 未安装
  pgvector 且不可安装），改为 `double precision[]` + 固定 384 维；归属 **T064（#64）
  / T067（#68）**。不执行 `CREATE EXTENSION vector`，不改契约文件。
- 不新增 background kind：`document_ingest` 已在 `0009` 的 CHECK 与 Go 白名单中，
  本票只补 handler/retry 表/完成守卫（与 `0017` 的 guarded drop/recreate 结论一致）。
- `orchara_worker`/PUBLIC 仍对 `platform` 零授权（迁移显式 REVOKE）。

### 1.2 解析器 / 分块器 / embedding 缝

- `internal/knowledge/parse.go`：闭合 `Parser` 注册表，仅
  `text/plain`（`orchara-text-parser/1`）与 `text/markdown`
  （`orchara-markdown-parser/1`）；UTF-8 校验、单个 BOM 剥离（记录
  `text_normalization`）、空/非法编码返回固定错误。CSV/XLSX/PDF 未注册 → 422。
- `internal/knowledge/chunk.go`：`orchara-paragraph-overlap-chunker/1`；段落打包到
  1200 rune，超长段落 1200/200 重叠硬切并在词边界对齐；裁剪空白后重算 span；
  `chunk.Text == []rune(parsed.Text)[Start:End]` 不变量由 handler 与测试双重核验。
- `internal/embedding`：`local-hash-v1`（384 维、SHA-256 分桶、L2 归一化、跨进程稳定）；
  任何其它 provider 走真实路径并返回 `ErrNotConfigured`，handler → 文档失败
  `embedding_provider_not_configured`，HTTP 读 → 503 `provider_not_configured`。
  每个 chunk 与 citation 记录 `embedding_model_revision`，检索只匹配当前 revision。

### 1.3 受控摄取与完成守卫

- `POST /api/v1/knowledge-bases/{id}/documents`：owner-only + 源 artifact 必须
  owner、ready、未删除/未隔离且有注册解析器；同一事务写
  `knowledge_documents`（`scanning`）并入队一个 `document_ingest`
  job（`expected_revision`、`input_artifact_id`、dedup key
  `knowledge-doc:<artifact>:<artifact_revision>:<document>:<revision>`）。
- `internal/background/handlers/document_ingest.go`：经 job-scoped binding 获取
  document/revision/源 artifact sha256/media type，`Download`+`FetchURL` 取真实字节并
  复核 sha256；解析→分块→embedding→每 chunk 一次 `Upload`/`PutURL`/`Complete`
  （purpose `knowledge_chunk`，复用既有封存流水线）→ 等全部 body artifact ready
  （超时有界，`ingest_chunk_artifact_timeout` 可重试）→ `StageChunks` 单事务写入。
  handler **从不**写 `state`/`active_revision`/`error_code`。
- `internal/background/document_ingest.go` 完成守卫（唯一激活者）：锁文档行，核对
  `expected_revision` 与状态，重读暂存集合（数量>0、chunk_no 0..n-1、citation 的
  document/revision/source 与 job 一致、embedding 全部 384 维且单一模型 revision、
  body artifact 全部 ready 且属 job owner），满足才
  `state='ready', active_revision=revision`；任何不满足 → `failed` + 具体码。
- 失败码：`ingest_empty_content`、`ingest_invalid_encoding`、
  `ingest_unsupported_media_type`、`ingest_source_unavailable`、
  `ingest_source_changed`、`ingest_too_many_chunks`、`ingest_chunk_artifact_timeout`、
  `ingest_incomplete`、`ingest_citation_invalid`、`ingest_cancelled`、
  `embedding_provider_not_configured`。retry 表仅 4 个 infrastructure 码（≤3 次），
  未知码 business（fail safe）。
- failed 文档再次 `ingestDocument` 同一 artifact = `revision+1` + 新 job（可重试）；
  ready/indexing 的重复请求幂等返回原文档，不产生第二个 job。

### 1.4 非契约检索/引用/诊断面（ADR 0010）

- `POST /api/v1/knowledge-bases/{id}/retrieval-queries`
  （body `{query 1..2000, top_k 1..50 默认 8}`）→ `{items, next_cursor, retrieval}`；
  SQL **先**过滤 owner ACL、KB/文档 tombstone、`state='ready'`、
  `active_revision=revision`、`chunk.document_revision=revision`、body artifact ready、
  模型 revision，再对有界候选集（≤2000）在 Go 内精确余弦排序取 top_k；
  空结果 200 + `items:[]`。`retrieval.acl_epoch` = KB revision（T067 缓存键用）。
- `GET /api/v1/knowledge-citations/{chunk_id}`：每次重新校验 ACL/当前版本/body artifact，
  撤销/删除/旧版本/他人 → 同一 404 `resource_not_found`，无正文。
- `GET /api/v1/knowledge-documents/{id}/diagnostics`：owner-only 的非契约诊断读，
  返回失败码/解析器/分块器/embedding revision/当前 revision 片段数/最近 job 状态，
  页面据此展示失败原因。

### 1.5 `/knowledge` 页面

`apps/web/src/knowledge.html`（内联页面脚本）+ `assets/knowledge.js`（纯 helper，
Node 可执行）：创建 KB、选择 KB、用既有 artifact 上传流上传 txt/md
（浏览器算 SHA-256→签名 PUT→complete→轮询封存）、开始摄取、轮询
document+diagnostics 展示状态/失败码/片段数、检索并展示带
文档/版本/行号/score 的片段、点击引用重新打开并校验 citation 一致性；
所有服务端文本用 `textContent` 渲染，无浏览器存储、无 object key/凭据。

## 2. AC 逐条

| # | 验收标准 | 结果 | 证据 |
|---|---|---|---|
| 1 | txt/md 从上传到 ready 有真实后台处理 | **proven** | `tests/f10/test_t055_ingestion.py::test_txt_upload_becomes_ready_with_traceable_chunks`、`::test_markdown_uses_its_own_parser_revision_and_queries_within_the_same_kb`：真实 upload→finalize→`ingestDocument`→真实 maintenance 双池→document `ready`（DB 行 `state/active_revision/parser_version/chunker_version/embedding_model_revision` 逐项断言） |
| 2 | 能检索到带 doc/version/span 的片段 | **proven** | 同一用例：`retrieval-queries` 返回 `document_id/document_revision/chunk_no/span/score/text/citation`；并用独立真值核验（源封存字节 sha256 == citation.source_sha256；`[]rune(源文本)[start:end] == chunk.text`；行号 == 文本换行计数；chunk body 封存字节 == chunk.text；`GET knowledge-citations` 重开一致） |
| 3 | 无权上传/读取拒绝 | **proven** | `test_t055_knowledge_api.py::test_a_foreign_principal_and_an_administrator_both_get_404`、`::test_ingest_requires_a_ready_owner_artifact_and_a_registered_parser`（他人 artifact → 404、未 ready → 409）、`test_t055_ingestion.py::test_retrieval_filters_acl_and_current_revision_before_ranking`（B/admin 检索与 citation → 404）；CSRF/API key scope 见 `test_cookie_sessions_need_csrf_and_api_keys_need_the_existing_scopes` |
| 4 | 空解析或失败不能标 ready | **proven** | `test_t055_failures.py::test_an_empty_parse_is_failed_and_never_ready`（failed `ingest_empty_content`、active_revision NULL、0 chunk、检索空、重试仍 failed）、`::test_invalid_utf8_input_is_failed_with_its_own_code`；守卫 SQL 层拒绝空/不完整集合（`internal/background/document_ingest.go`） |
| 5 | 通过端到端入口实际演示；失败、当前授权与重试/恢复边界有断言 | **proven** | 上述真实 HTTP/maintenance 用例；`test_a_retryable_job_failure_returns_to_ready_and_then_succeeds`（infrastructure 失败→job ready→attempt 2→ready）、`test_a_lost_lease_is_recovered_by_the_next_claim`（租约丢失→下一 claim 恢复→ready）；页面结构 + Node 执行 |
| 6 | 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件 blocked、未运行 not_run | **proven（含显式 blocked/not_run）** | 本报告 §3/§4/§5；local embedder 为 deterministic local（非真实语义模型）；真实 provider blocked；精确扫描性能 not_run |

## 3. 门禁命令与原始输出

环境：Go 1.27.3（`E:\orchara-toolchains\go\bin`）、Python 3.12.2、Node v24.13.0、
PostgreSQL 16.4（`127.0.0.1:55432`，`tools/pg_dev.py status`）；Windows PowerShell；
`product/.env.local` 提供本地口令，报告不含任何凭据。

```text
$ python -m pytest tests/f10 -q
....................                                                     [100%]
20 passed in 149.34s (0:02:29)

$ go build ./...
(exit 0, no output)

$ go vet ./...
(exit 0, no output)

$ go test ./...
... all packages ok ... (exit 0; 无 FAIL)

$ gofmt -l .
(empty)

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

相邻套回归（本票改动触及 artifact 准入、background 完成守卫、API 路由）：

```text
$ python -m pytest tests/f05 tests/tooling -q
1 failed, 164 passed, 5 errors in 1424.00s (0:23:43)
# 失败集中在 tests/f05/test_t028_quarantine.py（T028 上传隔离，非本票新增路径）：
# 1 failed + 5 errors，报错形态是 finalize/等待超时类环境抖动；单独复跑：
$ python -m pytest tests/f05/test_t028_quarantine.py -q
..........                                                               [100%]
10 passed in 78.22s (0:01:18)

$ python -m pytest tests/f00b -q
135 passed in 945.32s (0:15:45)
```

> 说明：全量 `pytest tests` 按 BRIEF §3 不在本票运行（≈67 分钟），由 orchestrator
> 在合并后执行；本票运行 f10 + f05 + f00b + tooling。f05 那一次合并运行出现的
> T028 隔离用例失败单独复跑即绿，未被本票改动触及（本票对 artifacts 的改动只有
> `text/markdown` 在 `purpose=knowledge` 下的准入和一条错误文案），按环境/时序抖动
> 记录，不冒充本票回归失败或通过。

合并 `integration/f13a-f15a-f09-f10`（merge commit `231a3b8`）后复跑目标门禁：
integration 已先占 `0033_memory_candidates.sql`，故本票迁移让号为
`0034_knowledge.sql`、ADR 让号为 0010（F15A=0008、F09=0009）；`PROGRESS.md` 与
`internal/api/server.go` 冲突按双方保留解决（F09 memory + F10 knowledge 路由/服务并存）。

```text
$ python -m pytest tests/f10 -q          # merge 231a3b8 之后
....................                                                     [100%]
20 passed in 127.49s (0:02:07)

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 5.28s

$ go build ./... ; go vet ./... ; go test ./... ; gofmt -l .
(exit 0；gofmt clean)

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

> f00b 135 passed 是合并前在 `t/t055-knowledge-ingest` 上的结果；合并后 Go 单测覆盖
> background 包，但未再次整跑 `tests/f00b`（整跑 ≈16 分钟），由 orchestrator 的
> 集成门禁负责。

## 4. 真实栈、输入与版本

- 真实 PG 16.4（每次用例新建临时库，跑真实 `cmd/admin migrate` 0001→0034；
  迁移链连续，重跑 migrate no-op）；真实 `cmd/api` 二进制；
  真实 HMAC 签名对象链接与文件系统 `staged/`、`sealed/` 字节；
  真实 internal mTLS（maintenance 客户端证书）；真实 `cmd/maintenance` 进程
  （work 池 = `document_ingest`，system_io 池 = `artifact_finalize`，2+2 slot）。
- 输入/哈希：样例 txt/md 内容由 `tests/support/t055.py::SAMPLE_DOCUMENT`
  确定生成；上传时测试端计算 SHA-256 并写 real staging 字节；assert 里对源封存字节
  重新计算 SHA-256 与 citation 对比。每个 chunk body 的 SHA-256 由 handler 计算并
  经 finalize 守卫复核。
- 版本：`local-hash-v1`（384 维）、`orchara-text-parser/1`、
  `orchara-markdown-parser/1`、`orchara-paragraph-overlap-chunker/1`；
  `document_ingest` handler/守卫、迁移 0034。
- 原始结果：pytest 输出见 §3；maintenance 真实进程日志由 `tests/support/t055.py`
  写入 pytest 临时目录（`maintenance-t055-*.log`），本报告不复述日志正文。

## 5. stub / real、blocked、not_run（分开记录）

| 项 | 结论 | 具体条件 |
|---|---|---|
| 确定性本地 embedding（`local-hash-v1`） | **real executed**（本地计算真实发生） | 384 维、跨进程稳定；但不是语义模型，不承诺检索质量 |
| 真实 embedding provider | **blocked** | 本环境无 provider 凭据，构建无 adapter；`Embed` 返回 `ErrNotConfigured`，handler 失败为 `embedding_provider_not_configured`、HTTP 503 `provider_not_configured`；从未回退到本地向量。归属：后续真实部署/模型票 |
| 精确扫描性能/规模基准 | **not_run** | 未做延迟/吞吐/大规模数据集实验；T064/T067 拥有 ANN/规模/缓存决定 |
| 浏览器 DOM 驱动页面 | **not_run** | 只做 `GET /knowledge` 静态结构与 Node 执行纯 helper；与 T023/T027/T029/T031/T032/T033/T037/T039 同一边界 |
| CSV/XLSX 解析 | **not_run / 后续** | T059（#60） |
| PDF 解析 | **not_run / 后续** | T060（#61） |
| 版本重建与激活切换 | **not_run / 后续** | T061（#62）；本票已留 `revision`/`active_revision`/`document_revision` seam |
| 删除治理（文档删除 job） | **not_run / 后续** | F07/T058；本票不实现 `deleteDocument`，只在测试里写 F07 形状的 tombstone 证明检索/citation 立即不可读 |
| 检索缓存 | **not_run / 后续** | T067（#68）；本票不加缓存表，只给 `acl_epoch`/embedding revision |

## 6. 下一票接口（复用点）

- 解析器：实现 `knowledge.Parser` 并在 `parserRegistry` 注册（CSV/XLSX → T059，
  PDF → T060）；`SourceSpan` 的 rune 偏移/行号语义与 citation 字段已固定。
- 激活/版本：`knowledge_documents.revision` 为当前版本、`active_revision` 为可读版本；
  检索/引用都要求 `state='ready' AND active_revision=revision AND
  chunk.document_revision=revision`；T061 只应在权威事务里切换该指针。
- chunk 行：`citation` jsonb 是唯一溯源文档；`embedding_model_revision` +
  `embedding_dimension` 已固定 384；T061/T064 换模型 = 新 revision + 新迁移。
- 后台：`document_ingest` 已在 `AllKinds`/`KnownKind`/CHECK；handler 注册在
  `internal/background/handlers.Registry`；完成守卫在
  `internal/background.completionGuards`；retry 表在 `job.go`；job-scoped binding
  为 `POST /internal/v1/background/jobs/{job_id}/document-ingest`（ADR 0010）。
- 检索：`knowledge.Service.Query` 的 SQL 谓词是 T064 的起点；`acl_epoch` 当前为
  KB revision；T067 缓存键至少含 principal/acl_epoch/embedding revision/query/top_k。

## 7. 与契约的偏差（均已记录，未改契约）

1. `embedding double precision[]` + 384 维 CHECK（pgvector 不可安装；T064/T067 拥有
   ANN/规模）——`0034` 头注释 + ADR 0010 §1。
2. `knowledge_documents.active_revision/chunker_version/embedding_model_revision/
   error_code/updated_at`、`knowledge_chunks.embedding_dimension/created_at` 为
   additive 列（T061/守卫需要）——`0034` 头注释。
3. `text/markdown` 仅对 `purpose=knowledge` 准入（T028 的准入表 purpose-scoped 扩展）
   ——`internal/artifacts/admission.go` 注释 + ADR 0010 §6。
4. 非契约路径 `retrieval-queries`/`knowledge-citations`/`diagnostics`
   （契约无检索操作）——ADR 0010 §3，沿用 ADR 0005 先例；`sync_spec` no drift。
5. 契约 `ingestDocument` 无 `Idempotency-Key`：用资源状态 + `background_jobs`
   `UNIQUE(kind,dedup_key)` 去重——ADR 0010 §5。
