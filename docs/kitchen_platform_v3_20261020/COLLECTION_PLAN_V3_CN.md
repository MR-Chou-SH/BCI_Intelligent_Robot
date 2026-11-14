# Expert V3 正式采集大纲

2026-11-14，分支`feature/data-collection-improve`。用于训练听指令的VLA：同一批数据既可按**粗指令**（整条任务一句话）导出，也可按**细指令**（每个阶段一句话）导出，不需要分开采。

## 分配原则（用户确认：每句粗指令40条）

- **每一句不同的粗指令采40条。** 长程任务的每个“水果×主食”组合是一句不同的指令，各采40条（如C05有8个组合，共320条）；原子任务每种水果/主食各40条（如L1共160条）。
- **同一句指令的不同初态平分40条。** B1无论托盘上放什么都是同一句指令，15种负载各3条（共45）；R3的炉内空/汉堡/热狗各14条（共42）。
- **组合是均匀枚举的，不随机抽样**：同一句指令的每个实例条数相同。随机的只有每条样本的初始摆放（6组物体±80 mm、±5°），且每条样本使用不同种子（`seed_scheme: per_sample`）；失败自动换种子重试。
- 平均时长与采集耗时取自同一代码下全目录seed 0与seed 1两批实测（research初态、高速直采）。

## 采集大表

| 类型 | 编号 | 任务 | 实例（变量） | 不同粗指令 | 每实例 | 条数 | 平均时长 | 数据时长 | 粗指令（整条，一句） | 细指令（按阶段） |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 原子 | L1 | 抓水果直接放餐盘 | 4：4 种水果 | 4 | 40 | 160 | 16 s | 0.71 h | Place the {fruit} directly on the serving tray. | 同粗指令（单阶段 L1） |
| 原子 | L2 | 抓水果→清洗→放餐盘 | 4：4 种水果 | 4 | 40 | 160 | 41 s | 1.83 h | Wash the {fruit} and place it on the serving tray. | 同粗指令（单阶段 L2） |
| 原子 | R1 | 抓主食→放微波炉→关门 | 2：2 种主食 | 2 | 40 | 80 | 19 s | 0.43 h | Put the {food} in the microwave and close the door. | 同粗指令（单阶段 R1） |
| 原子 | R2 | 从微波炉取主食→放餐盘→关门 | 2：2 种主食 | 2 | 40 | 80 | 22 s | 0.49 h | Take the {food} out of the microwave, place it on the serving tray, and close the door. | 同粗指令（单阶段 R2） |
| 原子 | R3 | 微波炉关闭→直接开门 | 3：炉内 空/汉堡/热狗 | 1 | 14 | 42 | 12 s | 0.14 h | Open the microwave door. | 同粗指令（单阶段 R3） |
| 原子 | R4 | 启动加热→等待→开门 | 2：2 种主食 | 2 | 40 | 80 | 23 s | 0.50 h | Heat the {food} in the microwave, wait until it is ready, and open the door. | 同粗指令（单阶段 R4） |
| 原子 | R5 | 抓主食直接放餐盘 | 2：2 种主食 | 2 | 40 | 80 | 11 s | 0.25 h | Place the {food} directly on the serving tray. | 同粗指令（单阶段 R5） |
| 原子 | B1 | 双臂送餐盘（空盘/带负载） | 15：15 种托盘负载 | 1 | 3 | 45 | 23 s | 0.28 h | Deliver the serving tray to the user with both arms. | 同粗指令（单阶段 B1） |
| 装盘组合 | C04 | 主食加热装盘 | 2：2 种主食 | 2 | 40 | 80 | 69 s | 1.53 h | Heat the {food} and place it on the serving tray. | 同粗指令（单阶段 H） |
| 装盘组合 | C05 | 水果与主食直接装盘 | 8：4 水果 × 2 主食 | 8 | 40 | 320 | 30 s | 2.63 h | Place the {fruit} directly on the serving tray. Then, place the {food} directly on the serving tray. | L1 → R5 |
| 装盘组合 | C06 | 清洗水果与主食直接装盘 | 8：4 水果 × 2 主食 | 8 | 40 | 320 | 54 s | 4.78 h | Wash the {fruit} and place it on the serving tray. Then, place the {food} directly on the serving tray. | L2 → R5 |
| 装盘组合 | C07 | 水果直接装盘与加热主食 | 8：4 水果 × 2 主食 | 8 | 40 | 320 | 86 s | 7.62 h | Place the {fruit} directly on the serving tray. Then, heat the {food} and place it on the serving tray. | L1 → H |
| 装盘组合 | C08 | 清洗水果与加热主食 | 8：4 水果 × 2 主食 | 8 | 40 | 320 | 112 s | 9.93 h | Wash the {fruit} and place it on the serving tray. Then, heat the {food} and place it on the serving tray. | L2 → H |
| 送餐组合 | D01 | 水果直接装盘并送餐 | 4：4 种水果 | 4 | 40 | 160 | 41 s | 1.81 h | Place the {fruit} directly on the serving tray. Then, deliver the serving tray to the user with both arms. | L1 → B1 |
| 送餐组合 | D02 | 清洗水果装盘并送餐 | 4：4 种水果 | 4 | 40 | 160 | 66 s | 2.93 h | Wash the {fruit} and place it on the serving tray. Then, deliver the serving tray to the user with both arms. | L2 → B1 |
| 送餐组合 | D03 | 从炉内取餐装盘并送餐 | 2：2 种主食 | 2 | 40 | 80 | 47 s | 1.05 h | Take the {food} out of the microwave, place it on the serving tray, and close the door. Then, deliver the serving tray to the user with both arms. | R2 → B1 |
| 送餐组合 | D04 | 主食直接装盘并送餐 | 2：2 种主食 | 2 | 40 | 80 | 35 s | 0.78 h | Place the {food} directly on the serving tray. Then, deliver the serving tray to the user with both arms. | R5 → B1 |
| 送餐组合 | D05 | 主食加热装盘并送餐 | 2：2 种主食 | 2 | 40 | 80 | 94 s | 2.10 h | Heat the {food} and place it on the serving tray. Then, deliver the serving tray to the user with both arms. | H → B1 |
| 送餐组合 | D06 | 水果与主食直接装盘并送餐 | 8：4 水果 × 2 主食 | 8 | 40 | 320 | 53 s | 4.72 h | Place the {fruit} directly on the serving tray. Then, place the {food} directly on the serving tray. Finally, deliver the serving tray to the user with both arms. | L1 → R5 → B1 |
| 送餐组合 | D07 | 清洗水果与主食直接装盘并送餐 | 8：4 水果 × 2 主食 | 8 | 40 | 320 | 80 s | 7.09 h | Wash the {fruit} and place it on the serving tray. Then, place the {food} directly on the serving tray. Finally, deliver the serving tray to the user with both arms. | L2 → R5 → B1 |
| 送餐组合 | D08 | 水果与加热主食装盘并送餐 | 8：4 水果 × 2 主食 | 8 | 40 | 320 | 112 s | 9.95 h | Place the {fruit} directly on the serving tray. Then, heat the {food} and place it on the serving tray. Finally, deliver the serving tray to the user with both arms. | L1 → H → B1 |
| 送餐组合 | D09 | 清洗水果与加热主食装盘并送餐 | 8：4 水果 × 2 主食 | 8 | 40 | 320 | 138 s | 12.26 h | Wash the {fruit} and place it on the serving tray. Then, heat the {food} and place it on the serving tray. Finally, deliver the serving tray to the user with both arms. | L2 → H → B1 |
| **原子合计** | 8 类 | | 34 | 18 | | **727** | | **4.64 h** | | |
| **长程合计** | 14 类 | | 80 | 80 | | **3200** | | **69.18 h** | | |
| **总计** | 22 类 | | 114 | 98 | | **3927** | | **73.82 h** | | |

注：
- {fruit}为apple/pear/banana/tomato，{food}为hamburger/hot dog（对象ID分别为burger/hotdog）。B1的托盘负载、R3的炉内状态是可见的初始状态，不写进指令。
- 原子任务（以及C04）只有一个阶段，粗指令＝细指令。长程任务的粗指令由各阶段细指令用 Then/Finally 串成。
- H（加热装盘）是一个细阶段，内部按实测设备状态执行开门R3→装炉R1→加热开门R4→取出装盘R2（已满足的步骤跳过），不拆成R1–R4的细指令。
- 阶段边界：水果放好并稳定即结束水果阶段；随后左臂让位属于下一阶段（R5或H）；最后的稳定等待属于最后阶段。

## 细指令覆盖

| 阶段代号 | 细指令模板 | 出现在 | 阶段条数 | 阶段时长 |
| --- | --- | --- | ---: | ---: |
| L1 | Place the {fruit} directly on the serving tray. | L1、C05、C07、D01、D06、D08 | 1600 | 7.03 h |
| L2 | Wash the {fruit} and place it on the serving tray. | L2、C06、C08、D02、D07、D09 | 1600 | 18.30 h |
| R5 | Place the {food} directly on the serving tray. | R5、C05、C06、D04、D06、D07 | 1440 | 5.00 h |
| H | Heat the {food} and place it on the serving tray. | C04、C07、C08、D05、D08、D09 | 1440 | 28.07 h |
| R1 | Put the {food} in the microwave and close the door. | R1 | 80 | 0.43 h |
| R2 | Take the {food} out of the microwave, place it on the serving tray, and close the door. | R2、D03 | 160 | 0.99 h |
| R3 | Open the microwave door. | R3 | 42 | 0.14 h |
| R4 | Heat the {food} in the microwave, wait until it is ready, and open the door. | R4 | 80 | 0.50 h |
| B1 | Deliver the serving tray to the user with both arms. | B1、D01–D09 | 1885 | 13.36 h |

R1、R3、R4单独作为指令时样本较少（42–80条），它们大多包含在H阶段内。物体均衡：每种水果出现在809条样本中，每种主食出现在1629条样本中（含B1负载与R3炉内状态）。

## 总量与资源

| 项目 | 数值 |
| --- | --- |
| 样本条数 | 3927（原子727＋长程3200） |
| 演示时长 | 73.8 h（原子4.6 h＋长程69.2 h；原子帧占6%） |
| 训练帧数（25 Hz） | 约664万帧；三路相机共约1993万张图 |
| 采集耗时 | 约4.6小时（6进程，含重试；Isaac Sim同时运行时更长），可一晚完成 |
| H5存储 | 约8 GB（最终H5是直采/备份H5的硬链接，只存一份） |
| 图像（三路PNG） | 约598 GB——**不可行** |
| 图像（H.264视频） | 约9 GB（实测PNG/视频约68:1） |

本机剩余约43 GB。

## 执行

采集器已支持本大纲（2026-11-14）：`--samples-per-instruction 40` 按粗指令分组生成每个实例的条数（写入plan的`samples`），整批共用一套不重复的种子（首次尝试0–3926，第a次重试再加a×3927）；最终H5与直采/备份H5为硬链接。在工作区根目录执行（本工作区没有自己的`.venv`，使用主仓库的Python）：

```bash
cd /home/user/Projects/BCI_Intelligent_Robot-dci
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONUNBUFFERED=1 \
../BCI_Intelligent_Robot/.venv/bin/python -B -m integration.kitchen.data.collect_dataset \
  --output experiments/kitchen_platform_v3/collections/kitchen_v3_40pi_$(date +%Y%m%d) \
  --suite all --samples-per-instruction 40 --jobs 6 \
  --profile research --motion-profile faster \
  --postprocess adaptive --defer-backup-replay --direct-fast
```

先加`--plan-only`可只生成计划（应显示target 3927）；中断后原命令加`--resume`续跑。

**正式批次（2026-11-14 16:51开始）**：数据写在共享存储 `/home/user/data-staging/jiayu/BCI_DATA/kitchen_v3_40pi_20261114/`（NFS），日志 `kitchen_v3_40pi_20261114.collect.log`、协调进程号 `.pid` 在同级目录。仓库外输出需显式设置 `KITCHEN_DATA_ROOTS=/home/user/data-staging/jiayu/BCI_DATA`（采集、续跑以及之后读取这批数据的渲染/导出工具都要设置）。源码提交 `f12fd38`；采集进行中不要修改 `integration/kitchen/{planning,tasks,validation,data,runtime}` 和模型/控制源码，否则未完成的样本会因源码身份不符而拒绝运行。NFS上的文件锁、HDF5写入、硬链接与续跑已用小批次验证。

**结果（2026-11-14 20:17完成，用时3小时26分）**：3927/3927合格，无缺失；3927个种子各不相同；全部独立重放误差为0（CONTROL_RESIMULATION_PASS）；最终H5均为硬链接，批次共9.0 GB。3880条高速直采（DIRECT_FAST），46条直采失败后经adaptive重定时（OPTIMIZED），1条回退原速轨迹（ORIGINAL_FALLBACK，D02香蕉）。演示时长74.7 h。共140次尝试失败后换种子成功，主要为汉堡餐盒无可行抓取（65）、入炉路线IK无解（25）、香蕉松手前无支撑（14）；因此数据略偏向专家能完成的初态。各类条数与上表一致（例如C05–C08、D06–D09各320条）。

## 采集后、训练前

1. **评测种子另留**：VLA评测使用100000以上的种子，与训练初态不重叠。
2. **图像直接编码为视频**：现有离线渲染逐帧写PNG，此规模需改为直接编码视频后再导出LeRobot（视频特征），或在其他机器完成。H5可随时重新渲染。
3. **训练时按任务类型加权**：原子任务只占约6%的帧，按帧均匀采样时原子技能曝光偏少。
4. **推理限速同步**：高速直采数据需要在放宽后的限速下复现，评估VLA前必须同步`vla/environment.py`。
5. **香蕉**：约1/12的初态规划失败，自动换种子重试，已计入时间估计；这些初态在数据中缺失。

## 模型侧：OpenPI能否处理长程任务（2026-11-14分析）

依据本机OpenPI源码（`~/Projects/openpi`，`b6ed826`）与缓存的PaliGemma分词器实测：

- **任务长度不进入模型上下文**。π0/π0.5每次只看当前一帧（三路图像＋16维状态＋一句指令），输出50步动作块（25 Hz下2秒）；D09约138秒就是连续推理约70个动作块（按0.4秒重规划约350次）。每次推理的序列长度固定：π0约867个token（3×256图像＋48文本＋1状态＋50动作），π0.5约1018个（3×256＋200文本＋50动作）。
- **指令长度够用**：最长粗指令（D09＋热狗）在π0格式下40/48个token，在π0.5格式（指令＋16维离散状态）下111/200；全部182条指令均未截断。π0只剩8个token余量，以后若在指令里加入进度/记忆文本，应使用π0.5或调大`max_token_len`。
- **真正的长程风险是没有记忆**：模型只能从当前画面和本体状态判断进行到哪一步。本任务各阶段大多留下可见变化（水果/餐盒是否在盘上、门的状态），清洗前后、加热前后等少数时刻主要靠手臂姿态区分。本机OpenPI源码没有子任务生成（高层推理）接口，长程执行可由外部按细指令逐段下发，粗指令则依赖模型自己判断阶段。
- **帧采样偏向长程**：长程任务占全部帧的94%，原子任务6%；按帧均匀采样时原子技能曝光偏少，训练时可按任务类型重加权。
- **显存是硬约束**：OpenPI README给出推理需>8 GB、LoRA微调需>22.5 GB、全量微调需>70 GB。本机RTX 5060 Ti 16 GB可做推理，不能做LoRA微调；训练需要24 GB以上显卡或云端。
