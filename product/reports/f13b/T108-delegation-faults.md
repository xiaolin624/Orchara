# T108（GitHub #109）故障注入：子应用并发、满槽与嵌套汇合（FAULT-07/08/13）

状态：**passed（真实栈证据）**。三条故障都打到真实产品协议/持久层：真实编译 manifest、
真实 PostgreSQL 16.4 行与配额账本、真实对象存储字节、真实 `cmd/supervisor exec --once`
与 `cmd/maintenance artifact_finalize` 进程、真实 mTLS internal 协议（claim /
spawnChild / submitWorkerResult / attempt-exit / waiting）。模型仍只有
`ORCHARA_PROFILE=dev-stub`；真实供应商、浏览器 UI、常驻 supervisor 守护进程见 §8 `not_run`。

- 分支 / worktree：`t/t108-delegation-faults` / `E:\Orchara-wt\t108`
- 基线：`integration/f13b` @ `f234e72`；测试代码提交 `1f0bfd5`；结束前 merge
  `integration/f13b` @ `d593c1e`（其中已含 T106、T107）→ 合并提交 `203b2d5`；
  门禁与证据都在合并后的树 `203b2d5` 上重跑。
- 环境：PostgreSQL 16.4 `127.0.0.1:55432`（每个 stack 一个一次性数据库）；Go 1.27.3；
  Python 3.12.2；WSL2 Ubuntu-24.04 + runsc release-20241118.0（**本票不执行任何 python
  节点，不使用沙箱**）；模型 dev-stub。
- 交付：
  - `product/tests/f13b/test_t108_delegation_faults.py`（4 条真实栈用例：FAULT-07、
    FAULT-08、FAULT-13、负边界）
  - `product/tests/support/t108.py`（故障证据记录器 + 双请求并发注入 + 20 父快照/时延模型
    + 嵌套 condition/join 图构造器）
  - `product/reports/f13b/T108-faults.json`（`F13B_WRITE_EVIDENCE=1` 生成，4 条记录）
  - 本文件；原始 tail 在 `E:\Orchara\.scratch\f13b\T108\`
- 未修改 `docs/spec/**`、`contracts/**`、迁移与既有票测试断言；未改产品 Go/Python 代码。

## 1. 结论（先读）

1. **FAULT-07 passed**：一个真实父 Run（`844a747a-7e0b-41ed-a6e4-7192777fefa0`）的同一
   attempt 用 `threading.Barrier(2)` + 两条独立 mTLS 连接**同时**发两个 `spawnChild`
   （同一 `application_version` `1f23be2e-f70c-4897-96aa-6af3dca25dbf`、不同 logical key
   `call_app:left` / `call_app:right`）：两条请求都在对方返回前已发出（同时 in-flight），
   平台创建两个独立 child Run `5f1bc53b-…`（left）与 `b237adb3-…`（right），
   `parent_run_id` 相同、`checkpoint_thread_id` 不同（`87bb590b-…` / `8ca6ed1d-…`，也
   不同于父 `844a747a-…` 的 thread），封存输入分别是 `{"source":"alpha"}` /
   `{"source":"beta"}`（sha256 独立重算一致）。一个 child 的 slot 被真实 claim 持有、
   另一个由真实 supervisor 进程执行，**两个 child attempt 在同一时刻都 live**
   （attempt 行：held `19:57:54.673` 起、executing `19:58:03.085`～`19:58:03.375`；
   采样 19 次读到 `child_slots.used=2`）。held attempt 以 `started=false` 的
   "从未运行" exit 合法 requeue（复用同一 job `b1fc9dfe-…`），再由真实 supervisor
   执行。父 Run 被真实 supervisor 恢复后重放两次调用，在一个 join 汇合，输出
   `results.left.echo=alpha` / `results.right.echo=beta`（无串扰），join succeeded
   事件 seq=17（artifact `4ab861d1-…`）、恰好 1 次，父事件 seq 1..22 连续；child 池
   used 0、per-parent used 0、execution 池 used 0。
2. **FAULT-08 passed**：20 个真实父 Run 各自 claim→spawnChild→`waiting_children`；
   Python `execution_slots` 压到 1（被一个真实 claimed 顶层 Run 占住），独立
   `child_slots` 压到 2。子任务仍推进：22 个静默采样点里 20 个 child 全部
   `succeeded`（每个 1 个 attempt、1 个封存输出），2 个 child slot 被真实占满时
   `active=2 / queued=18 / waiting_children=20`，第 3 个 claim 返回 204；队列越长时
   `queued` 单调下降、`completed` 单调上升、`active+queued+completed≡20`、任何时刻
   `active≤2`、**queued child 的 attempt 数恒为 0**（排队从不计为执行），child 池
   used 恒等于 active。P50/P95 完成时延 8.60s / 12.73s，P50/P95 排队时延
   8.43s / 12.59s（含刻意关池的 setup 段），错误 0。释放外部顶层 slot 后队列真实
   排出（一个父 resume 被 claim 并成功），无死锁。
3. **FAULT-13 passed**：真实嵌套 condition/join 图（`choose` 内嵌 `choose2`，分支节点
   是真实 `call_app` 委派），`mode=deep` + 0.00000001 USD 额度注入：
   - 成功子例：`join2` 与 `join` **各恰好触发 1 次**（各 1 个 data 制品、各 1 个
     succeeded 事件），envelope 分别是
     `{"deep":succeeded(echo=deep),"shallow":skipped}` 与
     `{"join2":succeeded(value=内层 envelope),"direct":skipped}`；封存 manifest 的
     join 配置（fork/sources）对照 envelope 计数：每个 join 2 个 source、1 activated、
     1 skipped；`deep`/`end`/两个 join 终态 succeeded，`shallow`/`direct` 只出现一次
     `skipped`；父 Run `95d67492-…` succeeded，事件 seq 1..41 连续。
   - 必需失败子例：必需分支 child `91b8b853-…` 继承父额度
     `0.00000001 USD`，真实 supervisor 执行到模型预留被真实拒绝
     （`budget_exhausted`），父 Run `b7ade333-…` **显式 failed / `budget_exhausted`**
     （`finished_at` 已写，不是永久等待）；`join2` 与 `join` 均 **0 次 data、0 个
     succeeded 事件**、节点状态为 `skipped`，事件 seq 1..35 连续；没有 `join_required_failed`
     这类模糊码，失败沿真实 `budget_exhausted` 传播。

## 2. 逐票面要求（issue #109）

| 票面 | 结论 | 证据 |
|---|---|---|
| FAULT-07：同父并发调用同子应用两次 → 独立 Run/thread，无状态串扰 | **passed** | 2 个并发 spawn（202）× 2 个独立 child Run/thread；封存输入 alpha/beta 与输出 alpha/beta 一一对应；父 join envelope 正确；无重复 child |
| FAULT-08：20 父任务均等待子任务 → 子槽仍能推进，无池饥饿死锁 | **passed** | 20 真实父 waiting；child 池 2 槽被占满时 active=2/queued=18；20 child 全 succeeded、错误 0；P50/P95 见 §4；queued 不计执行 |
| FAULT-13：分支嵌套、skipped、必需失败 → join 一次触发或明确失败，不永久等待 | **passed** | 成功子例 join 各 1 次（data+event 唯一）；必需失败子例 join 0 次、Run failed `budget_exhausted`、finished_at 已写 |
| 负边界：子槽满 → 拒绝/排队（稳定码）而非静默丢弃 | **passed** | capacity 0 时 spawn 202 + child `queued` + job `ready` + attempt 0；恢复 1 槽后 child 真实 claim/succeed，父 succeeded |
| 负边界：父预算耗尽 → child 不启动 | **passed** | 父额度 0 时 spawn `429 budget_exhausted`；0 条 child Run、0 条 receipt、0 条 ledger 行 |

## 3. FAULT-07：同一父 Run 并发调用同一子应用版本两次

**注入点。** T029 真实栈（`tests/f13b` 的 `t108_stack`：真实 `cmd/admin` 迁移、编译
`cmd/api`、真实 mTLS internal listener、真实对象存储、真实 `cmd/supervisor`），父定义
`start → {left,right}(call_app, 同一 child_version) → join → end`。测试先真实 claim 父
Run 的 attempt，然后在 `threading.Barrier(2)` 后由两条独立 mTLS 连接**同时** POST
`/internal/v1/runs/{parent}/children`（契约 `spawnChild`），logical key
`call_app:left` / `call_app:right`，输入 `{"source":"alpha"}` / `{"source":"beta"}`。

**输入 / seed / hash。**
- 父定义 canonical JSON sha256 `35a092bb104ca94699abbf92ae2cad4cbb761442715a7ca0d2f78bfc92faa9a7`；
  child version `1f23be2e-f70c-4897-96aa-6af3dca25dbf`，child manifest sha256
  `5e04d448c236af80af7f26e68497b0896c4051d9c37d7e7ac4465bdd0e8200fd`（`seed`）。
- 两条封存输入 artifact 的 sha256 与 `platform.run_children.input_sha256` 独立重算一致：
  left `39ebab756a244d517bc3aaa99b8d252a0963e60575d9b572d7eab743effb185e`（alpha）、
  right `97a1c86dafe395b74599e2715791e9938bb7c0ae4f27e14f55de5f7b81a63d30`（beta）。
- 并发证据：`max(started_at)=30976.652` < `min(finished_at)=30976.756`（两条 HTTP 请求
  同时在途，单位 perf_counter 秒）。

**独立真值。** `platform.runs` / `platform.run_children` / `platform.run_attempts` /
`platform.quota_allocations` 原始行；对象目录里封存输入的 SHA-256 重算；两个真实
supervisor 进程的 JSON exec report；父 Run 的 `platform.run_events`（seq 连续）与
sealed final output 字节。

**预期 vs 实际。**

| 步骤 | 预期 | 实际 |
|---|---|---|
| 并发 spawn ×2 | 202 + 两个不同 child Run | 202/202，child `5f1bc53b-…` / `b237adb3-…` |
| 独立 thread/attempt | thread 不同且都 ≠ 父 thread | `87bb590b-…` / `8ca6ed1d-…` ≠ 父 `844a747a-…`；attempt 行独立 |
| 封存输入 | 每 child 只有自己的映射 | left=`{"source":"alpha"}`、right=`{"source":"beta"}`，sha256 重算一致 |
| 同时占用 child 槽 | 两个 child attempt 同时 live，used=2 | held attempt `19:57:54.673` 起；executing `19:58:03.085..19:58:03.375`；19 次采样读到 used=2；held 两条 allocation（global+parent）live |
| 执行 | 两个 child 都由真实 supervisor 执行 | held child 先以 `started=false` 合法 requeue（exit evidence `started:false`），再由真实 supervisor 执行；executing child 一次执行 |
| 父恢复 | 重放两次调用、无重复 child、join 一次 | 父 `succeeded`，child 数仍 2，join data=1/event seq=17，事件 1..22 连续 |
| 输出无串扰 | left=alpha、right=beta | `{"results":{"left":{...alpha},"right":{...beta}}}` |
| 槽位归零 | child/per-parent/execution 全 0 | child 0/8、`parent:844a747a…` 0/3、execution 0/2 |

**说明。** 为了让"两个 child attempt 同时 live"不依赖进程启动抖动，测试用真实 claim
持有 left child 的 lease（真实 attempt、真实两条 allocation），在它 live 期间由真实
supervisor 进程执行 right child，然后把这个**从未运行**的 attempt 用平台文档化的
`started=false` release 路径（running→queued、job 重新 ready，exit evidence 记录
`started:false`）交回队列，再由真实 supervisor 真正执行。两个 child 的最终输出都来自
真实 supervisor 执行；同时刻双槽 liveness 由 attempt 行（时间戳 + `exited_at`）与
allocation 行证明，而不是靠采样撞运气。

## 4. FAULT-08：20 个等待父任务 + 1 个 Python 槽 + 2 个 child 槽

**注入点。** 20 个真实 debug 父 Run（各自 claim→`spawnChild`→`waiting_children`→
confirm exit，setup 期间 child 池 capacity=0 以保证显式排队）；一个真实 claimed 顶层
Run 占住唯一的 `global/execution_slots`（capacity 1）；独立 child 池
`global/child_slots` 从 8 压到 2（`platform.quota_counters` 部署旋钮，非产品规则）。

**规模与等价性。** 父任务就是 **20 个真实 Run**（未缩小）；被压到 2 的是 child 池和
1 的 Python 池，所以同时真正执行的 child attempt 最多 2 个——这正是"满槽 + 小池"的注入
条件，不是用模型并发代替 20 个父。20 个父的 resume job 都真实存在且 ready，只是被外部
顶层 Run 占用的 Python 槽挡住（T081 文档化的独立池行为），§4 末尾用真实 cancel/exit
释放该槽并让队列排出，证明不是死锁。

**独立真值。** `platform.runs`（20 父 + 20 child）、`platform.run_attempts`、
`platform.quota_allocations`/`quota_counters`、`platform.run_waitings`、
`platform.jobs`（20 个 resume job），以及每个 child 的 1 个真实 supervisor exec report。

**时间线（22 个采样点，全部来自 SQL 行，见 JSON `observed.samples`）：**

| 采样 | active | queued | completed | parents(waiting_children / queued) | child_pool used | Python 池 used |
|---|---|---|---|---|---|---|
| after-setup | 0 | 20 | 0 | 20 / 0 | 0 | 1 |
| two-children-active | **2** | 18 | 0 | 20 / 0 | 2 | 1 |
| after-first-completion | 1 | 18 | 1 | 19 / 1 | 1 | 1 |
| slot-backfilled | **2** | 17 | 1 | 19 / 1 | 2 | 1 |
| three-completed | 0 | 17 | 3 | 17 / 3 | 0 | 1 |
| supervisor-0…15 | 0 | 16…1 | 4…19 | … / 4…19 | 0 | 1 |
| supervisor-16 | 0 | 0 | **20** | 0 / 20 | 0 | 1 |

每个采样点断言：`active+queued+completed+failed≡20`、`active≤2`、
`queued_with_attempts=[]`（排队 child 没有任何 attempt）、每个 active child 恰 1 个 live
attempt 且恰 2 条 live child allocation、`live_child_allocations=2×active`、
`child_pool.used=active`。采样期间第 3 个 claim 返回 `204`（池满），没有"超卖"。

**时延（PostgreSQL `percentile_cont`，20/20 完成）：**
完成（created_at→finished_at）P50 **8.5988s** / P95 **12.7279s**；
排队（created_at→首个 attempt started_at）P50 **8.4339s** / P95 **12.5854s**
（含刻意关池的 setup 段，故绝对值偏大；比较的是同一注入下的分布）。错误数 **0**
（20 child 全 succeeded、无 failed）。

**收敛与无死锁。** 20 个 child 全部 succeeded 后，20 个父各恰好 1 条 `children/succeeded`
decision（`child_run_id` 对应各自的 child）和 1 个 `resume` job（state `ready`），父
状态 `queued`；此时 `claim` 返回 204（被外部顶层槽挡住，显式阻塞而非丢失）。随后对
saturating Run 走真实 `cancel` + attempt-exit：Python 槽 used 1→0，下一次 claim 取到一个
父 resume 并由真实 supervisor 成功执行（重放同一 child，child 数仍 1，输出是该父自己的
echo），证明队列可排出。

## 5. FAULT-13：嵌套分支、skipped 与必需失败

**注入点。** 真实 debug Run 的嵌套 condition/join 图：
`start → choose --yes--> choose2 --yes--> deep(call_app) / --no--> shallow(call_app) -->
join2 --no--> direct(call_app) --> join --> end`。`choose2/join2` 完整嵌套在
`choose/join` 的选中区域内；三个分支节点都是真实 `call_app` 委派（无需沙箱）。
子例 A `mode=deep`、child 为真实 `start→end` echo 应用；子例 B 同一图、child 是真实
`start→agent→end` 应用，父 Run 额度被设为 `0.00000001 USD`。

**独立真值。** 封存进 execution snapshot 的 manifest（join 的 `fork_id` /
`required_sources` / `optional_sources`）、join data 制品字节、`platform.run_node_outputs`、
`platform.run_events`（seq 连续）、父/child 的 `platform.runs` 行、child 的
`runs.budget_limit`（继承父额度）。

**子例 A（join 恰一次）：**

| 项 | 值 |
|---|---|
| 父 Run | `95d67492-0cab-40f8-801e-811f53bf2cc3`，succeeded，2 个 attempt（spawn + 恢复） |
| deep child | `af2f7fed-ad4e-4e95-a12b-e1d88e132ff9`，succeeded，1 attempt；输出 `{"echo":"deep"}` |
| `join2` | data=1（artifact `d8a6ecde-…`），succeeded 事件 1 个（seq 31）；envelope `{"results":{"deep":succeeded,"shallow":skipped}}` |
| `join` | data=1（artifact `ae650bc6-…`），succeeded 事件 1 个（seq 34）；envelope 内嵌 `join2` 的完整 envelope，`direct`=skipped |
| join 输入计数 | `join2`: sources=[deep,shallow]、activated=[deep]、skipped=[shallow]；`join`: sources=[join2,direct]、activated=[join2]、skipped=[direct] |
| skipped 事实 | `shallow`、`direct` 各只出现一次 `skipped`（等待交接前不发布，恢复后发布） |
| 事件 | 41 条，seq 1..41 连续 |

**子例 B（必需失败 → 明确失败、不永久等待）：**

| 项 | 值 |
|---|---|
| 父 Run | `b7ade333-524b-4d31-81f0-404b49e894cc`，**failed / `budget_exhausted`**，finished_at 已写 |
| 必需 child | `91b8b853-5b1c-4751-a641-971bffd84d6c`，failed `budget_exhausted`，`runs.budget_limit=0.00000001 USD`（与父一致） |
| join | `join2`、`join` 均 data=0、succeeded 事件 0；节点状态 `skipped` |
| 节点状态 | `deep` = pending,running,pending,running,**failed**；`shallow`/`direct`/`join2`/`join`/`end` = `skipped` |
| 事件 | 35 条，seq 1..35 连续 |

**说明。** 这里没有 `join_required_failed` 这种"发明码"：必需分支失败按产品语义
fail-fast，父 Run 携带 child 的真实稳定码 `budget_exhausted` 终止；join 从未触发，
也没有任何节点停在等待态。等待交接（waiting_children）只在子例 A/B 的中间态出现，
并由真实生命周期 hook 解析出唯一 decision（success/failed 各一次）。

## 6. 负边界（`FAULT-07/08-boundaries`）

| 注入 | 预期 | 实际 |
|---|---|---|
| child 池 capacity=0，父 Run 真实执行到 `waiting_children` | spawn 被接受并**排队**，不静默丢弃 | spawn 202；child `aeeb38e3-…` state=`queued`，`run_children` receipt 存在，job `ready`，attempt=0，child 池 used=0，per-parent 计数器尚未创建 |
| 恢复 1 个 child 槽 | 排队 child 真实 claim 并完成 | 真实 supervisor 执行 → succeeded；hook decision succeeded；父 resume 真实执行 → succeeded，输出 `{"echo":"queued"}` |
| 父额度 0 时 spawn | 稳定拒绝，child 不启动 | `429`，`error.code=budget_exhausted`；0 child Run / 0 receipt / 0 ledger 行 |

## 7. 命令与门禁（原始 tail）

全部从 `E:\Orchara-wt\t108\product` 运行；最终树 `203b2d5`（已 merge `integration/f13b`
@ `d593c1e`）。原始日志在 `E:\Orchara\.scratch\f13b\T108\`。

```text
$ python -m pytest tests/f13b/test_t108_delegation_faults.py -q
....                                                                     [100%]
4 passed in 72.32s (0:01:12)

$ python -m pytest tests/f13b -q            # T102 36 + T103 4 + T104 48 + T105 91 + T106 3 + T107 4 + T108 4
........................................................................ [ 37%]
........................................................................ [ 75%]
..............................................                           [100%]
190 passed in 253.42s (0:04:13)

$ python -m pytest tests/f12 -q
........................................................................ [ 16%]
........................................................................ [ 32%]
........................................................................ [ 48%]
........................................................................ [ 64%]
........................................................................ [ 80%]
........................................................................ [ 96%]
...............                                                          [100%]
447 passed in 350.01s (0:05:50)

$ go build ./...      -> exit 0
$ go vet ./...        -> exit 0
$ go test ./...       -> exit 0（全部 ok / 无 FAIL）
$ gofmt -l internal cmd tools -> 空输出（exit 0）
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

证据生成（同一最终树，`F13B_WRITE_EVIDENCE=1` 才写 `reports/f13b/T108-faults.json`）：

```text
$ $env:F13B_WRITE_EVIDENCE='1'; python -m pytest tests/f13b/test_t108_delegation_faults.py -q
....                                                                     [100%]
4 passed in 87.79s (0:01:27)

# 证据 JSON：4 条记录全部 passed（FAULT-07 34 断言 / FAULT-08 91 / FAULT-13 26 /
# 负边界 8），product_commit=203b2d5…
```

普通 pytest 运行不会重写已提交的证据 JSON（`support/t108.py` 的 `persist` 在
`F13B_WRITE_EVIDENCE!=1` 时直接返回；T101/T106 同一约定）。

## 8. 覆盖边界与 `not_run`

- **真实模型/供应商**：`not_run`。全部模型调用（仅 FAULT-13 子例 B 的 agent child 用
  dev-stub 触发真实 budget reserve 拒绝）走 dev-stub，无供应商凭据/端点；不能据此推断
  真实供应商的质量、延迟或费用。
- **真实浏览器 DOM**：`not_run`（本票不涉及页面）。
- **常驻 supervisor 守护进程**：`not_run`；只使用生产的 `exec --once` 形态。20 父/2 child
  槽是真实 Run/attempt/账本，但 child 并发执行被**故意**压到 2（注入条件），不是 20 个
  child 同时执行。
- **20 父的 resume 并发**：20 个父的 resume job 都 ready，但 Python 池被一个外部 Run 占
  1 槽；本票只排出并执行 **1 个**父 resume 证明可解开，其余 19 个仍 queued（同一 T081
  文档化独立池行为），未逐个 drain。
- **FAULT-07 的同时执行**：held child 的 attempt 被真实 claim 持有但**没有**执行图；
  它先以 `started=false` 合法 release/requeue，再由真实 supervisor 执行；"同时"指的是
  两个 child attempt/两条 child slot 同时 live（由 attempt 行时间戳与 allocation 行证明），
  不是两个图同时在 CPU 上跑。
- **与 T106 `fault_support.py` 的关系**：开始实现时 `integration/f13b` 尚无该文件
  （brief 允许"新建同样结构"）；T108 用 `tests/support/t108.py` 记录器（字段与
  T106 的 `Fault` 相同：injection_point/input/seed/expected/observed/independent_truth/
  assertions/commands/not_run，同样的 `F13B_WRITE_EVIDENCE` 约定与
  `tools/evidence_redaction` 校验），未改动 T106 已提交的证据写入器；T107 也采用
  自己的 `tests/support/t107.py` 记录器，仓库模式一致。
- **测试用部署旋钮**：`ORCHARA_EXECUTION_LEASE_TTL=300s` 是 T068/T084/T101 同款测试余量
  （默认 30s），因为用例在 claim child 后要采样/停车/多次协议调用；不改变产品规则。
- 未修改 `docs/spec/**`、`contracts/**`、迁移、既有票测试断言。
