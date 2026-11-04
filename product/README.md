# 产品工作区（Orchara 平台实现）

本目录是**产品代码工作区**，不是规格包。规格是只读来源，位于本仓库根目录，并被完整复制到
[`docs/spec/`](docs/spec/) 作为实现依据与漂移比对基线。契约运行快照在 [`contracts/`](contracts/)。

- 规格入口：[docs/spec/HANDOFF.md](docs/spec/HANDOFF.md)
- 实现进度权威：[PROGRESS.md](PROGRESS.md)
- 决策记录：[docs/adr/](docs/adr/)
- 术语表：[docs/spec/GLOSSARY.md](docs/spec/GLOSSARY.md)
- 干净开发环境启动与销售冒烟：[deploy/dev/README.md](deploy/dev/README.md)

## 当前状态（不要读成"平台已完成"）

**已交付范围**（逐票证据以 [PROGRESS.md](PROGRESS.md) 为准）：

- **D00 + P01–P05 + F00 + F01**：环境探测与规格复现、锁定依赖、双语言指纹/DSL 校验、
  检查点恢复与制品钉版、Profile/CI、WSL2 沙箱探针、租约/排空/丢回执；可启动 HTTP 骨架与
  身份基础（迁移/bootstrap、邀请/激活/登录/登出、CSRF、默认拒绝的资源 ACL、服务账号与
  可撤销 API Key、禁用与责任交接）。
- **F00B**：持久后台任务控制器与 `cmd/maintenance run` 双池；job 限定文件访问、HMAC 签名
  URL、受控模型调用（T026 的合成评测 runner 属 F13A，状态为 `partial`）。
- **F02A**：草稿 ETag/CAS 与版本读模型、执行清单固化与依赖校验、私有 debug 快照与排队 Run
  （T023/T025 的发布提交与真实 worker 分别属 F02B/F03，状态为 `partial`；T027 `passed`）。
- **F03**：领取/租约/配额、结果提议 + 身份化退出证据的统一幂等 finalizer、持久事件流/SSE、
  跨尝试活跃时钟与截止、等待/唯一恢复、取消与迟到消息不可复活、恢复证据与安全重驱。
- **F05**：上传/格式准入/封存与授权下载、预置 `sales_stats` 工具（Decimal CSV/XLSX）、
  run 绑定的沙箱工具调试与产物登记、工具构建与审核（WSL2/runsc；正式隔离仍是外部阻塞）。
- **F06 / F04A**：模型登记/兼容验证/受控调用、连接器、预算、上下文、模板、审批、能力账本；
  画布线性编译与校验（T039–T051 共 13 个 issue）。
- **F15A / T057**：干净开发环境的可重放启动产品 `deploy/dev/`，以及真实 HTTP 的销售/失败/
  取消/stub 模型 profile 冒烟与证据 [`reports/f15a/`](reports/f15a/)。Compose、S3、真实
  模型与正式隔离均为 `blocked`，不是"已覆盖"。

平台**仍不是完整 v1**：路由/评测执行器（F13A）、发布门禁（F02B）、RAG/记忆（F08/F09）、
审批后的真实外部操作、编译器其余节点、部署与灾备等 epic 尚未实现；`cmd/worker` 目前只是
**不领取工作的 liveness 骨架**，真实异步循环是 `cmd/maintenance run` 与
`cmd/supervisor exec --once`。open issue 数以 GitHub 为准。

### 已登记阻塞（D00/T001 实测 + 后续补充）

| 阻塞 | 影响 | 解除条件 |
|---|---|---|
| `linux_isolation`（生产） | 验收已在 WSL2 完成（ADR 0002）；生产隔离交付仍需真实独立 Linux 宿主 | 独立 Linux 宿主 + runsc/gVisor + root，并重跑 P03 证据 |
| `model_credentials` | 只能报告 `dev-stub` / `capacity-stub` 的 stub 路径；真实模型质量不可声明 | 部署者提供已获批模型端点凭据 |
| `container_runtime` | Compose/镜像部署票不可验证；当前是"多进程 + 本地 PG"启动 | 安装 Docker/兼容运行时 |

**另一种缺项不是阻塞**：本机无 Redis（平台用 PostgreSQL 轮询）、无 S3/MinIO（用真实本地
文件系统 store）；这两项按 `not_run`/`blocked` 如实记录，不为了拓扑图新增组件。

GPU 存在（RTX 3050 Laptop，4 GiB），但未运行任何训练；4 GiB 显存低于常规 LoRA 微调需求，
F14 仍为 `not_run`。

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

# F15A/T057：干净开发环境（migrate → bootstrap → API → maintenance）与销售冒烟
# 细节、逐条原始命令和 blocked/not_run 说明见 deploy/dev/README.md
$env:ORCHARA_DEV_ADMIN_PASSWORD = "<至少 12 位的本地管理员口令>"
python deploy\dev\bringup.py smoke --fresh --out reports\f15a\dev-smoke-run.json
# 常驻开发环境：python deploy\dev\bringup.py up --fresh  （Ctrl-C 停止）

# 手工启动骨架（等价于 deploy/dev 的原始命令）
$env:ORCHARA_PROFILE  = "dev-stub"          # 必填，缺失即拒绝启动
$env:ORCHARA_DB_DSN   = (python tools/pg_dev.py dsn)
$env:ORCHARA_HTTP_ADDR = "127.0.0.1:8088"
$env:ORCHARA_WEB_DIR  = "apps/web/src"
go run ./cmd/api

# F01 身份：迁移 → 建首个管理员（密码只从隐藏 stdin 读）→ 启动后访问页面
go run ./cmd/admin migrate
go run ./cmd/admin bootstrap --login admin --display-name "Platform Admin"
# 页面：/login、/admin、/apps、/activate；F05/F06 另有 /attachments、/runs、/models
```

可选 profile：`dev-stub` / `linux-sandbox` / `real-model` / `capacity-stub`。
非 `linux-sandbox` 下启用 `ORCHARA_ALLOW_ARBITRARY_SCRIPTS=true` 会被拒绝；
`real-model` 需要 `ORCHARA_MODEL_ENDPOINT` 与 `ORCHARA_MODEL_SECRET_REF`（只接受
`env:NAME` / `file:NAME`，不接受 URL 或路径）。本机**只有** `dev-stub` 可实际启动
（无模型凭据、无被接受的 Linux 隔离宿主）。

一键基础检查：`python tools/ci_checks.py`（无 Go/数据库时加 `--quick`）。

健康检查同时提供契约路径 `/health/live`、`/health/ready`（`contracts/openapi.json`
的 operationId `liveness`/`readiness`）与惯用别名 `/healthz`、`/readyz`。页面每 3 秒读
`/health/live` 与 `/api/status`：数据库不可用时 `/health/ready` 返回 503，不会因为
"进程还活着"就报告就绪。状态页还会显示 `profile` 与 `stub_results` 标记。

## 测试

```powershell
python -m pytest tests -q             # 工具 + 探针 + 集成（集成需 Go 与开发库）
python -m pytest tests/acceptance -q  # F15A/T057 干净环境冒烟（真实 PG/二进制/HTTP/mTLS）
go test ./...                         # 平台 Go 测试
cd probes/jcs/go; go test ./...       # JCS 黄金向量（Go 侧）
```

规格自带的静态校验（在仓库根目录执行）：

```powershell
tools/.venv/Scripts/python.exe tools/validate_contracts.py
tools/.venv/Scripts/python.exe tools/validate_handoff.py
tools/.venv/Scripts/python.exe tools/check_handoff_package.py
```

各票证据与限制见 [`reports/`](reports/)（`f06/T039-model-registry.md` 是报告体例样例）；
票状态见 [PROGRESS.md](PROGRESS.md)。
