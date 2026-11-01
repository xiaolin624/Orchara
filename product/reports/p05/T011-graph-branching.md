# P05 T011 验收：在真实图引擎中验证分支和汇合结果

- 任务：P05 / `T011`
- 状态：**passed**（引擎语义；与 T007 校验器的接线见第 5 节）
- 引擎：LangGraph（worker 实际使用的引擎，不是模拟器）
- 代码：`probes/p05_graph/graph_probe.py`
- 证据：[`graph-probe.json`](graph-probe.json)
- 测试：`pytest product/tests/p05 -q` → **8 passed**

## 1. 分支：未选中的那条不会执行，并被报告为 skipped

条件边 `entry → (left | right)`，两条分支汇到 `join`：

```text
route=left  → executed [entry, left, join]   skipped [right]
route=right → executed [entry, right, join]  skipped [left]
```

`skipped` 不是引擎给出的状态，而是探针**从执行轨迹推导**的：记录了哪些节点真正跑过，
再与声明分支集合相减。没有把"引擎没跑"冒充成"引擎报告了 skipped"。

## 2. 汇合：恰一次、无死等

| 场景 | join 执行次数 |
|---|---|
| 条件分支（左右各一次） | 1 |
| 并行扇出（两条分支同时跑） | 1 |
| 嵌套分支（分支里的分支） | 1 |
| 一条分支失败 | 1 |

`executed.count("join") == 1` 与 `join_count == 1` 双重断言。

## 3. 并行与嵌套

- **并行**：`entry` 同时连到 `left` 和 `right`，两者都执行，然后汇合一次。
  这里踩到一个真实约束：LangGraph 对同一 superstep 写同一个 key 会抛
  `InvalidUpdateError`，必须用 `Annotated[list, operator.add]` reducer。已按此修正，
  否则并行 fixture 根本起不来。
- **嵌套**：外层选中的分支里再放一个条件边；实测只跑 `inner_right`，`inner_left` 未执行。

## 4. 失败收敛

一条分支记录失败（`bad_branch:RuntimeError`）而不是让整个图崩掉，`join` 仍然执行**一次**。
测试断言"失败被记录"且"汇合仍只一次"。

## 5. 结构性非法图在执行前被拒

| 情形 | 拒绝原因 |
|---|---|
| 跨区边 | `cross_region_edge:a->b` |
| 无激活来源的节点 | `no_active_source:b` |

校验发生在 `invoke` 之前，不靠引擎运行时报错。入口节点由 START 提供，不参与
"无激活来源"判定（这一点写错会让合法图被误拒，已修正）。

## 6. 覆盖的验收条件

- [x] 未选分支为 skipped
- [x] join 恰一次且无死等
- [x] 非法跨区边/无激活来源被拒

## 7. 边界

- 引擎语义用固定 fixture 验证；把 T007 的 DSL 校验结果**编译**成这些图属编译器（F04），
  不在本票范围。本票证明的是运行时行为正确。
- 失败收敛采用"节点记录失败"的约定；异常直接抛出时的引擎行为未覆盖。
- 未接入 checkpointer 的持久化分支（T005/T009 已单独验证恢复与钉版）。
