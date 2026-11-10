# SmolVLA 独立训练环境与 MuJoCo RTC 交互

基线：`feature/data-collection-improve`，HEAD `395d8295e4669127f82722ae329e58ea261ae543`。保留既有两份未提交的多样性评估文档。没有切换分支、提交、修改 `.cache/upstream` 或外部 LeRobot 源码，没有操作真实硬件。

## 变更

- 新增 `thirdparty/bootstrap.py`，从本机 LeRobot/DexJoCo OpenPI 复制带许可证和逐文件哈希的独立源码快照，复制 `setup.py` 以保留上游 dynamic readme 构建逻辑。快照、环境与大权重不提交Git。
- `thirdparty/smolvla/` 保存训练实现、独立CUDA环境、精简requirements及本次验证过的96包lock。`thirdparty/openpi/`保存后续π0.5源码，未安装其训练依赖或宣称完成厨房适配。
- `integration/kitchen/vla/`提供同配置离线/在线相机、图像sidecar、真正LeRobot导出、轻量HTTP推理契约、模型适配器、时间戳动作队列和独占MuJoCo执行环境。模拟进程不导入Torch，学习环境没有MuJoCo。
- 统一脚本`kitchen_smolvla.sh`分别选择两套解释器；`setup_kitchen_environment.sh`默认只检查基础仿真，显式`--with-rl`才安装RL可选依赖。RL训练本轮暂停，该可选组合未做功能验收。
- 新增协议/归一化回归；更新README、context、status和[单任务操作文档](../kitchen_platform_v3_20261020/SMOLVLA_PILOT_CN.md)。

## GPU 与依赖

沙箱内`nvidia-smi`失败曾导致错误选择CPU安装方向。用户提供主机GPU状态后，在沙箱外验证RTX4060 Laptop8188MiB、驱动595.99.02和已有Torch2.11+cu128的小矩阵运算。新环境实际安装CUDA12.8版本；没有装入CPU Torch。训练入口默认cuda并显式检查，避免自动回退。

独立环境是Python3.13.9、Torch2.11.0+cu128、LeRobot0.5.2、Transformers5.5.4。首次Torch/CUDA下载约15.5分钟，这不是微调耗时。基础仿真环境保持原Python/MuJoCo/NumPy，验证Torch/Warp仍未安装。

主机继承的PYTHONPATH包含ROS Python3.10路径，原始包枚举一度包含外部ROS元数据。脚本现将子进程PYTHONPATH限定为项目根目录；锁文件只枚举独立环境的site-packages，未包含ROS包。

## 数据、归一化与训练

源数据：`collections/whole_flow_atomic_20261106/attempts/kitchen_expert_v3_L1_apple/sample_01/attempt_01_seed0/trajectory.h5`。保持原H5与报告/标注不变。723个动作前关键状态渲染为25Hz、512×512 `v3_oblique`真实图像，sidecar绑定逐帧SHA256和tick；LeRobot真实读取确认：state(16)、action chunk(50,16)、RGB(3,512,512)，指令`Place the apple directly on the serving tray.`。

图像导出先尝试过右侧审核用front视角，目视发现不能完整覆盖水果，改为斜视。早期`images/`为未采用的视角试验，实际训练使用`images_oblique/`；没有用占位图或审核MP4充当训练源。

发现上游float32矩统计在近常量关节上存在数值问题：原始state部分std=0，均值误差使归一化状态达到13649，动作达到184.8，初始loss2428.99。`train_smoke/`是修复前两步诊断，`train_pilot/`在约500步停止并标记`INTERRUPTED_FOR_NORMALIZATION_FIX`，不得拿来作有效策略效果。

修正仅在项目训练适配层：用训练数据float64重算state/action统计，std下限分别为joint0.001rad、opening0.001ratio、width0.0001m，保存到同一pre/post处理器。原始数据和上游代码不改，归一化后的全数据最大绝对值约state2.602、action2.657。回归覆盖常量/近常量及反变换，不引入测试数据统计。

修正后实际训练：冻结VLM/视觉、训练action expert/state projection，99,880,992个可训练参数；batch1、AdamW1e-4、fp32可训练权重和bf16 autocast、1000步。耗时284.07s，CUDA峰值1.939GiB，初始loss0.3093、末步0.06368。末步loss不是验证集指标；本轮没有泛化评估、正式split或optimizer续训。

## 执行与 RTC 结果

先用原始专家动作经过新的私有策略执行器验证：L1 apple26.72s完成，额外限速0帧，实测guard无触发。这是执行器控制实验，不是模型成绩。

真实checkpoint`train_stable/checkpoint_001000`重载成功，模型SHA256：`035500d965e5cd9c2fc6b3f33982bfe28842a59063c3b046b019488aefd6130b`。RTC直接调用上游去噪引导，传递归一化模型空间前缀；执行端仅用postprocessor的绝对目标，过期动作按实际控制帧裁剪。模型身份/相机/动作时钟绑定到交互报告。

| 场景 | 结果 |
| --- | --- |
| 60s lockstep | 1500控制帧，50次推理，墙钟12.87s，队列下溢0、过期块0、guard触发0；1492帧调用限速；**装盘失败** |
| 8s realtime | 200控制帧，7次推理，墙钟8.17s，队列下溢0、过期块0、guard触发0；200帧调用限速；测试窗内未完成装盘 |

lockstep可以在队列用尽时暂停仿真等待，不能据此认定推理硬实时。realtime短窗验证动作供给正常，也不是所有状态/长时间硬实时证明。大量限速和未完成任务说明当前模型动作尚未充分匹配专家执行；单条示范1000步不足以得出正式效果结论，后续需在保持接口验收基础上增加训练/数据与独立评估，并诊断动作偏差。不能通过放宽成功标准或关闭保护把本轮失败改为成功。

当前私有执行器只接L1/R5，使用原有控制器限幅与物理子步，不重新求IK；不调用脚本专家，不从源H5读取未来动作来修正策略。保留实测ContactGuard，不恢复物体qpos。未声称提供连续几何碰撞证明。π0.5厨房模型、Unity训练图像、组合策略和RL增强未实现。

## 产物与验证

所有本轮数据在`experiments/kitchen_platform_v3/vla/smolvla_l1_apple_20261109/`：

- `images_oblique/images.json`、`dataset/`：合格源图像与真实LeRobot数据；`dataset/kitchen_export.json`保留源/图像/语言契约。
- `train_stable/training_report.json`与`checkpoint_001000/`：修正后实际GPU训练和模型/处理器。
- `executor_control.json`：独立执行器成功对照。
- `eval_rtc_lockstep/report.json`、`eval_rtc_realtime/report.json`：真实模型执行，task_success=false。
- 两个评估目录的`rollout.mp4`：分别60s/1500帧和8s/200帧，ffprobe核验25fps、1倍速。
- `pilot_summary.json`汇总上述实验；诊断失败与旧视角保留在原位。

最终126项厨房测试全部通过（含真实本机HTTP通信、状态/动作映射、归一化和历史采集回归）；Shell语法、源文档链接和Git空白检查通过。HTTP/CUDA验证在沙箱外执行以满足本机权限限制。测试与评估结束后，已停止本次临时推理服务，没有遗留训练或推理任务。
