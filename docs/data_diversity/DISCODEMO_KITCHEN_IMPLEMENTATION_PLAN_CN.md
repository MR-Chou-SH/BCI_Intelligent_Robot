# Kitchen DiscoDemo适配实施计划

日期2026-11-09。工作基线：`feature/data-collection-improve`，HEAD `395d8295e4669127f82722ae329e58ea261ae543`，保留现有未提交VLA/采集改动。用户已授权先详细规划再实施MuJoCo Warp状态RL数据增强、可动物体状态接口、所有任务成功条件，以及SmolVLA batch8测试。后续澄清以本地DiscoDemo论文为方法依据，替代此前提出的RECAP奖励主方案。

## 1. 论文与本项目目标

本地论文`DISCODEMO: DISCOVERING EFFICIENT AND DIVERSE demenstrations for IL.pdf`，§3.1–3.3、§4.1、附录A/表2、B.1–B.4是依据。用户提供的`.cache/upstream/DiscoDemo`仅作只读参考，不改其中源码，不安装Isaac Sim/Lab。官方[项目](https://davian-robotics.github.io/DiscoDemo/)、[源码](https://github.com/DAVIAN-Robotics/DiscoDemo)用于核对。

**论文初态并非固定。** PnP-Banana的香蕉和碗在指定区域随机分布，香蕉yaw随机；固定场景只是用于排除初态差异、展示同一问题的多种解法。生成器训练最终切回原始初态分布ρ0，正式生成数据同样从ρ0采样。厨房必须同时检验：同初态不同z的解法差异，以及一个共享策略在不同初态的成功率。

主方法是FlashSAC＋并行示范逆向课程＋每episode固定z＋METRA表示学习＋轨迹成功门控＋安全筛选。不是“加动作噪声”、不是一初态一专家，也不是BC→PPO。BC→PPO保留为用户最初提出的可选对照；不把其结果称为DiscoDemo复现。本轮优先建立同模型Warp设施和L1 apple原子试点，其他任务先完成统一状态/判据设计与CPU接口，不声称全部任务的GPU策略已训练成功。

## 2. 目录与边界

| 位置 | 职责 |
| --- | --- |
| `robot_arm/kitchen/object_state.py` | 保留现有语义查询API与同tick只读语义 |
| `robot_arm/kitchen/state_tensor.py`（新增） | 固定字段/固定ID顺序的数值状态，全部可动物体、设备关节、机器人、参考目标；缓存ID及scratch，避免逐步构造大JSON |
| `robot_arm/kitchen/warp_backend.py`（新增） | 隔离GPU训练模型、批量数据、物理步进、Torch/Warp共享内存；显式记录兼容性差异，不做学习 |
| `integration/kitchen/rl/observations.py` | 任务状态归一化、目标相对坐标、actor/critic及φ各自字段选择 |
| `integration/kitchen/rl/tasks.py` | 100实例共用的结构化成功/失败与阶段进度，不依赖专家主动提交skill label |
| `integration/kitchen/rl/environment.py` | CPU参考reset/step、完整快照及L1交互；保留原控制限幅 |
| `integration/kitchen/rl/warp_environment.py` | Warp向量环境、并行reset、L1成功计时/任务事实适配 |
| `integration/kitchen/rl/anchors.py` | 从合格H5重放捕获少量完整课程锚点，独立缓存，不改原数据 |
| `thirdparty/discodemo/` | 算法来源/配置与训练入口；复用上游独立FlashSAC、METRA、课程核心，厨房适配不塞入上游 |
| `integration/kitchen/rl/collect.py` | 冻结生成器，正常初态采样z，CPU重执行/资格检查后接既有H5和标注流程 |
| `scripts/kitchen_rl.sh` | 状态检查、Warp smoke/吞吐、锚点准备、训练、评测、生成命令，均用项目`.venv` |
| `robot_arm/requirements-kitchen-rl.txt` | 显式可选依赖；固定与MuJoCo3.12一致的Warp，CUDA Torch和所需小型训练库 |
| `experiments/kitchen_platform_v3/rl/` | 计划/权重/训练摘要/课程缓存/生成数据分开，训练replay buffer不混入H5训练数据 |

MuJoCo模型、IK、控制器及任务阈值不重写。原始图像-free H5保持不变；RL内部动作可以是左臂8维增量，但最终采集必须写实际接受的双臂绝对joint position＋夹爪opening ratio。SmolVLA/LeRobot仍使用独立`thirdparty/smolvla/.venv`，不把RL状态真值送入VLA输入。

## 3. 状态与恢复契约

### 3.1 固定数值状态

每次返回schema版本、tick和固定字段说明，数值与语义JSON可按ID互查：

- 双臂q、dq、实测指宽、前一/当前接受目标、TCP位置/方向；保持米/弧度/开度ratio的区分。
- 每个可移动对象：固定ID表、世界位置、wxyz四元数、世界线/角速度。包括四水果、两主食及小盘、木餐盘；设备把手/门/按钮包含关节q/dq与位姿及joint mask；载体/支撑关系按需走语义API，不在每步向量中重复构造。
- 设备清洗/加热进度、标记、按钮latch、当前加热对象，以及任务稳定窗口/阶段进度。它们是仿真语义，不声称真实温度或清洁度。
- 支撑、抓持等接触事实按需提供；完整支持证据仍可走已有语义查询。正式终态判定不能只靠对象中心距离。

actor可用真值，按物理工作区/关节范围归一化；不用示范mean/std窄范围裁剪新状态。φ优先目标物、载体、TCP的相对运动特征，排除episode ID、时钟、RNG、z、无关右臂、上一动作及纯夹爪执行内部量，避免靠无关抖动刷分。z单独拼接actor/critic，episode内不切换。

### 3.2 完整快照

快照与策略观测分开。恢复内容为integration state/warmstart、qfrc_bias缓存、控制器previous/accepted/reference/tick/frame_step/stop、设备计时/latch/事件、任务计时/进度、RNG、模型/控制/状态schema身份。只在控制边界、无待执行队列时捕获；仅在episode重置时恢复。

从一个持物中段锚点启动时恢复其整套场景，不能再单独随机挪动已抓物或桌上物体。不同场景覆盖通过多条示范锚点，以及最终正常分布reset完成。原H5中段只有qpos/qvel不能直接冒充完整恢复锚点，必须先从原初态重放到该处。

## 4. 任务判据与顺序

复用`ExpertQuality`、`validate_skill`、`validate_final`、DeviceState，形成`reset(case) / update() / evidence()`接口，返回success、failure_reason、阶段、当前事实与保持时间。任务进度来自实际物理事实，不信任策略自报。

| 原子技能 | 成功必须满足 |
| --- | --- |
| L1 | 指定水果由tray_bottom稳定承托≥2s，线速≤0.02m/s、角速≤0.05rad/s，无手指接触，完整包络在餐盘内 |
| L2 | L1＋实际清洗标记；标记仍由开水/篮内支撑/未抓持及2s设备计时产生 |
| R1 | 指定主食和自己的小盘实际在炉内、主食由小盘承托，门关闭稳定≥0.5s |
| R2 | 指定小盘装盘满足稳定/完整包络/无手指接触，主食仍由自己的小盘承托，门关闭≥0.5s |
| R3 | 门开启≥70°且角速≤0.05rad/s，保持≥0.5s；初始为空/汉堡/热狗前提检查不变 |
| R4 | 指定主食实际完成模拟加热，门打开稳定≥0.5s；按钮/门/炉内前提由设备逻辑检验 |
| R5 | 指定小盘稳定装盘≥2s、完整包络、无手指接触，食物仍在自己的小盘上 |
| B1 | 木餐盘由user_table承托、释放、低速、倾角≤10°稳定≥2s；送盘阶段峰值倾角≤10°，已有载荷关系保留 |

组合任务严格遵循`tasks/catalog.py`中去重链与B1，完成上游后不回home、不重置物体：C04加热装盘；C05 L1+R5；C06 L2+R5；C07 L1+加热；C08 L2+加热；D01～D09为相应链追加B1。加热宏阶段从真实炉内/门/加热状态选择，最终要求加热标记、餐盘稳定主食和关门；已完成水果不能在后续阶段丢失。任务evaluator的CPU语义覆盖20原子+80组合实例；GPU训练先开放L1 apple，其他GPU任务必须显式报不支持。

## 5. RL动作、奖励与DiscoDemo核心

L1生成器先控制左臂8维：7个受限关节增量＋夹爪目标；右臂保持。增量基于上一个接受目标还原，不基于未来示范动作，也不把上一帧实测角度误当同一参考。保留关节/TCP/夹爪限幅与同一位置执行器的重力补偿和力矩上限。

基准奖励遵循论文：

`r = r_task - λ_contact*C_contact + α*gate(episode)*<φ(s_next)-φ(s), z>`。

- `r_task=+1`仅在原厨房成功条件成立时（本项目稳定2s，不照搬论文1s），其余0。
- 不默认加入距离、抓取阶段或固定路线奖励；保留α=0的P-RFCL对照，再增加α多样性项。
- `z∈R³`在reset时采样，整条固定；参考N(0,I)再裁剪[-1,1]。
- episode完成后才知道gate，replay中存唯一episode UID，训练采样时查询最终成功标签。未结束/失败/示范转移gate=0；不能只在最后一步才加多样性，也不能将后来成功写回所有环境或复用UID。
- φ和对偶约束采用上游METRA；n-step intrinsic用同一n步两端φ差，n-step截断、终态和自重置不能跨episode。
- 安全软项只计不期望接触，合法双指-目标接触排除；不直接复制Isaac的50N/20N阈值替换厨房当前阈值。训练与导出规则独立版本化，原始成功示范先校准再固定。
- 最终采集必须从正常ρ0启动，并经CPU后端真实执行、任务验证和独立重放。训练中的近终点课程片段不冒充完整示范。

此前RECAP的`-1/0/-C_failure`不作为本轮默认；若以后比较，单独做奖励消融，明确PPO/RECAP与论文FlashSAC方法的差别。

## 6. 初态随机化与课程

1. 继续既有research分布：对象组XY参考位置附近±8cm、yaw±5°，左右布局不改，接触/桌面支持/不重叠审计保持。
2. 从已有50条合格苹果示范中按初态位置分桶选择约20条种子示范，优先覆盖空间，不要求另采20条。开发/未见测试seed隔离，所有同初态z变体共享split group。
3. 每示范独立frontier；从靠近结尾的合法完整状态开始，frontier窗口最近10条成功率≥0.9才向前退16步，按原文只统计恰在frontier开始的episode。
4. 至少一半示范在起点通过后切到正常ρ0；记录各frontier、阶段和正常初态成功率，不能只报告课程后缀成功率。
5. 单个共享actor覆盖所有初态，z控制解法而不是初态选择；多初态示范＋状态真值观测帮助闭环适应。
6. 固定测试两张表：A固定20个初态×多个z（同场景多样性）；B未见正常seed及边缘位置（随机化泛化）。生成率、接触合格率、模拟时长和样本量匹配。高Vendi但低成功率不算改进。

## 7. Warp后端与资源预算

使用[官方MuJoCo Warp](https://mujoco.readthedocs.io/en/stable/mjwarp/)，保留现有MuJoCo3.12.0，先装同版本mujoco-warp3.12.0和CUDA Torch到项目`.venv`。不升级模型、Python或物理依赖；SmolVLA环境不参与RL。安装仅由`setup_kitchen_environment.sh --with-rl`触发，普通仿真仍不需要Torch/Warp。

GPU训练编译同一模型，Torch共享Warp数组，初始并行8/16/32个world实测显存与SPS后选值，不照搬B200的2048world/100M缓存。CPU小批量参考与GPU终态校验用于发现接触、tendon、solver/float32差异；不承诺CPU/GPU位级相等。内存/约束溢出或不支持特性须显式失败，不能静默退回CPU并标作GPU训练。

**已发现的实际兼容边界：**原模型`noslip_iterations=2`，Warp3.12在put_model时直接报不支持noslip。临时安装检查的Warp3.15同样在io.py拒绝noslip，且它导入时要求MuJoCo3.12没有的`mjINT_DISCRETE`。因此不升级原MuJoCo、不修改原MJCF。新增后端默认显式报错，只有给`--allow-noslip-variant`才在独立模型副本将noslip设0，标记`noslip_disabled_training_only_v1`并分别记录原/训练模型SHA。此为有物理差异的训练兼容实验，不是原模型的等价GPU替换；原模型仍noslip2。已在RTX4060上验证双world真实GPU步进，首次CUDA编译约210s，不计入热吞吐。Warp对部分MULTICCD碰撞类型只产生单点接触的原生警告也必须保留在证据中，不能宣称全接触算法一致。只有原CPU后端重执行并成功的轨迹可进入最终数据。若迁移合格率不足，须停止扩大训练并处理后端差异。

Warp物理和任务Python逻辑是两层：转成GPU并不自动加速设备计时/稳定窗口。L1先移植批量同tick状态/物体支撑/释放/稳定窗口，验证与CPU语义一致；后续设备和长任务再按门禁扩展。

## 8. 逐步实施与验收门槛

| 步骤 | 交付与机器可验标准 | 初始状态 |
| --- | --- | --- |
| G0 方案与依赖 | 本文；只读上游来源核对；可选依赖解析不升级原MuJoCo/NumPy/h5py | 已安装Torch2.11cu128、Warp3.12/Warp-lang1.19；原三项版本保持 |
| G1 batch8 | 同一SmolVLA模型/数据下batch1/8的显存、有限loss/梯度、样本吞吐；不把短测试称为成功率提升；通过后默认batch8 | 已测batch1 1.709 samples/s、1.949GiB；batch8 2.401 samples/s、3.079GiB，吞吐约+40.5%；默认已改8 |
| G2 状态与任务 | 数值表与语义API同tick一致、查询不改integration state；100实例任务链单测；L1 CPU专家动作成功 | 已通过；100实例仅逻辑条件覆盖，不等同100例物理成功 |
| G3 Warp | candidate013隔离副本CUDA真实step，保持控制时钟；记录CPU/GPU差异 | 原模型不兼容noslip；训练副本箱形底座显式双开关；初态向量最大差9.66e-6，8组限幅目标最大差2.25e-7；双world近终点严格2s稳定通过 |
| G4 课程锚点 | H5重放捕获完整稀疏锚点，原数据不变 | 20条不同随机初态完成；CPU快照下一帧逐值相同；GPU恢复有数值/接触差异 |
| G5 生成器 | 上游FlashSAC/混合buffer/课程，先α=0，再METRA+gate，checkpoint恢复 | 两种128transition短测各50次更新；METRA中间checkpoint恢复至相同更新数，gate从0到0.5；16world试跑另见开发记录。尚未学会正常初态完整任务 |
| G6 生成导出 | 冻结策略、正常初态、多z，原CPU成功与独立重放后入manifest | 接口实现；原专家动作经新接口444帧成功且重放误差0；短训策略不宣称成功，失败不入manifest |

短功能训练可执行；50M步或过夜预算不自动启动。即便本轮完成代码与小规模更新，也只报告实测训练/采集结果，不把未充分训练的策略称为合格RL专家。不以稀疏奖励长期无成功为由偷偷加shaping或放宽成功条件。

## 9. 本次实验确认的兼容性边界

MuJoCo-Warp3.12不支持现有模型的`noslip_iterations=2`；3.15仍拒绝noslip，且要求本机MuJoCo3.12尚无的积分器接口，因此没有升级原物理库。训练副本需显式`--allow-noslip-variant`，原模型始终保持noslip=2。

仅关闭noslip仍不足：Warp对cylinder/box的MULTICCD接触近似只有一个接触点，苹果底座持续摇晃，7.2s后角速仍0.058–0.079rad/s，未达到原0.05rad/s条件。显式`--box-foot-proxy`只在训练副本把苹果圆柱底座碰撞形状改成内接箱体，保留质量/惯量和底面高度；该副本双world都在2s后通过原稳定门槛。原MJCF和CPU模型没有改。它是**存在物理域差异的工程试验**，不是声称GPU与CPU完全等价；是否能迁移完整抓取必须另行检验，最终动作必须在原CPU模型通过实测guard、任务判定和独立重放。

当前GPU向量环境仅L1 apple；物体/设备关节状态可读，但尚未实现清洗/加热设备计时，因此不能拿它训练其他任务。100实例成功监视器在CPU可复用，不代表100任务Warp环境已完成。

课程在每16个原始控制帧保存一个完整锚点，上游课程索引映射为锚点索引，一次后退一锚点；几何后缀采样因此也以锚点为单位。这是降低缓存量的明确偏离。每回合预算用真实剩余控制帧+75，稳定计时从零开始。正式生成不使用锚点，只从原随机初态分布开始。checkpoint保存网络/优化器/表征/gate/replay/n-step队列/随机数/课程和物理状态；GPU恢复重算接触缓存，不保证逐bit相同。

安装、训练及生成命令见[运行说明](DISCODEMO_KITCHEN_RUNBOOK_CN.md)。
