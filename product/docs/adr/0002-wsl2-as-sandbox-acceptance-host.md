# ADR 0002: WSL2 作为沙箱验收宿主

- 状态：已接受（维护者确认，2026-11-01）
- 相关：P03 / T006，D00 blocker `linux_isolation`

## 背景

规格要求沙箱隔离在**真实 Linux** 上验收，并明确"报告不使用 Windows 结果冒充 runsc 验收"。
D00 阶段把 `linux_isolation` 记为 blocked，理由是"无 Linux 宿主 + runsc + root"。

该理由有两处问题，后来都纠正了：

1. **root 其实可用**：`wsl -u root` 不需要密码即可拿到 root（此前只测了 `sudo`，默认用户要密码 → 误判为无 root）。
2. **宿主归属未决**：机器上只有 WSL2 的 Ubuntu 24.04，是否算"真实 Linux 宿主"属于验收口径问题，应由维护者决定。

## 决定

**WSL2 Ubuntu 24.04（内核 6.6.87.2-microsoft-standard-WSL2）作为 P03 的验收宿主。**

## 后果

- 沙箱探针在此宿主上运行 gVisor `runsc`（release-20241118.0，spec 1.1.0-rc.1）。
- 报告必须写明"宿主是 WSL2"，不能写成"独立部署的 Linux 服务器"。
- 能力证据必须来自实跑：内核版本、runsc 版本、实际执行结果、拒绝列表。
- 仍不改变的安全底线：未跑出负例就不称"隔离通过"；生产交付仍应使用独立 Linux 宿主，
  本决定只解决**验收**口径。

## 证据

`product/reports/p03/T006-sandbox-probe.md` 与 `product/reports/p03/sandbox-probe.json`。
