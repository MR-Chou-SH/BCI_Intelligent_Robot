# 无 VR：物体状态、记录和重放

当前默认 `kitchen_state_actions_v3`：[完整格式与动作语义](DATASET_FORMAT_CN.md)。每个合格样本只有一次初态、25Hz 关节位置/夹爪开度动作、关键物理状态和简短结果；不含图像。批量执行见[自动采集说明](DATASET_COLLECTION_CN.md)。

## 查询与录制

```python
client.call("get_object_states", {"detail": "key"})
client.call("get_object_states", {"ids": ["apple", "microwave_door"]})
recording = client.call("start_state_recording", mutating=True)
# 正常执行动作和 step；录制期间不可 reset。
sealed = client.call("stop_state_recording", mutating=True)
```

状态接口只读，支持设备关节、位姿、支撑、清洗/加热等信息。`detail=key` 返回记录所需的精简状态，不计算完整几何包围盒和接触解释。记录器复用该实现；`get_replay_snapshot` 可显式查询完整初态。操作沿用 manual 服务、data_root、token 及单写者规则。

## 重放

```bash
TRAJECTORY=/绝对路径/trajectory.h5
.venv/bin/python -B -m integration.kitchen.data.replay inspect "$TRAJECTORY"
.venv/bin/python -B -m integration.kitchen.data.replay verify "$TRAJECTORY"
```

`inspect` 检查封存、数值、时序和数组维度。`verify` 从初态和动作独立计算物理，并在关键状态处比较误差、重新检查任务成功；不向中间状态强行纠偏。无任务标注的手动记录只能验证物理重放。V2 历史文件仍可读取，但动作语义和 V3 不同。

## 可选观看

```bash
.venv/bin/python -B -m integration.kitchen.data.replay viewer "$TRAJECTORY" --max-seconds 180 --speed 1
MUJOCO_GL=egl .venv/bin/python -B -m integration.kitchen.data.replay video "$TRAJECTORY" \
  --output /tmp/kitchen_review.mp4 --max-seconds 180 --speed 1
```

这两种方式播放已记录的 25Hz 状态，方便回看，不是新的动力学验收。viewer 需要图形桌面；MP4 需要 ffmpeg 和可用 EGL，输出独立于数据集。采集、检验无需这些渲染条件。Unity 可后续用同一关键状态渲染。

坐标 m、角度 rad、四元数 wxyz。清洗和加热为模拟设备标记，并非真实流体/温度。模型身份不同会拒绝动力学重放，不能把旧模型的成功直接算作当前模型成功。

```bash
.venv/bin/python -B -m unittest discover -s integration/kitchen/tests -q
```

原有成功研究数据保留原位。2026-10-27 清理的是失败仿真中间文件、临时渲染和开发 checkpoint；清单为 `experiments/kitchen_platform_v3/collections/cleanup_20261027.json`，旧封存报告保持不变，其中被清理的失败文件不再可用。
