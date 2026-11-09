<!-- orchara-all-architecture:2026-11-08:map -->

# 全六项架构改进：总规格与范围地图

用户已确认需求 Q1–Q8、共同理解、测试入口及拆票。共 22 个工作切片：4 张复用 C1 票 + 18 张新增票。**当前仅 C1 有本地工具实现证据；其余重构尚未验收，本 Issue 保持 open。**

## Problem Statement

平台维护者需要完整落实原架构审核 HTML 中的六项改进，并准确判断哪些已设计、已任务化、已实现和已交付。此前只完成规格同步工具的本地改进，关闭了该项父票，后来又拆出四张开放票；其余五项仍是审核建议。这不能代表整份报告已经完成。

当前代码还存在几类维护成本：模型同步/流式与主/备调用分散编排生命周期；知识片段三条读取路径的正文完整性校验不一致；图执行借用假线性视图执行节点；真实验收支撑通过多层票号别名和转发复用；页面为旧测试加载方式保留第二套运行状态解释。修改共同规则时需要同时理解多个调用点，容易发生行为分歧。

## Solution

完整推进六项改进，每项形成设计、规格、任务、实现与真实验收证据。保留已确认的产品约束和既有业务入口，将重复规则集中到能隐藏复杂顺序的 module，让调用者少掌握内部知识。

规格同步继续复用已有成果并完成逐票核对；模型调用统一尝试生命周期；检索、引用与缓存统一读取并验证正文；Agent 和子应用调用通过显式执行上下文复用；验收支撑按真实能力组织；页面与测试都使用相同的运行状态规则。知识完整性增强和页面依赖失败处理按已确认 Q8、Q7 落实。

## User Stories

1. As a platform maintainer, I want the complete six-item review to have traceable deliverables, so that one completed item cannot be mistaken for completion of the report.
2. As a platform maintainer, I want specification checks to compare the current source, both managed copies and the baseline, so that outdated copies cannot pass as current.
3. As a platform maintainer, I want additions, changes and deletions in the source to be detected before synchronization, so that specification drift is visible.
4. As a reviewer, I want changes in either copy and inconsistent baselines to fail verification, so that matching edits cannot bypass the synchronization record.
5. As a platform maintainer, I want unavailable or incomplete sources to fail clearly, so that unverifiable input cannot appear valid.
6. As a platform maintainer, I want valid deletions to propagate while a missing critical source directory stops synchronization, so that intentional removal and incomplete input remain distinct.
7. As a platform maintainer, I want new copies prepared and verified before replacement, so that incomplete content cannot be published.
8. As a platform maintainer, I want recoverable failures to preserve or restore complete copies, so that a failed update does not damage the workspace.
9. As a platform maintainer, I want process interruption to be detected and recovered by an explicit synchronization rerun, so that mixed copies cannot silently pass.
10. As a platform maintainer, I want recovery to remain retryable and invalid evidence preserved, so that another failure does not destroy the last complete state.
11. As an automation maintainer, I want cooperating processes to use the same workspace lock, so that concurrent operations cannot race through replacement.
12. As a platform maintainer, I want path restrictions and existing normalization semantics preserved, so that synchronization remains safe and compatible across supported environments.
13. As a reviewer, I want the four existing synchronization tickets matched to code and evidence, so that a historical parent closure does not substitute for their acceptance.
14. As an application user, I want synchronous and streaming model calls to share the same attempt rules, so that delivery mode does not change authorization or budget enforcement.
15. As an application user, I want model candidates validated against the frozen application version and deployment configuration, so that undeclared or unavailable candidates cannot be silently used.
16. As an application user, I want rejected preflight calls to avoid fictitious physical attempts, so that the ledger reflects what was actually dispatched.
17. As an application user, I want every real attempt reserved and recorded atomically before dispatch, so that concurrent calls cannot overspend the budget.
18. As an application user, I want at most three primary attempts and one compatible fallback, so that retries remain bounded and explainable.
19. As an application user, I want permission, policy, Schema and tool-argument refusals to stop immediately, so that refusal cannot be bypassed by retry or fallback.
20. As an application user, I want cancellation before a call or during backoff to prevent further dispatch, so that stopped work does not start another request.
21. As an application user, I want cancellation during a call to stop without retry or fallback, so that the platform respects the stop request.
22. As an application user, I want stream consumer failure during retry or fallback notification to stop further requests, so that a disconnected consumer cannot trigger invisible work.
23. As an application user, I want failed primary reservations handled before evaluating fallback affordability, so that fallback uses the actually available budget.
24. As an application user, I want fallback candidates checked for compatibility and fixed-version membership, so that another model cannot silently change the declared behavior.
25. As an application user, I want each attempt settled using the actual candidate's price version, so that usage is charged consistently.
26. As an application user, I want real consumption retained when a complete response later fails platform validation or sealing, so that accounting does not erase an external cost.
27. As an application user, I want missing usage and ordinary business cancellation to retain their accepted accounting rules, so that a structural refactor does not invent new charges or zero-cost claims.
28. As an application user, I want each stream attempt isolated in its own generation with only complete tool calls accepted, so that failed drafts and partial calls cannot become a final answer.
29. As a platform maintainer, I want failed cleanup to be observable and committed success to survive final-frame delivery failure, so that the ledger is not misrepresented by transport errors.
30. As a knowledge-base user, I want retrieval, citation opening and cache hits to enforce the same current read permissions, so that access does not depend on the chosen path.
31. As a knowledge-base user, I want all three paths to verify sealed-body SHA-256 and recorded byte count, so that damaged content is never returned as valid.
32. As a knowledge-base user, I want same-length replacement, truncation and incorrect size metadata detected, so that partial integrity checks cannot conceal corruption.
33. As a knowledge-base user, I want missing or invalid integrity metadata to prevent unverified content from being returned, so that verification has a consistent meaning.
34. As a knowledge-base user, I want deletion and revoked access applied before content is served, so that cached data cannot bypass current restrictions.
35. As a knowledge-base user, I want the old active index readable during a rebuild or after a failed rebuild, so that maintenance does not unnecessarily remove working knowledge.
36. As a knowledge-base user, I want activation of a replacement index to invalidate superseded references, so that citations identify currently readable source facts.
37. As a knowledge-base user, I want opening an existing citation independent of the current embedding model, so that changing query infrastructure does not itself invalidate readable citations.
38. As a knowledge-base user, I want new retrieval candidates compatible with the current embedding model, so that ranking does not mix incompatible vectors.
39. As a knowledge-base user, I want unreadable retrieval candidates skipped without caching partial or empty results, so that temporary failure cannot pin an incomplete answer.
40. As a knowledge-base user, I want unreadable citations to return the existing safe failure, so that storage details and unauthorized content are not exposed.
41. As a knowledge-base user, I want transient cache validation failures to miss without permanently discarding valid entries, so that service recovery can restore cache use.
42. As a knowledge-base user, I want deterministic corruption or missing content to invalidate cached results, so that a miss cannot fall back to unchecked damaged bytes.
43. As a knowledge-base user, I want embedding availability checked before a cache hit, so that a cache does not hide an unavailable dependency.
44. As a knowledge-base user, I want citations revalidated before an answer is returned and final answers kept out of retrieval cache, so that source changes and personal context remain correctly handled.
45. As an application creator, I want Agent and child-application nodes to share their execution behavior across linear and graph workflows, so that the same node does not require different business implementations.
46. As an application user, I want each node to receive explicit allowed inputs and sealed facts, so that fake execution order cannot grant access to unavailable data.
47. As an application user, I want parallel nodes to receive stable execution facts, so that one branch cannot mutate another branch's input namespace.
48. As an application creator, I want child applications called with fixed published versions and explicit bindings, so that refactoring does not broaden delegation.
49. As an application user, I want distinct logical child calls to remain distinct even when using the same version, so that legitimate work is not incorrectly deduplicated.
50. As an application user, I want node success to require validated and registered outputs, so that an execution return value alone cannot authorize artifacts.
51. As an application user, I want nodes to return local success, failure, cancellation or waiting decisions, so that the owning scheduler controls global execution.
52. As an application user, I want graph cancellation and failure to drain started siblings before final submission, so that a terminal proposal does not race ongoing work.
53. As an application user, I want waiting decisions to preserve checkpoints and required evidence, so that resumption remains safe.
54. As an application user, I want optional failures, skipped nodes and join activation to retain their accepted rules, so that graph behavior remains unchanged.
55. As an application user, I want recovery to reuse valid sealed node outputs without inventing completed nodes, so that replay is both safe and efficient.
56. As a platform maintainer, I want graph execution to stop depending on a fake linear view, so that scheduler ownership is explicit in code and tests.
57. As a test maintainer, I want one capability-named Stack definition, so that real infrastructure fields do not depend on ticket alias chains.
58. As a test maintainer, I want a shared operation for one real supervisor execution, so that callers do not repeat process and finalizer ordering.
59. As a test maintainer, I want existing process lifetimes and fixture isolation preserved, so that reorganizing helpers does not change the experiment.
60. As a test maintainer, I want environment overrides confined to the intended child process, so that tests do not contaminate one another.
61. As a test maintainer, I want startup, timeout, exception and teardown paths to reap processes, so that a failed test does not leave orphan writers.
62. As a test maintainer, I want supervisor reports distinguished from committed Run state, so that a proposed result cannot masquerade as finalization.
63. As a test maintainer, I want shared reads for database, SSE and sealed outputs while retaining independent expectations, so that helper reuse does not make assertions circular.
64. As a test maintainer, I want ticket-specific fixtures, graphs and business assertions preserved, so that shared support does not erase meaningful scenarios.
65. As a test maintainer, I want temporary aliases removed after callers migrate, so that the final architecture contains one real source for each shared capability.
66. As a reviewer, I want actual execution and cleanup evidence in addition to test collection, so that import success cannot be mistaken for runtime acceptance.
67. As an application user, I want chat, run detail and debug pages to interpret the same Run facts, so that the page does not change the meaning of execution state.
68. As an application user, I want finalizing shown as a phase awaiting evidence, so that a proposed result is not displayed as committed success.
69. As an application user, I want an accepted cancellation shown as intent until stopping is confirmed, so that the page does not promise rollback of external work.
70. As an application user, I want unknown external writes shown as awaiting reconciliation, so that the page does not encourage blind replay.
71. As an application user, I want observation to continue after a platform request is dispatched, so that later Run state changes remain visible.
72. As an application user, I want terminal state, child progress and artifact readiness interpreted together, so that partial outputs cannot be presented as the final result.
73. As an application user, I want expired event cursors to trigger safe snapshot rebuilding, so that history loss is disclosed rather than hidden.
74. As an application user, I want snapshot recovery restricted to approved same-origin Run paths, so that a supplied URL cannot redirect recovery elsewhere.
75. As an application user, I want expired cursors discarded without replaying invented history, so that the current view does not claim a complete timeline.
76. As an application user, I want late responses from a previous selection ignored, so that switching Runs does not restore stale content.
77. As an application user, I want current authentication, missing-resource and transient-stream handling preserved, so that refactoring does not create uncontrolled polling.
78. As an application user, I want a missing or broken shared-state script to show “运行状态暂不可用”, so that dependency failure is explicit.
79. As an application user, I want affected realtime status and related operations paused during dependency failure, so that the page does not act using incomplete state rules.
80. As an application user, I want navigation, reload and independent page functions retained during that failure, so that recovery remains possible.
81. As a test maintainer, I want page harnesses to load the real shared dependency, so that isolated tests exercise the same rules as served pages.
82. As a platform maintainer, I want all duplicate Run-state fallbacks removed from pages and run views, so that fixing one rule fixes every caller.
83. As a reviewer, I want real browser asset-failure checks distinguished from Node logic checks, so that an isolated harness is not presented as browser acceptance.
84. As a reviewer, I want every original review item mapped to design, specification, tickets, code and evidence, so that no item silently drops out of delivery.
85. As a reviewer, I want existing synchronization tickets reused, so that the broader program does not create duplicate work or rewrite historical evidence.
86. As an implementer, I want tickets with only genuine blocking dependencies, so that independent complete paths can proceed without artificial serialization.
87. As a reviewer, I want local implementation, committed code and remote integration reported separately, so that issue closure cannot conceal unintegrated changes.
88. As a reviewer, I want unavailable environments and unrun experiments recorded explicitly, so that passing local tests cannot imply real-provider or production acceptance.
89. As a platform maintainer, I want a final six-item integration review with no orphan compatibility layers, so that the architecture improvements are complete and maintainable.

## Implementation Decisions

- **C1：**复用已确认的完整规格与现有两个操作。统一规范化清单、来源/双副本/基线比较、受保护切换、进程互斥、回滚与显式恢复均保留。路径限制、关键来源检查、换行与二进制语义不放宽。既有四票用于核对和补齐，不另造平行实现。
- **C2：**保留同步与流式外部 interface，共用私有 operation session 和统一尝试驱动。同步完整响应与流式 generation 是有意义的内容处理差异，不另建公开执行计划或任意可配置的重试/计费 policy。
- **C2：**真实物理调用前完成候选门禁与原子预留。重试与 fallback 分别判断；主三次、备一次上限固定。每次使用实际候选费率；清理失败明确可观察。内容已完整产生后的消费结算、缺失 usage、已封存成功后的投递失败按已接受规则处理。
- **C2：**普通业务取消与兼容验证取消探针保持原有记账区别，job 与 run 两种账本不合并。凭据只由现有 adapter 解析，不进正文、投影、错误或日志。
- **C3：**在现有共享权限谓词上深化可读片段 module。请求内限定 owner，支持检索候选、引用打开、缓存复检；授权决定不跨请求缓存。每路都验证当前权威行、原始正文摘要和记录字节数。
- **C3：**使用已有 artifact metadata，无需新增 schema。候选与批量复检继续有界；同次读取计算摘要与长度。不将坏对象重新登记为“正确”来绕过校验。保留既有错误码/缓存原因及瞬态保留策略。
- **C3：**活动索引与最新源版本不同是合法情况；重建失败仍读旧活动索引。来源删除组合风险先复现并按既有即时不可读约束修正，不能以收紧最新版本条件替代删除治理。embedding、缓存键、排序、最终答案复检保持自身职责。
- **C4：**Agent 与子应用调用使用显式节点执行事实及 typed outcome。图/线性各自从权威调度事实构造输入，节点返回局部决定和证据。节点没有全局终态、跳过兄弟或排空的权限。
- **C4：**移除假线性视图及图专用覆盖，目标节点不通过隐藏 context 判断调度模式。图单 owner、optional、skipped、join、waiting、恢复和固定子版本规则保持；不重写全部节点或切换执行运行时。
- **C5：**按 Stack、一次执行、finalizer 生命周期、事实观察等真实能力组织 support。保持每次执行的现有生命周期和 pytest isolation；先 expand，再按调用者批次迁移，最后 contract 删除别名/转发。专用场景、独立期望和报告归属保持。
- **C6：**深化现有共享运行状态 module，提供聚合 view、流失败分类和快照修正。DOM、请求选择和既有传输生命周期留在页面 adapter，保留纯规则的可测试性，不引入新网络观察框架。
- **C6：**页面启动先验证共享依赖完整；失败按 Q7 明确提示并暂停受影响观察/操作，保留导航和重载。缺依赖不再执行第二套状态规则。既有同源快照、410、取消意图、unknown、迟到响应和轮询上限不变。
- **C6：**迁移全部受影响 harness 与页面调用者，再删除重复状态解释和兼容导出。节点表、页面 DOM、输入形状与浏览器能力检测仍按其职责保留，不按关键字机械删除。
- **整体：**保留现有业务契约与任务 ID，不预设新的迁移或端点。若实际实现确需改变生成契约，先修改权威生成源并重建、同步、验证，不手工改变复制件。本次工程选型均为可逆内部组织，不新增业务词义或重开既有 ADR 决定。

## Testing Decisions

- **原则：**使用最高的既有业务 seam，断言外部可观察结果。私有故障注入只控制依赖，验收仍跨真实 interface；不新增生产故障端点，不以 helper 名称或内部调用次数证明正确。旧内部形状测试在结果覆盖充分后替换，安全/故障行为回归保留。
- **C1：**现有同步与检查命令为外部 seam；同 module 函数作定点回归。观察实际双目录、基线、诊断与退出码；真实子进程中断与普通异常分开。沿用既有同步工具测试及完整 C1 规格矩阵。
- **C2：**通过现有 capability 同步/流式调用及真实 HTTP/mTLS 链路，核对实际物理调用、PG 预算/attempt/operation、generation 和封存字节。用已有 stub 与 loopback fake provider 覆盖协议和失败；真实供应商单列，缺条件明确 blocked。
- **C2：**先复现同步/流式 adapter 类错误路由差异、通知 emitter 失败、尝试前/退避取消与账本清理失败，再收敛实现。覆盖重放零派发、拒绝、三主一备、预留释放、真实费率、缺 usage、完整响应后失败消费、半流/工具片段及 final 投递失败。先例为 T044/T045/T046/T122。
- **C3：**从既有检索、引用、回答端点做 cache on/off 对照，使用真实 PG 和对象字节；定点 reader 测试补分类。覆盖同长度篡改、截断、大小不符、非法 metadata、对象缺失、瞬态恢复、权限/删除、旧索引重建失败、新索引激活、回答返回前失效。先例为 T061/T064/T067/T069/T113。
- **C3：**验证源 A 活动、替换 B 重建中或失败、再删除 A 的组合路径。报告实际结果，不把静态差异写成已确认攻击链。记录校验成本和缓存成对对照，沿用现有粗性能护栏，不用脆弱毫秒断言代替功能正确性。
- **C4：**通过既有 Run 创建/领取/执行/恢复链路观察节点事实、子运行、checkpoint、兄弟排空和唯一提交；定点测试沿用节点执行 seam 的真实测试 adapter。覆盖线性/图、optional、取消、waiting、join、封存复用、顺序与并行固定版本调用。Go 实际执行与 Python 独立语义证明分别记录。
- **C5：**记录并复核真实启动参数、关键环境配置、退出/超时清理、DB/SSE 与封存事实。测试收集仅是基础检查。代表真实执行在迁移批次中运行，最终搜索确认目标别名/纯转发调用者清零；独立断言不共享“期望生成器”。
- **C6：**同一共享脚本字节的 Node 行为测试、真实服务 HTML/加载顺序检查、实际浏览器资源 404/语法错误三类证据分别记录。正常矩阵覆盖 finalizing、cancel、unknown、子运行/产物、410 安全路径、dispatched 观察、迟到响应。故障矩阵观察提示、相关按钮/观察暂停、导航和重载可用。先例为 T096 与旧页面 harness。
- **整体：**逐票记录适用代码基线、环境、真实命令、原始输出与限制；最终运行受影响套件、Go 检查、同步门禁及产品集成回归。未运行、部分、失败和 blocked 均如实报告；没有以单元、静态、stub 或浏览器 shim 证明全部平台能力。

## Out of Scope

- 把六项改进扩展为新产品功能、供应商协议、公开插件框架或整体前端迁移。
- 改变权限、删除治理、审批、固定版本、预算/取消、图调度权威等已确认政策。
- 将业务取消改成兼容探针的记账政策，或合并 job/run 账本。
- 新增通用仓储 port、通用执行计划语言、共享网络观察框架，或让测试 helper 成为产品 interface。
- 通过修改期望、削弱校验、保留第二套业务 fallback 或默认为成功来“修复”测试。
- 以本地验收声称真实供应商、生产部署、物理断电、远程文件系统或完整首版的验收通过。
- 自动关闭历史父票、改写历史证据、创建重复 C1 拆票，或未经实际集成声称远端代码已经更新。

## Further Notes

- C1 既有[完整规格及 42 条故事](https://github.com/xiaolin624/Orchara/issues/137#issuecomment-6719314337)继续有效，其已发布版本见 [#137 规格评论](https://github.com/xiaolin624/Orchara/issues/137#issuecomment-6719314337)。本总规格中的 C1 汇总不删减该范围。
- 全部六项都属于当前实施目标；C2–C6 已有产品功能，未实施指本次架构改进。既有功能/测试不能自动成为新重构的完成证据。
- 本轮只完成需求确认、设计比较和规格/拆票草拟，没有运行新业务验收，没有提交或推送实现。历史 C1 证据继续标注适用范围。
- 本 Issue 为全六项总规格和地图入口，范围切片见下方完整清单。C1 原父子关系保留，其余按 ready-for-agent 发布；父子和阻塞关系使用正文链接回退，不宣称原生关系。
- 用户已回答“同意”，确认沿用既有验收入口，以及 18 张新增票、4 张 C1 复用票的粒度和依赖；本次发布依该批准执行。此确认不等于代码实现或测试通过。



## 范围地图与全部工作切片

- [ ] [C1.01 #138](https://github.com/xiaolin624/Orchara/issues/138)（复用，保留原父 #137）
- [ ] [C1.02 #139](https://github.com/xiaolin624/Orchara/issues/139)（复用，保留原父 #137）
- [ ] [C1.03 #140](https://github.com/xiaolin624/Orchara/issues/140)（复用，保留原父 #137）
- [ ] [C1.04 #141](https://github.com/xiaolin624/Orchara/issues/141)（复用，保留原父 #137）
- [ ] [C5.01 #142 — 共享真实 Stack 并迁移票号类型别名的调用者](https://github.com/xiaolin624/Orchara/issues/142)
- [ ] [C5.02 #143 — 通过共享能力执行一次真实 supervisor 尝试并回收资源](https://github.com/xiaolin624/Orchara/issues/143)
- [ ] [C5.03 #144 — 共享运行事实读取并迁移图验收调用者](https://github.com/xiaolin624/Orchara/issues/144)
- [ ] [C5.04 #145 — 删除已迁移的测试别名与纯转发入口](https://github.com/xiaolin624/Orchara/issues/145)
- [ ] [C2.01 #146 — 同步主调用与兼容 fallback 共用模型尝试生命周期](https://github.com/xiaolin624/Orchara/issues/146)
- [ ] [C2.02 #147 — 流式主调用与 fallback 接入同一尝试驱动](https://github.com/xiaolin624/Orchara/issues/147)
- [ ] [C2.03 #148 — 闭环四条模型路径的取消、清理失败与结算回归](https://github.com/xiaolin624/Orchara/issues/148)
- [ ] [C3.01 #149 — 普通知识检索统一读取并验证正文摘要与字节数](https://github.com/xiaolin624/Orchara/issues/149)
- [ ] [C3.02 #150 — 引用、回答和缓存复检接入同一已验证读取](https://github.com/xiaolin624/Orchara/issues/150)
- [ ] [C3.03 #151 — 验证重建、来源删除与损坏恢复的知识读取一致性](https://github.com/xiaolin624/Orchara/issues/151)
- [ ] [C4.01 #152 — Agent 节点通过显式上下文复用图与线性执行](https://github.com/xiaolin624/Orchara/issues/152)
- [ ] [C4.02 #153 — 固定子应用调用接入共享节点执行约定](https://github.com/xiaolin624/Orchara/issues/153)
- [ ] [C4.03 #154 — 删除假线性视图并闭环节点调度恢复回归](https://github.com/xiaolin624/Orchara/issues/154)
- [ ] [C6.01 #155 — 页面显式加载共享状态依赖并处理资源失败](https://github.com/xiaolin624/Orchara/issues/155)
- [ ] [C6.02 #156 — 对话和节点视图采用统一运行状态投影](https://github.com/xiaolin624/Orchara/issues/156)
- [ ] [C6.03 #157 — 运行详情统一子运行、产物与 410 快照恢复解释](https://github.com/xiaolin624/Orchara/issues/157)
- [ ] [C6.04 #158 — 清理共享状态兼容出口并验收三页面正常与故障路径](https://github.com/xiaolin624/Orchara/issues/158)
- [ ] [ALL.01 #159 — 集成六项架构改进并闭环原报告覆盖与证据](https://github.com/xiaolin624/Orchara/issues/159)

## 来源与完成证据

原审核 HTML 与 JSON 已原样归档；HTML SHA-256 为 a5e76e29d584bbd6bf27e517e1b347f1113328a41d9b0e6af0a7f7730e675fb5。临时 HTML 是原始审核快照，当前完成情况以各票实际证据为准。

全部叶子票及 ALL.01 验收前不宣告六项完成。保留已有任务 ID、ADR 与平台门禁；新规格/设计/票据不等于已实现。代码当前仍位于本地工作区，未提交、未推送、未部署。历史 #137 状态不改写。
