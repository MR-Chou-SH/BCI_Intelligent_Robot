# Expert V3 正式采集大纲（草案）

2026-11-14，分支`feature/data-collection-improve`（`0978010`）。用于训练听指令的VLA：同一批数据既可按**粗指令**（整条任务一句话）导出，也可按**细指令**（每个阶段一句话）导出，不需要分开采。条数待用户确认后执行。

## 分配原则

- **按任务类型平衡**：每类原子任务60条，每类长程任务至少50条；同一类内每个实例条数相同（向上取整），例如R1有2个实例、每实例30条，C05有8个实例、每实例7条共56条，D01有4个实例、每实例13条共52条。这样每句指令模板的样本量接近，不会因为某类实例多就占大头。
- **原子多于长程（按类）**：原子任务短、是长程任务的组成技能；长程任务的每个阶段也会贡献细指令样本，见第二张表。
- **每条样本一个独立随机初态**（`seed_scheme: per_sample`），1232条即1232种初始摆放；失败自动换种子重试。
- 平均时长、采集耗时取自同一代码下全目录seed 0与seed 1两批的实测（research初态、高速直采）。

## 采集大表

| 类型 | 编号 | 任务 | 实例（变量） | 每实例 | 条数 | 平均时长 | 数据时长 | 粗指令（整条，一句） | 细指令（按阶段） |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| 原子 | L1 | 抓水果直接放餐盘 | 4：4 种水果 | 15 | 60 | 16 s | 0.27 h | Place the {fruit} directly on the serving tray. | 同粗指令（单阶段 L1） |
| 原子 | L2 | 抓水果→清洗→放餐盘 | 4：4 种水果 | 15 | 60 | 41 s | 0.69 h | Wash the {fruit} and place it on the serving tray. | 同粗指令（单阶段 L2） |
| 原子 | R1 | 抓主食→放微波炉→关门 | 2：2 种主食 | 30 | 60 | 19 s | 0.32 h | Put the {food} in the microwave and close the door. | 同粗指令（单阶段 R1） |
| 原子 | R2 | 从微波炉取主食→放餐盘→关门 | 2：2 种主食 | 30 | 60 | 22 s | 0.37 h | Take the {food} out of the microwave, place it on the serving tray, and close the door. | 同粗指令（单阶段 R2） |
| 原子 | R3 | 微波炉关闭→直接开门 | 3：炉内 空/汉堡/热狗 | 20 | 60 | 12 s | 0.20 h | Open the microwave door. | 同粗指令（单阶段 R3） |
| 原子 | R4 | 启动加热→等待→开门 | 2：2 种主食 | 30 | 60 | 23 s | 0.38 h | Heat the {food} in the microwave, wait until it is ready, and open the door. | 同粗指令（单阶段 R4） |
| 原子 | R5 | 抓主食直接放餐盘 | 2：2 种主食 | 30 | 60 | 11 s | 0.18 h | Place the {food} directly on the serving tray. | 同粗指令（单阶段 R5） |
| 原子 | B1 | 双臂送餐盘（空盘/带负载） | 15：15 种托盘负载 | 4 | 60 | 23 s | 0.38 h | Deliver the serving tray to the user with both arms. | 同粗指令（单阶段 B1） |
| 装盘组合 | C04 | 主食加热装盘 | 2：2 种主食 | 25 | 50 | 69 s | 0.96 h | Heat the {food} and place it on the serving tray. | 同粗指令（单阶段 H） |
| 装盘组合 | C05 | 水果与主食直接装盘 | 8：4 水果 × 2 主食 | 7 | 56 | 30 s | 0.46 h | Place the {fruit} directly on the serving tray. Then, place the {food} directly on the serving tray. | L1 → R5 |
| 装盘组合 | C06 | 清洗水果与主食直接装盘 | 8：4 水果 × 2 主食 | 7 | 56 | 54 s | 0.84 h | Wash the {fruit} and place it on the serving tray. Then, place the {food} directly on the serving tray. | L2 → R5 |
| 装盘组合 | C07 | 水果直接装盘与加热主食 | 8：4 水果 × 2 主食 | 7 | 56 | 86 s | 1.33 h | Place the {fruit} directly on the serving tray. Then, heat the {food} and place it on the serving tray. | L1 → H |
| 装盘组合 | C08 | 清洗水果与加热主食 | 8：4 水果 × 2 主食 | 7 | 56 | 112 s | 1.74 h | Wash the {fruit} and place it on the serving tray. Then, heat the {food} and place it on the serving tray. | L2 → H |
| 送餐组合 | D01 | 水果直接装盘并送餐 | 4：4 种水果 | 13 | 52 | 41 s | 0.59 h | Place the {fruit} directly on the serving tray. Then, deliver the serving tray to the user with both arms. | L1 → B1 |
| 送餐组合 | D02 | 清洗水果装盘并送餐 | 4：4 种水果 | 13 | 52 | 66 s | 0.95 h | Wash the {fruit} and place it on the serving tray. Then, deliver the serving tray to the user with both arms. | L2 → B1 |
| 送餐组合 | D03 | 从炉内取餐装盘并送餐 | 2：2 种主食 | 25 | 50 | 47 s | 0.66 h | Take the {food} out of the microwave, place it on the serving tray, and close the door. Then, deliver the serving tray to the user with both arms. | R2 → B1 |
| 送餐组合 | D04 | 主食直接装盘并送餐 | 2：2 种主食 | 25 | 50 | 35 s | 0.49 h | Place the {food} directly on the serving tray. Then, deliver the serving tray to the user with both arms. | R5 → B1 |
| 送餐组合 | D05 | 主食加热装盘并送餐 | 2：2 种主食 | 25 | 50 | 94 s | 1.31 h | Heat the {food} and place it on the serving tray. Then, deliver the serving tray to the user with both arms. | H → B1 |
| 送餐组合 | D06 | 水果与主食直接装盘并送餐 | 8：4 水果 × 2 主食 | 7 | 56 | 53 s | 0.83 h | Place the {fruit} directly on the serving tray. Then, place the {food} directly on the serving tray. Finally, deliver the serving tray to the user with both arms. | L1 → R5 → B1 |
| 送餐组合 | D07 | 清洗水果与主食直接装盘并送餐 | 8：4 水果 × 2 主食 | 7 | 56 | 80 s | 1.24 h | Wash the {fruit} and place it on the serving tray. Then, place the {food} directly on the serving tray. Finally, deliver the serving tray to the user with both arms. | L2 → R5 → B1 |
| 送餐组合 | D08 | 水果与加热主食装盘并送餐 | 8：4 水果 × 2 主食 | 7 | 56 | 112 s | 1.74 h | Place the {fruit} directly on the serving tray. Then, heat the {food} and place it on the serving tray. Finally, deliver the serving tray to the user with both arms. | L1 → H → B1 |
| 送餐组合 | D09 | 清洗水果与加热主食装盘并送餐 | 8：4 水果 × 2 主食 | 7 | 56 | 138 s | 2.15 h | Wash the {fruit} and place it on the serving tray. Then, heat the {food} and place it on the serving tray. Finally, deliver the serving tray to the user with both arms. | L2 → H → B1 |
| **原子合计** | 8 类 | | 34 | | **480** | | **2.78 h** | | |
| **长程合计** | 14 类 | | 80 | | **752** | | **15.27 h** | | |
| **总计** | 22 类 | | 114 | | **1232** | | **18.06 h** | | |

注：
- {fruit}为apple/pear/banana/tomato，{food}为hamburger/hot dog（对象ID分别为burger/hotdog）。B1的托盘负载、R3的炉内状态是可见的初始状态，不写进指令。
- 原子任务（以及C04）只有一个阶段，粗指令＝细指令。长程任务的粗指令由各阶段细指令用 Then/Finally 串成。
- H（加热装盘）是一个细阶段，内部按实测设备状态执行开门R3→装炉R1→加热开门R4→取出装盘R2（已满足的步骤跳过），不拆成R1–R4的细指令。
- 阶段边界：水果放好并稳定即结束水果阶段；随后左臂让位属于下一阶段（R5或H）；最后的稳定等待属于最后阶段。

## 细指令覆盖

| 阶段代号 | 细指令模板 | 出现在 | 阶段条数 | 阶段时长 |
| --- | --- | --- | ---: | ---: |
| L1 | Place the {fruit} directly on the serving tray. | L1、C05、C07、D01、D06、D08 | 336 | 1.48 h |
| L2 | Wash the {fruit} and place it on the serving tray. | L2、C06、C08、D02、D07、D09 | 336 | 3.84 h |
| R5 | Place the {food} directly on the serving tray. | R5、C05、C06、D04、D06、D07 | 334 | 1.13 h |
| H | Heat the {food} and place it on the serving tray. | C04、C07、C08、D05、D08、D09 | 324 | 6.29 h |
| R1 | Put the {food} in the microwave and close the door. | R1 | 60 | 0.32 h |
| R2 | Take the {food} out of the microwave, place it on the serving tray, and close the door. | R2、D03 | 110 | 0.68 h |
| R3 | Open the microwave door. | R3 | 60 | 0.20 h |
| R4 | Heat the {food} in the microwave, wait until it is ready, and open the door. | R4 | 60 | 0.38 h |
| B1 | Deliver the serving tray to the user with both arms. | B1、D01–D09 | 538 | 3.74 h |

物体均衡：每种水果出现在180条样本中，每种主食出现在484条样本中（含B1负载与R3炉内状态）。

## 总量与资源

| 项目 | 数值 |
| --- | --- |
| 样本条数 | 1232（原子480＋长程752） |
| 演示时长 | 18.06 h（原子2.78 h＋长程15.27 h） |
| 训练帧数（25 Hz） | 约163万帧；三路相机共约488万张图 |
| 采集耗时 | 约70分钟（6进程；Isaac Sim同时运行时约1.5小时），含重试 |
| H5存储 | 约4.1 GB（直采每条保存直采与最终两份H5） |
| 图像（三路PNG） | 约146 GB——本机剩余43 GB，**不可行** |
| 图像（H.264视频） | 约2.2 GB（实测PNG/视频约68:1） |

## 执行前要准备

1. **按任务设置条数**：当前采集命令对所有实例用同一个`--samples-per-case`。确认本大纲后，增加按任务指定条数的选项（如`--samples-by-task L1=15,R1=30,...`），整批共用一个计划和一套不重复的种子（首次尝试0–1231）。
2. **评测种子另留**：VLA评测使用100000以上的种子，与训练初态不重叠。
3. **图像导出改为视频**：渲染与LeRobot导出须改用视频格式，或在笔记本上完成。采集本身不生成图像，H5可随时重新渲染。
4. **推理限速同步**：高速直采数据需要在放宽后的限速下复现，评估VLA前必须同步`vla/environment.py`。
5. **香蕉**：约1/12的初态规划失败，自动换种子重试，已计入时间估计；这些初态在数据中缺失。

## 模型侧：OpenPI能否处理长程任务（2026-11-14分析）

依据本机OpenPI源码（`~/Projects/openpi`，`b6ed826`）与缓存的PaliGemma分词器实测：

- **任务长度不进入模型上下文**。π0/π0.5每次只看当前一帧（三路图像＋16维状态＋一句指令），输出50步动作块（25 Hz下2秒）；D09约138秒就是连续推理约70个动作块（按0.4秒重规划约350次）。每次推理的序列长度固定：π0约867个token（3×256图像＋48文本＋1状态＋50动作），π0.5约1018个（3×256＋200文本＋50动作）。
- **指令长度够用**：最长粗指令（D09＋热狗）在π0格式下40/48个token，在π0.5格式（指令＋16维离散状态）下111/200；全部182条指令均未截断。π0只剩8个token余量，以后若在指令里加入进度/记忆文本，应使用π0.5或调大`max_token_len`。
- **真正的长程风险是没有记忆**：模型只能从当前画面和本体状态判断进行到哪一步。本任务各阶段大多留下可见变化（水果/餐盒是否在盘上、门的状态），清洗前后、加热前后等少数时刻主要靠手臂姿态区分。本机OpenPI源码没有子任务生成（高层推理）接口，长程执行可由外部按细指令逐段下发，粗指令则依赖模型自己判断阶段。
- **帧采样偏向长程**：长程任务占全部帧的85%，原子任务15%；按帧均匀采样时原子技能曝光偏少，训练时可按任务类型重加权。
- **显存是硬约束**：OpenPI README给出推理需>8 GB、LoRA微调需>22.5 GB、全量微调需>70 GB。本机RTX 5060 Ti 16 GB可做推理，不能做LoRA微调；训练需要24 GB以上显卡或云端。
