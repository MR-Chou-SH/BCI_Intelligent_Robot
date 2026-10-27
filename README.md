# BCI Intelligent Robot

## Kitchen V3 科研交接入口

全任务各两条无图像轨迹：使用 [批量采集脚本说明](docs/kitchen_platform_v3_20261020/DATASET_COLLECTION_CN.md)，目标为 64 用例 × 2 条，支持失败重试和断点续跑。

左侧连续轨迹、局部让位及全任务回 home 检查见[运动连续性记录](docs/development_log/2026-10-26-kitchen-motion-continuity.md)，内含正常速度 MP4 的位置与前后量化对比。

最新水果轨迹已改为斜向接近与曲线携带，采用整段关节插值；[曲线路径记录](docs/development_log/2026-10-27-curved-fruit-trajectories.md)提供25fps正常速度视频、实际TCP运动核对和验证边界。

**无VR时请从[物体状态查询、无图像录制与MuJoCo MP4／viewer重放](docs/kitchen_platform_v3_20261020/STATE_RECORD_REPLAY_CN.md)开始。** 可直接运行PC录制／重放示例；当前Planner API为1.3.0，原接口保留。

当前默认 V3：一次初态、25Hz 双臂目标关节位置和夹爪开度、关键状态；通过动作重放与独立成功判定后入库。不自动渲染，不记录逐步检查详情。见[模块结构](integration/kitchen/README.md)与[字段/单位](docs/kitchen_platform_v3_20261020/DATASET_FORMAT_CN.md)。2026-10-27 另清理约1.66GB失败仿真中间文件，历史成功数据保留原位。

当前 Linux 与任务采集开发请读 [Linux环境、局部随机化与任务分段](docs/kitchen_platform_v3_20261020/LINUX_TASK_COLLECTION_CN.md)。两张新任务表的20＋44个用例及批量脚本见 [Expert V2采集说明](docs/kitchen_platform_v3_20261020/EXPERT_V2_COLLECTION_CN.md)。新分支 `feature/kitchen-linux-task-collection` 保持左水果／右主食布局；全部流程已接入，逐任务物理验收仍在进行，失败尝试不计入专家数据。

**负责 VLA / 轨迹规划的同学请先读 [中文开发交接指南](docs/kitchen_platform_v3_20261020/VLA_PLANNER_HANDOFF_CN.md)。** 无需 Quest 即可在 Windows PowerShell 5.1 启动 MuJoCo、随机 reset、调用 Planner API，并读取两条真实固定场景参考控制轨迹。

- [Planner API 中文接口说明](docs/kitchen_platform_v3_20261020/PLANNER_API_GUIDE_CN.md)
- [两条参考轨迹及验证边界](reference/kitchen_v3_trajectories/README.md)
- [Quest Research 中文用户手册（39 项界面字段）](docs/kitchen_platform_v3_20261020/RESEARCH_USER_GUIDE_CN.md)
- [本次发布验收结果](docs/kitchen_platform_v3_20261020/HANDOFF_RELEASE_VERIFICATION_CN.md)

本分支使用候选 013 / API 1.3.0；候选尚未擅自晋升为正式布局。苹果新版避水为历史内部动力学 PASS，汉堡有历史公开 API 完整流程证据且终点仅为炉外持物；均不代表本轮Linux新身份或随机场景泛化成功。原 Goal 保持 PAUSED。本轮推进无图像状态采集与重放，尚未启动 VLA 训练，下面保留原项目背景。

研究 Context-aware BCI Shared Autonomy：EEG 提供用户的低带宽 intent evidence，任务 context 逐步支持更高的机器人自主性。

## Project Goal

当前近期 benchmark 使用可控的虚拟桌面与 sequential block manipulation：

Quest 3 负责虚拟积木、SSVEP 与交互；PC 负责 EEG 解码和 task logic；MuJoCo 负责 Franka FR3 + UMI gripper 仿真和执行反馈。

总体流程：

`Quest SSVEP → EEG block selection → logical block ID → PC task logic → MuJoCo Pick / Place`

Passthrough、Quest Camera、YOLO、StableTarget 和现实场景 SSVEP binding 是 M1–M8 已完成能力与未来扩展，不是当前主要 benchmark。

## Current Stage

以下是原项目历史背景，不覆盖上方 Kitchen V3 交接范围。M1–M8 工程能力已完成，历史 warnings 和 evidence boundaries 保留。该历史路线下一阶段是 **M9 — Virtual Manipulation Baseline**；详细状态见 [PROJECT_STATUS.md](docs/status/PROJECT_STATUS.md)，研究路线见 [M9–M15 roadmap](docs/roadmap/context-aware-bci-shared-autonomy.md)。

M9 的 logical block ID 示例为 `block_red_01`、`block_green_01`、`block_blue_01`。MuJoCo 低层仿真优先复用 `feature/add-robotArm-simulation`；本项目承担 BCI selection、task/command interface、integration、execution status/feedback 和端到端实验。M9 不做 VLM、LLM、Context AI、RL 或 Dynamic Stopping。

`m7_unity6000/` 是 Unity 6000.0.66f2 工程；`vr_stimulus/` 是 Unity 6000.5.8f1 的 M1–M6 legacy 工程和可复用代码来源。

M9 当前复用 FR3 + UMI + MuJoCo 快照 `a42a0876350f6c94747ce93fa640807b14b18bfd`：`robot_arm/` 保存运行所需的原始规划器、预编译模型和被引用网格，`integration/m9_mujoco_execution.py` 实现 logical ID 到场景对象的绑定及 headless 调用。运行环境要求和场景映射说明见 [robot_arm/README.md](robot_arm/README.md)。真实 headless smoke 已在隔离的 CPython 3.12.14 + MuJoCo 3.12.0 环境中返回 `place_ok`；默认 CPython 3.9 verifier 环境保持未安装 MuJoCo。

## Main Directories

- `vr_stimulus/` — M1–M6 legacy Unity project（Unity 6000.5.8f1）与已验证 SSVEP / trigger 代码来源
- `m7_unity6000/` — M7+ active Unity project（Unity 6000.0.66f2；Meta 官方 PCA sample 基线）
- `vision/` — 现实场景视觉能力与未来扩展
- `eeg/` — ND8采集、预处理和SSVEP分类
- `robot_arm/` — 复用的 FR3/UMI 仿真基线最小运行子集；不含重复 vendor 模型源
- `integration/` — confirmed selection、logical ID scene binding、机器人 adapter 与执行结果
- `experiments/` — 实验记录与结果
- `reference/` — 只读参考资料
- `docs/` — 项目文档、决策、文献和开发记录
- `docs/agent/overnight/` — long-running agent policy 与运行模板

## AI Development Workflow

本项目主要使用：

- ChatGPT：理论、架构、决策、方案审核
- Codex：仓库阅读、编程、测试、Git与工程操作

Codex在开始任务前应阅读：

1. `AGENTS.md`
2. `project_context.md`
3. `docs/status/PROJECT_STATUS.md`
4. `docs/roadmap/context-aware-bci-shared-autonomy.md`（涉及研究方向时）

## Development Principles

- 小步开发；
- 每一步必须可以验证；
- 使用Git保存稳定节点；
- 不直接修改历史参考项目；
- 不随意升级Unity和XR依赖；
- 硬件结果必须实机验证。

## Current Milestones

- [x] M0 — Project initialization
- [x] M1 — Empty Unity project runs on Quest 3
- [x] M2 — Passthrough + one fixed square
- [x] M3 — One SSVEP flicker target
- [x] M4 — Three independent flicker targets
- [x] M5 — Stimulus timing and EEG synchronization
- [x] M6 — ND8 EEG decoding and validation（Completed / PASS WITH WARNINGS；证据边界保留）
- [x] M7 — Vision-guided SSVEP Target Binding（Quest 3 PASS；slot 0/1/2 = 7.2/9/12 Hz，`5/4/3` frame-driven）
- [x] M8 — SSVEP slot / EEG class / TargetId selection and confirmed batch capability（warnings and evidence boundaries preserved）
- [ ] Next — M9 Virtual Manipulation Baseline
