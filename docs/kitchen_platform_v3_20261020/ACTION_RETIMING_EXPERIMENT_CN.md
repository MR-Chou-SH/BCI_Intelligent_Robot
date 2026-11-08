# 首次执行备份与动作后优化实验

2026-11-06后续已把同一优化算法接入正式批量采集，当前请优先阅读[所有任务统一采集流程](UNIFIED_COLLECTION_CN.md)。本文描述的小样实验CLI仍独立保留；下述“本轮/未接入”属于实验初次实现时的范围，不能据此判断最新批量入口状态。

2026-11-05。此入口是显式实验，不改变现有全任务采集脚本的默认行为。

## 流程与文件

1. 首次专家规划、执行、成功检验及独立动作重放，保存完整合格样本到 `backup/`：`trajectory.h5`、`report.json`、`annotations.json`。备份始终保留，不以优化结果覆盖它。
2. 同时保存小型 `action_context.json`，只记录接触合同/guarded gripper 模式切换、结束接触事件、原子技能及语言阶段边界，不记录逐步接触点、接触力或 RPC 全集。文件哈希与原轨迹哈希相互绑定。
3. 从备份动作生成候选。候选为 NPZ 中的关节目标、夹爪目标比例及精确原生夹爪值，以及 JSON 中的重新映射阶段和模式边界；没有候选 H5。
4. 候选从相同完整物理/控制器/设备初态执行，通过现有平台 API，保留其速率限制、release 支撑/稳定检查和在线/最终任务质量检查。不使用中间状态纠偏。
5. 选择通过且仿真时长更短的候选，重新从初态执行并录制，最后独立重仿真、生成新标注，保存到 `optimized_XX/`。只有此步骤通过才称为 `QUALIFIED_EXPERT`；单次候选成功称为 `CANDIDATE_TASK_PASS`。

若最快候选的最终录制/重放失败，再试下一条已成功且更短的候选；否则保留原始备份，报告没有可用优化结果。优化失败不更换 seed 来掩盖失败。

```text
<新实验目录>/plan.json
<新实验目录>/<case>_seedN/
  backup/                      首次完整成功执行及稀疏阶段信息
  candidate_XX.npz/json         动作候选与边界，没有状态数组
  trial_XX/report.json          候选物理执行结果，没有 H5
  optimized_XX/                被选候选的完整录制、报告与英文标注
  summary.json
<新实验目录>/summary.json
```

复用历史实验备份时，新目录中的 summary 指向原备份，原备份不移动、不覆盖。运行前后校验备份文件哈希。

## 第一版优化范围

- 动作频率仍25Hz，物理500Hz；目标语义与 V3 H5 一致。
- 保持原关节折线与端点，重新分配运动时间；不是新的全局空间路径规划器。输出控制帧仍连接离散目标，原折线拐角的连续碰撞认证不在本实验范围。
- 原样保留夹爪变化帧、精确静止目标等待、guarded gripper 帧和 device 合同运动时长。机械臂的 acquire/carry/release 等运动允许加速，是否仍成功交给重新仿真判断。
- 每帧在请求进度内尽量沿源轨迹前进；若触及原控制器 joint/TCP 上限，收缩该帧进度，不提高上限。倍率是请求上限，最终整条轨迹不会等比例加速。
- 所有原子/语义阶段与接触模式切换按真实的新控制帧边界重映射，不能沿用旧语言 step。
- 第一个倍率必须1，检查重建流程。除任务成功外，还要求末态 qpos/qvel 与备份误差不超过1e-8；加速候选则不要求与旧物理轨迹相同。
- 当前只接受规则25Hz、无帧中hold/reset、阶段位于控制帧边界的备份；不支持时明确拒绝，不静默丢弃事件。

当前原控制器/接触模块已有部分接触/碰撞门禁被此前用户设置为关闭，实验继承现状，没有重新开启或进一步放宽。任务成功和精确重放不等于证明所有中间接触无碰撞；更高速度的可接受性还需结合正常速度视频和具体物理指标讨论。

## 命令

从仓库根目录执行。目录必须全新；实验不提供原地续跑，失败批次保留，用新目录再试。

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python -B -m integration.kitchen.data.retiming_experiment \
  --output experiments/kitchen_platform_v3/collections/retiming_my_pilot \
  --case-ids kitchen_expert_v3:R5:burger kitchen_expert_v3:R5:hotdog \
  --seeds 0 --factors 1 1.25 1.5
```

已拥有上述结构的首次备份时，避免重新规划，增加：

```bash
  --backup-root experiments/kitchen_platform_v3/collections/retiming_my_pilot
```

与新的 `--output` 一起使用；backup-root 必须包含所选 case/seed 的合格备份、稀疏上下文和匹配哈希。不能直接拿没有阶段信息的旧普通 H5 冒充实验备份。跨优化器版本复用允许，但须通过当前模型、控制器/设备语义和 MuJoCo 身份校验。

每次实验最多4个seed、5个倍率，每倍率1–5（2026-11-06由3扩展到5，用于测量现有限速下的剩余空间），单轮执行预算默认900秒。请求进度倍率不提高控制器速度上限，也不表示整条任务等比例加速。结束码0表示所有选定实例都有合格且更短的最终样本；否则返回1，查看各级报告定位原因。倍率生成、候选执行、最终录制/重放都会增加采集耗时，不能只统计最终一次执行墙钟。

这仍是实验接口，尚未接入全量200条采集调度及其manifest。不要把 `trial_XX/report.json` 的PASS当成训练资格；派生LeRobot只使用带合格报告、独立重放和标注的最终 `trajectory.h5`。同一备份及其各优化版本必须归为同一训练/验证组，不能因为H5哈希不同就跨集合。

## 可选连续运动与小圆角（2026-11-06）

在上述实验命令中增加 `--continuous`，并使用新目录。倍率为1的对照仍保持原动作完全不变；其他候选启用下列处理：

- 在每个技能/接触模式区间内，跨过不超过10帧（0.4秒）、前后均为手臂运动且夹爪不变的内部静止段。明确事件边界、夹爪变化、guarded gripper、较长等待和末端稳定段仍受保护。
- 后续carry衔接补充：净距检查确认活动机械臂及载荷对其他物体/夹具至少20mm时，可跨过同一carry区间内最长2秒的到位等待；同一夹爪内部几何和原意图抓持接触不充当障碍，跨手碰撞对仍检查。炉口等净距不足的等待和明确边界保留，见[热狗空中衔接复核](../development_log/2026-11-06-kitchen-hotdog-carry-handoff.md)。
- 运动以关节路径弧长推进，消除原IK路点附近的时间密度与速度低谷；仍经过现有速度限制。弧长匀速不等于保证笛卡尔或设备铰链严格匀角速度。
- 水龙头device运动可以使用连续时钟；微波炉门/按钮device合同暂时保持原时钟。水龙头开启后的长清洗等待保留，不能删掉它冒充提速。
- 在原源轨迹上做两次局部二项式圆角，关节变化≤0.008rad、TCP位置变化≤2mm。首末点、事件附近5帧、夹爪附近5帧、acquire和device路径不做空间圆角。
- 圆角检查机械臂相对固定夹具的原始距离和现有几何运动界；近水池/炉壁等位置，不能证明有净距就保留原点。携带时用临时刚体假设在候选和中点检查食物/小盘对夹具净距，不修改真实物体或加附件。动态滑移仍由物理重跑决定。

例如复用上一轮香蕉初始备份：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python -B -m integration.kitchen.data.retiming_experiment \
  --output experiments/kitchen_platform_v3/collections/smoothing_my_wash \
  --backup-root experiments/kitchen_platform_v3/collections/retiming_wash_20261105 \
  --case-ids kitchen_expert_v3:L2:banana --seeds 0 --factors 1 1.5 2 --continuous
```

小圆角不是完整连续碰撞证明。轨迹通过原任务成功门槛，并不意味着所有中间接触门禁均已开启。明确失败候选保留小报告，较慢的成功候选可回退录制。当前没有因更快候选失败而放宽稳定/支撑要求。

[数据格式](DATASET_FORMAT_CN.md) · [原采集流程](DATASET_COLLECTION_CN.md) · [LeRobot接口](LEROBOT_PI05_CN.md)
