# F02A T025 验收：把草稿固化为可核验执行清单

- 任务：F02A / GitHub Issue #26（票 `T025`，规划 ID F02A.02），分支 `feat/f00b-f02a`
- 状态：**`partial`**（复核修复 2026-11-02 更正）。manifest 组合/hash、依赖闭包、密封去重、
  validate 端点与复核机制全部真实运行通过；票面 AC3“worker 能复核规范字节”只交付了**机制**
  （`compiler.VerifyManifestBytes`），真正 worker 属 F03/F04A，尚未构建，因此 AC3 记
  `partial (mechanism delivered; worker not built)`，整票随之记 `partial`——绝不写成 worker
  已运行。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP + 真实文件系统对象存储；无 mock、
  无跳过。全量 `pytest tests` 在复核修复收尾运行一次（§3.5）。
- 主要文件：
  - `db/migrations/0012_manifest_and_tool_versions.sql`（`tool_versions` 只读模型 +
    `manifest_artifacts` 去重表）
  - `internal/compiler/{manifest.go,verify.go}`（manifest 组合/JCS/字节复核）
  - `internal/catalog/manifest.go`（依赖闭包/就绪/环/归属检查 + validate 编排 + 密封字节）
  - `internal/api/validate.go`、`internal/api/server.go`、`internal/config/config.go`、`cmd/api/main.go`
  - `tests/contracts/{README.md,snapshots/}`（golden：JSON + JCS 原字节 + sha256，含 layout-only 双胞胎）
  - `tests/support/t025.py`、`tests/support/fixtures.py`、`tests/f02a/test_t025_*.py`（4 文件 16 用例；
    复核修复删除了一个不调用 verifier 的 Python 篡改断言，见 §3.2/§5）
  - `docs/adr/0004-f00b-f02a-boundaries.md` §25–30
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（`tools/pg_dev.py`）
  - Python 3.12.2（`python -m pytest`）
  - profile：`dev-stub`；API 只开公共 listener（validate 不需要 mTLS；对象目录在 `tmp_path`）
  - 部署输入（测试注入，**不是真实镜像**）：
    `ORCHARA_WORKER_IMAGE_DIGEST=sha256:aaaa…(64)`（golden 用固定占位值）；
    runtime lock = `probes/agent_min/requirements.lock.txt`，真实 SHA-256
    `a451afed0a24f4dc73e2fb98d5b8592d311deb226a98d70371c7003d568a801d`
  - golden 执行 hash：`9cc418d167377e667c77284ce7b35819c7374c0459ec90e41f4131719aef5686`
- 真实/桩分报：**全部 real**（PG/二进制/HTTP/对象字节）。没有 stub 服务、mock 数据库或
  “静态校验当运行”。模型/工具/子版本行按 F01 方式用 SQL 播种，因为本批没有 F05 的工具构建审批与
  F02B 的发布写入；检查本身在 Go 内对真实约束执行。

## 0. 工作记录（简短）

1. 读 brief/PLAN/票面与相关规格、契约、T022–T024 代码/测试/报告/ADR，确认 0011 为最新迁移。
2. 写 0012（tool_versions 契约基线 + manifest_artifacts 去重表），空库 0001→0012 一次通过。
3. 写 `internal/compiler`：schema 精确的 Manifest 结构、JCS 规范字节/外置 hash、能力卡 schema 校验、
   `VerifyManifestBytes`（先 hash 后 parse）与 13 个 Go 单测（含 golden 生成/漂移/篡改/非规范/缺字段；
4. 写 `internal/catalog` 执行校验：P05 定义校验、能力卡、依赖闭包/就绪/归属/环、manifest 组合、
   按 `(application_id, execution_revision, manifest_sha256)` 去重密封 artifact；纯规则函数有单测。
5. 写 `POST /applications/{id}/validate`（owner-only、CSRF、`application:write` scope、
   `X-Manifest-SHA256`、503 `compiler_unconfigured`/`object_store_unavailable`），config 增两个部署输入。
6. 建 `tests/contracts/snapshots` golden（sales + layout-only 双胞胎）与 `tests/f02a` 4 文件 16 用例
   （复核修复后的当前数；完成时为 17，其中一个无效篡改断言移入 Go），
   真实 PG/二进制/HTTP 全量跑；T023 两个边界断言随本票闭环更新（validate 存在，debug/submit 仍 404；
   迁移链到 0012）。
7. 跑完全部门禁（§3），写本报告/ADR/PROGRESS，按 feat/test/docs 三次提交。

## 1. 交付内容

### 1.1 迁移 `0012_manifest_and_tool_versions.sql`

- `platform.tool_versions`：与 `contracts/database.sql` 基线一致（id/owner_id/source_artifact_id/
  input_schema/output_schema/dependency_lock/connector_versions/image_digest/risk_class/state/
  manifest_sha256/approved_by/approved_at/created_at）。本批没有工具提交/构建/审批流程：F05 拥有
  写入；T025 建表作为**只读模型**（与 F01 建 `applications` 读模型同理），迁移/COMMENT 明确写出。
  - 契约 CHECK `state <> 'ready' OR image_digest IS NOT NULL` 原样保留：ready 工具必有镜像摘要，
    manifest 的 `tool_artifacts[].image_digest` 才有可信来源。
  - 未新增 `tool_build` job、构建 CLI 或审批端点（F05）。
- `platform.manifest_artifacts`：**本票新增、契约基线没有**的去重表：
  `PRIMARY KEY (application_id, execution_revision, manifest_sha256)` + FK `artifact_id`。
  规范字节按内容身份只密封一次；重复 validate 不复写对象。T027 的 `execution_snapshots` 与 F02B 的
  `application_versions` 必须复用这里记录的 artifact，不能为同一 hash 再造第二份字节。
- 权限沿用 0007–0011：`REVOKE ALL ON SCHEMA platform FROM PUBLIC`；`orchara_worker` 无 platform
  USAGE/表/序列权限；重复 `admin migrate` 输出 `no pending migrations`。

### 1.2 `internal/compiler`：manifest 组合 + 规范字节 + 复核机制

- 常量（固定、文档化）：`ManifestVersion="1.0"`、`CompilerVersion="orchara-compiler/1.0.0"`、
  `StateSchemaVersion="orchara-state/1.0"`、`DefaultRuntimeLockPath="probes/agent_min/requirements.lock.txt"`。
  `CompilerVersion` 是组合规则版本（本批不产编译器二进制）；`StateSchemaVersion` 是固定名字，
  真正的 state schema 制品属 F03。
- `compiler.Inputs{WorkerImageDigest, RuntimeLockPath}` → `Resolve()`：
  - worker digest 缺失/格式错 → `*UnconfiguredError`（API 映射 503 `compiler_unconfigured`），
    **绝不发明 digest**；config 在启动时拒收格式非法值，缺失则允许进程启动（identity/draft 仍可用）。
  - runtime lock 每次 resolve 时重新读文件并 SHA-256；缺失/不可读同样是 `UnconfiguredError`
    （`ORCHARA_RUNTIME_LOCK_PATH`），不回退到猜测值。
- `Build(resolved, applicationID, sourceRevision, definition, card, tools, models, subapps)`：
  - 产出字段与 `contracts/execution-manifest.schema.json` 完全一致（13 字段，无额外字段）；
    `source_revision` 取草稿 `execution_revision`；layout/version/snapshot/review/hash 一律不进入对象。
  - 工具 `{id, image_digest}` 按 id 排序去重；model/subapp UUID 数组排序去重；`connector_policies: []`。
  - `json.Marshal` 后经 `internal/jcs.CanonicalizeBytes` 得到 UTF-8 RFC 8785 原字节；
    `SHA256Hex(bytes)` 外置为 `manifest_sha256`。Build 末尾用 `decodeManifest` 自检，
    保证“构造器不会产出复核器拒绝的字节”。
  - 能力卡按 manifest schema 校验：六字段、精确类型、无额外键、examples ≥ 2；空卡 `{}` 是
    T023 契约缺口下的“尚未填写”状态，**不可执行**并返回定位错误，不伪造示例。
- `VerifyManifestBytes(objects, ArtifactRef{ObjectKey, SHA256, SizeBytes})`（F03/F04A worker 的入口）：
  1. 以 20 MiB 上限读密封对象；2. **先**按字节重算 SHA-256 并比记录值/大小（不符即
     `ErrManifestHashMismatch`/`ErrManifestSizeMismatch`，绝不进入 parser）；3. 要求字节是精确
     JCS 规范形式；4. 严格解码并复核 schema 语义（必填/唯一字段、UUID、digest 模式、卡、
     数组有序去重），返回 `*Manifest` 与字节。
- 规范字节与 Go map 顺序无关：`TestBuildIsStableAcrossJSONMapOrdering`（同一 JSON 不同键序）、
  `TestBuildNormalisesSetOrderAndDeduplicates`（集合乱序/重复）字节完全一致。

### 1.3 `internal/catalog`：依赖闭包、就绪与归属（T027 复用）

- `ValidateExecution(ctx, callerID, applicationID, objects, compiler.Inputs, ExecutionOptions)`：
  - 草稿读取后 **owner-only**：非 owner（含 read grant/管理员）一律 `ErrNotVisible`（404）。
  - 顺序：定义 P05 校验 → 能力卡 schema → 依赖闭包 → 部署输入 resolve → manifest Build →
    密封 artifact。定义或卡有错时不再解析依赖（无法可靠解释文档）；坏草稿即使
    `ORCHARA_WORKER_IMAGE_DIGEST` 缺失也返回 `valid:false` 的错误，而不是 503。
  - 只写 manifest artifact/去重行；**不创建** version/snapshot/review/report/job。
- 依赖提取（节点 config，pointer 精确到字段）：
  - `model.model_profile_version`、`agent.model_profile_version` → model；
  - `agent.tool_versions[]`、`python.tool_version` → tool；
  - `agent.subapp_versions[]`、`call_app.application_version` → sub-app version。
- 就绪规则（真实表）：
  - model：存在、`revoked_at IS NULL`、`validation_state='passed'`。
  - tool：存在、未 revoked、`state='ready'`、`image_digest IS NOT NULL`；共享/提交执行还要求
    `approved_at IS NOT NULL`。作者私有 debug（`ExecutionOptions{PrivateDebug:true}`，T027 传）允许
    作者**自己**的 ready 工具无需审批；用他人未批工具返回 `dependency_not_visible`。
  - sub-app version：存在、未 revoked、应用未归档、调用者对其应用有 read ACL（owner 或未过期 grant）；
    他人依赖无 ACL 是错误而非静默纳入。
- 环检测：`version_dependencies` 的递归闭包（`UNION` 使环查询可终止）+ 文档内调用图：
  - 闭包中边自身成环 → `dependency_cycle`；
  - 直接/间接调用到**本应用**的 published version（草稿调自己）→ `dependency_cycle`；
  - 错误定位到第一个达到环的 `call_app`/`agent.subapp_versions` pointer。
- 稳定错误码（新增于本票，ADR §28 记录）：

  | code | 含义 |
  |---|---|
  | `capability_card_incomplete` | 空卡或非 manifest 六字段形状 |
  | `dependency_invalid_id` | config 值不是 UUID（防御性；P05 通常已先报 `bad_config`） |
  | `dependency_not_found` | 固定引用行不存在 |
  | `dependency_revoked` | model/tool/version 已撤销 |
  | `dependency_not_ready` | model 非 passed；tool 非 ready/未审批/无镜像摘要 |
  | `dependency_not_visible` | 他人 sub-app version 无 ACL；私有 debug 用他人未批工具；归档应用 |
  | `dependency_cycle` | sub-app 闭包环（含草稿调用自身应用版本） |

  每条错误始终有 `pointer`（`/capability_card[/field]` 或
  `/nodes/<i>/config/<field>[/<j>]`），指向单节点时填 `node_id`；消息只含 id/状态，不含他人正文。
- 密封与去重 `persistManifestArtifact`：事务内
  `pg_advisory_xact_lock(hashtextextended(application|execution_revision|sha))` 串行化同身份写入 →
  查 `manifest_artifacts`；存在则校验 artifact 仍 ready/未墓碑/hash/大小一致后复用；
  不存在则 `sealed/<64hex>` 全有或全无写入、核验落盘 hash/大小、同事务插
  `artifacts(state='ready', purpose='execution_manifest', media_type='application/json', revision=1)`
  与去重行；冲突（绕过锁的写者）删除自己的对象并返回赢家。事务失败/冲突路径删除本次对象；
  进程在对象写后、commit 前崩溃可能留下未引用 sealed 对象，属 T026 孤儿清理范围（如实记录）。

### 1.4 API `POST /api/v1/applications/{application_id}/validate`

- `operationId=validateApplication`；`authorize`（会话 CSRF / API key）+ 会话要求
  `application:write` scope（`application:read` key → 403 `insufficient_scope`）。
- owner-only：非 owner 404 `resource_not_found`（read grant 也不放行；不泄露存在性）。
- 200 `{valid:false, errors:[{node_id?,code,pointer,message}]}`：定义/卡片/依赖错误，`X-Manifest-SHA256`
  不出现。
- 200 `{valid:true, errors:[]}` + `X-Manifest-SHA256: <64hex>`：服务器 hash 只在响应头扩展返回
  （契约 200 schema 装不下，见 §2）。同时密封/复用 canonical bytes。
- 503 `compiler_unconfigured`：`ORCHARA_WORKER_IMAGE_DIGEST` 缺失/非法或 runtime lock 不可读；
  503 `object_store_unavailable`：`ORCHARA_OBJECT_DIR` 未配置（有效草稿无法密封字节时 fail closed）。
- 其他认证/权限错误沿用既有错误封套；validate 本身不读请求体、不写版本/发布/报告。
- config 新增：`ORCHARA_WORKER_IMAGE_DIGEST`（可缺省；非空必须 `^sha256:[0-9a-f]{64}$`，格式错拒启动）、
  `ORCHARA_RUNTIME_LOCK_PATH`（默认 P01 lock）。两者在 `internal/config` 与 `internal/compiler`
  共用同一组常量。

### 1.5 Golden snapshots（`tests/contracts/snapshots/`）

- `inputs.json`：固定 application id `3333…`、source_revision 1、占位 worker digest、
  被引用 tool `1111…` / model `2222…` 与 tool 镜像摘要。
- `sales/`：spec sales definition + 完整能力卡 + layout + `manifest.json`（可读）、
  `manifest.jcs`（权威原字节，无尾随换行）、`manifest.sha256`。
- `layout-only/`：同一执行内容、不同 layout；三个输出文件与 `sales/` **逐字节相同**
  （测试断言，作为“只改布局不变 hash”的仓库内证据）。
- `TestGoldenSnapshotsMatchTheBuilder` 重新生成并对比，漂移即失败；`-update` 才重写。
  runtime lock 摘要由文件实时计算，锁文件变化必然要求一次显式 golden 更新。
- `TestVerifyManifestBytesAcceptsTheGoldenSnapshot` 把 golden 字节写进真实文件系统对象存储，
  调 `VerifyManifestBytes`；另有篡改字节/错误记录 hash/非规范 JSON/缺必填字段/大小不符/
  对象不存在/未配置 store 的负例。
- 真实 HTTP 侧 `test_t025_manifest.py` 断言 validate 返回头 == golden sha、密封对象字节 ==
  `manifest.jcs`、artifact 行 sha/size/media/state 一致。

## 2. 契约缺口与占位输入（如实）

1. **`X-Manifest-SHA256` 是非契约扩展**。`contracts/openapi.json` 的 validateApplication 200 只有
   `{valid, errors}`，没有 `manifest_sha256` 字段；本票把服务端 hash 放在响应头，**不改 contracts**。
   客户端必须用服务端返回的 hash，不能自行重序列化猜测。ADR §26 记录，后续契约修订票应把它变成
   响应字段（或显式 header 契约）。
2. **空草稿能力卡**：T023 已记录 `DraftInput.capability_card` 六字段/examples≥2 与“新草稿无卡”的
   缺口；本票沿用 `{}` 作为“尚未填写”，validate 返回 `capability_card_incomplete` 拒绝执行，
   **不填充伪造示例**。T027/F02B 必须继续接受 `{}` 的保存状态。
3. **worker image digest 是部署方占位输入**：本环境/本批**未构建 worker 镜像**（F04A/F15A 才有）。
   测试注入固定 `sha256:aaaa…` 只证明“输入参与 hash 且缺失时 fail closed”，不证明镜像存在。
   F04A/F15A 必须替换为真实镜像 digest 并重新生成 golden/发布 hash。
4. **`state_schema_version` 是固定常量**（`orchara-state/1.0`）：真正的 state schema 制品属 F03；
   本票只固定清单里的名字，不声称 schema 文件已存在/已运行。
5. **工具审批/构建不存在**：`platform.tool_versions` 由 0012 作为读模型创建，F05 才写；T025 不提供
   任何工具行写入。测试用 SQL 播种真实约束下的一致行。
6. **知识库引用不在闭包内**：本批没有 `knowledge_bases` 表/读取授权实现，`retrieve` 节点的
   `knowledge_base_ids` 不参与依赖就绪（F09/F05 范围）。manifest 也不含 KB 快照（WORKFLOW-SPEC 说明
   KB 是运行时当前授权检索）。
7. **`X-Manifest-SHA256` 只在 valid:true 的 200 出现**；invalid/503 请求没有 hash（没有可核验字节）。

## 3. 实际命令与原始结果

工作目录 `E:\Orchara\product`；Go 环境 `GOCACHE=E:\orchara-toolchains\gocache`、
`GOMODCACHE=E:\orchara-toolchains\gomodcache`。

### 3.1 Go

```text
> E:\orchara-toolchains\go\bin\gofmt.exe -l internal cmd tools
（无输出）

> E:\orchara-toolchains\go\bin\go.exe vet ./...
（无输出，exit 0）

> E:\orchara-toolchains\go\bin\go.exe test ./...
?   orchara/platform/cmd/admin        [no test files]
?   orchara/platform/cmd/api          [no test files]
?   orchara/platform/cmd/maintenance  [no test files]
?   orchara/platform/cmd/supervisor   [no test files]
?   orchara/platform/cmd/worker       [no test files]
?   orchara/platform/db               [no test files]
ok  orchara/platform/internal/api       1.459s
ok  orchara/platform/internal/background
ok  orchara/platform/internal/catalog   1.181s
ok  orchara/platform/internal/compiler  0.785s
ok  orchara/platform/internal/config    0.494s
ok  orchara/platform/internal/db
ok  orchara/platform/internal/health
ok  orchara/platform/internal/identity
ok  orchara/platform/internal/jcs
ok  orchara/platform/internal/modelproxy
ok  orchara/platform/internal/objectstore
ok  orchara/platform/internal/policy
ok  orchara/platform/internal/supervisor
ok  orchara/platform/internal/uuid
ok  orchara/platform/internal/workflow
```

### 3.2 Python 真实栈

```text
> python -m pytest tests/f02a -q
..............................................................           [100%]
62 passed in 208.63s (0:03:28)

> python -m pytest tests/f00b -q
........................................................................ [ 83%]
..............                                                           [100%]
86 passed in 214.21s (0:03:34)

> python -m pytest tests/f01 -q
........................................................................ [ 46%]
........................................................................ [ 92%]
...........                                                              [100%]
155 passed in 451.48s (0:07:31)

> python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 4.62s
```

`tests/f02a` 62 = T023 45 + T025 17（迁移 6、validate 3、manifest 3、dependencies 5）。
T025 全部真实 PG/二进制/HTTP/对象字节；无 skip。

> 复核修复（2026-11-02）：`test_t025_manifest.py` 原先的“单字节篡改后 SHA-256 不同”测试被删除
> ——它不调用 `VerifyManifestBytes`，无法因 verifier 缺陷而失败。同一边界现在由
> `internal/compiler/verify_test.go::TestVerifyManifestBytesRejectsASealedObjectTamperedInPlace`
> 覆盖（真实密封对象就地篡改 + typed `ErrManifestHashMismatch` + 未篡改对象先验证通过）。
> 因此 T025 的真实栈用例数为 16（f02a 本票相关合计随复核修复节更新）。

### 3.3 契约同步

```text
> python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

### 3.4 迁移

空库 `0001→0012` 一次通过，重复 migrate `no pending migrations`（`test_t025_migration.py`
与 T023 迁移测试都断言；0001–0011 未修改）。

### 3.5 未运行 / 按计划

- 全量 `python -m pytest tests -q`：本票完成时按 PLAN §1 未跑；**2026-11-02 复核修复收尾已跑**
  （最新原始结果见 `PROGRESS.md` 的“F00B/F02A 两轴复核修复”一节）。
- 真实 worker 执行/领取/复核：未构建（F03/F04A）；本票交付 `VerifyManifestBytes` 机制与测试，
  票面 AC3 因此记 `partial`。
- 真实镜像 registry/镜像构建：不在本批（`worker_image_digest` 为占位输入）。

## 4. 验收标准逐条

票面（`.scratch/v1-tickets/issues/T025.md`）：

| # | 验收 | 状态 | 证据 |
|---|---|---|---|
| 1 | 只改布局不变执行 hash | **passed** | `test_layout_only_save_keeps_the_hash_and_definition_change_changes_it`：layout-only 保存后 `revision` 2→`execution_revision` 仍 1，`X-Manifest-SHA256` 与首次相同；golden `sales` vs `layout-only` 的 `manifest.jcs` 逐字节相同 |
| 2 | 依赖环/归属错误被拒 | **passed** | `test_version_dependency_cycles_and_self_calls_are_refused`（version_dependencies 环、草稿调自身版本、闭包回到本应用）；`test_subapp_readiness_requires_a_live_version_and_a_readable_application`（他人应用无 ACL → `dependency_not_visible`，授权后通过）；工具所有权矩阵见 Go `TestToolReadinessRules`（`PrivateDebug` 规则） |
| 3 | worker 能复核规范字节 | **partial（机制已交付，worker 未构建）** | `compiler.VerifyManifestBytes`（先 hash 后 parse）+ Go 测试：golden 通过、**真实 sealed-object 就地篡改**（先验证未篡改对象，再覆盖已密封文件的一个字节，调用 `VerifyManifestBytes` 断言 `ErrManifestHashMismatch`；重记篡改摘要后仍以 `ErrManifestInvalid` 拒绝）、非规范/缺字段/大小不符/缺对象/未配置 store 拒绝 + HTTP 真实字节 hash 断言。真正 worker 属 F03/F04A；本票不写成 worker 已运行 |
| 4 | 通过端到端入口实际演示；失败、授权与重试/恢复边界有断言 | **passed** | 真实 HTTP `POST /applications/{id}/validate`：401/403（CSRF、scope）/404（owner-only）/200 valid:false（P05 定位、能力卡、依赖、环）/200 valid:true（golden hash）/503（compiler_unconfigured、object_store_unavailable）。validate 同步、无租约/后台 job；重试边界 = 同项目同 hash 的幂等去重（重复 validate 不新写对象/行），测试断言 |
| 5 | 记录版本、输入/hash、命令、原始结果、环境；stub/real 分报；缺条件 blocked，未运行 not_run | **passed** | 本报告 §1/§3/§5；real/stub 明确；未运行项列 `not_run` |

Brief §2 交付项：

| 项 | 状态 |
|---|---|
| JCS 包真实存在/顺序稳定 | **passed**（复用 T023 `internal/jcs`，新增 map 顺序稳定性测试） |
| Manifest builder 精确 schema、固定常量、digest/lock 输入、空卡拒绝 | **passed**（`internal/compiler` Go 测试 + HTTP golden） |
| 规范字节 = JCS、hash 外置、无 layout/身份/审核状态 | **passed**（字段集合断言、无 layout、无 hash 字段） |
| `VerifyManifestBytes` 先 hash 后 parse、篡改拒绝 | **passed（机制）** | Go：golden 通过 + 真实 sealed-object 就地篡改返回 `ErrManifestHashMismatch`（`TestVerifyManifestBytesRejectsASealedObjectTamperedInPlace`）；另有错误记录 hash/非规范/缺字段/大小不符/缺对象/未配置 store 负例；HTTP 侧断言端点密封字节 == golden `manifest.jcs`，不把“两个 SHA-256 不同”冒充 verifier 证据 |
| 依赖闭包：model/tool/subapp、排序去重、未知/latest | **passed**（P05 先拒非 UUID；未知 UUID → `dependency_not_found`；Go 排序去重测试） |
| 就绪/归属/环规则定位错误 | **passed**（4 个真实栈依赖测试 + Go 规则矩阵） |
| validate 端点 owner-only/CSRF/200/hash/503/不写执行身份 | **passed**（3 个 validate 测试；version/review/job 行数为 0） |
| 落盘密封字节并按三元组去重 | **passed**（`manifest_artifacts` + 真实对象文件；重复 validate 不增对象） |
| Golden snapshots（linear/layout-only × json/jcs/sha） | **passed**（`tests/contracts/snapshots/` + Go 生成/漂移测试） |
| 负例集（环、未知、model 未就绪、tool 未批/构建中、他人 sub-app 无 ACL、定义定位错误） | **passed**（`tests/f02a/test_t025_dependencies.py`、`test_t025_validate.py`） |
| `tests/f02a` 真实栈测试 | **passed**（62 passed 含 T023 回归） |
| 报告/PROGRESS/ADR | **passed**（本文件、PROGRESS 行、ADR §25–30） |

## 5. 边界与未运行（not_run / 后续票）

- **真正 worker 不存在（AC3 = `partial`）**：本票交付的是复核机制
  （`compiler.VerifyManifestBytes` + 真实字节/篡改/负例测试）。F03/F04A 构建 worker 后必须：从
  snapshot/claim 取 `manifest_artifact_id` 与 `{object_key, sha256, size_bytes}`，在解析前调用该
  函数；当前没有 worker 二进制可跑，记 `not_run`，票面 AC3 因此不是 `passed`。
- **worker 镜像未构建**：`ORCHARA_WORKER_IMAGE_DIGEST` 是部署方占位输入；F04A/F15A 必须替换真实
  digest，并接受“所有 manifest hash 变化 + golden 重生成”。
- **state schema 制品属 F03**：本票只固定 manifest 字段 `orchara-state/1.0`。
- **F05 拥有 tool_versions 写/构建/审批**：本票只读；`approved_at` 的组织审批流未实现。
- **F02B 拥有 publish/version 写入**：validate 不创建 version/review；发布事务必须复用
  `manifest_artifacts` 记录与 `X-Manifest-SHA256` 的同一字节。
- **T027 拥有 debug**：复用 `ValidateExecution + ExecutionOptions{PrivateDebug:true}`；debug 必须用
  draft 行 `revision` 作为 `draft_revision`、用本票 artifact 作为 `manifest_artifact_id`，不得重算/重写字节。
- **知识库/连接器**：`retrieve` 的 KB 依赖与 `connector_policies` 内容分别属后续票；本票
  `connector_policies` 恒为 `[]`，KB 不参与闭包。
- **全量 pytest**：按计划未跑（§3.5）。
- **孤儿 sealed 对象**：对象写后、commit 前崩溃可留未引用对象；T026 清理，本票不声称已处理。
- **Redis/真实 registry 等**：与本票无关，未触及。

## 6. T027 可复用接口 / F03 必须复核

T027（debug）直接复用：

- `catalog.ValidateExecution(ctx, callerID, applicationID, objects, compiler.Inputs, catalog.ExecutionOptions{PrivateDebug: true})`
  → `*catalog.ValidatedExecution{Manifest, Bytes, SHA256, ArtifactID}` 或
  `[]catalog.ExecutableError`；错误码见 §1.3 表。
- `ValidatedExecution.ArtifactID` 写 `execution_snapshots.manifest_artifact_id`；
  `SHA256` 写 `manifest_sha256`；`Manifest` 写 JSONB 投影；`Bytes` 不必再写（已在 sealed artifact）。
- 依赖就绪规则：共享（默认）与作者私有（`PrivateDebug:true`）两套；调用者 id 用于 sub-app ACL 与
  私有工具所有权。
- env：`ORCHARA_WORKER_IMAGE_DIGEST`、`ORCHARA_RUNTIME_LOCK_PATH`（默认 P01 lock）；
  对象 store 仍用 `ORCHARA_OBJECT_DIR`；三个 503 code：`compiler_unconfigured`、
  `object_store_unavailable`。
- 常量/入口：`compiler.DefaultRuntimeLockPath`、`compiler.EnvWorkerImageDigest`、
  `compiler.EnvRuntimeLockPath`、`compiler.ManifestVersion/CompilerVersion/StateSchemaVersion`。

F03/F04A 必须替换或复核：

1. 用真实 worker 镜像 digest 与真实 state schema 制品替换占位常量/输入；
2. worker 领取响应携带 manifest artifact 的 `object_key/sha256/size_bytes`，先调
   `compiler.VerifyManifestBytes` 再解析（不要从 JSONB 重序列化推断 hash）；
3. 不得为同一 `(application_id, execution_revision, manifest_sha256)` 另造 artifact/字节。

## 7. 文件与提交

- `db/migrations/0012_manifest_and_tool_versions.sql`
- `internal/compiler/{manifest.go,verify.go,manifest_test.go,verify_test.go,golden_test.go}`
- `internal/catalog/{manifest.go,manifest_test.go,draft.go}`
- `internal/api/{validate.go,server.go}`、`internal/config/{config.go,config_test.go}`、`cmd/api/main.go`
- `tests/contracts/{README.md,snapshots/**}`、`tests/support/{t025.py,fixtures.py}`
- `tests/f02a/test_t025_{migration,validate,manifest,dependencies}.py`、
  `tests/f02a/test_t023_{invalid_draft,migration}.py`（边界断言随 T025 闭环更新）
- `reports/f02a/T025-manifest.md`、`PROGRESS.md`、`docs/adr/0004-f00b-f02a-boundaries.md` §25–30

## 8. 规格变更

无。`product/docs/spec/**` 与 `product/contracts/**` 未修改；`sync_spec --check` no drift。
发现的契约缺口（`X-Manifest-SHA256`、空卡）记录在本报告 §2 与 ADR，由后续契约/复核票处理。
