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

项目已完成M1最小Quest应用、M2沉浸式Passthrough与固定虚拟方块、M3单目标SSVEP、M4三目标SSVEP的Quest 3实机验证，以及M5刺激时序与EEG trigger同步和首次真实 Quest→ND8 sample association 验证。当前准备进入M6：ND8 EEG online classification。

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

- ND8 EEG online classification；
- 视觉识别模块；
- 机械臂正式集成。

GitHub远程仓库地址已经确定；实际连接状态以本地Git remote配置为准。

---

## 4. 当前工程里程碑与长期第一功能目标

当前工程里程碑是：

> M5 — Stimulus Timing / EEG Trigger Synchronization（Completed / PASS）

M1、M2、M3、M4和M5均已完成。M5在真实 Quest 3 + ND8 session 中验证了 software stimulus event、Quest-PC clock mapping、ND8 stable post-sync packet 和 software-derived sample estimate 的端到端关联；物理光学时序、硬件 sample anchor 和 hardware-exact EEG timing 仍待验证。

长期第一功能目标是：

> 在Meta Quest 3中，在三个指定三维坐标显示三个具有指定刺激频率的黑白闪烁方块。

现阶段：

- 不自动识别物体；
- 不接入机械臂；
- 不要求EEG在线分类；
- 三个刺激位置可以预先指定；
- 刺激频率可以预先指定。

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

当前优先开发此模块。

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

机械臂具体型号及控制接口后续补充。

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

M1、M2、M3、M4和M5均已完成。当前最高优先级为：

> M6 — ND8 EEG Online Classification

M6.0 已完成 legacy / architecture audit。M6.1a 已建立软件侧 ND8 全八通道 sanity recording 与离线质量分析工具，真实 ND8 / Quest sanity validation 尚未执行；本轮仍未实现 CCA、FBCCA 或在线分类。M5 的证据边界必须继续保留：`hardwareTimingVerified=false`、`physicalOpticalTimingVerified=false`，sample index 仅为 `software-derived estimate`。
