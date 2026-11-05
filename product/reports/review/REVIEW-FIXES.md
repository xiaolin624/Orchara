# 集成评审修复报告 — `integration/t068-t082`（T068–T082 / GitHub #69–#83）

- 任务：修复 `.scratch/scope-69-83/review/REPORT.md` 的 **3 个 P2 + 8 个 P3** 全部发现。
- 分支：`t/review-fixes`（worktree `E:\orchara-worktrees\reviewfix`），基线 **`d94af24`**
  （= 评审后 `integration/t068-t082` 的 tip；评审报告基于 `69154c5`，其后只有 T082 合并记录）。
- 状态：**passed**（11/11 fixed；每条行为修复都有回归测试；两条并发/鉴权关键用例做了
  红→绿验证，见 §2）。
- 真实依赖：共享主机上的真实 PostgreSQL **16.4**、现场编译 `cmd/api`/`cmd/maintenance`/…、
  真实 HTTP+CSRF、真实 WSL2/runsc（genuine-runner e2e）、Node 24。无 mock、无 skip 冒充通过。

## 1. 逐条映射

| # | 级别 | 修复（一句话） | 主要文件 | 回归测试 |
|---|---|---|---|---|
| 1 | P2-1 | 审批事务的冻结依赖复查对引用的子版本行取 `FOR KEY SHARE`；并发 revoke 只能“先提交”或“等审批提交后” | `internal/catalog/{manifest,publication}.go` | `tests/f02b/test_t068_review.py::test_a_concurrent_revoke_cannot_slip_past_the_approval_dependency_lock` |
| 2 | P2-2 | 只有 pre-flight “无 WSL2/runsc” 可 skip；产品记录的 `run_timeout` 在重试预算耗尽后 **AssertionError**，绝不 skip | `tests/support/t068.py`、`tests/f02b/test_t068_e2e.py` | `test_the_runner_classifier_never_skips_a_product_recorded_timeout`（分类器单测）+ 真实 e2e 只捕获 pre-flight 异常 |
| 3 | P2-3 | T075 row 改 `partial`、ADR 链接 0023→**0024**、`not_run` 只留 F04A 画布 palette；row 与 section 一致 | `PROGRESS.md` | 文档（row/section 两处一致，见 §3） |
| 4 | P2-4 | T073 row 改 `partial` 并写明“本构建无 tracer/metrics SDK、无 owning 票、结构性保证见 ADR 0028 §6” | `PROGRESS.md`、`reports/f11/T073-diagnostic-grants.md` | 文档（row + 报告头 + §4 三处一致） |
| 5 | P3-1 | `modelCallCount` 失败记录为 **failed/copy_vendor_count_failed**（scope 保留、job failed），不再 `absent` | `internal/deletion/copies.go` | Go `copies_test.go::TestVendorCopyCheckDistinguishesAFailedCountFromAnAbsentPath` + `tests/f11/test_t072_copy_cleanup.py::test_a_failed_model_call_count_is_recorded_as_a_failed_check` |
| 6 | P3-2 | fan-out 的 BeginTx/audit/UPDATE/Commit 失败全部返回；`Delete` 用第二次写把 `run_cancel_incomplete` 落到 job，仍失败则把错误返回 | `internal/deletion/deletion.go` | `tests/f11/test_t069_runs.py::test_a_failed_cancellation_fanout_is_recorded_instead_of_swallowed` |
| 7 | P3-3 | `verifyHandoffScope` 先算 allowed 集合（只需 id）再读 artifact 元数据 | `internal/requests/handoff.go` | `tests/f08/test_t082_handover.py::test_a_confirmed_server_preview_is_required_for_the_switch`（未知 id 断言 located 422 `artifact_not_found`，不是 500） |
| 8 | P3-8 | 0050 头注释 ADR 0025 → **0029** | `db/migrations/0050_router_interactions.sql` | 迁移链/注释（f02b+f08+f11 migration 用例在整包跑中复跑） |
| 9 | P3-9 | 抽出 `catalogSearchPageSQL`：rank 表达式 + keyset cursor + ORDER BY/LIMIT 一份；两路只剩权限谓词和文本参数（`$2`/`$3`）不同 | `internal/catalog/index.go` | Go `catalog_page_test.go::TestCatalogSearchPageSQLHasOnePaginationShape` + `tests/f08/test_t077_search.py` |
| 10 | P3-10 | t025 注释 0042 → **0043**（`application_versions_revoke_shape` 的真实来源） | `tests/support/t025.py` | 注释修复（该 helper 被 f02a/f07/t070 复用，整包跑复验） |
| 11 | P3-11 | 0048 补标准零授权块（`REVOKE ALL ON SCHEMA platform FROM PUBLIC` + `orchara_worker` DO 块） | `db/migrations/0048_diagnostic_grants.sql` | `tests/f11/test_t073_migration.py`（worker 无权限 + 重复 migrate no-op） |
| 12 | P3-12 | 正文门按 ADR 0028 §3 复核 **live admin role**；被降权 grantee 403 `diagnostic_grantee_not_admin`，拒绝写审计 | `internal/diagnostics/{content,grants}.go`、`internal/api/diagnostics.go`、ADR 0028、`tests/f11/test_t073_content.py` | `test_a_demoted_grantee_is_refused_at_the_content_door` |

## 2. 关键判定的线性化与红→绿证据

### P2-1 线性化（写死并断言）

选定并断言：**revoke 先提交 → 审批失败（409 `dependency_unavailable`，`dependency_revoked`）**。
测试用测试库内的 trigger 把真实 revoke 停在它自己的 `version.revoked` 审计写入处（advisory
lock），此时 revoke 已持有子版本行 `FOR UPDATE`、尚未提交；随后真实审批请求到达复查并被
`FOR KEY SHARE` 挡住；放行后 revoke 先提交，审批复查重读已提交行并失败。无版本/依赖边被写出。
反方向（审批先拿到行锁）也是线性化允许的：revoke 会等到审批提交后才执行，`spawnChild` 的
`ResolveRunnableVersionTx` 仍拒绝已撤销子版本（defence in depth，T078 已覆盖）。

红→绿原始证据（临时把复查改成不加锁）：

```
E   AssertionError: {"id":"9a43...","state":"approved","version_id":"7781..."}
E   assert 200 == 409
FAILED tests/f02b/test_t068_review.py::test_a_concurrent_revoke_cannot_slip_past_the_approval_dependency_lock
```

恢复锁后两次运行：`1 passed in 18.04s` / `1 passed in 18.40s`。

### P2-2 skip/fail 语义

- **只有** pre-flight `genuine_sandbox_present()`（`wsl` 存在且 guest 里 `runsc --version` 成功）
  为假才 `SandboxEnvironmentUnavailable` → `pytest.skip`。
- 环境存在而报告非 `passed`（例如 case `run_timeout`）→ 记 product failure；重试仅用于吸收共享
  沙箱争用，**预算耗尽后 `AssertionError`（fail），永不 skip**。这符合评审的修复方向
  （“assert pytest.fail on run_timeout **after the retries**”）。
- 实测：本轮第一次 `pytest tests/f02b` 就出现一次真实
  `sales-happy-csv=run_timeout`（其余 4 例 passed），新语义把它记成失败；重跑该套件
  `20 passed`。这是修复后的正确行为，不是放宽断言。

### P3-12 红→绿

门禁临时关闭时同一用例返回 **200 + 真实封存正文**（测试失败在 `expected 403, got 200`）；
恢复后两次 `1 passed`。说明该检查确实在鉴权后、取正文前起作用。

## 3. 文档一致性（P2-3 / P2-4）

- T075：`PROGRESS.md` row 与 `## F07 固定子应用依赖` 段现在都写 **`partial`**；ADR 链接为
  `docs/adr/0024-f07-fixed-subapp-delegation.md`；唯一 `not_run` 项是 **F04A 画布 `call_app`
  palette**（票面允许编辑面板回退）；真实发布事务/固定引用写明由 **T078 #79**
  `test_real_publication_pins_the_child_and_closes_t075` 闭环，不再留在 not_run。
- T073：`PROGRESS.md` row 与 `reports/f11/T073-diagnostic-grants.md` 的报告头/§4 现在都写
  **`partial`**，边界为 AC4 的 trace/OTel：本构建无 tracer/metrics SDK、无 owning 后续票，
  结构性保证（十字段审计白名单、请求日志无 query、真实日志/审计哨兵扫描）见 ADR 0028 §6。

### 如实说明（不夸大）

- **P3-3**：提交路径上层 T074 的 `checkArtifacts` 本来就会给未知/外来 id 一个 located 422，
  所以评审描述的 500 在当前树经 HTTP 实际不可达；本次排序修复是深度防御（门在元数据读之前），
  新测试钉住“未知 id 不是 500”。标题里的 “preview” 实为提交时门禁 `verifyHandoffScope`。
- **P3-12**：`internal/api/diagnostics.go` 在 authorize 时已用 live session role 做过一次 admin
  检查；本次是在 service 门内再按 ADR 0028 §3 复核 live `role_bindings`（服务边界不变量），
  测试用“鉴权后、门内被停住时删除角色”的确定性交错证明它生效。
- **P3-2**：墓碑已提交，故 `Delete` 仍返回 202；只有“连可见标记都写不进去”时才把错误返回。
- 评审报告 §3 的其它 P3（5–12 之外）无遗漏；未改 `docs/spec/**` 或 `contracts/**`。

## 4. 门禁原始输出（共享主机，单进程顺序执行）

```
python -m pytest tests/tooling -q --basetemp=E:\orchara-tmp\reviewfix-tooling
21 passed in 4.72s

python -m pytest tests/t070 -q --basetemp=E:\orchara-tmp\reviewfix-t070
12 passed in 82.25s (0:01:22)

python -m pytest tests/f02b -q --basetemp=E:\orchara-tmp\reviewfix-f02b2
20 passed in 155.30s (0:02:35)

python -m pytest tests/f11 -q --basetemp=E:\orchara-tmp\reviewfix-f11
84 passed in 462.82s (0:07:42)

python -m pytest tests/f08 -q --basetemp=E:\orchara-tmp\reviewfix-f08
65 passed in 498.78s (0:08:18)

python -m pytest tests/f07 -q --basetemp=E:\orchara-tmp\reviewfix-f07
36 passed in 275.06s (0:04:35)

# 修复专项复跑（第二次，证明确定性）
pytest <P2-1 / P2-2 分类器 / P3-1 / P3-2 / P3-12 / P3-3 六个用例> -q --basetemp=...\reviewfix-specific2
6 passed in 57.85s

go build ./...        -> exit 0
go vet ./...          -> exit 0
go test ./...         -> 38 ok / 0 FAIL / exit 0 (28.2s)
gofmt -l .            -> (empty)
python tools/sync_spec.py --check -> no drift: spec copy and contract snapshot match the manifest (exit 0)
```

`git merge integration/t068-t082`：集成分支 tip 仍是基线 `d94af24`（评审后没有新提交），
合并结果为 **Already up to date**，无冲突、无新 merge commit。
