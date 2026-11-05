# F07 T075 验收：在创作台选择固定子应用并校验依赖（#76）

- 任务：F07 / GitHub Issue #76（票 `T075`，规划 ID F07.01），分支
  `t/t075-fixed-subapp-deps`（worktree `E:\orchara-worktrees\t075`，基线 = 在飞
  T068 发布分支 `t/t068-publication-review` @ `b9acfa7`）。
- 合并：`integration/t068-t082` @ `163940f`（含 T069）合并为 `4d2b928`；因该时刻集成分支尚未
  收到 T068 的 0043 重编号（T068 分支自己已 merge 集成并重编号），再合并
  `t/t068-publication-review` @ `b30128b` 为 `1a201b3`，只解决 `PROGRESS.md` 的行冲突；随后
  集成分支前进到 `afeba90`（T070 撤销并入），最终合并为 **`eb46b0c`**（再解决
  `PROGRESS.md` 行/节与 `tests/support/t025.py` revoked 种子两处冲突）。最终树迁移链
  `0042_deletion_foundation`（T069）+ `0043_publication_reviews_and_outbox`（T068），
  ADR `0020`（T069）/`0021`（T068）/`0022`（T070）/`0023`（本票）。合并树上重跑全部门禁
  （§3.7）：`tests/f07` 12 passed ×2、`tests/f02a` **104 passed / 0 failed**、`tests/tooling` 21 passed。
- 状态：**`partial`**。范围内已真实交付并验证：`call_app` 的 I/O 映射/类型校验（新 5 个定位码）、
  `policy.max_child_calls`/`max_child_depth` 对真实 `platform.version_dependencies` 闭包的检查、
  撤销/未发布/循环三类拒绝的区分、创作台 `call_app` 面板（真实 catalog/version 读 + 保存 + 校验 +
  节点高亮）。**未闭环**：真实发布事务写入 `version_dependencies` 并给出“父发布后子最新版不替旧引用”
  的端到端断言——见 §1.4 的具体阻塞（T078 #79）。
- 边界（如实）：父版本固定的是**子版本 id**（`call_app.config.application_version`,
  `agent.subapp_versions`），闭包由 `platform.version_dependencies` 与之上的冻结 manifest 表达；
  子运行执行/`spawnChild`/预算/等待属 T078/T080/T081，本票**不实现**；`internal/presetexec` 对
  `call_app` 仍诚实拒绝（`tool_unsupported_graph`，本票新增回归断言），作为 T078 的接口记录；
  F04A Vue 画布 bundle **未重建**（见 §5.2）；浏览器 DOM 驱动 not_run；未改
  `docs/spec/**`/`contracts/**`，`sync_spec --check` no drift；**无迁移**（见 §1.1）。

## 0. 工作记录（简短）

1. 读 `BRIEF.md`、票 #76、`notes/f07-delegation.md` §3.1/§4/§10、`WORKFLOW-SPEC.md` §2/§3、
   `COMPILER-CONTRACT.md`、`contracts/workflow.schema.json`（`call_app` config 仅
   `{application_version}`；`policy.max_child_depth` 0..3、`max_child_calls` 0..12）、
   `internal/catalog/{manifest,draft,versions,publication}.go`、`internal/workflow/validate.go`、
   `internal/api/validate.go`、`apps/web/src/{editor.html,assets/editor.js}`、T068 的
   `internal/catalog/publication.go`（approve 事务里的 edge 写入）与 `tests/support/{t025,t068}.py`。
2. 用真实栈确认 T068 的发布事务**已**为 `manifest.subapp_versions` 的每一项写
   `version_dependencies`（`publication.go:1023-1034`，输入集由 `collectDependencyRefs` 同时覆盖
   `call_app.application_version` 与 `agent.subapp_versions`，见 Go 测试证明）。
3. 探查真实发布前置条件：`SubmitPublication`/approve 都要求“五类作者案例 + 真实 runner 报告”；
   唯一图执行器 `internal/presetexec` 只接受 `start/python/agent/end`（`plan.go:123-127`）并且拒绝
   非空 `agent.subapp_versions`（`agent.go:154-157`），因此**任何含 `call_app` 的定义都无法通过
   application 评测**，基线没有任何 API 路径能发布带固定子应用的父版本。先尝试跑一条真实
   `tests/f02b` 评测（13:37 启动，13:39 后 supervisor 停在 sandbox 冷启动，同机另有 T068/T069/T070
   三套 pytest 并发占满沙箱），据此决定：本票测试**不依赖 WSL2/runsc 评测**，而用“真实校验器封印
   的 manifest 字节 + 发布器存储形状”的版本 fixture（`tests/support/t075.py` 明写跳过的是哪一个
   事务），并把该 AC 标为 partial。
4. TDD 垂直切片：
   - Go 单测（先写 `delegation_test.go` 红）→ 实现 `internal/catalog/delegation.go`
     （映射/类型/输出细化/深度/次数）→ green；
   - 真实 HTTP 用例（先写 `tests/f07/test_t075_delegation.py` 红）→ 在
     `ValidateExecution` 中接线（`checkChildDelegation`）→ 9 passed；
   - 页面用例（先写 `tests/f07/test_t075_pages.py` 红）→ 实现 editor 面板与纯函数 → 3 passed；
   - 既有 `tests/f02a`（含 `call_app` 撤销/归档/外部/循环覆盖）回归；`test_t025_dependencies`
     依赖 `seed_published_version` 现在带冻结 `definition`。
5. 跑门禁、写 ADR 0023、本报告与 PROGRESS 行；合并 `integration/t068-t082` 后复跑。

## 1. 交付

### 1.1 无迁移（说明）

本票需要的持久事实都已存在：`application_versions`（T068 发布器写入，含
`manifest`/`manifest_sha256`/`manifest_artifact_id`）、`platform.version_dependencies`
（`0010_application_drafts.sql:102-112`，T068 approve 事务写边）、`policy` 在冻结 manifest 的
`definition.policy` 内。T075 只增加**校验规则**与**创作台建模**，不新增表/列，所以没有 `0043_*`。
（`run_children`、child 配额等属 T078/T080/T081，见 notes §4。）

### 1.2 I/O 映射与类型校验（`internal/catalog/delegation.go`）

新增门禁位于 `ValidateExecution` 就绪/循环检查之后、`compiler.Build` 之前，因此
`POST /applications/{id}/validate`、`POST /publication-preview`、`POST /submit` 走同一个 seam；
有错误时**不封印 manifest、不建 version/review**。

子版本 schema 来自其**已发布版本的冻结 `manifest.definition`**（`application_versions.manifest ->
'definition'`，一次查询随就绪检查取回，不增第二条查询）。没有冻结 definition 的版本（只有 T075 之前
的测试夹具会产生）报 `dependency_not_ready`，不会被当作“空 schema”静默通过。

| 问题 | code | pointer |
|---|---|---|
| 映射了子版本未声明的输入字段 | `io_mapping_unknown_field` | `/nodes/<i>/inputs/<field>` |
| 子版本必填输入未映射 | `io_mapping_missing_field` | `/nodes/<i>/inputs/<field>` |
| 可静态判定类型的绑定与子 schema 不兼容 | `io_mapping_type_mismatch` | `/nodes/<i>/inputs/<field>` |
| 节点声明了子版本不产出的输出 | `io_output_unknown_field` | `/nodes/<i>/output_schema/properties/<field>` |
| 节点输出类型子版本无法产出 | `io_output_type_mismatch` | `/nodes/<i>/output_schema/properties/<field>` |

规则要点（ADR 0023 §1）：类型兼容 = 同名类型 + `integer → number` 放宽；字面量按 JSON 值类型
（整数/小数的区分用 `math.Trunc`）；`ref` 的静态类型来自 `input_schema` 或上游节点 `output_schema`
的 JSON Pointer 解析，无法静态判定的绑定不报错（只报可证明的错误）。子输入 schema 显式
`additionalProperties: true|{schema}` 时允许多余映射字段；输出侧不允许子版本未声明的字段
（平台只保证冻结 schema 声明的输出）。节点输出 schema 可以是子输出的**子集**，不能发明字段或类型。

### 1.3 深度/次数限制（同文件）

- `policy.max_child_calls`：统计定义声明的固定子引用——每个 `call_app` 节点 1 次，每个 agent
  `subapp_versions` 允许集里**去重后**的每个版本 1 次；超过则 `child_calls_exceeded`，定位
  `/policy/max_child_calls`（限制字段本身）。
- `policy.max_child_depth`：从 `platform.version_dependencies` 的真实闭包计算——被引用子版本为深度 1，
  它的子版本为 2，依此类推；超过则 `child_depth_exceeded`，定位到引用该子版本的具体
  `/nodes/<i>/config/...` 字段。闭包边由循环检查同一次 `loadVersionEdges`（递归 CTE，`UNION` 终止
  环）返回并复用，深度与循环不可能对“闭包”产生两种解读；深度遍历带 memo 与环保护，环存在时先被
  `dependency_cycle` 拒绝。

### 1.4 发布固定与发布器事实（含 partial 说明）

- **已确认**：T068 的 approve 事务在插入 `application_versions` 后，对
  `normalizedDependencyIDs(verified.Manifest.SubappVersions)` 的每一项执行
  `INSERT INTO platform.version_dependencies ... ON CONFLICT DO NOTHING`
  （`internal/catalog/publication.go:1023-1034`）。`Manifest.SubappVersions` 的来源由 Go 测试
  `TestCollectDependencyRefsLocatesEveryConfigField` 证明：`call_app.application_version` 与
  `agent.subapp_versions[]` 都作为 `subapp` 引用进入闭包，最后由 `compiler.Build` 归一化排序去重。
  没有任何 SQL 更新既有 `application_versions`/`version_dependencies` 行（全仓仅两处 INSERT）。
- **端到端未执行（partial，owner T078 #79）**：真实 `submitApplication` → 审核 → approve 要求一份
  通过的五类作者案例报告；`internal/presetexec` 目前拒绝 `call_app`（`plan.go:123-127`）与非空
  `agent.subapp_versions`（`agent.go:154-157`），所以含固定子应用的定义**无法**产生 passed 报告，
  也就没有任何 API 路径能发布带依赖的父版本。本票不实现子运行（T078），也不伪造报告。
- 因此 T075 的真实栈测试把版本行物化为**发布器的存储形状**：真实 `POST /validate` → 真实
  canonical manifest 字节（对象存储 + sha256 复算）→ `application_versions` 行 → 按 manifest 自己的
  `subapp_versions` 写边（`tests/support/t075.py:seal_version`，返回记录带
  `published_by="publisher-shape-fixture"`）。测试注释与报告都明写“跳过的是发布审核事务”，不把
  fixture 结果说成发布成功。
- **仍然可证且已断言**（`test_a_newer_child_version_never_replaces_the_pinned_reference`）：父草稿
  校验后冻结的 manifest 字节、`subapp_versions == [child v1]` 与 `X-Manifest-SHA256` 在**子 v2
  发布/存在之后**逐字节不变；父版本行、`version_dependencies` 边与冻结 artifact 字节不变；
  `GET /versions/{v1}` 仍返回 v1 的 manifest（子 v2 有自己的 sha）；把父草稿改为引用 v2 会立即得到
  `io_mapping_missing_field`（证明 v2 确实更严格，而旧引用没有被“最新版”替换）。
- **不由路由临时拼流程**：本票唯一的“用哪个子版本”事实写在父版本的冻结 manifest
  （`subapp_versions`）与 `version_dependencies` 边里；validate/preview/submit 用同一份编译结果，
  `ResolveRunnableVersion` 只接受显式 version id（没有 latest 解析），运行时（T078）只从 manifest
  的固定集合读取。仓库里没有任何“按目录/名称在请求期组装子流程”的代码路径。

### 1.5 创作台 `call_app` 面板（`apps/web/src/{editor.html,assets/editor.js}`）

- 应用/版本选择器只用既有 ACL 过滤读：`GET /api/v1/applications`、`GET
  /api/v1/applications/{id}/versions`（真实 HTTP 断言：外部应用不出现在列表；无授权时版本接口
  404；显式读授权后可列出且带冻结 manifest，用于渲染 child schema；T070 之后已撤销版本不在列表内，
  且其 detail 读为 404）。
- 选中版本后按冻结 `definition.input_schema`/`output_schema` 生成映射行：应用输入
  （`input#/...`）、字面量 JSON、上游节点输出（`node:<id>#/...`）；输出行勾选并选类型。
- “加入/更新 `call_app` 节点”写入同一份 definition：新节点插在 `end` 前并改写
  `python1 -> end` 为 `python1 -> call_app -> end`；同 id 更新不增节点；其它类型占用的 id 拒绝。
- “保存并校验”复用页面唯一保存路径（If-Match/412/428/401 语义不变）后调用 `POST /validate`，
  把定位错误按 `/nodes/<i>...` 映射到节点条：出问题的节点 chip 标红并列出 `code · pointer ·
  message`，文档级错误（如 `/policy/max_child_calls`）单列。
- **F04A 画布 bundle 未重建**：画布仍是 `start/python/agent/end` 的线性四节点（其 committed 契约），
  没有伪装成画布第五种节点；`call_app` 在编辑器自有面板编辑并进入同一 definition。画布 palette 的
  `call_app` 项 **not_run**（理由见 §3.3）。

### 1.6 presetexec 诚实拒绝（T078 接口，非本票实现）

`internal/presetexec/plan_test.go` 的拒绝矩阵新增一例：含 `call_app` 节点的定义必须仍以
`tool_unsupported_graph` 确定性拒绝（不是重试、不是伪成功）。T078 实现子运行后，该用例应改为
真实计划/执行断言——本票把它写成回归守卫。

## 2. 测试与证据

### 2.1 真实栈（`tests/f07`）

`tests/f07/test_t075_delegation.py`（9 用例，真实 PG 16.4 + 编译 `cmd/api` + 真实 HTTP +
creator 会话 + CSRF + 真实对象字节）：

- `test_a_compatible_mapping_is_accepted_and_the_child_version_is_pinned`：兼容映射通过，
  `X-Manifest-SHA256` 与封印 artifact 一致，manifest `subapp_versions` 固定为孩子版本。
- `test_mapping_errors_are_located_at_the_offending_field`：必需字段缺失/未声明字段/字面量类型
  三种定位（含 `node_id="child"`），且失败校验不建 version/manifest artifact 行。
- `test_an_incompatible_output_declaration_is_located`：`io_output_unknown_field` +
  `io_output_type_mismatch` 同时定位到 `/nodes/1/output_schema/properties/...`。
- `test_unpublished_revoked_and_archived_child_versions_are_distinguishable`：
  `dependency_not_found` / `dependency_revoked` / `dependency_not_visible` 三个可区分 code，全部定位到
  `/nodes/1/config/application_version`；外部（未授权）版本同样不可见。
- `test_a_real_dependency_cycle_and_a_self_call_are_refused`：真实
  `platform.version_dependencies` 上的 A↔B 环 → `dependency_cycle`；同一应用里引用自己已发布版本
  （seal 出的 v1）→ `dependency_cycle`。
- `test_child_depth_is_enforced_over_the_real_version_closure`：真实子→孙边（深 2）在
  `max_child_depth=1` 时 `child_depth_exceeded`（message 含 2），`=2` 时通过。
- `test_child_call_count_is_enforced_over_the_declared_references`：两个 `call_app` 节点在
  `max_child_calls=1` 时 `child_calls_exceeded`（定位 `/policy/max_child_calls`），`=2` 时通过。
- `test_a_newer_child_version_never_replaces_the_pinned_reference`：见 §1.4。
- `test_the_picker_reads_are_acl_filtered`：创作台的两种读都是 ACL 过滤的真实行为。

`tests/f07/test_t075_pages.py`（3 用例）：页面/资产真实 HTTP 提供 + 面板标记；HTML 内联脚本与
`editor.js` 过 `node --check`；Node（stub fetch/window，真实页面代码）执行选择器模型、schema 抽取、
映射行、`callAppNodeFromForm`（含 6 类拒绝）、`upsertCallAppNode` 的插入/改线/幂等/类型冲突、
`callAppValidationModel` 的节点高亮与文档级错误、`callAppPrefillFromNode`、以及
`validateDraftRequest` 的 POST + `X-CSRF-Token` 请求形状。

### 2.2 Go 单测

`internal/catalog/delegation_test.go` 10 个用例：兼容映射、缺必填、未知字段、字面量/引用类型不匹配、
显式开放子 schema、输出未知/类型不匹配、无冻结 definition、真实闭包深度（2 vs 1/2）、声明引用计数
（重复允许集只算一次）、环闭包下深度遍历终止。`internal/presetexec` 新增 call_app 拒绝例。

### 2.3 回归

`tests/f02a` 全量（既有 `call_app` 撤销/归档/外部/循环覆盖 + T023/T025/T027）；
`tests/support/t025.py:seed_published_version` 现在总是带冻结 `definition`（默认空 schemas），
现有用例语义不变。另跑 `tests/tooling`、Go 包与 `sync_spec`。

## 3. 门禁与原始输出

环境 profile：Windows 宿主机；Python 3.12.2；Go 1.27.3 windows/amd64（
`E:\orchara-toolchains\go`）；Node v24.13.0；PostgreSQL 16.4 用户态集群
`127.0.0.1:55432`（每个用例 `create_database` 新建、`cmd/admin migrate` 真迁移后销毁）；
`cmd/api` 真编译二进制 + 真对象存储目录 + 真实 creator 会话/CSRF；`dev-stub` profile。
测试不以 WSL2/runsc 评测为前提（见 §5.3）。所有命令都在
`E:\orchara-worktrees\t075\product` 下执行，`--basetemp` 指向 `E:\orchara-tmp\`。

### 3.1 `python -m pytest tests/f07 -q --basetemp=E:\orchara-tmp\t075-run1`（第 1 轮）

```
............                                                             [100%]
12 passed in 128.22s (0:02:08)
```

### 3.2 第 2 轮（同一命令，`t075-run2`）

```
............                                                             [100%]
12 passed in 116.83s (0:01:56)
```

### 3.3 `pytest tests/tooling`、`pytest tests/f02a`（回归）

```
.....................                                                    [100%]
21 passed in 8.55s
```

`tests/f02a`：见 §3.5（其中 2 个失败为 **T068 基线自带**，已在基线 `b9acfa7` 上复现；
1 个 T025 撤销夹具失败已由本票修好并通过）。

### 3.4 Go 与契约

```
go build ./...            -> 无输出（成功）
go vet ./...              -> 无输出（成功）
go test ./...             -> 33 个包 ok，无 FAIL
gofmt -l .                -> 无输出（干净）
python tools/sync_spec.py --check
  -> no drift: spec copy and contract snapshot match the manifest
```

### 3.5 `pytest tests/f02a -q --basetemp=E:\orchara-tmp\t075-f02a-3`（合并前分叉树，103 用例）

```
FAILED tests/f02a/test_t023_invalid_draft.py::test_an_invalid_draft_is_refused_by_validate_and_debug_while_submit_is_absent
FAILED tests/f02a/test_t023_migration.py::test_publication_reviews_report_id_fk_lands_with_t026
2 failed, 102 passed in 1053.17s (0:17:33)
```

（一次更早的 f02a 全量运行在四套 pytest 并发、宿主机内存紧张时出现 61 个
setup error——均为 fixture/进程启动失败；单测重跑通过，随后这次顺序运行除上述 2 个基线失败外
102 passed。）

**基线复现（本票改动全部 stash 后在 `b9acfa7` 上运行同一命令）**：

```
FAILED tests/f02a/test_t023_invalid_draft.py::test_an_invalid_draft_is_refused_by_validate_and_debug_while_submit_is_absent
FAILED tests/f02a/test_t023_migration.py::test_publication_reviews_report_id_fk_lands_with_t026
2 failed in 35.50s
```

- 前者断言 `POST /submit` 是 404（T023“F02B 尚无端点”的证据），T068 已实现该端点，现返回
  400 `idempotency_key_required`；断言已过时。
- 后者断言 `publication_reviews` 只有 3 个 FK，T068 的 `0042` 迁移又加了
  `snapshot_id/application_version_id/evaluation_id/...` 4 个 FK；断言已过时。
- 两者都是 **T068 分支的既有缺陷**，不属于 T075 改动（本票不新增端点、不新增 FK）；本票不改
  其它票的测试文件以避免与其在飞分支冲突，留待 T068/orchestrator 收口。
- 第三个 f02a 失败（`test_subapp_readiness...` 的 revoked 种子）由 T068 的
  `application_versions_revoke_shape` CHECK 引起：`seed_published_version(revoked=True)` 现在同时写
  `revoke_reason`，该用例已通过（21.12s）。

### 3.6 合并后最终树复跑（`1a201b3`，T069+0042 与 T068 0043/ADR0021 已并入）

```
pytest tests/f07 --basetemp=E:\orchara-tmp\t075-merged1   -> 12 passed in 122.91s (0:02:02)
pytest tests/f07 --basetemp=E:\orchara-tmp\t075-merged2   -> 12 passed in 117.40s (0:01:57)
pytest tests/tooling --basetemp=E:\orchara-tmp\t075-tooling-merged -> 21 passed in 6.65s
pytest tests/f02a --basetemp=E:\orchara-tmp\t075-merged-f02a       -> 104 passed in 1077.38s (0:17:57)
go build ./... / go vet ./...                             -> 无输出（成功）
go test ./...                                             -> 无 FAIL
gofmt -l .                                                -> 无输出（干净）
python tools/sync_spec.py --check                         -> no drift
```

合并后 T068 已自行更新 `test_t023_invalid_draft.py`/`test_t023_migration.py` 的两处过时断言
（T023 的 submit-404 与旧 FK 集合），§3.5 记录的两个基线失败随合并消失：**合并树 f02a 104 passed，
0 failed**。§3.5 保留的是合并前分叉树的历史输出与基线复现证据，用于说明那两个失败不属于 T075。

### 3.7 最终集成合并树（`eb46b0c`，integration @ `afeba90` = T068+T069+T070 已并入；本票在此树上复跑）

```
pytest tests/f07 --basetemp=E:\orchara-tmp\t075-final1        -> 12 passed in 111.05s (0:01:51)
pytest tests/f07 --basetemp=E:\orchara-tmp\t075-final2        -> 12 passed in 104.28s (0:01:44)
pytest tests/tooling --basetemp=E:\orchara-tmp\t075-final-tooling -> 21 passed in 5.33s
pytest tests/f02a --basetemp=E:\orchara-tmp\t075-final-f02a   -> 104 passed in 1103.53s (0:18:23)
go build ./... / go vet ./...                                 -> 无输出（成功）
go test ./...                                                 -> 无 FAIL
gofmt -l .                                                    -> 无输出（干净）
python tools/sync_spec.py --check                             -> no drift
```

合并解决：`PROGRESS.md` 行/节冲突（保留 T068、T070、T075 三行与各自小节）；
`tests/support/t025.py` 的 revoked 种子冲突（采用 T070 的 `revoked_at/revoked_by/revoke_reason`
三列版本，正好满足 0043 的 revoke CHECK）。本票 ADR 因 T070 占用 0022 而重编号为 `0023`。
T070 合并后 `ListVersions`/`GetVersion` 已过滤撤销版本，创作台版本选择器因此天然不列出已撤销版本，
本票新增一条真实 HTTP 断言（`test_the_picker_reads_are_acl_filtered` 的 revoked 段）。

## 4. AC 对照

| 验收项 | 结论 | 证据 |
|---|---|---|
| 未发布/撤销/循环子版本拒绝（三码可区分、定位正确） | 通过（撤销=真实 revoked_at 行；未发布=真实缺行；循环=真实 `version_dependencies` 环） | `test_unpublished_revoked_and_archived_child_versions_are_distinguishable`、`test_a_real_dependency_cycle_and_a_self_call_are_refused`；既有 `tests/f02a/test_t025_dependencies.py` 回归 |
| 映射不兼容被定位（code + JSON pointer，编辑器高亮） | 通过 | `test_mapping_errors_are_located_at_the_offending_field`、`test_an_incompatible_output_declaration_is_located`；面板 Node 用例的 `callAppValidationModel` |
| 父发布后子最新版不替旧引用 | **partial**：冻结/解析层已证明（manifest 字节、sha、`subapp_versions`、版本读在子 v2 后不变；改为引用 v2 立即失败）；“父版本真的发布”与“发布事务写边”因 T078 运行时不支持 `call_app` 而无法执行（无 passed 五类报告 ⇒ 无发布路径） | `test_a_newer_child_version_never_replaces_the_pinned_reference`；§1.4 的代码锚点 + Go 依赖集测试；ADR 0023 §4 |
| 深度/次数限制按真实闭包执行 | 通过 | `test_child_depth_is_enforced_over_the_real_version_closure`、`test_child_call_count_is_enforced_over_the_declared_references`、Go `TestChildDepthIsMeasuredOverTheRealClosure`/`TestChildCallCountIsTheDeclaredFixedReferenceCount` |
| 创作台选固定子应用、声明 I/O 并看到定位错误 | 部分为 true：编辑器面板（真实页面代码 + Node 执行 + 真实 HTTP 端点）已交付；F04A Vue 画布 palette 未加第五种节点 | `tests/f07/test_t075_pages.py`；§1.5；`not_run` 列表 |
| 运行时执行 `call_app` 不在本票（T078） | 遵守：`presetexec` 仍拒绝，新增回归断言 | `internal/presetexec/plan_test.go` call_app 用例 |

## 5. `not_run` / 未闭环

1. **真实发布事务对依赖版本写边**（owner T078 #79）：无 API 路径可发布含 `call_app` 的版本（五类报告
   需要能执行该图）。本票以真实封印字节的发布器形状 fixture 覆盖读侧，不声称发布成功。
2. **F04A Vue 画布 bundle 的 `call_app` 节点/palette**：画布是已交付的线性四节点特性，其 committed
   契约与 `linearChainProblems`/`addNode` 只允许四种节点；重建需改 F04A 的模型与页面测试，且画布
   主应用只能被浏览器执行（Node 只测 `lib/*.js`）。本票选择编辑器自有面板并在 §1.5/§5.2 明示。
3. **真实 WSL2/runsc 五类评测**：本机同时有 T068/T069/T070 三套套件并发，13:37 的一次真实评测在
   sandbox 冷启动处停住（13:39 后无 API 调用、supervisor 空转），故本票测试不依赖评测；真实
   `tests/f02b` 不受本票修改影响但未在本票内重跑（由 T068 复跑）。
4. **浏览器 DOM 驱动面板**（点击/下拉/高亮绘制）：not_run；Node 执行的是同一页面函数的纯逻辑。
5. **同一子应用并发调用、child Run/预算/等待**：T078/T080/T081。

T070 合并后补充：`ListVersions`/`GetVersion` 现在过滤 `revoked_at`（T070 的版本撤销），因此创作台的
版本选择器**不会列出已撤销版本**；若撤销发生在草稿已固定该版本之后，`validate` 仍按显式 version id
以 `dependency_revoked` 定位并显示——两条规则合起来满足“只提供可调用的发布版本”，客户端不需要
（也无法）自己判断撤销状态。

## 6. 给后续票的接口

- `internal/catalog/delegation.go`：`checkChildDelegation(document, refs, subappRows, versionEdges)` 是
  mapping/limit 的唯一实现；T078 的 `spawnChild` 复查必须复用它，不得另建兼容表。
- `subappDependency.definition`（`manifest -> 'definition'`）就是子版本冻结 I/O schema 的读取点；
  子输出校验（T078）与运行期 `child_output_schema_invalid` 应使用同一份 definition。
- `executionDependencySet.versionEdges`：循环检查与深度检查共享的闭包视图；T081 的容量/深度判定可复用。
- `call_app` 节点语义：config 仅 `{application_version}`；inputs 是子应用输入字段 → 绑定；节点
  output_schema 是子输出的子集。`presetexec` 的拒绝码 `tool_unsupported_graph` 是 T078 的替换点。
- 编辑器：`apps/web/src/assets/editor.js` 的 `callApp*` 纯函数（选择器模型/schema 抽取/表单→节点/
  upsert/节点高亮/prefill）是页面唯一实现；`tests/f07/test_t075_pages.py` 的 harness 可复用。

## 7. 参考

- ADR：[0023 — Fixed sub-application delegation at publication time](docs/adr/0023-f07-fixed-subapp-delegation.md)
- 规格：spec ADR 0011、`WORKFLOW-SPEC.md` §2/§3、`COMPILER-CONTRACT.md:35`、EXECUTION.md:70/72/78。
- 依赖：T068 发布器（`internal/catalog/publication.go`）、T070 版本撤销（`ListVersions`/`GetVersion`
  过滤 revoked，`POST /versions/{id}/revoke`）、T078 子运行（本票 partial 的 owner）。
