# B2 两轴复核修复（Standards + Spec + T113 增量）

- **分支/worktree**：`t/review-fixes-b2` @ `E:\Orchara-wt\review-fixes`；基线 = 集成分支
  `integration/t113-t114-t122` tip `e4591d6`；结束前 merge 该集成分支为 **Already up to date**
  （无 merge commit，见 §9）。
- **范围**：`E:\Orchara\.scratch\f13b\b2\REVIEW-FIXES.md` 的全部 finding：
  H1–H3（Standards hard）、S1–S7（Spec）、T113-1..T113-7（T113 增量）、§1.3 可选 smell。
- **状态**：hard/spec/必做 T113 项全部修复并有测试或文档证据；§1.3 全部记录为 `not_run`
  （judgement call，见 §7）；真实厂商端到端仍 `blocked`（本报告未用 fake provider 冒充）。
- **环境**：Windows 11；Go 1.27.3；Python 3.12.2；PostgreSQL 16.4 隔离集群
  `127.0.0.1:55432`；profile `dev-stub` / `capacity-stub`；无真实厂商凭据/端点，无 GPU，
  无浏览器 DOM。真实栈运行按 ENV-B2 §2 占 `locks\realstack\review-fixes.owner.txt`
  （容量套件独占；其余正确性套件串行执行）。
- **原始日志**：`E:\Orchara\.scratch\f13b\review-fixes\`（`red-*.log` / `green-*.log` /
  `gate-*.log`）。

## 1. 总表

| finding | 结论 | 一句话证据 |
|---|---|---|
| H1 ADR 0007 冲突未声明 | **fixed**（文档） | 新增 `docs/adr/0035-t122-openai-compatible-adapter.md`；ADR 0007 三处原句旁加 dated 修订指针；T122 报告 + PROGRESS 引用 0035 |
| H2 非流式调用方取消落 provider_error | **fixed**（行为） | Go 表驱动红→绿 + 真实栈中止在途请求：attempt/operation `failed`+`caller_cancelled`、`settlement_state=released`、charge=0、1 次物理请求、无 receipt |
| H3 provider 错误词表分散 | **fixed**（结构） | 新叶包 `internal/providercodes`；modelproxy/capabilities/api/background 共用；3 个表驱动测试（每个错误→稳定码、jobdata JSON、后台重试表）+ 词表漂移守卫 |
| S1 T114 未断言“不丢任务” | **fixed**（gate 收紧） | `assert_trial` 断言 `submit_failures==[]`、`process_failures==[]`、`executor.failures==[]`；旧证据（2 条 executor failure）红，修复后 short profile 绿 |
| S2 `ORCHARA_MODEL_SECRET_REF` 只验证不生效 | **fixed**（行为） | `WithDeploySecret` + `resolveCredential` fallback（行内优先、部署级回退、都无→`provider_not_configured`）；Go 单测 + 真实栈两态 e2e（可解析→Bearer 部署凭据；env 未设→503 0 行） |
| S3 routing-control 提示无覆盖 | **fixed**（测试） | Go 单测断言 system/user 形状、fixture 原文字节、model id、`response_format=json_object`、decision/reason 抬升；真实栈 e2e 属 F08，记录为边界 |
| S4 T114 AC4 取消断言过弱 | **fixed**（gate 收紧） | 执行中取消必须 `cancelled`+`finished_at`+`live_attempts==0`、`stuck_count==0`、queued 保持 intent==stop、迟到 result 只接受 `cancelled`；旧证据（2/4/6 stuck）红，修复后 short profile 绿 |
| S5 T114 证据 provenance | **fixed**（文档） | T114 报告新增 §2.1：JSON commit `5ffaff7`、probe 重写 `c27f7e3`、重写后 short 绿跑 `21ee9a0`（169.97s）、49 分钟未重跑原因/限制；不伪造新数字 |
| S6 cancel 修复未覆盖分支 | **fixed**（补测；行为既有） | Go 单测覆盖“exit 已确认但 Run 非 cancelled”（failed=deadline 形态 / needs_reconciliation）；deadline 真实栈路径仍 `not_run`（诚实降级） |
| S7 T122 证据行未绑定交付树 | **fixed**（证据） | 最终交付树重跑并记录 `T122-EVIDENCE`，`git_head=3c7713c...`（证据运行树本身），见 §8.3 |
| T113-1 汇总字段是常量断言 | **fixed**（行为/证据） | `comparison_summary` 全部从测量行派生（每请求 `answer_sha256`、hit_rate=hits/attempts、延迟/计数/成本）；纯函数自检喂“不一致/0 命中”假 rows 必须反映；Node/Python 版本运行时探测；删死代码；oversize body 用 UTF-8 字节 |
| T113-2 瞬态存储错误删有效缓存行 | **fixed**（行为） | 确定性（缺对象/非法 key/超大）→`item_unreadable`+删行；瞬态（IO/不可用）→`cache_unavailable`+保留行；Go 分类/丢弃矩阵单测 + 真实栈“暂时不可读→miss 且行在→恢复后命中” |
| T113-3 ADR 0015 成本描述过时 | **fixed**（文档） | ADR 0015 末尾 dated 修订块（保留原文）：命中含每条 chunk 封存 body 重读+sha256；两类失败；词表不扩 |
| T113-4 可选 smell | **fixed/partial** | `cachedBodyMatches`→`item.matchesSealedBody`；缓存行仍存整份 text 为 judgement call 不修（§7.2） |
| T113-5 AC3 撤权只能 partial | **fixed**（文档/诚实性） | 报告 AC3 改 `partial` + §6 说明（撤权入口都在缓存查询之前；唯一直接复检是合成投毒行；per-resource grant `not_applicable`）；evidence 增 `ac3_verdict/ac3_scope` |
| T113-6 缺封存对象 sha256 独立对照 | **fixed**（证据） | 删除用例直接读封存 source/body 对象重算 sha256，对 citation `source_sha256`、cache `body_sha256`/text 三方对照；evidence 记录 |
| T113-7 Go 单测覆盖 hit 读 body | `not_run`（judgement call） | `validateCachedItems` 依赖 `*sql.DB` + 具体 `*objectstore.Store`；由真实栈 Python 负向/瞬态用例守卫，Go 单测只管匹配/分类函数 |
| §1.3 六个可选 smell | `not_run`（judgement call） | 见 §7.1；每项给出理由，未静默跳过 |

## 2. H1 — ADR 0007 与已编译 adapter 的冲突

- **原缺陷**（Standards）：ADR 0007 三处（约 lines 184/247/309）仍写“真实厂商 adapter 未编译”，
  而 T122 已编译 `internal/modelproxy/openai.go`；`docs/agents/domain.md` 要求方案与 ADR 冲突时
  指出对应 ADR、原因与证据。没有 addendum/superseding ADR，T122 报告与 PROGRESS 也未引用。
- **根因**：T122 只改了代码/报告，没有更新决策记录；ADR 0007 是“当时事实”的 dated 记录，没有修订机制。
- **修复**（文档，非行为验收）：
  1. 新增 `product/docs/adr/0035-t122-openai-compatible-adapter.md`：单一通用协议
     （OpenAI-compatible chat completions）、适用范围（`provider != stub`）、与 ADR 0007 的
     修订关系（D4：修订事实性“未编译”结论，其余决策继续有效）、fail-closed 四道门禁
     （endpoint / catalog destination / protocol / credential）、错误/超时/重试/流式/取消边界、
     “真实厂商 e2e 仍 blocked”、S2 部署级凭据优先级。
  2. ADR 0007 三处原句旁各加一行 `**修订指针（2026-11-08）**`，**只加指针、不改历史正文**。
  3. `reports/f06/T122-real-provider-adapter.md` 头部与 `PROGRESS.md` T122 小节引用 ADR 0035。
- **证据**：`docs/adr/0035-...md`；`git diff` 中 ADR 0007 只新增指针行；`sync_spec --check`
  no drift（未改 spec/contracts）。

## 3. H2 — 调用方取消不再落进 provider_error

- **原缺陷**（Standards）：`internal/modelproxy/openai.go` 的 `classifyTransportError` 把
  `parent.Err()`（`context.Canceled`/`DeadlineExceeded`）原样返回；非流式 `dispatchModel`
  用 `providerErrorCode` 分类，default 为 `provider_error`，于是一次调用方取消会被记成
  provider 失败（流式路径 `stream.go` 已正确报 `stream_cancelled`）。
- **根因**：取消没有被当成一个独立的“非 provider”结局；重试/fallback/失败分类共用一条
  provider 失败路径，且账本清理用的是已取消的请求 ctx。
- **修复**：
  - `capabilities.modelCallFailureCode(ctx, err)`：先判 `context.Canceled` /
    `context.DeadlineExceeded` / 已取消的 `ctx`，返回 `(caller_cancelled, true)`；否则再按
    transient / provider 码分类。`dispatchModel` 在**写任何账本之前**短路：
    不重试、不 fallback、不写 `provider_error`；attempt 循环开始前的取消、backoff 期间的取消、
    fallback attempt 的取消都走同一路径。
  - `finishCallerCancelled` 用 `context.WithoutCancel(ctx)` + 5s 超时（与流式清理同形状）把
    attempt/operation 以新稳定码 `caller_cancelled` 关闭，释放预算预留（`settlement_state=released`）、
    不计费、无 receipt。
  - `providerErrorCode` / `providerFailure` 也把 ctx 错误映射为 `caller_cancelled`（安全网，
    防止其他调用点重新引入 `provider_error`）。
  - `caller_cancelled` 是**新增**的稳定码（brief 允许“若需要新码，先记录理由并加测试”）：
    理由 = 非流式路径没有一个既有码表达“消费方消失”（`stream_cancelled` 是流式专属语义），
    而 `run_cancelled` 会谎称 Run 被取消；语义是“调用方离开，不是 provider 失败”。
- **测试（红→绿）**：
  - 红（`red-go-h2h3.log`）：`undefined: CodeCallerCancelled` / `undefined: modelCallFailureCode`
    （修复前树，测试先写）。
  - 绿（`green-go-h2h3s2s6.log`）：`TestCallerCancellationIsNotAProviderFailure` 7 个子用例
    （已取消 ctx、过期 deadline、无 ctx 的 cancel error、取消 ctx + 无关 error、transient、
    unclassified、not_configured），断言取消用例 `code==caller_cancelled && cancelled==true`、
    且 `providerErrorCode(context.Canceled/DeadlineExceeded)==caller_cancelled`。
  - 真实栈（`gate-t122.log`）：新增
    `test_caller_cancellation_is_not_recorded_as_a_provider_failure`：客户端在途中止请求 →
    operation `failed` + `last_error_code=caller_cancelled`、attempts 1 条
    `failed`/`caller_cancelled`/`released`/charge 0、无 receipt、fake provider 恰 1 次物理请求。
  - T114 真实栈回归 `tests/f13b/test_t114_cancel_exit.py` 仍绿，并补断言在途 operation
    attempt 非 `provider_error`（`gate-t114-cancel.log`）。

## 4. H3 — provider 错误词表单点化

- **原缺陷**（Standards，Shotgun Surgery）：`capabilities.CodeProviderResponseTooLarge` 无人使用；
  同一批字符串散落在 `modelproxy/openai.go`、`api/jobdata.go`（还把
  `provider_destination_not_approved`/`provider_transient` 重新字面量化）、
  `background/handlers/model_validate.go`。
- **根因**：provider 词表没有单一来源；capabilities 的常量、modelproxy 的 adapter 码、
  job 面的历史拼写三处各写一份。
- **修复**：新增叶包 `internal/providercodes`（常量 + `Vocabulary` + `Known`），四处引用：
  - `modelproxy.ProviderCode` / `ProviderStatusError.ProviderCode`（openai.go）；
  - `capabilities` 的 provider 常量改为别名（值不变）；
  - `api/jobdata.go`（HTTP code）；
  - `background.CodeProviderAdapterUnavailable = providercodes.DispatchAdapterUnavailable`，
    后者由 `"provider_" + AdapterUnavailable` 派生（保留 job 面历史拼写，无法漂移）；
  - `background/handlers/model_validate.go` 的 retry table。
- **测试**：
  - `TestProviderCodeVocabularyIsSingleSourced`：12 个 typed provider error → 期望码；每个
    `Vocabulary` 成员可达；`DispatchAdapterUnavailable` 派生关系断言。
  - `TestJobDataErrorUsesTheSharedProviderVocabulary`：真实 `writeJobDataError` writer 的
    JSON `error.code`（7 个错误）。
  - `TestModelValidateOutcomeUsesTheSharedProviderVocabulary`：真实 `outcomeFor` 的重试分类
    （9 例，含 unclassified 保持 retryable）。
- **红证据说明**：H3 是**结构性收敛、不改变任何对外 code 字符串**，同一组映射在旧实现上也会
  产生相同值，所以不存在“值级”红；红体现为旧树缺少 `internal/providercodes` 与
  `jobdata`/`handlers` 的引用（新测试在旧树编译失败，见 `red-go-h2h3.log` 同批）。
  漂移守卫（`Vocabulary` 完备性 + 常量派生）是新增的可检测性。

## 5. S1–S7

### 5.1 S1 / S4 — T114 的“不丢任务”与取消门禁

- **原缺陷**：`tests/support/t114.py` 只记录 `submit_failures` 与 executor `failures`，
  `assert_trial` 从不检查；AC4 只要求 `cancel["stopped"] > 0`，并允许迟到结果停在
  `cancelled/cancelling`，所以“2/4/6 个执行中取消卡住 + 槽位泄漏”也能 `passed`。
- **修复**（`tests/support/t114.py`）：
  - `assert_trial` 增加：`submit_failures == []`、`process_failures == []`、
    `executor.failures == []`（不允许过滤/清空记录；若未来出现合法分类失败，必须写进
    evidence 并对真实 Run/终态断言，而不是丢弃）；
  - `_cancel_rows` 增查 `live_attempts`（`exited_at IS NULL AND revoked_at IS NULL`），
    `_cancel_metrics` 行增 `state_after_api` / `live_attempts`；
  - 执行中取消：每个已持久化 intent 的 running Run 必须 `state=cancelled` + `finished_at` +
    `intent_to_stop_s` 有界（< 30s）+ `live_attempts==0`；`stuck_count==0`；
  - 排队取消保持 `intent == stop`（`finished_at == cancel_requested_at`，无 live attempt）；
    被 claim 抢先的排队目标按执行中规则检查，而不是伪装成瞬时取消；
  - 所有持久化 intent 的行统一必须 `cancelled` + `finished_at` + 无 live attempt；
  - 迟到 result 只接受 `state_after == "cancelled"`。
- **红**：把新 `assert_trial` 喂给已提交的 `T114-capacity.json`：
  `red-s1s4-old-evidence.log`（2 条 executor failure → S1 断言失败）；
  `red-s4-old-evidence.log`（2 条 stuck cancelling → S4 断言失败）。
- **绿**：`gate-t114-capacity.log`：`2 passed, 1 skipped in 155.00s`（short profile +
  committed-evidence 一致性用例；full 用例按设计 skip）。本轮 short trial 的
  `submit_failures==[]`、`process_failures==[]`、`executor.failures==[]`、`stuck_count==0`，
  执行中取消全部 `cancelled` 且无 live attempt。
- **不可重跑部分**：49 分钟 full measurement 未重跑（见 S5），新的 gate 只在 short profile
  与后续 full run 生效；旧 JSON 保留为历史红证据。

### 5.2 S2 — `ORCHARA_MODEL_SECRET_REF` 真正接线

- **原缺陷**：`config.go` 在 stub profile 下校验 endpoint+secret 配对，但 `cmd/api/main.go`
  只把 `cfg.ModelEndpoint` 传给 provider；`Config.ModelSecret` 除 config_test 外无消费者，
  e2e 无法区分部署旋钮有效/无效。
- **修复（选择“接线”，不是删除半实现）**：
  - `realProvider.deploySecret` + `Providers.WithDeploySecret(string(cfg.ModelSecret))`；
  - `resolveCredential` 优先级：候选行 `secret_ref`（`env:`/`file:`/catalog credential env）
    优先；行引用不可解析 → 部署级凭据；两者都不可用 → `provider_not_configured`
    （fail closed）。优先级写入 ADR 0035 D2 与本报告；
  - 半配置仍拒绝启动（既有 config 校验不变）。
- **测试**：
  - Go `TestDeploymentLevelSecretIsTheCredentialFallback`：无部署凭据→`ErrNotConfigured`；
    有→`Authorization: Bearer <部署凭据>`；行凭据可解析时行凭据优先（两者值不同）。
  - 真实栈两态：
    - `t122_stack`（`ORCHARA_MODEL_SECRET_REF` 可解析）→ 行凭据不可解析的候选回退成功，
      新增 `test_deployment_level_secret_ref_is_the_credential_fallback`（200、1 次请求、
      Bearer 部署凭据、两种凭据不泄漏）；
    - `t122_stack_without_deployment_secret`（配对存在但 env 未设置的等价“不可用部署凭据”）
      → 同一行 503 `provider_not_configured`、0 operation 行、0 请求（原 fail-closed 用例
      移到此 fixture，保证两态真能区分）。
  - 证据 tail：`gate-t122.log` `13 passed in 110.24s`。

### 5.3 S3 / S7

- **S3**：`task=route`（F08 routing）与 model_compatibility 是共享 provider 缝的两个
  job-scoped fixture 调用；兼容性已有 e2e，routing 此前只有代码。新增 Go 单测
  `TestRoutingFixtureCallSynthesisesTheRoutingControlPrompt`：断言 messages 是
  system（routing control prompt）+ user（fixture 原文字节）、`model` 是登记 model id、
  `response_format={"type":"json_object"}`、`decision`/`reason` 被抬进 `Data`。路由的真实栈
  e2e 属 F08（本票未新增，记录为边界，不是静默省略）。
- **S7**：旧的唯一 `T122-EVIDENCE` 行 `git_head=ae1af91`（改动前基线），post-merge rerun
  没有机器可读行。修复：在最终交付树上以 `T122_GIT_HEAD=<证据运行树 commit>` 重跑并捕获
  `T122-EVIDENCE`（`git_head=3c7713c...`，见 §8.3）；报告写明该行对应交付树 commit，
  且写入该行之后的 diff 只含文档。

### 5.4 S5 — T114 证据 provenance（文档）

- `T114-capacity.json` 产生于 `git_commit 5ffaff7`；之后 AC5 lease-expiry probe 被
  `c27f7e3` 重写（真实 claim + 过期租约 + 真实 `cmd/admin runs sweep-leases` + sweep report），
  报告 §3.10 仍把旧 harness 的 probe 当 AC5 证据。
- 修复：`reports/f13b/T114-capacity.md` 新增 §2.1 provenance 表：
  evidence commit `5ffaff7`、probe 重写 `c27f7e3`、重写后 **short-profile 在 `21ee9a0`
  绿跑 169.97s**、49 分钟 full 未重跑的原因（只改 after-window probe 路径、证据字节未变、
  成本 49min+seed）与限制；§3.10 明确 probe 证据来源；**不伪造新的全量数字**。

### 5.5 S6 — cancel 修复的“非 cancelled”分支（补测）

- 新增 Go 单测 `TestRefusedProposalWithExitConfirmedButRunNotCancelled`：exit 确认成功、
  `RunState` 返回非 `cancelled`（`failed`=deadline 形态；`needs_reconciliation`=parked）时，
  执行器必须 `outcome=failed`、`RunState` 为 finalizer 状态、保留原失败码，诊断含
  “proposal was refused + 拒绝码 + attempt exit was confirmed”且不含“cancelled”。
- **性质**：补测，不是新行为（分支在集成分支既有修复中已存在，测试直接绿）；
  `T114-cancel-exit-fix.md` §8 记录该分支现被测试钉住，**deadline 真实栈路径仍 `not_run`**，
  不把 Go 单测当真实栈验收。

## 6. T113 增量

详见 `product/reports/f13b/T113-cache-comparison.md` §14（逐 finding 表 + 测试/证据）。
本节只列关键红→绿与命令：

- **T113-1**：
  - 红（`red-t113-1-summary.log`）：旧 `comparison_summary` 无 `measurements` 参数 →
    `TypeError`；语义上旧实现把 `answers_identical_*` 写死为 `len(specs)`、`hit_rate` 写死
    1.0/0.0。
  - 绿（`gate-t113.log`）：`test_t113_comparison_summary_is_derived_from_measured_rows`
    （纯函数，无 DB）通过；喂 1 个不一致答案 + 0 命中的假 rows → `1` 与 `0.0`；重复行被拒。
  - 其他：每请求 `answer_sha256`；`VERSIONS["node"]` 运行时探测（`not_installed` 兜底）、
    python 运行时版本；删除 `Evidence.fail/block`；oversize body 用 UTF-8 字节计。
- **T113-2**：
  - Go 红→绿：`TestTransientSealedBodyReadFailureKeepsTheCacheRow`（6 个错误分类 + 丢弃矩阵
    + ADR 0015 D6 词表边界）。旧实现任何读错误都 → `item_unreadable` 删行。
  - 真实栈（`gate-t113.log`）：负向用例 (5b) 把 body 对象临时改成不可读目录：
    `hit=false`、`discarded=true`、`discard_reason=cache_unavailable`、**行在且 hit_count 不动**；
    恢复后同一行 `hit=true` 且 hit_count+1；确定性移除仍删行。
- **T113-3**：ADR 0015 末尾 dated 修订块（保留原文）；命中成本补上“每条 chunk 封存 body
  重读 + sha256”；确定性/瞬态两类失败；词表不扩。
- **T113-5**：报告 AC3 → `partial`（删除 proven；撤权跨主体/管理员禁用/合成投毒行；
  per-resource grant `not_applicable`）；evidence `revocation` 段增 `ac3_verdict=partial`
  与 `ac3_scope`。
- **T113-6**：删除用例直接读封存 source/body 对象、重算 sha256：
  `source_object == artifacts.sha256 == citation.source_sha256`；
  `body_object == artifacts.sha256 == cache_item.body_sha256`；`text`/`citation` 逐项对照；
  evidence `independent_truth.sealed_object_sha256`。
- **T113-4**：`cachedBodyMatches(item, text)` → `item.matchesSealedBody(text)`（Feature Envy）；
  缓存行仍存整份 text **不修**（judgement call，§7.2）。
- **T113-7**：`not_run`（judgement call）：见 §7.3。
- **未重新生成 T113 evidence JSON**：本轮未设 `F13B_WRITE_EVIDENCE=1`，committed
  `T113-cache-comparison.json` 仍绑定 `e64e7b7` 树；新的 summary 字段只在新的证据跑出现，
  报告不拿旧 JSON 冒充新证据。

## 7. `not_run` / judgement calls（逐条）

### 7.1 §1.3 可选 smell（全部 `not_run`）

| smell | 判定 | 理由 |
|---|---|---|
| `endRefusedProposal(started bool)` 恒为 true，去掉参数 | `not_run` | 纯签名收敛；三个调用点均传 `true`，删除参数不改变行为，但会与 T114-cancel-exit-fix 的报告/单测 diff 交叉；本轮优先级低于 hard/spec，未做 |
| `real.go Call` 先 `Available` 再重复 parse/resolve，提取 prepared call | `not_run` | 需要重排 provider 调用路径（判断/执行分离），风险高于收益；现有测试已覆盖 fail-closed 顺序 |
| `providerFailure` vs `providerErrorCode` 两个 switch 默认值不同 | `not_run` | 默认值语义有意不同（HTTP 映射默认 `provider_not_configured`，账本默认 `provider_error`）；合并需要保留两默认值参数化，属纯重构，未做 |
| `config.go explicitStubDestination` 与 real-model 分支重复校验 | `not_run` | 两分支的“必填/半配置拒绝”语义不同（stub 可选对、real-model 必填）；安全合并需要额外测试矩阵，未做 |
| `modelproxy_test.go` 90ms/50ms 墙钟断言放宽 | `not_run` | 属 CI flake 降噪；本轮未观察到这两条 flake，改断言需单独证明不弱化语义 |
| `openai.go:214/234` 在 catalog 之前接受 `env:` destination 的纵深防御注释 | `not_run` | 纯注释；代码已有 `modelregistry.ValidateInput` 注册期拦截，记录为已知边界 |

### 7.2 T113-4 第二部分：缓存行仍存整份 `items.text`

judgement call，**不修**：`storeCachedRetrieval` 已按 `RetrievalCacheMaxEntryBytes`
（256 KiB，text UTF-8 bytes + citation bytes）拒绝超限条目；缓存行的 text 是命中时可直接
服务的记忆正文（命中还要重读封存 body 做校验）。改为只存引用会改变 oversized 用例语义、
增加额外失败面，且 ADR 0015 的“命中不需要重新解析文档”边界会变。保持行为与 ADR 一致。

### 7.3 T113-7

`not_run`：`validateCachedItems` 依赖 `*sql.DB` 与具体 `*objectstore.Store`，本包 Go 测试是
纯单测；命中读取路径已由真实栈 Python 负向/瞬态用例守卫（读失败 → 特定 reason/行保留/恢复），
Go 单测只覆盖匹配/分类函数。若未来把对象读取抽成 interface seam，可补该测试。

### 7.4 其他边界

- **真实厂商 e2e、真实模型延迟/成本、真实容量机器、GPU、多机、浏览器 DOM**：`blocked` /
  `not_run`（沿用 T122/T113/T114 既有边界），本报告未用 fake provider 冒充真实厂商。
- **T114 49 分钟 full measurement 未重跑**（S5）：见 §5.4；新的 S1/S4 gate 只在 short
  profile / 后续 full run 生效。
- **deadline-refused 的真实栈路径**（S6）：`not_run`（需要 proposal 窗口内 deadline 先被
  finalizer 观察到的时序注入）；只有 Go seam 测试覆盖返回/错误码。

## 8. 门禁（从 worktree 的 `product/`）

### 8.1 Go / spec

```text
$ go build ./...
build exit=0
$ go vet ./...
vet exit=0
$ go test ./...
test exit=0（全部包 ok / no test files；无 FAIL 行）
$ gofmt -l internal cmd tools
（空）
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

（完整逐包输出：`E:\Orchara\.scratch\f13b\review-fixes\gate-go.log`。）

### 8.2 真实栈（锁：`locks\realstack\review-fixes.owner.txt`，容量短 profile 独占）

```text
$ python -m pytest tests/f06/test_t122_real_provider.py -q
13 passed in 110.24s (0:01:50)          # gate-t122.log

$ python -m pytest tests/f13b/test_t114_cancel_exit.py -q
1 passed in 33.10s                      # gate-t114-cancel.log

$ python -m pytest tests/f13b/test_t114_capacity.py -q
2 passed, 1 skipped in 155.00s (0:02:34)  # gate-t114-capacity.log（short profile + committed-evidence 校验；full skip）

$ python -m pytest tests/f13b/test_t113_cache_comparison.py -q
6 passed in 77.49s                      # gate-t113.log

$ python -m pytest tests/f10/test_t067_cache.py -q
8 passed in 75.04s                      # gate-t067-cache.log
```

（真实栈窗口：T122 → T114 cancel → T114 capacity short（独占）→ T113 → T067；容量测量期间
无其它真实栈运行。T114 short 的首次运行因**测试自身**在“queued 目标被 claim 抢先”的竞态上
断言过严而失败（trial 内所有 S1/S4 新断言均通过）；把该行改判为执行中取消后重跑
`2 passed, 1 skipped in 155.00s`。第一次失败的日志保留在 `gate-t114-capacity.log` 的上一版，
本报告不把它当产品缺陷。）

### 8.3 S7 证据行（最终交付树）

```text
T122-EVIDENCE {"charged_amount": "0.00002500",
  "git_head": "3c7713cdc2f167c611087e67b1579e7d144d8a94",
  "model_id": "fake-vendor-model-1",
  "model_profile_version_id": "22222222-2222-4222-8222-222222222222",
  "params_sha256": "ed59ed7c047dd96d7f5c17df85d827230d1ba185e46eef40a4aa901a2a84546a",
  "price_version": "price-v1:22222222-2222-4222-8222-222222222222:in=1.00:out=2.00:USD",
  "profile": "dev-stub + explicit ORCHARA_MODEL_ENDPOINT (loopback fake provider)",
  "provider": "openai-compatible",
  "receipt_artifact_sha256": "f332165a26f3caf682f25b048ded043dd30ef2775ebea6f3d6e8cf8c2988b882",
  "receipt_size_bytes": 169, "tokens_in": 11, "tokens_out": 7}
13 passed in 109.97s (0:01:49)
```

该行由最终交付树上的 `python -m pytest tests/f06/test_t122_real_provider.py -q -s`
（`T122_GIT_HEAD=3c7713c...`）打印；`git_head` 就是**证据运行的交付树 commit**
（`ad9b39a` 代码 + `3c7713c` 文档）。本报告随后只新增这一行证据（docs-only），
`git diff 3c7713c..HEAD --stat` 只含文档。`gate-t122-evidence.log` 里 PowerShell 把
fake provider 的 `ConnectionResetError`（H2 取消用例主动断连的预期 stderr）记成 exit 1，
pytest 本身是 **13 passed**；前一门禁跑 `-q` 同样 **13 passed** 且进程 exit 0。

### 8.4 补充 tail（T113 / T067 / S7 evidence 行）

```text
# gate-t113.log
......                                                                   [100%]
6 passed in 77.49s (0:01:17)

# gate-t113-negative.log（T113-2 瞬态读失败单测复跑）
.                                                                        [100%]
1 passed, 5 deselected in 31.66s (0:00:31)

# gate-t067-cache.log
........                                                                 [100%]
8 passed in 75.04s (0:01:15)
```

## 9. 提交与 merge

- 本分支基线 `e4591d6`（= `integration/t113-t114-t122` tip）；结束前执行
  `git merge integration/t113-t114-t122` → **Already up to date**（无冲突、无 merge commit）。
- 提交：`ad9b39a` `fix(b2)`（H2/H3/S2/T113-2/T113-4/S6/S3 产品代码 + Go/Python 测试）、
  `3c7713c` `docs(b2)`（ADR 0035、ADR 0007/0015 修订、四份报告、PROGRESS）；随后一个
  docs-only commit 写入本报告的 S7 证据行/门禁 tail（HEAD 见 `git log`）。
- 验证：`git diff 3c7713c..HEAD --stat` 只含 `docs/`、`reports/` 与 `PROGRESS.md`。
- 未 push；未改 `docs/spec/**`、`contracts/**`、迁移；未改其它票测试断言
  （除 S1/S4/S2 的 harness/fixture 收紧与 S6 的失败路径测试，均为本 brief 要求）。

## 10. 原始日志

`E:\Orchara\.scratch\f13b\review-fixes\`：

- `red-go-h2h3.log`、`red-go-s2.log`、`green-go-h2h3s2s6.log`
- `red-s1s4-old-evidence.log`、`red-s4-old-evidence.log`、`red-t113-1-summary.log`
- `gate-go.log`、`gate-t122.log`、`gate-t114-cancel.log`、`gate-t114-capacity.log`、
  `gate-t113.log`、`gate-t067-cache.log`
