# 只读参考资料说明

## 来源

本目录资料于 2026-08-13 从 `C:\Users\zsh21\Desktop\EEG_back_up` 只读筛选复制。原目录未被修改。所有历史项目、SDK、示例和文档在本项目中默认均为只读参考，不能直接运行或在原地开发。

## 目录

- `neurodance/`：Neurodance ND8 Python SDK 演示、使用说明和分发包，以及 Tello SDK 文档。
- `drone_demo/`：从 Drone2.1 打包内容中逐个筛选的关键业务源码和历史 README。
- `papers/`：预留给经确认可归档的论文资料；当前为空。

## 分析脑电无人机流程的重点

- `OperationMain.py`：历史主业务流程入口。
- `Drone_psycho.py`：无人机与 PsychoPy 相关控制流程。
- `ND8.py`：ND8 设备相关封装。
- `spatialFilter.py`：空间滤波处理。
- `RoboMasterThread2.py`：RoboMaster 控制线程参考。
- `Config.py`：历史业务配置。
- `wheel_core.py`：历史打包业务依赖中的核心 Python 文件；仅因原指令明确指定而单独保留。
- `nd_device_demo.py`：Neurodance Python SDK 设备示例。
- `Tello_SDK_3.0_User_Guide_cn.pdf`：Tello SDK 中文文档。

## 明确未复制

- Drone2.1 `_internal` 中除上述明确指定源码外的大量 Python 第三方运行库。
- `Drone2.1.rar` 大型历史打包归档。
- NeuroAI 安装器、Drone/NDDrone 可执行文件及 dll。
- FFmpeg、SciPy、scikit-learn 等可重新安装或生成的第三方依赖副本。
- 其他打包运行时、调试符号和无关二进制依赖。

## 待人工确认

- 扫描来源目录时未发现原指令提及的 `pics2/`，因此没有复制，也没有搜索其他目录。
