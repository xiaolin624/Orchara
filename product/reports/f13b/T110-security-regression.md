# T110 / GitHub #111 执行平台身份、文件、出站与注入安全回归

- **状态：`partial`（平台层安全回归的攻击全部被真实层阻断；但 AC2 的导出要求里，TAB/CR 前缀未被转义/加引号这一条存在未修 finding，故不写 `passed`；不是真实浏览器、不替代 T111 的 Linux 沙箱验收）**
- 分支：`t/t110-security-regression`；worktree：`E:\Orchara-wt\t110`
- 基线：`integration/f13b` @ `f6e0394`；结束前 merge 集成 tip @ `5a210c0`（T108/T109 最终 merge）→ merge commit `40e54cc`
- 证据生成时的 HEAD：`40e54cc`（证据 JSON 的 `product_commit` 字段即此值；其后只追加本报告与 PROGRESS 小节）
- 命令工作目录：`E:\Orchara-wt\t110\product`

## 0. 交付物

| 交付物 | 说明 |
|---|---|
| `product/tests/f13b/security_support.py` | 攻击证据记录器（`blocked_at` 只允许 `os/network/proxy/app_validation/schema`）、输入 hash、响应 fact、独立真值；Node+DOM shim 的 XSS harness；committed CSV 导出器探针；`F13B_WRITE_EVIDENCE=1` 才写回 JSON，并用 `tools/evidence_redaction` 拒绝凭据形状的值 |
| `product/tests/f13b/test_t110_security_regression.py` | 15 个 pytest 用例，36 条攻击记录；真实 API/真实准入/真实 connector/真实 maintenance；页面执行**已提交真实字节** |
| `product/reports/f13b/T110-security.json` | 36 条逐攻击机器可读证据（输入/sha256/期望/实际/阻断层/响应/独立真值/断言/命令/`not_run`） |
| `product/reports/f13b/T110-security-regression.md` | 本报告 |
| `product/PROGRESS.md` | 追加 T110 小节 |
| `E:\Orchara\.scratch\f13b\T110-REPORT.md` + `E:\Orchara\.scratch\f13b\T110\` | 给编排者的摘要与原始日志 tail |

未修改 `docs/spec/**`、`contracts/**`、迁移与既有票断言；未改产品安全语义（发现的问题按“报告不修”处理）。

## 1. 环境与 stub/real 分界

| 项 | 实际使用 | 说明 |
|---|---|---|
| PostgreSQL | **16.4** `127.0.0.1:55432` | 每次真实栈会话一条一次性数据库；真实 `cmd/admin migrate` 全链 + bootstrap |
| API | **编译后的 `cmd/api`** | 真实 HTTP、真实 object store、HMAC signed links、真实内部 mTLS 监听（supervisor/maintenance/worker 证书） |
| maintenance | **编译后的 `cmd/maintenance`** | 真实 `artifact_finalize` / `document_ingest` / `tool_run` handler（`--once` drain） |
| connector 端点 | **真实 loopback HTTP server** | 每个策略一个端点；端点自己的请求日志是独立外部账本 |
| 预置工具 | **committed `worker/platform_worker/nodes/sales_stats.py`** | 真实 tool_run 作业里由 Go executor 作为子进程执行；导出 `summary.csv` 读取真实封存字节 |
| 模型 | `ORCHARA_PROFILE=dev-stub` | 知识 answer 是文档化的 `extractive_stub`（`not_configured`）；**无真实厂商模型** |
| 浏览器 DOM | **无 Playwright/Chromium** | 页面测试用 Node `vm` 执行 `apps/web/src/assets/memories.js` **已提交真实字节** + 最小 DOM/fetch shim；**不是真实浏览器** |
| 沙箱 | 本票不使用 WSL2/runsc | 脚本/构建隔离属 T111 Linux 沙箱验收，本票不代替 |

`not_run`（每份证据里也逐条记录）：真实浏览器 DOM、真实第三方 connector、真实厂商模型对注入的服从/拒绝、Excel/LibreOffice 打开导出 CSV、带活动 DNS 的 rebinding、T111 的 Linux 沙箱逃逸。**本票没有 OS 层阻断结论**（`os` 层 0 条）；OS 层属 T111。

## 2. 攻击矩阵（36 条，逐 AC）

| 攻击 | 目标 | 阻断层 | 状态 | 断言数 |
|---|---|---|---|---|
| `T110-EVID-01` | 证据完整性门（每条阻断声明有层次、有断言、`not_run` 有理由） | `app_validation` | `blocked` | 78 |
| `T110-ID-01` | B 读 A 的 Run / 打开 SSE / 取消 Run；与随机 UUID 同 404 | `app_validation` | `blocked` | 18 |
| `T110-ID-02` | B 读/下载/读正文/complete A 的 artifact | `app_validation` | `blocked` | 20 |
| `T110-ID-03` | B 读/决定 A 的 approval；admin 可读但决定 403 | `app_validation` | `blocked` | 13 |
| `T110-ID-04` | B 读 A 的 KB/document/citation/retrieval/写文档 | `app_validation` | `blocked` | 25 |
| `T110-ID-05` | B 与 admin 读/改/删 A 的个人 memory | `app_validation` | `blocked` | 15 |
| `T110-ID-06` | B 读/删 A 的 conversation、列 messages、伪造 conversation_id 提交 request | `app_validation` | `blocked` | 15 |
| `T110-ID-07` | 匿名访问七类受保护 family 全部 401 | `app_validation` | `blocked` | 28 |
| `T110-ID-08` | 伪造 Bearer / session cookie / `X-Principal-Id` 头 | `app_validation` | `blocked` | 10 |
| `T110-ID-09` | 缺/伪造 CSRF 的 session 写请求，零写入 | `app_validation` | `blocked` | 7 |
| `T110-ID-10` | 伪造内部 attempt token 调 capability | `app_validation` | `blocked` | 5 |
| `T110-ID-11` | 无客户端证书连内部 mTLS 监听（TLS 层） | `network` | `blocked` | 2 |
| `T110-ID-12` | `X-Internal-Role` 与证书组织不一致 / 缺失 | `app_validation` | `blocked` | 6 |
| `T110-ID-13` | 未知 API key 不会回退到同客户端上的真实 session cookie | `app_validation` | `blocked` | 3 |
| `T110-ID-14` | read-scope service key 不能写；读 A 的 Run 仍 404（scope≠ACL） | `app_validation` | `blocked` | 8 |
| `T110-ID-15` | 篡改 HMAC 下载链接 404；真实链接返回封存字节 | `app_validation` | `blocked` | 8 |
| `T110-SSRF-01` | connector 指向 `169.254.169.254`（allow_private 也为 true） | `proxy` | `blocked` | 5 |
| `T110-SSRF-02` | `127.0.0.1` + `allow_private=false`，端点 0 请求 | `proxy` | `blocked` | 4 |
| `T110-SSRF-03` | `localhost` DNS 解析到私网 + `allow_private=false`，端点 0 请求 | `proxy` | `blocked` | 4 |
| `T110-SSRF-04` | 端点 302 到 metadata；拒绝跟随，端点恰 1 请求 | `proxy` | `blocked` | 5 |
| `T110-SSRF-05` | 对照：同一 `localhost` + 显式 `allow_private=true` 真的到达端点 | — | `observed` | 3 |
| `T110-DOC-01` | 宏 XLSX（vbaProject.bin）真实上传 → quarantine | `app_validation` | `blocked` | 3 |
| `T110-DOC-02` | 路径穿越条目 XLSX 真实上传 → quarantine | `app_validation` | `blocked` | 3 |
| `T110-DOC-03` | 520 条目 zip 炸弹真实上传 → quarantine | `app_validation` | `blocked` | 3 |
| `T110-DOC-04` | 压缩比炸弹通过准入，真实 parser → `ingest_archive_limit_exceeded` | `app_validation` | `blocked` | 5 |
| `T110-DOC-05` | DTD/entity XLSX → `ingest_dtd_not_supported` | `app_validation` | `blocked` | 3 |
| `T110-DOC-06` | 已 seeded ready 的穿越 XLSX → parser `ingest_archive_path_traversal`（防御纵深） | `app_validation` | `blocked` | 3 |
| `T110-DOC-07` | 加密 PDF → `pdf_encrypted` | `app_validation` | `blocked` | 3 |
| `T110-XSS-01` | 真实 API 返回的 XSS payload 喂给 committed `memories.js` 渲染函数 | `app_validation` | `blocked` | 12 |
| `T110-XSS-02` | 第一方页面源码无 HTML 解析 sink（静态门） | `app_validation` | `blocked` | 2 |
| `T110-CSV-01` | 真实合法 tool run 的导出字节无公式前缀 | `app_validation` | `blocked` | 5 |
| `T110-CSV-02` | `= + @ -`、`space+=`、`TAB+=`、`CR+=` 七种 region 全部 `formula_not_supported` | `app_validation` | `blocked` | 42 |
| `T110-CSV-04` | committed 导出器对 `= + - @` 加前缀（TAB/CR 仍原样） | `app_validation` | `partial` | 6 |
| `T110-CSV-05` | `\tTAB`/`\rCR` 开头的真实导出字节：TAB/CR 未被转义/加引号（结构 finding） | — | `finding` | 5 |
| `T110-PI-01` | 知识注入文档真实检索+answer：0 新 operation/approval、不泄 canary | `app_validation` | `blocked` | 11 |
| `T110-PI-02` | connector 响应里的注入文本落为 receipt 数据：不产生新 operation/approval | `schema` | `blocked` | 7 |

**阻断层分布**（JSON `summary.by_blocking_layer`）：

| 层 | 条数 | 说明 |
|---|---|---|
| `os` | **0** | 本票不含 OS/沙箱逃逸；T111 的 Linux 沙箱验收负责 |
| `network` | 1 | 无客户端证书的内部 TLS 握手被关闭（无 HTTP 响应） |
| `proxy` | 4 | connector egress guard：metadata/私网/DNS/302 在拨号前拒绝 |
| `app_validation` | 28 | 真实 ACL/CSRF/attempt token/HMAC/准入/parser/CSRF/渲染器编码 |
| `schema` | 1 | capability receipt 是 schema 约束的数据，不被当作指令消费 |
| `—`（对照/finding） | 2 | `SSRF-05` 显式 opt-in 对照（`observed`）；`CSV-05` finding 不冒充被阻断 |

状态分布：`blocked` 33、`partial` 1、`finding` 1、`observed` 1。

## 3. 逐 AC 对照

| AC | 结论 | 证据 |
|---|---|---|
| #111 AC1：IDOR/伪主体/token/thread 被拒 | **满足** | `T110-ID-01..15`：另外主体对 A 的 run/artifact/approval/knowledge/memory/conversation 读写全部 404，且与随机 UUID 同码；匿名 401；伪造 Bearer/cookie/principal 头、伪造内部 attempt token、错误内部 role、篡改 signed link 全部拒绝；admin 读 approval 200/决定 403 为文档化边界 |
| #111 AC2：SSRF/重定向/恶意文档及导出注入受阻 | **partial**：SSRF/DNS/redirect/恶意文档全部阻断；CSV 公式注入被真实 parser 阻断（七种前缀）；但**导出器对 TAB/CR 前缀未转义**，产生结构歧义（finding，未修） | `T110-SSRF-01..04`、`T110-DOC-01..07`、`T110-CSV-01/02`；缺口见 `T110-CSV-04/05` 与 §7 |
| #111 AC3：XSS 不执行 | **满足（限定手段）**：真实 API 的 payload 以文本进入 committed renderer；没有创建 script/iframe/object/embed/img，没有 `innerHTML`/`insertAdjacentHTML`/`document.write`，没有 `on*` 属性或 `javascript:` URL，harness 里无脚本执行；shim 的正对照证明它能发现 `innerHTML` sink。**这不是真实浏览器执行结果** | `T110-XSS-01` + `T110-XSS-02`；见 §5.1 |
| #111 AC4：每个阻断点有真实层次证据 | **满足** | 36 条记录均写 `blocked_at`（或 finding/control 明确无阻断层）、响应 status/code/request_id/body sha256、独立真值（PostgreSQL 行、sealed 字节、端点请求日志、TLS 异常）；`T110-EVID-01` 再校验 |
| #111 AC5：端到端演示 + 失败/授权/恢复边界 + 版本/hash/命令/结果/profile；stub/real 分报 | **满足** | 本报告 §1/§6/§7、JSON 的 `environment`/`product_commit`/`input_sha256`/`commands`/`assertions`/`not_run`；预置工具与知识检索走真实进程，模型/浏览器标注 `not_run` |

## 4. 关键独立真值（节选，完整见 JSON）

- **IDOR**：B 读 A 的 run → `404 resource_not_found`（A 自己 200；DB `platform.runs.owner_id` 仍为 A，state=`running`，B 的 cancel 未改变）。B 读 A 的 approval → `404 approval_not_found`；admin 读 200、决定 `403 approver_mismatch`，DB approval 仍 `pending`（`params_sha256=99287ef0…`，expires 21:44）。B 读 A 的 conversation/提交到 A 的 thread → `404 resource_not_found`，DB `deleted_at` 仍 NULL。
- **内部协议**：伪造 attempt token → `403 stale_attempt`，`platform.operations` 7→7、DNS 端点请求数不变；无客户端证书的 TLS 连接被关闭（`RemoteDisconnected`，无 HTTP 响应），真实 maintenance 证书 `/health/live` 200；role 头与证书不一致 → `403 forbidden`。
- **SSRF**：metadata → `422 connector_target_forbidden`（"link-local"），操作 `failed` 且 `receipt_artifact_id=NULL`；loopback allow_private=false 与 `localhost` DNS 拒绝时端点请求日志为空；302 → `422 connector_redirect_refused` 且端点恰 1 次请求、未跟随到 metadata；对照 `allow_private=true` 成功且端点恰 1 次真实请求。
- **恶意文档**：macro/traversal/entry-bomb 真实上传后 artifact `quarantined`，admission `rejected` 原因分别为 `xlsx_macro_present`/`xlsx_path_traversal`/`xlsx_entry_limit_exceeded`，无 document 行；ratio bomb 通过准入但文档 `failed` + `ingest_archive_limit_exceeded`（detail `compression_ratio`, value 1048849）、0 chunk；DTD → `ingest_dtd_not_supported`；seeded traversal → `ingest_archive_path_traversal`；encrypted PDF → `pdf_encrypted`；全部 0 chunk、无 `ready`。
- **XSS**：真实 `GET /api/v1/memories/{id}`（body sha256 `693bf20a…`）与 `GET /api/v1/memory-candidates`（`bd1a2352…`）里 payload 原样返回；committed `memories.js`（sha256 `30636e4a…`）渲染后 `inner_html_count=0, attribute_count=0, handler_property_count=0, url_property_count=0, script_executions=0, control_inner_html=1`。
- **CSV**：合法运行真实导出字节 = `region,total\n东区,200.00\n西区,50.00\n`（sha256 `bb60f942…`），`csv_risky_cells=[]`；七种恶意 region 的真实 tool_run 全部 `failed/formula_not_supported/business/attempts=1` 且无 `tool_artifact`（诊断含 row/column；CR 前缀的物理行号为 3）；committed 导出器对 `= + - @` 输出 `'` 前缀（sha256 `8df8d829…`）。
- **提示注入**：注入文档真实检索（1 个 item，text sha256 `c14c7b8f…`）并 answer（`extractive_stub/1`, `stub=true`, `model_provider=not_configured`）；`platform.operations` 7→7（delta 0）、`platform.approvals` 1→1（delta 0）；文本中只出现文档自带的 `ORCHARA_T110_CANARY` 字面量，canary **值**不出现；connector 响应注入文本落为 receipt（sha256 `a314fd19…`），该 operation 唯一且 succeeded，未产生新 operation/approval。

## 5. XSS / CSV / 提示注入的诚实结论

### 5.1 XSS（执行了真实页面字节，但不是浏览器）

- 执行对象：`apps/web/src/assets/memories.js` 的 `renderMemory`/`renderCandidate`（真实 committed 字节，sha256 `30636e4a…`）；输入来自**真实 API** 的 memory 与 memory-candidate 响应。
- 观察：payload（`<img onerror>`、`<script>`、`<iframe javascript:>`、`<svg onload>`、`javascript:`）作为 `textContent` 文本出现在 DOM 文本节点中；没有 HTML 解析 sink 调用，没有创建可执行/嵌入标签，没有设置属性、`on*`、`src/href`，没有导航/fetch，harness 内无脚本执行。shim 的正对照（故意对 shim 元素赋 `innerHTML`）被记录为 1 次 sink，证明观察不是“瞎的”。
- **边界**：Node `vm` + 最小手写 shim，不是 Playwright/Chromium；不证明浏览器布局、CSS、浏览器 HTML parser 行为或真实事件循环。`canvas.js` 是生成的前端 bundle，未在 harness 中执行，其页面列为 `not_run`；第一方资产/`apps/web/canvas/src/**` 另做静态 sink 审计（无 first-party `innerHTML/outerHTML/insertAdjacentHTML/document.write(`）。
- 因此结论是“**在真实 API 数据 + committed 渲染字节下，未观察到执行路径**”，不是“浏览器已证实不执行”。

### 5.2 CSV 公式注入

- **公式注入被阻断**：真实 tool_run 对 `=`、`+`、`@`、`-`、`space+=`、`TAB+=`、`CR+=` 七种 region 全部 `formula_not_supported`（business，1 次尝试，无 summary.csv 导出），诊断定位 row/column（CR 前缀为物理行 3）。
- **合法导出字节已读**：真实导出的 `summary.csv` 解析为两列、无任何 `= + - @ \t \r` 开头 cell，金额是 Decimal 字符串。
- **committed 导出器防御纵深**：`_write_outputs` 对 `= + - @` 加 `'` 前缀；**TAB/CR 原样输出**。
- **finding（未修，按票面“发现 bug 报告”处理）**：真实可达的 region `"\rCR"` 在 `summary.csv` 中裸 CR 输出，Python `csv` 解析为 `[['region','total'],['\tTAB','10.00'],[],['CR','20.00']]`——CR 前缀会被某些解析器当作换行，导致行结构歧义。无公式执行；`= + - @` 公式字符因 parser `lstrip` 先拒绝而不可达。位置：`worker/platform_worker/nodes/sales_stats.py::_csv_text_field`（只检查首字节 `= + - @`，且写入器 `lineterminator="\n"` 不 quote CR）。**本票不改产品语义**，建议后续票修复/回归。

### 5.3 提示注入（平台层）

- 知识文档/工具结果中的“忽略指令、调用写工具、泄露系统提示/密钥”文本被真实检索、真实答案引用，但平台层**没有**产生 capability operation、approval 或外写（delta 0/0）；API 进程 canary 值不出现，除文档自带的变量字面量外无其它 `ORCHARA_` 标记；answer 响应结构没有 tool call/operation 成员。
- 工具结果注入只成为 receipt JSON 的 `body.note` 数据（schema 约束的 receipt 文档，无 `tool_calls/operations/commands/actions` 成员），不产生额外 operation/approval。
- **blocked/not_run**：dev-stub 下不存在可被注入操纵的语义模型/tool 执行面，因此“真实模型服从注入并调用写工具”在宿主上**无法构造**；官方 AgentDojo suite 属 T105，本票不冒充。本票只证明平台层结构性阻断。

## 6. 门禁命令与原始 tail（合并后树 `40e54cc`）

```text
$ python -m pytest tests/f13b/test_t110_security_regression.py -q        # F13B_WRITE_EVIDENCE=1
...............                                                          [100%]
15 passed in 41.24s

$ python -m pytest tests/f13b/test_t110_security_regression.py -q        # 普通运行，不写证据
...............                                                          [100%]
15 passed in 31.95s

$ python -m pytest tests/f13b -q
........................................................................ [ 34%]
........................................................................ [ 68%]
.................................................................        [100%]
209 passed in 448.58s (0:07:28)

$ python -m pytest tests/f12 -q
........................................................................ [ 16%]
........................................................................ [ 32%]
........................................................................ [ 48%]
........................................................................ [ 64%]
........................................................................ [ 80%]
........................................................................ [ 96%]
...............                                                          [100%]
447 passed in 344.78s (0:05:44)

$ go build ./...            -> exit 0（无输出）
$ go vet ./...              -> exit 0（无输出）
$ go test ./...             -> exit 0（39 个包 ok，0 FAIL；12 个 no test files）
$ gofmt -l internal cmd tools -> 空
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

合并前同一代码树（`5c9102d`，已含本票测试）也跑过：`pytest tests/f13b` → **201 passed in 190.05s (0:03:10)**；`pytest tests/f12` → **447 passed in 293.47s (0:04:53)**。合并后新增 T108/T109 用例，故 f13b 由 201→209。

原始日志 tail：`E:\Orchara\.scratch\f13b\T110\`（`t110-targeted-1/2.log`、`t110-evidence-final.log`、`gate-f13b-prefmerge/final.log`、`gate-f12-prefmerge/final.log`、`gate-build/vet/go-test-final.log`、`gate-gofmt-final.log`、`gate-sync-spec-final.log`、`evidence-facts.txt`、`attack-table.md`）。

## 7. `not_run` / 残留风险

| 项 | 状态 | 原因/边界 |
|---|---|---|
| 真实浏览器（Playwright/Chromium）执行页面 | `not_run` | 本机无浏览器 DOM；Node+shim 只证明 committed 渲染字节的 sink/文本行为 |
| 真实第三方 connector 供应商 | `not_run` | loopback 端点代替部署定义端点；egress guard 本身是真的 |
| 真实厂商模型服从/拒绝注入 | `blocked`/`not_run` | dev-stub，无凭据/端点；answer 是 extractive stub，无 tool 执行面 |
| Excel/LibreOffice 打开导出 CSV | `not_run` | 只读取并独立解析真实字节；解析器行为不代表 Excel |
| 带活动 DNS 的 rebinding | `not_run` | 只测静态解析校验与 address pinning |
| 画布页 `canvas.js` 生成 bundle 的 XSS | `not_run` | 未在 harness 执行；其源码（`apps/web/canvas/src/**`）静态审计无 sink |
| OS/沙箱隔离（脚本读环境/宿主/出站） | 不在本票 | T111（Linux 沙箱）验收；本票 `os` 层 0 条 |
| CSV TAB/CR 导出转义 | **finding（未修）** | `_csv_text_field` 只处理 `= + - @`；`\r` 前缀真实可达并破坏行结构；见 §5.2 |
| Signed link 是 bearer 能力 | 文档化边界 | 拿到真实短时签名 URL 的任意客户端在 TTL 内可取字节（实测无 session 也 200）；API download 路由仍 owner-only；立即撤销用代理下载（SECURITY §5） |
| `admin` 可读 approval proposal | 文档化边界 | admin 读 200、决定 403 `approver_mismatch`（不是自动审批人）；个人 memory 对 admin 仍 404 |

**环境/宿主限制**：本机是 Windows + 一次性 PostgreSQL；证据中的 API/维护进程均为真实二进制与真实 socket。无真实厂商凭据、无浏览器、无 GPU。
