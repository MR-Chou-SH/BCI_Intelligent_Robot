# 2026-10-26 全任务分样本批量采集

在现有 `feature/kitchen-linux-task-collection` 分支、HEAD `4815510` 和未提交工作区上继续；保留用户及此前修改，未切换、提交或推送。

新增 `collect_dataset.py` 与 `scripts/collect_kitchen_dataset.sh`，复用现有 one_case 物理执行及质量/控制重仿真。20 原子实例加 44 组合实例，每例两个合格样本位；失败按不同 seed 有限重试，不占成功位。不可变计划固定源码及参数，独占目录锁避免并发协调；断点从封存报告恢复，未封存尝试保留且消耗一次额度。MP4 按样本隔离，记录源码轨迹和视频校验值，渲染失败可续跑。只在全部样本与视频齐全时退出 0。

正式采集关闭全局开发 checkpoint，避免并行任务覆盖同名 checkpoint。保留默认紧凑状态/动作与失败截图策略。历史证据未移动或删除；新数据统一 collections/，补充 Git 忽略规则、目录说明及本地 workspace_inventory.json。

验证：

- 既有 29 项加新增 5 项测试，共 34 项通过，涵盖 128 样本矩阵、重试 seed 隔离、失败次数上限、部分中断保留、篡改拒绝及分样本视频续跑。
- 原生 MuJoCo：`collections/pipeline_smoke_20261026`，R5 burger research seed 0/1，2/2 QUALIFIED_EXPERT 与 CONTROL_RESIMULATION_PASS；两个 320×180、4fps、4 倍速 MP4 成功，抽帧确认可正常显示场景。
- 同参数 --resume：2/2 轨迹、2/2 视频直接复用，无新增仿真或视频目录。
- `collections/all_tasks_2each_20261026/plan.json`：仅预生成全矩阵计划，64 用例、128 目标样本，供用户执行。
- Bash 语法及 git diff --check 通过。

未全量实采 128 条，未宣称全部任务在当前随机化模型下成功；未涉及 VR、EEG 或真实硬件。
