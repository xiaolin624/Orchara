# F13B T111 验收：在正式 Linux 重跑构建与脚本隔离攻击

- 任务：F13B / GitHub Issue #112（票 `T111`），分支 `t/t111-linux-isolation`
  （worktree `E:\Orchara-wt\t111`，基线 `integration/f13b` @ `f6e0394`）
- 结束前 merge `integration/f13b` @ `5a210c0` → merge commit `abfd01a`（PROGRESS 冲突按“保留双方小节”解决：T108/T109 + T111）；合并后树复跑见 §4。
- 状态：**`passed`**（10 条攻击用例 + 1 条端到端正控制，全部在真实
  WSL2 Ubuntu-24.04 + gVisor `runsc` 中通过产品路径执行；无 mock、无跳过）
- 验收宿主（ADR 0002）：**WSL2 Ubuntu-24.04**，Linux `6.6.87.2-microsoft-standard-WSL2`，
  `runsc version release-20241118.0`（spec 1.1.0-rc.1），rootfs
  `sha256:03c3f647d1d6e68173bfa9df84b87e5d6162d351bfdd55e4c3b208c0b5412ff5`
  （1093 文件 / 46 MiB）。这是 WSL2 上的 runsc，不是独立 Linux 服务器；报告不使用
  Windows 本地进程结果冒充 runsc 验收。
- 主要文件：
  - `product/tests/f13b/test_t111_linux_isolation.py`（10 用例：run 阶段 4 + build 阶段 5 +
    端到端正控制 1）
  - `product/tests/support/t111.py`（宿主/内核/runsc/rootfs 事实、probe/build 环境、WSL 清理真值、
    证据记录器；仅 `F13B_WRITE_EVIDENCE=1` 时写回）
  - `product/reports/f13b/T111-isolation.json`（逐攻击记录）
  - `product/deploy/sandbox/isolation_probe.py`（**仅观测字段扩展**：宿主/挂载/环境/权限/升级
    尝试；OCI bundle、driver、uid/caps/挂载/网络/限额语义未改）
  - 本报告、`product/PROGRESS.md`
- 真实/桩分报：**全部 real**（WSL2/runsc、真实 OCI bundle、真实宿主文件/证书/对象目录 decoy、
  真实 maintenance `tool_build` 与 supervisor `exec`、真实 PG/mTLS 用于 build/端到端用例）。
  合成物只有测试自造的 zip 源、decoy 文件、恶意 `setup.py` 与 marker；模块仅 dev-stub，
  真实供应商模型/连接器 `not_run`。

## 0. 工作记录（简短）

1. 读 `briefs/T111.md`、`ENV.md`、`EVALUATION.md` §5、ADR 0002、T033/T037/T010 报告与
   `tests/f05/test_t033_sandbox_run.py`、`test_t037_build_isolation.py`、`test_t037_build.py`、
   `deploy/sandbox/*`、`tests/support/{t033,t037}.py`，确认现有真实路径：
   `cmd/sandboxprobe`（run 阶段）与 T037 `tool_build`（build 阶段，`setup.py` 是不可信代码）。
2. TDD 切片：先写 run 阶段断言并用**旧 probe** 跑出红（`KeyError: 'privileges'`），再扩展
   probe 观测字段转绿；随后逐条加入 build 攻击、fork/内存/磁盘/时间限制与端到端正控制。
3. 校准真实运行时行为（只用于设计用例，不作为验收证据）：pids 限额、内存限额、磁盘 tmpfs
   配额在 gVisor 下的实际表现（见 §5 发现）。
4. 在 `realstack` 锁内、无其它 runsc 使用者时跑完 T111、T033/T037 回归、`tests/f13b` 全量、
   Go 门禁；所有原始 tail 在 `E:\Orchara\.scratch\f13b\T111\`。
5. 未改 `docs/spec/**`、`contracts/**`、迁移、shell driver / OCI bundle 构建；`sync_spec --check`
   `no drift`。

## 1. 攻击矩阵与实测结果

宿主身份（每条证据都写入 `T111-isolation.json` 的 `environment` 与每个 case 的 `host`）：
WSL2 Ubuntu-24.04 / kernel `6.6.87.2-microsoft-standard-WSL2` / runsc
`release-20241118.0`（spec 1.1.0-rc.1）/ rootfs digest
`sha256:03c3f647…12ff5`。

### 1.1 run 阶段（`cmd/sandboxprobe --mode isolation`，真实 executor + runsc）

| # | 攻击 | 实测结果（真实 errno / 观测） | 阻断层 |
|---|---|---|---|
| R1 | 读 `/etc/hostname`、`/etc/shadow`、`/etc/passwd`、`/etc/os-release`、`/root/.ssh/id_rsa`、`/var/lib/postgresql/.pgpass` | 全部 `FileNotFoundError` **ENOENT**（rootfs 无宿主 `/etc`） | gVisor mount namespace + 只读最小 rootfs |
| R1 | 读 `/mnt/c/.../hosts`、`/mnt/wsl`、`/mnt/e/Orchara/product/contracts/database.sql` | 全部 **ENOENT**（宿主盘未挂载；客人 `/mnt` 在 bundle 内不存在） | 挂载策略（bundle 只挂 5 个批准路径） |
| R1 | 读另一 Run 工作区 `/var/tmp/orchara-sandbox/other-run/secret.txt`、driver decoy `foreign/secret.txt` | 全部 **ENOENT**；客人侧 root 读同一 decoy 成功（正控制） | mount namespace |
| R1 | 写 `/usr/bin/python3.12`、`/etc/passwd`、`/etc/hostname`、`/tool/probe.py`、`/tool/build_tool.py`、`/inputs/input.csv` | 全部 `PermissionError` **EACCES**；`/workspace` 写+读成功（正控制） | 只读 rootfs/tool/inputs 挂载 |
| R1 | dump 环境变量与 `/proc/1/environ` | 仅有 OCI 固定 6 项（`HOME/PATH/PYTHONDONTWRITEBYTECODE/PYTHONHASHSEED/PYTHONHOME/PYTHONNOUSERSITE`）；`credential_prefixed=[]`、`outside_oci_allowlist=[]` | OCI `process.env` 固定；不继承宿主环境 |
| R1 | 宿主进程 env 注入 `ORCHARA_T111_ENV_SECRET / ORCHARA_DB_DSN / ORCHARA_SIGNING_KEY / ORCHARA_CONNECTOR_SECRET`，以及虚假 `ORCHARA_SANDBOX_UID=0 / PRIVILEGED=1 / CAPABILITIES=ALL / NO_NEW_PRIVS=0` | 沙箱 env 中**没有**任何 `ORCHARA_*`；probe 仍是 `uid/gid 65534`、`NoNewPrivs=1`（`PR_GET_NO_NEW_PRIVS` 实测 1）、`CapInh/CapPrm/CapEff/CapBnd/CapAmb=0`；假配置键被产品忽略 | 产品 bundle 不读取这些键；运行时固定身份 |
| R1 | `setuid(0)` / `setgid(0)` | **EPERM** | gVisor + uid 65534 + 空 capability + NoNewPrivs |
| R1 | `mount("tmpfs", …)` / `unshare(CLONE_NEWNS)` / `capset(CAP_SYS_ADMIN)` | **EPERM** | 运行时（无 CAP_SYS_ADMIN） |
| R1 | 清 `no_new_privs`（`prctl(PR_SET_NO_NEW_PRIVS,0)`） | **EINVAL**（不可清除） | 内核/gVisor |
| R1 | `unshare(CLONE_NEWUSER\|CLONE_NEWNS)` | **允许**（gVisor 允许非特权 user namespace）；随后：uid/gid 仍 65534、`setuid(0)` **EINVAL**、tmpfs 挂载仅沙箱本地成功、3 个宿主路径读取仍 **ENOENT**、`/workspace` 写成功 | 运行时允许但**未获得宿主权限**；见 §5 发现 2 |
| R1 | 挂载表 | 恰为 `/`(ro) `/sys` `/dev` `/dev/pts` `/tmp`(noexec) `/proc` `/tool`(ro) `/inputs`(ro) `/workspace`(rw)；无 `/mnt`、无证书/密钥路径 | OCI mounts + runsc 固定运行期挂载 |
| R2 | 网络正控制：客人侧 127.0.0.1:18080 真 listener | 客人连接 **CONNECTED**（检测方法有效）；停止后客人连接 **ECONNREFUSED** | 独立 guest netns 对照 |
| R2 | 沙箱内连 `1.1.1.1:443`、`8.8.8.8:53`、`169.254.169.254:80` | 全部 **EHOSTUNREACH** | `--network=none` + 新 network namespace |
| R2 | 沙箱内连宿主/客人 loopback `127.0.0.1:18080` | **ECONNREFUSED**（沙箱自己的 loopback 无监听） | network namespace |
| R3 | 通过部署 env 配置超限 profile：`MEMORY_MB=999999`、`WORKSPACE_QUOTA_MB=99999`、`PIDS=not-a-number`、`TIMEOUT=-5s` | `cmd/sandboxprobe` 退出 1，稳定错误：`ORCHARA_SANDBOX_MEMORY_MB is not a valid limit` / `ORCHARA_SANDBOX_WORKSPACE_QUOTA_MB must be a positive integer at most 4096` / `ORCHARA_SANDBOX_PIDS is not a valid limit` / `ORCHARA_SANDBOX_TIMEOUT must be a positive duration`；没有创建任何 runsc 单元（`runsc list` 空、state root 无新增） | **产品策略层** `sandbox.OptionsFromEnv`（在任何单元创建前） |
| R4 | 时间超时：probe `slow --seconds 60`，sandbox/timeout 2s | 2.597s 返回 `outcome=timeout`、`timed_out=true`；driver cleanup `sandbox_list_matches=0`、`pid=932`、`pid_alive=false`、`ps_matches=0`、`workspace_removed=true`；独立客人检查无 sandbox/进程/目录/挂载，宿主 staging 空 | driver timeout + executor kill + cleanup 证明 |

### 1.2 build 阶段（真实 T037 `tool_build`，不可信 `setup.py` 钩子）

| # | 攻击 | 实测结果（真实 errno / 观测） | 阻断层 |
|---|---|---|---|
| B1 | `setup.py` 读 `/etc/passwd|shadow|hostname`、`/mnt/c/.../hosts`、真实仓库文件 `/mnt/e/Orchara-wt/t111/product/PROGRESS.md`、对象目录 decoy、DSN decoy、mTLS worker 私钥、另一 Run workspace、driver foreign decoy | 10/10 全部 `FileNotFoundError` **ENOENT**；客人侧 root 对同一 repo/object/DSN/other-run 路径读取成功（正控制）；worker key sha256 前后不变 | uid 65534 + 宿主盘未挂载 + 只读 rootfs |
| B1 | 写 `/etc/passwd`、`/inputs/source.zip`、`/tool/build_tool.py` | `PermissionError` **EACCES**（只读挂载） | 只读 bind/rootfs |
| B1 | 写宿主对象目录与另一 Run workspace | `FileNotFoundError` **ENOENT**（未挂载） | mount namespace |
| B1 | 写 `/workspace` | 成功（构建产物仍写入本构建唯一的沙箱 workspace） | 可写 tmpfs |
| B1 | dump 钩子 env（build_tool 已剥离）与 `/proc/1/environ`（build_tool 的 OCI env） | 钩子仅 8 项、PID 1 仅 6 项，均无 `ORCHARA_*`/DSN/secret 名或值；`secret`/db 名未出现在构建日志；状态 `NoNewPrivs=1`、caps 全 0 | 产品剥离 env + OCI env 固定 |
| B1 | 构建是否仍成功、产物是否唯一 | `ready`；`image_digest ==` 由 source/canonical lock/SBOM 独立重算的 bundle digest；无宿主篡改、无额外产物 | 完成守卫 + 内容寻址 |
| B2 | fork bomb（200 次）vs 限制 64 | 对照 12 子进程 `ready`；同一 16 子进程在 64 下被停止、在 128 下 `ready`（边界随配置移动）；200 次攻击 `failed/build_failed`、无 digest、攻击未打印完成 marker | **OCI pids cgroup 限额**（runsc 直接停止容器，未给 EAGAIN） |
| B3 | 内存耗尽：子进程申请 512 MiB，限额 192 MiB | 1 GiB 对照真实分配 512 MiB（`survived`）；192 MiB 下进度到 **152 MiB** 后子进程退出码 1、未 `survived`，父钩子存活并上报；构建仍 `ready`、digest 正常 | **OCI memory limit** |
| B4 | 磁盘写满：向 `/workspace` 写 64 MiB，限额 8 MiB | 写到 **7 MiB** 时 `OSError` **ENOSPC**（errno 28）；删除填充文件后构建 `ready`；客人 `/` 文件系统 used 增量 **0 字节**（写入只落在 tmpfs） | **tmpfs size quota**（+ RLIMIT_FSIZE 64 MiB） |
| B5 | 时间超时：钩子 sleep 60s，构建预算 2s | 6.212s 结束；tool/job `failed`、`build_error_code=build_timeout`、`image_digest`/`build_bundle_digest` 均为 NULL；独立客人检查无 sandbox/进程/目录/挂载，宿主 staging 空 | 沙箱 executor timeout + kill + cleanup |

### 1.3 端到端正控制

`upload CSV → debug Run → 真实 cmd/supervisor exec --once → runsc 工具运行 → 结果/退出证据`：
`outcome=succeeded`、`run_state=succeeded`、`sandbox.started=true`、driver cleanup
`list=0/pid_alive=false/ps=0/workspace_removed=true`；独立客人检查该 sandbox id 无进程/无目录/无挂载，
宿主 staging 空。它证明隔离加固没有破坏真实产品执行路径。

## 2. 验收标准逐条

| AC | 状态 | 证据 |
|---|---|---|
| 1. 真实 Linux 构建/运行均拒绝文件与凭据越权 | **passed** | R1（run 阶段 6 类宿主文件 + 3 类 `/mnt` + 其它 Run 工作区 + env/PID1 environ + 只读挂载写入）、B1（build 阶段 10 条精确宿主路径读 + 5 条写 + 钩子/PID1 env）全部真实拒绝并记录 errno；运行阶段由 `cmd/sandboxprobe`，构建阶段由真实 `tool_build`+`build_tool.py` 执行 |
| 2. 出站和宿主挂载策略受验证 | **passed** | R2：真实客人 listener 正控制（CONNECTED）与沙箱内 EHOSTUNREACH/ECONNREFUSED；R1 挂载表恰为批准集合，`/tool`/`/inputs` ro、`/workspace` rw，无 `/mnt`/证书路径；B1 构建沙箱同一挂载表 |
| 3. fork/内存/磁盘/时间超限可停止清理 | **passed** | B2（pids 限额、对照/128 比例对照）、B3（1 GiB 对照 vs 192 MiB 停止在 152 MiB）、B4（ENOSPC at 7 MiB、宿主卷 0 增长）、R4/B5（2s/2s 预算真实 kill）；每条都有独立客人 reclamation（`runsc list` 空、id 无进程/目录/挂载、宿主 staging 空） |
| 4. 报告不使用 Windows 结果冒充 runsc 验收 | **passed** | 每条记录写入 WSL2 kernel / runsc 版本 / rootfs digest；Windows 进程只负责发起 `wsl`/二进制，所有攻击答案来自客人内部 syscall/errno；环境段明确 "WSL2 … not a standalone Linux server" |
| 5. 端到端演示 + 失败/授权/恢复边界 + 版本/hash/命令/结果/profile | **passed** | §1.3 端到端；失败/超时/限额/拒绝路径均有断言；授权边界：R3 产品配置层拒绝、B1 只读挂载与只读源；恢复边界：超时后无 orphan、无 digest/结果发布；§4 列真实命令与原始 tail；证据 JSON 含 profile 与每 case host/inputs/observed/cleanup |

## 3. 清理证据（真实回收，不只看退出码）

每条用例执行后独立读取客人真值（`tests/support/t111.py:cleanup_snapshot`），记录于证据 JSON：

- `runsc --network=none list`：**空**（只有表头，过滤后 0 行）；全部 10 条用例后均为空。
- 本用例 sandbox id：不在 `runsc list`、无对应 `runsc/timeout` 进程、`/var/tmp/orchara-sandbox/<id>`
  目录不存在、无对应 tmpfs 挂载。
- 共享 state root 的**新增**目录：0。基线已有 `t033-manual-1`（更早人工 T033 运行遗留，非本票产生；
  本票不删他人遗留物），每条用例都断言 `after ⊆ baseline`。
- 宿主 staging root（`ORCHARA_SANDBOX_WORKSPACE_ROOT` 指向测试临时目录）：每条用例后为空。
- driver 报告（run 阶段）：`sandbox_list_matches=0`、`pid_alive=false`、`ps_matches=0`、
  `workspace_removed=true`；超时用例另有 driver 独立 cleanup 的 `pid=932` 证明进程曾启动后被回收。
- 构建阶段另验证：输出/日志 artifact 只在唯一构建目录产生、内容包 digest 由 source/lock/SBOM 独立重算；
  另一 Run 工作区与对象目录 decoy 前后哈希/存在性不变。

## 4. 真实命令与原始 tail

工作目录 `E:\Orchara-wt\t111\product`；`ORCHARA_PG_PASSWORD` 从本地 git-ignored `.env.local`
注入（未打印/未提交）。原始日志：`E:\Orchara\.scratch\f13b\T111\`。

```text
$ python -m pytest tests/f13b/test_t111_linux_isolation.py -q
10 passed in 131.98s (0:02:11)                     [exit 0]

$ F13B_WRITE_EVIDENCE=1 python -m pytest tests/f13b/test_t111_linux_isolation.py -q
10 passed in 131.98s (0:02:11)                     [exit 0]  → reports/f13b/T111-isolation.json

$ python -m pytest tests/f05/test_t033_sandbox_run.py tests/f05/test_t037_build_isolation.py -q
10 passed in 177.56s (0:02:57)                     [exit 0]

$ python -m pytest tests/f13b -q
196 passed in 307.44s (0:05:07)                    [exit 0]   (最终树，含 T111 10 条)

$ go build ./...
(no output)                                        [exit 0]
$ go vet ./...
(no output)                                        [exit 0]
$ go test ./...
39 ok / 0 FAIL / 12 no test files                  [exit 0]
$ gofmt -l internal cmd tools
(no output)                                        [exit 0]
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest   [exit 0]

# 合并后树（merge commit abfd01a，含 T108/T109；证据 JSON 在该树重新生成，commit=abfd01a）
$ F13B_WRITE_EVIDENCE=1 python -m pytest tests/f13b/test_t111_linux_isolation.py -q
10 passed in 113.56s (0:01:53)                     [exit 0]
$ python -m pytest tests/f05/test_t033_sandbox_run.py tests/f05/test_t037_build_isolation.py -q
10 passed in 138.34s (0:02:18)                     [exit 0]
$ python -m pytest tests/f13b -q
204 passed in 446.46s (0:07:26)                    [exit 0]
$ go build ./... ; go vet ./... ; go test ./...
exit 0 (39 ok / 0 FAIL / 12 no test files)         [exit 0]
$ gofmt -l internal cmd tools
(no output)                                        [exit 0]
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest   [exit 0]
```

WSL/runsc 身份（证据 JSON `environment`，每 case 重复记录）：

```text
host:    WSL2 Ubuntu-24.04 (accepted test host per ADR 0002; not a standalone Linux server)
kernel:  Linux 6.6.87.2-microsoft-standard-WSL2
runsc:   runsc version release-20241118.0 / spec: 1.1.0-rc.1
rootfs:  sha256:03c3f647d1d6e68173bfa9df84b87e5d6162d351bfdd55e4c3b208c0b5412ff5
         (1093 files, 46536 KiB, builder orchara.sandbox-rootfs/v1)
```

## 5. 发现与偏离（如实，不在本票修复）

1. **pids 限额的真实边界由 gVisor 运行期任务占用**：`ORCHARA_SANDBOX_PIDS=64` 时 12 个 fork 子进程
   成功、16 个被停止；把配置改为 128 后 16 个成功。说明限额确实生效且随配置移动，但 gVisor 自身
   任务占用了配置值的一部分（默认 64 时用户可见余量约 13 个进程）。超限时 runsc **直接停止容器**
   （无输出、`build_failed`），而不是给应用 `EAGAIN`。部署若需要更多并行工具进程，应提高该配置。
   本票不修改 runsc/driver。
2. **gVisor 允许非特权 user namespace**：`unshare(CLONE_NEWUSER|CLONE_NEWNS)` 成功；随后只能做
   沙箱本地 tmpfs 挂载，uid/gid 仍 65534，`setuid(0)` 返回 EINVAL，所有宿主路径仍 ENOENT，未获得
   宿主权限。若产品希望连非特权 userns 也禁止，需要在未来票评估 gVisor 配置（本票记录行为，不改
   语义）。
3. **SIGKILL 137 被 driver 归类为 timeout 的风险**：校准时用 64 MiB 内存限额导致 runsc 以 137
   退出，`sandbox_exec.sh` 将 124/137 统一标为 `timed_out`，于是 `build_error_code` 显示
   `build_timeout` 而非 OOM/崩溃。最终 B3 使用 192 MiB（父进程存活并上报）避开误分类。这是可诊断性
   bug（不影响“停止/清理”结论），建议后续把“driver 自己的 timeout 触发”与“runtime SIGKILL”分开
   记录；本票只报告，不改 driver 语义（除非维护者批准）。
4. **磁盘**：产品真实的磁盘保护 = `/workspace` size 限定的 tmpfs + `RLIMIT_FSIZE`，B4 实测 ENOSPC
   且宿主卷 0 增长。宿主级 filesystem project quota 在 WSL2 上不可用，属 F15B 部署平台，记 `not_run`
   （不是本票 AC 的前置条件）。
5. **probe 扩展是观测性的**：`deploy/sandbox/isolation_probe.py` 新增 host/env/mount/capability/
   userns 观测与 errno 记录；`internal/sandbox` 的 bundle（uid/caps/no_new_privs/mounts/limits）、
   `sandbox_exec.sh`、`build_tool.py` 安全语义未改。T033/T037 回归 10/10 通过。

## 6. `not_run` / 边界

- `not_run`：宿主级 filesystem project quota（WSL2 无该能力；产品 tmpfs 配额已实测）。
- `not_run`：完整出站协议矩阵（ICMP/UDP/DNS/代理穿透）——本票记录真实 TCP connect 失败 + 真实客人
  listener 正控制；`--network=none` 下代理端点不可达属 P03 既有结论。
- `not_run`：WSL2 之外的独立 Linux 生产宿主（ADR 0002 只接受 WSL2 作验收宿主；生产交付仍需独立宿主）。
- `not_run`：真实供应商模型 / connector 凭据 / 真实第三方端点（环境只有 `dev-stub`）。
- 未覆盖：并发多 sandbox 压测、Windows 本地结果不用于任何 runsc 结论。

## 7. 结论

在 ADR 0002 接受的正式验收宿主（WSL2 Ubuntu-24.04 + gVisor runsc release-20241118.0）上，通过产品
真实路径（`cmd/sandboxprobe`、T037 `tool_build`、`cmd/supervisor exec`）重跑了文件/凭据/出站/挂载/
权限/资源攻击：**10/10 攻击用例通过，1 条端到端正控制通过，全部真实清理证据成立，无伪造的
Windows 替代结果**。唯一如实边界是宿主级磁盘 project quota 与生产独立宿主，均已标明 `not_run`。
