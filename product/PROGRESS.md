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
| T068 | F02B | 创作者提交已评测快照，管理员批准同一 hash 内容并原子创建版本 | passed | `reports/f02b/T068-publication-review.md`、`docs/adr/0021-publication-review-and-versions.md`、`db/migrations/0043_publication_reviews_and_outbox.sql`（review 决策/冻结绑定列 + 一个 live submitted review 的 partial unique + `application_versions` 撤销三字段/CHECK/`UNIQUE(review_id)` + 契约 `platform.outbox`）、`internal/catalog/{publication,publication_cases,publication_report}.go`（submit 绑定当前 manifest hash 与 sealed report、T062 case-run 复查、五类作者案例门禁、冻结快照、幂等；approve 单事务 version+version_dependencies+active 指针+outbox+audit；reject；list/get ACL；`RecheckFrozenDependencies`；`ResolveRunnableVersion`）、`internal/catalog/manifest.go`（依赖 loader 抽 queryer + 冻结闭包复查）、`internal/api/{publication.go,server.go}`（4 个契约 operation + 非契约导出预览）、`internal/evaluation/report.go`+`internal/background/evaluation.go`（共享严格报告解码）、`apps/web/src/{publish.html,reviews.html,assets/publish.js,assets/reviews.js}`、`tests/f02b/`（18 用例：迁移 5 + submit/review 9 + pages 4；含真实 runner e2e、并发审批、旧版本字节不可变）、`tests/support/t068.py`；真实 PG 16.4 / 编译二进制 / 真实 HTTP+CSRF+mTLS / 真实 sealed 字节 / 真实 WSL2+runsc（e2e）/ Node；`pytest tests/f02b` **18 passed ×2**（290.87s / 后续背靠背轮，见报告 §3）、`go build/vet/test`、`gofmt -l`、`sync_spec --check` 全绿、`tests/tooling`/`tests/f02a`/`tests/f13a -k "t052 or t056 or t062"` 见报告 §3；not_run：撤销端点（T070）、published Run 创建（T074）、child run（T075/T078）、outbox 消费者/目录索引（T077/T082）、浏览器 DOM、全量 `pytest tests`；未改 `docs/spec/**`/`contracts/**`；合并提交见报告 |
| T070 | F02B | 撤销问题版本并阻止陈旧目录继续启动 | passed | `reports/f02b/T070-version-revocation.md`、`docs/adr/0022-f02b-version-revocation.md`、`internal/catalog/revocation.go`（`ValidateRevokeVersionRequest` 严格两字段体；`RevokeVersion` 单事务锁定 version+application，`revoked_at IS NULL` CAS 自然幂等（契约无 Idempotency-Key），只写 `revoked_at/revoked_by/revoke_reason`；活动指针置 NULL **不重指**，previous 记入 outbox+audit；`stop_existing` 选中非终态 Run，`cancel_requested_at`+`error_code=version_revoked`（first-write-wins）并经 `execution.AppendRunStatusTx` 追加一条 `run.status`，**不写 Run 终态**；`version.revoked` audit）、`internal/catalog/outbox.go`（唯一 `insertOutboxTx`，T068 发布与 T070 撤销共用；`catalog_index/op=revoke` payload 含 previous_active_version_id，无消费者）、`internal/catalog/versions.go`（`ListVersions`/`GetVersion` 加 `revoked_at IS NULL`；契约 Version 无状态字段 → 省略/404）、`internal/api/revocation.go`+`server.go`（契约 `POST /versions/{id}/revoke` admin+CSRF 200 Accepted、定位 422/404；非契约 `GET /versions/{id}/runnable` 薄封装 `catalog.ResolveRunnableVersion`：200/409 `version_revoked`/409 `version_archived`/404）、`apps/web/src/{versions.html,assets/versions.js,apps.html}`（管理员版本/撤销页 + 目录“不可用：没有生效版本（未发布或已撤销）”，Node 执行纯函数）、`tests/support/t070.py`、`tests/t070/`（12 用例：真实编译器 preview 封存的真实对象字节 + 真实 PG 16.4 + 编译二进制 + 真实 HTTP 会话/CSRF + 真实 seeded `published` 快照/Run + Node）；`pytest tests/t070` **12 passed ×2**（合并 T068 最终树 153.08s / 150.00s）、`pytest tests/f02a` **104 passed**（1107.33s）、`pytest tests/tooling` 21 passed、`go build/vet/test ./...`/`gofmt -l`/`sync_spec --check` 干净；无新迁移（撤销三列/CHECK/outbox 由 T068 `0043_publication_reviews_and_outbox.sql` 提供；合并时 T068 把基线 0042 重编号为 0043，避免与 T069 的 0042 重号）；not_run：T074 真实 published Run 创建与创建时守卫、T077 outbox 消费/目录索引、queued claim 读取消意图、浏览器 DOM、全量 `pytest tests`（orchestrator）；未改 `docs/spec/**`/`contracts/**`；合并提交见报告 §3.6 |

P01 venv 说明：worktree 内 `.venv` 不在 git，重建因环境 TLS CA bundle 问题失败并删除空
venv；门禁用 `ORCHARA_P01_PYTHON` 指向主 checkout 的 P01 锁定 interpreter。测试缺失时
`pytest.fail`（不 skip）。

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

## F02B 发布审核与不可变版本（T068，2026-11-05）

| 项 | 内容 |
|---|---|
| 提交 | `POST /applications/{id}/submit`（owner+creator+CSRF，`Idempotency-Key`）：对当前草稿 `ValidateExecution` 并要求 hash==`expected_manifest_sha256`；重读不可变 report/evaluation/snapshot/sealed manifest/sealed report 字节，按 T062 逐 case run 复查 runner 可信性；五类作者案例门禁；冻结 shared-definition 快照（用途卡/模型目的地/工具读写联网/固定子版本/连接器/report 证据）；事务锁 draft 行再验 execution_revision；同内容重复提交返回已有 review；202 `{id,state}` |
| 批准 | `POST /reviews/{id}/decision`（仅 admin，`Idempotency-Key`，body hash 必须等于冻结 hash，reason 记录）：再次复查报告/五类/应用未归档/冻结依赖闭包；单事务插不可变 version（用冻结 manifest 规范字节）+ version_dependencies + `applications.active_version_id` + `outbox(catalog_index)` + audit + review approved；reject 只记录状态/原因；并发批准 = 一个版本、一个 approval（真实并发测试） |
| 读/页面 | `GET /reviews`（admin 全量；creator 只看本人提交/本人应用；cursor+limit）与 `GET /reviews/{id}`（未知/他人 404）；非契约 `POST .../publication-preview` 与提交共享同一投影；`/apps/{id}/publish`、`/reviews[/{id}]` 页面 + Node 执行 |
| 五类门禁 | 规范句 EVALUATION.md §6；schema 无类别字段 → 文档化最小判定（`catalog/publication_cases.go` + ADR 0021 §3）：normal=run succeeded；output_check=passed json/decimal 断言；failure_reason 按平台码分类 tool_*/sandbox_*、ambiguous/out_of/limit/row_limit/over_limit/too_large、其余坏输入；五类齐且 ≥5 passed case |
| 真实证据 | `test_t068_e2e.py`：真实两池 maintenance + 真实 WSL2/runsc 产出 5 例真实 runner 报告（2 正常、1 输入 schema 拒绝、1 row_limit_exceeded、1 真实 sandbox_unavailable 工具失败；见测试断言）；提交后撤销真实工具依赖 → 批准 409 `dependency_unavailable`（located `dependency_revoked`）→ 恢复 → 批准 200 → version/active/outbox/audit/version 读全部断言。其余 review 事务用例用真实 guard 接受的密封报告（passed case 绑真实 case run 行，终态种子；报告 §4 明示） |
| 门禁 | `pytest tests/f02b` 18 passed ×2（合并前 290.87s / 242.53s；合并后重跑见报告 §3）；`go build/vet/test`、`gofmt -l`、`sync_spec --check` 全绿；`tests/tooling`、`tests/f02a`、`tests/f13a -k "t052 or t056 or t062"` 数字见报告 §3；真实 PG 16.4 / 编译二进制 / 真实 HTTP+CSRF+mTLS / sealed 字节 / Node |
| 边界/not_run | T070 撤销端点与撤销后目录/取消；T074 从 published version 建 Run；T075/T078 child run（含子应用依赖的发布端到端 **partial**）；T077/T082 outbox 消费者/目录索引；浏览器 DOM；全量 `pytest tests`；未 push/未关 issue、未改 docs/spec/contracts |

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
