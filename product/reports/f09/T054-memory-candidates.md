# F09 T054 验收：用户确认候选后才保存个人长期记忆

- 任务：F09 / GitHub Issue #55（票 `T054`，规划 ID F09.01），分支
  `t/t054-memory-candidates`（worktree `E:\orchara-worktrees\t054`）；
  父门禁 #53–#68 集成批次（`integration/f13a-f15a-f09-f10`）。
- 状态：**`passed`**（本票范围 = `proposeMemoryCandidate`（worker mTLS）→
  `listMemoryCandidates` → `decideMemoryCandidate`（confirm/reject）的候选确认垂直切片 +
  `createMemory` / `listMemories` 真实读模型 + `/memories` 页面）。真实 PostgreSQL 16.4 +
  真实编译二进制 + 真实 HTTP/mTLS + 真实 sealed 对象字节 + Node 执行页面逻辑；无 mock、
  无跳过。
- 边界（如实）：真实 worker 自动抽取器不在本构建内（本票用真实 supervisor claim + 真实
  worker 证书调用真实内部端点，正文由测试提供；抽取策略属 F04A/T043 的 agent 循环）；
  浏览器 DOM 驱动、后台过期清扫进程、记忆编辑/删除（T058）、运行时召回（T063）、Mem0 对照
  （T067）、记忆共享均为 `not_run`/后续票；全量 `pytest tests` 由 orchestrator 在合并后运行。

## 0. 工作记录（简短）

1. 读 T054 票面、BRIEF、`notes/f09-f10-contract.md`（§1 契约形状、§2 表、§5 seam）、
   `contracts/openapi.json` 5 个 operation、`contracts/database.sql:297-316`、
   `ROUTING-MEMORY-KNOWLEDGE.md` §2、F01 的 `handleListMemories` 诚实空列表、
   `internal/approvals`（封存提案/决定/过期/所有权）、`internal/capabilities`
   （live attempt 复核 SQL）、`internal/catalog`（ACL/keyset cursor/limit）、
   `internal/modelregistry`（服务端封存 artifact 的真实写法）、迁移 0009/0014/0015/0026/0032
   头部约定与 `tests/support/{stack,fixtures}.py`；
2. TDD 垂直切片（每片先红后绿）：
   - 迁移 0033 的表/列/键/权限/重复 migrate → 目录断言（4 用例）；
   - propose：真实 worker mTLS + run live attempt → 201 契约字段 + 封存字节 + 作用域来自
     run（红：404；绿：通过）；同 logical_key 重试/冲突、worker-only、stale attempt、
     run 非 running；
   - list candidates：owner-only、真实正文、keyset 分页、application 过滤、契约校验边界、
     他人/管理员 404 语义、服务账号 403；
   - decide：确认恰好一条记忆（复用候选正文、无第二个对象、`confirmed_by`/`confirmed_at`/
     revision=1）、新 key/同 key 重复确认同 memory、同 key 相反决定 409、拒绝无副作用、
     过期边界（仅差 `expires_at` 的两个候选）、失败决定不收据、epoch bump；
   - createMemory / listMemories：直接写入 + 真实读模型 + 隔离 + 分页 + 输入/ACL 边界；
   - 失败/恢复：删除 sealed 字节 → 列表 503（绝不造空文本）、确认 503 且候选保持 pending、
     恢复字节后同一行可读/同一候选可确认；
   - 页面：结构 + Node 执行纯函数（先红：页面不存在；绿：3 用例）；
   - 并发：两个 propose 同 logical_key → 同一候选/一条 artifact；两个不同 key 同时确认 →
     同一 memory（并据此补上 23505 竞态恢复分支）。
3. 跑门禁（§4），写 ADR 0008/本报告/PROGRESS；按 feat/test/docs 提交；未 push、未关 issue、
   未改 `docs/spec/**`/`contracts/**`。

## 1. 交付内容

### 1.1 迁移 0033（契约两张表 + 记录在案的列级偏离）

`db/migrations/0033_memory_candidates.sql`：

- `platform.memory_candidates`：契约列（id/owner_id→users/application_id→applications/
  body_artifact_id→artifacts/source_run_id→runs/state/expires_at）+ 偏离列
  `logical_key`（NOT NULL，`UNIQUE(source_run_id, logical_key)`）、`created_at`、
  `decided_at`、`decided_by`、`decision_key`（均非契约，T054 负责）；
- `platform.memories`：契约列 + `confirmed_by`（NOT NULL→users，T054 负责，
  记录确认者）；`revision DEFAULT 1` 为 T058 ETag/CAS 的起点；
- `memories_scope`（契约索引）+ `memories_owner_recent`、`memory_candidates_owner_recent`、
  `memory_candidates_scope`；
- 不建 `platform.memory_embeddings`：运行时召回与索引属 T063（#64），本集群无 pgvector
  （0009 记录同一环境边界），迁移头部写明 owning ticket；
- 无“来源 run”第二列：provenance 单链 `memories.candidate_id → memory_candidates.source_run_id`；
- 头部按 house 约定逐条列偏离与 owner、边界、`orchara_worker`/PUBLIC 零 `platform` 权限；
  重复 `admin migrate` no-op，`0001..0033` 连续无缺口。

### 1.2 `internal/memory`（候选 + 记忆存储）

- `Propose`：worker 收到的 `{attempt_token, logical_key, text}`；服务端复核
  `run_attempts` 是该 run 的 live lease（`exited_at/revoked_at IS NULL`、lease 未过期、
  等于 `jobs.current_attempt_token`），409 `run_not_running`/`run_cancelled`，
  403 `stale_attempt`/`permission_revoked`/`personal_scope_forbidden`；
  应用范围来自 `runs.execution_snapshot_id → execution_snapshots.application_id`。
  正文 UTF-8 ≤4000 字符，经 `objectstore.PutSealed` 封存（purpose `memory_candidate`，
  sha256/size 复核，事务回滚删对象）；`(run, logical_key)` 唯一，同文重试同一行、
  异文 409 `idempotency_conflict`，并发 insert 的 23505 走“回读胜者”恢复；
- `ListCandidates`：先惰性 `pending→expired`（同一 owner），再按
  `(created_at DESC, id DESC)` keyset + application 过滤；投影再兜
  `CASE WHEN pending AND expires_at<=now() THEN 'expired'`；正文逐条读 sealed 对象并校验摘要，
  读不出 → 503 `object_store_unavailable`（**不返回空文本**）；
- `Decide`：`platform.idempotency_keys` 收据（principal+POST+path+key，JCS 决定摘要）先 claim，
  重复 key 回放已存 body、异决定 409；行锁（`FOR UPDATE`）后判过期/状态；确认
  `INSERT ... ON CONFLICT (candidate_id) DO NOTHING`，正文复用候选 artifact（确认前重读校验），
  同事务 bump `policy_epochs` 的 `memory:<owner>:<app>`；拒绝只改状态；
  失败决定事务回滚（不收据），过期迁移单独提交；
- `Create`/`List`：`createMemory` 校验 `{application_id, text 1..4000, confirmed:true}`，
  用 `catalog.Reader.GetVisible` 复用应用 ACL（未知/他人/归档 → 404），自己封存
  `memory_body` 正文；列表 owner-only、`deleted_at IS NULL`、keyset + application 过滤。

### 1.3 HTTP（5 个契约 operation）

- `POST /api/v1/memories`（201 `Memory` 精确四字段）、`GET /api/v1/memories`
  （`{items,next_cursor}`，替换 F01 诚实空列表）、`GET /api/v1/memory-candidates`、
  `POST /api/v1/memory-candidates/{candidate_id}/decision`（`Idempotency-Key` 必填，
  缺失 422 + `details.field`；200 `MemoryCandidateDecision`）、
  `POST /internal/v1/runs/{run_id}/memory-candidates`（worker-only mTLS，201
  `MemoryCandidate` 精确五字段）；
- 五个端点在任何 body/存储访问前拒绝服务凭据：403 `personal_scope_forbidden`（契约列了
  `apiKey`，这是记录在 ADR 0008 §1 的刻意偏离）；
- 错误词表：沿用 `resource_not_found`/`validation_failed`/`forbidden`/`stale_attempt`/
  `run_not_running`/`run_cancelled`/`personal_scope_forbidden`/`idempotency_conflict`/
  `object_store_unavailable`/`invalid_cursor`；新增三个聚合码
  `memory_candidate_{expired,rejected,confirmed}`（对齐审批的 `approval_expired` 先例）。

### 1.4 页面

`apps/web/src/memories.html` + `assets/memories.js` + `server.go` 静态路由
（`GET /memories`、`GET /assets/memories.js`）：候选卡显示来源类别、应用范围、状态
（待确认/已确认/已拒绝/已过期）、过期时间、正文与确认/拒绝按钮；记忆卡显示 revision 与正文；
每次点击新 `Idempotency-Key` + CSRF；无默认选择；全部 `textContent`，不保留凭据或正文；
纯函数（`describeCandidate`/`describeMemory`/`decisionPayload`/`decisionConfirmationText`/
`decisionPath`/`candidatesListPath`/`memoriesListPath`/`newDecisionKey`/
`decisionResultText`）由 Node 执行断言。

### 1.5 配置

`ORCHARA_MEMORY_CANDIDATE_TTL`（`internal/config`，默认 7 天，允许 1m–365d；非法值拒绝启动），
经 `api.Deps.MemoryCandidateTTL` 注入 `memory.Service`。过期判定永远按存储的 `expires_at`，
与 TTL 配置无关。

## 2. AC 核对

| AC | 结果 | 证据（真实栈，非 stub） |
|---|---|---|
| 1. 重复确认返回同 memory | proven | `test_a_confirm_creates_exactly_one_memory_and_repeats_return_it`：同 key 回放、新 key 再次确认都返回同一 `memory_id`；PostgreSQL 恒为 1 条 `memories`、artifact 数不变（复用候选正文）；另断言 `revision=1`、`confirmed_by`=owner、`confirmed_at` 非空、候选 state=confirmed；页面的每次点击新 key 由 Node 用例覆盖。并发同 key 不同 key 双确认见 `test_two_concurrent_confirms_with_different_keys_create_one_memory` |
| 2. 拒绝/过期不生效 | proven | `test_a_reject_creates_nothing_and_a_later_confirm_is_refused`（拒绝 0 记忆、重复拒绝幂等、之后确认 409 `memory_candidate_rejected`、无 epoch）；`test_expiry_is_enforced_by_the_server_not_by_a_status_field`（只差 `expires_at` 的两个候选：列表把过期的持久化为 expired，确认/拒绝都 409 `memory_candidate_expired`，0 记忆、0 收据；未来候选确认成功） |
| 3. 服务账号不能写个人记忆 | proven | `test_a_service_credential_cannot_touch_personal_memory`：真实 service principal + scoped API key，对 `POST/GET /memories`、候选列表、决定全部 403 `personal_scope_forbidden`，同 key 的 `/auth/me` 仍 200（是授权决定而非坏请求）；F01 的 `tests/f01/test_t020_personal_boundary.py` 保留读侧并新增写侧拒绝；数据库层 `owner_id/confirmed_by → platform.users` 使服务主体结构上无法成为所有者 |
| 4. 真实端到端入口 + 失败/授权/重试恢复断言 | proven | 正式用户路径：真实 debug run → 真实 supervisor claim → 真实 worker mTLS propose → 真实 cookie+CSRF decide → 真实 `/memories` 页面与 `GET /api/v1/memories` 读回；失败/重试：删除 sealed 字节 → 列表 503、确认 503 且候选保持 pending、恢复字节后同一候选可确认（`test_a_confirm_never_commits_a_memory_without_readable_bytes`、`test_missing_memory_bytes_fail_closed_and_recover`）；当前授权：他人/管理员 404、stale token 403、非 running run 409、maintenance 证书 403（`test_the_decision_input_boundaries_are_located_and_leak_no_existence`、`test_the_internal_capacity_is_worker_only_and_the_attempt_is_proved`）；并发重试见两个 concurrent 用例 |
| 5. 记录版本/hash/命令/结果/环境，stub 与 real 分报 | proven（本报告 §4/§5） | 全部环境与输入 hash、原始命令尾巴、`not_run` 清单在 §4/§5/§6；本票无 stub 路径（模型/供应商未被调用） |

补充（票面 prompt 要求、非 AC）：`/memories` 页面显示候选来源/应用范围/过期时间/状态与
确认/拒绝动作 + 当前记忆列表，全部 textContent、无凭据（`tests/f09/test_t054_pages.py` 3 用例）。

## 3. 后续票 seam

| 票 | 本票已留的接口 |
|---|---|
| T058（#59）| `memories.revision` 从 1 起（强 ETag/CAS 无需回填）；`deleted_at` 为删除/tombstone 落点；`policy_epochs` 的 `memory:<owner>:<app>` 已由确认/直接写入 bump，更新/删除在同一事务继续 bump 即失效；owner-only SQL 形态可直接复用；`Memory` 投影已含 revision |
| T063（#64）| `internal/context.MemorySource` 仍是 `NoMemorySource`（候选永不可召回）；确认后的召回从这里接；`memories_scope`/`memories_owner_recent` 索引与 `revision` 是召回缓存键的一部分 |
| T067（#67）| vendor memory ID 必须映射平台 `memory_id` + `revision`；确认路径是唯一写入点（`UNIQUE(candidate_id)`），`confirmed_by`/`confirmed_at` 可作对照审计 |
| T055（F10）| 迁移号 0033 已被本票占用，F10 用 0034；本票未触碰 knowledge 表 |

## 4. 门禁命令与原始输出（2026-11-04）

环境：Windows 10 + PowerShell 7；`go version go1.27.3 windows/amd64`；
`Python 3.12.2`；`node v24.13.0`；PostgreSQL **16.4**（用户态集群 `127.0.0.1:55432`，
`python tools/pg_dev.py status` → `running: true, server_version: 16.4`）。

```text
$ python -m pytest tests/f09 -q
25 passed in 244.42s (0:04:04)

$ go build ./...
(no output, exit 0)

$ go vet ./...
(no output, exit 0)

$ go test ./...
ok  orchara/platform/internal/memory  1.978s
... (all packages ok; no FAIL; exit 0)

$ gofmt -l .
(no output, exit 0)

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest   (exit 0)

$ python -m pytest tests/tooling -q
21 passed in 9.85s

$ python -m pytest tests/f01/test_t020_personal_boundary.py -q
2 passed in 9.68s
```

套内细分：`test_t054_migration.py` 4、`test_t054_memory.py` 18（含 2 个并发用例）、
`test_t054_pages.py` 3。

### 合并结果（BRIEF §7.6）

```text
$ git merge integration/f13a-f15a-f09-f10
merge commit b7cfd6c（父 1b1c283 = 本票 docs 提交；父 7e0ccc2 = 集成 tip）
冲突：仅 product/PROGRESS.md → 保留 T054 与 T053 两节（无内容丢失）

# 合并后的最终树
$ python -m pytest tests/f09 -q
25 passed in 262.90s (0:04:22)
$ go build ./... ; go vet ./... ; go test ./... ; gofmt -l . ; python tools/sync_spec.py --check
全部 exit 0；gofmt 空；“no drift: spec copy and contract snapshot match the manifest”
```

注：集成 tip 的 T053 也新增了 `docs/adr/0008-f15a-object-store-compatibility.md`，与本票
`0008-f09-memory.md` 同号；按 orchestrator 约定（“merger renumbers”）两个文件各自自包含，
重编号由合并方执行。

## 5. 输入与版本

| 输入 | 版本 / SHA-256（前 16 位） |
|---|---|
| 基线 | `feat/f06-f04a-models-and-workflow` @ `7d0f442` |
| `db/migrations/0033_memory_candidates.sql` | `FE99AA0F88C20FCE…` |
| `internal/memory/{service,candidates,memories}.go` | `5E43509D012BB1D5…` / `B830986802BB93DE…` / `888117788B021571…` |
| `internal/api/memory.go` | `FEEDBDDF517D9382…` |
| `apps/web/src/memories.html` / `assets/memories.js` | `A9E550E14CCB206D…` / `6DB0FDE3DD96737D…` |
| `docs/adr/0008-f09-memory.md` | `51DD1B8A31D31C5C…` |
| `tests/f09/test_t054_{memory,migration,pages}.py` | `C07D148064A23E1A…` / `C6B6BD5846C77D3B…` / `5C5DD9413E1FAA54…` |
| manifest / 运行输入 | T025 golden manifest（T054 不改 compile/execution） |

真实/stub 分报：**全部为 real**（真实 PG、真实二进制、真实 mTLS、真实对象字节、真实 Node）；
本票没有调用任何模型或外部供应商，因此没有 stub 结果需要单列。

## 6. `not_run` / 债务（如实）

| 项 | 原因 / owning ticket |
|---|---|
| 浏览器 DOM 驱动（点击/渲染截图） | 无浏览器驱动；页面纯函数由 Node 执行，与 T023/T027/T029/T039/T050 同一边界 |
| 后台过期清扫进程 | 惰性迁移 + 决定复检已真实生效；独立 sweeper 属后续运维票，未实现 |
| 记忆编辑/删除/强 ETag/412/428（`getMemory`/`updateMemory`/`deleteMemory`） | T058（#59）；本票只留 revision/deleted_at/epoch seam |
| 运行时召回（上下文预算内） | T063（#64）；`MemorySource` 仍是 `NoMemorySource`，候选永不可召回 |
| Mem0 对照写入/映射 | T067（#67）；未运行，不声称采用 |
| 记忆共享/授权（`policy.ResourceTypes` 扩展） | 本票不扩展；owner-only + 管理员只看自己，债务记录在 ADR 0008 §9 |
| 真实 worker 自动抽取器 | 真实内部端点、真实 worker 证书、真实 attempt 被调用；抽取策略本身属 F04A agent 循环，本票不冒充 |
| 全量 `pytest tests/f01`（38 个文件） | 启动后 >1 小时未完成（CPU 时间仅 ~55s，疑有等待型用例），按“不把未完成运行写成通过”的规则终止；与记忆边界直接相关的 `tests/f01/test_t020_personal_boundary.py` 在合并前后各跑一次均 2 passed（9.68s / 33.48s）。全量 f01 由 orchestrator 在合并后运行 |
| 全量 `pytest tests`（约 67 分钟） | BRIEF §3 明令不跑；由 orchestrator 合并后运行 |

## 7. 下一票接口

- 表：`platform.memory_candidates`（`state ∈ pending|confirmed|rejected|expired`，
  `logical_key` 唯一于 run）、`platform.memories`（`candidate_id` UNIQUE、`revision`、
  `confirmed_by`、`deleted_at`）；
- Go：`internal/memory.Service`（`Propose/ListCandidates/Decide/Create/List/MemoryEpoch`）、
  `api.Deps.MemoryCandidateTTL`、`api.Server.memory`；
- HTTP：5 个契约 operation；`GET /api/v1/memories` 已不是空列表；
- 缓存失效键：`platform.policy_epochs(scope='memory:<owner>:<application>')`；
- 页面：`/memories` + `/assets/memories.js`。

## 8. ADR

`product/docs/adr/0008-f09-memory.md`（服务账号偏离、sealed 正文与复用、worker-only 角色、
TTL/过期、决定幂等、cache epoch、来源单链、结构 seam、共享债务、页面来源显示边界）。
合并后与 T053 的 `0008-f15a-object-store-compatibility.md` 同号，按 orchestrator 约定由
合并方重编号（两文件互不依赖，本票内容自包含）。
