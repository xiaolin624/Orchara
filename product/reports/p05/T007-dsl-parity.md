# P05 T007 验收：用同一 corpus 校验两语言的变量和类型

- 任务：P05 / GitHub Issue [#8](https://github.com/xiaolin624/Orchara/issues/8)（`T007`）
- 状态：**passed**（仅限本票探针范围内的校验器行为；图执行语义由 T011 单独验收）
- 代码：`probes/p05_dsl/`（Python 与 Go 均只用标准库）
- 数据：`probes/p05_dsl/corpus.json`（40 例：7 正 33 负）、`probes/p05_dsl/rules.json`（两端共用的规则表）
- 测试：`product/tests/p05/test_dsl_parity.py`（97 passed）、`probes/p05_dsl/go`（7 passed）
- 环境：Windows，Python 3.12.2 / pytest 9.0.3，Go 1.27.3 windows/amd64；无凭据、无网络调用

## 1. 两个实现，一份 corpus，一份规则表

| 端 | 位置 | 依赖 |
|---|---|---|
| Python（参考实现） | `probes/p05_dsl/python/validate.py` | 仅标准库；规范化复用 `probes/jcs/python/jcs.py`（P01/T003） |
| Go（发布侧复验） | `probes/p05_dsl/go/`（自带 `go.mod`） | 仅标准库；JCS 序列化半部分移植自 T003，并用同一批向量回查 |

> **已知重复代码（如实记录）**：`probes/p05_dsl/go/jcs.go` 是
> `probes/jcs/go/jcs.go` 的副本，不是共享依赖。原因是两个 Go 模块各自独立、且 T003
> 的实现是 `package main`（不可 import）。缓解措施：本票的 `go test` 会拿 T003 的 RFC
> 向量回跑这份副本，一旦漂移会失败；彻底的修法是先把 T003 拆成可 import 的库 + 命令，
> 属重构，未在本票做。

两端各有一个 CLI，输出同一份 canonical 信封：

```powershell
python probes/p05_dsl/python/validate.py --corpus probes/p05_dsl/corpus.json
go -C probes/p05_dsl/go run . -corpus ../corpus.json
```

实测两端 stdout **逐字节相同**：24 965 字节，`sha256 7755d5713e5572b8e3baeb1ff9a99531026075488b7f5c37a22f39e52bd0c3df`。
`-check` 模式会逐例比对 verdict / reason 列表 / digest，两端均输出 `OK: 40 cases`。

## 2. golden corpus：每个正负例都有预期和 hash

| 字段 | 含义 |
|---|---|
| `verdict` | `valid` / `invalid` |
| `input_sha256` | **每个** case 都有：`input` 的 JCS canonical 文本的 SHA-256 |
| `expected_hash` | 正例：normal form（data 摘要）的 SHA-256 |
| `expected_canonical` | 锚例：手写 normal form 文本，便于人工核对 digest |
| `reason` / `reasons` | 负例：主原因码与完整有序原因列表（优先级也是契约） |

- 正例来自真实契约样例：`examples/sales`、`examples/parallel`、`examples/branch-approval`；
  另加两份自撰文档：三节点线性锚例、条件区嵌套并行区的嵌套例（`COMPILER-CONTRACT.md` §2/§4）。
- 负例都是上述文档的**单点命名变异**（`build_corpus.py` 里逐条写明改了什么），`--check` 可检出陈旧 pin。
- 文件摘要：`corpus.json` = `fcfc0ebb4ba11525…`、`rules.json` = `c36d560d80ae9971…`。

**诚实说明（复核后补充）：**

- **拒绝原因是手写的，且被强制校验**：33 个负例都在 `build_corpus.py` 里逐条写明 `reason`，
  builder 一旦发现与实现不符就 `SystemExit`；`test_corpus_is_reproducible` 会跑
  `build_corpus.py --check`。所以"Python 对 Python 写出的期望"这一说法对**原因码不成立**。
- **真正由实现产出的**是 valid 例的 `expected_hash`（规范化摘要）与负例的 `reasons` 细目；
  这两者只有一个手写 canonical 锚点。规范摘要没有第三方 oracle（JCS 那层由 T003 的 RFC 向量锚定）。
- 因此独立性来自三处：手写并被强制的 33 条原因码、独立的 Go 实现逐例复现、以及那条手写锚点。
  想让哈希也完全独立，需要为 7 个正例各手写一份规范文本——目前只做了 1 个，属已知欠缺。

## 3. 拒绝原因与优先级

五个阶段，**只有第一个出问题的阶段决定 verdict**，阶段内按检测顺序去重：

| 阶段 | 检查 | 原因码 |
|---|---|---|
| 1 | 文档形状、受限 Schema 子集、按节点类型的 config 白名单、policy 范围 | `bad_document` `bad_id` `unknown_type` `bad_binding` `bad_schema` `bad_config` `bad_edge` `bad_policy` |
| 2 | 身份与端点 | `duplicate_id` `unknown_endpoint` `start_end_count` |
| 3 | DAG 性质 | `cycle` `unreachable_node` `cannot_reach_end` `merge_without_join` |
| 4 | 结构化区域：配对 → 端口 → 跨区边 → join 来源 | `invalid_region` `bad_port` `cross_region_edge` `bad_join_sources` |
| 5 | 引用、指针、支配、缺值 | `unknown_ref` `bad_pointer` `non_ancestor_ref` `type_mismatch` `missing_value` |

- 24 个原因码全部在 `rules.json` 声明，且**每个码都有负例**（测试断言双向覆盖）。
- 票面点名的三类都有专门负例：`unknown-node-type` → `unknown_type`；
  `condition-branch-escapes-outer-join` → `cross_region_edge`；`cycle` → `cycle`。
- 优先级用三个探针钉住：`precedence-schema-over-duplicate-id`（我们报 `unknown_type`，
  旧文档 linter 报 `duplicate_id`）、`precedence-cycle-over-missing-value`、
  `precedence-port-over-join-sources`。
- 每个问题都带 JSON Pointer 形状的 `pointer`（`/nodes/3/inputs/summary`、`/edges/4`），
  两端给出**一致且可定位**的结论，而不只是布尔值。

## 4. 缺值不隐式 null

- `end-missing-required-output`：`end` 未映射应用 `output_schema.required` 的 `summary`
  → `missing_value` @ `/nodes/3/inputs/summary`，且**不产出** normal form / artifact；wire 里没有
  `data_sha256`、`data_canonical`、`artifact_sha256` 这些键（缺失保持缺失，而不是写成 null）。
- `"inputs": null` 是**值**不是缺失：按形状错误 `bad_document` 拒绝；删除该键同样拒绝（不默认成 `{}`）。
- 正例的 normal form 里递归检查不存在 `null`；模板只允许读已绑定输入（`{{extra}}` → `missing_value`）；
  被跳过分支的引用（祖先但非支配）同样是 `missing_value`，不默默返回 null。

## 5. data 与 artifact 分离

- Python：`NormalizedWorkflow`（data：normal form + 其 JCS 摘要）与 `WorkflowArtifact`
  （artifact：schema id + data 摘要 + provenance）是两个类型、两个对象、两个 digest；Go 同样分离。
- 测试断言：`data.sha256 != artifact.sha256`、`artifact.descriptor["data_sha256"] == data.sha256`；
  只改 provenance（corpus 摘要 0000 → ffff）时 artifact digest 变、data digest 与 pinned 值不变。

## 6. 覆盖的验收条件

- [x] 正负例均有预期和 hash — 每个 case 有 `verdict` + `input_sha256`，正例另有 `expected_hash`（含一个手写锚例）
- [x] 非法类型/跨区/循环有拒绝原因 — 见第 3 节，代码在一处声明、两侧实现、corpus 全覆盖
- [x] 现有文档 linter 只作参考 — 测试实际运行 `tools/validate_contracts.py` 的 `graph_errors`：
      33 个负例里它 18 个沉默、1 个直接崩溃、原因词表更小且优先级不同；没有任何 pin 来自它
- [x] Go/Python 对相同输入结论一致 — 两端 CLI stdout 逐字节相同；`go test` 与 pytest 各自复跑同一 corpus
- [x] 缺值不隐式 null — 见第 4 节（两端各有断言）
- [x] Python data 与 artifact 分离 — 见第 5 节
- [x] 通过端到端入口实际演示 — 两端 `-check` 可重放；失败路径即 33 个负例的 code+pointer
- [x] 记录版本/输入 hash/命令/原始结果 — 本文件第 7 节

## 7. 原始命令与结果

```text
python -m pytest product/tests/p05/test_dsl_parity.py -q   → 97 passed in 3.71s
python -m pytest product/tests/p05 -q                      → 105 passed in 7.46s   （含 T011 的 8 个）
python -m pytest product/tests -q                          → 366 passed in 236.72s
go -C product/probes/p05_dsl/go test ./...                 → ok orchara/p05dsl 0.739s（7 个测试）
go -C product/probes/p05_dsl/go vet ./...                  → 无输出
python product/probes/p05_dsl/python/validate.py --corpus … --check → OK: 40 cases
go -C product/probes/p05_dsl/go run . -corpus ../corpus.json -check  → OK: 40 cases
python product/probes/p05_dsl/build_corpus.py --check       → OK: corpus.json is up to date (166537 bytes)
```

## 8. 边界（未证明的部分）

- 不执行图：激活 token、skipped、join 恰一次等运行时语义由 T011 的探针验收；本票只证明静态校验，
  以及"被跳过分支的引用会被静态拒绝"。
- 纯函数探针：没有 I/O、凭据、授权、重试或恢复路径，因此不存在"授权/重试边界"可断言，也没有
  `blocked` 项。发布流程后续阶段（依赖 ready、作者授权、测试门禁、内容哈希）不在本票范围。
- `ref` 绑定的输入没有静态类型，`gt`/`gte`/`lt`/`lte`/`in` 只在绑定了 literal 时做类型检查。
- `end` 中不属于 `output_schema.properties` 的多余输入未拒绝；输入/输出 Schema 只做"受限 object schema"
  检查，没有解析成完整类型系统。
- 区间边界（跨区）的入口/出口支配性按**边**逐条判定，而不是整区域拒绝；这样更可定位，
  但也意味着"区域配对本身错"和"边跨区"用的是不同的码（`invalid_region` / `cross_region_edge`）。
