# 2026-10-26 组合任务的左右臂衔接修复

用户指定暂不处理内存问题，只修改组合任务。本轮保持 `feature/kitchen-linux-task-collection` 分支与已有未提交工作区。

## 原因与改动

之前 C05/C06/C07 的失败末帧均出现 `left_umi_umi_gripper_base` 与 `right_fr3_link6` 接触。左手释放水果后仍留在托盘上方；原子右侧任务从左臂待机姿态开始，所以独立成功不能证明组合成功。

仅在 `KitchenExpert.run_case` 的跨技能边界增加让位：C05–C08 的 L1/L2 完成后，调用现有带几何路径检查的 `return_home("left")`；再复核水果持续稳定支撑与盘内包络，然后才启动右侧直接装盘或加热链。撤离失败会中止，不继续右侧动作。单独 L1/L2、C01/C02 不追加回待机动作。

不改变模型、摩擦、随机化范围、跟踪容差与质量门槛，不引入新的规划算法，不修改内存或采集调度实现。

## 验证

新增四项衔接测试：直接装盘前让位并复核水果、加热前让位、单技能不增加回待机动作、撤离失败阻止右侧启动。连同已有测试共 38 项通过。

原生 MuJoCo 回归输出位于 `experiments/kitchen_platform_v3/collections/composite_clearance_regression_20261026/`；本次只跑四个 research 代表用例，不启动全量 128 条。完成情况和控制重仿真以各 `report.json`、`results.json` 与 `acceptance.json` 为准。

| 用例 | seed | 结果 |
| --- | ---: | --- |
| C05 苹果＋汉堡 | 1 | QUALIFIED_EXPERT / CONTROL_RESIMULATION_PASS |
| C06 清洗香蕉＋热狗 | 0 | QUALIFIED_EXPERT / CONTROL_RESIMULATION_PASS |
| C07 香蕉＋加热汉堡 | 0 | QUALIFIED_EXPERT / CONTROL_RESIMULATION_PASS |
| C08 清洗梨＋加热热狗 | 0 | QUALIFIED_EXPERT / CONTROL_RESIMULATION_PASS |

四条重仿真的最大 qpos/qvel 误差均为 0；这四条的结果绑定本轮计划记录的源码身份。

C05 apple/burger seed1 的独立回看视频：`review/C05_apple_burger_seed1.mp4`，640×360、8fps、4 倍速，完整轨迹200.48仿真秒；抽帧确认左臂已退出右侧通道。

25Hz 几何复核针对先前碰撞的特定左夹爪底座—右臂第六连杆，不是整条轨迹的连续防碰撞证明。全矩阵随机化验收仍需后续采集；苹果 seed0 的独立抓取 IK 与香蕉 seed1 的支撑释放问题未在本次改动中处理。

旧批次已绑定旧源码身份，保留原位。修复后的正式采集使用新目录，不修改旧 plan/report 绕过校验。
