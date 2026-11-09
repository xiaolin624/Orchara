# 05: [C2.01] 同步主调用与兼容 fallback 共用模型尝试生命周期

已发布：[GitHub #146](https://github.com/xiaolin624/Orchara/issues/146)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C2.01 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

使用者通过现有同步 capability 调用完成主候选及兼容 fallback，共用门禁、预留、尝试开闭与结算规则，输出和账本一致。

## Acceptance criteria

- [ ] 保留外部调用与幂等/授权语义，复用既有预留及结算实现，增加私有统一尝试驱动而非另一套账本。
- [ ] 调用前门禁失败不伪造 attempt；真实调用先原子预留，主三次、兼容备一次上限固定。
- [ ] 分别判断 typed transient 重试与 availability fallback；权限/Schema/工具参数拒绝终止，实际候选费率和预算守卫保持。
- [ ] 完整响应后的平台校验或封存失败仍按既有真实 usage 结算；缺 usage、业务取消与兼容探针记账区别保持。
- [ ] 从现有入口观察真实 PG、实际请求数和封存结果，验证重放零派发、预算拒绝、拒绝不重试、同步主/备完整路径。

## Blocked by

None (can start immediately).

## Traceability

原报告：C2；总规格 User Stories：14, 15, 16, 17, 18, 19, 23, 24, 25, 26, 27。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

