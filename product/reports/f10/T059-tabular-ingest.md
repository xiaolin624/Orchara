# F10 T059 验收：摄取 CSV/XLSX 并保留行范围和安全错误

- 任务：F10 / GitHub Issue #60（票 `T059`，规划 ID F10.02），分支
  `t/t059-tabular-ingest`（worktree `E:\orchara-worktrees\t059`，基线 integration tip
  `9f58fc2`）；父门禁 #53–#68 集成批次。
- 状态：**`passed`**（本票范围 = 在 T055 摄取链上扩展 **CSV/XLSX 一种输入族**：
  解析器注册、行批次分块与 citation 行范围、公式/DTD/宏/压缩比/行列单元格上限的
  failed 文档与只读派生诊断位置、`/knowledge` 页面 CSV/XLSX 上传与行范围/位置展示。
  真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP/mTLS + 真实封存对象字节 +
  真实 maintenance 双池进程 + Node 执行页面逻辑；无 mock、无跳过。）
- 边界（如实）：真实 embedding provider **blocked**（继承 T055：无凭据/adapter，
  fail closed）；浏览器 DOM 驱动页面 **not_run**（与既有页面票同一边界）；PDF（#61/
  T060）、版本重建（#62/T061）、删除治理（F07/T058）、检索规模/缓存（#64/#68）不在本票；
  XLSX 只读**第一个工作表**，多工作表选择留待后续票；全量 `pytest tests` 按 BRIEF §3
  不在本票运行（≈67 分钟，orchestrator 负责）。
- 契约偏差：**无新增/修改契约**。`citation` 是 jsonb，本票只加 additive 字段；诊断
  `detail` 是 T055 已登记的非契约读的扩展，见 **ADR 0012**；`docs/spec/**`、
  `contracts/**` 未改，`sync_spec --check` no drift。
- 迁移：**无**（0034 已提供全部存储；本票不新增表/列）。

## 0. 工作记录（简短）

1. 读 `BRIEF.md`、`briefs/T059.md`、票面 #60、T055 报告/ADR 0010、`internal/knowledge`
   （Parser/Parsed/Chunk/Span/Citation、handler 映射、完成守卫）、
   `internal/artifacts/{admission.go,upload.go}`（准入与 archive 上限）、
   `0034_knowledge.sql`、`tests/f10` + `tests/support/t055.py`、以及
   `worker/platform_worker/nodes/sales_stats.py`（**仅**用于理解表头/数据行/行号口径，
   未导入、未调用）。
2. TDD 垂直切片（每片先红后绿）：
   - CSV：源文本保留解析（BOM/CRLF/引号/字段内换行/空行跳过）→ Go 单测；
   - 行批次 chunker + citation 行字段（行范围/表头行/`row_count`/`text_render`）→ Go 单测；
   - XLSX：zip+XML 只读解析、公式/DTD/宏/压缩比/条目/工作表/行/列/单元格/单行上限 →
     Go 单测（真实 zip 夹具）；
   - handler：`ChunkParsed`/`ChunkerVersionFor`/`*ParseError` → 稳定 failed 码；
   - 诊断读：`detail` 只读派生（同 parser revision + 同封存字节重放）→ HTTP 用例；
   - 真实栈：CSV/XLSX 上传→封存→摄取→ready→检索→citation 独立真值；失败矩阵；
     ACL/引用协议一致性；重试/恢复边界；页面结构 + Node 执行 helper。
3. 跑门禁（§3），两次连跑 f10；写本报告/`PROGRESS.md`/ADR 0012；按 feat/test/docs
   提交；合并 `integration/f13a-f15a-f09-f10` 后复跑目标门禁（§3 末尾）；未 push、
   未关 issue、未改 `docs/spec/**`/`contracts/**`、无迁移。

## 1. 交付内容

### 1.1 解析器注册表（T055 缝的扩展，不是新链）

- `internal/knowledge/parse.go`：新增 `text/csv` → `orchara-csv-parser/1`、
  `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` →
  `orchara-xlsx-parser/1`；`SupportedMediaTypes()` 稳定顺序。PDF 仍不注册（T060 → 422）。
- `Parsed` 增加 `TextRender` 与 `Table`（tabular 行范围），`Chunk` 增加 `Table`
  （`ChunkTable`）；`ChunkParsed` 按 family 分派到既有段落 chunker 或新的行批次 chunker
  （`orchara-table-row-batch-chunker/1`）。txt/md 的解析、chunk、citation 字段不变。

### 1.2 CSV：源文本保留 + 逻辑记录行号

- `internal/knowledge/parse_csv.go`：单 BOM 剥离（记录 `utf8_bom_stripped`），其余字节
  原样保留（CRLF、引号、字段内换行），因此 citation 的 rune span 可对封存源字节回切。
- 严格 RFC 4180-ish 扫描：`"` 包裹、`""` 转义、字段内逗号/换行、CRLF/LF/裸 CR；空行
  跳过且不占行号；**表头 = 逻辑行 1，数据从行 2 开始**；未闭合引号/未引字段中的引号/
  闭合引号后多字符 → `ingest_invalid_csv`（带记录号）。
- 上限：数据行 ≤ 10 000、列 ≤ 64、单行 ≤ 64 KiB；超限 → `ingest_row_limit_exceeded`/
  `ingest_column_limit_exceeded`/`ingest_row_too_large`。行永不被拆成多个片段。
- 公式：CSV 没有公式元数据，任何内容都不执行；测试断言 `=SUM(...)` 以字面文本保留。

### 1.3 XLSX：只读 zip+XML 数据读取器，公式绝不执行

- `internal/knowledge/parse_xlsx.go`（仅标准库 `archive/zip` + `encoding/xml`）：
  `[Content_Types].xml`、`xl/workbook.xml`（工作簿顺序第一个 sheet 的名字与 r:id）、
  工作簿 rels、`xl/sharedStrings.xml`（富文本 run 拼接）、第一个 worksheet 的
  row/cell（共享字符串 / inlineStr / 字符串 / 数值 / 布尔 / 错误 / 日期原文）。
- **公式单元格（`<f>`）→ `ingest_formula_not_supported` + `{sheet,row,column,cell}`**；
  即使有缓存 `<v>` 也不作为内容（未重算缓存可能过期/伪造，平台未验证）。
- **DTD/实体**（在任何要解析的 XML 部件中）→ `ingest_dtd_not_supported`；
  **宏**（`vbaProject.bin`/`xl/macrosheets/`/content-types `macroEnabled`）→
  `ingest_macro_present`；路径穿越 → `ingest_archive_path_traversal`。
- **归档上限在信任内容前**：条目数 512、解压总量 64 MiB、压缩比 100（总量与单条目）、
  单条目/总量溢出保护、入口路径谓词；全部复用 `internal/artifacts` 既有常量，并导出
  `artifacts.SafeArchivePath` 复用同一路径规则（不另立第二套）。
- **内容上限**：工作表 ≤ 64、数据行 ≤ 10 000、列 ≤ 64、单元格 ≤ (10 000+1)×64、
  单行 ≤ 64 KiB；超限 → `ingest_worksheet/row/cell/column_limit_exceeded`、
  `ingest_row_too_large`。bad zip/缺 workbook/坏 XML/坏 shared-string 引用 →
  `ingest_invalid_xlsx`。
- 渲染：`Parsed.Text` 是确定性 CSV 渲染（表头一行 + 数据行，`\n`），citation
  `text_render = "sheet_csv/1"`；行号是**工作表自身行号**（`r`，1-based），允许空洞，
  `row_count` 为片段实际数据行数。`sheet_name` 进入 citation；第二个 sheet 不摄取。

### 1.4 citation 行范围（唯一溯源文档的 additive 扩展，ADR 0012 §4）

`knowledge_chunks.citation` 新增（`omitempty`，txt/md 不变）：
`row_start`、`row_end`、`row_count`、`header_row`、`sheet_name`、`columns`、
`columns_omitted`、`text_render`。handler 从 `chunk.Table` 写入；完成守卫的既有
citation 一致性检查不变。

### 1.5 失败位置：只读派生，不新增状态权威/写路径/迁移（ADR 0012 §5）

- `GET /api/v1/knowledge-documents/{id}/diagnostics` 新增 `detail`
  （`{sheet,row,column,cell,part,limit,value}`，可空）。
- 仅当 failed 且 `error_code` 是解析拒绝码、记录的 `parser_version` == 当前注册版本、
  源 artifact 仍 ready/可读时，用同一解析器重放同一封存字节；得到相同 `*ParseError`
  才返回 location，否则 null。`error_code` 仍是唯一持久事实，完成守卫仍是唯一
  `state/active_revision/error_code` 写者。

### 1.6 handler/API/页面

- `internal/background/handlers/document_ingest.go`：解析器错误映射增加 `*ParseError`
  → 其稳定码；`ChunkParsed` + `ChunkerVersionFor`；citation 写 table 字段。
- `internal/api/knowledge.go`：诊断视图增加 `detail`；不支持格式的 422 文案更新为
  txt/md/csv/xlsx（PDF 仍 422）。
- `apps/web/src/{knowledge.html,assets/knowledge.js}`：`.csv/.xlsx` 扩展名→声明媒体
  类型、上传 accept、行范围渲染（`formatRowRange`：CSV 逻辑行 / XLSX 工作表名+工作表
  行号+表头行+`row_count`）、失败位置渲染（`describeFailureDetail`）、新失败码文案。
- 授权/引用：无按格式分叉——CSV/XLSX 与 txt/md 共用同一 KB ACL、同一 SQL 先过滤后
  排序、同一 `GET /knowledge-citations/{chunk_id}` 重校验。

### 1.7 夹具与生成脚本

- `tests/fixtures/f10/t059_sales.csv`（2 541 B，UTF-8 BOM + CRLF，60 数据行，含引号
  逗号/双引号/字段内换行）；
- `tests/fixtures/f10/t059_workbook.xlsx`（3 297 B，真实 zip；2 个 sheet，shared
  strings，表头行 1，数据行 2–41、43–44，**42 缺行**用于行号空洞）；
- `tests/fixtures/f10/build_fixtures.py`：确定性生成器（固定 zip 时间戳/条目顺序），
  测试只读产物、不运行生成器；哈希见 §4。

## 2. AC 逐条

| # | 验收标准 | 结果 | 证据 |
|---|---|---|---|
| 1 | CSV/XLSX 片段保留行范围 | **proven** | `test_t059_tabular.py::test_csv_rows_become_ready_with_row_ranges_verifiable_against_sealed_bytes`（每个 chunk 的 `row_start/row_end/row_count/header_row`；span 回切封存源字节；body 重解析 == 源记录切片）；`::test_xlsx_chunks_keep_the_sheet_name_and_worksheet_row_numbers`（`sheet_name=Sales`、工作表行号 2–41/43–44、空洞 42、独立 zip+XML 真值逐行相等）；Go 单测 `parse_csv_test.go`/`parse_xlsx_test.go` |
| 2 | 公式不执行且压缩/行列超限拒绝 | **proven** | `::test_formula_cells_are_never_executed_and_the_page_can_diagnose_the_location`（failed `ingest_formula_not_supported`、0 chunk、detail 精确到 B2、同 artifact 重试仍 failed、换正确内容后 ready）；`::test_content_refusals_end_as_failed_documents_with_specific_codes`（压缩比/行/列/工作表/单行超限、DTD、宏、坏 CSV、坏 zip、空 CSV 逐一 failed + 具体码 + 期望 detail）；Go 单测真实 zip（含 1 MiB 重复单元格压缩炸弹、65 工作表、10 001 行、65 列、640 128 单元格） |
| 3 | 解析失败在页面可诊断 | **proven** | 上述 HTTP 用例断言 diagnostics `detail`；`test_t059_pages.py::test_knowledge_js_tabular_behaviour_executes_under_node`（Node 执行 `describeDiagnostics`/`describeFailureDetail`/`describeIngestion`，显示 `工作表 Orders · 行 2 · 列 B · 单元格 B2`、`失败位置`、压缩比上限）；diag `detail` 为 null 时无位置文案 |
| 4 | 不同格式使用同一授权和引用协议 | **proven** | `::test_csv_xlsx_and_text_share_the_same_authorization_and_citation_protocol`（同 KB 内 csv+xlsx+txt 同轮检索；每个 item 的 citation 与 DB 一致；B/管理员对 KB 检索、每格式 document 与 citation 均同一 404 `resource_not_found`；撤销 CSV 文档后其片段从检索消失、citation 404，xlsx/txt 仍可读） |
| 5 | 真实端到端入口；失败/授权/重试边界有断言，无单层 mock | **proven** | 所有用例经真实 `POST /artifacts/uploads`→签名 PUT→complete→真实 finalize 准入→`ingestDocument`→真实 maintenance 双池→ready/failed；公式失败→revision 2 再失败→新 artifact 恢复 ready；`tests/f10` 28 passed ×2；page 结构 + Node 执行 |
| 6 | 记录版本、输入/hash、命令、原始结果、profile；stub/real 分报，缺条件 blocked、未运行 not_run | **proven（含显式 blocked/not_run）** | 本报告 §3/§4/§5；版本：Go 1.27.3、Python 3.12.2、Node 24.13.0、PG 16.4；夹具 SHA-256 与输入字节；真实 embedding provider blocked（继承 T055）；浏览器 DOM not_run |

## 3. 门禁命令与原始输出

环境：Windows PowerShell；Go 1.27.3（`E:\orchara-toolchains\go\bin`）、Python 3.12.2、
Node v24.13.0、PostgreSQL 16.4（`127.0.0.1:55432`，`tools/pg_dev.py status`）；
`product/.env.local` 提供本地口令，报告不含任何凭据。

```text
$ python -m pytest tests/f10 -q          # 第 1 次
............................                                             [100%]
28 passed in 289.16s (0:04:49)

$ python -m pytest tests/f10 -q          # 第 2 次（紧接第 1 次）
............................                                             [100%]
28 passed in 286.58s (0:04:46)

$ go build ./...
(exit 0, no output)

$ go vet ./...
(exit 0, no output)

$ go test ./...
ok  	orchara/platform/internal/supervisor	(cached)
ok  	orchara/platform/internal/tools	(cached)
?   	orchara/platform/probes/f15a_objectstore/localfs_probe	[no test files]
# 33 个包 ok / 0 FAIL

$ gofmt -l .
(empty)

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

相邻套回归（本票改到 artifacts 导出、document_ingest handler、api 诊断视图、页面资源）：

```text
$ python -m pytest tests/tooling tests/f05/test_t028_quarantine.py tests/f05/test_t028_upload_flow.py -q
.....................................                                    [100%]
37 passed in 145.74s (0:02:25)

$ python -m pytest tests/f00b/test_t024_artifacts.py tests/f00b/test_t022_finalize.py -q
......................                                                   [100%]
22 passed in 123.90s (0:02:03)
```

> 说明：全量 `pytest tests` 按 BRIEF §3 不在本票运行（≈67 分钟），由 orchestrator 在
> 合并后执行。本票另运行了 `tests/f10` 全量 + `tests/tooling` + T028 准入两个文件 +
> T024 artifact/T022 finalize 两个文件；`tests/f00b` 整包与 `tests/f05` 整包未在合并前
> 整跑（合并后由集成门禁负责），故不写成"全绿"。

## 4. 真实栈、输入与版本

- 真实 PG 16.4（每个 fixture 新建临时库，真实 `cmd/admin migrate` 0001→当前链；
  本票无迁移，链不变）；真实 `cmd/api` 二进制（真实 HMAC 签名链接、文件系统
  `staged/`/`sealed/` 字节、internal mTLS）；真实 `cmd/maintenance` 进程
  （work 池 `document_ingest`，system_io 池 `artifact_finalize`，2+2 slot）。
- 输入/哈希（`tests/fixtures/f10/build_fixtures.py` 生成，提交后测试只读）：
  - `t059_sales.csv`：2 541 B，SHA-256
    `7c87e8c2dd577172e207a58a4eb9cc37df82374acb20b37b1f226ff1348b1513`；
  - `t059_workbook.xlsx`：3 297 B，SHA-256
    `5507db9029c565060d4651c9ddb8fe011887a400e65e8d86b29cf8b4dbef8630`；
  - 失败夹具在测试内确定性生成（公式/DTD/宏/压缩炸弹/600+ 条目/10 001 行/65 列/
    65 工作表/64 KiB+ 单行/坏 CSV/坏 zip），无外部下载、无凭据。
- 版本：`orchara-csv-parser/1`、`orchara-xlsx-parser/1`、
  `orchara-table-row-batch-chunker/1`、`local-hash-v1`（384 维，继承 T055）。
- 原始结果：pytest 输出见 §3；每个用例的维护进程日志由 `tests/support/t055.py` 写入
  pytest 临时目录（`maintenance-t059-*.log`），报告不复述日志正文；夹具生成命令见
  `tests/fixtures/f10/build_fixtures.py` 文件头。

## 5. stub / real、blocked、not_run（分开记录）

| 项 | 结论 | 具体条件 |
|---|---|---|
| CSV/XLSX 解析与行范围 | **real executed** | 真实封存字节 + 真实 handler + 独立 Python/Go 真值；无 mock |
| 公式/DTD/宏/压缩比/行列上限拒绝 | **real executed** | 真实 zip 字节；失败码/失败类/无 chunk/诊断位置逐项断言 |
| 上传准入（`purpose=knowledge` 已收 `text/csv`/XLSX） | **real executed / 无改动** | Go 单测 `TestKnowledgeUploadsAlreadyAdmitCSVAndXLSXWithoutAnAdmissionChange` + 真实上传的 `artifact_admissions` 行（declared/sniffed/accepted） |
| 真实 embedding provider | **blocked** | 继承 T055：本环境无凭据/构建无 adapter；handler/HTTP fail closed，从不回退本地向量 |
| 浏览器 DOM 驱动页面 | **not_run** | 只做 `GET /knowledge` 结构与 Node 执行纯 helper（与既有页面票同一边界） |
| PDF（#61/T060） | **not_run / 后续** | 仍 422；T055 用例改为用 PDF 断言未注册格式 |
| 版本重建/激活切换（#62/T061） | **not_run / 后续** | 复用 T055 的 `revision`/`active_revision` seam |
| 删除治理（F07/T058） | **not_run / 后续** | 测试只写 tombstone 证明检索/citation 立即不可读 |
| 检索规模/缓存（#64/#68） | **not_run / 后续** | 本票不加缓存；`acl_epoch` 语义未变 |
| 全量 `pytest tests`、`tests/f05`/`tests/f00b` 整包 | **not_run（orchestrator）** | 按 BRIEF §3；本票跑定向/相邻文件（§3） |

## 6. 下一票接口（复用点）

- 解析器：T060 实现 `knowledge.Parser` 并在 `parserRegistry` 注册；tabular 解析器可参考
  `Parsed.Table`/`Chunk.Table` 模式，但 PDF 的页范围应新增自己的 additive citation 字段
  （不要复用 `row_start` 表达页码）。
- 行范围/citation：`Citation` 的 tabular 字段已固定；XLSX 的 `text_render = "sheet_csv/1"`
  与 `parser_version` 绑定，换渲染规则 = 新 parser revision。
- 失败位置：`GET /knowledge-documents/{id}/diagnostics` 的 `detail` 是只读派生；
  T061/T064 若要持久化历史失败位置，应作为新的数据模型决定并更新 ADR。
- 上限常量：`internal/knowledge/table.go`（`MaxTableRows/Columns/Cells/Worksheets/RowChars`）
  与 `internal/artifacts` 的 archive 常量是唯一来源；T064 若要放宽必须同改码/测试/ADR。
- 页面：`knowledge.js` 的 `formatRowRange`/`describeFailureDetail` 可被后续格式复用；
  多工作表选择、PDF 页码显示是后续票的页面工作。

## 7. 与契约/既有票据的关系（如实）

1. **无迁移、无契约改动**：`contracts/**`、`docs/spec/**` 未改；`sync_spec --check` no drift。
2. `citation` 新增字段是 jsonb additive 扩展（ADR 0012 §4）；txt/md 的既有 citation 不变
   （omitempty），T055 的 20 个用例 + 本票 8 个新用例全绿。
3. 诊断 `detail` 是 T055 非契约读的扩展（ADR 0012 §5），不是新端点/新状态表。
4. **T055 用例的一处预期更新**：`test_ingest_requires_a_ready_owner_artifact_and_a_registered_parser`
   原断言“CSV 未注册 → 422”，T059 注册后该断言被本票取代：CSV 现在 202 进入
   scanning，未注册格式改用 PDF（T060）证明仍是 422。这是本票唯一改动的既有断言，
   已在 diff 中可见。
5. **准入无改动**：`uploadPurposes`/`SupportedUploadMediaTypeForPurpose` 本就接受
   `purpose=knowledge` 的 `text/csv` 与 XLSX；本票只加单测钉住该事实并复用其 archive
   常量/路径谓词。解析器的内容上限是同一输入族的第二道细校验，不是第二套准入。

## 8. 参考

- ADR：`product/docs/adr/0012-f10-tabular-ingest.md`（本票）；`0010-f10-knowledge.md`
  §6 扩展点 + 后续更新指针；`0005`/`0007` 非契约先例。
- 基础：`product/reports/f10/T055-knowledge-ingest.md`。
