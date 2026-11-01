# 实施闭环与跨模块约束

v0.3补充规范，修复交接审阅中发现的缺口；不扩大已确认产品。字段/路径由生成后的OpenAPI与SQL配套提供。以下设计均待实现和运行验证。

## 1. 身份与管理的启动闭环

`cmd/admin bootstrap`创建首个本地管理员，不提供公网bootstrap接口。`POST admin/users`创建不可登录的用户和一次性activation token，只保存token哈希；`POST auth/activate`原子消费未过期token并设置密码。管理员通过手动方式转交令牌，平台不要求邮件服务。`GET auth/me`返回当前principal、角色、CSRF与到期信息，刷新页面后不依赖旧前端内存。

密码/会话/Key使用成熟散列与随机数基础，不自创密码学。令牌/密码不出现在日志和URL查询；activation token从请求体提交，默认24h过期。管理员禁用用户/服务账号时立即撤销会话/Key、提升policy epoch，并使下一能力调用重新鉴权。删除/禁用不删除审计引用的主体行。

服务账号的owner是自身principal。它不自动借用responsible_user的个人文件/记忆/权限；个人长期记忆接口仅给人类用户，服务账号调用返回403。需要高影响审批时，指定有权限的负责人员，但执行仍须服务账号和批准人各自满足所需授权，不能做权限并集。应用硬性需要个人资料而服务账号未提供时，返回缺输入，不伪装用户。

## 2. 模型先验证，应用再引用

`POST models`登记candidate配置，状态pending，不要求已经有应用评测报告；otherwise会形成“先有模型才能评应用、先有报告才能注册模型”的循环。`POST models/{id}/validate`建立model_compat评测任务，由可信后台handler检查协议、结构化输出/工具调用、取消/流式/usage及获批目的地；`GET evaluations/{id}`查询进度，最终指向不可变evaluation_report。

通过后validation_state=passed，才可被共享应用发布引用；失败仍可查看报告，但不进入可用模型列表。debug只允许作者明确选定的候选模型兼容实验，不默认用生产用户正文测试。模型配置变更产生新profile version，旧版状态/撤销原因留痕。

endpoint_ref和secret_ref指向部署者预登记的端点/凭据目录。管理员选择这些引用，接口不接受任意用户URL或文件路径；Go读取真实凭据，worker/脚本不拿原始供应商密钥。开发stub也是明确登记的provider profile，不能把stub结果标为真实模型。

## 3. 草稿、快照、评测、发布

草稿单行保存definition、layout、capability_card及revision。新增应用时创建revision=1的空草稿。保存需If-Match，GET返回ETag；类型/硬合法性不通过仍可保存草稿，但不能debug/submit。

debug/evaluate都先固定execution_snapshot，包含图、能力卡、模型/工具/子版本、编译器/镜像/锁和状态Schema。snapshot不进入公共目录；发布要求报告绑定**同一manifest hash、同一dataset hash与可信runner**，passed且无硬失败。不能把另一revision/应用的报告拿来批准。

`POST applications/{id}/evaluations`返回evaluation_id与background job，未完成前不伪造report ID；GET evaluations状态为queued/running/succeeded/failed/cancelled，最终report_id可为空直到完成。基础设施失败与业务评测不通过分开：前者job.failed，后者job.succeeded但report.status=failed。

审核事务核对提交快照和报告，创建不可变application_version并返回version_id。仅layout变更不改变执行制品；发布hash覆盖可执行定义、能力卡与依赖等，不包含可变viewport/审核状态。test报告需绑定实际compiler/worker镜像与数据，不能仅匹配应用名称。

## 4. 清单、哈希与精确审批

制品封套见`execution-manifest.schema.json`和examples/execution-manifest.json。SHA256作用于固定的UTF-8规范JSON字节；采用[RFC8785 JCS](https://www.rfc-editor.org/rfc/rfc8785)，拒绝重复键、NaN/Infinity及不合法Unicode，高精度金额用字符串。JCS库及跨Go/Python golden vectors在P01验证，不把普通sort_keys等同完整JCS。

manifest_sha256放外部引用，不参与自身hash；平台保留规范原始字节/对象，JSONB只是可查询投影，不能从JSONB重序列化后假设hash相同。worker验证字节hash再解析，客户端使用服务端返回hash，不自行猜测字节规则。

可执行manifest不包含Run ID、snapshot ID、发布version ID、审核/验证状态或layout。这些属于外层记录，否则同一草稿从debug转发布就会改变被评测的hash。manifest保留稳定application_id/source_revision、图/能力卡/依赖与编译环境；评测报告绑定这个内容hash，发布事务再创建version身份。

审批hash同样由服务端对规范proposal计算，包含run/snapshot、逻辑调用键、目标、参数及必要来源版本。批准提交返回的hash；任何参数差异使旧批准失效。操作ID与params hash不同：同ID改参数冲突，相同参数的新操作仍是新授权，不自动复用旧批准。

## 5. 请求路由与无Run的模型调用

提交Request事务写route_request后台job。只有同会话最早未完成请求可从queued进入routing；等待澄清占逻辑槽。路由handler失联由后台job租约恢复，Request不能永久卡routing。每次重试先查已有interaction/Run，避免双创建。

路由在Run创建之前，不能强塞进只接受run_id的工具账本。使用platform_model_calls记录request或background job归属、模型、物理attempt、用量/费用和结果；仅允许管理员指定的控制模型，不开放Agent工具。默认每请求最多3次控制模型调用，超过转为明确候选选择/人工补充或失败，所有调用受平台限流与费用策略。

`POST requests`可显式带application_version_id：绕过模型匹配但仍创建Request、排队、鉴权和检查输入。这样应用目录点击和固定版本测试不必伪造自然语言路由。handoff只接收用户明确确认的本属授权摘要/文件引用，记录来源Run；不自动读取其他应用内部历史。

## 6. 领取、结果、退出与终态

领取发生在supervisor身份，mTLS声明其角色；每个worker尝试派生只绑定run/attempt的执行凭据。普通worker不能领取其他任务、给自己作退出证明或读取任意manifest。领取响应含固定制品、输入引用、job kind/resume引用和预算，不能只给worker一个run_id后让其查询所有数据库。

worker提交result是**结果提议**：Go校验并持久化run_attempt.reported_result，等待checkpoint刷新及supervisor证据。需要waiting的运行可先显示等待状态，但旧attempt未退出时禁止resume；succeeded/failed/cancelled终态须在退出/沙箱/DB在途写排空、子任务结束且无未知操作后才提交、释放会话槽。

最终结果提交HTTP200不等于Run已经终态；GET状态可能仍running并phase=finalizing。退出确认与结果到达调用同一幂等finalizer：任一先到都只保存证据；两者齐全才完成job/撤销token/释放allocation/发布终态或建立resume job。退出发生但result丢失转recovery_pending，不能根据exit code=0猜成功。

取消与终态竞争以Go事务为准：若终态已提交，取消返回当前终态；否则cancel_requested优先于未提交的成功结果，新能力调用被拒绝。审批拒绝/过期请求停止，待执行单元停止后failed。unknown操作优先needs_reconciliation，不假装失败或取消已撤销远端效果。

## 7. 节点事件、尝试日志与回放

每个event_id唯一；worker提交local_sequence用于查缺口，平台seq才是展示顺序。run.next_event_seq初始1；锁Run后取当前值作为新event.seq，再将计数+1，与revision/状态同事务提交。重复event不递增seq。

有效token还不够：状态/phase必须允许该动作。waiting/finalizing/cancelling仅允许退出/心跳/必要回执，不得开新工具调用。终态拒绝迟到结果；实际供应商用量晚到只补账，不复活Run。

node_attempts每次执行新attempt_no；动态Agent工具/子调用的invocation_path用稳定父路径+调用序号。重放核对原参数hash，变化则报错，不生成不同操作绕过去。写工具SDK把待审批操作返回给节点adapter，保存proposal后退出/interrupt；恢复重跑时读取回执。任意脚本不能靠宣称“成功”清除未决审批。

## 8. 读接口和UI闭环

活跃执行时间累计在runs.active_elapsed_ms，退出时按attempt区间幂等结算；实时截止检查还需加当前attempt已运行时间。审批/子任务等待不计活跃时间，新attempt不得重置总截止时间。Agent轮数/节点重试/child调用计数在持久图状态与Go稳定调用键中保留，恢复不能重新获得一整份预算或轮数。

Web通过列表/详情选择当前可读应用、工具、模型、知识库及版本，GET draft和memory携带ETag。会话消息是Request、interaction与Run输出的授权投影，不再建立一份无生命周期约束的正文副本。所有列表cursor稳定、owner/ACL过滤在分页之前；隐藏后台按钮不替代授权。

记忆候选提供pending列表与确认/拒绝事务，确认创建唯一memory并关联candidate；重复确认返回原memory。删除状态提供deletion job查询，不能仅显示202后永久无反馈。

## 9. 上传后不可变与文件准入

createUpload只签发staging key，artifact预分配另一个私有sealed key。complete用CAS从uploading进入scanning，记录预期hash并创建artifact_finalize；客户端轮询GET artifact，ready前不能作为任务输入。handler读取staging实际字节计算hash并做格式/大小/解压/宏/路径准入，再写私有sealed对象，最后PG事务检查未删除后激活。hash不符/超限进入quarantined。

不可把签名上传URL直接用于最终object_key，否则URL过期前可在校验后覆盖内容。sealed key不重用、不向用户或worker签发PUT权限；重复complete同hash幂等、不同hash冲突。删除与finalize竞争时不激活，被拒绝的孤儿对象后续清理。

首版文件“扫描”指上述准入与解析安全检查，当前没有选定或运行病毒查杀引擎，不宣称可识别全部恶意文件。若生产要求查杀，作为额外handler门禁接入并记录定义/版本；任何解析器仍在受限环境运行。

## 10. 固定HTTP连接器与能力种类

能力调用显式区分model/tool/connector与version ID，不能只拿一个可能跨表重复的UUID猜类型。首版受控HTTP连接器由部署者/管理员登记版本化策略：UUID、endpoint_ref、method/path allowlist、请求/响应Schema、secret_ref、read/write风险和policy hash；不提供任意用户URL透传。

策略可先由受控配置/`cmd/admin`登记并保存不可变制品，不需要再引入连接器平台。脚本工具声明connector_version_ids，Go发布时核对已批准策略并将版本/hash纳入manifest闭包，风险不能比所需连接器更低。运行/恢复要求原策略可加载且当前未撤销；ACL资源类型connector使用该UUID。原始凭据只由Go代理读取，脚本得到限定本Run/工具/策略的token。

模型config的kind/protocol/时间/上下文/输出上限/价格明确登记，embedding额外固定dimension；备用必须用途、工具/输出与预算兼容。精确供应商协议通过P01/F06 Adapter实现，未完成的provider不出现在可用目录。控制/评测模型也遵守目的地与费用规则。
