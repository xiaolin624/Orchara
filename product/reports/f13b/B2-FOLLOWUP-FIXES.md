# B2 后续审查修复（2026-11-08）

范围：审查 `origin/main...integration/t113-t114-t122`（`a3e5045...2c70e55`）的五项发现。
本次修复以 `2c70e55` 为父提交，本记录随修复一同提交。没有改生成契约、迁移或 `docs/spec/**`。

## 行为修复

1. **SSE 单行上限**：`internal/modelproxy/openai.go` 以 `ReadSlice` 分段累计字节，
   实际执行单行 1 MiB / 累计 8 MiB 限制。原 `ReadString` 会跨缓冲区累积，
   因此 reader buffer 大小并不构成行长限制。
2. **SSE 错误语义**：解析 error 帧的 type/code/status，不保留或返回供应商原文。
   权限、模型不存在、Schema/请求拒绝使用原稳定码且不重试；瞬态错误仍可有界重试。
   半成品仍不进入结果或回执。
3. **真实模型兼容门禁**：单项 JSON 对象响应不再代表完整通过。每项独立物理调用：
   - 结构化输出：固定 `{"ok":true}` 与可读 usage；
   - 工具调用：强制 `orchara_probe`，核对 id、函数名、`{"value":"ok"}` 及 usage；
   - 流式：实际 delta、完整结束和 usage；
   - 取消：实际 delta 后取消 Go 传输；未观察到的用量明确为未知。
   目的地/凭据门禁在每项调用之前执行。每项均有独立 logical key、账本行和封存结果。
   completion guard 从模型登记行识别真实 provider，复核四项不同的调用/制品，
   重读结果并核对摘要、判定、provider、owner、candidate、job。
   缺失、重复、伪造、任何一项失败均不得获得总体通过。

取消探针不证明供应商停止执行/计费；中断时 `usage_observed=false`，
账本 tokens/charged_amount 留 NULL，预留金额保留。
旧真实模型的单项报告不是完整验证证据，使用过该路径的部署需重新登记并运行新验证。
stub 的原单项模拟验证保持原有行为及标签。

## 证据与文档修复

- **容量画像**：T114 原始三轮测量建立的是 100 用户/1000 应用草稿，负载调试同一草稿；
  child version 是封存 fixture。没有独立证明 `EVALUATION.md` 要求的 1000 已发布应用。
  报告及 JSON 总体状态更正为 `partial`，发布画像为 `not_run`；没有重写任何原始样本。
  `profile_db_truth` 补实际 active version / approved review / ready manifest 计数，
  完整证据检查要求 profile 及各轮都证明 1000 已发布应用。缺失不能再被计为通过。
  当前 seeder 仍是草稿画像；full 模式在每轮 15 分钟测量开始前即拒绝该不合格画像，
  不浪费长测窗口，也不能解除此门禁。
- **capacity-stub 边界**：修正“profile 强制拒绝真实 candidate”的错误描述。
  本次历史测量没有配置真实端点；配置完整且经过 catalog 门禁时该 profile 可以调真实模型。
- ADR 0035 增日期化修订；历史测试次数/测量数字保留，并明确其适用树和范围。

## 验证

cwd：`E:\Orchara\product`。原始日志：`E:\Orchara\.scratch\code-review-b2\fixes\`。

- 修复前：`go test ./internal/modelproxy -run 'TestSSELineLimit|TestStreamErrorFrames'` 为红；
  超限行被接受，权限/Schema/模型不存在帧被错误标为 transient。
- 修复前：`go test ./internal/modelproxy -run TestCompatibilityCannot` 为红，
  缺工具的 JSON 对象被错误视作通过。
- 修复前：容量 `draft_only` 回归为红，三轮草稿画像被汇总为 passed。
- 修复后：上述 Go 回归通过；新增四项协议探针/每项单请求测试，以及探针缺失/重复/失败矩阵通过。
- `go test ./...` → exit 0；`go vet ./...` → exit 0；
  `python tools/sync_spec.py --check` → no drift。
- `pytest tests/f06/test_t122_real_provider.py -q` → **17 passed in 100.09s**：
  正常注册使用四次独立 HTTP/账本/封存结果；缺工具或流式能力不能通过；
  权限/Schema SSE 错误各一次物理请求、无成功回执。
- 容量纯证据检查 `pytest tests/f13b/test_t114_capacity.py -k 'not harness_short and not full_three' -q`
  → **3 passed, 2 deselected in 0.48s**；包含逐轮发布数量缺失/999/1000 的汇总判定。
  历史 JSON 对比仅 status/scope_assessment 改变，原始测量字段全部不变。
- 完整 `pytest tests/f06 -q --tb=short` → **190 passed in 909.84s**；
  原始日志 `f06-regression.log`。其中原有 stub、Agent、流式、备用、预算和连接器回归保持通过。
- 最终工作区（取消探针账本未知值留 NULL 的调整之后）：
  `pytest tests/f06/test_t122_real_provider.py tests/f13b/test_t114_capacity.py tests/f13b/test_t114_cancel_exit.py -q --tb=short`
  → **22 passed, 1 skipped in 251.91s**；日志 `final-targeted.log`。
  其中 T122 17 项、容量短测/证据 4 项、取消回归 1 项全部通过。
  唯一 skip 是未启用的 15 分钟 × 3 容量实验，不是完整发布画像验收通过。
  账本断言确认取消探针 tokens/费用为 NULL 且 reserved_amount > 0。
- 最终 Go `build ./...` / `vet ./...` / `test ./...` exit 0 / 0 / 0；
  `go-final.log` 保存测试结果；`gofmt -l internal cmd tools` 空。
  `git -c core.whitespace=cr-at-eol diff --check` 通过（原容量 JSON 保持既有 CRLF）。

真实供应商验收仍为 blocked；1000 已发布应用画像及修复后 15 分钟 × 3 容量验收为 not_run。
本次修复不把上述缺口改写成已完成。
