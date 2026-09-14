# BCI Intelligent Robot

研究 Context-aware BCI Shared Autonomy：EEG 提供用户的低带宽 intent evidence，任务 context 逐步支持更高的机器人自主性。

## Project Goal

当前近期 benchmark 使用可控的虚拟桌面与 sequential block manipulation：

Quest 3 负责虚拟积木、SSVEP 与交互；PC 负责 EEG 解码和 task logic；MuJoCo 负责 Franka FR3 + UMI gripper 仿真和执行反馈。

总体流程：

`Quest SSVEP → EEG block selection → logical block ID → PC task logic → MuJoCo Pick / Place`

Passthrough、Quest Camera、YOLO、StableTarget 和现实场景 SSVEP binding 是 M1–M8 已完成能力与未来扩展，不是当前主要 benchmark。

## Current Stage

M1–M8 工程能力已完成，历史 warnings 和 evidence boundaries 保留。下一阶段是 **M9 — Virtual Manipulation Baseline**；详细当前状态见 [PROJECT_STATUS.md](C:/Users/zsh21/Desktop/BCI_Intelligent_Robot/docs/status/PROJECT_STATUS.md)，研究路线见 [M9–M15 roadmap](C:/Users/zsh21/Desktop/BCI_Intelligent_Robot/docs/roadmap/context-aware-bci-shared-autonomy.md)。

M9 的 logical block ID 示例为 `block_red_01`、`block_green_01`、`block_blue_01`。MuJoCo 低层仿真优先复用 `feature/add-robotArm-simulation`；本项目承担 BCI selection、task/command interface、integration、execution status/feedback 和端到端实验。M9 不做 VLM、LLM、Context AI、RL 或 Dynamic Stopping。

`m7_unity6000/` 是 Unity 6000.0.66f2 工程；`vr_stimulus/` 是 Unity 6000.5.8f1 的 M1–M6 legacy 工程和可复用代码来源。

M9 当前复用 FR3 + UMI + MuJoCo 快照 `a42a0876350f6c94747ce93fa640807b14b18bfd`：`robot_arm/` 保存运行所需的原始规划器、预编译模型和被引用网格，`integration/m9_mujoco_execution.py` 实现 logical ID 到场景对象的绑定及 headless 调用。运行环境要求和场景映射说明见 [robot_arm/README.md](C:/Users/zsh21/Desktop/BCI_Intelligent_Robot/robot_arm/README.md)。真实 headless smoke 已在隔离的 CPython 3.12.14 + MuJoCo 3.12.0 环境中返回 `place_ok`；默认 CPython 3.9 verifier 环境保持未安装 MuJoCo。

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
