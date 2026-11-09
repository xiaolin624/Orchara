# 11: [C4.01] Agent 节点通过显式上下文复用图与线性执行

已发布：[GitHub #152](https://github.com/xiaolin624/Orchara/issues/152)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C4.01 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

图与线性工作流的 Agent 节点从明确允许的输入与封存事实执行，返回局部决定，由各自调度器处理全局结果。

## Acceptance criteria

- [ ] 建立受约束的节点执行输入和 typed outcome，覆盖成功、失败、取消、waiting 与 sandbox/退出证据。
- [ ] Agent 绑定与 context 构建不依赖假 linearPlan；启动时 namespace 稳定，不把计划顺序当作完成事实。
- [ ] 目标节点不自行提交 Run 终态、跳过兄弟或通过隐藏 ctx 判断图/线性模式；调度器接收决定。
- [ ] 保留受控工具/子应用、schema、artifact 注册和封存复用语义，局部成功不能绕过登记失败。
- [ ] 验证图/线性 Agent、optional 失败、waiting/取消兄弟排空、恢复与已封存输出复用。

## Blocked by

None (can start immediately).

## Traceability

原报告：C4；总规格 User Stories：45, 46, 47, 50, 51, 52, 53, 54, 55。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

