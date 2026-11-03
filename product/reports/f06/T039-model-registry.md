# F06 T039 验收：管理员登记模型并查看兼容验证后才共享

- 任务：F06 / GitHub Issue #40（票 `T039`，规划 ID F06.01/F06.08），分支
  `t/t039-model-registry`（worktree `E:\Orchara-wt\t039`）；父门禁 #38/#39。
- 状态：**`passed`**（本票范围 = `registerModel` / `listModels` / `getModel` /
  `validateModel` / `listModelEndpoints` / `listCredentialRefs` + `model_validate`
  完成守卫的模型/评测对账 + 管理员 `/models` 页面 + 真实兼容验证路径）。
  真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP/mTLS + 真实封存对象字节 +
  真实 maintenance 双进程（`model_validate` + `artifact_finalize`）+ Node 执行页面逻辑；
  无 mock、无跳过。
- 边界（如实）：stub 供应商的合成结构化输出用例是真实后台任务与真实封存报告；真实厂商
  provider adapter 不在本构建内（F06 后续/真实部署），`provider=openai` 在无 endpoint 部署
  下安全失败为 `provider_not_configured`，本报告把它作为“终端失败 → 模型 failed、无报告”
  的断言路径；浏览器 DOM 驱动页面仍 `not_run`（与 T023/T027/T029/T031/T032/T033/T037
  同一边界）；全量 `pytest tests` / 全量 `tests/f00b` 门禁由 orchestrator 在合并后运行。

## 0. 工作记录（简短）

1. 读 T039 票面与 LITE 简报、`modelproxy.Candidate`/stub provider、T024
   `model_validate` handler、`complete.go` 守卫表、`evaluation.go` 报告绑定、
   `0011`/`0013` 表结构、`tools.go`/`server.go` 约定、`tests/support/t024.py`
   的真实 job/维护进程工具；确认无需迁移。
2. TDD 垂直切片（每片先红后绿）：
   - catalog 解析/默认 stub/fail closed、JCS manifest 摘要 → 单测；
   - 注册（201 pending/重复 409/未知引用 422/secret 不回显）→ 真实 HTTP；
   - 可见性（admin 全部、非 admin 仅 passed 未撤销）→ 真实 HTTP + 真实 PG；
   - 验证（`Idempotency-Key` 400/回放、真实 job、报告绑定、passed/failed）→
     真实 maintenance 双进程；
   - 可重试失败（首个 attempt 真实 model call 后以 `transient_storage_io` 失败）→
     model/evaluation 保持 running、无报告；真实进程重试后 passed、1 条报告；
   - 终端失败对账（real provider 未配置 → evaluation failed、model failed、无 report）；
   - 依赖就绪（pending 模型被草稿引用 → `dependency_not_ready`；真实通过后可再次 validate）；
   - 页面结构 + Node 执行 `models.js`。
3. 跑门禁（§3），写本报告/PROGRESS/ADR；按 feat/test/docs 提交；未推送、未关 issue、
   未改 `docs/spec/**`/`contracts/**`/迁移 0001–0024（本票无迁移）。

## 1. 交付内容

### 1.1 目录与引用 catalog（无新表）

`internal/modelregistry/catalog.go`：部署配置 JSON，严格解码（未知字段/尾随数据拒绝），
**fail closed**。两个变量都不设时是文档化 dev stub；只设一个即拒绝启动/构造。

```json
ORCHARA_MODEL_ENDPOINTS='[{"id":"stub-endpoint","data_destination":"local-stub","credential_ref":"stub-credential"}]'
ORCHARA_CREDENTIAL_REFS='[{"id":"stub-credential","description":"dev stub (no real secret)"}]'
```

- endpoint 项：`id`、`data_destination`、`credential_ref`、可选 `description`；
- credential 项：`id`、`description`、可选 `env`（部署环境变量名；只是引用名，不是密钥值，
  且**不进入任何 API 投影**）；
- id 必须是 slug（`^[a-z0-9][a-z0-9._-]{0,63}$`），杜绝 URL/路径/密钥形状；
- endpoint 的 `credential_ref` 必须已登记；重复 id 拒绝；
- dev-stub 默认：`stub-endpoint` → `local-stub` + `stub-credential`。

### 1.2 注册（`POST /api/v1/models`，admin）

- 校验 `ModelInput` 全字段：name/provider/model_id/endpoint_ref/secret_ref/config/
  data_destination；`ModelConfig` 的 kind/protocol 枚举、timeout_ms>0、context_window/
  max_output_tokens（非 embedding）/embedding_dimension（embedding）、temperature 0–2、
  fallback UUID 唯一、price currency 3 字符 + 十进制金额（正则，不用二进制浮点）。
- 引用必须是 catalog 登记项；`data_destination` 必须等于该 endpoint 登记的目的地；
  `secret_ref` 必须是该 endpoint 的 credential；provider `stub` 只允许 `local-stub`/
  `capacity-stub` 目的地。
- manifest = **服务端构建**对象的 RFC 8785 JCS 字节（format_version/name/provider/
  model_id/endpoint_ref/secret_ref/config/data_destination，无时间戳/run 内容），sha256；
  经 `objectstore` 封存，单事务写 `platform.artifacts`（purpose `model_manifest`，ready）
  + `platform.model_profile_versions`（pending）。重复 `manifest_sha256` → 409
  `model_already_registered`，不写第二行/第二个对象。
- 响应 201 `{id,state}`（契约 `Accepted`），绝不返回 `secret_ref`。

### 1.3 读取与可见性

- `GET /api/v1/models`：auth + API key `application:read`，limit 1–100、opaque keyset
  cursor（created_at,id），`{items,next_cursor}`；`application_id` 过滤只返回 passed 未撤销。
- `GET /api/v1/models/{model_version_id}`：auth + `application:read`；admin 看全部，
  其他身份只看 passed 且 `revoked_at IS NULL`；不可见与不存在同为 404。
- 投影精确六字段：`{id,name,model_id,validation_state,data_destination,
  evaluation_report_id}`。

### 1.4 验证（`POST /api/v1/models/{model_version_id}/validate`，admin）

- `Idempotency-Key` 必需（重复 header/空值 → 400 `idempotency_key_required`，与 debug 路径
  一致）；同 key 同请求回放记录的 202（`platform.idempotency_keys.response_json`），
  异请求 → 409 `idempotency_conflict`。
- 事务内 `SELECT ... FOR UPDATE` 锁模型行：
  revoked → 409 `model_revoked`；running → 返回已有运行中 evaluation；passed → 409
  `already_validated`；pending/failed → 继续。
- 固定 fixture：`{"schema":"orchara.model-compat/1","case":...,
  "check":"structured_output","model_profile_version_id":...}` 的 JCS 字节，封存为
  `purpose=model_fixture` 的 ready artifact。provider `stub` 且 model_id 以
  `-incompatible` 结尾时 case=`structured_output_invalid`（文档化 stub 模拟）。
- 同一事务：`background.EnqueueTx(kind=model_validate, target=model, owner=admin,
  input=fixture, dedup=evaluationID)` + `platform.evaluation_jobs`（kind=model、
  model_profile_version_id、dataset_artifact_id=fixture、queued）+ model running +
  idempotency 收据；返回 202 契约 `EvaluationJob` 六字段。

### 1.5 完成守卫（`internal/background`）

- 成功：`verifyJobResultArtifact` 后，严格解码封存报告（≤1 MiB），校验
  `report_version/kind/model_profile_version_id/call_id/fixture_artifact_id/
  fixture_sha256/check{name,passed,reason}/compatible/provider/stub`；
  候选 id == `job.TargetID`、fixture id == `job.InputArtifactID`、fixture sha ==
  fixture artifact sha、`platform_model_calls.id=call_id` 属于本 job + 本候选 +
  `succeeded`、`evaluation_jobs.dataset_artifact_id == job.InputArtifactID`、
  `compatible == check.passed`；同事务插 `evaluation_reports`（manifest=候选 manifest、
  dataset=fixture sha、status passed/failed）、evaluation succeeded+report_id、
  model passed/failed+evaluation_report_id。**失败判定 = 成功 job + failed 报告**。
- 终端 failed/cancelled（不可重试）：evaluation failed/cancelled、model failed、无报告；
  重试保留 model running。
- **T024 兼容分支**：直接入队（无 `evaluation_jobs` 行）的 `model_validate` job 保持原语义
  ——报告 artifact 校验通过即成功，不改模型/评测状态；失败/取消同样只对“有绑定 evaluation”
  的 job 对账（三个分支都先查 `modelValidationEvaluationBound`）。该分支让
  `tests/f00b/test_t024_*` 继续全绿。

### 1.6 HTTP 与管理页

- `POST /api/v1/models` 201、`GET /api/v1/models`、`GET /api/v1/models/{model_version_id}`、
  `POST .../validate` 202；契约 admin 读：`GET /api/v1/admin/model-endpoints` 精确
  `[{id,data_destination}]`、`GET /api/v1/admin/credential-refs` 精确
  `[{id,description}]`（不泄露 `env`）。
- `apps/web/src/models.html` + `assets/models.js`：登记（endpoint/credential 下拉来自
  catalog）、列表、验证轮询、报告卡（status/check/profile id/manifest sha/fixture/call/
  provider/stub）；pending/running/failed 明确“不能被共享应用引用”；全部 `textContent`，
  无 localStorage、无 secret。`admin.html` 导航加“模型登记”。

## 2. AC 核对

| AC | 结果 | 证据 |
|---|---|---|
| 候选登记不依赖已有应用报告 | passed | 注册只读 catalog + 写 manifest/model 行，不创建/读取 application/evaluation；`test_registration_is_pending_and_never_echoes_the_secret` |
| pending/failed 不能被共享应用引用 | passed | 非 admin 列表只见 passed 未撤销；pending 模型被草稿引用 → `dependency_not_ready`；failed 模型对非 admin 不可见；`test_pending_model_blocks_draft_validation_until_it_passes`、`test_failed_stub_case_...` |
| 模型界面显示兼容报告、数据目的地和固定 profile | passed | `/models` 页面 + 报告卡（manifest/profile/fixture/call/check）；`test_the_models_page_...`、Node `describeReport`；真实报告字节断言 |
| 凭据只用预登记引用且不回显 | passed | 未知 endpoint/credential → 422；响应/页面断言不含凭据值；catalog 仅 id+description（`env` 不投影） |
| 端到端入口实际演示；失败/授权/重试边界有断言 | passed | 真实 maintenance 双进程 happy/failed/terminal 三路径；可重试失败保持 running 后重试 passed；非 admin 403；同 `Idempotency-Key` 回放不建第二个 job；T024 重试语义回归通过 |
| 记录版本/输入/hash/命令/原始结果/profile；stub/real 分报 | passed | 本报告 §3/§4；环境 `dev-stub`；real 路径失败为 `provider_not_configured`（无 adapter，诚实失败） |

## 3. 命令与原始结果（tails）

环境：Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`，
`GOCACHE=E:\orchara-toolchains\gocache`，`GOMODCACHE=E:\orchara-toolchains\gomodcache`）；
PostgreSQL 16.4 隔离集群 `127.0.0.1:55432`；Python 3.12.2；Node 24.13.0；profile `dev-stub`；
对象目录在各测试 `tmp_path`；catalog 为 dev-stub 默认（见 §1.1）。

```text
$ go vet ./...
(exit 0, no output)

$ go test ./...
23 packages ok, 0 FAIL, 9 no test files
（含 ok orchara/platform/internal/modelregistry、ok .../modelproxy、ok .../background）

$ gofmt -l internal cmd tools
(no output)

$ python -m pytest tests/f06 -q
15 passed in 42.77s

$ python -m pytest tests/tooling -q
21 passed in 4.63s

$ python -m pytest tests/f00b/test_t024_model_validate.py tests/f00b/test_t024_links.py -q
9 passed in 33.18s

$ python -m pytest tests/f00b/test_t024_artifacts.py tests/f00b/test_t024_model_calls.py -q
14 passed in 47.48s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

测试计数：`tests/f06` **15**（models 11 + pages 4），Go 单测 `internal/modelregistry`
**7** 个顶层测试（catalog 默认/严格失败/投影、manifest 稳定与内容绑定、输入校验矩阵、
profile 无 secret）；受影响的 `tests/f00b` T024 文件 **23** 用例回归通过。

## 4. 偏离与决定

1. **ADR 编号 0007**：简报写 `docs/adr/0006-f06-f04a-boundaries.md`，但 0006 已被
   F05/F03 使用；本票用 `docs/adr/0007-f06-f04a-model-registry-boundaries.md`，内容为
   简报要求的 D1–D3 + catalog shape。
2. **T024 兼容分支**：完成守卫只有在 `evaluation_jobs` 存在本 job 绑定行时才解析/绑定报告并
   改模型状态；直接入队的 T024 job 保持 report-only。成功/失败/取消三个分支一致。
3. **catalog credential `env`**：可选部署环境变量名，用于真实 adapter 在 Go 内解析密钥；
   它不进 API 投影、不进报告/日志；真实 adapter 本构建未实现，故该字段只由 catalog 单测
   覆盖，e2e 使用 dev-stub。
4. **validate 认证/幂等**：header 规则与错误码对齐 debug 路径（缺失/重复 400
   `idempotency_key_required`、异请求 409 `idempotency_conflict`）。
5. **modelproxy 引用双形态**：注册存 catalog id；`Candidate` 同时接受旧 `env:`/`file:`
   部署引用与 catalog id，防止 T024/维护路径被 T039 破坏。
6. **无迁移**：`0011` 的 `model_profile_versions`/`model_manifest` purpose 与 `0013` 的
   `evaluation_reports`/`evaluation_jobs` 已足够；0011–0024 未改。
7. **`listModels` 的 `application_id`**：它是契约声明的 query 参数，但契约没有规定其选择
   语义；T039 把它实现为“该应用可选择的模型 = passed 且未撤销”（即使调用者是 admin 也
   不收窄为全量），供 T040/T041 的模型选择器使用。本票 e2e 不依赖该参数（只由代码路径
   覆盖），若后续票需要不同语义应显式改接口而不是默认沿用。
8. **重试边界**：可重试失败用例通过 internal result API 注入
   `transient_storage_io`（与 T024 的重试测试同一手段：真实 handler 已完成真实 model call），
   随后由真实 maintenance 新进程重试；这不是 mock 成功，报告与账本仍是真实封存字节。

## 5. not_run / 未覆盖（如实）

- `not_run`：真实厂商 provider adapter 与真实模型兼容判定（本构建 real 路径安全失败为
  `provider_not_configured`，已作为终端失败断言；stub 用例不冒充真实模型）。
- `not_run`：浏览器 DOM/真实浏览器驱动 `/models` 页（与既有票同一边界；页面逻辑由 Node
  在 `vm` 中执行，页面 HTML 由真实 HTTP 服务返回）。
- `not_run`：全量 `pytest tests` 与全量 `tests/f00b`（orchestrator 合并后运行）。本票期间
  一次全量 f00b 运行因 Windows 子进程管道悬置被中止；受 modelproxy/守卫影响的 T024 文件
  已单独回归通过（§3），首次运行暴露的 3 个失败是守卫回归，修复后 9+14 用例全绿。
- `not_run`：多 endpoint/多 credential 的 e2e 组合（只由 catalog 单测覆盖；e2e 使用
  dev-stub 默认单条）。
- `not_run`：`application_id` 选择器的独立 e2e（契约未规定语义，见 §4.7；T040/T041 接入时
  再补用户路径断言）。
- 未执行：合并到 `feat/f06-f04a-models-and-workflow`、推送、关闭 issue（按约束留给
  orchestrator）。

## 6. 给 T040/T041 的接口

- Go：`modelregistry.New(db, store, catalog)`；`Service.Register/List/Get/ValidateInput/
  BeginValidation/Manifest`；错误类型 `*ValidationError{Field}`、`*ConflictError{Code}`、
  `ErrNotFound`/`ErrInvalidCursor`/`ErrIdempotencyKeyReuse`。
- 目录/引用：`modelregistry.Catalog` 的 `Endpoint(s)`/`Credential(s)`/
  `HasEndpoint`/`HasCredential`/`CredentialEnv`；`modelproxy.ReferenceCatalog` 接口
  （`HasEndpoint`/`HasCredential`）由 `modelproxy.Service.Catalog` 消费。
- HTTP：`POST /api/v1/models`、`GET /api/v1/models{?cursor,limit,application_id}`、
  `GET /api/v1/models/{model_version_id}`、`POST .../validate`（202 `EvaluationJob`）；
  admin `GET /api/v1/admin/model-endpoints`、`GET /api/v1/admin/credential-refs`。
- 数据：`model_profile_versions.validation_state ∈ pending/running/passed/failed` +
  `evaluation_report_id`；`evaluation_jobs(kind=model, model_profile_version_id,
  dataset_artifact_id=fixture, background_job_id)`；`evaluation_reports.status ∈
  passed/failed`。任何新的 `model_validate` 生产者若希望模型状态被对账，必须在同一事务
  写 evaluation 行；否则走 report-only 兼容路径。
- 报告文档：`background.ModelCompatibilityReport`（严格字段集）；共享应用选择模型前应只
  接受 `validation_state=passed AND revoked_at IS NULL`（现有 `catalog.modelReadinessProblem`）。
