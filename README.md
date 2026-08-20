# BCI Intelligent Robot

基于 Meta Quest 3、SSVEP脑电、视觉识别、场景理解和机械臂的智能脑机接口科研项目。

## Project Goal

系统最终希望实现：

用户通过VR/MR观察真实环境，系统识别候选物体并生成SSVEP视觉刺激；用户通过EEG选择目标，系统结合场景上下文推断任务，并控制机械臂执行。

总体流程：

`Vision → XR/SSVEP Stimulus → EEG Target Selection → Scene Understanding / Task Reasoning → Robot Execution`

## Current Stage

M0 至 M5 已完成；M6 正在进行离线 EEG decoder 与跨 session robustness 验证：

`M6.4 — Cross-Session Generalization / Robustness Validation: exploratory analysis performed; formal completion pending`

M6.1b 的 Session A 是当前最完整的正式 dataset（30/30 QC-valid）。M6.2 与 M6.3 已完成固定 CCA/FBCCA offline baselines 和 window/filter-realization characterization。M6.4 已完成 B1/B2 的只读 replay 与 exploratory cross-session analysis；B1 为 29 QC-valid trials，B2 的 replay 30/30 仅是 post-hoc exploratory evidence，原始 formal 状态仍为 incomplete。

当前不进入 pseudo-online，也不计划立即进行第四次 EEG acquisition。软件 sample association、硬件 sample anchor、硬件/光学 timing 与名义刺激频率的光学验证仍是证据边界。

EEG 在系统中承担离散 target / intention selection，不承担机械臂关节或位置的连续控制。机械臂 MuJoCo 仿真与底层控制将复用同门系统；本项目负责 BCI→robot command/task interface、integration、execution status/feedback 与端到端实验。

视觉、scene understanding 与 robot integration 仍是后续里程碑。

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
- [x] M5 — Stimulus timing and EEG synchronization
- [ ] M6 — ND8 EEG decoding and validation (M6.0–M6.3 complete; M6.4 exploratory/formal pending)
- [ ] M7 — Vision-based automatic target placement
