# HTTP、worker 与事件接口

机器契约：[openapi.json](contracts/openapi.json)。外部路径 `/api/v1`，内部路径 `/internal/v1`。本文件规定跨接口不变量；OpenAPI 给出核心请求/响应结构和路径。实现不得把权限或状态校验省略为通用 CRUD。

## 1. 通用约定

- Web 使用 HttpOnly、Secure、SameSite session cookie，状态修改配 CSRF token；API 使用可撤销、限 scope 的服务账号 key，存 key 哈希，明文仅显示一次。内网 worker 使用独立 mTLS 身份，不复用用户 key。
- UUID 由服务端生成；时间为 UTC RFC3339；金额/费用用 decimal 字符串，禁止二进制 float 记账。分页为 opaque cursor+limit，不接受任意 SQL 排序列。
- 资源读/写均校验主体与对象权限，禁止通过枚举 ID 访问正文；内部执行 token 也不授予任意资源读取权。
- 创建任务/审批/外部操作使用 `Idempotency-Key`。按 principal+method+path+key 唯一，保存规范请求哈希和响应；相同键不同内容返回 409。有效期推荐 24h；过期不能成为外部操作重复执行的依据，operation ID 回执持久保留。
- 草稿、记忆更新用 `If-Match`/ETag；缺失返回 428，版本冲突 412。Run 取消幂等，无需先读取 ETag。
- 错误体 `{error:{code,message,request_id,details}}`，details 不带其他用户正文/凭据。常见 400格式、401未登录、403无权、404不可见、409状态冲突、413超限、422语义校验、429配额、503依赖不可用。

## 2. 关键流程

提交请求先返回 202+request_id，不保证已经选中应用。`GET requests/{id}` 返回状态、interaction、候选摘要和 run_id；选择候选时提交 interaction_id+candidate_version_id，重新校验候选快照、权限和发布状态。输入不足进入 awaiting_input。

上传采用 `POST artifacts/uploads` 申请受限对象 key/大小/类型，传完调用 complete。complete 后扫描并校验实际大小/hash，不以客户端声明为准。上传签名过期前可能仍能上传，但未完成/被删除对象不注册为可用 artifact。下载必须每次经平台鉴权获取短期 URL；严格立即撤销场景使用平台代理下载，不能声称已经签发的对象 URL 会自动立刻失效。

`POST runs/{id}/cancel` 202 表示接受取消意图，实际终态看 Run 状态；不把 HTTP 成功解释为外部操作被撤销。`POST approvals/{id}/decision` 只能由指定有权人批准/拒绝，API 服务账号的高影响操作由绑定的人类负责人处理；没有负责人不能开始高影响流程。

发布流程：保存草稿→validate→submit（形成冻结快照和门禁报告）→管理员 review→创建版本。只审核 hash 一致的 submitted snapshot；更新草稿不会偷换正在审核的内容。

调试通过 `applications/{id}/debug-runs` 指定 draft_revision 和测试输入，创建私有固定快照；返回的 Run.kind=debug、application_version_id=null。发起评测由 `applications/{id}/evaluations` 排入可信评测runner，报告由服务端产生，不能把前端自报 passed 当发布依据。工具审核批准同样绑定 manifest_sha256，构建 ready 不是组织共享批准。

## 3. worker 协议

领取返回 job_id、run_id、attempt_token、lease_deadline、固定版本 manifest 和签名输入引用。worker 定时 heartbeat；响应带 cancel_requested、policy_revision、剩余预算，不依赖一次不可恢复的取消推送。

提交批量事件/结果时带 attempt_token、event_id、local_sequence。Go 在同一事务核对租约和运行状态，再写业务事件；过期/错误 token 返回 `409 stale_attempt`。每个 event_id 幂等，local_sequence 用于检测缺口，不能作为全局展示顺序。最终 seq 由 Go 每 Run 原子计数分配。

worker 不自行生成用户批准，不直接调用对象存储任意路径。输入下载与结果登记绑定 run/attempt/scope，临时结果只有登记成功后对用户可见。旧尝试迟到的对象进入孤儿清理，不覆盖当前结果。

内部 capability 调用必须带 operation_id、逻辑调用路径、能力版本、规范输入；代理自行加载授权，不相信 worker 声称 `approved=true`。参数改变必须创建新 proposal，不能重用旧 operation 授权。

## 4. SSE

`GET /runs/{id}/events` 认证后回放持久事件；`id: <run-local-seq>`，`event: <type>`，`data` 为 event.schema 封套。`Last-Event-ID` 为同 Run 单调 seq，重连可能重复，客户端按 seq 去重。无事件每 15s 心跳，不增加业务 seq。

持久事件包括 run.status、node.status、approval.required、artifact.ready、usage.updated；正文不放元数据事件。模型 delta 可作临时 SSE 预览，无持久回放保证。重连以最终 artifact/当前 Run 快照修正显示。游标超出保留窗口返回 410+snapshot_url，不能静默从当前点继续伪装完整历史。

等待审批/子任务时 SSE 可断开，业务不会因此终止。读取到终态后仍允许获取最终快照和文件；删除 Run 内容后只能返回被授权的最小状态，不回放原正文。

## 5. 首版 API 范围与阶段

OpenAPI 覆盖核心用户流程、管理与内部执行契约；批量运维/SSO/训练 UI 不在首版。第一周实现请求、附件、Run、事件和固定示例应用的最少路径。完整首版才要求全部文档路径和对应 RBAC/审计/故障用例通过。

worker API 和 Schema 版本在 manifest 中显式标记；不兼容返回 `worker_protocol_mismatch`，不得忽略未知必需字段继续执行。

## 6. v0.3闭合接口与内部角色

新会话以生成的OpenAPI为字段/路径权威：补齐账号激活/当前身份、列表/详情、候选模型验证、异步评测查询、记忆候选确认、审批创建、后台任务领取/结果、文件状态与代理内容。对应调用链和状态见IMPLEMENTATION-DETAILS、BACKGROUND-JOBS。

内部mTLS只证明身份，仍按`x-internal-role`检查supervisor/maintenance/worker；worker携带attempt_token且必须匹配本次run与租约。不能因为统一安全scheme叫workerMTLS就让任意内部客户端领取任务或自证退出。权限拒绝403，旧token409。

模型stream接口为受控临时SSE：每帧带operation_id/generation_id和type=delta/tool_delta/final/error；final含完整校验响应/receipt/usage引用，不是业务Run终态。第一个delta后失败不在同一iterator混入备用模型结果；平台开启新node/model尝试时给新generation_id并让UI丢弃旧草稿，完整tool-call校验后才执行工具。持久run事件仍遵循第4节。

文件complete返回202后轮询artifact至ready；签名只可写staging，最终对象经后台校验封存。下载返回短期URL或平台content代理URL。列表接口（静态凭据/端点目录除外）统一items/next_cursor与cursor/limit；draft/memory GET/更新返回ETag。所有error.code与错误分类在实现和契约测试中固定。
