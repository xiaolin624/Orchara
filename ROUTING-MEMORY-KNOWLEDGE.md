# 路由、记忆、知识与上下文

## 1. 路由：先过滤，再匹配，再决定

输入为当前 Request 内容、显式选定附件摘要及用户允许的最少交接上下文。先取得当前有使用权限且未撤销的已发布应用版本，再用能力卡全文/向量检索得到小候选集；不能先对全库生成有敏感描述的候选再在 UI 隐藏。

模型接收需求和候选的 purpose/includes/excludes/input/output/examples，输出 `select(candidate_id)`、`clarify(question,missing_fields)` 或 `no_match(reason)`。服务端检查输出类型、候选成员、应用可用性及必填输入。模型分数未经校准不当作概率；自动选择条件由独立验证集确定，未通过时保持用户选择。

多个合理候选展示区别与必填项；没有合适应用解释缺口，不自动选近似应用执行。应用目录变更通过事务 outbox 更新索引，检索返回后再次核对版本/ACL，防止索引滞后越权。元数据未索引可回退获授权范围内全文检索。

一条新需求只创建一个主 Run。复杂组合在明确的主应用中表达，平台路由不临时合成未经审核的流程。应用撤销或权限变化时重新选择，不偷偷换到最新版。

## 2. 跨应用与个人记忆

平台会话保存用户可见历史；每次 Run 只得到显式映射的 task context。应用切换展示“将交接的摘要/附件”，由用户确认新增敏感交接范围。主应用内部状态和其他应用个人记忆默认不传递。

个人记忆以 user_id+application_id 隔离，版本升级不自动转给另一应用。保存结构为正文 artifact/受保护文本、来源、确认者/时间、revision、状态、可选过期时间；自动抽取先候选，不直接覆盖已有事实。用户明确“记住”或确认候选才进入 active 集。

修改使用 ETag；冲突信息展示给用户，不让后台模型按最后写入静默覆盖。删除立刻增加 memory_revision/cache epoch，并清理嵌入与缓存。长期记忆是用户可管理的数据，不从所有历史聊天自动抽取无限积累。

Agent 上下文按 token 预算分配：必要系统策略、当前输入、必要工具 Schema、已授权记忆、检索片段、最近历史；超限先减少低相关检索/历史，保留引用和摘要来源。摘要不是原始事实；不可用于授权或金额计算。截断记录来源和原因，不能丢掉审批参数或用户否定条件。

## 3. 组织知识库

首版支持 UTF-8 txt/md、普通 PDF 文本、CSV/XLSX 的受控解析；扫描 PDF/OCR、复杂表格先明确不保证。原始资料、解析版本、chunk、embedding revision 分离；解析失败可重试，不把空索引标 ready。

每个 chunk 继承文档 ACL，检索在 SQL 候选阶段限制可读文档，随后排序及 top_k；向量模型 revision/dimension 一致，换模型建新索引版本，不混用向量。1000 应用的路由向量和知识资料向量分 collection/表，不复用授权逻辑。

返回引用包含 doc_id/version/chunk_id、页或行范围、短摘要。最终答案引用必须能解析到本次实际检索且当前可读来源；凭空 citation ID 拒绝。文档撤销后用户再打开引用应显示不可用，不继续提供缓存正文。

## 4. 缓存键与失效

检索键至少包含 principal、有效授权 scope/ACL revision、知识库/文档 revision、embedding revision、规范 query、top_k。确定性只读工具键包括用户授权范围、工具版本、输入哈希、源版本/快照和策略版本。未提供稳定来源版本的实时外部读，默认不缓存或只用明确短 TTL。

不跨用户缓存个性化最终答案。外部写回执以 operation ID 持久存储，这是幂等记录，不等于用普通 TTL 缓存跳过执行。

删除/权限变更先在权威库生效并提升 revision，异步驱逐是清理优化；即使驱逐丢失也不能命中旧授权键。缓存命中仍检查资源 tombstone，不把 Redis 的 TTL 当权限保证。

## 5. v0.2开源适配

详见 [RAG/记忆源码核查](research/rag-memory-reuse-audit.md)。普通PDF评估Docling `NativePdfFormatOption`，分块评估LlamaIndex `SentenceSplitter/NodeParser`，保持SourceSpan贯通。暂不默认启用模型OCR/复杂表格；完整IngestionPipeline/PGVectorStore需先解决自身正文/docstore/cache与平台表、ACL和删除的对应。

Q45已确认：PG基线先可用，Mem0优先做对照；用户确认记忆后可用`infer=False`写入。返回vendor ID必须映射平台memory ID/revision，检索仍检查active/tombstone。所读V3 `infer=True`主路径是自动提取并新增，不能只凭docstring声称自动覆盖/删除。它仍初始化SQLite历史，删除路径保留旧正文历史；物理清理要覆盖消息、历史、实体、向量与日志。未满足收益/确认/删除门禁不成为正式后端，当前未执行这些实验。

## 6. 采用的方法与可推翻条件

采用数据溯源方法：每份进入上下文的内容记录 source ID/version/ACL revision/用途，答案保留引用链；检查记忆确认、文档更新、删除和恢复是否沿来源传播。新发现是：只清理向量库远远不足，checkpoint 和上下文摘要也会复制正文，必须纳入删除与恢复规则。

尚未运行检索质量实验。若权限过滤导致相关性显著下降，应调索引/检索策略，不能放宽授权换取效果；若能力卡无法区分真实需求，先改卡和澄清交互，不立即用微调掩盖产品定义不清。
