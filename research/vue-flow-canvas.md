# Vue Flow 画布组件核查

查询日期：2026-10-31。用户指定 https://github.com/bcakmakoglu/vue-flow 。GitHub MCP 查询 6926 Stars，MIT；读取 commit `17953c329db2d5dc5f5097370cbf5e1173bab520`，该 commit 日期 2026-06-23。仅静态阅读，无安装/运行/兼容性实验。

- [`packages/core/package.json`](https://github.com/bcakmakoglu/vue-flow/blob/17953c329db2d5dc5f5097370cbf5e1173bab520/packages/core/package.json)：包名 `@vue-flow/core`，源码声明版本 `1.48.2`，MIT，peer dependency `vue: ^3.3.0`。此为所读源码版本，未核验 npm latest。
- [`types/node.ts::Node`](https://github.com/bcakmakoglu/vue-flow/blob/17953c329db2d5dc5f5097370cbf5e1173bab520/packages/core/src/types/node.ts)：节点包含 id、position、type、data 及界面交互/样式字段；类型允许组件等界面值，不能假定任意前端对象都可作为后端执行配置。
- [`types/flow.ts::FlowExportObject`](https://github.com/bcakmakoglu/vue-flow/blob/17953c329db2d5dc5f5097370cbf5e1173bab520/packages/core/src/types/flow.ts)：导出节点、连线及视图状态，用于图形保存恢复。
- [`save-restore/Controls.vue`](https://github.com/bcakmakoglu/vue-flow/blob/17953c329db2d5dc5f5097370cbf5e1173bab520/docs/examples/save-restore/Controls.vue)：示例通过 `useVueFlow().toObject()/fromObject()` 与 localStorage 保存恢复画布。

设计推论：Vue Flow 可作为创作界面的基础；平台自己的工作流定义应采用明确 schema，仅接收允许的节点类型、参数及引用，分离布局与执行语义，并在服务端验证类型、权限、版本、DAG 和资源限制。保存恢复画布不等于具备任务调度、持久化恢复或安全执行。这些属于本平台待实现部分。

用户新选择已取代此前 research/implementation-stack-options.md 中 React Flow 的候选位置；后者保留为历史事实核查，不应误认为仍选择 React。
