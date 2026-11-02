# F02A T023 验收：创作者编辑用途说明和草稿并处理保存冲突

- 任务：F02A / GitHub Issue #24（票 `T023`，规划 ID F02A.01+F02A.04），分支 `feat/f00b-f02a`
- 状态：**`partial`**（复核修复 2026-11-02 更正）。草稿保存/读回/冲突/版本读模型/编辑器主体全部
  真实运行通过；票面 AC3“非法草稿可保存但**不能调试/提交**”只完成了 validate(T025) 与
  debug(T027) 两条真实拒绝路径。**submit 拒绝不在本批**：`POST /submit` 返回 404 只证明端点
  不存在，不能当作“拒绝已实现”的证据；F02B 才实现发布提交。因此 AC3 保持 `partial`，不把
  F02B 的未实现写成闭环（见 §2 AC3 与 §8）。
- 代码提交：
  - `ebb8c34`（`feat(f02a): draft CAS, version read model and the creator editor`）
  - `63aa388`（`test(f02a): prove draft CAS, version ACL and editor behaviour on the real stack`）
  - 本报告、`PROGRESS.md`、ADR 0004 §12–17 在随后的 `docs(f02a)` 提交中
- 主要文件：
  - `db/migrations/0010_application_drafts.sql`（新增：四表、`execution_revision` 偏离、
    `active_version_id` 复合 FK、`report_id` 延期外键、权限收口、注释）
  - `internal/catalog/{draft,versions}.go`、`internal/api/{drafts,versions}.go`、`internal/api/server.go`
  - `internal/jcs/`（P01/T003 Go 移植 + 40 条 RFC 向量测试）
  - `internal/workflow/`（P05 Go 校验器直移植 + 嵌入 rules.json + corpus 对拍测试）
  - `internal/identity/apikeys.go`（新增 `application:write` scope）
  - `apps/web/src/{editor.html,assets/editor.js,assets/app.js,apps.html}`
  - `tests/f02a/`（9 文件 45 用例）、`tests/support/f02a.py`、`tests/support/fixtures.py`
  - `docs/adr/0004-f00b-f02a-boundaries.md` §12–17
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`tools/pg_dev.py`，数据目录 `E:\orchara-pg`）
  - Python 3.12.2；Node 24.13.0（页面 JS 行为/语法真实执行）
  - 环境 profile：`dev-stub`；T023 的测试 API 不配置 mTLS/对象存储（本票不移动对象字节）
  - 基线（T022 之后）：`tests/f02a` 新增 45；`tests/f00b` 60、`tests/f01` 155、
    `tests/tooling` 21，全部 0 failed / 0 skipped
- 真实/桩分报：**全部 real**。没有 stub、mock 或“静态校验当运行”。唯一未运行的自动化层是
  浏览器 DOM（与 F01 相同的边界），见 §6。

## 0. 工作记录（简短）

1. 读 brief/PLAN/票面/规格与契约；确认 0009 为最新迁移、F01 的 `active_version_id` 无 FK。
2. 写 0010；移植 JCS 与 P05 校验器并先跑通它们的 Go 测试（corpus 40 例、JCS 40 向量）。
3. 写 `internal/catalog` 草稿/版本读模型与 `internal/api` 5 个 handler、路由、scope。
4. 写编辑器页/JS 与 apps.html 入口；扩 `apiFull` 暴露 ETag 响应头（`api` 形状不变）。
5. 扩 `tests/support`、新建 `tests/f02a` 9 文件；真实 PG/二进制/HTTP 全量跑。
6. 修 0010 带来的 F01 seed 冲突（4 个失败）：F01 现在为带 `active_version_id` 的应用播种真实
   version/review/artifact 行；同时更新 T020 的两处 scope 词汇表测试。
7. 跑全部门禁、写报告/ADR/PROGRESS、提交。

## 1. 交付内容

### 1.1 迁移 `0010_application_drafts.sql`

- `platform.application_drafts`：契约基线 + `execution_revision bigint NOT NULL DEFAULT 1`；
  `revision` 是 ETag/CAS 计数器，`execution_revision` 只在 `definition`/`capability_card` 的
  jsonb 值变化时 +1（保存路径用 `IS DISTINCT FROM`，一次 UPDATE 同时维护两个计数器）。
  三列均 `CHECK (jsonb_typeof(...) = 'object')`。
- `platform.publication_reviews`：契约基线；`report_id uuid NOT NULL` **无 FK**——`evaluation_reports`
  属 T026，0010 不伪造该表；`COMMENT` 写明 T026 建表后补 FK。
- `platform.application_versions`：契约基线；`review_id` FK 指向同一迁移创建的
  `publication_reviews`；`UNIQUE(application_id, version_no)`、`UNIQUE(application_id, id)`。
- `platform.version_dependencies`：契约基线 + `CHECK(parent<>child)` + `child_version_id` 索引。
- `applications.active_version_id` 的 FK：契约的复合形式
  `(id, active_version_id) -> application_versions(application_id, id)`，`ON DELETE RESTRICT`。
  复合形式同时 guarantee 活动版本属于同一应用；版本只 `revoked_at` 不删除，删除导致孤儿是 bug，
  因此 RESTRICT 而不是 SET NULL。NULL（从未发布）豁免。
- 权限：`REVOKE ALL ON SCHEMA platform FROM PUBLIC`；`orchara_worker` 无 platform USAGE/表/序列
  权限（与 0006–0009 相同）；重复 `admin migrate` 输出 `no pending migrations`。
- 0010 的唯一跨票延期：T026 的 `evaluation_reports` FK。0010 不改 0001–0009。

### 1.2 草稿聚合（`internal/catalog/draft.go`）

- `CreateApplication`：单事务插入 application + `revision=1/execution_revision=1` 的空草稿，
  `definition={}`、`layout={}`、`capability_card={}`（不伪造用途卡内容）。
- `GetDraft`：owner 或未过期 read grant；其余一律 `ErrNotVisible` → 404；返回强 ETag
  `"<revision>"`；`Cache-Control: no-store` 由统一写响应函数保证。
- `SaveDraft`：可见性(404) → owner-only(403) → If-Match（缺失/空 428 `precondition_required`，
  过期/弱/列表/`*`/不可解析 412 `precondition_failed` + `current_revision`/`current_etag`）→
  DraftInput 结构校验(422) → `UPDATE ... WHERE revision = <etag>` 的 CAS。零行即 412，
  两个持同一 ETag 的写者不可能都成功；任何失败路径不写行。
- `ValidateDraftInput`：三键齐全、必须是对象、字段大小上限（definition/layout 256 KiB、
  card 64 KiB）、空卡 `{}` 与完整六键卡二选一、非空卡 `examples >= 2`；未知顶层键也 422。
  **不校验 DSL 语义**（PLAN §2 D6）。
- `CheckExecutable(draft)`：纯函数，跑 `internal/workflow.Validate`，把 Problems 映射为
  `{node_id?, code, pointer, message}`；pointer 指向 `/nodes/<i>/...` 时解析 node id，
  cycle 这类文档级错误的 pointer 为空。T023 只用 Go 单测证明其定位能力，**没有** validate/debug
  端点（见 §3）。
- 双计数器语义：相同 definition 不同 layout ⇒ `execution_revision` 不变、`revision`/ETag +1；
  definition 或 card 改变 ⇒ 两者都 +1；JSON 对象键序变化不算执行内容变化（jsonb 相等）。

### 1.3 版本读模型（`internal/catalog/versions.go`）

- `ListVersions`：先做应用级 ACL 判定（不可见/归档 → 404，而不是空页），列表 SQL 里再次把
  同一 ACL 谓词放进 WHERE，与 cursor/LIMIT 同层（先过滤后分页）；顺序
  `version_no DESC, id DESC`；游标为不透明 base64 `v1|<version_no>|<uuid>`；limit 默认 20、
  1..100（与 F01 `ParseLimit` 同一约定，空值按默认），坏游标 400 `invalid_cursor`。
- `GetVersion`：只返回不可变发布行的 `id/application_id/version_no/manifest_sha256/manifest`；
  不 join `application_drafts`；不可见/归档 → 404。
- 测试播种真实 `artifacts`/`publication_reviews`/`application_versions` 行并设置
  `active_version_id`；`getApplication` 的 `active_version_id`、列表项与详情三者一致。

### 1.4 API（`internal/api`）

| 方法/路径 | 行为 |
|---|---|
| `POST /api/v1/applications` | 201 `Application`；name trim、1..200 字符；422 `validation_failed`（field=name）；**会话须 creator 角色（第二次复核修复，ADR §56，403 `creator_role_required`）**；API key 需 `application:write` |
| `GET /api/v1/applications/{id}/draft` | 200 `DraftInput` + `ETag` + `Cache-Control: no-store`；404 不可见 |
| `PUT /api/v1/applications/{id}/draft` | 200 `Accepted{id,state:"saved"}` + 新 `ETag`；428/412/403/404/422/413 按 §1.2；**会话须 creator 角色（403 `creator_role_required`，先于可见性，不泄露存在性）**；会话/写 scope 需 CSRF |
| `GET /api/v1/applications/{id}/versions` | 200 `{items[], next_cursor}`；ACL 先于分页；404 不可见；400/422 游标/limit |
| `GET /api/v1/versions/{version_id}` | 200 `Version`；404 不可见 |
| `GET /apps/{id}/edit` | 服务编辑器页（`editor.html`），无查询串 |
| `GET /assets/editor.js` | 编辑器脚本 |

所有错误体沿用 `{error:{code,message,request_id,details}}`；`GET` draft 的 ETag 是保存所需的
`If-Match` 值。既有 `GET /api/v1/applications/{id}`（ACL 404）与 T022 路由未改。

### 1.5 编辑器页与 `apps.html`

- `editor.html`：用途卡六字段（purpose/includes/excludes/required_inputs/expected_outputs/examples，
  列表按行）、definition/layout 文本域、当前 revision、状态区、冲突面板（“重载服务器版本”+
  “保留本地副本（下载 JSON）”），单一 `<form method="post">`（无 action/查询串），无
  localStorage/sessionStorage/location.search。
- `assets/editor.js`：`editorApplicationId`（从 `/apps/<id>/edit` 路径取 id）、
  `capabilityCardFromFields`（空卡 `{}`；非空卡六键 + examples≥2 客户端先拒）、
  `draftPayloadFromFields`、`loadDraftRequest`（GET + ETag）、`saveDraftRequest`
  （PUT + `If-Match` + CSRF）、`isPreconditionConflict`（412）、`localCopyJSON`。
  412 时**不自动重试/不覆盖**：显示冲突面板并保留表单；用户显式重载才丢弃本地修改。
- `assets/app.js`：新增 `apiFull`（返回 `{payload,status,headers}`，`api` 变成 payload 包装，
  既有调用点形状不变），错误对象新增 `details`（412 的 current revision 来自这里）。
- `apps.html`：新增“新建应用”表单（POST 后跳 `/apps/<id>/edit`）与每项“编辑草稿”链接；
  保留 T019 的全部标记与“只显示/管理员”文案。

## 2. 验收标准逐条

| # | 验收标准（票面） | 证据（真实命令/测试） | 结果 |
|---|---|---|---|
| 1 | 缺 If-Match 为 428 | `test_t023_drafts.py::test_missing_or_empty_if_match_is_428_and_changes_nothing`（None 与空串，code `precondition_required`、details.header、DB 不变） | **passed** |
| 2 | 冲突为 412 | `::test_stale_and_garbled_etags_are_412_and_change_nothing`（`"1"`、`"99"`、`W/"2"`、`"abc"`、`*`、多值；details 带 current revision/etag）、`test_t023_concurrency.py`（两写者恰一 200 一 412） | **passed** |
| 3 | 非法草稿可保存但不能调试/提交 | 保存：`test_t023_invalid_draft.py::test_a_cyclic_definition_saves_and_reads_back_verbatim`（P05 corpus 的 cycle 定义，200 + 原样读回）；定位：`internal/catalog/draft_test.go::TestCheckExecutableLocatesACycleAndAcceptsAValidWorkflow`（code=cycle）。**调试拒绝已真实**：`test_an_invalid_draft_is_refused_by_validate_and_debug_while_submit_is_absent`（`POST /validate` 200 valid:false；`POST /debug-runs` 缺 key 400、带 key 422 定位错误）。**提交拒绝未实现**：同测试的 `POST /submit` 仍是 404——这是“端点不存在”，不是“提交被拒绝”，F02B 才实现 submit | **partial**（validate T025 + debug T027 为真实证据；submit 拒绝属 F02B，绝不写成闭环） |
| 4 | 刷新能加载已保存草稿 | `test_t023_drafts.py::test_save_then_get_round_trips_every_field_and_the_new_revision`（GET after PUT 完全相等 + 新 ETag）、`test_t023_pages.py::test_the_real_creator_path_serves_the_page_and_refreshes_the_saved_draft`、Node 执行 `loadDraftRequest`（GET+ETag） | **passed**（HTTP 层；DOM reload 属浏览器边界，见 §6） |
| 5 | 并发编辑不静默覆盖 | `test_t023_concurrency.py::test_two_writers_with_the_same_etag_get_exactly_one_200_and_one_412`（两个真实会话、同一 ETag、不同内容；库中恰为胜者内容；败者 details current_revision=2）、`::test_a_sequential_stale_writer_is_refused_and_the_row_is_unchanged` | **passed** |
| 6 | 列表/详情/固定版本引用一致 | `test_t023_versions.py::test_application_list_and_version_detail_agree_with_the_seeded_rows`（active_version_id == 列表最新项 == getVersion）、`::test_versions_are_newest_first_with_a_stable_cursor_and_bounded_limit`、`::test_version_reads_never_expose_the_draft_body`、`::test_versions_of_an_invisible_or_archived_application_are_404` | **passed** |
| 7 | 通过上述端到端入口实际演示；失败/授权/重试/恢复边界有断言，不以单层 mock 成功代替 | 真实 PG + 编译二进制 + 真实 HTTP：create→draft→save→refresh 全链路（`test_t023_drafts.py`、`test_t023_pages.py`）；401/403/404/412/428/413/422 逐类断言；`test_t023_acl.py`（owner/grant/读者/管理员/撤销/过期）、`test_t023_scopes.py`（read/write scope 交集）；无 mock、无 skip | **passed**（唯一未自动化层：浏览器 DOM，见 §6） |
| 8 | 记录版本/输入/hash/命令/原始结果/环境；stub/real 分报，缺条件 blocked，未运行 not_run | 本报告 §1/§3/§4/§5；环境与版本见页首；全部 real，未运行项显式列出 | **passed** |

## 3. 与 T022/后续票的接口（下一票可依赖）

### 3.1 表（迁移 0010，platform schema）

- `application_drafts(application_id PK, revision bigint>0, execution_revision bigint>0,
  definition jsonb, layout jsonb, capability_card jsonb, updated_at)`：
  `revision`=ETag/CAS；`execution_revision`=T025 manifest `source_revision`；
  空卡表示为 `{}`。
- `publication_reviews(id, application_id, submitted_by, snapshot, manifest_sha256, report_id[无 FK],
  state∈submitted/approved/rejected, reviewer_id, decided_at, created_at)`。
- `application_versions(id, application_id, version_no, review_id, manifest, manifest_sha256,
  manifest_artifact_id, capability_card, compiler_version, worker_image_digest,
  state_schema_version, revoked_at, created_at)`；`UNIQUE(application_id,version_no)` 与
  `UNIQUE(application_id,id)`。
- `version_dependencies(parent_version_id, child_version_id)`（PK + parent<>child）。
- `applications.active_version_id` 复合 FK，`ON DELETE RESTRICT`。

### 3.2 Go 接口

- `catalog.Reader.CreateApplication(ctx, ownerID, name) (*Application, error)`
- `catalog.Reader.GetDraft(ctx, callerID, applicationID) (*Draft, error)`
- `catalog.Reader.SaveDraft(ctx, callerID, appID, ifMatch string, input DraftInput) (*SaveResult, error)`
- `catalog.Draft{ApplicationID, Revision, ExecutionRevision, Definition/Layout/CapabilityCard
  json.RawMessage, UpdatedAt}`、`Draft.ETag()`、`catalog.FormatETag/ParseETag`
- `catalog.ValidateDraftInput(DraftInput)`、`catalog.CheckExecutable(Draft) ([]ExecutableError, error)`
- `catalog.Reader.ListVersions(ctx, callerID, appID, cursor, limit, now)` / `GetVersion(...)`
- `workflow.DefaultRules()`、`workflow.Validate(document, rules, options)`、
  `workflow.Rules.ReasonMeaning(code)`；`jcs.Canonicalize/CanonicalizeBytes/SHA256Hex`
- `identity.ScopeApplicationWrite == "application:write"`

T025 应使用 `execution_revision` 作为 manifest `source_revision`，使用
`catalog.CheckExecutable`/`workflow.Validate` 而不是重写规则；T027 应使用 draft 行 `revision`
作为 `draft_revision`，并沿用本票的 404/403/412 语义。

### 3.3 HTTP

见 §1.4。页面入口：`/apps`（新建/进入编辑）→ `/apps/{id}/edit`。

## 4. 门禁原始结果（本票最终运行）

> 下列数字是**本票完成时**（早于 2026-11-02 两轴复核修复）的原始结果；复核修复后的最新门禁与
> 用例数见 `PROGRESS.md` 的“F00B/F02A 两轴复核修复”与“F00B/F02A 第二次复核修复”两节。本票证据
> 本身不变；第二次复核新增 `tests/f02a/test_t023_creator_role.py`（creator 角色门禁）并更新了
> `test_t023_acl.py` 中“管理员保存”的拒绝码（无 creator → 403，先于可见性；场景本意是 owner-only，
> 该用例的 outsider 分支仍是 404）。

```text
$ E:\orchara-toolchains\go\bin\go.exe vet ./...
(no output; exit 0)

$ E:\orchara-toolchains\go\bin\go.exe test ./...
?   	orchara/platform/cmd/admin	[no test files]
?   	orchara/platform/cmd/api	[no test files]
?   	orchara/platform/cmd/maintenance	[no test files]
?   	orchara/platform/cmd/supervisor	[no test files]
?   	orchara/platform/cmd/worker	[no test files]
?   	orchara/platform/db	[no test files]
ok  	orchara/platform/internal/api	(cached)
?   	orchara/platform/internal/audit	[no test files]
ok  	orchara/platform/internal/background	(cached)
?   	orchara/platform/internal/background/handlers	[no test files]
ok  	orchara/platform/internal/catalog	(cached)
ok  	orchara/platform/internal/config	(cached)
ok  	orchara/platform/internal/db	(cached)
ok  	orchara/platform/internal/health	(cached)
ok  	orchara/platform/internal/identity	(cached)
ok  	orchara/platform/internal/jcs	(cached)
ok  	orchara/platform/internal/objectstore	(cached)
ok  	orchara/platform/internal/policy	(cached)
ok  	orchara/platform/internal/supervisor	(cached)
ok  	orchara/platform/internal/uuid	(cached)
ok  	orchara/platform/internal/workflow	(cached)

$ E:\orchara-toolchains\go\bin\gofmt.exe -l internal cmd tools
(no output)

$ python -m pytest tests/f02a -q
.............................................                            [100%]
45 passed in 141.38s (0:02:21)

$ python -m pytest tests/f00b -q
............................................................             [100%]
60 passed in 153.97s (0:02:33)

$ python -m pytest tests/f01 -q
........................................................................ [ 46%]
........................................................................ [ 92%]
...........                                                              [100%]
155 passed in 445.23s (0:07:25)

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 7.97s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest

$ git status --short
(empty)
```

`tests/f02a` 分布：migration 8 + drafts 11 + execution_revision 3 + concurrency 2 + ACL 4 +
scopes 2 + versions 6 + invalid-draft 2 + pages 7 = **45**；第二次复核新增
`test_t023_creator_role.py` 4 个用例（跨 T023/T025/T027 的 creator 角色门禁）。

## 5. 契约缺口与显式偏离

1. **空草稿 vs DraftInput.capability_card**（契约缺口，最重要）：契约要求六键 + examples≥2，
   新草稿没有卡。T023 用 `capability_card: {}` 表示“未填写”，接受 `{}` 保存与 GET→PUT 往返；
   非空卡严格六键 + examples≥2，不伪造示例。T025/T027/F02B 必须继续接受 `{}`。
   ADR 0004 §13。
2. **428 的 error.code**：契约只规定 428 状态，未固定 code；实现用 `precondition_required`
   （412 用 `precondition_failed`）。详见 API.md §1 的通用错误分类。
3. **scope 词汇表**：F01 只有 `application:read`；契约却给 createApplication/saveDraft 列了
   `apiKey` security。T023 依 T020 注册表注释扩展 `application:write`（实现扩展，非契约修改），
   编辑一个应用需要 read+write 两把 scope；会话不受 scope 限制。同步更新 F01 的两个测试
   （一个“未知 scope”取值、一个注释），未放宽任何断言。
4. **active_version FK 形状**：brief 写 `active_version_id -> application_versions(id)`；
   实现采用 `contracts/database.sql` 的复合 FK `(id, active_version_id) ->
   (application_id, id)`，它是单列引用 + “版本属于本应用”的更强约束，`ON DELETE RESTRICT`。
5. **publication_reviews.report_id**：按契约类型建列、**无 FK**；`evaluation_reports` 由 T026
   创建后补 FK（迁移/ADR/报告三处写明），在此之前不得视为已验证引用。
6. **Version 无撤销字段**：契约 `Version` 只有 5 个字段；撤销版本仍可读，不显示 revoked 状态
   （F02B 的发布/撤销语义）。
7. **listVersions 排序**：契约未固定顺序；实现为 `version_no DESC, id DESC` 的 keyset，游标
   不透明。空 `limit` 值按默认（与 F01 应用列表的 `ParseLimit` 一致）。
8. **400 vs 428 的语法优先**：请求体不是合法 JSON 时先返回 400 `invalid_json`；结构 422 在
   428/412 之后。语法错误不泄漏存在性，且不属于契约 DraftInput。

## 6. 未运行 / 限制（not_run，如实）

- **浏览器 DOM/HCI 未运行**：没有 headless browser 驱动表单，页面断言是“服务端真实返回的
  HTML/JS + Node 真实执行 JS 逻辑（stub fetch）”，与 F01 的边界相同。未做截图或 §5 HCI 任务。
- validate/debug/submit 端点**未实现**（T025/T027/F02B）；`test_t023_invalid_draft.py` 以 404
  证明缺失，不冒充通过。
- manifest/hash/artifact 写入、依赖就绪与环检测（T025）、debug 快照/队列（T027）、发布/审核
  （F02B）均未在 T023 实现。
- 版本行的 manifest/artifact 是测试用 SQL 播种的真实表行；没有对象字节（T025 才生成 manifest
  artifact，T024 才有上传）。
- Redis/S3/真实 worker 协议与 T023 无关，沿用 T022 的 not_run 状态。
- 没有修改 `probes/`、`docs/spec/`、`contracts/`。

## 7. 真实/桩分报

| 层 | 状态 | 说明 |
|---|---|---|
| PostgreSQL 16.4 迁移/约束/CAS/ACL/分页 | real | 每个测试独立 `CREATE DATABASE` + 真实 `cmd/admin migrate` |
| Go 二进制（api/admin） | real | 每次会话编译后以真实进程启动，真实 socket |
| HTTP 会话/CSRF/API key bearer | real | 真实登录/激活/签发 key，无内存注入 |
| 编辑器 JS 逻辑 | real（Node 24 vm） | stub fetch 只替代浏览器网络；请求构造/412 分支/载荷校验真实执行 |
| 浏览器 DOM/渲染 | not_run | 无 headless browser；与 F01 相同限制 |
| P05 校验器移植 | real | 40 例 corpus 对拍 verdict/reasons/hash/canonical；嵌入 rules 逐字节等于 probe |
| JCS 移植 | real | 40 条 RFC/T003 向量（含错误码） |
| validate/debug/submit | not_run（不属本票） | 端点 404；由 T025/T027/F02B 交付 |

## 8. 复核修复（2026-11-02）：AC3 回到 `partial`，不把 404 当拒绝

复核纠正了早前“AC3 已闭环、状态改为 passed”的记录。事实边界如下：

- **真实交付且可复跑**：非法草稿能保存并原样读回；`POST /applications/{id}/validate`（T025）对
  空草稿/非法草稿返回 200 `valid:false` 并定位错误；`POST /applications/{id}/debug-runs`（T027）
  对不可执行草稿返回 422 `validation_failed` + `details.errors`。这两条是真实端点、真实拒绝。
- **不构成拒绝证据**：`POST /applications/{id}/submit` 返回 **404**，因为 F02B 还没有实现发布
  提交端点。缺失的端点没有拒绝逻辑可失败，不能证明“非法草稿不能提交”；把 404 写成通过就是把
  未实现当验收。
- 因此票面 AC3（“不能调试/提交”）只能是 **`partial`**：validate/debug 侧由 T025/T027 闭环，
  **submit 侧由 F02B 闭环**。本报告页首、§2 AC3 与 `PROGRESS.md` 的 T023 行/段落均已改为
  `partial`；`tests/f02a/test_t023_invalid_draft.py` 的测试改名为
  `test_an_invalid_draft_is_refused_by_validate_and_debug_while_submit_is_absent`，docstring 明确
  “404 是缺失而不是拒绝”，断言本身未放宽。
- 其余限制不变：发布提交端点不存在（F02B）；浏览器 DOM 渲染仍未跑（§6）。
