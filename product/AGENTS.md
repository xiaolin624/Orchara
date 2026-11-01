# 产品工作区指南（供代理与开发者）

## 权威顺序

1. 本文件与 [`../AGENTS.md`](../AGENTS.md)（仓库级指令）；
2. 规格：[`docs/spec/HANDOFF.md`](docs/spec/HANDOFF.md) → [`docs/spec/IMPLEMENTATION-ORDER.md`](docs/spec/IMPLEMENTATION-ORDER.md) → [`contracts/task-graph.json`](contracts/task-graph.json)；
3. 进度：[`PROGRESS.md`](PROGRESS.md)（实现进度权威，规格里的 `docs/spec/PROGRESS.md` 只是接手快照）；
4. 决策：[`docs/adr/`](docs/adr/)；术语：[`docs/spec/GLOSSARY.md`](docs/spec/GLOSSARY.md)。

## 硬规则

- **不许把静态校验、stub、未运行写成通过**。缺条件写 `blocked`，未跑写 `not_run`。
- 规格目录是只读来源。修改实现时不要顺手改 `docs/spec/`；如确需改规格，同时在仓库根生成源
  （`tools/build_contracts.py` 等）修改并重建，再 `python tools/sync_spec.py` 重新同步。
- 契约漂移必须可检测：`python tools/sync_spec.py --check` 非零即失败。
- 凭据不进仓库、日志、报告或测试夹具。
- 按任务图推进；每票完成时更新 `PROGRESS.md`，附代码位置、实际命令、原始结果与剩余阻塞。

## 按需阅读规格

| 正在开发 | 必读 |
|---|---|
| 数据/事务/身份 | `docs/spec/DATA-MODEL.md`、`SECURITY.md`、`contracts/database.sql` |
| API/前端 | `docs/spec/API.md`、`UX.md`、`contracts/openapi.json` |
| 工作流/节点 | `docs/spec/WORKFLOW-SPEC.md`、`COMPILER-CONTRACT.md`、`contracts/workflow.schema.json` |
| worker/取消/恢复/审批 | `docs/spec/EXECUTION.md`、`STATE-MACHINES.md` |
| 摄取/评测/删除后台任务 | `docs/spec/BACKGROUND-JOBS.md` |

## 测试

```powershell
python -m pytest tests/tooling -q          # 工具/契约同步
python -m pytest tests -q                  # 全部产品测试
```
