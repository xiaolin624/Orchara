# 后台任务协议

用户Run的job与平台后台job分开。`jobs/run_attempts`执行LangGraph；`background_jobs`承担artifact_finalize、route_request、tool_build、document_ingest、application_evaluate、model_validate、delete_content、catalog_index。Redis只唤醒，outbox只可靠投递，不混作执行成功记录。

## 1. 创建与领取

资源事务同写background_jobs：kind、target ID、expected_revision、dedup_key、输入artifact引用、owner、ready_at。每个handler为平台固定白名单，不接受用户传shell命令。evaluation_jobs保存评测业务状态并关联相应background job；报告是完成产物，不是排队记录。

maintenance身份通过内部claim接口领取支持的kind，PG行锁SKIP LOCKED，生成token/lease。handler可能在Go内执行控制逻辑，或启动可信Python解析/评测进程；用户脚本与包构建仍进入沙箱，不能在maintenance主进程运行不可信代码。

默认lease30s、heartbeat5s。结果提交核对当前token与expected_revision，幂等event/result键。超期只对已知无外部写的幂等handler重试；旧token禁止提交。写/删除对象采用源版本与tombstone约束，未知外部写仍走operation核对，不由通用job自动重发。

## 2. 各类handler的完成条件

| kind | 输入/目标 | 成功提交与防晚到 |
|---|---|---|
| artifact_finalize | staging对象+预期hash+artifact状态 | 流式读入受控临时区计算实际hash/准入，再写私有sealed对象；tombstone/超限/hash不符禁止ready |
| route_request | Request+输入/授权候选快照 | 当前请求仍持会话槽；只建一个interaction或Run |
| tool_build | tool_version+源hash/依赖锁 | ready image digest+测试/构建日志；版本撤销或源变更拒绝提交 |
| document_ingest | doc revision+source artifact | parser/chunk/embedding版本及结果；tombstone/旧revision禁止变ready |
| application_evaluate | evaluation_job+固定debug snapshot+dataset | 可信runner原始结果/报告；报告绑定snapshot与dataset hash |
| model_validate | candidate model profile+compat fixture | 兼容报告与passed/failed；不拿业务用户正文试模型 |
| delete_content | deletion_job+资源tombstone revision | 每类副本幂等清理与剩余scope；失败仍不可下载/检索 |
| catalog_index | 已提交版本/ACL epoch | 索引更新；检索后仍检查当前ACL与撤销 |

解析/索引可分批暂存，只有在权威事务核对revision后激活；旧任务产生的临时对象由孤儿清理，不覆盖新版本。报告/日志正文通过受控artifact登记，不放事件metadata。

## 3. 状态、错误和配额

background job：ready→leased→succeeded/failed/cancelled；暂时故障可退回ready（递增attempt_count、退避、最多3次），业务校验失败不重试。删除失败保留deletion_job.remaining_scopes，后续清理按部署策略继续，不把内容恢复可见。

evaluation job：queued→running→succeeded/failed/cancelled。succeeded表示报告已生成，报告status可能failed；not_run、空checker、未完成case不能构成passed报告。客户端GET evaluations返回业务状态和report_id，GET background-jobs仅给有权管理员/owner的脱敏诊断。

非Run模型请求记录platform_model_calls，带request_id或background_job_id二选一；默认显式模型端点/预算/用量/物理attempt，禁止SDK绕开Go直接发供应商请求。控制任务和用户Run预算分别结算，按统一元数据视图展示。

Python解析/评测handler通过job-scoped model-calls与artifact上传/下载接口访问获授权数据，不能假造run_id借用用户worker接口。Go按kind限制目的：model_validate只能用对应candidate及合成fixture，其余模型须已验证且用途匹配；judge/embedding流量也记账。每次读取重新检查owner/当前授权与源tombstone，deletion清理只按可信删除账本执行，不把正文返回用户或日志。

本handler凭据只允许写受控对象前缀/领取固定kind，不拿业务用户任意目录。staging签名URL可能尚未过期，但后续覆盖不影响sealed对象；sealed key不重复使用、不发PUT签名。具体文件准入见IMPLEMENTATION-DETAILS。

artifact_finalize使用独立system_io槽（初始2），不与可能等待文件ready的评测/摄取handler共享唯一执行池。worker/maintenance通过各自scoped artifact-status查询，不能借用用户cookie接口；handler等待状态时续租但不耗尽system_io槽。否则所有评测任务都等待自己的报告封存，会阻塞完成它们的finalize任务。对应拥塞/恢复用例在F00B/F13中测试。

## 4. 最小测试

进程重启后ready/leased状态可恢复；同dedup_key不双建；旧token提交拒绝；文档删除后旧摄取不能激活；route重试不双Run；报告业务失败与job基础设施失败区分；Redis丢唤醒后PG轮询仍推进。后台任务没有通用“至少一次就安全”的承诺，安全来自每个handler的幂等/源版本/副作用约束。
