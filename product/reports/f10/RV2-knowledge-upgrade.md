# F10 复核修复 RV2：升级后的摄取死局与共享可读谓词

- 任务：外部复核第二轮（范围 `7d0f442...8ad3bcc`，见
  `.scratch/integration-scope/review2-fixes.md` 的 C/E 两项）；分支
  `fix/rv2-knowledge-upgrade`（worktree `E:\orchara-worktrees\rv2`），基线 integration tip
  **`8ad3bcc`**；两个 P2 复核项都属于 F10：C 命中 #62/T061（"失败可重新摄取"）的升级路径，
  E 命中 #68/T067 的缓存命中复检。**不是新票**，不关闭/不改写 issue，不 push。
- 状态：**两项均已修复**；真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP/mTLS +
  真实封存对象字节 + 真实 `cmd/maintenance` 双池 + Node（页面未改）；无 mock、无跳过。
- 边界（如实）：租约耗尽（`recoverExpiredTx` 以 `lease_expired` 终结 job、本就没有文档写入者）
  留下的 `scanning` 文档仍由**下一次摄取**自愈，sweep 本身不写文档状态（不在本轮复核范围，
  见 ADR 0018 §6 "边界"）；历史索引/失败暂存 chunk 的物理清理仍属 F07；真实 embedding
  provider 继承 T055/T067 blocked；全量 `pytest tests`（≈67 分钟）按 BRIEF §3 不运行。
- 契约：`contracts/**`、`docs/spec/**` 未改；`sync_spec --check` no drift。

## 0. 工作记录（简短）

1. 读 `BRIEF.md`、复核清单、ADR 0015/0018、`db/migrations/0039`、
   `internal/background/{document_ingest,complete,sweep,job}.go`、
   `internal/knowledge/{service,index_versions,cache_store}.go`、`internal/api/knowledge.go`、
   `tests/f10/test_t061_{migration,index_versions,races}.py`、`tests/support/{t055,t061}.py`；
   用 `git show t/t055-knowledge-ingest:` 核对 0039 之前的真实行为（T055 守卫没有索引绑定，
   `documentDedupKey` = `knowledge-doc:<artifact>:<artifact revision>:<document>:<revision>`），
   使复现夹具与升级前部署写下的行完全同形。
2. TDD（先红后绿）：先提交 `tests/f10/test_t061_upgrade_recovery.py`（5 用例，4 红：
   迁移未存在、legacy job 永不被结束、无 live job 无法自愈、`index_build_in_progress`
   对死构建也 409；1 绿是必须保留的 live 幂等语义），再分两个实现切片转绿。
3. Finding E 先写"三处谓词逐条比对"清单，再抽 `readable_chunk.go` 并替换三处调用点；
   仅做纯重构（不新增/删除任何谓词），以 `tests/f10` 全量两轮背靠背证明行为不变。

## 1. Finding C（P2，spec）：升级后的部署把 pre-0039 摄取 job 永久卡住

**缺陷**：0039 只给"已经 ready"的文档回填了索引版本，没有处理升级瞬间处于 `ready`/`leased`
的 `document_ingest` job。这些 job 的 `expected_index_version_id` 为 NULL，新守卫以
`stale_revision` 失败它**却不改文档**，文档永远停在 `scanning`/`indexing`；同一 artifact
重新摄取走 T055 幂等分支返回同一卡住文档，同源重建又被 `ErrIndexBuildInProgress`
（`ReindexDocument` 只看 state）拒绝——文档再也无法摄取。

**决策（ADR 0018 §6 记录取舍）**：迁移把这类尝试**失败掉**，而不是给它们新建索引版本再绑定。
绑定不确定：同一文档可能有多条历史重试 job，`expected_revision` 可能已过期，两条 job 绑到
同一个 building 索引版本会把两套暂存 chunk 写进同一版本。失败掉恰好落在既有"失败可重试"语义。

| 项 | 内容 |
|---|---|
| 迁移 | 新增 `db/migrations/0041_ingest_upgrade_recovery.sql`（0040 之后下一个空位；**只修数据，无新 schema 对象**）：(1) 无索引绑定的非终态 `document_ingest` job → `failed/ingest_index_unbound`（`failure_class=business`、清 `lease_token/lease_deadline/worker_id`、写 `last_error_at/finished_at`）；(2) 这些 job 的文档若仍 `uploaded`/`scanning`/`indexing` 且 `deleted_at IS NULL` → `failed/ingest_index_unbound`。ready 文档、tombstone 文档、已在 `failed` 的文档都不动。幂等：重跑无匹配行；可重跑；不创建 job/索引版本/chunk，不动激活指针，因此没有可"复活"的删除 |
| 守卫 | `internal/background/document_ingest.go`：无绑定（`ingest_index_unbound`）、索引版本缺失/属于他人/已非 `building`（`stale_revision`）的尝试，除失败 job 外**同时**把文档置 failed（仅当没有其它 live 构建，见下）；`failAbandonedAttemptTx` + `failDocumentAfterAttemptTx`，从不写 ready/激活指针 |
| 自愈 | `background.LiveDocumentBuildTx`（唯一定义"构建真的在跑"：非终态 `ready`/`leased` job + 同文档同源 revision 且仍 `building` 的索引版本）。`CreateDocument`：`scanning`/`indexing` 且有 live 构建 → T055 幂等返回；没有 → 放弃的索引版本置 `failed/ingest_build_abandoned`、文档置 failed，然后走失败重试路径（新 revision/索引版本/job）。`ReindexDocument`：同源重建只在 live 构建时 409 `index_build_in_progress`，否则回收后再建。`insertIndexVersionTx` 在插入前用同一谓词处理"每源 revision 一个 building"，让孤儿 building 行不再永久阻塞 |
| 新码 | `ingest_index_unbound`、`ingest_build_abandoned`（都是 business、不可重试、页面/诊断可见；`background_jobs.error_code` 本就不在契约内） |

## 2. Finding E（P2，standards）：重复的授权/活动索引 SQL

`internal/knowledge/cache_store.go` 的命中复检原样重抄了检索（`rankRetrieval`）与引用打开
（`GetChunk`）的"当前可读 chunk"谓词（活动索引版本 ready、chunk 属于该版本、
`document_revision` 相等、artifact 归属/ready/未删、KB/文档 tombstone）。三处各一份，
T061 的激活切换当时必须同时改三份——权限条件有漂移风险。

**处置**：新增 `internal/knowledge/readable_chunk.go::currentReadableChunkSQL(ownerParam)`，
一个 `FROM ... WHERE ...` 片段，包含上述全部合取条件；owner 占位符由调用点传入
（检索/引用 `$2`、缓存复检 `$3`，编译期常量）。三处调用点只保留各自的候选集与排序/模型条件。
**纯重构**：谓词集合逐条相同，未新增/删除任何条件，参数编号不变。

## 3. 证明测试

| 测试 | 断言 |
|---|---|
| `tests/f10/test_t061_upgrade_recovery.py::test_an_upgraded_database_ends_a_legacy_ingest_and_reingests_to_ready` | 真实 pre-0039 schema（`--migrations-dir` 截到 0038）+ 真实 T055 形状文档/queued job → 真实 `admin migrate` 到 tip：ledger 出现 `0041_ingest_upgrade_recovery.sql`（按名字、版本从 ledger 读）、job `failed/ingest_index_unbound/business/finished_at`、文档 `failed/ingest_index_unbound`、无索引版本；重复 migrate 是 no-op 且不改行；真实 API + `maintenance run --once` 不认领任何 job；同 artifact 经真实 HTTP 重摄取 → ready、revision 2、索引版本 2 active、检索命中 |
| `...::test_the_guard_ends_a_legacy_job_that_survived_the_migration` | 迁移之后仍存在的 legacy job 由真实 `document_ingest` handler + 完成守卫结束：job/文档都是 `ingest_index_unbound`、无索引版本；同一 artifact 重摄取 → ready |
| `...::test_a_reingest_while_the_build_is_live_is_still_the_same_intent` | live 构建期间的重复摄取仍是 T055 幂等：同一文档、revision 1、1 个 job、1 个 building 索引版本；随后正常 ready（保护既有语义不被自愈破坏） |
| `...::test_a_reingest_heals_a_build_that_died_without_a_live_job` | 写入 sweep 的真实终态（`lease_expired`、无文档写入）→ 重新摄取：revision 2、旧索引版本 `failed/ingest_build_abandoned`（`activated_at` NULL）、新构建 building → ready 且新版本 active |
| `...::test_index_build_in_progress_needs_a_genuinely_live_build` | 真实 live 构建 → 409 `index_build_in_progress`；该构建的 job 死后同源重建不再 409，而是回收（v2 failed/`ingest_build_abandoned`，v1 保持 active 可读）并新建 v3 → ready，v1 superseded |
| `tests/f10`（全部既有用例） | 检索/引用/缓存复检行为不变：越权/删除/换 revision/换索引/失效引用/命中投毒路径全部原样通过 |

## 4. 门禁（`product` 目录，Go 在 PATH）

（见文末"门禁原始输出"一节，命令与末尾输出按实际运行填写。）

- `go build ./...`、`go vet ./...`、`go test ./...`、`gofmt -l .`
- `python tools/sync_spec.py --check`
- `python -m pytest tests/f10 -q`（§6.1 要求背靠背两轮）
- `python -m pytest tests/tooling -q`
- 合并 `integration/f13a-f15a-f09-f10` 后重跑 `tests/f10`

## 5. not_run / 边界

- 浏览器 DOM：页面本轮未改（not_run，与其他页面票同边界）。
- 全量 `pytest tests`：按 BRIEF §3 不运行（orchestrator 负责）。
- 租约耗尽 sweep 的文档写入：本轮不扩展（理由见 ADR 0018 §6 边界）。
- 真实 embedding provider：继承 T055/T067 blocked。
- 历史失败索引/暂存 chunk 的物理清理：F07。
