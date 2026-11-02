# ADR 0004: F00B/F02A 后台任务、对象与内部协议的边界

- 状态：已接受
- 日期：2026-11-02
- 相关：F00B / T022（GitHub #23）；后续 T023–T027 在本文件追加各自小节；`product/reports/f00b/T022-background-jobs.md`

## 背景

T022 首次把后台任务从规格变成可运行实现：一个正式控制接口创建固定 handler 的工作，维护进程通过
内网 mTLS 领取、心跳、提交结果，服务端守卫决定产物是否能被激活。实现里有一批“顺手就能做、但
不做才正确”的边界；后续票（T024 上传与模型调用、T026 评测与孤儿清理、T027 debug 排队）必须
沿用，否则会把限制当成遗漏，或提前把别票的工作塞进 T022。

## 决定

### 1. `platform.artifacts.revision` 是后台任务唯一的源版本

`contracts/database.sql` 没有 `revision` 列，但 `background_jobs.expected_revision` 需要一个可比较的
来源版本。0009 给 `artifacts` 加 `revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1)`，语义是
**artifact 的源版本**：任何状态/墓碑变更都在同一事务里 `revision = revision + 1`（最终激活、隔离、
删除均如此）。T022 的 finalize 守卫以它拒绝“领取后源已变化”的结果。

后续约束：T024 的 `completeUpload` 必须显式插入 `revision = 1`（或依赖默认值）并在从
`uploading → scanning` 的状态迁移中递增；任何写 artifact 状态的新路径都必须递增 revision，否则
`expected_revision` 守卫会失去意义。不得回填 0009，改动走新迁移。

### 2. `background_jobs` 的完成记账列是对 `database.sql` 的显式偏离

0009 在契约基线上增加四列，每个都有唯一理由：

| 列 | 理由 |
|---|---|
| `finished_at` | 终态时间；`CHECK` 强制“终态必有、非终态必无”，控制器与数据库不能各说一套 |
| `failure_class` | `business` 立即失败、绝不重试；`infrastructure` 指数退避、最多三次。空表示尚未分类 |
| `last_error_at` | 最近一次被接受的失败时间，用于诊断“ready 但上一次是失败”的状态 |
| `result_event_digest` | 结果事件的请求内容摘要（state/error_code/result_artifact_id 的 SHA-256） |

`result_event_id UNIQUE` 是幂等键：同一 job 的同一 event id + 同一摘要返回已记录的结果（200，
不重复计数）；同 event 不同内容、或同一 job 的第二个终态事件是 `409 event_conflict`。摘要列是
必要的，因为服务端守卫可以把“worker 声称成功”改判为失败——重放必须返回**记录的结果**，不能
拿请求内容去反推结果。后续票不得删除/重定义这些列；新增列走各自迁移。

### 3. 去重身份是“同一意图”，不是“同一个键”

`UNIQUE(kind, dedup_key)` 是幂等的物理保证。`EnqueueTx` 对已存在行的比较字段是
`target_id + owner_id + expected_revision + expected_sha256 + input_artifact_id`：全相同返回既有 job
（同 id、不新建），任一不同返回 dedup 冲突且**不写任何行**。

推论（后续票必须知道）：**终态 job 仍占用它的 dedup key**。失败后想重试同一目标必须使用新的
dedup key（新的意图）；不能靠重复 enqueue 把 failed job 复活。T024 的 `completeUpload` 应把
artifact id + 期望 hash 编入 dedup key，这样“同一上传重复 complete”幂等、“不同内容”冲突。

### 4. 领取、租约与重试只有数据库时钟一个权威

`Claim` 用 `FOR UPDATE SKIP LOCKED` 领取 `state='ready' AND available_at <= now()` 的最老一行，
生成新的 `lease_token`，把 `attempt_count + 1`，租约 30s。`Heartbeat` 只在 `state='leased'` +
token 匹配 + `lease_deadline > now()` 时续租。`Complete` 用同一组条件判定，失败一律
`409 stale_attempt` 且不改任何业务事实。

过期租约由**下一次 claim**（同一事务内）或 `SweepExpired` 恢复：`failure_class=infrastructure`、
`error_code=lease_expired`；`attempt_count < 3` 时回到 ready，按 `base * 2^(attempt-1)` 退避
（base 由 `ORCHARA_BACKGROUND_RETRY_BACKOFF` 配置，默认 5s，上限 1h）；第 3 次直接 failed。
“最多三次”覆盖 `lease_expired` 与可重试业务码，两者共用 `attempt_count`。

每 kind 的可重试码表固定在 `internal/background/job.go`（`retryableByKind`），T022 只有
`artifact_finalize`：`transient_storage_io`、`transient_internal`、`lease_expired`。**未列入的码一律
business（fail-safe）**，新 handler 票必须在同一变更里扩展该表，不能靠字符串推断。

### 5. 激活权威只在服务端：handler 永远不能让 artifact 变 ready

`artifact_finalize` 的 handler 只做“读 staging → 限长/hash → 写预分配 sealed key → 报告”；它不写
任何业务行。`Complete` 在同一事务里持 artifact 行锁并核对：

- `deleted_at IS NULL`，否则 `source_deleted`；
- `state='scanning'`，否则 `artifact_state_not_scanning`；
- `expected_revision` 等于当前 `revision`，否则 `source_changed`；
- 重新读取 sealed 对象并**流式重算 SHA-256/大小**，与 `expected_sha256`/`size_bytes` 一致；
- 通过后才 `state='ready', sha256=实际值, revision+1`。

hash/大小/缺失/无预期 hash 一律 `quarantined`（revision+1），job 以 business 失败；任何失败路径都
不得把 artifact 置为 ready。`artifact_finalize` 的成功结果**不接受 worker 提供的
`result_artifact_id`**（422）：该 job 的结果就是目标 artifact，服务端不接受第二个未经验证的引用。
API 未配置对象存储时，任何需要验证的完成请求返回 `503 object_store_unavailable` 且不改变任何行。

后续票：T024 的 `completeUpload` 只创建 artifact/job，不激活；T026 的孤儿清理删除 sealed/staging
残留，但不复活 quarantined。

### 6. 本地对象存储的 key 语法与写入语义是共享契约

`internal/objectstore` 的 key 只有两种：`staging/<64 位小写 hex>` 与 `sealed/<64 位小写 hex>`；
目录由 key 前缀决定，调用方不能提供路径。写入 = 同目录临时文件 + `rename`（全有或全无），单对象
上限 20 MiB（契约 `size_bytes.maximum`），写入/重读都流式计算 SHA-256。`PutSealed` 只接受 sealed
key（预分配后由 finalize 填充，重试重写同一 key）；staging key 永远不能被当作 sealed 目标。

签名 URL、S3/MinIO、病毒查杀/深格式扫描不在 T022：T024 加签名 URL，F05/T028 承接扫描。未配置
`ORCHARA_OBJECT_DIR` 时 store 构造失败/操作 fail closed，不回退到临时目录。

### 7. 内部协议是独立 TLS listener，角色 = 证书 Organization + 同名头

`cmd/api` 在 `ORCHARA_INTERNAL_ADDR` 非空时另起一个 TLS listener（`RequireAndVerifyClientCert`，
TLS ≥ 1.2，客户端 CA 由 `ORCHARA_INTERNAL_CLIENT_CA` 指定）。四个变量必须同时配置，任一缺失或
证书不可读都拒绝启动；没有“半开”的内部端口。

授权分两层，缺一不可：

1. 证书 `Subject.Organization` 必须恰好一个值，且属于 `supervisor`/`maintenance`/`worker`；
2. `X-Internal-Role` 头必须存在且与证书 Organization 完全相等，否则 403。

claim/heartbeat/result 是 maintenance 容量端点：worker/supervisor 证书即使头部一致也 403。
内部接口在角色之外仍逐请求校验 job 的 attempt_token/租约，角色不替代租约。公共 listener 不注册
`/internal/v1/**`（请求得到 404），内部 listener 不注册用户/页面路由。

### 8. handler 是编译期映射，白名单 kind ≠ 已实现 handler

`internal/background/handlers.Registry` 是唯一的 kind→func 映射；`cmd/maintenance run --kinds`
**只能收窄**这个集合，不能引入新 handler。`--kind sh -c ...` 在 admin CLI、数据库 CHECK、claim
白名单、registry 四个入口都被拒。八个固定 kind 里 T022 只实现 `artifact_finalize`；其余 kind 的
job 会停在 ready，直到拥有 handler 的票（T024/T026）注册。这是诚实状态，不是遗漏。

### 9. T022 的过渡读取：handler 通过 maintenance DSN 读 artifact 行

契约里 job-scoped 的 artifact-status/download 端点属于 T024；T022 的 handler 需要 staging/sealed
key、expected hash 与 revision，因此直接用 maintenance 数据库身份读 `platform.artifacts` 的**只读**
行。信任边界没有移动：激活仍只由服务端守卫完成，handler 的读不产生任何写。

T024 必须提供 `getBackgroundArtifactStatus`/`createBackgroundDownload` 等契约端点，并把 handler
迁到这些端点上；届时 `BackgroundLease.input_url` 也应从 null 变为签名 URL。在那之前
`input_url` 恒为 null，客户端不得把 null 当“可以自造 URL”。

### 10. 本批的创建入口与读权限

`cmd/admin background-jobs enqueue` 是 T022 的正式控制接口：`--kind/--target-id/--owner-id/
--dedup-key` 必填，`--expected-revision/--expected-sha256/--input-artifact-id` 可选；参数形状错误
exit 2，去重冲突/数据库拒绝 exit 1，成功打印 `{id,state,kind}`。`--owner-id` 必填是因为 CLI 没有
用户会话，owner 必须由操作者显式指定。

`GET /api/v1/background-jobs/{job_id}` 是 owner-or-admin，其余 404；响应只有契约四字段
(`id/kind/state/error_code`)，不泄漏 owner、target、dedup key 或 expected 事实。后台 job 的
API-key scope 尚未定义（F01 注册表只有 `application:read`）；产生后台 job 的 T024 若要给服务账号
读权限，应新增专用 scope，而不是复用 `application:read`。

### 11. 唤醒总线缺失时的推进方式：PG 轮询

本机没有 Redis，T022 不引入 Redis 客户端。`cmd/maintenance run` 以固定间隔（默认 2s）轮询 claim
端点；空轮询后创建的工作仍会被下一轮领取，因此“唤醒丢失”不会丢失工作。Redis 集成（通知型
wakeup）记为 `not_run`，等有实例的票再做；`SweepExpired` 的恢复不依赖任何唤醒总线。

## 后果

- 0009 之后，`platform.artifacts` 与 `platform.background_jobs` 的写入路径只有两条：T022 的
  controller/guard 与后续票的 producer；两者都必须遵守第 1/2/3/5 条的不变量。
- 任何新增 handler kind 的票必须同时：注册 `handlers.Registry`、扩展 `retryableByKind`、决定
  quarantine/失败语义，并在同一 PR 里给出真实后台进程的测试。
- 内部 listener 的错误面（403/409/503）是契约的一部分；不要用更改状态码来“方便客户端”。
- Redis、S3/MinIO、真实 worker 协议（`/internal/v1/runs/...`）、签名 URL、上传 producer、深度扫描
  都是显式未完成项，分别由后续票承接；不得在 T022 报告里写成已通过。

## 证据

- `product/db/migrations/0009_background_jobs.sql`（两表、偏离注释、索引、权限收口）
- `product/internal/background/`（完成语义、守卫、重试表、读模型）、`internal/background/handlers/`
- `product/internal/objectstore/`（key 语法、原子写、上限、流式 hash）
- `product/internal/api/{internal,background}.go`、`product/cmd/api/main.go`（mTLS listener）
- `product/cmd/maintenance/{run,client}.go`、`product/cmd/admin/background.go`
- `product/tests/f00b/`（真实 PG + 真实二进制 + 真实 mTLS HTTP）、`product/tests/support/`
- `product/reports/f00b/T022-background-jobs.md`（命令、原始结果、not_run 清单）
