# 10: [C3.03] 验证重建、来源删除与损坏恢复的知识读取一致性

已发布：[GitHub #151](https://github.com/xiaolin624/Orchara/issues/151)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C3.03 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

旧活动索引在合法重建期间可读，但删除来源、替换索引或正文损坏后，各读取路径都按当前事实处理。

## Acceptance criteria

- [ ] 真实验证源 A 活动→替换 B 重建中/失败→删除 A，记录结果；若复现泄漏则按既有即时删除规则修正，不以禁止旧索引读掩盖。
- [ ] 验证重建失败旧活动索引保留、成功激活后旧引用失效、删除/撤权立即阻止片段。
- [ ] 验证瞬态读取失败后缓存恢复、回答前引用再次失效，以及三路径篡改/截断/大小不符对照。
- [ ] 运行相关完整知识回归及成对 cache on/off 对照，报告新增校验开销与未运行条件。

## Blocked by

- Blocked by: #150 — C3.02 引用、回答和缓存复检接入同一已验证读取

## Traceability

原报告：C3；总规格 User Stories：34, 35, 36, 41, 42, 44。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

