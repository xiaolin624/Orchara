# C1 拆票发布记录

来源：[父任务 #137](https://github.com/xiaolin624/Orchara/issues/137) 与[完整规格](https://github.com/xiaolin624/Orchara/issues/137#issuecomment-6719314337)。用户已确认四票颗粒度、阻塞关系及文本关联方式。

| 票 | 交付行为 | Blocked by |
| --- | --- | --- |
| [#138 / C1.01](https://github.com/xiaolin624/Orchara/issues/138) | 现有命令检查当前规格、两份复制件与基线，识别漂移和无法核实来源 | 无 |
| [#139 / C1.02](https://github.com/xiaolin624/Orchara/issues/139) | 完成有效更新与删除，失败时保护或恢复完整旧复制件 | #138 |
| [#140 / C1.03](https://github.com/xiaolin624/Orchara/issues/140) | 真实进程中断后检测未完成状态，显式再次同步可恢复，恢复中断可重试 | #139 |
| [#141 / C1.04](https://github.com/xiaolin624/Orchara/issues/141) | 多进程互斥、锁释放后正常操作，以及各平台命令行为与证据一致 | #139 |

四票均已发布为 open、ready-for-agent，回读核对正文、标签和阻塞引用一致。当前无阻塞的是 #138；#139 完成后，#140 与 #141 可并行。没有增加传递依赖，也没有单独拆水平的工具抽取或测试层票。

当前连接器未提供原生子 Issue 和阻塞关系操作，gh 不在 PATH，因此使用各票正文中的 Part of #137 与 Blocked by 引用；这些是文本关系，不声称为 GitHub 原生关系。父任务正文、全部评论、关闭状态和标签在发布前后保持相同。

本地实现与历史 64 项工具测试证据仍然有效；拆票用于独立复核、补齐和集成，不要求重新制造已有能力。新票不因旧父票已关闭而自动获得完成状态。本轮未领取、执行或关闭这些新票，未修改平台任务图或代码。
