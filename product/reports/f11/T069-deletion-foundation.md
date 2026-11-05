# F11 T069 验收：用户删除会话或文件后立即无法再读取（共享删除基础）

- 任务：F11 / GitHub Issue #70（票 `T069`，规划 ID F11.01）；分支
  `t/t069-deletion-scope`（worktree `E:\orchara-worktrees\t069`）；父批次
  `integration/t068-t082`；基线 `cd8ca3c`；合并提交：本报告 §4.3（收尾时填写）。
- 状态：**`partial`**（T069 范围内的四个资源类型全部真实交付并验证；票面
  “会话”资源没有权威表/读路径，属 **F08/T074**，本票只把 `resource_type=conversation`
  接进同一通用基础并对未知 id 返回 404 —— 见 §2 AC 表与 §6）。
- 边界（如实）：本票交付 **tombstone + 删除任务 + 取消受影响运行 + 审计引用 + 202
  可查询**；物理清理（对象/chunk/vector/缓存）属 **T071**，checkpoint/摘要/临时沙箱/
  第三方历史/备份删除账本属 **T072**，诊断授权属 **T073**，会话/请求属 **T074**，
  无复活回归全套属 **T076**。本票未注册 `delete_content` handler，job 诚实保持
  `pending` 且 `remaining_scopes` 非空；未创建 `platform.conversations` 表。
- 真实依赖：PostgreSQL **16.4**（`127.0.0.1:55432`）、编译后的 `cmd/api`/`cmd/admin`/
  `cmd/maintenance`/`cmd/supervisor`、真实 HTTP/CSRF/mTLS、真实 sealed 对象字节、
  真实 T036 取消路径、真实 T038 恢复拒绝、Node 执行页面纯函数。无 mock、无跳过。

## 0. 工作记录（简短）

1. 读 BRIEF、`issues-69-83.md` #70、`notes/f11-deletion.md`（591 行）、`product/AGENTS.md`、
   `SECURITY.md`/`DATA-POLICY.md`/`BACKGROUND-JOBS.md`/`DATA-MODEL.md` 的删除/tombstone/
   retention/audit 条款、`contracts/openapi.json` 的五个 operationId 与
   `DeletionAccepted`/`DeletionJob`、`contracts/database.sql` 的 `deletion_jobs`、现有
   `internal/{memory,knowledge,artifacts,deletion?}/`、`internal/background/`、
   `internal/audit/`、`internal/execution/cancel.go`、`internal/api/{memory,knowledge,
   artifacts,server}.go` 与 `tests/support/{stack,t028,t054,t055,t058,t063,t038}.py`。
2. 结论：T058 已有 `platform.deletion_jobs` 与 memory tombstone；`deleteDocument` **不存在**
   （票面初稿说 F10 已有，实际没有，本票新建）；artifact/document/conversation 均无删除
   endpoint；读拒绝谓词已存在（artifacts `readyRow`、knowledge `readable_chunk.go`、
   memory recall、run reads 的 `runs.deleted_at`），因此只扩展谓词、不新增第二套过滤。
3. TDD 垂直切片（红→绿，红阶段失败已记录）：
   - artifact DELETE + 202 `DeletionAccepted` + tombstone + GET deletion + 重复同 job
     （红：`404 page not found`，无路由）→ 2 用例；
   - memory / knowledge-document DELETE + 契约字段 + 检索/引用/问答/缓存拒绝 + conversation
     404（红：`404 page not found`/旧路由缺失；`remaining_scopes` 首次为 objects 与契约
     string[] 不符的失败已修）→ 4 用例；
   - `resource_in_use`（真实冻结 manifest artifact）、真实 queued run 取消、run artifact
     列表拒绝、真实 `recovery_pending` 运行 `input_deleted` 拒绝 → 3 用例；
   - 删除后真实 agent run 不召回被删记忆（真实 supervisor + sealed 字节 + trace/模型请求）
     → 1 用例；
   - 迁移链/新列/约束/索引/worker 零权限 → 4 用例；
   - 页面（/personal-data + Node 执行 10 个纯函数）→ 3 用例；
   - 授权/CSRF/管理员/API key scope/审计最小化 → 4 用例。
4. 迁移编号 0042（本 worktree 下一个空位；按名字断言，合并重编号不误配）；更新
   f09/T058 与 mem0 baseline 对 `remaining_scopes` 的旧 objects 断言（行为变化见 §1.5）。
5. 门禁与合并见 §4；未 push、未关 issue、未改 `docs/spec/**`/`contracts/**`。

## 1. 交付内容

### 1.1 迁移 0042：一套删除任务 + 固定 scope 词表

`db/migrations/0042_deletion_foundation.sql`（SHA-256 前缀 `81FB7DDC1D0E8DF9…`）：

- 复用 0036 的契约表 `platform.deletion_jobs`，只做加法列：
  `scope_details jsonb`（每个固定 scope 的副本描述：object key/artifact/document/run id，
  非契约、不经 API 暴露）、`completed_scopes jsonb`、`attempt_count integer >= 0`、
  `error_code text`、`started_at timestamptz`、`updated_at timestamptz NOT NULL DEFAULT now()`；
  `state` 闭集 `pending|running|completed|failed`、`owner_id`（请求主体）、
  `resource_type/resource_id/deletion_revision`、`UNIQUE(resource_type,resource_id,
  deletion_revision)` 与 `completed_at` 不变。
- **`remaining_scopes` 改为契约 `string[]` 的固定词表**：
  `object_store|search_index|retrieval_cache|checkpoints|sandbox_temp|
  third_party_history|backup_catalog`；新 job 创建时七个 scope 全部在列（本票无法证明
  任何一类派生副本不存在，声称已完成即撒谎）。
- 旧 T058 objects 的转换（`object|artifact→object_store`、`embedding→search_index`；
  `memory_row` 不转为清理 scope，tombstone 行是禁止复活的权威、永不清理），转换后仍保留
  descriptor 到 `scope_details`；重复执行/新库无旧行均安全。
- 索引 `deletion_jobs_open (state, created_at) WHERE state IN
  ('pending','running','failed')` 供 T071/T072 扫描；列注释写明每个 scope 的归属票；
  `orchara_worker`/PUBLIC 对 `platform` 零权限；重复 `admin migrate` no-op，链连续。

### 1.2 `internal/deletion`：唯一删除基础

- `Service.Delete(callerID, resourceType, resourceID)`：一个事务内
  调用资源包 `Target.Delete`（行锁 + 归属校验 + tombstone + 计划）→ 插入/复用
  `deletion_jobs`（`ON CONFLICT ... DO NOTHING` + 回读，重复/并发删除返回同一 job）
  → 写 `audit_events(action='deletion.requested')`（只含 deletion_id/resource_type/
  resource_id/deletion_revision/remaining_scopes/affected_run_ids）→ 提交 → **才**答 202。
- 提交后通过注入的 `CancelRun`（真实 `execution.Service.Cancel`，T036）取消受影响且仍可
  执行的 run；结果写 `audit_events(action='deletion.run_cancel')`（run_id/previous_state/
  result_state/outcome），取消未完成时 job 记 `error_code='run_cancel_incomplete'`，
  重复 DELETE 会重新选中仍非终态的 run 重试；取消失败绝不恢复可读性。
- 取消选择：`queued/running/waiting_*/cancelling` 取消；`recovery_pending/
  needs_reconciliation` **不取消**（它们已不能执行，T038 恢复门禁会重读 tombstone 并以
  `input_deleted` 拒绝——取消它们会掩盖该防御性证据）；终态不处理。
- `Service.Get(callerID, deletionID)`：owner-only，返回契约
  `{id,state,remaining_scopes}`；他人/未知/畸形同一 404。

### 1.3 三个资源适配器（复用既有 tombstone 列）

- `internal/artifacts/deletion.go`：`artifacts.deleted_at + state='deleted' + revision+1`；
  已存在的 `Get/readyRow/DownloadURL/OpenContent` 与 finalize guard 立即拒绝。删除
  knowledge source artifact 时**同事务**把派生 `knowledge_documents`
  `deleted_at/state='deleted'/revision+1`，检索/引用/缓存谓词因此立即拒绝。
  `resource_in_use` 检查：`application_versions.manifest_artifact_id`、
  非终态 run 的 `execution_snapshots.manifest_artifact_id`、
  `model_profile_versions.manifest_artifact_id`、`tool_versions.source_artifact_id`、
  `evaluation_reports.result_artifact_id`、`background_jobs` 的 ready/leased
  input/result artifact → 409 `resource_in_use` + `details.references`（仅引用类别）。
- `internal/memory/deletion.go`：`memories.deleted_at + revision+1` + 同事务 bump
  `policy_epochs(memory:<owner>:<app>)`；body artifact 描述、embedding 描述、缓存 scope、
  checkpoint/temp/third-party/backup scope 写入 job。另把 memory/candidate 的读取谓词
  加上 body artifact `deleted_at IS NULL AND state='ready'`（artifact 被单独删除时也不再
  可读；候选确认不会把已 tombstone 的正文复活）。
- `internal/knowledge/deletion.go`：`knowledge_documents.deleted_at + state='deleted' +
  revision+1`；既有 `readable_chunk.go`、document/diagnostics 查询、reindex/ingest 激活守卫
  全部已过滤 tombstone，所以检索/引用/问答/缓存立即拒绝。scope details 记录
  source+chunk-body 对象、chunk/index-version id、kb/document 缓存键。
- `conversation`：无 Target、无表；DELETE 返回 404（T074 创建 conversations 后注册
  Target 即可复用全部基础）。

### 1.4 HTTP + 页面

- 新增路由：`DELETE /api/v1/artifacts/{id}`、`DELETE /api/v1/memories/{id}`（改为走共享
  基础）、`DELETE /api/v1/knowledge-documents/{id}`、`DELETE /api/v1/conversations/{id}`
  （恒 404，直到 T074）、`GET /api/v1/deletions/{id}`；202 为契约 `DeletionAccepted`
  （`id` 是 deletion_job id，`resource_id` 是资源 id），重复删除同 id。
- 授权沿用邻接端点：cookie 会话必须 CSRF（缺失 403 `csrf_invalid`）；API key 删除需
  `application:write`、读 job 需 `application:read`（删 memory 的服务账号仍是 403
  `personal_scope_forbidden`）；他人/管理员/未知/畸形同一 404。
- `GET /personal-data` + `assets/personal-data.js`：列出我的记忆与知识文档、按 artifact id
  删除文件、删除后轮询 `GET /deletions/{id}` 显示真实状态与 `remaining_scopes`；轮询有界
  （job 在 T069 会一直 pending），文案明确“旧内容已停止被读取，物理副本由后续清理任务完成”，
  从不声称字节已删；全部 `textContent`，无 innerHTML/localStorage。

### 1.5 行为变化（同 change 内更新受影响测试）

- `deletion_jobs.remaining_scopes` 从 T058 的 objects 变为契约 string[]；T058 的
  `tests/f09/test_t058_memory.py`、`tests/support/t058.py`（新增
  `DELETION_SCOPES`/`deletion_scope_details`）、`tests/f09/test_t058_migration.py`
  （列集合并）与 mem0 baseline `pg_baseline._remaining_scope_kinds` +
  `test_t066_mem0_experiment.py`（接受字符串词表）在同一 change 更新；T058 原有的
  tombstone/revision/epoch/幂等断言全部保留。
- `memory.Service.Delete` 保留为进程内兼容入口（mem0 pgdriver 证据工具使用），内部同样
  走共享 foundation；产品 HTTP 不再调用它。
- `artifacts.purged_at` 仍是未写列（T071 在 scope 清零后写）。

## 2. AC 核对

| AC | 结果 | 证据（真实栈） |
|---|---|---|
| 1. 下载/检索/缓存/恢复立即拒绝 | **proven（artifact/document/memory，四项读面）** | artifact：`test_delete_artifact_answers_202_and_tombstones_before_any_cleanup` —— 202 后立即 `GET /artifacts/{id}`、`/download`、`/content` 全 404，bytes 仍在盘（T069 未 purge）；document：`test_delete_document_stops_retrieval_citations_answers_and_the_cache` —— 先 warm cache（hit/stored 真），删后 detail 404、list 空、citation 404、retrieval items 空且 cache hit=false、answer `no_results`；memory：`test_a_deleted_memory_is_not_recalled_by_a_later_agent_turn` —— 真实 supervisor agent run 的 trace source 不含被删 memory id、模型请求无被删正文，kept memory 是阳性对照；run artifact listing：`test_deleting_a_run_linked_artifact_cancels_the_queued_run` 删后列表立即为空；恢复：`test_a_parked_runs_deleted_input_makes_recovery_refuse` 真实 `admin runs recover` 拒绝 `input_deleted` |
| 2. 202 可查询 remaining scopes | **proven** | 三个资源测试均断言 `GET /deletions/{id}` 200、恰好 `{id,state,remaining_scopes}`、state `pending`、`remaining_scopes` 等于七项固定词表（排序稳定）；DB 行 `scope_details` 含 object/artifact 描述；`test_the_fixed_scope_vocabulary_is_the_one_documented_for_later_tickets` 钉住列注释词表 |
| 3. 受引用配置 artifact 返回 resource_in_use | **proven** | `test_a_recoverable_runs_manifest_artifact_is_resource_in_use`：真实调试 run 的冻结 `execution_manifest` artifact，DELETE → 409 `resource_in_use` + `details.references=["recoverable_run"]`，artifact 行 deleted_at/revision 不变、无 job、run 仍 running |
| 4. 端到端入口 + 失败/当前授权/重试边界 | **proven（四资源）+ partial（conversation）** | 真实 cookie+CSRF / API key 请求；`test_a_delete_without_the_csrf_token_changes_nothing`（403 且行/ job 零变化）、`test_anonymous_deletes_are_401_and_admin_is_not_exempt`（匿名 401、管理员 404）、`test_api_key_scopes_gate_delete_and_the_job_read`（读 key 删 403 insufficient_scope、写 key 删 memory 403 personal_scope、他人 404、写 key 读 job 403）；重复删除同 job、并发/重复 memory 删除只 bump 一次（T058 既有用例保持绿）；取消真实 queued run → `cancelled` + `run.status` 事件；`resource_in_use` 失败不写；审计仅 ids（`test_the_deletion_audit_keeps_ids_but_no_content`）。conversation 见下行 |
| 4a. conversation 删除 | **partial → F08/T074** | 路由已接同一 foundation：`test_conversation_delete_is_wired_but_has_no_authority_until_t074` 断言未知/格式错误 id 均 404 且不写 job；platform.conversations 表、会话读路径、会话删除的 immediate-read-refusal 由 T074 创建并证明 |
| 5. 记录版本/输入/hash/命令/原始结果/profile | **proven（本报告 §4/§5）** | 环境与命令尾巴、迁移/deletion.go SHA-256 前缀、两轮 f11 与邻接套件原始输出；stub/real：本票全部 real，无 stub 结果；未运行项列 §6 |

## 3. 下一票接口（强制；T071/T072/T073/T074/T076 依赖）

### 3.1 表与列（唯一权威，不要再建并行删除表）

`platform.deletion_jobs`（0042 后）：

| 列 | 语义 |
|---|---|
| `id uuid PK` | deletion_job id（API `DeletionAccepted.id` / `DeletionJob.id`） |
| `owner_id uuid → principals` | 请求主体（owner-only 读的唯一依据） |
| `resource_type text` | `artifact`、`memory`、`knowledge_document`；`conversation` 预留给 T074 |
| `resource_id uuid` | 被删资源 id（API `DeletionAccepted.resource_id`） |
| `deletion_revision bigint` | tombstone 后的 revision；`UNIQUE(resource_type,resource_id,deletion_revision)` 使重复删除同 job |
| `state text` | 闭集 `pending|running|completed|failed`；T069 永远写 `pending` |
| `remaining_scopes jsonb` | **固定 string[] 词表**（见 3.2），API 原样返回 |
| `scope_details jsonb` | 每个 scope 的副本描述（object key/artifact_id/document_id/chunk_ids/run_ids/checkpoint_thread_ids/knowledge_base_ids/memory ids）；内部只用，不出 API |
| `completed_scopes jsonb` | T071/T072 清除后追加 |
| `attempt_count integer` | 清理尝试次数（T071/T072 递增） |
| `error_code text` | 最近一次安全错误码；取消未完成时 `run_cancel_incomplete` |
| `started_at/updated_at/completed_at` | 时间戳；`created_at` 是创建时间 |

审计：`audit_events` 现有 `deletion.requested`（资源删除，metadata 固定六键）与
`deletion.run_cancel`（每个受影响 run 一条，metadata run_id/previous_state/result_state/
outcome）。T073 在此基础上做 scoped diagnostics/audit，不要把正文写进 metadata。

### 3.2 scope 词表与归属

```
object_store         sealed object + artifact row                T071
search_index         chunks/vectors/index versions               T071
retrieval_cache      knowledge_retrieval_cache / memory epoch    T071
checkpoints          run checkpoint + summaries                  T072
sandbox_temp         sandbox/tool temp leftovers                  T072
third_party_history  provider/memory-component copies            T072
backup_catalog       backup deletion ledger                      T072
```

语义：新 job 七项全在 `remaining_scopes`；T071/T072 完成一个 scope 时在**同一事务**把它从
`remaining_scopes` 移除、追加 `completed_scopes`，并（重新）计算 `remaining_scopes` 的稳定排序；
全部清空时置 `state='completed'` + `completed_at=now()`（失败置 `failed` + `error_code`，
但绝不恢复可读性）。`scope_details` 里的 key/id 是清理目标；禁止按“未被引用”猜孤儿。
`remaining_scopes` 空数组表示七类都已完成。

### 3.3 Go 接缝

- `internal/deletion.Target`：
  `ResourceType() ResourceType` + `Delete(ctx, tx, ownerID, resourceID) (Plan, error)`；
  `Plan{Revision, AlreadyDeleted, Details map[Scope]any, AffectedRuns []AffectedRun}`。
  新资源（T074 conversation）只需实现 Target 并在 `api.New` 注册；foundation 负责 job/
  audit/取消/幂等。
- `deletion.Service.CancelRun` 是 T036 取消路径注入缝；T071/T072 不要绕过它。
- `deletion.AllScopes` 是词表唯一代码源；加值需迁移 + 报告/ADR 更新。
- `platform.deletion_jobs` 是 T071 `delete_content` 的 target（`background_jobs.kind`
  已存在）；T071 在其上注册 handler/claim/retry/completion guard，**不要**新建第二张删除表。
  T072 的备份账本作为独立可回放账本另建（本票未建）；T073 的 diagnostic_grants 另建。

### 3.4 T071/T072 仍未做的（本票诚实边界）

对象/索引/缓存/chunk/checkpoint/临时沙箱/第三方/备份均**未物理清理**：sealed bytes 仍在
`stack.object_dir`，`artifacts.purged_at` 仍 NULL，job 永远 `pending`。失败重试、lease、
completion guard、`delete_content` handler 全部属 T071；`deletion_jobs_open` 索引与
`scope_details` 已为它们准备好。本票不消费 scope、不删任何字节。

## 4. 门禁命令与原始输出

环境：Windows 10 + PowerShell 7；`go version go1.27.3 windows/amd64`；Python 3.12.2；
Node v24.13.0；PostgreSQL **16.4**（`python tools/pg_dev.py status` → running，端口 55432）。

### 4.1 本票套件（`--basetemp` 隔离，背靠背两轮）

```text
$ python -m pytest tests/f11 -q --basetemp=E:\orchara-tmp\t069-15
.....................                                                    [100%]
21 passed in 155.89s (0:02:35)

$ python -m pytest tests/f11 -q --basetemp=E:\orchara-tmp\t069-16
.....................                                                    [100%]
21 passed in 153.96s (0:02:33)
```

套内细分：`test_t069_delete_api.py` 2、`test_t069_resource_deletes.py` 4、
`test_t069_runs.py` 3、`test_t069_recall.py` 1、`test_t069_migration.py` 4、
`test_t069_pages.py` 3、`test_t069_authz.py` 4 = 21。

### 4.2 邻接套件（改动可破坏的）

```text
$ python -m pytest tests/f09 -q --basetemp=E:\orchara-tmp\t069-17
.....................................................................    [100%]
69 passed in 408.36s (0:06:48)

$ python -m pytest tests/f10 -q --basetemp=E:\orchara-tmp\t069-18
........................................................................ [ 81%]
................                                                         [100%]
88 passed in 563.58s (0:09:23)

$ python -m pytest tests/f05/test_t028_authz.py tests/f05/test_t028_migration.py \
    tests/f05/test_t028_pages.py tests/f05/test_t028_quarantine.py tests/f05/test_t028_race.py \
    tests/f05/test_t028_upload_flow.py tests/f05/test_t033_endpoints.py \
    tests/f05/test_t033_migration.py tests/f05/test_t033_pages.py -q \
    --basetemp=E:\orchara-tmp\t069-20
................................................                         [100%]
48 passed in 375.42s (0:06:15)

$ python -m pytest tests/tooling -q --basetemp=E:\orchara-tmp\t069-21
.....................                                                    [100%]
21 passed in 7.18s
```

诚实说明：T028/T033 组合套件第一次调用（`t069-19`）在 31 个点后以 exit 1 结束、没有任何
failure/断言行（主机同时有其他作业，疑似 pytest 会话被外部清理/内存压力）；隔离 basetemp
重跑 48 passed，以上为通过的那次。f09/f10 均只跑一次（含 mem0 真实实验与 PDF/缓存对照）；
未运行全量 `pytest tests`（BRIEF 明令，orchestrator 合并后运行）。

### 4.3 Go / 格式 / 契约

```text
$ go build ./...
(no output, exit 0)

$ go vet ./...
(no output, exit 0)

$ go test ./...
(all packages ok / no test files; 0 FAIL; exit 0)

$ gofmt -l .
(no output)

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest   (exit 0)
```

### 4.4 合并（BRIEF §7.6）

```text
$ git merge integration/t068-t082
<见收尾记录：merge commit、冲突与重跑>
```

<!-- MERGE-RESULT -->

## 5. 输入与版本

| 输入 | 版本 / SHA-256（前 16 位） |
|---|---|
| 基线 | `integration/t068-t082` @ `cd8ca3c` |
| `db/migrations/0042_deletion_foundation.sql` | `81FB7DDC1D0E8DF9…` |
| `internal/deletion/deletion.go` | `99158F4820C1E30D…` |
| PostgreSQL | 16.4（用户态 `127.0.0.1:55432`） |
| Go / Python / Node | go1.27.3 windows/amd64 / 3.12.2 / v24.13.0 |
| 测试输入 | 真实上传/封存字节；document marker 与 memory marker 均为每测试唯一字符串；无凭据进仓库 |

## 6. `partial` / `not_run` 清单（如实）

| 项 | 状态 | 原因 / 承接票 |
|---|---|---|
| conversation 删除的立即不可读 | **partial** | 无 `platform.conversations` 表与会话读路径；路由已通用化，未知 id 404。**F08/T074** 创建表并在同一 foundation 注册 Target + 证明读拒绝 |
| 对象/索引/缓存物理清理、失败重试、lease/completion、`delete_content` handler | **not_run（本票边界）** | **T071** |
| checkpoint/摘要/临时沙箱/第三方副本/备份删除账本 | **not_run（本票边界）** | **T072** |
| 诊断授权与每次正文读审计 | **not_run（本票边界）** | **T073** |
| 无复活回归全套 | **not_run（本票边界）** | **T076** |
| 旧 T058 object 数组的迁移转换（有旧数据的升级库） | **not_run** | 新库/实验库无 0042 前遗留 job；迁移 SQL 已在真实链上执行且幂等，但未构造 pre-0042 行做数据级断言，故不写成 proven |
| 浏览器 DOM/真实点击 | **not_run** | 无浏览器驱动；页面纯函数由 Node 真实执行 |
| 全量 `pytest tests` | **not_run** | BRIEF 禁止（>60min），orchestrator 合并后运行 |
| T028/T033 之外的 f05（tool/build/sandbox） | **not_run** | 本票未改那些代码路径；只跑受删除路由影响的 artifact 套件 |
