# F13A RV1 复核修复：逐例正文 owner-only + `effect_count` 只计确认副作用

- 任务：独立复核确认项 RV1（其中 Finding 1 = P1 标准+规格、Finding 2 = P1 规格），
  不是新票；不动 GitHub Issues、不 push。
- 分支：`fix/rv1-eval-integrity`（基线 `integration/f13a-f15a-f09-f10` @ `8ad3bcc`，
  worktree `E:\orchara-worktrees\rv1`）。
- 状态：**fixed** —— 两个 finding 均有真实 PG 16.4 / 编译二进制 / 真实 HTTP 的
  回归测试；`go build/vet/test`、`gofmt -l`、`sync_spec --check`、`pytest tests/f13a`
  （两次背靠背）、`pytest tests/tooling` 结果见 §4。
- 边界（如实）：未改 `contracts/**` 或 `docs/spec/**`（`DATA-POLICY.md` 是只读依据）；
  无迁移；未跑全量 `pytest tests`（BRIEF §3 明确禁止）；未 push、未动 issue。

## 0. 工作记录（简短）

1. 基线复核：`internal/catalog/evaluation_cases.go` 的 SQL 为
   `(e.owner_id = $2::uuid OR $3::boolean)`，`internal/api/evaluation_cases.go` 传
   `caller.HasRole("admin")`；`internal/execution/evaluation_runs.go` 的
   `effectCounts` 对 `platform.operations` 只按 `run_id` 分组、不筛 state。
2. 先写会失败的断言：T056/T062 中“admin 200”改为“与 missing 同码同文的 404”；
   `internal/evaluation` 增加 capability_version 归一化/未采集原因的单测；新增
   `tests/f13a/test_rv1_effect_evidence.py`（7 个案例走真实完整链路）。
3. 修复后按绿切片提交：`fix(f13a)` 两笔（Finding 1、Finding 2）、`test(f13a)` 一笔、
   `docs` 两笔（ADR/PROGRESS/报告）。
4. 合入 `integration/f13a-f15a-f09-f10` 最新 tip 后复跑门禁（见 §4）。

## 1. Finding 1（P1，标准+规格）：管理员可读他人的逐例结果正文

### 1.1 问题

`GET /api/v1/evaluation-reports/{report_id}/cases` 返回密封报告里的逐例
`expected`/`actual`/`detail`/`facts`（私有结果正文）。基线用
`(e.owner_id = $2::uuid OR $3::boolean)` 授权，API 传 `caller.HasRole("admin")`。
`docs/spec/DATA-POLICY.md` §可见性：应用作者与平台管理角色均**不因其身份自动**
取得用户结果正文查看权限；管理员的合法审计面是 T062 的 admin-only attribution
读（ADR 0014），该端点固定字段集**不含正文**。

### 1.2 修复

- `internal/catalog/evaluation_cases.go`：SQL 谓词改为 `e.owner_id = $2::uuid`；
  `EvaluationCaseResults(ctx, reportID, principalID, objects)` **删除 `admin bool` 参数**
  —— 调用方在编译期就无法传管理员标志；非 owner（含 admin）与不存在报告走同一个
  `ErrNotVisible` → 同一个 `404 resource_not_found`。
- `internal/api/evaluation_cases.go`：不再计算/传 `caller.HasRole("admin")`；
  `application:read` scope 检查保留（非 owner 仍先过 scope 再 404，见 ADR 0017）。
- 注释、ADR 0017（含 RV1 amendment）、T056 报告、PROGRESS 行同步改为 owner-only。

### 1.3 证明

| 证据 | 断言 |
|---|---|
| `tests/f13a/test_t056_sales.py::test_a_succeeded_csv_sales_case_yields_a_passed_report` | owner 读 200（正文/断言/附件/复读 byte-identical 不变）；其他用户 404；**admin 404**，且与 missing report 的 `error.code`/`error.message` 相同；无 `application:read` 的 key 403、有 scope 的非 owner 仍 404 |
| `tests/f13a/test_t062_authz.py::test_accounts_are_isolated_on_reads_and_submission` | 合同 `getEvaluation`/`getReport` 仍 owner-or-admin 200；逐例读：owner 200（空 cases）、admin 404 且与 missing 同 error code/message（不能枚举 report id） |

**对管理员的确切行为变化**：修复前 `GET /api/v1/evaluation-reports/{id}/cases`
对 admin 返回 **200 + 他人逐例正文**；修复后 admin 对该端点返回
**404 `resource_not_found`（message = "that evaluation report does not exist"，与
不存在的 report id 完全相同）**。合同评测/报告读（六/四字段）对 admin 仍是 200。

## 2. Finding 2（P1，规格）：`effect_count` 用错真值

### 2.1 问题

基线 `effectCounts`：

```sql
SELECT capability_version, count(*) FROM platform.operations
 WHERE run_id = $1::uuid GROUP BY capability_version
```

把 `prepared`/`awaiting_approval`/`authorized`/`dispatching`/`failed`/`unknown`
都当作“副作用”。零写入的 run 只要有过一次尝试/未知结果就报
`effect_count=1`，`expected=0` 会假失败；同时 capability 键映射未经验证：
case 的 `capability`（如 `test.send`）直接查 `facts.Effects`，而账本列是
`capability_version`。核对写入方：`internal/capabilities/service.go` 把
`capability_version` 小写化并要求 UUID（model profile/tool version id），
账本里永远不会有 `test.send` 这样的名字；旧实现按 key-miss 得 0，
`expected=0` 因此“看似通过”，映射从未被测试。

### 2.2 修复（`effectCounts` + 全链路 callers）

- **只计确认效果**：`state = 'succeeded'`。
- `failed`/`prepared`/`awaiting_approval`/`authorized`：贡献 0 个确认效果。
- 存在 `unknown` 或 `dispatching` 行：该 run 的效果证据**整体不采集**——
  `EffectsCollected=false`、**不返回任何数字**，并把稳定原因
  `unconfirmed_unknown` / `unconfirmed_dispatching`（两者都有时按字典序
  `+` 连接）写进 facts/断言 detail（`effect_evidence=...`）。断言因此永远失败，
  不会把未确认当 0 或把部分计数当完整计数。
- **capability 键映射显式化**：计数键 = 账本里**原样存储的** `capability_version`
  （写入方小写化；`evaluation.NormalizeCapabilityKey` 对账本键和 case 的
  `capability` 都做 trim+lowercase）。计数按 capability version 分组，**不是**
  run 总数；报告 `facts.effect_counts` 暴露同一归一化键，便于定位 key-miss。
  模块注释、facts 注释、报告 `limits[]` 都写明“case 的 capability 必须是平台记录的
  capability_version id”。
- 链路：`execution.EvaluationRuns.effectCounts` → `internal/api/evaluation_runs.go`
  内部 facts 响应 → `cmd/maintenance/scope.go` 解码 → `background.EvaluationRunFacts`
  → handler → `evaluation.Facts.EffectEvidence` → `CaseFacts.effect_evidence`
  （JSON，owner-only 逐例读可见）/断言 detail。
- `EvaluationRunFacts` 返回值补上 `EffectEvidence`（首次接线遗漏由回归测试暴露并修正）。

### 2.3 证明（全部真实 PG + 编译二进制 + 真实 maintenance 双池 + 真实 supervisor）

`tests/f13a/test_rv1_effect_evidence.py::test_effect_count_reads_confirmed_effects_from_the_real_ledger`
一个真实评测 7 个案例（`platform.operations` 行按 F06 写入方的形状写入真实账本表，
case 的 run 由服务端真实创建、真实 `cmd/supervisor exec` 执行；被测对象是采集 SQL 与
断言链路，未被 mock）：

| 场景 | 账本行 | 断言 | 结果 |
|---|---|---|---|
| succeeded 计 1 | `succeeded` | `effect_count=<version>,1` | 通过，`actual="1"`，facts `{version:1}` |
| 非派发状态计 0 | `failed`+`prepared`+`authorized`+`awaiting_approval` | `effect_count=<version>,0` | 通过，`actual="0"`，facts `{}` |
| unknown 未采集 | `unknown` | `effect_count=<version>,0` | **失败**，`actual="not_collected"`，detail 含 `effect_evidence=unconfirmed_unknown`，`facts.effect_counts=null`、`facts.effect_evidence=unconfirmed_unknown` |
| dispatching 未采集 | `dispatching` | `effect_count=<version>,0` | 同上，原因为 `unconfirmed_dispatching` |
| 空账本 expected=0 | 无 | `effect_count=<version>,0` | 通过，`actual="0"`，facts `{}` |
| 按 capability version 计数 | 另一个 version 的 `succeeded` | `effect_count=<other>,0` | 通过（不是 run 总数） |
| 混合仍不采集 | `succeeded`+`unknown` | `effect_count=<version>,1` | **失败**（不报部分计数 1） |

Go 单测：

- `internal/evaluation/assertions_test.go::TestEffectCountLooksUpTheStoredCapabilityVersionKey`
  （大小写/空白归一化；按 version 键；错值不通过）；
- `internal/evaluation/assertions_test.go::TestEffectCountUncollectedCarriesTheLedgerEvidenceReason`
  （未采集 ≠ 0，detail 带 `effect_evidence`）；
- `internal/evaluation/collect_test.go::TestUnconfirmedEffectEvidenceIsRecordedWithoutInventingACount`
  （报告 facts 记录原因、不发布计数，case 永不 passed）。

既有 `expected=0` 正面路径保持：`tests/f13a/test_t052_runner.py`、
`test_t056_*.py` 的空账本断言仍 `passed`。

## 3. 门禁命令与原始输出

环境：Windows PowerShell，`$env:PATH = "E:\orchara-toolchains\go\bin;$env:PATH"`；
PostgreSQL 16.4 用户态集群 `127.0.0.1:55432`（running）；Go 1.27.3；Python 3.12.2；
Node 24；WSL2/runsc 可用。

```text
== go build ./... ==
(no output)  exit=0

== go vet ./... ==
(no output)  exit=0

== go test ./... ==
ok  orchara/platform/internal/api         2.455s
...
ok  orchara/platform/internal/workflow    1.347s
(33 个有测试的 package 全 ok，0 FAIL；cmd/* 为 no test files)  exit=0

== gofmt -l . ==
(no output)  exit=0

== python tools/sync_spec.py --check ==
no drift: spec copy and contract snapshot match the manifest  exit=0

== python -m pytest tests/tooling -q ==
.....................                                                    [100%]
21 passed in 8.54s

== python -m pytest tests/f13a -q --basetemp=C:\orchara-pytest\rv1-run1 ==   (run A)
...................................................                      [100%]
51 passed in 666.88s (0:11:06)  exit=0

== python -m pytest tests/f13a -q --basetemp=C:\orchara-pytest\rv1-run2 ==   (run B, 背靠背)
...................................................                      [100%]
51 passed in 818.86s (0:13:38)  exit=0

== git merge integration/f13a-f15a-f09-f10 ==
integration tip 8ad3bcc -> 5f6401e（F09/T063 review fix）；本地 merge commit b6cc5af，
RV1 触及的文件无冲突（ort 自动合并，PROGRESS 双方内容保留）。

== merge result: go build ./... / go vet ./... / gofmt -l . / sync_spec --check ==
全部 exit=0（gofmt 无输出，sync_spec = no drift）
== merge result: go test ./... ==
33 行 ok（含 internal/context 0.898s、internal/memory 1.222s 等），0 FAIL，exit=0

== merge result: python -m pytest tests/f13a -q -k "t056 or t062 or rv1" --basetemp=C:\orchara-pytest\rv1-merge ==
...........................                                              [100%]
27 passed, 24 deselected in 244.92s (0:04:04)  exit=0
```

关于 `--basetemp`：本机是多 agent 共享的 12 核环境，第一次用裸
`python -m pytest tests/f13a -q` 跑到末尾时，共享的
`C:\Users\...\Temp\pytest-of-19677\pytest-24` 整个会话临时目录**被外部删除**
（同一临时根下还有其它 worktree 的并发会话），造成 1 个测试的二进制路径
`FileNotFoundError` + 6 个 `tmp_path` fixture `FileNotFoundError`：
`1 failed, 44 passed, 6 errors in 689.22s`。失败/错误均发生在 fixture/进程启动层，
没有任何断言被判定；随后两次门禁用隔离的 `--basetemp`（避免共享临时根被外部
清理）背靠背跑完整套件，结果如上。这是测试环境隔离措施，不改变产品行为或断言。

## 4. not_run / 边界

- **全量 `pytest tests`**：not_run（BRIEF §3 禁止；证据以按目录套件为准）。
- **浏览器 DOM**：not_run（本修复不改页面；逐例页面逻辑仍由既有 Node 测试覆盖）。
- **合同/规格**：未改 `contracts/**`、`docs/spec/**`；`python tools/sync_spec.py --check`
  = no drift。
- **迁移**：无（无 schema 变化）。
- **管理员归属读（T062）**：不回归测试改动；`tests/f13a/test_t062_authz.py` 全文件在
  套件内执行（含 admin-only attribution 仍 200/非 admin 403）。
- 定向套件首次运行时 `test_t062_guard.py::test_a_forged_passed_report_over_unfinished_or_cancelled_runs_is_refused`
  在 fixture 启动 API 时出现 `/health/live` 超时（负载下启动抖动，无断言执行）；
  该测试在随后完整套件中重跑（见 §3 原始输出），不是产品判决。

## 5. 下一票接口

- `evaluation.CaseFacts.effect_evidence` 是密封报告/逐例读的 additive 字段
  （`omitempty`），owner-only；后续若要给管理员做无正文的 effect 审计，
  应扩展 T062 attribution 的固定字段集，而不是放开逐例读。
- `effectCounts` 的语义现在是“确认效果”，`EffectEvidenceUnknown`/
  `EffectEvidenceDispatching` 是稳定原因码；未来若把 `operation_attempts` 纳入
  证据，应先把 `unknown` 的核对结论（reconciliation）映射成确认状态。
