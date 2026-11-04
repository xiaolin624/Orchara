# `deploy/dev` — 干净开发环境启动与销售冒烟（F15A / T057）

本目录是**部署者可见的可重放入口**：在一台已具备用户态 PostgreSQL 16.4、Go 1.27、
Python 3.12 的 Windows 开发机上，从**空数据库**完成

```text
admin migrate → admin bootstrap → cmd/api（含真实 Web 页面）→ cmd/maintenance run
```

并跑通**销售 / 失败 / 取消 / stub 模型 profile** 冒烟，把机器可读证据写到
[`reports/f15a/dev-smoke-run.json`](../../reports/f15a/dev-smoke-run.json)。

**profile 固定为 `dev-stub`，并且写明原因**：本机没有 Docker/Compose、Redis、S3/MinIO，
也没有任何已获批的模型供应商凭据；`dev-stub` 的 stub 结果在 `/api/status`、状态页与
stub 模型兼容报告里都有可见标记。这不是"完整 M1"，也没有把本地文件存储冒充 S3。

| 文件 | 作用 |
|---|---|
| `bringup.py` | 一条命令的启动/冒烟编排：建库、编译、开发 mTLS 证书、migrate、bootstrap、启动 API 与 maintenance、等待就绪；`up` 常驻，`smoke` 一次性跑完并写证据 |
| `dev_pki.py` | **仅开发用**的 X.509 生成器（CA + server + maintenance/supervisor/worker 客户端证书）；私钥不打印、不进仓库 |
| `smoke.py` | 通过**公开 HTTP API** 跑销售/失败/取消/模型 profile 冒烟，并用 Node 执行真实页面脚本；返回/写入证据字典 |
| `dev_http.py` | 无第三方依赖的真实 HTTP 小型客户端（cookie、不跟随重定向、逐响应返回） |

证据、AC 逐条结论与限制见
[`reports/f15a/T057-clean-env-smoke.md`](../../reports/f15a/T057-clean-env-smoke.md)。

---

## 0. 前置条件（实测版本）

| 组件 | 版本 / 位置 | 检查 |
|---|---|---|
| PostgreSQL 16.4（用户态集群） | `127.0.0.1:55432`，数据目录 `E:\orchara-pg` | `python tools\pg_dev.py status` |
| Go 1.27.3 | `E:\orchara-toolchains\go\bin\go.exe`（不在 PATH） | `go version` |
| Python 3.12.2 | `D:\Python\python.exe` | `python --version` |
| Node 24 | 页面逻辑执行用；缺失时冒烟把该步记为 `not_run` | `node --version` |
| `cryptography` | 生成开发 mTLS 材料（`dev_pki.py`）；测试环境已具备 | `python -c "import cryptography"` |
| `product\.env.local` | git-ignored，含 `ORCHARA_PG_PASSWORD` | 不打印、不提交 |

本机 5432 端口的 `postgresql-x64-16` 是**无关服务**，本项目从不使用
（`reports/p0/D00-environment-report.md:55-57`）。

```powershell
cd E:\Orchara\product
$env:PATH = "E:\orchara-toolchains\go\bin;$env:PATH"
python tools\pg_dev.py init      # 首次；之后可跳过
python tools\pg_dev.py status
```

---

## 1. 一条命令（推荐；也是证据生成方式）

```powershell
$env:ORCHARA_DEV_ADMIN_PASSWORD = "<至少 12 位的本地管理员口令>"
python deploy\dev\bringup.py smoke --fresh --out reports\f15a\dev-smoke-run.json
```

它会依次打印并执行第 2 节的所有原始命令，跑完冒烟后写出证据，再停掉 API 与 maintenance。
`--fresh` 会 **drop 并重建** `orchara_dev`（只影响该开发库）。

常驻开发环境（适合手工点页面）：

```powershell
python deploy\dev\bringup.py up --fresh
# Ctrl-C 同时停止 API 与 maintenance
```

自动化（例如 CI/验收脚本）可加 `--run-seconds 5`：打印 `ready` 后 5 秒自动干净退出。

口令只从 `ORCHARA_DEV_ADMIN_PASSWORD` 或隐藏终端读取，**从不放在 argv、日志或证据里**。

---

## 2. 手工原始命令（可与 `bringup.py` 逐条对照）

> 下面使用固定端口 `8088`（公开）与 `18088`（内部 mTLS）。被占用时换成其他端口即可；
> `bringup.py` 默认自动选择空闲端口。

### 2.1 建一个空数据库

```powershell
$env:PGPASSWORD = ((Get-Content .env.local | Where-Object { $_ -like 'ORCHARA_PG_PASSWORD=*' }) -split '=',2)[1]
& 'E:\orchara-toolchains\pgsql\bin\psql.exe' -X -h 127.0.0.1 -p 55432 -U orchara_owner -d postgres `
  -c 'DROP DATABASE IF EXISTS orchara_dev WITH (FORCE)'
& 'E:\orchara-toolchains\pgsql\bin\psql.exe' -X -h 127.0.0.1 -p 55432 -U orchara_owner -d postgres `
  -c 'CREATE DATABASE orchara_dev'
Remove-Item Env:\PGPASSWORD
```

### 2.2 编译真实二进制

```powershell
New-Item -ItemType Directory -Force .devstack\bin | Out-Null
go build -o .devstack\bin\orchara-api.exe .\cmd\api
go build -o .devstack\bin\orchara-admin.exe .\cmd\admin
go build -o .devstack\bin\orchara-maintenance.exe .\cmd\maintenance
go build -o .devstack\bin\orchara-supervisor.exe .\cmd\supervisor
```

### 2.3 开发 mTLS 证书 + 对象签名密钥

内部监听器强制 mTLS，maintenance 必须持客户端证书；`cmd/api` 的签名下载链接必须
有 ≥32 字节的 HMAC 密钥。

```powershell
python deploy\dev\dev_pki.py --dir .devstack\certs
python -c "import secrets,pathlib; pathlib.Path('.devstack/object-signing-key').write_text(secrets.token_urlsafe(48), encoding='utf-8')"
```

`.devstack\` 已在根 `.gitignore` 中（二进制、对象字节、开发证书、日志都不入库）。

### 2.4 管理员/API 进程的环境（窗口 A）

```powershell
$env:ORCHARA_PROFILE = "dev-stub"
$env:ORCHARA_DB_DSN = (python tools\pg_dev.py dsn orchara_dev)
$env:ORCHARA_HTTP_ADDR = "127.0.0.1:8088"
$env:ORCHARA_PUBLIC_URL = "http://127.0.0.1:8088"
$env:ORCHARA_WEB_DIR = "apps/web/src"
$env:ORCHARA_OBJECT_DIR = ".devstack/objects"
$env:ORCHARA_OBJECT_SIGNING_KEY = (Get-Content .devstack\object-signing-key -Raw).Trim()
$env:ORCHARA_INTERNAL_ADDR = "127.0.0.1:18088"
$env:ORCHARA_INTERNAL_SERVER_CERT = ".devstack/certs/server.pem"
$env:ORCHARA_INTERNAL_SERVER_KEY = ".devstack/certs/server-key.pem"
$env:ORCHARA_INTERNAL_CLIENT_CA = ".devstack/certs/ca.pem"
# 占位部署输入：本构建没有构建 worker 镜像（F15A 不做镜像构建）。
# debug Run / validate 需要它；证据里记为 deployer-placeholder, image_built=false。
$env:ORCHARA_WORKER_IMAGE_DIGEST = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
$env:ORCHARA_RUNTIME_LOCK_PATH = "probes/agent_min/requirements.lock.txt"
$env:ORCHARA_PYTHON = (Get-Command python).Source
$env:ORCHARA_PRODUCT_ROOT = (Get-Location).Path
```

### 2.5 迁移与首个管理员

```powershell
.devstack\bin\orchara-admin.exe migrate
.devstack\bin\orchara-admin.exe bootstrap --login admin --display-name "Platform Admin"
# 交互终端会隐藏读取口令（至少 12 位）；管道/脚本则读一行，不落日志。
```

重复执行 `migrate` 是 no-op；已有管理员时 `bootstrap` 会拒绝（`ErrAdminExists`），
`bringup.py` 会跳过它并提示沿用原口令。

### 2.6 启动 API（窗口 A）

```powershell
.devstack\bin\orchara-api.exe
```

### 2.7 启动真实后台 worker（窗口 B）

`cmd/worker` 在本构建中只是**不领取工作的 liveness 骨架**；真正的异步循环是
`cmd/maintenance run`（`artifact_finalize` 的 `system_io` 池 + 其余 kind 的 `work` 池）。

```powershell
$env:ORCHARA_PROFILE = "dev-stub"
$env:ORCHARA_MAINTENANCE_DSN = (python tools\pg_dev.py dsn orchara_dev)
$env:ORCHARA_INTERNAL_URL = "https://127.0.0.1:18088"
$env:ORCHARA_INTERNAL_CLIENT_CERT = ".devstack/certs/client-maintenance.pem"
$env:ORCHARA_INTERNAL_CLIENT_KEY = ".devstack/certs/client-maintenance-key.pem"
$env:ORCHARA_INTERNAL_CA = ".devstack/certs/ca.pem"
$env:ORCHARA_OBJECT_DIR = ".devstack/objects"
$env:ORCHARA_PYTHON = (Get-Command python).Source
$env:ORCHARA_PRODUCT_ROOT = (Get-Location).Path
.devstack\bin\orchara-maintenance.exe run --poll-interval 200ms --worker-id dev-maintenance
```

### 2.8 就绪检查（窗口 C）

```powershell
Invoke-RestMethod http://127.0.0.1:8088/health/ready
Invoke-RestMethod http://127.0.0.1:8088/api/status
# 期望：status=ready，profile=dev-stub，stub_results=True
```

页面：`http://127.0.0.1:8088/`（状态页会显示 profile 与 stub 标记）、`/login`、`/apps`、
`/attachments`（销售工具 UI）、`/runs`（取消/终态 UI）、`/models`（stub 兼容报告）。

### 2.9 跑冒烟（窗口 C）

```powershell
$env:ORCHARA_DEV_ADMIN_PASSWORD = "<管理员口令>"
python deploy\dev\smoke.py --base-url http://127.0.0.1:8088 `
  --internal-url https://127.0.0.1:18088 `
  --ca .devstack\certs\ca.pem `
  --client-cert .devstack\certs\client-supervisor.pem `
  --client-key .devstack\certs\client-supervisor-key.pem `
  --out .devstack\smoke-run.json
```

`--internal-*` 可选：提供时额外验证"取消后的 Run 没有可领取工作"（真实 mTLS
`POST /internal/v1/jobs/claim` 返回 204）。

---

## 3. 冒烟覆盖什么

1. **环境/页面**：`/api/status` 的 `profile=dev-stub`、`stub_results=true`；`/health/ready`；
   `/`、`/login`、`/apps`、`/attachments`、`/runs`、`/models` 均由真实 API 返回；
   状态页确实渲染 profile 与 stub 标记。
2. **线性销售**：登录 → 建创作者 → `POST /api/v1/artifacts/uploads` → 真实签名 PUT →
   `complete` → `artifact_finalize` → `POST /api/v1/tool-runs`（`sales_stats`）→ 轮询到
   `succeeded` → 下载 `tool_summary`（`summary.json`）与 `tool_artifact`（`summary.csv`）。
   **真值由本脚本用 `decimal.Decimal` 从 CSV 独立计算**（`250.00` / 东区 `200.00` / 西区
   `50.00` / 3 单），不使用工具自己的输出。
3. **失败示例**：缺列与重复订单各一，均在 `failed` 终态给出 `missing_column` /
   `duplicate_order_id` 诊断（`status=rejected`、`retryable=false`、message/fix/location）；
   这两类是**业务失败，不重试**（`attempt_count=1`，证据在验收测试中断言）。
4. **取消示例**：真实 `POST /applications/{id}/debug-runs` 排队一个 debug Run →
   匿名读 401、他人取消 404、owner 取消 202 且 `state=cancelled`、`phase=idle`、
   无 output；内部 mTLS 再领取得到 **204**（取消后没有被重新排队的 job）。
5. **明确的模型 profile（stub）**：admin 登记 `provider=stub` 的候选并 `validate` →
   真实 `model_validate` → `GET /models/{id}` 为 `passed`、`GET /evaluation-reports/{id}`
   为 `passed`、封存报告内容 `provider=stub, stub=true, compatible=true`。
6. **页面逻辑由 Node 执行**（DOM 不驱动）：用真实 API 数据调用
   `summaryRows/summaryTotals/toolDownloadLinks/diagnosticsFields/describeRunCancel/mergeRunSnapshot/describeReport`。

---

## 4. 证据

- **机器可读**：[`reports/f15a/dev-smoke-run.json`](../../reports/f15a/dev-smoke-run.json)
  （schema、profile、版本、命令、id、状态、输入/产物 sha256、blocked/not_run、defects）。
- **人读**：[`reports/f15a/T057-clean-env-smoke.md`](../../reports/f15a/T057-clean-env-smoke.md)。

证据里不出现口令、DSN、对象签名密钥或私钥内容；`ORCHARA_DB_DSN` /
`ORCHARA_MAINTENANCE_DSN` / `ORCHARA_OBJECT_SIGNING_KEY` 在命令记录中为 `<redacted>`。

---

## 5. 明确 blocked / not_run（不是"没写文档"，是环境缺项）

| 维度 | 状态 | 依据 |
|---|---|---|
| Compose/容器部署 | `blocked`（`container_runtime`） | 本机无 Docker/Compose；本 runbook 是"多进程 + PostgreSQL"部署，不是容器部署 |
| 正式隔离 / 任意脚本沙箱 | `blocked`（`linux_isolation`） | WSL Ubuntu-24.04 有 `runsc release-20241118.0`，但 **WSL 不是被接受的隔离宿主**（ADR 0002 / `BOOTSTRAP.md:71`）；冒烟的预置工具走 Python 子进程路径，未经过 gVisor |
| 真实模型 profile | `blocked`（`model_credentials`） | 无已获批端点/凭据；只有 `dev-stub` 的 stub provider |
| S3/MinIO 对象存储 | `blocked` | 无端点；这里真实使用的是本地文件系统 store，不声明 S3 兼容性 |
| Redis 唤醒 | `not_run` | 本机无 Redis；平台只用 PostgreSQL 轮询（没有为了拓扑图去加 Redis） |
| `cmd/supervisor exec --once` | `not_run` | 需要被接受的 Linux 隔离宿主；本冒烟不需要 debug 工具执行 |
| `cmd/worker` | `not_run` | liveness-only 骨架，不领取工作；真实循环是 `cmd/maintenance run` |
| 评测 UI | `not_run` | 本构建没有评测页面 |
| 浏览器 DOM | `not_run` | 真实页面脚本由 Node 用真实数据执行，未驱动浏览器 |

---

## 6. 清理

```powershell
# 停止窗口 A/B 的进程（Ctrl-C）
Remove-Item -Recurse -Force .devstack            # 开发状态：二进制、对象字节、证书、日志
python tools\pg_dev.py stop                      # 若不再需要本地库
```

## 7. 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| `ORCHARA_PG_PASSWORD is not set` | 缺 `product/.env.local`；按 `README.md` 创建（口令不入库） |
| API 启动即退出并提示 `ORCHARA_PROFILE` | 未设置 profile；本 runbook 只接受 `dev-stub` |
| API 报 `object link signing rejected` | `ORCHARA_OBJECT_SIGNING_KEY` 不足 32 字节 |
| API 报 internal listener rejected | 四个 `ORCHARA_INTERNAL_*` 必须成套；先用 `dev_pki.py` 生成 |
| maintenance 报 `ORCHARA_INTERNAL_URL` | 必须是 `https://` 且无 path/query/credential |
| maintenance 报证书错误 | 客户端证书的 Organization 必须是 `maintenance`，并带 `X-Internal-Role`（进程自己会带） |
| `/health/ready` 503 | 数据库不可达；`python tools\pg_dev.py status` |
| debug-run 返回 503 `compiler_unconfigured` | 缺 `ORCHARA_WORKER_IMAGE_DIGEST` / runtime lock；用第 2.4 节占位值 |
