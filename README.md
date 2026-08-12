# BCI Intelligent Robot

面向 Meta Quest 3、SSVEP 脑电、视觉识别、智能场景理解与机械臂协同的长期科研开发项目。

## 当前状态

项目已完成工程骨架初始化，尚未进入功能开发。当前仓库不包含 Unity 工程、SSVEP 刺激实现、视觉算法、脑电算法或机械臂控制代码。

## 目录说明

- `docs/`：架构、决策、开发日志、文献、会议记录和项目状态。
- `reference/`：从历史资料中筛选的只读参考文件，不能原地修改或直接作为生产依赖。
- `vr_stimulus/`：未来的 Meta Quest 3 与刺激呈现模块。
- `vision/`：未来的视觉识别模块。
- `eeg/`：未来的脑电采集与算法模块。
- `robot_arm/`：未来的机械臂模块。
- `integration/`：未来的跨模块集成。
- `experiments/`：未来的实验方案、配置和结果索引。

## 项目管理入口

- 长期背景：`project_context.md`
- 当前状态：`docs/status/PROJECT_STATUS.md`
- 架构决策：`docs/decisions/`
- 开发记录：`docs/development_log/`
- 协作规范：`AGENTS.md`

## 安全提示

历史 SDK 和项目仅供只读分析。不要运行其中的可执行程序，不要修改原始脑电资料，不要把大型运行环境、敏感数据或凭据提交到仓库。
