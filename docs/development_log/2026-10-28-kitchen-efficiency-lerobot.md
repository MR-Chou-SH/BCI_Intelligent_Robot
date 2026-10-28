# 采集效率、热狗抓取连续性与LeRobot接口（2026-10-28）

## 结论

专家采集使用 `LocalPlannerClient` 同步手动步进，没有sleep或按真实时间限速。`time.monotonic`用于预算，`perf_counter`用于计时；viewer和实时服务的播放调度不参与此采集路径。长任务需要完整的500Hz物理推进，再执行一次完整的独立重仿真，此外还有控制、接触/质量、记录、状态查询和规划开销。

仿真轨迹时长与实际采集墙钟不同。旧D09香蕉热狗seed0是447.32秒仿真、216.15秒采集含重放；本轮最终同case/seed为383.56秒仿真、160.56秒墙钟。初始完整物理状态逐元素相同，均成功、重放qpos/qvel误差0。并发CPU负载未严格控制，墙钟只是本机实测，不是跨机器加速承诺。

## 计时与运行效率

首轮R5 hotdog cProfile测得41820次mj_step、约7.05秒原生物理时间；包含执行与重放，剖析总CPU时间约24.4秒。另有大量静态拓扑遍历、对象包络查询、回复复制等Python开销。profiler本身影响墙钟，不能把该次25.68秒与无profiler运行直接比较。

- `step(detail="execution")`只返回执行摘要，默认full接口保持原样；专家批量发送不再每8帧构造全部机器人/物体/接触状态回复。控制、接触、质量与H5记录回调仍照常执行。
- 按编译场景缓存对象所属geom的静态拓扑集合；返回独立set，防止调用者修改缓存。没有全局无限缓存，也不缓存动态物体位置或接触。
- 精确重放按下一动作、关键状态、阶段事件或控制帧边界成批调用原500Hz控制器，避免每个物理子步重复Python分发与扫描所有阶段。没有跳过物理步，没有降低检查频率。
- faster连续路径每次最多发送25个25Hz目标，接触边界处仍精确切开；旧为8个。每40ms目标和20个物理子步不变，guard和观察者仍按原时钟工作。
- `report.json`新增 `simulated_seconds`、`timing_seconds`：setup、preparation、recording_start、execution、seal、replay、annotation。execution_step_api和execution_outside_api是execution的子项，不能重复相加；后者包含IK/几何/本地计算，不是纯IK计时。

最终D09墙钟分解约为：执行107.39秒，其中step API80.22秒、API外计算9.22秒，其余为其他API；重放52.02秒，启动/封存/标注等约1秒。主要开销不是规划或人为sleep。原子R2/R4等还需要录制前的物理准备；失败重试也要逐次计入总耗时。

## 轨迹与夹爪

热狗原流程是几何接近段→距把手25mm→多个6mm acquire小段，后两者各自等待收敛；抓取时还有偏慢的夹爪闭合。相机看到的“顿一下”既有调姿/收敛，也有速度低谷，不能全部等同于机械臂速度严格为零。

- faster主食抓取先完整预规划接近与最后25mm路径，使用一个时钟，在25mm处切换接触合同，内部不停车；接触前逐渐减速。几何不可行时保留有原因记录的旧路径回退。
- 空载/携带软速率提高，统一考虑关节、平移及旋转时间；原控制器关节0.7rad/s、命令TCP0.12m/s、实测TCP0.15m/s上限不变。没有放宽成功、食物支撑或倾角门槛。
- faster开门的关节规划步长0.004→0.008rad/帧；关门接触弧保持原值。
- faster主食托盘闭爪每帧目标增量0.0004→0.0008m，仍保留原抓持力、完整闭合目标与控制器0.002m/帧上限。水果与门把手的抓持策略不随此项修改。
- 空夹爪不再无条件发115帧；faster根据实际剩余行程按原0.002m/帧上限生成目标，仍使用guarded_gripper验证手臂静止。夹爪已到目标时不再发空动作串。
- 合并路径回归发现炉内取餐遗漏初始化oven坐标变量，修复后从初态重新采集；失败报告未计入合格数据。

同R5 hotdog seed0、同初态：总时长41.32→31.72秒，抓取接近11.84→7.20秒。接近内部实测近静止（最大关节速度<.01rad/s、连续至少3帧、排除首尾各0.2秒）0.12→0秒；这不是证明整条任务没有必要停顿。1倍速视频位于 `experiments/kitchen_platform_v3/videos_efficiency_20261028/R5_hotdog_1x.mp4`，31.72秒、25fps。

## LeRobot与π0.5

本地阅读 `.cache/upstream/dexjoco` 的转换器、OpenPI输入输出和执行客户端后，保留H5原始数据，增加 `data/lerobot_export.py`。派生格式为16维双FR3/UMI关节/夹爪、25Hz，隔离特权场景状态。粗指令保留整条episode；细指令切分episode，保留粗指令和原轨迹组，避免action chunk跨语义边界。

真实H5的粗/细导出计划已通过；writer协议、关节/夹爪重排、动作前实测状态、帧中动作到规则网格的映射、语言边界和不覆盖源文件均有测试。当前环境没有LeRobot/PyArrow/Torch，未安装新依赖、未运行实际Parquet writer。未来Unity图像对齐、模型padding16→32、动作chunk与推理适配详见 [接口方案](../kitchen_platform_v3_20261020/LEROBOT_PI05_CN.md)。

## 验证与证据

93项测试通过；最后仅补充导出元数据中的粗指令/targets及writer版本记录，相关5项再次通过。原生增量验证覆盖9个不同case/seed：R5两种主食seeds0/1，R1 hotdog seed1，R2 hotdog seed1，C04 hotdog seed1，D09 banana/hotdog seeds0/1。对应合格报告均复核任务成功，最大重放误差0。未完成全部100实例验收。

主要证据在 `experiments/kitchen_platform_v3/collections/`：

- `efficiency_baseline_20261028`：带profiler的旧R5；`.pstats/.txt`在 `experiments/_prof/`。
- `efficiency_runtime_20261028`：相同时序的运行层优化及分项计时。
- `efficiency_motion_pilot_20261028`、`efficiency_rates_20261028`：分轮合并与软速率实验。
- `efficiency_long_fixed_20261028`：修复后的R2和D09 seed1。
- `efficiency_gripper_20261028`：两种主食R5及完整C04 seed1。
- `efficiency_ready_20261028`：最终R5和D09 seed0，含同初态对照JSON。
- `lerobot_coarse_ready_20261028.json`、`lerobot_fine_ready_20261028.json`：真实原始数据的派生映射计划，不是虚构的LeRobot数据集。

源码和批量计划身份已改变，新采集请使用新目录并保留 `--motion-profile faster`。未启动全量200条采集。
