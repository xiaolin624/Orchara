# T090 / F14 — 冻结清单（routing baseline）

- 机器可读：`experiments/routing_sft/freeze/T090-freeze.json`
  （schema `orchara.t090-routing-freeze/1`，生成于 2026-11-06T00:14:28+0800，
  git head `0deeab9`）。
- 生成/复核：`python -m tests.f14.routing_freeze`；一致性由
  `python -m pytest tests/f14/test_t090_freeze.py -q` 断言（语料/卡定义漂移即失败）。
- 本清单只冻结**不依赖模型**的评测输入。状态 `blocked`：同基模强提示基线、
  任何训练都不在本机能力范围内（见 JSON `strong_prompt_arm` / `training`）。

## 1. 语料：直接引用 T084 的冻结路由语料，不复制第二份

来源 `tests/f08/routing/datasets/`（T084 #85 冻结；本票未修改任何一例）。

| 项 | 值 |
|---|---|
| corpus_version | `1.0.0` |
| frozen_at | `2026-11-05` |
| registry_sha256 | `491e4f25b6f8fbf91a8d345d1c260e2f576411978058cb3ab02e21351e39e42e` |
| dataset_digest | `9128080ae557062f6ff5c0e2c96d999bad0e5bb8e52a0061cf554fb0e13071d2` |
| 例数 | 34（`frozen_test` 25 / `dev` 9） |
| families | 26（18 frozen + 8 dev），同一 family 只在一个 split/单元格 |
| T084 证据锚 | `reports/f04b/T084-routing-eval.json`（head `7c0e456`），其 registry/dataset 摘要与本清单一致 |

逐文件 sha256：

| 文件 | sha256 | size |
|---|---|---|
| `ambiguous.json` | `73433b4d…04e04`（全值在 JSON） | 4013 |
| `missing_input.json` | `9d8ee8d0…ebbc5` | 6144 |
| `no_match.json` | `1032c870…50997` | 2852 |
| `unauthorized_candidate.json` | `6701daba…9f76d` | 3463 |
| `unique.json` | `389b5acd…6fead2` | 6543 |
| `unseen_application.json` | `947c52b7…2698a9` | 3826 |
| `inputs/sales_sample.csv` | `a6be4cf4…2cd88ca` | 114 |

`frozen_test` 是 held-out：不得用于调参、提示迭代或训练数据筛选；`dev` 仅作观察。
25 例低于 `EVALUATION.md:35` 建议的 100 例起步规模，本票不虚增规模，报告里明写。

## 2. 目录/能力卡：两套都被冻结（同一批 34 例）

T084 证据运行里 “sales” 版本实际携带的是通用卡
`tests/support/f02a.full_card()`（purpose `summarise sales`），不是产品销售模板卡；
T084 报告 catalog 也记录了这个 purpose。T090 因此冻结两套卡定义：

| 套 | definitions_sha256 | sales card_sha256 | 用途 |
|---|---|---|---|
| `frozen_t084_catalog` | `62a2e50edc935b255b9ecff606582731a68daa54373274b84f1679f0e66c1398` | `8c1cdba7…4297c41c8` | 复现 T084 的目录（基线臂 A） |
| `product_template_card_probe` | `01295c2ad5525e238a07dfde04f2ac27cf53a2ef69fb82e5734c73dbee603399` | `7f337a8a…41e6e19` | 仅换入产品模板卡（探针臂 B） |

其余六个 fixture 卡（`forecast/inventory/invoice/alpha/beta/payroll`）两套相同，
逐个 `card_sha256` 见 JSON `catalog.*.cards`。卡哈希用平台同一规范化
（RFC 8785 形状：sorted keys + compact separators + UTF-8）计算；基线报告再从
平台 `application_versions.capability_card` 读回、重算并与本清单比对。

版本 UUID / `manifest_sha256` 由真实编译器在一次性 PG 集群里生成，不能静态复现，
因此按运行记录在 `reports/f14/T090-routing-baseline.json` 的 `catalog` /
`card_variant_probe.catalog`（每应用 application_id、version_id、manifest_sha256、
card_sha256）。

## 3. 检索冻结：显式 `not_used`，不伪造 revision

```json
"retrieval": {"not_used": true, "embedding_model_revision": null, "chunker_version": null,
  "kb_document_revision": null, "index_revision": null, "acl_epoch": null}
```

冻结路由语料只走“授权 SQL 候选 + 能力卡字面 token 匹配”，不涉及知识库文档、
embedding、chunker、parser 或向量索引，因此没有可冻结的检索 revision。冻结的是
授权目录卡定义与真实候选快照（`platform.request_routing_candidates.card_sha256`）。

## 4. 控制模型：dev-stub，不是语义基线

`provider=stub`、`stub=true`、`validation_state=passed`、`config.route_control=true`、
`data_destination=local-only`。它是一个真实注册的模型 profile，但**不是厂商模型**，
不能冒充强提示基线，也不产生任何模型质量信号；候选授权、必填输入与决策策略都由
服务端拥有。

## 5. 环境

| 项 | 值 |
|---|---|
| git head / branch | `0deeab9…` / `t/t090-routing-baseline` |
| Python | 3.12.2 |
| Go | go1.27.3 windows/amd64 |
| PostgreSQL | 运行时记录在基线报告（本次 `PostgreSQL 16.4`，隔离集群 `127.0.0.1:55432`） |
| OS | Windows 11 (AMD64) |
| profile | dev-stub（本机唯一模型路径；无厂商凭据） |

## 6. 重放

```powershell
cd E:\Orchara-wt\t090\product
python -m pytest tests/f14/test_t090_freeze.py -q     # 冻结/家族/近重复/许可/卡哈希
python -m tests.f14.routing_freeze                    # 仅在显式重新冻结时才应运行
```

`T090-freeze.json` 由测量值生成：逐文件 sha256、dataset digest、family→split/cell、
跨 split 重复/近重复、provenance、两套卡定义哈希都必须与磁盘一致，否则测试失败。
