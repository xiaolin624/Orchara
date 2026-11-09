# 01: [C5.01] 共享真实 Stack 并迁移票号类型别名的调用者

已发布：[GitHub #142](https://github.com/xiaolin624/Orchara/issues/142)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C5.01 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

测试维护者可直接使用按能力命名的真实 Stack，原有 API、证书、数据库、对象目录与签名配置保持一致；首批调用者不再依赖多层票号类型链。

## Acceptance criteria

- [ ] 将真实字段持有者迁为规范 Stack，保留 client 行为与现有 fixture scope，不引入全局可变栈。
- [ ] 扩展新入口并迁移 Stack 类型使用者；兼容别名仅保留到明确 contract 任务，不要求调用者同时学两套配置。
- [ ] 检查证书/身份、对象目录、签名配置及隔离行为一致；敏感值不进报告。
- [ ] 运行测试收集及代表真实栈创建/HTTP 使用场景，记录实际结果；导入成功不代替运行验收。

## Blocked by

None (can start immediately).

## Traceability

原报告：C5；总规格 User Stories：57, 59, 60。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

