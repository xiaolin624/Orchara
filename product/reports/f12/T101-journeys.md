# T101（GitHub #102）请求、审批、记忆、知识及服务账号五旅程真实复现报告

- 状态：**`passed`**（2026-11-07 T101 收尾：新增 **J6** 把 AC2 的「API-key scope + 资源 ACL + 人类
  operation 审批」放到**同一个真实外部写**上证明（49 条断言；真实五类评测→提交→管理员评审的真实发布链、
  服务 key + grant 提交的已发布版本 Run、负责人批准、回环端点自己的请求日志恰好 1 次物理写），AC2 由
  `partial` 改判为**满足（平台/授权边界）**。J1b（F08↔F04B 输入缺口）和新增的 **J6b**（图分发外部写缺口）
  按实际行为保持 `blocked`。J6 的真实运行还暴露并修复了一个 **P1 产品缺陷**：published Run 的 sealed
  manifest artifact 属主相等规则会让任何非发布者的已发布 Run 无法调用任何 capability（503
  `manifest_unavailable`），见 §2/§5/§7.5）。
- 分支：`t/t101-three-in-one`；worktree：`E:\Orchara-wt\t101b`；基线 `integration/f13b` @ `30642be`
  （原 F12 旅程分支 `t/t101-data-journeys`、复核修复分支 `t/f12-review-fixes` 的事实保留在 §6.3/§9）。
- 完成前 `git merge integration/f13b` = **Already up to date**。
- 证据：`reports/f12/T101-journeys.json`（8 条 journey 记录：J1/J1b/J2/J3/J4/J5/J6/J6b；真实入口/用时/
  独立真值/断言点/失败原因；`passed` 由旅程体显式 `mark_passed()` 设置，`blocked` 由 `record.block()` 设置，
  证据文本在写入源头脱敏）。
- 环境条件：`ORCHARA_PROFILE=dev-stub`（**无真实厂商模型/凭据**）；WSL2 + runsc 真实执行；
  PostgreSQL 16.4（隔离集群 `127.0.0.1:55432`，每个测试自建库）。stub/real 分界见 §7。

## 1. 环境 profile（实测）

| 项 | 实测值 | 命令/来源 |
|---|---|---|
| 分支 / 基线 | `t/t101-three-in-one` / `integration/f13b` @ `30642be` | `git rev-parse` |
| Go | go1.27.3 windows/amd64 | `go version` |
| Python / pytest | Python 3.12.2 / pytest 9.0.3 | `python --version`、`python -m pytest --version` |
| PostgreSQL | `running: true`，port 55432，server 16.4 | `python tools/pg_dev.py status` |
| 沙箱 | `runsc version release-20241118.0`（WSL2 Ubuntu-24.04） | `wsl -d Ubuntu-24.04 -u root -e runsc --version` |
| 模型 | 只有 `dev-stub`；无厂商端点/凭据 | 部署环境（`ORCHARA_PROFILE=dev-stub`） |
| 浏览器 DOM | 本票**不使用**页面/DOM（票面明确「不重复实现功能页面」） | — |
| 真实栈互斥 | 全程持有 `E:\Orchara\.scratch\f13b\locks\realstack`（与并行真实栈票串行） | 目录锁 |

## 2. 逐 AC 对照

| AC（issue #102 原文） | 结论 | 证据 |
|---|---|---|
| 1. 请求结果、写审批、个人记忆、知识摄取、服务账号五旅程真实完成 | **满足（J1/J2/J3/J4/J5 全 passed；J1b 按实际行为记 blocked；另新增 J6/J6b 为 AC2 补证）** | `T101-journeys.json`：J1 16 断言、J2 34、J3 28、J4 19、J5 24、J6 49、J6b 7（blocked），全部真实 HTTP/真实 PG/真实 supervisor+maintenance/真实对象目录；无 mock 替身 |
| 2. scope/ACL/人类批准同时满足 | **满足（平台/授权边界）；同一次真实外部写** | **J6**：服务 key（`application:read`+`application:write`）持有 application read grant → `POST /requests` 提交**真实评测→提交→管理员评审发布**的版本 → Run 归 service principal → 写入操作审批的 approver 是该服务账号的**人类负责人**（`platform.users` 行，非 service principal）→ 负责人用浏览器会话按精确 `params_sha256` 批准 → 回环端点 `records` **恰好 1 次**物理写、1 条 succeeded attempt、真实 2xx receipt；同一调用去掉任一条件都被拒：无 grant → 404、只有 read scope → 403 `insufficient_scope`、service key 自己批准 → 404、admin → 403 `approver_mismatch`、错 hash → 409。**残留（J6b `blocked`）**：写操作由真实 worker capability 协议（Run 的活动 attempt token）发出，已发布**图**本身不产生 connector operation——图执行器没有 connector 节点，见 §5/§7.6。这是产品分派缺口，不是本旅程伪造；J6 的授权/审批/恰好一次链是真实的 |
| 3. 缺判定条件不计 passed | **满足** | 自然语言请求旅程 J1b 记 `blocked`（非 passed）；J6b 图分发缺口记 `blocked`；不声称真实模型质量、真实外部供应商、真实 embedding；未跑的「无显式版本的自然语言路由」写 `not_run` 及原因。J6 的 49 条断言全部来自真实 HTTP/mTLS/PG/对象字节 |
| 4. 通过端到端入口实际演示；失败、当前授权与重试/恢复边界有断言 | **满足** | 越权：跨用户 Run/artifact 404、陌生人审批 404、admin 批准 403、服务 key 个人数据 403；拒绝/过期/取消：审批 reject→失败、expiry→失败、cancel→invalidated；重复投递：同幂等键请求不建第二个 Run、同 logical key 不二次外写、丢回执重复 invoke 不重发；恢复：resume 恰好一次、丢回执→`needs_reconciliation`→证据 reconcile 成功且只 1 次物理写 |
| 5. 记录版本、输入/hash、命令、原始结果与 profile；stub/real 分报，缺条件 blocked、未运行 not_run | **满足** | 每条 journey 记录 entry/truth/assertions/facts；报告 §1/§4/§6/§7 含版本、输入 hash、命令、原始 tail、profile；`UV_*`/真实模型/真实外部供应商 not_run 均已列明 |

## 3. 逐旅程结果表（来自 `T101-journeys.json`）

| journey_id | 状态 | 真实入口（摘要） | 用时 | 独立真值来源 | 断言数 | 失败原因 |
|---|---|---|---|---|---|---|
| `J1-request-to-run-result` | **passed** | `POST /api/v1/requests` 显式 `application_version_id` → `cmd/supervisor exec --once` → `GET /runs/{id}`、`GET /requests/{id}`、`GET /artifacts/{id}/content` | 4.04s（版本发布在 J1b 前置一次完成） | 封存 request_input 对象字节 + `platform.runs` 行 + `summary.csv` 对 `tests/support/t049.py` 的独立 Decimal 复算 | 16 | — |
| `J1b-natural-language-request` | **blocked** | 同一显式版本入口，`message` 为自然语言正文 → 真实 supervisor | 8.97s（含一次性真实五类评测发布） | 封存 request_input 对象字节（纯文本）+ `platform.runs.state/error_code` + node-results 空数据行 | 5 | F08↔F04B 产品缺口：正文原字节当 Run 输入 → `node_input_invalid`（§7.1） |
| `J2-write-approval` | **passed** | 管理员 `POST /admin/connectors`（write 风险）→ worker mTLS `invokeCapability` → owner `POST /approvals/{id}/decision` → supervisor 恢复租约 → 真实回环外写 | 6.96s | 回环连接器自己的请求日志 `server.records`（物理写次数）+ `platform.approvals/operations/operation_attempts` + 封存 proposal 对象字节 | 34 | — |
| `J3-personal-memory` | **passed** | worker mTLS `POST /internal/v1/runs/{id}/memory-candidates` → `GET /memory-candidates` → `POST .../decision` → 3 次真实 agent Run 的 context trace 与封存模型请求 → `PATCH/DELETE /memories/{id}` | 16.54s | `platform.memory_candidates/memories` + 封存 body 对象字节 + run 的 context-trace 与 model request artifact | 28 | — |
| `J4-knowledge-ingestion` | **passed** | `POST /knowledge-bases` → 真实 signed-link 上传 + `artifact_finalize` → `POST .../documents` + `cmd/maintenance run --once --kinds document_ingest` → 检索 → `GET /knowledge-citations/{chunk}` → `DELETE /knowledge-documents/{id}` | 2.13s | 封存源对象字节的 sha256 与 rune 切片**由测试重算**（不是 chunk 行回读）+ `platform.knowledge_documents/chunks/background_jobs` | 19 | — |
| `J5-service-account` | **passed** | admin `POST /admin/service-accounts` → `POST /admin/api-keys` → 仅 Bearer 的 HTTP 调用 → `PATCH /admin/principals/{id}`（禁用负责人） | 5.52s | `platform.api_keys`（仅 SHA-256、scopes、expires_at、revoked_at） | 24 | — |
| `J6-service-key-write-approval` | **passed** | 真实发布链（五类评测→`POST /applications/{id}/submit`→admin `POST /reviews/{id}/decision`）→ admin `POST /admin/grants` → 服务 key `POST /requests`（Bearer only）→ supervisor mTLS claim → worker mTLS `kind=connector` 写操作 → 负责人 `POST /approvals/{id}/decision` → waiting/exit → resume → 恰好 1 次回环写 → 真实 F03 finalizer 结束 Run | 12.77s | 回环端点 `server.records`（物理写次数）+ `platform.approvals/operations/operation_attempts/api_keys/resource_grants/application_versions` + 封存 manifest/proposal/receipt/run-output 对象字节 | 49 | — |
| `J6b-graph-dispatch-write-gap` | **blocked** | 同一服务 key、同一已发布版本再提交一次 → 真实 `cmd/supervisor exec --once` 执行图到 succeeded | 1.79s | `platform.operations`（该 Run 0 条 connector operation）+ 回环 `records` 不变 + 真实 node results/output | 7 | 产品分派缺口：图里没有 connector 节点，agent tool call 恒为 `kind=tool`，所以已发布图自己无法发出受控外部写（§5/§7.6）；J6 用文档化的 worker capability 协议补证授权/审批/恰好一次 |

## 4. 旅程细节（真实入口、独立真值、断言点）

### J1 请求 → Run → 结果（passed）

- **前置发布**：不是种子版本。`t068.genuine_sales_report` 跑真实五类评测（runsc supervisor），
  `POST /applications/{id}/submit` 真实提交，管理员 `POST /reviews/{id}/decision` 真实批准 → 版本激活。
  该步骤与 T100 的「草稿→评测→发布」旅程同链路，本票只作为前置，不重复其内部断言。
  记录：`evaluation_cases=5`、`evaluation_status=passed`、`published_version_id=1f20cde6-…`。
- **真实入口序列**：`POST /admin/grants`（read）→ `POST /artifacts/uploads`+PUT+complete+finalize →
  `POST /conversations` → `POST /requests`（**显式** `application_version_id`）→ 同事务 `state=dispatched`
  → `cmd/supervisor exec --once` 真实执行 → `GET /runs/{id}`、`GET /requests/{id}`、`GET /runs/{id}/node-results`、
  `GET /artifacts/{id}/content`。
- **独立真值**：
  1. 封存 request_input artifact 的对象字节逐字节等于提交的任务文档，且
     `sha256(message) == artifact.sha256`（从对象目录独立读，不从 API 200 推断）；
  2. `platform.runs` 行：`state=succeeded`、`error_code IS NULL`、`kind=published`、
     `application_version_id` == 发布版本、`owner_id` == 请求者；
  3. `summary.csv` 字节 == 文档化期望值，且经 `tests/support/t049.py:check_summary_csv` 的
     **独立 Decimal 复算**（不调用产品工具）；artifact 行 `sha256` == 下载字节哈希；
  4. `output_artifact_id` 链接存在，final output 的 summary 也过同一独立校验。
- **边界断言**：同幂等键重复投递 → 202 且返回同一 request id、仍只有 1 个 request/run；已撤销版本的新请求 →
  `409 version_revoked` 且不建 request/run；发布者读请求者的 Run/artifact → `404 resource_not_found`；匿名 → 401。
  （撤销边界使用的第二个版本由既有 T074 助手 `f08.publish_version` 创建 —— 只有该负边界用版本行种子，
  主旅程版本来自真实评测/提交/审批链；见 §8。）

### J1b 请求（自然语言）→ Run（blocked，产品缺口实测）

- 同一显式版本入口提交**纯中文正文**：请求被接受并同事务 dispatch（不是测试侧拒绝），封存输入就是正文原字节。
- 真实 supervisor 执行该 Run：`platform.runs.state=failed`、`error_code=node_input_invalid`、
  `finished_at` 非空、无 output artifact、`GET /runs/{id}/node-results` 数据行为空。
- 判定：**blocked**。缺什么、影响哪条 AC 写在 §7.1；没有跳过、没有伪造成功。

### J2 写操作审批（passed）

- **真实入口序列**：admin `POST /admin/connectors`（`risk_class=write`，真实回环 HTTP 端点）→ worker mTLS
  `POST /internal/v1/runs/{id}/capabilities` → `GET /api/v1/approvals/{id}` → 陌生人/管理员决定被拒 →
  改变参数的同逻辑调用 → owner 用 `POST /approvals/{id}/decision`（必须带精确 `params_sha256`）→
  worker 提交真实 waiting result → supervisor confirm exit → 恰 1 个 resume job → 恢复租约再次
  `invokeCapability` → 回环端点收到 **1 次**真实写 → replay 不再发。
- **独立真值**：回环连接器 `server.records`（端点自己的请求日志，测试不参与发送逻辑）；
  `platform.approvals`（state/approver/params_sha256/expires_at）、`platform.operations`
  （state/params_sha256/receipt_artifact_id）、`platform.operation_attempts`；封存 proposal 对象字节
  （target/params/risk_class/policy_sha256/params_sha256）。
- **断言点**（34 条，摘要）：
  - proposal 由服务端构造并绑定精确参数：`approval.params_sha256 == operation.params_sha256`，
    sealed proposal 的 `params.body` 精确等于发送的字面量，target origin/method/path 正确，未泄漏 connector secret；
  - 批准前 0 次外写；approver 必须是 Run owner：陌生人 404、admin 403 `approver_mismatch`；
  - 改变参数 → `409 operation_conflict`；错误 hash 决定 → `409 approval_params_mismatch`，approval 仍 pending；
  - 批准后确认 exit → **恰 1 个** resume job；恢复调用成功后端点**恰 1 次**写、operation=succeeded、
    attempt 1 条 succeeded、封存 receipt 是端点真实 2xx body；同调用 replay 仍只 1 次写；
  - **拒绝**：operation failed `approval_rejected`，Run failed `approval_rejected`，无 resume、0 次写；
  - **过期**：`409 approval_expired`，Run failed `approval_expired`，无 resume、0 次写；
  - **取消**：`POST /runs/{id}/cancel` → approval `invalidated`，迟到决定 409，0 次写；
  - **写后丢回执**：真实端点收到写后断连 → operation `unknown`、1 条 dispatching attempt、无 receipt；
    重复 invoke 不重发；attempt 退出后 Run `needs_reconciliation`（`unknown_operations`，`finished_at` 为空）；
    owner 经真实上传的 evidence artifact + `POST /operations/{id}/reconcile` 结案为 succeeded，
    物理写次数仍为 1、端点记录仍为 1。

### J3 个人记忆（passed）

- **真实入口序列**：真实 Run 由 supervisor 租约持有 → worker mTLS
  `POST /internal/v1/runs/{id}/memory-candidates` → `GET /memory-candidates` → 陌生人决定 404 →
  服务 key 403 `personal_scope_forbidden` → `POST /memory-candidates/{id}/decision`（confirm）→
  真实 supervisor 跑 3 次 agent Run → `GET /memories/{id}`、`PATCH /memories/{id}`（If-Match）、
  `DELETE /memories/{id}` → `GET /memories`。
- **独立真值**：`platform.memory_candidates`（state/application/owner/封存 body artifact）、
  `platform.memories`（revision/candidate_id/deleted_at/封存 body）、run 的 context-trace artifact
  （`runs/{id}/node-results` 定位 → artifact content 读取）与封存 model request artifact。
- **断言点**（28 条，摘要）：
  - 候选是 pending，封存字节精确等于 worker 提交文本，作用域 = 该 Run 的 application + owner；
  - pending 候选不是 memory（记忆表为空）；陌生人队列为空；服务 key 队列 403；陌生人决定 404；
  - confirm 后恰 1 条 memory，candidate=confirmed；对已确认候选做相反决定 → 409 `memory_candidate_confirmed`；
    过期候选决定 → 409 `memory_candidate_expired`（`expires_at` 由时间效果推进，非改语义）；
  - 运行时召回：`memory_state=included`、source 恰为已确认 memory、pending/expired 不在 source、
    `0 < memory_tokens <= memory_budget_tokens`（本次 41 ≤ 30976）、封存模型请求文本含 memory 正文、
    trace 不泄漏正文（只有 id/计数）；陌生人读该 Run 的 trace → 404；
  - 修订后：revision=2，召回新文本、旧文本不出现；旧 If-Match → `412 precondition_failed`；
  - 删除后：`202` 删除任务，下一次真实 Run 的 recall sources 为空、正文不再出现在模型请求，`GET /memories` 列表为空。
- **诚实边界**：生产图执行**没有 memory 节点**（`presetexec/graphrun.go` 的分派表不含 memory），
  所以「真实 Run 自己产生候选」不存在；本旅程是「真实 Run 持有租约 + 真实 worker 身份直接调用内部端点」，
  与 F09/T054 既有事实一致，不写成 agent 自动提案。

### J4 知识摄取（passed）

- **真实入口序列**：`POST /knowledge-bases` → 真实 signed-link 上传 + `complete` +
  `cmd/maintenance run --once`（artifact_finalize）→ `POST /knowledge-bases/{id}/documents` →
  `cmd/maintenance run --once --kinds document_ingest` → `GET /knowledge-documents/{id}` 到 ready →
  `POST /knowledge-bases/{id}/retrieval-queries` → `GET /knowledge-citations/{chunk_id}` →
  `DELETE /knowledge-documents/{id}` → 再检索 + 再取引用 + `GET /deletions/{job_id}`。
- **独立真值**：
  1. `platform.knowledge_documents`（state=ready、revision=1、error_code 空）、
     `platform.knowledge_chunks`（chunk_no 连续、embedding 维度/revision）、
     `platform.background_jobs`（document_ingest = succeeded、attempt_count=1）；
  2. 引用**独立复算**：从封存源对象读字节 → sha256 必须等于 citation.source_sha256；
     用 citation span 的 rune 区间在源文本上重切 → 必须逐字等于返回文本；chunk body artifact 字节同文；
     引用声明 document/revision/chunk_no/source_artifact 与真值一致。
- **断言点**：检索非空且排序；引用可打开且与 query 同文；陌生人检索/引用均 404（owner-only）；
  删除 202 → 同一引用立刻 404 `resource_not_found` 且响应无 `text`/`citation`；删除后检索 `items=[]`
  （`cache.hit=false`，不是缓存回退）；删除任务可读（state + remaining scopes）；文档路由 404。

### J5 服务账号（passed）

- **真实入口序列**：admin 建两个人类（owner + responsible）→ owner `POST /applications` →
  admin `POST /admin/service-accounts` → `POST /admin/api-keys`（`application:read`；另建 `application:write`、
  短寿命、将被撤销的 key）→ 仅 `Authorization: Bearer` 的 HTTP 调用：`GET /auth/me`、
  `GET /applications`、`GET /applications/{id}`、`POST /applications`、`GET/POST /memories` →
  `DELETE /admin/api-keys/{id}` → `PATCH /admin/principals/{responsible}`（禁用）→ 再调用。
- **独立真值**：`platform.api_keys`（`key_hash` == SHA-256(secret)、明文不落库、scopes 精确、expires_at、revoked_at）；
  响应状态码；禁用事务后同一 key 行的 `revoked_at` 与 `expires_at` 变化。
- **断言点**（24 条，摘要）：
  - secret 一次返回（`orchara_` + 43 字符），库中只有 SHA-256；scope 集合精确；
  - `/auth/me`：kind=service、roles=[]、csrf_token=null；
  - 有 read scope 但无 grant → 列表空、详情 404（ACL 缺）；有 grant → 同一调用 200；
  - write-scoped key 读详情 → 403 `insufficient_scope`（scope 先于 ACL）；read key 写应用 → 403；
    write key 建应用 → 201；
  - 人类专属：服务 key `GET/POST /memories` → 403 `personal_scope_forbidden`；
  - 撤销：`204` 后下一次调用 401 `invalid_key`；过期：真实 3 秒寿命，deadline 过后 401（不注入时间）；
  - 禁用负责人：官方 `PATCH` 立即生效（401），且独立读 key 行证明 `revoked_at` 被禁用事务写入而 `expires_at` 未变。

### J6 service key → published task → responsible-human-approved write（passed，AC2 补证）

- **真实入口序列**：
  1. admin `POST /admin/connectors` 注册 write-risk 回环 connector（`allow_private=true`，
     `secret_ref=env:T048_TEST_SECRET`，端点由 `t048.LocalConnectorServer` 提供）；
  2. 真实 creator 建应用/草稿（`start -> python(sales_stats) -> end`）；**在评测编译快照之前**用
     文档化 T048 seam 把该 policy 绑到 preset tool 的 `connector_versions`（`platform.tool_versions`），
     使评测 manifest 与发布 manifest 都闭包该 policy；
  3. **真实五类评测**（`cmd/maintenance` pools + 每 case 真实 `cmd/supervisor exec --once`/runsc）→
     `POST /applications/{id}/submit`（creator，带 `expected_manifest_sha256` + 真实 report id）→
     admin `POST /reviews/{id}/decision` 批准 → `applications.active_version_id` 指向新版本；
  4. admin 建真人（responsible，role=user）→ `POST /admin/service-accounts`（responsible_user=该真人）→
     `POST /admin/api-keys`（`application:read`+`application:write`）→ `POST /admin/grants`（read）；
  5. 服务 key **仅 Bearer、无 Cookie/CSRF**：`POST /conversations`、真实 artifact 上传 + finalize、
     `POST /requests`（显式 `application_version_id`）→ 202 `dispatched`；
  6. supervisor mTLS `POST /internal/v1/jobs/claim` 领取该 Run 的 `start` job → worker mTLS
     `POST /internal/v1/runs/{id}/capabilities`（`capability_kind=connector`，policy id，精确 params）
     → 200 `awaiting_approval`；
  7. 负责人/陌生人/admin/服务 key 分别尝试决定（全部被拒）→ 负责人按精确 `params_sha256` 批准 →
     worker 提交 `waiting_approval`（checkpoint `capability:<operation_id>`）→ supervisor confirm exit
     → **恰好 1 个** resume job → supervisor 再领取 → 同一 operation 的恢复调用 → 端点收到 **1 次**物理写；
  8. worker 通过真实 run-artifact 协议上传 `final_output`（内容 = 封存 receipt 字节）+ register
     `relation=output` → `submitWorkerResult(succeeded)` → supervisor exit → 真实 F03 finalizer 把
     published Run 提交为 `succeeded`。
- **独立真值**：
  1. 回环连接器 `server.records`（端点自己的请求日志；测试不参与发送逻辑）= 物理写次数；
  2. `platform.approvals`（state/approver_id/decided_by/params_sha256）、`platform.operations`
     （capability_kind=connector、risk_class=write、state、receipt_artifact_id）、
     `platform.operation_attempts`（恰好 1 条 succeeded）、`platform.api_keys`、`platform.resource_grants`、
     `platform.applications.active_version_id`、`platform.application_versions.manifest_sha256`；
  3. 封存对象字节：发布 manifest（hash == version digest，`connector_policies` 精确含 policy id+digest）、
     request input（逐字节等于提交消息，sha256 匹配）、approval proposal（target/params/risk/policy digest）、
     connector receipt（端点真实 2xx body）、Run output（== receipt 字节）。
- **断言点**（49 条，摘要）：
  - 发布：五类评测 `passed` 且 5 个 case 全 passed；提交 202；admin 批准激活版本；manifest 字节 hash 到
    version digest；`connector_policies` 精确含该 write policy；`tool_artifacts` 含 preset tool；
  - 服务身份：service principal 的 responsible_user 是真人；key 行只有 SHA-256、scopes 精确；
    `/auth/me` kind=service/roles=[]/无 CSRF；
  - 三重门控（同一请求逐条去条件）：无 grant → 404 且不建 request/run；只有 read key → 403
    `insufficient_scope`；scope+grant → 202；durable request owner=service principal、pins version；
    sealed input 字节/hash；Run `kind=published`、owner=service principal、version/manifest 精确；
  - 写入操作：lease `job_kind=start` 且 manifest 含 policy；capability 调用 `awaiting_approval`；
    approval pending、run/operation 精确、**approver=负责人且是 `platform.users` 行**、
    `approval.params_sha256 == operation.params_sha256`、risk=write；
    封存 proposal target origin/method/path_prefix/path 与 params body 精确；**决定前 0 次写、0 attempt**；
  - 审批边界：service key 自己决定 → 404（不是 approver，即使 key 带 write scope）；陌生人 → 404；
    admin → 403 `approver_mismatch`；负责人错 hash → 409 且仍 pending；这些被拒决定都不外写；
  - 批准/恢复：waiting 提交 200；负责人批准 200 且 `decided_by`=负责人；决定本身 0 次写、0 resume；
    confirm exit 后恰好 1 个 resume；resume job 领取；恢复调用 succeeded；端点 **1 次** POST /v1/orders；
    platform 注入 `X-Connector-Secret`（值不落证据）；operation succeeded、1 条 succeeded attempt、
    receipt 是端点真实 2xx body（`write_id` 匹配）；重复投递不重发；
  - 收尾：Run output（真实 worker 上传）== receipt 字节；succeeded proposal + exit → Run `succeeded`、
    有 output、无 error、`kind=published`、owner=service principal；服务 key 可读自己的 Run；输出可下载。
- **stub/real 与 seam 声明**：
  - 模型：本旅程不调用模型（图是 `start -> python -> end`；写操作由 worker capability 协议发出），
    **dev-stub**，不声明任何真实厂商模型能力；
  - 外部写：本机回环 HTTP 端点（企业 stub），**不是真实第三方供应商**；物理写次数以端点自己的日志为准；
  - **唯一非 API 部署缝**：tool↔connector 绑定（`platform.tool_versions.connector_versions`，T048/T050
    文档化 seam）；断言不信任该行，而信任回环端点日志 + PG + 封存字节；
  - **worker 角色**：写操作由真实 mTLS worker capability 调用发出（Run 的活动 attempt token），与
    T048/T050/J2 相同；已发布图本身发不出 connector 调用（J6b，§7.6）。这是本旅程明确声明的边界，
    不写成「图自己触发」。

### J6b 图分发外部写缺口（blocked，产品分派缺口实测）

- 同一服务 key、同一已发布版本再提交一次（真实 `POST /requests`），让真实
  `cmd/supervisor exec --once` 执行该图（python preset tool 在 WSL2/runsc 中真实运行）。
- 实测：supervisor `outcome=succeeded`；Run `succeeded`、`kind=published`、owner=service principal、
  version/manifest 精确；node results ≥4；有真实 output artifact；但该 Run 的
  `platform.operations` 中 **0 条 connector operation**，回环端点物理写计数**不变**（仍为 J6 的 1 次）。
- 该 Run 的 execution snapshot manifest **确实携带** J6 的 write policy（digest 一致）——即「授权存在，
  但图里没有节点能发出这个调用」。
- 判定：**blocked**。缺口定位：`internal/presetexec/graphrun.go` 的节点分派表没有 connector 节点；
  `internal/presetexec/agent.go` 的 agent tool call 恒为 `capability_kind="tool"`；capability service 的
  tool 分支不读 `tool_versions.connector_versions`；`internal/presetexec/approval.go` 明确「业务审批不
  授权后续外部写」。修它要改图 DSL/执行器/契约（§5/§7.6），超出本票；本票以 J6 的 worker capability
  路径补证 AC2 的授权/审批/恰好一次链，并把这个缺口如实记 blocked。

## 5. scope / ACL / 人类批准：现在由 J6 在同一次真实外部写上同时证明

> **历史（2026-11-07 复核）**：本节原结论是 `partial`——J5 证明 scope+ACL（**读**，无审批），J2 证明人类
> 审批（**浏览器会话**，无 API-key scope/ACL），但没有任何一个外部写同时满足三者。该复核结论当时成立。
> **T101 收尾新增 J6 后改判**：一个真实外部写（回环端点恰好 1 次物理写）现在同时满足 API-key scope +
> 资源 grant + 负责人 operation 审批，另有 5 条负边界证明缺一即拒。

**J6 的四格矩阵（同一 Run、同一 operation，全部真实证据）**：

| 需要同时成立 | J6 的实测 | 去掉该条件 | 证据 |
|---|---|---|---|
| API-key scope | 服务 key `application:read`+`application:write` 的 `POST /requests` → **202 dispatched** | 同一服务 principal、同一 grant，换成只有 `application:read` 的 key → **403 `insufficient_scope`** | 响应码 + `platform.api_keys.scopes` |
| 资源 ACL grant | admin `POST /admin/grants`（action=read）后同一调用 202 | 同一 key、有 scope、无 grant → **404 `resource_not_found`**，不建 request/run | 响应码 + `platform.resource_grants` 行 |
| 人类 operation 审批 | 写入操作 approver = 服务账号的 `responsible_user_id`（`platform.users` 行）→ 该人类批准 → 恢复调用 succeeded | 服务 key 自己决定 → **404**；陌生人 → **404**；admin → **403 `approver_mismatch`**；负责人错 hash → **409** 且仍 pending | 响应码 + `platform.approvals(approver_id, decided_by, params_sha256)` |
| 同一操作产生外部写 | 回环端点 `records` **恰好 1 次** POST `/v1/orders`；1 条 succeeded attempt；封存 receipt = 端点真实 2xx body；重复投递不重发 | 决定前 0 次写、0 attempt；决定本身也不外写；只有 resume 后的 capability 调用才写 | 端点自己的请求日志 + `platform.operations/operation_attempts` + receipt 字节 |

- **产品为什么能表达**：`internal/approvals/service.go:187-210` 的 `approverForOwnerTx` 把 service-owned
  Run 的审批人解析为 `platform.principals.responsible_user_id`；`POST /requests` 的契约 security 允许
  `apiKey`，`internal/api/requests.go` 只要求 scope，版本解析要求应用的 read grant（`catalog.readAllowed`）；
  Run 的 `owner_id` 是调用者（`internal/requests/submit.go:649-665`）。J6 把这些既有事实接成一条真实链。
- **J6 发现并修复的 P1 产品缺陷**（见 §7.5）：`internal/capabilities/service.go` 的 `loadAuthorizedRun`
  原来要求 sealed manifest artifact 的 owner 等于 Run owner。debug Run 成立；但 published Run 的 snapshot
  复用**版本**的 manifest artifact（发布者 sealed，`SubmitPublication` 是 owner-only），Run owner 却是被
  授权请求者，于是**任何非发布者的已发布 Run 都无法调用任何 capability**（503 `manifest_unavailable`）。
  修复只对 `run.Kind == "published"` 放宽 owner 相等，保留 artifact ready、snapshot digest 相等、
  字节 sha256 == `runs.manifest_sha256` 的全部证明；真实 503 红证据与修复后绿证据见 §6.5。
- **J6 不声称的**：写操作由真实 mTLS **worker capability** 协议（Run 的活动 attempt token）发出，不是图
  节点发出——图执行器没有 connector 节点，agent tool call 恒为 `kind=tool`（J6b `blocked`，§7.6）。
  这是 T048/T050/J2 一直使用的文档化 worker seam；J6 把它用在**服务 key 提交的已发布版本 Run** 上，
  授权来源是该 Run 的冻结 manifest（真实包含该 write policy 与其 sealed digest）。
- **仍然分别成立的旧证据**：J5 的读路径三重门控（scope→ACL→负责人启用）与 J2 的人类审批三重绑定
  （精确 params hash、精确 approver、未过期/未取消 + supervisor exit 后 1 个 resume）保持原样；
  J6 复用并补上了 API-key scope + grant 这一半。J3 的记忆专属边界与 J4 的删除后 404 不受影响。

**J3 的人类专属边界**：候选/记忆只能由人类 owner 读取和决定；服务 key 即使带 `application:read/write`
也是 **403 `personal_scope_forbidden`**；陌生人决定 404；confirm 是唯一产生记忆的路径（pending 不是记忆）。

**J4 的当前授权**：知识库/引用按 owner ACL 当前状态判定；删除后引用立即 404 且不读缓存。

## 6. 真实命令与原始 tail

所有命令均在 `E:\Orchara-wt\t101b\product` 下运行；工具链环境变量：
`GOCACHE=E:\orchara-toolchains\gocache`、`GOMODCACHE=E:\orchara-toolchains\gomodcache`、PATH 前置 Go。

### 6.1 本票套件（证据运行，`T101_WRITE_EVIDENCE=1` 写 JSON）

```powershell
$env:T101_WRITE_EVIDENCE='1'
python -m pytest tests/f12/test_t101_journeys.py -q
```

原始 tail（2026-11-07 T101 收尾重跑，8 条 journey 记录）：

```
......                                                                   [100%]
6 passed in 87.93s (0:01:27)
```

（6 个测试函数、8 条 journey 记录：J1/J2/J3/J4/J5/J6 `passed`，J1b/J6b `blocked`；状态由旅程体显式
`mark_passed()` / `record.block()` 设置：没有“正常退出就 passed”的隐式改写。证据文本在
`t101_support._short()` / `record()` / `persist()` 源头脱敏。）

### 6.2 全量 F12 套件（不破坏 T093/T095/T096/T098）

```powershell
python -m pytest tests/f12 -q
```

原始 tail（2026-11-07 T101 收尾重跑）：

```
........................................................................ [ 16%]
........................................................................ [ 32%]
........................................................................ [ 48%]
........................................................................ [ 64%]
........................................................................ [ 80%]
........................................................................ [ 96%]
...............                                                          [100%]
447 passed in 262.76s (0:04:22)
```

（基线 446 + 本票新增 J6/J6b 用例 1 个测试函数 = 447；全部保留。J6/J6b 是同一测试函数里的两条
journey 记录，f12 套件按 pytest 测试函数计数。）

### 6.3 服务账号/身份相关：`tests/f01` 单跑（F12 历史记录，本轮未重跑）

> 以下 tail 是 F12 基线的历史证据（T101 原分支）。F13B 的门禁清单不含 `tests/f01`（该目录必须单跑，
> 见 ENV §2），本轮 T101 收尾**未重跑** `tests/f01`；J5/J6 的服务账号与身份断言都在 `tests/f12` 的
> 真实栈套件里独立执行。若把 `tests/f01` 全绿当合并门禁，仍需先修这两个既有页面标记测试。

```powershell
python -m pytest tests/f01 -q
```

原始 tail：

```
FAILED tests/f01/test_t017_pages.py::test_login_page_is_served_and_posts_the_contract_path
FAILED tests/f01/test_t019_pages.py::test_the_apps_page_lists_the_visible_applications
2 failed, 153 passed in 967.73s (0:16:07)
```

**这两个失败是基线既有缺陷，不是本票引入**：`git diff 60d9de0 -- product/tests/f01 product/apps/web`
为空（本票只新增 `tests/f12/**` 与 `reports/f12/**`），两个用例断言的是旧页面标记
（login 页要求 `window.location.assign('/admin')`，apps 页要求旧分页/空态标记），而已提交页面字节
在后续 F12 票（T095/T096/T098）中已改为角色落地/新空态。服务账号与身份相关用例**全部通过**
（含 `test_t020_*`：一次性明文、scope 先于 ACL、个人数据边界、撤销/过期/负责人禁用）。
本票 J5 已把服务账号旅程用 `support` helper 在 `tests/f12` 重写并真实执行，不依赖 `tests/f01`
的裸 `from conftest import`（该目录与其它套件同跑会 34 个 collection error，见 ENV §0）。
如实记录，不隐藏、不在本票修。

### 6.4 Go 与静态门禁

```powershell
& go build ./...
& go vet ./...
& go test ./...
& 'E:\orchara-toolchains\go\bin\gofmt.exe' -l internal cmd tools
python tools/sync_spec.py --check
```

原始结果（build/vet/gofmt 无输出 = exit 0）：

```
build_exit=0
vet_exit=0
gofmt_exit=0
sync_exit=0
no drift: spec copy and contract snapshot match the manifest
```

`go test ./...` 原始 tail（exit 0，2026-11-07 T101 收尾重跑）：

```
go_test_exit=0
ok  	orchara/platform/internal/audit	1.262s
ok  	orchara/platform/internal/background	2.555s
ok  	orchara/platform/internal/background/handlers	3.066s
ok  	orchara/platform/internal/budget	1.078s
ok  	orchara/platform/internal/capabilities	2.453s
ok  	orchara/platform/internal/catalog	1.713s
?   	orchara/platform/internal/children	[no test files]
ok  	orchara/platform/internal/compiler	2.688s
ok  	orchara/platform/internal/config	1.688s
ok  	orchara/platform/internal/connectors	3.497s
ok  	orchara/platform/internal/context	2.285s
ok  	orchara/platform/internal/db	1.752s
ok  	orchara/platform/internal/deletion	2.161s
ok  	orchara/platform/internal/diagnostics	2.415s
ok  	orchara/platform/internal/embedding	1.883s
ok  	orchara/platform/internal/evaluation	2.082s
ok  	orchara/platform/internal/execution	0.768s
ok  	orchara/platform/internal/health	4.930s
ok  	orchara/platform/internal/identity	5.421s
ok  	orchara/platform/internal/jcs	2.758s
ok  	orchara/platform/internal/jsonschema	2.057s
ok  	orchara/platform/internal/knowledge	7.898s
ok  	orchara/platform/internal/memory	3.170s
ok  	orchara/platform/internal/modelproxy	2.756s
ok  	orchara/platform/internal/modelregistry	3.075s
ok  	orchara/platform/internal/objectstore	1.965s
ok  	orchara/platform/internal/policy	2.803s
ok  	orchara/platform/internal/presetexec	6.087s
ok  	orchara/platform/internal/requests	2.213s
ok  	orchara/platform/internal/routing	3.292s
ok  	orchara/platform/internal/sandbox	5.028s
ok  	orchara/platform/internal/supervisor	2.096s
ok  	orchara/platform/internal/templates	2.855s
?   	orchara/platform/internal/toolruns	[no test files]
ok  	orchara/platform/internal/tools	6.103s
ok  	orchara/platform/internal/toolversions	2.292s
ok  	orchara/platform/internal/uuid	1.211s
ok  	orchara/platform/internal/workflow	1.743s
?   	orchara/platform/probes/f10_pdf_compare/go_baseline	[no test files]
?   	orchara/platform/probes/f15a_objectstore/localfs_probe	[no test files]
```

（完整日志：`E:\Orchara\.scratch\f13b\T101\gate-*.log`、`evidence-run.log`。）

### 6.5 P1 产品缺陷的红/绿证据（J6 实测）

```powershell
# 修复前（真实运行）：capability 调用被 503 拒绝
python -m pytest tests/f12/test_t101_journeys.py -q -k service_key_write
```

```
E   AssertionError: J6-service-key-write-approval: the write-risk connector call parks as
    awaiting_approval failed: {'error': {'code': 'manifest_unavailable', 'message': 'the sealed
    execution manifest is not readable'}}
1 failed, 5 deselected in 31.40s
```

（完整日志：`E:\Orchara\.scratch\f13b\T101\j6-sixth-run.log`；根因 §7.5。）

```powershell
# 修复后：Go 回归测试 + J6 真实运行
go test ./internal/capabilities/ -run 'TestLoadAuthorizedRun' -v
```

```
=== RUN   TestLoadAuthorizedRunAllowsPublishedManifestSealedByItsPublisher
--- PASS: TestLoadAuthorizedRunAllowsPublishedManifestSealedByItsPublisher (0.02s)
=== RUN   TestLoadAuthorizedRunStillRefusesPublishedManifestDigestMismatches
--- PASS: TestLoadAuthorizedRunStillRefusesPublishedManifestDigestMismatches (0.02s)
    --- PASS: .../run_and_snapshot_digests_disagree (0.00s)
    --- PASS: .../sealed_bytes_do_not_hash_to_the_run_digest (0.00s)
=== RUN   TestLoadAuthorizedRunKeepsTheOwnerEqualityForNonPublishedRuns
--- PASS: TestLoadAuthorizedRunKeepsTheOwnerEqualityForNonPublishedRuns (0.02s)
PASS
ok  	orchara/platform/internal/capabilities	1.597s
```

J6 修复后真实运行：`6 passed in 87.93s`（证据运行，J6 49 断言全过；见 §6.1）。

## 7. 已知产品缺口与诚实边界（blocked / not_run）

### 7.1 已实测 `blocked`：F08 聊天正文当 Run 输入 → `node_input_invalid`

- 链路：`POST /requests` 的 message 任意字符串 → 按字节封存为 `request_input` artifact
  （`internal/requests/submit.go:131`），并作为 Run 输入链接（`submit.go:646-690`）；执行器要求 JSON 对象
  （`internal/presetexec/linear.go:177-187`，图路径 `graphrun.go:929-937` 同点）→
  `Run.state=failed`、`error_code=node_input_invalid`，发生在 sandbox 启动之前。
- 本票实测：J1b 的 Run failed/`node_input_invalid`/无 output artifact/node-results 空；封存输入就是纯文本。
- 影响 AC4 里「自然语言请求→结果」的完整演示；**J1 用规格内的显式固定版本入口 + JSON 任务文档**
  （IMPLEMENTATION-DETAILS §5）跑通并计 passed，**不是**绕过正式接口，也**不是**测试写法问题。
  该缺口属于 F08↔F04B，本票不修（修它要改 F08 的输入通道或 F04B 的输入契约，超出验收票面）。

### 7.2 `not_run`：不带 `application_version_id` 的自然语言路由

- 需要 `model_profile_versions.config->>'route_control' = true` 的路由控制模型；`ModelConfig` 是强类型
  （`internal/modelregistry/modelregistry.go:92-102`），没有公开注册入口，既有测试只能 SQL 播种
  （`t024.seed_candidate`）。本票不做「非 API 部署播种 + 路由」的组合，故该路径 **not_run**，
  原因写在此处（J1b 用显式版本隔离了 Run 输入缺口，不引入路由模型变量）。

### 7.3 部署/测试缝（已在旅程记录中显式标注）

- **J2 工具↔连接器绑定**：`platform.tool_versions.connector_versions` 用 SQL 绑定
  （公开 `POST /tools` 接受 `connector_version_ids`，但需要新建工具版本并走构建/评审链，代价远超本票）。
  断言不信任该行，而信任回环端点请求日志 + PG。
- **J1 撤销边界的第二个版本**：用既有 T074 助手 `f08.publish_version`（真实编译 manifest + SQL 版本行）
  只为负边界；主旅程版本走真实五类评测 + 提交 + 审批。
- **J2 capability 审批过期**：产品是决策路径惰性检查（无 sweeper），故用 `expires_at = now() - 1m`
  的时间效果验证；不声称 30s 自动过期（那是 F04B node approval 的 sweeper 语义）。
- **J3 候选过期**：同样用时间效果推进 `expires_at`。

### 7.4 环境性 not_run（继承批次诚实边界）

- 真实厂商模型/真实模型质量：只有 `dev-stub`，**not_run**；旅程只证明真实执行/存储/权限链路。
- 真实第三方连接器供应商：只有本机回环 HTTP，**not_run**；「外部写真实发生」的真值是回环端点的
  请求日志，不是供应商后台。
- 真实 embedding：确定性 stub（`embedding` 是 dev-stub），**not_run**；检索链路真实。
- 真实浏览器 DOM / Playwright：本票不涉及页面，**not_run**（票面「不重复实现功能页面」）。
- J6 不调用模型（图是 `start -> python -> end`）：真实模型下的 agent→tool 行为 **not_run**，不声称。

### 7.5 已修复的 P1 产品缺陷（J6 验收运行发现）：published Run 的 manifest 属主规则

- **缺陷**：`internal/capabilities/service.go` 的 `loadAuthorizedRun` 原先要求 sealed execution-manifest
  artifact 的 `owner_id` 等于 Run 的 `owner_id`。debug Run 成立（作者自己封存）；但 F08/T074 的 published
  Run 的 execution snapshot 复用**版本**的 manifest artifact（`internal/requests/submit.go:632-641`），
  该 artifact 由发布者在 `SubmitPublication` 时封存（`internal/catalog/publication.go`，owner-only：
  `callerID == application owner`），而 Run 的 owner 是**被授权请求者**。结果：任何非发布者提交的已发布
  Run 调用**任何** capability（model/tool/connector）都会被 503 `manifest_unavailable` 拒绝。
- **真实红证据**（J6 第一次真实运行，修复前）：
  ```
  POST /internal/v1/runs/{id}/capabilities (worker mTLS, kind=connector)
  -> 503 {"error":{"code":"manifest_unavailable",
                  "message":"the sealed execution manifest is not readable"}}
  ```
  完整日志：`E:\Orchara\.scratch\f13b\T101\j6-sixth-run.log`。
- **修复**（`internal/capabilities/service.go`）：只对 `run.Kind == "published"` 跳过 artifact-owner 相等；
  artifact 必须 ready、artifact digest == snapshot digest、字节 sha256 == `runs.manifest_sha256` 全部保留
  （`DispatchVersionTx` 已在创建 Run 的事务里用 ACL/撤销/版本摘要校验过版本）。
- **Go 单元测试**（`internal/capabilities/manifest_owner_test.go`，同包，`go test ./internal/capabilities/`）：
  (a) published + 不同 artifact owner + 摘要匹配 → 允许；(b) published + snapshot/run 摘要不一致、
  字节摘要不一致、artifact 非 ready → 仍 503 `manifest_unavailable`；(c) 非 published（debug）+
  不同 artifact owner → 仍 503。
- **绿证据**：修复后 J6 真实运行 49 条断言全过（`E:\Orchara\.scratch\f13b\T101\evidence-run.log`）；
  `/runs/{id}/capabilities` 返回 `awaiting_approval` 并最终 succeeded，端点恰好 1 次写。
- **影响面**：所有 published Run 的 capability 调用（不只 connector）；本修复恢复的是产品本应具备的行为
  （发布应用被授权者可运行其 agent/model/tool 节点），不新增权限——仍需 live attempt token、冻结
  manifest 摘要与版本 ACL。

### 7.6 `blocked`：已发布图本身无法发出 connector operation（J6b 实测）

- 实测（J6b）：同一服务 key、同一已发布版本、真实 supervisor 执行图到 succeeded，manifest 确实携带
  write policy，但该 Run 的 `platform.operations` **0 条 connector operation**，回环端点物理写计数不变。
- 原因（逐处代码）：
  - `internal/presetexec/graphrun.go` 的节点分派表只有 start/end/python/agent/call_app/condition/join/
    approval/model/retrieve，没有 connector 节点；
  - `internal/presetexec/agent.go` 的 agent tool call 恒为 `capability_kind="tool"`，然后跑 preset tool
    sandbox（`runAgentToolSandbox`），不读 tool 的 `connector_versions`；
  - `internal/capabilities/service.go` 的 tool 分支只解析 `tool_versions`（state/approved/input_schema），
    不把绑定的 connector policy 变成一次 connector 调用；
  - `internal/presetexec/approval.go` 明确业务审批「never authorizes a later external write」。
- 修它需要改图节点分派、工具事实（policy ids + 参数映射）、契约（workflow/manifest schema）以及
  评测对「审批停住」的语义（`internal/evaluation/collect.go` 会把 parked case 记 `run_timeout`/`not_run`，
  发布门禁 `internal/catalog/publication.go` 随后拒绝）。本票不做产品改造，只把该子缺口如实记 `blocked`；
  AC2 的授权/审批/恰好一次链由 J6 在平台/授权边界证明。

## 8. 修了 / 未修分栏

**本票修了（交付物）**

| 项 | 文件 |
|---|---|
| 5 条原旅程的真实实现（HTTP/mTLS/CLI + 独立真值 + 失败/恢复边界） | `product/tests/f12/journeys/j101_requests.py`、`j101_approvals.py`、`j101_memory.py`、`j101_knowledge.py`、`j101_service_accounts.py` |
| **J6 三合一外部写旅程 + J6b 图分发缺口**（真实发布链、服务 key+grant、负责人审批、恰好一次回环写、真实 finalizer） | `product/tests/f12/journeys/j101_service_key_write.py` |
| **P1 产品缺陷修复**（published Run 的 sealed manifest 属主规则） | `product/internal/capabilities/service.go` |
| **缺陷的 Go 回归测试**（published 允许 / 摘要三类拒绝 / debug 规则不变） | `product/internal/capabilities/manifest_owner_test.go` |
| 真实栈支持（每测试独立库/API/mTLS/对象目录；证书、connector secret、证据记录器） | `product/tests/f12/t101_support.py` |
| 旅程用例（8 条 journey 记录） | `product/tests/f12/test_t101_journeys.py` |
| 机器可读证据（真实运行的 8 条记录） | `product/reports/f12/T101-journeys.json` |
| 本报告 + PROGRESS 小节 + 编排者摘要 | `product/reports/f12/T101-journeys.md`、`product/PROGRESS.md`、`E:\Orchara\.scratch\f13b\T101-REPORT.md` |

**未修（明确归属，不在本票范围）**

- F08↔F04B 聊天正文输入缺口（F08/F04B 产品设计；本票只实测记录，J1b `blocked`）。
- 图分发 connector operation（J6b `blocked`；需要图 DSL/执行器/契约/评测语义改造，见 §7.6）。
- 路由控制模型经公开 API 注册（F08/F06 模型注册；本票 not_run）。
- 真实厂商模型/连接器/embedding（F14/T090 等环境条件）。
- `tests/f01` 与其它套件同跑的 34 个 collection error 与 2 个页面标记陈旧失败（既有缺陷，见 §6.3）。
- 服务账号无 list/rotate key 端点、scope 词表固定（F01/F02A 治理）。

## 9. 未解风险

1. **共享 runsc 抖动**：真实栈在共享 WSL2/runsc 上会有已知 `the attempt lease was lost while the sandbox ran`
   抖动；本票的真实执行用例在持锁串行下本轮全绿，但未加通用重试（避免掩盖不稳定）。
2. **依赖既有 helper 的语义**：J2/J3 复用 T050/T051/T054/T058/T063 的 helper（它们是既有票的真实栈缝），
   若那些 helper 的语义随产品演进变化，本票需要同步——本轮以真实 HTTP/PG/对象真值兜底，不依赖 helper 的断言。
3. **f01 既有失败**：若合并者把 `tests/f01` 全绿当作门禁，需要先修页面标记测试（与 T101 无关）。
4. **审批过期无 sweeper**：即时性依赖决策路径；没有后台自动过期的证据，报告已声明。
5. **每测试独立真实栈**：本票套件 6 个测试各建一个库/API（约 88s），换来的是与既有票一致的隔离；
   若 CI 资源更紧，可改模块级栈但需解决 golden draft/tool version 的全局唯一约束。
6. **AC2 的边界（2026-11-07 收尾更新）**：AC2 已由 J6 在**平台/授权边界**满足（同一次真实外部写：
   scope + grant + 负责人审批 + 恰好 1 次写 + 5 条负边界）。**不要**把 J6 读成「已发布图自己发出写」——
   图分发缺口记在 J6b `blocked`（§7.6），需要产品改造才能满足那句话；J1b 仍 `blocked`。
7. **P1 产品修复的影响面**：`internal/capabilities/service.go` 的 owner 相等规则放宽只作用于
   `run.Kind == "published"`；Go 回归测试覆盖「published 允许 / 三类摘要拒绝 / debug 仍拒」。风险在于
   这是本票唯一的产品代码改动，合并评审应把它当成缺陷修复而不是旅程测试的尾部改动；若评审不接受，
   回退该处即回到「published Run 的 capability 全 503」的缺陷状态（J6 会失败）。
8. **J6 的 worker seam**：写操作由真实 mTLS worker capability 调用发出（Run 的活动 attempt token），
   与 T048/T050/J2 相同；证据里显式记为 `worker_seam`，不写成图节点发出。
9. **已修复的凭据泄漏事故**：原提交的 `T101-journeys.json` 第 841 行含一次性测试 API key 明文
   （`orchara_gATw…`，所属测试库已 drop，已不可认证），`:316/:371/:386` 的请求日志 detail 含
   `x-connector-secret` 值。2026-11-07 复核后在**产生证据的源头**脱敏并重新生成（`t101_support._short/record/
   persist`、`j101_service_accounts.py:105` 改为脱敏形状），本文件与 JSON 不再含明文；**git 历史仍保留该
   已失效字符串，未改写历史**（分支已推送、issue tracker 引用 SHA）。细节见 `reports/f12/REVIEW-FIXES.md`。
   本次 J6 证据重生成后已再跑 `Select-String` 检查：只出现 `<redacted>`。
10. **状态改写事故已修复**：原 `journey()` 在正常退出时把 `not_run` 隐式改成 `passed`；现在 `passed`
    必须由旅程体在所有断言之后显式 `mark_passed()`，且 `persist()/record()` 强制校验
    `passed` 至少有一条断言。空旅程现在是 `not_run`，回归测试见 `tests/f12/test_journey_status.py`。

## 10. 复现步骤

```powershell
cd E:\Orchara-wt\t101b\product
python tools/pg_dev.py status                     # running: true
$env:GOCACHE='E:\orchara-toolchains\gocache'; $env:GOMODCACHE='E:\orchara-toolchains\gomodcache'
$env:PATH="E:\orchara-toolchains\go\bin;$env:PATH"
New-Item -ItemType Directory E:\Orchara\.scratch\f13b\locks\realstack   # 与并行票串行
$env:T101_WRITE_EVIDENCE='1'
python -m pytest tests/f12/test_t101_journeys.py -q
python -m pytest tests/f12 -q
go build ./... ; go vet ./... ; go test ./...
gofmt -l internal cmd tools
python tools/sync_spec.py --check
Remove-Item E:\Orchara\.scratch\f13b\locks\realstack
```

> 证据 JSON 只在 `T101_WRITE_EVIDENCE=1` 时写回 `reports/f12/T101-journeys.json`；
> 普通 pytest 运行不会改写已提交证据（与 T093 的 `F12_WRITE_EVIDENCE` 约定一致）。
> J6 的评测/写审批依赖 WSL2+runsc 与 PostgreSQL 隔离集群；缺任一时该测试按真实栈规则失败/skip，
> 不会用种子结果顶替。
