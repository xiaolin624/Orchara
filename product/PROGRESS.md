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
| T002 | P01 | 从锁定依赖运行一个只有授权工具的 Agent | passed | `reports/p01/T002-agent-min-probe.md`、`probes/agent_min/`（锁 48 包 + 探针） |
| T003 | P01 | 让 Go/Python 对同一清单给出相同指纹 | passed | `reports/p01/T003-jcs-parity.md`、`reports/p01/jcs-agreement.json`（40 向量 0 不一致） |
| T004 | P01 | 在第二个干净环境重建核心探针 | passed | `reports/p01/T004-second-environment.md`（WSL Linux 重建 + 跨平台指纹一致） |
| T005 | P02 | 让一个中断任务在新进程中安全继续 | passed | `reports/p02/T005-checkpoint-resume.md`（真实 PG，7 passed） |
| T009 | P02 | 升级后恢复旧图或明确报告缺制品 | passed | `reports/p02/T009-artifact-pinning.md`、`db/migrations/0002_artifacts.sql`（11 passed） |
| T006 | P03 | 启动一个拒绝危险配置的 Linux 沙箱探针 | passed | `reports/p03/T006-sandbox-probe.md`（WSL2 + runsc 20241118.0，15 passed） |
| T010 | P03 | 证明脚本无法读越权文件或绕过网络代理 | passed | `reports/p03/T010-isolation-matrix.md`（最小 OCI bundle，17 passed） |
| T014 | P03 | 停止超限脚本并证明资源已回收 | passed | `reports/p03/T014-resource-limits.md`（24 passed；OCI 内存上限未生效，改用 ulimit） |
| T013 | P04 | 模拟租约失联而不启动第二个执行者 | passed | `reports/p04/T013-lease-loss.md`、`db/migrations/0004_fence_tokens.sql`（14 passed，p04 共 30） |
| T016 | P04 | 等待旧进程、沙箱和数据库写全部排空再恢复 | passed | `reports/p04/T016-drain-gate.md`（9 passed，含真机集成） |
| T018 | P04 | 写成功丢回执后恢复仍只写一次 | passed | `reports/p04/T018-lost-receipt.md`（16 passed，账本恰好 1 条） |
| T007 | P05 | 用同一 corpus 校验两语言的变量和类型 | passed | `reports/p05/T007-dsl-parity.md`、`probes/p05_dsl/`（97 新测试，p05 共 105） |
| T011 | P05 | 在真实图引擎中验证分支和汇合结果 | passed | `reports/p05/T011-graph-branching.md`（8 passed，LangGraph 实跑） |
| T008 | F00 | 启动产品并在浏览器看到真实健康状态 | passed | `reports/f00/T008-product-skeleton.md`、`cmd/api/`、`internal/health/`（4 passed） |
| T012 | F00 | 按环境 profile 启动并重现一次 CI 验收 | passed | `reports/f00/T012-profile-and-ci.md`、`internal/config/`、`tools/ci_checks.py`（15 passed） |

## 本轮小结（D00 + P01–P05 + F00，共 16 票）

| 状态 | 数量 | 票 |
|---|---|---|
| passed | **16** | T001–T007, T008–T014, T016, T018（全部） |
| blocked | 0 | — |
| not_started | 0 | — |

16 票全部通过并关闭。**这不等于完整首版**：本批只是"地基"（D00 + P01–P05 + F00），
覆盖环境探测、依赖锁定、双语言指纹与 DSL 校验、检查点恢复与制品钉版、
Profile/CI、沙箱隔离与资源回收、租约与丢回执。

仍未实现（属后续票，不在本批范围）：业务模块（身份/目录/请求/路由/执行/评测/RAG/记忆）、
前端画布、编译器与十类节点、发布门禁、部署与灾备。仓库 open issue 仍为 108 个。

## 第二轮复核修复（2026-11-01）

第二次两轴复核（`9707af3...eb13188`）发现 21 项，全部处理：

| 类别 | 问题 | 修法 |
|---|---|---|
| 硬违规 | CI 门禁把 skipped 当通过（`ok = not failed`） | 判定改为 `ok / partial / failed`，skipped 即 `partial` 且非零退出；`--allow-skipped` 只降退出码不改判定 |
| 硬违规 | 沙箱校验了挂载却从不施加 | `do` 路径直接拒绝任何 mounts 并指向 OCI bundle（真正施加只读 bind 的地方） |
| 硬违规 | 4 处硬编码 `E:\orchara-toolchains` | 新增 `tools/toolchain.py`，`pg_dev`/`probe_environment`/测试/CI 全部走它 |
| 硬违规 | T012 报告称"迁移只有 0001" | 改为不列清单，指向 T008 报告 |
| 证据 | T014 的 `ulimit` 写在被测命令里 | 改用 OCI `process.rlimits`（`RLIMIT_AS`/`RLIMIT_CPU`），由运行施加；补 CPU 与输出两类用例 |
| 证据 | `feature-flag.json` 是测试自己写的 | 删除该文件；开关只由 `ORCHARA_ALLOW_ARBITRARY_SCRIPTS` 决定，`record_failure` 不再写盘 |
| 证据 | T010 网络断言无法区分"被阻断"与"工具坏了" | 增加 guest 侧对照组（`CONNECTED`），沙箱内改记真实退出码与错误文本；代理一行如实说明 busybox `wget` 不读 `HTTP_PROXY` |
| 证据 | T010 的 env 修复无测试 | 新增宿主密钥不可见 / 批准变量可见 / 未批准变量被拒 / 启动命令带 `env -i` 四条测试 |
| 证据 | T011 的"真引擎"断言是常量 | 改为让引擎拒绝并发写非 reducer key（`InvalidUpdateError`），桩无法伪造 |
| 证据 | T009 无机器可读产物 | 新增 `probes/p02_checkpoint/artifact_evidence.py` 生成 `T009-artifact-pinning.json` |
| 记录 | T007 "corpus 由实现生成"的指控 | 核实后更正：33/33 负例的原因码是手写并被 builder 强制；真正派生的是 valid 摘要与 reasons 细目（仅 1 条锚点），已如实写入报告 |
| 卫生 | psql 块复制 4 份且碰私有函数 | `pg_dev.run_sql()` / `quote_literal()` 公开，`drain_gate` 改用 |
| 卫生 | supervisor 死代码与机器 JSON 里的散文 | 删 `validate()` 死分支与 `NeverRestartedNote`，改名 `ErrNoStore` |
| 卫生 | worker 直写 `app.workers` 与 DATA-MODEL §48 冲突 | 新增 `0005_worker_grants.sql` 显式授权该表并撤销其余写权限 |
| 记录 | T016 报告"属 T013"已过期 | 改为如实说明 T013 未接线 |



本轮 6 票已在 `xiaolin624/Orchara` 关闭（`closed / completed`），每票附实测证据评论：

| 票 | Issue | 分支提交 |
|---|---|---|
| T001 | [#2](https://github.com/xiaolin624/Orchara/issues/2) | `110bc6e` |
| T002 | [#3](https://github.com/xiaolin624/Orchara/issues/3) | `be97f87` |
| T003 | [#4](https://github.com/xiaolin624/Orchara/issues/4) | `62f96f6` |
| T004 | [#5](https://github.com/xiaolin624/Orchara/issues/5) | `71da374` |
| T005 | [#6](https://github.com/xiaolin624/Orchara/issues/6) | `d8e059b` |
| T008 | [#9](https://github.com/xiaolin624/Orchara/issues/9) | `a4eb7f8` |

代码在 `integration/foundation-p0`（已推送）。仓库 open issue 数由 121 降至 115。

## 环境事实（D00 实测 + 本轮补充）

- Go 1.27.3、PostgreSQL 16.4（用户态，仓库外 `E:\orchara-toolchains`）、Python 3.12.2、Node 24.13.0、npm 11.13.0。
- 隔离开发库：`tools/pg_dev.py` 在 `127.0.0.1:55432`，数据目录 `E:\orchara-pg`（仓库外）；
  本机 5432 的 `postgresql-x64-16` 属无关服务，探针从不使用。
- 无 Docker、无 Redis、WSL 有 Ubuntu-24.04 但**无 sudo**（第二环境用 `--target` 隔离安装）。
- GPU：RTX 3050 Laptop 4 GiB；未跑训练 → F14 `not_run`。
- 无模型供应商凭据 → 只能报告 stub。
- 锁版环境：`probes/agent_min/.venv`（48 包，全部 sha256 锁定）；SBOM/许可清单见 `reports/p01/sbom.json`。
- 本地开发口令只放在 **git-ignored 的 `product/.env.local`**（`ORCHARA_PG_PASSWORD`）。
  仓库、测试、报告均不含口令；`tools/pg_dev.py` 未设置时 fail closed。

## 代码复核修复（2026-11-01）

两轴复核（Standards / Spec）发现的 25 项已逐条修复，其中影响结论的：

- 凭据从代码/测试/报告移除，改由环境或 git-ignored 文件提供；泄漏断言改为匹配 DSN 形状。
- 漂移检查现在同时校验 `docs/spec` 与 `product/contracts`，并按 LF 归一化哈希
  （换行差异不再误报，契约被改动会失败）。
- 环境探测不再把本机 5432 的**无关** PostgreSQL 记为本项目能力，改为
  `foreign_postgres: present_not_owned`，自有集群用 55432。
- T003 的 `expected_sha256` 由 RFC 文本独立推导并真正断言（原断言恒真）。
- T005 的 `run/resume/approve` 现在真的以 `orchara_worker` 受限角色执行，并在运行路径上
  校验 `search_path`。
- T008 同时提供契约路径 `/health/live`、`/health/ready`；状态页的存活来自 `/health/live`，
  不再写死。
- T002 探针实际使用锁定栈（pydantic 结果模型 + jsonschema 报告校验），并新增
  `tools/bootstrap_probe.py` 让干净环境可复现。
- T004 新增 `tools/second_env_probe.sh`（可重跑）与 `reports/p01/sbom.json`；
  镜像 digest 明确记 `not_run`（本阶段未构建镜像）。

下面各票"证据"列中的提交号是**本轮修复前**的关闭提交；修复提交见分支历史。

## 关闭票内的两处缺口（已补齐）

复核时承认过：这 6 张票虽然关着，但按它们自己的文字还有两行没兑现。现已补上：

| 缺口 | 票 | 补法 | 证据 |
|---|---|---|---|
| 多套依赖锁 | T004 | 三套独立锁：运行时 48 包、测试 8 包、训练 58 包，全部 sha256 锁定并分别验证安装 | `probes/agent_min/requirements.lock.txt`、`tests/requirements-test.lock.txt`、`experiments/routing_sft/requirements-train.lock.txt`、`reports/p01/sbom.json` |
| supervisor 模块 | T008 | `cmd/supervisor` + `internal/supervisor`：advisory lock 保证单实例，只把死 worker 的 run 置为 `recovery_pending`，**从不重启**；worker 现在会注册与心跳 | `internal/supervisor/supervisor.go`、`db/migrations/0001_worker_lease.sql`、`tests/f00/test_supervisor_integration.py` |

训练锁只做到**解析**：安装验证记 `install_verified: false` / `not_run`，因为没有训练宿主与冻结数据，
不把"能解析"写成"装过"。

## T010 侦察发现（已修复）

在 WSL2 + runsc 上做 T010（越权文件/网络）侦察时，实测到一个真实缺陷：

```text
ORCHARA_TEST_SECRET=leakme runsc --network=none do sh -c 'env | grep ORCHARA_TEST_SECRET'
→ ORCHARA_TEST_SECRET=leakme        # 宿主环境变量被继承进沙箱
```

修复：沙箱启动统一包在 `env -i`，只传 `PATH` 与 `ORCHARA_APPROVED_` 前缀的批准变量；
其余由策略层拒绝。修后实测：宿主密钥不可见、批准变量可见、未批准变量被拒。

同批侦察还发现 `runsc do` 会以 root 挂载宿主文件系统（可读），因此用户脚本改用
**最小 OCI bundle**（自有 rootfs、uid 65534、零 capabilities、只挂批准输入）。见
`reports/p03/T010-isolation-matrix.md`。



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
