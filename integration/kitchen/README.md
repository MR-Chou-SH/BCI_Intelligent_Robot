# Kitchen 模块边界

当前任务：20 个原子实例、44 个组合实例。批量入口：`bash scripts/collect_kitchen_dataset.sh --output experiments/kitchen_platform_v3/collections/<新批次>`。默认各两条，仅记录轨迹，不启动渲染。

| 目录 | 职责 |
| --- | --- |
| `tasks/` | `catalog.py` 定义任务矩阵；`execution.py` 根据实测状态选择技能、串联组合任务和准备初态 |
| `planning/` | `expert.py` 的抓取、放置、设备操作；`motion.py` 的运动执行与平滑；`meal_geometry.py`、`curved_motion.py` 的局部几何路径；只读参考轨迹适配 |
| `validation/` | `quality.py` 计算稳定支撑等物理条件；`success.py` 统一在线执行与重放的任务成功标准 |
| `runtime/` | 本地 API 客户端，沿用仿真服务的身份、写权限及动作校验 |
| `data/` | 批量采集、续跑、索引和独立动作重放；不导入视频工具 |
| `tools/` | 可选 viewer/视频、状态查询示例及开发 checkpoint；不属于自动采集流程 |
| `tests/` | 接口、几何、任务组合、记录与重放回归 |

物理场景、随机初态、对象状态查询及低层控制位于 `robot_arm/kitchen/`，参见其 README。模型、IK 和控制器没有因为此次目录整理而重建。

数据流：`tasks → planning → runtime → robot_arm/kitchen`；仿真以 25Hz 提供关键状态给 `validation` 和记录器；`data/replay.py` 从初态和动作独立重算物理，再调用同一套成功判定。

根目录的 `launch.py`、`platform_server.py`、`planner_client.py` 是现有公开服务接口。`task_catalog.py`、`task_annotation.py`、`collection.py` 是原 V1 API 的任务定义/分段兼容能力，不与用户两张新任务表的 V2 目录合并。仅转发模块的旧文件已经删除；调用方应直接导入上述目录。

```bash
.venv/bin/python -B -m integration.kitchen.data.collect_experts plan --output experiments/kitchen_platform_v3/collections/<计划名>
.venv/bin/python -B -m integration.kitchen.data.replay verify /绝对路径/trajectory.h5
.venv/bin/python -B -m integration.kitchen.tools.state_demo --help
.venv/bin/python -B -m integration.kitchen.planning.reference_trajectories inspect
```

[采集步骤](../../docs/kitchen_platform_v3_20261020/DATASET_COLLECTION_CN.md) · [数据格式](../../docs/kitchen_platform_v3_20261020/DATASET_FORMAT_CN.md)
