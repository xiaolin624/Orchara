# ADR 0012：F10 回答只引用本次检索且当前可读的资料（非契约回答面）

- 状态：已接受
- 日期：2026-11-04
- 相关：F10 / T064（GitHub #65，规划 ID F10.04）；分支
  `t/t064-grounded-citations`；报告 `product/reports/f10/T064-grounded-citations.md`；
  前置 T055（`product/reports/f10/T055-knowledge-ingest.md`、ADR 0010、迁移
  `0034_knowledge.sql`、`internal/knowledge`）。
- 先例：ADR 0005（`tool_run` 非契约桥）、ADR 0007（F06 非契约内部端点与 stub 故障约定）、
  ADR 0010（F10 非契约检索/引用/诊断面）。
- 无迁移；`contracts/**` 与 `docs/spec/**` 未改，`python tools/sync_spec.py --check` no drift。

## 背景

T055 已交付“上传 txt/md → ready → 检索到可追溯片段 → 打开 citation 时重新校验当前可读性”。
契约（`contracts/openapi.json`）只有知识库/文档的六个操作，**没有**“就知识库提问并返回带引用
答案”的操作；`docs/spec` 的 ROUTING-MEMORY-KNOWLEDGE §3 要求答案引用必须来自本次检索，
并且文档撤销后打开引用不能继续提供旧正文。

T064 要回答的问题是：**一个回答只能引用本次检索真正返回过的片段，而且这些片段在回答返回的
那一刻仍然可读**。同时本环境：

1. 没有任何模型 provider 凭据，也没有 provider adapter（ADR 0010 §2、ADR 0007 D3 的同一
   部署事实）。因此“生成”不能伪装成模型输出。
2. 当前的检索在 SQL 中先做 ACL/当前版本过滤再排序（T055），但 chunk body artifact 的
   join 没有核对 artifact 的 owner；一条被伪造的 chunk 行（指向他人的封存正文）在理论上
   可以进入候选集。
3. 页面上还没有提问入口，也没有“撤销后引用不可用”的问答展示。

## 决策

### D1 — 回答面是非契约路径，形状固定

```
POST /api/v1/knowledge-bases/{knowledge_base_id}/answer
body: {"query": "1..2000 chars", "top_k": 1..50 (default 8, 0 = default)}
200 answered: {
  "outcome": "answered",
  "answer": {
    "text": "...",
    "generation": {"kind": "extractive_stub", "version": "extractive-stub/1",
                   "stub": true, "model_provider": "not_configured"},
    "citations": [{
      "chunk_id": uuid, "document_id": uuid, "document_revision": int, "chunk_no": int,
      "span": {"start_char": int, "end_char": int, "start_line": int, "end_line": int},
      "score": number, "text": "chunk body text",
      "citation": {...存储的 citation 文档，含 source_artifact_id/source_sha256/
                     parser_version/chunker_version/embedding_model_revision...}
    }]
  },
  "retrieval": {"embedding_model_revision": "local-hash-v1", "top_k": int,
                "acl_epoch": int, "chunk_ids": [uuid, ...]}
}
200 no_results: 同形状，answer.text="" 且 citations=[]、retrieval.chunk_ids=[]
422 answer_without_citation | 422 citation_not_found | 409 citation_unreadable:
  既有错误信封 {"error":{code,message,request_id,details}}，
  details.outcome = 同一 code；citation_* 另带 details.chunk_id。
```

- `retrieval.chunk_ids` 是**本次检索**的有序 chunk-id 集合。答案的每个 citation 都必须是它的
  成员；`citations` 是服务端从检索行投影出来的，生成器只能给 chunk id，不能给 provenance。
- 这是非契约路径（契约没有检索/回答操作），沿用 ADR 0005/0007/0010 的先例：只在本 ADR 与
  报告登记，不新造第二套 HTTP 错误词汇，契约未来若增加回答操作则以契约替换。
- owner-only（管理员不例外）；cookie 会话要 CSRF，API key 用既有
  `application:read` scope；缺失/他人/删除/畸形 id 一律 404 `resource_not_found`。

### D2 — 生成是带标签的抽取式 stub；真实 provider 仍 blocked

- 本构建 `knowledge.Answerer` 只有一个实现 `ExtractiveStub`：从本次检索的前
  `AnswerCiteLimit=3` 个片段逐字摘录（每个 ≤600 rune）拼成答案，并只引用这些片段。
  它是确定性的：同样的检索结果产生同样的答案文本与引用。
- 每个回答（含 `no_results`）都带
  `generation.kind=extractive_stub`、`version=extractive-stub/1`、`stub=true`、
  `model_provider=not_configured`。页面把生成方式显示出来，绝不把 stub 呈现为模型答案。
- 真实模型 provider：**blocked**（无凭据、构建无 adapter，与 ADR 0007/0010 同一事实）。
  后续接入真实 provider 时替换 `AnswerGenerator` 实现即可；D3 的校验不依赖生成器是谁。
- 为使拒绝路径可端到端观察，`ORCHARA_KNOWLEDGE_ANSWER_FAULTS` 允许 dev-stub 部署让 stub
  **按文档错一次**：`uncited`（有答案无引用）、`foreign-chunk`（在真实引用之外加一个
  不存在的 chunk id）、`slow=<duration>`（检索完成后延迟返回草稿，用于撤销竞态）。
  它是部署配置、不是请求参数；只在 `dev-stub`/`capacity-stub` profile 生效，其它 profile
  启动即拒绝（fail closed）；它只能让服务端多拒绝，**不能**绕过 D3 的任何校验。
  这与 ADR 0007 D3/T045 用注册 model id 制造确定性 stub 故障是同一约定。

### D3 — 引用契约：集合成员 + 返回前可读性复检 + 服务端 provenance

生成器的草稿是不可信输入。`Service.Answer` 在返回前依次执行：

1. **引用必需**：草稿没有引用 → `answer_without_citation`，整条回答拒绝。
2. **集合成员**：每个 cited chunk id 必须属于 `Service.Query` 本次返回的 chunk-id 集合；
   否则 `citation_not_found`（伪造/越权/旧检索的引用绝不随答案返回）。重复引用去重。
3. **当前可读**：对每个引用重新执行 `GetChunk`（与 `GET /knowledge-citations/{id}` 同一 SQL
   谓词）：owner ACL、KB/document tombstone、`state='ready'`、
   `active_revision = revision`、`chunk.document_revision = revision`、body artifact
   ready/未删除/同 owner。任一不满足 → `citation_unreadable`（409）。重新读到的
   document/revision/chunk_no/正文必须与检索时一致，否则同样拒绝。
4. **provenance**：citation 的 document id、revision、chunk id、rune span/行号与存储
   citation 文档全部来自服务端检索行，不采信生成器提供的任何元数据。

因此一个回答要么完整可溯源且当前可读，要么被明确拒绝；不存在“部分引用有效就凑合返回”。
`no_results` 是空检索的正常空答案（200），不是错误。

### D4 — 检索 ACL 硬化：chunk body artifact 必须与知识库同 owner

T055 的检索/引用 SQL 只要求 body artifact ready 且未删除。T064 在两条 SQL 上都加
`a.owner_id = kb.owner_id`：一个 chunk 行即使真实存在并指向真实封存字节，只要 body
artifact 属于其它 principal，就既不进入检索候选，也不能通过 citation 端点打开。
这不是新契约，是对“SQL 先限制当前可读文档再排序”的补强；测试用真实伪造行 + 真实他人
封存字节验证。

### D5 — 不持久化回答，不加迁移

回答是请求期产物：检索、生成、校验、复检都在一个请求内完成，没有答案表、没有缓存。
T067 若做检索缓存，必须在缓存命中时仍执行 D3 的返回前可读性复检（`acl_epoch` 已由 T055
提供；T064 的结论是缓存不得缓存“可读性结论”）。删除治理仍归 F07/T058。

### D6 — 页面反馈：四种结果各自可见

`/knowledge` 增加 Q&A 区块：提问、显示生成方式（stub 标注）、显示答案与引用列表，点击引用
重新 `GET /knowledge-citations/{id}`；404 显示“引用不可用”，**不**回放答案里的旧引用正文。
四种结果分别显示：已回答、空结果（不是错误）、回答被拒绝（无引用/引用不属于本次检索/
引用已不可读），并显示服务端错误码。页面只用 `textContent` 渲染，不使用 localStorage/
sessionStorage，不显示 object key。

## 后果

- 正面：回答的每个引用都可解析到本次检索、当前 ACL、当前文档版本与真实封存字节；失败面
  按 `no_results`/`answer_without_citation`/`citation_not_found`/`citation_unreadable`
  区分并可端到端观察；撤销后既不能通过 citation 端点也不能通过回答拿到旧正文。
- 代价：生成质量只是抽取式拼接（真实模型 blocked）；回答不持久化，页面刷新后不保留历史；
  `ORCHARA_KNOWLEDGE_ANSWER_FAULTS` 是 dev-stub 的显式故障开关（非 stub profile 拒绝）。
- 未改 `contracts/**`/`docs/spec/**`，`sync_spec --check` no drift；无迁移，0001–0035 未改。

## 后续更新

- 2026-11-04：T067（#68）在 D2 的 dev-stub 故障约定中新增 `wrong-answer`：保留真实引用、
  只把答案文本替换成任何检索片段中都不存在的固定结论，用于把 RAG 的"错答案"失败类归因到
  生成阶段（与 `citation_not_found`/`no_results` 区分）。它仍只在 stub profile 生效、只能
  误标文本，不能绕过 D3 的任何校验。检索缓存设计见 ADR 0015，D3 校验在缓存命中时仍然逐次
  执行。
