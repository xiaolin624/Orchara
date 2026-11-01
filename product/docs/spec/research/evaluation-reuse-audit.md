# Agent、RAG、测试集与故障测试的开源复用核查

查询日期：2026-10-31。目标：为 Go 平台 API/SSE、Python LangGraph/DeepAgents 执行层选择可复用的评测组件，并明确业务验收中仍需我们实现的判定器。

**推荐结论：第一周使用 pytest/Go testing 与平台自己的确定性验收；Agent 质量评测优先验证 DeepEval 的本地用例/指标接入；Ragas 作为 RAG 专项候选，Promptfoo 作为 HTTP 黑盒回归候选。三者不同时列为首版强制服务。外部测试集只引入能适配并保留判定器的小子集。**

后续状态：Q46已由用户确认上述分层与候选策略。这里的源码证据和未运行范围保持不变，不将用户确认写成组件已集成或指标已实测。

本次使用 GitHub MCP 阅读仓库元信息、固定 commit 文件、部分测试与许可证；没有安装或运行这些框架，没有调用付费模型，没有跑任何 benchmark。下述“可接入”是依据接口的设计推断，非兼容性实测结果。目录树请求中有未完成项，改用明确目录与文件核查；未找到的路径不作为“不支持”的证据。

## 1. 固定阅读版本

| 项目/官方仓库 | 所读 commit | commit 日期 | 所读根许可证 | 建议位置 |
|---|---|---|---|---|
| [Promptfoo](https://github.com/promptfoo/promptfoo) | `5582f347bbc5359b201ce2d48c9c7fbebd8dcbd9` | 2026-10-31 | MIT | 开发/CI 黑盒评测候选 |
| [DeepEval](https://github.com/confident-ai/deepeval) | `90e230cbe916c73240a48312c494065a39bf52d1` | 2026-10-29 | Apache-2.0 | 开发/离线质量评测候选 |
| [Ragas](https://github.com/vibrantlabsai/ragas) | `298b68274234c060deacab3cf5fb52aa3a20e885` | 2026-02-24 | Apache-2.0 | RAG 专项评测候选 |
| [τ²-bench](https://github.com/sierra-research/tau2-bench) | `da0835f6c67baef58bdda18f2a3b037dfb3a1754` | 2026-10-30 | MIT | 外部任务/环境/判定器参考 |
| [AgentDojo](https://github.com/ethz-spylab/agentdojo) | `089ed468cf3ed0322acc66b0211f26d9d90dbf60` | 2026-06-02 | MIT | 提示注入与工具环境测试参考 |
| [Toxiproxy](https://github.com/Shopify/toxiproxy) | `49299d8838525126e09c85ebe0ba89940a3261e5` | 2026-10-14 | MIT | 集成/故障测试依赖 |

上述仓库查询时均未标记 archived。commit 是阅读锚点，不是已经通过平台集成测试的安装版本。许可证原文分别见 [Promptfoo](https://github.com/promptfoo/promptfoo/blob/5582f347bbc5359b201ce2d48c9c7fbebd8dcbd9/LICENSE)、[DeepEval](https://github.com/confident-ai/deepeval/blob/90e230cbe916c73240a48312c494065a39bf52d1/LICENSE.md)、[Ragas](https://github.com/vibrantlabsai/ragas/blob/298b68274234c060deacab3cf5fb52aa3a20e885/LICENSE)、[τ²-bench](https://github.com/sierra-research/tau2-bench/blob/da0835f6c67baef58bdda18f2a3b037dfb3a1754/LICENSE)、[AgentDojo](https://github.com/ethz-spylab/agentdojo/blob/089ed468cf3ed0322acc66b0211f26d9d90dbf60/LICENSE)、[Toxiproxy](https://github.com/Shopify/toxiproxy/blob/49299d8838525126e09c85ebe0ba89940a3261e5/LICENSE)。根许可证不能替代所用第三方模型、远程数据或附加内容的单独来源核对。

## 2. 先分清要证明的事情

| 层次 | 需要的证据 | 复用边界 |
|---|---|---|
| 合约与程序正确性 | Schema、状态转换、权限、幂等、取消、预算账本 | pytest/Go testing；判定条件由平台契约定义 |
| 单个 Agent 行为 | 工具选择/参数、任务结果、停止行为、重复调用 | DeepEval 或 Promptfoo 接收平台导出的标准结果；不能只看答案文字 |
| 多 Agent/工作流 | 子任务归属、权限交集、预算、等待与恢复 | 平台集成测试；通用质量框架不自动验证这些不变量 |
| RAG | 检索命中、引用真实性、答案依据、拒答、权限 | 确定性来源/ACL checker + 可选 Ragas/DeepEval 质量指标 |
| 外部副作用 | 实际写入/发送了什么、是否重复/越权 | 模拟系统状态与回执判定；LLM judge 不能作唯一真值 |
| 故障与性能 | 网络分区、旧进程存活、DB事务、队列/槽位、资源 | Toxiproxy + supervisor + 受控 stub + DB 断言 |

因此“接入一个 Agent 评测框架”不等于完成企业平台测试。质量分数、状态真值和安全硬门禁分别报告。

## 3. DeepEval：适合复用用例与质量指标，不能把云端接口当本地能力

### 3.1 已读入口与数据

`deepeval/test_case/llm_test_case.py::LLMTestCase/ToolCall` 定义 input、actual_output、expected_output、retrieval_context、tools_called、expected_tools 等数据；`ToolCall` 还区分 FUNCTION/MCP。`conversational_test_case.py::Turn/ConversationalTestCase` 表达多轮内容及工具数据。由接口可知，可以把已运行平台任务导出成评测输入，不必为了评测再次执行真实外部写操作。[固定 LLMTestCase/ToolCall](https://github.com/confident-ai/deepeval/blob/90e230cbe916c73240a48312c494065a39bf52d1/deepeval/test_case/llm_test_case.py)、[固定多轮模型](https://github.com/confident-ai/deepeval/blob/90e230cbe916c73240a48312c494065a39bf52d1/deepeval/test_case/conversational_test_case.py)。

`deepeval/evaluate/evaluate.py::evaluate/assert_test` 接收这些用例及指标，可生成本地结果/HTML/Markdown；`ToolCorrectnessMetric` 比较 tools_called/expected_tools，可选择顺序或精确匹配；`TaskCompletionMetric` 将输入、输出、工具或 trace 提供给质量评判路径。其构造器和生成函数包含模型初始化/调用，因此不能把工具指标一概当成“零模型调用的纯离线函数”。本平台的参数等值、Decimal 金额、操作次数与权限断言仍用确定性 checker。[evaluate](https://github.com/confident-ai/deepeval/blob/90e230cbe916c73240a48312c494065a39bf52d1/deepeval/evaluate/evaluate.py)、[ToolCorrectnessMetric](https://github.com/confident-ai/deepeval/blob/90e230cbe916c73240a48312c494065a39bf52d1/deepeval/metrics/tool_correctness/tool_correctness.py)、[TaskCompletionMetric](https://github.com/confident-ai/deepeval/blob/90e230cbe916c73240a48312c494065a39bf52d1/deepeval/metrics/task_completion/task_completion.py)。

交叉核对的测试：`tests/test_metrics/test_tool_correctness_metric.py` 覆盖工具类型、顺序/精确匹配等；该文件在没有 OPENAI_API_KEY 时跳过，不能把存在这些测试说成无需 API 就能跑全部指标。`test_task_completion_tools_called.py` 使用 fake generate 捕获真正交给 judge 的提示，验证工具名没有因模板参数错误丢失；这是本平台也应采用的集成检查方法。原始历史 issue 未另行读取，不据测试注释推断当前未解决缺陷。[工具测试](https://github.com/confident-ai/deepeval/blob/90e230cbe916c73240a48312c494065a39bf52d1/tests/test_metrics/test_tool_correctness_metric.py)、[任务完成回归测试](https://github.com/confident-ai/deepeval/blob/90e230cbe916c73240a48312c494065a39bf52d1/tests/test_metrics/test_task_completion_tools_called.py)。

### 3.2 需要防止混淆的云边界

`deepeval/tracing/offline_evals/trace.py::evaluate_trace/a_evaluate_trace` 虽然路径包含 offline_evals，实际创建 Confident API 客户端并 POST `EVALUATE_TRACE_ENDPOINT`；不能把这条接口写进“完全本地离线评测”的承诺。应验证本地用例/指标路径，明确 judge 端点，再决定是否接云服务。[固定实现](https://github.com/confident-ai/deepeval/blob/90e230cbe916c73240a48312c494065a39bf52d1/deepeval/tracing/offline_evals/trace.py)。

`test_run/test_run.py::wrap_up_test_run` 先保存本地结果，在 `is_confident()` 且允许请求时上传；本地保存和上传是不同分支。评测环境必须固定上传/遥测策略、限制出站并检查实际网络，而非仅凭“SDK开源”假设数据不外发。judge 请求本身也可能包含用户输入、工具输出、检索片段；需与在线模型一样审查目的地和授权。[结果保存/上传实现](https://github.com/confident-ai/deepeval/blob/90e230cbe916c73240a48312c494065a39bf52d1/deepeval/test_run/test_run.py)。

### 3.3 推荐接入

新增一个开发期 `DeepEvalAdapter`：读平台规范化、获授权的 evaluation artifact，映射用例、显式注入获批 judge，输出逐例指标/原因/模型/用量/成本/错误。先验证无需追踪装饰器也能评估录制结果；需要 trace 的指标再做字段映射。不给生产 Agent 主循环自动插入全量内容采集；不让评测指标直接修改业务状态。

未验证：全链路依赖锁版、所选 judge 的结构化输出兼容、离线 trace 的完整映射、当地网络策略及实际费用。推荐是候选实施方案，不是已接入能力。

## 4. Ragas：RAG专项模型与指标可复用，必须避开过时入口和默认外部模型

`src/ragas/dataset_schema.py::SingleTurnSample` 包含 user_input、retrieved_contexts/reference_contexts、上下文 ID、response/reference；`MultiTurnSample` 包含 Human/AI/Tool 消息及 reference_tool_calls，并校验工具消息顺序。这些字段适合承接平台已经记录的检索来源和回答。[固定数据模型](https://github.com/vibrantlabsai/ragas/blob/298b68274234c060deacab3cf5fb52aa3a20e885/src/ragas/dataset_schema.py)。

但所读 `evaluation.py::evaluate/aevaluate` 已发出弃用警告，推荐 `@experiment`；旧入口在某些需要 LLM 的指标没有传模型时会创建 OpenAI 客户端及默认模型。不能把旧博客里的 `evaluate(dataset)` 原样纳入私有化方案。P0 应围绕固定版本的 collections 指标/experiment 接口验证，并显式传 judge 与 embedding，避免隐式外部请求。[固定旧入口与警告](https://github.com/vibrantlabsai/ragas/blob/298b68274234c060deacab3cf5fb52aa3a20e885/src/ragas/evaluation.py)、[experiment 实现](https://github.com/vibrantlabsai/ragas/blob/298b68274234c060deacab3cf5fb52aa3a20e885/src/ragas/experiment.py)。

`metrics/collections/faithfulness/metric.py::Faithfulness.ascore` 接收问题、回答、检索上下文，通过 statement 生成和 NLI 判定计算依据一致性；这是模型辅助指标，存在额外调用、随机性与 judge 偏差。它检查回答相对于传入上下文是否有依据，不能证明检索到了正确资料，更不证明片段有权限或引用未删除。后两项必须用 doc/version/chunk/ACL 与 tombstone 的确定性校验补上。[固定 Faithfulness](https://github.com/vibrantlabsai/ragas/blob/298b68274234c060deacab3cf5fb52aa3a20e885/src/ragas/metrics/collections/faithfulness/metric.py)。

已读 `tests/unit/test_async_evaluation.py` 展示 mock/Executor 层的异步测试；未运行，不将其当真实中文RAG效果。`_analytics.py::do_not_track/track` 读取 `RAGAS_DO_NOT_TRACK=true` 并控制用量遥测；建议实验环境显式关闭并通过出站观测验证，judge 数据传输仍单独控制。[异步测试](https://github.com/vibrantlabsai/ragas/blob/298b68274234c060deacab3cf5fb52aa3a20e885/tests/unit/test_async_evaluation.py)、[遥测实现](https://github.com/vibrantlabsai/ragas/blob/298b68274234c060deacab3cf5fb52aa3a20e885/src/ragas/_analytics.py)。

**建议**：先实现确定性 retrieval recall@k、引用可解析/版本正确、越权命中=0；RAG进入质量改进阶段时，再加 Ragas Adapter。若 DeepEval 已覆盖实际所需质量指标并达到可信度要求，可暂缓额外依赖，不为了“全都用过”同时运行两套相同 judge。

## 5. Promptfoo：HTTP黑盒回归很合适，异步平台协议仍需适配

`src/providers/http.ts::HttpProvider.callApi/callApiInternal` 提供请求体/头、认证、response transform、session 等配置；读到的普通请求路径用 `fetchWithCache` 取得完整响应再转换；`test/evaluator/sessions.test.ts` 验证 provider 返回的 sessionId 与 vars 的优先关系。它能复用平台 HTTP 边界，不要求平台改用某种 Agent 框架。[HTTP provider](https://github.com/promptfoo/promptfoo/blob/5582f347bbc5359b201ce2d48c9c7fbebd8dcbd9/src/providers/http.ts)、[session 测试](https://github.com/promptfoo/promptfoo/blob/5582f347bbc5359b201ce2d48c9c7fbebd8dcbd9/test/evaluator/sessions.test.ts)。

本平台 `POST requests` 返回202、可能澄清、再生成Run、等待审批，不能把初次HTTP响应当最终 Agent 回答。推荐自定义 provider/runner 执行“提交→必要的受控交互→轮询终态/读取结果→导出工具回执”，然后交给断言。SSE断线重连另用集成测试。这里只说已经读到的普通请求路径不自动完成平台协议，不宣称 Promptfoo 整体不支持多轮或流式。

官方 LangGraph 指南给出 Python provider+YAML 测试的接入方式，但那份示例不等于我们的发布、授权、审批和恢复已被覆盖。自定义 provider 必须只使用测试账号/模拟外部系统，不触发真实生产写入。[固定 LangGraph 指南](https://github.com/promptfoo/promptfoo/blob/5582f347bbc5359b201ce2d48c9c7fbebd8dcbd9/site/docs/guides/evaluate-langgraph.md)。

`src/telemetry.ts::getPostHogClient` 检查 `PROMPTFOO_DISABLE_TELEMETRY`；配置中还可能有 judge/远程 provider。禁遥测不等于禁 judge 出网。建议仅在开发/CI使用本地 CLI，不默认把它的服务器或UI变成平台生产必需服务。[遥测源码](https://github.com/promptfoo/promptfoo/blob/5582f347bbc5359b201ce2d48c9c7fbebd8dcbd9/src/telemetry.ts)。

**推荐取舍**：若优先 Python 内部工具/轨迹质量，先验证 DeepEval；若优先跨语言公共 API 黑盒回归，Promptfoo 是合理替代。平台自己的 TaskRunner/事件采集器都需要实现。首版先选一个通用质量适配器，不叠加两个管理同一套测试集的工具。

## 6. 外部任务集：τ²-bench 与 AgentDojo

### 6.1 τ²-bench：复用可执行环境和结果判定的方法

`data_model/tasks.py::Task/EvaluationCriteria/Action/EnvAssertion` 将用户场景、初始状态、预期动作、环境断言和 reward_basis 分开；`evaluator/evaluator.py::evaluate_simulation` 可分别评估动作、环境、沟通，包含 recorded trajectory replay 参数。它比“回答像不像参考答案”更适合启发受控写操作验收。[任务模型](https://github.com/sierra-research/tau2-bench/blob/da0835f6c67baef58bdda18f2a3b037dfb3a1754/src/tau2/data_model/tasks.py)、[评估入口](https://github.com/sierra-research/tau2-bench/blob/da0835f6c67baef58bdda18f2a3b037dfb3a1754/src/tau2/evaluator/evaluator.py)。

已读 airline/tasks.json 的实际任务：用户要求取消预订，但应遵守取消政策；数据同时给 instructions、evaluation_criteria 和 reward_basis。因此可研究“用户坚持错误操作时是否拒绝”和“最终环境有无变化”。这些任务不直接覆盖我们的人类审批、组织 ACL、文件分析、删除传播；需要自己补案例。[固定任务数据](https://github.com/sierra-research/tau2-bench/blob/da0835f6c67baef58bdda18f2a3b037dfb3a1754/data/tau2/domains/airline/tasks.json)。

一个必须移植的防空验检查：当前 `evaluate_simulation` 在没有 evaluation_criteria 时返回 reward=1 并注明原因。导入外部任务时必须拒绝缺 checker/缺断言的测试，不能把默认成功当任务质量。[同一评估入口](https://github.com/sierra-research/tau2-bench/blob/da0835f6c67baef58bdda18f2a3b037dfb3a1754/src/tau2/evaluator/evaluator.py)。

当前 pyproject 要求 Python `>=3.12,<3.14`，依赖 LiteLLM 等；测试标记区分付费 live_api。`run.py::run_task` 已是弃用兼容层，提示使用 run_single_task + RunConfig。完整运行包含 agent/user simulation 等角色，必须按实际配置预算，不能只计算一次回答费用。建议独立实验环境，避免把其 Python/推理依赖直接并入生产 worker。[依赖](https://github.com/sierra-research/tau2-bench/blob/da0835f6c67baef58bdda18f2a3b037dfb3a1754/pyproject.toml)、[运行兼容层](https://github.com/sierra-research/tau2-bench/blob/da0835f6c67baef58bdda18f2a3b037dfb3a1754/src/tau2/run.py)。

许可范围：已读仓库根MIT与仓内任务JSON，未逐项审查全部domain及外部附加数据。拷贝/修改选中任务时保留来源、commit、task ID、版权/许可、变更说明，第三方素材另核对。改变工具/用户模拟/判定后，报告为“改编任务”，不与官方排行榜直接比较。

### 6.2 AgentDojo：同时检查正常任务与注入攻击结果

`base_tasks.py::BaseUserTask.utility/utility_from_traces` 可用执行前后环境及工具序列判断正常任务；`BaseInjectionTask.security/security_from_traces` 判断攻击目标是否完成。注意 security 的 True 代表注入目标成功，平台报告应转换为明确的 attack_success，避免把名称误读成安全通过。[固定任务接口](https://github.com/ethz-spylab/agentdojo/blob/089ed468cf3ed0322acc66b0211f26d9d90dbf60/src/agentdojo/base_tasks.py)。

`benchmark.py::run_task_with_injection_tasks/benchmark_suite_with_injections` 把正常用户任务、注入任务、agent pipeline 和 attack 分开；`default_suites/v1/workspace/user_tasks.py` 包含实际工具环境任务；`tests/test_workspace_user_tasks.py` 检查标记邮件已读时不得顺便修改主题等其他字段。这为平台提供具体方法：不仅检查任务做成，还检查没有顺手产生多余写入。[benchmark](https://github.com/ethz-spylab/agentdojo/blob/089ed468cf3ed0322acc66b0211f26d9d90dbf60/src/agentdojo/benchmark.py)、[实际任务](https://github.com/ethz-spylab/agentdojo/blob/089ed468cf3ed0322acc66b0211f26d9d90dbf60/src/agentdojo/default_suites/v1/workspace/user_tasks.py)、[副作用测试](https://github.com/ethz-spylab/agentdojo/blob/089ed468cf3ed0322acc66b0211f26d9d90dbf60/tests/test_workspace_user_tasks.py)。

建议首批移植“检索片段/工具结果中夹带泄密或写操作指令”的思路，到我们的知识库和能力代理：正常任务成功率、攻击成功率、未授权能力调用被拒绝数分开记录。AgentDojo 的环境/攻击机制不自动测出 gVisor 逃逸、平台 IDOR 或数据库竞态，仍需系统安全测试。

当前 pyproject 包含多个模型SDK、LangChain、DeepDiff等，可在隔离的测试环境运行；没证据要求把其全套依赖放进在线worker。已读根MIT、仓内任务代码和测试；未逐个审计所有环境数据/攻击payload来源，也未运行正式suite。[固定依赖](https://github.com/ethz-spylab/agentdojo/blob/089ed468cf3ed0322acc66b0211f26d9d90dbf60/pyproject.toml)。

## 7. 故障测试：Toxiproxy负责网络时序，平台负责故障语义

`toxics/timeout.go::TimeoutToxic.Pipe` 丢弃流数据；timeout>0 时到期关闭连接，timeout=0 时保持连接并丢弃数据。`timeout_test.go` 覆盖超时与移除toxic后的连接关闭。可把它插在 Go↔Python、代理↔模型stub、平台↔Redis/PG之间，构造在途响应丢失与网络黑洞。[实际 toxic](https://github.com/Shopify/toxiproxy/blob/49299d8838525126e09c85ebe0ba89940a3261e5/toxics/timeout.go)、[测试](https://github.com/Shopify/toxiproxy/blob/49299d8838525126e09c85ebe0ba89940a3261e5/toxics/timeout_test.go)。

它不能替代“供应商已成功写入但本地未记回执”的可控业务stub，也不能证明旧worker退出；后者仍由supervisor与DB会话证据负责。每个故障测试应记录注入位置/时间、operation ID、外部真实状态、Run终态以及是否重发。网络断开不能一律标记未发送。

## 8. 最小实施边界与验证动作

### 建议默认栈

1. **必需测试底座**：pytest、Go testing、固定HTTP/model/tool stub、独立统计真值与数据库断言；平台 TaskRunner 负责真实 API 请求、等待、审批、结果/回执采集。
2. **质量组件**：优先小实验验证 DeepEvalAdapter；仅把需要主观语义的部分交judge，确定性hard gate独立运行。Promptfoo为替代候选；Ragas为RAG专项候选，不同时强制三套。
3. **故障组件**：测试环境部署 Toxiproxy，配合进程终止、资源限制及checkpoint检查；不作为线上转发服务。
4. **外部测试集**：τ²-bench与AgentDojo各挑有明确task/checker的小子集；业务销售/路由/权限/记忆删除集自建并冻结。BFCL本轮未查，不列为已核验依赖。

### 先做三个便宜的集成探针

- **录制结果探针**：一个成功、一个错误工具参数、一个答案声称成功但stub无写入的Run。导出相同输入给确定性checker和质量指标；若judge高分却实际未执行，必须由hard gate判失败，并在报告呈现分歧。
- **RAG探针**：固定小资料集，分别制造缺正确片段、正确片段但错误答案、伪引用、越权片段。要求检索/依据/引用/权限四种失败可区分；不能用一个faithfulness分数覆盖。
- **故障探针**：stub写入成功后Toxiproxy丢响应，预期unknown+人工核对而非重复写入；成功次数、重复次数、实际状态用stub账本判断。

上述均待运行。若适配器必须把正文上传云端才能得到必要指标、依赖冲突无法隔离、模型成本超过用例预算，或judge与人工/确定性真值持续不一致，应换组件/缩指标，不把不可靠评测设成发布硬门禁。

**本轮消除的不确定性**：通用Agent/RAG评测有可复用的数据模型和指标，但不能承包平台正确性；同名“offline”接口可能依赖云；当前教程API可能已弃用；外部任务需保留可执行判定器而非只复制问题。

**可能推翻建议的证据**：本地无上传路径无法满足必要指标、强确定性测试已覆盖全部首期需求、适配成本高于少量自建checker、真实业务任务与外部suite差异过大。当前尚不能下结论的部分是实际安装兼容性、中文质量、judge可靠性/成本及用户认可度。
