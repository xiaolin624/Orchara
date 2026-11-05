# F14 T090 验收：冻结动态候选路由的基线（dev-stub 真实控制面）

- 任务：F14 / GitHub Issue #91（票 `T090`），feature 分支 `t/t090-routing-baseline`，
  worktree `E:\Orchara-wt\t090`（产品代码 `product/`），基线 `integration/t083-t091`
  @ `0deeab9` → 结束前 merge `491cb78`（合并提交 `1d94984`）；代码/证据提交 `4c10b22`，
  证据运行 head `0deeab9`。
- 状态：**`blocked`**。不依赖模型的全部必交项已真实执行并留证；**同基模强提示基线、
  规则 vs 强提示比较、任何训练都不可在本机运行**（无模型凭据/端点/权重、无训练 GPU）。
  本报告不声称训练已运行，也不把 dev-stub 冒充强提示基线。
- 证据运行环境：真实 PostgreSQL 16.4 隔离集群（`tests/support/stack.py`，`127.0.0.1:55432`）
  + 真实编译 `cmd/api` / `cmd/maintenance`；**无 sandbox 执行**（本票只测路由决策）；
  Python 3.12.2、Go 1.27.3、Windows 11；git head `0deeab9`。
- 权威机器可读结果：`reports/f14/T090-routing-baseline.json`
  （逐例 expected/actual、两臂 summary、catalog 版本/manifest/card hash、环境）。
- 冻结清单：`experiments/routing_sft/freeze/T090-freeze.json`（+
  `T090-freeze.md`），sha256 `96911815fd550c96c2fd36343832bbd5f6885cdc94551b27f5756fa463cea79d`。
- 控制模型：真实注册的 dev-stub（`provider=stub`、`stub:true`、`validation_state=passed`、
  `route_control=true`、`data_destination=local-only`）。服务端拥有 ACL、必填输入与决策策略。

## 0. 一句话结论

在不依赖模型的范围内，T090 交付了冻结清单、留出/泄漏检查与**两臂真实控制面基线**：
臂 A（复现 T084 的目录）34/34 决策全对、误自动执行 0、澄清完成 13/13、候选召回 1.0，
与 T084 证据逐字段一致；臂 B（仅把 sales 卡换成**产品真实模板卡**，语料/其余卡不变）
出现 **5 个可归因错误，全部在 retrieval 层**（4 例未见应用被误召回、1 例应召回未召回），
仍无 false auto、隐藏版本零泄漏。因此结论是 **provisional stop：先修检索/卡/分词，
不训练**；同基模强提示臂与训练仍 `blocked`，本票整体 `blocked`。

## 1. 交付内容

### 1.1 冻结清单（不依赖模型）

`product/experiments/routing_sft/freeze/T090-freeze.json`：

- **语料**：直接引用 T084 `tests/f08/routing/datasets/`，不复制第二份语料。记录
  `corpus_version=1.0.0`、`registry_sha256=491e4f25…`、`dataset_digest=9128080a…`、
  每文件 sha256/size（7 个）、每次 split/单元格/family 计数、T084 证据锚
  （`reports/f04b/T084-routing-eval.json` 的 head `7c0e456` 与同摘要）。
- **held-out**：T084 `frozen_test` 25 例（18 families）为 held-out，`tuning_allowed=false`；
  `dev` 9 例（8 families）仅观察。25 例低于 `EVALUATION.md:35` 的 100 例起步建议，
  报告明写“未虚增规模”，不声称统计充分。
- **目录/候选卡**：冻结**两套**能力卡定义（同一批 34 例）——
  `frozen_t084_catalog`（T084 证据实际使用的卡：sales = `f02a.full_card()`，
  purpose `summarise sales`；`definitions_sha256=62a2e50e…`）与
  `product_template_card_probe`（仅 sales 换成 `internal/templates/templates.go::
  CapabilityCard` 的产品模板卡；`definitions_sha256=01295c2a…`）。逐卡 `card_sha256`
  用与平台相同的 RFC 8785 规范化计算；六个 fixture 卡两套相同。
  版本 UUID/`manifest_sha256` 由真实编译器在一次性 PG 集群生成，无法静态冻结，
  按运行记录在基线 JSON `catalog` / `card_variant_probe.catalog`。
- **检索冻结**：显式 `retrieval.not_used=true`（该语料只走“授权 SQL 候选 + 能力卡
  字面 token 匹配”，无 KB/embedding/chunker/parser/向量索引）。所有 revision 字段为
  `null`，不伪造；冻结的是授权卡定义 + 真实候选快照 `card_sha256`。
- **控制模型**：`provider=stub, stub=true, profile=dev-stub, validation_state=passed`，
  `semantic_baseline_allowed=false`，明写“非厂商模型，不能作为语义基线”。
- **环境与版本**：git head/branch、Python、Go、OS、profile；PostgreSQL 版本由运行时报告
  记录（本次为 16.4）。
- **重放命令**与报告路径。

### 1.2 留出与泄漏检查器（独立断言）

`product/tests/f14/routing_freeze.py` + `test_t090_freeze.py`（12 个纯 Python 断言）：

- 逐个重算语料文件 sha256/size 与 `dataset_digest`；语料或 registry 改一个字节即失败；
  并断言 T084 证据 JSON 的摘要与当前语料一致（T084 冻结的 25 例未被修改）。
- **同家族不跨 split/单元格**：独立构建 family→{split,cell,cases} 映射（不复用 T084
  `validate_corpus` 的实现），26 个 family、0 violation。
- **跨 split 重复**：9×25=225 对消息，完全重复 0、规范化（NFKC/casefold/去标点/空白折叠）
  重复 0；另按预注册阈值 token Jaccard ≥ **0.75** 判定近重复，实测最大值 **0.5**，
  近重复 0（阈值、225 对、最大值均写入 manifest）。
- **provenance/许可**：34 例全部 `origin=self_created`、`license_review=not_applicable`、
  `reference` 非空；唯一附件 `inputs/sales_sample.csv` 与仓库公开样例
  `docs/spec/examples/sales/input.csv` 逐字节相同，无真实用户数据、无第三方语料。
- **卡定义**：两套 `definitions_sha256` 与逐卡哈希重算一致；T084 卡等于 `f02a.full_card()`；
  产品模板卡每个字符串仍存在于 `templates.go`；sales purpose 与 T084 报告 catalog 一致。

### 1.3 两臂基线 harness（真实栈，非 mock）

`product/tests/f14/t090_baseline.py` + `test_t090_baseline.py`：

- 复用 T084 的 `tests/f08/routing/runner.py`（`PublishedApp`、`build_catalog`、
  `upload_sales_csv`、`run_case`、`aggregate`）与真实 `tests/support/t079.py`
  （`route_once` / `pending_interaction_row` / `reply` / `request_state` / `candidate_rows`）。
- 公开目录：6 个 fixture 应用用真实编译器 manifest 发布（T077 版本行），sales 用
  `t077.publish_version` 发布两份（臂 A 通用卡 / 臂 B 产品模板卡）；两个 principal
  分别只被授予各自臂可见的 6+1 个应用，`payroll` 始终 published-but-unreadable。
- 逐例记录：decision/state/error_code/候选（按 key + 版本 id + rank）/交互类型/run/
  owner/`false_auto_execution`/澄清完成/耗时；两臂各 34 例全部经真实
  `POST /api/v1/requests` + `cmd/maintenance --kinds route_request`。
- 指标由真实记录计算（`runner.aggregate` + T090 taxonomy），不手写。
- 输出：证据运行设 `T090_ROUTING_REPORT` 写仓库；普通 pytest 只写自己的 tmp 文件。

### 1.4 报告

- 本文件 + `reports/f14/T090-routing-baseline.json`
- `.scratch/t083-t091/T090-REPORT.md`（编排者摘要）、`product/PROGRESS.md` 小节

## 2. AC 对照

| AC | 状态 | 证据 |
|---|---|---|
| 1. 记录基线逐例错误 | **已交付（非模型部分）** | JSON `results`（臂 A 34 例）、`card_variant_probe.results`（臂 B 34 例）逐例 expected/actual；`errors.cases` 逐例归类；臂 B 5 个真实错误 |
| 2. 同家族/改写不泄漏 | **已交付** | `test_t090_freeze.py`：26 family 不跨 split/单元格；225 对跨 split 消息无完全/规范化重复；近重复阈值 0.75、实测最大 0.5；provenance/license 齐全 |
| 3. 无训练价值则保留停止结论、不声称训练已运行 | **已交付（provisional）** | `stop_go.decision=stop_training_fix_the_measured_layers_first`，证据为 5 个 retrieval 错误；`training.status=not_run`、`executed=false`；manifest/report 均无“训练已运行”声明 |
| 4. 通过正式入口实际演示；失败/授权/重试边界有断言 | **部分** | 真实 REST + `route_maintenance` 控制面跑了 68 例；无匹配/越权候选/隐藏版本零泄漏/零 Run 有逐例断言；**T068 真实发布未重跑**（T084 证据）；本票不涉及重试路径 |
| 5. 版本/输入 hash、命令、原始结果、环境 profile；stub/real 分报 | **已交付** | 两套卡定义哈希、逐应用 `manifest_sha256`/`card_sha256`、语料 sha、环境（PG 16.4/Python/Go/OS/head）、§7 原始输出；控制模型逐处标 `stub:true`，无真实模型声明 |
| 切片：比较规则/检索提示与**同基模强提示** | **blocked / not_run** | 本机无模型凭据/端点/权重，无法运行；缺项见 §6 |

## 3. 冻结与泄漏结果

### 3.1 语料

| split | cases | families | unique | ambiguous | missing_input | no_match | unseen_application | unauthorized_candidate |
|---|---|---|---|---|---|---|---|---|
| `frozen_test`（held-out） | 25 | 18 | 6 | 4 | 5 | 3 | 4 | 3 |
| `dev`（观察） | 9 | 8 | 2 | 1 | 3 | 1 | 1 | 1 |

`registry_sha256=491e4f25b6f8fbf91a8d345d1c260e2f576411978058cb3ab02e21351e39e42e`，
`dataset_digest=9128080ae557062f6ff5c0e2c96d999bad0e5bb8e52a0061cf554fb0e13071d2`；
与 T084 报告逐字段一致 ⇒ T084 冻结的 25 例 + dev 9 例未被 T090 修改。

### 3.2 泄漏

- 完全重复 / 规范化重复：**0 / 0**（225 对跨 split 组合）。
- 近重复（token Jaccard ≥ 0.75）：**0 对**；实测最大 `max_cross_split_similarity=0.5`
  （`rt-unauthorized-dev-01` vs `rt-unique-frozen-05`，二者只是共享 `csv/sales` 等词）。
  阈值与最大值均冻结在 manifest，`manual_review.required=false`。
- 家族隔离：26 family，0 violation；同一 family 只在一个 split 且只在一个单元格。
- 许可：34/34 `self_created` + `not_applicable`；附件为仓库公开样例的逐字节副本。

### 3.3 两套目录卡

| 臂 | sales 卡 | card_sha256 | definitions_sha256 |
|---|---|---|---|
| A `frozen_t084_catalog` | T084 实际卡（`f02a.full_card`，purpose `summarise sales`） | `8c1cdba7987d87f249b9436cba5b3f4af6e97bfce579fe9dbea586c4297c41c8` | `62a2e50e…` |
| B `product_template_card_probe` | 产品模板卡（`templates.CapabilityCard`） | `7f337a8ae7a37a8d1d7974366d7eba5e5415111b361427402756f16bef1e6e19` | `01295c2a…` |

**重要发现（诚实记录，不改 T084 语料）**：T084 证据里的 “sales” 应用并非产品销售模板卡，
而是测试 helper `t068.sales_application` 保存的通用卡（purpose `summarise sales`）；
T084 报告 catalog 也记录了这个 purpose。T090 因此同时冻结 T084 目录与产品真实模板卡，
并用同一批 34 例测出卡内容差异带来的真实影响（§4.2）。

## 4. 基线结果（`reports/f14/T090-routing-baseline.json`）

### 4.1 臂 A：复现 T084 目录（primary）

| split | cases | families | decision accuracy | 误自动执行 | 澄清完成 | 候选召回 | 零候选泄漏 | 创建 Run |
|---|---|---|---|---|---|---|---|---|
| `frozen_test` | 25 | 18 | **1.0** | 0 / 19（0.0） | 9 / 9（1.0） | 1.0 | 是 | 6 |
| `dev` | 9 | 8 | **1.0** | 0 / 7（0.0） | 4 / 4（1.0） | 1.0 | 是 | 2 |

逐单元格（cases / decision_matches / false_auto / clarification required→completed）：

| cell | frozen | dev |
|---|---|---|
| unique | 6/6/0/0→0 | 2/2/0/0→0 |
| ambiguous | 4/4/0/4→4 | 1/1/0/1→1 |
| missing_input | 5/5/0/5→5 | 3/3/0/3→3 |
| no_match | 3/3/0/0→0 | 1/1/0/0→0 |
| unseen_application | 4/4/0/0→0 | 1/1/0/0→0 |
| unauthorized_candidate | 3/3/0/0→0 | 1/1/0/0→0 |

与 T084 证据逐字段比较（`t084_comparison.identical=true`）：cases / decision_accuracy /
false_auto / clarification / candidate_recall 全部相同。逐例 `T090CASE` 记录在 §7 原始输出
与 JSON `results`。所有 auto 案例的 Run owner 均为第二用户、版本为期望版本；
`payroll` 版本 id 从未出现在任何候选快照（逐例断言）。

### 4.2 臂 B：产品模板卡探针（supplementary，不是 T084 基线）

| split | cases | decision accuracy | 误自动执行 | 澄清完成 | 候选召回 | 零候选泄漏（此处=过度召回） | 创建 Run |
|---|---|---|---|---|---|---|---|
| `frozen_test` | 25 | **0.84**（21/25） | 0 / 19（0.0） | 12 / 12（1.0） | 0.944444 | `rt-unseen-frozen-01/02/04` | 6 |
| `dev` | 9 | **0.888889**（8/9） | 0 / 7（0.0） | 5 / 5（1.0） | 1.0 | `rt-unseen-dev-01` | 2 |

逐单元格（cases / decision_matches / false_auto / clarification）：

| cell | frozen | dev |
|---|---|---|
| unique | 6/6/0/0→0 | 2/2/0/0→0 |
| ambiguous | 4/4/0/4→4 | 1/1/0/1→1 |
| missing_input | 5/5/0/5→5 | 3/3/0/3→3 |
| no_match | 3/3/0/0→0 | 1/1/0/0→0 |
| unseen_application | 4/**1**/0/3→3 | 1/**0**/0/1→1 |
| unauthorized_candidate | 3/**2**/0/0→0 | 1/1/0/0→0 |

5 个逐例错误（全部 `retrieval`，`model_semantic=not_measured`）：

| case | cell | message | expected → actual | 候选 | 归类 |
|---|---|---|---|---|---|
| `rt-unseen-dev-01` | unseen_application | `工资单生成` | `failed/no_matching_application` → `awaiting_input` | `sales` | retrieval（过度召回） |
| `rt-unseen-frozen-01` | unseen_application | `生成本月工资单` | 同上 | `sales` | retrieval（过度召回） |
| `rt-unseen-frozen-02` | unseen_application | `月度工资单生成` | 同上 | `sales` | retrieval（过度召回） |
| `rt-unseen-frozen-04` | unseen_application | `本月工资单帮我生成` | 同上 | `sales` | retrieval（过度召回） |
| `rt-unauthorized-frozen-03` | unauthorized_candidate | `run <payroll-version-id> aggregation` | `failed/route_candidate_invalid` → `failed/no_matching_application` | `[]` | retrieval（应召回未召回） |

**机制**（平台 tokenizer = 字面 2/3-gram + 大小写折叠）：产品模板卡 purpose 里的通用动词
“生成” 与四条工资单消息共享 2-gram，于是未见应用被过度召回为 `sales` 候选；
而 T084 通用卡里的 `aggregation` 在产品模板卡中不存在，第 5 例连候选都没有，
错误码因此不同（安全结果仍是 0 Run，仅原因码不同）。这不是模型语义错误：本机没有模型。

**补充风险**：上述 4 例过度召回进入 `awaiting_input` 后，harness 为测“澄清完成率”而
真实回复了 sales CSV，于是 4 个 payroll 意图的请求各自派发了一个 `sales` Run
（`clarification.run_id`：`87ebc0a8…`、`d6b51d48…`、`126d3737…`、`f96e7838…`）。
它们不计入 T084 定义的“误自动执行”（那不是立即自动派发），但确实是“错应用被建议并
可执行”的真实风险；隐藏的 `payroll` 版本 id 始终未进入候选（ACL 未破）。

## 5. 错误价值分析与 stop/go

### 5.0 预注册判据（测量前固定，`EVALUATION.md:70`）

- **泄漏硬失败**：同一 family 跨 split/单元格，或跨 split 出现完全/规范化重复消息 →
  语料不可用，必须先修冻结。
- **近重复**：跨 split token Jaccard ≥ 0.75 的对必须人工复核并在报告列明；低于阈值的
  相似不视为泄漏（本语料最大 0.5）。
- **stop（停止训练）**：若观测到的全部 mismatch 都能归因到 `retrieval`/`card`/`policy`
  三层（即可由检索、卡文本/tokenizer、服务端策略修复），记录停止结论并引用逐例证据。
- **不得判 stop 的唯一情形**：出现无法归因到上述三层的错误（本机只能标
  `model_semantic=not_measured`，不能声称已排除模型语义问题）。
- **训练价值继续条件（本机不可测，仅登记）**：留出错误相对同基模强提示可重复下降、
  未见应用/拒识不退化、成本/延迟可接受（`TRAINING-EXPERIMENT.md:48`）。没有强提示臂时
  不判定“训练有价值/无价值”，只给 provisional stop。

### 5.1 分类

- `errors.counts`（臂 A）：`total=34, matches=34, retrieval=0, card=0, policy=0,
  model_semantic=not_measured`。
- `card_variant_probe.errors.counts`：`total=34, matches=29, retrieval=5, card=0,
  policy=0, model_semantic=not_measured`。
- 分类覆盖：所有 mismatch 都能归到 retrieval/card/policy 三层之一；`model_semantic`
  从未被赋值（本机无模型，不能把错误归因给模型语义）。

**结论（provisional）：停止训练，先修可测层。**

- `stop_go.decision = stop_training_fix_the_measured_layers_first`，
  `provisional=true`，`basis.mismatches=5`，其中 retrieval=5、card=0、policy=0。
- 依据：
  - `TRAINING-EXPERIMENT.md:40` — 只有错误能被训练目标解释才生成训练数据；观测到的
    错误是候选召回/卡内容问题，微调不替代检索与卡。
  - `ROUTING-MEMORY-KNOWLEDGE.md:49` — 能力卡无法区分真实需求时，先改卡与澄清交互，
    不立即用微调掩盖产品定义不清。
  - `TRAINING-EXPERIMENT.md:48` — 无同基模强提示对照时不得声称提升；本票的停止结论
    因此只是**可测部分的 provisional 结论**，不是完整的训练价值判定。
- 臂 A 的 1.0 不是“不需要模型”的证据：该语料是 T084 按“消息与目标卡共享字面 token”构造的
  饱和语料，1.0 只说明 ACL/card/tokenizer/policy 管线在该语料上没有错误。真实语义改写
  （同义不同词、复合需求、追问）没有被测，`model_semantic` 永远是 `not_measured`。
- 若要推翻该 stop：需要同基模端点/预算跑强提示臂；需要**不共享卡 token 的改写 held-out**
  （按 family 隔离、记录 provenance）测语义召回；需要 GPU + 固定 revision 的基模后
  才能谈 LoRA。缺项见 §6。

## 6. 缺项 / `blocked` / `not_run`（不用 0 冒充）

| 项 | 状态 | 原因 |
|---|---|---|
| 同基模**强提示基线**（same-base-model strong prompt） | **blocked / not_run** | 本机无任何已获批模型凭据（`.env.local` 只有 `ORCHARA_PG_PASSWORD`）、无 `ORCHARA_MODEL_ENDPOINT`/secret ref、无本地权重/Ollama/HF cache；dev-stub 不是模型，不能冒充 |
| 规则/检索提示 vs 同基模强提示 比较 | **blocked / not_run** | 依赖上一项；无预算/价格配置、无 tokenizer/上下文限制验证 |
| 任何训练（LoRA / 三 seed / adapter） | **blocked / not_run** | 无训练可用 GPU（RTX 3050 Laptop 4 GiB）、无基模权重与固定 revision；未生成训练数据、无 adapter；`TRAINING-EXPERIMENT.md:42/:44` 门禁未满足 |
| 100 例 held-out 规模 | **未交付** | 交付 T084 的 25 例 held-out；`EVALUATION.md:35` 明确 60/100 是起步建议、不是统计充分。T090 未虚增规模，也未新增更难的语义改写 held-out（需另行设计与重跑，见 `what_would_change_it`） |
| T068 真实发布重跑 | **not_run（有意）** | 路由基线只依赖不可变版本的授权卡内容；T084 JSON 是 T068 real 发布证据。两臂均用 T077 发布路径，报告 §4 明示，不冒充第二次 T068 |
| 浏览器 DOM / Playwright | **not_used** | 本票是实验报告类，不涉及页面 |
| sandbox 执行 | **not_used** | 只测路由决策，不执行 Run |
| 全量 `pytest tests/f08` | 见 §7 门禁 | 本票未改 `product/` 生产代码、未改 T084 语料/runner；T084 自身最终树门禁也是 `not_run`（其报告修正）；跑 T084 语料/状态子集 + 冻结哈希锚 |
| 向量检索 revision 冻结 | **not_applicable** | 语料不涉及 KB/embedding，`retrieval.not_used=true` |

## 7. 实际命令与原始输出（尾部）

### 7.1 冻结/泄漏

```text
$ cd E:\Orchara-wt\t090\product
$ python -m pytest tests/f14/test_t090_freeze.py -q
............                                                             [100%]
12 passed in 0.20s
```

### 7.2 证据运行（两臂，真实 PG+API+maintenance）

```text
$ $env:T090_ROUTING_REPORT='E:\Orchara-wt\t090\product\reports\f14\T090-routing-baseline.json'
$ python -m pytest tests/f14/test_t090_baseline.py -q -s
T090CASE {"arm": "FROZEN_T084", ... }   # 68 行 = 34 例 × 2 臂（全量在 .scratch 原始日志）
...
T090: routing baseline report written to E:\Orchara-wt\t090\product\reports\f14\T090-routing-baseline.json
T090: primary_taxonomy={"total": 34, "matches": 34, "retrieval": 0, "card": 0, "policy": 0, "model_semantic": "not_measured"}
T090: probe_taxonomy={"total": 34, "matches": 29, "retrieval": 5, "card": 0, "policy": 0, "model_semantic": "not_measured"}
T090: t084_comparison_identical=true
T090: stop_go="stop_training_fix_the_measured_layers_first"
1 passed in 73.92s (0:01:13)
```

环境（来自报告 `environment`）：`PostgreSQL 16.4`、Python 3.12.2、Go 1.27.3、
Windows 11 (AMD64)、git head `0deeab9`；控制模型 id/`validation_state=passed` 见 JSON
`control_model`。

### 7.3 门禁

```text
$ cd E:\Orchara-wt\t090\product
$ python -m pytest tests/f14 -q
.............                                                            [100%]
13 passed in 73.36s (0:01:13)

# merge integration/t083-t091（b343f7b）→ merge commit 9c062ec 后复跑
$ python -m pytest tests/f14 -q
.............                                                            [100%]
13 passed in 76.66s (0:01:16)

$ python -m pytest tests/f08/test_t084_corpus.py -q
....                                                                     [100%]
4 passed in 0.20s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

- `pytest tests/f14 -q` = 12 个纯 Python 冻结/泄漏断言 + 1 个真实栈基线用例
  （门禁运行未设 `T090_ROUTING_REPORT`，只写 pytest tmp，不改仓库证据 JSON）。
- **全量 `pytest tests/f08 -q`：`not_run`**。理由：本票未改 `product/` 生产代码、未改
  T084 语料/runner（见 `git diff --stat` 仅 tests/f14 + experiments + reports + PROGRESS）；
  T084 语料回归 4 passed，且 registry/dataset 摘要逐字节锚定 T084 证据 JSON；共享
  WSL/runsc 栈在收尾期间被其它集成任务连续占用（等待锁超过 1 小时），不并发跑 16 分钟
  真实栈。T084 自身最终树的全量 f08 门禁也是 `not_run`（其报告修正）。
- Go 门禁不适用：无 `internal/`/`cmd/` 改动；`docs/spec/**`、`contracts/**`、迁移均未改。

## 8. 边界与风险

1. **证据强度**：臂 A 是 T084 目录的独立复现（同 34 例、同卡、同 runner），但 sales
   版本以 T077 fixture 路径发布（T084 是真实 T068 发布）；路由只依赖卡内容，故决策可比，
   但本票不重新声明 T068 发布已完成。
2. **卡内容敏感**：同一语料只换 sales 卡就产生 5 个错误，说明“冻结语料 + 冻结目录卡”
   必须一起冻结；T084 冻结语料时使用的 sales 卡是通用测试卡，与产品模板卡不一致。
   建议后续把 T084/T090 的目录卡与产品真实卡对齐后重新冻结（会改变基线数字，需重新预注册）。
3. **语料饱和**：34 例消息按卡片 token 手工构造，不能测语义改写、复合需求、追问与
   候选乱序（`TRAINING-EXPERIMENT.md:46` 要求）。1.0 不能外推为真实环境路由质量。
4. **dev-stub 边界**：所有“决策”是服务端策略确定性结果；`stub:true`，无模型质量含义。
5. **规模**：25 例 held-out 低于 spec 起步建议，仅能发现问题，不能证明泛化。
6. **证据文件可重放性**：版本 UUID/时间/耗时每次运行不同；JSON 的汇总率、逐例决策与
   卡片哈希可复现。仓库内 JSON 是本次证据运行产物，普通 pytest 不会改写它。

## 9. 重放命令

```powershell
cd E:\Orchara-wt\t090\product
python -m pytest tests/f14 -q                     # 冻结/泄漏 + 受控真实栈基线（约 75s）
$env:T090_ROUTING_REPORT='reports/f14/T090-routing-baseline.json'
python -m pytest tests/f14/test_t090_baseline.py -q -s   # 证据运行（会重写 JSON）
python -m pytest tests/f08/test_t084_corpus.py -q        # T084 语料回归（纯 Python）
python tools/sync_spec.py --check
```
