# 本地开发凭据清理记录（C1–C6 架构改进）

日期：2026-11-09。范围：`product/reports/architecture/` 下的验收原始输出。

## 发现

C5/C6 批次的部分 pytest 原始输出把真实测试失败对象（`Stack`）打印进了证据文件，
其中包含本机隔离开发 PostgreSQL 的 owner 密码（来自被 gitignore 的
`product/.env.local`）以及每个测试临时生成的 `t024-signing-*` 签名值。共 6 个文件：

- `C5.03-after-observations.txt`
- `C5.04-execution-cleanup.txt`
- `C5.04-merge-validation.txt`
- `C6.01-f03-pages-final.txt`
- `C6.02-f03-t032-baseline-red.txt`
- `C6.02-regressions-f03-f02a-f12-pages.txt`

C2.03 的 `C2.03-red-sealing.txt` 在其票内已单独脱敏（临时 fake-provider bearer
与截断 DSN），本次复核确认该文件无残留。

## 本次处理

- 在最终树中把 6 个文件里的密码、`signing_key='t024-signing-*'` 与
  `postgresql://user:password@` 统一替换为 `<REDACTED>` / `<REDACTED-LOCAL-DB-PASSWORD>`；
  证据的行数、命令、断言与结果字节之外的内容不变。
- 复核：`git grep` 全仓（`product/`）已无本机密码与 `t024-signing-*` 值；
  剩余 `postgresql://user:pw@` 均为产品单测中的固定占位符，不是可用凭据。
- 未改写历史：这些值仍存在于本分支此提交之前的提交对象中。

## 发布约束

- 该分支**未 push**，请勿直接 push；如需远端发布，先改写历史或使用
  `git merge --squash` 把最终树合并到 main，避免把历史中的本地凭据带入远端。
- 本清理不改变任何产品代码、契约、迁移或测试断言；只影响证据文件中的凭据文本。
- 真实供应商凭据从未进入仓库；本记录只涉及本机开发数据库口令与一次性测试签名。
