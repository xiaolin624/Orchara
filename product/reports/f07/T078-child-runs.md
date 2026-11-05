# F07 T078 验收：主应用委派后查看独立子运行并取回结果（#79）

- 任务：F07 / GitHub Issue #79（票 `T078`），分支 `t/t078-child-runs`（worktree
  `E:\orchara-worktrees\t078`，基线 `integration/t068-t082` @ `0fcf8ec`，含 T068 发布、T069 删除、
  T070 撤销、T074 会话/请求、T075 固定子应用校验）。
- 状态：**passed**。契约 `spawnChild` 完整实现（幂等重放/冲突、固定子版本闭包、当前 ACL 交集、
  规范化映射输入封存、深度/次数、子结果 schema 校验），`internal/presetexec` 真实执行
  `call_app` 与 `agent.subapp_versions`，Python `PublishedAppDelegate` 走真实 mTLS，Run 页面显示
  独立 child 时间线与结构化结果；T075 的发布事务 partial 已闭环。
- 边界（如实）：并行子调用的**共享父预算/次数原子**属 T080；child 终态的**自动恢复 hook**、
  独立子槽与每父并行上限属 T081（本票测试用既有 T035 bridge 提供 children 决议，报告 §5 明示）；
  真实 WSL2/runsc 五类评测仍未跑（依赖 T081 的自动恢复；本票用 T068 已验证的五类报告 fixture
  完成真实发布事务）；浏览器 DOM not_run（Node 执行页面纯逻辑）。
- 迁移：`0048_f07_child_runs.sql`（分支内初为 0045；合并 `integration/t068-t082` 时 0045–0047 已被 T077/T071/T072 占用，按合并树顺延为 0048；orchestrator 目标号 0052，最终合并可再重编号）。
  ADR：`docs/adr/0025-f07-child-runs.md`。未改 `docs/spec/**`/`contracts/**`，`sync_spec --check` no drift。

## 0. 工作记录（简短）

1. 读 BRIEF、票 #79、`notes/f07-delegation.md` §3–§10、T075 报告/ADR 0023、`spawnChild` 契约、
   `internal/{catalog,execution,presetexec,capabilities,requests}`、`cmd/supervisor`、
   `worker/platform_worker/agents`、`apps/web/src/{runs.html,assets/runs.js}` 与既有测试支撑。
2. 以真实栈确认基线缺口：无 `/children` 路由；`presetexec` 拒绝 `call_app`/`agent.subapp_versions`；
   `run_children` 表不存在；result 校验/child 结果读/页面 child 列表都不存在。
3. 切片与 TDD：先落 Go 纯规则（catalog spawn 复查、schema 身份）与 children 服务及其单元测试；
   随后真实栈用例驱动修正（`start -> end` 身份链、finalize worker、agent 无 python 链、
   page Node fixture）。真栈用例使用真实 PG 16.4、编译后的 `cmd/api`/`cmd/supervisor`/`cmd/maintenance`、
   真实 mTLS、真实封存对象字节与真实 result/exit 协议。
4. 门禁、报告、PROGRESS、ADR，合并 `integration/t068-t082` 后复跑（§3）。

## 1. 交付

### 1.1 迁移 `0048_f07_child_runs.sql`（非契约、加法）

`platform.run_children`：`child_run_id` PK/FK、`parent_run_id` FK、`parent_attempt_token` FK、
`parent_invocation_key` + `UNIQUE(parent_run_id, parent_invocation_key)`、`child_version_id` FK、
`input_sha256` + `input_artifact_id`、`io_mapping`（`{"node_id","fields"}`）、
`input_schema_sha256`/`output_schema_sha256`、`required`、`depth`。详细决策见 ADR 0025 D1：
不复制 child 状态机（child 的 `runs.state` 是权威）、不建 child 槽（T081）、不重新加 0014 已有的
`runs.parent_run_id/parent_invocation_key` 约束。`orchara_worker`/`PUBLIC` 零权限；重复 migrate no-op。

### 1.2 `spawnChild` 单事务（`internal/children`）

`POST /internal/v1/runs/{run_id}/children`（worker mTLS，body 恰为契约四字段，202 `Run`）：
父 Run `FOR UPDATE` → live attempt token（`execution.RequireLiveAttemptTx`，导出 seam）→
`(parent_run_id,parent_invocation_key)` 幂等预读（同版本 + 同规范化 input sha → 原 child，无写入；
任一不同 → 409 `child_invocation_conflict` + `details.field`）→ 用**调用者当前 ACL** 解析子版本
（403 `child_not_visible`、409 `child_version_revoked`/`child_version_archived`）→ 父冻结 snapshot
`subapp_versions` 成员（403 `child_not_in_dependency_closure`）→ 复用 T075 `CheckChildSpawn`
（mapping/声明次数/深度；422 `child_mapping_invalid`）→ 输入键恰好等于声明的 call_app mapping
（422 `child_input_mapping_mismatch`；agent 允许子集）→ 子冻结 input schema
（422 `child_input_invalid`）→ 运行期深度/次数（422 `child_depth_exceeded` / 429
`child_calls_exceeded`）→ JCS 规范化映射输入封存为 `run_input` artifact → 子版本自己的 published
snapshot + child Run（owner=父 owner，独立 checkpoint thread）+ queued 事件 + start job + input
link + 收据。重放发生在 ACL/版本解析之前：已存在的受控工作不会因事后撤权而消失。

### 1.3 子结果 schema（finalizer seam）

`execution.Service.ResultValidator` 在 finalizer 内、output artifact 检查之后执行（api.New 装配
`children.ResultValidator`）。仅对存在 `run_children` 收据的 Run：读子版本
`manifest.definition.output_schema`，对 output artifact 真实字节复算 sha256 后用共享
restricted-schema validator 校验；不合规 → 该 child Run 提交 `failed` +
`child_output_schema_invalid`，不写 output artifact，父 Run 不受影响（规则引用：EXECUTION.md §7
子结果固定 schema；required-child 的父侧收敛属 T080/T081）。

### 1.4 执行器：`call_app`、`agent.subapp_versions`、`start -> end`

- 线性计划扩展为 `start -> [python] -> [agent] -> [call_app] -> end` 与 `start -> end`
  身份链（无沙箱，供只有输入校验/回显的子应用真实执行）。`call_app` 校验固定 UUID 与 object
  output schema；`agent` 不再强制前面有 python。
- `call_app`：logical key `call_app:<node>`；非终态 child → `waiting_children` +
  checkpoint `child:<child_run_id>` + 确认退出；终态 succeeded → 非契约结果读、按节点
  output_schema 再校验、登记 node data artifact 并继续；failed/cancelled → required 分支失败，
  错误码取 child 稳定平台码或 `child_required_failed`。spawn 拒绝码（如 `child_calls_exceeded`）
  成为父 Run 的错误码。
- `agent.subapp_versions`：只接受 sealed manifest 闭包中的版本，派生工具名 `call_app_1..N`；
  模型 tool call 的参数由能力服务按子版本冻结 input schema 校验，执行器把它映射为
  `spawnChild`（logical key `agent:<node>:subapp:<call_id>`）。没有第二条建 Run 路径。
- 能力服务 `loadManifestTools` 增加 sealed `subapp_versions` 的只读工具声明（revoked/archived/
  缺 definition 不提供）；`invokeCapability(kind=tool)` 仍只认 `manifest.tool_artifacts`，
  子版本的真正调度只在 `spawnChild`。

### 1.5 三个读/路由

| 路由 | 容量 | 说明 |
|---|---|---|
| `POST /internal/v1/runs/{run_id}/children` | worker mTLS | 契约 `spawnChild`，202 `Run`（创建或重放不可区分，id 相等即重放） |
| `POST /internal/v1/runs/{run_id}/children/{child_run_id}/result` | worker mTLS | 非契约：父 live attempt + 父子链接 + child 终态；succeeded 返回校验过的结构化对象，failed/cancelled 返回状态/码，非终态 409 |
| `GET /api/v1/runs/{run_id}/children` | session/API key `application:read` | 非契约 owner-only（与 `GET /runs/{id}` 相同 404 规则），返回 `{items: Run[], next_cursor:null, generated_at}` |

### 1.6 Python `PublishedAppDelegate`

`worker/platform_worker/agents/delegate.py`：`DelegateTransport` 仅
`spawn_child`/`child_result`；`HTTPDelegateTransport` 真实 mTLS（worker 证书 +
`X-Internal-Role: worker`）；`PublishedAppDelegate` 实现既有 `ToolExecutor`，logical key 由
`(run_id, invocation_path, tool_name)` 决定，live child 抛 `ToolExecutionError(waiting=True)`
（`agent.py` 新增 `waiting` 语义并映射为 `NodeOutcome(state="waiting")`），终态结果按冻结
output schema 本地再校验。模块无 DB/DSN 依赖。

### 1.7 Run 页面

`apps/web/src/runs.html` 新增 `#children-panel`（`#children-status`/`#children-list`）；
`runs.js` 新增 `runChildrenPath`/`artifactContentPath`/`describeChildRun`/`loadChildRuns`/
`childRunSummaryText`/`loadChildRunsPanel`，详情打开时加载独立 child 列表，succeeded 且有
output artifact 的 child 再读结构化结果并展示；点击“查看”打开 child 自己的详情。页面只显示
服务端 Run/结果，未终态 child 不显示成功。

### 1.8 T075 partial 闭环

`test_real_publication_pins_the_child_and_closes_t075`：真实 `submitApplication` → 管理员
approve 发布子 v1 与父版本（真实 `version_dependencies` 边）；发布更严格子 v2 后，父版本行、
冻结 manifest 字节、边逐字节/逐行不变，`GET /versions/{parent_v1}` sha 不变；父草稿切到 v2 被
`io_mapping_missing_field`（`/nodes/1/inputs/region`）拒绝；真实发布的 v1 可被 `spawnChild`
解析并创建 child Run。T075 报告 §1.4/§4/§5 与 PROGRESS 已同步为 passed 并写明仍未跑的
“真实 WSL2/runsc 五类报告”。

## 2. 测试与证据

新增 `tests/f07/test_t078_child_runs.py`（8 用例）与 `tests/f07/test_t078_pages.py`（3 用例），
支撑 `tests/support/t078.py`；所有用例真实栈（真实 PG 16.4、编译 `cmd/api`/`cmd/supervisor`/
`cmd/maintenance`、真实 HTTP/mTLS、真实对象字节、真实 result/exit 协议、Node 执行页面逻辑）。

| 用例 | 证明 |
|---|---|
| `test_spawn_replay_conflict_closure_and_the_sealed_mapped_input` | 202 + Run 十字段；同键同体→同 id 且无第二 Run/收据；同键异输入/异版本→409 定位字段；闭包外版本→403；伪造 token→409；`io_mapping`/`input_sha256`；封存字节恰为 `{"source":"one"}`、无父历史标记 |
| `test_supervisor_delegates_then_returns_the_child_result` | 真实 supervisor：父 spawn→`waiting_children`；child 独立 Run 由第二次 `exec --once` 执行成功；T035 bridge 决议→单一 resume job→第三次 `exec --once` 重放同一 child、取回结构化结果并完成父；`run_children` 仍 1 行、父 2 attempts；owner 可见 child 详情/列表/结果，他人 404 |
| `test_two_concurrent_children_are_isolated_runs_and_threads` | 同父并发两个不同 invocation key→两个 Run、不同 thread/attempt/output，结果分别为 alpha/beta |
| `test_cannot_borrow_the_child_authors_permissions` | 撤权后 spawn 403 `child_not_visible`；重新授权后 202，child owner=调用者（非子作者），child snapshot 是子版本而非父 |
| `test_a_nonconforming_child_result_fails_the_child_not_the_parent` | 真实上传不合规 output 并 succeeded+exit→child `failed`/`child_output_schema_invalid`；父仍 running 无 error |
| `test_real_publication_pins_the_child_and_closes_t075` | §1.8 |
| `test_published_app_delegate_uses_only_spawn_child` | 真实 mTLS `PublishedAppDelegate`：首调 spawn+waiting；child 执行后同 invocation path 重放原 child 并返回结构化结果；不同 path 生成独立 child；静态守卫无 DB/自建 Run 路径 |
| `test_agent_subapp_tool_creates_a_platform_child` | `start -> agent -> end` 真实执行：dev-stub 模型调用 `call_app_1`→真实 child Run（logical key `agent:analysis:subapp:stub-call-1`），父 `waiting_children` |
| `test_the_runs_page_serves_the_child_runs_panel` / `..._parse_with_node` / `test_the_child_runs_panel_logic_executes_under_node` | 页面/资产真实 HTTP；`node --check`；Node 执行 helper：请求路径、投影、不把 running 当成功、结果读失败降级 |

Go 单测：`internal/catalog/childspawn_test.go`（spawn 复查、agent 子集映射、schema 身份/输入输出校验、
精确输入集合）、`internal/presetexec/plan_test.go`（call_app/identity/agent 链形状与拒绝矩阵）、
`internal/presetexec/callapp_test.go`（waiting/终态结果/required 失败/refusal 码/identity 链/
agent 子应用工具）。真实栈覆盖行为，Go 单测锁定纯规则与执行器守卫。

## 3. 门禁与原始输出

环境 profile：Windows 宿主机；Python 3.12.2；Go 1.27.3 windows/amd64
（`E:\orchara-toolchains\go`）；Node v24.13.0；PostgreSQL 16.4 用户态集群
`127.0.0.1:55432`（每用例 `create_database` + 真实 `cmd/admin migrate`）；`dev-stub` profile。
所有 pytest 均带 `--basetemp=E:\orchara-tmp\t078-*`。

### 3.1 `pytest tests/f07`（合并前，两轮背靠背；含 T075 12 用例 + T078 11 用例）

```
run1: ......................                                              [100%]
      22 passed in 284.83s (0:04:44)
run2: ......................                                              [100%]
      22 passed in 226.73s (0:03:46)
```

（此两轮之后又加入了 `test_agent_subapp_tool_creates_a_platform_child`，并放宽了 agent 无 python
链；**最终树**的两次全量见 §3.6。）

### 3.2 邻接回归

```
pytest tests/tooling tests/f02a/test_t025_dependencies.py -q
  27 passed in 80.82s
pytest tests/f03/test_t029_claim.py tests/f03/test_t035_waiting.py tests/f03/test_t036_cancel.py -q
  26 passed in 298.84s
pytest tests/f06/test_t043_agent_node.py tests/f06/test_t041_capabilities.py -q
  <§3.7 原始输出>
```

### 3.3 Go 与契约

```
go build ./...   -> 无输出（成功）
go vet ./...     -> 无输出（成功）
go test ./...    -> 全部包 ok，无 FAIL（internal/children 尚无单测文件，真实栈覆盖）
gofmt -l .       -> 无输出（干净）
python tools/sync_spec.py --check -> no drift: spec copy and contract snapshot match the manifest
```

### 3.4 合并后最终树复跑

见 §3.6（合并 `integration/t068-t082` 后重跑 f07 ×2）。

## 4. AC 对照

| 验收项 | 结论 | 证据 |
|---|---|---|
| 重放返回原 child；同键异输入/版本冲突 | **passed** | `test_spawn_replay_conflict_closure_and_the_sealed_mapped_input`；Go `TestMatchChildInputMappingRequiresExactlyTheDeclaredFields` |
| 同子应用并发两次相互隔离 | **passed** | `test_two_concurrent_children_are_isolated_runs_and_threads`（不同 Run/thread/attempt/output） |
| child 结果符合固定 Schema | **passed** | `test_a_nonconforming_child_result_fails_the_child_not_the_parent` + `children.ResultValidator`（finalizer 内，fails child 不触碰 parent）；规则：EXECUTION.md §7 |
| 不能借子作者权限 | **passed**（交集 = 调用者当前读 ACL ∩ 父冻结闭包 ∩ 子自身冻结 manifest；撤权即时 403；child owner=父 owner） | `test_cannot_borrow_the_child_authors_permissions`；§1.2/§1.3 |
| 未显式映射内容不出现在 child | **passed** | 同用例：`io_mapping == {node_id,fields:["source"]}`，封存字节 `{"source":"one"}`，父输入中的 `PARENT-HISTORY-MARKER` 不在 child 输入 artifact |
| SDK 内部子 Agent 不绕开平台调度 | **passed**（`call_app` 真栈 + `agent.subapp_versions` 真栈 + Python delegate 真栈；静态守卫） | `test_supervisor_delegates_then_returns_the_child_result`、`test_agent_subapp_tool_creates_a_platform_child`、`test_published_app_delegate_uses_only_spawn_child`、Go `TestCallApp*`/`TestAgentSubappToolSpawnsAChild` |
| 通过端到端入口实际演示；失败/授权/重试边界有断言 | **passed** | 上述用例覆盖 spawn 拒绝码、409/403、非终态等待、required child 失败、schema 不合规、结果读拒绝 |
| 记录版本/输入 hash/命令/原始结果/环境；stub/real 分报 | **passed** | 本报告 §3；`run_children.input_sha256`、`input_schema_sha256`、`output_schema_sha256`；真实栈命令见 §3 |
| 页面显示子运行时间线/结构化结果/state | **passed** | §1.7、`tests/f07/test_t078_pages.py`；浏览器 DOM not_run |
| T075 partial 闭环（真实发布 + 新子版本 + 固定引用/边不变 + 切换拒绝） | **passed** | §1.8、`test_real_publication_pins_the_child_and_closes_t075` |

## 5. `not_run` / 边界（如实）

1. **child 终态自动恢复 hook / 独立子槽 / 每父并行上限**：F07/T081。本票的父恢复测试使用既有
   T035 `waiting-resolution` bridge；生产自动路径未实现（报告中明确）。因此**真实 WSL2/runsc 的
   五类评测**（call_app 父运行需等 child 终态后恢复）仍 `not_run`；T075 闭环使用 T068 已验证的
   五类报告 fixture（evaluation/case/run/snapshot 真实，terminal 状态为种子）。
2. **共享父预算/次数原子**：F07/T080。本票只做 spawn 时的结构深度/声明次数，不实现预算账本。
3. **并行子调用的父等待容量/公平领取**：F07/T081。
4. **浏览器 DOM 驱动页面**：not_run；Node 执行的是同一页面纯逻辑。
5. **真实模型 provider**：继承 dev-stub（F06/F07 现状）；agent 子应用真栈用例即 dev-stub 脚本
   的模型 tool call。
6. **全量 `pytest tests`**：BRIEF 明确不跑；已跑 f07×2、tooling、f02a T025 依赖、f03 claim/
   waiting/cancel、f06 T041/T043。

## 6. 给后续票的接口

- `internal/children.Service.Spawn/Result/ListForParent`：唯一 child Run 创建/读取路径；
  `Spawn` 的锁序是 parent→version→seal→child；**T080/T081 不要复制它**，只扩展预算/槽/恢复。
- `platform.run_children`：T081 的 child 终态 hook 按 `child_run_id` 读它，更新父等待；不新增状态列。
- `execution.Service.ResultValidator` / `children.InvalidRunResultError`：T080 的 required-child 失败
  收敛读取 child 的 `error_code` 即可。
- `presetexec.Client.SpawnChild/ChildResult` + `SpawnChildRequest`：T081 的等待/恢复复用同一 client；
  `call_app` 的 checkpoint 规则 `child:<child_run_id>` 固定。
- 非契约 REST：`POST .../children/{id}/result`、`GET /api/v1/runs/{id}/children`；后者是页面唯一
  child 列表来源，T082 交接 UI 可复用。
- `worker/platform_worker/agents/delegate.py`：`DelegateTransport`/`ChildApp`/`PublishedAppDelegate`
  是 DeepAgents 的唯一委派接缝。

## 7. 参考

- ADR：[0025 — F07/T078 child Runs, replay identity and non-contract reads](docs/adr/0025-f07-child-runs.md)
- 契约：`spawnChild`（`contracts/openapi.json`）；EXECUTION.md §7（:70 权限交集/最小上下文、:72 幂等键与
  冲突、:74 task 工具经平台适配、:78 fail-fast）；OPERATIONS.md:28 子调用限制；STATE-MACHINES.md:28/66/78。
- 依赖：T074 `ResolveRunnableVersionTx`/published run 形状；T075 `CheckChildDelegation`；T068 发布事务/
  五类报告 fixture；T035 waiting bridge（T081 替换）。
