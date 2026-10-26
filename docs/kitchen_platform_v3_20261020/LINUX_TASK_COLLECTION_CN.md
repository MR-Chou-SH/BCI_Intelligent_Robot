# Linux 厨房环境与原子／长任务采集

日期：2026-10-24。开发分支：`feature/kitchen-linux-task-collection`，起点 `4815510`。

最新无VR入口：[状态接口、连续无图像录制、MuJoCo重放与MP4](STATE_RECORD_REPLAY_CN.md)。API已扩展为1.3.0，本阶段暂不采集图像。最新录制／观看能力以上述文档为准。

本轮已落地 Linux 独立环境、本机服务启动与验收、局部初态随机化、图片任务图，以及规划器侧的分段记录层。新提供的两张任务表使用独立Expert V2目录，已接入20＋44个用例的动作流程、合法初态准备、500Hz质量检查及批量脚本，见[Expert V2采集说明](EXPERT_V2_COLLECTION_CN.md)。**全套任务的物理验收仍在进行，不能把完整目录视为全部成功。** 当前暂不采图像；两条历史长轨迹提供动作设计参考。

## 1. Linux 环境

本机已创建 `.venv/`，使用已有 CPython **3.12.12**，未替换系统 Python 3.10.12。依赖来自原 `robot_arm/requirements-kitchen-research.txt`，MuJoCo **3.12.0**、NumPy **2.5.3**、h5py **3.16.0** 及其十项锁定包全部安装成功。Windows 原实测解释器为3.12.14；这里单独记录 Linux 的实际版本，不冒称完全一致。

在项目根运行。当前机器可以直接使用 `.venv/bin/python`。需要重建时，先选择机器已有的 Python 3.12；本机的安装命令是：

```bash
uv --cache-dir /tmp/kitchen-uv-cache venv \
  --python /home/firedust/.local/share/uv/python/cpython-3.12.12-linux-x86_64-gnu/bin/python3.12 .venv
uv --cache-dir /tmp/kitchen-uv-cache pip install \
  --python .venv/bin/python -r robot_arm/requirements-kitchen-research.txt
.venv/bin/python -B -m integration.kitchen.research_deploy_probe --inventory --compile-model
```

解释器绝对路径是本机配置，不是其他 Linux 电脑的部署前提。有 `python3.12` 和 venv/pip 的机器也可按标准方式建环境并安装同一个 requirements。以上不需要安装或升级系统依赖。无窗口物理仿真与Linux EGL视频已验收；交互viewer未人工验收，RGB训练采集不在本轮范围。

## 2. 不改变左右布局的局部随机化

保留候选013的机器人底座、左右工作区、水槽、微波炉、中央托盘和用户桌板。四种水果始终在左侧；汉堡、热狗及其承载物始终在右侧。

每组根物体围绕模型原始初位，世界坐标 **x、y 各最多 ±0.08 m**，相对原始姿态的偏航 **±5°**；这是方形采样范围，不是8 cm圆形半径。高度仅按真实台面支撑修正。食物与容器一起旋转和平移，不打乱相对放置关系。不随机质量、摩擦、底座或设备位置。

每组最多64次拒绝采样，仍检查完整形状、台面支撑、左右区域、障碍间距、组间距，以及两个静态接近端点的7关节IK和净距。所有组放完后再次核验；失败不提交live状态、不放宽安全阈值。seed决定初态，新回合仍有独立episode ID。

选择8 cm的原因：3 cm与5 cm窗口在seed 0的西红柿附近64次均未通过已有静态接近检查，不能仅为了“范围小”删除检查。8 cm、±5°在seed 0–7通过；这不是任意seed、完整路径或真实抓放成功保证。若后续要收紧窗口，应先改善接近姿态搜索，而非降低碰撞门槛。

初始化算法标识为 `local_xy80mm_yaw5deg_full_geometry_static_ik_v2`。源码参与control hash，初态身份也已更新。Linux编译模型产生新指纹；原参考加载器的精确身份检查保持有效，会拒绝将Windows历史身份用于本版本。不要改参考metadata或关闭身份检查来“兼容”。

## 3. 启动和验收

原PowerShell入口保留；新增不依赖PowerShell的Python入口。下面启动一个新run，路径相对于本仓库；不能覆盖旧run或通过符号链接写到其他目录。

```bash
RUN="experiments/kitchen_platform_v3/linux_acceptance_manual_$(date +%Y%m%dT%H%M%S)"
.venv/bin/python -B -m integration.kitchen.launch \
  --data-root "$RUN/data" --ready-file "$RUN/ready.json" \
  --initialization-profile research --seed 0 --mode manual \
  --bind-host 127.0.0.1 --planner-port 11031 --world-port 11032
```

看到 `PLATFORM_READY` 后连接PlannerClient。manual模式必须由规划器step才推进物理，终端留在运行状态是正常的。

以下验收会自行启动和关闭自己的服务，并用自动端口避免占用现有服务：

```bash
RUN="experiments/kitchen_platform_v3/linux_acceptance_smoke_$(date +%Y%m%dT%H%M%S)"
.venv/bin/python -B -m integration.kitchen.platform_research_handoff_example \
  --launch --launcher native --seeds 0 1 0 --settle-substeps 20 \
  --audit-geometry --read-records \
  --data-root "$RUN/data" --ready-file "$RUN/ready.json" --output "$RUN/client"
.venv/bin/python -B -m unittest discover -s integration/kitchen/tests -v
```

本机实际验收报告：`experiments/kitchen_platform_v3/linux_acceptance_20261024_retry/client/report.json`，状态 `PC_RESEARCH_HANDOFF_PASS`。验证了同seed复现且回合ID不同、非法seed/陈旧token/重复请求保护、World身份同步、实际20步微动作和封存物理记录校验；服务退出后两个端口均关闭。首次运行被沙箱禁止socket，失败记录保留在`linux_acceptance_20261024/`。上述目录已忽略，不提交大日志。

## 4. 图片任务如何对应现有原子任务

以下为采集用例名称，不重编号、不修改冻结V1.1目录。等待是设备事件，不是机械臂原子动作。这里“水槽”沿用现有果篮承托实现。

| 用例名称 | 图片路线 | 原子序列 | 必须先满足的状态 |
| --- | --- | --- | --- |
| `fruit_to_tray` | 水果直接到餐盘 | L01 → L06 | 左侧水果可抓、左夹爪空；托盘有合法落放空间 |
| `wash_fruit_to_tray` | 入水槽、开水、等、关水、取出到餐盘 | C04：L01 → L02 → L03 → 清洗完成 → L04 → L05 → L06 | 水果在左侧，龙头初始关闭 |
| `load_open_microwave` | 门开且空，放主食后关门 | R02 → R03 → R04 | 门已开、炉内空、主食在右侧 |
| `unload_open_microwave` | 门开且有物，取到餐盘后关门 | R06 → R07 → R04 | 门已开、指定主食在炉内 |
| `open_closed_microwave` | 门关，直接开门 | R01 | 门关闭、未加热 |
| `heat_then_open` | 门关，按启动、等待、开门 | R05 → 加热完成 → R01 | 炉内有合法承托主食、门关闭、未在加热 |
| `meal_to_tray` | 直接把主食放到餐盘 | R02 → R07 | 指定主食在右侧且夹爪空 |
| `deliver_tray` | 双臂把托盘递给用户 | B01 | 双爪空、托盘承托稳定，载荷及递送空间需另行验证 |

“任意状态”仅表示某些设备状态与该路线无关，不表示允许穿过关闭的门、从空炉取物或重叠落放。直接放托盘也需要空间与承托检查。

```bash
.venv/bin/python -B -m integration.kitchen.collection list
.venv/bin/python -B -m integration.kitchen.collection heat_then_open
.venv/bin/python -B -m integration.kitchen.collection L01
.venv/bin/python -B -m integration.kitchen.collection C10
```

输出包括依赖图、左右臂、重复原子出现编号、旧/新主食角色及计划哈希。支持16个原子、12个原组合和上述8个用例。C10保留双臂独立链并在B01前汇合；C12保留old/new meal身份。输出计划不执行动作，`initial_state_verified=false`不能当成已经满足初态。

## 5. 在规划器中记录原子边界与整条长任务

`TaskCollection`是PlannerClient外层的记录对象，不实现运动策略。先通过合法动作准备场景，并独立核验用例初态；准备过程另存，不进入该原子任务的训练区间。开始后所有规划器RPC都通过同一个collection调用，不在中途reset。

下面的`planner.run_atomic`需要由自己的规划器实现；这是接口接法，不是已经存在的可运行专家策略：

```python
from integration.kitchen.planner_client import PlannerClient
from integration.kitchen.collection import TaskCollection

with PlannerClient() as client:
    # 此前应已通过合法动作准备并核验初态。
    with TaskCollection(client, "新的采集旁车目录", "fruit_to_tray") as collection:
        collection.start_segment("L01:1")
        planner.run_atomic("L01", rpc=collection.call)  # 需要实现：查物体→IK→预检→执行/step→反馈
        collection.end_segment("L01:1")
        collection.start_segment("L06:1")
        planner.run_atomic("L06", rpc=collection.call)
        collection.end_segment("L06:1")
```

中间结束一个原子区间不会hold或reset，因此抓持状态能传给下一原子。只有整局结束/异常退出才hold并封存。设备完成事件必须来自当前反馈，使用 `collection.device_event(event)` 记录，不能伪造等待完成或以sleep代替物理推进。不能重复提交同一事件。

每局旁车保存`initial.json`（计划、状态、manifest）、追加`journal.jsonl`（完整RPC回复与分段事件）、`collection.json`（全部已遇到的execution记录路径、文件SHA/链终值/覆盖tick、分段摘要、反馈）。RPC异常同样写入journal；封存失败另存`collection_failure.json`。它依赖本机可读的服务器记录路径，暂不支持跨机器直接读取物理文件，也不保证断电持久化。

采集完整性检查包含：不同episode混入、损坏链、未封存记录、子步缺口、分段没有物理证据覆盖。记录完整与任务分段完整是两个字段，均不等于物理成功。`task_success`、`physical_quality`保持`NOT_VERIFIED`，`training_export_approved=false`；不绕过旧HDF5/LeRobot的人类来源门禁。新旁车是独立schema，不冒充旧HDF5任务绑定。

## 6. 剩余工作的顺序和验收边界

1. **原子专家轨迹**：从历史苹果/汉堡的阶段、抓取方向和接触契约提取设计思路，目标改为由当前query_objects计算。先固定局逐个通过抓取、落放、开关设备；再验证有限局部随机seed。不直接平移关节表。
2. **任务初态**：开门、放入食物、果篮已有水果、持物等状态需合法预备动作；预备数据与任务区间分开。不直接改物体/门qpos、不创建无条件焊接。当前reset仅接受seed，未提供这些场景预置。
3. **成功判据**：抓持须验证持续双侧接触、抬升及滑移；放置须验证松爪后持续承托和速度；开关设备依据实际关节/设备状态；B01验证双臂承载、托盘倾角、载荷留存、用户桌板稳定承托。每项阈值/持续窗口独立版本化，并核验反例。
4. **组合与批量**：共享原子控制器，规划器根据实时状态选图片分支。记录所有尝试、失败类别和准备开销，不丢弃失败后只报告成功。验证串行链、左右并行链及B01汇合，再扩展seed数量。
5. **状态数据与查看**：按用户要求暂不采图，使用完整初态、500Hz实际控制、25Hz状态检查点完成采集和重放；MP4或viewer用于查看。视觉数据留待单独任务。

Linux服务、状态采集、控制重放与EGL视频已验证，见无VR入口；**全套厨房专家动作、Quest/ND8及真实机器人尚未在本轮验收**。原研究Goal未恢复。本轮新增独立开发工作不修改旧EEG证据和研究方向。
