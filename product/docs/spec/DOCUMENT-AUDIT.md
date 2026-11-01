# 全局审阅与新会话交接记录 v0.3

日期：2026-11-01。方法：不依赖聊天补设定，沿“环境→身份→模型→创作→调试/评测→发布→请求→执行/审批→结果/删除”逐步检查入口、API、表和完成条件，并审查任务依赖/配置/跨文件规则。本次只修改规格与文档工具，不实现业务平台。

## 1. 结论

v0.2有清晰产品与架构，但不足以直接交给新会话完整开发：缺统一入口、启动闭环，部分流程只有文字没有可调用接口/持久记录，任务分组存在隐含循环。v0.3已补成可开始D00/P0的自包含规格包。

“可接手”不等于已经证明架构运行正确。文档层闭环由静态检查和隔离目录校验检验；Go/PG/模型/Linux沙箱、完整用户旅程和训练仍需任务图中的实际实验。无法保证任意模型自动一次完成企业项目，工程验收仍以真实结果为准。

## 2. 发现与修复

| ID | 原缺口/冲突 | v0.3处理 | 尚需实际验证 |
|---|---|---|---|
| A01 | 新会话不知道先读/写哪里，依赖旧会话技能 | HANDOFF/AGENTS/BOOTSTRAP/PROGRESS，产品仓库独立；移除绝对本机Markdown链接 | 新实现者按入口推进是否顺畅 |
| A02 | F02发布依赖评测，评测需可执行版本；十节点/子应用相互牵制 | F02A/B、F04A/B、F13A/B、F15A/B拆分，机器DAG成为依赖权威 | 分阶段开发节奏与真实工期 |
| A03 | 首个管理员/邀请后无密码激活路径 | 本地bootstrap CLI、activation token表、activate/me/禁用接口 | 单次消费、并发最后管理员保护、凭据安全 |
| A04 | draft API有capability_card，表中无对应字段 | 持久字段补齐，快照/报告/审核绑定内容hash | 并发编辑/提交/发布事务 |
| A05 | 注册模型要求先有报告，但模型又是应用评测前提 | candidate→独立model_validate→passed后共享 | 真实provider/tool/stream兼容 |
| A06 | 异步评测只有报告表，没有任务状态/领取链 | evaluation_jobs/background_jobs及进度查询；结果失败与基础设施失败分开 | runner重启/报告幂等/空验拒绝 |
| A07 | 创建有接口，UI所需列表/详情/ETag不全 | 补会话/消息、版本、审核、模型/工具/KB、文件状态等读接口 | 分页前过滤、角色/IDOR与浏览器旅程 |
| A08 | 记忆候选有表，无提出/确认/拒绝接口 | 内部提候选、用户列表/决定、candidate唯一关联memory | 确认重放、服务账号个人数据拒绝、删除传播 |
| A09 | worker与supervisor/maintenance共用安全scheme容易误授权限 | 内部operation声明角色，job/run token范围与可信退出证明分离 | mTLS身份映射、伪造role/worker ID负例 |
| A10 | worker返回成功就提交终态，与退出/排空规则冲突 | 结果提议/phase=finalizing、supervisor证据、统一finalizer | 两种到达顺序、取消竞争、进程退出却result丢失 |
| A11 | Request.routing进程重启无恢复；路由模型还没有run_id | route_request后台job；platform_model_calls为Request/BG记账 | 路由重投不双Run、控制调用限额/费用 |
| A12 | 摄取/构建/删除等无Run任务没有统一执行契约 | 固定后台handler、源版本守卫、job-scoped模型/文件访问 | 删除后晚到摄取、旧token结果与副本清理 |
| A13 | 上传URL可在校验后覆盖最终对象 | staging→后台hash/准入→sealed对象，不给最终key PUT签名 | 重复上传、complete/删除竞态、对象存储实测 |
| A14 | manifest格式/hash、debug变发布是否改hash不清 | manifest Schema、规范字节artifact、JCS、运行身份不进内容hash | Go/Python JCS golden vectors、镜像/状态Schema锁版 |
| A15 | 仅凭UUID区分不了模型/工具/连接器；外部能力版本范围不清 | capability_kind与version、工具声明连接器、manifest闭包policy hash | ACL交集、SSRF/参数审批/未知写核对 |
| A16 | 节点输出封套、模板、比较类型、join/skip细节分散 | COMPILER-CONTRACT明确wrapper/data/结果与结构化region编译 | 真实LangGraph嵌套、optional/required失败与中断 |
| A17 | 总执行时间/轮数可能在resume重置 | active_elapsed_ms+当前区间、持久计数与稳定操作键 | 长等待/多恢复仍不越预算和截止时间 |
| A18 | 删除202返回哪个ID不明确，无法查清理状态 | DeletionAccepted明确deletion_job_id和resource_id，状态接口 | 重复删除/失败重试/备份回放 |
| A19 | 检索/个人记忆PG基线索引表留给未来猜测 | 独立catalog entries与memory embeddings；维度/ANN在锁模后迁移 | 中文召回、维度迁移、权限先过滤 |
| A20 | 相同F编号同时表示功能与故障用例 | 任务使用F*，故障案例使用FAULT-*；明确引用 | 实施报告持续保持命名一致 |
| A21 | 评测handler等文件封存时可占满同一后台池 | artifact_finalize独立system_io槽，job/run-scoped状态查询 | 全槽等待仍能推进与续租 |
| A22 | 迁移/平台/checkpoint凭据与schema初始化容易混用 | BOOTSTRAP明确不同DSN/search_path与独立setup | 实际最小权限与建表位置 |

这些是**规格层修复**，不是已修复一个运行中的系统。没有将新增Schema/API当成业务代码已存在。

## 3. 检查结果与局限

执行结果分别保存在validation-results.txt、handoff-validation-results.txt、package-validation-results.txt；命令和范围见VALIDATION。检查包括：JSON/Schema/OpenAPI、SQL语法、任务DAG、关键旅程接口/表存在、内部角色声明、manifest/模型边界fixture、相对文件目标、隔离复制后的校验。

静态存在不能证明handler实现、鉴权、跨表事务、S3/PG行为或用户体验。隔离目录测试使用当前校验环境，不等于新安装了依赖或另一个模型已开发完整平台；标题锚点/所有远端链接未全量重新联网检查。

## 4. 新会话应消除的下一个不确定性

从D00/P01记录实际环境/锁版开始，优先做P02恢复、P03隔离、P04退出交接、P05图语义。它们失败时保留反证，不能用“文档已审阅”跳过。真实业务采用、RAG质量、Mem0收益/完整删除、judge可信度、容量和LoRA收益仍没有运行证据。

本轮消除的是“需要旧聊天才能知道如何开始、接口/任务不闭合”的不确定性。可能推翻本轮可接手判断的证据：隔离包缺文件/契约生成不一致、旅程仍缺必需操作/状态、或实际开发遇到只有作者知道的隐含条件。发现后更新本报告和权威规格，不堆新的口头约定。
