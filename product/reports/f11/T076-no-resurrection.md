# T076 — 删除后恢复与组件历史不能让内容复活

- 任务：T076 / GitHub #77（父 F11），"删除后恢复与组件历史不能让内容复活"
- 分支：`t/t076-no-resurrection`；基线 `integration/t068-t082` @ `affc12c`；
  合并提交见 §5
- 状态：**passed**（OTel/metrics 专用行 **partial**：本仓库无 tracer/metrics SDK，
  交付的是只读 operator 告警面 + 退出码，见 ADR 0032 §2）
- 边界：第三方组件自有存储、备份介质、用户已下载副本**不由平台擦除**；T072 只把
  它们登记为 external/not_erasable，本票的告警报文重复该边界（§1.4）。备份工具本身
  属 T072，本票不做恢复工具。

## 0 工作记录

| # | 动作 | 结果 |
|---|---|---|
| 1 | 读 BRIEF、#77 票面、`notes/f11-deletion.md` §3.5/§3.10/§7/§9/§10、F11 四票实现 | 发现 3 个真实缺口（§1.1–§1.3） |
| 2 | 先写失败测试：`tests/f11/test_t076_recovery.py` 的 linked-input 用例在修复前返回 `recovered`（真实复现） | 红 → 修复 → 绿 |
| 3 | 修复 T038：`input` = 主输入 artifact ∪ `run_artifacts.relation='input'` 全集 | `internal/execution/{recovery,recovery_store}.go` |
| 4 | 新增只读清理告警面 `orchara-admin deletion alerts` + `internal/deletion/alerts.go` | 真实超时/逾期/停滞三类告警 + 退出码 3 |
| 5 | 守卫保留 `delete_timeout` 分类（此前被 evidence 结果覆盖为 `transient_storage_io`） | 告警面可见真实超时 |
| 6 | 扩展现有 Q45 门禁：组件删除充分性（`unverifiable_copies` / `deletion_sufficient`） | `gates.f11_component_deletion`，提交证据仍 `do_not_adopt` |
| 7 | 回归套件 6 文件：late writer / recovery / alerts / restore / adoption / governance | 真实进程、真实字节、真实 replay |
| 8 | 合并 `integration/t068-t082`（基线 0048 重号已由 `affc12c` 修复） | 见 §5 |

## 1 交付

### 1.1 恢复拒绝"每一个输入"而不只是主输入（真实缺口，已修）

`runs.input_artifact_id` 只是请求 Run 的封存消息；`internal/requests/submit.go`
把每个附件同样以 `run_artifacts.relation='input'` 关联，supervisor 也是按该关系
发现本次输入。修复前，删除附件后 T038 recovery 仍 `recovered`（
`tests/f11/test_t076_recovery.py::test_recovery_rechecks_every_linked_input_artifact_not_only_the_primary`
在修复前真实失败）。

修复：`linkedInputArtifactIDs` 取主输入 + 全部 input link（排序去重），
`checkInputArtifact` 与写入事务内的 `recheckRecoveryArtifactRows` 逐个复核 ready /
未删除 / 有 hash+size / 封存字节一致；任一失败是既有 `input_deleted`，并点名该
artifact id；hash 不符仍是 `input_hash_mismatch`。拒绝发生在任何证据写入之前，二次
重放同样拒绝且不写。

### 1.2 清理超时告警（新增，只读）

`internal/deletion/alerts.go` + `orchara-admin deletion alerts [--deadline 24h]
[--limit N]`：从 `platform.deletion_jobs` / `platform.background_jobs` 读出

| code | 含义 |
|---|---|
| `deletion_job_overdue` | 逾期（默认 24h，OPERATIONS.md §36）仍未完成的删除 job |
| `deletion_job_failed` | 删除 job 证据事务记为 failed |
| `cleanup_timeout` | `delete_content` 尝试报告 T071 的 `delete_timeout` |
| `cleanup_failed` | `delete_content` job 终态失败 |
| `cleanup_stalled` | `delete_content` 超过 deadline 仍未被认领 |

单行 JSON：`{status, deadline, checked_at, deletion_jobs, cleanup_jobs,
jobs_examined, alerts[], boundary}`；告警只含 id/code/state/age/attempt/剩余 scope，
不含正文、object key、hash。退出码 0=无告警，3=有告警（`AlertsExitCode`），
2=用法，1=无法执行——`cmd/admin` 为此新增 `exitCodeError`，把"已判定信号"与失败
分开。命令只读：不推进 job、不删字节、不碰 tombstone。

同时修正了一个真实可见性缺陷：`delete_content` handler 的 bounded timeout 会被
completion guard 用 evidence 结果覆盖（`objects_present` → `transient_storage_io`）。
现在 guard 在"唯一剩余证据就是对象仍在"时保留 `delete_timeout`；deletion job 行仍
记录 evidence 派生的错误码，超时不会被用来隐藏未清 scope（§1.3 的
`cleanup_timeout` 因此才有真实数据来源）。

### 1.3 晚到 writer 不能复活（回归 + 命名证据）

- **late `artifact_finalize`**：真实 DELETE 后经真实
  `orchara-admin background-jobs enqueue` 重放（带删除前 revision/hash），真实
  maintenance 执行 → job `failed` / `source_deleted` / `failure_class=business`，
  sealed key 集合不变，artifact 仍 404；重放两次。删除**之前**若 finalize writer
  仍活着，DELETE 返回 409 `resource_in_use`（`references=["background_job"]`）。
- **late `document_ingest`**：T061 的 parked-handler 技术（work-only 进程上传 chunk
  body、扣住 finalize pool）→ 真实 DELETE 文档 → 放开 finalize pool，旧 handler
  跑完 → 激活事务拒绝 `source_deleted`、索引版本 `activated_at` 保持 NULL、文档
  保持 deleted；随后 T071 purge 依据 purge 时权威行清掉晚到任务上传的 chunk body。
  重放两次：reindex 404、无新字节、无新 job。
- **恢复/领取边界**：parked run 的删除输入 → recovery `input_deleted`（两次）；
  队列 Run 的输入被删 → 真实 cancel，连续两次 claim 204 且无 attempt。

### 1.4 组件删除不足即不准采用（扩展 Q45 门禁，未另起一套）

`mem0exp/decision.py::deletion_proof` 把 `residual_body_copies`（已观察泄漏）与
`unverifiable_copies`（组件无法证明可清除：无删除路径的 store/log/cache，或显式
`deletion_sufficient:false`）合并为组件删除门禁
`gates.f11_component_deletion`；任一非空 → `do_not_adopt`，与
`gates.f11` 独立。`mem0exp/records.py` 拒绝缺该门的 `adopt`、拒绝列出未清副本却
声称门禁通过的自相矛盾记录。提交的 T066 证据（6 个 residual、`do_not_adopt`）
校验不变，仍不采用。

边界诚实性：本票**没有**重新跑一次 Mem0（隔离 venv 不是产品依赖，本机不保证），
新 Mem0 删除尝试记 `not_run`。

### 1.5 治理门禁证据集中在一处

`tests/f11/test_t076_governance.py::test_the_whole_deletion_governance_chain_has_evidence_at_every_gate`
按顺序重放并记录每个门禁的证据（缺一个即失败）：

| gate | 证据（真实来源） |
|---|---|
| `deletion_accepted` | 202 + `deletion_jobs` 行 + `deletion.requested` 审计 + 同事务 purge job |
| `ledger_tombstone` | `deletion_ledger` 的 `tombstone` 行（sequence/record_sha256/prev_sha256） |
| `read_refusal` | 文档 404、列表空、引用 404、检索空、缓存 miss、正文 marker 不出现 |
| `purge` | T071 三 scope 完成、对象消失、chunk/cache 0 |
| `copy_ledger` | T072 四 scope 完成、job `completed`、ledger `job_completed`、third-party 结果如实记录 |
| `grant_read` | 真实字节 200 + 一条内容读取审计 |
| `grant_revoked` | 撤销后 403 `diagnostic_grant_revoked` |
| `grant_deleted_content` | 真实删除后同一 grant 403 `diagnostic_run_deleted`，正文不出现 |
| `late_writer` | 重放 finalize `failed/source_deleted`，sealed 集合不变 |
| `final_read_refusal` | 文档/source artifact 仍 404、检索空、chunk 0 |

### 1.6 备份/旧 checkpoint 边界（真实 replay）

`tests/f11/test_t076_restore.py`：把 sealed 字节从**测试自持备份文件**写回对象
存储（行仍 tombstone）→ 一切读取仍 404（对象不是读权威）；再做一次"未回放账本的
数据库恢复"（清 `deleted_at`/`state='ready'`）→ 平台无法区分恢复行与活跃行（此即
OPERATIONS.md §46 要求在开放读取前回放账本的原因）；真实
`orchara-admin deletion replay --file` 重新施加 tombstone → 再次 404；第二次回放
`replayed=0` 且 ledger 行数不变（不用 sleep 断言）。

## 2 验收标准（逐条）

| AC | 结论 | 证据 |
|---|---|---|
| 晚到 finalize/ingest 与恢复拒绝已删输入 | **passed** | `test_t076_late_writer.py::test_a_replayed_finalize_job_cannot_resurrect_a_deleted_artifact`、`::test_a_parked_ingest_handler_cannot_activate_a_really_deleted_document`、`test_t076_recovery.py` 3 用例（含 linked-input 修复）；每条重放两次 |
| Mem0 删除无法满足时不准采用 | **passed** | `test_t076_adoption.py` 3 用例（`decision` 与 `records` 双门禁 + 提交证据不变） |
| 清理超时有告警 | **passed**（OTel/metrics 专有行 **partial**） | `test_t076_alerts.py` 2 用例：真实 `--delete-content-timeout 1ns` → `delete_timeout` → `cleanup_timeout` 告警 + 退出码 3；逾期/停滞/失败告警；完成后恢复 `ok` |
| 端到端入口实际演示；失败、授权与重试/恢复边界有断言 | **passed** | 6 个 f11 T076 文件共 13 用例；真实 HTTP/DELETE/maintenance/admin/ledger/grant |
| 记录版本、输入/hash、命令、原始结果、profile；stub/real 分报 | **passed**（原始命令与尾部输出见 §3；Mem0 新删除尝试 `not_run`） | §3、§4 |
| 切片边界：按来源图核对平台和组件副本；读取时重查 tombstone | **passed** | 治理链（§1.5）+ T071/T072 的按来源 scope 清理 + 每个读路径的 tombstone 谓词（T067 起的逐项复检） |

## 3 门禁命令与原始输出

（最终数字在 §3.z；以下为命令原文。）

```powershell
$env:PATH = "E:\orchara-toolchains\go\bin;$env:PATH"
$env:GOCACHE='E:\orchara-toolchains\gocache'; $env:GOMODCACHE='E:\orchara-toolchains\gomodcache'
cd E:\orchara-worktrees\t076\product

python -m pytest tests/f11 -q --basetemp=E:\orchara-tmp\t076-<run>
go build ./... ; go vet ./... ; go test ./...
gofmt -l .
python tools/sync_spec.py --check
```

### 3.z 实测

（最终数字：见下；所有运行均为真实 PG 16.4 + 现场编译的 admin/api/maintenance/supervisor。）

```
# 目标套件 ×2 背靠背（含本票 13 用例，tests/f11 共 81 用例）
python -m pytest tests/f11 -q --basetemp=E:\orchara-tmp\t076-f11-runB
81 passed in 732.23s (0:12:12)
python -m pytest tests/f11 -q --basetemp=E:\orchara-tmp\t076-f11-runC
81 passed in 790.14s (0:13:10)
```

诚实记录：一次全量 `tests/f11` 运行中 T069 的
`test_t069_recall.py::test_a_deleted_memory_is_not_recalled_by_a_later_agent_turn`
失败一次（`tests/support/t063.py:148` 期望恰好 1 条 `context-trace`，实际该 run 只有
stats 的 4 个工具产物，无 trace 行）；隔离重跑 1 passed（44.40s），随后两轮全量连续
green（上面 runB/runC）。该用例与本票改动无交集（本票未触碰 node-results/trace/agent
执行路径），属既有 WSL sandbox/agent 链路的瞬态；未放宽任何断言。

相邻套件（本票改动可影响面）：

```
# 本票改了 execution/recovery 与 background delete_content，故跑 T038 恢复/拒绝子集
# + tooling + Mem0 采用门禁既有套件 + F10 摄取/激活守卫子集
python -m pytest tests/tooling tests/f03/test_t038_recover.py tests/f03/test_t038_recovery_refusals.py tests/f09/test_t066_mem0_experiment.py tests/f10/test_t061_races.py tests/f10/test_t055_ingestion.py -q --basetemp=E:\orchara-tmp\t076-neighbours
57 passed in 287.34s (0:04:47)
```

Go/规格门禁：

```
go build ./...            -> exit 0
go vet ./...              -> exit 0
go test ./...             -> exit 0（35 ok / 6 no-test / 0 FAIL；internal/deletion 无 Go 单测，由真实栈用例覆盖）
gofmt -l .                -> 无输出（clean）
python tools/sync_spec.py --check -> "no drift: spec copy and contract snapshot match the manifest"，exit 0
```

## 4 not_run / blocked

- 新的 Mem0 删除尝试：`not_run`（隔离 venv 非产品依赖，本机不保证；提交证据仍被
  重新校验）。
- OTel/metrics/tracing 导出：本构建无 SDK（ADR 0028 §6 复核），告警面是只读命令
  + 退出码，**不声称**有 tracer；对应 AC 记 **partial**。
- 真实备份介质擦除/恢复工具：T072 的账本回放是恢复侧控制，备份软件本身不在本票。
- 浏览器 DOM：页面未改（本票无 UI 变更）。
- 全量 `pytest tests`（>60 min，BRIEF §3 禁止）。

## 5 合并

见最终汇报（`git merge integration/t068-t082` 的 merge commit）。

## 6 与后续票的接口

- `orchara-admin deletion alerts`（`internal/deletion.LoadAlerts`）是删除积压的
  唯一 operator 告警面；新增清理 kind 若想报告超时，必须同时（a）把 code 加入
  `retryableByKind`，(b) 在 completion guard 用 evidence 覆盖前保留该 code。
- recovery 的输入集合 = `runs.input_artifact_id` + `run_artifacts.relation='input'`；
  新资源类型必须继续写这个关系而不是新列。
- `gates.f11_component_deletion` + `cleanup.unverifiable_copies` 是任何第三方
  记忆/RAG 组件采用记录的新必填证据；`do_not_adopt` 结论不变。
- 恢复流程仍是 `orchara-admin deletion replay --file`（T072），本票只证明它是
  恢复后关闭内容复活的控制点，并给出"未回放即暴露"的诚实对照。
