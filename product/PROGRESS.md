# 产品实现进度（权威）

本文件是平台实现的进度权威。规格仓库里的 `docs/spec/PROGRESS.md` 只保留接手时的初始快照。

状态取值：`not_started` / `in_progress` / `blocked` / `passed` / `failed` / `not_run`。
`passed` 必须满足该票在 `contracts/task-graph.json` 的完成条件；缺外部条件写 `blocked`，
未执行写 `not_run`。**静态校验通过、探测到硬件、stub 成功都不升级为 passed。**

## 当前发布范围

本轮已交付范围：**D00 + P01–P05 + F00 + F01**，以及本分支的 **F00B（T022/T024 passed；
T026 partial）** 与 **F02A（T023/T025 partial；T027 passed）**；其余票保持 open。
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
平台整体**仍不是完整 v1**：业务执行/路由/评测/RAG/记忆/审批/编译器/画布/发布门禁/部署灾备
等后续 epic 未实现；F01/F00B/F02A 的限制见各票报告 §5/§7。
本文件 2026-11-02 的两轴复核修复记录见下方“F00B/F02A 两轴复核修复”一节；状态以本节之后的表格为准。

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
| T022 | F00B | 提交一个后台任务并在重启后得到唯一结果 | passed | `reports/f00b/T022-background-jobs.md`、`db/migrations/0009_background_jobs.sql`、`internal/objectstore/`、`internal/background/`（含 handlers、`Notifier` 唤醒缝）、`internal/api/{internal,background}.go`、`cmd/api/main.go`、`cmd/maintenance/{main,client,run}.go`、`cmd/admin/{main,background}.go`、`tests/support/`、`tests/f00b/`（60 passed，真实 PG + 真实二进制 + 真实 HTTP/mTLS；复核修复后新增丢唤醒实测，Redis adapter 仍 not_run） |
| T023 | F02A | 创作者编辑用途说明和草稿并处理保存冲突 | **partial** | `reports/f02a/T023-drafts-and-editor.md`（§2 AC3/§8：validate T025 + debug T027 的拒绝是真实的；`POST /submit` 的 404 只证明 F02B 端点缺失，不能当拒绝证据）、`db/migrations/0010_application_drafts.sql`、`internal/catalog/{draft,versions}.go`、`internal/api/{drafts,versions}.go`、`apps/web/src/{editor.html,assets/editor.js}`、`tests/f02a/`（T023 45 用例） |
| T024 | F00B | 后台评测只读取获准文件并记账模型调用 | passed | `reports/f00b/T024-job-files-and-models.md`、`db/migrations/0011_job_files_and_models.sql`、`internal/objectstore/sign.go`、`internal/background/{attempt,artifacts,scope}.go`、`internal/modelproxy/`、`internal/api/{jobdata,links}.go`、`internal/background/handlers/model_validate.go`、`cmd/maintenance/scope.go`、`tests/support/t024.py`、`tests/f00b/test_t024_*.py`（`tests/f00b` 86 passed；复核修复后 T024 28 用例：强制 `purpose='model_fixture'` 的 CLI/model-calls 双端拒绝） |
| T025 | F02A | 把当前草稿固化为可核验执行清单 | **partial** | `reports/f02a/T025-manifest.md`（AC3 `partial`：`VerifyManifestBytes` 机制 + 真实 sealed-object 篡改测试已交付，真正 worker 属 F03/F04A）、`db/migrations/0012_manifest_and_tool_versions.sql`、`internal/compiler/`、`internal/catalog/manifest.go`、`internal/api/validate.go`、`internal/config/config.go`、`tests/contracts/snapshots/`、`tests/support/t025.py`、`tests/f02a/test_t025_*.py`（T025 16 用例，真实 PG + 真实二进制 + 真实 HTTP + 对象字节） |
| T026 | F00B | 评测池满载时文件封存仍能完成 | partial | `reports/f00b/T026-evaluation-pool.md`、`db/migrations/0013_evaluation_pool.sql`、`internal/background/{evaluation,complete}.go`、`cmd/maintenance/{run,sweep}.go`、`tests/f00b/test_t026_*.py`（40 用例；`kind=application` 的真实 runner/checker 属 F13A，T027 后 snapshot 绑定结构可用；**deletion 状态属 F07、未实现**；三条 partial 原因见报告页首） |
| T027 | F02A | 发布前提交私有草稿并查看固定调试任务 | passed | `reports/f02a/T027-debug-submit.md`、`db/migrations/0014_execution_snapshots_and_runs.sql`、`internal/catalog/{debug.go,debug_test.go}`、`internal/api/debug.go`、`apps/web/src/{debug.html,assets/debug.js}`、`tests/support/t027.py`、`tests/f02a/test_t027_*.py`（38 用例，真实 PG + 真实二进制 + 真实 HTTP + 对象字节；领取/执行属 F03） |

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
T028 的用户上传准入；浏览器 DOM 渲染（与 F01/T023 相同）；真实 worker 镜像（占位 digest）；
F13A 的真实评测 runner。全量 `pytest tests` 已在复核修复收尾运行。本票不推送、不关闭 issue。

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
