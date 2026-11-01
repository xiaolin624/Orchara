# P03 T014 验收：停止超限脚本并证明资源已回收

- 任务：P03 / GitHub Issue #15（`T014`）
- 状态：**passed**（内存强制走 `ulimit`；OCI cgroup 内存上限未生效，已如实记录）
- 宿主：WSL2 Ubuntu 24.04，Linux `6.6.87.2-microsoft-standard-WSL2`，cgroup v2
- 运行时：gVisor `runsc release-20241118.0`
- 代码：`probes/p03_sandbox/limits_matrix.sh`、`sandbox_probe.py`
- 证据：[`limits-matrix.json`](limits-matrix.json)
- 测试：`pytest product/tests/p03 -q` → **24 passed**

## 1. 四种超限的实测结果

| 用例 | 配置 | 实测 |
|---|---|---|
| 超时 | 宿主 `timeout 4` + 沙箱内 `sleep 60` | exit **124**；清理前残留 1 个沙箱 → `runsc delete --force` 后 **0** |
| 磁盘 | `/tmp` tmpfs `size=16m`，尝试写 40 MiB | 只写入 **16,777,216 字节**（被 tmpfs 截断） |
| 内存（OCI） | 配置 `memory.limit = 64 MiB`，分配 80 MB | **存活**（`survived=80000000`）→ 该上限**未生效** |
| 内存（ulimit） | 启动前 `ulimit -v 65536` | `sh: out of memory`，exit 非 0 |
| fork | `pids.limit = 256`，循环后台 `sleep` | exit **2**，迅速终止，无残留 |

## 2. 一个必须写明的坏消息

**OCI 的 cgroup 内存上限在这台宿主上没有生效**。宿主 `cgroup2fs` 已挂载且
`memory` 控制器可用，但 `runsc run` 下 64 MiB 限额内仍能分配 80 MB。

处置：内存改由启动器用 `ulimit -v` 强制（有效，见上表），并在测试里**保留**一条断言
`test_the_oci_cgroup_limit_is_recorded_as_not_enforced`——如果哪天 cgroup 限额开始生效，
这条会失败并提醒更新报告，而不是让旧结论悄悄失真。

## 3. 资源回收

- **无孤儿沙箱**：超时用例清理前确实残留 1 个（宿主 kill 只杀客户端），显式
  `runsc delete --force` 后为 0；最终 `final_sandboxes = 0`。
- **无残留可读输入**：矩阵跑完后删除批准输入目录，`input_removed = true`。
- 每个用例都记录 `leftover_sandboxes_before_cleanup` / `after_cleanup`，
  测试逐条断言 after 为 0。

## 4. 失败后功能开关保持关闭

`arbitrary_scripts_enabled()` 默认 **False**；`record_failure()` 只写
`{"arbitrary_scripts": false, ...}`，不会因为一次失败反而打开开关。
`test_feature_switch_is_off_by_default_and_stays_off_after_failure` 断言这两点。

## 5. 覆盖的验收条件

- [x] 超时/fork bomb/内存/磁盘超限均可停止
- [x] 无孤儿沙箱或残留可读输入
- [x] 失败维持功能开关关闭

## 6. 边界

- 内存的**硬隔离**依赖 `ulimit -v`（启动器强制），不是内核 cgroup；多进程共享
  memory cgroup 的真实配额仍需在支持 cgroup 的 runc/runsc 配置下复验。
- fork 的收敛依赖 `pids.limit`；具体上限值（256）是当前实测可用值，不是产品定值。
- 本票只覆盖 P03 探针层；执行器的取消/超时语义属 F13。
