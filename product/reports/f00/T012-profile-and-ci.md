# F00 T012 验收：按环境 profile 启动并重现一次 CI 验收

- 任务：F00 / `T012`
- 状态：**passed**
- 代码：`internal/config/`、`cmd/api/main.go`（启动接线）、`tools/ci_checks.py`、
  `probes/agent_min/agent_min/__main__.py`（stub 账本）
- 测试：`pytest product/tests/f00 -q` → **15 passed**；`go test ./internal/config/` → **9 passed**

## 1. profile 与启动校验

`config.Load` 要求显式声明 `ORCHARA_PROFILE`，取值限于
`dev-stub` / `linux-sandbox` / `real-model` / `capacity-stub`。校验规则：

| 规则 | 结果 |
|---|---|
| 未声明 profile | 拒绝启动，错误点名 `ORCHARA_PROFILE` |
| 未知 profile | 拒绝启动 |
| 缺 `ORCHARA_DB_DSN` | 拒绝启动（不会先起服务再"稍后失败"） |
| `real-model` 缺 endpoint / secret 引用 | 拒绝启动 |
| 非 `linux-sandbox` 却启用任意脚本 | 拒绝启动 |

实测：`test_startup_is_refused_without_a_profile` 起真实二进制、去掉 profile，
进程非零退出且输出含变量名，**且不含数据库口令**。

## 2. secret 只能是引用

`ParseSecretRef` 只接受 `env:NAME` 或 `file:NAME`（部署者受控目录内的裸文件名）。
以下一律拒绝：`https://…`、`file:///etc/passwd`、`/etc/passwd`、`C:\secrets\key`、
`../secrets/key`、裸值。配置对象只保留引用，不携带凭据值——业务输入无法改凭据来源。

## 3. stub 输出明确标记 + 独立账本

- `/api/status` 返回 `profile` 与 `stub_results`：dev-stub 下为 `"dev-stub"` / `true`。
- 探针每次写报告时向 **append-only** 的 `reports/p01/stub-ledger.jsonl` 追加一行：
  `{recorded_at, probe, profile, stub: true, python, host_platform, report_digest}`。
  测试断言"两次运行两行"（只追加不覆盖）以及 `stub: true` 必须存在。

这样 stub 结果与真实结果在事后仍然可区分，不依赖人记得当时跑的是什么。

## 4. 干净环境执行基础检查

```powershell
python product/tools/ci_checks.py            # 全部检查
python product/tools/ci_checks.py --quick    # 无 Go / 无数据库时
python product/tools/ci_checks.py --json     # 机器可读
```

检查项：规格漂移、Python 快速测试、（非 quick）Go vet/测试、集成测试。
没有 Go 或数据库时对应项记 `skipped`，**不计入 passed**；任一 failed 则退出码非零。
`test_ci_checks_quick_reports_a_machine_readable_verdict` 实测该契约。

## 5. 失败日志只有获准元数据

`/api/status` 的字段集合被测试限定为
`status/service/version/profile/stub_results/uptime_seconds/checks`，
每个 check 只允许 `status/detail`；依赖失败的原因是粗粒度的（`unavailable`），
不含 DSN、主机口令或堆栈。

## 6. 边界

- 这是骨架：`dev-stub` 下没有真实模型调用，`real-model` 只完成**配置校验**，未接供应商。
- 本票只涉及 API 进程；worker / maintenance / supervisor 的骨架与迁移见
  `reports/f00/T008-product-skeleton.md`。迁移文件随其他票增加（`0001`–`0004`），
  这里不再逐个数列，以免与本报告写成时的快照分叉。
- Redis / 对象存储仍未接入，故 profile 的依赖就绪只覆盖 PostgreSQL。
