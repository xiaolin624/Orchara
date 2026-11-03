# F05 T028 验收：上传、校验封存并授权下载不可覆盖的文件

- 任务：F05 / GitHub Issue #29（票 `T028`，规划 ID F05.01），分支 `feat/f05-f03-run-and-tools`
- 状态：**`passed`**（本票范围 = 用户上传 + 格式准入 + owner-only 读取）。真实 PostgreSQL 16.4 +
  真实编译二进制 + 真实 HTTP + 真实文件系统对象字节 + 真实 HMAC 签名 URL + 真实
  `cmd/maintenance run --once --kinds artifact_finalize`；无 mock、无跳过。
- 主要文件：
  - `db/migrations/0015_artifact_admissions.sql`（新增 `platform.artifact_admissions`，唯一新表）
  - `internal/artifacts/{upload.go,admission.go,service.go,admission_test.go}`（上传服务、允许清单、
    格式/解压/宏/路径准入分类器）
  - `internal/api/{artifacts.go,server.go}`（5 个契约端点 + 页面/资产路由）
  - `internal/background/handlers/artifact_finalize.go`（复用既有流水线：worker 半场对真实字节做准入、
    持久化 admission、拒绝即删除本尝试对象；激活仍只由完成守卫决定）
  - `internal/background/{job.go,format_test.go}`（`artifact_format_rejected` /
    `artifact_upload_incomplete`，quarantine-implying、business、不可重试）
  - `apps/web/src/{attachments.html,assets/attachments.js}`、`apps.html`/`debug.html` 入口
  - `tests/support/{t028.py,fixtures.py}`、`tests/f05/test_t028_*.py`（6 文件 31 用例）
  - `tests/f00b/test_t026_migration.py`、`tests/f02a/test_t0{23,25,27}_migration.py`（迁移链尾部
    0014 → 0015 的预期适配）
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`tools/pg_dev.py`）
  - Python 3.12.2（`python -m pytest`）、Node 24.13.0（页面 JS 语法与行为真实执行）
  - profile `dev-stub`；对象目录在测试 `tmp_path`；签名 key 每测试随机生成、不落日志/仓库
- 真实/桩分报：**全部 real**（PG/二进制/HTTP/签名 URL/对象字节/Node 执行页面逻辑）。没有 stub
  路径参与本票验收；唯一的 seeded 行是“公共入口有意拒绝、但存量行仍可能到达 handler”的对抗
  用例（不支持的声明类型、超过 20 MiB 的暂存对象），其行、字节、job、guard 都是真实的。

## 0. 工作记录（简短）

1. 读共享 NOTES、票面、AGENTS/HANDOFF/PROGRESS、BACKGROUND-JOBS、EXECUTION §1、
   IMPLEMENTATION-DETAILS §9、SECURITY §4/§5、ADR 0004 §2/§5/§6/§18–24/§38–44/§53、
   T022/T024 报告；确认分支、干净树、0014 为最新迁移。
2. 先写迁移测试（红）→ 写 0015 → 4 用例绿；再写格式准入/上传字段的 Go 单测（红）→ 实现
   `internal/artifacts` 分类器和服务。
3. 写真实栈上传测试（红 404）→ 实现 5 个端点、路由与 owner-only/scope 规则；happy path 红在
   “没有 admission 行” → 在既有 finalize handler 里加入真实字节准入与 admission 持久化。
4. 补 quarantined/ACL/删除竞争/页面测试；页面 JS 由 Node 24 行为执行；harden：仅对 user-upload
   purpose 做 T028 准入（报表/日志等 job 产物保持原有语义），XLSX 允许目录条目但拒绝穿越。
5. 跑门禁、写本报告 / `PROGRESS.md` / NOTES，按 feat/test/docs 提交；不推送、不关闭 issue、
   未修改 `docs/spec/**` 与 `contracts/**`。

## 1. 交付内容

### 1.1 迁移 `0015_artifact_admissions.sql`

- 只新增一张表：`platform.artifact_admissions(artifact_id PK/FK, declared_media_type,
  sniffed_media_type NULL, detected_format NULL, verdict, reason NULL, checked_at)`。
- `artifact_id` 是 `platform.artifacts(id)` 的真实外键（`ON DELETE CASCADE` 仅作未来物理清理
  的安全网，本批不建删除模型）；一 artifact 一行，handler 重试 upsert 同一事实。
- CHECK：`verdict ∈ {accepted, rejected}`；accepted 必须有 sniffed_media_type + detected_format
  且无 reason；rejected 必须有 reason；reason 非空白且 ≤200；detected_format 是固定 7 值。
- 迁移头列出全部偏离（新表、两个 nullable 的“无字节可检”原因、无删除模型/请求/工具表）及
  owner（F07/T037）。`REVOKE ALL ON SCHEMA platform FROM PUBLIC`，`orchara_worker` 无
  USAGE/表/序列权限；重复 migrate no-op。未建 deletion/requests/conversations/tool 表。

### 1.2 上传服务与格式准入（`internal/artifacts`）

- `UploadInput` 精确校验：`media_type` 固定允许清单（text/plain、text/csv、application/json、
  xlsx 官方类型；大小写归一、带参数类型拒绝）、`size_bytes` ∈ [1, 20971520]、`purpose` ∈
  {task_input, tool_source, knowledge, evaluation_dataset}；错误定位到字段。
- `InspectUpload` 只信真实字节：
  - text/plain：UTF-8、无 NUL、非 HTML/zip/二进制；csv 另做 `encoding/csv` 解析；
  - JSON：UTF-8 + `json.Valid`；
  - XLSX：必须 zip；条目数 ≤512、解压总量 ≤64 MiB；拒绝 `vbaProject.bin`/`xl/macrosheets/`/
    `macroEnabled`；拒绝绝对路径、反斜杠、`..`/`.` 段（目录条目允许）；必须有
    `[Content_Types].xml` + `xl/workbook.xml`；
  - 声明类型与真实内容不一致、空内容、不支持类型、非法编码/JSON/CSV → 固定安全 reason code
    （不含字节、文件名、URL）。
- `Service.CreateUpload`：预分配不同 staging/sealed key，插 `uploading` revision=1 行，签发
  仅 staging 的 PUT 链接（max_bytes=声明 size）。
- `Service.CompleteUpload`：owner-only；CAS `uploading→scanning`（revision+1）+ 同事务
  `artifact_finalize` job（`expected_revision`=新 revision、`artifact:<id>:<sha>` dedup）；
  同 hash 在 scanning/ready 幂等且不建第二个 job；异 hash 409 `artifact_hash_conflict`；
  deleted/quarantined 409 `artifact_conflict`；不读对象存储、不激活。
- `Get`/`DownloadURL`/`OpenContent`：owner-only（管理员/他人/畸形/已删/隔离 = 404）；
  ready 才发 sealed GET 或流式内容；uploading/scanning = 409 `artifact_not_ready`。

### 1.3 API

| operationId | 端点 | 成功 | 权限 / 说明 |
|---|---|---|---|
| `createUpload` | `POST /api/v1/artifacts/uploads` | 201 `Upload` | 会话 CSRF；API key `application:write`；普通用户可上传（不要求 creator） |
| `completeUpload` | `POST /api/v1/artifacts/{id}/complete` | 202 `Artifact` | owner-only（他人 404）；写 scope |
| `getArtifact` | `GET /api/v1/artifacts/{id}` | 200 `Artifact`（4 字段） | owner-only；读 scope；quarantined 时附 `X-Artifact-Quarantine-Reason` |
| `downloadArtifact` | `GET /api/v1/artifacts/{id}/download` | 200 `Download` | owner-only；ready 才签发 sealed GET；隔离 404、未 ready 409 |
| `readArtifactContent` | `GET /api/v1/artifacts/{id}/content` | 200 octet-stream | owner-only；`Content-Disposition: attachment`；流式复制，不把 20 MiB 读进内存 |

- 页面：`GET /attachments` 与 `GET /assets/attachments.js`（编译期常量路径；apps/debug 导航入口）。
- 新错误码：`artifact_hash_conflict`(409)、`artifact_conflict`(409)、`artifact_not_ready`(409)、
  复用 `validation_failed`(422，`details.field`)、`resource_not_found`(404)、
  `insufficient_scope`(403)、`object_store_unavailable`/`object_signing_unavailable`(503)。
  新 job 业务码：`artifact_format_rejected`、`artifact_upload_incomplete`（均 quarantine）。

### 1.4 finalize handler 的格式准入（复用既有流水线）

- 只在 handler 既有“读 staging → hash/大小 → 写 attempt sealed → 报告”之后增加：当 artifact
  purpose 是 user-upload 时，读取**本尝试的** sealed 对象（≤20 MiB）、`InspectUpload`、upsert
  admission；拒绝则删除本尝试对象并返回 `artifact_format_rejected`。激活仍只由
  `background.Complete` 的 guard 完成；handler 不写 state/sha。
- 业务失败路径（staging 缺失、超 20 MiB、声明 size/hash 不符）也为 user upload 写拒绝 admission
  （reason=稳定码）；`artifact_staging_missing` 对 user upload 映射为新的
  `artifact_upload_incomplete`（quarantine），T022 原码含义保持不变。
- `ImpliesQuarantine`/重试分类一致：两个新码在 `quarantineCodes`，不在 `retryableByKind`，
  `ClassifyFailure` 必为 business（Go 单测钉死）。
- 非 user-upload purpose（report/log/receipt/parsed_data）不走 T028 准入，T024/T026 的产物语义
  不变。

### 1.5 Web 附件页

- `attachments.html` + `attachments.js`：选择文件/声明类型/用途 → 浏览器 `crypto.subtle` 计算
  SHA-256 → createUpload → PUT signed staging URL（`credentials:'omit'`、无 CSRF/Authorization）
  → complete → 轮询 GET 显示 uploading/scanning/ready/quarantined；ready 才显示下载按钮，点击时
  才获取新的短时下载链接；quarantined 用安全中文文案 + reason code 展示，全部用 `textContent`。
- 页面无内联用户内容脚本、无 localStorage/sessionStorage、无查询串凭据、不展示/记录签名 URL。

### 1.6 测试（`tests/f05`，全部真实栈）

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `test_t028_migration.py` | 4 | 链尾 0015、唯一新表列/约束/FK、worker/PUBLIC 拒绝面、重复 migrate no-op |
| `test_t028_upload_flow.py` | 6 | 201 预分配两 key、PUT staging、complete CAS+单 job、finalize ready、下载/内容精确字节、**校验后覆盖 staging 不变**、同 hash 幂等/异 hash 409、字段/ sha 校验 |
| `test_t028_quarantine.py` | 10 | hash/size/超 20 MiB/不支持类型/非法 JSON/宏 XLSX/声明类型不一致/缺失 staging → quarantined，admission reason、不可下载/读、合法 XLSX 通过、隔离后不可再 complete |
| `test_t028_authz.py` | 5 | 他人/管理员 404、匿名 401、CSRF 403、API key read/write scope、普通 user 可上传 |
| `test_t028_race.py` | 1 | 真实 lease 在飞 + F07 tombstone 列 → guard `source_deleted`、永不 ready/可读、仅删除本尝试对象 |
| `test_t028_pages.py` | 5 | 页面结构/表单规则、JS 资产、apps/debug 入口、Node 语法、Node 行为（SHA-256 已知向量、请求/CSRF、无凭据 PUT、reason header、轮询停止规则） |

## 2. 票面验收逐条

| # | 验收 | 状态 | 证据 |
|---|---|---|---|
| 1 | 校验后覆盖 staging 不改变 sealed | **passed** | `test_overwriting_staging_after_ready_never_changes_the_sealed_bytes`：ready 后再次 PUT 同一 staging URL，staging 文件确实变了，`artifact.object_key` 字节/hash/revision 不变，下载与 content 仍是原字节 |
| 2 | 同 hash complete 幂等、异 hash 冲突 | **passed** | `test_the_same_hash_is_idempotent_and_a_different_hash_conflicts`：scanning/ready 重放同 4 字段、job 行始终 1；异 hash 409 `artifact_hash_conflict` 且零新 job |
| 3 | 删除/finalize 竞争不复活 | **passed** | `test_a_tombstone_written_while_finalize_is_in_flight_wins`：真实 claim 在飞、写 F07 tombstone 列后 guard 返回 `source_deleted`，artifact 保持 deleted、sha NULL、不可读、无 sealed 对象；DELETE 端点仍属 F07、未实现 |
| 4 | 通过端到端入口实际演示；失败/授权/重试边界有断言 | **passed** | 真实 HTTP create→PUT→complete→maintenance→ready→download/content；失败矩阵 10 用例；ACL 5 用例；重试边界 = 同 hash 幂等/异 hash 冲突/隔离后不可重试；无单层 mock |
| 5 | 记录版本、输入/hash、命令、原始结果、profile；stub/real 分报，blocked/not_run | **passed** | 本报告页首 + §3/§4/§6；real/stub 分报；未运行项明确 `not_run` |

Brief 交付项：

| 项 | 状态 |
|---|---|
| 0015 迁移（admission 表、无越界表、权限收口、重复 no-op） | **passed**（迁移测试 4 用例） |
| createUpload：精确校验、预分配 sealed/staging、201 Upload + 短时 PUT staging URL | **passed** |
| completeUpload：owner-only、CAS + 单 job 同事务、幂等/冲突、不激活 | **passed** |
| finalize handler 真实字节准入 + admission 持久化 + 新 quarantine 码 + 分类器单测 | **passed** |
| 读取：4 字段 Artifact、ready 下载、ready 流式内容、404/409 规则、隔离不可读 | **passed** |
| API-key scope 过渡映射（write/read）与普通用户上传（无 creator 要求） | **passed** |
| Web 附件页（选择/上传/轮询/下载/隔离原因） | **passed（逻辑层 + 真实页面 HTTP）**；浏览器 DOM `not_run`（Node 行为执行） |
| 测试 31 用例 + 报告/PROGRESS/NOTES | **passed** |

## 3. 实际命令与原始结果

工作目录 `E:\Orchara\product`；Go 环境变量由 `tools/toolchain.py` 提供。

### 3.1 Go

```text
> E:\orchara-toolchains\go\bin\gofmt.exe -l internal cmd tools db
（无输出）

> E:\orchara-toolchains\go\bin\go.exe vet ./...
（无输出，exit 0）

> E:\orchara-toolchains\go\bin\go.exe test ./... -count=1
?   	orchara/platform/cmd/admin	[no test files]
?   	orchara/platform/cmd/api	[no test files]
?   	orchara/platform/cmd/maintenance	[no test files]
?   	orchara/platform/cmd/supervisor	[no test files]
?   	orchara/platform/cmd/worker	[no test files]
?   	orchara/platform/db	[no test files]
ok  	orchara/platform/internal/api	2.884s
ok  	orchara/platform/internal/artifacts	2.245s
?   	orchara/platform/internal/audit	[no test files]
ok  	orchara/platform/internal/background	1.930s
ok  	orchara/platform/internal/background/handlers	2.420s
ok  	orchara/platform/internal/catalog	2.188s
ok  	orchara/platform/internal/compiler	1.411s
ok  	orchara/platform/internal/config	0.911s
ok  	orchara/platform/internal/db	1.447s
ok  	orchara/platform/internal/health	2.215s
ok  	orchara/platform/internal/identity	2.724s
ok  	orchara/platform/internal/jcs	1.867s
ok  	orchara/platform/internal/modelproxy	2.462s
ok  	orchara/platform/internal/objectstore	1.208s
ok  	orchara/platform/internal/policy	0.575s
ok  	orchara/platform/internal/supervisor	0.963s
ok  	orchara/platform/internal/uuid	1.065s
ok  	orchara/platform/internal/workflow	1.168s
```

### 3.2 Python 真实栈

```text
> python -m pytest tests/f05 -q
...............................                                          [100%]
31 passed in 110.20s (0:01:50)
```

五个套件的收尾原始结果见 §3.3；`tests/f05` = 迁移 4 + 上传 6 + 隔离 10 + ACL 5 + 竞争 1 +
页面 5 = **31**。

### 3.3 门禁（五套件）

```text
> python -m pytest tests/f05 tests/f00b tests/f02a tests/f01 tests/tooling -q
........................................................................ [ 16%]
........................................................................ [ 32%]
...（略）
........................................................................ [ 96%]
..............                                                           [100%]
446 passed in 1528.82s (0:25:28)
```

按套件：`tests/f05` 31、`tests/f00b` 135、`tests/f02a` 104、`tests/f01` 155、`tests/tooling` 21
（`--collect-only -q` 逐个核对，31+135+104+155+21 = 446）。全部真实 PG/二进制/HTTP/对象字节；
无 skip、无 xfail。

### 3.4 迁移

空库 `0001→0015` 一次通过（15 migration(s) applied）；重复 `admin migrate` 输出
`no pending migrations`；`tests/f05/test_t028_migration.py` 断言链尾/列/约束/FK/权限；
T023/T025/T026/T027 的迁移链断言同步把尾部从 0014 适配到 0015（预期适配，非放宽）。

### 3.5 契约同步

```text
> python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

## 4. 契约缺口与显式偏离（如实）

1. **隔离原因没有契约字段**：`Artifact` 是 `additionalProperties:false` 的 4 字段，无处放
   quarantine reason。`getArtifact` 在 state=quarantined 时用**非契约响应头**
   `X-Artifact-Quarantine-Reason` 返回固定安全 reason code（同 `X-Manifest-SHA256` 先例）。
   后续契约修订应把它变成正式字段或新增 admission 读端点；客户端不得自行推断原因。
2. **`artifact_admissions` 是新表**（`contracts/database.sql` 无）：本票唯一结构偏离，理由/
   语义/owner 写在 0015 头注释。它不是激活权威，只是 handler 的准入证据。
3. **API-key scope 是过渡映射**：平台仍只有 `application:read`/`application:write` 两个
   scope；本票 create/complete 用 write、读用 read。更细的 `artifact:*` scope 与注册表扩展
   属后续契约修订；scope 先于 owner ACL 判定，scope 不等于可见。
4. **管理员不是任意用户正文读者**：5 个端点全部 owner-only；管理员/read grant 对他人 artifact
   一律 404（本票显式选择；诊断授权的审计模型属 F07/F09）。
5. **“扫描”的定义**：首版 = hash/大小/UTF-8/JSON/CSV/zip 宏/条目/解压总量/路径/类型一致性准入；
   未接入病毒查杀或 XLSX 工作表/行级解析（IMPLEMENTATION-DETAILS §9 允许，UI 不宣称查杀）。
   工作表/行数/公式等深解析属 `document_ingest`/F13A。
6. **DELETE 未实现**：删除模型、tombstone 传播与 DELETE 端点属 F07；本票只用 F07 将写的
   `deleted_at/state=deleted/revision+1` 列做竞争测试，不建删除任务。
7. **`purpose` 集合**：UploadInput 用契约的 4 个用户用途；job 产物的 report/log/receipt/
   parsed_data 仍由 T024 job-scoped 端点创建。handler 只对用户用途做 T028 准入，避免改变
   T024/T026 语义。
8. **`artifact_staging_missing` 的既有含义未改**：T022 原语仍“job 失败、artifact 不隔离”；
   user upload 用新的 `artifact_upload_incomplete`（quarantine）以免页面永远停在 scanning。
9. **重复 complete 不重跑 EnqueueTx**：实现直接返回已记录的 4 字段视图；job 的唯一性由首次
   同事务创建 + `UNIQUE(kind,dedup_key)` 保证，测试断言 job 行始终 1。
10. **handler 写入 admission 行是对 ADR 0004 §5/§23“handler 不写业务行”的窄偏离**：admission 是
   insert-once 的准入证据，不含 state/sha/激活，由可信 handler 通过 maintenance DSN upsert；
   激活仍只由完成守卫在同一事务完成。把准入结果塞进结果协议需要扩展内部 Completion 字段
   （当前只有 state/error_code/result_artifact_id），属更大的契约变更；本票选择窄偏离并在此记录。

## 5. 边界与未运行（not_run）

- **F07 删除**：DELETE 端点、deletion_jobs/副本清理/墓碑传播未实现；竞争测试只写 F07 将写的
  列并调用真实 guard。
- **病毒查杀/深格式扫描**：未选择引擎、未运行；不宣称能识别全部恶意文件。
- **XLSX 工作表/行数/公式/单元格语义**：未解析；只做 zip 元数据准入。属 `document_ingest`/
  F13A。
- **浏览器 DOM 渲染**：无 headless browser；页面逻辑用 Node 24 真实执行，页面经真实 HTTP 服务。
- **全量 `python -m pytest tests -q`**：本票按 brief 跑五套件（§3.3），其余套件（p0–p05 等）
  未被本票改动；未重跑全量 `tests`，记为 `not_run`。
- **真实对象存储服务（S3/MinIO）**：仍用本地文件系统实现；S3 属后续部署票。
- **管理员诊断授权读取正文**：不在本票范围（显式 404），属 F07/F09 的审计授权模型。
- **T029+**：未实现 run_attempts/claim/events/cancel/SSE、工具构建、删除模型。

## 6. 后续票可依赖的接口与表

- 表：`platform.artifact_admissions(artifact_id PK/FK, declared_media_type, sniffed_media_type,
  detected_format, verdict, reason, checked_at)`；`platform.artifacts` 仍是唯一状态权威
  （uploading/scanning/ready/quarantined/deleted + revision），本票未加列。
- 端点：`createUpload`(201 Upload)、`completeUpload`(202 Artifact)、`getArtifact`(200 Artifact，
  quarantined 带 `X-Artifact-Quarantine-Reason`)、`downloadArtifact`(200 Download)、
  `readArtifactContent`(200 octet-stream)；错误码见 §1.3。
- 服务入口：`artifacts.New(...)` / `CreateUpload` / `CompleteUpload` / `Get` / `DownloadURL` /
  `OpenContent`；准入分类器 `artifacts.InspectUpload`（纯函数，Go 单测）。
- finalize 语义：user-upload purpose 会写 admission 并可能返回 `artifact_format_rejected`/
  `artifact_upload_incomplete`；guard 负责 quarantined；激活仍只由 guard 完成。
- F07 删除时必须同时：在 tombstone 事务后让 finalize guard 继续拒绝激活（已有测试
  `test_a_tombstone_written_while_finalize_is_in_flight_wins`）、清理 staging/sealed/attempt
  对象与 admission 行的保留策略。
- F03 执行若引用用户上传 artifact，只能接受 owner、ready、未墓碑且有 sha/size 的行（T027 的
  run_input 准入规则不变）。

## 7. 文件与提交

- `db/migrations/0015_artifact_admissions.sql`
- `internal/artifacts/{upload.go,admission.go,service.go,admission_test.go}`
- `internal/api/{artifacts.go,server.go}`
- `internal/background/{job.go,format_test.go}`、
  `internal/background/handlers/artifact_finalize.go`
- `apps/web/src/{attachments.html,assets/attachments.js,apps.html,debug.html}`
- `tests/support/{t028.py,fixtures.py}`、`tests/f05/`（6 文件 31 用例）
- `tests/f00b/test_t026_migration.py`、`tests/f02a/test_t0{23,25,27}_migration.py`（链尾适配）
- `reports/f05/T028-artifact-uploads.md`、`PROGRESS.md`、`E:\orchara-notes\f05-f03\NOTES.md`

## 8. 规格变更

无。`product/docs/spec/**` 与 `product/contracts/**` 未修改；`sync_spec --check` no drift。
发现的契约缺口（隔离原因无字段、scope 词汇不足、无 artifact admission 读模型）记录在 §4。
