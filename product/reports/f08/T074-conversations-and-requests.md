# F08 T074 验收：从目录指定版本提交需求并有序排队

- 任务：F08 / GitHub Issue #75（票 `T074`，规划 ID F08.01/F08.05），分支
  `t/t074-conversations`（worktree `E:\orchara-worktrees\t074`）；父门禁 F08。
- 状态：**`passed`**（本票范围 = `createConversation` / `listConversations` /
  `getConversation` / `listMessages` / `submitRequest` / `getRequest` / `cancelRequest`
  + 会话逻辑槽/有序队列 + 固定版本直接建 Run + 幂等 receipt + T069 conversation
  `Target`（删除会话立即不可读、7-scope job、取消排队请求与活动 Run）+ `/chat` 页面
  + Node 可执行纯函数；真实 PostgreSQL 16.4、真实编译二进制、真实 cookie+CSRF HTTP、
  真实封存对象字节、真实 F03/T036 取消路径、真实 maintenance（附件 finalize）、Node。
- 边界（如实）：F08/T079 的模型路由/候选选择/澄清答复/控制预算不在本票（本票只在
  `routing` 停住并留下 `route_request` 后台意图，T079 的 handler 尚未注册）；T082 的
  交接 UI 不在本票（`handoff` 对象已接收、校验、落库并进入 Run 输入闭包）；浏览器 DOM
  驱动页面仍 `not_run`（与 T023/T027/T033 同一边界）；全量 `pytest tests` 未运行。
- 迁移编号：本票为 `0044_conversations_and_requests.sql`。基线合并把 T068 的
  `0042_publication_reviews_and_outbox.sql` 原样带进了本 worktree，与 T069 的
  `0042_deletion_foundation.sql` 撞号（**该重复版本号来自基线合并，不是本票引入**）；
  为在最终合并前运行真实迁移链，本 worktree 曾只在未提交工作区把 T068 文件临时改名为
  `0043`。最终 `git merge integration/t068-t082`（merge commit `7138ae9`）带入了
  T068 的正式 0043，最终链为 **0042_deletion_foundation → 0043_publication_reviews_and_outbox
  → 0044_conversations_and_requests**，无重复、无缺口。
- 最终合并：`integration/t068-t082` 合并时点为 `afeba90`（含 T068 发布审核/版本与
  T070 撤销/权威 run guard）。合并后本票修正了 T070 的一个种子夹具：T070 的
  `tests/support/t070.py::seed_published_run` 原先用不存在的随机 `request_id`/
  `conversation_id` 插入 `runs`，本票补全的 FK 会拒绝；现在它在同一夹具里创建真实的
  conversation + dispatched request 再插 Run（T070 被测行为与状态未变）。
  ADR 编号冲突同步解决：T068 取 0021、T070 取 0022，本票 ADR 重编号为 **0023**。

## 0. 工作记录（简短）

1. 读票面、`BRIEF.md`、`notes/f08-conversations.md` §3.3/§4/§5/§8/§9/§10/§11，打开
   `internal/catalog/{debug,publication,versions}.go`、`internal/execution/cancel.go`、
   `internal/deletion`、`internal/api/{server,authz,errors,deletions}.go`、
   `tests/support/{stack,t069,t068,t025,t058}.py`、`0010/0014/0021/0042` 迁移与契约 schema。
2. TDD 垂直切片（每片先红后绿，先写 §6.1 要求的并发竞争测试）：
   - 并发双提交只能一个持槽（真实双会话线程 + barrier）→ 迁移 + aggregate + API；
   - 队列顺序 / 取消槽持有者推进下一项 / 重复取消幂等 → 真实行断言；
   - 固定版本提交直接建 published Run（无 router、无 model call、真实 snapshot/输入链接）
     → 真实封存字节与 `runs`/`jobs` 行；
   - 幂等键同体复用、异体 409、删除后回放 404 → 真实 receipt 表；
   - 撤销版本 409、不可见版本 404、错误 body/附件 422 → 真实行证明零写入；
   - 新应用不读旧历史/记忆 + 交接最小确认 → 两个真实发布版本 + 真实 memory API + 扫描
     Run 输入闭包的每个封存字节；
   - 会话删除 → T069 foundation 202/7-scope/tombstone/读拒绝/Run 取消；
   - 迁移链接/表/部分唯一索引/3 个补全 FK/worker 零权限；页面结构与 Node 纯函数。
3. 跑门禁（§3），写本报告/ADR 0023/PROGRESS 行；未推送、未关 issue、未改
   `docs/spec/**`/`contracts/**`；最终合并 `integration/t068-t082`。

## 1. 交付内容

### 1.1 数据模型（`0044_conversations_and_requests.sql`）

- `platform.conversations`：id/owner/revision/created_at/deleted_at（T069 tombstone 列）。
- `platform.requests`：conversation_id/owner_id/input_artifact_id/**artifact_ids uuid[]**/
  state（契约 7 值）/revision/error_code/**requested_version_id**/**control_calls（≤3）**/
  created_at/updated_at。非契约列的理由见 ADR 0023 §5。
- 索引：`requests_queue`（会话内 (created_at,id) 全序）、
  `one_active_request_per_conversation`（`routing|awaiting_input|awaiting_selection` 部分唯一）。
  **没有** `state='dispatched'` 的部分唯一索引：STATE-MACHINES §17 下它会让会话在首个 Run
  结束后永久卡死（ADR 0023 §1）。Run 半边沿用 0014 的 `one_active_top_run_per_conversation`。
- `platform.interactions`（契约 shape；proposal_artifact_id NOT NULL、pending 唯一）。
- `platform.request_handoffs`（摘要 artifact、来源 Run 数组、确认人/时间、来源版本）。
- 补全 0011/0014 延迟的 FK：`runs_request_id_fkey`、`runs_conversation_id_fkey`、
  `platform_model_calls_request_fkey`（DO guard 幂等）。

### 1.2 `internal/requests`（本票发布的复用接缝）

- `CreateConversation`/`ListConversations`（owner 过滤 + cursor）/`GetConversation`（删除/他人/
  未知同 404）。
- `Submit`：请求体 located 422 → 会话存活 404 → 版本 published/runnable 校验
  （`version_revoked`/`version_archived`/`version_unavailable`）→ 附件与交接来源 422 →
  封存 message（和确认摘要）→ 单事务：锁会话、claim receipt、写 Request(+handoff)、
  槽空闲且最早则派发固定版本或进入 `routing` 并写唯一 `route_request` job。
- `dispatchVersionTx`：`ResolveRunnableVersionTx`（`FOR SHARE`）→ 校验 manifest artifact →
  `execution_snapshots(kind='published')` → `runs(kind='published')` → `AppendRunStatusTx` →
  `jobs(start,ready)` → 只链接 message/显式附件/确认摘要 → `queued→dispatched` CAS + 审计。
- `Cancel`：pre-dispatch 请求同事务取消+失效 pending interaction+推进下一项；dispatched
  请求走既有 `execution.Cancel`（不复制状态机），随后推进；重复取消幂等；failed 409。
- `AdvanceConversationQueueTx`：惰性过期 interaction → 槽判定 → 最早未完成请求
  → 固定版本直接派发/普通请求 `routing`+job。提交、取消、**排队请求读取**调用它。
- `ListMessages`：requests/runs/interactions 的授权投影（无第二份正文）。
- `DeletionTarget`：会话 tombstone + 取消排队请求 + 失效 interaction + 7 scope details +
  受影响 Run 交给 foundation 的取消扇出。

### 1.3 API 与页面

- 7 个契约 operation 的真实实现（会话 3 读/1 写、requests submit/get/cancel、messages），
  全部 session+CSRF；API key 需 `application:read`/`application:write`。
- 契约封闭 schema 之外的事实走文档化响应头：`X-Request-Conversation-Id`、
  `X-Request-Application-Version-Id`、`X-Request-Application-Version-No`、
  `X-Request-Queue-Position`、`X-Request-Interaction-Id`、`X-Request-Correlation-Id`
  （ADR 0023 §4）。
- `/chat` + `/assets/chat.js`：新建/选择会话、提交（消息 + ≤5 真实上传附件 + 可选固定版本 +
  可选最小交接确认）、轮询 `getRequest`、显示处理版本/队列位置/Run 链接、取消、上传附件、
  消息时间线；纯函数在 Node 下真实执行（路径、7 状态、队列、交接确认、handling version、
  idempotency attempt、轮询停止条件）。

## 2. 验收标准（逐条，全部由已执行测试证明）

| AC | 结论 | 证据（真实执行） |
|---|---|---|
| 相同幂等键同请求复用、异内容冲突 | passed | `tests/f08/test_t074_submit.py::test_idempotent_resubmission_replays_and_a_different_body_conflicts`：同 key 同体重放相同 Request/Run，`idempotency_keys` 1 行、`requests` 1 行、`runs` 1 行；同 key 异体 409 `idempotency_conflict` 且零新增；新 key 同体是新请求 |
| 并发提交不双活 | passed | `test_t074_queue.py::test_concurrent_submits_cannot_both_hold_the_single_conversation_slot`（本票最先写的测试，§6.1）：两个真实登录会话经 `threading.Barrier` 并发 POST，两个 202；PG 行为 `dispatched`+`queued`，活跃顶层 Run 恰好 1，槽持有者恰好 1，失败方无 run/无 job；无 <5s 计时断言 |
| 取消队列请求能推进下一项 | passed | `test_cancelling_the_slot_holder_advances_the_next_queued_request`：r1 routing / r2 queued / r3 queued，取消 r1 后 r2= routing（拥有唯一 route_request job）、r3 仍 queued；再取消 r2 后 r3=routing；重复取消幂等。另有 `test_a_run_cancelled_through_the_execution_path_advances_the_next_request`：经既有 `POST /runs/{id}/cancel` 结束 Run 后，下一次 `getRequest` 把 queued 的固定版本请求推进为 dispatched |
| 固定版本测试不需伪造路由 | passed | `test_t074_submit.py::test_fixed_version_submit_creates_the_published_run_without_any_router`：`application_version_id` 直接建 `kind=published` Run；真实 `execution_snapshots`（version id、manifest hash/artifact、policy）；真实封存 message 字节；`run_artifacts` 恰为 message+附件；`jobs` 恰一个 start/ready；该 request **零** `route_request` job、**零** `platform_model_calls` 行；吊销版本 → 409 `version_revoked` 且零 Request/Run/receipt（`test_a_revoked_version_is_refused_before_any_request_or_run_exists`） |
| 新应用不读旧内部历史/记忆 | passed | `test_t074_isolation.py::test_a_new_application_never_reads_the_old_history_or_personal_memory`：真实两个发布版本 + 真实 memory API 写入两个应用记忆；会话先跑 A 再以 B 提交（带确认交接）；B 的 Run 输入闭包恰为 B 的 message、显式附件、确认摘要，**不含** A 的 message artifact；逐字节扫描所有闭包附件，A 的历史 marker 与 A 的个人记忆 marker 均不出现；memory 读按应用隔离。`test_the_handover_needs_an_explicit_minimal_confirmation` 证明未确认 422 `handoff_not_confirmed`、空来源/他人 Run/跨会话 Run 422 `handoff_source_unknown`、他人附件 422，且全部零写入 |
| 用户可看清处理者版本 | passed | `test_fixed_version_submit_...` 与竞争测试断言 `GET /requests/{id}` 的 `X-Request-Application-Version-Id`/`-No`（契约 `Request` 无字段，ADR 0023 §4）；页面 `describeHandling` 在 Node 下断言“尚无处理应用”/“v3 正在处理”/“v3 已处理”/“v3 已取消” |
| 通过上述端到端入口实际演示；失败、授权与重试边界有断言 | passed | 全部 AC 均经真实 REST/session+CSRF；`test_submit_validates_...` 覆盖缺 artifact_ids/空 message/>5/他人附件/未知附件；`test_conversation_delete_is_owner_only_and_unknown_is_the_same_404` 覆盖删除授权；重试边界 = 幂等重放 + 重复取消 + 重复 DELETE 同 job；失败边界 = revoked 版本/交接拒绝/删除后回放 404 |
| 记录版本、输入/hash、命令、原始结果与 profile；stub/real 分报 | passed | 本报告 §3 原始输出；profile `dev-stub`；无 stub 参与本票断言（unavailable：真实模型路由属 T079）；输入/hash = 封存 artifact 的 sha256 与 manifest hash，均在测试中断言 |
| **T069 conversation 删除 deferred line** | passed | `test_t074_delete.py::test_conversation_delete_tombstones_cancels_and_returns_the_seven_scope_job`：真实 `DELETE /conversations/{id}` 202 `DeletionAccepted`，tombstone+revision 同事务，读全部立即 404；queued 请求 cancelled、活动 Run 经既有取消路径 cancelled；7 scope 全 outstanding + 真实 details（object_store 含封存 key、checkpoints run ids、search_index request ids）；重复 DELETE 同 job、revision 不再变；`tests/f11` 43 passed 证明 T069 套件不回归 |
| 页面 | passed | `test_t074_pages.py`：真实编译 API 提供 `/chat` 与 `/assets/chat.js`（结构 marker）；Node 24 真实执行 app.js+attachments.js+chat.js，断言路径/状态/队列/交接/handling/idempotency/轮询逻辑 |

## 3. 门禁（原始输出）

环境：Windows PowerShell；Go 工具链 `E:\orchara-toolchains\go`；PostgreSQL 16.4
（`127.0.0.1:55432`，user-space cluster）；profile `dev-stub`；Node 24。

```
$ gofmt -l .
（空）

$ go build ./...
build=0

$ go vet ./...
vet=0

$ go test ./...
ok  	orchara/platform/internal/...
（全部包 ok；test_exit=0）

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest   (exit 0)

$ python -m pytest tests/f08 -q --basetemp=E:\orchara-tmp\t074-final1
...................                                                      [100%]
19 passed in 195.23s (0:03:15)

$ python -m pytest tests/f08 -q --basetemp=E:\orchara-tmp\t074-final2
...................                                                      [100%]
19 passed in 196.02s (0:03:16)

$ python -m pytest tests/tooling tests/f11 -q --basetemp=E:\orchara-tmp\t074-neigh
...........................................                              [100%]
43 passed in 219.49s (0:03:39)

$ python -m pytest tests/f02a/test_t027_debug.py tests/f02a/test_t027_idempotency.py tests/f02a/test_t025_manifest.py -q --basetemp=E:\orchara-tmp\t074-f02a2
.............                                                            [100%]
13 passed in 151.72s (0:02:31)

# 最终 merge commit 7138ae9 之后（合并带入 T068 0043 + T068/T070 套件与真实撤销 guard）
$ python -m pytest tests/f08 -q --basetemp=E:\orchara-tmp\t074-merged
...................                                                      [100%]
19 passed in 168.67s (0:02:48)

$ python -m pytest tests/t070 -q --basetemp=E:\orchara-tmp\t074-t070b
............                                                             [100%]
12 passed in 138.96s (0:02:18)

$ python -m pytest tests/f02b tests/t070 tests/f11 tests/tooling -q --basetemp=E:\orchara-tmp\t074-cross
........................................................................ [ 98%]
.                                                                        [100%]
73 passed in 612.54s (0:10:12)
```

说明：`tests/f08` 的 19 个用例 = 队列/竞争/惰性推进 5 + 提交/幂等/版本 4 + 隔离/交接 2 +
会话删除 2 + 迁移 4 + 页面 2。`tests/f11` 同时覆盖 T069 的会话删除 route 404 语义（未回归）。
两次 back-to-back 均包含真实二进制构建、真实迁移 0044、真实 maintenance finalize。
合并后 73 passed 覆盖 T068 的发布/版本套件（f02b）、T070 撤销/权威 guard 套件（t070）、
T069 删除套件（f11）与本票队列/恢复语义；这是“补全 3 个延迟 FK 后跨票仍绿”的独立证据。
第一次合并后组合运行曾暴露 4 个 T070 用例失败（T070 夹具用不存在的 request/conversation id
插 Run，被新 FK 拒绝）；按 §头部修正夹具后 t070 12 passed，最终组合 73 passed。

另：全量 `pytest tests/f02a` 的一次尝试在共享主机上 >20 分钟无任何输出且无 python 进程
进度，作为可选邻接套件被中止；改为运行与本票 `internal/catalog` 改动直接相邻的
T025/T027 三个文件（13 passed）。该改动是加法（新 `ResolveRunnableVersionTx`/`RunPolicy`
与 `Version` 冻结字段），既有 `ResolveRunnableVersion` 语义不变。

## 4. not_run / 未在本票

- 浏览器 DOM 驱动页面（与 T023/T027/T033 同一边界）：页面逻辑经 Node 真实执行，DOM 未驱动。
- 模型路由/候选选择/澄清答复/控制预算（F08/T079）：本票只留下 `routing` + `route_request`
  job 与 `interactions` 表；`replyInteraction` 契约操作未实现。
- 交接 UI 与跨应用切换（F08/T082）：`handoff` 的服务端接收/校验/落库/输入闭包已交付。
- 完整 T068/T070 套件在最终合并后已作为跨票回归运行（合并后 73 passed 含 `tests/f02b`
  与 `tests/t070`）；本票开发期间的“未运行”仅指合并前，最终状态已运行。
- 全量 `tests/f02a` 的一次尝试在共享主机上无输出挂起 >20min 被中止（可选邻接），改为
  T025/T027 直接相邻 13 passed（见 §3）。
- 全量 `pytest tests`（BRIEF 明令不运行，>60min）。
- 真实模型/供应商调用：本票固定版本路径按设计不经过模型。

## 5. 下一步接口（T077/T079/T082 及后续）

- 复用 `internal/requests.Service`：`CreateConversation`、`Submit`、`GetRequest`、
  `Cancel`、`ListMessages`、`AdvanceConversationQueueTx(ctx, tx, conversationID)`、
  `Facts`；`NewDeletionTarget(service)` 已注册进 `api.New`。
- 复用 `catalog.ResolveRunnableVersion(Tx)`、`catalog.RunPolicy`；T070 撤销应保持
  `version_revoked`/`version_archived` 错误码与 `FOR SHARE` 竞争语义。最终合并后已验证
  T070 的 `GET /versions/{id}/runnable` 与 T074 `submitRequest` 使用同一 guard/同一错误码，
  且 `tests/t070` + `tests/f02b` 与本票套件同轮全绿（73 passed）。
- T079 必须：写 `interactions`（proposal artifact、correlation id、`expires_at` 用
  `requests.InteractionTTL` 的文档默认 15m）、在路由/派发后调用
  `AdvanceConversationQueueTx`、在 Run 终态路径调用同一接缝；控制预算写
  `requests.control_calls`（DB CHECK ≤3）。
- T077/T082 复用：`RequestFacts` 响应头约定；`request_handoffs`（摘要 artifact 必须继承
  T071/T072 清理 scope）；会话删除后一切读取 404 的谓词。
- 页面：`/chat` + `/assets/chat.js` 的纯函数与 `/assets/attachments.js` 的上传 helper 可复用。

## 6. 决策记录

- ADR 0023 `docs/adr/0023-conversation-request-queue.md`：逻辑槽两半、固定版本 `FOR SHARE`、
  非契约响应头/列/表、交接最小确认、惰性推进与消息投影、删除接回 T069。
- 迁移偏差与 owning later ticket 见 `0044` 头注释。
- 合并记录：`merge(f08)` `7138ae9`（`integration/t068-t082` @ `afeba90`），ADR 因编号冲突
  由 0021 重编号为 0023，T070 种子夹具随补全 FK 修正（见头部）。
