# F15A T053 验收：在候选 S3 上验证封存、匿名拒绝和删除

- 任务：F15A / GitHub Issue #54（票 `T053`，规划 ID F15A.02），分支
  `t/t053-s3-probe`（worktree `E:\orchara-worktrees\t053`）；父门禁 #53/#54。
- 状态：**`blocked`（候选 S3 验收）**。探针交付与判别证明**真实执行并通过**；
  本机**没有候选 S3 端点**，因此“候选实现被接受/拒绝”的最终判定无法发生，按票面记
  `blocked`，不伪造候选通过。
- 边界（如实）：合规/违规夹具是**判别证明（stub）**，不是候选验收；本地文件系统类比证明的是
  平台 `internal/objectstore` 的边界，**不是 S3 证据**。没有把任何 fixture/MinIO 风格的
  stub 运行记作真实 S3 通过。
- 环境：Windows 11，Python 3.12.2，Go 1.27.3，Node 24.13.0，pytest 9.0.3；无 Docker/
  Compose/MinIO/AWS CLI/Redis，9000/9001/6379 关闭；WSL 有 `runsc release-20241118.0` 但
  WSL 不是被接受的隔离宿主（ADR 0002），本票不使用它。机器可读事实见
  [`environment-profile.json`](environment-profile.json)。

## 0. 工作记录（简短）

1. 读 T053 票面、集成简报/笔记 §5.4、`internal/objectstore/{objectstore,sign}.go`、
   `ARCHITECTURE.md:45`、`DEVELOPMENT-PLAN.md` C07、`SECURITY.md`/`OPERATIONS.md` 的对象存储
   条目、D00 环境报告与 T039 报告风格；确认本机无候选 S3、只能交付探针 + 判别证明 + 本地 FS 类比。
2. TDD 垂直切片（每片先红后绿）：
   - SigV4 原语 → AWS 官方向量（get-vanilla、query-order、post-vanilla、form-urlencoded、
     S3 API 参考 presigned GET）8 用例，先用外部 oracle 钉死签名；
   - 配置/环境失败路径 → 缺配置/端点不可达/非法 scheme 必须 exit 2 + `blocked`，凭据只进环境；
   - 探针 10 断言 + 真实 HTTP fixture（合规/违规）→ 合规 9 passed + versioning not_run；
   - 逐断言违规矩阵 → 每次只破坏一个规则，仅对应断言失败；全部破坏 → 9 failed；503 注入被真实重试；
   - 边界常量钉住（Go `MaxObjectBytes` = 契约 `size_bytes.maximum` = 探针常量）；
   - 本地 FS Go 类比 → 真实目录/真实字节，6 passed + 2 not_run；
   - 证据文件诚实性测试（候选 `blocked`、夹具矩阵、无凭据字面量）。
3. 生成真实证据（`reports/f15a/`，含命令、退出码、报告 SHA-256），跑门禁，写本报告/
   PROGRESS/ADR；按 `feat(f15a)/test(f15a)/docs(f15a)` 提交；未 push、未关 issue、未改
   `docs/spec/**`/`contracts/**`、无迁移。

## 1. 交付内容

### 1.1 探针 CLI（`product/probes/f15a_objectstore/`）

- Python 3.12 **标准库 only**，无新依赖/锁文件；path-style `/bucket/key`；SigV4 header auth +
  query auth（presigned GET）；`UNSIGNED-PAYLOAD`；不把 `..` 归一化，让服务端/前置代理的行为
  可被观测。
- 配置：非敏感项可用 flag 或环境，凭据**只从环境**读（进程命令行对本机所有用户可见）：
  `F15A_S3_ENDPOINT/REGION/BUCKET`、`F15A_PREFIX`、`F15A_S3_ACCESS_KEY/SECRET_KEY/
  SESSION_TOKEN`、`F15A_S3_PROVISION_ACCESS_KEY/SECRET_KEY/SESSION_TOKEN`、
  `F15A_S3_TIMEOUT/EXPIRY_SKEW/RETRIES/CA_FILE`。`--out`/`--emit`、`--short-ttl`、`--timeout`、
  `--expiry-skew`、`--retries`、`--ca-file`、`--profile`；**没有** secret/access-key flag。
- 退出码：`0` 全通过（或候选无版本化时 `not_run`）；`1` 真实不兼容；`2` 配置/环境失败
  （含“无候选端点”）。
- 证据 JSON 只含端点主机（无凭据）、桶、区域、前缀、输入 SHA-256/字节数、HTTP 状态、
  探针生成的 key 名、检查明细、清理结果、版本/环境；**从不写凭据值或对象正文**。

### 1.2 十条断言

| id | 判定（摘要） |
|---|---|
| `anonymous_read_denied` | 未签名 GET sealed → 401/403；响应体不是对象字节、不是列表 |
| `anonymous_put_denied` | 未签名 PUT 拒绝且 runtime 读回 404 |
| `anonymous_list_denied` | 未签名 `list-type=2` 拒绝且无 `<ListBucketResult>` |
| `out_of_range_key_denied` | 前缀外/兄弟租户/绝对键 401/403 且对象未创建；`..` 请求**不得逃逸**到 sealed；改路径的签名 URL 被拒 |
| `sealed_put_denied` | runtime PUT sealed 401/403，provisioning 读回 404 |
| `staging_overwrite_preserves_sealed` | staging 与 sealed 同名；两次覆盖后 sealed SHA-256 不变 |
| `short_lived_signature_expiry` | TTL 内 200 且字节正确；过期后 401/403 且无对象字节 |
| `proxy_download_boundary` | HEAD Content-Length 精确；>20 MiB 代理判定拒绝；Range 206+精确 `Content-Range`；不可满足 Range 416；记录未过期 URL 删除/重写后是否仍可读（`requires_proxy_download`） |
| `delete_semantics` | runtime 不能删 sealed；删除后 404、重复删除幂等、兄弟键字节不变 |
| `versions_immutable` | 无版本化/未启用 → `not_run`（不因此拒绝候选）；启用但旧版本不可寻址 → failed |

判定口径（关键取舍）：`anonymous_*` 只接受 401/403；`..` 的失败条件是“逃逸到了 sealed 键”，
不是“字面键返回 2xx”（S3 对象键是字面量，错误地要求 4xx 会把合格实现判死）。详见 ADR 0008。

### 1.3 判别证明（真实 HTTP 夹具，`fixtures/`）

- `compliant_server.py`：实现对象 GET/HEAD/PUT/DELETE、列表、versioning 查询、SigV4 header/
  query 校验、Range/206/416、runtime/provisioning 两类策略。**独立**实现 SigV4 校验（探针签名
  正确性另由 AWS 向量锁定，避免自己验自己）。
- `violating_server.py`：`--violate <assertion-id>` 逐条破坏规则；`--violate all` 全破坏；
  `--transient-failures N` 注入 503。违规是**真实的服务器行为**（真实 HTTP、真实签名校验）。
- `tests/f15a/test_discrimination.py`：合规 → exit 0；全部违规 → exit 1、9 failed；
  **逐条矩阵**：破坏一条规则时只有对应断言 failed、其余 passed；503 注入 → 探针重试后 exit 0
  且 `client_stats.retries >= 1`；缺 provisioning → 仅匿名断言可跑，sealed 断言 blocked。

### 1.4 本地文件系统类比（`localfs_probe/main.go`）

直接 import `orchara/platform/internal/objectstore`（不是复制逻辑），真实临时目录、真实字节：
键类型/遍历拒绝、staging 覆盖不影响 sealed、`PutStaging`/`PutSealed` 跨命名空间拒绝、
HMAC 链接 TTL/操作/上限绑定/未配置 fail closed、幂等删除与兄弟键安全、20 MiB 上限无残留。
报告 `not_s3_evidence: true`；`anonymous_network_boundary` 与 `object_versioning` 明确
`not_run`（本地 FS 无 HTTP/IAM/版本化）。

### 1.5 真实证据（`reports/f15a/`）

`runs.json` 记录了 15 次运行的真实命令、退出码、报告 SHA-256 与逐断言 verdict：

| 运行 | 退出码 | 状态 | 说明 |
|---|---|---|---|
| `candidate-absent` | 2 | blocked | 真实候选环境：`http://127.0.0.1:9000` 不可达（`endpoint_unreachable`），assertions 为空，重试 2 次后失败关闭 |
| `candidate-not-configured` | 2 | blocked | 无 `F15A_S3_*`：`config_missing`，列出 endpoint/bucket/access_key/secret_key |
| `fixture-compliant` | 0 | passed | 9 passed + 1 not_run；cleanup 27/27 成功 |
| `fixture-violating-all` | 1 | failed | 9 failed + 1 not_run |
| `fixture-violation-<id>` ×9 | 1 | failed | 每条仅对应断言 failed |
| `fixture-transient-retry` | 0 | passed | 首个 provisioning PUT 收到 503 后真实重试 |
| `local-fs-analogue` | 0 | passed | 6 passed + 2 not_run，`not_s3_evidence: true` |

输入哈希示例（完整表在 `fixture-compliant.json` 的 `input_hashes`）：
`oversized` 20972544 B `75c3ef0c6c1ee6ca…`；`sealed-stable` 128 B `4d555bfd541738e5…`；
`staging-second` 144 B `21182b65596222f0…`。探针版本 1.0.0；夹具版本 `f15a-fixture/1.0`。

## 2. AC 核对

| AC | 结果 | 证据 |
|---|---|---|
| 匿名读和越界 key 拒绝 | **候选 blocked**；探针能力 passed（夹具真实执行） | `anonymous_read_denied`/`anonymous_put_denied`/`anonymous_list_denied`/`out_of_range_key_denied`：合规夹具 4 passed，逐条违规夹具各自 failed；候选端点缺失 → `candidate-absent.json` blocked |
| staging 覆盖不影响 sealed | **候选 blocked**；探针 + 本地 FS passed | `staging_overwrite_preserves_sealed` 夹具 passed；`local-fs-analogue.json` 的 `staging_sealed_separation` passed（真实目录/字节） |
| 短签名与代理下载边界可复现 | **候选 blocked**；探针 + 本地 FS passed | `short_lived_signature_expiry`（TTL 内/后）、`proxy_download_boundary`（HEAD/Range/416/未过期 URL 观察）；`signed_link_boundaries` 本地 FS TTL/操作/上限绑定 passed |
| 通过正式入口实际演示；失败/授权/重试边界有断言，不以单层 mock 成功代替 | **passed（探针层面）** | 真正 CLI 子进程 + 真正 HTTP 服务器 + 真正 SigV4；授权拒绝（匿名/前缀/sealed）、失败（exit 1）、环境失败（exit 2）、503 重试都有断言；夹具仅用于判别，不作为候选证据 |
| 记录版本/输入 hash/命令/原始结果/profile；stub/real 分报，缺条件 blocked，未运行 not_run | **passed** | 本报告 §1/§3；`runs.json`、`environment-profile.json`、15 份 JSON 证据；`candidate-absent` blocked；`versions_immutable`/本地 FS 的匿名与版本化 not_run；stub（fixtures）与 real（候选缺席）分列 |

## 3. 命令与原始结果（tails）

环境：`E:\orchara-worktrees\t053\product`；Go 1.27.3（`E:\orchara-toolchains\go\bin`，
`GOCACHE`/`GOMODCACHE` 指到 `E:\orchara-toolchains`）；Python 3.12.2；pytest 9.0.3。

```text
$ python -m pytest tests/f15a -q
....................................                                     [100%]
36 passed in 77.08s (0:01:17)

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 6.17s

$ go build ./...
(exit 0, no output)

$ go vet ./...
(exit 0, no output)

$ go test ./...
29 packages ok, 0 FAIL, 11 no test files
（含 ? orchara/platform/probes/f15a_objectstore/localfs_probe [no test files]）

$ gofmt -l .
(no output)

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

真实证据命令（`runs.json` 记录完整 argv；此处为摘要）：

```text
$ python probes/f15a_objectstore/object_store_probe.py --out reports/f15a/candidate-absent.json \
    --short-ttl 5 --timeout 10 --retries 2 --profile candidate
status: blocked (exit 2)
blocked: endpoint_unreachable: cannot reach http://127.0.0.1:9000: ConnectionRefusedError: [WinError 10061] ...
assertions: 0 passed, 0 failed, 0 blocked, 0 not_run

$ python probes/f15a_objectstore/object_store_probe.py --out reports/f15a/fixture-compliant.json \
    --short-ttl 5 --timeout 10 --retries 2 --profile fixture
status: passed (exit 0)
assertions: 9 passed, 0 failed, 0 blocked, 1 not_run
  [not_run] versions_immutable: bucket versioning is not enabled; ...

$ python probes/f15a_objectstore/object_store_probe.py --out reports/f15a/fixture-violating-all.json \
    --short-ttl 5 --timeout 10 --retries 2 --profile fixture
status: failed (exit 1)
assertions: 0 passed, 9 failed, 0 blocked, 1 not_run
```

`pytest tests/f15a` 的 36 个用例分布：SigV4 官方向量 8、配置/环境失败 4、判别矩阵 13
（合规 1 + 全违规 1 + 逐条 9 + 503 重试 1 + 缺 provisioning 1）、边界常量 4、本地 FS 2、
证据诚实性 5。

## 4. 偏离与决定

1. **路径**：集成简报写 `product/probes/s3_compat/`，集成笔记 §5.4（orchestrator 指令）建议
   `product/probes/f15a_objectstore/`；采用后者并记录于此。断言 id 采用票面
   `anonymous_read_denied`。
2. **两凭据**：runtime 与 provisioning 分离；缺 provisioning 时相关断言 `blocked`（exit 2），
   不把部署方 IAM 配置错误判成候选不兼容。ADR 0008 §2。
3. **`..` 语义**：失败条件是“逃逸到 sealed 目标”，不是“必须 4xx”。S3 对象键是字面量；把
   literal 2xx 判死会错误拒绝合格实现。前置代理归一化路径才是真实风险。ADR 0008 §3。
4. **`proxy_download_boundary` 的“边界”**：探针镜像产品的下载判定（20 MiB 上限），并用
   `test_boundary_constants.py` 把 `internal/objectstore.MaxObjectBytes` 与契约
   `size_bytes.maximum=20971520` 钉到同一数值；探针从不真正流式读取超大对象。产品 S3 后端
   代理下载本身不是本票交付（F15B/后续接入票），本票只证明候选可复现该边界。
5. **未过期 URL 的撤销**：S3 没有撤销 API；探针做了“删除并重写同名键后同一 URL 仍可读”的
   观察（合规夹具 `pre_expiry_url_still_authorizes_key=true`、`requires_proxy_download=true`），
   作为平台必须走代理下载的理由，**没有**声称 URL 可撤销。
6. **版本化**：只观察不强制；候选不支持时 `not_run`，不因此拒绝。
7. **MinIO/moto 类 stub**：未使用，也不得作为候选验收；夹具明确标注为判别证明。
8. **无迁移/无契约改动/无平台 UI/无新端点**；唯一新增 Go 代码是只读消费
   `internal/objectstore` 的探针 main 包。

## 5. `not_run` / `blocked`（如实）

- **blocked**：候选 S3 上的真实运行（无端点、无凭据、无 IAM 策略）——这是本票验收的阻塞项，
  需要部署方提供候选端点与两套凭据后重跑本 CLI。
- **blocked（取决于凭据）**：缺 `F15A_S3_PROVISION_*` 时 sealed 相关断言（匿名读、越界、sealed
  PUT、staging 覆盖、TTL、代理边界、删除）为 blocked；匿名写/列表仍可运行。
- **not_run**：`versions_immutable`（夹具未启用版本化）；本地 FS 的
  `anonymous_network_boundary`、`object_versioning`。
- **not_run（非本票）**：备份/恢复演练（F15B）、跨区复制、SSE/KMS、对象锁/WORM、真实厂商
  IAM/桶策略审计、把平台的运行时对象存储切换到 S3（后续接入票）。
- 未执行：`pytest tests` 全量（brief 明令不跑）、push、关闭 issue（orchestrator）。

## 6. 给 F15B / 后续票的接口

- 候选验收入口：
  `python probes/f15a_objectstore/object_store_probe.py --out <json>`，退出码 0/1/2；证据 JSON 的
  `status`/`assertions[].status` 是机器可读判定，`blocked_reason.code` 区分
  `config_missing`/`config_invalid`/`endpoint_unreachable`/`endpoint_tls_error`/
  `provisioning_setup_failed`。
- 部署必须提供两个身份：runtime（staging 读写删、sealed 读）与 provisioning（sealed 写删）；
  前缀 `tenant/<id>/{staging,sealed}/`；匿名全拒绝。IAM 策略不属于本票。
- 平台接入 S3 时：下载路径必须代理（`pre_expiry_url_revocation` 证据）、20 MiB 上限由产品
  强制、sealed 写入只能来自服务器身份；`versions_immutable` 仅在候选启用了版本化时才有断言。
- 本地 FS 回归：`go run ./probes/f15a_objectstore/localfs_probe -root <tmp> -out <json>` 可作为
  对象存储边界回归入口（`not_s3_evidence: true`）。
- ADR：[`docs/adr/0008-f15a-object-store-compatibility.md`](../../docs/adr/0008-f15a-object-store-compatibility.md)。

## 7. 确定性修复（2026-11-04，集成/后续门禁暴露的两处探针自身缺陷）

两次缺陷都不是候选行为，而是探针在负载或概率下的自伤；按“任何加载的机器都能输掉”的标准修复。

### 7.1 签名过期窗口竞态（`fix(f15a): T053 make signature-expiry assertions load-tolerant`）

- 症状：集成门禁连续 3 次中 2 次失败于 `short_lived_signature_expiry.accepted_before_expiry`
  （403）与 Go 本地 FS 的 1 s TTL 立即验签；SigV4 `X-Amz-Date` 是整秒截断，1 s TTL 留下 0–1 s
  窗口。
- 修复：
  - 探针 `--short-ttl` 下限/默认 **5 s**；过期断言从 URL 自有查询解析
    `X-Amz-Date + X-Amz-Expires` 得到绝对 `expires_at`，先做“窗口内必须被接受”的检查并记录
    `seconds_before_expiry_when_accepted`（本次证据 4.415 s），再
    `sleep(max(0, expires_at-now) + skew + 1.5s)` 做过期**负向控制**；若主机在窗口内连一次 GET
    都来不及完成，记 `blocked`（探针宿主调度失败），不误判候选 `failed`。
  - `localfs_probe` 的 HMAC TTL 改 5 s + 1 s margin，记录 `verified_after_ms`。
  - 证据用 5 s TTL 重新生成：15 次运行，合规夹具仍 9 passed + 1 not_run，
    `fixture-violation-short_lived_signature_expiry` 仍 failed（判别力未削弱）。

### 7.2 随机签名 key 被 TrimSpace 截短（同提交）

- 症状：本地 FS 探针用 32 字节**原始随机** key；`NewSigner` 会 `strings.TrimSpace`，key 首/尾
  恰为空白字节（约 4–6% 概率）时有效长度 < `MinSigningKeyBytes=32`，探针以 1 退出——这是探针
  输入缺陷，不是存储边界失败。
- 修复：key 改为 32 字节随机数的 **hex** 编码（64 字符，trim 为 no-op）；并在
  `signed_link_boundaries` 内加入确定性的 trim 回归检查：
  `generated_signing_key_is_trim_stable`、`whitespace_padded_key_is_accepted_after_trim`
  （前后加空白仍按 64 字符配置成功）、`whitespace_trimmed_below_minimum_refused`
  （32 字节含 1 空格 → trim 后 31 字节，干净拒绝而非崩溃/伪通过）。`tests/f15a` 对三个检查
  逐一断言，证据记录 `signing_key_encoding="hex"`、`signing_key_bytes=64`。

### 7.3 重复门禁（正常机器，同一 worktree，连续运行）

| 批次 | run | 结果 |
|---|---|---|
| TTL 修复后（fix 2 前） | 1 | `36 passed in 134.48s`，exit 0 |
| | 2 | `36 passed in 128.05s`，exit 0 |
| | 3 | `36 passed in 126.95s`，exit 0 |
| 两处修复后（最终） | 1 | `36 passed in 123.68s`，exit 0 |
| | 2 | `36 passed in 125.45s`，exit 0 |
| | 3 | `36 passed in 130.01s`，exit 0 |
| 合并 integration 后复跑（head `4bc875e`） | 1 | `36 passed in 124.84s`，exit 0；`tests/tooling` 21 passed；`go test ./...` 30 ok/0 FAIL/11 no-test |

两次修复后连续 3 次全绿，是本次验收证据。**人为加载不作为验收证据**：一次无界 CPU
burn-in 在发现后立即终止并未采用（它只会污染共享机器，不能证明探针内在的确定性）；本报告
的结论只来自正常机器连续运行与 5 s/负向控制的确定性设计。

### 7.4 回归覆盖

`tests/f15a` 现在 36 用例仍全绿；新增/加强的确定性断言在
`test_local_fs_analogue.py`（trim 三项 + hex 证据）与 `short_lived_signature_expiry` 的
`expires_at`/margin 证据；`run_evidence.py` 与 `tests/f15a/helpers.py` 统一使用 `--short-ttl 5`。
门禁命令与最新 tail：`pytest tests/f15a` 36 passed、`pytest tests/tooling` 21 passed、
`go build`/`go vet` exit 0、`go test ./...` 30 ok/0 FAIL/11 no-test、`gofmt -l .` 空、
`sync_spec --check` no drift。
