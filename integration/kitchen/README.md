# Kitchen 模块边界

`vla/` 是独立策略适配层：图像sidecar/LeRobot导出、推理通信、原生SmolVLA RTC桥接和MuJoCo执行。VLA训练代码与学习环境在 `thirdparty/smolvla/`。[单任务试验说明](../../docs/kitchen_platform_v3_20261020/SMOLVLA_PILOT_CN.md)。VLA当前支持L1/R5策略评估，其他任务不冒用这两个成功判据。

`data/speed_stress.py`提供已规划轨迹的独立1～4倍时序压缩实验，显式放宽私有控制器限制、保留物理任务计时，重新执行并独立重放；不修改正式采集默认值。[结果与复现命令](../../docs/development_log/2026-11-10-kitchen-relaxed-speed4.md)。

正式采集可显式选择`--postprocess adaptive`：优先4倍，仅对按钮、清洗完成及实测失败区间修复时序；原速也碰撞的备份不回退为新数据。100个既有任务实例中97条通过高速重录及独立重放，3条原路径碰撞被明确排除。[规则、结果与采集命令](../../docs/kitchen_platform_v3_20261020/ADAPTIVE_OFFLINE_TIMING_CN.md)。

上述梨组合的原规划交接已修复：整条左臂退让、B1先右后左的几何接近、直接送盘跳过主食通道退让。完整一轮加受影响8例复测覆盖100/100任务，84优化/16合格原始回退，独立重放误差均为0。[两轮验证记录](../../docs/development_log/2026-11-10-kitchen-pear-corridor-repair.md)。

`rl/` 是显式可选的状态真值数据增强层：可复用100实例任务监视器，当前CPU/Warp训练环境只支持L1 apple；算法复用`thirdparty/discodemo`，GPU后端在`robot_arm/kitchen/warp_*`。RL依赖在项目`.venv`通过`--with-rl`安装，基础仿真不强制依赖Torch。生成的动作必须在原CPU模型实测验收并独立重放后才写入合格manifest。[RL运行说明及物理兼容边界](../../docs/data_diversity/DISCODEMO_KITCHEN_RUNBOOK_CN.md)。

[50条随机苹果采集→真实replay观测→LeRobot v3→训练→独立评估命令](../../docs/kitchen_platform_v3_20261020/APPLE50_SMOLVLA_CN.md)。采集上限支持每case1000条；replay按manifest读取最终合格轨迹，支持逐episode恢复，不把备份或优化候选重复当作训练样本。

`scripts/kitchen_smolvla.sh evaluate --run <训练目录> --episodes 10 --video`连接单独启动的GPU服务，串行测试多个未见seed，统一保存视频、报告和summary；支持显式输出目录和`--resume`。

当前任务：20 个原子实例、80 个组合实例。批量入口：`bash scripts/collect_kitchen_dataset.sh --output experiments/kitchen_platform_v3/collections/<新批次>`。默认各两条，仅记录轨迹，不启动渲染。

默认使用 `--motion-profile standard`；选择 `faster` 可尝试缩短实际运动时长，香蕉左臂技能保留原时序。具体命令和验收范围见下方采集步骤。

| 目录 | 职责 |
| --- | --- |
| `tasks/` | `catalog.py` 定义任务矩阵；`language.py` 定义共享英文提示词；`execution.py` 根据实测状态选择技能、串联组合任务和准备初态 |
| `planning/` | `expert.py` 的抓取、放置、设备操作；`motion.py` 的运动执行与平滑；`meal_geometry.py`、`curved_motion.py` 的局部几何路径；只读参考轨迹适配 |
| `validation/` | `quality.py` 计算稳定支撑等物理条件；`success.py` 统一在线执行与重放的任务成功标准 |
| `runtime/` | 本地 API 客户端，沿用仿真服务的身份、写权限及动作校验 |
| `data/` | 批量采集、续跑、索引、英文粗/细标注和独立动作重放；不导入视频工具 |
| `tools/` | 可选 viewer/视频、状态查询示例及开发 checkpoint；不属于自动采集流程 |
| `tests/` | 接口、几何、任务组合、记录与重放回归 |

物理场景、随机初态、对象状态查询及低层控制位于 `robot_arm/kitchen/`，参见其 README。模型、IK 和控制器没有因为此次目录整理而重建。

数据流：`tasks → planning → runtime → robot_arm/kitchen`；仿真以 25Hz 提供关键状态给 `validation` 和记录器；`data/replay.py` 从初态和动作独立重算物理，再调用同一套成功判定。

根目录的 `launch.py`、`platform_server.py`、`planner_client.py` 是现有公开服务接口。`task_catalog.py`、`task_annotation.py`、`collection.py` 是原 V1 API 的任务定义/分段兼容能力，不与去重并扩展送餐的 V3 目录合并。仅转发模块的旧文件已经删除；调用方应直接导入上述目录。

```bash
.venv/bin/python -B -m integration.kitchen.data.collect_experts plan --output experiments/kitchen_platform_v3/collections/<计划名>
.venv/bin/python -B -m integration.kitchen.data.replay verify /绝对路径/trajectory.h5
.venv/bin/python -B -m integration.kitchen.tools.state_demo --help
.venv/bin/python -B -m integration.kitchen.planning.reference_trajectories inspect
```

[采集步骤](../../docs/kitchen_platform_v3_20261020/DATASET_COLLECTION_CN.md) · [数据格式](../../docs/kitchen_platform_v3_20261020/DATASET_FORMAT_CN.md)

H5到LeRobot的数值映射入口为 `data/lerobot_export.py`；真实MuJoCo图像sidecar和视觉数据导出在 `vla/dataset.py`。`vla/cameras_left.json`默认启用C全景＋左腕，保留但关闭右腕槽位，训练与推理严格匹配相机配置。Unity图像仍待接入。[π0.5接口与环境边界](../../docs/kitchen_platform_v3_20261020/LEROBOT_PI05_CN.md)。

`data/optimized_collection.py` 已将首次备份、离线连续平滑、同初态试跑、最终独立重放及失败回退接入常规采集，默认 `--postprocess continuous`。`data/retiming_experiment.py` 仍用于显式小样实验。渲染通过 `scripts/render_kitchen_dataset.sh` 只读最终manifest，不扫描备份。[统一流程和两条运行命令](../../docs/kitchen_platform_v3_20261020/UNIFIED_COLLECTION_CN.md)。
