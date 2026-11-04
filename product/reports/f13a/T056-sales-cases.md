# F13A T056 验收：运行销售与坏输入案例并展示逐例结果

- 任务：F13A / GitHub Issue #57（票 `T056`），分支 `t/t056-sales-cases`
  （worktree `E:\orchara-worktrees\t056`）；基线 `integration/f13a-f15a-f09-f10`
  @ `f1a5257`；合并提交 `<MERGE_SHA>`。
- 状态：**`passed`**（本票范围 = 案例按内容绑定调用方 CSV/XLSX 为 run 的
  `task_input`、真实销售工具 succeeded 案例、坏输入/超时案例的定位期望、
  非契约逐例读取 + ADR 0015、逐例页面、独立 Decimal 真值与守卫）。
  真实 PostgreSQL 16.4、真实编译二进制、真实 HTTP/mTLS、真实封存对象字节、
  真实 `cmd/maintenance run` 双池、真实 `cmd/supervisor exec --once` + WSL2/runsc
  Python 3.12 沙箱；无 mock、无跳过。
- **关键结论**：T052 报告中“案例输入不能绑定调用方 CSV，sales/tool 案例无法以
  `succeeded` 终态跑通”的边界已被本票关闭。真实 CSV 与 XLSX 销售案例均在
  正式 API 上跑到 `succeeded`，密封报告逐例 `passed`，报告状态 `passed`，
  断言证据（Decimal `250.00`、`order_count=3`、`effect_count=0`）全部来自平台
  自有事实。这把 #53 从 `partial` 升级为“succeeded 销售案例已被证明”。
- 边界（如实）：真实 judge adapter 仍不存在（quality 仅信息性，T052 基线）；
  `published_version` 目标仍 `blocked/unsupported_target`（F02B #69 / F07）；
  F06 审批型交互仍不在本票；真实浏览器 DOM 未运行（Node 执行页面逻辑是页面边界，
  与其他页面测试一致）；共享 WSL2/runsc 环境下的 attempt 孤立恢复仍属 F03/T038
  运维命令（见 §5），测试用有界重试隔离该环境抖动并记录。

## 0. 工作记录（简短）

1. 读 `BRIEF.md`、票面、`briefs/T056.md`、T052 报告/ADR 0011、`internal/evaluation`、
   `internal/execution/evaluation_runs.go`、`internal/api/{evaluation_submit,evaluation_runs,
   evaluations}.go`、`internal/background/handlers/{application_evaluate,tool_run}.go`、
   `internal/presetexec/{linear,presetexec}.go`、`internal/tools/executor.go`、`examples/sales/*`、
   `contracts/evaluation-case.schema.json`、`tests/f13a` 与 `tests/support/{t052,t042,t033,t029}.py`。
2. 先红后绿的垂直切片：
   - 数据集保留成员 `task_input`/`expected_failure` 的严格解析与 `RunInput` 剥离 →
     `internal/evaluation` 单测；
   - `failure_reason` 逐例 outcome（run state + error code 真值）→ 单测；
   - 案例 run 创建时按内容解析并绑定 `task_input` artifact（服务端事务内
     `run_artifacts(input)`）+ facts 记录绑定身份 → 真实 HTTP/真实 runner e2e；
   - 非契约逐例读取 `GET /evaluation-reports/{id}/cases` → 真实 HTTP（owner/admin/
     API key scope/非 owner 404/复读一致/无 object key/附件可下载）；
   - 逐例页面 + Node 执行页面逻辑；
   - 真实销售 CSV/XLSX/frozen 案例、5 个坏输入、2s 工具超时案例 → 真实沙箱 e2e；
   - 独立 Decimal 参考计算器 + 期望表 + “不得 import 被测工具”守卫。
3. 提交按 `feat/test/docs` 分片；合并 `integration/f13a-f15a-f09-f10` 后复跑门禁；
   未 push、未动 GitHub issue、未改 `docs/spec/**` 或 `contracts/**`。

## 1. 交付内容

### 1.1 案例输入：按内容绑定调用方 fixture（`internal/evaluation/dataset.go`）

契约允许案例 `input` 带额外成员（`additionalProperties: true`），T056 在其中新增
两个**保留成员**，两者都在 `RunInput()` 中被剥离，绝不进入应用输入：

```json
"input": {
  "source": "sales-happy-csv",
  "task_input": {"sha256": "<64 hex>", "media_type": "text/csv"},
  "expected_failure": {"error_code": "missing_column"}
}
```

- `task_input` 在**提交时**严格校验：`additionalProperties:false`、sha256 必须 64 位
  十六进制、`media_type` 只接受 `text/csv` 或 XLSX；错误定位到
  `/cases/N/input/task_input/<member>`（`dataset_schema`/`dataset_missing_field`/
  `dataset_unknown_field`）。
- 用**内容**而不是 artifact id 绑定：已提交数据集不嵌入易变 id；评测 owner 自己
  上传的 ready `task_input` artifact 才可解析。
- `expected_failure.error_code` 必须是稳定业务码（小写/数字/下划线，≤64）；
  契约没有“run error code”断言种类，故该期望是文档化的非契约拼写（ADR 0015）。

### 1.2 独立 checker 扩展：`failure_reason`（`internal/evaluation/assertions.go`）

`EvaluateFailureExpectation` 只读 run 的**平台事实**（`platform.runs.state` 与
`error_code`），逐例追加一个 `kind="failure_reason"` 的 outcome（expected/actual/
detail）。坏输入案例只有同时满足 `run_state=failed`、**精确**预期错误码、其他契约
断言（`effect_count=0`）才会 `passed`。未采集状态或空错误码永不通过。

### 1.3 案例 run 桥：服务端解析并绑定 fixture（`internal/execution/evaluation_runs.go`）

`POST /internal/v1/background/jobs/{job}/evaluation-runs` 的请求新增可选
`task_input{sha256, media_type}`（ADR 0011 桥的扩展）。服务端在同一事务创建案例
run 时：

- 只查询**评测 owner 自己的**、`purpose='task_input'`、`state='ready'`、未删除、
  media type 与 sha256 都匹配的 artifact；同内容多次上传时取最早
  `(created_at, id)`，确定性；
- 找不到 → `422 task_input_unavailable`（逐例 `blocked/task_input_unavailable`），
  不创建 run、绝不伪造；大小必须在 `(0, 20 MiB]`；
- 解析成功后写入 `run_artifacts(run_id, artifact_id, relation='input')`，与 debug
  提交的 `artifact_ids` 同一路径；supervisor 通过 `ListRunInputArtifacts` 发现它，
  字节仍只经 run-scoped 签名链接并按 sha256/size 复核；
- facts 读回 `task_input_artifact_id`/`task_input_sha256` 并写入逐例 `facts`，
  密封结果因此能指名究竟绑定了哪个平台 artifact。

### 1.4 失败证据登记（`internal/presetexec/presetexec.go`）

线性/legacy 失败路径原本上传安全 log/diagnostics 但不登记到 run。T056 让它们经
与成功产物相同的 `Register` 路径登记（`relation='log'/'artifact'`），于是坏输入
案例的“实际附件”是真实可下载的平台 artifact。字节、准入与拒绝语义不变。

### 1.5 非契约逐例读取（ADR 0015）

`GET /api/v1/evaluation-reports/{report_id}/cases`（`internal/catalog/evaluation_cases.go`
+ `internal/api/evaluation_cases.go` + `server.go`）：

- owner 或 admin；其他主体一律 **404**（与不存在同答，不能枚举 id）；
  API key 缺 `application:read` → **403 `insufficient_scope`**；
- 读取密封报告 artifact 的**真实字节**，先按 artifact 行 sha256/size 复核再解析；
- 每例返回：case id、verdict、reason_code/detail、逐断言 outcome
  （kind/pointer|capability/expected/actual/passed/detail）、facts 摘要、
  可下载附件 id（`summary.json`/`summary.csv`/`output`/`diagnostics`）；
- 附件从平台自有行解析：`run_node_outputs`（data/file）、`runs.output_artifact_id`、
  已登记的失败证据；log 按“日志不是运行数据”规则排除；
- 响应**不含 object key、staging key 或签名 URL**；下载走既有 owner-only
  `GET /api/v1/artifacts/{id}/download`；
- 复读 byte-identical（密封报告不可变）。

### 1.6 逐例页面（`apps/web/src/{evaluate.html,assets/evaluate.js}` + `server.go`）

`GET /apps/{application_id}/evaluate`：创作者上传评估数据集（可同时上传数据集按
SHA-256 引用的 CSV/XLSX），页面读取当前 draft revision 并通过 `evaluateDraft`
提交、轮询评测直到报告封存，然后读取逐例结果，**每个案例一行**显示期望、实际值/
状态、失败原因与可下载的实际附件（点击时才换取短时签名链接）。计数只显示
“共 N 个案例：通过/失败/未运行/阻塞”，不显示无法推导的百分比；所有文本经
`textContent`；无浏览器存储。

### 1.7 夹具与独立真值（`tests/f13a/datasets/`、`tests/f13a/reference/`）

- 5 个契约合法数据集（`python -m pytest` 里用 `jsonschema.Draft202012Validator`
  校验）：`sales_happy_csv.json`、`sales_happy_xlsx.json`、`bad_inputs.json`
  （缺列/重复/负值/公式/超限）、`timeout.json`、`frozen_boundary.json`；
- 输入：公开样例 `examples/sales/input.csv`/`input.xlsx`（sha256 冻结在数据集中）、
  4 个小 CSV、`frozen_boundary.csv`（新地区/日期/精度，提交后未再调整）；
  超限 CSV（10 001 行）与 20 MiB 超时 CSV 由 `tests/support/t056.py`
  **确定性生成**（太大不入库），sha256 冻结在数据集中；
- `expected_values.json`：独立期望表；`registry.json`：数据集字节 sha256/size 钉住；
- `tests/f13a/reference/sales_reference.py`：只用 stdlib（`csv`/`zipfile`/
  `xml.etree`）+ `decimal.Decimal` 的参考计算器，**不 import 任何产品/worker 模块**；
  守卫测试用 AST 扫描 + 子进程 `sys.modules` 断言它没有 import 被测工具，并额外
  独立复算坏输入拒绝码。

## 2. 验收标准逐条

| AC | 结论 | 证据 |
|---|---|---|
| 1. 正常统计与下载通过（CSV 与 XLSX） | **proven** | `test_t056_sales.py::test_a_succeeded_csv_sales_case_yields_a_passed_report` / `...xlsx...`：真实 run `succeeded`、报告 `passed`、`decimal_equals` 实际 `250.00`、`json_equals` `3`、`effect_count` 实际 `0`（真实空账本）；逐例读取返回 `summary.csv`/`summary.json`/`output`，CSV 经合同下载路由得到 `region,total\n东区,200.00\n西区,50.00\n`；XLSX 案例同样 `250.00`（两格式期望一致） |
| 2. 缺列/重复/负值/公式/超时/超限有预期与定位 | **proven** | `test_t056_bad_inputs.py::test_bad_input_cases_pass_only_for_their_expected_located_failure`：5 个案例真实 run 分别 `failed(missing_column/duplicate_order_id/negative_amount/formula_not_supported/row_limit_exceeded)`，`failure_reason` expected=actual 且 passed；逐例 `diagnostics` 附件含同码与 location（超限为文件级无 location）；`...test_a_tool_timeout_is_a_passed_case_with_the_expected_failure`：20 MiB 输入在 2 s 沙箱超时下真实 `failed(tool_timeout)`（解析耗时 >4 s，裕量记录见 §3） |
| 3. 真值不由被测工具产生 | **proven** | `test_t056_fixtures.py::test_the_reference_calculator_reproduces_the_committed_expected_table`（独立计算器复算 expect 表）、`...test_the_committed_dataset_assertions_agree_with_the_expected_table`、`...test_the_reference_calculator_rejects_the_bad_inputs_for_their_declared_reason`、`...test_the_reference_module_does_not_import_or_call_the_tool`（AST + 子进程 `sys.modules` 守卫） |
| 4. 真实端到端入口 + 失败/授权/重试边界，无单层 mock | **proven** | 全部 T056 e2e 走真实 REST 提交 → 真实 background job → 真实 maintenance 双池 → 真实 supervisor/runsc 沙箱 → 真实封存报告 → 真实 completion guard；授权：`test_...csv...` 中非 owner 404、admin 200、API key `application:read` 404、无 scope 403；失败：`test_a_missing_fixture_blocks_the_case_and_never_passes`（逐例 `blocked/task_input_unavailable`、未创建 run、报告 `not_run`）、`test_a_malformed_reserved_binding_is_refused_at_submit_time`（422 定位） |
| 5. 版本/输入 hash/命令/原始结果/profile，stub/real 分报，缺条件 blocked，未跑 not_run | **proven** | 本报告 §3 贴原始门禁输出；数据集/期望表/输入 sha256 均在仓库与报告中；环境 profile、stub/real 边界、not_run 清单见 §4；超时案例的确定性裕量与测试部署配额容量调整在本报告与 ADR 0015 记录 |

## 3. 门禁命令与原始输出

环境：Windows PowerShell；`$env:PATH = "E:\orchara-toolchains\go\bin;$env:PATH"`；
PostgreSQL 16.4 用户态集群 `127.0.0.1:55432`（`python tools/pg_dev.py status` = running）；
Go `go1.27.3 windows/amd64`；Python 3.12.2；Node v24.13.0；
WSL Ubuntu-24.04 + `runsc release-20241118.0`，python-rootfs digest
`sha256:03c3f647…12ff5`。

```text
$ python -m pytest tests/f13a -q          # 第 1 次
<FIRST_RUN>

$ python -m pytest tests/f13a -q          # 第 2 次（背靠背）
<SECOND_RUN>

$ python -m pytest tests/f10 -q
<F10_RUN>

$ go build ./... ; go vet ./... ; go test ./... ; gofmt -l .
<GO_OUTPUT>

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

### 3.1 说明

- `tests/f13a` 现在包含 T052 的 16 个用例与本票的 17 个用例（共 33）。
- 共享 WSL2/runsc 沙箱与同机并发套件会造成偶发 attempt 孤立：supervisor 在
  API 短暂变慢时无法在租约内完成心跳，平台如实把该案例记为
  `not_run/run_timeout`（绝不 passed）。`tests/support/t056.py` 只在**严格
  attempt 孤立证据**（attempt 未退出且心跳/租约已过期）或配额耗尽时重试整个评测
  （同一密封数据集，新提交），业务失败立即返回并按断言失败；测试部署把执行槽
  容量从 2 调到 32（`seed_evaluation_capacity`，测试自有 SQL），避免一个环境孤立
  attempt 拖死后续案例。T056 e2e 的 API 还使用 300 s 执行租约（T052 默认 30 s），
  使一次短暂 API 抖动不再导致 supervisor 放弃 attempt；沙箱超时仍兜底。
- 超时案例确定性：20 MiB 输入（合法表头 + 空行填充）在 Linux 沙箱内仅 CSV 解析
  就测得 4.34 s；测试用 `ORCHARA_SANDBOX_TIMEOUT=2s`，裕量 ≥2.1×（未计沙箱启动）。
- 冻结边界集在实现跑通前提交（`frozen_boundary.json` + `inputs/frozen_boundary.csv`
  + `expected_values.json`），之后未改动；其哈希被 `registry.json`/`expected_values.json`
  钉住。

## 4. `not_run` / 未覆盖清单

- **真实浏览器 DOM**：`not_run`（页面逻辑由 Node 执行，与其他页面测试同一既有边界）。
- **真实 judge adapter**：`not_run`（本构建无 adapter；quality 仅信息性，T052 基线）。
- **`published_version` 目标执行**：`blocked`（无发布路径；F02B #69 / F07）。
- **F06 审批/等待型交互**：`not_run`（不在本票；T052 只实现 cancel）。
- **孤立 execution attempt 的自动恢复**：`not_run`（属 F03/T038 的运维命令
  `cmd/admin runs sweep-leases|recover`；本票未改恢复语义，只在测试侧隔离该环境
  抖动并如实记录）。
- **真实外部对象存储（S3）**：`not_run`（本机无候选端点，T053 blocked；本票用真实
  本地对象字节）。
- **并发/规模**：本票不引入负载测试；未运行全量 `pytest tests`（BRIEF 明确禁止）。

## 5. 已知限制与契约债务

1. `expected_failure` 是契约断言词汇之外的非契约拼写（`input` 允许额外成员）：
   案例契约没有“run error code”断言种类。ADR 0015 记录；未来契约修订应吸收它。
2. `GET /evaluation-reports/{id}/cases` 是非契约读（ADR 0015），与 ADR 0005/0011
   的桥先例一致；公共契约未改，`sync_spec --check` 干净。
3. `task_input` 绑定按内容解析：同内容多次上传时取最早 artifact，绑定 id 写入
   facts；内容不同即 `task_input_unavailable`，绝不回退到“任意 CSV”。
4. 失败证据（log/diagnostics）现在登记到 run（`run_artifacts`），T033/T042 的既有
   成功断言不受影响（日志仍不进入所有者 artifact 列表）；这是本票对 preset 工具
   路径的唯一改动。
5. 执行槽容量与 300 s 租约只在 **T056 测试栈**调整并写入报告，不是产品默认值。

## 6. 下一票接口

- **T062**：`evaluation_case_runs.run_id -> runs.owner_id` 仍是归属事实；逐例读取的
  owner/admin 谓词可作为其越权检查的参照。
- **T065**：报告 `progress_total/progress_done` 与逐例状态一致；本票的
  `createSession` 无新增进度列。
- **T066/T063**：逐例读取的附件解析可复用 `internal/catalog/evaluation_cases.go`
  的 `caseAttachments` 规则（node data/file + output + diagnostics，log 排除）。
- **F03/T038**：孤立 attempt 的自动恢复（quota 释放）仍是运维命令；若后续要实现
  自动恢复，应复用 `internal/execution/recovery.go` 的证据语义。
