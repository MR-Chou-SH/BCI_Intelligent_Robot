# SmolVLA 单任务训练与 MuJoCo 推理交互

2026-11-09。目标是先验证 L1 apple 的视觉数据→真实 SmolVLA 微调→独立推理服务→MuJoCo 闭环流程。当前在线任务 evaluator 只接 L1/R5；其他原子/组合任务不冒用这两个成功条件。RL 数据增强暂停，没有为每初态或每任务训练 RL 专家。

当前50条随机苹果训练流程见[APPLE50_SMOLVLA_CN.md](APPLE50_SMOLVLA_CN.md)：`replay --manifest`从初态真实执行动作，采集实测本体状态/双视角RGB，再导出LeRobot v3.0；数据采集与训练期间不运行学习策略eval。L1在线评估可通过`--initial-seed`使用未见随机初态。

训练后可使用`bash scripts/kitchen_smolvla.sh evaluate --run <训练目录> --seed-start 10000 --episodes 10 --video`串行评测并汇总多个seed，GPU服务仍以独立`serve`命令运行；批量结果统一保存在一个文件夹。

## 实现和参考边界

DexJoCo 的 `dexjoco_openpi_client/eval_dexjoco_openpi.py` 提供独立推理进程、按观察时间戳收取动作块、丢过期动作与重叠插值；它不是 flow-matching 去噪中的 RTC。其 LeRobot 适配材料是 reference code，不能直接替换当前 FR3/UMI。

本项目复用 DexJoCo 的“仿真与推理分离、动作块绑定观察时刻”的组织方式，RTC 直接调用本机 LeRobot 的 `SmolVLAPolicy.predict_action_chunk` 和 `RTCProcessor`。不使用 `select_action` 冒充 RTC，不对世界坐标欧拉角做插值，也不搬用 DexJoCo 的44维手部动作。

| 层 | 路径/解释器 |
| --- | --- |
| MuJoCo、已有专家与物理判定 | 项目 `.venv`；模型/控制器保持原实现 |
| 模型训练、LeRobot 导出、推理 | `thirdparty/smolvla/.venv`，CUDA Torch |
| 上游源码 | `thirdparty/smolvla/upstream` 与 `thirdparty/openpi/upstream` |
| 项目适配 | `integration/kitchen/vla/{dataset,images,protocol,smolvla,server,environment,rollout}.py` |
| 训练入口 | `thirdparty/smolvla/train.py` |
| 统一命令 | `scripts/kitchen_smolvla.sh` |

GPU 检查须区分沙箱和主机：本机沙箱内 `nvidia-smi`/CUDA 不可用，但沙箱外确认 RTX4060 Laptop 8188MiB、驱动595.99.02、Torch2.11+cu128 可以完成 CUDA 矩阵运算。默认安装 CUDA12.8；默认训练 device=cuda，不静默回退 CPU。驱动显示的 CUDA13.2 是驱动能力，不要求 Torch runtime 必须同为13.2。

## 数据与动作契约

保持原始 H5 不变。从匹配模型的 H5 每个动作前关键状态离线渲染25Hz真实图像，记录图像 tick、每文件 SHA256、源 H5 SHA256 和相机配置，再在学习环境转换为真正 LeRobot 数据。当前使用 image feature，避免额外视频编码/解码依赖；不是黑图、占位帧或4倍速审核视频。Unity 以后可实现同样的图像 sidecar 契约。

第一轮历史模型使用固定 `v3_oblique` 单视角。用户确认 C 后，当前默认改为正对工作台的高位近景 C＋左腕，均为512×512；右腕槽位保留但关闭。训练、推理必须使用同一配置；checkpoint 保存 `kitchen_policy.json`，服务端拒绝不匹配相机。旧数据和旧模型保留，使用新相机需要重新导出并训练。

### 多视角选取核查（2026-11-09）

本机缓存的官方`smolvla_base`配置有`camera1/camera2/camera3`三路；本地`prepare_images`和`embed_prefix`根据配置遍历视角，没有把3写成模型固定上限。1/2/3路都属于可配置的输入；超过3路需另行调整配置并验证资源和训练效果，不能据此认为任意路数都已验证。[官方相机键说明](https://huggingface.co/docs/lerobot/main/smolvla)

配置文件为`integration/kitchen/vla/cameras_left.json`，三路固定键依次为`observation.images.camera1`全景、`camera2`左腕、`camera3`右腕。C位置为世界坐标`[0,-2.10,2.20]m`，看向`[0,-0.18,0.90]m`，垂直视场50°。关闭的右腕不分配渲染器、不生成图像、不输入黑图、不占用视觉token。以后启用右腕需重新生成数据并训练匹配配置的模型。

四组正对候选及左腕在0/8/20s的预览位于`experiments/kitchen_platform_v3/vla/frontal_camera_review_20261109/`。用户选择 C；全景与左腕按同一qpos/tick渲染，sidecar v2分别保存每路PNG路径/SHA及完整相机配置，再导出真正的LeRobot多图像feature。训练及HTTP推理按启用键遍历，缺图、多出关闭视角或姿态不一致均拒绝。仍兼容显式传入的旧单视角sidecar和checkpoint，但不能把此前单视角1.94GiB/约187ms结果套到双/三视角。

输入只有 RGB、机器人实测状态和英文指令，不发送物体真值/清洗加热标记。状态16维：左7关节(rad)、左实测指间距(m)、右7关节、右实测指间距；输出16维：左7绝对关节目标(rad)、左开度ratio、右7绝对关节目标、右开度ratio。开度0关闭、1张开；不把米制宽度冒充开度，也不使用 ALOHA 索引/delta 变换。

原始源 H5/报告/语言标注必须合格并匹配哈希，RGB/action 按相同 tick 对齐。粗/细语言视图沿用现有语义区间，fine 数据不能跨阶段训练一个action chunk；备份/优化及同初态变体不能跨 train/validation。当前最小试点仅用一条数据做管线/过拟合检查，不支持泛化结论；正式微调应收集多个初态并先按源组划分独立测试集。

## 运行

以下命令在项目根目录执行。源轨迹可以替换为其他当前模型的合格 L1/R5 数据；输出目录必须不存在，脚本不覆盖原数据或已有实验。

安装与来源固定：

```bash
.venv/bin/python -B thirdparty/bootstrap.py smolvla --install --torch-backend cu128 --locked
.venv/bin/python -B thirdparty/bootstrap.py openpi
```

本机已有 Python3.13 的学习环境时，可显式指定 `--python ~/Robot-Arm/lerobot/.venv/bin/python` 创建新的独立环境；仅借用解释器，不复用其 site-packages。也可用可用的 Python3.12+。来源/快照哈希与安装记录见 `thirdparty` 说明。

设置源轨迹：

```bash
SOURCE=experiments/kitchen_platform_v3/collections/whole_flow_atomic_20261106/attempts/kitchen_expert_v3_L1_apple/sample_01/attempt_01_seed0/trajectory.h5
PILOT=experiments/kitchen_platform_v3/vla/my_smolvla_l1_apple
```

离线渲染使用仿真环境，导出使用学习环境；多条源数据可在 `--trajectories` 后追加：

以下默认使用 C＋左腕。`render`和`rollout`可通过`--camera-rig path/to/rig.json`显式指定其他配置；导出和训练读取已封存的sidecar/数据集配置，不自行切换相机。

```bash
bash scripts/kitchen_smolvla.sh render --trajectories "$SOURCE" --output "$PILOT/images"
bash scripts/kitchen_smolvla.sh export --trajectories "$SOURCE" \
  --images "$PILOT/images/images.json" --output "$PILOT/dataset"
```

先运行短训练检查，再用全新输出做较长试验。RTX4060当前默认batch8、冻结VLM/视觉、只训expert和state projection，fp32可训练权重+bf16 autocast；历史50条数据的10000步模型采用batch1，旧模型不会被自动重训。首次需要 Hugging Face 权重与 tokenizer；有完整缓存时可设置 `HF_HUB_OFFLINE=1`。不会自动登录、上传权重或创建远程仓库。

单臂任务的另一臂存在近乎常量通道，上游float32矩统计可能得到零std和带误差的mean，放大数值误差。训练入口只对本次训练数据的state/action用float64重算统计；std下限为关节0.001rad、目标开度0.001ratio、实测宽度0.0001m，并随处理器保存。图像统计仍沿用数据集；不修改原始动作、不从评估轨迹计算归一化。脚本将子进程PYTHONPATH限定到本项目，避免本机ROS Python3.10路径混入独立Python3.13环境。

```bash
bash scripts/kitchen_smolvla.sh train --dataset "$PILOT/dataset" \
  --output "$PILOT/train_smoke" --steps 2 --save-every 2

bash scripts/kitchen_smolvla.sh train --dataset "$PILOT/dataset" \
  --output "$PILOT/train" --steps 5000 --save-every 1000
```

终端A启动独立推理服务（默认仅监听loopback）：

```bash
bash scripts/kitchen_smolvla.sh serve \
  --checkpoint "$PILOT/train/checkpoint_005000" --device cuda --rtc
```

终端B启动仿真客户端：

```bash
bash scripts/kitchen_smolvla.sh rollout --trajectory "$SOURCE" \
  --output "$PILOT/eval_realtime" --clock realtime --seconds 60
```

追加 `--video` 可在结果目录记录25fps、1倍速 `rollout.mp4`；这是策略真实执行的视频，渲染开销会影响墙钟吞吐。

`--trajectory` 仅提供一次合法的精确起始状态和任务定义，在线推理不会读取其后续专家动作。每25Hz接受模型目标，经过原关节/夹爪/TCP命令限幅，再执行20个500Hz物理子步；不求IK、不调用专家、不在中途恢复物体状态。私有环境独占仿真，未连接物理机器人或公开仿真服务器。

在线环境独立执行现有 `ContactGuard.inspect` 实测保护，不修改专家采集此前关闭的全局开关。最终成功仍由原物理稳定/支撑/包络等检查判定，不以动作块耗尽、模型自报或目标位置近似作为成功。

## 两种时间模式与 RTC

当前正式评测默认`realtime`。`lockstep`仅作为显式指定的调试模式：推理在后台运行，有剩余动作时继续物理步进；队列耗尽时暂停仿真时钟等待结果。这种模式不能模拟真实在线交互或宣称真实25Hz推理性能。

`realtime` 用于延迟/RTC测试：

```bash
bash scripts/kitchen_smolvla.sh rollout --trajectory "$SOURCE" \
  --output "$PILOT/eval_realtime" --clock realtime --seconds 60
```

该模式按25Hz墙钟执行，推理不阻塞已有动作；下溢时沿当前目标真实保持并计入下溢帧，持续无及时动作会停止。不凭空重复过期动作。它不是硬实时控制保证，渲染或Python执行超过40ms也会使实际速率下降。

动作块绑定观察时的控制帧索引，返回时根据已经执行的帧数裁掉旧前缀；下一次 RTC 输入使用**模型归一化空间**的未执行动作，最终执行用 postprocessor 输出的绝对目标。这两种数组分别保存，不能把米/弧度的执行目标直接当作归一化前缀。当前仅支持绝对关节动作；未来引入相对动作必须补上基于新观测的 prefix re-anchoring。

`--replan-remaining`当前默认40帧（历史为20）。50步预测块每执行10步（0.4秒）请求一次新预测，等待返回期间继续旧动作。批量evaluate也开放该参数。首轮同步warmup仍在仿真时钟启动前完成；运行期间队列耗尽则真实保持目标并继续步进。推理延迟以实际消耗的控制帧估计，丢过期块、下溢、限幅帧数、墙钟/模拟时长和成功证据写入`report.json`。动作是16维双臂绝对关节目标及夹爪开度，没有末端姿态或欧拉角线性混合。

服务端`--execution-horizon`默认10，控制RTC前缀约束范围，不决定客户端执行步数；可减小，但实际使用`min(剩余前缀长度,max(设定值,上一轮延迟步数+1))`。例如推理耗时约6步时设5仍会提升到至少7步。服务端新加载模型的metadata记录预测步数、RTC horizon和去噪步数，已运行旧服务需重启后才包含新字段。

追加`--audit`会汇总服务端/客户端延迟、观察→新目标应用时间、墙钟执行间隔、块替换分歧和25Hz关节速度差分加速度，不保存密集检查日志。RTX4060同一模型的30s实测：客户端稳态均值187ms/P95 201ms，执行间隔均值40.0ms，下溢0；但748/750帧限幅、模型动作仍明显不够平滑。[完整测量与口径](../development_log/2026-11-09-smolvla-latency-continuity.md)。

## 验证范围

代码测试覆盖 RGB 编码、输入约束、延迟动作块裁剪、模型空间前缀和真实本地 HTTP 通信。独立仿真对照已把原专家动作通过新执行器成功完成 L1 apple：26.72s、无实测保护触发、无额外限速帧；这是执行接口控制实验，不是模型表现。

真实训练、checkpoint 重载、RTC 推理与闭环结果以本轮开发记录及 `experiments/kitchen_platform_v3/vla/smolvla_l1_apple_20261109/` 中的报告为准。短训练 loss 有限或接口跑通，不能当作已学会抓苹果、未见初态泛化或全部任务支持。

已知边界：目前没有正式数据 split、训练 optimizer 续跑、Unity 训练图像或π0.5厨房微调；不在这里恢复RL数据增强。正式训练的样本量/初态范围应在当前管线验证后单独确定。
