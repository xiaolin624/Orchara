# ADR 0035 — 编译并启用 OpenAI-compatible chat-completions provider adapter

- 状态：已接受（B2 复核修复 H1；F06/T122 / GitHub #135 的既有实现，本 ADR 补记决策）
- 日期：2026-11-08
- 修订：ADR 0007（F06 模型目录/边界）中“真实厂商 adapter 仍未编译”的三处结论
- 相关：`product/reports/f06/T122-real-provider-adapter.md`；
  `product/internal/modelproxy/openai.go`；`product/internal/modelproxy/real.go`；
  `product/reports/f13b/B2-REVIEW-FIXES.md`

## 背景

ADR 0007（2026-11-03，F06/T039）在三个地方把“真实厂商 adapter 未编译”写成当时事实，
并据此规定真实 provider 一律以 `provider_not_configured` / `adapter_unavailable` 诚实失败
（ADR 0007 正文约 lines 184/247/309）。T122（GitHub #135，F06）随后编译了
`internal/modelproxy/openai.go`：**一个**通用 OpenAI-compatible chat-completions 协议 adapter，
并接入部署配置 `ORCHARA_MODEL_ENDPOINT`、deployer catalog 的目的地/凭据引用、真实 HTTP
传输、SSE 流式映射与错误分类。实现与 ADR 0007 的历史结论冲突，而 ADR 0007 没有修订指针，
T122 报告也没有声明该冲突，违反 `docs/agents/domain.md` 的“方案与 ADR 冲突必须指出对应
ADR、原因和证据”要求。

本 ADR 只补记这一决策与边界，**不重写** ADR 0007 的历史正文（保留原文，按 dated-pointer
惯例加修订指针）；也不改 `docs/spec/**` / `contracts/**` / 迁移，`sync_spec --check` 保持无漂移。

## 决策

### D1 — 本构建只编译一个厂商协议：OpenAI-compatible chat completions

- 唯一协议是 `POST <endpoint>/chat/completions`（endpoint 已以 `/chat/completions` 结尾则原样
  使用）。请求字段是闭集：`model`、`messages`、`tools`、`response_format`、`max_tokens`、
  `temperature`、`stream`/`stream_options`；响应映射到 `ProviderResponse` 的
  `text` / `tool_calls` / `structured` / `usage`，再封存为 RFC 8785 canonical 字节。
- 不引入任何厂商 SDK；不新增管理页面、契约端点或迁移。OpenAPI 的 `Error.code` 仍是自由
  字符串，但 provider 错误词表已单点化到 `internal/providercodes`（B2 复核修复 H3）。
- 只支持流式 SSE 的文本增量、按 index 缓冲的 tool-call 片段、usage-only 帧、`finish_reason`
  与 `[DONE]`；半条流、损坏帧、缺失结束标记都是失败，**绝不**把截断内容拼成答案。

### D2 — 适用范围：`provider != stub`，且仍受部署与目录门禁

adapter 只在候选行的 `provider` 不是 `stub` 时参与（stub 输出始终 `stub=true`，不因本 ADR
改变）。真实路径在**任何物理请求之前**依次校验：

1. 部署 endpoint 存在且是合法绝对 http(s) URL（无 userinfo/query/fragment）；
2. 候选的 `endpoint_ref` 被 deployer catalog 注册（旧的 `env:` 部署引用仍接受）；
3. 注册 config 声明 `protocol=chat_compatible`，否则 `adapter_unavailable`；
4. 凭据可在 Go 内解析：候选行 `secret_ref` 优先（`env:`/`file:`/catalog credential env），
   行引用不可解析时回退到部署级 `ORCHARA_MODEL_SECRET_REF`（`Config.ModelSecret`，
   B2 复核修复 S2）；两者都不可用即 `provider_not_configured`。

凭据只出现在 outbound `Authorization: Bearer` 头；不进入 Data、Output、错误串、日志或账本。
未通过门禁时**不写任何账本 attempt 行**（fail closed，不伪造成功）。

### D3 — 出错、重试与流式的边界

- 每次 `Call`/`Stream` 恰好一次物理 HTTP 请求；adapter 自身**没有**重试循环，也禁用
  Go transport 的 POST 重放（`req.GetBody=nil`）。有界重试（最多 3 次物理尝试）与
  compatible fallback（至多 1 次）只属于 capability service。
- 3xx 不跟随（`provider_redirected`）；4xx 是终态拒绝（401/403/404 有专门码）；429/408/5xx、
  网络故障与请求超时是 `TransientError`，按上述有界重试处理。
- **调用方取消/ctx deadline 不是 provider 失败**：非流式路径把 attempt/operation 以
  `caller_cancelled` 关闭（不可取消的清理上下文），不重试、不 fallback、不记
  `provider_error`、不计费；流式路径沿用既有 `stream_cancelled`（T044）。
- 响应体上限 8 MiB（流式累计、单行 1 MiB）；超限终止、不截断解析。流在结束标记前 EOF 是
  transient 失败且不返回半成品。

### D4 — 与 ADR 0007 的关系（修订而非取代）

- ADR 0007 中“真实厂商 adapter 仍未编译 / 真实 provider 以 `adapter_unavailable` 诚实失败”
  的**事实性结论**被本 ADR 修订：adapter 已编译、可用（在 D2 门禁通过时）。
- ADR 0007 的其余决策**继续有效**：目录预登记、manifest 不可变、公开投影不含 `secret_ref`、
  兼容报告绑定、`validation_state=passed` 且未撤销才可被共享应用引用、stub/real 分栏、
  `provider_not_configured` 的 fail-closed 语义。
- ADR 0007 正文不改写；在其三处原句旁加“Amended by ADR 0035”指针。

### D5 — 真实厂商端到端仍为 blocked

本机没有真实厂商 endpoint / 凭据 / 配额 / 外发批准，因此：
`blocked`——真实厂商模型的真实网络 e2e、真实延迟、成本、限流与供应商侧错误语义未验收。
已验收的是协议 adapter 本身：Go 单测（本地 `httptest` fake provider）、真实栈 e2e（真实 mTLS
worker、真实 PostgreSQL 账本、真实封存字节、loopback fake provider），以及取消/超时/超大/
错误状态/无隐藏重试/凭据不外泄等边界。fake provider 的通过**不**等于真实厂商通过。

## 后果

- 正面：真实模型路径从“永远不可用”变为“部署配置正确时真的可用”，且只有一种协议要维护；
  T122 的六个 AC 都有“缺失即失败”的测试；真实厂商缺口被明确记录为 `blocked` 而不是伪装。
- 代价：新增协议即新增一版 adapter；provider 错误词表成为跨 seam 的共享词汇
  （`internal/providercodes`），新增错误码必须同时更新映射与漂移测试。
- 不做：不引入第二家厂商协议、不新增 provider SDK、不改 spec/contracts/迁移。
