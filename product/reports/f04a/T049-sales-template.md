# T049 / GitHub #50 — F04A sales template: another user runs CSV/XLSX and downloads a verified report

状态：**passed**（真实栈端到端；金额真值由独立 Decimal checker 复核）
分支：`t/t049-sales-template`（worktree `E:\Orchara-wt\t049`），base `e84ff51`（T048 merged）。
日期：2026-11-04。环境：Windows 11 + WSL2/runsc（Ubuntu-24.04），PostgreSQL 17 @
`127.0.0.1:55432`，Go 工具链 `E:\orchara-toolchains\go`，P01 locked Python 3.12。
提交：`533546b`（注册表/seed/实例化/定义/stub 报告/页面 helper）、本证据提交（验收测试、
页面测试、报告、PROGRESS、ADR D16）。

## 1. 交付范围

- 服务端控制的**销售模板注册表**：`platform.application_templates`（迁移 `0030`），固定模板 id
  `sales`，保存源应用 + revision-1 草稿的 `definition` 与 `capability_card`。
- `orchara-admin seed-sales-template --owner-login ADMIN --model-version-id UUID`：
  - 只接受真实 T039 register/validate 流程已经 **passed** 的 stub 模型；pending/failed/revoked/
    非 stub 一律拒绝，命令从不写 `validation_state`（不伪造验证）。
  - 幂等确保固定预设工具版本 `11111111-1111-4111-8111-111111111111`（`sales_stats`，state ready，
    `risk_class=read`，有 `approved_by/approved_at`，`image_digest` = 真实
    `worker/platform_worker/nodes/sales_stats.py` 的 SHA-256）。
  - 定义里是**实际** UUID：`stats.config.tool_version = 1111…`、
    `explain.config.model_profile_version = <passed 模型 id>`；不存在 `2222…` 占位。
  - 重复运行输出相同 `{template_id, application_id, tool_version_id, model_version_id}`，
    模板/工具行各一条。
- 非契约 `POST /api/v1/templates/{template_id}/applications`（creator + CSRF）：把模板定义与
  能力卡复制成调用者拥有的新应用 + revision-1 草稿，返回 `{id, revision, definition_revision}`；
  未知/畸形模板 404 `template_not_found`，非 creator 403 `creator_role_required`，未认证 401。
  这是“另一个使用者运行模板”的桥，不是发布。
- 固定四节点定义 `start → stats(python) → explain(agent) → end`；CSV 与 XLSX 等价；python 产出
  `summary.json` / `summary.csv`，agent 只写报告正文，end 汇总三者。
- `/apps` 页面新增“从销售模板创建（F04A/T049）”动作；纯 helper
  `salesTemplateInstantiateRequest()` 由 Node 执行验证请求形状。

## 2. T050 可用的精确接口

### 2.1 模板 seed（CLI，内部/运维入口）

```text
orchara-admin seed-sales-template --owner-login <ADMIN_LOGIN> --model-version-id <UUID>
```

- 环境：`ORCHARA_PROFILE` + `ORCHARA_DB_DSN`（与其它 admin 命令相同）；脚本路径可用
  `ORCHARA_PRODUCT_ROOT` 或 `--tool-script <path>` 覆盖。
- 成功：exit 0，stdout 恰好一行 JSON：
  `{"template_id":"sales","application_id":"<uuid>","tool_version_id":"11111111-1111-4111-8111-111111111111","model_version_id":"<uuid>"}`。
- 失败：exit 1（数据库/语义拒绝）或 exit 2（参数形状），stderr 含原因；不写任何行。

### 2.2 模板（数据行）

```sql
platform.application_templates(
  id text PK,                         -- 'sales'
  name text,                          -- '销售统计模板'
  application_id uuid UNIQUE,         -- 源应用（admin 所有，revision-1 草稿）
  definition jsonb, capability_card jsonb,
  created_at timestamptz, updated_at timestamptz
)
```

### 2.3 实例化（HTTP，非契约）

```text
POST /api/v1/templates/sales/applications
Cookie: <session>   X-CSRF-Token: <token>   Body: {}
-> 201 {"id":"<new app uuid>","revision":1,"definition_revision":1}
-> 404 {"error":{"code":"template_not_found", ...}}         # 未知/畸形模板
-> 403 {"error":{"code":"creator_role_required", ...}}      # 非 creator
-> 401                                                       # 未认证
```

### 2.4 运行（既有路径，不变）

1. `POST /api/v1/artifacts/uploads` → PUT 签名链接 → `POST .../complete` → finalize → ready。
2. `POST /api/v1/applications/{id}/debug-runs`，header `Idempotency-Key`，body：

```json
{"draft_revision":1,"input":{"file":"sales.csv","media_type":"text/csv"},
 "artifact_ids":["<uploaded artifact id>"]}
```

   XLSX 时 `media_type` 为
   `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`。
3. `cmd/supervisor exec --once`（真实 mTLS、真实 WSL2/runsc）。
4. owner 读取：`GET /api/v1/runs/{id}/node-results`、`GET /api/v1/artifacts/{id}/content|download`。
   - `stats` 节点：`data`（`{total,regions,order_count}`）、`file:summary.json`、
     `file:summary.csv`、`log`。
   - `explain` 节点：`data`（`{"report","totals"}`）、`file:context-trace`；无工具调用。
   - end 输出 artifact：`{"summary","report","totals"}`。
   - 第三用户对该 run / node-results / artifact 全部 404（owner-only）。

## 3. 金额真值证据（独立 Decimal checker，测试侧）

公开样例（`WORKFLOW-SPEC §6`，字面量，独立于工具）：

```text
A1 2026-01-01 东区 120.50
A2 2026-01-02 东区  79.50
A3 2026-01-03 西区  50.00
=> total 250.00, 东区 200.00, 西区 50.00, order_count 3
```

实际运行输出（CSV 与 XLSX 完全一致）：

```text
summary.json = {"total":"250.00","regions":{"东区":"200.00","西区":"50.00"},"order_count":3}
summary.csv  = "region,total\n东区,200.00\n西区,50.00\n"
explain.data = {"report":"销售报告：总额 250.00，订单数 3，区域：东区 200.00，西区 50.00。以上数字全部来自已验证统计，报告未重新计算任何金额。",
                "totals":{"total":"250.00","regions":{"东区":"200.00","西区":"50.00"},"order_count":3}}
end.output   = {"summary":<summary.json>,"report":<explain report>,"totals":<summary>}
```

Checker（`tests/support/t049.py`）不导入工具：用 `decimal.Decimal` 从字面量重算 250/200/50/3，
逐项校验 summary.json、summary.csv、report 中的每个两位小数（必须 ∈ {250.00,200.00,50.00} 且
三者齐全）、订单数 3 与 totals == summary；并证明非空转：把 total 改为 `251.00`、把 report 的
`250.00` 改为 `999.00` 都必须抛 `CheckerError`。

`summary.json`、`summary.csv`、`stats.data`、`explain.data`、`end.output` 五个 artifact id
互不相同：报告正文不覆盖 summary 附件。

拒绝输入（真实 run 路径，工具自身拒绝）：重复 `order_id` → run `error_code=duplicate_order_id`；
负数金额 → `negative_amount`；只有表头 → `empty_input`；三种情况 run 均 `failed`、无
`summary.json`/`summary.csv`、无 output artifact（不产出可下载的报告）。

## 4. Stub / real 分报

| 项 | 实际 |
|---|---|
| 模型 provider | **stub**（`provider=stub`，T039 真实 register/validate 流程 passed；每次 explain 一次模型轮次，账本 `capability_version` = 该 profile id） |
| python 工具 | **real**：预设 `sales_stats`，真实 WSL2/runsc 沙箱执行，真实 CSV/XLSX 字节 |
| agent 节点 | **real** 执行器 + **stub** 模型（无工具调用，`max_turns=4`） |
| PostgreSQL | 真实 0001–0030 迁移链 |
| HTTP/mTLS/对象字节 | 真实 `cmd/api`、真实 supervisor/worker 证书、真实文件对象存储 |
| 报告正文生成 | stub 从**已验证 summary**逐字复制生成；不重算金额（这是模板契约，不是真实厂商模型） |
| 真实厂商 adapter | **not_run**（本批无真实模型/连接器 adapter） |

## 5. 测试清单

`tests/f06/test_t049_template.py`（3 用例，真实栈）：

1. `test_seed_registry_and_instantiate_boundaries` — pending 模型被 seed 拒绝；seed 幂等；
   工具行 ready/read/approved/image_digest=脚本 SHA-256；定义实际 UUID、无占位；源草稿 revision 1；
   creator 实例化后的副本定义/卡逐字相同；未知模板 404；非 creator 403；未认证 401；
   实例化不创建 version/review。
2. `test_csv_and_xlsx_runs_produce_the_verified_report` — CSV 与 XLSX 各一次真实 run：
   250/200/50/3、summary/CSV/report/end 分离、独立 Decimal checker、解释报告不编造金额、
   非空转扰动检查、四节点时间线、账本 1 次模型轮次且 profile id 正确、owner 下载、第三用户隔离。
3. `test_invalid_sales_inputs_are_rejected_by_the_preset_tool` — 重复/负数/空输入三种真实 run，
   工具拒绝码、run failed、无 summary/无 output。

`tests/f06/test_t049_pages.py`（4 用例）：`/apps` 模板动作与发布边界文案、debug 页面可服务、
`app.js` 纯 helper 在 Node 下执行（路径编码/方法/空体/空 id 拒绝）、`/runs` 与 `runs.js` 的
report 下载路径与 stub 标签。

## 6. 门禁（原始 tail，按序串行执行）

```text
$ E:\orchara-toolchains\go\bin\go.exe vet ./...
(no output; exit 0)

$ E:\orchara-toolchains\go\bin\go.exe test ./...
ok   orchara/platform/internal/modelproxy     1.593s
ok   orchara/platform/internal/templates       0.723s
...
ok   orchara/platform/internal/workflow      1.850s
# 28 ok / 0 FAIL / 10 no-test（38 个包）

$ E:\orchara-toolchains\go\bin\gofmt.exe -l internal cmd tools
(empty)

$ python -m pytest tests/f06 -q
FAILED tests/f06/test_t043_agent_node.py::test_agent_final_output_schema_failure_is_located_and_never_succeeds
FAILED tests/f06/test_t043_agent_node.py::test_agent_cancel_mid_loop_stops_without_success
2 failed, 131 passed in 745.56s (0:12:25)
# 两条均为既有 WSL2/runsc 抖动（"the attempt lease was lost while the sandbox ran"），
# 与 T049 代码路径无关；单独重跑这两条：
$ python -m pytest tests/f06/test_t043_agent_node.py::test_agent_final_output_schema_failure_is_located_and_never_succeeds tests/f06/test_t043_agent_node.py::test_agent_cancel_mid_loop_stops_without_success -q
2 passed in 44.23s
# 有效结果：133/133 通过（131 + 重跑 2）

$ $env:ORCHARA_P01_PYTHON='E:\Orchara\product\probes\agent_min\.venv\Scripts\python.exe'
$ python -m pytest tests/f04a -q
36 passed in 68.47s (0:01:08)

$ python -m pytest tests/tooling -q
21 passed in 5.84s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
(exit 0)
```

环境说明：当前 worktree 没有 `probes/agent_min/.venv`（该 venv 是主检出中的未跟踪 P01
locked 解释器），因此 f04a 通过 `ORCHARA_P01_PYTHON` 指向 `E:\Orchara` 主检出的同一 P01
venv；主检出保持只读，未被修改。

T049 新增用例：`tests/f06/test_t049_template.py` 3 passed、`tests/f06/test_t049_pages.py`
4 passed。

## 7. 偏差与边界

1. **迁移编号**：brief 写“0028（或下一个空位）”；0028 已被 T046 的 usage ledger 占用，实际新增
   `0030_application_templates.sql`，0001–0029 未改。`contracts/database.sql` 无此表：这是记录在案
   的实现偏差（迁移头注释、本报告、ADR 0007 D16）。
2. **F02B 发布边界**：模板实例化只创建调用者私有的应用 + 草稿，随后走既有 debug 路径；不创建
   `application_versions`/`publication_reviews`，也不声称共享/已发布。F02B 若要发布模板内容，必须
   新增它自己的发布事实与审核门。
3. **模板源草稿可被 seed 重写**：它保持在 revision 1，是服务端受控种子而非编辑器保存的草稿；
   用户副本在实例化时复制同一 JSON，不受后续 seed 影响。
4. **agent 无工具**：模板 explain 节点不声明 tools，避免额外的模型/工具轮次；每个 run 恰好一次
   模型调用（账本断言）。
5. **UI**：`/apps` 动作与 helper 由 Node 执行；真实浏览器 DOM 驱动为 `not_run`（沿用既有边界）。

## 8. not_run

- 真实厂商模型/连接器 adapter（本批只有 dev stub）。
- 浏览器 DOM 驱动（无 Playwright 等驱动）。
- 全量 `pytest tests`、f00b/f03/f05 套件（按任务约束不跑）。
- F02B 发布/共享版本路径（本票明确不做）。
- 跨节点真实模型流式（T044 边界，与本票无关）。
