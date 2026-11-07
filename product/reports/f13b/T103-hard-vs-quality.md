# T103（GitHub #104）同一录制结果：hard checker vs 质量评分

状态：**passed（真栈证据）；获批 judge 运行 blocked / not_run**。本票是 test/probe-only，
未改 `docs/spec/**`、`contracts/**`、迁移或 `internal/evaluation` 语义；产品代码零改动。

- 分支 / worktree：`t/t103-quality-vs-hard` / `E:\Orchara-wt\t103`（基于 `integration/f13b`）
- 环境：PostgreSQL 16.4 `127.0.0.1:55432`、Go 1.27.3、Python 3.12.2、真实编译二进制
  （`cmd/api`、`cmd/admin`、`cmd/maintenance`、`cmd/supervisor`）、WSL2 Ubuntu-24.04 + runsc
  release-20241118.0；模型画像只有 `ORCHARA_PROFILE=dev-stub`，**无获批 judge 端点/凭据**。
- 证据目录：`E:\Orchara\.scratch\f13b\T103\`（原始 tail）；提交的证据副本在
  `product/probes/f13b_quality/evidence/` 与 `results/`。

## 1. 结论（先读）

1. **同一录制结果**：真实 runner（真实 maintenance 双池 + 真实 supervisor 执行真实 WSL2/runsc
   sandbox）产出 sealed report `a42cc60e…`：两个 case，一个真 hard pass（断言 == 录制事实），
   一个真 hard fail（`json_equals` 期望 `{"total":"100.00"}`，录制输出实际为 `{}`）。
   产品 status = `failed`；独立探针从 `cases[].assertions` 复算 = `failed`，两者一致。
2. **高质量分不能覆盖硬失败**：把同一份 case 证据用真实 job-scoped 路径重新封印成
   `status=failed` + `quality={informational:true, adapter:"heuristic-local/1", score:1.0}`，
   **真实 completion guard 接受**（report 行写入、evaluation 成功），随后真实
   `POST /api/v1/applications/{id}/submit` **HTTP 422 `evaluation_not_passed`** 拒绝，
   `details.report_status="failed"`，0 条 review 被创建。
3. **反向对照**：passed 五类作者案例报告 + `quality.score=0.0` → completion guard 接受、
   `submit` **HTTP 202 submitted**、review 创建、`author_cases.satisfied=true`。质量分只是信息，
   不是门禁。
4. **适配器**：`heuristic-local/1`（本票自建确定性本地代理分，**不是 judge**）outbound=0、
   vendor_tokens=0、cost=$0；DeepEval/Ragas/Promptfoo 在隔离 venv/npm 前缀里真实安装并做了
   最小真实调用，因**无获批 judge 端点/凭据**而 `blocked`（原始错误 tail 在矩阵里），
   没有伪造任何 judge 运行。获批 judge 运行 = `not_run`（缺端点+凭据）。
5. **边界**：反向对照的五条 case run 是 T068 既有的“真实 evaluation 行 + seeded 终态”评审
   fixture（本票未改其性质，报告单独标注）；本票的 hard checker 证据与质量覆盖证据都不是它。

## 2. 逐 AC

| AC（issue #104） | 结论 | 证据 |
|---|---|---|
| 1. 同录制结果 hard fail 不被高分覆盖 | **passed** | 真录制报告 hard=failed；`quality.score=1.0` 的 failed 报告被真实 submit 以 422 `evaluation_not_passed` 拒绝；反向 `quality=0.0` 的 passed 报告被 202 接受 |
| 2. 出站观察与成本可查 | **passed（本地适配器）** | `heuristic-local/1` 运行期 trap 与 socket 计数均为 0 出站；vendor_tokens=0、cost=$0；真实 judge 成本 `not_run`；矩阵记录每个候选的真实安装/调用/出站观察 |
| 3. 无可接受适配器留淘汰证据而不伪造运行 | **passed（blocked 为主，无伪造）** | DeepEval/Ragas/Promptfoo 均真实安装成功但语义 judge 运行 `blocked`（缺获批端点/凭据），原始错误 tail 已留；没有 `eliminated` 被硬凑，也没有任何 judge 分数被编造 |
| 4. 通过端到端入口实际演示；失败/授权/重试边界有断言 | **passed** | 真实 runner+supervisor 产报告；真实 `POST /applications/{id}/submit`（而非内部函数）拒绝/允许；断言 0 review、details 稳定码、case run 真实执行 |
| 5. 版本/输入 hash/命令/原始结果/profile；stub/real 分报，缺条件 blocked、未运行 not_run | **passed** | 本文件与 `probes/f13b_quality/**` 记录 commit、manifest/dataset/sealed hash、命令、原始 tail、profile；stub/real 分节；blocked/not_run 清单见 §8 |

## 3. 演示的层次（不把静态探针写成端到端）

| 层 | 是什么 | 不是 |
|---|---|---|
| 真栈录制的 hard checker | 真实 maintenance 双池收集、真实 supervisor 执行、真实 completion guard 封印的 report；断言对真实 run state / sealed output 判定 | 不是手写 JSON 冒充运行；不是 mock 返回值 |
| 质量覆盖（quality=1.0 / 0.0） | 用真实 job-scoped artifact 上传 + 真实 claim/result 协议 + 真实 completion guard 封印；case run 由真实 supervisor 实际执行；断言 outcome 逐字复制自真实 runner 报告 | 不是直接 SQL 插入 report 行；不是跳过 guard |
| 反向对照的五类报告 | T068 既有评审 fixture：真实 evaluation/snapshot/report 行，case run 终态 seeded（不执行 sandbox） | **不是**本轮硬检查证据；仅用于证明“低质量分不阻断硬门禁” |
| 适配器探针 | 真实 pip/npm 安装 + 真实最小调用 + 回环代理 trap 计数 | 不是 judge 运行；没有真实 vendor token/费用 |

## 4. 真实录制结果（同一条记录）

命令（cwd=`product`，真实栈锁 `E:\Orchara\.scratch\f13b\locks\realstack` 已占用）：

```powershell
$env:ORCHARA_T103_EVIDENCE_DIR='E:\Orchara\.scratch\f13b\T103'
python -m pytest tests/f13b/test_t103_quality_gate.py::test_the_real_recorded_result_is_a_hard_failure_and_a_local_quality_one -q
```

| 项 | 值 |
|---|---|
| evaluation id | `f48d4d5a-43a1-4869-8650-4763d5742720` |
| report id（`platform.evaluation_reports`） | `37962747-157f-43f3-8261-0c43390dccb8` |
| result artifact id | `b516225f-1bcb-4af3-8815-fa727baf6584` |
| **sealed report sha256** | `a42cc60e4f2cf0abef6d1648b21eaa83830c2b6615c76c9ee0477ee3d20354d9` |
| manifest_sha256 | `27f5a398faa3fda47e4283f99c469a52ccb9d18b73d952fe451d7cef3c43bf3a` |
| dataset_sha256 | `3de21d559453ea8f637799b518dd67d4aa6d561344080159193b03915db42865` |
| snapshot id | `8a124a42-bef7-402b-99b0-ebb923cbbb73` |
| evaluator | `orchara-platform-runner/1`（`synthetic=false`） |
| product status | `failed`（job `state=succeeded`，业务失败≠基础设施失败） |
| quality slot（构建产物） | `{informational:true, adapter:"not_installed", score:null}` — 无 judge 就不编分 |
| 独立复算 hard verdict | **failed**（`compare.py`，`quality_used=false`、`agrees_with_declared_status=true`） |
| 本地质量代理分 | **1.0**（`heuristic-local/1`，证据完整度/可解释性，不判正确性） |
| 出站 / 成本 | 0 连接 / vendor_tokens=0 / $0（本地 CPU） |

两条 case 的真实断言（sealed report 原文）：

| case | run state | 断言 | 结果 |
|---|---|---|---|
| `t103-hard-pass` | `succeeded` | `run_state: succeeded`；`json_equals("", {})` | **passed**：两条断言都命中真实录制事实 |
| `t103-hard-fail` | `succeeded` | `run_state: succeeded`；`json_equals("", {"total":"100.00"})` | **failed**：输出实际是 `{}`，硬值不匹配 |

- 两个 run 都是真实 `platform.runs` 行（分别属于该 evaluation 的 case run），owner/snapshot/manifest
  均对得上；输出 artifact 的 sha256 = `sha256("{}")`。
- 独立复算不读产品 `status`、不读 `quality`：逐条断言按 `kind/expected/actual` 重算，
  case 只有在“≥1 条断言、全过、run 已完成”时才 passed；之后才把复算结果与产品声明的
  `status` 对比（一致才 `agrees_with_declared_status=true`）。
- `compare.py` 输出：`probes/f13b_quality/results/compare-real-recorded.json`；
  sealed 证据：`probes/f13b_quality/evidence/t103-real-recorded-report.json`（sha 与上表一致）。

### 4.1 版本、输入 hash 与环境

| 项 | 值 |
|---|---|
| 分支 / worktree | `t/t103-quality-vs-hard` / `E:\Orchara-wt\t103` |
| 基线 | `integration/f13b` @ `30642be`；收尾 merge 进来的集成分支 tip = T102 合并提交 `ffe4022` |
| Go / Python / Node | go1.27.3（windows/amd64）/ Python 3.12.2 / Node v24.13.0（npm 11.13.0） |
| 数据库 / 沙箱 | PostgreSQL 16.4 `127.0.0.1:55432` / WSL2 Ubuntu-24.04 + runsc release-20241118.0 |
| 模型画像 | `ORCHARA_PROFILE=dev-stub`（无真实厂商端点/凭据） |
| evaluator | `orchara-platform-runner/1`（sealed report 声明，`synthetic=false`） |
| 适配器版本 | DeepEval 4.3.3（`pip show deepeval`）；Ragas 0.4.3（`pip show ragas`）；Promptfoo 0.125.1（`package.json`）；`heuristic-local/1` 本仓代码 |
| 输入 hash（real report） | manifest `27f5a398…`、dataset `3de21d55…`、sealed report `a42cc60e…`（全文 sha256 见 §4 表） |
| 命令 | 见 §4 / §5 / §9 与 `probes/f13b_quality/README.md` |


## 5. 高质量分不能覆盖硬失败（真实发布门禁）

同一条 case 证据被重新封印为 failed 报告，并写入 `quality.score=1.0`。封印走真实
job-scoped 路径（`upload → PUT signed link → complete → artifact_finalize → ready`），
case run 由真实 supervisor 执行（输出 `{}` 真实 ready），断言 outcome 逐字复制自真实
runner 报告，最后提交给真实 completion guard。

| 项 | 值 |
|---|---|
| evaluation id | `e62f5bee-4c87-4a2b-b6be-b501b2daae68` |
| report id | `f13511bc-f97e-4fcb-b8d3-f2442372107a` |
| sealed report sha256 | `06c72b8f329dc460cf4bce25064ef6faf595a40e51ff6c326d67710ffc5be1ed` |
| quality | `{informational:true, adapter:"heuristic-local/1", score:1.0}` |
| completion guard | **接受**：`result.state=succeeded`，report 行 `status=failed` |
| 独立复算 | `failed`，与产品声明一致，`quality_used=false` |
| case runs（真实执行） | `t103-hard-pass`=`7841baea-f968-42f1-a375-cd3dc319a258`，`t103-hard-fail`=`bec8c169-7eb9-453c-9a03-d61452aa283f` |

真实 `POST /api/v1/applications/{application_id}/submit` 的原始响应（`ORCHARA_T103_EVIDENCE_DIR`
导出 `t103-gate-refusal.json`，整个 body 原样保留）：

```json
{"error":{"code":"evaluation_not_passed","message":"the cited evaluation report is not passed; a publication review can only cite a passed report","request_id":"4250107486f4a1ae1709dd1cdc5f5ee2","details":{"report_status":"failed"}}}
```

- **HTTP 422**，稳定码 `evaluation_not_passed`，`details.report_status="failed"`；
  `platform.publication_reviews` 中该 application 的 review 数 = **0**。
- 该拒绝发生在质量分被平台读到的任何地方之前：门禁只读 report 行 status 与 sealed
  document 的 status/cases；`quality` 字段既不参与 completion guard 的
  `ComputeStatus`/`ValidateShape`，也不参与 publication gate。

## 6. 反向对照（低质量分不阻断硬门禁）

| 项 | 值 |
|---|---|
| 报告 | T068 既有五类作者案例 passed 报告（normal×2 / bad_input / out_of_bounds / tool_failure / output_check） |
| evaluation id | `8e2067d0-a5d1-4fef-8fe0-8f70e4f4a369` |
| report id | `473e3744-98ae-4fe0-87cf-ac2ed49c4eb4` |
| quality | `{informational:true, adapter:"heuristic-local/1", score:0.0}` |
| completion guard | 接受（report status=`passed`） |
| submit 响应 | **HTTP 202** `{"id":"c06155c8-4019-4810-b00e-8755f4953863","state":"submitted"}` |
| review / 作者案例 | review 行创建；`author_cases.satisfied=true`，`missing=[]`，`case_count=5` |

即：同一门禁下，quality=1.0 的 failed 报告被拒；quality=0.0 的 passed 报告被放行。
质量槽只是信息。该对照的 case run 终态是 seeded 的 T068 fixture（见 §3），不是本轮
hard checker 的证据来源。

## 7. 适配器矩阵与出站/成本

原始矩阵：`probes/f13b_quality/results/adapter-matrix.json`（本表是它的摘要）；
探针：`probes/f13b_quality/adapter_probe.py`；原始 tail：`E:\Orchara\.scratch\f13b\T103\adapter-probe.log`。
判定口径：`accepted` 仅表示“在本机可运行且适合所标注的角色”；`blocked` 必须写明缺什么；
`eliminated` 必须有真实出站/数据策略证据；`not_run` 表示未执行、不声称任何结果。

| adapter | 版本 | 角色 | 真实安装 | 真实最小调用 | 出站（trap 观察） | tokens / 成本 | 判定 |
|---|---|---|---|---|---|---|---|
| `heuristic-local/1` | 本仓 probe（T103 自建） | 确定性本地代理分，**不是 judge** | in-process，无包 | `compare.py` 在真实 sealed report 上复算 hard + 质量分 | **0** | 0 / $0 | **accepted**（仅作本地代理分） |
| DeepEval | 4.3.3 | 首个通用语义质量适配器（候选） | 隔离 venv，`pip install deepeval` rc=0（160s） | 确定性 `ExactMatchMetric` 本地跑通（score=1.0，`requires_model=false`，0 出站）；`AnswerRelevancyMetric` 构造报 `DeepEvalError: OpenAI API key is not configured…` | **0** | 0 / $0（未发生 vendor 调用） | **blocked**（`judge_endpoint_and_credential`） |
| Ragas | 0.4.3 | RAG 专项（候选） | 隔离 venv，`pip install ragas` rc=0（388s） | `import ragas` 失败：`ModuleNotFoundError: No module named 'langchain_community.chat_models.vertexai'`（`ragas/llms/base.py:12`） | **0** | 0 / $0（未发生 vendor 调用） | **blocked**（`dependency_incompatibility`） |
| Promptfoo | 0.125.1 | 替代 harness（本身不是 judge） | npm 前缀，`npm install promptfoo` rc=0（613 包，256s） | 离线确定性 eval（echo + equals）通过 1 passed；但**默认 telemetry/update 路径产生 6 条真实出站代理请求**：`api.promptfoo.dev:443`、`a.promptfoo.app:443`、`r.promptfoo.app:443` | **6** | 0 / $0（无模型 provider 调用） | **blocked**（`semantic_provider_endpoint`）+ **数据外发策略：无 opt-out 时 eliminated** |

**`eliminated` 与 `blocked` 的分界（本票口径）**：

- DeepEval / Ragas 是 **`blocked`**：它们本身可以指向获批端点（DeepEval 支持自定义
  `OpenAIModel`/本地模型；Ragas 由调用方指定 judge/embedding），缺的是本机的获批
  endpoint/凭据与 Ragas 的兼容依赖，不是被证明“不可用”。
- Promptfoo 是 **数据外发策略上的 `eliminated_without_opt_out`**：一个完全不涉及模型
  provider 的离线确定性 eval，as-shipped 默认仍向 `*.promptfoo.dev`/`*.promptfoo.app`
  发了 6 条出站请求（安装包源码里存在 `PROMPTFOO_DISABLE_TELEMETRY` /
  `PROMPTFOO_DISABLE_UPDATE` 开关）。即使关掉遥测，它的语义判定仍需一个模型 provider，
  所以语义角色仍是 `blocked`。矩阵因此同时记录 `summary.blocked` 与
  `summary.policy_eliminated_without_opt_out`，不把两者混为一谈。
- **`not_run`**：获批 judge 的真实语义评分及其 vendor tokens/费用。没有任何 judge 分数、
  token 或账单被编造；矩阵中所有“0”都来自本地 CPU 运行与真实 trap 计数。

安装阶段的网络访问是 **包管理器 registry（PyPI/npm）** 流量，不携带任何评测正文；
所有探针调用阶段的出站观察以上表为准。`heuristic-local/1` 运行期在 `compare.py` 内还做了
`socket.connect/connect_ex/create_connection` 计数，同样为 0。


## 8. blocked / not_run 清单（诚实边界）

| 项 | 状态 | 缺什么 / 解除条件 |
|---|---|---|
| 获批 judge 的真实语义评分运行 | **blocked / not_run** | 无获批 judge 端点与凭据（只有 dev-stub 画像）。解除：提供获批 endpoint + credential 与数据外发批准，再按矩阵中记录的入口重跑 |
| 真实 judge 的 vendor tokens / 美元成本 | **not_run** | 依赖上一项；本票只报告本地适配器成本 = $0 |
| DeepEval 语义指标 | **blocked** | 安装成功，但语义指标需要 judge 模型；本机无获批端点/凭据 |
| Ragas faithfulness/answer 质量 | **blocked** | 安装成功，但需要 judge + embedding 模型；本机无获批端点/凭据 |
| Promptfoo 语义断言 | **blocked** | 安装成功；它是 harness，语义判定仍需模型 provider；本机无可用的获批 provider |
| 反向对照的 case 真实执行 | **seeded（T068 fixture）** | 不是本票要证明的点；本票的 hard checker 与质量覆盖证据均为真实执行 |

## 9. 门禁命令与原始 tail

完整原始输出在 `E:\Orchara\.scratch\f13b\T103\`：`go-gates.log`、`gate-f13b.log`、
`gate-f13a.log`、`gate-f13a-baseline-3tests.log`、`adapter-probe.log`。

| 命令（cwd=`product`） | 退出码 | 原始 tail |
|---|---|---|
| `python -m pytest tests/f13b/test_t103_quality_gate.py -q` | 0 | `4 passed in 51.10s`（不含 `ORCHARA_T103_EVIDENCE_DIR`，以保留 §4/§5 对应的已提交 sealed 证据字节） |
| `python -m pytest tests/f13b -q`（导出证据的那次） | 0 | `4 passed in 42.76s`（`ORCHARA_T103_EVIDENCE_DIR=E:\Orchara\.scratch\f13b\T103`） |
| `python -m pytest tests/f13a -q` | 1 | `3 failed, 48 passed in 602.30s (0:10:02)` — **基线既有红，非本票引入**，见下 |
| `go build ./...` / `go vet ./...` / `go test ./...` | 0 / 0 / 0 | 全部包 `ok`；`no test files` 只出现在 `cmd/*` 与 `probes/*/go_baseline` |
| `gofmt -l internal cmd tools` | 0 | 无输出（0 行） |
| `python tools/sync_spec.py --check` | 0 | `no drift: spec copy and contract snapshot match the manifest` |

`tests/f13a` 的 3 个失败是**集成分支基线既有**，与本票的 test/probe-only 改动无关：

```text
FAILED tests/f13a/test_rv1_effect_evidence.py::test_effect_count_reads_confirmed_effects_from_the_real_ledger
FAILED tests/f13a/test_t052_runner.py::test_a_real_run_is_traced_and_never_claimed_successful
FAILED tests/f13a/test_t062_runner.py::test_a_passed_report_is_accepted_over_a_real_completed_run
3 failed, 48 passed in 602.30s
```

- 根因一致：这三个用例仍断言 `start->end` 最小链执行会以 `tool_unsupported_graph` **失败**；
  本基线实际执行**成功**并封印输出 `{}`（本票的真录制结果因此改用 successful run + `json_equals`）。
- 复现方式：把本票唯一的 tracked 改动（`tests/support/t068.py`）stash 掉（其余 tracked 文件
  与基线 `30642be` 完全一致），再跑这 3 条用例 → **同样 3 failed in 98.65s**
  （`gate-f13a-baseline-3tests.log`）。这 3 条用例都不 import `t068`，且 `tests/f13a/` 无任何
  T103 diff。故判定：**基线红，非本票引入**；修复它属于既有 F13A 用例与当前执行器行为对齐的
  另一个工作项，不在 T103 切片内。
- 该基线红不影响本票结论：T103 的真栈证据跑在真实 runner/supervisor/guard 上并全部通过。

## 10. 残留风险

1. **质量分的语义**：`heuristic-local/1` 度量的是“证据完整度/可解释性”，不是答案正确性。
   它的 1.0 与硬失败并存正是要证明的“质量分不能替代硬检查”；但它**不能**被当作
   “语义质量已测量”的证据。真实 judge 仍是 `blocked`。
2. **反向对照的执行层**：五类 passed 报告用 T068 既有 seeded-terminal-run fixture；
   该 fixture 在本批次既有报告里已标注，本票未改变其性质。
3. **质量覆盖的封印路径**：报告由测试重放真实 runner 的 job 协议（claim/upload/result）
   封印，不是产品 runner 自己写的 `quality` 槽。产品 runner 在当前构建里只写
   `not_installed/null`（如实）。要生产化质量评分，需要新的产品改动与获批 judge。
4. **环境相关**：真实栈依赖 WSL2/runsc 与 55432 集群；真实执行在本机通过，换宿主
   需重跑（`tests/f13b` 的 fixture 会在集群缺失时 skip，而不是把 skip 当通过）。
5. **hash 复现**：sealed report 的 hash 绑定 evaluation/snapshot/run id 与时间戳，换一次
   运行就会变化；本报告记录的是本次运行的具体 hash，`compare.py` 的复算不依赖固定 hash。
