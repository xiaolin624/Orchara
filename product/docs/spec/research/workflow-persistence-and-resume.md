# 动态画布工作流：持久化与恢复语义

查询日期：2026-10-31。只读官方文档和本地固定源码；未运行工作流、迁移或审批实验。源码使用现有 checkout commit `e5c961d7fbe7ca6fdd4c94c84d90511372514d55`，不声称它是查询时最新 HEAD。文档为查询时官方站点。

## 持久化究竟保存什么

官方区分 checkpointer（thread 的图状态）与 store（跨 thread 的应用数据）。内存 checkpointer 不能跨进程重启保留数据。[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

| 标识 | 已核实含义 | 不应赋予的含义 |
|---|---|---|
| `thread_id` | 读写一条检查点历史的主要定位键；恢复中断须与原 thread 相同 | 不是调用者身份、权限或自动图版本 |
| `checkpoint_ns` | 同一 thread 内区分根图/子图；根为 `""`，嵌套 namespace 有 node/task 路径 | 不是企业/用户安全隔离或业务发布版本的替代品 |
| `checkpoint_id` | 选定具体状态快照；不提供则通常取该 thread/namespace 最新 | 不是 graph definition、Python runtime 或脚本版本 |

[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)说明 super-step 完成后保存状态快照，并持久化 step 内各任务的完成写入以避免恢复时重算已成功节点。检查点不是任意指令位置的进程内存快照。

源码证据：[`Checkpoint`/`CheckpointTuple`](https://github.com/langchain-ai/langgraph/blob/e5c961d7fbe7ca6fdd4c94c84d90511372514d55/libs/checkpoint/langgraph/checkpoint/base/__init__.py)保存 channel values/versions、各节点已见版本和相关元数据；不包含业务画布定义、可执行 Python 函数和完整依赖环境。格式中的 `v` 是检查点格式版本，不能混同本平台的应用发布版本。

[`Pregel.get_state`](https://github.com/langchain-ai/langgraph/blob/e5c961d7fbe7ca6fdd4c94c84d90511372514d55/libs/langgraph/langgraph/pregel/main.py#L1460)从 config 获取 thread ID 并调用 saver；[`PostgresSaver.get_tuple`](https://github.com/langchain-ai/langgraph/blob/e5c961d7fbe7ca6fdd4c94c84d90511372514d55/libs/checkpoint-postgres/langgraph/checkpoint/postgres/__init__.py#L197)实际 SQL 以 `thread_id + checkpoint_ns [+ checkpoint_id]` 查询。[表主键定义](https://github.com/langchain-ai/langgraph/blob/e5c961d7fbe7ca6fdd4c94c84d90511372514d55/libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py)同样使用这些存储键。

**授权推论**：以上 raw checkpointer 接口接受定位键，不认证 Web 调用者。平台应在读取历史、下载状态/文件、更新状态、resume、取消、复制或删除前，先根据已认证用户校验对 conversation/run 的权限，再由服务端解析检查点键。不可把 UUID 难猜、`user_id` metadata 或用户传入 namespace 当授权。该结论不表示托管 Agent Server 或外部数据库 RLS 没有额外鉴权，只表示本项目调用 raw 库时不能从这些键获得鉴权。

## 发布版与旧运行恢复

官方 [Backward compatibility](https://docs.langchain.com/oss/python/langgraph/backward-compatibility)明确：默认以当前部署的图代码执行既有 thread 的状态，LangGraph 不自动将运行锁定到启动时的代码版本。画布发布版固定是平台自己的职责。

官方 [Graph migrations](https://docs.langchain.com/oss/python/langgraph/graph-api#graph-migrations)说明：已完成 thread 可更改拓扑；中断 thread 的待执行节点若被改名或删除可能无法恢复；state 字段改名会失去旧保存值，不兼容类型或新增无默认必填值可能破坏旧状态。即便技术上能加载，新边/工具/提示词仍可能改变业务意义。

由这些语义得到的实现约束（不替用户选择迁移政策）：

- 运行记录需要绑定已发布不可变图定义和必要制品；恢复时先解析该绑定，再加载相同定义。只在 checkpoint metadata 放一个版本标签、实际却执行最新函数，不能达到固定行为。
- 节点稳定 ID 与可编辑显示名分开；持久化状态应能识别节点和 state schema 的版本。
- 脚本内容/依赖镜像、Agent 配置/工具契约、可执行编译器/runtime 兼容版本需要可取回，不能仅保留一份后来被覆盖的文件路径。模型服务漂移等外部依赖不能仅凭本地版本号保证完全重现。
- 同一根 namespace 复用同一 thread ID 会读取其既有状态。会话、应用运行与图检查点的映射必须明确，避免不同画布或发布版误复用状态。
- 若确需让旧运行迁移到新版，应有可核查的 state/node 映射及恢复验证；继续旧版、排空旧运行、显式迁移或终止属于产品政策，本文不代选。

## 审批中断与重放

官方 [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)与实际 [`interrupt`](https://github.com/langchain-ai/langgraph/blob/e5c961d7fbe7ca6fdd4c94c84d90511372514d55/libs/langgraph/langgraph/types.py#L903)一致：首次调用抛可恢复中断；客户端用同一 thread 的 `Command(resume=...)` 提供回应；恢复会从该节点函数开头重执行，中断前代码会再次运行。多个中断的回应按任务内调用顺序匹配，因此不能随意在旧恢复点之前添加、删除或调换 interrupt/task 调用。

对平台审批节点的含义：

- 把审批请求和后续动作的准确参数、发布版本、运行/节点/中断身份保存为可验证记录；不能将审批页面展示的旧动作批准后换成新草稿动作。
- `resume` 只提供恢复值，不验证回应者是否有审批权；Web 审批端仍需自己的身份、待办状态和并发/重复提交校验。当前 typed `response_schema` 只能约束数据结构，也不等于授权。
- 中断前不可重复副作用在 resume 时可能重复；即使把副作用移到审批后，若提交后节点失败重试仍可能重复。需要具体外部工具的幂等/对账契约，不能从 checkpoint 推导 exactly-once。
- 审批等待期间不要求 Python 线程一直存活，真正依赖持久检查点与可取回的图制品。待办过期/撤销/保留多久由产品另定。

## DeepAgents 节点/嵌套子图

官方 [Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)区分：

- 默认 `checkpointer=None`：继承父级并按调用隔离；可支持中断，但不自动跨多次调用累积该子图自己的会话记忆。
- `checkpointer=True`：per-thread 子图状态跨调用累积；相同实例并行调用可产生检查点冲突。
- `checkpointer=False`：无检查点恢复/中断能力；失败后从头再跑。

子图直接作为图节点时可获得基于节点名的 namespace；在一个节点内动态依次调用多个子图时，调用顺序会参与 namespace 分配，重排可能让子图读错状态。图中稳定节点 ID/版本绑定因此需要覆盖内部 DeepAgents graph 的适配层。父节点以函数调用子图且子图中断时，父节点也从该调用所在节点开头重跑。

用户已确认同一 conversation 串行，这减少入口并发，却不能自动消除一个 Agent 内部并行调用相同 per-thread 子图的冲突。是否允许这种调用、为每次调用分离状态或串行化，须在具体 Agent 节点契约中明确。

## 待验证而非已证明

需要实现后的恢复用例至少覆盖：进程重启后的审批恢复；发布新版后旧版待办恢复；重复/过期审批；不同用户访问相同定位 ID；同一应用的不同运行隔离；嵌套中断；副作用提交后客户端失败；以及旧 state schema 兼容/拒绝。这里没有运行这些用例，也未据此承诺平台已具备版本安全恢复或数据隔离。
