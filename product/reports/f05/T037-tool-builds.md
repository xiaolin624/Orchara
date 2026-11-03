# F05 T037 验收：提交脚本依赖，隔离构建并由管理员审核成可共享工具

- 任务：F05 / GitHub Issue #38（票 `T037`，规划 ID F05.04/F05.05），分支
  `feat/f05-f03-run-and-tools`，前置 T033（#34）已完成。
- 状态：**`passed`**（本票范围 = `submitTool` / `listTools` / `getTool` / `reviewTool` +
  非契约 revoke/build-facts + `tool_build` 后台隔离构建 + 完成守卫 + 工具提交/审核页）。
  真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP/mTLS（maintenance/worker/supervisor
  证书）+ 真实对象字节 + **真实 WSL2/runsc(gVisor) 构建** + Node 执行页面逻辑；无 mock、无跳过。
  边界（如实）：F06 拥有 connectors（非空列表被拒绝而非静默接受）；F15A 构建 OCI 镜像，本票的
  `image_digest` 是**内容包摘要而非镜像**；F04A 才拆分真实 worker/supervisor；最小 rootfs 没有
  pip/wheelhouse/setuptools，lock 需要包时安全失败为 `dependencies_unavailable`；浏览器 DOM 驱动
  页面仍 `not_run`（与 F01/T023/T027/T029/T031/T032/T033 同一边界）；全量 `pytest tests` 门禁
  未跑（批次规则，由 orchestrator 跑）。
- 主要文件：
  - `db/migrations/0023_tool_builds_and_review.sql`（`tool_versions` 的非契约构建/审核扩展）
  - `internal/toolversions/service.go`（submit/JCS manifest/list/get/review/revoke/
    build facts/job-scoped build binding）
  - `internal/api/{tools.go,server.go,internal.go}`（契约 operation + 非契约端点 + 内部 binding）
  - `internal/background/{toolbuild.go,job.go,complete.go,claim.go,sweep.go,artifacts.go,handler.go,scope.go}`
    （`tool_build` 认领→building、完成守卫、重试表、failure 对账、artifact purpose）
  - `internal/background/handlers/tool_build.go`（一次构建：binding→下载→沙箱→上传日志/SBOM→result）
  - `internal/sandbox/{sandbox.go,bundle.go,command.go,executor.go}`（`build` 程序、固定 argv、
    build 模式 driver 参数、每请求 timeout）
  - `deploy/sandbox/{build_tool.py,sandbox_exec.sh}`（检查入库的构建脚本 + driver build 模式）
  - `internal/artifacts/{admission.go,upload.go}`（`application/zip` 仅 `purpose=tool_source`）
  - `internal/config/config.go`、`cmd/maintenance/{run.go,scope.go}`（build timeout、sandbox executor、
    tool-build binding 客户端）
  - `apps/web/src/{tools.html,assets/tools.js}`（提交/构建事实/审核队列）
  - `tests/support/{t037.py,fixtures.py}` + `tests/f05/test_t037_{migration,submit,admission,build,
    build_isolation,review,internal,pages}.py`（8 文件 39 用例）；迁移链 0022→0023 适配
    T023/T025/T026/T027/T028/T029/T030/T032/T033/T034/T035/T036 断言
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（真实 `cmd/admin migrate`）
  - Python 3.12.2、Node 24.13.0
  - WSL2 Ubuntu-24.04、`runsc version release-20241118.0`（spec 1.1.0-rc.1）、
    rootfs 身份 `sha256:03c3f647d1d6e68173bfa9df84b87e5d6162d351bfdd55e4c3b208c0b5412ff5`
    （T033 的 `/opt/orchara-sandbox/python-rootfs`，无 pip/setuptools/site-packages）
  - profile `dev-stub`；对象目录在测试 `tmp_path`；部署输入沿用 T025 占位 worker image digest
- 真实/桩分报：**全部 real**（PG/二进制/mTLS/HTTP/对象字节/runsc 构建/Node）。只有测试源 zip、
  schema/lock 文档与恶意钩子源码是 test-owned fixtures；构建、准入、finalize、mTLS、审计均为真实执行。

## 0. 工作记录（简短）

1. 读 NOTES/T037 票面/T031-T038-BRIEFS §T037、WORKFLOW-SPEC §5/§6、SECURITY §2/§4/§6、
   BACKGROUND-JOBS §1–2、STATE-MACHINES §5、IMPLEMENTATION-DETAILS §2/§10、
   OPEN-SOURCE-REUSE、T025/T028/T030/T033 报告与 `contracts/openapi.json` 的四个 operation、
   `contracts/database.sql` 的 `tool_versions`，确认 0022 为最新迁移、树干净。
2. 先降风险：先用真实 runsc 手工跑通 `build_tool.py`（固定 argv、读 source/lock、写
   build.json/sbom/log），再写 Go 协议层；随后 TDD 切片：
   - 0023 迁移/重放/权限/约束（红：链尾仍是 0022）；
   - zip 准入：purpose 允许清单 + 恶意归档拒绝（红：`InspectUploadForPurpose` 不存在）；
   - submit：owner/ready/purpose/media/schema/lock/risk/connectors 校验 → 202；同 owner 幂等、
     跨 owner `tool_conflict`（红：无 endpoints）；
   - 真实构建：`cmd/maintenance run --kinds tool_build,artifact_finalize` 领取→building→runsc→
     ready，SBOM/log 字节可下载；
   - 恶意钩子/崩溃/依赖/源变更竞态；review/revoke + T025 共享 vs 私有规则；
   - 页面结构 + Node 执行 `tools.js`。
3. 跑门禁（§3），写本报告/PROGRESS/NOTES，按 feat/test/docs 提交；未推送、未关 issue、未改
   `docs/spec/**`/`contracts/**`、未改迁移 0001–0022。

## 1. 交付内容

### 1.1 迁移 `0023_tool_builds_and_review.sql`

只对既有 `platform.tool_versions` 做 guarded `ADD COLUMN`/`ADD CONSTRAINT`/`CREATE INDEX`，
不新建表、不改契约列/状态枚举、不改 CHECK `state <> 'ready' OR image_digest IS NOT NULL`：

| 列 | 语义 |
|---|---|
| `revision` | 工具行 CAS（submit=1；review/revoke/build guard 用；契约投影不含它） |
| `source_sha256` | 提交时 admitted source artifact 的 sha256；构建守卫再次核对 |
| `dependency_lock_sha256` | `dependency_lock` 的 RFC 8785 JCS 字节 SHA-256 |
| `build_log_artifact_id` / `sbom_artifact_id` | job-scoped `build_log`/`sbom` artifact；守卫要求 ready+job-linked |
| `build_bundle_digest` | 内容包摘要（见 §1.4），与 `image_digest` 同值 |
| `build_started_at` / `build_finished_at` / `build_error_code` | 构建事实与安全失败码 |
| `reviewed_by` / `reviewed_at` / `review_decision` / `review_reason` | 管理员决定；`decision ∈ approved/rejected` |
| `revoked_at` / `revoked_by` / `revoke_reason` | 非契约撤销事实 |

- 全部列/约束/索引/COMMENT 都明确标注为 **non-contract extension**（ADR 0005）；契约列与状态
  枚举未变；`sync_spec --check` 仍只比较生成契约文件。
- 约束 NULL-tolerant（既有 T025/T027/T030/T033 的 ready seed 不含构建事实，必须继续可用）；
  `build_bundle_digest` 非空时必须是 `sha256:<64 hex>`；`review_decision` 闭集；
  `state='revoked'` 必须带 `revoked_at`；reason/error 有界。
- `orchara_worker`/PUBLIC 仍无 `platform` USAGE/表/序列权限；重复 migrate no-op。

### 1.2 `POST /api/v1/tools`（`submitTool`）

- 认证/授权：会话必须 creator 角色；API key 必须 `application:write`（镜像
  `requireApplicationWrite`），且源 artifact 必须属于该 caller。
- body 精确契约 `ToolInput`（unknown field 400）：`source_artifact_id` UUID；`input_schema`/
  `output_schema`/`dependency_lock` 必须是 JSON 对象；`risk_class ∈ pure/read/write`；
  `connector_version_ids` 必须为空（F06 拥有 connectors；非空 422，不静默丢弃）。
- 源 artifact：存在、caller 自有、未删除、state=ready、purpose=`tool_source`、
  media_type=`application/zip`、有 64-hex sha256；不满足分别 404/409 `artifact_not_ready`/422。
- 内容身份：`manifest_sha256` = RFC 8785 JCS 的
  `{connector_version_ids:[], dependency_lock, dependency_lock_sha256, input_schema,
  output_schema, risk_class, source_sha256, tool_manifest_version:"1.0"}` 的 SHA-256；不含
  数据库分配的 tool id（内容寻址，使重提交可识别、跨 owner 相同内容可冲突）。
- 单事务：`INSERT tool_versions(state=submitted, revision=1, source_sha256, lock_sha)` +
  `background.EnqueueTx(kind=tool_build, target=tool id, expected_revision=1,
  expected_sha256=source sha, input_artifact_id=source)`；dedup key `tool-build:<tool id>`，
  `UNIQUE(kind,dedup_key)` 保证一个工具一个构建 job。
- 幂等/冲突：同 owner 同内容 → 202 同 tool id/状态，无第二个 job（若既有 job 已结束也不重建）；
  另一 owner 相同内容 → 409 `tool_conflict`（`manifest_sha256` 全局唯一）。
- 202 `Accepted {id,state}`；`state` 是当前工具状态（submitted/…）。

### 1.3 `application/zip` 的 purpose-scoped 准入（T028 扩展）

- 声明阶段：`NormalizeUploadInput` 按 purpose 选择 allowlist；`application/zip` 只对
  `purpose=tool_source` 合法，`task_input`/`knowledge`/`evaluation_dataset` 仍在 create 阶段 422
  `validation_failed`（`details.field=media_type`）。
- finalize 阶段：`InspectUploadForPurpose` 先嗅探真实字节（zip magic），再只读 central
  directory 检查：entry ≤512、总解压 ≤64 MiB、压缩比 ≤100、拒绝绝对路径/`..`/反斜杠/NUL、
  symlink entry、encrypted entry、非 Store/Deflate 压缩、空归档；不执行、不解压内容。
- 拒绝时 artifact 进入 `quarantined`，`artifact_admissions.reason` 是安全码
  （`zip_path_traversal`/`zip_symlink_entry`/`zip_ratio_exceeded`/`zip_entry_limit_exceeded`/
  `zip_uncompressed_limit_exceeded`/`zip_encrypted_entry`/`zip_unsupported_compression`/
  `zip_invalid_archive`/`zip_empty_archive`）；quarantined artifact 对 submit 与缺失一样 404。
- 既有 text/csv/json/xlsx 规则和测试保持不变；`XLSX` 声明配 zip 字节仍是 content-type mismatch。

### 1.4 隔离构建（`tool_build` handler + runsc）

- 构建在 T033 的同一最小 Python rootfs/gVisor bundle 中运行：
  - 固定 argv：`/usr/bin/python3.12 /tool/build_tool.py --source /inputs/source.zip
    --lock /inputs/lock.json --output /workspace/build.json --log /workspace/build.log
    --artifacts-dir /workspace/artifacts --work /workspace/work`；
  - 只读 rootfs、`/tool`、`/inputs`；可写 `/workspace`；uid/gid 65534；无 caps/no_new_privs；
    pid/net/ipc/uts/mount namespaces；`--network=none`；CPU/内存/PID/timeout；无宿主目录/DSN/
    Docker socket/平台密钥；
  - handler 只通过 job-scoped binding 取 source hash + canonical lock，通过 signed link 下载源；
  - `build_tool.py` 安全解包（再次检查 entry/解压/ratio/路径/symlink/encrypted/重复名/真实
    header 大小），语法 `compile()` 每个 .py（不执行），若有 `setup.py` 则以 stripped env
    运行 `python setup.py build`（不可信钩子只在沙箱内执行）；无 setup.py 也产出源文件 SBOM；
  - lock 声明非空 `requirements`/`packages`/`dependencies` → `dependencies_unavailable`（最小
    rootfs 无 pip/wheelhouse；F15A 的镜像构建才提供 locked wheelhouse），绝不伪成功；
  - 成功产物：`/workspace/artifacts/sbom.json` + `build_log` + `/workspace/build.json`；
    handler 校验 build.json 的 source/lock hash、上传 `sbom`（result artifact）与 `build_log`
    （均 job-scoped、ready 后才提交 result）；失败时上传 capped log + safe diagnostics 文档。
- 失败码（安全闭集）：`build_source_invalid`、`build_source_changed`、`dependencies_unavailable`、
  `build_failed`、`build_sandbox_unavailable`、`build_timeout`、`build_crashed`、
  `build_output_invalid`、`build_artifacts_invalid`、`build_artifact_invalid`；重试表只有
  `transient_storage_io`/`transient_internal`/`lease_expired`，其余业务失败不重试。
- 完成守卫 `guardToolBuildCompletion`：claim 同事务把 submitted→building；只有守卫写 ready。
  锁 tool 行后要求 state=building、revision=expected、source artifact/hash 未变、源仍 ready、
  SBOM 是本次 completion 的 result 且 ready+job-linked、最新 `build_log` ready+job-linked；
  重读 sealed source/SBOM + canonical lock 并计算：
  `build_bundle_digest = image_digest = sha256:<hex>`，其中 hex =
  SHA-256(RFC 8785 canonical JSON array
  `[{"role":"source","sha256":...},{"role":"lock","sha256":...},{"role":"sbom","sha256":...}]`)。
  角色标签使摘要具单射性（两份 payload 互换角色必然改变摘要）。
  **这是内容包摘要，不是 OCI 镜像**（F15A 构建镜像）。CAS 失败/源变更 → `build_source_changed`
  且绝不 ready；失败只记录安全 `build_error_code`，retryable 时保持 building 等下一次 claim；
  过期 lease 耗尽由 sweep 对账为 failed。

### 1.5 审核与撤销

- `POST /api/v1/tools/{tool_version_id}/review`（`reviewTool`，admin 会话）：body 精确
  `{decision ∈ approve/reject, manifest_sha256, reason}`；`Idempotency-Key` 必需（缺失 400
  `idempotency_key_required`）；API key 不是管理员（service account 无角色）→ 403。
- 只接受 `state=ready`；`manifest_sha256` 与工具当前值不符 → 409 `review_conflict` 且零写入；
  approve 写 `approved_by/approved_at/reviewed_*/review_decision='approved'` + 审计
  `tool.approved`；reject 写 `review_decision='rejected'`/reason/reviewed_* + 审计
  `tool.rejected`，`state` 仍 ready、`approved_at` 仍 NULL（构建成功不等于批准）；同
  decision+reason 幂等（无第二条审计），相反决定/不同理由 409 `review_conflict`。
- 非契约 `POST /api/v1/tools/{id}/revoke`（admin）：`state=revoked` + `revoked_at/by/reason` +
  审计 `tool.revoked`；历史 GET 仍可读；T025 依赖闭包返回既有 `dependency_revoked`，私有 debug
  也拒绝。幂等重放 202、无第二条审计；非 ready 409 `tool_state_conflict`。
- 未批准 ready 工具：共享 `validate` 仍 `dependency_not_ready`；作者自己的私有 debug 仍可用
  （T025 `PrivateDebug` 规则），其他创作者的私有 debug 为 `dependency_not_visible`。

### 1.6 读接口 / UI

- `GET /api/v1/tools`：cursor（共享 opaque `(created_at,id) DESC`，ACL 过滤在分页前）、
  `limit 1..100`；可见性 owner / admin / approved（`approved_at IS NOT NULL`）；返回契约
  `ToolVersion` 七字段。`application_id` 查询参数按契约接受并校验 UUID，但过滤延后到 F02B
  拥有发布版本→工具关系时实现（报告记录，不伪造 join）。
- `GET /api/v1/tools/{id}`：同可见性，不可见=404；投影严格七字段。
- 非契约 `GET /api/v1/tools/{id}/build`（owner 或 admin）：`build_log_artifact_id`、
  `sbom_artifact_id`、`build_bundle_digest`、`build_error_code`、`build_started/finished_at`、
  `review_*`、`revoke_*`；不含对象键/正文。失败构建的 `build_log_artifact_id` 由守卫在失败
  收尾时链接最新 ready log，供诊断。
- `/tools` 页：提交源 artifact + risk/schemas/lock；可见工具状态、manifest hash、内容包摘要、
  构建日志/SBOM 下载、review/revoke 结果；管理员额外看到待审队列（ready 且未批准），
  可 approve/reject/revoke（均要求 reason）；全部 `textContent`、无 localStorage/sessionStorage。
- Node 执行 `tools.js` 的提交体/审核体/路径/队列投影；页面本身仍无浏览器 DOM 自动化。

## 2. 验收标准逐条

| AC | 状态 | 证据 |
|---|---|---|
| 恶意安装钩子受隔离限制 | **passed** | `test_a_malicious_setup_hook_is_confined...`：真实 runsc 内 setup.py 读宿主 `/mnt/c/...`、guest state-root decoy、`ORCHARA_T037_TEST_SECRET`、连 127.0.0.1:80、写 `/inputs/source.zip` 与 `/tool/build_tool.py` 全部 false；日志不含 secret/foreign 内容；构建仍正常 ready |
| 源变更后旧构建不得 ready | **passed** | `test_a_source_change_during_the_build_can_never_become_ready`：真实构建中 SQL CAS 改 source_sha/revision，构建 job/tool 都 `failed/build_source_changed`，`image_digest` NULL |
| 失败日志可诊断且不含正文凭据 | **passed** | 恶意钩子日志、`test_a_crashing_install_hook...`（`exited with code 7`、`build_failed`）、`dependencies_unavailable` 日志；诊断 artifact 只含安全码/hash，不含凭据/对象键 |
| 未批准 ready 工具不能组织共享 | **passed** | `test_ready_is_not_approved_shared...`：共享 validate `dependency_not_ready`，作者私有 debug 202；其他创作者私有 debug `dependency_not_visible`；批准后共享 validate valid |
| 审核中改源不偷换版本 | **passed** | `test_review_cas_blocks_a_hash_swapped...`：准备旧 hash 后行 CAS 变化，旧 hash review 409 `review_conflict`；只有当前 hash 可批准；build guard 也用 revision+source hash CAS |
| 撤销后新执行与后续调用被拒 | **passed** | `test_revoke_blocks_new_dependency_validation...`：revoke 后共享 validate 与私有 debug 都 `dependency_revoked`；历史仍 200；重复 revoke 幂等 |
| 通过正式 Web/REST/SSE 用户路径演示 | **passed**（REST/UI/JSON 读；SSE 不适用于工具） | 真实 HTTP + 真实 `/tools` 页 + `/api/v1/tools` 读；构建日志/SBOM 下载走 T028 artifact 路径 |
| 失败/授权/重试/恢复边界有断言 | **passed** | 恶意钩子、崩溃、dependencies_unavailable（attempt 1、business、不重试）、hash 不匹配、ACL/scope、stale attempt、worker denied、幂等/冲突、迁移 replay |
| 记录版本/输入 hash/命令/原始结果/环境；stub/real 分报 | **passed** | 本报告 §0/§1/§3；真实 runsc digest、source sha、manifest sha、bundle digest、构建日志原始字节均断言；无 stub |

## 3. 证据（实际运行）

命令均在 `E:\Orchara\product`：

```text
python -m pytest tests/f05 -q
145 passed in 611.79s (0:10:11)

python -m pytest tests/f05/test_t037_migration.py -q          -> 6 passed
python -m pytest tests/f05/test_t037_submit.py -q             -> 11 passed
python -m pytest tests/f05/test_t037_admission.py -q          -> 4 passed
python -m pytest tests/f05/test_t037_build.py -q              -> 2 passed
python -m pytest tests/f05/test_t037_build_isolation.py -q    -> 4 passed
python -m pytest tests/f05/test_t037_review.py -q             -> 8 passed
python -m pytest tests/f05/test_t037_internal.py -q           -> 1 passed
python -m pytest tests/f05/test_t037_pages.py -q              -> 3 passed

python -m pytest tests/f00b/test_t026_migration.py tests/f02a/test_t023_migration.py \
  tests/f02a/test_t025_migration.py tests/f02a/test_t027_migration.py \
  tests/f03/test_t029_migration.py tests/f03/test_t032_migration.py \
  tests/f03/test_t034_migration.py tests/f03/test_t035_migration.py \
  tests/f03/test_t036_migration.py -q
61 passed in 209.19s（含 T025 基线列集适配后）

go test ./...     -> all packages ok / no-test, 0 FAIL
go vet ./...      -> exit 0
gofmt -l .        -> no output
python tools/sync_spec.py --check -> "no drift: spec copy and contract snapshot match the manifest"
```

真实构建证据（`test_real_runsc_build...`）：
- 源 zip（setup.py sleep 2s + print）→ submit 202 → tool 行在 runsc 启动期间可观察为 `building`
  （`build_started_at` 非 NULL）→ maintenance `--once` 完成 → `ready`。
- job `succeeded`；`background_job_artifacts` 恰有 `sbom` 与 `build_log`，均 ready、id 与
  tool 行一致；owner 通过 `/api/v1/artifacts/{id}/content` 读回真实字节。
- SBOM JSON 含 setup.py/tool/main.py 的真实 sha256/size，`dependencies=[]`。
- `build_bundle_digest` 与本报告 §1.4 的独立 Python 计算（source/canonical lock/SBOM 的
  `{role,sha256}` JCS 数组 SHA-256）逐字节一致；`image_digest` 与之相同且不以 “docker/image”
  命名。

## 4. 偏离 / 契约缺口

1. **`image_digest` 语义**：契约 ToolVersion 把 `image_digest` 描述为固定镜像 digest；本票写入
   的是内容包摘要 `sha256:<hex>`（source/lock/SBOM 的 `{role,sha256}` JCS 数组）。报告/迁移
   COMMENT/页面/测试都明确它不是 OCI 镜像；F15A 才构建镜像并应替换/补充该字段（可能需契约
   修订）。
2. **迁移 0023 全列非契约**：ToolVersion schema 只有七字段，构建/审核/撤销事实通过非契约
   `GET /tools/{id}/build` 暴露；未来契约修订可把这些字段并入版本读模型。
3. **`application_id` 列表过滤未实现**：契约允许该 query 参数，但 F02B 之前没有“发布版本→工具”
   关系，本票接受并校验 UUID、忽略过滤（报告记录，不伪造 join）。
4. **审核 Idempotency-Key 只强制存在，不存收据**：自然幂等由 `(decision,reason,manifest_hash,
   revision)` 行状态提供；同 key 不同 body 不会返回旧响应，而是按当前状态 409。契约要求的 header
   被强制，但“同 key 重放旧 body”的完整收据语义留待统一幂等层（F07/F02B 模式）。
5. **构建日志/SBOM artifact 仍 owner-only**：管理员能读 build facts（含 artifact id/digest），
   但不能通过 T028 的 artifact 读接口下载别人构建的日志正文（T028 文档化边界）；审核页展示
   manifest hash 与构建事实，管理员下载日志正文仍是 F07/F09 的诊断授权范围。
6. **依赖安装未实现**：最小 rootfs 无 pip/wheelhouse/setuptools。`setup.py build` 可运行；任何
   非空 lock 依赖都安全失败为 `dependencies_unavailable`。F15A 的镜像构建需提供 locked
   wheelhouse 与安装器；本票不伪称已安装依赖。
7. **`connector_version_ids` 非空 422**：F06 拥有 connector policy；清单恒为空数组，避免
   “以为绑定了连接器但实际没有”。
8. **工具源 zip 的 admission 是 header 级**：真实解压在 `build_tool.py` 中重复校验（真实读取
   字节/大小/ratio），两层都在；没有 AV/深度扫描（F13A/运营扫描）。
9. **失败构建的 `build_log_artifact_id` 由守卫回填**（最新 ready `build_log`）；若上传失败则为
   NULL（不会伪造一个不存在的日志）。

## 5. `not_run` / partial

- **not_run**：浏览器 DOM 自动化（无 headless 浏览器依赖；页面结构 + Node 执行逻辑已测）；
  真实 F06 connectors/F15A OCI 镜像构建；F04A 独立 supervisor/worker 进程；全量 `pytest tests`
  多套件门禁（批次规则，orchestrator 跑）。
- **partial（有后继）**：依赖安装（F15A）；`application_id` 过滤（F02B）；管理员构建日志正文
  读取（F07/F09 诊断授权）；跨 owner 工具发现 UI 的完整目录语义（F02B/F06）。

## 6. 后续票接口（T038 / F04A / F15A / F06）

- **T038**：`tool_versions.state='building'` 的过期/失联构建由 `background` 的 lease sweep 负责
  （本票已接 `failToolBuildForJobTx`，3 次 lease 耗尽 → failed/`lease_expired`）；T038 若增加
  tool build 的人工恢复/重驱，应复用 `EnqueueTx(kind=tool_build, target=tool id, dedup
  key `tool-build:<id>`)` 并把行 CAS 回 submitted/revision+1，而不是直接改 ready。
- **F04A**：`tool_build` handler 现在的 `HandlerWork.Sandbox` 是 maintenance 进程内构造的
  `*sandbox.Executor`（与 T033 `cmd/supervisor` 共用同一驱动/rootfs digest）；拆 worker 时把
  sandbox executor 放到 worker 进程，保持 binding/上传/守卫协议不变。
- **F15A**：唯一允许把 `image_digest` 从内容包摘要升级为真实 OCI 镜像 digest 的票；需要
  locked wheelhouse、`pip install --no-index --no-deps`、镜像仓库/签名与根文件系统重建；本票的
  `build_bundle_digest` 应保留为内容身份，二者不可混称。
- **F06**：connectors 落地后，submit 校验可接受非空 `connector_version_ids` 并把 policy
  versions/hash 纳入 `manifest_sha256`；当前 manifest 文档已含空的
  `connector_version_ids` 键，扩展时需版本化 `tool_manifest_version`。
- **F02B/F07**：`application_id` 过滤、管理员诊断授权、工具/构建 artifact 的删除与保留策略。

## 7. 复核修复（2026-11-03）

**发现：内容包摘要不具单射性。** `contentBundleDigest` 把 source/锁/SBOM 三份未加标签的字节
按字典序排序后直接拼接再 SHA-256。两个 payload 互换角色时排序结果可能相同，从而得到同一
digest；摘要无法证明“哪份字节是源、哪份是锁、哪份是 SBOM”。

**修复：** 摘要改为对如下规范化文档做 RFC 8785 JCS 后 SHA-256：

```json
[{"role":"source","sha256":"<source sha>"},
 {"role":"lock","sha256":"<canonical lock sha>"},
 {"role":"sbom","sha256":"<sbom sha>"}]
```

role 标签是身份的一部分，角色互换必然改变摘要；`build_bundle_digest`/`image_digest` 的
`sha256:<hex>` 形状与“内容摘要非 OCI 镜像”的语义不变。Go 与 Python 两侧独立实现同步更新：
`internal/background/toolbuild.go` 与 `tests/support/t037.py::bundle_digest`（不再排序拼接）。

**回归测试：**
- `internal/background/toolbuild_test.go::TestContentBundleDigestIsInjectiveAcrossRoles`
  用手工构造的 canonical `{role,sha256}` 数组独立计算期望值，断言 digest 相等、同输入稳定、
  **角色互换后不同**、SBOM 变化后不同。
- `tests/f05/test_t037_build.py::test_real_runsc_build_makes_the_tool_ready_with_a_bundle_digest`
  用真实 runsc 构建后的 source/现成 lock/SBOM 字节独立计算并逐字节比对数据库 digest
  （`tests/f05/test_t037_build.py`、`test_t037_build_isolation.py` 6 passed；真实 WSL2/runsc）。

## 第二次独立复核修复（2026-11-03）

- **缺陷（P1）**：构建沙箱的 `/workspace` 无磁盘配额，恶意安装钩子可写满宿主卷。
- **修复**：复用 T033 的 size 限定 tmpfs `/workspace`（`--workspace-quota-mb`，默认 512 MiB）+ OCI
  `RLIMIT_FSIZE`（默认 64 MiB）；驱动 cleanup 先 umount，未卸载即判定清理失败。构建失败/超限仍走
  原有安全码，不把 ENOSPC 当作成功。
- **证明**：`tests/f05/test_t037_build_isolation.py::test_the_build_workspace_has_a_disk_quota`
  （真实 runsc、8 MiB 配额、hook 捕获 ENOSPC 后释放并完成，日志含 `T037_QUOTA_ENFORCED` 且无
  `NOT_ENFORCED`；cleanup 报告 `workspace_removed`/`workspace_unmounted` 为真）。
- 状态仍为 `passed`；真实 OCI 镜像/宿主级磁盘配额仍属 F15A/F15B。
