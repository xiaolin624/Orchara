# 工作流 DSL、编译与节点语义

契约：[workflow.schema.json](contracts/workflow.schema.json)。DSL 版本 `1.0`。Vue Flow 的 viewport/position/style 存独立 `layout`；布局修改不影响已发布执行版本，执行配置修改必须重发版。

## 1. 发布结构与校验流水线

每个定义有 input_schema、output_schema、nodes、edges、policy。节点有稳定 id、type、inputs、output_schema、config；边表示控制依赖，不隐含数据复制。编辑器使用节点配置面板，业务人员无需手写 DSL；服务端拒绝前端未展示但注入的能力。

发布按顺序校验：JSON Schema → ID 唯一/端点存在 → 一个 start/一个 end、所有节点从 start 可达且可达 end、DAG 拓扑排序 → 分支与汇合规则 → 引用/类型/支配关系 → 固定依赖及镜像 ready → 当前作者授权 → 测试门禁 → 审核内容哈希。任何硬校验失败不能由管理员点击跳过。

Go 与 Python 均消费同一 DSL fixture；Go 负责发布校验，Python 编译前防御性复验。实现可先维护两份小校验器，以 golden corpus 对齐；不是把安全规则只交前端。

## 2. 类型与输入映射

值类型采用受限 JSON Schema：object/array/string/number/integer/boolean/null，限制嵌套深度与数组长度。附件类型是平台定义的 ArtifactRef 对象，必须经服务端解析归属，不能把任意 URL/path 当文件。

输入值只允许：`{"literal": JSON}` 或 `{"ref":"input#/field"}` 或 `{"ref":"node:<id>#/field"}`。后者 JSON Pointer 指向该节点结构化输出。禁止 eval、任意 Python/JS 表达式和动态读取环境变量。大正文用 artifact 引用，不存入所有中间状态。

引用节点必须为控制依赖祖先；所有可能执行路径上必须有该值，除非 inputs 明确给缺失默认值（首版不提供隐式默认）。条件分支结果应在 join 后引用按源节点命名的结果。被 skipped 的节点无输出，引用它为静态错误或运行期 `missing_branch_value`，不默默返回 null。

模型提示模板只允许选择已授权变量，采用转义模板；模板替换不等于允许执行代码。严格 Schema 输出由平台校验，模型的“JSON 看起来合法”不能代替契约验证。

## 3. 十类节点

| type | config 必需项/主要输入 | 输出与错误 | 恢复及扩展 |
|---|---|---|---|
| start | 无；inputs 必须为空 | 已验证平台输入 | 只映射平台授权附件 |
| end | inputs 映射最终输出 | 必须满足应用 output_schema | 不含未确认/跨域 artifact |
| model | model_profile_version、prompt_template、response_mode | 单次生成/分类；Schema 错误独立记录 | 可用性故障有限重试与兼容备用；非 Agent 循环 |
| agent | model_profile_version、instructions、tool_versions、subapp_versions、max_turns | 结构化最终结果及产物 | DeepAgents 受控工具/子运行；超预算明确停止 |
| python | tool_version | 工具声明输出；stdout 协议/文件/超时错误 | 固定环境 digest；纯计算回执可复用 |
| call_app | application_version | 子应用固定输出 | 独立 child Run/thread；禁止动态未发布 ID |
| retrieve | knowledge_base_ids、top_k | 片段、doc_version、页/行、chunk ID | 先权限过滤，删除失效；无结果可为空 |
| condition | cases、default_port | 控制端口和判定结果 | 受限比较，不执行代码；首个匹配分支 |
| join | mode=all_active、required_sources | 按节点 ID 合并输出/错误 | 等待激活分支；可选纯读分支需显式标识 |
| approval | title、proposal 输入、expires_seconds | 决定引用及参数摘要 | interrupt；运行工具仍独立验证具体授权 |

所有节点config字段在JSON Schema中按type白名单化，服务端继续做跨字段/权限/依赖语义校验。工具/模型/子应用引用固定版本，不接受latest；知识库引用稳定KB ID并在运行时按当前授权/资料版本检索，不能把KB ID误称不可变知识快照。

## 4. 条件与并行：首版使用结构化区域

为了使业务用户能理解、编译器能证明跳过语义，首版只接受结构化分支区域：condition 的所有出口在唯一配对 join 重汇合；不允许区域内边跨越到其他区域，允许嵌套。condition config 包含 join_id。并行 fork 使用一个普通节点的多条出边，配对 join 通过 config.fork_id 声明。每个区域入口/出口须符合支配/后支配关系。

condition cases 为顺序列表，每项比较一个已绑定输入：eq/ne/gt/gte/lt/lte/in/exists；不做字符串代码求值。选一端口，其他端口整段标记 skipped。边 port 缺省为 default；条件出口的 port 必须与 cases/default_port 对齐。

join 只等实际激活的输入分支；若无激活必需分支则 `invalid_activation`。分支 skipped 不当成失败；必需激活分支失败按 fail-fast 终止。默认不支持 N-of-M 竞速、任意跨区域连线、动态 map 与跨运行共享变量。编辑器阻止非法连线，服务端仍复验。

LangGraph 编译器必须有一组证明激活 token、skipped 和 join 触发次数的集成测试；不得简单认为“多条 add_edge 就能满足上述所有业务语义”。若 P0 无法正确编译，先禁用分支发布，不能以无测试自研调度器冒充已支持。

## 5. 脚本协议

平台将 JSON 写到 `/workspace/input.json`、授权文件放只读 `/inputs`，输出仅 `/workspace/output.json` 和 `/workspace/artifacts`。脚本启动命令由平台固定，例如 `python /tool/main.py --input /workspace/input.json --output /workspace/output.json`，不接受用户 shell 拼接参数。

`output.json` 包含 `data` 与 `artifacts`；data 按工具 Schema 校验，文件列表使用相对路径。拒绝绝对路径、`..`、符号链接越界、设备文件、超限文件及不在授权目录的文件。stdout/stderr 为有上限的日志，不作为输出 JSON；日志默认不进入运维正文视图。

依赖提交 requirements/lock → 隔离构建 → 扫描+测试 → 固定 image digest。构建执行安装脚本同样不可信，不接触平台密钥。首周只跑管理员预置固定工具；任意上传脚本必须在隔离门禁通过后开放。

## 6. 销售样例与正确性

输入字段：`order_id,date,region,amount`。日期为 ISO 日期；amount 为十进制金额字符串，脚本使用 Decimal；order_id 不重复；空地区/负值/重复订单/缺列明确拒绝，首版不猜测清洗策略。

公开样本三行：东区 120.50、东区 79.50、西区 50.00；真值总额 250.00、东区 200.00、西区 50.00、订单数 3。相同数据提供 CSV 与 XLSX，禁用宏，限制压缩解包/工作表/行数/单元格公式处理。公式输入不执行；首版拒绝含公式的金额列。

v0.3细化见COMPILER-CONTRACT：节点输出object、Python工具data解包、join结果封套、模板/条件类型规则与结构化region编译。policy显式包含budget_limit/currency，样例1.00 USD仅为fixture限额，不是供应商实际费用。manifest及fixture-registry中的占位ID/digest必须由实现seed流程解析/构建，不能直接登记为已运行制品。

流程 start → Python 统计 → Agent 解释 → end。脚本产出 summary.json 与 CSV；Agent 只引用已经验证的统计数值，报告正文与统计附件分开。自动验收金额由确定性提取与比对；语言可读性需人工盲评，不能用模型自评代替数值正确性。
