# F13B 复核修复（2026-11-07，review range `30642be..edc59b4`）

复核报告提出 **5 项 P2**（Standards 2、Spec 3）。本文件逐条记录：原缺陷、根因、修复、红→绿证据。
所有修改都只在 `product/tests/f13b/**`、`product/tests/support/**`、`product/tools/**`、
`product/reports/f13b/**` 与 `product/PROGRESS.md`；未改 `docs/spec/**`、`contracts/**`、迁移或产品业务语义。

环境：PostgreSQL 16.4 `127.0.0.1:55432`、Go 1.27.3、Python 3.12.2、WSL2 Ubuntu-24.04 +
runsc `release-20241118.0`、dev-stub。

## 1. Standards P2：T111 部分/失败运行仍可生成 `passed`

**原缺陷**：`tests/support/t111.py` 的 `Evidence._status` 只判断 `cases` 非空且 `blocked` 为空；
记录一条 case、甚至把它的 `verdict` 设为 `failed`，汇总仍是 `passed`。session teardown 又无条件
`write_if_enabled()`，一次只跑单个用例的运行可以把已提交的完整证据覆盖成子集。

**修复**：

- `EXPECTED_CASE_IDS` 固定 10 条验收 case；
- `_status()`：任一 case verdict ≠ passed → **failed**；blocked/缺失/多余/重复 → **partial**；
  只有完整集合且全部 passed 才是 **passed**；
- `document()` 增加 `expected_cases / missing_cases / unexpected_cases / failed_cases / complete`；
- `write_if_enabled()` 只在 `complete and status == "passed"` 时写；部分/失败运行不覆盖已提交记录；
- 新增离线测试 `tests/f13b/test_t111_evidence.py`（7 条）。

**红→绿**：复核的最小复现（1 case + verdict=failed → passed）在修复后为
`status=failed`、`failed_cases=[...]`；子集运行 `write_if_enabled()` 返回 `None` 且不创建文件。
完整 10 条运行写出的 JSON：`status=passed, complete=true, expected_cases=10, failed_cases=[],
missing_cases=[]`。

## 2. Standards P2：T107 报告生成源未脱敏执行凭据

**原缺陷**：`tests/support/t107.py` 直接把 `platform.run_attempts` 事实序列化进
`reports/f13b/T107-faults.json`，已提交报告含完整（已撤销）attempt token
`4d9dd2c6-0241-442c-af0a-5cd1852ecf89`，且 `tools/evidence_redaction.find_violations` 能检出。

**修复**：

- 写入前使用统一 `redact_value`（`*_token` 成员替换为 `<redacted>`）；
- 额外收集所有 credential-named 成员的字符串值，在序列化文本中逐字替换，覆盖 token 被嵌进
  其它字符串的情况（`run_waitings.body` 的 JSON 字符串）；
- 写入前用 `find_violations` 做最后扫描，仍有凭据形状则 **拒绝写**；
- 重新生成 `reports/f13b/T107-faults.json`：`violations=[]`、旧 token 不再出现、
  `redacted` 值保留结构；
- 新增离线测试 `tests/f13b/test_t107_evidence.py`（3 条：命名成员脱敏、嵌入 body 脱敏、
  scanner 拒绝未脱敏文档、已提交文件干净）。

**历史说明**：该 token 已随提交进入过 git 历史（与 F12 的已失效测试 key 同类）；本轮只清理当前树与生成源，未改写已推送历史。token 所属测试租约已撤销。

**红→绿**：修复前 `find_violations(committed)=1`（`credential-member`）；修复后 = `[]`，
且 `"4d9dd2c6-…" not in text`。

## 3. Spec P2：46 个冻结案例全部被判不可判定

**原缺陷**：`tools/f13b_corpus.py::is_decidable` 用 `for fact in CATEGORY_FACTS.values()`
要求**每个**案例具备全部七类事实；生成器只声明所属类别事实，导致 `is_decidable` = **0/46**，
#103 的案例永远无法判定。

**修复**：只要求该 case 自己 `category` 的 `CATEGORY_FACTS[category]`，保留 `COLLECTION_FACTS`
与 assertions 派生事实；类别非法时给 `missing_fact:category`。新增正向测试
`test_every_frozen_case_is_decidable`（46 条全部 decidable，且每条 registry 确实声明本类事实），
保留原有空/不完整案例的负向测试。

**红→绿**：修复前 `decidable 0 / 46`；修复后 **46 / 46**。

## 4. Spec P2：τ² 门禁接受实际为空的 checker

**原缺陷**：`tools/f13b_external.py::reject_reason` 只检查 `reward_basis` 非空且词表合法，
没有确认 basis 选中的评分分量有实际断言。`actions` 非空、`env_assertions=[]`、
`reward_basis=["ENV_ASSERTION"]` 通过门禁，但固定上游评估器对空 `env_assertions`
从 1.0 起乘，永远给 reward=1.0。

**修复**：按每个 basis 对应的实际检查计数（`DB`/`ACTION`→actions、`ENV_ASSERTION`→env_assertions、
`NL_ASSERTION`→nl_assertions、`COMMUNICATE`→communicate_info）判断：**所有被声明的分量都没有
检查**时拒绝（稳定码 `reward_basis_without_assertions`）。

这里采用「全部被声明分量都为空」而不是「任一分量为空」，因为真实上游任务 39 的
`reward_basis=["DB","COMMUNICATE"]` 本身带空的 `communicate_info`（11 条 actions 的 DB 分量是
实质的）。若按「任一为空」拒绝，会把已提交的 3 个真实改编任务全部判非法；而复核指出的
「只有 ENV_ASSERTION 且 env_assertions 为空」仍被拒绝。

**红→绿**：修复前复核用例 `reject_reason` 不含该码；修复后 = `['reward_basis_without_assertions']`。
同时 `ext.validate_external(ext.load_external())` 仍为 `0` 个 issue（已提交导入保持合法）。
新增负向测试 `test_reject_a_reward_basis_component_without_assertions`（含 `DB` 空 actions
与真实任务 39 的正向对照）。

## 5. Spec P2：JSON 判定器把数字误认成布尔值

**原缺陷**：`tools/f13b_external.py::evaluate_assertions` 的 `json_equals` 用 Python `==`；
`False == 0`、`True == 1`，所以把已提交 AgentDojo gold 结果的 `read` 布尔值改成数字后，
`utility` 与 `security_expectation_met` 仍为 true，与产品 Go JSON checker 的类型语义不一致。

**修复**：新增 `json_equal` 递归比较：bool 只与 bool 相等、`null` 只与 `null` 相等、
数字按值比较（JSON 单一 number 类型）、字符串/数组（逐位）/对象（键集合 + 逐键）分别比较；
`json_equals` 改用它。新增单测（`json_equal` 类型矩阵）与端到端回归
`test_a_number_cannot_stand_in_for_a_boolean_in_the_gold_verdict`：把 gold `email-2.read=false`
改成 `0` 后，`utility=False`、`security_expectation_met=False`。

**红→绿**：修复前 `json_equal(False, 0)` 为 `True`；修复后 `False`。
`json_equal({"a":[True]}, {"a":[1]})` = `False`；数字 `1` 与 `1.0` 仍相等。

## 验证（本机实测）

| 命令 | 结果 |
|---|---|
| `pytest tests/f13b/test_t102_corpus.py tests/f13b/test_t104_tau2_import.py tests/f13b/test_t105_agentdojo_import.py tests/f13b/test_t107_evidence.py tests/f13b/test_t111_evidence.py -q` | **186 passed in 9.48s** |
| `F13B_WRITE_EVIDENCE=1 pytest tests/f13b/test_t111_linux_isolation.py tests/f13b/test_t111_evidence.py -q` | **17 passed in 108.22s**（证据重生成：`status=passed, complete=true`） |
| `F13B_WRITE_EVIDENCE=1 pytest tests/f13b/test_t107_lifecycle_faults.py tests/f13b/test_t107_evidence.py -q` | **7 passed in 57.86s**（证据重生成，`find_violations=[]`、旧 token 不再出现） |
| `pytest tests/f13b -q` | **233 passed in 415.76s**（修复前 219；新增 14 条） |
| `pytest tests/f12 -q` | **447 passed in 273.73s** |
| `go build ./...` / `go vet ./...` / `go test ./internal/capabilities ./internal/evaluation` | 0 / 0 / 均 ok |
| `gofmt -l internal cmd tools` | 空 |
| `python tools/sync_spec.py --check` | `no drift` |

**未改**：`docs/spec/**`、`contracts/**`、迁移、产品 auth/审批/沙箱语义；`tests/f13a` 的 3 条
基线既有红不在本次修复范围（与本轮 5 项无关）。
