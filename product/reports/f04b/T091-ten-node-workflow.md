# F04B/T091 — 保存并恢复含十节点的复杂工作流（GitHub #92）

- 状态：**passed**（十类画布/导出 + 十类 fixture 保存/校验 + 真实两次审批嵌套汇合运行 + optional/required/未知不降级；边界见 §6）- 分支：`t/t091-ten-node-graph`；worktree `E:\Orchara-wt\t091`；基线 `integration/t083-t091` @ `15db973`（含 T083–T090 全部合并）
- 契约：**未改** `contracts/**`、`docs/spec/**`、迁移编号；`python tools/sync_spec.py --check` → `no drift`
- 环境：Windows；Go 1.27.3；Node v24.13.0；Python 3.12.2 / pytest 9；PostgreSQL 16 隔离集群 `127.0.0.1:55432`；WSL2 Ubuntu-24.04 + runsc；模型仅 `ORCHARA_PROFILE=dev-stub`（无厂商凭据）

本票是**集成票**：不重写任何节点类型，只组合已合并的 T083（runtime/canvas/runview）、T085（并发/optional）、T086（model）、T087（approval）、T088（retrieve）、T089（call_app）实现，补齐十类画布/导出一致性、十类 fixture 往返与跨票验收测试，并修复集成暴露的等待调度缺陷（见 §2.5）。

## 1. AC 对照（票面 7 条）

| # | 票面 AC | 结论 | 证据 |
|---|---|---|---|
| 1 | 十类节点均可配置保存（start/end/model/agent/python/call_app/retrieve/condition/join/approval） | **passed** | `NODE_TYPES` 十类；`defaultNode` 每类 config 与契约白名单逐键一致；palette/panel 十类（bundle marker + palette Node 用例）；`tests/f04b/fixtures/ten_node_definition.json` 含十类各至少一个；真实 `PUT /draft` 保存 → GET 结构完全一致、读取与再保存逐字节稳定；`POST /validate` `valid:true` + `X-Manifest-SHA256`；debug 提交把十类定义封入 manifest（节点类型集合、条件端口、`subapp_versions` 均为真实值） |
| 2 | 非法连线/类型错误有定位 + 中文可操作修复提示 | **passed（数据/文案层；浏览器交互 not_run）** | `regionProblems` 对十类 fixture 返回 `[]`；`errors.js` 把 `/nodes/<i>/config/<field>` 映射到十类面板字段、`/edges/<i>` 映射到边（source→target、edgeId），中文 hint；真实栈：跨区边 `cross_region_edge /edges/N`、坏端口 `bad_port /edges/1`、join 来源 `bad_join_sources /nodes/8/config/required_sources`、类型错误 `type_mismatch /nodes/1/config/cases/0`、环 `cycle`、未知节点类型 `unknown_type` 全部 `valid:false` 精确定位；跨区变体 debug 提交 422 `validation_failed` 同定位、零 Run/零 Job |
| 3 | 不能注入 UI 未展示的能力（导出白名单 + 服务端拒绝） | **passed** | 画布 `CONFIG_ALLOWLIST` 十类；Node 用例：干净十类 fixture 导出无 `dropped`；每类注入未知键 → `dropped[{path,reason:unknown_config_key}]`；未知节点类型 → `unknown_node_type`；`api_key`/`run_input`/`endpoint_ref`（definition 或 layout）→ `ExportRefused`；服务端：model config 注入面板从不展示的 `response_schema` + `api_key` → `bad_config` 定位 `/nodes/3/config/...`，未知类型 `mystery` → `unknown_type`（保存成功但 validate/debug 拒绝） |
| 4 | 两次审批后汇合成功（真实运行） | **passed** | 真实栈 `test_two_approvals_resume_the_same_run_through_the_nested_join`：`start→choose→(approve1→fork→{py,agent}→join2 | direct)→join1→approve2→summarise(model)→end`；exec1 `waiting_approval`（approve1 pending、无 resume）、决定 → exec2（新进程）跑到 approve2 `waiting_approval`（join2/join1 已各执行一次）、决定 → exec3 succeeded；两次 approval 行、两个 resume job、approve1/approve2 各 1 条 data 行；join1/join2 各恰 1 条 data、`succeeded` 事件各恰 1 次；end 输出按来源读外层 join 封套（`join1`, `join2`） |
| 5 | optional 只吞允许的纯读错误 | **passed** | 真实栈 `test_optional_failure_is_folded_into_the_join_envelope_and_the_run_succeeds`：py 分支 optional 且输出 schema 不匹配 → `node_output_invalid`；join2 信封 `py={status:"failed",error_code:"node_output_invalid"}`（无 `value`）、`agent` succeeded；run succeeded；end 输出把失败当作数据（`join2.results.py.status=="failed"`，无 value）；py 无 data 行；join2 恰 1 行 |
| 6 | unknown 与越权不降成可选成功 | **passed** | 真实栈 `test_an_optional_agent_capability_failure_is_never_downgraded`：join2 的 agent 源为 optional，审批后撤销 golden model profile → exec2 `model_profile_unavailable`，run **failed**（不是 succeeded）；join2/join1/approve2/summarise/end 全 `skipped`、无 join 行、无 final output。服务端注入未展示能力键/未知类型亦在 validate 阶段拒绝（见 AC3） |
| 7 | 真实端到端演示 + 失败/授权/重试边界 + 版本/输入 hash/命令/原始结果/环境；stub/real 分报 | **passed** | §3/§4/§5；版本：manifest `orchara-execution-manifest/1.0`、`compiler_version=orchara-compiler/1.0.0`、`X-Manifest-SHA256` 与 run 的 `manifest_sha256` 一致、sandbox rootfs digest/runsc；输入：`requires_approval=true` + `task` 文本、CSV `sha256=f226b1fe…`；本轮真实栈原始输出见 §4；stub（dev-stub 模型、Go fake seam）与 real（PG/mTLS/supervisor/runsc/API）分列 |

补充（brief §5 优先级）：

| 优先级 | 交付点 | 结论 |
|---|---|---|
| P0 | 十类 palette/面板/allowlist/往返；十类 fixture 保存/validate；两次审批嵌套汇合；optional/required/unknown 断言 | **passed**（§1 AC1–6） |
| P1 | run-view/审批页展示；恢复不重置；错误修复提示 | **passed（run-view 审批行显示、SSE 重连重建、预算/次数不重置）；浏览器 DOM not_run** |
| P2 | 更多组合变体；导出负例矩阵 | **partial**：十类导出正/负例矩阵 + 跨区/端口/来源/类型/环/未知类型/注入能力变体；未追加“三层嵌套并行+条件”新组合（现有 P05 语料 + T083/T085 已覆盖） |

## 2. 实现

### 2.1 画布十类（`apps/web/canvas/src`）

- 十类已在 T086/T087/T088/T089 合并进 `dsl.js`（`NODE_TYPES`/`NODE_LABELS`/`REPEATABLE_NODE_TYPES`/`defaultNode`/`DEFAULT_POSITIONS`）与 `main.js`（十类面板）；`CONFIG_ALLOWLIST` 十类（start/end `{}`；python `tool_version`；agent 五键；model `model_profile_version/prompt_template/response_mode`；retrieve `knowledge_base_ids/top_k`；call_app `application_version`；condition `join_id/default_port/cases`；join `mode/fork_id/required_sources/optional_sources`；approval `title/expires_seconds`）。
- T091 新增 `tests/f04b/ten_node_canvas.test.mjs`（12 用例，**PASS 12**）把十类作为一组钉住：palette/默认 config、真实 fixture 的 `definitionToGraph→graphToDefinition` **逐字节**往返（拓扑序、条件端口、join sources、各 config 全保留）、嵌套 region 本地证明、来源选择器（join 后按来源 `results/<source>[/value]`、retrieve `#/citations` 整数组、call_app 映射候选、approval proposal 对象、分支输出/内层 join 隐藏）、导出白名单/dropped/敏感键拒绝、十类面板字段与边的定位/中文提示。
- 画布源码**无需改动**：`npm ci --offline && npm run build` 重建后与已提交 `canvas.js`/`canvas.css` **逐字节一致**（无过期产物）；`test_t091_canvas.py` 仍保留 stale-bundle 守卫（node_modules 存在时执行重建比较）。
- 浏览器 DOM 交互 not_run（仓库无浏览器驱动，沿用 T083/T089 边界）。

### 2.2 十类 fixture 与保存/校验（`tests/f04b/fixtures/`）

`ten_node_definition.json` + `ten_node_layout.json`：13 个节点覆盖十类；`choose(condition)` 的 yes 分支内嵌 `fork(python)`/`join2` 嵌套 region（`join2` 的 `required_sources=[agent]`、`optional_sources=[py]`），条件出边 `yes`/`no`，join 后按来源绑定：

```text
start → choose ─yes→ approve → fork ─→ py(optional) ─┐
           │                    └→ agent           → join2 ─┐
           └─no→ direct(model) ──────────────────────────→ join1 → retrieve → approve2 → child(call_app) → end
```

- 固定引用：model `22222222-…`（seeded passed profile）、python `11111111-1111-4111-8111-111111111111`（seeded ready+approved preset tool）、`call_app` 的 child version 由 T078 真实 producer 创建后替换 `__CHILD_VERSION__`、retrieve 的 KB 为真实 owner KB（`POST /knowledge-bases` 创建后替换 `__KNOWLEDGE_BASE_ID__`）。
- `tests/f04b/test_t091_ten_node_workflow.py::test_the_ten_node_fixture_saves_reloads_byte_stably_and_validates`：
  1. `PUT /draft` 接受（保存不做 DSL 校验）→ `GET` 结构等于 authored fixture；两次 GET 的响应体逐字节相同；把 GET 回来的 definition/layout 再 PUT 后 GET 仍逐字节相同（jsonb 在首次写入时规范化键序，此后读取/再保存均字节稳定；报告中明确此口径，不声称原始上传文本被逐字节保留）；
  2. `POST /validate` 200、`valid:true`、`X-Manifest-SHA256` 64 hex；十类 check 每类 config 键集合恰好等于契约白名单；
  3. debug 提交（真实 CSV artifact）202：`execution_snapshots.manifest.definition` 的类型集合为十类、条件端口为 yes/no、`subapp_versions == [child_version_id]`（固定 child 版本进入冻结 manifest）。
- 非法变体（真实 `PUT` 仍 200，`POST /validate` `valid:false` 精确定位）：跨区边（append `fork→escapee`, `escapee→join1`）、坏端口 `bad_port /edges/1`、join 来源缺失 `bad_join_sources /nodes/8/config/required_sources`、`type_mismatch /nodes/1/config/cases/0`、`cycle`、model 注入 `response_schema`+`api_key`（`bad_config /nodes/3/config/...`）、未知节点类型 `mystery`（`unknown_type`）。跨区变体另做真实 debug 提交：422 `validation_failed`、同 pointer、零 Run/零 Job。

### 2.3 两次审批 + 嵌套汇合运行（真实栈）

`tests/support/t091.py` 构造验收图（结构见 §1 AC4），真实栈 `test_two_approvals_resume_the_same_run_through_the_nested_join` 断言：

- exec1：`waiting_approval`，`report.error_code=approval_required`；waiting kind=approval、`resume_job_id=NULL`；approve1 pending（服务端 proposal 内容 `subject/amount`）、approve2 尚未创建；approve1/join1/join2 无 data 行；
- 决定 approve1（契约 `POST /approvals/{id}/decision` + `params_sha256`）→ 唯一 resume job；
- exec2（新进程）：`waiting_approval`，approve2 pending，approve1 approved；fork/py/agent 真实 runsc 并发、join2/join1 各 1 条 data；join2 封套 `agent.value.text`（dev-stub digest）/`py.value.total=="250.00"`；join1 封套 `join2 succeeded+value`、`direct skipped`；condition data `{port:"yes",matched_case:0}`；
- 决定 approve2 → 第二个 resume job；exec3：`summarise`（model 单次生成）→ end → run succeeded；
- 恢复不重置：两次 approval、两个 resume、每节点 data 恰 1 条、join succeeded 事件各 1 次（重放不重复触发 join）、capability operations 恰 2 条（`agent:agent:model:1`、`model:summarise:1`）各 1 次 attempt、`budget_limit="1.00"` 不变、usage_ledger 6 行 entry_key 唯一；
- 恢复不重置/不伪造状态：除 unselected `direct` 的 `skipped` 外每个已执行节点终态 succeeded；SSE 重连回放与 DB `node.status` 序列一致；重新 GET node-results + artifact 内容可重建审批决定、条件端口、两层 join 封套（run view 数据面）；
- end 输出：`{join1:<outer join 的 results 按来源映射>, join2:<内层 join 的完整封套>, second_approval:<approve2 id>, summary:<16 hex>}`，即 **join 后按来源取值**（`node:join1#/results` 与 `node:join1#/results/join2/value`）。

### 2.4 optional / required / unknown 三组边界（真实栈）

| 变体 | 结果 |
|---|---|
| py optional + 输出 schema 不匹配 | py `node_output_invalid` → join2 信封 `py={status:failed,error_code}` 无 value；agent succeeded；run **succeeded**；end 把 failed 当数据、无 py value；py 无 data 行 |
| py required + 输出 schema 不匹配 | fail-fast：run **failed** `node_output_invalid`、无 final output；py `failed`；join2/join1/approve2/summarise/end **skipped**、无 join data 行、无 approve2 行、无 resume job；runsc 无孤儿 |
| agent optional + 撤销 golden model profile | agent `model_profile_unavailable` 不被 optional 吸收：run **failed**、join2/join1/approve2/… skipped、无 success/output；证明“越权/能力失败不降级” |

### 2.5 等待交接的 skip 语义（集成对齐，不重复修复）

- 旧集成基线 `15db973` 上，T085 的 `graphScheduler.stopAndCommit` 对 **waiting** 交接也会 `skipUnsettled()`：waiting 尝试把未来节点逐个 emit `skipped`，resume 后又 emit `pending/running`，既污染 node.status/run view，也让 `test_t087_approval_node.py::test_two_approvals_wait_twice_and_converge_on_one_run` 在旧基线单独复现时变红。
- 集成分支后续的 T085 `9a302f2` 已修复该语义（skip 在分支确定未选中时**记录**，每个 attempt 只按拓扑序发布一次：成功 attempt 在 end 前、failed/cancelled 终态前；waiting 交接不发布任何 skip）。本票合并 `bd4d791` 后**采用上游实现**，不重复修改 `graphrun.go`，只保留一个新增回归：
  `TestGraphRunWaitingAttemptDoesNotReportFutureNodesAsSkipped`（waiting 尝试中 approve 之后的 python/end 无任何 node.status；approved 重放同图 stats/end 各 pending,running,succeeded、sandbox 恰 1 次）。
- 合并后 T087 两审批真实栈用例、T089 的 skip 计数用例与 T091 的三 exec 流程在同一棵树上全部通过（见 §4 `pytest tests/f04b` 89 passed）。

### 2.6 run view：审批等待/决定可见（P1）

- `apps/web/src/assets/runview.js`：把 approval 节点纳入 data artifact 预取；新增 `runViewDescribeApproval(row, runState)`：
  - 已决定行渲染服务端 decision artifact：`审批已通过 · <approval_id 前 12> · <decided_at>`；
  - run 处于 `waiting_approval` 且该审批节点已有 pending/running 事件时，行内标注「等待人工审批（审批通过后由审批人恢复同一运行）」，不再只显示“运行中”；
  - `sourceText` 说明审批决定的数据来源（node data artifact，非契约面）。
- `tests/f04b/runview_t091.test.mjs`（4 用例，**PASS 4**）用真实 `runview.js` 字节 + 十类 fixture 钉住等待/已决定/嵌套 join 逐源/拓扑行。审批页本体（T087 `approvals.js`）已能显示服务端封存 proposal，本票未改。
- 浏览器 DOM 交互 not_run（沿用 T083 页面测试边界）。

## 3. 环境与版本

| 项 | 值 |
|---|---|
| Go | `go1.27.3 windows/amd64`；`GOCACHE=E:\orchara-toolchains\gocache`，`GOMODCACHE=E:\orchara-toolchains\gomodcache` |
| PostgreSQL | 16，隔离集群 `127.0.0.1:55432`（`tests/support/stack.py` 每用例新库；真实迁移+bootstrap） |
| Python | 3.12.2；pytest 9.0.3 |
| Node | v24.13.0；canvas `npm ci --offline` + `npm run build`（esbuild，产物逐字节一致） |
| Sandbox | WSL2 Ubuntu-24.04 + runsc；真实 runsc units，孤儿检查 `runsc list == []` |
| 模型 | 仅 `ORCHARA_PROFILE=dev-stub`（确定性 stub，receipt `stub:true`），无真实厂商凭据 |
| 输入/版本 hash | manifest `orchara-execution-manifest/1.0`、`compiler_version=orchara-compiler/1.0.0`；debug 输入 `{"requires_approval":true,"task":"Summarize the regional sales"}`；CSV `sha256=f226b1fef2d09026714dfbc29a01f1439d1353ba0bfb4a98fd3531db1fc612da`；`X-Manifest-SHA256` 与 run `manifest_sha256` 一致（fixture/验收图分别断言 64 hex） |

## 4. 证据：命令与真实原始输出

工作目录 `E:\Orchara-wt\t091\product`；全局 pytest 锁（`E:\Orchara\.scratch\t083-t091\pytest.lock`）串行执行，结束后释放；原始日志 `E:\Orchara\.scratch\t083-t091\T091\`（`pytest-f04b.log`、`pytest-f06.log`、`pytest-tooling.log`、`go-*.log`、`node-*.log`、`gates-exit.txt`）。

```text
$ python -m pytest tests/f04b -q
.................                                                        [100%]
89 passed in 678.89s (0:11:18)
exit=0

$ python -m pytest tests/f06 -q
.............................                                            [100%]
173 passed in 1311.21s (0:21:51)
exit=0

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 6.49s
exit=0

$ go build ./...
exit=0
$ go vet ./...
exit=0
$ go test ./... -count=1
（38 个包全部 ok，无 FAIL；完整输出 .scratch/t083-t091/T091/go-test.log，末尾示例）
ok   orchara/platform/internal/workflow  1.565s
?    orchara/platform/probes/f10_pdf_compare/go_baseline  [no test files]
exit=0
$ gofmt -l internal cmd tools
（无输出）
exit=0
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
exit=0
$ node --check apps/web/src/assets/canvas.js
exit=0
$ node tests/f04b/canvas_f04b.test.mjs
PASS 9 canvas F04B module tests
exit=0
$ node tests/f04b/ten_node_canvas.test.mjs
PASS 12 canvas T091 ten-node tests
exit=0
$ node tests/f04b/runview.test.mjs
PASS 6 runview tests
exit=0
$ node tests/f04b/runview_t091.test.mjs
PASS 4 runview T091 tests
exit=0
```

`tests/f04b` 的 89 = 集成分支 75（T083 全回归、T084、T085、T086、T087、T088、T089、T090 相关）+ 本票新增 14（`test_t091_ten_node_workflow.py` 8、`test_t091_canvas.py` 4（1 skip：本 worktree 已执行 npm ci，故重建用例实际执行）、`test_t091_runview.py` 2）。其中真实栈新增用例 8 个：十类 fixture 保存/重载/validate+冻结 manifest、非法变体定位+debug 拒绝、两次审批嵌套汇合、optional 收敛、required fail-fast、可选能力失败不降级、冻结 snapshot 执行。

专项复跑（合并 `bd4d791` 后、最终树）：`test_t087_approval_node.py::test_two_approvals_wait_twice_and_converge_on_one_run` 与 T091 全文件 → **8 passed**；`go test ./internal/presetexec -count=1` → ok。

## 5. stub / real 分界

- **real（生产验收）**：PostgreSQL 16 + 真实迁移/bootstrap；编译后的 `cmd/api`（真实 mTLS internal listener、对象目录、HMAC 签名链接）、`cmd/supervisor exec --once`、`cmd/maintenance`；真实 debug 提交/冻结 manifest/`execution_snapshots`；真实 runsc 执行（并发分支）；真实 worker mTLS node-approval 路由、契约审批决定、唯一 resume job；真实 capability proxy + usage ledger；owner-only node-results/SSE/artifact 读取；真实知识库行与真实 published child version。
- **stub / seam（分报，不冒充生产）**：模型 provider 为文档化 dev-stub（确定性 digest，`stub:true`）；Go 单测的 fake client/sandbox/inputs 只验证调度器语义；画布/run view 用 Node 执行已提交真实字节（无浏览器）。
- **not_run**：
  1. 浏览器 DOM/Playwright 交互（仓库测试栈无浏览器驱动；面板逻辑由 Node 真实字节 + bundle marker 验收）；
  2. 真实厂商模型/嵌入（无凭据；本票 UI/运行时只用 dev-stub）；
  3. 跨版本 checkpoint 恢复（本批明确不做；本票断言恢复使用冻结 manifest 而非新草稿）；
  4. `pytest tests/f05`/`tests/f08` 等无关全量套件（本票未改其代码路径；受影响面由 f04b/f06/tooling/go 门禁覆盖）。

## 6. 变更文件

新增：
- `product/tests/f04b/fixtures/ten_node_definition.json`、`ten_node_layout.json`
- `product/tests/f04b/ten_node_canvas.test.mjs`、`test_t091_canvas.py`
- `product/tests/f04b/runview_t091.test.mjs`、`test_t091_runview.py`
- `product/tests/f04b/test_t091_ten_node_workflow.py`
- `product/tests/support/t091.py`
- `product/reports/f04b/T091-ten-node-workflow.md`（本文件）

修改：
- `product/apps/web/src/assets/runview.js`（approval 行等待/决定显示、approval data artifact 预取、sourceText）
- `product/internal/presetexec/approval_test.go`（新增 waiting 不 skip 的 Go 回归 `TestGraphRunWaitingAttemptDoesNotReportFutureNodesAsSkipped`；其它 `graphrun.go` 的 skip 语义来自集成分支 T085 `9a302f2`，本票不重复修改）
- `product/PROGRESS.md`（小节）

未改：`contracts/**`、`docs/spec/**`、`db/migrations/**`、`apps/web/canvas/src/**`（十类已由上游票合并；`npm ci --offline && npm run build` 重建产物与已提交 `canvas.js`/`canvas.css` 逐字节一致，无产物变化）、`internal/presetexec/graphrun.go`（采用集成分支 T085 的 skip 政策）。

## 7. 合并 / HEAD / 门禁

- 分支 `t/t091-ten-node-graph`；worktree `E:\Orchara-wt\t091`。
- 开发提交：`dd5bb0e`（十类 fixture/画布测试 + runview 审批显示）、`664170f`（真实栈验收套件 + Go wait-skip 回归）。
- 结束前 `git merge refs/heads/integration/t083-t091`（`bd4d791`，含 T085 `9a302f2` 的 skipped-fact 政策、T087/T088/T089 报告与测试对齐）→ 合并提交 `c64fef0`，无冲突；合并后 `go test ./internal/presetexec` 绿、T087 两审批真实栈用例绿、T091 全套绿。
- 最终 HEAD：分支 tip（`git log -1 t/t091-ten-node-graph`；本报告与 tip 同提交，父提交为合并 `c64fef0`，功能提交 `664170f`/`dd5bb0e`）。
- 门禁（串行、全局 pytest 锁、最终合并树）：见 §4，全部 `exit=0`；`tests/f04b` 89 passed、`tests/f06` 173 passed、`tests/tooling` 21 passed、Go 38 包全绿、gofmt 空、sync_spec no drift。
- 风险：`regionProblems`/修复提示是服务端阶段 2–4 的本地镜像（T083 既有取舍，服务端仍是权威）；run view 依赖非契约 node data artifact 约定（T083/T087/T088）；waiting 交接的 skip 语义依赖集成分支 T085 实现，本票不重复修改。
