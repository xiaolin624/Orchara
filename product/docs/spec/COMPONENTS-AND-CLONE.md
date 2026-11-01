# 组件准备、克隆与安装清单

本轮官方仓库和固定阅读版本见 contracts/components.json。分类不表示所有项目都必须安装：研究源码、在线依赖、独立服务和测试工具是不同交付物。

## 1. 源码阅读顺序

| 顺序 | 项目 | 先看什么 |
|---|---|---|
| 核心 | DeepAgents、LangGraph、Vue Flow、OpenSandbox | 模型/工具入口、检查点恢复、画布导出、沙箱API与默认配置 |
| 平台参考 | Coze Studio优先；Dify/Flowise按需 | 画布转换、变量池、运行准备、分支/人工输入测试、导出过滤 |
| RAG/记忆 | LlamaIndex、Docling、Mem0 | 分块来源、Native PDF、提取/确认、历史删除与存储副本 |
| 评测 | DeepEval优先；Ragas/Promptfoo按适配需要 | 本地用例路径、judge、遥测/上传、202异步适配 |
| 任务集 | τ²-bench、AgentDojo | task+环境+checker，不只复制问题 |
| 后续实验 | LlamaFactory | 固定训练配置、数据split、adapter与评测产物 |

完整固定源码路径见 research 下四份新增审计，不要求先把所有源码读完再写第一条纵向切片。

## 2. 克隆脚本

从文档目录运行：

```powershell
# 只准备四个核心上游参考副本
./tools/clone-upstreams.ps1 -Destination F:/agent-platform-upstream -Groups core

# 需要时再加研究、评测或训练组；组内项目并不因此成为生产依赖
./tools/clone-upstreams.ps1 -Destination F:/agent-platform-upstream -Groups research,evaluation,training
```

脚本仅克隆缺失目录，已有目录跳过，不fetch/reset/checkout、不修改现有工作区。默认不自动执行；本轮未运行克隆。它使用partial clone保留历史访问能力；服务器/代理若不支持过滤，需自行选择普通clone。大仓下载资源与路径长度应提前评估。

当前用户使用F:/agent-platform-upstream，其他机器可传任意目标目录；省略参数则使用当前目录的agent-platform-upstream子目录。既有上游副本先核对commit/工作区，不在其中写产品。缺少clone不阻塞从官方包解析兼容依赖，也不把clone目录存在当环境安装完成。

## 3. 安装而非克隆

- chi/pgx/sqlc、Vue/Vite/Element Plus、OTel：通过Go/前端/Python包管理锁版。
- PostgreSQL、pgvector、Redis、S3服务：固定镜像/发行版部署；不要求先编译数据库源码。
- runsc：在Linux执行宿主安装并验收；clone gVisor源码不等于已安装运行时。
- Playwright/k6/Toxiproxy：开发与CI专用版本；不作为生产Agent能力暴露。
- 解析器/embedding/judge：按最少extra、模型revision与数据目的地固定，不隐式下载或使用云默认值。

所读某些仓库HEAD包版本为dev/next，不能直接复制成安装命令。P0才生成真实packages/镜像锁；现有versions.lock.example.json只是阅读锚点。

## 4. 分发与来源

复制代码和数据前记录对应路径许可证；平台代码、企业目录、权重和数据分别检查。Dify/Flowise/LiteLLM有需区分的许可边界；MinIO本次元信息已归档，不默认作为新项目的长期维护选型。详细来源在研究文档，当前没有宣称完成整体许可审查。
