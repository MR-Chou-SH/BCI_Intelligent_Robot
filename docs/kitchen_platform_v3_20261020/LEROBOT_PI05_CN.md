# H5原始数据、LeRobot派生数据与π0.5接口

> **2026-11-15 已实现的OpenPI π0.5管线见文末“OpenPI π0.5训练管线（A800）”。** 下文“图像：本轮仅规划”“命令与当前环境边界”是此前的规划记录；其中π0.5相机映射、16→32维padding、绝对关节动作等约定已按此实现。

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

统一采集输出的 `postprocessing.source_group` 指向首次备份轨迹哈希。转换器优先沿用这个组ID，使备份/不同优化版本以及粗/细视图不会因最终H5哈希不同而被划到训练集与验证集两侧；没有此字段的历史样本仍以自身轨迹哈希分组。

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

## OpenPI π0.5训练管线（A800，2026-11-15）

**两段式**：本仓库在仿真环境中渲染；OpenPI分支在A800上组装LeRobot并训练。

1. **渲染暂存（本机，`integration/kitchen/vla/openpi_export.py`）**：对采集批次每条合格轨迹做一次独立控制重放（加热灯、水柱需要重放出的设备状态），三路224×224相机直接编码H.264（GOP 10、CRF 20、无B帧），保存25 Hz实测状态、绝对动作与粗/细指令区间；不写PNG。单条目录原子发布，可`--resume`。需设置`KITCHEN_DATA_ROOTS`。

   ```bash
   KITCHEN_DATA_ROOTS=/home/user/data-staging/jiayu/BCI_DATA \
   ../BCI_Intelligent_Robot/.venv/bin/python -B -m integration.kitchen.vla.openpi_export \
     --batch /home/user/data-staging/jiayu/BCI_DATA/kitchen_v3_40pi_20261114 \
     --output /home/user/data-staging/jiayu/BCI_DATA/openpi_staging_kitchen_v3_40pi_20261114 --jobs 6
   ```

   实测：每进程约200帧/秒（三路），本机6进程约600帧/秒；全批约19 GB（每帧每路约0.9 KB）。A800未挂载本机NFS，且没有rsync；用tar经SSH传输，本机→A800约5.9 MB/s。

2. **OpenPI分支**：A800上`~/openpi`的新分支`feature/kitchen-bci-pi05`（基于`origin/main` 5631db9），独立worktree在`~/openpi-kitchen-bci`，不改动原`RECAP`工作区；复用`~/openpi/.venv`，运行时设`PYTHONPATH=~/openpi-kitchen-bci/src`。说明见该分支`examples/kitchen_bci/README.md`。
   - `examples/kitchen_bci/build_lerobot.py`：暂存→LeRobot v2.1（视频硬链接、不重编码；每帧附`fine_task_index`、`frames_to_stage_end`、`task_type_index`；`meta/kitchen_bci.json`含逐条case/seed/来源哈希与采样重复数）。
   - `kitchen_bci_policy`：camera1/2/3→`base_0_rgb`/`left_wrist_0_rgb`/`right_wrist_0_rgb`；16维状态与绝对动作（夹爪始终绝对）；`InstructionGranularity`在多阶段样本上以0.5概率改用细指令，并把越过该阶段末尾的动作块保持为阶段最后一帧（等价于按阶段切分的episode）。
   - `data_loader`：可选逐episode整数重复（采样权重）；默认`sqrt`规则：各任务类型采样帧数∝其帧数平方根。
   - 配置：`pi05_kitchen_bci`（全量，`pi05_base`初始化，batch 128，60k步，余弦学习率5e-5→5e-6）与`pi05_kitchen_bci_smoke`（每实例1条、300步）。
   - `examples/kitchen_bci/fetch_gcs_parallel.py`：A800单连接访问GCS仅约0.3 MB/s，`pi05_base`（12.4 GB）用64路分段并行下载（约3 MB/s）并逐文件校验MD5后放入openpi缓存。

**已验证（每实例1条的测试集，114条/17.3万帧）**：LeRobot加载、torchcodec解码三路视频、分位数归一化统计、OpenPI训练数据管线（三路uint8图像、状态补齐到32维、动作块50×32、含离散状态的提示词约79 token）；200个样本中72个为多阶段粗指令、128个为单句（细指令或原子任务），与设计一致；`sqrt`重复使原子任务采样占比约22%（原约6%）。

**全量数据与训练准备（2026-11-15）**：
- 暂存3927/3927条、无失败，18 GB；已传到A800 `~/datasets/jiayu/bci_staging/openpi_staging_kitchen_v3_40pi_20261114`（本机NFS保留一份）。
- LeRobot数据集 `~/datasets/jiayu/kitchen_bci_v3_40pi_20261114`：3927条、6,725,816帧、98句指令；视频为硬链接，不额外占盘。`sqrt`采样重复后每轮约1075万个样本，原子任务占约17.5%。
- 归一化统计（分位数，200万帧抽样）写在数据集目录 `norm_stats.json`。
- `pi05_base`（12.4 GB，逐文件MD5/CRC32C校验）在 `~/.cache/openpi/openpi-assets/checkpoints/pi05_base/params`。
- 冒烟训练（`pi05_kitchen_bci_smoke`，114条，8×A800，batch 32，300步）：1.5步/秒，loss 0.078→0.008；检查点42 GB（参数12 GB＋优化器状态31 GB），norm stats随检查点保存；用该检查点在真实帧上推理得到50×16动作块，单卡约0.11秒/次（首次编译29秒）。冒烟检查点已删除。
- 全量配置计时：batch 128时约1.9秒/步、GPU利用率91%；60k步约32小时，约为加权数据0.7轮。检查点保留20k/40k/60k及最新一个（约170 GB）；共享盘剩余约490 GB。

全量训练命令（A800，尚未启动）：

```bash
cd ~/openpi-kitchen-bci
PYTHONPATH=$HOME/openpi-kitchen-bci/src XLA_PYTHON_CLIENT_MEM_FRACTION=0.9 \
nohup ~/openpi/.venv/bin/python scripts/train.py pi05_kitchen_bci --exp-name=<run> --fsdp-devices=8 \
  > ~/logs/<run>.log 2>&1 &
```

数据加载进程较多，启动约10分钟后才开始出步数。

**全量训练已启动（2026-11-15 21:48，服务器时间）**：分支提交 `e290730`，`pi05_kitchen_bci`，8×A800 FSDP，batch 256（每卡32），84k步＝加权数据2轮（每轮10,749,084个样本、41,989步；按不重复帧计每轮26,273步），峰值学习率1e-4（按sqrt(256/64)从5e-5放大），warmup 3k，余弦衰减到1e-5，EMA 0.999。实测约3.6秒/步、GPU利用率100%、无显存不足；预计约84小时。每5k步保存，保留20k/40k/60k/80k与最新一个（每个42 GB）。日志 `~/logs/kitchen_bci_b256_2ep_20261115.log`（loss行因输出缓冲成批出现），W&B离线记录在 `~/openpi-kitchen-bci/wandb/`，主进程号记录在 `~/logs/kitchen_bci_b256_2ep_20261115.pid`，检查点在 `~/openpi-kitchen-bci/checkpoints/pi05_kitchen_bci/b256_2ep_20261115/`。

**训练提前停止（2026-11-16 03:00，服务器时间）**：按用户要求，在第5000步检查点保存后停止，不再续训。检查点 `~/openpi-kitchen-bci/checkpoints/pi05_kitchen_bci/b256_2ep_20261115/5000`（42 GB：参数12 GB＋优化器状态31 GB）。共训练5000步×256＝128万样本（加权数据约0.12轮）；学习率仍按84k步计划处在约1e-4，未退火。loss：0.070（第0步）→0.0045（500）→0.0025（2000）→0.0018（4400）。此检查点尚未做指令遵循评估。分支配置已恢复为该次训练实际使用的84k计划。

A800用户区（`/mnt/dex/a800/home/qujiayu`）共约1.6 TB，其中本项目新增约73 GB：上述检查点42 GB、暂存（含视频）18 GB、`pi05_base` 12 GB、LeRobot数据集parquet/meta 0.9 GB（视频与暂存为硬链接，不重复占用）。共享CPFS剩余约445 GB。

**数据集上传到OSS（2026-11-16）**：完整LeRobot数据集与冒烟测试集已从A800经阿里云内网（约1.4 GB/s）上传到 `oss://cri-ddk33nxnhdntfn4q-registry/jiayu/kitchen_bci_v3_40pi_20261114/`（15,714个文件、19,078,833,076字节，与A800逐字节总量一致，含 `norm_stats.json`）与 `.../jiayu/kitchen_bci_test_percase1/`。该桶在B300上以ossfs2挂载为 `/mnt/data`，因此B300可直接访问 `/mnt/data/jiayu/kitchen_bci_v3_40pi_20261114`；训练前建议复制到B300的CPFS以加快随机读取。OpenPI训练代码见 `github.com/bitqjy/openpi` 分支 `feature/kitchen-bci-pi05`（移植到上游main，尚未在新机器上运行测试），训练配置里的数据集路径需改为新位置。
