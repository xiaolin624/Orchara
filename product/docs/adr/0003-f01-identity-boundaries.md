# ADR 0003: F01 身份与授权的边界

- 状态：已接受
- 日期：2026-11-02
- 相关：F01 / T015、T017、T019、T020、T021；`product/reports/f01/*`

## 背景

F01 在既有 P04/F00 工作区上首次引入产品身份数据（`platform` schema），同时要满足三件事：
空库与 pre-F01 库都能迁移；管理端点能用 `sessionCookie` 认证并防 CSRF；资源读取默认拒绝。
实现中出现了若干“看起来可以顺手做、但不做才正确”的边界。本 ADR 把它们固定下来，避免后续票
把限制当成遗漏，或把后续票的工作提前塞进 F01。

## 决定

### 1. `platform` schema 与探测期 `app` 骨架并存；0005 是一次性修复，记账从 T015 开始

身份/授权/Key 全部放在 `platform`，不复用 P04 探测期的 `app` 骨架。迁移链 0001–0005 仍管
`app` 的 worker 记账；T015 只把 0005 中悬空的 `GRANT ... ON app.operations` 改为“表存在才
授权”，让空库迁移可以跑完 0001→0008。

这条修复不等于 `app.operations` 授权已验证：**当前链没有任何迁移创建该表，条件 GRANT 永不
执行**；0005 文件头关于 `app.operations` 的承诺是继承 P04 的未验证声明。F03 必须新建
`app.operations` 并在同一迁移里显式授权。

不可变性例外只覆盖这次账本建立前的修复：`platform.schema_migrations` 由 T015 引入，在它之前
不存在记录 checksum 的账本行，因此没有行引用 0005 旧字节；账本建立后，从 0006 起迁移文件一律
不可变。pre-F01 库没有账本，`admin migrate` 会应用 0001–0005（按构造幂等）并记录当前 checksum。

### 2. `platform.applications` 是 F01 的读模型切片；版本外键与 `createApplication` 属 F02A

F01 需要一张真实表来证明默认拒绝的 ACL、列表分页与授权/撤销；它只需要 `id`、`owner_id`、
`name`、`active_version_id`、`archived_at`、`created_at`。`active_version_id` 在 F01 **不加
外键**，`createApplication` 也不在 F01 契约里——两者都依赖 `platform.application_versions`，
由 F02A 拥有。测试用 psql 播种真实的 `platform.applications` 行（真实约束、真实表），这只是
创建入口不在 F01，不是 mock。

### 3. 登录基础放 T015，完整生命周期放 T017

管理端点（`POST /admin/users` 等）在契约里要求 `sessionCookie`，所以 T015 必须交付最小登录
基础设施：`POST /auth/login`、会话签发、CSRF 哈希存储。完整生命周期——`GET /auth/me` 刷新、
登出、对所有不安全方法强制 CSRF、登录限流、`/login` 页面——属 T017。拆分依据是“端点能否
被合法调用”，不是“登录功能是否完整”。

### 4. CSRF token 由会话 token HMAC 派生

CSRF token 不是第二个随机 token，也不是双提交 Cookie：它由会话 token 经 HMAC 派生，服务端只
存 `sessions.csrf_hash`。这样没有额外存储/迁移，刷新页面时能从仍然存在的 HttpOnly 会话 Cookie
重新推导出同一个 token；Cookie 始终 `HttpOnly`，CSRF 明文只在登录响应与 `/auth/me` 中交给页面，
通过 `X-CSRF-Token` 头回传，绝不进 URL。T017 打开强制校验；轮换策略不在 F01。

### 5. 管理员不是自动内容读者；ACL 写路径禁止自授

`admin` 角色只代表“可以管理授权/人员”，不代表“可以读正文”。资源读取始终经过
`policy.Authorizer` 的 owner-or-unexpired-grant 判定；管理员没有 grant 时与普通用户一样 404、
列表排除。

`POST /api/v1/admin/grants` 还禁止 `principal_id == caller`：否则管理员可以先给自己授 `read`
再读任意应用，绕过上述验收。拒绝是 422 `validation_failed`、`details.field = "principal_id"`，
消息说明必须由另一位管理员授予；不写行、不 bump epoch、不写审计。UUID 文本大小写不敏感，
比较用 `EqualFold`，大写拼写不能绕过。两人路径仍然是文档化的授权方式。

### 6. 服务账号责任是 F01 的“未完成待办”交接；审批属 F06

F01 可交接的“未完成待办”定义为服务账号责任（`responsible_user_id`）。审批/待办实体属 F06 的
approvals，F01 不伪造审批数据模型。交接只改责任归属，不动角色/ACL/Key，因此不扩权；
`updatePrincipal` 的契约 body 只有 `{disabled, reason}`，没有 successor 字段，所以交接只能通过
`cmd/admin handover` 完成。F06 若引入 successor/审批交接，应扩展契约，而不是回填 F01。

### 7. `GET /memories` 的 F01 边界是拒绝，存储属 F09

`GET /api/v1/memories` 由 F09 拥有并实现记忆存储。F01 只交付**拒绝边界**：服务凭据在解析
`limit`、接触存储之前就 403 `personal_scope_forbidden`。人类会话的 200 空列表是诚实的空读模型，
不是“记忆已实现”的声明；F01 不伪造数据，也不以 501 代替契约响应。

### 8. `ROLESET1` 是角色/禁用/交接共享的 advisory lock

`SetRoles`、`SetDisabled`、`Handover` 都在各自事务里取同一个
`pg_advisory_xact_lock("ROLESET1")`。它串行化所有会改变“谁可管理安装 / 谁负责服务账号”的写：
并发的两次降级或自禁用不可能都看到“还有一个管理员”，交接也不会与禁用交错成无负责人的状态。
未来任何新增的角色/责任写路径必须继续复用这把锁；这是并发正确性的一部分，不是可选优化。

## 后果

- F01 的认证、授权、服务账号、禁用/交接共享一套中间件与审计词汇；后续票扩展资源类型/动作时
  改 `policy` 词汇表，不要另写可见性 SQL。
- 0005 的 `app.operations` 承诺、`applications` 版本外键、`createApplication`、审批、记忆存储、
  跨实例限流/缓存都是显式未完成项，分别由 F03/F02A/F06/F09/F15B 承接；不得在 F01 报告里写成
  已通过。
- 空库与 pre-F01 库的迁移路径已记录；F01 之后不再允许修改任何已记账迁移。
- 浏览器 E2E 仍未运行：共享 `app.js` 由 Node 真实执行、内联脚本做语法检查，但 DOM 行为不在
  自动验收内。

## 证据

- `product/reports/f01/T015-identity-bootstrap.md`（0005 修复/记账/未验证声明）
- `product/reports/f01/T017-login-and-csrf.md`（CSRF 派生、限流、JS 执行）
- `product/reports/f01/T019-resource-grants.md`（ACL、last-admin、自授拒绝、单一时钟）
- `product/reports/f01/T020-service-accounts.md`（服务账号、scope、`/memories` 边界）
- `product/reports/f01/T021-disable-and-handover.md`（禁用、交接、`ROLESET1`）
- `product/tests/f01/test_t019_self_grant.py`、`test_f01_app_js.py`
