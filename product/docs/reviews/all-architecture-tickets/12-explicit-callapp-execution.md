# 12: [C4.02] 固定子应用调用接入共享节点执行约定

已发布：[GitHub #153](https://github.com/xiaolin624/Orchara/issues/153)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C4.02 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

图与线性 call_app 都通过显式输入调用固定发布版本，返回局部等待或结果，独立逻辑调用不会误合并。

## Acceptance criteria

- [ ] 两种调度器使用同一 call_app 执行约定，不借用伪线性运行实例；显式绑定与固定子应用版本保持。
- [ ] 保持 logical key 身份，同版本的顺序不同调用、并行子运行均各自独立。
- [ ] waiting 返回 checkpoint/proposal 和执行证据，由调度器排空后唯一提交；取消和失败同样返回局部决定。
- [ ] 通过真实调用与恢复路径验证结果、输入不越界、waiting、兄弟失败和子运行身份，不只做编译检查。

## Blocked by

- Blocked by: #152 — C4.01 Agent 节点通过显式上下文复用图与线性执行

## Traceability

原报告：C4；总规格 User Stories：45, 46, 48, 49, 50, 51, 52, 53, 55。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

