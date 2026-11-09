# C1–C6 两轴复核修复与最终集成

日期：2026-11-09。复核固定点：`e27c6fb`（handoff）；复核 diff：`e27c6fb...HEAD`（187 commits）。

## 合并

- Code-review 修复分支：`t/c1c6-review-fixes-2`，提交 `daceb9f`，合并 `dfdab53`
  （`merge(review): remove page run-state copies, strengthen C4 drain, scope status`）。
- 修复后最终集成 tip：`dfdab53`（本记录提交后为新的 docs tip）。

## 两轴处理

- **C6 规格缺口**：删除 `runs.js`/`debug.js`/`chat.js` 剩余的页内 Run 终态/轮询副本，
  页面统一调用 `OrcharaRunState`；C6.04 仓库级静态合同扩展到这些定义，16 项合同测试
  （含 9 个真实 Chrome case）通过；C6.01–C6.03 62 passed；f12 pages/stacks 44 passed；
  f03 pages 29 passed + 1 个既有 T032 `location.search` 红（基线可复现，未修/未计通过）。
- **C4 断言**：取消测试改为确定性 Popen + runsc 双容器 `running` 轮询后再写取消；
  断言改为显式 drain 合同（两分支已启动；每个分支终态或非终态但容器/工作区确实消失；
  至少一个 failed；join/end 各恰好一次 skipped；无 join 结果；一次 attempt/exit；
  reported_result NULL；无孤儿）。`exit_evidence.started` 是 supervisor 认证时间戳，
  在取消先于 `sandbox.Start` 返回的合法交错中可为 false，因此不作为该测试断言；
  确定性终态决定由 Go seam 覆盖。C4.03 3 passed ×5；C4.02+C4.03 5 passed。
  原始失败 `REVIEW-C4-run3-red.txt` 保留为历史。
- **状态/证据口径**：`ALL.01-integration.md` 与覆盖表明确“受影响完整套件/产品集成回归”
  = tooling 148、f10 114、f11 85、f12 525、C2 19、C4 5、T113 6、Go 39 包、sync gate；
  完整 `pytest tests` 仍为 `not_run`，不称为全量。PROGRESS #157/#158 的旧“未改 PROGRESS”
  措辞已修正；#159 作用域写明本地集成验证，C1–C6 的 partial/blocked/not_run 保留。
- **安全**：最终 HEAD 已脱敏（`SECURITY-redaction-note.md`）；历史仍含本地开发口令旧提交，
  分支未 push。交付分支另提供单提交 `integration/architecture-c1-c6-clean`，
  基于 main `1be9a92`，树等于最终集成树，供 squash/单提交合并。

## 最终验证（修复合并后，integration tip）

| 命令 | 结果 |
|---|---|
| `pytest tests/f12/test_c6_04_page_contract.py tests/f04b/test_c4_02_callapp_context.py tests/f04b/test_c4_03_scheduler_regressions.py` | **21 passed in 124.39s** |
| `pytest tests/f12/test_c6_01_dependency.py test_c6_02_runstate_view.py test_c6_03_runs_recovery.py` | **62 passed in 74.28s**（修复分支） |
| `pytest tests/tooling` / f10 / f11 / f12（ALL.01） | 148 / 114 / 85 / 525 passed |
| `go test ./...` / `go build` / `go vet` / `gofmt` | 39 包 ok / exit 0 / exit 0 / 空 |
| `tools/sync_spec.py --check` | no drift, exit 0 |
| 凭据扫描 | 最终树 0 命中（仅安全说明文字提到旧模式） |
