# 新底座梨继续提速到4/5倍运动进度请求

用户要求继续测试提速。在 `feature/data-collection-improve`、HEAD `89768588655a6abda722af2db38113ef37611198` 的已有工作区上，将实验动作进度请求上界从3扩展到5，定义共享常量 `MAX_FACTOR`；不改默认倍率、不改模型、关节/TCP硬限速、夹爪时序、清洗等待和任务成功标准。保留首次备份及所有既有数据。

使用带圆柱底座的 `pear_foot_20261106` 首次备份，相同完整初态、L2 pear seeds0/1，连续时钟和受限圆角保持原实现，新实验批次为 `experiments/kitchen_platform_v3/collections/pear_foot_speed5_20261106`。

| seed | 上轮3倍请求合格 | 本轮4倍候选 | 本轮5倍最终合格 |
| --- | ---: | ---: | ---: |
| 0 | 54.40s | 52.36s | 51.88s |
| 1 | 55.56s | 53.64s | 53.16s |

两条5倍候选均重新执行并记录为 `optimized_02/trajectory.h5`、`report.json`、`annotations.json`，通过任务成功检验和独立动作重仿真，qpos/qvel最大误差0，初态与各自备份相同，备份哈希未变。包含1倍对照的6个候选试跑均通过。不是全目录或任意随机初态验收。

相对3倍请求仍节省约4.6%/4.3%，但从4到5每条仅再节省0.48秒。较大请求主要被已有速率上限截住，且稳定/夹爪/设备时间保留；不能把5倍请求解释为整条快5倍。继续提高倍率的收益可能很小，若后续要明显提速，应分析路径、保护段与动力学，不能未经验证直接提升物理控制上限。

新增测试验证5倍请求仍服从原逐帧限速，并拒绝超出实验预算的倍率。完整 **106项测试通过**；两条最终数据的粗/细导出计划映射通过。未渲染本轮新视频，未改变默认全任务采集。

复现实验使用新输出目录：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python -B -m integration.kitchen.data.retiming_experiment \
  --output experiments/kitchen_platform_v3/collections/pear_foot_speed5_new \
  --backup-root experiments/kitchen_platform_v3/collections/pear_foot_20261106 \
  --case-ids kitchen_expert_v3:L2:pear --seeds 0 1 --factors 1 4 5 --continuous
```
