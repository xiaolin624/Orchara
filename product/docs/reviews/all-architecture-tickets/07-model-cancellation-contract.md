# 07: [C2.03] 闭环四条模型路径的取消、清理失败与结算回归

已发布：[GitHub #148](https://github.com/xiaolin624/Orchara/issues/148)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C2.03 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

调用者在派发前、调用中、退避或流式通知期间停止时，四条模型路径都停止新请求；清理失败不会伪装为账本已关闭。

## Acceptance criteria

- [ ] 先复现并记录同步/流式 adapter 路由、通知 emit、尝试前取消及清理错误差异，再依已接受规则修正。
- [ ] 取消清理使用有界独立 context，数据库清理失败明确返回/关联错误，不继续派发或声称关闭已成功。
- [ ] 覆盖同步/流式主/备的调用前、调用中、退避与通知取消，检查物理请求数、attempt/operation、预留与费用。
- [ ] 删除被统一驱动取代的四套生命周期循环及无意义转发，保留流式独立内容处理。
- [ ] 记录本地 fake HTTP、stub、真实 PG 与真实供应商的不同证据，缺真实供应商不伪称验收。

## Blocked by

- Blocked by: #147 — C2.02 流式主调用与 fallback 接入同一尝试驱动

## Traceability

原报告：C2；总规格 User Stories：20, 21, 22, 27, 29。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

