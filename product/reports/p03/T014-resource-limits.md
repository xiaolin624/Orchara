# P03 T014 验收：停止超限脚本并证明资源已回收

- 任务：P03 / GitHub Issue #15（`T014`）
- 状态：**passed**（内存强制走 `ulimit`；OCI cgroup 内存上限未生效，已如实记录）
- 宿主：WSL2 Ubuntu 24.04，Linux `6.6.87.2-microsoft-standard-WSL2`，cgroup v2
- 运行时：gVisor `runsc release-20241118.0`
- 代码：`probes/p03_sandbox/limits_matrix.sh`、`sandbox_probe.py`
- 证据：[`limits-matrix.json`](limits-matrix.json)
- 测试：`pytest product/tests/p03 -q` → **24 passed**

## 1. 六类超限的实测结果（复核后重跑）

所有上限都由**运行时从 OCI spec** 施加，或由**启动器**施加，没有一条写在被测脚本里。

| 用例 | 施加方式 | 实测 |
|---|---|---|
| 超时 | 宿主 `timeout 4` | exit **124**；清理前残留 1 个沙箱 → `runsc delete --force` 后 **0** |
| 磁盘 | tmpfs `size=16m`（spec） | 只写入 **16,777,216 字节** |
| 内存 | `process.rlimits: RLIMIT_AS=64MiB`（spec） | `sh: out of memory`，exit **1** |
| CPU | `process.rlimits: RLIMIT_CPU=2s`（spec） | exit **137**（SIGKILL），2 秒终止 |
| fork | `pids.limit=256`（spec） | exit **2**，迅速终止，无残留 |
| 输出 | 启动器按 64 KiB 截断管道 | 捕获 **恰好 65,536 字节** |

## 2. 复核修正的两个错误

早先版本有两处不成立，已修：

1. **内存不是启动器施加的**：`ulimit -v` 曾写在**被测命令里**，等于夹具自己限制自己。
   现在改用 OCI `process.rlimits`，由 runsc 在启动时施加（`RLIMIT_AS`），实测生效。
   顺带解决了原先"cgroup 内存上限不生效"的问题——rlimit 路径在这台宿主上可用。
2. **CPU 与输出用例原先缺失**，而票面切片要求"限制 CPU/内存/PID/磁盘/时间/输出"。
   现在六类齐全，`test_every_limit_kind_the_ticket_names_is_covered` 断言覆盖集合。

## 3. 资源回收

- **无孤儿沙箱**：超时用例清理前确实残留 1 个（宿主 kill 只杀客户端），显式
  `runsc delete --force` 后为 0；最终 `final_sandboxes = 0`。
- **无残留可读输入**：矩阵跑完后删除批准输入目录，`input_removed = true`。
- 每个用例都记录 `leftover_sandboxes_before_cleanup` / `after_cleanup`，
  测试逐条断言 after 为 0。

## 4. 失败后功能开关保持关闭

开关只有一个来源：环境变量 `ORCHARA_ALLOW_ARBITRARY_SCRIPTS`（与 `internal/config`
校验的是同一个变量）。`arbitrary_scripts_enabled()` 默认 False，
`record_failure()` **不写任何文件**、只回报当前状态——失败不可能反而打开开关，
运行时的状态也不会再被提交进 `reports/`。早先那个 `reports/p03/feature-flag.json`
（内容是测试自己写的字面量）已删除。

## 5. 覆盖的验收条件

- [x] 超时/fork bomb/内存/磁盘超限均可停止（另补 CPU 与输出）
- [x] 无孤儿沙箱或残留可读输入
- [x] 失败维持功能开关关闭

## 6. 边界

- 内存的**硬隔离**依赖 `ulimit -v`（启动器强制），不是内核 cgroup；多进程共享
  memory cgroup 的真实配额仍需在支持 cgroup 的 runc/runsc 配置下复验。
- fork 的收敛依赖 `pids.limit`；具体上限值（256）是当前实测可用值，不是产品定值。
- 本票只覆盖 P03 探针层；执行器的取消/超时语义属 F13。
