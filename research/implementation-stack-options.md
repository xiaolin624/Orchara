# 实现栈核查：Go / Vue Flow 与 Python Agent 执行边界

> 当前用户已选：Go 后端、Vue 3 + `bcakmakoglu/vue-flow` 画布。下文 React Flow / FastAPI 是先前研究的历史候选，**不代表当前选型**。Python 是否负责整个工作流，还是只执行 Agent 节点，尚待选择；两种结构的差异见新增第 5–7 节。

查询日期：2026-10-31。范围是候选依赖的真实接口与集成约束，不替用户决定产品政策。本轮只读官方文档及源码，没有安装、运行或验证端到端兼容性。LangGraph 使用已有本地 clone 的固定 commit；不将它标为当前远端最新版本。

## 1. 历史候选 React Flow：免费 MIT 核心可用于自建画布

**事实。** `xyflow/xyflow` commit `d4bc936edad07aba995dbdb8e2c63af8cdd631c9` 的 `packages/react/package.json` 表明包名是 `@xyflow/react`、版本 `12.12.0`、license 为 MIT、公开发布，React/react-dom peer dependency 为 `>=17`。根 LICENSE 是 MIT，分发须保留版权和许可声明。[固定 package.json](https://github.com/xyflow/xyflow/blob/d4bc936edad07aba995dbdb8e2c63af8cdd631c9/packages/react/package.json)、[固定 LICENSE](https://github.com/xyflow/xyflow/blob/d4bc936edad07aba995dbdb8e2c63af8cdd631c9/LICENSE)。

**事实。** 官方 [React Flow Pro](https://reactflow.dev/pro) 将高级示例、模板、支持等列为订阅内容，同时说明 React Flow 保持 MIT 开源。使用核心库、自建节点/连线 UI 不要求购买 Pro。付费模板的分发条件应另查，不能自动套用核心库 MIT。

**集成推断。** 画布节点和连线是编辑器表达；发布版本 schema、服务端 DAG 校验、权限、图编译与节点执行契约仍应由平台明确。前端能否连线不能充当服务端授权/合法性校验。这里不是声称库存在缺陷，而是在区分 UI 依赖与本平台运行语义。

## 2. Mem0 OSS Python：可复用 PostgreSQL + pgvector，接口有同步边界

**事实。** `mem0ai/mem0` commit `753496cf1435d76c3a711f3af51303f7fdcb6420` 的 `PGVectorConfig` 暴露 `collection_name`、`embedding_model_dims`、`connection_string`、`connection_pool`、`sslmode`、`minconn/maxconn`、`hnsw/diskann` 等字段。官方 Python 示例通过 `Memory.from_config({"vector_store":{"provider":"pgvector","config":...}})` 使用它；Python 配置是 snake_case，不能直接复制 TS 的 camelCase 配置。[固定配置类型](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/mem0/configs/vector_stores/pgvector.py)、[固定官方 pgvector 文档](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/docs/components/vectordbs/dbs/pgvector.mdx)。

**事实。** 同 commit 的 `PGVector.__init__` 使用 psycopg3 `ConnectionPool`，或降级为 psycopg2 `ThreadedConnectionPool`；`_get_cursor` 是同步 contextmanager。`create_col` 执行 `CREATE EXTENSION IF NOT EXISTS vector`，创建 `id UUID / vector vector(dims) / payload JSONB` 表和索引；DiskANN 还涉及 vectorscale 扩展。不能将这个 adapter 当作原生 async PostgreSQL 驱动。[固定实现：PGVector / _get_cursor / create_col](https://github.com/mem0ai/mem0/blob/753496cf1435d76c3a711f3af51303f7fdcb6420/mem0/vector_stores/pgvector.py)。

**集成约束。** 在 FastAPI async 路径直接调用此同步 adapter 会有阻塞风险；需要在实际调用链确定线程池/worker/异步包装边界，不能仅因上层有 async 方法就推定全部 I/O 原生异步。初始化角色需要建扩展/建表能力，或将预建表与迁移纳入部署。嵌入模型维数必须与 collection 维数相符，换模型应有显式重建/迁移处理。已读配置中没有独立 `schema` 字段，暂不提出未经核实的 schema 配置写法。

**边界。** 向量库存储、跨会话语义记忆、LangGraph 会话 checkpoint 是不同模型。`user_id` 等筛选参数不能替代平台用户授权；从可信登录上下文产生 scope，而非照单接受客户端输入。本轮未追踪 Memory 的完整抽取/更新调用链，也没有证明它相对简单自建记忆在本产品内有必要。

## 3. LangGraph：Postgres checkpoint 有独立 async 实现

**事实。** 官方 [Memory / PostgreSQL checkpointer 文档](https://docs.langchain.com/oss/python/langgraph/add-memory) 给出 `langgraph-checkpoint-postgres` + `psycopg[binary,pool]` 安装方式，以及 `AsyncPostgresSaver.from_conn_string(...)`、`await checkpointer.setup()` 和 `graph.astream_events(...)` 的 async 示例。首次使用需要 setup。

**固定源码。** 已读本地 commit `e5c961d7fbe7ca6fdd4c94c84d90511372514d55` 的 `AsyncPostgresSaver.__init__`、`from_conn_string`、`setup`：构造器支持内部定义的 async connection/pool；`from_conn_string` 创建 `AsyncConnection`，开启 `autocommit=True`、`prepare_threshold=0`、`row_factory=dict_row`；`setup` 创建表并逐项执行 migrations。pool 与单连接 pipeline 同时传入会抛 ValueError。[固定 aio.py](https://github.com/langchain-ai/langgraph/blob/e5c961d7fbe7ca6fdd4c94c84d90511372514d55/libs/checkpoint-postgres/langgraph/checkpoint/postgres/aio.py)。

**集成约束。** 连接/池生命周期应与服务或 worker 生命周期一致，而不是退出 context 后继续使用 saver；自建 pool 时须核对连接参数及库版本。API 层、执行 worker、Mem0 各自连接池的总量应计算。共享一个 PG 实例是部署选项，不等于三者共用表、权限或数据生命周期。

**不能省略。** checkpoint 不等于运行任务队列，也不负责发布版本归属和访问授权；旧图恢复及审批 replay 规则见同目录 [workflow-persistence-and-resume.md](workflow-persistence-and-resume.md)。

## 4. 历史候选 FastAPI BackgroundTasks：响应后执行，不是长运行任务持久化方案

**事实。** [FastAPI Background Tasks 官方文档](https://fastapi.tiangolo.com/tutorial/background-tasks/) 说明任务在响应发送后执行；对于较重且不必与 Web 应用在同一进程执行的工作，官方建议考虑 Celery 等依赖 RabbitMQ/Redis、可跨进程/服务器工作的工具。这是选项示例，不是要求本项目必须引入 Celery。

**集成推断。** 长 Agent/脚本运行若要在 API 重启后恢复，不能仅依靠 Web 进程内 BackgroundTasks；应单独定义持久 run 记录、执行者领取/租约、重试、取消和 checkpoint 的关系。独立 worker 可以是 Compose 内另一服务，不必因此引入 Kubernetes。Redis 的存在本身不提供这些语义；具体队列机制尚未选定，本轮未验证其可靠性。

## 5. 当前 Vue Flow：序列化的是编辑图，需另建执行契约

固定 commit：`17953c329db2d5dc5f5097370cbf5e1173bab520`。

**事实。** `FlowExportObject` 包含 `nodes`、`edges`、`viewport` 以及已废弃的 position/zoom 字段。`store/actions.ts::toObject` 移除拖动/选中/尺寸等内部状态，再 JSON stringify/parse 返回这些数据。[固定 FlowExportObject](https://github.com/bcakmakoglu/vue-flow/blob/17953c329db2d5dc5f5097370cbf5e1173bab520/packages/core/src/types/flow.ts#L123)、[固定 toObject](https://github.com/bcakmakoglu/vue-flow/blob/17953c329db2d5dc5f5097370cbf5e1173bab520/packages/core/src/store/actions.ts#L783)。

**边界推断。** 这是通用节点/连线/视口的保存格式，不自带本产品的端口数据类型、Agent 配置版本、脚本版本、输入绑定、审批语义和运行权限。不能把任意导入 JSON 直接作为可信可执行工作流。应从画布数据生成受版本管理的执行定义，并在服务端按允许的节点类型和引用做校验；这不是要求把 UI 库 fork 成引擎。

## 6. Go 与 LangGraph/DeepAgents 的两种边界

**接口事实。** LangGraph 是可嵌入 Python 库：`StateGraph.compile` 返回可 invoke/stream/async 调用的 `CompiledStateGraph`，可绑定 checkpointer；并非只能通过托管云服务使用。[固定 StateGraph.compile](https://github.com/langchain-ai/langgraph/blob/e5c961d7fbe7ca6fdd4c94c84d90511372514d55/libs/langgraph/langgraph/graph/state.py#L1177)、[固定 Pregel.invoke / ainvoke / astream](https://github.com/langchain-ai/langgraph/blob/e5c961d7fbe7ca6fdd4c94c84d90511372514d55/libs/langgraph/langgraph/pregel/main.py#L3156)。节点是函数、状态需要 schema/reducer、图需编译；Vue Flow 的导出对象并不自动满足这些条件。[官方 Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)。

| 选项 | Go 的边界 | Python 的边界 | 必须明确的代价 |
| --- | --- | --- | --- |
| A：Go 控制面 + Python 整图 worker | 用户/权限、应用与发布版本、请求鉴权、run 管理、事件转发；执行任务交给 Python | 将已校验发布定义构造成 LangGraph，调度全图，DeepAgents 执行 Agent 节点，整图 checkpoint/interrupt/resume | 两门语言与跨服务协议；Go run 状态与 Python 图状态必须定义权威来源及对账规则 |
| B：Go 工作流引擎 + Python Agent 节点服务 | 画布 DAG 调度、节点状态、依赖/汇合、输入输出、流程级暂停/恢复/重试 | 仅执行一个 Agent 节点及其内部子 Agent/工具调用；保留该 Agent 的内部 checkpoint | Go 必须实现流程引擎语义；Python Agent checkpoint 不能自动充当整个 Go 工作流的 checkpoint |

两者都是架构选项，**本轮未选择其一**。B 仍包含 Python Agent 服务，因此“Go 编排”不等于“全系统纯 Go”。不应同时让 Go 与 Python 都调度同一张顶层 DAG，再含糊地同步状态。

**恢复边界事实。** 官方 [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) 要求用同一 thread_id 和 `Command(resume=...)` 恢复；节点会从开头重新执行，interrupt 之前的代码会再次运行。若 Go 包装 Python 服务，RPC 协议必须区分普通新输入、审批恢复、查询、取消；审批不应被简单映射为“重试 Agent 请求”。业务 run/节点 ID、发布版本与 Python thread_id 的绑定及授权是平台责任。重放/副作用细节见前述 persistence 报告。

## 7. Asynq 是候选任务队列，不等于 DAG 引擎或强制终止机制

固定 `hibiken/asynq` HEAD：`83e0966ab47a0ea99f66094afe4a32bbfab80747`（2026-10-19）。[官方 API 文档](https://pkg.go.dev/github.com/hibiken/asynq) 说明 Redis 任务分发、失败重试、worker crash recovery、超时/截止时间；它提供的至少一次执行意味着 handler/外部副作用应允许重复，队列去重不等于业务副作用 exactly-once。

**取消源码事实。** `Inspector.CancelProcessing` 仅调用 `PublishCancelation`，注释明确是 best-effort，返回 nil 只代表信号发出，不保证任务已停。[固定 inspector.go](https://github.com/hibiken/asynq/blob/83e0966ab47a0ea99f66094afe4a32bbfab80747/inspector.go#L760)。`processor` 创建 task context 并在 goroutine 执行 handler；`ctx.Done()` 走 `handleFailedMessage`，默认失败路径可能再次重试。只有适当区分永久取消、暂时故障与重试策略，才能形成产品级取消行为。[固定 processor.go：exec / handleFailedMessage](https://github.com/hibiken/asynq/blob/83e0966ab47a0ea99f66094afe4a32bbfab80747/processor.go#L205)。

**集成推断。** handler 应尊重 context；若它通过 HTTP 调用 Python，取消 Go context 不能被当作“Python 已停止”，需要明确下游取消/终止确认机制。已产生的外部副作用不会被队列自动回滚。`SkipRetry` 可将失败归档、`RevokeTask` 可避免重试/归档，但不替代平台用户取消状态与 Python/sandbox 实际执行状态。Asynq 可用于 A 的任务投递或 B 的节点任务，不能仅靠它推导依赖、分支汇合和审批状态。

## 历史候选阶段的影响（以第 5–7 节的新边界为准）

- 可以先用 React Flow 核心实现画布，无需先购买 Pro 或 fork 编辑器。
- Python + LangGraph + PostgreSQL 的 async checkpoint 路径已有现成基础；仍需锁定完整依赖版本并做故障恢复实验。
- Mem0 pgvector 能减少额外向量数据库，但它是可选的语义记忆依赖，不是必须先接入的会话持久化层；需要根据真实记忆场景决定是否引入。
- FastAPI 负责 HTTP/API 与运行编排接口；长任务的持久执行语义应显式设计。脚本 sandbox 是另一边界，沿用已调查的 OpenSandbox/gVisor 选项，不能由 task queue 或 Python 进程代替。

**本轮消除的不确定性：** 这些候选组件是否具备所需基础接口、是否需要付费核心、是否全链路原生 async。**可能推翻当前可行性判断的证据：** 实际锁版后依赖冲突、私有环境模型/嵌入调用不可用、预期并发下连接池或同步路径成为瓶颈、重启/恢复实验显示状态与外部副作用不一致。以上均尚未执行，不写成已验证结果。
