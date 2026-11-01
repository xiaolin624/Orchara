# Issue tracker: GitHub

目标仓库：xiaolin624/Orchara
地址：https://github.com/xiaolin624/Orchara
任务与功能规格使用 GitHub Issues 跟踪。

## 操作约定

优先使用 GitHub MCP；缺少相应能力时使用已认证的 gh CLI。
所有操作显式指定目标仓库，不依赖当前目录推断。

- 发布任务：创建 GitHub Issue。
- 获取任务：读取 Issue 正文、标签与全部评论。
- 分类：使用 triage-labels.md 中的标签映射。
- 多行正文：使用结构化工具参数；gh 使用 --body-file。
- 修改标签：保留与本次操作无关的已有标签。

## 任务依赖与进度

已有任务拆票时保留 contracts/task-graph.json 的任务 ID、依赖和完成条件。
执行顺序以该任务图为准。

Issue 记录讨论、分派与验收证据。
实现及实验进度按 HANDOFF.md 和 PROGRESS.md 记录；
Issue 关闭须具备相应完成证据。

## Pull requests as a triage surface

PRs as a request surface: no.

## Wayfinding operations

地图使用一个标有 wayfinder:map 的 Issue。
子任务优先使用 GitHub sub-issues；不可用时使用父任务清单，
并在子任务正文写明 Part of #<父任务编号>。

依赖优先使用 GitHub 原生 issue dependencies；
不可用时在正文记录 Blocked by: #<编号>。
领取前确认依赖均已完成，再分配给执行者。
完成后留下答案与证据、关闭任务，并更新地图中的链接。
