# 开发规格包

接手开发先读 [HANDOFF.md](HANDOFF.md)、[PROGRESS.md](PROGRESS.md)、[IMPLEMENTATION-ORDER.md](IMPLEMENTATION-ORDER.md)。本目录当前是规格与文档工具，平台尚未实现；已有源码/测试是否存在以当前产品仓库为准。

按任务图推进，变更生成契约时同时改生成源并校验。业务模块开发按HANDOFF的触发条件加载相应详细规格，不必每轮重读全部research或访谈。

Q1–Q46已确认；保留候选门禁，不重开访谈，不把组件策略确认写成运行通过。实现与实际实验分别留下进度证据。

## Agent skills

### Issue tracker

任务使用 xiaolin624/Orchara 的 GitHub Issues 跟踪。
操作前读取 `docs/agents/issue-tracker.md`。

### Triage labels

采用五种默认 triage 标签。
分类前读取 `docs/agents/triage-labels.md`。

### Domain docs

采用 single-context：根级 `GLOSSARY.md` 与 `docs/adr/`。
探索领域或设计实现前读取 `docs/agents/domain.md`。
