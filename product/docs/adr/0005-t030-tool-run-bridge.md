# ADR 0005：T030 预置工具运行的非契约桥与执行边界

- 状态：已接受
- 日期：2026-11-03
- 相关：F05 / T030（GitHub #31，规划 ID F05.02）；分支 `feat/f05-f03-run-and-tools`；
  报告 `product/reports/f05/T030-sales-tool.md`；后续：T031（result/finalizer/events）、
  T033（sandbox runsc / 工具与 Run 绑定）、T037（工具构建与任意脚本）

## 背景

T030 的产品行为是：使用者提交 CSV/XLSX，在界面得到 Decimal 统计、可下载的
summary.json/summary.csv，以及缺列/重复/负值/空地区/公式/超限等具体安全错误。此时 F03 的
result/finalizer/worker 协议（T031+）尚不存在，`contracts/openapi.json` 也没有 tool-run 端点或
tool 执行协议；`contracts/database.sql` 的 `background_jobs.kind` 只有八个值。

若等 F03 完成才给用户路径，本票会退化为探针；若伪造一个 Run，则会把“结果/退出/终态”这些尚未实现
的协议写成已完成。选择是：**复用已经真实运行的 background-job 流水线**，用一个显式的非契约扩展交付
可信预置工具的完整用户路径，并把 F03 的正式绑定明确留给 T033/F04A。

## 决策

### 1. `tool_run` 是第九个 background-job kind（迁移 0017）

- `0017_tool_run_kind.sql` 以 guarded、幂等的方式 drop/recreate
  `background_jobs_kind_check`，加入 `tool_run`；不改任何其他表，不新增业务行。
- Go 白名单 `internal/background/job.go` 同步扩展；未知 kind 仍由数据库 CHECK 与
  `KnownKind` 双重拒绝。
- `tool_run` 属于 **work pool**，不属于 `system_io`：它可能等待自己的产物 finalize，而
  artifact_finalize 必须在独立 system_io 池继续推进（BACKGROUND-JOBS §3）。
- 重试表固定：`transient_storage_io`、`transient_internal`、`lease_expired`、`tool_timeout`、
  `tool_crash` 可重试（最多 3 次）；工具拒绝码（`missing_column` 等）与输出/产物违规码是
  business，立即失败。未列出的码一律 business（fail safe）。
- 这是**非契约扩展**：contracts 文件不修改，`sync_spec --check` 保持 no drift；T033/F04A 把
  预置工具绑定到 Run/沙箱协议后可替换本 seam。

### 2. 公开 API 是非契约桥（POST/GET `/api/v1/tool-runs`）

- `POST /api/v1/tool-runs`：body 精确 `{"tool":"sales_stats","artifact_id":"<uuid>"}`；
  会话需 CSRF，API key 需 `application:write`；`Idempotency-Key` 必需。
  202 body 精确 `{id,state,tool,artifact_id}`，其中 state 是创建时的 job 状态（`ready`）。
- 同一 key + 同一请求返回已存 body；同 key + 不同 body 409 `idempotency_conflict`；artifact
  非本人（含管理员）404 `resource_not_found`；已就绪但 media type 非 CSV/XLSX 422
  `validation_failed`；未 ready 409 `artifact_not_ready`。
- receipt 与 job 同一事务写入；job 的 `target_id`/`input_artifact_id` = artifact，
  `expected_revision` = artifact 当前 revision，dedup key 为
  `tool-run:<tool>:<artifact>:<revision>:<server nonce>`。**幂等由 receipt 负责**：同一
  `Idempotency-Key` 回放已存 body；新 key 是新的运行意图，因此同一 artifact revision 可以重跑
  （例如一次瞬时失败之后）。job 的 expected_revision 仍然保证旧 revision 的结果不会套在新输入上。
- `GET /api/v1/tool-runs/{job_id}`：owner-only（他人/管理员/未知/畸形 id 都是 404），body
  精确 `{id,state,error_code,artifacts:[{id,purpose,state,media_type,size_bytes}]}`；join
  `platform.background_job_artifacts`，不泄露 object key/staging key/其他用户数据。
- 这两个路径只在报告/ADR 记录为桥；不写入 `contracts/openapi.json`，也不改动已有契约路径。

### 3. 可信预置工具执行器（`internal/tools`）

- 固定 argv：`[python, <product-root>/worker/platform_worker/nodes/sales_stats.py,
  --input ... --output ... --inputs-dir ... --artifacts-dir ...]`，**从不经过 shell**，脚本路径与
  Python 可执行文件来自部署配置（`ORCHARA_PYTHON`、`ORCHARA_PRODUCT_ROOT`），不接受用户输入。
- 每次运行一个临时 workspace：平台写 `input.json`，把唯一授权输入复制成 `inputs/input.csv|input.xlsx`
  并设为只读；工具只能写 `output.json` 与 `artifacts/`。
- **OS 边界（如实）**：Windows 用 DOS read-only 属性、POSIX 用 0444 实现“只读输入”。同一用户运行
  的可信工具理论上可 chmod 回来；这是防误写/被攻破的工具写入者的诚实机制，不是 gVisor/容器级隔离。
  真正的不可信脚本隔离属 T033（sandbox runsc）与 T037（工具构建）。
- 进程环境被裁剪为最小集合（`PYTHONHASHSEED`/`PYTHONIOENCODING`/`PYTHONUTF8`/
  `PYTHONDONTWRITEBYTECODE`，Windows 另加 `SystemRoot`/`TEMP`/`TMP`），不继承 `ORCHARA_*`、
  DSN、签名 key 或模型凭据。
- 超时（`ORCHARA_MAINTENANCE_TOOL_TIMEOUT`，默认 30s，上限 10m）杀死子进程；stdout/stderr 各自
  截断至 64 KiB，合并、脱敏（workspace/product 路径替换）后只作为 `log` artifact，**从不解析 stdout**。
- 严格解析 `output.json`：
  - 成功：`{"data": <object>, "artifacts": [<relative path>...]}`，未知顶层键/非对象 data/尾随数据
    一律拒绝；
  - 拒绝：退出码 3 + `{"error":{code,message,fix,location?}}`，code 必须在固定词汇表内；
  - 其他退出码是 crash（可重试），不是拒绝。
- 产物准入：相对路径、无绝对路径/盘符、无空/`.`/`..` 段、位于 artifacts 目录内、Lstat 后必须是
  普通文件（拒绝 symlink/directory）、非空、≤20 MiB、数量 ≤16、读取时重新计算 SHA-256；
  `output.json` 未列出的文件永不登记。
- `data` 的销售 Schema 与 Decimal 一致性由 handler 校验：total/regions 必须是两位小数非负字符串，
  total 必须等于各 region 之和（big.Int 分单位，无浮点），且 tool 的 `summary.json` 规范化后必须
  与 `output.json.data` 完全相同。

### 4. 产物 purpose 与完成守卫

- `internal/background/artifacts.go` 的 producer 集合加入 `tool_run`；job-scoped 上传 purpose
  加入 `tool_summary`（summary.json，完成守卫认的结果产物）、`tool_artifact`（summary.csv）、
  `diagnostics`、`log`。
- handler 上传后等待 artifact 变 ready；成功提交 `SucceededWith(summaryID)`。
- `complete.go` 新增 `guardToolRunCompletion`：成功必须给出 result artifact，且该 artifact
  属于本 job/owner、未删除且 ready（复用 `verifyJobResultArtifact`）。失败的 job 由 `Complete`
  清空 result artifact，因此诊断/日志产物永远不会被当成可用结果激活。

### 5. 工具拒绝、诊断与日志的安全边界

- 工具拒绝码固定且具体：`missing_column`、`extra_column`、`column_order_mismatch`、
  `duplicate_order_id`、`negative_amount`、`invalid_amount`、`empty_region`、`invalid_date`、
  `formula_not_supported`、`invalid_row`、`empty_input`、`invalid_encoding`、`invalid_csv`、
  `field_too_large`、`empty_order_id`、`invalid_xlsx`、`macro_present`、`dtd_not_supported`、
  `archive_limit_exceeded`、`archive_path_traversal`、`worksheet_limit_exceeded`、
  `row_limit_exceeded`、`cell_limit_exceeded`、`invalid_cell_type`、`artifact_path_invalid`，
  以及平台侧 `tool_timeout`/`tool_crash`/`tool_output_invalid`/`tool_artifact_*`/`tool_input_invalid`。
- 失败时上传 `diagnostics`（机器可读：error_code/message/fix/retryable/input facts/location）
  与 `log`；message/fix 是固定安全文本，不含输入正文、绝对路径、object key 或 secret。
- 成功/失败都不把 stdout 当正文；日志只作为受控 artifact 供 owner 下载（页面只提示其存在）。

### 6. Python 工具实现（`worker/platform_worker/nodes/sales_stats.py`）

- Python 3.12、stdlib only（csv、zipfile + ElementTree、decimal、json、argparse），不读环境、
  不开 socket、不执行子进程；金额全程 Decimal，输出 Decimal 字符串，从不 float。
- CSV：UTF-8（可带 BOM）、严格四列表头、逐行校验、拒绝公式前缀、重复 order_id、负值、非十进制、
  空地区、非 ISO 日期、空表头/空数据、行/单元格/字段上限。
- XLSX：zip + XML 读取第一个工作表（含 inlineStr/sharedStrings），拒绝宏、DTD/实体、公式单元格、
  绝对/穿越条目、加密条目、条目数>512、解压总量>64 MiB、单条目>32 MiB、压缩比>100、工作表>64、
  行>10000、单元格>(10000+1)×4；日期/订单号/地区必须是文本单元格，金额可接受数字单元格的原始十进制文本。
- 产物只写 `summary.json` + `summary.csv`，写前再次校验 artifact 路径；拒绝时写结构化 `output.json`
  且不写任何 summary 产物。

### 7. 测试与已知边界

- Python 单测（36）以真实子进程覆盖示例 CSV/XLSX、Decimal 精确性、所有拒绝码、最大合法工作表
  与 XLSX caps；Go 单测覆盖固定 argv/只读输入/裁剪环境/超时/崩溃/output.json 严格解析/产物准入；
  真实栈测试覆盖 T028 上传 → tool-run → maintenance 双池 → 下载/拒绝/重试/超时/迁移/ACL/幂等。
- **未覆盖/未承诺**：CPU/内存/磁盘 rlimit（只做超时与大小上限）；网络拒绝的 OS 级强制（工具源码
  无网络，但执行器不是网络沙箱）；任意用户脚本/工具构建；病毒查杀；Deep XLSX 解析（F13A）；
  正式 Run/result/finalizer/events/SSE/cancel/recovery（T031+）；发布契约的 tool-run 端点
  （T033/F04A 后修订）。

## 影响

- T031 可把 `tool_run` 的 result/退出证据接入统一 finalizer；T033 可把预置工具执行器换成 runsc
  沙箱并把该 kind/端点绑定到 Run；T037 可在 executor 外增加不可信构建/依赖锁。
- 数据库只多一个 CHECK 值；没有新表、没有新权限、没有契约漂移。API 的 tool-run 路径是显式桥，
  可在 T033 后保留为兼容层或按新契约替换。
