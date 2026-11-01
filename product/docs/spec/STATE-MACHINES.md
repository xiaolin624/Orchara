# 状态机与业务不变量

所有业务状态转换由 Go 事务执行：Run/Request 等带 revision 的聚合使用 `WHERE id=? AND state=? AND revision=?`，成功后 revision+1；审批、job、operation 等行使用行锁及预期状态 CAS。同事务写事件和必要 job/outbox。事件到达次序不决定状态，重复事件不能倒退终态。

## 1. 平台请求 Request

| 当前状态 | 事件 | 下一状态/动作 |
|---|---|---|
| queued | 获得会话执行槽 | routing |
| routing | 无匹配/缺信息 | awaiting_input；创建 interaction |
| routing | 候选歧义 | awaiting_selection；保存候选快照 |
| routing / awaiting_selection / awaiting_input | 唯一合法选择且必填输入满足 | dispatched；同事务创建唯一顶层 Run |
| awaiting_input / awaiting_selection | 有效 interaction 答复 | routing；重新鉴权与输入校验 |
| awaiting_input / awaiting_selection | interaction 等待超期 | failed，error_code=interaction_expired；释放会话槽 |
| queued / routing / awaiting_* | 用户取消 | cancelled |
| routing | 无法恢复的路由错误 | failed |
| dispatched | Run 终止 | 请求保持 dispatched，结果通过 run_id 查询 |

同会话最早未完成请求占逻辑会话槽，包括澄清等待；后续普通任务排队。审批、澄清答复、候选选择与取消均为带 correlation ID 的控制操作，不作为排队新任务。等待过期后释放会话槽。已结束 interaction 的答复返回 `409 interaction_closed`。

## 2. Run

| 当前状态 | 允许的下一状态 | 条件 |
|---|---|---|
| queued | running, cancelled, failed | 领取、排队取消、发布版本失效 |
| running | waiting_approval, waiting_children, succeeded, failed, cancelling, recovery_pending, needs_reconciliation | 检查点/结果、外部不确定性或失联 |
| waiting_approval | queued, failed, cancelling | 批准生成 resume job；拒绝/过期失败 |
| waiting_children | queued, cancelling, failed, needs_reconciliation | 子任务完成后恢复父图；失败策略见执行规格 |
| running / waiting_* / queued | cancelling | 已执行任务取消命令；未领取 queued 可直接 cancelled |
| cancelling | cancelled, needs_reconciliation, recovery_pending | 必要执行单元确认停止；存在未明副作用时不得直接 cancelled |
| recovery_pending | queued, failed, cancelled, needs_reconciliation | 证明旧单元退出后恢复；或人工终止/核对 |
| needs_reconciliation | queued, failed, cancelled | 授权人工核对外部回执；恢复须绑定原 operation ID |
| succeeded / failed / cancelled | 无 | 再次运行创建新 Run，不能复活终态 |

`cancel_requested_at` 是控制意图；`cancelled` 表示平台已确认停止受控执行，并不表示撤销已发生的外部写入。审批拒绝/过期的失败原因分别为 `approval_rejected/approval_expired`。预算耗尽失败原因 `budget_exhausted`，仍保存已确认产物。

表中所有进入终态的转换都有退出guard：已有执行必须确认执行进程/沙箱与数据库在途写排空、子任务结束且无unknown操作。未启动queued可直接终止。worker结果是提议，先存run_attempts.reported_result；Run可保持running且phase=finalizing，不能仅凭result HTTP200立即释放会话。等待/终态交接统一finalizer见IMPLEMENTATION-DETAILS。

顶层会话占用直到 Run 终态。挂起审批/子任务释放执行 worker，但不释放逻辑会话槽。终态与尚未结清的实际费用可分开记录，不能为了补账重新激活 Run。

## 3. Job、执行尝试与节点

Job：`ready → leased → completed`；失联转 `held`，满足安全恢复条件再 `ready`；终态 Run 的 job 置 `void`。每次领取生成新 `attempt_token`，续租不可改变 token。

节点：`pending → running → succeeded/failed/skipped`，或 `running → waiting`。每次实际重试建立新 node_attempt；逻辑节点结果与尝试日志分离。重放时可复用已校验 succeeded 的纯计算回执；不把“曾开始”当成“已完成”。

外层 DAG 不提供任意 goto/retry 回边。平台重试以同一节点逻辑执行键和递增尝试号发生，最多次数由策略限制；Agent 内循环单独计数。

## 4. 审批与外部操作

审批：`pending → approved/rejected/expired/invalidated`，仅第一次有效决定生效。重复同一决定幂等；相反决定或参数版本冲突返回 409。Run 取消、权限撤销或操作参数改变使待办失效。

ToolOperation：`prepared → awaiting_approval → authorized → dispatching → succeeded/failed/unknown`。无需审批的只读操作可由 prepared 直接 authorized。`dispatching` 之前写持久意图，之后的超时/进程丢失可能为 unknown；只能通过供应商查询或人工证据核对为 succeeded/failed。已确认未发送且无副作用的失败才允许按策略重试。

批准并不保证后续可执行：代理执行前再次检查主体、资源、模型数据去向和预算。审批签名对象包含 run、版本、节点/调用序号、目标、规范参数哈希及过期时间。脚本不能以更换参数复用批准。

## 5. 发布与资料状态

- 应用：draft revision → submitted snapshot → approved version；reject 回到可编辑草稿但保留审核记录。published version 不可改写；revoked 禁止新执行。
- 工具环境：submitted → building → ready/failed；审核发布只能引用 ready 的镜像 digest。构建成功不等于脚本业务安全。
- 文档：uploaded → scanning → indexing → ready/failed；deleting/deleted 立即排除检索。索引完成事务核对 tombstone，禁止晚到任务把已删文档恢复为 ready。
- 记忆候选：pending → confirmed/rejected/expired；只有 confirmed 的个人记忆进入上下文。

## 6. 必测不变量

同请求最多一个顶层 Run；同会话最多一个非终态顶层 Run；终态拒绝迟到输出；相同 operation ID 不重复发送；取消后 job 不自动重试；失联不等于死亡；等待审批不占 worker；父任务不能绕过子任务 ACL；删除标记立即阻止下载/检索/恢复读取；最终成功前所有必需输出通过 Schema 校验。
