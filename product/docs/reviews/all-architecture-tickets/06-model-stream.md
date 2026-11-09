# 06: [C2.02] 流式主调用与 fallback 接入同一尝试驱动

已发布：[GitHub #147](https://github.com/xiaolin624/Orchara/issues/147)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C2.02 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

流式使用者得到与同步一致的生命周期规则，每个真实尝试独立 generation，失败草稿和半条工具调用不会混入最终结果。

## Acceptance criteria

- [ ] 流式主候选和 fallback 使用已存在的同一驱动，保留 StreamProvider 与 generation 的实际内容差异。
- [ ] 每尝试独立 generation，失败草稿丢弃、仅完整工具调用进入最终校验；不通过同步伪 SSE 实现。
- [ ] retry/fallback 通知 emit 失败即停止后续派发；消费者停止和 ctx 取消不再重试/fallback。
- [ ] 已封存成功后 final 帧投递失败不撤销成功；完整响应后校验/封存失败消费处理与实际候选一致。
- [ ] 通过现有流式链路观察帧、实际请求、PG 账本和封存字节，保留既有授权/重放与半流回归。

## Blocked by

- Blocked by: #146 — C2.01 同步主调用与兼容 fallback 共用模型尝试生命周期

## Traceability

原报告：C2；总规格 User Stories：14, 18, 19, 22, 23, 24, 25, 26, 27, 28, 29。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

