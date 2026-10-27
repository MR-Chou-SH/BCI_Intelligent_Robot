# Kitchen 模块整理与精简策略轨迹

2026-10-27，分支 `feature/kitchen-linux-task-collection`，开始时 HEAD `50b1ea4c70f7a755943fe49123a5096e0ada166a`、工作区干净。保留当前分支；无切换、合并、提交或推送。用户授权清理无用中间文件，并要求记录 joint pos、夹爪开度、初态与关键状态，可动作重放、可重新判定成功。用户随后要求直接删除仅转发导入的旧入口。

## 实现

- 上层按 `tasks/`、`planning/`、`validation/`、`runtime/`、`data/`、`tools/` 拆分。删除13个旧转发入口，同步所有当前代码、测试、脚本和操作指南的引用。历史开发日志保留当时路径。公开服务和 V1 冻结任务 API 保留原位置。
- `robot_arm/kitchen/` 继续拥有模型、随机初态、对象查询、IK与低层控制，模型/控制算法没有重建。模块边界见两处 README。
- V3 只记录25Hz名义关节目标与夹爪目标开度，关键状态与一次完整初态；不再默认记录500Hz带补偿执行器数组。夹爪开度为比例0关1开，实际间距在状态中以m记录。
- 早期仅开度反算腱长时，苹果重放出现约2.37e-4速度偏差，香蕉长接触放大到约3.54。保留每动作两个原生夹爪目标值后，四个实际任务重算qpos/qvel误差均为0。这个字段用于无损重放，不是检查日志。首步缓存偏置、控制器插值和设备计时也必须保留。
- 在线执行与离线重放共享 `validation/success.py`。离线重新验证原子初态、技能时间线、边界支撑/稳定窗口、清洗/加热和最终子目标。CLI即使物理重算通过，任务失败仍返回非零。
- 采集完全不导入渲染工具。合格样本目录只有 `trajectory.h5`、小型 `report.json`；新失败尝试只保留报告。计划、索引、调度凭据仍用于续跑和校验，不进入动作训练数组。详细诊断需显式启用。

## 验证

`OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -B -m unittest discover -s integration/kitchen/tests`：**56项通过**。包括移动目标、帧中hold、非零初始tick、末帧、初始设备标记、损坏文件、夹爪反算一致性、任务假标签不能冒充成功、CLI失败退出码及续跑。两个shell入口通过 `bash -n`，`git diff --check` 通过。

原生MuJoCo增量验证（各轮保留自己的源码身份，不宣称是一次全量冻结验收）：

| 用例 | seed | 动作时长 | HDF5大小 | 任务/动作重放 |
| --- | ---: | ---: | ---: | --- |
| L1 apple | 1 | 34.88s | 1.106MiB | 成功、qpos/qvel最大误差0 |
| L2 banana | 1 | 100.68s | 3.190MiB | 成功、误差0 |
| R4 burger | 0 | 97.08s | 2.797MiB | 成功、误差0 |
| C08 pear/hotdog | 0 | 648.40s | 19.096MiB | 成功、误差0 |

四例在模块入口删除后亦通过独立重验，索引为 `experiments/kitchen_platform_v3/collections/reorganized_replay_acceptance_20261027.json`。这些保存了完整初态与任务边界，可在匹配环境下独立检验，不依靠视频。

最终源码下运行 `policy_dataset_final_20261027`：L1 apple seed0/1两条合格，`summary.json` complete=true，轨迹各约1.1MiB。相同命令 `--resume` 返回2/2，未重新执行仿真。全任务 `--plan-only` 核实64实例、128目标；**本次没有实采全部128条**。

## 清理

清单先写入 `experiments/kitchen_platform_v3/collections/cleanup_20261027.json`，再仅删除明确标记FAILED_ATTEMPT的生成数据/截图和临时渲染、开发checkpoint：**403文件、1,663,306,372字节**。保留当时全部229份合格报告与轨迹，并核对报告SHA未变。失败报告保留，历史报告中被清理的诊断路径不再可用；不改封存报告冒充文件仍存在。另清理两处Python缓存和320字节MuJoCo生成日志，并忽略今后生成的该日志。

成功研究轨迹、用户暂停的未封存尝试、`robot_arm/outputs/`、`reference/`及EEG原始数据均未删除；不移动历史成功目录，避免破坏绝对路径与证据关联。新数据、视频和计划仍被Git忽略。测试过程日志在核对后删除，保留本记录及小型验收索引。

无VR、图像、Unity渲染或真实硬件验证；现有Linux虚拟环境未安装或升级依赖。Unity后续可从记录的物体位姿/qpos渲染。完整字段、采集命令与兼容边界见[格式说明](../kitchen_platform_v3_20261020/DATASET_FORMAT_CN.md)、[采集说明](../kitchen_platform_v3_20261020/DATASET_COLLECTION_CN.md)及[ADR-005](../decisions/ADR-005-kitchen-policy-trajectory-format.md)。
