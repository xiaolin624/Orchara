# 产品工作区（Orchara 平台实现）

本目录是**产品代码工作区**，不是规格包。规格是只读来源，位于本仓库根目录，并被完整复制到
[`docs/spec/`](docs/spec/) 作为实现依据与漂移比对基线。契约运行快照在 [`contracts/`](contracts/)。

- 规格入口：[docs/spec/HANDOFF.md](docs/spec/HANDOFF.md)
- 实现进度权威：[PROGRESS.md](PROGRESS.md)
- 决策记录：[docs/adr/](docs/adr/)
- 术语表：[docs/spec/GLOSSARY.md](docs/spec/GLOSSARY.md)

## 当前状态（不要读成"平台已完成"）

平台**尚未实现**。当前只有 D00 环境探测与规格复现产物、以及工具脚本。没有任何业务服务、
数据库迁移、前端画布或模型调用。真实状态以 [PROGRESS.md](PROGRESS.md) 为准。

已知阻塞（D00/T001 实测）：

| 阻塞 | 影响 | 解除条件 |
|---|---|---|
| `linux_isolation` | P03 沙箱票（T006/T010/T014）、F13B 隔离验收无法执行 | 真实 Linux 宿主 + runsc/gVisor + root |
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

```powershell
# 规格复现与漂移检查
python tools/sync_spec.py
python tools/sync_spec.py --check

# 环境探测（生成 reports/p0/environment-probe.{json,md}）
python tools/probe_environment.py

# 工具测试
python -m pytest tests/tooling -q
```

规格自带的静态校验（在仓库根目录执行）：

```powershell
tools/.venv/Scripts/python.exe tools/validate_contracts.py
tools/.venv/Scripts/python.exe tools/validate_handoff.py
tools/.venv/Scripts/python.exe tools/check_handoff_package.py
```
