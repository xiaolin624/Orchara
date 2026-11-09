# 02: [C5.02] 通过共享能力执行一次真实 supervisor 尝试并回收资源

已发布：[GitHub #143](https://github.com/xiaolin624/Orchara/issues/143)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C5.02 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

现有执行验收通过一个按能力命名的操作完成真实 finalizer 与 supervisor 的一次执行，正常、失败和超时路径都能观察结果并回收进程。

## Acceptance criteria

- [ ] 迁移现有环境组合、FinalizeWorker 生命周期、实际 supervisor 启动与报告解析，保持每次执行顺序和默认生命周期。
- [ ] 迁移当前一次执行入口及它的直接纯转发调用者；专用取消/并发场景仍可使用显式生命周期能力。
- [ ] 环境覆盖只作用于目标子进程，保持 supervisor/worker 身份区分，不把凭据写入日志。
- [ ] 正常、异常、超时和启动失败均 terminate/必要时 kill 并 wait，记录实际清理证据。
- [ ] 返回进程/报告不被自动解释为 Run 已终态；使用真实 DB/封存/SSE 验证代表运行，保持测试独立断言。

## Blocked by

- Blocked by: #142 — C5.01 共享真实 Stack 并迁移票号类型别名的调用者

## Traceability

原报告：C5；总规格 User Stories：58, 59, 60, 61, 62, 66。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

