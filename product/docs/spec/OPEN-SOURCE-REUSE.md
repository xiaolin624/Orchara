# 开源复用总方案 v0.2

日期：2026-10-31。范围：整个平台、前端、工作流、Agent、多 Agent、模型接入、RAG、记忆、工具、隔离、测试/评测/测试集、训练和部署。保留已确认的 Go/Vue Flow/LangGraph/DeepAgents 架构，不重新发明通用 Agent 框架。

**开发基线：独立平台仓库＋成熟组件直接依赖＋必要适配层。** 热门整平台用于研究具体模块和测试，不把多套平台/引擎都装进本项目。Q45/Q46已确认：PG记忆基线＋Mem0门禁对照，确定性测试＋统一TaskRunner＋一个通用质量适配器。确认底座仍须锁版与实跑，候选保持门禁状态，不能将策略确认写成已经集成。

## 1. 状态和复用方式

- **确认底座**：已有用户决策；版本兼容尚待 P0。
- **推荐默认**：本轮提出的可逆实现默认值，整体审阅后实施。
- **门禁候选**：先与基线做收益/治理实验，通过才进入正式依赖。
- **测试期组件**：仅在开发、CI或独立实验环境使用，不增加默认生产服务。
- **源码参考**：读其结构/测试；不代表可以随意复制代码或须部署整仓。

复用可采用普通包、独立服务、适配器、上游贡献或源码参考。只有公共扩展点不足且收益明确时才维护 fork；每个 fork 记录 patch、对应 upstream commit、回归测试和升级成本。

## 2. 总体拆解

| 平台部分 | 组件/项目 | 采用状态与方式 | 本项目必须实现 |
|---|---|---|---|
| 整个平台 | Coze Studio、Dify、Flowise | 源码参考，不整仓作为默认底座 | 产品领域、权限、请求/Run、发布与数据治理 |
| Web基础与控件 | Vue3、TypeScript、Vite；Element Plus | 前三为既定栈；Element Plus推荐默认包依赖 | 页面、业务表单、错误提示、角色交互 |
| 工作流画布 | Vue Flow | 确认底座，直接包依赖 | 自定义节点、类型变量选择器、DSL/布局映射 |
| 工作流执行 | LangGraph、Postgres checkpointer | 确认底座，Python库 | DAG编译、激活/汇合语义、版本制品、恢复交接 |
| 单个自主Agent | DeepAgents、其LangChain依赖 | 确认底座，库与受控适配 | 工具白名单、上下文、预算、结果/错误契约 |
| 多Agent | DeepAgents委派接口＋平台child Run | 复用框架接口，不增加第二种Agent引擎 | 固定子版本、ACL交集、独立thread、父预算、幂等委派 |
| 统一意图路由 | PostgreSQL全文/pgvector＋结构化模型决策 | 推荐默认，不另建通用路由框架 | 授权候选、能力卡、澄清、拒识、一次一个主应用 |
| 模型接入 | 选定供应商适配器；LiteLLM为门禁候选 | 先最少协议，多供应商收益明确再引入 | 模型目的地、授权、费用账本、唯一重试/降级权威 |
| 脚本运行 | OpenSandbox＋gVisor/runsc | 确认底座，服务/运行时 | 固定环境、文件契约、网络/资源策略、监督和清理 |
| 工具互操作 | MCP Python SDK | 后续连接器候选，不扩大首周范围 | 工具注册审核、Schema快照、授权、审批/回执适配 |
| CSV/XLSX | Python csv/Decimal、openpyxl等解析包 | 轻量库依赖 | 错误数据规则、公式/压缩风险、独立金额真值 |
| 文档解析 | 普通文本reader；Docling Native PDF候选 | 模块包/隔离解析任务 | 准入、限制、页/字符出处、解析失败与版本 |
| 分块 | LlamaIndex core的SentenceSplitter/NodeParser | 优先评估纯转换组件 | SourceSpan贯通、平台chunk ID、中文/布局质量 |
| 组织RAG | PostgreSQL/pgvector＋解析/分块/模型组件 | 基础知识库已确认，不引入整套第二平台 | 资料ACL、摄取状态、检索、引用、撤销和删除 |
| 个人长期记忆 | 平台PG基线；Mem0 OSS候选适配器 | 收益/确认/完整删除门禁后决定 | pending→confirmed、冲突、来源、scope、正文生命周期 |
| Go服务/数据库 | net/http＋chi、pgx/sqlc | 推荐包/工具依赖 | 业务事务、错误、资源授权、配额和API |
| 任务调度 | PostgreSQL durable jobs，Redis唤醒 | 当前推荐，不引入第二套工作流队列 | 领取/租约、supervisor、配额、防重复、交接事务 |
| 身份与权限 | 首版本地账号；成熟密码/会话基础库 | 需求已确认；具体库锁版时核查 | 用户/角色/资源ACL、撤销、API Key scope、诊断授权 |
| 文件/对象存储 | S3兼容接口；已有S3优先、SeaweedFS自部署候选 | 服务，不把具体发行版强制成产品边界 | artifact归属、短期下载、tombstone、备份/清理 |
| 观测 | OpenTelemetry SDK；本地Collector可选 | 包依赖/按需服务 | 业务时间线、元数据白名单、正文授权和关联ID |
| 程序/契约测试 | Go testing、pytest、Schema/OpenAPI校验 | 测试期底座 | 确定性checker、任务runner、stub和DB断言 |
| 浏览器与负载 | Playwright、k6 | 测试期工具 | 画布/审批测试场景、负载、指标与判定 |
| Agent质量 | DeepEval优先候选；Promptfoo替代候选 | 测试期适配器，先选一个通用质量入口 | 平台任务采集、用例转换、judge目的地/成本、硬门禁 |
| RAG质量 | 确定性来源检查；Ragas专项候选 | 测试期适配器，按需增加 | 召回真值、引用/权限检查、质量与程序错误分离 |
| 外部任务集 | τ²-bench、AgentDojo小子集 | 测试期参考/改编，保留task+环境+checker | 版本/许可/来源、环境映射、业务用例和冻结split |
| 故障注入 | Toxiproxy | 测试环境服务 | 精确故障点、远端副作用账本、退出/DB证据 |
| 微调 | LlamaFactory；TRL/MLflow仅参考/可选 | 已确认离线LoRA实验，独立环境 | 数据来源、强基线、split、消融、报告与模型登记 |

Go基础库、Vue/Vite等按常规依赖使用；本轮未逐个深入阅读其全部实现，不把它们标为安全/性能已验证。深读范围、符号及固定链接见第12节。

## 3. 为什么不直接fork整个平台

- **Coze Studio** 最适合读 Go 平台分层、画布转换与运行准备。但所读实现为 Go/Hertz/Eino＋React，部署示例还有多种数据/队列服务。整仓采用意味着接受或替换另一套引擎/前端/领域模型。
- **Dify** 可参考变量池、发布/运行准备、并行人工输入恢复的测试；它使用 Python/Flask/Celery＋React，根许可含附加条件。
- **Flowise** 可参考变量选择和导出敏感字段白名单测试；独立 Agentflow 包也依赖 React/ReactFlow/MUI，整仓另有商业许可分区。

上述判断来自源码、依赖与许可文本，不以README的节点数量衡量。当前栈已确认，因此不因某个平台也有Agent功能就重新打开所有架构问题。若将来明确愿意接受其完整栈、以最快获得现成功能为主，才重评整仓二次开发。

固定证据见 [整平台研究](research/platform-reuse-audit.md)。复制任何具体实现前核查该文件许可证和依赖；参考设计不意味着直接复制其测试数据或把结果称为原创。

## 4. 一条应用如何连接组件

```text
VueFlow编辑 + ElementPlus配置
  → 平台DSL/能力卡/输入输出Schema
  → Go校验/测试/审核/固定版本
  → Go创建Request与Run、持久job
  → Python加载固定制品、编译LangGraph
       ├─model节点：受控模型适配器
       ├─agent节点：DeepAgents + 平台工具/委派适配器
       ├─python节点：OpenSandbox/runsc
       ├─retrieve节点：Go授权检索 → 有出处片段
       └─call_app节点：child Run/独立thread
  → Go确认结果、登记artifact与SSE事实
  → 受权EvaluationArtifact → checker/可选质量适配器
```

框架内部消息、memory/node/document对象不成为公共API。输入、输出、错误、版本、权限与用量都在平台适配边界归一化。这个整合的收益是可追溯的一条运行链，而不是把每个热门框架都变成线上服务。

## 5. Agent、模型和多Agent的具体接入

DeepAgents复用自主循环与工具/子Agent机制；平台仅暴露已授权的能力。文件、终端、任意HTTP与默认继承工具清单需要逐项核查，不能把框架默认能力全部交给业务创建者。

`PublishedAppDelegate` 将模型提出的任务映射到已登记的固定子应用版本，调用Go创建child Run，返回结构化结果。不要把同一子应用的所有并发请求放进同一持久thread；每次调用以parent invocation key幂等，并有独立Run/thread与预算占用。

`AgentModelAdapter` 将DeepAgents所需模型接口连接到Go授权的模型调用路径；具体BaseChatModel/供应商适配器接口在固定依赖上做P0，不以普通HTTP函数已通就认为工具流式调用兼容。重点检查tool-call IDs、结构化输出、stream中断、cancel和usage。SDK隐藏重试必须关闭或纳入唯一策略权威；用stub实际调用次数和费用账本验证。

LiteLLM可减少多供应商适配工作，但它的router/fallback/计费元数据不能悄悄成为第二个业务状态中心。若采用，须写明策略所有者、调用attempt映射、兼容性和成本；首版不默认部署其企业控制台。详见 [基础设施研究](research/infrastructure-reuse-audit.md)。

## 6. RAG与记忆的具体复用

**RAG**：隔离解析任务产出带SourceSpan的文本块；LlamaIndex分块后转为平台chunk；embedding走获批端点；平台PG按当前权限检索；答案引用到本次真实片段。Docling优先评估NativePdf路径，扫描件/OCR/复杂表格不因此自动成为首版承诺。

采用LlamaIndex完整IngestionPipeline/PGVectorStore虽可节省部分摄取逻辑，也会带来docstore/cache/text副本及自身迁移。当前推荐先复用纯转换，只有证明权限/删除/版本对齐才扩大。`UPSERTS_AND_DELETE` 的全量集合语义不能误用于每次增量小批次。

**Mem0**：默认 `infer=True` 所读V3主路径自动提取、去重并新增持久记忆；不能用仍提及ADD/UPDATE/DELETE的docstring代替执行代码。确认后写入可用 `infer=False` 做对照，但此时主要复用存储/检索，不自动证明比PG基线更有价值。

Mem0即使用PG向量库，所读构造仍初始化SQLite历史；删除路径会保留旧正文到DELETE历史。接入需清理历史/消息/实体/向量/日志副本，并做多worker管理。平台确认、revision、权限和tombstone始终为权威。不存在“装上Mem0就完成了企业记忆”。详见 [RAG/记忆研究](research/rag-memory-reuse-audit.md)。

## 7. 评测与测试集的具体复用

完整契约见 [TESTING-AND-DATASETS.md](TESTING-AND-DATASETS.md)。

1. **程序正确性**：Go testing/pytest＋独立真值。金额、权限、状态、外部写次数与取消/恢复是硬断言，不由LLM评分决定。
2. **平台TaskRunner**：经真实API提交、处理测试交互、等待终态、收集结果/工具回执/外部状态。所有评测框架共用这个采集层，不各写一套绕过权限的入口。
3. **Agent语义质量**：优先验证DeepEval本地用例适配。Promptfoo适合作为HTTP黑盒替代，仍需处理202/澄清/Run等待，不能评初次响应就算完成。
4. **RAG专项**：先有来源/权限/召回断言，再按需用Ragas的依据一致性等质量指标。避免与通用适配器重复judge收费。
5. **任务集**：外部任务导入环境、策略、checker和来源，明确改编。τ²-bench启发环境/动作判定，AgentDojo启发正常任务和攻击成功分开统计；不直接拿其分数证明平台企业可靠性。

所读DeepEval一条名为offline_evals的trace路径会向云API发送；Ragas旧evaluate/aevaluate入口已弃用；τ²没有evaluation_criteria会返回reward=1。集成时必须避开云边界误判、过时API及空验。详见 [评测研究](research/evaluation-reuse-audit.md)。

## 8. 安全、身份和部署组件边界

本地账号/RBAC按已确认需求实现，不因已有Keycloak/Casbin等产品就额外部署第二套身份/策略服务。后续SSO按OIDC接口接，重开身份迁移决策前只作为候选，不把企业认证整套复制进首版。密码散列、TLS和会话基础使用成熟库，不自行设计密码学。

OpenSandbox提供生命周期，runsc提供执行隔离，平台负责谁能运行/访问什么；三者不是互相替代。MCP复用协议，不授予工具权限。S3服务管理对象，平台管理artifact授权。OTel记录技术追踪，平台事件决定业务状态。这些边界决定了哪些工作可以复用，哪些不能省掉。

私有部署还要核查包/镜像/模型是否隐式下载、judge端点、遥测与正文副本。开源SDK不自动等于全链路离线。源码许可、权重许可、数据许可、商业功能许可分开记录，不由Stars推断。

## 9. 自己实现什么，为什么值得做

| 自有模块 | 需要解决的具体问题 | 可以展示的证据 |
|---|---|---|
| 类型DSL/编译适配 | 画布编辑与可恢复执行的语义一致 | golden corpus、分支/汇合/跳过测试 |
| 版本制品/依赖闭包 | 新发布不改变旧任务 | 旧版本恢复、镜像/Schema追溯 |
| Request/Run/授权 | 澄清、队列、选择、身份不能混淆 | 幂等提交、跨用户拒绝、取消状态 |
| 能力代理/副作用账本 | 审批精确绑定、未知结果不重发 | 故障注入、stub最终状态、operation回执 |
| 子运行与预算 | 并行隔离、无提升权限、无槽位死锁 | 独立thread、父预算和满载子任务测试 |
| 来源/删除传播 | 多库/缓存/checkpoint中的副本可治理 | 删除账本、引用与恢复负例 |
| TaskRunner与业务checker | 回答说成功不代表外部操作成功 | 真实状态与judge分歧报告 |

不自行实现图执行器、画布渲染引擎、向量数据库、沙箱内核或训练框架。组件选择的工程深度在可解释的适配和验证中，不在重新实现这些通用底层。

## 10. 分阶段依赖，不一次部署全部候选

- **首周**：既定栈、四节点线性样例、预置统计工具、Go/Python测试、最少控件和现有S3/测试对象服务。无任意脚本上传、无强制Mem0/Ragas/完整外部benchmark。
- **完整首版基础**：十节点、多Agent、审批/恢复、RAG/个人记忆、API、数据治理和运行评测；Element Plus、分块/解析适配和一套质量适配器按探针结果锁定。
- **按证据启用**：Mem0、Docling模型OCR路径、完整LlamaIndex摄取、LiteLLM、MCP连接器、额外质量框架；未通过不放进生产依赖。
- **独立实验**：LlamaFactory、外部任务环境、k6/Playwright/Toxiproxy。可生成可分享成果，不必成为线上常驻服务。

## 11. 本轮结论与可推翻证据

消除了“每层是否已有可复用组件”和“接库是否能自动提供平台语义”的不确定性：多数通用能力可复用；权限、版本、审批/恢复、副作用真值和数据治理仍需平台落实。

尚未消除锁版兼容、真实用户收益、中文RAG效果、Mem0治理/召回收益、judge可信度与部署负担。若对照发现组件收益低于适配成本，或必须违背确认/删除/权限才能接入，就不用它；若整平台移植探针反而明显省事且符合既定边界，也可重新评价当前建议。

## 12. 证据与阅读范围

- [整平台/画布/UI研究](research/platform-reuse-audit.md)：Coze/Dify/Flowise/Vue Flow/Element Plus，固定源码/依赖/测试/许可。
- [RAG/记忆研究](research/rag-memory-reuse-audit.md)：Mem0/LlamaIndex/Docling，实际调用路径与测试交叉核对。
- [评测/任务集研究](research/evaluation-reuse-audit.md)：DeepEval/Ragas/Promptfoo/τ²-bench/AgentDojo及Toxiproxy。
- [模型/协议/存储/工程工具研究](research/infrastructure-reuse-audit.md)：LiteLLM/SeaweedFS/MCP/OTel/Playwright/k6，明确部分文件与未完成范围。
- 既定引擎/恢复/脚本证据沿用 research 既有固定commit。所有新组件调查为静态，不把测试文件存在当已运行结果。

克隆与安装清单见 [COMPONENTS-AND-CLONE.md](COMPONENTS-AND-CLONE.md)；机器清单为 contracts/components.json。阅读commit与安装稳定发行版分开记录，不建议把全部仓库当前HEAD直接混装。
