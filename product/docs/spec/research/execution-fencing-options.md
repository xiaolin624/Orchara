# 执行租约与 LangGraph checkpoint 写入隔离

查询：2026-10-31。对象：Go 控制面/任务表、Python 整图 worker、AsyncPostgresSaver。只读源码与官方文档；以下是待实现和并发验证的设计，不是已通过测试的协议。

## 已确定的缺口

租约到期只说明控制面允许考虑接管，**没有证明旧 worker 已停止**。`SELECT lease` → `super.aput(...)` 是两个动作：接管可发生在二者之间，因而普通 wrapper preflight 不构成原子 fencing。

已读 LangGraph commit `e5c961d7fbe7ca6fdd4c94c84d90511372514d55`：

- [`base.py::MIGRATIONS / UPSERT_*`](https://github.com/langchain-ai/langgraph/blob/e5c961d7fbe7ca6fdd4c94c84d90511372514d55/libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py#L46)：`checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 都有 thread_id/checkpoint_ns；没有执行租约或 attempt token 列/判断。
- [`aio.py::aput`](https://github.com/langchain-ai/langgraph/blob/e5c961d7fbe7ca6fdd4c94c84d90511372514d55/libs/checkpoint-postgres/langgraph/checkpoint/postgres/aio.py#L237)：先保存 blob，再 upsert checkpoint；`aput_writes` 独立保存 task writes。只拦截 aput 会遗漏 writes。
- [`aio.py::_cursor`](https://github.com/langchain-ai/langgraph/blob/e5c961d7fbe7ca6fdd4c94c84d90511372514d55/libs/checkpoint-postgres/langgraph/checkpoint/postgres/aio.py#L418)、[`_ainternal.py::get_connection`](https://github.com/langchain-ai/langgraph/blob/e5c961d7fbe7ca6fdd4c94c84d90511372514d55/libs/checkpoint-postgres/langgraph/checkpoint/postgres/_ainternal.py)：可以接收固定 AsyncConnection 或从 AsyncConnectionPool 取连接。saver 自身 asyncio.Lock 只保护这个实例，不能协调两个进程。
- `from_conn_string` 使用 autocommit=True；写路径会尝试 pipeline，缺少 pipeline 支持时走 transaction。**autocommit 不代表 pipeline 内每条语句立即独立提交**：Psycopg 说明 pipeline 语句可处在隐式事务，Sync 影响边界；要保证指定语句组原子性应显式 transaction。[Psycopg pipeline](https://www.psycopg.org/psycopg3/docs/advanced/pipeline.html#synchronization-points)。不能仅凭“pipeline=True”推导整个任意 saver 调用与外部 lease 查询原子。

## 选项 A：数据库写入点 fencing，允许租约接管但隔离陈旧写入

这是可实现方向，不是上游现成能力。最小安全边界是让**租约核验和实际 checkpoint DML 处在同一数据库事务，且持有与接管冲突的行锁**。

### 数据与连接契约

1. 控制面维护执行行 `run_id / attempt_token / lease_until / 可写状态`。领取/接管用条件 UPDATE，更新不可复用的 attempt token，提交成功后才启动新 attempt。worker 不能自行把执行行改成新的 token。
2. Python 每个执行 attempt 使用独占 psycopg AsyncConnection 和 saver 实例；在真正开始 graph 前设置 session GUC `app.run_id / app.attempt_token`。设置失败则不运行。连接不跨 attempt 复用，用后关闭；不在共享 pool 借出连接 A 上 SET、却让 saver 在连接 B 上写。
3. `set_config(..., false)` 是 session 范围；`true` 仅当前事务有效。[PG 配置函数](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADMIN-SET)。若使用 pool+SET LOCAL，必须在**每一次实际写入的同一借用连接与事务内**设置，并覆盖 aput、aput_writes 等；在调用前开另一条连接设置无效。单连接不能走 transaction pooling 代理而仍假定 session GUC 固定。
4. 显式绑定本 run 允许写入的 thread_id。会话可多轮复用一个 LangGraph thread，而每轮 run/attempt 不同，因此不能把 thread_id 永久等同 run_id。子图通常以 namespace 区分，但任何独立创建的子 thread 也要进入可信绑定，不能仅相信客户端传来的 thread/ns。

### Trigger 的核心（伪逻辑，未执行）

为三张 checkpoint 数据表的 INSERT/UPDATE/DELETE 安装 BEFORE ROW guard；对 UPDATE 检查 OLD/NEW thread 不能跨绑定迁移；删除使用 OLD.thread_id。普通 worker 禁止 TRUNCATE/DDL/禁用 trigger。

```text
从 current_setting 读取 run_id、attempt_token；缺失则报错
SELECT attempt_token, lease_until, 可写状态
  FROM execution_runs WHERE run_id = session.run_id FOR SHARE
核对返回 token == session.token、状态允许、DB 时间下 lease 仍有效
核对 NEW/OLD.thread_id 属于该 run 的可信绑定
不匹配则 RAISE EXCEPTION；否则允许本事务的 checkpoint DML
```

**为什么有别于 preflight：** FOR SHARE 持有到事务结束，并与同一 lease 行的 UPDATE 冲突。若旧写先锁住 lease 行，它须提交/回滚后接管 UPDATE 才能提交；若接管先提交，旧写锁定后读到新 token 并失败。在 READ COMMITTED 下，阻塞后的 SELECT FOR SHARE 会锁住并返回更新后的行。[PG 行锁](https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-ROWS)、[READ COMMITTED](https://www.postgresql.org/docs/current/transaction-iso.html#XACT-READ-COMMITTED)。不要改成普通 SELECT，也不要用不会阻止普通非键 UPDATE 的 FOR KEY SHARE。

**准确保证：** 新 token 的接管事务提交之后，旧 token 无法再提交受保护 checkpoint DML。不是“时钟越过 lease_until 的瞬间，全部旧写已消失”：旧写可在到期前获得锁、稍后提交，接管需等待该事务。到期判断应使用实际 DB 时刻（如 clock_timestamp），避免长事务中的 transaction-start 时间；限制写事务时长/锁等待，避免接管无限等待。[PG 时间函数](https://www.postgresql.org/docs/current/functions-datetime.html#FUNCTIONS-DATETIME-CURRENT)。

**维护成本与实现要求：**

- trigger 必须覆盖 blobs/writes/checkpoints，含 UPSERT UPDATE 路径；只检查最新 checkpoint 行不够。把 aput 的多条 SQL 明确包进一个事务可增强调用级原子边界，但这需要适配 `_cursor`/写入实现；该 API 是私有实现细节，升级必须跑兼容测试，不可承诺无维护成本。
- 接管事务只改执行行并先提交，之后再启动新 attempt；避免持有 lease 排他锁时去读取/修改 checkpoint，从而形成与旧 writer 相反的锁序。管理删除/裁剪需要独立受控维护路径；与写者并发时可能死锁，必须限制、超时并测试。
- setup/migrations 由部署角色执行；运行角色不能成为表 owner/superuser。Trigger/GUC 针对可信 worker 的过期 attempt，**不是对恶意或已攻陷 worker 的授权防线**；自定义 GUC 本身不是秘密凭据。用户脚本 sandbox 不得获得这些数据库连接/凭据。
- **权限细节不能略过：** PG 的 SELECT FOR SHARE 也要求被锁表至少一个列的 UPDATE 权限；不能一面不给 writer 任何 lease UPDATE 权限，一面假定普通 invoker trigger 可以成功取锁。一个可实施边界是最小权限 owner 的 SECURITY DEFINER guard：锁 lease/查绑定的能力封在函数内，固定安全 search_path、schema-qualified 对象、不接受可执行 SQL，收紧函数权限，worker 无 SET ROLE 到 owner 的能力。[SELECT 权限](https://www.postgresql.org/docs/current/sql-select.html)、[SECURITY DEFINER 安全要求](https://www.postgresql.org/docs/current/sql-createfunction.html#SQL-CREATEFUNCTION-SECURITY)。这是额外部署/审计成本，不是贴上 trigger 就自动安全。
- saver 发生 fencing 异常后停止该 attempt，不能吞异常继续工具调用或用旧 token 无限重试。checkpoint 历史不应由失败 attempt 随意删除。
- 会话内实例并发、pipeline 错误延迟、SET 与首次写入顺序、连接断开/重连必须实验；重连必须重新建立作用域，缺失配置应 fail closed。独占连接按并发运行占用数据库连接，需预算。

## 选项 B：确认旧执行终止之后恢复，不自动并发接管

可以不改 saver SQL/表 trigger，但相应削弱可用性：lease 到期进入待恢复状态；只有独立监督者确认旧执行单元终止且不能重连后，才启动新 attempt。无法确认（例如 worker 所在主机失联）则保持待人工确认，不能以“心跳超时”替代死亡证明。

取消协程、发 SIGTERM、HTTP 取消响应或关闭一条 DB connection 都不充分：协程可能未响应取消，进程可能存活，pool 可重连。若采用每 attempt 子进程/容器，监督者须确认该执行单元确已退出，并清理其 DB 会话；共享 worker 主进程的“已收到取消”回执不是同等证据。

PG `pg_cancel_backend` 只取消当前 query；`pg_terminate_backend(pid)` 不带正 timeout 时返回 true 仅代表信号发送，带正 timeout 才会等到终止或超时。即便杀掉 DB session，也不能防止仍活着的 Python 重新连接。[PG 信号函数](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADMIN-SIGNAL)。不能据此声称旧 Agent 进程已死亡。

适合本轮先保证恢复安全而接受人工介入的实现；代价是网络分区时不能自动恢复。若既要求无法确认旧执行死亡时自动接管，又要求旧执行绝不再写 checkpoint，单用此选项不成立。

## 两者均不解决外部副作用 exactly-once

数据库 fencing 保护 checkpoint，不会撤销旧 worker 已发出的邮件/外部 API/脚本请求，也不会保证远端在收到取消后停止。恢复前 checkpoint 只能表明“已持久化到哪”，不能证明“未持久化的外部动作没有执行”。对写外部系统的工具仍需其支持的幂等键、受控工具代理的 attempt 核验，以及结果未知时的人工处理；核验后远端调用之间依然有竞态，不能写成严格 exactly-once。对象产物也宜按 attempt 暂存、在带 token 的事务中登记/确认可见性，不能只靠文件名相同防陈旧覆盖。

## 实现前必须通过的并发验收（本轮均未运行）

1. barrier 固定在 preflight 后、DML 前，证明普通 wrapper 会失守，而最终方案能拒绝旧写。
2. 旧事务先持有 lease SHARE 锁；接管阻塞；旧事务提交后接管完成；随后所有旧 token 的三表 DML 均失败。
3. 接管先提交；旧 worker 仍可运行/重连，但不能写；检查子 namespace、独立子 thread、aput_writes 与 UPDATE/DELETE 路径。
4. 触发异常、kill worker、PG 断线、pool/session 混用、pipeline sync、长事务/死锁/超时、租约恰好到期；验证没有半成功状态被报告为已完成。
5. 外部副作用在“远端成功但本地 checkpoint 前”故障；验证系统报告不确定，不自动声称任务未执行或盲目重复。

本轮消除的是“当前 saver 是否天然受 Go 租约约束”的不确定性：**没有**。待定是选择数据库级 fencing、还是先以确认终止换取更保守的恢复。任一并发验收出现旧 token 在接管后仍提交、维护 bypass、或外部重复动作未被识别，都能推翻实现的安全声明。
