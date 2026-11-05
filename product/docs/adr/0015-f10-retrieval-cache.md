# ADR 0015：F10 检索缓存——PostgreSQL 中的可重建只读结果缓存

- 状态：已接受
- 日期：2026-11-04
- 相关：F10 / T067（GitHub #68，规划 ID F10.05）；分支 `t/t067-retrieval-cache`；
  报告 `product/reports/f10/T067-retrieval-cache.md`；
  迁移 `product/db/migrations/0037_knowledge_retrieval_cache.sql`。
- 前置：T055（`reports/f10/T055-knowledge-ingest.md`、ADR 0010、迁移 `0034_knowledge.sql`、
  `internal/knowledge` 的 SQL 先过滤后排序检索）与 T064（`reports/f10/T064-grounded-citations.md`、
  ADR 0012、`Answer`/`GetChunk` 返回前复检）。
- 先例：ADR 0005（非契约桥）、ADR 0010/0012（F10 非契约检索/回答面）、ADR 0009 §11
  （F09 删除后缓存失效的版本事实）。`contracts/**`/`docs/spec/**` 未改，
  `python tools/sync_spec.py --check` no drift。

## 背景

T055 交付了"SQL 先做 ACL/当前版本过滤，再对有界候选集排序"的检索；T064 交付了
"回答只引用本次检索、且返回前逐引用复检当前可读性"。两者都没有缓存：每一次提问都会
重新做候选 SQL、读出每个候选的封存正文并计算余弦。

本票要回答的是：**重复查询能否复用一次合格的只读检索结果，同时保证删除/撤权后即使
缓存驱逐丢失也绝不命中。** 约束：

1. 本机没有 Redis（与 ADR 0007/0010 的部署事实一致），缓存只能放在 PostgreSQL。
2. 缓存是**派生、可重建状态**：权威事实在 `principals`/`knowledge_bases`/
   `knowledge_documents`/`knowledge_chunks`/`artifacts` 与封存对象字节里。丢缓存只损失性能。
3. 契约没有检索操作，更没有缓存表；检索/引用/回答是 ADR 0010/0012 登记的非契约面。
4. `TESTING-AND-DATASETS.md` §7 要求 RAG 四类失败（缺目标来源、错答案、伪引用、越权片段）
   分别可定位；缓存命中不得把它们糊成一个综合分。

## 决策

### D1 — PostgreSQL 表 + 当前状态派生键；不引入 Redis

- 新增非契约表 `platform.knowledge_retrieval_cache`（迁移 0037）。一行 = 一次合格检索：
  `cache_key`（hex SHA-256，主键）、`principal_id`、`knowledge_base_id`、`acl_epoch`、
  `kb_revision`、`source_revision_digest`、`source_document_count`、
  `embedding_model_revision`、`canonical_query`、`query_sha256`、`top_k`、`item_count`、
  `body_bytes`、`items`（包含正文的 chunk 投影）、`created_at`/`expires_at`/`last_hit_at`/
  `hit_count`。
- 键的组成（长度前缀后 SHA-256，见 `internal/knowledge/cache.go`）：
  - **principal**：身份；
  - **acl_epoch** 与 **kb_revision**：有效授权/资源版本。本切片 owner-only、没有共享操作，
    `acl_epoch` 取 `knowledge_bases.revision`（T055/ADR 0010 的已记录替身）；未来的权限
    变更先在权威行 bump 它，旧键立即不可达。两者分开存列，T061/共享票可让它们分离；
  - **source_revision_digest**：当前 KB 内**未删除文档**的
    `(id, revision, state, active_revision)` 摘要。新增来源、删除来源、重新激活都会改变它；
  - **embedding_model_revision**、**canonical_query**、**top_k**。
  - `canonical_query` 就是 T055 实际嵌入/排序的 `strings.TrimSpace(query)`：不再折叠内部
    空白，否则"命中"与"未命中"会回答不同的字符串。
- 组件值全部存列，命中时与重算键逐项比对：可调试，也能发现被篡改/碰撞的行。
- TTL 默认 15 分钟（`RetrievalCacheDefaultTTL`），单条目正文上限 256 KiB
  （`top_k<=50`、每 chunk 由 chunker 限制在 1200 rune，故有余量）。过期行在 miss 路径按
  principal 懒清理；没有新增后台驱逐 kind。
- **稳定来源规则**：只有 `state='ready'`、`active_revision = revision`、chunk 当前
  revision、body artifact ready 且 `sha256` 非空的 chunk 才可能进入缓存结果；未就绪/失败/
  部分结果根本不缓存，TTL 15 分钟也限制任何条目的最长寿命。因此"没有稳定版本的来源"
  不会按长期缓存处理。
- **为什么不用 Redis**：本机无可安装/可用的 Redis；缓存可重建，PG 既是权威也是存储，
  少一个一致性边界。报告记录该决定。

### D2 — 命中必须重算键 + 逐 chunk 复检；驱逐丢失也绝不命中

每次（缓存开启的）查询：

1. 先按 T055 路径校验参数、owner ACL（`GetBase`）、embedding model 并把 query 嵌入。
   embedding 在缓存查找**之前**执行：缓存不能掩盖 provider 失败/未配置。
2. 从当前权威行重算键（含 `source_revision_digest`）。
3. 按 `(cache_key, principal_id, knowledge_base_id)` 取行。行不存在 → miss。
4. 行的组件与重算键逐项比较；不同 → 丢弃整行 → miss（`components_changed`）。
5. 过期 → 删除 → miss（`expired`）。**TTL 不是权限保证**：未过期也要执行第 6 步。
6. 对 `items` 中**每一个** chunk 执行一条 SQL 复检（与检索/引用谓词一致）：
   owner ACL、KB/文档 tombstone、`state='ready'`、`kd.active_index_id` 指向的索引版本
   `ready` 且 `iv.id = kc.index_version_id`、`kc.document_revision = iv.document_revision`、
   body artifact ready/未删除/与 KB 同 owner；
   并要求 body artifact 的 `sha256` 与 chunk 的 document/revision/chunk_no/artifact id
   ｜全部等于缓存行记录的值。任一不满足（缺行、重复、字段不一致）→ 删除整行 → miss
   （`item_unreadable`/`item_mismatch`）。
   该谓词自 2026-11-05 复核修复起由 `internal/knowledge/readable_chunk.go` 的
   `currentReadableChunkSQL` **唯一定义**，检索（`rankRetrieval`）与引用打开（`GetChunk`）
   复用同一片段——此前三处各抄一份，T061 激活切换时须同时改三份，权限条件有漂移风险。
   缓存这一处只多传 owner 参数位（`$3`）与自己的候选集条件。
7. 只有全部通过才返回缓存正文，并 `hit_count+1`/`last_hit_at=now()`。

因此：**异步驱逐只是优化**。删除/撤权先在权威表生效，旧键不再被查找；即使旧行还在
（eviction lost），键已不同；即使有人把已删除 chunk 塞进**当前键**的行，第 6 步也会
丢弃整行并走 miss。缓存失败（读/写/校验 SQL 出错）按 miss 处理，权威路径照常回答。

### D3 — 正文不跨用户复用；答案本身不缓存

- 键含 principal，行按 `(cache_key, principal_id, knowledge_base_id)` 读取；`items` 里的
  正文只可能返回给存储它的同一 principal + 同一 KB。
- 测试用"活行投毒"证明：把 Alice 条目的 chunk/正文注入 Bob 当前键的行，Bob 的请求在第 6
  步因 owner/KB 谓词失败而丢弃整行，永不返回 Alice 的正文；Bob 连 Alice 的 KB 都 404。
- 缓存只存**检索结果**，不存回答。`Service.Answer` 每次仍然执行生成、引用集合校验与
  逐引用 `GetChunk` 复检（ADR 0012 D3）：个性化最终答案从不进入缓存。

### D4 — RAG 四类失败仍分别定位；空/部分结果不缓存

- **缺目标来源**：空结果或目标文档被删除时，检索阶段返回空 → 回答 `no_results`。
  `shouldStoreRetrievalCache` 明确拒绝缓存空结果，所以空结果不会被 TTL "钉住"。
- **部分结果**：读取封存正文失败而跳过的候选计入 `skipped`，该结果不缓存——暂时的
  body 不可读不会被缓存掩盖。
- **伪引用**：引用集合校验/返回前复检不在缓存里，缓存命中后仍逐次执行；`foreign-chunk`
  dev-stub 故障在缓存命中时仍得到 `citation_not_found`（测试用命中计数证明该请求确实命中
  了缓存）。
- **越权片段**：第 6 步的 body-artifact owner 谓词与 T064 加固的检索谓词一致；活行被注入
  指向他人封存正文的 chunk 时整行作废，任何路径都拿不到该正文。
- **错答案**：T067 在 ADR 0012 D2 的 dev-stub 故障约定下新增 `wrong-answer`：保留真实
  引用、只把答案文本换成检索片段中不存在的固定结论。于是"回答已生成但文本无依据"可被
  归因到生成阶段，与"引用集合失败"（`citation_not_found`）、"检索为空"（`no_results`）
  区分开。该故障只在 stub profile 生效、只能误标文本，不能绕过任何校验。
- 缓存命中会在非契约检索/回答响应的 `retrieval.cache` 中如实报告（见 D6）。

### D5 — 部署开关与对照实验

- `ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE`：`on`/`off`（默认 on）。未知值**启动即拒绝**
  （fail closed，`CacheOptionsFromEnv`），不静默退化为某个默认。
- `tests/f10/test_t067_comparison.py` 用两个真实 API 进程（同一 DB/对象目录，一个 off）
  跑同一数据集/查询：8 文档 24 chunk、3 查询 × 8 次测量（每模式 24 次）、
  p50/p95、命中率、正确性逐字段相同、以及 top_k=5 vs top_k=20 的逐项复检成本。
  报告记录实测数字；若没有收益就应删除缓存（本票实测有收益，故保留）。
  2026-11-05 复核把两种模式的取样改成**成对**（每次先 cache-on hit、再同一 query 走
  cache-off 进程，同一测量窗口），并额外记录成对中位比。原因：本机多套件并发（BRIEF §6.1），
  先后两个相位相隔数分钟，宿主级慢窗口会把 p50 比较翻转（实测 hit 14.4/13.9 ms vs miss
  12.9/10.9 ms；同一棵树安静窗口 3.4 vs 9.9 ms，基线 `8ad3bcc` 在同套件通过）。计时断言
  因此只作**粗回归护栏**（hit p50 ≤ miss p50 × 3），keep/drop 的量化结论仍以记录数字为准。

### D6 — 非契约可观察面与文档

- 检索/回答响应的 `retrieval.cache` 是 additive 字段：
  `{enabled, hit, discarded, discard_reason?, key_prefix, validated_chunks, stored, ttl_seconds}`。
  `key_prefix` 是摘要前 16 hex，够运维对行、无法还原查询。它不进入契约
  （`contracts/openapi.json` 本来就没有检索操作），沿用 ADR 0010/0012 的先例。
- 命中/驱逐原因用固定安全词（`expired`/`components_changed`/`item_unreadable`/
  `item_mismatch`/`cache_unavailable`），不暴露 chunk 正文或他人数据。

### D7 — 边界与债务

- 物理清理缓存行不是正确性前提：旧键不可达 + 命中复检 + TTL 懒清理已经足够。F07 删除
  治理将来可以按资源额外清理 `items`/行；那是优化，不是本票的通过条件。
- `source_revision_digest` 取的是**文档行**摘要：T061 的激活切换必须移动
  `revision`/`active_revision`（它本来就是唯一激活者写这两个字段），因此切换会改变键；
  若未来出现"chunk 行在文档 revision 不变的情况下被替换"，必须同时 bump 文档 revision
  或把该事实加入摘要（本票记录的 seam）。
- ANN/pgvector/规模性能仍不是本票：缓存不改变候选集上限（`MaxCandidates=2000`）与精确
  排序；T064/后续规模票拥有 ANN 决定。
- `canonical_query` 以明文存在 owner-scoped 的缓存行里（TTL 到期/被丢弃时删除）。这是可
  调试性的取舍；它不经任何响应返回（除 `key_prefix`）。

## 后果

- 正面：重复只读查询在键与内容都仍然当前时可复用，且删除/撤权/改版/换 embedding/
  改 query/top_k/换 principal 都会立即失效；即使缓存驱逐丢失，旧授权键也不会命中。
  四类 RAG 失败仍分别可观察；回答本身从不缓存。对照实验实测有收益（见报告 §3/§4）。
- 代价：多一张非契约表与一次 TTL 懒清理；命中要付一次源版本摘要查询 + 一次组件读取 +
  一次逐 chunk 复检 SQL；缓存写失败按 miss 处理（不会破坏正确性，但没有收益）。
- 未改 `contracts/**`/`docs/spec/**`，`sync_spec --check` no drift；迁移只新增 0037，
  0001–0036 未改；`orchara_worker`/PUBLIC 对 `platform` 仍零权限。
