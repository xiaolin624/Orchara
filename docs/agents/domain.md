# Domain docs

布局：single-context。

## 读取规则

先按根 AGENTS.md 读取交接入口。
探索领域或设计实现前读取根 GLOSSARY.md，
并按当前工作范围读取 docs/adr/ 中相关决策。
详细规格按 HANDOFF.md 的触发条件加载。

术语表或 ADR 尚不存在时继续工作；
由 domain-modeling 在真实术语或决策确定后按需补充。

## 术语与决策

Issue、设计、代码和测试使用 GLOSSARY.md 定义的术语，
遵守其中的 Avoid 约束。
新增概念先检查已有定义，再记录确有必要的术语缺口。

方案与现有 ADR 冲突时，明确指出对应 ADR、原因和证据。
Q1–Q46 及当前规格的权威顺序遵循 HANDOFF.md。
组件策略确认与实际实验结果分别记录。
