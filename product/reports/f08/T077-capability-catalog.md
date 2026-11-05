# F08 T077 验收：浏览授权用途目录并在索引落后时安全检索

- 任务：F08 / GitHub Issue #78（票 `T077`，规划 ID F08.02），分支
  `t/t077-catalog-index`（worktree `E:\orchara-worktrees\t077`）；父门禁 F08。
- 状态：**`passed`**（本票范围 = 授权用途目录投影 + 非契约搜索面 + ACL/发布过滤先于检索 +
  索引落后回退授权全文并二次查权 + 可重建/可观测的目录索引 + `catalog_index` handler +
  `/catalog` 页面与 Node 可执行纯函数；真实 PostgreSQL 16.4、真实编译二进制、真实 HTTP、
  真实编译产物 manifest 字节、真实 admin rebuild 进程、真实 maintenance 进程、Node 24）。
- 边界（如实）：模型路由/候选选择/澄清/控制预算（T079）、交接 UI（T082）、子运行（T078）、
  删除/清理（T071/T072）、发布/撤销端点（T068/T070）均不在本票；本票只发布 T068/T070 可调用
  的 `IndexVersionTx`/`EnqueueIndexVersionTx` 接缝，未修改其端点。
- 迁移编号：**`0045_catalog_index.sql`**。orchestrator 预分配 `0051`，但本 worktree 基线链止于
  `0044`；`tests/support/stack.py::migration_chain` 强制 `0001..NNNN` 无缺口，故工作树按
  “下一个空号”使用 0045 并在测试中按 slug 断言；**合入 integration 时分发号应为 0051，
  合并者需重编号（测试不 pin 数字）**。ADR 按要求使用 **0026**。
- 最终合并：`integration/t068-t082` 在本票开发期间从 `57d90df` 前进到 `0fcf8ec`
  （T068 发布/版本合并）；已 `git merge integration/t068-t082`（merge commit `4dfcf37`），
  冲突无；迁移链在合并树仍连续到 0045。

## 0. 工作记录（简短）

1. 读票面、`BRIEF.md`、`notes/f08-conversations.md` §3.2/§7/§10/§11、规格
   `ROUTING-MEMORY-KNOWLEDGE.md` §5/§9/§27、`BACKGROUND-JOBS.md` §24、`DATA-MODEL.md` §20/§56、
   `contracts/database.sql:131`、`contracts/openapi.json` 的 `listApplications`，以及
   `internal/catalog/{catalog,versions,publication,revocation,outbox}.go`、
   `internal/background/{job,enqueue,handlers}`、`cmd/admin`、`cmd/maintenance`、`internal/api`、
   `apps/web/src/{apps.html,assets/app.js,assets/chat.js}` 与既有 `tests/f08`。
2. TDD 垂直切片（每片先红后绿）：
   - 迁移：链连续/表/列/GIN/台账 CHECK/worker 零授权 → `0045`；
   - 搜索：真实编译发布 3+1 个版本 → admin rebuild → 真实 HTTP 搜索的 ACL/排名/cursor/
     `listApplications` 零变化 + grant 授予/撤销 + `%`/`_` 字面量；
   - 陈旧行：真实撤销/归档/他人版本 + 测试插入的陈旧投影行 → 不出现、不可执行；
   - 回退：新发布未索引 → 数量不一致 → fallback；撤销后第二次授权检查拒绝并计数；
   - 重建：truncate 投影 → rebuild（epoch、outbox delivered）→ 恢复；hash 不匹配 → 回退；
     真实锁 + 杀进程的“中断重建”只留 `rebuilding` 台账、不半服务；坏能力卡 → 失败码台账；
   - 分离：真实 knowledge 行/索引版本计数与身份签名在 rebuild 前后不变；
   - handler：incremental job 正常写、撤销后复读删行、坏卡 business 失败；
   - 页面：结构 marker + Node 真实执行纯函数。
3. 写 ADR 0026、本报告、PROGRESS 行；未推送、未关 issue、未改
   `docs/spec/**`/`contracts/**`；最终合并后重跑门禁并删除 basetemp。

## 1. 交付内容

### 1.1 数据模型（`0045_catalog_index.sql`，合入时重编号 0051）

- `platform.application_catalog_entries`：契约的 `application_version_id`(PK)、`search_text`、
  `source_manifest_sha256`、`updated_at`；**不建**契约里的 `embedding_model_revision`/
  `embedding vector`（本机无 pgvector；T077 只做全文，ANN/向量归 T064/T067——迁移头、
  ADR 0026 §2、本报告 §4 均记录该偏差）。非契约附加列：`application_id`（SQL ACL 谓词用；
  读取时用权威 version/application 复核）与生成列 `ts`（GIN）；`source_manifest_sha256`
  普通索引用于就绪计数。
- `platform.catalog_index_state`：单行台账（`id='catalog'`）——
  `state ∈ {ready,rebuilding,failed}`、`projection_epoch`、`rebuild_started_at`、`rebuilt_at`、
  `last_error_code`/`last_error_at`、`expected_count`/`indexed_count`。
- 外键指向 `application_versions`/`applications`；`orchara_worker`/PUBLIC 零授权；
  重复 `migrate` 为 no-op；迁移头记录所有非契约偏差与 owning ticket。

### 1.2 `internal/catalog`（本票发布的复用接缝）

- `SearchAuthorized(ctx, principalID, q, cursor, limit, now) (*CatalogSearchPage, error)`：
  - 先读台账并比较“权威可读未撤销未归档且有非空能力卡”的版本数
    （`countEligibleVersions`，ACL 在 SQL）与“仍匹配当前 manifest hash 的合法投影行数”
    （`countValidEntries`，ACL 在 SQL）。仅当 `state='ready'` 且两者相等才走索引；
  - 索引查询：`application_catalog_entries JOIN application_versions JOIN applications`，
    WHERE 内含 ACL、`archived_at IS NULL`、`revoked_at IS NULL`、同 application、hash 相等、
    `search_text ILIKE ... OR ts @@ plainto_tsquery`，排名 `rank`（用途命中 0 / 元数据命中 1）
    与 `LIMIT` 之前完成；keyset `(rank, version_no DESC, id DESC)`；
  - 回退查询：直接对 `application_versions.capability_card` 做授权全文（ACL + 归档在 SQL）；
    **撤销刻意留给逐行第二次检查**（notes §7 的顺序，`ROUTING:9` “检索返回后再次核对版本/ACL”），
    拒绝数进 `rejected_by_recheck`；
  - 每条返回行（两条路径都一样）再由 `authoritativeCatalogHit` 用**另一条 SQL** 复读
    ACL/撤销/归档/能力卡；畸形能力卡同样拒绝；
  - `retrieval` 非契约元数据如实上报 `source`/`stale`/`index_state`/`projection_epoch`/
    `rechecked`/`rejected_by_recheck`，页面据此显示“回退检索（索引落后）”。
- `IndexVersionTx`/`IndexVersionNow`：事务内 `FOR SHARE` 复读权威版本；可读则 upsert，
  已撤销/归档/不存在则删除旧行；坏能力卡返回 `ErrCatalogSourceInvalid`。
- `EnqueueIndexVersionTx`：以 `<version_id>:<manifest_sha256>` 为 dedup key 写
  `catalog_index` job（owner = 应用 owner）。T068/T070 可在发布/撤销事务内调用；
  本票不依赖钩子存在。
- `RebuildCatalog`（admin 进程）：全量重建先提交 `rebuilding` + 新 epoch（不读版本表）→
  删不再合法的旧行 → 逐版本一事务 upsert 并推进 `indexed_count` → 最后事务
  `state='ready'` + `rebuilt_at` + 把待投递 `catalog_index` outbox 标 delivered
  （`attempts+1`）。错误写 `last_error_code`/`last_error_at` 并保持 `failed`；通知/超时
  不改变“绝不半服务”的读侧判定。
- `parseCatalogCard`/`likePattern`：能力卡必须是带非空 `purpose` 的 JSON 对象，且出现的
  `includes`/`excludes`/`required_inputs`/`expected_outputs`/`examples` 必须是字符串数组；
  `%`/`_` 被转义为字面量，用户输入不会变成通配。

### 1.3 后台 handler、API、admin、页面

- `internal/background/handlers/catalog_index.go`：`catalog_index` 已注册（retry 表：仅
  transient_storage_io/transient_internal/lease_expired 可重试）；handler 在写事务内复读权威行、
  校验 job 的 `expected_sha256`；坏卡/缺失是 business 失败，撤销/归档/不存在是“删旧行并成功”。
- `internal/api/catalog.go` + `GET /api/v1/catalog/search`（scope `application:read`）：
  非契约响应 `{items,next_cursor,retrieval}`；`listApplications` 的参数/响应完全未动。
- `cmd/admin/catalog.go`：
  `orchara-admin catalog rebuild [--all|--version UUID]`、`catalog index --version UUID`、
  `catalog status`；JSON 输出便于测试/运维直接断言。
- `/catalog` + `/assets/catalog.js`：检索框、用途卡（用途/包含/不包含/必填输入/预期输出/
  示例）、版本号、应用名、索引/回退徽标、cursor 下一页、`用这个版本提交 → /chat?version=`；
  纯函数 `catalogSearchPath`/`catalogCard`/`catalogVersionLabel`/`describeCatalogHit`/
  `describeRetrieval`/`describeCatalogEmpty`/`describeCatalogCount`/`catalogSubmitTarget`/
  `catalogSearchRequest` 在 Node 24 下真实执行；页面全部用 `textContent` 构节点，无 `innerHTML`、
  无 localStorage/sessionStorage。
- `apps.html` 增加 `/catalog` 导航链接；既有应用列表渲染未改。

## 2. 验收标准（逐条，全部由已执行测试证明）

| AC | 结论 | 证据（真实执行） |
|---|---|---|
| 撤销/旧索引不泄漏描述、不能作为运行候选；测试插入的陈旧投影行被权威复检过滤 | passed | `tests/f08/test_t077_search.py::test_search_never_returns_revoked_foreign_or_archived_versions`：rebuild 后撤销一个版本、归档一个应用、取他人一个版本，再**直接插入 3 条陈旧投影行**（搜索文本含 secret marker，测试断言 DB 里确有 3 行）；`GET /catalog/search` 不返回它们，响应正文不含任何 secret/purpose/owner id；`POST /requests` 分别 409 `version_revoked`、409 `version_archived`、404 `resource_not_found`，且零 Request/Run。`test_t077_jobs.py::test_the_handler_reconciles_a_revoked_version_before_writing`：job 在撤销前入队，真实 maintenance 进程执行后删除陈旧行、job succeeded。`test_stale_index_falls_back...`：回退候选中的已撤销版本被第二次授权检查拒绝，`retrieval.rejected_by_recheck >= 1` |
| 目录索引与知识索引分离 | passed | `test_t077_rebuild.py::test_the_catalog_rebuild_never_touches_the_knowledge_index`：先插入真实 `knowledge_documents`/`knowledge_chunks`/`knowledge_index_versions` 行（1/1/1），记录计数 + chunk id 集合 + index state 的身份签名；真实 `catalog rebuild --all` 与真实 `catalog_index` maintenance 后签名逐字节不变；目录表只含该 catalog 版本。迁移/重建/查询都没有读写知识表（`application_catalog_entries` GIN 与 `knowledge_chunks` 无共享逻辑） |
| 索引重启可重建；截断投影→权威表+outbox 重建→搜索恢复；中断留下 epoch/错误台账而不半服务 | passed | `test_rebuild_restores_search_after_projection_truncation_and_consumes_outbox`：从未索引（fallback、`index_state=absent`）→ `rebuild --all`（epoch 1、expected/indexed=2、`outbox_consumed=2`、outbox `delivered_at` 非空且 `attempts=1`）→ 搜索 `source=index`；truncate 投影但保留 ready 台账 → 搜索因活计数不一致回到 fallback 且仍返回 2 项；再次 rebuild（epoch 2、outbox 0）→ 恢复 index。`test_a_stale_hash_entry...`：entry hash 改坏 → fallback 返回权威 manifest hash → rebuild 修复。`test_an_interrupted_rebuild_records_the_epoch_and_never_half_serves`：真实 psql 会话对 `application_versions` 持 `ACCESS EXCLUSIVE`，启动真实 `orchara-admin catalog rebuild --all`，轮询到已提交的 `rebuilding` 后 `TerminateProcess`；台账仍是 `rebuilding` 且 epoch/`rebuild_started_at` 已记录；搜索回退并返回全部 3 个可读版本；释放锁后完整 rebuild 恢复。`test_a_failed_rebuild...`：坏能力卡使 rebuild 退出 1，台账 `failed` + `last_error_code=catalog_source_invalid` + `last_error_at`，outbox 未投递，搜索回退且坏卡被复检拒绝；修复后 rebuild 清错并恢复 |
| 索引落后回退授权全文；第二次授权检查仍拒绝撤销项 | passed | `test_stale_index_falls_back_to_authorized_full_text_and_rechecks_authorization`：已建索引 1 个版本，再发布 2 个不重建 → 活计数 3≠1 → `source=fallback`、`stale=true`，三条都返回；撤销其中一个后仍因另一未索引版本保持 stale → fallback 返回剩余 2 条，`rejected_by_recheck>=1`，被撤销 purpose 不在响应中；随后 rebuild 切回 `source=index`。回退 SQL 刻意不在检索前过滤撤销（notes §7 / ROUTING:9 的顺序），拒绝完全由逐行权威复检承担，因此该断言可证伪 |
| ACL 在 SQL 内、排名/LIMIT 之前（无 post-filter）；cursor/limit/ordering 与 `listApplications` 契约形状不变 | passed | `test_search_returns_only_authorized_published_versions_before_ranking`：他人的“精确命中”版本不因排名/`limit=1` 挤掉调用者的行；`limit=2` + cursor 两页恰好覆盖 3 个 owner 可读版本、无重复/遗漏、rank 命中的两条在前；不可见查询返回空页；非法 cursor 400 `invalid_cursor`、limit 0/101 与 201 字符 q 422 `validation_failed`；`%`/`_` 查询是字面量；未过期 read grant 授予后被检索到、撤销 grant 后立即消失（同一投影，无 rebuild）；`GET /api/v1/applications` 仍严格是 `{items,next_cursor}` 且 item 恰为 `{id,name,active_version_id}` |
| 页面：用途卡/版本/检索 + Node 可执行纯函数 + 结构测试 | passed | `tests/f08/test_t077_pages.py`：真实编译 API 提供 `/catalog` 与 `/assets/catalog.js` 及结构 marker；Node 24 真实执行 `app.js`+`catalog.js`，断言路径编码、六字段卡投影、版本号未知、索引/回退措辞、空页/计数、提交跳转；无 `innerHTML`/localStorage/sessionStorage |
| 记录版本、输入/hash、命令、原始结果、环境 profile；stub/real 分报 | passed | 本报告 §3；profile `dev-stub`；本票无 stub 参与：发布版本由真实编译器产出，manifest/对象字节/HTTP/进程均为真实；输入/hash = 真实 manifest sha256 与投影 `source_manifest_sha256`，测试逐条断言 |

## 3. 门禁（原始输出）

环境：Windows PowerShell；Go `E:\orchara-toolchains\go`；PostgreSQL 16.4（`127.0.0.1:55432`，
user-space cluster）；profile `dev-stub`；Node 24；真实编译的 `orchara-{admin,api,maintenance}`。

```
$ gofmt -l .
（空）

$ go build ./...
build_exit=0

$ go vet ./...
vet_exit=0

$ go test ./...
（全部包 ok / no test files；test_exit=0）

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest

# 最终 merge commit 4dfcf37（integration/t068-t082 @ 0fcf8ec）之后：
$ python -m pytest tests/f08 -q --basetemp=E:\orchara-tmp\t077-final1
...................................                                      [100%]
35 passed in 522.07s (0:08:42)

$ python -m pytest tests/f08 -q --basetemp=E:\orchara-tmp\t077-final2
...................................                                      [100%]
35 passed in 498.01s (0:08:18)

$ python -m pytest tests/tooling tests/f02b/test_t068_migration.py tests/f02b/test_t068_pages.py \
    tests/f02a/test_t023_migration.py tests/f02a/test_t023_pages.py tests/f02a/test_t027_migration.py \
    -q --basetemp=E:\orchara-tmp\t077-neigh
........................................................                 [100%]
56 passed in 356.83s (0:05:56)
```

`tests/f08` 的 35 = T074 既有 19 + **T077 16**（迁移 3 + 搜索/ACL 3 + 重建/分离 5 +
handler 3 + 页面 2）。两次 back-to-back 都包含真实二进制构建、真实 `0045` 迁移、
真实 admin rebuild/maintenance 进程；不依赖固定短 sleep，等待均用 `wait_until` 轮询权威条件。
中断用例的进程终止是 `TerminateProcess`（Windows force-kill），不是信号；台账断言在锁释放前
读取，避免与后台清理竞争。

## 4. not_run / 本票外

- 浏览器 DOM 驱动页面（与 T023/T027/T033/T074 同一边界）：页面逻辑经 Node 真实执行，DOM 未驱动。
- 中文分词/检索相关性质量实验：未运行，也不在本票宣称；只实现 UTF-8 子串 `ILIKE` +
  `tsvector('simple')` 词项匹配，`ROUTING:56` 的数据验证归检索后续票（T064/T067）。
- 向量/ANN：pgvector 不可用，`embedding` 列故意不建；ANN 决策与实现归 T064/T067。
- 模型路由/候选选择/澄清/控制预算（T079）：本票只提供候选读取与卡片，不实现 router。
- 交接 UI（T082）、子运行（T078）、删除/清理（T071/T072）、发布/撤销端点（T068/T070）：
  均未修改；T068/T070 只获得可调用的增量接缝，本票未接其事务钩子。
- 全量 `pytest tests`（BRIEF 明令不运行，>60min）；全量 `tests/f02a`/`tests/f02b` 未整包运行，
  只跑了与迁移链/页面直接相邻的文件（见 §3）；T068 e2e 的 runner/sandbox 用例未重跑
  （本票未改其代码路径）。
- 未 push、未关闭 issue、未改 GitHub、未改 `docs/spec/**`/`contracts/**`。

## 5. 下一步接口（T079 / T082 / T068 / T070）

- `catalog.SearchAuthorized(ctx, principalID, q, cursor, limit, now)` 返回
  `[]CatalogHit{ApplicationID, ApplicationName, VersionID, VersionNo, ManifestSHA256,
  CapabilityCard}` + `CatalogRetrieval`；T079 的候选快照可直接写
  `request_routing_candidates`（`version_id`+card hash），但**执行前仍须**
  `catalog.ResolveRunnableVersion(Tx)`。
- `catalog.IndexVersionTx(ctx, tx, versionID)`：T068 发布事务/T070 撤销事务内同步增量；
  `catalog.EnqueueIndexVersionTx(ctx, tx, versionID)`：异步 `catalog_index` job（dedup
  `<version>:<manifest_sha256>`）。两接缝都可缺省：缺省时 search 继续用授权全文回退，
  不会误用陈旧行。
- `orchara-admin catalog rebuild|index|status`；台账表
  `platform.catalog_index_state`（epoch/state/`last_error_code`）是运维与测试的共同真值。
- 页面 `/catalog` + `assets/catalog.js` 的纯函数可被 T079 复用（候选展示/版本跳转）；
  `describeRetrieval` 的“索引/回退”措辞是 UX 诚实性边界，不要在 T079 删除。
- T082 可复用同一搜索响应展示处理者版本，但交接确认仍由 T082 自己的记录承担。

## 6. 决策记录

- ADR 0026 `docs/adr/0026-t077-catalog-search-projection.md`：非契约搜索端点/页面、
  投影与偏差（无 embedding 列、`application_id`/`ts`）、ACL+发布过滤先于排名、
  活计数陈旧判定、回退+逐行二次查权、重建台账/中断语义、增量接缝、与知识索引分离。
- 迁移偏差与 owning ticket 见 `0045` 头注释；orchestrator 预分配 0051，合入时需重编号
  （测试按 slug 断言，不 pin 数字）。
- 合并记录：`merge(f08)` `4dfcf37`（`integration/t068-t082` @ `0fcf8ec`）。
