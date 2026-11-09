# 六项架构改进：interface 设计与迁移方案

日期：2026-11-08。基线：`6141328beaf12568a594095aef9dbc4a1ff2dc21` 及当前 C1 工作区改动。

需求依据：[全六项需求与已确认决策](2026-11-08-all-architecture-grilling.md)。Q1–Q8 和共同理解均已确认。本文完成工程方案选择，**不代表 C2–C6 已实现或验收**。后续完整规格和拆票另行追溯。

## 设计方法与全貌

按 codebase-design 的深 module、deletion test、interface 即测试表面原则设计。依据 DESIGN-IT-TWICE，三个新上下文分别探索最小 interface、扩展能力、最常见调用者三个方向；每个方向均核对 C2–C6 的真实代码。以下依次展示方案，再给工程选择；可逆的内部设计不重新访谈产品需求。

| 项 | 选择的 module / interface | seam 与真实依赖 | 必须删除的知识或重复 |
| --- | --- | --- | --- |
| C1 | 既有 `check` / `sync` | CLI；真实目录与临时目录，同一个 filesystem adapter | 调用者自行拼来源、双副本、基线与恢复顺序 |
| C2 | 保留 `Invoke` / `Stream`；私有统一模型尝试驱动 | 现有 capability 入口；PG、对象目录、Provider / StreamProvider | 四套候选、预留、attempt、重试、fallback、关闭与结算编排 |
| C3 | 按 owner 限定的可读片段 module；候选、单片段、缓存复检三个操作 | 现有检索/引用/回答入口；PG 与 objectstore | 三条路径各自组合 metadata SQL、对象读取和完整性校验 |
| C4 | 显式节点执行输入，返回节点决定与证据 | 现有节点执行 seam；Client、沙箱、封存输出 | `graphLinearView`、图专用线性覆盖字段、目标节点执行中的隐式调度模式判断 |
| C5 | 规范 Stack、一次执行、事实观察等能力 module | 现有真实栈 fixture 与 supervisor 操作 | 多层票号别名、纯转发、共享能力藏在某张票文件里的顺序知识 |
| C6 | 纯运行状态 module，聚合投影、失败分类、快照修正 | 现有页面；真实共享脚本、页面 DOM adapter | 页面与 runview 的第二套运行状态规则，以及旧 harness 触发的业务 fallback |

候选、预算、封存、节点状态等 engineering 结构沿用业务定义，不新增业务术语。未改现有 ADR 的权威与业务政策；可逆内部设计无需新增 ADR。

## C1：复用已选方案，补齐票级闭环

已有[三案比较与已实现方案](2026-11-08-spec-sync-design.md)选择保留两个操作，将内容清单、核验、切换、回滚、恢复与锁隐藏在 implementation。继续使用既有 CLI；检查不修复，显式同步才恢复。

不重复建票或重写已有代码。把现有实现与证据映射到 #138–#141，各票依实际验收记录闭环。#137 的历史关闭不等于新拆票验收完成，也不等于整份报告完成。完整 pytest 与有限 WSL 冒烟仍分开记录。

## C2：模型尝试生命周期

### 三种 interface

**A：单一执行函数。** `executeModel(ctx, input, emit)`；空 emit 表示同步。一次调用隐藏授权、幂等、候选、预留、重试、fallback、结算。interface 小，但空值同时承担执行模式选择；会把本来有意义的同步/流式调用区别埋入一个大分支。

**B：显式执行计划。** 先产生受验证的候选与尝试计划，再由 driver 执行。可扩展性好，也便于展示计划；代价是计划失效、重新授权、调用顺序和公开策略成为调用者要学的 interface。当前没有独立计划消费者，不公开任意重试或计费策略。

**C：面向既有调用者的 operation session。** 外部继续 `Invoke` / `Stream`，私有 session 持有已准备 operation、候选事实和内容处理方式，共用一个尝试驱动。调用者无需传 nil 选择模式，也不学习 Begin/Reserve/Settle 顺序。

**选择 C，吸收 A 的单驱动。** session 是私有实现结构，不新增公开类型或新账本 abstraction。已有授权、operation 幂等、`beginModelAttempt` / `finishModelSuccess` 继续复用。沿用 Provider / StreamProvider，不把同步调用强行伪装为 SSE。

### Interface 行为与隐藏职责

示意：`Invoke → 准备 operation → 统一驱动 + 完整响应处理`；`Stream → 准备 operation → 同一驱动 + generation 处理`。这些箭头是 implementation 顺序，调用者仍只使用原入口。

统一驱动负责候选解析、部署可用性、预留/费率、物理尝试计数、取消检查、错误分类、退避、兼容 fallback 和账本关闭。内容处理负责完整响应，或独立 generation 的增量/完整工具调用校验。重试/fallback 通知也必须传播 emitter 错误，不能在消费者停止后继续调用。

- 实际请求之前完成门禁与预算事务；未配置不伪造物理 attempt。最多三次 primary、一次 compatible fallback，权限/Schema/参数拒绝和取消不重试、不 fallback。
- “可重试”与“允许 fallback”分别分类，不因共享逻辑就把 adapter 不可用与 typed transient 混为一类。同步/流式现有差异先复现，再按已接受约束统一。
- 每次尝试读取实际候选费率；失败后先正确释放再评价 fallback 的可用预算。完整响应已经产生的消费不因平台校验/封存失败而消失；缺 usage 按既有规则处理。
- 清理用有界、脱离已取消请求的上下文；写账本失败要被明确返回/关联，不声称已经关闭，不重复物理请求“补救”记账失败。
- ordinary caller cancellation 与流式取消保留各自稳定码；业务取消政策与兼容验证取消探针政策保持区分。job 与 run 账本保持归属。
- 每个物理流尝试独立 generation，失败草稿不拼入下一次；最终封存后 final 帧投递失败不撤销已提交成功。公共响应和凭据保护不变。

依赖：兼容性/分类为 in-process；PG 与对象目录为 local-substitutable，使用实际本地 PG/临时对象目录；供应商为 true external，保留现有 adapter，测试用现有 stub 与本地 fake HTTP provider。账本不换成内存 mock。

**Depth / locality / deletion test：** 删除统一驱动会迫使四条路径重新学习相同顺序，故其职责值得保留。迁移完成后删除四套独立循环与纯转发；不删除有意义的 generation implementation。

迁移：同步主/备完整路径 → 流式主/备路径 → 取消、消费者停止、清理失败与结算差异定向回归 → 移除旧编排。测试通过原 capability 入口观察请求数、预算/attempt、封存结果与帧，保持既有授权/重放测试。

## C3：可读片段与完整性

### 三种 interface

**A：候选集合 + 批量读取。** `Candidates(owner, base, embeddingRevision)` 和 `Read(owner, ids)` 返回经验证片段或逐项失败。共享读取深，interface 小；缓存仍要自己组合来源比较，调用者容易遗漏某个 provenance 字段。

**B：查询选择 + metadata + 验证计划。** `Select / Inspect / Verify / Read` 允许自由组合。适合通用存储检查工具，却将授权与读取的先后顺序暴露出去；容易产生“只取 metadata、跳过字节验证”的新路径。

**C：owner 限定的 reader。** 对检索候选、单片段打开、缓存复检提供有意义的操作。reader 内部封装现有权限 SQL、metadata 投影、对象读取和缓存来源比较，调用者保留排序、公开错误映射和缓存键。

**选择 C，吸收 A 的有界批量处理。** reader 在请求内绑定 owner，不缓存授权决定，不把它作为跨请求长期 session。三个操作是共享 module 的一个 interface；具体名字在实现时确定，不新增公开端点。

### Interface 行为与隐藏职责

示意：`Query → reader.Candidates → 排序 / top-k`；`GetChunk → reader.Open`；`缓存命中 → reader.CheckCached → 返回当前验证结果或 miss`。CheckCached 不能只验证缓存记录的摘要，也必须核对当前数据库来源及实际正文。

- 三路共用现有 `currentReadableChunkSQL`，先限定 owner、tombstone、活动 ready 索引和正文 artifact，再读取字节；不创建新权限策略副本。
- metadata 从 artifact 取得 `sha256`、`size_bytes` 与对象键；同次有界读取累计字节数并计算 SHA-256，二者均与权威记录一致才返回正文。不信缓存值或宿主 stat 代替字节验证。
- `size_bytes` 已存在，不新增 schema 或重新定义正文格式。缺失/非法完整性 metadata 不作为通过；若真实存量发现此类行，要记录并按既有封存流程修复，不能跳过校验或偷偷为损坏字节重签摘要。
- metadata 整体 SQL 失败与逐对象失败分开；内部区分不可读/完整性不符/瞬态故障，外部不暴露对象键、路径和他人正文。
- 检索保留候选上限与模型筛选，坏片段跳过且部分/空结果不缓存；引用不可读保持 404；回答返回前逐引用复检，失败不返回含失效引用的最终答案。
- 缓存瞬态读取故障本次 miss、条目保留；确定性缺失或损坏使条目作废。缓存键、TTL、source digest 继续发挥原作用，不能代替即时权限和字节复检。
- 新检索与当前 embedding revision 匹配，打开已有引用不加该要求。重建中/失败后旧活动索引继续可读；来源删除仍要即时阻止相关片段，不能误加“文档当前 ready/最新 revision”条件破坏旧索引政策。

依赖：PG/本地 objectstore 使用现有实际 adapter，不新增通用仓储 port。摘要是 in-process；embedding 仍在缓存查找之前检查，不移入可绕开的缓存分支。

**Depth / locality / deletion test：** 删除 reader 会把 SQL 投影、字节校验、故障分类散回三个调用者；仅包一层 `readChunkText(key)` 不足以达标。迁完三路后删除裸读取和各入口的重复拼装；允许不同入口保持业务错误表达。

迁移：普通检索完整路径与通用读取器 → 引用/回答和缓存 → 损坏、瞬态、撤权、重建与删除组合对照。保留批量 metadata SQL，单次读取完成摘要与大小验证，不重复打开同一对象来算两个值；记录校验成本，不承诺无依据的性能提升。

## C4：显式节点执行上下文

### 三种 interface

**A：统一 ExecuteNode，返回 NodeResult。** 输入显式 NodeExecution，返回输出、决定和证据；调度器 apply。职责清楚，但不必因此重写所有十类节点的总 dispatcher。

**B：可扩展 NodeHost / NodeSession。** host 暴露 Resolve、Emit、Wait、Cancel、Skip、Commit 等能力，节点执行扩展方便。若开放全局 Skip/Commit，图调度权重新泄漏；即便限权也容易出现大量转发。

**C：最常见节点使用受约束 execution context。** 构造器提供当前节点、绑定/namespace、封存 scope 和执行证据；Agent/call_app 各返回 typed outcome，图/线性分别适配。

**选择 C + A 的返回决定。** 范围限定原报告涉及的 Agent 与 call_app，共用显式输入和结果约定，保留它们不同的内容执行。不新增十节点插件框架，不把既有调度器放进共享上下文。

### Interface 行为与隐藏职责

示意：`图或线性调度器构造 NodeExecution → Agent/call_app → NodeOutcome → 调度器处理`。

NodeExecution 包含当前 manifest 节点、明确 start 身份、输入绑定、启动时 namespace 快照、允许的 sealed node scope、固定子应用事实、execution unit 与已有证据。不能持有假的 linearPlan，也不暴露自由 artifact IDs。传递引用/事实快照而非复制全部正文；保留现有允许 scope 与实际可用输出的区别。

NodeOutcome 区分成功、失败、取消、waiting，携带节点输出、checkpoint/proposal、sandbox started/outcome/report 及所需证据。节点仍登记自己产物和节点事实；不提交应用运行终态，不跳过兄弟节点。

图调度器继续唯一管理取消、排空、skipped、optional 失败、join 和终态提议。线性调度器把同一 NodeOutcome 适配到已有线性收尾。Agent/call_app 不再通过 `context.Value` 判断自己是否处于图执行来决定是否自行提交全局结果。

依赖：绑定、schema、context 构建为 in-process；既有平台 Client 是 remote but owned，真实 mTLS client 与已有测试 adapter 已构成实际 seam；沙箱沿用现有执行能力。Go 执行权威、Python 独立语义证明按 ADR 0034 保持。

**Depth / locality / deletion test：** 删除共享节点执行会让 Agent/委派的安全循环在两个调度器重复；共享职责有价值。`graphLinearView` 及 `linearRun.available/skip/scopeIDs` 图专用字段则应消失。迁移后目标节点不再依赖 graph capture 判断；其他节点仍在使用的 `captureGraph*` 和调度器内部 `graphNodeAttempt` 不为本项强行全删。

迁移：Agent 的图/线性完整路径 → call_app 的图/线性完整路径 → 删除假视图并验收等待/取消/恢复。固定版本、明确输入绑定、`call_app:<node>` logical key 语义保持，顺序两次独立调用不能被误去重成同一次；并行子运行仍各自独立。

## C5：共享测试能力

### 三种 interface

**A：按实际能力组织小 module。** 规范 Stack、一次真实执行、finalizer 生命周期、事实观察各有明确职责；测试直接组合，票专用夹具与断言保留。能力之间不再靠票号串联。

**B：SuitePlan + Pipeline。** 通过配置描述启动、运行、观察和清理，适合高度可配置测试平台，但会引入另一套执行语言，让真实身份/生命周期隐藏在配置里，超出当前需求。

**C：Stack + 长生命 execution session。** `with execution(stack) as s: s.exec_once()`，默认调用方便；若顺手延长 finalizer 生命周期，会改变既有每次执行的资源语义。

**选择 A + C 的规范 Stack，保留每次执行的现有生命周期。** 不用长生命 session 改变行为，不把所有支持能力塞进一个万能对象。

### Interface 行为与隐藏职责

建议能力包括：持有真实 API/PG/对象目录/证书/binaries/签名配置的 Stack；一次执行操作；可独立使用的 FinalizeWorker 资源管理；读取 Run/节点结果/封存正文/SSE 证据的观察能力。示意：`completed, report = execute_once(stack, env_extra=...)`，然后测试独立断言实际 DB/SSE/封存结果。

一次执行内部复用环境构造、实际 supervisor 启动、finalizer context 和报告解析。退出/超时必须 terminate、必要时 kill、wait/communicate 回收；异常不能把进程遗留藏在成功返回值里。返回 supervisor report 不等于 Run 终态。

规范 Stack 接管 `T024Stack` 的真实字段和 client 行为；使用短期 re-export 支持批次迁移，最后删除 `T033 → T031 → T029 → T024` 别名链。迁移 `t042.run_linear_once` 的实际组合、`t033` 的真实执行能力、`t083/t086/t088` 纯转发和 `t091` 通用观察再次导出。票专用销售数据、图、审批、SQL、期望值与断言保持在票模块。

依赖：Python 结构为 in-process；PG、binaries、对象目录、Linux/runsc 使用既有实际能力，HTTP/mTLS 沿用真实栈。不给产品添加故障开关，不发明 mock-only execution seam。

**Depth / locality / deletion test：** 实际一次执行与事实观察是深 module；只有改名/转发的 wrapper 应删除。新旧并存是迁移手段，不是交付终点。按 Stack 调用者、执行调用者、观察调用者分批，最终 contract 删除所有目标兼容入口并验证没有仓库调用者。

## C6：页面共用状态解释与恢复

### 三种 interface

**A：RunSession.observe。** 共享 SSE、poll、取消和投影，页面拿 view。调用简洁，但会重排现有网络/定时器生命周期，扩大此次重构范围。

**B：可配置 typed observation session。** 提供 start/subscribe/refresh/cancel/stop，适配多来源与扩展策略；interface 需要更多事件、版本和释放协议，当前没有第二套观察产品需求。

**C：纯规则 + 聚合 view。** 以现有 runstream 为基础，页面显式加载同一个 module，取得状态/动作/文案投影、流失败分类、快照修正。DOM 与请求生命周期继续留在页面 adapter。

**选择 C。** 沿用原报告的运行事实到展示决定 seam，吸收 A 的常见调用方便性，不新建通用网络观察框架。现有快照读取注入点仍可复用，不为纯计算增加 transport port。

### Interface 行为与隐藏职责

建议一个显式只读 module，三个有意义操作：`view(facts)`、`failure(status, payload)`、`correct(failure, options)`。facts 包含运行快照、平台请求、子运行/产物和已有轮询计数，view 返回状态、动作与展示事实；页面不自己判断终态。safe snapshot path、state/phase 字典等为 implementation。迁移期保留旧导出仅服务调用者迁移，最终删除目标冗余导出。

保持 `finalizing` 是 phase、202 cancel 是意图、unknown 写待结果核对。dispatched Request 继续观察 Run；现有轮询上限不扩大。410 丢旧 cursor、标明历史缺失，只走安全同源 Run 快照；晚到响应沿用页面 selection generation 守卫，不覆盖新选择。

页面/bootstrap 只做一次完整依赖可用性检查。共享资源 404、语法错误或未完整导出时显示“运行状态暂不可用”，暂停相应状态观察与依赖可靠状态的操作；保留导航/重载，不渲染原始快照作为替代状态，也不执行旧业务 fallback。各页面受影响按钮清单在实施时与实际 DOM 一起验收。

删除范围必须覆盖：

| 调用者 | 重复内容 | 迁移结果 |
| --- | --- | --- |
| chat | handling 三态、仅 Request 轮询、可选 terminal 判断 | 使用共享 view，依赖故障走明确提示 |
| runs | child summary、streamFailure、truth、artifact readiness、cancel accepted、snapshot path/correction | 统一共享操作，安全快照不再由页面临时拼装 |
| runview / debug | Run terminal 字典、formatRunError 的共享状态判断、canCancel/label、cancel 文案 fallback | 共用 Run 规则，节点表、节点错误解释和 DOM 渲染保持自身职责 |
| 旧 F03/F08 等 harness | 只加载 app/页面脚本，因缺依赖触发旧语义 | 显式加载真实共享字节，缺依赖测试作为单独故障场景 |

DOM 锚点存在性、可选预览 renderer、数据类型检查、浏览器能力探测不是上述业务规则副本，不机械删除。与 model fallback 账本展示无关的 `fallback` 文本也不属于删除对象。

依赖：投影 in-process，DOM 是页面 adapter，已有 HTTP/SSE 是 remote but owned；保持现有真实 fetch 与 harness 传输，不新增浏览器框架。纯规则测试、真实服务脚本顺序、实际浏览器缺依赖验证分开记录。

**Depth / locality / deletion test：** 删旧 fallback 使重复规则消失；删共享 module 则规则回流三页面，故共享 module 有 leverage。迁移顺序：显式 module/启动守卫 + harness → chat/runview 与 runs 两批调用者 → 删除兼容出口及重复解释。

## 验收 seam 提案（供 to-spec 核对）

| 项 | 最高且已存在的验收入口 | 必须观察的事实 | 定点测试补充 |
| --- | --- | --- | --- |
| C1 | 现有检查/同步 CLI | 退出码、诊断、真实双目录与基线 | 原 check/sync 函数，同一个 module |
| C2 | capability Invoke/Stream，经实际 HTTP/mTLS 路径 | 物理请求数、预算/attempt/operation、帧、封存字节 | 同一 module 的 Go 调用；本地 fake provider |
| C3 | 既有检索、引用打开、回答端点 | cache on/off 输出、权限/删除/完整性与公开错误 | reader 定点故障和真实 PG/对象目录 |
| C4 | 既有创建/领取/执行/恢复 Run 路径 | 真实节点事实、子运行、checkpoint、排空后唯一提交 | presetexec 测试 adapter 与独立语义证明 |
| C5 | 既有真实栈 fixture + supervisor exec | 真实进程、退出回收、DB/SSE/封存证据 | import/collection 仅作补充 |
| C6 | 实际 chat/runs/debug 页面及服务的静态资源 | 正常状态、410、迟到响应、404/语法错误提示与操作状态 | 同一共享字节的 Node 逻辑测试 |

不新增生产测试端点，不以私有 helper 调用次数作验收。旧的内部形状测试仅在被相同业务结果覆盖后替换，不删安全/故障回归来换数量。测试强度跟随行为风险：六项整合时运行受影响完整套件、Go 检查、规格同步门禁和产品集成回归，记录跨系统、真实供应商等实际限制。

## 迁移与完成判定

先完成可逆的 C5 expand，再批量迁移并 contract；C2/C3/C4/C6 各自形成可完整验收的路径，不人为串成单链。只把真实 interface 依赖写成阻塞边；修改同一文件造成的集成冲突由工程顺序协调，不能伪装成业务依赖。

每个切片包含行为实现、必要测试和证据，避免“只写类型”“只移动文件”的水平任务。最终整合依赖各项叶子验收及 C1 四票核对，回填六项覆盖表。暂时保留的 wrapper、旧循环、假视图和页面 fallback 均有明确 contract 删除任务。

本轮仅设计与草拟后续规格/票据；没有修改 C2–C6 运行实现，没有执行新的产品验收。方案示意不是已存在的新函数，也不是编译证明。
