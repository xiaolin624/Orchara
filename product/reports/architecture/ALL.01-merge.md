# ALL.01 / #159 合并记录

日期：2026-11-09。票：[GitHub #159](https://github.com/xiaolin624/Orchara/issues/159)（ALL.01，父 #160）。
集成工作树：`E:\Orchara-wt\integration`，分支 `integration/architecture-c1-c6`。
合并依据：票报告 `product/reports/architecture/ALL.01-integration.md`（六项覆盖矩阵、删除审计、
集成检查、限制、5 列 PROGRESS 行）、覆盖表
`product/docs/reviews/2026-11-09-all-architecture-coverage.md` 及同目录 `ALL.01-*` 原始输出。

## 合并提交

- 合并前集成 tip：`5fa2eeac1eb46c7f1552af24b716cf4208b099e3`
  （`chore(security): redact local development credentials from evidence`）。合并前
  `git status --short` 为空；任务书给出的集成 tip `5fa2eea` 与实际一致。
- 票分支 tip：`5d8c1fcd60c147932a2bb2feac29cb403c9ca8d7`
  （`t/all.01-integration`，`docs(all.01): clarify the post-evidence commits in tip notes`）。
  任务书给出的票分支 tip `5d8c1fc` 与实际一致。票分支自 `5fa2eea` 起 4 个提交：
  `0be7ff2`（证据/覆盖表/报告/PROGRESS 归一化）、`682e085`（回填证据 tip）、
  `ee012af`（新增文件统一 LF）、`5d8c1fc`（tip 说明措辞）。
- merge-base：`5fa2eea`（= 合并前集成 tip）。集成侧自票分支创建后没有新提交，本次为单侧前进，
  不存在双端并行改动。
- 合并提交：`628d530981b266f2d2dbe86080c861197a3677e7`
  （`merge(ALL.01): integrate six architecture improvements and close out coverage (#159)`，
  双亲 `5fa2eea` + `5d8c1fc`）。21 个文件，+1029 / −12：修改 1、新增 20。
- `git diff --stat 5d8c1fc HEAD` 为空：合并树与票分支 tip 逐字节相同。因此票报告 §6 在
  `5fa2eea`/票 tip 代码树上执行的套件结果直接适用于合并树；本次合并没有、也无法引入代码或测试
  字节变化（唯一修改文件是 `PROGRESS.md`，其余 20 个为报告/证据/覆盖表）。

## 冲突

**无。** `git merge --no-ff` 由 `ort` 策略直接生成合并提交，未做手工冲突合并，未删除或改写任一侧
行为。原因：merge-base 就是合并前集成 tip，票分支包含集成侧全部提交；`PROGRESS.md` 的差异全部
来自票分支（归一化 + 行追加），不存在集成侧并行版本，故无冲突标记。

```powershell
git merge-base integration/architecture-c1-c6 t/all.01-integration
# 5fa2eeac1eb46c7f1552af24b716cf4208b099e3（= 合并前集成 tip）
git diff --stat t/all.01-integration HEAD
# 空（exit 0）：合并树 == 票分支树
```

## 变更文件（21 个）

| 类别 | 文件 | 内容 |
|---|---|---|
| 进度 | `PROGRESS.md`（+43/−12） | 票分支的 C1–C6 归一化：#138–#141 四行补 `C1.0x` 标签；新增 #142/#143 行；`C3.01 / #149`、`C4.01` 两行规范为 `#149 | #160`、`#152 | #160`；#150/#151 标题补 `C3.02`/`C3.03`；#152 真实栈计数口径更正（见下）；#155 状态注明 #156–#158 已完成；新增 #159 `partial` 行；表后新增“已交付 / 仍有限制”节 |
| 覆盖表 | `docs/reviews/2026-11-09-all-architecture-coverage.md`（新，231 行） | 原报告 6 项 + 5 项 observation、89 条故事、22 个工作切片、删除目标、代码/合并提交、命令与原始输出、未运行/blocked/partial 的逐项映射 |
| 票报告 | `reports/architecture/ALL.01-integration.md`（新，196 行） | 六项覆盖摘要、叶子票合并审计、删除审计、集成检查结果、限制、提交映射、#159 PROGRESS 行 |
| 删除审计 | `reports/architecture/ALL.01-deletion-audit.ps1`（新）、`ALL.01-deletion-audit.txt`（新） | 仓库级 production/tests/historical 分类与逐符号匹配数；C5 AST 契约 0 callers/0 definitions |
| 原始输出 | `reports/architecture/ALL.01-{go-build,go-test,go-vet,gofmt,sync-check,pytest-tooling,pytest-f10-full,pytest-f11,pytest-f12,pytest-c2-focus,pytest-c4-focus,pytest-c4-focus-rerun,pytest-t113,f03-t032-preexisting}.txt`（14 个新文件） | Go 门禁、sync、tooling/f10/f11/f12、C2/C4 交叉模块、T113、T032 既有红 |
| 证据工具 | `reports/architecture/ALL.01-{redact-evidence,fix-evidence-encoding}.py`（2 个新文件） | 仅用于提交前脱敏/行尾修正的报告工具，不是产品代码或测试，不被产品运行/CI 调用 |

未改 `product/docs/spec/`、`product/contracts/`、`product/internal/`、`product/tests/`、
`product/apps/`、`product/deploy/`、`product/cmd/`、`product/tools/`；无 Go/SQL/页面字节变化。

## PROGRESS.md

票分支在 `## C1–C6 架构改进（2026-11-09）` 下把表归一化为：C1 复用票 4 行（#138–#141，
`| 票 | 父 | 标题 | 状态 | 证据 |`）＋新增 18 票一行一票（#142–#159）。合并后核实：

- 编号数据行共 **22**（4 个 C1 复用 + 18 个新增）；新增表恰为 **18 行**，票号从 #142 到 #159
  连续、无缺号、无重复。
- 18 行清单（标题/状态为表内原文，本节为摘要）：#142 C5.01 `passed`、#143 C5.02 `passed`、
  #144 C5.03 `passed（一次并发外来 runsc 孤儿断言失败，隔离复跑通过）`、#145 C5.04 `passed`、
  #146 C2.01 `passed（真实厂商 not_run）`、#147 C2.02 `passed（真实厂商 not_run）`、
  #148 C2.03 `passed（真实厂商 not_run）`、#149 C3.01 `passed（本票范围；…归 C3.02/#150）`、
  #150 C3.02 `passed`、#151 C3.03 `passed`、#152 C4.01 `passed`、#153 C4.02 `passed`、
  #154 C4.03 `passed`、#155 C6.01 `passed（本票范围；#156–#158 已随后完成）`、#156 C6.02 `passed`、
  #157 C6.03 `passed`、#158 C6.04 `passed`、#159 ALL.01
  **`partial（本分支本地集成验证；C4 交叉模块 1 项已知缺陷失败；待修复/合并）`**。
- **#159 行**在票分支中已存在，且与报告 §9 的 ready-to-append 行逐字符相等（trim 后完全相等，
  685 字符；程序化比对输出 `True`）。按 orchestrator 指令保留 `partial` 状态与 C4 已知失败原文，
  未升级为 `passed`、未改写失败措辞。因该行已随合并进入，**未重复追加第二行**；重复追加会制造
  重复票号，故只做存在性与逐字符校验。合并报告 `git diff --numstat 5fa2eea..HEAD -- PROGRESS.md`
  = `43 12`，即归一化改动（43 增/12 删）全部来自票分支。
- 历史行核对：旧表 17 个编号行 + 2 个未编号行（`C3.01 / #149`、`C4.01`）= 19 个票行；新表 22 个
  编号行。没有票行丢失（增加 #142/#143/#159；#149/#152 由未编号行规范为票号行）。内容改动仅限：
  - #138–#141 标题补 `C1.0x` 标签（证据单元格未变）；
  - #149/#152 票/父列规范为 `#149 | #160` / `#152 | #160`，标题补 `C3.01`/`C4.01`；
  - #150/#151 标题补 `C3.02`/`C3.03`（证据未变）；
  - #152 证据口径更正：原“真实栈 pytest 36 passed”改为“真实栈 29 passed …另有 LangGraph
    语义证明 7 passed（非生产执行，不计入 29，两者合计 36，不是 36 项真实栈通过）”，与
    `C4.01-agent-context.md` 一致；
  - #155 状态从“本票本地实现与验收完成；#156/#157/#158 仍待…”改为“本票范围；#156–#158 已随后
    完成”；
  - 其余行（#144–#148、#153、#154、#156–#158）内容不变，仅按票号排序；未删除任何证据单元格。

## 合并 tip 验证

环境：Windows 11（NT 10.0.26200）；`D:\Python\python.exe` Python 3.12.2 / pytest 9.0.3；
pytest 均经 `E:\Orchara-notes\test.ps1`；cwd 为 `E:\Orchara-wt\integration\product`
（`git grep` 在仓库根执行）。下列结果均在合并提交 `628d530` 的树上执行；原始输出见
`reports/architecture/ALL.01-merge-checks.txt`。

| 检查 | 命令 | 结果 |
|---|---|---|
| 规格同步门禁 | `D:\Python\python.exe -X utf8 tools\sync_spec.py --check` | `no drift: current source, both copies and synchronized baseline agree`，**exit 0** |
| 旧全局删除 | `git grep -n 'runStream' -- 'product/apps' 'product/tests' 'product/deploy'` | **0 命中，exit 1（完全空）**；C6.04 自身的“断言其不存在”针对的是 `RunStateDependencyUnavailable`（`tests/f12/test_c6_04_page_contract.py:9,164` 两处），不是 `runStream`，因此本命令无例外行。`product/` 下其余 `runStream` 命中全在历史文档/报告/PROGRESS（实现者审计计 93 处 historical、production/tests 0） |
| 覆盖表 | `product/docs/reviews/2026-11-09-all-architecture-coverage.md` | 存在，37209 字节 |
| 票报告 | `product/reports/architecture/ALL.01-integration.md` | 存在，16311 字节 |
| PROGRESS | 编号数据行 / 新增 18 行 | 22 / 18；#159 行与报告 §9 逐字符相等，状态 `partial` |
| 收集健全性 | `& E:\Orchara-notes\test.ps1 tests -q --collect-only -p no:cacheprovider` | **2910 tests collected in 15.86s，exit 0**，无 import/collection error（pytest 收集错误会 exit 2）；各套件计数与票报告一致：f10 114、f11 85、f12 525、tooling 148 |

收集数说明：任务书预期约 2830，实测 2910（+81）。差异与集成分支历史一致——最后一次记录的
2829 收集在 C5.04 合并（`3eec0c2`）时点；其后合入的 C6.01–C6.04 页面/依赖套件新增约 79 条
测试（C6.01 24、C6.02 19、C6.03 20、C6.04 16，含真实浏览器/真实 API/Node 用例）。本次仅做
收集，不声称运行通过；收集 exit 0 只证明无导入/收集错误。

按 orchestrator 指令**未重跑**：`tests/f10`、`tests/f11`、`tests/f12` 长套件，以及 C2/C4 交叉
模块、T113、f03/T032 既有红。理由：合并树与票分支 tip 逐字节相同（`git diff --stat 5d8c1fc HEAD`
为空），合并只增加文档/证据，票报告 §6 的计数与 C4 已知失败结论直接适用于本树。**C4 交叉模块
`tests/f04b/test_c4_03_scheduler_regressions.py::test_graph_cancellation_drains_the_started_sibling_and_commits_one_exit`
的已知冷启动取消计时失败保持原状：未修复、未跳过、未放宽、未升级**，ALL.01 状态保持 `partial`。

## 证据路径

- 覆盖表：`product/docs/reviews/2026-11-09-all-architecture-coverage.md`
- 票报告：`product/reports/architecture/ALL.01-integration.md`
- 删除审计与原始输出：`product/reports/architecture/ALL.01-*`（含 `ALL.01-deletion-audit.txt`、
  `ALL.01-pytest-c4-focus*.txt`、`ALL.01-go-test.txt` 等）
- 本次合并小证据：`product/reports/architecture/ALL.01-merge-checks.txt`
- 本记录：`product/reports/architecture/ALL.01-merge.md`

## 异常与说明（诚实记录）

1. **收集数高于任务书预期**：2910 vs 约 2830，原因见上（C6 套件在 2829 之后合入）；无收集错误，
   不是失败，也不代表运行通过。
2. **PROGRESS 归一化改写了部分历史行并重排**：MERGER 默认“不改写/不重排历史行”，本次票分支按
   orchestrator 明确预期（“PROGRESS normalized to 18 rows”）做了归一化：补 `C1.0x`/`C3.0x`/`C4.0x`
   标签、补 #142/#143 行、规范 #149/#152 的票/父列、更正 #152 计数口径、更新 #155 状态措辞、按
   票号排序，并在表后新增“已交付 / 仍有限制”节。已逐票核对：无票行丢失、无证据单元格被删除、
   #159 行未被升级。若评审认为历史行应保持原文，需另行回退归一化（会同时失去 #142/#143 行与
   排序），本合并未擅自改写。
3. **#159 行已随票分支存在**：未重复追加（重复票号会误导进度统计）；以程序化逐字符相等校验
   代替“再追加一行”，校验结果为 True。
4. **行尾**：`PROGRESS.md` 全文件为 CRLF（合并前后均如此），`git diff --check` 会把新增行报为
   trailing whitespace；这是该文件既有行尾约定，不是本次引入的损坏。新增覆盖表/报告/证据均为
   LF（票分支 `ee012af` 已统一）。
5. **凭据**：新增 `ALL.01-*` 证据已脱敏；本次复核未在新增文件中发现真实 DSN/口令/签名值
   （仅 `<REDACTED>`/`<REDACTED-SIGNING-KEY>` 占位与脱敏脚本的正则定义）。历史提交仍含旧值，
   本分支未 push，**不要直接 push 该历史**（见 `reports/architecture/SECURITY-redaction-note.md`）。
6. **合并无分叉**：merge-base = 合并前集成 tip，合并树 == 票分支树；后续票若再动
   `PROGRESS.md`/覆盖表/报告，需以本 tip 为基线追加，避免覆盖 #159 行。

## 限制

- 未 push、未创建 PR、未关闭/编辑 Issue、未改写历史、未 amend 既有提交。
- 未运行完整 `pytest tests`；未重跑 f10/f11/f12、C2/C4/T113 交叉模块与 T032 既有红（理由见上）。
- 真实供应商模型/embedding、生产部署、非 Chrome 浏览器、远程/网络文件系统、物理断电、
  `go test -race` 仍按票报告 §7/§8 为 `blocked`/`not_run`，本合并未改变这些条件。
- 本记录只证明“按 orchestrator 指令在合并 tip 的验证结果”，不替代代码评审或 AC 人工复核；
  C4 已知缺陷的修复与复验仍待后续实现者/评审处理。

## 下一步

- `integration/architecture-c1-c6` 现含 `628d530`（C1–C6 各票 + ALL.01 收口）。
- orchestrator：按票报告 §10 回填最终 merge tip 到覆盖表 §9 与本报告对应票报告 §8；关闭
  `#138–#159` 前确认 `blocked`/`not_run`/`partial` 条件不被抹去；不要直接 push 含旧凭据的历史
  （如需远端发布，改用 squash 或先改写历史）。
