# 新会话开发入口

文档基线：v0.3，2026-11-01。**只拿到本目录即可接手；不需要此前聊天记录、本机技能、旧会话记忆或37个上游仓库全部可用。** 当前交付是规格/样例/静态校验工具，平台代码尚未实现。

## 1. 任务与不可重新猜测的决定

目标：实现单企业、多用户、私有部署的可视化Agent创作与运行平台，形成可复现的AI应用工程成果。完整首版范围保留，按阶段交付；第一周只验收销售CSV/XLSX分析纵向切片，日期不是跳过质量门禁的理由。

- Go负责平台业务与状态，Vue3/Vue Flow负责画布，Python用LangGraph执行整图、DeepAgents执行自主节点。
- 外层DAG、十类节点；Agent内部循环受预算/时间/轮数限制。一次请求选择一个主应用，子应用显式固定版本。
- 发布版本和运行环境固定；用户共享应用不共享个人正文。读写资源都鉴权。
- 用户脚本使用OpenSandbox+gVisor/runsc；首周仅预置工具，隔离未通过不开放任意上传。
- 人工审批绑定实际操作参数；未知外部写不盲目重发。失联先挂起，旧执行单元与数据库写排空后才恢复。
- RAG属于完整首版；个人记忆确认后保存。PG基线先可用，Mem0收益/完整删除通过后再正式引入。
- 确定性checker独立于质量评分；统一TaskRunner，优先验证DeepEval，其他框架按需。训练是独立LoRA实验，不是首周在线训练平台。

这些是已确认需求。版本号、硬件和凭据属于实施输入，不是重新访谈产品的理由。

## 2. 第一次打开：按顺序做

1. 读本文件、[PROGRESS.md](PROGRESS.md)、[IMPLEMENTATION-ORDER.md](IMPLEMENTATION-ORDER.md)。确定已有代码/报告；这里的初始状态不是未来进度的永久真相。
2. 将本规格目录作为只读来源起点，选择独立产品仓库。默认建议在同级新建`agent-platform`；用户指定位置优先。不要在上游clone中写产品代码。若已有产品仓库，先看git状态/其AGENTS，保留用户修改。
3. 按 [BOOTSTRAP.md](BOOTSTRAP.md) 探测环境、复制文档到产品仓库`docs/spec`、建立代码骨架和进度记录。实际根路径由当前环境决定。
4. 执行文档校验，再按`contracts/task-graph.json`最早未完成、依赖满足的任务推进。第一项D00是环境/路径记录，之后P01是安装锁版；不能从“做一个漂亮首页”开始冒充完整实现。
5. P0先做最小集成探针，保存版本、命令、输入、原始结果与结论；依赖/服务不可用时记录blocked，并继续独立可做的任务，不编造通过。
6. 每个任务完结更新产品仓库的PROGRESS及任务状态，附代码位置、实际命令/结果、剩余问题。随后推进可执行任务；用户授权完整开发时不在每个小步骤重复请求许可。

完成接手的条件：知道代码放哪里、当前有哪些产物、能运行哪些命令、下一任务是什么、缺什么外部条件。仅复述架构不算接手完成。

## 3. 权威顺序与按需阅读

Q1–Q46与本文件的产品约束为已确认范围；当前详细规格和机器契约为实施依据。`research`是事实/历史候选证据，`DESIGN-SESSION`是历史访谈，不从旧“待定”恢复已关闭的问题。审阅记录不是当前开发的审批闸门。

| 正在开发 | 必须读取 |
|---|---|
| 数据/事务/身份 | DATA-MODEL、SECURITY、IMPLEMENTATION-DETAILS、contracts/database.sql |
| API/前端交互 | API、UX、contracts/openapi.json；字段/路径以OpenAPI为准 |
| 工作流/节点 | WORKFLOW-SPEC、COMPILER-CONTRACT、workflow.schema、相关examples |
| worker/取消/恢复/审批 | EXECUTION、STATE-MACHINES、IMPLEMENTATION-DETAILS、manifest协议 |
| 摄取/构建/评测/删除后台任务 | BACKGROUND-JOBS、DATA-MODEL、对应API与表 |
| RAG/记忆 | ROUTING-MEMORY-KNOWLEDGE、DATA-POLICY、组件研究及C03/C04 |
| 评测/训练 | TESTING-AND-DATASETS、EVALUATION、TRAINING-EXPERIMENT |
| 部署/发布 | BOOTSTRAP、OPERATIONS、SECURITY、P0与F15报告 |

生成源：`tools/build_contracts.py`生成OpenAPI/DSL/事件/manifest与核心样例；`build_reuse_contracts.py`生成组件/评测用例；`build_handoff_contracts.py`生成任务图和旅程。修改生成物须同时修改源并重建。SQL为直接编辑的规格基线；编号迁移由实施任务创建，不对生产数据库直接执行整份参考文件。

v0.3的API补缺在`tools/handoff_api.py::extend_contract`，由主生成器调用并覆盖基础定义；修改这些接口时一并检查该扩展，不能只改前面的旧定义或生成JSON。示例中的占位digest不是实际可运行镜像，按fixture-registry说明在seed流程替换。

## 4. 可自主解决与需要用户输入的事项

可自主：兼容稳定依赖解析、内部接口/可逆库选择、测试组织、按文档补充迁移、修复冲突；每次记录依据，不擅改产品范围。

需要事实输入时仅问缺少的内容：获批模型端点/凭据、可用Linux执行宿主、真实业务数据/授权、外部服务访问。缺模型可先完成stub测试；缺Linux不能把Windows/WSL未验证路径写成runsc验收通过。凭据不写进文档、仓库或日志。

必须重新讨论：实验证明底座无法满足核心契约、需要放宽权限/删除/审批、改成整仓fork、扩大首版产品范围。不要为了避免提问而静默降低安全/正确性要求。

## 5. 可直接给新会话的任务文本

> 请以本目录HANDOFF.md为入口，按已确认规格开发完整首版。先检查实际仓库与PROGRESS，按BOOTSTRAP和任务图从最早未完成且依赖满足的任务开始。保留现有修改，按需阅读对应规格，不依赖旧聊天、不重问已确认需求。未具备的外部条件记录为blocked，继续独立任务；所有通过结论附真实命令与证据。第一阶段完成可复现纵向切片，随后继续完整首版，不将mock能力或静态校验冒充生产验收。
