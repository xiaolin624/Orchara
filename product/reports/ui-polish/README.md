# 前端精细化二期验收与证据（A 精密工作台骨架 + C 暖色/状态语言）

2026-11-26；状态：**passed，仅限本次 UI 精细化范围**。集成分支 `integration/ui-polish-20261126`，集成 tip
`2de56b1`；验收分支 `t/ui-polish.t10`；本轮基线 `736b8db`（上一轮 #163 收口）。上一轮证据：
[`../ui-redesign/`](../ui-redesign/README.md)。

本报告记录**实际执行**的命令与原始结果。`dev-stub`、静态校验与 Node shim 结果不等同真实模型质量或生产验收；
未运行项一律写 `not_run`，失败历史原样保留。

## 1. 范围与方向

- 22 个产品页面 + Vue Flow 画布，从「统一但不够精细」提升到可交付质量：真实数据页按任务组织信息层级、
  画布占满视口的专业编辑器工作台、手机端针对对话/审批/查看结果优化。
- 方向：**A 精密工作台**的布局、密度与交互骨架 + **C 暖色指挥台**的色彩、表面与状态图形语言。
  设计令牌唯一来源：`product/apps/web/src/assets/design.css` 的 `:root`（规格见仓库根 `docs/frontend-polish.md` §2）。
- 不改业务范围、API、权限与角色可见性、状态语义、审批与数据去向的事实；页面改版不提升真实模型、正式隔离或
  完整 v1 业务验收状态。
- 状态双编码：形状/图形 + 文字 + 颜色第三编码；字号下限 11px；按钮/链接 44px 触摸目标；无横向溢出。

本轮 11 张子票均已合并到集成分支：

| 票 | 内容 | 合并 commit |
|---|---|---|
| #165 T1 | 设计系统与共享外壳 | `05b5fbe` |
| #166 T2 | 对话工作区 | `37b2b6f` + 修复轮 `acc2c98` |
| #167 T3 | 应用目录与我的应用 | `75a82ba` |
| #168 T4 | 工作流编辑器与画布 | `ebc211d` |
| #169 T5 | 运行与审批 | `c27de4b` |
| #170 T6 | 数据与能力页 | `794e5f8`（+ 测试断言修正 `2ad9742`） |
| #171 T7 | 评测与发布链路 | `7ee3568` |
| #172 T8 | 身份、状态与管理台 | `4726612` |
| #173 T9 | 移动端、无障碍与动效精修 | `a48aa7f` |
| #175 T11 | 两轴 code-review 修复 | `d4a96b7`（规格同步 `fd4c6bb`、注释修复 `2de56b1`） |
| #174 T10 | 总验收、证据与文档 | 本报告所在提交 |

## 2. 截图索引

总览：[22 页桌面总览](contact-sheet.png)、[22 页手机总览](contact-sheet-mobile.png)。
截图共 **53 张**：22 页桌面 + 22 页手机 + 4 页手机横屏 + 5 张关键交互态；全部为真实 Chrome headless 渲染，
数据为验收生成的 dev-stub 样例数据，不含口令、DSN 或令牌。

| 页面 | 桌面 1440×1000 | 手机 375×812 | 手机横屏 844×390 |
|---|---|---|---|
| 平台状态 `/status` | [index](screenshots/index-desktop.png) | [index](screenshots/index-mobile.png) | — |
| 登录 `/login` | [login](screenshots/login-desktop.png) | [login](screenshots/login-mobile.png) | — |
| 激活 `/activate` | [activate](screenshots/activate-desktop.png) | [activate](screenshots/activate-mobile.png) | — |
| 应用与创作 `/apps` | [apps](screenshots/apps-desktop.png) | [apps](screenshots/apps-mobile.png) | — |
| 用途目录检索 `/catalog` | [catalog](screenshots/catalog-desktop.png) | [catalog](screenshots/catalog-mobile.png) | — |
| 工作流编辑器 `/apps/{id}/edit` | [editor](screenshots/editor-desktop.png) | [editor](screenshots/editor-mobile.png) | — |
| 调试 `/apps/{id}/debug` | [debug](screenshots/debug-desktop.png) | [debug](screenshots/debug-mobile.png) | — |
| 对话工作区 `/chat` | [chat](screenshots/chat-desktop.png) | [chat](screenshots/chat-mobile.png) | [chat](screenshots/chat-landscape.png) |
| 运行 `/runs` | [runs](screenshots/runs-desktop.png) | [runs](screenshots/runs-mobile.png) | [runs](screenshots/runs-landscape.png) |
| 审批 `/approvals` | [approvals](screenshots/approvals-desktop.png) | [approvals](screenshots/approvals-mobile.png) | [approvals](screenshots/approvals-landscape.png) |
| 工具 `/tools` | [tools](screenshots/tools-desktop.png) | [tools](screenshots/tools-mobile.png) | — |
| 模型 `/models` | [models](screenshots/models-desktop.png) | [models](screenshots/models-mobile.png) | — |
| 附件 `/attachments` | [attachments](screenshots/attachments-desktop.png) | [attachments](screenshots/attachments-mobile.png) | — |
| 知识库 `/knowledge` | [knowledge](screenshots/knowledge-desktop.png) | [knowledge](screenshots/knowledge-mobile.png) | — |
| 记忆 `/memories` | [memories](screenshots/memories-desktop.png) | [memories](screenshots/memories-mobile.png) | — |
| 个人数据 `/personal-data` | [personal-data](screenshots/personal-data-desktop.png) | [personal-data](screenshots/personal-data-mobile.png) | [personal-data](screenshots/personal-data-landscape.png) |
| 评测入口 `/apps/{id}/evaluate` | [evaluate](screenshots/evaluate-desktop.png) | [evaluate](screenshots/evaluate-mobile.png) | — |
| 评测详情 `/evaluations/{id}` | [evaluations](screenshots/evaluations-desktop.png) | [evaluations](screenshots/evaluations-mobile.png) | — |
| 发布 `/apps/{id}/publish` | [publish](screenshots/publish-desktop.png) | [publish](screenshots/publish-mobile.png) | — |
| 发布审核 `/reviews` | [reviews](screenshots/reviews-desktop.png) | [reviews](screenshots/reviews-mobile.png) | — |
| 版本 `/apps/{id}/versions` | [versions](screenshots/versions-desktop.png) | [versions](screenshots/versions-mobile.png) | — |
| 管理台 `/admin` | [admin](screenshots/admin-desktop.png) | [admin](screenshots/admin-mobile.png) | — |

关键交互态（5 张）：

- [草稿并发冲突](screenshots/editor-conflict-desktop.png)：真实并发保存后本地草稿保留、冲突面板出现，不覆盖本地修改。
- [应用行 ID/版本展开](screenshots/apps-row-expanded-desktop.png)：内部 ID 按需展开，与标题缩进对齐、不溢出。
- [对话输入与提交前状态](screenshots/chat-compose-desktop.png)：新建会话后的 composer 与真实请求/队列上下文。
- [手机 tabbar 净空](screenshots/chat-bottom-mobile.png)：滚到底部后正文最后一个区块仍在固定底栏之上。
- [手机上下文 sheet](screenshots/chat-context-mobile.png)：运行/队列/产出/数据去向/可交接应用五项事实。

评测详情页使用**不存在的评测 ID**，用于明确验证错误状态；其余页面使用真实 dev-stub API 返回的草稿、空列表与表单状态。

## 3. 实际执行与原始结果

环境（Windows）：Python 3.12.2、pytest 9.0.3、Node v24.13.0 / npm 11.13.0、Go 1.27.3、
PostgreSQL 16.4（独立开发集群 127.0.0.1:55432）、Chrome 158.0.8127.88 headless。

命令均在 `product/` 下执行，Go/psql 使用用户态工具链（`E:\orchara-toolchains`）：

```powershell
$env:PATH = "E:\orchara-toolchains\go\bin;E:\orchara-toolchains\pgsql\bin;$env:PATH"
$env:GOCACHE = "E:\orchara-toolchains\gocache"; $env:GOMODCACHE = "E:\orchara-toolchains\gomodcache"
```

| # | 命令 | 原始结果 |
|---|---|---|
| 1 | `python -X utf8 tools\verify_frontend.py --out reports\ui-polish` | **passed**，42.5s；22 桌面 / 22 手机 / 4 横屏；`page_errors=[]`；17 项检查；53 张截图（[verify-frontend.log](verify-frontend.log)、[browser-report.json](browser-report.json)） |
| 2 | `pytest tests\f01\{t015,t017,t019,t020,t021}_pages.py test_page_query_guard.py test_f01_app_js.py --junitxml=reports\ui-polish\f01-regression.xml` | **36 passed / 0 failed / 0 skipped**，43.0s（[f01-regression.xml](f01-regression.xml)） |
| 3 | 其余 51 个 `*_pages.py` 一批，`--junitxml=reports\ui-polish\pages-regression.xml` | **244 passed / 0 failed / 0 skipped**，505.3s（8m25s）（[pages-regression.xml](pages-regression.xml)） |
| 4 | 页面级契约组 11 文件（canvas assets、T095 角色/入口、C6.01–C6.04、T083 runview、T096/T098 复核、T175 复核修复），`--junitxml=reports\ui-polish\page-contract.xml` | **148 passed / 0 failed / 0 skipped**，125.7s（[page-contract.xml](page-contract.xml)） |
| 5 | `pytest tests\f12\test_c6_04_page_contract.py --junitxml=reports\ui-polish\c6-04-repeat.xml`（已知 flake 复跑） | **16 passed**，25.1s（[c6-04-repeat.xml](c6-04-repeat.xml)） |
| 6 | 快速组 8 文件（T165/T173/T167/T170/T171/T172/T169/T166），`--junitxml=reports\ui-polish\quick-regression.xml` | **286 passed / 0 failed / 0 skipped**，4.0s（[quick-regression.xml](quick-regression.xml)） |
| 7 | `cd apps\web\canvas; npm.cmd ci; npm.cmd run build` | **成功**；`canvas.js` 809.4kb、`canvas.css` 30.1kb；构建后 `git status` 无资产变化 → 产物可由源码逐字节重建 |
| 8 | `go test ./internal/api ./cmd/api` | **ok**：`internal/api` 0.990s；`cmd/api` 无测试文件（构建通过）；总 5.3s |
| 9 | `python -X utf8 product\tools\sync_spec.py --check`（仓库根） | **no drift**，exit 0 |
| 10 | `node --check` 本轮改动的 16 个 JS | **16/16 通过**，0 失败 |
| 11 | `git diff --check`；`python -m py_compile tools\verify_frontend.py` | 均 exit 0 |

`verify_frontend.py` 的对比度实测（12 对，均 ≥4.5:1）：

| 对 | 比值 | 对 | 比值 |
|---|---|---|---|
| 正文 / 卡片底 | **16.49** | 次要文字 / 卡片底 | **7.71** |
| muted / 卡片底 | **5.41** | muted / soft 底 | **4.72** |
| 次要文字 / soft 底 | **6.72** | 白字 / 主按钮 | **5.02** |
| accent-ink / accent-soft | **5.73** | success / success-soft | **5.56** |
| warning / warning-soft | **5.24** | danger / danger-soft | **6.04** |
| info / info-soft | **5.89** | teal / teal-soft | **5.47** |

浏览器验收还实际证明：22 页唯一 `<h1>`、每页恰好一个 `design.css`、每个导航地标内最多一个 `aria-current="page"`
（侧栏主导航恰好一个）、22 页 375px 无横向溢出且所有可见 `a`/`button` ≥44px、6 个 tabbar 页最后区块在底栏之上、
手机抽屉与上下文 sheet 的打开/关闭（scrim/Escape/焦点返回 `aria-expanded`）、键盘焦点环、减少动态效果下
transition/animation 为 0s、点击退出图标真实撤销服务端会话（`/api/v1/auth/me` 返回 401）。

> T10 对验收脚本的唯一改动：把手机横屏截图从 1 张（personal-data）补到 4 张（chat/approvals/runs/personal-data），
> 只增加证据，不放宽/删除任何断言。`--out reports\ui-polish` 仍是本轮唯一写仓库截图的一次运行。

## 4. 页面回归汇总

- **f01 批**（5 个 `*_pages.py` + `test_page_query_guard.py` + `test_f01_app_js.py`，7 文件）：36 passed。
  f01 的 `from conftest import` 与其它目录同批会 ImportError，因此单独一批。
- **其余页面批**（51 个 `*_pages.py`）：244 passed；按目录 f02a 13、f02b 5、f03 30、f05 15、f06 39、f07 6、
  f08 12、f09 6、f10 15、f11 6、f12 90、f13a 3、t070 4。
- **页面级契约组**（11 文件）：148 passed；f04a 9、f04b 7、f12 132。其中 `test_c6_04_page_contract.py`
  的真实浏览器矩阵（含 `[runs-normal]`）本轮通过。
- **快速组**（8 文件）：286 passed，与上面批次有意重叠 4 个文件，作为收口复跑。
- 五组执行合计 **730 次测试执行**；按 `(模块, 用例名)` 去重后 **642 个用例，642 passed / 0 failed / 0 skipped**，
  覆盖 73 个测试模块文件。机器可读汇总见 [regression-summary.json](regression-summary.json)，
  合并视图见 [combined-regression.xml](combined-regression.xml)。

失败历史（如实保留，不隐藏）：

- 本轮合并前的集成轮里 `tests/f02a/test_t027_pages.py` 曾因 T4 把「调试当前草稿」改为「打开私有调试页」而失败
  （190 passed / 1 failed）；已由 `2ad9742` 改为断言 `#debug-entry` 锚点与中文标签，本次该文件 6 passed。
- 已知并发 flake：`test_c6_04_page_contract.py::test_real_browser_final_matrix[runs-normal]` 在并发下偶发一次失败，
  隔离/重跑通过。本次契约组 16 passed，随后单独复跑该文件仍 16 passed（[c6-04-repeat.xml](c6-04-repeat.xml)），
  本次未复现；不据此宣称该 flake 已根治。
- 本轮最终全量执行（f01 + 51 页 + 契约组 + 快速组）**没有新增失败**；上表与 XML 为原始结果，未做任何重写。

## 5. 构建与静态检查

- **画布**：`npm.cmd ci`（干净 worktree 无 `node_modules`，按 `package-lock.json` 安装 43 包，5s）后
  `npm.cmd run build` 成功，57ms 内产出 `apps/web/src/assets/canvas.js`（809.4kb）与 `canvas.css`（30.1kb）；
  构建后 `git status` 只显示本票文档/证据改动，**画布产物无 diff**，证明提交的产物可由 `canvas/src/**` 重建。
- **Go**：`go test ./internal/api ./cmd/api` 通过；API 包 0.990s，`cmd/api` 无测试文件。
- **规格同步**：`sync_spec.py --check` → `no drift: current source, both copies and synchronized baseline agree`。
  本票把仓库根 `docs/frontend-polish.md` 的状态改为已实现并验收后，用 `sync_spec.py` 同步镜像
  `product/docs/spec/docs/frontend-polish.md` 与 `SYNC-MANIFEST.json`，再以 `--check` 复核。
- **语法/空白**：本轮改动的 16 个 JS 全部 `node --check` 通过；`git diff --check` 与验收脚本的 `py_compile` 均 exit 0。

## 6. 与上一轮 `ui-redesign` 的对比

| 维度 | 上一轮 #163（`reports/ui-redesign/`） | 本轮 #164（本目录） |
|---|---|---|
| 视觉方向 | 统一浅色设计系统、靛紫主色 | A 工作台骨架 + C 暖纸底/赤陶主色；画布整页三栏工作台 |
| 截图 | 34 张（24 桌面 + 9 手机 + 1 横屏） | 53 张（25 桌面 + 24 手机 + 4 横屏），另加 2 张总览 |
| 手机覆盖 | 9 个页面 | 22 个页面全部 375px 溢出检查；6 个 tabbar 页净空检查；44px 触摸目标全量检查 |
| 对比度 | 正文 15.22 / 次要 4.97 / 主按钮 5.64 | 正文 16.49 / 次要 7.71 / 主按钮 5.02（共 12 对全部 ≥4.5） |
| 状态语言 | 颜色 + 文字 | 形状/图形 + 文字 + 颜色三编码；步骤点、进度条、导航当前项 2px 左条 |
| 无障碍/动效 | 跳转链接、减少动态效果 | 追加逐导航地标 `aria-current`、抽屉 `aria-expanded`、上下文 sheet 焦点管理、44px、focus-visible 焦点环 |
| 画布 | 卡片内画布 | 占满视口的节点库—画布—属性面板三栏工作台，节点类型色条与小地图 |

两轮证据目录互不覆盖：`ui-redesign/` 保留历史，本轮写入 `ui-polish/`。

## 7. 范围与限制

- **dev-stub**：浏览器与回归使用真实本地 PostgreSQL、真实编译的 `cmd/api`、真实 HTTP 与真实 Chrome，
  但产品 profile 为 `dev-stub`；**不代表**真实供应商模型质量、正式隔离、生产部署或完整 v1 业务验收。
- **浏览器**：仅 Chrome 158.0.8127.88 headless（Playwright），未验证 Safari/Firefox/Edge 与真实移动设备。
- **手机**：22 页做了 375px 无溢出检查、可见控件 44px 检查、6 个 tabbar 页净空检查；4 页做了 844×390 横屏
  无溢出检查。未做真机、iOS 安全区、软键盘、横竖屏切换、屏幕阅读器与高对比度模式的实机验收。
- **画布**：完整编辑仍只承诺桌面；≤720px 显示 `data-mobile-scope="no-full-canvas-editing"` 提示，不做手机全量编辑。
- **静态断言边界**：页面结构、脚本顺序、状态/权限契约由 pytest 静态/Node shim + 真实浏览器共同覆盖；
  像素级视觉质量由截图人工复核，不是自动像素回归。
- **not_run**：完整 `pytest tests`（本轮按页面/契约范围分组执行，未跑全部产品测试）、其他浏览器、真实模型
  供应商与真实 embedding、生产部署、非 Chrome 屏幕阅读器、真机移动端、远程/网络文件系统。
- 截图中的账号、应用、会话、运行均为验收生成的样例数据；证据中无口令、DSN 或令牌（DevStack 日志中的 DSN 已脱敏）。

## 8. 复现

```powershell
# 仓库根
python -X utf8 product\tools\sync_spec.py --check

# product/
python -X utf8 tools\verify_frontend.py --out reports\ui-polish   # 会自建并清理独立 dev-stub 数据库
python -X utf8 -m pytest tests\f01\test_t015_pages.py tests\f01\test_t017_pages.py tests\f01\test_t019_pages.py `
  tests\f01\test_t020_pages.py tests\f01\test_t021_pages.py tests\f01\test_page_query_guard.py `
  tests\f01\test_f01_app_js.py -q --junitxml=reports\ui-polish\f01-regression.xml
# 其余 51 个 *_pages.py（排除 tests\f01）一批
go test ./internal/api ./cmd/api
cd apps\web\canvas; npm.cmd ci; npm.cmd run build
```

## 9. 证据文件清单

| 文件 | 内容 |
|---|---|
| [browser-report.json](browser-report.json) | 真实浏览器报告：22 页条目、17 项检查、对比度 token 值、`page_errors`、时间戳 |
| [verify-frontend.log](verify-frontend.log) | 上述运行的原始控制台输出（DevStack 命令、每页通过行、最终 passed） |
| [f01-regression.xml](f01-regression.xml) | f01 批 36 项原始 JUnit |
| [pages-regression.xml](pages-regression.xml) | 其余 51 个 `*_pages.py`、244 项原始 JUnit |
| [page-contract.xml](page-contract.xml) | 页面级契约组 11 文件、148 项原始 JUnit |
| [quick-regression.xml](quick-regression.xml) | 快速组 8 文件、286 项原始 JUnit |
| [c6-04-repeat.xml](c6-04-repeat.xml) | 已知 flake 文件单独复跑、16 项原始 JUnit |
| [combined-regression.xml](combined-regression.xml) / [regression-summary.json](regression-summary.json) | 642 个去重用例的合并视图与逐文件汇总 |
| [screenshots/](screenshots/) | 53 张真实 Chrome 截图 |
| [contact-sheet.png](contact-sheet.png) / [contact-sheet-mobile.png](contact-sheet-mobile.png) | 22 页桌面 / 手机总览 |
