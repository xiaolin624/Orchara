# F00B T022 验收：提交一个后台任务并在重启后得到唯一结果

- 任务：F00B / GitHub Issue #23（票 `T022`，规划 ID F00B.01+F00B.02），分支 `feat/f00b-f02a`
- 状态：**passed**（真实 PostgreSQL 16.4 + 真实编译 Go 二进制 + 真实 HTTP/mTLS；无 mock、无跳过）
- 代码提交：
  - `9c1777f`（`feat(f00b): durable background jobs, object store and the internal mTLS API`）
  - `b92d5f9`（`test(f00b): prove claim, retry, finalize guard, mTLS and polling on the real stack`）
  - 本报告、`PROGRESS.md`、ADR 0004 在随后的 `docs(f00b)` 提交中
- 主要文件：
  - `db/migrations/0009_background_jobs.sql`（新增：两表、完成记账列、索引、权限收口、偏离注释）
  - `internal/objectstore/{objectstore.go,objectstore_test.go}`（新增）
  - `internal/background/{job,controller,enqueue,claim,heartbeat,complete,sweep,read,handler}.go`（新增）
  - `internal/background/handlers/artifact_finalize.go`（新增：本批唯一真实 handler）
  - `internal/api/{internal,background}.go`、`internal/api/server.go`（内部 mTLS 路由 + 公共 GET job）
  - `cmd/api/main.go`（独立 TLS listener 接线）、`cmd/maintenance/{main,client,run}.go`、`cmd/admin/{main,background}.go`
  - `internal/config/config.go`（对象目录/重试退避/内部 mTLS/maintenance 配置，fail-closed）
  - `tests/support/`（真实栈共享支持包）、`tests/f00b/`（60 个集成测试）
  - `docs/adr/0004-f00b-f02a-boundaries.md`
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`E:\orchara-pg`）
  - Python 3.12.2；`cryptography` 36.0.2（生成 CA/服务端/客户端证书；**未新增 Python 或 Go 依赖**）
  - 环境 profile：`dev-stub`（`ORCHARA_PROFILE=dev-stub`）
  - 基线（T021 之后）：`product/tests` = 529 passed、`tests/f01` = 155 passed；
    本票后 `product/tests` = **589 passed**、`tests/f00b` = **60 passed**，无失败、无跳过
- 真实/桩分报：**全部为 real**。Redis 唤醒集成、S3/MinIO、签名 URL、上传 producer、真实 worker
  协议不在本批（见 §5，分别记 `not_run` 或由后续票承接）。没有 stub 结果被当成通过。

## 1. 交付内容

### 1.1 迁移 `0009_background_jobs.sql`

- `platform.artifacts`：契约基线 + `revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1)`。
  理由：`background_jobs.expected_revision` 需要源版本；本批在状态/墓碑变更时同事务递增。
- `platform.background_jobs`：契约基线 + `finished_at`、`failure_class`、`last_error_at`、
  `result_event_digest`；`UNIQUE(kind, dedup_key)`、`result_event_id UNIQUE`；
  `CHECK` 强制“终态必有 finished_at、非终态必无”。
- 索引：契约的 `background_jobs_ready (available_at,id) WHERE state='ready'` 保留（claim 扫描）；
  新增 `background_jobs_lease_expiry (lease_deadline) WHERE state='leased'`（过期恢复）与
  `background_jobs_owner (owner_id, created_at DESC)`（owner 诊断）。
- 权限：`REVOKE ALL ON SCHEMA platform FROM PUBLIC`；`orchara_worker` 无 platform USAGE/表/序列权限
  （与 0006–0008 相同的收口）。每条偏离都在文件头/`COMMENT` 写明原因与承接票（T024/T026）。
- 幂等：`IF NOT EXISTS` 全量；空库 `0001→0009` 一次通过；重复 `admin migrate` 输出
  `no pending migrations`。

### 1.2 `internal/objectstore`

- 本地 FS，根 `ORCHARA_OBJECT_DIR`；`staging/`、`sealed/`；key 只能是
  `staging/<64 小写 hex>` / `sealed/<64 小写 hex>`（目录由前缀决定，拒绝路径穿越）。
- 写入 = 同目录临时文件 + `rename`（全有或全无）；单对象上限 20 MiB（与契约
  `size_bytes.maximum` 一致）；写入与重读都流式计算 SHA-256；`Delete` 幂等。
- `PutSealed(key, r, limit)` 只接受 sealed key：finalize 填充预分配 key，重试重写同一 key；
  staging key 永不能当 sealed 目标。未配置根目录时构造/操作 fail closed。

### 1.3 `internal/background`（控制器）

- 固定 8 kind 白名单（与迁移 CHECK、票面一致）；`ParseKinds` 拒绝未知/重复/空。
- `EnqueueTx`：与资源同事务；`INSERT ... ON CONFLICT (kind,dedup_key) DO NOTHING`；已存在时比较
  `target/owner/expected_revision/expected_sha256/input_artifact_id`，全同返回既有 job（同 id），
  任一不同 → `DedupConflictError`，不写行。终态 job 仍占用其 dedup key（重试需新 key）。
- `Claim`：`FOR UPDATE SKIP LOCKED`、`available_at <= now()`、kind ∈ 客户端 supported_kinds；
  `attempt_count+1`、新 30s 租约与 token；claim 事务内先恢复过期租约。
- `Heartbeat`：仅 `leased + token 匹配 + lease_deadline > now()` 续租 30s。
- `Complete`：同一事务行锁；`state=leased + token + 未过期` 才受理；终态事件幂等（同 event id +
  同请求摘要返回已记录结果，同 event 不同内容/第二个终态事件 `409 event_conflict`）；失败按
  kind 固定重试表分类，未知码按 business（fail-safe）；`attempt_count<3` 时指数退避回 ready，
  第 3 次 failed。
- `SweepExpired`：过期租约 → `lease_expired`/infrastructure；<3 次回 ready + 退避，第 3 次 failed。
- 读模型 `Get`：owner-or-admin 一条 SQL 判定，其余 `ErrNotFound`。
- 唤醒缝（复核修复 2026-11-02）：`internal/background/notifier.go` 定义 `Notifier`
  （非阻塞、无错误）与默认 `NopNotifier`；本批唯一实现 `InProcessNotifier` 是无缓冲
  channel（`Deliveries()` 接收端，无消费者即丢弃并回调 `onDrop`）。`Controller.Enqueue`
  在提交后通知，`ArtifactStore.CompleteUpload` 在 finalize job 提交后通知；`cmd/api` 以
  “无消费者”的方式安装它并把第一次丢弃记进日志。唤醒只是建议，PostgreSQL 轮询仍是唯一
  交付权威（ADR §45）。Redis adapter 仍 `not_run`。
- 错误码/失败分类/隔离集合均为导出常量；本包无 `os/exec`，handler 是编译期 kind→func 映射。

### 1.4 `artifact_finalize` handler + 服务端守卫

- handler（`internal/background/handlers`）：用 maintenance 身份读取 artifact 只读行（T022 还没有
  job-scoped artifact-status 端点，属 T024；见 ADR §9），校验墓碑/`state=scanning`/`expected_revision`，
  流式读 staging → 20 MiB 上限 → 写预分配 sealed key；size/hash 不符时删除 sealed 副本并报告
  business 失败。handler 不写任何业务行。
- 服务端守卫（`internal/background/complete.go`，与激活同事务）：持 artifact 行锁，要求
  `deleted_at IS NULL`、`state='scanning'`、`revision=expected_revision`，重读 sealed 对象并重新
  计算 SHA-256/大小；通过才 `state='ready', sha256=实际值, revision+1`。hash/大小/对象缺失/无预期
  hash → `quarantined`（revision+1）+ business failed；墓碑/改版本 → `source_deleted`/`source_changed`
  且不激活。`artifact_finalize` 的 succeeded 不接受 worker 提供的 `result_artifact_id`（422）。
  API 未配置对象目录 → Complete 返回 `503 object_store_unavailable` 且不改变任何行。
- 任何失败路径都不会把 artifact 置为 ready（测试逐条覆盖，见 §2）。

### 1.5 内部 mTLS API 与公共 GET

- `cmd/api` 在 `ORCHARA_INTERNAL_ADDR` 非空时另起 TLS listener：`RequireAndVerifyClientCert`、
  TLS ≥1.2、客户端 CA 由 `ORCHARA_INTERNAL_CLIENT_CA` 指定；四个变量不全或证书不可读 → 拒绝启动。
- 授权：证书 `Subject.Organization` 必须恰好一个且 ∈ {supervisor, maintenance, worker}；
  `X-Internal-Role` 必须存在且与证书 Organization 完全相等；claim/heartbeat/result 还要求
  maintenance 容量，否则 `403 forbidden`。内部接口逐请求校验 job token/租约。
- 路由：`POST /internal/v1/background/jobs/claim`（200 BackgroundLease / 204 无任务）、
  `.../{job_id}/heartbeat`（200 Accepted）、`.../{job_id}/result`（200 BackgroundJob）、
  内部 `/health/live`。响应体严格按契约字段；`input_url` 恒为 null（T024 才发签名 URL）。
- 公共 `GET /api/v1/background-jobs/{job_id}`：会话或 API key 认证，owner-or-admin；其余一律
  404 `resource_not_found`；响应只有 `id/kind/state/error_code`。

### 1.6 `cmd/maintenance run`

- 新增 `run` 子命令：claim（固定 kind 集合可 `--kinds` 收窄）→ 立即心跳一次 → handler →
  按 `ORCHARA_BACKGROUND_HEARTBEAT_INTERVAL`（默认 5s）定时心跳 → 提交结果；`--once` claim 到 204
  后退出，`--poll-interval` 控制空轮询间隔；每 job 一行 JSON，最后一行 summary。
- 无参数连通性检查行为逐字保留（仍用 `ORCHARA_MAINTENANCE_DSN`，输出 JSON，失败 exit 1）。
- 配置 fail-closed：`ORCHARA_INTERNAL_URL`（必须 https、无凭据/路径/查询）、客户端证书/Key/CA、
  `ORCHARA_OBJECT_DIR`、`ORCHARA_MAINTENANCE_DSN` 缺一即拒绝启动。

### 1.7 `cmd/admin background-jobs enqueue`

- 正式控制接口：`--kind`（8 kind 白名单）、`--target-id`、`--owner-id`、`--dedup-key` 必填；
  `--expected-revision/--expected-sha256/--input-artifact-id` 可选。
- 形状错误（未知 kind、`sh -c ...`、坏 UUID、坏 hash、显式 0 revision、多余参数）exit 2 且不写行；
  去重冲突/数据库拒绝 exit 1；成功打印 `{"id","state","kind"}`。
- `--owner-id` 必填并已文档化：CLI 没有用户会话，owner 必须由操作者显式给出。

### 1.8 配置（`internal/config`）

| 变量 | 用途 | 默认/失败行为 |
|---|---|---|
| `ORCHARA_OBJECT_DIR` | 对象存储根 | 空 = 允许启动但对象操作 fail closed |
| `ORCHARA_BACKGROUND_RETRY_BACKOFF` | 重试退避基数（Go duration） | `5s`；非法/≤0/>1h 拒绝启动 |
| `ORCHARA_BACKGROUND_HEARTBEAT_INTERVAL` | maintenance 心跳间隔 | `5s`；≤0 拒绝 |
| `ORCHARA_INTERNAL_ADDR` | 内部 listener 地址 | 空 = 不开内部端口；非空则其余三项必填 |
| `ORCHARA_INTERNAL_SERVER_CERT/KEY`、`ORCHARA_INTERNAL_CLIENT_CA` | 服务端证书/私钥/客户端 CA | 任一缺失拒绝启动；文件不可读拒绝启动 |
| `ORCHARA_INTERNAL_URL`、`ORCHARA_INTERNAL_CLIENT_CERT/KEY`、`ORCHARA_INTERNAL_CA` | maintenance 客户端 | 缺一拒绝启动；URL 必须 https |
| `ORCHARA_MAINTENANCE_DSN` | maintenance 自己的数据库身份 | `run` 缺之拒绝；不读 API 的 `ORCHARA_DB_DSN` |

## 2. 验收标准逐条

| # | 验收标准（票面） | 证据（真实命令的测试） | 结果 |
|---|---|---|---|
| 1 | Redis 清空仍能从 PG 轮询推进 | **复核修复后的证据**：`test_t022_polling.py::test_work_created_after_the_poller_started_is_picked_up`（先启动真实维护轮询进程、后创建两个 job，均从 PG 领取并完成）证明空轮询后仍有推进；`test_t024_artifacts.py::test_a_dropped_wakeup_never_loses_the_finalize_work` 在真实栈上让 API 的 `InProcessNotifier` **丢弃每一次唤醒**（无消费者，日志记录 drop），已启动的 maintenance 仍 claim→finalize→ready。Redis 适配器本身仍 `not_run`（本机无实例）；本行不声称清空过 Redis 队列（§5.3、ADR §45） | passed（唤醒缝实测；Redis adapter not_run） |
| 2 | 同键不双建 | `test_t022_enqueue.py::test_the_same_request_is_idempotent`（同 id、1 行）、`::test_a_different_target_under_the_same_key_conflicts`（exit 1、1 行）、`::test_a_different_kind_with_the_same_key_is_a_second_job` | passed |
| 3 | 用户 shell 文本不能成为 handler | `test_t022_enqueue.py::test_bad_command_lines_exit_2_without_writing[shell text as kind]`、`::test_the_maintenance_registry_refuses_uncompiled_or_unknown_kinds`、`test_t022_lease.py::test_claim_and_result_validation`（claim 422）、`test_t022_migration.py::test_the_database_refuses_an_unknown_kind`（DB CHECK） | passed |
| 4 | 旧 token 提交拒绝 | `test_t022_lease.py::test_the_old_token_is_refused_after_the_lease_was_recovered`（重领后旧 token 409、job 不变）、`::test_a_result_after_the_lease_expired_changes_nothing`（租约过期后提交 409、无副作用）、`::test_claim_leases_one_job_and_heartbeat_renews_it` | passed |
| 5 | 源修改/删除后不能激活旧结果 | `test_t022_finalize.py::test_a_deleted_artifact_can_never_be_activated`、`::test_a_revision_change_after_the_claim_cannot_activate`、`::test_a_successful_claim_after_a_tombstone_is_refused_by_the_guard`、`::test_the_guard_rehashes_the_sealed_object_instead_of_trusting_the_worker` | passed |
| 6 | 业务错误不盲重试 | `test_t022_lease.py::test_a_business_failure_is_never_retried`、`::test_an_unknown_error_code_is_treated_as_business`、`test_t022_finalize.py::test_a_hash_mismatch_is_quarantined_by_the_real_handler`（一次即 failed、quarantined） | passed |
| 7 | 端到端演示，失败/授权/重试边界有断言 | `test_t022_lifecycle.py::test_a_finalize_job_runs_end_to_end`（admin CLI → 真实 maintenance → 内部 mTLS → handler → 服务端守卫 → ready+sealed bytes）；`test_t022_lease.py::test_a_transient_failure_is_retried_then_fails_on_the_third_attempt`、`::test_an_expired_lease_is_recovered_and_the_retry_succeeds`；`test_t022_job_get.py::test_the_owner_other_users_and_an_admin_get_owner_or_404`；`test_t022_mtls.py` 全部 | passed |
| 8 | 记录版本/输入/hash/命令/原始结果/环境，stub-real 分报 | 本报告 §1–§5；环境 profile 与版本见页首；`input_url=null`、Redis、S3 等未运行项逐条列出 | passed |

票面补充（“完成口径”）：T022 是运维/控制类票，正式入口是 admin CLI + 内部协议 + 公共 REST
读接口；按票面“纯探针/运维/训练票使用对应 CLI 或实验报告，不人为添加无用管理页面”，本票
**没有**新增 Web 页面。用户可见的 artifact/上传界面属 T024/F03。

“重启后得到唯一结果”的证据口径（如实）：本票用**被弃置的租约**表示进程在 claim 后死亡——
`test_an_expired_lease_is_recovered_and_the_retry_succeeds` 中第一次 claim 的进程不提交结果，
租约过期后由**新启动的** maintenance 进程恢复并完成，最终恰好一条 succeeded 结果、artifact
只激活一次。没有做字面意义的 SIGKILL 注入（handler 均为毫秒级，无法在不加测试钩子的前提下
稳定卡在执行中）；该限制记在 §5。

## 3. 测试真实性说明

- 每个测试用例独立：`CREATE DATABASE` 一次性库、`cmd/admin migrate` 真实迁移、`cmd/admin bootstrap`
  真实管理员、`cmd/api` 真实进程（公共 HTTP + 内部 HTTPS）、`cmd/maintenance`/`cmd/admin` 真实二进制。
- 对象字节是真实文件：Python 侧把 staging/sealed 内容写入 `ORCHARA_OBJECT_DIR`，Go 侧 handler/守卫
  用同一目录真实读取；哈希由两侧独立计算。
- 内部协议是真实 TLS：`tests/support/certs.py` 用 `cryptography` 生成 CA、带 SAN
  `IP:127.0.0.1`/`DNS:localhost` 的服务端证书、按角色（Organization）签发的客户端证书，以及
  “错误 CA”证书；客户端用 `http.client.HTTPSConnection` + 客户端证书真实握手。
- 边界：T022 没有上传 producer（T024 的 `completeUpload`），测试用 SQL 播种 `platform.artifacts`
  行（真实表/真实约束）并写真实 staging bytes；handler 用 maintenance DSN 读取 artifact 行
  （契约的 job-scoped 端点属 T024）。见 ADR §9。
- 租约过期不靠 wall clock：测试直接把 `lease_deadline` 移到过去（SQL），租约判定由数据库
  `now()` 单一时钟完成；只有“配置的 50ms 退避已过”这类有界轮询（`wait_until`/`retry_until_claimed`，
  上限 5s）使用短等待。
- 失败面断言均来自真实响应/真实行/真实文件，不以进程内 mock 代替。

## 4. 门禁命令与原始结果

> 以下命令/计数是**本票完成时**（2026-11-02 早于两轴复核修复）的原始结果。复核修复后的最新门禁
> （全量 `pytest tests`、Go、sync_spec）见 `PROGRESS.md` 的“F00B/F02A 两轴复核修复”一节；
> `tests/f00b` 的用例数在该节更新，本票自身证据不变。

工作目录 `E:\Orchara\product`；Go 环境 `GOCACHE=E:\orchara-toolchains\gocache`、
`GOMODCACHE=E:\orchara-toolchains\gomodcache`。

```powershell
E:\orchara-toolchains\go\bin\go.exe vet ./...
# (no output)  exit 0

E:\orchara-toolchains\go\bin\gofmt.exe -l internal cmd tools
# (no output)  —— 无未格式化文件

E:\orchara-toolchains\go\bin\go.exe test ./...
# ok  orchara/platform/internal/api           1.454s
# ok  orchara/platform/internal/background    (cached)
# ok  orchara/platform/internal/catalog       0.920s
# ok  orchara/platform/internal/config        (cached)
# ok  orchara/platform/internal/db            (cached)
# ok  orchara/platform/internal/health        (cached)
# ok  orchara/platform/internal/identity      1.686s
# ok  orchara/platform/internal/objectstore   (cached)
# ok  orchara/platform/internal/policy        0.714s
# ok  orchara/platform/internal/supervisor    (cached)
# ok  orchara/platform/internal/uuid          0.484s
# (cmd/* 与 db 无测试文件)

python -m pytest tests/f00b -q
# 60 passed in 138.60s (0:02:18)

python -m pytest tests/f01 -q
# 155 passed in 449.85s (0:07:29)

python -m pytest tests/tooling -q
# 21 passed in 4.78s

python tools/sync_spec.py --check
# no drift: spec copy and contract snapshot match the manifest

python -m pytest tests -q          # 全量（时间允许时必跑）
# 589 passed in 733.56s (0:12:13)
```

`git status --short`：本票开始与结束均为空（docs 提交后复核）；未 push、未关闭 GitHub issue、
未修改 `product/docs/spec/**`、`product/contracts/**` 或已应用的迁移 0001–0008。

## 5. 偏离、契约缺口与未运行项（如实）

### 5.1 迁移/数据模型偏离（已在 0009 注释与 ADR §1/§2 记录）

| 偏离 | 原因 | 承接 |
|---|---|---|
| `artifacts.revision` | `background_jobs.expected_revision` 无源版本 | T024 插入/递增，后续票保持 |
| `background_jobs.finished_at/failure_class/last_error_at` | 完成/失败分类记账 | T022 使用，后续票沿用 |
| `background_jobs.result_event_digest` | 区分“同 event 重放”与“同 event 不同内容” | T022 使用 |
| 额外两个索引 | 过期租约扫描与 owner 诊断 | T026 孤儿清理可复用 |

### 5.2 契约缺口（不改 contracts，按最不意外的行为实现）

1. `BackgroundLease.input_url` 在 T022 恒为 `null`：签名 URL 属 T024；客户端不得把 null 当可自造 URL。
2. 后台 job 的 API-key scope 未定义（F01 只有 `application:read`）；GET 仅 owner-or-admin。
   T024 若要给服务账号读权限，应新增专用 scope。
3. T022 没有 job-scoped artifact-status/download 端点（T024），finalize handler 过渡期用
   maintenance DSN 只读 `platform.artifacts`；激活信任仍在服务端。见 ADR §9。
4. `contracts/openapi.json` 还有其他票的内部端点（model-calls、artifact-uploads/
   downloads/status、`/internal/v1/runs/...`），本票未实现、也未注册路由；请求得到 404。

### 5.3 未运行（`not_run`）/未实现

| 项 | 状态 | 说明 |
|---|---|---|
| Redis 唤醒总线集成 | `not_run`（仅 Redis 传输） | 唤醒缝已交付：`background.Notifier` + 默认 `NopNotifier` + `InProcessNotifier`（无消费者即丢弃）；真实栈测试证明每次丢弃后 PG 轮询仍 claim/完成（§2 AC1）。**Redis 客户端/实例传输**仍未实现（本机无实例），不得写成 Redis 已验证 |
| S3/MinIO 对象存储 | `not_run` | 仅本地 FS 实现（D2） |
| 签名上传/下载 URL、`input_url` | `not_run` | T024 |
| 上传 producer（`completeUpload` → 自动创建 finalize job） | `not_run` | T024；本票用 SQL 播种真实 artifact 行 |
| 真实 worker 协议 `/internal/v1/runs/...` | `not_run` | F03；本批只有 maintenance 后台协议 |
| 病毒查杀/深格式扫描 | `not_run` | F05/T028；本批只有 hash/大小/墓碑/媒体类型准入 |
| 字面 SIGKILL/进程重启注入 | `not_run` | 用弃置租约模拟；见 §2 说明 |
| F02A 表/端点 | 未开始（按计划） | T023+ |

## 6. 下一票（T023/T024/T026/T027）可依赖的接口

**表**

- `platform.artifacts(id, owner_id, object_key, staging_object_key, expected_sha256, purpose, sha256,
  media_type, size_bytes, state∈{uploading,scanning,ready,quarantined,deleted}, revision, created_at,
  deleted_at, purged_at)`。`object_key` 必须写成 `sealed/<64hex>`；`staging_object_key` 写成
  `staging/<64hex>`。任何状态/墓碑变更**必须** `revision = revision + 1`。
- `platform.background_jobs(id, kind∈8, target_id, owner_id, expected_revision, expected_sha256,
  dedup_key, input_artifact_id, result_artifact_id, state, available_at, lease_token, lease_deadline,
  worker_id, attempt_count, result_event_id, result_event_digest, error_code, created_at, finished_at,
  failure_class, last_error_at)`；`UNIQUE(kind,dedup_key)`；终态行占用 dedup key。

**Go**

- `internal/objectstore`: `Open(root)`, `StagingKey()`, `SealedKey()`, `CreateStaging/CreateSealed`,
  `PutSealed`, `Open`, `Hash`, `Stat`, `Delete`; `MaxObjectBytes = 20 MiB`。
- `internal/background`: `EnqueueTx(ctx, tx, EnqueueParams) (*Job, created bool, error)`,
  `Controller.{Enqueue,Claim,Heartbeat,Complete,SweepExpired,Get}`, `Registry`/`Handler`,
  `Retryable/ClassifyFailure/ImpliesQuarantine`, 全部错误码常量；复核修复后 `New(db, store,
  retryBackoff, notifier)` 与 `Notifier`/`NopNotifier`/`InProcessNotifier`（唤醒是建议，轮询是权威）。
- `internal/background/handlers.Registry`（编译期 handler 映射；新 kind 必须同时扩展
  `retryableByKind`）；完成侧的 kind 注册表是 `background.CompletionGuard`。
- `internal/api`: `Server.RegisterInternal(mux)`、`RequireInternalMTLS`、`InternalRole(ctx)`；
  `api.New(api.Deps{...})`（复核修复后的命名结构体签名，见 ADR §51）。

**HTTP**

- 内部：claim（200 `BackgroundLease`/204）、heartbeat（200 `Accepted`）、result（200 `BackgroundJob`；
  409 `stale_attempt`/`event_conflict`；422 `validation_failed`+`details.field`；503
  `object_store_unavailable`；404 `job_not_found`）。
- 公共：`GET /api/v1/background-jobs/{job_id}`（owner-or-admin，404 其余，四字段）。

**环境**

- API：`ORCHARA_OBJECT_DIR`、`ORCHARA_BACKGROUND_RETRY_BACKOFF`、
  `ORCHARA_INTERNAL_ADDR/SERVER_CERT/SERVER_KEY/CLIENT_CA`。
- maintenance：`ORCHARA_INTERNAL_URL`、`ORCHARA_INTERNAL_CLIENT_CERT/KEY`、`ORCHARA_INTERNAL_CA`、
  `ORCHARA_OBJECT_DIR`、`ORCHARA_MAINTENANCE_DSN`、`ORCHARA_BACKGROUND_HEARTBEAT_INTERVAL`。
- 测试：`tests/support/`（`stack`, `fixtures`, `certs`, `f00b`）；`tests/f00b` 的 `t022_stack`
  起真实 API + 内部 mTLS + 对象目录。

## 7. 工作日志（供续接）

1. 读 brief/PLAN/T022/BACKGROUND-JOBS/契约/F01 模式；确认分支与干净树。
2. 写 0009 迁移并在临时库跑通 `0001→0009` + 幂等复跑。
3. 实现 objectstore（含 `PutSealed`）→ background 控制器（enqueue/claim/heartbeat/complete/
   sweep/read + 守卫）→ handlers registry → config → internal API/mTLS → cmd/api listener →
   cmd/maintenance run → cmd/admin enqueue。
4. 建 `tests/support/`（PG/二进制/证书/对象/mTLS/CLI helper）与 `tests/f00b/` 8 个文件共 60 用例；
   逐个文件跑绿，再跑全量。
5. 门禁：gofmt/vet/go test、f00b、f01、tooling、sync、全量 `pytest tests` 全绿。
6. 提交 `9c1777f`（代码）与 `b92d5f9`（测试）；随后 docs 提交（本报告 + PROGRESS + ADR 0004）。

## 8. 剩余阻塞

无阻塞。未运行项见 §5.3，均属后续票或本机无该外部组件，不影响本票验收。
