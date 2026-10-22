# Planner API 中文接口说明

日期：2026-10-23。以 `robot_arm/kitchen/platform.py`、`control.py`、`contact_execution.py`、`integration/kitchen/platform_server.py`、`planner_client.py` 为权威。API 1.2.1，候选 013，接口与物理门禁本轮未修改。先按 [开发入口](VLA_PLANNER_HANDOFF_CN.md) 安装和启动。

## 1. 这是不是规划器

不是。规划器根据任务和观测计算安全动作；本平台是执行器、环境和证据记录器。它不会因接收到“把苹果放进篮子”就自动生成路径。Research APK 是只读状态显示加 reset 客户端，PC MuJoCo 是唯一物理权威，VLA 模型和通用规划算法由接手同学开发。没有 Quest 也能完成全部 PC 操作。

提交一个遥远关节目标只会触发限速或拒绝，不等于规划好了连续无碰撞路径。几何预检 PASS、执行队列 COMPLETED、持续承载成功、最终任务成功是四种不同证据。

## 2. 连接和协议

Planner：TCP 11031，UTF-8 一行一个 JSON，行尾 `\n`，请求上限 2 MiB。World：TCP 11032，连接即持续收到只读场景 JSON 行，不向它发送控制请求。默认只绑定 127.0.0.1；Quest 联机须使用 PC 可达 LAN 地址。没有 TLS/账号鉴权，只用于可信开发网络，不开放公网。

```python
from integration.kitchen.planner_client import PlannerClient

def require(reply):
    if not reply["ok"]:
        raise RuntimeError(reply["error"])
    return reply["result"]

with PlannerClient("127.0.0.1", 11031) as pc:
    scene = require(pc.call("get_scene_manifest"))
    obs = require(pc.call("observe"))
    print(scene["scene_version"], obs["episode"], obs["sim_tick"])
```

所有正常 RPC 回包为 `{"ok":true,"result":...,"token":...}`；失败为 `{"ok":false,"error":{"code":...,"detail":...},"token":...}`。**PlannerClient 不自动为 `ok:false` 抛异常**，调用方必须检查。网络断线/不完整回复才会抛网络异常。默认超时 30 s；有限 contact 预检可能较慢，可显式增加，不无限重试。

### 身份和单写者

| 项目 | 真实含义与规则 |
| --- | --- |
| token | `runtime_session, epoch, sim_tick, scene_hash, control_hash, episode_id, initialization_hash` 必须与当前环境一致；每次回复更新客户端 token |
| `scene_hash` | 基础场景/模型身份；Research 不因每次随机初态改变基础模型 hash |
| episode | `episode_id` 是本局唯一身份；`episode_index` 随 reset 增加；`seed` 决定同版本初始化；`initialization_hash` 绑定完整初态，不等于基础模型 hash |
| writer lease | 第一次成功写操作绑定 `writer_id + TCP connection_id`；只有同连接同 writer 可继续写。多个只读客户端可共存 |
| Research UI | reset 也属于写操作；规划器持续持有 lease 时，Quest reset 可能收到 `WRITER_OWNERSHIP_CONFLICT`，不是随机化损坏 |
| request_id | 每次写请求 1–128 字符；writer_id 16–128 字符；客户端自动生成 UUID。服务缓存最近 2048 个写回复 |
| 幂等重试 | 同一 request_id 只能重试**完全相同**的 method/args/token/writer_id，返回原回复；换 token 或参数会 `REQUEST_ID_CONFLICT`。不要把失败请求换 ID 盲发 |
| stale token | reset/step 后旧 token 失效；`hold/cancel` 仅容忍旧 sim_tick，其他身份仍须匹配 |
| 断线 | 有 RUNNING 或活跃接触时，写连接断开触发 `WRITER_DISCONNECTED`、取消并锁存停止；不是自动续跑或安全释放物体 |

## 3. 状态、坐标与控制单位

| 数据 | 顺序 / 单位 / 解读 |
| --- | --- |
| 16 维目标 | 索引 0–6 左 FR3 q1…q7，7 左 tendon，8–14 右 q1…q7，15 右 tendon |
| 关节 | 七旋转关节，rad；速度 rad/s；真实机构、限位来自实际模型，不修改连杆长度或限位伪造可达 |
| tendon 目标 | m，范围 0…0.05，0 张开、增大闭合；不是“目标指间距”。每控制帧最多改变 0.002 m |
| `finger_qpos_m` | 两个手指实际滑动关节位置；`state16_measured` 对夹爪取其均值，不能把它当 tendon 命令 |
| `physical_inner_gap_m` | 根据真实指垫几何算出的内间距，m；完全张开约 0.08344 m，不等于 tendon 值 |
| TCP | `tcp_position_world_m`、`tcp_quaternion_wxyz`，右手世界坐标 +X 右、+Y 远离用户、+Z 向上；m，四元数 wxyz |
| 机器人局部坐标 | 基座位姿见 manifest/模型；规划目标通常用世界坐标。局部目标须用基座刚体变换转换，不能直接把世界 xyz 当基座 xyz |
| Unity | 位置 `(x,z,y)`；旋转 `C R C^T`，不是随意交换四元数分量；米制比例 1。Unity 不独立计算抓持、碰撞或随机化 |
| `reference16` | 上一个完整控制帧的参考；`accepted_target16` 本帧接受的目标；`actual_ctrl16` 当前物理步实际驱动信号，三者不等同 |
| 时钟 | 控制 25 Hz（0.04 s），物理 500 Hz（0.002 s）；每 16 维 waypoint 占 20 个真实子步，不是瞬移到 qpos |
| manual | 只由 `step` 推进物理；World 每约 0.04 s 发一帧并不让机器人自动运动 |
| realtime | 服务自行每轮推进 20 子步；外部 `step` 被 `REALTIME_CLOCK_OWNS_STEP` 拒绝。首个示例用 manual |

当前门禁：关节命令 0.7 rad/s，TCP 命令 0.12 m/s、实测 0.15 m/s；普通接触 5 N、明确允许的指垫接触 40 N；普通净距 2 mm。完整限位请读取模型与 `limits.py`，不要按表格盲改数值。规划器不能关闭碰撞、直接写自由物体位姿、添加无条件焊接，或直接驱动门/水龙头关节。

## 4. 只读接口

以下都是 `pc.call(method, args)`，不加 mutating，不推进 live 物理。`check_contact_motion` 在私有 scratch 数据上进行有限动力学预演，不是只算 IK。

| 方法 | 参数和最小调用 | result / 使用时机 / 常见错误 |
| --- | --- | --- |
| `get_scene_manifest` | `{}`；`pc.call("get_scene_manifest")` | 返回 scene/layout/source/compiled/control 身份、robots、objects/object_ids、坐标、limits、初始化和能力边界；连接后先读，拒绝版本不匹配 |
| `observe` | `{}` | 当前 token 同字段、sim_time_s、episode、完整 qpos/qvel、两臂状态、三类控制向量、objects、devices、execution、contact_execution、stopped/stop_reason；**没有 RGB** |
| `query_objects` | `{"ids":["apple","burger_container"]}`；ids 可单字符串或省略，另可 `object_type` 过滤 | 列表：语义 ID、类型、世界位姿、速度、尺寸/可操作信息；`UNKNOWN_OBJECT`、`INVALID_OBJECT_IDS`；rice 等 `UNSUPPORTED_OBJECT_IN_SCENE_VERSION`，不得把旧任务编号重新分配 |
| `get_world_state` | `{}` | World schema 1：runtimeSession、sceneEpoch、sceneHash、sequence、simTick、episode、bodies(id/name/position/quaternion_wxyz)、state16、stopped；读取会增加流 sequence，不增加 sim_tick |
| `check_configuration` | `{"joint_targets":q16,"contact_targets":[]}` | 当前物体/设备/手指冻结时的机器人姿态净距检查；非法16维/限位→`INVALID_TARGETS`/`JOINT_OR_TENDON_LIMIT` |
| `check_motion` | `{"end_joint_targets":q16}`；可选 `start_joint_targets`、`contact_targets` | result.pass/status、连续净距界、起终配置 hash、identity；只证明当前冻结物体/手指/设备下的关节插值几何，不是负载和接触动力学 PASS |
| `check_contact_motion` | `{"waypoints":[q16],"contact_contract":contract}` | pass/status、测量与有限预演；最多50帧、1000 scratch步、3秒预算。目标不存在/契约非法/超限则拒绝；无 live 写入 |
| `get_execution_status` | `{}` 当前；或 `{"execution_id":id}` | 返回最近执行或历史最多128条，初始可为 null；未知 ID→`UNKNOWN_EXECUTION_ID` |
| `get_task_feedback` | `{"task_instance_id":"pick-001","object_ids":["apple"]}`；可省略 | 对象实际 grasp/in_basket/in_microwave、device_state、events、contact_execution、evidence_tick；`task_success` 固定 `NOT_VERIFIED`，`trajectory_completion_is_task_success=false`，不替外部任务验证器决定最终成功 |

`contact_targets` 是旧式几何接近白名单，不是关闭碰撞，也不是完整抓持协议。新的真实接触操作优先使用下面 contact_contract；不能同时混用旧权限与新契约。

## 5. 写接口

均须 `pc.call(method, args, mutating=True)`；先读当前状态、检查 stopped、phase 和身份。所有 waypoint 是有限16维数值。

| 方法 | 真实参数 / 示例 | 行为、result 和关键失败 |
| --- | --- | --- |
| `reset` | `{"seed":0}`、`{"seed":1}`、`{}`；无其他参数 | 返回完整 observe；Research 省略/null seed 生成不同于当前 seed 的 uint32；fixed 省略保留当前 seed。显式 seed 必须 Python int 0…2^32−1，bool/负数/浮点拒绝；不能用 reset 偷改布局或 profile |
| `submit_joint_targets` | `{"joint_targets":q16,"source":"external_planner"}`；可选 guarded_gripper/contact_contract/contact_targets | 接受一个控制帧，返回 execution、requested、accepted、rate_limited；**可能限速**，必须看 accepted；此调用本身不 step |
| `execute_trajectory` | `{"waypoints":[q0,q1],"source":"external_planner"}`；同上可选项 | 逐帧预检并排队，返回 execution；普通最多4500帧，contact最多50。要求已满足每帧限速，否则 `TRAJECTORY_REQUIRES_RATE_LIMITING`，不会替你把遥远点自动填成完整安全路径 |
| `step` | `{"n_substeps":20}`，默认20，整数1…5000 | manual 推进指定真实物理步，返回完整 observe。每帧再次预检，环境改变/接触/力/速度异常会 FAILED 并停止；HTTP/TCP ok 不等于执行无失败，要读 execution/stopped |
| `end_contact` | `{}` | 必须处于 release 且队列空、控制帧结束、实际指垫分离≥50 ms、力<0.5 N、负载有声明支撑、线速度≤0.02 m/s和角速度≤0.05 rad/s；封存证据后清理契约，返回 released/feedback，**不推断任务成功** |
| `cancel` | `{}` | 锁存 `CANCELLED_BY_CLIENT`、执行 CANCELLED，清队列/接触并封存；不是打开夹爪或把物体放回原处 |
| `hold` | `{}` | 锁存 `HELD_BY_CLIENT`、执行 HELD、保持实际关节附近；不是物体释放或暂停后可继续。处理原因后 reset 才能重新动作 |

`source` 只允许 `external_planner` 或 `programmatic_validation`，不是任意标签。开始动作须位于20步控制边界、无正在运行执行、未锁存停止；否则 `EXECUTION_BUSY`、`CONTROL_BOUNDARY_REQUIRED`、`CONTROLLER_STOPPED`。

### reset 的失败保护

先私下构建新场景、几何初始化、记录元数据，并封存旧证据，全部成功后才替换 live episode。失败保留旧回合；准备文件的 `PREPARED_ONLY_ACTIVE_EPISODE_IS_FROM_OBSERVE` 不能当发布成功。RUNNING→`EXECUTION_BUSY_CANCEL_FIRST`；活跃接触→`CONTACT_ACTIVE_CANCEL_FIRST`。不要跳过正确释放而强行 reset。reset 不是回放倒带；seed 相同是**初态复现**，新 episode_id/epoch 仍不同。

### 实际接触契约

```python
contract = {
    "arm": "right", "target": "burger_container", "phase": "acquire",
    "payload_ids": ["burger_container", "burger"],
    "supports": {"burger_container": ["v3_counter_right", "microwave_bottom"],
                 "burger": ["burger_plate"]}
}
preview = require(pc.call("check_contact_motion", {"waypoints": [q16], "contact_contract": contract}))
if not preview["pass"]:
    raise RuntimeError(preview)
```

这段只演示数据结构，**q16 必须由你的规划器生成且真实可达**，不声称从 home 即可抓取容器。设备 target 只能为 `faucet_handle_geom`、`microwave_door_handle`、`microwave_start_geom`；自由目标必须在 payload_ids 内。phase 为 acquire/carry/device/release；设备不能 carry，自由物体不能 device。支持几何名称须真实存在、有限且非机器人。契约不创建刚性附着。

acquire/release 为局部运动（30 mm 限制），carry/device 依赖已测双侧接触≥50 ms的持续抓持证据；容许丢失间隔50 ms、滑移15 mm和转动30°等门禁见源码。`target_support_transition:true` 是有测量抓持条件时，允许目标接近声明支撑面的明确选项，不是无条件接触豁免，不代表真正承托成功。汉堡公开参考已经展示该选项和有限等待。

接触队列 COMPLETED 后，contact 仍可能 active，仍需逐步监测。必须实际释放并 `end_contact`，或明确 hold/cancel。漏做释放时直接断开触发停止；不能将“运动执行完”写成“已稳定落放”。guarded_gripper 仅支持手臂静止且实测速度≤0.01 rad/s、跟踪误差≤0.005 rad的序列，不是任意携物动作的替代。

## 6. 最小真实动作链

下面是小幅关节动作与记录烟雾检查，**不是抓取成功示例**。先启动 manual Research 服务。

```python
from integration.kitchen.planner_client import PlannerClient

def require(r):
    if not r["ok"]:
        raise RuntimeError(r["error"])
    return r["result"]

with PlannerClient() as pc:
    a = require(pc.call("reset", {"seed": 0}, mutating=True))
    apple = require(pc.call("query_objects", {"ids": ["apple"]}))
    q = list(a["accepted_target16"])
    q[0] += 0.002  # rad，有限烟雾动作，不含夹爪变化
    check = require(pc.call("check_motion", {"end_joint_targets": q}))
    if not check["pass"]:
        raise RuntimeError(check)
    command = require(pc.call("submit_joint_targets", {"joint_targets": q}, mutating=True))
    b = require(pc.call("step", {"n_substeps": 20}, mutating=True))
    status = require(pc.call("get_execution_status"))
    feedback = require(pc.call("get_task_feedback", {"object_ids": ["apple"]}))
    print(b["episode"], b["sim_tick"], status, feedback["task_success"])
    require(pc.call("hold", mutating=True))
```

所有例子须从源码根运行，或以可移植方式安装该源码包。更完整、可自行启动/停止服务、验证 0→1→0、非法 seed 保护、World、真实20步和记录的脚本见 `integration/kitchen/platform_research_handoff_example.py`，命令见开发入口。

## 7. 从参考轨迹到新原子任务

1. 先读 manifest 与 observe，核对 scene/control/init/episode，查询 apple 或 burger_container 的真实几何、位姿与速度。
2. 从参考阶段理解抓取方向和支撑关系；物体位置改变后重新计算抓取、预接近、撤离和中间路标，使用真实 FR3 IK/连续碰撞验证。不能原样套随机局。
3. 生成按25Hz分帧的有限目标；自由移动 check_motion，指垫/载荷操作用 contact 预检和契约，保留声明支撑面。
4. 通过公开 API 执行小批、step 并检查每次反馈。几何 PASS 不是实际力学 PASS，COMPLETED 不是持续抓住。
5. 自己实现有版本的任务判据：双侧实际接触、抬升高度/持续时间、释放后支撑力、滑移/速度、设备阈值。不要仅依据对象中心点或最后一帧截图。
6. 每次保存请求、完整回复、episode/seed/模型身份、sim_tick/时间、执行状态、失败原因、物理记录路径和 SHA-256；失败也保留，label 未验证时写 UNKNOWN/NOT_VERIFIED。

苹果最新完整避水在内部控制器成功，未验证完整公开 API；汉堡旧2ff来源动作经公开 run04 在当前b672控制器完成，增加10个实测等待帧，终点只是炉外持物。交接脚本默认仅跑有限前缀，详见 [参考资产](../../reference/kitchen_v3_trajectories/README.md)。不要通过修改共享冻结控制器“适配”失败轨迹。

## 8. 记录、批量回合与 VLA 缺口

| 记录 | 作用 / 边界 |
| --- | --- |
| ready.json | 服务真正绑定两个端口后的启动快照；不是实时当前回合 |
| `session_<runtime>/initializations/<episode_id>.json` | 完整初态、seed、区域与几何验收、控制身份；以 observe 的活跃 ID 对照 |
| `reset_failures.jsonl` | 记录输入/准备/旧证据封存失败；占用、token 等前置拒绝不一定在这里，客户端也须记录 RPC |
| `episode_<uuid>/physics.jsonl` | 每真实500Hz子步的 metadata/physics/end 哈希链，含同 tick qpos/qvel、reference/accepted/actual命令、接触、设备、停止信息；不是25Hz命令表 |
| execution | evidence_path/evidence_complete/pending_contact_release/last_recorded_tick；无 end、sampling_failed、tick 缺口或 hash 不符不可标完整 |

一个完整任务可以跨多个 evidence 文件，不能只取最后一个文件。原始日志较大，不进 Git；应单独打包传输并校验 SHA-256。现有旧 LeRobot/HDF5/相机工具不等于公开 Research RPC 已完成视觉采集。

接手者可在 PC 循环 reset(seed)→规划→执行→独立判据→封存，记录所有成功/失败并保留 seed 分布与任务版本。基础环境不提供批量专家、通用动作规划或 VLA 训练算法。保持动作来源和真机/仿真数据分开，不因观看2x就把动作时间戳除二。

**目前尚无经过验证的公开 RGB、相机内外参和图像—动作同步接口。** API observe 无 image；World 是几何状态不是图像；sim_tick/仿真秒和记录中的动作—状态同 tick绑定已有，不能替代相机曝光/采样时间。第一周应先定义只读、版本化的视觉观测扩展：相机ID/分辨率/内外参、scene/episode/init绑定、sim_tick/帧ID、RGB存储引用和校验、与action/state的同步规则；另建可选接口或记录旁车，保持现有方法/控制身份不变，独立验收后再标AVAILABLE。尚缺任务语言/组合、成功标签生产、通用随机场景规划和批量数据导出策略，不宣传为开箱即用完整 VLA 训练平台。
