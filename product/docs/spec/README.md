# Agent 创作与运行平台：开发规格 v0.3

文档审阅日期：2026-11-01。目标读者：实现该项目的开发者、测试者与项目评审者。

**全新会话先读 [HANDOFF.md](HANDOFF.md)**，再看PROGRESS与任务图即可开始环境盘点/P0；无需旧聊天记录或本机技能。具体补缺与检查范围见 [DOCUMENT-AUDIT.md](DOCUMENT-AUDIT.md)。这里没有已实现平台或可直接启动的业务Compose。

**结论：采用 Go 平台服务、Vue 3/Vue Flow 编辑器、Python LangGraph 整图执行与 DeepAgents 自主节点，建设单企业、多用户、可私有部署的 Agent 应用平台。** 第一周交付销售文件分析的纵向切片；完整首版的十类节点、知识库、多 Agent、审批、API 和离线微调随后按依赖完成。

这里交付的是开发规格和可检查的契约，不是已经上线的企业系统。Q1–Q46已确认，包含长期记忆的门禁接入策略与分层评测方案；设计共同理解已达成，不再重复确认这些决定。Mem0、DeepEval等候选仍需按方案验证，确认策略不等于组件已经通过门禁。运行时兼容性、故障恢复、安全隔离、容量和微调收益均尚未实测。

## 阅读与实施顺序

| 文档 | 回答的问题 |
|---|---|
| [HANDOFF.md](HANDOFF.md)、[BOOTSTRAP.md](BOOTSTRAP.md)、[PROGRESS.md](PROGRESS.md) | 新会话如何从零接手、放置代码、检查环境和持续记录 |
| [IMPLEMENTATION-ORDER.md](IMPLEMENTATION-ORDER.md) | 可执行依赖顺序、M1与完整V1完成条件 |
| [PRODUCT.md](PRODUCT.md)、[GLOSSARY.md](GLOSSARY.md) | 给谁用，范围是什么，术语如何统一 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 模块、技术栈、信任边界和代码组织 |
| [OPEN-SOURCE-REUSE.md](OPEN-SOURCE-REUSE.md) | 每层复用哪些组件、接入边界、保留的工程工作 |
| [COMPONENTS-AND-CLONE.md](COMPONENTS-AND-CLONE.md) | 哪些源码值得clone、哪些只需安装依赖 |
| [WORKFLOW-SPEC.md](WORKFLOW-SPEC.md) | 画布如何成为可执行的固定版本工作流 |
| [UX.md](UX.md) | 业务用户如何创作、诊断、审批和管理数据 |
| [EXECUTION.md](EXECUTION.md)、[STATE-MACHINES.md](STATE-MACHINES.md) | 调度、检查点、审批、重试、取消和恢复 |
| [IMPLEMENTATION-DETAILS.md](IMPLEMENTATION-DETAILS.md)、[BACKGROUND-JOBS.md](BACKGROUND-JOBS.md) | 启动/模型/评测闭环、终态交接、无Run后台任务 |
| [COMPILER-CONTRACT.md](COMPILER-CONTRACT.md) | 节点封套、类型/模板、region编译和汇合细则 |
| [DATA-MODEL.md](DATA-MODEL.md) | 状态权威、表、索引和事务不变量 |
| [API.md](API.md) | Web/API/worker 的调用与错误契约 |
| [SECURITY.md](SECURITY.md)、[DATA-POLICY.md](DATA-POLICY.md) | 权限、脚本、凭据、正文访问和删除 |
| [ROUTING-MEMORY-KNOWLEDGE.md](ROUTING-MEMORY-KNOWLEDGE.md) | 意图路由、上下文、记忆与知识检索 |
| [EVALUATION.md](EVALUATION.md)、[TRAINING-EXPERIMENT.md](TRAINING-EXPERIMENT.md) | 如何证明任务成功、可靠性及训练收益 |
| [TESTING-AND-DATASETS.md](TESTING-AND-DATASETS.md) | TaskRunner、Agent/RAG评测、外部任务集与判定器 |
| [OPERATIONS.md](OPERATIONS.md) | 部署、锁版、备份恢复、资源与运行手册 |
| [DEVELOPMENT-PLAN.md](DEVELOPMENT-PLAN.md) | 任务依赖、第一周与完整首版完成条件 |
| [REVIEW.md](REVIEW.md) | 开发前整体确认的默认值和阻断关卡 |
| [REQUIREMENTS-TRACEABILITY.md](REQUIREMENTS-TRACEABILITY.md) | 44项产品决策对应哪些实施与验收任务 |
| [VALIDATION.md](VALIDATION.md) | 本轮实际检查了什么、哪些尚未运行 |

机器可读契约位于 [contracts](contracts)：SQL 参考基线、DSL JSON Schema、OpenAPI、事件 Schema；合法及非法工作流例子位于 [examples](examples)。运行 `python tools/validate_contracts.py` 检查文档内契约的静态一致性。该命令不启动平台，也不证明数据库迁移或实际运行行为正确。

v0.2增加组件状态清单 `contracts/components.json`、评测用例Schema和正反例、可选克隆脚本。重建这些新增文档契约使用 `python tools/build_reuse_contracts.py`，不安装或启动候选组件。具体许可证和源码覆盖范围在四份新增research中逐项记录。

## 规格的证据等级

- **已确认需求**：来自 [DESIGN-SESSION.md](DESIGN-SESSION.md) Q1–Q44，不因技术方便而删除。
- **推荐设计**：本文档中的表结构、内部协议、默认参数与开发顺序；经整体审阅后作为实施基线。
- **源码事实**：见 [research](research) 的 commit 固定链接及阅读范围；不把 README 承诺当作平台实测能力。
- **待验证**：P0 集成实验、外部服务行为、SLO、性能和模型效果。失败必须缩小能力或重新设计，不得改写为已通过。

优先级冲突时，先看HANDOFF中的已确认约束，再看当前详细规格与机器契约；发现同级矛盾记录并修复而非凭旧聊天补齐。research与DESIGN-SESSION保留历史，不作为重新打开决策的依据。API字段以OpenAPI为准，跨表事务和授权以详细规格为准；任务依赖以task-graph.json为准，不把旧按天估时当完成事实。

## 本轮消除与保留的不确定性

已经明确产品边界、执行责任、版本固化和数据权限需要由平台补齐；LangGraph/DeepAgents/Vue Flow 不会自动提供这些企业语义。尚未证明两套语言与沙箱在固定版本上的集成可行性、用户愿意维护能力卡/测试集、20 顶层运行的容量及 LoRA 收益。

可能推翻当前方案的证据：旧版本无法恢复、审批重放导致重复写入、沙箱网络策略无法落地、必要任务无法用十类节点表达，或业务人员不能独立完成一个真实应用。对应停止条件见 [REVIEW.md](REVIEW.md)。Stars 不作为工程验收或采用意愿的替代指标。
