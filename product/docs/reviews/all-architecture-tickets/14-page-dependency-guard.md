# 14: [C6.01] 页面显式加载共享状态依赖并处理资源失败

已发布：[GitHub #155](https://github.com/xiaolin624/Orchara/issues/155)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C6.01 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

chat、运行详情、debug 的共享状态脚本缺失或损坏时，用户看到明确提示，受影响功能暂停，仍可导航和重新加载。

## Acceptance criteria

- [ ] 扩展显式共享状态 interface 并暂留旧出口，建立完整依赖就绪检查；真实 HTML 顺序与全部受影响旧 harness 加载实际共享字节。
- [ ] 真实资源 404、语法错误或未完整导出均显示“运行状态暂不可用”，不启用旧规则或原始快照替代状态。
- [ ] 逐页列出并验证暂停的实时观察/相关按钮，保留导航、重载及独立页面功能，避免锁死整页。
- [ ] 保留正常路径状态逻辑，新增依赖失败验收；浏览器实际故障与 Node 隔离结果分别记录。

## Blocked by

None (can start immediately).

## Traceability

原报告：C6；总规格 User Stories：78, 79, 80, 81, 83。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

