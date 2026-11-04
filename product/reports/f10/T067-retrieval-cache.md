# F10 T067 验收：命中检索缓存也不能读到删除或撤权内容

- 任务：F10 / GitHub Issue #68（票 `T067`，规划 ID F10.05），分支
  `t/t067-retrieval-cache`（worktree `E:\orchara-worktrees\t067`，从集成 tip
  `ddaeb46` 切出）；父门禁 #53–#68 集成批次。
- 状态：**`passed`**（本票范围 = PostgreSQL 检索缓存表 `platform.knowledge_retrieval_cache`、
  当前状态派生键、命中时逐 chunk 复检与丢弃、正文不跨用户复用、四类 RAG 失败仍分别定位、
  `retrieval.cache` 非契约可观察元数据、`ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE` 部署开关、
  缓存关/开对照实验；真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP + 真实封存对象字节 +
  真实 maintenance 摄取 + 第二个真实 API 进程；无 mock、无跳过）。
- 边界（如实）：本机**没有 Redis**，缓存放在 PostgreSQL（可重建派生状态，PG 即权威与存储；
  ADR 0015 D1）；缓存是**非契约**面（契约本来就没有检索操作，ADR 0010/0012 先例），
  `contracts/**`/`docs/spec/**` 未改，`sync_spec --check` no drift；物理驱逐/清理缓存行
  不是正确性前提（旧键不可达 + 命中复检 + TTL 懒清理），F07 删除治理未来可按资源清理；
  ANN/pgvector/规模性能仍 not_run（T064 边界延续）；浏览器 DOM 驱动 not_run（与既有页面票同
  边界）；真实 embedding provider 继承 T055 **blocked**（无凭据/adapter）；全量
  `pytest tests` 由 orchestrator 合并后运行。
- 收益判定：对照实验**有收益，保留缓存**（同一数据集/查询，8 文档 24 chunk，每模式 24 次
  测量：cache on 命中 p50/p95 = 6.954/29.881 ms，cache off = 28.391/55.102 ms；另一轮实测
  5.249/10.672 vs 25.883/43.478，方向一致；正确性逐字段相同；逐 chunk 复检成本
  ≈ 0.09–0.22 ms/项）。原始 JSON 见 §3。

## 0. 工作记录（简短）

1. 读 `BRIEF.md`、`briefs/T067.md`、票面 #68、T055/T064 报告、ADR 0010/0012、迁移
   `0034_knowledge.sql`、`internal/knowledge/service.go`（SQL 先过滤后排序 + `acl_epoch`）、
   `internal/knowledge/answer.go`（`Answer` + 引用复检 + dev-stub 故障约定）、
   `tests/support/{t055,t064}.py`、`tests/f10/`，以及 F09/T058 报告与 ADR 0009 §11 的
   "revision/epoch 是权威失效事实、异步驱逐只是优化"先例；核对 `cmd/api` 装配、
   `internal/api/knowledge.go` 的非契约检索/回答响应、`tests/support/fixtures.py` 的
   `t055_stack`、`t024.start_second_api` 第二真实进程模式。
2. TDD 垂直切片（每片先红后绿）：
   - 迁移 0037 + 纯键/开关/存储策略单测（红：表不存在、`undefined: CacheOptionsFromEnv`
     等；绿：`pytest tests/f10/test_t067_migration.py` 3 passed、Go 单测 6 passed）；
   - 真实栈命中/失效切片：先写 `tests/f10/test_t067_cache.py` 8 用例（红：无缓存表/无
     `retrieval.cache`），再实现 `cache_store.go` + `Query` 的键重算/复检/丢弃/存储 +
     API 元数据 + `cmd/api` 装配；绿：8 passed；
   - 四类 RAG 失败切片：先加 `wrong-answer` dev-stub 故障的 Go 单测（红：字段/哨兵未定义）
     与 `tests/f10/test_t067_rag_classes.py` 4 用例，再实现；绿：Go 单测、4 passed；
   - 对照实验：`tests/f10/test_t067_comparison.py`（真实第二 API 进程 off、真实测量）；
     绿且实测有收益。
3. 跑门禁（§3）：`pytest tests/f10` 两次背靠背、`pytest tests/tooling`、`go build/vet/test`、
   `gofmt -l`、`sync_spec --check`；按 feat/test/docs 提交；合并
   `integration/f13a-f15a-f09-f10` 后复跑；未推送、未关 issue、未改
   `docs/spec/**`/`contracts/**`、只新增迁移 0037（0001–0036 未改）。

## 1. 交付内容

### 1.1 迁移 `0037_knowledge_retrieval_cache.sql`

- `platform.knowledge_retrieval_cache`：`cache_key`（hex SHA-256 主键 + 格式 CHECK）、
  `principal_id→principals`、`knowledge_base_id→knowledge_bases`、`acl_epoch`、
  `kb_revision`、`source_revision_digest`（hex）、`source_document_count`、
  `embedding_model_revision`、`canonical_query`、`query_sha256`、`top_k`（1..50 CHECK）、
  `item_count>=1`、`body_bytes`、`items jsonb`（array CHECK）、`created_at`/`expires_at`
  （`expires_at > created_at` CHECK）、`last_hit_at`、`hit_count`；
  `(principal_id, knowledge_base_id, expires_at)` 与 `(expires_at)` 索引。
- 头注释逐条写明：为什么没有 Redis（本机不可用、缓存可重建、PG 既是权威也是存储）、
  键的组成与"旧授权键不命中"的机制、`canonical_query` 明文与 TTL 懒清理、无后台驱逐
  kind、以及**契约无缓存表**的偏离与归属（ADR 0015；未来契约有检索操作时以契约替换）。
- 重复 `admin migrate` no-op；`0001..0037` 链连续；`orchara_worker`/PUBLIC 对 `platform`
  零权限（迁移显式 REVOKE）。

### 1.2 键、开关与存储策略（`internal/knowledge/cache.go`）

- `CacheOptions`/`CacheOptionsFromEnv`：`ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE` = on/off
  （默认 on），未知值启动即拒绝（fail closed）；TTL 默认 15 分钟。
- `retrievalCacheKey`：principal + `acl_epoch` + knowledge base + KB revision +
  `source_revision_digest` + document count + embedding model revision + canonical query +
  top_k；长度前缀后 SHA-256，组件边界不会碰撞（单测）。
- `canonicalCacheQuery` = `strings.TrimSpace(query)`，与 T055 实际嵌入/排序的字符串完全
  一致；不折叠内部空白。
- `shouldStoreRetrievalCache`：空结果、部分结果（有候选正文读不出）、超过 256 KiB 的条目
  一律不缓存——这正是"缺目标来源"和暂时不可读不被 TTL 掩盖的机制。

### 1.3 命中复检、丢弃与存储（`internal/knowledge/cache_store.go` + `Service.Query`）

- 每次查询先做 T055 的参数/owner ACL/embedding 校验并嵌入 query（缓存不掩盖 provider
  失败），再从当前权威行重算键：`source_revision_digest` 取当前未删除文档的
  `(id, revision, state, active_revision)` 摘要。
- 按 `(cache_key, principal_id, knowledge_base_id)` 取行；组件逐项比对；过期即删；
  `items` 中**每个** chunk 用 T055/T064 同一组 SQL 谓词复检（owner ACL、KB/文档 tombstone、
  ready、`active_revision = revision`、chunk 当前 revision、body artifact ready/未删除/同
  owner），并核对存储的 document/revision/chunk_no/body artifact id/content sha256。
  任一不满足 → 删除整行 → 当作 miss 重新检索；**绝不返回**。
- 全部通过才返回缓存正文；命中计数 `hit_count`/`last_hit_at` 只用于证据。缓存读/写/复检
  SQL 出错按 miss 处理（缓存是派生状态，权威路径照常回答）。
- 缓存只存检索结果，不存回答：`Service.Answer` 每次仍执行生成 + 引用集合校验 + 逐引用
  `GetChunk` 复检（ADR 0012 D3）。

### 1.4 非契约可观察面（ADR 0015 D6）

- `POST /api/v1/knowledge-bases/{id}/retrieval-queries` 与 `/answer` 的 `retrieval` 增加
  additive `cache`：`{enabled, hit, discarded, discard_reason?, key_prefix,
  validated_chunks, stored, ttl_seconds}`；`key_prefix` 是 64-hex 摘要前 16 位。
  `T055` 的既有断言从"精确 retrieval 三字段"更新为"三字段 + additive cache"，语义不变。
- `cmd/api` 用 `knowledge.CacheOptionsFromEnv` 装配 `knowledge.Service.Cache`；
  `api.Deps` 新增可选 `*knowledge.CacheOptions`（nil = `knowledge.New` 的默认）。

### 1.5 RAG 四类失败仍分别定位（ADR 0015 D4）

- 缺目标来源 → 检索阶段空结果 → `no_results`；空/部分结果不缓存。
- 错答案 → 新增 dev-stub 故障 `wrong-answer`（ADR 0012 D2 约定）：保留真实引用、只把答案
  文本替换成任何检索片段中都不存在的固定哨兵结论；回答仍 `answered` 且引用可读，失败可
  归因到生成阶段。
- 伪引用 → 引用集合校验/返回前复检不在缓存里；缓存命中时 `foreign-chunk` 仍
  `citation_not_found`（用命中计数证明该请求确实用了缓存）。
- 越权片段 → 命中复检的 body-artifact owner 谓词与 T064 加固一致；活行被注入指向他人封存
  正文的 chunk 时整行作废，任何路径拿不到正文。
- 对照：wrong-answer / fabricated-citation 两类在真实 cache-off 第二进程上得到同一阶段
  归因。

## 2. AC 逐条

| # | 验收标准 | 结果 | 证据 |
|---|---|---|---|
| 1 | 删除/撤销即使驱逐丢失也不命中 | **proven** | `tests/f10/test_t067_cache.py::test_a_deleted_documents_live_cache_row_is_never_returned`：先真实 warm 缓存，再直接写 F07 形状的文档 tombstone（**不驱逐**缓存行，断言旧行仍 live），请求 → `hit=false`、新键、无被删文档 chunk；随后把被删文档的存储条目注入**当前键**的活行（`replace_entry_items`），请求 → `discarded=true`、`discard_reason=item_unreadable`、正文哨兵不出现、重新存储的条目无被删内容；`::test_an_authorization_revision_change_invalidates_a_live_entry`（KB revision bump，旧行仍 live、未命中）；`::test_a_document_revision_change_does_not_hit_the_old_revision`（真实复制 revision 2 chunk 行并移动 `active_revision`，旧行仍 live、只返回 revision 2） |
| 2 | 正文不跨用户复用 | **proven** | `tests/f10/test_t067_cache.py::test_a_stored_body_is_never_served_to_another_principal`：Alice/Bob 各自真实 KB/文档/缓存行，键与 principal 不同，Bob 的重复查询只命中自己的行、Alice 行 hit_count 不变；Bob 问 Alice 的 KB → 404 无 items；把 Alice 的 chunk/正文注入 Bob 当前键的活行 → `discarded`、响应无 Alice 正文、仍有 Bob 正文、新条目无 Alice 内容 |
| 3 | 四类 RAG 失败分别定位 | **proven** | `tests/f10/test_t067_rag_classes.py` 4 用例：missing target source → `no_results`（含空 KB、撤销后、cache-off 对照）；wrong answer → 缓存命中 `answered` + 哨兵存在且不在任何检索/引用文本里（cache-off 同归因）；fabricated citation → 422 `citation_not_found` + `details.chunk_id`，且命中计数 +1 证明缓存确实被使用（cache-off 同归因）；unauthorized chunk → 真实伪造 chunk 行指向 Bob 真实封存 artifact，注入当前键活行 → `item_unreadable` 丢弃，检索/引用/回答都拿不到，citation 对 Alice/Bob 都 404 |
| 4 | 真实端到端入口；失败/授权/重试边界有断言，无单层 mock | **proven** | 全部用例走真实 `cmd/api` 二进制 + 真实 PG 16.4 + 真实 HTTP + 真实封存字节 + 真实 `document_ingest` maintenance；第二个/第三个**真实** API 进程承载 cache-off 与 dev-stub 故障；授权：owner-only/他人 404/管理员 404、CSRF/scope 由 T055/T064 既有用例覆盖；失败：无效开关启动拒绝（真实进程 exit≠0）、过期/组件变更/内容不可读三种丢弃原因、缓存写失败按 miss（代码路径）；恢复：丢弃后重新检索并存储新条目、撤销后重问得到新空结果而不是旧正文；无 mock 被测对象 |
| 5 | 记录版本、输入/hash、命令、原始结果与 profile；stub/real 分报，缺条件 blocked、未运行 not_run | **proven（含显式 blocked/not_run）** | 本报告 §3/§4/§5：本地 `local-hash-v1`、真实 provider blocked；`wrong-answer`/`foreign-chunk` 是 dev-stub 故障（real executed，但生成器本身是 stub）；浏览器 DOM、ANN/规模、全量 pytest 等 not_run 单列 |

## 3. 门禁命令与原始输出

环境：Go 1.27.3（`E:\orchara-toolchains\go\bin`）、Python 3.12.2、Node v24.13.0、
PostgreSQL 16.4（`127.0.0.1:55432`，`python tools/pg_dev.py status` → running true,
server_version 16.4）；Windows PowerShell；`product/.env.local` 提供本地口令，报告不含凭据。

<!-- GATES -->

```text
$ python -m pytest tests/f10 -q          # 第 1 次
.........................................................                [100%]
57 passed in 688.47s (0:11:28)

$ python -m pytest tests/f10 -q          # 第 2 次（背靠背，BRIEF §6.1）
.........................................................                [100%]
57 passed in 721.45s (0:12:01)

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 8.01s

$ go build ./...
(exit 0, no output)

$ go vet ./...
(exit 0, no output)

$ go test ./...
?   	orchara/platform/cmd/api	[no test files]
ok  	orchara/platform/internal/api	3.579s
...
ok  	orchara/platform/internal/knowledge	5.576s
...（全部 ok / no test files，无 FAIL；exit 0）

$ gofmt -l .
(empty)

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

套内细分：`tests/f10` 57 = T055 20 + T059 8 + T064 13 + T067 16
（`test_t067_migration.py` 3、`test_t067_cache.py` 8、`test_t067_rag_classes.py` 4、
`test_t067_comparison.py` 1）。两次整跑均全绿，主机负载不同只影响耗时。

对照实验（真实第二 API 进程 `ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE=off`；同一数据集/查询；
每模式 24 次测量）：

```text
$ python -m pytest tests/f10/test_t067_comparison.py -q -s
T067-COMPARISON {"documents": 8, "chunks": 24, "queries": ["华北地区十月的销售额是多少",
"退款政策需要几天内申请", "华东地区订单数量"], "measured_runs_per_query": 8,
"cache_on": {"cold_ms": [35.011, 26.296, 29.324], "hit_rate": 1.0,
"p50_ms": 6.954, "p95_ms": 29.881, "runs": 24},
"cache_off": {"p50_ms": 28.391, "p95_ms": 55.102, "runs": 24},
"validation": {"top_k": 20, "items": 20, "p50_ms": 8.319, "per_extra_item_ms": 0.091},
"cache_rows": [{"query": "华北地区十月的销售额是多少", "top_k": 20, "item_count": 20,
"body_bytes": 65521, "hit_count": 8}, {"query": "华东地区订单数量", "top_k": 5,
"item_count": 5, "body_bytes": 16931, "hit_count": 8}, {"query": "退款政策需要几天内申请",
"top_k": 5, "item_count": 5, "body_bytes": 17045, "hit_count": 8},
{"query": "华北地区十月的销售额是多少", "top_k": 5, "item_count": 5,
"body_bytes": 16931, "hit_count": 8}]}
1 passed in 33.98s
```

> 读数：命中 p50 ≈ 6.95 ms（含必做复检）vs 未命中 p50 ≈ 28.39 ms；冷请求
> 35.0/26.3/29.3 ms（含写缓存）；正确性（items/`acl_epoch`/embedding revision）逐字段
> 相同；top_k=5→20 的复检成本约 0.09 ms/项。另一轮单独实测为 5.249/10.672 vs
> 25.883/43.478（host 负载不同），结论一致：**缓存有收益，保留**。

### 合并（BRIEF §7.6）

```text
$ git merge --no-ff integration/f13a-f15a-f09-f10
合并提交 e225967（父 26dfd63 = 本票 docs 切片；父 c7d7e21 = 集成 tip/T062）
冲突：product/PROGRESS.md（两侧都新增票行/段落 → 双方保留：T062 + T067；无内容丢失）
重编号：ADR 0014 已被集成侧 T062 占用 → 本票 ADR 重编号为
        0015-f10-retrieval-cache.md（报告/PROGRESS 引用同步）
迁移：0037 未被集成占用（集成 tip 无 0037），链 0001..0037 连续
```

merge 后复跑目标门禁：

```text
$ python -m pytest tests/f10 -q          # merge e225967 之后
.........................................................                [100%]
57 passed in 762.66s (0:12:42)

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 7.51s

$ go build ./... ; go vet ./... ; gofmt -l .
(exit 0；gofmt clean)

$ go test ./...
(全部 ok / no test files，无 FAIL；exit 0)

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

## 4. 真实栈、输入与版本

- 真实 PostgreSQL 16.4：每个用例新建临时库，跑真实 `cmd/admin migrate`（0001→0037，
  链连续；重复 migrate no-op）；真实 `cmd/api` 二进制；真实 HTTP；真实 HMAC 签名对象链接
  与文件系统 `staged/`、`sealed/` 字节；真实 `cmd/maintenance` 进程（work=`document_ingest`，
  system_io=`artifact_finalize`）；对照/故障路径另起真实 API 进程（同 DB/对象目录，自己的
  端口与 mTLS 证书）。
- 输入/哈希：源文档由 `tests/support/t055.py::SAMPLE_DOCUMENT` 与
  `tests/f10/test_t067_comparison.py::benchmark_document` 确定性生成；上传时测试端计算
  SHA-256；缓存条目的 `body_sha256` 从真实 `platform.artifacts.sha256` 读取并与 chunk 行
  的 body artifact 对应；`items` 中的正文/引用与真实封存字节、chunk 行独立核对。
- 版本：`local-hash-v1`（384 维 embedding，T055）、`orchara-text-parser/1`、
  `orchara-paragraph-overlap-chunker/1`、`extractive-stub/1`（T064）、迁移 0037、
  缓存键版本 `knowledge-retrieval-cache/1`、TTL 900 s。
- 时序裕量（BRIEF §6.1）：对照实验记录真实 p50/p95 但只对"命中 p50 < 未命中 p50"做宽裕量
  断言（实测 6.954 vs 28.391 ms，约 4×；另一轮约 5×，不是边界竞速）；确定性断言（命中率、
  正确性、丢弃原因）不依赖亚秒窗口；没有"固定短 sleep 后断言无副作用"的用例，全部从权威行读取。

## 5. stub / real、blocked、not_run（分开记录）

| 项 | 结论 | 具体条件 |
|---|---|---|
| 检索缓存（PG 表 + 键 + 命中复检 + TTL） | **real executed** | 真实 PG 行/真实 HTTP/真实封存字节；见 §3/§2 |
| 删除/撤权隔离与"驱逐丢失" | **real executed** | 真实文档 tombstone + 真实活缓存行注入 + 真实请求；citation/检索/回答三路径 |
| 四类 RAG 失败定位 | **real executed（其中生成器是 stub）** | `wrong-answer`/`foreign-chunk` 是 ADR 0012 D2 的 dev-stub 部署故障；失败路径本身真实 |
| 真实 embedding provider | **blocked** | 本环境无凭据、构建无 adapter（T055 同一事实）；缓存键使用 `local-hash-v1` revision，换模型即换键 |
| Redis 缓存 | **not_run / 不采用** | 本机无 Redis；ADR 0015 D1 记录用 PG 的决定（可重建、少一个一致性边界） |
| 后台异步驱逐/清理 job | **not_run / 不要求** | 正确性不依赖驱逐（旧键不可达 + 复检 + TTL 懒清理）；F07 可做资源清理优化 |
| 物理删除缓存行 / 删除治理 `delete_content` | **not_run / 后续 F07** | 本票不实现删除 job；只在测试里写 F07 形状 tombstone 证明立即不可读 |
| ANN/pgvector/规模基准 | **not_run / 后续规模票** | 缓存不改变候选上限与精确排序；T055/T064 同一边界 |
| 浏览器 DOM 驱动页面 | **not_run** | 本票无页面改动（缓存对客户端透明，非契约元数据可观察）；与既有页面票同边界 |
| 真实模型生成 | **blocked** | 无 provider 凭据/adapter；`wrong-answer`/`extractive-stub/1` 是带标签 stub |
| 全量 `pytest tests`（≈67 分钟） | **not_run** | BRIEF §3 规定由 orchestrator 合并后执行 |

## 6. 下一票接口（复用点）

- 表：`platform.knowledge_retrieval_cache`（非契约；`ON DELETE` 无级联，KB 为软删除）；
  关键列 `cache_key`/`principal_id`/`knowledge_base_id`/`acl_epoch`/`kb_revision`/
  `source_revision_digest`/`embedding_model_revision`/`canonical_query`/`top_k`/`items`/
  `expires_at`。
- Go：`knowledge.Service.Cache CacheOptions`（`Enabled`/`TTL`/`Now`）；
  `knowledge.CacheOptionsFromEnv`；`knowledge.CacheDiscard*` 原因词；
  `knowledge.RetrievalCacheInfo`（`QueryResult.Cache`、`AnswerRetrieval.Cache`）；
  `retrievalCacheKey`/`canonicalCacheQuery`/`shouldStoreRetrievalCache`（同包测试可复用）。
- HTTP：`retrieval.cache` additive 元数据（检索与回答两个非契约面）；部署开关
  `ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE=on|off`。
- 失效 seam：F07 删除治理可额外清理 `items`/行（优化）；T061 激活切换必须移动
  `knowledge_documents.revision`/`active_revision`（本票摘要已包含）；未来共享/ACL 变更必须
  bump `knowledge_bases.revision` 或引入独立 `acl_epoch`（列已分离）。
- 生成器故障约定：`ORCHARA_KNOWLEDGE_ANSWER_FAULTS` 新增 `wrong-answer`（ADR 0012 D2 /
  ADR 0015 D4），仍只在 dev-stub/capacity-stub 生效、只能造成误标或拒绝。

## 7. 偏差与边界（均已记录）

1. 缓存表是**非契约**的 additive 表（契约无检索操作/缓存表）——ADR 0015，沿用
   ADR 0005/0010/0012 先例；`contracts/**`/`docs/spec/**` 未改，`sync_spec --check` no drift。
2. 本机无 Redis，缓存放 PostgreSQL——ADR 0015 D1（可重建、PG 即权威）。
3. `acl_epoch` 在本切片取 `knowledge_bases.revision`（T055/ADR 0010 已记录的替身），与
   `kb_revision` 分成两列；未来共享/ACL 变更必须 bump 它（ADR 0015 D1/D7）。
4. 新增 dev-stub 故障 `wrong-answer` 仅用于阶段定位，不改变生产行为——ADR 0015 D4；
   ADR 0012 仍管回答面与引用校验。
5. 不新增后台驱逐 kind；过期行按 principal 懒清理——ADR 0015 D1/D7。
6. `T055` 的既有 retrieval 精确断言更新为 additive `cache` 元数据——语义不变，见 §1.4。
