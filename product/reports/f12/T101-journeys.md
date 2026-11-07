# T101（GitHub #102）请求、审批、记忆、知识及服务账号五旅程真实复现报告

- 状态：**`passed`**（判定口径：手段真实 + 覆盖完整 + 裁决诚实；五条旅程 J1/J2/J3/J4/J5 全部真实跑通，
  其中「请求→Run（自然语言）」作为 **J1b 子记录记 `blocked`** —— 这是 F08↔F04B 的**产品缺口**，
  不是本票测试写法或环境问题；本票没有为了全绿而绕开正式入口或修改产品语义）。
- 分支：`t/t101-data-journeys`；worktree：`E:\Orchara-wt\t101`。
- 基线：`integration/f12` @ `60d9de0`；完成前 `git merge integration/f12` = **Already up to date**，
  最终代码 HEAD 见 `E:\Orchara\.scratch\f12\T101-REPORT.md`。
- 证据：`reports/f12/T101-journeys.json`（6 条 journey 记录：真实入口/用时/独立真值/断言点/失败原因）。
- 环境条件：`ORCHARA_PROFILE=dev-stub`（**无真实厂商模型/凭据**）；WSL2 + runsc 真实执行；
  PostgreSQL 16.4（隔离集群 `127.0.0.1:55432`，每个测试自建库）。stub/real 分界见 §7。

## 1. 环境 profile（实测）

| 项 | 实测值 | 命令/来源 |
|---|---|---|
| 分支 / 基线 | `t/t101-data-journeys` / `integration/f12` @ `60d9de0` | `git rev-parse` |
| Go | go1.27.3 windows/amd64 | `go version` |
| Python / pytest | Python 3.12.2 / pytest 9.0.3 | `python --version`、`python -m pytest --version` |
| PostgreSQL | `running: true`，port 55432，server 16.4 | `python tools/pg_dev.py status` |
| 沙箱 | `runsc version release-20241118.0`（WSL2 Ubuntu-24.04） | `wsl -d Ubuntu-24.04 -u root -e runsc --version` |
| 模型 | 只有 `dev-stub`；无厂商端点/凭据 | 部署环境（`ORCHARA_PROFILE=dev-stub`） |
| 浏览器 DOM | 本票**不使用**页面/DOM（票面明确「不重复实现功能页面」） | — |
| 真实栈互斥 | 全程持有 `E:\Orchara\.scratch\f12\locks\realstack`（与 T100 串行） | 目录锁 |

## 2. 逐 AC 对照

| AC（issue #102 原文） | 结论 | 证据 |
|---|---|---|
| 1. 请求结果、写审批、个人记忆、知识摄取、服务账号五旅程真实完成 | **满足（J1/J2/J3/J4/J5 全 passed；J1b 按实际行为记 blocked）** | `T101-journeys.json`：J1 16 断言、J2 34、J3 28、J4 19、J5 24，全部真实 HTTP/真实 PG/真实 supervisor+maintenance/真实对象目录；无 mock 替身 |
| 2. scope/ACL/人类批准同时满足 | **满足** | J5：同一调用在「scope + read grant + 负责人启用」三者齐备时 200；去掉任一 → 404（无 ACL）/403（越 scope）/401（负责人禁用连带撤销）。J2：人类批准绑定 `params_sha256` + approver=Run owner（ACL）+ admin/陌生人被拒。J3：人类 confirm 决定 + owner-only 队列 + 服务 key `403 personal_scope_forbidden`。详见 §5 |
| 3. 缺判定条件不计 passed | **满足** | 自然语言请求旅程 J1b 记 `blocked`（非 passed）；不声称真实模型质量、真实外部供应商、真实 embedding；未跑的「无显式版本的自然语言路由」写 `not_run` 及原因 |
| 4. 通过端到端入口实际演示；失败、当前授权与重试/恢复边界有断言 | **满足** | 越权：跨用户 Run/artifact 404、陌生人审批 404、admin 批准 403、服务 key 个人数据 403；拒绝/过期/取消：审批 reject→失败、expiry→失败、cancel→invalidated；重复投递：同幂等键请求不建第二个 Run、同 logical key 不二次外写、丢回执重复 invoke 不重发；恢复：resume 恰好一次、丢回执→`needs_reconciliation`→证据 reconcile 成功且只 1 次物理写 |
| 5. 记录版本、输入/hash、命令、原始结果与 profile；stub/real 分报，缺条件 blocked、未运行 not_run | **满足** | 每条 journey 记录 entry/truth/assertions/facts；报告 §1/§4/§6/§7 含版本、输入 hash、命令、原始 tail、profile；`UV_*`/真实模型/真实外部供应商 not_run 均已列明 |

## 3. 逐旅程结果表（来自 `T101-journeys.json`）

| journey_id | 状态 | 真实入口（摘要） | 用时 | 独立真值来源 | 断言数 | 失败原因 |
|---|---|---|---|---|---|---|
| `J1-request-to-run-result` | **passed** | `POST /api/v1/requests` 显式 `application_version_id` → `cmd/supervisor exec --once` → `GET /runs/{id}`、`GET /requests/{id}`、`GET /artifacts/{id}/content` | 4.67s（版本发布在 J1b 前置一次完成） | 封存 request_input 对象字节 + `platform.runs` 行 + `summary.csv` 对 `tests/support/t049.py` 的独立 Decimal 复算 | 16 | — |
| `J1b-natural-language-request` | **blocked** | 同一显式版本入口，`message` 为自然语言正文 → 真实 supervisor | 10.27s（含一次性真实五类评测发布） | 封存 request_input 对象字节（纯文本）+ `platform.runs.state/error_code` + node-results 空数据行 | 5 | F08↔F04B 产品缺口：正文原字节当 Run 输入 → `node_input_invalid`（§7.1） |
| `J2-write-approval` | **passed** | 管理员 `POST /admin/connectors`（write 风险）→ worker mTLS `invokeCapability` → owner `POST /approvals/{id}/decision` → supervisor 恢复租约 → 真实回环外写 | 7.00s | 回环连接器自己的请求日志 `server.records`（物理写次数）+ `platform.approvals/operations/operation_attempts` + 封存 proposal 对象字节 | 34 | — |
| `J3-personal-memory` | **passed** | worker mTLS `POST /internal/v1/runs/{id}/memory-candidates` → `GET /memory-candidates` → `POST .../decision` → 3 次真实 agent Run 的 context trace 与封存模型请求 → `PATCH/DELETE /memories/{id}` | 19.26s | `platform.memory_candidates/memories` + 封存 body 对象字节 + run 的 context-trace 与 model request artifact | 28 | — |
| `J4-knowledge-ingestion` | **passed** | `POST /knowledge-bases` → 真实 signed-link 上传 + `artifact_finalize` → `POST .../documents` + `cmd/maintenance run --once --kinds document_ingest` → 检索 → `GET /knowledge-citations/{chunk}` → `DELETE /knowledge-documents/{id}` | 2.24s | 封存源对象字节的 sha256 与 rune 切片**由测试重算**（不是 chunk 行回读）+ `platform.knowledge_documents/chunks/background_jobs` | 19 | — |
| `J5-service-account` | **passed** | admin `POST /admin/service-accounts` → `POST /admin/api-keys` → 仅 Bearer 的 HTTP 调用 → `PATCH /admin/principals/{id}`（禁用负责人） | 5.82s | `platform.api_keys`（仅 SHA-256、scopes、expires_at、revoked_at） | 24 | — |

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

## 5. scope / ACL / 人类批准「三者同时满足」是怎么断言的

**J5 的字面三重门控断言**（同一调用，缺一即拒，这是 AC2 的核心证据）：

| 条件 | 齐备时 | 去掉该条件 | 证据 |
|---|---|---|---|
| **scope**：key 带 `application:read` | `GET /applications/{id}` → **200** | 同主体、有 grant，但 key 只带 `application:write` → **403 `insufficient_scope`**（scope 先于 ACL） | 响应码 + DB scope 行 |
| **ACL**：服务主体持有未过期 read grant | 同上 **200** | 有 scope、无 grant → 列表空、详情 **404 `resource_not_found`**（与不存在同形，不泄漏枚举） | 响应码 + `platform.grants` 行被创建/删除 |
| **人类**：负责人启用 | 同上 **200** | `PATCH /admin/principals/{responsible}` 禁用 → **401 `invalid_key`**，且独立读 key 行 `revoked_at` 已由禁用事务写入（SECURITY.md §1：禁用负责人连带撤销其服务账号 key） | `platform.api_keys` 行前后对比 |

**J2 的「人类批准」三重绑定**：approval 只对**精确参数**（`params_sha256` 与服务端封存 proposal 一致）、
**精确 approver**（Run owner；陌生人 404、admin 403 `approver_mismatch`）、**未过期且未取消**的 approval 生效；
批准后仍要 supervisor exit 才生成 1 个 resume，恢复调用才真正外写。即「人类批准」不是单一 200，
而是参数绑定 + 身份绑定 + 时序/状态绑定同时成立。

**J3 的人类专属边界**：候选/记忆只能由人类 owner 读取和决定；服务 key 即使带 `application:read/write`
也是 **403 `personal_scope_forbidden`**；陌生人决定 404；confirm 是唯一产生记忆的路径（pending 不是记忆）。

**J4 的当前授权**：知识库/引用按 owner ACL 当前状态判定；删除后引用立即 404 且不读缓存。

## 6. 真实命令与原始 tail

所有命令均在 `E:\Orchara-wt\t101\product` 下运行；工具链环境变量：
`GOCACHE=E:\orchara-toolchains\gocache`、`GOMODCACHE=E:\orchara-toolchains\gomodcache`、PATH 前置 Go。

### 6.1 本票套件（证据运行，`T101_WRITE_EVIDENCE=1` 写 JSON）

```powershell
$env:T101_WRITE_EVIDENCE='1'
python -m pytest tests/f12/test_t101_journeys.py -q
```

原始 tail：

```
.....                                                                    [100%]
5 passed in 76.88s (0:01:16)
```

### 6.2 全量 F12 套件（不破坏 T093/T095/T096/T098）

```powershell
python -m pytest tests/f12 -q
```

原始 tail：

```
........................................................................ [ 23%]
........................................................................ [ 46%]
........................................................................ [ 69%]
........................................................................ [ 92%]
.........................                                                [100%]
313 passed in 235.96s (0:03:55)
```

（基线 308 + 本票 5 = 313，全部保留。）

最终提交树复跑（同一命令）：

```
........................................................................ [ 92%]
.........................                                                [100%]
313 passed in 232.72s (0:03:52)
```

### 6.3 服务账号/身份相关：`tests/f01` **单跑**

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

`go test ./...` 原始 tail（exit 0）：

```
go_test_exit=0
ok  	orchara/platform/internal/context	2.436s
ok  	orchara/platform/internal/db	1.749s
ok  	orchara/platform/internal/deletion	2.213s
ok  	orchara/platform/internal/diagnostics	2.246s
ok  	orchara/platform/internal/embedding	1.783s
ok  	orchara/platform/internal/evaluation	2.432s
ok  	orchara/platform/internal/execution	0.778s
ok  	orchara/platform/internal/health	2.369s
ok  	orchara/platform/internal/identity	3.944s
ok  	orchara/platform/internal/jcs	3.284s
ok  	orchara/platform/internal/jsonschema	2.631s
ok  	orchara/platform/internal/knowledge	7.967s
ok  	orchara/platform/internal/memory	3.894s
ok  	orchara/platform/internal/modelproxy	2.855s
ok  	orchara/platform/internal/modelregistry	3.832s
ok  	orchara/platform/internal/objectstore	2.150s
ok  	orchara/platform/internal/policy	3.424s
ok  	orchara/platform/internal/presetexec	6.278s
ok  	orchara/platform/internal/requests	1.464s
ok  	orchara/platform/internal/routing	2.761s
ok  	orchara/platform/internal/sandbox	4.218s
ok  	orchara/platform/internal/supervisor	2.253s
ok  	orchara/platform/internal/templates	2.581s
ok  	orchara/platform/internal/tools	4.995s
ok  	orchara/platform/internal/toolversions	2.371s
ok  	orchara/platform/internal/uuid	2.157s
ok  	orchara/platform/internal/workflow	2.398s
?   	orchara/platform/internal/toolruns	[no test files]
?   	orchara/platform/probes/f10_pdf_compare/go_baseline	[no test files]
?   	orchara/platform/probes/f15a_objectstore/localfs_probe	[no test files]
```

（完整日志：`E:\Orchara\.scratch\f12\T101\gate-*.log`、`evidence-run.log`。）

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

## 8. 修了 / 未修分栏

**本票修了（交付物）**

| 项 | 文件 |
|---|---|
| 5 条旅程的真实实现（HTTP/mTLS/CLI + 独立真值 + 失败/恢复边界） | `product/tests/f12/journeys/j101_requests.py`、`j101_approvals.py`、`j101_memory.py`、`j101_knowledge.py`、`j101_service_accounts.py` |
| 真实栈支持（每测试独立库/API/mTLS/对象目录；证书、connector secret、证据记录器） | `product/tests/f12/t101_support.py` |
| 旅程用例（6 条 journey 记录） | `product/tests/f12/test_t101_journeys.py` |
| 机器可读证据（真实运行的 6 条记录） | `product/reports/f12/T101-journeys.json` |
| 本报告 + PROGRESS 小节 + 编排者摘要 | `product/reports/f12/T101-journeys.md`、`product/PROGRESS.md`、`E:\Orchara\.scratch\f12\T101-REPORT.md` |

**未修（明确归属，不在本票范围）**

- F08↔F04B 聊天正文输入缺口（F08/F04B 产品设计；本票只实测记录）。
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
5. **每测试独立真实栈**：本票套件 5 个测试各建一个库/API（约 77s），换来的是与既有票一致的隔离；
   若 CI 资源更紧，可改模块级栈但需解决 golden draft/tool version 的全局唯一约束。

## 10. 复现步骤

```powershell
cd E:\Orchara-wt\t101\product
python tools/pg_dev.py status                     # running: true
$env:GOCACHE='E:\orchara-toolchains\gocache'; $env:GOMODCACHE='E:\orchara-toolchains\gomodcache'
$env:PATH="E:\orchara-toolchains\go\bin;$env:PATH"
New-Item -ItemType Directory E:\Orchara\.scratch\f12\locks\realstack   # 与并行票串行
$env:T101_WRITE_EVIDENCE='1'
python -m pytest tests/f12/test_t101_journeys.py -q
python -m pytest tests/f12 -q
python -m pytest tests/f01 -q                     # 单独进程（既有 2 个页面标记失败）
go build ./... ; go vet ./... ; go test ./...
gofmt -l internal cmd tools
python tools/sync_spec.py --check
Remove-Item E:\Orchara\.scratch\f12\locks\realstack
```

> 证据 JSON 只在 `T101_WRITE_EVIDENCE=1` 时写回 `reports/f12/T101-journeys.json`；
> 普通 pytest 运行不会改写已提交证据（与 T093 的 `F12_WRITE_EVIDENCE` 约定一致）。
