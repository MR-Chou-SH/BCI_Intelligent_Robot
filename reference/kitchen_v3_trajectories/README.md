# 两条固定场景物理参考轨迹

这是轻量控制资产，不是通用轨迹规划器。先读 [开发入口](../../docs/kitchen_platform_v3_20261020/VLA_PLANNER_HANDOFF_CN.md)。文件的逐项SHA见manifest，来源身份/阶段/判据见两个metadata。单位：七关节rad、夹爪tendon m；16维顺序左7+左夹爪+右7+右夹爪；25Hz控制、500Hz动力学。

## 1. 来源和兼容关系

| 参考 | 原始身份与证据 | 新提取资产的验收边界 |
| --- | --- | --- |
| 苹果 | `apple_clear_14_oblique30r75_fullcycle`，251.76s/125880物理步，完整抓放/洗涤/托盘释放；独立避水审计绑定同一记录 | 内部b672控制器来源；6294帧接受目标；**未经完整公开API执行** |
| 汉堡 | `cycle013_physics_01`，旧2ff控制，128.32s/64160步，Demo保持此记录；公开`api_microwave_cycle013_run_04`为当前b672，实际64360步/128.72s | 保留run04成功公开RPC、contact和10帧真实等待；终点**HELD_OUTSIDE**，不是托盘/桌板落放 |

共同基础：候选013、固定初始化seed0、scene `f52f3483a7d4863ead908a98aa2aae726f13b5f67e6468a1ed5323c5f3771e27`。当前control `b6722bb912fddedf853d54925fda092b8ea2833cbcaeb904040ecde5965335d2`。旧burger source control `2ff69af80d5c6fc0724382dcd478ffa2e0699a227153c46d263a368c5b246155` 不得改贴新身份。两者不是同一连续任务，也不是任意随机场景已安全。

苹果原始physics.jsonl SHA：`4e7ccecda30014113c59df98b2bdab70507345c0f2e5dd79ed6ae4513dfe228c`；burger原始SHA：`f4b117e30e3f0464a9be84d133458867f2a6c6b8945a99fd3500c7eec051a94b`。原始数据单独交付，不进Git；新电脑运行轻量参考不读取原开发者D盘。

## 2. 先看数据

```powershell
& .\.venv\Scripts\python.exe -B -m integration.kitchen.reference_trajectories inspect
Get-Content reference\kitchen_v3_trajectories\apple_metadata.json
Get-Content reference\kitchen_v3_trajectories\burger_metadata.json
Get-Content reference\kitchen_v3_trajectories\apple_controls_25hz.jsonl -TotalCount 2
```

`target16`来自每20步**第一物理样本的accepted_target16**，不是实测qpos、不是前一帧requested_reference16、更不是500Hz actual_ctrl16。控制器逐物理步在前后参考间插值；保留原时间，不对动作做视觉平滑。expected_state16是原实测状态，只用于检查偏离，不写回物体或机器人qpos。

`manifest.extraction_tool_sha256`对应`historical_source/reference_extractor_at_export.py`原字节快照（SHA `bc2d0b20b7d3c787257293fefe3f5189859d899fab4f6ed6557500d657807bbf`）。当前执行入口仅补充了文件句柄关闭，不改提取/控制语义；历史快照只供来源核验，不执行其CLI。

burger RPC去除了原token/连接ID/个人路径，执行时使用新PlannerClient生成的身份；保留真实method/args/waypoints/contact_contract与时钟。3208来源帧加10等待帧=3218实际帧，不能把它们写成3208帧或篡改原记录时长。

## 3. 有界运行：默认只执行100帧

内部苹果短前缀不需要服务器，使用真实MuJoCo与原受限控制器并写新的物理记录：

```powershell
$Tag=[guid]::NewGuid().ToString('N')
& .\.venv\Scripts\python.exe -B -m integration.kitchen.reference_trajectories apple `
  --max-frames 100 --wall-seconds 120 --output "experiments/kitchen_platform_v3/ref_apple_$Tag"
```

burger须先启动**独立 fixed / seed0 / manual**服务，不可连Research随机局。将开发入口启动命令中InitializationProfile改fixed，使用新的Run目录，保持Layout013/Seed0。再运行：

```powershell
$Tag=[guid]::NewGuid().ToString('N')
& .\.venv\Scripts\python.exe -B -m integration.kitchen.reference_trajectories burger `
  --host 127.0.0.1 --port 11031 --max-frames 100 --wall-seconds 120 `
  --output "experiments/kitchen_platform_v3/ref_burger_$Tag"
```

脚本核对当前hash/profile/seed/tick0，使用原API预检/执行/真实step，不绕过保护；遇到失败立即保留result/日志并停止。结束hold是锁存停止，不是释放；重新运行需正确reset或新服务。前缀可能按完整原请求批次停止，少于max-frames是正常的。不要在别人正在执行任务的服务上运行该示例。

显式放大max-frames可研究完整固定轨迹：apple6294、burger3218，wall上限2700s。**本次只验收有限前缀；新提取数据完整重新执行NOT TESTED，未自动继承历史任务PASS。** 整段执行也必须重新核验完整物理窗口/支撑/设备/避水；脚本的FULL执行不是新的完整任务审计器，不会因退出码0自动给任务成功。

## 4. 真实生成思路与代码

| 代码 | 可复用思路 |
| --- | --- |
| `historical_source/platform_apple_cycle.py` | AppleCycle：实际几何抓取、预接近/抬升、接触保持、支撑负载平衡、入篮/再抓/托盘释放；不是无条件附着 |
| `platform_apple_side_maintenance.py` | SideCycle：真实双侧指垫接触、水平旋转开关、开启阈值、明确的状态式清洗、关闭和撤离 |
| `platform_faucet_clearance_maintenance.py` | 最新oblique30/r75几何：侧向接近与姿态、所有机器人碰撞几何对加15mm余量的重力向下水柱检查；非真实水流仿真 |
| `platform_microwave_cycle.py`、`platform_fullops_witness.py`、`platform_microwave_dynamics.py` | 门把手抓持、炉门扫掠、容器携带/支撑释放、真实按钮接触预载、简化5s加热、再次开门取出 |
| `platform_public_microwave_cycle.py` | 旧source到当前API的contact分段、50帧内小批、实测稳定等待、原始时钟与插入步的显式映射 |
| `robot_arm/kitchen/control.py`、`contact_execution.py`、`payload_geometry.py` | 受限IK/关节控制、500Hz接触守卫、负载几何、有限接触预演；共享冻结部分不为参考随意改动 |

上表未写目录前缀的历史生成脚本也位于本目录 `historical_source/`。它们保留原身份/批准与目录约束，**仅用于阅读，不是新电脑的一键入口**；它们读取历史几何/协议/封存文件，不能直接运行旧CLI或伪造旧Goal active。仅两处个人路径字符串被替换为明确占位符；原SHA与公开副本SHA分别见`generator_sources.json`，不冒充原字节。苹果四份源来自该次封存快照；其它历史源来自当前保留的原生成实现，不据此宣称重演旧控制器。无个人路径依赖的执行入口是本目录资产与reference_trajectories模块。

苹果阶段：初态稳定→实际抓取→持续抬升→入篮负载平衡→张开撤离→侧向握把→开水/清洗→关水撤离→再抓→抬升→托盘负载平衡/松指/稳定。每阶段原TCP世界位置、tick和时间见metadata。汉堡阶段/路标和contact阶段见burger metadata，具体每帧目标与支持面见calls。两者采用有限路标/局部IK，不是环境任意变化的通用策略。

## 5. 泛化与独立原始数据交付

可复用的是阶段结构、坐标/单位、接近/撤离思路、实际接触/支撑判据和API调用模式。随机物体位姿后须重新计算目标、IK、全臂/负载/门扫掠路径、接触预演并重新做真实动力学验收；不能只平移TCP或复制关节表。

需要独立复核历史物理时，向维护者索取：苹果完整physics/report/原判据audit/避水audit及其绑定的水柱扫描；burger source physics/report/scene，以及公开run04的全部evidence文件、raw_records_manifest、audit_summary、report与rpc。按metadata中的SHA核验，原始文件缺失时标NOT INDEPENDENTLY REAUDITED，不伪造下载地址。运行提取器必须显式传这些文件；没有隐藏个人D路径，输出只能是新目录。
