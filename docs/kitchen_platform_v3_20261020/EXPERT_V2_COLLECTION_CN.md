# 两张任务表的专家轨迹采集（Expert V2）

## 2026-10-27 曲线路径修订

水果接近改为斜向Bezier曲线，抓取前25mm协议交接不额外停稳；携带放置与关水后的再接近使用实际几何规划。整段C1关节插值用于真实仿真执行，视频按记录状态以25fps正常速度重放。47项测试通过，增量回归最新22个case/seed通过（含全部左侧16实例/seed），均无传统水果路线回退。详见[曲线路径及验证记录](../development_log/2026-10-27-curved-fruit-trajectories.md)。正式采集使用新目录，完整128条尚未重采。

## 当前运动连续性优化

左侧长距离运动、原地变姿态、水龙头自由段与开门圆弧改为连续执行，保留必要接触稳定等待。各任务固定回home已移除；C05–C08改为左手局部退出托盘通道，当前姿态规划失败时才带原因回home兜底。41项测试通过；分轮回归最新31个case/seed通过，包含全部左侧16种水果/seed组合及四类双侧组合；未重采完整128条。正常速度视频、详细指标和各轮身份见[连续性记录](../development_log/2026-10-26-kitchen-motion-continuity.md)。下方整臂回home方案为上一轮历史。

## 当前组合衔接修复

C05–C08 完成水果装盘后，左臂先沿几何检查通过的路径返回待机，并复核水果状态，再开始右侧任务。C05 苹果/汉堡 seed1、C06 香蕉/热狗 seed0、C07 香蕉/加热汉堡 seed0、C08 梨/加热热狗 seed0 均已通过质量与精确控制重仿真；38 项测试通过。未放宽跟踪与质量阈值。完整证据见[组合衔接日志](../development_log/2026-10-26-kitchen-composite-handoff.md)。这不是全部随机组合验收；改动源码后正式采集需使用新目录。

## 当前60°把手与关门提速

两种小盘把手当前均为60°，把手滑动摩擦1.6，食物/小盘承载面1.2。新模型已有9个固定初态用例和2个research seed1用例通过质量与精确重放；完整关门阶段约从134.5秒降到41.8秒（仿真时间）。详见[新模型实验与验收清单](../development_log/2026-10-26-kitchen-handle60-door-speed.md)。下方30°模型记录保留为历史，旧轨迹须使用匹配模型重放。

## 2026-10-26 早期30°几何修订（历史）

右侧固定初态 R1–R5 的 11 个实例已通过最终质量检查及控制重放。主食改用当前把手几何规划；候选 013 热狗把手倾角为可配置的 30°，门默认 100°。Research 仍为 ±8cm / ±5°，主食与另一小盘/托盘之间的初始操作间隙提高到 60mm；research seed 1 的两种直接装盘已完整通过。完整清单与证据边界见[本轮日志](../development_log/2026-10-26-kitchen-measured-meal-geometry.md)，下文较早状态保留为历史记录。

重新采集右侧全部任务（使用新的输出目录）：

```bash
MUJOCO_GL=egl scripts/collect_kitchen_experts.sh run \
  --suite atomic --tasks R1 R2 R3 R4 R5 --profile fixed --jobs 2 \
  --case-timeout 1000 --output experiments/kitchen_platform_v3/right_geometry_new_run
```

本页对应用户2026-10-24提供的两张表，命名空间为`kitchen_expert_v2`，不替换旧V1.1编号。全部使用现有FR3/UMI、IK、MuJoCo动力学和接触检查；不需要VR。数据不包含图像。**已实现全部64个用例的调度与动作流程，但整套物理验收仍在进行，不能把64个计划当成64条成功轨迹。** 实际成功数量以每次运行的`summary_*.json`和`QUALIFIED_EXPERT`记录为准。

## 用例与初态

| 编号 | 数量 | 执行流程／任务开始时的状态 |
| --- | ---: | --- |
| L1 | 4 | 苹果／梨／香蕉／西红柿直接装盘 |
| L2 | 4 | 水果放入篮中→开水→等待清洗完成→关水→取出装盘 |
| R1 | 2 | 炉门已开、炉内空；主食放入炉中并关门 |
| R2 | 2 | 炉门已开、指定主食在炉内；取出装盘并关门 |
| R3 | 3 | 炉门关闭，炉内分别为空／汉堡／热狗；直接开门 |
| R4 | 2 | 炉门关闭、指定主食在炉内；按启动→等待加热事件→开门 |
| R5 | 2 | 汉堡／热狗连同各自承载物直接装盘 |
| B1 | 1 | 两臂递送空托盘；托盘内容不作为变化条件 |
| C01／C02 | 4＋4 | 分别执行L1／L2 |
| C03／C04 | 2＋2 | 分别直接装主食／根据实测状态完成加热装盘 |
| C05／C06 | 8＋8 | L1／L2后接R5 |
| C07／C08 | 8＋8 | L1／L2后接完整加热装盘流程 |

合计20种原子实例、44种组合实例。`＋`目前按顺序执行。C01–C08不自动追加B1。加热链按实际炉门、承托和加热状态选择R3→R1→R4→R2，不把计划目标当成已完成状态。

每个用例重新构建场景。特殊初态通过真实动作准备，准备过程单独留痕；进入任务前调用状态接口保存完整初态，然后开始任务轨迹录制。不瞬移主食、焊接物体或直接设置炉门状态。微波炉加热与清洗仅指仿真设备事件。

## 当前验证状态（2026-10-25）

当前继续使用带苹果／番茄平底支脚的模型、连续动作规划和精简状态／动作记录。不同批次绑定各自源码身份，不把旧版成功自动计入新版。

- 连续规划批次：L1 四种水果均通过；L2 苹果／番茄通过，香蕉在本轮局部速度调整后也通过。梨通过加强清洗后再抓取的夹持力，也已通过；继续提高速度上限不能解决实际滑落。
- 本轮右侧已通过：汉堡 R1（装炉关门）、R3（有餐直接开门）、R4（启动／加热／开门）、R5（直接装盘）。热狗 R5 也已通过。R2 实测取餐路径与热狗微波炉路径仍在验证；不宣称右侧完整通过。
- 全部 20 原子／44 组合的目录与调度已有实现，完整物理验收仍未完成。逐条结果以相应 `report.json`、`summary_*.json` 为准，历史过程见[续作日志](../development_log/2026-10-24-kitchen-expert-v2-continuation.md)和[本轮日志](../development_log/2026-10-25-kitchen-right-side.md)。

已获用户授权的当前检查参数：普通接触力 15N、指垫有意接触 40N，普通穿透容差 3mm，指垫 3mm，支撑柔顺 1.5mm，抓持丢失宽限 100ms。整条相对位姿门禁临时关闭，不能据此宣称夹持姿态稳定。梨的指垫近接交接／已分离释放允许 0.40m/s；香蕉在双侧抓持或已承托的 carry 阶段允许 6rad/s。其他载荷／设备仍为 0.30m/s、4rad/s，释放自转豁免沿用此前用户指示；最终装盘仍要求实际支撑与连续稳定。

食物在自有盘面上有最多 2mm、100ms 的短暂离面容差，投影中心必须仍在盘内；不会伪造原生支撑，最终必须恢复实际接触。在线任务／接触检查为 25Hz，scratch 动力学预检逐 2ms 采样；积分和实际动作记录仍为 500Hz。

默认失败仅保留报告；成功保留一次初态、25Hz 关键状态及压缩实际动作，无图像、完整 RPC 或逐步大日志。需要诊断过程时采集前显式启用 `--diagnostics`。

## 启动

在仓库根目录运行；使用已经安装的`.venv`。输出目录必须是新的，原记录不会覆盖。先生成两张表的完整计划：

```bash
bash scripts/collect_kitchen_experts.sh plan \
  --suite all --profile fixed \
  --output experiments/kitchen_platform_v3/linux_acceptance_expert_plan_01
```

启动全部64个用例，每个固定初态运行一次：

```bash
bash scripts/collect_kitchen_experts.sh run \
  --suite all --profile fixed --seeds 0 --jobs 4 --case-timeout 1800 \
  --output experiments/kitchen_platform_v3/linux_acceptance_expert_all_01
```

固定场景通过后，再增加保持左右分区的小范围随机化。`research`保留左水果、右主食，并做完整物体间不重叠检查；初始化可行不代表整条轨迹必然成功：

```bash
bash scripts/collect_kitchen_experts.sh run \
  --suite all --profile research --seeds 0 1 2 --jobs 4 --case-timeout 1800 \
  --output experiments/kitchen_platform_v3/linux_acceptance_expert_random_01
```

调试时可用`--tasks L1 R3`选任务，也可用完整`--case-ids kitchen_expert_v2:L1:apple kitchen_expert_v2:R3:empty`选单个实例。`--jobs`范围1–4；`--case-timeout`限制专家动作阶段的墙钟时间，回放验证另计。每次运行保留至少10GiB磁盘空间。长组合需要较多时间与磁盘；程序不会删旧数据腾空间。

中断后，使用完全相同参数和输出路径加`--resume`。仅在计划、源码和已有索引一致时续采；索引中的失败不自动重试。修改代码或需要重试失败用例时，选择新目录。尚未封存／未入索引的局部目录会使续采停止，保留供诊断。

## 数据与合格条件

每个用例默认只保存：

- `data/state_trajectory_*/trajectory.h5`：一次完整初态、压缩关键状态／动作数组。
- `report.json`：任务标签、结果、关键任务边界与重放结果。

默认关闭500Hz物理诊断、完整RPC回复和逐局源码副本，不另存重复的完整初态JSON。确需诊断时显式加`--diagnostics`。具体字段见[精简记录格式](STATE_RECORD_REPLAY_CN.md)。

`plan.json`列出完整目录与本次选择；`index.jsonl`逐局追加结果；`summary_*.json`列出合格与失败。新索引还绑定报告SHA，续采会检查报告、已合格轨迹的完整性。

只有实际抓持／松爪支撑等接触门禁、25Hz在线任务判据、逐2ms的scratch接触预检和初态＋实际控制重仿真全部通过，才标记`QUALIFIED_EXPERT`。装盘需当前连续2秒稳定承托、无手指接触且完整物体包络位于托盘内部；组合结束还检查前面子目标未被后续动作破坏。B1还检查四指垫实际夹持、双手滑移、托盘倾角和用户桌板承托。任一失败保留`FAILED_ATTEMPT`，批次未达全部配额时返回非零退出码。

## MuJoCo查看

从合格报告复制`trajectory`路径，执行：

```bash
.venv/bin/python -B -m integration.kitchen.replay inspect <trajectory.h5>
.venv/bin/python -B -m integration.kitchen.replay verify <trajectory.h5>
.venv/bin/python -B -m integration.kitchen.replay viewer <trajectory.h5>
MUJOCO_GL=egl .venv/bin/python -B -m integration.kitchen.replay video \
  <trajectory.h5> --output /tmp/kitchen_expert_review.mp4 --speed 4
```

MP4是单独生成的查看文件，不加入状态训练数据。viewer需要本机图形桌面；无窗口环境可生成MP4。更详细的录制、重放身份边界见[状态记录与重放](STATE_RECORD_REPLAY_CN.md)。
