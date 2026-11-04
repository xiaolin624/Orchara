# F13A T065 验收：查询异步评测进展并区分业务与设施失败

- 任务：F13A / GitHub Issue #66（票 `T065`，规划 ID F13A.05），分支
  `t/t065-async-progress`（worktree `E:\orchara-worktrees\t065`）；基线
  `feat/f06-f04a-models-and-workflow` @ `7d0f442`；已合并
  `integration/f13a-f15a-f09-f10`（合并提交
  `df555c5deea85b598dbbe089b4e3f683e0233727`，合并前 integration tip `ab8a800`，
  即 T060 之后）。
- 状态：**`passed`**（范围 = 非契约进展读 + 由 runner 逐例持久化的计数 + 业务/设施
  失败分类 + 真实双池拥塞验证 + 可刷新评测页。真实 PostgreSQL 16.4、真实编译二进制、
  真实 HTTP/mTLS、真实封存对象字节、真实 `cmd/maintenance run` 双池（work=1 /
  system_io=1）；页面纯逻辑由真实 Node 执行。无 mock、无跳过、无静态通过。）
- 边界（如实）：T056（#57）的逐例用户读取/逐例页面未合并到本分支，本票页面只交付
  **进展与报告**，不重复 T056 的逐例表。本构建没有评测级取消端点，本票**不伪造**它；
  cancelled 分类由真实内部 result 协议（claim 后报告 `cancelled`）驱动。真实
  supervisor/沙箱不在本票路径（案例由真实 cancel、无执行器超时、目标拒绝决定）。
  sales/tool 案例的真实 `succeeded` 终态仍受 #57 的 `task_input` 绑定边界限制。
- 合并重编号：迁移原 `0038_evaluation_progress.sql`、ADR 原 `0016`；integration 的
  T060/F10 已占 `0038_pdf_admission.sql` 与 `0016-f10-pdf-ingest.md`，故合并结果中
  本票为 **`0039_evaluation_progress.sql`** 与 **`docs/adr/0017-evaluation-progress.md`**。
  本报告与 PROGRESS 使用最终编号；迁移文件头记录重编号。

## 0. 工作记录（简短）

1. 按顺序读 `BRIEF.md`、`briefs/T065.md`、票面 #66、T052/T062 报告、迁移 0035、
   ADR 0011/0014、`internal/evaluation/`（dataset/assertions/report/collect）、
   `internal/background/{evaluation,complete,scope}.go`、
   `internal/api/{evaluation_submit,evaluation_runs,evaluations,evaluation_attribution}.go`、
   `internal/execution/evaluation_runs.go`、`cmd/maintenance/{run,scope}.go`、
   `tests/f13a/` 与 `tests/support/t052.py`/`t062.py`，以及 T056 分支上的评测页
   （仅确认路由不冲突，未把本票建在未合并分支上）。
2. 确认接缝：`progress_total/progress_done`（0035）只由 case run 观察状态重算，无
   runner 判定列、无 failed 计数，且报告只在结束时存在 → 必须新增持久化事实。
3. TDD 垂直切片（每片先红后绿，红证据在 §0.1）：
   - 进展读（路由不存在 → 404）→ 读模型 + handler + 守卫终态计数；
   - runner 逐例持久化（blocked 案例没有任何 case 行 → 计数不前进）→ 迁移 + 上报桥；
   - 业务失败 / `not_run` 报告状态 / 基础设施失败 / cancelled 分类；
   - work=1 双池拥塞（真实 finalize 独立完成、报告仍封存）；
   - 评测页与 Node 纯逻辑；迁移按**名字**断言 + replay no-op。
4. 跑门禁、写本报告/PROGRESS/ADR，按 feat/test/docs 提交；合并 integration 后
   重编号并重跑目标套件两次；未 push、未动 GitHub issue。

### 0.1 红证据（真实执行）

进展读在实现前：真实 HTTP 返回 `404 page not found`，测试在
`assert response.status == 200` 处失败。

runner 持久化在实现前：blocked 案例（`published_version`，runner 决策为
`blocked/unsupported_target`，从不创建 run）完成后，进度桥尚不存在时案例行集合为空：

```text
>       assert len(rows) == 1, rows
E       AssertionError: []
E       assert 0 == 1
FAILED tests/f13a/test_t065_progress.py::test_the_runner_persists_each_case_outcome_while_it_collects
```

评测页在实现前：`GET /evaluations/{uuid}` 返回 `404 page not found`。

## 1. 交付内容

### 1.1 持久化（迁移 `0039_evaluation_progress.sql`，非契约）

`platform.evaluation_case_runs` 增加 `verdict text`（闭集
`passed|failed|blocked|not_run`，0001–0038 未改）。`state` 保持原义（观察到的 run
状态，或没有 run 时的 `blocked`），因此“已取消的 run + failed 判定”能同时保留。

`platform.evaluation_jobs` 增加 `progress_failed integer NOT NULL DEFAULT 0`
（`CHECK progress_failed >= 0 AND progress_failed <= progress_done`）。

`progress_done` 的语义统一为**已判定**：`verdict IS NOT NULL` 或 case run 到达
rest 状态（succeeded/failed/cancelled/recovery_pending/needs_reconciliation）。
该表达式只定义一次（`internal/execution/evaluation_runs.go` 的
`evaluationProgressDoneSQL`），run 创建 hook、facts hook 和 runner 上报共用，
重试不会让计数倒退。迁移以 `IF NOT EXISTS`/`DROP CONSTRAINT IF EXISTS` 保证
repeat migrate no-op；权限边界与 0007–0038 相同（`PUBLIC`/`orchara_worker` 零权限）。

### 1.2 runner 逐例上报桥（job-scoped，非契约）

`POST /internal/v1/background/jobs/{job_id}/evaluation-progress`
（`{attempt_token, case_id, ordinal, verdict, reason_code}`）：

- 与 T052 run 桥同一认证/授权：维护身份 mTLS + live attempt token，服务端
  `loadEvaluationScope` 重证“应用仍属评测 owner 且未归档、snapshot owner 一致”；
- handler 只提交**自己已经做出的判定**；run/owner/snapshot 身份不经 handler 传递；
- `(evaluation_id, case_id)` 幂等 upsert（ordinal 来自数据集顺序），同一事务重算
  `progress_done/progress_failed`；
- 无 run 的案例（blocked）由插入分支落 `state='blocked'`，判定在 `verdict`；
- collector 在**每例判定后**调用（`RunClient.RecordCaseOutcome`），上报失败不中止
  评测：密封报告仍是真值，完成守卫写终态计数（§1.4）。
  单测 `TestCollectReportsEveryCaseDecisionWithItsOrdinal` 断言每例上报的
  case_id/ordinal/verdict/reason。

### 1.3 进展读（非契约，ADR 0017）

`GET /api/v1/evaluations/{evaluation_id}/progress`：

- **授权**与契约评测读一致：owner-or-admin；其他人一律 `404 resource_not_found`
  （与不存在同码同文，不能枚举）；API key 先过 `application:read` scope
  （write-only key → `403 insufficient_scope`），ACL 仍是内容权威；
- 字段固定（测试断言集合）：`evaluation_id/state/report_status/report_id/
  total_cases/completed_cases/failed_cases/error_code/failure_class/created_at/
  finished_at`；
- `report_id` 在报告存在前为 `null`；`report_status` 同样为 `null`；
- **绝不从 `state` 编造百分比/进度条**：queued 状态诚实显示 `0/N`；
- `failure_class` 规则（纯函数 `evaluationFailureClass`）：

| 值 | 条件 | 含义 |
|---|---|---|
| `business` | 有报告且 status 为 `failed`/`not_run` | 平台完成评测，报告是对**应用**的判定；job/evaluation 均 `succeeded` |
| `infrastructure` | 无报告且 evaluation `failed` | job `failed`，平台没有产出任何判定；`report_id` 为空 |
| `null` | queued/running、cancelled、或报告 `passed` | — |

这是“是否存在报告”的轴，**不是** `background_jobs.failure_class`（重试分类）：
确定性的平台拒绝（如 `source_deleted`）与瞬时故障（`transient_storage_io`）都
没有报告，从创作者视角都是“任务失败、没有判定”。job 自己的分类仍在 job 契约读上
可见；ADR 0017 明确记录两者语义不同，不混用。

### 1.4 完成守卫写入终态计数

`guardEvaluationSuccess` 在**写 report 行的同一事务**里写
`progress_total/progress_done = GREATEST(current, report case_count)`、
`progress_failed = 报告 failed 案例数`。因此即使 live 上报被重试中断，终态计数
仍来自密封报告本身（报告仍是唯一真值，守卫仍是唯一 report 写入者）。

### 1.5 评测页

`GET /evaluations/{evaluation_id}` + `GET /assets/evaluations.js`
（`apps/web/src/evaluations.html`）：

- evaluation id 在 URL 路径中 → 刷新就是同一可分享视图；queued/running 每 2s 轮询
  `progress`，终态停止，不轮询纯静态数据；
- 页面用明文解释两件事的区别：**报告 failed（业务判定，任务 succeeded）** vs
  **任务 failed（设施失败，没有报告，report_id 为空）**；并把 `not_run` 明确写成
  “报告状态，不是任务状态”；
- `report_id` 空态单独渲染；报告区只显示四个契约字段，重读后比较快照，
  “重新读取报告（验证不可变）”按钮给出与上次一致的结论；
- 页面逻辑为纯函数，由 Node 在 `tests/f13a` 中执行（`describeProgress` /
  `describeFailureClass` / `reportSnapshotText` / `reportHasChanged` 等），并断言
  任何文案都不含 `%`/进度条；
- `/apps/{id}/debug` 增加评测 id 入口（创建者在应用页即可打开评测进展页），
  debug 页面内联脚本由 Node `--check` 解析。

## 2. 验收标准逐条

| AC | 结论 | 证据 |
|---|---|---|
| 1. 报告 `failed` 可对应 job `succeeded`（并显示为业务结果） | **proven** | `test_t065_progress.py::test_a_failed_report_is_a_business_result_of_a_succeeded_job`：真实 cancel 终态 → 报告 `failed`；契约 `GET /background-jobs/{id}` = `succeeded`、`GET /evaluations/{id}` = `succeeded` + report_id；progress `failure_class=business`、`failed_cases=1`；Go 单测 `evaluationFailureClass` 由同一测试覆盖 |
| 2. 报告未生成时 `report_id` 为空 | **proven** | `test_an_infrastructure_failure_leaves_report_id_empty`：数据集封存对象真删 → runner 重试 `transient_storage_io` 至 `MaxAttempts=3` → job/evaluation `failed`、`report_count==0`、契约读与 progress 的 `report_id` 全为 `null`、`failure_class=infrastructure`；`test_a_queued_evaluation_reports_only_honest_zero_counters` 证明报告存在前同样为空；`test_the_runner_persists...` 的 blocked 案例报告为 `not_run` 而 `report_id` 非空（报告状态 vs 任务状态） |
| 3. 后台池拥塞不阻塞封存 | **proven** | `test_work_pool_congestion_does_not_block_report_sealing`：真实 `cmd/maintenance run` `--work-slots 1 --system-io-slots 1`；第一个评测 handler 占满唯一 work slot，第二个评测仍 `queued/ready`；此时真实 `artifact_finalize` job 在 system_io 完成（artifact ready），且评测 job 仍 `leased`；随后该评测 `succeeded`，其自身报告 artifact `ready`、progress `completed_cases=1`；维护日志含 `run: pools=...` 且成功 job 的 `(kind,pool)` 同时出现 `(application_evaluate,work)` 与 `(artifact_finalize,system_io)` |
| 4. 通过真实端到端入口实际演示；失败、授权与重试/恢复边界有断言，不以单层 mock 成功代替 | **proven** | 全部用例走真实 REST 提交 → 真实 PG → 真实 background job → 真实 mTLS（内部 result/claim）→ 真实 maintenance 双池 → 真实封存对象字节 → 真实完成守卫；失败边界（报告 failed、job failed 无报告、cancelled）、授权边界（B 404 不可枚举、未认证 401、畸形 id 404、admin 200、write-only key 403 insufficient_scope、read-scoped 外部 key 404）、重试边界（infra 3 次 attempt 后终态、无报告）；页面由真实 HTTP 提供并由 Node 执行逻辑 |
| 5. 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件 blocked，未运行 not_run | **proven** | §3 贴原始门禁输出与版本；封存报告字节/sha256 的断言在用例内（前后两次读取一致、记录 hash 与字节一致）；本票不依赖任何 stub（无模型/judge 调用）；未执行项列于 §4，未伪造通过 |
| 附加（brief）：进展只返回诚实可导出的计数，不从 state 编百分比 | **proven** | queued 用例断言 `0/1` 与 `report_id=null`；页面 Node 断言所有文案无 `%`；progress 只读持久列 |
| 附加（brief）：runner 在每例完成时持久化 | **proven** | `test_the_runner_persists_each_case_outcome_while_it_collects`：blocked 案例（无 run）的 `evaluation_case_runs` 行 `verdict=blocked`、`ordinal=0`、`run_id=NULL`，`progress_done=1`；Go 单测覆盖 collector 的上报 seam |
| 附加（brief）：cancelled 是独立分类 | **proven** | `test_a_cancelled_evaluation_is_neither_business_nor_infrastructure`：真实内部 result 协议 `state=cancelled` → evaluation/job `cancelled`、无报告、progress `failure_class=null` |
| 附加（brief）：报告字节/sha256 创建后不变，重开一致 | **proven** | 业务失败用例两次读取封存字节与 sha256 相同、与 `artifacts.sha256` 相同、两次 `GET /evaluation-reports/{id}` 响应相同、report 行不变；页面纯函数对相同 payload 的两个快照相等、改 status 则不等 |

## 3. 门禁命令与原始输出

环境（本次实际执行）：Windows PowerShell；PostgreSQL 16.4 用户态集群
`127.0.0.1:55432`（`python tools/pg_dev.py status` = running）；Go
`go1.27.3 windows/amd64`；Python 3.12.2；Node `v24.13.0`。本票不依赖
supervisor/沙箱（案例决策来自真实 cancel / 无执行器超时 / 目标拒绝），故未启动
`cmd/supervisor`。

```text
$ python -m pytest tests/f13a -q            # 合并结果，第一次
.................................                                        [100%]
33 passed in 526.04s (0:08:46)

$ python -m pytest tests/f13a -q            # 背靠背第二次，无代码改动
.................................                                        [100%]
33 passed in 682.46s (0:11:22)

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 8.16s

$ python -m pytest tests/f00b/test_t026_binding.py tests/f00b/test_t026_failures.py \
    tests/f00b/test_t026_pool.py tests/f00b/test_t026_authz.py \
    tests/f00b/test_t026_migration.py -q
...........................                                              [100%]
27 passed in 387.59s (0:06:27)

$ go build ./...        -> exit 0, no output
$ go vet ./...          -> exit 0, no output
$ go test ./...         -> exit 0；所有包 ok（cmd/* 无测试文件），无 FAIL
$ gofmt -l .            -> no output

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

两次连跑没有 flaky；本票唯一的长等待是拥塞用例里 25s 的**有界案例等待**（无执行器时
run 保持 queued 的负向控制，不是与真实完成竞速），其余等待都是对数据库条件的轮询
（`wait_until`/`wait_for_evaluation`），不依赖 <5s 窗口。

### 3.1 本次交付关键文件的 sha256（本地实际文件）

```text
db/migrations/0039_evaluation_progress.sql   b7661ee7b865c32a31fab44c556faf64df7748d4a815c701b63e60280d134371
apps/web/src/evaluations.html                190193712546fd8ec092b290730669776607a552294d2ced6499cc5f3c5b3377
apps/web/src/assets/evaluations.js           8b45f9a8ac1abfb9272410fd684904b29ee000b51f87460dcd573b3e3bc14779
internal/execution/evaluation_progress.go    f22e7f149da3ea6d153b7b5f8ca7313fcb7d14eee034f7fda977a085c7157247
internal/background/evaluation_progress.go   81d27e57d30c119ca97ad2093a298f9319700d9a5493a4404a951ecce7f0f78b
internal/api/evaluation_progress.go          d46983e2237a3f1830ca91eeb1c811190154cdf61db534c0a4b59bdc153c3e0b
internal/background/evaluation.go            b53fc6673088a38f141a520e35b33c9a6c6df659b2b71283be2c1cb53a05667d
internal/evaluation/collect.go               57650699ba37a524ad870c8550f69c87e61d7991ed34232bb90f8789d52463a0
```

封存对象（报告 artifact）的 sha256 不在此表：它由每次真实评测动态生成，测试在用例内
比较“创建时读取”与“重读”两次的字节哈希、并与 `platform.artifacts.sha256` 复核，
见 §2 的附加行。

## 4. `not_run` / 未覆盖清单

- **浏览器 DOM**：`not_run`（与 F01/T023/T029/F10 页面票同一边界：页面纯逻辑由
  Node 执行；真实浏览器/截图未运行）。
- **T056 逐例用户读取与逐例表**：`not_run`（分支未合并；本票页面只显示进展与报告，
  不重复实现 #57 端点）。
- **评测级取消端点**：不存在于本构建，故**不实现也不伪造**；cancelled 分类由真实
  内部 result 协议证明。`evaluation_jobs.cancel_requested_at` 列仍由 T062 归属面
  显示，其转移不在本票。
- **真实 supervisor / runsc 沙箱**：不在本票路径（`not_run`）；sales/tool 案例的真实
  `succeeded` 终态仍 blocked 于 #57（`task_input` 绑定）。
- **真实模型/judge**：不适用（评测不调用模型；T052 已记录 quality 仅信息性）。
- **全量 `pytest tests`**：`not_run`（BRIEF §3 明确禁止；约 67 分钟）。已跑目标套件
  两遍 + tooling + T026 邻接回归 + 全量 Go 门禁。
- **负载/压测生成器**：`not_run`（BRIEF 禁止无界负载）。

## 5. 已知限制与契约债务

1. **非契约面**：`GET /evaluations/{id}/progress`、job-scoped
   `evaluation-progress` 桥、`evaluation_case_runs.verdict` 与
   `evaluation_jobs.progress_failed` 都是文档化扩展（ADR 0017 + 迁移头 + 本报告），
   公开契约未改，`sync_spec --check` 干净。
2. **两级 failure_class**：进展读的 `failure_class` 是“是否产出报告”的评测级分类；
   `background_jobs.failure_class` 是重试分类。两者可能不同（如 `source_deleted`：
   job 侧 business、进展侧 infrastructure）。这是刻意分开的两个问题，报告与 ADR
   都写明，页面文案只说“任务失败、没有报告”，不声称可重试性。
3. **live 进展可能短暂滞后**：上报桥失败被 collector 忽略（不把显示功能变成评测的
   可用性依赖）；此时 live 计数停在上一次成功写入，终态计数由守卫按密封报告写定。
   读从不编造判定。
4. **终态 `progress_failed` 以报告为准**：重试尝试期间 live 计数可能更高，报告被接受
   时守卫写报告自己的 failed 案例数（终态真值）。
5. **重编号**：迁移 0038→0039、ADR 0016→0017（integration 已被 T060 占用）。若后续
   merger 再重编号，只需同步本报告/PROGRESS/迁移头与 `test_t065_migration.py` 的
   **名字**断言（该测试不钉版本号）。
6. **T052 基线有意迁移**：`tests/f13a/test_t052_negatives.py` 的 published_version
   用例原断言 blocked 案例**没有任何 case 行**；T065 的进度桥现在把“无 run 的
   runner 判定”记为该行（`run_id=NULL`、`state=blocked`、`verdict=blocked`），否则
   进展读无法看到它。该用例改为断言这一新事实（一行、无 run、verdict/reason 正确），
   并保留“报告 not_run 永不 passed”。不是放宽断言，而是把存储语义写实。
7. **withdrawn application 的 blocked 案例不落行**：进度桥每次重证 owner ACL；应用在
   提交后被归档时上报被拒（collector 视为 live 进展不可用），该 case 行不存在；终态
   计数仍由完成守卫按密封报告写定。这是有意的 fail-closed，不缓存授权。

## 6. 下一票接口

- **T056（#57）**：`platform.evaluation_case_runs` 现在同时有 `state`（run 观测态）
  与 `verdict`（runner 判定），逐例 UI 可直接用后者；`GET /evaluations/{id}/progress`
  提供总数/已判定/失败数；页面 `/evaluations/{id}` 已存在，T056 的评测页可从提交结果
  直接链接它，无需重复实现进展/失败分类。
- **后续进度写入者**：必须复用
  `internal/execution/evaluation_runs.go` 的 `evaluationProgressDoneSQL` /
  `evaluationProgressFailedSQL`；不要另写“已判定”定义。
- **完成守卫**：任何新增 report 写入路径都必须同时写终态计数（当前只有
  `guardEvaluationSuccess` 一处）。
- **cancelled 分类**：若将来加入评测级取消端点，应复用
  `cancelEvaluationTx`（现有语义）而非在进展读里推断。
- **迁移**：`0039_evaluation_progress.sql`；ADR `0017`。
