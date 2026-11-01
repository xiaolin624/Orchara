# 产品实现进度（权威）

本文件是平台实现的进度权威。规格仓库里的 `docs/spec/PROGRESS.md` 只保留接手时的初始快照。

状态取值：`not_started` / `in_progress` / `blocked` / `passed` / `failed` / `not_run`。
`passed` 必须满足该票在 `contracts/task-graph.json` 的完成条件；缺外部条件写 `blocked`，
未执行写 `not_run`。**静态校验通过、探测到硬件、stub 成功都不升级为 passed。**

## 当前发布范围

本轮实现范围（用户确认）：**D00 + P01–P05 + F00 骨架**，其余票保持 open。
平台整体**未实现**，无业务服务、无迁移、无前端、无模型调用。

## 票状态

| 票 | 父 | 标题 | 状态 | 证据 |
|---|---|---|---|---|
| T001 | D00 | 在产品工作区复现规格并生成环境报告 | passed | `reports/p0/D00-environment-report.md`、`reports/p0/spec-*.txt`、`tools/probe_environment.py` |
| T002 | P01 | 从锁定依赖运行一个只有授权工具的 Agent | passed | `reports/p01/T002-agent-min-probe.md`、`probes/agent_min/`（锁 47 包 + 探针） |
| T003 | P01 | 让 Go/Python 对同一清单给出相同指纹 | passed | `reports/p01/T003-jcs-parity.md`、`reports/p01/jcs-agreement.json`（40 向量 0 不一致） |
| T004 | P01 | 在第二个干净环境重建核心探针 | passed | `reports/p01/T004-second-environment.md`（WSL Linux 重建 + 跨平台指纹一致） |
| T005 | P02 | 让一个中断任务在新进程中安全继续 | not_started | — |
| T009 | P02 | 升级后恢复旧图或明确报告缺制品 | not_started | — |
| T006 | P03 | 启动一个拒绝危险配置的 Linux 沙箱探针 | blocked | 无 Linux 宿主 + runsc/gVisor + root（D00 报告 §6） |
| T010 | P03 | 证明脚本无法读越权文件或绕过网络代理 | blocked | 依赖 T006 |
| T014 | P03 | 停止超限脚本并证明资源已回收 | blocked | 依赖 T006/T010 |
| T013 | P04 | 模拟租约失联而不启动第二个执行者 | not_started | — |
| T016 | P04 | 等待旧进程、沙箱和数据库写全部排空再恢复 | blocked | 需要 Linux 沙箱证据（T006 系） |
| T018 | P04 | 写成功丢回执后恢复仍只写一次 | blocked | 依赖 T016 |
| T007 | P05 | 用同一 corpus 校验两语言的变量和类型 | not_started | — |
| T011 | P05 | 在真实图引擎中验证分支和汇合结果 | not_started | — |
| T008 | F00 | 启动产品并在浏览器看到真实健康状态 | passed | `reports/f00/T008-product-skeleton.md`、`cmd/api/`、`internal/health/`（4 passed） |
| T012 | F00 | 按环境 profile 启动并重现一次 CI 验收 | not_started | — |

## 环境事实（D00 实测）

- Go 1.27.3、PostgreSQL 16.4（用户态，仓库外 `E:\orchara-toolchains`）、Python 3.12.2、Node 24.13.0、npm 11.13.0。
- 无 Docker、无 Redis、WSL 有 Ubuntu-24.04 但**无 sudo**。
- GPU：RTX 3050 Laptop 4 GiB；未跑训练 → F14 `not_run`。
- 无模型供应商凭据 → 只能报告 stub。

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
