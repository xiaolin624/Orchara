# F11 T073 验收：用户限范围授权诊断且每次正文读取都有审计

- 任务：F11 / GitHub Issue #74（票 `T073`，规划 ID F11.03/F11.04）；分支
  `t/t073-diagnostic-grants`（worktree `E:\orchara-worktrees\t073`）；父批次
  `integration/t068-t082`；基线 `57d90df`（并入 T068 后集成 tip `0fcf8ec`）；
  合并提交 **`a1ffd60`**（`git merge integration/t068-t082`，无冲突）。
- 状态：**`passed`**（六条验收全部由已执行的真实栈测试证明；仅“OTel 默认无正文”
  这条观测行标 **`partial`**：仓库没有任何 tracer/metrics SDK，不能伪造 tracer，
  结构性保证见 ADR 0028 §6）。
- 边界（如实）：本票交付授权表/对象快照/用途/期限、契约 `grantDiagnostics` 与
  `revokeDiagnostics`、非契约元数据列表与受审计正文门、管理员默认只见元数据、
  每次读取的 typed 审计与 operation/attempt 关联、日志/错误/审计脱敏、页面授权管理。
  **物理清理**属性 **T071**，checkpoint/摘要/临时沙箱/第三方/备份账本属 **T072**，
  无复活全套属 **T076**；本票未实现 OTel/metrics，也未新增依赖。
- 真实依赖：PostgreSQL **16.4**（`127.0.0.1:55432`，真实 16.4 用户态集群）、
  pytest fixture 现场编译的 `cmd/api`/`cmd/admin`/`cmd/maintenance`/`cmd/supervisor`、
  真实 HTTP + cookie 会话 + CSRF、真实 mTLS 内部监听、真实 sealed 对象字节（真实
  worker 上传 + `artifact_finalize` 落盘）、真实 `platform.*` 行回读、Node
  **v24.13.0** 执行页面纯函数。无 mock、无 skip。Go **1.27.3**。

## 0. 工作记录（简短）

1. 读 `BRIEF.md`、`issues-69-83.md` #74、`notes/f11-deletion.md` §3.8/§3.9/§4/§6/§8、
   `SECURITY.md` §5、`DATA-POLICY.md`、`OPERATIONS.md:57`、`API.md:12`、
   `contracts/openapi.json` 的 `grantDiagnostics`/`revokeDiagnostics` 请求/响应形状、
   `contracts/database.sql:335` 的 `diagnostic_grants`、现有
   `internal/{audit,api,deletion,execution,policy,artifacts}/` 与 `tests/support/` 的
   T024/T028/T033/T041/T069 真实栈接缝。
2. 结论：`platform.diagnostic_grants` 在迁移链中不存在；契约只有建/撤两个操作，没有
   对象范围、没有正文读取面、没有 trace/metrics。正文在 sealed artifacts，owner-only
   读路径不能放宽；operator 需要单独的门 + 单独的审计。
3. TDD 垂直切片（红→绿，红阶段原始失败均已记录）：
   - 授权生命周期（POST/list/DELETE、owner-only、grantee 必须是另一位启用的 admin、
     422/403/401、CSRF、API key `personal_scope_forbidden`、撤销幂等）：红 =
     `404 page not found`（路由不存在）→ 实现迁移 + `internal/diagnostics` grant store +
     `internal/api/diagnostics.go` 后 3 passed。
   - 正文门（受权读取、对象快照、越范围/错用途/过期/撤销/运行 tombstone 的 distinct
     code、管理员无授权 metadata-only、真实 operation/attempt 关联）：红 =
     `404 page not found`（content 路由不存在）→ 实现 `audit.RecordContentRead` +
     `diagnostics.ReadContent` 后 7 passed；期间修正两处测试侧假设（非 admin 是
     `403 forbidden` 而非 404；`runs` 没有 `deleted` 状态值，只置 `deleted_at`）。
   - 脱敏/失败/审计非备份（marker 正控制 + 全审计/全日志扫描、删字节后 404
     `diagnostic_content_unavailable`、422 不回显值、tombstone + 模拟 T071 清字节后
     audit 不能重建正文）：3 passed。
   - 迁移（链连续/重复 migrate no-op、列/约束/FK/索引/注释、worker/PUBLIC 零权限、
     无 training/consent 列）：4 passed。
   - 页面（served HTML 结构 + Node 执行 12 个纯函数）：3 passed（页面实现与测试同批
     完成，首次执行即绿；其余四个切片均有真实红阶段）。
4. 迁移编号：任务提示预留 `0049`，但本 worktree 的迁移尾是 `0044`，BRIEF §5 与
   `stack.migration_chain` 要求**无空洞**，因此使用本 worktree 下一个空位
   **`0045_diagnostic_grants.sql`**（按 `_diagnostic_grants.sql` 名字断言，合并时由
   merger 重编号）；ADR 使用预留的 **0028**（0025–0027 属并行票）。
5. 未 push、未关 issue、未改 `docs/spec/**`/`contracts/**`；合并后工作树干净。

## 1. 交付内容

### 1.1 迁移 `0045_diagnostic_grants.sql`

- 契约表 `platform.diagnostic_grants(id, owner_id → users(principal_id),
  grantee_id → principals, run_id → runs, purposes text[], expires_at, revoked_at)`
  **原样**建立；加法：`object_scope jsonb NOT NULL DEFAULT '[]'`（
  `jsonb_typeof='array'`）与 `created_at timestamptz NOT NULL DEFAULT now()`。
- 数据库事实：`cardinality(purposes) > 0`、`expires_at > created_at`、
  `revoked_at IS NULL OR revoked_at >= created_at`；索引
  `diagnostic_grants_live(grantee_id, run_id) WHERE revoked_at IS NULL` 与
  `diagnostic_grants_owner(owner_id, created_at DESC)`。每个列/表都有归属说明注释。
- 无 training/consent 列、触发器或外键；表只承载诊断授权。重复 `admin migrate` no-op，
  新库 0001..0045 连续；`orchara_worker`/PUBLIC 对 `platform` 零权限。

### 1.2 `internal/audit.RecordContentRead`：唯一 typed 审计写入器

- 位置参数结构体 `ContentRead`（不是 `map[string]any`），自带校验：UUID 形状、
  purpose/type/error-code token 形状、outcome 闭集、`served` 禁止 error_code、
  `request_id` 只允许短 ASCII。任何一项不合法 → 拒绝写审计（fail closed），调用方不得
  在读之前放行。
- 元数据闭集恰好十个键：`grant_id, purpose, run_id, resource_type, resource_id,
  operation_id, attempt_token, request_id, outcome, error_code`；缺失关联写 JSON null，
  保证 schema 稳定。无 body/query/path/credential 字段可表达。
- `internal/audit/contentread_test.go` 用 13 条反例（把 marker 塞进每个字段、served
  带 error code、refused 无 code、未知 outcome 等）证明 fail-closed，并断言 metadata
  键集合/值类型/长度上界。

### 1.3 `internal/diagnostics`：授权 + 受审计正文门

- `Create(ownerID, params)`：一个事务内 `FOR SHARE` 读 run（owner-only，未知/他人 404，
  tombstone 404 `resource_not_found`）；grantee 必须是**另一位**启用 admin（缺失 404
  `principal_not_found`，禁用/非 admin/自授权 422 `validation_failed` + field
  `grantee_id`）；purposes 词表 `[a-z][a-z0-9_-]{0,63}`、≤8、去重；`expires_at` 必须
  是未来 RFC3339；对象快照：`X-Diagnostic-Object-Scope: artifact:<uuid>,…` 显式给出时
  逐个必须是本 run 的未删除 artifact，缺省时**此刻**从 run_artifacts/
  run_artifact_uploads 派生一次并排序固化（之后新增对象不在范围内）；插入 grant + 一条
  `diagnostic.grant_created` 审计（ids/purposes/expiry/object_count），提交后才 201。
- `Revoke(ownerID, grantID)`：owner-only，`FOR UPDATE` 后 `revoked_at` 只写一次；重复
  DELETE 幂等 204 且不重复审计；写 `diagnostic.grant_revoked`。
- `List(callerID)`：owner 或 grantee 的元数据列表（`direction=owner|grantee`），
  `state` 由 DB 时间派生 `active|expired|revoked`，无正文。
- `ReadContent(callerID, request, requestID)`：`grant ∧ NOT deleted` 读谓词，顺序
  `revoked → expired(DB now()) → run.deleted_at → purpose → object_scope → run 成员 +
  artifact.state/deleted_at → 打开 sealed 对象`。`artifact` 仍须属于该 run 且
  `ready`；只有租约 attempt token 恰好对应**一个** `operation_attempts` 行时才记录
  `operation_id`（否则只记录真实 attempt token，不随便挑一个 operation）。
  **审计先提交、后出字节**：`served` 审计写失败则关闭 reader 并 500；`refused` 也写
  `outcome=refused` + 自己的 error_code；对象/存储失败写 `outcome=failed` +
  `diagnostic_content_missing`/`diagnostic_storage_io`；未知/非 grantee 的 404 无可归属，
  不写审计也不泄露 grant 是否存在。
- distinct 拒绝码：`diagnostic_grant_revoked` / `diagnostic_grant_expired` /
  `diagnostic_run_deleted` / `diagnostic_purpose_denied` / `diagnostic_scope_denied` /
  `diagnostic_content_unavailable` / `diagnostic_content_not_ready`；422
  `validation_failed`（field only）、403 `forbidden`（非 admin 的凭据形状检查）、
  404 `resource_not_found`（未知/非 grantee/畸形 id）。

### 1.4 HTTP 面与日志脱敏

- 契约：`POST /api/v1/diagnostic-grants` → 201 `Accepted {id,state:"granted"}`；
  `DELETE /api/v1/diagnostic-grants/{grant_id}` → 204。二者仅 cookie 会话 + CSRF
  （API key → 403 `personal_scope_forbidden`，ADR 0009 §1 先例）。
- 文档化非契约（ADR 0028）：`GET /api/v1/diagnostic-grants` 元数据列表；
  `GET /api/v1/diagnostic-grants/{grant_id}/content?object=artifact:<uuid>&purpose=<token>`
  受审计的正文流（admin+grantee only，`Content-Disposition: attachment`，
  `X-Diagnostic-Grant` 回显 grant id；不使用会活过撤销的签名 URL）。
- 请求日志仍是一行 `METHOD path(无 query) status duration request_id`：object/purpose
  查询值不落日志；错误体是契约 envelope，422 只带 `field`，不回显值；正文流错误日志只
  带 request_id + 传输错误。没有 tracer/metrics 依赖。

### 1.5 页面

`apps/web/src/personal-data.html` 新增“诊断授权”区（运行 id + 列出可授权对象 +
多选、管理员 principal id、用途、到期时间、创建/列表/撤销），
`assets/personal-data.js` 新增纯函数：`diagnosticGrantPath`、`diagnosticObjectEntry`、
`diagnosticObjectScopeHeader`、`diagnosticPurposes`、`diagnosticExpiryISO`、
`diagnosticGrantStateText`、`diagnosticReadRefusalText`、`diagnosticGrantCreateRequest`、
`diagnosticGrantRevokeRequest`、`describeDiagnosticGrant`、`diagnosticGrantSummaryText`。
页面只用 `textContent`，无 `innerHTML`/`localStorage`，不读取正文；诊断授权与训练同意
在页面与文案上明确分开。

### 1.6 测试

新增 `tests/support/t073.py` 与 5 个文件共 **20** 个用例（f11 由 22 → 42）：

- `test_t073_grants.py`（3）：授权范围/审计/双方元数据、owner-only + 另一位 admin +
  422/403/401/API-key、撤销立即/幂等/owner-only。
- `test_t073_content.py`（7）：受权读取真实字节 + 完整审计（run/attempt/operation/
  request_id）、对象快照与缺省派生一次、越范围/错用途 distinct 码、过期（真实 6 s 窗口
  + 1.5 s 余量）、撤销、运行 tombstone、管理员无授权 metadata-only（真实
  artifact/run/node-results/background-job 端点）。
- `test_t073_redaction.py`（3）：正控制 marker 后全审计/run_events/API 日志/maintenance
  日志无 marker、删字节失败读的错误/日志/审计无 marker 且无 cookie/密码/header
  credential、撤销 + tombstone + 模拟 T071 清字节后审计只有有界 typed 元数据。
- `test_t073_migration.py`（4）：链连续 + 重复 migrate、列/约束/FK/索引/注释、
  worker 零权限、无 training/consent 列。
- `test_t073_pages.py`（3）：served HTML、served JS helper、Node 执行请求形状
  （含 `X-Diagnostic-Object-Scope` 只在选择对象时发送）/用途/到期/投影/拒绝文案。

## 2. AC 逐条

| # | 验收 | 结果与证据 |
|---|---|---|
| 1 | 超范围/过期/撤销读取拒绝（distinct code） | **passed**：`test_out_of_scope_and_wrong_purpose_reads_are_refused_with_distinct_codes`（`diagnostic_scope_denied`、`diagnostic_purpose_denied`）、`test_an_expired_grant_is_refused_after_its_deadline`（真实 6 s 窗口，过期后 403 `diagnostic_grant_expired`，1.5 s 余量）、`test_a_revoked_grant_is_refused_immediately_and_forever`（403 `diagnostic_grant_revoked`，连续两次）；运行 tombstone 见 `test_a_tombstoned_run_ends_the_grant_and_the_artifact_read_refuses`（403 `diagnostic_run_deleted`） |
| 2 | 管理员默认只读元数据（真实端点） | **passed**：`test_an_admin_without_a_grant_sees_metadata_only_on_the_real_surfaces`：admin 对 owner 的 artifact GET/download/content、run、run artifacts、node-results 全部 404；background-job 200 且字段恰为 `{id,kind,state,error_code}`；无授权时 grantee 列表为空、猜 grant id 404；授权后列表只含元数据（object ids/purposes/expiry），正文明文不出现在任何 body |
| 3 | 授权不复制正文进长寿日志 | **passed**：`test_a_granted_read_never_copies_content_into_audit_or_logs`：唯一 marker 在真实 sealed 内容中且被 200 正控制读出；随后 `platform.audit_events` 全表文本、`run_events`、真实 `cmd/api` 日志、真实 maintenance finalize 进程 stdout/stderr 均无 marker；content-read 审计恰为十字段白名单 |
| 4 | 错误/追踪/审计不带请求或模型正文（追踪 `partial`） | **passed（错误/审计）+ partial（trace）**：`test_a_failed_granted_read_carries_no_content_or_credential`：删 sealed 字节后 404 `diagnostic_content_unavailable`，错误体/日志/审计无 marker，无 session cookie、admin 密码、请求头 credential；422 只回 `{"field":"object"}` 且查询值不落日志（请求日志无 query）。**trace 行 partial**：仓库无 OTel/tracer/metrics SDK（`grep` 零命中），没有可观测的 trace 可测；按任务要求不引入依赖、不伪造 tracer，只交付结构性保证（审计白名单、无 query 日志、sentinel 扫描）+ ADR 0028 §6；后续接入 exporter 时必须沿用同一白名单 |
| 5 | 能关联 operation/attempt | **passed**：`test_a_granted_read_serves_the_real_bytes_and_audits_run_attempt_operation`：真实 tool capability operation 的 `operation_attempts.attempt_token` 与真实 worker 上传的 run artifact 关联；审计 `run_id`、`attempt_token` 等于 lease token、`operation_id` 等于真实 operation id、`request_id` 等于响应 `X-Request-ID`，`actor_id` 是 grantee；第二次读追加第二行 |
| 6 | 必要审计不充当正文备份 | **passed**：`test_audit_is_not_a_content_backup_after_revocation_and_purge`：撤销 + T069 tombstone（202）+ 模拟 T071 移除 sealed 字节后，active grant 的读取也 404；内容读审计恰好十字段、值为 UUID/token/null、长度 ≤128、无换行，marker 不在任何审计行/API 日志中，audit 不能重建正文 |
| 7 | 端到端入口实际演示；失败、当前授权与重试/恢复边界有断言 | **passed**：全部用例走编译 `cmd/api` + 真实 HTTP/CSRF；失败 = 缺失密封字节（failed 审计）+ 对象/运行过期拒绝；当前授权 = `grant ∧ NOT revoked ∧ NOT expired ∧ NOT run.deleted ∧ purpose ∈ ∧ object ∈ ∧ ready`；重试 = 撤销重复 DELETE 幂等 204、读取重复拒绝；恢复/复活路径不在本票（T076） |
| 8 | 记录版本/输入/hash/命令/原始结果/环境；stub/real 分报 | **passed**：见 §3 的原始命令与 §4 的版本/环境；本票不使用 stub（tool capability 路径无模型 provider，属真实受控能力；诊断读取的是真实 sealed 字节）；未运行项见 §4 |

## 3. 门禁与原始输出（合并 `a1ffd60` 后）

环境：Windows PowerShell，`$env:PATH=E:\orchara-toolchains\go\bin;…`，
`GOCACHE=E:\orchara-toolchains\gocache`，`GOMODCACHE=E:\orchara-toolchains\gomodcache`；
PostgreSQL 16.4 `127.0.0.1:55432`；Node v24.13.0；Go 1.27.3。所有 pytest 使用独立
`--basetemp=E:\orchara-tmp\t073-NN`。

```text
python -m pytest tests/f11 -q --basetemp=E:\orchara-tmp\t073-16
42 passed in 418.85s (0:06:58)

python -m pytest tests/f11 -q --basetemp=E:\orchara-tmp\t073-17
42 passed in 470.10s (0:07:50)

python -m pytest tests/tooling -q --basetemp=E:\orchara-tmp\t073-18
21 passed in 6.29s

go build ./...                    -> exit 0
go vet ./...                      -> exit 0
go test ./...                     -> exit 0 (ok=35, no test files=14, FAIL=0)
gofmt -l .                        -> (empty; "gofmt clean")
python tools/sync_spec.py --check -> "no drift: spec copy and contract snapshot match the manifest" (exit 0)
go test ./internal/audit ./internal/diagnostics -> ok (unit: content-read fail-closed matrix + grant validation matrix)
```

红阶段原始失败（TDD 证据，保留在会话输出中）：
`POST /api/v1/diagnostic-grants` 首次 `404 page not found`；content 首次
`404 page not found`（404 == 200 断言失败）。

## 4. not_run / partial / blocked

- **partial**：OTel/trace “默认无正文”。原因：全仓无 OpenTelemetry/tracer/span/metrics
  依赖与实现，无法运行一个“trace”来断言其内容；不添加依赖、不伪造 tracer。结构性
  替代：typed 审计白名单 + 请求日志不含 query/body/header + 真实日志/审计 sentinel 扫描；
  交付边界记录在 ADR 0028 §6。metrics 同理：当前没有任何 metrics 标签，故
  “避免高基数 user/run 标签”由“没有标签”满足；接入时必须沿用。
- **not_run**：物理清理/对象/索引/缓存 purge（**T071**）——本票用“删除 sealed 字节”
  模拟 T071 的 object_store 终态并在报告中如实标注；checkpoint/摘要/临时沙箱/第三方/
  备份删除账本（**T072**）；无复活全套（**T076**）；浏览器 DOM（无浏览器驱动，页面只做
  served HTML + Node 纯函数）；全量 `pytest tests`（BRIEF 禁止，>60min）；
  `tests/f01`（未改 authz，不适用）；合并带入的 `tests/f02b` 未重跑（T073 未触碰其代码，
  merge 无冲突，f11/tooling/go 全绿）；真实模型/OTel 观测栈不存在，不适用。

## 5. 给 T076（无复活回归）的接口

- 表：`platform.diagnostic_grants`（契约列 + 非契约 `object_scope jsonb`、`created_at`）；
  读谓词必须是 `grant ∧ revoked_at IS NULL ∧ expires_at > now() ∧ run.deleted_at IS NULL
  ∧ artifact.deleted_at IS NULL`。**不能**把 grant 当成复活路径。
- 审计：唯一写入器 `audit.RecordContentRead`（`internal/audit/contentread.go`），action
  `diagnostic.content_read`，十字段白名单；`outcome=served|refused|failed` +
  `error_code`。T076 的新读取面必须复用它，不得新写一份 metadata。
- 拒绝码（稳定词表）：`diagnostic_grant_revoked`、`diagnostic_grant_expired`、
  `diagnostic_run_deleted`、`diagnostic_purpose_denied`、`diagnostic_scope_denied`、
  `diagnostic_content_unavailable`、`diagnostic_content_not_ready`。
- 授权动作审计：`diagnostic.grant_created`、`diagnostic.grant_revoked`。
- 非契约面：`GET /api/v1/diagnostic-grants`（owner/grantee 元数据）、
  `GET /api/v1/diagnostic-grants/{grant_id}/content?object=artifact:<uuid>&purpose=<token>`
  （admin+grantee，审计先于字节）。对象词表扩展点：`artifact:<uuid>`（`ParseObject`）。
- T071 清理时必须保留 `platform.audit_events`（审计是证据不是备份）；purge 后同一授权读
  必须 404 `diagnostic_content_unavailable`。

## 6. ADR / 提交

- ADR：`docs/adr/0028-diagnostic-grants.md`（契约禁改下的对象范围/列表/正文门三项非契约
  扩展、typed 审计、默认元数据、日志/错误/trace 边界、无 training 捆绑、编号说明）。
- 提交：`90fba13 test(f11): T073 scoped diagnostic grant acceptance suite`；
  `a84a2db feat(f11): T073 scoped diagnostic grants and audited content door`；
  merge `a1ffd60`；docs commit 见本条之后的 `docs(f11)`。
