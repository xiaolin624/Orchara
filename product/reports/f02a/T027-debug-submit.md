# F02A T027 验收：发布前提交私有草稿并查看固定调试任务

- 任务：F02A / GitHub Issue #28（票 `T027`，规划 ID F02A.03），分支 `feat/f00b-f02a`
- 状态：**`passed`**（本票交付范围 = 提交与排队）。真实 PostgreSQL 16.4 + 真实编译二进制 +
  真实 HTTP + 真实文件系统对象字节；无 mock、无跳过。边界（如实）：真正 worker 的领取/执行/
  事件/结果属 F03，本批不实现也不声称已执行；全量 `pytest tests` 已在复核修复收尾运行一次（§3.5）。
- 主要文件：
  - `db/migrations/0014_execution_snapshots_and_runs.sql`（执行身份五表 +
    `evaluation_jobs_snapshot_fkey` + 三个延期外键的显式记录 + 权限收口）
  - `internal/catalog/{debug.go,debug_test.go}`（校验顺序、revision 冻结、幂等 replay、
    输入 artifact 密封、run/job/snapshot 事务、`GetRun`）
  - `internal/api/{debug.go,server.go}`（`debugDraft` 202、`getRun` 200、`X-Manifest-SHA256`、
    错误映射、路由与页面）
  - `apps/web/src/{debug.html,assets/debug.js}`（调试入口页），
    `apps/web/src/{editor.html,assets/editor.js,apps.html}`（入口链接）
  - `tests/support/{t027.py,fixtures.py}`、`tests/f02a/test_t027_*.py`（7 文件 38 用例）
  - `docs/adr/0004-f00b-f02a-boundaries.md` §38–44
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`，
    `GOCACHE=E:\orchara-toolchains\gocache`、`GOMODCACHE=E:\orchara-toolchains\gomodcache`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`tools/pg_dev.py`）
  - Python 3.12.2（`python -m pytest`）、Node 24.13.0（页面 JS 语法与行为真实执行）
  - profile `dev-stub`；对象目录在测试 `tmp_path`；部署输入沿用 T025 占位值
    `ORCHARA_WORKER_IMAGE_DIGEST=sha256:aaaa…(64)`、runtime lock =
    `probes/agent_min/requirements.lock.txt`（真实 SHA-256）
  - 冻结的 golden 执行 hash（与 T025 相同）：
    `9cc418d167377e667c77284ce7b35819c7374c0459ec90e41f4131719aef5686`
- 真实/桩分报：**全部 real**（PG/二进制/HTTP/对象字节/Node 执行页面逻辑）。测试用 SQL 播种
  工具/模型/已发布版本/artifact 行，因为本批没有 F05 的工具构建审批、F02B 的发布写入与 T028 的
  用户上传；debug 的准入判定本身在 Go 内对真实行/约束执行。浏览器 DOM 渲染仍 `not_run`
  （与 F01/T023 相同的边界）。

## 0. 工作记录（简短）

1. 读 brief/PLAN/票面/规格/契约与 T022–T026 代码/测试/报告/ADR，确认 0013 为最新迁移、树干净。
2. 写 0014（五表 + 延期 FK 记录 + `evaluation_jobs_snapshot_fkey`），空库 `0001→0014` 一次通过、
   重复 migrate `no pending migrations`、`orchara_worker`/PUBLIC 仍无 platform 权限。
3. 写 `internal/catalog/debug.go`：请求形状/规范摘要、幂等 replay、owner-only、revision 复核、
   复用 T025 `ValidateExecution(PrivateDebug:true)`、预算范围、输入 artifact 准入、单事务写
   snapshot/run/job/artifact/链接/收据；`GetRun` owner-only。配 6 个 Go 纯函数单测。
4. 写 `internal/api/debug.go` 与路由：202/200、`X-Manifest-SHA256`、422 定位错误、409
   revision/idempotency、400 缺 key、503 未配置；页面路由与 `debug.js` 静态服务。
5. 写调试页与 JS 纯函数/DOM 接线，编辑器与 apps 页加入调试入口（不改 T023 的草稿表单）。
6. 写 `tests/support/t027.py` + `tests/f02a/test_t027_*.py` 7 文件 38 用例；按 0014 收紧后的
   schema 适配 T023/T025/T026 的迁移链断言与 T026 的 snapshot seed；把 T023 的“端点仍缺失”
   断言改成“validate/debug 已拒绝、submit 仍属 F02B”。
7. 跑门禁（§3），写本报告/PROGRESS/ADR，按 feat/test/docs 三次提交；未推送、未关 issue、
   未改 `docs/spec/` 与 `contracts/`。

## 1. 交付内容

### 1.1 迁移 `0014_execution_snapshots_and_runs.sql`

- 五张契约表，全部 schema 限定、`IF NOT EXISTS`、重复执行 no-op：
  - `platform.execution_snapshots`：契约 13 列 + kind/身份 XOR CHECK；`manifest` 是 jsonb object
    （可查询投影，hash 永远按密封字节算）；`manifest_sha256` 64 字符 CHECK；debug 的
    `draft_revision > 0`。
  - `platform.runs`：契约 26 列 + 三条契约 CHECK（debug 无 request/conversation/version/parent；
    published 必须有 version；parent 必须有 parent_invocation_key 且无 request）+ 契约索引
    `one_active_top_run_per_conversation`、`runs_owner_time`、`runs_parent`。
  - `platform.jobs`：契约 8 列 + `one_live_job_per_run`、`jobs_ready` 部分索引。
  - `platform.run_artifacts`：契约 4 列，PK(run_id, artifact_id)。
  - `platform.idempotency_keys`：契约 9 列（含 `response_code`/`response_artifact_id`）+
    **新增 `response_json jsonb`**（T027 偏离，见 §2.2）+ 主键(principal, method, path, key) +
    `request_sha256` 64 字符与 key 非空白 CHECK。
- **补齐 T026 延期 FK**：`evaluation_jobs_snapshot_fkey (snapshot_id) -> execution_snapshots(id)`
  （guard 里判 `pg_constraint`，可重复执行）。从 0014 起 application-kind 评测的 snapshot 引用
  是真实外键。
- **三个继续延期的外键**（列存在、无 FK，迁移头/COMMENT 写明归属与补偿约束）：
  `runs.request_id`/`runs.conversation_id`（F05/F07 的 `requests`/`conversations`）、
  `jobs.current_attempt_token`/`run_artifacts.attempt_token`（F03 的 `run_attempts`）。本批只写
  NULL，任何代码不得把它们当已验证引用。
- 权限沿用 0007–0013：`REVOKE ALL ON SCHEMA platform FROM PUBLIC`，`orchara_worker` 无 platform
  USAGE/表/序列权限（测试逐个断言 SELECT/INSERT 被拒且授权清单为空）。
- 不建 requests/conversations/run_attempts/node_attempts/operations/approvals/run_events/
  quota_*/deletion_jobs，不写 published snapshot。

### 1.2 `internal/catalog/debug.go`：提交语义

- `ValidateDebugRequest`：`draft_revision` 整数 ≥1、`input` JSON 对象且可 RFC 8785 JCS 规范化
  （重复键/NaN/非法 Unicode 拒绝）、`artifact_ids` ≤5 个唯一 UUID 且必须出现（可为 `[]`）；
  未知顶层键按字段名 422。规范输入字节上限 20 MiB（对象存储契约上限）。
- `SubmitDebug` 固定顺序：形状(422) → **已提交收据 replay 或 409** → owner-only 草稿(404) →
  当前 revision(409) → T025 `ValidateExecution(PrivateDebug:true)`（422 定位 / 503）→ run
  预算/币种(422) → 引用 artifact(422) → 密封 JCS 输入字节 → 单事务创建。
- 幂等：`request_sha256 = SHA-256(JCS({application_id, artifact_ids(小写排序), draft_revision,
  input(JCS 字节)}))`；收据主键 `(principal, method, 具体 path, key)`；只有成功的 202 写收据；
  同 key 同摘要 → 原 202 Run（不重编译、不写对象、不建行）；同 key 异摘要 → 409；缺/空/重复
  header → 400。并发重复：第二个提交在唯一键上阻塞，赢家提交后它读回 202 并重放；输家删除本次
  密封的输入对象。实现里先查一次收据（快路径，避免为 replay 写对象），再在事务内 `INSERT …
  ON CONFLICT DO NOTHING` 认领（慢路径赢家唯一）。
- revision 冻结：事务内 `SELECT revision … FOR UPDATE` 复核；保存要么已提交（revision 变 → 409），
  要么阻塞到提交完成。每次保存都 +1 `revision`，所以 revision 相等 ⇔ `execution_revision` 未变
  ⇔ 校验时密封的 manifest 仍描述被冻结内容。
- 创建：`execution_snapshots(kind=debug, draft_revision, owner, manifest 投影, sha, T025 的
  manifest_artifact_id, compiler/worker digest/state schema)`、`runs(kind=debug, queued, idle,
  revision=1, next_event_seq=1, budget/currency 来自 definition.policy, request/conversation/
  version/parent NULL)`、`jobs(start, ready)`、输入 artifact（`run_input`, ready, JCS 字节,
  真实 sha/size, revision=1）、`run_artifacts(relation=input)` 链接规范输入 + 所有 artifact_ids。
  不创建 version/review/request/conversation/report/background job。
- 预算：`definition.policy` 的 `budget_limit`/`currency`；`budget_limit` 必须放进
  `numeric(20,8)`（≤12 位整数、≤8 位小数），否则 422 `policy_budget_out_of_range`；缺 policy 是
  防御性 `policy_incomplete`（P05 校验器通常已先报 `bad_document`）。
- 输入 artifact 准入：必须是调用者自己的、`state=ready`、未墓碑、有 sha256/size；错误定位
  `/artifact_ids/<i>`，码 `artifact_not_found`/`artifact_not_visible`/`artifact_not_ready`。
- `GetRun`：owner-only（WHERE owner_id），未知/畸形/已删除同答 `ErrNotVisible` → 404；只投影
  契约 10 字段。
- Go 单测（`debug_test.go`）：形状矩阵（13 个字段错误）、重复键拒绝、摘要规范/集合顺序无关、
  不同输入/revision/应用/artifact 集合摘要不同、`numeric(20,8)` 边界、预算/币种读取。

### 1.3 API

- `POST /api/v1/applications/{application_id}/debug-runs`（`debugDraft`）：会话 CSRF、API key
  `application:write`、**会话还须 creator 角色（第二次复核修复，ADR §56；管理员不豁免，
  403 `creator_role_required`，在请求体与 owner 判定之前）**、owner-only（其余含管理员 404）；
  202 + 契约 `Run`；`X-Manifest-SHA256` 返回冻结 hash（非契约扩展，§2.1）。API key 是服务账号，
  没有角色，仍只按 scope 判定。
- `GET /api/v1/runs/{run_id}`（`getRun`）：owner-only（管理员/read grant 均 404；畸形/未知同答）；
  200 + 同一 `Run` 投影 + `X-Manifest-SHA256`；API key 需 `application:read`。读路径不要求 creator。
- 错误映射：400 `idempotency_key_required`、400 `invalid_json`、409 `revision_conflict`
  （details requested/current）、409 `idempotency_conflict`、422 `validation_failed`
  （`details.field` 或 `details.errors` 定位）、404 `resource_not_found`、503
  `compiler_unconfigured`/`object_store_unavailable`、413 超限。
- 路由同时服务 `/apps/{application_id}/debug` 页面与 `/assets/debug.js`（编译期常量路径，
  无请求输入参与文件路径）。

### 1.4 Web 调试入口

- `apps/web/src/debug.html` + `assets/debug.js`：加载当前草稿 revision（ETag）预填、JSON 测试
  输入、可选 artifact UUID（≤5）、每次提交生成一个 `Idempotency-Key`（crypto.randomUUID，
  退化路径 32 hex；只在本页内存，不进 URL/storage）、CSRF header、`method="post"` 表单；
  202 后显示 **state / kind / Run.revision / 冻结的 draft revision / execution_snapshot_id /
  manifest_sha256**，并轮询 `GET /runs/{id}`（终态或 40 次后停止；本批会停在 queued，页面
  明确写“领取/执行/结果属 F03”）。
- 编辑器页新增 `#debug-entry` 链接（由路径段构造，无查询串）；apps 列表每项新增“调试”链接。
  T023 的草稿表单与结构断言保持不变。
- JS 纯函数（`debugApplicationId`/`debugPayloadFromFields`/`newIdempotencyKey`/`runSummary`/
  `runIsTerminal`/`shouldPollRun`/`submitDebugRequest`/`fetchRun`/`fetchDraftRevision`）在 Node
  24 真实执行（stub fetch），不是源码子串断言。

### 1.5 测试（`tests/f02a`，全部真实栈）

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `test_t027_migration.py` | 10 | 0014 位置/重放、五表列集、真实 FK 与三个延期 FK、契约 CHECK、checkpoint UNIQUE、索引、`evaluation_jobs_snapshot_fkey`（悬空拒绝/真实接受）、worker/PUBLIC 拒绝面 |
| `test_t027_debug.py` | 4 | 202 契约字段与队列状态、snapshot/run/job/输入 artifact/链接/收据逐字段断言、golden 字节与 hash、无 version/review/job/evaluation、owner 读、额外 artifact 链接 |
| `test_t027_immutability.py` | 2 | 改草稿后既有 run/snapshot/输入与 manifest 字节/hash 逐字节不变、旧 revision 409、新 revision 产生不同 hash 的第二份快照 |
| `test_t027_authz.py` | 2 | 匿名 401、**无 creator 角色管理员 403 `creator_role_required`**、持有 creator 的管理员/read grant 仍 404、CSRF 403、scope 403、service-account write key 仍 404；run 读同样 owner-only |
| `test_t027_refusals.py` | 7 | 旧 revision 409、非法 DSL/空卡可保存但不能 debug（T023 AC3）、pending model/未就绪/他人未批工具/无 ACL 子应用定位拒绝、作者私有工具规则（validate 拒/debug 收）、artifact 归属/就绪/形状、预算越界、编译器/对象存储 503 |
| `test_t027_idempotency.py` | 7 | 同 key 同体重放（含对象数不变）、改草稿后重放仍返回原 Run、异体 409、artifact 顺序无关、缺/空/重复 header 400、并发重复仅一个 Run（DB 触发器确定性重叠）、过期仍重放 |
| `test_t027_pages.py` | 6 | 页面结构与表单规则、JS 资产、编辑器/apps 入口、真实应用页面、Node 语法检查、Node 行为执行 |

合计新增 38 用例；`tests/f02a` 62 → **100 passed**。第二次复核新增
`tests/f02a/test_t023_creator_role.py`（4 用例，跨 T023/T025/T027 的 creator 门禁）；T027 的
`test_t027_authz.py` 按新门禁更新了管理员拒绝码，`tests/f02a` → 103 passed（详见
`PROGRESS.md` 的“F00B/F02A 第二次复核修复”）。

## 2. 契约缺口与显式偏离（如实）

1. **Run 没有 snapshot hash 字段，契约也没有 getSnapshot 端点**。`Run` 是
   `additionalProperties:false` 的 10 字段，没有 `manifest_sha256`/`draft_revision`；契约没有读取
   execution_snapshot 的 operation。为让“查看固定快照”诚实：
   - `debugDraft` 202 与 `getRun` 200 用**非契约扩展**响应头 `X-Manifest-SHA256`（64 位小写
     hex，与 T025 validate 同一先例）返回冻结 hash；页面展示 hash + `execution_snapshot_id` +
     提交时冻结的 draft revision（客户端事实）。服务端 snapshot 的 JSONB/draft_revision 只能由
     DB/worker 协议读取。
   - 后续契约修订应把它变成正式字段或新增 snapshot 读端点/内部协议字段（ADR §42）。
2. **`idempotency_keys.response_json`**：契约只有 response_code/response_artifact_id，没有响应体
   位置，而 API.md §1 要求“保存规范请求哈希和响应”。T027 新增该列并在同一事务写 202 body；
   F05/F06 继承。ADR §38 记录，未改 contracts。
3. **幂等 replay 优先于“当前 revision”规则**：同 key + 同规范请求描述同一次已授权操作，草稿
   之后被保存也返回原 Run；用新 key 指定旧 revision 才 409。brief 的两句话在这里只能二选一，
   本票选择“同 key 同请求必重放”并写测试（idempotency + immutability 两处证据）。
4. **预算范围检查是 debug 专有规则**：workflow schema 的 `budget_limit` 无上限，而
   `runs.budget_limit` 是 `numeric(20,8)`。`policy_budget_out_of_range` 只由 debug 返回；validate
   仍按 DSL 报 valid（manifest 不存预算）。ADR §40。
5. **三个延期外键**（§1.1）与 **`run_attempts` 不存在**：`jobs.current_attempt_token`/
   `run_artifacts.attempt_token` 只写 NULL；F03 必须补 FK。`runs.request_id`/`conversation_id`
   同理留给 F05/F07。
6. **worker 镜像 digest 仍是部署方占位输入**（继承 T025）：本环境未构建 worker 镜像
   （F04A/F15A）；debug 只证明同一 digest 参与 manifest/hash 且缺失时 503。
7. **application-kind 评测**：0014 补齐 FK 后 T026 的 `resolveBoundManifest` 对真实 snapshot 生效
   （owner 必须一致）；T027 不实现评测 UI/端点，真实 runner 仍属 F13A，T026 的 `partial` 只剩
   这一条。
8. **`debugDraft` 的 `artifact_ids` 是集合**：规范化时小写+排序，所以仅顺序不同的重放返回原
   Run；重复项则 422。

## 3. 实际命令与原始结果

工作目录 `E:\Orchara\product`；Go 环境变量同上。

### 3.1 Go

```text
> E:\orchara-toolchains\go\bin\gofmt.exe -l internal cmd tools
（无输出）

> E:\orchara-toolchains\go\bin\go.exe vet ./...
（无输出，exit 0）

> E:\orchara-toolchains\go\bin\go.exe test ./...
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
ok  	orchara/platform/internal/compiler	(cached)
ok  	orchara/platform/internal/config	(cached)
ok  	orchara/platform/internal/db	(cached)
ok  	orchara/platform/internal/health	(cached)
ok  	orchara/platform/internal/identity	(cached)
ok  	orchara/platform/internal/jcs	(cached)
ok  	orchara/platform/internal/modelproxy	(cached)
ok  	orchara/platform/internal/objectstore	(cached)
ok  	orchara/platform/internal/policy	(cached)
ok  	orchara/platform/internal/supervisor	(cached)
ok  	orchara/platform/internal/uuid	(cached)
ok  	orchara/platform/internal/workflow	(cached)
```

### 3.2 Python 真实栈

> 下列计数是本票完成时的原始结果；复核修复（2026-11-02）把 T025 的一个无效篡改测试移入 Go，
> f02a 总数随之为 99，最新门禁见 `PROGRESS.md`。

```text
> python -m pytest tests/f02a -q
........................................................................ [ 72%]
............................                                             [100%]
100 passed in 513.48s (0:08:33)

> python -m pytest tests/f00b -q
........................................................................ [ 57%]
.....................................................                    [100%]
125 passed in 356.76s (0:05:56)

> python -m pytest tests/f01 -q
........................................................................ [ 92%]
...........                                                              [100%]
155 passed in 621.23s (0:10:21)

> python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 4.95s
```

`tests/f02a` 100 = T023 45 + T025 17 + T027 38（本票完成时；复核修复后 T025 的一个无效篡改测试
移入 Go，f02a 为 99 = T023 45 + T025 16 + T027 38）；全部真实 PG/二进制/HTTP/对象字节，无 skip。

### 3.3 迁移

空库 `0001→0014` 一次通过（14 migration(s) applied），重复 `admin migrate` 输出
`no pending migrations`；`test_t027_migration.py` 与 T023/T025/T026 的迁移测试都断言链位置。

### 3.4 契约同步

```text
> python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

### 3.5 未运行

- 全量 `python -m pytest tests -q`：本票完成时按 PLAN §1 未跑；**2026-11-02 复核修复收尾已跑**
  （最新原始结果见 `PROGRESS.md` 的“F00B/F02A 两轴复核修复”一节）。
- 真实 worker 领取/执行/事件/结果/终态：未构建（F03）；本票只创建 queued run + ready start job。
- 浏览器 DOM/渲染：无 headless browser（与 F01/T023 相同），页面逻辑用 Node 24 真实执行。
- 真实 worker 镜像/registry、真实评测 runner：见 §2.6/§2.7。

## 4. 验收标准逐条

票面（`.scratch/v1-tickets/issues/T027.md`）：

| # | 验收 | 状态 | 证据 |
|---|---|---|---|
| 1 | 修改草稿不改变已有 debug | **passed** | `test_t027_immutability.py`：改 definition（revision 2、execution_revision 2）后 snapshot/run/输入 artifact 行逐字段相同、manifest 与输入对象逐字节相同、`X-Manifest-SHA256` 不变；旧 revision 新 key 409 |
| 2 | 他人不能读私有结果 | **passed** | `test_t027_authz.py`：管理员/read grant/他人 404，owner 200；**写入还要求 creator 角色（无角色管理员 403 `creator_role_required`）**；API key 需 scope 但 scope 不等于所有权；debug 与 run 两个入口都断言（读不要求 creator） |
| 3 | 未经授权工具或未 ready 依赖不能执行 | **passed** | `test_t027_refusals.py`：pending model、building tool、他人未批工具、无 ACL 子应用全部 422 定位且零行；作者自有 ready 工具在 debug 可用而 validate 拒绝（规则边界）；不创建可执行 job |
| 4 | 通过端到端入口实际演示；失败、授权与重试/恢复边界有断言 | **passed** | 真实 HTTP `debugDraft` 202 + `getRun` 200（owner）与 400/401/403/404/409/413/422/503；失败零行断言；重试边界 = 同 key 重放（含改草稿后、并发重复、过期收据），不同/缺失 key 的 409/400 |
| 5 | 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件 blocked，未运行 not_run | **passed** | 本报告 §1/§2/§3/§5；real/stub 明确；未运行项列 `not_run` |

Brief §2 交付项：

| 项 | 状态 |
|---|---|
| 0014：五表 + `evaluation_jobs_snapshot_fkey` + 延期 FK 文档 + `response_json` + 权限收口 | **passed**（迁移测试 10 用例） |
| debugDraft：Idempotency-Key、owner-only、当前 revision、PrivateDebug 校验、私有 snapshot + 输入 artifact + queued Run + ready start job、无 version/review/request/conversation | **passed**（`test_t027_debug.py`、`test_t027_authz.py`、`test_t027_refusals.py`） |
| `GET /runs/{run_id}` owner-only，他人/管理员 404 | **passed**（`test_t027_authz.py`） |
| Web 调试入口（选 revision + 测试输入 + 单次 Idempotency-Key + 展示 snapshot/run + 轮询） | **passed（逻辑层）**；浏览器 DOM `not_run`（Node 行为执行 + 真实页面 HTTP） |
| 测试：提交建行、字节 hash、改草稿不变、404、拒绝零行、幂等（含并发）、输入 JCS、页面 | **passed**（38 用例） |
| 报告/PROGRESS/ADR；T023 状态按证据更新 | **passed**（本文件、`PROGRESS.md`、ADR §38–44、T023 报告 §8；复核修复后 T023 为 `partial`，见 §4 末尾） |

**T023 AC3 的边界（复核修复 2026-11-02 更正）**：`test_t023_invalid_draft.py` 现在断言
`POST /validate` 对空草稿返回 `valid:false`、`POST /debug-runs` 缺 key 返回 400、带 key 对不可
执行草稿返回 422 定位错误；`test_t027_refusals.py` 进一步证明“非法 DSL/空卡先保存成功（200）
再被 debug 拒绝（422）”在同一真实栈上成立。这两条是真实的拒绝证据。但 `POST /submit` 仍是
**404——端点不存在**，不能当作“提交被拒绝”的通过项；发布提交属 F02B，尚未实现。因此 T023 AC3
保持 `partial`（validate T025 + debug T027 闭环，submit F02B 闭环），本票不代 T023 宣布
AC3 已闭环。T023 报告 §8 与 `PROGRESS.md` 的 T023 行/段落同步改为 `partial`。

## 5. 边界与未运行（not_run / 后续票）

- **F03 未实现**：领取/租约/attempt/事件/SSE/结果/退出 finalizer/cancel/终态。本票创建的
  debug run 会一直停在 `queued`，start job 停在 `ready`；页面如实说明。F03 必须：
  用 snapshot 的 `manifest_artifact_id` 取 `{object_key,sha256,size_bytes}` 并先调
  `compiler.VerifyManifestBytes`；补 `jobs_current_attempt_token_fkey`、
  `run_artifacts_attempt_token_fkey`；不得重编译或从 JSONB 重序列化推断 hash。
- **F02B 未实现**：发布审核/version 写入/发布提交端点（测试断言 `/submit` 404）；发布 snapshot
  必须 `kind=published` + `application_version_id` 并复用同一 manifest artifact。
- **F05/F07**：`requests`/`conversations` 与其 FK、幂等收据过期清理、deletion 模型。
- **T028**：用户上传的 `purpose`/扫描/准入；本批 `run_input` 只由服务端写。
- **浏览器 DOM**：未跑（无 headless browser）。
- **全量 pytest**：按 PLAN §1 未跑（§3.5）。
- **真实 worker 镜像**：占位 digest（继承 T025）。
- **application-kind 评测真实 runner**：F13A；本票只补齐 FK/行形状（并适配 T026 测试）。

## 6. F03 / F02B 可依赖的接口与表

F03（执行切片）可直接依赖：

- 表：`platform.execution_snapshots(id, kind, application_id, draft_revision, owner_id, manifest,
  manifest_sha256, manifest_artifact_id, compiler_version, worker_image_digest,
  state_schema_version)`；`platform.runs(id, owner_id, kind, execution_snapshot_id,
  manifest_sha256, input_artifact_id, checkpoint_thread_id, state='queued', revision=1,
  next_event_seq=1, budget_limit, currency, phase='idle')`；`platform.jobs(id, run_id,
  kind='start', state='ready', available_at)`；`platform.run_artifacts(run_id, artifact_id,
  relation='input', attempt_token NULL)`。
- 事务保证：snapshot + run + job + 输入 artifact + 链接 + 幂等收据同事务；失败/拒绝零行。
- 端点/错误词汇：`debugDraft` 与 `getRun` 的契约 `Run` 投影；`X-Manifest-SHA256`；
  `idempotency_conflict`/`revision_conflict`/`validation_failed`（`details.errors` 定位）/
  `artifact_not_*`/`policy_budget_out_of_range`/`compiler_unconfigured`/`object_store_unavailable`。
- 代码入口：`catalog.ValidatedExecution`（T025）、`compiler.VerifyManifestBytes`、迁移注释里
  点名的两个 F03 外键。

F02B（发布）可依赖：`platform.manifest_artifacts` 的内容寻址 artifact（同一 hash 不重写字节）、
`execution_snapshots` 的 published 分支（契约 CHECK 已就位）、`X-Manifest-SHA256` 的同一 hash
来源；发布评审必须绑定同一 snapshot/manifest hash（F02B 不重算）。

## 7. 文件与提交

- `db/migrations/0014_execution_snapshots_and_runs.sql`
- `internal/catalog/{debug.go,debug_test.go}`、`internal/api/{debug.go,server.go}`
- `apps/web/src/{debug.html,assets/debug.js}`、
  `apps/web/src/{editor.html,assets/editor.js,apps.html}`
- `tests/support/{t027.py,fixtures.py}`
- `tests/f02a/test_t027_{migration,debug,immutability,authz,refusals,idempotency,pages}.py`
- `tests/f02a/{test_t023_invalid_draft.py,test_t023_migration.py,test_t025_migration.py}`、
  `tests/f00b/test_t026_{migration,enqueue,failures}.py`（schema 收紧后的预期适配）
- `reports/f02a/T027-debug-submit.md`、`PROGRESS.md`、
  `docs/adr/0004-f00b-f02a-boundaries.md` §38–44、
  `reports/f02a/T023-drafts-and-editor.md`（AC3 闭环补记）

## 8. 规格变更

无。`product/docs/spec/**` 与 `product/contracts/**` 未修改；`sync_spec --check` no drift。
发现的契约缺口（Run 无 hash 字段/无 getSnapshot、idempotency 无响应体列、预算无上限）记录在
本报告 §2 与 ADR §38/§40/§42，由后续契约修订票处理。
