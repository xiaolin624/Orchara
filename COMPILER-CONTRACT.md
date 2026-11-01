# DSL编译与节点适配细则

本文件细化WORKFLOW-SPEC；不是另造DAG引擎。LangGraph负责图执行/持久化，平台编译器负责受限DSL到框架图的正确映射。API名字以P01锁定版本为准，下述为语义而非可直接复制的SDK代码。

## 1. 类型、模板与绑定

应用I/O首版接受受限JSON Schema：type/properties/required/items/additionalProperties、enum/const、长度/数量/数值上下限、title/description及受控format；union只接受单类型+null。拒绝远程$ref、动态代码及未准入关键字，不让两个语言对同一Schema各自猜测。

节点输出均为JSON object。text模式模型输出包装成`{text:...}`；json模式按node.output_schema校验，Agent最终结果同样校验。Python工具协议的`output.json.data`才是节点结构化输出，`artifacts`由平台另登记，不能将脚本给的路径当已授权artifact ID。

引用按JSON Pointer解析，拒绝无效转义/不存在字段/非祖先；类型必须兼容，必需输出值在所有可执行路径上存在。可选分支结果只在配对join后按来源读取。输入缺失不隐式变null；需要空值时Schema显式nullable并由生产者返回null。

模板只支持`{{input_name}}`取当前节点已绑定输入；对象/数组以受限JSON呈现；不执行表达式、属性链、循环或环境读取。输入/输出尺寸和token预检先执行，提示不包含无权数据。

condition无类型强转：eq/ne比较JSON类型和值，gt/gte/lt/lte仅数值，in要求列表，exists检查已经绑定的值非null；缺失绑定仍报错。金额是Decimal字符串，需要数值比较时先由确定性工具产出布尔字段，不用float猜金额。

## 2. 结构化region与汇合

验证器计算支配/后支配，确保每个condition或并行fork有唯一配对join、区域无交叉入口/出口，允许严格嵌套。required_sources/optional_sources必须覆盖该region实际直接分支终点且互斥；未知/重复来源拒绝发布。

编译时把每个branch作为独立结构化子图/节点：条件region通过框架条件边只激活一个branch，所有选中路径到配对join；并行region显式激活每个branch，以所有branch终点作为汇合依赖。嵌套region先在自身join收敛再进入父region。不能把未选条件分支也放进无条件all-of屏障，否则会永远等待。

join标准输出为`{results:{source_node_id:{status,value,error_code?}}}`。status为succeeded/failed/skipped；未激活branch记录skipped，不算失败。all_active只等待激活branch，至少一个必需来源被激活；optional只允许纯读/计算的明确错误输出，不吞掉未知写或越权。

共享图状态按node/invocation命名保存，不让并行节点覆盖同一messages/list。reducers需幂等地合并稳定结果键；异常/重放不重复追加。跨应用child Run使用独立thread，不把库内子图namespace当平台鉴权。

## 3. NodeAdapter接口语义

`execute(NodeContext, validated_inputs) → NodeOutcome`，NodeContext来自固定snapshot、当前run/attempt、作用域能力token、截止时间/预算与逻辑invocation路径。Outcome为succeeded/output、waiting/pending_ref、failed/error或needs_reconciliation/operation_ref。返回原始厂商异常/对象不算平台结果。

平台wrapper负责输入/输出Schema、去重回执、状态事件、截止时间与错误归类；adapter只执行本节点语义。模型、沙箱、子运行、检索这些真实可替换依赖通过小接口注入；不要为每个简单辅助函数建插件系统。

- model/agent：通过受控ModelAdapter；隐藏retry要可观测/禁用，不允许预算外循环。
- python：取固定tool image与本次输入，出站仅能力代理；文件引用重新验证。data解包后校验，日志与artifact另登记。
- call_app：当前ACL交集、固定版本、parent invocation key幂等；等待时保存child引用并释放执行槽。
- retrieve：查询平台授权检索，返回真实doc/version/chunk/source span；无结果为空，不伪造引用。
- approval：创建/复用服务端proposal，interrupt前无外部写，恢复读原决定。
- start/end：开始只取已验证输入，结束映射并检验整个应用输出；生成final artifact后提交结果提议，等待退出协议再发布终态。

## 4. 最少golden corpus

线性四节点；条件true/false/default；并行两branch；条件嵌并行与并行嵌条件；optional失败；required失败取消其他branch；两次人工等待后汇合；重复child调用；无激活来源；跨region非法边；未知变量/不兼容类型；循环；重放后join只触发一次。

Go发布验证与Python编译运行使用同一fixture/hash；对实际执行结果做断言。文档工具当前有限图linter仅覆盖少数结构负例，不是本编译器实现，不能当P05已完成。
