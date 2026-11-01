# P01 T004 验收：在第二个干净环境重建核心探针

- 任务：P01 / GitHub Issue #5（`T004`）
- 状态：**passed**（第二个环境为 Linux；Go 侧未在该环境重建，见边界）
- 第二环境：WSL `Ubuntu-24.04`，Linux 6.6.87.2-microsoft-standard-WSL2，Python 3.12.3
- 证据：
  - [`agent-min-probe-wsl.json`](agent-min-probe-wsl.json)（探针在 Linux 重建并全部断言通过）
  - [`jcs-digests-linux.json`](jcs-digests-linux.json)（Linux 侧规范摘要）
  - [`agent-min-probe.json`](agent-min-probe.json)（Windows 侧同一探针）

## 1. 重建了什么

同一份 `probes/agent_min/requirements.lock.txt`（47 包、全部 `--hash`）装进 Linux：

```bash
python3 -m pip install --require-hashes --target /tmp/p01deps -r requirements.lock.txt
PYTHONPATH=/tmp/p01deps python3 -m agent_min --out reports/p01/agent-min-probe-wsl.json
```

结果与 Windows 一致：

| 包 | Windows | Linux |
|---|---|---|
| langgraph | 1.2.4 | 1.2.4 |
| langgraph-checkpoint-postgres | 3.0.5 | 3.0.5 |
| pydantic | 2.12.4 | 2.12.4 |
| jsonschema | 4.25.1 | 4.25.1 |
| psycopg | 3.3.6 | 3.3.6 |

探针断言 `happy_completed / happy_invoked_subset_of_allowlist / unauthorized_rejected /
unauthorized_run_failed / retry_detected / bad_input_failed` 在 Linux 上全部为 `true`。

规范指纹跨平台一致：32 条向量的 SHA-256 与 Windows **完全相同**，8 条拒绝向量错误码相同
（[`jcs-digests-linux.json`](jcs-digests-linux.json) 对 [`jcs-agreement.json`](jcs-agreement.json)）。

## 2. 失败/不采用项及原因（可追溯）

| 项 | 现象 | 处置与原因 |
|---|---|---|
| 首版锁文件（单平台哈希） | 在 Linux 上 `--require-hashes` 无法安装：wheel 是平台特定的，Windows wheel 的 sha256 在 Linux 不匹配 | 改为**每版本列出 PyPI 全部 artifact 哈希**；版本仍精确锁定，哈希更多而非更松。属真实缺陷，已修 |
| 锁文件续行格式 | pip 静默忽略超长哈希列表的尾部（`hashes but no requirement` 警告，漏掉 4 条） | 续行改为除末行外每行以 `\` 结尾（pip-tools 的标准写法） |
| `python3 -m venv` | Ubuntu 缺 `python3-venv`/`ensurepip`，且**无 sudo** | 不采用 venv；改为 `--target /tmp/p01deps` 隔离目录，等价于干净依赖集 |
| `pip install --user` | PEP 668 外部管理环境拒绝 | 仅对 **pip 自身**使用 `--break-system-packages`（写入 `~/.local`，不动系统包）；被测依赖仍用隔离 `--target` |
| `get-pip.py` 未锁版 | pip 是工具不是运行时依赖 | 记录局限：第二环境的 pip 版本未纳入锁；被测依赖全部锁版 |
| Go 侧未在第二环境重建 | WSL 未安装 Go，且无 sudo/无包管理器权限 | 记录为未做；Go↔Python 一致性已由 T003 在 Windows 上逐向量验证。不声称 Go 在 Linux 重建通过 |

## 2.1 复核后补充（可重跑入口与 SBOM）

复核指出"第二环境只有一份 JSON，没有可重跑入口"以及"缺 SBOM/许可清单"。已修：

- `tools/second_env_probe.sh` 是提交进仓库的可重跑脚本：按同一把哈希锁装到隔离
  `--target` 目录并重跑探针（无需 sudo、无需 venv）。
- 探针报告现在自带 `profile`、`command`、`host.platform`，新证据见
  [`agent-min-probe-second-env.json`](agent-min-probe-second-env.json)。
- `tools/sbom.py` 生成 [`sbom.json`](sbom.json)：48 个包的版本、sha256 与许可证，
  0 缺失、0 版本不符、0 许可证未声明。
- 镜像 digest：本阶段**未构建任何应用镜像**，因此显式记 `not_run`，不用占位 digest 冒充。

## 2.2 缺口补齐：生产/测试/训练三套锁

复核还指出本票切片要求"分别锁生产/测试/训练依赖"，而当时只有一套运行时锁。现在三套都在仓库里：

| 集合 | 锁文件 | 包数 | 安装验证 |
|---|---|---|---|
| 运行时 | `probes/agent_min/requirements.lock.txt` | 48 | `--require-hashes` 装入 `probes/agent_min/.venv`，`pip check` 干净 |
| 测试 | `tests/requirements-test.lock.txt` | 8 | `--require-hashes` 装入 `tests/.venv-test`，`pytest 9.0.3` 可运行 |
| 训练 | `experiments/routing_sft/requirements-train.lock.txt` | 58 | **仅解析**，`install_verified: false` / `not_run`（无训练宿主与冻结数据） |

三套锁各自带 `.in` 顶层约束，均按每版本**全部** artifact 哈希锁定；`reports/p01/sbom.json`
的 `lock_sets` 记录每套的包数、哈希覆盖率与验证方式。测试
`tests/tooling/test_lock_sets.py` 断言三套文件存在、互相独立、每包都有哈希，
且训练那套**不得**被标成已验证安装。

## 3. 覆盖的验收条件

- [x] 另一干净环境重建最小调用（Linux + 全新依赖树 + 同断言）
- [x] 无 `latest` 或研究版本冒充实装（精确 `==` + sha256；两平台版本逐项相同）
- [x] 失败包及不采用原因可追溯（上表）

## 4. 边界

- 第二个环境是 WSL，不是独立物理机；它证明的是"跨 OS/Python 构建可复现"，不是"另一台机器可部署"。
- 该 Linux 环境**没有** runsc/gVisor 且无 root，所以本票不构成任何沙箱隔离验收。
