# P02 T005 验收：一个中断任务在新进程中安全继续

- 任务：P02 / GitHub Issue #6（`T005`）
- 状态：**passed**
- 代码：`probes/p02_checkpoint/checkpoint_probe.py`
- 测试：`python -m pytest product/tests/p02 -q` → **7 passed**
- 环境：隔离开发集群 PostgreSQL 16.4 @ `127.0.0.1:55432`（`tools/pg_dev.py`），
  langgraph 1.2.4 + langgraph-checkpoint-postgres 3.0.5 + psycopg 3.3.6（`[binary]`）

## 1. 真实 PG 建表与写读

```powershell
python checkpoint_probe.py init                # schema checkpoint / app + worker 角色
python checkpoint_probe.py setup-checkpointer  # checkpointer.setup() 在 checkpoint schema
# {"checkpoint_tables": ["checkpoint_blobs","checkpoint_migrations","checkpoint_writes","checkpoints"]}
```

checkpointer 表由 `PostgresSaver.setup()` 真实创建在专用 schema 中，不是内存替身。

## 2. 中断 → 新进程恢复

每一步都是**独立操作系统进程**：

```powershell
python checkpoint_probe.py run    --thread T2 --owner alice
# {"stage":"prepared","interrupted":true,"pending_nodes":["approve"],"checkpoint_id":"1f1d5df0-..."}
python checkpoint_probe.py resume --thread T2 --owner alice --decision approve
# {"stage":"done","decision":"approve","resumed_from_pending":["approve"],"finished":true,"operation_rows":1}
```

`run` 在审批节点前停下（`interrupt_before=["approve"]`）；新进程 `resume` 读回原状态与
决定并跑完。`read` 在第三个进程里仍能看到 `stage=prepared / amount=120.50 /
operation_key=op-T2`。

## 3. 六项验收条件

| 条件 | 证据 |
|---|---|
| 真实 PG 建表和写读成功 | `setup-checkpointer` 建出 4 张表；`read` 跨进程读回状态 |
| 运行身份不能 DDL | `guards.ddl.blocked=true`（`permission denied for schema checkpoint`） |
| 运行身份不能跨业务表写 | `guards.cross_module_write.blocked=true`（`permission denied for table operations`） |
| 错误 search_path 被检测 | 正确路径解析到 `checkpoints`；`search_path=public` 时解析不到，`detected=true` |
| 恢复读取原状态与决定 | `resume` 返回 `decision=approve`，`finished=true` |
| 伪造他人 thread 不能读取 | `read --owner bob` → `{"error":"thread_not_owned"}` |
| 同逻辑操作不因重放变新操作 | 再 `resume` → `nothing_to_resume`；`replay --times 3` → `operation_rows=1` |

## 4. 关键设计

- **审批边界用静态中断**：`interrupt_before=["approve"]`，写操作只在 `approve` 节点发生，
  因此"未批准不产生外部写"是结构性的，而不是靠调用方自觉。
- **状态通道显式声明**：`thread_id`/`owner` 必须在 `RunState` 里声明，否则不会进入
  checkpoint，恢复后就拿不到（本次实现踩到并修掉了）。
- **幂等写在存储层**：`app.operations.idempotency_key` 主键 + `ON CONFLICT DO NOTHING`，
  重放三次仍只有一行。
- **权限最小化**：worker 角色只有 `checkpoint` schema 的 DML 与 `app` 的 `SELECT`，
  没有任何 schema 的 `CREATE`。

## 5. 边界

- 只有一张三节点线性图；分支/并行/嵌套汇合属 T011（未完成）。
- 没有真实 worker/supervisor 进程模型、没有租约与心跳，属 P04（T013/T016，未完成）。
- 本机 5432 上的无关 `postgresql-x64-16` 服务全程未被使用。
