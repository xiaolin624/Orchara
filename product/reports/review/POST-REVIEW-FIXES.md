# 复核后修复报告 — `integration/t068-t082`（T068–T082 / GitHub #69–#83）

- 任务：修复独立复核在 **`integration/t068-t082` @ `c755012`** 上复现的 **2 个 P1 + 3 个 P2**。
- 分支：`t/post-review-fixes`（worktree `E:\orchara-worktrees\postreview`）。
- 状态：**5/5 fixed**；每条都有先在 **pre-fix `c755012`** 上失败的回归测试（§2 原始尾巴），
  修复后同一测试绿；没有把未执行的行为写成通过。
- 真实依赖：本机 PostgreSQL **16.4**（127.0.0.1:55432）、现场编译的 `cmd/api`/`cmd/admin`/
  `cmd/maintenance` 等真实二进制、真实 HTTP+CSRF、真实文件对象存储与 sealed 字节、Node 24
  （页面 DOM 执行测试用最小 DOM shim 驱动真实 `chat.js` 字节，不是浏览器）。无 mock 被测对象。
- 边界：未改 `docs/spec/**` 与 `contracts/**`；无新迁移；未 push、未触碰 GitHub。浏览器 DOM
  仍为 `not_run`（本报告的页面测试是 Node + DOM shim，见 §4）。

## 0 工作记录

1. 逐条读复核描述与相关实现（`internal/requests/{deletion,submit}.go`、
   `internal/deletion/purge.go`、`apps/web/src/{chat.html,assets/chat.js}`），先写失败测试。
2. 在 **pre-fix（`c755012`）detached worktree** 上跑同一批测试，5 条全部按复核描述失败
   （§2 原始输出）；随后在当前 worktree 修复并跑绿。
3. 页面两条用 Node 执行 `chat.js` 真实字节：最小 DOM shim 实现了页面用到的元素/监听器/
   选择器并记录页面真实发出的 `api`/`apiFull` 调用；不是对 helper 的孤立断言
   （`tests/f08/test_postreview_pages.py`）。
4. 门禁、PROGRESS 与本报告见 §3。

## 1 逐条映射（finding → repro → fix → file → regression）

| # | 级别 | 复核复现 | 修复 | 主要文件 | 回归测试 |
|---|---|---|---|---|---|
| 1 | P1 | DELETE 会话返回 202 后，请求正文 artifact 仍 200+完整内容 | 会话 tombstone 同事务 tombstone 其 artifact 闭包（请求 input/附件、run input/output/`run_artifacts`、interaction proposal/reply、handoff 摘要），经既有 `deleted_at` 读谓词立即 404；被其他 live 资源显式引用的 artifact 不 tombstone | `internal/requests/deletion.go`、`internal/deletion/purge.go` | `tests/f08/test_t074_delete.py::test_deleted_conversation_body_is_unreadable_through_every_artifact_entry`（另更新既有“删除后字节仍可读”的旧断言为该语义） |
| 2 | P1 | A、B 共用一个附件；删除并清理 A 后 B 的附件 404 | `sharedArtifacts` 扩展为全部显式引用关系：request `input_artifact_id`/`artifact_ids`、run `input_artifact_id`/`output_artifact_id`/`run_artifacts`、`request_handoffs` `summary_artifact_id`/`artifact_ids`、interaction proposal/response，加既有 knowledge/memory/candidate；引用方会话必须 live。同一谓词经 `deletion.LiveArtifactReferences` 供会话 tombstone 复用 | `internal/deletion/purge.go`、`internal/requests/deletion.go` | `tests/f11/test_t071_conversation.py::test_a_shared_attachment_survives_another_conversations_delete_and_purge`（`object_shared_reference`、business/non-retryable、B 三条读入口 200） |
| 3 | P2 | 首次提交 202 → 撤销版本 → 同 key/body 重试 409 `version_revoked` | `Submit` 在会话 liveness 检查后、版本/授权检查**之前**先读已提交 receipt：同体重放原 202，异体 409 conflict；新 key 仍 `version_revoked`；删除会话仍 404 | `internal/requests/submit.go` | `tests/f08/test_t074_submit.py::test_a_committed_request_replays_after_its_version_is_revoked` |
| 4 | P2 | 重新打开会话后当前请求被清空，交互面板/候选/轮询不恢复，队列不可点 | 新增纯函数 `resumableRequest` + DOM 装配：加载会话后恢复持槽请求的面板/候选并重新 `poll`；队列每行是可点按钮（切换请求）且 queued/awaiting 行有取消按钮；`cancelRequest` 统一取消入口 | `apps/web/src/assets/chat.js` | `tests/f08/test_postreview_pages.py::test_reopening_a_conversation_restores_the_pending_request_and_an_actionable_queue` |
| 5 | P2 | 上传缺失输入 CSV 后答复 body 仍不含文件（调用点写死 `[]`） | 答复携带用户在本页上传的 artifact ids（`replyArtifactIDs(attachmentIDs)`，去重、服务端仍校验 ready/owner/缺失输入）；`chat.html` 交互面板补一行提示 | `apps/web/src/assets/chat.js`、`apps/web/src/chat.html` | `tests/f08/test_postreview_pages.py::test_the_reply_carries_the_uploaded_missing_input_artifact`（服务端侧的缺输入→上传→答复→路由闭环由既有 `tests/f08/test_t079_routing.py::test_missing_input_creates_no_run_until_the_reply_supplies_it` 覆盖） |

### 1.1 机制选择（finding 1）

选 **tombstone 会话 artifact 闭包**，不是给每个 artifact 读入口加“被已删除会话引用”谓词：
`Get`/`readyRow`（GET、download、content 三条入口）已经统一按 `artifacts.deleted_at` 拒绝，
tombstone 与 T069 `artifacts.deleted_at`/`state='deleted'` 语义一致，且与 T071 purge 用同一个
artifact 闭包（`conversationArtifactsTx`）和同一个 live-reference 谓词
（`deletion.LiveArtifactReferences`），不会出现“删除时跳过、清理时误删”的分叉。

不变式：

- 会话 tombstone 在**同一事务**内完成会话行、请求取消、interaction invalidation、artifact
  tombstone；202 之前已提交，因此三条读入口立即 404。
- 被其他 live 资源（另一个未删会话的 request/run/handoff/interaction，或 knowledge/memory）
  显式引用的 artifact 不 tombstone；T071 对同一引用返回 `object_shared_reference`
  （business、non-retryable），B 的字节与行都保留。
- 会话自己的 run/请求不构成“live 引用”（join `conversations.deleted_at IS NULL`）；没有会话的
  run（历史行）仍算 live，避免误删。
- T069 `resource_in_use` 规则未改：本次只 tombstone 会话闭包内的 artifact，不经过 artifact
  删除入口；发布 manifest / 配置类 artifact 从不在会话闭包内。

## 2 红→绿证据（pre-fix `c755012` 原始尾巴）

在 detached worktree `E:\orchara-tmp\oldhead`（`git worktree add --detach ... c755012`）中只
拷入本报告的测试文件，跑同一批用例，5 条全部失败；原始尾巴：

```
$ pytest <5 fix tests> -q
E       AssertionError: expected 404, got 200: {"id":"82fd770f-...","state":"ready","media_type":"text/plain; charset=utf-8","size_bytes":34}
tests\support\stack.py:246: AssertionError
E       AssertionError: {"error":{"code":"version_revoked",...}}
E       assert 409 == 202
tests\f08\test_t074_submit.py:312: AssertionError
E       AssertionError: {'deleted_at': '2026-11-05 22:05:20.470395+08', 'expected_sha256': '2105...', 'id': '262aa207-...', ...}
tests\f11\test_t071_conversation.py:158: AssertionError
E       AssertionError [ERR_ASSERTION]: the request panel was not restored
tests\f08\test_postreview_pages.py:421: AssertionError
E       AssertionError [ERR_ASSERTION]: the reply did not carry the uploaded attachment: {"interaction_id":"44444444-...","message":"sales csv attached"}
tests\f08\test_postreview_pages.py:475: AssertionError
FAILED tests/f08/test_t074_delete.py::test_deleted_conversation_body_is_unreadable_through_every_artifact_entry
FAILED tests/f08/test_t074_submit.py::test_a_committed_request_replays_after_its_version_is_revoked
FAILED tests/f11/test_t071_conversation.py::test_a_shared_attachment_survives_another_conversations_delete_and_purge
FAILED tests/f08/test_postreview_pages.py::test_reopening_a_conversation_restores_the_pending_request_and_an_actionable_queue
FAILED tests/f08/test_postreview_pages.py::test_the_reply_carries_the_uploaded_missing_input_artifact
5 failed in 43.76s
```

finding 2 在 pre-fix 树上被 A 的 purge 直接标记/删除（上面的 `deleted_at` 尾巴）；把同一用例
单独重跑以拿到复核描述的 404 观察点（断言顺序调整后测的同一行为）：

```
$ pytest tests/f11/test_t071_conversation.py::test_a_shared_attachment_survives_another_conversations_delete_and_purge -q
E           AssertionError: ('get_artifact', 404, '{"error":{"code":"resource_not_found","message":"that artifact does not exist",...}}')
tests\f11\test_t071_conversation.py:159: AssertionError
1 failed in 11.92s
```

修复后同一批：

```
$ pytest <5 fix tests> -q --basetemp=E:\orchara-tmp\postreview-green-fixes
.....                                                                    [100%]
5 passed in 29.76s
```

页面两条的复现细节：pre-fix `chat.js` 的 `selectConversation` 把 `currentRequestID` 清空后只
`loadMessages`+`refreshQueue`，队列行只是纯文本 `<li>`；DOM shim 因此看到
`#request-panel` 仍 hidden、`#interaction-panel` 仍 hidden、无定时器（未恢复轮询），队列行无
按钮。答复路径把 `[]` 硬编码进 `interactionReplyBody`，shim 捕获的 reply body 只有
`interaction_id`+`message`。

## 3 门禁（逐条原始输出）

所有 pytest 使用隔离 `--basetemp=E:\orchara-tmp\postreview-*`；套件串行运行（不做并发测试）。

```
$ python -m pytest tests/f11 -q --basetemp=E:\orchara-tmp\postreview-f11-final
........................................................................ [ 84%]
.............                                                            [100%]
85 passed in 462.80s (0:07:42)

$ python -m pytest tests/f08 -q --basetemp=E:\orchara-tmp\postreview-f08-final
.....................................................................    [100%]
69 passed in 503.95s (0:08:23)

$ python -m pytest tests/tooling -q --basetemp=E:\orchara-tmp\postreview-tooling
.....................                                                    [100%]
21 passed in 4.63s
```

（本分支在 f11 增加 1 条回归、在 f08 增加 4 条回归（finding 1/3 各 1、页面 finding 4/5 各 1）。）

```
$ go build ./...        # exit 0（无输出）
$ go vet ./...          # exit 0（无输出）
$ go test ./...         # exit 0，全部包 ok；关键包：
ok  	orchara/platform/internal/artifacts	0.991s
ok  	orchara/platform/internal/deletion	1.188s
ok  	orchara/platform/internal/requests	1.988s
（日志中 FAIL 计数 = 0）

$ gofmt -l .            # 空输出，exit 0
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest   # exit 0
```

5 条修复测试背靠背第二轮（确定性）：

```
$ python -m pytest <5 fix tests> -q --basetemp=E:\orchara-tmp\postreview-fixes-run1
.....                                                                    [100%]
5 passed in 31.53s

$ python -m pytest <5 fix tests> -q --basetemp=E:\orchara-tmp\postreview-fixes-run2
.....                                                                    [100%]
5 passed in 30.23s
```


## 4 not_run / 边界

- 浏览器 DOM：`not_run`（无浏览器驱动）。页面两条用 Node 24 + 最小 DOM shim 驱动真实
  `chat.js` 字节与真实事件处理路径；shim 只实现页面实际用到的 API，不代表真实布局/CSS。
- 全量 `pytest tests`（>60 分钟）未作为单次门禁运行；按 BRIEF 只跑 `tests/f11`、`tests/f08`、
  `tests/tooling` 与逐条修复测试。
- 真实 S3/MinIO、真实厂商模型、WSL2/runsc 不在本次修复路径。
- 预签名的 GET URL 在签发后仍可用至短 TTL 到期（SECURITY §5 已声明：需要即时撤销的对象走
  proxy download）；本修复关闭的是签发入口（`/download`）与 `/content`，未改签名链接设计。

## 5 给后续票/合并器的接口

- `deletion.LiveArtifactReferences(ctx, db, ids)`：唯一“live 显式引用”谓词（request/run/
  `run_artifacts`/handoff/interaction + knowledge/memory/candidate）。以后新增引用关系时改这里，
  会话 tombstone 与 T071 purge 同时生效。
- `requests.DeletionTarget.conversationArtifactsTx`：会话 artifact 闭包的唯一定义（tombstone、
  purge `object_store` details 共用）；以后新增“会话拥有的 artifact”列时改这里。
- `chat.js` 纯函数 `resumableRequest(items, activeID)`、`replyArtifactIDs(uploaded)`；DOM 入口
  `selectConversation` 在加载后恢复持槽请求，队列行携带 `dataset.requestId` 且可点。
