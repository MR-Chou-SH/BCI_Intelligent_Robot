# BCI_Intelligent_Robot 项目暂停检查点（2026-09-27）

## 1. 当前总体状态
项目暂停工程推进，转入 VLA 学习阶段。当前主要冻结两条主线：
1. M19 / Phase-2 Research EEG 采集链路；
2. M20 日常辅助桌面场景 + 新场景全链路回归 + Context/EEG 离线分析。

## 2. M19 / Phase-2 Research EEG
已完成：
- Demo / Research 双模式分离；
- Trigger dwell = 1.5 s；
- Research trialZero = Trigger 完成后的软件事件；
- 连续 ND8 → sample anchor → 4 s capture 链路已跑通；
- Smoke/QC 出现过 6/6 PASS；
- live ND8 时钟域问题已修复；
- 正式 Research 每 9-trial block 自动暂停并保存 resume 状态；
- target cue 已改成音高 + 声数双重编码：
  - slot0 / 7.2 Hz → 660 Hz
  - slot1 / 9 Hz → 880 Hz
  - slot2 / 12 Hz → 1320 Hz
- 人工试听已确认三种音高可清楚区分。

今天调试产生的 formal F1 均不作为正式研究数据继续使用，保留原始文件用于审计，但不要 resume：
- formal-20260927-012720
- formal-20260927-014338
- formal-20260927-015340
- 以及更早因提示音/操作问题产生的 formal session

恢复 EEG 采集时必须新建 formal session，从新的 F1 第 1 条重新开始。

## 3. M20 Task 1 — 日常辅助桌面场景
软件验收：PASS。

Unity 与 MuJoCo 已使用统一 canonical scene specification，包含：
- 小药盒
- 带铰链收纳盒
- 手机
- 按钮/开关
- 无线充电座
- USER ZONE

软件侧已验证：
- Unity / MuJoCo 尺寸、位置与左右方向一致；
- 收纳盒铰链可开合；
- 按钮有约 5 mm 行程；
- 初始状态无 M20 对象穿模；
- 药盒 / 手机抓取位置可达；
- 旧 M9 积木场景仍保留。

2026-09-27 已人工 Build & Run 到 Quest，用户确认最新 M20 场景显示正确。
如后续需要完整 Quest HCI 验收，可继续人工检查分页、Previous / Next / Undo Last / Submit。

## 4. M20 Task 2 — 新场景全链路
软件 / synthetic 验收：PASS。

候选顺序：
Page 1：
1. medicine box
2. storage box
3. phone

Page 2：
1. button
2. wireless charger
3. USER ZONE

SSVEP slot：
- slot0 = 7.2 Hz
- slot1 = 9 Hz
- slot2 = 12 Hz

软件回归覆盖：
- Previous / Next
- 跨页选择
- Undo Last
- Submit 顺序
- stale result rejection
- active-slot check
- Context affordance
- strong synthetic EEG override

Focused regression：61 tests passed。

MuJoCo 三个 pick-and-place 路径通过：
1. medicine → USER ZONE
2. phone → USER ZONE
3. phone → wireless charger

本阶段只宣称 MuJoCo / software / synthetic 通过，不宣称实体机器人通过。

## 5. M20 Task 3 — Context × EEG 离线分析
状态：PASS（research / replay / offline only）。

主要时间指标改为：
effective EEG evidence time = onset-relative decision time - 0.5 s

历史 V3：
- EEG-only：mean onset-relative ≈ 1.445 s；effective ≈ 0.945 s
- EEG+Context：mean onset-relative ≈ 1.432 s；effective ≈ 0.932 s
- Context 平均只提前约 13.6 ms

关键结论：
- FBCCA 输出是非负 score，不是校准概率；
- M12 存在 lambda = 0.5 prior softening；
- 原始 prior [0.90, 0.05, 0.05] 会被软化到约 [0.6167, 0.1917, 0.1917]；
- 历史强 EEG 数据上更强 Context 权重只让少数 trial 更早停止；
- cross-fitted selector 在历史数据上选择 alpha = 0；
- 当前历史数据的主要限制是 EEG 本身已经很强，Context 可提供的额外提前空间很小；
- 当前不应把 stronger Context fusion 直接部署到 production M19；
- 下一步更有价值的是用 weaker Natural-gaze EEG 再验证 Context。

## 6. 当前冻结原则
暂停期间不要破坏：
- slot0/1/2 = 7.2/9/12 Hz
- Demo / Research 双模式分离
- M19 frozen page mapping
- Quest 负责 slot → TargetId 解析
- Previous / Next / Undo Last / Submit 已验收语义
- M9 ordered dispatch
- 旧积木场景
- M20 新场景
- production M19 fixed-window decoder

V3 / stronger Context 仍为 research-only。

## 7. 下一阶段
当前工程推进暂停，优先补充 VLA 知识：
ACT → Diffusion Policy → RT-1 → OpenVLA → Pi0

后续再决定如何把：
EEG 离散意图 → Context / task state → VLA / robot skill
接回 BCI_Intelligent_Robot。

## 8. 恢复项目时
1. 先阅读本文件；
2. 阅读 M20_OVERNIGHT_FINAL_REPORT.md；
3. 检查 Git branch / HEAD / working tree；
4. 不恢复今天作废的 EEG formal session；
5. Phase-2 重启时创建全新 formal session；
6. 如继续 M20，补 Quest HCI 真人验收；
7. 如进入 VLA，优先设计 action/skill interface，不直接重构稳定的 M9/M19 主链路。

## 9. 推荐 Git commit message
checkpoint: freeze M20 assistive scene and context analysis
