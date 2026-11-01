# 用户上传脚本：执行隔离选项核查

查询日期：2026-10-31。上下文：单企业私有部署，普通业务用户可视化配置 Agent，按需运行，允许上传脚本。只读官方文档与源码；没有部署、运行脚本、做逃逸测试或性能测试。下述选项均未替用户作决定。

## 分层事实

Docker/runc 与 gVisor/runsc 解决的是进程、文件系统、内核接口等执行边界；OpenSandbox 是这些运行时之上的沙箱创建、执行、文件和生命周期 API。三者并非同层互斥选项。

| 选项 | 私有独立运行 | 已核实边界 | 需要项目承担的工作 |
|---|---|---|---|
| 自己的执行 worker＋加固 Docker/runc | 可以，单个 Linux Docker 主机即可起步 | Linux namespaces/cgroups/capabilities/seccomp 等机制；与宿主共享内核，不是单独虚拟机内核 | 沙箱创建/回收、作业状态、取消/超时、资源上限、网络和挂载策略、输入输出/日志、身份与配额 |
| 相同 worker＋Docker 配置 gVisor/runsc | 可以，无须 Kubernetes 或云服务 | 应用的 Linux 系统调用由用户态应用内核处理，减少直接暴露的宿主内核接口；不是硬件虚拟机 | 上一项仍需要；另加 runsc 安装、升级、兼容性与性能验证 |
| 自部署 OpenSandbox，Docker 后端，可选 runsc | 可以；官方给出本地 server＋Docker 路径；Kubernetes 是另一部署方式 | 现成生命周期/命令/文件 API 和多语言 SDK；隔离强度仍取决于实际选用运行时和配置 | 平台业务身份/权限、脚本制品与包治理、网络/凭证授权、任务结果契约、并发与保留策略，以及沙箱服务运维 |

Docker 官方说明 daemon 由受信任管理员控制；允许调用方传入任意挂载、特权或运行时参数会破坏边界。[Docker Engine security](https://docs.docker.com/engine/security/) 默认容器没有 CPU/内存资源约束，须显式配置。[Resource constraints](https://docs.docker.com/engine/containers/resource_constraints/) 所以“加了 Docker”不能等同于完成上传脚本运行服务。

Docker CLI 提供 `--user`、`--read-only`、`--tmpfs`、`--cap-drop`、`--security-opt no-new-privileges`、`--memory`、`--cpus`、`--pids-limit`、`--network` 等配置；是否允许可写目录、网络和哪些资源值属于产品部署决策。[docker run reference](https://docs.docker.com/reference/cli/docker/container/run/) 这些配置不包含应用层的“谁有权运行哪个脚本/访问哪个数据源”。

## gVisor 的条件与取舍

官方当前安装要求 **Linux 5.6+，x86_64 或 ARM64**。[Installation](https://gvisor.dev/docs/user_guide/install/) Docker 上可注册 `runsc` 并用 `--runtime=runsc` 运行，独立 Docker 主机即可使用。[Docker quick start](https://gvisor.dev/docs/user_guide/quick_start/docker/)

gVisor 的应用内核提供额外隔离；它不是 VM hypervisor，也不会阻止脚本使用已经被授予的文件或凭证。控制平面自身漏洞、错误授权和硬件侧信道不是这层自动解决的。[Security architecture](https://gvisor.dev/docs/architecture_guide/intro/) 不能根据“平台只有一家企业”推断不必隔离：这是组织部署边界，脚本仍可能包含错误、破坏性逻辑和第三方依赖。

官方列出未覆盖的 Linux 功能、设备和 I/O 接口；必须用实际脚本与依赖验证。单个沙箱的总体资源可在宿主 cgroup 上限制，沙箱内部相互竞争进程的 cgroup 限制不是同一保证。[Application compatibility](https://gvisor.dev/docs/user_guide/compatibility/) 当前默认 systrap；KVM 平台需要 `/dev/kvm`，虚拟机内嵌套 KVM 不一定合适。[Platforms](https://gvisor.dev/docs/user_guide/platforms/) 本轮不提供未经测量的启动/性能数字。

## OpenSandbox：已读当前代码

当前官方仓库 [opensandbox-group/OpenSandbox](https://github.com/opensandbox-group/OpenSandbox)，GitHub 元数据许可证 Apache-2.0；本轮固定 commit `c2f55b20e945d89efe3d35220942a997a3551751`。原 Alibaba 路径在一些 SDK 引用中仍存在，当前主仓库应以 canonical 地址为准。

- [README 快速启动](https://github.com/opensandbox-group/OpenSandbox/blob/c2f55b20e945d89efe3d35220942a997a3551751/README.md)给出本地 Docker＋Python server、SDK 创建/执行/销毁流程；不要求购买某云上的沙箱控制平面。完全离线交付仍需要另行准备依赖、镜像及其更新来源，本轮未验证离线安装。
- 实际 [`DockerConfig`](https://github.com/opensandbox-group/OpenSandbox/blob/c2f55b20e945d89efe3d35220942a997a3551751/server/opensandbox_server/config.py)默认 `network_mode="host"`，`no_new_privileges=True`，丢弃一组 capabilities；不是默认禁止出网。`SecureRuntimeConfig`支持 `gvisor` 并指定 Docker `runsc` 或 K8s RuntimeClass，安全运行时并非默认开启。
- 实际 [`DockerContainerOpsMixin` 所在 container_ops.py](https://github.com/opensandbox-group/OpenSandbox/blob/c2f55b20e945d89efe3d35220942a997a3551751/server/opensandbox_server/services/docker/container_ops.py)把配置转换成 Docker `security_opt`、`cap_drop`、`pids_limit`、`mem_limit`、`nano_cpus` 和 `runtime`。这些不是只写在宣传文档中的配置。
- [配置参考](https://github.com/opensandbox-group/OpenSandbox/blob/c2f55b20e945d89efe3d35220942a997a3551751/server/configuration.md)说明网络策略 sidecar 在 Docker 下要求 bridge；DNS 模式与 DNS+nft 模式并不提供完全相同的 IP/CIDR 控制。API key 未配置时需显式不安全启动确认。不能把 server API key 当作本项目每个业务用户的权限系统。
- [secure-container guide](https://github.com/opensandbox-group/OpenSandbox/blob/c2f55b20e945d89efe3d35220942a997a3551751/docs/guides/secure-container.md)给出 gVisor Docker 组合。其概览中的笼统“硬件隔离”表述不应用于 gVisor；运行时隔离性质以 gVisor 自己的架构文档为准。
- 一种特定组合有明确限制：[egress-upstream-proxy guide](https://github.com/opensandbox-group/OpenSandbox/blob/c2f55b20e945d89efe3d35220942a997a3551751/docs/guides/egress-upstream-proxy.md)说明透明 MITM＋上游代理配置拒绝 gVisor；这不能扩大为“gVisor 不支持所有网络策略”。若企业网络必须经过该上游代理，应把这个组合纳入选型条件。

## Windows 开发与生产环境

Windows 上 Docker Desktop 的 WSL2 后端运行 Linux 内核与 Linux 容器，可以用于开发 Linux 脚本执行路径。[Docker Desktop WSL2](https://docs.docker.com/desktop/features/wsl/) Windows 开发机可通过 API 使用单独的 Linux 执行主机；开发机操作系统不必与生产执行主机相同。

runsc 的上述官方运行条件是 Linux；本轮未证明在用户当前 WSL2/Docker Desktop 内安装 runsc 的兼容性，不能承诺 Windows 终端装一个二进制就可使用。需要真实 Linux VM/主机的部署验证。

OpenSandbox 的“Windows Sandbox”是另一个含义：实际 [`windows_profile.py`](https://github.com/opensandbox-group/OpenSandbox/blob/c2f55b20e945d89efe3d35220942a997a3551751/server/opensandbox_server/services/docker/windows_profile.py)说明 dockur/windows 镜像本身基于 Linux，以 KVM/QEMU 运行 Windows guest，需要 `/dev/kvm`、`/dev/net/tun` 并增加网络 capabilities。它不是把上面的 Linux Python 容器方案自动变为原生 Windows 进程沙箱。若上传脚本必须调用 Windows 专用组件、Office COM 或 `.exe`，应单列 Windows 执行需求，不据此假设已解决。

## 沙箱之外仍要定义的契约

这些是实现时要有归属的能力，不是本报告替用户选择的政策：

1. **权限与身份**：脚本属于哪个 Agent 版本，谁能上传/发布/运行、哪个执行用户的数据可进入沙箱；不要让业务用户提交原始 Docker 挂载/privileged 参数。
2. **包与镜像**：允许的语言/版本、依赖如何固定/构建、运行时能否装包及访问哪些仓库。构建阶段同样会执行第三方安装代码，不能默认在平台 Web/API 主机完成。
3. **网络**：是否离线、可否访问企业内网/互联网、域名/IP策略及 DNS/代理。gVisor 不会自行替平台判断哪些业务系统允许访问。
4. **凭证**：哪个工具可取得哪个授权、凭证作用域与寿命、撤销、日志遮蔽；把平台全部环境变量复制到容器中会把相同权限给脚本。
5. **资源与生命周期**：每次运行沙箱还是可复用会话、CPU/内存/PID/磁盘与输出上限、墙钟截止时间、取消后的进程终止、产物导出、沙箱销毁和失败清理。作业 timeout、沙箱 TTL、应用请求 timeout 是不同边界。
6. **副作用与结果**：脚本返回 `exit=0` 不自动等于业务任务成功，重试也不保证外部写入不重复。验证/幂等应由具体工具契约决定。

## 适用前提与未决项

上述主比较适用于“生产执行端可以使用 Linux，脚本作为受限制的作业执行，平台控制镜像与容器启动参数”。这是一组比较前提，并非已记录的用户决定。若需要原生 Windows、任意自带镜像、特权设备/GPU、无限制内网或任意依赖，需扩大相应兼容性和权限调查。

尚无证据支持直接选定一种方案。最小下一步可在同一组真实允许的脚本和资源/网络规则下，对所选候选检查正确输出、超时取消、资源耗尽、文件越界、出网以及清理后残留；本轮未执行这些检查。
