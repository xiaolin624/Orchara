# F05 T030 验收：预置销售统计工具、Decimal 结果与具体输入错误

- 任务：F05 / GitHub Issue #31（票 `T030`，规划 ID F05.02），分支
  `feat/f05-f03-run-and-tools`，基线 `52c1f63`（T029 docs）+ T028/T029 代码。
- 状态：**`passed`**（本票范围 = **可信预置工具执行 + Decimal 统计 + 具体输入错误 +
  用户可见的运行/下载/诊断路径**）。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP +
  真实文件系统对象字节 + 真实 HMAC 签名 URL + 真实 `cmd/maintenance run --once` 双池
  （`tool_run` work pool + `artifact_finalize` system_io pool）+ 真实 Python 3.12 子进程；
  无 mock、无跳过、无 stub 结果。
- 主要文件：
  - `db/migrations/0017_tool_run_kind.sql`（guarded、幂等扩展 `background_jobs_kind_check`，
    只加入第九个 kind `tool_run`；无新表、无新业务行）。
  - `worker/platform_worker/nodes/sales_stats.py`（Python 3.12 stdlib-only CSV/XLSX Decimal 工具）。
  - `internal/tools/`（固定 argv 执行器 + 临时 workspace + 只读输入 + 严格 output.json /
    产物准入 + 超时/日志上限）。
  - `internal/toolruns/`（幂等创建、owner-only 读模型、receipt+job 同事务）。
  - `internal/api/{toolruns.go,server.go}`（`POST/GET /api/v1/tool-runs`，非契约桥）。
  - `internal/background/{job.go,complete.go,artifacts.go,handler.go}`、
    `internal/background/handlers/tool_run.go`（kind/retry/producer purpose/完成守卫/handler）。
  - `internal/config/config.go`、`cmd/maintenance/{main.go,run.go}`（`ORCHARA_PYTHON`、
    `ORCHARA_PRODUCT_ROOT`、`ORCHARA_MAINTENANCE_TOOL_TIMEOUT` 与 `--tool-timeout`）。
  - `apps/web/src/attachments.html`、`apps/web/src/assets/attachments.js`（工具区、Decimal 表、
    下载、诊断；无内联用户内容）。
  - `tests/support/{t030.py,fixtures.py}`、`tests/f05/test_t030_*.py`（6 文件 55 用例，其中
    36 个是 Python 工具真实子进程用例）。
  - `docs/adr/0005-t030-tool-run-bridge.md`（非契约桥与执行边界的决策记录）。
- 版本/环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）；Python 3.12.2；
    Node 24.13.0；PostgreSQL 16.4（隔离开发集群 `127.0.0.1:55432`，`tools/pg_dev.py`）。
  - profile `dev-stub`；对象目录与签名 key 在测试 `tmp_path` 内、每测试随机生成；不落仓库。
  - 分支基线 `52c1f63`；实现提交 `c15ef62`（feat）、`defd4c4`（test）、本报告所在 docs 提交。

## 0. 工作记录（简短）

1. 读共享 NOTES、T030 brief/票面、WORKFLOW-SPEC §5–6、COMPILER-CONTRACT §1/§3、SECURITY §2/§4、
   BACKGROUND-JOBS §1–2、IMPLEMENTATION-DETAILS §6/§9、示例与现有 T022/T024/T026/T028/T029 代码。
2. 先写 Python 工具测试（红，36 用例）→ 实现 `sales_stats.py` → 36 passed；覆盖示例 CSV/XLSX、
   Decimal 精确性、公式/宏/DTD/zip caps/行/单元格/工作表 caps、产物只写 artifacts。
3. 写 Go 执行器测试（红）→ 实现 `internal/tools`：固定 argv、裁剪环境、只读输入、超时、
   strict output.json、成功/拒绝两形状、产物准入；`go test ./internal/tools` 全绿。
4. 迁移 0017 + background kind/retry/purpose/完成守卫；API `internal/toolruns` + 路由；handler；
   maintenance 配置/双池接线；Go 单测。
5. 写真实栈 support/tests：上传→创建→maintenance 双池→GET→下载/拒绝/超时/IO/迁移/ACL/幂等。
   首轮 f05 84/85（timeout 用例错误假设“一进程一 attempt”），改为按“持久状态序列 + 固定 1s backoff”
   断言后 85/85；随后补“最大合法工作表不被 cell cap 拒绝”的用例（36 个 Python 用例），最终 86/86。
6. 扩展附件页与 Node 行为测试；跑门禁；写 ADR/报告/PROGRESS/NOTES；按 feat/test/docs 提交。

## 1. 交付内容

### 1.1 迁移 `0017_tool_run_kind.sql`

- 只做一件事：`DROP CONSTRAINT IF EXISTS background_jobs_kind_check` 后重建，加入
  `tool_run`，仍保持九值封闭列表；无新表、无列、无索引、无业务行、无权限变化。
- 迁移头列出非契约扩展、owner（T030；T033/F04A/T037 后续）与边界；重复 `migrate` no-op；
  `orchara_worker`/PUBLIC 仍无 `platform` 权限。
- 六处既有迁移链尾断言（T023/T025/T026/T027/T028/T029 测试）从 0016 适配到 0017。

### 1.2 后台 kind 与完成守卫

- `internal/background/job.go`：`KindToolRun = "tool_run"`、`AllKinds()`/`KnownKind()`；
  retry 表：
  - 可重试（≤3 次）：`transient_storage_io`、`transient_internal`、`lease_expired`、
    `tool_timeout`、`tool_crash`；
  - 工具拒绝码（`missing_column` 等）与 `tool_output_invalid`/`tool_artifact_*` 都是 business；
    未列出的码一律 business。
- `tool_run` 在 **work pool**（`systemIOKind` 只认 `artifact_finalize`），因此 handler 等待自己的
  summary/log 被封存时，system_io 池仍能继续跑 finalize。
- `internal/background/artifacts.go`：producer 集合加入 `tool_run`；job-scoped 上传 purpose 加入
  `tool_summary`（结果 artifact）、`tool_artifact`（CSV）、`diagnostics`、`log`。
- `complete.go`：`guardToolRunCompletion` 在成功时必须给出 result artifact，并复用
  `verifyJobResultArtifact` 在同一事务里检查它属于本 job/owner、未删除、ready；失败 job 的
  result artifact 由 `Complete` 清空，诊断/日志永远不会被激活为结果。

### 1.3 公开 API（ADR 0005 记录为非契约桥）

| 端点 | 成功 | 权限/语义 |
|---|---|---|
| `POST /api/v1/tool-runs` | 202 `{id,state,tool,artifact_id}` | 会话 CSRF 或 API key `application:write`；`Idempotency-Key` 必需；body 精确 `{"tool":"sales_stats","artifact_id":"<uuid>"}` |
| `GET /api/v1/tool-runs/{job_id}` | 200 `{id,state,error_code,artifacts:[{id,purpose,state,media_type,size_bytes}]}` | owner-only（他人/管理员/未知/畸形 id 一律 404）；API key 读需 `application:read` |

- 创建：artifact 必须本人、ready、未删除、sha256/size 有效、media type 为 text/csv 或 XLSX；
  `expected_revision`=artifact 当前 revision；receipt 与 `background_jobs(kind=tool_run)` 同一事务；
  dedup key `tool-run:<tool>:<artifact>:<revision>:<server nonce>`。幂等由 receipt 负责：同 key
  回放已存 body；新 key 是新的运行意图（同一 artifact revision 可重跑，例如瞬时失败后）；job 的
  expected_revision 仍防止旧 revision 结果套在新输入上。
- 幂等：同 key+同请求回放已存 202 body；同 key+不同 body 409 `idempotency_conflict`；
  artifact 未 ready 409 `artifact_not_ready`；非本人/已删/畸形 id 404 `resource_not_found`；
  非 CSV/XLSX 422 `validation_failed`（`details.field=artifact_id`）。
- GET 的 artifacts 只 join `platform.background_job_artifacts` 的 `relation='output'`，不含
  object key、staging key、其他用户数据。

### 1.4 执行器 `internal/tools`

- 固定 argv：`[python, <product-root>/worker/platform_worker/nodes/sales_stats.py, --input,
  <workspace>/input.json, --output, <workspace>/output.json, --inputs-dir, <workspace>/inputs,
  --artifacts-dir, <workspace>/artifacts]`；无 shell、无用户参数；python/脚本路径来自部署配置。
- 每次临时 workspace（0700）：写 `input.json`、复制唯一授权输入到 `inputs/input.csv|input.xlsx`
  并设只读；运行结束递归 chmod 后删除。
- 环境裁剪为 `PYTHONHASHSEED`/`PYTHONIOENCODING`/`PYTHONUTF8`/`PYTHONDONTWRITEBYTECODE`
  （Windows 另加 `SystemRoot`/`TEMP`/`TMP`）；不继承 `ORCHARA_*`、DSN、签名 key。
- 超时（默认 30s，`ORCHARA_MAINTENANCE_TOOL_TIMEOUT`/`--tool-timeout`，≤10m）杀死子进程；
  stdout/stderr 各 64 KiB 截断，合并、脱敏（workspace/product 路径替换）后只作 log artifact。
- `output.json` 严格解析：
  - 成功 `{"data": object, "artifacts": [相对路径...]}`，未知顶层键/非对象 data/尾随数据拒绝；
  - 退出码 3 + `{"error":{code,message,fix,location?}}` 是结构化拒绝，code 必须在固定词汇表内；
  - 其他退出码是 crash（可重试）。
- 产物准入：相对路径、无盘符/绝对路径/空段/`.`/`..`、位于 artifacts 目录内、Lstat 普通文件
  （拒绝 symlink/directory）、非空、≤20 MiB、≤16 个、读取时 SHA-256；`output.json` 未列出的文件
  永不登记。
- 销售 data 的 Schema/Decimal 一致性由 handler 校验（total/regions 两位小数字符串、total 等于
  region 之和、order_count 正整数），且 summary.json 的规范化字节必须等于 `output.json.data`。

### 1.5 Handler 与 Python 工具

- `internal/background/handlers/tool_run.go`：
  job-scoped Status/Download/FetchURL 取输入 → 执行器 → 校验 data/产物一致性 →
  上传 `tool_summary`/`tool_artifact`/`log` 并逐个等待 ready → 成功提交 summary artifact；
  工具拒绝/超时/崩溃/IO/输出违规时上传安全 `diagnostics`（+log）并失败；失败 job 永远没有
  result artifact。诊断含 `error_code/message/fix/retryable/input{artifact_id,media_type,size_bytes,
  sha256}/location?/log_artifact_id?`，不含正文、绝对路径、object key 或 secret。
- `sales_stats.py`：stdlib only（csv、zipfile + ElementTree、decimal、json、argparse），不读环境、
  无网络、无子进程。CSV 与 XLSX 都输出示例真值
  `{"total":"250.00","regions":{"东区":"200.00","西区":"50.00"},"order_count":3}`；
  金额全程 Decimal（0.1+0.2 → 0.30；250.00 保留两位），从不 float。
  拒绝码覆盖票面：缺列/多列/乱序、重复订单、负值、非十进制、空地区、非 ISO 日期、公式、
  非 UTF-8、宏、DTD/实体、zip 穿越/条目/解压/压缩比、工作表/行/单元格上限（单元格上限 =
  10000 数据行 × 四列 + 表头）、错误单元格类型。
  CSV/XLSX 都在写 summary 前再次校验产物路径，拒绝时只写结构化 output.json，不写 summary 产物。

### 1.6 Web

- 附件页新增“预置销售统计工具（sales_stats）”区块：artifact id、运行按钮、中文状态
  （ready=排队中、leased=运行中、succeeded=成功、failed=失败）、Decimal 总额/地区表、
  `summary.csv`/`summary.json` 下载、失败诊断（code/message/fix/location，含 retryable 提示）。
- 所有值用 `textContent`；页面无内联脚本/用户内容、无 localStorage/sessionStorage、无查询串；
  金额不经过 `Number()`/`parseFloat`；日志只提示“作为受控附件保存”，不在页面展示正文。

## 2. 票面验收标准逐条

| # | 验收标准 | 状态 | 证据 |
|---|---|---|---|
| 1 | 缺列/重复订单/负值/空地区明确拒绝 | **passed** | `tests/f05/test_t030_tool_run_rejections.py`（8 个 CSV 用例 + XLSX 公式/宏）：job `failed`，error_code 分别 `missing_column`/`extra_column`/`duplicate_order_id`/`negative_amount`/`empty_region`/`invalid_date`/`formula_not_supported`/`invalid_amount`/`macro_present`；failure_class=business、attempt_count=1、result_artifact_id=NULL；diagnostics 有 row/column/cell 或安全说明 |
| 2 | 金额公式不执行 | **passed** | Python 单测：CSV `=SUM(1,2)`、`=1+1` → `formula_not_supported`；XLSX 任意 `<f>` → `formula_not_supported`（D2）；工具源码无 eval/exec/网络/环境读取；失败日志无输入正文 |
| 3 | 输出路径穿越/符号链接/超限被拒 | **passed** | Go 执行器测试（真实子进程 + 真实文件）：`../x`/绝对路径/`a/../b`/盘符 → `ErrArtifactEscape`；symlink → `ErrArtifactSymlink`；目录/缺失/空/重复/超 4 字节上限 → 特定错误；`output.json` 未列出的文件不登记；工具自身写 summary 只用固定文件名并再次校验 artifacts 目录 |
| 4 | 通过端到端入口实际演示；失败、当前授权与重试/恢复边界有断言，不以单层 mock 成功代替 | **passed** | 真实栈：T028 上传（CSV 与 XLSX）→ `POST /tool-runs` → `cmd/maintenance run --once` 双池 → `GET /tool-runs` → `GET /artifacts/{id}/content` 逐字节/逐 Decimal 断言；另测超时（1ms 注入）重试 3 次后 failed+infrastructure、输入 sealed 字节消失 3 次后 `transient_storage_io`、tombstone 后 `source_deleted` 不重试、失败后新 key 可重跑、第二用户/管理员 404、API key scope 403、同 key 幂等回放/异 body 409 |
| 5 | 记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件 blocked，未运行 not_run | **passed** | 本报告 §4 记录版本、示例/工具/迁移 SHA-256、命令原始结果与 profile；全部真实路径，无 stub；未运行项在 §6 `not_run` |

## 3. 实现与测试结构（补充）

### 3.1 真实栈测试覆盖

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `tests/f05/test_t030_tool.py` | 36 | 真实 Python 子进程：示例 CSV/XLSX 精确结果、0.1+0.2/尾零、BOM/CRLF、数字金额、sharedStrings；最大合法工作表接受、所有拒绝/caps；源码无网络/子进程/环境读取；默认路径 |
| `test_t030_tool_run_flow.py` | 4 | CSV+XLSX 成功流、summary/csv/log 字节、Decimal 字符串、owner-only、API key scope、同 key 幂等/新 key 重跑、非 ready/非本人/未知工具/错误 media type、GET 不泄露 object key |
| `test_t030_tool_run_rejections.py` | 3 | 10 类真实工具拒绝 → failed + 具体码 + safety diagnostics；失败无 summary；失败后同 key 回放 |
| `test_t030_tool_run_failures.py` | 3 | 超时（重试→失败、infrastructure、diagnostics retryable）、sealed 字节消失（transient_storage_io）、tombstone（business、不重试） |
| `test_t030_tool_run_migration.py` | 5 | 0017 链尾、九值 CHECK、tool_run 可插入/未知 kind 拒绝、repeat migrate no-op、worker/PUBLIC 拒绝、无新表/业务行 |
| `test_t030_pages.py` | 4 | 页面结构（无内联脚本）、JS 语法、Node 行为：create body/headers、状态词汇、poll 规则、Decimal 表、diagnostics、下载路径 |

### 3.2 Go 单测

- `internal/tools/executor_test.go`（15）：固定 argv、stdout 不进结果、log 上限/脱敏、只读输入、
  裁剪环境、结构化拒绝、crash、超时、strict output.json、未知拒绝码、错误 media/tool、
  缺失脚本、产物准入（clean/traversal/absolute/drive/symlink/directory/missing/empty/oversize/
  duplicate/unlisted）。
- `internal/background/handlers/tool_run_test.go`（7）：销售 Schema/Decimal 精确性和边界、
  money cents、错误码映射、失败文案安全、tool_run retry 表/分类。
- `internal/background/background_test.go`：白名单更新为九个 kind。
- `internal/config/config_test.go`（1）：python/product-root/tool-timeout 默认值与上限。

## 4. 证据（实际命令与原始结果）

### 4.1 输入真值与哈希

| 输入 | SHA-256 |
|---|---|
| `examples/sales/input.csv` | `a6be4cf4d34d0fdce677964afc38a6ffa02145d4325b32a9aa0237bad2cd88ca` |
| `examples/sales/input.xlsx` | `287bd26ca0cac1ef630caef40807c0a82739e86285b4343905242979190cdb60` |
| `examples/sales/expected.json` | `b89f841f78a42367cef30eb2072a77ea67e6b7e881c5afd32bc4be6faa122640` |
| `worker/platform_worker/nodes/sales_stats.py` | `0b67e2eb35800eced787efa95cd42596e6501692a97ff31840c0733f7311ec2e` |
| `db/migrations/0017_tool_run_kind.sql` | `4f1f773033e9a0a74db9aa0d1558d24d8a241bd265fbcfc581546c3c26858ee0` |
| `internal/tools/executor.go` | `494ce46d344fcf7fe13a962dc77eaeaa3ed12e84558882f090cac74c8d1ed082` |
| `internal/background/handlers/tool_run.go` | `77bb0bdf8571299623b005da16406ee13ceea49b95aca5188fc911310caa0818` |

示例 XLSX 为真实 zip/XML（第一个工作表、inlineStr），由 stdlib 解析；示例 CSV 的支出真值为
东区 120.50 + 79.50、西区 50.00，总额 250.00。

### 4.2 门禁命令与原始结果

工作目录 `E:\Orchara\product`；Go 在 `E:\orchara-toolchains\go\bin`：

```text
$ python -m pytest tests/f05 -q
........................................................................ [ 83%]
..............                                                           [100%]
86 passed in 196.67s (0:03:16)

$ python -m pytest tests/f05/test_t030_tool.py -q
....................................                                     [100%]
36 passed in 11.46s

$ go test ./... -count=1
?   	orchara/platform/cmd/admin	[no test files]
?   	orchara/platform/cmd/api	[no test files]
?   	orchara/platform/cmd/maintenance	[no test files]
?   	orchara/platform/cmd/supervisor	[no test files]
?   	orchara/platform/cmd/worker	[no test files]
?   	orchara/platform/db	[no test files]
ok  	orchara/platform/internal/api	2.268s
ok  	orchara/platform/internal/artifacts	2.016s
?   	orchara/platform/internal/audit	[no test files]
ok  	orchara/platform/internal/background	1.440s
ok  	orchara/platform/internal/background/handlers	2.172s
ok  	orchara/platform/internal/catalog	1.609s
ok  	orchara/platform/internal/compiler	0.990s
ok  	orchara/platform/internal/config	0.640s
ok  	orchara/platform/internal/db	1.019s
ok  	orchara/platform/internal/execution	1.679s
ok  	orchara/platform/internal/health	1.851s
ok  	orchara/platform/internal/identity	2.209s
ok  	orchara/platform/internal/jcs	1.559s
ok  	orchara/platform/internal/modelproxy	1.913s
ok  	orchara/platform/internal/objectstore	1.616s
ok  	orchara/platform/internal/policy	1.443s
ok  	orchara/platform/internal/supervisor	1.299s
?   	orchara/platform/internal/toolruns	[no test files]
ok  	orchara/platform/internal/tools	3.303s
ok  	orchara/platform/internal/uuid	1.329s
ok  	orchara/platform/internal/workflow	1.371s

$ go vet ./...
(no output)                                    exit=0

$ gofmt -l .
(no output)                                    exit=0

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

**未运行**：25 分钟的全量 `python -m pytest tests -q`（按本批 brief 明确不跑；由 orchestrator
在批次末运行）。`tests/f05` 是包含 T028 + T030 的完整目录，已全绿；`tests/f00b`/`tests/f02a`/
`tests/f03` 的链尾断言已适配 0017，但本票未重跑这三个目录（不属于本票 brief 的门禁）。

### 4.3 真实栈运行命令

测试内部执行的命令（不是 mock）：

```text
cmd/maintenance run --once \
  --kinds tool_run,artifact_finalize \
  --poll-interval 20ms --system-io-slots 2 --work-slots 1 \
  --evaluation-poll-interval 50ms --report-wait-timeout 30s
# 子进程(argv 数组，无 shell):
<ORCHARA_PYTHON> <product-root>/worker/platform_worker/nodes/sales_stats.py \
  --input  <workspace>/input.json \
  --output <workspace>/output.json \
  --inputs-dir <workspace>/inputs \
  --artifacts-dir <workspace>/artifacts
```

超时用例通过 `ORCHARA_MAINTENANCE_TOOL_TIMEOUT=1ms` 注入；重试用例通过
`ORCHARA_BACKGROUND_RETRY_BACKOFF=1s` 使每次 `--once` 只观察一次 attempt，最终 attempt_count=3。

## 5. 偏离 / 契约缺口

1. **非契约桥**：`POST/GET /api/v1/tool-runs` 与 `tool_run` kind 不在冻结契约中（`contracts/openapi.json`
   没有该路径，`contracts/database.sql` kind 只有八个）。本票不修改 `contracts/**`，在 ADR 0005
   与本报告显式登记为 T033/F04A 前的桥；`sync_spec --check` 仍 no drift。
2. **迁移 0017 只改 CHECK**：第九个 kind 是显式偏离；没有新表。T033/F04A 绑定到 Run/沙箱协议时
   可继续使用该 kind 或在新迁移中调整。
3. **新增 job-scoped purpose**：`tool_summary`/`tool_artifact`/`diagnostics` 加入
   `internal/background` 的 job 产物 purpose（契约的 createBackgroundUpload purpose 枚举是
   report/log/receipt/parsed_data）。这是内部 job-scoped 上传职责的扩展，不是用户 upload purpose；
   未修改契约 schema。非 user-upload purpose 不走 T028 格式准入（T024/T026 语义不变）。
4. **OS 只读边界**：Windows 只读是 DOS read-only 属性、POSIX 是 0444；同用户可信工具理论上可
   chmod 回来。没有 gVisor/容器/网络命名空间/CPU/内存 rlimit，只有超时与文件大小上限。真正的
   不可信脚本隔离属 T033，任意工具构建属 T037。报告与 ADR 不把这写成沙箱。
5. **宏 XLSX 的测试入口**：T028 上传准入会先隔离含 `vbaProject.bin` 的 XLSX，公开上传路径无法
   产生“ready 的宏工作簿”。真实栈用例用测试 SQL 播种 ready artifact（行/字节/job/工具执行都是
   真实的），只绕过公开准入以证明工具自身的独立宏拒绝。其余拒绝（缺列、公式等）都走真实上传。
6. **等待时长复用**：handler 等待自己的产物 ready 复用 `ReportWaitTimeout`/`EvaluationPollInterval`，
   没有新增配置；这是在两个既有 job 产物等待参数上的复用，不是新的契约字段。
7. **维护进程必须同时跑两个池**：因为 handler 要等待自己的 artifact finalize，测试与部署需在同一
   进程/部署中运行 `tool_run` + `artifact_finalize`（`--kinds` 可分开进程，但要有 finalize 进程）。
   报告如实记录，不把“只跑 tool_run 的进程能独立完成”写成事实。
8. **迁移链尾断言适配**：T023/T025/T026/T027/T028/T029 的既有迁移测试从 0016 改到 0017；
   只改预期链尾/版本范围，不改这些票的行为断言。
9. **错误码新增**（本票范围内稳定）：`tool_timeout`、`tool_crash`、`tool_output_invalid`、
   `tool_artifact_invalid`、`tool_artifact_path_escape`、`tool_artifact_symlink`、
   `tool_artifact_too_large`、`tool_artifact_count_exceeded`、`tool_input_invalid`，以及工具拒绝
   词汇表（缺列/多列/乱序/重复/负值/非十进制/空地区/日期/公式/编码/CSV/XLSX caps 等）。
   API 层新码：`tool_run_conflict`(409)；复用 `idempotency_conflict`/`idempotency_key_required`/
   `artifact_not_ready`/`resource_not_found`/`insufficient_scope`/`validation_failed`。

## 6. `not_run` / 边界（如实）

- **not_run**：全量 `python -m pytest tests -q`（25 分钟批次门禁，按 brief 不跑）；`tests/f00b`、
  `tests/f02a`、`tests/f03` 目录的完整重跑（只适配/未重跑）；真实浏览器 DOM 驱动（页面逻辑由
  Node 执行，未驱动浏览器文件选择/点击）；CPU/内存/磁盘 rlimit 与网络隔离的系统级证明；病毒查杀；
  真实 supervisor/worker 执行 `tool_run`（本票走 maintenance；F04A/T033 才有 run/worker 协议）；
  任意用户脚本/依赖构建（T037）；正式契约的 tool-run/result/events/SSE/cancel/recovery（T031+）。
- 未 push、未关闭 GitHub issue、未修改 `docs/spec/**` 或 `contracts/**`、未 amend 旧提交。
- 平台整体仍不是完整 v1；本票的边界不影响“用户现在可以真实上传 CSV/XLSX、运行预置工具并拿到
  Decimal 结果/下载/安全诊断”的事实。

## 7. 下一票接口（T031/T033/T037 可依赖）

- **后台 job**：`background.KindToolRun`（`tool_run`）；`AllKinds()` 第九项；`retryableByKind`
  固定表（tool_timeout/tool_crash/transient_storage_io/transient_internal/lease_expired）；
  `guardToolRunCompletion` 的“成功必须命名本 job ready summary artifact”规则；迁移 0017；
  dedup key 含 server nonce（幂等由 receipt 负责，新 key 可重跑）。
- **执行器**：`tools.New(tools.Options{PythonPath, ScriptPath, Timeout, ...})`、
  `(*Executor).Run(ctx, tools.Request) (tools.Result, error)`；`tools.RejectionError` 携带
  `Rejection{Code,Message,Fix,Row,Column,Cell}`；`ErrTimeout`/`ErrCrashed`/`ErrOutputInvalid`/
  `ErrArtifact*`/`ErrPythonMissing`/`ErrScriptMissing` 可作为未来的 result/finalizer 或沙箱
  adapter 的输入；固定 argv 与产物准入是 T033 沙箱替换时要保持的协议。
- **handler**：`background.HandlerWork.Tools *tools.Executor`；maintenance 的
  `ORCHARA_PYTHON`/`ORCHARA_PRODUCT_ROOT`/`ORCHARA_MAINTENANCE_TOOL_TIMEOUT`/`--tool-timeout`；
  handler 只通过 `JobScope`（Status/Download/FetchURL/Upload/PutURL/Complete）访问数据，不读业务表。
- **artifact purpose**：`tool_summary`（结果）、`tool_artifact`（CSV）、`diagnostics`、`log`；
  T031 若引入正式 result artifact 语义，可复用/改名这些 purpose，但需保持 owner-only 与
  “失败不激活结果”的规则。
- **API**（桥；T033 可替换）：202 create 四字段、200 run 四字段 + artifacts 五字段；
  owner-only 404、scope 403、409 幂等/冲突、422 media/tool、202 的 `tool`/`artifact_id`。
- **UI**：`/attachments` 工具区可由 T033 的 Run 页面替换；`toolRunBody`/`createToolRunRequest`/
  `fetchToolRun`/`fetchToolArtifactJSON`/`summaryRows`/`diagnosticsFields` 是可复用的纯 JS 协议函数。
