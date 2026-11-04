# ADR 0016：F10 PDF 摄取、页码引用与解析/分块候选对照

- 状态：已接受
- 日期：2026-11-04
- 编号：合并 `integration/f13a-f15a-f09-f10` 时 T062 已占用 ADR 0014、
  T067 已占用 ADR 0015，本 ADR 让号为 0016。
- 相关：F10 / T060（GitHub #61，规划 ID F10.02 之后的 PDF 切片）；
  分支 `t/t060-pdf-ingest`；报告 `product/reports/f10/T060-pdf-ingest.md`；
  基础：T055（#56）的解析器缝 / chunker / citation / 完成守卫 / 非契约检索与诊断面
  （ADR 0010）、T059（#60）的表格输入族与 citation additive 字段（ADR 0013）、
  迁移 `0034_knowledge.sql`；本票新增 `0038_pdf_admission.sql`；
  后续：#62/T061（版本重建）、#64/T064（检索规模）、#68/T067（缓存）。
  先例：ADR 0005/0007（非契约面）、ADR 0010/0013（F10 输入族扩展）。

## 背景

票面要求：普通文本 PDF 上传后得到**页码 + 来源片段**；扫描件/无文字层文件不能冒充 OCR
成功；基线与 Docling Native/分块候选做真实质量与资源对照，无收益可淘汰；记录版本、输入
hash、命令、原始结果与环境 profile。约束与既有事实：

- `contracts/openapi.json` 没有 PDF 专属操作，`contracts/database.sql` 的
  `knowledge_chunks.citation` 是 jsonb，允许 additive 字段；
- `internal/artifacts` 的上传准入此前只把 `text/plain`/`text/csv`/JSON/XLSX（及
  `purpose=knowledge` 的 markdown、`purpose=tool_source` 的 zip）当作可声明类型，
  `application/pdf` 一律拒绝；`platform.artifact_admissions.detected_format` 的 CHECK
  是闭合词表，没有 `pdf`；
- 平台没有任何 OCR 组件，也不承诺 OCR；环境预装 `pypdf`、`pdfminer.six`、`PyMuPDF`，
  **没有** Docling / sentence splitter；
- Go 侧没有 PDF 解析器。生产 handler 从不执行 shell（T055 注释），因此不能调用 Python。

## 决策

### 1. 准入：`application/pdf` 仅对 `purpose=knowledge`，魔数按 `%PDF-` 精确判定

- `artifacts.PDFMediaType` + `FormatPDF = "pdf"`；`SupportedUploadMediaTypeForPurpose`
  只在 `purpose=knowledge` 时接受该声明，其它 purpose 保持原来的 unsupported 拒绝。
  字节上限沿用既有 `MaxUploadBytes = 20 MiB`，未放宽。
- 完成时的 sniff 要求内容**以** `%PDF-` 开头；只有声明、没有魔数的内容
  （文本/zip/中间含 `%PDF-`）都是 `content_type_mismatch`，不猜格式。准入只验证魔数，
  **不**解析、**不** OCR：扫描件也是合法 PDF，由解析器以 `pdf_no_text_layer` 失败。
- 迁移 `0038_pdf_admission.sql`：`platform.artifact_admissions` 是 T028 的非契约表
  （0015，契约文件未定义），其 `detected_format` CHECK 词表增加 `'pdf'`。只加词表，
  声明仍按 purpose 收窄；`orchara_worker`/PUBLIC 零授权不变。

### 2. 解析器：`orchara-pdf-parser/1`，逐页文本 + 页码范围

- 新增 Go 依赖 **`rsc.io/pdf v0.1.1`（BSD-3-Clause）**，只用于容器解析（xref/stream/
  对象/字体编码）。文本重建由本票的 `pdfTextState` 完成：按内容流文本算子
  （`Tj`/`TJ`/`'`/`"`，`Td`/`TD`/`T*`/`Tm`/`TL`）维护文本矩阵，基线变化开新行、
  超过 1.7×字号记录空行（段落分隔）、同一行两个 show-text 之间超过 ¼ em 记录空格、
  `TJ` 大负调整也记录空格；字体编码走 `Font.Encoder()`（WinAnsi/MacRoman/pdfDoc/
  ToUnicode CMap），Form XObject 递归至深度 4。
- `Parsed.Text` 是**确定性页文本拼接**（页间 `\n\n`），`text_render = "pdf_pages/1"`；
  `Parsed.PDF` 记录每页 rune 偏移 `[StartChar, EndChar)` 与 `EmptyPages`。citation 的
  `span` 仍是该拼接文本的全局 rune 偏移/行号；页内偏移单列。
- 支持范围（如实）：经典 xref 与 xref stream、FlateDecode 内容流、简单字体与
  ToUnicode 编码、Form XObject；**不支持** OCR、加密 PDF（`pdf_encrypted`）、
  结构损坏（`pdf_invalid`）。Type0/CID 字体的宽度表近似（编码仍经 ToUnicode 解码），
  这是已知边界。解析库 panic 被 recover 成 `pdf_invalid`，绝不是进程崩溃或空成功。

### 3. 分块器：`orchara-pdf-page-paragraph-chunker/1`，块不跨页

- 复用 T055 的段落打包引擎（段落 = 非空行连续段；≤ `MaxChunkChars=1200` rune 打包；
  超长段落 1200/200 重叠硬切、词边界对齐），但**逐页**运行：一个块永远属于一页。
  段落跨页时按页各引一次，换取精确页码；这是与普通段落 chunker 不同的算法，故是
  新 revision。块正文仍是独立封存 artifact（T055 既有流水线）。
- citation 新增**一个 additive 对象**（其它族一个字节不变，`omitempty`）：
  ```
  pdf: {page_start, page_end, page_char_start, page_char_end,
        page_count, pages_without_text?}
  ```
  用嵌套对象而不是平铺字段，是因为 `page_char_start` 可以为 0，平铺 `omitempty` 会
  丢掉合法的 0；`pages_without_text` 让部分文字层的文档在每个片段上都如实记录空页，
  不会把“2/3 页有字”说成全文覆盖。页码 1-based，`page_start = page_end`（本 chunker）。

### 4. 无文字层 = 可诊断失败，绝不 OCR 成功

- 文档级门槛：**32 个非空白 rune**（`MinPDFTextRunes`，代码常量 + 报告 + 夹具
  reference 三处一致）。低于门槛（含 0，如整页图片）→
  `ParseError{pdf_no_text_layer, detail:{part:"text_layer", limit:"min_text_runes=32",
  value:N}}`，完成守卫把文档置 `failed`，0 个 chunk、检索不可见；重试仍是同一失败。
- 图片-only 页在**部分**文字层文档里不会让整篇失败：该页不产生 chunk，页码进入
  `pdf.pages_without_text`（部分文字层如实记录）；整篇文字量 ≥ 门槛才 ready。
- 失败码 `pdf_no_text_layer` / `pdf_invalid` / `pdf_encrypted` 进入
  `parseFailureCodes`，诊断读按 T059 的只读派生规则重放同一封存字节得到 detail
  （part/limit/value），不新增状态权威、不新增写路径。

### 5. 授权/引用：同一协议，不按格式分叉

PDF chunk 与 txt/md/csv/xlsx 共用 owner-only ACL、SQL 先过滤后排序、每次
`GET /knowledge-citations/{id}` 重校验当前 revision/body artifact；他人/管理员/撤销
一律同一 404。

### 6. 解析/分块候选对照：基线保留，候选只做实验，不进入产品

- 基线：本票注册的 Go parser + page chunker，经 `probes/f10_pdf_compare/go_baseline`
  以真实 `ParserFor`/`ChunkParsed` 调用，冻结夹具上度量覆盖率/页码归属/块形状/墙钟/峰值 RSS。
- 候选 A：Docling Native + Docling HierarchicalChunker。安装/运行在**隔离 venv**
  （`--system-site-packages`，`attempt_docling.py` 可重放）内进行：pip exit 0/306.5 s，
  Docling 2.146.0 + docling-core 2.105.0 + docling-parse 7.24.2 + torch 2.15.0(cpu)，
  venv 813.8 MB、模型缓存 505.4 MB。转换真实执行（worker 97.6 s，峰值 RSS 1.89 GB），
  pip 原始输出与逐夹具结果记录在报告 §5。宿主的 `NO_PROXY=...,[::1]` 会让
  huggingface/httpx 报 `Invalid port`，重跑时用合法 `NO_PROXY` 并在 attempt JSON 记录该
  适配；这不是伪造的通过。若转换无法执行则记 `blocked`，不伪造数字。
- 候选 B：环境已有的 PyMuPDF 提取 + 本票写的 sentence packer，**明确标注为
  substitute，不是 Docling**；另加 pypdf/pdfminer 两行提取对照（共用同一 chunker）。
- 结论（实测）：四个候选在冻结文本夹具上都没有严格质量收益（`strict_quality_gain =
  false`；pdfminer 布局页 coverage 0.5417、page-exact 0/1，其余候选 coverage 1.0、
  page-exact 与基线相同、页码归属 1.0）。成本：基线整批 5 夹具进程墙钟 0.107 s、
  峰值 RSS 14.58 MB；Docling 单夹具 4–20 s、RSS 1.89 GB；PyMuPDF RSS 44.27 MB
  （**AGPL-3.0**）、pypdf 27.39 MB、pdfminer 26.29 MB 且慢数百倍。替代 chunker 还会把
  27 字符短文本扫描页当作可检索块，而平台的 32 rune 门槛会拒绝。因此**不引入第二套生产
  解析器**，Docling 也不进入依赖；候选只保留为可重放实验入口。若未来要采用某个候选，
  必须是新的 parser revision + 新 citation 语义 + 新的安全/OCR 边界决定，不能静默替换
  `orchara-pdf-parser/1`。

## 后果

- 交付：普通文本 PDF 的真实用户路径（上传→封存→摄取→ready→检索→按页打开引用）；
  扫描件/无文字层/损坏/加密 PDF 都是具体失败码的可诊断 failed 文档；部分文字层如实记录
  空页；`/knowledge` 页面支持 PDF 上传与页码展示。
- 迁移只新增 `0038_pdf_admission.sql`；`contracts/**`、`docs/spec/**` 未改，
  `sync_spec --check` 保持 no drift；txt/md/csv/xlsx 的 citation 与行为不变。
- 未做/不属于本票：OCR、加密 PDF 解密、Type0 宽度精确排版、版本重建（T061）、
  删除治理（F07/T058）、检索规模/缓存（T064/T067）、浏览器 DOM 驱动页面。
- 已知边界：`pages_without_text` 在每个 PDF citation 上重复（20 MiB 输入上限 +
  `MaxChunksPerDocument=1000` 约束其规模；若未来出现大量空页的合法文档，应改为文档级
  事实而不是每块重复，这是新的数据模型决定）。
