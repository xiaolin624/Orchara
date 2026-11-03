# F05 T033 验收：在正式沙箱调试预置工具并登记可授权下载的产物

- 任务：F05 / GitHub Issue #34（票 `T033`，规划 ID F05.03），分支 `feat/f05-f03-run-and-tools`
- 状态：**`passed`**（本票交付范围 = run-bound artifact scoped 接口 + `listRunArtifacts` +
  gVisor/runsc 沙箱执行器与最小 Python rootfs + `cmd/supervisor exec --once` 工具调试路径 +
  运行页沙箱面板）。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 mTLS（supervisor/worker/
  maintenance 三种证书）+ 真实 HTTP + 真实对象字节 + **真实 WSL2/runsc(gVisor) 沙箱**
  + Node 真实执行页面逻辑；无 mock、无跳过。
  边界（如实）：取消接口（T036）、任意工具构建/dependency lock（T037）、恢复/对账（T038）、
  supervisor/worker 进程拆分与实际 LangGraph worker（F04A）不在本票；浏览器 DOM 驱动页面仍
  `not_run`（与 F01/T023/T027/T029/T031/T032 相同边界）；全量 `pytest tests` 门禁未跑（批次规则，
  由 orchestrator 跑）。
- 主要文件：
  - `db/migrations/0019_run_artifact_uploads.sql`（唯一新表 `platform.run_artifact_uploads`）
  - `internal/execution/runartifacts.go`（五个 scoped 操作的 SQL/校验/签名/失败码）
  - `internal/catalog/runartifacts.go`（owner-only `listRunArtifacts` + 4 字段投影 + 共享 cursor）
  - `internal/api/{runartifacts.go,internal.go,server.go,debug.go}`（路由、worker 角色门、错误映射、
    `X-Run-Execution-Unit` / `X-Run-Cancel-Requested-At` 非契约头）
  - `internal/execution/{store.go,service.go}`（live lease 的 `execution_unit_id`、cancel 读取）
  - `internal/sandbox/{sandbox.go,bundle.go,command.go,runner*.go,executor.go,result.go,sandbox_test.go}`
    （OCI bundle 构建、固定 argv、进程组/kill、driver 报告解析、协议/产物准入、digest 身份）
  - `internal/presetexec/{presetexec.go,presetexec_test.go}`（一次 attempt 的编排：manifest→输入→
    沙箱→上传→登记→result→exit）
  - `cmd/supervisor/{main.go,exec.go,client.go}`（`exec --once`、双容量 mTLS 客户端、只读 DSN）
  - `cmd/sandboxprobe/main.go`（运维/测试用沙箱验证入口，非用户面）
  - `deploy/sandbox/{build_python_rootfs.sh,sandbox_exec.sh,isolation_probe.py}`
  - `apps/web/src/{runs.html,assets/runs.js}`（沙箱工具调试面板 + 产物下载）
  - `tests/support/{t033.py,fixtures.py}` + `tests/f05/test_t033_{migration,endpoints,sandbox_run,pages}.py`
    （4 文件 20 用例）；f00b/f02a/f03/f05 迁移链断言 0018→0019
- 版本 / 环境：
  - Go 1.27.3 windows/amd64（`E:\orchara-toolchains\go\bin\go.exe`）
  - PostgreSQL 16.4，隔离集群 `127.0.0.1:55432`（真实 `cmd/admin migrate`）
  - Python 3.12.2（`python -m pytest`）、Node 24.13.0
  - WSL2 Ubuntu-24.04：Linux 6.6.87.2-microsoft-standard-WSL2、`runsc version
    release-20241118.0`（spec 1.1.0-rc.1）、`/usr/bin/python3.12` 3.12.3、root in WSL（无需 sudo）
  - 沙箱镜像身份（由 builder 记录）：`sha256:03c3f647d1d6e68173bfa9df84b87e5d6162d351bfdd55e4c3b208c0b5412ff5`
    （1093 个文件，46 MiB；`/opt/orchara-sandbox/python-rootfs`）
  - profile `dev-stub`；对象目录在测试 `tmp_path`；部署输入沿用 T025 占位值
    `ORCHARA_WORKER_IMAGE_DIGEST=sha256:aaaa…(64)`
- 真实/桩分报：**全部 real**（PG/二进制/mTLS/HTTP/对象字节/runsc 沙箱/Node）。测试用 SQL
  播种「非当前 token 的 attempt」与取消触发器（T036 端点尚不存在，票据要求由测试写
  `runs.cancel_requested_at`）；沙箱、产物字节、finalize、finalizer、mTLS 全部真实执行。

## 0. 工作记录（简短）

1. 读 NOTES/T033-DESIGN/票面、WORKFLOW-SPEC §5、SECURITY §2–4、BACKGROUND-JOBS §3、
   EXECUTION §1/§3/§8、IMPLEMENTATION-DETAILS §6/§9、API §3、T027/T029/T030/T031/T032 报告、
   `contracts/openapi.json`（六个 operationId 的 body/响应/`x-internal-role`）与
   `contracts/database.sql`，确认 0018 为最新迁移、树干净。
2. **先降风险**：先写并真实运行 `deploy/sandbox/build_python_rootfs.sh`（WSL 内构建 46 MiB rootfs 并
   记录 digest），再用 `sandbox_exec.sh` 手工跑通 sales CSV → `output.json`/`summary.json`/
   `summary.csv` 与隔离 probe；随后才写 Go/协议层。这避免了"先写 3000 行再发现 runsc 跑不起来"。
3. TDD 切片：
   - 迁移 0019 → 表/约束/索引/权限用例（红：表不存在）；
   - scoped 端点：角色矩阵（worker/supervisor/maintenance/无证书/角色头不匹配）→ employee
     `forbidden`；上传/完成/状态/下载/登记往返（真实签名 URL + 真实 finalize）；旧/非当前 attempt
     → 409 `stale_attempt`；终端 run → 409 `run_terminal`；跨 run/未知 → 404；
   - `listRunArtifacts`：owner-only（admin 404）、4 字段、稳定 cursor、log 排除；
   - `internal/sandbox`：bundle/argv/digest/协议/产物准入 + 用 fake runner 断言 timeout/cancel/
     orphan/digest mismatch 的 kill+cleanup 语义（无 WSL 依赖）；
   - `internal/presetexec`：成功、图形/工具拒绝、输入零/多/完整性、manifest 哈希、取消、超时、
     工具拒绝码、崩溃、孤儿、沙箱不可用的编排（fake client/inputs/sandbox）；
   - 真实栈：一条完整 runsc 工具调试运行、输入拒绝失败路径、取消中途 kill、隔离 probe、
     timeout/crash 无孤儿；
   - UI：面板结构 + Node 执行 `describeSandboxPanel`/`artifactDisplayName`/`describeArtifactList`。
4. 跑门禁（§3），写本报告/PROGRESS/NOTES，按 feat/test/docs 三次提交；未推送、未关 issue、
   未改 `docs/spec/` 与 `contracts/`、未改迁移 0001–0018。

## 1. 交付内容

### 1.1 迁移 `0019_run_artifact_uploads.sql`

唯一新表，只记录「哪个 attempt 为哪个 run 预留了哪个 artifact」：

```sql
platform.run_artifact_uploads(
  artifact_id   uuid PRIMARY KEY REFERENCES platform.artifacts(id),
  run_id        uuid NOT NULL REFERENCES platform.runs(id),
  attempt_token uuid NOT NULL REFERENCES platform.run_attempts(token),
  purpose       text NOT NULL CHECK (purpose IN ('node_output','final_output','log','receipt')),
  created_at    timestamptz NOT NULL DEFAULT now()
)
+ run_artifact_uploads_run (run_id, created_at)
+ run_artifact_uploads_attempt (attempt_token, created_at)
```

- 复用 `platform.run_artifacts` 作为 run 链接（relation `output|artifact|log`；input 链接仍由 T027 写）；
  **没有**新的删除模型：F07 删除 artifact 时必须同删 provenance（artifact_id PK 保证 id 不复用）。
- purpose 是契约 `createWorkerUpload` 的四值枚举（`tool_summary` 等 T030 job 用途**不**混入 run 域）；
  CHECK、两个 FK、两个索引与 0007–0018 一致的 worker/PUBLIC 零权限。

### 1.2 五个 scoped 内部路由（attempt token；契约要求 worker 角色）

| operationId | 路由 | 角色 | 行为 |
|---|---|---|---|
| `createWorkerUpload` | `POST /internal/v1/runs/{run_id}/artifact-uploads` | worker | 校验 purpose 枚举/media allowlist/0..20 MiB；artifact owner = **run owner**；staging key + 预分配 sealed key + 签名 PUT；同事务写 provenance |
| `completeWorkerUpload` | `POST .../artifacts/{artifact_id}/complete` | worker | 只允许**该 attempt 自己**预留的 artifact；CAS `uploading→scanning`（revision+1）并**同事务**入队一个 `artifact_finalize`（dedup `run-artifact:<id>:<sha>`，expected_revision/hash）；同 hash 重放 202、异 hash 409 `artifact_conflict` |
| `createWorkerDownload` | `POST .../artifact-downloads` | worker | 只允许本 run 的**输入** artifact（`runs.input_artifact_id` 或 `run_artifacts.relation='input'`，ready/未删除）；输出/外部 → 404 `artifact_not_found` |
| `getWorkerArtifactStatus` | `POST .../artifact-status` | worker | 只允许本 attempt 的 provenance artifact 或本 run 输入；其它 404 |
| `registerRunArtifact` | `POST .../artifacts` | worker | artifact 必须 ready、owner=run owner、provenance=本 attempt；relation 限定 `output|artifact|log`；插入 `run_artifacts(run_id,artifact_id,attempt_token,relation)`；同 relation 幂等、异 relation 409 `artifact_conflict` |

所有路由统一先解析 scope：token 必须属于路径 run、是 job 的**当前** token、未 revoked/exited、
租约未过期；run 终态 → 409 `run_terminal`，非执行的未终态 → 409 `run_state_conflict`；跨 run/
伪造/过期 token → 409 `stale_attempt`；artifact 不在 scope → 404（不泄露存在性）。
角色门是显式的 `requireInternalRole(worker)`：supervisor/maintenance 证书 403 `forbidden`，
证书组织与 `X-Internal-Role` 不一致 403（mTLS 中间件，未变）。

### 1.3 `GET /api/v1/runs/{run_id}/artifacts`（`listRunArtifacts`）

- owner-only，与 `getRun` 同一 404 规则（管理员不是超级读者）；items 恰为契约四字段
  `{id,state,media_type,size_bytes}`；`(a.created_at, a.id) DESC` keyset + 共享 opaque cursor；
  ACL 判定在分页之前。
- **决定（记录）**：`purpose='log'` 不列出——WORKFLOW-SPEC §5 明确 stdout/stderr 是日志而非输出；
  log 仍由 owner 通过 `GET /api/v1/artifacts/{id}` 读取。输入 artifact（relation=input）也不列出：
  它们是调用方上传、已在附件/artifact 路径可见，本列表回答「run 产出了什么」。
- 不暴露 object key/owner id/provenance token。

### 1.4 沙箱执行器（`internal/sandbox` + `deploy/sandbox`）

- **rootfs**：`build_python_rootfs.sh` 在 guest 内构建最小 Python rootfs（python3.12 + stdlib 去
  tests/IDLE/Tk + 全部 ldd 依赖 + loader，1093 文件/46 MiB），生成
  `<rootfs>.digest`（mode/size/sha256/相对路径清单的排序哈希）、`.manifest`、`.meta.json`；
  可重放、结账脚本在仓库内。
- **bundle（Go 构建，不是 shell 拼）**：rootfs 只读、uid/gid 65534、`noNewPrivileges`、
  五类 capability 全空、pid/network/ipc/uts/mount namespace、`--network=none`、memory+pids 限额、
  RLIMIT_CPU/NOFILE、`/proc`、tmpfs `/tmp`（nosuid,nodev,noexec,16m）、`/tool` 与 `/inputs` 只读 bind、
  `/workspace` 唯一可写 bind；**固定 argv**：
  `/usr/bin/python3.12 /tool/sales_stats.py --input /workspace/input.json --output /workspace/output.json
  --inputs-dir /inputs --artifacts-dir /workspace/artifacts`。请求数据不进 argv/env/mount option。
- **driver** `sandbox_exec.sh`：一个 WSL 调用完成 stage→runsc run→收集 output.json/artifacts/
  stdout/stderr→`runsc delete --force`→pid-file kill/`ps` 校验→删除 workspace，并输出一份 JSON；
  timeout 由 `timeout --signal=KILL` 执行，host 侧另有进程组 kill + 独立 `cleanup` 入口兜底。
- **Go executor**：读记录 digest（`ORCHARA_SANDBOX_ROOTFS_DIGEST`，缺省 fail closed）、预检
  rootfs digest 与固定脚本、写 staging、独立进程组运行（Windows `taskkill /T`，POSIX 负 pid）、
  5s 心跳（可配）观察 `cancel_requested`、超时/取消/心跳丢失立即 kill 并执行 cleanup、
  解析 driver JSON、校验 cleanup（list/pid/ps/workspace 全清）与 digest 一致；
  任一不满足 → `ErrOrphan`/`ErrDigestMismatch`，绝不报告成功。
- **记录**：rootfs digest、sandbox id、pid、started_at、退出码、cleanup 四事实、heartbeats、
  execution_unit_id 进 exit evidence。

### 1.5 `cmd/supervisor exec --once`

一次进程最多执行一个 attempt（持续循环属 F04A），流程：

1. 用 **supervisor** 证书 claim（`supported_compilers=[orchara-compiler/1.0.0]`、
   `execution_unit_id=exec-<host>-<pid>-<nonce>`）；无工可领 → `{"status":"idle"}`。
2. 用签名 `manifest_url` 取**封套字节**，sha256 必须等于 lease 的 `manifest_sha256`（不信任 JSONB 投影）；
   从中解析 `worker_image_digest`（exit evidence 用）与唯一 python 节点；节点 `config.tool_version`
   必须是固定预置 `1111…`，否则失败并给出具体码。
3. 解析 run 输入：**只读** supervisor DSN 查 `run_artifacts(relation=input)` 中 purpose `task_input`、
   ready、CSV/XLSX 的 artifact（lease 只带 canonical run_input，这是记录在案的缺口），必须恰好一个；
   `createWorkerDownload` 取签名 GET，校验 size+sha256 后才进沙箱（字节仍只走签名链接）。
4. 沙箱执行；心跳每 5s（测试 50–100ms）观察取消；取消/超时/心跳丢失按 §1.4 kill+verify。
5. 成功：严格解析 `output.json`（data 对象 + artifacts 列表），按收集字节准入（≤16、≤10 MiB、
   非空、basename），要求 `summary.json` 且与 `output.json.data` JCS 规范字节一致；通过
   **worker** 证书上传 `summary.json`(final_output)、`summary.csv`(node_output)、其它产物(node_output)、
   log(stdout/stderr, purpose=log)，逐个等 finalize ready，然后 register（output/artifact/log）。
6. 先 `submitWorkerResult(succeeded, output_artifact_id=summary)`，再 `confirmAttemptExit`：
   evidence 必含 T031 四键（started/protocol_version/manifest_sha256/image_digest）+
   execution_unit_id/sandbox_id/sandbox_rootfs_digest/sandbox_started/sandbox_exit_code/
   sandbox_cleanup{list,pid,pid_alive,ps,workspace}/sandbox_started_at/heartbeats/db_writes_drained。
   T031 finalizer 在同一事务提交终态。
7. 失败：输入/图形不支持的**确定性拒绝** → 提交 failed + exit（`started=true` 表示 supervisor 认证
   本 attempt 结束，`sandbox_started=false` 表示没有启动沙箱；见 §2.4）；工具拒绝 → 用工具拒绝码
   （allowlist 校验）并上传安全 diagnostics + log；崩溃/输出非法/产物非法/孤儿/沙箱不可用 → 对应
   具体码 + exit；租约丢失 → 不写 result/exit（服务器已恢复）。取消 → **不提交结果**，
   `started=true`（若沙箱已启动）→ run 停在非终态 `recovery_pending`，取消终态归 T036。
8. stdout 只输出一个 JSON 报告（run/outcome/run_state/error_code/execution_unit_id/sandbox/artifacts/
   heartbeats/duration）；不打印 token、签名 URL、DSN。任何失败以非零退出。

### 1.6 Web：运行详情「沙箱工具调试」面板

`runs.html`/`runs.js` 新增面板：执行单元与租约（`X-Run-Execution-Unit`、attempt started /
lease deadline）、沙箱状态行、退出状态（终态 + error_code）、停止状态（`cancel_requested_at`：
**正在停止 vs 已停止**）、产物列表与下载按钮。因为 T036 端点不存在，面板**不调用**
`POST /runs/{id}/cancel`，而是明确写出 T036 归属与运维路径（SQL 写 `cancel_requested_at` 后跑
`cmd/supervisor exec --once`，当前会停在 `recovery_pending` 而非伪装 cancelled）；资源/退出细节
明确指向 supervisor 的 exec 报告与 exit evidence（公共 API 不暴露沙箱内部状态）。全部用
`textContent`，不渲染 object key，不提前显示成功。

## 2. 契约缺口与显式偏离（如实）

1. **`registerRunArtifact.relation` 词汇闭合**：契约只写 `type: string`；服务端限定
   `output|artifact|log`（422 `validation_failed` + `details.field=relation`），避免不可查询的链接。
2. **run upload media allowlist 闭合**：契约允许任意 `media_type` 字符串；服务端只接受
   `application/json|text/csv|text/plain|XLSX`（422 定位），因为这是预置工具能产出的数据格式。
3. **supervisor 输入解析用只读 DSN**：lease 只带 canonical `run_input`，没有 attempt 级输入列表端点。
   supervisor 以可信基础设施身份只读查 `run_artifacts` 得到候选 id，字节仍只经
   `createWorkerDownload` 签名链接；若未来契约增加 attempt-scoped 输入列表端点可移除该查询。
4. **确定性拒绝的 exit 语义**：T031 的 `started=false` 会 requeue（T029 语义），对永久性拒绝会形成
   重试环。因此 supervisor 对「图形/工具/输入不支持」提交 failed result + `started=true`，并用
   `sandbox_started=false` 明确「没有沙箱运行」；该区分写入 evidence 与报告，不是把未运行的沙箱
   说成运行过。
5. **取消中间态**：取消端点/T036 状态机尚不存在。收到 `cancel_requested` 后 supervisor kill 沙箱、
   不提交 success、确认 exit（started=true）→ T031 finalizer 把 run 停在 **`recovery_pending`**
   （非终态、job held、slot released）。T036 应以 `cancel_requested_at` + 无 live 单元为前提提交
   `cancelled`；本票不发明该终态。
6. **公共 Run 详情新增两个非契约响应头**：`X-Run-Execution-Unit`（live attempt 的 execution_unit_id，
   诊断身份，非凭据）与 `X-Run-Cancel-Requested-At`（取消意图）。沿用 `X-Manifest-SHA256`/
   `X-Run-Attempt-Started-At` 先例；契约 Run 无对应字段。attempt token 仍绝不暴露。
7. **双证书进程**：契约把五个 artifact 路由定为 `x-internal-role: worker`，supervisor 证书被 403；
   `exec --once` 在同一进程持有 supervisor 与 worker 两个客户端身份（文档化桥），F04A 拆进程后应
   只保留各自身份。
8. **`ORCHARA_SANDBOX_ROOTFS_DIGEST` 是必填部署输入**：digest 由 builder 记录；未配置或与 guest
   内 `<rootfs>.digest` 不一致时 fail closed（`sandbox_unavailable`），不做信任首次使用。
9. **新增稳定错误码**（报告/运行 error_code）：`tool_unsupported_graph`、`tool_unsupported_tool`、
   `tool_input_invalid`、`tool_input_ambiguous`、`tool_input_integrity_failed`、`manifest_unavailable`、
   `sandbox_unavailable`、`sandbox_orphan`、`sandbox_failed`、`tool_timeout`、`tool_crash`、
   `tool_output_invalid`、`tool_artifact_invalid`、`worker_protocol_mismatch`；工具输入拒绝码沿用
   T030 allowlist（`negative_amount` 等）并原样作为 run `error_code`。HTTP 复用
   `stale_attempt`/`run_terminal`/`run_state_conflict`/`artifact_not_found`/`artifact_not_ready`/
   `artifact_conflict`/`validation_failed`/`forbidden`。
10. **`cmd/sandboxprobe` 是运维验证入口**（非用户面）：用同一 executor 跑平台自有 isolation/slow/
    crash probe，供部署主机复核沙箱边界；测试也用它证明 timeout/crash 无孤儿。

## 3. 实际命令与原始结果

工作目录 `E:\Orchara\product`；Go 环境 `GOCACHE=E:\orchara-toolchains\gocache`、
`GOMODCACHE=E:\orchara-toolchains\gomodcache`。

### 3.1 rootfs 构建（WSL2 内，真实）

```text
$ wsl -d Ubuntu-24.04 -u root -e bash -lc 'bash /tmp/build_python_rootfs.sh'
rootfs built: /opt/orchara-sandbox/python-rootfs
digest: sha256:03c3f647d1d6e68173bfa9df84b87e5d6162d351bfdd55e4c3b208c0b5412ff5
files: 1093  size: 46M
exit=0
```

手工首跑（在 Go 层之前验证路径，真实 runsc）：

```text
$ bash /tmp/sandbox_exec.sh run --sandbox-id t033-manual-5 ...
{"ok": true, "exit_code": 0, "duration_ms": 578, "timed_out": false,
 "rootfs_digest": "sha256:03c3f6...2ff5",
 "output_json_size": 194,
 "cleanup": {"sandbox_list_matches": 0, "pid_alive": false, "ps_matches": 0,
             "workspace_removed": true}}
output.json = {"data":{"total":"250.00","regions":{"east":"200.00","west":"50.00"},
                       "order_count":3},"artifacts":["summary.json","summary.csv"]}
summary.csv = "region,total\neast,200.00\nwest,50.00\n"
```

### 3.2 T033 用例（真实栈，全部 real）

```text
$ python -m pytest tests/f05/test_t033_migration.py -q
6 passed in 20.13s

$ python -m pytest tests/f05/test_t033_endpoints.py -q
6 passed in 30.96s

$ python -m pytest tests/f05/test_t033_sandbox_run.py -q
5 passed in 43.47s

$ python -m pytest tests/f05/test_t033_pages.py -q
3 passed in 10.57s
```

T033 合计 **4 文件 20 用例**（migration 6、endpoints 6、sandbox_run 5、pages 3）。

真实 runsc 端到端（`test_real_runsc_tool_debug_run_end_to_end`）断言的事实：

- `cmd/supervisor exec --once` 退出 0，报告 `outcome=succeeded`、`run_state=succeeded`；
  run 终态 `succeeded`、`finished_at` 非空、`output_artifact_id` 指向 `summary.json`；
- `summary.json` 通过 `GET /api/v1/artifacts/{id}/content` 与签名下载两条路径都逐字节等于
  ```json
  {
    "total": "250.00",
    "regions": {
      "东区": "200.00",
      "西区": "50.00"
    },
    "order_count": 3
  }
  ```
  `summary.csv` 逐字节等于 `region,total\n东区,200.00\n西区,50.00\n`（UTF-8）；
- `listRunArtifacts` 只返回 `summary.json`+`summary.csv`（2 项，4 字段），**不含 log**；
  另一 owner 与 admin 均 404；
- provenance 3 行（final_output/node_output/log）全部指向同一 live attempt；`run_artifacts`
  relation 分别为 `output`/`artifact`/`log`，attempt_token 为该 attempt；
- exit evidence：`started=true`、`protocol_version=1.0`、`manifest_sha256`=snapshot 冻结哈希、
  `image_digest`=snapshot `worker_image_digest`、`execution_unit_id`=报告值、`sandbox_id`=报告值、
  `sandbox_rootfs_digest`=记录 digest、`sandbox_cleanup_verified=true`、
  `sandbox_cleanup.{list=0,pid_alive=false,ps=0,workspace_removed=true}`、`db_writes_drained=true`；
- 独立孤儿检查（guest `runsc --network=none list` 为空、`/var/tmp/orchara-sandbox` 无该 run 工作区）。

失败/取消/隔离：

- 输入拒绝（负金额 CSV）→ run `failed`、`error_code=negative_amount`、诊断 artifact 含
  `error_code`/`location` 且不含输入正文、无 summary、无孤儿；
- 取消（测试用 **AFTER UPDATE ON run_attempts 触发器**在第一次心跳后写 `cancel_requested_at`，
  模拟 T036 的到达时机）→ 报告 `outcome=cancelled`、`sandbox.started=true`/`cancelled=true`、
  不提交结果、run 停在非终态 `recovery_pending`、attempt 已 exit、evidence `started=true`/
  `sandbox_started=true`、job held、`runsc list` 空、工作区已删；
- isolation probe（同 bundle、同 executor）：uid/gid 65534、hostname `sandbox`、
  `/etc/hostname`/`/etc/shadow` 不可读、bundle 外 foreign 文件不可读、`/inputs/input.csv` 写入
  `PermissionError`、`/workspace` 可写可读、`1.1.1.1:443` 与 guest `127.0.0.1:18080` 均不可连、
  `/proc/1/cmdline` 是本沙箱进程（PID namespace）；cleanup 全清；
- timeout（`--timeout 2s` + sleep 60）→ `outcome=timeout`、`timed_out=true`、cleanup 全清；
  crash（exit 7）→ `outcome=ran`、`exit_code=7`、cleanup 全清；两者 `runsc list` 空、工作区不存在。

### 3.3 Go 单测 / 门禁

```text
$ E:\orchara-toolchains\go\bin\go.exe vet ./...
(no output)                                    exit=0

$ E:\orchara-toolchains\go\bin\go.exe test ./...
ok  	orchara/platform/internal/api	3.238s
ok  	orchara/platform/internal/artifacts	1.633s
ok  	orchara/platform/internal/background	2.295s
ok  	orchara/platform/internal/background/handlers	3.358s
ok  	orchara/platform/internal/catalog	2.549s
ok  	orchara/platform/internal/compiler	(cached)
ok  	orchara/platform/internal/config	1.073s
ok  	orchara/platform/internal/db	(cached)
ok  	orchara/platform/internal/execution	2.578s
ok  	orchara/platform/internal/health	(cached)
ok  	orchara/platform/internal/identity	(cached)
ok  	orchara/platform/internal/jcs	(cached)
ok  	orchara/platform/internal/modelproxy	2.627s
ok  	orchara/platform/internal/objectstore	(cached)
ok  	orchara/platform/internal/policy	(cached)
ok  	orchara/platform/internal/presetexec	1.614s
ok  	orchara/platform/internal/sandbox	(cached)
ok  	orchara/platform/internal/supervisor	(cached)
ok  	orchara/platform/internal/tools	5.016s
ok  	orchara/platform/internal/uuid	(cached)
ok  	orchara/platform/internal/workflow	(cached)
exit=0

$ E:\orchara-toolchains\go\bin\gofmt.exe -l .
(no output)                                    exit=0

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
exit=0
```

新增 Go 单测 **27 个**：`internal/sandbox` 15（bundle/argv/digest/协议/产物准入 + fake runner 的
success/orphan/digest-mismatch/timeout/cancel-before-start/cancel-mid-run/lost-lease）、
`internal/presetexec` 12（成功矩阵、图形/工具拒绝、输入 0/2/非 CSV、完整性、manifest 哈希、
取消、超时、拒绝码、崩溃/非法输出、孤儿、沙箱不可用）。

`tests/f03` 与全量 `tests/f05` 的结果见 §4 的最后两行；批次规则下未跑 25 分钟全量 `pytest tests`。

## 4. 票面验收逐条

- [x] **失败/取消沙箱可回收**：真实 timeout（`--timeout 2s`+sleep 60）与 crash（exit 7）后
  `runsc list` 空、pid 不存活、`ps` 无匹配、workspace 删除；取消（第一次心跳后写
  `cancel_requested_at`）杀死执行中的沙箱，不提交结果、run 停在非终态、无孤儿。
- [x] **旧 attempt 不能登记当前产物**：撤销/非当前 token 在五个路由均 409 `stale_attempt`；
  `registerRunArtifact` 还要求 provenance=该 attempt、artifact ready、owner=run owner；同 relation
  幂等、异 relation 409 `artifact_conflict`；跨 run 的 artifact 一律 404。
- [x] **输入文件和日志不越权**：scoped download 只允许本 run 输入（输出/外部 → 404）；
  `listRunArtifacts` owner-only 且按 purpose 排除 log；沙箱内输入 `/inputs` 只读（写入
  `PermissionError`）、host/foreign 文件不可读、无网络；`/proc/1` 是自身 PID namespace。
- [x] **通过上述端到端入口实际演示；失败、授权与重试/恢复边界有断言，不以单层 mock 成功代替**：
  一条真实 runsc 工具调试 run 从 T028 上传 → T027 debug run → claim → 沙箱 → scoped 上传/登记 →
  result → exit → T031 终态；失败路径（工具拒绝/崩溃/超时/取消/孤儿）、角色/权限（403/404）、
  旧 token（409）、终态（409）、finalize 等待均有断言。Go 层 fake 只用于协议/编排单测，真实路径
  另有真实 PG/mTLS/runsc 用例覆盖。
- [x] **记录版本、输入/hash、实际命令、原始结果与环境 profile；stub/real 分报，缺条件 blocked，
  未运行 not_run**：§0/§3 记录 rootfs digest、runsc/WSL/kernel/Python 版本、输入 CSV 的
  sha256（真实 T028 上传计算）、固定 argv、原始 output/summary 字节、cleanup 事实；
  §2 逐条列偏离；§5 列 not_run。

## 5. 边界与未运行（not_run）

- `not_run`：浏览器 DOM 驱动页面（无 Playwright/浏览器；与既有票相同边界）。
- `not_run`：T036 的 `POST /api/v1/runs/{id}/cancel` 端点与 cancelling→cancelled 终态（本票只保证
  kill+无孤儿+非终态停放）；测试用 SQL/trigger 写 `cancel_requested_at`。
- `not_run`：T037 的任意工具构建/依赖锁/镜像 digest、F04A 的 supervisor/worker 进程拆分与真实
  LangGraph/Python worker；本票只跑固定预置 `sales_stats`。
- `not_run`：Docker（宿主无 Docker）与「生产 server YAML」；沙箱边界以 WSL2+runsc 真实执行验证。
- `not_run`：全量 `python -m pytest tests -q`（批次规则，预计 ~25 分钟；本票只跑 `tests/f03` 与
  `tests/f05` 以及 Go 门禁）。
- 沙箱网络证据为「连不上」而非「全协议不可达」：`--network=none` + 独立 netns，probe 对公网与
  guest loopback 端口各尝试一次；没有做 DNS/ICMP 全矩阵与代理穿透实验（P03 已做更宽的矩阵，
  本票复用其结论但未重跑全部 P03 用例）。

## 6. 后续票可依赖的接口与表

- 表：`platform.run_artifact_uploads(artifact_id PK, run_id, attempt_token, purpose, created_at)`；
  `run_artifacts` 链接关系的 writer 仍是 `registerRunArtifact`（relation output/artifact/log），
  input 链接仍属 T027。F07 删除 artifact 时必须同删 provenance。
- 内部路由（worker 角色，`attempt_token` 在 body）：`createWorkerUpload`/`completeWorkerUpload`/
  `createWorkerDownload`/`getWorkerArtifactStatus`/`registerRunArtifact`；**首次** completion 入队
  `artifact_finalize`（dedup `run-artifact:<id>:<sha>`），scanning/ready 的同 hash 重放只返回当前
  artifact、不再入队。T036/T037/F04A 可复用。
- 公共读：`GET /api/v1/runs/{run_id}/artifacts`（owner-only，4 字段，cursor；log 排除）；
  Run 详情新增 `X-Run-Execution-Unit`、`X-Run-Cancel-Requested-At`（非契约）。
- Go 接口：
  - `sandbox.NewExecutor(Options)` + `OptionsFromEnv(productRoot)`；`(*Executor).Run(ctx, Request{Program,
    ProbeMode, MediaType, Input, InputJSON, ExecutionUnitID, Timeout}, Control{Heartbeat, HeartbeatInterval})`
    → `Outcome{SandboxID, RootfsDigest, SandboxStarted, Cancelled, TimedOut, ExitCode, Duration,
    Stdout, Stderr, OutputJSON, ProbeJSON, Artifacts, Cleanup, CleanupVerified}`；错误
    `ErrRootfsUnavailable/ErrToolUnavailable/ErrRunnerUnavailable/ErrDriverReport/ErrTimeout/ErrCancelled/
    ErrHeartbeat/ErrOrphan/ErrDigestMismatch/ErrOutputInvalid`。
  - `deploy/sandbox/sandbox_exec.sh run|cleanup`（固定参数面，guest 内执行）；builder
    `build_python_rootfs.sh`；`ORCHARA_SANDBOX_ROOTFS_DIGEST` 必填。
  - `presetexec.Run(ctx, Deps{Client, Inputs, Sandbox, Options, Logger}) Report`；
    `Client` 是 supervisor/worker 协议集合（claim/heartbeat/result/exit/upload/complete/status/
    register/download/fetch/put/run-state），F04A 可换成真实 worker 进程。
  - `execution.ListRunInputArtifacts(ctx, db, runID)`（supervisor 只读输入解析）；
    `execution.NewRunArtifactStore(...)`；`execution.Service.CancelRequestedAt`。
- 运行 error_code（report/`runs.error_code`）：见 §2.9；T036 应识别
  `recovery_pending + cancel_requested_at + 无 live 单元` 并提交 `cancelled`。
- 沙箱工具验证入口：`cmd/sandboxprobe --mode isolation|slow|crash`（同一 executor，打印事实 JSON）。

## 7. 文件与提交

- feat：`db/migrations/0019_run_artifact_uploads.sql`、`internal/execution/runartifacts.go`、
  `internal/catalog/runartifacts.go`、`internal/api/runartifacts.go`（+ internal.go/server.go/debug.go）、
  `internal/execution/{store.go,service.go}`、`internal/sandbox/*`、`internal/presetexec/*`、
  `cmd/supervisor/*`、`cmd/sandboxprobe/main.go`、`deploy/sandbox/*`、
  `apps/web/src/{runs.html,assets/runs.js}`
- test：`tests/support/{t033.py,fixtures.py}`、`tests/f05/test_t033_*.py`（4 文件 20 用例）、
  `tests/f05/test_t028_migration.py` 与 f00b/f02a/f03/f05 迁移链断言 0018→0019
- docs：本报告、`PROGRESS.md`、NOTES 追加
- 提交：`feat(f05): execute and register sandboxed tool-debug run artifacts (T033)`、
  `test(f05): cover run artifacts, runsc sandbox and the tool-debug executor (T033)`、
  `docs(f05,f03): record T033 acceptance, sandbox profile and gates`；未推送、未关 issue、
  `docs/spec/**` 与 `contracts/**` 未改。

## 8. 规格变更

无。`docs/spec/**`/`contracts/**` 未修改，`python tools/sync_spec.py --check` 为
`no drift: spec copy and contract snapshot match the manifest`。

## 9. 复核修复（2026-11-03）

### 9.1 `completeWorkerUpload` 重放不再尝试入队

**发现：** `scanning`/`ready` 的同 hash 重放仍调用 `background.EnqueueTx`。第一次 completion
已入队唯一 finalize job；重放时 artifact revision 已前进（ready 为激活后的 revision），这次
入队只能命中 `ErrDedupConflict` 并被吞掉——一个“注定失败”的入队，且把幂等性寄托在吞错上。

**修复：** `scanning`/`ready` 分支只做同 hash 校验 + 幂等提交，返回当前 artifact 状态（202），
**完全不调用** EnqueueTx/notify。quarantined 同 hash → 409 `artifact_conflict`（终局裁决不可
重放）；deleted/tombstone → 404 `artifact_not_found`（已不在 run 可见范围）；异 hash 仍是
409 `artifact_conflict`。不新增 job。

**回归测试：**
`tests/f05/test_t033_endpoints.py::test_complete_replay_on_scanning_and_ready_never_enqueues_a_job`
在真实 mTLS/PG 上分别重放 scanning 与 ready 状态，断言 202 + 当前状态 + `background_jobs` 中
恰好一个 `artifact_finalize` 且 id 不变；
`test_complete_on_quarantined_or_deleted_artifact_is_refused` 断言 quarantined=409、
deleted=404 且 job 数不增。

### 9.2 沙箱结论分类：报告证明启动、pre-start 拒绝、坏 base64

**发现：** `Outcome.SandboxStarted` 在解析 driver 报告前就被置 true；driver 的
`{"ok":false,"error":...}` pre-start 拒绝文档没有 cleanup 块，却被送进清理断言/错误分类；
`decodeB64` 静默返回 nil，损坏的 base64 可能被当成“空输出”。

**修复（`internal/sandbox/executor.go`）：**
- `SandboxStarted` 只在解析出 `mode:"run"` 报告（runsc 已实际启动）后置 true。kill 导致报告
  缺失时，使用独立 cleanup 入口返回的 `pid`（driver 仅在 runsc 启动后写 pid 文件）作为启动
  证明；cleanup 同时上报 `pid`（`sandbox_exec.sh` cleanup 文档新增字段）。
- 没有 `mode` 的 `{"ok":false,"error":...}` 文档解析为新的 typed `ErrSandboxRefused`：立即返回，
  不做 cleanup/orphan 断言，`SandboxStarted=false`。只有 `mode:"run"` 且 `!ok`/`error` 的
  post-launch 报告才断言 cleanup 并归为 `ErrOrphan`。
- 新增 `applyReportStreams`：stdout/stderr/output_json/probe_json/artifact 的 base64 解码错误
  一律返回 `ErrOutputInvalid`（不再静默 nil）；cleanup 已由 driver 完成，不重复调用。
- `presetexec` 将 `ErrSandboxRefused` 归入 `sandbox_unavailable`；exit evidence 的
  `sandbox_started`/`sandbox_cleanup_verified` 使用证明后的 flag（pre-start 拒绝为 false，
  `started=true` 仍表示已领取 attempt 的确定性失败，避免 T029 重试环）。

**回归测试（Go）：**
- `internal/sandbox`: `TestExecutorTreatsADriverRefusalAsPreStart`（`ErrSandboxRefused`、
  `SandboxStarted=false`、不调用 cleanup）、`TestExecutorRejectsCorruptBase64AsOutputInvalid`
  （`ErrOutputInvalid`、不重复 cleanup）、cancel/timeout 用例断言 cleanup pid 证明 `SandboxStarted=true`。
- `internal/presetexec`: `TestPreStartSandboxRefusalKeepsTheProvenFalseFlag`
  （`sandbox_unavailable`、`sandbox_started=false`、cleanup_verified=false）。
- 真实 runsc 回归：`tests/f05/test_t033_sandbox_run.py`（含 cancel/timeout/crash）通过。

## 第二次独立复核修复（2026-11-03）

- **缺陷（P1）**：`/workspace` 是宿主目录的无配额 rw bind，不可信工具/构建可持续写入并耗尽宿主卷，
  违反 SECURITY §2 磁盘限额要求。
- **修复**：`deploy/sandbox/sandbox_exec.sh` 把 `/workspace` 改为 size 限定的 tmpfs
  （`--workspace-quota-mb`，默认 512 MiB，范围 1–4096），cleanup 必须先 `umount` 并把
  `workspace_unmounted` 纳入 `ok`；`internal/sandbox` bundle 增加 `RLIMIT_FSIZE`（默认 64 MiB），
  Go 侧把 quota 传给每次沙箱运行。
- **证明**：`tests/f05/test_t037_build_isolation.py::test_the_build_workspace_has_a_disk_quota`
  在真实 runsc、8 MiB 配额下让 hook 持续写入，沙箱内捕获 ENOSPC 并输出
  `T037_QUOTA_ENFORCED`，构建仍可完成；`internal/sandbox` 单测断言 RLIMIT_FSIZE 与 argv。
- 状态仍为 `passed`；tmpfs 配额限制的是总写入量，磁盘/inode 的宿主级配额仍属 F15B 部署面。
