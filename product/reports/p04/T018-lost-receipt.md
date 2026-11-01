# P04 T018 验收：写成功丢回执后恢复仍只写一次

- 任务：P04 / GitHub Issue #19（`T018`）
- 状态：**passed**
- 代码：`probes/p04_unknown/unknown_probe.py`
- 证据：[`unknown-receipt.json`](unknown-receipt.json)、[`unknown-ledger.jsonl`](unknown-ledger.jsonl)
- 测试：`pytest product/tests/p04 -q` → **16 passed**

## 1. 场景

外部服务**已经应用**了写入，但响应没有回到调用方。调用方无法区分"已应用"和"没应用"，
盲目重试就会写出第二条。

探针如实建模：

- stub 服务把每次写入记录到**独立账本**，然后直接断开连接、不回响应（丢回执）；
- 客户端把这次操作标成 `unknown`——**既不是成功也不是普通失败**；
- 对账走独立账本：账本里有一条 → 判定完成，**不重发**；
- 账本没有记录且没有"未发送证明" → 保持 `unknown`，**不自动补发**。

## 2. 实测结果

```json
{
  "initial": { "state": "unknown", "sends": 1,
               "detail": "RemoteDisconnected: Remote end closed connection without response" },
  "final":   { "state": "completed", "sends": 1,
               "detail": "reconciled from ledger (1 record(s)); not resent" },
  "ledger_entries": 1
}
```

| 断言 | 结果 |
|---|---|
| 回执确实丢了 | true |
| `unknown` 不等于成功/失败 | true |
| 独立账本记录**恰好一次**写入 | true |
| 从账本对账后完成，且未重发 | true |
| 全程只发送 1 次 | true |

## 3. 三条决策规则（各有测试）

| 情形 | 结果 | 理由 |
|---|---|---|
| 账本里有记录 | `completed`，不重发 | 已应用，重发就是重复写 |
| 账本无记录 + 有"未发送证明" | `failed`（允许下一次尝试） | 证明没发出去，才可重试 |
| 账本无记录 + 无证明 | `unknown`，**不自动补发** | "查不到"不等于"没发生" |

`test_delivery_failure_is_unknown_not_failed` 还验证了：连接被重置/超时得到的是 `unknown`，
不是 `failed`——把超时当失败是重复写的根源。

## 4. 覆盖的验收条件

- [x] 实际写次数恰为 1
- [x] `unknown` 不算成功/普通失败
- [x] 没有未发送证明不能自动补发

## 5. 边界

- 账本是本地 JSONL 文件，代表"外部系统自己的记录"；真实场景需要对方提供幂等键查询接口。
- 只覆盖单个外部调用；多步事务与补偿属执行器语义（F13）。
- 没有实现自动对账轮询，只实现"一次对账 + 决策"。
