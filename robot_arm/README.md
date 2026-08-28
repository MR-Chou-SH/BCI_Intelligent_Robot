# robot_arm —— fr3 + UMI 夹爪的抓取-放置（Pick & Place）仿真

> MuJoCo 仿真：Franka FR3 机械臂 + UMI 两指夹爪，在桌面上竖直下压抓取物体、
> 抬升后放到放置箱里。本目录从 DexGrasp 项目迁移而来，**只保留抓取-放置相关的
> 部分**（模型、IK 求解器、规划控制脚本），不依赖原项目。

## 1. 目录结构

```
robot_arm/
├── assets/
│   ├── fr3_umi_merged.xml     # [核心] 统一模型(fr3 7 关节 + 夹爪 2 手指关节, 单一 XML)
│   └── meshes/                # [核心] 模型引用的 mesh/贴图(含少量未被引用的多余文件, 无害)
├── utils/
│   ├── ik.py                  # [核心] fr3 数值 IK 求解器(6DOF 多起点 / 位置 / mink 可选)
│   ├── gripper_scene.py       # [核心] 场景构建: 桌 + 4 个物体 + 放置箱 + 灯光相机
│   ├── camera_utils.py        # [辅助] 相机朝向小工具(见下方"文件职责说明")
│   └── viewer_utils.py        # [辅助] GUI 回放的安全封装(见下方"文件职责说明")
├── control/
│   └── gripper_planner.py     # [核心] 抓取-抬升-放置规划控制器(状态机)
├── scripts/
│   └── build_merged_xml_umi.py# [辅助] 模型生成器(仅需重新生成模型时用)
├── tests/
│   └── test_pick_and_place.py # [核心] 测试入口(headless 统计 / GUI 回放)
└── vendor/
    └── mujoco_menagerie/      # [无关/可选] 原始模型源(fr3 + umi_gripper, 仅生成器需要)
```

### 文件职责说明（核心 / 辅助 / 无关）

**核心（跑抓取-放置必需）**：`assets/fr3_umi_merged.xml`、`utils/ik.py`、
`utils/gripper_scene.py`、`control/gripper_planner.py`、`tests/test_pick_and_place.py`。
缺任何一个都无法完成抓取-放置仿真。

**辅助（不参与抓取逻辑，但被场景/GUI 用到）**：

- `utils/camera_utils.py` —— 为什么存在：`gripper_scene.py` 要放一个 GUI 相机，
  需要 `look_at_quat`（算相机朝向四元数）。原项目里这个函数在 `utils/camera.py`，
  那是一个 ~300 行的"相机渲染 + 深度图 + 点云 + 法向估计"**感知管线**，抓取-放置
  用不到。迁移时只摘出 `look_at_quat`/`mat2quat` 两个函数，避免把整个感知管线
  带进来。**删掉它只影响 GUI 相机的朝向，不影响抓取**（把 `_add_camera` 里的
  `look_at_quat` 换成单位四元数即可）。
- `utils/viewer_utils.py` —— `mujoco.viewer.launch_passive` 的安全封装（修复脚本
  退出时的段错误）。只在 `--view`/`--replay` GUI 模式用到，headless 不需要。
- `scripts/build_merged_xml_umi.py` —— 模型**生成器**。`fr3_umi_merged.xml` 是它的
  产物；日常跑测试不需要它，只有改了模型处理逻辑才要重新生成。

**无关/可选（可以整个删掉）**：

- `vendor/mujoco_menagerie/` —— 原始模型源（fr3、umi_gripper），**只被生成器
  读取**。不重新生成模型就完全用不到，删除后运行测试不受影响（约 60MB）。
- `assets/meshes/` 里未被 XML 引用的少量多余文件（如 `link0.obj` 等独立 obj，
  是生成器平铺拷贝产生的），删除不影响加载。

## 2. 环境准备

**推荐用 conda 建一个独立环境**（模拟器对 numpy/mujoco 版本敏感，不要装进
base 或做科研用的其他环境）：

```bash
conda create -n loco_mujoco python=3.10 -y
conda activate loco_mujoco
pip install mujoco numpy        # 建议 mujoco >= 3.x(代码用 3.12 验证)
```

**可选**：`pip install mink`。IK 求解器优先用 mink(更稳)，装不了会自动回退到
自带的手写阻尼最小二乘，功能不受影响。

**不需要 GPU、不需要 dexgrasp 模型**——夹爪抓取是纯几何规划（最短轴对齐 +
竖直下压 + 平滑闭合）。

## 3. 运行

```bash
conda activate loco_mujoco
cd ~/brainInteract/BCI_Intelligent_Robot/robot_arm

# 1) headless 统计: 抓取 + 抬升(默认, 4 个物体)
python tests/test_pick_and_place.py

# 2) headless 统计: 抓取 + 抬升 + 放入放置箱
python tests/test_pick_and_place.py --place

# 3) 只看某个物体(默认 0)
python tests/test_pick_and_place.py --obj 2 --place

# 4) GUI 查看解算好的抓取姿态(空格播放物理)
python tests/test_pick_and_place.py --view 0

# 5) GUI 实时回放完整流程(抓→抬→放)
python tests/test_pick_and_place.py --replay 0 --place
```

结果写入 `outputs/pick_and_place_results.json/.csv`。判定枚举：
`place_ok` / `lift_ok` / `hold_only` / `dropped` / `grasp_fail` / `ik_fail`。

## 4. 模型说明与重新生成

`assets/fr3_umi_merged.xml` 是**生成产物**，一般直接用即可。改了源码里的
模型处理逻辑后需要重新生成：

```bash
python scripts/build_merged_xml_umi.py     # 输出到 assets/, 覆盖 XML + meshes
```

生成时做的关键处理（理解模型行为必需）：
- 删掉 UMI 原模型的 6 个"自由浮动关节"（遥操作用）——夹爪焊死在 fr3 法兰上，
  只留 2 个手指滑动关节 + 1 个 `fingers_actuator`；
- 夹爪根 body 的 quat 置**单位**：手指指向 = 法兰 +z = **fr3 末端圆柱轴**，
  开合轴 = 法兰 +x（这是夹爪"正对末端轴"的关键，不要改回原版 -90° quat）；
- 机身(base_link)缩小 ×0.55：原机身 18.5cm 太长，法兰到物体顶只有约 10cm，
  不缩会顶到物体；
- 手指关节去掉弹簧刚度（原 stiffness=100 会抵消夹持力）；
- option 加 `impratio=10` 等（防手指打滑）。

## 5. 抓取-放置规划逻辑（control/gripper_planner.py）

**纯几何规划，不用神经网络/点云**：
1. 取物体在 x-y 平面的**最短轴** ŝ（比较物体 geom 尺寸），夹爪开合轴与它平行；
2. 夹爪（张开）在物体正上方，竖直下压到"最高点下一小段距离"（`pinch_from_top`，
   且不低于桌面+垫尖余量，否则垫尖刮桌面会把闭合卡住）；
3. 闭合：规划器只发 **0/1 命令**，底层 `GripperServo` 按最大速率平滑 ramp
   （全行程约 0.6s，不猛烈开闭）；
4. 竖直抬升 `LIFT_H`；
5. `--place`：**L 形**轨迹——先抬升高度上水平横移到放置箱中心上方（不降），
   再小段下降，保持到位后平滑张开，物体落入箱内。

轨迹都是"分段局部 6DOF IK + 逐段热启动"（`_build_traj`）：单次 IK 会陷入
"折叠"位形，关节空间直线 ramp 会甩大弧（实测垫子能偏出 44cm），分段热启动
保持近似直线；某段解离前一段太远就用更高阻尼重解，再不行就跳过该段。

### 常用调参（都在 control/gripper_planner.py 顶部）

| 常量 | 默认 | 含义 |
|---|---|---|
| `LIFT_H` | 0.28 | 抬升高度(m) |
| `PINCH_MIN_Z` | — | 夹持点最低高度（桌面+垫尖 7.66cm+余量） |
| `close_steps` / `settle_steps` | 340 / 100 | 闭合/稳定等待步数（约 0.7s/0.2s） |
| `DROP_CLEAR` | 0.25 | 释放时垫子高出箱口(m) |
| `PLACE_H_N` / `PLACE_D_N` | 12 / 4 | 放置水平段/下降段分段数 |
| `CTRL_RATE` | 0.05/300 | 夹爪开闭最大速率（每步 ctrl 增量） |

放置箱参数在 `utils/gripper_scene.py` 的 `PLACE_BOX`（位置/内腔/壁高）。

## 6. 坐标系与标定（改模型后需重新实测）

- 夹爪根 body `umi_umi_gripper_base` 原点 = 法兰原点（`attachment_site`）；
- 手指垫中心在**法兰系** `(0, 0.003, 0.1244)`，即沿法兰 +z 向下 12.4cm
  （`PAD_OFFSET_IN_FLANGE`，规划器算法兰位置用它）；
- 手指垫最大开度 ≈ 8.6cm（物体宽度必须小于它）；
- 抓取姿态下法兰朝向 `R_flange`：z 轴竖直向下（手指朝下）、x 轴 = ŝ（开合）。

## 7. 与 BCI 项目的接口点（当前状态）

- **物体选择目前是按名字**（`--obj N` → `obj_0` 等，场景里写死的 4 个物体），
  没有视觉识别。BCI 侧把"选中哪个物体"（名字或索引）传进来即可；
- **抓取方位目前直接读仿真真值**（`data.xpos` / `model.geom_size`），
  不走相机点云。后续要做"感知驱动"时，把最短轴和中心换成点云估计
  （PCA / 包围盒）即可，规划器其余部分不用动。
