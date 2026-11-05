# F11 T071 验收：异步清理对象和索引并可观察失败重试

- 任务：F11 / GitHub Issue #72（票 `T071`，规划 ID F11.02）；分支
  `t/t071-async-purge`（worktree `E:\orchara-worktrees\t071`）；父批次
  `integration/t068-t082`；基线 `afeba90`；收尾合并 `integration/t068-t082`
  **`6952e25`**（合并时集成 tip 已含 T074/T075；唯一冲突 `internal/api/server.go`
  保留双方：T074 conversation Target + T071 `PurgeEnqueue`）。
- 状态：**`passed`**（T071 的三个 scope：`object_store`、`search_index`、
  `retrieval_cache` 全部真实幂等清理；失败保持禁读、`GET /deletions/{id}` 可查
  `failed` + 未清 scope、真实重试后完成；sealed 孤儿 sweep 有真实引用回归；无复活
  读路径。T072 的四个 scope 诚实保留）。
- 边界（如实）：本票**只消费** T069 的 tombstone-first 作业与它记录的源版本；
  checkpoint/摘要/临时沙箱/第三方副本/备份删除账本属 **T072**；诊断授权属 T073；
  无复活全套属 T076。T074 合并进集成树后，conversation 删除作业的 `object_store`
  （请求/附件/run 输出/交接摘要的 sealed 对象）由本引擎按同一通用规则清理；
  conversation 没有独立 chunk/vector 索引或 retrieval-cache 表，这两个 scope 对它是
  诚实的空集。T071 不新增公开 API：清理是 maintenance/worker 内部行为，用户可见面
  沿用 T069 的 `GET /deletions/{id}` 与既有 `GET /background-jobs/{id}`。
- 真实依赖：PostgreSQL **16.4**（`127.0.0.1:55432`）、编译后的
  `cmd/api`/`cmd/admin`/`cmd/maintenance`、真实内部 mTLS、真实 sealed 对象字节
  （文件系统）、真实 HTTP/CSRF、真实 `maintenance run --once --kinds
  delete_content` 进程与真实 `maintenance sweep`。失败注入是**真实文件系统共享冲突**
  （测试进程持有 Windows 文件句柄，`os.Remove` 得到 sharing violation、字节仍在盘），
  不是被测对象的 mock。

## 0. 工作记录（简短）

1. 读 BRIEF、`issues-69-83.md` #72、`notes/f11-deletion.md`（§3.6/§3.7/§4/§5/§8/§9）、
   T069 代码与报告（`internal/deletion`、三个资源 adapter、`api/deletions.go`、
   `0042`）、`internal/background/{job,complete,claim,sweep,handler}.go` + handlers、
   `cmd/maintenance/{run,sweep}.go`、`internal/objectstore`、
   `internal/knowledge/{cache_store,readable_chunk}.go`、`0034/0037/0039` schema、
   `tests/support/{stack,f00b,t055,t058,t067,t069}.py`。
2. TDD 垂直切片（红→绿；红阶段失败均在报告记录：`0047/0044` 迁移不存在、
   `delete_content` job 未 enqueue、GIN 索引不存在）：
   - 事务内 enqueue + handler + guard + retry：真实 DELETE 后 job 立即可见、
     owner 可 `GET /background-jobs/{id}`、重复 DELETE 仍一个 purge 单元；
   - artifact：sealed 字节消失、`artifacts.purged_at` 写入、`state='deleted'`、
     三个 scope 从 `remaining_scopes` 移到 `completed_scopes`、T072 四项保留、
     handler+guard 重放 no-op；
   - memory：当前 body + 被 update 取代的历史 body（T058 明确遗留的 F07 清理债）
     均物理清理；memory_embeddings 未建 → `search_index` 诚实清空而非失败；
   - knowledge document：chunk/vector 行、index version 行、active 指针、
     retrieval cache 行、source+chunk-body sealed 字节全部消失；
     删除后检索/引用/detail 继续拒绝；
   - 失败/重试：真实文件句柄锁 → `transient_storage_io` retryable 失败，
     `GET /deletions` 报 `failed` 且 `object_store` 未清、读端点 404、字节仍在盘；
     释放句柄后同一 job 第二次 attempt 成功，deletion job 回到 `pending`（T072 四项）；
   - 合法共享引用：删除 document A 后、purge 前把同一 source artifact 重新 ingest 到
     另一个 KB（真实 live document B）→ source 字节**不被删除**，purge 记
     `object_shared_reference`（business，不重试），B 仍可检索；
   - orphan sweep：旧的无引用 sealed 删除；年轻无引用 sealed 因 10 分钟 grace 保留；
     被 artifact row / knowledge chunk / memory body / run_artifact 引用的旧 sealed
     全部保留；dry-run 同集合；重复 sweep no-op；
   - 迁移/索引/权限边界；
   - conversation（T074 合并后）：真实 conversation 删除的 sealed request 对象由同一
     purge 清理，三个 T071 scope 进 completed、T072 四项保留；一个无 purge adapter 的
     真实 deletion job（SQL 种子的 `unknown_resource`，由真实 `admin` enqueue）保持全部
     scope 未清并记 `purge_unsupported_target`（fail-safe，不把空派生集当“已清”）。
3. 迁移编号说明（**请 merger 重编号**）：orchestrator 提示用 `0047`，但本分支基线
   `afeba90` 的链是 `0001..0043`（T068 占 0043），且共享断言
   `tests/support/stack.py:migration_chain` 要求**版本从 0001 连续无空洞**；0044–0046
   是并行票的号段，本分支若直接用 0047 会让**所有** migration 测试在本分支失败。
   按 BRIEF §5「号码全局、冲突由 merger 重编号」的规则，本票先取 `0044`；收尾合并
   集成 tip 时 T074 已占 `0044_conversations_and_requests.sql`，故在合并提交
   `6952e25` 中把本票迁移重编号为 **`0045_deletion_purge_paths.sql`**（按名字断言，
   不按号码）。合并后全局预期空位为 `0047`（T072/T073 占 0045/0046），
   **merger 需再次重编号**。
4. 行为变化：`cmd/maintenance sweep` 现在会删除**可证明无引用且足够旧**的 sealed 对象
   （此前只计数）；`tests/f00b/test_t026_sweep.py` 的注释已同步更新（断言值不变：它
   写的是刚创建的 sealed 孤儿，仍落在 10 分钟 grace 内，`sealed_deferred` 仍为 1）。
5. 门禁与合并见 §4；未 push、未关 issue、未改 `docs/spec/**`/`contracts/**`、
   未新增 ADR（无契约/公开面偏离；内部语义记录在 `reports/f11` 与本文件）。

## 1. 交付内容

### 1.1 `delete_content` 作业：同一删除事务内 enqueue

- `internal/deletion/deletion.go` 新增 `PurgeEnqueue` 接缝，`Service.Delete` 在
  **tombstone + deletion job 的同一事务**里调用它（`ON CONFLICT` 幂等由
  `background_jobs(kind, dedup_key)` 保证）：
  `kind=delete_content`、`target_id=deletion_job_id`、`owner_id=删除请求主体`、
  `dedup_key=deletion:<deletion_job_id>`（`deletion.PurgeDedupKey`）。重复/并发 DELETE
  复用同一 job；enqueue 失败则删除事务回滚（不会出现“202 了却没有持久清理单元”）。
- 接缝为注入式而非 import：`internal/deletion` 不依赖 `internal/background`
  （反向 guard 依赖它），由 `internal/api/server.go` 装配真实 `background.EnqueueTx`。
- `memory.Service.Delete`（mem0 证据工具用的进程内兼容入口）保持无 enqueue：
  仍会写 tombstone/job，但没有 purge 单元；文档中已注明。

### 1.2 handler + registry + retry table + completion guard

- `internal/background/handlers/delete_content.go`：读取该 deletion job 的 scope 描述
  （与 `artifact_finalize` 同类、已文档化的 interim DB read），只删**字节**：
  对每个非共享命名 key 调 `objectstore.Store.Delete`（缺对象即成功，天然幂等），
  不写任何业务行；有界超时 `Settings.DeleteContentTimeout`（默认
  `background.DefaultDeleteContentTimeout = 2m`，超时 → 可重试
  `delete_timeout`）。
- Registry 增加 `KindDeleteContent: DeleteContent`；retry table 增加
  `delete_content: {transient_storage_io, transient_internal, lease_expired,
  delete_timeout}`；未知/共享/不支持的码按 fail-safe 归为 business。
- `internal/background/delete_content.go`：`guardDeleteContentCompletion` 注册进
  `completionGuards`。它在 Complete 的同一事务中调用
  `deletion.ApplyPurgeOutcome`（见 1.3），把 handler 的提议改为：
  全部清空 → `succeeded`；T071 三个 scope 清空但 T072 四项仍欠 → `succeeded`
  （删除作业保持 `pending`，不谎报 completed）；有 scope 未清 → `failed(code)`。
  **“对象仍在”绝不是 guard error**（F11 notes §3.6：guard error 会终止
  `maintenance run`），而是 retryable `transient_storage_io`；共享引用是
  non-retryable `object_shared_reference`。
- 失败语义有真实用例：第一次 attempt 失败时 maintenance 进程 **exit 0**，background
  job 回 `ready` + `attempt_count=1`，第二次 attempt 成功（§2 AC2）。
- 配置：`ORCHARA_MAINTENANCE_DELETE_CONTENT_TIMEOUT` / `--delete-content-timeout`
  （默认 2m，上限 10m，非正值启动即拒绝）；`cmd/maintenance/run.go` 传给
  `HandlerSettings.DeleteContentTimeout`。

### 1.3 `internal/deletion/purge.go`：唯一的 scope 清理/证据引擎

- `LoadPurgeTargets`（handler 用 `work.DB`，guard 用事务）从 **deletion job 自身的
  `scope_details` + 与 job 关联的已 tombstone 行** 推导目标集合：
  - `object_store`：descriptor 里的 artifact id / object key（兼容 T069 的
    组合项与 artifact/object 分离项），加上已 tombstone 文档的 chunk-body artifact；
    memory 额外加上“owner 的 `purpose='memory_body'` 且没有任何 live memory 指向”的
    artifact（这正是被 update 取代、T058 明确留给 F07 清理的历史 body；live memory 的
    body 永远不可能进入该集合，candidate body 是另一个 purpose）。
  - `search_index`：job 命名/派生的 document ids（全部 revision 的 chunk 行与
    index version 行）与 chunk ids；`memory_embeddings` 不存在时诚实视为空。
  - `retrieval_cache`：job 的 document ids / chunk ids（cache `items` 数组包含即删）。
  - **绝不按“未被引用”猜删除**：每个 key 都来自 job descriptor 或上面可证明的派生。
- **合法引用保护**（本票额外的安全边界）：命名 artifact 若仍被 live knowledge
  document（`deleted_at IS NULL`）、live document 的 chunk body、live memory body 或
  pending memory candidate 引用，则不删该对象、不 mark 该 artifact 行，并把
  `object_store` scope 记 `shared_reference`（business，状态 `failed`）。真实场景用例：
  删除 document 后、purge 前把同一 source artifact 重新 ingest 到另一个 KB（§2 AC3）。
- `ApplyPurgeOutcome`（guard 事务，`SELECT ... FOR UPDATE` 锁 deletion job）：
  1. 对 `object_store` 用 `store.Stat` 逐个验证非共享 key 已不存在；`ErrNotExist`
     视为清除；其他 store 错误视为可重试；然后只对**验证已消失**的 artifact 行
     写 `purged_at=now(), state='deleted', deleted_at=COALESCE(deleted_at,now())`
     （`purged_at` 是 0009 起一直没人写的列，T071 是第一个写者）。
  2. `search_index`：先清 document 的 `active_index_id/active_revision`、解绑
     `background_jobs.expected_index_version_id` FK，再删 chunk 行（向量就是 chunk
     行上的 `double precision[]`）与 index version 行；按 document/chunk 命名。
  3. `retrieval_cache`：按 owner + 文档/链命中项删 cache 行（GIN 索引见 1.5），
     再数一次确认 0。
  4. 同一事务把清的 scope 追加到 `completed_scopes`、从 `remaining_scopes` 移除
     （保持稳定排序），`attempt_count+1`、`started_at=COALESCE(started_at,now())`；
     全部清空 → `state=completed` + `completed_at`；有失败 → `state=failed` +
     `error_code`（与 background job 同一套安全码）；只剩 T072 四项 → `pending`、
     `error_code=NULL`。
- 幂等：scope 只在“验证已清”后写；对象删除对缺失 key 成功；cache/chunk 删除是
  DELETE；artifact `purged_at` 只补 NULL；deletion job `state=completed` 时重放直接
  返回 done、不再改行。真实重放用例（同 dedup key 不产生第二个 job；不同 dedup key
  的 operator replay 真实跑 handler+guard，计数/`purged_at` 不变）见 §2 AC1。
- 不支持的资源类型（没有 purge adapter 的未来资源）→ `purge_unsupported_target`，
  不把 scope 误标清除。F08/T074 合并后 conversation 已被支持：其 `object_store`
  描述（请求输入、附件、run 输出、交接摘要、interaction 提案的 sealed 对象）走同一
  命名/引用/验证规则；conversation 没有 chunk/vector 索引与 retrieval-cache 表
  （消息正文就是 sealed artifact，request/interaction 行是已 tombstone 的元数据），
  因此 `search_index`/`retrieval_cache` 对它是可证明的空集；checkpoint/temp/
  third-party/backup 四项仍留给 T072。

### 1.4 sealed orphan sweep（AC3）

`cmd/maintenance/sweep.go` 扩展（不新增第二个 sweeper）：

- 五类引用显式检查：`platform.artifacts.object_key`、`run_artifacts`、
  `knowledge_chunks.body_artifact_id`、`memories.body_artifact_id`、
  `memory_candidates.body_artifact_id`（后四类 join 到 artifact row；写成一个
  UNION，规则可执行而非隐含）。**任何 artifact row 仍命名 key（含 tombstone 行）都算
  引用**：被删 artifact 的字节由该 deletion job 的 purge 负责，sweep 不与它竞争。
- 删除条件：无引用 **且** `age >= max(--orphan-age, 10m sealed-write grace)`。
  grace 是缺失的 writer/deletion 共享锁的替代：sealed 对象先写盘、artifact row 后
  commit，10 分钟窗口把“快照里没 row”变成稳定观察；即使 `--orphan-age 0s` 也不能
  绕过 grace。unreferenced-but-young 计 `sealed_deferred`（不删）。
- `--dry-run` 报告同一集合；重复 sweep no-op；summary 新增 `sealed_deleted`。
- 回归：被 artifact/run/chunk/memory 引用的旧 sealed 全部存活；年轻无引用者存活；
  旧的无引用 sealed 与旧 staging 孤儿被删；真实 mtime 由 `os.utime` 设置。

### 1.5 迁移 `0045_deletion_purge_paths.sql`（合并时由 0044 重编号）

- 只加一条 `CREATE INDEX IF NOT EXISTS knowledge_retrieval_cache_items ON
  platform.knowledge_retrieval_cache USING gin (items jsonb_ops)`：purge 用 JSONB
  数组包含（`items @> '[{"document_id":...}]'`）找“可能返回被删正文”的缓存项，
  0037 明确把“按资源删 cache 行”留给删除治理票；`jsonb_ops`（不是 path_ops）是
  因为需要数组包含。无新表、无契约列改动；`artifacts.purged_at` 是 0009 既有列。
- 幂等、链连续、`orchara_worker`/PUBLIC 零权限；名字断言
  `_deletion_purge_paths.sql`，merger 重编号不误配。

### 1.6 行为变化

- `delete_content` 从“whitelist 里永远 ready 的 kind”变成有 handler/guard/retry 的
  kind；DELETE API 现在在同一事务写 purge job（T069 的删除测试不跑该 kind，不受影响）。
- `maintenance sweep` 默认会删旧的无引用 sealed 对象（±10m grace）；F00B/T026 的
  sweep 测试注释同步更新、断言保持（其 sealed 孤儿是新建的，仍 deferred）。
- `platform.artifacts.purged_at` 从死列变为 T071 写者。

## 2. AC 核对

| AC | 结果 | 证据（真实栈） |
|---|---|---|
| 1. 对象/chunk/vector/cache 按 scope 幂等清理 | **proven** | artifact：`test_artifact_purge_clears_object_store_and_is_idempotent`（DELETE 事务内 job 已存在、owner 可读 `GET /background-jobs`、重复 DELETE 同 job；purge 后 sealed 文件消失、`purged_at`/`deleted` 写入、`remaining_scopes=[T072 四项]`、`completed_scopes=[object_store,retrieval_cache,search_index]`、`attempt_count=1`；第二次 drain claimed=0；另一真实 purge job（replay）真实跑 handler+guard 但 `purged_at`/计数不变）。memory：`test_memory_purge_clears_the_body_and_every_superseded_revision`（当前 body + update 取代的历史 body 都消失、artifact 行 purged/deleted、memory tombstone 行保留、检索/detail 继续拒绝）。document：`test_document_purge_removes_chunks_vectors_versions_and_cache_rows`（384 维 embedding 的 chunk 行、index version 行、active 指针、`knowledge_retrieval_cache` 行、source+chunk-body sealed 字节全部消失；cache 行在 DELETE 后、purge 前仍真实存在，证明是 purge 清的；之后 detail/citation/list/retrieval 全拒绝、cache hit=false）。conversation（T074 合并后）：`test_conversation_purge_and_unknown_resource_fail_safe`（真实 conversation/request 的 sealed request 字节被清理、三个 T071 scope 进 completed、T072 四项保留、会话/消息读仍 404；无 adapter 的未知资源类型由真实 admin enqueue + 真实 purge 保持全部 scope 并记 `purge_unsupported_target`） |
| 2. 失败保持禁读并可查询 remaining scopes + 重试完成 | **proven** | `test_a_real_store_failure_keeps_the_content_unreadable_and_a_retry_finishes`：测试进程 `open()` 持有 sealed 文件句柄（Windows sharing violation，字节仍在盘）→ attempt1 `retried`/`transient_storage_io`、background job `ready` attempt=1、`GET /deletions/{id}` 200 `failed` + `remaining_scopes=sorted([object_store]+T072 四项)` + `search_index/retrieval_cache` 已进 completed、artifact 三个读端点 404 且字节仍可读；释放句柄 → `wait_retry_available` → attempt2 `succeeded`，字节消失、deletion job `pending`（T072 四项）、`attempt_count=2`、`error_code=NULL`；读完仍 404（purge 级无复活）。`--delete-content-timeout -1s` 被真实 CLI 以 exit 2 拒绝；`--delete-content-timeout 2m` 真实通过 |
| 3. 暂存孤儿清理不误删合法引用 | **proven** | `test_the_sweep_deletes_only_provably_unreferenced_sealed_orphans`：真实 ingested document 的 source+chunk-body、真实 memory body、真实 ready artifact、真实 tombstone artifact 的 sealed 对象（mtime 2h）全部存活；run_artifact/chunk/memory 引用经真实行存在；旧无引用 sealed 与旧 staging 孤儿被删；新写 sealed 孤儿（落在 10m grace）存活并计 `sealed_deferred`；dry-run 同集合且零删除；第二次 sweep 零删除。purge 侧额外回归：`test_a_legitimately_shared_source_artifact_is_never_deleted`（live document B 共享 source artifact → 字节不被删、B 仍可检索、job `failed` + `object_shared_reference` + background job 同码 business） |
| 4. 端到端入口 + 失败/授权/重试边界，不以单层 mock 成功代替 | **proven（T071 范围）** | 真实 HTTP DELETE → 真实 202/`GET /deletions`；真实编译 `cmd/maintenance run --once --kinds delete_content`；真实后台 job/lease/retry/completion；真实内部 mTLS（maintenance 进程）；真实 sealed 字节。授权沿用 T069：删除入口 owner-only+CSRF，purge 无新公开入口；`GET /background-jobs/{id}` owner 可读（失败可观察），无新增高基数/正文面。失败注入是真实文件句柄/真实 CLI 拒绝，不是 mock |
| 5. 记录版本/输入/hash/命令/原始结果/profile | **proven** | 本报告 §4/§5：环境、原始输出尾巴、迁移/核心文件 SHA-256 前缀；stub/real：本票全部 real，无 stub；not_run 列 §6 |

## 3. 下一票接口（T072 / T076）

1. **作业行状态是唯一进度权威**：T071 成功后 deletion job `state=pending`、
   `remaining_scopes` 只剩 `backup_catalog|checkpoints|sandbox_temp|third_party_history`
   （排序稳定）、`completed_scopes=[object_store,retrieval_cache,search_index]`、
   `attempt_count` 已+1、`started_at` 置位、`error_code=NULL`。T072 必须在**同一
   `remaining_scopes/completed_scopes`** 上继续，全部清空才写 `completed`+
   `completed_at`；不要把 T071 的 scope 加回。conversation 作业同理（T071 清
   object_store + 两个空 scope，checkpoint/temp/third-party/backup 留给 T072）。
2. **一个 purge 单元**：`background.KindDeleteContent` +
   `deletion.PurgeDedupKey(deletionJobID)` 是每 deletion job 的唯一清理单元；
   T072 的 checkpoint/temp/third-party/backup 清理应扩展
   `deletion.ApplyPurgeOutcome` 的 scope switch（或在同一 guard 内扩展），不要新
   kind、不要第二个 dedup key。
3. **失败码词表**：`deletion.PurgeFailure` + `ErrorCode()` 是 deletion_jobs.
   error_code 与 background job error_code 的同一拼写；新增清理失败要同时进
   `internal/background` 的 `retryableByKind[KindDeleteContent]` 才能重试
   （当前 retryable：transient_storage_io / transient_internal / lease_expired /
   delete_timeout；business：object_shared_reference / purge_unsupported_target）。
   `internal/background/delete_content_test.go` 钉住映射。
4. **`artifacts.purged_at` 已启用**：T071 只对“验证字节已消失”的命名 artifact 写；
   T072 不要重置/覆盖。`state='deleted'`+`deleted_at` 仍是读路径权威。
5. **`ORCHARA_MAINTENANCE_DELETE_CONTENT_TIMEOUT` / `--delete-content-timeout`**、
   `HandlerSettings.DeleteContentTimeout` 是清理尝试的有界窗口；T072 的 checkpoint
   清理也要有界，不要引入无界等待。
6. **`maintenance sweep` 已扩展**：T072 的 `sweep --deletions`（账本/过期删除作业）
   应继续扩展同一命令；sealed 删除规则是“无引用 + max(age, 10m grace)”。
   T071 的 sealed 清理不处理“被 tombstone artifact 命名但 purge 未跑”的字节，
   那是 deletion job 的职责，sweep 不抢。
7. **T076 注意**：T071 的 purge 是“scope 清一次”；若 T072 的 restore/replay 把字节
   放回，**同一 deletion job 的 `object_store` 已不在 remaining，重跑 handler 不会
   再删它**。无复活必须靠 T069 的 tombstone 读谓词（本票已证明 purge 后读仍拒绝）+
   T072 的 ledger replay 重新 tombstone/purge；T076 的恢复场景要按此接口写，不能
   假设“再跑一次 delete_content 就会重新清 scope”。
8. **共享引用语义**：`object_shared_reference`（business，不自动重试）表示 job 命名
   的对象仍被 live 资源合法引用；要恢复清理需等引用消失后**显式重放**一个 purge
   单元（当前没有自动重排；T072 的 ledger/replay 或运维重排可承接）。

## 4. 门禁命令与原始输出

环境：Windows 10 + PowerShell 7；`go version go1.27.3 windows/amd64`；
Python 3.12.2；Node v24.13.0；PostgreSQL **16.4**（`tools/pg_dev.py status` →
running, port 55432）。

### 4.1 本票套件（`--basetemp` 隔离，最终合并树背靠背两轮）

```text
$ python -m pytest tests/f11 -q --basetemp=E:\orchara-tmp\t071-run6
................................                                         [100%]
32 passed in 369.96s (0:06:09)

$ python -m pytest tests/f11 -q --basetemp=E:\orchara-tmp\t071-run7
................................                                         [100%]
32 passed in 345.96s (0:05:45)
```

套内细分：T069 既有 22 + T071 新增 10（`test_t071_migration.py` 3、
`test_t071_purge.py` 3、`test_t071_failure.py` 2、`test_t071_orphans.py` 1、
`test_t071_conversation.py` 1）。合并前同一 T071 代码树（无 conversation 用例）也跑过
31 passed 两轮（`t071-run3` 271.25s / `t071-run4` 291.49s）与 31 passed 两轮
（`t071-run1` 317.85s / `t071-run2` 257.03s）；合并集成 tip 后的首轮为
`t071-run5` 31 passed（391.92s，当时 conversation 用例尚未加入）。

红阶段（TDD 记录）：迁移测试 `assert len(versions) == 1` 失败（无迁移）、
GIN 索引 `[]`、artifact purge 在 `delete_content_job` 处 `AssertionError: []`
（T069 未 enqueue）；随后逐切片转绿。

### 4.2 邻接套件（改动可破坏的；最终合并树）

```text
$ python -m pytest tests/f00b/test_t026_sweep.py tests/f00b/test_t026_migration.py \
    tests/f10/test_t067_cache.py tests/f10/test_t067_migration.py tests/tooling -q \
    --basetemp=E:\orchara-tmp\t071-neigh1
.........................................                                [100%]
41 passed in 203.13s (0:03:23)

$ python -m pytest tests/f05/test_t028_authz.py tests/f05/test_t028_migration.py \
    tests/f05/test_t028_pages.py tests/f05/test_t028_quarantine.py tests/f05/test_t028_race.py \
    tests/f05/test_t028_upload_flow.py tests/f05/test_t033_endpoints.py \
    tests/f05/test_t033_migration.py tests/f05/test_t033_pages.py \
    tests/f05/test_t030_tool_run_migration.py tests/f05/test_t030_tool_run_flow.py -q \
    --basetemp=E:\orchara-tmp\t071-neigh3
.........................................................                [100%]
57 passed in 625.28s (0:10:25)
```

诚实说明（**集成树既有回归，非 T071 行为**）：第一次合并树运行（`071-neigh2`）为
56 passed / 1 failed：`tests/f05/test_t028_migration.py::test_the_migration_chain_ends_at_0015`
断言 `conversations`/`requests` 表“不存在”，而 T074 的
`0044_conversations_and_requests.sql` 正是创建这两张表的票（T074 的 gate 没跑 f05）。
本分支做了**测试-only 兼容修复**（同一 change 更新受影响断言）：保留 0015 链位置与
`artifact_admissions` 断言，把“0015 不得夹带删除模型”收窄为仍成立的
`deletion_scopes` 不存在（T069 选择 job-row JSON 列，未建该表），并在注释中写明
conversations/requests 属 T074 的 0044。修复后该文件 4 passed，完整 f05 子集
`071-neigh3` **57 passed**（625.28s）。T071 没有改动 f05 的产品代码。

### 4.3 Go / 格式 / 契约

```text
$ go build ./...
BUILD_EXIT=0

$ go vet ./...
VET_EXIT=0

$ go test ./...
（全部包 ok / no test files，0 FAIL；TEST_EXIT=0；含新增
 internal/background: TestPurgeFailureCodesAreTheControllerVocabulary）

$ gofmt -l .
（无输出）

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest   (exit 0)
```

### 4.4 合并（BRIEF §7.6）

```text
$ git merge integration/t068-t082
（集成 tip 已从 afeba90 前进到 57d90df，含 T074/T075）
Auto-merging product/internal/api/server.go
CONFLICT (content): Merge conflict in product/internal/api/server.go
Automatic merge failed; fix conflicts and then commit the result.

# 解决：server.go 同时保留 T074 的 conversation Target 注册与 T071 的 PurgeEnqueue；
# 合并提交 6952e25。T074 已占 0044_conversations_and_requests.sql，故本票迁移
# 由 0044 重编号为 0045_deletion_purge_paths.sql（按名字断言，无代码依赖号码）。
$ git log --oneline -1
6952e25 Merge branch 'integration/t068-t082' into t/t071-async-purge
```

合并后重跑：`pytest tests/f11` **32 passed ×2**（§4.1 run6/run7）；`go build/vet/test`、
`gofmt -l`、`sync_spec --check` 全绿（§4.3）。工作树在最终 docs 提交后 `git status
--short` 为空。

## 5. 输入与版本

| 输入 | 版本 / SHA-256（前 16 位） |
|---|---|
| 基线 | `integration/t068-t082` @ `afeba90` |
| 收尾合并 | `6952e25`（merge `integration/t068-t082` @ `57d90df`，含 T074/T075） |
| `db/migrations/0045_deletion_purge_paths.sql`（合并时由 0044 重编号；merger 再重编号） | `CEAD1505DACE006B…` |
| `internal/deletion/purge.go` | `4B2C76CDAC9A43E2…` |
| `internal/background/delete_content.go` | `7542DB4B7F4A7777…` |
| `internal/background/handlers/delete_content.go` | `8DB977DDA9AE1A48…` |
| `cmd/maintenance/sweep.go` | `F54ABB8F31340E57…` |
| `tests/support/t071.py` | `0AD26EE1DA76E8FD…` |
| PostgreSQL | 16.4（用户态 `127.0.0.1:55432`） |
| Go / Python / Node | go1.27.3 windows/amd64 / 3.12.2 / v24.13.0 |
| 测试输入 | 每测试唯一 marker 正文/字节；真实 sealed 文件与真实句柄失败注入；无凭据进仓库 |

## 6. `partial` / `not_run` / 已知边界（如实）

| 项 | 状态 | 原因 / 承接 |
|---|---|---|
| `delete_content` 的 `delete_timeout` 实际触发 | **not_run** | 旗标/环境配置与拒绝负值已真实执行；“卡死 store 触发超时”需要不可控的 FS 挂起，未构造；有界超时的 lease/sweep 兜底属既有 T022 机制 |
| 3 次 attempt 耗尽后的终态路径（attempt-limited） | **not_run** | 本票选择“真实 store 失败 + 同 job 重试成功”证明 AC2；通用 retry 上限由 T022 既有测试覆盖，本 kind 的耗尽只做代码审查（guard 仍写 `failed` + 未清 scope） |
| T072 四 scope（checkpoint/sandbox temp/third-party/backup ledger） | **not_run（本票边界）** | T072；本票成功后 deletion job 保持 `pending` 且四项在 `remaining_scopes` |
| 诊断授权/每次正文读审计 | **not_run（边界）** | T073 |
| 无复活全套（restore/replay 六场景） | **not_run（边界）** | T076；本票只证 purge 级：purge 后所有读仍拒绝、cache 行/对象已无 |
| conversation 删除的物理清理 | **proven（T074 合并后新增）** | `test_conversation_purge_and_unknown_resource_fail_safe`：真实 conversation/request 的 sealed 字节由同一 purge 清理、三个 T071 scope 进 completed、T072 四项保留、会话/消息读仍 404；conversation 无 chunk/vector 索引与 retrieval-cache 表，两个 scope 是诚实空集 |
| 无 purge adapter 的资源类型 | **proven（fail-safe）** | 同一用例：真实 `admin background-jobs enqueue` + SQL 种子的 `unknown_resource` deletion job → 真实 maintenance 以 `purge_unsupported_target`（business）失败，全部 scope 保留、`completed_scopes=[]` |
| 被删除 document 的 source artifact 在 purge 失败窗口内仍可按 artifact id 读 | **已知边界，非复活** | T069 的 document 删除只 tombstone 文档与派生数据，没有 tombstone source artifact；T071 在对象 scope 清除时把它 mark `deleted`。document 自身的所有读面从 202 起即拒绝（T069 用例）；若审查认为 source artifact 也必须即刻拒绝，需在 T069/T074 的删除事务扩展（会改变“删除文档 ≠ 删除用户上传文件”的现有切片语义） |
| sealed 孤儿删除与并发写者的竞态 | **设计缓解，非强锁** | 无共享 writer/deletion 锁（F11 notes §9.5）；用“五类引用 + 10 分钟 grace”把观察窗口做稳，未做真实并发写/删压测 |
| 真实 S3/MinIO 对象存储 | **not_run** | 部署使用本地文件系统 store（既有实现）；本票在本地 store 上真实执行 |
| 浏览器 DOM/点击 | **not_run** | 本票无页面改动 |
| 全量 `pytest tests` | **not_run** | BRIEF 禁止（>60min），orchestrator 合并后运行 |
| 迁移编号 | **需 merger 重编号** | 本分支合并后为 `0045` 保链连续；全局预期 `0047`（见 §0.3） |
| f05/T028 迁移断言与 T074 的兼容修复 | **已修复（test-only）** | 见 §4.2 诚实说明：合并树让 T028 的“conversations/requests 不存在”断言失效；本分支最小化收窄该断言，T028 迁移文件 4 passed，完整 f05 子集重跑结果见 §4.2 |
