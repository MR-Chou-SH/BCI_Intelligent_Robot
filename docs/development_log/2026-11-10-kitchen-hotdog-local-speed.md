# 2026-11-10：热狗加热提速失败定位与局部倍率

用户指出整条轨迹回退到2倍会拖慢原本可高速执行的区间。本次复用上一轮C04 hotdog seed0的同一已规划轨迹和完整初态，只调整动作时间参数，不重新规划、不修改设备成功条件。

## 失败在按钮启动，并未进入加热

新增按阶段的少量诊断：门角、主食就位、按钮最大位移/力/有效保持时间以及真实device events。复测3/4倍均在`MEAL_NOT_HEATED`失败，按钮阶段炉门已关、主食仍符合炉内就位条件；两次都没有`heat_started`事件。因此不是5s加热等待被缩短或打开门打断加热。

当前仿真按钮条件是位移≥3mm、手指法向力≥1N，连续有效时间≥0.05s。设备以25Hz采样，每次累计0.04s，实际至少需连续两次有效采样。

| 轨迹时序 | 按钮最大位移 | 最大手指法向力 | 最长有效按压 | 结果 |
| --- | ---: | ---: | ---: | --- |
| 整体3倍 | 4.029mm | 1.0096N | 0.04s | 保持不足，未启动 |
| 整体4倍 | 3.927mm | 0.9850N | 0s | 力未达到判定阈值，未启动 |
| 其余4倍、按钮相关接触段2倍 | 4.078mm | 1.0213N | 0.08s | 启动并完成5s加热 |

加速改变了真实接触响应，不是仅按键目标位置不足。最大力只是诊断峰值，保持时间仍由原设备逻辑计算；没有放宽1N/0.05s阈值来声称成功。

## 局部调整结果

`speed_stress.schedule`新增可重复的`--segment-factor TARGET=CAP`：指定contact target对应区间的速度上限，其他区间保持全局倍率；对于本次只限制`microwave_start_geom`的device/release，即按钮按压和撤离段。固定物理任务计时与原有保护窗口继续保留，原轨迹空间位置与全部端点不变。

- 原备份：206.60s。
- 上轮整条2倍：107.56s，任务成功。
- 本轮其余4倍、按钮接触2倍：**59.16s**，任务成功，整任务实际3.49倍，比整条2倍再缩短约45%。
- 相对无法成功的均匀4倍计划58.00s，只增加1.16s；没有额外插入稳定或加热等待，控制目标限幅帧数0。
- 同初态再录制与独立重放通过，qpos/qvel最大误差均0，原始备份未修改。新状态记录仍无训练图像。

这说明应按接触/动作区间调整倍率，不能由一个局部失败推导整条都只能2倍。不同任务动作链和固定设备计时本就不同，不要求总时长相同；同类运动区间则可以采用共同的高速配置。

## 位置与复现

诊断目录：`experiments/kitchen_platform_v3/collections/hotdog_local_speed_20261110/`，包括原3/4倍复现与混合倍率试跑。

最终完整对照/录制目录：`experiments/kitchen_platform_v3/collections/hotdog_mixed4_button2_20261110/`，`summary.json`和`recorded_4x/report.json`保留实际时间、局部倍率、device events、按钮证据及重放结果。

`hotdog_mixed_execution_1x_view.mp4`按正常1倍速度播放本次59.16s的执行结果，没有用播放加速代替轨迹提速。

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -B \
  -m integration.kitchen.data.speed_stress \
  --backup-root experiments/kitchen_platform_v3/collections/all_tasks_1each_optimized_20261106 \
  --output experiments/kitchen_platform_v3/collections/hotdog_mixed4_button2_new \
  --case-ids kitchen_expert_v3:C04:hotdog \
  --factors 1 4 --segment-factor microwave_start_geom=2 --jobs 1
```

局部倍率是上限：1倍对照仍按1倍，不会被局部2倍设置加速。新增测试验证只有指定接触区间改变时间、其他边界之前时钟不变、1倍对照保持及原设备窗口；完整154项回归通过。当前是同一固定seed的验证，按压峰值余量较小，更多随机初态仍需逐轨迹验收，未更改正式采集默认配置。
