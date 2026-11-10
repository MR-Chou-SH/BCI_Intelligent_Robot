# 梨组合的双臂交接路径修复

2026-11-10，分支`feature/data-collection-improve`。此前100条旧轨迹中，C06 pear/hotdog、D07 pear/burger、D07 pear/hotdog在原速也有双臂接触，离线降速不能修复，用户要求修改并检查所有任务采集。

## 路径修改

- `planning/motion.py::clear_left_corridor`原先只检查夹爪越过餐盘左边缘。洗梨后夹爪虽在x≈-0.409m，左link3的几何右边缘仍可到x≈+0.054m，肘部占据右臂装盘通道。现在同时检查整条左FR3和UMI相对餐盘坐标的包围范围，要求最右边缘退到中心线左侧60mm，并把局部退让候选扩展到480mm。执行后再检查实测边缘（允许5mm跟踪差）；回home的几何回退也必须满足该条件，否则明确失败。没有放宽碰撞阈值。
- 第一次修复后热狗两例成功，但梨/汉堡送盘暴露B1的另一个问题：第一段双臂同时做关节插值，可能选中前臂互相穿过的IK分支；实测left_link4/right_link4接触力最高约332.5N，最终表现为跟踪超时。`expert.py::deliver`现在先以已有`free_pose`几何路径撤右臂，再接近左臂到首个托盘上方预备位；检查完整场景和停放臂，随后保持原双臂抓盘/搬运流程。

两处修改共用于组合交接和B1，不按梨的名字硬编码轨迹，不改主食随机位置或物理成功标准。原始数据和上轮失败证据保留。

全目录验收进一步发现D02（洗水果后直接送盘）不需要右臂装主食前的退让。`tasks/execution.py`现在只在下一技能不是B1时做该退让；直接送盘由B1自身的几何接近路径处理。D01/D02全部8个实例已专项重采，不靠换seed避开问题。新增回归覆盖此分支。

## 专项验收

`experiments/kitchen_platform_v3/collections/pear_corridor_checked_delivery_20261110/`使用research初态seed0、faster专家、adaptive离线优化、每任务一次尝试，三个任务均首次成功：

| 任务 | 首次专家仿真时长 | 最终仿真时长 | 独立重放qpos/qvel误差 |
| --- | ---: | ---: | --- |
| C06 pear/hotdog | 114.68s | 33.92s | 0 / 0 |
| D07 pear/burger | 160.24s | 46.88s | 0 / 0 |
| D07 pear/hotdog | 171.72s | 49.80s | 0 / 0 |

三条均OPTIMIZED；按实际cleaned标记补足必要清洗时间，未整体降速，未将失败候选或原速碰撞备份收入新数据。160项回归测试通过。

## 全目录验收

当前代码完整100实例采集验收目录：`experiments/kitchen_platform_v3/collections/all100_corridor_verified_20261110/`。使用全部任务、每任务一条、research随机化、最多3次有限seed尝试、4个spawn子进程，命令：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -B \
  -m integration.kitchen.data.collect_dataset \
  --output experiments/kitchen_platform_v3/collections/all100_corridor_verified_20261110 \
  --suite all --samples-per-case 1 --max-attempts 3 --jobs 4 \
  --motion-profile faster --postprocess adaptive
```

最终manifest/summary为验收依据。该检查只能证明已测试初态的任务覆盖，不能保证无限随机初态均成功；失败样本必须继续被排除，保留备份和原因。

### 最终验收结果

第一轮完整100实例检查得到96个合格任务，D02四例被退让后检查挡住；失败记录和有限seed重试全部保留。修正直接送盘分支后，在`direct_delivery_corridor_verified_20261110/`重采所有受影响的D01/D02共8例，8例均在seed0首次成功，全部OPTIMIZED，summary complete=true。两轮间仅`tasks/execution.py`变化，其他92个实例的执行分支不变。第一轮目录因此仍是96/100的历史批次，不改写为完成，也不绕过其源码身份恢复检查。

最终去重覆盖为**100/100任务、全部seed0**：取第一轮不属于D01/D02的92条和第二轮8条；84条成功优化，16条高速候选失败而回退到合格首次专家。回退主要涉及多设备长链的候选条件，不把“成功采集”误称为“全部加速成功”。

`experiments/kitchen_platform_v3/collections/pear_corridor_coverage_20261110/coverage.json`是两轮去重验收索引，记录每个任务的来源、报告/H5哈希、时长和优化状态；**它不是伪造的单一源码新批次manifest**。全部100条核验报告、轨迹、英文标注、备份哈希和各自批次源码身份一致，H5完整初态与首次备份完全一致，独立重放qpos/qvel误差均为0，训练接口可读取25Hz、16维本体状态和动作。最终161项回归测试通过。

今后采集使用当前代码和新的输出目录，沿用上方命令；不要对第一轮旧源码目录强行`--resume`。任意随机初态仍需逐条成功验收和有限重试，本轮没有证明所有可能初态必然成功。
