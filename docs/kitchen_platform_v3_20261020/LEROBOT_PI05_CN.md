# H5原始数据、LeRobot派生数据与π0.5接口

## 数据分层

原始真值继续是 `trajectory.h5`、`report.json`、`annotations.json`。H5保存精确初态、float64动作、原生夹爪目标及关键状态，用来重放和复核成功。LeRobot是派生训练数据，使用float32机器人状态和动作；不替代精确重放文件，不修改或裁剪原H5。

实现入口：`integration.kitchen.data.lerobot_export`。只转换带匹配合格报告和标注哈希的样本。转换按episode读取，不把所有轨迹常驻内存，不自动安装依赖或上传数据。

本地参考源码是 `.cache/upstream/dexjoco/`：

- `dexjoco-data-converter/src/dexjoco_data_converter/to_lerobot/merge_episode_lerobot.py`：原始低维数据/图像→`LeRobotDataset.create/add_frame/save_episode`。
- `openpi/src/openpi/policies/dual_arm_policy.py`：训练与推理复用输入映射，动作chunk输出适配。
- `dexjoco/dexjoco_openpi_client/dexjoco_openpi_env.py`：环境动作转换和分块执行。
- `openpi/src/openpi/models/pi0_config.py`、`openpi/src/openpi/training/config.py`：π0.5、默认32维动作、模型输入padding和delta/absolute变换。

这里只参考流程，不直接使用DexJoCo的44维旋转向量/灵巧手动作、46维状态或转换器硬编码的30fps。FR3/UMI是左右各7个关节、各1个夹爪，数据是25Hz。

## 训练字段

| LeRobot字段 | 形状/类型 | H5来源/语义 |
| --- | --- | --- |
| `observation.state` | 16，float32 | 左7关节rad、左实测夹爪宽度m、右7关节rad、右实测夹爪宽度m |
| `action` | 16，float32 | 左7目标关节rad、左目标开度ratio、右7目标关节rad、右目标开度ratio |
| task/task_index | LeRobot writer管理 | 英文粗指令或细阶段指令 |
| episode/frame索引、timestamp | LeRobot writer管理 | 25fps，timestamp按导出episode从0起 |

夹爪目标仍是0关闭、1张开。**实测夹爪宽度与目标开度单位不同**，不能互相冒充，也不能对夹爪通道做“目标减状态”的delta变换。状态只包含机器人本体感知，不把物体位姿、清洗/加热真值或成功标记喂给策略。那些信息继续留在H5供初始化、成功检验和复现。

原H5的关节数组是左7/右7，夹爪另存；`pack16/unpack16`负责显式重排。导出时先取同一时刻的**动作前实测状态**，再关联该时刻开始应用的目标动作，不使用下一帧状态。

## 时间与语言

导出网格是 `start_tick + 20*k`，对应500Hz物理时钟中的25Hz控制帧。正常采集一行对应一帧；若H5包含帧中hold/reset，保留原H5，用最近已生效动作关联规则生成规则25Hz训练样本。导出数据不声称能精确复现被规则网格省略的帧中事件。

细标注原本以原始动作行号为单位。导出器把它映射到导出网格，在 `kitchen_export.json` 保留原区间与新范围、原轨迹路径及SHA256。

- `--instruction-level coarse`：完整轨迹保持一个episode，所有帧使用粗指令，细区间保留为旁侧元数据。
- `--instruction-level fine`：每个语义阶段分别导出一个episode，复用相同原轨迹的对应状态、动作与提示词。避免训练action chunk越过语言边界，却仍被错误地标成上一阶段。
- 两种视图写入不同输出目录。训练/验证划分应按原轨迹组进行，不能把同一长轨迹的不同子段分到不同集合。更严格的场景泛化实验还应按seed/布局分组。

## 图像：本轮仅规划

当前导出器只包含真实的低维数据，不写黑图、占位视频或虚假的图像mask，输出声明 `vision_ready=false`。**缺少图像时不能直接宣称已具备π0.5视觉训练数据。**

后续Unity按H5物理状态/重放产生RGB，与上述25Hz网格tick对齐；建议相机为front、left_wrist、right_wrist。原始渲染分辨率与OpenPI的模型输入尺寸分别记录，后者由图像变换完成。LeRobot使用对应 `observation.images.*` 视频特征；图像帧数、帧号、动作和语义阶段边界必须一起校验。不能用4倍速审核MP4作为训练图像源。

OpenPI适配可将front映射到 `base_0_rgb`，左右腕分别映射到 `left_wrist_0_rgb`、`right_wrist_0_rgb`；mask反映实际可用相机。图像及相机外参实现不属于本轮已验收内容。

## π0.5训练与执行约定

本地OpenPI默认 `action_dim=32`、`action_horizon=50`。数据本身保留16维，在模型输入变换中padding到32维，输出只取前16维；不需要照搬44维checkpoint转换脚本。自定义FR3/UMI适配器，不使用ALOHA六关节的索引或夹爪角度映射。

初期可直接训练绝对关节目标。如选择delta动作，只对两侧7个关节做delta，mask为 `make_bool_mask(7,-1,7,-1)`，输出必须通过同一配置的 `AbsoluteActions`还原；夹爪通道始终保持绝对开度ratio。归一化统计仅从训练集计算，状态和动作分别归一化。

50帧chunk在25Hz下是2秒；推理可先执行8–12帧（0.32–0.48秒）后重新观测与推理，参数后续实测确定。模型给的是关节目标，不再对它重复求IK。通过 `unpack16`恢复两组关节和两组开度，交给现有动作codec、控制器、任务目标上下文及执行校验。不能直接套用DexJoCo的rotvec→quat/左右手重排。当前没有加载π0.5权重、没有运行视觉闭环，也没有替换既有控制器。

## 命令与当前环境边界

无需LeRobot依赖即可验证真实源文件、索引映射和导出计划：

```bash
.venv/bin/python -B -m integration.kitchen.data.lerobot_export plan \
  --manifest experiments/kitchen_platform_v3/collections/vla_dataset_final_20261028/manifest.json \
  --output /tmp/kitchen_lerobot_plan.json --instruction-level coarse
```

在已配置LeRobot的转换环境中，使用真实writer创建数据集：

```bash
python -B -m integration.kitchen.data.lerobot_export export \
  --manifest /绝对路径/manifest.json \
  --output /新目录/kitchen_lerobot_fine --instruction-level fine
```

也可用 `--trajectories <一个或多个trajectory.h5>` 替代manifest。输出必须是新目录；不覆盖已有数据。

当前仿真venv没有LeRobot、PyArrow、Torch。本轮已实现真实writer的调用接口、数值映射与协议测试、真实H5导出计划；没有安装新依赖，也没有伪造Parquet/LeRobot目录。实际LeRobot writer及后续OpenPI训练仍需在转换/训练环境验证并固定其依赖版本。
