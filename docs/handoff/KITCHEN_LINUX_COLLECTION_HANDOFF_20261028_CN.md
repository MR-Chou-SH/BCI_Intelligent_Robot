# Kitchen Linux 专家轨迹采集交接（2026-10-28）

## 1. 范围与当前结论

本文交接用户本机本轮 Kitchen 开发：Linux 环境、局部初态随机化、原子/组合任务、几何抓取、运动连续性、无图像记录与重放、批量采集，以及后续 VLA 数据准备。已有 FR3 + UMI + MuJoCo 基线继续使用；没有另建机器人控制器，也没有改变 EEG/Quest 研究主线。全项目里程碑以 [PROJECT_STATUS](../status/PROJECT_STATUS.md) 为准。

当前已能脚本化采集初态、动作和关键状态，并通过独立动作重仿真重新判定任务成功。任务目录共 **100 个具体实例，原子任务建议 每个50条，组合任务建议每个20条**。最新源码（进行第一轮提速后）尚未完成全量实采，也未完成全部 100 实例的统一版本物理验收。

**H5 目前不含图像；先采可重放的轨迹是当前默认流程。** 英文任务标注已实现。LeRobot 数值映射与真实 writer 调用接口已实现，但当前环境缺少对应依赖，尚未完成实际 LeRobot 文件写出、Unity 图像接入或 π0.5 训练。

## 2. 优先阅读的文档

以下均位于 `docs/kitchen_platform_v3_20261020/`，详细字段和参数以实现及这些专题文档为准。部分早期文档仍包含历史 V1/V2 数量和旧批次命令；**当前任务以 V3 目录为准，当前采集用新的输出目录**。

| 文档 | 用途 |
| --- | --- |
| [TASK_CATALOG_V3_CN.md](../kitchen_platform_v3_20261020/TASK_CATALOG_V3_CN.md) | 去重后的完整任务大表、B1 送餐组合、英文粗/细指令和 step 定义 |
| [DATASET_COLLECTION_CN.md](../kitchen_platform_v3_20261020/DATASET_COLLECTION_CN.md) | 批量采集、有限重试、恢复、目录与合格规则 |
| [DATASET_FORMAT_CN.md](../kitchen_platform_v3_20261020/DATASET_FORMAT_CN.md) | V3 H5 字段、动作单位、初态、无损重放条件 |
| [STATE_RECORD_REPLAY_CN.md](../kitchen_platform_v3_20261020/STATE_RECORD_REPLAY_CN.md) | 状态查询、录制、verify、viewer 和 MP4 |
| [LEROBOT_PI05_CN.md](../kitchen_platform_v3_20261020/LEROBOT_PI05_CN.md) | H5→LeRobot、16 维映射、语言边界、Unity 图像和 π0.5 接口计划 |
| [LINUX_TASK_COLLECTION_CN.md](../kitchen_platform_v3_20261020/LINUX_TASK_COLLECTION_CN.md) | Linux 环境、原生启动器、局部随机化的由来；任务数量部分属历史版本 |
| [PLANNER_API_GUIDE_CN.md](../kitchen_platform_v3_20261020/PLANNER_API_GUIDE_CN.md) | 服务与规划接口参考，结合当前模块路径使用 |

## 3. 本轮在本机完成的修改

| 方向 | 已完成内容与边界 |
| --- | --- |
| Linux 适配 | 建立独立 `.venv`，新增原生 Python 启动器及 shell 采集入口；无窗口采集不依赖 Windows、VR、Unity 或视频渲染。原 PowerShell 服务入口仍保留。 |
| 域随机化 | 保留左水果、右主食和设备布局。初始 x/y 各 ±8 cm、yaw ±5°；主食与小盘成组变换，保留支撑、完整几何间隔、不重叠及静态接近检查，不随机质量和摩擦。具体可行性仍由拒绝采样检查。 |
| 任务体系 | 原子 L1/L2/R1–R5/B1，共 20 实例；装盘组合 C04–C08 共 34 实例；送餐组合 D01–D09 共 46 实例。删除重复的 C01–C03；B1 显式加入送餐组合，避免重复的 hotdog_hotdog 命名。微波炉默认初态仅关闭或开 100°。 |
| 右臂主食 | 基于当前编译几何、把手坐标和物体实测位姿计算抓取/接近，适应位置随机化；采用有限候选的局部路径和 IK，没有引入复杂全局规划器。两个主食小盘把手调整为 60°并增大摩擦。 |
| 双臂送餐 | 根据实测支撑关系处理餐盘→水果/小盘→主食载荷链；带餐抓持目标力加强，要求送达后食物仍被承托，不通过焊接或瞬移携带食物。 |
| 连续性 | 左侧采用检查过的斜线/曲线接近与搬运、关节平滑插值；正常组合衔接使用局部撤手，减少例行 home 回位。右侧合并接近/旋转/入炉路径，按实际手部包络决定退出距离，并衔接关门。必要接触确认与稳定等待仍保留。 |
| 香蕉与水龙头 | 组合任务中香蕉长轴沿餐盘局部 Y，保留清洗后正反面，降低与主食占位冲突；增加清场与转向抓持处理。水龙头把手直径改为 32 mm，并设置明确摩擦。 |
| 记录与重放 | 用一次完整初态、25Hz joint pos/夹爪目标和关键状态代替默认 500Hz 底层控制大数组。对象状态由统一查询接口取得；在线与重放共享成功判据。失败默认仅保留小报告，详细诊断需主动开启。 |
| 自动采集 | 每实例目标合格条数、有限换 seed 重试、计划/源码/模型指纹、封存哈希、同目录锁、恢复与缺失项汇总；不再自动渲染。 |
| VLA 标注 | 原子英文提示词；组合任务粗指令和细阶段共用原子模板，完整加热装盘为 H 阶段。成功样本输出 `annotations.json`，绑定轨迹哈希。 |
| 性能与派生数据 | 精简 step 回复、缓存静态 geom 归属、重放按事件边界批量调用、faster 路径批量发送；新增分项计时及 LeRobot 25Hz/16 维转换接口。 |

改动的历程、失败原因和证据见 [目录与数据整理记录](../development_log/2026-10-27-kitchen-data-reorganization.md)、[语言/几何记录](../development_log/2026-10-28-kitchen-vla-faucet-banana.md)、[效率与 LeRobot 记录](../development_log/2026-10-28-kitchen-efficiency-lerobot.md)。较早记录中的数量与成功结论只对应当时版本。

## 4. 目录增减与代码入口

当前职责如下，后续修改按职责进入对应目录，避免再次把仿真、规划和采集混在一个文件。

```text
robot_arm/kitchen/                  模型、初始化、状态、控制、原始记录
  model.py                         厨房与把手几何
  research_initialization.py       局部随机化和可行性检查
  object_state.py                  对象状态接口
  platform.py / control.py         服务步进 / 原控制器
  action_codec.py                  关节/开度与原生控制目标映射
  state_recording.py               V3 H5 记录
integration/kitchen/
  tasks/                           catalog、legacy_catalog、execution、language
  planning/                        expert、motion、speed、meal_geometry、curved_motion
  validation/                      quality、success：在线/离线共用判据
  runtime/                         本地 API 客户端
  data/                            collect_dataset、collect_experts、replay、annotations、lerobot_export
  tools/                           可选渲染、状态查询与开发工具
  tests/                           回归测试
scripts/
  collect_kitchen_dataset.sh       正式批量采集入口
  collect_kitchen_experts.sh       指定任务/seed 的专家调试入口
experiments/kitchen_platform_v3/
  collections/                     批次、原始 H5、报告、标注和派生计划
  videos_*/                        独立的审核视频
docs/kitchen_platform_v3_20261020/  使用与格式专题文档
docs/development_log/              分阶段证据与开发记录
docs/handoff/                     本交接文档
```

### 迁移或删除的旧入口

整理时移除 13 个扁平旧入口，不保留仅转发导入的壳；原有调用、脚本和测试已适配。不要继续运行 `python -m integration.kitchen.collect_experts`。

| 原 `integration/kitchen/` 下文件 | 当前位置（同属该模块） |
| --- | --- |
| `collect_dataset.py`、`collect_experts.py`、`replay.py` | `data/` 下同名模块 |
| `expert.py`、`curved_motion.py`、`meal_geometry.py`、`reference_trajectories.py` | `planning/` 下同名模块；运动执行进一步拆到 `motion.py` |
| `expert_catalog.py` | `tasks/catalog.py` |
| `expert_quality.py` | `validation/quality.py` |
| `checkpoint.py`、`render_videos.py`、`state_demo.py`、`state_query.py` | `tools/` 下同名模块 |

仍保留顶层 `launch.py`、`platform_server.py`、`planner_client.py` 等公开服务入口，以及 V1 的 `task_catalog.py`、`task_annotation.py`、`collection.py`。`tasks/legacy_catalog.py` 用于历史轨迹语义兼容，有实际用途，不应按“旧文件”删除。

最近新增且交接时尚未跟踪的源码包括 `tasks/language.py`、`data/annotations.py`、`data/lerobot_export.py`；对应新增测试为 `test_language_annotations.py`、`test_banana_placement.py`、`test_collection_efficiency.py`、`test_lerobot_export.py`。相关专题文档和两份 2026-10-28 开发日志也需随源码移交。

### 生成文件清理的范围

历史清理删除明确失败的仿真中间文件、临时视频、开发 checkpoint 等，清单在 `experiments/kitchen_platform_v3/collections/cleanup_20261027.json`；当次为 403 个文件、约 1.66GB，保留当时 229 份合格报告/轨迹。历史失败报告引用的诊断文件可能已删除。另有提交 `59e84b1` 删除旧 `robot_arm/outputs/pick_and_place_results.csv/json`。这些是历史变更说明，本交接不再清理任何数据。

保留原始研究数据、旧成功批次、参考资产和 EEG 数据；不要为整理目录移动成功样本，已有报告可能使用绝对路径。跨机器搬迁应另做路径映射与副本校验，不手改原封存报告。

## 5. Linux 环境与采集命令

从仓库根目录执行。本机 `.venv` 使用 Python 3.12.12、MuJoCo 3.12.0、NumPy 2.5.3、h5py 3.16.0；锁定依赖见 `robot_arm/requirements-kitchen-research.txt`。新机器需重建环境，不复制 Linux venv 冒充可移植环境；建环境步骤见前述 Linux 指南。采集脚本已设置 `OPENBLAS_NUM_THREADS=1`、`OMP_NUM_THREADS=1`，避免每个进程再开大量计算线程。

### 先做一个小批次

下面目录名仅为示例，必须选未使用的新目录。此处 `--profile research` 是初态随机化，`--motion-profile faster` 是运动时序，两者不同。脚本默认运动档其实是 `standard`，示例显式选择提速档。

```bash
bash scripts/collect_kitchen_dataset.sh \
  --output experiments/kitchen_platform_v3/collections/handoff_smoke_20261028 \
  --case-ids kitchen_expert_v3:R5:hotdog \
  --profile research --motion-profile faster --samples-per-case 2 --jobs 1
```

### 全任务批次

```bash
bash scripts/collect_kitchen_dataset.sh \
  --output experiments/kitchen_platform_v3/collections/all_tasks_efficient_vla_20261028 \
  --profile research --motion-profile faster --samples-per-case 2 --jobs 2
```

命令从进程内本地客户端推进 MuJoCo，无需先启动 TCP 服务或 Unity。目标 100×2=200 条合格轨迹；默认每条最多尝试 3 次，失败换 seed，不无限重试。采集返回 0 表示目标齐全，返回 1 需查看缺失项。不要假设“两条尝试”就等于“两条合格”。

若先只检查计划，在上述命令末尾加 `--plan-only`；真正执行时用同一命令加 `--resume`。中断后恢复也加 `--resume`，并保留原参数。代码、模型、控制身份或计划参数变化后必须新建批次目录，不能改旧 `plan.json` 绕过身份校验。同一目录只允许一个调度器。

调试指定专家任务可使用：

```bash
bash scripts/collect_kitchen_experts.sh run \
  --output experiments/kitchen_platform_v3/collections/handoff_expert_20261028 \
  --case-ids kitchen_expert_v3:R5:hotdog --seeds 0 \
  --profile research --motion-profile faster
```

仅排错时加 `--diagnostics`，常规采集不加。不要把 shell 的视频播放倍速当作运动提速参数。

### 数据位置和进度

```text
<批次>/plan.json
<批次>/attempts/<case>/sample_XX/attempt_XX_seedN/
  trajectory.h5                  仅合格尝试默认保留
  report.json                    含成功/重放摘要、哈希、计时
  annotations.json               合格轨迹的英文粗/细标注
<批次>/manifest.json             已合格样本索引
<批次>/summary.json              target、qualified、missing、complete
```

调度凭据 `.job.json` / `.result.json` 和锁文件是恢复所需的小文件，不是训练数据。`summary.json`/`manifest.json` 在批次收尾生成或更新；运行中不能只依据旧汇总判断进度，还应看已封存 `report.json`。旧 `all_tasks_policy_20261027` 暂停时有 64 条合格数据，属于旧版本目标，不能直接算进本次 200 条。

最新代表性样本在 `collections/efficiency_ready_20261028/`；审核视频在 `experiments/kitchen_platform_v3/videos_efficiency_20261028/R5_hotdog_1x.mp4`。这些是验证样本，不是完整训练集。

## 6. 原始格式、语言与重放

原始 schema 为 `kitchen_state_actions_v3`，任务命名空间为 `kitchen_expert_v3`；两个版本名称表示不同对象。

- 一次初态：完整 MuJoCo integration state、初始偏置、控制器插值/目标、设备计时与标记，用于恢复起点。
- 动作：25Hz 的 `actions/joint_pos` 为 N×14，左 7/右 7，单位 rad；`actions/gripper_opening` 为 N×2，左/右目标比例，0 关闭、1 张开。
- 精确重放补充：每动作两个原生夹爪目标及必要的帧中插值重置位，避免比例往返误差；不保存 500Hz 大型 ctrl 数组。
- 关键状态：25Hz qpos/qvel、物体位置/wxyz 四元数、实测夹爪间距 m、清洗/加热标记，结尾补齐末状态。物体标识/关节索引/模型身份见元数据。
- 英文 `annotations.json` 保存粗指令和细区间。`[start_step,end_step)` 是 **0 起始动作行号**，不是物理 tick、视频帧号或关键状态行号；组合 H 阶段覆盖完整加热装盘流程。
- 不记录图像、逐步检查解释、每步 RPC 或完整诊断；必要成功检查照常执行。

独立动作重放：

```bash
.venv/bin/python -B -m integration.kitchen.data.replay verify /绝对路径/trajectory.h5
```

只恢复一次初态，然后按动作重新运行 500Hz 物理，不通过中间状态纠偏。合格同时需要状态误差满足阈值（当前 ≤1e-8）与重算后的任务成功，不能只检查“轨迹能读出来”。模型、MuJoCo 和控制环境需匹配；几何修改前的旧成功记录不自动成为新模型的成功记录。

可选审核视频，与采集/动力学检验分开：

```bash
MUJOCO_GL=egl .venv/bin/python -B -m integration.kitchen.data.replay video \
  /绝对路径/trajectory.h5 --output /tmp/kitchen_review.mp4 \
  --max-seconds 600 --speed 1
```

MP4 需要可用 EGL 和 ffmpeg。它播放已记录状态，不等同于独立动作重仿真，也不应将倍速视频作为训练图像源。

### LeRobot 派生接口

H5 保持原始真值。训练数据按 25Hz 转成 float32 的 16 维：`左7关节、左夹爪、右7关节、右夹爪`。状态夹爪为实测间距 m，动作夹爪为目标比例，两者不能混同。训练输入不直接包含物体真值/清洗加热标记等特权状态。细标注视图分阶段导出 episode，并按原轨迹分组做训练/验证划分，避免片段泄漏。

以下 `plan` 不需要 LeRobot 依赖，但需要真实合格样本及其报告/标注：

```bash
.venv/bin/python -B -m integration.kitchen.data.lerobot_export plan \
  --manifest /绝对路径/批次/manifest.json \
  --output /tmp/kitchen_lerobot_plan.json --instruction-level coarse
```

在另行配置并固定版本的 LeRobot 转换环境中执行真实导出：

```bash
python -B -m integration.kitchen.data.lerobot_export export \
  --manifest /绝对路径/批次/manifest.json \
  --output /绝对路径/新的_lerobot_目录 --instruction-level fine
```

当前仿真环境无 LeRobot/PyArrow/Torch；已验证映射、真实 H5 导出计划和 writer 协议测试，未验证真实 Parquet 写出。参考 `.cache/upstream/dexjoco` 只复用流程，不能照搬其 44 维动作或 30fps。π0.5 自定义适配、16→32 padding 与图像接口见 [LEROBOT_PI05_CN](../kitchen_platform_v3_20261020/LEROBOT_PI05_CN.md)。

## 7. 后续重点一：Unity 图像补录（方案建议，尚未实现）

用户提出在数据集转换脚本加入 Unity 图像接口，这个方向可行；建议将其作为**可选图像来源接口**，把耗时的渲染与转换器主体拆开。优先采用：

```text
合格 H5 → 独立离线补渲染任务 → 带 tick 索引的图像缓存 → LeRobot 转换器
```

这样 H5 无需改写；Unity 不可用时仍可先采轨迹；渲染失败可逐段重试，粗/细两种导出也能复用同一份图像。同步“每 add_frame 一次就阻塞请求 Unity”会把转换与 Unity 生命周期、通信和渲染失败耦合起来，不建议作为默认实现。

建议实施顺序：

1. **先做状态映射与单帧对照。** MuJoCo 保持唯一物理来源，Unity 仅展示，不再模拟食物/机械臂动力学。明确坐标系、米制单位、wxyz 四元数转换、关节轴/零位、UMI 联动、微波炉门和水龙头状态；使用完整 qpos 与对象状态恢复可见场景，避免对象根节点和子节点重复施加变换。先核对若干已知 tick 的机器人、物体和设备姿态。
2. **定义离线渲染契约。** 每次输入包含源 H5 SHA256、指定 tick、场景/资源版本、相机标识与参数、渲染随机种子。Unity 应返回同一 tick 的各相机帧和成功回执；在状态应用且渲染完成后才封存，不能用实时墙钟或普通 Update 帧号替代仿真 tick。异步 GPU 读回也必须保留原请求 tick。
3. **按导出器的同一网格补图。** 使用 `start_tick + 20*k` 的 25Hz **动作前状态**，不能简单逐行动作或逐行状态对齐；帧中 hold/reset 会使动作行数不同于规则网格。优先 front、left_wrist、right_wrist，相机可见性及内外参需记录。当前 H5 只保存了部分材质/外观信息，无法凭空恢复未记录的视觉随机化；新增渲染配置应单独封存。
4. **独立缓存和恢复。** 缓存放在新派生目录，索引保存 source SHA、tick、camera、帧路径/校验值、渲染配置身份。实现明确背压、有限重试和断点续渲染，Unity 进程可持续复用，批量传输状态，避免逐帧启动。缺帧或错误 tick 必须报错，不用黑图顶替。
5. **转换器增加可选图像 provider。** 依据 `(source_sha256,tick,camera)` 读取已验证缓存，写入 LeRobot `observation.images.*`；核对帧数、tick、动作前状态和语言边界后才标记 `vision_ready=true`。统一先渲染整条轨迹，再按粗/细视图切片，保持同一源帧对应关系。
6. **先小样验收再扩展。** 一条水果原子、一条含门/加热的主食任务、一条带餐组合，检查首帧/语言边界/末帧、设备状态与腕相机；再跑真实 LeRobot writer 和训练样本读取。训练/验证仍按原轨迹及必要的 seed/布局分组。

实现时可保留一个按需调用 Unity 的 provider 用于调试或小样，正式批量优先走缓存。渲染进程/协议、Unity 场景和图像 provider **均未实现**，上面的名称是契约建议，不能视为已有 API。需要先确认可用的 Unity 场景资源及几何映射，不默认升级 Unity 或修改既有 EEG/XR 功能。

## 8. 后续重点二：继续提高采集速度

当前采集没有 `sleep` 或按实时速度运行的限制；`time` 用于超时和统计。主要开销包括每秒 500 次物理推进、Python 控制/接触/状态处理，以及采集后再完整做一次独立重放。应分别优化**轨迹仿真时长**和**运行墙钟开销**。

同初态代表性对照：

| 用例 | 仿真时长修改前→后 | 含重放的采集墙钟 |
| --- | --- | --- |
| R5 hotdog seed0 | 41.32→31.72 s | 最终约 14.85 s；旧 profiler 墙钟不用于直接比较 |
| D09 banana/hotdog seed0 | 447.32→383.56 s | 216.15→160.56 s |

墙钟受机器与并发影响，不是全量速度承诺。最终 D09 约执行 107.39s、重放 52.02s；执行中 step API 80.22s，API 外计算约 9.22s，后者包含 IK/几何等，不等于纯规划耗时。`report.json` 的 `execution_step_api` / `execution_outside_api` 是 `execution` 子项，不能重复相加。证据见 [效率实测](../development_log/2026-10-28-kitchen-efficiency-lerobot.md)。

后续建议按以下优先级推进，每次改动都使用新批次目录：

1. **固定基线与计时。** 选 R5 两种主食、L2 香蕉、C04、D09，固定初态/seed，在一致 CPU 负载下比较 wall、simulated、execution、replay、失败/重试率。耗时应按“每条合格样本”统计，不能只看最快成功尝试。
2. **继续削减执行与重放中的 Python 开销。** 用 profile 判断接触/支撑查询、重复动态几何计算、状态复制与记录读取的热点；复用同一 tick 的只读结果、减少分配和批量调用。动态位置不能像静态 geom 归属一样跨 tick 缓存。重放当前仍需完整物理演算，先优化开销，不默认关闭它。
3. **改善剩余轨迹低速谷和必要停顿之外的等待。** 继续检查几何路径、转向、设备衔接和接触前减速；用整段时钟减少重复收敛。香蕉左臂仍保留 standard 时序，是后续重点，但此前提速暴露过放盘不稳，不能只调大速度就宣称解决。清洗/加热的模拟语义等待、抓持/稳定确认应区别对待。
4. **测并发而非盲目加进程。** 当前默认 jobs=2，脚本允许 1–4；比较单位时间合格量与重试率，再决定。还可评估把独立重放排到单独的有界队列，但采集完成不等于合格，必须等重放通过后才能进入 manifest。
5. **最后再评估更大改动。** 提高控制器限速、改变物理步长/检查频率或改加热时长均会改变执行或数据语义，不能作为无损性能开关；如确需修改，重新定义身份并做物理成功与重放回归。

不承诺全量耗时。旧资料中的 4–6 小时是旧版本估算；应先做代表性小批次，用各类最新墙钟、并发效率和重试率重新估算 200 条。Unity 补图的时间另计，保持它不阻塞轨迹采集。

## 9. 接手时的验证与边界

交接前最近一次完整测试为 **93 项通过**；其后只补导出元数据，相关 5 项再次通过。本交接仅写文档，未重跑所有物理采集。需要重新检查代码时：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python -B -m unittest discover -s integration/kitchen/tests
bash -n scripts/collect_kitchen_dataset.sh scripts/collect_kitchen_experts.sh
git diff --check
```

效率修改的增量原生证据覆盖 9 个不同 case/seed，均合格且动作重放误差为 0；语言/香蕉改动也有单独增量验证。**这些跨版本证据不能累加成同一冻结版本的全量通过率。** 后续应在冻结源码、模型和参数后完成全目录采集，审查 `missing`、失败分布及正常速度视频，再决定是否用于训练。

接手优先事项：确认最新未提交文件已移交 → 小批次实采与恢复 → 实际 LeRobot writer 验证 → Unity 状态映射/图像补录 → 按剖析结果继续提速与全量采集。没有进行真实 Quest、ND8 或物理机器人验收；清洗/加热是模拟设备标记，不是真实流体或温度。
