# 离线 LoRA 实验：TRL 与 MLflow 的参考边界

查询日期：2026-10-31。当前主产品采用 Go 控制面 + Python 执行面；离线 LoRA 实验仍是待定扩展。本轮只查源码/官方文档，没有下载权重、执行训练或测量效果。这里不要求将两个项目都部署成平台服务。

## TRL：参考训练机制与最小脚本

仓库：[huggingface/trl](https://github.com/huggingface/trl)。读取 HEAD `a03b8fe31d5f68b645ccf0d6b95c1f1245b5006e`（2026-10-30）。

**已核对源码。** `trl/scripts/sft.py::main` 加载 dataset，创建 `SFTTrainer` 并传入训练/验证 split、quantization_config 和 `get_peft_config(model_args)`；随后 `trainer.train(resume_from_checkpoint=...)`、`save_model(output_dir)`。只有显式 `push_to_hub` 时才上传 Hub。[固定最小训练脚本](https://github.com/huggingface/trl/blob/a03b8fe31d5f68b645ccf0d6b95c1f1245b5006e/trl/scripts/sft.py#L66)。

**官方接口。** [SFTTrainer 文档](https://huggingface.co/docs/trl/en/sft_trainer) 给出 `SFTTrainer(..., peft_config=LoraConfig())`；数据可用 messages 或 prompt/completion。`assistant_only_loss` 要求聊天模板提供 generation mask，不能只设置一个参数就假定所有模型模板都兼容。[CLI 文档](https://huggingface.co/docs/trl/en/clis) 提供 `trl sft --config sft_config.yaml`；可将训练配置保存在仓库中。示例是接口说明，没有证明其模型/数据适合本产品。

**适合借鉴。** 若已经使用 LLaMA-Factory 完成第一轮 LoRA，TRL 更适合作为理解 loss masking、数据整理、PEFT 接口、训练/保存边界的参考，而非同时维护第二套训练入口。若要把方法控制在短 Python 脚本内，TRL 本身也可作为训练依赖；两者是选择关系，不是完成实验必须串联的服务。

**没有证明。** 训练 loss 或 token accuracy 下降/上升，不等于实际 Agent 路由、工具参数、任务完成率改善；LoRA 也不会自动解决缺工具、权限错误、工作流设计错误。需要固定基模/推理配置、独立留出任务、与原模型及最佳可行提示方案对照，并报告无效工具调用、拒绝/澄清以及退化。

## MLflow：参考实验溯源，暂不等于平台必需服务

仓库：[mlflow/mlflow](https://github.com/mlflow/mlflow)。读取 HEAD `64d84ff97633e8734e9b7312282a3c317fc82209`（2026-10-31）。

**官方能力。** [Tracking 文档](https://mlflow.org/docs/latest/ml/tracking/) 将 run 作为记录参数、指标、代码版本与产物的单元；[Model Registry 文档](https://mlflow.org/docs/latest/ml/model-registry/) 管理模型版本、tags、可变 aliases，以及其来源 run。Registry 解决的是“哪个产物来自哪次实验”，不会自动审计实验是否公平、数据是否泄漏或 Agent 是否有效。

**源码核对。** `MlflowClient.log_inputs` 将 DatasetInput/模型输入记录到 run；`create_model_version` 接受 source URI 和可选 run_id；`set_registered_model_alias` 将名字指向版本。[固定 client.py：log_inputs](https://github.com/mlflow/mlflow/blob/64d84ff97633e8734e9b7312282a3c317fc82209/mlflow/tracking/client.py#L2818)、[create_model_version](https://github.com/mlflow/mlflow/blob/64d84ff97633e8734e9b7312282a3c317fc82209/mlflow/tracking/client.py#L4711)、[set_registered_model_alias](https://github.com/mlflow/mlflow/blob/64d84ff97633e8734e9b7312282a3c317fc82209/mlflow/tracking/client.py#L5596)。这些是记录接口，不会强制调用者提供全部可复现材料。

**适合借鉴。** 第一轮离线实验可以先维护一份 manifest：基模 revision、训练集与验证集版本/哈希、代码 commit、完整配置、seed、adapter 产物哈希及评测结果。多次实验需要检索/比较时再选择 MLflow 记录；不必为了“有 MLOps”立即增加常驻服务。Registry alias 会变化，已发布应用若要求版本固定，应绑定确定模型/adapter 版本，而不是仅保存 champion 别名。

## 与当前数据原则的接口

已确认默认脱敏观测及用户任务数据删除。**推断：** 脱敏 trace 不是天然合格的 SFT 样本，操作日志也不能自动变成“正确答案”；失败尝试、用户纠正与工具结果需要区分。原始业务数据进入训练导出、实验产物保留及删除传播，必须与已确认的数据范围一致；不能因为部署训练工具就默认复制用户任务数据。

本轮消除的不确定性：开源训练/记录接口是否可以小范围复用，及二者的责任边界。尚未证明：存在足够合法可用的高质量样本、LoRA 相比提示/工具修复有收益、硬件成本与结果波动。能推翻继续实验的证据包括：留出集收益消失、工具调用退化、泄漏/重复样本造成假提升，或用少量提示改动即可达到同等结果。
