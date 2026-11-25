# 全产品前端改版验收（#163）

2026-11-09；状态：**passed，仅限本次 UI 改版范围**。验收基线：`97d6e30`（安全压缩提交）；交付分支：`feat/ui-redesign-163`。

## 交付

全部 22 个 HTML 页面及 Vue Flow 画布使用统一的浅色设计系统：灰白背景、靛紫主色、语义状态色、统一字体、间距、圆角、卡片、表单、按钮、表格、焦点与本地 SVG 图标。共享外壳提供分组侧边导航、活动入口、身份信息、面包屑、页面标题和主要操作。

默认 `/` 以 303 跳转 `/chat`，人类账号登录后进入对话工作区；原平台状态页保留在 `/status`。角色决定导航和操作可见性，服务端鉴权逻辑保持现有契约。补齐了此前未注册的 `/assets/nav.js` 静态路由。

创作台保留真实应用与版本状态；内部应用 ID 折叠于详情。画布采用节点库—画布—属性面板，保存入口位于顶部，冲突提示位于画布上方；用途说明、子应用高级映射和 JSON 定义可展开。对话区分会话列表、空状态、输入、交接、附件、请求、队列与消息，技术标识按需查看。管理、发布、评测、审批、知识库、文件及个人数据页面同步覆盖。

设计共识记录在仓库根 `docs/frontend-redesign.md`。没有新增领域概念或不可逆架构选择，因此没有为颜色、间距等可逆细节增加术语或 ADR。

## 视觉证据

[全部桌面截图总览](contact-sheet.png)；完整截图位于 `screenshots/`。

| 页面/场景 | 桌面 | 手机 |
| --- | --- | --- |
| 登录 | [桌面](screenshots/login-desktop.png) | [手机](screenshots/login-mobile.png) |
| 应用与创作 | [桌面](screenshots/apps-desktop.png) | [手机](screenshots/apps-mobile.png) |
| 工作流画布 | [桌面](screenshots/editor-desktop.png) | 完整编辑仍以桌面为目标 |
| 草稿保存冲突 | [桌面](screenshots/editor-conflict-desktop.png) | 同上 |
| 对话 | [空状态](screenshots/chat-desktop.png) / [输入](screenshots/chat-compose-desktop.png) | [手机](screenshots/chat-mobile.png) |
| 运行 | [桌面](screenshots/runs-desktop.png) | [手机](screenshots/runs-mobile.png) |
| 审批 | [桌面](screenshots/approvals-desktop.png) | [手机](screenshots/approvals-mobile.png) |
| 个人数据 | [桌面](screenshots/personal-data-desktop.png) | [手机](screenshots/personal-data-mobile.png) / [横屏](screenshots/personal-data-landscape.png) |

34 张截图包括 22 个页面的桌面视图、2 个额外交互状态、9 个手机视图和1个横屏视图。截图中的应用与账号为验收生成的数据。

## 实际浏览器验收

命令（仓库根执行）：`python -X utf8 product/tools/verify_frontend.py`。结果见 [browser-report.json](browser-report.json)。

- 使用正常 DevStack 启动路径、独立随机命名 PostgreSQL 数据库、真实 Go API/后台进程及 Chrome headless；没有拦截或伪造 API 响应。
- 22 个页面在 1440×1000 下加载，断言唯一主标题、共享样式、唯一活动导航、无页面横向溢出。
- 9 个页面在 375×812 下验证；对话、审批、运行、个人数据额外检查 844×390 横屏。
- 实际完成登录、创建应用、添加模型节点、新建会话和输入、桌面导航折叠、手机导航及 Escape 关闭、点击退出图标撤销服务端会话。
- 通过真实并发保存制造草稿冲突，确认本地修改保留并显示冲突处理入口。
- 键盘跳转入口、减少动态效果和文字对比度检查通过；正文 15.22:1，次要文字 4.97:1，主按钮文字 5.64:1。
- JavaScript 页面异常为 0。评测详情使用不存在的评测 ID，明确验证错误状态；其余页面主要覆盖实际草稿、空列表及表单状态。

环境：Windows；Python 3.12.2、Node v24.13.0、Go 1.27.3、Chrome 157.0.8103.38；画布沿用锁定的 Vue/Vue Flow/esbuild 依赖。

## 页面回归与构建

208 个不同页面回归用例的**最新结果均通过**，详见 [combined-regression.xml](combined-regression.xml)。这是按用例合并的最新结果，来源为以下独立执行记录，不是一轮 208 用例运行的原始输出：

| 记录 | 观察结果 |
| --- | --- |
| [page-regression.xml](page-regression.xml) | 187 项，173 passed / 14 failed；保留脱敏后的失败历史 |
| [identity-regression.xml](identity-regression.xml) | 身份页面单独执行，21 passed |
| [final-regression.xml](final-regression.xml) | 14 项失败的修正复验及会话/画布复核，共 21 passed |

失败历史中的本机数据库口令与一次性测试签名均已脱敏，断言与结果保持原样。

原失败包括旧导航静态链接、开发阶段标题、HTML 属性顺序、协议说明必须出现于按钮页面等断言，以及会话测试替身缺少 `setAttribute`；断言已调整为共享导航、实际业务文案及真实 DOM 语义。既有 T032 的“禁止所有查询参数”断言与已存在的运行深链接冲突，改为只允许 `run` 参数，继续禁止令牌进入 URL。表单、权限、身份失效、状态、调用参数和业务判定的回归约束保留。

执行方式：`rg --files product/tests -g '*pages.py' -g 'test_canvas_assets.py' -g 'test_t095_page_roles.py'` 取得页面测试；F01 的旧 `from conftest import ...` 导入存在跨目录冲突，身份页面单独执行，其余文件批量交给 `python -m pytest ... -q --junitxml=...`。定向复验的用例名与结果完整保存在 `final-regression.xml`。

其他已执行检查：

- `npm.cmd run build`（`product/apps/web/canvas`）：成功，源码与提交的 `assets/canvas.js` / `canvas.css` 同步。
- `go test ./internal/api ./cmd/api`（`product`）：API 包通过；API 命令包无测试文件，构建通过。
- `node --check`：共享 UI、导航与会话脚本通过；画布及其他页面脚本解析由页面回归覆盖。
- `python product/tools/sync_spec.py --check`：no drift；新增的设计共识文档已同步，业务契约没有变化。
- `git diff --check`：通过。
- 原有表单与控件 ID 全部保留；原静态 `logout-link` 改为公共导航按身份生成。

## 范围与运行环境

浏览器验收使用真实本地数据库和 HTTP，但产品 profile 为 `dev-stub`，不代表真实模型质量、正式隔离或完整 v1 业务验收通过。其他浏览器、真实有数据的全部业务状态及未见任务 HCI 测试未在此报告中宣称通过。

测试 API、后台进程、Chrome 均已停止；开发 PostgreSQL 亦恢复到测试前的停止状态。代码、截图与验收证据交付于 `feat/ui-redesign-163` 分支。项目已有的业务/实验阻塞与历史安全约束继续保留。
