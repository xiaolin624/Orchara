# 13: [C4.03] 删除假线性视图并闭环节点调度恢复回归

已发布：[GitHub #154](https://github.com/xiaolin624/Orchara/issues/154)。本轮回读状态 open / ready-for-agent；发布不等于实现或验收。以下为已核对的远端正文。

<!-- orchara-all-architecture:2026-11-08:C4.03 -->

## Parent

Part of #160 — https://github.com/xiaolin624/Orchara/issues/160

规格来源：#160（完整六项、89 条用户故事）。

## What to build

Agent/call_app 的复用不再依赖图伪装线性结构，调度器仍准确处理跳过、等待、排空、恢复和唯一终态。

## Acceptance criteria

- [ ] 删除 graphLinearView 及目标图专用 available/skip/scopeIDs 覆盖和空 hook，目标节点移除隐式 graph capture 分流。
- [ ] 保留其他节点仍用的 capture 与调度器内部记录，不借此重写十节点运行时或更改 Go 权威。
- [ ] 真实验证每 attempt skipped、optional、每激活 join 一次、waiting/失败/取消排空、恢复封存输出与唯一最终提议。
- [ ] 运行节点/图/线性及真实 call_app 相关回归，分别标记 Python 独立证明与 Go 实际执行证据。

## Blocked by

- Blocked by: #153 — C4.02 固定子应用调用接入共享节点执行约定

## Traceability

原报告：C4；总规格 User Stories：51, 52, 53, 54, 55, 56。保留既有业务入口和已接受 ADR；实际结果写入本票证据，不以设计文案代替运行。

