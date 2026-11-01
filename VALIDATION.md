# 文档契约验证记录

最近审阅日期：2026-11-01。执行环境：本地Windows、Python3.12，隔离虚拟环境tools/.venv。精确依赖见tools/requirements.lock.txt；文档契约结果见 [validation-results.txt](validation-results.txt)，交接检查见handoff-validation-results.txt，隔离复制检查见package-validation-results.txt。后两项由下述新增命令产生，不是产品运行结果。

## 已执行

- 所有契约/样例 JSON 可解析；DSL 与事件通过 JSON Schema 元校验。
- 三条合法工作流通过 Schema 和有限图检查；环、缺工具配置、未知引用、重复ID、错误分支端口的负例被拒绝。
- OpenAPI 3.1 通过独立 `openapi-spec-validator` 校验，引用、路径参数与 operationId 检查通过。
- SQL 通过 `pglast` PostgreSQL 语法解析，**没有连接真实 PostgreSQL 执行**。
- CSV/XLSX 数据一致，独立 Decimal 计算与公开统计期望一致，**未执行被设计的统计工具或 Agent**。
- Markdown 本地文件目标存在。未逐个校验标题锚点或重新联网检查所有历史来源链接。
- v0.2新增组件清单的证据文件/commit格式检查、两个评测用例Schema正例和一个空checker反例；这不是运行了TaskRunner、Agent或外部benchmark。
- 克隆脚本仅做PowerShell语法解析；未执行克隆、fetch或工作区切换。

有限图检查不是完整发布校验器：尚未证明类型支配关系、嵌套分支编译、子应用依赖、ACL 或 LangGraph 实际行为。通过静态检查不能替代 P0。

## 重复命令

从本目录运行，Windows：

```powershell
python -m venv tools/.venv
tools/.venv/Scripts/python.exe -m pip install -r tools/requirements.lock.txt
tools/.venv/Scripts/python.exe tools/build_contracts.py
tools/.venv/Scripts/python.exe tools/build_reuse_contracts.py
tools/.venv/Scripts/python.exe tools/build_handoff_contracts.py
tools/.venv/Scripts/python.exe tools/validate_contracts.py
tools/.venv/Scripts/python.exe tools/validate_handoff.py
tools/.venv/Scripts/python.exe tools/check_handoff_package.py
```

Linux 使用 `tools/.venv/bin/python`。锁文件来自本次 Windows 环境，跨平台 wheel/版本兼容须实际安装确认。XLSX 容器元数据可能含生成时间，重建不保证二进制 hash 不变；比较的是业务单元格。

## 尚未执行/环境限制

当前 PATH 未发现 Go 和 Docker。未构建 Go/Python 业务服务、运行 Linux/OpenSandbox/gVisor、部署数据库/对象存储、调用真实模型、做压测、恢复/删除实验或微调。没有应用镜像或已上线服务。P0 实验中取得的结果才能更新这些状态。

本轮确认的是开发契约的静态可读性及部分一致性；运行可行性和用户价值尚未被验证。能推翻方案的证据与停止条件见 REVIEW。

Q45/Q46已确认；v0.3补齐新会话入口、任务DAG、启动/模型/评测/后台任务闭环和跨契约约束，详见DOCUMENT-AUDIT。候选仍为gated_candidate，产品运行仍not_run；没有启动平台或候选服务。隔离复制检查仅证明当前工具在不访问原目录/本机技能文件时能校验副本，不证明依赖新安装或完整业务旅程通过。
