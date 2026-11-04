# F15A T057 验收：在干净开发环境启动并跑通销售冒烟

- 任务：F15A / GitHub Issue **#58**（票 `T057`，规划 ID F15A.01/F15A.03），分支
  `t/t057-clean-env-smoke`（worktree `E:\orchara-worktrees\t057`）；父门禁 F15A 部署侧。
- 状态：**`passed`**（本票范围 = 部署者可见的可重放启动产品 `deploy/dev/` + 通过真实 HTTP API
  执行的销售/失败/取消/stub 模型 profile 冒烟 + 机器可读证据 + `tests/acceptance/` 验收套件 +
  过时 README 修正）。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP/mTLS + 真实
  `cmd/maintenance` 进程 + 真实 Python 子进程 + Node 执行页面脚本；**无 mock、无跳过、无迁移**。
- 边界（如实）：**Compose/容器部署 `blocked`（`container_runtime`）**；**正式隔离/任意脚本
  `blocked`（`linux_isolation`；WSL 有 `runsc release-20241118.0`，但 WSL 不是被接受的隔离
  宿主，ADR 0002 / `BOOTSTRAP.md:71`）**；**真实模型 profile `blocked`
  （`model_credentials`）**；**S3/MinIO `blocked`（无端点；本地 FS store 不是 S3 兼容性
  声明）**；Redis `not_run`（平台只用 PostgreSQL 轮询）；`cmd/supervisor exec` `not_run`；
  `cmd/worker` 是 liveness-only 骨架 `not_run`；浏览器 DOM `not_run`。**M1 仍须 F02B/F08/F13A
  门禁，本票不声明完整 M1。**
- 另两条 dev-only 限制：maintenance 进程的数据库身份仍是 owner DSN（迁移只创建
  `orchara_worker`，没有独立的 maintenance DB 角色）；开发 mTLS 证书由 `dev_pki.py` 用
  `cryptography` 生成，只适合开发机。

## 0. 工作记录（简短）

1. 读 `BRIEF.md` §3/§7/§8、`briefs/T057.md`、issue #58、探索笔记 §5.1–§5.5 与 §3.7；
   `cmd/{api,maintenance,supervisor,worker}/main.go`、`tests/support/{stack,f00b,t030,t033,t025,t029,t036,t039}.py`、
   `deploy/`、`product/README.md`、`reports/p0/D00-environment-report.md`；逐项实测环境事实（§2）。
2. TDD 垂直切片（红 → 绿）：
   - **bring-up**：先写 `tests/acceptance/test_t057_bringup.py`，`import bringup` 失败
     （4 errors）→ 实现 `dev_pki.py` / `bringup.py` → 4 passed；
   - **销售**：独立 `decimal.Decimal` 从 CSV 求真值（`250.00`/东区 `200.00`/西区 `50.00`/
     3 单）→ 上传/工具运行/下载产物 → 断言与工具输出逐字一致；
   - **失败**：缺列 `missing_column` 与重复订单 `duplicate_order_id`，业务失败一次即终态；
   - **取消**：debug Run → 匿名 401、他人取消 404、owner 取消 `cancelled`，内部 mTLS 再领取 204；
   - **模型 profile**：admin 登记 stub 候选 → `validate` → 真实 `model_validate` → 报告
     `passed`、封存文档 `provider=stub, stub=true`；
   - **页面**：Node 加载 4 个真实页面资源，用真实冒烟数据执行页面纯函数；
   - **超时/重试**：用 `--tool-timeout 1ms` 重启 maintenance，真实触发 `tool_timeout`，
     断言重试到 `MaxAttempts=3` 后终态失败、诊断 `retryable=true`。
3. 冒烟暴露 1 个真实缺陷并修复（§6）：失败诊断附件在 job 已 failed 时仍可能 `scanning`，
   页面立即读取会得到 409 而不是解释。另做 1 个 AC2 小修：状态页渲染 profile/stub 标记。
4. 用交付入口本身生成证据：
   `python deploy\dev\bringup.py smoke --fresh --out reports\f15a\dev-smoke-run.json`
   （`2026-11-04T08:43:53Z → 08:44:03Z`，`status=passed`）。
5. 门禁（§3）→ 本报告 / PROGRESS / README → 按 `feat(f15a)` / `fix(f15a)` / `test(f15a)` /
   `docs(f15a)` 提交 → merge `integration/f13a-f15a-f09-f10`（`458c132`）→ 重跑目标套件。
   未 push、未改 GitHub issue、未改 `docs/spec/**` 或 `contracts/**`。

## 1. 交付内容

### 1.1 `product/deploy/dev/`（部署者入口）

| 文件 | 内容 |
|---|---|
| `bringup.py` | `DevStack`：建空库、`go build` 4 个二进制、生成开发证书/对象签名密钥、`admin migrate`、`admin bootstrap`、启动 `cmd/api` 与 `cmd/maintenance run`、等待 `/health/ready` 与内部 mTLS；CLI `up`（常驻，Ctrl-C 停止）与 `smoke`（一次性跑完并写证据）；**每条命令执行前先打印**，凭证类环境变量打印为 `<redacted>` |
| `dev_pki.py` | 开发专用 X.509：CA、`127.0.0.1`/localhost SAN 的 server 证书、`maintenance`/`supervisor`/`worker` 客户端证书（Organization 即内部角色）；私钥 0600、从不打印 |
| `smoke.py` | 公开 HTTP API 的冒烟驱动（§1.3），返回完整证据字典；`--out` 写 JSON；不带内部证书时把"取消后不可领取"记为 `not_run` |
| `dev_http.py` | 标准库 HTTP 客户端：真实 cookie jar、不跟随重定向、逐响应返回 |
| `README.md` | 一条命令、逐条原始命令、profile/占位镜像 digest、冒烟覆盖、blocked/not_run、清理与故障排查 |

`bringup.py` 生成的 dev 状态（二进制/对象字节/证书/日志）位于 `product/.devstack/`，已加入根
`.gitignore`；命令记录里只保留 `ORCHARA_*` 非敏感值。

**启动序列（与 README §2 的原始命令一一对应）**：`pg_dev.py init/status` → 建空库 →
`go build ./cmd/{api,admin,maintenance,supervisor}` → `dev_pki.py --dir` + 48 字节对象签名密钥 →
`admin migrate` → `admin bootstrap`（口令走隐藏 stdin）→ `cmd/api`（公开 HTTP + 内部 mTLS，含
`ORCHARA_WEB_DIR`）→ `cmd/maintenance run --poll-interval 200ms`（双池：`artifact_finalize`
的 `system_io` + 其余 kind 的 `work`）。

- profile 固定 `dev-stub`；`--profile real-model/linux-sandbox` 被拒绝并给出 `blocked`
  原因（无凭据/无被接受的隔离宿主），不是静默降级。
- `ORCHARA_WORKER_IMAGE_DIGEST` 是**显式占位值**（`sha256:aaaa…`，64 位 a），证据中记为
  `{"kind":"deployer-placeholder","image_built":false}`；本票不构建镜像。
- `--run-seconds N` 用于自动化检查：打印 `ready` 后 N 秒走同一 `finally` 停止路径并退出 0
  （实测见 §3）；交互式 Ctrl-C 复用同一路径，未单独自动化。

### 1.2 冒烟（真实 HTTP，无单层 mock）

| 场景 | 真实路径 | 断言 |
|---|---|---|
| 环境/profile | `GET /api/status`、`/health/ready`、`GET / /login /apps /attachments /runs /models` | `profile=dev-stub`、`stub_results=true`、`status=ready`；页面 `text/html` 且状态页含 `id="environment-profile"`/`id="environment-stub"` |
| 线性销售 | 登录/建用户 → `POST /artifacts/uploads` → 签名 `PUT` → `complete` → `POST /tool-runs`（Idempotency-Key）→ 轮询 → `GET /artifacts/{id}/content` | `succeeded`；`summary.json`/`summary.csv` 与**独立 Decimal 真值**逐项相等；记录输入与两个产物的 sha256/size |
| 失败 | 缺列 CSV、重复订单 CSV → tool run | `failed` + `missing_column`/`duplicate_order_id`；`diagnostics` 文档 `status=rejected`, `retryable=false`, message/fix/location；无 summary 产物 |
| 取消 | `POST /applications` → `PUT draft`（If-Match）→ `POST /debug-runs` → 匿名读 → 他人取消 → owner `POST /runs/{id}/cancel` | 匿名 401 `unauthenticated`；他人 404 `resource_not_found`；owner 202 `state=cancelled/phase=idle`、`X-Run-Cancel-Requested-At`、无 output；内部 `POST /internal/v1/jobs/claim` = **204** |
| 模型 profile | admin `POST /models`（stub）→ `validate`（Idempotency-Key）→ 轮询 evaluation → `GET /models/{id}`、`GET /evaluation-reports/{id}` → report artifact content | `validation_state=passed`、report `status=passed`；封存文档 `provider=stub`、`stub=true`、`compatible=true`；非 admin 登记 403 |
| 页面逻辑 | Node 24 加载 `apps/web/src/assets/{app,attachments,runs,models}.js`，真实冒烟数据 | `summaryRows/summaryTotals/toolDownloadLinks/diagnosticsFields/describeRunCancel/mergeRunSnapshot/describeReport` 与期望一致，输出 `f15a page logic ok` |

销售真值由 `smoke.independent_sales_truth` 用 `decimal.Decimal` 从 `examples/sales/input.csv`
计算；`250.00`/`200.00`/`50.00`/`3` 是 WORKFLOW-SPEC §6 的已知好值，先断言"独立计算 == 已知好值"，
再断言"工具输出 == 独立计算"，不是用工具输出反推期望。

### 1.3 证据（机器可读）

`reports/f15a/dev-smoke-run.json`（schema `orchara.f15a.dev-smoke/1`）包含：
profile/`stub_results`、版本（Python 3.12.2 / Go 1.27.3 / Node v24.13.0 / PostgreSQL 16.4）、
实际命令（含 env，敏感值 `<redacted>`）、步骤状态、id、产物 sha256、`blocked`/`not_run`、
`defects` 与 `environment`（数据库、对象 store 类型、占位镜像 digest、runtime lock sha256、
日志路径、启动时间）。本次真实运行的要点：

| 事实 | 值 |
|---|---|
| 冒烟窗口 | `2026-11-04T08:43:53Z` → `08:44:03Z`，`status=passed`，7/7 步骤 passed |
| 销售输入 `sha256` | `a6be4cf4d34d0fdce677964afc38a6ffa02145d4325b32a9aa0237bad2cd88ca` |
| `summary.json` `sha256` / 字节 | `84751af4c9cd7057707d57c93055593f7cebc0f1c634c81f3f9a9c9a58d83987` / 110 |
| `summary.csv` `sha256` / 字节 | `bb60f9428113f627e31b70eda2c3f2373339699d675f774ac8f0ba9825fd7a65` / 40 |
| 取消 Run | `22097a46-f587-494b-93ba-a70af2c9ba1e`，`cancelled`，内部 claim 204 |
| stub 模型 report | `b0c3e33a-a0d5-4cb3-b9d5-30358b09e9d2`，封存文档 `b862ce4e5b5fe7544e54efb949111079722dc3e0ef839cbce60169654e187f8d` |

证据生成过程自身断言：不含管理员/创作者口令、不含 `postgresql://` DSN、不含 `PGPASSWORD`，
也不含 PostgreSQL 本地口令。

### 1.4 `tests/acceptance/`（13 用例，真实栈）

`conftest.py` 按 `tests/f06/conftest.py` 方式导入 `support.fixtures`，并新增两个 session fixture：
`dev_stack`（**通过 `bringup.DevStack.start` 启动**，不是测试专用栈）与 `smoke_evidence`
（对同一栈执行一次冒烟）。

| 文件 | 用例 |
|---|---|
| `test_t057_bringup.py` | 迁移链连续 + 唯一 bootstrap 管理员；`/health/ready` + 6 个真实页面；`/api/status` profile/stub；状态页渲染 profile/stub；maintenance 双池日志 |
| `test_t057_smoke.py` | 证据 schema/版本/命令序列/blocked+not_run；销售独立 Decimal 真值与 job/artifact 行（`attempt_count=1`）；两种失败均为 business 失败且 `attempt_count=1`、无 summary；取消 owner-only + run/job 行（`state=void`、无 attempt）；stub 模型 profile + report 行 + artifact 哈希；Node 页面逻辑；证据无凭证 |
| `test_t057_timeout.py` | 真实 `--tool-timeout 1ms`：`tool_timeout` 重试到 `MaxAttempts=3` 后 `failed`、`failure_class=infrastructure`、诊断 `retryable=true`、维护日志出现 `retried`→`failed` |

真实依赖：PostgreSQL 16.4（真实迁移链与数据行）、真实编译 API/maintenance、真实 HTTP/mTLS
（含内部客户端证书）、真实 Python 子进程、Node 24。

### 1.5 页面文件改动（仅两处小修，均记录）

1. `apps/web/src/assets/attachments.js` — **真实缺陷修复**：`tool_run` 失败路径"尽力"
   发布诊断后立即报告 failed，页面在终态瞬间读取可能是 `scanning` 的产物并显示 409；
   现在用纯函数 `shouldPollToolArtifact`（scanning 继续、ready/quarantined/deleted 停止、有上限）
   有界等待真实 `ready` 后再渲染。Node 用例覆盖判定函数；DOM 驱动仍 `not_run`。
2. `apps/web/src/index.html` — **AC2 小修**：状态页原本已读取 `/api/status` 但不显示
   `profile`/`stub_results`；新增一张卡片（10 行脚本）从同一响应渲染，未新增页面/平台功能。
   acceptance 断言页面标记 + `/api/status` 值。

两处都在 `reports/f15a/dev-smoke-run.json` 的 `defects`/`profile.stub_visible_in_product_output`
中留有机器可读记录；没有 Go/契约/迁移改动。

### 1.6 文档修正

- `product/README.md`：从过时的"D00 + P01–P05 + F00 + F01，平台尚未实现"改为当前真实范围
  （F00B/F02A/F03/F05/F06/F04A/F15A-T057）、仍不是完整 v1 的明确表述、`cmd/worker` 骨架边界、
  blocked 表与 `deploy/dev` 启动入口。
- `product/PROGRESS.md`：新增 T057 行（pass/blocked/not_run 全列），当前发布范围加入 F15A/T057。

## 2. 环境事实（本次实测；命令原样）

| 项 | 实测 |
|---|---|
| PostgreSQL | `python tools\pg_dev.py status` → `running: true`, `port 55432`, `server_version 16.4`, `data_dir E:\orchara-pg` |
| Go / Python / Node | `go version go1.27.3 windows/amd64`；`Python 3.12.2`；`v24.13.0`；`cryptography 36.0.2` |
| Docker/Compose | `Get-Command docker,docker-compose` → 均不存在 |
| Redis | `redis-server` 不存在；`127.0.0.1:6379 open=False` |
| S3/MinIO/AWS | `minio`/`aws` 不存在；`127.0.0.1:9000 open=False`；`mc` 解析为 `E:\VC98\bin\MC.EXE` = Midnight Commander，**不是** MinIO 客户端 |
| WSL runsc | `wsl -d Ubuntu-24.04 -u root runsc --version` → `runsc version release-20241118.0`；但 WSL 不是被接受的隔离宿主（ADR 0002 / `BOOTSTRAP.md:71`），正式隔离 `blocked` |
| `cmd/worker` | `cmd/worker/main.go:73`：`logger.Printf("runtime ready: id=%s role=%s (skeleton, no work claimed)", id, role)` → 不领取工作；真实循环是 maintenance/supervisor |
| 模型凭据 | 无任何已获批端点/凭据；仅 `stub` provider（`dev-stub`）可用 |
| 镜像 | 未构建任何镜像；manifest 使用占位 `sha256:aaaa…`，证据 `image_built=false` |
| 部署目录 | 变更前 `product/deploy/` 只有 `sandbox/` 4 个脚本；本票新增 `deploy/dev/` |
| maintenance DB 身份 | 内部身份 = maintenance 客户端证书；DB 身份 = owner DSN（迁移无独立 maintenance 角色，dev-only 限制） |

## 3. 门禁原始输出

```text
# 本票目标套件（merge 后复跑）
$ python -m pytest tests/acceptance -q
.............                                                            [100%]
13 passed in 57.13s

# 交付入口自身的证据运行（真实进程/真实 PG/真实 HTTP）
$ python deploy\dev\bringup.py smoke --fresh --out reports\f15a\dev-smoke-run.json
[smoke] orchara.f15a.dev-smoke/1 status=passed profile=dev-stub
[smoke]   environment      passed
[smoke]   identity         passed
[smoke]   sales            passed
[smoke]   failure          passed
[smoke]   cancel           passed
[smoke]   model_profile    passed
[smoke]   page_logic       passed
[bringup] evidence written to reports\f15a\dev-smoke-run.json

# 相邻套件（页面/工具链；分开跑，避免仓库既有 conftest 同名冲突）
$ python -m pytest tests/tooling tests/f00 -q
37 passed in 62.24s
$ python -m pytest tests/f01/test_t015_pages.py tests/f01/test_f01_app_js.py -q
13 passed in 39.59s
$ python -m pytest tests/f05/test_t030_pages.py -q
4 passed in 32.19s
$ python -m pytest tests/f15a -q              # merge 带入的 T053 套件，单独运行
36 passed in 76.78s

# Go / 静态（merge 后）
$ go build ./...      -> exit 0
$ go vet ./...        -> exit 0
$ gofmt -l .          -> (empty)
$ go test ./...       -> exit 0（全部 ok，无 FAIL）
$ python tools\sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest   (exit 0)
```

说明：`tests/tooling` 与 `tests/f15a` **同一次调用**串跑时，T053 的
`test_one_broken_rule_is_caught_by_its_own_assertion[staging_overwrite_preserves_sealed]`
出现过 1 次时序失败；该用例单独运行与 `tests/f15a` 整体单独运行均通过（36 passed）。
该套件与本票改动面无交集，如实记录为环境/串跑时序 flake，不作为本票通过依据。

## 4. AC 逐条

| # | 验收项 | 结论 | 证明 |
|---|---|---|---|
| 1 | 新环境可复现 | **passed** | `bringup.py` 从空库执行 migrate→bootstrap→API→maintenance；README 列出可手抄的原始命令；`test_the_bring_up_creates_a_fresh_migrated_database` 断言迁移链连续 + 唯一管理员；证据含命令与版本 |
| 2 | stub 标识明显 | **passed** | `/api/status` `profile=dev-stub, stub_results=true`；状态页渲染 profile/stub（`test_the_status_page_renders_the_profile_and_the_stub_flag`）；stub 模型封存报告 `provider=stub, stub=true` 经 API 读回并在 `/models` 页面渲染；证据 `profile.stub_visible_in_product_output` |
| 3 | 缺 runsc 不冒充正式任意脚本环境 | **passed（口径）** | 证据 `blocked: linux_isolation`、`not_run: supervisor_sandbox_exec`；报告与 README 写明 runsc 存在但 WSL 不是被接受宿主；预置工具走 Python 子进程路径，未声称 gVisor |
| 4 | 至少预置工具和明确模型 profile 可跑 | **passed** | 销售 `sales_stats` 真实 tool_run `succeeded`（summary 两个产物 + 独立 Decimal 真值）；stub 模型 profile `validate` 真实执行、`passed` 报告；证据记录 provider/stub/data_destination |
| 5 | 错误/超时/取消可解释 | **passed** | 失败：`missing_column`/`duplicate_order_id` 诊断文档（message/fix/location，`retryable=false`）；超时：真实 `tool_timeout` 重试到 3 次后终态，诊断 `retryable=true`，维护日志 `retried`→`failed`；取消：`cancelled` + `X-Run-Cancel-Requested-At` + 页面 `describeRunCancel`"已停止"文案 + 无 output |
| 6 | M1 仍须 F02B/F08/F13A 门禁 | **passed（声明）** | 报告页首/README/PROGRESS 明确"不声明完整 M1"；F02B/F08 不在本分支，F13A 的评测执行器不在本票 |
| 7 | 通过端到端入口演示；失败/授权/重试边界有断言 | **passed** | 全部经公开 HTTP API（登录/CSRF/幂等键/签名 URL/tool run/debug run/cancel/模型验证）；授权：匿名 401、他人取消 404、非 admin 模型登记 403；重试：业务失败 1 次不重试 vs 超时重试到 `MaxAttempts=3`；取消后内部 claim 204 |
| 8 | 记录版本/输入 hash/命令/原始结果/profile；stub 与 real 分报；缺条件 blocked、未运行 not_run | **passed** | `reports/f15a/dev-smoke-run.json` + 本报告 §2/§3；`blocked` 与 `not_run` 分列；无真实模型/S3/容器运行被写成通过 |

## 5. blocked / not_run（不预填通过）

| id | 状态 | 具体缺项 | 承接 |
|---|---|---|---|
| `container_runtime` | blocked | 本机无 Docker/docker-compose；本票交付"多进程 + 本地 PG"启动，不冒充 Compose | F15A/F15B 部署票 |
| `linux_isolation` | blocked | WSL Ubuntu-24.04 有 runsc，但 WSL 不是被接受的隔离宿主；正式任意脚本沙箱未运行 | F13B / 独立 Linux 宿主 |
| `model_credentials` | blocked | 无已获批模型端点/凭据；只有 `dev-stub`/`capacity-stub` 的 stub provider | 部署者提供凭据 |
| `s3_object_store` | blocked | 无 S3/MinIO 端点；这里真实使用的是本地文件系统 store，不声明兼容性 | #54/F15A（同分支 T053 已记录候选 `blocked`） |
| `redis_wakeup` | not_run | 无 Redis；平台用 PostgreSQL 轮询，没有为拓扑图新增 Redis | F00B/F15B |
| `supervisor_sandbox_exec` | not_run | `cmd/supervisor exec --once` 需要被接受的隔离宿主；本冒烟不需要 debug 工具执行 | F13B/F05 |
| `cmd_worker` | not_run | liveness-only 骨架，不领取工作；启动它不增加能力，反而会误导 | 后续 worker 票 |
| `evaluation_ui` | not_run | 本构建没有评测页面；F13A 的逐例报告 UI 未实现 | F13A |
| `browser_dom` | not_run | 真实页面脚本由 Node 用真实数据执行；未驱动浏览器 | 后续 UI 验收票 |
| 交互式 Ctrl-C | not_run | `up` 的启动/ready/干净停止已用 `--run-seconds` 自动化验证；Ctrl-C 复用同一 `finally` 停止路径，未单独脚本化 | — |

## 6. 冒烟暴露的缺陷与修复

| 缺陷 | 级别 | 现象（真实复现） | 修复 | 证明 |
|---|---|---|---|---|
| 失败诊断附件读取竞态 | 小（页面逻辑） | job 已 `failed` 时诊断附件可能仍 `scanning`，页面/冒烟立即 `GET .../content` 得到 409 `artifact_not_ready`，用户看不到解释 | `attachments.js` 新增 `shouldPollToolArtifact` + 有界等待真实 `ready`；烟测侧同样等待并记录实测延迟 | 页面纯函数由 Node 断言；`tests/acceptance/test_t057_smoke.py` 断言两份诊断文档；证据 `defects[0]` |
| 状态页不显示 profile/stub | 小（AC2 缺口） | `/api/status` 已返回 `profile`/`stub_results`，但人看的 `/` 页面不渲染，部署者无法从 UI 区分 stub | `index.html` 新增一张卡片从同一响应渲染 | `test_the_status_page_renders_the_profile_and_the_stub_flag` |

没有发现其他需要本票修复的缺陷；未改 Go 代码、未改契约、未新增迁移。

## 7. 下一票接口（复用约定）

- `deploy/dev/bringup.py::DevStack`：`start(state_root, database, fresh, admin_password, …)`、
  `facts()`（非敏感环境事实，可直接并入证据）、`stop()`、`stop_maintenance()`、
  `start_maintenance(*extra_args, extra_env=…)`、`run_maintenance(*args)`。后续部署/运维票
  （F15A #54、F15B、F13A 手工复验）应复用该入口，不要复制启动逻辑。
- `deploy/dev/smoke.py`：`run(base_url, admin_login, admin_password, web_dir, examples_dir,
  internal, environment, command, require_node)` → `orchara.f15a.dev-smoke/1` 证据字典；
  `InternalAccess(url, ca, client_cert, client_key, role)` 是内部 mTLS 断言的可复用值对象。
- `tests/acceptance/conftest.py`：session fixture `dev_stack` / `smoke_evidence`；后续票若需
  "真实端到端冒烟"可直接复用（但不要把它当成 M1 门禁）。
- 页面：状态页新增 `id="environment-profile"`/`id="environment-stub"`；
  `attachments.js` 新增纯函数 `shouldPollToolArtifact(artifact, attempts, maxAttempts)`。
- **无迁移**（本票未新增/未改迁移；当前迁移链最高仍 `0032_operation_reconciliation.sql`，共
  32 个迁移，acceptance 用 `stack.migration_chain` 断言 `0001..0032` 连续）；`contracts/**`、
  `docs/spec/**` 未改动。M1 的评测执行器（F13A）、发布门禁（F02B）、记忆/知识（F08/F09）
  仍需各自门票闭环。

## 8. 合并与仓库状态

- merge：`integration/f13a-f15a-f09-f10` @ `e7c2891`（T053 合并提交）→ 本分支，merge commit
  **`458c132`**；`git merge` 自动合并，`product/PROGRESS.md` 的 T053/T057 两行与 T053 小节
  同时保留。
- merge 后复跑：`pytest tests/acceptance` **13 passed in 57.13s**；`go build/vet/test` exit 0、
  `gofmt -l` 空、`sync_spec --check` no drift。
- 提交序列：`feat(f15a)` `7129691`、`fix(f15a)` `f75ef9b`、`test(f15a)` `6e002e9`、
  `docs(f15a)` `90e200d`、merge `458c132`（本报告与证据在 merge 后的 docs 提交中）。
- 未 push、未关闭/评论 GitHub issue、未改 `docs/spec/**`/`contracts/**`/迁移。

## 9. 自检

- 证据与仓库内无口令/DSN/签名密钥/私钥内容；`smoke.run` 在写证据前对管理员口令、创作者口令、
  PostgreSQL 口令、`postgresql://`、`PGPASSWORD` 做断言。
- `.devstack/`（二进制、对象字节、dev 证书、日志）在根 `.gitignore`；证据中的
  `ORCHARA_DB_DSN`/`ORCHARA_MAINTENANCE_DSN`/`ORCHARA_OBJECT_SIGNING_KEY` 均为 `<redacted>`。
- 未新增 UI 页面/平台功能；两处页面改动均为小修并在 §1.5/§6 说明。
