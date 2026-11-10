# SmolVLA

训练实现位于本目录 `train.py`；模型、处理器与 RTC 实现来自本目录 `upstream/src/lerobot/`。独立 `.venv` 执行学习和推理，仿真 `.venv` 执行 MuJoCo。安装与来源见 [thirdparty 说明](../README.md)。

默认面向 RTX4060 8GB：CUDA、batch8、冻结 VLM/视觉编码器、训练 action expert 和 state projection；可训练参数保持 float32，使用 bfloat16 autocast。batch1/8短测峰值显存1.95/3.08GiB，样本吞吐1.71/2.40每秒；这是效率测试，不代表任务成功率改善。保存完整模型和处理器，不改为只存 bf16 训练权重。不存在的 GPU 会报错，不静默切 CPU。

`--steps`、`--batch-size`、`--learning-rate`、`--save-every` 可配置；输出目录必须全新。当前训练入口面向独立单任务试验，不支持从 optimizer 恢复训练；保存的是可加载的模型/处理器 checkpoint。完整训练断点续训与正式数据划分需另行实现，不能把短 smoke 当作成功率评估。

RTC 是推理时的去噪引导，不需要另训一个 RTC 模型。在线程序调用 `predict_action_chunk`，使用模型原始归一化动作前缀，输出再由训练同一 postprocessor 还原成16维绝对关节/夹爪目标。[运行步骤](../../docs/kitchen_platform_v3_20261020/SMOLVLA_PILOT_CN.md)
