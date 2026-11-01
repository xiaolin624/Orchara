# 需求与验收追踪

源头为 DESIGN-SESSION 的真实答复。表中“覆盖”表示有规格和实施任务，不表示已经实现。所有运行验收当前为 not_run。

| 已确认项 | 规格位置 | 实施/验收 |
|---|---|---|
| Q1平台、Q2单企业私有化、Q3首周切片 | PRODUCT、ARCHITECTURE、OPERATIONS | F15、D1–D7 |
| Q4普通业务创作、Q5定义/运行分离、Q7画布 | UX、GLOSSARY、WORKFLOW-SPEC | F02/F04、未参与用户任务观察 |
| Q6脚本上传、Q8Python工具、Q9权限/资源 | SECURITY、WORKFLOW-SPEC | P0-3/F05、安全负例 |
| Q10外层图/Agent、Q16DAG、Q29十节点 | ARCHITECTURE、WORKFLOW-SPEC | P0-5/F04、分支用例 |
| Q11销售样例 | examples/sales、EVALUATION | D3–D7、独立Decimal真值 |
| Q12版本、Q27固定依赖 | ARCHITECTURE、EXECUTION | F02/F05、旧图恢复 |
| Q13Web附件、Q25Request/Run | API、STATE-MACHINES | F03、幂等/交互与Run关系 |
| Q14角色/审核、Q15共享与正文 | SECURITY、DATA-MODEL | F01/F02/F11、跨用户拒绝 |
| Q17全局路由、Q22歧义、Q24能力卡 | ROUTING-MEMORY-KNOWLEDGE | F08、拒识/候选/未见应用 |
| Q18个人记忆、Q28组织知识 | ROUTING-MEMORY-KNOWLEDGE | F09/F10、来源/确认/删除 |
| Q19串行会话、Q21跨应用、Q23一个主应用 | STATE-MACHINES、EXECUTION、UX | F03/F08、队列与交接 |
| Q20Linux部署 | OPERATIONS | P0-3/F15、干净机器复现 |
| Q26子应用 | EXECUTION、WORKFLOW-SPEC | F07、并行隔离与死锁用例 |
| Q30审批、Q31错误恢复 | STATE-MACHINES、EXECUTION | F03/F06、故障用例 |
| Q32本地账号、Q33容量画像 | SECURITY、EVALUATION | F01/F13、替身与真实模型分测 |
| Q34备用模型、Q35缓存 | EXECUTION、ROUTING-MEMORY-KNOWLEDGE | F06/F10、降级/缓存消融 |
| Q36Go/VueFlow、Q37Python整图 | ARCHITECTURE、SYSTEM-BOUNDARIES | P0-1/2、F04 |
| Q38离线微调 | TRAINING-EXPERIMENT、EVALUATION | F14、同基模/未见任务/退化 |
| Q39诊断正文、Q40删除 | DATA-POLICY、SECURITY、OPERATIONS | F11、删除与恢复 |
| Q41模型目的地、Q42OpenSandbox/gVisor | SECURITY、OPERATIONS | P0-3/F06、网络隔离与提示 |
| Q43发布门禁、Q44服务账号REST/SSE | EVALUATION、API、contracts/openapi.json | F02/F12、权限/接口契约 |

详细推荐默认值见 REVIEW；其变更需记录原因，不能把尚未实测的容量目标当已确认性能事实。

## v0.2用户追加要求

| 要求 | 交付 | 状态 |
|---|---|---|
| 全平台到组件的复用拆解 | OPEN-SOURCE-REUSE、四份新增research | 静态研究已完成，运行未验证 |
| Agent评测和测试集 | TESTING-AND-DATASETS、evaluation-case Schema与正反例 | 契约已写，框架未运行 |
| GitHub本地准备清单 | COMPONENTS-AND-CLONE、components.json、可选克隆脚本 | 未执行克隆 |
| 最终结论同步开发方案 | 架构/RAG/评测/任务/安全/运维及REVIEW | 已同步，Q45/Q46已确认；运行未验证 |

| 已确认决策 | 实施位置 | 验收 |
|---|---|---|
| Q45 PG记忆基线＋Mem0门禁对照 | F09/F11、C04、ADR0015 | 确认、隔离、召回/成本、全部正文副本删除 |
| Q46 分层测试＋一个通用质量适配器 | F13、C05/C06、ADR0016 | hard checker独立、空验拒绝、judge数据去向与成本 |

## v0.3全新会话接手要求

用户要求文档脱离历史聊天即可开始完整开发。交付HANDOFF/AGENTS、BOOTSTRAP/PROGRESS、任务DAG和关键旅程矩阵，补齐账号/模型/评测/后台任务/终态与不可变上传闭环；原始发现见DOCUMENT-AUDIT。静态验证及隔离目录复制检查通过，业务实现和P0仍未运行；不以文档审阅代替实际验收。
