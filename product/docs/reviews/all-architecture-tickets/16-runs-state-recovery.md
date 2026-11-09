# 16: [C6.03] 运行详情统一子运行、产物与 410 快照恢复解释

已发布：[GitHub #157](https://github.com/xiaolin624/Orchara/issues/157)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C6.03 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

运行详情的状态、子任务、产物和事件恢复按共享事实解释，过期历史与安全快照路径清楚可见。

## Acceptance criteria

- [ ] 迁移 child summary、streamFailure、truth、artifact readiness、cancel accepted、snapshot path/correction 的共享规则。
- [ ] 410 丢弃过期 cursor、明确 history loss，禁止从零伪造完整回放或直接跟随未验证 snapshot_url。
- [ ] 保留安全同源 Run 路径、认证/不存在/瞬态分类、取消意图及迟到选择守卫。
- [ ] 最终产物必须与已提交运行事实匹配，子运行等待/失败不能伪装父成功。
- [ ] 移除本批页面业务 fallback，验证实际页面行为与共享字节 Node 逻辑；DOM/可选 renderer 检查保留。

## Blocked by

- Blocked by: #155 — C6.01 页面显式加载共享状态依赖并处理资源失败

## Traceability

原报告：C6；总规格 User Stories：67, 68, 69, 70, 72, 73, 74, 75, 76, 77, 82。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

