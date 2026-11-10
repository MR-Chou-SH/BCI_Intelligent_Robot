# 按失败区间进行高速离线优化

2026-11-10。处理对象是首次专家已规划并执行成功的V3动作轨迹；不调用IK或重新规划。原始backup永久保留，候选从同一完整初态执行，成功后重新录制并独立重放。新入口显式使用`--postprocess adaptive`，原`continuous`仍保留用于历史流程复现。

## 处理策略

1. 初始候选使用4倍运动时序，以及已验证的微波炉按钮接触/撤离2倍局部上限；其他普通段仍4倍。
2. 保持原设备计时及最终成功窗口：清洗2s、加热5s，门角/装盘/送盘保持时间均不降低。
3. 实测失败记录回原source tick，同时只保存少量关键证据：首次携带接触丢失、按钮位移/力/连续有效时长、设备事件、送盘倾角峰值时刻，以及失败时物体状态。
4. 首先试局部候选：清洗标记不足时，只在实际完成前补有界等待；洗后重抓失败时调整重抓和初始抬起（含短接近段），必要时向上游扩展到清洗技能前缀。其他故障按已测接触丢失/倾角/门接触区间定位。
5. 保留原路径、全部事件端点和非故障区间的高速。按候选计划时长排序；已有成功候选比后续候选的理论最短时间还短时，不再浪费试跑。
6. 局部候选都不通过，才试整条3倍/2倍。普通优化失败保留原合格backup并明确标记`ORIGINAL_FALLBACK`，不把失败高速轨迹混入最终manifest。例外是碰撞：若4倍和原速审计均在相同几何对上失败，标记`SOURCE_PATH_COLLISION_AT_1X`，该attempt不进入新manifest，采集器按既有上限换seed重试；原始backup仍保留。

这不会使不同任务总时长相同。动作链更长、包含固定物理计时的任务仍更长；目标是避免单个接触段失败导致整条无谓降速。

## 已验证的局部问题

- 微波炉按钮：整体3倍有效按压仅0.04s，4倍接触力峰值0.985N，均未满足当前仿真1N/0.05s条件。只把按钮相关接触段限为2倍，C04 hotdog由整条2倍107.56s降为59.16s。
- 香蕉清洗：仅把洗后重抓局部放慢仍会丢失水果；必须向前恢复与放篮/清洗有关的时序。本轮识别出的清洗前缀限为3倍、后续仍4倍，L2 banana为34.88s，优于整条3倍36.68s。该规则按源cleaned标记与技能事件找窗口，没有硬编码具体tick或任务编号。
- 新采集的香蕉轨迹还暴露了另一种情况：保留源轨迹的清洗窗口后，实际在水下的有效时间仍不足。因此失败后启用`wash_completion_gate`，在源清洗完成边界保持当前动作、继续真实物理计时，直到实际cleaned标记成立（最多约3s，否则失败）。新L2 banana原始93.36s，4倍候选只补0.24s后在26.80s成功，独立重放误差为0；不修改物体状态或伪造完成标记。
- 双臂接触：部分原始规划本身在原速也发生双臂接触，不能靠局部降速或取消加热/稳定要求宣称已修复。高速审计失败应显式保留，后续需要几何路径修正。

完整检查已有100个任务实例的各一条轨迹：97条通过重新录制及独立重放，实际整任务提速2.90～3.88倍；其中10条香蕉清洗链需要清洗前缀局部3倍，38条含按钮局部2倍。剩余C06 pear/hotdog、D07 pear/burger、D07 pear/hotdog在原速也触发双臂接触审计，未接纳为新数据。详见[全任务验证记录](../development_log/2026-11-10-kitchen-adaptive-local-timing.md)。这些是固定初态的证据，不能推断任意随机初态成功；审计采样为25Hz，也不是连续无碰撞证明。

随后已修正上述梨组合的规划衔接：退让检查包含整条左臂，送盘首段采用完整场景检查的先右后左路径。三个原失败初态均重新采集、优化及独立重放成功；旧轨迹不原地修改，使用新代码重新采集。见[路径修复与全目录验收](../development_log/2026-11-10-kitchen-pear-corridor-repair.md)。

## 采集新数据

```bash
bash scripts/collect_kitchen_dataset.sh \
  --output experiments/kitchen_platform_v3/collections/adaptive_collection_new \
  --suite all --samples-per-case 1 --jobs 2 \
  --profile research --motion-profile faster --postprocess adaptive
```

adaptive初始倍率固定4，局部倍率由候选生成器选择；不用手动列`--optimization-factors`。若明确填写，只接受`--optimization-factors 4`，避免把旧`continuous`的3/5请求误用于这里。

批处理使用spawn，每个worker子进程只处理一个case，结束后释放本地仿真资源；建议先用`--jobs 2`。

`--resume`继续使用原批次source identity、备份和已封存组件；部分试跑目录不覆盖。代码改动后使用新批次目录，不能强行更改旧计划身份。

## 优化已有backup

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -B \
  -m integration.kitchen.data.adaptive_optimization \
  --backup-root experiments/kitchen_platform_v3/collections/all_tasks_1each_optimized_20261106 \
  --output experiments/kitchen_platform_v3/collections/adaptive_review_new \
  --jobs 2
```

可加`--case-ids kitchen_expert_v3:L2:banana kitchen_expert_v3:C04:hotdog`限定任务。该入口检查已有轨迹，不生成新的专家规划；每个已封存`qualified.json`绑定H5、英文标注和独立重放结果。正式采集仍只以采集批次最终manifest为训练来源，不能递归收集trial/backup目录。

## 文件位置与边界

- `planning/local_timing.py`：从原事件/状态和实测失败生成语义局部窗口。
- `data/speed_stress.py`：带source-clock映射的时间缩放、私有控制器实验限制、物理执行及稀疏诊断。
- `data/adaptive_optimization.py`：候选排序、局部重试、最后全局回退、独立重放与正式资格封存。
- `data/optimized_collection.py`：与现有backup/最终选择/恢复校验共用。

中间候选仍只有动作与小报告，H5只用于选中轨迹的最终录制。图像不写入H5。实验控制参数与候选局部窗口会写入H5 metadata和报告，500Hz物理、25Hz控制、原模型和执行器力矩限保持。
