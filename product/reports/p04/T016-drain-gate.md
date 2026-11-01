# P04 T016 验收：等待旧进程、沙箱和数据库写全部排空再恢复

- 任务：P04 / `T016`
- 状态：**passed**
- 代码：`probes/p04_drain/drain_gate.py`、`db/migrations/0003_drain.sql`
- 测试：`pytest product/tests/p04 -q` → **9 passed**（8 决策表 + 1 真机集成）

## 1. 门禁要回答的问题

"这次恢复尝试可以开始了吗？" 必须同时满足三份证据，任何一份单独看都会误判：

| 证据 | 来源 | 为什么不能只看它 |
|---|---|---|
| 进程 | `/proc/<pid>/stat` 第 22 字段（start ticks），运行时记录、恢复时比对 | 进程退出不等于写入已落盘 |
| 沙箱 | `runsc list --quiet` | gVisor 沙箱可以比客户端活得更久 |
| 数据库 | `app.pending_writes` 行数 + `pg_stat_activity` 中 `idle in transaction` 会话数 | 会话还在事务里时，写入可能随后才生效 |

## 2. PID 复用不误杀

`ProcessEvidence.verdict()` 有三种结果：

| 观测 | 判定 | 处置 |
|---|---|---|
| `/proc/<pid>` 不存在 | `exited` | 视为已退出 |
| start ticks 与记录相同 | `still_running` | **挂起**，不恢复 |
| start ticks 不同 | `pid_reused` | 视为**旧进程已消失**；不去杀这个号码的新进程，也不把它当旧进程 |

`test_pid_reuse_is_not_treated_as_a_live_process` 断言：复用判定下门禁可以放行，
且证据里保留 `verdict: pid_reused` 供人核对。

## 3. 决策表（每一条都有测试）

| 场景 | 结果 |
|---|---|
| 三份证据齐全 | `resume` |
| 进程仍在 | `keep_pending`（"still running"） |
| 进程已退，但 `pending_writes > 0` | `keep_pending`（"write(s) still in flight"） |
| 进程已退，但有 `idle in transaction` 会话 | `keep_pending` |
| 沙箱仍存在 | `keep_pending` |
| 多个阻塞同时存在 | 全部列出（`len(reasons) == 4`），不只报第一条 |

## 4. 真机集成

`test_real_process_writes_and_sandbox_gate_the_resume`：在 guest 里用 `nohup setsid` 起一个
真实进程并记录 start ticks → 门禁挂起；杀掉进程但保留一条 `pending_writes` → 仍挂起
（**"仅进程退出而 DB 写未排空仍挂起"**）；清空写入记录后 → 放行。

## 5. 覆盖的验收条件

- [x] 仅进程退出而 DB 写未排空仍挂起
- [x] PID 复用不误杀
- [x] 双证据（进程+数据库，外加沙箱）齐全才允许下一尝试

## 6. 边界

- 门禁目前是探针层（Python）。T013 交付的是 fencing token 与能力校验，**并未**把它接进
  `cmd/supervisor` 的恢复路径；`cmd/supervisor` 仍是"只停靠"，恢复决策尚未接线。
- 数据库"排空"用 `pending_writes` 计数 + `idle in transaction` 会话数近似；
  PostgreSQL 的 WAL 落盘与复制滞后未纳入，属运维层检查。
- 沙箱判定按 id 精确匹配；同一 run 起了多个沙箱的场景未覆盖。
