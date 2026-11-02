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

## T026 追加：评测池、报告绑定与孤儿清理（F00B.04）

T026 把“评测池满载时文件封存仍能完成、业务失败与基础设施失败可区分”从规格变成可运行
实现，并补上 T023/T024 留下的两个延期外键。以下决定为 T027、F13A、F07 与部署方必须沿用；
改动需新 ADR 或在本节追加修订理由。

### 31. 迁移 0013：评测表、两个补齐的延期外键与一个继续延期

- `platform.evaluation_jobs`/`platform.evaluation_reports` 按 `contracts/database.sql`
  基线创建（含 kind XOR CHECK、state/status CHECK、report/artifact/principal/background_job 外键）。
  `evaluation_reports` 没有 owner 列，所有权由产生它的 `evaluation_jobs.report_id` 表达。
- `evaluation_jobs.snapshot_id` 是**裸 uuid、无 FK**。契约引用 `execution_snapshots(id)`，但该表
  属 T027（迁移 0014）且本批不存在。T027 必须在其迁移里补
  `evaluation_jobs_snapshot_fkey (snapshot_id) -> execution_snapshots(id)`；在此之前
  `kind=application` 的行只能结构存在（CHECK 真实），不能执行（binding 409、handler 以
  `evaluation_binding_missing` 失败、无报告）。T026 绝不伪造 snapshot 行。
- 两个延期外键在本迁移补齐：`publication_reviews.report_id -> evaluation_reports(id)`（0010 延期）
  与 `model_profile_versions.evaluation_report_id -> evaluation_reports(id)`（0011 延期）。因为
  约束现在真实存在，T019/T023/T025 的测试 seed 辅助函数改为插入真实 report 行；这是 schema
  收紧后的预期适配，不是放宽断言。T026 本身不写 `publication_reviews`，也不写
  `model_profile_versions.evaluation_report_id`（F02B/F13A 责任）。延期 FK 不做事后回填：升级
  一个已存在 dangling `report_id` 的旧库前必须先修正/清空这些行（本批没有已部署数据；所有门禁
  都在新库上迁移）。
- 索引：`evaluation_jobs_owner_state (owner_id, state, created_at DESC)` 供 owner/admin 读，
  `evaluation_jobs_report (report_id) WHERE report_id IS NOT NULL` 供 report→owner 判定；
  `background_job_id UNIQUE` 提供 1:1 job 查找。权限沿用 0007–0012（PUBLIC 无、`orchara_worker`
  无 platform USAGE/表/序列权限）。
- 不建 F07 的 deletion 模型；不建 snapshot/run/job；不写发布版本。`evaluation_reports` 不可变
  （没有 update 路径），删除生命周期归 F07。

### 32. 一个评测 = 业务行 + 一个固定后台 job，同事务创建；claim 把 queued 变 running

- 控制接口是 `cmd/admin evaluations enqueue --kind model|application --owner-login LOGIN
  --dataset-artifact-id UUID [--model-profile-version-id UUID] [--snapshot-id UUID]`。它在一个
  事务里先 `EnqueueTx(kind=application_evaluate, target=evaluation_id, owner, dedup=evaluation_id,
  input_artifact_id=dataset)` 再插 `evaluation_jobs`；任一失败两者都不存在。
  `--kind=model` 必须且有 candidate、不能有 snapshot；`--kind=application` 反之。形状错误
  exit 2、数据库语义拒绝（未知 login、候选不存在、dataset 不存在 FK）exit 1，均不写部分行。
- `background_jobs.target_id = evaluation id`，`input_artifact_id = dataset`，`dedup_key = evaluation
  id`（一次评测一个意图；终态 job 仍占用该 key，重跑必须新评测）。job 的 `expected_*` 不参与
  评测绑定：绑定由 `evaluation_jobs` 行的 kind 决定（候选 manifest / snapshot manifest）。
- Claim（任何 worker）在同一事务里把 evaluation `queued -> running`；重试保持 running；只有
  Complete 的终态路径写 `failed`/`cancelled`，只有通过守卫的 report 写 `succeeded` + `report_id`。
- 公共 `POST /api/v1/applications/{application_id}/evaluations` 仍属 F13A 的受信 runner 入口；
  T026 不实现、不伪造响应。API key 也拿不到未实现的公共评测入口。

### 33. 完成守卫：报告绑定与状态规则；report failed ≠ job failed

`Complete` 对 `application_evaluate` 的 `succeeded` 结果执行服务端守卫（同一事务）：

1. `result_artifact_id` 必填，且必须是本 job 的、ready、未墓碑、owner 一致的 artifact（复用
   T024 的 `verifyJobResultArtifact`）；报告 artifact 大小上限 1 MiB（远低于 20 MiB store 上限）。
2. `FOR UPDATE` 锁 evaluation 行：必须属于本 job/owner、state ∈ {queued,running}；dataset 行必须
   ready、未墓碑、有 64 hex `sha256`（否则 `source_deleted`/`dataset_not_ready`）。
3. 解析绑定 manifest：model → `model_profile_versions.manifest_sha256`；application →
   `execution_snapshots.manifest_sha256`（表存在且 owner 一致；T027 前为 `evaluation_binding_missing`）。
4. 读 sealed report 字节（cap + 重算 SHA-256 必须等于 artifact 记录值）、严格 JSON 解码
   （未知字段/尾随数据拒绝）、文档规则校验。
5. 绑定比较：`manifest_sha256` == bound；`dataset_sha256` == dataset artifact 的 sha256；
   `dataset_artifact_id`、`evaluation_id` 一致；`evaluation_kind` 一致。任一不符 →
   `report_binding_mismatch`（文档身份不符/字段类型问题 → `report_invalid`）。
6. 状态规则：`passed` 要求 ≥1 case、≥1 check、全部 case passed；`failed` 要求 ≥1 case、≥1 check、
   至少一个 case failed；`not_run` 才能 0 case。空 case、空 check、status 与 cases 矛盾、坏 case
   id 一律 `report_invalid`——`not_run`/空集永远不能变成 passed。
7. 全部通过才插入 `evaluation_reports`（immutable）、把 evaluation 置 succeeded + report_id +
   finished_at、并让 job succeeded。任何守卫拒绝：job failed（business，不重试）、evaluation
   failed 同名 error_code、**没有 report 行**。sealed 对象读失败是 `transient_storage_io`：
   job 可重试，evaluation 保持 running 直到终态。

业务评测失败（case failed / dataset 不是有效评测数据集）由 handler 表达为 report `status=failed`：
job succeeded、evaluation succeeded、report status=failed。handler 主动提交 job failed 时：重试表
内的基础设施码返回 ready 重试（evaluation 保持 running）；business 码立即 job failed + evaluation
failed（error_code 相同）。cancelled → evaluation cancelled。`application_evaluate` 的可重试码固定为
`transient_storage_io`/`transient_internal`/`lease_expired`/`dataset_wait_timeout`，其余（含未知码）
一律 business。

handler 是受信基础设施，不是用户：报告由它生成，但“passed”只能由服务端按上述绑定/状态规则确认；
用户/前端永远不能自报 passed。

### 34. handler 边界与合成评测器；application kind 如实 partial

- `JobScope` 增加 job-scoped `Evaluation` 读取（内部 mTLS maintenance 角色 + 活 attempt_token +
  本 job 归属校验），返回 evaluation id/kind/dataset id/bound manifest hash。这是**非契约内部扩展**：
  `BackgroundLease`（`additionalProperties:false`）没有地方放 bound manifest hash，而让 handler
  直接用 maintenance DSN 读评测表会把绑定决策移进 handler。该端点不是公共路径，只返回本 job 的
  evaluation 事实；报告/ADR 记录该缺口，契约修订时应把它纳入正式内部协议。
- handler 等 dataset：job-scoped `artifact-status` 轮询（默认 100ms）+ heartbeat（默认 5s，测试 50ms）；
  默认等待 60s，可配（`--dataset-wait-timeout` / `ORCHARA_MAINTENANCE_DATASET_WAIT_TIMEOUT`）。
  uploading/scanning 继续等；quarantined/deleted/404 → business `source_deleted`；超时 →
  可重试 `dataset_wait_timeout`。等待发生在 work 池，不占 system_io 槽。
- 合成评测器（T026 占位，`report.synthetic=true`、`evaluator="synthetic-jcs-case/1"`）：dataset 文档
  `{schema:"orchara.evaluation-dataset/1", cases:[{id,input,expected_sha256}]}`；对每个 case 的 `input`
  求 RFC 8785 JCS 字节的 SHA-256 并与声明值比较。空 `cases` → report `not_run`；坏 dataset → 一个
  failed `dataset` case（report failed、job succeeded）。报告含 `report_version/kind/evaluation_kind/
  evaluation_id/dataset_artifact_id/dataset_sha256/manifest_sha256/status/checks/case_count/cases/
  synthetic/evaluator`。
- **F13A 取代 case 判定与 dataset schema**；替换时必须同时扩展守卫的状态/绑定规则与测试，不得
  让 handler 自证 passed。T026 不调用真实模型、不声称真实评测已通过；真实模型/真实 checker 是
  F13A 的 `not_run`。
- `kind=application`：CLI 结构接受并写行（snapshot_id 无 FK 仍可存），但 execution_snapshots 不存在
  时 binding 返回 409 `evaluation_binding_unavailable` → handler 以 `evaluation_binding_missing`
  job failed、evaluation failed、无报告。T027 建表且补 FK 后同一 `resolveBoundManifest` 查询自然生效
  （要求 snapshot 行有 `owner_id` 与 `manifest_sha256`，owner 必须等于评测 owner）；T026 不把
  application 评测写成已跑通，报告记 `partial`。

### 35. 运行时双池与 `--once` 语义

- `cmd/maintenance run` 起两个独立池：`system_io` 只含 `artifact_finalize`（默认 2 槽），`work`
  含其余已编译 kind（默认 1 槽）。每槽一个 worker/worker_id（`<base>:<pool>:<slot>`），各自走
  claim→execute。`--kinds` 只能收窄并仍按池划分：`--kinds artifact_finalize` 只起 system_io，
  `--kinds model_validate,application_evaluate` 只起 work。T022 的启动日志行与 summary 形状保留，
  summary 聚合两池，jobReport 增 `pool` 字段。
- 参数：`--system-io-slots`/`--work-slots`/`--dataset-wait-timeout`/`--evaluation-poll-interval`/
  `--report-wait-timeout`，环境默认 `ORCHARA_MAINTENANCE_SYSTEM_IO_SLOTS` 等五个。非法值（0、越界、
  过短 poll、过长 wait）启动即 exit 2。环境先校验、flag 覆盖之；flag 不会用 0 值静默覆盖配置。
- `--once` 是**跨池 drain**而不是“第一个空 claim 就退出”：每个槽在空 claim 时记录当前 activity
  epoch；只有所有槽都在同一 epoch 观察到空队列才退出。任何 job 开始或结束都会 bump epoch 并清空
  空标记——因为 work handler 的 `completeUpload` 会产生 system_io 的 finalize job，finalize 完成后
  又可能产生新的 work。活动 job 存在时进程绝不会退出；因此“报告 finalize 还没跑完 `--once` 就退出”
  不可能发生。正常空队列时一次 drain 会在若干 poll interval 内退出。
- 等待评测的 handler 在 work 池续租；system_io 池独立，因此 finalize 不会被等待中的评测饿死。
  真实测试：两个 uploading dataset 的评测占满 work 槽时，另一个 artifact_finalize job 仍完成并
  ready（此时两个评测仍 leased/running）；随后 dataset 就绪，两评测都成功且报告 passed。

### 36. 评测/报告读权限与投影

- `GET /api/v1/evaluations/{evaluation_id}` 与 `GET /api/v1/evaluation-reports/{report_id}`：
  evaluation 的 owner 或 admin 得 200，其余（含无 grant 的管理员以外用户、未知 id、畸形 id）一律
  404（与不存在同答，不可枚举）。report 无 owner 列：owner 由 `evaluation_jobs.report_id` join 判定，
  report 不被任何 evaluation 引用时同为 404。
- 响应只有契约字段：EvaluationJob 六字段（id/kind/state/background_job_id/report_id/error_code），
  Report 四字段（id/status/manifest_sha256/result_artifact_id）。不返回 dataset/candidate/snapshot id、
  object key、报告正文或 draft；后台 job GET 仍按 T022 的 owner-or-admin 脱敏投影。
- 契约给读端点声明了 `apiKey` security，但没有定义评测 scope。T026 不做 scope 判定（一致按
  owner/admin），记录为契约缺口；F13A/契约修订应为评测读定义专用 scope（不得复用
  `application:read` 假装已授权）。

### 37. 孤儿清理边界（F07 前的最小安全规则）

- `cmd/maintenance sweep --orphan-age D [--dry-run]` 只需要 `ORCHARA_MAINTENANCE_DSN` 与
  `ORCHARA_OBJECT_DIR`（不需要内部 mTLS），它不领取 job、不写业务行。
- `staging/*`：当且仅当没有“live 上传者”时删除。live 定义是存在 `uploading`/`scanning` 且
  `deleted_at IS NULL` 的 artifact 行以该 staging key 为目标。ready/quarantined/deleted 行的 staging
  残留可删（否则永不清）；无行引用可删。reason 记录 `artifact_ready`/`artifact_quarantined`/
  `artifact_deleted`/`no_artifact_reference`。
- `sealed/*`：只有**任何 artifact 行都不引用**该 object_key 时才删（崩溃留下的未登记 sealed）；
  被任何行引用的 sealed 字节永不在这里删除（包括 deleted 行——那属 F07 的删除账本）。
- 年龄用对象 mtime 与 `--orphan-age` 比较，是并发/TOCTOU 的护栏：新写入的对象不会被误删；
  staging key 由行在同一事务预分配，旧 key 不会被复用。删除 `Store.Delete` 幂等，重复 sweep 删除 0。
  `--dry-run` 只报告（`would_delete`），不动文件。临时文件名/非法 key 不是 platform 对象，不参与。
- 不做：F07 的 deletion_jobs/墓碑传播、对象内容扫描（F05/T028）、quarantined 行复活。

## 后果（T026）

- 0013 之后，评测写入只有 `cmd/admin evaluations enqueue` 一条路，报告写入只有完成守卫一条路；
  任何直接写 `evaluation_reports` 或先写 report 再补绑定的实现都是 bug。F13A 必须复用同一守卫，
  替换 runner 而不是替换绑定规则。
- `application_evaluate` 的终态语义固定：report failed 是 job succeeded；基础设施失败是 job failed
  + evaluation failed + 无 report。新增错误码必须同时进 `retryableByKind` 或明确标 business。
- 迁移 0013 之后，任何引用 `evaluation_reports` 的新表/新列直接受 FK 约束；T027 只需补
  `evaluation_jobs_snapshot_fkey` 并写 snapshot 行，不重复 T026 的绑定/守卫。
- 运行时双池是与 T022/T024 的既有 `--once/--kinds` 行为兼容的扩展；等待型 handler 必须使用
  work 池，绝不能把 finalize 类工作放进 work 池（否则又回到 T026 要修的拥塞）。
- 孤儿清理只清“确定没人要”的对象；它不是删除功能，不能作为 F07 的替代，也不把 quarantined
  变成可用。

## 证据（T026）

- `product/db/migrations/0013_evaluation_pool.sql`
- `product/cmd/admin/{evaluations.go,main.go}`、`product/cmd/maintenance/{run,sweep,scope,client,main}.go`
- `product/internal/background/{evaluation.go,complete.go,claim.go,handler.go,scope.go,job.go}`
- `product/internal/background/handlers/{application_evaluate.go,artifact_finalize.go}`
- `product/internal/api/{evaluations.go,server.go,internal.go}`、`product/internal/objectstore/objectstore.go`
- `product/internal/config/{config.go,config_test.go}`
- `product/tests/support/{t026.py,fixtures.py,f00b.py,f02a.py,t025.py}`、`product/tests/f00b/test_t026_*.py`
- `product/reports/f00b/T026-evaluation-pool.md`

## T027 追加：私有调试快照、排队 Run 与幂等提交（F02A.03）

T027 把“创作者从调试入口选择 revision 与测试输入，冻结仅自己可见的执行快照并排队一个
debug Run”从规格变成可运行实现，并且只交付**提交与排队**：领取、执行、事件、结果和终态属
F03。以下决定为 F03、F02B、F05/F07 与后续契约修订必须沿用；变更需新 ADR 或在本节追加理由。

### 38. 迁移 0014：执行身份五表、补齐的 `evaluation_jobs_snapshot_fkey` 与三个延期外键

- `platform.execution_snapshots`、`platform.runs`、`platform.jobs`、`platform.run_artifacts`、
  `platform.idempotency_keys` 按 `contracts/database.sql` 基线创建。`execution_snapshots` 的
  kind/身份 XOR CHECK、`runs` 的三条契约 CHECK、`jobs`/`run_artifacts` 的 PK 与部分唯一索引
  （`one_live_job_per_run`、`jobs_ready`、`one_active_top_run_per_conversation`）原样保留。
- **补齐 T026 延期的外键**：`evaluation_jobs_snapshot_fkey (snapshot_id) ->
  execution_snapshots(id)`。从 0014 起 `evaluation_jobs.snapshot_id` 是真实引用；此前 T026 的
  测试 seed 了一个裸 uuid，schema 收紧后改为 seed 真实 snapshot 行（预期适配，不是放宽断言）。
- **三个继续延期的外键**（列已建、无 FK，迁移头/COMMENT 写明归属）：
  `runs.request_id`/`runs.conversation_id` → F05/F07（`requests`/`conversations` 表不存在）；
  `jobs.current_attempt_token`/`run_artifacts.attempt_token` → F03（`run_attempts` 不存在）。
  F05/F07/F03 必须在各自迁移里补 `runs_request_id_fkey`、`runs_conversation_id_fkey`、
  `jobs_current_attempt_token_fkey`、`run_artifacts_attempt_token_fkey`。在此之前任何代码都不得
  把这几列当作已验证引用；本批只写 NULL。
- **一个显式新增列**：`idempotency_keys.response_json jsonb`。契约的 idempotency 表只有
  `response_code`/`response_artifact_id`，没有地方保存 `debugDraft` 必须原样重放的 202 响应体
  （API.md §1 要求“保存规范请求哈希和响应”）。T027 在同一事务里写 response_code 与
  response_json；F05/F06 的创建类操作继承该列。提交成功的收据**总是**同时带 code 与 body。
- 收紧但契约未明写的 CHECK（本批所有写入者都满足）：`execution_snapshots.manifest_sha256`、
  `runs.manifest_sha256`、`idempotency_keys.request_sha256` 为 64 字符；
  `execution_snapshots.manifest` 是 jsonb object（只是可查询投影，hash 永远按密封字节算）；
  debug 的 `draft_revision > 0`；`idempotency_keys.key` 非空白。
- 权限沿用 0007–0013：PUBLIC 无 platform 权限，`orchara_worker` 无 USAGE/表/序列权限。
- 不建 worker/attempt/operation/approval/event/quota 表（F03），不建 requests/conversations
  （F05/F07），不写 published snapshot（F02B）。

### 39. debugDraft 的固定顺序、owner-only 与“revision 在事务内冻结”

- 端点是 `POST /api/v1/applications/{application_id}/debug-runs`（operationId `debugDraft`）；
  会话需 CSRF，API key 需 `application:write`。**只有 owner**（非 owner 含管理员/read grant
  一律 404；Run 没有 grant 类型）。
- 顺序固定：401/403 → 请求体（400/422；缺 `Idempotency-Key` 为 400 `idempotency_key_required`）
  → 幂等收据（replay 或 409 `idempotency_conflict`）→ 可见性/owner（404）→ 当前 revision
  （409 `revision_conflict`，details 带 requested/current）→ T025 `ValidateExecution`
  （PrivateDebug=true；定义/卡片/依赖 422 `validation_failed` + `details.errors` 定位错误；
  未配置编译器/对象存储 503）→ run 预算/币种（422）→ 引用 artifact（422）→ 密封输入字节 →
  一个事务（幂等收据 + snapshot + run + start job + artifact 行 + 链接）。
- **修订在创建事务内用 `SELECT ... FOR UPDATE` 复核**：并发保存要么在锁前提交（revision 变了
  → 409，绝不把旧 revision 静默升级成新内容），要么在锁后阻塞到本次提交完成（它改不到这个
  run）。因为每次保存都 +1 `revision`，revision 相等即证明 `execution_revision` 未变，所以
  校验时密封的 manifest 必然仍描述被冻结的草稿内容。
- 创建结果：`execution_snapshots(kind=debug, application_version_id NULL, draft_revision=请求的
  revision, owner=caller, manifest jsonb 投影, manifest_sha256, manifest_artifact_id=T025 的
  artifact, compiler/worker digest/state schema)`；`runs(kind=debug, state=queued, phase=idle,
  revision=1, next_event_seq=1, manifest_sha256, input_artifact_id, checkpoint_thread_id,
  budget/currency 来自 definition.policy, request/conversation/version/parent 全 NULL)`；
  `jobs(kind=start, state=ready)`；输入 artifact（`purpose=run_input`、owner=caller、ready、
  JCS 规范字节、真实 sha256/size/revision=1）；`run_artifacts(relation=input)` 链接规范输入与
  所有 `artifact_ids`。**不创建** application_version、publication_review、request、
  conversation、report、background job。
- 响应 202 + 契约 `Run`（10 字段，debug/null/queued/idle）。`GET /api/v1/runs/{run_id}` 只给
  owner（其余 404，含管理员；畸形/未知/已删除同答），返回同一投影；API key 需
  `application:read`。不实现 events/SSE/claim/cancel（F03）。

### 40. 私有依赖规则、预算范围与输入 artifact 的准入

- 依赖复用 T025 `ValidateExecution(..., ExecutionOptions{PrivateDebug:true})`：作者自己的
  ready 工具**无需组织批准**即可私有调试；他人未批工具 → `dependency_not_visible`；共享/
  提交执行仍要求 `approved_at`。model 必须 `validation_state=passed` 且未撤销；sub-app version
  必须未撤销、应用未归档且调用者有 read ACL；环照旧拒绝。错误码/pointer/node_id 沿用 §28。
- run 的 `budget_limit`/`currency` 取自 `definition.policy`（P05 校验器已要求五字段）。额外规则：
  `budget_limit` 必须能放进 `numeric(20,8)`（≤12 位整数、≤8 位小数），否则 422
  `policy_budget_out_of_range` 定位 `/policy/budget_limit`；这是 debug 专有的列范围检查，validate
  仍按 DSL 报 valid（manifest 不存预算）。缺 policy/类型错是防御性 `policy_incomplete`。
- `artifact_ids`：≤5 个、唯一、UUID；每个必须是**调用者自己的**、`state=ready`、未墓碑、有
  sha256/size 的 artifact。错误定位 `/artifact_ids/<i>`，码为 `artifact_not_found`/
  `artifact_not_visible`/`artifact_not_ready`（他人 artifact 不泄露其状态）。`artifact_ids` 是
  集合：规范化时小写、排序，因此仅顺序不同的重放是同一请求。
- 规范输入字节 = `input` 对象的 RFC 8785 JCS 字节，密封为 `sealed/<64hex>` 后才进入事务；
  事务失败/重放/竞态落败都删除本次字节，崩溃留下的未引用对象属 T026 孤儿清理。

### 41. 幂等语义：canonical digest、replay 无副作用、并发唯一、过期仍重放

- 请求摘要 `request_sha256` = SHA-256(JCS(`{application_id, artifact_ids(规范化排序),
  draft_revision, input(JCS 字节)}`))；收据主键是契约的
  `(principal_id, method, path, key)`，其中 path 用**具体请求路径**
  （`/api/v1/applications/<uuid>/debug-runs`），同一 key 不能跨应用借用。
- 只有**成功**的 202 才写收据；422/409 不消耗 key，修正请求后可重试。同 key 同摘要 → 202
  原 Run（同一 id/同一 snapshot/同一字节，不重编译、不写对象、不建行）；同 key 不同摘要 →
  409 `idempotency_conflict`；缺/空/重复 header → 400 `idempotency_key_required`。
- **replay 优先于“当前 revision”规则**：同一 key + 同一规范请求描述的是同一次已授权操作，
  即使草稿随后保存过，也返回原来的 202 Run；用**新** key 指定旧 revision 才 409
  `revision_conflict`（旧 revision 不会被重新调试）。该优先级是本票的显式选择。
- 并发重复提交：收据插入与 snapshot/run/job 同一事务，第二个提交在唯一键上阻塞，待赢家提交后
  读到已提交 202 体并重放；输家删除自己刚密封的输入对象，因此“并发重复 ⇒ 恰好一个 Run”
  在数据库层成立（不是先查后写的竞态）。
- `expires_at` 推荐 24h（`IdempotencyTTL`）。过期是清理元数据，**不允许**再次执行同一操作：
  已提交收据永远可重放；F07 负责删除过期收据。提交成功的收据与响应体同事务，缺 body 是内部
  缺陷（500），绝不重新创建。

### 42. 契约缺口：Run 没有 snapshot hash 字段，也没有 getSnapshot 端点

- 契约 `Run` 是 `additionalProperties:false` 的 10 字段，既没有 `manifest_sha256` 也没有
  `draft_revision`；契约也没有读取 execution_snapshot 的 operation。为让“查看固定快照”诚实，
  `debugDraft` 202 与 `getRun` 200 都用**非契约扩展**响应头 `X-Manifest-SHA256`（64 位小写
  hex，与 T025 validate 同一先例 §26/§30）返回冻结的 hash；页面展示
  `execution_snapshot_id`、该 hash、以及提交时冻结的 draft revision（客户端事实），并明确
  “领取/执行/结果属 F03”。服务端 snapshot 的 manifest JSONB/draft_revision 仍只能通过 DB/worker
  协议读取。
- 后续契约修订应把 `manifest_sha256`（及可选 `draft_revision`）变成正式字段，或新增 snapshot
  读端点/内部协议字段；不得在客户端自行重算 hash 或猜测快照内容。

### 43. 与 T026/F13A 的衔接：application-kind 评测的绑定现在真实可用

- `evaluation_jobs_snapshot_fkey` 之后，T026 的 `resolveBoundManifest` 对 kind=application 读
  真实 `execution_snapshots.manifest_sha256`，仍要求 snapshot owner == 评测 owner（他人 snapshot
  = `evaluation_binding_missing`）。T027 **不**实现评测 UI/端点；F13A 用同一绑定规则替换合成
  runner，不得自证 passed。T026 的 `partial` 只剩“真实 runner”一条（本票在 PROGRESS 说明）。

### 44. 边界：F03/F02B/F05/F07/T028 各拥有什么（不得越界）

- **F03（执行）**：领取/租约/attempt/事件/结果/退出 finalizer/cancel/SSE；必须从 snapshot 取
  `manifest_artifact_id` 的 `{object_key,sha256,size_bytes}` 并先调
  `compiler.VerifyManifestBytes`（不得从 JSONB 重序列化推断 hash，不得重编译）；必须补
  `jobs_current_attempt_token_fkey`、`run_artifacts_attempt_token_fkey`；只有它能改
  `runs.state`/`phase`/`next_event_seq`。
- **F02B（发布）**：审核/版本写入；发布 snapshot 必须 `kind=published` +
  `application_version_id`，并复用 `manifest_artifacts` 记录的同一 artifact/hash；不得为同一
  hash 重写字节。
- **F05/F07**：requests/conversations 及其 FK；F07 还拥有 idempotency 过期清理、deletion 模型与
  墓碑传播。T027 只写 `response_json` 并依赖收据永久可重放。
- **T028（用户上传）**：`run_input` purpose 由服务端写；用户上传的 purpose/扫描/准入属 T028，
  不得借 `artifact_ids` 让任意上传绕过就绪判定（仍要求 ready/本人/未墓碑）。

## 后果（T027）

- 0014 之后，执行身份的写入只有 `debugDraft` 一条路（F02B 会有第二条 published 路径）；任何
  绕过它直接写 snapshot/run/job 的实现都是 bug。队列中的 start job 在 F03 领取前保持 ready。
- debug 的 manifest 字节/hash 与 T025 validate 完全相同（golden 断言），发布必须复用同一
  artifact；同一个草稿从 debug 走到发布不改变被评测的 hash。
- 幂等收据是操作级事实：重放返回原 Run，过期不解除；并发重复由数据库唯一键收敛。
- `X-Manifest-SHA256` 是已记录的契约缺口；契约修订前客户端只能用服务端返回值。

## 证据（T027）

- `product/db/migrations/0014_execution_snapshots_and_runs.sql`
- `product/internal/catalog/{debug.go,debug_test.go}`
- `product/internal/api/{debug.go,server.go}`
- `product/apps/web/src/{debug.html,assets/debug.js}`、`apps/web/src/{editor.html,apps.html,assets/editor.js}`
- `product/tests/support/{t027.py,fixtures.py}`、`product/tests/f02a/test_t027_*.py`
- `product/tests/f02a/test_t023_invalid_draft.py`、`product/tests/f02a/test_t0{23,25}_migration.py`、
  `product/tests/f00b/test_t026_{migration,enqueue,failures}.py`（schema 收紧后的预期适配）
- `product/reports/f02a/T027-debug-submit.md`
