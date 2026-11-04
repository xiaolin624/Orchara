# F10 T064 验收：回答只引用本次检索且当前可读的资料

- 任务：F10 / GitHub Issue #65（票 `T064`，规划 ID F10.04），分支
  `t/t064-grounded-citations`（worktree `E:\orchara-worktrees\t064`，从
  `integration/f13a-f15a-f09-f10` @ `9f58fc2` 切出）；父门禁 #53–#68 集成批次。
- 状态：**`passed`**（本票范围 = 非契约回答面
  `POST /api/v1/knowledge-bases/{id}/answer`、服务端引用校验（本次检索集合 + 返回前
  ACL/当前版本复检 + 服务端 provenance）、四个可区分结果
  （`answered`/`no_results`/`answer_without_citation`/`citation_not_found`/
  `citation_unreadable`）、检索 ACL 硬化（body artifact 必须与 KB 同 owner）、
  `/knowledge` 问答视图与 Node 执行页面逻辑）。真实 PostgreSQL 16.4 + 真实编译二进制 +
  真实 HTTP + 真实封存对象字节 + 真实 maintenance 摄取进程 + 真实第二个 API 进程
  （dev-stub 故障约定）+ Node；无 mock、无跳过、无迁移、未改契约/规格。
- 生成边界（如实）：本环境**没有模型 provider**（无凭据、构建无 adapter，ADR 0010/0007
  同一事实），回答生成是**带标签的抽取式 stub**（`extractive-stub/1`，从本次检索片段
  逐字摘录）；真实模型生成 **blocked**。本票真正的交付是引用契约与其服务端校验。
- 契约偏差：回答面是契约外的非契约路径（契约没有检索/回答操作），记录在 ADR 0012；
  `contracts/**`/`docs/spec/**` 未改，`sync_spec --check` no drift；无迁移。
- 页面边界：只做服务端页面结构 + Node 执行纯 helper；真实浏览器 DOM 驱动 **not_run**
  （与 T023/T027/T029/T031/T032/T033/T037/T039/T055 同一边界）。

## 0. 工作记录（简短）

1. 读 `BRIEF.md`、票面 #65、`briefs/T064.md`、ADR 0010、T055 报告、`internal/knowledge`
   （检索 SQL / `GetChunk` / `DocumentIngestBinding` / 完成守卫）、`internal/api/knowledge.go`、
   迁移 `0034_knowledge.sql`、`tests/support/t055.py` 与 `tests/f10/`、ADR 0007（stub 边界
   与故障约定）、`internal/modelproxy`（provider 缝）。
2. TDD 垂直切片（每片先红后绿）：
   - Go 单元缝：`ExtractiveStub`（确定性抽取、引用=使用片段、有界 3 段）+ `validateAnswerDraft`
     （无引用拒绝 / 非本次检索拒绝 / provenance 只从检索行投影 / 去重）+ 故障解析 →
     `internal/knowledge` 单测（先编译失败为红）。
   - 真实栈 HTTP：`Service.Answer`（检索→草稿→集合校验→逐引用 `GetChunk` 复检）+ handler +
     路由 + `cmd/api` 装配；`tests/f10/test_t064_answer.py` 每个用例先红（404/形状不符）。
   - ACL 硬化：`Query`/`GetChunk` 的 body artifact join 增加 `a.owner_id = kb.owner_id`；
     伪造“指向他人封存正文”的真实 chunk 行（真实行 + 真实字节）证明检索与回答都拿不到。
   - 拒绝路径端到端：第二个真实 API 进程用部署环境 `ORCHARA_KNOWLEDGE_ANSWER_FAULTS`
     让 stub 按文档出错（`uncited`/`foreign-chunk`/`slow=5s`），走真实 HTTP 得到三个不同码；
     撤销竞态用 `slow` 的日志检查点保证“检索已完成、校验未开始”再提交 tombstone。
   - 页面：`/knowledge` 第 5 区块 + `knowledge.js` 纯 helper；先写 Node 执行测试（红），
     再实现，`test_t055_pages.py` 保持绿。
3. 跑门禁（§3），写 ADR 0012、本报告、`PROGRESS.md`；按 feat/test/docs 提交；未推送、
   未关 issue、未改 `docs/spec/**`/`contracts/**`、无迁移。

## 1. 交付内容

### 1.1 非契约回答面（ADR 0012 D1）

- `POST /api/v1/knowledge-bases/{knowledge_base_id}/answer`，body `{query 1..2000,
  top_k 1..50 默认 8（0 视为默认）}`；owner-only（管理员不例外）、cookie 会话 CSRF、
  API key `application:read`；缺失/他人/删除/畸形 id → 同一个 404 `resource_not_found`。
- `200 answered`：`{outcome, answer:{text, generation, citations[]}, retrieval:{...}}`；
  citation 含 `chunk_id/document_id/document_revision/chunk_no/span/score/text/citation`，
  其中 `citation` 是 chunk 行存储的完整溯源文档（源 artifact id + sha256、parser/chunker/
  embedding revision）。`retrieval.chunk_ids` 是本次检索的有序 chunk-id 集合。
- `200 no_results`：同一形状，`answer.text=""`、`citations=[]`、`chunk_ids=[]`；
  空检索是正常空答案，不是错误。
- `422 answer_without_citation`、`422 citation_not_found`、`409 citation_unreadable`：
  既有错误信封，`error.code` 即结果码，`details.outcome` 相同；citation 两个码另带
  `details.chunk_id`。被拒绝的回答**不返回**答案文本或任何引用。

### 1.2 生成缝：带标签的抽取式 stub（ADR 0012 D2）

- `knowledge.AnswerGenerator`（`Version`/`Stub`/`Generate`）与唯一实现
  `ExtractiveStub`：引用本次检索前 `AnswerCiteLimit=3` 个片段，逐字摘录（每段 ≤600 rune）
  拼成答案；确定性（同检索 → 同文本/引用），从不访问检索之外的片段。
- 每个回答都带 `generation:{kind:"extractive_stub", version:"extractive-stub/1",
  stub:true, model_provider:"not_configured"}`；页面显示“抽取式 stub；未配置模型 provider”。
- 真实 provider **blocked**：不伪造模型输出。后续接入真实 provider 只替换
  `AnswerGenerator`，§1.3 校验不变。
- dev-stub 故障约定（部署环境、非请求参数，仅 `dev-stub`/`capacity-stub` profile 生效，
  其它 profile 启动即拒绝）：`uncited`（有答案无引用）、`foreign-chunk`（真实引用外加
  一个不存在的 `00000000-0000-4000-8000-0000000000ff`）、`slow=<duration>`（检索完成后
  延迟返回草稿，用于撤销竞态）。它只能让服务端多拒绝，不能绕过校验；与 ADR 0007 D3/
  T045 用 model id 制造 stub 故障同一约定。

### 1.3 服务端引用校验（ADR 0012 D3）

`Service.Answer` 在返回前依次执行，草稿完全不可信：

1. 引用必需：无引用 → `answer_without_citation`；
2. 集合成员：每个 cited chunk id 必须 ∈ 本次 `Query` 返回的 chunk-id 集合，否则
   `citation_not_found`；重复引用去重；
3. 返回前可读：每个引用重新走 `GetChunk`（与 `GET /knowledge-citations/{id}` 同一 SQL
   谓词：owner ACL、KB/document tombstone、`state='ready'`、
   `active_revision=revision`、`chunk.document_revision=revision`、body artifact
   ready/未删除/同 owner）；任一不满足，或复读到的 document/revision/chunk_no/正文与
   检索时不一致 → `citation_unreadable`；
4. provenance：document/revision/chunk id/span/存储 citation 全部来自服务端检索行。

### 1.4 检索 ACL 硬化（ADR 0012 D4）

- `Query` 与 `GetChunk` 的 body artifact join 增加 `a.owner_id = kb.owner_id`。
- 测试用真实伪造：Alice 的 ready 文档里插入一条真实 chunk 行，指向 Bob 的真实 ready
  封存 artifact（真实字节、真实行）；结果为：Alice 的检索候选不含它、Alice/Bob 的
  citation 端点都 404、回答的引用里也不会出现它，而数据库里那行确实存在。

### 1.5 `/knowledge` 问答视图

`apps/web/src/knowledge.html` 第 5 区块 + `assets/knowledge.js` 纯 helper：
提问（`{query, top_k}`）、显示生成方式（stub 标注）、显示答案文本/本次检索元数据/引用列表；
每条引用显示文档/版本/片段/行号并标出“不属于本次检索/引用不一致”；点击引用重新
`GET /knowledge-citations/{id}`，成功后显示服务端返回的正文，404 显示“引用不可用”，
**不**回放回答里的旧引用正文；每次提问失败先清空上一次答案面板。四种结果各有独立状态文案。
只用 `textContent`，无浏览器存储、无 object key。

## 2. AC 逐条

| # | 验收标准 | 结果 | 证据 |
|---|---|---|---|
| 1 | 无结果返回空 | **proven** | `tests/f10/test_t064_answer.py::test_an_empty_knowledge_base_returns_an_empty_answer_not_an_error`（200 `no_results`、`text=""`、`citations=[]`、`chunk_ids=[]`）；`::test_a_revoked_document_cannot_be_read_through_a_citation_after_the_answer` 撤销后重问同样返回空答案 |
| 2 | 伪引用/越权片段拒绝 | **proven** | 伪引用：`::test_a_generator_that_invents_a_citation_is_refused`（真实第二个 API 进程、真实 HTTP：422 `citation_not_found`，`details.chunk_id` = 伪造 id，无答案体）；越权片段：`::test_a_chunk_pointing_at_another_principals_sealed_body_is_never_answered`（真实伪造 chunk 行指向 Bob 的封存字节 → 检索/citation/回答全部拿不到）；跨 principal：`::test_a_foreign_principal_and_an_administrator_cannot_ask`；Go 单测 `TestAnswerDraftWithACitationFromOutsideThisRetrievalIsRefused` |
| 3 | 撤销后打开引用不可读旧正文 | **proven** | `::test_a_revoked_document_cannot_be_read_through_a_citation_after_the_answer`：先拿到带引用的回答，撤销后 DB 行与封存字节仍在（证明不是删数据），每个 citation 404 `resource_not_found` 且响应无 `text/citation/answer` 字段；`::test_a_document_revoked_while_the_answer_is_generating_is_refused`：撤销竞态下回答在返回前被 409 `citation_unreadable` 拒绝 |
| 4 | 真实端到端入口；失败/授权/重试边界有断言，不以单层 mock 成功代替 | **proven** | 全部用例走真实 `cmd/api` 二进制 + 真实 PG + 真实 HTTP + 真实封存字节 + 真实 `document_ingest` maintenance；拒绝路径用部署故障的第二个真实 API 进程；授权：A/B/admin 404、CSRF、scope；恢复/重试：撤销后重问得到新的空检索而不是旧答案；无 mock 被测对象 |
| 5 | 记录版本、输入/hash、命令、原始结果与 profile；stub/real 分报，缺条件 blocked、未运行 not_run | **proven（含显式 blocked/not_run）** | 本报告 §3/§4/§5；生成 = 抽取式 stub（real executed），真实模型生成 = blocked；性能/浏览器/后续票 = not_run |

## 3. 门禁命令与原始输出

环境：Go 1.27.3（`E:\orchara-toolchains\go\bin`）、Python 3.12.2、Node v24.13.0、
PostgreSQL 16.4（`127.0.0.1:55432`，`tools/pg_dev.py status` running）；Windows PowerShell；
`product/.env.local` 提供本地口令，本报告不含任何凭据。

```text
$ python -m pytest tests/f10 -q          # 第 1 次
.................................                                        [100%]
33 passed in 341.33s (0:05:41)

$ python -m pytest tests/f10 -q          # 第 2 次（背靠背）
.................................                                        [100%]
33 passed in 311.88s (0:05:11)

$ go build ./...
(exit 0, no output)

$ go vet ./...
(exit 0, no output)

$ go test ./...
?   	orchara/platform/cmd/api	[no test files]
...
ok  	orchara/platform/internal/knowledge	0.877s
ok  	orchara/platform/internal/memory	(cached)
ok  	orchara/platform/internal/modelproxy	(cached)
ok  	orchara/platform/internal/modelregistry	(cached)
...
?   	orchara/platform/probes/f15a_objectstore/localfs_probe	[no test files]
go test exit=0

$ gofmt -l .
(empty)

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 6.61s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

相邻套回归（本票改动触及 `internal/knowledge`、`internal/api/server.go` 路由、`cmd/api`
启动装配、`apps/web/src/{knowledge.html,assets/knowledge.js}`）：

```text
$ python -m pytest tests/tooling -q
21 passed in 6.61s
```

> 说明：门禁日志第一次写在仓库根 `gate-t064.log`，`sync_spec --check` 因此报
> `upstream_new gate-t064.log`（规格源多了一个文件），把日志移出仓库后复检 no drift；
> 这是本票检查方式的失误，不是契约漂移。

合并 `integration/f13a-f15a-f09-f10`（merge 时的 tip = `9f58fc2`，与本分支切出基线相同）：
`git merge --no-ff` 返回 **"Already up to date"**，没有产生新的 merge commit，也没有冲突
（分支已包含集成 tip；报告的 `MERGE-COMMIT` 记为 `9f58fc2`）。merge 后复跑目标门禁：

```text
$ python -m pytest tests/f10 -q          # merge 之后
.................................                                        [100%]
33 passed in 336.63s (0:05:36)
```

> 说明：全量 `pytest tests` 按 BRIEF §3 不在本票运行（≈67 分钟），由 orchestrator 在合并后
> 执行。本票运行 `tests/f10`（含既有 T055 的 20 个用例）与 `tests/tooling`。`tests/f00b`
> 等相邻套未整跑：本票对共享代码的改动只有两条 SQL 的 owner 谓词、`knowledge.New` 的默认
> Answerer 与新增路由/装配，`go test ./...` 覆盖编译与单测；f10 全套（含 T055）已证明
> 既有检索/引用/摄取不回归。

## 4. 真实栈、输入与版本

- 真实 PostgreSQL 16.4：每个用例新建临时库，跑真实 `cmd/admin migrate`（0001→0035，迁移链
  连续）；真实 `cmd/api` 二进制；真实 HTTP；真实 HMAC 签名对象链接与文件系统 `staged/`、
  `sealed/` 字节；真实 `cmd/maintenance` 进程（work=`document_ingest`、
  system_io=`artifact_finalize`）；拒绝路径另起一个真实 API 进程（同一数据库/对象目录，
  自己的 mTLS 证书与端口）。
- 输入/哈希：源文档由 `tests/support/t055.py::SAMPLE_DOCUMENT` 确定性生成；测试端上传时
  计算 SHA-256；回答里每个 citation 的 `source_sha256` 与重新读取的封存源字节 SHA-256
  比对；`[]rune(源文本)[start_char:end_char] == citation.text`、行号由源文本换行计数
  独立核对；citation 的 document id/revision/chunk_no/span/score 与先独立调用
  `/retrieval-queries` 的结果逐项比对。
- 版本：`extractive-stub/1`（生成器）、`local-hash-v1`（384 维 embedding，T055）、
  `orchara-text-parser/1`、`orchara-paragraph-overlap-chunker/1`、迁移 0035（T052，
  本票无迁移）。
- 原始结果：`tests/f10` 原始 pytest 输出见 §3；第二个 API 进程日志由 `tests/support/t064.py`
  写入 pytest 临时目录（`api-*.log`），撤销竞态等待其中的
  `knowledge answer stub: slow fault waiting` 行作为“检索已完成”检查点。
- 时序裕量（BRIEF §6.1）：撤销竞态注入 `slow=5s`，测试在日志检查点（检索完成后、草稿
  返回前）提交 tombstone，到服务端校验还有 ≥5s；`worker.join(timeout=30)`。断言不依赖
  <5s 的时钟窗口，未使用固定 sleep 断言“没有发生”。

## 5. stub / real、blocked、not_run（分开记录）

| 项 | 结论 | 具体条件 |
|---|---|---|
| 回答生成（`extractive-stub/1`） | **real executed，但是 stub** | 确定性抽取本次检索片段；真实 HTTP 返回，带 `stub=true`/`model_provider=not_configured`；不是模型，不承诺语义质量 |
| 真实模型生成 provider | **blocked** | 本环境无 provider 凭据、构建无 adapter；`AnswerGenerator` 只有 stub 实现；从未把 stub 输出呈现为模型答案 |
| dev-stub 故障开关 | **real executed（stub 故障）** | `ORCHARA_KNOWLEDGE_ANSWER_FAULTS` 仅 dev-stub/capacity-stub；非 stub profile 启动拒绝（fail closed）；只能触发拒绝，不能绕过校验 |
| 引用校验 / ACL 硬化 | **real executed** | 真实 PG 行 + 真实封存字节 + 真实 HTTP；含真实伪造 chunk 行（他人封存正文）与撤销竞态 |
| 跨进程 / 重启后的回答 | **not_run** | 本票不持久化回答（ADR 0012 D5），读路径每次重新检索；无跨进程缓存可测 |
| 检索缓存 / 规模 / ANN | **not_run / 后续** | T067（#68）；本票不缓存“可读性结论”，`acl_epoch` 仍由 T055 提供 |
| 版本重建 / 激活切换 | **not_run / 后续** | T061（#62）；本票只用当前 `active_revision` 语义 |
| 删除治理（deleteDocument job） | **not_run / 后续** | F07/T058；测试用 F07 形状的 tombstone 证明撤销后不可读 |
| CSV/XLSX、PDF | **not_run / 后续** | T059（#60）、T060（#61） |
| 浏览器 DOM 驱动页面 | **not_run** | 只做服务端页面结构 + Node 执行纯 helper；与既有页面票同一边界 |
| 全量 `pytest tests` | **not_run** | BRIEF §3 规定由 orchestrator 合并后执行（≈67 分钟） |

## 6. 下一票接口（复用点）

- **回答面**：`knowledge.Service.Answer(ctx, ownerID, baseID, query, topK)` 返回
  `AnswerResult{Outcome, Answer, Retrieval, OffendingChunkID}`；新增生成器只需实现
  `knowledge.AnswerGenerator`（`Version/Stub/Generate`），D3 校验始终在服务层执行。
- **可观察结果**：`OutcomeAnswered/OutcomeNoResults/OutcomeAnswerWithoutCitation/
  OutcomeCitationNotFound/OutcomeCitationUnreadable`；HTTP 码分别为 200/200/422/422/409。
- **检索集合**：`AnswerRetrieval.ChunkIDs` 是“本次检索”的权威集合；T067 若引入缓存，
  缓存命中后仍必须重新执行 citation 的 `GetChunk` 复检（缓存不得缓存可读性结论）。
- **ACL 硬化**：`Query`/`GetChunk` 已要求 chunk body artifact 与 KB 同 owner；T058 删除
  artifact 时必须让两者同时不可读（现有谓词已覆盖）。
- **页面**：`knowledgeAnswerPath/buildAnswerBody/describeAnswer/describeAnswerCitation/
  describeAnswerGeneration/describeAnswerRetrieval/describeAnswerRefusal/answerOutcomeText`
  均可在 Node 中直接执行；页面不存浏览器存储。
- **ADR**：`product/docs/adr/0012-f10-grounded-answers.md`；与 ADR 0010 的关系：0010 管
  摄取/检索/引用/诊断面，0012 管回答面与引用校验。

## 7. 偏差与边界（均已记录）

1. 回答面是**非契约路径**（契约无检索/回答操作）——ADR 0012 D1，沿用 ADR 0005/0007/0010
   先例；`contracts/**`/`docs/spec/**` 未改，`sync_spec --check` no drift。
2. 生成是**带标签的抽取式 stub**（无模型 provider）——ADR 0012 D2；报告 §5 单列 blocked。
3. dev-stub 故障开关是**部署配置**（非请求参数），仅 stub profile 生效且只能造成拒绝——
   ADR 0012 D2，与 ADR 0007 D3/T045 同一约定。
4. 检索/引用 SQL 增加 `a.owner_id = kb.owner_id`（T055 谓词的补强，非契约变化）——
   ADR 0012 D4。
5. 不持久化回答、无缓存、无迁移——ADR 0012 D5。
6. 票面/简报写“通过 `GET /knowledge-chunks/{id}` 打开引用”；T055 的实际非契约路径是
   `GET /api/v1/knowledge-citations/{chunk_id}`（ADR 0010 §3），本票沿用后者，不新增第二套
   引用路由（同一 SQL 谓词复用为返回前复检），`/knowledge` 页面也用它。
