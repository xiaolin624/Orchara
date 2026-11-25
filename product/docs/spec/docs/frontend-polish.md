# Orchara 前端精细化（二期）规格

日期：2026-11-26。状态：方向已选定，待实现。
上一轮：`docs/frontend-redesign.md` 与 [GitHub #163](https://github.com/xiaolin624/Orchara/issues/163)（已关闭，不重开）。
基线：`736b8db`。基线截图：`product/reports/ui-redesign/screenshots/`、总览 `contact-sheet.png`。

## 1. 目标与范围

把 22 个产品页面与 Vue Flow 画布，从「统一但不够精细」提升到可交付的视觉与交互质量：

- 每页第一屏能回答「这是什么、现在有什么、我能做什么」。
- 页面按任务设计，不再 22 页同一套「标题 + 说明 + 白卡片」节奏。
- 空白由有意义的内容占据（统计、最近活动、下一步、状态），不是留白。
- 画布成为专业编辑器工作台：整页三栏、占满视口高度、节点与属性面板精细。
- 手机端针对对话/审批/查看结果优化，不做桌面缩水版。

不在本次范围：业务范围、API、权限与角色可见性、状态语义、审批与数据去向的事实、发布/评测证据口径。
页面改版不提升真实模型、正式隔离或完整 v1 的业务验收状态。

## 2. 选定方向：A 的骨架 + C 的暖色

用户于 2026-11-26 选定：采用方向 A「精密工作台」的布局、密度、信息层级与交互骨架，替换为方向 C「暖色指挥台」的色彩、表面与状态图形语言。
参考原型（一次性对比物，不在仓库内，实现时只读）：

- `C:\Users\19677\AppData\Local\Temp\orchara-ui-polish\prototypes\a\`（骨架：三栏对话、应用表格、画布工作台、移动端底栏）
- `C:\Users\19677\AppData\Local\Temp\orchara-ui-polish\prototypes\c\`（色彩与状态：暖纸底、赤陶主色、色条、进度条、步骤点、小地图）
- 说明：`...\notes\DIRECTION-A.md`、`DIRECTION-C.md`；内容：`...\notes\CONTENT.md`；页面意图：`...\notes\PAGE-INTENT.md`
- 代码与测试地图（实现前必读）：`...\notes\EXPLORATION.md`

### 2.1 设计令牌（唯一来源：`product/apps/web/src/assets/design.css` 的 `:root`）

```css
:root {
  color-scheme: light;
  /* 表面：暖纸底，取自方向 C */
  --bg: #f7f4ee;
  --surface: #fffdf9;
  --surface-2: #f2ede4;
  --surface-3: #eae3d7;
  --border: #e5ded1;
  --border-strong: #d3c9b8;
  /* 文字 */
  --text: #211d18;
  --text-2: #57514a;
  --muted: #6f685e;
  /* 品牌：赤陶 */
  --accent: #b4530a;
  --accent-hover: #9d4708;
  --accent-ink: #96430a;      /* 浅色底上的文字用，避免 accent 在 accent-soft 上只有 4.4:1 */
  --accent-soft: #fbe9db;
  --teal: #0f6b66;
  --teal-soft: #e4f1ef;
  /* 语义 */
  --success: #2f6b3f;
  --success-soft: #e8f2ea;
  --warning: #8a5a00;
  --warning-soft: #fbf0d9;
  --danger: #a52a2a;
  --danger-soft: #fbe9e7;
  --info: #2f5d8a;
  /* 形状与密度：取自方向 A */
  --radius-sm: 7px;
  --radius: 10px;
  --radius-lg: 12px;
  --shadow-card: 0 1px 2px rgb(33 29 24 / 4%);
  --shadow-pop: 0 12px 32px rgb(33 29 24 / 10%);
  --sidebar: 232px;
  --topbar: 52px;
  /* 字号阶梯 */
  --fs-11: 11px; --fs-12: 12px; --fs-13: 13px; --fs-14: 14px;
  --fs-16: 16px; --fs-20: 20px; --fs-24: 24px;
  --font-mono: ui-monospace, "Cascadia Mono", Consolas, monospace;
  /* 动效 */
  --ease: cubic-bezier(.2, .7, .2, 1);
  --dur-1: 120ms; --dur-2: 180ms; --dur-3: 240ms;
  /* 层级 */
  --z-sticky: 20; --z-drawer: 40; --z-overlay: 60; --z-toast: 80;
}
```

保留向后兼容：现有组件继续使用 `--primary` / `--primary-soft` / `--primary-hover` 的地方，映射到赤陶
（`--primary: var(--accent)` 等），避免一次性改所有页面；`--muted` 等既有变量名不变。

对比度（必须实测并记录）：

- `--text` / `--surface` ≥ 12:1，`--text-2` / `--surface` ≥ 7:1，`--muted` / `--surface` ≥ 5:1
- 白字 / `--accent` ≥ 4.5:1，`--accent-ink` / `--accent-soft` ≥ 4.5:1
- 状态色文字（success/warning/danger）在各自 soft 底上 ≥ 4.5:1

### 2.2 排版

- 正文 13px / 1.55；次要文字 12px；元数据与表格表头 11px；标签 12px/550。
- H1 24px/650/-0.3px；H2（区块标题）15px/650；H3 13px/650；页头描述 13px `--text-2`，最大 68ch。
- 数字（计数、时间、版本、run id）用 `font-variant-numeric: tabular-nums`；节点 id、hash、JSON 用 `--font-mono`。
- 中文不加大写英文标记；eyebrow 仅在品牌/入口页保留，业务页用「区块标题 + 12px 说明」。

### 2.3 布局骨架（取自 A）

- 侧栏 232px：品牌 56px、工作空间切换 36px、分组（工作空间/创作与资源/个人空间/管理）10px/600 + 计数徽章、
  条目 32px（图标 18px + 13px 文字）、当前项 `--accent-soft` 底 + 2px 赤陶左条 + `--accent-ink`、
  底部账号 48px + 折叠手柄。≤720px 变抽屉，行为不变（`inert` / `body.nav-open` / Escape）。
- 顶栏 52px：面包屑（12px）左；中间搜索入口（`--surface-2` pill，240px，含 `⌘K` 提示，**只做入口不做功能**）；
  右侧「新建」主按钮、待办计数 pill（审批 2）、账号（姓名/角色两行 + 退出图标 `a[data-orchara-logout]`）。
- 页面头：单行 H1 + 右侧操作（主/次），下方 12px 元数据行（`·` 分隔），底边 1px 分隔线。
- 内容区 padding 24px 28px，最大宽度不设居中小栏（编辑器除外），区块间距 20px。
- 卡片 `--surface` + 1px `--border` + 12px 圆角 + `--shadow-card`；禁止卡片套卡片；页面内大卡片 ≤3 个。

### 2.4 状态语言（取自 C，双编码）

状态必须同时具备**形状/图形 + 文字**，颜色只作第三编码：

- `进行中 / 运行中`：实心圆点 + 文字；运行卡顶部 4px 进度条（accent → `--accent-soft` 剩余段）。
- `等待 / 待审批`：空心圆环 + 文字；列表左侧 2px `--warning` 竖条。
- `已完成 / 成功`：对勾图形 + 文字，`--success`。
- `草稿`：小方块图形 + 文字，`--muted`。
- `失败 / 拒绝`：三角感叹图形 + 文字，`--danger`。
- `已取消 / 过期`：斜线圆 + 文字，`--muted`。
- 进度/阶段：运行卡内 5 段步骤点（匹配应用 → 排队 → 模型调用 → 产出 → 完成），已完成实心、当前 accent 环、未达 `--border-strong`。
- 节点类型色条（编辑器 3px 顶条）：输入 `--teal`、模型 `--accent`、控制 `#7a5c2e`、输出 `--muted`。

### 2.5 组件清单（T1 交付）

按钮（primary/secondary/ghost/danger/icon/small）、表单控件（label/help/error/readonly/required/字符计数）、
select、checkbox/radio、卡片/区块（带标题槽与操作槽）、表格（表头/紧凑/行 hover/滚动容器/空行）、
状态 pill 与 dot、分段控件（segmented）、工具条（搜索/筛选/排序）、面包屑、页面头、空态、骨架屏、
行内提示/错误/成功条、toast（仅静态样式）、头像（首字）、kbd、分隔线、统计 chip、步骤点、进度条、工具提示。
图标：`OrcharaUI.icon(name)` 扩展到 ≥30 个（导航 14 + 操作 16），统一 24×24 / stroke 1.6 / 圆角端点。

### 2.6 移动端

- ≤720px：顶栏 52px（菜单、标题、更多），内容单列，卡片 padding 16px。
- 底部导航 52px（会话 / 应用 / 运行 / 审批 / 我的）用于对话、运行、审批、个人数据等支持页；
  安全区 `padding-bottom: env(safe-area-inset-bottom)`；触控目标 ≥44px。
- 输入框 16px 防 iOS 缩放（沿用 `mobile.css` 既有规则）。
- 完整画布编辑仍只承诺桌面，保留 `data-mobile-scope="no-full-canvas-editing"` 提示。

## 3. 每页设计意图

见 `C:\Users\19677\AppData\Local\Temp\orchara-ui-polish\notes\PAGE-INTENT.md`（22 页：主任务 / 结构 / 必须展示的事实 / 状态与移动端）。
实现时以该文件为准；与页面现状冲突时，以「不改变业务事实」为优先，并在 Issue 里记录取舍。

### 3.1 导航文案保持现状

`nav.js` 的导航项与中文文案被 `tests/f12/test_t095_page_roles.py` 断言钉死，**本轮不改导航文案与角色规则**：
会话、用途目录检索、运行、审批、应用与创作（无 creator 时「应用（只读）」）、知识库、工具、模型、记忆、个人数据、附件、
管理台、发布审核、状态、退出。改版只动视觉与分组呈现。

## 4. 任务图

父任务见 GitHub Issue（`Part of #<parent>`）。依赖关系：

| 票 | 内容 | 依赖 |
|---|---|---|
| T1 | 设计系统与共享外壳（token/组件/图标/侧栏/顶栏/页头/移动端外壳） | —（阻塞全部） |
| T2 | 对话工作区 | T1 |
| T3 | 应用目录与我的应用 | T1 |
| T4 | 工作流编辑器与画布 | T1 |
| T5 | 运行与审批 | T1 |
| T6 | 数据与能力页（知识库/文件/记忆/个人数据/工具/模型） | T1 |
| T7 | 评测与发布链路（评测/评测详情/发布/审核/版本/调试） | T1 |
| T8 | 身份、状态与管理台（登录/激活/平台状态/管理台） | T1 |
| T9 | 移动端、无障碍与动效精修 | T2–T8 |
| T10 | 总验收、证据与文档 | T9 |

并行策略：T1 先合入集成分支；T2–T8 各自独立 worktree 并行；T9、T10 收口。

## 5. 验收与证据

必须实际执行并在 Issue 记录原始结果：

1. `python -X utf8 product\tools\verify_frontend.py --out product\reports\ui-polish`（真实 Chrome + 真实 DevStack）
2. 全量页面回归，拆两批（f01 的 `from conftest import` 与其它目录同批会冲突）：
   - f01 的 7 个页面相关文件一批（约 36 项）
   - 其余 47 个 `*_pages.py` 一批（约 172 项）
   - 另加非 `*_pages.py` 的页面级测试：`test_canvas_assets.py`、`test_t095_page_roles.py`、`test_c6_01/02/04`、`test_t098_mobile_pages.py` 等
3. `cd product\apps\web\canvas; npm.cmd run build`，`git status` 确认 `assets/canvas.js` / `assets/canvas.css` 与源码同步
4. `cd product; go test ./internal/api ./cmd/api`
5. `python -X utf8 product\tools\sync_spec.py --check`（no drift）
6. 合并前 Skill `code-review`（Standards / Spec 两轴），问题由单个实现子代理统一修复

**证据口径**：stub 结果、静态校验、Node shim 结果不得写成真实模型/生产验收；失败项原样保留；`not_run` 写 `not_run`。
截图写入 `product/reports/ui-polish/`，不覆盖 `product/reports/ui-redesign/` 的历史证据。

## 6. 不可破坏的契约（实现前必读 EXPLORATION.md §2、§5）

- 22 个 HTML 数量不变；每个页面唯一 `<h1>`、唯一 `aria-current="page"`、恰好一个 `link[href="/assets/design.css"]`。
- `<script src="/assets/x.js"></script>` 精确子串与顺序契约；`runstream → runstate-guard → 页面脚本`；不得加 `defer`/`type=module`。
- `data-runstate-paused` 必须写在 `id=` 之后；6 个暂停按钮一个都不能少。
- `nav.js` 会清空重写所有 `<nav>`，只有 `data-static-navigation="true"` 幸存。
- `[data-auth-content]` 只标服务端行/面板；身份失效时会被清空。
- 查询参数白名单只有 `error` / `run` / `version`。
- 全仓库不得出现 `runStream` 字面量（连注释）；用 `runstream.js` 或「共享 Run 状态模块」。
- 画布源码改动必须 `npm.cmd run build` 并提交产物。
- `verify_frontend.py` 的硬编码样式断言随新 token 同步更新，但行为断言（身份、导航、唯一性、无溢出、reduced-motion、logout）不得放宽。
