# Project Context: VR + EEG + Intelligent Robotic Arm System

## 1. 项目概述

本项目旨在开发一个结合以下技术的脑机接口智能机器人系统：

- Meta Quest 3 VR/MR
- SSVEP脑电
- 视觉识别
- 场景理解与智能决策
- 机械臂控制

项目核心思想不是使用脑电连续遥控机械臂。

脑电主要承担：

> 离散意图选择（classification）

视觉和智能推理负责：

> 理解用户关注的物体及当前环境，并推断更合理的任务意图。

机械臂负责：

> 执行最终动作。

最终系统希望形成：

`视觉感知 → VR刺激 → EEG选择 → 场景理解 → 任务规划 → 机械臂执行`

的完整闭环。

---

## 2. 最终愿景

例如现实场景中存在：

- 面包
- 微波炉
- 水杯
- 盘子

Quest 3首先识别环境中的物体。

随后系统在候选物体附近叠加不同编码的SSVEP视觉刺激。

用户注视其中一个刺激目标。

EEG系统判断用户选择了哪个目标。

例如：

`用户选择面包`

系统未来不应简单理解为：

`抓起面包`

而应该结合：

`面包 + 微波炉 + 当前任务状态`

推理更合理的操作，例如：

`抓取面包 → 放入微波炉 → 加热 → 取出`

因此整个项目最终研究重点是：

`EEG意图选择 + 场景理解 + 智能机器人执行`

而不仅是提高脑电分类准确率。

---

## 3. 当前开发阶段

项目已完成 M1–M5 的 Quest/SSVEP/同步工作、M6 的 ND8 decoder / diagnostic-live 验证（均保留其 warnings 与证据边界），并已完成 M7 视觉目标到 SSVEP target binding。M7+ 的唯一 active Unity application 为仓库内 `m7_unity6000/`（Unity 6000.0.66f2）；`vr_stimulus/` 是 Unity 6000.5.8f1 的 M1–M6 legacy 工程，不再作为 M7 默认入口。

M7.5 已在 Quest 3 真机验证 Meta 官方链路：`MultiObjectDetection → PassthroughCameraAccess.ViewportPointToRay → EnvironmentRaycastManager.Raycast → world marker`。M7.6 已在同一正式工程中完成 `eligible detection → StableTarget → stable world anchor → at most three SSVEP slots` 的 Quest 3 真机验收。M7.4 自研 RGB→Environment Depth UV 路线保留在历史 checkpoint 中但继续暂停；不重复实现 PCA、YOLO 或 raycast 基线。

已经完成：

- 已安装国际版Unity；
- 已拥有Meta Quest 3；
- 已获得Neurodance ND8相关Python SDK资料；
- 已获得师兄完成的脑电控制无人机Demo；
- 已初步分析无人机项目的完整数据链路。
- 已建立正式项目根目录；
- 已建立本地Git仓库；
- 已建立项目管理Markdown体系；
- 已完成reference资料整理。
- 已创建正式Unity Quest项目；
- 已在Meta Quest 3实机完成最小VR应用构建与运行验证；
- 已在Meta Quest 3实机完成沉浸式Passthrough基线验证。
- 已在Meta Quest 3实机完成固定世界坐标虚拟方块验证。
- 已完成单个世界坐标固定、帧驱动黑白SSVEP目标及软件侧时序诊断；Quest runtime为72 Hz，`framesPerHalfCycle = 3`，推导软件频率为12 Hz。
- 已完成三目标共享frame origin的帧驱动SSVEP基线；Quest runtime为72 Hz，N为`5/4/3`，推导软件频率为`7.2/9/12 Hz`，30秒软件侧时序验证PASS。

当前尚未完成：

- formal prospective cross-session validation；
- pseudo-online replay infrastructure and later real-time classification readiness；
- `SSVEP slot ↔ EEG class ↔ real-world TargetId` 集成；
- 机械臂正式集成。

GitHub远程仓库地址已经确定；实际连接状态以本地Git remote配置为准。

---

## 4. 当前工程里程碑与长期第一功能目标

当前 active 工程里程碑是：

> M7 — Vision-guided SSVEP Target Binding（Completed / PASS）

M6 的历史证据、冻结 decoder 配置和 warnings 仍保留；本轮不因 Unity 工程入口切换而重做或重新解释 M1–M6 实验。

M1、M2、M3、M4和M5均已完成。M5在真实 Quest 3 + ND8 session 中验证了 software stimulus event、Quest-PC clock mapping、ND8 stable post-sync packet 和 software-derived sample estimate 的端到端关联；物理光学时序、硬件 sample anchor 和 hardware-exact EEG timing 仍待验证。

长期第一功能目标是：

> 在Meta Quest 3中，在三个指定三维坐标显示三个具有指定刺激频率的黑白闪烁方块。

M6.4 closeout evidence summary:

- Session A：30/30 QC-valid，是当前最完整的正式 baseline session；
- Session B1：29/30 QC-valid（10/9/10）；trial 011 因固定 5 秒 clock-sync freshness gate 无效；
- Session B2：原始 formal status 为 `incomplete`，但 association bug 修复后的只读 replay 为 30/30（10/10/10），仅属 post-hoc exploratory replay evidence；
- 固定 CH2/3/4/5/7、1000 Hz、0.5 s onset guard、demean-only、7.2/9/12 Hz、3 harmonics 的 exploratory results 显示明显 session effect，不能写成 generalized 或 online accuracy。

M6.5a 使用同一冻结配置建立了 `historical packet → rolling buffer → event → eligibility → window → decoder → prediction` 的 replay-only pseudo-online pipeline。其 0.5 s guard + 1.5 s window 的 first decision 在 A/B1/B2 固定 QC-valid trials 上逐 trial 复现了对应 offline prediction；这验证软件 extraction semantics，不等于真实 online、端到端 latency 或泛化验证。

M6.5b 在该 pipeline 上以固定 0.2 s step 生成连续预测，并比较 First、2-Consecutive、3-Consecutive 三个预声明策略。当前仅形成 exploratory engineering candidate，不进行 threshold/step tuning；真实 ND8 online 仍需另行授权与验证。

现阶段：

- M7.5 官方 2D detection 到 world marker 已 Quest 3 PASS；
- M7.6 eligible detection → StableTarget → stable world anchor → 三槽位 SSVEP binding 已 Quest 3 PASS；
- 固定映射为 slot 0/1/2 → 7.2/9/12 Hz，对应 `framesPerHalfCycle = 5/4/3` 和共享 frame origin；
- 非 allowlist 类别不进入 BCI target pipeline；稳定目标短暂漏检时保持 anchor/slot；
- 已知非 blocker：快速移动静态目标时约 1–2 秒旧 target 滞留；黑色刺激主观上可能比旧 M6 scene 略浅；本轮不调整；
- 当前不接入 EEG selection、SSVEP slot 与 EEG class/real-world TargetId 的集成或机械臂；
- 不接入机械臂；
- 不在本轮改动 M6 pseudo-online / decoder 证据；
- 三个刺激的历史 frame-driven 参数仍是后续复用来源。

完成刺激模块后再逐步：

1. 接入刺激同步；
2. 与ND8 EEG采集建立联动；
3. 跑通EEG分类；
4. 接入视觉识别；
5. 将标签位置改为视觉检测结果；
6. 接入机械臂；
7. 最后研究场景理解与智能任务规划。

---

## 5. 系统模块

正式工程划分为以下模块。

### 5.1 `vr_stimulus`

负责：

- Meta Quest 3
- Unity XR
- Passthrough
- SSVEP视觉刺激
- 刺激位置
- 刺激频率
- 帧/时序管理
- EEG trigger同步

这是 Unity 6000.5.8f1 的 M1–M6 legacy Unity project。它保留已验证的 frame-driven SSVEP、M5 trigger / Quest-PC communication 及相关历史场景；M7+ 不再直接在此工程继续 Unity 开发。

### 5.1.1 `m7_unity6000`

这是 Unity 6000.0.66f2 的 M7+ active Unity application，来源于 Meta `Unity-PassthroughCameraApiSamples` upstream commit `9105be64da8690b41154baf5629cb82dc2dbe4a7`，使用 MRUK / Meta Core 85.0.0。它包含已通过 Quest 3 真机验证的 Passthrough、Camera API、官方 MultiObjectDetection 与 2D→world localization 基线。后续视觉→SSVEP→EEG→robot 的 Unity 侧集成优先在此进行；具体来源、本地 M7.5 修改和许可证见 `m7_unity6000/BCI_M7_PROVENANCE.md`。

### 5.2 `vision`

负责：

- Quest摄像头数据
- 目标检测
- 物体类别
- 位置估计
- 输出统一的检测对象信息

未来可能使用：

- YOLO系列
- 其他轻量目标检测模型
- Unity端推理或PC端推理

具体技术路线尚未最终确定。

### 5.3 `eeg`

负责：

- Neurodance ND8连接
- EEG数据采集
- EEG缓存
- 数据截取
- 信号预处理
- SSVEP分类
- 算法实验与评估

### 5.4 `robot_arm`

负责：

- 机械臂通信
- 预定义动作
- 预定义轨迹
- 动作执行
- 机械臂状态
- 安全处理

MuJoCo 机械臂仿真与底层控制系统允许复用同门正在开发的系统；本项目不需从零重建。`robot_arm/` 主要定义/适配 BCI→robot command/task interface、执行状态与安全反馈，并与 integration 完成端到端实验。

### 5.5 `integration`

负责模块之间的数据和事件连接，例如：

`Vision detected object`
→
`VR assigns SSVEP target`
→
`EEG returns selected class`
→
`Task selected`
→
`Robot executes`

### 5.6 文献与研究支持

文献不作为独立软件模块。

统一放在：

`docs/literature/`

用于支持：

- SSVEP
- VR BCI
- EEG decoding
- computer vision
- scene understanding
- shared autonomy / robotic control

---

## 6. 当前脑电设备和已有资料

脑电设备：

Neurodance ND8无线便携式脑电采集系统。

已有：

- Python SDK
- SDK示例
- 脑电控制无人机完整Demo
- Tello SDK资料

目前已有无人机Demo的关键代码包括：

- `OperationMain.py`
- `Drone_psycho.py`
- `ND8.py`
- `spatialFilter.py`
- `RoboMasterThread2.py`
- `Config.py`
- `wheel_core.py`
- `pics2/`（当前在指定备份目录扫描范围内未找到，待后续确有需要时确认）

---

## 7. 已有无人机项目的数据链路

目前已初步确认：

`Drone_psycho.py`
负责PsychoPy视觉刺激。

刺激开始时发送：

`TIME:<timestamp>`

给EEG处理程序。

`ND8.py`
负责从本地脑电数据服务读取EEG，并按时间戳截取对应刺激窗口。

主要服务地址曾配置为：

`127.0.0.1:8899`

EEG经过预处理后送入：

`spatialFilter.py`

其中包含FBCCA实现。

FBCCA完成SSVEP频率类别预测。

分类结果通过：

`RSLT:<class>`

返回控制程序。

分类编号再映射到：

- takeoff
- up
- land
- down
- forward
- right
- back
- left
- flip

等Tello风格无人机命令。

最后通过UDP发送给无人机。

因此现有参考系统的核心闭环是：

`视觉刺激 → EEG采集 → 预处理 → FBCCA → 类别 → 动作映射 → 无人机`

未来机械臂项目将在理解此闭环之后重新设计正式模块。

---

## 8. EEG算法定位

老师已经明确：

脑电在整个系统中主要承担classification。

即：

屏幕/VR中存在多个不同编码的闪烁刺激。

EEG分类器只需要判断：

> 用户正在选择哪个目标？

因此脑电算法不是整个系统唯一重点。

### 当前baseline

现有无人机Demo使用：

FBCCA

因此首先需要：

- 理解；
- 复现；
- 保留为baseline。

### 后续候选方法

可能研究：

- CCA
- FBCCA
- TRCA
- EEGNet
- MTSGNN
- EEG-Conformer
- Transformer类方法
- Mamba / State Space Model类方法

### Diffusion

暂时主要作为：

- EEG去噪
- 数据增强
- 信号增强

方向研究。

不默认使用Diffusion直接替代分类器。

---

## 9. MTSGNN说明

项目参考过动态背景SSVEP论文中的MTSGNN。

这里的MTSGNN不应简单理解为常规Graph Neural Network。

其核心设计包括：

- multi-scale temporal convolution
- spatial convolution
- separable convolution
- global average pooling
- softmax classification

该方法对动态背景SSVEP任务具有参考价值。

---

## 10. VR技术路线

正式目标设备：

Meta Quest 3

正式开发引擎：

国际版Unity。

不将以下方案作为最终正式路线：

- 团结引擎
- WebXR
- Unreal Engine

原因主要不是显示效果，而是需要长期使用Meta Quest官方Unity开发生态，包括：

- OpenXR
- Meta XR SDK
- Passthrough
- Quest专属接口
- 后续摄像头/视觉功能

---

## 11. SSVEP刺激实现原则

第一阶段至少需要支持：

- 3个目标；
- 独立三维坐标；
- 独立刺激频率；
- 黑白刺激；
- 同步开始/停止；
- 刺激时间记录；
- 刷新率记录；
- 后续EEG trigger接口。

刺激属于实验关键时序。

不能仅依靠普通低精度计时器并假设最终物理显示频率准确。

需要关注：

- Quest实际刷新率；
- 渲染帧；
- 掉帧；
- 相位；
- 软件时间戳；
- 最终真实显示时序验证。

---

## 12. 第一阶段开发路线

### Milestone 0

项目初始化：

- 目录
- Git
- GitHub
- Markdown项目管理
- reference整理

### Milestone 1

Unity空项目成功运行到Quest 3。

### Milestone 2

实现Passthrough，并显示一个固定虚拟方块。

### Milestone 3

实现单个指定频率SSVEP闪烁方块。

### Milestone 4

实现三个指定位置、不同频率刺激目标。

### Milestone 5

增加刺激时间、日志和EEG同步接口。

### Milestone 6

接入ND8 EEG并跑通在线分类。

### Milestone 7

开发视觉识别并自动给真实物体绑定刺激目标。

### 后续

机械臂、场景理解和智能任务规划。

---

## 13. GPT与Codex协作方式

### GPT网页端

主要负责：

- 理论学习
- 系统设计
- 技术方案讨论
- Codex任务设计
- Codex方案审核
- 实验结果分析
- 文献理解
- 项目级决策

### Codex

主要负责：

- 阅读完整工程
- 搜索调用关系
- 创建和修改代码
- 创建项目文件
- 运行命令
- Git操作
- 构建和测试
- 分析错误日志
- 工程重构

原则：

GPT主要帮助“想清楚”。

Codex主要负责“在仓库里执行”。

---

## 14. 当前最高优先级

M1–M6 已完成并保留 warnings，M7 视觉→SSVEP binding 已 Completed / PASS。当前最高优先级为：

> `SSVEP slot ↔ EEG class ↔ real-world TargetId` integration

以 `m7_unity6000/` 为唯一 active Unity application；下一阶段只定义并实现 SSVEP 槽位、EEG 分类结果与稳定 `TargetId` 的最小接口。不得重做已 PASS 的 Passthrough、Camera API、YOLO、official raycast、StableTarget 或 frame-driven SSVEP baseline；不得在接口尚未单独设计前开始机械臂集成。`vr_stimulus/` 仅作为 M1–M6 的代码与实验证据来源。

M6.0–M6.7 已完成并保留 warnings。M6.7 的 `stress_online` session `m6_7-formal-20260820T160940Z-0ef360f6` 在非理想精神/注意力状态下，以冻结 CH2/CH4/CH7（3/5 engineering admission）完成 30 trials、10/10/10 randomized、30/30 technical-valid、30/30 decisions、30/30 post-hoc correct，logical decision 为 2.2 s。该结果是 non-ideal-condition engineering stress evidence，不是 primary formal online accuracy；不能宣称 three-channel equivalence、cross-subject/generalized performance 或 physical end-to-end latency。两次真实 ND8 disconnect 导致的 incomplete preflight 必须保留；随后 120 s ND8-only stability check PASS（594 packets、593 continuous、1 startup anomaly、无 callback/runtime error）。当前冻结 decoder 已足以支持下一阶段 BCI decision → robot command interface 集成，但该集成属于下一 milestone，M6 closeout 不开始 M7 编码。M5/M6 的证据边界继续保留：`hardwareTimingVerified=false`、`physicalOpticalTimingVerified=false`，ND8 hardware sample anchor 与 hardware-exact timing 未验证，sample index 仅为 `software-derived estimate`，且名义刺激频率未获独立 optical measurement。当前 workspace 使用 NumPy 2.2.6 与 SciPy 1.14.1，但仓库尚无正式 requirements/pyproject dependency declaration，属于可复现性 warning。
