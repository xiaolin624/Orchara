# Orchara 纵向切片执行清单

124 条旧草案经合并/拆分整理为 121 条 Ticket。27 个任务组保留为规格追踪，未额外创建父 Issue。

所有条目已发布并标记 ready-for-agent。该标签表示规格可实施，领取仍须前置已完成且环境条件满足。运行实现与实验均未开始。

本分支工作流仅建立并核对 177 条 GitHub 原生阻塞边；成功后才移除 Issue 正文中的临时 Blocked by 列表。主分支产品规格未修改。

## 编号、阻塞与端到端交付

1. **[T001 在产品工作区复现规格并生成环境报告](https://github.com/xiaolin624/Orchara/issues/2)**
   - Blocked by：None，可立即开始。
   - What it delivers：维护者从当前规格入口检查产品位置与已有修改，运行基线验证并得到工具/服务/宿主可用性和阻塞清单；报告不包含密钥。

2. **[T002 从锁定依赖运行一个只有授权工具的 Agent](https://github.com/xiaolin624/Orchara/issues/3)**
   - Blocked by：#2。
   - What it delivers：开发者在干净环境安装稳定组合，用 stub 模型运行最小 Agent，并在报告中看到实际工具清单和调用次数。

3. **[T003 让 Go/Python 对同一清单给出相同指纹](https://github.com/xiaolin624/Orchara/issues/4)**
   - Blocked by：#3。
   - What it delivers：开发者向两语言命令输入相同合法/非法 JSON，得到一致规范字节和哈希或相同拒绝结果。

4. **[T004 在第二个干净环境重建核心探针](https://github.com/xiaolin624/Orchara/issues/5)**
   - Blocked by：#4。
   - What it delivers：部署者凭精确版本、镜像来源和许可清单复现最小调用，能追溯冲突和未采用组件。

5. **[T005 让一个中断任务在新进程中安全继续](https://github.com/xiaolin624/Orchara/issues/6)**
   - Blocked by：#5。
   - What it delivers：开发者在真实 PG 中初始化隔离 checkpoint，运行至人工等待，停止旧进程后恢复同一任务，并拒绝他人 thread 定位。

6. **[T006 启动一个拒绝危险配置的 Linux 沙箱探针](https://github.com/xiaolin624/Orchara/issues/7)**
   - Blocked by：#5。
   - What it delivers：部署者在真实 Linux 建立 runsc 隔离环境，提交合成任务并看到运行时身份和安全配置检测结果。

7. **[T007 用同一 corpus 校验两语言的变量和类型](https://github.com/xiaolin624/Orchara/issues/8)**
   - Blocked by：#5。
   - What it delivers：创作者提交线性图与非法变量/模板/类型样例，Go/Python 给出一致可定位结论；节点结构输出可验证。

8. **[T008 启动产品并在浏览器看到真实健康状态](https://github.com/xiaolin624/Orchara/issues/9)**
   - Blocked by：#5。
   - What it delivers：部署者在独立产品工作区启动最小 Go/Web/worker 骨架，通过 API 和状态页区分存活与依赖就绪；规格同步可检查。

9. **[T009 升级后恢复旧图或明确报告缺制品](https://github.com/xiaolin624/Orchara/issues/10)**
   - Blocked by：#6。
   - What it delivers：部署者换上新图后恢复旧任务，仍加载原图和状态版本；缺少原制品时得到明确失败而非静默替换。

10. **[T010 证明脚本无法读越权文件或绕过网络代理](https://github.com/xiaolin624/Orchara/issues/11)**
   - Blocked by：#7。
   - What it delivers：安全评估者执行文件/凭据/出站攻击样例，从真实返回与宿主观察中确认阻断点。

11. **[T011 在真实图引擎中验证分支和汇合结果](https://github.com/xiaolin624/Orchara/issues/12)**
   - Blocked by：#8。
   - What it delivers：创作者执行条件、并行和嵌套 fixture，看到正确激活、skipped、失败收敛以及恰一次 join。

12. **[T012 按环境 profile 启动并重现一次 CI 验收](https://github.com/xiaolin624/Orchara/issues/13)**
   - Blocked by：#9。
   - What it delivers：部署者选择明确 profile 启动隔离依赖与 stub，安全配置错误会拒绝启动；同一最小调用在 CI 可复现。

13. **[T013 模拟租约失联而不启动第二个执行者](https://github.com/xiaolin624/Orchara/issues/14)**
   - Blocked by：#10。
   - What it delivers：部署者断开受监督进程的心跳，观察恢复等待与旧凭据拒绝，证明活进程没有被并发接管。

14. **[T014 停止超限脚本并证明资源已回收](https://github.com/xiaolin624/Orchara/issues/15)**
   - Blocked by：#11。
   - What it delivers：部署者运行超时、fork、内存和磁盘负例，看到完整终止证据和受控目录清理结果。

15. **[T015 从空数据库建立管理员并邀请激活用户](https://github.com/xiaolin624/Orchara/issues/16)**
   - Blocked by：#13。
   - What it delivers：管理员运行初始化命令，再通过管理界面邀请用户，受邀用户在激活页设置密码；全过程有约束和审计。

16. **[T016 等待旧进程、沙箱和数据库写全部排空再恢复](https://github.com/xiaolin624/Orchara/issues/17)**
   - Blocked by：#14。
   - What it delivers：部署者依次提供退出和数据库证据，只有证据齐全后才能启动恢复尝试，并防止 PID 复用误判。

17. **[T017 用户登录、刷新身份并安全登出](https://github.com/xiaolin624/Orchara/issues/18)**
   - Blocked by：#16。
   - What it delivers：使用者在登录页获取安全会话，刷新仍能识别身份，退出或会话过期后被拒绝访问；CSRF 不能伪造写操作。

18. **[T018 写成功丢回执后恢复仍只写一次](https://github.com/xiaolin624/Orchara/issues/19)**
   - Blocked by：#17。
   - What it delivers：评测人员使用有独立账本的企业 stub 注入丢响应，观察 unknown、核对和恢复行为。

19. **[T019 管理员授予或撤销某个用户的资源访问](https://github.com/xiaolin624/Orchara/issues/20)**
   - Blocked by：#18。
   - What it delivers：管理员在权限界面管理具体资源动作，使用者立刻看到受过滤列表；撤销后下一次读取或调用被拒绝。

20. **[T020 创建服务账号并用可撤销 Key 调用授权 API](https://github.com/xiaolin624/Orchara/issues/21)**
   - Blocked by：#20。
   - What it delivers：管理员创建具名负责的服务账号与 Key，外部调用同时受 scope 和资源 ACL 约束，且不能借用个人数据。

21. **[T021 禁用人员并安全交接未完成待办](https://github.com/xiaolin624/Orchara/issues/22)**
   - Blocked by：#21。
   - What it delivers：管理员从界面禁用主体，旧会话和 Key 立即失效；待办仅交给有权负责人，历史审计引用仍保留。

22. **[T022 提交一个后台任务并在重启后得到唯一结果](https://github.com/xiaolin624/Orchara/issues/23)**
   - Blocked by：#22。
   - What it delivers：管理员通过正式控制接口创建固定 handler 工作，查看领取、重试和完成；Redis 丢唤醒或旧 token 不改变事实。

23. **[T023 创作者编辑用途说明和草稿并处理保存冲突](https://github.com/xiaolin624/Orchara/issues/24)**
   - Blocked by：#22, #12。
   - What it delivers：创作者从应用列表进入编辑器，保存用途卡和草稿后刷新恢复；并发编辑能重载或保留本地副本，不能静默覆盖。

24. **[T024 后台评测只读取获准文件并记账模型调用](https://github.com/xiaolin624/Orchara/issues/25)**
   - Blocked by：#23。
   - What it delivers：开发者发起合成后台工作，handler 通过限定 kind/job 的接口读取封存输入、调用获批模型并登记产物。

25. **[T025 把当前草稿固化为可核验执行清单](https://github.com/xiaolin624/Orchara/issues/26)**
   - Blocked by：#24。
   - What it delivers：创作者触发校验得到固定依赖闭包及服务端 hash，错误定位到配置；只改布局不改变执行内容。

26. **[T026 评测池满载时文件封存仍能完成](https://github.com/xiaolin624/Orchara/issues/27)**
   - Blocked by：#25。
   - What it delivers：管理员提交等待文件的评测并观察状态，独立 system_io 槽仍推进封存；业务评测失败与基础设施失败分别可见。

27. **[T027 发布前提交私有草稿并查看固定调试任务](https://github.com/xiaolin624/Orchara/issues/28)**
   - Blocked by：#26。
   - What it delivers：创作者从调试入口选择 revision 和测试输入，创建仅自己可见的 queued 调试 Run 并查看固定快照；本票交付提交与排队，实际领取和最终结果由运行执行切片接通。

28. **[T028 上传、校验封存并授权下载不可覆盖的文件](https://github.com/xiaolin624/Orchara/issues/29)**
   - Blocked by：#27, #15。
   - What it delivers：使用者从附件界面上传，看到 scanning/ready/quarantined，ready 后拿到封存内容；覆盖 staging 不改变已验证输入。

29. **[T029 提交运行并只让合格执行者在配额内领取](https://github.com/xiaolin624/Orchara/issues/30)**
   - Blocked by：#28, #19。
   - What it delivers：使用者查询唯一固定运行，supervisor 经内部协议领取并启动尝试；多请求竞争不能双活或超配额。

30. **[T030 用预置工具对销售表格返回统计或具体输入错误](https://github.com/xiaolin624/Orchara/issues/31)**
   - Blocked by：#29。
   - What it delivers：使用者提交 CSV/XLSX，在界面得到 Decimal 统计及下载，缺列/重复/公式/超限显示安全修复建议。

31. **[T031 运行完成后等待真实退出证据再展示终态](https://github.com/xiaolin624/Orchara/issues/32)**
   - Blocked by：#30, #29。
   - What it delivers：使用者提交固定快照调试运行并观察 finalizing 到终态，结果/退出乱序或错误制品均得到正确处理。

32. **[T032 断开事件流后从持久时间线继续查看运行](https://github.com/xiaolin624/Orchara/issues/33)**
   - Blocked by：#30。
   - What it delivers：使用者重连 Run 时间线，按 seq 去重回放并在窗口过期时切回快照；预览与最终正文可区分。

33. **[T033 在正式沙箱调试工具并登记可授权下载的产物](https://github.com/xiaolin624/Orchara/issues/34)**
   - Blocked by：#31。
   - What it delivers：创作者启动固定沙箱工具，查看资源与退出状态，通过 scoped 接口上传下载结果；取消后无孤儿执行。

34. **[T034 跨恢复尝试累计活跃耗时并在截止时停止](https://github.com/xiaolin624/Orchara/issues/35)**
   - Blocked by：#32。
   - What it delivers：使用者看到跨尝试累计的执行耗时与超时原因，等待不烧活跃时间，恢复不能重新获得完整时限。

35. **[T035 等待条件和退出乱序到达都只恢复原任务一次](https://github.com/xiaolin624/Orchara/issues/36)**
   - Blocked by：#32。
   - What it delivers：开发者以合成等待 fixture 走正式内部协议提交决定或子结果引用，旧尝试退出与 checkpoint 到齐后才恢复，并在界面解释等待原因；真实审批和委派在各自后续切片接通。

36. **[T036 点击取消后等待执行停止且迟到消息不复活任务](https://github.com/xiaolin624/Orchara/issues/37)**
   - Blocked by：#32, #33。
   - What it delivers：使用者在运行详情取消任务，看到正在停止和最终结果；旧消息和重投不能触发新操作。

37. **[T037 提交脚本依赖并审核成可共享固定工具](https://github.com/xiaolin624/Orchara/issues/38)**
   - Blocked by：#34。
   - What it delivers：创作者提交不可信脚本和依赖，查看隔离构建结果；管理员核对同 hash/digest 后批准共享，并可撤销。

38. **[T038 执行宿主失联后保持恢复等待直到证据充分](https://github.com/xiaolin624/Orchara/issues/39)**
   - Blocked by：#37, #35, #36。
   - What it delivers：运维从状态页定位挂起原因并提供受审计停止证据；无法证明旧执行退出时绝不启动第二执行者。

39. **[T039 管理员登记模型并查看兼容验证后才共享](https://github.com/xiaolin624/Orchara/issues/40)**
   - Blocked by：#39, #38。
   - What it delivers：管理员从模型管理界面选择预登记目的地/凭据引用，运行合成兼容案例，查看报告并只让通过的版本可被共享应用选择。

40. **[T040 在画布配置线性流程并看到服务端校验反馈](https://github.com/xiaolin624/Orchara/issues/41)**
   - Blocked by：#40。
   - What it delivers：创作者拖入四类节点并选择合法变量，保存后用同一 DSL 校验/编译；错误跳转到面板字段而非只显示状态码。

41. **[T041 Agent 经受控模型适配完成一次完整工具调用](https://github.com/xiaolin624/Orchara/issues/42)**
   - Blocked by：#40。
   - What it delivers：创作者调试工具调用和结构输出，能按实际 stub 次数对账；隐藏重试、错误参数或越权工具被拒绝。

42. **[T042 运行固定 Python 节点并查看结构数据与文件](https://github.com/xiaolin624/Orchara/issues/43)**
   - Blocked by：#41。
   - What it delivers：创作者调试一个固定工具节点，在运行详情看到 data、文件和受限日志各自结果；越权或输出违规有明确错误。

43. **[T043 运行受控 Agent 节点并校验最终输出](https://github.com/xiaolin624/Orchara/issues/44)**
   - Blocked by：#41, #42。
   - What it delivers：创作者配置获准模型和工具，用明确 stub 测试自主节点，在界面看到合法输出或可解释失败。

44. **[T044 流式响应中断后丢弃旧代次且不执行残缺工具](https://github.com/xiaolin624/Orchara/issues/45)**
   - Blocked by：#42。
   - What it delivers：使用者观看临时流式预览，半流失败后展示明确错误或新 generation；只有完整验证的 tool-call 才执行。

45. **[T045 供应商故障后只切换兼容备用模型](https://github.com/xiaolin624/Orchara/issues/46)**
   - Blocked by：#42。
   - What it delivers：使用者遇到限流/短故障时得到有限重试或兼容备用结果，界面可查原因和费用，不能拼接两份半段答案。

46. **[T046 并行模型调用共享预算并按实际用量结算](https://github.com/xiaolin624/Orchara/issues/47)**
   - Blocked by：#42。
   - What it delivers：使用者设置费用和轮数上限，多调用/恢复共同消耗一份预算；不足时明确停止并保留已确认产物。

47. **[T047 Agent 在上下文上限内使用授权来源和记忆](https://github.com/xiaolin624/Orchara/issues/48)**
   - Blocked by：#42。
   - What it delivers：使用者得到保留否定条件、引用和审批参数的上下文处理，低相关历史可截断但不会扩大权限或改变金额真值。

48. **[T048 管理员登记固定连接器并阻止越界出站](https://github.com/xiaolin624/Orchara/issues/49)**
   - Blocked by：#42。
   - What it delivers：管理员审核版本化目的地/方法/路径/风险，运行者只能经能力代理调用；SSRF、DNS/重定向绕过均受阻。

49. **[T049 另一使用者运行销售模板并下载正确报告](https://github.com/xiaolin624/Orchara/issues/50)**
   - Blocked by：#43, #44。
   - What it delivers：使用者上传等价 CSV/XLSX，经线性流程得到已验证统计和独立报告附件；展示模型 profile 和金额真值。

50. **[T050 使用者确认真实参数后安全恢复受控操作](https://github.com/xiaolin624/Orchara/issues/51)**
   - Blocked by：#49。
   - What it delivers：使用者从审批页看清实际目标、参数和去向，再批准或拒绝；过期、换参数、撤权和并发相反决定不误执行。

51. **[T051 写后丢响应时暂停核对且真实写次数不增加](https://github.com/xiaolin624/Orchara/issues/52)**
   - Blocked by：#51。
   - What it delivers：使用者看到 unknown 并提交授权核对证据；唯一 CAS 发送者与原 operation 回执保证恢复不会盲目补发。

52. **[T052 用正式 API 运行案例并让独立 checker 判定](https://github.com/xiaolin624/Orchara/issues/53)**
   - Blocked by：#50, #45, #46, #47, #48, #52。
   - What it delivers：创作者触发评测，采集器处理预定义交互、等待终态并读取独立外部账本；空 checker 或模型声称成功不会被当真。

53. **[T053 在候选 S3 上验证封存、匿名拒绝和删除](https://github.com/xiaolin624/Orchara/issues/54)**
   - Blocked by：#45, #46, #47, #48, #52。
   - What it delivers：部署者运行合成对象存储兼容探针，得到签名、staging/sealed、路径和删除行为证据；不兼容实现不得选用。

54. **[T054 用户确认候选后才保存个人长期记忆](https://github.com/xiaolin624/Orchara/issues/55)**
   - Blocked by：#45, #46, #47, #48, #52。
   - What it delivers：使用者在候选页查看来源和应用范围，确认、拒绝或等待过期；只有确认记录进入 active，重复确认幂等。

55. **[T055 上传文本资料后检索到可追溯片段](https://github.com/xiaolin624/Orchara/issues/56)**
   - Blocked by：#45, #46, #47, #48, #52。
   - What it delivers：使用者在知识库页面上传授权 txt/md，查看摄取进度并检索到含来源版本的片段；最小解析和 embedding 走受控后台路径。

56. **[T056 运行销售与坏输入案例并展示逐例结果](https://github.com/xiaolin624/Orchara/issues/57)**
   - Blocked by：#53。
   - What it delivers：创作者从案例界面比较 CSV/XLSX 和冻结边界输入，查看独立 Decimal 期望、实际附件和失败原因。

57. **[T057 在干净开发环境启动并跑通销售冒烟](https://github.com/xiaolin624/Orchara/issues/58)**
   - Blocked by：#54, #50。
   - What it delivers：部署者按实际命令迁移、bootstrap、启动依赖/API/Web/worker，使用明确 profile 完成线性销售和失败/取消示例。

58. **[T058 用户修订或删除记忆后立即停止旧内容召回](https://github.com/xiaolin624/Orchara/issues/59)**
   - Blocked by：#55。
   - What it delivers：使用者在个人数据页查看、修改和删除当前应用记忆，ETag 冲突可处理；缓存和后续模型输入不继续使用旧内容。

59. **[T059 摄取 CSV/XLSX 并保留行范围和安全错误](https://github.com/xiaolin624/Orchara/issues/60)**
   - Blocked by：#56。
   - What it delivers：使用者上传表格资料，得到可核验的行范围片段；公式/解包超限和非法内容有可理解错误。

60. **[T060 摄取普通文本 PDF 并对比解析分块方案](https://github.com/xiaolin624/Orchara/issues/61)**
   - Blocked by：#56。
   - What it delivers：使用者上传普通 PDF 后得到页码与来源片段；评测基线和 Docling Native/SentenceSplitter，不扩展 OCR 承诺。

61. **[T061 更新资料或 embedding 后不激活陈旧索引](https://github.com/xiaolin624/Orchara/issues/62)**
   - Blocked by：#56。
   - What it delivers：管理员更新版本后重建索引并查看进度；源 revision、维度和 tombstone 守卫阻止旧任务晚到覆盖新数据。

62. **[T062 阻止越权、取消和错制品报告通过验收](https://github.com/xiaolin624/Orchara/issues/63)**
   - Blocked by：#57。
   - What it delivers：管理员在报告中核对账号、取消结果、manifest/dataset/compiler/worker 归属；伪造通过或借用他人报告失败。

63. **[T063 运行时按上下文预算召回当前应用的已确认记忆](https://github.com/xiaolin624/Orchara/issues/64)**
   - Blocked by：#59。
   - What it delivers：使用者在一次运行中看到当前授权记忆被有来源地采用；其他用户/应用、未确认和已删除内容不会进入上下文。

64. **[T064 回答只引用本次检索且当前可读的资料](https://github.com/xiaolin624/Orchara/issues/65)**
   - Blocked by：#60, #61, #62。
   - What it delivers：使用者查询知识并打开引用，ACL 在候选前过滤、答案引用可解析；无结果、伪引用和撤销来源分别处理。

65. **[T065 查询异步评测进展并区分业务与设施失败](https://github.com/xiaolin624/Orchara/issues/66)**
   - Blocked by：#63。
   - What it delivers：创作者从评测页刷新进度，最终获得不可变报告；报告 failed 与任务 failed 展示不同原因，封存拥塞仍可完成。

66. **[T066 在隔离数据中对比 Mem0 与 PG 并决定是否值得接入](https://github.com/xiaolin624/Orchara/issues/67)**
   - Blocked by：#64。
   - What it delivers：评测人员比较召回、成本与正文副本清理证据；没有收益或治理不合格就保留 PG，不将初测当正式采用。

67. **[T067 命中检索缓存也不能读到删除或撤权内容](https://github.com/xiaolin624/Orchara/issues/68)**
   - Blocked by：#65。
   - What it delivers：使用者重复查询可复用合格只读结果，但身份、权限、源版本变化立即失效；可通过对照报告核验收益。

68. **[T068 创作者提交评测快照并由管理员发布同一内容](https://github.com/xiaolin624/Orchara/issues/69)**
   - Blocked by：#66。
   - What it delivers：创作者在发布页查看用途、风险和可信报告再提交，管理员只批准冻结的同 hash 内容；版本事务与目录投影一致。

69. **[T069 用户删除会话或文件后立即无法再读取](https://github.com/xiaolin624/Orchara/issues/70)**
   - Blocked by：#67, #68。
   - What it delivers：使用者从个人数据页发起删除并查看进度，相关下载/检索/缓存/恢复立刻拒绝，受影响运行进入取消流程。

70. **[T070 撤销问题版本并阻止陈旧目录继续启动](https://github.com/xiaolin624/Orchara/issues/71)**
   - Blocked by：#69。
   - What it delivers：管理员撤销版本，使用者在目录和运行入口看到不可用；旧运行保留原制品引用，不偷偷替换最新版。

71. **[T071 异步清理对象和索引并可观察失败重试](https://github.com/xiaolin624/Orchara/issues/72)**
   - Blocked by：#70。
   - What it delivers：使用者查询 remaining scopes，后台幂等清理对象、向量、缓存和孤儿暂存；失败保持禁读并不误删合法引用。

72. **[T072 清理 checkpoint 和上下文副本并登记备份删除范围](https://github.com/xiaolin624/Orchara/issues/73)**
   - Blocked by：#70。
   - What it delivers：部署者执行删除后核对 checkpoint、摘要、临时沙箱和第三方历史，保留可供备份恢复回放的独立账本。

73. **[T073 用户限范围授权诊断且每次正文读取都有审计](https://github.com/xiaolin624/Orchara/issues/74)**
   - Blocked by：#70。
   - What it delivers：使用者在界面设置诊断对象、用途与期限后授权或撤销；管理员只在有效范围内读正文，日志默认脱敏。

74. **[T074 从目录指定版本提交需求并有序排队](https://github.com/xiaolin624/Orchara/issues/75)**
   - Blocked by：#71。
   - What it delivers：使用者点击固定版本并带附件提交，获得 Request/Run 关联，重复提交幂等；同会话后续普通请求可见且可取消。

75. **[T075 在创作台选择固定子应用并校验依赖](https://github.com/xiaolin624/Orchara/issues/76)**
   - Blocked by：#71。
   - What it delivers：创作者选择有权调用的发布版本，声明 I/O 映射并看到循环/撤销/类型错误；父版本固定子依赖。

76. **[T076 删除后恢复与组件历史不能让内容复活](https://github.com/xiaolin624/Orchara/issues/77)**
   - Blocked by：#72, #73, #74。
   - What it delivers：安全评估者重放晚到摄取、封存、旧 checkpoint 与组件召回，验证已删输入拒绝恢复、所有治理门禁有证据。

77. **[T077 浏览授权用途目录并在索引落后时安全检索](https://github.com/xiaolin624/Orchara/issues/78)**
   - Blocked by：#75。
   - What it delivers：使用者只能检索当前可读且未撤销版本；陈旧索引不泄漏描述，授权全文回退仍可用。

78. **[T078 主应用委派后查看独立子运行并取回结果](https://github.com/xiaolin624/Orchara/issues/79)**
   - Blocked by：#76。
   - What it delivers：使用者看到 child Run 时间线和结构结果；DeepAgents 委派经平台适配，重复调用复用原 child，最少上下文和 ACL 交集生效。

79. **[T079 自然语言需求通过选择或澄清进入唯一主运行](https://github.com/xiaolin624/Orchara/issues/80)**
   - Blocked by：#78。
   - What it delivers：使用者提出需求后看到唯一选择、歧义候选或缺输入提问，原请求控制回复可继续；过期回复不能复活任务。

80. **[T080 并行子调用只消耗同一份父预算](https://github.com/xiaolin624/Orchara/issues/81)**
   - Blocked by：#79。
   - What it delivers：使用者并行委派时仍受父费用、次数和深度上限，结果或超限原因返回父任务，恢复不能重置额度。

81. **[T081 父任务满载等待时子任务仍能运行并收敛](https://github.com/xiaolin624/Orchara/issues/82)**
   - Blocked by：#79。
   - What it delivers：部署者让父任务全部进入等待，独立子槽继续执行；取消和必需失败会等待所有受控单元停止后收敛。

82. **[T082 切换处理应用前确认最少交接信息](https://github.com/xiaolin624/Orchara/issues/83)**
   - Blocked by：#80。
   - What it delivers：使用者在同一会话切换应用时检查摘要与附件范围；新应用不会自动获得旧内部历史或个人记忆。

83. **[T083 在画布运行条件分支并只汇合已激活路径](https://github.com/xiaolin624/Orchara/issues/84)**
   - Blocked by：#81, #82, #68。
   - What it delivers：创作者配置比较条件和配对 join，运行 true/false/default 并看到未选分支 skipped；非法跨区边有定位错误。

84. **[T084 冻结路由案例并由另一用户完成销售旅程](https://github.com/xiaolin624/Orchara/issues/85)**
   - Blocked by：#83。
   - What it delivers：评测人员从对话界面复现唯一/歧义/无匹配/未见应用任务，记录误自动执行、澄清和最终文件结果。

85. **[T085 在画布运行并行嵌套并正确处理分支失败](https://github.com/xiaolin624/Orchara/issues/86)**
   - Blocked by：#84。
   - What it delivers：创作者配置并行/嵌套与 optional/required，看到命名结果及 fail-fast 收敛，join 不死等也不重复。

86. **[T086 配置单次模型节点并验证结构输出](https://github.com/xiaolin624/Orchara/issues/87)**
   - Blocked by：#84。
   - What it delivers：创作者在模型节点面板选固定 profile 和响应 Schema，运行后得到包装输出或独立校验错误。

87. **[T087 配置人工审批节点并在重启后继续原决定](https://github.com/xiaolin624/Orchara/issues/88)**
   - Blocked by：#84。
   - What it delivers：创作者在画布加入人工等待，使用者在审批页决定后恢复同运行；通用审批不能授权后续未知外部写。

88. **[T088 把知识检索节点的真实引用传给后续步骤](https://github.com/xiaolin624/Orchara/issues/89)**
   - Blocked by：#84。
   - What it delivers：创作者在画布选知识库和合法字段，使用者运行后可打开真实来源；无结果和撤权按契约处理。

89. **[T089 在画布调用固定子应用并映射结果](https://github.com/xiaolin624/Orchara/issues/90)**
   - Blocked by：#84。
   - What it delivers：创作者选择固定子版本并在变量面板映射 I/O，运行后看到独立 child 和原结构结果；并发状态不串扰。

90. **[T090 冻结动态候选路由的强提示基线与合法训练数据](https://github.com/xiaolin624/Orchara/issues/91)**
   - Blocked by：#85。
   - What it delivers：评测人员先验证错误是否值得训练，冻结检索和留出任务，按任务家族隔离数据并记录来源许可。

91. **[T091 保存并恢复含十节点的复杂工作流](https://github.com/xiaolin624/Orchara/issues/92)**
   - Blocked by：#86, #87, #88, #89, #90。
   - What it delivers：创作者完整配置十类节点和变量，执行两次等待与嵌套汇合后恢复，检查合法字段、导出白名单及错误定位。

92. **[T092 在独立训练宿主完成一轮资源和配置探针](https://github.com/xiaolin624/Orchara/issues/93)**
   - Blocked by：#91。
   - What it delivers：开发者选择合法可用基模、模板与 tokenizer，运行最小训练并记录真实硬件和不可变 manifest；缺 GPU 保持 blocked。

93. **[T093 从外部客户端验证全量 REST/SSE 共同契约](https://github.com/xiaolin624/Orchara/issues/94)**
   - Blocked by：#92, #85, #77。
   - What it delivers：集成开发者按正式接口调用所有首版端点，获得一致分页/幂等/错误/ETag 行为；缺端点或越权即失败。

94. **[T094 用三个 seed 训练独立且可追溯的 LoRA 产物](https://github.com/xiaolin624/Orchara/issues/95)**
   - Blocked by：#93。
   - What it delivers：开发者使用固定数据和配置完成三次训练，输出唯一目录与 adapter/hash、资源、耗时，不能覆盖旧产物。

95. **[T095 跨角色刷新与撤权后界面保持真实授权状态](https://github.com/xiaolin624/Orchara/issues/96)**
   - Blocked by：#94。
   - What it delivers：使用者切换角色、刷新页面或被禁用时看到正确导航与数据，列表详情不会延续过期身份；不是补做此前未实现页面。

96. **[T096 浏览器断线与父子等待时仍能解释真实运行状态](https://github.com/xiaolin624/Orchara/issues/97)**
   - Blocked by：#94。
   - What it delivers：使用者经历 SSE 断线、窗口过期、finalizing、取消和 child 等待后能从快照修正页面；部分产物不伪装成功。

97. **[T097 对强提示与 LoRA 做未见应用和候选乱序对照](https://github.com/xiaolin624/Orchara/issues/98)**
   - Blocked by：#95。
   - What it delivers：评测人员在同基模同输入候选下配对比较三 seed，报告拒识、成本、延迟、退化和不确定性；loss 不是收益。

98. **[T098 手机完成对话、审批和个人数据控制](https://github.com/xiaolin624/Orchara/issues/99)**
   - Blocked by：#96, #97。
   - What it delivers：使用者在小屏上传需求、批准真实参数、查看结果及记忆/删除/诊断状态；不承诺手机完整画布编辑。

99. **[T099 按真实实验结论登记或拒绝上线模型](https://github.com/xiaolin624/Orchara/issues/100)**
   - Blocked by：#98。
   - What it delivers：管理员检查留出收益与兼容报告，符合条件才登记获准模型版本；无收益可保留实验结论，未运行不能完成。

100. **[T100 复现账号到发布和后台执行的关键旅程](https://github.com/xiaolin624/Orchara/issues/101)**
   - Blocked by：#99。
   - What it delivers：评测人员从空环境跑通账号启动、模型验证、草稿评测发布、worker 终结和后台任务五条完整旅程。

101. **[T101 复现请求、审批、记忆、知识及服务账号旅程](https://github.com/xiaolin624/Orchara/issues/102)**
   - Blocked by：#99。
   - What it delivers：评测人员用隔离账号和正式 API 跑通其余五条关键旅程，独立检查实际状态、权限和外部写结果。

102. **[T102 冻结七类内部案例并证明没有切分泄漏](https://github.com/xiaolin624/Orchara/issues/103)**
   - Blocked by：#101, #102。
   - What it delivers：评测人员准备可再分发的 sales/routing/rag/memory/delegation/approval/lifecycle 案例、真值和采集规则，拒绝空验。

103. **[T103 对同一录制结果比较 hard checker 与质量评分](https://github.com/xiaolin624/Orchara/issues/104)**
   - Blocked by：#103。
   - What it delivers：评测人员用获批 judge 和首个通用适配器比较语义指标、实际数据外发与成本；高质量分不能覆盖硬失败。

104. **[T104 导入 tau2 改编任务并拒绝空判定器](https://github.com/xiaolin624/Orchara/issues/105)**
   - Blocked by：#103。
   - What it delivers：评测人员保留来源/许可/环境/checker 后运行小子集，没有评价标准的任务被拒而不是自动高分。

105. **[T105 导入 AgentDojo 改编任务并分开报告攻击成功](https://github.com/xiaolin624/Orchara/issues/106)**
   - Blocked by：#103。
   - What it delivers：安全评估者向检索/工具输出注入恶意指令，同时检查正常任务和真实副作用；security=True 正确映射攻击成功。

106. **[T106 故障注入审批重启、换参和写后丢回执](https://github.com/xiaolin624/Orchara/issues/107)**
   - Blocked by：#103。
   - What it delivers：评测人员复现 FAULT-01/02/03，以真实操作账本证明同 ID、原参数批准和不盲重发。

107. **[T107 故障注入旧图、活旧进程、取消与事件竞争](https://github.com/xiaolin624/Orchara/issues/108)**
   - Blocked by：#103。
   - What it delivers：评测人员复现 FAULT-04/05/06/10，以进程/DB/状态证据证明不双跑、不复活、seq 和决定唯一。

108. **[T108 故障注入子应用并发、满槽与嵌套汇合](https://github.com/xiaolin624/Orchara/issues/109)**
   - Blocked by：#103。
   - What it delivers：评测人员复现 FAULT-07/08/13，检查独立 thread、子槽进展和 join 恰一次与失败收敛。

109. **[T109 故障注入删除、限流、Redis 和权限撤销](https://github.com/xiaolin624/Orchara/issues/110)**
   - Blocked by：#103。
   - What it delivers：评测人员复现 FAULT-09/11/12/14，检查无陈旧正文、有限重试、事实不丢和下一调用撤权。

110. **[T110 执行平台身份、文件、出站与注入安全回归](https://github.com/xiaolin624/Orchara/issues/111)**
   - Blocked by：#105, #106, #107, #108, #109, #110。
   - What it delivers：安全评估者使用外部 API/浏览器和企业 stub 检查 IDOR、token、SSRF、XSS/CSV 与提示注入，实际越权和副作用为零。

111. **[T111 在正式 Linux 重跑构建与脚本隔离攻击](https://github.com/xiaolin624/Orchara/issues/112)**
   - Blocked by：#107, #108, #109, #110。
   - What it delivers：安全评估者在完整集成平台执行文件/网络/凭据/资源攻击，证明隔离与清理在产品路径中仍成立。

112. **[T112 用真实模型小并发重复测量质量、延迟和费用](https://github.com/xiaolin624/Orchara/issues/113)**
   - Blocked by：#104, #111。
   - What it delivers：评测人员在获批配额下至少三次实际调用并报告范围，不能从替身吞吐推断供应商能力。

113. **[T113 对比缓存开关且验证过期越权命中为零](https://github.com/xiaolin624/Orchara/issues/114)**
   - Blocked by：#111。
   - What it delivers：评测人员固定数据、模型和资源，比较缓存的延迟/费用及删除撤权后的真实命中情况。

114. **[T114 持续执行 20 个顶层替身任务并测量容量](https://github.com/xiaolin624/Orchara/issues/115)**
   - Blocked by：#111, #112。
   - What it delivers：部署者以 100 用户/1000 应用/20 顶层活跃画像，500ms stub 持续 15 分钟三次，独立报告执行与排队资源。

115. **[T115 让未参与开发者完成创作、坏输入和审批任务](https://github.com/xiaolin624/Orchara/issues/116)**
   - Blocked by：#115, #113, #114。
   - What it delivers：至少三名未参与者从浏览器完成未见任务，观察求助、误解与状态复述；修复后复测，形成分项验收结论。

116. **[T116 在新 Linux 环境部署完整首版并拒绝危险配置](https://github.com/xiaolin624/Orchara/issues/117)**
   - Blocked by：#116, #100, #58。
   - What it delivers：部署者用固定镜像和明确模型 profile 启动平台与独立脚本宿主，跑完整旅程；缺 gVisor/生产密钥等拒绝正式启动。

117. **[T117 升级平台并保持旧任务可安全恢复](https://github.com/xiaolin624/Orchara/issues/118)**
   - Blocked by：#117。
   - What it delivers：部署者先备份和迁移预检，再部署兼容 worker 与冒烟；原制品仍可用，回滚不随意降数据库 Schema。

118. **[T118 将 DB/WAL、对象和镜像恢复到隔离环境](https://github.com/xiaolin624/Orchara/issues/119)**
   - Blocked by：#118。
   - What it delivers：部署者从真实备份恢复一致的数据与制品，量测实际 RPO/RTO；恢复期间不开放普通读取和执行。

119. **[T119 恢复备份后回放删除和撤销账本再开放访问](https://github.com/xiaolin624/Orchara/issues/120)**
   - Blocked by：#119。
   - What it delivers：部署者先撤销旧 Key/token、回放删除、核对 unknown，再逐项恢复任务；被删内容和未知写不因灾难恢复复活。

120. **[T120 按运行手册处置积压、失联和依赖故障](https://github.com/xiaolin624/Orchara/issues/121)**
   - Blocked by：#120。
   - What it delivers：部署者通过指标告警发现 job/unknown/删除延迟，按已验证命令处置；DB/Redis 故障不放开副作用或配额。

121. **[T121 交付可在另一机器复现的完整首版证据包](https://github.com/xiaolin624/Orchara/issues/122)**
   - Blocked by：#121。
   - What it delivers：维护者核对全部父门禁、候选采用/淘汰、训练与真实容量/恢复结果，由新部署者独立复现并明确限制。

## 审查结论

- 24 条新票合并原本分散的相关能力；20 条旧草案细分为更窄行为。
- 124 条来源全部有去向，106 个 API operation 各有一个实施主责。
- 依赖闭包约简前后一致；177 条直接阻塞边无循环，最多 6 个直接阻塞项。
- 用户功能的数据、接口、界面与行为测试同票交付；独立探针/部署/训练通过可重放 CLI 或实验入口验收。
- 来源、需求与接口责任映射见 published-tickets.json。
- 没有关闭、修改已有父 Issue，没有将文档校验或发布 Ticket 记为实现通过。
