# 所有任务共用的采集、离线优化和渲染流程

2026-11-06。当前Expert V3有100个实例：20个原子、34个装盘组合、46个送餐组合，见[任务大表](TASK_CATALOG_V3_CN.md)。默认无图像采集，不依赖Unity/VR。

## 统一流程

```text
合法初态/局部随机化
  → 首次专家规划执行 → 成功检查＋独立动作重放 → 完整backup
  → 离线连续时钟/短停顿处理/小圆角 → 动作候选
  → 从相同完整初态物理试跑
  → 选最快成功候选 → 重新执行录制＋独立动作重放
  → 最终H5、报告、英文粗细标注 → manifest
```

首次备份不覆盖。候选普通试跑只保存动作和小报告，不生成完整H5。若所有优化未通过，使用**已经成功并通过独立重放的首次备份**，报告标记 `ORIGINAL_FALLBACK`；优化成功标记 `OPTIMIZED`。两者都计入合格数据，summary分别统计，便于定位尚未优化的任务。首次专家失败才换seed有限重试。

本入口现在默认 `--postprocess continuous`，可用 `--postprocess none`明确选择仅首次采集。默认优化请求倍率3和5；`--optimization-factors`可选最多4个不同倍率，均大于1且不超过5。它们是进度请求，不提高关节/TCP上限，也不是整条任务按倍速运行。

## 本轮共同改进

后续整条轨迹复核已加入：加热期间提前张爪并接近门把手（完成加热才开门）、按剩余行程生成松爪动作、右侧撤手与双臂直线不再逐点停顿、携带物装盘曲线及炉外小圆角、水龙头连续转动。见[典型场景验证](../development_log/2026-11-06-kitchen-whole-task-flow.md)。候选试跑只补实际缺少的有界停稳/稳定时间，最终H5与语言边界使用真实执行tick；因此实际动作数可能超过候选NPZ的请求帧数，按实际时长选择结果。

- 保持左水果、右主食，research局部随机化和原不重叠检查。
- 主食基于实测几何规划；60度小盘把手；组合任务局部撤手、B1实际载荷支撑检查。
- 梨新增20mm半径/8mm高圆柱底座，原底面和180g总质量保留。
- 开水后的显式等待及模拟清洗计时为2秒，仍须满足实际开水、在篮内且未被夹持。
- 离线按关节弧长连续推进，跨过短内部停车；受限圆角的TCP偏移≤2mm，水池/炉壁净距不足保留源点，携带物使用副本做局部净距检查。
- 夹爪操作、明确事件边界、稳定窗口、较长设备等待受保护。微波炉device接触不做大范围改形。
- 原始备份和最终数据都须通过任务成功与独立重放；不以中间状态纠偏。
- 英文粗细标注按最终动作重新映射；原始与优化版本共用 `source_group`，不能跨训练/验证集合。

完整优化算法与边界见[动作后优化说明](ACTION_RETIMING_EXPERIMENT_CN.md)。现有部分中间接触门禁仍维持此前设置，成功和重放不是完整连续无碰撞证明。统一入口覆盖所有任务，当前尚未完成最新源码100实例的全量验收。

## 每任务采1条：100条最终数据

从仓库根目录执行，使用未用过的新目录：

```bash
bash scripts/collect_kitchen_dataset.sh \
  --output experiments/kitchen_platform_v3/collections/all_tasks_1each_optimized_20261106 \
  --samples-per-case 1 --jobs 2 \
  --profile research --motion-profile faster \
  --postprocess continuous --optimization-factors 3 5
```

无需先启动服务或Unity，worker通过本地客户端完成规划/物理执行。目标是100条**合格最终样本**，另保留对应的首次成功备份，备份不重复计数或渲染。仅排错可加 `--case-ids kitchen_expert_v3:L2:pear`。

中断后原命令加 `--resume`。先查看计划可加 `--plan-only`，之后同参数加 `--resume`执行。源码、模型、设备语义或计划参数变化后必须新建批次，不手改plan绕过校验。

默认每样本最多3次首次专家尝试，失败换seed。seed按 `base_seed+sample_index+attempt_index*samples_per_case` 分配，每实例1条时依次0/1/2。执行预算默认1800秒为每阶段协作式预算，含优化的整条worker实际耗时会更长，不是1800秒硬进程超时。

## 数据结构与恢复

```text
<批次>/plan.json
<批次>/attempts/<case>/sample_01/attempt_01_seed0/
  backup/                      完整首次H5、报告、标注、稀疏阶段信息
  candidate_00.npz/json         优化动作与阶段边界，不含状态/图像
  trial_00/report.json          候选物理试跑结果
  optimized_00/                被选候选重新录制和独立重放
  trajectory.h5                最终数据统一入口
  annotations.json             最终英文粗细标注
  report.json                  最终资格、来源/备份哈希、优化或回退状态
<批次>/manifest.json            仅最终样本，每case/sample一条
<批次>/summary.json             target/qualified/optimized/original_fallback/missing
```

根部最终文件是选中结果的副本，统一下游读取路径。保留备份和最终结果会占两份以上H5空间；候选普通试跑不记录H5。目录中的备份/候选/优化分支不应通过递归扫描当成独立训练数据。

- 同目录有文件锁，不允许两个调度器并发写入。
- 已封存最终样本通过报告、轨迹、标注和备份哈希验证后跳过，不重复规划。
- 合格备份已存在但优化中断，可继续同attempt/seed，复用封存候选和报告；无报告半成品保留并跳过，不覆盖。
- 若首次备份尚未封存，半成品保留，占一次尝试后换seed；重试有上限。
- 不支持离线处理的帧中hold等合法原始记录明确回退；哈希损坏/源码身份冲突不会作为普通优化失败悄悄通过。
- 结束码0表示目标齐全，否则查看missing。运行中看终端的最终report输出，manifest/summary在批次收尾更新。

`wall_seconds`为当前worker调用实际耗时，含本次新增的备份、试跑、录制与重放；恢复复用部分不重复计时。`postprocessing`保留原始/最终仿真时长、候选报告引用和选择原因。采集没有sleep或实时限速。

历史批次保留原位，旧64条、旧100条和各轮实验不自动计入本次目标。H5字段见[数据格式](DATASET_FORMAT_CN.md)。

## 一个命令渲染最终轨迹

采集完成后：

```bash
bash scripts/render_kitchen_dataset.sh \
  --manifest experiments/kitchen_platform_v3/collections/all_tasks_1each_optimized_20261106/manifest.json \
  --output experiments/kitchen_platform_v3/videos_all_tasks_1each_optimized_20261106 \
  --speed 1 --lightweight
```

只渲染manifest中的最终样本，不渲染备份或候选。默认25fps、960×540、前方宽景，覆盖水果、水槽、微波炉、餐盘和送餐区。`--speed 1`为正常播放；提高该值仅改变审核视频速度，不加速采集动作。

视频位于 `<视频目录>/videos/<case>/sample_01/render_001/trajectory.mp4`，总索引为 `<视频目录>/index.json`；单样本的 `qualified_video.json`保存渲染凭据。中断后渲染原命令加 `--resume`，已完成且哈希匹配的视频不重复渲染。源manifest、相机或其他设置变动后应使用新视频目录。

需要ffmpeg和可用EGL；脚本自动设置MuJoCo/EGL和线程数。`--lightweight`仅关闭MSAA、阴影和反射，适合本机软件渲染；去掉可用默认画面。MP4只作审核，H5仍无图像，训练图像补录见[LeRobot/Unity接口](LEROBOT_PI05_CN.md)。
