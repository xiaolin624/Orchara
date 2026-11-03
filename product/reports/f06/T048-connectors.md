# T048 / GitHub #49 — 固定 HTTP 连接器与受控出站

状态：**`passed`**（范围 = 管理员登记版本化连接器策略；工具声明的 connector 版本进入编译期
manifest 闭包；`invokeCapability(kind=connector)` 校验策略/method/path/请求 schema/风险后由 Go
执行一次真实 HTTP 出站，SSRF/DNS/重定向/私网/元数据/凭据保护均为真实实现；写风险停在
`awaiting_approval`/`approval_required`；撤销使下一次调用失败；操作/attempt/回执/owner 账本完整。
真实厂商连接器仍是部署定义的后续工作，见 §8 `not_run`）。

证据：本文件、`PROGRESS.md` T048 段、ADR 0007 D15。未修改 `docs/spec/**`、`contracts/**`、
迁移 0001–0028；新增迁移仅 `0029_connector_policies.sql`。

## 1. 验收对照（issue #49）

| 验收项 | 证据 | 结论 |
|---|---|---|
| SSRF（169.254.169.254 / 私网 / 无 allow_private 的 loopback）拒绝 | `tests/f06/test_t048_connectors.py::test_metadata_and_private_targets_are_refused_before_dialing`：真实 loopback server 零请求，operation 行 `failed/connector_target_forbidden`、无回执 | 通过（真实拒绝，非 mock） |
| DNS 解析到私网拒绝；allow_private 时按已验证地址固定连接 | `test_a_dns_name_that_resolves_to_a_private_address_is_refused`：`localhost` 解析为 ::1/127.0.0.1；`allow_private=false` 在 dial 前拒绝；`true` 时真实请求到达（Host 保持 `localhost:<port>`） | 通过 |
| 重定向到内网拒绝且不跟随 | `test_a_redirect_to_an_internal_host_is_refused_and_not_followed`：302 → `http://169.254.169.254/...`；只发生 1 次原始请求，`connector_redirect_refused` | 通过（拒绝全部 3xx 是文档化选择，见 §8 偏差 2） |
| 能力种类歧义拒绝 | `test_a_uuid_declared_as_both_model_and_connector_is_refused`：同一 UUID 同时出现在 `model_profile_versions` 与 `connector_policies`，`kind=connector` 与 `kind=model` 均 409 `capability_kind_ambiguous`，0 operation、0 出站 | 通过 |
| 撤销策略使下一调用失败 | `test_revocation_fails_the_next_call_and_never_deletes_the_policy`：先成功一次；撤销后下一次 403 `connector_policy_revoked`，行保留（`revoked_at` 非空、不删除），无新 operation/出站；重复撤销 409 | 通过 |
| 方法/路径/schema/query 越界拒绝 | `test_method_path_schema_and_query_rules_are_enforced`：prefix 外路径/点段 → `connector_request_invalid`；GET 带 body、未声明 query、请求 schema 不满足 → 422 `validation_failed`（在 operation 行之前）；声明 query 时真实到达 `?q=hello` | 通过 |
| 端到端真实入口 + 持久化/回执/账本 | `test_read_connector_is_frozen_into_the_manifest_and_called_for_real`：debug run → manifest 冻结 `{version_id,policy_sha256}` → mTLS `invokeCapability` → 真实 loopback 请求 → operation/attempt/receipt/ledger | 通过 |
| 凭据不泄漏（响应/日志/回执/params artifact/账本） | 同一用例逐项断言；密钥只出现在 API 进程环境与测试断言中；回执仅含 status/response_sha256/headers/body | 通过 |
| 写风险需审批 | `test_a_write_risk_policy_returns_awaiting_approval_and_never_calls`：200 `state=awaiting_approval`、`data.error_code=approval_required`、0 出站、operation `awaiting_approval`、0 attempt；同键重放仍停住 | 通过（T050 负责审批/恢复，见 §3） |
| 记录版本/输入/hash/命令/原始结果/profile，stub/real 分报 | §5、§6、§7；`not_run` 见 §8 | 通过 |

## 2. 交付内容

### 2.1 迁移 0029：连接器策略注册表（契约基线无此表，登记偏差）

`platform.connector_policies`：`id/version/origin/method/path_prefix/request_schema/response_schema/
secret_ref/secret_header/risk_class/allow_private/policy_artifact_id/policy_sha256(UNIQUE)/
created_by/approved_at/revoked_at/created_at`，CHECK（origin 形状、method 集合、risk read|write、
sha256 hex、secret_header token），`revoked_at` 保留行不删除；同 0007–0028 的权限边界
（REVOKE worker/PUBLIC）与逐列 COMMENT。`contracts/database.sql` 没有连接器表，本票**不修改**
契约/生成源，偏差写在迁移头注释与本报告 §8。

### 2.2 管理员登记（非契约、文档化）

- `POST /api/v1/admin/connectors`（admin + CSRF）→ 201 策略 JSON；
  `GET /api/v1/admin/connectors` → `{items:[...]}`；
  `POST /api/v1/admin/connectors/{id}/revoke` → 200 策略 JSON。
- 输入校验（纯 Go，`internal/connectors.normalizePolicyInput`）：origin 必须恰为
  `scheme://host[:port]`（无 path/query/fragment/userinfo，http|https，端口 1..65535）；
  method ∈ GET/POST/PUT/PATCH/DELETE；path_prefix 单斜杠开头、无点段/转义/反斜杠/query；
  request/response schema 为受限 object schema（未知关键字拒绝，64 KiB 上限）；secret_ref 必须是
  `env:NAME`/`file:NAME` 部署引用；secret_header 为合法 header token 且不可覆盖 Host/
  Content-Length/Transfer-Encoding 等；risk_class ∈ read|write。
- 策略体按 RFC 8785 JCS 封存为 `purpose=connector_policy` 的不可变 artifact；
  `policy_sha256` 是版本身份（UNIQUE）。完全相同的策略 → 409 `connector_policy_exists` +
  `details.policy_id`（幂等、不产生第二份 artifact）。`version` 是
  `(origin,method,path_prefix)` 的登记序号，属元数据，不进入 hash。
- 响应不含凭据值；`secret_ref` 只是引用，`secret_header` 只是 header 名。

### 2.3 编译期闭包

- `platform.tool_versions.connector_versions`（0012）在 `catalog.loadToolDependencies` 读取；
  每个工具声明的连接器 id 展开为同一 document pointer（工具字段）上的 located dependency，
  `source` 记录工具版本。
- readiness：不存在 → `dependency_not_found`；`revoked_at` → `dependency_revoked`；
  未 `approved_at` 或无 policy hash → `dependency_not_ready`。错误定位在工具的
  `/nodes/<i>/config/tool_version(s)`。
- `compiler.Build` 新增可变参数 `connectors ...ConnectorPolicy`，规范化（UUID、64 hex、同 id
  不同 hash 拒绝、按 version_id 排序）后写入 `manifest.connector_policies`；空集仍是 `[]`，
  不影响既有 manifest 字节/hash。
- 工具提交现在接受 connector ids：每个 id 必须是已批准未撤销的策略，规范化（去重排序）后参与
  `tool_manifest_sha256` 内容身份（此前恒为 `[]`）；只影响声明了连接器的工具。

### 2.4 `invokeCapability(kind=connector)`

- 解析顺序：run/attempt/manifest 字节校验（T041 不变）→ manifest 成员（`version_id` 与
  `policy_sha256` 必须一致）→ 策略行存在/已批准/未撤销 → params 词汇（仅 `path/query/body`）→
  method/body 规则 → 请求 schema（对完整 params 对象）→ query 声明 → 风险。
- 同一个 UUID 出现在 model/tool/connector 任一以上集合 → 409 `capability_kind_ambiguous`，绝不猜。
- read 风险：一次 operation（`capability_kind=connector`、`risk_class=read`）→ 一次 physical
  attempt → Go 受控出站 → 成功封存回执并置 `succeeded`；失败以稳定码置 `failed`（attempt 同样
  有行）。连接器调用**没有隐藏重试**：一次 invoke 恰好一次 HTTP 请求。
- write 风险：operation 置 `awaiting_approval`，返回
  `{state:"awaiting_approval", data:{error_code:"approval_required", connector_policy_version_id,
  risk_class:"write", status:"awaiting_approval"}}`，**不产生 attempt、不发出站**；同 operation/
  logical key 重放返回同一停住状态。T050 写 approval 行后再恢复。
- 回执内容：`{capability_kind:"connector", connector_policy_version_id, policy_sha256, method,
  origin, path_prefix, status, response_sha256, response_bytes, headers{content-type,
  content-length,date,cache-control,x-request-id}, body(JSON 可解析时), stub:false}`；
  不含请求 body/query 值/凭据。`data` 返回 `{status,headers,body,connector_policy_version_id,
  policy_sha256,stub}`。
- owner 账本 `GET /api/v1/runs/{id}/capabilities` 每项新增 `risk_class`，连接器项新增
  `connector_target`（`"<METHOD> <origin><path_prefix>"`，无 params/凭据）。

### 2.5 受控出站（`internal/connectors.Egress`）

真实 `net/http`：
- URL 只由注册 origin + 已验证 path（prefix 内、无绝对 URL/`//host`/反斜杠/点段（含
  `%2e%2e`）/fragment/控制字符）构成；query 只有在 request schema 的 `properties.query` 显式
  声明时才允许，并按该 schema 校验；
- DNS：解析全部地址并逐个分类；任一拒绝即整体拒绝；通过后把**全部**已验证地址固定进
  `DialContext`（按序尝试，绝不二次 DNS），Host/SNI 保持注册 origin；
- 分类：unspecified/multicast/link-local/cloud metadata（169.254.169.254）**即使
  allow_private=true 也拒绝**；loopback/RFC1918 仅 allow_private=true 放行；其余必须
  global unicast；
- 重定向：`CheckRedirect` 立即返回错误，3xx 不跟随，稳定码 `connector_redirect_refused`；
- `Proxy=nil`（环境代理不得决定连接目标）、10s 超时、请求/响应各 1 MiB 上限、无 keep-alive；
- 凭据在 Go 内按 `env:`/`file:` 解析，只写入 `secret_header`；错误信息不含凭据/引用/内网地址。

### 2.6 页面

`/runs` 受控调用账本新增「目标」列与风险标签：连接器项显示
`GET http://127.0.0.1:8080/v1` 形式的安全目标与「读/写」风险；写风险停住项显示「待审批」；
不显示 params/query/回执正文/凭据。`describeCapabilityOperation` 暴露
`connectorTarget/targetText/riskClass/riskText/isConnector`，纯函数在 Node 下执行。

## 3. T050 / T051 可直接使用的确切接口

### 3.1 T050（审批与恢复）

- 停住的写操作：`platform.operations.state='awaiting_approval'`、`risk_class='write'`、
  `capability_kind='connector'`，0 attempt、0 receipt；`invokeCapability` 返回
  `state=awaiting_approval`、`data.error_code=approval_required`。
- 恢复入口（建议）：T050 在 approval 行落库并授权后，将同一 operation 从
  `awaiting_approval` → `authorized`，再调用
  `capabilities.(*Service).dispatchConnector(ctx, run, input, spec, operationID)`（包内方法；
  T050 在 `capabilities` 包内实现可直接调用）。契约所需 `approval_id` 字段已在
  `capabilities.Result.ApprovalID` 与 HTTP 响应中存在，当前恒 `null`。
- 幂等：同 operation id/logical key/params 的调用在停住状态下只返回停住结果；
  T050 恢复后必须保持 `UNIQUE(run_id,logical_key)` 与 `params_sha256` 不变的语义。
- 连接器服务：`connectors.(*Service).Load(ctx, id) (*Policy, *Error)`；策略行
  `Approved()`/`Revoked()`；写风险判断 `RiskClass=="write"`。

### 3.2 T051（后续连接器消费方）

- `connectors.Service`：
  - `New(db *sql.DB, store *objectstore.Store, lookup config.Lookup, secretDir string) *Service`
  - `Register(ctx, adminID string, input Input) (*Policy, *Error)`
  - `List(ctx) ([]Policy, error)`、`Revoke(ctx, policyID string) (*Policy, *Error)`
  - `Load(ctx, policyID string) (*Policy, *Error)`
  - `Execute(ctx, policy *Policy, call Call) (*Response, error)`
- 类型：
  - `Input{Origin, Method, PathPrefix string; RequestSchema, ResponseSchema map[string]any;
    SecretRef, SecretHeader, RiskClass string; AllowPrivate bool}`
  - `Policy{ID, Version, Origin, Method, PathPrefix, RequestSchema, ResponseSchema, SecretRef,
    SecretHeader, RiskClass, AllowPrivate, PolicyArtifactID, PolicySHA256, CreatedBy string;
    ApprovedAt, RevokedAt *time.Time; CreatedAt time.Time}`
  - `Call{Path string; Query url.Values; Body any}`；
    `Response{Status int; Header map[string]string; Body []byte; ContentType string}`
  - `Error{Status int; Code, Message, Field string; Details map[string]any}`（`AsError`）
- 稳定错误码：`connector_policy_exists`、`connector_policy_not_found`、
  `connector_policy_revoked`、`connector_policy_unavailable`、`connector_policy_mismatch`、
  `connector_request_invalid`、`connector_target_forbidden`、`connector_redirect_refused`、
  `connector_secret_unavailable`、`connector_http_error`、`connector_response_invalid`、
  `connector_response_too_large`、`connector_timeout`、`connector_transport_error`；
  capabilities 侧另有 `validation_failed`、`capability_not_allowed`、
  `capability_kind_ambiguous`。
- 编译器：`compiler.Build(inputs, applicationID, sourceRevision, definition, card, tools,
  modelProfileVersions, subappVersions, connectors ...ConnectorPolicy)`；
  `compiler.ConnectorPolicy{VersionID, PolicySHA256 string}`。
- 账本投影：`capabilities.LedgerOperation` 新增 `RiskClass string`（JSON `risk_class`）与
  `ConnectorTarget *string`（JSON `connector_target,omitempty`）。

## 4. 测试

### 4.1 Go 单测

- `internal/connectors`（14 个顶层 test）：origin/method/prefix/secret/schema 拒绝矩阵与规范化；
  invocation path/query 拆分与走私拒绝；地址分类（metadata/link-local/multicast/unspecified
  永远拒绝；loopback/RFC1918 仅 allow_private）；策略体 JCS 稳定且不含凭据值；真实 loopback
  server 成功/头注入/凭据不泄漏；allow_private=false 在 dial 前拒绝（server 计数 0）；metadata
  拒绝；DNS 私网拒绝 + 一次性解析/地址固定；重定向拒绝且不跟随；响应 schema 校验与字节上限。
- `internal/catalog`（2 个顶层 test）：工具连接器展开的 pointer/source/去重；readiness 矩阵
  （missing/revoked/unapproved/no digest/ready）。
- 既有测试更新：`capabilities.TestResolveCapabilityAllowlistAndAmbiguity` 增加 connector 成员与
  三集合歧义；`toolversions` 的 `toolManifestSHA` 调用补连接器参数。

### 4.2 真实栈（`tests/f06/test_t048_connectors.py`，12 用例）

见 §1 对照表。全部用例使用真实 PG（0001–0029）、编译后的 `cmd/api`、真实 mTLS capability、
真实对象存储/回执字节、真实 manifest 编译与真实 loopback HTTP server。

### 4.3 页面（`tests/f06/test_t048_pages.py`，3 用例）

`/runs` 结构含「目标」列与 F06/T048 说明、页面不含 `secret_ref`/`Authorization`；`runs.js` 暴露
`capabilityRiskText`/`connectorTarget`/`riskClass`/`isConnector`；Node 执行纯函数验证连接器目标、
读/写风险、`awaiting_approval` 显示、失败码与模型项不受影响。

### 4.4 回归

- `tests/f06` 既有用例仅两处需要随行为更新：`test_t041_capabilities.py` 账本项集合新增
  `risk_class`（T048 的账本字段扩展）；T042 legacy golden agent 与 T043 revoked-model 两个
  依赖 WSL2/runsc 的用例在两次全量运行中分别出现一次 `sandbox_failed`（lease lost），两者
  单独重跑均通过，代码路径未改动，判定为沙箱环境抖动。
- `tests/f02a` 104 passed；`tests/tooling` 21 passed。

## 5. 版本 / 输入 / 命令 / 环境 profile

- 分支 `t/t048-connectors`，基线 `6d72ef2`（T047 merge）；worktree `E:\Orchara-wt\t048`。
- Go：`E:\orchara-toolchains\go\bin\go.exe`（go1.27.3），
  `GOCACHE=E:\orchara-toolchains\gocache`、`GOMODCACHE=E:\orchara-toolchains\gomodcache`。
- Python 3.12（`python -m pytest`）；PostgreSQL `127.0.0.1:55432`；对象存储为每个测试独立的
  tmp 根；内部 mTLS 证书每个 fixture 现生成。
- 连接器测试凭据：仅 API 进程环境变量 `T048_TEST_SECRET`（`env:` 引用），值不进仓库/数据库/
  回执/日志；本地 HTTP server 绑定 `127.0.0.1` 随机端口，brief 明确允许 loopback（测试策略
  `allow_private=true`）。
- 迁移：仅新增 `0029_connector_policies.sql`；0001–0028 未改。

## 6. 门禁原始输出

> 全部顺序执行（未并行跑套件）；按 brief 未跑 f05/f03。

```text
$ E:\orchara-toolchains\go\bin\go.exe vet ./...
（无输出，exit 0）

$ E:\orchara-toolchains\go\bin\go.exe test ./...
（27 个包 ok，0 FAIL；尾部 6 行）
ok  	orchara/platform/internal/supervisor	(cached)
?   	orchara/platform/internal/toolruns	[no test files]
ok  	orchara/platform/internal/tools	(cached)
ok  	orchara/platform/internal/toolversions	(cached)
ok  	orchara/platform/internal/uuid	(cached)
ok  	orchara/platform/internal/workflow	(cached)

$ gofmt -l internal cmd tools
（空输出）

$ python -m pytest tests/f02a -q
104 passed in 755.00s (0:12:35)

$ python -m pytest tests/tooling -q
21 passed in 7.88s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest

$ python -m pytest tests/f06 -q
125 passed, 1 failed in 684.34s (0:11:24)
# 唯一失败：tests/f06/test_t043_agent_node.py::test_agent_revoked_model_is_a_platform_outcome
#   error_code=sandbox_failed / "the attempt lease was lost while the sandbox ran ... heartbeat: context canceled"
# 单独重跑该用例：1 passed in 29.56s。
# 同一沙箱环境在另一次全量运行中让 T042 legacy golden 用例出现同类 sandbox_failed 抖动，
# 该用例单独重跑亦通过；两次失败均不在连接器代码路径。
```

`tests/f06` 最终数字见 §9（全量运行记录；本机 T042/T043 sandbox 用例各出现一次环境抖动）。

## 7. stub / real 分报

- **real**：admin 登记/撤销 HTTP 路径、PostgreSQL 0029、JCS 策略 artifact 字节与 hash、debug
  run 的 manifest 编译与实际 `connector_policies` 冻结、mTLS `invokeCapability`、Go 真实
  HTTP 出站（DNS 解析、地址分类、连接固定、重定向拒绝、响应 schema 校验）、operation/
  attempt/receipt 行与 artifact 字节、owner 账本读、真实 loopback server 观测。
- **stub（仅模型路径）**：既有 T041 dev-stub 模型提供方；与本票连接器无关。
- **fake**：无生产状态 fake。DNS 重绑定 / 地址分类矩阵的部分单测用注入 resolver；真实栈用
  `localhost`/`127.0.0.1`/`169.254.169.254` 真实解析路径。
- **部署定义（真实厂商连接器）**：本构建不包含任何厂商连接器实现；连接器策略是部署契约，
  `not_run` 见 §8。

## 8. not_run / 偏差 / 已知限制

- **not_run（真实厂商连接器）**：只有通用 HTTP 连接器策略与受控客户端；Slack/GitHub 等具体
  厂商语义、分页/签名/重试策略由部署方以策略+schema 登记，未在本票运行。
- **not_run（T050 审批恢复）**：审批行写入与 `awaiting_approval → authorized` 恢复属于 T050；
  本票只保证停住状态、幂等重放与“绝不出站”。
- **not_run（浏览器 DOM）**：页面逻辑以 Node 执行纯函数验证；无浏览器驱动。
- **not_run（全量 `pytest tests`、f00b/f03/f05）**：按 brief 只跑 f06/f02a/tooling。
- **偏差 1（契约基线无连接器表）**：新增 `platform.connector_policies`，`contracts/database.sql`
  与生成源未改（只读约束）；迁移头注释、本报告与 ADR 0007 D15 记录该偏差。
- **偏差 2（重定向一律拒绝）**：brief 允许“拒绝并记录”的最简安全实现；不实现逐跳重校验。
- **偏差 3（query 显式声明）**：无新列；query 只有在 request schema 的 `properties.query`
  中声明时才允许，并按该 schema 校验；否则 422。这是“管理员显式允许 query 形状”的落地。
- **偏差 4（`version` 语义）**：`version` 是同一 origin/method/prefix 的登记序号（展示/血缘），
  不进入 sealed body/hash；相同策略重复登记是 409 而不是新版本。
- **偏差 5（写风险不执行）**：`awaiting_approval` 不是成功；不产生 attempt/receipt，账本明确
  显示待审批；T050 恢复后仍复用同一次 operation 身份。
- **偏差 6（一次调用一次物理请求）**：连接器不重试；DNS 多地址仅在已验证集合内按序拨打，
  不是重试，也不重新解析（防重绑定）。
- **偏差 7（歧义用例的构造）**：生产登记路径无法制造跨种类同 UUID；真实栈用例用一次数据库
  fixture 更新把策略 id 改成 golden model id，随后走真实 manifest/调用路径验证 409。
- **偏差 8（工具提交行为变化）**：`connector_version_ids` 从“必须为空”变为“接受已批准策略并
  进入内容身份”；只影响声明连接器的工具，未声明时 manifest 字节不变。
- **未做**：未 push、未关闭 issue #49；未修改 `docs/spec/**`、`contracts/**`、迁移 0001–0028。

## 9. 提交与最终门禁

- 代码提交：`5cbf0c2`（迁移 0029 + connectors 包 + 共享 jsonschema）、
  `0567d91`（编译期闭包 + invokeCapability + API/工具提交/账本）、
  `50ef5e6`（T048 测试 + 页面投影 + T041 账本字段更新）。
- 证据提交：本报告 / `PROGRESS.md` / ADR 0007 D15（见 `git log` 最后一个 docs 提交）。
- 最终门禁见上方 §6 与下方最后一次顺序记录：

```text
$ E:\orchara-toolchains\go\bin\go.exe vet ./...
（无输出，exit 0）

$ E:\orchara-toolchains\go\bin\go.exe test ./...
（27 包 ok，0 FAIL，exit 0）

$ gofmt -l internal cmd tools
（空输出）

$ python -m pytest tests/f06 -q
125 passed, 1 failed in 684.34s (0:11:24)
# 失败用例 test_agent_revoked_model_is_a_platform_outcome 为 WSL2/runsc 沙箱 lease 抖动
# （error_code=sandbox_failed）；该用例随后单独重跑 1 passed in 29.56s，与连接器改动无关。

$ python -m pytest tests/f02a -q
104 passed in 755.00s (0:12:35)

$ python -m pytest tests/tooling -q
21 passed in 7.88s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```
