# C1：规格同步与漂移检查验收

日期：2026-11-08。任务：[GitHub #137](https://github.com/xiaolin624/Orchara/issues/137)。代码起点：`6141328beaf12568a594095aef9dbc4a1ff2dc21`；本次实现与文档位于当前工作区。

## 结果

本地工具验收通过。保留 `check()` / `sync()` 及原 CLI，当前来源、两份复制件与同步基线统一核验；暂存、切换、回滚、进程锁和中断恢复由同一 module 承担。

需求：[Q1–Q6 决策及 R1–R6 验收矩阵](../../docs/reviews/2026-11-08-architecture-grilling.md)。设计：[三案比较与最终选型](../../docs/reviews/2026-11-08-spec-sync-design.md)。实现：[sync_spec.py](../../tools/sync_spec.py)。行为测试：[test_sync_spec.py](../../tests/tooling/test_sync_spec.py)。

## 实际验证

主验证环境为 Windows 11、Python 3.12.2、本地文件系统；未调用真实模型、数据库、沙箱或远程存储。原始命令与输出见 [C1-spec-sync-validation.txt](C1-spec-sync-validation.txt)。

| 命令 | 观察 |
| --- | --- |
| `python -X utf8 -m pytest product/tests/tooling -q -p no:cacheprovider` | **64 passed in 37.06s**，exit 0 |
| `python -X utf8 product/tools/sync_spec.py` | copied 105 files，105 份规格文件摘要、10 份契约摘要，exit 0 |
| `python -X utf8 product/tools/sync_spec.py --check` | 当前来源、两份副本和基线一致，exit 0 |
| 独立代理复核修复后的三个问题并运行相关回归 | 10 passed，42 deselected；未操作实际仓库同步 |
| `wsl.exe -e python3 -`，标准库临时目录冒烟 | Python 3.12.3；来源变更、同步、进程退出恢复、fcntl 进程互斥均通过；原始输出见 [C1-posix-smoke.txt](C1-posix-smoke.txt) |

专项测试共 52 项，包含原有复制/基线检查，及来源文件新增、修改和删除、源不可用、基线损坏、普通文件删除传播、关键来源目录缺失、布局越界、Windows junction、并发进程互斥、复制期间来源变化等行为。

故障验收通过实际临时目录与子进程完成：

- 四个目录切换点的可捕获异常，恢复旧复制件与基线的原始字节。
- 四个切换点的真实 `os._exit(73)`，新进程检查报告 `incomplete_sync`，显式再次同步可恢复。
- 回滚过程中再次退出、首次同步没有旧 generation 时退出、恢复失败后重试。
- discard 清理部分完成后出错，完整旧复制件不受损，后续重试可完成。
- 提交后的清理失败只报告 warning，已核验的新 generation 保持有效。
- 来源无效时，不开始修改目标或恢复旧事务。

## 审阅中发现并修正的问题

1. 运行契约中的 `SYNC-MANIFEST.json` 被通用扫描规则跳过：现在只有规格复制件自身的基线文件被排除；运行契约同名文件纳入检查。
2. 目标位于来源 `contracts/` 内时可能把必需来源剪枝：现在在任何写入前拒绝该布局。
3. 回滚先递归删除活动目标，部分失败后无法继续：现在先把目标移动到 discard，再恢复备份，递归清理不阻塞完整目标恢复。

三个问题均先由回归复现，再修复；没有仅凭代理审阅结论判定通过。

## 实际仓库同步的范围

新检查首次在当前仓库发现 `source/.gitignore` 与历史基线不一致。根 `.gitignore` 是既有内容，未被本次修改；同步只把它更新到 `product/docs/spec/.gitignore` 并更新 manifest 的来源、generation、计数与摘要。

没有修改根生成源、业务规格或生成契约；`product/contracts/` 内容没有差异。`product/docs/spec/SYNC-MANIFEST.json` 属于同步工具生成的基线，不是手工修改契约。

复制件沿用来源的换行字节，根 `.gitignore` 含既有 CRLF 行。`git -c core.whitespace=cr-at-eol diff --check` 通过；默认 whitespace 检查会把这些已复制的 CR 视为行尾空白，不表示新增文本空格。

## 边界

本轮不提升平台功能、真实模型、Linux 沙箱或容量实验状态。全量产品 pytest 未运行，本次改动局限于规格工具与其行为测试。

Windows 完整 tooling 已通过；WSL 缺少 pytest，未安装额外依赖，只执行了标准库冒烟，不将它写成 Linux 完整测试套件通过。

已验证的是进程退出与可注入 I/O 故障，不是物理断电或设备损坏。两个目标目录不具备一次文件系统原子替换；未完成记录负责避免检查把混合状态判为成功。同机恶意进程实时替换路径、远程文件系统和真实断电恢复不在本轮证据内。

任务 #137 已附上述本地证据并关闭。代码、文档和报告仍在工作区，未提交或推送；此完成记录不表示远端代码已集成。
