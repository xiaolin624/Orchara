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

## T023 追加：草稿、版本读模型与编辑器（F02A.01+F02A.04）

T023 把“保存草稿/刷新恢复/冲突不覆盖”从规格变成可运行实现。以下决定为后续票必须沿用；改动需
新 ADR 或在本节追加修订理由。

### 12. 迁移 0010 的显式偏离与延期外键

- `application_drafts` 在契约基线上增加 `execution_revision bigint NOT NULL DEFAULT 1`。
  `revision` 是 ETag/CAS 计数器（每次成功保存 +1）；`execution_revision` 只在 `definition` 或
  `capability_card` 的 jsonb 值变化时 +1（`IS DISTINCT FROM`）。后者是 T025 manifest 的
  `source_revision`；只改 layout 不得改变执行 hash，不能靠“不保存 layout”规避。
- `publication_reviews.report_id` 按契约类型建列但**没有 FK**：`evaluation_reports` 属 T026，
  0010 不伪造该表。T026 建表后在自己的迁移里补 FK；在此之前任何代码不得把该列当已验证引用。
- `applications.active_version_id` 的 FK 用契约的复合形式
  `(id, active_version_id) -> application_versions(application_id, id)`，`ON DELETE RESTRICT`。
  复合形式保证活动版本属于同一应用；版本不可删除，撤销走 `revoked_at`，因此删除导致孤儿是
  bug，必须失败而不是静默置空。NULL（从未发布）不受约束。
- 0010 之后 `python -m pytest tests/f01` 仍须全绿：F01 的 seed 辅助函数现在为带
  `active_version_id` 的应用补真实 version/review/artifact 行（以前可指向不存在的 uuid）。
  这是 schema 收紧后的测试适配，不是放宽断言。

### 13. 草稿 ACL：读 owner-or-grant，写 owner-only；空草稿的契约缺口

- GET draft：owner 或未过期 `read` grant；其余（含无 grant 的管理员）一律 404，避免枚举。
- PUT draft：先做同一个读权限判定；能读但非 owner 返回 403 `forbidden`（调用者已知草稿存在，
  拒绝的是写权）；不能读仍返回 404。读写顺序固定为：可见性(404) → owner(403) →
  If-Match(428/412) → 结构校验(422) → CAS 更新。失败路径不写任何行。
- ETag 是强验证器 `"<revision>"`。缺失/空 `If-Match` = 428 `precondition_required`
  (`details.header=If-Match`)；过期、弱验证器、列表、`*`、不可解析值 = 412
  `precondition_failed`，`details` 带 `current_revision`/`current_etag`。成功 200
  `Accepted{state:"saved"}` 并返回新 `ETag`。并发写用 `UPDATE ... WHERE revision = <If-Match>`
  的零行结果判 412，所以两个持同一 ETag 的写者不可能都成功。
- **契约缺口（空草稿）**：`contracts/openapi.json` 的 `DraftInput.capability_card` 要求六个键
  且 `examples.minItems=2`，但新建草稿还没有用途卡。T023 用 `capability_card: {}` 表示“尚未
  填写”，接受 `{}` 原样保存，GET→PUT 空草稿可往返；任何非空卡都必须六键齐全且 examples≥2，
  绝不填充伪造示例。T025/T027 及 F02B 必须继续接受 `{}` 这一状态，不得按“必须先有卡”拒绝
  读取/调试/提交草稿；契约修订属后续票，T023 不改 contracts。

### 14. 版本读模型：只读不可变发布行

`getVersion`/`listVersions` 只返回 `application_versions` 的 `manifest`、`manifest_sha256`、
`version_no`（及 id/application_id），绝不 join `application_drafts`；不可见/已归档应用的版本
一律 404。列表的 ACL 谓词与 cursor、LIMIT 在同一条 SQL 的 WHERE 中（先过滤后分页），游标是
`(version_no DESC, id DESC)` 的不透明 base64 token；limit 默认 20、1..100，越界 422，
坏游标 400 `invalid_cursor`。发布行的 `active_version_id`、列表项和详情必须一致（测试用真实
播种行证明）。

### 15. 校验器移植：internal/workflow + internal/jcs（T025 复用，不得分叉）

- `internal/workflow` 是 `probes/p05_dsl/go` 的直移植：相同 stage 顺序、code、pointer 与
  优先级；`rules.json` 逐字复制并以 `go:embed` 打入包内，另有 Go 测试断言它与 probe 原件
  逐字节一致，防止单边漂移。`validate_corpus_test.go` 用 `probes/p05_dsl/corpus.json`
  对拍 verdict/reasons/input hash/normal-form hash/canonical 文本。
- `internal/jcs` 是 P01/T003 Go 实现的可用形态（probes 不动），用 `probes/jcs/vectors.json`
  的 40 条 RFC 向量做测试。T023 需要规范字节来实现校验器；T025 的 manifest 必须直接复用
  这个包，不得再复制第三份序列化器。
- `catalog.CheckExecutable(draft)` 是纯函数，返回 `{node_id?, code, pointer, message}`
  （node_id 只在 pointer 指向 `/nodes/<i>/...` 时解析；cycle 这类文档级错误的 pointer 为空）。
  T023 只用 Go 单测证明它能定位 cycle；**不提供** validate/debug/submit 端点。

### 16. T023 未完成、由后续票闭环的验收

- “非法草稿可保存但不能调试/提交”：保存半条已实现并测试；`validate`(T025)、`debug-runs`
  (T027)、`submit`(F02B) 端点不存在（测试断言 404）。该验收在 T023 只能记 `partial`，
  T025 闭环“不能调试/提交”的校验侧，T027 闭环 debug 的 revision 固定与拒绝语义。
- T025 必须用 `execution_revision` 作为 manifest `source_revision`，用
  `catalog.CheckExecutable`（或 workflow 包）而不是重写规则；`saveDraft` 继续不校验 DSL。
- T027 必须用 draft 行 `revision` 作为 debug 的 `draft_revision`，并保持本节的 404/403 与
  ETag 语义不变。

### 17. scope 词汇表由 T023 扩展（T020 的注册表注释授权的扩展点）

`identity` 增加 `application:write`：T020 的 `knownScopes` 注释明确“later ticket extends this
map together with whatever endpoint uses the scope”，而契约给 createApplication/saveDraft 列了
`apiKey` security，只有 `application:read` 时写路径对 scoped key 不可达。T023 让创建/保存要求
`application:write`，读取仍要求 `application:read`（编辑一个应用需要两把 scope）；会话不受
scope 限制。因此 F01 的两个测试随词汇表更新：`test_t020_api_keys.py` 的“未知 scope”用例改用
仍不存在的 `application:execute`，`test_t020_scope_and_acl.py` 的注释改为“write-scoped key 是
真实凭据”。这是实现扩展，不是契约修改。

## 后果（T023）

- 0010 之后草稿写入只有 `catalog.SaveDraft` 一条路；任何直接 UPDATE 绕过 CAS/ACL 的代码都是
  bug。T024 不碰本票表；T025/T027 只读草稿并按 `execution_revision`/`revision` 区分语义。
- 空 `capability_card: {}` 是受支持状态；前端、T025、T027、F02B 都必须容忍。
- `internal/jcs` 与 `internal/workflow` 成为共享实现，后续票只扩展不改语义；probes 保持
  原样作为独立真值。
- validate/debug/submit 的缺失是显式的，不是遗漏；T023 报告把相应验收记 `partial`。

## 证据（T023）

- `product/db/migrations/0010_application_drafts.sql`
- `product/internal/catalog/{draft,versions}.go`、`product/internal/api/{drafts,versions}.go`
- `product/internal/jcs/`、`product/internal/workflow/`
- `product/apps/web/src/{editor.html,assets/editor.js,assets/app.js,apps.html}`
- `product/tests/f02a/`、`product/tests/support/f02a.py`
- `product/reports/f02a/T023-drafts-and-editor.md`

## T024 追加：job 限定文件与受控模型调用（F00B.03）

T024 把“后台 handler 只能读获准文件、每次模型调用都有账”从规格变成可运行实现。以下决定
为后续票必须沿用；改动需新 ADR 或在本节追加修订理由。

### 18. 迁移 0011 的显式偏离与两个延期外键

- `platform.model_profile_versions`、`platform.platform_model_calls` 按 `contracts/database.sql`
  基线建表；`platform.background_job_artifacts` 是新增表（job→artifact 归属）。
- 偏离 1：`platform_model_calls.params_sha256 text`。契约基线没有地方存逻辑调用的参数，
  而 IMPLEMENTATION-DETAILS §4/§5 要求“同逻辑键不同参数”是冲突而不是被当成另一次调用悄悄计费。
  该列存 params 的 JCS SHA-256（`internal/jcs`），不存 params 本身。
- 偏离 2：`background_job_artifacts(background_job_id, artifact_id, relation, purpose, created_at)`。
  契约只用 `input_artifact_id`/`result_artifact_id` 关联 job 与 artifact，无法表达“job 产出的
  artifact 在 uploading/scanning 期间也归它”，也无法表达多个输出；job-scoped 访问需要这条
  归属行。PK 是 `(background_job_id, artifact_id)`，`relation ∈ {input, output}`，两外键
  `ON DELETE CASCADE`。
- 延期外键 1：`platform_model_calls.request_id` 保持裸 `uuid`，**没有 FK**。`platform.requests`
  属 F05/F07，本批不建；F05（或 F07 删除路径）在自己的迁移里补
  `platform_model_calls.request_id -> platform.requests(id)`。在此之前任何代码不得把该列当已
  验证引用。契约的 XOR CHECK（`request_id` 与 `background_job_id` 恰一非空）仍然由数据库强制。
- 延期外键 2：`model_profile_versions.evaluation_report_id` 保持裸 `uuid`。`evaluation_reports`
  由 T026（0012）创建并在其迁移里补 FK；在此之前不得视为已验证引用。
- 权限沿用 0007–0010：`REVOKE ALL ON SCHEMA platform FROM PUBLIC`，`orchara_worker` 无 platform
  USAGE/表/序列权限。`background_job_artifacts` 的归属行不是授权本身：每次读取仍要同时证明
  活租约与 scope。

### 19. 签名对象 URL 的模型与拒绝面

- 签名器在 `internal/objectstore`（`sign.go`），HMAC-SHA256，key 来自
  `ORCHARA_OBJECT_SIGNING_KEY`（≥32 字节）。空 key 不阻止进程启动，但**铸造失败**（
  `503 object_signing_unavailable`）且**任何已签发链接被拒**；非空但过短的 key 是配置错误，
  拒绝启动。
- token 绑定 `op(put|get) + key + artifact_id + exp + max_bytes`，base64url(payload).base64url(mac)。
  `PUT` 只能指向 `staging/<64hex>`，其 max_bytes 是 artifact 声明的 size（0 表示只接受空体）；
  `GET` 只能指向 `sealed/<64hex>`。**签名器拒绝铸造 sealed PUT**，验证器拒绝 op 与 key 前缀
  不匹配的 token，因此泄露一个 GET 链接不可能变成写权限。
- 公共 listener 提供 `PUT/GET /object-links/{token}`，无会话、无 CSRF、无列表端点（presigned
  语义）。过期/篡改/换 key/换 op/sealed-PUT 一律 `404 not_found`，不泄露对象是否存在；超限
  是 `413 payload_too_large`（size 拒绝不是存在性泄露），且 store 的临时文件+rename 保证不
  留半截对象。
- 链接需要绝对 URL：新增 `ORCHARA_PUBLIC_URL`（默认 `http://<ORCHARA_HTTP_ADDR>`），
  `BackgroundLease.input_url`、`Upload.upload_url`、`Download.url` 都以它铸造。请求日志对
  `/object-links/` 路径脱敏，token 不落日志。
- 链接 TTL 由 `ORCHARA_OBJECT_LINK_TTL` 配置（默认 10m，上限 1h）。链接只用于一次传输，不是
  能力；对象 key 永不复用，sealed 永不给 PUT。

### 20. job-scoped artifact 规则

- 允许创建 artifact 的 kind 是固定集合 `{model_validate, application_evaluate}`（后者供 T026）；
  其他 kind 即使持有活租约也 422。新增 kind 必须在同一变更里扩展该集合。
- upload 预留：同一事务写 `artifacts(state='uploading', revision=1, purpose ∈ {report,log,
  receipt,parsed_data}, size_bytes ≤ 20MiB)` 与 `background_job_artifacts(relation='output')`；
  staging key 与 sealed key 都是随机不可猜值，客户端只拿到 staging PUT 链接。
- complete：`uploading -> scanning` 的 CAS（`revision = revision + 1`，写
  `expected_sha256`）与 `EnqueueTx(artifact_finalize, target=artifact, expected_revision=新
  revision, expected_sha256=sha, dedup_key='artifact:<id>:<sha>')` 在同一事务。同 hash 重复
  complete 是幂等 202 且不会新建第二个 finalize job（dedup 是同意图判定；artifact 已 ready 后
  重放时 expected_revision 不同导致的 dedup 冲突被视为同一意图，不再报错）；不同 hash 或
  墓碑是 409 `artifact_conflict`。**complete 不激活 artifact**：激活仍只由 T022 的 finalize
  守卫在同一事务里做。
- 读取 scope 一条 SQL 判定：artifact 是该 job 的 `input_artifact_id`、`result_artifact_id`，
  或在 `background_job_artifacts` 里属于该 job；否则与不存在同为 `404 artifact_not_found`。
  `artifact-status` 返回 uploading/scanning/ready/quarantined；`artifact-downloads` 只对
  `ready` 且未墓碑的对象签发 sealed GET，未 ready 是 409 `artifact_not_ready`。
- 状态/墓碑写入都 bump `revision`（complete 的 CAS、T022 激活/隔离/删除），维持 ADR §1。

### 21. 模型 provider 与密钥边界

- `internal/modelproxy` 是唯一模型调用边界。候选行的 `endpoint_ref` 必须是 `env:` 引用，
  `secret_ref` 必须是 `env:NAME`/`file:NAME` 引用；两者都在 Go 内解析，worker/handler 永远
  不传 endpoint、路径或密钥，也收不到它们。
- `provider:"stub"` 只在 `dev-stub`/`capacity-stub` profile 下可用；其他 profile 里 stub 候选
  直接 `503 provider_not_configured`。stub 输出必须带 `provider:"stub"`/`stub:true`，其
  fixture_sha256/fixture_bytes/usage 都由**服务器实际读到的 fixture 字节**推导；兼容性判定由
  fixture 里的显式 case 驱动。stub 不是“真实模型通过”的替身。
- 真实 provider 路径：无 `ORCHARA_MODEL_ENDPOINT`（非 real-model profile 时为常态）→
  `503 provider_not_configured`；有 endpoint 但本批没有编译任何厂商协议 adapter（F06 才有）
  → `503 provider_adapter_unavailable`。两种都在**预留 ledger 行之前**拒绝，因此不会留下可被
  读成“已计费调用”的行，也不会伪造成功。密钥只经 `secret_ref` 在 Go 内解析，任何错误消息、
  HTTP 响应、日志都不包含密钥值或引用字符串。
- 真实 provider 集成测试在本环境 `not_run`（无供应商 endpoint/凭据）；报告必须分开列 stub 与
  real。

### 22. platform_model_calls 记账语义

- 每次物理 attempt 一行：`state` 从 `prepared` → `dispatching` → `succeeded`/`failed`；
  行在 dispatch **之前**写入（`prepared`），所以崩溃/拒绝也留下可审计的尝试。
- `scope_key = job_id`，`logical_key` 由调用方给定，`physical_attempt` 在 job 行锁内取
  `max+1`（`UNIQUE(scope_key,logical_key,physical_attempt)` 是物理兜底）。同逻辑键 + 同 params
  且已 succeeded → 返回原 call（不再计费）；同逻辑键 + 不同 params → `409 model_call_conflict`；
  失败后重试 → 新 physical_attempt。
- `params_sha256` 是 JCS 规范字节的 SHA-256；`reserved_amount`（dispatch 前估算）与
  `charged_amount`（provider 返回后，按真实 tokens 计算）都是 decimal 字符串，金额只用整数
  1e-8 单位计算，不经过 float。stub 的 tokens 由 fixture/response 字节推导，永不为 null。
- 成功时把 provider 的规范响应写成 sealed、ready 的 `purpose='model_result'` artifact 并登记
  `background_job_artifacts`，然后再把 ledger 行置 succeeded；失败 attempt 记
  `state='failed', charged_amount=0`，不挂 result artifact。
- 进程在 `prepared` 行写入后、dispatch 前崩溃会留下 `prepared` 行：它永远不会被读成成功或
  已收费，`unknown`/`prepared` 的清扫与对账属 F05/F13A（本批不做，不在报告里写成已处理）。
- `request_id` 恒为 NULL、`background_job_id` 恒有值（契约 XOR）；F05 接管用户请求调用时必须
  显式二选一。

### 23. model_validate handler 的边界

- handler 只通过 `background.JobScope` 访问 job-scoped 数据：下载 fixture、上传/complete/
  status 报告、发起 model-call。它没有 maintenance DSN 的业务读权限，也不构造对象路径；T022
  artifact_finalize 的 interim direct read 仅保留在该 kind（T024 不触碰它，T026 迁移）。
- logical key 由 fixture SHA 派生（`compatibility:<sha>`），params 携带 fixture hash 与检查项；
  同一 job 重试同 fixture 是同一逻辑调用的新 physical attempt。
- 兼容性失败（如 structured output 不合规）是**报告结果**：job `succeeded`、report
  `compatible:false`，ledger 仍是一次成功的真实调用。provider 未配置/无 adapter 是
  business failure（不重试），只有 `transient_storage_io`/`transient_internal`/`lease_expired`
  可重试——`retryableByKind[model_validate]` 与 registry 在同一变更里扩展。
- handler 必须等报告 artifact 变成 `ready` 才提交 `succeeded` 与 `result_artifact_id`；服务端
  Complete 对 `model_validate` 额外校验该 artifact 存在、属于 job owner、linked 且 ready，
  否则 422 且不改 job。
- T022 的 maintenance 循环是单线程；T024 测试用两个真实进程（work handler + system_io
  finalize）驱动。**运行时双池属 T026**，本票不改调度器。

### 24. T025/T026 可以依赖的接口

- 端点（内部 mTLS，maintenance 角色，全部要求活 attempt_token）：
  `POST /internal/v1/background/jobs/{id}/artifact-uploads`、
  `.../artifacts/{artifact_id}/complete`、`.../artifact-downloads`、`.../artifact-status`、
  `.../model-calls`；错误码 `stale_attempt`(409)、`artifact_not_found`(404)、
  `artifact_conflict`/`artifact_not_ready`(409)、`object_signing_unavailable`/
  `object_store_unavailable`/`provider_not_configured`/`provider_adapter_unavailable`(503)、
  `model_call_conflict`(409)、`candidate_mismatch`/`candidate_unavailable`/`model_not_allowed`/
  `fixture_missing`(422)。
- 表：`platform.model_profile_versions`、`platform.platform_model_calls`、
  `platform.background_job_artifacts`；`background_jobs.result_artifact_id` 由 Complete 记录。
- env：`ORCHARA_OBJECT_SIGNING_KEY`、`ORCHARA_OBJECT_LINK_TTL`、`ORCHARA_PUBLIC_URL`、
  `ORCHARA_SECRET_DIR`；`ORCHARA_OBJECT_DIR`/内部 mTLS 变量沿用 T022。
- Lease 的 `input_url` 是真实签名 sealed URL（无输入/未 ready/未配置签名时为 null）。T026 的
  application_evaluate 应走同一 upload/download/status 流程并在同一 retry 表扩展自己的码。

## 后果（T024）

- 0011 之后，artifact 的创建路径有两条：T022 controller/CLI 与 T024 `completeUpload`；两者都
  必须写 `revision=1` 并在状态迁移时 bump。任何其他 INSERT 都是 bug。
- 对象字节只能经签名链接移动；公共 listener 不暴露对象命名空间，日志不记录 token。
- 模型调用必须先检查 provider 可用性再写 ledger；“未配置”永远不产生成功行，也不重试。
- `input_url`/`upload_url` 依赖 `ORCHARA_PUBLIC_URL`；部署方必须把它设成 worker 可达的绝对
  地址，否则链接不可用（不是安全降级）。
- T025/T026 不得复制签名器、modelproxy 或 job-scoped 校验的实现，只扩展 kind/码/测试。

## 证据（T024）

- `product/db/migrations/0011_job_files_and_models.sql`
- `product/internal/objectstore/{sign.go,sign_test.go}`、`internal/objectstore/objectstore.go`
- `product/internal/background/{attempt,artifacts,scope}.go`、`internal/background/complete.go`、
  `internal/background/handlers/model_validate.go`
- `product/internal/modelproxy/`（provider seam、stub、real not-configured 路径、ledger）
- `product/internal/api/{jobdata,links,internal,server,middleware}.go`
- `product/cmd/api/main.go`、`product/cmd/maintenance/{scope,client,run}.go`
- `product/tests/support/t024.py`、`product/tests/f00b/test_t024_*.py`
- `product/reports/f00b/T024-job-files-and-models.md`

## T025 追加：执行清单、依赖闭包与字节复核边界（F02A.02）

T025 把草稿固化成一个**内容可核验**的执行清单：同一执行内容（definition + capability card + 固定依赖 +
编译器/镜像/锁/状态 schema）得到同一份规范字节与 hash，布局和身份不参与；错误定位到节点 config；
依赖闭包无环且只含调用者可用的已就绪版本。以下决定为 T027（debug）、F02B（发布）与 F03/F04A（worker）
必须沿用。

### 25. 迁移 0012 的表归属：tool_versions 只读模型 + manifest_artifacts 去重

- `platform.tool_versions` 按 `contracts/database.sql` 基线创建，**写入/构建/审批归 F05**。本批没有工具
  提交/构建/审批流程，而 T025 的依赖闭包必须能证明“python/agent 引用的工具已 ready、已审批、有镜像
  digest”，因此 T025 现在建表作为只读模型（与 F01 为 applications 建读模型同理），迁移头/COMMENT
  明确写出归属。**T025 不写任何工具行**，测试用 SQL 播种真实约束下的一致行；不得把本票读模型写成
  “工具构建已实现”。
- `platform.manifest_artifacts` 是**对契约基线的显式新增**（T025 拥有），主键
  `(application_id, execution_revision, manifest_sha256)`，FK 到 `platform.artifacts`。它不是发布/快照
  身份，而是“同一执行内容只密封一次”的内容注册表。T027 的 debug snapshot 与 F02B 的发布版本必须
  引用这里记录的 artifact；**不得为同一 hash 再写第二份字节**。
- 权限沿用 0007–0011：PUBLIC 无 platform 权限，`orchara_worker` 无 platform USAGE/表/序列权限。

### 26. Manifest 组合与 hash：JCS 外置、身份/布局不参与、执行 revision 是 source_revision

- 字段严格等于 `contracts/execution-manifest.schema.json`（13 字段，无额外字段）：
  `manifest_version=1.0`、`application_id`、`source_revision`（= 草稿 `execution_revision`，不是 ETag
  的 `revision`）、`compiler_version`、`worker_image_digest`、`runtime_lock_sha256`、
  `state_schema_version`、`definition`、`capability_card`、`tool_artifacts`、`model_profile_versions`、
  `subapp_versions`、`connector_policies`（本批恒 `[]`）。
- **不含** layout、run/snapshot/version/review id、审核/验证状态或 hash 自身。`manifest_sha256` 是
  UTF-8 RFC 8785 JCS 原字节的 SHA-256，外置返回/存储；绝不能从 JSONB 重序列化后推断。
- 固定常量：`compiler_version="orchara-compiler/1.0.0"`、`state_schema_version="orchara-state/1.0"`。
  前者是组合规则版本（本批没有编译器二进制）；后者只是清单字段，真正的 state schema 制品属 F03。
- 集合规范化：`tool_artifacts` 按 tool version id 排序去重，model/subapp UUID 数组排序去重；
  `python.tool_version` 与 `agent.tool_versions[]` 指向同一 tool 时只出现一次。
- 只修改 layout 的保存仍会 +1 `revision`（ETag/CAS），但 `execution_revision` 不变，因而同一份
  manifest 字节/hash 不变。该性质由保存路径的 jsonb `IS DISTINCT FROM` 与 manifest 不含 layout 共同保证，
  不靠“不保存 layout”规避。golden 的 `sales`/`layout-only` 双胞胎是仓库内的逐字节证据。

### 27. 部署输入 fail closed：worker 镜像占位、runtime lock 真实摘要、503 而非伪造

- `worker_image_digest` 来自 `ORCHARA_WORKER_IMAGE_DIGEST`，必须匹配 `^sha256:[0-9a-f]{64}$`。
  本批**未构建 worker 镜像**（F04A/F15A 才有），该值是部署方占位输入；缺失/非法时生成 manifest 的
  端点返回 `503 compiler_unconfigured`，**绝不发明 digest**。配置里非空但格式非法 = 拒启动。
- `runtime_lock_sha256` 是 `ORCHARA_RUNTIME_LOCK_PATH`（默认 P01 锁
  `probes/agent_min/requirements.lock.txt`）文件字节的 SHA-256；每次生成时重新计算，文件不可读同样
  `503 compiler_unconfigured`。锁文件变化必然改变所有执行 hash，golden 必须显式重生成。
- 有效草稿但没有 `ORCHARA_OBJECT_DIR`：`503 object_store_unavailable`（字节无处密封，不能只返回一个
  无法复核的 hash）。坏草稿（定义/卡片/依赖错误）不需要部署输入即可得到 `valid:false`，不会被 503
  掩盖。

### 28. 依赖就绪词汇与错误码：共享 vs 作者私有，归属错误不静默

- 闭包只覆盖固定引用：model（`model`/`agent.model_profile_version`）、tool（`agent.tool_versions[]`、
  `python.tool_version`）、sub-app version（`agent.subapp_versions[]`、`call_app.application_version`）。
  知识库引用不参与（本批无 KB 表/读取授权实现，F09/F05 范围）。
- 就绪规则（Go 对真实行判定）：
  - model：存在、未撤销、`validation_state='passed'`；pending/running/failed 均不可执行。
  - tool：存在、未撤销、`state='ready'`、`image_digest` 非空；**共享/提交执行**还要求 `approved_at`
    非空（构建 ready ≠ 组织批准）。**作者私有 debug**（T027 传 `ExecutionOptions{PrivateDebug:true}` +
    caller id）允许作者自己的 ready 工具无需批准；用他人未批工具是归属错误，不是静默纳入。
  - sub-app version：存在、未撤销、应用未归档、调用者对该应用有 read ACL（owner 或未过期 grant）。
    他人依赖无 ACL = 错误；有 ACL 才进入 manifest 的 `subapp_versions`。
- 环：`version_dependencies` 的递归闭包必须无环；另外草稿直接/间接调用到**本应用**的 published
  version 也是环（草稿不能递归调用自己）。错误定位到第一个达到环的引用 pointer。
- 稳定错误码（T025 新增，客户端/T027 可依赖）：
  `capability_card_incomplete`、`dependency_invalid_id`、`dependency_not_found`、
  `dependency_not_ready`、`dependency_revoked`、`dependency_not_visible`、`dependency_cycle`。
  每条都有 `pointer`（`/capability_card[/field]` 或 `/nodes/<i>/config/<field>[/<j>]`），单节点错误带
  `node_id`；消息只含 id/状态，不含其他用户正文。定义错误继续使用 P05 的 code/pointer/优先级。

### 29. 复核边界：`VerifyManifestBytes` 是机制，worker 属 F03/F04A

- `internal/compiler.VerifyManifestBytes(objects, ArtifactRef{ObjectKey,SHA256,SizeBytes})` 是未来
  supervisor/worker 的复核入口：读密封对象（≤20 MiB）→ **先**重算字节 SHA-256/大小并与记录值比较
  （不符立即失败，不进入 parser）→ 要求字节是精确 JCS 规范形式 → 严格解码并复核 schema 语义。
- Go 测试证明 golden 通过，篡改字节、错误记录 hash、非规范 JSON、缺必填字段、大小不符、对象不存在、
  未配置 store 均被拒；真实 HTTP 测试证明端点密封的字节 hash == artifact 行 == 响应头。
- **真正 worker 尚未构建**：本批不声称 worker 已复核过任何执行。F04A/F03 必须从 snapshot/claim 取得
  `{object_key,sha256,size_bytes}` 后调用同一函数，不得自行重序列化或另造字节。

### 30. `X-Manifest-SHA256` 非契约扩展、去重密封与“validate 不建执行身份”

- `validateApplication` 的契约 200 体只有 `{valid,errors}`，装不下 `manifest_sha256`。T025 在
  **valid:true 的 200 响应头**返回 `X-Manifest-SHA256`（64 位小写 hex），不改 contracts；这是显式
  契约缺口，后续契约修订票应把它变成正式字段或正式 header 契约。invalid/503 没有 hash。
- 端点是 **owner-only**（非 owner 一律 404，read grant 也不放行），会话 POST 需 CSRF；API key 需
  `application:write`（validate 会写 artifact，按写能力授权）。
- validate 只写“规范字节 artifact + 去重行”，**不创建** application_version、publication_review、
  execution_snapshot、run、report 或 background job。同 `(application_id, execution_revision,
  manifest_sha256)` 重复 validate 复用既有 artifact，不产生第二个对象；同一事务用
  `pg_advisory_xact_lock`（按三元组）串行化并发写。
- 事务失败/冲突路径删除本次写入的对象；对象写成功而 commit 前崩溃可能留下未引用 sealed 对象，
  属 T026 孤儿清理，不在本票声称已处理。
- T027/F02B 必须复用本票的 `ValidatedExecution.ArtifactID` 与同一 `SHA256`：debug 的
  `manifest_artifact_id`、发布的 manifest 字节/hash 都来自这一次密封，不允许各自重编译出第二份。

## 后果（T025）

- 执行内容的唯一身份是 `(application_id, execution_revision, manifest_sha256)`；任何改变 definition /
  capability_card / 固定依赖 config 的保存都改变执行 hash，任何只改 layout 的保存都不改变。
- 依赖就绪判定只在 Go 内、对真实行执行；T025 没有工具构建/审批写入，也没有发布写入；后续票不得
  复用 `manifest_artifacts` 表去伪造发布身份。
- `internal/compiler` 是 manifest 组合/复核的唯一实现；`internal/catalog` 是依赖/ACL/密封的唯一编排。
  T027/F02B/F03 只调用，不复制 JCS、不复写 hash 规则。
- worker 镜像 digest 与 state schema 制品是显式的占位/未完成项；F04A/F15A/F03 替换后必须重生成 golden，
  并把“所有执行 hash 变化”作为预期影响记录。

## 证据（T025）

- `product/db/migrations/0012_manifest_and_tool_versions.sql`
- `product/internal/compiler/{manifest.go,verify.go,*_test.go}`
- `product/internal/catalog/{manifest.go,manifest_test.go,draft.go}`
- `product/internal/api/{validate.go,server.go}`、`product/internal/config/{config.go,config_test.go}`
- `product/tests/contracts/{README.md,snapshots/**}`
- `product/tests/support/t025.py`、`product/tests/f02a/test_t025_*.py`
- `product/reports/f02a/T025-manifest.md`



