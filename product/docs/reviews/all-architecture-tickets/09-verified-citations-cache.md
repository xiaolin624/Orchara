# 09: [C3.02] 引用、回答和缓存复检接入同一已验证读取

已发布：[GitHub #150](https://github.com/xiaolin624/Orchara/issues/150)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C3.02 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

打开引用、返回答案和使用缓存都经过同一正文完整性标准；缓存瞬态故障可恢复，损坏内容不会从 miss 路径重新返回。

## Acceptance criteria

- [ ] 引用和缓存复检共用已验证读取与 metadata 规则，当前 provenance、正文、摘要与大小均核对。
- [ ] 引用正文不可读保持安全 404；回答返回前仍逐引用复检，不返回失效引用答案，不缓存最终答案。
- [ ] 缓存确定性缺失/损坏使整条 miss 并丢弃；瞬态读/DB 故障只 miss 保留条目，恢复后可再次命中。
- [ ] 缓存不能绕过 embedding 检查、owner 与版本；既有引用不要求当前 embedder 相同。
- [ ] 迁完三路后移除裸正文读取入口及重复校验；以真实端点和对象字节验证 cache on/off 等价和公开错误。

## Blocked by

- Blocked by: #149 — C3.01 普通知识检索统一读取并验证正文摘要与字节数

## Traceability

原报告：C3；总规格 User Stories：30, 31, 32, 33, 34, 37, 40, 41, 42, 43, 44。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

