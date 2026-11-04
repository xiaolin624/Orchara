# ADR 0013：F10 表格输入族的解析、行范围与只读诊断边界

- 状态：已接受
- 日期：2026-11-04
- 编号：合并 `integration/f13a-f15a-f09-f10`（T064）后让号；T064 的 grounded-answer
  ADR 占用 0012，本 ADR 为 0013。
- 相关：F10 / T059（GitHub #60，规划 ID F10.02）；
  分支 `t/t059-tabular-ingest`；报告 `product/reports/f10/T059-tabular-ingest.md`；
  基础：T055（#56）的解析器缝、chunker、citation jsonb、完成守卫、非契约检索/诊断面
  （ADR 0010）、迁移 `0034_knowledge.sql`；
  后续：#61/T060（PDF）、#62/T061（版本重建）、#64/T064（检索规模）、#68/T067（缓存）。
  先例：ADR 0005（非契约桥）、ADR 0007（非契约内部端点）、ADR 0010（F10 基础）。

## 背景

T055 的解析器注册表只注册 `text/plain` / `text/markdown`；CSV/XLSX 被明确留给
T059。本票要把 CSV/XLSX 作为**同一输入族**接到既有摄取链上：不新增知识状态权威、
不新增上传路径、不新增 citation 协议。票面要求：

1. 片段保留行范围，且行范围要能对封存源字节核验；
2. 公式不执行，压缩/行列超限拒绝；
3. 解析失败在页面可诊断；
4. 不同格式同一授权与引用协议；
5. 真实端到端、失败/授权/重试边界有断言。

约束与既有事实：

- 契约 `contracts/database.sql` 的 `knowledge_chunks.citation` 是 jsonb，
  `knowledge_documents` 没有失败位置列；`contracts/openapi.json` 的
  `KnowledgeDocument` 是 `additionalProperties:false` 的五字段；
- T055 只把稳定失败码写进 `knowledge_documents.error_code`（唯一写者是完成守卫），
  诊断读 `GET /knowledge-documents/{id}/diagnostics` 只返回码/版本/计数，没有位置；
- `internal/artifacts/admission.go` 已把 `text/csv` 与 XLSX 纳入上传准入
  （`purpose=knowledge` 无需新允许项），已有条目数 512、解压总量 64 MiB、路径穿越、
  宏部件、缺 workbook 的拒绝；没有压缩比、DTD、工作表/行/列/单元格上限；
- Python 预置工具 `sales_stats.py` 的表格口径（表头 1 行、数据行、行号）是本平台
  对"表"的既有语义参考，但 T059 **不导入也不调用**它。

## 决策

### 1. CSV：解析结果就是解码后的源文本，行号按逻辑记录编号

- `orchara-csv-parser/1` 只剥离一个 UTF-8 BOM（`text_normalization` 记录
  `utf8_bom_stripped`），**不做**别的规范化：CRLF、引号、字段内换行都原样保留。
  因此 `Parsed.Text` 可精确回切到封存源字节（去掉同一个 BOM 后），citation 的
  字符 span 与行号是对源文本的真实偏移。
- RFC 4180-ish 严格解析：引号包裹字段、`""` 转义、字段内逗号/换行、CRLF/LF/裸 CR
  记录分隔；空行跳过且不占行号；**逻辑记录号从 1 开始，表头是第 1 行，数据从第 2 行**。
  引号不合法（未闭合、未引字段中的引号、闭合引号后还有字符）→
  `ingest_invalid_csv`，带出错记录号。
- 单个逻辑记录即一行，行号与物理行号可能不同（字段内换行时）；citation 的
  `span.start_line/end_line` 仍是真实物理行，`row_start/row_end` 是逻辑行范围。
- 不把 CSV 中以 `=`、`+`、`-`、`@` 开头的单元格当公式：CSV 没有公式元数据，
  平台也从不执行任何内容；值是文本，原样保留（本票有测试证明
  `=SUM(...)` 仍以字面文本出现在片段中）。

### 2. XLSX：只读的 zip+XML 数据读取器，绝不执行公式

- `orchara-xlsx-parser/1` 只用标准库（`archive/zip` + `encoding/xml`），读取
  `[Content_Types].xml`、`xl/workbook.xml`、工作簿关系、`xl/sharedStrings.xml`
  与**工作簿顺序的第一个工作表**；单元格值取存储的文本（数字/布尔/错误/日期按
  存储原文），不应用数字格式、不做日期换算、不计算公式。
- **公式单元格 = 失败**：任何 `<c>` 含 `<f>` → `ingest_formula_not_supported`，
  location 带 `sheet/row/column/cell`（cell 用规范列字母+行号重建，不回显源属性）。
  即使有缓存 `<v>` 也不使用：未重算的缓存可能是过期或伪造的，平台没有验证过它。
- **DTD/实体拒绝**：读取每个要解析的 XML 部件前扫描 `<!DOCTYPE`/`<!ENTITY`，
  命中 → `ingest_dtd_not_supported`（`part` 为安全固定名）。Go 的 XML 解码器本身
  不展开 DTD 实体，这里进一步把"声明了 DTD 的文档"整类拒掉。
- 渲染：`Parsed.Text` 是本次解析的确定性 CSV 渲染（每行一条，表头在前，`\n` 结尾），
  citation 的 `text_render = "sheet_csv/1"` 明示 span 索引的是这个渲染。行号使用
  **工作表自身的行号（`r` 属性，1-based）**，因此范围可以有空洞；`row_count` 是该
  片段实际数据行数，可能小于 `row_end-row_start+1`。工作表名进入 citation
  （`sheet_name`），第二个工作表不摄取。
- 读取顺序与上限（全部在信任内容前）：zip 中央目录 → 条目数/单条与总解压量/压缩比/
  路径穿越 → 宏（部件名 + content-types 的 `macroEnabled`）→ workbook（工作表数）→
  relations/target（包内安全路径）→ shared strings（数量）→ worksheet（单元格数、
  列数、行数、单行字符数）。命中即失败，码见 §3。
- **复用既有上限**：条目数、解压总量、压缩比、路径谓词直接使用
  `internal/artifacts` 的常量与（新导出的）`SafeArchivePath`，不另立一套；T028 仍然
  是上传格式闸门。解析器 **额外** 检查压缩比（含单条目与总量）与 DTD，因为：
  (a) 封存对象可能来自绕过准入的路径（测试里用 seed 证明）；(b) 只有解析器拒绝才能
  产生"可诊断的 failed 文档"，准入拒绝只会是 quarantine 且没有文档。

### 3. 表格上限（本票固定，写入代码常量与失败码）

| 上限 | 值 | 失败码 |
|---|---|---|
| 工作表数 | 64 | `ingest_worksheet_limit_exceeded` |
| 数据行 | 10 000 | `ingest_row_limit_exceeded` |
| 列 | 64 | `ingest_column_limit_exceeded` |
| 单元格（表头+数据） | (10 000+1)×64 = 640 064 | `ingest_cell_limit_exceeded` |
| 单行字符 | 64 KiB | `ingest_row_too_large` |

行上限与可信预置工具 `sales_stats` 的 10 000 数据行口径一致；单元格上限取
"满行 × 满列"，超过它必然已经超出行或列上限，单个上限仍然可独立命中（空单元格行
不占数据行但计入单元格数）。**行永不被拆分**：片段的行范围必须描述完整行，超长行
直接失败而不是把一个行范围切成两段。

### 4. citation 的 additive 扩展（不新增协议）

`knowledge_chunks.citation` 仍是唯一溯源文档；T059 只新增字段（`omitempty`，txt/md
的既有 citation 一个字节都不变）：

```
row_start, row_end, row_count, header_row, sheet_name,
columns, columns_omitted, text_render
```

- 独立核验：CSV 用封存源字节解码（同一 BOM 规则）→ 字符切片 == 片段正文，逻辑记录
  切片 == 片段正文的记录；XLSX 用封存 zip 的 workbook/rels/sharedStrings/sheet 独立
  解析（测试用 Python 标准库另写一遍）→ 工作表名与行范围内的单元格值 == 片段正文的
  CSV 行。测试不读平台的解析结果当答案。
- `columns` 仅当表头渲染长度 ≤ chunk 上限时才重复进每个 citation；否则
  `columns_omitted=true`，表头行仍由 `header_row` 定位（不静默截断）。

### 5. 失败位置：只读派生，不新增状态权威/迁移/写路径

票面要求"解析失败在页面可诊断"，且公式失败要带行列。T055 的稳定码是唯一持久事实，
但契约和 `knowledge_documents` 都没有位置列。本票**不**新增 `error_detail` 列、不新增
表、不让 handler 写 document 状态，而是扩展 T055 已有的非契约诊断读：

- `GET /knowledge-documents/{id}/diagnostics` 新增 `detail` 对象
  `{sheet,row,column,cell,part,limit,value}`（可空）；
- `detail` 在**读取时派生**：仅当文档 `failed`、`error_code` 是解析拒绝码、
  记录的 `parser_version` 仍等于当前注册解析器版本、源 artifact 仍 ready 且可读时，
  用同一解析器重新解析同一封存字节；若得到相同 `*ParseError` 则返回其 location，
  否则返回 null。代码是持久权威，位置是其投影。
- 这样保持"只有完成守卫写 state/error_code"的边界，且不引入第二个知识状态权威；
  源被删/换版/解析器升级后 detail 为 null 是如实行为，不猜。
- 若未来需要历史位置持久化，那是 T061/T064 的数据模型决定；本 ADR 不预占。

### 6. 授权与引用：同一协议

CSV/XLSX 片段与 txt/md 完全共用：同一 KB owner-only ACL（管理员不例外，404 不泄露
存在性）、同一 SQL 先过滤再排序、同一 `GET /knowledge-citations/{chunk_id}` 每次重校验、
同一 tombstone/当前 revision 规则。没有按格式分叉的授权或 citation 代码路径。

### 7. 上传准入：无需改动（本票复核结论）

`internal/artifacts/upload.go` 的 `uploadPurposes` 含 `knowledge`；
`SupportedUploadMediaTypeForPurpose("knowledge", "text/csv"|XLSX)` 走基础 allowlist，
本就返回 true。本票只验证并记录，**没有**新增允许项、没有改准入行为。解析器的内容
上限是对同一输入族的第二道（更细的）校验，不是第二套准入。

## 后果

- 交付：CSV/XLSX 的真实用户路径（上传→封存→摄取→ready→检索→打开引用），行范围可
  对封存字节核验；公式/DTD/宏/压缩比/行列单元格超限/坏 CSV/坏 zip 都是具体码的
  failed 文档并可在页面诊断；不同格式同授权/引用。
- 无迁移、无契约改动；`python tools/sync_spec.py --check` no drift；txt/md 的 citation
  与行为不变。
- 未做/不属于本票：PDF（T060）、版本重建（T061）、删除治理（F07/T058）、检索缓存/
  规模（T064/T067）、浏览器 DOM 驱动页面（与既有页面票同一边界）。XLSX 只读第一个
  工作表，多工作表选择留待后续票。
- 已知边界：XLSX `text_render` 是平台定义的确定性渲染，不是源字节原文；行范围对
  workbook 的核验依赖记录的 `parser_version`（换解析器 = 新版本语义）。
