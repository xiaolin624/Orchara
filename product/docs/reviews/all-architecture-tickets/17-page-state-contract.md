# 17: [C6.04] 清理共享状态兼容出口并验收三页面正常与故障路径

已发布：[GitHub #158](https://github.com/xiaolin624/Orchara/issues/158)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C6.04 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

三类运行页面与测试仅使用同一状态规则，缺依赖时明确暂停，全部目标旧 fallback 和兼容导出已清理。

## Acceptance criteria

- [ ] 逐项核对全部目标 fallback 和旧全局兼容出口，迁移调用者清零后删除；不按 typeof 或 fallback 字符串盲删。
- [ ] 节点状态专用渲染、model fallback 账本展示、输入检查和浏览器能力探测保持正确职责。
- [ ] 真实浏览器验证正常加载及资源 404/语法错误，检查提示、相关操作暂停、导航/重载；记录所用浏览器与环境。
- [ ] 运行受影响 F03/F08/F12 和其他实际引用的页面回归，保留脚本服务字节/顺序、安全快照和迟到响应证据。

## Blocked by

- Blocked by: #156 — C6.02 对话和节点视图采用统一运行状态投影
- Blocked by: #157 — C6.03 运行详情统一子运行、产物与 410 快照恢复解释

## Traceability

原报告：C6；总规格 User Stories：78, 79, 80, 81, 82, 83。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

