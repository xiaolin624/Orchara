# 15: [C6.02] 对话和节点视图采用统一运行状态投影

已发布：[GitHub #156](https://github.com/xiaolin624/Orchara/issues/156)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C6.02 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

对话和节点视图使用共享状态解释，dispatched 后仍能看到运行变化，取消/收尾不再因页面旧分支显示成完成。

## Acceptance criteria

- [ ] 共享聚合 view 供常用调用，迁移 chat handling/poll/terminal 与 runview 的 Run terminal/cancel 规则。
- [ ] finalizing 是 phase、取消只是意图、unknown 待核对；dispatched Request 继续观察 Run，保持轮询上限。
- [ ] 迟到响应不能覆盖新选择；节点表/节点专用错误与 DOM adapter 保留，不迁入通用网络框架。
- [ ] 删除该批业务 fallback，使用真实依赖运行正常状态、选择切换和取消回归，保持 Q7 故障提示。

## Blocked by

- Blocked by: #155 — C6.01 页面显式加载共享状态依赖并处理资源失败

## Traceability

原报告：C6；总规格 User Stories：67, 68, 69, 70, 71, 76, 77, 82。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

