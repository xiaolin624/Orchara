# 从零建立开发环境与产品仓库

以下是**待实现步骤/命令契约**，不表示已有可运行平台。规格包的校验命令现在可执行；`cmd/api`、Compose、迁移和业务测试命令由后续任务创建。

## 1. 路径与探测

当前用户上游副本位置可用`F:/agent-platform-upstream`，规格当前位置可用`E:/haiyi/agent-project-design`。这两个是本次机器示例，不写死进产品代码。产品目录默认选规格同级`agent-platform`，用户另指定时遵从。

先检查Git/Python/Go/Node/包管理器、Docker/Compose、Linux执行宿主及网络。记录版本和可用性，不打印环境变量或密钥。本次本机曾发现Git/Python/Node，未在PATH发现Go/Docker/psql；接手时重新检查。

最低实施策略：Python先验证3.12兼容线；Go、Node选择满足实际锁定依赖的受支持稳定发行版，读各包go.mod/engines后记录精确版本，不能把上游dev/next当稳定发行。数据库先使用受支持PostgreSQL发行版与对应pgvector构建；实际major、镜像digest和扩展版本在P01记录。不要在文档中虚构已解析版本。

## 2. 规格工具可执行命令

从规格目录执行：

```powershell
python -m venv tools/.venv
tools/.venv/Scripts/python.exe -m pip install -r tools/requirements.lock.txt
tools/.venv/Scripts/python.exe tools/build_contracts.py
tools/.venv/Scripts/python.exe tools/build_reuse_contracts.py
tools/.venv/Scripts/python.exe tools/build_handoff_contracts.py
tools/.venv/Scripts/python.exe tools/validate_contracts.py
tools/.venv/Scripts/python.exe tools/validate_handoff.py
```

Linux把解释器换为`tools/.venv/bin/python`。无Python时先准备环境；这些命令不安装平台组件、不启动模型或数据库。以后优先运行校验，只有修改生成源时才重建，以免无关XLSX元数据改变。

## 3. F00应建立的代码骨架

```text
agent-platform/
  AGENTS.md / PROGRESS.md / README.md
  docs/spec/                    # 本规格完整相对目录，排除.venv和缓存
  contracts/                    # 从docs/spec/contracts复制的运行契约快照
  cmd/api/ cmd/maintenance/ cmd/admin/
  internal/{identity,policy,catalog,requests,routing,execution,background,
            capabilities,artifacts,memory,knowledge,evaluation,audit}/
  db/{migrations,queries}/
  worker/platform_worker/{compiler,nodes,agents,checkpoints,transport}/
  apps/web/src/{canvas,chat,catalog,runs,evaluation,admin}/
  tests/{contracts,integration,faults,security,load,acceptance}/
  experiments/routing_sft/
  deploy/{compose,images,policies}/
  reports/{p0,acceptance,security,capacity}/
```

spec和运行contracts不能悄悄分叉：F00创建同步/校验命令，contract变更同次提交更新。业务模块先放单体，尚无替换需求的内部函数不堆抽象接口。外部模型、对象存储、沙箱与时钟等真实可替换依赖在调用接口注入。

产品根AGENTS应指向`docs/spec/HANDOFF.md`和根PROGRESS，不能原样复制规格AGENTS的相对链接。根PROGRESS是后续实现进度权威，`docs/spec/PROGRESS.md`只保留接手初始快照。

## 4. 配置和凭据

使用`deploy/config.example.yaml`作为字段模板，示例值不含凭据。产品配置加载器在F00/F01实现；路径/端点由部署配置提供，不允许业务输入改任意base_url。SecretRef只接受部署者登记的env或受限secret文件标识；Go能力代理读取真实值，Python/脚本只拿短期能力令牌。

首个管理员由`cmd/admin bootstrap`一次性创建：本地连接平台数据库，在单事务确认不存在管理员后，读隐藏stdin密码并创建principal/user/role。不能提供公开无认证bootstrap HTTP。重复执行拒绝；不在命令行参数/日志打印密码。

管理员邀请其他用户：POST admin/users返回一次性激活令牌；平台仅保存哈希及过期/消费状态，交付给管理员手动转交。activation接口凭令牌设密码，单次消费。无需配置邮件服务，也不擅自发送邮件。

## 5. 环境profile和启动顺序

| profile | 能力 | 不得声称 |
|---|---|---|
| dev-stub | PG/Redis/对象存储＋受控模型stub；预置可信工具 | 真实模型质量、任意脚本隔离、20并发已通过 |
| linux-sandbox | 上述＋真实Linux OpenSandbox/runsc、独立脚本宿主 | 未跑负例就称安全 |
| real-model | 获批模型/embedding/judge，凭据由部署者提供 | 由stub吞吐外推供应商能力 |
| capacity-stub | 明确20顶层执行单元、独立子槽和足够stub额度 | 仅20排队等于20执行 |

创建命令顺序：数据库/对象存储可用→编号迁移/checkpointer独立迁移→管理员bootstrap→可信后台handler和supervisor→Go API→Web。模型候选注册→兼容验证通过→应用调试/评测→审核发布→真实用户请求。具体闭环见IMPLEMENTATION-DETAILS。

F15A创建可运行Compose与命令，文档再更新真实启动说明；当前不伪造`docker compose up`已可启动平台。正式隔离只在Linux验收，Windows可作为开发端连接Linux执行服务。

迁移/运行凭据分开：migration DSN执行平台编号迁移、扩展/角色授权及checkpointer.setup；Go运行DSN只业务表DML，worker checkpoint DSN只其schema的DML。按固定库要求单独处理checkpointer的autocommit/迁移，不能未经检查包进一笔Go迁移事务。配置连接search_path，P02验证实际建表与读写位置，关闭public任意CREATE，防止表落错schema；不把数据库角色权限发给脚本沙箱。

## 6. 缺外部条件时

没有模型key：实现/测试stub适配、注册/校验流程，真实模型报告blocked。没有Linux执行宿主：推进DSL、授权、API、UI与测试，把任意脚本上传保持关闭；不要用未验证Docker模式替换runsc。缺GPU：先完成数据/基线/训练manifest，训练not_run，不取消已确认离线实验交付。
