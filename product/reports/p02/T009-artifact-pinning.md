# P02 T009 验收：升级后恢复旧图或明确报告缺制品

- 任务：P02 / `T009`
- 状态：**passed**
- 代码：`probes/p02_checkpoint/checkpoint_probe.py`（publish / resolve / delete 与 resume 的钉版逻辑）、
  `db/migrations/0002_artifacts.sql`
- 测试：`pytest product/tests/p02 -q` → **11 passed**（新增 3 条制品测试）

## 1. 发布新图后仍加载旧图

运行开始时把**当时最新**的制品钉进状态（`artifact_id` / `graph_version` 是显式声明的状态通道），
恢复时只解析这个制品：

```text
publish --version 1                 → artifact-v1, digest d156ff52…
run --thread A1 --owner alice       → prepared，停在 approve 前
publish --version 2                 → artifact-v2, digest b267b467…
resume --thread A1 --decision approve
  → loaded_version: 1, pinned_artifact: artifact-v1, artifact_digest: d156ff52…
```

`loaded_version` 与 `artifact_digest` 都来自钉住的制品，不是最新版本——测试
`test_a_new_publish_does_not_change_what_a_run_resumes_with` 断言这三点。

## 2. 缺失制品明确失败

```text
publish --version 3 → run --thread A2 → delete-artifact --version 3
resume --thread A2 --decision approve
  → {"error": "artifact_unavailable", "artifact_id": "artifact-v3"}   exit 2
```

删除是**软删除**（`deleted_at`），所以钉住的 run 得到的是 `artifact_unavailable`，
而不是被静默升级到新版本。`latest-artifact` 也会跳过已删除的版本
（`test_a_deleted_artifact_is_not_offered_as_the_latest`）。

## 3. 真实进程/数据库证据

每次 `run` / `resume` / `publish` / `delete-artifact` 都是**独立进程**；状态落在真实
PostgreSQL 的 `app.artifacts`、`app.threads`、`app.operations` 与 checkpointer 表中。
`runtime_role` 显示执行者是受限角色 `orchara_worker`。

## 4. 覆盖的验收条件

- [x] 发布新图后仍加载旧图
- [x] 删旧制品返回 `artifact_unavailable`
- [x] 保存真实进程/数据库证据

## 5. 边界

- 制品内容是三节点图的**清单**（`fixture manifest`），不是编译后的 LangGraph 产物；
  本票验证的是"钉版与缺件语义"，不是编译器（属 F04）。
- 没有实现制品存储的物理删除与垃圾回收，只做了软删除。
- 图结构的真实版本迁移（如节点参数变更）不在本票范围。
