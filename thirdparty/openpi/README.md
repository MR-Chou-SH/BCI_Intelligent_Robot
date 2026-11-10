# OpenPI / π0.5

`upstream/` 从 `.cache/upstream/dexjoco/openpi/` 独立复制，保留上游目录、许可证、模型和 websocket client；通过 `thirdparty/bootstrap.py openpi` 准备。没有修改原 `.cache` 源码。

该目录为后续 OpenPI π0.5 训练预留。当前不安装其 JAX/CUDA/Torch 大型依赖，也不在 RTX4060 上尝试 π0.5 微调。当前优先打通 SmolVLA；未声称已完成 π0.5 的厨房训练配置或服务端适配。

未来复用厨房相同的25Hz、16维绝对关节/夹爪、图像和英文任务边界，转换到 OpenPI 的模型输入及 action padding；不复制 DexJoCo 的44维末端/灵巧手动作。训练环境独立放在本目录 `.venv`，不与 SmolVLA 或仿真混装。
