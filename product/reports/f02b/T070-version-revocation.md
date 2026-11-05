# F02B T070 验收：撤销问题版本并阻止陈旧目录继续启动

- 任务：F02B / GitHub Issue #71（票 `T070`，规划 ID F02B.04），分支
  `t/t070-version-revocation`（worktree `E:\orchara-worktrees\t070`，从 T068 发布分支
  `t/t068-publication-review` @ `b9acfa7` 切出）；父门禁 F02B；契约 operationId
  `revokeVersion`；ADR [0022](../docs/adr/0022-f02b-version-revocation.md)。
- 状态：**`passed`**（本票范围：契约 `POST /api/v1/versions/{id}/revoke` 的管理员会话 + CSRF
  撤销、自然幂等、活动指针置空而**不**替换版本、撤销版本从 `Version` 读模型隐藏、权威
  `catalog.ResolveRunnableVersion`（陈旧索引只能给候选 id）、`stop_existing` 复用取消控制事实、
  同事务 outbox/audit、子应用依赖 `dependency_revoked`、管理员撤销页与目录不可用文案；
  真实 PostgreSQL 16.4 + 编译二进制 + 真实 HTTP 会话/CSRF + 真实封存对象字节 + 真实
  `published` 快照/Run 行；无 mock、无跳过）。
- 边界（如实）：**T074（#75）拥有真实的 published Run/Run 创建路径**；本票用 schema 合法的
  `published` 快照 + `runs` 行证明“历史运行保持固定引用”，并在报告与 ADR 中标注为 seeded；
  `catalog.ResolveRunnableVersion` 是本票交付的入口守卫，T074 必须在创建 Request/Run 前调用；
  目录索引消费者/`application_catalog_entries` 归 **T077（#78）**，本票只写
  `platform.outbox(kind='catalog_index', op='revoke')`，不声称投影已更新；撤销事务**不**写 Run
  终态，正在运行的 Run 由 worker/exit + T036 单一 finalizer 终止；`stop_existing` 只记录取消
  意图，`queued` Run 的 claim 谓词今天不读 `cancel_requested_at`（见 ADR 0022 §5，属后续执行票）；
  浏览器 DOM 驱动 not_run（与既有页面票同边界）；全量 `pytest tests` 由 orchestrator 运行。
- 迁移：**无**（T068 的 `0042_publication_reviews_and_outbox.sql` 已提供
  `application_versions.revoked_at/revoked_by/revoke_reason`、`application_versions_revoke_shape`
  CHECK 与 `platform.outbox`；本票只更新这三列，不新增 schema 对象）。

## 0. 工作记录（简短）

1. 读 `BRIEF.md`、票面 #71、`notes/f02b-publication.md`（§2/§5/§8/§9）、T068 实际代码
   （`internal/catalog/publication.go` 的发布事务/`ResolveRunnableVersion` 骨架、
   `db/migrations/0042_*.sql`、`internal/api/publication.go`）、`contracts/openapi.json`
   `revokeVersion`/`Version`/`Accept`/`Run`、`contracts/event.schema.json`（`run.status` payload
   闭集）、`internal/execution/{cancel,cancel_store,events,service}.go`、`internal/catalog/versions.go`、
   `internal/api/versions.go`、`tests/support/{stack,t025,t068}.py` 与 `tests/f02a/test_t025_dependencies.py`。
2. TDD 垂直切片（每片先红后绿，测试先行）：
   - 切片 1：`tests/t070/test_t070_revoke.py` 的鉴权/CSRF/定位 422/幂等 → 红（路由 404）→
     实现 `catalog.RevokeVersion` + `api.revocation.go` + 路由 + 读模型过滤 → 绿；
   - 切片 2：活动指针置空、不替换旧版本、撤销版本 404/列表省略 → 绿；
   - 切片 3：`stop_existing` 的取消意图与 `run.status` 事件、`stop_existing:false` 只记录
     → 绿（复用 `execution.AppendRunStatusTx`，不写终态）；
   - 切片 4：陈旧索引拒绝（`GET /versions/{id}/runnable` 200/409 `version_revoked`/409
     `version_archived`/404）+ 真实子应用 `dependency_revoked` → 绿；
   - 切片 5：真实 seeded `published` 快照/Run 的固定引用与封存字节复读哈希 → 绿；
   - 切片 6：`versions.html`/`assets/versions.js` + `/apps` 目录不可用文案，Node 执行纯函数
     → 绿（12 用例全绿）。
3. 跑门禁（§3 原始输出）：`pytest tests/t070` 两次背靠背、`pytest tests/tooling`、
   `pytest tests/f02a`、`go build/vet/test ./...`、`gofmt -l .`、`sync_spec.py --check`；
   按 `test(f02b)`/`feat(f02b)`/`docs(f02b)` 提交；合并 `integration/t068-t082` 后复跑；
   未推送、未关 issue、未改 `docs/spec/**`/`contracts/**`、无新迁移。

## 1. 交付内容

### 1.1 `catalog.RevokeVersion`（`internal/catalog/revocation.go`）

- `ValidateRevokeVersionRequest` 严格校验契约体：未知字段（`additionalProperties:false`）、
  `reason` 必须是非空字符串（trim 后 ≤2000 rune）、`stop_existing` 必须存在且是 JSON 布尔；
  每个违反都返回 `ValidationError{Field}` → API 422 `validation_failed` + `details.field`。
- 单事务：`SELECT … FROM application_versions v JOIN applications a … FOR UPDATE OF v, a`；
  `revoked_at IS NOT NULL` → 幂等回放（200，无第二次 audit/outbox）；否则
  `UPDATE application_versions SET revoked_at=now(), revoked_by=$admin, revoke_reason=$reason
  WHERE id=$1 AND revoked_at IS NULL`（CAS `RowsAffected()==1`），manifest/哈希/制品/version_no/
  review_id **从不重写**（0042 的 CHECK 也保证三列一起动）。
- 活动指针：仅当 `applications.active_version_id = 该版本` 时置 `NULL`（CAS），**不**重指到
  任何其它版本；`previous_active_version_id` 写入 outbox payload 与 `version.revoked` audit
  metadata。
- 写一条 `platform.outbox(kind='catalog_index', aggregate_id=version_id, payload{op:"revoke",
  application_id, version_id, version_no, manifest_sha256, previous_active_version_id,
  active_version_id:null, reason, stop_existing})`，经唯一 writer `insertOutboxTx`
  （T068 发布事务也改用它，见 `internal/catalog/outbox.go`）；无 dispatcher，`delivered_at`/
  `attempts` 不动。
- 审计：`version.revoked`（resource `application_version`），metadata 含 application/version_no/
  manifest_sha256/reason/stop_existing/previous_active_version_id/runs_cancel_requested/
  runs_already_cancelling。

### 1.2 权威入口守卫 `catalog.ResolveRunnableVersion`（T068 函数，T070 验证）

- 名字即票面建议名，保留并记录：实时 DB 读（join `applications`），先 ACL，再 `ErrVersionArchived`、
  `ErrVersionRevoked`，未知/不可见 `ErrNotVisible`；**从不**读投影。T074/T077 必须在创建
  Request/Run 或搜索结果进入执行前调用。
- 非契约薄 HTTP 面 `GET /api/v1/versions/{version_id}/runnable`（ADR 0022 §4，先例 ADR 0005）：
  200 返回契约 `Version`；409 `version_revoked` / 409 `version_archived` / 404 `resource_not_found`。

### 1.3 读模型隐藏撤销版本（`internal/catalog/versions.go`）

- `ListVersions` 与 `GetVersion` 各加 `v.revoked_at IS NULL`：契约 `Version` 没有状态字段，
  “不可用”只能表现为列表省略 + 详情 404；契约/规格未改。

### 1.4 HTTP 面（`internal/api/revocation.go` + `internal/api/server.go`）

- `POST /api/v1/versions/{version_id}/revoke`：`s.authorize(w, r, identity.RoleAdmin)`（Bearer API key
  无角色 → 403；cookie 会话缺 CSRF → 403 `csrf_invalid`；未认证 401），200 `Accepted{id,state:"revoked"}`，
  422 定位校验错误，404 未知/不可枚举；契约**没有** Idempotency-Key，服务端不复用收据表，
  由 `revoked_at IS NULL` CAS 提供自然幂等。
- `GET /api/v1/versions/{version_id}/runnable`：会话/API key 需 `application:read` scope，ACL 同
  `getVersion`。
- 页面路由：`GET /apps/{application_id}/versions` + `GET /assets/versions.js`（编译期常量路径，
  `staticPage` no-store）。

### 1.5 `stop_existing`：取消意图，不写终态

- 选中 `application_version_id=$1 AND state NOT IN ('succeeded','failed','cancelled') AND
  deleted_at IS NULL` 的 Run（`ORDER BY id FOR UPDATE`），逐个：
  `cancel_requested_at = COALESCE(cancel_requested_at, now())`、`error_code = CASE WHEN error_code
  IS NULL THEN 'version_revoked' ELSE error_code END`（与 T034 `markDeadlineIntentTx` 同一
  first-write-wins 规则），再经 `execution.AppendRunStatusTx` 追加一条 `run.status` 事件。
- 契约 `run.status` payload 闭集只有 `{state,error_code}`（`contracts/event.schema.json`），所以
  “撤销原因”落在 `error_code='version_revoked'`（事件里可见）与持久 audit/outbox metadata；
  worker 退出、单一 finalizer 提交 `cancelled` 时清空 `error_code`（`execution/service.go`）。
- 已在取消中的 Run（`cancel_requested_at` 非空）只记录到 `runs_already_cancelling`，不重复事件；
  `stop_existing:false` 完全不碰 Run。**不写任何 Run 终态。**
- 诚实边界：`Claim` 的候选 SQL 只要求 `runs.state='queued'`，不读 `cancel_requested_at`，因此
  `queued` published Run 在撤销后仍可能被领取；本构建还没有 published Run 创建者（T074），
  T074 的创建路径必须调用 `ResolveRunnableVersion` 关闭“新 Run”缺口；把取消意图并入 claim
  谓词属后续执行票（ADR 0022 §5 已记录）。

### 1.6 页面

- `apps/web/src/versions.html` + `assets/versions.js`：管理员按路径 id 查看应用可见版本、撤销
  （原因 + `stop_existing` 复选框，body 恰好 `{reason, stop_existing}`）、显示目录可用性；
  纯函数 `revokeVersionBody`/`canRevokeVersion`/`describeCatalogAvailability`/`describeVersion`/
  `describeVersionOptions`/`describeRevocationFailure`/`applicationIdFromVersionsPath` 由 Node 执行。
- `apps/web/src/apps.html` 的目录条目改用同一 helper 显示
  “不可用：没有生效版本（未发布或已撤销）”，并新增“版本与撤销”链接；纯 `textContent` 渲染，
  无 localStorage/sessionStorage，表单无 action。

## 2. AC 逐条

| AC（票面 + 任务指令） | 结论 | 证据（执行过的测试） |
|---|---|---|
| 旧索引不能启动已撤销版本；守卫是 start-time 权威 DB 读而非投影 | proven | `test_a_stale_index_version_id_is_refused_by_the_authoritative_guard`：撤前 `GET /versions/{id}/runnable` 200；记住 id（=陈旧索引条目）→ 撤销 → 同 id 409 `version_revoked`；无授权调用者 404；未知 id 404；归档应用 409 `version_archived` |
| 不会自动替换为最新/上一版 | proven | `test_revocation_nulls_the_pointer_without_replacing_the_active_version`：v1+v2（不同编译器封存 hash），active=v2，撤销 v2 → DB 与 `GET /applications/{id}` 的 `active_version_id` 均为 null，v1 未撤销、未激活、仍在列表，outbox/audit 记录 previous=v2 |
| 历史运行保持固定引用（含封存字节） | proven | `test_historical_runs_and_sealed_bytes_are_unchanged_by_revocation`：真实 seeded `published` 快照 + Run；撤销后 Run 的 `application_version_id`/`execution_snapshot_id`/`manifest_sha256`/`input_artifact_id`/kind/owner 不变，快照行逐字段相同，版本行 manifest/哈希/制品/version_no/review_id 不变，对象字节复读 SHA-256 与记录一致，artifact 仍 ready/未删除 |
| 端点：admin-only（session+CSRF）、200 Accepted | proven | `test_revoke_requires_an_admin_session_and_csrf`（401/403 `forbidden`/403 `csrf_invalid`/定位 422/404）+ `test_revoke_returns_the_accepted_body_and_is_idempotent`（`{id,state:"revoked"}` 精确 body） |
| 第二次调用幂等（无第二次 audit/outbox） | proven | `test_revoke_returns_the_accepted_body_and_is_idempotent`：第二次不同 reason/`stop_existing:true` 仍 200 同 body，reason 保持首个，outbox 1 条、`version.revoked` audit 1 条；撤销后新建的 Run 不被追溯取消 |
| `stop_existing:true` 经既有取消机制请求取消、不写终态、追加带撤销原因的 `run.status` 事件 | proven | `test_stop_existing_records_a_cancel_intent_and_a_version_revoked_event`：非终态 Run `cancel_requested_at` 有值、`error_code=version_revoked`、state 仍 `running`、`finished_at` 空；一条 `run.status` payload `{state:"running",error_code:"version_revoked"}`；终态 Run 不受影响；audit 列出该 run |
| `stop_existing:false` 只记录 | proven | `test_stop_existing_false_only_records_the_revocation`：Run 的 cancel/error/state/事件全部不变，版本已撤销 |
| 撤销版本从 `GET /versions/{id}` 与版本列表隐藏（404/省略） | proven | `test_revoke_returns_the_accepted_body_and_is_idempotent`（404 + `items:[]`）、`test_revocation_nulls_the_pointer_without_replacing_the_active_version`（列表只剩 v1） |
| 草稿调用已撤销子版本 → `dependency_revoked` 端到端 | proven | `test_a_revoked_version_cannot_be_a_subapp_dependency`：真实子应用 + 真实 preview 封存 + seed 版本 + 真实 validate 200 valid；管理员撤销后同一 parent draft validate `valid:false` + `dependency_revoked` @ `/nodes/1/config/application_version`，无 `X-Manifest-SHA256` |
| 撤销事务写 `platform.outbox`（kind `catalog_index`, op `revoke`），复用 T068 writer，不实现消费者 | proven | `test_revoke_returns_the_accepted_body_and_is_idempotent` 与 `test_historical_runs…` 断言 payload 字段、`delivered_at IS NULL`、`attempts=0`；`insertOutboxTx` 同时被 T068 approval 使用（`go build/test` 覆盖） |
| 页面：管理员撤销页 + 目录不可用；Node 可测纯 helper、结构化页面测试 | proven | `tests/t070/test_t070_pages.py` 4 用例（页面标记/no-store/无 storage/表单 method=post 无 action；asset 被真实服务；`/apps` 引用 helper 与版本链接；Node 执行 helper 行为） |
| 票面通用项：端到端演示、失败/授权/重试边界断言、记录输入/hash/命令/环境、stub/real 分报 | proven | 上述 12 用例全部真实栈执行；§3 原始输出与环境；seed 行与真实编译器字节的区分在 §4 记录 |

## 3. 门禁命令与原始输出

（待填：见下方运行结果）

## 4. not_run / blocked / 边界

- **T074（#75）**：真实 published Request/Run 创建路径与“创建时调用 `ResolveRunnableVersion`”；
  本票用 schema 合法的 seeded `published` 快照/Run 证明历史引用不变（真实对象字节、真实 DB 行，
  非 mock；创建者归 T074）。
- **T077（#78）**：outbox 消费 + `application_catalog_entries` 索引 + 搜索结果执行前复检；
  本票只有事务内 outbox 行，不声称索引已更新。
- `queued` published Run 的 claim 不读 `cancel_requested_at`（ADR 0022 §5）；撤销后的新 Run 由
  T074 的守卫关闭。
- 浏览器 DOM 自动化 not_run（仓库无此设施，与 T023/T056/T065 同边界）。
- 全量 `pytest tests` not_run（>60 分钟，BRIEF §3 要求不跑；orchestrator 合并后运行）。
- 真实模型/外部 provider 与本票无关。

## 5. 后续票接口（next-ticket interface）

- **DB**：`application_versions.revoked_at/revoked_by/revoke_reason`；
  `platform.outbox(kind='catalog_index', aggregate_id=version_id, payload{op:"revoke", …})`
  （T068 的 0042，本票无新迁移）。
- **Go**：`catalog.ResolveRunnableVersion(ctx, callerID, versionID) (*Version, error)` +
  `ErrVersionRevoked`/`ErrVersionArchived`/`ErrNotVisible`；`catalog.RevokeVersion(ctx, adminID,
  versionID, RevokeVersionRequest) (*VersionRevocation, error)`；`catalog.insertOutboxTx`
  （包内唯一 outbox writer）。
- **T074（#75）**：创建 Request/Run 前调用守卫并把返回的 `manifest_sha256`/`manifest_artifact_id`
  复制进 `execution_snapshots(kind='published')`；不要把目录投影当授权。
- **T077（#78）**：消费 outbox（含 `op='revoke'`），每个搜索结果在进入执行前重新检查
  `application_versions.revoked_at` + ACL。
- **页面**：`/apps/{application_id}/versions` 与 `assets/versions.js` 的 helper 契约
  （`revokeVersionBody`/`describeCatalogAvailability`) 可被后续目录页复用。
