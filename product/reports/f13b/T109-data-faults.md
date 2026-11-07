# T109（GitHub #110）故障注入：删除、限流、Redis 丢唤醒与权限撤销（FAULT-09/11/12/14）

状态：**partial** —— FAULT-09 / FAULT-11 / FAULT-14 **passed（真实栈）**；FAULT-12 的
PostgreSQL-notifier 与 SSE 回放半 **passed**，但本构建**没有 Redis 适配器/实例**，Redis 重启
半按规则记 **blocked**，不冒充 Redis 通过。真实厂商 429/5xx 端点仍 `not_run`（dev-stub 故障
model id 代替并逐处标注）。

- 分支 / worktree：`t/t109-data-faults` / `E:\Orchara-wt\t109`
- 基线：`integration/f13b` @ `d593c1e`；结束前依次 merge `integration/f13b` @ `f6e0394`（T106
  最终证据，合并 `4175a53`）与 @ `883ca35`（T108 子任务并发，合并 `e3829b5`，PROGRESS 双小节
  保留）；§7 的门禁 tail 全部在最终合并树 `e3829b5` 上重跑，证据 JSON 也在该树重生成。
- 环境：PostgreSQL 16.4 `127.0.0.1:55432`；Go 1.27.3；Python 3.12.2；Node v24.13.0；
  `ORCHARA_PROFILE=dev-stub`；模型 dev-stub。
- 交付：
  - `product/tests/f13b/test_t109_data_faults.py`（4 条真实栈故障用例）
  - `product/tests/support/t109.py`（证据记录器 + grants/后台任务探针；仅
    `F13B_WRITE_EVIDENCE=1` 写回）
  - `product/reports/f13b/T109-faults.json`（4 条 fault 记录；FAULT-12 含 `blocked` 子项）
  - 本文件；原始 tail 在 `E:\Orchara\.scratch\f13b\T109\`
- 未修改 `docs/spec/**`、`contracts/**`、迁移、产品 Go/Python 代码与既有票测试断言。

## 1. 结论（先读）

1. **FAULT-09 passed**：真实打开 PostgreSQL 检索缓存并命中后，经真实
   `DELETE /api/v1/knowledge-documents/{id}` 删除文档。删除后检索 `items` 不再含该文档、
   `cache.hit=false`、响应无正文；`GET /knowledge-citations/{chunk}` 立即 404 且无 text/citation；
   把已删 item 注入**当前 key** 的活缓存行后，响应 `hit=false / discarded=true /
   discard_reason=item_unreadable`，旧正文不出现；重放 ingest job 以
   `failed/source_deleted`（business）结束、reindex 404、删除源文件后再摄取 404。真实缓存行
   中仍含已删 chunk 的 stale 行 `hit_count` 恒为 0（stale hits=0）。
2. **FAULT-11 passed**：dev-stub 注册故障 model id 打出 429（`stub-rate-limited-primary`）与
   5xx（`stub-unavailable-primary`）；真实 mTLS capability 调用走真实 Run/manifest。
   429 后账本 attempts=`[failed,failed,failed,succeeded]`（恰 3 次物理重试 + 1 次兼容
   fallback，fallback 只成功一次，receipt 1 条）；半段流响应（`stub_stream_fail_after=2`，
   3 个 generation 各 2 个 delta 后 transient 失败）由 fallback generation 完成：最终文本 ==
   最后 generation 的 delta，`final != 全部 generation 拼接`（不拼接半段）；无 fallback 的 5xx
   最终 503 `provider_transient`、恰 3 次 attempt、operation failed、无 fallback。
3. **FAULT-12 partial（PG 半 passed / Redis 半 blocked）**：API 安装的
   `InProcessNotifier` 无消费者，每次 enqueue 的唤醒都被丢弃（日志有 drop 行）。在**没有
   maintenance 进程**时提交的 `artifact_finalize` job 仍是 durable `ready` 行；随后启动真实
   `cmd/maintenance` 进程从 PG 轮询 claim 并 succeeded（round 2 是"进程已退出→再次提交→重启
   maintenance"的真实重启）。SSE 断线后用 `Last-Event-ID` 重连，从 `platform.run_events`
   回放 seq 2、3（worker 的 node.status 事件没有任何 notifier 唤醒）。
   **Redis 重启 `blocked`**：本构建只有 `NopNotifier` + 同进程 `InProcessNotifier`，
   没有 Redis client/server；T022/T024 报告亦记 `not_run`。没有把 PG 结果写成 Redis 通过。
4. **FAULT-14 passed**：真实 parent Run 已领取并成功 spawn 一个 child 后，经真实
   `DELETE /api/v1/admin/grants/{id}` 撤销 child application 的 read grant（204、无 body、
   epoch bump、grants 行 0）。下一次 `spawnChild` 被 **403 `child_not_visible`** 拒绝，而 Run 的
   **冻结 snapshot 仍钉着该 child version**；对 child application 与 runnable version 的下一
   次读取均 404 `resource_not_found`。已完成的第一个 child Run 保留（不回滚），但拒绝调用
   没有新增 Run/job/operation。

## 2. 逐 AC / 票面要求

| 票面（issue #110 / EVALUATION §4） | 结论 | 证据 |
|---|---|---|
| FAULT-09：删除知识/文件后缓存命中与恢复 → 无正文泄漏，拒绝恢复已删输入 | **passed** | 真实删除事务 + 真实缓存行；citation 404 无 text；检索无已删文档；poison 当前 key → `item_unreadable`；stale 行 hit=0；replay `source_deleted`、reindex 404、重摄取已删源 404 |
| FAULT-11：供应商 429/5xx/半段响应失败 → 有限重试/兼容备用，结果不拼接 | **passed** | 3 次物理重试 + 1 次 fallback；SSE 半段后新 generation，最终文本不拼接；无 fallback 时 503 `provider_transient`（stub 故障 id，标注） |
| FAULT-12：Redis 重启/丢唤醒 → job/事实未丢，SSE 从 PG 回放 | **partial（PG passed / Redis blocked）** | 无 maintenance 时 job 仍 `ready`，启动/重启 maintenance 后 claim→succeeded；SSE `Last-Event-ID=1` 回放 seq 2、3。Redis adapter 不存在 → `blocked` |
| FAULT-14：应用/资料权限在执行中撤销 → 下一调用被拒绝，原快照不授永久权 | **passed** | 撤销后下一次 `spawnChild` 403 `child_not_visible`；application/runnable 读 404；snapshot 仍钉 child version；无新 Run/job/operation |
| 负边界：撤销后旧 token/旧缓存不能继续读；删除后不能恢复已删输入 | **passed** | 同一 live attempt token 的下一 capability 被拒；poison 缓存行被丢弃；reindex/replay/重摄取全部拒绝 |

## 3. FAULT-09：删除知识文档时的真实缓存

**注入点。** 真实 `POST /api/v1/knowledge-bases` + signed upload + `artifact_finalize` +
`document_ingest` 得到两个 ready 文档并 `POST .../retrieval-queries` 预热
`platform.knowledge_retrieval_cache`（`hit=false, stored=true`）；随后执行真实
`DELETE /api/v1/knowledge-documents/{id}`（F11 tombstone + deletion job，202）。再查询同一个
query；之后把已删文档的**真实 stored item**（含正文）注入当前 key 的活缓存行（lost-eviction
adversary）。最后删除源 artifact 文件本身并尝试重新摄取。

**输入 / 独立真值。**
- 封存源对象字节的 sha256（测试重算）与 citation span 在源文本中的 rune 切片逐字符相等；
- `platform.knowledge_documents`（`deleted_at`/`state='deleted'`/`revision+1`）、
  `platform.knowledge_chunks`（物理行仍在）、`platform.deletion_jobs`（scopes 含
  `retrieval_cache`）；
- `platform.knowledge_retrieval_cache` 原始行与 `hit_count`（stale 行仍存在但 0 hit；
  命中行只含存活文档且无已删正文）；
- `platform.background_jobs` 重放 job 的 `failed/source_deleted/business`。

**预期 vs 实际。**

| 步骤 | 预期 | 实际 |
|---|---|---|
| 删除前 citation | 200 + 正文，span 对封存源字节可复算 | 200；citation `source_sha256` == 对象 sha；rune 切片 == item.text |
| 删除后 citation | 404 `resource_not_found`，无 text/citation | 404；`has_text=false` |
| 删除后检索 | 无已删文档 item，`hit=false`，响应无正文 | items=3、deleted_document_items=0、`hit=false` |
| 注入当前 key 的 stale item | 丢弃，不得返回正文 | `hit=false / discarded=true / discard_reason=item_unreadable`，marker 不出现 |
| 恢复/重放 | 不得让已删内容回来 | reindex 404；replay job `failed/source_deleted/business`；删除源文件后重摄取 404；删除全部文档后 `items=[]`、`stored=false`、answer `no_results` |
| stale hit 计数 | 0 | stale 行 hit=0；命中行只含未删文档、无 marker |

## 4. FAULT-11：429/5xx/半段响应

**注入点。** 真实 capability `POST /internal/v1/runs/{run_id}/capabilities`（worker mTLS，真实
Run + 真实 sealed manifest）。主 profile 的 `model_id` 设为 dev-stub 文档化的
`stub-rate-limited-primary` / `stub-unavailable-primary`；半段响应通过 streaming 路由
`POST /internal/v1/runs/{run_id}/model-stream` 的 `stub_stream_fail_after`/
`stub_stream_fail_attempts` 参数。兼容 fallback 是另一个真实 `model_profile_versions` 行，
冻结在同一个 run manifest。

**独立真值。** `platform.operation_attempts`（每次物理 attempt 的 `attempt_no/state/
error_code/tokens/charged_amount/result_artifact_id`）、`platform.operations`（fallback
profile/reason、last_error_code）、公开 owner ledger、sealed receipt 对象字节。

**预期 vs 实际。**

| 场景 | 预期 | 实际 |
|---|---|---|
| 429 主模型 + fallback | 3 次 transient 后 fallback 成功一次 | attempts 4 = failed×3（provider_transient，charged=null）+ succeeded；operation fallback=`rate_limited`；ledger attempts=4；receipt 1 |
| 半段流 ×3 + fallback | 失败 generation 的 delta 不得拼进最终结果 | frames：3×(delta,delta,error)+4×delta+final；final == 最后 generation deltas；final != 全部 generation 拼接；attempts 4；receipt 文本 == final |
| 5xx 无 fallback | 有限重试后稳定错误码 | 503 `provider_transient`；attempts 恰 3，全 failed；operation failed、fallback=null |

失败 attempt 均无 `result_artifact_id`、无 charge；成功 attempt 才有 tokens/charge 与 receipt。
**stub 边界**：这是 dev-stub 的确定性故障 model id，不是真实厂商；真实厂商的 429/5xx body、
Retry-After 与请求日志 `not_run`。

## 5. FAULT-12：丢唤醒、maintenance 重启与 SSE 回放

**注入点。** `cmd/api` 安装 `background.InProcessNotifier` 且**无消费者**——这是产品自己的
notifier；每次 enqueue 的唤醒被丢弃并记录一次日志。真实 job-scoped upload/complete 在
**没有任何 maintenance 进程**时提交 `artifact_finalize`；之后才启动真实
`cmd/maintenance run --kinds artifact_finalize`（round 2 是重启后再次提交→再次启动）。
SSE 用真实 `GET /api/v1/runs/{id}/events` 连接，读取 seq 2 后关闭，再由 worker mTLS 提交
`node.status`（无任何 notify），最后带 `Last-Event-ID: 1` 重连。

**独立真值。** `platform.background_jobs`（state/attempt_count/lease_token）、
`platform.artifacts`（state/sha256）、`platform.run_events`（seq/type/payload）、API 日志中的
wakeup drop 行、maintenance 进程 pid/退出码。

**预期 vs 实际。**

| 步骤 | 预期 | 实际 |
|---|---|---|
| 无 maintenance 时 enqueue | job 为 durable `ready`，无人 claim | 0.5s 后仍 `ready`、`lease_token=null`、artifact `scanning`；API 日志有 drop 行 |
| 启动 maintenance（round 1） | 从 PG claim 并 succeeded | job succeeded、attempt_count=1、artifact `ready`（sha256 匹配） |
| 重启 maintenance（round 2） | 同样不丢 job | 同上；两次都是真实进程（pid 48268 / 66280，terminate 退出） |
| SSE 断线重连 | seq > Last-Event-ID 从 PG 回放 | 首连 `["2"]`；重连 `["2","3"]`；`run_events` = 1,2,3，seq3 类型 `node.status` |
| Redis 重启 | 本构建无法执行 | **blocked**：无 Redis adapter/server；不写成 Redis 通过 |

## 6. FAULT-14：执行中撤销 application grant

**注入点。** 真实 `POST /api/v1/admin/grants`（admin session + CSRF，201）把 provider 的 child
application read grant 授给 reader；reader 用真实 `debugDraft` 提交 parent Run（call_app 固定
child version），真实 supervisor claim 后第一次 `POST /internal/v1/runs/{id}/children` 成功；
随后真实 `DELETE /api/v1/admin/grants/{id}`（204，epoch bump），再发第二次 spawnChild 与
application/runnable 读取。

**独立真值。** `platform.resource_grants`（撤销后 0 行）、`platform.policy_epochs`、
`platform.runs`/`platform.run_children`/`platform.jobs`/`platform.operations` 计数、
`platform.execution_snapshots` 指向的封存 manifest 字节（`subapp_versions` 仍含 child version）。

**预期 vs 实际。**

| 步骤 | 预期 | 实际 |
|---|---|---|
| 撤销前 spawn | 202 创建 child Run | 202，child Run 1 个 |
| 撤销 | 204 无 body，grant 行消失，epoch bump | 204、`raw=b""`、0 行、epoch 增加 |
| 撤销后 application / runnable | 404 `resource_not_found` | 404 / 404 |
| 撤销后 spawnChild（live token 不变） | 稳定拒绝，且不新增写 | **403 `child_not_visible`**；run_children 仍 1、child runs 仍 1、runs/jobs/operations 不增 |
| 冻结 snapshot | 仍钉 child version，但不是永久授权 | manifest `subapp_versions` 仍含 child version，spawn 仍拒 |

## 7. 门禁与原始 tail

从 `product/` 运行；原始日志在 `E:\Orchara\.scratch\f13b\T109\`。下列 tail 均在最终合并树
`e3829b5`（含 T108）上取得。

```
python -m pytest tests/f13b/test_t109_data_faults.py -q
python -m pytest tests/f13b -q
python -m pytest tests/f12 -q
go build ./... ; go vet ./... ; go test ./...
gofmt -l internal cmd tools
python tools/sync_spec.py --check
```

（tail 见 §7.1–§7.6，合并后最终树复跑结果同步更新。）

### 7.1 T109 故障用例

```text
....                                                                     [100%]
4 passed in 55.35s
```

### 7.2 tests/f13b

```text
........................................................................ [ 37%]
........................................................................ [ 74%]
..................................................                       [100%]
194 passed in 278.25s (0:04:38)
```

### 7.3 tests/f12

```text
........................................................................ [ 16%]
........................................................................ [ 32%]
........................................................................ [ 48%]
........................................................................ [ 64%]
........................................................................ [ 80%]
........................................................................ [ 96%]
...............                                                          [100%]
447 passed in 277.62s (0:04:37)
```

### 7.4 Go build / vet / test

```text
go build ./...   exit 0
go vet ./...     exit 0
go test ./...    exit 0   (39 ok / 0 FAIL / 12 no test files)
...
ok  	orchara/platform/internal/tools	4.744s
ok  	orchara/platform/internal/toolversions	1.446s
ok  	orchara/platform/internal/uuid	0.926s
ok  	orchara/platform/internal/workflow	1.488s
?   	orchara/platform/probes/f10_pdf_compare/go_baseline	[no test files]
?   	orchara/platform/probes/f15a_objectstore/localfs_probe	[no test files]
```

### 7.5 gofmt

```text
gofmt -l internal cmd tools   (no output; exit 0)
```

### 7.6 sync_spec

```text
no drift: spec copy and contract snapshot match the manifest
```

## 8. 证据文件

`product/reports/f13b/T109-faults.json` 由 `F13B_WRITE_EVIDENCE=1` 生成；含
`environment`、4 条 fault（`id/title/verdict/injection_point/inputs/independent_truth/
expected/observed/commands/not_run/blocked`）、`blocked` 列表（Redis）与 `not_covered`。
普通门禁运行不会改写该文件。

## 9. 边界 / blocked / not_run

- **blocked（FAULT-12 的 Redis 半）**：本构建无 Redis client/server，也无 Redis 唤醒适配器
  （`internal/background/notifier.go` 只有 `NopNotifier` 与同进程 `InProcessNotifier`；
  T022/T024 报告记 Redis `not_run`）。解除条件：后续构建提供 Redis 实例 + 订阅路径。
- **not_run（真实供应商）**：真实厂商 429/5xx/半段 body、Retry-After 与厂商请求日志；本机只有
  dev-stub，无凭据/端点。FAULT-11 使用 stub 故障 model id 并已标注。
- **not_run（真实 Redis 重启）**：同 blocked 原因；没有把 PG 轮询结果写成 Redis 通过。
- **not_run（SSE UI / 浏览器 DOM）**：无 Playwright/Chromium；SSE 回放由真实 HTTP 流验证，
  页面渲染不在本票范围。
- **边界（grant 词汇）**：本构建 F01 grant 只有 `application` + `read`；不存在
  document-level grant。文档访问撤销由 FAULT-09 的真实删除覆盖，未虚构 document grant。
- **边界（child version 发布）**：FAULT-14 的 child version 用 T075 publisher-shape fixture
  （真实 validate/manifest 字节/version 行/依赖边；publication review 事务未跑），与既有
  F07 child-run 套件一致并已在证据中标注。
- **边界（清理范围）**：FAULT-09 验证逻辑不可读（tombstone 立即生效）；物理清理 scope 属
  T071/T072，本票不声称已清理。
