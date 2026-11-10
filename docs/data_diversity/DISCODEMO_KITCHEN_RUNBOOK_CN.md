# Kitchen状态RL与DiscoDemo运行说明

2026-11-09。当前是L1 apple工程试验：官方FlashSAC＋逆向课程＋成功门控METRA已接入，尚无从正常随机初态完成整任务的合格RL专家。其余100实例的CPU任务链/成功条件可复用，GPU训练环境暂只支持苹果直接装盘。

## 环境与文件

基础仿真和RL使用项目`.venv`；SmolVLA仍用`thirdparty/smolvla/.venv`，未来训练默认batch8。RL可选安装：

```bash
bash scripts/setup_kitchen_environment.sh            # 仅检查基础环境
bash scripts/setup_kitchen_environment.sh --with-rl  # 安装CUDA Torch/Warp并复制算法快照
```

已经在本机安装可选依赖，不必重复安装。MuJoCo3.12/NumPy/h5py保留原版本。`.cache/upstream/DiscoDemo`只读；算法快照在`thirdparty/discodemo/upstream`，逐文件receipt校验。`robot_arm/kitchen/warp_*`负责物理与控制；`integration/kitchen/rl`负责状态、任务、reset锚点、环境和数据出口；`thirdparty/discodemo`负责算法配置/训练。没有安装Isaac，也没有向SmolVLA输入物体真值。

## 准备20条示范

已有缓存`experiments/kitchen_platform_v3/rl/implementation_20261109/seed_bank_20/bank.json`，来自原50条苹果数据，不修改原文件。重建时使用新目录：

```bash
bash scripts/kitchen_rl.sh prepare \
  --manifest experiments/kitchen_platform_v3/vla/apple50_c_left_20261109/collection/manifest.json \
  --output experiments/kitchen_platform_v3/rl/apple_seed_bank_v2 --demos 20 --stride 16
```

逐条独立重放、采集271维归一化状态/8维左臂动作经验及稀疏完整reset锚点；从20个初始场景学习一个策略，不是一seed一个专家。实际终态验收使用支撑/释放/稳定/完整包络，不靠位置距离代替。

## 有界训练试验

先用`--alpha 0`验证纯逆向课程基线，再用`.3`启用DiscoDemo。下面各10万world控制transition是试验预算，**不保证足够学会整任务**；不同试验输出目录必须不同。16world意味着每次交互产生16transition，更新1次；不是论文2048world/每交互64更新的资源配置。

```bash
bash scripts/kitchen_rl.sh train \
  --bank experiments/kitchen_platform_v3/rl/implementation_20261109/seed_bank_20/bank.json \
  --output experiments/kitchen_platform_v3/rl/apple_prfcl_trial \
  --worlds 16 --steps 100000 --batch-size 128 --buffer-size 50000 \
  --warmup 512 --updates 1 --alpha 0 --allow-noslip-variant --box-foot-proxy

bash scripts/kitchen_rl.sh train \
  --bank experiments/kitchen_platform_v3/rl/implementation_20261109/seed_bank_20/bank.json \
  --output experiments/kitchen_platform_v3/rl/apple_discodemo_trial \
  --worlds 16 --steps 100000 --batch-size 128 --buffer-size 50000 \
  --warmup 512 --updates 1 --alpha .3 --allow-noslip-variant --box-foot-proxy
```

训练从示范末段倒推，每个frontier最近10次达到90%成功后后退；半数示范从开头学会后切回正常随机初态。z为每回合固定的3维截断高斯；成功gate在回合结束后更新到唯一UID，未完成/失败/示范样本gate=0，始终混合50%示范。外在奖励为末态成功+1，其余0，另有未授权机器人接触代价；不是RECAP的逐步-1或PPO/BC预热路线。

`metrics.jsonl`记录frontier、phase、episodes、successes、loss和gate；**reverse阶段近终点成功不算正常初态成功率**。`forward_episodes/forward_successes`只计从正常初态启动的回合，不把切换时残留的逆向回合算进去；正式泛化仍需独立未见seed评测。checkpoint在`stepNNNNNNNNN/`，`latest.json`指向最后一次完整保存。显式`--resume <checkpoint目录>`恢复到新输出目录，训练参数须相同，`--steps`可延长但必须大于checkpoint已完成步数；保留原学习率计划，达到旧计划末尾后维持原末端学习率，避免学习率突然回升。保留replay/未提交n-step/课程/latent/UID/RNG/原生物理状态，重算GPU接触缓存，因此不是逐bit重放。

从已有基线10万步继续到总计100万步（新增90万步），每10万步保存：

```bash
bash scripts/kitchen_rl.sh train \
  --bank experiments/kitchen_platform_v3/rl/implementation_20261109/seed_bank_20/bank.json \
  --resume experiments/kitchen_platform_v3/rl/apple_prfcl_trial/step000100000 \
  --output experiments/kitchen_platform_v3/rl/apple_prfcl_1m \
  --worlds 16 --steps 1000000 --batch-size 128 --buffer-size 50000 \
  --warmup 512 --updates 1 --save-every 100000 --alpha 0 \
  --allow-noslip-variant --box-foot-proxy
```

后续预算可按同一方式增加到300万，使用新的输出目录并指定最新checkpoint。延长预算不保证课程成功回到初态，须查看frontier与独立正常初态评测。

百万步复核发现CPU矩阵回转TCP四元数与GPU原生`xquat`可能相差一个负号，已统一为原生body四元数。旧bank在哈希校验后仅在内存副本中按FK校正TCP四元数符号，原文件保留；新checkpoint记录`bank_observation_adapter`和派生观测SHA。旧模型可继续恢复，无需重建bank或重训；其在线GPU经验原本就采用原生符号。该修复不等于解决剩余物理迁移差异，见[百万步结果](../development_log/2026-11-09-kitchen-rl-1m-review.md)。

**物理差异：** 两个兼容开关仅允许隔离训练副本关闭noslip并用箱形底座接触代理。原模型未改；这不保证迁移，见[实施计划第9节](DISCODEMO_KITCHEN_IMPLEMENTATION_PLAN_CN.md)。不提供绕过最终CPU验收的导出开关。

## 冻结生成、重放、转VLA数据

训练得到可用策略后，显式选择checkpoint；同初态采样多个z，正式生成只用原CPU模型和正常随机初态：

```bash
bash scripts/kitchen_rl.sh collect \
  --checkpoint experiments/kitchen_platform_v3/rl/apple_discodemo_trial/step000100000 \
  --output experiments/kitchen_platform_v3/rl/apple_generated_v1 \
  --seeds 1000 1001 1002 1003 1004 --variants 3 --horizon 750
```

`horizon750`是30s上限；末态保持时间仍2s。`alpha0`基线无z，须`--variants 1`。每个seed×z有一份报告；失败不入`manifest.json`。当前保留新生成失败尝试的小H5便于定位，`attempts.json`包含全部尝试；不要递归把失败/备份当训练数据。

合格输出是既有V3 H5（一次完整初态＋25Hz双臂绝对关节角/夹爪opening目标＋关键状态，无图像）、英文`annotations.json`和哈希报告，并经过独立control replay。原GPU轨迹不直接导成合格H5。

同初态不同z共享`source_group`，各有唯一`variant_id`，划分训练/验证必须按source_group进行，不能把同初态不同z拆到两侧。现有LeRobot接口允许有哈希证明的DiscoDemo不同variant，仍拒绝专家备份副本。该H5 manifest可交给原`kitchen_smolvla.sh replay/export`流程生成图像、本体状态和LeRobot v3；命令见[苹果50条流程](../kitchen_platform_v3_20261020/APPLE50_SMOLVLA_CN.md)。正式生成的seed应与种子示范训练组分开。

进一步评估必须分别测：未见过的正常随机初态成功率；同初态不同z的路径/姿态/时长差异；最坏碰撞与失败比例。课程末段reward、loss下降或一次终态稳定测试不能替代这些指标。
