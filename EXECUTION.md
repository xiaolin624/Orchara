# 执行、恢复与副作用协议

## 1. 一次真实任务的调用链

1. 用户经 Go 上传文件并获得 artifact ID，文件经过扫描、大小和格式检查。提交需求携带此 ID、会话 ID、幂等键。
2. Go 锁会话/请求行，确认请求顺序。授权检索候选能力卡，模型给建议；服务端校验候选成员、权限与必填输入。必要时创建澄清 interaction。
3. 输入齐全后，在一个事务中固定 application_version、依赖闭包、授权快照和预算，创建 Run、job、事件。授权快照用于追溯，执行仍复查当前撤销状态。
4. Python supervisor 为一次领取预分配执行单元标识，经内部 API 领取 job。Go 用 `FOR UPDATE SKIP LOCKED` 选择 ready job，原子占用配额并发放 attempt_token；领取失败不能启动图。进程未成功启动时由 supervisor 提交未启动证据，按保守恢复处理，不伪造执行失败后无限重投。
5. 每次尝试在独立受监督进程中加载指定 compiler/worker image 和版本制品；LangGraph thread 为该 Run 分配的随机 ID，不能用用户可控字符串。
6. 输入映射通过类型校验后执行脚本。沙箱只得到本次授权文件及能力 token。验证output.json协议、生成文件和结构化输出（stdout/stderr仅日志），再登记产物。
7. DeepAgents 节点使用工具白名单、必要统计结果及受限上下文调用模型；模型访问经能力代理，记录调用回执与实际用量。
8. worker提交结果引用、检查点位置和状态建议，Go先保存result提议并显示finalizing。supervisor确认退出/沙箱/数据库写排空、子任务结束且无未知操作后，Go同一finalizer事务提交终态/结果事件、完成job并释放配额/会话槽。结果到达和退出证据可任意先后，均须幂等；详见IMPLEMENTATION-DETAILS。

## 2. 业务状态、checkpoint、事件的关系

Go 表是业务权威；LangGraph checkpoint 是恢复执行的内部状态；SSE 事件是已提交事实的投影。三者不能互相推断：有 checkpoint 不等于用户批准，有 token 事件不等于成功，worker 返回成功但业务事务未提交仍需对账。

每次节点执行生成稳定逻辑键 `(run_id,node_id,invocation_path)`。外部模型/工具调用有独立 operation ID；恢复先查询回执，再决定是否执行。最终状态提交失败时，用同一 event_id/result_digest 重报，不生成新业务操作。

持久节点事件与业务转换在 Go 同事务写；Redis 只唤醒订阅者。模型流式 delta 为临时 UI 预览，不作为恢复凭据。最终答案单独以正文 artifact 固化。worker 的本地缓冲丢失允许少一段预览，不允许丢掉审批或成功事实。

## 3. 安全恢复基线：失联先挂起

**首版采用保守恢复：租约超时转 recovery_pending，不因心跳超时立即并发接管。** supervisor 必须确认旧尝试进程及其子沙箱已退出，并撤销旧 token，才为恢复发放新租约。无法证明时保持挂起，由运维核对；不以 HTTP 断开、一次 SIGTERM 或一次数据库 session kill 作为死亡证据。

执行宿主机不可达时，以可审计的基础设施关机/隔离证据处理，或等待其恢复后终止；不用“网络分区应该已经死了”作为恢复依据。代理拒绝旧 token 的新请求，但不能召回已送到供应商的请求。每 attempt 使用独立 checkpoint 连接并记录 backend PID、backend_start 与连接标识，supervisor/受限维护服务核实对应会话及在途事务排空后才恢复；PID 复用不能误杀其他连接，连接池不跨 attempt 复用。无法核实继续挂起。

这是可靠性与恢复可用性的取舍。它允许首版在没有实现原子 checkpoint 围栏时避免自动产生双执行，不承诺任意网络分区下自动快速恢复。第一周必须展示“挂起而不重复执行”的故障结果。

可选的后续自动接管必须先实现 [checkpoint 原子围栏实验](research/execution-fencing-options.md)：对 checkpoints/checkpoint_blobs/checkpoint_writes 全部写路径，在同一连接与事务中验证租约；旧写入与 takeover 更新同一租约行形成互斥。单纯 `check lease; saver.aput()` 有竞态。触发器的角色、SECURITY DEFINER、安全 search_path 与最小权限也需验证。未通过所有写路径/断线/pipeline 测试不得启用自动接管。

## 4. 审批中断与恢复

LangGraph interrupt 后恢复会从节点开头重新执行；因此节点按下面顺序设计：

```text
稳定 operation_id → 读取已有回执
  已完成：校验后返回
  无回执：持久化 canonical 参数与审批待办 → checkpoint/interrupt
恢复：读取审批决定 → 重新鉴权 → 代理领取一次性授权
      → 持久化 dispatching → 发送 → 保存供应商回执 → 返回
```

节点重放不能再次创建不同审批或再次发送。审批可能比 worker 挂起确认先到，Go 先保存决定；只有挂起状态/检查点已确认且无活跃尝试，才生成唯一 resume job。resume payload 为签名决定引用，不接受客户端任意 `Command(resume=...)` 或 thread_id。

正常挂起也必须完成交接：worker 停止图并刷新 checkpoint，提交 waiting 结果，supervisor 确认该进程退出、连接关闭及沙箱退出后释放执行槽；Go 再完成旧 job、按已保存决定/子结果生成 resume job。仅收到 waiting 事件不足以启动后继尝试。普通 worker 无权给自己的退出作可信证明。

批准、子结果到达和退出确认均调用同一个幂等 `maybe_enqueue_resume` 事务：锁 Run→旧 job→attempt；只有等待条件满足、checkpoint 位置已固化、退出/数据库排空证据齐全且未取消，才完成旧 leased job、撤销旧 token、释放对应执行槽，再插入唯一 ready resume job。任何一步失败整笔回滚。批准先到只留决定；退出先到只留挂起事实。恢复调度前再查权限/预算。配额释放用 allocation 唯一键幂等，不能在心跳超时与退出确认各减一次。

通用“人工审批节点”用于业务决策；受控外部写工具本身仍必须在能力代理检查 operation 审批。单独在画布前放一个审批节点不能授权它后面所有未知操作。

## 5. 副作用与未知结果

只读操作和纯计算：默认初次+最多两次重试，指数退避+抖动，遵守总截止时间。Schema/权限/语法错误不重试。模型限流、暂时连接故障才可重试；重试及备用模型均计预算。

外部写：优先供应商幂等键和查询回执；发出前后本地记录无法与远端组成原子事务，所以不宣称 exactly-once。写入后客户端超时进入 unknown，Run needs_reconciliation。人工核对写回证据及操作人；确认成功只取原回执，确认未发送才允许同一逻辑操作按供应商规则重试。没有幂等/查询支持的连接器必须明确这一限制，不能自动补发。

发送必须为 `authorized → dispatching` 的唯一 CAS 赢家。其他并发调用若见 dispatching/unknown，不因缺少回执就发送；复用原操作查询/核对。输入哈希不一致直接冲突。保守恢复前同时终止/核实旧数据库连接及在途 checkpoint 写已结束，再读最新 checkpoint；只证明 Python 本地进程退出、不核实远端数据库在途写，仍不足以保证无旧写覆盖。

## 6. 取消与预算

Go 首先写 cancel_requested 并拒绝新能力调用/子任务；worker 轮询心跳响应和取消控制流，停止图调度、终止沙箱、等待子任务结束。无需等普通消息队列。仍在运行的模型流尝试断开；供应商可能继续计费，最终补账。

取消幂等，不自动撤回已发送邮件/数据写入。未知写结果转核对状态。只有所有受控执行单元退出且无未知操作，才 cancelled。超时不能直接用成功状态掩盖。

每次模型调用先按最大输出/输入估计预留预算，回执后结算，多并发共享父预算的原子预留账本。预算上限基于供应商声明 token 上限与价格版本，实际账单可能滞后，不承诺精确财务硬封顶。预留不能为负；未知调用先保留上限预留，后核对。Agent 内限制工具轮数、子调用次数、总耗时与费用；备用模型不是新预算。

## 7. 子应用、并行与工作槽

每次委派创建独立 child Run 与 checkpoint thread，输入是显式映射的最少字段，不默认传父聊天历史/个人记忆。子应用必须在父版本依赖闭包，当前用户仍有使用权限；有效权限 = 发起者当前权限 ∩ 父调用授权 ∩ 子应用需求。

创建子任务以 `(parent_run_id,parent_invocation_key)` 幂等，重放返回原 child Run；相同键不同应用版本或输入哈希报冲突。并行两次调用同子应用使用不同 invocation key，不能按应用 ID 去重。

Agent 的 `task` 工具通过平台适配器创建 child Run，不能直接把任意已发布应用当作 DeepAgents 内部配置。子应用执行整张 LangGraph 图，结构化结果经校验返回。每次并发调用隔离 thread，避免复用 per-thread 子图实例。

父等待子任务时释放 Python 执行槽，但保留顶层配额与逻辑会话槽。子任务有独立全局槽与每父并行上限，防止父任务占满所有执行槽后等待永远无法开始的子任务。队列公平按用户轮转；不跨等待持有数据库事务/锁。

并行分支默认 fail-fast：任一必需分支失败，发出其他分支取消；等待确认停止后 failed。外部未知写优先 needs_reconciliation。纯读取/计算可显式声明 optional 分支；其失败被汇合为带错误的结果，不假装成功数据。分支结果按节点 ID 命名，禁止多个节点盲写同一个状态字段。

## 8. 固定条件与恢复验证

恢复读取固定 worker 镜像/编译器/状态 schema，不能在新部署上直接用新图恢复旧 checkpoint。恢复窗口内保留旧镜像；不存在或迁移未验证时拒绝恢复，返回 `artifact_unavailable`。旧状态迁移只能离线备份、显式变换、双跑核验后引入，首版不自动迁移。

必须验证：审批前后重启；发布新版后恢复旧版；相同节点并发子调用；终态后迟到事件；删除输入后恢复拒绝；模型响应后未提交成功；外部写成功但回执丢失；宿主失联不自动接管；job 重复投递。测试步骤和期望见 EVALUATION，不以读过框架测试替代本平台集成测试。
