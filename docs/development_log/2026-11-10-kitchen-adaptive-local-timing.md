# 全任务离线高速复核与局部时序修复

2026-11-10，当前分支`feature/data-collection-improve`。用户要求检查其他任务是否存在类似热狗加热的局部失败，并在离线优化时专门处理。未修改模型几何、原数据、任务完成标准或执行器力矩限；不重新做IK或生成空间路径。

## 实测结果

从`all_tasks_1each_optimized_20261106`读取100个任务实例的完整首次备份，每实例检查一个既有初态。候选从同一完整初态执行；选中后重录H5，并独立只用初态和动作重算物理。

| 结果 | 数量 | 处理 |
| --- | ---: | --- |
| 4倍，含按需按钮2倍上限 | 87 | 保留高速普通运动 |
| 香蕉清洗前缀3倍，其余4倍 | 10 | 局部恢复放篮/清洗/重抓动力学 |
| 原速也发生双臂接触 | 3 | 保留源备份，不接纳为新高速数据 |

97条最终H5与标注哈希校验通过，独立重放qpos/qvel最大误差均为0，源H5哈希未变。实际整任务提速2.8979～3.8753倍；其中38条含按钮局部2倍。不是所有任务都严格缩短4倍：清洗、加热和最终稳定计时保留。

证据目录为`experiments/kitchen_platform_v3/collections/adaptive_all100_20261110/`：`summary_completed.json`记录100条检查结果，`verified_results.json`记录97条最终资格、来源和重放校验。该目录是只读复核产物，训练仍须使用正式采集manifest，不递归扫描其trial/backup。

## 局部问题及修复

1. 微波炉按钮沿用先前证据：全局3/4倍没有满足1N且连续0.05s的启动条件；只把按钮接触/撤离限为2倍。C04 hotdog为59.16s，相比整条2倍107.56s明显缩短。
2. 旧L2 banana全局4倍在洗后重抓时丢失水果。只放慢最终重抓，或仅加长其接近段，都不能恢复上游放篮导致的状态差异。按源cleaned标记和技能边界，恢复当前清洗技能前缀3倍、后续仍4倍，得到34.88s，相比整条3倍36.68s更快。同类规则覆盖另外9条香蕉清洗组合。
3. 新采集L2 banana还暴露另一种故障：原93.36s轨迹压缩后，源清洗窗口虽保留，实际有效清洗仍不足。新增失败后才启用的`wash_completion_gate`，在源完成边界保持当前控制、推进真实物理直到实际cleaned成立，最多约3s，否则失败。首次4倍候选26.56s未清洗完成；修复只补0.24s，26.80s通过，未出现携带接触丢失。水龙头无需整体降速。

新鲜数据完整链路证据：`adaptive_banana_verified_20261110/`，summary为qualified1/optimized1/original_fallback0。最终H5独立重放670帧、qpos/qvel误差0，`policy_episode`成功读取670×16的实际本体状态与关节位置/夹爪开度动作；源备份哈希与source_group一致，无图像。较早`adaptive_banana_collection_check_20261110`的原始回退结果保留，不能误称其优化成功。

## 原路径碰撞：时间优化不能解决的情况

| 任务 | 原速接触几何 | 25Hz审计接触力 |
| --- | --- | ---: |
| C06 pear/hotdog | left_fr3_link5 / right_fr3_link6 | 约92.81N |
| D07 pear/burger | left_fr3_link3 / right_fr3_link4 | 约76.89N |
| D07 pear/hotdog | left_fr3_link5 / right_fr3_link6 | 约92.81N |

在原速和局部1/2倍仍触发接触审计，不能把问题归咎于压缩，也不能继续整条降速来掩盖。`adaptive_collision_triage_20261110/`在最新碰撞分流逻辑下重测三条，均由4倍失败加1倍同几何对失败判为`SOURCE_PATH_COLLISION_AT_1X`。正式采集标记`BACKUP_PATH_AUDIT_FAILED`，保留原backup，当前attempt不写最终合格样本，按既有有限重试规则换seed。需要后续修正双臂几何路径；本轮未宣称修复空间碰撞。

## 公共流程和验证边界

`planning/local_timing.py`根据语义事件和实测故障生成局部窗口；`data/adaptive_optimization.py`负责有界候选、理论时长排序、最后全局3/2倍回退和资格封存；正式采集共享`optimized_collection.py`的备份、标注、source_group、恢复校验和manifest，显式选择`--postprocess adaptive`。旧continuous仍为默认。中间试跑只存动作和小报告，选中后才录制完整H5。

全量初次使用4个常驻worker时出现一次原生进程中止，具体原因未确认；保留已封存结果及半成品，用2个spawn worker、一case一子进程恢复完成。正式批处理也采用该隔离方式，并在局部试跑后回收Python循环引用。未删除或覆盖实验结果。

100条复核使用的执行源码快照在`adaptive_all100_20261110/executed_sources/`；随后增加的碰撞分流与清洗门控分别有上述专项验证，不把它们描述为最新源码再跑100条。全部160项单元测试通过，含局部窗口、故障归属、清洗门控候选、原速碰撞排除、备份保留、正式采集适配和恢复。未开展多随机seed统计；25Hz接触采样不构成连续无碰撞证明。运行命令及实验控制限制见[使用说明](../kitchen_platform_v3_20261020/ADAPTIVE_OFFLINE_TIMING_CN.md)。
