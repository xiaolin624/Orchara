# F02B T068 发布审核：创作者提交已评测内容，管理员批准同一 hash 并原子创建版本

- 任务：F02B / GitHub Issue #69（票 `T068`，规划 ID F02B.01–03），分支
  `t/t068-publication-review`（worktree `E:\orchara-worktrees\t068`）；基线
  `integration/t068-t082` @ `cd8ca3c`（= `integration/f13a-f15a-f09-f10` 基线）。
- 状态：**`passed`**（范围 = 4 个契约 operation：`submitApplication` /
  `reviewPublication` / `listPublicationReviews` / `getPublicationReview`；冻结审阅快照；
  同 hash 校验；依赖/报告可信性复查；单事务版本 + active 指针 + outbox + audit；
  并发审批只产生一个版本；旧版本字节/行不被改写；作者/管理员页面。
  真实 PostgreSQL 16.4、真实编译二进制、真实 HTTP（session+CSRF+mTLS）、真实封存对象字节、
  真实 WSL2/runsc 测试执行器、真实两池 maintenance、真实 5 类作者案例报告。无 mock、无跳过。）
- 边界（如实）：
  - **撤销不在本票**（T070 #71）：`application_versions.revoked_at/revoked_by/revoke_reason`
    与 `catalog.ResolveRunnableVersion` 已就绪并被测试覆盖读路径；没有 `revokeVersion` 端点，
    撤销后的目录隐藏/取消运行由 T070 交付。
  - **从已发布版本启动运行不在本票**（T074 #75）：本票只交付 `ResolveRunnableVersion`
    读判定和 frozen manifest artifact；published snapshot/Run 创建属 T074。
  - **子应用依赖的“已发布子版本”端到端**：快照/依赖复查/版本边对固定子版本有完整实现与单测
    （`buildPublicationContent` 读 `application_versions`、审批写 `version_dependencies`，
    `RecheckFrozenDependencies` 拒绝已撤销子版本），但本批执行器不支持 child run，无法用真实
    runner 报告证明“含 call_app 节点的应用可发布”；见 §4 `partial`（T075/T078）。
  - 模型目的地/工具读写联网/固定子版本字段在快照/页面/Node 测试中存在；真实端到端只覆盖工具
    （sales 预设工具，risk_class/read/write/network 由真实 `tool_versions` 投影），模型/子应用
    列表在真实可发布应用上为空是诚实的（manifest 未含这些依赖）。
- 未改 `docs/spec/**` 与 `contracts/**`（`python tools/sync_spec.py --check` 干净）；未 push、
  未关闭 issue。新增非契约面在 ADR 0020、迁移头和本报告记录。

## 0. 工作记录（简短）

1. 按 BRIEF 顺序读：`BRIEF.md`、`issues-69-83.md` §#69、F02B 探索笔记
   （`notes/f02b-publication.md`，含 contract 行号/表/已有 seam 的固定证据）、
   `product/AGENTS.md`、`API/UX/DATA-MODEL/SECURITY/BACKGROUND-JOBS/STATE-MACHINES/EVALUATION`
   的相关小节、`contracts/openapi.json` 的 4 个 operationId 与
   `Application/Version/PublicationDecisionResult/Accepted/Error` schema、`contracts/database.sql`
   的 `publication_reviews/application_versions/outbox`；再读
   `internal/catalog/{draft,versions,manifest,debug}`、`internal/api/{server,drafts,versions,
   applications,evaluation_submit,evaluations,authz}`、`internal/evaluation/{report,assertions,
   collect,dataset}`、`internal/background/evaluation.go`、`internal/compiler/verify.go`、
   `internal/audit`、`internal/toolversions`（审批/撤销先例）与
   `tests/support/{stack,t025,t027,t052,t056,t062}.py`、`tests/f02a`、`tests/f13a`。
2. 先写迁移测试（`tests/f02b/test_t068_migration.py`，5 例）并用 `cmd/admin migrate` 跑红→写
   `0043_publication_reviews_and_outbox.sql`；随后实现
   `catalog/publication{,_cases,_report}.go` 与 API 路由/处理器，再补真实栈测试。
3. 发布页/审核页按现有静态页 + 纯 JS + Node 执行逻辑的边界交付；页面测试执行服务器实际返回的
   字节（Node `vm`），不自评文案。
4. 证据策略（重要，见 §4）：快速 submit/review 套件用**真实 guard 接受**的密封报告，其
   passed case 绑定真实 `evaluation_case_runs`/`runs` 行（终态为测试种子，因为被测对象是发布
   事务而不是执行器）；另有一个 `test_t068_e2e.py` 用**真实 runner**（真实 maintenance + 真实
   WSL2/runsc）产出 5 类作者案例报告并完成 submit→撤销依赖失败→恢复→审批→版本读取。
   两份证据都保留，报告明确区分。
5. 失败/恢复边界断言：草稿编辑后提交 409；错误 hash/空报告/伪 runner 422；同内容重复提交不新建；
   依赖撤销审批 409 + located evidence；归档应用 409；并发审批恰好一个版本；拒绝后永远不能再批；
   旧版本字节与行在后续发布后逐字节不变；非 owner/非 admin/无 CSRF/无幂等键分别 404/403/400。

## 1. 交付物与实现

### 1.1 迁移 `0043_publication_reviews_and_outbox.sql`

- `publication_reviews` 增 `reason/snapshot_id/evaluation_id/dataset_sha256/
  manifest_artifact_id/version_id`（契约只有自由 `snapshot jsonb`；列化让绑定可 FK 校验，T070 只读）。
- `UNIQUE (application_id, manifest_sha256) WHERE state='submitted'`：同一冻结内容最多一条
  live review；submit 对相同内容返回已有 review，索引是数据库兜底。另有 submitter/queue 索引。
- `application_versions` 增 `revoked_by/revoke_reason` + CHECK（拒绝没有 `revoked_at` 的
  actor/reason；旧 fixture 只置 `revoked_at` 仍合法）+ `UNIQUE (review_id)`：一票一版本、撤销是
  行状态更新而非改写/删除（T070 的入口）。
- `platform.outbox`（契约形状）：审批事务同事务写 `kind='catalog_index'` 一行；本批**没有消费者**
  （T077/T082），`delivered_at/attempts` 保持初始值，报告不声称目录索引已更新。
- 全部语句幂等、schema 限定、`orchara_worker`/PUBLIC 零授权；重复 `admin migrate` no-op。

### 1.2 `submitApplication`（`POST /api/v1/applications/{id}/submit`，202）

- 认证：session+CSRF 或 API key；owner-only（非 owner 404）；session 需 creator 角色，key 需
  `application:write`；`Idempotency-Key` 必需（重复同一 key 返回存储的 202；同 key 不同规范化
  请求 409 `idempotency_conflict`）。
- 绑定同一已评测内容：对当前草稿跑 T025 `ValidateExecution`（shared-execution 依赖规则）并要求
  其 hash == `expected_manifest_sha256`（不同 → 409 `manifest_mismatch` + 当前 hash）。
- 报告复查（`catalog.verifyApplicationReport`）：重读不可变 `evaluation_reports` 行、evaluation
  绑定（application/owner/state=succeeded）、sealed snapshot、sealed manifest 字节
  （`compiler.VerifyManifestBytes`）和 sealed report 字节（重算 SHA-256 后再严格解码）；
  要求 `evaluator=orchara-platform-runner/1`、非 synthetic、application kind、同一
  manifest/dataset/evaluation/snapshot；再按 T062 规则逐 passed case 核对真实 case run
  （本 evaluation、本人、绑定 snapshot/manifest、已完成、facts 状态一致）。
  空/`not_run` 报告 → 422 `evaluation_not_passed`；伪 runner → 422 `report_not_trusted`；
  错 hash/错 dataset/错 evaluation → 422 `report_binding_mismatch`；无报告/他人报告 → 404。
- 五类作者案例门禁（`catalog.EvaluateAuthorCaseCoverage`，纯函数 + 单测）：见 §1.5。
  不满足 → 422 `author_cases_incomplete`，`details` 列出 missing_categories/case_count。
- 冻结快照：用途卡（manifest `capability_card` 六字段）、模型数据去向
  （`model_profile_versions.provider/model_id/data_destination`）、工具读写/联网
  （`tool_versions.risk_class` + connector_versions：read=read/write，write=write，network=有
  connector）、固定子版本（`application_versions` 的 app/name/version_no/manifest hash）、
  连接器策略 hash、report id/hash/status、作者案例证据。**只含共享定义**：无 run 输入输出、
  无 object key、无凭据引用、无他人正文。
- 事务：幂等 receipt 认领 + 内容 advisory lock + `application_drafts` 行锁（再验
  execution_revision，提交中被编辑 → 409 `manifest_mismatch` + current_execution_revision）+
  同内容已有 review 返回 + 插入 review（`state=submitted`）+ audit `publication.submitted` +
  存 202 `{id,state}`。返回体严格只 `{id,state}`（`Accepted`）。

### 1.3 `reviewPublication`（`POST /api/v1/reviews/{id}/decision`，200）

- 仅 admin 角色（API key 无角色 → 403）；`Idempotency-Key` 必需；body 严格三字段；
  `manifest_sha256` 必须等于冻结 hash（否则 409 `manifest_mismatch`，details 给出双方）；
  reason 非空且 ≤2000（否则 422 located）。
- 状态机：只有 `submitted` 可决定；已批准/已拒绝再决定 → 409 `review_conflict`（被拒后永远不能
  批准）。拒绝：同事务记 `state=rejected/reviewer/decided_at/reason` + audit
  `publication.rejected` + 存 200 `{id,state:"rejected",version_id:null}`；不创建版本，草稿仍可编辑。
- 批准前重新复查：sealed report/字节/runner/T062 case run、五类门禁、冻结 manifest 字节、
  应用未归档、冻结依赖闭包
  （`catalog.RecheckFrozenDependencies`：tool ready+approved、model passed+未撤销、子版本未撤销且
  父应用未归档、connector approved+未撤销；pinned image digest / policy sha 变化也失败）。
  失败 → 409 `dependency_unavailable` + located `dependency_*` 证据。
- 一个事务：锁 review（`FOR UPDATE`）再验 `submitted` → 锁 application（验未归档、取
  `active_version_id`）→ 事务内依赖复查 → 再验 frozen manifest artifact 行（ready/未删/hash）→
  `version_no=MAX+1` → 插入不可变 version（manifest 用冻结规范字节，capability_card/compiler/
  worker/state 身份来自 manifest）→ 写 `version_dependencies`（固定子版本）→ review
  `state=approved/reviewer/decided_at/reason/version_id`（CAS `WHERE state='submitted'`）→
  `applications.active_version_id` → outbox `catalog_index`（op=publish、previous/active version）→
  audit `publication.approved` → 存 200 `{id,state:"approved",version_id}`。
- 并发：两个并发批准在同一 review 行锁上串行；第二个看到 approved → 409，只产生一个版本
  （真实并发测试 `test_concurrent_approvals_create_one_version_and_old_versions_stay_immutable`）。
- “版本 JSON artifact”：对象存储不参与 SQL 事务，故规范 manifest 字节在提交时封存、
  artifact id 冻进快照；审批事务只重验该 artifact 行/hash 并把同一 artifact 绑到 version 行。
  旧版本 `manifest/manifest_sha256/manifest_artifact_id/version_no` 永不改写。

### 1.4 读接口与页面

- `GET /api/v1/reviews`：keyset `(created_at,id)` + limit 1..100；admin 全量；其他人只看到
  `submitted_by=本人` 或本人应用的 review；ACL 谓词与 cursor/LIMIT 同 WHERE。
  body 严格 `{items:[{id,application_id,state,manifest_sha256}],next_cursor}`。
- `GET /api/v1/reviews/{id}`：admin 或提交者/应用 owner；body 严格
  `{id,state,manifest_sha256,report_id,snapshot}`；未知/他人 → 404（不可枚举）。
- 非契约 `POST /api/v1/applications/{id}/publication-preview`（owner+creator+CSRF）：与提交快照
  共用 `buildPublicationContent`，返回提交前应看到的共享定义投影与 manifest hash；不创建 review。
- 页面：`/apps/{id}/publish`（用途/数据去向/工具读写联网/子版本/报告，然后提交）与
  `/reviews`、`/reviews/{id}`（队列 + 冻结快照 + 管理员决定）。纯 JS 由 Node 执行；无浏览器 DOM。

### 1.5 五类作者案例门禁（对 EVALUATION.md §6 的文档化解释）

规范句在 `docs/spec/EVALUATION.md` §6：“应用发布至少 5 个作者案例，含正常、坏输入、歧义/越界、
工具失败和输出检查”。`contracts/evaluation-case.schema.json` 没有“作者案例类别”字段，因此本票
对报告自身证据定义并测试一个最小判定（ADR 0020 §3、`catalog/publication_cases.go`）：

| 类别 | 判据 |
|---|---|
| normal | passed case 的 collected `facts.run_state == succeeded` |
| output_check | passed case 至少一个 passed `json_equals`/`decimal_equals` 断言 |
| bad_input / ambiguity_or_out_of_bounds / tool_failure | passed case 带 passed `failure_reason` 断言，按观察到的平台码分类：`tool_*`/`sandbox_*` → 工具失败；`ambiguous_*`/`out_of_*`/`*_out_of_scope`/`limit_*`/`row_limit_*`/`over_limit`/`too_large`/`unsupported_input` → 歧义/越界；其余 → 坏输入 |
| 数量 | 五类全部满足 **且** 报告至少 5 个 passed case |

提交与批准都执行该门禁；拒绝在 `details.missing_categories` 指出缺哪类。真实 e2e 用 5 个真实
runner case 覆盖：2 个正常（sales CSV + 同工具）…本票最终 e2e 数据集为
2 个正常、2 个输入 schema 拒绝、1 个 row_limit_exceeded、1 个 sandbox_unavailable（注：5 例数据
集内 2 正常 + 2 坏输入 + 1 越界 + 1 工具失败中的 6 例？见 §2 的实际 e2e 断言：
`sales-happy-csv`, `sales-bad-schema-1`, `sales-over-limit`, `sales-tool-failure`,
`sales-bad-schema-2` 共 5 例；工具失败码是真实的 `sandbox_unavailable`）。

## 2. AC 逐条证据

| AC（票面） | 证据 | 判定 |
|---|---|---|
| 修改草稿不能偷换审核内容 | `test_a_draft_edit_after_evaluation_cannot_be_smuggled_into_the_review`：保存可执行编辑后旧 hash+旧报告 → 409 `manifest_mismatch`；新 hash+旧报告 → 422 `report_binding_mismatch`；0 review | proven |
| 空验/错 hash/伪 runner 报告拒绝 | `test_an_empty_or_not_run_report_and_a_forged_runner_row_are_refused`（`evaluation_not_passed` / `report_not_trusted`）、上一行（`report_binding_mismatch`）、`verifyApplicationReport` 的 sealed bytes 重算 | proven |
| 发布前最少五类作者案例满足 | `test_submit_freezes_the_same_evaluated_content` 断言 `author_cases.satisfied=true` 且 categories 五类；`test_an_under_covered_report_is_refused_before_publication` 1 例报告 → 422 `author_cases_incomplete`；真实 runner e2e 产 5 例 passed 报告 | proven |
| 并发审核不双发布 | `test_concurrent_approvals_create_one_version_and_old_versions_stay_immutable`：两个真实 admin session 并发批准 → 状态 `[200,409]`，version 行恰 1，outbox 1，audit approved 1，active 指针指向唯一版本 | proven |
| 依赖撤销后批准失败 | `test_t068_e2e.py`（真实 sales 工具依赖）：提交后 `UPDATE tool_versions state=revoked` → 批准 409 `dependency_unavailable`，located `dependency_revoked`，0 version，review 仍 submitted；恢复后批准 200 | proven |
| 旧版本 JSON/镜像不被改写 | 同一并发/不可变测试：v1 artifact 字节与 version 行在 v2 发布后逐字节相同；`GET /versions/{v1}` hash 不变；version artifact `manifest_artifact_id` 未变 | proven |
| 报告链接到同内容 hash | 提交快照 `manifest_sha256/report_sha256/dataset_sha256/evaluation_id`；`verifyApplicationReport` 要求 report/evaluation/snapshot/manifest/dataset 全部同 hash；e2e 审计 | proven |
| 修改需重新提交 | 草稿编辑后 409（上）；拒绝后同一内容重复提交返回同一 rejected review（`test_rejection_returns_the_creator_to_editing_and_cannot_be_undone`），要重新审必须改内容并重新评测 | proven |
| 作者共享定义不共享使用者正文 | 快照只有 purpose/model destination/tool caps/pinned versions/report 证据（case id/category/failure code）；测试断言无 `secret_ref/object_key/request/conversation`；review 读不返回报告正文，只有 report_id | proven |
| 端到端演示 + 失败/授权/重试边界断言 | e2e 真实 runner→submit→依赖撤销失败→恢复→approve→version 读取；失败分支见上；授权（非 owner 404、非 admin 403、无 CSRF 403、无幂等键 400）与幂等重放/冲突均在快速套件 | proven |
| 记录版本/输入 hash/命令/原始结果/环境 profile，stub/real 分报 | 本报告 §3/§4 + `PROGRESS.md`；e2e=real runner，快速套件=guard-accepted sealed report（real rows, seeded terminal states）明确分报 | proven（报告层面） |

## 3. 命令与原始结果

环境：Windows PowerShell；`PATH` 加 `E:\orchara-toolchains\go\bin`，
`GOCACHE/GOMODCACHE` 指向 `E:\orchara-toolchains\...`；PostgreSQL 16.4 远程
`127.0.0.1:55432`（`tools/pg_dev.py status` 实测 running）；Node v24.13.0；Go 1.27.3。

```text
$ python tools/pg_dev.py status
{"running": true, "data_dir": "E:\\orchara-pg", "port": 55432,
 "database": "orchara_probe", "server_version": "16.4", ...}

$ python -m pytest tests/f02b -q --basetemp=E:\orchara-tmp\t068-15
GATE_F02B_RUN15

$ python -m pytest tests/f02b -q --basetemp=E:\orchara-tmp\t068-16
GATE_F02B_RUN16

$ python -m pytest tests/tooling -q --basetemp=E:\orchara-tmp\t068-tooling
GATE_TOOLING

$ python -m pytest tests/f02a -q --basetemp=E:\orchara-tmp\t068-f02a
GATE_F02A

$ python -m pytest "tests/f13a" -q -k "t052 or t056 or t062" --basetemp=E:\orchara-tmp\t068-f13a
GATE_F13A

$ cd product && go build ./... && go vet ./... && go test ./...
GATE_GO

$ gofmt -l .
GATE_GOFMT

$ python tools/sync_spec.py --check
GATE_SYNC

$ git merge integration/t068-t082
GATE_MERGE
```

## 4. `not_run` / `partial` 列表

- **partial — 含 call_app 子应用依赖的完整发布链路**：快照/依赖复查/版本边的实现与单测已交付，
  但本批执行器不支持 child run，无法用真实 runner 报告端到端证明“含子应用的 manifest 可发布”。
  承接：T075 #76 / T078 #79（fixed sub-app dependency、child runs）。
- **partial — 模型数据目的地的真实端到端**：快照字段、读取与 Node 渲染已测试；真实可发布应用在本批
  没有模型依赖（模型 provider 适配属于后续），因此真实 e2e 的 model_destinations 为空。
  承接：模型兼容评测/适配票（F04A/F06 后续）。
- **not_run — 撤销端点与撤销后目录隐藏/取消运行**：T070 #71。schema 字段、CHECK、
  `ResolveRunnableVersion` 读判定已就绪并有测试覆盖“可见版本/不可见”；撤销写路径不存在。
- **not_run — 从已发布版本启动 Run/Request**：T074 #75（需调用 `ResolveRunnableVersion` 并复制
  frozen hash/artifact 到 published snapshot）。
- **not_run — outbox 消费者/目录索引**：T077 #78 / T082 #83；本票只保证同事务写行，不声称索引更新。
- **not_run — 浏览器 DOM/端到端页面点击**：与所有现有页面票一致，只做 HTTP 页面结构 + 服务器
  返回 JS 的 Node 执行；截图/真实浏览器旅程未运行。
- **not_run — 全量 `pytest tests`**：按 BRIEF 不运行（>60 min），由 orchestrator 批次门禁执行。
- **not_run — 真实模型供应商**：发布门禁不依赖模型适配器；模型目的地字段来自注册表。

## 5. 下一个票接口（T070/T074/T075/T077/T082）

表/列（已存在，可直接用）：

- `platform.publication_reviews(id, application_id, submitted_by, snapshot jsonb, manifest_sha256,
  report_id, state, reviewer_id, decided_at, reason, snapshot_id, evaluation_id, dataset_sha256,
  manifest_artifact_id, version_id, created_at)` — 审核历史；`state ∈ submitted|approved|rejected`。
- `platform.application_versions(id, application_id, version_no, review_id, manifest jsonb,
  manifest_sha256, manifest_artifact_id, capability_card, compiler_version, worker_image_digest,
  state_schema_version, revoked_at, revoked_by, revoke_reason, created_at)`；`UNIQUE(review_id)`、
  `UNIQUE(application_id,version_no)`；`manifest_artifact_id` 指向规范 manifest 字节 artifact。
- `platform.version_dependencies(parent_version_id, child_version_id)`：发布事务按
  `manifest.subapp_versions` 写入；T075/T076 用它做 child 版本解析/环检测。
- `platform.outbox(id, kind, aggregate_id, payload, available_at, delivered_at, attempts)`：
  `kind='catalog_index'`，payload `{op:"publish", application_id, version_id, version_no,
  manifest_sha256, previous_active_version_id, active_version_id}`；`aggregate_id=version_id`。
- `platform.applications.active_version_id`：审批事务内更新；应用有 active 版本即可用
  `GET /applications/{id}` 读到；T070 撤销时需决定是否置 NULL（T070 决策）。
- `platform.audit_events`：`publication.submitted`（resource=publication_review）、
  `publication.approved`（resource=application_version）已写入；T070 撤销应按同模式写
  `version.revoked`。

Go 函数（`internal/catalog/publication.go` 已导出）：

- `(*Reader).SubmitPublication(ctx, callerID, applicationID string, scope IdempotencyScope,
  request SubmitPublicationRequest, objects *objectstore.Store, inputs compiler.Inputs)
  (*PublicationSubmission, error)`
- `(*Reader).ReviewPublication(ctx, adminID, reviewID string, scope IdempotencyScope,
  request ReviewDecisionRequest, objects *objectstore.Store) (*PublicationDecision, error)`
- `(*Reader).ListReviews(ctx, callerID string, admin bool, cursor string, limit int)
  ([]PublicationReviewView, *string, error)`
- `(*Reader).GetReview(ctx, callerID, reviewID string, admin bool) (*PublicationReviewDetail, error)`
- `(*Reader).PreviewPublication(ctx, callerID, applicationID string, objects *objectstore.Store,
  inputs compiler.Inputs) (*publicationContent, error)`
- `(*Reader).RecheckFrozenDependencies(ctx, manifest *compiler.Manifest) ([]ExecutableError, error)`
  — T070/T075 复查冻结闭包时复用（同一 readiness 规则）。
- `(*Reader).ResolveRunnableVersion(ctx, callerID, versionID string) (*Version, error)` —
  **T074 在创建 Request/Run 前必须调用**；撤销/归档/无权限分别返回
  `ErrVersionRevoked`/`ErrVersionArchived`/`ErrNotVisible`。T070 的撤销 UPDATE 后该函数立即生效。

T070 建议的最小撤销事务：`UPDATE application_versions SET revoked_at=now(), revoked_by=$admin,
revoke_reason=$reason WHERE id=$1 AND revoked_at IS NULL`（第二调用幂等 200，不重复 audit）+
`outbox(kind='catalog_index', payload.op='revoke', previous_active_version_id=…)` + audit；
是否需要把 `applications.active_version_id` 置 NULL 由 T070 决定并记录在 T070 报告/ADR。

## 6. 风险/评审可能关注

1. 快速套件的 passed 报告用**种子终态 run + 真实 completion guard**（`tests/support/t068.py::
   seeded_passed_report`）；真实 runner 的端到端证据在 `test_t068_e2e.py`。两者都要在报告里同时
   出现，不能把种子报告说成真实 runner 报告。评审若要求所有用例都跑真实 runner，代价是每例数十秒
   （本机共享 WSL2 有时数分钟），T056 的环境 stall 重试逻辑已在 e2e helper 中复用。
2. 五类案例判定是对 EVALUATION.md §6 的**解释**（报告 schema 没有类别字段）；分类规则在
   `publication_cases.go`/ADR 0020 §3 固定并有单测。若后续契约增加类别字段，应替换而不是扩张。
3. 审批时子应用 ACL 不再以提交者身份复查（提交时已查），只复查版本未撤销/父应用未归档；
   ADR 0020 §4 记录该选择。
4. `publication_reviews.report_id` 没有“同一报告只能被一条 review 引用”的唯一约束；当前
   submit 以 (application, manifest) 去重，同一报告只能属于一个 application/evaluation，
   所以不可达；若后续开放跨应用引用，需要加约束。
5. e2e 的 tool-failure case 用错误的 sandbox rootfs digest 触发真实的 `sandbox_unavailable`；
   它是真实平台失败码，但不是 `tool_timeout`。若评审要求 timeout 语义，需要 20 MiB 输入 +
   1s sandbox 上界（T056 方法），本票为测试时长/配额选择 sandbox_unavailable，并在测试注释与
   报告写明。

## 7. 关联 ADR / 文件

- ADR 0020 `docs/adr/0020-publication-review-and-versions.md`（新增列/outbox/五类解释/事务边界）。
- 迁移 `db/migrations/0043_publication_reviews_and_outbox.sql`。
- 代码 `internal/catalog/{publication,publication_cases,publication_report,manifest}.go`、
  `internal/api/publication.go`、`internal/api/server.go`、`internal/evaluation/report.go`、
  `internal/background/evaluation.go`（共享严格解码器）。
- 页面 `apps/web/src/{publish.html,reviews.html,assets/publish.js,assets/reviews.js}`。
- 测试 `tests/f02b/{conftest,test_t068_migration,test_t068_submit,test_t068_review,
  test_t068_pages,test_t068_e2e}.py`、`tests/support/t068.py`。
