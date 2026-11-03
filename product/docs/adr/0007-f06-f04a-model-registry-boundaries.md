# ADR 0007：F06 模型目录、兼容验证与完成绑定的边界

- 状态：已接受
- 日期：2026-11-03
- 相关：F06 / T039（GitHub #40，规划 ID F06.01/F06.08）；分支
  `t/t039-model-registry`（worktree `E:\Orchara-wt\t039`）；报告
  `product/reports/f06/T039-model-registry.md`；前置 T024（`model_validate` handler /
  `platform_model_calls` 账本）、T026（`evaluation_jobs` / `evaluation_reports` 与报告绑定）、
  T025（`catalog.modelReadinessProblem` 依赖就绪）；后续 T040/T041（模型选择与共享应用）
- 备注：简报要求 `docs/adr/0006-f06-f04a-boundaries.md`，但 0006 已被 F05/F03 使用，故本记录为
  0007；内容仍为简报规定的 D1–D3 + catalog shape。

## 背景

T039 的产品行为是：管理员从预登记的目的地/凭据引用登记模型版本，运行平台合成的结构化
输出兼容用例，查看不可变报告，并且只有 `validation_state=passed` 且未撤销的版本能被共享
应用引用。T024 已经交付 `model_validate` 后台 handler（下载 fixture → 调模型 → 上传报告 →
等 finalize → succeeded），T026 已经交付“封存报告 + 严格绑定 + `evaluation_reports`”的
完成守卫模式，但两者之间没有“谁创建候选/谁把报告变成模型状态”的产品入口。

本票需要同时解决三个边界问题，且不得破坏 T024 已交付的直接入队路径：

1. **引用边界**：`modelproxy.Candidate` 历史上只接受 `env:`/`file:` 部署引用；F06 的产品
   入口要求管理员只能选择 **预登记** 的 endpoint/credential 引用，而不是自由文本 URL 或密钥。
2. **完成边界**：兼容报告是 worker 上传的字节，服务端必须把它绑定到候选、fixture、
   `platform_model_calls` 账本行和评测行；同时 T024 的“无评测行”直接 job 必须保持
   report-only 语义。
3. **契约边界**：`registerModel`/`listModels`/`getModel`/`validateModel` 以及两个 admin
   catalog 读在 `contracts/openapi.json` 中已有精确 schema（`additionalProperties:false`），
   实现不得添加私有字段，也不得改 contracts。

## 决策

### D1 — 注册只接受 deployer catalog 引用；manifest 由服务端构建

- 新增 `internal/modelregistry` 包；catalog 来自两个环境变量（见下），严格 JSON
  （未知字段/尾随数据拒绝），**fail closed**：只设一个变量即拒绝；两个都不设是文档化
  dev-stub 默认。
- catalog 的 id 必须是 slug（`^[a-z0-9][a-z0-9._-]{0,63}$`），从形状上排除 URL/路径/密钥；
  endpoint 的 `credential_ref` 必须已登记；同 id 重复拒绝。
- 注册校验 `ModelInput` 全字段 + `ModelConfig`（kind/protocol 枚举、正值、embedding 条件
  必填、temperature 0–2、fallback UUID 唯一、price 十进制正则而非二进制浮点）。
  `data_destination` 必须等于 endpoint 登记目的地；`secret_ref` 必须是该 endpoint 的
  credential；provider `stub` 只允许 `local-stub`/`capacity-stub`。
- manifest = 服务端构建对象的 RFC 8785 JCS 字节（format_version/name/provider/model_id/
  endpoint_ref/secret_ref/config/data_destination；**无时间戳/run 内容**），sha256 即
  `manifest_sha256`；封存为 `platform.artifacts(purpose=model_manifest, ready)`，与
  `model_profile_versions(pending)` 同事务；重复 manifest → 409，不写第二行。
- 公开投影只有契约六字段，永不返回 `secret_ref`；catalog 的 credential 只投影
  `id`+`description`。可选的 credential `env`（部署环境变量名，非密钥值）用于真实 adapter
  在 Go 内解析密钥，**不进入任何 API/页面/报告**。
- `modelproxy.Candidate` 同时接受旧的 `env:`/`file:` 引用与 catalog id（catalog 存在性检查），
  使 T024/维护路径与 F06 注册路径共存；真实 provider 的凭据读取仍在 Go 内。

### D2 — 完成守卫：有评测行才绑定；无评测行的 T024 job 保持 report-only

- 成功：`verifyJobResultArtifact` 后，**若本 job 有 `evaluation_jobs` 行**，严格解码封存报告
  （≤1 MiB、未知字段拒绝），校验：
  candidate id == `job.TargetID`；fixture id == `job.InputArtifactID`；report fixture sha ==
  fixture artifact sha256；`platform_model_calls.id=call_id` 属于本 job + 本候选 + succeeded；
  `evaluation_jobs.dataset_artifact_id == job.InputArtifactID`；
  `compatible == check.passed`；报告状态由 `compatible` 决定。
  同事务插 `evaluation_reports`（manifest=候选 manifest、dataset=fixture sha、
  status passed/failed）、evaluation succeeded+report_id、model passed/failed+
  evaluation_report_id。失败判定 = 成功 job + failed 报告。
- 终态 failed/cancelled（不可重试）：仅对本 job 有评测行的情况把 evaluation 置
  failed/cancelled、model 置 failed、不写报告；重试（`retryAllowed`）不改模型/评测。
- 直接入队的 T024 job（无评测行）：成功仍只要求报告 artifact 就绪且属于本 job；失败/取消
  也不改任何模型/评测状态。三个分支共用 `modelValidationEvaluationBound` 判定，避免
  “成功跳过绑定、失败却改状态”的不一致。

### D3 — HTTP 只返回契约投影；幂等与 catalog 读的语义固定

- `POST /api/v1/models`：admin（API key 永不是 admin）、CSRF、201 `Accepted{id,state}`。
- `GET /api/v1/models`：auth + API key `application:read`；`{items,next_cursor}`；
  limit 1–100、opaque keyset cursor。`application_id` 是契约声明的参数但语义未定；本票实现为
  “passed 且未撤销”的可选集合（见报告 §4.7），T040/T041 可依赖该语义。
- `GET /api/v1/models/{model_version_id}`：auth + `application:read`；admin 全量，其他身份
  仅 passed 且未撤销；不可见 == 不存在 == 404。
- `POST /api/v1/models/{model_version_id}/validate`：admin；`Idempotency-Key` 必需且规则与
  debug 路径一致（缺失/重复/空 → 400 `idempotency_key_required`；同 key 同请求回放记录 202；
  异请求 → 409 `idempotency_conflict`）。行锁决定 revoked(409)/running(返回既有)/passed(409
  `already_validated`)/pending|failed(继续)。fixture 是服务端确定性 JCS 字节，封存为
  `purpose=model_fixture`（T024 的 synthetic-fixture 规则）；provider `stub` 且 model_id 以
  `-incompatible` 结尾时使用 `structured_output_invalid` 用例（文档化 stub 模拟）。同事务：
  `EnqueueTx(model_validate, target=model, input=fixture, dedup=evaluationID)` +
  `evaluation_jobs(kind=model, dataset_artifact_id=fixture)` + model running + 幂等收据；
  202 返回契约 `EvaluationJob` 六字段。
- `GET /api/v1/admin/model-endpoints` → 精确 `{items:[{id,data_destination}]}`；
  `GET /api/v1/admin/credential-refs` → 精确 `{items:[{id,description}]}`。两者 admin-only，
  不返回 `credential_ref`/`env`/密钥值（契约 `additionalProperties:false`）。
- `/models` 页面只通过上述 HTTP 读数据；所有内容用 `textContent`，不存 localStorage，
  不接收/显示任何密钥文本。

## Catalog shape（固定）

```json
ORCHARA_MODEL_ENDPOINTS='[{"id":"stub-endpoint","data_destination":"local-stub","credential_ref":"stub-credential"}]'
ORCHARA_CREDENTIAL_REFS='[{"id":"stub-credential","description":"dev stub (no real secret)"}]'
```

- endpoint 项：`id`（slug）、`data_destination`（非空，≤100）、`credential_ref`（必须已登记）、
  可选 `description`。
- credential 项：`id`（slug）、`description`（1–200）、可选 `env`（`^[A-Za-z_][A-Za-z0-9_]{0,127}$`，
  只是部署环境变量名）。
- 默认（两个变量都不设）：`stub-endpoint` → `local-stub` + `stub-credential`
  （description `dev stub (no real secret)`）。
- 只设一个变量、未知字段、尾随数据、重复 id、endpoint 指向未登记 credential → 启动/构造失败
  （fail closed），绝不静默退化为“接受任意引用”。

## 后果

- 正面：注册不可变、可复算 manifest；报告不能脱离候选/fixture/账本被接受；共享应用只可能
  选中 passed 未撤销版本；T024 既有路径与测试不回归；真实 adapter 的凭据解析仍在 Go 内。
- 代价：`validateModel` job 必须由产品入口创建（同事务写 evaluation 行）才会对账模型状态；
  手工 `admin background-jobs enqueue` 的 `model_validate` 仍是 report-only。真实厂商 adapter
  未实现前，真实 provider 失败为 `provider_not_configured`（诚实失败，不伪造成功）。
- 不修改 `contracts/**`/`docs/spec/**`/迁移 0001–0024；`sync_spec --check` 保持 no drift。

## T040 追加（2026-11-03）：F04A 线性四节点运行时与画布边界

- 相关：F04A / T040（GitHub #41，规划 ID F04A.01/F04A.02）；分支 `t/t040-canvas`
  （worktree `E:\Orchara-wt\t040`）；报告 `product/reports/f04a/T040-canvas-and-linear-compile.md`；
  前置 T025（manifest/`ExecutableError`）、T037（工具就绪+审核）、T039（passed 模型）。
- 备注：简报仍写“append ADR 0006”，但 0006 已被 F05/F03 使用；T039 已在 0007 记录该编号
  冲突，本票继续追加到 0007，不再新开编号。

### D4 —— F04A 是线性四节点编译器 + LangGraph stub 证明，不是生产 worker

- 生产 Run 运行时仍由 Go supervisor/线性执行器承担（T042/T043 才交付真实 python/agent
  节点执行）。本票交付的是：Go 权威校验器（`internal/workflow`）之上的一份 **Python
  防御性重校验**（`worker/platform_worker/compiler/validate.py`，只覆盖
  `start → python → agent → end` 子集）、一个只会按声明边顺序执行的 LangGraph
  `StateGraph`（`graph.py`）以及确定性 stub 适配器（`stub.py`）。
- 共享语料 `tests/f04a/corpus/linear_f04a.json` 的共享用例同时被 Go
  `TestF04ACorpusParity` 与 Python 测试断言同一 `(verdict, code, pointer)`；F04A 之外的
  分支/join/其他节点类型在 Python 侧用稳定码拒绝（`unsupported_node_type`、
  `linear_order`、`not_linear_chain`、`unsupported_port`、`unsupported_subapp`、
  `bad_output_mapping`），不伪造“与 Go 全量一致”。
- `NodeOutcome` 是唯一归一化结果（`succeeded/failed/waiting/needs_reconciliation`），
  适配器永不返回 provider/framework 对象；失败/等待/需对账会短路后续节点。
- 不新增执行端点、不做真实模型/工具执行；真正数据/文件/日志/沙箱属 T042/T043。

### D5 —— 画布是真实 Vue Flow 应用，产物是提交的静态 bundle

- `apps/web/canvas` 是独立的 Vue 3 + Vue Flow 应用，`build.mjs` 用 esbuild 打成
  `apps/web/src/assets/canvas.{js,css}` 并**提交**；运行时/测试都不需要 npm；`node_modules/`
  被忽略。纯逻辑在 `canvas/src/lib/*.js`，由 Node 测试直接执行（DSL↔图、合法变量来源、
  错误→节点/字段映射、导出清洗、连线拒绝）。
- 画布只允许 `start → python → agent → end` 单链：连线在右向/入向已存在、自环、成环、
  端节点越界时被拒绝并显示原因；非法 DSL 仍可保存（既有 T023 语义），但校验/调试/发布
  由服务端权威判定。
- 布局只存在独立 `layout`（`{version:1, positions}`），与 `definition` 分开保存；
  保存仍走 `PUT /draft` + `If-Match`，412/428 分支与调试入口保持 T023 行为；校验调用
  `POST /applications/{id}/validate`，`{node_id,code,pointer,message}` 映射到节点和配置字段
  并可点击跳转，成功时显示 `X-Manifest-SHA256`。
- 契约/迁移零改动：无新字段、无新执行路径；`/assets/canvas.{js,css}` 只是静态页面资产。

## T041 追加（2026-11-03）：运行域受控能力代理与 worker 适配边界

### D6 —— 一次逻辑 operation，每次物理调用一行 attempt；Go 是唯一重试权威

- `platform.operations` 是逻辑身份（worker 给的 operation id；`UNIQUE(run_id,logical_key)`），
  `platform.operation_attempts` 是每次真实派发一行；worker 适配器每次 `generate` 只发一次
  请求，绝不隐藏重试。transient provider 失败最多重试 2 次（每次新 attempt 行）；部署事实
  （`provider_not_configured`/`adapter_unavailable`）与 schema/权限/校验错误不重试。
- 授权全部先于 provider：run=running、live attempt、封存 manifest 字节重算 SHA-256、capability
  version 在 manifest allowlist、kind 双集合歧义拒绝。params 的 RFC 8785 JCS 字节先封存为
  `capability_params` artifact，其 SHA-256 是回放/冲突/工具 grant 的共同身份。
- 偏差（已写在迁移 COMMENT）：`operations` 增加 `created_at`/`updated_at`/`last_error_code`；
  `operation_attempts` 是非契约表（T046 在其上结算）；`attempt_token` 绑定物理 attempt 与
  run attempt。未创建 approvals/connectors/budget 表。

### D7 —— 工具执行留在 worker；服务端只发 scoped grant 并校验回报

- 工具 operation 在服务端只到 `dispatching`：返回
  `grant{operation_id,capability_version,capability_kind,risk_class,params_sha256,run_id,
  attempt_token}`；真实沙箱执行是 T043。
- 回报走文档化非契约 `POST /internal/v1/runs/{run_id}/capability-results`（worker mTLS）：
  必须绑定同一 run/attempt 且 operation 处于 dispatching；succeeded 只接受 ready 且 owner 为
  run owner 的 artifact，或把 worker 上报的小 `data` 服务端封存为 receipt；失败只接受安全
  小写 code；重复回报幂等。owner 账本只投影状态/尝试数/tool-call id+name/结构化键/stub，不返回
  params 或回执正文。
- 真实厂商 adapter 仍未编译：真实 provider 诚实失败，stub 输出处处 `stub=true`；本能力
  投影/账本不得被读成真实模型结果。

## T042 追加（2026-11-03）：线性 python 节点执行与节点结果边界

### D8 —— 线性计划优先，legacy T033 图逐字节保留

- 执行器先解析封存 `definition` 为 `start → python → end` 单链（恰好 3 节点、2 条边）；
  分支/汇合/多 python/其他节点类型确定性拒绝，agent 节点以 `agent_node_not_supported`
  拒绝（T043 替换点）。`agent` 的拒绝分支与 `linearPlan` 是 T043 的唯一扩展缝。
- 兼容规则：封存 golden 图是 `start/python/agent/end`，T033/T037 的真实行为与既有测试要求
  它继续成功。因此当 manifest 恰有一个预设 python 节点时走原 legacy 单 python 路径（不执行、
  不校验 agent 节点，不产生 node 事件/节点结果）；只有没有可运行预设 python 节点的 agent 图
  才拒绝。T043 落地后删除该兼容分支。
- `node.status` 的 `attempt_no` 由 supervisor 的可信 DB 读身份按 run 内 attempt 序号派生
  （契约无该列）；worker 自报的值不被接受。事件只提交 `{node_id,state,attempt_no}`，不含正文。

### D9 —— 节点结果只是 provenance；原始输出绝不直接成为已授权 artifact

- 迁移 0026 `platform.run_node_outputs`：`(run_id,node_id,kind∈{data,file,log},artifact_id,
  label,created_at)`，PK 四列；不含内容、对象 key、token。artifact 行仍是内容权威，
  `run_artifact_uploads` 仍是 run/attempt/purpose 预约证明，`run_artifacts` 仍是 run 链接。
  无 ON DELETE 子句：F07 删除 artifact 时必须同删 provenance 行。
- 线性 python 节点从不上传原始 `output.json`：data 是 JCS(`output.json.data`)，
  文件是逐个 admit 的 artifact，日志是截断的 stdout/stderr；三者都必须先走
  create/complete/register（finalize guard 置 ready）之后才允许写 provenance。上报端点
  用 live attempt + artifact ready + purpose 匹配 kind 复核（log 不能被报成 data）。
- 两个端点非契约并记录在 T042 报告：`POST /internal/v1/runs/{run_id}/node-results`
  （worker/supervisor mTLS + attempt token，1–64 行，幂等）与 owner-only
  `GET /api/v1/runs/{run_id}/node-results`（返回 artifact 事实，不含内容）。日志仍走
  既有 `GET /artifacts/{id}/content`，且因 `purpose=log` 不出现在 `listRunArtifacts`。
- `/runs` 节点结果面板只读上述投影，纯函数由 Node 执行；"无结果"与"失败"是两种显示，
  不互相顶替；页面不存 localStorage/sessionStorage，不显示 object key。
