# P01 T002 验收：从锁定依赖运行只有授权工具的 Agent

- 任务：P01 / GitHub Issue #3（`T002`）
- 状态：**passed**（stub 模型；控制面与依赖锁定已实测）
- 环境：Windows 11 / Python 3.12.2 / 探针 venv `product/probes/agent_min/.venv`
- 机器可读结果：[`agent-min-probe.json`](agent-min-probe.json)

## 1. 干净环境安装

```powershell
python -m venv product/probes/agent_min/.venv
python -m pip install --dry-run --report pip-report.json -r requirements.in   # 47 包解析
python product/probes/agent_min/lock.py pip-report.json requirements.lock.txt  # 47 个 sha256 锁定
product/probes/agent_min/.venv/Scripts/python.exe -m pip install --require-hashes -r requirements.lock.txt
# exit 0
product/probes/agent_min/.venv/Scripts/python.exe -m pip check
# No broken requirements found.
```

解析结果（真实版本，非"latest"）：

| 包 | 版本 |
|---|---|
| langgraph | 1.2.4 |
| langgraph-checkpoint-postgres | 3.0.5 |
| pydantic | 2.12.4 |
| jsonschema | 4.25.1 |
| psycopg | 3.3.6 |

共 47 个包（P02 追加 `psycopg[binary]` 后为 48 个，见下），每个都带
`--hash=sha256:<64 hex>`；锁文件由真实 pip 解析报告生成
（`lock.py`），不是手写、不是取当前已装版本。Python 3.12 优先线已实测（3.12.2），
未出现需要降级的冲突。

复现过程中修复的两个真实缺陷（保留证据价值）：

1. `pip --report` 的 `archive_info.hash` 在当前 pip 版本形如 `sha256=<hex>`，
   而 requirements 需要 `sha256:<hex>`；`lock.py::_normalise_hash` 负责归一化。
2. 锁文件若以 CRLF 写入，pip 的行连接（`\`）解析失败；`lock.py` 显式以 LF 写出。

后续在 P02 追加 `psycopg[binary]==3.3.6`：纯 `psycopg` 需要系统 libpq，而本机没有；
`[binary]` 自带 libpq，使探针不依赖系统 PostgreSQL 安装。锁文件随之变为 48 个包，
并改为列出每个版本的**全部** artifact 哈希（跨平台可安装），详见 T004 报告。

## 2. 授权工具集

| 工具 | 作用 | 约束 |
|---|---|---|
| `read_dataset` | 按**名字**读取 fixture 数据集 | 只允许 fixture 目录内，拒绝路径穿越 |
| `sum_amounts` | 用 `Decimal` 精确求和 | 拒绝 float 金额，只接受十进制字符串 |

未注册的工具（探针中用 `delete_records` 扮演）一律拒绝。

## 3. 实测场景（全部 stub，未调用任何供应商）

| 场景 | 结果 |
|---|---|
| `happy` | completed；2 次工具调用；结构化结果 `{"total": "200.00", "count": 2}` |
| `unauthorized` | failed；`delete_records` 被拒绝并记录；未继续执行后续步骤 |
| `retry` | 同一调用重放被计为 1 次隐藏重试（`hidden_retry_detected=true`） |
| `bad_input` | float 金额触发 `TypeError`，整轮 failed，不静默吞掉 |

探针自检断言（`agent-min-probe.json` 的 `assertions`）全部为 `true`。

## 4. 覆盖的验收条件

- [x] 干净环境安装成功（`--require-hashes` + `pip check`）
- [x] 精确版本/校验和可追溯（47 个 sha256 锁定 + 安装后版本比对测试）
- [x] Python 3.12 优先探测，无冲突需实证
- [x] 一次最小调用返回结构化结果
- [x] 实际工具列表符合允许集（子集断言 + 拒绝未注册工具）
- [x] 隐藏重试和意外工具调用能被捕获

## 5. 边界（不要读成已交付）

- **stub 模型**：没有调用任何真实供应商，不能据此声明模型质量、延迟或成本。
- 只验证了控制面（允许集、重试审计、步骤预算、结构化输出），不是平台 worker，
  也尚未接入 LangGraph 图执行或真实 checkpointer（属 P02）。
- 测试：`python -m pytest product/tests/p01 -q` → 9 passed。
