# ADR 0006：F05/F03 上传、工具、执行与恢复的边界

- 状态：已接受
- 日期：2026-11-03
- 相关：F05（T028/T030/T033/T037）、F03（T029/T031/T032/T034/T035/T036/T038）；分支
  `feat/f05-f03-run-and-tools`（基于 `feat/f00b-f02a` @ `51b0d4b`）；各票报告见
  `product/reports/f05/`、`product/reports/f03/`；前序决策见 ADR 0004、ADR 0005。
- 范围：本批只实现 F05/F03 的 11 张票；不包含 F04A（LangGraph 编译器/真实 worker）、F06（审批/
  模型/连接器/operations）、F07（requests/conversations/删除/幂等清理）、F13A、F15A/F15B。

## 背景

T027 已交付“冻结 snapshot + 排队 debug Run + 单个 ready start job + 幂等收据”，并显式把
`runs.request_id`/`conversation_id` 留给 F05/F07、把两个 attempt 外键留给 F03。本批在不改
`contracts/` 与 `docs/spec/` 的前提下，把执行身份、上传、预置工具、沙箱、事件、等待/恢复、取消、
工具构建和保守恢复做成真实可运行的闭环；每张票都留下迁移、报告和实测证据。

## 决定

### 1. 迁移账本 0015–0024：一票一迁移，全部可重放

| 迁移 | 票 | 结构变化 |
|---|---|---|
| `0015_artifact_admissions.sql` | T028 | 新表 `artifact_admissions`（声明/嗅探 media type、格式、裁决、安全原因） |
| `0016_execution_claims_and_quota.sql` | T029 | `run_attempts`、`quota_counters`、`quota_allocations`；补 `jobs.current_attempt_token`、`run_artifacts.attempt_token` 两个延期 FK；一 Run 至多一个 live attempt 的部分唯一索引 |
| `0017_tool_run_kind.sql` | T030 | 仅重建 `background_jobs_kind_check`，加入第九个 kind `tool_run` |
| `0018_run_events.sql` | T032 | `run_events`（契约基线）+ 回放/保留索引 |
| `0019_run_artifact_uploads.sql` | T033 | `run_artifact_uploads`（artifact 上传 provenance：run/attempt/purpose） |
| `0020_active_time_and_deadline.sql` | T034 | `runs.max_active_ms`、`run_attempts.active_ms`/`active_settled_at` |
| `0021_run_waitings.sql` | T035 | `run_waitings`（等待种类、checkpoint、决定、退出确认、唯一 resume job） |
| `0022_run_unknown_operations.sql` | T036 | `run_unknown_operations` + `run_waitings.cancel_requested_at` |
| `0023_tool_builds_and_review.sql` | T037 | 只扩 `tool_versions`：源/锁 hash、构建日志/SBOM、bundle digest、审核/撤销列 |
| `0024_run_recovery_evidence.sql` | T038 | `run_recovery_evidence` + `runs.recovery_reason`/`recovery_since` |

共同约束：全部 schema-qualified、幂等、重复 migrate no-op；`orchara_worker`/PUBLIC 不获得任何
`platform` 权限；新表/列都在迁移头注释里标为“对 contracts/database.sql 的显式偏离”并写明承接票。
没有任何一票回改已应用迁移 0001–0014。

### 2. 上传与文件（T028，ADR 0005 §3 的延续）

- `createUpload` 只签发 staging PUT；`completeUpload` 用 CAS 进入 scanning，同一 hash 幂等、异 hash
  409；`artifact_finalize` 在真实字节上复核 hash/size/格式后由服务端守卫激活或隔离。staging 在
  ready 后被覆盖不改变 sealed 字节。
- 格式准入是按 purpose 收窄的白名单：T028 只允许 text/csv/json/xlsx；T037 仅为
  `purpose='tool_source'` 增加受控 zip（路径/条目/解压/加密限制，不执行）。
- 读取（GET artifact/download/content）**owner-only**，管理员不获得任意用户文件读取；删除端点/
  删除任务/墓碑传播属 F07，T028 只交付 finalize 守卫对 tombstone 的拒绝（有真实竞争测试）。
- 配额/用量/对象清理仍按 F07/F15B；本批不建请求/会话表。

### 3. 预置工具（T030，ADR 0005）

- 可信 `sales_stats` 是 Python stdlib 工具（Decimal；CSV/XLSX；严谨拒绝），通过 `tool_run`
  background job + 维护进程的固定 argv 执行器运行；`POST/GET /api/v1/tool-runs` 是显式非契约桥。
- 输出 `summary.json`/`summary.csv`/诊断/日志均为 job-scoped artifact；结果不来自 stdout。
- T033/F04A 负责把同一工具绑定到 Run/沙箱；T037 负责不可信构建。

### 4. 执行控制面（T029、T031、T032）

- **领取**：仅 supervisor mTLS 可 `claimJob`；PG `FOR UPDATE SKIP LOCKED` + 原子 quota allocation +
  单个 attempt + `jobs.leased`/`runs.running` 同事务；无容量/无兼容 job 时 204 且零变化。Lease 的
  manifest/input URL 是真实签名 sealed GET；返回前必须 `VerifyManifestBytes`，不得重编译或从 JSONB
  重序列化推断 hash。
- **一 Run 一 live attempt** 由部分唯一索引保证；普通 worker/maintenance 不能领取。
- **结果提议 ≠ 终态**：`submitWorkerResult` 只存 `reported_result`/checkpoint/phase=finalizing；
  HTTP 200 时 Run 仍 running。`confirmAttemptExit` 记录证据并要求 started/protocol/manifest hash/
  image digest 一致（非契约证据字段）；任一证据缺失不终态。
- **统一 finalizer**：结果与退出任意顺序只终结一次；退出无结果 → `recovery_pending`；succeeded 要求
  输出 artifact ready/owner/linked；终态同事务完成 job/token/配额/事件。waiting/needs_reconciliation
  由 T035/T036 扩展同一 finalizer，不分叉。
- **事件**：`run_events` 由 Go 在业务事务内分配 run-local seq；worker 只能提交 `node.status` 且
  payload 严格白名单；SSE owner-only，`Last-Event-ID` 回放去重，超出保留窗口 410 + snapshot 链接，
  15s 注释心跳不占 seq。模型 delta 预览属 F06，本批 `not_run`，不伪造。

### 5. 沙箱工具调试与 run-bound artifact（T033）

- 真实 WSL2 + runsc（release-20241118.0）最小 Python rootfs（digest 必填部署输入），只读 rootfs/
  tool/inputs、可写 workspace、uid 65534、无 caps、独立 pid/net/ipc/uts/mount namespace、
  `--network=none`、CPU/内存/pid/超时限额；无宿主目录、无业务密钥。
- `cmd/supervisor exec --once`：supervisor 身份领取，worker 身份上传/登记，固定预置工具 + 单个
  run 输入；scoped 上传/完成/下载/状态/登记全部绑定 live attempt；旧 attempt 不能登记当前产物；
  owner-only `listRunArtifacts` 不把日志当数据；取消/超时/失败必须 kill 并验证无孤儿。
- supervisor/worker 进程拆分与真实 LangGraph worker 属 F04A；本批只执行固定预置工具。

### 6. 活跃时间与截止（T034）

- `runs.max_active_ms` 在创建时从冻结 policy 固化，NULL 表示未知/无截止，不当作 0；attempt 的活跃
  区间只结算一次（`active_settled_at`），跨恢复累加不重置；等待/终态/finalizing 不计。
- 截止时先记录 cancel 意图 + `deadline_exceeded`，停止新调用，退出后由统一 finalizer 提交
  `failed(deadline_exceeded)`，不发布输出；T036 的取消优先级在其上再加一层。

### 7. 等待与单一 resume（T035）

- `run_waitings` 保存等待种类/checkpoint/决定/退出确认；同事务 run.status 事件。决定与退出任意顺序
  只由一个 `maybeEnqueueResume` 事务生成**恰好一个** `jobs(kind=resume)`；resume payload 密封为
  `resume_input` artifact，经 Lease 的 resume_url 下发；恢复前重新检查 owner 未禁用、预算/截止、
  未取消。旧 job/token/配额幂等收尾；等待释放执行槽但 Run 仍占逻辑会话槽。
- 真实审批属 F06、真实子应用属 F07；本批的内部 resolution 端点是显式合成桥。

### 8. 取消与 unknown（T036）

- `cancelRun` owner-only、幂等、202；终态不再变更；无 live attempt 直接 cancelled，有 live attempt
  先 cancelling（cancel 意图 first-write），等退出证据后 cancelled。cancelled 只在无 live 子任务、
  无未解决 unknown 时提交；unknown 存在 → needs_reconciliation + `error_code=unknown_operations`。
- 优先级固定：live child > unresolved unknown > 已达截止 > cancel 意图 > 未提交结果。终态后迟到
  result/exit 一律拒绝，不复活。
- 真实 operations/审批属 F06；本批的 unknown 记录/解决端点与表是最小护栏。

### 9. 工具构建、审核与撤销（T037）

- `POST /tools` 校验作者本人 ready 的 `tool_source`、JCS manifest hash、固定 lock/SBOM；一个事务
  内建 `tool_versions(state=submitted)` + 一个 `tool_build` job。构建在同样的 runsc 根文件系统里，
  无网络、无宿主目录、无平台密钥；恶意安装钩子被隔离，日志不含凭据。
- 只有完成守卫能写 ready：CAS revision/source hash/artifact，源变更/撤销后旧构建永不 ready；
  `image_digest` 是**内容 bundle digest**（源/锁/SBOM 的 `{role,sha256}` 规范化 JCS 数组的 sha256；
  role 标签保证角色互换必然改变摘要），不是 OCI 镜像；真实镜像属 F15A，报告如实记录。
- 审核是管理员、绑定 `manifest_sha256`，ready ≠ approved；未批准 ready 工具不能用于共享/发布
  （T025 规则），作者私有 debug 仍可用；撤销保留历史并让后续依赖校验得到 `dependency_revoked`。

### 10. 保守恢复（T038）

- 租约过期只 park（`recovery_pending`/`lease_expired`），job held、allocation 保留、token 不变；
  park 的同事务结算活跃时间到最后可证明的活动终点（`lease_deadline`），不把 park 窗口计入
  T034 累计；claim 只选 queued，因此不可能自动第二执行。退出无结果 park 记
  `exit_without_result`。
- `cmd/admin runs recover` 要求受审计证据：执行单元匹配、旧心跳超过 grace、`pg_stat_activity` 中
  记录的 checkpoint 连接全部消失、冻结 manifest 经 `VerifyManifestBytes` 且输入 artifact 仍
  ready/未删；任一不满足即拒绝（`old_execution_still_live`/`database_writes_in_flight`/
  `artifact_unavailable`/`input_deleted`/hash 不符）。通过后一个事务记录证据、终结旧 attempt、
  requeue 恰好一个 job、Run 回 queued；重复命令不产生第二个 attempt，且因为 park 已结算，恢复
  不再补记已停机的窗口。`resumed_job_id` 不是唯一约束：后续恢复周期复用同一 job 时，每行仍
  记录它 requeue 的 job，once-per-attempt 由 `UNIQUE(run_id, attempt_token)` 保证。恢复是 CLI
  运维面，不新增公共 REST；状态页只展示原因/时间/证据，不提供伪造的恢复按钮。

### 11. 非契约扩展与状态口径

- 所有契约缺口都以“非契约扩展 + 报告/ADR 记录”处理：`X-Manifest-SHA256`（T027 起）、
  `X-Artifact-Quarantine-Reason`、`X-Run-*` 诊断头、`tool-runs`/`tool revoke`/`waiting-resolution`/
  `unknown-operations` 桥、`tool_run` kind。没有修改 `contracts/openapi.json`、`contracts/database.sql`
  或 `docs/spec/**`；`sync_spec.py --check` 全程 no drift。
- 本批的 `passed` 只覆盖票面范围；票面中属于 F04A/F06/F07/F13A/F15A/F15B 的部分在报告中逐条
  `not_run`/`partial`/successor，不把沙箱、组件或静态校验升级为生产验收。

## 后果

- 现在可以：上传→扫描/隔离→ready；提交/排队 debug Run→supervisor 领取/租约/心跳→结果提议→退出
  证据→终态；持久事件/SSE 回放；固定预置工具在 runsc 沙箱执行并登记 run-bound 产物；等待/单一
  resume；取消/unknown 护栏；工具提交/隔离构建/管理员审核/撤销；租约到期保守 park 与受审计恢复。
- 仍不能：运行任意 LangGraph 图（F04A）、真实审批/模型/连接器/operations（F06）、真实子应用/
  requests/conversations/删除（F07）、真实 OCI 镜像（F15A）、生产部署/灾备（F15B）。
- 后续票不得重新引入第二 finalizer、第二 settlement、token 换发自动接管、stdout 解析结果或
  未经守卫的 artifact 激活；恢复/取消/等待都必须走已固定的单一事务入口。

## 证据

- 迁移：`product/db/migrations/0015_*.sql` … `0024_*.sql`
- 代码：`internal/{artifacts,execution,background,background/handlers,sandbox,presetexec,tools,
  toolversions,catalog,api}`、`cmd/{admin,supervisor,sandboxprobe,maintenance}`、`worker/`、
  `deploy/sandbox/`、`apps/web/src/`
- 测试：`tests/f05/`（上传/工具/沙箱/构建）、`tests/f03/`（领取/结果/事件/活跃时间/等待/取消/恢复）、
  `tests/support/{t028,t029,t030,t031,t032,t033,t034,t035,t036,t037,t038}.py`
- 报告：`product/reports/f05/T0{28,30,33,37}-*.md`、`product/reports/f03/T0{29,31,32,34,35,36,38}-*.md`
- 提交：本分支 `feat/f05-f03-run-and-tools`；`docs/spec/**` 与 `contracts/**` 未修改
