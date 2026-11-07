# F12/T093 从外部客户端验证全量 REST/SSE 共同契约（GitHub #94）

**状态：`partial`（判定手段 `passed`；完整首版门禁裁决 `route_only`）**——2026-11-07 复核修复后，
门禁把「路由存在」和「功能路径被真实调用」分开计数：**route-registered 106/106、
functional probe 60/106、route_only 46**、跨主体内容隔离 18 条实测、0 授权失败、0 不变量失败、
0 错误。报告不再声称 106/106 operation 已实现；46 条 route_only 的功能路径未验证，见 §5.1/§5.5。
唯一 `not_run` 是 SSE 410 保留窗口分支，见 §5.3/§7。

需要明确区分两句话，避免把“手段通过”读成“首版 API 已全绿”或反过来：

| 口径 | 结论 | 证据 |
|---|---|---|
| **门禁手段本身通过**（本票交付物） | `passed` | `tests/f12` 全绿；CLI 可重放并在独立 pristine 栈上复跑出同一裁决；变异（105 条被拒、107 条注入被判 missing）都以非零退出码收口 |
| **完整首版 API 门禁裁决** | **`route_only`（route-registered 106/106；functional 60/106）** | `reports/f12/T093-api-conformance.json`：`summary.verdict=route_only`、`implemented=60`、`route_only=46`、`missing_operations=[]`、`probe_failed=[]`、`authorization_failed=[]`、`invariant_failures=[]`、`errors=[]` |

- 分支：`t/t093-api-conformance`；worktree `E:\Orchara-wt\t093`；基线 `integration/f12` @ `1cdff9c`；
  结束前 merge `integration/f12`（结果见 §8）。
- 范围：`product/tools/f12_api_gate.py`（本票新建的外部客户端门禁 CLI）、`product/tests/f12/`（6 个测试文件、
  242 个用例）、`product/reports/f12/`（机器证据 + 本报告）；服务端为让门禁裁决成立而修的 5 处契约缺陷 +
  门禁自身 5 处判定缺陷（missing 分类、形状采样顺序、检查计数语义、UUID 误判、CLI 复跑污染；§3）。
- **不改** `product/docs/spec/**`、`product/contracts/**`、迁移；没有新增契约字段、没有改 operation 数量。
- 承接说明：本票上一轮实现者在 ~95% 处被进程终止。本轮**保留**其全部工作（门禁 CLI、5 个服务端修复、
  测试套件），只做收尾：复现并修掉 3 条测试失败、修门禁的 missing 分类与形状不变量（含新暴露的 UUID
  误判）、删除调试残留、补报告/PROGRESS/摘要、跑全量门禁与 7 个受影响既有套件、提交并合并。

## 1. 验收对照（issue #94）

| 票面验收项 | 结论 | 证据 |
|---|---|---|
| 106 项均有实现与授权测试记录 | **partial（route-registered 106/106；functional 60/106）** | 门禁从 `contracts/openapi.json` 读 operation 清单（不手写 106 行），逐条记录 `route_registered / runtime_reached / functional_probe / implementation_status / implemented / authorization_ok / probes / normal_path / cross_principal_isolation / verdict`：4 条 public、23 条 internal（真实 mTLS 证书角色）、79 条 session/session_only。每条都有 `unauthenticated`（401 或证书边界）、`wrong_principal`（403/404/200）或 `wrong_internal_role`（403）探针；**到达 handler 不再等于 implemented**：只有该 operation 自己的 normal-path 探针返回契约声明的成功状态，`implemented` 才为 true。其余 46 条标记 `route_only`（路由已注册、功能路径未验证），在 `summary.route_only_operations` 与 §5.1 按 epic 列出。跨主体内容隔离对 18 个 list/get operation 真实断言“另一主体看不到 creator 的资源 id”；13 条无夹具的读操作明确 `not_run` 并写原因。另有 106×3 个 parametrized pytest 用例逐 operation 断言分类、隔离事实与记录存在且不是 skip。见 `reports/f12/T093-api-conformance.json` 与 `tests/f12/test_operations.py`。 |
| 没有默认通用 CRUD 兜底 | **passed** | 运行时：未知路径 404 契约封套、真实路径错误方法 405（保留 `Allow`）、随机 UUID 资源 404 且不可枚举；静态：`scan_route_registry()` 解析 `server.go`/`internal.go`/`main.go` 的 199 条字面注册，106 条契约 operation 全部有 `method+path` 字面注册，`generic_patterns=[]`（没有首段通配的通用资源路由）、`missing_registrations=[]`。`tests/f12/test_invariants.py::test_no_generic_crud_fallback`。 |
| 未实现端点使完整首版验收失败 | **passed** | 变异测试把不存在的 `/api/v1/f12-missing-operation-<hex>` 注入契约成 107 条：门禁以**字面路由注册表**判定该 operation `implementation_status=missing / route_registered=false`，进入 `summary.missing_operations`，`verdict=failed`，CLI 退出码 1 并打印 `MISSING OPERATION: f12InjectedMissingOperation`；同一次运行里真实 106 条仍是 route-registered 106，其中 60 条 functional、46 条 route_only。第二个变异证明「已注册但只有匿名 401」的 operation（`getVersion`）不会被算成 implemented（`test_a_registered_but_always_401_operation_is_not_counted_as_implemented`）。另一变异删掉 1 条 operation → 清单数量校验拒绝运行（退出码 2，不产出报告）。`tests/f12/test_gate_mutations.py`、`tests/f12/test_manifest.py`。 |
| 端到端真实入口 | **passed** | 入口就是 `python tools/f12_api_gate.py --base-url ... --internal-* ... --contract ... --out ...`：真实 HTTP/HTTPS 套接字打到独立编译的 `cmd/api`（真实 PG 16 隔离库 + 真实 mTLS internal listener + 真实对象目录），不 import handler、不进程内调用。测试里既用 session fixture 跑同一个 `Gate`，也有一条用例**直接起 CLI 子进程**复跑未修改的 106 条契约并比对裁决（`test_the_gate_cli_reproduces_the_session_verdict`）。 |
| 版本、命令与原始结果 | **passed** | 见 §7：Go/Python/Node/PG 版本、真实命令、退出码与原始 tail；`gofmt`、`go build/vet/test`、`sync_spec --check` 全绿。 |

## 2. 判定手段（`product/tools/f12_api_gate.py`）

一个可重放的“裁判”，不是产品功能：

- **清单来自契约**：`load_operations()` 读 `contracts/openapi.json`；operationId 重复、数量不等于
  `DEFAULT_EXPECTED_OPERATIONS=106`（可用 `--expected-operations` 显式覆盖，用于变异测试）都在启动前
  `ManifestError` 拒绝（退出码 2），**不产出报告**——静默少跑一条不可能变绿。
- **逐 operation 三面探针 + 功能探针分离**：public（liveness/readiness/login/activate 的失败路径）、
  session（匿名 401 + 另一主体 403/404/200）、internal（无客户端证书 TLS 边界 + 正确角色 + 错误角色 403）；
  能通过公开 API 真实构造的资源另外跑 normal path（创建/读取/更新/取消/领取/心跳/结算）。
  `functional_probe` 只取该 operation 自己的 normal-path 结果；没有 normal path 的 operation 是
  `route_only`，不计入 `implemented`（T093 复核 G）。
- **跨主体内容隔离**：对 18 个有夹具的 list/get operation，用 plain member 调同一路由并断言 creator 的
  资源 id 不在响应里（列表）或详情读是 403/404；记录 `cross_principal_isolation` 字段（member 状态、
  items 数、foreign_id_visible、理由）。13 条无夹具读操作明确 `not_run`，不拿状态码冒充隔离（复核 H）。
- **10 条跨端点不变量**：`error_envelope`、`pagination`、`etag_if_match`、`idempotency`、`sse_frames`、
  `uuid_server_generated`、`utc_rfc3339`、`decimal_strings`、`no_store`、`no_generic_crud`。
- **路由注册表静态扫描**：`scan_route_registry()` 把三个 mux 文件的 `HandleFunc("METHOD /path")` 解析成
  集合，与契约 106 条逐条比对；同时拒绝首段通配/尾随通配的通用 CRUD 路由。**本轮把“是否存在该端点”的
  权威从运行时探测改成该注册表**，原因见 §3.6：`ContractErrors` 把 mux 404 规范成契约封套后，运行时
  无法再区分“路由不存在”和“handler 查不到资源”。
- **按 epic 分组 + 机器可读证据**：`--out` 写 JSON（contract sha256、环境、fixture、106 条逐 operation
  记录、10 条不变量、summary/by_epic/verdict）；`F12_WRITE_EVIDENCE=1` 时 pytest session fixture 把同一
  份结构写到 `product/reports/f12/T093-api-conformance.json`。
- **退出码**：0=verdict passed（全部 106 条都有 functional probe）；3=verdict route_only（无失败，但
  功能覆盖不完整，是本次复核后的诚实裁决）；1=有 missing/probe_failed/授权失败/不变量失败/跨主体失败/错误；
  2=契约清单本身不合法。

## 3. 审计发现并修复（含“修前行为 → 修后行为 → 覆盖测试”）

### 3.1 mux 自己的 404/405 是纯文本，不符合统一错误封套

- **修前**：`net/http` 对未匹配路径/方法回 `404 page not found` / `405 method not allowed` 纯文本，
  没有 `{error:{code,message,request_id}}`，外部客户端拿不到 `request_id`。
- **修后**：`internal/api/errors.go:42` 新增 `ContractErrors` 中间件，只在 status 是 404/405 **且响应还不是
  JSON** 时介入，改写为契约封套；保留 mux 的 `Allow` 头，删掉会撒谎的 `Content-Length`；handler 自己的
  `writeError`（已带 `application/json`）原样通过；实现 `Flush` 让 SSE 继续工作。内部 mux 在最外层套上
  （`cmd/api/main.go:246`）。公开 mux 由 2026-11-07 复核改为 `ContractErrorsFor([]string{"/api/"}, mux)`
  （`cmd/api/main.go:221`、`internal/api/errors.go:48`）：页面不是 API 契约面，未知页面保留 net/http 的
  普通 404/405；只有 `/api/` 前缀得到契约封套。Go 单测
  `internal/api/errors_test.go::TestContractErrorsForOnlyWrapsAPIPaths` 同时断言
  `GET /no-such-page` 仍是 text/plain、`GET /api/v1/no-such` 是 JSON 封套。
- **覆盖**：`tests/f12/test_invariants.py::test_error_envelope_including_the_mux_404_and_405`
  （未知路径 404 `not_found`、未知方法 405 `method_not_allowed`，两者 `envelope_ok=true`）；
  门禁 `error_envelope` 不变量扫描**每一个**错误响应（本次 202 条），不再只抽查两条。

### 3.2 `mux.HandleFunc("/")` catch-all 把真实 API 的方法不匹配变成自己的 404

- **修前**：根路径用 catch-all 注册；对已注册路径发错方法时，method-specific pattern 不匹配，catch-all
  抢先接住并 `http.NotFound`，于是“方法不允许”被答成 404，且无法保留 `Allow`。
- **修后**：`cmd/api/main.go:215` 改为 Go 1.22 精确模式 `GET /{$}`（只匹配根路径 GET）。其余路径交给
  mux 自己产生 404/405，再由 `ContractErrors` 规范化。页面路由仍是显式注册，未改任何页面。
- **覆盖**：同上 `test_error_envelope...`（`PATCH /api/v1/applications` 得 405 + `Allow`）、
  `test_no_generic_crud_fallback` 的 runtime 分支；回归侧 `tests/f01` 页面用例、`tests/f02a` 页面/草稿用例
  仍按 §6 全量通过。

### 3.3 审批列表 `limit` 用本地解析、越界被服务层静默 clamp

- **修前**：`handleListApprovals` 自己解析 `limit`（只拒非整数/0），`limit>100` 交给 `approvals.Service.List`
  静默截到 100，外部客户端拿到 200 却少数据；其它列表端点早已统一 `catalog.ParseLimit`。
- **修后**：`internal/api/approvals.go:165` 改用 `catalog.ParseLimit`；`0/101/abc` 都是 422
  `validation_failed` + `details.field="limit"`；删除本地 `parseQueryInt`/`errNotAnInteger`。
- **覆盖**：`tests/f12/test_invariants.py::test_pagination_is_uniform_and_never_clamps` 对全部 14 个
  分页 operation（含 `listApprovals`）逐一断言 `limit=101/0/abc → 422` 且 `field=limit`；本轮 §6 的
  `tests/f06` 全量回归也为绿（无既有用例依赖 clamp）。

### 3.4 审批服务层也静默 clamp（内部调用同样会拿到截断页）

- **修前**：`internal/approvals/query.go` 在 `limit > MaxListLimit` 时直接 `limit = MaxListLimit`。
- **修后**：返回 `422 validation_failed`（“no silent clamp”）；HTTP 层先行拒绝，服务层是纵深防御，内部
  调用者也不会拿到悄悄截断的页。
- **覆盖**：HTTP 层由 3.3 的分页不变量证明；服务层这条分支当前没有独立 HTTP 入口（处理器已先返回 422），
  故**只由同一不变量间接覆盖**，在 §4 记为“防御性分支、无独立用例”。

### 3.5 执行租约时间戳用 `::text`，带 DB 会话本地时区，既非 UTC 也非 RFC3339

- **修前**：`internal/execution/store.go` 的 claim/heartbeat `RETURNING ...::text` 产出
  `2026-11-06 17:39:05.123456+08` 形状（本机 DB 时区 +08），违反 API.md §1“UTC RFC3339”。
- **修后**：新增 `contractTimestamp(column)`（`store.go:313`）：
  `to_char(column AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`；`lease_deadline`、`heartbeat_at`、
  `started_at` 三个出现在契约响应/头里的值全部改走它（`store.go:336-338`、`:465`）。
- **覆盖**：门禁 `utc_rfc3339` 不变量（本轮修好采样顺序后真实扫到 N 个时间字段，见 §5）；
  `tests/f03` 的 claim/heartbeat 用例用 `fromisoformat` 解析并与 DB 值比 epoch，本轮 `tests/f03`
  **201 passed**（失败 2 条是既有 fixture 的 FK 漂移，见 §6.2，与本改动无关）。
- **影响面判断（orchestrator 要求）**：精度从“本地时区、秒 + 毫秒文本”变为“UTC、6 位微秒”；所有消费方
  都是按时间解析或按同一格式字符串比较（f00b 的 `fromisoformat`、f03 的 epoch/`X-Run-Lease-Deadline`），
  没有按固定长度/固定偏移匹配的断言。已用 `tests/f00b`、`tests/f03` 真实栈回归确认（§6）。

### 3.6 门禁自身：形状不变量在 normal path 之前运行，且 `*_checked` 统计的是失败数而非检查数

- **修前**（上一轮留下的真实缺陷，两处）：
  1. `run()` 顺序是 `..._inv_shapes(); _run_normal_probes()`。形状不变量只在 fixture/探针阶段攒下来的
     成功响应里找 `id`/`*_at`/金额字段，真实 Run/Lease/Artifact/目录等 normal response 还没产生，
     覆盖不完整。
  2. `_inv_shapes` 把 `scan_*_fields()` 返回的**失败条数**累加进 `id_fields_checked` /
     `timestamp_fields_checked` / `money_fields_checked`；于是 `0` 只代表“没有失败”，**不代表“检查过
     字段”**。配合第 1 点，证据很容易读成“检查过 0 个字段却 passed”。
- **修后**：
  1. `_inv_shapes()` 移到 `_run_normal_probes()` 之后；
  2. 三个扫描函数改为返回 `(检查字段数, 失败列表)`，观测计数改成真实检查数；
  3. `test_uuid_utc_decimal_and_no_store` 增加断言：UUID / 时间 / 金额三类检查数都必须 **> 0**，
     “零字段的 checked” 不再允许。
- **效果**：同一份证据现在真实扫到 `id_fields_checked=123`、`timestamp_fields_checked=22`、
  `money_fields_checked=4`、`json_responses_checked=389`；并且**立刻暴露了下一条**（契约允许的
  配置目录 string id 被 UUID 不变量误判）。
- **覆盖**：`tests/f12/test_invariants.py::test_uuid_utc_decimal_and_no_store`；`tests/f03` 的
  claim/heartbeat 真实栈用例同时证明 `lease_deadline` 的 UTC 形状（§3.5）。

### 3.7 门禁自身：UUID 不变量把契约声明为 `string` 的配置目录 id 误判为违约

- **修前**（3.6 修好采样后暴露）：`uuid_server_generated` 对**所有**名为 `id` 的字段要求 UUID；但
  `listCredentialRefs` 的 `items[].id` 与 `listModelEndpoints` 的 `items[].id` 在
  `contracts/openapi.json` 里明确是 `{"type": "string"}`（部署者键，dev-stub 下是
  `stub-credential`/`stub-endpoint`）。门禁把契约允许的值判成产品违约，会让完整首版裁决变成假红。
- **修后**：`load_operations()` 从每个 operation 的 2xx 成功 schema 推导
  `non_uuid_id_fields`（只认 `type: string` 且无 `format: uuid` 的 `id` 属性），`_inv_shapes` 按
  probe label 精确豁免；**不是**按观察值白名单，也不是手写 operation 清单。当前契约里只有上述两条
  命中（单测锁定）。产品代码与契约均未改。
- **覆盖**：`tests/f12/test_manifest.py::test_only_the_contract_declared_string_ids_are_exempt_from_the_uuid_rule`
  （断言豁免集合恰为这两条）；`tests/f12/test_invariants.py::test_uuid_utc_decimal_and_no_store`
  （125 个 id 字段 checked、0 失败）；CLI 独立复跑得到同一结果（§7.3）。

### 3.8 测试/分类缺陷（3 条红 → 绿，均不是产品放宽）

| # | 失败 | 根因 | 修法 |
|---|---|---|---|
| 1 | `test_an_unauthenticated_caller_gets_the_contract_401_for_every_family` | 对 `GET /api/v1/admin/users` 探测，但该路径只注册 `POST`，得到 mux 405 而非 401 | 按真实注册方法探测（`admin/users` 用 POST）；断言仍是 401 + `unauthenticated/invalid_key`，没有把 405 当 401 掩盖 |
| 2 | `test_the_gate_fails_on_an_injected_unimplemented_operation` | 注入路由的 404 被 `ContractErrors` 规范成契约封套后，运行时探测无法区分“路由不存在”与“资源不存在”，被归到 `authorization_failed` | 门禁改为以 `scan_route_registry()` 的字面注册表判定“是否存在”，运行时只判定授权/形状；新增 `route_registered` 证据与单测。真实 106 条仍 route-registered 106，其中 60 functional / 46 route_only；注入的 1 条落进 `missing_operations` 且退出码 1。复核另加「已注册但只有匿名 401」变异：`getVersion` 必须是 route_only，不是 implemented |
| 3 | `test_sse_frames_carry_id_event_and_the_event_schema_envelope` | 断言 `content_type == "text/event-stream"`，实际 `text/event-stream; charset=utf-8`（合法参数） | 按 media type 解析（分号前、去空白、小写）比较；保留“必须是 SSE”的语义 |

## 4. 审计发现但未修（如实记录，归属其它 epic / 后续票）

| # | 发现 | 证据位置 | 归属 epic | 为什么本票不修 |
|---|---|---|---|---|
| 1 | `artifact.ready`、`usage.updated` 两种持久事件**没有 emitter** | 契约枚举：`contracts/event.schema.json:29-30`；实现只有 `run.status`/`node.status`/`approval.required`（`internal/execution/events.go:41,43,144`；`grep -r artifact.ready internal/` 无命中）；T086 报告已记 `usage.updated` 无 emitter | F03/T032（事件）与 F05（artifact）/F06（usage） | 属功能缺口，不是契约判定缺陷；本票只记录，不实现 F12 之外的功能 |
| 2 | 缺少 `Idempotency-Key` 时 `details` 有**两种形状**：`{"header":"Idempotency-Key"}`（8 条）与 `{"field":"Idempotency-Key"}`（3 条：`decideApproval`、`decideMemoryCandidate`、`reconcileOperation`） | 证据 JSON `invariants.idempotency.observations[].missing_key_details_shapes`；代码：`internal/api/debug.go:104`、`models.go:200`、`publication.go:129`、`requests.go:267`、`evaluation_submit.go:97` vs `approvals.go:211`、`memory.go:317`、`reconcile.go:47` | F06/F08/F09（各 handler 的写路径） | 两种形状都“定位到了同一个 header”，契约未把 `details` 内部键钉死；统一属于跨 epic 的 API 形状收敛，不在本票范围。门禁按“标识到 header”判定并在证据里显式记录两种形状 |
| 3 | 所有 JSON 响应（含成功读取）都带 `Cache-Control: no-store`，使 HTTP 协商缓存（ETag + `If-None-Match` 条件 GET）实际不可用 | `internal/api/errors.go:97-99`（`writeJSON` 统一 no-store）；证据 JSON `invariants.no_store.observations[0].json_responses_checked=389`（全部 no-store） | F01/API 响应头策略（`docs/spec/API.md` 只要求草稿/记忆有 ETag；未要求可缓存） | `no-store` 是既有全局安全策略（会话/密钥/审批数据），本票不能为缓存收益擅自放宽；ETag 仍作为**写侧乐观并发**校验有效（428/412 已由 `etag_if_match` 证明）。记录为“协商缓存不可用”的设计后果 |
| 4 | 非契约路由：注册表 199 条去重字面模式 vs 契约 106 条 operation，另有 93 条非契约模式（90 带方法 + 3 运维别名） | 见 §5.4 清单（由 `scan_route_registry` 同一正则 + 契约 diff 得到） | 各 epics（F02A/F05/F06/F07/F08/F10/F11/F13A 的页面/内部/扩展路由） | 这些是**有意的**非契约面（页面、签名链接、内部 mTLS、评测进度等），ENV §8 已把它列为已知边界；本票只验证“契约内 106 条都在”，不把非契约面写进契约（也不得改 `contracts/**`） |
| 5 | 审批服务层 3.4 的 422 防御分支没有独立用例（HTTP 层先拒绝） | `internal/approvals/query.go:54-58` | F06 | 只能通过绕过 HTTP 的直接 service 调用触发；现有测试均为 HTTP 黑盒。记录为覆盖缺口 |
| 6 | `GET /api/v1/runs/{run_id}/capabilities` 同时存在**公开 mux 版本**（`server.go:476`）与契约内 internal 版本；公开版本不在 OpenAPI | §5.4 清单第 7 条 | F03/F06 | 同上，非契约扩展面；本票不删不改 |

## 5. 机器可读证据

- 路径：`product/reports/f12/T093-api-conformance.json`（`F12_WRITE_EVIDENCE=1` 时由 session fixture 写出；
  CLI 的 `--out` 是同一 `_report()` 结构，`test_the_gate_cli_reproduces_the_session_verdict` 已用真实 CLI
  复跑并比对 verdict/by_epic/not_run）。
- `generated_at`、`environment.requests`、契约 sha256：见证据文件头；契约
  `sha256=bf191b9697f99b362bd61c287964724637e1281153232699aacf582157fc3e91`（93 paths / 106 operations）。

### 5.1 按 epic 分组（106 条；implemented = 有 functional probe）

| epic | operations | functional | route_only | failed |
|---|---:|---:|---:|---:|
| F00/D00 | 2 | 2 | 0 | 0 |
| F00B | 9 | 1 | 8 | 0 |
| F01 | 12 | 9 | 3 | 0 |
| F02A | 8 | 8 | 0 | 0 |
| F02B | 6 | 1 | 5 | 0 |
| F03 | 10 | 8 | 2 | 0 |
| F03/F04A | 5 | 0 | 5 | 0 |
| F05 | 10 | 4 | 6 | 0 |
| F06 | 13 | 7 | 6 | 0 |
| F07 | 1 | 0 | 1 | 0 |
| F08 | 9 | 8 | 1 | 0 |
| F09 | 8 | 6 | 2 | 0 |
| F10 | 7 | 4 | 3 | 0 |
| F11 | 3 | 2 | 1 | 0 |
| F13A | 3 | 0 | 3 | 0 |
| **合计** | **106** | **60** | **46** | **0** |

### 5.2 不变量（10 条全 passed）

| 不变量 | 观测 |
|---|---|
| error_envelope | 未知路径 404 `not_found`、未知方法 405 `method_not_allowed` + `Allow`；本次扫描 202 条错误响应，封套全合法 |
| pagination | 14 个分页端点；`limit=1` 有 items/next_cursor；`101/0/abc → 422 + field=limit`；`listMemories` 游标往返 |
| etag_if_match | draft/memory：缺 `If-Match` 428、旧版本 412；`cancelRun` 202 且不要求 ETag |
| idempotency | `debugDraft` 重放同 run_id、同 key 异 body 409；11 个必填 header 的 operation 缺头均 422/400 且定位 |
| sse_frames | `id/event/data` 帧、`event.schema.json` 校验、Last-Event-ID 重放不倒退；410 分支 `not_run` |
| uuid_server_generated | **125** 个 id 字段均满足契约（服务端资源 UUID；两个契约声明为 string 的配置目录 id 按 schema 豁免） |
| utc_rfc3339 | **23** 个时间字段均为 UTC RFC3339（含执行租约 `lease_deadline` 的微秒精度） |
| decimal_strings | **4** 个金额字段均为十进制字符串，无二进制浮点 |
| no_store | **389** 个 JSON 响应全部 `Cache-Control: no-store` |
| no_generic_crud | 运行时 404/405/随机资源 404；静态 106 条字面注册、无通配 |

> 这些计数是**实际检查的字段数**（本轮修正了计数器语义，见 §3.6），原始值见证据 JSON 的
> `invariants.*.observations[0]` 与 §7.2 的 tail。

### 5.3 `not_run`

- `sse_frames` 的 **410 `event_cursor_expired` + `snapshot_url`** 分支：需要“比保留窗口更老的事件”。
  开发栈默认 `ORCHARA_RUN_EVENT_RETENTION=24h`（`internal/config/config.go:197`），本票没有压低它（压低会
  改变 T032 的保留语义且影响其它套件）；该分支归 **T096** 用低保留窗口的栈验证。
- 其余“未跑”只有本机环境能力：真实厂商模型/凭据（本票不涉及模型调用，profile 固定 `dev-stub`）。
  浏览器 DOM/Playwright 与本票无关（本票全部是 HTTP/SSE 外部客户端）。

### 5.4 非契约路由清单（审计证据，未写入契约）

由 `scan_route_registry` 同一正则 + 契约 diff 得到：三个 mux 共 **200 次注册、去重后 199 条字面模式**，
契约 106 条，**非契约 93 条**（90 条带方法 + 3 条 method-less 运维别名；`/health/live` 在外部/内部两个
mux 各注册一次但属契约路径，去重后不重复计数）。分类：

- **页面与静态资源（45，含根 `GET /{$}`）**：`GET /login|/admin|/activate|/apps|/attachments|/tools|/models|/reviews|/runs|
  /personal-data|/chat|/catalog|/approvals|/knowledge|/memories`、`/apps/{id}/...`、`/reviews/{id}`、
  `/evaluations/{id}`、`/assets/*.js|css`。
- **公开业务扩展（26）**：`GET /api/v1/versions/{id}/runnable`、`GET /api/v1/catalog/search`、
  `POST /api/v1/applications/{id}/publication-preview`、`POST /api/v1/templates/{id}/applications`、
  `GET /api/v1/runs/{id}/children|node-results|capabilities|model-preview`、
  `GET /api/v1/evaluation-reports/{id}/cases`、`GET /api/v1/evaluations/{id}/progress`、
  `GET /api/v1/admin/evaluations/{id}/attribution`、`POST /api/v1/tool-runs`、`GET /api/v1/tool-runs/{id}`、
  `POST /api/v1/tools/{id}/revoke`、`GET /api/v1/tools/{id}/build`、`/api/v1/admin/connectors*`（3）、
  `GET /api/v1/knowledge-documents/{id}/diagnostics`、`POST .../reindex`、
  `POST /api/v1/knowledge-bases/{id}/retrieval-queries|answer`、`GET /api/v1/knowledge-citations/{id}`、
  `GET /api/v1/diagnostic-grants`、`GET /api/v1/diagnostic-grants/{id}/content`、
  `POST /api/v1/conversations/{id}/handoff-preview`。
- **内部 mTLS 扩展（17）**：`/internal/v1/background/jobs/{id}/{route,evaluation,tool-build,document-ingest,
  evaluation-runs,evaluation-progress,...}`、`/internal/v1/runs/{id}/{children/{child}/result,waiting-resolution,
  unknown-operations,node-results,retrieval,capability-results,node-approvals,capability-settlements}`。
- **签名对象链接（2）**：`PUT|GET /object-links/{token}`（短期签名 URL，不属于 JSON API）。
- **运维别名（3，method-less）**：`/healthz`、`/readyz`、`/api/status`。

### 5.5 `route_only` 清单与跨主体隔离口径（2026-11-07 复核）

**route_only = 路由已注册、运行时可达，但没有 functional normal-path 探针**，因此不计入
`implemented`。本机 dev-stub 栈能真实构造 60 条；其余 46 条的 normal path 需要尚不存在的夹具
（发布版本、审批、评测报告、后台 job、ingress 文档等），如实列在这里而不是用“路由存在”冒充：

`activateUser`、`completeBackgroundJob`、`completeBackgroundUpload`、`completeWorkerUpload`、
`confirmAttemptExit`、`createBackgroundDownload`、`createBackgroundUpload`、`createWorkerDownload`、
`createWorkerUpload`、`decideApproval`、`decideMemoryCandidate`、`deleteArtifact`、`deleteDocument`、
`downloadArtifact`、`evaluateDraft`、`getApproval`、`getBackgroundArtifactStatus`、`getBackgroundJob`、
`getEvaluation`、`getEvaluationReport`、`getKnowledgeDocument`、`getPublicationReview`、`getTool`、
`getVersion`、`getWorkerArtifactStatus`、`grantDiagnostics`、`heartbeatBackgroundJob`、`ingestDocument`、
`invokeBackgroundModel`、`invokeCapability`、`prepareApproval`、`proposeMemoryCandidate`、
`readArtifactContent`、`reconcileOperation`、`registerRunArtifact`、`replyInteraction`、
`reviewPublication`、`reviewTool`、`revokeVersion`、`runEvents`、`setUserRoles`、`spawnChild`、
`streamModelCall`、`submitApplication`、`submitTool`、`updatePrincipal`。

跨主体内容隔离（`cross_principal_isolation`）：**18 条实测**——`listApplications`、`listRuns`、
`listConversations`、`listMemories`、`listKnowledgeBases`、`getApplication`、`getDraft`、
`listVersions`、`getRun`、`listRunArtifacts`、`getConversation`、`listMessages`、`getMemory`、
`getKnowledgeBase`、`listKnowledgeDocuments`、`getArtifact`、`readArtifactContent`、
`downloadArtifact`。每条记录 member 的状态、items 数、`foreign_id_visible` 与 `isolation_ok`；
13 条无夹具读操作（`getVersion`、`getTool`、`getApproval`、`getRequest`、`getPublicationReview`、
`getEvaluation`、`getEvaluationReport`、`getKnowledgeDocument`、`getBackgroundJob`、`getDeletion`、
`listApprovals`、`listMemoryCandidates`、`runEvents`）明确记 `not_run` 并写原因。报告不再说
“每条记录都有 403/404”：无路径参数的自助 list 的 wrong_principal 本来就是 200，隔离由内容断言证明。

### 5.6 门禁的已声明边界（不隐瞒）

1. **normal-path 覆盖 60/106**：不是 106 条都有功能探针；46 条 route_only 已在 §5.5 列出。
2. **实现判定依赖源码正则**：`implemented` 的路由存在性来自 `scan_route_registry()` 对
   `server.go`/`internal.go`/`main.go` 的 `HandleFunc("METHOD /path")` 扫描；不是编译产物或运行时注册表。
   读不到源码时 fail-closed（全部 missing），生产只发布二进制时必须以源码快照运行。
3. **夹具有限**：没有发布版本、审批、评测报告、后台 job 的通用构造器，因此这些 functional probe 缺失；
   这是门禁边界，不是“已验证通过”。
4. **跨主体隔离 18/31 个读操作**：其余 13 条 not_run 的原因是“没有第二个主体的资源夹具”，不是“已隔离”。

## 6. 回归影响面（4 处全局改动的既有套件结果）

> 所有真实栈套件串行执行，跑前占 `E:\Orchara\.scratch\f12\locks\realstack`，结束删除。原始日志在
> `E:\Orchara\.scratch\f12\t093\regression-*.log`。

| 改动 | 受影响套件 | 真实结果 |
|---|---|---|
| `ContractErrors` 套外部+内部 mux | f01（页面/身份/错误形状）、f00（readiness 页面）、f02a（页面）、acceptance | 见 §6.1 |
| `/` → `GET /{$}` | f01 页面、f00 readiness、f02a/f03 页面 | 见 §6.1 |
| `contractTimestamp`（lease/heartbeat/started_at 形状） | f03（claim/租约/终态/finalizer）、f00b（租约/后台任务） | 见 §6.1/§6.2 |
| 审批 `limit` 不再 clamp、越界 422 + `details.field` | f06（审批） | 见 §6.1 |

### 6.1 本轮真实结果

| 套件 | 命令 | 真实结果 | 与本票改动的关系 |
|---|---|---|---|
| `tests/f00b`（后台任务、租约、mTLS、维护池） | `python -m pytest tests/f00b -q` | **133 passed, 2 failed in 752.79s**；2 条见 §6.2，与本票无关 | 覆盖 3.5 的 `contractTimestamp` 邻接（f00b 自己的后台 job lease 走 `job_row`，未受改） |
| `tests/f03`（claim/租约/终态/finalizer） | `python -m pytest tests/f03 -q` | **201 passed, 2 failed in 1405.48s**；2 条见 §6.2，与本票无关 | **直接覆盖 3.5**：claim/heartbeat/`X-Run-Lease-Deadline` 全部按 UTC 解析并与 DB 比 epoch，通过 |
| `tests/f06`（审批，含 limit 语义） | `python -m pytest tests/f06 -q` | **173 passed in 792.15s** | **直接覆盖 3.3/3.4**：没有任何既有用例依赖审批 `limit` 的静默 clamp |
| `tests/f02a`（页面 serving / 草稿 ETag） | `python -m pytest tests/f02a -q` | **102 passed, 2 failed in 682.51s**；2 条见 §6.2，与本票无关 | 覆盖 3.1/3.2 的页面与 404 形状（`/admin.html`、`/assets/` 仍 404，`GET /` 仍 200） |
| `tests/f07`（子运行/等待容量/租约断言） | `python -m pytest tests/f07 -q` | **36 passed in 302.05s** | 覆盖租约/子槽邻接；未回归 |
| `tests/acceptance`（跨模块冒烟：页面 + 健康检查） | `python -m pytest tests/acceptance -q` | **13 passed in 38.60s** | **直接覆盖 3.1/3.2**：真实 `cmd/api` 的 `GET /`、`/health/*`、`/api/status` 全部 200 |
| `tests/f01`（身份/会话/页面；必须单跑避开已知 collection 冲突） | `python -m pytest tests/f01 -q` | **155 passed in 722.68s** | **直接覆盖 3.1/3.2 的页面与错误形状**（`GET /`、命名页面、未知页面 404） |

六条既有红全部在 **detached baseline worktree `E:\Orchara-wt\t093-baseline`（`1cdff9c`，不含本票任何改动）
逐条真实复跑并同样失败**（日志 `E:\Orchara\.scratch\f12\t093\baseline-*.log`）——因此它们不是本票引入的
回归，而是集成分支上更早的 epic 与旧夹具之间的漂移。

### 6.2 既有红：6 条既有用例与本票改动无关（跨 epic fixture/迁移漂移，baseline 可复现）

| 失败用例 | 真实报错 | 归因 |
|---|---|---|
| `tests/f03/test_t029_invariants.py::test_one_active_top_level_run_per_conversation` | `runs_conversation_id_fkey`：fixture 直接 INSERT run 用的随机 conversation_id 不在 `platform.conversations` | F08/T074 的 `0044_conversations_and_requests.sql` 给 `runs.conversation_id` 加了 FK；f03 夹具仍按 FK 引入前的方式造数据 |
| `tests/f03/test_t035_migration.py::test_waiting_still_occupies_the_top_level_session_slot` | `runs_request_id_fkey`：随机 request_id 不在 `platform.requests` | 同一迁移（F08/T074） |
| `tests/f02a/test_t027_migration.py::test_the_five_tables_match_the_contract_baseline` | `runs` 多出列 `last_child_claim_at` | F07/T081 的 `0051_f07_waiting_capacity.sql` 新增父轮转列；f02a 的列基线未同步 |
| `tests/f02a/test_t027_refusals.py::test_unready_and_foreign_dependencies_are_refused` | 恢复子应用读权后仍 422 `io_mapping_unknown_field`（`/nodes/1/inputs/source`） | F07/T075（`337616c`）把 `call_app` 输入映射改为按冻结子 schema 校验；f02a 旧夹具的子版本 input_schema 未声明 `source` |
| `tests/f00b/test_t022_enqueue.py::test_the_maintenance_registry_refuses_uncompiled_or_unknown_kinds` | `route_request` 被 maintenance 接受（退出码 0，期望 2/no compiled handler） | F08/T079 把 `route_request` 注册为可执行 kind（`internal/background/job.go:43`、`20cafb6`/`ea404f7`）；f00b 用例仍假设它是“未知 kind” |
| `tests/f00b/test_t024_migration.py::test_the_foreign_keys_match_their_deferral_owners` | `platform_model_calls` 出现 `request_id` FK | F08/T079 迁移新增；f00b 的 deferral-owner 断言未同步 |

**基线复现（硬证据）**：在 `1cdff9c` 的 detached worktree 上逐条跑上述 6 个用例，结果同样是
**2 failed（f00b）+ 2 failed（f03）+ 2 failed（f02a）**，报错文本逐字相同（日志
`baseline-f00b-2b.log`、`baseline-f03-2.log`、`baseline-f02a-2.log`）。这些迁移/注册表提交
（`bb56fb0`、`ea404f7`、`2ddd7b5`、`337616c`、`20cafb6`）全部是本票 HEAD 的祖先，且本票 diff 不触碰
`db/migrations/**`、`internal/background/**`、`internal/catalog/delegation.go`。

本票**不**替其它 epic 改产品或夹具；按“既有红”记录，留给对应 epic 的后续票处理。**没有**用
`--ignore`、`-k` 或删用例掩盖：每个套件都是整套真实栈运行（f00b/f03/f02a/f06/f07/acceptance/f01）。

### 6.3 逐套件原始 tail（`E:\Orchara\.scratch\f12\t093\regression-*.log`，逐字）

```text
# regression-f00b.log
..............F......................................................... [ 53%]
.....F.........................................................          [100%]
FAILED tests/f00b/test_t022_enqueue.py::test_the_maintenance_registry_refuses_uncompiled_or_unknown_kinds
FAILED tests/f00b/test_t024_migration.py::test_the_foreign_keys_match_their_deferral_owners
2 failed, 133 passed in 752.79s (0:12:32)

# regression-f03.log
...................................F.................................... [ 35%]
......................................................F................. [ 70%]
...........................................................              [100%]
FAILED tests/f03/test_t029_invariants.py::test_one_active_top_level_run_per_conversation
FAILED tests/f03/test_t035_migration.py::test_waiting_still_occupies_the_top_level_session_slot
2 failed, 201 passed in 1405.48s (0:23:25)

# regression-f06.log
........................................................................ [ 41%]
........................................................................ [ 83%]
.............................                                            [100%]
173 passed in 792.15s (0:13:12)

# regression-f02a.log
........................................................................ [ 69%]
...........F...............F....                                         [100%]
FAILED tests/f02a/test_t027_migration.py::test_the_five_tables_match_the_contract_baseline
FAILED tests/f02a/test_t027_refusals.py::test_unready_and_foreign_dependencies_are_refused
2 failed, 102 passed in 682.51s (0:11:22)

# regression-f07.log
....................................                                     [100%]
36 passed in 302.05s (0:05:02)

# regression-acceptance.log
.............                                                            [100%]
13 passed in 38.60s

# regression-f01.log
........................................................................ [ 46%]
........................................................................ [ 92%]
...........                                                              [100%]
155 passed in 722.68s (0:12:02)
```

失败用例的完整 traceback 与 6 条 baseline 复现的逐字报错分别在
`regression-{f00b,f03,f02a}.log` 与 `baseline-{f00b-2b,f03-2,f02a-2}.log`。

## 7. 环境、版本、命令与原始输出

### 7.1 环境与版本

| 项 | 值 |
|---|---|
| profile | `ORCHARA_PROFILE=dev-stub`（无真实厂商模型/凭据/端点；本票不调用模型） |
| 数据库 | 真实 PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`；每个栈 `create_database` + 真实 `cmd/admin migrate`（0001→0052）+ bootstrap，用完 drop |
| API | 编译真实 `cmd/api` 二进制，监听 loopback 端口；外部走真实 HTTP + Cookie/CSRF，内部走真实 mTLS（worker/supervisor/maintenance 客户端证书） |
| 对象存储 | 真实临时对象目录（sealed 字节） |
| Python | 3.12.2；pytest 9.0.3 |
| Go | `go1.27.3 windows/amd64`；`GOCACHE=E:\orchara-toolchains\gocache`、`GOMODCACHE=E:\orchara-toolchains\gomodcache` |
| Node | v24.13.0（本票不涉及页面脚本，未用） |
| 并发 | 所有真实栈套件串行：跑前 `New-Item -ItemType Directory E:\Orchara\.scratch\f12\locks\realstack`，结束删除；原始日志在 `E:\Orchara\.scratch\f12\t093\` |

### 7.2 本票门禁（命令 + 原始 tail）

```powershell
cd E:\Orchara-wt\review-f12\product
$env:F12_WRITE_EVIDENCE='1'
python -m pytest tests/f12/test_operations.py -q
```
```text
........................................................................ [ 22%]
........................................................................ [ 45%]
........................................................................ [ 67%]
........................................................................ [ 90%]
...............................                                          [100%]
319 passed in 26.44s
```

证据 JSON（`reports/f12/T093-api-conformance.json`，复核修复后重新生成）的 summary 原始值：

```json
{"operations": 106, "route_registered": 106, "functional_probe": 60,
 "functional_coverage": "60/106", "implemented": 60, "route_only": 46,
 "probe_failed": 0, "missing_operations": [], "authorization_failed": [],
 "per_operation_failed": [], "invariant_failures": [],
 "cross_principal_isolation": {"checked": 18, "failures": []},
 "errors": [], "verdict": "route_only",
 "not_run": {"sse_frames": ["410 event_cursor_expired + snapshot_url: requires an event older than ORCHARA_RUN_EVENT_RETENTION; the development stack keeps 24h, so this branch was not exercised"]}}
```

形状不变量原始观测（证明不是空集通过）：

```json
{"uuid_server_generated": [{"id_fields_checked": 125}],
 "utc_rfc3339": [{"timestamp_fields_checked": 23}],
 "decimal_strings": [{"money_fields_checked": 4}],
 "no_store": [{"json_responses_checked": 389}]}
```

变异测试（可单独重放，单独运行也通过）：

```powershell
python -m pytest tests/f12/test_gate_mutations.py -q -rA
```
```text
.....                                                                    [100%]
PASSED tests/f12/test_gate_mutations.py::test_the_gate_cli_refuses_a_manifest_with_105_operations
PASSED tests/f12/test_gate_mutations.py::test_the_gate_fails_on_an_injected_unimplemented_operation
PASSED tests/f12/test_gate_mutations.py::test_the_gate_cli_reproduces_the_session_verdict
PASSED tests/f12/test_gate_mutations.py::test_the_gate_reports_the_same_verdict_as_its_exit_code
PASSED tests/f12/test_gate_mutations.py::test_a_registered_but_always_401_operation_is_not_counted_as_implemented
5 passed in 51.19s
```

### 7.3 CLI 直接产出（端到端真实入口）

`test_the_gate_cli_reproduces_the_session_verdict` 用文档化命令行（`--base-url`、`--admin-*`、
`--internal-url/--internal-ca/--internal-cert-dir`、`--contract contracts/openapi.json`、`--out`）在
**另一个 pristine 栈**上以子进程运行 CLI，断言退出码 3、`--out` 文件存在且逐项等于 session 裁决：
`operations=106 / route_registered=106 / functional_probe=60 / route_only=46 / missing=[] /
authorization_failed=[] / invariant_failures=[] / verdict=route_only`、`by_epic` 与 `not_run` 键集合一致，
stdout 含 `F12 API gate: route-registered 106/106, functional probe 60/106, route_only 46`。
退出码 3 是 2026-11-07 复核新增的**诚实裁决**：没有失败，但 46 条只有路由、没有功能探针，不能报
“106/106 implemented”。这证明证据 JSON 是**可由 CLI 独立重放产出**的，而不是测试专属路径。

### 7.4 Go / 静态门禁

```powershell
$env:PATH="E:\orchara-toolchains\go\bin;$env:PATH"
$env:GOCACHE='E:\orchara-toolchains\gocache'; $env:GOMODCACHE='E:\orchara-toolchains\gomodcache'
cd E:\Orchara-wt\t093\product
go build ./...
go vet ./...
gofmt -l internal cmd tools
go test ./...
python tools/sync_spec.py --check
```
```text
BUILD=0
VET=0
GOFMT=0        # gofmt -l 输出为空，没有文件需要格式化
TEST=0         # go test ./... 全部 ok，无 FAIL
no drift: spec copy and contract snapshot match the manifest
```

## 8. 变更文件、提交与合并

### 8.1 提交（分支 `t/t093-api-conformance`）

| 提交 | 内容 |
|---|---|
| `1cf8c2c` `feat(f12)` | 新增 `product/tools/f12_api_gate.py`（门禁 CLI） |
| `9385ee6` `fix(f12)` | 5 处服务端契约修复（`cmd/api/main.go`、`internal/api/errors.go`、`internal/api/approvals.go`、`internal/approvals/query.go`、`internal/execution/store.go`） |
| `8ab0b1f` `test(f12)` | `product/tests/f12/` 6 个文件（含 3 条失败修复、门禁自身 5 处判定修复、独立 pristine CLI 栈） |
| 本报告 + `PROGRESS.md` | 文档提交；最终 HEAD 见 `E:\Orchara\.scratch\f12\T093-REPORT.md` |

### 8.2 合并

`git merge integration/f12` → **`Already up to date`**（集成分支 tip 仍为基线 `1cdff9c`，未产生合并提交）。
合并后在同一工作树重跑：

```text
pytest tests/f12 -q                → 242 passed in 65.88s (0:01:05)
go build ./... / go vet ./...      → BUILD=0 VET=0
gofmt -l internal cmd tools        → GOFMT=0（空）
go test ./...                      → TEST=0（全部 ok，无 FAIL）
python tools/sync_spec.py --check  → no drift
```

### 8.3 文件清单

新增：

- `product/tools/f12_api_gate.py`
- `product/tests/f12/{conftest,test_manifest,test_operations,test_cross_principal,test_invariants,test_gate_mutations}.py`
- `product/reports/f12/T093-api-conformance.json`（机器可读证据）
- `product/reports/f12/T093-api-conformance.md`（本文件）

修改：

- `product/cmd/api/main.go`（`GET /{$}` + 外部/内部 mux 套 `ContractErrors`）
- `product/internal/api/errors.go`（`ContractErrors` 中间件）
- `product/internal/api/approvals.go`（`catalog.ParseLimit` + `details.field`，删本地解析器）
- `product/internal/approvals/query.go`（服务层不再 clamp）
- `product/internal/execution/store.go`（`contractTimestamp()`）
- `product/PROGRESS.md`（本票小节）

删除（开发残留，从未入库）：`product/tests/f12/test_zz_dump.py`——上一轮把报告 dump 到 `.scratch` 的调试用例；
证据已由 `--out` 与 `F12_WRITE_EVIDENCE=1` 正式产生。

**未改**：`product/docs/spec/**`、`product/contracts/**`、`db/migrations/**`、页面脚本。

## 9. 仍存在的风险 / 需要后续票处理的点

1. **SSE 410 保留窗口分支未实测**（§5.3）：`event_cursor_expired`+`snapshot_url` 的客户端行为没有真实
   低保留窗口证据，归 T096。当前门禁把该分支显式记 `not_run`，不冒充通过。
2. **6 条既有红**（§6.2）是集成分支上其它 epic 与旧夹具的漂移，本票未修；这些套件整体绿之前，
   F12 的“全仓回归”结论只覆盖本票列出的 7 个套件，不等价于全仓无回归。
3. **门禁会改变被测栈**（创建 Run/领取租约/注册模型）：必须对一次性测试栈运行；同一库上重复运行会把
   第 2 次判成失败。CLI 复跑用例因此使用独立 pristine 栈（§7.3）。
4. **静态注册表是存在性权威**：它依赖 `--product-root` 下 `server.go`/`internal.go`/`main.go` 源码可读；
   读不到时会把所有 operation 判 missing（fail-closed，不会假绿），但结果不再与运行时等价。生产部署若
   只发布二进制，应以同一份源码快照运行门禁。
5. **UUID 豁免规则来自契约 schema**：若未来契约把某个配置目录 id 改成 UUID（或反之），门禁会随契约
   自动调整；单测锁定当前恰为 2 条，契约变更时该单测会提醒同步。
6. **未覆盖的真实环境**：无真实厂商模型/凭据、无真实浏览器 DOM/Playwright；本票是 HTTP/SSE 外部客户端
   验收，页面逻辑与模型供应商行为不在本票证据内。
7. **非契约路由继续增长**：本票只审计并列出 93 条非契约模式（§5.4），不阻止后续 epic 新增；若要求
   “OpenAPI 是唯一对外面”，需要另开契约治理票。
8. **`details` 形状未统一**（§4 #2）、**`no-store` 使协商缓存不可用**（§4 #3）、
   **`artifact.ready`/`usage.updated` 无 emitter**（§4 #1）仍是已知产品/契约边界，本票只记录证据。
9. **复核后的功能覆盖边界（2026-11-07）**：`implemented` 只统计有 functional probe 的 60 条；
   46 条 route_only 见 §5.5。若要把它们变成 functional，需要先补发布版本/审批/评测报告/后台 job/
   ingress 文档等夹具，或另开票扩展门禁；本票不把“路由存在”写成“功能已验证”。退出码 3 表示
   route_only 裁决，CI 若要求 0 必须显式处理这一新语义，而不是把 3 当成通过。
