# 第二轮独立复核修复 — `ea56392..525a28f`（2026-11-06）

独立的第二轮 review 在批次收尾树上发现 4 项范围内问题（Standards 2 项、Spec 2 项）。本分支
`t/review2-fixes`（worktree 已合并回主工作树）逐条修复；每条先写复现测试（红）再修（绿）。
未改 `contracts/**`、`docs/spec/**`、迁移、凭据；`sync_spec --check` no drift。

## 1. 逐条映射

| # | 轴/级别 | 复核发现 | 修复 | 回归证据 |
|---|---|---|---|---|
| 1 | Standards / P2 | 恢复时可能继续使用已删除的知识正文：retrieve 成功后进入审批等待，删除来源文档，批准恢复后执行器仍复用保存的 excerpt，未复查 tombstone。违反 `ROUTING-MEMORY-KNOWLEDGE.md`「缓存命中仍检查资源 tombstone」。 | `reusableNodeOutputs` 不再复用 `retrieve` 节点的保存输出；恢复时重新执行 retrieve，经内部检索路由重新应用当前 ACL/tombstone/active index。日志说明原因。 | Go：`TestGraphRunResumeReExecutesRetrieveInsteadOfReusingTheSavedExcerpt`（旧代码 retrieve 调用 0 次 → 修复后 1 次且新结果进入输出）。真实栈：`test_a_resumed_run_rechecks_document_readability_instead_of_reusing_the_excerpt`（retrieve→approval，等待中 `revoke_document`，批准恢复后 `count=0`、最终输出不含删除文档 id）。 |
| 2 | Standards / P2 | 审批标题和有效期未从冻结配置派生：服务器采用 worker 提交的 title/expires，sealed definition 只解析 ID/type。持有效 token 的 worker 可改标题、延长窗口。违反 ADR 0034「服务端依据 sealed manifest 重建提案」。 | `nodeDefinitionNode` 增加 `Config`；新增 `approvalNodeSettings` 从 sealed definition 读取并校验 `title`（1..200）/`expires_seconds`（60..86400）；`PrepareNode` 用 sealed 值覆盖 worker 请求值（worker 请求只保留 run/attempt/node identity 与绑定 proposal）。sealed config 非法 → 503 `manifest_unavailable`，不回退 worker 值。 | Go：`TestApprovalNodeSettingsComeFromTheSealedDefinition`、`...RefuseAnInvalidSealedConfig`、`...RequireTheNodeInTheSealedDefinition`。真实栈：`test_the_server_takes_the_approval_title_and_window_from_the_sealed_definition`（伪造 title=Forged title/expires=86400 → 提案 title 与 3600s 窗口来自 sealed 定义，DB 行与 `approval.required` 事件同窗口）。 |
| 3 | Spec / P1 | 并行挂起掩盖兄弟分支失败并提交错误退出证据：审批分支先进入等待后 `drain()` 丢弃兄弟分支终止原因，随后用挂起前证据提交退出；兄弟沙箱 `sandbox.ErrOrphan`、清理未确认时 Run 仍提交 `waiting_approval` 且 `sandbox_started=false`。违反 `EXECUTION.md` 必需分支 fail-fast 与沙箱退出确认。 | `drain()` 记录确定性（最低 plan index）兄弟失败 `drainFailure`；`stopAndCommit` 在 waiting 时若存在兄弟失败则改为提交该失败（不提交等待提案、不确认假退出）。`commitWaiting`/`commitCancelled` 的退出证据改为聚合：任一分支 sandbox 启动过（`s.started`）时用最高 index 的 sandbox 记录与 `started=true` 确认退出。 | Go：`TestGraphRunWaitingHandoverDoesNotSwallowASiblingSandboxFailure`（旧代码 `waiting_approval` + exit `started=false` → 修复后 `failed/sandbox_orphan`、无 waiting 提案、exit `started=true`、唯一 `failed` 提交）。 |
| 4 | Spec / P2 | condition/join 输出绕过 Schema 校验：两类节点直接编码/上传/宣布成功，向 output_schema 加不会生成的必填字段后运行仍成功。违反 `COMPILER-CONTRACT.md`「平台 wrapper 负责输入/输出 Schema」。 | `publishNodeData`（condition/join/model/retrieve/approval 共用）在编码/登记前统一按 `node.OutputSchema` 校验，失败 → `node_output_invalid`（定位 `#`）、不发布 data artifact、不宣布成功。 | Go：`TestGraphRunConditionOutputIsValidatedAgainstItsSchema`、`TestGraphRunJoinOutputIsValidatedAgainstItsSchema`（旧代码 succeeded → 修复后 `node_output_invalid`、无 data 行）。 |

## 2. 门禁（真实命令与原始 tail）

工作树 E:\Orchara，分支 `t/review2-fixes`（基线 `525a28f`）。Go 工具链：
`GOCACHE=E:\orchara-toolchains\gocache`、`GOMODCACHE=E:\orchara-toolchains\gomodcache`，
`PATH` 含 `E:\orchara-toolchains\go\bin`。

```text
$ go test ./internal/approvals ./internal/presetexec -count=1
ok  	orchara/platform/internal/approvals	1.208s
ok  	orchara/platform/internal/presetexec	3.892s

$ python -m pytest "tests/f04b/test_t087_approval_node.py::test_the_server_takes_the_approval_title_and_window_from_the_sealed_definition" -q
1 passed in 18.43s

$ python -m pytest "tests/f04b/test_t088_retrieve.py::test_a_resumed_run_rechecks_document_readability_instead_of_reusing_the_excerpt" -q
1 passed in 25.46s
```

（完整 `tests/f04b`、`go test ./...`、`gofmt`、`sync_spec` 的最终 tail 见 §3。）

## 3. 最终门禁（本轮收尾树）

最终树（`t/review2-fixes`，基于 `525a28f`）实测，原始输出：

```text
$ go build ./...            # exit 0
$ go vet ./...              # exit 0
$ go test ./... -count=1    # exit 0（无 FAIL 行）
$ gofmt -l internal cmd tools   # 空
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest

$ python -m pytest tests/f04b -q
91 passed in 489.44s (0:08:09)
```

`tests/f04b` 的 91 个用例包含本轮新增的真实栈回归：
- `test_the_server_takes_the_approval_title_and_window_from_the_sealed_definition`
- `test_a_resumed_run_rechecks_document_readability_instead_of_reusing_the_excerpt`

Go 侧新增回归：
- `TestGraphRunResumeReExecutesRetrieveInsteadOfReusingTheSavedExcerpt`
- `TestGraphRunWaitingHandoverDoesNotSwallowASiblingSandboxFailure`
- `TestGraphRunConditionOutputIsValidatedAgainstItsSchema`
- `TestGraphRunJoinOutputIsValidatedAgainstItsSchema`
- approvals：`TestApprovalNodeSettingsComeFromTheSealedDefinition` 等 3 条。

## 4. 修复过程中暴露并修正的交互（诚实记录）

第一版 #3 修复把**所有** drain 到的兄弟失败都当作挂起阻断，全量 `tests/f04b` 随即在
`test_parallel_call_apps_create_two_independent_children_and_join_both` 暴露回归：等待交接
会取消兄弟 call_app 分支，被取消的 `SpawnChild` / `ChildResult` HTTP 调用以
`context canceled` 失败，这不是分支自身的失败。修正为 `handoverCancelFailure`：仅当
失败**没有**启动过 sandbox、**没有** typed sandbox 错误，且 code 属于
`child_spawn_failed` / `child_result_failed` / `agent_capability_failed` /
`node_capability_failed` 并携带 `context canceled`/`deadline exceeded` 时，才视为交接取消
引起、不阻断等待；reviewer 复现的 `sandbox.ErrOrphan`（sandbox started / typed error）仍然
使 Run 失败。该用例在 `tests/f04b/test_t089_callapp.py` 上单独复跑 2 次均 **7 passed**，
最终全量 91 passed。
