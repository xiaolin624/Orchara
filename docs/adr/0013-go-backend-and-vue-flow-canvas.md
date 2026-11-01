---
status: accepted
---

# Go 后端与 Vue Flow 画布

用户明确选择 Go 后端和 bcakmakoglu/vue-flow，替代之前推荐的 Python 主后端和 React 画布。Vue Flow 的 Vue 3 依赖、节点/连线与保存恢复接口已静态核查。Go 与 Python 执行层如何分工仍待确认；该决策不自动意味着已选择 Go 工作流引擎，或已放弃 LangGraph/DeepAgents。

画布组件提供编辑与图形交互。平台仍需自己的工作流定义、服务端校验、发布和执行契约；不能将包含界面状态的 Vue Flow 图对象直接视为可信可执行定义。

后续已确认执行边界：Go 负责平台服务和任务管理，Python worker 负责 LangGraph 整图执行及 DeepAgents 节点，前端使用 Vue 3 与 Vue Flow。该方案不把 LangGraph 内部检查点误当作 Go 平台完整授权和业务状态的替代品；双方状态与事件职责须在接口方案中明确。
