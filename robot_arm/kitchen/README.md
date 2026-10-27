# Kitchen 仿真与控制

本目录保留已有 FR3 + UMI + MuJoCo 基线的模型与资源路径，避免移动资产破坏模型身份或参考轨迹。

| 文件 | 职责 |
| --- | --- |
| `model.py` 及布局配置 | 编译物理场景、几何与碰撞模型 |
| `research_initialization.py` | 小范围初始位姿随机化、组内刚性关系、初始不重叠校验 |
| `platform.py` | 公开请求、执行与录制生命周期、仿真步进 |
| `control.py`、`limits.py`、`mechanics.py` | 已有 IK、25Hz 关节目标、500Hz 插值及动力学控制 |
| `collision.py`、`contact*.py`、`safety.py` | 路径、接触和控制域检查 |
| `devices.py` | 水龙头、门、清洗/加热计时与标记；标记是仿真语义 |
| `object_state.py`、`registry.py` | 物体状态查询、关键点、对象与模型/控制身份 |
| `action_codec.py` | 关节位置与夹爪开度的动作编码 |
| `state_recording.py` | 仿真侧小型 HDF5 写入器：一次初态、25Hz 动作与关键状态 |

任务组合、专家规划、成功判定、采集调度和离线重放归 `integration/kitchen/`。底层不会导入上层任务模块。状态查询 API 为 `get_object_states`；记录器调用同一实现，避免另造物体状态定义。

[数据格式与动作语义](../../docs/kitchen_platform_v3_20261020/DATASET_FORMAT_CN.md)
