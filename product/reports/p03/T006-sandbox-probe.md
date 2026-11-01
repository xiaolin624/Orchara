# P03 T006 验收：启动一个拒绝危险配置的 Linux 沙箱探针

- 任务：P03 / GitHub Issue #7（`T006`）
- 状态：**passed**（此前为 `blocked`，原因已更正）
- 宿主：WSL2 Ubuntu 24.04，Linux `6.6.87.2-microsoft-standard-WSL2`（[ADR 0002](../../docs/adr/0002-wsl2-as-sandbox-acceptance-host.md)）
- 运行时：gVisor `runsc release-20241118.0`，spec `1.1.0-rc.1`
- 代码：`probes/p03_sandbox/sandbox_probe.py`
- 证据：[`sandbox-probe.json`](sandbox-probe.json)
- 测试：`pytest product/tests/p03 -q` → **15 passed**

## 1. blocker 更正

D00 曾把本票记为 blocked，理由是"无 Linux 宿主 + runsc + root 权限"。两处需要更正：

| 原判断 | 实测 |
|---|---|
| WSL 无 sudo，所以没有 root | **错**。`wsl -u root` 无需密码即可 root；此前只试了 `sudo` |
| 无 Linux 宿主 | 有 WSL2 Ubuntu 24.04；是否计入验收由维护者决定，现已确认为**算**（ADR 0002） |
| runsc 未安装 | 现已安装并做校验和验证：`runsc` 20241118.0（sha512 校验通过） |

## 2. 报告中的版本信息（criterion 1）

```json
{
  "guest_distro": "Ubuntu-24.04",
  "kernel": "6.6.87.2-microsoft-standard-WSL2",
  "runtime": "runsc version release-20241118.0",
  "spec": "1.1.0-rc.1",
  "platform": "default",
  "runsc_path": "/usr/local/bin/runsc",
  "policy_version": "orchara.sandbox-policy/v1"
}
```

`CapEff`/`CapPrm` 也一并记录，便于以后核对宿主能力。

## 3. 真实执行

```text
runsc --network=none do sh -c "echo sandbox-ok"
→ exit_code 0, stdout "sandbox-ok"
```

这是真的 gVisor 沙箱在跑命令，不是 mock。

## 4. 危险配置被拒（criterion 2）

runsc 本身**愿意**给宿主网络——实测 `runsc --network=host do ...` 能看到宿主网卡
（`lo`、`127.0.0.1`），所以拒绝必须发生在我们的策略层，且已在调用 runsc **之前**：

| 危险配置 | 拒绝理由 |
|---|---|
| `network=host` | `only ('none',) is permitted` |
| `network=loopback` | 同上 |
| `overlay=false` | 会让沙箱获得宿主文件系统写权限 |
| `privileged=true` | 不向用户脚本开放 |
| `uid=0` | 沙箱内不允许 root |
| `mount=/etc` | 超出批准根 `/work/` |

`test_rejected_request_never_reaches_the_guest` 用 spy 断言：被拒请求**根本不会**调用
guest，而不是"调用了但随后失败"。

## 5. 覆盖的验收条件

- [x] 报告包含 Linux/内核/运行时/策略版本
- [x] host 网络和 privileged 配置被拒
- [x] Windows 运行不计验收

关于第三条：本报告的宿主是 WSL2 Linux 内核，**不是** Windows 进程；宿主信息在报告里
明确写出，不冒充独立部署的 Linux 服务器。

## 6. 边界与后续

- 本票只证明"能起沙箱 + 拒绝危险配置"，**没有**证明文件/网络越权攻击被阻断（T010）、
  也没有资源回收（T014）。不要据此声称隔离整体安全。
- 宿主是 WSL2，生产交付仍建议独立 Linux 宿主；本决定只解决验收口径。
- runsc 安装在 guest 的 `/usr/local/bin`，版本与校验和已记录，可用同一 URL 复现。
