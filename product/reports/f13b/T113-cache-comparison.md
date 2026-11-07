# F13B T113 验收：对比缓存开关并验证过期/越权命中为零

- 任务：F13B / GitHub Issue #114（票 `T113`，epic F13B；EVALUATION §2 缓存对照行/
  §4 FAULT-09 删除后缓存）；分支 `t/t113-cache-toggle`（worktree `E:\Orchara-wt\t113`，
  基线 = 集成分支 `integration/t113-t114-t122` @ `ae1af91`）。
- 状态：**`passed`**（本票范围 = 固定输入下的缓存 on/off 对照、删除与撤权后的
  真实命中矩阵、无稳定来源/空/部分/超大结果的负向证据、失败与恢复边界；真实 PostgreSQL 16.4
  + 真实编译二进制 + 真实 HTTP + 真实封存对象字节 + 真实 maintenance 进程 + 第二个/第三个真实
  API 进程；无 mock、无跳过）。
- 证据：`reports/f13b/T113-cache-comparison.json`（可重放，逐次延迟/命中/行数/矩阵）、
  `tests/f13b/test_t113_cache_comparison.py`（5 条证据测试）、`tests/support/t113.py`。
- 本票发现并修复 **1 个产品缺陷**（红→绿见 §0）：命中复检只比对 `artifacts.sha256` 列，
  没有读封存对象；对象缺失/被替换时缓存命中仍返回记忆正文，而权威路径会跳过该 chunk。
  修复：命中时重读每条封存正文并核对正文与 sha256（`internal/knowledge/cache_store.go`）。
- 边界（如实）：真实模型/embedding provider 缺失 → 真实延迟/质量/费用 `blocked`；
  本切片知识库 owner-only、无 per-KB/document grant → “grant 撤销”入口不存在，以文档
  tombstone（内容撤权）+ 管理员禁用凭据 + 跨 principal 隔离三种真实入口覆盖；知识缓存无 SSE
  面；浏览器 DOM `not_run`（页面脚本以 Node 执行已提交字节）；容量规模 `not_run`（T114 窗口）。

## 0. 工作记录（TDD 红→绿）

1. 读 `ENV-B2.md`、`T113.md`、issue #114、`internal/knowledge/{cache,cache_store,service,answer,deletion}.go`、
   `internal/api/knowledge.go`、`internal/policy/{policy,grants}.go`、ADR 0015、T067/T109 报告、
   `tests/f10/test_t067_*`、`tests/support/{t067,t069,t055,t064,t061}.py`，确认切片边界：
   缓存是**可重建优化**，删除/撤权的权威事实在 PG 行与封存字节里。
2. 垂直切片（每条先红后绿，原始日志在 `E:\Orchara\.scratch\f13b\T113\`）：
   - 先写 `tests/f13b/test_t113_cache_comparison.py`（ImportError: `support.t113` 不存在，
     `red-01-missing-helper.log`）→ 补 `tests/support/t113.py`；
   - 对照实验切片：先因 Node 输出编码失败（`red-03-run.log`，测试侧缺陷）→ 修复；
     随后 24 spec × 2 模式 × 冷/热 的真实测量通过；
   - 删除矩阵切片：因“历史 hit_count 是累计值”错误断言红（`red-04/05-deletion.log`）→
     改成“post-delete 增量 + `last_hit_at <= deleted_at` + 空键无行”的真实不变量；
   - 撤权切片：管理员 disable/跨 principal/投毒/回答不缓存全部通过（`red-06-revocation.log`）；
   - 负向切片**发现产品缺陷**：先 warm 条目、再移除该 chunk 的封存对象文件，请求仍
     `cache.hit=true`（`red-07-negative.log`，命中正文与权威路径不一致）→ 先写 Go 单测
     `TestCachedBodyVerificationRejectsChangedSealedBytes`（`red-08-go-unit.log`：
     `undefined: cacheBodySHA256 / cachedBodyMatches`）→ 实现命中时重读封存正文 → 红转绿
     （`green-01-negative.log`，`item_unreadable` 丢弃、条目删除、恢复后重新存储并命中）；
   - 边界切片：恢复后第一次请求命中“幸存条目”是正确行为（原断言写成 miss 红，
     `red-09-boundaries.log`）→ 改为“幸存条目可再次命中 + 新 top_k 缺失→存储→命中”；
   - 全量证据跑 `green-03/04-full-evidence.log`：5 passed，JSON 五段全 passed。
   - 编排中断（**不是失败**）：T114 容量 WINDOW 期间的一次 `tests/f10` 被编排者终止；WINDOW 结束后
     又因锁文件名筛选（无前缀 `owner.txt` vs `*.owner.txt`）在本票脚本里漏检，一次 `tests/f13b`
     与 T114 的独占门禁并发启动——两次运行均**不作为证据**（原始日志保留为
     `gate-f10.log`/`gate-f13b-aborted-overlap.log`）。最终 f10/f13b/证据在 realstack 目录无其它
     owner 时独占重跑（`gate-f10-solo.log`/`gate-f13b-solo.log`/`evidence-final.log`）。

## 1. 交付内容

| 文件 | 内容 |
|---|---|
| `tests/f13b/test_t113_cache_comparison.py` | 5 条真实栈证据测试：对照、删除、撤权/跨用户、负向、边界 |
| `tests/support/t113.py` | 固定输入、百分位/成对统计、独立真值（缓存行/封存对象/时间戳）、部署开关/后端故障、页面脚本 Node 执行、证据 recorder |
| `tests/support/...` 复用 | `t067`（缓存行/投毒/第二 API）、`t069`（真实删除）、`t061`（reindex）、`t064`（answer/fault server）、`t055`（摄入/检索/引用） |
| `reports/f13b/T113-cache-comparison.json` | 逐次测量（96 条）、P50/P95/P99、命中率、费用口径、删除/撤权矩阵、负向/边界、固定输入/版本/命令 |
| `internal/knowledge/cache_store.go` + `cache_test.go` | 命中复检新增封存正文重读与 sha256 核对（T113 发现的产品缺陷修复） |
| 本报告 + `PROGRESS.md` + `.scratch/f13b/T113-REPORT.md` | 验收记录、AC 对照、门禁与原始 tail、blocked/not_run |

## 2. AC 逐条

| # | 验收标准 | 结果 | 证据 |
|---|---|---|---|
| AC1 | 固定输入/版本/模型/资源比较缓存开 vs 关 | **proven** | 固定 8 文档（24 chunk，逐文档 sha256 记录）、固定 24 spec（8 查询 × top_k 3/8/20，固定顺序）、`local-hash-v1`、固定 TTL 900s、同一 DB/对象目录的 cache-on 与 cache-off 两个真实 API 进程；每模式 48 次测量（24 冷 + 24 重复），冷/热成对取样（`comparison.measurements` 96 行） |
| AC2 | 报告命中/P50–P95/费用 | **proven** | 命中率：on 冷 0/24、on 重复 24/24（100%）、on 整体 50%、off 0/48；重复阶段 on p50/p95/p99 = **7.311/32.393/37.807 ms** vs off **11.642/32.220/35.632 ms**（成对中位比 0.6119，on 更快 17/24）；按 top_k 3/8/20：p50 比 0.403/0.598/**1.756**；费用 = **0 USD**（本地 hash embedding、无模型调用；真实模型费用 `blocked`），权威检索执行次数 24 vs 48（off 侧翻倍） |
| AC3 | 删除/撤权后过期或越权命中为零 | **proven** | 删除：真实 `DELETE /knowledge-documents/{id}` 后 `hit=false`、旧键不再命中、引用 404 无正文、答案无被删内容、投毒活行 `item_unreadable` 丢弃、reindex/replay/re-ingest 都不复活；并发删除风暴 5 条 post-commit 请求 **0 hit/0 items/0 marker**；撤权：跨 principal 404/无引用正文、投毒 Bob 行丢弃、管理员禁用 Alice 后 401 且 Alice 行 hit 3→3、Bob 仍命中自己的行 |
| AC4 | 不缓存无稳定来源或跨用户个性化答案 | **proven** | 新增来源 1→2→3 文档时 `source_revision_digest` 变化、旧键不命中且 `hit_count` 不动；空结果/部分结果不存储（行数 0）；超出 256 KiB 的 50-chunk 条目实测 267,960 B → `stored=false`、无行；回答每请求重新生成（`wrong-answer` 故障服务器在检索命中的同时给出新生成哨兵），答案文本不进入缓存，跨用户答案互不含对方 marker |
| AC5 | 正式端到端入口；失败/授权/重试恢复有断言，不以单层 mock 代替 | **proven** | 全部走真实 REST 用户路径（检索/引用/回答/删除/管理员 PATCH）；`/knowledge` 页面脚本以 Node 执行已提交字节，页面构造的检索 body 与测量请求一致；未知开关值真实进程启动 exit≠0；缓存表不可用时第二真实进程 `cache_unavailable` 降级为权威答案且引用仍可读；恢复后幸存条目再次命中、缺失 spec 重新存储后命中；删除进行中的并发查询有 start-time 断言 |
| AC6 | 记录版本、输入/hash、真实命令、原始结果与 profile；stub/real 分报、blocked/not_run | **proven（含显式 blocked/not_run）** | JSON：`versions`/`commands`/`fixed_inputs`（文档 sha256、查询序列、迁移链尾、parser/chunker/embedding revision、cache key version/TTL）、逐次原始结果、环境 profile；`blocked` = 真实 embedding provider、真实模型生成/质量/延迟/费用；`not_run` = SSE、浏览器 DOM、容量规模、Redis；本报告 §8/§9 |

## 3. 实验设计（固定项）

- **语料**：`tests/support/t113.py::benchmark_document(i)`（T067 形状的确定性销售/退款文本），
  `i=0..7`，UTF-8；每文档 sha256 已记录；共 **8 文档 / 24 chunk**。
- **查询集**：8 条固定中文查询 × top_k ∈ {3, 8, 20} = **24 spec**，固定顺序
  （查询主序、top_k 次序）。每个 spec 在每模式各跑冷/热两次。
- **版本**：`local-hash-v1` embedding、`orchara-text-parser/1`、
  `orchara-paragraph-overlap-chunker/1`、`extractive-stub/1`（仅回答路径）、
  cache key version `knowledge-retrieval-cache/1`、TTL 900s、迁移链尾见 JSON `fixed_inputs`。
- **资源**：同一主机、同一 PG 16.4、同一 DB/对象目录；cache-off 是第二个真实 API 进程
  （部署开关只在启动时读取，无法在同一进程内切换；ADR 0015 D5 同一做法）。
- **降噪**：两模式在各阶段内**成对**取样（同一 spec 先 on 后 off），冷/热分离；先跑一次
  非测量 warmup 查询；记录成对中位比与 P50 比，避免先后相位被宿主慢窗口翻转。
- **费用口径**：检索端点只用进程内 `local-hash-v1` hash，不调用任何模型/厂商；回答路径是
  带标签的 extractive stub。因此 `estimated_external_cost_usd=0.0` 是**如实记账**（不是省略）；
  真实模型费用 `blocked`。权威检索执行次数的代理指标：on 24 / off 48。

## 4. 对照结果（真实执行，见 JSON `comparison`）

| 阶段（24 次/格） | cache on | cache off | 说明 |
|---|---|---|---|
| 冷（miss，on 侧写缓存） | p50 16.470 / p95 39.843 / p99 41.154 ms | p50 27.819 / p95 33.603 / p99 36.059 ms | 冷阶段两侧都是权威检索；on 多一次写缓存 |
| 重复（on 侧命中） | p50 **7.311** / p95 32.393 / p99 37.807 ms | p50 11.642 / p95 32.220 / p99 35.632 ms | on 命中率 **24/24**，off 0/24 |
| 成对重复（24 对） | — | — | 中位比 **0.6119**、P50 比 0.628、on 更快 **17/24** |

按 top_k 分解（重复阶段，各 8 次）：

| top_k | on p50 / p95 (ms) | off p50 / p95 (ms) | p50 比 |
|---|---|---|---|
| 3 | 5.139 / 26.833 | 12.749 / 35.632 | **0.403** |
| 8 | 6.630 / 32.393 | 11.083 / 25.684 | **0.598** |
| 20 | 20.992 / 37.807 | 11.952 / 32.220 | **1.756** |

读数（不夸大）：

- 小/中 top_k（3、8）命中比权威 miss 快约 1.7–2.5×；top_k=20 在本 24-chunk 语料上没有收益，
  甚至更慢：T113 的必做正确性修复让命中也要重读 20 条封存正文并核对 sha256，而这与 miss 路径
  读取候选正文的成本同量级；命中还要多付一次大行 `items` jsonb 读取/反序列化。
- 本机同时跑其它套件，P95 波动明显；配对视图（每一次 on/off 相邻取样）比跨相位 P50 更稳，
  报告同时给出两者。数字只代表本机该固定输入，不外推到容量/真实模型。
- 正确性：24/24 spec 在 on 冷、on 命中、off 三种答案上 `items`/`next_cursor`/`acl_epoch`/
  `index`/embedding revision 逐字段相同（精确 JSON 相等断言）。
- 缓存行：25 行（24 spec + warmup），每行 `hit_count=1`（warmup 0）；cache-off 进程写入 0 行。

## 5. 删除矩阵（AC3，真实 API）

| 检查 | 结果 |
|---|---|
| warm 后同一查询 | on 命中；旧行 `hit_count=1` |
| 真实删除 `DELETE /knowledge-documents/{deleted}` | 202；文档 `state=deleted`、`deleted_at` 写入、revision+1；deletion job `pending` 且 `retrieval_cache` scope 未完成（不靠清缓存行） |
| 删除后同查询 | `hit=false`、`key_prefix` 改变、`items` 无被删文档、marker 不出现；新键为剩余文档正常存储 |
| 旧活行 | 仍在，`hit_count` 保持 1（未因删除被动过；历史 hit 早于 tombstone，`last_hit_at <= deleted_at`） |
| 引用 / 文档 / 诊断 | 404 `resource_not_found`，响应无 `text`/`citation`；marker 不出现 |
| 回答 | 200 `answered`（只剩保留文档），引用不含被删文档，marker 不出现 |
| 活行投毒（把被删 item 塞进当前键） | `hit=false`、`discarded=true`、`discard_reason=item_unreadable`、无被删正文；行被替换为干净条目 |
| reindex / 重放 ingest / 重摄已删源 | 404 / job `failed`+`failure_class=business` / 404；文档仍 deleted，结果无 marker |
| 并发删除风暴（最后一份文档） | 17 次请求，其中 5 次在 DELETE 返回后开始：**0 hit / 0 items / 0 marker**；post-delete 单次读后 hit 计数快照不变；空结果不建行 |
| 最终行事实 | 引用被删文档的活行 post-delete 增量 0；空键无行；允许并发命中校验淘汰旧行（行数只减不增） |

## 6. 撤权 / 越权矩阵（AC3）

前置：本切片知识库 **owner-only**；`policy.ResourceTypes` 只有 `application`，没有 per-KB/
document 共享或 grant 入口（代码事实，记录为 `not_applicable`）。因此“撤权”用现有真实入口：

| 入口 | 观察 |
|---|---|
| 跨 principal：B 用同一 query 访问 A 的 KB（A 的活行存在） | 404 `resource_not_found`，无 `items`/`retrieval`/正文；B 打开 A 的引用 404 无 text；A 访问 B 的 KB 同样 404 |
| 投毒 Bob 活行（塞入 Alice 的真实 item/body） | `hit=false`、`discarded=item_unreadable`；响应无 Alice marker，仍返回 Bob 内容；替换后的行无 Alice 内容 |
| 管理员撤凭据：`PATCH /admin/principals/{alice} {disabled:true}` | 200 `disabled`；Alice 的检索/回答 401 `session_revoked`，响应无 items/正文；Alice 活行 `hit_count` 3→3（未被服务）；Bob 仍正常命中自己的行 |
| 内容撤权：文档 tombstone（§5） | 同 query 旧键不命中、无正文；命中校验/引用/回答均拒绝 |
| 回答个性化 | Alice/Bob 的答案只含自己的 marker；重复回答在检索缓存命中时仍重新生成（`wrong-answer` 故障服务器输出新哨兵且 `retrieval.cache.hit=true`）；缓存 `items` 字段是固定 chunk 投影，答案文本字段不存在，故答案不跨用户/时点复用 |

## 7. 负向与非缓存（AC4）

| 场景 | 结果 |
|---|---|
| 新增文档（1→2 文档） | source digest/count 改变，旧键 `hit=false`，旧行 hit 不动，新行 `stored=true` |
| 重传同字节为新文档（2→3 文档） | 新 document id、新 digest；上一键不命中；`documents_for_artifact` 独立核实 |
| 空结果（空 KB） | `items=[]`、`hit=false`、`stored=false`；重复请求仍 miss；该 KB 缓存行 0 |
| 部分结果（移除一个候选的封存 body 文件） | 该 chunk 被跳过、`stored=false`、`hit=false`、行 0；恢复文件后 `stored=true`，下一请求命中 |
| 命中时封存 body 变为不可读（T113 修复点） | 修复前 `hit=true`（缺陷，`red-07`）；修复后 `hit=false`、`discarded=true`、`discard_reason=item_unreadable`、条目删除、引用 404；恢复后重新存储并命中 |
| 超大条目（50 chunk × 1200 rune 4 字节 emoji，top_k=50） | 实测 body 267,960 B > 262,144 B → `stored=false`、`hit=false`、行 0（真实 API 达到上界） |

## 8. 失败/恢复边界（AC5）

| 场景 | 结果 |
|---|---|
| 未知开关值 `ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE=maybe` | 真实进程启动 exit 1，stderr 含开关名（fail closed） |
| 缓存后端不可用（真实 `ALTER TABLE ... RENAME`，期间启动第二真实 API） | 查询 200；`enabled=true, hit=false, discarded=true, discard_reason=cache_unavailable, stored=false`；items 与权威 miss 完全一致；引用 200 可读 |
| 恢复（改回表名后启动第三真实进程） | 幸存旧行可再次命中（`validated_chunks=3`，旧行 hit 1→2）；无行的 top_k+1 先 `stored=true` 再命中 |
| 删除进行中的并发查询 | §5 风暴：post-commit 0 hit/0 body/0 marker；post-delete 读后 hit 快照不变 |
| 页面用户路径 | `/knowledge` 200、`/assets/knowledge.js` 200；Node 执行已提交字节，`buildRetrievalBody(query, top_k)` 与测量请求 body 一致；浏览器 DOM `not_run` |

## 9. 门禁命令与原始输出

环境：Windows PowerShell；Go 1.27.3（`E:\orchara-toolchains\go\bin`）、Python 3.12.2、
Node v24.13.0、PostgreSQL 16.4（`127.0.0.1:55432`，`python tools/pg_dev.py status` → running true）。
真实栈按 ENV-B2 §2.1 执行：T114 容量 WINDOW 结束后，最终 `tests/f10`、`tests/f13b`、T113 延迟证据
均在 realstack 锁目录无其它票 owner 时**独占重跑**（T113 含延迟测量，必须独占）；T114 容量
WINDOW 期间的一次 `tests/f10` 尝试被编排中断，未用作证据。报告不含任何凭据。

```text
<!-- GATES -->
```

## 10. 真实栈、输入与版本

- 每个证据测试新建临时数据库，跑真实 `cmd/admin migrate`（0001→当前尾，链连续）与 bootstrap；
  真实 `cmd/api` 二进制 + 真实 HTTP + 真实文件系统对象存储（HMAC 签名链接、staged/sealed 字节）
  + 真实 `cmd/maintenance` 的 `document_ingest`/`artifact_finalize` 池；cache-off/故障/恢复各起
  独立的真实 API 进程（同 DB/对象目录，自己的端口与 mTLS 证书）。
- 输入/哈希：语料由 `support/t113.py` 确定性生成并记录每个文档 sha256；缓存条目中的
  `body_sha256` 来自真实 `platform.artifacts` 行，命中时由修复后的代码重读封存字节核对；
  引用 span 与正文来自真实 chunk 行/封存对象（T109 同一边界）。
- 版本：见 JSON `versions`；迁移链尾见 JSON `fixed_inputs.migration_chain`。

## 11. stub / real、blocked、not_run（分开记录）

| 项 | 结论 | 具体条件 |
|---|---|---|
| 缓存 on/off 对照（真实进程/真实 PG/真实对象） | **real executed** | §4；两/三个真实 API 进程 |
| 删除/撤权/越权零命中 | **real executed** | §5/§6；真实删除事务、真实 admin PATCH、真实投毒活行 |
| 非缓存负向与超大条目 | **real executed** | §7；命中 body 重读缺陷已修复 |
| 失败/恢复边界 | **real executed** | §8；真实 rename 后端故障 + 新进程恢复 |
| 真实 embedding provider | **blocked** | 本机无凭据/端点/adapter（继承 T055）；数字是 `local-hash-v1` 数字 |
| 真实模型生成/质量/延迟/费用 | **blocked** | 回答路径是 `extractive-stub/1`；不冒充真实模型数字 |
| per-KB/document grant 撤销 | **not_applicable（代码事实）** | `policy.ResourceTypes={application}`；以 tombstone/凭据/跨 principal 覆盖 |
| SSE 知识路径 | **not_applicable** | 知识检索/回答面没有 SSE 端点；run-event SSE 是另一面 |
| 浏览器 DOM 驱动 `/knowledge` | **not_run** | 无 Playwright/Chromium；页面脚本以 Node 执行 |
| 容量规模（100 用户/并发进程） | **not_run** | T114 独占窗口；本票单机固定语料 |
| Redis 缓存后端 | **not_run / 不采用** | 本构建无 Redis adapter；ADR 0015 用 PG |

## 12. 偏差与边界（均已记录）

1. **命中路径成本上升**：为保证“命中的答案与权威路径一致”，命中现在重读每个返回 chunk 的
   封存正文并核对 sha256。固定 24-chunk 语料上 top_k=20 的命中收益被抵消；小/中 top_k 仍有
   1.7–2.5× 收益。这是正确性优先的取舍，已由 §4 实测记录，而不是把成本藏起来。
2. **两个真实进程**：部署开关只在启动时读取（fail closed），off 模式必须另起真实进程；
   这是部署差异不是 mock，采用成对取样降低宿主噪声影响。
3. **无 per-KB grant 入口**：本切片 owner-only；AC3 的“撤权”以真实内容 tombstone、管理员禁用
   凭据、跨 principal 隔离三种入口证明，grant 撤销记录为 `not_applicable`，不伪造入口。
4. **删除 job 的物理清理未在本票执行**：T069 语义= tombstone 立即禁读、scopes 留给后续治理；
   本票证明“缓存行还在也零命中”，不以清缓存行代替读路径复检；T071 的物理清理另有票。
5. **并发删除的合法竞态**：请求在 DELETE 提交前开始、提交后完成时，可能读到提交前快照并给
   旧行记一次历史 hit；断言按 start-time 区分，并要求 post-commit 响应 0 hit/0 body、post-delete
   读不再改变 hit 快照，且 `last_hit_at <= deleted_at`。
6. 未改 `docs/spec/**`、`contracts/**`、迁移；产品改动仅命中复检（`cache_store.go`）与其单测。

## 13. 下一票接口（复用点）

- 证据/统计：`tests/support/t113.py` 的 `benchmark_document`/`QUERY_SPECS`/`stats`/`comparison_summary`/
  `Evidence`（`F13B_WRITE_EVIDENCE=1` 才写、只写全 passed）；删除/撤权矩阵可复用到 F13B 其余票。
- 产品：`knowledge.RetrievalCacheInfo`/`CacheDiscard*`；命中复检新增
  `cachedBodyMatches`/`cacheBodySHA256`，T114 容量票与未来缓存后端票必须保留“命中=权威答案”。
- 运维：`ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE=on|off`，未知值启动拒绝；缓存后端不可用按 miss 降级。
