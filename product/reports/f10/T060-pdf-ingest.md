# F10 T060 验收：摄取普通文本 PDF 并对比解析分块方案

- 任务：F10 / GitHub Issue #61（票 `T060`），分支 `t/t060-pdf-ingest`
  （worktree `E:\orchara-worktrees\t060`，基线 integration tip `ddaeb46`）；
  父门禁 #53–#68 集成批次。
- 合并：`integration/f13a-f15a-f09-f10` tip `c7d7e21`（T062）已合入本分支，
  merge commit **`9aa2982`**；T062 已占用 ADR 0014，本票 ADR 让号为 **0015**；
  迁移 `0037` 在集成 tip 上未冲突；合并后目标门禁复跑见 §3 末尾。
- 状态：**`passed`**（本票范围 = 在 T055 摄取链上新增 **普通文本 PDF 一种输入族**：
  `purpose=knowledge` 的 `application/pdf` 上传准入 + 魔数嗅探、逐页文本解析与页码
  citation、页内分块、无文字层/损坏/加密 PDF 的可诊断 failed 文档、部分文字层的空页
  如实记录、`/knowledge` 页面 PDF 上传与页码展示，以及基线与 Docling/可用替代方案的
  **真实测量对照**。真实 PostgreSQL 16.4 + 真实编译二进制 + 真实 HTTP/mTLS + 真实封存
  对象字节 + 真实 maintenance 双池进程 + Node 执行页面逻辑；无 mock、无跳过。）
- 边界（如实）：**OCR 不做也不承诺**——扫描件/无文字层是 `pdf_no_text_layer` 失败，
  不是空索引成功；加密 PDF 不解密（`pdf_encrypted`）；PDF 解析只覆盖普通文本 PDF
  （简单字体/ToUnicode、FlateDecode、经典 xref 与 xref stream、Form XObject；
  Type0/CID 宽度为近似值）。真实 embedding provider **blocked**（继承 T055：无凭据/
  adapter，fail closed）；浏览器 DOM 驱动页面 **not_run**（与既有页面票同一边界）；
  版本重建（#62/T061）、删除治理（F07/T058）、检索规模/缓存（#64/#68）不在本票；
  全量 `pytest tests` 按 BRIEF §3 不在本票运行（≈67 分钟，orchestrator 负责）。
- 契约偏差：新增迁移 `0037_pdf_admission.sql`，只扩展 T028 **非契约**表
  `platform.artifact_admissions.detected_format` 的闭合词表（+`pdf`）；
  `citation` 的 `pdf` 对象是 jsonb additive 字段；新增 Go 依赖 `rsc.io/pdf v0.1.1`
  （BSD-3-Clause）。均记录在 ADR 0015；`docs/spec/**`、`contracts/**` 未改，
  `sync_spec --check` no drift。

## 0. 工作记录（简短）

1. 读 `BRIEF.md`、`briefs/T060.md`、票面 #61、T055/T059 报告与 ADR 0010/0013、
   `internal/knowledge`（Parser/Parsed/Chunk/Span/Citation、handler 映射、完成守卫、
   诊断 detail）、`internal/artifacts/{admission.go,upload.go}`（准入、20 MiB 上限、
   `artifact_admissions` 落库）、`0034`/`0015` 迁移、`tests/f10` + `tests/support/t055/t059`。
2. TDD 垂直切片（每片先红后绿）：
   - 准入：`application/pdf` purpose-scoped + `%PDF-` 魔数 + 原有 20 MiB（Go 单测）；
   - 夹具：确定性 PDF 生成器（5 个夹具）+ 手写页码 reference（渲染图逐页目检）；
   - 解析器：`rsc.io/pdf` 容器 + 自写文本重建（页文本、空页、阈值、损坏/加密拒绝）
     → Go 单测；
   - 分块：页内段落 chunker + 页码/页内偏移 + citation `pdf` 对象 → Go 单测；
   - handler/诊断：citation 写 pdf 字段、失败码进入 parseFailureCodes、detail 派生；
   - 真实栈：上传→封存→摄取→ready→检索→按页打开 citation 的独立真值；部分文字层；
     扫描/短文本/损坏/加密失败矩阵；ACL/撤销/重试边界；页面结构 + Node 执行 helper；
   - 对照：Go 基线 probe + Docling 隔离安装/真实运行 + PyMuPDF/pypdf/pdfminer 替代，
     冻结夹具上度量覆盖率/页码归属/块形状/墙钟/峰值 RSS，按“无收益即淘汰”给结论。
3. 跑门禁（§3），两次连跑 `tests/f10`；写本报告/PROGRESS/ADR 0015；按 feat/test/docs
   提交；合并 integration 后复跑；未 push、未关 issue、未改 `docs/spec/**`/`contracts/**`；
   迁移只新增 `0037`。

## 1. 交付内容

### 1.1 上传准入：`application/pdf` 仅限 `purpose=knowledge`

- `internal/artifacts/admission.go`：`FormatPDF = "pdf"`、`PDFMediaType`、
  `SupportedUploadMediaTypeForPurpose("knowledge", "application/pdf") -> FormatPDF`；
  其它 purpose（task_input/tool_source/evaluation_dataset）继续拒绝；基础 allowlist
  （无 purpose 的 `SupportedUploadMediaType`）保持关闭。
- 完成 sniff：内容必须以 `%PDF-` 开头才算 pdf；文本/zip/中间含 magic 的内容是
  `content_type_mismatch`；空内容 `empty_content`。准入**只**验魔数，不解析/不 OCR：
  扫描件也先被接收，再由解析器 `pdf_no_text_layer` 失败。字节上限沿用
  `MaxUploadBytes = 20 MiB`，未放宽。
- 迁移 `0037_pdf_admission.sql`：`platform.artifact_admissions.detected_format` CHECK
  词表 `text,csv,json,xlsx,zip,pdf,binary,unknown`（T028 表是 0015 的非契约偏差；
  `contracts/database.sql` 未定义该表）。重复 migrate no-op；worker/PUBLIC 零授权不变。

### 1.2 解析器 `orchara-pdf-parser/1`：逐页文本 + 页映射

- `internal/knowledge/pdf.go`：`rsc.io/pdf v0.1.1` 负责 PDF 容器（xref/对象/stream/
  字体编码），本票的 `pdfTextState` 负责文本重建：
  `Tj`/`TJ`/`'`/`"` + `Td`/`TD`/`T*`/`Tm`/`TL` 维护文本矩阵；基线变化开新行，
  竖直间距 > 1.7×字号记录空行（段落分隔），同行两个 show-text 间距 > ¼ em 记录空格，
  `TJ` 大负调整记录空格；字体解码走 `Font.Encoder()`（WinAnsi/MacRoman/pdfDoc/
  ToUnicode CMap）；Form XObject 递归（深度 4）。
- `Parsed.Text` 是确定性页文本拼接（页间 `\n\n`），`text_render = "pdf_pages/1"`；
  `Parsed.PDF` 记录 `PageCount`、每页 rune span `[StartChar, EndChar)` 与
  `EmptyPages`。citation 的 `span` 仍是该拼接文本的全局 rune 偏移/行号。
- 失败：结构损坏/解析 panic → `pdf_invalid`（part=xref/structure/page/header）；
  声明 `/Encrypt`（或 NewReader 因密码失败）→ `pdf_encrypted`（不解密）；
  非 PDF 头 → `pdf_invalid(header)`。
- 支持边界（ADR 0015 §2）：不支持 OCR、解密、Type0/CID 精确宽度；这些不是静默错误，
  除宽度近似外都以具体码失败。

### 1.3 分块器 `orchara-pdf-page-paragraph-chunker/1` 与 citation

- 复用 T055 段落打包引擎（≤1200 rune、超长段落 1200/200 重叠硬切、词边界、span 裁剪
  后重算），但**逐页**运行：块永不跨页，页码是精确值而不是推断；段落跨页时按页各引一次。
- `citation` 新增一个 additive 对象（其它输入族 byte-for-byte 不变，`omitempty`）：
  ```json
  "pdf": {
    "page_start": 2, "page_end": 2,
    "page_char_start": 0, "page_char_end": 129,
    "page_count": 3,
    "pages_without_text": [2]
  }
  ```
  嵌套对象而非平铺：`page_char_start` 合法为 0，平铺 `omitempty` 会丢字段。
- 每 chunk 正文仍是既有 upload→complete→finalize 路径产生的独立封存 artifact
  （purpose `knowledge_chunk`），完成守卫未改。

### 1.4 无文字层/部分文字层：可诊断失败，不冒充 OCR

- `MinPDFTextRunes = 32`（全文档非空白 rune；代码常量 + reference JSON + 报告三处一致）。
  低于门槛（含 0）→ `ParseError{pdf_no_text_layer, detail:{part:"text_layer",
  limit:"min_text_runes=32", value:N}}`；文档 `failed`、0 chunk、检索不可见，
  重试仍是同一失败。
- 图片-only 页在部分文字层文档中不产生 chunk，页码进入 `pages_without_text`；
  整篇文字量 ≥ 门槛才 ready。扫描件、短文本、损坏、加密均不会被当作 OCR 成功。
- 新失败码进入 `parseFailureCodes`，诊断读按 T059 的只读派生规则重放同一封存字节得到
  detail；不新增状态表/列/写路径。

### 1.5 页面

`apps/web/src/{knowledge.html,assets/knowledge.js}`：上传 accept/label/扩展名映射加入
`.pdf`→`application/pdf`；`formatPageRange` 渲染“第 N 页（共 M 页）· 空白页 …”；
`describeChunk` 把 pdf 页范围纳入 citation 一致性检查（页号/页内范围不合法即
“引用不一致”）；新增 `pdf_no_text_layer`/`pdf_invalid`/`pdf_encrypted` 文案。
页面仍用 `textContent`，无浏览器存储/object key。

### 1.6 夹具与手写 reference

`tests/fixtures/f10/build_pdf_fixtures.py` 手写确定性 PDF（经典 xref、FlateDecode、
Helvetica WinAnsi + Widths、无 Producer 日期），输出 5 个夹具与
`t060_reference.json`（sha256/字节数/页数/逐页文本/空页）：

| 夹具 | 字节 | sha256 | 页 | 说明 |
|---|---|---|---|---|
| t060_plain.pdf | 1 987 | `21907eb06c072d2682c1c0dad7f69c266e4fd08d1d61e90175c5655ad46f2109` | 3 | 普通文本 3 页（含一个段落空行） |
| t060_layout.pdf | 1 192 | `e81e076cc714a8774817ef3d25ad624931c97e277a83523263ad11c6017b1d4f` | 1 | 三列表格状布局（每行三个文本位置） |
| t060_scanned.pdf | 2 199 | `ddeb26336fc626abefc8487d9a0f5f9efaa0893a2680e34ab0b943e33d0dc770` | 1 | 整页 image XObject，无文本算子 |
| t060_partial.pdf | 2 938 | `2d5b7793a9f21028a89320367f3a6b7f0370326cbfb980199828aa4ea0768bbc` | 3 | 第 2 页 image-only，1/3 页有文字 |
| t060_short.pdf | 1 097 | `1cbaf751412fd9cb663353579b5285e84cccb6c7b71741b30d8ce25aa743a463` | 1 | 27 字符短文本层（23 非空白 < 32 门槛） |

- reference 的逐页文本是**作者页文本**，不是平台解析结果；T060 用 pypdfium2 渲染每页
  PNG 并逐页目检（普通页文字/布局页三列/扫描页无文字），生成器可重放且两次生成字节一致。
- 独立第三方校验：`pypdf 6.9.1`、`pdfminer.six 20251230`、`PyMuPDF 1.27.2.3` 都能打开
  这些夹具；扫描页在所有库中都是空文本（见 §6）。

## 2. AC 逐条

| # | 验收标准 | 结果 | 证据 |
|---|---|---|---|
| 1 | 普通 PDF 片段可核验页码/来源 | **proven** | `test_t060_pdf.py::test_plain_pdf_pages_become_ready_with_page_verifiable_citations`：真实上传/封存/摄取/ready；每个 chunk 的 `pdf.page_start/page_end/page_char_start/page_char_end/page_count` 对 committed reference 页文本切片 == 封存 chunk body；全局 span 对 reference 页拼接切片 == body 且行号一致；源 sha256/citation 字段逐项断言；检索项 citation == DB citation；`GET knowledge-citations` 重开一致。Go 单测 `TestPDFParserExtractsEveryPageWithItsOwnSpan`、`TestPDFChunkerKeepsEachChunkOnOnePageAndSlicesThePageText` |
| 2 | 基线与 Docling Native/分块候选有实际质量资源对照 | **proven** | §5/§6 + `reports/f10/t060-comparison.json` + `probes/f10_pdf_compare/`：Go 基线（真实注册 parser/chunker）、Docling 2.146.0 Native+HierarchicalChunker（隔离 venv 真跑）、PyMuPDF/pypdf/pdfminer 替代；每候选每夹具 coverage/page-exact/attribution/chunk 形状/墙钟/峰值 RSS；`tests/f10/test_t060_comparison.py` 现场重跑基线+PyMuPDF 并断言测量属性 |
| 3 | 无收益可淘汰候选 | **proven** | §6 表 + comparison JSON `eliminations`：Docling/PyMuPDF/pypdf/pdfminer 在冻结文本夹具上 coverage/page-exact/attribution 均不优于基线（pdfminer 布局页 0.5417 更差），成本更高；`test_t060_comparison.py` 断言 `strict_quality_gain == false` 与 elimination 文案 |
| 4 | 扫描件不冒充 OCR 成功 | **proven** | `test_t060_pdf.py::test_scanned_and_short_pdfs_fail_as_pdf_no_text_layer_never_ready`：两个 artifact 的准入均为 accepted，文档 failed `pdf_no_text_layer`、0 chunk、检索空、重试仍 failed（revision 2）；诊断 detail part/limit/value；短文本记录实测 23 < 32。部分文字层 `test_a_partial_text_layer_records_the_image_only_page_honestly`：`pages_without_text=[2]`、页 2 无 chunk |
| 5 | 真实端到端入口；失败/授权/重试边界有断言，无单层 mock | **proven** | 所有用例经真实 `POST /artifacts/uploads`→签名 PUT→complete→真实 finalize 准入→`ingestDocument`→真实 maintenance 双池→ready/failed；`test_pdf_chunks_use_the_same_acl_and_authorization_protocol_as_text`（他人/管理员 404、撤销 PDF 后检索消失+citation 404、txt 仍可读）；`test_invalid_and_encrypted_pdfs_fail_with_their_own_codes`；`test_pdf_upload_admission_is_knowledge_scoped_and_sniffs_the_bytes`（task_input 声明 422、假 PDF 字节 quarantined content_type_mismatch）；页面结构 + Node 执行 helper |
| 6 | 记录版本、输入/hash、命令、原始结果与 profile；stub/real 分报，缺条件 blocked、未运行 not_run | **proven（含显式 blocked/not_run）** | §3–§7：夹具 hash、Go/Python/Node/PG/OS 版本、命令与原始输出、Docling pip 原始日志（`reports/f10/t060-docling-pip.log`）、comparison JSON；真实 embedding provider blocked；浏览器 DOM、全量 `pytest tests` not_run |

## 3. 门禁命令与原始输出

环境：Windows PowerShell；Go 1.27.3（`E:\orchara-toolchains\go\bin`）、Python 3.12.2、
Node v24.13.0、PostgreSQL 16.4（`127.0.0.1:55432`）、PyMuPDF 1.27.2.3、pypdf 6.9.1、
pdfminer.six 20251230、Docling 2.146.0（隔离 venv，§5）；`product/.env.local` 提供本地
口令，报告不含任何凭据。

```text
$ python -m pytest tests/f10 -q          # 第 1 次
......................................................                   [100%]
54 passed in 591.50s (0:09:51)

$ python -m pytest tests/f10 -q          # 第 2 次（紧接第 1 次）
......................................................                   [100%]
54 passed in 661.66s (0:11:01)

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 13.29s

$ go build ./...
(exit 0, no output)

$ go vet ./...
(exit 0, no output)

$ go test ./...
# 33 个包 ok / 0 FAIL（含 internal/knowledge、artifacts、background、api；
# probes/f10_pdf_compare/go_baseline 为无测试文件的真实编译入口）
(exit 0)

$ gofmt -l .
(empty)

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest

$ python probes/f10_pdf_compare/run_comparison.py --json-out ..\.scratch\cmp-recheck.json --runs 2
（完整表格见 §6；本次复跑的尾部：）
docling: measured — worker 97.6s, peak RSS 1890.29 MB,
  packages {'docling': '2.146.0', 'docling-core': '2.105.0', 'docling-parse': '7.24.2', 'torch': '2.15.0'}
decision candidate-b-pymupdf-sentence: eliminated: no measured strict quality gain ...
decision candidate-c-pypdf-sentence: eliminated: no measured strict quality gain ...
decision candidate-d-pdfminer-sentence: eliminated: no measured strict quality gain ...
decision docling-native: eliminated: no measured strict quality gain ...
```

> 并发/时序说明（BRIEF §6.1）：本批次多个套件在同一 12 核笔记本上并发使用同一个
> PostgreSQL 集群。`ALTER ROLE orchara_worker PASSWORD` 是集群级 catalog 写；并发时
> PostgreSQL 会返回瞬时 `tuple concurrently updated`（运行中实际遇到 1 次，属环境并发，
> 不是断言失败）。本票在 `tests/support/stack.py::set_worker_password` 对该**特定**瞬时
> 错误做最多 5 次、0.2–1.0 s 退避重试（每次写入的值相同，未放宽任何断言）；其余等待
> 窗口均 ≥30 s（文档状态 60–90 s、artifact 30 s），没有 <5 s 的时序断言，也没有
> “短睡眠后断言无副作用”的检查。

合并 `integration/f13a-f15a-f09-f10`（tip `c7d7e21`，含 T062；merge commit
`9aa2982`）后复跑目标门禁（T062 只与本票的 `PROGRESS.md`/ADR 编号相交；因 T062 已占
ADR 0014，本票 ADR 让号为 0015）：

```text
$ python -m pytest tests/f10 -q          # merge 9aa2982 之后
......................................................                   [100%]
54 passed in 708.85s (0:11:48)

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 8.53s

$ go build ./... ; go vet ./... ; go test ./... ; gofmt -l .
(exit 0；33 个包 ok / 0 FAIL；gofmt 空)

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

相邻套回归（本票改到上传准入、`artifact_admissions` 词表、`document_ingest` handler、
knowledge citation/页面；T028 的“未支持声明”例子从 PDF 改成 msword）：

```text
$ python -m pytest tests/f05/test_t028_quarantine.py tests/f05/test_t028_upload_flow.py \
    tests/f00b/test_t024_artifacts.py tests/f00b/test_t022_finalize.py -q
......................................                                   [100%]
38 passed in 473.69s (0:07:53)
```

> 全量 `pytest tests`、`tests/f05`/`tests/f00b` 整包按 BRIEF §3 不在本票运行
> （orchestrator 的合并门禁负责），故不写成“全绿”。


## 4. 真实栈、输入与版本

- 真实 PG 16.4（每个用例新建临时库，真实 `cmd/admin migrate` 0001→0037；迁移链连续，
  重跑 migrate no-op）；真实 `cmd/api` 二进制（HMAC 签名链接、文件系统 `staged/`/
  `sealed/` 字节、internal mTLS）；真实 `cmd/maintenance` 进程（work 池
  `document_ingest`，system_io 池 `artifact_finalize`，2+2 slot）。
- 输入/哈希：§1.6 的 5 个提交夹具 + reference；失败夹具（损坏/加密声明）由
  `tests/support/t060.py` 确定性构造（对合法夹具替换/截断），不下载、无凭据。
- 版本：`orchara-pdf-parser/1`、`orchara-pdf-page-paragraph-chunker/1`、
  `local-hash-v1`（384 维，继承 T055）、`rsc.io/pdf v0.1.1`（BSD-3-Clause）。
- 原始结果：pytest/Go/Node 输出见 §3；maintenance 真实进程日志由 `tests/support/t055.py`
  写入 pytest 临时目录（`maintenance-t060-*.log`），本报告不复述日志正文。

## 5. Docling 隔离安装与运行（原始证据）

候选 A = **Docling Native（`DocumentConverter`）+ Docling `HierarchicalChunker`**，
在独立 venv 中安装与运行，产品依赖/测试锁/`PYTHONPATH` 均未受影响：

```text
# 创建隔离环境（--system-site-packages 复用已装的全局包，避免重复下载 torch）
python -m venv --system-site-packages E:\orchara-worktrees\t060\.scratch\docling-venv
# 安装（原始 pip 输出全文已提交：product/reports/f10/t060-docling-pip.log）
E:\orchara-worktrees\t060\.scratch\docling-venv\Scripts\python.exe -m pip install \
    --disable-pip-version-check --no-input docling
# 结果：exit 0，elapsed 306.5s；docling 2.146.0 / docling-core 2.105.0 /
#       docling-parse 7.24.2 / torch 2.15.0+cpu；venv 813.8 MB，
#       Docling 模型缓存 505.4 MB（docling-layout-heron 163.8 MB +
#       docling-models 341.6 MB）。
# 记录到的依赖告警（未阻断）：instructor 1.15.1 requires openai<3.0.0,>=2.0.0,
#       but you have openai 1.109.1（全局包）。
```

第一次转换失败与修复（如实记录）：宿主环境同时存在 `NO_PROXY`/`no_proxy`，值为
`...,::1,[::1]`；`huggingface_hub` 的 httpx 客户端在下载模型前解析 `[::1]` 报
`httpx.InvalidURL: Invalid port: ':1]'`。`attempt_docling.py` 用
`NO_PROXY=localhost,127.0.0.1` 的**合法**环境重启 worker 后转换成功（该适配记录在
`docling_attempt.json.env_adaptation`，不是伪造的通过）。

```text
$ <isolated-python> probes/f10_pdf_compare/docling_worker.py --out docling_attempt.worker.json
docling measured          # 5 个夹具全部真实转换 + HierarchicalChunker
worker elapsed 97.6s（模型已缓存；首次含下载 181.0s）
peak RSS 1890.29 MB
```

`probes/f10_pdf_compare/docling_attempt.json` 记录了命令、pip exit/耗时、venv、pip log、
包版本、每个夹具的 wall/coverage/page-exact/attribution/chunk 数与 `pages_without_text`。
**结论：Docling 在本环境可运行（不是 blocked），但其质量与基线打平、资源成本高两个
数量级，因此被淘汰**（§6）。

## 6. 解析/分块候选对照（真实测量）

入口（可重放）：`python probes/f10_pdf_compare/run_comparison.py --runs 5 --json-out
reports/f10/t060-comparison.json`。基线通过编译的 Go probe 调用**真实注册接口**
`knowledge.ParserFor("application/pdf")` → `Parse` → `ChunkParsed`；Docling 用隔离 venv；
PyMuPDF/pypdf/pdfminer 作为明确标注的 **available substitute（不是 Docling）**。
质量真值是 §1.6 的 committed reference；冻结输入 sha256 由 harness 再次核对。

覆盖/页码归属（5 次运行的中位耗时；RSS 为子进程峰值工作集中位数/记录值）：

| 夹具 | 候选 | coverage | page-exact | attribution | chunks | compute ms | RSS MB |
|---|---|---|---|---|---|---|---|
| plain | baseline | 1.00 | 3/3 | 1.00 | 3 | 0.743 | 14.58 |
| plain | PyMuPDF+B | 1.00 | 3/3 | 1.00 | 3 | 7.677 | 44.27 |
| plain | pypdf+B | 1.00 | 3/3 | 1.00 | 3 | 5.335 | 27.39 |
| plain | pdfminer+B | 1.00 | 3/3 | 1.00 | 3 | 3169.52 | 26.29 |
| plain | Docling | 1.00 | 3/3 | 1.00 | 4 | 19 574 | 1890.29 |
| layout | baseline | 1.00 | 1/1 | 1.00 | 1 | 0.215 | 14.58 |
| layout | PyMuPDF+B | 1.00 | 1/1 | 1.00 | 1 | 4.685 | 44.27 |
| layout | pypdf+B | 1.00 | 1/1 | 1.00 | 1 | 5.931 | 27.39 |
| layout | pdfminer+B | **0.5417** | **0/1** | 1.00 | 1 | 1902.06 | 26.29 |
| layout | Docling | 1.00 | 1/1 | 1.00 | 1 | 3 982 | 1890.29 |
| scanned | baseline | 1.00 | 1/1 | — | 0 | 0.081 | 14.58 |
| scanned | PyMuPDF+B | 1.00 | 1/1 | — | 0 | 3.422 | 44.27 |
| scanned | Docling | 1.00 | 1/1 | — | 0 | 13 318 | 1890.29 |
| partial | baseline | 1.00 | 3/3 | 1.00 | 2 | 0.420 | 14.58 |
| partial | PyMuPDF+B | 1.00 | 3/3 | 1.00 | 2 | 5.771 | 44.27 |
| partial | Docling | 1.00 | 3/3 | 1.00 | 2 | 14 377 | 1890.29 |
| short | baseline | **0.00 / failed** | 0/1 | — | 0 | 0.221 | 14.58 |
| short | PyMuPDF+B | 1.00 | 1/1 | 1.00 | 1 | 4.399 | 44.27 |
| short | Docling | 1.00 | 1/1 | 1.00 | 1 | 4 806 | 1890.29 |

进程级：基线的整批 5 夹具 wall 中位 0.107 s；PyMuPDF 1.103 s、pypdf 1.694 s、
pdfminer 16.493 s；Docling worker 97.6 s（且安装 306.5 s + 模型 505 MB）。
块大小：基线 plain 129–186 字符、layout 99、partial 72–91；Docling plain 67–134
（4 块，切得更碎）、layout 99、partial 72–91；替代方案与基线块形状基本一致。

**淘汰结论（票面允许）**：四个候选在冻结文本夹具上都没有严格质量收益
（`strict_quality_gain = false`）；pdfminer 还在布局页丢失列顺序（0.5417/0-of-1）。
Docling 质量打平但单夹具 4–20 s、峰值 RSS 1.89 GB、安装 306 s/模型 505 MB；PyMuPDF
RSS 44 MB、pypdf/pdfminer 26–27 MB 且都慢于基线上百倍。替代方案的 chunker 也不具备
平台的无文字层策略：`short` 夹具上基线与平台均拒绝，而替代方案会把它当 1 个可检索块。
因此**保留注册基线**，Docling/替代方案仅保留为可重放实验入口，不进入产品依赖。
许可证成本另记：PyMuPDF 为 **AGPL-3.0**（生产采用需评估），pypdf BSD-3-Clause、
pdfminer.six MIT、Docling MIT（其 torch 等依赖各自许可证）。

## 7. stub / real、blocked、not_run（分开记录）

| 项 | 结论 | 具体条件 |
|---|---|---|
| PDF 解析/分块/citation | **real executed** | 真实封存字节 + 真实 handler + committed reference 独立真值；无 mock |
| 扫描件/短文本/损坏/加密拒绝 | **real executed** | 真实字节；失败码/0 chunk/诊断 detail/重试仍失败逐项断言 |
| 上传准入（purpose-scoped pdf） | **real executed** | Go 单测 + 真实 HTTP（task_input 422、假 PDF quarantined content_type_mismatch、真 PDF accepted） |
| Docling 候选 | **real executed（measured）** | 隔离 venv 安装 exit 0、真实转换 5 夹具、原始 pip 日志提交；转换质量与资源数字见 §5/§6 |
| PyMuPDF/pypdf/pdfminer 候选 | **real executed（available substitute，非 Docling）** | 同一冻结夹具、同一 sentence chunker；数字见 §6 |
| 真实 embedding provider | **blocked** | 继承 T055：无凭据/构建无 adapter；handler/HTTP fail closed，从不回退本地向量 |
| OCR | **not_run / 明确不做** | 平台无 OCR 组件，票面不扩展 OCR 承诺；扫描件固定 `pdf_no_text_layer` |
| Type0/CID 精确宽度与复杂排版 | **not_run / 已知边界** | 普通文本 PDF 子集；编码仍经 ToUnicode，但宽度为近似；复杂多栏/旋转文本不承诺 |
| 浏览器 DOM 驱动页面 | **not_run** | 只做 `GET /knowledge` 结构与 Node 执行纯 helper（与既有页面票同一边界） |
| 版本重建/删除治理/检索规模/缓存 | **not_run / 后续** | T061、F07/T058、T064、T067 |
| 全量 `pytest tests` | **not_run（orchestrator）** | 按 BRIEF §3；本票跑 `tests/f10` 两遍 + `tests/tooling` |
| 直接替换生产 parser 为候选 | **不做（决策）** | ADR 0015 §6：任何候选采用都需新 parser/chunker revision + 新安全/OCR 决定 |

## 8. 下一票接口（复用点）

- 解析器：`ParserFor(MediaTypePDF)` → `pdfParser`；`Parsed.PDF`（页映射/空页）与
  `Chunk.PDF`（页码/页内偏移）是后续格式的参考形状；换算法 = 新 parser/chunker revision。
- citation：`pdf` 对象字段已固定；`pages_without_text` 是文件级事实的当前承载方式
  （每 chunk 重复），若未来大文档空页很多，应改为文档级事实——这是新的数据模型决定。
- 失败诊断：`pdf_no_text_layer`/`pdf_invalid`/`pdf_encrypted` 已进 `parseFailureCodes`，
  T061 若需要持久化历史失败位置仍是新决定。
- 迁移：`0037` 只扩非契约 `artifact_admissions` 词表；后续格式新增 detected_format 值
  应同样用 additive migration + purpose-scoped allowlist，而不是放宽基础 allowlist。
- 实验入口：`probes/f10_pdf_compare/` 可重放（`run_comparison.py`、`attempt_docling.py`、
  `candidate_worker.py`）；其 dependencies 在 `probes/f10_pdf_compare/requirements.txt`
  （仅实验，不进 product/test lock）。
- 页面：`formatPageRange`/pdf citation 一致性检查可被后续页面复用。

## 9. 与契约/既有票据的关系（如实）

1. **迁移 0037**：只改 T028 非契约表 `platform.artifact_admissions` 的
   `detected_format` CHECK（+`pdf`）；`contracts/**`、`docs/spec/**` 未改，
   `sync_spec --check` no drift。
2. **citation additive**：`pdf` 对象仅 PDF chunk 出现；txt/md/csv/xlsx citation 不变
   （T055/T059 用例仍绿）。
3. **新 Go 依赖** `rsc.io/pdf v0.1.1`（BSD-3-Clause，约 50 KB 源码）——只用于 PDF 容器
   解析；`go.mod`/`go.sum` 已更新，install 可在模块缓存/网络可用环境重建。
4. **既有测试预期更新（两处，diff 可见）**：
   - `tests/f05/test_t028_quarantine.py` 的“未支持声明”例子从 `application/pdf`
     改为 `application/msword`（PDF 现在 purpose=knowledge 合法）；
   - `tests/f10/test_t055_knowledge_api.py` 的“无注册 parser”422 例子改为
     `application/msword`；T055/T059 页面测试改为 `.pdf -> application/pdf`。
   这些是 T060 扩展准入/注册表后的**必然**预期更新，不是放宽断言。
5. **无第二条知识状态权威**：handler 仍不写 document state；完成守卫仍是唯一
   `ready/failed/error_code` 写者；PDF 失败同样走它。

## 10. 参考

- ADR：`product/docs/adr/0015-f10-pdf-ingest.md`（本票）；`0010`/`0013`（F10 基础/
  表格族）；`0005`/`0007`（非契约面先例）。
- 基础：`product/reports/f10/T055-knowledge-ingest.md`、
  `product/reports/f10/T059-tabular-ingest.md`。
- 对照证据：`product/reports/f10/t060-comparison.json`、
  `product/probes/f10_pdf_compare/docling_attempt.json`、
  `product/reports/f10/t060-docling-pip.log`。
