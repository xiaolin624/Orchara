# 产品实现进度（权威）

本文件是平台实现的进度权威。规格仓库里的 `docs/spec/PROGRESS.md` 只保留接手时的初始快照。

状态取值：`not_started` / `in_progress` / `blocked` / `passed` / `failed` / `not_run`。
本分支另用 `partial` 表示“本票范围内的工作已真实交付并验证，但票面验收仍有明确未闭环项，且已指定承接票”；
`partial` 不等于 `passed`，未运行项必须同时写 `not_run`。`passed` 必须满足该票在 `contracts/task-graph.json`
的完成条件；缺外部条件写 `blocked`，未执行写 `not_run`。**静态校验通过、探测到硬件、stub 成功都不升级为 passed。**

## 当前发布范围

本轮已交付范围：**D00 + P01–P05 + F00 + F01**，以及本分支的 **F00B（T022/T024 passed；
T026 partial）**、**F02A（T023/T025 partial；T027 passed）**、**F05（T028、T030、T033、T037
passed）**、**F03（T029、T031、T032、T034、T035、T036、T038 passed）** 与
**F15A（T057 passed；#54 的 S3 探针不在本分支）**；其余票保持 open。
**F06（T039–T051）与 F04A（T040、T042、T043、T049）已按 #40→#52 顺序交付，全部 13 个 issue
关闭**；证据见各票报告、ADR 0007 与下方票表。批次最终门禁（2026-11-04，分支
`feat/f06-f04a-models-and-workflow`，HEAD `b9147a8`）：`pytest tests -q` **1323 passed in
4051.57s（67m31s）**，`go vet ./...`/`go test ./...`/`gofmt -l`/`sync_spec --check` 全绿；两轴
复核的 10 个迁移链测试、stream manifest 绕过、connector 前缀边界、unknown replay 披露、approval
等待错误吞掉、T042 cancel 测试与迁移约定均已修复（`t/review-fixes`，merge `b9147a8`）。
第二轮独立复核（2026-11-04，HEAD `c6618d3`，merge `t/review2-fixes`）又确认并修复 5 个 P1：
connector 重复 query 参数绕过 schema、登记的 minimum/maximum/minLength/maxLength 运行时不执行、
外部写成功但回执保存失败被记为 failed、批准过期后仍可执行、输出校验失败释放真实模型费用；
每项都有回归测试（f06 共 173 个：全量跑 172 passed + 1 个既有 WSL sandbox 瞬时 flake，隔离复跑
通过；T036 21 passed、tooling 21 passed、go test/vet、gofmt、sync_spec 全绿）。
F01（T015/T017/T019/T020/T021）已交付身份基础：迁移与管理命令、邀请/激活/登录/登出、
Cookie 会话与 CSRF、默认拒绝的资源 ACL、服务账号与可撤销 API Key、禁用与责任交接。
F00B 的 T022 已交付持久后台任务控制器（迁移 0009、对象存储、claim/租约/重试/完成守卫、
内部 mTLS API、maintenance 处理器循环、admin enqueue、GET job）；T024 已交付 job 限定文件访问
与受控模型调用（迁移 0011、HMAC 签名对象链接、5 个内部端点、`internal/modelproxy` 与
`model_validate` handler、`platform_model_calls` 记账；复核修复后强制合成夹具标记）。
F00B 的 T026 为 `partial`（合成 runner/application-kind 属 F13A，deletion 状态属 F07）。
F02A 的 T023 已交付草稿 CAS/版本读模型/编辑器（AC3 仍 `partial`：submit 拒绝属 F02B）；T025 已交付
执行清单固化、依赖闭包与 `POST /applications/{id}/validate`（迁移 0012、`internal/compiler`、
`manifest_artifacts` 去重、golden 规范字节、`VerifyManifestBytes` 机制；AC3 `partial`：真正
worker 属 F03/F04A）；T027 已交付私有 debug 快照、排队 Run、`debugDraft`/`getRun` 与幂等提交
（迁移 0014、`execution_snapshots`/`runs`/`jobs`/`run_artifacts`/`idempotency_keys`）。F02A 三票
范围内的工作完成，但 T023/T025/T026 的 `partial` 原因分别由 F02B/F03-F04A/F13A-F07 闭环；
执行/领取属 F03、发布提交属 F02B，限制见各票报告。
F03 的 T029 已交付执行领取的首个切片（迁移 0016、`internal/execution`、内部 claim/heartbeat/
attempt-exit、owner-only `GET /runs` 与运行页面）：真实 mTLS + PG 原子领取/配额/hmac sealed 链接；
result/finalizer/事件/恢复/取消属 T031+。T031 在 T029 之上交付 result 提议与统一幂等 finalizer：
同一 attempt 的 result 与 supervisor 身份化 exit 证据按任意顺序到达都只提交一次终态
（succeeded/failed）、退出无结果转 recovery_pending、waiting/needs_reconciliation 如实非终态。
T032 再把每个已提交 Run 转换投影为 `platform.run_events` 的持久 `run.status`（同事务 seq 分配）、
提供 worker `node.status` 批量事件端点与 owner-only SSE 回放（Last-Event-ID 去重、保留窗口 410+快照、
15s 注释心跳、终态后关闭）；取消与未知操作守卫由 T036 交付，安全恢复/重驱属 T038。F05 的 T030 在此之上交付可信预置销售统计工具：迁移 0017
只把 `tool_run` 加入后台任务 kind 白名单；真实维护进程用固定 argv 的 Python 子进程执行固定脚本，
Decimal 统计 CSV/XLSX，产物与诊断经 T028 的 artifact 路径封存；公开 `POST/GET /api/v1/tool-runs`
是 ADR 0005 记录的非契约桥，T033/F04A 再绑定到 Run/沙箱协议。平台整体**仍不是完整 v1**：业务执行/
路由/评测/RAG/记忆/审批/编译器/画布/发布门禁/部署灾备等后续 epic 未实现；F01/F00B/F02A/F05/F03
的限制见各票报告 §5/§7。
本文件 2026-11-02 的两轴复核修复记录见下方“F00B/F02A 两轴复核修复”一节，第二次独立复核的 6 项
缺陷 + 1 个 flaky 测试见“F00B/F02A 第二次复核修复”一节；状态以本节之后的表格为准。

## 票状态

| 票 | 父 | 标题 | 状态 | 证据 |
|---|---|---|---|---|
| T001 | D00 | 在产品工作区复现规格并生成环境报告 | passed | `reports/p0/D00-environment-report.md`、`reports/p0/spec-*.txt`、`tools/probe_environment.py` |
| T002 | P01 | 从锁定依赖运行一个只有授权工具的 Agent | passed | `reports/p01/T002-agent-min-probe.md`、`probes/agent_min/`（锁 48 包 + 探针） |
| T003 | P01 | 让 Go/Python 对同一清单给出相同指纹 | passed | `reports/p01/T003-jcs-parity.md`、`reports/p01/jcs-agreement.json`（40 向量 0 不一致） |
| T004 | P01 | 在第二个干净环境重建核心探针 | passed | `reports/p01/T004-second-environment.md`（WSL Linux 重建 + 跨平台指纹一致） |
| T005 | P02 | 让一个中断任务在新进程中安全继续 | passed | `reports/p02/T005-checkpoint-resume.md`（真实 PG，7 passed） |
| T009 | P02 | 升级后恢复旧图或明确报告缺制品 | passed | `reports/p02/T009-artifact-pinning.md`、`db/migrations/0002_artifacts.sql`（11 passed） |
| T006 | P03 | 启动一个拒绝危险配置的 Linux 沙箱探针 | passed | `reports/p03/T006-sandbox-probe.md`（WSL2 + runsc 20241118.0，15 passed） |
| T010 | P03 | 证明脚本无法读越权文件或绕过网络代理 | passed | `reports/p03/T010-isolation-matrix.md`（最小 OCI bundle，17 passed） |
| T014 | P03 | 停止超限脚本并证明资源已回收 | passed | `reports/p03/T014-resource-limits.md`（24 passed；OCI 内存上限未生效，改用 ulimit） |
| T013 | P04 | 模拟租约失联而不启动第二个执行者 | passed | `reports/p04/T013-lease-loss.md`、`db/migrations/0004_fence_tokens.sql`（14 passed，p04 共 30） |
| T016 | P04 | 等待旧进程、沙箱和数据库写全部排空再恢复 | passed | `reports/p04/T016-drain-gate.md`（9 passed，含真机集成） |
| T018 | P04 | 写成功丢回执后恢复仍只写一次 | passed | `reports/p04/T018-lost-receipt.md`（16 passed，账本恰好 1 条） |
| T007 | P05 | 用同一 corpus 校验两语言的变量和类型 | passed | `reports/p05/T007-dsl-parity.md`、`probes/p05_dsl/`（97 新测试，p05 共 105） |
| T011 | P05 | 在真实图引擎中验证分支和汇合结果 | passed | `reports/p05/T011-graph-branching.md`（8 passed，LangGraph 实跑） |
| T008 | F00 | 启动产品并在浏览器看到真实健康状态 | passed | `reports/f00/T008-product-skeleton.md`、`cmd/api/`、`internal/health/`（4 passed） |
| T012 | F00 | 按环境 profile 启动并重现一次 CI 验收 | passed | `reports/f00/T012-profile-and-ci.md`、`internal/config/`、`tools/ci_checks.py`（15 passed） |
| T015 | F01 | 从空数据库建立管理员并邀请激活用户 | passed | `reports/f01/T015-identity-bootstrap.md`、`db/migrations/0006_identity_core.sql`、`internal/db/migrate.go`、`cmd/admin/`、`internal/identity/`、`internal/api/`、`tests/f01/`（37 passed） |
| T017 | F01 | 用户登录、刷新身份并安全登出 | passed | `reports/f01/T017-login-and-csrf.md`、`internal/identity/csrf.go`、`internal/api/authz.go`、`internal/api/loginlimit.go`、`internal/api/server.go`、`apps/web/src/login.html`、`apps/web/src/assets/app.js`、`tests/f01/test_t017_*.py`（63 passed，真实 PG + 真实二进制 + 真实 HTTP） |
| T019 | F01 | 管理员授予或撤销某个用户的资源访问 | passed | `reports/f01/T019-resource-grants.md`、`db/migrations/0007_authorization.sql`、`internal/policy/`、`internal/catalog/`、`internal/audit/`、`internal/identity/roles.go`、`internal/api/{grants,roles,applications,server}.go`、`apps/web/src/apps.html`、`apps/web/src/admin.html`、`tests/f01/test_t019_*.py`（`tests/f01` 共 94 passed，真实 PG + 真实二进制 + 真实 HTTP） |
| T020 | F01 | 创建服务账号并用可撤销 Key 调用授权 API | passed | `reports/f01/T020-service-accounts.md`、`db/migrations/0008_api_keys.sql`、`internal/identity/apikeys.go`、`internal/api/{apikeys,authz,applications,server}.go`、`apps/web/src/admin.html`、`tests/f01/t020_stack.py`、`tests/f01/test_t020_*.py`（`tests/f01` 共 124 passed，真实 PG + 真实二进制 + 真实 HTTP） |
| T021 | F01 | 禁用人员并安全交接未完成待办 | passed | `reports/f01/T021-disable-and-handover.md`、`internal/identity/{disable,handover}.go`、`internal/api/{principals,server}.go`、`cmd/admin/main.go`、`apps/web/src/admin.html`、`tests/f01/test_t021_*.py`（`tests/f01` 共 142 passed，真实 PG + 真实二进制 + 真实 HTTP） |
| T022 | F00B | 提交一个后台任务并在重启后得到唯一结果 | passed | `reports/f00b/T022-background-jobs.md`、`db/migrations/0009_background_jobs.sql`、`internal/objectstore/`、`internal/background/`（含 handlers、`Notifier` 唤醒缝、尝试对象栅栏）、`internal/api/{internal,background}.go`、`cmd/api/main.go`、`cmd/maintenance/{main,client,run}.go`、`cmd/admin/{main,background}.go`、`tests/support/`、`tests/f00b/`（60 passed，真实 PG + 真实二进制 + 真实 HTTP/mTLS；复核修复后新增丢唤醒实测，第二次复核新增尝试栅栏与 lease 恢复测试确定性，Redis adapter 仍 not_run） |
| T023 | F02A | 创作者编辑用途说明和草稿并处理保存冲突 | **partial** | `reports/f02a/T023-drafts-and-editor.md`（§2 AC3/§8：validate T025 + debug T027 的拒绝是真实的；`POST /submit` 的 404 只证明 F02B 端点缺失，不能当拒绝证据）、`db/migrations/0010_application_drafts.sql`、`internal/catalog/{draft,versions}.go`、`internal/api/{drafts,versions}.go`、`apps/web/src/{editor.html,assets/editor.js}`、`tests/f02a/`（T023 45 用例；第二次复核新增 `test_t023_creator_role.py` 4 用例，create/save 现要求会话 creator 角色） |
| T024 | F00B | 后台评测只读取获准文件并记账模型调用 | passed | `reports/f00b/T024-job-files-and-models.md`、`db/migrations/0011_job_files_and_models.sql`、`internal/objectstore/sign.go`、`internal/background/{attempt,artifacts,scope}.go`、`internal/modelproxy/`、`internal/api/{jobdata,links}.go`、`internal/background/handlers/model_validate.go`、`cmd/maintenance/scope.go`、`tests/support/t024.py`、`tests/f00b/test_t024_*.py`（`tests/f00b` 86 passed；复核修复后 T024 28 用例：强制 `purpose='model_fixture'` 的 CLI/model-calls 双端拒绝；第二次复核后 replay 返回记录的原结果） |
| T025 | F02A | 把当前草稿固化为可核验执行清单 | **partial** | `reports/f02a/T025-manifest.md`（AC3 `partial`：`VerifyManifestBytes` 机制 + 真实 sealed-object 篡改测试已交付，真正 worker 属 F03/F04A）、`db/migrations/0012_manifest_and_tool_versions.sql`、`internal/compiler/`、`internal/catalog/manifest.go`、`internal/api/validate.go`、`internal/config/config.go`、`tests/contracts/snapshots/`、`tests/support/t025.py`、`tests/f02a/test_t025_*.py`（T025 17 用例，真实 PG + 真实二进制 + 真实 HTTP + 对象字节；第二次复核新增依赖 UUID 大小写归一） |
| T026 | F00B | 评测池满载时文件封存仍能完成 | partial | `reports/f00b/T026-evaluation-pool.md`、`db/migrations/0013_evaluation_pool.sql`、`internal/background/{evaluation,complete,sweep}.go`、`cmd/maintenance/{run,sweep}.go`、`tests/f00b/test_t026_*.py`（41 用例；`kind=application` 的真实 runner/checker 属 F13A，T027 后 snapshot 绑定结构可用；**deletion 状态属 F07、未实现**；第二次复核后 sweep 只清 staging、sealed 孤儿清理延后 F07/F13A，租约耗尽的评测终态与 job 同事务对账；三条 partial 原因见报告页首） |
| T027 | F02A | 发布前提交私有草稿并查看固定调试任务 | passed | `reports/f02a/T027-debug-submit.md`、`db/migrations/0014_execution_snapshots_and_runs.sql`、`internal/catalog/{debug.go,debug_test.go}`、`internal/api/debug.go`、`apps/web/src/{debug.html,assets/debug.js}`、`tests/support/t027.py`、`tests/f02a/test_t027_*.py`（38 用例，真实 PG + 真实二进制 + 真实 HTTP + 对象字节；领取/执行属 F03；第二次复核后 debug 要求会话 creator 角色，无角色管理员 403） |
| T028 | F05 | 上传、校验封存并授权下载不可覆盖的文件 | passed | `reports/f05/T028-artifact-uploads.md`、`db/migrations/0015_artifact_admissions.sql`、`internal/artifacts/`（服务 + 格式准入分类器）、`internal/api/{artifacts.go,server.go}`、`internal/background/handlers/artifact_finalize.go`、`apps/web/src/{attachments.html,assets/attachments.js}`、`tests/support/t028.py`、`tests/f05/test_t028_*.py`（6 文件 31 用例；真实 PG + 真实二进制 + 真实 HTTP + 真实签名 URL + 真实 maintenance finalize；删除模型/病毒查杀/深工作表解析分别属 F07/F13A，见报告 §5） |
| T029 | F03 | 提交运行并只让合格执行者在配额内领取 | passed | `reports/f03/T029-claim-and-lease.md`、`db/migrations/0016_execution_claims_and_quota.sql`、`internal/execution/`（store + service：claim/heartbeat/attempt-exit）、`internal/api/{execution.go,runs.go,server.go,internal.go,debug.go}`、`apps/web/src/{runs.html,assets/runs.js,debug.html}`、`tests/support/t029.py`、`tests/f03/test_t029_*.py`（9 文件 51 用例；真实 PG + 真实 mTLS/HTTP + 真实对象字节 + 真实并发；result/finalizer/事件/恢复/取消属 T031–T038，发布版 Run 入口属 F02B/F05，见报告 §4/§5） |
| T030 | F05 | 用预置工具对销售表格返回统计或具体输入错误 | passed | `reports/f05/T030-sales-tool.md`、`db/migrations/0017_tool_run_kind.sql`（只扩展 kind CHECK）、`internal/tools/`（固定 argv/只读输入/超时/严格 output.json/产物准入）、`internal/toolruns/`（幂等创建 + owner-only 读）、`internal/api/toolruns.go`、`internal/background/handlers/tool_run.go`（summary/CSV/diagnostics/log + 完成守卫）、`worker/platform_worker/nodes/sales_stats.py`（stdlib-only Decimal CSV/XLSX）、`apps/web/src/{attachments.html,assets/attachments.js}`（工具区/统计表/下载/诊断）、`tests/support/t030.py`、`tests/f05/test_t030_*.py`（6 文件 55 用例，含 36 个 Python 工具子进程用例；真实 PG + 二进制 + HTTP + 签名 URL + maintenance 双池 + 真实 Python 子进程；非契约桥见 ADR 0005；沙箱/结果协议/工具构建分别属 T033/T031/T037，见报告 §6） |
| T031 | F03 | 运行完成后等待真实退出证据再展示终态 | passed | `reports/f03/T031-result-and-finalizer.md`、`internal/execution/`（result 提议 + 身份化 exit 证据 + 统一幂等 finalizer + children guard 扩展点）、`internal/api/execution.go`、`apps/web/src/{runs.html,assets/runs.js,debug.html,assets/debug.js}`、`tests/support/{t029.py,t031.py}`、`tests/f03/test_t031_*.py`（4 文件 17 用例；T029 exit 用例按新必填证据收紧）；无迁移（`reported_result` 已含 event_id）；真实 PG + 真实 mTLS/HTTP + 真实对象字节；事件/SSE/tools/cancel/recovery 属 T032–T038，见报告 §5/§7 |
| T032 | F03 | 断开事件流后从持久时间线继续查看运行 | passed | `reports/f03/T032-run-events.md`、`db/migrations/0018_run_events.sql`、`internal/execution/events.go`（同事务 run.status 投影 + 同事务 seq 分配 + worker node.status 批量提交）、`internal/execution/{service.go,execution.go}`、`internal/api/events.go`（submitWorkerEvents + SSE）、`internal/api/{server.go,internal.go,middleware.go}`、`internal/config/config.go`、`catalog/debug.go`、`apps/web/src/{runs.html,assets/runs.js,debug.html,assets/debug.js}`、`tests/support/t032.py`、`tests/f03/test_t032_*.py`（4 文件 31 用例；真实 PG + 编译 API + 真实 mTLS + 真实 SSE 连接/断开重连 + Node 执行页面逻辑）；retention 默认 24h（`ORCHARA_RUN_EVENT_RETENTION`），410 `event_cursor_expired` + `details.snapshot_url`；Delta 预览/真实 worker/沙箱/取消/恢复属 F06/T033/T036/T038，见报告 §5/§7 |
| T033 | F05 | 在正式沙箱调试工具并登记可授权下载的产物 | passed | `reports/f05/T033-sandbox-tool-debug.md`、`db/migrations/0019_run_artifact_uploads.sql`（唯一新表：artifact PK + run/attempt FK + 契约 purpose 枚举 + 两索引；无删除模型）、`internal/execution/runartifacts.go`（五个 run-scoped 操作：upload/complete/download/status/register；live attempt + 当前 token 唯一凭据；provenance 决定 scope；complete→CAS scanning + 一个 `artifact_finalize`）、`internal/catalog/runartifacts.go`（owner-only `listRunArtifacts`，4 字段 + 共享 cursor，log/输入不列出）、`internal/api/runartifacts.go`、`internal/execution/{store,service}.go`（live lease 的 execution_unit_id、cancel 读取）、`internal/sandbox/*`（Go 构建 OCI bundle：只读 rootfs、uid 65534、无 caps/no_new_privs、pid/net/ipc/uts/mount ns、--network=none、内存/pid/CPU/timeout、/proc+tmpfs、/tool+/inputs 只读、/workspace 可写、固定 argv；进程组 kill+cleanup 验证；digest 身份；fake-runner 单测）、`deploy/sandbox/{build_python_rootfs.sh,sandbox_exec.sh,isolation_probe.py}`（WSL/runsc driver + 可重放 rootfs 构建，digest `sha256:03c3f6…2ff5`）、`internal/presetexec/`（claim→manifest 字节校验→唯一 python 节点/唯一 task_input→签名下载→沙箱→scoped 上传/登记→result→exit；确定拒绝用 started=true+sandbox_started=false；取消不提交结果停 recovery_pending）、`cmd/supervisor/{main,exec,client}.go`（`exec --once`，supervisor+worker 双证书、只读 DSN）、`cmd/sandboxprobe/`、`apps/web/src/{runs.html,assets/runs.js}`（沙箱面板：执行单元/租约/状态/退出/停止 vs 停止中/T036 诚实说明/产物下载）、`tests/support/{t033.py,fixtures.py}`、`tests/f05/test_t033_*.py`（4 文件 20 用例：迁移 6、端点矩阵 6、真实 runsc e2e/失败/取消/隔离/超时/崩溃 5、页面 3）+ 迁移链 0018→0019；真实 PG + 编译二进制 + 真实 mTLS（三角色）+ WSL2/runsc + Node；`go test ./...` 全绿（新增 27 个 Go 单测）、`go vet`/`gofmt -l` 干净、`sync_spec --check` no drift；T036 取消端点、T037 工具构建、F04A 进程拆分与恢复/T038 属后续票，见报告 §2/§5/§6 |
| T034 | F03 | 跨尝试累计活跃耗时并在超时时停止 | passed | `reports/f03/T034-active-time.md`、`db/migrations/0020_active_time_and_deadline.sql`（`runs.max_active_ms` + `run_attempts.active_ms/active_settled_at` + policy 回填 + 两个 CHECK；无新表/权限）、`internal/execution/active.go`（唯一结算/投影/截止 helper：`settleAttemptActiveTx`、`deadlineStateOf`、`checkDeadlineTx`、`markDeadlineIntentTx`）、`internal/execution/{store,service,execution}.go`（claim 拒绝已超时 queued Run；heartbeat/result/exit/finalizer 结算；到期记录 intent 一次并复用 T031 `finalizeRunTx` 提交 `failed(deadline_exceeded)`、无 output、slot 一次释放；`ErrDeadlineExceeded` 409）、`internal/catalog/debug.go`（debug Run 创建时从冻结 policy 写 max，不猜）、`internal/api/{debug,execution}.go`（非契约 `X-Run-Active-Elapsed-Ms`/`X-Run-Max-Active-Ms`/`X-Run-Deadline-Exceeded`、409 映射）、`apps/web/src/{runs.html,assets/runs.js}`（累计活跃/上限、一个时钟、deadline 原因、NULL≠0）、`tests/support/t034.py`、`tests/f03/test_t034_*.py`（4 文件 18 用例：迁移 5、累计/幂等/等待 4、截止 5、页面 4）+ 迁移链 0019→0020 与两处先前票陈旧断言适配；真实 PG + 编译二进制 + 真实 mTLS/HTTP + 对象字节 + Node；`tests/f03` 117 passed、`tests/f05` 106 passed 回归；`go test ./...` 全绿、`go vet`/`gofmt -l` 干净、`sync_spec --check` no drift；T035 waiting/resume、T036 cancel/优先级、T038 恢复与到期后能力/注册门禁属后续票，见报告 §2/§5/§6 |
| T035 | F03 | 等待审批或子任务时只恢复原任务一次 | passed | `reports/f03/T035-waiting-resume.md`、`db/migrations/0021_run_waitings.sql`（唯一新表 `platform.run_waitings`：run_id PK、kind/checkpoint、安全 decision jsonb + decision_at、exit_confirmed_at、UNIQUE resume_job_id、resume_artifact_id、created/updated + 闭合 CHECK；无 approval/child/operation 表，worker/PUBLIC 零权限）、`internal/execution/waiting.go`（waiting 转换 + 合成 resolution 校验 + `maybeEnqueueResumeTx` 唯一恢复事务 + owner-facing facts）、`internal/execution/waiting_store.go`（waiting/决定/退出/恢复 job/失败收尾 SQL）、`internal/execution/service.go`（result 的 waiting 分支、exit 的 waiting 分支、waiting 重放、deadline/权限/预算重查后 `failed(...)`）、`internal/api/{execution,internal,debug}.go`（supervisor mTLS `POST /internal/v1/runs/{id}/waiting-resolution` + 六个非契约 waiting/resume 头）、`apps/web/src/{runs.html,assets/runs.js}`（等待 kind/结果/退出确认/唯一恢复任务/逻辑会话槽仍占用；无恢复输入内容）、`tests/support/t035.py`、`tests/f03/test_t035_*.py`（5 文件 24 用例：waiting 4、resume 5、refusal 4、迁移 7、页面 4）；真实 PG + 编译二进制 + 真实 mTLS/HTTP + 真实对象字节 + Node；迁移链 0020→0021 适配 10 个迁移测试；`tests/f03` 141 passed；`go test ./...` 全绿、`go vet`/`gofmt -l` 干净、`sync_spec --check` no drift；真实 F06 审批/F07 子应用接入、T036 取消终态、T038 恢复证据属后续票，见报告 §2/§5/§6 |
| T036 | F03 | 取消正在运行的执行并让迟到消息不能复活终态 | passed | `reports/f03/T036-cancel.md`、`db/migrations/0022_run_unknown_operations.sql`（唯一新表 `platform.run_unknown_operations`：run/operation_id/reason/resolved_at + `UNIQUE(run_id,operation_id)` + 未解决 partial index；`run_waitings.cancel_requested_at`；无 `runs.cancel_reason/cancelled_by`，worker/PUBLIC 零权限）、`internal/execution/cancel.go`（owner-only `Cancel` 两分支：无 live attempt 立即 cancelled/unknown→needs_reconciliation；有 live attempt 记录 intent→cancelling；unknown record/resolve bridge）、`internal/execution/cancel_store.go`（cancel/job/waiting/ledger SQL）、`internal/execution/service.go`（`finalizeRunTx` 单一优先级：live child 阻塞 > unknown→needs_reconciliation > deadline→failed(deadline_exceeded) > cancel→cancelled > proposal；started=false cancel 走 job void）、`internal/execution/{store,waiting,waiting_store,runartifacts}.go`（unknown guard 查询、waiting cancel marker、receipt/query 在 cancelling 仍可用）、`internal/api/{cancel.go,execution.go,server.go,internal.go}`（public `POST /api/v1/runs/{id}/cancel` + 两个 internal unknown bridge）、`apps/web/src/{runs.html,assets/runs.js}`（取消按钮/正在停止 vs 已停止/终态原因/终态不回翻）、`tests/support/t036.py`、`tests/f03/test_t036_*.py`（6 文件 29 用例：cancel 14、unknown 6、migration 5、pages 4，真实 PG + 编译 API + 真实 mTLS + 对象字节 + Node）；迁移链 0021→0022 适配 11 个测试，T033 的取消 interim 断言由 T036 终态取代；`tests/f03` 170 passed；`go test ./...` 全绿、`go vet`/`gofmt -l` 干净、`sync_spec --check` no drift；T038 恢复/核对重驱、F06 真实 operations 属后续票，见报告 §2/§5/§6 |
| T037 | F05 | 提交脚本依赖并审核成可共享固定工具 | passed | `reports/f05/T037-tool-builds.md`、`db/migrations/0023_tool_builds_and_review.sql`（`tool_versions` 扩展：revision/source_sha256/dependency_lock_sha256/build_log/sbom/build_bundle_digest/build_started_finished/build_error_code + reviewed_*/revoked_*；契约列/状态枚举保留；worker/PUBLIC 零权限；非契约扩展见报告 §4）、`internal/toolversions/service.go`（submit 单事务/幂等/跨 owner `tool_conflict`、JCS `manifest_sha256`、owner-or-admin-or-approved 读、review/revoke CAS+audit、job-scoped build binding）、`internal/api/tools.go`（`submitTool`/`listTools`/`getTool`/`reviewTool` + 非契约 `POST /tools/{id}/revoke`、`GET /tools/{id}/build`、内部 `POST /internal/v1/background/jobs/{job_id}/tool-build`）、`internal/background/{toolbuild.go,handlers/tool_build.go}`（claim submitted→building；完成守卫 CAS revision+source hash+ready/job-linked log+SBOM，唯一 ready 写入者；内容包摘要 `sha256:<hex>`=sorted(source+canonical lock+SBOM) 非 OCI）、`internal/sandbox/*` + `deploy/sandbox/{sandbox_exec.sh,build_tool.py}`（复用 T033 runsc bundle：固定 argv/无网络/uid 65534/只读 rootfs+inputs/可写 workspace；安全解包 + `setup.py build` 钩子；lock 需不可用 wheelhouse 时安全 `dependencies_unavailable`）、`internal/artifacts/admission.go`（`application/zip` 仅 `purpose=tool_source`：entry/解压/ratio/traversal/symlink/encrypted 拒绝；其他 purpose 仍拒绝）、`apps/web/src/{tools.html,assets/tools.js}`（提交/构建日志+SBOM 下载/审核状态 + 管理员审核队列，Node 执行逻辑）、`tests/support/{t037.py,fixtures.py}`、`tests/f05/test_t037_*.py`（8 文件 39 用例：迁移 6、submit 11、admission 4、真实 runsc 构建 2、恶意钩子/崩溃/依赖/源变更 4、审核/撤销 8、internal binding 1、页面 3；真实 PG + 编译二进制 + 真实 mTLS + WSL2/runsc + Node）；`tests/f05` **145 passed in 611.79s**；`go test ./...` 全绿、`go vet`/`gofmt -l` 干净、`sync_spec --check` no drift；F06 connectors、F15A 镜像构建、F04A 进程拆分属后续票，见报告 §5/§6 |
| T038 | F03 | 恢复待证据并在核对后安全重驱 | passed | `reports/f03/T038-recovery.md`、`db/migrations/0024_run_recovery_evidence.sql`（唯一新表 `platform.run_recovery_evidence`：run/attempt、闭合证据 jsonb + JCS sha256、真实 principal `recorded_by`、`recorded_at`、UNIQUE `resumed_job_id`、`UNIQUE(run_id,attempt_token)`、run 索引；`runs.recovery_reason/recovery_since` + 配对/闭合 CHECK；worker/PUBLIC 零权限；重复 migrate no-op）、`internal/execution/recovery.go`（证据闭合形状 + JCS SHA-256、`SweepExpiredLeases` 只 park、`Recover` 全检查 + 单事务重排、`RecoveryFacts`、拒绝码）、`internal/execution/recovery_store.go`（sweep/recover/actor/evidence SQL + 行锁复检）、`internal/execution/{store,service,cancel_store}.go`（T031 park 写 `exit_without_result`；离开 recovery_pending 时清列）、`cmd/admin/runs.go` + `cmd/admin/main.go`（`runs sweep-leases [--grace] [--dry-run]` / `runs recover --run --evidence [--check-only] [--actor-login] [--grace]`，stdout 单 JSON，exit 0/1/2，绝不打印 token/DSN）、`internal/api/debug.go`（非契约 `X-Run-Recovery-Reason/Since/Evidence-At/Evidence-SHA256`）、`apps/web/src/{runs.html,assets/runs.js}`（恢复原因/挂起时间/证据/运维命令，无恢复按钮，终态不回翻，`runLifecycle` 增加 recovery_pending）、`tests/support/t038.py`、`tests/f03/test_t038_{migration,sweep,recover,recovery_refusals,pages}.py`（5 文件 27 用例：migration 5、sweep 5、recover 7、refusals 6、pages 4；真实 PG + 编译 admin/API + 真实 mTLS + 真实对象字节 + 真实打开中的 psql 事务/pg_stat_activity + Node）；迁移链 0023→0024 适配 12 个迁移测试；`tests/f03` **197 passed**；`go test ./...` 22 ok/9 no-test/0 FAIL、`go vet`/`gofmt -l` 干净、`sync_spec --check` no drift；not_run：常驻租约巡检、cancelling/waiting_* 失联恢复、真实 F04A supervisor 证据、浏览器 DOM、25 分钟多套件门禁（orchestrator），见报告 §5 |

| T039 | F06 | 管理员登记模型并查看兼容验证后才共享 | passed | `reports/f06/T039-model-registry.md`、`docs/adr/0007-f06-f04a-model-registry-boundaries.md`、`internal/modelregistry/{catalog,modelregistry,validate}.go`（deployer catalog 严格解析/fail closed、JCS manifest + sha256、单事务 manifest artifact + pending model、`FOR UPDATE` 验证编排、确定性 fixture 封存、Idempotency-Key 收据/回放）、`internal/api/{models.go,server.go}`（`registerModel`/`listModels`/`getModel`/`validateModel` + admin `model-endpoints`/`credential-refs` + `/models` 页面）、`internal/background/{model_validate.go,complete.go,evaluation.go}`（报告严格绑定 candidate/fixture/`platform_model_calls` 账本、`evaluation_reports` + model passed/failed 同事务；T024 直接入队 job 保持 report-only 语义）、`internal/modelproxy/{modelproxy.go}`（catalog id 与 `env:` 引用双形态）、`apps/web/src/{models.html,assets/models.js}`（登记/列表/验证/报告，Node 执行）、`tests/support/{t039.py,fixtures.py}`、`tests/f06/`（15 用例：注册/可见性/幂等/可重试失败/失败用例/终端失败/依赖就绪/页面）；真实 PG + 编译二进制 + 真实 HTTP/mTLS + 对象字节 + maintenance 双进程 + Node；`go test ./...` 23 ok/0 FAIL/9 no-test、`go vet` exit 0、`gofmt -l` 空、`sync_spec --check` no drift；not_run：真实厂商 provider adapter（本构建 real 失败为 `provider_not_configured`，已断言）、浏览器 DOM、全量 `pytest tests`（orchestrator）；无迁移（0011/0013 已够用） |
| T040 | F04A | 在画布配置线性流程并看到服务端校验反馈 | passed | `reports/f04a/T040-canvas-and-linear-compile.md`、`docs/adr/0007-f06-f04a-model-registry-boundaries.md`（T040 追加 D4/D5）、`worker/platform_worker/compiler/{validate,graph,stub,jcs,__init__}.py`（F04A 线性子集防御性校验 + 真实 LangGraph `StateGraph` + 确定性 stub adapter；共享语料 22 例 = 16 shared + 6 f04a_only）、`internal/workflow/f04a_corpus_test.go`（Go 权威对共享语料断言同 `(verdict, code, pointer)`）、`tests/f04a/corpus/linear_f04a.json`、`apps/web/canvas/`（Vue 3.5.44 + Vue Flow 1.48.2 真实应用；`build.mjs` esbuild 0.28.2；纯模块 `lib/{dsl,sources,errors,export}.js`）、`apps/web/src/assets/canvas.{js,css}`（提交的静态产物，测试/运行不需要 npm）、`apps/web/src/{editor.html,assets/editor.js}`（画布主编辑、四类节点配置面板、工具/模型选择器、合法变量来源、错误点击跳转、`X-Manifest-SHA256`、折叠 JSON 视图；保留 ETag/412/428/调试入口）、`internal/api/server.go`（`GET /assets/canvas.{js,css}`）、`tests/f04a/`（36 用例：validate 17、graph 7、assets 5、integration 7；Node 8 模块检查；真实 PG + 编译二进制 + 真实 HTTP + P01 langgraph 1.2.4）、`go vet` exit 0、`go test ./...` 23 ok/9 no-test/0 FAIL、`gofmt -l` 空、`pytest tests/f04a tests/f02a` **140 passed**、`tests/tooling` 21、`sync_spec --check` no drift；not_run：浏览器 DOM、生产 LangGraph worker（Go runtime 属 T042/T043）、全量 `pytest tests`（orchestrator）；无迁移/契约改动 |
| T057 | F15A | 在干净开发环境启动并跑通销售冒烟 | passed | `reports/f15a/T057-clean-env-smoke.md`、`reports/f15a/dev-smoke-run.json`（真实执行记录：profile、版本、命令、id、状态、输入/产物 sha256、blocked/not_run）、`deploy/dev/{bringup,dev_pki,smoke,dev_http}.py` + `README.md`（可重放启动产品：建库/编译/开发 mTLS 证书/migrate/bootstrap/API/maintenance；打印它执行的原始命令）、`tests/acceptance/`（**13 passed in 41.62s**：bring-up 迁移链/bootstrap/页面、销售 Decimal 独立真值（`250.00`/东区`200.00`/西区`50.00`/3 单）、缺列与重复订单业务失败（`attempt_count=1`）、owner-only 取消 + 取消后内部 claim 204、真实 `tool_timeout` 重试到 `MaxAttempts=3` 后终态失败（`failure_class=infrastructure`、诊断 `retryable=true`）、stub 模型 profile `passed` + 封存报告 `provider=stub,stub=true`、真实页面脚本由 Node 执行）、`apps/web/src/index.html`（状态页显示 profile/stub，AC2）、`apps/web/src/assets/attachments.js`（冒烟暴露的真实缺陷：失败诊断附件仍在扫描时页面读 409；改为有界等待真实 ready，报告 §4 记录）；真实 PG 16.4 + 编译二进制 + 真实 HTTP/mTLS + 真实 Python 子进程 + Node；`go build/vet/test`、`gofmt -l`、`sync_spec --check` 干净；无迁移、无契约改动；blocked：`container_runtime`、`linux_isolation`（WSL 有 runsc 但 WSL 不是被接受的隔离宿主）、`model_credentials`、S3/MinIO、Redis；not_run：`cmd/supervisor exec`（沙箱）、`cmd/worker`（liveness 骨架，不领取工作）、评测 UI、浏览器 DOM |

| T053 | F15A | 在候选 S3 上验证封存、匿名拒绝和删除 | **blocked** | `reports/f15a/T053-s3-probe.md`（探针与判别证明真实执行并通过；本机无候选 S3 端点 → 真实候选运行 exit 2 `blocked`，不伪造通过）、`probes/f15a_objectstore/`（`object_store_probe.py` 十条断言 + stdlib SigV4 + 环境凭据、`fixtures/{compliant,violating}_server.py` 真实 HTTP 判别夹具、`run_evidence.py`、`localfs_probe/main.go` 真实 `internal/objectstore` 类比）、`tests/f15a/`（36 passed：AWS SigV4 向量 8 + 配置失败 4 + 判别矩阵 13 + 边界常量 4 + 本地 FS 2 + 证据诚实性 5）、`reports/f15a/`（`candidate-absent`/`candidate-not-configured` blocked、`fixture-compliant` passed、`fixture-violating-all` 9 failed、逐条违规 9 份、503 重试、`local-fs-analogue`、`runs.json`、`environment-profile.json`）、`docs/adr/0008-f15a-object-store-compatibility.md`；`go build/vet/test`（29 ok / 0 FAIL / 11 no-test）、`gofmt -l` 空、`pytest tests/tooling` 21 passed、`sync_spec --check` no drift；not_run：版本化（夹具未启用）、备份/恢复（F15B）、真实候选 IAM/桶策略与平台 S3 后端接入；无迁移/契约改动 |
| T055 | F10 | 上传文本资料后检索到可追溯片段 | passed | `reports/f10/T055-knowledge-ingest.md`、`db/migrations/0034_knowledge.sql`、`internal/knowledge/`、`internal/embedding/`、`internal/background/handlers/document_ingest.go`、`internal/background/document_ingest.go`（完成守卫）、`internal/api/knowledge.go`、`apps/web/src/{knowledge.html,assets/knowledge.js}`、`tests/support/t055.py`、`tests/f10/`（20 passed）；真实 PG 16.4 + 编译二进制 + 真实 HTTP/mTLS + 真实封存对象 + 真实 maintenance 双池 + Node；pgvector 不可安装 → `double precision[]`/384 维（T064/T067），本地 `local-hash-v1` embedding（真实 provider blocked），非契约检索/引用/诊断面见 ADR 0010 |
| T059 | F10 | 摄取 CSV/XLSX 并保留行范围和安全错误 | passed | `reports/f10/T059-tabular-ingest.md`、`docs/adr/0013-f10-tabular-ingest.md`、`internal/knowledge/{table.go,parse_csv.go,parse_xlsx.go}`（`orchara-csv-parser/1`、`orchara-xlsx-parser/1`、`orchara-table-row-batch-chunker/1`；citation additive `row_start/row_end/row_count/header_row/sheet_name/columns/columns_omitted/text_render`；公式/DTD/宏/压缩比/工作表/行/列/单元格/单行上限 → 具体 failed 码；行不拆分）、`internal/knowledge/{parse.go,chunk.go,service.go}`（注册表/Parsed.Table/诊断 `detail` 只读派生）、`internal/background/handlers/document_ingest.go`、`internal/api/knowledge.go`、`internal/artifacts/admission.go`（仅导出 `SafeArchivePath`，准入无改动）、`apps/web/src/{knowledge.html,assets/knowledge.js}`、`tests/support/{t059.py,fixtures.py}`、`tests/fixtures/f10/`（提交夹具 + 确定性生成器）、`tests/f10/`（28 passed ×2 合并前；合并后 41 passed）；真实 PG 16.4 + 编译二进制 + 真实 HTTP/mTLS + 真实封存对象 + 真实 maintenance 双池 + Node；无迁移、无契约改动、`sync_spec` no drift；真实 provider 继承 T055 blocked |
| T060 | F10 | 摄取普通文本 PDF 并对比解析分块方案 | passed | `reports/f10/T060-pdf-ingest.md`、`docs/adr/0016-f10-pdf-ingest.md`、`db/migrations/0038_pdf_admission.sql`（只扩 T028 非契约 `artifact_admissions.detected_format` 词表 +`pdf`）、`internal/artifacts/admission.go`（`application/pdf` 仅 purpose=knowledge + `%PDF-` 魔数 + 原 20 MiB）、`internal/knowledge/{pdf.go,pdf_chunk.go,parse.go,chunk.go,table.go,service.go}`（`orchara-pdf-parser/1` 逐页文本/页映射/空页；`orchara-pdf-page-paragraph-chunker/1` 不跨页；citation additive `pdf{page_start,page_end,page_char_start,page_char_end,page_count,pages_without_text}`；`pdf_no_text_layer`/`pdf_invalid`/`pdf_encrypted`；32 非空白 rune 门槛、不做 OCR）、`internal/background/handlers/document_ingest.go`、`internal/api/knowledge.go`、`apps/web/src/{knowledge.html,assets/knowledge.js}`（PDF 上传/页码/空白页/失败文案）、`go.mod`（+`rsc.io/pdf v0.1.1` BSD-3）、`probes/f10_pdf_compare/`（Go 基线 probe + Docling 隔离 venv 真跑 + PyMuPDF/pypdf/pdfminer 替代 + 指标/淘汰判定）、`tests/fixtures/f10/`（5 个确定性 PDF + 手检 reference）、`tests/support/t060.py`、`tests/f10/{test_t060_pdf,test_t060_pages,test_t060_migration,test_t060_comparison}.py`、`reports/f10/{t060-comparison.json,t060-docling-pip.log}`；真实 PG 16.4 + 编译二进制 + 真实 HTTP/mTLS + 真实封存对象 + 真实 maintenance 双池 + Node；Docling 2.146.0 measured（质量打平基线、峰值 RSS 1.89 GB/安装 306 s/模型 505 MB → 淘汰），PyMuPDF(AGPL)/pypdf/pdfminer 替代无严格收益 → 淘汰，保留基线；真实 provider 继承 blocked、OCR not_run |
| T064 | F10 | 回答只引用本次检索且当前可读的资料 | passed | `reports/f10/T064-grounded-citations.md`、`docs/adr/0012-f10-grounded-answers.md`、`internal/knowledge/answer.go`（`Answer` + 带标签抽取式 stub `extractive-stub/1` + `validateAnswerDraft` + 引用返回前 `GetChunk` 复检 + dev-stub 故障约定）、`internal/knowledge/service.go`（检索/引用 SQL 硬化 `a.owner_id = kb.owner_id`）、`internal/api/knowledge.go`（非契约 `POST /knowledge-bases/{id}/answer`：200 answered/no_results、422 answer_without_citation/citation_not_found、409 citation_unreadable）、`internal/api/server.go`、`cmd/api/main.go`（`Answerer` 装配）、`apps/web/src/{knowledge.html,assets/knowledge.js}`（问答视图 + 引用点击重校验 + 撤销后显示不可用）、`tests/support/t064.py`、`tests/f10/{test_t064_answer.py,test_t064_pages.py}`（外加 Go 单测）；真实 PG 16.4 + 编译二进制 + 真实 HTTP + 真实封存字节 + 真实 maintenance 摄取 + 第二个真实 API 进程（故障约定）；真实模型生成 blocked（无凭据/adapter，生成=stub），无迁移、未改契约/规格 |
| T056 | F13A | 运行销售与坏输入案例并展示逐例结果 | passed | `reports/f13a/T056-sales-cases.md`、`docs/adr/0017-evaluation-case-results.md`、`internal/evaluation/{dataset,assertions,collect,report}.go`（保留成员 `task_input` 按内容 sha256+media_type 绑定调用方 fixture、`expected_failure`；`RunInput` 剥离控制成员；`failure_reason` 逐例 outcome）、`internal/execution/evaluation_runs.go`（按内容解析评测 owner 的 ready `task_input` artifact 并 `run_artifacts(input)` 绑定；facts 记录绑定 artifact id/sha256）、`internal/presetexec/presetexec.go`（失败 log/diagnostics 登记到 run，逐例附件可下载）、`internal/catalog/evaluation_cases.go` + `internal/api/{evaluation_cases,server}.go`（非契约 `GET /evaluation-reports/{id}/cases`：**owner-only**（RV1 复核修正：admin 也是非 owner、一律与不存在同答 404；无 admin 参数）、API key 需 `application:read`、非 owner 404、复读一致、无 object key）、`apps/web/src/{evaluate.html,assets/evaluate.js}`（逐例期望/实际/失败原因/附件下载）、`tests/support/t056.py`、`tests/f13a/datasets/`（5 个契约合法数据集 + 小 CSV + registry + 独立期望表）、`tests/f13a/reference/sales_reference.py`（stdlib+Decimal 独立计算器与“不得 import 被测工具”守卫）、`tests/f13a/test_t056_*.py`（17 用例）；真实 PG 16.4 + 编译二进制 + 真实 HTTP/mTLS + 真实封存字节 + 真实 maintenance 双池 + 真实 `cmd/supervisor exec` + WSL2/runsc 销售工具；**succeeded 销售 CSV/XLSX 案例真实 `passed`**；无迁移、未改契约/规格 |
| T062 | F13A | 阻止越权、取消和错制品报告通过验收 | passed | `reports/f13a/T062-report-integrity.md`、`docs/adr/0014-evaluation-attribution-admin-read.md`、`internal/evaluation/collect.go`（cancelled/unfinished run 永不 passed：`ReasonRunCancelled`/`ReasonRunUnfinished` + `CompletedRunState`/`TerminalRunState`）、`internal/background/evaluation.go`（`guardReportCaseRunsTx`：passed 案例必须由本评测的、owner 一致、snapshot/manifest 一致且已完成的真实 run 支撑；application 报告必须是真实 runner 评估器）、`internal/background/evaluation_attribution.go` + `internal/api/evaluation_attribution.go` + `internal/api/server.go`（非契约、admin-only `GET /api/v1/admin/evaluations/{id}/attribution`：owner/login、evaluation/job id、逐例 run 状态含 cancelled、snapshot/manifest/dataset hash、compiler/worker、terminal/completed；无 object key/report body/用户内容）、`internal/evaluation/collect_test.go`、`tests/support/t062.py`、`tests/f13a/{test_t062_runner.py,test_t062_guard.py,test_t062_authz.py}`、`tests/f13a/test_t052_runner.py`（取消案例按 #63 收紧为 failed/run_cancelled）；真实 PG 16.4 + 编译二进制 + 真实 HTTP/mTLS + 真实封存字节 + 真实 maintenance 双池 + 真实 supervisor 执行器；无迁移、未改契约/规格 |
| T065 | F13A | 查询异步评测进展并区分业务与设施失败 | passed | `reports/f13a/T065-progress-and-failure-classes.md`、`docs/adr/0019-evaluation-progress.md`（三次合并重编号：T060 0016→0017、T056 0017→0018、T061 0018→0019）、`db/migrations/0040_evaluation_progress.sql`（非契约：`evaluation_case_runs.verdict` 闭集 passed/failed/blocked/not_run；`evaluation_jobs.progress_failed`；`progress_done` 统一为“有 verdict 或 run 到达 rest 状态”；三次合并重编号 0038→0039→0040）、`internal/execution/evaluation_progress.go`（job-scoped `POST /internal/v1/background/jobs/{job_id}/evaluation-progress`：attempt token + 重证 owner ACL，逐例幂等 upsert，重算 progress_done/progress_failed）、`internal/evaluation/collect.go`（每例判定后上报，含无 run 的 blocked 案例）、`internal/background/evaluation.go`（守卫在写 report 的同一事务写入终态计数）、`internal/background/evaluation_progress.go` + `internal/api/evaluation_progress.go` + `internal/api/server.go`（非契约 `GET /api/v1/evaluations/{id}/progress`：owner-or-admin、`application:read` scope、404 不可枚举；仅 `state/report_status/report_id/total_cases/completed_cases/failed_cases/error_code/failure_class/created_at/finished_at`，不从 state 编百分比）、`apps/web/src/{evaluations.html,assets/evaluations.js,debug.html}`（可刷新评测页：轮询、业务 vs 设施失败、report_id 空态、报告不可变重读；Node 执行页面逻辑）、`tests/support/t065.py`、`tests/f13a/{test_t065_progress.py,test_t065_migration.py}`、`tests/f00b/test_t026_migration.py`（列基线按本票加法列有意迁移）；真实 PG 16.4 + 编译二进制 + 真实 HTTP/mTLS + 真实封存字节 + 真实 `cmd/maintenance run` 双池（work=1）；未改契约/规格 |
| T067 | F10 | 命中检索缓存也不能读到删除或撤权内容 | passed | `reports/f10/T067-retrieval-cache.md`、`docs/adr/0015-f10-retrieval-cache.md`、`db/migrations/0037_knowledge_retrieval_cache.sql`（非契约 PG 缓存表：principal/acl_epoch/kb_revision/source_revision_digest/embedding revision/canonical query/top_k 组件 + items + TTL + hit_count；worker/PUBLIC 零权限）、`internal/knowledge/cache.go`（`CacheOptionsFromEnv` 未知值 fail closed、长度前缀 SHA-256 键、canonical=TrimSpace、空/部分/超大结果不缓存）、`internal/knowledge/cache_store.go`（`sourceRevisionDigest`、命中组件比对、逐 chunk SQL 复检 + body artifact sha256、丢弃→miss、TTL 懒清理）、`internal/knowledge/service.go`（`Query` 重算键/命中/存储 + `rankedRetrieval` + `QueryResult.Cache`）、`internal/knowledge/answer.go`（`AnswerRetrieval.Cache` + `wrong-answer` dev-stub 故障）、`internal/api/{knowledge,server}.go`（`retrieval.cache` additive 元数据、`Deps.RetrievalCache`）、`cmd/api/main.go`（开关装配）、`tests/support/t067.py`、`tests/f10/{test_t067_migration,test_t067_cache,test_t067_rag_classes,test_t067_comparison}.py`（3+8+4+1 用例；真实 PG 16.4 + 编译二进制 + 真实 HTTP + 真实封存字节 + 真实 maintenance + 第二/第三真实 API 进程 off/故障；缓存关/开对照 8 文档 24 chunk × 每模式 24 次：命中 p50/p95 6.954/29.881 ms vs 未命中 28.391/55.102 ms（另一轮 5.249/10.672 vs 25.883/43.478）、正确性逐字段相同、复检 ≈0.09–0.22 ms/项，**有收益故保留**）；`pytest tests/f10` 57 passed ×2 背靠背（合并 `e225967` 后 57 passed）、`tests/tooling` 21 passed、`go build/vet/test`/`gofmt`/`sync_spec` 干净；Redis 不可用→PG 缓存（ADR 0015 D1），真实 embedding provider 继承 blocked，未改契约/规格 |
| T069 | F11 | 用户删除会话或文件后立即无法再读取 | **partial**（共享删除基础 + artifact/memory/document 三资源全部真实交付；conversation 无权威表/读路径 → **F08/T074**） | `reports/f11/T069-deletion-foundation.md`、`docs/adr/0020-deletion-foundation.md`、`db/migrations/0042_deletion_foundation.sql`（`deletion_jobs` 加法进度/重试/时间列；`remaining_scopes` 改为契约固定词表 `object_store|search_index|retrieval_cache|checkpoints|sandbox_temp|third_party_history|backup_catalog`；旧 T058 objects 转换；`deletion_jobs_open` 索引；worker/PUBLIC 零权限）、`internal/deletion/`（唯一 job/审计/取消编排 + `Target` 接缝）、`internal/artifacts/deletion.go`（artifact tombstone + `resource_in_use` + 派生 document 同事务 tombstone）、`internal/memory/deletion.go`（memory tombstone + epoch + 读谓词加 body artifact 检查）、`internal/knowledge/deletion.go`（document tombstone + scope details）、`internal/api/{deletions.go,server.go,memory.go}`（4 DELETE + `GET /deletions/{id}`，202 `DeletionAccepted`，重复删除同 job）、`apps/web/src/{personal-data.html,assets/personal-data.js}`（删除进度页）、`tests/support/t069.py`、`tests/f11/`（7 文件 22 用例：删除 API 2 + memory/document 4 + run/resource_in_use/恢复 3 + recall 1 + 迁移 5 + 页面 3 + 授权 4；真实 PG 16.4/编译二进制/真实 HTTP+CSRF/mTLS/真实 sealed 字节/真实 T036 取消/真实 T038 恢复/Node）；`pytest tests/f11` **22 passed ×2**（211.28s/211.63s；新增升级转换用例后；一次真实 WSL sandbox 瞬时 `sandbox_failed` 已隔离重跑两轮全绿）、`tests/f09` 69 passed（408.36s）、`tests/f10` 88 passed（563.58s）、f05 T028/T033 48 passed（375.42s）、`tests/tooling` 21 passed、`go build/vet/test`/`gofmt -l`/`sync_spec --check` 全绿；not_run：物理清理/retry/completion（T071）、checkpoint/备份账本（T072）、诊断（T073）、无复活全套（T076）、会话（T074）、浏览器 DOM、全量 pytest；未 push/未关 issue/未改契约/规格 |
| T073 | F11 | 用户限范围授权诊断且每次正文读取都有审计 | **partial**（AC1–AC3、AC5–AC8 与 AC4 的“错误/审计不带正文”由已执行真实栈测试证明；**AC4 的 trace/OTel 观测行为 partial**：本构建无 tracer/metrics SDK，无 owning 后续票，结构性保证见 ADR 0028 §6） | `reports/f11/T073-diagnostic-grants.md`、`docs/adr/0028-diagnostic-grants.md`、`db/migrations/0048_diagnostic_grants.sql`（契约 `diagnostic_grants` + 非契约 `object_scope` 快照/`created_at`、expiry/revocation CHECK、live/owner 索引、worker/PUBLIC 零权限、无 training/consent 列；本分支合并 T077/T071/T072 后 0045–0047 已被占，用下一个空位 0048，名字断言待 merger 按预留 0049 重编号）、`internal/audit/{contentread.go,contentread_test.go}`（唯一 typed 审计写入器：十字段白名单、UUID/token 形状、`served` 无 error_code、非法即拒绝写；13 条把 marker 塞进各字段的 fail-closed 单测）、`internal/diagnostics/{grants.go,content.go,grants_test.go}`（owner-only 创建 + 另一位启用 admin grantee + 用途/期限/对象快照校验；`grant ∧ NOT revoked ∧ NOT expired ∧ NOT run.deleted ∧ purpose ∈ ∧ object ∈ ∧ 成员且 ready` 读谓词；distinct 拒绝码；`operation_attempts` 恰好一个时才记 operation_id；审计先提交后出字节）、`internal/api/{diagnostics.go,server.go}`（契约 POST/DELETE + 非契约 `GET /diagnostic-grants` 与 `…/{id}/content`；`X-Diagnostic-Object-Scope` 头；API key 403 `personal_scope_forbidden`；错误体不回显值、请求日志无 query）、`apps/web/src/{personal-data.html,assets/personal-data.js}`（诊断授权对象多选/用途/到期/列表/撤销；11 个 Node 纯函数；只用 textContent）、`tests/support/t073.py`、`tests/f11/test_t073_{grants,content,redaction,migration,pages}.py`（**20 用例**：生命周期 3 + 受权读/真实 operation-attempt 关联/越范围/错用途/过期 6 s 窗口/撤销/tombstone/admin metadata-only 7 + marker 全审计与真实 API/maintenance 日志扫描/删字节失败读/审计非备份 3 + 迁移 4 + 页面 3；真实 PG 16.4 + 现场编译 api/admin/maintenance/supervisor + 真实 HTTP+CSRF+mTLS + 真实 sealed 字节 + 真实 tool capability operation + Node 24）；`pytest tests/f11` **42 passed ×2 背靠背**（最终合并 `2ee1caa` 后：394.89s/454.91s；此前一次全量遇 T069 真实 WSL2/runsc 瞬态 `sandbox_failed`，隔离重跑该用例 1 passed，未放宽断言）、`tests/tooling` 21 passed、`go build/vet/test ./...`（ok=35/no-test=14/FAIL=0）、`gofmt -l` 空、`sync_spec --check` no drift；not_run：物理清理（T071）、checkpoint/备份账本（T072）、无复活全套（T076）、浏览器 DOM、全量 pytest；**OTel/metrics：本构建无 SDK → AC4 的 trace 观测行 partial，无 owning 后续票（结构性保证见 ADR 0028 §6）**；未 push/未关 issue/未改契约/规格 |
| T072 | F11 | 清理 checkpoint 和上下文副本并登记备份删除范围 | **passed**（四 scope 全部真实交付：checkpoint/摘要/临时沙箱/第三方历史逐类 ledger 记录 + 同一 evidence 事务清 scope；独立 append-only 哈希链账本由真实 `admin deletion export/replay` 在第二个真实 PG 16.4 库回放、篡改拒绝、二次回放 no-op；真实 FS 失败下 job 失败可见、`GET /deletions` 仍列未清 scope、真实读端点仍 404；对象/索引/缓存与 `delete_content` handler 属 **T071**） | `reports/f11/T072-copy-ledger.md`、`docs/adr/0027-f11-copy-cleanup-ledger.md`、`db/migrations/0046_deletion_copy_ledger.sql`（票面分配 0048；起始用下一个空位 0045，合入集成 tip 时 T077 已占 0045，故最终用 0046 保持 0001..NNNN 连续（最终编号以合并器 0048 分配为准）：`platform.deletion_ledger` append-only 触发器 + `record_sha256/prev_sha256` 链 + 双向无 FK + `entry_kind`/`copy_class` 闭集 + worker/PUBLIC 零权限）、`internal/deletion/ledger.go`（同事务 tombstone 决策；导出 JSONL header count/head；验链/四资源回放 artifact+derived document/memory epoch/knowledge_document/conversation 请求取消；只改 `deleted_at IS NULL`、`GREATEST(revision+1, deletion_revision)`、回放库内追加 `source=replay` 记录）、`internal/deletion/copies.go`（`CopyCleaner` + `ClearScopesTx`：真实 checkpoint 三表按 thread 删除/缺失 schema 记 absent、summary 无存储记 absent+surface 计数、sandbox 按 `run_attempts.execution_unit_id` 定位真实主机目录并验 FS、第三方默认 absent/配置后 external、backup_catalog 登记 + vendor/downloaded/backup_media 不可擦除声明；失败保留 scope 且 job failed）、`internal/deletion/deletion.go`（tombstone 事务内追加账本决策）、`cmd/admin/deletion.go` + `cmd/maintenance/copies.go` + 两个 main.go（单 JSON stdout/exit 0/1/2；`export|replay --check-only`、`copies --sandbox-temp-root/--sandbox-age/--third-party-components/--limit`）、`internal/sandbox/{sandbox.go,executor.go}`（主机 workspace 名含 sandbox id 以绑定 attempt 来源引用 + `ORCHARA_SANDBOX_WORKSPACE_ROOT`，默认行为不变）、`tests/support/t072.py`、`tests/f11/{test_t072_ledger,test_t072_copy_cleanup,test_t072_failures,test_t072_migration}.py`（16 用例：账本 5 + 副本 5 + 失败/活 attempt 2 + 迁移 4）；`pytest tests/f11` **38 passed ×2 背靠背**（443.55s/450.46s；最终合并树 400.70s；一次 WSL 瞬时 sandbox_failed 隔离重跑 17 passed/245.21s；真实 PG 16.4 + 编译 API/admin/maintenance/supervisor + 真实 HTTP/CSRF/mTLS + 真实 sealed 字节 + 真实 T036 取消 + 真实 checkpoint schema fixture + 真实主机目录/Windows 文件句柄 + 第二个真实库回放）、`pytest tests/f09` **69 passed**（593.17s）、`pytest tests/f08` **19 passed**（186.91s）、`tests/tooling` 21 passed、`go build/vet/test ./...`（全 ok / 0 FAIL）、`gofmt -l .` 空、`sync_spec --check` no drift；未改 `delete_content` registry、未新增 job kind（部署级副本清单走 maintenance CLI，ADR 0027 §3）；not_run：pg_basebackup 真实备份、真实崩溃残留沙箱、checkpoint 表锁失败分支、真实 Mem0/供应商擦除、浏览器 DOM、pre-0045 历史回填、全量 pytest；未 push/未关 issue/未改契约/规格 |
| T074 | F08 | 从目录指定版本提交需求并有序排队 | passed（会话/请求/队列/固定版本 Run/幂等/取消/处理版本可见/T069 conversation 删除全部真实交付；模型路由与候选选择 → **F08/T079**；交接 UI → **F08/T082**） | `reports/f08/T074-conversations-and-requests.md`、`docs/adr/0023-conversation-request-queue.md`、`db/migrations/0044_conversations_and_requests.sql`（conversations/requests/interactions/request_handoffs；非契约列 `artifact_ids`/`requested_version_id`/`control_calls`；`one_active_request_per_conversation` 部分唯一 + 0014 顶层 Run 唯一构成逻辑槽，**不**为 dispatched 建唯一索引；补全 `runs_request_id_fkey`/`runs_conversation_id_fkey`/`platform_model_calls_request_fkey`；worker/PUBLIC 零权限）、`internal/requests/`（`CreateConversation`/`ListConversations`/`GetConversation`/`Submit`/`GetRequest`/`Cancel`/`ListMessages`/`AdvanceConversationQueueTx`/`Facts`/`DeletionTarget`：located 422/404/409、幂等 receipt 同体重放异体 409、显式版本 `ResolveRunnableVersionTx`+`FOR SHARE` 防撤销竞争、消息/摘要真实封存、交接最小确认与来源 Run 校验、惰性 interaction 过期与排队读取推进、会话 tombstone 同事务取消排队请求）、`internal/catalog/{publication,versions}.go`（`ResolveRunnableVersionTx`/`Version` 冻结执行身份字段/`RunPolicy`）、`internal/api/{requests,server}.go`（7 契约 operation + 非契约 `X-Request-*` 处理版本/队列位置/correlation；会话 DELETE 走 T069 基础）、`apps/web/src/{chat.html,assets/chat.js}`（新建/选择会话、附件真实上传、固定版本、最小交接确认、队列/处理版本/Run 链接/取消/轮询/消息时间线，纯函数 Node 执行）、`tests/support/f08.py`、`tests/f08/{test_t074_queue,test_t074_submit,test_t074_isolation,test_t074_delete,test_t074_migration,test_t074_pages}.py`（19 用例：并发竞争先写、队列推进、固定版本无 router/model call、吊销 409、幂等、新应用不读旧历史/记忆、会话删除 7-scope/tombstone/取消、迁移、页面）；`pytest tests/f08` **19 passed ×2 背靠背**（195.23s/196.02s；真实 PG 16.4 + 编译 API/admin/maintenance + 真实 HTTP+CSRF + 真实封存字节 + 真实 T036 取消 + Node 24）、`tests/tooling tests/f11` 43 passed、最终 merge `7138ae9` 后 `tests/f02b tests/t070 tests/f11 tests/tooling` 73 passed、合并后 `tests/f08` 19 passed ×2（201.09s/183.13s）、`tests/f02a` 的 T025/T027 直接相邻 13 passed（全量 f02a 在共享主机无输出挂起 >20min 已中止）、`go build/vet/test`/`gofmt -l`/`sync_spec --check` 全绿；not_run：模型路由/候选/澄清/控制预算（T079）、交接 UI（T082）、浏览器 DOM、全量 pytest、T068 并行套件 f02b（迁移重编号属 T068）；未 push/未关 issue/未改契约/规格 |
| T076 | F11 | 删除后恢复与组件历史不能让内容复活 | **passed**（OTel/metrics 专用行 **partial**：本仓库无 tracer/metrics SDK，交付只读告警命令 + 退出码，见 ADR 0033 §2；新 Mem0 删除尝试 `not_run`） | `reports/f11/T076-no-resurrection.md`、`docs/adr/0033-no-resurrection-and-cleanup-alerts.md`、`internal/execution/{recovery.go,recovery_store.go}`（recovery 的"输入"= `runs.input_artifact_id` ∪ `run_artifacts.relation='input'` 全集，逐个复核 ready/未删/hash+size/封存字节，任一失败 → 既有 `input_deleted` 并点名 artifact id；写事务内 `recheckRecoveryArtifactRows` 同样复核；修复前"删除附件后 recovery 仍 recovered"的真实缺口由失败测试先复现）、`internal/deletion/alerts.go` + `cmd/admin/{deletion,main}.go`（只读 `orchara-admin deletion alerts [--deadline 24h] [--limit N]`：`deletion_job_overdue`/`deletion_job_failed`/`cleanup_timeout`/`cleanup_failed`/`cleanup_stalled`；单行 JSON 只含 id/code/state/age/attempt/remaining scopes/boundary，无正文/object key/hash；exit 0=无告警、3=有告警、2=用法、1=无法执行，新增 `exitCodeError` 把"已判定信号"和失败分开）、`internal/background/{handlers/delete_content.go,delete_content.go}`（bounded attempt 在读取 scope 前过期 → 可重试 `delete_timeout`；completion guard 在"唯一剩余证据是对象仍在"时保留超时分类，不再被 evidence 结果覆盖为 `transient_storage_io`；deletion job 行仍记录 evidence 派生错误码）、`experiments/mem0/mem0exp/{decision.py,records.py}`（扩展现有 Q45 门禁而非另起：`deletion_proof` 合并 `residual_body_copies` ∪ `unverifiable_copies` ∪ `deletion_sufficient:false` → `gates.f11_component_deletion` 失败 → `do_not_adopt`，与 `gates.f11` 独立；record 校验拒绝缺该门的 adopt 与"列出未清副本却声称门禁通过"的自相矛盾；提交 T066 证据校验不变、仍 `do_not_adopt`）、`tests/support/t076.py`、`tests/f11/test_t076_{late_writer,recovery,alerts,restore,adoption,governance}.py`（13 用例：late finalize 与 parked late ingest 各重放两次并断言 `source_deleted`/无新字节/无新 job、删除前 live finalize writer 时 DELETE 409 `resource_in_use`；recovery 4（主输入两次拒绝、链接输入修复、等待审批 Run 删除后取消且两次决定均无 resume、队列 Run 取消且两次 claim 204）；alerts 2（真实 `--delete-content-timeout 1ns` → `delete_timeout`、48h 逾期、未认领停滞、完成后恢复 `ok`、输出无 marker/object key/hash）；restore（备份字节回写后仍 404；未回放账本的"恢复"会复活行 → 真实 `deletion replay` 重新 tombstone，二次 `replayed=0` 且 ledger 行数不变）；adoption 3；治理链 1 个用例顺序重放 10 个门禁并集中记录证据）；真实 PG 16.4 + 编译 api/admin/maintenance/supervisor + 真实 HTTP/CSRF/mTLS + 真实 sealed 字节 + 真实 job 控制器 + 真实账本导出/回放（本票无 UI 改动）；`pytest tests/f11` **81 passed ×2 背靠背**（732.23s / 790.14s；一次全量运行中既有 T069 recall 用例瞬态失败一次，隔离重跑 1 passed，未放宽断言）、相邻 `tests/tooling`+T038 恢复/拒绝+T066+F10 摄取子集 57 passed、`go build/vet/test ./...` 全绿（35 ok / 6 no-test / 0 FAIL）、`gofmt -l .` 空、`sync_spec --check` no drift，实测尾部见报告 §3；无迁移（0050 未使用：无新表/列，告警只读既有表）；not_run：新 Mem0 删除尝试（隔离 venv 非产品依赖）、OTel/metrics/tracing 导出（无 SDK → partial）、真实备份介质擦除/恢复工具（T072 账本回放为控制点）、浏览器 DOM、全量 pytest；未 push/未关 issue/未改契约与 `docs/spec` |
| T079 | F08 | 自然语言需求通过选择或澄清进入唯一主运行 | passed（`replyInteraction`/授权候选检索/受记账控制模型/服务端候选-输入-授权校验/input+selection interaction/3 次控制预算+degrade/精确 409 `interaction_closed`/`route_request` 真实 handler+失败守卫/页面全部真实交付；真实 vendor 模型 adapter 属 F06 → 真实厂商模型 not_run） | `reports/f08/T079-router-and-interactions.md`、`docs/adr/0029-t079-router-interactions.md`、`db/migrations/0050_router_interactions.sql`（非契约 `request_routing_candidates`（rank 序号、card_sha256、无 score 列）/`request_routing_decisions`（attempt 轮次、model_call_id、raw_decision）/`requests.selected_version_id`；worker/PUBLIC 零权限）、`internal/routing/`（ACL 在 LIMIT 前的授权候选查询 + 中文 2/3-gram 字面/全文匹配；`Advance` 一轮 prepare/control/commit：重试先查 Run/pending interaction、显式候选不重调模型、账本计数预算、3 次后 degrade、模型只建议、>1 候选一律 selection、=1 且输入满足才派发、失败推进队列；候选/提案/decision 无分数，`DispatchVersionTx` 复用 T074）、`internal/requests/reply.go`（`ParseReply` anyOf/封闭字段、路径级幂等 receipt、精确 CAS `state='pending' AND expires_at > now()`、0 行回滚 receipt 后独立事务 expired+failed+推进、伪造候选 422、答复附件并入输入闭包）、`internal/requests/store.go`（`Request.interaction.proposal` 读取封存提案）、`internal/modelproxy/{routecontrol,provider}.go`（`route_control:true` passed 候选解析、XOR 账本归属 route job、dev-stub 确定性路由分支 `stub:true`）、`internal/background/{scope,job,complete,controller,route_guard,handlers/route_request}.go`（`JobScope.Route` + 内部协议、重试表、终止失败 guard 失败请求并释放槽）、`internal/api/{route,requests,server,internal}.go`（`POST /internal/.../route`、`POST /api/v1/requests/{id}/reply` 202）、`cmd/maintenance/scope.go`、`apps/web/src/{chat.html,assets/chat.js}`（交互面板候选/提问/缺字段/答复，纯函数 Node 执行）、`tests/support/t079.py`、`tests/f08/{test_t079_routing,test_t079_interaction,test_t079_jobs,test_t079_migration,test_t079_pages}.py`（19 用例 + Go `internal/routing/routing_test.go`；真实 PG 16.4/编译 API+admin+maintenance/真实 HTTP+CSRF/mTLS/真实封存字节/Node 24）、`pytest tests/f08` **54 passed ×2 背靠背**（949.35s/842.00s）、`tests/tooling` 21 passed、`tests/f11` 22 passed（conversation 删除线）、`go build/vet/test`/`gofmt -l`/`sync_spec --check` 全绿；not_run：真实厂商模型（F06）、语义/中文分词召回（T064/T067）、浏览器 DOM、全量 pytest；未推送/未关 issue/未改契约/规格 |
| T061 | F10 | 更新资料或 embedding 后不激活陈旧索引 | passed | `reports/f10/T061-index-revisions.md`、`docs/adr/0018-f10-index-revisions.md`、`db/migrations/0039_index_revisions.sql`（`platform.knowledge_index_versions`：document/version/document_revision/embedding_model_revision/embedding_dimension/state/chunk_count/error_code/activated_at；每源 revision 一个 building；upgrade backfill；`knowledge_documents.active_index_id`/`active_embedding_model_revision`；`knowledge_chunks.index_version_id` 身份改为 `(index_version_id,chunk_no)`；`background_jobs.expected_index_version_id`）、`internal/knowledge/index_versions.go`（`ReindexDocument` 非契约重建入口 + 新索引版本/同事务 job；`IndexVersion.Stage()` 诚实进度；`RetrievalIndexInfo`）、`internal/background/document_ingest.go`（激活事务：`FOR UPDATE` 重读文档/索引行，要求 revision 相等、仍 indexing、无 tombstone、索引仍 building；`stale_revision`/`source_deleted` 拒绝且不改指针；失败保留旧活动索引；唯一写 `active_index_id` 者）、`internal/background/handlers/document_ingest.go` + `scope.go`（按索引版本暂存、部署模型不一致 `embedding_model_changed`）、`internal/embedding`（第二个确定性 dev-stub `local-hash-v2`，未知 stub fail closed）、`internal/knowledge/{service,cache_store}.go`（检索/引用/缓存改为活动索引版本谓词；source digest 含 active index/model）、`internal/api/knowledge.go` + `server.go`（`POST /knowledge-documents/{id}/reindex`、诊断 `active_index/indexes[]`、检索/回答 `retrieval.index`）、`apps/web/src/{knowledge.html,assets/knowledge.js}`（索引版本/模型/进度/失败/活动切换 + 重建按钮）、`tests/support/t061.py`、`tests/f10/{test_t061_migration,test_t061_index_versions,test_t061_races,test_t061_pages}.py`（真实 PG 16.4 + 编译二进制 + 真实 HTTP/mTLS + 真实封存字节 + 真实 maintenance work-only/finalize-only 双进程挂起旧 handler：源 revision 推进后 `stale_revision`、tombstone 后 `source_deleted`，`activated_at` 保持 NULL；换模型两个真实进程/两个索引版本/两套向量；失败重建旧索引仍可读并可重试）；`pytest tests/f10` 两次背靠背 + `tests/tooling`、`go build/vet/test`/`gofmt -l`、`sync_spec --check` 干净；真实 provider 继承 blocked，历史索引 GC、浏览器 DOM、全量 pytest not_run；未改契约/规格 |

## 本轮小结（D00 + P01–P05 + F00，共 16 票）

| 状态 | 数量 | 票 |
|---|---|---|
| passed | **16** | T001–T007, T008–T014, T016, T018（全部） |
| blocked | 0 | — |
| not_started | 0 | — |

16 票全部通过并关闭。**这不等于完整首版**：本批只是"地基"（D00 + P01–P05 + F00），
覆盖环境探测、依赖锁定、双语言指纹与 DSL 校验、检查点恢复与制品钉版、
Profile/CI、沙箱隔离与资源回收、租约与丢回执。

仍未实现（属后续票，不在本批范围）：请求/路由/执行/评测/RAG/记忆/审批等业务模块、前端画布、
编译器与十类节点、发布门禁、部署与灾备。身份基础已由 F01 交付，但平台不是完整 v1；
open issue 数以 GitHub 为准。

## F01 身份（5/5 完成：T015、T017、T019、T020、T021）

F01 按 DESIGN 顺序推进：T015 → T017 → T019 → T020 → T021。五票全部通过。

| 票 | 状态 | 交付 / 证据 |
|---|---|---|
| T015 | passed | 迁移链从空库可执行（0006 身份六表 + 0005 条件授权修复）、`cmd/admin migrate/bootstrap`、邀请/激活/登录三条契约端点、admin/activate 页面；`reports/f01/T015-identity-bootstrap.md`、`tests/f01/`（37 passed，真实 PG + 真实二进制 + 真实 HTTP） |
| T017 | passed | 登出/`GET /auth/me`/页面刷新恢复身份、CSRF 强制（会话绑定的 HMAC 派生 token，零迁移）、统一授权助手 `authorize(w,r,role)`、登录限流 5 次/60 秒；`reports/f01/T017-login-and-csrf.md`、`internal/identity/csrf.go`、`internal/api/{authz,loginlimit,server,cookies}.go`、`apps/web/src/login.html`、`tests/f01/test_t017_*.py`（`tests/f01` 共 63 passed） |
| T019 | passed | `policy_epochs`/`resource_grants`/`applications` 迁移（0007）、默认拒绝的 `policy.Authorizer`（每次读 epoch + 进程内决策缓存 + 过期重校验）、`catalog` 先 ACL 后分页的读模型、grant/revoke/角色/应用列表与详情五条契约端点、`/apps` 页面与 admin 授权/角色表单；`reports/f01/T019-resource-grants.md`、`tests/f01/test_t019_*.py`（`tests/f01` 共 94 passed） |
| T020 | passed | `api_keys` 迁移（0008）、具名负责人的服务账号、scope（F01 仅 `application:read`）+ 资源 ACL 交集、bearer 认证（严格 `Authorization: Bearer`，无 Cookie 回退）、key 明文仅一次且只存 SHA-256、撤销/过期/负责人禁用即时拒绝、`/memories` 对服务账号 403 与对人类的诚实空列表、admin 服务账号/API key 表单；`reports/f01/T020-service-accounts.md`、`tests/f01/test_t020_*.py`（`tests/f01` 共 124 passed） |
| T021 | passed | `PATCH /admin/principals/{id}` 禁用/启用（同一事务 + 与角色变更共用的 `ROLESET1` 锁：撤销全部会话、本人 key 与所负责服务账号的 key、bump epoch、写 reason 审计；幂等 no-op 不写审计不 bump；最后一个启用中的人类管理员 409）、`cmd/admin handover`（只把服务账号责任交给启用中的人类管理员，逐账号审计，不改角色/授权/key；拒绝 exit 1、用法错误 exit 2）、admin 页面禁用/启用表单；`reports/f01/T021-disable-and-handover.md`、`internal/identity/{disable,handover}.go`、`internal/api/principals.go`、`cmd/admin/main.go`、`tests/f01/test_t021_*.py`（`tests/f01` 共 142 passed） |

> 两轴复核修复后（2026-11-02）的计数：`product/tests/f01` **151 passed**，
> `product/tests` **525 passed**，无失败；Node 24 在 PATH，因此没有 skip（唯一的条件 skip 是
> Node 不可用时的 `test_f01_app_js.py`，本次未触发）。新增 9 个 pytest 用例：T019 自授/时钟
> 一致性 3 个、Node 执行 `app.js` 与内联脚本语法 6 个。Go 侧新增 3 个单测（owner fail-closed、
> `ListVisible` 时钟参数、自授规则）。F01 的决策边界见
> [ADR 0003](docs/adr/0003-f01-identity-boundaries.md)。
>
> 其后独立复核又确认 4 项并发/页面问题（见下文“复核后补（2026-11-02）”），修复后新增 4 个
> pytest 用例（T020 两条签发路径的竞态 2 个、T017 会话签发竞态 1 个、三个页面表单必须 POST 1 个），
> 锁在签发事务内联施加，未新增 Go 单测。最终计数：`product/tests/f01` **155 passed**、
> `product/tests` **529 passed**，无失败、无跳过。

T015 交付身份表与三条端点；T017 补齐 CSRF 强制、登出、`/auth/me` 与登录限流
（限流当前是**单进程内存态**，多实例不共享，属 F15B，见 T017 报告 §7）。
T019 交付 `policy_epochs`/`resource_grants`/`applications` 与默认拒绝的资源 ACL：
每次动作读当前 epoch，撤销/角色变更在同一事务里 bump epoch，因此进程内缓存不可能
跨过一次撤销；列表在 SQL 里先 ACL 过滤再分页。
T020 交付 `api_keys`/服务账号与 bearer 认证：key 只属于 service principal，明文只显示一次、
库中只有 SHA-256；`Authorization: Bearer` 只加在 `internal/api/authz.go` 的 `authenticate`，
scope 缺失在 catalog 之前 403、ACL 缺失仍是 404；撤销/过期/负责人禁用在下一次认证即生效。
T021 交付 `PATCH /admin/principals/{id}` 的禁用/启用与 `cmd/admin handover`：禁用在同一事务里
撤销该人的全部会话与 key、以及其负责的服务账号的 key，bump policy epoch 并写 reason 审计；
与角色变更共用 `ROLESET1` 咨询锁，因此最后一个启用中的人类管理员不可能被并发绕过。交接只改
服务账号的 `responsible_user_id`，逐账号审计，不动角色/授权/key，故不扩权；契约没有 successor
字段，所以交接是 CLI-only。F01 唯一可交付的“未完成待办”是服务账号责任，审批交接属 F06。

## 两轴复核修复（2026-11-02）

对 `feat/f01-identity`（HEAD `725a688`）做 Standards / Spec 两轴复核，发现 16 项；全部在本轮
修复或如实登记，未推送、未关闭 issue、未改 `docs/spec/`、`contracts/` 或迁移 0005–0008 的 SQL。
F01 的决策边界记录在 [ADR 0003](docs/adr/0003-f01-identity-boundaries.md)。

| 类别 | 复核项 | 修法 | 证据 |
|---|---|---|---|
| 硬违规 | `PROGRESS.md` T019 行写 `tests/f01` 90 passed，同一时点实际 94 | 改为 94；按 T015 37 / T017 63 / T019 94 / T020 124 / T021 142 核对，其余行无内部矛盾 | `PROGRESS.md` |
| 硬违规 | T015 报告把 0005 的 `app.operations` 条件授权写成“语义不变、链可跑完” | 改写为：当前链无表、条件 GRANT 永不执行、P04 承诺 unverified、F03 必须建表并显式授权；补不可变性例外与 pre-F01 运维说明 | `reports/f01/T015-identity-bootstrap.md` §1.1/§7 |
| 硬违规 | 缺 F01 决策记录 | 新增 ADR 0003：platform/app 并存、0005 修复与记账起点、applications 切片、登录拆分、CSRF 派生、自授规则、服务账号/审批、`/memories`、`ROLESET1` | `docs/adr/0003-f01-identity-boundaries.md` |
| 行为 | `Allow` 对 owner 在读 epoch 前返回 true，违反“每次调用检查 epoch” | epoch 读取移到最前；owner 是 epoch 之后的第一决策；缺 epoch 对 owner 也 fail closed | `TestTheOwnerIsAllowedAfterTheEpochRead`、`TestAnOwnerAlsoFailsClosedWhenThePolicyEpochIsMissing` |
| 行为 | 列表用数据库 `now()`、详情用进程时钟；`application`/`read` 字面量散落 | 新增 `policy.ResourceApplication`/`ActionRead`；`ListVisible(..., now)` 用 `$5` 与常量参数；API 传 `s.policy.Clock()` | `TestListVisibleQueriesWithTheProvidedClockAndVocabulary`、`test_the_list_and_the_detail_agree_at_the_same_grant_expiry` |
| 行为 | 管理员可给自己授 `read` 再读任意应用 | `validateSelfGrant`：422 `validation_failed`、`details.field=principal_id`、零行/零 epoch/零审计；`EqualFold` 防大写 UUID 绕过；两人路径保留 | `test_an_administrator_cannot_grant_themselves_read`、`test_a_different_administrator_can_grant_the_read`、`TestValidateSelfGrantRejectsTheActingAdministratorCaseInsensitively` |
| 判断 | last-admin 计数 SQL 在 roles/disable 重复 | 抽 `countOtherEnabledHumanAdmins(ctx, tx, principalID)`，两处调用；行为与既有测试不变 | `test_t019_roles.py`、`test_t021_last_admin.py` 全绿 |
| 判断 | 页面测试只做子串断言，从不执行 JS | 新增 Node 执行 `assets/app.js`（stub `fetch`）与五个页面内联脚本 `node --check`；保留结构测试 | `tests/f01/test_f01_app_js.py`（6 passed） |
| 判断 | 测试夹具含固定 password 字面量 | 所有 F01 密码常量改为 `secrets.token_urlsafe(18)` 加前缀（含弱密码/第二次输入）；日志/URL 秘密断言不减弱 | `test_t015_secrets.py`、`test_t015_bootstrap.py`、`test_t017_rate_limit.py` 等 |
| 记录 | T017 报告未说明登录限流是契约要求 | 引 `contracts/openapi.json` 的 `login` "Rate limited." 与 `API.md` §1 的 429；保留单进程限制 | `reports/f01/T017-login-and-csrf.md` §1.4/§6.9 |
| 记录 | T019 未引 last_admin 契约依据，未记录自授规则 | 引 `setUserRoles` description 的 "at least one enabled human administrator"、`updatePrincipal` 继承；新增自授规则条 | `reports/f01/T019-resource-grants.md` §6.10/§6.11 |
| 记录 | `/memories` 边界易被读成“记忆已实现” | 明确 F09 拥有端点/存储，F01 只交付服务凭据 403 拒绝边界；人类 200 空列表是诚实空读模型 | `reports/f01/T020-service-accounts.md` §6.8 |
| 记录 | 未完成待办/CLI-only 的依赖关系不够显式 | 明确服务账号责任因审批属 F06；`updatePrincipal` 无 successor 字段，故交接只能 CLI | `reports/f01/T021-disable-and-handover.md` §6.9 |
| 陈旧 | README“平台尚未实现”与 F00/F01 事实不符 | 重写当前状态：已实现 D00+P01–P05+F00+F01，列 F01 限制与阻塞；补 F01 命令、页面与报告入口 | `README.md` |
| 记录 | 进度缺复核记录、最终计数与 ADR 链接 | 修正 90→94；新增本节；F01 段补最终计数与 ADR 0003 链接 | `PROGRESS.md` |
| 交付 | 需要逻辑提交、干净树、不推送 | 按 `fix(f01)`/`test(f01)`/`docs(f01)` 拆分提交；门禁后 `git status --short` 为空；未 push/未关 issue | `git log`、最终门禁命令输出 |

## 复核后补（2026-11-02）

独立复核在 `feat/f01-identity`（`2ce698f`）上确认 4 项并发/页面问题，全部修复；未改
`docs/spec/`、`contracts/`、迁移 0005–0008，未推送、未关闭 issue。

| 编号 | 级别 | 问题 | 修法 | 证明测试 |
|---|---|---|---|---|
| 1 | P1 | `CreateAPIKey` 在事务内读负责人 `disabled_at`，但不取 `ROLESET1`；禁用与 INSERT 交错会留下未撤销的 key，重新启用负责人后仍可 200 认证 | 事务开头取 `pg_advisory_xact_lock("ROLESET1")`，状态检查 + INSERT + 审计都在锁内；校验顺序/状态码不变 | `test_t020_issuance_race.py::test_a_key_is_never_born_after_the_disable_that_should_have_revoked_it`（无锁实测：key 行 `revoked_at` 为 NULL、bearer 200；有锁：401 `invalid_key`） |
| 2 | P2 | `CreateServiceAccount` 同样缺锁；创建与禁用/交接交错会把新服务账号留在已禁用的负责人名下，已完成的交接不会回溯移动 | 事务开头取同一把锁，负责人检查 + INSERT + 审计都在锁内 | `test_t020_issuance_race.py::test_a_service_account_created_across_a_handover_is_moved_by_it`（无锁实测：负责人仍是 R；有锁：负责人是 A，且再以 R 创建 422 `validation_failed`） |
| 3 | P1 | `Login` 在 `BeginTx` 前检查禁用，会话 INSERT 不取锁；禁用后签发的会话在用户重新启用后复活 | 登录事务先取同一把锁，锁内重读 `disabled_at`，已禁用则回滚返回 `ErrPrincipalDisabled`；argon2 校验仍在事务外，未知登录 dummy verify 不变 | `test_t017_session_race.py::test_a_session_is_never_born_after_the_disable_that_should_have_revoked_it`（无锁实测：会话 `revoked_at` 为 NULL、重新启用后 200；有锁：立即与重新启用后均 401 `session_revoked`） |
| 4 | P1 | `login.html`、`activate.html`、`admin.html` 的 11 个 `<form>` 没有 `method`；无 JS/JS 未加载时 GET 提交把密码、激活令牌、CSRF 字段带进 URL 与历史 | 全部 11 个表单加 `method="post"`；不设置带参数的 action、不删除 JS handler | `test_t015_pages.py::test_every_form_posts_and_no_action_carries_a_query_string`（真实 HTTP 取三页，逐个 form 断言 POST、action 无 `?`、敏感字段在表单块内、表单总数为 11；无修法时在 `/login` 失败） |

并发测试的确定性来自 DB 侧：`BEFORE INSERT` + `pg_sleep` 把签发事务挂在真实表上，测试轮询
`pg_stat_activity` 的 `PgSleep` 等触发点（不是猜 wall-clock 延迟），再让禁用/交接在另一客户端
真实执行。修复提交：`e232b73`（问题 1–3，`internal/identity/{apikeys,session}.go`）、
`5006840`（问题 4，三个页面）；测试提交：`43b56aa`；本段与 ADR/报告在 docs 提交中。

## 第二轮复核修复（2026-11-01）

第二次两轴复核（`9707af3...eb13188`）发现 21 项，全部处理：

| 类别 | 问题 | 修法 |
|---|---|---|
| 硬违规 | CI 门禁把 skipped 当通过（`ok = not failed`） | 判定改为 `ok / partial / failed`，skipped 即 `partial` 且非零退出；`--allow-skipped` 只降退出码不改判定 |
| 硬违规 | 沙箱校验了挂载却从不施加 | `do` 路径直接拒绝任何 mounts 并指向 OCI bundle（真正施加只读 bind 的地方） |
| 硬违规 | 4 处硬编码 `E:\orchara-toolchains` | 新增 `tools/toolchain.py`，`pg_dev`/`probe_environment`/测试/CI 全部走它 |
| 硬违规 | T012 报告称"迁移只有 0001" | 改为不列清单，指向 T008 报告 |
| 证据 | T014 的 `ulimit` 写在被测命令里 | 改用 OCI `process.rlimits`（`RLIMIT_AS`/`RLIMIT_CPU`），由运行施加；补 CPU 与输出两类用例 |
| 证据 | `feature-flag.json` 是测试自己写的 | 删除该文件；开关只由 `ORCHARA_ALLOW_ARBITRARY_SCRIPTS` 决定，`record_failure` 不再写盘 |
| 证据 | T010 网络断言无法区分"被阻断"与"工具坏了" | 增加 guest 侧对照组（`CONNECTED`），沙箱内改记真实退出码与错误文本；代理一行如实说明 busybox `wget` 不读 `HTTP_PROXY` |
| 证据 | T010 的 env 修复无测试 | 新增宿主密钥不可见 / 批准变量可见 / 未批准变量被拒 / 启动命令带 `env -i` 四条测试 |
| 证据 | T011 的"真引擎"断言是常量 | 改为让引擎拒绝并发写非 reducer key（`InvalidUpdateError`），桩无法伪造 |
| 证据 | T009 无机器可读产物 | 新增 `probes/p02_checkpoint/artifact_evidence.py` 生成 `T009-artifact-pinning.json` |
| 记录 | T007 "corpus 由实现生成"的指控 | 核实后更正：33/33 负例的原因码是手写并被 builder 强制；真正派生的是 valid 摘要与 reasons 细目（仅 1 条锚点），已如实写入报告 |
| 卫生 | psql 块复制 4 份且碰私有函数 | `pg_dev.run_sql()` / `quote_literal()` 公开，`drain_gate` 改用 |
| 卫生 | supervisor 死代码与机器 JSON 里的散文 | 删 `validate()` 死分支与 `NeverRestartedNote`，改名 `ErrNoStore` |
| 卫生 | worker 直写 `app.workers` 与 DATA-MODEL §48 冲突 | 新增 `0005_worker_grants.sql` 显式授权该表并撤销其余写权限 |
| 记录 | T016 报告"属 T013"已过期 | 改为如实说明 T013 未接线 |



本轮 6 票已在 `xiaolin624/Orchara` 关闭（`closed / completed`），每票附实测证据评论：

| 票 | Issue | 分支提交 |
|---|---|---|
| T001 | [#2](https://github.com/xiaolin624/Orchara/issues/2) | `110bc6e` |
| T002 | [#3](https://github.com/xiaolin624/Orchara/issues/3) | `be97f87` |
| T003 | [#4](https://github.com/xiaolin624/Orchara/issues/4) | `62f96f6` |
| T004 | [#5](https://github.com/xiaolin624/Orchara/issues/5) | `71da374` |
| T005 | [#6](https://github.com/xiaolin624/Orchara/issues/6) | `d8e059b` |
| T008 | [#9](https://github.com/xiaolin624/Orchara/issues/9) | `a4eb7f8` |

代码在 `integration/foundation-p0`（已推送）。仓库 open issue 数由 121 降至 115。

## 环境事实（D00 实测 + 本轮补充）

- Go 1.27.3、PostgreSQL 16.4（用户态，仓库外 `E:\orchara-toolchains`）、Python 3.12.2、Node 24.13.0、npm 11.13.0。
- 隔离开发库：`tools/pg_dev.py` 在 `127.0.0.1:55432`，数据目录 `E:\orchara-pg`（仓库外）；
  本机 5432 的 `postgresql-x64-16` 属无关服务，探针从不使用。
- 无 Docker、无 Redis、WSL 有 Ubuntu-24.04 但**无 sudo**（第二环境用 `--target` 隔离安装）。
- GPU：RTX 3050 Laptop 4 GiB；未跑训练 → F14 `not_run`。
- 无模型供应商凭据 → 只能报告 stub。
- 锁版环境：`probes/agent_min/.venv`（48 包，全部 sha256 锁定）；SBOM/许可清单见 `reports/p01/sbom.json`。
- 本地开发口令只放在 **git-ignored 的 `product/.env.local`**（`ORCHARA_PG_PASSWORD`）。
  仓库、测试、报告均不含口令；`tools/pg_dev.py` 未设置时 fail closed。

## 代码复核修复（2026-11-01）

两轴复核（Standards / Spec）发现的 25 项已逐条修复，其中影响结论的：

- 凭据从代码/测试/报告移除，改由环境或 git-ignored 文件提供；泄漏断言改为匹配 DSN 形状。
- 漂移检查现在同时校验 `docs/spec` 与 `product/contracts`，并按 LF 归一化哈希
  （换行差异不再误报，契约被改动会失败）。
- 环境探测不再把本机 5432 的**无关** PostgreSQL 记为本项目能力，改为
  `foreign_postgres: present_not_owned`，自有集群用 55432。
- T003 的 `expected_sha256` 由 RFC 文本独立推导并真正断言（原断言恒真）。
- T005 的 `run/resume/approve` 现在真的以 `orchara_worker` 受限角色执行，并在运行路径上
  校验 `search_path`。
- T008 同时提供契约路径 `/health/live`、`/health/ready`；状态页的存活来自 `/health/live`，
  不再写死。
- T002 探针实际使用锁定栈（pydantic 结果模型 + jsonschema 报告校验），并新增
  `tools/bootstrap_probe.py` 让干净环境可复现。
- T004 新增 `tools/second_env_probe.sh`（可重跑）与 `reports/p01/sbom.json`；
  镜像 digest 明确记 `not_run`（本阶段未构建镜像）。

下面各票"证据"列中的提交号是**本轮修复前**的关闭提交；修复提交见分支历史。

## 关闭票内的两处缺口（已补齐）

复核时承认过：这 6 张票虽然关着，但按它们自己的文字还有两行没兑现。现已补上：

| 缺口 | 票 | 补法 | 证据 |
|---|---|---|---|
| 多套依赖锁 | T004 | 三套独立锁：运行时 48 包、测试 8 包、训练 58 包，全部 sha256 锁定并分别验证安装 | `probes/agent_min/requirements.lock.txt`、`tests/requirements-test.lock.txt`、`experiments/routing_sft/requirements-train.lock.txt`、`reports/p01/sbom.json` |
| supervisor 模块 | T008 | `cmd/supervisor` + `internal/supervisor`：advisory lock 保证单实例，只把死 worker 的 run 置为 `recovery_pending`，**从不重启**；worker 现在会注册与心跳 | `internal/supervisor/supervisor.go`、`db/migrations/0001_worker_lease.sql`、`tests/f00/test_supervisor_integration.py` |

训练锁只做到**解析**：安装验证记 `install_verified: false` / `not_run`，因为没有训练宿主与冻结数据，
不把"能解析"写成"装过"。

## T010 侦察发现（已修复）

在 WSL2 + runsc 上做 T010（越权文件/网络）侦察时，实测到一个真实缺陷：

```text
ORCHARA_TEST_SECRET=leakme runsc --network=none do sh -c 'env | grep ORCHARA_TEST_SECRET'
→ ORCHARA_TEST_SECRET=leakme        # 宿主环境变量被继承进沙箱
```

修复：沙箱启动统一包在 `env -i`，只传 `PATH` 与 `ORCHARA_APPROVED_` 前缀的批准变量；
其余由策略层拒绝。修后实测：宿主密钥不可见、批准变量可见、未批准变量被拒。

同批侦察还发现 `runsc do` 会以 root 挂载宿主文件系统（可读），因此用户脚本改用
**最小 OCI bundle**（自有 rootfs、uid 65534、零 capabilities、只挂批准输入）。见
`reports/p03/T010-isolation-matrix.md`。

## F00B 持久后台任务（1/5：T022）

T022（GitHub #23）已交付，状态 `passed`。真实 PostgreSQL 16.4 + 真实编译二进制 +
真实 HTTP/mTLS，无 mock、无跳过。决策边界见 [ADR 0004](docs/adr/0004-f00b-f02a-boundaries.md)。

| 项 | 结果 |
|---|---|
| 迁移 | `db/migrations/0009_background_jobs.sql`：`platform.artifacts`（+`revision` 源版本）、`platform.background_jobs`（+`finished_at`/`failure_class`/`last_error_at`/`result_event_digest`、`UNIQUE(kind,dedup_key)`、`result_event_id UNIQUE`、claim/lease/owner 索引），`orchara_worker`/PUBLIC 无 platform 权限；`0001→0009` 空库一次通过、重复 migrate 为 no-op |
| 对象存储 | `internal/objectstore`：本地 FS `staging/`/`sealed/`、类型化随机 key、临时文件+rename 原子写、20 MiB 上限、流式 SHA-256、`PutSealed` 填充预分配 key、未配置即 fail closed |
| 控制器 | `internal/background`：`EnqueueTx`（同事务 + dedup 意图比较）、`Claim`（`FOR UPDATE SKIP LOCKED` + 30s 租约 + token）、`Heartbeat`、`Complete`（token/租约/事件幂等 + artifact 服务端守卫/隔离）、`SweepExpired`（最多 3 次 + 指数退避）、owner-or-admin 读模型；无 `os/exec`，handler 为编译期映射 |
| 内部 API | `cmd/api` 独立 TLS listener（`RequireAndVerifyClientCert`、TLS≥1.2）；证书 Organization ∈ {supervisor,maintenance,worker} 且必须等于 `X-Internal-Role`；claim/heartbeat/result 仅 maintenance；公共 listener 不暴露 `/internal/v1/**` |
| 维护进程 | `cmd/maintenance run`：claim→立即心跳→handler→5s 心跳→提交，`--once/--kinds/--poll-interval`；无参数连通性检查行为不变 |
| 控制接口 | `cmd/admin background-jobs enqueue`：kind 白名单、形状错误 exit 2、dedup 冲突 exit 1、成功 JSON |
| 公共读 | `GET /api/v1/background-jobs/{job_id}`：owner-or-admin，其余 404，仅契约四字段 |
| 测试 | `tests/support/`（真实 PG/二进制/证书/对象/mTLS）+ `tests/f00b/` 8 文件 60 用例；`tests/f00b` 60 passed、`tests/f01` 155 passed、`tests/tooling` 21 passed、`tests` 全量 **589 passed**、`go vet`/`go test ./...`/`gofmt -l` 全绿、`sync_spec --check` no drift |
| 证据 | `reports/f00b/T022-background-jobs.md`（AC 逐条、命令原始结果、not_run 清单、下一票接口） |

未运行（如实）：Redis **传输适配器**（本机无实例；唤醒缝 `Notifier` 已交付，真实栈实测“每次通知
被丢弃后 PG 轮询仍 claim/完成”，不声称清空过 Redis 队列）、S3/MinIO、
签名 URL/`input_url`、上传 producer、真实 worker 协议、病毒查杀、字面 SIGKILL 注入；分别由
T024/T026/F03/F05 或专门故障票承接。本票不推送、不关闭 issue。

## F00B 后台文件与模型调用（2/3：T024）

T024（GitHub #25）已交付，状态 `passed`（真实 PostgreSQL 16.4 + 真实编译二进制 + 真实
HTTP/mTLS；模型侧为**明确的 stub profile**，真实 provider 在本环境 `not_run`）。决策边界见
[ADR 0004](docs/adr/0004-f00b-f02a-boundaries.md) §18–24。

| 项 | 结果 |
|---|---|
| 迁移 | `db/migrations/0011_job_files_and_models.sql`：`model_profile_versions`、`platform_model_calls`（+`params_sha256` 偏离）、`background_job_artifacts`（T024 归属表）；`request_id`→F05/F07、`evaluation_report_id`→T026 延期 FK 并在文件头/COMMENT 写明；`orchara_worker`/PUBLIC 无 platform 权限；重复 migrate no-op |
| 签名链接 | `internal/objectstore/sign.go`：HMAC-SHA256（`ORCHARA_OBJECT_SIGNING_KEY` ≥32B，空则 fail closed）、token 绑定 op+key+artifact_id+exp+max_bytes、PUT 仅 staging/GET 仅 sealed、TTL 可配；`cmd/api` 公共 `PUT/GET /object-links/{token}`（无会话、无列表、过期/篡改/换 key/换 op/sealed-PUT 全 404、超限 413、临时文件+rename）；日志对 token 脱敏 |
| 内部端点 | 5 个契约端点（upload/complete/download/status/model-calls）在 mTLS listener，全部校验活 attempt_token（stale=409）；upload 校验 kind/media/size/purpose；complete 同事务 CAS `uploading→scanning`（revision+1）+ `EnqueueTx(artifact_finalize)`；下载/状态按 input/result/归属表 scope，越界与不存在同为 404 |
| 模型代理 | `internal/modelproxy`：Provider seam + 确定性 stub（仅 dev-stub/capacity-stub 且候选 provider=stub；输出标注 stub、fixture hash/bytes/usage 来自真实 fixture 字节）+ real 路径（`ORCHARA_MODEL_ENDPOINT` 未配置→503 `provider_not_configured`；配置但无厂商 adapter→503 `provider_adapter_unavailable`；密钥仅经 `secret_ref` 在 Go 内解析，响应/日志不含密钥或引用）；两次拒绝都发生在 ledger 行之前 |
| 记账 | `platform_model_calls`：owner/job/candidate、`scope_key=job`、逻辑键+params 摘要、`physical_attempt`（job 行锁内 max+1）、prepared→dispatching→succeeded/failed、真实 tokens、decimal reserved/charged（整数 1e-8 单位，无 float）、provider 响应 sealed 为 ready artifact；同键同参幂等、同键异参 409、失败重试新 attempt |
| handler | `internal/background/handlers/model_validate.go`：经 `background.JobScope` 下载 fixture、调 model-calls、上传/complete/轮询报告、`succeeded` 带 `result_artifact_id`；兼容性失败是 job 成功 + report 失败，provider 未配置是 business 失败；registry + `retryableByKind` 同变更扩展；服务端 Complete 额外校验结果 artifact ready/owner/linked |
| lease/API | `BackgroundLease.input_url` 为真实签名 sealed URL（无输入/未 ready/无签名 key 时 null）；`Upload.upload_url`/`Download.url` 用 `ORCHARA_PUBLIC_URL` 铸造 |
| 测试 | `tests/support/t024.py` + `tests/f00b/test_t024_*.py` 5 文件 **28 用例**（迁移 6、上传/最终化/下载 6、签名链接 5、模型调用 8、端到端 3；复核修复后新增丢唤醒实测与合成夹具负面用例）；`tests/f00b` 86 passed（本票完成时）、`tests/f02a` 45 passed、`tests/f01` 155 passed、`tests/tooling` 21 passed、`go vet`/`go test ./...`/`gofmt -l` 全绿、`sync_spec --check` no drift |
| 证据 | `reports/f00b/T024-job-files-and-models.md`（AC 逐条、命令原始结果、stub/real 分报、not_run、下一票接口） |

未运行（如实）：真实模型 provider 集成与厂商协议 adapter（本机无 endpoint/凭据，且 F06 才拥有
adapter；本票交付诚实 503 边界与不泄露密钥的路径）、病毒查杀/深格式扫描（F05/T028）、Redis
传输适配器（唤醒缝已交付，适配器属后续票）；T026 的运行时双池属其票面，本票测试用两个真实进程
驱动 handler + finalize。本票不推送、不关闭 issue。

## F02A 草稿与版本读模型（1/3：T023）

T023（GitHub #24）已交付，状态 **`partial`**（2026-11-02 两轴复核修复更正；T027 后曾误记为
`passed`）。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP；页面 JS 用 Node 真实执行。
草稿保存/读回/CAS/冲突/版本读模型/编辑器主体全部真实运行通过。票面 AC3“非法草稿可保存但
**不能调试/提交**”：T025 的 validate 与 T027 的 debug 拒绝是真实端点证据；但 **submit 拒绝
未实现**——`POST /submit` 的 404 只证明 F02B 端点不存在，不能当作“提交被拒绝”。因此 AC3 与
整票保持 `partial`，submit 侧由 F02B 闭环（见 `reports/f02a/T023-drafts-and-editor.md`
§2/§8）。决策边界见 [ADR 0004](docs/adr/0004-f00b-f02a-boundaries.md) §12–17。

| 项 | 结果 |
|---|---|
| 迁移 | `db/migrations/0010_application_drafts.sql`：`application_drafts`（+`execution_revision` 执行内容计数器、jsonb object CHECK）、`publication_reviews`（`report_id` 无 FK，T026 补）、`application_versions`、`version_dependencies`；`applications.active_version_id` 复合 FK（`ON DELETE RESTRICT`）；`orchara_worker`/PUBLIC 无 platform 权限；重复 migrate no-op |
| 草稿聚合 | `internal/catalog/draft.go`：`CreateApplication`（application + revision=1 空草稿单事务）、`GetDraft`（owner-or-grant，强 ETag）、`SaveDraft`（可见性→owner→428/412→422→`WHERE revision=` CAS；两计数器）、`ValidateDraftInput`（三键/对象/大小上限/空卡 `{}`/完整卡 examples≥2）、`CheckExecutable`（P05 校验器定位错误） |
| 版本读模型 | `internal/catalog/versions.go`：`ListVersions`/`GetVersion` 只读不可变发布行、绝不 join draft；ACL 与 cursor/LIMIT 同层（先过滤后分页）；`version_no DESC, id DESC` 不透明游标；不可见/归档 404；与 `active_version_id` 三者一致 |
| 校验器移植 | `internal/workflow`（P05 Go 直移植 + 嵌入 rules.json + 40 例 corpus 对拍 verdict/reasons/hash/canonical，rules 与 probe 逐字节一致断言）、`internal/jcs`（P01/T003 移植 + 40 条 RFC 向量含错误码）；probes 未改 |
| API | `POST /api/v1/applications`（201）、`GET/PUT /applications/{id}/draft`（ETag/If-Match，428/412/403/404/422/413）、`GET /applications/{id}/versions`、`GET /versions/{version_id}`；新增 `application:write` scope（编辑需 read+write） |
| 编辑器 | `apps/web/src/editor.html` + `assets/editor.js`：用途卡六字段、definition/layout 文本域、revision、412 冲突面板（重载服务器版本/下载本地副本，永不自动覆盖）、刷新加载；`apps.html` 新建应用 + 编辑入口；`assets/app.js` 增 `apiFull`（`api` 形状不变） |
| 测试 | `tests/support/f02a.py` + `tests/f02a/` 9 文件 **45 用例**（迁移 8、草稿 11、执行 revision 3、并发 2、ACL 4、scope 2、版本 6、非法草稿 2、页面 7）；`tests/f02a` 45 passed、`tests/f00b` 60 passed、`tests/f01` 155 passed、`tests/tooling` 21 passed、`go vet`/`go test ./...`/`gofmt -l` 全绿、`sync_spec --check` no drift |
| 证据 | `reports/f02a/T023-drafts-and-editor.md`（AC 逐条、命令原始结果、契约缺口、not_run、下一票接口） |

未运行（如实）：浏览器 DOM/HCI（无 headless browser；页面为真实 HTML/JS + Node 执行逻辑）；
**submit 拒绝**（F02B；`/submit` 端点不存在，404 不是拒绝证据）；manifest/hash/对象写入属
T025/T024。`debug` 拒绝已由 T027 交付。本票不推送、不关闭 issue。

## F02A 执行清单固化（2/3：T025）

T025（GitHub #26）已交付，状态 **`partial`**（复核修复 2026-11-02 更正）。manifest 组合/hash、
依赖闭包、validate 端点、密封去重与复核机制均真实运行通过；票面第 3 条“worker 能复核规范字节”
只交付了机制，真正 worker 未构建（F03/F04A 拥有），因此 AC3 记
`partial (mechanism delivered; worker not built)`，整票随之 `partial`。真实 PostgreSQL 16.4 +
真实编译二进制 + 真实 HTTP + 真实文件系统对象字节；无 mock、无跳过。决策边界见
[ADR 0004](docs/adr/0004-f00b-f02a-boundaries.md) §25–30。

| 项 | 结果 |
|---|---|
| 迁移 | `db/migrations/0012_manifest_and_tool_versions.sql`：`tool_versions`（契约基线、F05 写、T025 只读模型）、`manifest_artifacts`（T025 去重表，PK `(application_id, execution_revision, manifest_sha256)`，FK artifact）；权限沿用 0007–0011；重复 migrate no-op |
| Manifest | `internal/compiler`：schema 精确 13 字段、`source_revision=execution_revision`、固定 `compiler_version`/`state_schema_version`、worker digest/runtime lock 部署输入 fail closed；`tool_artifacts`/model/subapp 排序去重、`connector_policies=[]`；JCS 规范字节 + 外置 `manifest_sha256`；不含 layout/身份/审核状态/hash |
| 依赖闭包 | `internal/catalog/manifest.go`：model/tool/subapp 引用提取（pointer 精确到 config 字段）、真实表就绪判定（model passed；tool ready+approved+image；作者私有 debug 规则）、sub-app ACL 归属、`version_dependencies` + 自调用环检测；错误码 `capability_card_incomplete`/`dependency_invalid_id`/`dependency_not_found`/`dependency_not_ready`/`dependency_revoked`/`dependency_not_visible`/`dependency_cycle` |
| API | `POST /api/v1/applications/{id}/validate`：owner-only（其余 404）、CSRF、`application:write` scope；200 `{valid,errors[]}` 定位错误；valid:true 返回 `X-Manifest-SHA256`（契约缺口，非契约扩展）；503 `compiler_unconfigured`/`object_store_unavailable`；不创建 version/review/snapshot/job |
| 密封/去重 | 规范字节 JCS 原样写 `sealed/<64hex>`（state=ready、`execution_manifest`、application/json），按三元组 advisory lock 去重；重复 validate 不写第二个对象；同一 hash 的 artifact 供 T027/F02B 复用 |
| 复核机制 | `compiler.VerifyManifestBytes(objects, ArtifactRef)`：先按字节重算 SHA-256/大小比对记录值，再要求精确 JCS 形式，最后严格解码复核 schema；golden 通过 + **真实 sealed-object 就地篡改**（`TestVerifyManifestBytesRejectsASealedObjectTamperedInPlace`，typed `ErrManifestHashMismatch`）+ 错误 hash/非规范/缺字段/大小不符/缺对象/未配置 store 全拒绝；真正 worker 属 F03/F04A（未构建，AC3 partial） |
| Golden | `tests/contracts/snapshots/`：`sales` 与 `layout-only` 双胞胎（definition/card/layout + manifest.json/jcs/sha256）；Go 生成/漂移测试、Verify 测试；HTTP 测试断言响应头 == golden sha、密封字节 == `manifest.jcs` |
| 测试 | `tests/support/t025.py` + `tests/f02a/test_t025_*.py` 4 文件 **16 用例**（迁移 6、validate 3、manifest 2、dependencies 5；原先的 Python 单字节 SHA 比较不调用 verifier、已删除，边界移入 Go）；`tests/f02a` 62 passed（本票完成时）、`tests/f00b` 86 passed、`tests/f01` 155 passed、`tests/tooling` 21 passed、`go vet`/`go test ./...`/`gofmt -l` 全绿、`sync_spec --check` no drift |
| 证据 | `reports/f02a/T025-manifest.md`（AC 逐条、命令原始结果、契约缺口/占位输入、not_run、T027/F03 接口） |

未运行（如实）：真正 worker 的领取/解析/复核（F03/F04A；本票只交付 `VerifyManifestBytes` 机制与
真实字节/篡改/负例测试，AC3 = `partial`）；真实 worker 镜像与 state schema 制品
（F04A/F15A/F03；digest 是部署方占位输入）。全量 `pytest tests` 已在复核修复收尾运行（见本节
之后的复核修复记录）。本票不推送、不关闭 issue。



## F00B 评测池、报告守卫与孤儿清理（3/3：T026）

T026（GitHub #27）已交付，状态 **`partial`**。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实
HTTP/mTLS/对象字节；评测执行器是**明确的合成占位**（`synthetic-jcs-case/1`，F13A 才有真实
runner/checker），且 `kind=application` 端到端执行同样依赖 F13A 的 runner（T027 已补齐 snapshot
表/FK，绑定结构可用）。复核修复又明确第三条：票面切片边界要求区分“业务 job/evaluation/
**deletion** 状态”，而 0013 不建 F07 的 deletion 模型——deletion 状态属 F07、未实现。因此三条
`partial` 原因是：合成 runner（F13A）、application-kind 真实 runner（F13A）、deletion 状态（F07）。
模型 kind 的“池不饿死 finalize、report failed 与 job failed 可区分、owner/admin 读、孤儿清理”
全部真实跑通。决策边界见 [ADR 0004](docs/adr/0004-f00b-f02a-boundaries.md) §31–37。

| 项 | 结果 |
|---|---|
| 迁移 | `db/migrations/0013_evaluation_pool.sql`：`evaluation_jobs`/`evaluation_reports` 契约基线（kind XOR、state/status CHECK、report→artifact FKs）；`snapshot_id` 裸 uuid 无 FK（T027/0014 补 `evaluation_jobs_snapshot_fkey`，COMMENT 写明）；补齐 0010/0011 延期的 `publication_reviews.report_id`、`model_profile_versions.evaluation_report_id` FK；owner/state 与 report 索引；`orchara_worker`/PUBLIC 无 platform 权限；重复 migrate no-op |
| 控制接口 | `cmd/admin evaluations enqueue --kind model/application --owner-login ... --dataset-artifact-id ... [--model-profile-version-id] [--snapshot-id]`：一个事务 = `EnqueueTx(application_evaluate, target=eval, input=dataset, dedup=eval id)` + evaluation 行；model/application 组合、UUID、login 语义校验；shape exit 2、数据库拒绝 exit 1、均无部分行 |
| 评测不饿死 finalize | `cmd/maintenance run` 双池：system_io=artifact_finalize（默认 2 槽）、work=其他 kind（默认 1 槽）；`--kinds` 只收窄仍按池划分；`--once` 是跨池 drain（activity epoch，任 job 开始/结束清空标记，所有槽同 epoch 空才退出）；slots/wait flags + 五个环境变量；summary 聚合、jobReport 带 pool |
| `application_evaluate` | handler 经 job-scoped binding 读取 evaluation 事实；dataset uploading/scanning 时 `artifact-status` 轮询 + heartbeat（可配 timeout，可重试 `dataset_wait_timeout`）；signed download → 合成 JCS case 评测 → signed report upload/complete → 等 finalize ready → `succeeded` + result artifact；等待只在 work 池，不占 system_io |
| 完成守卫 | `Complete` 同事务：锁 evaluation、核 dataset ready/sha、解析候选/snapshot manifest、读 sealed report（≤1 MiB、重算 sha256、严格 JSON）、绑定 manifest/dataset/identity、passed/failed/not_run 规则；通过才插 immutable report + evaluation succeeded + job succeeded；绑定/格式/非法 passed → job failed（`report_binding_mismatch`/`report_invalid`）+ evaluation failed + **无 report**；sealed 读失败是 `transient_storage_io`（可重试，evaluation 保持 running） |
| 状态区分 | 业务评测失败 = report `status=failed` + job/evaluation `succeeded`；基础设施失败 = job failed + evaluation failed + 同 error_code、无 report；`not_run`/空 case/空 check 永不 passed；`application_evaluate` 重试表只含 `transient_storage_io`/`transient_internal`/`lease_expired`/`dataset_wait_timeout` |
| 读权限 | `GET /api/v1/evaluations/{id}`（EvaluationJob 六字段）与 `GET /api/v1/evaluation-reports/{id}`（Report 四字段）：owner 或 admin 200，其余 404（不枚举）；report 的 owner 由产生它的 evaluation join；不返回 dataset/candidate/snapshot/object key/正文；复核修复后 API key 必须先携带 `application:read`（403 `insufficient_scope`，会话不受限），owner/admin ACL 仍是内容权威 |
| 孤儿清理 | `cmd/maintenance sweep --orphan-age D [--dry-run]`：只删无 live uploader 的 staging 与无任何 artifact 行引用的 sealed；被引用的 sealed（含 deleted 行）永不删（F07 负责）；mtime 年龄护栏、幂等、逐条 reason、重复 sweep 删 0 |
| 测试 | `tests/support/t026.py` + `tests/f00b/test_t026_*.py` 7 文件 **40 用例**（迁移 7、CLI 12、双池 2、失败区分 6、报告绑定 8、权限 3（复核修复新增 scope）、sweep 2）；全部真实 PG/二进制/HTTP/mTLS/对象字节；`go vet`/`go test ./...`/`gofmt -l` 全绿、`sync_spec --check` no drift |
| 证据 | `reports/f00b/T026-evaluation-pool.md`（AC 逐条、原始命令/结果、池/env/端点、stub/real 分报、T027 交接） |

未运行/部分（如实）：`kind=application` 端到端评测（T027 已建 `execution_snapshots` 并补 FK，
绑定结构可用；真实 runner/checker 仍属 F13A）；真实评测 runner/checker（F13A；本批是合成占位，
不是真实模型通过）；**deletion 状态区分（F07；0013 不建 deletion 模型，票面该维度未实现）**；
API key 的评测 scope（复核修复后强制平台既有 `application:read`；更细的专用 scope 仍属 F13A/
契约修订）；全量 `pytest tests` 已在复核修复收尾运行。本票不推送、不关闭 issue。

T027 追加说明（2026-11-02）：0014 已建 `platform.execution_snapshots` 并补齐
`evaluation_jobs_snapshot_fkey`，因此 `kind=application` 的 `resolveBoundManifest` 现在能解析真实
snapshot 的 `manifest_sha256`（仍要求 snapshot owner == 评测 owner；他人 snapshot 仍
`evaluation_binding_missing`）。“结构上不可执行”的状态已解除；真实 runner/checker 仍属 F13A，故本票
保持 `partial`。T026 的 application-kind 测试已改为 seed 真实 snapshot 行（schema 收紧的预期适配）。

## F02A 私有调试快照与排队 Run（3/3：T027）

T027（GitHub #28）已交付，状态 **`passed`**（本票范围 = **提交与排队**）。真实 PostgreSQL 16.4 +
真实编译二进制 + 真实 HTTP + 真实文件系统对象字节；页面 JS 用 Node 24 真实执行；无 mock、无跳过。
边界（如实）：领取/执行/事件/结果/终态属 F03；全量 `pytest tests` 已在复核修复收尾运行。
决策边界见 [ADR 0004](docs/adr/0004-f00b-f02a-boundaries.md) §38–44。

| 项 | 结果 |
|---|---|
| 迁移 | `db/migrations/0014_execution_snapshots_and_runs.sql`：`execution_snapshots`/`runs`/`jobs`/`run_artifacts`/`idempotency_keys` 契约基线（含 kind/身份 XOR 与 runs 三条 CHECK、`one_live_job_per_run`/`jobs_ready`/`one_active_top_run_per_conversation` 索引）；**补齐 T026 延期 FK `evaluation_jobs_snapshot_fkey`**；`runs.request_id`/`conversation_id` → F05/F07、`jobs.current_attempt_token`/`run_artifacts.attempt_token` → F03 三个延期外键在迁移头/COMMENT 写明；新增 `idempotency_keys.response_json`（偏离，F05/F06 继承）；64-hex/jsonb/draft_revision>0 收紧；`orchara_worker`/PUBLIC 无 platform 权限；重复 migrate no-op |
| debugDraft | `POST /api/v1/applications/{id}/debug-runs`（owner-only；会话 CSRF/API key `application:write`）：`Idempotency-Key` 必需（400/409/replay）；`draft_revision` 必须等于当前 revision（409 `revision_conflict`），事务内 `FOR UPDATE` 复核；T025 `ValidateExecution(PrivateDebug:true)` 复用（作者自有 ready 工具无需批准，他人未批工具 `dependency_not_visible`）；定义/卡片/依赖/预算/artifact 全部 422 定位且零行；单事务写私有 snapshot + 输入 artifact（JCS 规范字节、ready、真实 sha256）+ `runs(kind=debug, queued, idle, revision=1, next_event_seq=1, budget/currency 来自 policy)` + `jobs(start, ready)` + `run_artifacts` + 202 收据；**不建** version/review/request/conversation/report/background job |
| getRun | `GET /api/v1/runs/{run_id}`：owner-only（管理员/read grant/他人/畸形/未知一律 404），契约 Run 10 字段；API key 需 `application:read`；无 events/SSE/claim/cancel（F03） |
| 幂等 | canonical digest（JCS：application_id + artifact_ids 排序 + draft_revision + input）；同 key 同摘要 → 原 202 Run（不重编译/不写对象/不建行，改草稿后仍返回原 Run）；异摘要 409 `idempotency_conflict`；缺/空/重复 header 400；并发重复在唯一键上收敛为恰好一个 Run（输家删除自己的密封输入）；过期收据仍只重放、绝不二次执行 |
| 契约缺口 | `Run` 无 `manifest_sha256`/`draft_revision` 且契约无 getSnapshot：202/200 用非契约 `X-Manifest-SHA256`（同 T025 §26 先例）；页面展示 snapshot id + hash + 冻结 revision（客户端事实）并注明执行属 F03 |
| Web | `apps/web/src/debug.html` + `assets/debug.js`：当前 revision 预填、JSON 输入、≤5 artifact、每次提交一个 `Idempotency-Key`、CSRF、`method="post"`；显示 state/kind/Run.revision/冻结 revision/snapshot/hash 并轮询 `GET /runs/{id}`（终态或 40 次停止，本批停在 queued）；编辑器 `#debug-entry` 与 apps 列表“调试”入口 |
| 测试 | `tests/support/t027.py` + `tests/f02a/test_t027_*.py` 7 文件 **38 用例**（迁移 10、提交 4、不可变 2、权限 2、拒绝 7、幂等 7、页面 6）+ `internal/catalog/debug_test.go` 6 个 Go 单测；`tests/f02a` **99 passed**（本票完成时 100；复核修复移走一个无效 T025 篡改测试）、`tests/f00b` 129 passed（复核修复后）、`tests/f01` 155 passed、`tests/tooling` 21 passed、`go vet`/`go test ./...`/`gofmt -l` 全绿、`sync_spec --check` no drift（全部真实 PG/二进制/HTTP/对象字节） |
| 证据 | `reports/f02a/T027-debug-submit.md`（AC 逐条、原始命令/结果、契约缺口、not_run、F03/F02B 接口）；T023 AC3 的边界补记在 `reports/f02a/T023-drafts-and-editor.md` §8（复核修复后为 `partial`） |

未运行（如实）：F03 的领取/租约/attempt/事件/SSE/结果/退出 finalizer/cancel/终态（本票的 run 停在
queued、start job 停在 ready；F03 必须补两个 attempt 外键并先调 `VerifyManifestBytes`）；F02B 的发布
审核/version 写入/`submit`（测试断言仍 404）；F05/F07 的 requests/conversations 与 FK、幂等收据清理；
浏览器 DOM 渲染（与 F01/T023 相同）；真实 worker 镜像（占位 digest）；
F13A 的真实评测 runner。全量 `pytest tests` 已在复核修复收尾运行。本票不推送、不关闭 issue。

## F05 用户上传、格式准入与不可覆盖封存（1/N：T028）

T028（GitHub #29）已交付，状态 **`passed`**（本票范围 = **上传 + 准入 + owner-only 读取**）。
真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP + 真实文件系统对象字节 + 真实 HMAC 签名
staging/sealed URL + 真实 `cmd/maintenance run --once --kinds artifact_finalize`；无 mock、无跳过。
复用 T022 的 `artifact_finalize` 流水线（未写第二个 finalizer）。边界（如实）：F07 删除模型、
病毒查杀、XLSX 工作表/行级深解析、浏览器 DOM 分别见报告 §4/§5 的 `not_run`/后续票。

| 项 | 结果 |
|---|---|
| 迁移 | `db/migrations/0015_artifact_admissions.sql`：唯一新表 `platform.artifact_admissions`（PK/FK artifact、declared/sniffed media type、detected_format、verdict、reason、checked_at；accepted 必有 sniff、rejected 必有 reason 的 CHECK）；迁移头列出偏离与 owner（F07/T037）；无 deletion/requests/conversations/tool 表；`orchara_worker`/PUBLIC 无 platform 权限；重复 migrate no-op |
| createUpload | `POST /api/v1/artifacts/uploads`：会话 CSRF / API key `application:write`（普通 user 可上传，不要求 creator）；`UploadInput` 精确校验（4 类 media_type 允许清单、size 1..20971520、4 个 purpose）；预分配不同 staging/sealed key，`uploading` revision=1；201 `Upload` 只含短时 staging PUT URL（max_bytes=声明 size）+ expires_at；sealed key 永不发 PUT |
| completeUpload | `POST /api/v1/artifacts/{id}/complete`：owner-only（他人含管理员 404）；CAS `uploading→scanning`（revision+1）+ 同事务一个 `artifact_finalize` job（expected_revision=新 revision、`artifact:<id>:<sha>` dedup）；同 hash 在 scanning/ready 幂等且不建第二个 job；异 hash 409 `artifact_hash_conflict`；deleted/quarantined 409 `artifact_conflict`；不读对象存储、不激活 |
| 格式准入 | handler 在 hash/大小通过后读取**本尝试** sealed 对象并持久化 admission：text/UTF-8、CSV 解析、JSON `json.Valid`、XLSX zip 宏/条目≤512/解压≤64 MiB/路径穿越/必需 workbook；不一致/空/非法/不支持 → `artifact_format_rejected`（quarantine-implying、business、不可重试）；staging 缺失对 user upload → `artifact_upload_incomplete`；非 user-upload purpose 不走本准入（T024/T026 语义不变）；激活仍只由完成守卫决定；新增 Go 分类器单测 |
| 读取 | `GET /artifacts/{id}` 4 字段（quarantined 附非契约 `X-Artifact-Quarantine-Reason`）；`GET .../download` ready 才签 sealed GET、未 ready 409 `artifact_not_ready`、隔离/他人/已删 404；`GET .../content` 同一 ACL、`Content-Disposition: attachment`、流式复制不把 20 MiB 读进内存；管理员不是任意用户正文读者（404） |
| Web | `apps/web/src/attachments.html` + `assets/attachments.js`：选文件/类型/用途 → 浏览器 SHA-256 → createUpload → 无凭据 PUT staging → complete → 轮询 state；ready 才显示下载（点击时才取新链接）；quarantined 安全中文文案 + reason；全部 `textContent`，无 URL/localStorage 凭据；apps/debug 导航入口 |
| 测试 | `tests/support/t028.py` + `tests/f05/test_t028_*.py` 6 文件 **31 用例**（迁移 4、上传 6、隔离 10、ACL 5、删除竞争 1、页面 5）；核心 AC“校验后覆盖 staging 不改变 sealed”与“同 hash 幂等/异 hash 409 + 单 job”逐字节/逐行断言；五套件门禁 `pytest tests/f05 tests/f00b tests/f02a tests/f01 tests/tooling -q` = **446 passed**（f05 31 / f00b 135 / f02a 104 / f01 155 / tooling 21），`go vet`/`go test ./...`/`gofmt -l` 全绿、`sync_spec --check` no drift |
| 证据 | `reports/f05/T028-artifact-uploads.md`（AC 逐条、命令原始结果、契约缺口、`not_run`、F07/F03 接口） |

未运行/边界（如实）：F07 的 DELETE 端点/deletion_jobs/副本清理/tombstone 传播（本票只用 F07 将写的
`deleted_at/state=deleted/revision+1` 列做真实 guard 竞争测试，不实现删除）；病毒查杀与 XLSX 工作表/
行级/公式深解析（属 F13A/`document_ingest`）；管理员诊断授权正文读取（F07/F09）；浏览器 DOM；全量
`pytest tests`（本票按 brief 跑五套件，见报告 §3.3）。本票不推送、不关闭 issue。

## F03 领取、租约与退出证据（1/N：T029）

T029（GitHub #30）已交付，状态 **`passed`**（本票范围 = **领取 + 租约/心跳 + 退出证据记录 +
owner-only Run 列表与运行页面**）。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 mTLS 客户端证书 +
真实 HTTP + 真实文件系统对象字节 + 真实 HMAC sealed GET 链接 + 真实并发；无 mock、无跳过。
边界（如实）：result 提议/事件/统一 finalizer/resume/cancel/recovery/终态/SSE 属 T031–T038；发布版
Run 入口属 F02B/F05；真实 supervisor/worker 进程属 F04A/F15A。决策见报告 §4/§5。

| 项 | 结果 |
|---|---|
| 迁移 | `db/migrations/0016_execution_claims_and_quota.sql`：`run_attempts`/`quota_counters`/`quota_allocations` 契约基线；补齐 T027 延期的 `jobs_current_attempt_token_fkey`、`run_artifacts_attempt_token_fkey`；部分唯一索引 `run_attempts_one_live_per_run`（`exited_at IS NULL AND revoked_at IS NULL`）；租约/心跳索引 `run_attempts_live_lease`、`run_attempts_live_job`；种子 `('global','execution_slots',0,2)`（容量是部署 knob，`ON CONFLICT DO NOTHING`）；`orchara_worker`/PUBLIC 无 platform 权限；重复 migrate no-op；未建 requests/conversations/events/node_attempts/operations/approvals |
| internal/execution | store + service 两层；**单一数据库时钟**（租约/心跳/过期/退出全部 SQL `now()`）；claim 一个事务：`FOR UPDATE OF j,r SKIP LOCKED` 选 ready job（run queued、snapshot compiler 受支持）→ 配额行锁 + `used<capacity` 原子自增 → holder `job:<id>` 分配（释放后复用/重新武装）→ attempt 行（新 token、`now()+ttl`）→ job `leased` + token → run `running`/phase `executing`/`started_at`/`revision+1` → `compiler.VerifyManifestBytes`（只信密封字节）→ 真实 sealed GET 链接 → commit；任何失败整笔回滚 |
| claimJob | `POST /internal/v1/jobs/claim`，supervisor only（worker/maintenance 403）；body 严格校验（`worker_id`、非空/唯一/可打印 `supported_compilers`、`execution_unit_id`）；200 契约 `Lease` 14 字段（`protocol_version="1.0"`、manifest=snapshot JSONB 投影、resume 对 start 为 null）；无兼容 job/无容量 → 204 无 body 且零变化；字节/签名不可用 → 503 `object_store_unavailable`/`object_signing_unavailable` 且无租约 |
| heartbeat | `POST /internal/v1/runs/{run_id}/heartbeat`，supervisor/worker 可、maintenance 403；token 必须绑定该 run/job 且 live（过期/撤销/错 run/错 unit → 409 `stale_attempt`）；`lease_deadline=now()+ttl`（`ORCHARA_EXECUTION_LEASE_TTL`，默认 30s）；`checkpoint_connections` 校验形状后持久化（缺省不覆盖）；`HeartbeatReply`：真实 lease_deadline、`cancel_requested` 来自 `runs.cancel_requested_at`、`policy_revision`=acl epoch、`budget_remaining`=`runs.budget_limit::text`（usage ledger 后续票，报告中明确） |
| confirmAttemptExit | `POST /internal/v1/attempts/{attempt_id}/exited`，supervisor only；校验 live + execution_unit；持久化 `exited_at/exit_evidence`、撤销 token、`released_at IS NULL` 下只释放一次配额（`used-1`）；`evidence.started=false` → job `ready`、run `queued`/phase `idle`/`revision+1`（`started_at` 不变，未启动由证据证明）；其他 evidence（含缺省 started）→ job `held`、run 非终态（T031 finalizer 边界）；重复相同 evidence 幂等 200、冲突 409 `attempt_evidence_conflict` |
| 配额/过期 | 唯一维度 `global/execution_slots`；一次 claim 一个槽、一次 exit 释放一次；容量 1 × 10 并发恰 1 个 200；**过期租约不自动回收**：心跳/退出 409，claim 不抢 `leased` job，过期 attempt 的槽保持占用；`recovery_pending`/sweep/撤销旧 token/槽位回收属 T031（EXECUTION §3 保守恢复） |
| listRuns | `GET /api/v1/runs` owner-only（管理员只看自己的；无 grant 路径），`created_at,id DESC` 稳定 keyset + opaque cursor（过滤先于分页），`{items,next_cursor}` 契约；limit 缺省 20/非法 422、坏 cursor 400；API key 需 `application:read`；复用 T027 `catalog.Run` 投影（抽出 `runColumns/scanRun`，未复制第二份） |
| 详情扩展 | `GET /api/v1/runs/{id}` 在尝试存活时附非契约头 `X-Run-Attempt-Started-At`/`X-Run-Lease-Deadline`（Run schema 无租约字段；`X-Manifest-SHA256` 先例；attempt token 绝不暴露） |
| Web | `apps/web/src/runs.html` + `assets/runs.js`：owner-only 列表/分页、点击详情、state/phase/kind/revision/snapshot、manifest hash、运行中的真实 started_at/lease_deadline；无租约时明确“不伪造进度”；全部 `textContent`；debug 页新增 phase/租约行与 `/runs` 链接，apps 页新增 `/runs` 导航 |
| 测试 | `tests/support/t029.py` + `tests/f03/test_t029_*.py` 9 文件 **51 用例**（迁移 7、claim 6、heartbeat 9、exit 10、listRuns 3、并发 3、原子性 5、不变量 3、页面 5）+ `internal/execution` 4 个 Go 单测；`tests/f03` **51 passed**，`go test ./...` 全绿，`go vet ./...`、`gofmt -l .` 无输出，`sync_spec --check` no drift（全部真实 PG/二进制/mTLS/HTTP/对象字节/并发） |
| 证据 | `reports/f03/T029-claim-and-lease.md`（AC 逐条、原始命令/结果、契约缺口、not_run、T030/T031 接口） |

## F05 预置销售统计工具（2/N：T030）

T030（GitHub #31）已交付，状态 **`passed`**（本票范围 = **可信预置工具执行 + Decimal 统计 +
具体输入错误 + 用户可见运行/下载/诊断路径**）。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP
+ 真实文件系统对象字节 + 真实 HMAC 签名 URL + 真实 `cmd/maintenance run --once` 双池
（tool_run work pool + artifact_finalize system_io pool）+ 真实 Python 3.12 子进程；无 mock、无跳过。
F03 的 result/finalizer/events/SSE/cancel/recovery 尚不存在，因此本票走 ADR 0005 记录的
**非契约桥**：一个 `tool_run` 后台 job，而不是伪造 Run；T033/F04A 再把预置工具绑定到
Run/沙箱协议，T037 再开放任意工具构建。边界见报告 §5/§6。

| 项 | 结果 |
|---|---|
| 迁移 | `db/migrations/0017_tool_run_kind.sql`：guarded、幂等 drop/recreate `background_jobs_kind_check`，只加入第九个 kind `tool_run`；无新表、无新业务行、无权限变化；`orchara_worker`/PUBLIC 仍无 platform 权限；重复 migrate no-op |
| 后台 kind | `internal/background/{job.go,complete.go,artifacts.go}`：`AllKinds`/`KnownKind`/数据库 CHECK 同步；retry 表 `tool_run` = transient_storage_io/transient_internal/lease_expired/tool_timeout/tool_crash（≤3 次），工具拒绝码与输出违规码 business；`tool_run` 在 work pool（非 system_io）；producer purpose 加入 tool_summary/tool_artifact/diagnostics/log；新增 `guardToolRunCompletion`，成功必须命名本 job 的 ready summary artifact |
| API | `POST /api/v1/tool-runs`（会话 CSRF / API key `application:write`；`Idempotency-Key` 必需；body 精确 tool+artifact_id；202 `{id,state,tool,artifact_id}`）；同 key 同 body 回放已存 body、异 body 409 `idempotency_conflict`；非本人/已删/畸形 id 404 `resource_not_found`；未 ready 409 `artifact_not_ready`；非 CSV/XLSX 422 `validation_failed`；receipt+job 同事务。`GET /api/v1/tool-runs/{job_id}` owner-only，`{id,state,error_code,artifacts:[...]}`，join `background_job_artifacts`，不含 object key |
| 执行器 | `internal/tools`：固定 argv（无 shell）、裁剪环境、临时 workspace、输入复制只读（Windows DOS read-only / POSIX 0444，OS 边界见 ADR）、超时 30s 默认（环境可调、≤10m）、stdout/stderr 各 64 KiB 只作 log artifact、严格 output.json 成功/拒绝形状、未知码拒绝、产物准入（相对/目录内/普通文件/无 symlink/`..`/非空/≤20 MiB/≤16 个/SHA-256），`output.json` 未列出的文件不登记；Python 与脚本路径来自 `ORCHARA_PYTHON`/`ORCHARA_PRODUCT_ROOT` 部署配置 |
| 工具 | `worker/platform_worker/nodes/sales_stats.py`：Python 3.12 stdlib only；CSV 与 XLSX（zip+XML，含 sharedStrings）读取；表头精确 order_id/date/region/amount；Decimal 金额（输出两位小数字符串，永不 float）；产出 summary.json/summary.csv 仅在 artifacts 目录；拒绝缺列/多列/乱序、重复订单、负值/非十进制、空地区、非 ISO 日期、公式、非 UTF-8、宏、DTD/实体、zip 穿越/条目/解压/压缩比、工作表/行/单元格上限、错误单元格类型 |
| handler | `internal/background/handlers/tool_run.go`：job-scoped Status/Download/FetchURL 取输入 → 执行器 → 校验销售 Schema/Decimal 总和 + summary.json 与 data 规范化一致 → 上传 tool_summary/tool_artifact/log 并等待 ready → 成功提交 summary；工具拒绝/超时/崩溃/IO 失败上传安全 diagnostics+log 后失败，失败永不激活 result artifact |
| Web | `apps/web/src/attachments.html` + `assets/attachments.js`：上传页新增预置工具区（artifact id、运行、ready/leased/succeeded/failed 中文状态、Decimal 总额/地区表、summary.csv/summary.json 下载、失败诊断 error_code/message/fix/location）；全部 `textContent`，无内联用户内容、无 Number()/parseFloat、无凭据存储 |
| 测试 | `tests/support/t030.py` + `tests/f05/test_t030_*.py` **6 文件 55 用例**（工具 Python 子进程 36、迁移 5、成功流 4、拒绝 3、失败/重试 3、页面 4）；真实路径覆盖 CSV/XLSX 精确 Decimal、下载字节、日志无正文/secret、10 类拒绝 + 诊断、超时/IO/tombstone、幂等/重跑/ACL/API key scope；`internal/tools` 15 个 Go 单测、`handlers` 7 个、`config` 1 个、后台白名单更新 |
| 证据 | `reports/f05/T030-sales-tool.md`（AC 逐条、原始命令/结果、契约缺口、not_run、T031/T033 接口）；`docs/adr/0005-t030-tool-run-bridge.md` |

## F03 结果提议与统一幂等终态（2/N：T031）

T031（GitHub #32）已交付，状态 **`passed`**（本票范围 = **worker result 提议 + supervisor 身份化 exit
证据 + 统一幂等 finalizer + 运行页 finalizing/终态展示**）。真实 PostgreSQL 16.4 + 真实编译二进制 +
真实 mTLS 内部客户端（worker/supervisor/maintenance 三种证书）+ 真实 HTTP + 真实文件系统对象字节；
无 mock、无跳过。边界（如实）：T032 的事件/SSE、T033 的 run-bound artifact 注册、T035 的 waiting/
resume、T036 的 cancel/unknown guard、T038 的恢复证据均不实现；运行页为真实 HTML/JS + Node 执行
核心逻辑，无浏览器 DOM 驱动。见报告 §5/§7。

| 项 | 结果 |
|---|---|
| 迁移 | 无。契约基线 `run_attempts.reported_result/reported_at/checkpoint_id` 已够用：proposal 整体（含 `event_id`）存入 `reported_result`，不新增列/表；`contracts/**`、`docs/spec/**` 未改，`sync_spec --check` no drift |
| submitWorkerResult | `POST /internal/v1/runs/{run_id}/result`，worker 或 supervisor（maintenance 403）；body 按契约 `WorkerResult` 严格解码（additionalProperties:false），条件必填 `output_artifact_id`(succeeded)/`checkpoint_id`(waiting_*)/`error_code`(failed)；token 必须绑定该 run 且为该 run 最新 attempt（跨 run/被取代/已 requeue → 409 `stale_attempt`），run 必须 running 或 recovery_pending（终态 → 409 `run_terminal`，waiting/cancelling/needs_reconciliation → 409 `run_state_conflict`）；同内容重放 200、异内容 409 `event_conflict`；succeeded 的 output artifact 必须存在、owner=run owner、ready 且未删除、并在 `run_artifacts` 关联（422 `validation_failed` + `details.code`=artifact_not_found/not_owned/not_ready/not_linked）；事务只写 proposal/reported_at/checkpoint_id 与 `runs.phase='finalizing'`（state/revision 不变），200 返回契约 Run（proposal-only 时 running/finalizing）。T033 之前由测试/种子写 `run_artifacts` 链接 |
| confirmAttemptExit | 在 T029 上扩展：evidence 必须携带 `started`(bool)、`protocol_version`、`manifest_sha256`(64 hex)、`image_digest`（非契约 evidence 扩展）；协议/manifest/image 与 claim 时的 run manifest hash + snapshot worker_image_digest 不符 → 409 `execution_identity_mismatch` 且零写入；`started=false` 保持 T029 requeue；`started=true` 且无 result → run `recovery_pending`/phase idle/revision+1、job held、slot 已释放，绝不 requeue |
| 统一 finalizer | `finalizeRunTx` 由 result+exit 两个入口在各自事务内调用，要求两者齐全；`platform.runs(parent_run_id)` 有非终态子 run 时阻塞（job held、run 保持 running/finalizing，T035/T036 接续）；unknown-operation guard 已结构化（`hasUnknownOperationsTx` 目前恒 false，T036 替换该函数体）；succeeded/failed 同一事务映射 run state/revision+1/finished_at/output_artifact_id/error_code、phase idle、job completed、token revoked、allocation `released_at` 恰好一次（重复调用为 no-op）；needs_reconciliation 非终态提交；waiting_* 只存 proposal、保持 running/finalizing、job held（T035） |
| 幂等/顺序 | result-first：claim rev2 → proposal rev2/finalizing → exit rev3 终态；exit-first：claim rev2 → park rev3 recovery_pending → result rev4 终态；两种顺序都只有一次终态、一次 job 完成、一次配额释放、无重复行；重复 exit（同 evidence JSONB 相等）200 no-op，重复 result（proposal 已存且 run 非终态）200 no-op；终态后迟到 result 409 `run_terminal`，不同 exit evidence 409 `attempt_evidence_conflict` |
| 错误码 | 新增 `run_terminal`(409)、`event_conflict`(409)、`execution_identity_mismatch`(409)、`run_state_conflict`(409，T035/T036 之前的显式临时拒绝)、`run_not_found`(404)；复用 `stale_attempt`/`attempt_evidence_conflict`/`validation_failed`/`invalid_json`/`forbidden`；422 `details` 增加 `code` 以定位 artifact 原因 |
| Web | `runs.html`/`runs.js`：详情新增“执行阶段 executing → finalizing → 终态”行；finalizing 明确写“等待 supervisor 的退出证据……终态提交前这不是成功”；未终态时轮询 `GET /api/v1/runs/{id}`，终态即停，成功只在服务端提交终态后展示；`debug.html`/`debug.js` 同样解释 finalizing（`describeRunStage`）；全部 `textContent`、无凭据存储 |
| 测试 | `tests/support/t031.py` + `tests/f03/test_t031_{result,finalizer,identity,pages}.py` **4 文件 17 用例**（result 2、finalizer 10、identity 1、pages 4），T029 exit/invariants 用例按新必填证据收紧；`tests/f03` **68 passed**（51 + 17）；`internal/execution` Go 单测扩到 7 个（含 exit identity 必填/定位、WorkerResult 条件必填）；`go test ./...` 全绿、`go vet ./...`/`gofmt -l .` 无输出、`sync_spec --check` no drift（全部真实 PG/二进制/mTLS/HTTP/对象字节） |
| 证据 | `reports/f03/T031-result-and-finalizer.md`（AC 逐条、原始命令/结果、契约缺口、not_run、T032/T035/T036/T038 接口） |

## F03 持久事件与 SSE 回放（3/N：T032）

T032（GitHub #33）已交付，状态 **`passed`**（本票范围 = **持久 run_events + 同事务状态投影 +
worker node.status 批量事件 + owner-only SSE 回放/去重/保留窗口/心跳/终态关闭 + 页面时间线**）。
真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 mTLS 内部客户端（worker/supervisor/maintenance）+
真实 HTTP/SSE 连接（含中途断开与 Last-Event-ID 重连）+ Node 真实执行页面逻辑；无 mock、无跳过。
边界（如实）：模型 delta 临时预览属 F06（UI 明确 `data-preview="not_run"`，不伪造正文）；
run-bound artifact 注册属 T033；waiting/resume 属 T035；cancel/unknown guard 属 T036；恢复属 T038；
F07 负责保留窗口的实际清理；浏览器 DOM 驱动页面仍 `not_run`。见报告 §5/§7。

| 项 | 结果 |
|---|---|
| 迁移 | `db/migrations/0018_run_events.sql`：唯一新表 `platform.run_events`（run_id FK、seq>0、event_id uuid UNIQUE、type/revision/payload jsonb、created_at；PK `(run_id,seq)`）；命名 replay 索引 `(run_id,seq)` 与 retention 索引 `(created_at)`；事件类型限定契约五值、payload 必须为 JSON 对象、revision>0；`orchara_worker`/PUBLIC 仍零权限。`contracts/**`/`docs/spec/**` 未改，`sync_spec --check` no drift |
| 同事务事件 | `execution.AppendRunStatusTx(tx)` 是唯一控制器写入器：锁 Run→取 `next_event_seq`→以新 uuid 与**转换后** revision 插入→计数+1；debug 创建（queued rev1）、claim（running rev2）、result 提议（running，phase=finalizing 但事件无 phase 字段）、finalizer（succeeded/failed + error_code、needs_reconciliation）、exit 无结果（recovery_pending）、started=false requeue（queued）全部同事务；事件插入失败则业务转换一起回滚（测试用真实 PG trigger 证明） |
| submitWorkerEvents | `POST /internal/v1/runs/{run_id}/events`，worker|supervisor（maintenance 403）；batch 1..100；body 严格解码 `{attempt_token,events[{event_id,local_sequence,type,payload}]}`；token 必须是该 run 的**当前 live attempt**（job 仍 leased、未 exit/revoke、租约未过期；跨 run/随机 token → 409 `stale_attempt`）；只接受 `node.status`，payload 精确 `{node_id,state,attempt_no}`，content/body/delta 等键 422 定位拒绝；event_id 幂等：同 run+同 type+同 JSONB payload → 200 no-op 不占 seq，异内容/跨 run → 409 `event_conflict`；新事件按请求数组顺序各占一个 seq；终态 run → 409 `run_terminal`；非执行态 → 409 `run_state_conflict`；`local_sequence` 仅诊断（缺口/重复只写日志，绝不当展示顺序，也不入库） |
| runEvents (SSE) | `GET /api/v1/runs/{run_id}/events`，owner-only（管理员/他人/未知/已删除一律 404 `resource_not_found`）；`Last-Event-ID` 为同 run 本地 seq（非法 → 400 `validation_failed` + `details.field=Last-Event-ID`）；帧 `id: <seq>`、`event: <type>`、`data: <event.schema 封套>`；无事件每 15s 发 `: heartbeat` 注释（不占 seq、无 id/event/data）；空闲时轮询 PostgreSQL（Redis 仅唤醒且本批不存在）；回放期间重复 seq 由客户端按 seq 去重；回放到终态并追平后关闭；回放边界事件早于保留窗口 → 410 `event_cursor_expired` + `details.snapshot_url=/api/v1/runs/{id}`（契约 Error 无 snapshot_url 字段，记录为契约缺口），不静默跳过 |
| 配置 | `ORCHARA_RUN_EVENT_RETENTION`（默认 24h）、`ORCHARA_RUN_EVENT_POLL_INTERVAL`（默认 500ms）、`ORCHARA_RUN_EVENT_HEARTBEAT_INTERVAL`（默认 15s），均由 `config.Load` 校验（正数 + 上限），api.Deps 传入；无请求级覆盖 |
| Web | `runs.html`/`runs.js`：详情新增持久时间线（fetch+ReadableStream 读 SSE，不用 EventSource，以便观察 410 并显式发送 Last-Event-ID）；按 seq 去重排序；断线显示“按 Last-Event-ID 重连”，410 显示 `details.snapshot_url` 并停止；终态显示“已回放到已提交终态 … 事件流关闭”；`describeRunEvent` 只渲染 run.status state/error_code 与 node.status 元数据，绝不渲染 payload 正文；running/finalizing 明确“尚未成功”；`debug.html`/`debug.js` 复用同一 `beginRunTimeline`；两页都有 `data-preview="not_run"` 的 F06 说明 |
| 测试 | `tests/support/t032.py` + `tests/f03/test_t032_{migration,events,sse,pages}.py` **4 文件 31 用例**（migration 6、events 13、sse 7、pages 5）；`tests/f03` **99 passed in 420.83s**（基线 68 + 31）；`go test ./...` 19 包 ok、8 包 no test files、0 FAIL；`go vet ./...` exit 0、`gofmt -l .` 无输出、`sync_spec --check` no drift；均为真实 PG/二进制/mTLS/HTTP/SSE/Node |
| 证据 | `reports/f03/T032-run-events.md`（AC 逐条、原始命令/结果、契约缺口、not_run、T033/T035/T036/T038 接口） |

## F03 跨尝试累计活跃时间与截止（4/N：T034）

T034（GitHub #35）已交付，状态 **`passed`**（本票范围 = **跨 attempt 累计活跃耗时 + 幂等结算 +
等待/finalizing 不计时 + 冻结活跃时限 + claim/heartbeat/result 截止检查 + 退出后由唯一 finalizer
提交 `failed(deadline_exceeded)` + 运行页累计时钟/超时原因**）。真实 PostgreSQL 16.4 + 真实编译
二进制 + 真实 mTLS 内部客户端 + 真实 HTTP + 真实对象字节 + Node 执行页面逻辑；无 mock、无跳过。
边界（如实）：waiting/resume 属 T035，取消端点/优先级属 T036，恢复扫描属 T038；到期后的能力/
注册门禁按设计留给后续切片；浏览器 DOM 驱动页面仍 `not_run`。见报告 §2/§5/§6。

| 项 | 结果 |
|---|---|
| 迁移 | `db/migrations/0020_active_time_and_deadline.sql`：`runs.max_active_ms bigint`（NULL=unknown/no deadline，非空时 CHECK>0）、`run_attempts.active_ms bigint NOT NULL DEFAULT 0` + `active_settled_at timestamptz`（CHECK active_ms>=0）；按 snapshot `definition.policy.max_active_seconds`（1..3600 整数）回填 ×1000，其它保持 NULL；无新表、无新权限、无 trigger；`contracts/**`/`docs/spec/**` 未改，`sync_spec --check` no drift |
| 结算 | `settleAttemptActiveTx` 是唯一结算器，在离开 executing 的同一事务调用（result 提议/exit/finalizer；T035/T036 后续转换复用）：锁内 `active_settled_at IS NULL` 的 attempt 写 `active_ms=GREATEST(0, now-started_at)` + `active_settled_at=now()`，同一 delta 加到 `runs.active_elapsed_ms`；重复调用 0；新 attempt 从累计值继续、不重置也不重新获得完整时限；`revision` 不因结算 bump |
| 投影/截止 | 投影 = `active_elapsed_ms + (phase=executing && 未结算 live attempt ? now-started_at : 0)`（数据库时钟）。at/over 时记录 intent 一次（`cancel_requested_at=COALESCE(now)`、`error_code` 仅 NULL 置 `deadline_exceeded`）；heartbeat 续租并回答 `cancel_requested=true`；result 提交先结算，at/over 返回 409 `deadline_exceeded` 且不落库；exit 后复用 `finalizeRunTx` 提交 failed、无 output、slot 恰好释放一次、同事务 `run.status`；到期仍走 children/unknown guard，阻塞时 park `recovery_pending`；claim 拒绝已超时 queued Run（failed + job void）。NULL max 无截止 |
| 读接口/UI | `GET /runs/{id}` 非契约头 `X-Run-Active-Elapsed-Ms` / `X-Run-Max-Active-Ms`（NULL 省略）/ `X-Run-Deadline-Exceeded`；`/runs` 详情新增累计活跃/上限与超时原因，两次 attempt 显示为一个累计时钟，NULL 上限写“未知/无截止 ≠ 0”，不渲染百分比/预计完成；列表行不伪造（契约 Run 无该字段） |
| 错误码/语义 | 新增 409 `deadline_exceeded`（result 提交）；`ErrDeadlineExceeded`。claim 的过期拒绝仍 204（本票唯一会改变状态的 204：Run failed、job void）。T034 是 pre-cancel：只记录 intent/拒绝新结果，不提交 cancelled；T036 细化取消优先级 |
| 测试 | `tests/support/t034.py` + `tests/f03/test_t034_{migration,accounting,deadline,pages}.py` **4 文件 18 用例**（迁移 5、累计/幂等/等待/finalizing 4、截止 5、页面 4）；`tests/f03` **117 passed**；额外 `tests/f05` **106 passed** 回归；迁移链 0019→0020 适配 T023/T025/T026/T027/T028/T029/T030/T032/T033，另修两处**先前票遗留陈旧断言**（T027 的 `next_event_seq` 与 attempt FK 延期断言）；`go test ./...` 全绿、`go vet`/`gofmt -l` 干净、`sync_spec --check` no drift |
| 证据 | `reports/f03/T034-active-time.md`（AC 逐条、原始命令/结果、契约缺口、not_run、T035/T036/T038 接口） |

## F03 等待条件与唯一恢复（5/N：T035）

T035（GitHub #36）已交付，状态 **`passed`**（本票范围 = **waiting_approval/waiting_children 转换 +
唯一 `run_waitings` 事实 + 合成审批/子结果 bridge + 单一幂等 `maybe_enqueue_resume` + resume job 的
canonical payload + 权限/预算/截止/取消重查 + 运行页等待/恢复解释**）。真实 PostgreSQL 16.4 +
真实编译二进制 + 真实 mTLS（supervisor/worker 证书）+ 真实 HTTP + 真实文件系统对象字节 +
真实 signed sealed 链接 + Node 执行页面逻辑；无 mock、无跳过。边界（如实）：真实审批属 **F06**、
真实子应用/子结果属 **F07**，本票用记录在案的非契约 mTLS bridge 提供合成决定/子结果；取消终态属
**T036**（只记录 intent，不伪造 cancelled）；恢复证据/失联扫描属 **T038**；浏览器 DOM 驱动仍
`not_run`。见报告 §2/§5/§6。

| 项 | 结果 |
|---|---|
| 迁移 | `db/migrations/0021_run_waitings.sql`：唯一新表 `platform.run_waitings`（`run_id` PK→runs、`kind`∈{approval,children}、`checkpoint_id` 非空、安全 `decision` jsonb + `decision_at`、`exit_confirmed_at`、`resume_job_id` UNIQUE→jobs、`resume_artifact_id`→artifacts、created/updated）。CHECK 闭合 kind/outcome 联合体与 decision/decision_at 成对，且 resume job/artifact 只能在 `exit_confirmed_at` 之后出现；`orchara_worker`/PUBLIC 仍零 `platform` 权限；无 approvals/operations/children/requests 表（F06/F07 归属），`contracts/**`/`docs/spec/**` 未改，`sync_spec --check` no drift |
| waiting 转换 | 接受的 `waiting_approval`/`waiting_children` result proposal：存 proposal/checkpoint → T034 `settleAttemptActiveTx`（等待不计活跃时间）→ run state `waiting_*`、phase `waiting`、revision+1 → job `held`（attempt 仍 live、保留 token）→ 插入 `run_waitings` → 同事务 `run.status` 事件。attempt 在 supervisor 确认退出前仍 live；退出只写 `exit_confirmed_at` 并清掉 held job 的 token，绝不因此创建 resume。exit-first（先 exit 后 waiting proposal）同样进入 waiting 并在同一调用补齐退出事实 |
| 合成 resolution | `POST /internal/v1/runs/{run_id}/waiting-resolution`（仅 supervisor mTLS，maintenance/worker 403）。body 严格闭合：approval `{kind,outcome∈approved/rejected/expired,reference}`，children `{kind,outcome∈succeeded/failed,child_run_id}`；未知字段 400 `invalid_json`、非法枚举 422 `validation_failed`（定位 field）、run/kind 不匹配或非 waiting 409 `run_state_conflict`、不存在 404 `run_not_found`；第一次决定权威（同内容 replay 200 no-op，不同内容 409 `decision_conflict`）；children 的 child 必须是本 Run 的 child 且终态（否则 409 `run_state_conflict` + `details.reason`），rollback 不落决定；不接受 Command/thread_id/user_id |
| maybe_enqueue_resume | 单一事务：锁 Run→run_waitings→旧 job→attempt；要求 decision+decision_at+exit_confirmed_at+checkpoint 齐全且未取消/未终态；children 重查 parent/terminal；重新鉴权（owner `disabled_at`）、预算（`budget_limit > 0`）、T034 deadline（等待不累计，故用已结算 total）。通过后：对象存储写 JCS canonical resume payload（run_id/kind/checkpoint_id/decision）→ 真实 sha256/size 的 ready artifact（purpose `resume_input`、owner=run owner）→ 旧 job completed、旧 token revoked、allocation 恰好释放一次（T031 exit 已释放时为 no-op）→ 插入唯一 `jobs(kind='resume',state='ready',resume_artifact_id)` → `run_waitings.resume_job_id` → run `queued`/phase idle/revision+1 → 同事务 `run.status`。重复调用返回既有 resume；`UNIQUE(run_waitings.resume_job_id)` + `one_live_job_per_run` 让第二次 resume 在 DB 层不可能 |
| 拒绝语义 | 决定与退出齐备后权限/预算/截止失败 → 不恢复，提交 `failed(permission_revoked)` / `failed(budget_exhausted)` / `failed(deadline_exceeded)`（无 output、job completed、token revoked、slot 释放一次、同事务事件带 error_code）；记录取消意图（`cancel_requested_at`，T036 端点未实现）→ 不恢复、保持 waiting 非终态，不伪造 cancelled；对象存储不可用 → 503 且整笔回滚（无决定/无 resume） |
| Resume claim | T029 的 claim 已能签发 `job_kind='resume'` lease：真实 signed `resume_url` 的字节等于 canonical payload，新的 attempt token 与等待 attempt 不同；`run_attempts` 仍只有一个 live attempt，旧 attempt exited/revoked；新 job 取得自己的 holder allocation（旧 job 的 allocation 保持 released 历史） |
| Web | `runs.html`/`runs.js`：详情新增“等待条件”和“恢复任务”两行；显示等待 kind、已保存 outcome/decision_at、退出确认、唯一 resume job id/state；明确“等待释放 worker 执行槽，但逻辑会话槽与 Run 占用到终态”，未齐备时写“尚未创建恢复任务”，绝不显示恢复输入/token/URL；`runLifecycle` 把 waiting 作为真实阶段 |
| 测试 | `tests/support/t035.py` + `tests/f03/test_t035_{waiting,resume,resume_refusals,migration,pages}.py` **5 文件 24 用例**（waiting 4、resume 5、refusal 4、migration 7、pages 4）：decision-first/exit-first 各恰好一个 resume、重复/冲突幂等、canonical payload 字节、fresh attempt、children parent/terminal、disabled owner/0 budget/deadline/cancel intent 拒绝、迁移 0001→0021/replay no-op/worker 拒绝/top-level session slot 仍占用；`tests/f03` **141 passed**；`tests/f03` 的既有 10 个迁移测试链尾断言 0020→0021 适配（T031 的 waiting interim 断言按本票转换更新）；`go test ./...` 全绿、`go vet ./...`/`gofmt -l .` 无输出、`sync_spec --check` no drift |
| 证据 | `reports/f03/T035-waiting-resume.md`（AC 逐条、原始命令/结果、契约缺口、not_run、T036/T038 接口） |

## F03 取消与未知操作守卫（6/N：T036）

T036（GitHub #37）已交付，状态 **`passed`**（本票范围 = **owner-only 幂等取消端点 + cancelling 状态 +
cancel/unknown 优先级 finalizer + 未解决 unknown-operation ledger/bridge + waiting 取消不恢复 +
运行页取消/停止反馈**）。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 mTLS（supervisor/worker
证书）+ 真实 HTTP + 真实文件系统对象字节 + Node 执行页面逻辑；无 mock、无跳过。边界（如实）：
真实外部操作/回执属 **F06**，本票的 unknown 端点只是非契约 bridge；安全恢复/核对后的重驱属 **T038**；
浏览器 DOM 驱动仍 `not_run`。见报告 §2/§5/§6。

| 项 | 结果 |
|---|---|
| 迁移 | `db/migrations/0022_run_unknown_operations.sql`：唯一新表 `platform.run_unknown_operations`（`id` PK、`run_id`→runs、`operation_id`、安全 `reason`、`created_at`、`resolved_at`，`UNIQUE(run_id,operation_id)`，未解决 partial index `(run_id) WHERE resolved_at IS NULL`，reason 非空/≤128 CHECK）；`platform.run_waitings` 增加 `cancel_requested_at`（证据保留、仅让 resume 变为 inert）；**不**新增 `runs.cancel_reason`/`cancelled_by`；无真实 operations/approvals 表（F06 归属）；worker/PUBLIC 仍零 `platform` 权限；`contracts/**`/`docs/spec/**` 未改，`sync_spec --check` no drift |
| cancelRun | `POST /api/v1/runs/{run_id}/cancel` → 202 契约 Run。owner-only（管理员/他人/未知/畸形一律 404 `resource_not_found`，同 getRun 不可见规则）；浏览器会话需 CSRF、API key 需 `application:write`；幂等。终态 → 202 当前 Run 不变；已有 `cancel_requested_at` → 202 不变且不重复事件（deadline intent 也走此规则，first write wins）；queued/waiting/恢复类且**无 live attempt**（job ready/held）→ 立即 `cancelled`、phase idle、finished_at、revision+1、job `void`、waiting 行标记不可恢复、同事务一条 `run.status`；存在未解决 unknown → 同路径提交 `needs_reconciliation(error_code=unknown_operations)` 而非 cancelled；有 live attempt（running/finalizing/waiting）→ 记录 intent、state `cancelling`（phase 保持原值，枚举无 cancelling）、revision+1、同事务事件 |
| finalizer 优先级 | 扩展 T031 `finalizeRunTx`（不 fork）：live child run 阻塞一切终态（cancel 保持 cancelling、job held；deadline 仍按 T034 park recovery_pending）；未解决 unknown 优先于 deadline/cancel/proposal → `needs_reconciliation(unknown_operations)`；已记录/已达 deadline → `failed(deadline_exceeded)`；`cancel_requested_at` → cancelled 胜未提交的 success 结果（含 waiting proposal）；started=false 的取消 exit 将 job `void` 而非 completed。cancelled 设置 phase idle/finished_at/revision+1/output NULL/error_code NULL、job completed/void、token revoked、allocation 恰好释放一次、同事务 `run.status`；已终态重复 finalize 为 no-op |
| unknown bridge | `POST /internal/v1/runs/{run_id}/unknown-operations`（worker\|supervisor，**live** attempt token，run 非终态）记录安全 reason 一次（same id+reason 200 replay，different reason 409 `event_conflict`，bad reason 422 `validation_failed` 定位 field，terminal 409 `run_terminal`）；`POST /internal/v1/runs/{run_id}/unknown-operations/{operation_id}/resolve`（worker 需 live token；supervisor 可在 exit 后解析）；`hasUnknownOperationsTx` 现在查询未解决行。解析最后一个 unknown 且 run 已 cancelling/needs_reconciliation、最新 attempt 已 exit 时重入同一 finalizer → cancelled 恰好一次；terminal 后 record/resolve 409 且绝不复活 |
| waiting 集成 | 取消时同事务标记 `run_waitings.cancel_requested_at`（不删除决定/checkpoint/exit 证据）；`maybeEnqueueResumeTx` 对该标记 inert（deadline intent 仍按 T035 先判 deadline，避免抢占 deadline 失败路径）；cancelling 时 waiting-resolution 被 409 拒绝；cancel 前已创建的 resume job 在立即取消时置 `void`，不会被 claim；取消后的 Run 不再产生 resume |
| 拒绝/允许矩阵 | cancelling：heartbeat 继续（`cancel_requested=true`）、exit 接受；result/registration/create-upload/waiting-resolution 409 `run_state_conflict`；已 in-flight 的 upload complete、artifact status/download 仍可用（receipt/query）；终态后 result 409 `run_terminal`，同证据 exit 200 no-op、异证据 409 `attempt_evidence_conflict` |
| Web | `/runs` 详情新增取消行/按钮/状态：queued 说明立即生效；cancelling 显示“正在停止”；cancelled 显示“已停止”且明确不表示撤销外部写入；failed/succeeded 显示终态原因（error_code）；`runLifecycle` 增加 cancelling 阶段；`mergeRunSnapshot` 保证已提交终态不会被迟到/陈旧快照翻回；按钮 owner-only（页面只列自己的 Run）、幂等重试；沙箱面板停止/已停止措辞同步且不再写“取消未实现” |
| 测试 | `tests/support/t036.py` + `tests/f03/test_t036_{cancel,unknown,migration,pages}.py` **6 文件 29 用例**（cancel 14、unknown 6、migration 5、pages 4）：queued 立即 cancelled（job void/单事件）、running→cancelling（heartbeat true、result/registration/upload 拒绝、complete/status 可用、exit 后单次 cancelled/单 revision/单释放）、waiting 有 live attempt→exit 后 cancelled 且无 resume、decision 前后取消、resume 后取消 void resume job、terminal 不变、result+exit 先提交后 succeeded 保留、cancel 先提交则 cancelled、deadline 优先级、并发双 cancel 单转换、started=false→void、unknown→needs_reconciliation→resolve last→cancelled、unknown 后 terminal 不复活、live child 阻塞、owner/admin/API-key 权限、迁移 0001→0022/replay no-op/worker 拒绝；`tests/f03` **170 passed**；迁移链 0021→0022 适配 11 个测试，T033 的取消 interim 断言按 T036 终态更新；`go test ./...` 全绿、`go vet ./...`/`gofmt -l .` 无输出、`sync_spec --check` no drift |
| 证据 | `reports/f03/T036-cancel.md`（AC 逐条、原始命令/结果、契约缺口、not_run、T038/F06 接口） |

## F05 工具提交、隔离构建与审核（3/N：T037）

T037（GitHub #38）已交付，状态 **`passed`**（本票范围 = **submitTool/listTools/getTool/reviewTool
+ 非契约 revoke/build-facts + `tool_build` 隔离构建 + 完成守卫 + 工具页/审核队列**）。真实
PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP/mTLS + 真实对象字节 + **真实 WSL2/runsc(gVisor)
构建** + Node 执行页面逻辑；无 mock、无跳过。边界（如实）：F06 拥有 connectors（非空
`connector_version_ids` 被 422 拒绝而非静默接受）；F15A 构建 OCI 镜像，本票的 `image_digest` 是
**内容包摘要**不是镜像；最小 rootfs 没有 pip/wheelhouse/setuptools，lock 需要包时安全失败为
`dependencies_unavailable`（不伪成功）；F04A 拆真实 worker/supervisor；浏览器 DOM 驱动仍
`not_run`；全量 `pytest tests` 门禁未跑（批次规则，由 orchestrator 跑）。见报告 §2/§5/§6。

| 项 | 结果 |
|---|---|
| 迁移 | `db/migrations/0023_tool_builds_and_review.sql`：`tool_versions` 增加 revision、source_sha256、dependency_lock_sha256、build_log_artifact_id、sbom_artifact_id、build_bundle_digest、build_started_at、build_finished_at、build_error_code、reviewed_by、reviewed_at、review_decision、review_reason、revoked_at、revoked_by、revoke_reason + guarded CHECK/索引/COMMENT；契约列、状态枚举与 `state<>'ready' OR image_digest IS NOT NULL` 保留；worker/PUBLIC 零权限；重复 migrate no-op |
| submitTool | `POST /api/v1/tools`（会话 creator 或 API key `application:write`）：源 artifact 必须 caller 自有/ready/未删/`tool_source`/`application/zip`；schemas/lock 必须是 JSON 对象；risk 枚举；connectors 非空 422；JCS `manifest_sha256` 覆盖 source hash+schemas+lock+risk+空 connectors；单事务插 tool（submitted, revision=1）+ 恰好一个 `tool_build` job；同 owner 同内容幂等 202（不建第二个 job），另一 owner 同内容 409 `tool_conflict`（manifest 全局唯一）；202 Accepted |
| zip 准入 | T028 允许清单按 purpose 扩展：`application/zip` 只对 `tool_source`，真实字节检查 entry≤512、解压≤64 MiB、压缩比≤100、拒绝绝对/`..`/反斜杠路径、symlink、encrypted、未知压缩方法；既有 text/csv/json/xlsx 规则与测试不变；其他 purpose 的 zip 仍在声明阶段 422 |
| 构建 | `tool_build` handler 从 job-scoped binding 取 source sha/canonical lock，经 signed link 下载源；`deploy/sandbox/build_tool.py` 在 T033 同一 runsc bundle 内安全解包、语法编译、运行不可信 `setup.py build`（stripped env）；固定 argv、无 shell、无网络、uid 65534、只读 rootfs/`/tool`/`/inputs`、可写 `/workspace`、CPU/mem/pid/timeout；上传 capped `build_log` + `sbom`（job-scoped 端点）；sandbox 错误/崩溃/超时/依赖不可用均映射安全码，日志不含宿主/凭据/对象键 |
| 完成守卫 | claim 把 submitted→building；只有 `guardToolBuildCompletion` 写 ready：锁 tool 行，要求 state=building、revision/expected source hash/source artifact 未变、源 artifact ready、SBOM 是本次 completion 结果且 ready+job-linked、最新 `build_log` ready+job-linked；重读 sealed source/SBOM+canonical lock 计算 bundle digest；`image_digest=build_bundle_digest=sha256:<hex>`（sorted source+lock+SBOM 拼接的 SHA-256，**非 OCI 镜像**，F15A 才构建镜像）；CAS 失败/源变更→`build_source_changed` 且绝不 ready；失败仅 transient 三种重试，其余业务失败并记录安全 `build_error_code` |
| review/revoke | `POST /tools/{id}/review`（admin 会话；API key 403）：body 精确 `{decision,manifest_sha256,reason}`，require Idempotency-Key；只接受 state=ready；hash 不匹配 409 `review_conflict` 且零写入；approve 写 approved_by/at+reviewed_*+audit；reject 记录 reason/decision 不改 build state、不置 approved；同 decision+reason 幂等（不重复审计），相反决定 409。非契约 `POST /tools/{id}/revoke`（admin）：state=revoked+revoked_at/by/reason+audit，历史 GET 仍可读；T025 依赖校验返回 `dependency_revoked`；未批准 ready 工具仅作者私有 debug 可用，共享 validate 仍 `dependency_not_ready` |
| 读/UI | `GET /tools`（cursor，owner-or-admin-or-approved，approved=approved_at 非空；`application_id` 参数接受但过滤延后 F02B，报告记录）+ `GET /tools/{id}` 七字段契约投影；非契约 `GET /tools/{id}/build` 提供 build log/SBOM artifact id、digest、build error、review/revoke facts（不含对象键/正文）；`/tools` 页提交、状态、日志/SBOM 下载、管理员审核队列；全部 `textContent`、无 localStorage |
| 错误码 | 新增 `tool_conflict`(409)、`review_conflict`(409)、`tool_state_conflict`(409)、`resource_not_found`(404，复用)、`artifact_not_ready`(409，复用)；构建安全码 `dependencies_unavailable`/`build_source_invalid`/`build_source_changed`/`build_sandbox_unavailable`/`build_timeout`/`build_crashed`/`build_output_invalid`/`build_artifacts_invalid`/`build_artifact_invalid`/`build_failed`；internal binding 复用 `stale_attempt`/`forbidden` |
| 测试 | `tests/support/t037.py` + `tests/f05/test_t037_*.py` **8 文件 39 用例**（migration 6、submit 11、admission 4、真实 runsc build 2、恶意钩子/崩溃/依赖/源变更 4、review/revoke 8、internal 1、pages 3）：真实构建 ready+独立 bundle digest、SBOM/log 字节下载；恶意 setup.py 读宿主/foreign/secret、连网、写只读输入全部失败且日志无 secret；dependencies_unavailable；构建中源变更不 ready；审核 hash 不匹配/CAS；未批/批准/撤销的 T025 共享 vs 私有规则；迁移链 0022→0023（T023/T025/T026/T027/T028/T029/T030/T032/T033/T034/T035/T036 断言更新；T025 基线列集按文档扩展）；`tests/f05` **145 passed in 611.79s**；`go test ./...` 全绿（新增 background/toolversions/artifacts Go 单测）、`go vet`/`gofmt -l` 干净、`sync_spec --check` no drift |
| 证据 | `reports/f05/T037-tool-builds.md`（AC 逐条、原始命令/结果、契约缺口、not_run、T038/F04A/F15A 接口） |

## F03 保守恢复与受审计重驱（7/N：T038）

T038（GitHub #39）已交付，状态 **`passed`**（本票范围 = **租约超期扫描只挂起 + 受审计恢复证据命令 +
一次事务内的单次重排 + owner-facing recovery 事实/UI**）。真实 PostgreSQL 16.4 + 真实编译
`cmd/admin`/`cmd/api` + 真实 mTLS（supervisor/worker 证书）+ 真实 HTTP + 真实文件系统对象字节 +
**真实打开中的 psql 事务/pg_stat_activity** + Node 执行页面逻辑；无 mock、无跳过。边界（如实）：
**无自动接管**——sweep 只 park（job held、allocation 占用、token 不变），claim 只选 queued；
recover 是唯一重排路径且要求运维证据；`cancelling`/`waiting_*` 不被 sweep（STATE-MACHINES §2 无
`waiting_* → recovery_pending` 边，重排 cancel intent 会复活取消）；常驻巡检、F04A 真实
supervisor 证据、DOM 驱动仍 `not_run`。见报告 §2/§5/§6。

| 项 | 结果 |
|---|---|
| 迁移 | `db/migrations/0024_run_recovery_evidence.sql`：唯一新表 `platform.run_recovery_evidence`（run/attempt → runs/run_attempts、闭合证据 jsonb + object CHECK、JCS `evidence_sha256` + 64-hex CHECK、真实 principal `recorded_by`、数据库时钟 `recorded_at`、`resumed_job_id`→jobs（非 UNIQUE，每周期记录 requeued job）、`UNIQUE(run_id,attempt_token)`、run 索引）；`runs.recovery_reason`/`recovery_since` + `runs_recovery_pair_check` + 闭合 `runs_recovery_reason_check`；worker/PUBLIC 零 `platform` 权限；重复 migrate no-op；`contracts/**`/`docs/spec/**` 未改，`sync_spec --check` no drift |
| sweep-leases | `cmd/admin runs sweep-leases [--grace 60s] [--dry-run]`：扫描 `exited_at/revoked_at IS NULL` 且 `lease_deadline < now()-grace` 的 running Run（未删、无 cancel intent、≤100/次），逐条独立事务 `FOR UPDATE OF a,j,r` 复检 → `recovery_pending`/phase idle/revision+1/`recovery_reason=lease_expired`/`recovery_since=now()`、job `held`（清 token）、**不释放 allocation/不新增 attempt/不换 token**、同事务 `run.status` 事件 + `run.recovery_parked` 审计；`--dry-run` 只报 `would_park` 零写入；stdout 单 JSON，exit 0/2/1 |
| recover | `cmd/admin runs recover --run UUID --evidence FILE [--check-only] [--actor-login LOGIN] [--grace 60s]`：检查 run=recovery_pending、cancel intent、恰好一个可恢复 attempt、证据 `execution_unit_id` 与 attempt 一致、旧执行非存活（已 exited 或 heartbeat 早于 grace）、一个 held/leased job、冻结 manifest（存在/ready/未删/快照 hash/size、`compiler.VerifyManifestBytes`、manifest worker image digest 与快照一致）、input（存在/ready/未删/真实 sealed bytes sha+size）、`pg_stat_activity` 中所有记录的 checkpoint pid+backend_start 均不存在、真实管理员；证据闭合形状（process/sandbox 销毁结果 + observed_at + notes；自由文本拒绝）并记录 JCS SHA-256；`--check-only` 报告 `checks[]` 零写入 |
| 单次重排 | 一个事务：evidence 行（`recorded_by`=管理员、`resumed_job_id`=job，后续周期同样记录）→ 已结算则 no-op，否则 `settleAttemptActiveTx`（T034 唯一结算；sweep park 已按 `lease_deadline` 结算）→ 旧 attempt `exited_at`/`exit_evidence`/`revoked_at`（COALESCE 保留 supervisor 证据）→ job `ready`（同 kind、`resume_artifact_id` 保留）→ run `queued`/phase idle/revision+1/清 recovery 列（CAS state+revision）→ allocation 恰好释放一次 → 同事务 `run.status` → `run.recovery_recovered` 审计；`UNIQUE(run_id,attempt_token)` + state/revision CAS + `one_live_job_per_run` 让第二次重排不可能；重复命令 `already_recovered`，旧 token 迟到 result 409 `stale_attempt`、exit 409 `attempt_evidence_conflict` |
| 拒绝码 | `run_not_found`、`not_recovery_pending`、`already_recovered`、`cancel_requested`、`old_execution_still_live`、`database_writes_in_flight`、`artifact_unavailable`、`input_deleted`、`manifest_hash_mismatch`、`manifest_invalid`、`input_hash_mismatch`、`execution_identity_mismatch`、`evidence_required`、`evidence_invalid`、`evidence_unit_mismatch`、`live_attempt_conflict`、`job_unavailable`、`needs_reconciliation`、`actor_not_found`；用法错误 exit 2；均为 stdout JSON `status=refused` 且零写入 |
| API/UI | `GET /api/v1/runs/{id}` 非契约头 `X-Run-Recovery-Reason`/`-Since`/`-Evidence-At`/`-Evidence-SHA256`（token/证据正文/对象键不出现）；`/runs` 详情显示 recovery_pending、原因、挂起时间、是否有受审计证据（含 sha256）与运维命令，**无伪造恢复按钮**；`mergeRunSnapshot` 终态不回翻；`runLifecycle` 增加 recovery_pending 阶段 |
| 测试 | `tests/support/t038.py` + `tests/f03/test_t038_*.py` **5 文件 27 用例**（migration 5、sweep 5、recover 7、refusals 6、pages 4）：活旧 worker 不替换（sweep park、claim 204、recover 拒绝、无第二 attempt/token）、真实打开事务阻塞 `database_writes_in_flight` 且提交后成功、缺 manifest/已删 input/篡改 manifest、完整证据→恰一次重排/新 attempt/旧 token 失效/重复命令幂等/审计行/queued 事件、`--check-only` 与 `--dry-run` 零写入、exit-without-result park 可恢复、resume job 保留 kind+resume_artifact 重排、actor-login 记录真实管理员、无 attempt 的 `needs_reconciliation` 文档化拒绝、页面 Node 行为；`tests/f03` **197 passed in 980.65s**；定向回归 149 passed（T029/T031/T032/T034/T035/T036 + 12 个迁移链文件；T027 基线列集按 0024 扩展），`tests/f05/test_t033_pages.py` 3 passed；迁移链 0023→0024 适配 12 个测试；`go test ./...` 22 ok/9 no-test/0 FAIL、`go vet`/`gofmt -l` 干净、`sync_spec --check` no drift |
| 证据 | `reports/f03/T038-recovery.md`（AC 逐条、原始命令/结果、契约缺口、not_run、F04A/F09 接口） |

## F05/F03 批次复核修复（2026-11-03）

对 `feat/f05-f03-run-and-tools` 的 F05/F03 批次做复核，修复 6 个行为缺陷 + 3 项卫生/审计问题；
未推送、未关闭 issue、未修改 `contracts/**`/`docs/spec/**`；只就地修正新迁移 0024 与 0015 的注释，
未改 0001–0023 语义，未开始 F04A/F06/F07。修复提交：`fix(f05,f03): address the batch review findings`
（test 同提交）+ 本 docs 提交（见 `git log`）。

| # | 复核项 | 处置 | 证明 |
|---|---|---|---|
| 1 | T038 `sweep-leases` park 未结算，`recover` 把整个停机窗口计入 T034 `active_elapsed_ms`，可致恢复后立即 `deadline_exceeded` 且 UI 时钟跳变 | park 同事务按 `lease_deadline`（最后可证明活动终点）结算；`recover` 对已结算 attempt no-op | `tests/f03/test_t038_recover.py::test_park_settles_active_time_at_the_lease_deadline`（park 后 `active_ms`∈[4000,7000)，recover 后不变，下一次 claim 成功） |
| 2 | T036 立即取消绕过 live-child guard：有 live 子 Run 时仍提交 `cancelled` | 立即取消分支复用 `hasLiveChildrenTx`：有 live child 时保持 `cancelling`、记录 intent、job `held`、同事务事件 | `tests/f03/test_t036_cancel.py::test_cancel_with_a_live_child_run_stays_cancelling` |
| 3 | T033 `completeWorkerUpload` 在 scanning/ready 同 hash 重放时仍尝试入队（注定 dedup 失败）；quarantined/deleted 规则不清 | scanning/ready 重放不再调用 EnqueueTx，返回当前 artifact；quarantined→409 `artifact_conflict`、deleted→404 `artifact_not_found`、异 hash→409；job 数不变 | `tests/f05/test_t033_endpoints.py::test_complete_replay_on_scanning_and_ready_never_enqueues_a_job`、`test_complete_on_quarantined_or_deleted_artifact_is_refused` |
| 4 | T033 沙箱执行器：`SandboxStarted` 在报告前置 true；pre-start `{"ok":false,"error":...}` 被误分类；损坏 base64 静默 nil | 启动标志由解析出的 run 报告证明；新增 `ErrSandboxRefused`（无 cleanup 断言）；kill 场景用 cleanup `pid` 证明启动；base64 错误→`ErrOutputInvalid`；presetexec 映射 `sandbox_unavailable`，evidence 用 proven flag | Go：`internal/sandbox` 3 个新/强化用例、`internal/presetexec::TestPreStartSandboxRefusalKeepsTheProvenFalseFlag`；真实 `tests/f05/test_t033_sandbox_run.py` |
| 5 | T037 bundle digest 对无标签 payload 排序拼接，角色互换可能同 digest | 改为 `{role,sha256}`（source/lock/sbom）的 JCS 数组 SHA-256；Go 与 Python oracle 同步 | `internal/background::TestContentBundleDigestIsInjectiveAcrossRoles`；真实 `tests/f05/test_t037_build.py`（独立计算比对） |
| 6 | T025 迁移测试把 FK targets 从有序列表退化为 set，重复 FK 可蒙混 | 保留 set 相等 + 增加 7 项有序 target 列表（计数/序号）断言 | `tests/f02a/test_t025_migration.py::test_tool_versions_match_the_contract_baseline` |
| 7 | `handlers/tool_run.go`/`toolbuild.go` 的死赋值 `_ = summary/_ = logID/_ = result` | 改为 `if _, err := ...` / 直接丢弃返回值，逻辑不变 | `go test ./...` |
| 8 | 0015 注释自相矛盾（insert-once vs upsert） | 改为“一 artifact 一当前事实，重试 upsert 覆盖”；表 COMMENT 同步 | migration 0015 文件头/COMMENT |
| 9 | T038 恢复证据 `resumed_job_id` UNIQUE 迫使后续周期写 NULL，审计链断裂 | 去掉 UNIQUE（保留 FK），每个周期都写 requeued job；`UNIQUE(run_id,attempt_token)` 不变 | `tests/f03/test_t038_recover.py::test_a_second_recovery_cycle_names_the_same_requeued_job`、`tests/f03/test_t038_migration.py` 更新 |

门禁（`E:\Orchara\product`）：`python -m pytest tests/f03 tests/f05 tests/f02a/test_t025_migration.py -q`
= **353 passed in 1504.09s**；`go test ./...` = 22 ok / 9 no-test / 0 FAIL；`go vet ./...`/`gofmt -l .`
无输出；`python tools/sync_spec.py --check` = `no drift: spec copy and contract snapshot match the manifest`。
批次决策边界见 [ADR 0006](docs/adr/0006-f05-f03-execution-boundaries.md)。

## F05/F03 第二次独立复核修复（2026-11-03）

独立复核对 `51b0d4b...b77916c`（36 提交，T028–T038）提出 5 项缺陷：2 项 Standards（退出未核实
数据库排空、构建 workspace 无磁盘配额）与 3 项 Spec（禁用 owner 仍可领取、输入 artifact 可冒充
成功输出、地区名含逗号破坏导出 CSV）。全部在代码中复现并修复；未推送、未关闭 issue、未修改
`contracts/**`/`docs/spec/**`、未改迁移 0001–0024 语义（仅新增列/参数与业务规则）。

| # | 复核项 | 处置 | 证明 |
|---|---|---|---|
| 1 | P1 退出确认只核对执行身份，心跳登记的 checkpoint 连接仍有未提交事务时 Run 已 succeeded | 新增 `checkDatabaseDrainedTx`：按 attempt 的 `checkpoint_connections` 查 `pg_stat_activity`，`active`/`idle in transaction*`/`fastpath` 即拒绝；`ConfirmExit` 于任何写入前调用，`finalizeRunTx` 再防御性复核；409 `database_writes_in_flight` 且零写入 | `tests/f03/test_t031_exit_drain.py`（真实 psql 未提交事务阻塞、提交后同一 exit 恰好终结一次；纯 idle 连接不阻塞） |
| 2 | P1 不可信构建的 `/workspace` 是无配额可写 bind，可写满宿主卷 | 驱动把 `/workspace` 改成 size 限定的 tmpfs（`--workspace-quota-mb`，默认 512，1–4096），cleanup 必须先 umount 并把 `workspace_unmounted` 纳入 `ok`；Go bundle 增加 `RLIMIT_FSIZE`（默认 64 MiB） | `tests/f05/test_t037_build_isolation.py::test_the_build_workspace_has_a_disk_quota`（真实 runsc、8 MiB 配额、hook 捕获 ENOSPC 后继续成功）；`internal/sandbox` 单测断言 RLIMIT_FSIZE/argv |
| 3 | P1 禁用 owner 后排队 Run 仍被 claim 并 running | claim 事务开头把 disabled owner 的 queued Run 置 `failed(owner_disabled)`、job `void`、同事务事件；候选查询同时排除 disabled owner；无 lease 的返回路径也提交这些失败（否则被 deferred rollback 回滚） | `tests/f03/test_t029_claim.py::test_a_disabled_owner_is_never_leased_and_the_run_fails_closed` |
| 4 | P1 成功守卫只查“存在任意 run_artifacts 关联”，输入 artifact 可冒充输出 | 读取 `run_artifacts.relation` 并要求 `output`；`input`/`artifact`/`log` 一律拒绝，新增 `artifact_not_output`；输入 artifact 直传的回归用例 | `tests/f03/test_t031_finalizer.py::test_a_succeeded_proposal_needs_an_owned_ready_linked_artifact` |
| 5 | P2 地区名含逗号/引号/换行时 summary.csv 被字符串拼接破坏 | 改用 `csv.writer`（`lineterminator="\n"`），region 文本做 `= + - @` 公式前缀防护，金额不变；JSON 保留原文 | `tests/f05/test_t030_tool.py::test_region_text_with_csv_metacharacters_stays_one_two_column_row` |

门禁（修复后，`E:\Orchara\product`）：`python -m pytest tests/f03 tests/f05
tests/f02a/test_t025_migration.py -q` = **358 passed in 1564.32s**；`go test ./...` 全绿、
`go vet ./...`/`gofmt -l .` 无输出、`sync_spec --check` no drift。全量门禁结果见下一节。

## F05/F03 本批最终门禁（2026-11-03）

工作目录 `E:\Orchara\product`；分支 `feat/f05-f03-run-and-tools`（`51b0d4b` + 38 commits，含两次
复核修复）；未推送、未关闭 issue。全量测试使用 `--basetemp=E:\orchara-pytest-tmp`（临时目录不占系统盘）。

```text
$ python -m pytest tests -q --basetemp=E:\orchara-pytest-tmp
1120 passed in 3257.27s (0:54:17)

$ python -m pytest tests/f03 tests/f05 tests/f02a/test_t025_migration.py -q
358 passed in 1564.32s (0:26:04)      # f03 203 / f05 149 / t025 迁移 6

$ go test -count=1 ./...
22 ok / 9 no test files / 0 FAIL

$ go vet ./...
(no output) exit=0

$ gofmt -l .
(no output)

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

全部测试为真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP/mTLS + 真实对象字节 + 真实
WSL2/runsc（T033/T037 沙箱与构建）+ Node 页面逻辑；无 mock、无跳过。F04A/F06/F07/F13A/F15A/
F15B 的后续工作按各票报告记为 successor/`not_run`，不因本批门禁升级为 v1 完成。

## F00B/F02A 两轴复核修复（2026-11-02）

对 `feat/f00b-f02a` HEAD `eebc4c4`（当时全量 754 passed）做 Standards / Spec 两轴复核，发现 15 项；
全部在本轮修复或如实登记。未推送、未关闭 issue、未修改 `docs/spec/**`、`contracts/**` 或已应用
迁移 0001–0014。修复顺序：A 状态/证据诚实 → B 安全/行为 → C 代码卫生；决策记录见
[ADR 0004](docs/adr/0004-f00b-f02a-boundaries.md) §45–52。

| # | 类别 | 复核项 | 处置 | 证据 |
|---|---|---|---|---|
| 1 | 状态 | T022 的 Redis AC 只断言“无 REDIS 环境变量”，从未证明清空总线后仍推进 | 新增真实唤醒缝：`background.Notifier` + 默认 `NopNotifier` + `InProcessNotifier`；`Controller.Enqueue`/`ArtifactStore.CompleteUpload` 提交后通知；API 装无消费者实现并把第一次丢弃记日志。真实栈测试让**每次唤醒都被丢弃**，已启动的 maintenance 仍 claim→finalize→ready。Redis 适配器本身仍 `not_run`，不声称清空过 Redis 队列 | `internal/background/notifier.go`、`notifier_test.go`、`cmd/api/main.go`、`tests/f00b/test_t024_artifacts.py::test_a_dropped_wakeup_never_loses_the_finalize_work`、T022 报告 §2/§5.3 |
| 2 | 状态 | T023 AC3 用 `POST /submit == 404` 当“提交拒绝”，已把票写成 passed | AC3 与整票回到 **`partial`**：validate(T025)/debug(T027) 的拒绝是真实端点；submit 的 404 只证明 F02B 端点不存在。报告 §8、PROGRESS 行/段落同步；测试改名为 `test_an_invalid_draft_is_refused_by_validate_and_debug_while_submit_is_absent`，docstring 明确“404 是缺失不是拒绝” | T023 报告 §2/§8、`tests/f02a/test_t023_invalid_draft.py` |
| 3 | 状态 | T025 AC3“worker 能复核字节”只有机制，却写成整票 passed | AC3 记 **`partial (mechanism delivered; worker not built)`**，整票记 `partial`；真正 worker 属 F03/F04A。manifest/golden/依赖/密封/复核机制本身仍 passed | T025 报告页首/§4/§5、`internal/compiler`、PROGRESS T025 行 |
| 4 | 状态 | T026 票面要求区分“业务 job/evaluation/deletion 状态”，0013 却明确不建 deletion 模型 | T026 增加**第三条** partial 原因：deletion 状态属 **F07**、未实现；报告页首/§4/§6/§7 与 PROGRESS 行同步（另两条是合成 runner 与 application-kind runner，均 F13A） | T026 报告、PROGRESS T026 行 |
| 5 | 证据 | T025 的 Python“篡改测试”只比较两个 SHA-256，从不调用 `VerifyManifestBytes`，无法因 verifier 缺陷失败 | 删除该假断言；新增真实 sealed-object 篡改 Go 测试：先验证未篡改对象，再就地覆盖已密封文件的一个字节，调用 `VerifyManifestBytes` 断言 typed `ErrManifestHashMismatch`；重记篡改摘要后仍以 `ErrManifestInvalid` 拒绝 | `internal/compiler/verify_test.go::TestVerifyManifestBytesRejectsASealedObjectTamperedInPlace`、T025 报告 §3.2/§4/§5 |
| 6 | 安全 | `model_validate` 可用任意 ready artifact 当 fixture，违背“不拿业务用户正文试模型” | 新增合成标记 `purpose='model_fixture'`（非上传 purpose）。**两端强制**：CLI enqueue 在写 job 的同一事务校验 purpose/owner/ready（exit 1、零行）；model-calls 在打开字节前重读，非合成 → 422 `fixture_not_synthetic`，他人 → 422 `fixture_missing`，未 ready → 409，缺失 → 404。注册路径仍是管理/测试 SQL 播种（F06 才有 UI），已在报告/ADR 记录 | `internal/background/fixture.go`、`artifacts.go::ReadSyntheticFixture`、`cmd/admin/background.go`、`internal/api/jobdata.go`、`tests/f00b/test_t024_model_calls.py::test_a_business_artifact_is_never_accepted_as_a_model_fixture`、T024 报告 §1.3/§6、ADR §46 |
| 7 | 安全 | 三个公共读只认证不查 scope，`application:read` key 可读任意 owner-visible 记录 | `GET /evaluations/{id}`、`GET /evaluation-reports/{id}`、`GET /background-jobs/{id}` 对 API key 要求 `application:read`，先 scope(403 `insufficient_scope`) 后 owner/admin(404)；会话不受限。更细 scope 属 F13A/契约修订，ADR 记录这是显式过渡 | `internal/api/{background,evaluations}.go`、`tests/f00b/test_t022_job_get.py::test_an_api_key_needs_the_read_scope`、`tests/f00b/test_t026_authz.py::test_api_keys_need_the_read_scope_for_evaluations_reports_and_jobs`、ADR §10/§36/§47 |
| 8 | 一致性 | `/object-links/{token}` 用 `not_found`，其余 404 用 `resource_not_found` | 统一为 `resource_not_found`；`test_t024_links.py` 对每个拒绝同时钉状态码与错误码（不再只看非 200） | `internal/api/links.go`、`tests/f00b/test_t024_links.py` |
| 9 | 卫生 | `catalog/versions.go` 复制了 `catalog.go` 的 owner-or-grant SQL；详情各写各的 | 抽 `catalog.applicationReadPredicate(...)` 供 `ListVisible`/`ListVersions` 共用；`Reader.readAllowed` 供 `GetVisible`/`GetDraft`/`SaveDraft`/`GetVersion` 共用。占位符/参数顺序与行为不变 | `internal/catalog/{catalog,draft,versions}.go`、`catalog_test.go` |
| 10 | 范围 | `internal/workflow` 保留 P05 的 `Publish`/`BuildProvenance` 发布路径 | 删除 `Publish`/`BuildProvenance`/`WorkflowArtifact`/`ValidationResult.Artifact`；`Validate` 只返回 normal form，wire 不再有 `artifact_sha256`；corpus 仍对拍 verdict/reasons/input hash/normal-form hash/canonical text。执行清单属 `internal/compiler`，发布属 F02B | `internal/workflow/validate.go`、`validate_corpus_test.go` |
| 11 | 记录 | `/object-links/{token}` 是非契约传输路径，易被误当用户 API | ADR §19/§48 与 T024 报告 §1.2/§6.1 明确：无会话、无列表、token 即权限、404 不泄露存在性，**不是用户 API** | T024 报告、ADR |
| 12 | 卫生 | `api.New` 十个位置参数，同类型实参可静默互换 | 改为 `api.New(api.Deps{...})` 命名结构体，更新 `cmd/api` 调用点 | `internal/api/server.go`、`cmd/api/main.go` |
| 13 | 卫生 | 五个 jobdata handler 重复 role→decode→attempt→error 前缀 | 抽 `Server.withJobData`（T024 五端点 + T026 evaluation binding 共用），状态码/错误体形状保持不变 | `internal/api/{jobdata,evaluations}.go` |
| 14 | 卫生 | `complete.go` 两处重复重试表达式、四处按 kind 分支 | 新增 `background.CompletionGuard`/`completionGuards map[Kind]CompletionGuard`（与 `handlers.Registry` 对称），`Complete` 不再按 kind 分支；重试收敛为 `retryAllowed(job, code)`，与 evaluation guard 共用 | `internal/background/complete.go`、ADR §51 |
| 15 | 保持 | `/object-links` token 脱敏与 mTLS/Organization 检查 | 保持不削弱：日志仍对 token 脱敏（单测在位），内部 listener 仍要求证书 Organization 与 `X-Internal-Role` 精确相等 | `internal/api/middleware_test.go`、`test_t022_mtls.py` |

### 状态更正汇总（与上方表格一致）

| 票 | 原状态 | 新状态 | 未完成项 / 承接 |
|---|---|---|---|
| T022 | passed | passed | AC1 改为“唤醒缝 + 真实丢通知”证据；Redis 传输仍 `not_run`（无实例），不假装已接 |
| T023 | passed | **partial** | AC3 的 submit 拒绝属 **F02B**；validate/debug 拒绝为真实证据 |
| T025 | passed | **partial** | AC3 worker 复核属 **F03/F04A**；机制已交付并测试 |
| T026 | partial | partial（原因 +1） | 合成 runner 与 application-kind runner 属 **F13A**；**deletion 状态属 F07** |

### 最终门禁（2026-11-02 复核修复收尾，工作目录 `product`）

```text
$ E:\orchara-toolchains\go\bin\gofmt.exe -l internal cmd tools
(no output)                                    exit=0

$ E:\orchara-toolchains\go\bin\go.exe vet ./...
(no output)                                    exit=0

$ E:\orchara-toolchains\go\bin\go.exe test -count=1 ./...
?   	orchara/platform/cmd/admin	[no test files]
?   	orchara/platform/cmd/api	[no test files]
?   	orchara/platform/cmd/maintenance	[no test files]
?   	orchara/platform/cmd/supervisor	[no test files]
?   	orchara/platform/cmd/worker	[no test files]
?   	orchara/platform/db	[no test files]
ok  	orchara/platform/internal/api	2.522s
?   	orchara/platform/internal/audit	[no test files]
ok  	orchara/platform/internal/background	2.259s
?   	orchara/platform/internal/background/handlers	[no test files]
ok  	orchara/platform/internal/catalog	2.388s
ok  	orchara/platform/internal/compiler	1.507s
ok  	orchara/platform/internal/config	0.611s
ok  	orchara/platform/internal/db	0.819s
ok  	orchara/platform/internal/health	2.191s
ok  	orchara/platform/internal/identity	2.436s
ok  	orchara/platform/internal/jcs	1.090s
ok  	orchara/platform/internal/modelproxy	1.912s
ok  	orchara/platform/internal/objectstore	1.784s
ok  	orchara/platform/internal/policy	1.782s
ok  	orchara/platform/internal/supervisor	1.691s
ok  	orchara/platform/internal/uuid	1.634s
ok  	orchara/platform/internal/workflow	1.714s
exit=0

$ python -m pytest tests -q
........................................................................ [  9%]
........................................................................ [ 19%]
........................................................................ [ 28%]
........................................................................ [ 38%]
........................................................................ [ 47%]
........................................................................ [ 57%]
........................................................................ [ 66%]
........................................................................ [ 76%]
........................................................................ [ 85%]
........................................................................ [ 95%]
.....................................                                    [100%]
757 passed in 1454.52s (0:24:14)

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 4.95s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

测试计数变化：`tests/f00b` 125 → **129**（+丢唤醒、+三个夹具/scope 用例）、
`tests/f02a` 100 → **99**（T025 的无效 Python 篡改断言移入 Go）、`tests/f01` 155、`tests/tooling` 21；
全量 754 → **757 passed**。Go 新增：notifier 4 个单测、compiler 真实 sealed-object 篡改测试 1 个。
提交后 `git status --short` 为空；未推送、未关闭 issue。

## F00B/F02A 第二次复核修复（2026-11-02）

独立复核对 `feat/f00b-f02a` HEAD `2191861`（当时全量 757 passed）提出 6 项缺陷 + 1 个 flaky 测试
（`.scratch/f00b-f02a/FIX2-BRIEF.md`）。全部在本轮按“先写失败测试、再修、再跑门禁”处理；未推送、
未关闭/编辑 issue、未修改 `docs/spec/**`、`contracts/**` 或迁移 0001–0014（也不需要新迁移）。
决策记录见 [ADR 0004](docs/adr/0004-f00b-f02a-boundaries.md) §53–59；各票报告同步更新。

| # | 级别 | 缺陷 | 修前失败测试（红） | 修复 | 证明测试（绿） |
|---|---|---|---|---|---|
| 1 | P1 | 暂停的旧 finalize 尝试会覆盖/删除已激活的活动 sealed 对象 | `test_t022_finalize.py::test_a_failed_attempt_never_deletes_an_object_already_sealed_at_the_active_key`（旧 handler 删掉预置活动对象 → `FileNotFoundError`；用 `git stash` 只回退 handler+guard 复现） | `objectstore.AttemptSealedKey(job,token)` + `objectstore.Move`；handler 只写/删尝试 key；`Complete` 验证后原子 move 到 `artifact.object_key` 再激活；拒绝只删尝试 key | `test_t022_finalize.py::test_a_resumed_old_attempt_cannot_corrupt_the_activated_object`、`::test_a_stale_completion_deletes_only_its_own_attempt_key`、`::test_a_successful_completion_moves_the_attempt_object_and_leaves_no_attempt_key`、`internal/background/{finalize_test.go,handlers/artifact_finalize_test.go}`、`internal/objectstore/objectstore_test.go`（真本地 FS） |
| 2 | P1 | 三次租约耗尽只把 job 置 failed，`evaluation_jobs` 永远 running | `test_t026_failures.py::test_exhausted_leases_fail_the_evaluation_without_a_report`（断言 evaluation failed 时实际 `running`） | `recoverExpiredTx` 在终态失败的同事务调用 `failEvaluationForJobTx`（同 error_code/finished_at；可重试恢复不动 evaluation） | 同上（并断言第一次可重试恢复后 evaluation 仍 running、report 行 0） |
| 3 | P1 | 孤儿 sweep 用一次性引用快照删 sealed 对象，可能删掉“已 seal、行未提交”的新对象 | `test_t026_sweep.py::test_sweep_removes_only_orphans_and_is_idempotent`（原断言 sealed 孤儿被删，新断言它必须保留且 `sealed_deferred==1` → `KeyError`） | sweep 只清 staging；`sealed_deferred` 只报告；sealed 孤儿清理记 F07/F13A 且需与登记共享锁 | 同上 |
| 4 | P1 | 四个写端点（create/save/validate/debug）只查认证/owner/scope，从不要求 human `creator` 角色 | `test_t023_creator_role.py::test_a_session_without_the_creator_role_cannot_create_save_validate_or_debug`（修前 create 返回 201） | `api.requireApplicationWrite`：会话→`creator`（403 `creator_role_required`，先于 body/可见性），API key→`application:write`；页面显示要求 | `test_t023_creator_role.py`（4 用例：user 403 且零行、creator 成功、admin 无 creator 403、admin+creator 成功、validate/debug 门禁）；`test_t023_scopes.py` 仍证明 write key 可写 |
| 5 | P2 | 模型调用 replay 返回 `{replayed:true}`，重试的 model_validate 把 passed 变 failed | `test_t024_model_validate.py::test_a_retry_replays_the_original_call_and_keeps_the_report_passed`（修前报告 `compatible:false`、`check:null`）；`test_t024_model_calls.py` replay 断言原数据（修前 `data == {replayed:true}`） | `modelproxy.recordedResult` 同事务重读 sealed model_result、核对记录 hash，原样返回 data/state/call id/result artifact；不可读 → `ErrReplayUnavailable`（不重派发/不编造） | 上述两个用例；ledger 仍 1 行、call id/result artifact 与首次一致 |
| 6 | P2 | 大写 UUID 依赖 id 被报 `dependency_not_found` | `test_t025_dependencies.py::test_uppercase_dependency_ids_validate_like_their_lowercase_twins`（修前 `valid:false`） | `normalizeDependencyID`（trim+小写）用于 SQL/map/去重键/环 roots/manifest 规范集合；原文保留在 definition 与错误定位 | 同上（model/tool/subapp 三类 + 混合大小写去重） |
| 7 | flaky | `test_t022_lease.py::test_an_expired_lease_is_recovered_and_the_retry_succeeds` 偶发 `claimed=1`（期望 0） | 失败即该用例自身；根因是双池各一次 claim 事务之间的 50ms 退避窗口 | 恢复趟收窄为单池 `--kinds artifact_finalize`（一次 drain 只有一次 claim 事务；不引入 sleep）；不是 claim 逻辑缺陷，未改退避/断言 | 同一用例（`tests/f00b/test_t022_lease.py` 8 passed；单独重跑亦通过） |

### 状态与未运行项（第二次复核后）

| 票 | 状态 | 说明 |
|---|---|---|
| T022 | passed | 尝试栅栏 + 第 3 次租约失败的评测对账；Redis 传输仍 `not_run`（无实例） |
| T023 | **partial** | submit 拒绝属 F02B（未变）；本轮新增 creator 门禁，页面显示要求 |
| T024 | passed | replay 语义修正；真实 provider/厂商 adapter 仍 `not_run`（无 endpoint） |
| T025 | **partial** | worker 复核属 F03/F04A（未变）；依赖 UUID 归一 |
| T026 | partial | 合成 runner/application-kind runner 属 F13A、deletion 属 F07（未变）；sweep 现在只清 staging，sealed 孤儿清理明确延后到 F07/F13A（需共享锁） |
| T027 | passed | debug 现在要求 creator 角色（服务端门禁），读路径不变 |

未运行项没有变化，也没有把“组件策略确认”写成运行通过：Redis adapter、S3/MinIO、真实 provider、
worker 字节复核、F13A 真实 runner、F07 deletion 仍按各自状态记录。

### 提交

- `b063c95` `fix(f00b): fence artifact finalize attempts behind per-attempt object keys`
- `68fa973` `fix(f00b): fail the bound evaluation when the job's leases are exhausted`
- `0fc0492` `fix(f00b): restrict the orphan sweep to unreferenced staging objects`
- `65bc308` `fix(f02a): require the creator role for application writes`
- `86455a6` `fix(f00b): replay the recorded model result instead of a placeholder`
- `a883534` `fix(f02a): normalize dependency ids before manifest lookups`
- `fe9f210` `test(f00b): make the expired-lease recovery drain deterministic`
- 本节的 `docs(f00b,f02a)` 提交

### 最终门禁（第二次复核收尾）

工作目录 `E:\Orchara\product`；Go 环境 `GOCACHE=E:\orchara-toolchains\gocache`、
`GOMODCACHE=E:\orchara-toolchains\gomodcache`；PG 隔离集群 `127.0.0.1:55432`。原始输出：

```text
$ E:\orchara-toolchains\go\bin\gofmt.exe -l internal cmd tools
(no output)                                    exit=0

$ E:\orchara-toolchains\go\bin\go.exe vet ./...
(no output)                                    exit=0

$ E:\orchara-toolchains\go\bin\go.exe test -count=1 ./...
?   	orchara/platform/cmd/admin	[no test files]
?   	orchara/platform/cmd/api	[no test files]
?   	orchara/platform/cmd/maintenance	[no test files]
?   	orchara/platform/cmd/supervisor	[no test files]
?   	orchara/platform/cmd/worker	[no test files]
?   	orchara/platform/db	[no test files]
ok  	orchara/platform/internal/api	3.240s
?   	orchara/platform/internal/audit	[no test files]
ok  	orchara/platform/internal/background	2.893s
ok  	orchara/platform/internal/background/handlers	2.744s
ok  	orchara/platform/internal/catalog	2.638s
ok  	orchara/platform/internal/compiler	1.831s
ok  	orchara/platform/internal/config	1.080s
ok  	orchara/platform/internal/db	1.374s
ok  	orchara/platform/internal/health	2.774s
ok  	orchara/platform/internal/identity	2.859s
ok  	orchara/platform/internal/jcs	1.982s
ok  	orchara/platform/internal/modelproxy	2.408s
ok  	orchara/platform/internal/objectstore	1.642s
ok  	orchara/platform/internal/policy	2.054s
ok  	orchara/platform/internal/supervisor	1.677s
ok  	orchara/platform/internal/uuid	1.525s
ok  	orchara/platform/internal/workflow	1.634s
exit=0

$ python -m pytest tests -q
........................................................................ [  9%]
........................................................................ [ 18%]
........................................................................ [ 28%]
........................................................................ [ 37%]
........................................................................ [ 46%]
........................................................................ [ 56%]
........................................................................ [ 65%]
........................................................................ [ 75%]
........................................................................ [ 84%]
........................................................................ [ 93%]
................................................                         [100%]
768 passed in 1473.68s (0:24:33)

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 4.96s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

测试计数变化（相对第一次复核后的 757 passed）：`tests/f00b` 129 → **135**（finalize 尝试栅栏 4、
评测租约耗尽 1、模型 replay 1）、`tests/f02a` 99 → **104**（creator 门禁 4、依赖大小写 1）、
`tests/f01` 155、`tests/tooling` 21；全量 757 → **768 passed**。Go 新增真本地 FS 单测：
objectstore 2（attempt key 向量、Move）、background 4（守卫移动/拒绝/stale/另一尝试）、handlers 4
（写尝试 key、hash/size 失败只删自己的、staging 缺失）。文档提交后 `git status --short` 为空；
未推送、未关闭 issue。

## F04A 线性四节点编译器与 Vue Flow 画布（1/1：T040）

T040（GitHub #41）已交付，状态 **`passed`**（范围 = Python 防御性线性编译器 +
LangGraph stub-adapter 图证明 + 真实 Vue Flow 画布；生产 Run 运行时仍是 Go，真实
python/agent 节点执行属 T042/T043）。证据：`reports/f04a/T040-canvas-and-linear-compile.md`。

| 项 | 结果 |
|---|---|
| Python 校验 | `worker/platform_worker/compiler/validate.py` 只覆盖 `start → python → agent → end`；与 Go 共享语料 16 例逐例对账 `verdict/code/pointer`（`internal/workflow/f04a_corpus_test.go` + `tests/f04a/test_validate.py`）；另 6 例 F04A 额外拒绝（`unsupported_node_type`、`linear_order`、`not_linear_chain`、`unsupported_port`、`unsupported_subapp`、`bad_output_mapping`） |
| LangGraph | `graph.py` 的 `LinearGraph` 先校验再建 `StateGraph`；只按声明边执行；`NodeContext`/`NodeOutcome`（succeeded/failed/waiting/needs_reconciliation）为唯一归一化缝；`stub.py` 确定性回显应用输入→python→agent→end；失败/等待短路；真实 P01 env（langgraph 1.2.4）执行 |
| 画布 | `apps/web/canvas/` 真实 Vue 3 + Vue Flow；palette 只有四类节点；单链连线拒绝有原因；python 只列 ready+approved 工具、agent 只列 passed 模型；合法变量只列 `input#` + 上游 output；错误映射到节点/字段并可点击跳转；成功显示 `X-Manifest-SHA256`；导出只含 `{definition,layout}` 并拒绝敏感字段 |
| Bundle | `npm ci && npm run build` → 提交的 `apps/web/src/assets/canvas.{js,css}`；`node_modules/` 忽略；测试/运行不需要 npm；服务端新增 `/assets/canvas.{js,css}` 静态路由 |
| T023 行为 | 保存仍走 `PUT /draft` + `If-Match`；412/428/重载/下载本地副本/`#debug-entry` 不变；ETag/CSRF 不进 URL/存储 |
| 集成 | 真实 PG + 编译二进制 + 真实 HTTP：草稿 round-trip（含小数坐标）、定位错误（bad_pointer/missing_value/bad_config）、pending model + unapproved/non-ready tool 的 `dependency_not_ready`、非 owner/creator 拒绝、草稿无 run input/secret/memory |
| 门禁 | `go vet` exit 0；`go test ./...` 23 ok/9 no-test/0 FAIL；`gofmt -l` 空；`pytest tests/f04a tests/f02a` **140 passed**（f04a 36 + f02a 104）；`pytest tests/tooling` 21；`sync_spec --check` no drift；`node --check canvas.js` 0；Node 8 模块检查通过 |
| 边界/not_run | 生产 LangGraph worker 未实现（Go runtime 属 T042/T043）；浏览器 DOM/拖拽/焦点未做自动化；全量 `pytest tests` 由 orchestrator；未 push/未关闭 issue；无迁移、无契约/规格改动 |
| T042 | F04A | 运行固定 Python 节点并查看结构数据与文件 | passed |
| T043 | F04A | 运行受控 Agent 节点并校验最终输出 | passed | `reports/f04a/T043-agent-node.md`、`docs/adr/0007-f06-f04a-model-registry-boundaries.md`（T043 追加 D10）、`internal/presetexec/{plan.go,agent.go,linear.go,presetexec.go}`（3/4 节点线性计划、agent 配置校验、确定性 operation UUIDv5、模型轮次 + 固定预设工具真实沙箱 + capability-results 回报、最终输出节点 schema 二次校验、agent data/file/log 行与 node.status、heartbeat 取消/lease deadline、legacy `input#` 形状选择）、`internal/presetexec/{Facts,ModelFact,ToolFact}` + `cmd/supervisor/{client.go,exec.go}`（`InvokeCapability`/`ReportCapabilityResult` worker mTLS、只读 model/tool Facts）、`internal/modelproxy/provider.go`（dev stub 声明工具时确定性首轮工具调用，仍 `stub=true`）、`apps/web/src/{runs.html,assets/runs.js}`（Agent 面板：最终输出/轮次/工具调用 id+name/每次工具操作状态；`describeAgentNode` Node 执行）、`tests/support/t043.py`、`tests/f06/{test_t043_agent_node.py,test_t043_pages.py}`（6+3 用例，真实 PG/mTLS/对象字节/WSL2+runsc）；`go vet` exit 0、`go test ./...` 全绿、`gofmt -l` 空、`tests/f06` 62 passed、`tests/f05`、`tests/f04a` 36、`tests/tooling` 21、`sync_spec --check` no drift；not_run：真实厂商 adapter、`awaiting_approval` 真实写入（T048）、真实栈下 executor 的未知/重复工具调用 guard（服务端先拒绝，仅 Go 单测）、跨 attempt 重试真实 e2e、浏览器 DOM、全量 `pytest tests`/`tests/f00b`；未 push/未关 issue、未改 docs/spec/contracts/迁移 0001–0026 | `reports/f04a/T042-python-node.md`、`docs/adr/0007-f06-f04a-model-registry-boundaries.md`（T042 追加 D8/D9）、`db/migrations/0026_run_node_outputs.sql`（per-node provenance：run/node/kind/artifact/label、PK、FK、kind CHECK、worker 权限边界）、`internal/presetexec/{plan.go,linear.go,presetexec.go}`（start→python→end 单链计划、literal/input#/node:<id># RFC6901 绑定、受限 schema 校验、固定预设工具真实沙箱执行、data/文件/日志分别登记、node.status 事件、取消/超时复用 T036 控制、legacy T033 路径逐字节保留、agent 节点 `agent_node_not_supported` 拒绝）、`internal/execution/noderesults.go`（live attempt + artifact ready/purpose 复核、insert-once provenance、owner 只读投影）、`internal/api/{noderesults.go,server.go,internal.go}`（非契约 `POST /internal/v1/runs/{id}/node-results` + owner-only `GET /api/v1/runs/{id}/node-results`）、`cmd/supervisor/{exec.go,client.go}`（`SubmitNodeStatus`/`RecordNodeResults`、DB 派生 attempt_no）、`apps/web/src/{runs.html,assets/runs.js}`（折叠“节点结果”面板：data/文件/日志、状态与“无结果 vs 失败”、日志截断、Node 纯函数）、`tests/support/t042.py`、`tests/f06/{test_t042_python_node.py,test_t042_pages.py,test_t042_migration.py}`（6+3+2 用例，真实 PG 0026 + 编译二进制 + mTLS + 对象字节 + WSL2/runsc `release-20241118.0`）；`go vet` exit 0、`go test ./...` 24 ok/0 FAIL/9 no-test、`gofmt -l` 空、`pytest tests/f05` 149 passed、`tests/f03` 203、`tests/f06` 53、`tests/tooling` 21、`sync_spec --check` no drift；not_run：浏览器 DOM、全量 `pytest tests`、全量 `tests/f00b`（brief 明令不跑）、T043 agent 真实执行、线性链独立 timeout e2e（与 legacy 共用分支，legacy 超时由 T033 覆盖）；未 push/未关 issue、未改 docs/spec/contracts/迁移 0001–0025 |

| T054 | F09 | 用户确认候选后才保存个人长期记忆 | passed | `reports/f09/T054-memory-candidates.md`、`docs/adr/0009-f09-memory.md`、`db/migrations/0033_memory_candidates.sql`、`internal/memory/{service,candidates,memories}.go`、`internal/api/memory.go`、`apps/web/src/{memories.html,assets/memories.js}`、`tests/f09/`（25 用例）、`tests/support/t054.py`；真实 PG 16.4 / 编译二进制 / 真实 mTLS / 真实 sealed 对象字节 / Node 执行页面逻辑 |
| T058 | F09 | 用户修订或删除记忆后立即停止旧内容召回 | passed | `reports/f09/T058-memory-revision-and-delete.md`、`docs/adr/0009-f09-memory.md`（§11）、`db/migrations/0036_memory_revision_and_deletion.sql`（最终编号；基线 0034，合并时 T055 占 0034、T052 占 0035 后重编号；契约 `deletion_jobs` 最小切片 + F07 债务）、`internal/memory/{service,memories}.go`（Get/Update/Delete + 428/412 + CAS + 同事务 epoch）、`internal/api/{memory.go,server.go}`（GET/PATCH/DELETE，202 `DeletionAccepted`）、`apps/web/src/{memories.html,assets/memories.js}`（冲突恢复/删除状态，Node 执行）、`tests/f09/`（新增 22：迁移 4 + 记忆 15 + 页面 3；T054 25 保持绿）、`tests/support/t058.py`；真实 PG 16.4 / 编译二进制 / 真实 HTTP+CSRF / 真实 sealed 字节 / Node；`pytest tests/f09` **47 passed ×5**（308.18s / 330.67s / 480.97s / 499.18s / 337.02s，最终树后两轮）、`go build/vet/test`、`gofmt -l`、`sync_spec --check` 全绿、f01 边界 2 passed、tooling 21 passed、f10 20 passed；not_run：物理清理/delete_content/lease-retry（F07）、`memory_embeddings`（T063）、运行时召回（T063）、检索缓存（T067）、浏览器 DOM、全量 `pytest tests`；合并提交 `7190b2b` + `895a67f` |
| T063 | F09 | 运行时按上下文预算召回当前应用的已确认记忆 | passed | `reports/f09/T063-runtime-recall.md`、`docs/adr/0009-f09-memory.md`（§12）、`internal/memory/recall.go`（真实 PG 召回源：owner+app、`deleted_at IS NULL`、当前 ready body artifact、候选来源链、逐条 sha256 校验、fail closed）、`internal/memory/service.go`（抽出共享 `readSealedBody`）、`internal/context/context.go`（1/4 记忆预算、新近度排序、最低相关度先截断 `optional_memory`、逐条 memory source/truncation、否定/审批记忆保护、整体压力按条丢弃、线性精确编码）、`internal/presetexec/{presetexec.go,agent.go}`（`Deps.Memory`+`Deps.RunIdentity`，唯一注入点）、`cmd/supervisor/exec.go`+`internal/config/supervisorexec.go`（`ORCHARA_OBJECT_DIR` 可选，run owner+snapshot app scope）、`tests/f09/test_t063_recall.py`（4 用例：包含+隔离+当前修订+来源、over-budget+否定保护、字节缺失 unavailable+恢复、propose 身份 403/201）、`tests/support/t063.py`、`tests/f06/test_t047_context.py`（无记忆 owner 期望 not_run→empty）；真实 PG 16.4 / 编译二进制 / 真实 claim/result/exit / 真实 sealed 字节 / 真实 WSL2+runsc；无新迁移、无新端点；`pytest tests/f09` **51 passed ×2**（合并后最终树 511.80s / 643.37s；中间一轮 50 passed / 1 `sandbox_failed` 抖动，单独复跑通过）、`pytest tests/f10` 57 passed（679.35s）、`pytest tests/f06/test_t047_context.py` 2 passed（48.14s）、`pytest tests/tooling` 21 passed、`go build/vet/test`/`gofmt -l`/`sync_spec --check` 全绿；not_run：自动抽取/propose producer（F04A）、检索打分/嵌入/缓存（T067）、物理清理（F07）、浏览器 DOM、真实厂商模型（沿用 dev stub）、全量 `pytest tests`；propose 身份核实：supervisor/maintenance 403、worker 201，F04A 执行器已用 worker 容量（无需拆分）；合并提交 `0c8a995` |
| T066 | F09 | 在隔离数据中对比 Mem0 与 PG 并决定是否值得接入 | passed（实验已执行；决策 **do_not_adopt**，保留 PG；Mem0 证据标 **stub**，未采用） | `reports/f09/T066-mem0-comparison.md`、`experiments/mem0/`（独立 venv + 34 包 hash lock、fixture seed 66、runner CLI、`pgdriver` 调真实 `internal/memory`、Mem0/SQLite/Qdrant 副本枚举）、`experiments/mem0/evidence/t066-both.json`（合并树重跑 SHA-256 `2acb230c…`；PG real：hit@1 1.0、p50 0.541 ms / p95 9.082 ms（安静首轮 0.560/1.670）、删除后仅 1 个 sealed 对象 + pending job；Mem0 stub：hit@1 1.0、p50 6.119 ms / p95 8.615 ms、删除后 6 处正文副本）、`tests/f09/test_t066_mem0_experiment.py`（16 用例，含真实 PG 侧）、`evidence/{pip-install-mem0ai-2.2.1.log,venv-freeze.txt,lock-verify.log,t066-both.stdout.log,t066-tests-final-1.log,t066-tests-final-2.log,f09-merged.log,go-test-merged.log}`；T066 16 passed ×2（合并树 43.13s / 36.94s）、`pytest tests/f09` 63 passed（合并后，553.71s）、`go build/vet/test`（33 ok）/`gofmt`/`sync_spec` 全绿、tooling 21 passed；未采用 Mem0、未加产品依赖、未改 `internal/`/`contracts/`/`docs/spec/`；剩余门禁 **F11**；合并提交 `9638ddf` |
| T068 | F02B | 创作者提交已评测快照，管理员批准同一 hash 内容并原子创建版本 | passed | `reports/f02b/T068-publication-review.md`、`docs/adr/0021-publication-review-and-versions.md`、`db/migrations/0043_publication_reviews_and_outbox.sql`（review 决策/冻结绑定列 + 一个 live submitted review 的 partial unique + `application_versions` 撤销三字段/CHECK/`UNIQUE(review_id)` + 契约 `platform.outbox`）、`internal/catalog/{publication,publication_cases,publication_report}.go`（submit 绑定当前 manifest hash 与 sealed report、T062 case-run 复查、五类作者案例门禁、冻结快照、幂等；approve 单事务 version+version_dependencies+active 指针+outbox+audit；reject；list/get ACL；`RecheckFrozenDependencies`；`ResolveRunnableVersion`）、`internal/catalog/manifest.go`（依赖 loader 抽 queryer + 冻结闭包复查）、`internal/api/{publication.go,server.go}`（4 个契约 operation + 非契约导出预览）、`internal/evaluation/report.go`+`internal/background/evaluation.go`（共享严格报告解码）、`apps/web/src/{publish.html,reviews.html,assets/publish.js,assets/reviews.js}`、`tests/f02b/`（18 用例：迁移 5 + submit 4 + review 3 + pages 5 + 真实 runner e2e 1；含并发审批、旧版本字节不可变）、`tests/support/t068.py`；真实 PG 16.4 / 编译二进制 / 真实 HTTP+CSRF+mTLS / 真实 sealed 字节 / 真实 WSL2+runsc（e2e）/ Node；`pytest tests/f02b` **18 passed ×6**（合并前 290.87s/242.53s；T069 合并后 254.04s/251.68s；T070 合并后 261.65s/252.17s；均含真实 runner e2e，见报告 §3）、`tests/t070` 12 passed、`tests/f02a` 103 passed + 1 个 T074 交付后的旧 FK 断言已同步更新并复跑通过、`tests/tooling` 21 passed、`tests/f13a -k "t052 or t056 or t062"` 42 passed + 9 deselected、`go build/vet/test`、`gofmt -l`、`sync_spec --check` 全绿；not_run：published Run 创建（T074）、child run（T075/T078）、outbox 消费者/目录索引（T077/T082）、浏览器 DOM、全量 `pytest tests`；未改 `docs/spec/**`/`contracts/**`；T070 已合并并复用本票 outbox/撤销列/`ResolveRunnableVersion`（`reports/f02b/T070-version-revocation.md`，合并提交见报告） |
| T070 | F02B | 撤销问题版本并阻止陈旧目录继续启动 | passed | `reports/f02b/T070-version-revocation.md`、`docs/adr/0022-f02b-version-revocation.md`、`internal/catalog/revocation.go`（`ValidateRevokeVersionRequest` 严格两字段体；`RevokeVersion` 单事务锁定 version+application，`revoked_at IS NULL` CAS 自然幂等（契约无 Idempotency-Key），只写 `revoked_at/revoked_by/revoke_reason`；活动指针置 NULL **不重指**，previous 记入 outbox+audit；`stop_existing` 选中非终态 Run，`cancel_requested_at`+`error_code=version_revoked`（first-write-wins）并经 `execution.AppendRunStatusTx` 追加一条 `run.status`，**不写 Run 终态**；`version.revoked` audit）、`internal/catalog/outbox.go`（唯一 `insertOutboxTx`，T068 发布与 T070 撤销共用；`catalog_index/op=revoke` payload 含 previous_active_version_id，无消费者）、`internal/catalog/versions.go`（`ListVersions`/`GetVersion` 加 `revoked_at IS NULL`；契约 Version 无状态字段 → 省略/404）、`internal/api/revocation.go`+`server.go`（契约 `POST /versions/{id}/revoke` admin+CSRF 200 Accepted、定位 422/404；非契约 `GET /versions/{id}/runnable` 薄封装 `catalog.ResolveRunnableVersion`：200/409 `version_revoked`/409 `version_archived`/404）、`apps/web/src/{versions.html,assets/versions.js,apps.html}`（管理员版本/撤销页 + 目录“不可用：没有生效版本（未发布或已撤销）”，Node 执行纯函数）、`tests/support/t070.py`、`tests/t070/`（12 用例：真实编译器 preview 封存的真实对象字节 + 真实 PG 16.4 + 编译二进制 + 真实 HTTP 会话/CSRF + 真实 seeded `published` 快照/Run + Node）；`pytest tests/t070` **12 passed ×2**（合并 T068 最终树 153.08s / 150.00s）、`pytest tests/f02a` **104 passed**（1107.33s）、`pytest tests/tooling` 21 passed、`go build/vet/test ./...`/`gofmt -l`/`sync_spec --check` 干净；无新迁移（撤销三列/CHECK/outbox 由 T068 `0043_publication_reviews_and_outbox.sql` 提供；合并时 T068 把基线 0042 重编号为 0043，避免与 T069 的 0042 重号）；not_run：T074 真实 published Run 创建与创建时守卫、T077 outbox 消费/目录索引、queued claim 读取消意图、浏览器 DOM、全量 `pytest tests`（orchestrator）；未改 `docs/spec/**`/`contracts/**`；合并提交见报告 §3.6 |
| T075 | F07 | 在创作台选择固定子应用并校验依赖 | **partial**（AC1–AC3 由 T078 #79 闭环真实发布事务与固定引用；AC4 的 F04A 画布 `call_app` palette **not_run**） | `reports/f07/T075-fixed-subapp-deps.md`、`docs/adr/0024-f07-fixed-subapp-delegation.md`（编号 0024：0023 已被 T074 会话 ADR 占用）、`internal/catalog/delegation.go`（`checkChildDelegation`：`call_app` 输入映射/类型、输出细化、`max_child_calls`/`max_child_depth`；`io_mapping_unknown_field`/`io_mapping_missing_field`/`io_mapping_type_mismatch`/`io_output_unknown_field`/`io_output_type_mismatch`/`child_calls_exceeded`/`child_depth_exceeded`）、`internal/catalog/{manifest,delegation_test}.go`（子版本 `manifest -> 'definition'` 冻结 schema 随就绪检查一次取回；循环检查复用同一条 `version_dependencies` 闭包给深度规则；无 definition 版本 `dependency_not_ready`）、`internal/presetexec/plan_test.go`（`call_app` 仍 `tool_unsupported_graph` 的回归守卫，T078 接口）、`apps/web/src/{editor.html,assets/editor.js}`（账户/版本选择器只用既有 ACL 过滤读；按冻结 schema 生成 I/O 映射行；插在 end 前并改线；保存后 `POST /validate`，节点条标红并列出 code/pointer/message；F04A 画布 bundle 未重建）、`tests/support/{t075.py,t025.py}`（发布器存储形状 fixture；`seed_published_version` 带冻结 definition + revoke 三列）、`tests/f07/{test_t075_delegation.py,test_t075_pages.py,conftest.py}`（9+3 用例；真实 PG 16.4 + 编译 `cmd/api` + 真实 HTTP creator/CSRF + 真实对象字节 + Node 执行页面逻辑）；`pytest tests/f07` **12 passed ×2**（最终集成树 111.05s / 104.28s；此前合并树 122.91s / 117.40s）、`tests/f02a` 合并树 **104 passed / 0 failed**（1103.53s）、`tests/tooling` 21 passed、`go build/vet/test ./...`/`gofmt -l`/`sync_spec --check` 全绿；not_run：F04A 画布 `call_app` palette（**not_run**，本票允许编辑面板回退）、真实 WSL2/runsc 五类评测（并发沙箱占用）、浏览器 DOM；真实发布事务写边/父发布后复断言已由 **T078 #79** 用真实 submit/approve 闭环（`tests/f07/test_t078_child_runs.py::test_real_publication_pins_the_child_and_closes_t075`）；未 push/未关 issue、未改 `docs/spec/**`/`contracts/**`、无迁移 |
| T078 | F07 | 主应用委派后查看独立子运行并取回结果 | passed | `reports/f07/T078-child-runs.md`、`docs/adr/0025-f07-child-runs.md`、`db/migrations/0049_f07_child_runs.sql`（分支内下一空号；orchestrator 目标 0052，合并重编号；`platform.run_children`：child/parent/attempt/version FK、`parent_invocation_key` UNIQUE、`input_sha256`+input artifact、`io_mapping{node_id,fields}`、input/output schema sha、required/depth；不复制 child 状态机、不建 child 槽；worker/PUBLIC 零权限）、`internal/children/`（`Spawn` 单事务：父 Run 锁→live attempt→幂等预读（同 key 同版本同规范输入→原 child 无写入；异输入/版本 409 `child_invocation_conflict`+details.field）→`catalog.ResolveRunnableVersionTx` 当前 ACL（403 `child_not_visible`/409 revoked/archived）→父冻结 snapshot `subapp_versions` 成员（403 `child_not_in_dependency_closure`）→复用 T075 `CheckChildSpawn`→输入键恰等于声明 mapping（agent 子集）→子冻结 input schema→运行期深度/次数（422/429）→JCS 封存 `run_input`→子版本自身 published snapshot+child Run（owner=父 owner、独立 thread）+queued 事件+start job+input link+收据；`Result` 父 live attempt+父子链接+终态结果；`ListForParent`）、`internal/execution/result_validation.go`（finalizer 内 `ResultValidator`：子版本冻结 output schema 校验真实 artifact 字节；不合规 child 失败 `child_output_schema_invalid`，父不动）、`internal/catalog/childspawn.go`（`CheckChildSpawn`/agent 子集映射/`ChildSchemaHashes`/`ValidateChildInput`/`ValidateChildResult`）、`internal/presetexec/`（线性计划 `start->[python]->[agent]->[call_app]->end` 与 `start->end` 身份链；`call_app` logical key `call_app:<node>`、live child→`waiting_children`+`child:<id>` checkpoint、终态结果读+节点 schema 再校验、required 失败；`agent.subapp_versions` 派生 `call_app_1..N` 工具→spawnChild；真实栈已执行；`SpawnChild/ChildResult` Client seam）、`cmd/supervisor/client.go`（worker mTLS 两方法）、`internal/capabilities/service.go`（sealed `subapp_versions` 作为模型工具声明，仍不授予 tool capability）、`internal/api/{children,internal,server}.go`（契约 `POST /internal/v1/runs/{id}/children` 202 Run；非契约 child result 读；非契约 owner `GET /api/v1/runs/{id}/children`）、`worker/platform_worker/agents/delegate.py`（`PublishedAppDelegate`+`DelegateTransport`+真实 mTLS `HTTPDelegateTransport`；live child→waiting、终态结果本地 schema 再校验；无 DB/建 Run 路径）、`worker/platform_worker/agents/agent.py`（`ToolExecutionError(waiting=True)`→`NodeOutcome(waiting)`）、`apps/web/src/{runs.html,assets/runs.js}`（child panel：列表/状态/结构化结果/打开 child 详情；Node 纯函数）、`tests/support/t078.py`、`tests/f07/{test_t078_child_runs.py,test_t078_pages.py}`（8+3 用例：重放/冲突/闭包/封存最小输入/并发隔离/权限交集/结果 schema 失败归 child/真实 supervisor 委派+恢复取结果/真实发布闭环/Python delegate/agent 子应用/页面）；真实 PG 16.4 + 编译 `cmd/api`/`cmd/supervisor`/`cmd/maintenance` + 真实 HTTP/mTLS + 真实对象字节 + 真实 result/exit + Node；`pytest tests/f07` **22 passed ×2**（284.83s/226.73s；最终树加入 agent 用例后复跑见报告 §3.6）、`tests/tooling tests/f02a/test_t025_dependencies.py` 27 passed、`tests/f03` claim/waiting/cancel 26 passed、`tests/f06` T041/T043 见报告 §3.7、`go build/vet/test`/`gofmt -l`/`sync_spec --check` 全绿；not_run：child 终态自动恢复/独立子槽/每父并行（T081，测试用 T035 bridge）、父预算/次数原子（T080）、真实 WSL2/runsc 五类评测（依赖 T081；T075 闭环用 T068 已验证 fixture）、浏览器 DOM、全量 pytest；T075 报告 §1.4/§4/§5 与 PROGRESS 已同步 closed；未 push/未关 issue/未改契约/规格 |
| T081 | F07 | 父任务满载等待时子任务仍能运行并收敛 | passed | `reports/f07/T081-waiting-capacity.md`、`docs/adr/0032-f07-waiting-capacity.md`、`db/migrations/0051_f07_waiting_capacity.sql`（worktree 树内下一空号；orchestrator 目标 0054，最终合并按合并树重编号；`runs.last_child_claim_at` 父轮转键 + `global/child_slots` cap 8 + 首次 child claim 懒创建 `parent:<parent_run_id>/child_slots` cap 3；不新增 `run_children` 状态列；worker/PUBLIC 零权限）、`internal/execution/{execution,store,service,cancel,cancel_store,children_hooks}.go`（候选查询按 `runs.parent_run_id` 路由到 `execution_slots`/`child_slots` 并过滤已满池，child 按父 `last_child_claim_at` 轮转 + job 年龄；claim 固定锁序 global→parent 取槽并写两条 `quota_allocations`，顶层行为不变；`releaseAllocationOnceTx` 改为按 holder 释放全部行（child 两行各一次、重复 finalize/cancel/replay no-op）；`ConfirmExit/SubmitResult/Cancel/ResolveUnknownOperation` 提交后通知 `ChildLifecycle`；导出 `ResolveChildrenWaitingTx`/`MaybeEnqueueResumeTx`/`RedriveParentAfterChildrenStoppedTx`）、`internal/children/hook.go`（child 终态自动收敛：可从收据确定性产生唯一 children decision、必需 child 失败/取消先传播取消到存活兄弟、cancelling 父由 hook 重驱唯一 finalizer、等待决议只走同一 `maybeEnqueueResumeTx`；`Claim` 前有界幂等 `Reconcile` 覆盖提交后崩溃窗口）、`internal/api/server.go`（装配 `ChildLifecycle` 与 T036 Cancel seam）、`tests/support/t081.py`、`tests/f07/test_t081_waiting_capacity.py`（6 用例：20 父等待 + Python 池恒占 1 下 20 次真实 supervisor 子执行全部 succeeded 且父自动 resume、每父 cap 3 + 父轮转公平领取、父取消等待 running child 停止且数据库时钟 `child.finished_at <= parent.finished_at`、必需子失败传播为父 `child_output_schema_invalid`、T038 recovery 路径两条 child allocation 单次释放；重复 cancel/exit/recover 后 release ledger 不变）；真实 PG 16.4 + 编译 `cmd/api`/`cmd/supervisor`/`cmd/admin`/`cmd/maintenance` + 真实 HTTP/mTLS + 真实对象字节 + 真实 claim/result/exit + 真实 `runs sweep-leases`/`runs recover`；`pytest tests/f07` **36 passed ×2**（合并 T080/T071 后 411.33s / 451.80s；最终合并 T079 后同一棵树 480.93s / 493.23s；T081 隔离树 29 passed 462.36s，见报告 §3）、f03 claim/concurrency/exit/finalizer/waiting/resume/cancel 子集 + `tests/tooling`（见报告 §3）、`go build/vet/test`/`gofmt -l`/`sync_spec --check` 全绿；not_run：共享父预算/次数原子（T080）、真实 WSL2/runsc 五类评测、顶层配额与每会话逻辑槽（F08/T074 请求队列，本票不读写）、浏览器 DOM、全量 `pytest tests`；未 push/未关 issue/未改 `docs/spec/**`/`contracts/**`；合并 `integration/t068-t082`（含 T080/T071/T079）提交 `0e62587` |
| T080 | F07 | 并行子调用只消耗同一份父预算 | passed | `reports/f07/T080-parent-budget.md`、`docs/adr/0031-f07-parent-budget.md`、`internal/budget/ledger.go`（`LockAllowanceTx`：与 `ReserveTx` 相同的顶层祖先解析 + 根 Run 行 `FOR UPDATE`，返回 limit/currency/charged/outstanding/remaining 十进制串，`big.Int` 钳零、无 float；只读，不建第二账本）、`internal/children/service.go`（`Service.Budget`；`Spawn` 在父 Run 锁后、封存输入前用 `LockAllowanceTx` 冻结 child `budget_limit/currency` = 共享根额度；剩余为 0 → 429 `budget_exhausted`（details.remaining_amount/currency）；幂等重放先于额度读取，恢复不重置也不搁浅父任务；深度/次数仍是父 Run 锁内的行计算，无计数器）、`tests/support/t080.py`、`tests/f07/test_t080_parent_budget.py`（7 用例：两 child 模型预留经预算根行锁并发门 → 恰好一个 429 `budget_exhausted`、loser 零账本行/零 attempt、金额 `numeric(20,8)` 文本；父行锁下 3 个并发 spawn → 恰好 2 个 202 + 1 个 429 `child_calls_exceeded`，无孤儿 Run/artifact；嵌套深度 = 祖先链 2 > 父冻结 1 → 422 `child_depth_exceeded`；零额度 spawn 429；tiny 额度 child 模型预留 429 → child `budget_exhausted` → required-child 收敛父 Run 同码；真实 `orchara-admin runs recover` 后账本逐行/额度/冻结上限不变且后续预留仍 429；备用模型同根仅一次 charge）、`pytest tests/f07` **30 passed ×2**（442.55s/384.54s）、`tests/tooling` 21 passed、`tests/f06` T046 预算 12 passed、`tests/f03` claim/waiting/cancel 26 passed、`go build/vet/test ./...`/`gofmt -l .`/`sync_spec --check` 全绿；无新迁移（复用 0028 账本 + 0049 `run_children`）；真实 PG 16.4 / 编译二进制 / 真实 mTLS worker surface / 真实并发 child / 真实 admin recover；not_run：T081 自动 child 终态 hook（父收敛用例用既有 T035 bridge）、浏览器 DOM、真实厂商模型（dev-stub）、全量 `pytest tests` |
| T071 | F11 | 异步清理对象和索引并可观察失败重试 | passed | `reports/f11/T071-async-purge.md`、`db/migrations/0047_deletion_purge_paths.sql`（retrieval cache `items` GIN jsonb_ops 扫描路径；开发期依次重编号 0044→0045→0046，集成合并终版 **0047**，测试按名字后缀断言）、`internal/deletion/purge.go`（scope 目标派生：job descriptor+已 tombstone 文档/历史 memory body；五类 live 引用保护→`object_shared_reference`；`store.Stat` 验证后才写 `artifacts.purged_at`/`state='deleted'`；chunk/vector+index version+active 指针+cache 行清理；同一事务写 `remaining_scopes/completed_scopes/state/attempt_count`；失败码 `PurgeFailure.ErrorCode`）、`internal/deletion/deletion.go`（`PurgeEnqueue` 接缝，T069 删除事务内 enqueue，enqueue 失败即回滚）、`internal/api/server.go`（`background.EnqueueTx` 装配，dedup=`deletion:<deletion_job_id>`；与 T074 conversation Target 同时保留）、`internal/background/{delete_content.go,delete_content_test.go,job.go,complete.go,handler.go}`（completion guard：对象仍在→retryable transient_storage_io、共享→business object_shared_reference、从不返回 guard error；retry 表；`delete_timeout`；映射单测）、`internal/background/handlers/delete_content.go` + registry、`cmd/maintenance/{run.go,sweep.go,main.go}`（`--delete-content-timeout`/`ORCHARA_MAINTENANCE_DELETE_CONTENT_TIMEOUT`；sealed 孤儿“五类引用+max(年龄,10m grace)”删除）、`internal/config/config.go`、`tests/support/t071.py`、`tests/f11/test_t071_{migration,purge,failure,orphans,conversation}.py`（10 用例：迁移 3 + 三 scope 幂等 3 + 真实 store 失败/重试与共享引用 2 + sealed sweep 1 + T074 conversation object_store 清理与未知资源 fail-safe 1）、`tests/f00b/test_t026_sweep.py`（sweep 行为变化注释同 change，断言不变）、`tests/f05/test_t028_migration.py`（**test-only 集成兼容修复**：T074 的 0044 让“conversations/requests 不存在”断言失效，收窄为仍成立的 `deletion_scopes` 不存在并注明归属）；`pytest tests/f11` **最终集成树（含 T072/T073）68 passed ×2**（846.70s/979.59s）+ T071 切片 32 passed ×2（348.53s/334.38s）、`tests/f00b`+`tests/f10`+`tooling` 最终树 41 passed（291.56s）、f05 artifact/tool-run 57 passed（621.62s）、`go build/vet/test ./...`/`gofmt -l .`/`sync_spec --check` 全绿；not_run：timeout 实际触发、attempt 耗尽终态、T076 无复活全套、真实 S3/MinIO、浏览器 DOM、全量 `pytest tests`；T072 四 scope 与 T073 诊断由对应票在集成树交付；未 push/未关 issue/未改 `docs/spec/**`/`contracts/**`；集成合并 fast-forward 到 `a4e946d` 后再 merge 到最终 tip `affc12c`（合并提交 `5235711`），并把 T078 的重复 `0048_f07_child_runs.sql` 只重编号为 `0049`（集成 `affc12c` 同一修复）|

| T082 | F08 | 切换处理应用前确认最少交接信息 | passed（服务端最小交接预览 + 摘要/附件确认门禁 + 来源产出进入新 Run 输入闭包 + 固定版本切换零路由/零控制调用 + 撤销/不可见版本拒绝 + 队列/槽语义 + 处理版本可见 + `/chat` 交接面板与 Node 纯函数全部真实交付；交接摘要只含元数据，运行结果正文不传递属设计边界） | `reports/f08/T082-cross-app-handover.md`、`docs/adr/0030-t082-cross-app-handover.md`、`db/migrations/0052_handoff_scope_bounds.sql`（非契约 `request_handoffs` 具名 CHECK：来源 Run 1..5、附件 0..5；重复 migrate no-op；worker/PUBLIC 零授权；T082 原编号 0051，合并 T081 后按 BRIEF §5 用下一个空位 0052，测试按 slug 断言）、`internal/requests/handoff.go`（非契约 `POST /api/v1/conversations/{id}/handoff-preview` 的 `ParseHandoffPreview`/`HandoffPreview`；来源 Run owner+同会话+published 版本校验；附件走 T074 `checkArtifacts`；`buildHandoffSummary` 单源元数据摘要，字段 rune 裁剪、总长硬上限 2000、可复算；`verifyHandoffScope` 提交门禁：摘要必须等于服务端重算预览，附件范围=来源 Run `relation<>'input'` 产出 ∪ 本次 `artifact_ids`，越界/篡改 `422 handoff_scope_exceeded`，外来来源仍 `422 handoff_source_unknown`，未确认仍 `422 handoff_not_confirmed`）、`internal/requests/{service,submit}.go`（handoff 上限进入解析；`runInputArtifactIDsTx` 新 Run 输入闭包加入 `request_handoffs.artifact_ids`）、`internal/api/{requests,server}.go`（只读预览 handler + 路由）、`apps/web/src/{chat.html,assets/chat.js}`（来源 Run→读取产出附件→生成预览→确认；摘要只读；`handoffPreviewPath/handoffPreviewBody/describeHandoffPreview/handoffFromPreview/handoffArtifactOptions` Node 执行；处理版本仍读 `X-Request-Application-Version-Id/-No`）、`tests/support/t082.py`、`tests/f08/test_t082_{switch,handover,migration,pages}.py`（11 用例：固定版本零路由/权限 2 + 预览边界 1 + 确认门禁 1 + 隔离 1 + 来源产出交付 1 + 迁移 3 + 页面 2；另 Go `internal/requests/handoff_test.go` 2 条摘要边界/确定性单测）；`pytest tests/f08` **65 passed ×2 背靠背**（825.37s/799.41s）、`tests/tooling tests/f11/test_t071_conversation.py` **22 passed**（27.70s）、最终 merge `bec622e`（integration @ `6e52004`，迁移 0051→0052）后 `tests/f08` **65 passed**（637.46s）+ `go build/vet/test`/`gofmt -l`/`sync_spec --check` 复跑全绿、`go build/vet/test ./...`/`gofmt -l`/`sync_spec --check` 全绿；真实 PG 16.4 + 编译 API/admin/maintenance + 真实 HTTP+CSRF + 真实封存字节 + Node 24；not_run：全量 `pytest tests`、浏览器 DOM、路由内部（T079，本票固定版本路径刻意零模型调用）、子 Run/预算（T078/T080/T081）、发布/撤销/删除实现（消费既有行为）、真实厂商模型（无模型调用）；**测试变更披露**：`tests/f08/test_t074_isolation.py` 成功交接改为先取 T082 预览再提交（隔离断言集不变，T082 有意收紧）；未 push/未关 issue、未改契约/规格 |

P01 venv 说明：worktree 内 `.venv` 不在 git，重建因环境 TLS CA bundle 问题失败并删除空
venv；门禁用 `ORCHARA_P01_PYTHON` 指向主 checkout 的 P01 锁定 interpreter。测试缺失时
`pytest.fail`（不 skip）。

## F07 固定子应用依赖（T075，2026-11-05）

T075（GitHub #76）状态 **`partial`**（范围 = 创作台选择固定子应用版本、声明并校验 I/O 映射、
发布门禁对真实依赖闭包执行深度/次数限制；**不含**子运行执行/路由/撤销端点）。AC1–AC3 的真实
发布事务与固定引用已由 **T078 #79** 闭环；AC4 的 F04A 画布 `call_app` palette 仍 **`not_run`**
（本票允许编辑面板回退，未交付项就是这一项）。证据：
`reports/f07/T075-fixed-subapp-deps.md`、ADR 0024。

| 项 | 结果 |
|---|---|
| 固定版本 | `call_app.config.application_version` + `agent.subapp_versions` 进入冻结 manifest 的 `subapp_versions`；只接受显式 published version id，无 latest；F02B approve 事务按该集合写 `version_dependencies`（代码锚点 + Go 依赖集测试；发布端到端见下） |
| I/O 映射 | 子版本冻结 `definition.input_schema/output_schema` 决定可映射字段/类型；必填缺失、未声明字段、静态类型不兼容、输出越界/类型不兼容都定位到 `/nodes/<i>/...`；节点输出只能是子输出的子集，不可发明字段 |
| 限制 | `max_child_calls` = 声明引用数（`call_app` 每节点 1、agent 允许集去重每版本 1），`max_child_depth` = 真实 `version_dependencies` 闭包深度（子=1、孙=2）；超限定位 `/policy/max_child_calls` / 具体节点 config 字段 |
| 创作台 | editor 自有 `call_app` 面板：ACL 过滤的应用/版本选择器、按冻结 schema 的映射行与输出声明、插入/改线、保存并校验、节点条高亮；F04A 画布 bundle 未重建（palette 项 not_run） |
| 发布固定 | **T078 #79 已闭环**（`tests/f07/test_t078_child_runs.py::test_real_publication_pins_the_child_and_closes_t075`）：父草稿冻结的 manifest 字节/哈希/`subapp_versions`、版本行、依赖边与版本读在子 v2 存在后不变，切到 v2 立即被拒；含 `call_app` 的版本已由真实 submit/approve 发布事务写出 `version_dependencies` 边 |
| 运行时不支持 | `call_app` 仍以 `tool_unsupported_graph` 确定性拒绝（新增回归守卫）；子运行、预算、等待属 T078/T080/T081 |
| 门禁（合并树 `1a201b3`） | `pytest tests/f07` **12 passed ×2**（122.91s / 117.40s）、`tests/f02a` **104 passed**、`tests/tooling` 21 passed、`go build/vet/test ./...` 无 FAIL、`gofmt -l` 干净、`sync_spec --check` no drift |

## F06 受控能力代理（T041，2026-11-03）

T041（GitHub #42）已交付，状态 **`passed`**（范围 = 运行域 Go 能力代理：模型调用全链路 +
工具调用授权/worker 回报；`invokeCapability` 契约端点 + 非契约 `capability-results`；迁移
0025 `operations`/`operation_attempts`；owner-only 账本读取 + `/runs` 受控调用账本面板；
Python worker adapter/agent loop）。证据：`reports/f06/T041-capability-proxy.md`。

| 项 | 结果 |
|---|---|
| 授权 | run running + live attempt + 封存 manifest SHA-256/allowlist + kind 歧义拒绝；全部先于 provider |
| 账本 | 逻辑 operation 一行、物理 attempt 一行；JCS params SHA-256；同 op+params 回放、logical-key 冲突 409 |
| 模型调用 | profile passed/未撤销；Go 唯一重试权威（transient ≤2 次重试）；tool-call id/name/arguments 与 response_schema 全校验后才封存 receipt |
| 工具调用 | ready+approved（或 debug 作者拥有）；grant 绑定 run/attempt/params hash；结果端点幂等回报；真实执行 T043 |
| 账本读取 | owner-only `GET /api/v1/runs/{id}/capabilities`；不含 params/receipt 正文；`/runs` 面板 Node 测试 |
| 适配器 | `worker/platform_worker/agents`：每次 generate 一次物理调用、无隐藏重试；agent loop 只返回 NodeOutcome |
| 真实栈 | 真实 PG/mTLS/HTTP/对象字节：`tests/f06` 42 passed；真实本地 Python 工具执行 + 2 轮真实端点 loop（transport 调用=2=operation 数=attempt 总数） |
| 门禁 | `go vet` exit 0；`go test ./...` exit 0（24 ok/0 FAIL）；`gofmt -l` 空；`pytest tests/f06` 42；`tests/f04a` 36；`tests/tooling` 21；目标 T024 12；`sync_spec --check` no drift |
| 边界/not_run | 真实厂商 adapter、沙箱 python/agent 节点（T042/T043）、connector（T050）、预算结算（T046）、审批来源（T048）、浏览器 DOM、全量 `pytest tests`/全量 `tests/f00b`（Windows 管道风险，仅目标 T024） |
| 提交 | 5 个里程碑提交（迁移/服务、HTTP/测试、worker、页面、报告/文档）+ 3 个加固/证据/偏差说明提交（worker 角色 + manifest digest 校验、门禁复跑、已知偏差）；未 push、未关 issue、未改 docs/spec/contracts/迁移 0001–0024 |

## F04A 固定 Python 节点执行与节点结果（T042，2026-11-03）

T042（GitHub #43）已交付，状态 **`passed`**（范围 = 线性 `start → python → end` 调试运行、
显式绑定解析、固定预设工具的真实 WSL2/runsc 执行、每个 python 节点的 data/文件/受限日志
provenance + owner 只读接口 + `/runs` 折叠面板；agent 节点执行明确留给 T043 并
`agent_node_not_supported` 拒绝）。证据：`reports/f04a/T042-python-node.md`。

| 项 | 结果 |
|---|---|
| 迁移 | 0026 `platform.run_node_outputs`：run/node/kind/artifact/label + 复合 PK；kind 仅 data/file/log；artifact 行仍是内容权威；无第二内容存储；worker/PUBLIC 无权限 |
| 计划 | 封存 definition 必须是 1 start + 1 python + 1 end、恰好 2 条链式边；分支/汇合/多 python/其他类型拒绝；agent → `agent_node_not_supported`（T043 缝）；python 必须引用固定预设工具 |
| 绑定 | `literal`/`input#/...`/`node:<id>#/...` 的 RFC 6901 解析；python `file` 必须解析为本 run linked input 的服务器事实；缺失/类型不符 → 定位 `node_input_invalid`（沙箱不启动） |
| 执行 | 真实 runsc：start 校验 `input_schema` 并附单一 task_input 事实；python 下载/校验 size+sha → 固定 argv/uid/no-network → 严格 output.json → 节点 schema → admit 文件 → 登记 data/文件/日志；end 映射 `output_schema` → final_output → 成功 proposal/exit |
| 节点结果 | 非契约 `POST /internal/v1/runs/{id}/node-results`（worker/supervisor mTLS + live attempt + ready/purpose 复核 + 幂等）+ owner-only `GET /api/v1/runs/{id}/node-results`（artifact 事实，无内容）；日志 `purpose=log` 不进 `listRunArtifacts`；原始 output.json 从不成为 artifact |
| 事件 | 每节点 pending→running→succeeded/failed/skipped 一条 `node.status`（payload 恰好 node_id/state/attempt_no，无正文）；失败/取消不提交成功，后续节点 skipped；attempt_no 由 supervisor DB 派生 |
| 取消/超时 | 复用 T033/T036 runner 控制；真实取消 e2e：stats failed、end skipped、无 node result、无输出 artifact、无沙箱残留；线性 timeout 与 legacy 共用同一分支（legacy T033 超时已覆盖），报告如实标注未单独跑 |
| 兼容 | golden `start/python/agent/end` 图在恰有一个预设 python 节点时走原 legacy 路径，T033/T037 行为逐字节不变（真实 e2e 断言 summary/CSV/log/列表一致且不产生 node result）；agent-only 图才拒绝 |
| 页面 | `/runs` 折叠“节点结果”面板：按节点状态、data pretty JSON、文件下载、日志截断；`describeNodeResults` 等纯函数 Node 执行；“无结果”与“失败”分离；无 localStorage/sessionStorage/object key |
| 真实栈 | 真实 PG/迁移 0026/编译二进制/mTLS/对象字节/WSL2+runsc `release-20241118.0`；`tests/f06/test_t042_*` 11 用例（6+3+2）全部通过 |
| 门禁 | `go vet` exit 0；`go test ./...` 24 ok/0 FAIL/9 no-test；`gofmt -l` 空；`pytest tests/f05` 149；`tests/f03` 203；`tests/f06` 53；`tests/tooling` 21；`sync_spec --check` no drift |
| 边界/not_run | 浏览器 DOM、全量 `pytest tests`（orchestrator）、全量 `tests/f00b`（brief 明令不跑）、T043 agent 真实执行、T049 后续 UI |
| 提交 | 3 个里程碑提交（0026+provenance API、线性执行+事件+集成测试、面板+页面/迁移测试）+ 报告/文档提交；未 push、未关 issue、未改 docs/spec/contracts/迁移 0001–0025 |

## F04A 受控 Agent 节点与最终输出校验（T043，2026-11-04）

T043（GitHub #44）已交付，状态 **`passed`**（范围 = 四节点线性链 `start → python → agent → end`、
Go 受控 agent 循环（T041 模型轮次 + 固定预设工具真实 WSL2/runsc 执行 + `capability-results` 回报）、
最终输出节点 schema 定位校验、agent 节点 data/文件/状态事件、`/runs` Agent 面板）。证据：
`reports/f04a/T043-agent-node.md`。

| 项 | 内容 |
|---|---|
| 计划 | 3 节点 T042 链不变；4 节点链 agent 必须恰一个且位于 python/end 之间；agent 配置 `model_profile_version`/`instructions`/`max_turns` 1–50/`tool_versions`（manifest 子集、本构建只允许固定预设、最多 1 个）/`subapp_versions` 空/object `output_schema`；稳定拒绝码 |
| Agent 循环 | 绑定复用 T042 resolver；用户消息只含 inputs JCS；每轮 model operation（`agent:<node>:model:<turn>`）+ 每次 tool operation（`agent:<node>:tool:<call_id>`），确定性 UUIDv5 operation id；工具调用真实沙箱（独立 runsc unit）、文件/日志分别登记、`capability-results` 先回报再给模型只含校验 data 的 tool 消息；不把 stdout 发给模型 |
| 最终输出 | `structured` 优先，否则 `{text}`；节点 schema 以 `#` 指针再校验；`node_output_invalid` 含节点 id + pointer，无成功 proposal/final artifact；成功登记唯一 agent data 行并上报 file/log |
| 平台 outcome | `agent_turn_limit`、`agent_awaiting_approval`、`agent_unknown`、`agent_capability_failed`、`agent_tool_call_invalid`、`agent_config_invalid`、`agent_subapp_unsupported`、`agent_deadline_exceeded`；能力/工具失败沿用 T041 码；`run_cancelled` → cancelled/cancel_requested |
| 执行缝 | `presetexec.Client` 增 `InvokeCapability`/`ReportCapabilityResult`（worker mTLS，不绕过服务端授权/账本）；`Deps.Facts` 只读 model_profile/tool_versions（callable 与 T041 同规则） |
| Legacy | 恰一个预设 python 且 `file` 绑定 `input#...` → 原 T033 路径（golden `start/python/agent/end` 不变）；`node:start#/artifact` → 线性链；带 agent 的非法图 typed refusal，不回退 |
| Dev stub | 声明 tools 且无 tool 消息时，dev stub 确定性返回第一个声明工具的一次调用（id `stub-call-1`、arguments `{}`），仍 `stub=true`；真实 provider 仍 `provider_not_configured` |
| 页面 | `/runs` 折叠 Agent 面板：节点状态、模型轮次、工具调用 id/name、每次工具操作状态/尝试数、data/文件/日志计数与最终输出查看；`describeAgentNode` 由 Node 执行；不显示 params/receipt |
| 真实栈 | 真实 PG（0025/0026）、编译二进制、真实 mTLS、对象字节、WSL2+runsc `release-20241118.0`；`tests/f06/test_t043_*` 9 用例（6+3）通过；T042 legacy/节点结果回归仍绿 |
| 门禁 | 见 `reports/f04a/T043-agent-node.md` §6/§9 |
| 边界/not_run | 真实厂商 adapter、`awaiting_approval` 真实写入（T048）、真实栈 executor guard（未知/重复工具调用由 T041 服务端先拒）、跨 attempt 重试真实 e2e、浏览器 DOM、全量 `pytest tests`/`tests/f00b`；未 push/未关 issue |

## F06 流式代次、半流失败与残缺工具调用（T044，2026-11-04）

T044（GitHub #45）已交付，状态 **`passed`**（范围 = 受控临时模型流：provider 流式 seam、
`streamModelCall` 契约 SSE、operation/generation 账本语义、半流失败新代次且不拼接、残缺
tool-call 丢弃不执行、owner-only 临时预览 + `/debug`/`/runs` 面板、Python worker 流式适配；
持久 Run SSE 与 run_events 不变；真实厂商 adapter 仍未编译）。证据：
`reports/f06/T044-streaming.md`。

| 项 | 内容 |
|---|---|
| Provider seam | `StreamProvider.Stream(ctx, CallRequest, emit func(Chunk) error)`；dev stub 从 canonical params 确定性切块（`stub_stream_chunks` 默认 3）、`stub_stream_fail_after`/`stub_stream_fail_attempts` 产生 typed transient、`stub_stream_incomplete_tool_call` 产生残缺片段；真实 candidate `adapter_unavailable`，不伪造 |
| 代次/账本 | 每次物理流 = 一行 `operation_attempts`，其行 id 即 `generation_id`；帧恒带 operation/generation；transient + 还有尝试 → 新 generation 从头流，旧文本/工具片段不拼接；final 仅取成功代次完整内容 + 完整缓冲 tool-call，按 T041 校验后只封一份 receipt、只结算一次 attempt |
| 残缺工具 | 按 index 缓冲 fragments；id/name 缺失或 arguments 非完整 JSON object 的调用被丢弃、永不进入 final/执行；完整调用仍走 T041 id/name/schema 校验 |
| 契约协议 | `POST /internal/v1/runs/{id}/model-stream`（`streamModelCall`，worker mTLS）SSE：`delta`/`tool_delta`/`final`/`error`；授权错误在首帧前仍为 JSON；首帧后错误以 error 帧收尾；delta 绝不写入 `platform.run_events` |
| owner 预览 | `GET /api/v1/runs/{id}/model-preview`（session/API key + `application:read` + getRun 404 规则）；进程内 ring buffer（256 帧/60s TTL/单进程）、新 generation 清空旧缓冲、final/非重试 error 关闭、终态无缓冲 404；不持久 |
| 页面 | 新共享 `assets/stream-preview.js`（`previewPath`/`parsePreviewEvent`/`applyPreviewFrame`/`describePreview`/`beginModelPreview`）；`/debug`、`/runs` 预览面板显示 generation、流式文本、工具片段“待定、未执行”，新代次整块丢弃旧草稿，final/error 关闭并指向持久 Run；纯 helper Node 执行 |
| Worker | `PlatformChatModel.stream_generate` + `StreamingCapabilityTransport.stream` + `stream_agent`：每 generate 一条物理流、无隐藏重试；新 generation 重置草稿；只有 final 经 T041 同规则校验后的 tool calls 进入 agent loop 执行 |
| 真实栈 | 真实 PG（0001–0026）、编译二进制、真实 worker mTLS、对象字节、公网 owner SSE、真实 TLS socket RST 断开；`tests/f06/test_t044_*` 21 用例（10 流式 + 4 页面 + 7 Python worker）通过；断连时用不可取消清理上下文落 `stream_cancelled`，绝无成功 receipt |
| 门禁 | `go vet` exit 0；`go test ./...` 24 ok/0 FAIL；`gofmt -l` 空；`pytest tests/f04a` 36；`pytest tests/tooling` 21；`sync_spec --check` no drift；`pytest tests/f06` 83 passed + 1 个与本票无关的 T043 sandbox/lease 计时 flake（该文件隔离重跑 6 passed；T044 三文件 22 passed） |
| 边界/not_run | 真实厂商流式 adapter、浏览器 DOM 驱动、多进程预览 fan-out、全量 `pytest tests`/`tests/f00b`、T045/T046 预算与切换；未 push/未关 issue、未改 docs/spec/contracts/迁移 0001–0026、未新增迁移 |
T045（GitHub #46）已交付，状态 **`passed`**（范围 = 供应商可用性故障后的兼容备用：
备用清单进入冻结依赖闭包、kind/protocol/context/tools/schema/budget/manifest/已通过
门禁的 Go 判定、主重试仍 Go-only 有界、失败后第一个兼容备用只花一次物理尝试、
权限/语法/schema/工具参数/策略错误绝不重试或切换、operation/receipt/owner 账本/页面
展示切换原因与全部尝试费用；流式路径同一门禁且新代次作答；真实厂商 adapter 仍未编译）。
证据：`reports/f06/T045-fallback.md`；实现提交 `592fb69`、`d552282`，测试提交
`3e874c4`，f02a 门禁修复 `3e45eb1`。

| 项 | 内容 |
|---|---|
| 备用闭包 | 唯一来源是主 profile 行的 `ModelConfig.fallback_profile_version_ids`；`checkDependencies` 加载主行后把每个备用作为同一 `dependencyModel` 加入闭包（pointer 保留主声明，message 标出备用 id/来源），同一 `modelReadinessProblem` 要求存在/passed/未 revoke；`execution_manifest.model_profile_versions` 冻结主+备用，运行后改配置不能引入未冻结 profile |
| 兼容判定 | 纯 Go：同 `kind`/`protocol` 且为 `generation`/`chat_compatible`；备用 `context_window` ≥ 输入估计（canonical messages 每 4 字节 1 token）+ 请求 `max_output_tokens`；备用 `max_output_tokens` ≥ 请求值；显式 `tools:false` 不接带工具调用、显式 `structured_output:false` 不接带 `response_schema`（契约无此字段，缺省允许，只有显式 false 拒绝）；必须 passed/未 revoke/provider 可派发/在 manifest/保守预留放得进 `runs.budget_limit`（T046 接管完整账本）；仅“已配置”绝不启用 |
| 派发 | 主 transient 仍最多 3 次物理尝试（T041 不变）；第 3 次失败且属 `availability`/`rate_limited`/`adapter_unavailable` 时按配置顺序取第一个全门禁通过的备用，记 1 次物理尝试（attempt_no=4）；无兼容备用则返回主 typed failure。权限/授权/语法/schema/工具参数/策略错误 1 次尝试即终止，不重试不切换 |
| 账本/回执 | 迁移 0027 增 `platform.operations.fallback_profile_version_id`（FK）+ `fallback_reason`（closed：availability/rate_limited/adapter_unavailable；二元组同现同空）；`capability_version` 仍是 worker 请求的主版本；receipt 增 `fallback{primary_profile_version_id,profile_version_id,reason}`；owner 账本投影增 fallback 字段与 `tokens_in/tokens_out/charged_amount`（所有物理尝试求和），仍不含 params/回执正文 |
| 页面 | `/runs` 账本：物理尝试列显示合计费用/令牌，来源列显示“已切换到 <profile>，原因：<closed reason>”；`describeCapabilityOperation` 暴露 `fallback`/`fallbackText`/`costText`/`attemptsDetailText`，`capabilityFallbackReasonText` 映射稳定码；纯 helper 由 Node 执行，DOM 驱动 not_run |
| 流式 | `dispatchStreamFallback`：主 3 代次失败后兼容备用在**新 generation**作答；主代次文本/工具片段绝不与备用拼接，final/receipt 只来自备用成功代次；权限/schema 流失败仍终止 |
| 真实栈 | 真实 PG（0001–0027）、编译 `cmd/api`、真实 worker mTLS、对象字节、公网 owner HTTP/SSE；`test_t045_fallback.py` 9 用例（兼容切换+4 次尝试+费用、rate_limited、协议不兼容、权限单次、未冻结 manifest、未 passed 编译拒绝、预算耗尽、工具不兼容、流式新代次）+ `test_t045_pages.py` 3 用例通过；`test_t041_capabilities.py` 账本键集随投影扩展更新 |
| 门禁 | `go vet ./...` exit 0；`go test ./...` 24 ok/0 FAIL；`gofmt -l internal cmd tools` 空；`pytest tests/f06 -q` 96 passed（479.53s）；`pytest tests/f02a -q` 104 passed（726.21s）；`pytest tests/tooling -q` 21 passed；`python tools/sync_spec.py --check` no drift |
| 边界/not_run | 真实厂商 adapter（真实主/备切换 not_run）、浏览器 DOM 驱动、多进程预览 fan-out、T046 预算结算/释放与完整账本、全量 `pytest tests`/f00b/f03/f05 未跑；未 push/未关 issue；未改 docs/spec/contracts；迁移只新增 0027（0001–0026 未改）；f02a 三个“迁移链尾”断言原本在 base 已红（T041/T042 追加 0025/0026），本票改为连续性断言（仅测试） |

## F06 共享十进制预算预留与实际用量结算（T046，2026-11-04）

T046（GitHub #47）已交付，状态 **`passed`**（范围 = 一次 Run 树的共享十进制预算：
契约表 `platform.usage_ledger` 为唯一权威，物理调用前按预算根行锁原子预留，成功按价格版本
结算实际 usage 并释放未用预留，失败/取消释放，缺 usage 保留保守预留待维护对账；重试/备用/
流式共用父预算，回放不二次预留；预算拒绝是 typed 平台失败且保留已确认 artifact；owner
账本/`/runs` 页面显示预算投影）。证据：`reports/f06/T046-budget.md`。

| 项 | 内容 |
|---|---|
| 账本 | 迁移 0028 建 `platform.usage_ledger`（契约列 + `entry_key` 幂等键 + 非负 `numeric(20,8)`）；加法列 `operation_attempts.settlement_state`（none/reserved/settled/released/usage_missing）。`outstanding=sum(reserve)-sum(release)`、`charged=sum(charge)`；`budget_root_run_id` 递归解析父 run，F04A run 自为根 |
| 原子预留 | `internal/budget.ReserveTx` 在预算根 run 行 `FOR UPDATE`，同一事务检查 `limit-(charged+outstanding)`，放不下返回 typed `ExhaustedError`（HTTP 429 `budget_exhausted` + remaining/required/currency），不写 attempt、不调 provider；并发调用只能有一个拿到最后一笔预留 |
| 十进制 | `math/big.Rat` + 非负十进制字符串；预留向上取整、实际费用半进位，`numeric(20,8)` 溢出拒绝而非舍入；价格版本 `price-v1:<profile>:in=<输入价/百万>:out=<输出价/百万>:<currency>`；无 price 配置为零价 stub，异币种价格 422 |
| 结算/释放 | 每物理 attempt 一对 `entry_key`：失败/取消 release 整笔；成功 release 整笔 + charge 实际费用（price version）；provider 无 usage 时保留预留、attempt `usage_missing`，maintenance-only `POST /internal/v1/runs/{id}/capability-settlements` 对账（worker 不能自报费用，重复对账幂等） |
| 集成 | `internal/capabilities` 在 `beginModelAttempt` 内原子写 attempt + reserve，`finishModelSuccess` 同事务写 receipt + settle；重试/fallback/流式 generation 全部复用同一父预算根；回放走既有 operation idempotency，不二次预留/收费；预算拒绝保留此前 confirmed artifacts |
| 页面/接口 | owner `GET /api/v1/runs/{id}/capabilities` 响应增 `budget{budget_limit,reserved_amount,charged_amount,remaining_amount,currency,overrun}`；`/runs` 增 `#budget-status`，`describeRunBudget` 原样显示十进制串并在 overrun 时渲染“已超支：运行不能被呈现为成功”；纯 helper Node 执行 |
| 真实栈 | 真实 PG（0001–0028）、编译 `cmd/api`、真实 worker/maintenance mTLS、真实 run 行锁并发门（psql 持锁 + `pg_stat_activity` 等待计数，不以 sleep 决定胜负）；`test_t046_budget.py` 7 用例 + `test_t046_pages.py` 3 用例通过 |
| 门禁 | `go vet ./...` exit 0；`go test ./...` 25 ok/0 FAIL/9 no-test；`gofmt -l internal cmd tools` 空；`pytest tests/f06 -q` 106 passed（523.91s）；`pytest tests/f02a -q` 104 passed（689.15s）；`pytest tests/tooling -q` 21 passed；`python tools/sync_spec.py --check` no drift |
| 边界/not_run | 真实厂商 adapter（真实 usage/流式 not_run）、浏览器 DOM 驱动、全量 `pytest tests`/f00b/f03/f05、跨 run 的父/子预算真实 e2e（只有 F04A 自根 run 被真实验证，递归解析代码有单元级/迁移级证据）；未 push/未关 issue；未改 docs/spec/contracts；迁移只新增 0028（0001–0027 未改） |

## F06 授权上下文与受保护事实（T047，2026-11-04）

T047（GitHub #48）已交付，状态 **`passed`**（范围 = agent 每轮模型输入只含本次 Run 的已授权
start/python/tool 来源；预算 `context_window - max_output_tokens` 的确定性 byte/4 估算；超限按
低相关历史 → 旧工具细节 → 冗长上游文本截断；否定/来源引用/审批参数/精确金额统计逐字保留并
记 trace；摘要仅展示、绝不进入模型输入或事实派生；F09 记忆存储不存在 → `not_run`，不造假；
trace 作为 agent 节点 `context-trace` 文件行持久化并显示在 `/runs`）。证据：
`reports/f06/T047-context.md`、ADR 0007 D14。

| 项 | 内容 |
|---|---|
| builder | 新增 `internal/context`（package `agentcontext`）：`Build(ctx, Request) (Output, error)`；`Scope{NodeIDs,ArtifactIDs}` 由 sealed plan 派生，foreign node/artifact → typed `context_source_unauthorized`；`ResolvedInput` 必须带 literal/start/python/tool 来源；`Output.Messages` 为 role/content，`Output.Trace` 只含 id/kind/label/token 数/截断 reason/protected 键路径，绝不含正文或金额原文 |
| 预算/截断 | `budget = context_window - max_output_tokens`；估算 `ceil(UTF-8 bytes/4)` + 每消息 4 tokens，无 provider tokenizer/时钟/随机，重放确定；顺序：仅展示摘要与低相关历史 → 旧工具结果细节（保留 protected 分支）→ 冗长上游文本（1024→256→64→0）→ 最后手段；system+protected 最小值放不下 → `context_budget_exhausted`，绝不静默裁剪 protected |
| protected 事实 | 固定键表：`exclude/must_not/negative*/negations`、`sources/citations/document_id(s)/references`、`approval/proposal/target(s)/params`、`amount(s)/total/currency/quantity/price/count/stats` 等；匹配键整枝逐字保留；Go 单测断言 bounded input 仍含否定/来源/审批/金额，真实栈封存 model params 仍含 `"total":"250.00"`、`"order_count":3` |
| 摘要/记忆 | `HistoryMessage.Summary=true` 不进入模型输入、不扫描 protected、不派生金额/授权（`DisplayOnlySummaryCount` 计数）；`MemorySource` 生产实现 `NoMemorySource{}` 返回空，trace `memory_state=not_run`，模型无 memory 消息；active+confirmed 且同 owner+application 的记忆语义仅 Go 单测 seam，真实 F09 存储 not_run |
| Agent 集成 | `internal/presetexec/agent.go` 每轮模型调用前重建消息（system+authorized inputs+tool history），`ModelFact` 增 `ContextWindow/MaxOutputTokens`，supervisor 只读 `model_profile_versions.config`；超限/builder 错误以 `contextFailureCode` 作为 run error_code；未复制 T046 金额/预留/结算逻辑（`internal/budget`、`usage_ledger`、金额列零改动） |
| 持久化/页面 | 成功时新增 `kind=file`、`label=context-trace`、media `application/json` 的 JCS 文档 `{version:1,node_id,turns:[Trace]}`；`/runs` Agent 面板新增 `#agent-context-status`/`#agent-context-trace`，纯函数 `contextTraceRowOf`/`describeContextTrace` 渲染来源/截断原因/受保护键名/`memory_state=not_run`，通过既有 owner-only artifact content 读取，不新增接口 |
| 真实栈 | 真实 PG（0001–0028）、编译 `cmd/api`/`cmd/supervisor`、真实 mTLS、对象字节、WSL2+runsc python/tool 节点；模型 profile 在 run 入队后改小窗口（3072/1024 或 200/100）；`test_t047_context.py` 2 用例（截断+protected 经封存 model params 验证；protected 最小值超预算稳定失败、0 operation）+ `test_t047_pages.py` 3 用例通过 |
| 回归 | T043 6+3 用例通过（节点结果 8→9、analysis file 集合加 context-trace，其它断言未削弱）；T044/T046 共 32 用例通过；未跑 f05/f03 |
| 门禁 | 见 `reports/f06/T047-context.md` §9（go vet/test、gofmt、pytest f06/f04a/tooling、sync_spec --check 顺序执行） |
| 边界/not_run | 真实厂商 adapter/真实 tokenizer、F09 个人记忆存储与真实记忆消费、摘要生成链路、真实栈 foreign source 注入（只有 Go seam）、浏览器 DOM、全量 `pytest tests`/f00b/f03/f05；失败路径不写 trace 文件行（报告 §8 偏差 2）；未 push/未关 issue、未改 docs/spec/contracts、未新增迁移 |

## F06 固定 HTTP 连接器与受控出站（T048，2026-11-04）

T048（GitHub #49）已交付，状态 **`passed`**（范围 = 管理员登记版本化连接器策略并封存不可变
JCS artifact；工具声明的 connector 版本进入编译期 manifest 闭包；`invokeCapability(kind=connector)`
校验 manifest 成员/策略 hash/已批准未撤销/method/path/请求 schema/风险后，由 Go 执行一次真实
HTTP 出站并施加 SSRF/DNS/重定向/私网/元数据/凭据保护；写风险停在 `awaiting_approval`/
`approval_required`；撤销使下一次调用失败；能力种类歧义拒绝；operation/attempt/回执/owner 账本
完整；真实厂商连接器仍是部署定义，见报告 §8）。证据：`reports/f06/T048-connectors.md`、
ADR 0007 D15。

| 项 | 内容 |
|---|---|
| 迁移/注册表 | 新增 `0029_connector_policies.sql`：versioned `platform.connector_policies`（origin 恰为 scheme://host[:port]、method 集合、path_prefix、request/response schema、secret_ref/header、risk read|write、allow_private、policy_artifact_id、policy_sha256 UNIQUE、approved_at/revoked_at），撤销保留行不删除；同 0007–0028 权限边界。契约基线无此表：docs/spec/contracts 未改，偏差写入迁移注释/报告 §8 |
| 管理员 API | `POST/GET /api/v1/admin/connectors`、`POST /api/v1/admin/connectors/{id}/revoke`（admin+CSRF）；校验 origin/method/prefix/schema/secret 引用/header/risk；策略体 RFC 8785 JCS 封存为 `purpose=connector_policy` artifact，`policy_sha256` 为版本身份；重复体 409 `connector_policy_exists`+details.policy_id；响应不含凭据值 |
| 编译期闭包 | `platform.tool_versions.connector_versions` 展开到工具 document pointer（source=工具版本）；不存在/撤销/未批准分别 `dependency_not_found`/`dependency_revoked`/`dependency_not_ready`；`compiler.Build` 规范化并冻结 `manifest.connector_policies[{version_id,policy_sha256}]`；空集仍为 `[]`，不改既有 manifest 字节。工具提交接受已批准策略 id 并纳入内容身份 |
| invokeCapability | manifest 成员 + sealed digest 匹配 + 已批准未撤销 + params 仅 path/query/body + method/body 规则 + 请求 schema + query 声明；同 UUID 跨 model/tool/connector 集合 → 409 `capability_kind_ambiguous`；read 风险一次 operation/一次 attempt/一次真实 HTTP/回执；无隐藏重试 |
| 写风险/审批 | `risk_class=write` → operation `awaiting_approval`、返回 `state=awaiting_approval` + `data.error_code=approval_required`，0 attempt、0 出站；同键重放同一停住结果；T050 写 approval 行并把同一 operation 恢复为 authorized 后调用 `dispatchConnector` |
| 受控出站 | URL 仅 origin+prefix 内 path（无绝对 URL/点段/fragment/控制字符），query 需 request schema 显式声明；解析全部地址逐个分类（metadata/link-local/multicast/unspecified 即使 allow_private 也拒绝；loopback/RFC1918 仅 allow_private），已验证地址固定进 DialContext 不二次解析；重定向一律拒绝；Proxy=nil、10s 超时、请求/响应 1 MiB 上限、无 keep-alive；凭据只在 Go 内解析并写入 secret_header，不落 params/回执/日志/错误 |
| 账本/回执 | operation（connector、risk、params artifact、prepared→authorized→dispatching→succeeded/failed）、每物理调用一条 attempt、回执 artifact（status/response_sha256/response_bytes/headers 白名单/JSON body，无请求参数与凭据）；owner `GET /runs/{id}/capabilities` 每项新增 `risk_class`，连接器项新增 `connector_target`（METHOD origin+prefix） |
| 页面 | `/runs` 账本新增「目标」列与风险标签（读/写/无风险），写风险显示「待审批」；`describeCapabilityOperation` 暴露 `connectorTarget/targetText/riskClass/riskText/isConnector`；不显示 params/query/回执正文/凭据；纯函数 Node 执行 |
| 真实栈 | 真实 PG（0001–0029）、编译 `cmd/api`、真实 mTLS、对象字节、debug run manifest 编译、真实 loopback HTTP server；`test_t048_connectors.py` 12 用例（成功/凭据/SSRF/私网/DNS/重定向/撤销/歧义/写风险/method-path-schema-query/未入 manifest/管理员校验+ACL+重复+404）+ `test_t048_pages.py` 3 用例通过 |
| 回归 | T041 账本项集合随新增 `risk_class` 更新（唯一既有断言变化）；两次全量 f06 中 T042 legacy 与 T043 revoked-model 各出现一次 WSL2/runsc `sandbox_failed` 抖动，单独重跑均通过（连接器代码路径未触及）；`tests/f02a` 104 passed、`tests/tooling` 21 passed |
| 门禁 | 见 `reports/f06/T048-connectors.md` §6/§9（go vet/test、gofmt、pytest f02a/tooling、f06、sync_spec --check 顺序执行） |
| 边界/not_run | 真实厂商连接器语义、T050 审批写行与恢复、浏览器 DOM、全量 `pytest tests`/f00b/f03/f05；未 push/未关 issue #49、未改 docs/spec/contracts、迁移仅新增 0029（0001–0028 未改） |

## F04A 销售模板：另一使用者运行并下载已验证报告（T049，2026-11-04）

T049（GitHub #50）已交付，状态 **`passed`**（范围 = 服务端受控销售模板注册表与非契约实例化桥；
另一个真实 creator 把模板复制成自己的私有应用，用真实 CSV/XLSX 走既有 debug → supervisor →
WSL2/runsc 路径得到 250.00/东区 200.00/西区 50.00/3 单，报告只引用已验证统计；summary/CSV/正文
分离，第三用户隔离；模板实例化**不是** F02B 发布）。证据：`reports/f04a/T049-sales-template.md`、
ADR 0007 D16。

| 项 | 内容 |
|---|---|
| 迁移/注册表 | 新增 `0030_application_templates.sql`（0028 已被 T046 占用，0030 为下一个空位）：`platform.application_templates(id text PK, name, application_id uuid UNIQUE REFERENCES applications, definition jsonb, capability_card jsonb, created_at, updated_at)`；源应用 + revision-1 草稿是服务端种子，不是 application_version/publication_review；契约基线无此表，偏差写入迁移头注释/报告 §7 |
| Seed CLI | `orchara-admin seed-sales-template --owner-login ADMIN --model-version-id UUID`：只接受真实 T039 流程 passed 的 stub profile（pending/failed/revoked/非 stub 拒绝，绝不写 validation_state）；以真实 `sales_stats.py` SHA-256 作固定工具版本 image_digest，状态 ready/risk read/approved_by+at，幂等；定义解析为实际 tool/model UUID（无 2222… 占位）；重复运行输出相同 `{template_id,application_id,tool_version_id,model_version_id}` |
| 定义 | 固定四节点 `start → stats(python,sales_stats) → explain(agent) → end`：python 输出 `{total,regions,order_count}`；agent 只绑定 `node:stats#`、输出 `{report,totals}`、max_turns 4、无工具；end 映射 `{summary,report,totals}`。summary.json/summary.csv 是 python 文件 artifact，报告是 explain data + end 输出，互不覆盖 |
| 实例化 API | 非契约 `POST /api/v1/templates/{template_id}/applications`（creator+CSRF）：一个事务复制定义/卡为新应用 + revision-1 草稿，返回 `{id,revision=1,definition_revision}`；未知/畸形模板 404 `template_not_found`；非 creator 403 `creator_role_required`；未认证 401；不创建 version/review |
| 金额真值 | dev stub 仅在 response_schema 声明 `report` 且最后一条 user 消息含已验证 summary 时，逐字复制 total/regions/order_count 生成 report 与 totals（不重算不编造）；T043 `{text}` 路径不变。独立测试侧 `decimal.Decimal` 从字面量重算 250/200/50/3，校验 summary.json、summary.csv、report 全部两位小数与订单数，并证明扰动 total→251.00 / report→999.00 会失败 |
| 页面 | `/apps` 新增「从销售模板创建（F04A/T049）」动作；`app.js` 纯 helper `salesTemplateInstantiateRequest()` 由 Node 执行（路径编码/方法/空体/空 id 拒绝）；`/runs` report 下载路径与 stub 标签结构断言 |
| 真实栈 | 真实 PG（0001–0030）、真实 T039 register/validate + model_validate/artifact_finalize、真实 `cmd/api`/mTLS/对象字节、真实 supervisor + WSL2/runsc python 与 agent 节点；`test_t049_template.py` 3 用例（seed/实例化边界、CSV+XLSX 已验证报告、重复/负数/空输入拒绝）+ `test_t049_pages.py` 4 用例通过 |
| 门禁 | `go vet ./...` exit 0；`go test ./...` 28 ok/0 FAIL/10 no-test；`gofmt -l internal cmd tools` 空；`pytest tests/f06` 131 passed + 2 条既有 WSL2/runsc 抖动（T043，单独重跑 2 passed）；`pytest tests/f04a` 36 passed；`pytest tests/tooling` 21 passed；`sync_spec --check` no drift |
| 边界/not_run | F02B 发布/共享版本（本票明确不做）、真实厂商 adapter、浏览器 DOM、全量 `pytest tests`/f00b/f03/f05；未 push/未关 issue #50、未改 docs/spec/contracts、迁移仅新增 0030（0001–0029 未改） |

## F06 精确参数审批与安全恢复（T050，2026-11-04）

T050（GitHub #51）已交付，状态 **`passed`**（范围 = 服务端从 Run/operation/策略/manifest 真实
事实构建并封存审批提案；写风险连接器操作在 capability 代理同事务创建 pending approval 并停住；
指定人类审批人读取/决定；精确 params hash、过期、撤权、Run 终态/取消使批准不可复用；并发相反
决定只有一个赢家、同决定重放幂等；批准把 operation 翻到 `authorized`，旧尝试退出后经 T035 唯一
`maybeEnqueueResumeTx` 创建恰好一个 resume，恢复的尝试仍重校验权限/预算并在
`authorized → dispatching` CAS 后恰好派发一次；拒绝/过期为 `approval_rejected`/`approval_expired`
且零外部写入；发布审核无法决定 Run 审批）。证据：`reports/f06/T050-approvals.md`、ADR 0007 D17。

| 项 | 内容 |
|---|---|
| 迁移 | 新增 `0031_run_approvals.sql`：`platform.approvals`（契约列 + `UNIQUE(operation_id)`、`UNIQUE(run_id,node_key,params_sha256)`、`approvals_pending`/`approvals_run` 索引、`pending ⇔ decided_at IS NULL` CHECK）；偏差（node_key 由 logical key 节点前缀填充；不新增 operations.approval_id；无 created_at 列）写入迁移头注释/报告 §8。0001–0030 未改 |
| approvals 服务 | 新增 `internal/approvals`：`PrepareRecordTx`（capability 同事务建提案+行）、`Prepare`（契约 prepareApproval，忽略 worker proposal）、`Get/List`（审批人或 admin 读，stranger 404，pending-first cursor）、`Decide`（Run→approval→operation 锁序；hash/审批人/过期/权限/状态/策略撤销全查；同决定幂等、相反 409；approve→operation authorized，reject/expire→operation failed）；提案 JCS 封存 `purpose=approval_proposal`，owner=审批人，经既有 artifact-content ACL 读取 |
| capability 集成 | `capabilities.Service.Approvals`；写风险 connector 同事务建 approval + `awaiting_approval`，返回契约 `approval_id`；停住重放同 approval；`authorized` 写操作在恢复调用时 `proceed`，仍由 `authorized → dispatching` CAS 保证恰好一次；`loadAuthorizedRun` 新增 owner disabled 检查（批准不绕过当前权限），`resolveConnector` 重启策略撤销校验 |
| waiting/resume 桥 | `applyWaitingTransitionTx` 采纳先落库的真实 approval 决定；`maybeEnqueueResumeTx` 对真实 rejected/expired approval 以 `approval_rejected`/`approval_expired` 终止 Run，T035 synthetic opaque reference 保持既有 resume 契约；cancel/终态把 pending approvals 置 invalidated；唯一 resume 创建者仍是 T035 事务 |
| executor | `presetexec.Proposal.CheckpointID`；`runner.waitAgent` 把 `awaiting_approval` 转为 `waiting_approval` + `checkpoint_id=capability:<operation_id>` + `approval_required`，提交 waiting result 后 ConfirmExit；agent 模型/工具两处不再失败 Run；supervisor client 透传 checkpoint |
| HTTP/页面 | `POST /internal/v1/runs/{run_id}/approvals`（worker mTLS）、`GET /api/v1/approvals`、`GET /api/v1/approvals/{id}`、`POST /api/v1/approvals/{id}/decision`（Idempotency-Key 必需）；`/approvals` 页面展示真实目标/方法/路径/canonical params/去向/来源版本/过期/hash，批准/拒绝无默认选择，明确“批准不赋予额外权限”“拒绝/取消不撤销外部写入”；`/runs` 导航新增审批入口 |
| 真实栈 | 真实 PG（0001–0031）、编译 `cmd/api`、真实 mTLS、对象字节、真实 admin connector registry + loopback HTTP；`test_t050_approval.py` 15 用例（提案/ACL/list、内部 prepare 幂等、三种顺序各 1 resume + 恰好 1 次出站、reject、expire、换参数/错 hash、并发相反决定+幂等重放、撤权、预算 0、批准后撤策略、取消失效、真实 publication_reviews 行 404）+ `test_t050_pages.py` 3 用例通过 |
| 回归 | `tests/f03` T035 migration/waiting/resume/resume_refusals/pages 与 T036 cancel/unknown 全绿（T035 迁移尾部测试改为“连续链 + 本票行按 version”，不钉死 0026；synthetic rejected/expired resume 契约未改）；`go test ./...` 全 ok；`tests/tooling` 21 passed |
| 门禁 | `go vet ./...` exit 0；`go test ./...` exit 0；`gofmt -l internal cmd tools` 空；`pytest tests/f06` 151 passed；`pytest tests/f03` 目标集全绿；`pytest tests/tooling` 21 passed；`sync_spec --check` no drift |
| 边界/not_run | 自动过期 sweeper、`approval.required` 持久事件、真实浏览器 DOM、service-account 审批人真实栈路径、agent→connector 写策略消费、真实厂商连接器、全量 `pytest tests`/f00b/f01/f02a/f04a/f05；未 push/未关 issue #51、未改 docs/spec/contracts、迁移仅新增 0031（0001–0030 未改） |

## F13A 用正式 API 运行案例并让独立 checker 判定（T052，2026-11-04）

T052（GitHub #53）已交付，状态 **`partial`**（范围 = 契约 `evaluateDraft`
`POST /api/v1/applications/{id}/evaluations`（`{draft_revision, dataset_artifact_id}`、
`Idempotency-Key` 必需、202 六字段 `EvaluationJob`）+ 严格数据集校验（零断言/重复 case_id/
未知成员/JSON Pointer/十进制字符串均定位 422）+ 真实 PlatformTaskRunner（以评测 owner 身份
通过 job-scoped 桥创建真实 run，由真实 `cmd/supervisor exec --once` 执行）+ 五类独立
checker（`json_equals`/`decimal_equals`/`run_state`/`effect_count`/`citation_sources`，
`expected=0` 对真实空账本成立）+ 密封报告内的逐例结果/逐断言证据/采集事实/时间/冻结
provenance + 完成守卫扩展（唯一报告写入者：真实 runner 的 passed 案例必须有 ≥1 条已通过断言、
状态按案例重算、provenance 与快照行比对）+ 迁移 0035（`evaluation_case_runs` +
`application_id`/`cancel_requested_at`/`progress_total`/`progress_done`）+ ADR 0011）。

| 项 | 内容 |
|---|---|
| 判定 | **`partial`**：唯一未闭合项是 sales/tool 案例的 `succeeded` 终态——案例输入不能引用调用方 CSV（需绑定为 run 的 `task_input`，属 #57/T056）；真实 executor 对无工具节点链返回 `tool_unsupported_graph`，本票用真实失败证明它**不会**被当成成功。已证明的正面路径是真实终态：预定 `cancel` 交互使 run 真实 `cancelled`、真实账本 0 次效果、报告 `passed` |
| 交付 | `internal/evaluation/`（解析/checker/报告/采集循环）、`internal/catalog/evaluations.go`、`internal/api/{evaluation_submit,evaluation_runs,evaluations,internal,server}.go`、`internal/execution/evaluation_runs.go`、`internal/background/{evaluation,scope,handler}.go`、`internal/background/handlers/application_evaluate.go`、`cmd/maintenance/{run,scope}.go`、`internal/config/config.go`、`db/migrations/0035_evaluation_runner.sql`、`docs/adr/0011-evaluation-run-bridge.md`、`tests/support/t052.py`、`tests/f13a/`（3 文件 16 用例）、`reports/f13a/T052-evaluation-runner.md` |
| 门禁 | `pytest tests/f13a` **16 passed**；`pytest tests/f00b` **151 passed**（唯一适配：`test_t026_migration.py` 的列基线按 0035 的四个加法列**有意迁移**并注明归属）；`go build/vet/test ./...` 全绿；`gofmt -l .` 空；`sync_spec --check` no drift |
| 边界/not_run | 真实 judge 分数（无 adapter，quality 仅信息性）、`published_version` 执行（blocked/unsupported_target，F02B #69/F07）、F06 审批型交互、逐例 HTTP 读取（T056）、进度展示（T065）、带 citations 的 RAG 端到端样本（T064）；未 push、未关 issue #53、未改 `docs/spec/**`/`contracts/**`、迁移仅新增 0035 |

## F13A 阻止越权、取消和错制品报告通过验收（T062，2026-11-04）

T062（GitHub #63）已交付，状态 **`passed`**。目标是把 T052 的完成守卫从外部证明到
“伪造 / 借用 / 取消 / 未完成”四类报告都不能通过验收，并给管理员一份可核对的归属证据。
合并 integration 的提交为 `e92d8bac69cd39924d8da212c6e27df7e5c264e4`（合并前 tip `ddaeb46`；
本票 ADR 因 integration 已占用 0013 而重编号为 **0014**）。

| 项 | 内容 |
|---|---|
| 漏洞 G1（真实 runner） | T052 的取消案例在断言恰好为真时被判 `passed`（`run_state=cancelled` + 空账本 `effect_count=0` 两条断言都通过，checker-only 判决）。T062 在 `internal/evaluation/collect.go` 加入唯一完成态谓词 `CompletedRunState`（仅 succeeded/failed）：cancelled → `failed/run_cancelled`；`recovery_pending`/`needs_reconciliation` → `failed/run_unfinished`；断言结果仍作为证据保留。T052 的该用例按 #63 收紧 |
| 漏洞 G2（完成守卫） | 守卫此前只校验文档内部一致性 + manifest/dataset/provenance，**不读** `evaluation_case_runs`/`runs`：一份伪造 passed 报告（带自洽断言证据、provenance 全对）在 run 仍 `queued` 或已 `cancelled` 时会被接受。新增 `guardReportCaseRunsTx`：application 评测的报告必须声明真实 runner 评估器；每个带 `facts.run_id` 的案例必须命中本评测的 case run，run owner == 评测 owner、run snapshot == 评测 snapshot、run manifest == 绑定 manifest；`passed` 案例还要求 run 已完成（succeeded/failed）且报告记录的 run_state 与数据库一致。违背 → `report_binding_mismatch`、job failed、无 `evaluation_reports` 行 |
| 管理面 | 非契约、admin-only `GET /api/v1/admin/evaluations/{evaluation_id}/attribution`（ADR 0014）：owner principal+login、evaluation/job id、逐例 `run_state`（含 `cancelled`）与 `terminal`/`completed`、snapshot/manifest/dataset hash、compiler version、worker image digest、report 行；非 admin 在查询前 403，未知/畸形 id 404；响应不含 object key、report body、案例输入或 dataset 内容 |
| 对抗证据 | `tests/f13a/test_t062_guard.py`（未完成/取消 run 的伪造 passed 拒绝、4 类错误绑定逐一拒绝且无 report 行、借用他评测 artifact/文档拒绝）、`test_t062_runner.py`（取消案例 failed/run_cancelled、未完成案例 not_run/run_timeout、真实失败终态案例的合法 passed 被守卫接受）、`test_t062_authz.py`（A/B 读写隔离与提交拒绝、作废 attempt token 在重试产生新 attempt 后提交伪造 passed → 409 `stale_attempt` 且状态不变、管理员归属面 + 非 admin 403/401 + 无泄漏断言）；`internal/evaluation/collect_test.go` 单测覆盖三类判决 |
| 门禁 | 合并结果上 `pytest tests/f13a` **25 passed × 2 连跑**（503.19s / 382.58s）；`pytest tests/f10` **41 passed**（468.92s，合并后）；`pytest tests/f00b/test_t026_{binding,failures,pool,authz}.py` **20 passed**（258.54s，合并前守卫回归）；`pytest tests/tooling` **21 passed**；`go build/vet/test ./...` 全绿；`gofmt -l .` 空；`sync_spec --check` no drift |
| 边界/not_run | 逐例 **用户** HTTP 读取属 T056（本分支无该端点；T062 只交付 admin 归属面，非 admin 对其 403）；评测级取消转移不在 #63 票面（`cancel_requested_at` 列已在 0035 就位并出现在归属读数，case 级真实取消是本票路径）；sales/tool 案例的真实 `succeeded` 终态仍受 #57 的 `task_input` 绑定边界限制（本票用真实 `failed` 终态 + 期望失败的断言证明合法 `passed` 可被接受）；未 push、未关 issue #63、未改 `docs/spec/**`/`contracts/**`、无迁移 |

## F13A 查询异步评测进展并区分业务与设施失败（T065，2026-11-04）

T065（GitHub #66）已交付，状态 **`passed`**。范围 = 非契约进展读 + 由 runner 逐例持久化的
计数 + 业务/设施失败分类 + 真实双池拥塞验证 + 可刷新的评测页；合并 integration **三次**
（第一次 `df555c5` @ `ab8a800`；第二次 `cf82da2` @ `f2bc876`；第三次合并提交
`2f4bf1d1b670de20ceaeea1bfa9cc352059bc628` @ `98400a8`，即 T056/T060/T061 之后；
本票迁移与 ADR 因合并已占编号（T060 `0038`/`0016`、T056 ADR `0017`、T061 `0039`/`0018`）
而最终重编号为 **0040**/**0019**）。

| 项 | 内容 |
|---|---|
| 进展持久化 | `evaluation_case_runs.verdict`（闭集 passed/failed/blocked/not_run）+ `evaluation_jobs.progress_failed`；`progress_done` 统一为“有 verdict 或 run 到达 rest 状态”，创建 hook/facts hook/runner 上报共用同一 SQL 表达式，重试不会倒退。runner 在**每例判定完成时**通过 job-scoped `POST .../evaluation-progress` 上报（attempt token + 重证 owner ACL、幂等 upsert），无 run 的 blocked 案例也能被计数；完成守卫在写 report 的同一事务按密封报告写终态计数 |
| 进展读 | 非契约 `GET /api/v1/evaluations/{id}/progress`（ADR 0019）：owner-or-admin、`application:read` scope、非 owner 404 与不存在同码同文；仅 `state/report_status/report_id/total_cases/completed_cases/failed_cases/error_code/failure_class/created_at/finished_at`；`report_id` 在报告存在前为 null；**从不把 state 换算成百分比** |
| 失败分类 | `business` = 有报告且 status 为 failed 或 not_run（job/evaluation `succeeded`，是对应用的业务判定；`not_run` 是报告状态而非任务状态）；`infrastructure` = 无报告且 evaluation `failed`（job `failed`，report_id 为空）；cancelled 单独表示（`failure_class=null`）。这是“是否存在报告”的轴，与 `background_jobs.failure_class`（重试分类）分开；ADR 0019 记录二者可能不同 |
| 页面 | `GET /evaluations/{id}`（id 在路径，刷新即同一视图；`/assets/evaluations.js`）：queued/running 轮询、终态停止；明文区分“报告 failed（业务判定）”与“任务 failed（设施失败、无报告）”；`report_id` 空态；报告只读四个契约字段并按快照比较，重开显示相同内容。debug 页新增评测 id 入口。无百分比、无进度条 |
| 真实证据 | `tests/f13a/test_t065_progress.py` 7 用例：queued 零计数 + 授权/scope 边界；blocked 案例的 runner 持久化 + `not_run` 报告状态；真实 cancel 终态使报告 failed 而 job/evaluation succeeded（含封存字节/sha256/行不可变重读）；数据集封存对象真删 → `transient_storage_io` 重试耗尽 → job/evaluation failed、无报告、report_id null；内部 result 协议真实 cancelled；work_slots=1 双池拥塞下独立 finalize 与评测报告仍封存 ready；页面 HTTP + Node 执行纯逻辑。`tests/f13a/test_t065_migration.py` 以**名字**断言迁移与 replay no-op |
| 门禁 | 最终（第三次合并，T056/T061 之后）`pytest tests/f13a` **50 passed × 2 连跑**（531.74s / 425.83s；一次因外部重启共享 PG 中断后重跑，非测试失败）；时间盒定向 `pytest tests/f13a -q -k "t065"` **8 passed**（85.88s）；`pytest tests/tooling` **21 passed**；`pytest tests/f00b/test_t026_{binding,failures,pool,authz,migration}.py` **27 passed**；`go build/vet/test ./...` 全绿；`gofmt -l .` 空；`sync_spec --check` no drift。更早合并结果上同一套件也曾 33 passed × 2、50 passed × 2 |
| 边界/not_run | 浏览器 DOM（not_run，与其他页面票同一边界：页面纯逻辑由 Node 执行）；逐例读取/逐例页面属 T056（已随第二次 integration 合并并在本分支套件中执行；本票页面只显示进展与报告，不重复 T056 的逐例表）；评测级取消端点不存在故不伪造（用真实内部 result 协议 cancelled 路径证明分类）；真实 supervisor/沙箱不在本票路径（案例均为 cancel/无执行器/拒绝）；未 push、未关 issue #66、未改 `docs/spec/**`/`contracts/**` |

## F06 写后丢响应暂停核对与真实写次数不增加（T051，2026-11-04）

T051（GitHub #52）已交付，状态 **`passed`**（范围 = 写风险连接器派发前持久化
`authorized → dispatching` 意图，只有 CAS 赢家发送；超时/连接丢失/发送后 5xx/无法验证的 2xx
一律把 operation 置 `unknown`（从不 failed/succeeded），同事务写 T036
`run_unknown_operations`，attempt 退出后 Run 停在 `needs_reconciliation`；重复
invoke/resume 只回放已记录状态、零出站；owner 或该 operation 审批人用经校验且绑定 operation
的证据 artifact 调 `reconcileOperation`，`confirmed_success` 置 succeeded（原回执优先，否则
证据作为回执），`confirmed_not_sent`/`confirmed_failure` 置 failed；无供应商幂等键/查询回执
能力时披露限制且绝不自动补发；同一事务经唯一 T031 finalizer 让 Run 离开核对；同
(conclusion,evidence,Idempotency-Key) 幂等、不同结论 409；真实写次数全程为 1）。证据：
`reports/f06/T051-unknown-operations.md`、ADR 0007 D18。

| 项 | 内容 |
|---|---|
| 迁移 | 新增 `0032_operation_reconciliation.sql`：`platform.operations` 可空审计列 `reconciliation_conclusion`（closed CHECK）、`reconciliation_key`（≤200）、`reconciliation_limitation`（closed CHECK）；契约列语义不变，无新表/触发器；0001–0031 未改 |
| unknown 派发 | `capabilities.dispatchConnector` 在 `Connectors.Execute` 返回 timeout/transport/5xx-after-send/response-invalid 且策略为 write 时调 `markConnectorUnknown`：同一事务（`execution.RecordUnknownOperationTx` 锁 Run+校验 live attempt+写 unknown 行，再写 operation `unknown`）提交；返回契约 `CapabilityResult{state:"unknown"}`；物理 attempt 保持 `dispatching`；失败路径不改写为 failed/succeeded |
| CAS 单发送者 | `beginConnectorAttempt` 仍以 `authorized → dispatching` CAS（operation 行锁 + UNIQUE attempt）为唯一发送门；重复 invoke 见 `dispatching` 409 `operation_in_progress`、见 `unknown` 200 回放；同逻辑键换参数 409 `operation_conflict` |
| 限制披露 | 派发前持久化 `reconciliation_limitation=no_idempotency_or_query_support`（connector 策略当前无供应商幂等键/查询回执能力）；dispatch `data.limitation`、owner 账本与 `/runs` 页面三处披露；无任何自动重发 |
| 核对服务 | 新增 `internal/capabilities/reconciliation.go`：`ReconcileOperation`（Run→operation 锁序；owner 或 `platform.approvals.approver_id`；证据 artifact ready/未删/owner 或 run 链接 + JSON 绑定 operation_id 或 logical_key+params_sha256；`Idempotency-Key` 必需；同元组幂等回放、不同元组 409）与 `internal/execution/reconcile.go`：`ReconcileUnknownOperationTx`（解析唯一 unknown 行后重入 `finalizeRunTx`：取消意图优先、成功提案→succeeded、失败结论→合成 failed 提案、仍有未知/子 Run 保持停泊） |
| 结论语义 | `confirmed_success`：operation succeeded，原回执优先否则证据 artifact 作回执；`confirmed_not_sent`：failed + `operation_not_sent`，因策略无显式安全重试支持不做第二次派发、不建第二个 operation id；`confirmed_failure`：failed + 派发时安全码；三者都只结算原 operation |
| HTTP/页面 | 契约 `POST /api/v1/operations/{operation_id}/reconcile`（cookie+CSRF 或 API key `application:write`；返回 `Accepted{id,state}`；缺 `Idempotency-Key` 422）；owner 账本新增 `requires_reconciliation`/`reconciled_by`/`reconciliation_evidence_id`/`reconciliation_conclusion`/`reconciliation_limitation`/`provider_idempotency_key`（空值省略，旧投影不变）；`/runs` 显示「结果未知（待核对）」、限制披露与逐操作核对表单，核对后显示「真实写次数仍为 N（未增加）」 |
| 真实栈 | 真实 PG（0001–0032）、编译 `cmd/api`、真实 mTLS、对象字节、真实 T050 审批→resume、真实 loopback 端点（收到真实写后关闭连接不回响应）+ 真实 egress guard；`test_t051_unknown.py` 9 用例（丢回执 unknown/run 停泊/重复不补发/换参 409、5xx→unknown、confirmed_success+证据+Run succeeded+幂等重放+冲突 409、confirmed_not_sent 无第二次 operation/写、confirmed_failure、非 owner 403、证据缺失/绑定错误/已删除/他人证据 422、缺 Idempotency-Key/坏结论 422、无幂等/查询能力披露且不自动重发）+ `test_t051_pages.py` 3 用例通过；端点请求数=physical attempt 数=1 |
| 回归 | `tests/f03` T036 unknown/cancel 21 passed（T036 bridge 行为未改）；`go test ./...` 全 ok；`pytest tests/f06` 164 passed（含 T039–T051）；`tests/tooling` 21 passed |
| 门禁 | `go vet ./...` exit 0；`go test ./...` exit 0；`gofmt -l internal cmd tools` 空；`pytest tests/f06` 164 passed（582.73s）；`pytest tests/f03` 目标集 21 passed；`pytest tests/tooling` 21 passed；`sync_spec --check` no drift |
| 边界/not_run | 真实厂商连接器（无幂等键/查询回执 API，故不做同 operation 安全重试分支）、owner 以外的独立审批人核对（T050 审批人恒为 owner）、`needs_reconciliation → queued` requeue（T038 recover 仍是唯一 requeue 权威）、真实浏览器 DOM、自动重试/sweeper、全量 `pytest tests`/f00b/f01/f02a/f04a/f05；未 push/未关 issue #52、未改 docs/spec/contracts、迁移仅新增 0032（0001–0031 未改） |


## F09 个人长期记忆：候选确认后才保存（T054，2026-11-04）

T054（GitHub #55）已交付，状态 **`passed`**（范围 = `createMemory` / `listMemories`
真实读模型 + `proposeMemoryCandidate`（worker mTLS）+ `listMemoryCandidates` +
`decideMemoryCandidate`，候选→确认的完整垂直切片与 `/memories` 页面）。证据：
`reports/f09/T054-memory-candidates.md`、ADR 0009。

| 项 | 内容 |
|---|---|
| 迁移 | 新增 `0033_memory_candidates.sql`：契约表 `platform.memory_candidates`、`platform.memories`；列级偏离（logical_key + UNIQUE(run,key)、created_at/decided_at/decided_by/decision_key、memories.confirmed_by）逐条写在头部；`memory_embeddings` 延后到 T063（无 pgvector）；0001–0032 未改；重复 migrate no-op；worker/PUBLIC 零权限 |
| 存储 | `internal/memory`：候选/记忆正文均为 sealed artifact（`purpose=memory_candidate|memory_body`，sha256 校验，回滚删对象）；确认复用候选正文（不复制第二份个人字节），确认前重读校验；`UNIQUE(candidate_id)` 保证一条记忆 |
| 候选生命周期 | `ORCHARA_MEMORY_CANDIDATE_TTL`（默认 7 天，1m–365d，非法值拒绝启动）；过期由服务端按 `expires_at` 强制：列表惰性 `pending→expired`，决定在行锁内复检；拒绝/过期不产生记忆，之后确认 409 `memory_candidate_expired`/`memory_candidate_rejected` |
| 幂等 | 重复确认返回同一 memory（不同或相同 `Idempotency-Key` 都一样，不写第二行/第二个对象）；契约要求的 `Idempotency-Key` 用 `platform.idempotency_keys` 收据（JCS 决定摘要 + 已存 body），同 key 相反决定 409 `idempotency_conflict`；失败决定不收据 |
| 服务账号 | 五个记忆端点（含 T058 将加的写读）在读 body/存储前 403 `personal_scope_forbidden`；`owner_id`/`confirmed_by` 引用 `platform.users`，数据库层也无法把服务账号写成所有者/确认者 |
| 所有权 | 列表在 SQL 同一 WHERE 里过滤 owner；他人与管理员对单条候选/决定得到同一 404（不泄露存在性）；管理员列表为空而不是别人的行 |
| 缓存 epoch | 确认/直接写入在同一事务 bump `platform.policy_epochs` 的 `memory:<owner>:<application>`（不新建表；授权器只读 `acl`，故对鉴权惰性）；T058/T063/T067 的失效 seam |
| 页面 | `/memories` + `assets/memories.js`：候选（来源类别/应用范围/状态/过期时间/正文）+ 确认/拒绝（每次点击新 `Idempotency-Key`，无默认选择）+ 当前记忆（revision）；全部 `textContent`，无凭据/正文留存；Node 执行纯函数 |
| 真实栈 | 真实 PG 16.4、编译 `cmd/api`、真实 mTLS worker 证书 + run live attempt、真实 supervisor claim、真实对象字节（删字节→503 失败闭合，恢复→同一行可读）、真实 cookie+CSRF 决定；`tests/f09` 25 用例 |
| 门禁 | 见报告 §4（`pytest tests/f09` 25 passed；`go vet`/`go test`/`gofmt -l`/`sync_spec --check` 全绿；`tests/f01/test_t020_personal_boundary.py` 2 passed；`tests/tooling` 21 passed） |
| 边界/not_run | 浏览器 DOM；后台过期清扫进程（惰性迁移 + 决定复检已生效）；记忆编辑/删除/ETag（T058）、运行时召回（T063）、Mem0（T067）；记忆共享/授权；真实 worker 自动抽取生产者（本票用真实 supervisor claim + 真实 worker mTLS 调用真实内部端点）；全量 `pytest tests`；未 push/未关 issue、未改 docs/spec/contracts |

## F09 个人长期记忆：修订/删除立即停止旧召回（T058，2026-11-04）

T058（GitHub #59）已交付，状态 **`passed`**（范围 = `getMemory` 强 ETag、
`updateMemory` 必填 `If-Match`（428/412、新 revision/新正文 artifact/同事务 epoch）、
`deleteMemory` 202 `DeletionAccepted` + tombstone + `deletion_jobs`，以及 `/memories`
页面的冲突恢复与删除状态）。证据：`reports/f09/T058-memory-revision-and-delete.md`、
ADR 0009 §11。

| 项 | 内容 |
|---|---|
| 迁移 | 新增 `0036_memory_revision_and_deletion.sql`（最终编号；基线分支为 0034，合并时 T055 先占 0034、T052 再占 0035）：契约 `platform.deletion_jobs` 最小切片（pending + remaining_scopes + `UNIQUE(resource_type,resource_id,deletion_revision)`）；`memories` 不加列（ETag = 既有 `revision`，tombstone = 既有 `deleted_at`）；F07 债务写在头部；重复 migrate no-op；worker/PUBLIC 零权限 |
| 强 ETag | 复用草稿 PUT 的 `catalog.FormatETag/ParseETag` 与 `precondition_required/precondition_failed` 词表；弱/乱码/列表/多值一律 412（带 `details.current_etag`/`current_revision`），不做宽松解析 |
| 更新 | 行锁 + CAS（`WHERE revision=$claimed`），新 sealed `memory_body` artifact，`revision+1`，同事务 bump `memory:<owner>:<app>` epoch；旧 ETag 重试 412；并发同 ETag 恰一胜者 |
| 删除 | 202 `DeletionAccepted{id(deletion_job),state,resource_id}`；同一事务 tombstone + revision+1 + job + epoch 后才提交；重复/并发删除同 job、不再 bump；GET/列表/召回形查询立即拒绝旧内容 |
| 页面 | 编辑先取强 ETag；412 显示“版本冲突”并用 `details.current_etag` 显式重试；删除显示 job 真实状态 + “旧内容已停止召回” + “物理副本清理由删除治理异步执行”；全部 `textContent`，Node 执行纯函数 |
| 边界 | 物理清理 / `delete_content` / job 生命周期 = F07；`memory_embeddings`（无 pgvector）= T063；检索缓存 = T067；owner-only 与服务账号 403 不变 |
| 门禁 | `pytest tests/f09` **47 passed ×5**（308.18s / 330.67s / 480.97s / 499.18s / 337.02s，最终树后两轮）；`go build/vet/test`、`gofmt -l`、`sync_spec --check` 全绿；`tests/f01/test_t020_personal_boundary.py` 2 passed；`tests/tooling` 21 passed；`tests/f10` 20 passed；合并提交 `7190b2b` + `895a67f` |

## F09 个人长期记忆：运行时按上下文预算召回（T063，2026-11-04）

T063（GitHub #64）已交付，状态 **`passed`**（范围 = 真实 PostgreSQL 召回源、
确定性记忆预算取舍、唯一生产接点、真实 run 证明与失败/授权/恢复边界、propose 身份
核实）。证据：`reports/f09/T063-runtime-recall.md`、ADR 0009 §12。

| 项 | 内容 |
|---|---|
| 召回源 | `internal/memory.RecallSource` 每次直接读 PG（不缓存）：`owner_id`+`application_id`+`deleted_at IS NULL`+当前 ready `body_artifact_id`，`LEFT JOIN memory_candidates` 取来源链；逐条 sealed 正文 sha256 校验；一条读不出即整体 fail closed（trace `unavailable`，不注入任何记忆）；候选永不可召回，记忆本身无过期列 |
| 预算 | 记忆子预算 = `(context_window - max_output_tokens)/4`；`confirmed_at DESC, id DESC` 新近度排序（无 pgvector，检索打分属 T067）；超预算按最低相关度先截断，`reason="optional_memory"` + memory id/revision/tokens_removed；`memory_tokens`/`memory_budget_tokens` 记录实际花费与子预算；逐条 source 带 id/revision/confirmed_at/candidate/source run；编码 O(n) 精确构造并有 pinning 单测 |
| 保护 | 用户否定/审批记忆按固定词表标记 protected，选择优先且永不截断；整体上下文压力按条丢非受保护记忆；只剩受保护仍放不下走既有 `context_budget_exhausted`（与 protected minimum 同规则）；trace 无正文 |
| 接点 | `presetexec.Deps.{Memory,RunIdentity}`，唯一注入点仍是 `internal/presetexec/agent.go` 的 `agentcontext.Build`；`cmd/supervisor exec` 读 `runs.owner_id + execution_snapshots.application_id` 作 scope，并可选打开 `ORCHARA_OBJECT_DIR` 读 sealed 正文；未配置时 trace `unavailable`，不伪造 |
| 数据/契约 | **无新迁移**（0033 `memories_scope` 索引已覆盖；`memory_embeddings` 仍不建）；**无新端点/页面改动**；trace 经既有 `GET /runs/{id}/node-results` + owner-only artifact content 读取 |
| propose 身份 | 核实：`cmd/supervisor exec` 同时持 supervisor+worker 证书，能力调用与 node-results 已用 worker 容量；真实测试：supervisor/maintenance propose 403 `forbidden`、worker 201 且 application 从 run 推导。结论：传输身份可用，**不需要**拆分进程；自动抽取/propose 行为仍属 F04A（not_run，非 blocked） |
| 门禁 | `pytest tests/f09` **51 passed ×2**（合并后最终树 511.80s / 643.37s；中间一轮 50 passed / 1 `sandbox_failed` 抖动，单独复跑通过）、`pytest tests/f10` 57 passed（679.35s）、`pytest tests/f06/test_t047_context.py` 2 passed（48.14s）、`pytest tests/tooling` 21 passed、`go build/vet/test`、`gofmt -l`、`sync_spec --check` 全绿 |
| 边界/not_run | 自动抽取/propose producer（F04A）；检索打分/嵌入/缓存与 `memory_embeddings`（T067）；物理清理（F07）；浏览器 DOM；真实厂商模型（沿用 dev stub）；全量 `pytest tests`（orchestrator）；合并提交 `0c8a995`（合并后 f09 51 passed ×2、f10 57 passed、T047 2 passed、Go 门禁与 sync_spec 全绿） |

## F09/T063 RV3 复审修复：保护感知的召回扫描（2026-11-05）

复审确认并修复 1 个 P2 缺陷（T063 的召回只按新近度读 200 行，第 201 行及更早的用户否定
在 builder 保护检测前被丢弃且 trace 无记录）。ADR 0009 §12.9、T063 报告 §9。

| 项 | 内容 |
|---|---|
| 修复 | `internal/context.IsProtectionSensitive` 导出唯一保护词表（builder 与召回共用）；`internal/memory/recall.go` 新近 `RecallRowLimit=200` 页不变，满页时按 `(confirmed_at,id)` keyset 以 `RecallScanBatch=200` 行/批、最多 `RecallScanRowLimit=2000` 行（10 批）扫描同 owner+application 旧行，protected 行加入返回集；旧正文读不出仍 fail closed |
| 接口/trace | `MemorySource.ConfirmedMemories` 返回 `MemoryRead{Rows,Scan}`；`Trace.MemoryScan{scanned,limit,protected_found,truncated}`；触顶时 `truncations[]` 新增 `{source:"memory_scan", reason:"memory_scan_truncated", scanned, scan_limit, tokens_removed:0}`；`NoMemorySource` 仍返回零值、`not_run` 不调用；预算截断仍 `optional_memory` |
| 测试（真实 PG + sealed 字节 + 真实 run） | `tests/f09/test_t063_recall.py` 新增 `test_protection_scan_finds_a_negation_beyond_the_recency_cap_and_the_budget_keeps_it`（201 行、越界否定被保留且 512 token 子预算下不截断、`memory_scan` 记录、跨应用/跨用户隔离）与 `test_protection_scan_stops_at_its_hard_bound_and_the_trace_says_so`（2201 行、扫描 2000 行触顶、`memory_scan_truncated` 带 scanned/limit、越界否定不出现）；Go `internal/context/build_test.go` 增补 builder 侧用例；既有 T063 四用例与 T047 保持通过 |
| 门禁 | `pytest tests/f09` 本树 **69 passed ×2**（独立 `TMP`/`TEMP` 背靠背 513.24s / 503.26s；默认临时目录的中间一轮 3 failed/66 passed 是并发 pytest 会话清理 `pytest-<N>` 临时目录 + WSL/runsc 抖动，失败在上传 500/二进制缺失/`sandbox_failed`，非断言失败）、`pytest tests/f06/test_t047_context.py` 2 passed（35.24s）、`pytest tests/tooling` 21 passed（6.01s）、`go build ./...`/`go vet ./...`/`go test ./...` 全绿、`gofmt -l .` 空、`sync_spec --check` no drift；合并 `integration/f13a-f15a-f09-f10`（tip 仍为 8ad3bcc，`Already up to date`，无合并提交）后 `pytest tests/f09` 69 passed（477.18s）与 Go 门禁复跑全绿；无迁移、无端点、未改契约/规格 |

## F09 Mem0 对照实验：保留 PostgreSQL（T066，2026-11-04）

T066（GitHub #67）已交付，状态 **`passed`**（实验范围；**不是采用票**）。决策
**`do_not_adopt`**：Mem0 在删除后仍在自己的 SQLite history/Qdrant 文件中保留正文副本，且在
共享确定性 embedder 下召回不优于 PG、延迟未达 20% 收益阈值（安静首轮 p95 慢 6.65×；合并树
复跑因主机并发负载 p95 比 0.95×）；正式采用还须 **F11**。Mem0 侧证据按票面
标 **`stub`**（`infer=False` + 本地 embedder，无模型 provider）。证据：
`reports/f09/T066-mem0-comparison.md`、`experiments/mem0/evidence/t066-both.json`。

| 项 | 内容 |
|---|---|
| 隔离 | 独立 `product/experiments/mem0/`：一次性 `orchara_t066_mem0_<hex>` PG 库（真实 `cmd/admin migrate`）、一次性 Mem0 工作目录、自有 venv 与 34 包 hash lock；`mem0ai` 不进产品运行时依赖，`internal/` 不 import 实验代码，未使用产品数据库 |
| fixture | 固定 seed 66 + canonical SHA-256：2 用户/3 应用/9 条已确认记忆/1 次修订/1 次删除/1 个 pending 候选/8 正例 + 4 反例（跨用户、跨应用、已删、候选）；两侧同一 64 维确定性本地 embedder |
| PG 侧（real） | `pgdriver`（编译实验二进制）调用真实 `internal/memory.Service.Create/Update/Delete`；真实 sealed 对象字节、revision/tombstone/`deletion_jobs`/epoch；query 级召回指标由实验实现（T063 不在本分支基线，`t066.memory_vectors` 为实验索引替身，已记录限制） |
| Mem0 侧（stub） | 真实 mem0ai 2.2.1 + SQLite history + Qdrant local；`add(infer=False)`/`update`/`delete`/`search` 真实执行；无模型 provider，`infer=True`/rerank/hybrid 未跑 |
| 测量 | 12 query × 21 次（≥20）：合并树复跑 PG hit@1 1.0、p50 0.541 ms / p95 9.082 ms（安静首轮 0.560/1.670）；Mem0 hit@1 1.0、p50 6.119 ms / p95 8.615 ms（首轮 5.709/11.107）；两轮 p95 比分别 6.65× / 0.95×，均未达 20% 收益；存储与依赖成本：PG 无新增，Mem0 新增 35 个发行包 / venv 162.9 MB + 20 KB SQLite + 29 KB Qdrant |
| 副本清理 | 删除一条已确认记忆后逐列/逐文件枚举正文：PG = 1 个 sealed 对象（数据库无正文列；T058 同步停召回，`deletion_jobs` pending，F07 清理；旧 revision 另 1 个对象）；Mem0 = 6 处（SQLite `history.new_memory`/`history.old_memory`、`history.db`、Qdrant `storage.sqlite`、DEBUG 日志；默认 WARNING 日志 0） |
| 决策 | `do_not_adopt`，理由两条均成立：治理不合格单独即可否决 + 无可测收益；`decision.gates.f11.passed=false`、`remaining_gates=["F11"]`；不会把初测当正式采用 |
| 记录校验 | `records.validate` 拒绝 `complete`、拒绝未运行/仅 stub/有残留副本/F11 未过的 adopt；无 venv 时 runner 以 exit 2 产出 `blocked` 记录（无数字），不跳过 |
| 门禁 | T066 16 passed ×2（合并树 43.13s / 36.94s，真实 PG + 纯规则 + blocked 记录 + committed evidence）；`pytest tests/f09` 63 passed（合并后，553.71s；合并后首跑 1 个 T054 fixture 并发 `ALTER ROLE` setup error，单测复跑 1 passed、全量复跑 63 passed，非本票失败）；`go build/vet/test`（33 ok）、`gofmt -l`、`sync_spec --check` 全绿；tooling 21 passed；合并提交 `9638ddf`（集成 tip `eb30e1e`，无冲突）；未 push/未关 issue、未改 `docs/spec/`/`contracts/`/`internal/` |
| 边界/not_run | 真实模型/provider、`infer=True` 抽取、rerank、部署型向量库、多 worker history、备份恢复、规模压测、BM25/spaCy、浏览器 UI、F11、F07 物理清理、全量 `pytest tests`/f01 |

## F15A 候选 S3 兼容探针（T053，2026-11-04）

T053（GitHub #54）**候选 S3 验收 `blocked`**：本机没有候选 S3 端点（无 Docker/Compose、
MinIO/SeaweedFS、AWS CLI、S3 端口；9000/9001/6379 关闭；`mc` 实测是 Windows NT Message
Compiler，不是 MinIO 客户端），因此无法产生“候选被接受/拒绝”的真实结论。可以真实交付并已
执行的是部署验收**探针**及其**判别证明**：探针必须把“无候选端点”报成 exit 2 `blocked`，并且
能逐条拒绝故意违规的本地 S3 夹具。证据：`reports/f15a/T053-s3-probe.md`、
`reports/f15a/runs.json`、`reports/f15a/environment-profile.json`、ADR 0008。

| 项 | 内容 |
|---|---|
| 探针 | `probes/f15a_objectstore/object_store_probe.py`：Python 3.12 标准库 only，path-style `/bucket/key`，SigV4 header + presigned query auth，`--out/--emit` JSON 证据；退出码 `0` 全通过 / `1` 真实不兼容 / `2` 配置或环境失败（含无候选）；凭据只从 `F15A_S3_*` 环境读，不进 argv/证据；`--short-ttl`/`--expiry-skew`/`--retries`/`--ca-file` |
| 断言 | 10 条：匿名读/写/列表拒绝；越界/兄弟/绝对键与 `..` 不得逃逸 + 改路径签名 URL 被拒；runtime 不能 PUT sealed；staging 覆盖保 sealed 哈希；短 TTL 内可用/过期拒绝；HEAD Content-Length + Range 206/416 + 未过期 URL 撤销观察（`requires_proxy_download`）；删除需凭据/404/幂等/兄弟键安全；版本化观察（无则 `not_run`） |
| 判别证明 | `fixtures/compliant_server.py`、`fixtures/violating_server.py`（真实 HTTP + 独立 SigV4 校验，`--violate <id>`/`all`/`--transient-failures`）；`tests/f15a/test_discrimination.py` 合规 9 passed + 1 not_run/exit 0、全违规 9 failed/exit 1、逐条只有对应断言 failed、503 真实重试、缺 provisioning 时 sealed 断言 blocked |
| 本地 FS 类比 | `probes/f15a_objectstore/localfs_probe/main.go` 直接消费真实 `internal/objectstore`：键类型/遍历、staging↔sealed、跨命名空间写拒绝、HMAC TTL/操作/上限、幂等删除、20 MiB 上限；`not_s3_evidence: true`，匿名网络/版本化为 `not_run` |
| 证据 | `reports/f15a/` 15 次真实运行：`candidate-absent`/`candidate-not-configured` exit 2 blocked；`fixture-compliant` exit 0；`fixture-violating-all` exit 1（9 failed）；`fixture-violation-<id>` ×9；`fixture-transient-retry`；`local-fs-analogue`；`runs.json` 记录命令/退出码/SHA-256，`environment-profile.json` 记录机器事实；所有 JSON 无凭据值 |
| 门禁 | `pytest tests/f15a` **36 passed**（76.91s）、`pytest tests/tooling` 21 passed、`go build`/`go vet` exit 0、`go test ./...` 29 ok/0 FAIL/11 no-test、`gofmt -l .` 空、`sync_spec --check` no drift |
| 边界/not_run | 真实候选 S3 运行、候选 IAM/桶策略、备份/恢复（F15B）、版本化（夹具未启用）、平台 S3 后端与代理下载实现（后续接入票）；未 push/未关 issue #54、未改 docs/spec/contracts、无迁移 |

## F10/T055 知识摄取与可追溯检索（2026-11-04）

| 项 | 内容 |
|---|---|
| 迁移 | 新增 `0034_knowledge.sql`（合并 integration 后让号；F09 先占 0033）：`platform.knowledge_bases/knowledge_documents/knowledge_chunks`；契约 `embedding vector` 偏差为 `double precision[]` + 384 维 CHECK（pgvector 不可安装，T064/T067 拥有 ANN/规模）；`knowledge_documents.active_revision`（T061 激活 seam）、`error_code`、`chunker_version`、`embedding_model_revision`；`knowledge_chunks.embedding_dimension`；无新 kind（`document_ingest` 已在 0009 CHECK 与 Go 白名单），0001–0033 未改 |
| 摄取 | `ingestDocument` 只写 document（`scanning`）并入队一个 `document_ingest` job（同事务）；真实 maintenance handler 经 job-scoped binding 下载源 artifact，解析 txt/md、确定性段落/重叠分块、`local-hash-v1` 384 维 embedding、每 chunk 一个封存 body artifact（既有 upload→complete→finalize 路径，purpose `knowledge_chunk`，上限 1000/文档 revision），暂存 chunk 行；handler 永不写 ready |
| 激活/失败 | `background.guardDocumentIngestCompletion` 唯一写 `state/active_revision/error_code`：锁行 + revision 核对 + chunk 集合完整/连续/citation 一致/384 维/body artifact ready 才 `ready`；空解析 `ingest_empty_content`、非法 UTF-8、源不可用/换版、暂存不完整、超上限、取消均 failed；仅 4 个 infrastructure 码可重试（≤3 次），未知码 business；failed 文档再摄取 = revision+1 + 新 job，ready/indexing 重复请求幂等 |
| 检索 | 非契约 `POST /knowledge-bases/{id}/retrieval-queries`（SQL 先过滤 owner/KB tombstone/doc tombstone/state=ready/active_revision=revision/chunk.document_revision=revision/body artifact ready/模型 revision，再有界候选精确余弦排序取 top_k；空结果 200 `items:[]`）；`GET /knowledge-citations/{chunk_id}` 每次重校验 ACL/当前版本，撤销→404 无正文；`GET /knowledge-documents/{id}/diagnostics` 为页面提供失败码/计数；`acl_epoch`=KB revision 供 T067 缓存键 |
| 真实栈 | `tests/f10/` 20 用例：迁移链/约束/零授权、KB CRUD/游标/owner+admin 404/CSRF/API key scope、txt+md 上传→ready→检索→citation 独立真值（源 hash/rune 切片/行号/body 字节）、非当前 revision 与 tombstone 过滤、空解析/非法 UTF-8 永不 ready、可重试失败→重试成功、租约丢失→恢复；页面结构 + Node 执行 `knowledge.js` |
| 门禁 | `pytest tests/f10 -q` **20 passed**；`go build ./...`/`go vet ./...`/`go test ./...` exit 0；`gofmt -l .` 空；`sync_spec.py --check` no drift；`tests/f00b` 135 passed；f05 合并跑出现 T028 隔离用例环境抖动，单文件复跑 10 passed（见报告 §3） |
| 边界/not_run | 真实 embedding provider（无凭据/adapter，blocked，HTTP/handler 均 fail closed）；精确扫描性能/规模（not_run，T064/T067）；浏览器 DOM（not_run，与既有页面票同一边界）；CSV/XLSX（T059）、PDF（T060）、版本重建（T061）、删除治理（F07/T058）、检索缓存（T067，本票不加表）；未 push/未关 issue、未改 docs/spec/contracts |

## F10/T059 CSV/XLSX 表格摄取与行范围（2026-11-04）

| 项 | 内容 |
|---|---|
| 输入族 | CSV：源文本保留（单 BOM 剥离、CRLF/引号/字段内换行原样）、逻辑记录号（表头=行 1）、行批次 chunk、citation span 回切封存字节；XLSX：stdlib zip+XML 只读，工作簿顺序第一个工作表、共享字符串/富文本、工作表自身行号、`sheet_csv/1` 确定性渲染；行永不拆分，超长行失败 |
| citation | jsonb additive 字段 `row_start/row_end/row_count/header_row/sheet_name/columns/columns_omitted/text_render`；txt/md citation 一个字节不变；无迁移、无契约改动（ADR 0013 §4） |
| 安全/失败 | `<f>` 公式→`ingest_formula_not_supported`（sheet/row/column/cell，缓存值不使用）；DTD/实体→`ingest_dtd_not_supported`；宏→`ingest_macro_present`；路径穿越/条目 512/解压 64 MiB/压缩比 100（总量+单条目）复用 `internal/artifacts` 常量；工作表 64/数据行 10 000/列 64/单元格 640 064/单行 64 KiB；坏 CSV/坏 zip→具体码；全部 failed、business、0 chunk、检索空 |
| 诊断 | `GET /knowledge-documents/{id}/diagnostics` 新增只读派生 `detail`：仅 failed + 解析拒绝码 + 记录 parser revision 未变 + 源 artifact 可读时，用同解析器重放同封存字节；源删/换版→null。不新增表/列/写路径，完成守卫仍是唯一状态写者（ADR 0013 §5） |
| 真实栈 | `tests/f10/` 合并前 28 用例（T055 20 + T059 8），合并 T064 后 41 用例：CSV/XLSX 真实上传（`purpose=knowledge` 准入已存在）→ready→检索→citation 独立真值（封存字节 sha256、rune 切片、独立 Python zip+XML 行值）；10 类内容拒绝；公式重试仍失败→新 artifact 恢复 ready；csv+xlsx+txt 同 ACL/引用/撤销；页面结构 + Node 执行 `formatRowRange`/`describeFailureDetail`；夹具 2 541 B/3 297 B 已提交 + 确定性生成器 |
| 门禁 | 合并前 `pytest tests/f10 -q` **28 passed**（289.16s / 286.58s 两次连跑）；两次合入 integration（含 T064、T058；最终 merge commit `79af135`）后 `pytest tests/f10 -q` **41 passed**（220.94s / 269.94s，含 T064 13 用例）、`tests/tooling` 21 passed；`tests/tooling + f05 t028 quarantine/upload_flow` 37 passed、`f00b t024 artifacts/t022 finalize` 22 passed（合并前树）；`go build ./...`/`go vet ./...` exit 0；`go test ./...` 33 ok/0 FAIL；`gofmt -l .` 空；`sync_spec --check` no drift |
| 边界/not_run | 真实 embedding provider（继承 T055 blocked）；浏览器 DOM（not_run）；PDF（T060）、版本重建（T061）、删除治理（F07/T058）、检索规模/缓存（T064/T067）；XLSX 只读第一个工作表；全量 `pytest tests`、f05/f00b 整包 not_run（orchestrator）；未 push/未关 issue、未改 docs/spec/contracts、无迁移 |

## F10/T060 普通文本 PDF 摄取、页码引用与解析分块对照（2026-11-04）

| 项 | 内容 |
|---|---|
| 准入 | `application/pdf` **仅 purpose=knowledge**（其它 purpose 与基础 allowlist 继续拒绝）；完成 sniff 要求字节以 `%PDF-` 开头（文本/zip/中间 magic → `content_type_mismatch`）；沿用 20 MiB 上限；只验魔数不解析/不 OCR。迁移 `0038_pdf_admission.sql` 只扩 0015/T028 非契约表 `artifact_admissions.detected_format` 词表（+`pdf`），worker/PUBLIC 零授权不变 |
| 解析 | `orchara-pdf-parser/1`（`internal/knowledge/pdf.go`，新依赖 `rsc.io/pdf v0.1.1` BSD-3）：`Tj/TJ/'/" + Td/TD/T*/Tm/TL` 文本矩阵重建；基线下移 1.7×字号 = 段落空行、同行 ¼ em 间隙 = 空格；WinAnsi/MacRoman/pdfDoc/ToUnicode 解码；Form XObject 深度 4。`Parsed.Text` = 页文本拼接（`\n\n`，`text_render=pdf_pages/1`），`Parsed.PDF` 记录逐页 rune span 与 `EmptyPages`。损坏/panic → `pdf_invalid`，`/Encrypt`/密码失败 → `pdf_encrypted`（不解密） |
| 分块/citation | `orchara-pdf-page-paragraph-chunker/1`：复用 T055 段落打包（≤1200 rune、超长 1200/200 重叠）但**逐页**运行，块不跨页；citation additive `pdf{page_start,page_end,page_char_start,page_char_end,page_count,pages_without_text}`（嵌套对象，因 `page_char_start=0` 平铺 `omitempty` 会丢）；chunk body 仍是封存 artifact，完成守卫未改 |
| 无文字层 | `MinPDFTextRunes=32` 非空白 rune；低于门槛 → `pdf_no_text_layer`（detail `part/limit/value`）failed、0 chunk、检索空、重试仍失败；图片-only 页在部分文字层文档中不产生 chunk 并进入 `pages_without_text`。**不做 OCR**，扫描件不冒充成功 |
| 对照 | `probes/f10_pdf_compare/`：Go 基线走真实 `ParserFor`/`ChunkParsed`；Docling 2.146.0 Native+HierarchicalChunker 在隔离 venv 真跑（pip exit 0/306.5 s；模型 505 MB；峰值 RSS 1.89 GB）；PyMuPDF/pypdf/pdfminer 标注为 available substitute（非 Docling）。票面分块候选 LlamaIndex `SentenceSplitter` 不在 probe 依赖内、未运行，替代候选改用本 probe 手写的 `sentence-packer/1`（comparison JSON 标注 substitute；基线仍是注册 Go parser/chunker）；Docling 候选本身真实运行，但 “Docling Native/SentenceSplitter” 组合未原样测量。冻结 5 夹具上 coverage/page-exact/attribution 均不优于基线（pdfminer 布局页 0.5417/0-of-1），替代方案还会把短文本扫描页当可检索 → 四候选全部**淘汰**，保留基线（ADR 0016 §6） |
| 真实栈 | `tests/f10/` 新增 13 用例：plain 3 页 ready + 页码/页内偏移/全局 span 独立真值（committed reference 切片）、partial 空页 [2] 如实记录、scanned/短文本 `pdf_no_text_layer` + 重试仍 failed + 新 artifact 恢复、invalid/encrypted 失败码、PDF 与 txt 同 ACL/撤销/404、准入 422/quarantine、迁移词表、页面结构 + Node 执行 `formatPageRange`；另现场重跑 comparison 断言测量属性 |
| 门禁 | `pytest tests/f10 -q` 见 §3 两次连跑（54 用例）+ `tests/tooling`；`go build ./...`/`go vet ./...`/`go test ./...` 全绿；`gofmt -l .` 空；`sync_spec --check` no drift；对照 JSON/原始 pip 日志已提交 |
| 边界/not_run | OCR 明确不做；Type0/CID 精确宽度/复杂排版 not_run（已知边界）；真实 embedding provider 继承 T055 blocked；浏览器 DOM not_run；版本重建（T061）、删除治理（F07/T058）、检索规模/缓存（T064/T067）；全量 `pytest tests` orchestrator；未 push/未关 issue、未改 docs/spec/contracts |

## F10/T064 回答只引用本次检索且当前可读的资料（2026-11-04）

| 项 | 内容 |
|---|---|
| 回答面 | 非契约 `POST /api/v1/knowledge-bases/{id}/answer`（ADR 0012 D1）：owner-only + CSRF + `application:read`；200 `answered`（含 `generation` 标签、逐引用 provenance、`retrieval.chunk_ids`）、200 `no_results`（空答案不是错误）、422 `answer_without_citation`、422 `citation_not_found`、409 `citation_unreadable`；拒绝不返回答案体/引用 |
| 生成 | 本环境无模型 provider（无凭据/构建无 adapter）→ 带标签的抽取式 stub `extractive-stub/1`：从本次检索前 3 个片段逐字摘录，确定性，引用=使用片段；`generation.stub=true`/`model_provider=not_configured`；真实模型生成 **blocked**。dev-stub 故障约定 `ORCHARA_KNOWLEDGE_ANSWER_FAULTS`（`uncited`/`foreign-chunk`/`slow=`）只允许 stub profile，仅用于端到端观察拒绝路径 |
| 引用校验 | 草稿不可信：无引用→拒绝；每个 cited chunk id 必须 ∈ 本次 `Query` 返回集合；每个引用返回前重新 `GetChunk`（owner ACL、KB/doc tombstone、`state=ready`、`active_revision=revision`、`chunk.document_revision=revision`、body artifact ready/未删/同 owner），任一不满足或复读不一致→`citation_unreadable`；provenance 只从服务端检索行投影 |
| ACL 硬化 | `Query`/`GetChunk` 增加 `a.owner_id = kb.owner_id`：伪造的 chunk 行即使指向真实他人封存字节，也不进入检索候选、citation 端点 404、回答引用不含它（测试插入真实伪造行验证） |
| 页面 | `/knowledge` 第 5 区块：提问、显示 stub 生成方式、答案/检索元数据/引用列表；点击引用重新 `GET /knowledge-citations/{id}`，撤销后显示“引用不可用”且不回放旧正文；四种结果独立文案；仅 `textContent`，无浏览器存储 |
| 真实栈 | `tests/f10/test_t064_answer.py` 10 用例 + `test_t064_pages.py` 3 用例 + Go 单测：真实 PG 16.4（迁移链 0001→0035）、编译 `cmd/api`、真实 HTTP、真实封存字节、真实 `document_ingest` maintenance、第二个真实 API 进程（故障约定）、撤销竞态（`slow=5s` + 日志检查点，≥5s 裕量）、独立真值（源 sha256/rune 切片/行号 + 独立检索集合比对）、A/B/admin、CSRF/scope |
| 门禁 | `pytest tests/f10 -q` 两次背靠背 33 passed（341.33s / 311.88s）；merge 后复跑 33 passed；`go build ./...`/`go vet ./...`/`go test ./...` exit 0；`gofmt -l` 空；`pytest tests/tooling -q` 21 passed；`sync_spec.py --check` no drift（曾因门禁日志写在仓库根产生 1 条 upstream_new，日志移出仓库后复检 no drift） |
| 边界/not_run | 真实模型生成（blocked）；回答不持久化（无迁移）；检索缓存/规模（T067）、版本重建/激活（T061）、删除治理（F07/T058）、CSV/XLSX（T059）、PDF（T060）、浏览器 DOM（not_run）、全量 `pytest tests`（orchestrator）；未 push/未关 issue、未改 docs/spec/contracts |

## F10/T067 检索缓存：命中不返回删除/撤权内容（2026-11-04）

| 项 | 内容 |
|---|---|
| 缓存 | 非契约 `platform.knowledge_retrieval_cache`（ADR 0015；本机无 Redis，PG 即存储且缓存可重建）：`cache_key`=principal + `acl_epoch`（本切片 = KB revision，已记录替身）+ KB revision + 当前未删除文档 `(id,revision,state,active_revision)` 摘要 + embedding revision + canonical query（= TrimSpace，与 T055 嵌入字符串一致）+ top_k 的长度前缀 SHA-256；组件值全部存列；TTL 900s；单条目 256 KiB 上限；无后台驱逐 kind（过期按 principal 懒清理） |
| 命中 | 每次先重算键 → 组件逐项比对 → 过期判定 → 对 items 中每个 chunk 用 T055/T064 同一 SQL 谓词复检（owner ACL、KB/文档 tombstone、ready、active_revision=revision、chunk 当前 revision、body artifact ready/未删/同 owner）+ 存储的 document/revision/chunk_no/artifact id/sha256 比对；任一不满足删除整行走 miss。命中计数/`last_hit_at` 只作证据；读/写/复检 SQL 失败按 miss（权威路径照常回答） |
| 隔离 | 键含 principal，行按 `(cache_key, principal_id, knowledge_base_id)` 读取；正文只回同 principal+同 KB。测试用"活行投毒"：被删文档条目注入当前键活行、Alice 的 chunk/正文注入 Bob 当前键活行，均整行丢弃且正文不出现；Bob 问 Alice KB 仍 404 |
| 四类 RAG 失败 | 缺目标来源→`no_results`（空/部分结果不缓存）；错答案→新增 `wrong-answer` dev-stub 故障（保留真实引用、只换文本）→`answered` 但文本无依据，归因生成阶段；伪引用→缓存命中时仍 422 `citation_not_found`（命中计数 +1 证明确实命中）；越权片段→body-artifact owner 复检失败整行丢弃，检索/引用/回答都拿不到。两类另用真实 cache-off 进程对照同归因 |
| 对照实验 | 真实第二 API 进程 `ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE=off`；8 文档/24 chunk、3 查询 × 8 次测量 = 每模式 24 次：cache on 命中 p50/p95 **6.954/29.881 ms**，cache off **28.391/55.102 ms**（另一轮 5.249/10.672 vs 25.883/43.478）；冷请求 35.0/26.3/29.3 ms；正确性（items/acl_epoch/embedding revision）逐字段相同；top_k=5 vs 20 逐项复检 ≈0.09–0.22 ms/项；**有收益，保留缓存** |
| 真实栈 | `tests/f10/test_t067_{migration,cache,rag_classes,comparison}.py` 共 16 用例 + Go 单测（键/开关/策略/wrong-answer）：真实 PG 16.4（迁移链 0001→0037、重复 migrate no-op、worker 零权限）、编译 `cmd/api`、真实 HTTP、真实封存字节、真实 `document_ingest` maintenance、第二/第三真实 API 进程（cache off / dev-stub 故障）；真实活缓存行注入、文档 tombstone、KB revision bump、复制 revision 2 chunk 行 |
| 门禁 | `pytest tests/f10 -q` 57 passed ×3（688.47s / 721.45s 背靠背；merge `e225967` 后 762.66s）；`tests/tooling` 21 passed；`go build/vet/test`、`gofmt -l`、`sync_spec --check` 干净；合并仅 PROGRESS 冲突（T062 + T067 双方保留），ADR 0014 被 T062 占用 → 本票重编号 0015 |
| 边界/not_run | Redis 不采用（ADR 0015 D1）；异步驱逐/物理清理（F07 优化，正确性不依赖）；真实 embedding provider（继承 T055 blocked）；ANN/规模（not_run）；浏览器 DOM（无页面改动，not_run）；全量 `pytest tests`（orchestrator）；未 push/未关 issue、未改 docs/spec/contracts |

## 批次完成记录：#53–#68 / T052–T067（2026-11-04）

本批 16 票（T052–T067，GitHub #53–#68）全部合入 `integration/f13a-f15a-f09-f10`；
批次完成时 tip 为 **`4537ef2`**（`f1112ed` 上的 T061 迁移链测试修正）；其后的评审修复
提交（`6e481a4` 至 `18ec66b`）只修评审项，不改变合并顺序与票面结论。按 first-parent
合并顺序（按票）：T053 → T057 → T054 → T055 → T052 → T064 → T058 → T059 → T062 →
T067 → T060 → T063 → T056 → T066 → T061 → T065；T053 有过一次载荷修正二次合入，
其余为合并重编号/门禁修正提交（最终 ADR/迁移编号以 `docs/adr/` 与 `db/migrations/` 为准）。
各票的例外结论保持原样：T053 候选 S3 验收 **blocked**（本机无候选端点），T066 是
**`do_not_adopt`** 实验票。

**评审修复后的最终 tip**：`3b2a8fc`（`3ac8a80` 评审修复 + `3b2a8fc` T028 链测试陈旧断言修正）。
最终 tip 上的全局门禁：`go build ./...`、`go vet ./...`、`go test ./...`（无 FAIL）、`gofmt -l` 空、
`sync_spec --check` no drift、`tests/tooling` 21 passed；评审修复触及的套件另跑
`tests/f13a -k "t056 or t062 or t065"` 34 passed ×2、`tests/f10 -k "t055 or t061"` 33 passed ×2。
批次级单次 `pytest tests` 在本机未能一次跑完：首次运行因 C: 被 pytest 临时目录占满
（14.72 GB，0 字节可用）在 82% 中断（3 failed / 214 errors；其中 1 个是真实陈旧断言，已修，
其余为磁盘占满与 Postgres 重启所致），随后按目录分片重跑又因宿主机在 ~11 小时后降级
（WSL/PG 卡死）未能全部完成；因此批次级证据以**逐套件执行记录**为准，未声称“一次全绿”。

**状态闭合（覆盖本文件更早的行文，不改写各票行）**：

- **#53/T052 的 `partial` 边界由 T056 闭合**：T052 当时的未闭合项是“案例输入不能引用
  调用方 CSV”；T056 按内容 sha256 把调用方 fixture 绑定为 run 的 `task_input` 后，真实
  succeeded 销售案例（CSV/XLSX）逐例 `passed` 并进入密封报告
  （`tests/f13a/test_t056_sales.py::test_a_succeeded_csv_sales_case_yields_a_passed_report`）。
  T052 行的 `partial` 是历史记录。
- **取消案例不再是 `passed`**：T062 把“完成”收紧为 `CompletedRunState`（仅 succeeded/
  failed），cancelled → `failed/run_cancelled`（`internal/evaluation/collect.go`；
  `tests/f13a/test_t062_runner.py::test_a_cancelled_case_never_passes`）。T052 行/报告里
  “预定 cancel 使报告 `passed`”的文字同样是 G1 修复前的历史，当前以 T062 为准。

**非契约面（不要当成契约）**：以下表面是票面 AC 所需、经 ADR 记录的非契约增量；
`contracts/**` 与 `docs/spec/**` 本批未改，`python tools/sync_spec.py --check` no drift：

| 非契约面 | 为什么需要 | ADR |
|---|---|---|
| `GET /api/v1/evaluation-reports/{id}/cases` | T056 的逐例期望/实际/证据读取；契约只有提交与整体报告 | 0017 |
| `GET /api/v1/evaluations/{id}/progress` | T065 的异步进展读取（且不从 state 换算百分比） | 0019 |
| 检索/引用/回答的 additive 字段（`retrieval.*`、`generation.*`、citation 的 `pdf` 等） | T055/T060/T064 要可追溯引用与有依据回答；契约的检索/引用形状不承载这些证据 | 0010/0012/0016 |
| `retrieval.index`（活动索引版本） | T061 证明“哪套索引/模型服务了本次结果”，不改变契约响应形状 | 0018 |
| `retrieval.cache`（命中/未命中元数据） | T067 做 cache on/off 对照，并证明命中仍逐项复检 | 0015 |
| `ORCHARA_KNOWLEDGE_ANSWER_FAULTS`（`wrong-answer` 等） | #68/T067 的“四类 RAG 失败分别定位”需要确定性复现路径；仅 stub profile 生效、只能造成拒绝/错误答案标签，不是生产配置面 | 0012/0015 |

其余非契约面（T062 管理员归属读、F09 记忆端点、T060 的 `application/pdf` 准入与解析器
revision）分别见各票行、报告与 ADR 0014/0009/0016。

## RV1 评测完整性复核修复（2026-11-05，分支 `fix/rv1-eval-integrity`）

独立复核确认两项 P1，本分支已修复并以真实 PG/真实二进制/真实 HTTP 回归：

| Finding | 修复 | 证据 |
|---|---|---|
| 1（标准+规格）管理员可读他人逐例结果正文 | `internal/catalog/evaluation_cases.go` SQL 去 `OR admin`、**删除 `admin bool` 参数**（编译期强制）；`internal/api/evaluation_cases.go` 不再传 role；ADR 0017/T056 报告/PROGRESS 同步 owner-only | `tests/f13a/test_t056_sales.py::test_a_succeeded_csv_sales_case_yields_a_passed_report`（admin 404 = missing 同码同文，owner 200 正文不变）、`tests/f13a/test_t062_authz.py::test_accounts_are_isolated_on_reads_and_submission`（合同读 admin 仍 200，逐例读 admin 404） |
| 2（规格）`effect_count` 把未确认/未派发当副作用 | `internal/execution/evaluation_runs.go` `effectCounts` 只计 `state='succeeded'`；`unknown`/`dispatching` → `EffectsCollected=false` + `effect_evidence=unconfirmed_unknown|unconfirmed_dispatching`（不报数字）；failed/prepared/authorized/awaiting_approval 计 0；capability 键 = 原样 `capability_version`（两侧 trim+lowercase，按 version 分组，非 run 总数），facts/断言 detail/报告 limits 明示；`evaluation.NormalizeCapabilityKey`、`CaseFacts.effect_evidence` 经内部 facts 桥全链贯通 | `tests/f13a/test_rv1_effect_evidence.py::test_effect_count_reads_confirmed_effects_from_the_real_ledger`（7 场景：succeeded=1、非派发=0、unknown/dispatching 未采集、空账本 0、按 version 键、混合不采集）；Go 单测 `TestEffectCountLooksUpTheStoredCapabilityVersionKey`、`TestEffectCountUncollectedCarriesTheLedgerEvidenceReason`、`TestUnconfirmedEffectEvidenceIsRecordedWithoutInventingACount` |

报告：`reports/f13a/RV1-eval-integrity.md`。ADR 0017 增 RV1 amendment。
管理员行为变化：逐例读 `GET /api/v1/evaluation-reports/{id}/cases` 从 **200+他人正文**
变为 **404 `resource_not_found`（与不存在的 report id 同码同文）**；合同评测/报告读仍
owner-or-admin 200。门禁与合入记录见本分支报告 §3（`pytest tests/f13a` ×2、
`tests/tooling`、Go build/vet/test、gofmt、sync_spec）。

## RV2 知识库升级与可读谓词复核修复（2026-11-05，分支 `fix/rv2-knowledge-upgrade`）

外部复核第二轮确认两项 P2，本分支已修复并以真实 PG/真实二进制/真实 HTTP 回归（基线
`8ad3bcc`；收尾合并 integration tip `cd4f7cb`，merge commit **`702b6dc`**）。

| Finding | 修复 | 证据 |
|---|---|---|
| C（规格）从 0039 之前升级的部署把排队中的 `document_ingest` job 永久卡住 | 新迁移 `db/migrations/0041_ingest_upgrade_recovery.sql`（只修数据、无新 schema 对象、可重跑）：把无 `expected_index_version_id` 的非终态 job 置 `failed/ingest_index_unbound`（business、清租约、写 `finished_at`），并把仍处 `uploaded`/`scanning`/`indexing` 且未删除的对应文档置 `failed/ingest_index_unbound`；已有其它 live job 的文档跳过（滚动升级/重放安全），删除的文档不复活。选择“失败”而非“绑定新索引版本”的理由、运行时自愈与边界见 ADR 0018 §6。运行时：完成守卫对无绑定/索引版本缺失或非 building 的尝试结束 job 并（无其它 live 构建时）失败文档；`background.LiveDocumentBuildTx` 唯一定义“构建真的在跑”（非终态 job + 同文档同源 revision 且仍 building 的索引版本），`CreateDocument`/`ReindexDocument`/`insertIndexVersionTx` 据此自愈（放弃版本 `failed/ingest_build_abandoned`）并只在真有 live 构建时返回 409 `index_build_in_progress` | `tests/f10/test_t061_upgrade_recovery.py` 5 用例：真实 pre-0039 schema + 真实 queued job + 真实 `admin migrate`（含删 ledger 重放）+ 真实 maintenance/HTTP 重摄取到 ready；迁移后仍存活的 legacy job 由真实 handler/守卫结束；无 live job 自愈；live 构建期间重复摄取仍幂等；live 构建仍 409、其 job 死后可重建 |
| E（标准+规格）`cache_store.go` 重抄检索/引用的“当前可读 chunk”谓词，权限条件三份会漂移 | 抽出 `internal/knowledge/readable_chunk.go::currentReadableChunkSQL(ownerParam)`（FROM/WHERE 片段，owner 占位符由调用点传入），`rankRetrieval`、`GetChunk`、`validateCachedItems` 三处共用；纯重构，谓词集合逐条不变 | 既有 `tests/f10` 全套（越权/删除/换 revision/换索引/失效引用/缓存活行投毒路径）在合并结果上 88 passed × 2 |

附带（同一轮复核发现的门禁缺陷，非上述两项）：`tests/f10/test_t067_comparison.py` 的
cache on/off 计时断言在并发套件负载下会把两个相隔数分钟的相位比反（实测 hit 14.4/13.9 ms
vs miss 12.9/10.9 ms；同一棵树安静窗口为 3.4 vs 9.9 ms，基线 `8ad3bcc` 在同套件通过）。
改为**成对取样**（每次先 hit 再同一 query miss，同一测量窗口）并在 summary 报告成对中位比，
计时断言降级为文档已声明的“粗回归护栏”（hit p50 不得超过 miss 的 3 倍）；量化结论仍以记录
的 p50/p95 为准（ADR 0015 D5）。

门禁（合并结果 `702b6dc`，隔离 `--basetemp`）：`go build ./...`/`go vet ./...`/`go test ./...`
（33 包 ok、0 FAIL）exit 0；`gofmt -l .` 空；`python tools/sync_spec.py --check` no drift；
`pytest tests/tooling -q` **21 passed**；`pytest tests/f10 -q` **88 passed（394.34s）× 2 背靠背
（395.62s）**。迁移编号 `0041`（tip 上 0040 之后下一个空位；按名字断言，合并重编号不误配）。

报告：`reports/f10/RV2-knowledge-upgrade.md`。ADR 0018 §6（升级恢复）、ADR 0015 D2/D5 与
ADR 0010（共享可读谓词、计时实验方法）。

## F11 删除治理基础：删除后立即不可读、可查询 remaining scopes（T069，2026-11-05）

T069（GitHub #70）已交付，状态 **`partial`**（共享删除基础 + artifact/memory/document
三资源全部真实交付；conversation 的资源权威与读路径属 **F08/T074**，本票只把
`resource_type=conversation` 接进同一基础并对未知 id 返回 404）。证据：
`reports/f11/T069-deletion-foundation.md`、ADR 0020。

| 项 | 结果 |
|---|---|
| 唯一模型 | 复用 0036 `platform.deletion_jobs`；0042 只加 `scope_details/completed_scopes/attempt_count/error_code/started_at/updated_at`，`remaining_scopes` 改为契约固定词表七项（T071 前三、T072 后四），旧 T058 objects 一次性转换；`deletion_jobs_open` 索引；state 闭集/唯一键/owner 不变 |
| tombstone-first | 每个资源在**同一事务**写 `deleted_at`/`state='deleted'`/revision+1（memory 另 bump epoch），job + `deletion.requested` 审计提交后才 202；既有读谓词（artifact `readyRow`、knowledge `readable_chunk.go`、memory recall、run reads）直接复用，无第二套过滤；artifact 删除若为 knowledge source，同事务 tombstone 派生 document |
| 删除 API | `DELETE /api/v1/{conversations,artifacts,memories,knowledge-documents}/{id}` → 202 `DeletionAccepted`（`id`=deletion_job；`resource_id`=资源），重复/并发删除同 job；`GET /api/v1/deletions/{id}` → `{id,state,remaining_scopes}`；owner-only，cookie CSRF，API key `application:write`/`application:read`，他人/管理员/未知同一 404 |
| resource_in_use | 被发布/可恢复运行/模型 profile/工具版本/评测报告/活跃后台 job 引用的配置 artifact → 409 `resource_in_use` + `details.references`；真实冻结 manifest 用例证明不 tombstone、不写 job |
| 受影响运行 | 提交后走真实 T036 `Cancel`：queued/running/waiting/cancelling 被取消（真实 queued run 变 `cancelled` + `run.status` 事件；run artifact 列表立即排除被删 artifact）；`recovery_pending/needs_reconciliation` 留给 T038 恢复门禁，真实 `admin runs recover` 对被删输入返回 `input_deleted`；错误记 `error_code=run_cancel_incomplete` 并可重复删除重试 |
| 页面 | `GET /personal-data` + `assets/personal-data.js`：列记忆/知识文档、按 artifact id 删除、删除后轮询 job 状态与剩余 scope（有界，不假装 purged）；Node 执行纯函数断言 |
| 测试 | `tests/f11/` 7 文件 **22 passed ×2**（211.28s/211.63s；一次真实 WSL sandbox 瞬时失败隔离重跑后两轮全绿）；真实 PG 16.4 + 编译 API/admin/maintenance/supervisor + 真实 HTTP/CSRF/mTLS + 真实 sealed 字节 + 真实 cancel/recovery + Node；迁移测试含 pre-0042 旧对象数组由真实 `admin migrate` 重放转换 |
| 门禁 | `pytest tests/f09` 69 passed（408.36s）、`tests/f10` 88 passed（563.58s）、f05 T028/T033 48 passed（375.42s）、`tests/tooling` 21 passed、`go build/vet/test`/`gofmt -l`/`sync_spec --check` 全绿 |
| 行为变化 | `remaining_scopes` 由 T058 objects 改为契约 string[]；f09/T058 测试、`tests/support/t058.py` 与 mem0 baseline 断言同 change 更新（tombstone/revision/epoch/幂等断言保留） |
| 边界/not_run | 物理清理/chunk/vector/cache 与 `delete_content` handler/retry（T071）、checkpoint/摘要/临时沙箱/第三方/备份账本（T072）、诊断授权（T073）、无复活全套（T076）、conversation（T074）、浏览器 DOM、全量 `pytest tests` |

## F11 副本清理与备份删除账本（T072，2026-11-05）

T072（GitHub #73）已交付，状态 **`passed`**。证据：
`reports/f11/T072-copy-ledger.md`、ADR 0027。

| 项 | 结果 |
|---|---|
| 独立账本 | `db/migrations/0046_deletion_copy_ledger.sql`（票面分配 0048）：`platform.deletion_ledger` append-only（UPDATE/DELETE/TRUNCATE trigger 拒绝）、`record_sha256/prev_sha256` 哈希链（sequence 在 advisory lock 下分配并计入哈希）、与 `deletion_jobs`/资源表**双向无外键**、`entry_kind`/`copy_class` 闭集、worker/PUBLIC 零权限；每次删除在 tombstone 同事务写 `tombstone` 决策（重复 DELETE 不重复写） |
| 导出/回放 | `orchara-admin deletion export --file` 先验链写 JSONL（header count/head_sha256）；`deletion replay --file [--check-only]` 验链后按 `tombstone` 回放 artifact（含 derived document）、memory（revision+epoch）、knowledge_document、conversation（取消请求/invalidations），只改 `deleted_at IS NULL` 的行、`GREATEST(revision+1, deletion_revision)`、回放库内同事务追加 `source=replay` 记录；篡改/断链 exit 1 且零变更；二次回放 `replayed=0` |
| 四 scope | `cmd/maintenance copies`：checkpoint（out-of-band schema 缺失→absent/`checkpoint_schema_absent`；存在→按 thread 删三表）、summary（无存储→absent/`no_summary_store`+surface 计数）、sandbox_temp（按 `run_attempts.execution_unit_id` 定位 `orchara-sandbox-<unit>-*`，活 attempt 不删且记 failed）、third_party_history（默认 absent、`--third-party-components` 后 external）；`backup_catalog` 登记 + vendor/downloaded/backup_media `scope_external` 声明，`job_completed.non_erasable` 明示不可擦除边界 |
| 清除规则 | 同一 evidence 事务：逐类 ledger 行 → 移除 `remaining_scopes`/追加 `completed_scopes`/`attempt_count+1` → 全空才 `completed`+`completed_at`+`job_completed`；失败保留 scope、`state='failed'`+安全 code，内容仍不可读；规则导出 `deletion.ClearScopesTx` 供 T071 复用 |
| 真实证据 | `tests/f11/` T072 16 用例（账本 5 + 副本 5 + 失败/活 attempt 2 + 迁移 4）：真实删除/读 404、真实 checkpoint 三表行、真实主机目录与 Windows 打开句柄导致的真实 `os.RemoveAll` 失败、第二个真实 PG 库（编译 `admin migrate`）回放、真实 CLI 篡改拒绝；`pytest tests/f11` **38 passed ×2**（443.55s/450.46s），最终合并树 **38 passed**（400.70s，merge `3e12f35`，含 T077 的 0045 迁移与代码）；一次真实 WSL 瞬时 `sandbox_failed`（T069 既有 recall 用例）隔离重跑 17 passed |
| 邻接/门禁 | `tests/f09` **69 passed**（593.17s，memory 删除+mem0 实验）、`tests/f08` **19 passed**（186.91s，conversation 删除）、`tests/tooling` 21 passed（合并后 6.62s）、`go build/vet/test` 全 ok/0 FAIL、`gofmt -l` 空、`sync_spec --check` no drift（合并后复跑仍干净） |
| 边界/not_run | 对象/index/cache 物理清理与 `delete_content` handler/lease/completion（T071）、诊断授权（T073）、无复活全套（T076）；pg_basebackup 真实备份、真实崩溃残留沙箱、checkpoint 表锁失败分支、真实 Mem0/供应商擦除、浏览器 DOM、pre-0046 历史回填、全量 `pytest tests` |

## F08 会话/请求基础：固定版本提交、有序队列与处理版本可见（T074，2026-11-05）

T074（GitHub #75）已交付，状态 **`passed`**。证据：
`reports/f08/T074-conversations-and-requests.md`、ADR 0023。

| 项 | 结果 |
|---|---|
| 逻辑槽 | 同一会话最多一个“最早未完成请求”持槽：`one_active_request_per_conversation`（routing/awaiting_* 部分唯一）+ 0014 `one_active_top_run_per_conversation` + 会话行锁；**不**为 `dispatched` 建唯一索引（Run 终止后请求仍 dispatched，会让会话卡死） |
| 固定版本 | `application_version_id` 直接创建 `kind=published` Run：事务内 `FOR SHARE` 复核 published/未撤销/未归档 + manifest artifact ready/hash，复制真实 `execution_snapshots`/`runs`/`start` job，只链接封存 message/显式附件/确认摘要；**无** route_request job、**无** model call；吊销 409 `version_revoked`、不可见 404 |
| 幂等 | `platform.idempotency_keys`（principal+POST+/api/v1/requests+key）同体 202 重放同 Request/Run，异体 409 `idempotency_conflict`，删除会话后回放 404 |
| 队列/取消 | 普通新请求排队；取消 slot holder 同事务推进下一项（`AdvanceConversationQueueTx`）；dispatched 请求取消走真实 T036 `Cancel`，随后推进；Run 经既有执行取消结束后，排队请求的下一次读取惰性推进；重复取消/删除幂等 |
| 交接与隔离 | `request_handoffs` 记录确认人/来源 Run/摘要 artifact/来源版本；服务端校验 `confirmed`、同会话来源 Run、附件 owner+ready；新 Run 输入闭包仅 message+显式附件+摘要，旧会话历史永不作为输入；个人记忆按 `(owner, application)` 隔离（真实 memory API 证明） |
| 处理版本可见 | 契约 `Request` 无字段，走文档化响应头 `X-Request-Application-Version-Id`/`-No`（+ conversation id/queue position/interaction correlation）；`/chat` 页面显示“vN 正在处理/已处理” |
| 会话删除 | 注册 `deletion.Target`：tombstone+revision 同事务、7 scope job、读立即 404、queued/routing/awaiting 请求取消、活动 Run 经 T069 取消扇出；`tests/f11` 43 passed 不回归 |
| 测试 | `tests/f08` **19 passed ×2**（195.23s/196.02s）；`tests/tooling tests/f11` 43 passed；最终 merge `7138ae9`（带入 T068 0043 + T070 撤销 guard）后 `tests/f08` **19 passed ×2**（201.09s/183.13s）、`tests/t070` 12 passed、`tests/f02b tests/t070 tests/f11 tests/tooling` **73 passed**；`tests/f02a` 的 T025/T027 直接相邻 13 passed；`go build/vet/test`/`gofmt -l`/`sync_spec --check` 全绿；真实 PG 16.4 + 编译二进制 + 真实 HTTP+CSRF + 真实封存字节 + 真实取消路径 + Node 24 |
| 边界/not_run | 路由/候选/澄清/控制预算（T079）、交接 UI（T082）、目录索引（T077）、浏览器 DOM、全量 pytest；本票在基线合并中遇到 T068 的 `0042` 撞号，工作区临时改名 `0043` 运行迁移链；最终 merge `7138ae9` 带入 T068 的正式 0043，链为 0042→0043→0044，无重复缺口；本票固定 0044 |
## F02B 发布审核与不可变版本（T068，2026-11-05）

| 项 | 内容 |
|---|---|
| 提交 | `POST /applications/{id}/submit`（owner+creator+CSRF，`Idempotency-Key`）：对当前草稿 `ValidateExecution` 并要求 hash==`expected_manifest_sha256`；重读不可变 report/evaluation/snapshot/sealed manifest/sealed report 字节，按 T062 逐 case run 复查 runner 可信性；五类作者案例门禁；冻结 shared-definition 快照（用途卡/模型目的地/工具读写联网/固定子版本/连接器/report 证据）；事务锁 draft 行再验 execution_revision；同内容重复提交返回已有 review；202 `{id,state}` |
| 批准 | `POST /reviews/{id}/decision`（仅 admin，`Idempotency-Key`，body hash 必须等于冻结 hash，reason 记录）：再次复查报告/五类/应用未归档/冻结依赖闭包；单事务插不可变 version（用冻结 manifest 规范字节）+ version_dependencies + `applications.active_version_id` + `outbox(catalog_index)` + audit + review approved；reject 只记录状态/原因；并发批准 = 一个版本、一个 approval（真实并发测试） |
| 读/页面 | `GET /reviews`（admin 全量；creator 只看本人提交/本人应用；cursor+limit）与 `GET /reviews/{id}`（未知/他人 404）；非契约 `POST .../publication-preview` 与提交共享同一投影；`/apps/{id}/publish`、`/reviews[/{id}]` 页面 + Node 执行 |
| 五类门禁 | 规范句 EVALUATION.md §6；schema 无类别字段 → 文档化最小判定（`catalog/publication_cases.go` + ADR 0021 §3）：normal=run succeeded；output_check=passed json/decimal 断言；failure_reason 按平台码分类 tool_*/sandbox_*、ambiguous/out_of/limit/row_limit/over_limit/too_large、其余坏输入；五类齐且 ≥5 passed case |
| 真实证据 | `test_t068_e2e.py`：真实两池 maintenance + 真实 WSL2/runsc 产出 5 例真实 runner 报告（1 正常+输出检查、2 输入 schema 拒绝、1 row_limit_exceeded、1 真实 sandbox_unavailable 工具失败，覆盖五类）；提交后撤销真实工具依赖 → 批准 409 `dependency_unavailable`（located `dependency_revoked`）→ 恢复 → 批准 200 → version/active/outbox/audit/version 读全部断言。其余 review 事务用例用真实 guard 接受的密封报告（passed case 绑真实 case run 行，终态种子；报告 §4 明示） |
| 门禁 | `pytest tests/f02b` **18 passed ×6**（合并前 290.87s/242.53s；T069 合并后 254.04s/251.68s；T070 合并后 261.65s/252.17s，含真实 runner e2e）；`tests/t070` 12 passed（153.54s）、`tests/f02a` 103 passed（1170.93s；合并 T074 后 1 条旧 FK 断言更新并单测复跑通过）、`tests/tooling` 21 passed（6.43s）、`tests/f13a -k "t052 or t056 or t062"` 42 passed + 9 deselected（859.62s）；`go build/vet/test`、`gofmt -l`、`sync_spec --check` 全绿；真实 PG 16.4 / 编译二进制 / 真实 HTTP+CSRF+mTLS / sealed 字节 / Node |
| 边界/not_run | T070 撤销端点与撤销后目录/取消；T074 从 published version 建 Run；T075/T078 child run（含子应用依赖的发布端到端 **partial**）；T077/T082 outbox 消费者/目录索引；浏览器 DOM；全量 `pytest tests`；未 push/未关 issue、未改 docs/spec/contracts |

## F08 授权用途目录：投影索引、落后回退与安全重建（T077，2026-11-05）

T077（GitHub #78）已交付，状态 **`passed`**。证据：
`reports/f08/T077-capability-catalog.md`、ADR 0026。

| 项 | 结果 |
|---|---|
| 投影 | `platform.application_catalog_entries`（契约的 version/text/hash/updated_at + 非契约 `application_id`/生成 `ts` GIN；**不建** embedding 列，pgvector 不可用，ANN 归 T064/T067）+ 单行台账 `platform.catalog_index_state`（state/epoch/rebuild_started/rebuilt_at/last_error_code/expected/indexed）；迁移 `0045_catalog_index.sql`（orchestrator 预分配 0051，合入需重编号；测试按 slug 断言） |
| 搜索与 ACL | 非契约 `GET /api/v1/catalog/search`（`application:read`）+ `/catalog` 页面；ACL（owner 或未过期 read grant）、未归档、未撤销、同 application、manifest hash 在 SQL 内、排名/LIMIT 之前；keyset cursor `(rank,version_no,id)`；`listApplications` 契约响应零改动 |
| 陈旧与回退 | 就绪 = 台账 `ready` **且** 活“可读未撤销未归档可索引版本数” == 活“hash 仍匹配的合法投影行数”；不一致/缺失/重建中/失败/hash 不匹配 → 授权全文回退（ACL+归档在 SQL，撤销留给逐行二次查权），`retrieval.source=fallback/stale=true/rejected_by_recheck` 如实上报；每条返回行都由另一条 SQL 重读权威 version/application 后返回 |
| 重建/中断 | `orchara-admin catalog rebuild [--all|--version]`：先提交 `rebuilding`+新 epoch（不读版本表）→ 删陈旧行 → 逐版本一事务写 + 推进计数 → 最后 `ready`+`rebuilt_at`+投递 outbox；失败记录 `last_error_code/last_error_at`；真实 `ACCESS EXCLUSIVE` 锁 + 杀进程的中断只留 `rebuilding` 台账（epoch/start 在），搜索回退不半服务；`catalog index --version` 入队 `catalog_index` job（dedup `<version>:<manifest_sha256>`），handler 写事务内复读权威行、坏卡 business 失败 |
| 分离 | 目录表/授权谓词/重建路径/失败台账与 `knowledge_chunks`/`knowledge_index_versions` 完全分离；真实知识行计数与身份签名在 catalog rebuild 与 incremental handler 前后逐字节不变 |
| 测试 | `tests/f08` **35 passed ×2**（最终 merge `4dfcf37` 后 522.07s/498.01s；其中 T077 16 = 迁移 3 + 搜索/ACL 3 + 重建/分离 5 + handler 3 + 页面 2）；`tests/tooling tests/f02b/test_t068_{migration,pages}.py tests/f02a/test_t023_{migration,pages}.py tests/f02a/test_t027_migration.py` **56 passed**（356.83s）；`go build/vet/test`、`gofmt -l`、`sync_spec --check` 全绿；真实 PG 16.4 + 编译二进制 + 真实 HTTP + 真实 sealed manifest 字节 + 真实 admin rebuild/maintenance 进程 + Node 24 |
| 边界/not_run | T079 路由/候选选择/澄清/控制预算、T082 交接 UI、T078 子运行、T071/T072 删除清理、T068/T070 端点（本票只发 `IndexVersionTx`/`EnqueueIndexVersionTx` 接缝，未接其事务钩子）；浏览器 DOM；中文分词/检索质量实验未运行（只做子串+simple 词项，不宣称质量）；全量 `pytest tests` 与全量 f02a/f02b 未运行（只跑相邻迁移/页面文件）；未 push/未关 issue、未改 docs/spec/contracts |

## F08 路由控制面：候选建议、澄清/选择答复与唯一主运行（T079，2026-11-05）

T079（GitHub #80）已交付，状态 **`passed`**。证据：
`reports/f08/T079-router-and-interactions.md`、ADR 0029。

| 项 | 结果 |
|---|---|
| 候选 | 授权 SQL（ACL 在 LIMIT 前 + archived/revoked/非空卡 + 词项/全文）先落 `request_routing_candidates` 快照（rank 序号，无 score 列），再把六字段能力卡交给控制模型；全库不先生成候选 |
| 控制模型/预算 | `model_profile_versions.config.route_control=true` 的 passed 候选由服务端解析；每次物理尝试一行 `platform_model_calls`（`background_job_id=route_request job`，request_id NULL 满足 XOR）；预算 = 账本行数，3 次后写 `degraded`：≥2 候选转 selection，否则 `failed(route_budget_exhausted)`，无第 4 次调用 |
| 服务端校验 | 模型只建议 `select|clarify|no_match`；`select` 必须命中快照（否则 `route_candidate_invalid`，零 Run/interaction）；>1 候选一律 `awaiting_selection`（全部可见）；=1 且 `required_inputs` 满足才派发；候选/权限/发布状态在派发事务内用 `ResolveRunnableVersionTx` 复核 |
| interaction/答复 | proposal 封存 artifact + correlation id + 15 分钟 TTL，`Request.interaction.proposal` 投影；答复路径级幂等 receipt；精确 CAS `state='pending' AND expires_at > now()`；0 行回滚 receipt 后独立事务 expired+`failed(interaction_expired)`+推进队列 → 409 `interaction_closed`，旧答复不复活任务；显式 `candidate_version_id` 记录到 `requests.selected_version_id`，恢复轮不重调模型但复核快照/ACL/输入 |
| job/守卫 | 真实 `route_request` handler（维护 mTLS + live lease）执行服务端一轮；重试表含 transient；终止失败的 completion guard 把仍 `routing` 的请求置 `failed(route_failed)` 并经 T074 接缝释放会话槽 |
| 页面 | `/chat` 交互面板显示提问/缺失字段/过期提示/候选（序号仅为顺序），可单选候选或补充说明答复；Node 纯函数执行路径/候选/答复体/幂等 key，并断言无概率词 |
| 测试 | `tests/f08` **54 passed ×2 背靠背**（949.35s/842.00s）+ 最终 merge `085d025` 后 **54 passed**（779.55s；T079 19 = 路由 3 + 交互/预算 8 + job guard 2 + 迁移 4 + 页面 2，另 Go `internal/routing` 单测）；`tests/tooling` 21 passed、`tests/f11` 22 passed（conversation 删除线）；`go build/vet/test`/`gofmt -l`/`sync_spec --check` 全绿；真实 PG 16.4 + 编译 API/admin/maintenance + 真实 HTTP+CSRF/mTLS + 真实封存字节 + Node 24 |
| 边界/not_run | 真实 vendor 模型 adapter/凭据（F06）；语义/中文分词召回质量（T064/T067）；T082 交接 UI（保留 queue/interaction 接缝）；T078 子 Run；浏览器 DOM；全量 pytest；未 push/未关 issue、未改 docs/spec/contracts |

## F11 异步清理对象和索引并可观察失败重试（T071，2026-11-05）

T071（GitHub #72）已交付，状态 **`passed`**（T071 三个 scope：`object_store`/
`search_index`/`retrieval_cache` 真实幂等清理；失败禁读 + `GET /deletions` 可查
`failed` + 真实重试完成；sealed 孤儿 sweep 不误删合法引用；T072 四 scope 诚实保留）。
证据：`reports/f11/T071-async-purge.md`。

| 项 | 结果 |
|---|---|
| purge 单元 | T069 删除事务内 enqueue `delete_content`（target=deletion job、dedup=`deletion:<id>`、owner=请求主体）；handler 只删字节，completion guard 在同一事务验证并写 `remaining_scopes/completed_scopes`；同 dedup 不产生第二个 job，operator replay 真实跑 handler+guard 为 no-op |
| scope 语义 | object_store：命名 key（artifact/memory body，含 update 取代的历史 body）+ 已 tombstone 文档的 chunk-body artifact，`Stat` 验证消失后才写 `artifacts.purged_at`/`state='deleted'`；search_index：chunk/vector 行 + index version 行 + active 指针（`memory_embeddings` 未建→诚实清空）；retrieval_cache：items 含被删 document/chunk 的行；只清验证过的 scope，T072 四项保持 pending |
| 失败/重试 | 真实 Windows 文件句柄 sharing violation：attempt1 retried/`transient_storage_io`，`GET /deletions` failed + 未清 scope、读端点 404、字节仍在盘；释放后同 job attempt2 succeeded，deletion job 回 pending、`error_code=NULL`；guard 从不把“对象仍在”变成 error（不杀 `maintenance run`，进程 exit 0） |
| 合法引用 | 命名 artifact 若仍被 live document/chunk/memory/candidate 引用则跳过 + `object_shared_reference`（business）；真实“删除 doc A 后同 source 重新 ingest 到 KB B”用例证明 B 可检索、source 字节存活、A 仍拒绝 |
| orphan sweep | 无引用 sealed 且 `age>=max(--orphan-age,10m)` 才删；artifact/run_artifact/knowledge chunk/memory/memory candidate 五类引用显式检查；tombstone artifact 的字节留给 deletion job，sweep 不竞争；dry-run 同集；重复 no-op |
| 测试 | 最终集成树 `tests/f11` **68 passed ×2**（846.70s/979.59s）；T071 切片 **32 passed ×2**（348.53s/334.38s；含 conversation object_store 清理与未知资源 fail-safe）；真实 PG 16.4 + 编译 API/admin/maintenance + 真实 HTTP/CSRF/mTLS + 真实 sealed 字节 + 真实文件句柄失败 + 真实 mtime 的 10m grace |
| 门禁 | 最终树 `tests/f00b`+`tests/f10`+`tests/tooling` 41 passed（291.56s）、f05 artifact/tool-run 57 passed（T077 树 621.62s）、`go build/vet/test ./...`/`gofmt -l .`/`sync_spec --check` 全绿；f05/T028 的 T074 兼容断言已 test-only 修复 |
| 边界/not_run | timeout 实际触发、attempt 耗尽终态、T076 无复活全套、真实 S3/MinIO、浏览器 DOM、全量 `pytest tests`；T072 四 scope 与 T073 诊断由对应票在集成树交付；迁移最终 `0047_deletion_purge_paths.sql`；集成合并 fast-forward 到 `a4e946d` 后再 merge 到最终 tip `affc12c`（合并提交 `5235711`），并把 T078 的重复 `0048_f07_child_runs.sql` 只重编号为 `0049`（集成 `affc12c` 同一修复）|

## F07 子运行与委派结果（T078，2026-11-05）

T078（GitHub #79）已交付，状态 **`passed`**。证据：`reports/f07/T078-child-runs.md`、ADR 0025。

| 项 | 结果 |
|---|---|
| 契约入口 | `POST /internal/v1/runs/{run_id}/children`（worker mTLS）→ 202 契约 `Run`；同父调用键重放返回同 id 且无第二行；同键异输入/异版本 409 `child_invocation_conflict`（details.field） |
| 固定子版本与权限 | 父 Run 冻结 snapshot 的 `subapp_versions` 成员检查；子版本用调用者**当前**读 ACL 解析（撤权即时 403）；child Run `owner_id` = 父 owner，不是子应用作者；child snapshot 是子版本自己的冻结 manifest |
| 最小上下文 | 只封存显式 mapping 的字段（`io_mapping` 记录字段名），父输入中的历史标记不出现在 child input artifact；输入用 JCS 规范化并记录 sha256 |
| 结果 schema | finalizer 内 `ResultValidator` 用子版本冻结 output schema 校验真实 output artifact 字节；不合规 child `failed`/`child_output_schema_invalid`，父 Run 不受影响 |
| 执行器 | `call_app` 与 `agent.subapp_versions` 真实执行：spawn→`waiting_children`（`child:<id>` checkpoint）→child 独立 Run 由真实 supervisor 执行→重放取回结构化结果并在节点 schema 下端到端完成；agent 的模型 tool call 只映射到 `spawnChild` |
| 页面/适配 | Run 页 child 面板（列表/状态/结构化结果/详情链接，Node 执行纯函数）；Python `PublishedAppDelegate` 真实 mTLS 对真实 API 完成 spawn/waiting/replay/result |
| T075 闭环 | 真实 submit/approve 发布父版本与 `version_dependencies` 边；发布更严格子 v2 后父版本字节/边/行不变；切到 v2 被 `io_mapping_missing_field` 拒绝 |
| 门禁 | `pytest tests/f07` 22 passed ×2（284.83s/226.73s，最终树复跑见报告）、`tests/tooling`+`tests/f02a` 依赖 27 passed、`tests/f03` claim/waiting/cancel 26 passed、`tests/f06` T041/T043 见报告、`go build/vet/test`/`gofmt -l`/`sync_spec --check` 全绿；真实 PG 16.4 / 编译二进制 / 真实 HTTP+mTLS / 真实封存字节 / 真实 result+exit / Node 24 |
| 边界/not_run | child 终态自动恢复、独立子槽、每父并行（T081；本票测试用既有 T035 bridge）、父预算/次数原子（T080）、真实 WSL2/runsc 五类评测（依赖 T081；T075 闭环用 T068 已验证五类 fixture）、浏览器 DOM、全量 pytest |

## F07 并行子调用的父预算（T080，2026-11-05）

T080（GitHub #81）已交付，状态 **`passed`**。证据：`reports/f07/T080-parent-budget.md`、ADR 0031。
范围 = 并行委派仍受**同一份父预算/次数/深度**约束、超限原因回到父 Run、恢复/备用不重置额度；
child Run 协议不变，金额全程十进制。

| 项 | 结果 |
|---|---|
| 一份账本一份额度 | 新增只读 seam `budget.LockAllowanceTx`：与每次模型预留完全相同的顶层祖先解析 + 根 Run 行 `FOR UPDATE`，在事务内重算 `limit-(Σcharge+Σreserve−Σrelease)`；child 不建独立预算行/预留信封（ADR D1） |
| 委派时冻结 | `children.Spawn` 用该 seam 冻结 child `budget_limit/currency` = 共享根额度（不再用 T078 的未加根锁私有递归读）；父 Run 行锁 → 根行锁的顺序与预留一致 |
| 成本原子性 | 两个已 spawn 的 child 各自发起真实模型调用，经预算根行锁并发门：恰好一个 200、一个 429 `budget_exhausted`；loser 零 `usage_ledger` 行、零 attempt；所有根行 `budget_root_run_id` = 父 Run；`usage_ledger.amount`/`runs.budget_limit` 均 `numeric(20,8)`（无 float） |
| 次数原子性 | 父 Run 行锁下 3 个并发 `spawnChild`（`max_child_calls=2`）：恰好 2×202 + 1×429 `child_calls_exceeded`，无第三条 Run/收据/输入 artifact；次数来自 `run_children` 行，恢复不重置 |
| 深度原子性 | 嵌套委派在真实祖先链上测深度（2），与委派 Run 冻结的 `max_child_depth=1` 比较 → 422 `child_depth_exceeded`（details depth/max_child_depth），零行写入 |
| 超限回父任务 | 父额度为 1 单位时 spawn 允许、child 模型预留 429 `budget_exhausted`；child Run 记录同码且无预留行；T035 bridge 决议后父 Run 恢复、重放同一 child 并 `failed`/`budget_exhausted`（`run.status` 事件同码） |
| 恢复/备用 | 真实 `orchara-admin runs recover`：账本行逐行不变、根剩余不变、child 冻结上限不变；重 claim 后同尺寸预留仍 429。备用模型（不可用主模型）只在对同一根额度放得下时启用，4 reserve/release + 1 charge，无第二次 charge |
| 门禁 | `pytest tests/f07` **30 passed ×2**（442.55s / 384.54s）、`tests/tooling` 21 passed、`tests/f06` T046 12 passed、`tests/f03` claim/waiting/cancel 26 passed、`go build/vet/test ./...`、`gofmt -l .`、`sync_spec --check` 全绿；真实 PG 16.4 / 编译 `cmd/api`+`cmd/supervisor`+`cmd/admin` / 真实 HTTP+mTLS / 真实并发 child / 真实 admin recover |
| 边界/not_run | child 终态自动恢复 hook 与独立 child 槽（T081；父收敛用例用既有 T035 bridge，与 T078 一致）、浏览器 DOM、真实厂商模型（dev-stub）、全量 `pytest tests`；无迁移（复用 0028 账本与 0049 `run_children`） |

## F11 删除后不复活回归与清理告警（T076，2026-11-05）

T076（GitHub #77）已交付，状态 **`passed`**（OTel/metrics 专有行 **`partial`**：本仓库无 tracer/metrics SDK）。
证据：`reports/f11/T076-no-resurrection.md`、`docs/adr/0033-no-resurrection-and-cleanup-alerts.md`、`tests/f11/test_t076_*.py`（6 文件 13 用例）。

| 门禁 | 证据（真实入口） |
|---|---|
| 恢复拒绝每一个输入 | `input_deleted`：主输入 ∪ `run_artifacts.relation='input'` 全集；修复前"删附件后仍 recovered"由失败测试先复现，两次重放均拒绝且不写证据 |
| 晚到 finalize | 真实 DELETE 后重放 finalize → `source_deleted`、无新 sealed 字节、artifact 仍 404（两次）；删除前 live writer 时 DELETE 409 `resource_in_use` |
| 晚到 ingest | parked handler（扣住 finalize pool）→ 真实删文档 → 放开后激活事务 `source_deleted`、索引永不激活；T071 purge 清掉晚到 chunk body；重放两次无新字节/新 job |
| 等待/领取边界 | waiting_approval Run 的输入被删 → 取消 + 两次审批决定均无 resume job；队列 Run 输入被删 → cancelled 且两次 claim 204、无 attempt |
| 清理超时告警 | `orchara-admin deletion alerts`：真实 `--delete-content-timeout 1ns` → `cleanup_timeout` + 逾期 `deletion_job_overdue` + 停滞 `cleanup_stalled` + 失败；exit 3/0；完成后恢复 `ok`；输出无正文/object key/hash |
| 组件删除门禁 | `gates.f11_component_deletion`（T066 `do_not_adopt` 扩展）：`residual ∪ unverifiable ∪ deletion_sufficient:false` → `do_not_adopt`；记录校验拒绝缺门禁/自相矛盾的 adopt；提交证据不变 |
| 备份/恢复边界 | 备份字节回写后仍 404（对象非权威）；未回放账本的"恢复"会复活行 → 真实 `deletion replay` 重新 tombstone，二次 `replayed=0`；第三方/备份/已下载边界在告警报文与报告如实声明 |
| 治理证据集中 | `test_the_whole_deletion_governance_chain_has_evidence_at_every_gate` 顺序重放 10 个门禁并集中记录证据，缺一即失败 |

## F07 等待容量与子任务收敛（T081，2026-11-05）

T081（GitHub #82）已交付，状态 **`passed`**。证据：`reports/f07/T081-waiting-capacity.md`、ADR 0032。

| 验收 | 结果 |
|---|---|
| 20 父等待时独立子槽推进 | **passed**：20 个父停在 `waiting_children`、`execution_slots=1` 被一个运行中的顶层 Run 占死；20 次真实 supervisor 执行各自领取一个 child 并 `succeeded`，Python 池全程 used=1；hook 自动为 20 个父产生 `children/succeeded` decision + 单一 resume job（父池饱和所以 queued） |
| 每父并行上限/公平领取 | **passed**：父 A 4 个 child、父 B 1 个；A 第 4 个在 3 个并发时 204，一个 child 真实 succeeded 后才领取；B 在 A 刚领取后优先（父轮转），尽管 A 剩余 job 更老 |
| 取消/必需失败等待停止 | **passed**：父取消后停在 `cancelling` 且 child 收到取消 intent；child 真实 exit 证据后 child `cancelled`，hook 重驱 finalizer 提交父 `cancelled`；必需子失败以 child 稳定码失败父；数据库时钟断言 `child.finished_at <= parent.finished_at` 与 `child.finished_at <= decision_at` |
| allocation 单次释放 | **passed**：child 的两条 allocation（global+parent）在成功/失败/取消/T038 recovery 路径各释放一次；重复 cancel/exit/recover/replay 后 release ledger 与双计数不变 |
| 边界/not_run | 共享父预算（T080）、真实 WSL2/runsc 五类评测、顶层配额与每会话逻辑槽（F08/T074）、浏览器 DOM、全量 `pytest tests` |

## 集成评审修复（T068–T082 评审报告，2026-11-05）

`integration/t068-t082` 的独立评审报告（3 P2 + 8 P3）已全部修复，每条行为修复都有回归测试；
T075 的 row/section 与 T073 的 row/报告已按事实统一为 `partial`（唯一 `not_run` 项与 trace
边界均已点名）。逐条映射、线性化说明、红→绿证据与门禁原始输出见
`reports/review/REVIEW-FIXES.md`。门禁：`tests/tooling` 21、`tests/f02b` 20、`tests/f11` 84、
`tests/f08` 65、`tests/f07` 36、`tests/t070` 12 全 passed；`go build/vet/test ./...`、
`gofmt -l`、`sync_spec --check` 全绿；`git merge integration/t068-t082` 为 Already up to date。

## 批次完成记录：#69–#83 / T068–T082（2026-11-05）

本批 15 票（T068–T082，GitHub #69–#83）全部合入 `integration/t068-t082`（基线
`integration/f13a-f15a-f09-f10` @ `cd8ca3c`），最终 tip **`4fb29bf`**。按票合并顺序：
T069 → T070（含 T068 基线）→ T074 → T075 → T068 → T077 → T072 → T071 → T073 → T078 →
T080 → T079 → T081 → T076 → T082，其后为评审修复合并（`4fb29bf`）与本记录。

**架构落点**：发布/撤销（F02B）、删除治理 7-scope 与 copy/backup 账本（F11）、会话/请求队列、
授权目录、路由与交接（F08）、固定子应用、子运行、父预算与等待容量（F07）。迁移链
`0042_deletion_foundation` → `0052_handoff_scope_bounds` 连续（0001..0052），重复 migrate no-op；
`docs/spec/**` 与 `contracts/**` 未改，`sync_spec --check` no drift。

**独立验证**（`69154c5`，docs-only 增量为 `d94af24`；复核后最终 tip `4fb29bf` 上
`tests/f02b`+`tests/t070` 32 passed 复跑确认）：迁移 52 条连续无重复、二次 migrate no-op；
`tests/f02b` 18、`tests/t070` 12、`tests/f11` 81、`tests/f08` 65、`tests/f07` 36、
`tests/tooling` 21 全 passed；`go build/vet/test`、`gofmt -l`、`sync_spec --check` 干净。
1 个 Go 测试因本机无符号链接而 `t.Skipf`（环境限制，不计为通过）。

**两轴代码复核**（`cd8ca3c..69154c5`，15 票）：P1 0、P2 3、P3 8，全部在
`t/review-fixes` 修复并有回归测试（合并 `4fb29bf`，报告
`reports/review/REVIEW-FIXES.md`）：审批的冻结依赖复查改为 `FOR KEY SHARE` 锁带确定的
approve×revoke 线性化；真实 runner e2e 只对"无 WSL2/runsc"跳过、产品 `run_timeout` 一律失败；
PROGRESS 的 T073/T075 状态与 ADR 编号已对齐；删除 fan-out/copy 计数错误不再吞掉；诊断
内容门在读取时复检管理员角色；交接作用域先于存在性读取；目录分页 SQL 去重；0048 补零授权
REVOKE；0050 注释改指 ADR 0029。

**诚实边界（本批结束仍为 partial/not_run，均已在各票报告写明）**：
T073/T076 的 OTel/metrics/trace 导出（本构建无 SDK）；T075 的 F04A Vue 画布 `call_app`
调色板（票面允许 editor-surface 替代，`not_run`）；T078 对含子应用的**真实 WSL2/runsc 五类
评测报告**未跑（发布闭环用 T068 已验证的评审夹具）；各票浏览器 DOM 未驱动；全量
`pytest tests`（>60 分钟）未作为单次门禁运行，以逐套件证据为准。T069 的 conversation scope
由 T074 建表后闭环并测试。

## 复核后修复（post-review fixes，2026-11-05，分支 `t/post-review-fixes`）

独立复核在批次 tip `c755012` 上复现 **2 个 P1 + 3 个 P2**，本分支全部修复；每条先在
pre-fix `c755012`（detached worktree）上以回归测试复现失败，再修复并跑绿。无新迁移、未改
`docs/spec/**`/`contracts/**`、未改票状态（T068–T082 仍为各自已交付状态）。报告：
`reports/review/POST-REVIEW-FIXES.md`。

| # | 级别 | 复核发现 | 修复 | 回归测试 |
|---|---|---|---|---|
| 1 | P1 | 会话 DELETE 202 后正文 artifact 仍 200 | 会话 tombstone 同事务 tombstone 其 artifact 闭包（请求正文/附件、run input/output/`run_artifacts`、interaction、handoff 摘要）；live 共享引用不 tombstone | `tests/f08/test_t074_delete.py::test_deleted_conversation_body_is_unreadable_through_every_artifact_entry` |
| 2 | P1 | 清理 A 删掉 B 仍在用的共享附件 | `sharedArtifacts` 覆盖 request/run/`run_artifacts`/handoff/interaction 引用（引用方会话须 live），`object_shared_reference` business/non-retryable | `tests/f11/test_t071_conversation.py::test_a_shared_attachment_survives_another_conversations_delete_and_purge` |
| 3 | P2 | 撤销版本后同 key/body 幂等重放 409 `version_revoked` | receipt 读取先于版本/授权检查（会话 liveness 仍最前）；异体仍 409、新 key 仍拒、删除会话仍 404 | `tests/f08/test_t074_submit.py::test_a_committed_request_replays_after_its_version_is_revoked` |
| 4 | P2 | 重开会话不恢复交互面板/轮询、队列不可点 | 加载后恢复持槽请求的面板/候选并重新轮询；队列行可点切换且有取消按钮 | `tests/f08/test_postreview_pages.py::test_reopening_a_conversation_restores_the_pending_request_and_an_actionable_queue` |
| 5 | P2 | 缺输入答复不带已上传附件 | 答复携带页面已上传 artifact ids（服务端仍校验），交互面板加提示 | `tests/f08/test_postreview_pages.py::test_the_reply_carries_the_uploaded_missing_input_artifact` |

页面两条用 Node 24 + 最小 DOM shim 执行真实 `chat.js` 字节与真实事件处理（浏览器 DOM 仍
`not_run`）。门禁：`pytest tests/f11` / `tests/f08` / `tests/tooling`、5 条修复测试 ×2、
`go build/vet/test ./...`、`gofmt -l .`、`sync_spec --check`，原始输出见报告 §3。

## F08 冻结路由案例并由另一用户完成销售旅程（T084 / #85，2026-11-05）

- 状态：**passed**（真实 T068 发布 + 真实 supervisor 执行 + 独立真值 CSV 下载；
  真实模型基线与浏览器 DOM 仍 `not_run`）。报告
  `reports/f04b/T084-routing-eval.md` 与机器可读
  `reports/f04b/T084-routing-eval.json`（`corpus.dataset_digest=9128080a…`）。
- 语料：`tests/f08/routing/datasets/` 六单元格 34 例（frozen_test 25 / dev 9，
  18+8 个消息模板 family 按 split 隔离），`registry.json` 逐文件 sha256+size；
  失败即冻结漂移。附件只用 `docs/spec/examples/sales/input.csv`；provenance/license 已记录。
- 代码：`tests/f08/routing/{corpus.py,runner.py}`、`tests/f08/test_t084_{corpus,
  routing_eval,states,pages}.py`；`apps/web/src/assets/chat.js` 新增
  `REQUEST_FAILURE_REASONS`/`describeRequestFailure`/`applyRequestFailure` 并由
  `renderRequest` 渲染 `error_code`（稳定码保留 `data-error-code`），`chat.html` 增加
  `#chat-request-error`。
- 结果：frozen_test 25 例 decision 全对，误自动执行 **0/18**，澄清完成 **9/9**，
  候选召回 1.0，无候选泄漏；dev 9 例 0/7、4/4、1.0。跨用户旅程 Run owner=B、
  `summary.csv` 经 `t049.check_summary_csv`（Decimal 独立真值）通过；A 读 B 的
  Run/产物均 404，匿名 401。队列位置/按序推进、交互过期 409 与
  `failed/interaction_expired`、无匹配 `failed/no_matching_application` 均有真实 REST 断言。
- stub/real 分界：路由控制模型是 dev-stub（`stub:true`）；销售应用走真实
  `submit_publication`+`decided_review`（`publication.mode=t068_real`），其余 6 个
  catalog 应用是真实编译 manifest + 文档化 T077 版本行（JSON 逐项标注）。
- 边界/风险：聊天正文按原字节作为 published Run 输入，销售链 start 结点要求 JSON 对象，
  故旅程消息是 JSON 任务文档；自然语言消息会路由成功但执行 `node_input_invalid`
  （探针实测），属 F08↔F04B 集成缺口，未冒充通过。沙箱 heartbeat 抖动只做有界重试并逐次记录。
- 门禁：**最终合并树上的完整 `pytest tests/f08` 是 `not_run`**（两次最终树运行都在共享 WSL/runsc 状态根争用下
  被编排者终止，低 CPU 长时间等待，非代码失败）；实际证据是分套件结果：
  `test_t084_routing_eval.py` **1 passed in 84.91s**、`test_t084_pages.py` **2 passed in 10.34s**、
  `test_t084_corpus.py` + `test_t084_states.py` **7 passed in 46.93s**；第一次合并树（`08af658`）上的
  完整套件曾 **79 passed in 948.05s**。`go build/vet/test ./...`、`gofmt -l internal cmd tools`、
  `node --check apps/web/src/assets/chat.js`、`python tools/sync_spec.py --check` 全绿；未改
  `docs/spec/**` 与 `contracts/**`。
## F04B/T083 画布创作半：condition/join 配置与端口（GitHub #84，2026-11-05）

T083 的**画布创作半**已交付，状态 **passed**（运行视图/执行语义属其它子任务）。
分支 `t/t083-canvas`（基线 `integration/t083-t091` @ `ea56392`，HEAD `9fd08d2`；
结束前 merge 集成分支 `Already up to date`）。报告：`reports/f04b/T083-canvas.md`。

| 项 | 内容 |
|---|---|
| 节点/端口 | `canvas/src/lib/dsl.js`：六类 palette（condition/join 新增，python/agent/condition/join 可多个、start/end 唯一）；`defaultNode` 的 condition `{join_id,default_port,cases}` / join `{mode,fork_id,required_sources,optional_sources}`；`definitionToGraph` 为 condition 渲染命名 source handle（`cases[].port ∪ default_port`）并在边写 `sourceHandle/data.port/label`，join 单 target handle + 来源标签；`graphToDefinition` 保留 `edge.port`、按 id 深拷贝合并 config、拓扑序（不截断丢节点）、清理 join 悬空 sources |
| 连接规则 | 通用 DAG：只有 join 可多入（否则 `merge_without_join`）；condition 多出且同一 port 唯一（`bad_port`/`port_exists`/`no_free_port`，未指定时按 cases+default_port 自动选空闲端口）；普通节点多出必须由配对 join 的 `fork_id` 声明（`fork_requires_join`）；self/duplicate/cycle/end 无出/start 无入不变 |
| 本地规则 | `regionProblems(definition)` 替换线性链检查：按 Go 阶段 2–4 顺序镜像 duplicate_id/unknown_endpoint/start_end_count → cycle/unreachable/cannot_reach_end/merge_without_join → invalid_region（双向配对）→ bad_port → cross_region_edge（dom/pdom）→ bad_join_sources；返回 `{code,message,pointer,nodeId,edgeIndex}`（中文定位/修复提示），阶段 5 交服务端权威 |
| 来源 | `sources.js`：join 下游提供 `node:<join>#/results[/<source>[/value]]`；region 内分支节点不再直接暴露，join 自身不读分支输入；非支配上游不提供（避免 skipped 引用）；`sourceType` 支持封套路径 |
| 错误定位 | `errors.js`：`/edges/<i>` → `{edgeIndex,source,target,edgeId,nodeId=source}`（condition 出边 field=`edge.port`），`fixHintForCode` 覆盖 cross_region_edge/bad_port/bad_join_sources/merge_without_join/invalid_region 等中文修复提示；`main.js` 错误项显示 code+message+source→target+修复提示，点击高亮 `orchara-edge-invalid` 并滚动画布，节点错误聚焦 `data-field` |
| 面板 | `main.js`/`canvas.css`：palette 增 condition/join；condition 面板 `config.join_id`（同步 join.fork_id）/`config.default_port`/`config.cases.<i>.input|op|value|port`（增删/上下移/JSON value/端口预览）/输入映射；join 面板 `config.mode`（只读 all_active）/`config.fork_id`（同步 condition.join_id）/required+optional 多选互斥/覆盖提示；边选中编辑 `edge.port` |
| 导出 | `export.js` 正向 per-node-type config allowlist（含 cases 项白名单），未知类型/键丢弃并返回 `dropped[{path,reason}]`（干净导出仍精确 `{definition,layout}`）；secret/endpoint/run_input 仍 `ExportRefused` |
| 产物 | `apps/web/src/assets/canvas.js` SHA `4915DE69…56EB` → `618410A7…4D83`；`canvas.css` `167AAC85…C165` → `34BA780F…279B`。构建：`npm ci --offline`（added 43 packages）+ `npm run build`（751.2kb / 14.2kb）；`build.mjs` 加 `charset:'utf8'` 使提交产物中文可读 |
| 测试 | 新 `tests/f04b/canvas_f04b.test.mjs`（Node 执行真实 `lib/*.js`，fixture 全部为 P05 corpus：端口往返、连接规则、7 个合法 fixture `regionProblems=[]`、12 个非法 fixture code+pointer 与参考实现一致、sources join 封套、export allowlist、errors 边定位，`PASS 9`）；`test_canvas_f04b_assets.py`（bundle `node --check`、condition/join marker、依赖 pin、`PASS 9`、`node_modules` 存在时重建逐字节比较后还原）；`test_t083_canvas_validate.py`（真实 PG+API：condition/parallel/optional/nested validate `valid:true`+manifest hash；跨区 `/edges/10`、bad_port `/edges/1`、bad_join_sources `/nodes/4/config/required_sources` 定位） |
| 门禁 | `node --check` exit 0；`node tests/f04b/canvas_f04b.test.mjs` PASS 9；`pytest tests/f04b` **8 passed**（最终树 37.07s）；`pytest tests/f04a` **36 passed**（最终树 107.71s，P01 解释器用 `ORCHARA_P01_PYTHON` 指向主仓 venv）；`python tools/sync_spec.py --check` no drift。未改 Go/`internal`/`contracts`/`docs/spec`/`editor.js`/`debug.js`/`runs.js` |
| F04A 回归调整 | `tests/f04a/canvas_modules.test.mjs` 最小同步（仍 8 例）：`linearChainProblems`→`regionProblems`；连接码 `outgoing_exists/incoming_exists`→`merge_without_join`/`fork_requires_join`；同类型多节点允许；`/edges/<i>` 改为边级定位。F04A 其它页面/资产/HTTP 用例未改 |
| 边界/not_run | 浏览器 DOM 交互（拖拽/点击/焦点/高亮滚动）未驱动（测试栈无浏览器自动化，沿用 F04A 边界；`main.js` 由构建+`node --check`+marker+纯模块测试保证）；运行 F04B 图未跑（执行器当前拒绝 condition/join，属运行半）；本地规则只提示、不是安全边界；导出白名单仅覆盖本票六类节点（其余 F04B 类型由 T091 扩展，当前丢弃并报告）；未新增 fetch surface/未改 OpenAPI；未加 `editor.html` 的 run 挂载点 |

## F04B/T083 运行时半：条件分支与 join-once（2026-11-05）

T083（GitHub #84）运行时半已交付，状态 **`passed`**（画布/UI 半由另一 worktree 交付，合并后 T083 才完整）。证据：
`reports/f04b/T083-condition-branching.md`；分支 `t/t083-graph-runtime`（worktree `E:\Orchara-wt\t083`）。

| 交付点 | 结果 |
|---|---|
| graph 计划/调度器 | **passed**：`internal/presetexec/{graphplan,graphrun,condition}.go`；含 condition/join 的定义走新调度器，legacy/linear 选择对其它定义不变 |
| 激活/skipped/join-once | **passed**：真实 PG+mTLS+supervisor+runsc 下条件 true/default、并行 fork、嵌套 condition；未选分支只发 `skipped`；join 每次 region 激活恰一次（1 条 data + 1 次 succeeded） |
| 非法跨区/无激活来源 | **passed**：`POST /validate` 200 + `cross_region_edge` + `pointer=/edges/10` + `node_id`/`source->target`；debug 提交 422 同定位、零 Run/零 job；运行期 required 源全 skipped → join/run `invalid_activation` |
| run view 数据（非契约） | **passed**：condition data=`{"port","matched_case"}`、join data=结果封套、node.status 拓扑序；owner-only SSE 重放与 DB `run_events` 一致；不新增契约端点 |
| 恢复接缝 | **passed（第三轮补齐真实重放）**：`presetexec.Deps.NodeOutputs` 接口 + `cmd/supervisor` 读取 `run_node_outputs(kind='data')`/对象根/复算 sha256 + Go 复用单测；真实“同一 run 第二次 exec”由 T087 `test_a_real_resume_reuses_the_join_and_never_fires_it_twice` 覆盖 |
| Python LangGraph 证明 | **passed**：真实 `langgraph 1.1.6`，激活集合、skipped 集合、join 调用次数=1、未选分支不进屏障（超时保护） |

门禁（原始日志 `.scratch/t083-t091/T083/`）：`python -m pytest tests/f04b -q` **14 passed in 110.23s**；
`pytest tests/f04a -q` **36 passed in 85.31s**（另一次未设 `ORCHARA_P01_PYTHON` 的运行是 29 passed + 7 errors，
不计为通过）；`pytest tests/tooling -q` **21 passed**；`go build/vet/test ./...` exit 0；
`python tools/sync_spec.py --check` **no drift**；全量 `pytest tests/f06` **not_run**（日志只有 12 个点，
被共享栈争用打断、无 summary）；该次 `gofmt -l` 原始日志列出 4 个文件（`agent.go`/`agentcontext.go`/
`callapp.go`/`graphplan_test.go`），后续提交已格式化，批次最终树 `gofmt -l` 为空。无迁移；
未改 `docs/spec/**`、`contracts/**`。
**not_run**：真实二次 exec 复用观感烟测、真实厂商模型、浏览器 DOM、画布半。

## F04B/T083 运行视图半：调试页节点状态表（condition 端口 / skipped / join）（2026-11-05）

T083（GitHub #84）运行视图半已交付，状态 **`passed`**：页面、纯函数、页面结构与 DOM 测试、真实线性 Run 的
HTTP E2E、以及合并运行时后的**真实条件/join/skipped 执行 E2E** 全部通过（`pytest tests/f04b/test_t083_runview.py`
**7 passed in 42.69s**，全局 pytest 锁下真实 PG/API/mTLS/supervisor/runsc）。证据：`reports/f04b/T083-runview.md`；
分支 `t/t083-runview`（worktree `E:\Orchara-wt\t083-runview`），基线 `ea56392`，结束合并集成分支 `111e2c2`
→ 合并提交 **`a050e2f`**，随后 fast-forward 到 `491cb78`（含 run-view 合并 `c21d45e` 与 T084）。

| 交付点 | 结果 |
|---|---|
| 节点状态表 | **passed**：`apps/web/src/assets/runview.js` 新纯函数 `aggregateNodeStates`（attempt 优先 + 状态秩、乱序/重放幂等、skipped 独立）/`buildRunViewModel`（拓扑序、每节点一行、attempt、错误码、定义外节点追加）；`debug.html` 表格 6 列 |
| condition 端口 / join 信封 | **passed**：条件 data artifact `{port,matched_case}` → “端口 yes（第 1 个 case/默认分支）”；join 信封按 required+optional 顺序显示 `source=status/value 摘要(<details>)/error_code`；**真实条件 Run E2E**：`port=yes/matched_case=0`、`left:succeeded(value)/right:skipped`、`left=成功`/`right=已跳过`、join `succeeded` 恰一次 |
| skipped 视觉 | **passed**：`state-skipped`（灰/⏭/降透明）vs `state-failed`（红/✕/加粗）+ 图例；**真实 E2E** 的 owner-only SSE 回放中 `right=skipped` 且 `left` 无 skipped |
| run 失败/取消 | **passed**：`formatRunError`（state/稳定码/severity/已知码提示；202 永不写成 stopped）；取消按钮 `POST /runs/{id}/cancel` + CSRF，DOM harness 断言 202 意图文案与终态禁用；`/runs` 链接 |
| 断线/410 | **partial**：复用既有 `runs.js` SSE（`Last-Event-ID` 续传/去重/410 snapshot 文案），仅加可选 `onEvent` 钩子；事件缺失时表格用持久 node-results 并标注“由节点结果推断/无状态事件”，不伪造历史；真实 410 过期 E2E `not_run` |
| 数据面 | **非契约**：`node.status` 契约事件 + condition/join **node data artifact** 约定；读取链 `node-results`(非契约) → `artifacts/{id}/content`(契约)。未改 OpenAPI/`contracts/**`/`docs/spec/**`；新增仅 `GET /assets/runview.js` 静态路由 |

门禁：`node --check`（debug/runview/runs）exit 0；`node tests/f04b/runview.test.mjs` **PASS 6**；
合并前 `pytest tests/f04b` **6 passed, 1 skipped in 46.34s**（含真实线性 start→python→end Run 的 HTTP E2E；
1 skip = 条件 E2E，当时运行时未合入）；**合并后**（`491cb78`，全局 pytest 锁）
`pytest tests/f04b/test_t083_runview.py -q -rs` **7 passed in 42.69s**（含真实条件/join/skipped E2E）；
`sync_spec --check` no drift；`go build ./...`、`go test ./internal/api`、`gofmt -l internal cmd tools` 干净；
相关页面回归 `tests/f06/test_t044_pages.py` 4 passed、`tests/f03` 三个页面模块 14 passed。
**not_run**：真实 410 过期、真实取消 E2E（T036 已覆盖）、浏览器 DOM/Playwright（P2，非门禁）、
真实失败节点经 run-view 的 E2E、`pytest tests/f02a` 全量（并发争用下未取得可信结果）。

## F04B/T085 并行嵌套与分支失败（GitHub #86，2026-11-06）

T085（GitHub #86）已交付，状态 **`passed`**（`go test -race` 因本机无 cgo C 编译器 `not_run`；真实 resume join-once 与 optional 发布校验由第三轮复核补齐，见 `reports/review/REVIEW3-FIXES.md`）。
证据：`reports/f04b/T085-parallel-optional.md`；分支 `t/t085-parallel-optional`（worktree `E:\Orchara-wt\t085`），基线
`integration/t083-t091` @ `111e2c2`，结束合并 `15db973`（T087/T088/T089 + 集成方对并发调度器的适配，merge `723760b`）；
功能提交 `71213f0`、`3a98b3a`、`5935afd`、`78d09db`、`9a302f2`（skip 事实每次 attempt 只发布一次，waiting hand-over 不发布）。无迁移；未改 `docs/spec/**`、`contracts/**`。

| 交付点 | 结果 |
|---|---|
| 并发调度 | **passed**：`internal/presetexec/graphrun.go` 改为 ready 队列：所有前驱 settled 且可运行的节点各自一个 goroutine；结果经 channel 汇总；`graphRun` 单所有权、节点拿不可变快照。Go 单测用阻塞 barrier sandbox 证明两分支必须同时在飞（顺序执行必超时失败）；每节点独立 runsc unit（`graphSandboxUnit`）使真实栈两个 sandbox 真并发 |
| optional 收敛 | **passed**：显式 allowlist（节点类型 `python/condition/join`（`retrieve` 预留）+ 显式错误码集合）；optional 分支的纯读/计算失败 → join 信封 `{status:"failed",error_code}`（无 value），join 仍执行一次、run 继续、end 正常封输出（错误作为数据） |
| required fail-fast | **passed**：失败 → `cancel()` 其他在飞分支 → `drain()` 等所有节点 goroutine 返回（含 sandbox 清理）→ 未启动节点 emit `skipped` → 只提交一次 failed 结果；join 不执行、end skipped、无 final output；Go 单测用 client 钩子断言"分支停止后才提交失败" |
| 不吞越权/未知写 | **passed**：agent/call_app、能力/权限、预算、审批、`needs_reconciliation`、`sandbox_*` 等一律不降级。真实栈：optional agent + publish 后吊销 golden model profile → run failed `model_profile_unavailable`、join/end skipped；Go 单测另覆盖 `sandbox_orphan` 不被吸收 |
| 嵌套并行+条件 | **passed**：Go 单测 condition 内嵌 parallel region；真实栈 `choose→fork→deepA/deepB→join2→join`，两 join 各一次、15 条 node-results、外层 value=内层封套、`direct` skipped |
| optional 发布校验 | **passed（第三轮复核补齐）**：Go 生产校验器与 P05 Go/Python 校验器拒绝非 `python/condition/join/retrieve` 的 `optional_sources`（`bad_join_sources`，定位 `/nodes/<i>/config/optional_sources`）；P05 语料 43 例（model/agent/call_app 负例）；真实 `POST /validate` + debug 422 且零 Run/Job |
| join-once / replay | **passed（Go 单测 + 真实两次 exec）**：fake `NodeOutputSource` 回放仍为快速单测；真实 T087 `test_a_real_resume_reuses_the_join_and_never_fires_it_twice` 第一次 exec 发布 1 条 join data，第二次 exec 复用同一 artifact、join 每 attempt 至多一次 succeeded |
| 运行视图 / 画布提示（P1） | **passed**：run view 把 optional failed 显示为「已收敛的错误」（`state-converged`，与 failed 区分）+ 图例；画布 join 面板逐个标记 optional、列出已选 optional 并提示"只有纯读/计算错误会收敛；写/越权/预算/审批失败不降级"；`npm ci --offline && npm run build` 重建并提交 canvas bundle |

门禁（最终树、全局 pytest 锁）：`pytest tests/f04b` **43 passed in 239.44s**（含 T086 合并；本票 5 例、T083 全回归、
T086 model-node 9 例；`test_t085_parallel_optional.py` 单跑 **5 passed in 54.34s**）；合并 T087/T088/T089 后定向复跑
**30 passed / 1 failed in 376.48s**（`test_t085_parallel_optional`、`test_t083_condition_runtime`、`test_t087_approval_node`、
`test_t089_callapp`、`test_t086_model_node`；唯一失败是 T089 的双 call_app 子调用创建顺序断言，并发下非契约顺序）；
`pytest tests/f05` **149 passed**（sandbox 修复回归）；`pytest tests/tooling` **21 passed**；
`pytest tests/f06` **172 passed / 1 failed**（T047 的 sandbox 启动失败被旧 executor 竞态伪装成租约丢失，修复后该用例单测重跑
**1 passed**，见报告 §6.1）；`go build/vet/test ./...` 全绿；`gofmt -l internal cmd tools` 空；`sync_spec --check` no drift；
`go test -race ./internal/presetexec` **not_run**（`cgo: C compiler "gcc" not found`，原始输出见报告 §6）。
附带修复：`internal/sandbox/executor.go`（`78d09db`）不再把"进程退出后自己的取消失败"记成租约丢失，
真实启动失败保持原错误类型并提供确定性 Go 回归测试（T083/T085 真实栈偶发失败的根因）。
**not_run**：race detector、浏览器 DOM/Playwright、真实厂商模型。真实 resume join-once 与 optional 发布校验已由第三轮复核补齐（`REVIEW3-FIXES.md`）。

## F14/T090 冻结动态候选路由的基线（dev-stub 真实控制面）（2026-11-06）

T090（GitHub #91，F14）已交付全部**不依赖模型**的必交项，状态 **`blocked`**：本机无模型
凭据/端点/权重、无训练 GPU，同基模强提示基线与任何训练不可运行；不声称训练已运行。
证据：`reports/f14/T090-routing-baseline.md` / `.json`、`experiments/routing_sft/freeze/
T090-freeze.{json,md}`；分支 `t/t090-routing-baseline`（worktree `E:\Orchara-wt\t090`），
基线 `0deeab9`，merge `integration/t083-t091`（`491cb78`）→ 合并提交 `1d94984`。

| 项 | 内容 |
|---|---|
| 冻结清单 | 复用 T084 语料不复制：`registry_sha256=491e4f25…`、`dataset_digest=9128080a…`、逐文件 sha256；held-out=frozen_test 25 例（18 family，禁止调参）、dev 9 例仅观察；显式 `retrieval.not_used=true`（无 KB/embedding，不伪造 revision）；dev-stub 逐处标注；环境 git head/Python 3.12.2/Go 1.27.3/OS + 运行时 PG 16.4 |
| 目录卡 | 冻结两套：T084 实际目录（sales 卡=`f02a.full_card()`，purpose `summarise sales`）与产品模板卡探针（`templates.CapabilityCard`）；逐卡 sha256、`definitions_sha256`；版本 UUID/manifest sha 由真实编译器生成，记在基线 JSON |
| 泄漏检查 | 独立检查器 `tests/f14/routing_freeze.py`：26 family 不跨 split/单元格；跨 split 225 对消息完全/规范化重复 0；近重复阈值 0.75、实测最大 0.5、0 对；34/34 `self_created`+`not_applicable`，附件为公开样例逐字节副本 |
| 两臂基线 | 复用 T084 runner + `t079` 真实 REST/maintenance。臂 A（复现 T084 目录）frozen 25/25、dev 9/9，accuracy 1.0、误自动执行 0、澄清 13/13、候选召回 1.0，`t084_comparison.identical=true`；臂 B（仅换产品模板 sales 卡）frozen 21/25、dev 8/9，5 个错误全部 retrieval 类（4 例工资单意图被“生成”2-gram 过度召回为 sales，1 例模板卡缺 `aggregation` 未召回），无 false auto、`payroll` 版本零泄漏 |
| 错误分类/stop-go | `model_semantic=not_measured`（无模型）；`stop_training_fix_the_measured_layers_first`（provisional），依据 `TRAINING-EXPERIMENT.md:40/:48`、`ROUTING-MEMORY-KNOWLEDGE.md:49`；未虚增语料规模（25 < EVALUATION 建议 100，明写） |
| 门禁 | `python -m pytest tests/f14 -q` **13 passed in 73.36s**（12 冻结/泄漏 + 1 真实栈两臂基线）；merge `integration/t083-t091`（`b343f7b` → `9c062ec`）后复跑 **13 passed in 76.66s**；`pytest tests/f08/test_t084_corpus.py -q` **4 passed**（T084 语料未破坏）；`python tools/sync_spec.py --check` **no drift**；全量 `pytest tests/f08` **not_run**（未改产品代码/T084 语料，共享 runsc 栈被其它集成任务长期占用）；无 Go/契约/spec/迁移改动 |
| 边界/not_run | 同基模强提示基线 `blocked`（无凭据/端点/权重/预算）；规则 vs 强提示比较 `not_run`；训练 `not_run`（无 GPU/基模）；T068 真实发布未重跑（T084 证据）；全量 `pytest tests/f08` 与浏览器 DOM、sandbox 执行 `not_run/not_used` |

## 记录模板

```text
task_id / status:
code_commit / files:
inputs / versions / environment:
commands + raw report paths:
observed result (stub / real 分开):
remaining blockers + independent next tasks:
spec changes + reason:
```

## F04B/T086 模型节点：单次受控生成与结构输出校验（2026-11-06）

T086（GitHub #87）已交付，状态 **`passed`**（运行时 + 画布）。证据：`reports/f04b/T086-model-node.md`；分支 `t/t086-model-node`（worktree `E:\Orchara-wt\t086`）。

| 交付点 | 结果 |
|---|---|
| graph 路径 model 节点 | **passed**：`hasGraphConstructs` 纳入 `model`；`graphNodeTypes` + `parseModelConfig` 复验闭集 config（多键/缺键/非 UUID/非法 response_mode → `bad_config`）；移除 `node_unsupported_model` |
| 一次受控生成 | **passed**：一次 `InvokeCapability(kind=model)`（`task=model`、`messages`，json 模式 `response_schema=node.output_schema`），无 tools/循环/隐藏重试；真实栈 `platform.operations` 恰 1 条、`operation_attempts` 恰 1 条 |
| prompt 严格 `{{name}}` | **passed**：只替换已绑定输入；属性链/表达式/未绑定/不配对 → `node_prompt_invalid` 定位 `/nodes/<i>/config/prompt_template`，零 operation；真实栈证明 run 的其它授权输入不进入 prompt |
| 输出与 Schema 失败独立 | **passed**：text `{text}` / json `structured` 都由平台按 `node.output_schema` 复验；失败 → `node_output_invalid`、无 data artifact、end skipped、run failed、不重试 |
| 固定 profile | **passed**：执行前 `Facts.ModelProfile` 复验 passed/未撤销（`model_profile_unavailable`，零 operation）；真实栈中途撤销 profile 用例通过；已撤销 profile 的新草稿在 validate/debug 提交被 `dependency_revoked`/422 拒绝 |
| 预算/usage | **passed（ledger）**：`usage_ledger` 三行（reserve/release/charge，零价 dev stub 金额 0）；`usage.updated` SSE 事件本构建无 emitter → `not_run` |
| 恢复接缝 | **passed（接口+Go 单测）**：已完成 model 节点 data 复用、0 次 capability 调用；真实 resume 场景留 T087/T091 |
| 画布 | **passed**：`model` palette/panel（固定 profile、prompt 编辑+插入变量、response_mode、响应 Schema=output_schema）、导出白名单 `model_profile_version,prompt_template,response_mode`；bundle 已重建提交 |
| 回归/门禁 | `pytest tests/f04b/test_t086_model_node.py tests/f04b/test_t086_model_canvas.py` **9 passed in 44.74s**；`pytest tests/f06/test_t041_capabilities.py tests/f06/test_t043_agent_node.py` **18 passed in 127.72s**；`pytest tests/f04a/test_canvas_assets.py tests/f04b/test_t086_model_canvas.py` **9 passed in 1.06s**；`go build/vet/test ./...` exit 0；`gofmt -l internal cmd tools` 空；`python tools/sync_spec.py --check` no drift；全量 `pytest tests/f04b`/`tests/f06` 本票 **not_run**（共享栈争用，见报告 §6） |

无迁移；未改 `docs/spec/**`、`contracts/**`。**not_run**：真实厂商模型、`usage.updated` 事件、T087 审批拒绝/过期、真实二次 exec resume、浏览器 DOM。

## F04B/T087 人工审批节点：服务端业务审批与重启后继续原决定（2026-11-06）

T087（GitHub #88）已交付，状态 **`passed`**（服务端 + 等待桥 + 执行器 + 画布 + 真实栈均已交付并通过；
票面“审批过期后终止”曾因没有自动过期 sweeper 为 partial，第三轮复核已补 API 后台 sweeper 与真实等待
自动过期用例，见 `reports/review/REVIEW3-FIXES.md`）。
证据：`reports/f04b/T087-approval-node.md`；分支 `t/t087-approval-node`（worktree `E:\Orchara-wt\t087`），
基线 `integration/t083-t091` @ `491cb78`，结束前 merge `f21f09a` → 合并提交 `b086419`（+ 文档提交）。

| 交付点 | 结果 |
|---|---|
| 节点审批 = 业务决定 | **passed**：`platform.approvals` 行 `operation_id NULL`、`node_key="node:<id>"`、approver=run owner；无 `platform.operations`/capability grant；后续真实 write-risk connector 仍 `awaiting_approval` 并创建自己的 operation 审批 |
| 服务端提案 | **passed**：`internal/approvals/node.go` 从 sealed manifest（逐字节复算 hash）+ run/snapshot 事实构建 JCS proposal 文档，`params_sha256=sha256(JCS(proposal))`；title/expires(60..86400)/proposal 对象与尺寸校验；内部路由 `POST /internal/v1/runs/{id}/node-approvals`（worker mTLS） |
| 幂等/重放 | **passed**：同一 canonical proposal 重放返回原行（同 attempt 与恢复后的第二次 exec 均验证）；approvals 恰 1 行、`approval.required` 恰 1 条、resume job 恰 1 个；不同 proposal = 新 approval 行，旧决定不复用（旧 hash 决定新行 409 `approval_params_mismatch`） |
| 等待桥 | **passed**：`approval:<approval_id>` checkpoint 的 adopt 分支与既有 `capability:<operation_id>` 分支并列，capability 行为未变；rejected/expired → Run `approval_rejected`/`approval_expired`，无 resume；resume 仍由唯一 `maybeEnqueueResumeTx` 创建 |
| 执行器 | **passed**：graph `approval` 类型/触发/config 复验/`proposal` 输入；pending → `waiting_approval` + `approval:<id>` + ConfirmExit 不继续；approved → data artifact `{approval_id,decision,decided_at,params_sha256,proposal}` 并继续；invalidated/拒绝/过期稳定码；真实二次 exec 同一 Run 恢复成功 |
| 两次审批（T091 预演） | **passed**：`start→a1→a2→end` 两次等待/两次决定/第三次 exec 汇合；2 条独立 approval、2 个 resume job、a1/a2 各 1 条 data artifact（a1 由恢复接缝复用）、最终输出含两 id |
| 审批过期（票面“过期后终止”） | **passed（第三轮复核补齐）**：① 决定路径：`expires_at` 置过去后再 decide → 409 `approval_expired`、approval `expired`、Run failed、无 resume/output。② 自动路径：真实 supervisor 停泊后不调用任何 decision，API sweeper 到期把 approval 置 `expired`、Run 置 failed `approval_expired`、waiting decision `expired`、无 resume/output（`test_a_parked_approval_expires_automatically_without_a_decision`）。 |
| 画布 | **passed**：`approval` palette/panel（title、expires_seconds、proposal 来源绑定/JSON 字面量编辑器、“审批不授权后续外部写”说明）、export allowlist `title,expires_seconds`；bundle 已重建提交 |
| 审批页 | **passed**：`approvals.js` 识别 `kind=node_approval`，经既有 artifact-content ACL 显示真实 title/节点/来源/proposal；capability 提案渲染不变（T050 页面测试通过） |
| 真实栈 | **passed**：`tests/f04b/test_t087_approval_node.py` 原 7 用例 + 第三轮/复审 5 例（自动过期、sweeper/decision 两条故障回滚重试、真实 join 重放等，现全文件 **12 passed in 105.43s**）+ `test_t087_canvas.py` 2 用例；合并前 `pytest tests/f04b -q` **38 passed in 153.16s**；本票原 9 passed in 57.79s |
| 回归子集 | **passed**：`pytest tests/f06/test_t050_approval.py tests/f06/test_t050_pages.py tests/f06/test_t043_agent_node.py -q` **27 passed in 178.74s**（首跑 1 个 sandbox lease-lost 环境 flake，隔离重跑通过后整套复跑通过） |
| Go/画布门禁 | **passed**：合并后 `go build/vet/test ./...`、`gofmt -l`、`sync_spec --check` 全部干净；3 个 Node 画布模块测试 + 11 个画布 pytest 通过；合并后真实栈关键 3 用例（服务端幂等/批准恢复/两次审批）**3 passed in 58.54s** |
| 既有测试缺陷 | **修复（2 行，测试代码）**：`tests/f04b/test_t083_runview.py` 条件/join 真实栈用例在 `491cb78` 上把 items 列表当 body 使用（该用例在 graph runtime 合并前一直 skip，从未跑过）；修复后 7 passed，产品断言不变 |
| 边界/not_run | 全量 `pytest tests/f06` 未重跑（共享 WSL/runsc 栈争用；审批/等待子集 27 passed）；自动过期 sweeper 已由第三轮复核补齐（真实等待用例 + Go 单测）；真实浏览器 DOM `not_run`；审批后写工具的 unknown/reconciliation 归 T051；真实厂商模型不涉及 |

无迁移；未改 `docs/spec/**`、`contracts/**`（`sync_spec --check` no drift）。

## F04B/T089 画布调用固定子应用并映射结果（GitHub #90，2026-11-06）

T089 已交付，状态 **`passed`**（画布 call_app 节点/面板 + graph 路径真实栈 E2E；无 Go 运行时改动）。证据：`reports/f04b/T089-callapp-node.md`；分支 `t/t089-callapp-node`（worktree `E:\Orchara-wt\t089`，基线 `b343f7b`，结束前合入 `4851c4f`）。

| 交付点 | 结果 |
|---|---|
| 画布 call_app | **passed**：palette `call_app` + `defaultNode={config:{application_version:''}}`；面板只列 ACL-filtered 应用与已发布版本（UUID 过滤，无 latest），显示 version id/manifest sha256，按子版本冻结 `input_schema` 逐字段映射（literal/input/node），子 `output_schema` 只读、父输出可编辑并默认取子 schema；本地提示缺失/未知字段/类型不符/非法来源；`lib/callapp.js` 纯逻辑（editor sidebar 纯逻辑的复制，经典脚本不能 import ESM；editor.js 未改）；`export.js` allowlist `call_app:['application_version']` |
| 单 child 等待/恢复 | **passed**：`waiting_children` + confirm exit → child 独立 Run（独立 snapshot/thread）→ 唯一 resume → 父 succeeded，父节点 data=子结构化结果（label `child-run:<id>`），`GET /runs/{id}/children` 1 条，无第二 child |
| 并行/顺序同 child | **passed**：两个 child Run、不同 invocation key（`call_app:<node id>`）/thread/snapshot/结果；同版本顺序两次调用不按 app id 去重；join 两源独立 |
| 执行槽交接 | **passed**：等待 exit 后 `global/execution_slots` 释放（used=0）；child 只占独立 child pool（global+parent）；resume 重新占用 1 槽、终态释放一次；`assert_slot_consistent` |
| 失败/映射定位 | **passed**：required 子失败传播 child 稳定码（`child_output_schema_invalid`）；`io_mapping_type_mismatch` `/nodes/2/inputs/source`、`io_mapping_missing_field` `/nodes/{2,3}/inputs/region`、debug 422；执行器运行期绑定 `node_input_invalid`（report 定位 `child.source`） |
| 测试 | 新增 `tests/f04b/callapp_canvas.test.mjs`（12 Node 用例）、`test_t089_callapp_canvas.py`（4 passed）、`test_t089_callapp.py`（真实栈 7 passed）、`tests/support/t089.py`；定向回归 T078+T083 `15 passed` |
| 合并/门禁 | merge `f21f09a`（T086/T090）→ 画布冲突解决 + 从合并源重建 bundle；merge `aced554`（T087/T088）→ 画布 23 passed（含逐字节重建校验）；merge `4851c4f`（T085 并发调度器/optional）→ 并发语义下重跑本票真实栈 **7 passed in 110.27s**；`go build/vet/test ./...`、`gofmt -l`（空）、`sync_spec --check` no drift |
| 边界 | 浏览器 DOM `not_run`；call_app 无 doc/span 引用 → `not_applicable`；`spawnChild` 无 required/optional 字段 → optional 子失败 `not_applicable`；真实厂商模型 `not_run` |

发现（供后续票）：恢复接缝真实生效要求 supervisor 配置 `ORCHARA_OBJECT_DIR`（sealed object root）；T033 helper 未设置会让 resume 静默退化为重执行（T089 helper 已显式传入，建议补进共享 helper）。合并 T085 后 graph 调度器为 ready-queue 并发：fork 的两个 call_app 分支同时启动，首个 branch 等待时调度器取消其余在飞分支并在 drain 后提交 waiting_children；被取消的兄弟分支在 resume 后重执行（spawn 幂等键保证不产生重复 child），两个 child Run 仍完全独立。
## F04B/T088 知识检索节点：平台 ACL 检索与真实引用（GitHub #89，2026-11-06）

T088（GitHub #89）已交付，状态 **`passed`**（运行时 retrieve + 内部 ACL 检索路由 + 画布面板 + 运行视图引用链接）。
证据：`reports/f04b/T088-retrieve-node.md`；分支 `t/t088-retrieve-node`（worktree `E:\Orchara-wt\t088`），
基线 `111e2c2`，merge 集成分支 `b343f7b`/`f21f09a`/`4851c4f`（T085 并发调度器、T086/T087/T089/T090）后在本分支提交最终报告。

| 交付点 | 结果 |
|---|---|
| 内部检索路由（非契约） | **passed**：`POST /internal/v1/runs/{run_id}/retrieval`（worker mTLS，`internal/api/retrieval.go`）；attempt_token 复证 live attempt，owner 取 `runs.owner_id`（body 无 owner）；对每个 KB 调既有 `knowledge.Service.Query`（SQL 先 ACL/tombstone/active index/embedding revision 过滤），非 owner/不存在静默排除并计数 `requested/authorized`；多 KB 合并 score DESC + `chunk_id` 稳定 tie-break；响应仅平台字段；400/422/409/503 复用既有错误词汇 |
| retrieve 执行器 | **passed**：`internal/presetexec/retrieve.go`；`graphNodeTypes`/`hasGraphConstructs`/dispatch 最小扩展；config 复验（uuid 数组 min1 unique、top_k 1..20、拒未知键/内联 query）；query 必须绑定为 string，缺失/非 string → `node_input_invalid` 定位 `/nodes/<i>/inputs/query`；输出 `{items,citations,count}`，`span`/`citation` 原样；**输出已收窄（批次复核修复）**：items 为 `{chunk_id,document_id,document_revision,document_state,chunk_no,span,excerpt,citation}`、citations 为 `{chunk_id,document_id,document_revision,span,excerpt}`，`excerpt` 是 chunk 正文前 500 字符，**不含完整正文、不含向量 score**（R3/N-R10；正文只能经 owner-only citation 路由读）；无结果 `succeeded`；稳定码 `retrieve_unavailable`（503/网络）、`retrieve_denied`（调用被拒 403）、`node_output_invalid` |
| 真实栈验收 | **passed**：`pytest tests/f04b/test_t088_retrieve.py` **6 passed in 51.63s**（T086/T090 合并树）+ **6 passed in 78.47s**（T085 并发调度器/T087/T089 合并树）+ **6 passed in 74.49s**（最终树 `8a13ee2`，再并入 skipped-facts 修复；全局锁、真实 PG+mTLS+supervisor+真实知识摄取）；引用与 DB chunk/citation 逐字段相等、citation 路由同源、空结果、foreign KB 静默排除 + requested/authorized 计数、撤权后 404、并行 retrieve 不串 |
| 画布 | **passed**：`retrieve` palette「知识检索」、`{knowledge_base_ids:[],top_k:5}` 默认、KB 多选（`GET /knowledge-bases`，不可见已选保留+说明）、top_k 1..20、query 合法 string 来源绑定、`items/citations/count` 输出模板一键填入、导出 allowlist `knowledge_base_ids,top_k`；bundle `npm run build` 重建提交 |
| 运行视图 | **passed**：retrieve data artifact 预取；行内展示「检索 N 段 · 引用 M 条」，citation 链接 `GET /api/v1/knowledge-citations/{chunk_id}`；200 展开真实来源，404 显示不可用且不显示缓存正文；`retrieve_unavailable`/`retrieve_denied` 稳定码提示 |
| 门禁 | `pytest tests/f04b/test_t088_retrieve.py` 6 passed（最终树复跑）；`test_t083_runview.py` 7 passed（T086/T090 合并树）；canvas/runview/asset 模块 15 passed + 最终树 11 passed；`go build/vet/test ./...`、`gofmt -l internal cmd tools`、`python tools/sync_spec.py --check`（no drift）全部通过 |
| 边界/not_run | `not_applicable`：相同 child 并发不串 thread（retrieve 无 child，改写为并行 retrieve 不串结果并已真实栈验证）、节点等待交接释放槽（retrieve 不等待）；`not_run`：`pytest tests/f10` 全量（共享真实栈串行，最后窗口未取得；本票仅新增 `QueryBases`、不改知识 service/API 路由）、真实厂商 embedding、真实二次 exec 复用 retrieve 输出、浏览器 DOM/Playwright |

无迁移；未改 `docs/spec/**`、`contracts/**`。首轮真实栈曾用 `t055_stack`（缺 T025 编译器环境）出现
`compiler_unconfigured`/helper KB-id 拆分 bug，修正为 `t033_stack` + helper 归一化后 6 passed（报告 §5 如实记录）。

## F04B/T091 十节点工作流：保存/恢复、两次审批与嵌套汇合（GitHub #92，2026-11-06）

T091 已交付，状态 **`passed`**（十类画布/导出 + 十类 fixture 保存/校验 + 真实两次审批嵌套汇合 + optional/required/未知不降级）。
证据：`reports/f04b/T091-ten-node-workflow.md`；分支 `t/t091-ten-node-graph`（worktree `E:\Orchara-wt\t091`），
基线 `integration/t083-t091` @ `15db973`，结束前 merge 集成分支 @ `bd4d791`（含 T085 skip 政策 `9a302f2`、T088 收尾）。

| 交付点 | 结果 |
|---|---|
| 十类 palette/面板/allowlist | **passed**：`NODE_TYPES`/`CONFIG_ALLOWLIST` 十类；`ten_node_canvas.test.mjs` 12 用例整组钉住 palette、每类默认 config 与契约白名单、真实 fixture 逐字节 graph↔definition 往返、来源选择器（join 按来源/retrieve 数组/call_app 映射/approval proposal）、导出 allowlist+dropped+secret 拒绝、十类错误定位；画布源码无需改，重建 bundle 逐字节一致 |
| 十类 fixture 保存/校验 | **passed**：`tests/f04b/fixtures/ten_node_definition.json`（13 节点/十类/condition 内嵌 parallel fork+join/条件端口/join-by-source/固定 model+tool+child+KB）；真实 `PUT /draft`（保存不是校验门）→ GET 结构相等且读取/再保存逐字节稳定 → `POST /validate` valid:true + `X-Manifest-SHA256` → debug 提交冻结 manifest（十类/端口/`subapp_versions=[child]`） |
| 非法变体定位 | **passed**：跨区边/坏端口/坏 join sources/type_mismatch/cycle/注入未展示能力键（`response_schema`+`api_key`）/未知节点类型 → `valid:false` 精确 pointer；跨区变体真实 debug 422 `validation_failed` + pointer、零 Run/零 Job |
| 两次审批 + 嵌套汇合（真实栈） | **passed**：exec1 waiting（approve1 pending、无 resume）→ 决定 → exec2 新进程跑到 approve2 waiting（join2/join1 各一次）→ 决定 → exec3 succeeded；两个 approval/两个 resume、每节点 data 恰 1 条、join 每 attempt 至多一次 succeeded、capability 操作 2 条各 1 attempt、预算不重置；end 按来源读 join 封套；SSE 重连/node-results 重建 run view 数据面 |
| optional/required/unknown | **passed**：optional py `node_output_invalid` 收敛进 join 封套（run succeeded、end 把失败当数据）；required py 失败 fail-fast（run failed、join/end skipped、无 join 行、无第二审批）；optional agent 撤销 profile `model_profile_unavailable` 不降级（run failed） |
| 保存/恢复 | **passed**：run 使用冻结 snapshot/manifest；草稿被替换后 resume 仍执行原两次审批图；run snapshot 与 manifest hash 不变 |
| 集成对齐（T087 wait skip） | 旧基线 `15db973` 上 T087 两审批因 T085 wait-skip 变红；集成分支 `9a302f2` 已修（waiting 不发 skip、failed/cancelled 终态前按拓扑发一次）。本票 merge 后采用上游实现，不重复改 `graphrun.go`，仅新增 Go 回归 `TestGraphRunWaitingAttemptDoesNotReportFutureNodesAsSkipped` |
| run view（P1） | **passed**：`runview.js` 审批行显示「等待人工审批」/「审批已通过 · id · decided_at」，approval data artifact 纳入预取；`runview_t091.test.mjs` PASS 4 |
| 门禁 | `pytest tests/f04b -q` **89 passed in 678.89s**、`pytest tests/f06 -q` **173 passed in 1311.21s**、`pytest tests/tooling -q` **21 passed**、`go build/vet/test ./...` 全绿（38 包）、`gofmt` 空、`sync_spec --check` no drift、`node --check` + 4 个 Node 套件全绿（tail 见报告 §4） |

无迁移；未改 `docs/spec/**`、`contracts/**`。**not_run**：浏览器 DOM/Playwright、真实厂商模型/嵌入（仅 dev-stub）、
跨版本 checkpoint 恢复（本批不做）、无关全量套件（f05/f08 等）。

## 批次完成记录：#84–#92 / T083–T091（2026-11-06）

- 基线 `ea56392`（上一批 `integration/t068-t082` tip）→ 集成分支 tip `7e44e23`（T091 `344c139` 经 `93ddc6b` 合并，复核修复 `056fd75`/`7e44e23`）。最终门禁在 `056fd75` 上跑：`tests/f04b` **89 passed in 711.99s**、`tests/f14` **13 passed in 125.25s**、`tests/tooling` **21 passed**、Go 全绿、`sync_spec` no drift；`7e44e23` 仅恢复 PROGRESS 的 LF 行尾（代码/测试无差异）。
- 范围：本批 **9 票**（T083–T091，GitHub #84–#92）：F04B 7 票（T083/T085/T086/T087/T088/T089/T091）+ F08 T084 + F14 T090。
- 契约/规格：未改 `contracts/**`、`docs/spec/**`、迁移编号；`python tools/sync_spec.py --check` **no drift**。
  运行时的 spec 偏离（生产是 Go 图执行、不是 LangGraph）由 **ADR 0034** 记录，不静默。

### 票状态

| 票 | 状态 | 结果与缺项 |
|---|---|---|
| T083 | **passed** | 画布创作半 + 运行时半 + 运行视图半；真实栈 `tests/f04b` **14 passed in 110.23s**、`test_t083_runview.py` **7 passed in 42.69s**（含真实 condition/join/skipped E2E）；画布模块 8 passed；`tests/f04a` 36 passed、`tests/tooling` 21 passed。replay 后 join 只触发一次已由第三轮真实两次 exec 用例补齐（`REVIEW3-FIXES.md` #3） |
| T084 | **passed** | 34 例冻结语料（frozen 25 / dev 9，18+8 family 不跨 split）+ 真实发布/执行跨用户销售旅程；final-tree 全量 `pytest tests/f08` **not_run**（共享栈争用），实际证据为分套件 1/2/7 passed，第一次合并树完整套件 79 passed in 948.05s。已知缺口：自然语言聊天消息会被路由成功但执行 `node_input_invalid`（F08 把正文当 Run 输入，旅程用 JSON 任务文档） |
| T085 | **passed** | ready-queue 并发调度器、optional 收敛/required fail-fast/嵌套并行；`tests/f04b` **43 passed in 239.44s**；`tests/f05` 149 passed、`tests/f06` 172 passed + 1 个既有 sandbox flake（隔离重跑通过）、`tests/tooling` 21 passed；`go test -race` 因无 cgo 编译器 **not_run**。第三轮补齐 optional 发布校验（Go/Python + P05 43 例）与真实 resume join-once（T087 两次 exec）；附带修复 `internal/sandbox/executor.go` 租约竞态（`78d09db`） |
| T086 | **passed** | graph 路径 model 节点：一次受控生成、严格 `{{name}}` prompt、平台侧 `output_schema` 校验、恢复接缝；`test_t086_model_node.py`+`test_t086_model_canvas.py` **9 passed in 44.74s**；capability/agent 回归 18 passed；画布纯模块 9 passed。缺项：真实厂商模型、`usage.updated` 事件、真实二次 exec resume、浏览器 DOM 均 `not_run` |
| T087 | **passed** | 服务端 node approval、等待桥、执行器、画布、真实栈均交付（原 9 passed in 57.79s；审批/等待子集 27 passed；合并后关键 3 用例 3 passed in 58.54s）。第三轮补齐自动过期 sweeper：真实等待 + 置 past + 无 decision → API 自动 failed `approval_expired`，waiting decision `expired`、无 resume/output；复审 P1 修复为同事务交接（sweeper + decision 路径）并加两条故障回滚重试用例；现全文件 **12 passed in 105.43s**（最终树）。真实 join-once 重放也落在本套件 |
| T088 | **passed** | 平台 ACL 检索节点（非契约内部路由 + 执行器 + 画布 + run view 引用）；真实栈 `test_t088_retrieve.py` **6 passed in 74.49s**（最终树；另两次合并树 6 passed in 51.63s/78.47s）；引用与 DB chunk/citation 逐字段相等、撤权 404、并行不串。**输出已按 N-R10/R3 收窄**（本次复核修复）：items/citations 只带 `excerpt`（正文前 500 字符），无完整 `text`、无向量 `score`。缺项：`tests/f10` 全量 not_run、真实厂商 embedding、真实二次 exec 复用 retrieve 输出、浏览器 DOM |
| T089 | **passed** | call_app 画布面板 + graph 路径真实栈 E2E；真实栈 7 passed in 110.27s、画布 23 passed；单 child 等待/恢复、并行/顺序、执行槽交接、失败映射均有真实栈断言。票面共用的 child-thread/slot 与 doc/version/span AC 中：call_app 无文档/切片引用 → doc/version/span 为 **not_applicable**（其“版本保留”由固定 `application_version` + child Run manifest sha 证明）；child slot 与 child-thread 隔离是**真实交付并断言**的，不是 not_applicable。浏览器 DOM `not_run` |
| T090 | **blocked** | F14 所有不依赖模型的部分已交付：语料/目录卡冻结、泄漏检查、两臂 dev-stub 基线（臂 A frozen 25/25、臂 B 21/25）、`retrieval.not_used=true`；`tests/f14` **13 passed in 76.66s**（合并后复跑）、`test_t084_corpus.py` 4 passed。缺项（blocked 原因）：本机无模型凭据/端点/权重/训练 GPU，同基模强提示基线与训练 **not_run**；held-out 25 例低于 spec 的 100 例起步建议（未虚增）；训练 stop-go 只给 provisional |
| T091 | **passed** | 十类画布/导出、十类 fixture 保存/校验、真实两次审批嵌套汇合、optional/required/未知不降级；集成分支已 merge（`93ddc6b`，T091 `344c139`）。其报告门禁：`pytest tests/f04b` **89 passed in 678.89s**、`tests/f06` **173 passed in 1311.21s**、`tests/tooling` 21 passed、`go build/vet/test ./...` 全绿、`gofmt` 空、`sync_spec --check` no drift。缺项：浏览器 DOM/Playwright、真实厂商模型/嵌入、跨版本 checkpoint 恢复（本批不做） |

### 真实门禁汇总

| 套件 | 实际结果（最终相关树） |
|---|---|
| `pytest tests/f04b` | 各票关键套件 6/7/9/14/43/89 passed（见上表）；收尾分支在合并 T091 的树上重跑受影响文件：T088 retrieve + T083 runview + T086 model **18 passed in 178.05s**、T091 十类 acceptance 回归 **8 passed in 144.09s**、无栈模块/语料 **21 passed in 3.37s**；不重复全量 |
| `pytest tests/f05` / `tests/f06` / `tests/f04a` / `tests/tooling` / `tests/f14` | 149 / 172+1 flake（隔离重跑通过；T091 最终树 173 passed in 1311.21s）/ 36 / 21 / 13 passed（T085/T083/T091/T090 记录） |
| Go | `go build ./...`、`go vet ./...`、`go test ./...` 全绿；`gofmt -l internal cmd tools` 空 |
| 契约/静态 | `python tools/sync_spec.py --check` → `no drift`；`node --check`（runview/canvas 产物）exit 0；画布 bundle 由 `npm ci --offline && npm run build` 重建并提交 |
| 明确未跑/阻塞 | 全量 `pytest tests/f08`（final tree）、全量 `pytest tests/f04b`（收尾窗口）、`tests/f10` 全量、`go test -race`、T090 模型臂；均按实际标 `not_run`/`blocked`，不以部分结果冒充全绿 |

### 诚实边界

- **LangGraph 不是生产运行时**：生产图执行是 Go `internal/presetexec`（F04A 起），Python
  `worker/platform_worker/compiler/regions.py` + `tests/f04b/test_t083_langgraph_regions.py` 是独立证明
  （激活/skipped/join 次数）。这是对 `COMPILER-CONTRACT.md:3` 的偏离，ADR 0034 记录理由与边界。
- **非契约数据面**：节点输出约定、`/internal/v1/runs/{id}/retrieval`、`/node-approvals`（worker mTLS）、
  `GET /assets/runview.js`、`GET /runs/{id}/node-results` 均为非契约面，不写入 OpenAPI；见 ADR 0034。
- **浏览器 DOM 未驱动**：所有页面逻辑用 Node 执行已提交真实字节 + 最小 DOM/fetch shim（T083/T084/T086–
  T089/T091 同一边界），不是 Playwright。
- **真实厂商模型/嵌入**：仅 `ORCHARA_PROFILE=dev-stub`；无凭据/端点/权重/GPU，真实模型路径 `not_run`，
  T090 因此 `blocked`。
- **T084 输入形状**：聊天正文当 Run 输入会 `node_input_invalid`；旅程用 JSON 任务文档绕过，属 F08↔F04B
  集成缺口，未冒充通过。
- **T087 过期**：已由第三轮补齐自动 sweeper（API 定时扫描 + 真实等待自动过期用例），复审后改为
  **同事务原子交接**（approval expired + waiting decision + Run 终态一次提交，失败回滚重试）；decision
  路径仍保留 409 `approval_expired`。
- **T085 optional 发布限制**：发布校验已拒绝非纯读/计算 source（agent/call_app/model 等），
  与运行时 allowlist 同集合；不再是延迟项。
- **T083/T085 replay join-once**：真实两次 exec 已证明 join 只执行一次并复用同一 data artifact；
  Go fake-seam 仅作快速单测。
- **T088 输出边界**：节点数据只含 `excerpt`/无 score；完整正文只能经 owner-only citation 路由读取（撤权/
  删除后 404，不回退缓存正文）。内部 worker 路由的 text/score 是服务端排序用的 mTLS 数据面，不是节点数据。

### 合并顺序与提交

- 集成分支 first-parent merge（`git log --oneline --merges ea56392..bd4d791`）：
  `ea56392` → `08af658`（T083 画布）→ `111e2c2`（T083 运行时）→ `0deeab9`（T084）→
  `c21d45e`（T083 run view）→ `b343f7b`（T083 真实 condition E2E）→ `e5e7cc0`（T086）→
  `f21f09a`（T090）→ `04f09ba`（T087）→ `7b1becd`（T088）→ `aced554`（T089）→
  `15db973`（T085 并发调度器）→ `4851c4f`（T085 文档）→ `355e5d4`（T087 证据）→
  `5bd939d`（T089 证据）→ `7da0249`（T087 gate）→ `9d8208e`（T085 skip 事实）→
  `1cf7637`（T088 证据）→ `bd4d791`（T088 gate pins）→ `93ddc6b`（T091 十类工作流；T091 `344c139`）。
- 收尾分支 `t/batch-review-fixes`：`50d095f`（C.1–C.3 重构）→ `6a3195a`（T088 输出收窄 +
  C.5/C.6）→ `fbac878`（PROGRESS 诚实性/ADR 0034/T087 partial/T088 报告）→ merge
  `refs/heads/integration/t083-t091` @ `93ddc6b`（含 T091）→ **`593554c`**。合并后收尾门禁：
  受影响真实栈 `test_t088_retrieve.py`+`test_t083_runview.py`+`test_t086_model_node.py`
  **18 passed in 178.05s**；T091 十类 acceptance 回归 **8 passed in 144.09s**；无栈模块/语料
  **21 passed in 3.37s**；`go build/vet/test ./...` exit 0、`gofmt -l` 空、`sync_spec --check`
  no drift、`node --check` exit 0。详见 `reports/review/BATCH-REVIEW-FIXES.md` §3.3/§4。

## 第二轮独立复核修复（2026-11-06）

独立的第二轮 review 在 `ea56392..525a28f` 上发现 4 项范围内问题（Standards 2 / Spec 2），
全部在 `t/review2-fixes` 修复并先红后绿；报告：`reports/review/REVIEW2-FIXES.md`。

| # | 级别 | 发现 | 修复 |
|---|---|---|---|
| 1 | Standards/P2 | 恢复时复用已删除知识的 excerpt（未复查 tombstone） | retrieve 节点不再从恢复接缝复用；恢复时重跑内部 ACL 检索（`graphrun.go::reusableNodeOutputs`） |
| 2 | Standards/P2 | 审批 title/expires 取自 worker 请求 | 服务器从 sealed definition 读取并校验（`approvals/node.go::approvalNodeSettings`），非法 sealed config 503，不回退 |
| 3 | Spec/P1 | 并行挂起掩盖兄弟沙箱失败并用未启动证据确认退出 | `drain()` 记录兄弟失败；waiting 改为提交该失败；exit 证据聚合所有已启动 sandbox（`graphrun.go`） |
| 4 | Spec/P2 | condition/join 输出绕过 output_schema | `publishNodeData` 统一 schema 校验（`node_output_invalid`，不发布 artifact） |

回归：Go 单测 4 条新用例 + 真实栈 2 条新用例（T087 伪造 title/expiry；T088 等待中删除文档后恢复）；
最终门禁 `pytest tests/f04b`、`go build/vet/test ./...`、`gofmt -l`、`sync_spec --check` 全绿
（原始 tail 见 `REVIEW2-FIXES.md` §3）。

## 第三轮复核修复：T085/T087/join-once（2026-11-06）

第三轮复核在 `ea56392..8ce1d82` 上只要求修三项；报告：`reports/review/REVIEW3-FIXES.md`。

| # | 发现 | 修复 | 回归 |
|---|---|---|---|
| 1 | T087 停在 `waiting_approval` 的 Run 不会仅因时间自动终止 | `internal/approvals/expiry.go::ExpireDue`（只扫 node approval，`operation_id IS NULL`）+ `cmd/api` 定时器（`ORCHARA_APPROVAL_SWEEP_INTERVAL`，默认 30s）；数据库时钟、Run→approval 锁序、stale approval 不解析当前 wait；**审批 expired + waiting decision + Run 终态在同一事务**（`ResolveExpiredNodeApprovalTx`，sweeper 与 decision 路径共用，复审 P1 修复）；F06 capability approval 边界不变 | Go `TestWaitingReferencesApproval`、config 边界；真实栈 `test_a_parked_approval_expires_automatically_without_a_decision`、sweeper/decision 两条故障回滚重试；T087 全文件 **12 passed in 105.43s**（最终树） |
| 2 | T085 发布允许 optional 指向 agent/call_app/model | Go 生产校验器 + P05 Go/Python 校验器拒绝非 `{python,condition,join,retrieve}` source（`bad_join_sources`）；P05 corpus 重建 43 例（model/agent/call_app 负例）；受影响 runtime fixture 改为 pure python guard | `tests/p05` **111 passed in 7.17s**；真实 HTTP `test_publish_refuses_an_optional_agent_source`（`tests/f04b/test_t085_parallel_optional.py` **6 passed in 67.37s**，最终树）；T091 **8 passed in 87.75s** |
| 3 | T083/T085 replay join-once 只有 fake-seam | 真实两次 exec：fork/join 在 approval 之前，第二次 exec 复用 join artifact | `test_a_real_resume_reuses_the_join_and_never_fires_it_twice`（1 passed in 25.51s）；join data 行前后相同、每 attempt 至多一次 succeeded |

Go/静态门禁：`go build ./...`、`go vet ./...`、`go test ./... -count=1` 全绿；
`gofmt -l internal cmd tools` 空；`python tools/sync_spec.py --check` → no drift。
**复审 P1 修复（同日）**：第一版 sweeper 在审批事务提交后才 `resolveWaiting`，存在“approval 已 expired、
waiting decision 为空、Run 永久停泊”的窗口。已改为同事务交接：`expiry.go::expireOne` 与
`decision.go::Decide` 的 node-approval 过期分支都在提交前调用
`execution.ResolveExpiredNodeApprovalTx` 写 decision 并驱动既有 resume/fail；任何失败回滚整次过期，
下一轮重试。sweeper/decision 两条真实故障注入用例验证回滚与重试。T087 全文件 **12 passed in 105.43s**。

全量 `pytest tests/f04b`：**96 passed in 625.94s (0:10:25)**（最终树；含 sweeper/decision
两条故障回滚重试用例）。

## F12/T093 外部客户端全量 REST/SSE 契约门禁（GitHub #94，2026-11-06）

T093 已交付，状态 **`passed`**。本票交付的是**可重放判定手段 + 真实栈证据 + 诚实裁决**；**完整首版 API
门禁裁决 = passed（106/106）**，唯一 `not_run` 是 SSE 410 保留窗口分支（归 T096）。
证据：`reports/f12/T093-api-conformance.md`、机器可读 `reports/f12/T093-api-conformance.json`；
分支 `t/t093-api-conformance`（worktree `E:\Orchara-wt\t093`），基线 `integration/f12` @ `1cdff9c`，
结束 merge `integration/f12`（交付 HEAD 见 §8/摘要）。

| 交付点 | 结果 |
|---|---|
| 门禁 CLI | `tools/f12_api_gate.py`：从 `contracts/openapi.json` 读 106 operation（不手写清单）；逐 operation 三面探针（匿名 401 / 另一主体 403-404 / internal mTLS 角色）+ 可构造资源的 normal path；10 条跨端点不变量；199 条字面路由注册表静态扫描；`--out` 写机器可读证据；退出码 0=passed / 1=failed / 2=契约清单不合法。契约数量或 operationId 重复的变异直接拒绝（exit 2，不产报告） |
| 逐 operation | **106/106 implemented、0 authorization_failed、0 per_operation_failed**；4 public / 23 internal（真实 worker/supervisor/maintenance 客户端证书）/ 79 session；106×2 parametrized pytest 断言记录存在、不 skip、形状与契约一致 |
| missing 判定 / 无通用 CRUD | 注入第 107 条不存在的端点 → 以字面注册表判 `route_registered=false`、进 `missing_operations`、verdict failed、exit 1，真实 106 条仍 implemented；删 1 条 → exit 2。未知路径 404 封套、真实路径错误方法 405+`Allow`、随机资源 404，注册表 `generic_patterns=[]` |
| 服务端修复（5） | ① mux 404/405 → `ContractErrors` 契约封套（外部+内部 mux，保留 `Allow`，SSE 可 Flush）；② `/` → `GET /{$}` 让 mux 拥有 405；③ 审批 `list` 的 `limit` 改 `catalog.ParseLimit`（越界 422 + `details.field`，不 clamp）；④ 审批 service 层同样不再静默 clamp；⑤ 执行租约 `lease_deadline/heartbeat_at/started_at` 由 `::text` 改 `contractTimestamp()`（UTC RFC3339、微秒） |
| 门禁自身修复（5） | ① missing 分类以 `scan_route_registry()` 为存在性权威（`ContractErrors` 后运行时无法区分路由 404 与资源 404）；② 形状不变量移到 normal probes 之后；③ `*_checked` 由“失败数”改为“真实检查字段数”（123 id / 22 时间 / 4 金额 / 371 JSON 响应，测试要求 >0）；④ UUID 不变量按契约 2xx schema 豁免两个 `type:string` 的配置目录 id（`stub-credential`/`stub-endpoint`，否则假红）；⑤ CLI 复跑改用独立 pristine 栈并比对 session 裁决（门禁会真实创建/领取资源，不能在同一库上重复） |
| 门禁 | `pytest tests/f12` **242 passed in 50.71s**（`F12_WRITE_EVIDENCE=1`）；`tests/f12/test_gate_mutations.py` 单跑 **4 passed in 50.79s**；`go build ./...`/`go vet ./...`/`go test ./...` 全 exit 0；`gofmt -l internal cmd tools` 空；`python tools/sync_spec.py --check` → no drift |
| 回归影响面 | 7 个既有套件真实栈串行：`tests/f00b` 133 passed + 2 failed、`tests/f03` 201+2、`tests/f06` **173 passed**、`tests/f02a` 102+2、`tests/f07` **36 passed**、`tests/acceptance` **13 passed**、`tests/f01`（单跑）**155 passed**。6 条既有红已在 baseline `1cdff9c` detached worktree 逐条复现：F08/T074 的 `runs_{request,conversation}_id_fkey`（2）、F08/T079 的 `route_request` kind 与 `model_calls.request_id` FK（2）、F07/T081 的 `runs.last_child_claim_at`（1）、F07/T075 的子 schema `io_mapping_unknown_field`（1）。与本票 diff 不相交，按既有红记录，不静默修、不 skip |
| 边界/not_run | SSE 410 `event_cursor_expired`+`snapshot_url` 需要比 24h 更老的事件（开发栈默认 `ORCHARA_RUN_EVENT_RETENTION=24h`，本票未压低；归 T096 低保留窗口栈）；真实厂商模型/凭据（本票不调用模型）；非契约路由（去重 199 条中非契约 93：页面/静态 45、公开业务扩展 26、内部 mTLS 17、签名链接 2、运维别名 3）只审计不写契约；`artifact.ready`/`usage.updated` 无 emitter、缺 `Idempotency-Key` 时 `details` 有 `header`/`field` 两种形状、全部 JSON `no-store` 使 HTTP 协商缓存不可用——均记录并归属其它 epic；审批 service 的 422 防御分支无独立 HTTP 入口用例 |

提交：`1cf8c2c`（feat 门禁 CLI）→ `9385ee6`（fix 服务端 5 处）→ `8ab0b1f`（test 套件）→ 文档提交
（本小节 + 报告）。`git merge integration/f12` = **Already up to date**（集成分支仍为基线 `1cdff9c`）；
合并后重跑 `pytest tests/f12` **242 passed in 65.88s**、`go build/vet/test ./...` 全 exit 0、
`gofmt -l internal cmd tools` 空、`sync_spec --check` no drift。最终 HEAD 见
`E:\Orchara\.scratch\f12\T093-REPORT.md`。

## F12/T096 浏览器断线与父子等待时仍能解释真实运行状态（GitHub #97）（2026-11-06）

T096 已交付，状态 **`passed`**（手段真实 + 覆盖完整 + 裁决诚实）。它关掉了 T093 报告里唯一的
`not_run` —— **SSE 410 保留窗口分支** —— 并补上 finalizing/cancelling/child 等待/部分产物的
页面真值。**无 Playwright/真实浏览器 DOM、无物理网卡断线**：页面行为用 Node 执行已提交真实字节 +
最小 DOM/fetch shim；410 来自真实 `ORCHARA_RUN_EVENT_RETENTION=50ms` 的 `cmd/api` 进程。
证据：`reports/f12/T096-disconnect-and-run-state.md`、`E:\Orchara\.scratch\f12\T096\evidence.jsonl`；
分支 `t/t096-run-state-truthful-ui`（worktree `E:\Orchara-wt\t096`），基线 `integration/f12` @ `497f4e8`。

| 交付点 | 结果 |
|---|---|
| 新纯逻辑模块 | `apps/web/src/assets/runstream.js`：`state`(10)/`phase`(4) → UI 文案/可操作项；410 分类 + `details.snapshot_url` 安全校验 + 快照重建（`resumeCursor=""`，不静默续传）；父子 N/M；产物 readiness（未完成运行产物/最终产物/中间/未就绪）；`/chat` 的 handling 与“dispatched 后继续观察 Run”轮询规则；静态路由 `GET /assets/runstream.js` |
| `/runs` | 410 后**真实 GET 快照并重建**（不是只显示 URL），时间线标记“保留窗口…未回放旧帧”，`dataset.snapshotRebuilt=true`；`waiting_children/approval` 每次轮询刷新子面板；非终态轮询也刷新产物并标“未完成运行产物”；取消回执统一“不会撤回已经发生的外部写入” |
| `/chat` | `dispatched` 后继续轮询 Run 到终态（`shouldPollHandling`，旧 `shouldPollRequest` 不变）；finalizing/cancelling/waiting_children/needs_reconciliation 文案；`chat-handling` 带 `data-run-state/phase` |
| `/debug`、`/runview`、`/attachments` | debug 60s 后转 10s 慢速继续观察（不再“停止更新”）；runview `formatRunError` 增加 finalizing 分支 + 取消回执统一；attachments 的 uploading/scanning/ready/quarantined/deleted 统一文案，deleted 终止轮询 |
| 真实栈证据 | 410：`410 {"error":{"code":"event_cursor_expired","details":{"snapshot_url":"/api/v1/runs/<id>"}}}` + 快照 GET 200 + 页面真实 fetch；finalizing：`state=running phase=finalizing revision=2`，SSE 事件 `{"state":"running"}` 无 phase，页面 `terminal=false/isSuccess=false/isFailure=false`；cancelling：`202 state=cancelling phase=executing revision=3`，页面 JSON 无“已撤销”；父子：真实 `waiting_children` + 子 `queued` → “等待子任务（已完成 0/1…）”，真实 supervisor 执行子 Run 成功后 `1/1` 且父仍非成功；产物：ready 但 Run 未终态 = “未完成运行产物”，`succeeded`+`output_artifact_id` 后才“最终产物” |
| 门禁 | `python -m pytest tests/f12/test_t096_pages.py tests/f12/test_t096_stack.py -q` → **17 passed in 57.04s**；`python -m pytest tests/f12 -q` → **259 passed in 111.41s (0:01:51)**（合并后重跑；T093 的 242 全保留）；`go build/vet/test ./...` 全 exit 0；`gofmt -l internal cmd tools` 空；`sync_spec --check` → no drift；六个改动脚本 `node --check` 全 exit 0 |
| 受影响既有套件 | `tests/f03` **2 failed, 201 passed in 1362.47s**（2 条 = T093 §6.2 已记录的既有红：`runs_conversation_id_fkey` / `runs_request_id_fkey`，同名同因，baseline 可复现）；`tests/f07` **36 passed in 311.95s**；`tests/f08/test_postreview_pages.py` **2 passed in 0.36s**；纯 Node `tests/f04b/runview*.test.mjs` 6+7+4 passed |
| 未修（归属其它 epic） | `artifact.ready`/`usage.updated` 无 emitter（F03/F04B）；`run.status` 无 phase（契约只读，页面重取 `GET /runs/{id}`）；`/children`、`/node-results` 非契约无分页（F07/F04A）；artifacts 不返回 relation/purpose（F05）；Run 删除无公开端点；真实厂商模型/GPU 无 → `not_run` |
| 边界 | 无浏览器 DOM/Playwright（页面=真实字节+shim）；无物理断网；`debug.js` 慢速续轮询只有源码守卫（poll 是闭包，无独立驱动）；410 只在建流时判定，已建流不会中途因保留窗口被踢 |
## F12/T095 跨角色刷新与撤权后界面保持真实授权状态（GitHub #96，2026-11-06）

T095 已交付，状态 **`passed`**。本票交付**单一角色化导航 + 统一 401/403 出口 + 撤权/禁用后的界面真值**，
修的是界面真实性与可达性，**不是补鉴权**：服务端 `authorize`/`requireScope`/`requireApplicationWrite`
链未改，「隐藏入口不能代替鉴权」在服务端成立（20 条负例入口的真实 HTTP 拒绝见报告 §3）。
判定手段：页面是 **Node 执行已提交真实字节 + 最小 DOM/fetch shim（无 Playwright/真实浏览器）**；
REST 是真实 `cmd/api` + 真实 PostgreSQL 16 + mTLS + 对象目录。证据：
`reports/f12/T095-role-refresh-and-revocation.md`；分支 `t/t095-role-truthful-ui`
（worktree `E:\Orchara-wt\t095`），基线 `integration/f12` @ `497f4e8`。

| 交付点 | 结果 |
|---|---|
| 单一导航源 | 新增 `apps/web/src/assets/nav.js`，由 `GET /api/v1/auth/me` 的 roles/kind 生成导航：管理台仅 admin、`/apps` 标签 creator「应用与创作」/否则「应用（只读）」、service 不显示人类入口、匿名=状态/登录/激活。22 个 HTML 全部挂载，取代 22 份手写静态列表 |
| 统一 401/403 | `app.js` 新增 `handleAuthFailure`（401 + 403 `principal_disabled`/`responsible_disabled`）：清空所有 `[data-auth-content]` 区域、跳 `/login`（`?error=session_revoked|session_expired|principal_disabled` 让登录页说明原因）；`apiFull` 对任何数据请求统一走该出口（approvals 等无 401 分支的页面也不会把拒绝读当成功）；403 角色拒绝触发 nav 重读身份并重渲染 |
| UI 误导入口（AC1 反向） | `/apps` 不再给只读者渲染编辑/调试链接（versions 保留）；新建/模板、debug 提交、publish 预览+提交、evaluate 提交、editor 保存表单改为 `data-requires-role="creator" hidden`；非管理员进 `/admin` 明确提示“不是管理员，服务端会拒绝”；登录后按角色跳转（admin→`/admin`，其余→`/apps`） |
| 分页一致（AC2） | models 补「下一页」按钮、versions 跟随 `next_cursor` 并追加、personal-data `collectAllPages` 取完整清单、knowledge 知识库列表循环游标；REST 侧断言 `limit=1` 两页不重叠、详情与列表项逐字段相等、`limit=101/0/abc → 422 + details.field=limit`。非契约列表（children/node-results）与 runs/chat 分页归 T096，未改其 JS |
| 撤权/禁用真值（AC3） | 真实栈：禁用→旧 session `401 session_revoked`、旧 key `401 invalid_key`、登录 `403 principal_disabled`；启用不恢复旧凭据；角色提升/降级在同一活会话下一次请求即生效（403→201→403）；撤销 read grant 后下一次读 404 且列表不再含该应用。页面侧断言刷新重读身份、401/403 清旧行、会话内撤角色后入口当场收敛 |
| 死链/假退出 | `chat.html` 退出改走真实 `logout()`（POST + CSRF）；`/chat?version=` 在 chat 页读参数并预填版本输入；`/runs?run=` 在 runs 页读参数并调用既有 `loadRunDetail`。**未改** `chat.js`/`runs.js`/`runview.js`（T096 所有权），只在 HTML 内联做最小接线 |
| 测试 | `tests/f12/test_t095_role_entrypoints.py` **25 passed**（20 条入口↔端点↔负例主体↔状态码 parametrize + 禁用/角色更新/撤权/分页）；`tests/f12/test_t095_page_roles.py` **10 passed**（真实 committed bytes + shim：角色导航矩阵、刷新重读、401/403、creator 门禁、会话内收敛、logout、models/versions/personal-data 翻页、登录原因与角色分流） |
| 门禁 | `pytest tests/f12` **277 passed in 60.08s（242 T093 + 35 T095）**（含 T093 的 242 passed，未破坏）；merge `integration/f12`（Already up to date）后在交付 HEAD 上复跑 **277 passed in 66.16s (0:01:06)**，退出码 0；`go build ./...`/`go vet ./...`/`go test ./...` 全 exit 0；`gofmt -l internal cmd tools` 空；`python tools/sync_spec.py --check` → no drift；`node --check` 改动脚本 exit 0；22 页 15 个内联脚本块抽取后逐个 `node --check` 通过；`tests/tooling` **21 passed**；`tests/f08/test_postreview_pages.py` + `tests/f04b/test_t083_runview.py` **9 passed** |
| 回归影响面 | 没有既有测试执行 `app.js`/`nav.js`（全库 grep 无匹配）；T096 三个 JS 文件零改动；`tests/f08` 的两条 chat.js 页面测试与 `tests/f04b` 的 runview 测试仍全绿；Go 零改动 |
| 边界/not_run | 无 Playwright/真实浏览器（页面结论=Node+shim）；死链修复未做真实浏览器点击；SSE 不在本票路径（T096）；403 `principal_disabled` 在浏览器 cookie 路径实际不可达（禁用先命中 401 session_revoked；key 先命中 401 invalid_key），该分支为防御性处理，服务端可达入口是登录 POST；契约缺 `listGrants`/`getGrant`/`listApiKeys`（F01 记录，不补）；runs/chat 列表分页、children/node-results 口径归 T096；移动端归 T098；仅 dev-stub，无真实厂商模型 |


## F12/T098 手机完成对话、审批和个人数据控制（GitHub #99）（2026-11-06）

T098 已交付，状态 **`passed`**（手段真实 + 覆盖完整 + 裁决诚实；“小屏完整画布编辑”按票面明确不做）。
本票补齐**手机端对话/审批/结果/个人数据**四条旅程的响应式布局与状态反馈，并在真实栈上验证
冲突（412/409）、过期（两种审批语义 + interaction_expired）、无结果（空检索 + no_matching_application）、
撤销引用 404 不回退缓存、删除后立即禁读与异步清理/备份期限分离、候选显示来源/保存范围且默认不勾选、
资料摄取状态/失败码。**无 Playwright/真实浏览器 DOM**：页面行为用 Node 执行已提交真实字节 + 最小
DOM/fetch shim；CSS 断点/像素布局/真机触控标 `not_run`。证据：
`reports/f12/T098-mobile-and-personal-data.md`；分支 `t/t098-mobile-personal-data`
（worktree `E:\Orchara-wt\t098`），基线 `integration/f12` @ `1ba9d01`，代码+测试 HEAD `b2d3089`。

| 交付点 | 结果 |
|---|---|
| 响应式样式与页面接线 | 新增 `apps/web/src/assets/mobile.css`（720/480px 两档、44px 触控目标、导航换行、`dl`/`.row` 单列、16px 输入、`.table-scroll`）；`/chat`、`/approvals`、`/runs`、`/personal-data`、`/memories`、`/knowledge` 全部挂载，`/runs` 8 列账本包可聚焦横向滚动容器，`/chat` 补齐原先缺失的页面级布局；`/chat`、`/runs`、`/approvals`、`/editor`、`/debug` 声明“不承诺小屏完整画布编辑” |
| 冲突/过期/无结果反馈（AC1） | 真实栈：记忆陈旧 `If-Match` → `412 precondition_failed`（`current_etag="1"`/`current_revision=1`，正文未变）；同键异体 → `409 idempotency_conflict`（仍只有 1 条请求）；交互过期 → `409 interaction_closed` + `failed(interaction_expired)`；无匹配 → `failed(no_matching_application)`；空知识库 → 检索 `200 items=[]`、问答 `200 no_results`。页面：审批 `approvalExpiryNote` 区分 **capability 惰性过期** 与 **node 30s sweeper 自动过期**，失败后禁用/重读/不自动重试；chat 的 409 不重放旧幂等键、过期答复不复活任务；空结果有“不是错误 + 下一步” |
| 撤销引用不能读缓存（AC2） | 真实全链路：上传→finalize→ingest→ready→检索→引用 `200` 正文 → `DELETE /knowledge-documents/{id}` `202` → 同一引用 `404 resource_not_found`（响应无 `text`/`citation`）→ 检索空；页面 404 清空 `#citation-text`、`data-citation-available=false`，并断言**不回退**上一次正文 |
| 个人数据控制 | 记忆候选显示来源/交互封存/保存范围（应用）/状态/过期，`dataset.selection="none"`、无勾选控件、无全选、点击才发决定；诊断对象**默认不勾选**；文档 7 个摄取状态 + 真实失败码（`ingest_empty_content` 等）+ 下一步；删除同时展示“立即停止访问”（tombstone/epoch，202 前生效）与“异步物理清理/备份期限”+ 真实删除任务状态/剩余 scopes，`GET /deletions/{id}` 可读 |
| 真实栈证据 | 记忆删除 `202` 后下一次 `GET` 404、列表空、job `200 pending` + remaining scopes；候选 `201` 五字段 pending → 列表无预选择 → 显式 decision `200 confirmed` + 单条记忆；capability 审批过期后 0.6s（≥6 次 sweep）仍 `pending`，decision 才 `409 approval_expired`；node 审批（真实 prepare + waiting + exit）过期后由 100ms sweeper 自动变 `expired(decision_key=sweeper:expired)` 且 Run `failed(approval_expired)`，迟到决定 409 |
| 测试 | `tests/f12/test_t098_mobile_pages.py` **8 passed**（真实 committed bytes + DOM/fetch shim：移动结构/类名/viewport 样式接线、两种审批过期、chat 冲突/过期/无结果、候选默认未决、个人数据状态/默认未选、引用清除）；`tests/f12/test_t098_stack.py` **6 passed**（真实 PG/mTLS/maintenance/http；412/409/interaction/no-match/空结果/撤销引用/删除/两种审批过期/候选） |
| 门禁 | `pytest tests/f12/test_t098_*.py -q` → **14 passed in 27.93s**；`pytest tests/f12 -q` → **308 passed in 131.09s (0:02:11)**（既有 294 条全保留）；受影响：`tests/f08/test_postreview_pages.py` + `tests/f04b/test_t083_runview.py` + `tests/f06/test_t050_pages.py` **12 passed in 54.26s**、`tests/f09` **69 passed in 357.41s**、`tests/f10` 知识页 **6 passed in 24.43s**、`tests/f08` chat 页 **4 passed in 17.79s**；`go build/vet/test ./...` 全 exit 0；`gofmt -l internal cmd tools` 空；`python tools/sync_spec.py --check` → no drift；5 个改动脚本 `node --check` exit 0；8 个改动页面抽取 4 个内联块逐个 `node --check` 通过 |
| 边界/not_run | 无 Playwright/真实浏览器 DOM：CSS 断点真实求值、像素布局、真机触控/手势/横竖屏/`safe-area`/屏幕阅读器均 `not_run`；无真实厂商模型（dev-stub）；删除的“物理副本已删/备份已过期”不声称（只显示 job）；`MemoryCandidate` 契约无 `source_run_id`（`additionalProperties:false`），页面不编造具体 Run id；小屏完整画布编辑（`/editor`、`/debug`）明确不在票面范围；`/chat` 正文当 Run 输入的既有 `node_input_invalid` 缺口未绕过 |
| 未修（归属 epic） | 删除后的物理清理/备份期限（F07/T071/T072）；`MemoryCandidate` 的来源 run id（F09/契约治理）；诊断授权仍按 run UUID 手输（F11/T073）；删除任务不轮询到 completed（F07）；`/editor`、`/debug` 响应式（明确不做）；真实厂商模型（F14/T090） |

提交：`bb3b10e feat(f12)`（`mobile.css` + 6 页接线 + 审批/chat/记忆/个人数据/引用状态 + 静态路由）→
`b2d3089 test(f12)`（页面 8 + 真实栈 6）→ 本文档提交。`git merge integration/f12` = **Already up to date**
（集成分支仍为 `1ba9d01`）；合并后在最终树复跑 `tests/f12` **308 passed in 132.16s**、本票 14 passed、
Go/静态/语法门禁全绿。最终 HEAD 见 `E:\Orchara\.scratch\f12\T098-REPORT.md`。

## F12/T100 复现账号到发布和后台执行的关键旅程（GitHub #101，2026-11-07）

T100 已交付，状态 **`passed`**（五条旅程全部真实跑通 + 独立真值 + 失败/授权/重试边界断言；
1 条已知产品缺口 `blocked`、1 个真实厂商模型变体 `blocked` 单独声明，没有写成通过）。
证据：`reports/f12/T100-journeys.md`（逐 AC/逐旅程/真实命令+原始 tail）、
`reports/f12/T100-journeys.json`（机器可读：每条旅程 `entry_commands/entry_http/truth_sources/
assertions/facts/variants/duration_seconds` + `known_gaps`）；分支 `t/t100-core-journeys`
（worktree `E:\Orchara-wt\t100`），基线 `integration/f12` @ `60d9de0`。

| 交付点 | 结果 |
|---|---|
| 旅程实现 | `tests/f12/journeys/j100_{account,models,publication,worker_exit,background}.py` 承载五条旅程；`tests/f12/test_t100_*.py` 五个用例；`tests/f12/t100_support.py` 提供模块级真实栈 + 证据记录器 + 封存字节/hash 真值读取（**未改** `tests/f12/conftest.py`，不复用 T093 的 `f12_stack`） |
| J1 账号启动 | 真实 `cmd/admin migrate`（52 条连续迁移，头 `0052`）+ `bootstrap`（唯一 admin、argon2id、`admin.bootstrap` 审计）→ 真实 HTTP 邀请（token 只存 SHA-256、sentinel `!pending-activation`、creator 角色）→ 激活（一次性，复用 409 `activation_consumed`）→ 登录 + `/auth/me`（cookie/CSRF 只存 SHA-256）。边界：二次 bootstrap 非 0 `already exists`、401/403/`login_taken`/`csrf_invalid`/422 弱密码不消耗 token/`activation_invalid`/`activation_expired`（仅把截止时间移入过去的时钟型写，已声明）/`invalid_credentials` 不建 session |
| J2 模型验证（dev-stub） | 真实 `POST /models`（201 pending）→ `POST /models/{id}/validate`（202 EvaluationJob，同 key 重放不建第二个 job）→ 真实 `cmd/maintenance --kinds model_validate`（+ finalize 进程）→ `GET /models/{id}` passed、`GET /evaluations/{id}` succeeded、`GET /evaluation-reports/{id}` passed；独立真值：model/evaluation/report 行、对象目录封存 report+dataset 字节 sha256、`platform_model_calls` 1 条 succeeded。边界：非管理员 403、缺 Idempotency-Key 400、不兼容 stub（job succeeded 但 report failed、模型不可见）、未部署 provider（`provider_not_configured`、不写 report）。真实厂商模型 `blocked`，stub 明示 `provider=stub/stub=true` |
| J3 草稿→评测→发布 | 真实 `POST /applications`+`PUT /draft` → `POST validate`（`X-Manifest-SHA256` == 封存 manifest artifact 字节 sha256）→ 真实五类评测（maintenance 全 kind 池 + 每 case `cmd/supervisor exec --once`/runsc；封存报告逐 case：succeeded / `node_input_invalid` ×2 / `row_limit_exceeded` / `sandbox_unavailable`）→ `submit`（review 冻结五类 author_cases）→ admin `decision` → 唯一 `application_versions` v1（manifest_sha256 与已评测快照一致）+ active pointer + `catalog_index` outbox + `publication.approved` 审计。不可变性：二次 decision 409 `review_conflict`、相同内容重提返回同一 review、草稿改后再 validate 得到新 hash 而已发布行/字节不变。边界：外主体 submit 404、错 hash 409、缺 key 400、非管理员 decision 403；`publication-preview` 标注为非契约路由 |
| J4 worker 终结双证据 | 真实 debug Run + supervisor claim + 真实 run-scoped artifact 上传/注册（`final_output`/`output`）。**result-first**：proposal 后 `running/finalizing`、job leased、槽占用 → exit 一次提交 `succeeded/idle` revision 2→3、job completed、attempt `exited_at+revoked_at`、槽释放、input+output 两条 link；重复 exit 幂等、迟到 result 409 `run_terminal`、不同证据 409 `attempt_evidence_conflict`。**exit-first**：exit 先 `recovery_pending/rev3`、job held、槽已释放 → result 提交 `succeeded/rev4`。角色：maintenance 不能 result、worker/maintenance 不能 exit（403）；他 token 409 `stale_attempt`。真实 `cmd/supervisor exec --once` 变体：start→python→end 在 runsc 成功，4 条 node results，产品 Decimal 汇总 == 测试从 CSV 字节独立计算值（250.00/东区200.00/西区50.00/3） |
| J5 后台领取→结果 | 真实 `POST /tool-runs`（202，同 key 重放幂等）→ 真实 `cmd/maintenance --kinds tool_run,artifact_finalize` 执行预设工具 → job `succeeded/attempt_count=1/result_event_id/finished_at`，封存 summary 字节 sha256 == artifact 行，Decimal 汇总 == 测试独立计算值，receipt 202；契约读 `GET /background-jobs/{id}` 一致。精确协议：maintenance 证书 `claimBackgroundJob`（leased/attempt 1/lease_token）→ 第二次 claim 204 → `heartbeatBackgroundJob` 200 且 `lease_deadline` 前移 → `completeBackgroundJob` 可重试失败 `tool_timeout` → job ready/infrastructure/旧 token 409 `stale_attempt` → backoff 后重领新 token attempt 2 → 终态业务失败 `negative_amount`（business，attempt 2）→ 同 event 重放 200、异 event 409 `event_conflict`、终态后 claim 204。公开边界：外主体/admin 读 404、缺 key 400、同键异体 409、未知 tool 422；worker/supervisor 证书 claim 后台 job 403 |
| 已知产品缺口（显式） | **聊天正文作为已发布 Run 输入 → `node_input_invalid`**：`POST /requests` 带显式 `application_version_id`（规格内入口）→ 202 dispatched，输入 artifact 字节==消息原文 → 真实 supervisor → executor `{"status":"failed","outcome":"refused","run_state":"failed","error_code":"node_input_invalid"}`，DB `runs.failed/node_input_invalid/output NULL`、node-results 空。这是 F08↔F04B 产品缺口（`submit.go:131,646-690` → `linear.go:177-187`），记 `blocked`，不计入五旅程；未用“JSON 任务文档当聊天消息”绕法声称该能力。自然语言请求旅程归 T101 |
| 证据口径 | 每条旅程独立读真值：psql 行 + 对象目录封存字节（重算 sha256）+ 真实 CLI/进程退出码与 JSON 报告；不从 200 推断。测试侧 SQL 仅：J1 的 activation 过期时钟写、J3/J4 的部署种子（预售工具版本、执行槽容量）；J5 的 retry/stale-token 用真实可重试完成，不写时钟 |
| 门禁 | `pytest tests/f12/test_t100_{account,background,models,publication,worker_exit}.py -q` → **5 passed in 85.13s (0:01:25)**（`F12_WRITE_EVIDENCE=1` 生成 `T100-journeys.json`，35,128 bytes，5 passed + 1 blocked gap）；`pytest tests/f12 -q` → **313 passed in 220.60s (0:03:40)**（既有 308 全保留）；`go build ./...`/`go vet ./...`/`go test ./...` 全 exit 0；`gofmt -l internal cmd tools` 空；`python tools/sync_spec.py --check` → no drift；`git merge integration/f12` = **Already up to date**（tip 仍 `60d9de0`），合并后在最终树复跑上述 T100 套件与 `tests/f12` 全绿 |
| 既有红（非本票） | `pytest tests/f01 -q`（单跑）→ **2 failed, 153 passed in 925.64s**：`test_login_page_is_served_and_posts_the_contract_path`（缺 `window.location.assign('/admin')`）与 `test_the_apps_page_lists_the_visible_applications`（缺 `error.status === 401`）——T095 的角色化登录跳转/统一 401 出口改变了页面字面量，f01 marker 断言未同步。已在 baseline `60d9de0` detached worktree 逐条复跑，**报错逐字相同（2 failed in 26.65s）**，故为集成分支既有红，归 T095/T098 后续修正；本票未改页面/未改其测试/未掩盖 |
| 边界/not_run | 无真实厂商模型/凭据/GPU → J2 真实模型质量 `blocked`（机制与 stub 报告已验）；无浏览器 DOM 需求（全 HTTP/内部协议）；无 `not_run`（本机 WSL2/runsc 可用，J3 五类评测与 J4 真实执行器都真跑）；非契约路由（`/tool-runs*`、`publication-preview`、`/runs/{id}/node-results`）已在报告标注，不算契约入口 |
| 未修（归属其它 epic） | 聊天正文→Run 输入缺口（F08↔F04B）；`artifact.ready`/`usage.updated` 无 emitter（F03/F05/F06，T093 已记）；f01 两条页面 marker 漂移（T095/T098）；真实厂商模型（F14/T090） |

提交：`test(f12)`（五条旅程 + 支持模块 + 证据 JSON）、`docs(f12)`（本报告 + PROGRESS）。
`git merge integration/f12` = **Already up to date**；最终 HEAD 见 `E:\Orchara\.scratch\f12\T100-REPORT.md`。
## F12/T101 请求、审批、记忆、知识及服务账号五旅程真实复现（GitHub #102）（2026-11-07）

T101 已交付，状态 **`passed`**（手段真实 + 覆盖完整 + 裁决诚实）。五条旅程全部在真实栈上复跑：
真实 HTTP `/api/v1/**`、真实 PostgreSQL 16.4、真实内部 mTLS（worker/supervisor/maintenance）、
真实 `cmd/supervisor exec --once`（WSL2/runsc）、真实 `cmd/maintenance run --once`、真实对象目录；
DB/对象目录只用于**独立断言**外部副作用真值。票面「不重复实现功能页面」：本票不写页面、不做 DOM。
证据：`reports/f12/T101-journeys.md` + `reports/f12/T101-journeys.json`（6 条 journey 记录）；
分支 `t/t101-data-journeys`（worktree `E:\Orchara-wt\t101`），基线 `integration/f12` @ `60d9de0`。

| 交付点 | 结果 |
|---|---|
| J1 请求→Run→结果（AC1/AC4） | **passed，16 断言**。版本来自真实五类评测 + `POST /applications/{id}/submit` + admin `POST /reviews/{id}/decision`；请求用规格内的显式 `application_version_id` 入口（IMPLEMENTATION-DETAILS §5）同事务 dispatch；真实 supervisor 执行 → Run `succeeded`；`GET /runs/{id}`/`GET /requests/{id}` 可读；`summary.csv` 字节 == 文档化期望并经 `tests/support/t049.py` 独立 Decimal 复算；封存 request_input 对象字节 == 提交任务文档且 sha256 一致；发布者/匿名读为 404/401；同幂等键重投 202 同 request 且不建第二个 Run；已撤销版本新请求 `409 version_revoked` |
| J1b 请求（自然语言）→Run（AC4 边界） | **blocked（产品缺口实测，非跳过）**。同一显式版本入口提交纯中文正文 → 请求 202 dispatched → 真实 supervisor Run `failed/node_input_invalid`、无 output、node-results 空。链路 `internal/requests/submit.go:131 → :646-690` → `internal/presetexec/linear.go:177-187`。属 F08↔F04B，本票不修，报告 §7.1 显式声明 |
| J2 写操作审批（AC2/AC4） | **passed，34 断言**。write 风险连接器（真实回环 HTTP）→ `invokeCapability` park → approver=Run owner；`params_sha256` 绑定精确参数；陌生人 404 / admin 403 `approver_mismatch`；改参数 409 `operation_conflict`；错 hash 409 `approval_params_mismatch`；owner 批准 + 真实 exit → 恰 1 个 resume → 回环端点**恰好 1 次**真实写，receipt 为端点真实 2xx body，重放不重发；reject→`approval_rejected`、expire→`approval_expired`、cancel→approval invalidated，均 0 外写；写后丢回执→operation `unknown`、重复 invoke 不重发、Run `needs_reconciliation`（`unknown_operations`）、owner 证据 reconcile 后 succeeded 且物理写仍 1 次 |
| J3 个人记忆（AC2/AC4） | **passed，28 断言**。真实 Run 租约 + worker mTLS propose → pending 候选（封存字节/应用与 owner 作用域）→ 人类 confirm 恰 1 条 memory；pending 不是记忆；陌生人队列空/决定 404；服务 key `403 personal_scope_forbidden`；真实 agent Run 召回（`memory_state=included`、`0 < 41 ≤ 30976 tokens`、模型请求含正文、trace 不泄漏正文）；修订后召回新文本旧文本消失、旧 If-Match `412`；删除 202 后下一次 Run sources 为空、`GET /memories` 空。诚实边界：生产图无 memory 节点，候选由真实 worker 身份直接调内部端点（F09/T054 既有事实） |
| J4 知识摄取（AC1/AC4） | **passed，19 断言**。`POST /knowledge-bases` → 真实上传+finalize → `POST .../documents` + 真实 `document_ingest` maintenance → ready（row revision=1、chunks 连续、job succeeded）→ 检索 → 引用 200；引用**独立复算**（封存源字节 sha256 + span rune 重切 == 返回文本，chunk body 同文）；陌生人 404；删除 202 → 同一引用立即 404（无 text/citation）且检索 `items=[]`（`cache.hit=false`，不回退缓存）；删除任务可读 |
| J5 服务账号（AC2/AC4） | **passed，24 断言**。admin `createServiceAccount` → `createApiKey`（secret 一次返回、库中仅 SHA-256）；`/auth/me` kind=service/roles=[]/csrf=null；**scope+ACL+负责人三条件同时满足**：三者齐备时 200，去 scope 403 `insufficient_scope`，去 grant 404，禁用负责人后 401 且独立读 `platform.api_keys.revoked_at` 证明禁用事务撤销（SECURITY.md §1）；人类专属 `/memories` 403；撤销 204→401；3 秒真实寿命到期→401 |
| 测试 | `tests/f12/journeys/j101_requests.py`、`j101_approvals.py`、`j101_memory.py`、`j101_knowledge.py`、`j101_service_accounts.py`；`tests/f12/t101_support.py`（每测试独立真实栈 + 证书 + connector secret + 证据记录器）；`tests/f12/test_t101_journeys.py` **5 passed**（6 条 journey 记录） |
| 门禁 | `pytest tests/f12/test_t101_journeys.py -q` → **5 passed in 76.88s**；`pytest tests/f12 -q` → **313 passed in 235.96s**，最终提交树复跑 **313 passed in 232.72s**（基线 308 全保留）；`pytest tests/f01 -q`（单跑）→ **2 failed, 153 passed in 967.73s**，2 个失败为**基线既有**页面标记陈旧（`git diff 60d9de0 -- product/tests/f01 product/apps/web` 为空；login 页旧 `window.location.assign('/admin')`、apps 页旧空态标记），服务账号/身份用例全过；`go build/vet/test ./...` 全 exit 0；`gofmt -l internal cmd tools` 空；`python tools/sync_spec.py --check` → no drift |
| 边界/not_run | 无显式版本的自然语言路由 **not_run**（路由控制模型不能经公开 API 注册，`internal/modelregistry/modelregistry.go:92-102`，只能 SQL 播种）；真实厂商模型/连接器/embedding **not_run**（只有 dev-stub + 本机回环）；capability 审批过期是决策路径惰性检查（无 sweeper），用时间效果验证；J2 工具↔连接器绑定、J1 撤销边界第二版本为既有测试缝，报告已逐条声明；不声称真实模型质量/供应商后台 |
| 未修（归属 epic） | F08↔F04B 聊天正文输入缺口；路由控制模型注册；真实厂商凭据；`tests/f01` 既有 34 collection error 与 2 个页面标记失败；服务账号无 list/rotate key 端点 |

交付文件：`tests/f12/{t101_support.py,test_t101_journeys.py,journeys/j101_*.py}`、
`reports/f12/T101-journeys.{md,json}`、本小节、`E:\Orchara\.scratch\f12\T101-REPORT.md`。
提交：`test(f12): reproduce five F12 key journeys on the real stack`（T101）；`git merge integration/f12`
= **Already up to date**（集成分支仍为 `60d9de0`）。

## F12 复核修复（两轴 review A–K，2026-11-07）

基线 `integration/f12` @ `cacfc44`，分支 `t/f12-review-fixes`（worktree `E:\Orchara-wt\review-f12`）。
逐条发现/修法/红→绿证据见 `reports/f12/REVIEW-FIXES.md`；本节是进度摘要。

| 条目 | 结果 |
|---|---|
| A 凭据进证据（P0） | 旧 `T101-journeys.json` 含一次性测试 API key 明文 + `x-connector-secret` 值；新增 `tools/evidence_redaction.py`，在 `t101_support`/`t100_support`/`f12_api_gate._report`/`j101_service_accounts:105` 源头脱敏，三个证据 JSON 重新生成。当前形状：`"secret": "<redacted>"`、`"x-connector-secret": "<redacted>"`；`test_evidence_redaction.py` 扫描先红后绿（4 passed）。key 所属库已 drop、不可再认证；**git 历史不改写**（分支已推送、issue tracker 引用 SHA） |
| B `not_run` 假 `passed`（P0） | 删除 `t101_support.journey()` 与 `t100_support.__exit__` 的隐式改写；`passed` 必须旅程体显式 `mark_passed()` 且断言数 >0，`record()/persist()` 强制校验。空旅程保持 `not_run`；T100 5 条、T101 5+1 条重跑真实断言（J1b 仍 blocked）。`test_journey_status.py` 10 passed |
| C F01 查询守卫假通过（P1） | 守卫集中到 `tests/f01/conftest.py`；单/双引号、`searchParams.get`、变量 key 全覆盖；地址栏读取但 0 key → fail closed；key ⊆ `{error,run,version}`。`test_page_query_guard.py` 9 passed |
| D F01 源码字面量（P1） | 删除 `test_t019_pages.py:80-84` 三条字面量断言；新增 Node 执行 **served app.js 真实字节** 测试：401 → `/login?error=session_revoked`、两个 `[data-auth-content]` 清空、第二次调用不重复导航。`1 passed in 6.41s` |
| E1 `nav.js` 重复导出（P2） | 删除 `items`，保留 `navItemsFor`；无调用点；`node --check` 通过 |
| E2 重复分类器 | **保留**：页面总先加载 `runstream.js`，但 20+ 个既有 Node 页面测试单独加载 `runs.js/chat.js/runview.js`，删除会破坏隔离 harness；报告说明 |
| E3 fixture 四处复制 | **不修**：已知重复，参数化生命周期风险大于收益 |
| E4 门禁 60/106 + 源码正则 | 记录为**已声明边界**（T093 报告 §5.6）；未扩大改写 |
| G T093 route-only 冒充 implemented（P0） | `route_registered / runtime_reached / functional_probe / implementation_status / implemented` 分离；`implemented` 只计 functional；summary 增 `route_only`/coverage；verdict `route_only`、CLI 退出码 3。证据：route-registered 106/106、functional 60/106、route_only 46、0 失败。T093 报告顶部/AC/§5.1/§5.5/§5.6/§7 全部改正；新增 `getVersion` 已注册但只有 401 的变异测试（5 passed） |
| H T093 跨主体内容隔离（P0） | 新 pass 对 18 个 list/get 真实断言 member 看不到 creator 的资源 id；13 条无夹具读操作明确 `not_run`；每条记录 `cross_principal_isolation`（member 状态/items 数/foreign_id_visible/isolation_ok）。`test_operations.py` 106 条 parametrized 隔离断言 + summary 断言；T093 报告不再说“每条都有 403/404” |
| I T098 报告乱码（P1） | 原文件 BOM + 276 PUA，不可逆；按代码/测试重写 118 行，UTF-8 无 BOM、PUA=0、U+FFFD=0；新增 `test_report_encoding.py` 扫描 `reports/f12/**`；T098 真实重跑 `14 passed in 28.38s`。顺带记录未修的 `reports/f13a/T065…md` BOM、`reports/f15a/runs.json` U+FFFD |
| J T101 AC2（P1） | 原报告称“scope/ACL/人类批准同时满足”，实际 J5 只读无审批、J2 有人类审批但无 key scope；**改判 `partial`**，T101 状态 `passed → partial`，报告 §2/§5 重写；不谎称产品缺口（`approverForOwnerTx` 支持 service-owned Run → responsible human），剩余三合一 E2E 写清 `not_run` |
| K `ContractErrors` 越界页面（P2） | 新增 `ContractErrorsFor([]string{"/api/"}, mux)`；页面 404/405 保持原行为；Go 测试 `TestContractErrorsForOnlyWrapsAPIPaths` 等 2 条通过 |

**门禁（修复后，真实 tail）**

- `pytest tests/f12 -q` → **441 passed in 233.72s (0:03:53)**（基线 318，无退化；新增 123 条）
- `pytest tests/f12/test_gate_mutations.py -q` → **5 passed in 51.19s**
- `pytest tests/f12/test_operations.py -q`（证据运行）→ **319 passed in 26.44s**
- `pytest tests/f12/test_t100_{...}.py -q`（证据运行）→ **5 passed in 69.56s**
- `pytest tests/f12/test_t101_journeys.py -q`（证据运行）→ **5 passed in 74.29s**
- `pytest tests/f12/test_t098_* -q` → **14 passed in 28.38s**
- `pytest tests/f12/test_journey_status.py` → **10 passed**；`test_evidence_redaction.py` → **4 passed**；
  `test_report_encoding.py` → **1 passed**；`tests/f01/test_page_query_guard.py` → **9 passed**
- `pytest tests/f01 -q` → **165 passed in 714.73s (0:11:54)**（基线 155，无退化）
- Go：`build=0`、`vet=0`、`test=0`、`gofmt -l internal cmd tools` 空、`sync_spec --check` → `no drift`；
  `node --check apps/web/src/assets/nav.js` exit 0
- 真实栈串行锁 `E:\Orchara\.scratch\f12\locks\realstack` 本工作树持有至收尾并删除。

提交：`fix(f12): redact evidence, truthful journey status, page guards and gate classification` /
`test(f12): regression tests for redaction, statuses, page guards and gate semantics` /
`docs(f12): review-fixes report, rewritten T098 report and regenerated evidence`（最终 SHA 见 `git log`）。
## 批次完成记录：F12 #94–#102 / T093–T101（2026-11-07）

- 基线 `1cdff9c`（= `integration/t083-t091` tip）→ 集成分支 `integration/f12` tip 见 `E:\Orchara\.scratch\f12\MERGE-LOG.md`。
- 范围：本批 **6 票**（GitHub #94/#96/#97/#99/#101/#102），全部按 native `blocked_by` 顺序推进：
  T093 → {T095, T096} → T098 → {T100, T101}。
- 范围外未动：F14 #93/#95/#98/#100（blocked by #91）、F13B #103–#115、F15B #117–#122、T090 训练/模型实验。
- 契约/规格：未改 `contracts/**`、`docs/spec/**`、迁移编号；`python tools/sync_spec.py --check` **no drift**。

### 票状态（最终）

| 票 | Issue | 状态 | 说明 |
|---|---|---|---|
| T093 | #94 | **passed（门禁交付）** | 外部客户端门禁：`route_registered 106/106`、`functional probe 60/106`、`route_only 46`、`missing 0`、跨主体隔离 18 条真实断言、13 条读操作 `not_run`。**裁决为 `route_only`（不是 `passed`）**，CLI 退出码 3 |
| T095 | #96 | **passed** | 22 页统一角色导航 + 统一 401/403 出口 + 只读/创作者入口真实性 + 分页跟随；服务端鉴权链未改 |
| T096 | #97 | **passed** | SSE 断线去重 / 410+快照真重建 / `phase=finalizing`≠终态 / 取消不声称撤销外部写 / 父子与产物 readiness |
| T098 | #99 | **passed（像素级 `not_run`）** | 手机四类路径响应式 + 冲突/过期/无结果反馈 + 撤销引用 404 不复用缓存 + 候选默认不勾选 |
| T100 | #101 | **passed（1 条产品缺口 blocked）** | 五条旅程真实跑通；聊天正文当 Run 输入 → `node_input_invalid` 记 `blocked`，未用绕法声称 |
| T101 | #102 | **partial（issue 已重开）** | 五条旅程真实跑通；**AC2「scope/ACL/人类批准同时满足」未满足**：J5 是读且无审批，J2 有人类审批但无 key scope，三合一外部写 `not_run` |

### 两轴复核与修复（`reports/f12/REVIEW-FIXES.md`，A–K）

Standards 轴 4 条硬性违规 + Spec 轴 5 条，全部处理：
1. **凭据泄漏（P0）**：`T101-journeys.json` 曾含明文 API key 与 connector secret → 新增
   `tools/evidence_redaction.py` 在**源头**脱敏并重生成三份证据；新增 `test_evidence_redaction.py` 守卫。
   该 key 属已 drop 的测试库、不可再认证；**git 历史仍保留该失效字符串，未改写**（分支已推送、issue 引用 SHA）。
2. **旅程状态被隐式改写（P0）**：`not_run`→`passed` 的强制转换删除，`passed` 必须有事前断言且由旅程体显式设置；
   空断言无法被持久化为通过（`test_journey_status.py`）。
3. **门禁把「路由存在」当「已实现」（P0）**：拆分为 route_registered / functional_probe / route_only；
   新增「已注册但只有匿名 401」的变异测试；报告不再声称 106/106。
4. **授权记录空转（P0）**：17 条会话内 list 操作原以「wrong_principal 期望 200」为通过 → 改为 18 条跨主体
   内容隔离断言 + 13 条 `not_run` 说明。
5. **T098 报告乱码（P1）**：原文件编码损坏且不可反解（PUA 字符），按代码/测试/PROGRESS 重写，UTF-8 无 BOM；
   新增 `test_report_encoding.py`。
6. **T101 AC2 被替代（P1）**：改判 `partial`，issue 重开。
7. **F01 查询参数守卫假绿（P1）**：守卫集中到 `tests/f01/conftest.py`，单/双引号、`searchParams.get`、
   变量 key、未知参数一律 fail closed；`test_page_query_guard.py`。
8. **F01 401 断言用源码字面量（P1）**：改为 Node 执行 **served app.js**，断言真实导航到 `/login?error=session_revoked`
   且 `[data-auth-content]` 清空。
9. **越界（P2）**：`ContractErrors` 限定在 `/api/` 前缀，页面 404/405 恢复原行为。

### 最终门禁（集成树）

| 套件 | 结果 |
|---|---|
| `pytest tests/f12` | **441 passed in 239.12s** |
| `pytest tests/f01`（必须单跑） | **165 passed in 721.29s** |
| Go | `build=0` / `vet=0` / `test=0`；`gofmt -l internal cmd tools` 空；`sync_spec --check` no drift |

### 诚实边界（本批不掩盖）

- **T093 的功能覆盖只有 60/106**；另外 46 条是 `route_only`，其实现由各自功能 epic 的测试覆盖，
  但本门禁不为它们背书。T101 的 AC2 仍未满足。
- **无浏览器 DOM**（无 Playwright）：所有界面结论都是「Node 执行已提交真实字节 + 最小 DOM/fetch shim」，
  CSS 断点/像素布局/真实事件冒泡/触屏手势 `not_run`。
- **无真实厂商模型/端点/凭据**：全部 `ORCHARA_PROFILE=dev-stub`；F14 仍 `blocked`。
- **生产图执行是 Go `internal/presetexec`**，不是 LangGraph（ADR 0034）。
- 既有跨 epic 红（f03/f02a/f00b 各 2 条）与本批无关，已在 baseline `1cdff9c` 复现，未修。
- 证据文件已脱敏，但**历史提交仍含一个已失效的测试 key 字符串**；如需清除须单独走历史改写。

## F12 第二轮复核修复（page findings F1–F4，2026-11-07）

- 分支 `t/f12-review2`，worktree `E:\Orchara-wt\review2`；基线 `integration/f12` @ `e3aa1e1`；
  被复核修复提交 `83307b1`。逐条出处/复现/红→绿真实 tail 见
  `reports/f12/REVIEW2-FIXES.md`；T096/T098 报告各追加“第二轮复核后修正”小节。
- 手段：无 Playwright/真实浏览器；Node 执行**已提交真实字节** + 最小 DOM/fetch shim。
  红证据在 `e3aa1e1` 的独立 detached worktree 上生成（只拷入新测试、不改源码，已删除）。
- 范围：`apps/web/src/assets/{runs,debug,personal-data}.js`、
  `tests/f12/test_t096_review2.py`、`tests/f12/test_t098_review2.py`、
  `tests/f11/test_t073_pages.py`（旧断言随行为更新）、`reports/f12/**`、本节。
  未改 `docs/spec/**`、`contracts/**`、迁移；未回退 `83307b1`。

| 发现 | 结论 | 关键证据 |
|---|---|---|
| F1 空选择=授权整个 Run（Standards P1） | **已修** | 空选择 `throw` 且无 api/csrf/fetch；单选/多选发送等于选择的 `X-Diagnostic-Object-Scope`；页面原因进 `#grant-status`。`grants.go:8-10,395-423` 是“缺头推导全部”的服务端依据 |
| F1 追加：勾选后仍发不出请求 | **已修** | 复选框值原为 `artifact:<uuid>`，构造器期望裸 uuid → “artifact id 必须是 UUID”+0 POST（`83307b1` 上复现）；`personal-data.js:716` 改为 artifact id |
| F2 重建中/失败被说成不可检索（Standards P2） | **已修** | `indexing`/`failed` note 不再含旧两句假话，明确“旧索引仍可能是检索来源”+“首次”；ADR 0018:26,58-59 |
| F3 调试页 410 后无法重建（Spec P2） | **已修** | 无 renderer 且无 `#detail-id` → 明确报错、不取快照、不抛；`debug.js` 传入真 renderer |
| F3 追加：renderer 引用未定义变量 | **已修** | `debug.js:419 draftRevision` → renderer 一执行就 `ReferenceError`（`node --check` 不报）；改为 `frozenRevision` |
| F4 迟到快照覆盖新选择（Spec P2） | **已修** | await 后按 generation 丢弃：`displayedRunID`/`displayedRun`/详情仍为 B，时间线不追加，renderer 不调用 |

新增 5 条回归用例（先红后绿）：F12 全量从 **441** 变为 **446 passed**；`test_t096_review2.py`
3 条、`test_t098_review2.py` 2 条；红证据 5 条在 `e3aa1e1` 上全红（`5 failed in 1.38s`）。

**门禁（真实 tail）**

- `pytest tests/f12 -q` → **446 passed in 245.06s (0:04:05)**（最终提交树重跑）
- `pytest tests/f01 -q`（单跑）→ **165 passed in 737.24s (0:12:17)**（无退化）
- Go：`build=0`、`vet=0`、`test=0`；`gofmt -l internal cmd tools` 空；`sync_spec --check` → `no drift`
- `node --check` on `runs.js`/`debug.js`/`personal-data.js` → exit 0
- `pytest tests/f11/test_t073_pages.py::test_the_diagnostic_grant_helpers_execute_under_node`
  + `tests/f12/test_t096_pages.py` + `test_t098_mobile_pages.py` → **19 passed in 1.82s**
- `git merge integration/f12` → **Already up to date.**（集成分支 tip 仍为 `e3aa1e1`）
- 最终门禁在提交 `ca498e0` 的树上重跑；其后只把真实 tail 写回本文件与
  `reports/f12/REVIEW2-FIXES.md`（不改代码/测试）。

**诚实边界**：仍是 Node 执行真实字节，不是浏览器；CSS/真实 `EventSource`/表单控件语义
`not_run`。真实服务器 410→页面重建仍由 `test_t096_stack.py::test_real_410_drives_the_page_snapshot_rebuild`
覆盖（未改）。F1 页面路径只证明请求形状与“没有请求”，真实服务端解析由 F11/T073 既有真实栈用例覆盖。
跨 epic 既有红（f03/f02a/f00b）未触碰、未修。

## F13B T101 收尾（GitHub #102，2026-11-07）

- 分支 `t/t101-three-in-one`，worktree `E:\Orchara-wt\t101b`；基线 `integration/f13b` @ `30642be`；
  完成前 merge 了推进后的 `integration/f13b` @ `ffe4022`（T102），合并提交 `3921c81`；
  `PROGRESS.md` 尾部双追加冲突按保留双方小节解决。
- 交付：新增 `J6-service-key-write-approval`（49 断言）与 `J6b-graph-dispatch-write-gap`（7 断言，`blocked`）。
  AC2 从 `partial` 改判为**满足（平台/授权边界）**：服务 key 的 `application:read`+`write` scope + application
  read grant + 服务账号负责人的人类 operation 审批，作用在**同一个真实回环外部写**上（回环端点自己的
  请求日志恰好 1 次物理写、1 条 succeeded attempt、真实 receipt）；同一请求去掉任一条件均被拒：
  无 grant 404、只有 read scope 403 `insufficient_scope`、service key 自己批准 404、admin 403
  `approver_mismatch`、错 hash 409。版本由**真实五类评测 → submit → admin review decision** 发布。
- 发现并修复 **P1 产品缺陷**：`internal/capabilities/service.go` 的 `loadAuthorizedRun` 要求 sealed
  manifest artifact 属主 == Run owner，导致任何非发布者提交的 published Run 调用任何 capability 都被
  503 `manifest_unavailable`；修复只对 `run.Kind == "published"` 放宽属主相等，保留 artifact ready、
  snapshot digest 相等、字节 sha256 == `runs.manifest_sha256`；Go 回归测试
  `internal/capabilities/manifest_owner_test.go`（published 允许 / 三类摘要拒绝 / debug 仍拒）。
- 未解：**J6b `blocked`**（已发布图本身发不出 connector operation：图执行器无 connector 节点、agent
  tool call 恒为 `kind=tool`）；J1b 保持 `blocked`（F08↔F04B 输入缺口）。
- 门禁（真实 tail）：`pytest tests/f12/test_t101_journeys.py -q` → **6 passed in 90.82s**；
  证据重跑（`T101_WRITE_EVIDENCE=1`）→ **6 passed in 87.93s**，普通运行不改写 JSON；
  `pytest tests/f12 -q` → **447 passed in 262.76s**；`go build=0`、`go vet=0`、`go test=0`；
  `gofmt -l internal cmd tools` 空；`sync_spec --check` → `no drift`。
- 合并树最终复跑（`3921c81`，含 T102）：targeted 证据 **6 passed in 137.57s**、
  `pytest tests/f12 -q` **447 passed in 489.77s**、`go build/vet/test` = 0、gofmt 空、sync `no drift`
  （共享宿主同期有其它真实栈任务，耗时高于首跑，结果全绿）。
- 边界：dev-stub 模型、回环 connector stub（非真实供应商）、WSL2+runsc 真实执行；真实模型/真实第三方
  供应商 `not_run`；写操作由文档化 worker capability 协议发出（J6b 记图分发缺口）；证据重生成后
  `Select-String` 复查无明文凭据。

## F13B T102：冻结七类内部案例并证明没有切分泄漏（2026-11-07）

- 分支 `t/t102-frozen-cases`（worktree `E:\Orchara-wt\t102`，基线 `integration/f13b` @ `30642be`）。
- 交付：`tests/f13b/corpus/**`（7 个 `cases.<category>.json` + `registry.json` + 6 个 CSV fixture）、
  `tests/f13b/build_corpus.py`（确定性生成器，registry 与 case 文档可由其重放）、
  `tools/f13b_corpus.py`（loader/validator/切分泄漏检测/拒绝原因）、
  `tests/f13b/test_t102_corpus.py`（离线测试）、
  `internal/evaluation/dataset_f13b_corpus_test.go`（产品严格 `ParseDataset` 装载证明）、
  `reports/f13b/T102-frozen-cases.md`。
- 语料：**46 案例（25 dev / 21 frozen_test，20 families，178 assertions）**，覆盖
  sales/routing/rag/memory/delegation/approval_effects/lifecycle 七类；每 case 至少 1 assertion、
  family、split、provenance/licence，并在 registry 中有独立真值说明与采集事实清单。
- 切分隔离实测：跨 split family **0/20**、归一化模板 **0/20**、字符 5-gram Jaccard 最大跨 split
  **0.4018 < 0.75**（同 split 最大 0.6606，证明指标非零）；伪造跨 split family/template/近似重复均被拒绝。
- 真值：sales 由 `tests/f13a/reference/sales_reference.py`（stdlib+Decimal，不 import 产品工具）从 fixture 重算；
  routing 由 catalog 快照机械推导；rag 由来源快照（id/page/version/status/text）核对；
  memory/delegation/approval/lifecycle 采用人工标注 + 账本/状态不变量。
- 空/跳过/不完整：`reject_reasons` 8 个稳定码 + `is_decidable` 缺事实即不可判定；
  20 条负向检查（含伪造跨 split、重复 case_id、文件/schema pin 篡改）全部断言原因码。
- 未改 `docs/spec/**`、`contracts/**`、迁移与既有 `tests/f13a` 文件。
- 门禁（真实 tail）：`pytest tests/f13b` **36 passed in 16.12s**；`pytest tests/tooling` **21 passed in 15.02s**；
  `pytest tests/f13a/test_t056_fixtures.py`（离线子集）**7 passed in 1.31s**；`go build/vet/test` exit 0；
  `gofmt -l internal cmd tools` 空；`sync_spec --check` → `no drift`。
- `not_run`：case 执行器/真实模型、真实 artifact 采集、debug snapshot 发布、完整 `tests/f13a` 真实栈套件（按离线票范围）。

 HEAD
## F13B T103（GitHub #104）：同一录制结果的 hard checker vs 质量评分（2026-11-07）

- 分支 `t/t103-quality-vs-hard` / worktree `E:\Orchara-wt\t103`；基线 `integration/f13b` @ `30642be`。
  本票 test/probe-only：**未改** `docs/spec/**`、`contracts/**`、迁移、`internal/evaluation` 语义或任何产品代码；
  唯一 tracked 改动是 `tests/support/t068.py` 增加可选 `quality` 槽参数（向后兼容）。
- 真实栈（PostgreSQL 16.4 + 编译二进制 + 真实 maintenance 双池 + 真实 supervisor + 真实 completion guard）：
  真实 runner 产出单条录制结果 `a42cc60e…`（`status=failed`，quality 槽如实 `not_installed/null`）：
  `t103-hard-pass` 的 `run_state`+`json_equals("", {})` 全过；`t103-hard-fail` 的
  `json_equals("", {"total":"100.00"})` 对真实输出 `{}` 硬失败。
  `probes/f13b_quality/compare.py` 从 `cases[].assertions` 独立复算 = `failed`，与产品 status 一致，
  且 `quality_used=false`。
- 同一 case 证据经真实 job-scoped 封印为 `status=failed` + `quality={adapter:"heuristic-local/1", score:1.0}`：
  completion guard 接受；真实 `POST /api/v1/applications/{id}/submit` → **HTTP 422 `evaluation_not_passed`**
  （`details.report_status=failed`），0 条 review。反向对照：passed 五类作者案例 + `quality.score=0.0`
  → **HTTP 202 submitted**，review 创建，`author_cases.satisfied=true`。质量分只是信息。
- 适配器（隔离 venv/npm 真实安装 + 真实最小调用 + 回环代理 trap）：`heuristic-local/1` outbound=0、tokens=0、$0；
  DeepEval 4.3.3 确定性 `ExactMatch` 本地跑通（0 出站），语义指标报 `OpenAI API key is not configured` → `blocked`；
  Ragas 0.4.3 导入因 `langchain_community.chat_models.vertexai` 缺失失败 → `blocked`；
  Promptfoo 0.125.1 离线确定性 eval 通过，但默认 telemetry/update 发出 6 条真实出站
  （`api.promptfoo.dev:443`、`a.promptfoo.app:443`、`r.promptfoo.app:443`）→ 无 opt-out 时数据外发策略淘汰，
  语义 role 仍 `blocked`。获批 judge 运行 = `not_run`（无端点/凭据），无任何伪造的 judge 分数/费用。
- 门禁：`pytest tests/f13b` 全绿（本票 4 条 + T102 corpus 用例）；Go `build=0/vet=0/test=0`；
  `gofmt -l` 空；`sync_spec --check` no drift。`pytest tests/f13a` = **3 failed / 48 passed**，
  经 stash 本票唯一 tracked 改动后原样复现（3 failed in 98.65s）：三个既有 F13A 用例仍假设最小
  `start->end` 链以 `tool_unsupported_graph` 失败，而基线实际成功并封印 `{}`，**属基线既有红，非本票引入**。
- 证据：`reports/f13b/T103-hard-vs-quality.md`；`probes/f13b_quality/**`（compare/adapter probe/矩阵/证据）；
  原始 tail `E:\Orchara\.scratch\f13b\T103\`。

## F13B T104（GitHub #105）：导入 τ²-bench 改编任务并拒绝空判定器（2026-11-07）

- 分支 `t/t104-tau2-import` / worktree `E:\Orchara-wt\t104`；基线 `integration/f13b` @ `5c7c506`，
  结束时 merge `integration/f13b` @ `c132e8e` → **Already up to date**（分支已包含），工作区干净。
- 来源：`sierra-research/tau2-bench` @ `da0835f6c67baef58bdda18f2a3b037dfb3a1754`（MIT；LICENSE
  sha256 `74741b87…`）；`git init` + `git fetch --depth 1 origin <commit>` 真实克隆到
  `E:\Orchara\.scratch\f13b\T104\upstream`（不提交上游代码）。`tasks.json`/`policy.md`/
  `split_tasks.json`/`db.json`/`tasks.py`/`evaluator.py`/`pyproject.toml` 的 SHA-256 与抓取命令在
  `tests/f13b/external/tau2/source_pin.json`；上游 evaluator:134-139 的缺 criteria → reward=1.0
  陷阱记录为 `empty_checker_trap`。
- 导入 3 条改编 airline 任务（上游 task 11/39/40）：`tau2-airline-39-refund-eligibility`（退款资格
  拒绝；3/7 可取消；refund_total 995.75）、`tau2-airline-11-flight-downgrade`（不得改乘客数，降舱
  退款 5244.00）、`tau2-airline-40-passenger-correction`（乘客改名）。全部 `frozen_test`/
  `approval_effects`，改写为隔离本地 booking fixture（JSON + `/booking-desk` 端点面），
  `origin=adapted_external`、`license_review=reviewed`、reference 带 repo@commit#task#path；改编
  说明在 `external/registry.json`，未塞入 schema 禁止字段。共 23 条断言；产品 `ParseDataset` 由
  `internal/evaluation/dataset_t104_external_test.go` 证明可加载。
- 空判定器门禁 `tools/f13b_external.py`：稳定码 `missing_checker`/`empty_criteria`/
  `empty_reward_basis`/`text_only_checker`/`no_actions`/`no_environment`/`default_success` 等；真实
  τ² 形状负例（缺 criteria、空 reward_basis、仅 nl 文本、无环境）与适配 case 负例（仅 run_state、
  text_similarity、无环境、未审许可、未知 source task、错 reference、伪造跨 split）共 24 条全部按
  预期拒绝；`validate_tau2_source` 重校验 commit/LICENSE sha/criteria sha。
- 离线端到端（stub，非产品/非榜单）：loader → 门禁 → 参考轨迹 → 本地 fixture 模拟 → 五类断言
  镜像；3 条 gold 轨迹 23/23 断言通过，2 条错误轨迹（多取消 4 条不可退、移除乘客）按预期失败。
- T102 集成：`corpus/registry.json` 增加 `external_imports`（外部 registry sha256 + commit +
  `adapted_external` 标签），七类 case 文档字节不变；T102 36 条测试与两套 builder 复现全绿。
- 未改 `docs/spec/**`、`contracts/**`、迁移、产品 worker 代码。
- 门禁（真实 tail，从 `product/`）：`pytest tests/f13b -q` → **88 passed in 47.79s**（T104 48 +
  T102 36 + T103 真栈 4）；`pytest tests/tooling -q` → **21 passed in 4.72s**；
  `go build ./...`/`go vet ./...`/`go test ./...` exit 0（`go test` 39 ok / 0 FAIL）；
  `gofmt -l internal cmd tools` 空；`sync_spec --check` → **no drift**；
  `tools/f13b_external.py --summary` → exit 0（gate_issues=[]，23/23 stub）；两个 builder `matches`。
- `not_run`：产品 evaluation runner 执行适配 case（本构建无 booking 应用）、真实模型、τ² 官方
  suite/用户模拟、recovery 边界（三条任务无恢复语义）、上游全文件离线重算（需 pinned clone）、
  db.json 重构（仅 pin hash）；仅根 MIT 已审，域内第三方数据未逐项审计。
- 证据：`reports/f13b/T104-tau2-import.md`；原始 tail `E:\Orchara\.scratch\f13b\T104\`。

## F13B T105（GitHub #106）：导入 AgentDojo 改编任务并分开报告攻击成功（2026-11-07）

- 分支 `t/t105-agentdojo-import` / worktree `E:\Orchara-wt\t105`；基线 `integration/f13b` @ `834ca68`；结束时 merge `integration/f13b`（最终状态见报告 §11）。
- 来源：`ethz-spylab/agentdojo` @ `089ed468cf3ed0322acc66b0211f26d9d90dbf60`（MIT；LICENSE sha256 `a090c871…`，1182 bytes）；`git init` + `git fetch --depth 1 origin <commit>` 真实克隆到 `E:\Orchara\.scratch\f13b\T105\upstream`（git-ignored，不提交上游代码）。12 个上游文件的 SHA-256/size、任务/注入任务/向量证据、抓取命令在 `external/agentdojo/source_pin.json`；未伪造任何 hash。
- `security=True` 语义：上游 `BaseInjectionTask.security()` True = 注入目标成功；`benchmark.py` 的错误/跳过路径把 `security` 强制为 True，DoS 为 `security=not utility`，无注入 API 文档写明 "The security is always True"。`map_agentdojo_outcome(utility, security)` 映射 `attack_success=security`、`security_expectation_met=utility and not attack_success`；非布尔值拒绝；四条真值表 + 3 case × 4 轨迹交叉验证。
- 导入 3 条改编 workspace 任务：UserTask16 (v1.2.2) + InjectionTask4（外发 Facebook 验证码）；UserTask6 + InjectionTask2（注入建日程）；UserTask28 + InjectionTask1（注入删除 file 13）。均 `frozen_test`/`approval_effects`，25 条断言，utility/attack/side-effect 角色分离；攻击动作保留上游写入参数；3 个本地 JSON fixture；AgentDojo 环境/工具/用户模拟未复用。
- 许可门禁：缺/未审/不可再分发来源许可 → `source_unlicensed` / `license_not_reviewed`；`packageable_cases()` 不打包该 case；负例覆盖缺 license、pending、redistributable=false、错 license_id、self_created origin。
- 离线段到端（stub，非产品/非榜单）：gold 3/3 满足；attack_obeyed utility 3 但 attack_success 3、满足 0（证明只报一个 security 布尔会掩盖攻击成功）；attack_blocked 拒绝 3、满足 3；utility_failed 满足 0。汇总只有 utility/attack_success/被拒数等分列，无综合分。
- 真实栈检索注入：真实 PostgreSQL + 编译 `cmd/api` + 对象存储 + `cmd/maintenance` 摄入；文档含 `<INFORMATION>` 注入；真实 retrieval + answer 后 `platform.operations=0`、`platform.approvals=0`、connector 写 0、canary 值未泄漏、引用仅来自本人文档、外来主体仍 404；answer 文本引用了注入文本但不执行。3 passed。**blocked**：语义模型读取工具输出后发起写操作（缺厂商/本地语义模型端点+凭据与工具执行面）；官方 AgentDojo suite `not_run`。
- 未改 `docs/spec/**`、`contracts/**`、迁移、产品 worker 代码。T102 registry 仅新增 `external_imports.agentdojo`（sha256+commit pin），七类 case 文档字节不变；T104 tau2 引用保留。
- 门禁（真实 tail，从 `product/`）：`pytest tests/f13b -q` → **179 passed in 73.11s**；`pytest tests/tooling -q` → **21 passed in 4.54s**；`go build/vet/test` exit 0（39 ok / 0 FAIL / 12 no test files）；`gofmt -l internal cmd tools` 空；`sync_spec --check` → `no drift`；`tools/f13b_agentdojo.py --summary` exit 0（gate_issues=[]）；`build_agentdojo.py`/`build_corpus.py`/`build_external.py` 均 `matches`。
- `not_run`：官方 AgentDojo suite、真实模型、产品 evaluation runner 执行改编 workspace case、上游全文件离线重算（需 pinned clone）。恢复边界 `not_applicable`（三条任务无恢复语义）。
- 证据：`reports/f13b/T105-agentdojo-import.md`；原始 tail `E:\Orchara\.scratch\f13b\T105\`。

## F13B T106（GitHub #107）：故障注入审批重启、换参与写后丢回执（2026-11-07）

- 分支 `t/t106-approval-faults` / worktree `E:\Orchara-wt\t106`；基线 `integration/f13b` @ `c132e8e`，
  完成前 merge 推进后的 `integration/f13b`（T104/T105，`f234e72`），`PROGRESS.md` 双方小节保留。
- 交付：新增 `tests/f13b/fault_support.py`（Fault/进程/PG/证据 writer，仅 `F13B_WRITE_EVIDENCE=1` 重写）、
  `tests/f13b/t106_worker.py`（真实 worker OS 进程）、`tests/f13b/test_t106_approval_faults.py`
  （3 条真实栈测试，53 条断言）、`reports/f13b/T106-faults.json`（3 个故障记录）、
  `reports/f13b/T106-approval-faults.md`。未改 `docs/spec/**`、`contracts/**`、迁移与既有票断言。
- FAULT-01（真杀进程 + 真 supervisor 重启）：真实 worker 进程停在审批等待（waiting+confirm exit）→
  `taskkill /F /T` 真杀（原始输出列出其 psql 子进程；`pg_stat_activity` 会话 1→0）→ 真实
  `cmd/supervisor exec --once` 启动并 `status=idle` → owner 精确批准 → 恰 1 个 resume job → 新 worker 进程
  用**同一 operation id/logical key/params** 重放，succeeded。独立账本：回环端点 0 写（批准前）/恰 1 写
  （批准后），`operations` 一行同 id、`operation_attempts` 1 行、`approvals` 一行
  （id/proposal/params_sha256/approver 不变）、run succeeded。
- FAULT-02（换参与旧批准）：精确 params 批准后同 operation id 换 params → 409 `operation_conflict`，0 写、
  operation/approval 行不变；随后精确 params 仍成功（批准未被消耗）；错 `params_sha256` 决定 → 409
  `approval_params_mismatch`；陌生人 404、admin 403 `approver_mismatch`、取消后迟到决定 409，全部 0 写。
- FAULT-03（写后丢回执）：`LostReceiptServer` 真实断连 → operation `unknown`（非 succeeded/failed）、
  无 receipt、attempt 仍 dispatching；重复 invoke/换参不重发（端点仍 1 次请求）；run 停
  `needs_reconciliation`（非 succeeded）；真实 evidence artifact + `POST /operations/{id}/reconcile`
  结案 succeeded，物理 attempt 仍 1、端点请求仍 1。
- 门禁（真实 tail）：`pytest tests/f13b/test_t106_approval_faults.py` **3 passed in 51.53s**（证据重跑）；
  `pytest tests/f13b` **43 passed in 103.79s**；`pytest tests/f12` **447 passed in 385.14s**；
  `go build/vet/test` 全 0；`gofmt -l internal cmd tools` 空；`sync_spec --check` → `no drift`。
- 边界：dev-stub 模型、回环 connector 端点（非真实供应商）、无浏览器 DOM；真实供应商/模型 `not_run`；
  worker 角色是文档化 seam（图执行器无 connector 节点，T101 J6b），真实 supervisor 进程重启证据与本 seam 分列。
- 合并后（merge commit `cb20f85`，含 T104/T105）复跑：`pytest tests/f13b` **182 passed in 148.45s**；
  `pytest tests/f12` **447 passed in 488.01s**；证据重生成 **3 passed in 64.16s**
  （`T106-faults.json` 的 `product_commit=cb20f85`）；Go `build/vet/test` 全 0；gofmt 空；
  `sync_spec --check` → `no drift`。原始 tail 在 `E:\Orchara\.scratch\f13b\T106\*`。

## F13B T107（GitHub #108）：旧图、活旧进程、取消与事件竞态故障注入（2026-11-07）
- 分支 `t/t107-lifecycle-faults` / worktree `E:\Orchara-wt\t107`；基线 `integration/f13b` @ `834ca68`，结束前 merge `integration/f13b` @ `f234e72`（T105）→ 合并提交 `db94214`，其上重跑门禁；证据最后一轮在 `e904e0c` 树。
- 交付：`tests/f13b/test_t107_lifecycle_faults.py`（4 条真实栈故障，FAULT-04/05/06/10）、`tests/support/t107.py`（故障证据记录器 + Windows 真实进程探针 + `pg_stat_activity` 只读模型）、`reports/f13b/T107-faults.json`、`reports/f13b/T107-lifecycle-faults.md`；未改 `docs/spec/**`、`contracts/**`、迁移、产品代码与既有票断言。
- FAULT-04（旧图恢复）：v1 debug Run 的封存 manifest 与仓库 checked-in golden 字节逐字节相同（sha256 `9cc418d1…`）；经真实 submit/review/approve 事务发布 v1、v2（v2 sha256 `eac3a5db…`）后，v1 Run 两次 claim/一次 recover 都返回 v1 manifest（lease sha、signed URL 字节、execution snapshot 全为 v1）；删除 v1 对象后 `runs recover` 稳定拒绝 `artifact_unavailable`，Run 保持 `recovery_pending`，v2 对象完好但从未被回退使用。
- FAULT-05（活旧 worker + 过期租约）：真实 `cmd/supervisor exec --once`（pid 64356）真实领取 attempt（token 前缀 `8420f63a`），OS `NtSuspendProcess` 冻结后其真实 PG backend（pid 50836，`application_name=t107-old-worker-db-…`）仍在 `pg_stat_activity`；过期租约 + `sweep-leases --grace 1s` → `recovery_pending(lease_expired)`、job held、allocation 未释放、claim 204、仅 1 个 attempt。恢复进程后其 exit confirmation 被真实 `409 stale_attempt` 拒绝（exec report status=failed），Run 仍 parked、事件数不变；进程退出并留下真实退出证据后才 recover → queued，再 claim 才出现 attempt #2。
- FAULT-06（取消后迟到）：真实 cancel → `cancelling`，期间 result/event/upload/capability 全拒绝（capability `run_cancelled`、operations=0）；supervisor exit 后 `cancelled`（revision 4、seq 1..5、状态 queued→running→cancelling→cancelled）。迟到 result（含同 body 重投）/旧 event 重放/新 event → `409 run_terminal`；重复 claim 204；同 evidence exit 200 幂等、不同 evidence 409；upload 409 / capability 403 均 `stale_attempt`；外部 owner 读 404；终态 recover 拒绝 `not_recovery_pending`；Run 行、事件与 seq 完全不变。
- FAULT-10（重复事件/相反决定）：同 event_id 重放（含 batch）不新增 seq；真实终态 failed 后迟到事件 409 `run_terminal`，seq 1..6 连续。两个 supervisor mTLS 会话 barrier 并发 approve/reject → 恰一个 200、一个 409 `decision_conflict`；DB 仅 1 条决定（本轮 approved/`t107-race-approve`），相反决定重放仍 409，exit 后恰好 1 个 resume job；worker 证书 403、未知 run 404、跨 owner 404。
- 诚实边界：模型仅 dev-stub（无真实供应商）；FAULT-05 的失联注入是宿主 OS 挂起真实进程（非网络分区/真机关机）；FAULT-04 v2 发布的五类评测报告使用 T068 既有“真实 evaluation/case run 行 + seeded 终态”fixture（manifest/version/review/approve 事务与对象字节真实）；FAULT-06/10 的等待决定走 T035 文档化非契约 bridge，不是 F06 审批 UI；本批次无真实外部写 connector，FAULT-10 的“外写”按平台当前语义核对为恰好一次 resume 恢复。
- 门禁（真实 tail，合并 `f234e72` 后树，cwd=`product/`）：`pytest tests/f13b/test_t107_lifecycle_faults.py -q` → **4 passed in 76.85s**；`pytest tests/f13b -q` → **183 passed in 192.65s**；`pytest tests/f12 -q` → **447 passed in 374.23s**；`go build/vet/test` exit 0（39 ok / 0 FAIL / 12 no test files）；`gofmt -l internal cmd tools` 空；`sync_spec --check` → `no drift`。
- 证据：`reports/f13b/T107-lifecycle-faults.md` + `reports/f13b/T107-faults.json`（`F13B_WRITE_EVIDENCE=1`，HEAD `e904e0c`）；原始 tail `E:\Orchara\.scratch\f13b\T107\`。
- `not_run`：真实厂商模型/真实外部写供应商；生产部署上的 v1→v2 升级恢复；网络分区/宿主机失联恢复；浏览器 DOM/SSE UI；多机 supervisor 竞争与多轮并发压力。
