# Kitchen Expert V2 采集续作：跟踪、缓启动与失败归因

2026-10-24 下午接手 Expert V2（两张任务表，20 原子 + 44 组合）。基线：仓库 `/home/firedust/brainInteract/BCI_Intelligent_Robot`，分支 `feature/kitchen-linux-task-collection`，HEAD `4815510`，工作区包含上一轮未提交改动。没有 commit/push，没有操作 Quest、ND8 或物理机器人。

## 接手时的真实状态

- 采集管线和 64 用例目录已实现，`integration/kitchen/tests` 全绿。
- 旧源码下真正 `QUALIFIED_EXPERT` 的只有 `R3:empty`、`L1:apple`、`L1:pear`；其余 41 次尝试全部是失败证据（接触预检 19 次、运动认证 6 次等）。
- 之前只在 12 个用例上试过，`R1:hotdog`、`R2`、`R4`、`R3:burger` 等从未跑过。

## 本轮定位到的真实缺陷

1. **抬升高度写反**：`pick()` 用 `high[2]=min(high[2],1.15)`，只压低不抬高。机械臂在 z≈1.0 原地改姿态，156mm 长手指扫进番茄（1.779mm/2mm）和打开的微波炉门把手。改为 `max` 后番茄从“接近即碰撞”推进到释放阶段。
2. **跟踪提前返回过松**：`joints()` 接受最多 1.5mm / 0.02rad 的未到位偏差，后续 2mm 净距预检经常踩线（苹果 place 1.973mm/2mm）。收紧到 0.4mm / 0.005rad 后，苹果、梨在最终源码下重新合格。
3. **携带无缓启动**：香蕉在 place 第一段角速度 4.0176rad/s（限 4.0）。加入按路程前三点 0.002/0.004/0.006rad/frame 的软启动后，速度违规消失。
4. **B1 夹爪预闭合未认证**：`deliver()` 在无 contract 状态下把双指垫合到 0.020m，触发 `GRIPPER_SWEEP_NOT_CERTIFIED`。改为在 acquire contract 下执行。
5. **圆形水果释放未稳定**：`end_contact` 要求物体速度 ≤0.02rad/s，番茄刚放到托盘仍在滚动。释放抬升后增加 1.5s 稳定等待。

## 仍然阻塞的失败（需要后续轮次）

- **番茄**：32×32×30 椭球（主惯性差 6%），在平行夹爪里会自由滚动，相对转角超过 30°。ADR-004 第 1 条明确禁止把“球体旋转等价”推广到非球形物体，因此本轮已**回退**该放宽，番茄保留在原 30° 门禁下失败。可选方向（需用户确认）：换抓取位姿/方式，或在模型层把番茄改成等半径球体（苹果已有 `equal_shape_sphere_v1` 先例），而不是改门禁。
- **香蕉**：夹持点必须高于台面约 15mm（10mm 时指垫离台面 1.958mm、离水槽壁 1.997mm，均低于 2mm），因此重心力臂约 11–16mm；18s 横向搬运中在指垫内以约 1°/s 蠕滑，累计到 30°。把抓持点从 20mm 降到 15mm 并加速搬运仍未通过；需要更短的搬运或不同的抓取几何。
- **B1 双臂托盘**：静止托盘与台面实测穿透约 -1.0015mm，刚好超过 1mm 支撑柔顺上限（实测平衡穿透由接触刚度决定）；另有 `dual_rim_centering` 后的净距问题。未改阈值。
- **主食承载物**：汉堡盘/食物在抬升加速瞬间穿透约 1.03mm，同样踩在 1mm 支撑柔顺边界上；需要更缓的加速或更硬的接触参数。
- **R1 门把手**：抓取接近路径端点离 `microwave_door_handle` 1.96mm/2mm，尚未处理。

## 2026-10-24 用户决策：番茄球体与临时禁用相对位姿门禁

平台没有吸盘、软指、抱合夹具或凹槽，MuJoCo 也不建模滚动阻力，圆球在平行双指垫之间可以无阻力转动。用户据此指示：(1) 番茄碰撞体改成等半径球；(2) 如果没有抗滚设施，就暂时禁用旋转阈值。

- 番茄：`layouts_v3.json` 的 013 布局新增 `tomato_primitive=equal_shape_sphere_v1`，`contact_v4.apply_contact_v4` 把 32×32×30 椭球改成 32mm 球并把 body 上移 2mm（静止底面仍为 0.800m）。这是模型层等价球，不是放宽椭球门禁；抓取改用 30° 朝向，避免左臂肘部擦台面。`L1:tomato` 因此合格。
- 相对位姿门禁：先是旋转分量，随后用户在看到香蕉滑出 15.0067mm 的证据后指示**整条 `GRASP_RELATIVE_MOTION` 临时禁用**（需要改指垫 mesh，本轮不做）。`contact_execution.GRASP_RELATIVE_GATE_ENABLED=False`，保留 15mm/30° 数值、双向检查和单开关；50ms 夹持、抓持丢失、力/速度/支撑/普通净距门禁不变。`control_identity` 记录 `grasp_relative_gate=disabled_temporary_20261024`。恢复条件见 ADR-004 新增小节。
- 该阶段产生的轨迹必须标注“相对位姿门禁已禁用、支撑柔顺已放宽”，不能当完整专家质量证据。

## 2026-10-24 支撑柔顺与热狗扶手（用户指示）

- **支撑柔顺 1mm → 1.5mm**（临时，单行常量 + identity 标记）。香蕉重测：支撑穿透不再触发，但随即在搬运中失败于 `GRASP_RELATIVE_MOTION` **平移 15.0067mm（限 15mm，超 6.7µm）**，相对旋转 80.5°（旋转分量已禁用）。这说明香蕉的真实问题是**夹持不稳定**：它在 18 秒搬运里绕长轴滚约 80°、同时沿指垫滑出 15mm；放宽支撑柔顺只是把失败点从“放下”提前暴露到“搬运”。不宜再放宽 15mm 平移。
- **热狗抬高扶手**：013 布局与 `contact_v4` 已把 `hotdog_grasp_left/right` 抬到局部 z=0.060、外移到 x=±0.082（带立柱），专家改用与汉堡相同的斜向横杆抓取。探测显示右臂接近热狗时会擦到**汉堡的抬高横杆**（1.66mm/2mm），把容器搬运高度抬到 1.30m 也没消除，说明需要基于已知物体状态做**带 via 点的净距感知路径**，而不是继续固定姿态。该 1.30m 试验已回退。
- 参考轨迹复用：`reference/kitchen_v3_trajectories/` 里已有苹果完整洗水果控制序列和汉堡公开调用序列；但它们绑定旧的 scene/control 身份，当前模型改动后必须重新验证后才能作为 L2/R1/R2 的主干。下一步应把这两段录制序列接进专家录制与质量门禁，而不是继续手写抓放。

## 验证与边界

- `.venv/bin/python -B -m unittest discover -s integration/kitchen/tests -q`：**24/24 PASS**（新增旋转开关、支撑柔顺与热狗扶手回归测试）。
- `git -c core.whitespace=cr-at-eol diff --check` 通过。
- 固定 seed0 的 20 个原子实例全量矩阵（源码 `expert.py` f1734856…、`contact_execution.py` c30610c4…，旋转门禁禁用）：

| 用例 | 结果 | 主要证据 |
| --- | --- | --- |
| `L1:apple` / `L1:pear` / `L1:tomato` | **QUALIFIED_EXPERT** | `experiments/kitchen_platform_v3/linux_acceptance_expert_atomic_final_20261024/` |
| `R3:empty` | **QUALIFIED_EXPERT** | 同上 |
| `L1:banana` | FAILED | 放到托盘后 `tray_bottom`/`banana_2` 穿透 1.0211mm（柔顺上限 1mm） |
| `L2:banana` | FAILED | 接近篮底 1.7215mm（普通净距 2mm） |
| `L2:tomato` | FAILED | 篮底穿透 1.0000mm，超出上限约 9nm |
| `L2:pear` | FAILED | 物体角速度 4.032rad/s（限 4.0） |
| `L2:apple` | FAILED | 水龙头段 `JOINT_TRACKING_BUDGET` |
| `R1/R2/R3/R4` 汉堡 | FAILED | 微波炉门板与汉堡盘 1.9689mm（2mm） |
| `R1/R2/R3/R4/R5` 热狗 | FAILED | 指垫与热狗食物 1.9902mm（2mm） |
| `R5:burger` | FAILED | 抬升后 IK 失败 |
| `B1` | FAILED | 双臂托盘接近 1.9969mm（2mm） |

**关键结论**：20 个原子实例里 4 个合格；16 个失败中有 11 个只差 10–31 微米就达到 2mm 普通净距。也就是说当前瓶颈不是专家动作本身，而是“2mm 净距 / 1mm 支撑柔顺”与这套长指夹爪几何的标定过于贴边。没有用户授权，本轮没有改这两个阈值。下一步可以：微调热狗抓持高度（已在探测 +26mm）和汉堡入炉横向偏移（-4mm）；或经用户同意后给普通净距加入毫米级测量容差。

- 审阅视频：`experiments/kitchen_platform_v3/review_videos_atomic_final_20261024/`（每个原子用例一个 MP4，含失败轨迹；仅可视化，不是训练数据）。
- 所有结论仅限 MuJoCo 仿真；没有真机、光学或 EEG 结论。失败尝试全部保留在 `experiments/kitchen_platform_v3/`，由 gitignore 排除，不进训练数据。

## 最新一轮：整条相对位姿门禁禁用后的 L1/L2

在“相对位姿门禁整条禁用 + 支撑柔顺 1.5mm + 热狗抬高扶手”这版源码下重跑 L1/L2（`experiments/kitchen_platform_v3/linux_acceptance_expert_L1L2_norel_20261024`）：

- L1：apple / pear / tomato **合格**；banana 失败于 `end_contact`——一个指垫仍陷在香蕉里 0.114mm、另一个已离开 49.6mm，香蕉靠在打开的指垫上。这是释放动作缺少“侧向剥离”，不是阈值问题。
- L2：全部失败，且原因各不相同——apple 在水龙头段 `JOINT_TRACKING_BUDGET`；tomato 在放置段 `JOINT_TRACKING_BUDGET`；pear 载荷角速度 4.0316rad/s（限 4.0）；banana 接近篮底 1.7215mm（普通净距 2mm）。
- 结论：禁用相对位姿门禁把香蕉从“搬运滑出”推进到“释放分离”；L2 剩下的四个问题都不是相对位姿门禁，而是夹爪/规划/跟踪层的具体缺陷。下一步优先做：香蕉释放的侧向剥离、篮子/热狗的净距感知 via 点接近、以及在当前模型下重新验证苹果水龙头录制序列后再复用。

## 2026-10-24 晚：L2 洗水果推进（按用户“过度约束直接放宽”指示）

L2 的 4 个固定初态此前全部卡在 `device_faucet`。逐层定位后做了 5 处修改，均记录在 control identity 或代码注释里：

1. **`joints()` 收敛判定过严**：原逻辑要求“控制器下发的参考值精确等于目标”，但控制器本身有 TCP 步长限幅，参考值可能停住，导致白跑 600 帧后报 `JOINT_TRACKING_BUDGET`。改为：参考值连续 40 帧不变且实测关节误差 <3mrad、速度 <0.01 时接受，并记录 `measured_stall_settled`。
2. **录制转向复用**：历史上录制的苹果水龙头转向因相对滑移 >15mm 被改成 `turn_faucet` 重规划；重规划动作会丢手柄（`GRASP_LOST` 52ms，放宽到 100ms 后变成 102ms，证明是真丢）。相对位姿门禁已禁用，因此直接回放录制转向（`faucet_reference` 现在整段回放 47980–63880），不再 IK 重规划。
3. **回放尾部夹爪**：释放后录制尾段仍带夹爪开合指令，触发 `GRIPPER_SWEEP_NOT_CERTIFIED`。释放后把夹爪通道固定为当前值，只回放关节。
4. **丢持宽限 50ms → 100ms**：52ms 的短暂再接触曾被判丢失；保留 100ms 以拒绝真实丢持，同时不因一个控制帧的间隙失败。
5. **普通接触力上限 5N → 15N**：洗完后从篮子里重新抓取时，指垫压到篮壁属于正常接触，旧 5N 阈值会 `CONTACT_GUARD` 停机。指垫抓可取物仍是 40N；真正碰撞仍会停。`control_identity.ordinary_contact_limit_n=15.0`。

录制元数据的时间点（`reference/kitchen_v3_trajectories/apple_metadata.json`）：`faucet_on` 110.12s、`apple_wash_state_complete` 113.32s（水开 3.2s，满足 3s 清洗）、`faucet_off_released` 127.76s。

### L2 结果（2026-10-24 深夜）

- `L2:apple`、`L2:pear`、`L2:banana`：**QUALIFIED_EXPERT**（`experiments/kitchen_platform_v3/linux_acceptance_L2_force15_20261024/`），完整洗水果流程（抓→放篮→复用录制开水/等待/关水→再抓→放盘），控制重算通过。
- `L2:tomato`：重抓时指垫进入篮壁 3.364mm，超过当时 3mm 普通穿透容差 0.36mm；按用户“过度约束直接放宽”的指示把普通穿透容差改为 4mm 后单独复测。
- 普通接触力上限 5N→15N、丢持宽限 50ms→100ms 均已记录在 control identity。

番茄复测（容差 4mm）显示 `basket_wall_3` 距离 -4.23mm，正好跟着容差变深——说明重抓动作本身在往篮壁里压，不是临界阈值问题。已把普通穿透容差退回 3mm（与 apple/pear/banana 三个合格数据同一 identity），番茄保留为失败证据，下一步应改重抓接近姿态（从篮口上方/侧面切入或抬高抓取点），而不是继续放宽。

### 运行时提速（用户批准 1+2）

Profile（31s 采样）显示热点：每路点运动证书 14.3s（其中 `pairs()/eligible()` 重建占约 7s、390 万次）、`ExpertQuality.sample` 每子步 5.9s、`devices.update`+`safety.inspect` 每子步 3.4s、`mj_step` 2.3s。

修改：

1. `CollisionGeometry.pairs()` 按 moving 集合缓存——证书从 8.5ms/次降到 2.48ms/次；
2. 接触策略、任务质量判据、设备计时、ContactGuard 从每 2ms 子步改为**每 25Hz 控制帧一次**；引擎按 stride 累加年龄/丢失/分离时间，`devices.update(dt)` 用整帧时长；scratch 预检 clone 仍保持逐子步采样；
3. 500Hz 动作记录（`actual_ctrl16`）与 25Hz 状态检查点不变。

验证：同一番茄 L2 失败（篮壁 -3.3637mm）复现，墙钟 **373s（原 601–776s，约 1.6–2× 提速）**；27 项测试通过。

### 圆球底座（用户指示）与 L2 番茄合格

给场景里所有球（apple、tomato）加了与球底齐平的圆柱底座：`contact_v4._flat_foot`，苹果 r=20mm、番茄 r=18mm，半高 4mm，底面与球最低点同高（静止高度不变），摩擦/接触参数与球一致。

结果：**`L2:tomato` QUALIFIED_EXPERT**（421.56s 仿真、210,780 步、控制重算 qpos/qvel 误差 0、墙钟 745s）。底座消除了番茄在篮内的滚动，落点保持在中心，重抓不再压篮壁。

代价：模型身份改变，之前所有轨迹（L1×4、L2 apple/pear/banana、R3 empty）都变成“旧模型”。已启动 L1+L2 在新底座模型下的统一重跑，完成后以新数据为准。

### 去除每路点预检与连续规划

- **每路点 scratch 预检**：`_start_execution` 本来就会在整批路点下发前做一次完整认证（无 contract 逐路点碰撞证书，有 contract 整段 scratch 动力学预检）；`_advance_locked` 在每个 40ms 路点又对实时状态重复一遍，属于冗余。已删除该重复预检，保留整批认证 + 25Hz 实时接触策略/守卫。
- **连续规划**：去掉 `pick()` 开头、`reference_device()` 开头、`fruit()`/`meal()` 结尾的强制 `return_home`。改为从当前位姿直接规划：`pick` 用垂直抬升到 `high` 作为安全过渡；设备回放前先升到 1.15m 的公共安全高度再进入录制轨迹。路径允许斜线/直线段组合，不再“回位→重来”。
- 验证：`L2:apple` 在连续规划下 **QUALIFIED，墙钟 412s**；同一用例在旧代码（每路点预检 + 回 home）下为 933s，约 **2.3× 提速**。
- 底座模型下 L1 4/4 + L2 4/4 已达标（旧执行路径）；已启动新源码下的统一重跑，保证数据与当前代码一致。

### R 侧现状（连续规划下）

- `R3:empty` **QUALIFIED**（29s）。
- 热狗 R1/R2/R3/R4/R5：右臂接近热狗时路径跨过**汉堡的抬高扶手**，-4.23mm；加 1.28m via 点只是把冲突点后移，因为 pre 姿态下长指仍扫到扶手。需要换接近侧（热狗右扶手）或障碍感知走廊。
- 汉堡 R1/R2/R3/R4：放置时指垫压进**自己承载物的把手立柱** 3.0003mm（3mm 指垫柔顺）；把柔顺放到 4mm 后变成 4.0023mm——**放宽多少就压多深**，是抓持接触本身的几何重叠，不是余量问题。正确做法是让“指垫对自己抓取的 payload”的穿透检查豁免（40N 力上限仍保留），而不是继续加容差。
- `R5:burger`：抬升后旋转走廊 IK 够不到（目标 [0.287,-0.066,1.076]，误差 2cm）。
- 4mm 的两个临时放宽已退回 3mm，保持与 L1/L2 连续数据一致的 identity。
