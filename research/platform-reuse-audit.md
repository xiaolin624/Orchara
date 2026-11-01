# 整个平台、工作流与画布的开源复用核查

查询日期：2026-10-31。来源为 GitHub MCP 获取的官方仓库元数据、固定 commit 源码、依赖声明、许可证、部署定义和部分测试。没有克隆这些大仓、安装依赖、运行服务或执行上游测试。Stars 是查询快照，不用于证明质量或本项目适配性。

现有前提：Go 平台后端，Vue 3/Vue Flow 画布，Python LangGraph 执行整图，DeepAgents 执行自主节点；用户数据权限、固定版本、审批、副作用回执由本平台负责。下文建议不自动改变这些已经确认的边界。

## 1. 结论先行

**推荐保持独立平台仓库，直接依赖成熟组件，选取整平台的设计与测试作为参考。** Coze Studio 是最值得读的 Go 平台参考，但整仓二次开发会把执行引擎、前端及存储拓扑一同引入；Dify、Flowise 也不是可直接装入现有架构的 UI 模板。

推荐复用层次：

| 层次 | 现行推荐 | 应由本项目补齐 |
|---|---|---|
| 整平台 | Coze/Dify/Flowise 用作模块和交互参考，暂不整仓 fork | 当前需求对应的领域模型、权限和执行协议 |
| 画布渲染/交互 | 直接依赖 Vue Flow | 节点配置、变量选择、DSL、发布校验和运行投影 |
| 表单/变量/导出行为 | 阅读 Flowise Agentflow 的组件及测试，按许可证处理代码复用 | Vue 适配及本平台字段语义，禁止凭据导出 |
| 工作流执行 | 已选 LangGraph；上游引擎代码只作语义和测试参考 | DSL 编译、版本制品、受控副作用和租约交接 |
| Go 平台模块 | 可阅读 Coze 的画布转换、类型映射、发布/执行分层 | 不直接把其 internal 包当独立 SDK 使用 |

以上是基于静态调用关系的适配成本推断，不是已经完成移植后的工时或性能结论。

## 2. 取样与版本

| 项目 | 官方仓库 | Stars | 所读 HEAD / commit 日期 | 许可证事实 |
|---|---|---:|---|---|
| Dify | [langgenius/dify](https://github.com/langgenius/dify) | 160086 | `4c521a3c0a1f60607c8a7bcf90792f34adcf18d0` / 2026-10-31 | modified Apache 2.0，含附加条件 |
| Flowise | [FlowiseAI/Flowise](https://github.com/FlowiseAI/Flowise) | 55536 | `9291856d1ea4a4ceea9f8fef8ce14f4f6c81e8eb` / 2026-08-13 | 根许可区分 Apache 部分与商业许可部分 |
| Coze Studio | [coze-dev/coze-studio](https://github.com/coze-dev/coze-studio) | 21788 | `fefb05ff27be1da939612fbf9faf5db62583b8ae` / 2026-07-29 | 根 `LICENSE-APACHE` 为 Apache 2.0 |
| Vue Flow | [bcakmakoglu/vue-flow](https://github.com/bcakmakoglu/vue-flow) | 6926 | `17953c329db2d5dc5f5097370cbf5e1173bab520` / 2026-06-23 | MIT |

许可证核查只记录文本及适配注意点，不给出针对具体分发/商业模式的法律结论。依赖、模型和复制文件的许可还需逐项核对。

## 3. Coze Studio：Go 平台参考最接近，但整仓复用意味着另一套引擎

### 已读事实

- [`backend/go.mod`](https://github.com/coze-dev/coze-studio/blob/fefb05ff27be1da939612fbf9faf5db62583b8ae/backend/go.mod) 声明 Go 1.24.0，依赖 Hertz、Eino、GORM、MySQL 驱动、go-redis，并包含多种模型/嵌入组件。它不是一个纯 UI 模板。
- [`frontend/apps/coze-studio/package.json`](https://github.com/coze-dev/coze-studio/blob/fefb05ff27be1da939612fbf9faf5db62583b8ae/frontend/apps/coze-studio/package.json) 声明 React/ReactDOM、React Router、Rsbuild 和 workspace 包。不能因后端为 Go 就推定可以直接使用 Vue Flow 前端。
- [`docker/docker-compose.yml`](https://github.com/coze-dev/coze-studio/blob/fefb05ff27be1da939612fbf9faf5db62583b8ae/docker/docker-compose.yml) 定义 MySQL、Redis、Elasticsearch、MinIO、etcd、Milvus、NSQ 和应用服务。这里只说明被读部署文件的服务拓扑，不断言每个服务在所有部署模式下都必需。
- [`LICENSE-APACHE`](https://github.com/coze-dev/coze-studio/blob/fefb05ff27be1da939612fbf9faf5db62583b8ae/LICENSE-APACHE) 为 Apache 2.0 正文；具体第三方资源仍需核查。

### 实际转换与调用链

`CanvasToWorkflowSchema` 先剪除孤立节点，逐节点调用 `NodeToNodeSchema`、转换边、规范化端口，再构建 branches 和初始化 WorkflowSchema。它建立了编辑模型与执行模型之间的适配层，值得参考，而不是把前端图直接执行。

源码：[`backend/domain/workflow/internal/canvas/adaptor/to_schema.go::CanvasToWorkflowSchema / NodeToNodeSchema / normalizePorts`](https://github.com/coze-dev/coze-studio/blob/fefb05ff27be1da939612fbf9faf5db62583b8ae/backend/domain/workflow/internal/canvas/adaptor/to_schema.go#L66)。

随后 `NewWorkflow` 创建 `eino/compose.Workflow`，添加普通/复合节点，必要时挂 checkpoint repository，并调用 `Compile`。`Workflow.Compile` 补充 START/结束依赖后交给 Eino 编译。该调用链以 Eino 的类型、state、checkpoint 和节点适配为基础，并非 LangGraph 的可替换前端。

源码：[`backend/domain/workflow/internal/compose/workflow.go::NewWorkflow / Workflow.Compile`](https://github.com/coze-dev/coze-studio/blob/fefb05ff27be1da939612fbf9faf5db62583b8ae/backend/domain/workflow/internal/compose/workflow.go#L83)。

恢复准备阶段 `WorkflowRunner.Prepare` 对新执行生成 ID，对恢复执行读取首个 interrupt 并核对 event ID，再根据节点路径构建 Eino state modifier。它说明“审批回复”和“普通新输入”需要明确区分；不能把其 resume 对象直接传给 LangGraph。

源码：[`backend/domain/workflow/internal/compose/workflow_run.go::WorkflowRunner.Prepare`](https://github.com/coze-dev/coze-studio/blob/fefb05ff27be1da939612fbf9faf5db62583b8ae/backend/domain/workflow/internal/compose/workflow_run.go#L107)。

交叉核对读了 [`canvas_test.go::TestPruneIsolatedNodes`](https://github.com/coze-dev/coze-studio/blob/fefb05ff27be1da939612fbf9faf5db62583b8ae/backend/domain/workflow/internal/canvas/adaptor/canvas_test.go#L842)：fixture 中指定的孤立节点应被去除。没有运行测试，也没有用它证明本平台所有 DAG 校验正确。

### 对本项目的意义

- 可复用：画布→执行 schema 的分层方法、端口规范化、类型映射、执行与恢复准备的边界、相关负面用例设计。
- 不能直接替代：当前 LangGraph 整图执行器和平台请求/Run/副作用协议。其 Go `internal` 包及大量领域类型也不是独立稳定 SDK。
- 整仓 fork 的真实取舍：若全面接受 Eino、React 和它的领域/存储模型，能减少从零搭平台功能的工作；若仍坚持 Vue Flow/LangGraph，替换这些核心路径将形成较大的长期分叉。当前没有完成移植实验，不能声称某方案一定更快。

## 4. Dify：成熟能力参考，需尊重其栈与附加许可

### 已读事实

- [`api/pyproject.toml`](https://github.com/langgenius/dify/blob/4c521a3c0a1f60607c8a7bcf90792f34adcf18d0/api/pyproject.toml) 所读源码版本为 1.18.0、Python `~=3.13.0`，包含 Flask、Celery 与 `graphon`。graphon 还配置了固定 Git revision 的 override。不能把这组源码当成我们现有 Python 3.11 兼容线的已测依赖组合。
- [`web/package.json`](https://github.com/langgenius/dify/blob/4c521a3c0a1f60607c8a7bcf90792f34adcf18d0/web/package.json) 使用 React、Next、reactflow 和 workspace UI/工具包。不是 Vue 组件。
- [`docker/docker-compose.yaml`](https://github.com/langgenius/dify/blob/4c521a3c0a1f60607c8a7bcf90792f34adcf18d0/docker/docker-compose.yaml) 定义 API、worker、Web、数据库、Redis、sandbox、plugin daemon 等服务和额外 profile；具体启用集合依配置而定。
- [`LICENSE`](https://github.com/langgenius/dify/blob/4c521a3c0a1f60607c8a7bcf90792f34adcf18d0/LICENSE) 在 Apache 2.0 基础上增加条件，涉及未经书面授权的多租户运营，以及使用其前端时品牌/版权信息的保留。其 tenant 定义与 workspace 相关；不能把“我们的平台目前单企业”自动等同于所有未来工作空间/分发方式均已满足许可条件。

### 核心源码与测试

`WorkflowAppRunner.prepare` 对恢复状态与全新状态分支处理：恢复使用已有 RuntimeState/VariablePool，新任务创建系统变量池，再构建 graph；随后组合 Redis/停止标记/Celery 退出信号通道，创建 WorkflowEntry 并携带 workflow version。这与平台数据模型、队列、控制通道和图引擎有具体耦合，不是导出一个 Python 函数即可替换我们 worker 的模块。

源码：[`api/core/app/apps/workflow/app_runner.py::WorkflowAppRunner.prepare`](https://github.com/langgenius/dify/blob/4c521a3c0a1f60607c8a7bcf90792f34adcf18d0/api/core/app/apps/workflow/app_runner.py#L74)。

读了 [`test_parallel_human_input_join_resume.py::test_parallel_human_input_join_completes_after_second_resume`](https://github.com/langgenius/dify/blob/4c521a3c0a1f60607c8a7bcf90792f34adcf18d0/api/tests/e2e_tests/contracts/core/workflow/graph_engine/test_parallel_human_input_join_resume.py#L282)：两个并行人工输入先暂停，一支恢复后仍暂停，第二次恢复才在 join 后成功；同时断言表单文件对象和输出保留。这里使用测试内 pause store，并不证明跨进程数据库故障恢复。

### 对本项目的意义

可参考系统变量初始化、运行控制通道、并行人工输入/汇合测试、发布版本及 UI 交互；保持原许可边界，不把带附加许可的代码直接混入并宣称为本项目自有的宽松许可实现。当前建议不整仓 fork；若用户接受其原栈及品牌/授权边界，它才构成另一个完整产品底座选项，而非当前方案的无成本组件。

## 5. Flowise：有独立编辑组件，但与 Vue Flow 不是同一套 UI

### 已读事实

- [`packages/server/package.json`](https://github.com/FlowiseAI/Flowise/blob/9291856d1ea4a4ceea9f8fef8ce14f4f6c81e8eb/packages/server/package.json) 所读版本 3.1.4，声明 Node `^24`，依赖 Express、TypeORM、BullMQ、workspace components 等。
- [`packages/ui/package.json`](https://github.com/FlowiseAI/Flowise/blob/9291856d1ea4a4ceea9f8fef8ce14f4f6c81e8eb/packages/ui/package.json) 为 React/ReactFlow/MUI/Vite 体系。它不能直接安装成 Vue Flow 节点面板。
- [`LICENSE.md`](https://github.com/FlowiseAI/Flowise/blob/9291856d1ea4a4ceea9f8fef8ce14f4f6c81e8eb/LICENSE.md) 将 enterprise 目录及指定文件区分为商业许可，其余符合条件部分为 Apache 2.0；[`enterprise/LICENSE.md`](https://github.com/FlowiseAI/Flowise/blob/9291856d1ea4a4ceea9f8fef8ce14f4f6c81e8eb/packages/server/src/enterprise/LICENSE.md) 对生产使用与分发给出额外条件。不能把整个 monorepo 一概标为 Apache。
- [`IdentityManager.ts`](https://github.com/FlowiseAI/Flowise/blob/9291856d1ea4a4ceea9f8fef8ce14f4f6c81e8eb/packages/server/src/IdentityManager.ts#L1) 有明确许可说明，并引用 enterprise 身份/权限实现。这里不推断开源发行版无法运行，只提醒复制和拆出模块时需要逐文件核查。

### 执行器与组件边界

`executeAgentFlow` 从持久化 chatflow.flowData 解析 ReactFlow nodes/edges，构造正反向图，准备 history、override、执行及工作空间等上下文；`hasReceivedRequiredInputs` 对普通依赖要求全到达，对条件组要求每组至少一个来源到达。该行为可用于理解“分支不运行”和“缺失依赖”不能等同，但不是我们 `all_active` 规则已验证等价的证据。

源码：[`buildAgentflow.ts::executeAgentFlow / hasReceivedRequiredInputs`](https://github.com/FlowiseAI/Flowise/blob/9291856d1ea4a4ceea9f8fef8ce14f4f6c81e8eb/packages/server/src/utils/buildAgentflow.ts#L1541)。

值得注意的是新增独立包 [`packages/agentflow/package.json`](https://github.com/FlowiseAI/Flowise/blob/9291856d1ea4a4ceea9f8fef8ce14f4f6c81e8eb/packages/agentflow/package.json)：包名 `@flowiseai/agentflow`，所读版本 `0.0.0-dev.14`，package 声明 Apache 2.0，但 peer dependencies 为 React、ReactDOM、ReactFlow、MUI/Emotion。本轮没有核实 npm 已发布状态或 API 稳定性。

这为接受 React 的产品提供了“只嵌编辑器”的候选；在我们已选 Vue Flow 的前提下，直接使用会增加双 UI 运行时与状态/样式/事件适配。尚无证据证明这种桥接比自建必要节点面板更省事。

### 值得吸收的具体测试

`generateExportFlowData` 用字段 allowlist 构造导出数据，去掉 credential 引用、password/file/folder 输入及运行时状态。对应测试检查敏感字段剔除及必要字段保留。我们可以参考这种“导出按白名单”方法，并在自己的 DSL/布局导出中加等价负例；不能把单个库的过滤器当成服务端授权。

源码与测试：[`flowExport.ts::generateExportFlowData`](https://github.com/FlowiseAI/Flowise/blob/9291856d1ea4a4ceea9f8fef8ce14f4f6c81e8eb/packages/agentflow/src/core/utils/flowExport.ts)、[`flowExport.test.ts`](https://github.com/FlowiseAI/Flowise/blob/9291856d1ea4a4ceea9f8fef8ce14f4f6c81e8eb/packages/agentflow/src/core/utils/flowExport.test.ts)。另读 [`VariablePicker.test.tsx`](https://github.com/FlowiseAI/Flowise/blob/9291856d1ea4a4ceea9f8fef8ce14f4f6c81e8eb/packages/agentflow/src/atoms/VariablePicker.test.tsx)，覆盖类别分组、空状态、禁用和选择回调，适合作为业务用户变量选择器的测试设计参考。均未运行。

## 6. Vue Flow：继续直接依赖，执行语义放在平台

[`packages/core/package.json`](https://github.com/bcakmakoglu/vue-flow/blob/17953c329db2d5dc5f5097370cbf5e1173bab520/packages/core/package.json) 声明 `@vue-flow/core` 1.48.2、Vue peer `^3.3.0`；[`LICENSE`](https://github.com/bcakmakoglu/vue-flow/blob/17953c329db2d5dc5f5097370cbf5e1173bab520/LICENSE) 为 MIT。

`useActions` 内 `toObject` 去掉拖动/选中/尺寸等内部状态，再序列化 nodes、edges、viewport；`FlowExportObject` 对应这些编辑数据。它不携带我们发布所需的审批绑定、工具制品、依赖闭包和后端授权。

源码：[`store/actions.ts::toObject`](https://github.com/bcakmakoglu/vue-flow/blob/17953c329db2d5dc5f5097370cbf5e1173bab520/packages/core/src/store/actions.ts#L783)、[`types/flow.ts::FlowExportObject`](https://github.com/bcakmakoglu/vue-flow/blob/17953c329db2d5dc5f5097370cbf5e1173bab520/packages/core/src/types/flow.ts#L123)。

因此复用的是节点/边渲染、交互与编辑状态基础；本项目实现执行 DSL、变量类型、合法连接、发布校验、版本和运行轨迹映射。当前不需要维护 Vue Flow 核心 fork。

## 7. 阅读边界、下一步及可推翻证据

### 补充：Vue 3 基础 UI 推荐 Element Plus

官方仓库 [element-plus/element-plus](https://github.com/element-plus/element-plus)，本轮 HEAD `a8d102a91b96f52c433d0d3ef7c91c565a9aa317`，commit 日期 2026-10-30。已读 [`LICENSE`](https://github.com/element-plus/element-plus/blob/a8d102a91b96f52c433d0d3ef7c91c565a9aa317/LICENSE) 为 MIT；[`packages/element-plus/package.json`](https://github.com/element-plus/element-plus/blob/a8d102a91b96f52c433d0d3ef7c91c565a9aa317/packages/element-plus/package.json) 声明 Vue peer `^3.3.7`，源码包 version 为开发占位 `0.0.0-dev.1`，不能把它当作推荐安装的发行版。生产版本在依赖解析阶段选择稳定发行版并锁定。

[`packages/element-plus/index.ts`](https://github.com/element-plus/element-plus/blob/a8d102a91b96f52c433d0d3ef7c91c565a9aa317/packages/element-plus/index.ts) 导出 installer 与组件，组件入口 [`packages/components/index.ts`](https://github.com/element-plus/element-plus/blob/a8d102a91b96f52c433d0d3ef7c91c565a9aa317/packages/components/index.ts) 包含 form/table/dialog/upload 等。可直接依赖其表单、表格、弹窗等控件，在 Vue Flow 之外承担管理台和节点配置 UI；不必 fork 整套后台系统。我们仍实现节点业务配置、类型化变量选择、审批内容呈现和服务端校验。该建议是可逆的 UI 默认选项，未进行主题、无障碍或交互实测，不把它写成用户已经批准的新增产品决策。

已覆盖：四仓当前 metadata/HEAD；上述固定文件；Coze 画布转换/编译/恢复准备部分；Dify 运行准备及并行人工输入测试；Flowise 执行器关键段、独立编辑包及导出/变量选择测试；Vue Flow 序列化实现。未覆盖：各平台完整 RBAC/多租户隔离、生产恢复保证、全部插件许可、真实部署资源、编辑器可用性及移植成本实验。

最便宜验证：用我们的销售 DSL 和一条“并行两次审批→汇合”流程，记录各参考平台如何表达、导出和恢复；比较需要重写的边界，而非只比较节点数量。优先读 Coze 作为 Go 参考；不要求为每个参考平台都下载整仓或引入生产依赖。

在保持既定 Go/Vue Flow/LangGraph 前提下，本轮不产生必须再次让用户回答的产品问题。若用户希望改成“直接基于成熟整平台二次开发”，才需要重新决定是否接受其前端、执行引擎、数据模型、部署拓扑和许可边界，不能只更换仓库地址。

**本轮消除的不确定性：** 热门整平台是否是当前方案可直接嵌入的组件；答案是需要区分整平台、独立组件和设计参考，整仓复用会引入具体架构约束。**可能推翻当前建议的证据：** 一个小规模移植实验显示，保留本项目权限/版本/审批语义仍可通过稳定扩展点接入，而不必替换大量核心代码；或用户明确接受上游技术栈并将最快交付完整功能放在当前栈约束之前。
