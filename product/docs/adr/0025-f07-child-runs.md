# ADR 0025 — F07/T078: delegated child Runs, replay identity and the non-contract reads

- 状态：accepted（T078 #79 实现记录）
- 日期：2026-11-05
- 范围：F07 固定子应用运行；T080/T081 在此之上扩展预算/等待，不另建委派路径。

## 背景

`contracts/openapi.json` 只冻结了一个委派入口：`spawnChild`
（`POST /internal/v1/runs/{run_id}/children`，worker mTLS，body 仅
`attempt_token/logical_key/application_version_id/input`，响应 202 `Run`）。
`runs.parent_run_id/parent_invocation_key` 与 `UNIQUE(parent_run_id,
parent_invocation_key)` 已在 0014 存在；`execution_snapshots`、`run_artifacts`、
`jobs`、`artifacts` 也都存在。契约没有字段表达：

1. “同一父调用键、不同输入/版本”要报冲突——比较需要输入哈希与固定子版本，
   `runs` 只有子 Run 的输入 artifact id；
2. 哪些字段被显式映射（AC“未显式映射内容不出现在 child”）；
3. 子版本冻结 I/O schema 的身份（AC“child 结果符合固定 Schema”）；
4. 子 Run 的深度与 required 标记（T080/T081 的输入）。

同时，执行器需要两个契约没有的读：终态 child 的结构化结果（父应用取回结果），
以及父 Run 页面的 child Run 列表（AC“用户看到 child Run 时间线”）。把结果塞进
`Run`（`additionalProperties:false`）不可能；按应用 ID 再建一条运行入口则会绕开
`spawnChild` 的闭包/ACL 复查。

## 决策

### D1. 委派收据 `platform.run_children`（0045，非契约、加法）

一行一个 child Run，与 child Run 同事务写入：

- `child_run_id` PK / FK `runs`，`parent_run_id` FK，`parent_attempt_token` FK
  `run_attempts(token)`（审计事实，重放不改写）；
- `parent_invocation_key` + `UNIQUE(parent_run_id, parent_invocation_key)`（0014 的
  arbiter 保留；本表同一约束是 backstop）；
- `child_version_id` FK `application_versions`（父版本固定的子版本，而不是“最新”）；
- `input_sha256` + `input_artifact_id`（精确的规范化映射输入；重放比较这两项）；
- `io_mapping`（`{"node_id","fields"}`，只记字段名，不记值）；
- `input_schema_sha256`/`output_schema_sha256`（冻结 schema 身份）；
- `required`、`depth`、`created_at`。

不建 `state` 列：child 自己的 `runs.state` 是唯一权威，T081 只读本表，不再维护第二状态机。
不建 child 槽计数：独立子槽与每父并行上限属 T081（本票明确不实现）。

### D2. `spawnChild` 单事务（`internal/children.Spawn`）

父 Run 行 `FOR UPDATE` → 重证 live attempt token → `(parent_run_id,
parent_invocation_key)` 幂等预读（同 input sha + 同版本返回原 child，无写入；任一不同
→ 409 `child_invocation_conflict` + `details.field`）→ `catalog.ResolveRunnableVersionTx`
用**调用者当前 ACL** 解析子版本（403 `child_not_visible`、409 `child_version_revoked`/
`child_version_archived`）→ 父冻结 snapshot 的 `subapp_versions` 成员检查
（403 `child_not_in_dependency_closure`）→ 复用 T075 的 `CheckChildSpawn`
（mapping/声明次数/深度，422 `child_mapping_invalid`）→ 输入键集合必须**恰好等于**某个
`call_app` 声明的字段集（422 `child_input_mapping_mismatch`）→ 子冻结 input schema
（422 `child_input_invalid`）→ 运行期深度/次数（422 `child_depth_exceeded` /
429 `child_calls_exceeded`）→ 规范化映射输入封存（`run_input` artifact）→ 子版本自己的
published snapshot + child Run（owner=父 owner，`parent_run_id`/`parent_invocation_key`，
独立 checkpoint thread）+ queued 事件 + start job + input link + 收据。

幂等发生在 ACL/版本解析**之前**：子 Run 已存在时，即使之后撤销了授权或版本，重放仍返回
原 child；隐藏已存在的受控工作会让父任务永远悬挂。新 child 才重新验证当前权限。

### D3. 权限交集

有效权限 = 调用者当前读 ACL（`ResolveRunnableVersionTx`，每次 spawn 重算）∩ 父冻结闭包
（snapshot `subapp_versions`）∩ 子自身冻结 manifest（child Run 的 snapshot 是子版本
manifest，能力服务只按它授权）。child Run 的 `owner_id` 恒为父 Run 的 owner，绝不是子应用
作者；child 执行时读取的资源身份来自 child Run 的 owner，而不是应用作者。

### D4. 结果 schema 与“失败的是 child”

`execution.Service.ResultValidator` 是 finalizer 内的可选 seam（api.New 装配
`internal/children.ResultValidator`）。只有存在 `run_children` 收据的 Run 会被校验：读子版本
`manifest.definition.output_schema`，按共享 restricted-schema validator 校验
`output_artifact_id` 的真实封存字节（重新计算 sha256）。不合规 → finalizer 把该 Run 提交为
`failed` + `child_output_schema_invalid`，不写 output artifact，**不触碰父 Run**。父对
required child 失败的收敛（fail-fast/取消/预算）归 T080/T081。

### D5. 非契约内部读：child 结果

`POST /internal/v1/runs/{run_id}/children/{child_run_id}/result`，worker mTLS，body
`{attempt_token}`。服务端重证父 Run 的 live attempt、`run_children` 的父子链接、child 终态；
succeeded 时读取/复算 output artifact 字节并返回 `{child_run_id,state,output_artifact_id,
output}`；failed/cancelled 只返回状态与 error_code（父执行器按 required 分支处理）；非终态
409 `child_not_terminal`。它不暴露任何其他 Run 的 artifact，也不替代 spawn 的授权。

### D6. 非契约 owner 读：child Run 列表

`GET /api/v1/runs/{run_id}/children`（session/API key + `application:read`，与 `GET
/runs/{id}` 完全相同的 owner-only 404 规则），返回 `{items: Run[], next_cursor:null,
generated_at}`。页面用它渲染独立 child 时间线；`next_cursor` 恒 null 因为 child 数量受父
冻结策略上限约束。结构化结果由页面按 child 的 `output_artifact_id` 读既有
`GET /api/v1/artifacts/{id}/content`。

### D7. 执行器：`call_app` 与 `agent.subapp_versions`

`internal/presetexec` 的线性计划扩展为
`start -> [python] -> [agent] -> [call_app] -> end`（另有 `start -> end` 身份链，供只有
输入校验/回显的子应用在无沙箱下真实执行）。`call_app`：

- logical key `call_app:<node_id>`（同一 attempt 重试重放同一 child）；
- 非终态 child → `waiting_children` 提案，checkpoint `child:<child_run_id>`，确认退出；
- 终态 succeeded → 读结果、按节点 `output_schema` 再校验、登记 node data artifact 并继续；
- 终态 failed/cancelled → required 分支失败，错误码取 child 的稳定平台码或
  `child_required_failed`；
- spawn 拒绝的平台码（`child_calls_exceeded` 等）成为父 Run 的错误码。

`agent.subapp_versions`：`validateAgentNode` 只接受 sealed manifest `subapp_versions` 中的
版本，按声明顺序派生工具名 `call_app_1..N`；模型调用参数里的 `tools` 携带该名字与固定
版本，能力服务仅因为版本在 run 的冻结闭包内才接受（见 D8）；执行器把已校验的 tool call
映射为 `spawnChild`，logical key `agent:<node>:subapp:<call_call_id>`。模型不能命名任意
应用，也不能直接建 Run。

### D8. 能力服务接受 sealed 子应用作为模型工具声明

`capabilities.loadManifestTools` 增加 run.Manifest.SubappVersions 的只读解析：读取子版本
冻结 `definition.input_schema`，revoked/archived/缺 definition 的不提供。声明工具的参数
校验因此用子应用的冻结 input schema。它不是 capability operation：`resolve` 的 tool 授权
仍只认 `manifest.tool_artifacts`，所以用子版本 id 调 `invokeCapability(kind=tool)` 仍 403；
真正的调度仍然只发生在 `spawnChild`。

### D9. Python `PublishedAppDelegate`

`worker/platform_worker/agents/delegate.py`：`DelegateTransport` 只有
`spawn_child`/`child_result` 两个方法；`HTTPDelegateTransport` 用真实 mTLS（worker 证书 +
`X-Internal-Role: worker`）；`PublishedAppDelegate` 实现既有 `ToolExecutor`，logical key
由 `(run_id, invocation_path, tool_name)` 决定，live child 抛 `ToolExecutionError(waiting=True)`，
终态结果按冻结 output schema 本地再校验。模块无 DB/DSN 依赖，也没有其他建 Run 路径；
`tests/f07/test_t078_child_runs.py` 同时用真实 API 驱动它并做静态边界守卫。

### D10. 依赖/等待边界

父等待 child 的恢复：T078 的测试使用既有 T035 bridge 提供 children 决议；生产路径的
child 终态自动 hook（以及独立子槽、每父并行上限、预算）归 T081/T080。T078 只保证
`waiting_children` 条件、`child:<id>` checkpoint、单一 resume job 由既有 T035 机制产生。

## 影响

- 迁移：`0049_f07_child_runs.sql`（分支内初为 0045；合并集成分支时 0045–0047 已被其它票占用，按合并树顺延为 0048；orchestrator 目标号 0052，最终合并可再重编号）。
- 非契约 REST：3 条（spawnChild 是契约；结果读 + owner child 列表是非契约），全部有报告/ADR。
- 不改 `docs/spec/**`、`contracts/**`；`sync_spec --check` 保持 no drift。
- 后续：T080 只扩展预算/次数原子性；T081 只扩展 child 终态 hook、独立子槽、父等待收敛；
  两者共用 `run_children`、`Spawn` 与 `ChildResult` seam。

## 备选与取舍

- **把结果放进 `Run`**：契约 `additionalProperties:false`，拒绝。
- **child 结果只由 worker 读 object store**：会让 worker 拿到跨 Run 的字节权限，拒绝。
- **每次并发按应用 ID 去重**：违反 EXECUTION.md §7「不能按应用 ID 去重」，拒绝。
- **在 finalizer 内直接读取 catalog/object store**：catalog 已 import execution，会成环；
  用 seam + api.New 装配，保持依赖方向。
- **把 child 收据状态复制到 `run_children.state`**：会产生第二状态机，T081 需持续对齐，拒绝。
