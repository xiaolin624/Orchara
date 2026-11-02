# 产品工作区（Orchara 平台实现）

本目录是**产品代码工作区**，不是规格包。规格是只读来源，位于本仓库根目录，并被完整复制到
[`docs/spec/`](docs/spec/) 作为实现依据与漂移比对基线。契约运行快照在 [`contracts/`](contracts/)。

- 规格入口：[docs/spec/HANDOFF.md](docs/spec/HANDOFF.md)
- 实现进度权威：[PROGRESS.md](PROGRESS.md)
- 决策记录：[docs/adr/](docs/adr/)
- 术语表：[docs/spec/GLOSSARY.md](docs/spec/GLOSSARY.md)

## 当前状态（不要读成"平台已完成"）

已实现范围：**D00 + P01–P05 + F00 + F01**。

- **D00–P05**：环境探测与规格复现、锁定依赖、双语言指纹/DSL 校验、检查点恢复与制品钉版、
  Profile/CI、WSL2 沙箱隔离（ADR 0002）、租约/排空/丢回执。
- **F00**：可启动的 HTTP 骨架、健康/就绪检查、静态状态页、环境 profile。
- **F01（身份基础，5/5）**：迁移链与 `cmd/admin migrate` / `bootstrap`；本地用户邀请、激活、
  登录、登出，Cookie 会话与 CSRF 强制；默认拒绝的资源 ACL（`platform.applications` 读模型、
  `policy_epochs` / `resource_grants`、授权/撤销、角色更新与 last-admin）；具名负责人的服务账号
  与可撤销、带 scope 的 bearer API Key；禁用/启用（同一事务撤销会话与 Key、bump epoch、审计）
  与 `cmd/admin handover`；`/login`、`/admin`、`/apps`、`/activate` 页面。

平台**仍不是完整 v1**：请求/路由/执行/评测/RAG/记忆/审批/编译器/画布/发布门禁/部署与灾备等
后续 epic 尚未实现。F01 的已知边界（详见 [`reports/f01/`](reports/f01/) 与
[PROGRESS.md](PROGRESS.md)）：

- **没有浏览器 E2E**：页面测试是真实 HTTP 内容断言；共享 `assets/app.js` 由 Node 真实执行，
  页面内联脚本做 `node --check`，但 DOM/交互行为未跑。
- **没有 approvals / F09 记忆存储**：F01 只交付服务账号责任交接与 `/memories` 的服务凭据拒绝
  边界；审批属 F06，记忆属 F09。
- **登录限流是单进程内存态**（5 次/60 秒），多实例配额属 F15B；限流键取直连 peer 地址。
- **ACL 词汇表只有 `application` / `read`**；`createApplication` 与 `application_versions`
  外键属 F02A。
- **`app.operations` 的 worker 授权仍是未验证的 P04 声明**：当前链没有创建该表，0005 的条件
  GRANT 不执行；F03 必须建表并显式授权。
- **`Secure` Cookie 由 Go 单测覆盖**；`linux-sandbox` / `real-model` profile 未做 HTTP 级实测。

真实状态以 [PROGRESS.md](PROGRESS.md) 为准；F01 各票的证据与限制见 `reports/f01/T015-*.md`…
`T021-*.md`。

已知阻塞 / 限制（D00/T001 实测 + F01 补充）：

| 阻塞 | 影响 | 解除条件 |
|---|---|---|
| `linux_isolation`（生产） | 验收已在 WSL2 完成（ADR 0002）；生产隔离交付仍需真实独立 Linux 宿主 | 独立 Linux 宿主 + runsc/gVisor + root，并重跑 P03 证据 |
| `model_credentials` | 只能报告 stub 路径；真实模型质量不可声明 | 部署者提供已获批模型端点凭据 |
| `container_runtime` | Compose 部署票不可验证 | 安装 Docker/兼容运行时 |

GPU 存在（RTX 3050 Laptop，4 GiB），但未运行任何训练；4 GiB 显存低于常规 LoRA 微调需求，F14 仍为 not_run。

## 工具链位置

用户态工具链放在**仓库之外**，避免规格校验器扫描其 Markdown 相对链接而导致契约校验误报：

| 工具 | 路径 |
|---|---|
| Go 1.27.3 | `E:\orchara-toolchains\go` |
| PostgreSQL 16.4 客户端/服务端 | `E:\orchara-toolchains\pgsql` |

可用环境变量 `ORCHARA_TOOLCHAINS` 覆盖该目录。

## 可执行命令

先准备本地开发口令（**不要提交**）：创建 `product/.env.local`，内容一行：

```text
ORCHARA_PG_PASSWORD=<你自己设的本地口令>
```

该文件已在 `.gitignore` 中；`tools/pg_dev.py` 读不到时会直接失败（fail closed），
代码、测试、报告里都不含口令。

```powershell
# 规格复现与漂移检查（同时校验 docs/spec 与 contracts，按 LF 归一化）
python tools/sync_spec.py
python tools/sync_spec.py --check

# 环境探测（生成 reports/p0/environment-probe.{json,md}）
python tools/probe_environment.py

# 隔离开发数据库（本机 5432 已被无关服务占用，本项目固定用 55432）
python tools/pg_dev.py init
python tools/pg_dev.py status

# 锁版探针环境：从零创建、按哈希安装、跑探针
python tools/bootstrap_probe.py            # 或 --recreate 重建
python tools/sbom.py                       # 生成 reports/p01/sbom.json

# 启动产品骨架（F00）：浏览器打开 http://127.0.0.1:8088/
$env:ORCHARA_PROFILE  = "dev-stub"          # 必填，缺失即拒绝启动
$env:ORCHARA_DB_DSN   = (python tools/pg_dev.py dsn)
$env:ORCHARA_HTTP_ADDR = "127.0.0.1:8088"
$env:ORCHARA_WEB_DIR  = "apps/web/src"
go run ./cmd/api

# F01 身份：迁移 → 建首个管理员（密码只从隐藏 stdin 读）→ 启动后访问页面
go run ./cmd/admin migrate
go run ./cmd/admin bootstrap --login admin --display-name "Platform Admin"
# 终端里会隐藏读取密码；管道/脚本输入时读一行，不回显、不落日志
go run ./cmd/api
# 页面：/login（登录）、/admin（邀请/授权/角色/服务账号/禁用）、
#       /apps（ACL 过滤后的应用列表）、/activate（一次性令牌激活）

# F01 交接：服务账号责任转给另一位启用中的人类管理员（CLI-only）
go run ./cmd/admin handover --from <旧负责人 uuid> --to <新负责人 uuid> --by <操作者 uuid>
```

F01 的命令、接口、原始结果与限制见 [`reports/f01/`](reports/f01/)（T015–T021），票状态见
[PROGRESS.md](PROGRESS.md)。`admin` 的 DSN 只从 `ORCHARA_DB_DSN` 读取，不要放在 argv 上。

可选 profile：`dev-stub` / `linux-sandbox` / `real-model` / `capacity-stub`。
非 `linux-sandbox` 下启用 `ORCHARA_ALLOW_ARBITRARY_SCRIPTS=true` 会被拒绝；
`real-model` 需要 `ORCHARA_MODEL_ENDPOINT` 与 `ORCHARA_MODEL_SECRET_REF`（只接受
`env:NAME` / `file:NAME`，不接受 URL 或路径）。

一键基础检查：`python tools/ci_checks.py`（无 Go/数据库时加 `--quick`）。

健康检查同时提供契约路径 `/health/live`、`/health/ready`（`contracts/openapi.json`
的 operationId `liveness`/`readiness`）与惯用别名 `/healthz`、`/readyz`。
页面每 3 秒读 `/health/live` 与 `/api/status`：数据库不可用时 `/health/ready` 返回 503，
不会因为"进程还活着"就报告就绪。

## 测试

```powershell
python -m pytest tests -q        # 工具 + 探针 + 集成（集成需 Go 与开发库）
go test ./...                    # 平台 Go 测试
cd probes/jcs/go; go test ./...   # JCS 黄金向量（Go 侧）
```

规格自带的静态校验（在仓库根目录执行）：

```powershell
tools/.venv/Scripts/python.exe tools/validate_contracts.py
tools/.venv/Scripts/python.exe tools/validate_handoff.py
tools/.venv/Scripts/python.exe tools/check_handoff_package.py
```
