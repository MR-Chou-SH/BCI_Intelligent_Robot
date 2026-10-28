# 去重任务与送餐组合方案（Expert V3）

2026-10-27，用户确认记录本方案并提供采集入口。任务命名空间为 `kitchen_expert_v3`；数据文件格式仍为 `kitchen_state_actions_v3`，两者版本含义不同。

共 **22类、100个具体实例**：20个原子实例、34个装盘组合实例、46个送餐组合实例。每实例两条合格数据，目标 **200条**。水果为苹果、梨、香蕉、西红柿，主食为汉堡、热狗；seed不作为任务实例另计。

旧C01=L1、C02=L2、C03=R5，取消重复采集，保留C04–C08编号。新增D01–D09显式包含B1。R3名称只包含一次主食名；炉内主食类型作为初态字段保留，不再拼出hotdog_hotdog。门仅开100度或关闭，开关条件由实测状态决定，不另复制任务编号。

H表示状态驱动的加热装盘流程：需要时R3开门→R1装炉关门→R4加热等待开门→R2取出装盘关门，跳过已满足步骤。默认完整H任务从主食在炉外开始。D03从主食已在炉内开始，不要求本任务执行加热，因此与D05不同。

| 类型 | 编号 | 任务 | 技能链/过程 | 初始条件 | 实例数 |
| --- | --- | --- | --- | --- | ---: |
| 原子 | L1 | 水果直接装盘 | 抓水果→餐盘 | 水果在左侧 | 4 |
| 原子 | L2 | 清洗水果并装盘 | 清洗篮→开水→清洗→关水→取出装盘 | 水果在左侧 | 4 |
| 原子 | R1 | 主食装炉并关门 | 抓主食托盘→入炉→关门 | 门开、主食在炉外 | 2 |
| 原子 | R2 | 从炉内取餐并关门 | 取餐→装盘→关门 | 门开、主食在炉内 | 2 |
| 原子 | R3 | 开门 | 打开微波炉门 | 门关；炉内空/汉堡/热狗 | 3 |
| 原子 | R4 | 加热并开门 | 启动→等待加热完成→开门 | 门关、主食在炉内 | 2 |
| 原子 | R5 | 主食直接装盘 | 抓主食托盘→餐盘 | 主食在炉外 | 2 |
| 原子 | B1 | 双臂送餐盘 | 双臂抓盘→搬运→用户桌→松手 | 备餐区空餐盘 | 1 |
| 装盘组合 | C04 | 主食加热装盘 | H | 主食在炉外 | 2 |
| 装盘组合 | C05 | 水果与主食直接装盘 | L1→R5 | 水果/主食在备餐区 | 8 |
| 装盘组合 | C06 | 清洗水果与主食直接装盘 | L2→R5 | 同上 | 8 |
| 装盘组合 | C07 | 水果与加热主食装盘 | L1→H | 同上 | 8 |
| 装盘组合 | C08 | 清洗水果与加热主食装盘 | L2→H | 同上 | 8 |
| 送餐组合 | D01 | 水果直接装盘并送餐 | L1→B1 | 水果在左侧 | 4 |
| 送餐组合 | D02 | 清洗水果装盘并送餐 | L2→B1 | 水果在左侧 | 4 |
| 送餐组合 | D03 | 从炉内取餐装盘并送餐 | R2→B1 | 门开、主食已在炉内 | 2 |
| 送餐组合 | D04 | 主食直接装盘并送餐 | R5→B1 | 主食在炉外 | 2 |
| 送餐组合 | D05 | 主食加热装盘并送餐 | H→B1 | 主食在炉外 | 2 |
| 送餐组合 | D06 | 水果与主食直接装盘并送餐 | L1→R5→B1 | 水果/主食在备餐区 | 8 |
| 送餐组合 | D07 | 清洗水果与主食装盘并送餐 | L2→R5→B1 | 同上 | 8 |
| 送餐组合 | D08 | 水果与加热主食装盘并送餐 | L1→H→B1 | 同上 | 8 |
| 送餐组合 | D09 | 清洗水果与加热主食装盘并送餐 | L2→H→B1 | 同上 | 8 |
| 合计 | 22类 | 8类原子+14类组合 | | | 100 |

## 英文VLA提示词与两层标注（2026-10-28设计）

语言固定为英语，区分 serving tray（最终大餐盘）、food carrier（主食下面的小盘）和 microwave。`{fruit}`、`{food}` 是目标槽位：在线由EEG选择结果绑定对象ID；当前离线专家采集由case中的fruit/meal绑定，不能声称采集时实际使用了脑电。banana等对象ID保持机器可读；英文显示使用apple、pear、banana、tomato、hamburger、hot dog。提示词不包含速度、轨迹实现、检查状态或随机种子。

| 技能 | 标准英文提示词 |
| --- | --- |
| L1 | Place the {fruit} directly on the serving tray. |
| L2 | Wash the {fruit} and place it on the serving tray. |
| R1 | Put the {food} in the microwave and close the door. |
| R2 | Take the {food} out of the microwave, place it on the serving tray, and close the door. |
| R3 | Open the microwave door. |
| R4 | Heat the {food} in the microwave, wait until it is ready, and open the door. |
| R5 | Place the {food} directly on the serving tray. |
| B1 | Deliver the serving tray to the user with both arms. |
| H（组合内的完整主食阶段） | Heat the {food} and place it on the serving tray. |

原子任务的粗、细提示词相同，整条数据只有一个细区间。组合任务按上表既有技能链生成粗提示词，使用Then/Finally表达先后；H的R3/R1/R4/R2操作合成一个“加热并装盘”细阶段，沿用C04的整体提示词，不拆成与用户目标不一致的按钮/开门片段。H内部真实原子操作仍保留原有HDF5 skill timeline供成功检验，阶段语义不会取代物理检验。

例如D09 banana/hotdog：粗提示词为 `Wash the banana and place it on the serving tray. Then, heat the hot dog and place it on the serving tray. Finally, deliver the serving tray to the user with both arms.`；细阶段分别使用L2、H、B1的标准提示词。D06对应L1、R5、B1；D03对应R2、B1。语义阶段覆盖100个case，保持既有去重任务编号不变。

每条成功轨迹旁保存 `annotations.json`，schema为 `kitchen_language_annotations_v1`。包含case_id、language、targets（fruit/food对象ID）、coarse_instruction、num_steps、step_indexing，以及fine_segments（skill、instruction、start_step、end_step）。同时绑定trajectory SHA256；报告与manifest引用标注文件及哈希。没有图像和逐步检查记录。

**step是 `actions/joint_pos` / `actions/gripper_opening` 的0起始行号，区间为 `[start_step,end_step)`，end_step不包含在该段内。** 不能拿500Hz物理tick或关键状态数组行号充当训练step。边界从实际执行阶段时间和actions/tick映射计算，最后一段end_step等于动作行数N。阶段连续覆盖 `[0,N)`，不得重叠或留空；水果放盘后的局部撤手属于水果阶段，最后必要的稳定等待属于最后阶段。原子与组合采用同一映射规则。

新采集保存语义阶段的实际起点，H包含条件性开关门等真实执行时间。旧文件如有完整原子技能时间线，可以由实际技能链重建标注并写入新输出目录，保留原始HDF5、报告及索引不变；不通过手写step或视频目测推定边界。未完成或失败轨迹不生成训练标注，标注本身也不能替代任务成功与独立动作重放。

导出现有合格轨迹的标注示例：

```bash
.venv/bin/python -B -m integration.kitchen.data.annotations \
  /绝对路径/trajectory.h5 --output /新目录/annotations.json
```

新采集自动生成标注；旧文件导出需要同目录原合格报告及匹配轨迹哈希。历史文件的边界按已有原子时间线重建；若历史数据曾在原子时间线之外执行条件操作，其精确语义起点不能凭空恢复，应使用新采集的语义起点。已有D09 banana/hotdog示例位于 `experiments/kitchen_platform_v3/language_examples_20261028/D09_banana_hotdog.json`。

## 物理成功与数据

2026-10-28物理调整：龙头把手胶囊半径12→16mm（直径24→32mm），滑动/扭转/滚动摩擦为1.6/0.02/0.002，保持60g质量和设备ID。香蕉与主食组合时，在受控抓持下把香蕉长轴转向餐盘局部Y轴；槽位沿餐盘局部X轴分配，避免用世界坐标固定偏移。若抓起后仍轻擦邻物，先小幅抬起清场；转向使用至少8N指垫目标力，物理接触上限不变。香蕉在清洗篮内可能翻面，因此保留当前正反面，只对齐长轴，不强迫手腕翻转180度。释放已分离后不再追加会越出工作空间的抬高动作。新的成功轨迹仍须通过相同支撑、完整包络、无接触释放和送餐保留食物检验。

装盘任务检查指定食物稳定承托、完整包络在餐盘内、手指分离；L2还要求清洗标记，H还要求加热标记。送餐组合同时要求餐盘在用户桌面稳定支撑、四指垫完成实际抓取、倾角不超过10度，以及所有指定食物仍在盘内、主食仍由自己的小盘承托。不能以“餐盘送达但食物掉落”计成功。

带餐B1从实测支撑关系构造餐盘→水果/主食小盘→主食的载荷链，纳入接触检查；不焊接或瞬移食物。带餐每指垫目标法向力至少8N，空盘仍4N；这是规划动作参数，接触与成功阈值保持原值。默认数据只有初态、25Hz目标joint_pos/夹爪开度及关键状态，按动作独立重仿真并复核任务成功，不自动渲染。

## 数据位置与新批次命令

旧批次保留：`experiments/kitchen_platform_v3/collections/all_tasks_policy_20261027/`。用户暂停时已有64条合格报告；旧目标128条，新目标200条，不能把两个批次混成一个完成率。不要修改旧plan/报告来续跑新方案。旧V2任务定义保留给历史文件重放。

从仓库根目录执行：

```bash
bash scripts/collect_kitchen_dataset.sh \
  --output experiments/kitchen_platform_v3/collections/all_tasks_delivery_v3_20261027 \
  --samples-per-case 2 --jobs 2
```

中断后原命令加 `--resume`。先查看计划可加 `--plan-only`，之后同参数加 `--resume` 执行。只采新送餐组合可通过 `--case-ids` 选择具体实例，但完整100实例使用上述默认all。

需要尝试轨迹提速时加 `--motion-profile faster` 并使用新的批次目录；默认仍为 `standard`。提速档与实测对照见[采集参数](DATASET_COLLECTION_CN.md)和[提速记录](../development_log/2026-10-27-kitchen-motion-speed.md)。

每条成功数据位于 `attempts/<case_id>/sample_XX/attempt_XX_seedN/trajectory.h5`，同目录为 `report.json`；失败只留小报告。批次完成后生成manifest/summary，运行中先看各report；未来是否重用旧数据需要显式身份与成功校验，不会自动把旧样本计入新批。

旧批次64条的实测平均墙钟时间：L1约12s，L2约39s，R5约48s，完整主食加热装盘约230s，空盘B1约30s（含独立重放的单次worker耗时）。据各类数量累加约8–9 worker小时，两个进程理想约4–4.5小时；考虑带餐衔接和调度，建议预留 **4–6小时**，重试或其他CPU负载会延长。不是全部200条的实测承诺。

[采集参数](DATASET_COLLECTION_CN.md) · [字段与重放](DATASET_FORMAT_CN.md)

当前验证：61项测试通过；D04汉堡、D06苹果/热狗research seed0实际采集及动作重放均通过，重放最大误差0。D01苹果也有此前增量通过记录。尚未完成全部100实例的原生物理验收，采集中的失败会重试并列入缺失项。
