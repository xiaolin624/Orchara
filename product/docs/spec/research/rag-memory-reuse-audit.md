# RAG 与个人记忆：开源组件复用核查

查询日期：2026-10-31。以下为 GitHub MCP 获取的官方仓库元数据、固定源码、依赖和测试的静态调查。未安装这些新组件、未执行其测试、未调用模型、未运行数据库或文档解析；源码版本不等于已经验证的安装锁版。本文只提出选型建议，不修改已确认产品范围。

## 1. 建议结论

| 能力 | 优先复用 | 平台必须承担 | 本轮建议 |
|---|---|---|---|
| 普通 PDF 解析和出处 | Docling 的 NativePdfFormatOption / NativePdfPipeline | 文件准入、资源隔离、状态、来源版本与删除 | 做普通文本 PDF 的候选解析器，P0 对照现有轻量解析方案；不默认开启全部 OCR/VLM |
| 文本分块 | LlamaIndex SentenceSplitter / NodeParser | 段落到页/行范围映射、chunk ID、版本和可读权限 | 复用纯转换组件，不另建一套应用编排 |
| 向量存储和授权检索 | 现有 PostgreSQL/pgvector；LlamaIndex PGVectorStore 可比较 | 当前 ACL、tombstone、参数化 SQL、索引版本 | 优先保留平台现有表与查询权威，不为接库复制一份失去同步的正文/权限表 |
| 用户长期记忆 | Mem0 OSS 候选适配器 | 确认、冲突、来源、scope、可见性、撤销与完整删除 | 纳入小实验；未证明收益与删除契约前不升级为必需依赖 |
| 工作流恢复 | 既定 LangGraph checkpointer | Run/版本/租约/审批 | 不交给 Mem0 或 RAG 文档存储 |

“使用 RAG”不要求引入一套完整 RAG 平台；“采用 Mem0”也不表示将用户确认和数据治理外包给其 SDK。可以复用解析、分块、召回、评测算法，同时让本平台保持单一业务状态权威。

## 2. 固定调查版本与许可证

| 官方仓库 | 本轮 HEAD commit / 提交时间 UTC | 已读包版本 | 许可证来源 |
|---|---|---|---|
| [mem0ai/mem0](https://github.com/mem0ai/mem0) | `753496cf1435d76c3a711f3af51303f7fdcb6420` / 2026-10-30 21:35:34 | mem0ai 2.2.1 | [Apache-2.0 原文](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/LICENSE) |
| [run-llama/llama_index](https://github.com/run-llama/llama_index) | `619d74664e8404612038bc15a1a40c686037277c` / 2026-10-14 15:29:28 | core 0.14.25；postgres integration 0.9.0 | [MIT 原文](https://github.com/run-llama/llama_index/blob/619d74664e8404612038bc15a1a40c686037277c/LICENSE) |
| [docling-project/docling](https://github.com/docling-project/docling) | `764b5d4dc22b03ea87afd4c2207f510300b27a5b` / 2026-10-31 05:14:48 | docling / docling-slim 2.145.0 | [MIT 原文](https://github.com/docling-project/docling/blob/764b5d4dc22b03ea87afd4c2207f510300b27a5b/LICENSE) |

软件许可证不自动覆盖嵌入模型、OCR/布局模型权重、第三方数据或额外服务。这里不提供未经核查的整体商业许可结论。

## 3. Mem0：实际调用链与适配条件

### 3.1 需要修正一个容易沿用的描述

当前 `Memory.add` 的 docstring 仍描述模型可以决定 add/update/delete；但是本轮沿 `Memory._add_to_vector_store(infer=True)` 阅读到的实际主路径是 V3 additive pipeline：

```text
读取同 scope 最近消息和现有记忆
→ 查询 embedding 和向量召回
→ LLM 提取 memory 文本
→ 批量 embedding、hash 去重
→ 为新事实分配 UUID、批量 insert
→ ADD 历史、实体关联、保存消息
```

**不能据这个 docstring 宣称当前默认 add 主路径会自动删除/覆盖旧事实。** 显式 `update()` 和 `delete()` API 仍存在；关键事实是默认 add 会自行提取并持久化新记忆，不能直接作为本平台“用户确认前只产生候选”的无副作用调用。源码：[Memory.add / _add_to_vector_store](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/mem0/memory/main.py#L760)。

交叉核对：`test_update_infer_true_caches_embedding_on_llm_rewrite` 名称保留了旧语义，但其注释明确当前 V3 为 ADD-only，断言 vector_store.insert；`test_add_infer_true_caches_embedding_on_llm_rewrite` 也验证 query embed + batch embed + insert。它们是 mock 测试，不证明真实模型的事实一致性。[固定测试](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/tests/test_memory.py#L717)。

### 3.2 确认后保存可以怎样接

`infer=False` 分支逐条取得消息 content，做 embedding，调用 `_create_memory`，返回 memory ID；没有进入上述 LLM 提取路径。`test_add_infer_false_embeds_once` 验证该分支不重复计算 embedding。[分支源码](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/mem0/memory/main.py#L881)、[对应测试](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/tests/test_memory.py#L682)。

推荐先验证两种实现，而非直接把默认 add 接到每轮对话：

- 基线：平台提出候选→用户确认→平台 PostgreSQL 保存来源、正文引用、revision/确认记录，pgvector 检索。
- Mem0 对照：同样由平台确认；适配器以 `infer=False` 保存已确认内容，记录平台 memory ID 与 vendor memory ID 的映射；检索结果还须经过平台 active/revision/tombstone 检查。

此对照复用了 Mem0 的存储、检索和可选重排，却没有自动获得其提取整理的全部收益。如果想复用默认提取路径，应先设计隔离的“候选区”，保证生成结果只进入 pending、不会通过搜索进入生产上下文，且消息副本可清理。没有读取到一个可直接替代完整候选确认事务的无副作用公共入口；这不等于项目不存在可扩展方式，意味着不能凭猜测把私有方法拼成稳定 API。

scope 建议由可信后端把 `user_id` 映射用户、`agent_id` 映射应用；不要用每次 run_id 作为长期记忆唯一范围。`_build_filters_and_metadata` 的 ID 校验与过滤是数据范围能力，不等于平台登录鉴权。[范围构造实现](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/mem0/memory/main.py#L329)。

### 3.3 设为 PG 后仍有独立 SQLite 和消息/历史副本

`Memory.__init__` 和 `AsyncMemory.__init__` 都创建 `SQLiteManager(config.history_db_path)`。默认路径为 MEM0_DIR 下的 history.db；因此“向量库使用同一 PostgreSQL”不等于全链路只有一套存储。[构造器](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/mem0/memory/main.py#L487)、[MemoryConfig.history_db_path](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/mem0/configs/base.py#L42)、[SQLiteManager](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/mem0/memory/storage.py#L11)。

`_delete_memory` 删除向量后，把旧正文 `prev_value` 作为 old_memory 写入 DELETE 历史；`history(memory_id)` 可读取历史。这适合解释变更，但本平台不能把一次 `delete()` 当作完整正文清除。默认 infer 路径还会保存会话消息用于后续提取。[删除实现](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/mem0/memory/main.py#L2116)、[SQLite 历史/消息表实现](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/mem0/memory/storage.py#L102)。`test_delete_memory_history_has_timestamps` 确认删除后仍记录历史，不能把它误读为安全擦除测试。[固定测试](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/tests/test_memory.py#L819)。

接入门禁：明确历史/消息/实体/向量/日志/备份的保留与清理；多 worker 的 history 文件归属、并发、备份恢复也需设计。源码 `_create_memory` 还有包含正文的 debug 日志，平台日志策略必须限制/脱敏，不能默认启用整个第三方包 DEBUG。[创建实现](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/mem0/memory/main.py#L1975)。

### 3.4 异步、依赖与数据去向

PGVector adapter 是同步 psycopg pool/contextmanager；SDK 提供 AsyncMemory，并在已读创建/删除路径用 `asyncio.to_thread` 包装同步操作。准确结论是“有 async 接口，所读数据库路径包含线程卸载”，不是“只能同步”，也不能称全链路原生异步。[PGVector._get_cursor](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/mem0/vector_stores/pgvector.py#L225)、[AsyncMemory._add_to_vector_store](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/mem0/memory/main.py#L2558)。连接池、线程池与并行提取的延迟必须实测。

包声明 Python >=3.10，默认依赖含 qdrant-client、OpenAI、PostHog 等；安装依赖不代表必须部署对应服务。选择 pgvector 可以复用现有 PG 实例，但会使用 Mem0 自有 collection/table。模型和 embedding 调用需要明确接入获批端点/能力代理。[pyproject](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/pyproject.toml)、[PGVector 实现](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/mem0/vector_stores/pgvector.py)。

`MEM0_TELEMETRY` 默认开启，存在外部 PostHog 端点；私有部署应显式关闭并检查实际出网，而不是推测关闭了所有外部调用。这里没有证据表明遥测发送完整用户正文，不把外部遥测直接表述为泄密。[telemetry.py](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/mem0/memory/telemetry.py)。OSS 库与托管平台功能/密钥体系必须分开评估。

## 4. LlamaIndex：优先复用算法组件，谨慎采用其存储权威

### 4.1 可直接复用的部分

`SentenceSplitter` 按 token 预算、句子/段落和 overlap 拆分；`split_text_metadata_aware` 会扣除 metadata token 成本，metadata 过长可抛错。`NodeParser.get_nodes_from_documents/_postprocess_parsed_nodes` 建立 source/prev/next 关系，尝试定位 start/end char offset 并合并 metadata。[SentenceSplitter](https://github.com/run-llama/llama_index/blob/619d74664e8404612038bc15a1a40c686037277c/llama-index-core/llama_index/core/node_parser/text/sentence.py#L150)、[NodeParser](https://github.com/run-llama/llama_index/blob/619d74664e8404612038bc15a1a40c686037277c/llama-index-core/llama_index/core/node_parser/interface.py#L84)。

适配建议：将解析器输出转为平台 `ParsedDocument/SourceSpan`，按页/段保留原始映射，再用 SentenceSplitter；得到的 node 转成平台 chunk。不要先扁平化成一个字符串、丢失页/表格映射后再期待库自动恢复正确 citation。可先安装 core 子包，不为分块引入一套新在线 Agent/Workflow 引擎。

### 4.2 IngestionPipeline 能做的事及边界

`IngestionPipeline.run/arun` 串联 transformation、cache、可选 docstore/vector_store；`_handle_upserts` 按 ref_doc_id/hash 删除旧向量并更新，`UPSERTS_AND_DELETE` 还会删除输入集合中消失的资料。persist/load 持久化自身 cache/docstore。[pipeline.py](https://github.com/run-llama/llama_index/blob/619d74664e8404612038bc15a1a40c686037277c/llama-index-core/llama_index/core/ingestion/pipeline.py#L242)。

这可以节省增量摄取工作，但需要把 docstore 的集合范围与平台 KB/文档版本明确对应。每个增量小批次不应未经核验就当“全量集合”交给 UPSERTS_AND_DELETE。平台仍要负责上传者/文档 ACL、revision、tombstone、发布和后台任务。测试已覆盖同步/异步更新、去重、并行 cache 和 transformation 错误，未看到这些测试证明本平台的用户授权与删除传播。[test_pipeline.py](https://github.com/run-llama/llama_index/blob/619d74664e8404612038bc15a1a40c686037277c/llama-index-core/tests/ingestion/test_pipeline.py)。

### 4.3 Postgres adapter 可以复用，但不会自动适配现有业务表

`PGVectorStore.from_params` 支持 schema/table、embed_dim、hybrid_search、同步/异步连接串、perform_setup 与 customize_query_fn；`_connect` 创建 SQLAlchemy 同步/异步 engine。metadata filters 在 `_apply_filters_and_limit` 中作为 SQL WHERE 应用后 limit；支持检索前过滤不意味着自动理解平台 ACL。[PGVectorStore.from_params](https://github.com/run-llama/llama_index/blob/619d74664e8404612038bc15a1a40c686037277c/llama-index-integrations/vector_stores/llama-index-vector-stores-postgres/llama_index/vector_stores/postgres/base.py#L413)、[过滤与查询](https://github.com/run-llama/llama_index/blob/619d74664e8404612038bc15a1a40c686037277c/llama-index-integrations/vector_stores/llama-index-vector-stores-postgres/llama_index/vector_stores/postgres/base.py#L782)。

adapter 自身存储包含 text/metadata/embedding 等列，不是直接映射当前 knowledge_chunks 的 body_artifact 引用。采用它需要明确谁负责迁移、正文副本清理、ACL join/过滤和权限撤销时的失效；不是简单把两边都连接同一个 PG 就完成集成。默认 text_search_config=english 也不能当作中文混合检索质量已验证。

安全相关读取发现：`_build_filter_clause` 的部分 IN/ANY/CONTAINS 分支构造 raw SQL 文本。未构造攻击复现或确认漏洞，不作安全漏洞结论；但绝不能直接接受任意客户端 MetadataFilters。应使用后端生成/白名单化 scope，核查所有插值，并按平台参数化 SQL 或经验证的查询扩展接入。[过滤构造](https://github.com/run-llama/llama_index/blob/619d74664e8404612038bc15a1a40c686037277c/llama-index-integrations/vector_stores/llama-index-vector-stores-postgres/llama_index/vector_stores/postgres/base.py#L690)。

Postgres 集成测试包含真实数据库 add/query、metadata filters、删除、hybrid 与自定义 engine 的用例；本轮只读未运行。依赖为 core >=0.13,<0.15、SQLAlchemy async、asyncpg、psycopg2 等，不强制新向量数据库。[tests/test_postgres.py](https://github.com/run-llama/llama_index/blob/619d74664e8404612038bc15a1a40c686037277c/llama-index-integrations/vector_stores/llama-index-vector-stores-postgres/tests/test_postgres.py)、[集成依赖](https://github.com/run-llama/llama_index/blob/619d74664e8404612038bc15a1a40c686037277c/llama-index-integrations/vector_stores/llama-index-vector-stores-postgres/pyproject.toml)。LlamaCloud 的托管端点不是使用 core 转换函数的前提。

## 5. Docling：区分原生 PDF 与模型解析两种成本

`DocumentConverter.convert/convert_all` 接受 Path/DocumentStream/URL，返回包含 status、errors、pages、timings、confidence、document 的 ConversionResult。输入页数/文件大小默认 sys.maxsize，必须由平台收紧；上游“有这个参数”不等于默认限额符合本产品。[DocumentConverter.convert](https://github.com/docling-project/docling/blob/764b5d4dc22b03ea87afd4c2207f510300b27a5b/docling/document_converter.py#L632)、[ConversionAssets/ConversionResult](https://github.com/docling-project/docling/blob/764b5d4dc22b03ea87afd4c2207f510300b27a5b/docling/datamodel/document.py#L415)。

建议只把已注册 artifact 的本地文件流传入，首版不把用户任意 URL 直接交给解析器获取；资料摄取应在隔离资源环境运行，限制时间/页数/解压量。

### 5.1 与首版普通 PDF 对应的入口

`NativePdfFormatOption` 选择 `NativePdfPipeline`，实际 `_build_document/_assemble_document` 遍历 PDF backend 的原生文本单元，并构造 DoclingDocument。它不运行 layout/OCR/table 模型，保留来源页与文本框信息；不能因此声称正确理解复杂表格、扫描 PDF 或所有阅读顺序。[格式入口](https://github.com/docling-project/docling/blob/764b5d4dc22b03ea87afd4c2207f510300b27a5b/docling/document_converter.py#L331)、[NativePdfPipeline](https://github.com/docling-project/docling/blob/764b5d4dc22b03ea87afd4c2207f510300b27a5b/docling/pipeline/native_pdf_pipeline.py#L80)。

交叉核对：`test_native_pipeline_text_provenance_matches_the_page` 对 page_no、bbox 坐标范围、charspan 作断言；还存在页范围、并发指标隔离及原生文本粒度测试。本轮只读断言，没有运行，也没有将其上游测试数据直接作为本平台发布集。[固定测试](https://github.com/docling-project/docling/blob/764b5d4dc22b03ea87afd4c2207f510300b27a5b/tests/test_native_pdf_pipeline.py#L41)。

### 5.2 默认模型路径不是“纯离线轻依赖”

默认 `PdfFormatOption` 使用 StandardPdfPipeline，`PdfPipelineOptions` 默认 do_ocr=True、do_table_structure=True；`artifacts_path=None` 配置说明首次可能从远端获取模型。enable_remote_services 和 allow_external_plugins 默认 False，限制特定远程服务/插件，不等同于已经封禁所有下载和出网。[默认格式](https://github.com/docling-project/docling/blob/764b5d4dc22b03ea87afd4c2207f510300b27a5b/docling/document_converter.py#L301)、[PipelineOptions](https://github.com/docling-project/docling/blob/764b5d4dc22b03ea87afd4c2207f510300b27a5b/docling/datamodel/pipeline_options.py#L1465)、[PdfPipelineOptions](https://github.com/docling-project/docling/blob/764b5d4dc22b03ea87afd4c2207f510300b27a5b/docling/datamodel/pipeline_options.py#L2135)。

当前仓库根包是 docling-slim，提供分格式/模型 extras；`docling` 元包依赖 slim[standard]。不应在部署文档中一边说只解析普通 PDF，一边未经测量安装全套 OCR/VLM/音视频依赖。ML extras、系统库、权重 revision 和镜像体积在 P0 解析器探针中固定。[根依赖](https://github.com/docling-project/docling/blob/764b5d4dc22b03ea87afd4c2207f510300b27a5b/pyproject.toml)、[docling 元包](https://github.com/docling-project/docling/blob/764b5d4dc22b03ea87afd4c2207f510300b27a5b/packages/docling/pyproject.toml)。

错误处理有可读的测试：文件过大时可返回 FAILURE，raises_on_error=True 则抛 ConversionError；非法文件/后端释放也有覆盖。平台需要把这些错误映射到用户可操作的摄取失败，而不是空索引 ready。[test_invalid_input.py](https://github.com/docling-project/docling/blob/764b5d4dc22b03ea87afd4c2207f510300b27a5b/tests/test_invalid_input.py#L36)。另有模型 revision/trust_remote_code 安全测试，支持预下载固定制品的部署方向，但不等于所有第三方权重均已审查。[test_hf_model_download_security.py](https://github.com/docling-project/docling/blob/764b5d4dc22b03ea87afd4c2207f510300b27a5b/tests/test_hf_model_download_security.py)。

## 6. 推荐的适配契约与一条完整数据链

建议平台只公开自身契约，第三方 node/memory/document 对象留在适配层：

```text
Parse(artifact, parser_version, limits)
  → ParsedDocument{source_id,version,text_blocks,source_spans,warnings}
Chunk(parsed, chunker_version, token_budget)
  → Chunk{platform_id,source_version,text_ref,span_refs,hash}
Embed(chunks, approved_model_revision)
  → Vector{platform_chunk_id,dimension,model_revision}
Retrieve(principal,app,query,scope_revision)
  → Evidence{chunk_id,source_version,span_refs,score}
MemorySearch(principal,application,query)
  → 已确认且未删除的 platform_memory_id/revision，正文受同一授权检查
```

流程：Go 接收并授权 artifact → 隔离解析任务使用 Docling/轻量 reader → Python 复用分块组件 → embedding 经获批端点/能力代理 → Go 事务核对文档未删除及 revision 后提交索引 → 查询时当前 ACL 过滤并检索 → worker 取得有出处的片段 → 模型生成引用答案 → 服务端校验引用确实来自本次检索。

来源/删除属于平台模型。用任一组件时，平台授权主键、模型调用预算、审计、文档状态、引用、缓存、备份和 checkpoint 清理都不能省略。

## 7. 最便宜的验证与淘汰条件

以下全部为待执行计划，不是实验结果：

| 探针 | 对照和输入 | 成功/淘汰条件 |
|---|---|---|
| PDF 来源 | 相同普通文本 PDF、重复页眉/双栏/跨页段落/扫描件，轻量 reader vs Docling Native | 引用页/字符映射正确、失败可解释；若复杂布局错误无法识别，降低格式承诺而不宣称解析成功 |
| RAG 分块 | 固定 embedding/检索查询，简单段落分块 vs SentenceSplitter | 正确来源召回与引用完整性不退化；额外依赖/耗时有可解释收益，否则保持轻量实现 |
| Mem0 确认 | 同一组已确认记忆、矛盾偏好、用户改正、跨用户/应用负例；PG基线 vs infer=False适配器 | 相关召回、延迟/费用有收益且不会引入未确认/过期内容；无收益则不强制引入 |
| Mem0 删除 | 写→改→删→search/get/history/消息文件/日志检查；检查向量/SQLite/实体/缓存副本 | 逻辑删除立即不可用，物理清理满足平台时限；若只能保留可读旧正文且无法安全适配，不作为正式后端 |
| 权限撤销/索引延迟 | 检索前后撤销资料授权、删除过程中晚到摄取提交 | 旧向量/缓存也不能泄漏或恢复 ready；不满足则阻断共享知识库 |
| 依赖/出网 | 锁包、预下载、禁网启动和执行 | 无隐式外发/临时模型下载；无法部署则保留解析/记忆模块可替换边界 |

## 8. 已消除与尚未消除的不确定性

已经确认：Mem0 能作为长期记忆实现候选，但默认提取会持久化、历史删除有正文保留，不能直接满足本平台确认/完整删除；LlamaIndex 有可独立复用的分块与存储接口；Docling 当前有 model-free 普通 PDF 路径，也有更重的默认模型路径。

尚未验证：它们组合后的依赖兼容、性能/召回收益、中文布局处理、source span 贯穿分块的正确性、Mem0 全量清理和多 worker 历史管理。没有运行证明，也没有以未搜索到来推定其他功能不支持。

可能推翻当前建议的证据：在固定数据上的基线已经足够、组件引入第二套正文/权限权威且清理无法闭合、Native PDF 出处错误、或锁包/隔离环境不兼容。后续Q45已确认：Mem0通过收益/治理门禁后才成为正式依赖；既有记忆/RAG需求保持不变，复杂文档解析不自动扩大首版范围。该确认不改变本报告“静态调查、未运行”的证据等级。
