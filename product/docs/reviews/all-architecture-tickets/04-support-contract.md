# 04: [C5.04] 删除已迁移的测试别名与纯转发入口

已发布：[GitHub #145](https://github.com/xiaolin624/Orchara/issues/145)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C5.04 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

仓库测试直接依赖能力入口，目标票号类型链和纯转发已彻底移除，测试仍能真实执行并清理。

## Acceptance criteria

- [ ] 全仓搜索证明目标 Stack 别名、一次执行转发及通用观察再次导出的调用者清零，再删除兼容入口。
- [ ] 保留有业务作用的票模块，不整文件删除专用 fixture/断言。
- [ ] 受影响测试可收集并运行，至少有真实执行与失败/超时清理证据；不以仅 collection 通过宣布完成。
- [ ] 记录实际删除清单与剩余有理由的支持入口，禁止永久保留两套共享实现。

## Blocked by

- Blocked by: #142 — C5.01 共享真实 Stack 并迁移票号类型别名的调用者
- Blocked by: #143 — C5.02 通过共享能力执行一次真实 supervisor 尝试并回收资源
- Blocked by: #144 — C5.03 共享运行事实读取并迁移图验收调用者

## Traceability

原报告：C5；总规格 User Stories：65, 66。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

