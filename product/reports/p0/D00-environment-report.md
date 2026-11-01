# D00 环境与路径报告（T001 验收）

- 任务：D00 / GitHub Issue #2（`T001`）
- 报告日期：2026-11-01
- 执行者：本机接手会话（Windows 11，AMD64，12 core / 15.8 GiB）
- 结论：**规格已复现到产品工作区；平台实现未开始；P0 未通过。** 静态校验通过不改变 P0 状态。

本报告只记录实测事实。所有版本号均附产生它的命令；机器可读结果见
[`environment-probe.json`](environment-probe.json)，便于人工阅读的同一份结果见
[`environment-probe.md`](environment-probe.md)。

## 1. 产品目录候选与既有代码清单

| 候选 | 说明 | 决定 |
|---|---|---|
| `E:\agent-platform` | 规格 `BOOTSTRAP.md` 的默认建议（规格同级新建） | 未采用 |
| `E:\Orchara\product` | 本仓库内产品工作区 | **采用** |

采用理由与偏离说明见 [ADR 0001](../../docs/adr/0001-product-workspace-inside-spec-repo.md)：issue 跟踪、
证据与规格同仓，闭合 issue 时证据可直接溯源；代价是必须靠 `sync_spec.py --check` 守住"规格只读"。

既有平台代码清单：**无**。接手前本仓库只有规格文档、`contracts/`、`examples/`、`tools/` 静态校验脚本。
`product/cmd`、`product/internal`、`product/db`、`product/apps/web`、`product/worker` 等目录为空骨架，
仅表达 `BOOTSTRAP.md` §3 的目标结构，不含业务实现。

## 2. 已有修改的保留

接手时 `git status` 只有未跟踪的 `.scratch/`（前序会话的票务生成产物）与本机工具链目录；
未发现需要保留的已跟踪改动。`.scratch/` 未被删除、未被提交。

## 3. 复现规格并生成产物

```powershell
python product/tools/sync_spec.py          # copied 105 files; manifest product/docs/spec/SYNC-MANIFEST.json
python product/tools/sync_spec.py --check  # no drift: spec copy matches manifest
```

产物：`product/docs/spec/`（规格副本 + SHA-256 清单）、`product/contracts/`（运行契约快照，10 个文件）。

## 4. 工具与服务可用性（每项含探测命令）

| 工具 | 可用 | 版本 | 探测命令 |
|---|---|---|---|
| go | 是 | go1.27.3 windows/amd64 | `E:\orchara-toolchains\go\bin\go.exe version` |
| psql | 是 | PostgreSQL 16.4 | `E:\orchara-toolchains\pgsql\bin\psql.exe --version` |
| git | 是 | 2.49.0.windows.1 | `git --version` |
| python | 是 | 3.12.2 | `D:\Python\python.exe --version` |
| node | 是 | v24.13.0 | `node --version` |
| npm | 是 | 11.13.0 | `npm.cmd --version` |
| wsl | 是 | WSL 2.6.3.0（有 Ubuntu-24.04，**无 sudo**） | `wsl.exe --version` |
| nvidia-smi | 是 | RTX 3050 Laptop, 4096 MiB | `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader` |
| docker | **否** | 未找到 | `docker version --format {{.Server.Version}}` |
| redis-server | **否** | 未找到 | `redis-server --version` |

服务端口：`127.0.0.1:5432` 可达（探测命令 `tcp connect 127.0.0.1:5432`），但该监听属于本机既有的
Windows 服务 `postgresql-x64-16`，**凭据未知、不属于本项目**，不作为 P02 的受权实例；
`6379`/`9000` 不可达。

Go 与 PostgreSQL 为本会话下载的用户态发行版，置于仓库之外 `E:\orchara-toolchains`。
曾把工具链放在仓库内 `.toolchains/`，导致 `validate_contracts.py` 扫描到 Go/pgAdmin 的 Markdown
相对链接而报错；移到仓库外后三项校验恢复 0 退出。

## 5. 静态校验原始输出

| 校验 | 结果 | 原始输出 |
|---|---|---|
| `tools/validate_contracts.py` | exit 0 | [`spec-validate_contracts.txt`](spec-validate_contracts.txt) |
| `tools/validate_handoff.py` | exit 0 | [`spec-validate_handoff.txt`](spec-validate_handoff.txt) |
| `tools/check_handoff_package.py` | exit 0 | [`spec-check_handoff_package.txt`](spec-check_handoff_package.txt) |

这三项只证明文档/契约静态可读与隔离副本可校验，不证明依赖安装、数据库、沙箱、模型调用或用户旅程。

## 6. 阻塞清单（分别记录，不合并、不预填通过）

| id | 原因 | 解除后推进 |
|---|---|---|
| `linux_isolation` | 无真实 Linux 宿主 + runsc/gVisor + root；WSL 已注册但**无 sudo**，且规格明确 Windows/WSL 结果不能当隔离验收 | T006 / T010 / T014，F13B 隔离验收 |
| `model_credentials` | 环境无任何已获批模型供应商凭据；只能报告 stub 路径 | 真实模型 profile、供应商容量实验 |
| `container_runtime` | 无 Docker/兼容运行时 | F15A/F15B 部署票 |

**GPU 不缺失**：nvidia-smi 报告 RTX 3050 Laptop（4 GiB）。但未运行任何训练，且 4 GiB 显存低于常规
LoRA 微调需求，F14 训练状态为 `not_run`，不得因"探测到 GPU"而记通过。

## 7. 下一任务

按 `contracts/task-graph.json`：**P01 / `T002`**（从锁定依赖运行一个只有授权工具的 Agent）。

## 8. 复现命令

```powershell
python product/tools/sync_spec.py --check
python product/tools/probe_environment.py
python -m pytest product/tests/tooling -q
```
