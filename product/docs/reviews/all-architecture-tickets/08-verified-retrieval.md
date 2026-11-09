# 08: [C3.01] 普通知识检索统一读取并验证正文摘要与字节数

已发布：[GitHub #149](https://github.com/xiaolin624/Orchara/issues/149)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C3.01 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

知识库使用者通过现有检索得到当前允许读取且正文完整的片段，坏片段被跳过，部分或空结果不被缓存。

## Acceptance criteria

- [ ] 复用既有权限谓词，集中有界 metadata SQL、对象打开/关闭和原始字节摘要/长度校验。
- [ ] 从权威 artifact 取得摘要及 size_bytes，单次有界读取校验；同长度替换、截断、大小不符和非法 metadata 均不能作为有效正文返回。
- [ ] 保持 owner/tombstone/活动索引/正文归属、候选上限与当前 embedding 筛选；不新加文档最新 revision/ready 限制。
- [ ] 保留 SQL 整体错误与逐对象失败区别，跳过坏片段且不缓存部分/空结果，不泄露对象路径。
- [ ] 通过真实检索、PG 和对象字节验证正常与损坏路径，记录新增校验成本。

## Blocked by

None (can start immediately).

## Traceability

原报告：C3；总规格 User Stories：30, 31, 32, 33, 34, 38, 39。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

