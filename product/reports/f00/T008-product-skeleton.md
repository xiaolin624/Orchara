# F00 T008 验收：启动产品并在浏览器看到真实健康状态

- 任务：F00 / GitHub Issue #9（`T008`）
- 状态：**passed**
- 代码：`cmd/api/`、`internal/health/`、`apps/web/src/index.html`、`tools/pg_dev.py`
- 测试：`python -m pytest product/tests/f00 -q` → **5 passed**；
  `go test ./internal/health/` → **ok**

## 1. 产品入口

```powershell
python tools/pg_dev.py init                      # 隔离开发库 127.0.0.1:55432
$env:ORCHARA_DB_DSN  = (python tools/pg_dev.py dsn)
$env:ORCHARA_HTTP_ADDR = "127.0.0.1:8088"
$env:ORCHARA_WEB_DIR = "apps/web/src"
go run ./cmd/api
```

- 页面：<http://127.0.0.1:8088/>（显示真实状态，不是静态假页面）
- 存活：`/health/live`（契约路径，别名 `/healthz`）；就绪：`/health/ready`（别名 `/readyz`）；
  机器可读聚合：`/api/status`

README 的"可执行命令"一节与实际命令一致（criterion：产品入口链接正确）。

## 2. 真实健康状态的实测

`tests/f00/test_readiness_integration.py` 不是 mock，按顺序做了：

1. 用真实 DSN 启动 Go API；
2. `GET /healthz` → 200 `alive`；`GET /readyz` → 200 `ready`，`checks.database = ok`；
3. `pg_dev.stop_cluster()` 停掉 PostgreSQL；
4. `GET /readyz` → **503** `not_ready`，`checks.database = unavailable`；同一时刻
   `GET /healthz` 仍 200 —— 证明"进程活着但依赖不可用"会被区分；
5. `pg_dev.init_cluster()` 重新拉起 PostgreSQL 后，`/readyz` 回到 **200**；
6. 断言响应体中不含数据库口令（运行时从环境读取，仓库里没有该字面量）/
   `postgresql://` / `password` / `sslmode`。

对应 criterion：进程活着但 PG 不可用时 readiness 失败、恢复依赖后可就绪、错误不带凭据。

## 3. 契约漂移检查会失败

`tools/sync_spec.py --check` 对 `docs/spec` 的 SHA-256 清单做比对，非零退出即漂移。
`product/tests/tooling/test_sync_spec.py` 覆盖三种情形：修改、新增、删除；其中
`test_cli_check_exit_code_is_non_zero_on_drift` 断言 CLI 在篡改后返回非零。

## 4. 不在上游 clone 中写平台代码

本仓库不是任何上游 clone 的副本；`product/` 是产品工作区，规格只读并复制到
`product/docs/spec/`。理由与代价见 [ADR 0001](../../docs/adr/0001-product-workspace-inside-spec-repo.md)。

## 5. 设计要点

- **fail closed**：`ORCHARA_DB_DSN` 未设置时，readiness 报 `not_configured` 并保持
  503，而不是因为没有检查就"就绪"（`TestNilCheckIsNeverReady`）。
- **错误不泄露凭据**：依赖检查失败只返回粗粒度原因，DSN 不进响应、不进日志
  （`TestErrorsDoNotLeakCredentials`、`test_status_endpoint_never_includes_the_dsn`）。
- **检查有超时**：每个依赖检查受 2s 超时约束（`TestCheckTimeoutIsApplied`）。

## 6. 边界

- 这是 F00 骨架：没有业务模块、没有认证、没有迁移；`/api/status` 只反映进程与数据库。
- Redis、对象存储尚未接入，profile 启动与 CI 复现属 T012（未完成）。
- 本机 5432 上有无关的 `postgresql-x64-16` 服务；探针一律使用隔离集群 55432，从不接触该服务。
