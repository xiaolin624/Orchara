# 模型接入、协议、存储与工程测试：组件复用核查

查询日期：2026-10-31。通过 GitHub MCP 获取官方仓库元信息、固定 commit 源文件与部分测试；未安装、启动或运行下述组件。推荐状态不等于已采用/已兼容。

## 1. LiteLLM：多供应商适配候选，不交出平台控制权

官方仓库 [BerriAI/litellm](https://github.com/BerriAI/litellm)，所读 HEAD `8ff495be9624adbb45fd0e4c829d22f401ddab75`。元信息 SPDX 为 NOASSERTION，不能据此判断“没有许可证”：实际 [LICENSE](https://github.com/BerriAI/litellm/blob/8ff495be9624adbb45fd0e4c829d22f401ddab75/LICENSE) 将 enterprise 目录与其他内容区分，非 enterprise 内容采用 MIT；[enterprise/LICENSE.md](https://github.com/BerriAI/litellm/blob/8ff495be9624adbb45fd0e4c829d22f401ddab75/enterprise/LICENSE.md) 有单独生产使用条件。这里记录原文分区，不提供许可适用的法律结论。

已读 `litellm/router.py` 部分导入和 [tests/unit/test_router/test_router_fallbacks.py](https://github.com/BerriAI/litellm/blob/8ff495be9624adbb45fd0e4c829d22f401ddab75/tests/unit/test_router/test_router_fallbacks.py) 的 `test_async_fallbacks_streaming`、`test_default_model_fallbacks`：测试构造 Router、fallback 列表与 `acompletion`，包含 mock_response。只读测试不能证明所有故障/供应商组合满足本平台“半段输出不混拼”的契约；未追完整 Router 调用链或执行测试。

建议：首轮先完成固定协议的模型适配；多供应商确有需求时比较 LiteLLM 开源适配路径。Go 保留授权、预算、审批、取消和调用账本；运行恢复仍由平台负责。若启用 LiteLLM retries/fallback，必须指定唯一策略权威并记录每次物理调用，不能让 SDK、LiteLLM、LangChain、Run 各自独立重试。不能把其企业身份能力当作免费基础模块直接列入交付。

## 2. 对象存储：S3 接口，SeaweedFS 是自部署候选

官方仓库 [seaweedfs/seaweedfs](https://github.com/seaweedfs/seaweedfs)，所读 HEAD `f5989a21fb7c550a0a014a52bd664b10ecc3850d`，已读根 LICENSE 为 Apache-2.0。

- [weed/command/s3.go::S3Options / init / cmdS3](https://github.com/seaweedfs/seaweedfs/blob/f5989a21fb7c550a0a014a52bd664b10ecc3850d/weed/command/s3.go) 展示 filer、IAM 配置、TLS、上传限制等选项；复用的是对象存储服务，不是平台用户 ACL。
- [weed/s3api/s3api_auth.go::getRequestAuthType](https://github.com/seaweedfs/seaweedfs/blob/f5989a21fb7c550a0a014a52bd664b10ecc3850d/weed/s3api/s3api_auth.go) 区分签名/预签名/匿名等请求。
- [auth_config_default_deny_test.go::TestConfigWithoutIdentitiesDeniesAnonymous / TestNoConfigKeepsAnonymousAllowed](https://github.com/seaweedfs/seaweedfs/blob/f5989a21fb7c550a0a014a52bd664b10ecc3850d/weed/s3api/auth_config_default_deny_test.go) 明确区分“指定配置但无身份”与“未配置身份”：后者保留匿名模式。部署必须显式配置身份、关闭公开暴露，并验收匿名拒绝。未实际运行这些测试。

Go 仍管理 artifact 所属、tombstone、运行/尝试归属、下载授权与清理。签名 URL 行为、删除/备份、S3 API 覆盖、反向代理签名路径和一致性需要 P0 实测，不能由“S3兼容”四字推断全部通过。

另查 [minio/minio](https://github.com/minio/minio)：本次 GitHub 元信息 archived=true，license=AGPL-3.0；HEAD `7aac2a2c5b7c882e68c1ce017d8256be2feea27f`。归档是维护信号，不等于产品不能运行；但它不应被未经复核地写成新项目默认长期维护选型。首选部署者已有 S3；需要可复现自部署时先验证 SeaweedFS。此处不是全面对象存储性能/许可证比较。

## 3. MCP：复用协议，工具授权仍是平台职责

官方 [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk)，HEAD `d7845c073a2b9ba5ab2817ecf93d3e45ef24cd14`，GitHub 许可证元信息 MIT。

已读 [src/mcp/client/session.py](https://github.com/modelcontextprotocol/python-sdk/blob/d7845c073a2b9ba5ab2817ecf93d3e45ef24cd14/src/mcp/client/session.py) 前200行和 [src/mcp/client/stdio.py::StdioServerParameters / get_default_environment / stdio_client](https://github.com/modelcontextprotocol/python-sdk/blob/d7845c073a2b9ba5ab2817ecf93d3e45ef24cd14/src/mcp/client/stdio.py)：stdio transport 按传入 command/args 启动子进程，合并有限默认环境和显式 env。这提供协议传输，不自动获得本产品要求的脚本沙箱、对象权限与操作审批。

当前 HEAD 的 stdio 文件是 `stdio.py`；按旧版路径 `stdio/__init__.py` 读取得到404，随后读取实际树与源码纠正。session 的类型包导入也有演进，因此固定 SDK/适配器兼容线后才使用，不复制未经验证的旧教程。

MCP 是后续连接器候选。管理员登记服务/工具、版本/Schema 快照、资源需求和风险级别后才能暴露给 Agent；服务端给出的只读/危险提示不能代替平台审核。stdio 服务不得直接在 Go/Python平台宿主执行，须放入受控执行环境。未追完整 `call_tool`、远程认证和取消路径，不声称已经满足运行协议。

## 4. 可观测性：生产者白名单在前，Collector 过滤在后

官方 [open-telemetry/opentelemetry-collector-contrib](https://github.com/open-telemetry/opentelemetry-collector-contrib)，HEAD `504480882aa709e0a665bfff10a1263505c89fc1`，GitHub 元信息 Apache-2.0。已读 [processor/redactionprocessor/config.go::Config / Validate](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/504480882aa709e0a665bfff10a1263505c89fc1/processor/redactionprocessor/config.go)：属性 key 白名单、值模式、可选非字符串处理及诊断摘要均有配置。空白名单的失败关闭语义适用于该配置说明范围，不能据此推断日志正文、资源属性、trace外所有数据已脱敏。

本次递归树请求未完成而终止，未阅读处理器完整执行与测试。不把它列为已验证隐私方案。推荐先由 Go/Python 仅产生允许的状态/耗时/用量字段，再用本地 Collector 做额外防御。业务运行时间线由平台事件构建，OTel trace 不替代审批/业务状态。

## 5. 系统测试：复用执行工具，真值与故障边界自己定义

- [Shopify/toxiproxy](https://github.com/Shopify/toxiproxy)，MIT 元信息，HEAD `49299d8838525126e09c85ebe0ba89940a3261e5`。已读 [toxics/latency.go::LatencyToxic.delay / Pipe](https://github.com/Shopify/toxiproxy/blob/49299d8838525126e09c85ebe0ba89940a3261e5/toxics/latency.go)、[toxics/timeout.go::TimeoutToxic.Pipe](https://github.com/Shopify/toxiproxy/blob/49299d8838525126e09c85ebe0ba89940a3261e5/toxics/timeout.go)：可注入延迟/抖动或丢弃流量并超时关闭。它处理网络流，无法单独判断邮件是否已送达；需配合受控服务端副作用账本和精确故障时机。
- [microsoft/playwright](https://github.com/microsoft/playwright)，HEAD `deb7e86201e26cb4a8e25191fce5ef2f7a28aa87`。已读 [packages/playwright/package.json](https://github.com/microsoft/playwright/blob/deb7e86201e26cb4a8e25191fce5ef2f7a28aa87/packages/playwright/package.json)：Apache-2.0、Node>=20；源码 HEAD 为1.65.0-next，不作为稳定发行版锁。用于画布/审批/权限/下载的浏览器端到端测试，未执行浏览器测试。
- [grafana/k6](https://github.com/grafana/k6)，HEAD `006583cf0feebccbb106a05ef3ea705759b4be00`，GitHub元信息AGPL-3.0。已读 [go.mod](https://github.com/grafana/k6/blob/006583cf0feebccbb106a05ef3ea705759b4be00/go.mod)：该开发HEAD要求Go1.26；选独立测试工具稳定发行版，不因此提高平台编译器要求。此处仅核查依赖/许可线索，未阅读负载执行核心，未测HTTP/SSE性能。

上述工具仅进入开发/CI/压测环境，不给生产执行增加默认服务。Go test/pytest 负责不变量、确定性输出与契约；质量评测库负责语义指标，二者不能互相代替。

## 未覆盖与可推翻条件

未完成全量许可证/供应链审计、所有协议/模型/存储兼容矩阵、真实 Linux 部署与性能测试。必要负例若失败，应禁用相关能力或更换组件；不能通过放宽授权/删除规则来适配组件默认行为。项目stars只是仓库活跃线索，不构成正确性或采用理由。
