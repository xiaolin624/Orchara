# C1：可靠的规格同步与漂移检查

## Problem Statement

平台维护者在仓库根目录维护原始规格，产品工作区保留供开发查阅的规格复制件和供产品工具使用的运行契约复制件。仅证明复制件仍匹配上次同步时保存的文件指纹基线，不足以证明当前规格已经落实到产品。

原有检查会漏掉已有源文件的内容变化与删除；关键来源目录整体消失后，同步还可能保留旧运行契约，并将残留内容登记为新基线。维护者因此可能把未同步或不完整的状态误判为成功。同步中途出错时，还需要保护上一份完整状态，并让后续操作识别需要恢复的情况。

## Solution

提供含义明确的检查和同步操作。检查成功同时意味着当前原始规格、两份受管复制件和同步基线一致，且没有未完成同步。来源无法核实、内容不一致、基线损坏或同步未完成时，明确报告失败。

有效来源中的普通文件删除会在同步后传播到受管复制件；整个关键来源目录缺失时先拒绝同步。新内容准备和核验完成后才切换到受管位置。普通失败保护或恢复上一份完整复制件；异常中断留下可识别状态，维护者再次显式同步时可恢复并继续。

## User Stories

1. As a platform maintainer, I want one existing check command to verify the current specifications, both managed copies and the baseline, so that success has one complete meaning.
2. As a platform maintainer, I want changes to an existing source document to fail the check before synchronization, so that outdated product specifications cannot appear current.
3. As a platform maintainer, I want changes to an existing source contract to fail the check before synchronization, so that product tooling cannot silently use an older contract.
4. As a platform maintainer, I want newly added source files to be detected, so that new specifications cannot be omitted unnoticed.
5. As a platform maintainer, I want deleted source files to be detected before synchronization, so that stale copies remain visible until deliberately updated.
6. As a reviewer, I want edits to either managed copy to be detected against the baseline, so that matching the source alone cannot conceal an unsynchronized change.
7. As a reviewer, I want unexpected files added to either managed copy to be detected, so that the managed content set remains verifiable.
8. As a reviewer, I want files removed from either managed copy to be detected, so that incomplete copies cannot pass validation.
9. As a reviewer, I want a matching change to the source and a copy still to fail against an unchanged baseline, so that two matching edits cannot bypass the synchronization record.
10. As a platform maintainer, I want a missing source directory to produce a clear failure, so that an unverifiable source is never treated as an empty valid source.
11. As a platform maintainer, I want incomplete or denied source reads to produce a clear failure, so that partially observed content cannot be published as complete.
12. As a platform maintainer, I want synchronization to stop before modifying copies when the entire critical source contract directory is missing, so that an incomplete source cannot erase or legitimize existing content.
13. As a platform maintainer, I want ordinary deletions within a valid, readable source to propagate to the managed copies, so that intentional removal does not leave stale product content.
14. As a platform maintainer, I want a valid empty source contract directory to remain distinguishable from a missing directory, so that legitimate file deletions are not silently converted into a new restriction.
15. As a platform maintainer, I want a missing baseline to fail validation, so that uninitialized copies are not mistaken for verified copies.
16. As a platform maintainer, I want malformed or inconsistent baseline data to fail validation, so that invalid metadata cannot authorize a successful check.
17. As a reviewer, I want the contract baseline to agree with the contract portion of the specification baseline, so that the two managed copies cannot be validated against contradictory records.
18. As a developer using Windows or Linux, I want existing text newline normalization to remain effective, so that equivalent text checkouts do not cause false drift.
19. As a developer, I want binary content changes to remain detectable, so that text normalization does not conceal changes to non-text content.
20. As a developer, I want timestamps, machine-specific source locations and repository commit metadata to remain diagnostic information, so that unrelated metadata changes do not imply specification drift.
21. As a platform maintainer, I want synchronization to modify only explicitly managed copies and private transaction state, so that product code, reports and product decisions remain intact.
22. As a platform maintainer, I want escaping or overlapping target locations to be rejected, so that a configuration mistake cannot broaden cleanup scope.
23. As a platform maintainer, I want included symbolic links and directory redirections to be rejected, so that synchronization does not follow an unexpected external path.
24. As a developer, I want the normal layout with the product workspace inside the source repository to remain supported, so that the established project layout does not need migration.
25. As a platform maintainer, I want targets that would hide the critical source contract directory to be rejected, so that directory exclusion cannot turn real contracts into an apparently empty source.
26. As a platform maintainer, I want both new copies to be prepared and verified before replacement, so that incomplete staged content is not published.
27. As a platform maintainer, I want source changes observed during synchronization to prevent publication of inconsistent content, so that copies are not assembled from incompatible observations.
28. As a platform maintainer, I want a preparation failure to preserve the existing copies and baseline, so that a failed update does not damage a usable workspace.
29. As a platform maintainer, I want a recoverable switching failure to restore the previous on-disk state, so that a partially installed generation does not become active.
30. As a platform maintainer, I want an abrupt process exit during switching to be detected by a later check, so that mixed copies cannot be reported as synchronized.
31. As a platform maintainer, I want an explicit rerun of synchronization to recover an identifiable interrupted transaction, so that recovery does not require learning another public command.
32. As a platform maintainer, I want checking to leave managed copies unchanged, so that a diagnostic operation does not unexpectedly repair or replace files.
33. As a platform maintainer, I want recovery to remain retryable after another interruption, so that a second failure does not make a complete backup unusable.
34. As a platform maintainer, I want an interrupted first synchronization to recover correctly without a previous generation, so that initialization failures have defined behavior.
35. As a platform maintainer, I want invalid recovery records or unexplained backup changes to stop recovery while preserving evidence, so that the tool does not guess which content is safe to discard.
36. As a platform maintainer, I want partial cleanup failures to remain retryable without damaging restored copies, so that housekeeping cannot prevent recovery of a complete state.
37. As a platform maintainer, I want cleanup failures after a verified commit to be reported as warnings, so that already complete copies are not misrepresented as a failed publication.
38. As an automation maintainer, I want concurrent checks and synchronizations to respect one process lock, so that cooperating tool processes do not race through replacement or recovery.
39. As an automation maintainer, I want content drift, unverifiable sources, busy workspaces and interrupted synchronization to have distinguishable diagnostics and nonzero failure status, so that automation can explain why validation failed.
40. As a platform maintainer, I want every runtime contract file to be checked even when its name resembles the specification baseline name, so that reserved-name handling cannot conceal an extra or changed contract.
41. As a developer, I want existing check and synchronization entry points to remain usable, so that callers do not need to coordinate inventories, plans or recovery steps themselves.
42. As a reviewer, I want validation evidence to distinguish tooling tests, platform acceptance and unavailable environments, so that successful local checks are not presented as proof of unrelated capabilities.

## Implementation Decisions

- 深化现有规格同步 module，保留检查与同步两个操作及既有命令行为；不引入公开的扫描、计划、提交或恢复协议。三种 interface 已比较，选择调用者需要掌握信息最少的方案。
- 同一份规范化的来源清单负责内容比较和暂存核验，运行契约清单由规格清单中的契约部分投影。基线内的两份清单必须满足相同关系。
- 保留已有纳入、排除和内容归一化语义。目录排除在遍历进入之前发生；不能把读取失败视为空清单。复制件里的额外文件也要参与完整性检查。
- 默认检查同时核对当前来源与基线、规格复制件与基线、运行契约复制件与基线。当前来源不可核实或存在未完成同步时不能成功。
- 只有规格复制件自己的基线文件免于自引用摘要；运行契约中的同名文件仍属于待核对内容。
- 基线继续使用既有版本的兼容格式，并附加 generation 说明。来源绝对位置、同步时间和仓库提交号不替代内容摘要，也不作为跨机器一致性的必要身份。
- 检查返回可诊断的 findings；同步失败由 module 自有错误表达；命令将失败转换为非零退出。新增恢复与清理说明不改变原有成功结果的基本字段。
- 文件系统是 local-substitutable 依赖，生产目录与测试临时目录使用同一个 filesystem adapter；没有第二种真实存储需求，不新增存储 port。
- 受管目标必须是产品目录内互不包含的有效相对位置，且与私有状态位置分离。来源不能位于目标之内，目标不能吞掉关键契约来源；正常的同仓嵌套布局继续支持。
- 对纳入来源和受管目标拒绝 symbolic link、junction 或其他路径重定向。每次递归清理或目录移动之前重新核实实际位置和受管范围，恢复记录不能提供任意操作路径。
- 同步在触碰受管目标前完整核实来源；关键契约来源目录缺失即失败。有效且完整可读来源中的普通文件删除，通过替换完整复制件传播；不额外要求所有源文件始终存在。
- 检查和同步使用同一产品工作区的操作系统进程锁，竞争时快速失败；不通过删除锁文件、PID 猜测或超时抢占正在运行的进程。
- 新规格复制件、新运行契约复制件及新基线先在私有位置准备并核验；发布前再次检查来源。首次切换之前记录新旧内容身份和原目录存在性。
- 两份目标依次切换，不宣称整个操作具有跨目录文件系统原子性。完整安装并重新核验来源、两份复制件和基线后，才能清除未完成记录并视为提交。
- 普通失败恢复上次切换前状态；回滚先移动待丢弃的活动目录，再恢复完整备份，将递归清理留在完整目标恢复之后。首次同步无旧 generation 时恢复原先不存在的状态。
- 未完成记录会阻止检查成功。再次显式同步在来源有效时验证并恢复可识别的旧事务，然后进行本次同步；来源无效时不启动目标恢复或清理。
- 恢复记录、备份或目标无法安全解释时，保留证据并报告需要恢复，不能猜测或扩大清理范围。恢复自身中断后仍可依据记录和实际内容重试。
- 提交后的清理失败属于可报告的 housekeeping warning，不撤销已经核验的完整 generation；下次显式同步可以继续清理私有残留。
- 本规格不改变 Agent 应用、应用运行、权限、运行审批、删除治理、业务契约或数据库模型。保留已接受的同仓规格复制与比对决定，不将工程词汇新增为业务术语。

## Testing Decisions

- 首选现有“规格同步命令”作为唯一外部验收 seam，观察退出状态、诊断、两份复制件的实际内容及基线，而非内部函数拆分。检查与同步是该 seam 的两个操作，不新增公开测试协议。
- 既有检查与同步函数是同一个 module 的定点回归入口。测试可使用这些入口降低故障定位成本，但必须断言外部可观察行为，不能只证明内部 helper 被调用。
- 故障注入保留在私有文件操作 seam；被测操作仍经既有入口执行，结果通过实际目录、原始文件字节和后续检查判定。
- 测试先例来自现有工具验收：真实临时目录、双复制件基线校验、换行归一化和命令失败退出。新覆盖延续这些方式，不用内存文件系统 mock 代替实际替换行为。
- 覆盖来源与两份复制件的新增、修改、删除，以及来源与复制件同时修改仍不能绕过旧基线的情形。同步后必须再次检查完整一致性。
- 覆盖来源缺失、不是目录、读取不完整、关键契约目录缺失、空但有效契约目录、基线损坏及两份基线清单互相矛盾。
- 覆盖受管范围外哨兵不变、目标路径越界或重叠、目录重定向、合法同仓嵌套、目标吞掉关键来源，以及运行契约保留名文件检查。
- 覆盖准备和核验失败、四个目录切换位置的可捕获异常；失败后比较旧复制件和旧基线的实际字节。
- 使用真实独立子进程异常退出验证四个切换位置的中断检测、再次同步恢复、恢复中再次退出以及首次同步没有旧 generation 的场景。普通 exception 测试不替代真实进程退出测试。
- 覆盖恢复失败、损坏记录、部分清理失败后重试、提交后清理 warning，以及来源无效时不触碰恢复目标。
- 用两个实际进程验证互斥锁，要求竞争者快速报告 busy，持锁者释放后仍可正常检查。
- Windows 完整工具测试和 POSIX 标准库冒烟分别记录。环境缺少测试依赖时明确写 not_run，不能将冒烟结果写成完整套件通过。
- 本次 seam 核对仅确认上述已存在入口和观察方式，不重新打开已经确认的业务范围或 Q1–Q6。

## Out of Scope

- 其余五项架构候选：模型尝试生命周期、组织知识库可读片段、共享节点执行、测试支撑组织和运行页面状态逻辑。
- 新增离线检查模式、公开计划对象、额外恢复命令、远程存储 adapter 或存储插件框架。
- 修改根规格生成源、生成契约、数据库迁移、业务运行时、应用权限或运行审批语义。
- 用本工具验收代替完整平台验收、真实供应商调用、Linux 沙箱安全验收或容量实验。
- 物理断电、存储设备损坏、远程文件系统持久性，以及抵御同机恶意进程实时替换目录的隔离保证。
- 为任意外部进程持续改动的来源提供跨整个文件系统的原子快照。
- 提交、推送、部署或把当前工作区实现自动认定为远端已集成。

## Further Notes

- 本规格由现有对话直接归纳。Q1–Q6 已逐项确认；三种 interface 方案已比较并完成本地实现。本轮不新增访谈，不改变既有决定。
- 测试 seam：用户已回复“确认”，沿用现有规格同步命令及同一 module 的函数回归入口；不新增公开 seam。
- 已发布：[GitHub #137 完整规格评论](https://github.com/xiaolin624/Orchara/issues/137#issuecomment-6719314337)。发布后回读核对正文一致，42 条用户故事完整；`ready-for-agent` 标签、原任务正文及关闭状态均保留。
- 发布目标为现有任务 [#137](https://github.com/xiaolin624/Orchara/issues/137)，沿用 `ready-for-agent` 标签，并保留已有关闭状态与历史证据。该标签表达规格充分，不代表重新开工或提升运行实验状态。
- 已有本地证据为完整工具测试 64 passed，其中同步专项 52 项；实际仓库同步后检查通过。WSL Python 3.12.3 标准库冒烟验证通过，但缺少 pytest，Linux 完整套件为 not_run。本规格整理阶段没有重新运行这些测试。
- 实现、文档和证据仍在工作区，未提交、未推送；现有任务完成口径为本地工具实现和验收，不表示远端代码已更新。
- 正常同步只更新了历史遗漏的忽略规则复制件与工具生成的基线；原始忽略规则是既有内容，运行契约内容没有差异。
- 相关任务仍追溯到 T001 / F00 的后续改进，不更改原任务 ID、依赖、完成条件或平台任务图。
- 详细来源：[需求与问答](2026-11-08-architecture-grilling.md)、[设计比较](2026-11-08-spec-sync-design.md)、[验收报告](../../reports/tooling/C1-spec-sync.md)。完整规格以任务评论发布，原任务正文与完成证据保留。
