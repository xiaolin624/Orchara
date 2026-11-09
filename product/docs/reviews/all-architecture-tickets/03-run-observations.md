# 03: [C5.03] 共享运行事实读取并迁移图验收调用者

已发布：[GitHub #144](https://github.com/xiaolin624/Orchara/issues/144)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C5.03 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

图节点等既有验收直接读取共享的节点、封存产物和 SSE 事实，仍用各场景的独立期望判断结果。

## Acceptance criteria

- [ ] 提取真实复用的节点结果、封存正文、状态/SSE 和最终输出观察能力，并迁移相关图验收批次。
- [ ] 保留票专用工作流、知识准备、审批场景、销售数据、SQL 和业务断言及原报告归属。
- [ ] 不把数据准备与预期值合成同一成功生成器；区分 proposed result、supervisor exit 与 DB committed terminal。
- [ ] 运行代表线性/图或等待恢复真实路径并比较迁移前后观察结果，记录失败或未运行限制。

## Blocked by

- Blocked by: #142 — C5.01 共享真实 Stack 并迁移票号类型别名的调用者

## Traceability

原报告：C5；总规格 User Stories：63, 64, 66。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

