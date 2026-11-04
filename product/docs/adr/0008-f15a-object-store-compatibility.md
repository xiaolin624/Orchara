# ADR 0008：F15A 对象存储兼容探针的证明边界与候选采用规则

- 状态：已接受
- 日期：2026-11-04
- 相关：F15A / T053（GitHub #54，规划 ID F15A.02）；分支 `t/t053-s3-probe`；
  交付 `product/probes/f15a_objectstore/`、`product/tests/f15a/`；
  报告 `product/reports/f15a/T053-s3-probe.md`；规范依据
  `ARCHITECTURE.md:45`（S3 兼容接口、部署者提供实现、P0 确认许可证/签名 URL/删除行为）、
  `DEVELOPMENT-PLAN.md` C07、`OPERATIONS.md`、`SECURITY.md:57`；
  后续：F15B 部署/恢复演练、对象存储后端接入平台（把运行时切换到候选 S3）。

## 背景

平台契约把对象存储定义为 **S3 兼容接口**，由部署者提供实现；产品自身只有本地文件系统
`internal/objectstore`（staging/sealed、HMAC 签名链接、20 MiB 上限），文档明确 S3/MinIO 不在
该包范围内。F15A 的票面要求是“在候选 S3 上验证封存、匿名拒绝和删除”，但本机没有 Docker、
MinIO/SeaweedFS、S3 端点或 Redis（`reports/f15a/environment-profile.json` 记录实测命令）；
`runsc` 虽存在于 WSL Ubuntu-24.04，但 WSL 不是被接受的隔离宿主（ADR 0002），且与本票无关。

因此本票能真实交付的是**部署验收探针 + 判别证明**：探针必须能拒绝不兼容实现，必须把
“没有候选端点”报成 `blocked`/exit 2，而不是伪造成通过。候选 S3 的验收状态是 `blocked`，
直到部署方提供端点、凭据与 IAM 策略。

## 决策

### 1. 探针是部署工具，不是平台运行时特性

- 位置 `product/probes/f15a_objectstore/`，Python 3.12 标准库实现（`urllib`/`http.client`/
  `hmac`/`hashlib`），无第三方依赖、无新锁文件；不新增 HTTP 端点、不建表、不改 UI。
- CLI 入口 `object_store_probe.py`，退出码固定：
  - `0` 全部断言 `passed`（或候选不提供版本历史时 `not_run`）；
  - `1` 观察到真实不兼容（`failed`）；
  - `2` 配置/环境失败（含端点缺失/不可达、TLS 失败、凭据不可用）。
- `blocked`/`failed`/`not_run` 与 `passed` 严格分开；没有候选时 `assertions: []` 且
  `blocked_reason` 记录缺失字段或不可达原因。**禁止**把“无断言可跑”写成通过。

### 2. 两凭据模型：runtime 与 provisioning

平台实际有两个角色：运行时（staging 上传、sealed 读取）与服务器/维护（写、删 sealed，
孤儿清理）。探测若只用一个凭据，就无法验证“sealed 只写”边界：

- `F15A_S3_ACCESS_KEY`/`_SECRET_KEY`：runtime；断言其不能 PUT/DELETE `sealed/`、不能越出
  `<prefix>/`。
- `F15A_S3_PROVISION_ACCESS_KEY`/`_SECRET_KEY`：provisioning；用于播种 sealed 对象、验证
  “未创建”“删除后 404”“兄弟键不受影响”。
- 缺 provisioning 时，依赖它的断言是 `blocked`；匿名写/列表等仍可运行。凭据只从环境读，
  不进 argv、不进证据。

候选部署必须显式配置：runtime 只能访问 `tenant/<id>/staging/*` 与 `tenant/<id>/sealed/*`
的读/写子集；sealed 写入与删除只属于服务器身份；匿名身份全部拒绝。探针只能验证这两份
凭据，不能证明其它角色/桶策略。

### 3. 断言集（10）与判定口径

1. `anonymous_read_denied`、2. `anonymous_put_denied`、3. `anonymous_list_denied`：
   仅 401/403 视为拒绝；2xx 一律 `failed`，并检查响应体不是对象字节、不是桶列表。
4. `out_of_range_key_denied`：越出前缀、兄弟租户、绝对键必须 401/403 且对象确实不存在；
   `..` 遍历的判定是**不得逃逸**（可能 4xx，也可能是字面键 2xx 但 sealed 目标不存在）；
   改动路径的签名 URL 必须被拒。理由：S3 对象键是字面量，`..` 本身不产生遍历语义，真正的
   风险是前置代理在鉴权后归一化路径；把“字面键 2xx”判成不兼容会错误拒绝合格实现。
5. `sealed_put_denied`：runtime PUT sealed 必须 401/403，且 provisioning 读不到该对象。
6. `staging_overwrite_preserves_sealed`：staging 与 sealed 使用**同名对象**做碰撞测试；两次
   staging 覆盖后 sealed 的 SHA-256 必须不变，staging 必须是最后一次字节。
7. `short_lived_signature_expiry`：签名 URL 在 TTL 内 200 且字节正确，过期后 401/403 且无
   对象字节；`--expiry-skew` 只用于记录候选的时钟宽限，默认 0（过期即拒绝是安全属性）。
8. `proxy_download_boundary`：HEAD 的 Content-Length 必须精确；>20 MiB 时探针的代理判定必须
   拒绝整段下载（探针不会真的流式读超大对象）；Range 返回 206 + 精确 `Content-Range`，
   不可满足 Range 返回 416。另记录“泄漏的未过期 URL 在对象删除/重写后是否仍可读”——
   S3 无撤销 API；若仍然可读则 `requires_proxy_download=true`，平台必须走代理下载才能
   立即撤销。
9. `delete_semantics`：runtime 不能删 sealed；provisioning 删除后 GET 404、重复删除幂等、
   同名兄弟键字节不变。
10. `versions_immutable`：**观察性**断言。候选不提供版本历史（404/501/未启用）时 `not_run`，
    不因此拒绝候选；启用但旧版本不可寻址才算 `failed`。备份/恢复、跨区复制、SSE 不在探针
    范围。

### 4. 判别证明是 stub，不是候选验收

- `fixtures/compliant_server.py` 与 `fixtures/violating_server.py` 是本地真实 HTTP 进程，实现
  上述 S3 子集，并**独立**实现 SigV4 校验（探针签名正确性另由 AWS 官方向量锁定）。
- `tests/f15a/` 的真实子进程证据：合规夹具 9 passed + 1 not_run、exit 0；违规夹具逐个规则
  只有对应断言失败、其余通过、exit 1；全部规则同时破坏时 9 failed、exit 1；一次 503 注入
  被真实重试并记录。
- 这些夹具证明探针能拒绝不兼容实现，**不能**证明任何真实 S3 兼容。MinIO/moto 之类同 API
  stub 同理，不得记作候选验收。

### 5. 本地文件系统类比（真实，但不是 S3 证据）

`localfs_probe/main.go` 直接调用产品 `internal/objectstore`（而非复制逻辑），真实目录、真实
字节，证明：键类型/遍历拒绝、staging 覆盖不影响 sealed、sealed 写边界、HMAC 链接 TTL/操作/
字节上限绑定、幂等删除、20 MiB 上限无残留。它明确标注 `not_s3_evidence: true`，且
`anonymous_network_boundary`/`object_versioning` 为 `not_run`。它不能证明 IAM/匿名网络层/
桶策略/版本化/备份/TLS。

### 6. 候选采用规则

**没有针对候选的绿色探针运行 → 不得选用。** 绿色运行是必要条件，不是充分条件：许可证、
备份/恢复演练、容量与加密仍是人工/后续票（F15B）门禁。报告与证据保存端点主机（不含凭据）、
桶、区域、探针版本、输入 SHA-256、真实命令/退出码与环境 profile；stub 与 real 分列。

### 7. 路径与命名偏离

集成简报写 `product/probes/s3_compat/`，集成笔记 §5.4（orchestrator 指令）建议
`product/probes/f15a_objectstore/`。本票采用后者，并在报告 §4 记录。断言 id 采用
`anonymous_read_denied`（票面用词）。未改 `contracts/**`、`docs/spec/**`，无迁移。

### 8. TLS 与寻址

候选 HTTPS 使用系统信任或 `F15A_S3_CA_FILE` 指定的私有 CA；**不提供**跳过证书校验开关。
只支持 path-style（`/bucket/key`）；virtual-hosted-only 端点会连接失败并按环境失败处理。

## 影响

- F15B/部署方：用同一 CLI 对候选跑出绿色证据后才能进入部署；IAM 策略至少包含 §2 的两角色。
- 平台接入 S3 后端（未来票）：`proxy_download_boundary` 与 `pre_expiry_url_revocation` 的结论
  要求下载路径是平台代理，而不是直接把 presigned URL 交给浏览器；20 MiB 上限继续由产品
  侧执行，不能依赖 S3 的 5 GiB 限制。
- 版本化/对象锁/WORM：探针只观察，不把“未启用版本化”当作不兼容；若产品未来要求 WORM，
  应新增断言而不是复用本票。
