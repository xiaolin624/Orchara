# F11 T072 验收：清理 checkpoint 和上下文副本并登记备份删除范围

- 任务：F11 / GitHub Issue #73（票 `T072`，规划 ID F11.02）；分支
  `t/t072-checkpoint-backup`（worktree `E:\orchara-worktrees\t072`）；父批次
  `integration/t068-t082`；基线 `57d90df`（含 T069 删除基础、T074 会话/请求）；
  合并提交：见 §4.4（收尾 `git merge integration/t068-t082`）。
- 状态：**`passed`**（四个 T072 scope 全部真实交付：checkpoint/摘要/临时沙箱/
  第三方历史均落在独立哈希链账本；账本可由真实 `cmd/admin` 导出并在第二个真实
  PG 16.4 数据库回放；失败注入下 job 记录失败、`GET /deletions/{id}` 仍列未清
  scope、真实读端点仍拒绝）。
- 边界（如实）：对象/chunk/vector/cache 物理清理与 `delete_content` handler/lease/
  completion guard 属 **T071**；诊断授权属 **T073**；无复活全套回归属 **T076**；
  发布/撤销、会话/router/子运行不在本票。本票**不**修改 `delete_content` 注册表、
  **不**新增 job kind：部署级副本清单由 `cmd/maintenance copies` 执行（理由见 §1.4
  与 ADR 0027 §3）。
- 真实依赖：PostgreSQL **16.4**（`127.0.0.1:55432`，用户态 cluster）、编译后的
  `cmd/api`/`cmd/admin`/`cmd/maintenance`/`cmd/supervisor`、真实 HTTP/cookie+CSRF/
  mTLS、真实 sealed 对象字节、真实 T036 取消路径、真实 `checkpoint` schema fixture
  与真实主机临时目录/文件句柄。无 mock、无跳过。
- 账本独立性：`platform.deletion_ledger` 与 `deletion_jobs`/资源表之间**没有任何外键**，
  由数据库 trigger 强制 append-only；导出文件带 `count`/`head_sha256`，回放前先验链。

## 0. 工作记录（简短）

1. 读 BRIEF、`issues-69-83.md` #73、`notes/f11-deletion.md`（§3.5/§3.10/§4/§5/§6/§8）、
   T069 代码与报告“next ticket interface”、`contracts/openapi.json` 的
   `DeletionJob`/`DeletionAccepted`、`DATA-POLICY`/`SECURITY`/`OPERATIONS`/
   `BACKGROUND-JOBS`/`DATA-MODEL`/`ROUTING-MEMORY-KNOWLEDGE` 的删除/备份/第三方条款、
   `internal/{deletion,sandbox,execution,background,api}`、`cmd/{admin,maintenance}`、
   `experiments/mem0/` 的 `do_not_adopt` 证据、`tests/support/{stack,t069,f00b,t029,t041}.py`。
2. TDD 垂直切片（每个切片先有失败断言，再实现；red 记录见 §4.5）：
   - 账本切片：删除后账本有 tombstone、哈希链、重复 DELETE 不追加、导出 CLI、
     第二个数据库回放、篡改拒绝、append-only trigger（5 用例）；
   - 副本切片：四类副本记录、缺失 checkpoint schema 记 `absent`、真实 checkpoint 行
     + 真实沙箱目录被清、合法邻居行/目录保留、重复运行零删除、四 scope 清空后 job
     completed + 不可擦除声明、第三方组件 external、摘要面正文泄漏检查（5 用例）；
   - 失败切片：Windows 打开文件句柄使真实 `os.RemoveAll` 失败 → job failed / scope
     未清 / 读仍 404 / 释放后重试成功；活 attempt 的 workspace 绝不被删（2 用例）；
   - 迁移切片：名字断言/链连续/列与闭集 CHECK/无 FK/trigger/二次 migrate no-op（4 用例）。
3. 迁移编号：票面分配 **0048**，但本 worktree 当前最大为 `0044`；若直接写 0048 会让
   `tests/support/stack.py::migration_chain` 的 0001..NNNN 连续性断言失败（T069 迁移
   用例亦调用它）。故按 BRIEF §5“下一个空位”使用 **`0045_deletion_copy_ledger.sql`**，
   **合并时请重编号为 0048**（并注意 T071 分支的 `0044_deletion_purge_paths.sql` 与
   T074 的 0044 撞号，需一并重编号）。
4. 未 push、未关 issue、未改 `docs/spec/**`/`contracts/**`；`sync_spec --check` no drift。

## 1. 交付内容

### 1.1 迁移 0045：独立、append-only、哈希链账本

`db/migrations/0045_deletion_copy_ledger.sql`（SHA-256 前缀 `CD168C657D4180AC…`）：

- `platform.deletion_ledger(sequence identity PK, deletion_job_id, resource_type,
  resource_id, deletion_revision, entry_kind, scope, copy_class, scope_key,
  record jsonb, record_sha256, prev_sha256, created_at)`；
- `entry_kind` 闭集 `tombstone|scope_cleared|scope_absent|scope_failed|scope_external|
  job_completed`；`copy_class` 闭集 `checkpoint|summary|sandbox_temp|
  third_party_history|backup_catalog|backup_media|vendor_copy|downloaded_copy`；
  `scope` 复用 T069 固定七词表；scope 级记录必须同时有 scope/copy_class/scope_key；
- `record_sha256 = sha256(RFC 8785 canonical JSON of 整个 entry，含 sequence/prev)`，
  `prev_sha256` 链到上一行（genesis 为 NULL）；sequence 在事务级 advisory lock 下分配；
- `BEFORE UPDATE OR DELETE` + `BEFORE TRUNCATE` trigger 抛 `restrict_violation`，
  append-only 是数据库事实而非代码约定；两个索引（job、资源）；
- **没有任何外键**（双向检查）——账本可在只恢复部分表的目标上独立保留/回放；
  `orchara_worker`/PUBLIC 零权限；二次 `admin migrate` no-op、链连续。

### 1.2 账本写读导出回放：`internal/deletion/ledger.go` + `cmd/admin deletion`

- `Service.Delete` 在**同一事务**（tombstone + job + audit）追加一条 `tombstone` 记录，
  记录资源 id/revision 与同事务被 tombstone 的派生 document ids；重复 DELETE 复用 job，
  不追加第二条决策。
- `deletion.ExportLedger`：先验链，写 JSONL（首行 header：format/exported_at/database/
  count/head_sha256；每行一个 entry）。`orchara-admin deletion export --file PATH`
  写临时文件后原子 rename，stdout 单个 JSON `{status,file,entries,head_sha256,database}`。
- `deletion.ParseLedgerFile` + `ReplayLedger`：先验 format/count/head/逐行哈希/链连续性，
  再按 `tombstone` 顺序回放；每条回放与“新账本记录”同事务提交；只改
  `deleted_at IS NULL` 的行，`revision = GREATEST(revision+1, deletion_revision)`：
  artifact（含记录的 derived document）、memory（revision + policy epoch）、
  knowledge_document、conversation（并取消失活请求/invalidations）；缺行计 `missing`。
- `orchara-admin deletion replay --file PATH [--check-only]`：stdout 单个 JSON
  `{status,entries,replayed,already_deleted,missing,would_replay,check_only,head_sha256}`；
  篡改/断链/header 不一致 → exit 1 且目标库零变更；二次回放 `replayed=0` 且不追加。
  usage 错误 exit 2；错误只写 stderr，不打印 DSN。

### 1.3 四个 scope 的副本清单：`internal/deletion/copies.go`

`cmd/maintenance copies`（真实编译二进制）每个非终态且仍欠 T072 scope 的 job：

| copy class | 行为 | 诚实结论 |
|---|---|---|
| `checkpoint` | 按 `scope_details.checkpoint_thread_ids` + `runs.checkpoint_thread_id` 定位；`to_regclass` 探测 out-of-band `checkpoint` schema；存在则按 thread 删 `checkpoints/checkpoint_blobs/checkpoint_writes` | schema 不存在 → `absent`/`checkpoint_schema_absent`（**不是 cleared**）；无 run 引用 → `absent`/`no_checkpoint_thread_reference` |
| `summary` | 产品无摘要存储；统计 `run_events`/`run_attempts.reported_result`/`run_node_outputs` 真实行数作为边界 | `absent`/`no_summary_store`（有 run 时附 surface 计数；无 run → `no_run_reference`） |
| `sandbox_temp` | 由被删 run 的 `run_attempts.execution_unit_id` 定位真实主机目录 `orchara-sandbox-<unit>-*`（`internal/sandbox` 的目录名改为含 sandbox id），`--sandbox-age` 后删除并在真实 FS 复核 | 无残留 → `absent`/`no_leftover_workspace`；**活 attempt** → `failed`/`copy_sandbox_live_attempt`，目录保留且 scope 不完成；删除失败 → `failed`/`copy_sandbox_remove_failed` |
| `third_party_history` | 默认部署无第三方记忆组件（T066 `do_not_adopt` 已提交证据） | `absent`/`no_third_party_component_deployed`；`--third-party-components mem0` → `external`/`component_history_outside_platform_control`（vendor copy 不宣称擦除） |
| `backup_catalog` | 把删除决策登记进独立账本 | `cleared`/`deletion_registered_in_ledger`；同时记 `backup_media`/`downloaded_copy` 为 `external`（30 天保留；已下载副本不可召回） |
| `vendor_copy` | 若受影响 run 有真实 model operation（`platform.operations.capability_kind='model'`） | `external`/`model_provider_may_retain_prompts`；否则 `absent`/`no_external_model_path` |

清除规则（T069 报告 §3.2 的强制接口）：**同一 evidence 事务**内先追加每个 copy class 的
ledger 行，再把已终态的 scope 从 `remaining_scopes` 移除、追加到 `completed_scopes`
（稳定排序）、`attempt_count+1`、更新 `error_code/started_at`；只有 `remaining_scopes`
为空才置 `state='completed'`+`completed_at`+`job_completed`。失败只影响该 scope：
留在 `remaining_scopes`、job `failed`+安全错误码、内容仍不可读。该规则导出为
`deletion.ClearScopesTx` 供 T071 复用（T071 分支先于本 helper，合并时指向它即可）。

### 1.4 为什么不是第二个 `delete_content` handler

- checkpoint schema 在 out-of-band schema（`DATA-MODEL.md` §22：产品不得创建）、
  沙箱目录是 supervisor 主机文件系统状态、第三方历史/备份媒质在数据库与对象存储之外，
  这些都不是 T071 对象存储 handler 能处理的输入；
- T071 与本票并行；两票在同一个 registry 各加一条、同时改 job 行只会制造合并冲突且无收益；
- 因此 T072 **不修改** `background/handlers` registry、**不新增** background job kind、
  不注册 `delete_content`；`cmd/maintenance copies` 是 T072 的运维/调度入口，
  `cmd/maintenance run|sweep` 仍归 T071/T026。ADR 0027 §3 记录此决定。

### 1.5 沙箱临时目录的来源引用

`internal/sandbox` 的主机 workspace 目录名由 `orchara-sandbox-<random>` 改为
`orchara-sandbox-<sandboxID>-<random>`（sandboxID 由 attempt 的 execution unit id 派生），
从而 `run_attempts.execution_unit_id` 成为可回查的来源引用；不靠“目录旧/前缀像”猜孤儿。
新增 `ORCHARA_SANDBOX_WORKSPACE_ROOT` 让部署可固定 staging root（须与
`cmd/maintenance copies --sandbox-temp-root` 一致）；默认仍为空=OS 临时目录，单次
`defer RemoveAll` 自清不变。这是本票对 sandbox 的唯一改动。

## 2. AC 核对

| AC | 结果 | 证据（真实栈） |
|---|---|---|
| 1. checkpoint/摘要/临时沙箱/第三方历史均有清理记录 | **proven** | `test_every_copy_class_is_recorded_and_a_missing_checkpoint_schema_is_absent`：真实删除 job 后 `maintenance copies` 为每类写 ledger（checkpoint `absent`+`checkpoint_schema_absent`、summary `absent`+`no_summary_store` 且三个 surface 计数真实、sandbox `absent`、third_party `absent`、backup `cleared`、downloaded/backup_media `external`、vendor `absent`），job `completed_scopes` 精确为四项、`remaining_scopes` 为 T071 三项、`GET /deletions` 合同字段一致；`test_named_checkpoint_and_sandbox_copies_are_cleared_...`：真实 `checkpoint` 三表行按 thread 删除（计数 1/1/1）、真实目录删除，另有真实 `insert_checkpoint` fixture 的 provenance 说明“产品不创建该 schema”；`test_a_deployed_third_party_component_...`：`mem0` external 记录 + scope 仍终态 |
| 2. 重复运行不误删合法引用 | **proven** | `test_named_checkpoint_and_sandbox_copies_are_cleared_...`：第二个真实文件 `legit-labelled-copy` 的 3 张 checkpoint 行与内容均保留、无关注释目录及字节保留；两次 `maintenance copies` 后 ledger 行数不变、第二次 `jobs=0`；`test_a_delete_appends...` 重复 DELETE 只一条 tombstone；`test_every_copy_class...` 第二次 pass `jobs=0`；`test_all_four_scopes_terminal...` 完成后再跑仍 `jobs=0` |
| 3. 备份删除账本可独立保留与回放 | **proven** | `test_replay_reapplies_the_same_decisions_on_a_fresh_restored_database`：真实 `admin deletion export` → 第二个真实 PG 16.4 库（编译 `admin migrate`）按恢复前状态 seed 后 `deletion replay` → `replayed=1`、artifact `deleted_at/state='deleted'/revision+1`、回放库账本追加 `source=replay` 记录；二次回放 `replayed=0/already_deleted=1`；`test_replay_restores_the_derived_document_and_conversation_tombstones`：同一 artifact 决策携带 derived document（记录里 `derived_document_ids`），回放同时 tombstone artifact+document，conversation 决策单独回放；`test_the_ledger_is_independent_of_the_live_resource_tables` 断言双向无 FK；`test_a_tampered_ledger_file_is_refused_and_changes_nothing`：改 record/截断文件 → exit 1、目标库零账本/零 tombstone；`test_the_ledger_table_is_append_only`：真实 UPDATE/DELETE/TRUNCATE 全部被 trigger 拒绝 |
| 4. 清理失败可见而内容仍不可读 | **proven** | `test_a_failed_sandbox_cleanup_is_visible_while_the_content_stays_unreadable`：测试进程持有 Windows 文件句柄 → 清理进程真实 `os.RemoveAll` 失败；`maintenance copies` exit 0 且 `scopes_failed=1`；job `state='failed'`、`error_code='copy_sandbox_remove_failed'`、`remaining_scopes` 仍含 `sandbox_temp`；ledger `scope_failed`（含目录名/`removed=false`）；`GET /deletions/{id}` 200 `state=failed` + 未清 scope；真实 `GET /artifacts/{id}`/`/download`/`/content` 全 404；释放句柄后重试 → scope 完成、job 回到 pending（仅 T071 scope 待清）、`error_code=NULL`；第三次 pass `jobs=0` |
| 5. 端到端入口 + 失败/重试边界断言 | **proven** | 删除/读拒绝走真实 HTTP（cookie+CSRF）；清理/回放走真实编译 `cmd/maintenance`/`cmd/admin`；恢复目标为第二个真实库；失败注入为真实 OS 错误；`--check-only`/篡改/断链均为真实 CLI 断言；`test_a_live_attempt_workspace_is_never_deleted` 证明活 attempt 的 workspace 保留且 scope 不虚假完成 |
| 6. 记录版本/输入/hash/命令/原始结果/profile | **proven（本报告 §4/§5）** | 版本、迁移/代码 SHA-256 前缀、两轮 f11 原始尾巴、go/tooling 原始输出、环境 profile；stub/real：全部 real，无 stub 结果；未运行项列 §6 |

## 3. 下一票接口（T071/T073/T076 及合并器）

### 3.1 表与命令（唯一权威）

- `platform.deletion_ledger`：append-only；`entry_kind`/`copy_class` 闭集见 §1.1；
  任何后续“新增副本类”的票必须在此词表加值（迁移 + 报告/ADR 更新），不得建第二本账。
- `deletion.ClearScopesTx(ctx, tx, jobID, cleared []Scope, state, errorCode string) error`
  = T069/T071/T072 共享的 job 清除规则（移除 remaining、追加 completed、attempt+1、
  全空时 completed+job_completed）；T071 的 completion 路径应改调它，避免两份 update。
- `deletion.ReadLedger` / `VerifyLedger` / `ParseLedgerFile` / `ExportLedger` /
  `ReplayLedger`：T076 的“无复活”恢复演练直接复用；`ReplayLedger` 是恢复时唯一
  合法重建路径。
- `deletion.CopyCleaner{DB,SandboxRoot,SandboxAge,ThirdPartyComponents,Limit,Now,Logf}`
   与 `CopyPassReport`：T076 可用 `Now`/`SandboxAge` 做确定性断言；日志只含 id/code。
- `orchara-admin deletion export|replay --file … [--check-only]`；`copies --sandbox-temp-root
  --sandbox-age --third-party-components --limit`。

### 3.2 T076 直接可用的账本语义

- `tombstone` 是唯一回放源；`scope_*`/`job_completed` 是证据，不产生写操作。
- 回放只重新施加逻辑删除（tombstone），**不**重新删除对象字节；对象/索引/缓存的
  物理清理仍由 T071 按 scope 执行。恢复后应先 replay 再开放读，再运行
  `copies`/T071 purge。
- 不可擦除声明在 `job_completed.record.non_erasable`：`vendor_copy`/`downloaded_copy`/
  `backup_media` —— T076 不得把“completed”解释为“副本全擦除”。

## 4. 门禁命令与原始输出

环境：Windows 10 + PowerShell 7；`go version go1.27.3 windows/amd64`；Python 3.12.2；
Node v24.13.0；PostgreSQL **16.4**（`python tools/pg_dev.py status` → running，端口 55432）。

### 4.1 本票套件（`--basetemp` 隔离，背靠背两轮）

```text
$ python -m pytest tests/f11 -q --basetemp=E:\orchara-tmp\t072-20
......................................                                   [100%]
38 passed in 443.55s (0:07:23)

$ python -m pytest tests/f11 -q --basetemp=E:\orchara-tmp\t072-21
......................................                                   [100%]
38 passed in 450.46s (0:07:30)
```

套内细分：T069 既有 22 + T072 16（`test_t072_ledger.py` 5、`test_t072_copy_cleanup.py` 5、
`test_t072_migration.py` 4、`test_t072_failures.py` 2）。

### 4.2 邻接套件

```text
$ python -m pytest tests/f09 -q --basetemp=E:\orchara-tmp\t072-f09
.....................................................................    [100%]
69 passed in 593.17s (0:09:53)

$ python -m pytest tests/f08 -q --basetemp=E:\orchara-tmp\t072-f08
...................                                                      [100%]
19 passed in 186.91s (0:03:06)

$ python -m pytest tests/tooling -q --basetemp=E:\orchara-tmp\t072-tooling
.....................                                                    [100%]
21 passed in 7.53s
```

### 4.3 Go / 格式 / 契约

```text
$ go build ./...
(no output, exit 0)

$ go vet ./...
(no output, exit 0)

$ go test ./...
(全部包 ok / no test files；0 FAIL；exit 0；internal/deletion [no test files])

$ gofmt -l .
(no output)

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest   (exit 0)
```

### 4.4 合并（BRIEF §7.6）

```text
$ git merge integration/t068-t082
见下
```

### 4.5 TDD red 记录（摘要）

- 账本切片首跑：`relation "platform.deletion_ledger" does not exist`（迁移/存储未实现）
  → 实现 0045 + `ledger.go` + admin CLI 后 4 passed。
- 副本切片首跑：`cleanCheckpointsTx` 的 `scopeKeyOf` 用 RFC 8785 规范化时拒绝 `[]string`
  （`unsupported value type`）→ 增加 `jsonNative` 往返后 5 passed；此前 `copies` 仅记下
  checkpoint 一条 `scope_failed`，red 证据保留在 basetemp `t072-6`..`t072-9` 输出中。
- 派生文档回放首跑：`seed_restored_document` 违反 `ready_has_active_index`（真实 schema
  CHECK）→ 恢复目标 fixture 改为 `uploaded` 活行后 pass（回放决策不依赖索引行）。
- 迁移/失败切片均先有断言未实现（表缺失/未建 CLI）再实现。

## 5. 输入与版本

| 输入 | 版本 / SHA-256（前 16 位） |
|---|---|
| 基线 | `integration/t068-t082` @ `57d90df` |
| `db/migrations/0045_deletion_copy_ledger.sql`（分配 0048；合并重编号） | `CD168C657D4180AC…` |
| `internal/deletion/ledger.go` | `A858C6AF0C919E15…` |
| `internal/deletion/copies.go`（gofmt 后） | `71F856C056A1A363…` |
| `internal/deletion/deletion.go`（tombstone 同事务追加账本） | `F465F7F4FEDCB033…` |
| `cmd/admin/deletion.go` | `E5561C5BA150EC5C…` |
| `cmd/maintenance/copies.go` | `2FD3C3BF97E14F1B…` |
| `internal/sandbox/sandbox.go`（sandbox id 目录名 + staging root env） | `1A8EDF152EC306F3…` |
| PostgreSQL / Go / Python / Node | 16.4 / go1.27.3 windows/amd64 / 3.12.2 / v24.13.0 |
| 测试输入 | 真实上传/封存字节；每测试唯一 sentinel（body/checkpoint payload/目录内容）与真实目录/句柄；无凭据进仓库 |

## 6. `partial` / `not_run` 清单（如实）

| 项 | 状态 | 原因 |
|---|---|---|
| 恢复目标来自 `pg_basebackup`/`pg_dump` 真实备份 | **not_run** | 测试用第二个真实 PG 16.4 库（编译 `admin migrate`）+ 按“恢复会带回的删除前行”seed；账本回放只依赖行状态，不依赖对象字节。真实备份工具链不在本机验收条件内 |
| 真实崩溃遗留的 sandbox 目录 | **partial** | 失败/清理用例使用真实主机目录、真实文件句柄、真实 `os.RemoveAll`，目录名由真实 attempt 的 execution unit id 派生；但没有真实杀死 WSL 沙箱进程来制造崩溃残留（WSL/runsc 时序不可控） |
| checkpoint 表锁/超时失败分支 | **not_run** | `cleanCheckpointsTx` 有 `lock_timeout=2s` 与 `scope_failed` 分支；本票注入的真实失败是沙箱 FS 共享冲突，未单独构造成锁竞争（代码路径未执行） |
| 真实第三方组件（Mem0）删除尝试 | **not_run** | T066 已提交 `do_not_adopt` 证据（SQLite/Qdrant 残留 6 处副本），组件未部署；本票只记录部署边界（默认 absent / 配置后 external），不宣称擦除 |
| 真实模型供应商保留 | **not_run** | dev-stub profile 无外部 provider；账本按 `platform.operations` 是否有 model 调用决定 vendor_copy 的 external/absent，未接真实 provider |
| 页面/DOM | **not_run** | 本票是运维 CLI 面；无新页面、无浏览器驱动（票面明示纯运维票不加无用管理页面） |
| 全量 `pytest tests` | **not_run** | BRIEF 禁止（>60min），orchestrator 合并后运行 |
| pre-0045 已有删除历史的账本回填 | **not_run** | 账本从 0045 起记录；升级前已存在的 tombstone 仍在 `deletion_jobs`，未回填（本批次基线无真实历史；如需迁移需单独一次性工具） |
| OTel/metrics | **not_run（本票无此要求）** | 沿用 T073 的结论：无 exporter，账本/报表只含 id/code |

## 7. 关键实现位置

- `internal/deletion/ledger.go`：哈希链、导出/导入/验链、四资源回放（含 derived
  document、memory epoch、conversation 请求取消）。
- `internal/deletion/copies.go`：四 scope reducer、真实 FS 清理、scope 清除规则
  （`ClearScopesTx`）、`job_completed` 的 `non_erasable` 声明。
- `internal/deletion/deletion.go`：`Delete` 在 tombstone 事务内追加账本决策。
- `cmd/admin/deletion.go`、`cmd/maintenance/copies.go`、`cmd/maintenance/main.go`、
  `cmd/admin/main.go`：CLI 接线与单 JSON stdout/exit 0/1/2 约定。
- `internal/sandbox/{sandbox.go,executor.go}`：目录名含 sandbox id + staging root env。
- `tests/support/t072.py` + `tests/f11/test_t072_*.py`：真实栈 helper 与 16 用例。
- ADR：`docs/adr/0027-f11-copy-cleanup-ledger.md`（编号按票面分配 0027；本 worktree
  下一个空位是 0025，合并不冲突即可）。
