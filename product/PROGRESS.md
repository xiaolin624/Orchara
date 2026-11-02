# 产品实现进度（权威）

本文件是平台实现的进度权威。规格仓库里的 `docs/spec/PROGRESS.md` 只保留接手时的初始快照。

状态取值：`not_started` / `in_progress` / `blocked` / `passed` / `failed` / `not_run`。
`passed` 必须满足该票在 `contracts/task-graph.json` 的完成条件；缺外部条件写 `blocked`，
未执行写 `not_run`。**静态校验通过、探测到硬件、stub 成功都不升级为 passed。**

## 当前发布范围

本轮实现范围（用户确认）：**D00 + P01–P05 + F00 + F01**，其余票保持 open。
F01（T015/T017/T019/T020/T021）已交付身份基础：迁移与管理命令、邀请/激活/登录/登出、
Cookie 会话与 CSRF、默认拒绝的资源 ACL、服务账号与可撤销 API Key、禁用与责任交接。
F00B 的 T022 已交付持久后台任务控制器（迁移 0009、对象存储、claim/租约/重试/完成守卫、
内部 mTLS API、maintenance 处理器循环、admin enqueue、GET job）。F00B 其余票（T024/T026）
继续 open。
平台整体**仍不是完整 v1**：业务执行/路由/评测/RAG/记忆/审批/编译器/画布/发布门禁/部署灾备
等后续 epic 未实现；F01/F00B 的限制见各票报告 §5/§7。

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
| T022 | F00B | 提交一个后台任务并在重启后得到唯一结果 | passed | `reports/f00b/T022-background-jobs.md`、`db/migrations/0009_background_jobs.sql`、`internal/objectstore/`、`internal/background/`（含 handlers）、`internal/api/{internal,background}.go`、`cmd/api/main.go`、`cmd/maintenance/{main,client,run}.go`、`cmd/admin/{main,background}.go`、`tests/support/`、`tests/f00b/`（60 passed，真实 PG + 真实二进制 + 真实 HTTP/mTLS） |

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

未运行（如实）：Redis 唤醒集成（本机无实例；以“无唤醒总线时 PG 轮询仍推进”实测）、S3/MinIO、
签名 URL/`input_url`、上传 producer、真实 worker 协议、病毒查杀、字面 SIGKILL 注入；分别由
T024/T026/F03/F05 或专门故障票承接。本票不推送、不关闭 issue。

## F02A 草稿与版本读模型（1/3：T023）

T023（GitHub #24）已交付，状态 **`partial`**。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实
HTTP；页面 JS 用 Node 真实执行。判定为 `partial` 的唯一原因是票面验收
“非法草稿可保存但**不能调试/提交**”的后半条依赖 T025（validate）、T027（debug）与 F02B
（submit）的端点：T023 只交付“可保存”与 `CheckExecutable` 纯函数，并断言三端点仍 404。
决策边界见 [ADR 0004](docs/adr/0004-f00b-f02a-boundaries.md) §12–17。

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
validate/debug/submit 端点（T025/T027/F02B）；manifest/hash/对象写入（T025/T024）。本票
不推送、不关闭 issue。



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
