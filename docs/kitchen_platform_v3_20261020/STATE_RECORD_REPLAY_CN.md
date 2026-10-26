# 无VR：关键状态、动作与MuJoCo重放

2026-10-24，分支`feature/kitchen-linux-task-collection`。当前默认格式为压缩HDF5：`kitchen_state_actions_v2`。不采图像，MP4按需另行生成。

## 默认保存什么

每局只有一个`trajectory.h5`和简短任务报告。文件内容：

| 路径 | 内容 |
| --- | --- |
| `initial/integration_state` | 开始采集时的完整MuJoCo初态，只存一次，含重放需要的求解器状态 |
| `initial/ctrl` | 初始执行器输入 |
| `states/tick` | 状态对应的仿真步号 |
| `states/qpos`、`states/qvel` | 25Hz关键物理状态：机器人、物体和设备关节 |
| `states/object_pose` | 同一时刻经物体状态接口查询的位置及wxyz四元数 |
| `states/task_flags` | 物体的清洗／加热标记 |
| `actions/ctrl` | 实际16维执行器输入；保留500Hz输入以准确重算，使用压缩数组保存 |
| 文件属性 | 任务身份、物体ID顺序、模型／控制指纹、频率、结束状态 |

不再逐帧保存包围盒、接触点、支撑解释、完整查询回复、源码副本或JSON哈希链。不生成默认物理诊断和完整RPC日志。状态／动作采用gzip＋shuffle压缩及块校验；文件SHA在封存时计算一次。写盘按批次缓冲，结束时补齐不足一个控制帧的尾部。

原JSONL记录已完成必要转换，冗余旧记录及旧JSONL重放实现已清理；新入口统一读取HDF5。2026-10-24已按用户要求删除约32GB本轮冗余调试输出，保留5份成功用例的精简数据，合计约26MB。目录为`experiments/kitchen_platform_v3/expert_data/`，清单为其`index.json`；清理摘要为上级`cleanup_summary.json`。其中历史模型不匹配的轨迹会明确拒绝当前模型下的物理重放，不冒充重新验收。旧开发日志中的已清理run路径仅表示历史记录。

## 查询与录制

已有完整查询仍保留；关键查询不会计算几何包围盒和接触解释：

```python
client.call("get_object_states", {"detail": "key"})
client.call("get_object_states", {"ids": ["apple", "microwave_door"]})
recording = client.call("start_state_recording", mutating=True)
# 正常执行任务及step。录制中不可reset。
sealed = client.call("stop_state_recording", mutating=True)
```

查询只读；完整查询支持设备关节、支撑、清洗／加热等信息，但不把这些解释性字段反复写入数据。关键查询覆盖可动物体位姿和任务标记。`get_replay_snapshot`仍可显式查询完整初态。接口要求manual服务及data_root；所有可变操作沿用token和单写者规则。

普通启动见[Linux环境指南](LINUX_TASK_COLLECTION_CN.md)。批量采集见[Expert V2指南](EXPERT_V2_COLLECTION_CN.md)。只有确实需要排查接触问题时才加`--diagnostics`，该选项会产生额外大日志，常规采集不要开启。

## 重放与观看

本机已有`.venv`、MuJoCo、h5py与ffmpeg，无需安装依赖：

```bash
TRAJECTORY=experiments/kitchen_platform_v3/expert_data/kitchen_expert_v2_L1_apple_seed0/trajectory.h5
.venv/bin/python -B -m integration.kitchen.replay inspect "$TRAJECTORY"
.venv/bin/python -B -m integration.kitchen.replay verify "$TRAJECTORY"
.venv/bin/python -B -m integration.kitchen.replay viewer "$TRAJECTORY" --max-seconds 180 --speed 4
MUJOCO_GL=egl .venv/bin/python -B -m integration.kitchen.replay video "$TRAJECTORY" \
  --output /tmp/kitchen_review.mp4 --max-seconds 180 --speed 4
```

`inspect`检查封存、数组维度、数值与时间连续性。`verify`只恢复一次初态，再用保存的输入重新计算物理并比较qpos/qvel，默认误差门限1e-8；不会向中间状态强行校正。HDF5数组一次读入，不反复解析大JSONL。

viewer／MP4使用保存的25Hz状态，方便查看；这种状态回放不表示新的物理验收。MP4独立于数据集，已有输出不覆盖。viewer需要本机图形桌面；EGL视频支持无窗口环境。四元数为wxyz，坐标单位m，关节转角rad。清洗与加热标记是模拟设备事件，不是真实流体或温度测量。

验证：`.venv/bin/python -B -m unittest discover -s integration/kitchen/tests -q`。包括初态保真、尾部不足20步、控制重算、损坏／未封存文件拒绝、默认无诊断文件和记录大小回归。
