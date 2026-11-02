# F00B T026 验收：评测池不饿死封存，报告失败与任务失败可区分

- 任务：F00B / GitHub Issue #27（票 `T026`，规划 ID F00B.04），分支 `feat/f00b-f02a`
- 状态：**`partial`**（真实 PostgreSQL 16.4 + 真实编译 Go 二进制 + 真实 HTTP/mTLS + 真实对象字节；
  **模型 kind** 的评测池、报告绑定、读权限、孤儿清理全部真实跑通。三条 partial 原因（复核修复
  2026-11-02 补第三条，全部如实）：1) 票面 `T026.md:9` 要求区分“业务 job/evaluation/**deletion**
  状态”，而 0013 明确不建 F07 的 deletion 模型——deletion 状态属 F07，未实现；2) 真实评测
  runner/checker 属 F13A，本票是显式合成占位；3) `kind=application` 端到端执行仍依赖 F13A 的
  runner（T027 已补齐 snapshot 表/FK，绑定结构可用）。不写成 passed）
- 代码提交：
  - `21eecff`（`feat(f00b): execute evaluations from a guarded, pool-separated maintenance process`）
  - `73f17eb`（`test(f00b): prove the evaluation pool, report binding and orphan sweep on the real stack`）
  - 本报告、`PROGRESS.md`、ADR 0004 §31–37 在随后的 `docs(f00b)` 提交中
- 主要文件：
  - `db/migrations/0013_evaluation_pool.sql`（评测两表、补齐两个延期 FK、snapshot FK 继续延期、索引、权限）
  - `cmd/admin/{evaluations.go,main.go}`（`evaluations enqueue` 控制接口）
  - `cmd/maintenance/{run.go,sweep.go,main.go,scope.go,client.go}`（双池调度、孤儿清理、job-scoped binding 客户端）
  - `internal/background/{evaluation.go,complete.go,claim.go,handler.go,scope.go,job.go}`
    （评测读模型、完成守卫、claim→running、handler 设置、重试表）
  - `internal/background/handlers/{application_evaluate.go,artifact_finalize.go}`（合成评测器、registry）
  - `internal/api/{evaluations.go,server.go,internal.go}`（评测/报告 owner-or-admin 读、内部 binding 端点）
  - `internal/objectstore/objectstore.go`（`List` 供 sweep）
  - `internal/config/{config.go,config_test.go}`（双池槽位/等待超时、sweep 配置）
  - `tests/support/{t026.py,fixtures.py,f00b.py,f02a.py,t025.py}`、`tests/f00b/test_t026_*.py`、
    `tests/f01/t019_stack.py`、`tests/f02a/test_t02{3,5}_migration.py`
  - `docs/adr/0004-f00b-f02a-boundaries.md`（§31–37）
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离开发集群 `127.0.0.1:55432`（`E:\orchara-pg`，`tools/pg_dev.py`）
  - Python 3.12.2；`cryptography` 36.0.2（真实 CA/服务端/客户端证书；未新增依赖）
  - profile：`dev-stub`；评测**不调用**任何模型 provider（合成器只对 dataset 做 JCS SHA-256 判定），
    因此本票没有 stub-vs-real 模型结论，见 §5
  - 基线（T025 之后）：`tests/f00b` 86、`tests/f02a` 62、`tests/f01` 155、`tests/tooling` 21
  - 本票后：`tests/f00b` **125 passed**（其中 T026 39；复核修复后 T026 为 **40**，新增 API-key
    scope 用例）、`tests/f02a` **62 passed**、
    `tests/f01` **155 passed**、`tests/tooling` **21 passed**；`go vet`/`go test ./...`/`gofmt -l`
    全绿，`sync_spec.py --check` no drift，无失败、无跳过

## 1. 交付内容

### 1.1 迁移 `0013_evaluation_pool.sql`

- `platform.evaluation_reports`：`id/manifest_sha256/dataset_sha256/result_artifact_id(FK artifacts)/
  status∈{passed,failed,not_run}/created_at`，两 hash 各有 64 hex CHECK。不可变（无更新路径）。
- `platform.evaluation_jobs`：契约基线列 + `background_job_id UNIQUE NOT NULL REFERENCES
  background_jobs`、`owner_id`、`snapshot_id`（**裸 uuid，无 FK**）、`model_profile_version_id`
  （FK）、`dataset_artifact_id`（FK）、`report_id`（FK reports）、`state∈{queued,running,succeeded,
  failed,cancelled}`、`error_code/finished_at`，以及契约的 kind XOR CHECK：
  `(kind='application' AND snapshot_id IS NOT NULL AND model_profile_version_id IS NULL) OR
  (kind='model' AND model_profile_version_id IS NOT NULL AND snapshot_id IS NULL)`。
- 补齐 0010 延期的 `publication_reviews.report_id -> evaluation_reports(id)` 与 0011 延期的
  `model_profile_versions.evaluation_report_id -> evaluation_reports(id)`（具名约束、`DO $$` 幂等）。
  `snapshot_id` 继续延期给 T027（迁移 0014）并写进迁移头/COMMENT：T027 必须加
  `evaluation_jobs_snapshot_fkey`，在此之前不得当已验证引用。
- 索引：`evaluation_jobs_owner_state (owner_id, state, created_at DESC)`、
  `evaluation_jobs_report (report_id) WHERE report_id IS NOT NULL`；`background_job_id` 由 UNIQUE 约束
  索引覆盖。权限沿用 0007–0012（`orchara_worker` 无 platform USAGE/表/序列权限，PUBLIC 无）。
- 不建 F07 deletion 模型；不写发布版本/快照；不写 `model_profile_versions.evaluation_report_id`
  （F13A/F02B 责任）。

### 1.2 评测创建控制接口 `cmd/admin evaluations enqueue`

```text
orchara-admin evaluations enqueue --kind model|application --owner-login LOGIN \
    --dataset-artifact-id UUID [--model-profile-version-id UUID] [--snapshot-id UUID]
```

- 一个事务：`EnqueueTx(kind=application_evaluate, target=evaluation_id, owner=owner-login 解析出的
  principal, dedup_key=evaluation_id, input_artifact_id=dataset)` + `evaluation_jobs` 行（state=queued）。
  任一失败（FK、候选缺失、login 不存在、约束）全部回滚，不留部分行。
- 形状错误 exit 2（缺 kind、未知 kind、model 无候选、model 带 snapshot、application 无 snapshot、
  application 带候选、畸形 UUID、空 login）；数据库语义拒绝 exit 1（未知 login、候选不存在、dataset
  FK 不存在），同样不写行。
- 成功打印 `{"id","kind","state","background_job_id"}`。公共 `POST /applications/{id}/evaluations`
  仍属 F13A；T026 不实现它（测试未调用，路由 404）。

### 1.3 `application_evaluate` handler 与合成评测器

- handler 只经 `background.JobScope` 访问 job-scoped 数据：
  1. 内部 binding 端点取得 `{evaluation_id,kind,dataset_artifact_id,manifest_sha256}`；
  2. dataset 未 ready 时用 `artifact-status` 轮询 + heartbeat（默认 100ms 轮询/5s 心跳，测试 50ms；
     `--dataset-wait-timeout` 可配）；quarantined/deleted/404 → business `source_deleted`，超时 →
     可重试 `dataset_wait_timeout`；等待发生在 work 池，**不占 system_io 槽**；
  3. signed GET 下载 sealed dataset（≤20 MiB），计算 SHA-256；
  4. 合成评测：dataset 文档 `{schema:"orchara.evaluation-dataset/1",cases:[{id,input,
     expected_sha256}]}`，对每个 case 的 `input` 取 RFC 8785 JCS 字节的 SHA-256 与声明值比较；
     空 cases → report `not_run`，坏 dataset → 一个 failed `dataset` case（report `failed`，job 成功）；
  5. signed PUT 报告 + complete（`purpose=report`、`application/json`）→ 轮询 report artifact ready
     （heartbeat 续租）→ `succeeded` + `result_artifact_id`。
- 报告文档（`background.EvaluationReportDocument`，handler 与守卫共用一个类型）：
  `report_version=1`、`kind="evaluation"`、`evaluation_kind`、`evaluation_id`、`dataset_artifact_id`、
  `dataset_sha256`、`manifest_sha256`、`status`、`checks[]`、`case_count`、`cases[{id,status,
  expected_sha256?,actual_sha256?,detail?}]`、`synthetic=true`、`evaluator="synthetic-jcs-case/1"`。
- `kind=application`：handler 结构上可运行同一路径，但 T027 前 binding 返回 409
  `evaluation_binding_unavailable` → 以 `evaluation_binding_missing` 失败、无报告。报告如实记 partial。

### 1.4 服务端完成守卫（`internal/background/evaluation.go` + `complete.go`）

- 仅 `state=succeeded` 且 `result_artifact_id` 是**该 job 的、ready、未墓碑、owner 一致**的 artifact
  （复用 T024 `verifyJobResultArtifact`）才进入守卫；缺 result artifact 是 422 且不改 job。
- 同一事务内：`FOR UPDATE` evaluation 行（必须属于本 job/owner、state ∈ queued/running）；读 dataset
  行（ready、未墓碑、64 hex sha256；否则 `source_deleted`/`dataset_not_ready`）；解析绑定 manifest
  （model→候选 `manifest_sha256`；application→snapshot `manifest_sha256`，存在且 owner 一致，否则
  `evaluation_binding_missing`）；读 sealed 报告字节（≤1 MiB、重算 SHA-256 必须等于 artifact 记录值）；
  严格 JSON（禁未知字段/尾随数据）并校验文档。
- 绑定/状态规则：`manifest_sha256==bound`、`dataset_sha256==artifacts.sha256`、
  `dataset_artifact_id`/`evaluation_id` 一致（否则 `report_binding_mismatch`），`evaluation_kind` 一致；
  `passed` 需 ≥1 case + ≥1 check + 全 passed；`failed` 需 ≥1 case + ≥1 check + 至少一 failed；
  `not_run` 才能 0 case；其余（空 case、空 check、status 与 cases 矛盾、坏 id、大小/哈希不符）→
  `report_invalid`。
- 通过 → 同一事务插入 immutable `evaluation_reports`、evaluation `succeeded+report_id+finished_at`、
  job `succeeded`；拒绝 → job `failed`（business，不重试）+ evaluation `failed`（同 error_code）+
  **无 report 行**。sealed 读失败 → job `failed(transient_storage_io)` 可重试，evaluation 保持 running；
  第三次尝试失败才终态。
- handler 主动失败：重试表内码 → job ready 重试（evaluation 保持 running）；business 码 → job failed +
  evaluation failed；cancelled → evaluation cancelled。`application_evaluate` 重试表固定为
  `transient_storage_io`/`transient_internal`/`lease_expired`/`dataset_wait_timeout`。
- Claim 在同一事务把 evaluation `queued -> running`；retry 不减。评测终态由 Complete 的守卫
  写（succeeded/failed/cancelled），**以及租约耗尽时的恢复事务**写 failed：`recoverExpiredTx`
  （`Claim` 与 `SweepExpired` 共用）在第三次过期租约把 job 置 failed 的同事务里，
  若 job 是 `application_evaluate` 且 evaluation 尚未终态，则把 evaluation 也置
  `failed`、同一 `error_code`、`finished_at=now()`（第二次复核修复，ADR §54）。可重试的恢复
  （回 ready）不触碰 evaluation，它保持 running。测试：
  `test_t026_failures.py::test_exhausted_leases_fail_the_evaluation_without_a_report`
  （修前 job failed 而 evaluation 永远停在 running）。

### 1.5 运行时双池（`cmd/maintenance run`）

- `system_io` 池：仅 `artifact_finalize`，默认 **2 槽**；`work` 池：其余已编译 kind，默认 **1 槽**。
  每槽独立 worker/worker_id（`<base>:<pool>:<slot>`），claim→execute 循环与 T022 相同（首心跳、
  周期心跳、提交结果、stale 处理）。
- `--kinds` 只能收窄仍按池划分：`--kinds artifact_finalize` 只起 system_io；`--kinds
  model_validate,application_evaluate` 只起 work。启动日志保留 T022 的 `run: worker=... kinds=...
  once=... heartbeat=...` 行，并新增 `run: pools=...`；summary 聚合两池，jobReport 增 `pool`。
- `--once` 是**跨池 drain**：每个槽空 claim 时记录当前 activity epoch；所有槽在同一 epoch 观察到空
  队列才退出；任何 job 开始/结束 bump epoch 并清空标记（work handler 的 completeUpload 产生
  system_io job）。活动 job 存在时进程绝不退出；因此 `--once` 不会在报告 finalize 前退出。
- 新 flag/环境：`--system-io-slots`/`--work-slots`/`--dataset-wait-timeout`/
  `--evaluation-poll-interval`/`--report-wait-timeout`；`ORCHARA_MAINTENANCE_SYSTEM_IO_SLOTS`/
  `_WORK_SLOTS`/`_DATASET_WAIT_TIMEOUT`/`_EVALUATION_POLL_INTERVAL`/`_REPORT_WAIT_TIMEOUT`。
  非法值（0、>32 槽、0/过长等待、<10ms 轮询）启动 exit 2；环境先校验、flag 覆盖。

### 1.6 评测/报告读

- `GET /api/v1/evaluations/{evaluation_id}` → `EvaluationJob`（六字段：id/kind/state/
  background_job_id/report_id/error_code）。
- `GET /api/v1/evaluation-reports/{report_id}` → `Report`（四字段：id/status/manifest_sha256/
  result_artifact_id）。
- 都是 owner-or-admin：evaluation 的 owner 或 admin 200，其余（含未知/畸形 id、其他用户、无 grant
  用户）404（与不存在同答）；report 无 owner 列，owner 由产生它的 `evaluation_jobs.report_id` join
  判定，未被任何 evaluation 引用的 report 404。响应只含契约字段，不返回 dataset/candidate/snapshot
  id、object key、报告正文或 draft。

### 1.7 孤儿清理 `cmd/maintenance sweep`

```text
orchara-maintenance sweep --orphan-age 1h [--dry-run]
```

- 只需要 `ORCHARA_MAINTENANCE_DSN` 与 `ORCHARA_OBJECT_DIR`（不需要内部 mTLS）；不领取 job、不写业务行。
- `staging/*`：仅当不存在 “live 上传者”（`uploading`/`scanning` 且 `deleted_at IS NULL` 的 artifact
  行以该 key 为目标）且年龄 ≥ orphan-age 时删除；ready/quarantined/deleted 的 staging 残留可删。
  reason 记录 `artifact_ready`/`artifact_quarantined`/`artifact_deleted`/`no_artifact_reference`。
- **`sealed/*` 本命令永不删除（第二次复核修复，ADR §55）**：sealed 对象先于 artifact 行提交写入，
  一次性引用快照不能证明它没有主人（写入者可能正处于“已 seal、未 commit”之间），旧实现据此删除会
  误删刚登记的对象。票面只要求“孤儿暂存对象可清理”，因此本票只清 staging；未被引用的 sealed
  对象计数在 summary 的 `sealed_deferred` 里报告，清理留给 F07/F13A，且必须与登记/删除共享一把锁。
  staging 对象的旧 key 不会复用，年龄门槛仍用对象 mtime。
- 每删一条打印一行 JSON（key/store/reason/size/action），最后打印 summary；`--dry-run` 只输出
  `would_delete` 不动文件；删除幂等。临时文件/非法 key 不参与。

## 2. 池 / 环境 / 接口（T027、F13A、F07 可依赖）

| 项 | 事实 |
|---|---|
| 池 | `system_io` = artifact_finalize，默认 2 槽；`work` = 其他 kind，默认 1 槽；`--once` = 跨池 drain |
| 槽位/env | `--system-io-slots`/`ORCHARA_MAINTENANCE_SYSTEM_IO_SLOTS`、`--work-slots`/`ORCHARA_MAINTENANCE_WORK_SLOTS`，1..32 |
| 等待/env | `--dataset-wait-timeout`/`ORCHARA_MAINTENANCE_DATASET_WAIT_TIMEOUT`（默认 60s）、`--evaluation-poll-interval`/`ORCHARA_MAINTENANCE_EVALUATION_POLL_INTERVAL`（默认 100ms，≥10ms）、`--report-wait-timeout`/`ORCHARA_MAINTENANCE_REPORT_WAIT_TIMEOUT`（默认 60s） |
| 正式创建 | `cmd/admin evaluations enqueue`（唯一入口；公共评测端点属 F13A） |
| 内部端点 | 新增 `POST /internal/v1/background/jobs/{job_id}/evaluation`（maintenance 角色 + 活 attempt_token；返回 evaluation_id/kind/dataset_artifact_id/manifest_sha256）；T024 五端点不变 |
| 公共读 | `GET /api/v1/evaluations/{id}`、`GET /api/v1/evaluation-reports/{id}`（owner/admin，其余 404）；复核修复后 API key 还须 `application:read`（§7.4） |
| 表 | `platform.evaluation_jobs`、`platform.evaluation_reports`；`evaluation_jobs.snapshot_id` 裸 uuid（T027 补 FK `evaluation_jobs_snapshot_fkey`） |
| 错误码 | business：`evaluation_missing`、`evaluation_state_invalid`、`evaluation_binding_missing`、`evaluation_binding_mismatch`、`evaluation_unsupported_kind`、`dataset_not_ready`、`report_binding_mismatch`、`report_invalid`、`report_not_ready`、`source_deleted`；infra（可重试）：`transient_storage_io`、`transient_internal`、`lease_expired`、`dataset_wait_timeout` |
| 报告文档 | `background.EvaluationReportDocument`（导出类型；handler 与守卫共用）；上限 `MaxEvaluationReportBytes=1 MiB` |
| T027 前提 | `execution_snapshots` 必须有 `owner_id` 与 64 hex `manifest_sha256`；snapshot owner 必须等于评测 owner；T027 迁移补 FK 后 `resolveBoundManifest` 自动生效，无需改 T026 代码 |

## 3. 测试与原始结果

### 3.1 测试文件（真实 PG / 二进制 / HTTP / mTLS / 对象字节）

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `test_t026_migration.py` | 7 | 0013 记录/重放 no-op；两表列与 FK；补齐的两个延期 FK（含悬空引用被拒）；kind XOR/state CHECK；snapshot 无 FK；索引/UNIQUE；worker/PUBLIC 权限 |
| `test_t026_enqueue.py` | 12 | 7 种坏命令行 exit 2 且零行；未知 login/缺候选 exit 1 且零行；缺 dataset 由 FK 拒绝；model 成功两行一致；application 结构接受（snapshot 无 FK） |
| `test_t026_pool.py` | 2 | work 池两个等待评测占满时，system_io finalize 仍完成并 ready（finalize terminal 时两评测仍 leased/running）；数据集完成后两评测 succeeded + report passed；`--once` 跨池 drain 覆盖报告 finalize |
| `test_t026_failures.py` | 7 | case failed → report failed + job/evaluation succeeded；空 cases → not_run；坏 dataset → report failed；dataset 墓碑 → job/evaluation failed + error_code + 无 report；sealed 缺失 → 3 次重试后 `transient_storage_io` + 无 report；application kind → `evaluation_binding_missing` + 无 report；**第二次复核新增**：3 次租约过期（下一次 claim 同时恢复）→ job failed(`lease_expired`) + evaluation failed(同码) + 无 report，且第一次可重试恢复时 evaluation 保持 running（修前 evaluation 永远停在 running） |
| `test_t026_binding.py` | 8 | 直接 claim/upload/complete/finalize 后提交：错 manifest/错 dataset → `report_binding_mismatch`；坏 JSON/空 cases 声称 passed/无 checks/case_count 不符 → `report_invalid`；合法 not_run 被记录但绝不 passed |
| `test_t026_authz.py` | 3 | 非 admin owner 200、其他用户 404、admin 200（evaluation/report/background job）；未知/畸形 id 404；未认证 401；字段精确且无 body/路径泄漏；**复核修复新增 API-key scope**：write-only key 403 `insufficient_scope`，read-scoped key 非 owner 404，真正 owner 后 200（scope 必要但不充分） |
| `test_t026_sweep.py` | 2 | **第二次复核后**：真实孤儿 staging 被删；ready 行 staging 残留被删；scanning 行 staging 保留；未引用的 sealed 孤儿**保留**并在 `sealed_deferred` 计数（修前被删）；被引用 sealed（ready 与 deleted 行）保留；dry-run 不动文件；重复 sweep 删 0；缺 `--orphan-age`/负值 exit 2 |

### 3.2 收尾门禁（实际命令与原始输出尾部）

> 下列数字是**本票完成时**（早于 2026-11-02 两轴复核修复）的原始结果；复核修复后最新门禁与
> 用例数见 `PROGRESS.md` 的“F00B/F02A 两轴复核修复”与“F00B/F02A 第二次复核修复”两节。

```text
$ E:\orchara-toolchains\go\bin\go.exe vet ./...
(no output)                                        exit=0

$ E:\orchara-toolchains\go\bin\go.exe test ./...
?   	orchara/platform/cmd/admin	[no test files]
?   	orchara/platform/cmd/api	[no test files]
?   	orchara/platform/cmd/maintenance	[no test files]
ok  	orchara/platform/internal/api	(cached)
ok  	orchara/platform/internal/background	(cached)
ok  	orchara/platform/internal/catalog	(cached)
ok  	orchara/platform/internal/compiler	(cached)
ok  	orchara/platform/internal/config	(cached)
ok  	orchara/platform/internal/modelproxy	(cached)
ok  	orchara/platform/internal/objectstore	(cached)
...
ok  	orchara/platform/internal/workflow	(cached)
exit=0

$ E:\orchara-toolchains\go\bin\gofmt.exe -l internal cmd tools
(no output)                                        exit=0

$ python -m pytest tests/f00b -q
........................................................................ [ 57%]
.....................................................                    [100%]
125 passed in 354.19s (0:05:54)

$ python -m pytest tests/f02a -q
..............................................................           [100%]
62 passed in 220.44s (0:03:40)

$ python -m pytest tests/f01 -q
........................................................................ [ 46%]
........................................................................ [ 92%]
...........                                                              [100%]
155 passed in 457.63s (0:07:37)

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 4.80s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest

$ git status --short
(empty after the docs commit)
```

T026 定向（同一真实栈；用于定位失败时重跑）：

```text
$ python -m pytest tests/f00b -q -k t026
.......................................                                  [100%]
39 passed, 86 deselected in 134.06s (0:02:14)
```

> 说明：`tests/f02a/test_t023_migration.py`、`test_t025_migration.py` 的迁移链断言从 0012 更新到
> 0013，`test_t023_migration.py` 的 “report_id 无 FK” 断言改为 “T026 的 FK 已存在”；`tests/f00b/
> test_t024_migration.py` 的 `evaluation_report_id` 从 “无 FK” 改为 “已绑定”；T019/T023/T025 的
> seed 辅助函数改插真实 `evaluation_reports` 行。这些是 schema 收紧后的预期适配，不是放宽断言。
> `tests/support/f00b.py::maintenance_once` 显式传 `--system-io-slots 1 --work-slots 1`，保持 T022
> 单线程测试的确定性；双池默认值与并发行为由 `test_t026_pool.py` 单独证明。
> **全量 `python -m pytest tests` 未在本票运行**（PLAN §1 规定由统筹收尾跑一次）。

## 4. 验收标准逐条（票面）

| 票面验收 | 状态 | 证据 |
|---|---|---|
| 评测池全等待时 finalize 仍推进 | **passed** | `test_t026_pool.py::test_finalize_progresses_while_both_work_slots_wait`：两个 uploading dataset 评测占满 work 槽，后入队的 finalize job 完成且 artifact ready，此刻两评测 job 仍 leased、evaluation 仍 running；随后数据集就绪，两评测 succeeded + report passed |
| 报告 failed 与 job failed 可区分 | **passed** | `test_t026_failures.py`：case failed → report failed、job/evaluation succeeded；dataset 墓碑/字节不可读 → job failed + evaluation failed + error_code、report 行 0；绑定/格式非法 → `report_binding_mismatch`/`report_invalid`、job failed、无 report |
| 状态查询严格按 owner/管理员权限 | **passed** | `test_t026_authz.py`：非 admin owner 200、其他用户 404、admin 200；未知/畸形 id 404；未认证 401；字段精确、无泄漏；background-job GET 仍 T022 规则 |
| 通过端到端入口实际演示；失败/授权/重试边界有断言，不以单层 mock 代替 | **partial** | 模型 kind 端到端（CLI→双池 maintenance→signed 下载/上传→守卫→公共读）全部真实跑通；application kind 因 T027 的 snapshot 表缺失只能结构性接受并如实失败；真实 F13A checker 未实现（合成占位）；票面要求区分的 **deletion 状态**不属本票（0013 不建 deletion 模型，F07 承接）。失败（墓碑/坏字节/坏报告）、授权（owner/other/admin + API-key scope）、重试（3 次、退避、bounded wait）均有真实断言 |
| 记录版本、输入/hash、命令、原始结果、环境 profile；stub/real 分报，缺条件 blocked，未运行 not_run | **passed（本报告）** | 版本/环境见页首；输入/hash 在迁移/测试断言中（candidate/manifest、dataset sha、report sha）；命令与原始输出见 §3；stub/real 见 §5；未运行见 §6 |

## 5. stub / real 分报

- **真实（本环境实际运行）**：PostgreSQL 16.4、`cmd/api`/`cmd/admin`/`cmd/maintenance` 真实二进制、
  真实 HTTP 与 mTLS、真实对象存储字节、真实 signed PUT/GET、真实双池进程与数据库状态迁移、真实
  owner/admin/other HTTP 读、真实 sweep 文件删除、真实迁移约束/权限拒绝。
- **合成（明确标注，不冒充 F13A）**：评测执行器 `synthetic-jcs-case/1`：只对 dataset case 的
  `input` 做 JCS SHA-256 与声明值比较；report `synthetic=true`。它证明的是“下载→评测→报告→封存→
  守卫→读模型”整条真实管道，不是真实 checker/真实模型通过。dataset schema 是本票定义的合成格式。
- **未运行的模型/评测能力**：真实模型调用（F13A；本票评测不调用 provider，故无 stub/real 模型结论）、
  真实 checker 套件、真实评测数据集语义、application-kind 端到端（T027）。

## 6. 未运行 / 部分 / 阻塞（如实）

| 项 | 状态 | 原因/归属 |
|---|---|---|
| **业务 job/evaluation/deletion 状态区分**（票面切片边界） | `partial`（**F07**） | 本票交付并测了 job 与 evaluation 两种业务状态（queued/running/succeeded/failed/cancelled）与报告失败/job 失败的区分；但 **deletion 状态机不在本批**：0013 明确不建 F07 的 deletion_jobs/墓碑传播/删除账本，sweep 也不删被引用的 sealed。票面这一句要求的 deletion 维度属 F07，未实现，不能写成“三类状态都已区分” |
| `kind=application` 评测端到端 | `partial`（T027） | `execution_snapshots` 不存在；本票结构接受、binding 409、job/evaluation failed、无报告；T027 建表+补 FK 后同一代码路径生效 |
| 真实评测 runner/checker | `not_run`（F13A） | T026 是显式合成占位；真实结果不得由本票声称 |
| 真实模型 provider 评测 | `not_run`（F13A/F06） | 本票评测不调用模型；无供应商凭据/endpoint |
| API key 的评测 scope | **`partial`（过渡实现）** | 复核修复（ADR §47）：契约未定义评测/job 专用 scope，三个公共读对 API key 强制平台唯一读 scope `application:read`（先 scope 403、后 owner/admin 404；会话不受限）。更细的专用 scope 与契约修订仍属 F13A，本批不假装已有专用 scope |
| 公共 `POST /applications/{id}/evaluations` | `not_run`（F13A） | 契约的受信 runner 入口；T026 不实现、不伪造 |
| F07 删除/墓碑传播 | `not_run`（F07） | sweep **不删任何 sealed 对象**（未引用孤儿只在 `sealed_deferred` 计数；清理需与登记共享锁，属 F07/F13A）；deleted artifact 的字节归 F07 |
| 全量 `python -m pytest tests` | `not_run`（统筹） | PLAN §1 规定全部票完成后只跑一次 |
| Redis 唤醒 | `not_run`（无实例） | 双池沿用 PG 轮询；`--once`/连续模式均只靠 claim 轮询 |

无环境阻塞项（`blocked` 为空）；上述均是有明确归属票的未运行/部分项。

## 7. 契约缺口与实现记录的偏差

1. **`BackgroundLease` 无处安放 bound manifest hash**（`additionalProperties:false`）。T026 新增
   **非契约内部端点** `POST /internal/v1/background/jobs/{job_id}/evaluation`（maintenance 角色 + 活
   attempt_token + 本 job 归属），只返回本 job 的 evaluation 事实。它不是公共路径；后续契约修订应把
   它纳入正式内部协议。替代方案（handler 用 maintenance DSN 读评测表）被拒绝，因为会把绑定决策
   移进 handler。
2. **评测报告文档 schema 不在契约**：`Report` 只有四个元数据字段；报告正文（cases/checks/绑定 hash）
   是本实现定义并导出的 `background.EvaluationReportDocument`。F13A 扩展 checker 时必须扩展该文档、
   守卫规则和测试，不能只改 handler 自证。
3. **application kind 的 snapshot FK 继续延期**：`evaluation_jobs.snapshot_id` 无 FK，T027 必须在
   0014 补 `evaluation_jobs_snapshot_fkey`；迁移/ADR/报告均写明。T026 不建 snapshot 行。
4. **读端点的 API key scope**：契约给 `getEvaluation`/`getEvaluationReport` 列了 `apiKey`
   security，但没有评测 scope 词汇。复核修复后，三个公共读对 API key 要求平台既有读 scope
   `application:read`（scope 先于 owner/admin；会话不受限）；更细的评测/job 专用 scope 仍属
   F13A/契约修订，本批不声称已有专用 scope（ADR §47）。
5. **`evaluation_reports` 无 owner 列**：契约基线如此；T026 用 `evaluation_jobs.report_id` 的
   EXISTS join 做 owner 判定，不在 reports 表加列。
6. **deletion 状态缺失**：票面切片边界要求区分业务 job/evaluation/**deletion** 状态；0013 不建
   F07 的 deletion 模型，本票只覆盖前两类。该条是显式 partial，F07 承接（见 §4/§6）。
7. **T023/T024 延期 FK 的测试适配**：0013 补 FK 后，旧 seed 的随机 `report_id` 不再合法；
   测试辅助函数改为插入真实 report 行（见 §3.2 说明），断言从“无 FK”改为“FK 存在且悬空引用被拒”。
   迁移不做事后回填：升级一个已有 dangling `report_id` 的旧库前必须先修正/清空这些行（本批无已部署
   数据，所有门禁都在新库上迁移）。
8. **`artifact_finalize` 的 T022 interim 维护 DSN 读仍保留**：job-scoped artifact-status 只暴露
   state/media/size，不暴露 staging key/expected hash/revision，finalize 无法在不扩展该端点的情况下
   迁移；本票不改它，激活仍只在服务端守卫。该 interim read 是只读且已在 ADR §9/§23 记录。
9. **evaluation job 的 `expected_*` 不参与绑定**：绑定由 `evaluation_jobs` 行决定（kind 选候选/snapshot），
   job 的 `expected_revision/expected_sha256` 留空。这是与 artifact_finalize 的有意区别，已写入 ADR §32。

## 8. T027 / F13A / F07 交接

- **T027（必须做）**：迁移 0014 创建 `platform.execution_snapshots`（契约列，至少
  `id/kind/application_id/application_version_id/draft_revision/owner_id/manifest/manifest_sha256/
  manifest_artifact_id/compiler_version/worker_image_digest/state_schema_version`），并补
  `ALTER TABLE platform.evaluation_jobs ADD CONSTRAINT evaluation_jobs_snapshot_fkey
  FOREIGN KEY (snapshot_id) REFERENCES platform.execution_snapshots(id)`。T027 的 snapshot 行的
  `owner_id` 必须等于评测 owner（binding 校验），`manifest_sha256` 必须是 64 hex。之后
  `cmd/admin evaluations enqueue --kind application --snapshot-id ...` 与现有 handler/守卫可直接跑通
  （无需改 T026 代码）；验收时可复用 `tests/support/t026.py` 的 pool/read/guard 辅助。
- **T027 可依赖**：双池与 timeout flags/env（见 §2）；`ResolveEvaluationBinding` 的 `to_regclass`
  自动探测；`background.EvaluationReportDocument`/`MaxEvaluationReportBytes`；公共读与错误码；
  `evaluation_jobs.snapshot_id` 已存在（无 FK）。
- **F13A**：替换合成 checker/runner，扩展 `EvaluationReportDocument`（cases/checks 语义）与守卫的
  状态/绑定规则；必须复用 `evaluation_reports` immutable 行、`succeeded+report_id` 与失败无 report 的
  不变量；不得让 handler/front-end 自证 passed。真实模型调用/预算属 F13A/F05。
- **F07**：deleted artifact 的 sealed 字节清理、评测/报告删除生命周期；sweep **不删任何 sealed
  对象**（含无行引用的孤儿，只报告 `sealed_deferred`），也不复活 quarantined。sealed 孤儿的
  安全清理与登记共享一把锁，属 F07/F13A（ADR §55）。
- **部署方**：设置 `ORCHARA_OBJECT_DIR`/签名 key/`ORCHARA_PUBLIC_URL`/内部 mTLS；按负载调
  system_io/work 槽位与等待超时；sweep 建议 cron + `--orphan-age`（例如 1h）。

## 9. 工作日志（实际命令摘要）

```text
# 实现后
go build ./...                          # ok
go vet ./...                            # 无输出，exit 0
gofmt -l internal cmd tools             # 空
go test ./...                           # 全绿，exit 0

# T026 定向（真实栈）
python -m pytest tests/f00b/test_t026_migration.py -q     # 7 passed
python -m pytest tests/f00b/test_t026_enqueue.py -q       # 12 passed
python -m pytest tests/f00b/test_t026_pool.py -q          # 2 passed
python -m pytest tests/f00b/test_t026_failures.py -q      # 6 passed
python -m pytest tests/f00b/test_t026_binding.py -q       # 8 passed
python -m pytest tests/f00b/test_t026_authz.py -q         # 2 passed
python -m pytest tests/f00b/test_t026_sweep.py -q         # 2 passed

# 回归/门禁
python -m pytest tests/f00b -q          # 125 passed
python -m pytest tests/f02a -q          # 62 passed
python -m pytest tests/f01 -q           # 155 passed
python -m pytest tests/tooling -q       # 21 passed
python tools/sync_spec.py --check       # no drift
git status --short                      # 提交后为空
```
