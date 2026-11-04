# F09 T066 验收：在隔离数据中对比 Mem0 与 PG 并决定是否值得接入

- 任务：F09 / GitHub Issue #67（票 `T066`，规划 ID F09.04）；分支
  `t/t066-mem0-experiment`（worktree `E:\orchara-worktrees\t066`）；父门禁 #53–#68
  集成批次（`integration/f13a-f15a-f09-f10`）；基线 `ddaeb46`（T054/T058 的 PG 记忆基线）。
- 状态：**`passed`**（实验范围：隔离对照已真实执行、结果机器可读、决策有证据）。
  **这不是采用票**：决策为 **`do_not_adopt`（保留 PG）**，Mem0 侧证据按票面要求标记
  **`stub`**（`infer=False` + 确定性本地 embedder，无模型 provider），未被写成正式采用。
- 边界（如实）：真实 PostgreSQL 16.4 + 真实 `cmd/admin migrate` + 真实编译
  `internal/memory` 服务路径（`pgdriver` 调用 `Create/Update/Delete`）+ 真实 sealed 对象字节；
  Mem0 侧为**真实 mem0ai 2.2.1 + 真实 SQLite history + 真实 Qdrant local store**，但只跑
  `infer=False` 写路径；无模型、无 `infer=True` 抽取、无部署型向量库、无多 worker history、
  无备份恢复；**F11 删除治理门禁未通过**（未实现），因此即使数值反转也不能正式采用。
- 产品边界：`mem0ai` **未**加入任何产品运行时依赖；`internal/` **未** import 实验代码；
  未使用产品数据库（每次运行创建并删除一次性 `orchara_t066_mem0_<hex>`）；未改
  `contracts/`、`docs/spec/`，`sync_spec --check` no drift。

## 0. 工作记录（简短）

1. 读 BRIEF、`briefs/T066.md`、票面、T054/T058 报告、ADR 0009、`internal/memory/**`、
   `contracts` 记忆表、`rag-memory-reuse-audit.md` §3、`implementation-stack-options.md` §2、
   `tests/support/{stack,t058}.py`、F15A 探针的报告/README 体例。
2. **可行性时间盒**：在 `product/experiments/mem0/.venv` 安装 `mem0ai==2.2.1` 成功
   （原始 pip 输出 `evidence/pip-install-mem0ai-2.2.1.log`；34 个包的 hash lock
   `requirements.lock.txt`，`--require-hashes` dry-run 通过 `evidence/lock-verify.log`）。
   PyPI 可达，因此**不是 blocked**，按票面进入 **stub** 运行。
3. 先用一次性探针验证：`Memory.from_config` 只配本地 OpenAI-compatible embedder 端点即可
   构造（无模型调用）；`add(..., infer=False)`、`update`、`delete`、`search`、
   `history` 均真实可用；删除后 SQLite `history` 仍保留正文。据此定稿 harness。
4. TDD 垂直切片（先红后绿，红为 `ModuleNotFoundError: mem0exp`）：
   - fixture 确定性/自洽 + embedder 确定性与召回质量（正/负例）；
   - 决策规则（未运行⇒insufficient、删不净⇒do_not_adopt、无收益⇒do_not_adopt、
     仅 stub 有收益⇒insufficient、adopt 需收益+干净+real+F11）；
   - 记录校验（未执行不得标 complete/adopt；adopt 需双系统 run+real+干净+F11）；
   - 无隔离 venv 时 `mem0` 命令必须产出 **blocked** 记录（exit 2），不得跳过/伪造；
   - **真实 PG**：一次性库 + 真实 migrate + 真实 `internal/memory` 写/改/删 + 真实字节 +
     逐列副本枚举 + 真实 SQL 延迟（Go driver 常驻连接，252 次）。
   红阶段原始失败：`ModuleNotFoundError: No module named 'mem0exp'`；实现过程中另修了两个
   真实缺陷（信息模式 `table_type` 查询、Windows 命令行过长导致 psql 失败），以及一个
   **决策读取嵌套指标**的回归（记录里的 recall/latency 是嵌套字段，早期规则读顶层会得 0；
   已加 `test_the_verdict_reads_the_nested_metrics_of_a_real_result_record`）。
5. 执行 `both` 得到 `evidence/t066-both.json`（256-bit SHA-256 见 §9），据此写决策/报告/
   PROGRESS，按 feat/test/docs 提交，最后合并集成分支并复跑门禁。

## 1. 交付内容

### 1.1 实验目录 `product/experiments/mem0/`（独立 venv/target/锁定依赖）

| 文件 | 作用 |
|---|---|
| `README.md` | 运行方式、隔离声明、stub/real 分报、限制、清理 |
| `requirements.in` / `requirements.lock.txt` | 只锁实验依赖 `mem0ai==2.2.1`（34 包，全 hash、跨平台 artifact 哈希）；生成命令写在 in 文件；`--require-hashes` dry-run 通过 |
| `run_experiment.py` | 可重放入口；`pg`/`mem0`/`both`；`--out` JSON；exit 0/2/1 |
| `mem0exp/fixture.py` | 固定种子 66 的合成 fixture：2 用户/3 应用/9 条已确认记忆/1 次修订/1 次删除/1 个 pending 候选/8 正例 + 4 反例；canonical SHA-256 |
| `mem0exp/embedder.py` | 两侧共享的确定性本地 embedder（signed sha256 hashing，64 维，L2 归一）；同一函数以 127.0.0.1 OpenAI-compatible 端点供 mem0 使用（不 monkeypatch mem0 内部） |
| `mem0exp/pg_baseline.py` | PG 侧：一次性库/真实 migrate/真实 `internal/memory` 写改删/真实 SQL 召回延迟/存储/逐列+对象文件副本枚举 |
| `mem0exp/mem0_adapter.py` | Mem0 侧：真实 SDK + SQLite + Qdrant；`infer=False`；副本枚举（SQLite/Qdrant/文件/DEBUG 日志 + 默认 WARNING 对照） |
| `mem0exp/records.py` | 机器可读记录形状 + 校验（禁止假 complete/假 adopt） |
| `mem0exp/decision.py` | 决策规则（默认保留 PG） |
| `mem0exp/runner.py` | 环境/profile/版本/hash/命令记录 + 决策 + 写 JSON |
| `pgdriver/main.go` | 编译实验二进制：直接调用真实 `internal/memory.Service.Create/Update/Delete`，并用常驻 PG 连接对召回 SQL 计时 |
| `.gitignore` / `evidence/` | `.venv/.target/work` 不入库；`evidence/` 保存原始 pip 输出、freeze、lock 验证、结果 JSON 与运行 stdout |

### 1.2 实验隔离

- **PG 数据**：默认每次新建 `orchara_t066_mem0_<hex>`，`cmd/admin migrate` 建真实 schema，
  结束 `DROP DATABASE ... WITH (FORCE)`；显式 `--dsn` 时拒绝开发产品库
  `orchara_probe`。测试用 `tests/support/stack.py` 的一次性库。
- **Mem0 数据**：`experiments/mem0/work/<run>/` 一次性目录（`history.db`、`qdrant/`、
  `mem0-home/`、日志）；`MEM0_TELEMETRY=false` 在 import 前设置。
- **fixture 数据**：全部合成（Alice/Bob 及虚构偏好），无生产数据；证据 JSON 只含 id/hash/
  计数/指标，**不含正文与凭据**（唯一含合成正文的是工作目录与 DEBUG 日志，工作目录不入库）。

### 1.3 两侧测量口径

- **召回**：同一 fixture、同一 query 集、同一 top_k=3；每 query 21 次（≥20）；正例 hit@1/
  hit@3，反例统计 forbidden hit，另断言已删记忆不再被召回、pending 候选从不被召回。
- **延迟**：每个系统 252 次（12 query × 21）真实查询。PG = Python query embedding + 一次真实
  SQL 召回（Go 常驻连接计时）；Mem0 = `Memory.search()`（含其到本地 embedder 的 HTTP 调用）。
  p50/p95 用 nearest-rank。**延迟与主机负载相关**，写入记录并在此声明。
- **存储/成本**：PG 库大小（migrate 后基线 + delta）、表大小/行数、正文 artifact 字节、
  `t066.memory_vectors` 实验索引；Mem0 的 SQLite/Qdrant/工作目录字节、行数、venv 字节、
  安装发行包数。**新增运行时依赖**：Mem0 有（34 个锁包/35 个发行包），PG 无。
- **副本清理（治理门禁）**：删除一条已确认记忆后，枚举正文仍存在于何处：
  - PG：隔离库内所有 `text/varchar/char/json/jsonb` 列（55 表/201 列，逐列 `strpos`）+
    sealed 对象目录全部文件 + tombstone/revision + `deletion_jobs(remaining_scopes)` +
    `policy_epochs`；无产品进程 ⇒ 无文件日志（记录 `not_applicable`，不写成“干净”）；
  - Mem0：SQLite `history`/`messages` 逐表逐列、Qdrant 每个 collection 的 payload、工作目录
    全部文件（含 Qdrant 的 `storage.sqlite`）、进程日志（DEBUG 开关实测 + 默认 WARNING
    对照实例）。

## 2. 关键测量结果（`evidence/t066-both.json`，2026-11-04，Windows 11 / 12 核）

| 指标 | PostgreSQL 基线（real） | Mem0 2.2.1（stub） |
|---|---|---|
| hit@1（8 正例） | **1.0** | **1.0** |
| hit@3（8 正例） | **1.0** | **1.0** |
| 反例 forbidden hit（4 反例） | 0 | 0 |
| 已删记忆再召回 | 否 | 否 |
| pending 候选被召回 | 否 | 否 |
| p50 / p95（252 次） | **0.560 ms / 1.670 ms** | **5.709 ms / 11.107 ms**（p95 ≈ 6.65× PG） |
| 存储 | 库 11.6 MB（migrate 基线 11.4 MB，fixture delta ≈ 240 KiB）；正文 artifact 541 B；对象文件 582 B / 11 个 | `history.db` 20,480 B；Qdrant 29,311 B；工作目录 86,531 B；**venv 162.9 MB / 10,076 文件** |
| 依赖成本 | 无新增（复用现有 PG + Go 服务） | **35 个 Python 发行包**，Mem0 成为新运行时依赖 |
| 删除后正文副本 | **1 个 sealed 对象文件**（T058 同步停止召回；`deletion_jobs` 为 `pending`，F07 负责物理清理；tombstone revision=2、epoch 前移、job scopes=`artifact/embedding/memory_row/object`） | **6 处**：SQLite `history.new_memory` + `history.old_memory`、`history.db` 文件、`qdrant/.../storage.sqlite` 文件、DEBUG 日志 1 行；默认 WARNING 日志 0 行 |
| 修订旧正文 | 旧 `memory_body` artifact 字节保留（F07 债务，1 个对象） | SQLite history 保留旧/新正文 + 磁盘文件（未清理） |

**平台基线说明（重要）**：本分支的 PG 基线是 T054/T058 的存储与删除语义，运行时召回属
T063（不在本分支基线）。T063 的语义是“按 owner+application 列出全部当前记忆，由上下文预算
截断”，**不做 query 排序**；对这个小 fixture，全部 3 个 scope 的当前记忆都能进预算
（4/2/2 条，约 60/21/27 tokens），因此 T063 语义下期望记忆按构造必在注入集合内——这不是
query 级召回率。上表的 query 级数字是实验补充的“最便宜可信 PG 语义基线”：同一确定性
embedder + `t066.memory_vectors`（实验私有表，**不是**平台迁移）+ T058 的
owner/application/deleted/revision 守卫。该限制已写入结果 JSON，未伪装成产品能力。

## 3. 决策（ADR 风格）：**do_not_adopt — 保留 PostgreSQL**

**结论：`do_not_adopt`（保留 PG）。** 依据按优先级：

1. **删除治理不合格（单独即可否决）**：Mem0 `delete()` 后，被删正文仍留在它自己的
   SQLite `history` 两行（ADD 行 `new_memory` + DELETE 行 `old_memory`）以及
   `history.db`、Qdrant 本地 `storage.sqlite` 的磁盘字节中；DEBUG 日志也会含正文
   （默认 WARNING 不含）。Mem0 没有平台可用的清理 job/API，无法证明“删除即清理”。
2. **没有收益**：在共享确定性 embedder 下，Mem0 召回与 PG 基线相同（hit@1/hit@3 均 1.0），
   但 p95 慢 6.65×（11.1 ms vs 1.67 ms），并新增 35 个发行包 / 163 MB venv 与第二套存储。
3. **PG 的残留是“已登记、可治理”的**：T058 的删除在同一事务里写 tombstone + revision+1 +
   `deletion_jobs(pending, remaining_scopes)` + epoch，旧内容**立即不可召回**，物理清理明确
   归 F07；本实验逐列枚举证明正文只在 1 个 sealed 对象里（数据库无正文列），没有第三份副本。
4. **正式采用还须 F11 治理门禁**：即使将来 Mem0 数值更好、删除更干净，本票也只到
   “候选（candidate）”，F11 的物理清理/保留与备份策略/厂商数据出网审批未通过前不得采用。
   本记录的 `decision.gates.f11.passed=false`，`remaining_gates=["F11"]`。

**会改变结论的证据**（未出现）：在真实模型与真实部署形态下 Mem0 召回显著更好或 p95 至少低
20%，且 `delete()` 后枚举不到任何正文副本，且 F11 通过。本次 stub 口径不构成该证据。

## 4. AC 核对

| AC（票面） | 结果 | 证据 |
|---|---|---|
| 1. 记录 SQLite 历史/消息/实体/向量/日志清理 | **proven（Mem0 侧，stub 运行）** | `cleanup.sqlite`（2 表/16 列；`history` 11 行，正文在 `new_memory`×1 + `old_memory`×1；`messages` 0 行）、`cleanup.qdrant`（删除后点数 8、payload 含正文 0）、`cleanup.objects`（3 个文件含正文）、`cleanup.logs`（DEBUG 1 行 / 默认 WARNING 0 行）、`cleanup.entity_store`（infer=False 不写实体）；测试 `test_the_committed_evidence_is_a_stub_mem0_run_that_does_not_adopt` 断言该记录存在且决策不采用 |
| 2. 无收益或删不净就不用 | **proven** | 决策 `do_not_adopt`，两条理由同时成立（治理 + 无收益）；规则单测 `test_an_unclean_delete_disqualifies_mem0_even_with_better_recall`、`test_no_measurable_benefit_keeps_postgres`；实测 §2 |
| 3. 未运行不标实验完成或正式采用 | **proven** | `records.validate` 拒绝 `experiment_status="complete"`、拒绝在任一系统非 run 时 adopt、拒绝 stub-only adopt、要求 F11；测试 `test_a_record_refuses_false_completion_and_adoption`、`test_the_withheld_mem0_run_is_recorded_as_blocked_without_fake_numbers`（无 venv 时 exit 2 + blocked，无 recall/latency 数字）；本记录 `experiment_status="executed"`、`stub_real.mem0="stub"` |
| 4. 可重放入口；结果机器可读且可由记录命令复现 | **proven** | `run_experiment.py both --out ... --iterations 21`（退出 0，stdout 见 `evidence/t066-both.stdout.log`）；固定 fixture seed/hash、固定 embedder、记录 versions/inputs/commands；`records.validate` 通过；测试 `test_the_pg_baseline_measures_recall_latency_storage_and_deletion_cleanup` 在测试进程内独立重放 PG 侧 |
| 5. 记录版本、输入 hash、命令、原始结果与 profile；stub/real 分报 | **proven** | `evidence/t066-both.json` 的 `environment/inputs/commands/versions/stub_real`；`evidence/pip-install-mem0ai-2.2.1.log`（原始 pip 输出）、`venv-freeze.txt`、`lock-verify.log`；§9 列 hash/版本；报告 §8 分报 |

## 5. 命令与原始输出

环境：Windows 11（10.0.26200）+ PowerShell 7；`Python 3.12.2`；`node v24.13.0`；
`go1.27.3 windows/amd64`；PostgreSQL 16.4（用户态集群 `127.0.0.1:55432`）。

```text
# 1) 隔离 venv + 真实安装（完整原始输出：experiments/mem0/evidence/pip-install-mem0ai-2.2.1.log）
$ python -m venv experiments/mem0/.venv
$ experiments/mem0/.venv/Scripts/python -m pip install --no-input "mem0ai==2.2.1"
  ...
  Successfully installed annotated-types-0.8.0 anyio-4.16.0 backoff-2.2.1 ... mem0ai-2.2.1
  numpy-2.5.4 openai-3.45.0 portalocker-4.4.2 posthog-7.83.1 protobuf-6.33.6 pydantic-2.14.2
  qdrant-client-1.20.0 ...           (exit 0)

# 2) 锁定依赖（hash lock，34 包）
$ experiments/mem0/.venv/Scripts/python -m pip install --dry-run --ignore-installed \
      --report experiments/mem0/pip-report.json -r experiments/mem0/requirements.in
$ python probes/agent_min/lock.py experiments/mem0/pip-report.json experiments/mem0/requirements.lock.txt
locked 34 packages -> experiments/mem0/requirements.lock.txt

$ experiments/mem0/.venv/Scripts/python -m pip install --dry-run --ignore-installed \
      --require-hashes -r experiments/mem0/requirements.lock.txt
  ... Would install SQLAlchemy-2.1.8 ... mem0ai-2.2.1 ... urllib3-2.8.0     (exit 0)

# 3) 对照实验（真实 PG + stub Mem0，12 query × 21 次）
$ $env:MEM0_TELEMETRY="false"; experiments/mem0/.venv/Scripts/python \
      experiments/mem0/run_experiment.py both --out experiments/mem0/evidence/t066-both.json \
      --work-dir experiments/mem0/work/both
record: experiments/mem0/evidence/t066-both.json sha256=6db5843b9ea83adc158bf7a480171129b63967328fc8175f2d283f885d3cc1ce
  pg: run (real) hit@1=1.0 hit@k=1.0 p50=0.559881ms p95=1.669781ms clean=False residual=1
  mem0: run (stub) hit@1=1.0 hit@k=1.0 p50=5.7088ms p95=11.1067ms clean=False residual=6
decision: do_not_adopt
  - deletion still leaves body copies ... sqlite:history.new_memory, sqlite:history.old_memory
  - Mem0 also shows no measurable benefit: recall is not better and p95 is not at least 20% lower ...
(exit 0)

# 4) T066 套件，两次背靠背（BRIEF §6.1）
$ python -m pytest tests/f09/test_t066_mem0_experiment.py -q
15 passed in 40.10s
$ python -m pytest tests/f09/test_t066_mem0_experiment.py -q
15 passed in 46.10s

# 5) 全 F09 套件（T054 25 + T058 22 + T066 15 = 62；此处为本票合并前最终树）
$ python -m pytest tests/f09 -q
61 passed in 646.43s (0:10:46)
```

说明：`tests/f09` 首次统计为 **61**（T054 25 + T058 22 + **T066 14**）运行于新增嵌套指标回归
之前；随后补了第 15 个 T066 用例，合并后复跑见 §5.1。两次 T066 背靠背均全绿，耗时差异来自
主机负载，无时间竞态断言（实验只断言数值/集合/枚举，不断言亚秒窗口）。

```text
$ go build ./...            (no output, exit 0)
$ go vet ./...              (no output, exit 0)
$ go test ./...             (all packages ok; no FAIL; exit 0)
$ gofmt -l .                (no output)
$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest   (exit 0)
$ python -m pytest tests/tooling -q
21 passed in 5.76s
```

### 5.1 合并结果（BRIEF §7.6）

```text
$ git merge integration/f13a-f15a-f09-f10
<见本节末尾；合并后重跑数字在此填写>
```

## 6. `not_run` / 限制（如实）

| 项 | 原因 / owning ticket |
|---|---|
| Mem0 `infer=True` 抽取、LLM query rewriting、reranker、真实模型 provider | 本机无模型凭据/profile；本票只需要 `infer=False` 写路径。结果标记 `stub`，不构成采用证据 |
| 部署型 Qdrant/托管向量库、多 worker 共享 history、备份/恢复 | 实验用 Qdrant local（embedded）；多 worker history 归属是 Mem0 接入的独立门禁，未运行 |
| 真实规模/性能（0.5→N 条、并发、连接池） | 本票是隔离小 fixture 对照，未做规模压测；未跑无界负载生成器（BRIEF 禁止） |
| BM25/spaCy hybrid 关键词检索 | `mem0ai[extras]`/`nlp` 未安装；mem0 明确降级为纯语义，已作为 stub 限制记录 |
| PG query 级召回的“产品实现” | T063（#64）拥有运行时召回且不在本分支基线；实验用同一 embedder 的最便宜 PG 语义基线并记录限制 |
| 物理副本清理（对象/artifact/embedding 行） | **F07** 删除治理；Mem0 侧同样没有清理 job（这正是否决理由之一） |
| 浏览器/UI | Mem0 无平台 UI，本票不添加；无 DOM 驱动 |
| 全量 `pytest tests`（约 67 分钟）、全量 f01 | BRIEF §3 明令不跑；由 orchestrator 合并后运行 |
| 正式采用 / 依赖接入 | 未采用；`mem0ai` 未进入产品 lock，`internal/` 未 import；正式采用须 F11 |

## 7. 后续票接口 / 影响

- **产品代码零改动**：本票只新增 `product/experiments/mem0/` 与
  `product/tests/f09/test_t066_mem0_experiment.py`；无迁移、无契约、无 `internal/` 改动。
- **T063（运行时召回）**：实验的 PG 基线守卫（owner+application、`deleted_at IS NULL`、
  `revision` 当前、sealed 摘要校验）与 T063 语义一致；T063 若加 embedding 索引，本实验的
  `t066.memory_vectors` 口径可直接作为离线对照基线（但该表永远不会进产品 migration）。
- **T067（检索缓存）**：Mem0 若未来重启评估，缓存键必须含平台 `(memory_id, revision)` +
  epoch，并且 Mem0 的 vendor id 映射仍以平台 id 为权威；本实验任何结论都不改变这一 seam。
- **F07**：PG 的 1 个残留 sealed 对象与旧 revision artifact 属已登记债务；`deletion_jobs`
  的非 pending 生命周期由 F07 完成。
- **F11**：正式采用 Mem0（或任何第三方记忆存储）的唯一剩余门禁；本记录持续保留
  `remaining_gates=["F11"]`。

## 8. stub / real 分报

| 系统 | 证据等级 | 真实部分 | stub/未运行部分 |
|---|---|---|---|
| PostgreSQL | **real** | 真实 PG 16.4、真实 `cmd/admin migrate`、真实 `internal/memory.Service` 写入/修订/删除（编译 driver）、真实 sealed 对象字节、真实删除 job/tombstone/epoch、真实逐列/文件副本枚举、真实 SQL 延迟 | query 级召回指标是实验实现（T063 不在基线）；`t066.memory_vectors` 是实验索引替身 |
| Mem0 | **stub** | 真实 mem0ai 2.2.1、真实 SQLite history、真实 Qdrant local、真实 `add(infer=False)/update/delete/search`、真实枚举 | 无模型 provider ⇒ 无 `infer=True`/rerank/真实模型；Qdrant 为 embedded；BM25/spaCy 未装 |

## 9. 输入、版本与 hash

| 输入 | 值 |
|---|---|
| 基线 | `ddaeb46`（集成 tip，T054/T058 的 F09 PG 基线） |
| 结果文件 | `experiments/mem0/evidence/t066-both.json`，SHA-256 `6db5843b9ea83adc158bf7a480171129b63967328fc8175f2d283f885d3cc1ce` |
| fixture | schema `t066.mem0.fixture.v1`，seed `66`，canonical SHA-256 `59f439dead24aee865bc2fadd5822e1f1214df27f1992ef2070672f58a55138b` |
| embedder | `t066-hashed-bow-v1`，64 维，`embedder.py` SHA-256 `49dd0a5095b85414d0a47438311dedfc66c6a5376d01357c28a94643878be6a0` |
| harness | `harness_sha256` `62552204dc314643020cc5cbfa7cb710e36a00a45d85c03b268808d3d9c5fdc1`（`.py/.go/.in/.txt`，不含文档/证据） |
| requirements | `requirements.in` SHA-256 `0fa60d0fe4f67ea98a269594c2ac6f497ef7e328647bb1dc16994196cb0b0fa4`；lock SHA-256 `1de3838f34824af007d3519c57013b00746ae1af85a659c6496d519c9d376091`（34 包） |
| Mem0 版本 | `mem0ai 2.2.1`、`qdrant-client 1.20.0`、`sqlalchemy 2.1.8`、`openai 3.45.0`、Python 3.12.2；`venv-freeze.txt` 记录 34 个精确版本 |
| PostgreSQL | 16.4（`SHOW server_version`）；`cmd/admin` 二进制 SHA-256 `5b04ffbe09bbe87a6a7c86fe24d9af509dc475d13fe997d3e24066c9a584d77c`；`pgdriver` 二进制 SHA-256 `c7683d3f9cdef48db8f15b3e616e82c5a64a634b29098a8b11207e0902b0b678` |
| 命令 | 结果 JSON `commands`：venv 安装、lock 验证、`go build`×2、`admin migrate`（36 个迁移，输出 tail 已脱敏）、`pgdriver create×9 / update×1 / delete×1 / bench×252`、`search×252`、副本枚举 |

## 10. F11 门禁（正式采用的剩余条件）

`decision.gates.f11 = {passed: false, reason: "F11 deletion governance 尚未交付 physical purge、
retention/backup 策略与厂商数据出网审批"}`，`remaining_gates = ["F11"]`。本票为实验/取证，
不改变 F09 的 PG 权威；任何后续“接入 Mem0”的票必须先过 F11，且需在真实模型 + 真实部署形态
下重跑本 harness（`docs/adr/` 若决策改变再新增 ADR）。
