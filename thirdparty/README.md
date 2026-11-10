# VLA 上游代码与环境

项目的仿真与策略学习使用不同解释器：

| 路径 | 职责 |
| --- | --- |
| 项目 `.venv/` | 原 MuJoCo 仿真、轨迹采集/重放、图像渲染、在线执行；默认没有 Torch/RL |
| `thirdparty/smolvla/upstream/` | 从本机 LeRobot 复制的独立源码快照，含 SmolVLA 和原生 RTC；不修改外部仓库 |
| `thirdparty/smolvla/.venv/` | SmolVLA/LeRobot 数据转换、微调、推理；CUDA Torch |
| `thirdparty/smolvla/train.py` | 项目单任务微调入口，使用上游真实 SmolVLA 与处理器 |
| `thirdparty/openpi/upstream/` | 从 DexJoCo 带的 OpenPI 复制的源码快照；未来 π0.5 路线 |
| `thirdparty/discodemo/upstream/` | DiscoDemo的独立FlashSAC/METRA/逆向课程快照；不导入Isaac训练器 |
| `thirdparty/discodemo/train.py` | 厨房状态RL训练入口，使用项目`.venv`的显式可选依赖 |
| `integration/kitchen/vla/` | 项目拥有的相机/数据映射、协议、模型适配和仿真执行；不把它们写进上游库 |

快照和环境不提交 Git；首次准备会在本机生成 `source_receipt.json`，逐文件绑定来源与 SHA256，后续不覆盖或静默更新。上游 LICENSE 保留在快照中。当前来源是 LeRobot `34d81fbaa311f58c048c6badebff221ac6f55e9c`，以及 DexJoCo `8d23b0fab23b17a58c4b55f3942e17013aaf8267` 中的 OpenPI；本地未提交修改以实际快照哈希为准。

```bash
.venv/bin/python -B thirdparty/bootstrap.py smolvla --install --torch-backend cu128 --locked
.venv/bin/python -B thirdparty/bootstrap.py openpi
```

可用 `--source ~/Robot-Arm/lerobot` 指定来源，`--python <Python解释器>` 指定**新环境**的解释器，`--offline` 只使用已存在的完整 uv 缓存。不会在原项目 `.venv` 安装这些训练依赖。CPU 必须显式选择 `--torch-backend cpu` 和训练 `--device cpu`；默认是 GPU，不自动回退。

当前上游 LeRobot 的 pyproject 还列有与本任务无关的硬件依赖；独立环境使用 `smolvla/requirements.txt` 安装学习所需依赖，再 `--no-deps` 安装上游包。没有安装其全部机器人驱动。

RL状态数据增强现已按用户要求启动工程试验；`bash scripts/setup_kitchen_environment.sh`只检查基础环境，`--with-rl`才会在项目`.venv`安装可选CUDA Torch/MuJoCo-Warp，并约束原物理依赖版本，同时复制本地DiscoDemo的独立算法模块。当前已完成L1 apple短程更新/恢复及原CPU数据出口验证，尚无正常随机初态的合格RL专家；GPU训练副本与原模型有显式物理差异，禁止直接当成CPU合格轨迹。[状态RL计划与命令](../docs/data_diversity/DISCODEMO_KITCHEN_RUNBOOK_CN.md)。

[SmolVLA 单任务操作流程](../docs/kitchen_platform_v3_20261020/SMOLVLA_PILOT_CN.md)
