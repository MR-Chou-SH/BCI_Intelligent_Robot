# BCI Intelligent Robot

基于 Meta Quest 3、SSVEP脑电、视觉识别、场景理解和机械臂的智能脑机接口科研项目。

## Project Goal

系统最终希望实现：

用户通过VR/MR观察真实环境，系统识别候选物体并生成SSVEP视觉刺激；用户通过EEG选择目标，系统结合场景上下文推断任务，并控制机械臂执行。

总体流程：

`Vision → SSVEP Stimulus → EEG Classification → Task Reasoning → Robot Execution`

## Current Stage

M0、M1、M2、M3和M4已完成。当前工程里程碑是：

`M5 — Stimulus Timing / EEG Trigger Synchronization`

M4已完成三目标共享frame origin的frame-driven黑白闪烁、Quest 3实机视觉验收及30秒软件侧时序诊断。72 Hz runtime下推导软件频率为`7.2/9/12 Hz`；当前准备设计刺激开始/停止时间记录与EEG trigger同步接口。推导频率尚未经过物理光学测量验证。

后续第一个核心功能目标：

> 在Meta Quest 3中，在三个指定三维坐标显示三个指定频率的黑白SSVEP闪烁方块。

视觉识别、机械臂和智能场景理解暂不在当前里程碑实施。

## Main Directories

- `vr_stimulus/` — Quest 3与SSVEP视觉刺激
- `vision/` — 视觉识别与空间定位
- `eeg/` — ND8采集、预处理和SSVEP分类
- `robot_arm/` — 机械臂控制
- `integration/` — 模块集成
- `experiments/` — 实验记录与结果
- `reference/` — 只读参考资料
- `docs/` — 项目文档、决策、文献和开发记录

## AI Development Workflow

本项目主要使用：

- ChatGPT：理论、架构、决策、方案审核
- Codex：仓库阅读、编程、测试、Git与工程操作

Codex在开始任务前应阅读：

1. `AGENTS.md`
2. `project_context.md`
3. `docs/status/PROJECT_STATUS.md`

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
- [ ] M5 — Stimulus timing and EEG synchronization (Ready to Start)
- [ ] M6 — Online ND8 + EEG decoding
- [ ] M7 — Vision-based automatic target placement
