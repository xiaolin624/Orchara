# 六项架构改进：拆票与发布记录

日期：2026-11-08。需求与共同理解已确认；用户已回复“同意”，通过测试 seam、粒度和依赖审核。[设计](2026-11-08-all-architecture-design.md)已选型，[完整规格](2026-11-08-all-architecture-spec.md)已发布至 [#160](https://github.com/xiaolin624/Orchara/issues/160)。**18 张新票 #142–#159 发布并回读核验；发布不等于完成这些实现。**

## 范围和票数

复用 C1 四张已有票，新增 **18 张实施/整合票，共 22 张工作切片**。另建立全六项总规格/地图 Issue #160，作为范围入口，不计入实施票数，不用 #137 充当全报告父票。

每张新增票均有[独立正文](all-architecture-tickets/)和验收清单；[机器可读依赖与故事映射](all-architecture-tickets/plan.json)已填入真实 Issue 编号，[发布核验记录](all-architecture-tickets/publication.json)保留逐票远端正文。C2.01 等 ID 是稳定工程标识，不替代原产品任务图 ID。

| 原报告项 | 已发布工作票 |
| --- | --- |
| C1 | #138、#139、#140、#141（复用） |
| C2 | #146、#147、#148 |
| C3 | #149、#150、#151 |
| C4 | #152、#153、#154 |
| C5 | #142、#143、#144、#145 |
| C6 | #155、#156、#157、#158 |
| 全六项整合 | #159 |

## C1 已有票：复用而非重建

本轮经 GitHub connector 回读 #137–#141 的正文和全部评论：#137 closed；#138–#141 均 open、ready-for-agent，无新评论。远端未作任何修改。四票验收沿用已批准正文及完整 C1 规格；历史本地通过结果逐票核对，不自动勾选。

| 现有票 | 内容 | Blocked by | 总规格故事 |
| --- | --- | --- | --- |
| [#138](https://github.com/xiaolin624/Orchara/issues/138) | C1.01 当前源/复制件/基线检查 | 无 | 2–5、12 |
| [#139](https://github.com/xiaolin624/Orchara/issues/139) | C1.02 安全同步与回滚 | #138 | 6–8、12 |
| [#140](https://github.com/xiaolin624/Orchara/issues/140) | C1.03 进程中断检测与恢复 | #139 | 9–10 |
| [#141](https://github.com/xiaolin624/Orchara/issues/141) | C1.04 并发及跨平台行为 | #139 | 11–12 |

## 已发布切片（保留获批拆分）

1. **[C5.01 — 共享真实 Stack 并迁移票号类型别名的调用者](all-architecture-tickets/01-canonical-stack.md)**  
   **Blocked by：** 无，可立即开始。**交付：** 测试维护者可直接使用按能力命名的真实 Stack，原有 API、证书、数据库、对象目录与签名配置保持一致；首批调用者不再依赖多层票号类型链。

2. **[C5.02 — 通过共享能力执行一次真实 supervisor 尝试并回收资源](all-architecture-tickets/02-execution-support.md)**  
   **Blocked by：** C5.01。**交付：** 现有执行验收通过一个按能力命名的操作完成真实 finalizer 与 supervisor 的一次执行，正常、失败和超时路径都能观察结果并回收进程。

3. **[C5.03 — 共享运行事实读取并迁移图验收调用者](all-architecture-tickets/03-run-observations.md)**  
   **Blocked by：** C5.01。**交付：** 图节点等既有验收直接读取共享的节点、封存产物和 SSE 事实，仍用各场景的独立期望判断结果。

4. **[C5.04 — 删除已迁移的测试别名与纯转发入口](all-architecture-tickets/04-support-contract.md)**  
   **Blocked by：** C5.01、C5.02、C5.03。**交付：** 仓库测试直接依赖能力入口，目标票号类型链和纯转发已彻底移除，测试仍能真实执行并清理。

5. **[C2.01 — 同步主调用与兼容 fallback 共用模型尝试生命周期](all-architecture-tickets/05-model-complete.md)**  
   **Blocked by：** 无，可立即开始。**交付：** 使用者通过现有同步 capability 调用完成主候选及兼容 fallback，共用门禁、预留、尝试开闭与结算规则，输出和账本一致。

6. **[C2.02 — 流式主调用与 fallback 接入同一尝试驱动](all-architecture-tickets/06-model-stream.md)**  
   **Blocked by：** C2.01。**交付：** 流式使用者得到与同步一致的生命周期规则，每个真实尝试独立 generation，失败草稿和半条工具调用不会混入最终结果。

7. **[C2.03 — 闭环四条模型路径的取消、清理失败与结算回归](all-architecture-tickets/07-model-cancellation-contract.md)**  
   **Blocked by：** C2.02。**交付：** 调用者在派发前、调用中、退避或流式通知期间停止时，四条模型路径都停止新请求；清理失败不会伪装为账本已关闭。

8. **[C3.01 — 普通知识检索统一读取并验证正文摘要与字节数](all-architecture-tickets/08-verified-retrieval.md)**  
   **Blocked by：** 无，可立即开始。**交付：** 知识库使用者通过现有检索得到当前允许读取且正文完整的片段，坏片段被跳过，部分或空结果不被缓存。

9. **[C3.02 — 引用、回答和缓存复检接入同一已验证读取](all-architecture-tickets/09-verified-citations-cache.md)**  
   **Blocked by：** C3.01。**交付：** 打开引用、返回答案和使用缓存都经过同一正文完整性标准；缓存瞬态故障可恢复，损坏内容不会从 miss 路径重新返回。

10. **[C3.03 — 验证重建、来源删除与损坏恢复的知识读取一致性](all-architecture-tickets/10-knowledge-rebuild-deletion.md)**  
   **Blocked by：** C3.02。**交付：** 旧活动索引在合法重建期间可读，但删除来源、替换索引或正文损坏后，各读取路径都按当前事实处理。

11. **[C4.01 — Agent 节点通过显式上下文复用图与线性执行](all-architecture-tickets/11-explicit-agent-execution.md)**  
   **Blocked by：** 无，可立即开始。**交付：** 图与线性工作流的 Agent 节点从明确允许的输入与封存事实执行，返回局部决定，由各自调度器处理全局结果。

12. **[C4.02 — 固定子应用调用接入共享节点执行约定](all-architecture-tickets/12-explicit-callapp-execution.md)**  
   **Blocked by：** C4.01。**交付：** 图与线性 call_app 都通过显式输入调用固定发布版本，返回局部等待或结果，独立逻辑调用不会误合并。

13. **[C4.03 — 删除假线性视图并闭环节点调度恢复回归](all-architecture-tickets/13-node-view-contract.md)**  
   **Blocked by：** C4.02。**交付：** Agent/call_app 的复用不再依赖图伪装线性结构，调度器仍准确处理跳过、等待、排空、恢复和唯一终态。

14. **[C6.01 — 页面显式加载共享状态依赖并处理资源失败](all-architecture-tickets/14-page-dependency-guard.md)**  
   **Blocked by：** 无，可立即开始。**交付：** chat、运行详情、debug 的共享状态脚本缺失或损坏时，用户看到明确提示，受影响功能暂停，仍可导航和重新加载。

15. **[C6.02 — 对话和节点视图采用统一运行状态投影](all-architecture-tickets/15-chat-runview-state.md)**  
   **Blocked by：** C6.01。**交付：** 对话和节点视图使用共享状态解释，dispatched 后仍能看到运行变化，取消/收尾不再因页面旧分支显示成完成。

16. **[C6.03 — 运行详情统一子运行、产物与 410 快照恢复解释](all-architecture-tickets/16-runs-state-recovery.md)**  
   **Blocked by：** C6.01。**交付：** 运行详情的状态、子任务、产物和事件恢复按共享事实解释，过期历史与安全快照路径清楚可见。

17. **[C6.04 — 清理共享状态兼容出口并验收三页面正常与故障路径](all-architecture-tickets/17-page-state-contract.md)**  
   **Blocked by：** C6.02、C6.03。**交付：** 三类运行页面与测试仅使用同一状态规则，缺依赖时明确暂停，全部目标旧 fallback 和兼容导出已清理。

18. **[ALL.01 — 集成六项架构改进并闭环原报告覆盖与证据](all-architecture-tickets/18-six-item-integration.md)**  
   **Blocked by：** #140、#141、C5.04、C2.03、C3.03、C4.03、C6.04。**交付：** 维护者能从整份原报告追踪六项设计、规格、任务、代码和验收，明确哪些本地完成、已经提交或远端集成。

## 阻塞与粒度说明

- C5 是 expand–migrate–contract：C5.01 扩展规范 Stack 并迁移类型调用者；C5.02/C5.03 分别迁移执行与事实观察，可并行；C5.04 在所有迁移批次后删除兼容。不是先建空抽象再把实现留到最后。
- C2/C3/C4/C6 都按可真实调用和验收的完整路径拆分，每票有业务结果、必要测试与证据。涉及 UI/Schema 的改动只在实际需要时纳入，不为“纵向”而发明无关页面或迁移。
- C2–C6 不被整个 C5 链人为阻塞；既有 support 仍可执行，若某票确实引用新能力才按实际情况增加依赖。共享文件冲突由工程集成处理。
- C2/C3/C4 的最终票包含有明确风险的行为验证及必要修正，兼容实现和重复代码的删除可观察，不是只有“跑一次测试”的空票。
- C6.01 先 expand 显式依赖及守卫并迁移 harness；C6.02/C6.03 可独立迁移两组调用者；C6.04 contract 并验收三页。
- ALL.01 依赖各组叶子及 C1 #140/#141；后两张已传递覆盖 #138/#139，不重复加无意义阻塞。整体报告不能只因某个父票关闭而通过。

## 发布方式与完成口径

用户已审核通过。为让新总规格第一次创建就包含全部真实子票链接，同时遵守不修改父票的要求，本轮先按依赖序创建 18 张工作票，再创建含完整规格与 22 项清单的 #160，最后只在新增工作票正文补入 Part of #160。所有阻塞 ID 均已替换为真实 Issue 编号；C1 原父子关系保留。#160 创建后未修改正文、状态或评论，#137 及既有四张拆票亦未改动。

当前 GitHub connector 未暴露原生 sub-issue / blocking 关系写入能力；按仓库约定使用正文 Part of / Blocked by 链接作为回退，明确不宣称已建立原生关系。本轮总规格 #160 已实际具有 wayfinder:map 与 ready-for-agent 标签。

按 to-tickets 要求，发布阶段**不关闭或改写既有父票**。实现阶段才按实际证据逐票处理，保留本地实现、提交和远端集成状态；不把 ready-for-agent 当 passed。

## 本次审核与发布结果

1. 用户“同意”沿用这些测试入口：C1 现有命令；C2 capability 同步/流式；C3 检索/引用/回答；C4 Run 执行/恢复；C5 真实 supervisor/资源清理；C6 实际页面与共享脚本。定点 Go/Node 测试补充，不替代真实账本、进程或浏览器证据。
2. 用户“同意”18 张新增票加 4 张复用票的粒度及阻塞关系，无合并或进一步拆分要求。本轮核验 19 张新票正文与预期一致，18 子票均为 open / ready-for-agent、父链接与全部阻塞引用完整；历史 C1 五票正文、状态、标签保持原样。

to-spec 的测试 seam 和 to-tickets 的拆票审核门禁均已满足；本轮已依批准发布，后续无需再次确认相同范围。

## 本轮检查与限制

实施进展补记：#142 Stack、#143 执行支撑已分别完成本地实现与票级验收并附证据关闭；代码未提交/推送。C5 的 #144/#145、其他架构项及整体 #159/#160 均未因此完成。发布时状态和原始正文保留于 publication.json，最新实现进度以产品 PROGRESS 与逐票证据为准。

本记录对应的发布阶段完成设计、89 条故事的规格、独立票正文与远端核验；未因此声称 C2–C6 运行实现、业务测试、代码提交/推送/部署完成。发布验收清单均未勾选，实际实施证据随后按票更新。原报告源文件已归档；文档存在不表示对应重构完成。

