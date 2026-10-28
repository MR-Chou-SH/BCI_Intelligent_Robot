# Kitchen 轨迹格式 V3

后续训练采用派生LeRobot格式，H5仍为原始真值。16维FR3/UMI状态/动作、粗细指令、25Hz对齐和未来Unity图像的约定见[LeRobot与π0.5接口](LEROBOT_PI05_CN.md)。

默认 `kitchen_state_actions_v3`，一个成功样本为 `trajectory.h5`、简短 `report.json`，以及英文粗/细任务标注 `annotations.json`。不包含图像、逐步检查解释、RPC、完整物理日志、源码副本或失败截图。成功检查仍然执行，但不把检查过程写入训练数据。

标注schema为 `kitchen_language_annotations_v1`。细标注的 `[start_step,end_step)` 使用动作数组的0起始行号，连续覆盖全部N行动作；不是500Hz tick或状态数组索引。H合并为完整加热装盘阶段，提示词与原子任务共享模板；见[完整语言设计](TASK_CATALOG_V3_CN.md)。HDF5仅额外保存少量语义阶段起点，供精确映射与标注校验，不按每一步重复保存提示词。报告及manifest提供标注路径和SHA256，续跑同时校验哈希与轨迹对应关系。旧报告无此字段时仍可读取。

后续Unity图像应按 `actions/tick` 关联动作行，不能直接把渲染视频帧号或关键状态行号作为训练step。

## 动作

`actions/joint_pos` 为 N×14 的**目标关节位置**，单位 rad，左臂 7 维后右臂 7 维；不是带重力补偿的底层执行器输入。`actions/gripper_opening` 为 N×2 的**目标开度比例**，先左后右，0 关、1 开。实际夹爪间距由状态中的 `gripper_width_m` 给出，单位 m；目标开度不是测量间距。

动作在 25Hz 控制帧开始时记录，`actions/tick` 使用 500Hz 物理步号。动作经原控制器的 20 子步插值和重力补偿执行。没有 `actions/ctrl` 的 500Hz 大数组。少见的帧中 hold/cancel 会补一个动作，并用 `actions/interpolation_reset` 表示插值起点重置；这是重放必需的控制语义，不是检查日志。

## 文件内容

| 路径/属性 | 内容与用途 |
| --- | --- |
| `initial/integration_state` | 任务准备完成、开始采集时的完整 MuJoCo 状态，含求解器 warmstart，只存一次 |
| `initial/qfrc_bias` | 原控制器首步实际使用的缓存重力/偏置值，只存一次 |
| `controller_json` | 初始目标、插值起点、参考值、帧内步号 |
| `devices_json` | 初始清洗/加热计时、标记与按钮状态，供语义重放 |
| `actions/tick`、`joint_pos`、`gripper_opening` | 25Hz 动作及其精确开始时间 |
| `actions/interpolation_reset` | 帧中 hold/cancel 的插值重置位 |
| `replay/gripper_target` | 同一动作时刻的两个原生夹爪目标值，仅用于无损反算；每帧仅多两个数 |
| `states/tick`、`qpos`、`qvel` | 25Hz 关键物理状态，覆盖机器人、物体和设备，结尾补齐非整帧状态 |
| `states/object_pose` | 物体状态接口返回的位置和 wxyz 四元数；顺序见 `object_ids` |
| `states/task_flags` | 对应物体的 cleaned / heated 标记 |
| `states/gripper_width_m` | 左右夹爪实际内侧间距，m |
| `task_json`、`segments_json` | 任务定义与技能起止 tick；用于分别验证组合任务的各个子目标 |
| `metadata_json` | 模型、控制、软件身份、对象顺序、频率和单位，只存一次 |

为何另存两个夹爪值：现有控制器使用腱长，开度比例与腱长之间的浮点往返会损失末位；在长接触任务中会放大成重放偏差。保留这两个原值可避免修改已有控制器，也无需保存全部 500Hz 底层控制。训练使用 `joint_pos` 和 `gripper_opening` 即可。

全部时序数组采用 gzip、shuffle 和块校验，按一秒缓冲写盘。文件封存后计算 SHA256。机械臂关节索引见 `metadata_json.arm_qpos_indices`；浮动物体的姿态和速度保留在完整 qpos/qvel 中，便于 Unity 后续渲染。

## 重放与成功检验

```bash
.venv/bin/python -B -m integration.kitchen.data.replay verify /路径/trajectory.h5
```

仅恢复一次初态，按动作重新计算物理，不用中间状态纠偏。`status=CONTROL_RESIMULATION_PASS` 表示记录状态与重算状态最大误差 ≤1e-8；`task_success=true` 表示基于重算的支撑、稳定时间、门状态、清洗/加热标记和最终摆放重新通过任务判定。技能时间线和原子任务初始条件也会检查。仅通过前者不能成为专家数据；手动记录若未附任务定义，`task_success=null`。

读取仍支持 V2 历史文件；其 `actions/ctrl` 是 500Hz 执行器值，不能直接当成新格式的关节动作。V2 不追认新的独立任务成功结论。匹配的模型、控制语义及 MuJoCo 环境是动力学重放的前提，模型不匹配会拒绝执行。

viewer/MP4 是可选的已记录状态播放，和动作重仿真检验不同；采集不启动它们。示例见[状态查询与重放](STATE_RECORD_REPLAY_CN.md)。
