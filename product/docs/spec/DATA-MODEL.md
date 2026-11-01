# 数据模型与事务设计

SQL 参考基线：[database.sql](contracts/database.sql)。这是首版关系模型，不是已经执行的迁移。落地时拆分编号迁移，先在临时 PostgreSQL 验证建库、约束、并发与回滚，再生成 sqlc；不得把静态语法通过写成数据库验收通过。

## 1. 聚合与关系

| 聚合 | 主要表 | 关键约束 |
|---|---|---|
| 身份 | principals、users、role_bindings、sessions、api_keys | 用户/服务账号统一 principal；key/session 存哈希 |
| 账号启动 | activation_tokens | 一次性令牌哈希/过期/消费；无匿名bootstrap HTTP |
| 授权 | resource_grants、policy_epochs | 每次动作当前鉴权；epoch 驱动缓存失效 |
| 应用与工具 | applications、application_drafts、application_versions、publication_reviews、version_dependencies、tool_versions | 草稿 CAS；发布快照不可变；依赖闭包无环 |
| 模型 | model_profile_versions | 固定配置、secret_ref、目的地；不把真实密钥放 manifest |
| 任务 | conversations、requests、interactions、runs、jobs | 一个请求最多一个顶层 Run；同会话活跃顶层 Run 唯一 |
| 执行快照 | execution_snapshots | 发布运行引用固定版本；调试运行引用固定草稿revision/manifest，当前权限仍复查 |
| 执行 | run_attempts、node_attempts、operations、approvals、run_events、usage_ledger | 租约 fencing token、稳定操作键、审批哈希、事件幂等 |
| 数据 | artifacts、run_artifacts、memory_candidates、memories、knowledge_documents、knowledge_chunks | 正文引用、tombstone、来源版本、个人 scope |
| 治理 | audit_events、diagnostic_grants、idempotency_keys、outbox、deletion_jobs、evaluation_reports | 正文授权、清理/索引任务、测试报告绑定制品 |
| 后台与评测 | background_jobs、evaluation_jobs、platform_model_calls | 无Run任务领取、源版本守卫、候选模型兼容与控制模型账本 |
| 检索投影 | application_catalog_entries、memory_embeddings | 独立目录/记忆索引，当前权限与revision仍查权威表 |

checkpoint 由指定版本 `langgraph-checkpoint-postgres` 在独立 schema 管理；使用专用迁移角色执行 setup，运行角色不应有任意 DDL 权限。不要在本模型中复制一份猜测的框架表。

## 2. 关键事务

**建 Run**：锁 conversation 和 request；验证请求顺序/权限/输入；插入 Run（application_version、manifest hash、预算）；更新 request.dispatched；插入 job 和首事件。事务失败全部回滚。部分唯一索引防止竞态产生同会话双活跃 Run。

**领取 job**：选择 ready、可执行且配额可用的 job，行锁 SKIP LOCKED；锁相应配额计数，更新 job leased、创建 run_attempt、Run running、lease token；commit 后才启动进程。不得跨外部调用持锁。配额实现须有明确 counter/锁行，不能先 COUNT 再 INSERT 造成超发；SQL 基线中的 quota_counters 为此预留。

**状态与事件**：核对expected revision、attempt_token和转换；锁Run后取当前next_event_seq作为本条seq，再将计数+1，与revision/事件/outbox同事务。初值1的第一条事件就是seq=1。event_id重报不再次增加seq。

**批准**：锁 approval，验证 pending、参数哈希、审批人、Run 非终态/取消、过期时间与权限；保存决定、写审计/事件；仅当原尝试挂起确认后生成唯一 resume job。并发相反决定只有一个成功。

**发外部操作**：锁 operation，若 succeeded 返回原回执；dispatching/unknown 只查询或等待核对，绝不再次发送。只有验证参数哈希/授权后成功执行 `authorized → dispatching` CAS 的唯一赢家可发送。failed 默认不重发；只有证据确认未发送且策略允许时，审计后重授同一操作，继续使用供应商幂等键。跨网络不持 DB 锁；租约失效/异常结果进入 unknown。只靠 unique key 不能解决远端成功而本地提交失败。

**发布**：提交时复制 draft 的不可变 manifest/hash；审核通过时核对同一 snapshot、检查报告、依赖、ACL；创建 application_version 并 CAS 更新 active_version；同事务写 outbox 更新路由索引。已引用版本不改 JSON/镜像/模型参数；数据库应用角色限制 UPDATE，必要停用字段由专用命令更新。

**调试/评测**：锁指定 draft revision，创建 debug execution_snapshot 与 kind=debug Run，不挂平台 Request/Conversation，也不进入共享目录。发布前测试由可信评测 runner 调用相同执行协议并登记 evaluation_report；客户端不能自行上传 passed 状态充当门禁。共享工具要求 tool_version 同时 ready、approved_at 非空且未撤销；作者私有调试仅允许其自有 ready 工具，仍受相同隔离/授权规则。Snapshot 中应用/版本归属与 Run.kind 的一致性由事务校验。

v0.3闭环：draft表保存capability_card；evaluation_jobs关联固定snapshot或candidate model（二选一）、dataset与background job，最终产生report。模型先pending注册再validate，不要求预先存在应用报告。结果/退出finalizer及background领取细则见IMPLEMENTATION-DETAILS、BACKGROUND-JOBS。

manifest_artifact_id保留规范字节，manifest JSONB为查询投影，hash不能从任意重序列化推断。被发布/可恢复运行引用的配置artifact不接受普通用户内容删除操作，返回409 resource_in_use；用户任务正文的删除仍按既定策略传播。

## 3. 正文与隔离

会话/请求/节点/模型正文采用受保护 artifact 引用；数据库仍含敏感元数据，应加密磁盘/备份及限制角色。Schema 不代表所有 JSON 都可记录正文：manifest 内提示模板属于作者配置，用户运行内容不混入 manifest；events/audit 只允许类型化脱敏字段。

推荐独立数据库角色：migration（DDL）、platform（业务 DML）、worker_checkpoint（仅 checkpoint schema）、readonly_metadata（脱敏视图）；worker 无业务表直接写权限。授权主要在 Go 服务，首版不宣称已经实现并验证全表 PostgreSQL RLS。禁止前端直连数据库。

对象 key 含随机命名空间而不是可猜用户路径；artifact 的 owner、scope、purpose、run/attempt、hash、mime、size 和 tombstone 是数据库权威。S3 key 存在不代表对象可访问或归当前 Run。

## 4. 索引与向量

常用索引：应用发布状态+更新时间、grant 主体/资源/动作、request 会话顺序、job ready_at、run 状态/owner、事件 run+seq、approval approver+pending、文档 KB+state、记忆 owner+app+active。时间线和审计按时间分页，正文不建全局可读全文索引。

SQL的chunk、记忆、目录embedding保留无维数vector作为规格占位：P01/C03选定embedding模型/dimension后创建明确维度/索引的编号迁移并实测。首轮可在小数据授权过滤后精确检索，不宣称未测ANN性能。目录使用application_catalog_entries，个人记忆使用memory_embeddings；不能复用私人知识表。中文分词/混合检索质量须用数据验证，不把某个全文配置自动称为适合中文。

## 5. 删除与保留

删除事务先 tombstone 并增加 policy/data revision，创建 deletion_job。后台幂等清理对象、向量、缓存、checkpoint 与副本，保存清理范围/错误/重试。不能仅删除 artifacts 一行而把复制内容留在 checkpoint。

外部供应商与备份保留边界见 DATA-POLICY。恢复备份后，先回放独立保留的删除账本再开放读取，避免被删除数据重新可见。审计仅保留最小元数据；不得通过 audit.details 绕过正文删除。
