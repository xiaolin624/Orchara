# D00 环境探测报告（自动生成）

- schema: `orchara.env-probe/v1`
- 生成时间(UTC): `2026-11-01T09:52:37+00:00`
- 任务: D00 / T001

## 宿主

- OS: Windows 11 (AMD64)
- CPU: 12 cores, 内存: 15.8 GiB
- Python: 3.12.2 @ `D:\Python\python.exe`

## 工具（每项含探测命令）

| 工具 | 可用 | 版本/错误 | 探测命令 |
|---|---|---|---|
| go | yes | go version go1.27.3 windows/amd64 | `E:\orchara-toolchains\go\bin\go.exe version` |
| psql | yes | psql (PostgreSQL) 16.4 | `E:\orchara-toolchains\pgsql\bin\psql.exe --version` |
| git | yes | git version 2.49.0.windows.1 | `git --version` |
| python | yes | Python 3.12.2 | `D:\Python\python.exe --version` |
| node | yes | v24.13.0 | `node --version` |
| npm | yes | 11.13.0 | `npm.cmd --version` |
| docker | NO | not found on PATH and not present at the recorded path | `docker version --format {{.Server.Version}}` |
| redis-server | NO | not found on PATH and not present at the recorded path | `redis-server --version` |
| wsl | yes | WSL 版本: 2.6.3.0 | `wsl.exe --version` |
| nvidia-smi | yes | NVIDIA GeForce RTX 3050 Laptop GPU, 4096 MiB | `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader` |

## 服务可达性

| 服务 | 端口 | 可达 | 探测命令 |
|---|---|---|---|
| postgres | 5432 | yes | `tcp connect 127.0.0.1:5432` |
| redis | 6379 | NO | `tcp connect 127.0.0.1:6379` |
| object_storage | 9000 | NO | `tcp connect 127.0.0.1:9000` |

## 能力分类

- **go_build**: `available` — go version go1.27.3 windows/amd64
- **node_build**: `available` — v24.13.0
- **postgres**: `available` — server reachable on 127.0.0.1:5432
- **redis**: `unavailable` — no Redis server accepting connections on 127.0.0.1:6379
- **container_runtime**: `unavailable` — no docker CLI/daemon
- **linux_host**: `unavailable` — a WSL distribution is registered but this probe did not run inside it; WSL presence is not Linux isolation acceptance
- **linux_isolation**: `unavailable` — no runsc/gVisor runtime could be started and verified; Windows/WSL results may not be recorded as isolation acceptance
- **gpu**: `available` — NVIDIA GeForce RTX 3050 Laptop GPU

## 阻塞项

| id | 原因 | 解除后可推进 |
|---|---|---|
| `linux_isolation` | no Linux host with an installable sandbox runtime (runsc/gVisor) and root privileges was available for this probe | P03 sandbox tickets (T006/T010/T014) and F13B isolation acceptance |
| `model_credentials` | no approved model-provider credentials present in the environment; only stub-model paths may be reported as executed | real-model profiles and provider-capacity experiments |
| `container_runtime` | no container runtime available; compose-based deployment is not verifiable | F15A/F15B deployment tickets |

> 静态校验通过不等于平台可运行；本报告只记录探针事实，不改变 P0 状态。
