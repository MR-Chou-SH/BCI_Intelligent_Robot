# FR3 + UMI + MuJoCo 最小基线快照

本目录集成自 `feature/add-robotArm-simulation` 的只读快照
`a42a0876350f6c94747ce93fa640807b14b18bfd`（2026-09-14 检查）。

## 接入内容

`assets/fr3_umi_merged.xml`、其 44 个直接引用的 `assets/meshes/` 文件，
以及 `utils/ik.py`、`utils/gripper_scene.py`、`utils/camera_utils.py`、
`control/gripper_planner.py` 和 `tests/test_pick_and_place.py` 均从该快照逐字节复制。
网格文件保留源 blob 内容。没有复制模型生成器、GUI 回放辅助模块或重复的
`vendor/mujoco_menagerie` 源资产。FR3 与 UMI 来源的许可证文本保存在
`licenses/`。

BCI 集成入口位于 `integration/m9_mujoco_execution.py`。它添加本目录到 Python
模块搜索路径，复用原场景构建器、6DOF IK 和 `GripperGraspPlanner` 的 headless
执行；不会重新实现规划或低层控制。场景查找使用原场景建立的 MuJoCo BODY，
`obj_N` 只留在 adapter/backend 内部。

默认映射显式使用 `block_sim_01`–`block_sim_04` 对应 `obj_0`–`obj_3`。
原桌面场景把四个物体都定义为通用几何体，没有可核实的红/绿/蓝语义，所以这些
ID 仅表示本仿真的稳定物块身份，不猜测颜色或 Quest TargetId。调用方仍需通过
`create_execution_requests` 提供真实 TargetId → logical block ID 的明确映射。
公共执行结果只返回 logical block ID 和基线结果 provenance，不返回 `obj_N` 或
MuJoCo object ID。

## 运行条件

源 baseline 建议使用独立 Python 3.10 环境与 MuJoCo 3.x，并记录以 MuJoCo 3.12 验证；
本仓库实际 smoke 使用 CPython 3.12.14、MuJoCo 3.12.0 和锁定在
`requirements-m9-smoke.txt` 的依赖。`mink` 是可选项，原 IK 在缺少它时使用 DLS 回退。
默认 `software-default-v1` verifier 仍运行原 CPython 3.9.13 环境，不安装或导入 MuJoCo。

在已有 CPython 3.12 安装上建立可丢弃的项目局部环境并重现 smoke。若 Python Launcher
没有注册该解释器，请用已安装 CPython 3.12 的完整 `python.exe` 路径替换 `py -3.12`：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\robot_arm\requirements-m9-smoke.txt
.\.venv\Scripts\python.exe -B -m integration.m9_mujoco_smoke --operation pick_and_place
```

根目录 `.venv/` 被 `.gitignore` 忽略；这个环境不修改项目指定的 CPython 3.9，也不访问硬件。
原 headless 脚本示例：

```powershell
python .\robot_arm\tests\test_pick_and_place.py --obj 0 --place
```

供适配器使用的单请求闭环 smoke 使用合成的 confirmed selection，不访问 Quest/EEG：

```powershell
python -B -m integration.m9_mujoco_smoke --operation pick_and_place
```

它会构建原场景、执行一次 headless Pick/Place，并只输出不含 `obj_N` 的结果 JSON；
依赖不可用时以 BLOCKED 退出。2026-09-14 在上述隔离环境中执行成功，planner 返回
`place_ok`。这证明合成 selection 到仿真 Pick/Place 的代码闭环，不是 Quest/EEG 或真实机械臂验证。
