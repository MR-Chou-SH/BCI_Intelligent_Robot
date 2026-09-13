# Project Context: VR + EEG + Intelligent Robotic Arm System

## 1. 项目概述

本项目当前研究主线为 Context-aware BCI Shared Autonomy，近期使用完全可控的虚拟 sequential manipulation benchmark。系统结合：

- Meta Quest 3 VR/MR
- SSVEP脑电
- 视觉识别
- 场景理解与智能决策
- 机械臂控制

M1–M8 已形成可复用的工程能力基线，保留其 warnings 与证据边界。现实场景的 Passthrough / Quest Camera / YOLO / StableTarget 路线定位为已完成能力与未来扩展，不是当前主要科研 benchmark。

项目核心思想不是使用脑电连续遥控机械臂。

脑电主要承担：

> 离散意图选择（classification）

视觉和智能推理负责：

> 理解用户关注的物体及当前环境，并推断更合理的任务意图。

机械臂负责：

> 执行最终动作。

长期愿景希望形成：

`视觉感知 → VR刺激 → EEG选择 → 场景理解 → 任务规划 → 机械臂执行`

的完整闭环。

---

## 2. 长期愿景与当前研究主题

研究主题是 Context-aware BCI Shared Autonomy：EEG 提供低带宽、直接来自用户的 intent evidence；AI 根据 task context、history 与 scene state 推断意图；AI 越确定，用户操作越少，AI 越不确定，决策权越多交还用户。

近期先通过可控的虚拟积木 sequential manipulation benchmark 建立实验基础。现实厨房等场景示例属于未来扩展：

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

M1–M8 是已完成的工程能力，所有历史 warnings 和证据边界继续保留。下一阶段为 M9 — Virtual Manipulation Baseline；当前状态与验收边界以 `docs/status/PROJECT_STATUS.md` 为准，M9–M15 方向见 `docs/roadmap/context-aware-bci-shared-autonomy.md`。

`m7_unity6000/` 是 Unity 6000.0.66f2 工程；`vr_stimulus/` 是 Unity 6000.5.8f1 的 M1–M6 legacy 工程。`reference/` 是只读参考资料。已验收的现实场景 Passthrough、Camera、YOLO、StableTarget、EnvironmentRaycast 和 SSVEP binding 保留为完成能力与未来扩展，不作为当前主要 benchmark；M7.4 RGB→Environment Depth UV 路线仍为历史暂停方案。

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

M9 的职责边界是 Quest 负责虚拟桌面、积木、SSVEP 与用户交互；PC 负责 EEG 解码、logical block ID 映射及后续任务/共享自主逻辑；MuJoCo 负责 Franka FR3 + UMI gripper dynamics、IK、trajectory、Pick/Place 和执行反馈。

GitHub远程仓库地址已经确定；实际连接状态以本地Git remote配置为准。

---

## 4. 当前工程里程碑与长期第一功能目标

当前下一里程碑是：

> M9 — Virtual Manipulation Baseline（Planned）

M6 的历史证据、冻结 decoder 配置和 warnings 仍保留；本轮不因 Unity 工程入口切换而重做或重新解释 M1–M6 实验。

M1、M2、M3、M4和M5均已完成。M5在真实 Quest 3 + ND8 session 中验证了 software stimulus event、Quest-PC clock mapping、ND8 stable post-sync packet 和 software-derived sample estimate 的端到端关联；物理光学时序、硬件 sample anchor 和 hardware-exact EEG timing 仍待验证。

M9 的实验目标是：

> 在可控虚拟桌面上，通过 EEG 选择下一块积木，以统一 logical block ID 连接 Quest 选择、PC task logic 与 MuJoCo Pick / Place。

M6.4 closeout evidence summary:

- Session A：30/30 QC-valid，是 M6 阶段最完整的正式 baseline session；
- Session B1：29/30 QC-valid（10/9/10）；trial 011 因固定 5 秒 clock-sync freshness gate 无效；
- Session B2：原始 formal status 为 `incomplete`，但 association bug 修复后的只读 replay 为 30/30（10/10/10），仅属 post-hoc exploratory replay evidence；
- 固定 CH2/3/4/5/7、1000 Hz、0.5 s onset guard、demean-only、7.2/9/12 Hz、3 harmonics 的 exploratory results 显示明显 session effect，不能写成 generalized 或 online accuracy。

M6.5a 使用同一冻结配置建立了 `historical packet → rolling buffer → event → eligibility → window → decoder → prediction` 的 replay-only pseudo-online pipeline。其 0.5 s guard + 1.5 s window 的 first decision 在 A/B1/B2 固定 QC-valid trials 上逐 trial 复现了对应 offline prediction；这验证软件 extraction semantics，不等于真实 online、端到端 latency 或泛化验证。

历史 M6.5b 在该 pipeline 上以固定 0.2 s step 生成连续预测，并比较 First、2-Consecutive、3-Consecutive 三个预声明策略。它属于 exploratory engineering evidence；真实 ND8 online evidence 与其边界见 status / development logs。

M7–M8 已完成能力回顾（历史记录，非当前优先级）：

- M7.5 官方 2D detection 到 world marker 已 Quest 3 PASS；
- M7.6 eligible detection → StableTarget → stable world anchor → 三槽位 SSVEP binding 已 Quest 3 PASS；
- 固定映射为 slot 0/1/2 → 7.2/9/12 Hz，对应 `framesPerHalfCycle = 5/4/3` 和共享 frame origin；
- 非 allowlist 类别不进入 BCI target pipeline；稳定目标短暂漏检时保持 anchor/slot；
- 已知非 blocker：快速移动静态目标时约 1–2 秒旧 target 滞留；黑色刺激主观上可能比旧 M6 scene 略浅；本轮不调整；
- M8.1/M8.2a 已完成 Quest snapshot transport 与 PC final-decision orchestration；Quest 始终以冻结 snapshot 将 class 0/1/2 解析为 slot 0/1/2 与 TargetId，duplicate/unknown、EOF/reconnect、no-decision/abort 均已验证；
- M8.2b 已完成 real Quest + ND8 engineering validation 并冻结 ViewLockedHud baseline：camera-local `(-0.32, 0.18, 0.85)/(0, 0.18, 0.85)/(0.32, 0.18, 0.85)` m、`0.20 m` Quad、left-to-right assignment、duplicate suppression、leader line/marker、selection freeze；结果是 `Completed / PASS WITH WARNINGS`，不是 accuracy/latency/causal claim；
- M8.3 将 accepted frozen class → slot → TargetId + stable-world position 发布为一次性 downstream selection result；下游不得重新查询 live binding，也不开始机械臂控制；
- M8.4 在 M8.3 immutable result 上增加 group-level freeze、最多三目标的连续选择/Undo、controller Submit 与 `ConfirmedTargetBatch`；Quest 通过既有 newline JSON transport 向 PC 发布 batch。旧 closeout 中的模拟验收说明保留为历史证据边界，不代表当前 milestone；
- M8 的 confirmed batch 是既有选择能力基线；M9 将以 logical block ID 定义新的 robot task/command interface。

以下 M1–M7 顺序为历史开发建议，已被 M9–M15 roadmap 取代：

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

这是 Unity 6000.0.66f2 的 M7+ application，来源于 Meta `Unity-PassthroughCameraApiSamples` upstream commit `9105be64da8690b41154baf5629cb82dc2dbe4a7`，使用 MRUK / Meta Core 85.0.0。它包含已验收的 Passthrough、Camera API、官方 MultiObjectDetection 与 2D→world localization 能力；当前 M9 使用虚拟桌面 benchmark，现实场景能力作为未来扩展。来源、本地 M7.5 修改和许可证见 `m7_unity6000/BCI_M7_PROVENANCE.md`。

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

该模块属于现实场景未来扩展；M9 的虚拟积木 benchmark 不依赖 scene understanding。

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

M9 优先复用 `feature/add-robotArm-simulation` 的 Franka FR3 + UMI gripper + MuJoCo baseline，包含几何抓取、数值 IK、分段轨迹和 Pick/Lift/Place。不得默认重建 simulator 或低层 controller。该分支是后续正式复用来源；切换、合并、修改必须由独立任务授权。`robot_arm/` 在本项目中负责 BCI selection 到 task/command interface 的适配、integration、执行状态/反馈和端到端实验。

### 5.5 `integration`

当前 M9 的模块连接为：

`Quest SSVEP selection`
→
`logical block ID`
→
`PC task / command interface`
→
`MuJoCo Franka FR3 Pick / Place`
→
`execution status / feedback`

现实场景的 Vision → SSVEP binding 保留为已完成能力和未来扩展。

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

## 12. 历史初期路线

M0–M8 capability history is retained in PROJECT_STATUS.md and development logs. This initial M0–M7 sequence is complete and superseded. The current M9–M15 roadmap is in docs/roadmap/context-aware-bci-shared-autonomy.md.

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

## 14. 当前状态来源

当前 milestone、状态和验收边界以 docs/status/PROJECT_STATUS.md 为准；研究方向和 M9–M15 顺序以 docs/roadmap/context-aware-bci-shared-autonomy.md 为准。M1–M8 的历史 EEG/Quest evidence、warnings 与未验证项继续由对应 status 和 development logs 承载，不在这里重复维护。
