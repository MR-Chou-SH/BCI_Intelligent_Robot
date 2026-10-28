# 全任务自动采集（无图像）

入口 `scripts/collect_kitchen_dataset.sh`。20 个原子实例 + 80 个组合实例，默认每个采集 **2 条合格轨迹，共 200 条**。C04–C08以装盘结束，D01–D09显式包含B1送餐；C01–C03已去重删除。完整表见[TASK_CATALOG_V3_CN.md](TASK_CATALOG_V3_CN.md)。默认不渲染图片或视频，不依赖 ffmpeg / EGL / VR。

2026-10-28新增英文粗/细标注，并修改龙头把手几何和香蕉组合放盘。当前批次请使用新目录，例如 `--output experiments/kitchen_platform_v3/collections/all_tasks_vla_faucet32_20261028 --motion-profile faster`。计划身份现在也绑定模型生成代码、布局配置和主MJCF；修改这些内容后不能继续向原批次混入新模型数据。旧轨迹保留，动力学重放需要匹配原模型版本；旧文件语言标注导出不需要重建模型。

同日后续采集效率/连续抓取优化后，请使用下面的新批次命令，避免混入此前源码身份。墙钟与仿真时长、分项计时见[实测记录](../development_log/2026-10-28-kitchen-efficiency-lerobot.md)。

从仓库根目录执行：

```bash
bash scripts/collect_kitchen_dataset.sh \
  --output experiments/kitchen_platform_v3/collections/all_tasks_efficient_vla_20261028 \
  --motion-profile faster --samples-per-case 2 --jobs 2
```

中断后原命令加 `--resume`。代码、模型或计划参数变化后使用新目录；不要手改原计划绕过指纹。全量 200 条尚未实采完成，以每批 `summary.json` 为准。

默认参数：

- `--suite all --samples-per-case 2 --jobs 2`：两个采集进程。
- `--profile research`：保持左水果、右主食，小范围位姿随机化，沿用初始不重叠检查。
- `--base-seed 0 --max-attempts 3`：样本 seed 为 0、1，失败依次使用 2/4 或 3/5；重试有上限。
- `--case-timeout 1800`：每次专家执行的协作式预算，沿用现有 API 时间检查。
- `--motion-profile standard`：默认轨迹时序。可选 `faster` 提高空载/携带段速度，并减少右臂中间路点的停顿；香蕉左臂技能保留原时序。接触操作和清洗/加热等待不加速，控制器限速与成功门槛不变。此参数与场景随机化的 `--profile` 相互独立。

尝试提速采集时使用新目录：

```bash
bash scripts/collect_kitchen_dataset.sh \
  --output experiments/kitchen_platform_v3/collections/all_tasks_delivery_fast_20261027 \
  --motion-profile faster --samples-per-case 2 --jobs 2
```

提速档属于代表任务验证，尚未覆盖全部100实例；失败仍按原规则排除并有限重试。时序档写入计划和每条报告，续跑必须保留原参数。对照实验见[提速记录](../development_log/2026-10-27-kitchen-motion-speed.md)。

后续主食旋转及入炉关门衔接已进一步优化，见[右臂连续运动记录](../development_log/2026-10-27-kitchen-right-motion-flow.md)。已有 `all_tasks_delivery_fast_20261027` 数据不随源码更新；使用新目录 `all_tasks_right_flow_20261027` 才能采集这次修改后的轨迹。

最新入炉路径与退出到关门接近已合并，见[段间衔接记录](../development_log/2026-10-27-kitchen-segment-handoffs.md)。要采集这一版，请改用新目录 `all_tasks_segment_flow_20261027`；旧视频仍展示对应旧批次的运动。

可用 `--case-ids kitchen_expert_v3:L1:apple` 或 `--suite atomic` 缩小范围。`--plan-only` 只写计划；之后同参数加 `--resume` 开始执行。

## 输出

| 路径 | 内容 |
| --- | --- |
| `plan.json` | 用例、参数、源码/控制指纹 |
| `attempts/<用例>/sample_01/attempt_01_seed0/trajectory.h5` | 一次初态、25Hz joint_pos / 夹爪开度、关键状态 |
| 同目录 `report.json` | 任务、seed、成功/失败、文件哈希和简短重放结果 |
| 同目录 `annotations.json` | 英文完整指令、目标ID、按动作行号划分的细阶段及轨迹哈希 |
| 相邻 `.job.json` / `.result.json` | 中断恢复所需的小型调度凭据 |
| `manifest.json` | 已合格样本和轨迹路径 |
| `summary.json` | 目标、合格数量和缺失项 |

合格要求：在线任务质量通过，且独立动作重仿真通过，且重算后的任务成功。检查仍执行；默认不保存逐步检查详情、事件全集、失败截图、开发 checkpoint 或 RPC/物理诊断。新失败尝试仅保留小报告；合格尝试保留 HDF5 和报告。

报告的 `wall_seconds` 是本次采集含准备和重放的实际耗时；`simulated_seconds` 是录制段仿真时长。`timing_seconds`进一步区分执行和重放等开销；两个execution子项不与execution重复相加。采集手动步进不使用sleep或实时限速。派生LeRobot转换单独运行，见[格式与π0.5接口](LEROBOT_PI05_CN.md)。

续跑验证报告和轨迹哈希，跳过合格样本。未封存尝试保留并消耗一次尝试，再换 seed；达到上限后列入缺失项。同批目录有文件锁，禁止两个调度器并发使用。所有目标齐全返回 0，否则返回 1。

需要排错时，显式使用 `scripts/collect_kitchen_experts.sh run --diagnostics ...`，额外诊断不要用于常规数据集。可选视频/查看器通过 `integration.kitchen.data.replay video/viewer` 单独调用。[格式说明](DATASET_FORMAT_CN.md) · [模块结构](../../integration/kitchen/README.md)

2026-10-27 管线验证：新格式覆盖苹果直装盘、香蕉清洗、汉堡加热、梨清洗配热狗加热；另完成苹果两条采集及续跑验证。该段记录先前V2管线验证；不等于全部100个V3用例在当前身份下验收。
