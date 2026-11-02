# F00B T024 验收：后台评测只读取获准文件并记账模型调用

- 任务：F00B / GitHub Issue #25（票 `T024`，规划 ID F00B.03），分支 `feat/f00b-f02a`
- 状态：**passed**（真实 PostgreSQL 16.4 + 真实编译 Go 二进制 + 真实 HTTP/mTLS；模型侧为
  显式注册的 **stub provider profile**，真实 provider 集成在本环境 `not_run`，见 §4/§5）
- 代码提交：
  - `51aae97`（`feat(f00b): job-scoped artifact access, signed links and metered model calls`）
  - `3fe9f32`（`test(f00b): prove job-scoped files, signed links and the model ledger on the real stack`）
  - `8a68e5b`（`fix(f00b): refuse malformed artifact ids and validate signed-link configuration`）
  - 本报告、`PROGRESS.md`、ADR 0004 §18–24 在随后的 `docs(f00b)` 提交中
- 主要文件：
  - `db/migrations/0011_job_files_and_models.sql`（新增：三表、参数摘要列、归属表、延期 FK 注释、权限收口）
  - `internal/objectstore/{sign.go,sign_test.go,objectstore.go}`（签名器、`PutStaging`、key 校验共享）
  - `internal/background/{attempt,artifacts,scope,handler,job,complete}.go`（attempt 校验、job-scoped
    artifact 规则、handler seam、model_validate 完成守卫、重试表）
  - `internal/background/handlers/model_validate.go`（本票真实 handler）
  - `internal/modelproxy/`（provider seam、确定性 stub、真实 provider 未配置路径、`platform_model_calls` 记账）
  - `internal/api/{jobdata,links,internal,server,middleware}.go`、`cmd/api/main.go`
  - `internal/config/config.go`（签名 key/TTL/公网基址/secret 目录）
  - `cmd/maintenance/{scope,client,run}.go`
  - `tests/support/t024.py`、`tests/f00b/test_t024_*.py`、`tests/support/{fixtures,f00b}.py`
  - `docs/adr/0004-f00b-f02a-boundaries.md`（§18–24）
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离开发集群 `127.0.0.1:55432`（`E:\orchara-pg`）
  - Python 3.12.2；`cryptography` 36.0.2（真实 CA/服务端/客户端证书；**未新增依赖**）
  - profile：`dev-stub`（模型候选显式 `provider:"stub"`）；真实 provider 无 endpoint/凭据
  - 基线（T023 之后）：`tests/f00b` 60、`tests/f02a` 45、`tests/f01` 155、`tests/tooling` 21
  - 本票后：`tests/f00b` **86 passed**（其中 T024 26）、`tests/f02a` **45 passed**、
    `tests/f01` **155 passed**、`tests/tooling` **21 passed**；`go vet`/`go test ./...`/`gofmt -l`
    全绿，`sync_spec.py --check` no drift，无失败、无跳过
- 真实/桩分报：对象字节、HTTP、mTLS、数据库、maintenance 进程全部真实；模型调用全部经
  `provider:"stub"` 的显式 dev-stub profile。真实 provider 的**未配置路径**是真实运行的
  （503 + 无 ledger 行 + 不泄露密钥），**厂商协议集成** `not_run`（见 §4/§5）。没有 stub 结果
  被写成真实模型通过。

## 1. 交付内容

### 1.1 迁移 `0011_job_files_and_models.sql`

- `platform.model_profile_versions`：契约基线（provider/model_id/endpoint_ref/secret_ref/config/
  data_destination/manifest_sha256/manifest_artifact_id/validation_state/revoked_at/created_at）。
  `evaluation_report_id` 建列但**无 FK**：`evaluation_reports` 属 T026（0012），文件头/COMMENT 写明
  T026 在自己的迁移里补 FK，之前不得当已验证引用。
- `platform.platform_model_calls`：契约基线 + `params_sha256 text`（偏离，理由：同逻辑键不同参数
  必须是冲突而不是被悄悄计费；只存 JCS SHA-256，不存参数本身）。`request_id` 建列但**无 FK**：
  `platform.requests` 属 F05/F07，由其在自有迁移补 FK；契约的 XOR CHECK
  `((request_id IS NULL) <> (background_job_id IS NULL))` 与
  `UNIQUE(scope_key,logical_key,physical_attempt)` 由数据库强制。`background_job_id` 有 FK
  （0009 已建表）。
- `platform.background_job_artifacts`（新增，偏离）：job→artifact 归属，`PRIMARY KEY
  (background_job_id, artifact_id)`、`relation ∈ {input,output}`、`purpose`、两外键 `ON DELETE
  CASCADE`，用于证明“这个 job 可以读哪些 artifact”。契约的 input/result 单值关联无法表达
  uploading/scanning 期间的产出或多个输出。
- 权限：`REVOKE ALL ON SCHEMA platform FROM PUBLIC`；`orchara_worker` 无 platform USAGE/表/序列
  权限（与 0007–0010 相同）。所有语句 schema 全限定、`IF NOT EXISTS` 幂等。
- 幂等性：空库 `0001→0011` 一次通过；重复 `admin migrate` 输出 `no pending migrations`。

### 1.2 签名对象链接（`internal/objectstore` + `cmd/api` 公共 listener）

- HMAC-SHA256 token 绑定 `op(put|get) + key + artifact_id + exp + max_bytes`；key 来自
  `ORCHARA_OBJECT_SIGNING_KEY`（≥32 字节）。空 key：进程启动、铸造 503
  `object_signing_unavailable`、已签发链接一律拒绝；过短 key：拒绝启动。
- `PUT /object-links/{token}` 只能写 `staging/<64hex>`，`max_bytes` 是 artifact 声明的 size
  （0 表示只接受空体），store 流式写入临时文件 + rename，超限 413 且不留半截对象；
  `GET /object-links/{token}` 只能读 `sealed/<64hex>`。**签名器拒绝铸造 sealed PUT**，验证器拒绝
  op/key 前缀不匹配的 token。
- 无会话、无 CSRF、无列表端点（presigned 语义）；过期/篡改/换 key/换 op/sealed-PUT 一律
  `404 not_found`（不泄露对象是否存在）。请求日志对 `/object-links/` 路径脱敏（新增单测）。
- 链接以 `ORCHARA_PUBLIC_URL`（默认 `http://$ORCHARA_HTTP_ADDR`）铸造，TTL 由
  `ORCHARA_OBJECT_LINK_TTL` 配置（默认 10m，上限 1h）。`BackgroundLease.input_url` 现在是真实
  签名 sealed URL（无输入/未 ready/无签名 key 时保持 null）。

### 1.3 五个内部端点（mTLS listener，maintenance 角色）

全部要求 `job_id` 对应的**活 attempt_token**（`CheckAttempt`：不续租），过期/换 token → 409
`stale_attempt`；job 不存在 → 404 `job_not_found`；格式错误 → 422 `validation_failed`。

- `artifact-uploads`：kind 必须是 `{model_validate, application_evaluate}`；`media_type` 非空、
  `size_bytes ∈ [0,20MiB]`、`purpose ∈ {report,log,receipt,parsed_data}`；同一事务写
  `artifacts(state='uploading', revision=1)` + `background_job_artifacts`，返回 201
  `Upload{artifact_id,upload_url,expires_at}`。
- `artifacts/{id}/complete`：`sha256` 必须是 64 位小写 hex；`uploading→scanning` CAS
  （`revision+1`、写 `expected_sha256`）+ `EnqueueTx(artifact_finalize)` 同事务；同 hash 重复 202
  且只有一个 finalize job，不同 hash/墓碑 409 `artifact_conflict`；跨 job 的 artifact 与不存在
  同为 404。
- `artifact-downloads`：scope = 该 job 的 input / result / `background_job_artifacts` 归属；必须
  `ready` 且未墓碑；返回 200 `Download{url,expires_at}`（sealed GET）。
- `artifact-status`：同一 scope，返回 200 `Artifact{id,state,media_type,size_bytes}`（含
  uploading/scanning/quarantined）。
- `model-calls`：仅 `kind=model_validate`；`model_profile_version_id` 必须等于 `job.target_id`；
  候选必须存在、未撤销、`validation_state ∈ {pending,running}`、引用形状合法；fixture 只取
  `job.input_artifact_id`（sealed/ready），请求体没有 artifact id 字段；返回
  `{call_id,state,result_artifact_id,data}`。

### 1.4 `internal/modelproxy` 与 `platform_model_calls`

- Provider seam：`Providers.For` 只按候选行的 `provider` 与部署 profile 选择；`Available` 在
  **预留 ledger 行之前**判断“能否 dispatch”。
- `stub`：仅 `dev-stub`/`capacity-stub` 且候选 `provider:"stub"`；输出 `provider:"stub"`、
  `stub:true`、`fixture_sha256`/`fixture_bytes`（来自服务器实际读到的 fixture 字节）、
  `check{name,passed,reason}`；tokens_in = ceil(fixture/4)，tokens_out = ceil(response/4)，
  charged = (tokens_in+tokens_out)×1e-6，全部整数计算。fixture 的 `case` 驱动兼容性判定。
- real：`ORCHARA_MODEL_ENDPOINT` 为空 → `ErrNotConfigured`（HTTP 503
  `provider_not_configured`）；有 endpoint 但本批没有编译厂商 adapter（F06 才拥有）→
  `ErrAdapterUnavailable`（503 `provider_adapter_unavailable`）。两者都在写 ledger 前拒绝，因此
  “未配置”不会留下可被读成已计费调用/成功的行。密钥只经候选 `secret_ref`（`env:NAME` /
  `file:NAME`，后者在 `ORCHARA_SECRET_DIR` 内）在 Go 内解析；错误、HTTP 响应、日志都不含密钥值
  或引用字符串。
- 记账：每次物理 attempt 一行；job 行锁内 `max(physical_attempt)+1`（UNIQUE 兜底）；`prepared`
  → `dispatching` → `succeeded`/`failed`；成功时把 provider 规范响应写为 sealed、ready、
  `purpose='model_result'` 的 artifact 并登记归属，再关闭 ledger；失败 attempt
  `state='failed', charged_amount=0`，不挂 result artifact。同逻辑键+同 params 且已成功 → 幂等
  返回原 call；不同 params → 409 `model_call_conflict`。

### 1.5 `model_validate` handler

- `internal/background/handlers/model_validate.go` 注册进 `handlers.Registry`，并在**同一变更**
  里扩展 `retryableByKind[model_validate]`（`transient_storage_io`/`transient_internal`/
  `lease_expired`）。
- 流程：下载 fixture（job-scoped signed link）→ 调 model-calls（logical key 由 fixture SHA 派生）
  → 生成兼容性报告（JCS 字节）→ upload/PUT/complete → 轮询 status 直到 `ready`（期间心跳续租）
  → 提交 `succeeded` + `result_artifact_id`。
- 兼容性失败是报告结果（job succeeded、report `compatible:false`）；provider 未配置/无 adapter
  是 business failure（不重试）；只有基础设施码可重试。
- 服务端 `Complete` 对 `model_validate` 额外校验 result artifact：存在、属于 job owner、在该 job
  的归属表、`ready` 且未墓碑，否则 422 且不改 job。
- T022 的 maintenance 循环是单线程：测试用两个真实进程（model_validate 处理 + artifact_finalize
  服务）驱动；**运行时双池属 T026**，本票不改调度器（ADR §23/§24）。

### 1.6 配置与接线

- 新增 env：`ORCHARA_OBJECT_SIGNING_KEY`、`ORCHARA_OBJECT_LINK_TTL`、`ORCHARA_PUBLIC_URL`、
  `ORCHARA_SECRET_DIR`；`cmd/api` 构造 signer/providers，`api.New` 在既有参数后追加
  `signer`/`providers`/`publicURL`（T022/T023 路由不变）。
- `cmd/maintenance` 的 `internalClient` 实现 `background.JobScope`（download/upload/complete/
  status/model-call + 签名链接的 GET/PUT）；handler 不再需要 maintenance DSN 读业务行
  （artifact_finalize 的 T022 interim 读保留，T026 迁移）。

## 2. 测试与原始结果

### 2.1 测试文件（真实 PG / 二进制 / HTTP / mTLS）

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `tests/f00b/test_t024_migration.py` | 6 | 三表列/类型/默认值、两个延期 FK 真不存在、XOR/attempt 唯一/归属表约束、worker/PUBLIC 拒绝、重复 migrate no-op |
| `tests/f00b/test_t024_artifacts.py` | 5 | upload→真实 PUT→complete 202→真实 finalize→status ready→真实 GET 字节一致（media/size/hash）；upload 参数/kind 拒绝；complete 幂等/异 hash 冲突/跨 job 404；scope 隔离与未 ready 409；lease `input_url` 真实可取 |
| `tests/f00b/test_t024_links.py` | 5 | 过期（第二个进程 1s TTL）、篡改/换 op/sealed-PUT 404、异 key 404、超限 413 且无残留对象、无签名 key 时 API 仍启动且铸造 503/拒绝旧链接 |
| `tests/f00b/test_t024_model_calls.py` | 7 | ledger 行全字段与真实 usage；fixture 内容决定结果；候选/kind/validation_state 限制；同键幂等、异参 409；失败后重试 attempt=2；stale/畸形 token；真实 provider 503 + 零 ledger + 密钥/引用不泄露 |
| `tests/f00b/test_t024_model_validate.py` | 3 | 端到端报告 artifact ready + ledger；兼容性失败是 report 失败而非 job 失败；provider 未配置 business 失败且不重试 |

另有 Go 单测：`internal/objectstore/sign_test.go`（8）、`internal/modelproxy/modelproxy_test.go`
（8）、`internal/api/middleware_test.go` 新增 1（token 日志脱敏）。

### 2.2 收尾门禁（实际命令与原始输出尾部）

```text
=== gofmt -l internal cmd tools
(no output)

=== go vet ./...
(no output)

=== go test -count=1 ./...
?   	orchara/platform/cmd/admin	[no test files]
?   	orchara/platform/cmd/api	[no test files]
?   	orchara/platform/cmd/maintenance	[no test files]
?   	orchara/platform/cmd/supervisor	[no test files]
?   	orchara/platform/cmd/worker	[no test files]
?   	orchara/platform/db	[no test files]
ok  	orchara/platform/internal/api	2.325s
?   	orchara/platform/internal/audit	[no test files]
ok  	orchara/platform/internal/background	1.821s
?   	orchara/platform/internal/background/handlers	[no test files]
ok  	orchara/platform/internal/catalog	1.941s
ok  	orchara/platform/internal/config	1.034s
ok  	orchara/platform/internal/db	0.811s
ok  	orchara/platform/internal/health	2.025s
ok  	orchara/platform/internal/identity	2.053s
ok  	orchara/platform/internal/jcs	1.544s
ok  	orchara/platform/internal/modelproxy	2.028s
ok  	orchara/platform/internal/objectstore	0.672s
ok  	orchara/platform/internal/policy	1.731s
ok  	orchara/platform/internal/supervisor	1.626s
ok  	orchara/platform/internal/uuid	1.576s
ok  	orchara/platform/internal/workflow	1.662s

=== pytest tests/f00b
........................................................................ [ 83%]
..............                                                           [100%]
86 passed in 215.58s (0:03:35)

=== pytest tests/f02a
.............................................                            [100%]
45 passed in 143.20s (0:02:23)

=== pytest tests/f01
........................................................................ [ 92%]
...........                                                              [100%]
155 passed in 457.49s (0:07:37)

=== pytest tests/tooling
.....................                                                    [100%]
21 passed in 4.68s

=== python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest

=== git status --short
M product/PROGRESS.md
 M product/docs/adr/0004-f00b-f02a-boundaries.md
?? product/reports/f00b/T024-job-files-and-models.md
```

（`git status` 是文档提交前的状态：代码与测试已分别在 `51aae97`/`3fe9f32`/`8a68e5b` 提交，只剩
本报告、`PROGRESS.md` 与 ADR 的文档改动；文档提交后 `git status --short` 为空。）

## 3. 验收标准逐条

| 票面验收 | 状态 | 证据 |
|---|---|---|
| 后台凭据不能读任意用户文件 | passed | 所有 job-scoped 读取都先校验活 attempt_token；scope 一条 SQL（input/result/归属表），越界与不存在同为 `404 artifact_not_found`；`tests/f00b/test_t024_artifacts.py::test_artifact_scope_is_job_scoped`（他人 artifact、另一 job artifact、跨 job complete）、`...::test_a_leased_job_carries_a_real_input_url`；签名 token 只能打开它自己的 key |
| `model_validate` 只用对应候选和合成 fixture | passed | 端点拒绝不同候选（422 `candidate_mismatch`）、撤销/已通过候选（422 `candidate_unavailable`）、非 model_validate kind（422 `model_not_allowed`）；fixture 只从 `job.input_artifact_id` 读取，请求体无 artifact 字段；`test_t024_model_calls.py::test_only_the_jobs_candidate_and_kind_may_call_a_model`、`test_the_handler_produces_a_validated_candidate_report` |
| 每次调用有用量 | passed | 每次 attempt 一行 ledger：`tokens_in/tokens_out` 非空真实值（由 fixture/response 字节推导）、decimal reserved/charged、state、physical_attempt、result artifact；`test_every_model_call_is_metered_with_real_usage`、`test_a_retry_after_a_failed_attempt_is_a_new_physical_attempt` |
| 通过端到端入口实际演示；失败、授权与重试/恢复边界有断言 | passed | `test_t024_model_validate.py` 用真实 maintenance 进程跑完 claim→fixture→model-call→upload/complete→wait→succeeded；失败边界：兼容性失败是 report、provider 未配置是 business 失败（attempt_count=1，不重试）；授权边界：stale=409、跨 job=404；重试边界：可重试码表 + attempt 计数 |
| 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件 blocked，未运行 not_run | passed | 本报告 §1–§5：提交、环境、命令原始输出、26 个集成用例、stub 与 real 分开；真实 provider 集成 `not_run`（无 endpoint/凭据、adapter 属 F06） |

票面「完成口径」要求的持久化、服务端校验、接口、行为测试同票交付；本票是控制面/实验票，
不新增用户页面（票面明确不强行加管理页）。

## 4. stub 与 real 分报

| 项 | stub（真实运行） | real（本环境） |
|---|---|---|
| provider 选择 | 仅 `dev-stub`/`capacity-stub` + 候选 `provider:"stub"`；其他 profile 拒绝 | `ORCHARA_MODEL_ENDPOINT` 未配置 → 503 `provider_not_configured`；配置但无 adapter（F06）→ 503 `provider_adapter_unavailable` |
| 输出标注 | `data.provider="stub"`、`stub=true`；报告同样标注 | 未运行：`not_run`（无供应商 endpoint/凭据） |
| usage/费用 | 真实数字（fixture/response 字节推导）；写 ledger | 未运行 |
| 密钥边界 | n/a | 密钥只经 `secret_ref` 在 Go 内解析；Go 单测证明 `env:`/`file:` 解析、缺失即 ErrNotConfigured、错误不含值/引用；HTTP 测试证明 503 响应与 API 日志都不含密钥值或 `ORCHARA_MODEL_SECRET` |
| 结论 | 可重现、可断言 | **不能写成通过**：本批没有任何真实模型调用 |

“兼容性通过”在本报告中只表示 stub 的确定性检查通过；它不是“真实模型已验证”。

## 5. 未运行 / 阻塞（如实）

| 项 | 状态 | 归属/说明 |
|---|---|---|
| 真实模型 provider 集成（端到端，含 usage/取消/流式/目的地） | `not_run` | 本机无供应商 endpoint/凭据；厂商协议 adapter 属 F06（本批不臆造 wire format）；真实路径的未配置/无 adapter 503 与密钥边界已真实运行并测试 |
| 病毒查杀/深格式扫描 | `not_run` | 本批只做 hash/size/tombstone/media-type 准入；F05/T028 承接 |
| S3/MinIO 对象后端 | `not_run` | 本地 FS 实现；本机无 S3 |
| Redis 唤醒 | `not_run` | 本机无 Redis；PG 轮询已由 T022 实测 |
| 运行时双池（artifact_finalize 独立 system_io 池） | `not_run`（属 T026） | T024 用两个真实进程证明 handler 可等待 finalize；T026 实现调度器池化 |
| `prepared`/`unknown` ledger 行的清扫与对账 | `not_run`（属 F05/F13A） | 崩溃可能留下永不成功的 `prepared` 行；本批不谎称已清扫 |
| 浏览器 UI | 不适用 | 本票无用户界面要求（控制面/内部协议票） |

## 6. 契约缺口与实现记录的偏差

1. **契约没有对象链接端点**：`createBackgroundUpload` 返回 `upload_url`，`createBackgroundDownload`
   返回 `url`，但 `contracts/openapi.json` 未定义 `/object-links/{token}` 的 PUT/GET。T024 按
   ADR 0004 §19 在公共 listener 实现 presigned 传输路径（非契约扩展）；响应仍只出现在
   `Upload`/`Download` 的 `uri` 字段里。后续票不得把该路径当用户 API。
2. **`BackgroundLease.input_url` 的形态**在契约里只是 `type: string`；本实现返回绝对 URL
   （`ORCHARA_PUBLIC_URL`），无输入/未 ready 时为 `null`。部署必须设置可达的 `ORCHARA_PUBLIC_URL`。
3. **`platform_model_calls` 基线缺参数身份**：新增 `params_sha256` 偏离（ADR §18/§22），否则
   “同逻辑键不同参数冲突”无法实现。
4. **`platform_model_calls.request_id` 与 `model_profile_versions.evaluation_report_id` 的 FK
   延期**到 F05/F07 与 T026；本批不建 `requests`/`evaluation_reports`，不伪造引用。
5. **model-calls 请求体没有 artifact id**：fixture 只能由服务端按 `job.input_artifact_id` 决定；
   这是刻意的安全边界，不是契约缺口。
6. **`artifact-status` 的 `size_bytes`**：契约要求非空 integer，而 `artifacts.size_bytes` 可空；
   T024 创建的行总有值，极端历史行为 0（代码注释说明），不伪造未知大小。

## 7. 下一票（T025，然后 T026）可以依赖的

- 端点：内部 mTLS（maintenance 角色）5 条 —— `artifact-uploads`、`artifacts/{id}/complete`、
  `artifact-downloads`、`artifact-status`、`model-calls`；全部要求活 attempt_token。
- 错误码：`stale_attempt`(409)、`job_not_found`(404)、`artifact_not_found`(404)、
  `artifact_conflict`/`artifact_not_ready`(409)、`object_signing_unavailable`/
  `object_store_unavailable`/`provider_not_configured`/`provider_adapter_unavailable`(503)、
  `model_call_conflict`(409)、`candidate_mismatch`/`candidate_unavailable`/`model_not_allowed`/
  `fixture_missing`(422)、`payload_too_large`(413)。
- 表：`platform.model_profile_versions`、`platform.platform_model_calls`、
  `platform.background_job_artifacts`；`background_jobs.result_artifact_id` 由 Complete 记录。
- env：`ORCHARA_OBJECT_SIGNING_KEY`、`ORCHARA_OBJECT_LINK_TTL`、`ORCHARA_PUBLIC_URL`、
  `ORCHARA_SECRET_DIR`（对象目录与内部 mTLS 变量沿用 T022）。
- 共享实现：`objectstore.Signer`/`PutStaging`、`background.ArtifactStore`、`modelproxy.Service`、
  `background.JobScope`、`complete.verifyJobResultArtifact`；T025/T026 只扩展 kind/错误码，
  不得复制签名器/记账/scope 逻辑。
- T026：`application_evaluate` 已允许创建 artifact；需在同一 `retryableByKind` 扩展自己的码，
  并实现 runtime 双池；`input_url` 可直接用，但等待报告 ready 时仍应走 `artifact-status` 续租。

## 8. 工作日志（实际命令摘要）

```text
# 迁移/实现后
go build ./...                         # ok
go vet ./...                           # ok（无输出）
gofmt -l internal cmd tools            # 空
go test ./...                          # 全绿

# T024 定向
python -m pytest tests/f00b/test_t024_migration.py -q      # 6 passed
python -m pytest tests/f00b/test_t024_artifacts.py -q      # 5 passed
python -m pytest tests/f00b/test_t024_links.py -q          # 5 passed
python -m pytest tests/f00b/test_t024_model_calls.py -q    # 7 passed
python -m pytest tests/f00b/test_t024_model_validate.py -q # 3 passed

# 回归/全量
python -m pytest tests/f00b -q         # 86 passed
python -m pytest tests/f02a -q         # 45 passed
python -m pytest tests/f01 -q          # 155 passed
python -m pytest tests/tooling -q      # 21 passed
python tools/sync_spec.py --check      # no drift
git status --short                     # 仅本票文件（提交前）
```

> 说明：T023 的迁移测试原先硬编码迁移链止于 `0010`，0011 落地后同步改为断言链到 `0011` 且
> `0010` 仍在其位置；这是新迁移的预期适配，不是放宽断言（`tests/f02a/test_t023_migration.py`）。
