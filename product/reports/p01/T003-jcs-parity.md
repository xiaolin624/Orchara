# P01 T003 验收：Go/Python 对同一清单给出相同指纹

- 任务：P01 / GitHub Issue #4（`T003`）
- 状态：**passed**
- 依据：RFC 8785（JCS），非 `json.dumps(sort_keys=True)`
- 证据：[`jcs-agreement.json`](jcs-agreement.json)（机器可读）、`probes/jcs/`
- 测试：`python -m pytest product/tests/p01/test_jcs_vectors.py -q` → **116 passed**

## 1. 实现

| 端 | 位置 | 依赖 |
|---|---|---|
| Python | `probes/jcs/python/jcs.py` | 仅标准库 |
| Go | `probes/jcs/go/jcs.go` + `main.go` | 仅标准库（`go.mod` 无第三方依赖） |

两端命令行输出同一契约：成功 `{"canonical","sha256","length"}`，失败
`{"error":"<code>","message"}` 且退出码 1。

## 2. 黄金向量来自 RFC，不来自实现

`probes/jcs/vectors.json` 共 40 条，来源标注在每条 `source` 字段：

- §3.2.2/§3.2.3/§3.2.4 官方样例（含字节十六进制）；
- §3.2.3 属性排序验证数据（7 个属性）；
- 附录 B 的 24 个 IEEE 754 数字序列化样例（含 `-0`→`0`、`5e-324`、`1e+21`、
  `1424953923781206.2` 的 round-to-even）；
- 拒绝类：重复键、`NaN`、`±Infinity`、孤立代理项、尾随垃圾。

跨语言结果：**40/40 一致**，0 mismatch（`jcs-agreement.json`）。
RFC 样例规范字节的 SHA-256：`2d5e01a318d0f0879ab568c4be289c8b1f64ef8921a53c6277d5e069978baacb`。

## 3. 为什么 `sort_keys` 不算实现

测试里保留了三个反例断言（`test_plain_sort_keys_is_not_sufficient` 等）：

| 差异点 | Python 默认行为 | JCS |
|---|---|---|
| 属性排序 | 按码点 | 按 **UTF-16 code unit**（U+1F600 的 D83D 早于 U+E000） |
| 非 ASCII | `ensure_ascii=True` 转义 | 原样输出 UTF-8 |
| 浮点 `1.0` | `1.0` | `1` |
| `-0.0` | `-0.0` | `0` |
| 大数指数 | `1e+30` 形式取决于 repr | ECMAScript `Number::toString` |
| 重复键 | 静默保留最后一个 | 拒绝 |

## 4. 两端必须拒绝同一批输入

| 错误码 | 触发 |
|---|---|
| `duplicate_key` | 同一对象中重复属性名（含嵌套） |
| `non_finite` | `NaN` / `Infinity` / `-Infinity` |
| `invalid_unicode` | 孤立代理项（值或属性名） |
| `invalid_utf8` | 输入字节不是合法 UTF-8 |
| `invalid_json` | 语法错误、尾随数据 |

实现要点：Go 的 `encoding/json` 会把孤立代理项静默替换成 U+FFFD、并在能命名之前就
拒绝 `NaN`，因此 Go 端先做一次词法预扫描（`preScan`），再交给解码器；这样两端能给出
**相同的错误码**，而不是一端 `invalid_json`、另一端 `non_finite`。

## 5. 覆盖的验收条件

- [x] 两语言 golden vectors 字节和 SHA256 一致（40/40）
- [x] 普通 `sort_keys` 不当作充分实现（三个反例断言 + UTF-16 排序向量）
- [x] 异常输入被两端拒绝，且错误码一致

## 6. 边界

- 尚未接入执行器/制品清单的真实哈希计算（属 F00 及之后），本票只交付规范化算法与
  跨语言一致性证据。
- JCS 不做 Unicode 规范化：U+FB33 保持预组合形式（曾在本票向量里写错，已修正并记录）。
- Go 端暂未单独跑 `go test`；Go 的行为通过 Python 侧对 Go CLI 的 40 条向量逐条比对覆盖。
