# F09 T058 验收：用户修订或删除记忆后立即停止旧内容召回

- 任务：F09 / GitHub Issue #59（票 `T058`，规划 ID F09.02/F09.03）；分支
  `t/t058-memory-revision`（worktree `E:\orchara-worktrees\t058`）；父门禁 #53–#68
  集成批次（`integration/f13a-f15a-f09-f10`）；基线 `10c8637`，合并提交 `7190b2b`。
- 状态：**`passed`**（范围 = `getMemory`（强 ETag）+ `updateMemory`（必填 `If-Match`，
  428/412、新 revision/新正文 artifact/同事务 epoch）+ `deleteMemory`
  （202 `DeletionAccepted` + tombstone + `deletion_jobs` + epoch）+ `/memories`
  页面的冲突恢复与删除状态）。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP/CSRF +
  真实 sealed 对象字节 + Node 执行页面逻辑；无 mock、无跳过。
- 边界（如实）：物理副本清理、`delete_content` handler、删除作业的 claim/retry/completion
  属 **F07 删除治理**（本票只写 `pending` job 与 `remaining_scopes`）；`memory_embeddings`
  表仍属 **T063**（本集群无 pgvector，0009/0033/0034 记录同一边界），本票交付的是
  “旧 revision 永不当前”的权威事实（`revision` + `policy_epochs` + 删除 job 的
  embedding scope）；浏览器 DOM 驱动 `not_run`（页面纯函数由 Node 执行）；全量
  `pytest tests` 由 orchestrator 在合并后运行。

## 0. 工作记录（简短）

1. 读 BRIEF、`briefs/T058.md`、票面、`reports/f09/T054-memory-candidates.md`、
   `internal/memory/**`、迁移 `0033`、ADR `0009`、`tests/f09/**`、`tests/support/{stack,t054}.py`，
   以及 `internal/catalog/draft.go` + `internal/api/drafts.go` 的强 ETag/If-Match/412/428
   先例、`internal/api/apikeys.go`/`authz.go` 的个人数据与服务账号边界。
2. TDD 垂直切片（每片先红后绿，红阶段原始失败原因记录在下方）：
   - 迁移 0035（合并后编号）：`deletion_jobs` 契约列/键/权限/链连续/重复 migrate →
     4 用例（红：`relation "platform.deletion_jobs" does not exist`）；
   - `GET /api/v1/memories/{id}`：强 ETag、owner-only、他人/管理员/未知/畸形 404、
     服务账号 403 → 4 用例（红：`404 page not found`，无路由）；
   - `PATCH`：428 不写、412 带 `current_etag` 不写、成功=新 artifact+revision+epoch、
     旧 ETag 重试 412、并发同 ETag 恰一胜者、body 校验顺序、404/403 → 7 用例
     （红：`404 page not found`）；
   - `DELETE`：202 job、同事务 revision/epoch/tombstone、下一读取 404/列表空/召回查询空、
     重复删除同 job、并发删除一次 bump、跨应用 epoch 隔离、404/403 → 5 用例
     （红：`404 page not found`）；
   - 页面：HTML 结构 + Node 执行冲突恢复/删除状态纯函数 → 3 用例（红：缺少标记与 helper）。
3. 跑门禁（§4），写 ADR 0009 §11、本报告与 PROGRESS；按 feat/test/docs 提交；合并
   `integration/f13a-f15a-f09-f10`（T055 已占 0034 → 本票迁移重编号为 0035），
   解决 `tests/f09/test_t054_migration.py` 冲突（两侧都移除“0033 是链尾”的钉死断言，
   保留集成侧注释并补 T058 的 0035）；未 push、未关 issue、未改 `docs/spec/**`/`contracts/**`。

## 1. 交付内容

### 1.1 迁移 0035：（合并后编号）契约 `deletion_jobs` 最小切片

`db/migrations/0035_memory_revision_and_deletion.sql`（基线分支上曾为 `0034`，T055 先占
`0034_knowledge.sql`，按 BRIEF §5 由本票在合并时重编号）：

- 新增契约表 `platform.deletion_jobs`：`id/owner_id→principals/resource_type/resource_id/
  deletion_revision/state(pending|running|completed|failed)/remaining_scopes(jsonb array)/
  created_at/completed_at` + `UNIQUE(resource_type, resource_id, deletion_revision)`；
- **不给 `platform.memories` 加列**：强 ETag = 既有 `revision`（0033 起为 1，无需回填），
  tombstone = 契约 `deleted_at`，失效 = 既有 `policy_epochs(scope='memory:<owner>:<app>')`；
- 头部逐条写明偏离（本迁移无契约偏离）与 **F07 债务**：只写 `pending`、不注册
  `delete_content` handler、不物理删除对象/artifact 行；`remaining_scopes` 形状
  （`object`/`artifact`/`embedding`/`memory_row`）在此固定；
- 重复 `admin migrate` no-op；`0001..0035` 连续无缺口；`orchara_worker`/PUBLIC 零
  `platform` 权限。

### 1.2 `internal/memory`（在 T054 的存储上扩展，不新建第二套）

- `Get(ctx, ownerID, memoryID)`：owner 过滤与 id 在**同一条 SQL** 里（不是事后判断），
  `deleted_at IS NULL`，正文读 sealed 对象并校验 sha256；他人/管理员/未知/畸形/已删除
  全部同一 404。
- `Update(ctx, ownerID, UpdateInput)`：`FOR UPDATE` 行锁 → 缺失 `If-Match` 428 →
  `catalog.ParseETag` 不匹配/乱码 412（`Details{current_revision, current_etag}`）→
  `text` 1..4000 + `confirmed:true` 422 → 事务内封存新 `memory_body` artifact →
  `UPDATE ... WHERE revision = $claimed` CAS → bump 同一 `memory:<owner>:<app>` epoch →
  提交。并发同 ETag 恰一胜者；旧 ETag 永远 412，不 last-writer-wins。
- `Delete(ctx, ownerID, memoryID)`：行锁（含已删除行）→ 未删除则
  `deleted_at=now()`+`revision+1`+写 `deletion_jobs(pending, remaining_scopes)`+bump
  epoch，提交后才答 202；已删除则按 `(resource_type='memory', resource_id)` 回读同一 job
  返回，不再 bump。并发/重复删除因此幂等。
- `preconditionRequired()`/`preconditionFailed()` 复用草稿 PUT 的
  `precondition_required`/`precondition_failed` 词表；`memory.Error` 新增可选
  `Details`（HTTP 层与 `Field` 合并），错误信封仍严格契约形。

### 1.3 HTTP（3 个契约 operation）

- `GET /api/v1/memories/{memory_id}` → 200 `Memory` 精确四字段 + `ETag: "<revision>"`；
- `PATCH /api/v1/memories/{memory_id}` → 必填 `If-Match`；428 缺失、412 陈旧/弱/乱码/
  多值（`details.current_etag` + `current_revision`）、422 body；200 `Memory` + 新 ETag；
- `DELETE /api/v1/memories/{memory_id}` → 202 `DeletionAccepted` 精确三字段，`id` 是
  **deletion_job id**，`resource_id` 是记忆 id；
- 三个端点都在任何存储访问前拒绝服务凭据：403 `personal_scope_forbidden`（T054 边界
  延续；ADR 0009 §1/§11.9）；CSRF 由共享 `authorize` 强制。

### 1.4 页面

`apps/web/src/memories.html` + `assets/memories.js`：记忆卡显示 revision、可编辑正文
（`textarea` 的 `.value`，不拼 markup）、“保存修改”与“删除这条记忆”；保存前先
`GET /memories/{id}` 取强 ETag，body 精确 `{text, confirmed:true}`；412 显示“版本冲突”
与当前 revision/ETag，并出现“用最新版本重试”（采用 `details.current_etag`，只有显式点击
才再次写入）；428 提示先重读；删除后显示 deletion job 的真实状态（待处理/处理中/已完成/
失败）、明确“旧内容已停止召回”与“物理副本清理由删除治理异步执行”，不声称副本已删除。
全部 `textContent`，纯函数 `memoryPath/etagFromHeaders/updatePayload/updateHeaders/
preconditionPlan/conflictText/updateResultText/deletionStateText/describeDeletion/
deletionResultText/memoryDeletedText` 由 Node 执行断言。

### 1.5 配置

无新增配置项。

## 2. AC 核对

| AC | 结果 | 证据（真实栈，非 stub） |
|---|---|---|
| 1. 按用户和应用隔离查询 | proven | `test_memories_are_isolated_by_owner_and_application`：同一 owner 两个应用各自列表/详情；`test_the_detail_answers_the_same_404_for_foreign_unknown_and_deleted_rows`（他人/管理员/未知/畸形/匿名 401）；`test_a_foreign_or_unknown_memory_cannot_be_updated`；`test_delete_only_touches_the_owner_and_the_one_application_scope`（删除只 bump 本应用 epoch，另一应用记忆与 epoch 不动）；T054 的列表用例保持绿色 |
| 2. ETag 缺失或冲突不得覆盖 | proven | `test_a_missing_if_match_is_refused_and_never_overwrites`（428 + `details.header`，revision/正文/artifact 数不变）；`test_a_stale_or_garbled_etag_is_a_412_that_reports_the_current_etag`（`"0"`/`"2"`/`1`/`W/"1"`/`*`/列表/多值全部 412，`current_etag`=`"1"`，零写入）；`test_concurrent_updates_with_one_etag_have_exactly_one_winner`（恰一 200 + 一 412，最终 revision 2）；旧 ETag 重试 412（`test_a_matching_etag_...`） |
| 3. 删除后提升 revision/cache epoch 且下一读取拒绝 | proven（AC 原文）+ 说明 | `test_delete_answers_a_deletion_job_and_stops_old_recall_immediately`：响应后立即读 PG——`revision=2`、`deleted_at` 非空、`policy_epochs` epoch 前进、`deletion_jobs` 一行 `pending` 且 `remaining_scopes` 含 object/artifact/embedding/memory_row；`GET` 404、列表空、旧 ETag PATCH 404、按旧 revision 的召回形查询为空；重复删除同 job/同 revision/同 epoch。更新同样有旧 revision 不再当前的断言（`test_a_matching_etag_...`）。**说明**：`memory_embeddings` 行本身不存在（无 pgvector，T063 拥有），本票交付可被 T063/T067 直接消费的失效事实 + 删除 job 的 embedding scope，未声称物理 embedding 已删 |
| 4. 页面可看到冲突和删除状态 | proven | `test_the_memory_page_serves_the_revision_and_deletion_sections`、`test_the_memory_script_serves_the_t058_helpers`；`test_the_conflict_recovery_and_deletion_state_execute_under_node`（Node 执行：412 plan 采用 `current_etag`、428 提示、四种删除状态、`停止召回`/`删除治理`/`不声称` 文案、删除后文案不含“物理已删”） |
| 5. 真实端到端入口 + 失败/授权/重试边界 | proven | 正式入口：真实 `POST /memories`（cookie+CSRF）→ 真实 `GET/PATCH/DELETE` → 真实 202 job；鉴权：他人 404、管理员 404、服务账号 403（读/写/删三路径）、匿名 401；失败：404/422/428/412；重试/恢复：重复删除同 job、并发删除一次 bump、并发更新恰一胜者、页面 412→显式重试；无单层 mock（PG/二进制/HTTP/对象字节/Node 全部真实） |
| 6. 记录版本/hash/命令/原始结果/profile | proven（本报告 §4/§5） | 环境、输入 SHA-256 前 16 位、两条背靠背运行与各门禁原始尾巴在 §4/§5；stub/real 分报：本票**全部 real**（无模型/外部供应商调用，无 stub 结果需单列）；未运行项在 §6 |

## 3. 后续票 seam

| 票 | 本票已留的接口 |
|---|---|
| T063（#64） | `memories.revision` + `deleted_at` + `policy_epochs('memory:<owner>:<app>')` 是权威失效事实；embedding/召回条目必须以 `(memory_id, memory_revision)` 为键，服务前校验 `deleted_at IS NULL AND memory_revision = memories.revision`（或把 epoch 放进缓存键）；`deletion_jobs.remaining_scopes` 的 `embedding` scope 指出要清的派生副本 |
| T067（#67） | 同上缓存键/守卫；删除 job 的 `remaining_scopes` 与 `policy_epochs` 可共同作为“驱逐丢失也不命中”的版本；`Memory` 投影含 revision |
| F07（删除治理） | `platform.deletion_jobs` 已建（契约列/唯一键/四态 CHECK/零 worker 权限）；T058 只写 `pending`，`delete_content` handler/lease/retry/completion/物理清理由 F07 接入；更新遗留的旧 `purpose='memory_body'` artifact 可判为“不被任何 `memories.body_artifact_id` 引用”的垃圾（无需新列） |
| 其他读页 | `GET /memories/{id}` 是唯一强 ETag 读；`details.current_etag`/`current_revision` 是 412 的可恢复事实；428 的 `details.header` 指明缺失头 |

## 4. 门禁命令与原始输出（2026-11-04，合并后最终树 `7190b2b`）

环境：Windows 10 + PowerShell 7；`go version go1.27.3 windows/amd64`；`Python 3.12.2`；
`node v24.13.0`；PostgreSQL **16.4**（用户态集群 `127.0.0.1:55432`，
`python tools/pg_dev.py status` → `running: true, server_version: 16.4`）。

```text
$ python -m pytest tests/f09 -q          # 第 1 轮
47 passed in 308.18s (0:05:08)

$ python -m pytest tests/f09 -q          # 第 2 轮（背靠背，BRIEF §6.1）
47 passed in 330.67s (0:05:30)

$ python -m pytest tests/f09 -q          # 第 3 轮（最终树，主机同时有其他作业）
47 passed in 480.97s (0:08:00)

$ go build ./...
(no output, exit 0)

$ go vet ./...
(no output, exit 0)

$ go test ./...
(all packages ok; no FAIL; exit 0)

$ gofmt -l .
(no output)

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest   (exit 0)

$ python -m pytest tests/f01/test_t020_personal_boundary.py -q
2 passed in 12.17s

$ python -m pytest tests/tooling -q
21 passed in 4.67s

$ python -m pytest tests/f10 -q          # 合并进来的邻接套件
20 passed in 113.59s (0:01:53)
```

套内细分：`tests/f09` 47 = T054 25 + T058 22（`test_t058_migration.py` 4、
`test_t058_memory.py` 15、`test_t058_pages.py` 3）。测试无 sleep 竞态：并发用例用线程
barrier 后由 PG 行锁/CAS 判定结果；时间断言只比较“是否前进/是否为空”，不依赖亚秒窗口。

### 合并（BRIEF §7.6）

```text
$ git merge integration/f13a-f15a-f09-f10
合并提交 7190b2b（父 a856d2b = 本票页面切片；父 13eadb7 = 集成 tip）
冲突：product/tests/f09/test_t054_migration.py（两侧都删“0033 是链尾”的钉死断言 →
      保留集成侧注释并补 T058 的 0035；无内容丢失）
重编号：0034_memory_revision_and_deletion.sql → 0035_…（T055 已用 0034_knowledge.sql；
      链 0001..0035 连续，重复 migrate no-op）
合并后：pytest tests/f09 47 passed ×2、go build/vet/test、gofmt、sync_spec 全绿
```

## 5. 输入与版本

| 输入 | 版本 / SHA-256（前 16 位） |
|---|---|
| 基线 | `feat/f06-f04a-models-and-workflow` @ `7d0f442`；集成 tip `13eadb7` |
| `db/migrations/0035_memory_revision_and_deletion.sql` | `B2F48FFD81CCA663…` |
| `internal/memory/service.go` / `internal/memory/memories.go` | `5963B79E3EBFAA1E…` / `7C0DF7E8789C6DAF…` |
| `internal/api/memory.go` / `internal/api/server.go` | `69C7D975B5E99B58…` / `90F745FD8D180F7E…` |
| `apps/web/src/memories.html` / `assets/memories.js` | `7DDAB73278759B31…` / `591C6EA10EA0270C…` |
| `docs/adr/0009-f09-memory.md`（§11 增补） | `92770B887817F616…` |
| `tests/f09/test_t058_{memory,migration,pages}.py` | `4AE8A9D79A709A55…` / `B752D38CA8565220…` / `C6FA14F0AC63E47C…` |
| `tests/support/t058.py`（+ `t054.py` 增加通用 bearer `request`） | `D92B3B3DE4FC801B…` |

真实/stub 分报：**全部 real**（真实 PG、真实编译二进制、真实 cookie+CSRF、真实 sealed
对象字节、真实 Node）；本票没有调用模型或外部供应商，因此没有 stub 结果需要单列。

## 6. `not_run` / 债务（如实）

| 项 | 原因 / owning ticket |
|---|---|
| 物理副本清理（对象字节、artifact 行、embedding 行） | **F07**；T058 只写 `pending` job + `remaining_scopes`，并在页面明说未清理 |
| `delete_content` handler、job claim/lease/retry/completion/失败态 | **F07**；`deletion_jobs` 是最小切片，state 恒 `pending` |
| `memory_embeddings` 行的失效/删除 | 表不存在（本集群无 pgvector，0009/0033/0034 记录），**T063** 拥有；本票交付 revision/epoch 权威失效事实与 job 的 embedding scope |
| 更新遗留的旧 `memory_body` artifact | **F07** 物理清理；判据记录在迁移头/ADR §11.5 |
| 运行时召回（上下文内消费记忆） | **T063**；本票没有接入 `internal/context.MemorySource` |
| 检索缓存驱逐/命中守卫 | **T067**；本票只提供 epoch/revision |
| 浏览器 DOM 驱动（真实点击/渲染截图） | 无浏览器驱动；页面纯函数由 Node 执行，与 T023/T027/T029/T039/T050/T054 同一边界 |
| 全量 `pytest tests`（约 67 分钟） | BRIEF §3 明令不跑；由 orchestrator 在合并后运行 |
| 记忆共享/授权（`policy.ResourceTypes` 扩展） | 不扩展；owner-only + 管理员 404 的债务延续 T054 |

## 7. 下一票接口

- 表：`platform.deletion_jobs`（`owner_id→principals`、四态 CHECK、
  `remaining_scopes` array、`UNIQUE(resource_type, resource_id, deletion_revision)`）；
  `platform.memories`（`revision` 为强 ETag/CAS，`deleted_at` 为 tombstone，未加列）；
- Go：`memory.Service.Get/Update/Delete`、`memory.UpdateInput`、`memory.DeletionJob`、
  `memory.Error.Details`、`memory.Code{PreconditionRequired,PreconditionFailed}`；
- HTTP：`GET/PATCH/DELETE /api/v1/memories/{memory_id}`；`ETag`/`If-Match`；
  412 `details.{current_etag,current_revision}`、428 `details.header`；
- 失效键：`platform.policy_epochs(scope='memory:<owner>:<application>')`（小写）；
- 页面：`/memories` 的编辑/删除交互与 `describeDeletion/preconditionPlan` 纯函数。

## 8. ADR

`product/docs/adr/0009-f09-memory.md`（T058 增补 §11：强 ETag=revision 与严格 ETag 解析、
写序 404→428→412→422→CAS、更新=新 artifact+revision+epoch、删除=同步失效+异步清理、
`deletion_jobs` 最小切片与 F07 债务、召回/检索缓存 seam、页面文案边界、服务账号与
owner-only 不变）。未新建 ADR 文件，避免与并发分支的 ADR 编号冲突；`tools/sync_spec.py
--check` 保持 no drift。
