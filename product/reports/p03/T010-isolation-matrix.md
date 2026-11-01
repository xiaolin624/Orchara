# P03 T010 验收：证明脚本无法读越权文件或绕过网络代理

- 任务：P03 / GitHub Issue #11（`T010`）
- 状态：**passed**
- 宿主：WSL2 Ubuntu 24.04，Linux `6.6.87.2-microsoft-standard-WSL2`
- 运行时：gVisor `runsc release-20241118.0`
- 代码：`probes/p03_sandbox/isolation_matrix.sh`（隔离档 + 攻击矩阵）、`sandbox_probe.py`（策略层）
- 证据：[`isolation-matrix.json`](isolation-matrix.json)
- 测试：`pytest product/tests/p03 -q` → **17 passed**

## 1. 先说一个被修正的真实漏洞

侦察发现 `runsc do` 会**继承宿主环境变量**：

```text
ORCHARA_TEST_SECRET=leakme runsc --network=none do sh -c 'env | grep ORCHARA_TEST_SECRET'
→ ORCHARA_TEST_SECRET=leakme
```

已修：沙箱启动统一包在 `env -i` 里，只传 `PATH` 与以 `ORCHARA_APPROVED_` 开头的批准变量；
其余一律由策略层拒绝。修完实测：宿主密钥在沙箱内不可见，批准变量可见，未批准变量被拒。

## 2. 为什么不能继续用 `runsc do`

`runsc do` 是测试便利命令：宿主文件系统**只读挂载**且沙箱内是 **root**。实测它能读到
宿主 `/etc` 下的 root-only 文件——对用户脚本不构成隔离档。因此改为最小 OCI bundle：

| 项 | 取值 |
|---|---|
| rootfs | busybox，`readonly: true` |
| 用户 | `uid/gid 65534` |
| capabilities | bounding/effective/permitted 全空 |
| `noNewPrivileges` | true |
| 挂载 | 仅 `/proc`、`/tmp`（tmpfs 16m）、`/work/input`（**只读** bind） |
| 命名空间 | pid / network / ipc / uts / mount |
| 内存上限 | 64 MiB |

## 3. 攻击矩阵实测（全部按预期）

| 攻击 | 结果 |
|---|---|
| 读批准输入 `/work/input/input.csv` | `alice-data`（应可读） |
| 读其他租户 `/work/tenant-b/input.csv` | `No such file or directory` → **DENIED** |
| 读宿主 `/etc/hostname` | `No such file or directory` → **DENIED** |
| 写 `/work/input/blocked` | `Permission denied` → **DENIED** |
| 对照组：guest 侧 `nc -z` 到受控监听端口 | `succeeded! CONNECTED`（证明检测方法有效） |
| 直连公网 `1.1.1.1:443` | `EXIT=1`（连接失败） |
| 直连宿主受控端口 `127.0.0.1:18080` | `EXIT=1`（连接失败） |
| 设 `HTTP_PROXY` 后 `wget http://1.1.1.1/` | `Host is unreachable`，`EXIT=1` |

**复核修正**：早先这几行是 `nc … && echo CONNECTED || echo BLOCKED`，任何失败都产出同一个
字符串，无法区分"被阻断"和"检测工具本身不可用"。现在：

1. 增加**对照组**——同一个 `nc -z` 在 guest（沙箱外）对受控监听返回 `CONNECTED`，
   证明端口活着、检测方法有效；
2. 沙箱内不再输出布尔值，而是记录**真实退出码与错误文本**；
3. 代理那一行如实写明：busybox `wget` **不读取** `HTTP_PROXY`，所以无法演示"经代理绕出"。
   可证明的实质结论是**代理端点从沙箱不可达**（见直连宿主一行），拿不到的代理无法被绕过。
   这一点写在证据 JSON 的 `proxy_note` 字段里，而不是含糊成"BLOCKED"。
| 宿主凭据出现在沙箱 env | **不存在**（只有 `HOME/PATH/PWD/SHLVL`） |

`test_isolation_matrix_blocks_every_attack` 逐条断言上述结果；
`test_isolation_profile_script_declares_minimal_privileges` 守住配置本身
（uid 非 0、capabilities 为空、唯一 bind 挂载是批准输入、没有宿主根挂载）。

## 4. 覆盖的验收条件

- [x] 读取他人输入/环境凭据失败
- [x] 直连内网/公网与绕过代理失败
- [x] 每个阻断点有实际证据（见上表与 `isolation-matrix.json`）

## 5. 边界

- 网络全被阻断是**正确结果**，但这也意味着任何需要出网的工具都不能用；
  生产 profile 需要受控代理，属后续票，不在本票范围。
- 资源超限（超时/fork/内存/磁盘）的**终止与回收**是 T014，本票未验证。
- 隔离档是 WSL2 上的 gVisor；生产仍建议独立 Linux 宿主（ADR 0002）。
