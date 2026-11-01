# 测试、Agent评测与测试集实施契约

状态：Q46已确认的实施基线。确定性checker与统一TaskRunner必需，首个通用质量适配器优先验证DeepEval，其他工具按需替代/补充。用例格式为文档契约；未实现产品TaskRunner，未运行质量框架或外部benchmark。

## 1. 不同验收的职责

| 类别 | 执行工具 | 真值 | 失败意味着什么 |
|---|---|---|---|
| 单元/契约 | Go testing、pytest、JSON Schema/OpenAPI工具 | 类型、状态与权限规则 | 程序不符合规格 |
| 完整应用任务 | 平台TaskRunner＋stub/DB断言 | 最终产物和外部实际状态 | 用户任务未完成或产生错误副作用 |
| Agent质量 | DeepEval候选或Promptfoo替代 | 人工标签、工具轨迹、可选judge | 语义质量问题，需要分项解释 |
| RAG | 来源checker＋可选Ragas/DeepEval | 目标来源、引用/ACL、回答依据 | 召回、生成、引用或越权分别定位 |
| 注入/安全 | AgentDojo思路＋平台安全负例 | 正常任务、attack_success、外部状态 | 安全失败，不能用平均质量抵消 |
| 故障/容量 | Toxiproxy、supervisor、k6 | Run/operation/配额/外部账本 | 恢复/取消/可用性或资源问题 |
| 浏览器体验 | Playwright＋真实用户任务观察 | 交互与可理解性 | UI不能正确表达状态/授权 |

## 2. 一个统一任务采集器

`PlatformTaskRunner.run(case, execution_manifest) → EvaluationArtifact`：

1. 在隔离测试环境创建fixture、测试账号/资源授权与模拟企业系统状态。
2. 经正式API提交请求或指定版本/调试快照；记录幂等键，处理案例预定义的澄清/选择/人类审批。
3. 等待Run终态或明确超时；采集节点/工具回执、child归属、成本、引用和产物。
4. 独立读取stub/DB最终状态。不能从“模型输出已发送”推断写入成功。
5. 导出受权评测artifact，分别运行hard checker与可选质量指标。

首次HTTP202、模型流结束、网页显示成功均不是充分真值。采集器不得绕过授权直接调用内部图充当完整平台验收。纯节点质量实验可以单独调用节点，但报告需标明测试层次。

EvaluationArtifact包括：case/split/输入来源、应用或debug snapshot、worker/model/tool版本、Run状态、最终输出引用、工具调用参数摘要/回执、外部状态摘要、引用来源、用量、错误及采集完整性。敏感原文需要单独授权；默认不把全部trace正文复制进CI日志。

## 3. 用例和判定器

机器契约为 [evaluation-case.schema.json](contracts/evaluation-case.schema.json)，样例见 examples/evaluation。case必须有至少一个assertion；工具/状态断言中的expected=0为有意义的预期，不能把“没有判定器”混为零错误。

业务人员首版维护少量应用样例、输入和期望字段/断言，查看报告；复杂故障注入、外部benchmark适配和自定义checker由开发者在CI维护。不把评测产品扩展成任意脚本在线运行平台。

内置声明式checker：JSON字段相等、Decimal金额相等、Run状态、某能力真实副作用次数、引用来自允许来源。字段引用采用JSON Pointer，不eval用户代码；预期金额用decimal字符串。开发者自定义checker在可信测试镜像/隔离环境执行，不能由普通使用者上传到平台主进程。

Agent工具轨迹可能有多个合理计划，除安全/业务要求外不盲目要求唯一工具顺序。最终效果、允许动作集合、次数与关键依赖分开判断。副作用次数读取stub账本，不能只数模型tool-call文本。

## 4. 内部测试集

| 子集 | 首批案例 | 独立真值/边界 |
|---|---|---|
| sales | CSV/XLSX等价、缺列/重复订单/负值/公式/超限 | 独立Decimal校验，不复用被测脚本生成期望 |
| routing | 唯一/歧义/缺输入/无匹配/未见应用/越权候选 | 授权目录快照与人工标注合理处理集合 |
| rag | 正确片段缺失、生成错误、伪引用、撤销资料 | 文档/版本/chunk真值，权限确定性检查 |
| memory | 确认/拒绝/修改/删除、矛盾偏好、用户/应用隔离 | 平台active/revision/来源；副本清理检查 |
| delegation | 相同子应用并发、重复投递、父预算、满槽等待 | 独立Run/thread、幂等键与配额账本 |
| approval_effects | 换参数、重复批准、写后丢回执、取消 | 规范参数hash、实际写次数、unknown核对 |
| lifecycle | 旧图恢复、终态迟到、删除后恢复、Redis失联 | 固定制品与DB状态不变量 |

先做有解释的少量案例，不以测试集规模代替覆盖。dev与frozen_test按任务/应用家族划分；同模板改写、同资料衍生问答不得随机拆分造成泄漏。真实用户数据需另行授权，训练同意与诊断同意分开。

## 5. 外部测试集怎样使用

### τ²-bench

复用任务/初始环境/动作与环境checker的结构，从政策约束和写操作任务选小子集。保留来源commit/task ID/许可及改编差异。所读评估器无evaluation_criteria可直接reward=1，因此导入门禁拒绝空checker/空reward_basis，不把框架默认成功当测试通过。

完整suite有用户模拟和额外模型调用，独立实验环境与预算；不能将所有依赖强加给生产worker。改变工具/审批/环境后使用“改编任务”标签，不称官方排行榜成绩。

### AgentDojo

复用不可信检索/工具结果中的注入思路与utility/security双判定。该接口security=True代表攻击目标成功，映射为attack_success=true；只有正常任务完成且攻击未成功才满足该类安全期望。测试多余副作用，例如仅要求标已读却改主题。

它不覆盖OS逃逸、平台IDOR和PG竞态；这些仍需本平台安全/故障测试。BFCL、GAIA等本轮未深读，不列为已核验测试集；后续只有明确需要的能力才增加。

### 数据来源清单

每个导入条目记录repo、commit、原task ID、path/hash、许可证文件、第三方附加来源、工具/环境依赖、是否改编、split/family、checker版本、可再分发状态。来源或许可未确认的条目可以留研究链接，不能直接打包进公开数据集。根代码许可证不自动证明所有模型/数据素材可再分发。

## 6. 质量适配器与私有数据边界

**DeepEval优先探针**：平台artifact→LLMTestCase/多轮case/工具数据→显式配置指标与获批judge→逐例指标/原因/用量。避免默认把生产trace自动上报；`offline_evals.evaluate_trace`所读实现请求Confident云，不能当本地入口。

**Promptfoo替代探针**：自定义provider复用TaskRunner完成202/交互/等待，再给断言；不直接把初次HTTP响应当最终答案。CLI仅开发期使用，遥测/远端provider受控。

**Ragas专项探针**：映射检索片段和reference source IDs，验证当前collections/experiment接口；不复制已弃用evaluate教程。judge/embedding全部显式指定。Faithfulness不能证明检索正确、ACL合法或引用未删除。

“本地评测”只表示runner/报告在本地，不必然表示judge离线。报告分别记录数据流向、模型端点、遥测/上传开关和实际出站观察；需要完全离线时采用本地judge并预置依赖/权重。

## 7. 先做的三个探针和停止条件

1. **成功文本但没有副作用**：同一录制任务交hard checker和judge；若judge高分但stub没有写入，发布仍失败。验证指标不会覆盖hard gate。
2. **RAG四种失败**：缺目标来源、错答案、伪引用、越权片段分别注入；结果应能定位相应阶段，不能仅出一个综合分。
3. **写后丢响应**：stub确认写成功后丢回执，必须unknown+核对且真实写次数1；网络工具只负责时序，业务账本给真值。

全部待运行。若本地接口必须上传不允许的正文、judge/依赖不兼容或成本不合理，换适配器或缩小指标；不放宽数据策略。若确定性checker已覆盖首期需求，可延后质量框架。

重复模型实验至少报告每次结果/范围、版本、输入hash和费用；在预算允许时预先确定重复次数，不能挑一次高分。质量、可靠性、性能和安全分别报告；空验/跳过/not_run不得计为passed。

## 8. 用户可见与开发者工具的边界

推荐完整首版仅提供应用样例管理、运行评测、查看版本化报告与发布门禁。任意指标市场、在线训练、任意benchmark可视化编排和全量实时judge不加入默认交付。组件复用应减少重复工作，而不是扩大产品范围。

固定源码/测试与许可证据见 [评测研究](research/evaluation-reuse-audit.md)，集成后的效果和成本仍未验证。
