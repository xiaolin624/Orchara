# F08 T084 验收：冻结路由语料，并由第二个用户完成销售旅程

- 任务：F08 / GitHub Issue #85（票 `T084`），分支 `t/t084-routing-evaluator`，
  worktree `E:\Orchara-wt\t084`（产品代码在 `product/`）。
- 状态：**`passed`**（本票范围 = 六单元格冻结路由语料 + 真实 REST 评测 harness +
  真实 T068 发布 + 跨用户销售旅程 + 失败/队列/过期状态可理解 + Node 执行真实
  `chat.js` 字节）。真实模型基线、浏览器 DOM 仍 `not_run`（见 §6）。
- 运行环境：真实 PostgreSQL 16（隔离集群 `127.0.0.1:55432`，`tests/support/stack.py`
  建库/迁移）、真实编译 `cmd/api` / `cmd/maintenance` / `cmd/supervisor`、真实 WSL2/runsc
  沙箱（发布证据与旅程执行）、Python 3.12.2、Node 24.13.0。路由控制模型是
  `model_profile_versions` 中真实注册、`validation_state=passed`、`config.route_control=true`
  的 **dev-stub**（`provider=stub`, `stub:true`）。所有路由断言由服务端策略与数据库行决定，
  不依赖 stub 的“智能”。
- 权威机器可读结果：`reports/f04b/T084-routing-eval.json`（由本次真实执行生成；
  `git_head`、语料 sha256、catalog 版本、逐例 expected/actual、汇总率都在文件内）。
- 语料 sha256 registry：`tests/f08/routing/datasets/registry.json`
  （`corpus_version=1.0.0`，`registry_sha256=491e4f25…`）。

## 0. 一句话结论

A 通过真实 `submit → admin approve` 发布销售应用后，B 以另一 principal 从 REST 入口
路由到该版本、真实 supervisor 执行、下载 `summary.csv`，并用独立 Decimal 真值校验通过；
A 读 B 的 Run/产物得到同一 404。34 条冻结路由案例全部得到期望决策：误自动执行 0，
澄清完成率 100%，候选召回 1.0，未见应用/越权版本零泄漏。

## 1. 交付内容

### 1.1 冻结路由语料（非契约，测试资产）

位置 `product/tests/f08/routing/datasets/`：

- 六个单元格文件：`unique`、`ambiguous`、`missing_input`、`no_match`、
  `unseen_application`、`unauthorized_candidate`。
- 34 例 = `frozen_test` 25 例（18 个独立 family，每格 ≥3）+ `dev` 9 例（8 个 family，
  覆盖全部六格）。dev/frozen 按**消息模板 family** 隔离：同一 family 的改写只在同一 split；
  同一消息字符串不得跨 split（`corpus.validate_corpus` 断言）。
- 每例字段：`case_id/category/family_id/split/message/attachments/expected{decision,
  application,candidates,interaction,no_run_reason}/provenance{origin,reference,
  license_review,note}`。
- `registry.json`：每个语料文件与 `inputs/sales_sample.csv` 的 sha256 + size，
  另含 family→split/cell 清单与唯一 catalog 描述。冻结测试逐字节校验；改一个字节即失败。
- 数据来源/许可：只用仓库公开样例 `docs/spec/examples/sales/input.csv`（逐字节复制）
  作为附件；消息是按能力卡手工撰写的英文/中文改写；无真实用户数据、无第三方语料、
  无模型生成文本；`license_review=not_applicable`，语料是诊断集，不作为可再分发训练数据。
- 语料预冻结匹配论证：`tests/f08/routing/README.md` §Matching rationale（服务器
  `messageTokens` + 卡文本的 token 证据；最终由真实运行复核）。

### 1.2 评测 harness（真实栈，非 mock）

`product/tests/f08/routing/runner.py` + `product/tests/f08/test_t084_routing_eval.py`：

- 发布：`tests/support/t068.py:genuine_sales_report` 跑真实五类销售评测（WSL2/runsc），
  再由 creator `submit_publication`、admin `decided_review(approve)` 发布不可变版本
  （本次 `publication.mode=t068_real`，`review_id=fd8d1a94…`，
  `version_id=c4c9266a…`，`manifest_sha256=844cad66…`，五类案例 5 条）。
- catalog：`sales` 为真实 T068 发布；`forecast/inventory/invoice/alpha/beta/payroll`
  为真实编译 manifest + 文档化 F08/T077 版本行（`publication=t077_fixture`）；B 通过
  admin grant 获得前 6 个的 read，`payroll` 故意无 grant。
- 第二用户：`support/f08.account` 真实 invite+activate；`POST /api/v1/requests`（cookie+
  CSRF + `Idempotency-Key`）；`cmd/maintenance --kinds route_request` 真实路由。
- 逐例记录：decision/state/error_code/候选（含版本 id 与 rank）/interaction/run/owner/
  `false_auto_execution`/`clarification_required|completed`/`final_file_ok`/耗时。
- 指标由真实记录计算（`runner.aggregate`），不手写：误自动执行率、澄清完成率、
  候选召回、无候选泄漏、运行创建数。
- 输出：`reports/f04b/T084-routing-eval.json`（证据运行设 `T084_EVAL_REPORT`；
  普通 pytest 只写自己的 tmp 文件，避免测试运行改写仓库）。

### 1.3 第二用户销售旅程（AC1）

`test_t084_routing_eval.py::_journey`：B 上传公开 CSV（`text/csv`、`task_input`）→
消息 `{"source":"sales_sample.csv","message":"把这份销售明细汇总成报告"}` →
路由自动派发 → Run owner=B、版本=发布版本 → 真实 supervisor 执行成功 →
`/runs/{id}/node-results` 找到 `summary.csv` → `/artifacts/{id}/content` 下载 →
`tests/support/t049.py:check_summary_csv`（Decimal 独立真值）+ 已知正确字面量
`region,total\n东区,200.00\n西区,50.00`；`summary.json`/最终输出同真值；
附件的 `sha256` 与记录一致。A 读 B 的 Run 与产物均为 404 `resource_not_found`，
匿名 401。执行经历 1 次 supervisor 尝试（JSON `journey.execution_attempts`）。

### 1.4 “无匹配/队列/过期状态可理解”

- `apps/web/src/assets/chat.js`：新增 `REQUEST_FAILURE_REASONS`、
  `describeRequestFailure`、`applyRequestFailure`；`renderRequest` 现在渲染
  `error_code` 的用户可读原因（至少 `no_matching_application`、`interaction_expired`、
  `interaction_closed`，另有 `route_candidate_invalid` 等），稳定码保留在
  `data-error-code`，并保留 `data-retryable`；未知码显式显示原因码。七状态标签
  （`failed: '失败'` 等）语义未改。
- `apps/web/src/chat.html`：请求面板新增 `#chat-request-error`（`hidden` 初始）。
- `tests/f08/test_t084_pages.py`：Node + 最小 DOM shim 执行**真实**
  `app.js/attachments.js/chat.js` 字节：页面加载、会话选择、队列三行（位置 1/2/3、
  “排队中”、取消按钮）、800ms 轮询、提交路径、队列行点击；断言 no-match 显示
  “未找到匹配的应用”、过期交互显示“过期…重新提交”、`排队位置：第 2 位` 至少被真实
  渲染过一次、未知码显示原因码、非 failed 时隐藏。浏览器 DOM 仍 `not_run`。
- `tests/f08/test_t084_states.py`：真实 REST 证队列位置 header（第 2 位）、取消占槽
  Run 后下一请求按序推进、交互过期 → 请求 `failed/interaction_expired` 且旧答复
  409 `interaction_closed`、无匹配 → `failed/no_matching_application` 且零 Run/零交互。

## 2. AC 对照

| AC | 结论 | 证据 |
|---|---|---|
| 1. 销售发布后其他用户独立完成 | **passed**（real publish + real run + verified CSV；A 无权限） | `publication.mode=t068_real`；`journey.final_file_ok=true`；`go` 真值检查 `t049.check_summary_csv`；ACL 404/401 |
| 2. 无匹配/队列/过期状态可理解 | **passed**（REST `error_code` + 真实 chat.js 渲染 + 队列/过期测试；浏览器未驱动） | `test_t084_pages.py` 2 passed；`test_t084_states.py` 3 passed |
| 3. 记录误自动执行与澄清完成率 | **passed**（真实执行汇总） | frozen: 0/18 必须不自动执行的案例被自动执行；clarify 9/9；dev: 0/7；4/4 |
| 4. 真实入口演示；失败/授权/重试边界有断言 | **passed**（REST + 真实发布/执行；请求级无 SSE，Run 事件 SSE 属 T032） | 未授权候选 `route_candidate_invalid` 零 Run；未见应用零候选；`interaction_closed` 409；重试仅用于已知沙箱抖动且逐次记录 |
| 5. 版本/输入 hash/命令/原始结果/环境；stub/real 分报 | **passed** | JSON `environment`/`corpus`/`catalog`/`publication`/`journey`；§5 原始输出；stub 只标在控制模型 |

## 3. 评测结果（来自 `reports/f04b/T084-routing-eval.json`）

| split | cases | families | decision accuracy | 误自动执行 | 澄清完成 | 候选召回 | 零泄漏 | 创建 Run |
|---|---|---|---|---|---|---|---|---|
| `frozen_test` | 25 | 18 | 1.0 | 0 / 18（0.0） | 9 / 9（1.0） | 1.0 | 是 | 6 |
| `dev` | 9 | 8 | 1.0 | 0 / 7（0.0） | 4 / 4（1.0） | 1.0 | 是 | 2 |

逐单元格（cases / decision_matches / false_auto / clarify_required→completed）：

| cell | frozen | dev |
|---|---|---|
| unique | 6/6/0/0→0 | 2/2/0/0→0 |
| ambiguous | 4/4/0/4→4 | 1/1/0/1→1 |
| missing_input | 5/5/0/5→5 | 3/3/0/3→3 |
| no_match | 3/3/0/0→0 | 1/1/0/0→0 |
| unseen_application | 4/4/0/0→0 | 1/1/0/0→0 |
| unauthorized_candidate | 3/3/0/0→0 | 1/1/0/0→0 |

关键逐例事实（JSON 全量）：

- `unique` 六例全部 `auto_dispatch`，Run owner=B、版本=发布版本；候选仅 `sales`。
- `ambiguous` 四例 `awaiting_selection`，候选集合 `{alpha,beta}` 全可见；答复 alpha 后
  派发到 alpha，Run 属 B。
- `missing_input` 五例 `awaiting_input`，候选分别为 inventory/invoice/forecast；
  真实附件答复后派发。
- `no_match`/`unseen_application`：`failed/no_matching_application`，候选空、
  零 Run、零交互；隐藏 `payroll` 版本 id 从未出现在任何候选快照（逐例断言）。
- `unauthorized_candidate`：stub 按消息中 UUID 建议隐藏版本 → 服务端
  `failed/route_candidate_invalid`，零 Run；候选快照仅 `sales`。
- `journey`：`run_id=ddb98571…`，`summary_csv_sha256=bb60f942…`，
  `input_sha256=a6be4cf4…`（公开样例），supervisor `executed/succeeded`（2738ms），
  A 读 Run/产物 404、匿名 401。

## 4. 口径、诚实与不可比声明

- **控制模型 = dev-stub**：`stub:true`，无厂商凭据。本报告的“决策正确率”是服务器策略
  （ACL→候选→输入→策略）在冻结语料上的确定性结果，**不是**模型质量基线；真实模型 /
  强提示对照属 T090（F14），本票不声明。
- 候选检索是授权 SQL + 字面/全文匹配，不宣称语义召回或中文分词质量；
  “必填输入”判定是“每个 required_inputs 需要 1 个显式附件”的保守确定性规则（T079 已记录）。
- 五个 fixture 应用的版本行使用文档化的 F08/T077 形状（真实编译 manifest + 真实
  review/version 行）；**只有销售应用**走真实 T068 发布事务。JSON 中逐应用标注
  `publication`，`t068_real` 与 `t077_fixture` 分开。
- `git_head`/`generated_at` 是生成时点的 HEAD（评测文件当时在工作树、随后以 `78f67d5`
  提交）；重跑会重写 JSON（字节随 id/时间变化），但汇总率与断言可复现。
- published Run 的执行入口把聊天正文按原字节封存为 Run 输入，销售链 start 节点要求
  JSON 对象；因此**旅程消息是 JSON 任务文档**。同一销售应用的**自然语言**消息会路由成功
  但在执行时 `node_input_invalid`（本次探针实测，未写成语料）；这是 F08 对话入口与
  F04B 执行入口之间的集成缺口，见 §6 风险，不隐藏、不冒充通过。
- 环境沙箱有一次已知抖动（heartbeat 与工具退出竞态，历史 T044/T048/T049 亦有记录）。
  旅程仅对“该已知抖动”做有界重试并把每次尝试写进 JSON；本次 1 次成功，未触发重试。
  T068 发布内部的既有重试同样是环境吸收，不改变发布判定。

## 5. 实际命令与原始结果（尾部）

环境：`tests/support/t033.py`/`stack.py` 自动建隔离集群与库；本机 PostgreSQL 16 服务
`postgresql-x64-16`；无需 npm/浏览器。

```text
$ cd E:\Orchara-wt\t084\product
$ $env:T084_EVAL_REPORT='…\product\reports\f04b\T084-routing-eval.json'
$ python -m pytest tests/f08/test_t084_routing_eval.py -q -s
T084CASE {... 34 lines ...}
T084: routing evaluation report written to E:\Orchara-wt\t084\product\reports\f04b\T084-routing-eval.json
T084: summary={"splits": {"dev": {... "false_auto_execution_rate": 0.0, "clarification_completion_rate": 1.0,
  "candidate_recall": 1.0, "no_candidate_leaks": [], ...}, "frozen_test": {... same ...}}, ...}
.
1 passed in 84.91s (0:01:24)

$ python -m pytest tests/f08/test_t084_pages.py -q
2 passed in 10.34s

$ python -m pytest tests/f08/test_t084_corpus.py tests/f08/test_t084_states.py -q
7 passed in 46.93s
```

门禁（完整套件）原始输出尾部：

```text
$ python -m pytest tests/f08 -q          # 合并 08af658 之后的完整套件（本次实际跑完的完整套件）
........................................................................ [ 91%]
.......                                                                  [100%]
79 passed in 948.05s (0:15:48)

$ go build ./... ; go vet ./... ; go test ./... ; gofmt -l internal cmd tools
build_exit=0  vet_exit=0  test_exit=0
gofmt -l internal cmd tools: (empty)

$ node --check apps/web/src/assets/chat.js
exit 0

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

结束前两次合并集成分支：`08af658`（T083 画布创作，仅 `PROGRESS.md` 冲突，保留两票小节）
与 `111e2c2`（T083 图运行/condition 分支，无冲突）。第一次合并后完整 `tests/f08` 为
**79 passed 948.05s**（原始 tail 如上）；第二次合并后 Go/静态门禁复跑全绿
（`build_exit=0`、`vet_exit=0`、`test_exit=0`、`gofmt` 空、`sync_spec` no drift、
`node --check` exit 0）。但**最终合并树上的完整 `tests/f08` 与按文件 T084 复跑均为
`not_run`**：两次运行都在共享 WSL/runsc 状态根争用下长时间低 CPU 等待，被编排者终止
（非代码失败；按文件套件只跑到 4 个点、无汇总行，因此不写成通过）。最终树上确实完成的
门禁只有 Go/静态/`node --check` 与语料 freeze 校验；不把未完成的运行冒充为通过。
`docs/spec/**`、`contracts/**` 未改。

## 6. 未运行 / 边界（not_run，不用 0 冒充）

| 项 | 状态 | 原因 |
|---|---|---|
| 真实厂商模型 / 同基模强提示基线 | `not_run` | 本机无任何模型凭据；只有 dev-stub。属 T090/F14 |
| 浏览器 DOM 驱动（Playwright） | `not_run` | 无浏览器驱动；以 Node + 最小 DOM 执行真实 `chat.js` 字节（与 T074/T082 同边界） |
| 请求级 SSE | `not_run` | 产品明确是 REST + 800ms 轮询；Run 事件 SSE 属 T032 |
| 自然语言消息驱动的 published Run 成功执行 | `not_run`（探针证实会 `node_input_invalid`） | 执行入口把聊天正文当 Run 输入；销售链 start 节点要求 JSON 对象。F04B/T083 范围 |
| 规模/统计充分性（60/100 例） | 不声称 | 34 例是起步规模；EVALUATION 明确“不等于统计充分” |
| 最终合并树上的完整 `tests/f08` 与按文件 T084 复跑 | `not_run` | 两次最终树运行在共享 WSL/runsc 状态根争用下被编排者终止（低 CPU 长时间等待，非代码失败）；同树 Go/静态门禁全绿；第一次合并树上的完整套件 79 passed 948.05s 与按文件套件记录在上文 |

## 7. 风险与后续

1. **F08↔F04B 输入形状**：聊天正文直接作为 published Run 的封存输入；带结构化
   `input_schema` 的应用无法由自然语言直接执行。建议 T083/F04B 在派发时把
   `{message, artifact_ids}` 归一化成任务文档，或让 start 节点声明显式输入模式；
   本票语料只评测路由（不执行），旅程使用 JSON 任务文档并已在报告标注。
2. **候选检索是字面/全文匹配**：能力卡措辞决定候选召回；本语料刻意让每格消息与目标卡
   共享 token，未覆盖“同义不同词”的语义召回；不据此宣称模型路由质量。
3. **沙箱 heartbeat 竞态**：历史遗留抖动可能在旅程执行出现 `sandbox_failed`
   （“attempt lease was lost”）；本票有界重试并记录，未修产品代码（F04A/F06 范围）。
4. **语料是测试资产**：不进 `contracts/`，不被训练/发布流程消费；未来若升级为契约
   数据集，需先扩 schema 断言词表（现在不能表达 routing 结果）。
