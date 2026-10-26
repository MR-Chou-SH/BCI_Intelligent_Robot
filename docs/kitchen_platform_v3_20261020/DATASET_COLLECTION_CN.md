# 全任务两条专家轨迹与视频采集

入口为 `scripts/collect_kitchen_dataset.sh`，复用现有几何规划、任务准备、质量检查和控制重仿真。20 个原子任务实例与 44 个组合任务实例，每个采集 **2 条合格轨迹，共 128 条与 128 个 MP4**。组合任务不自动追加 B1 送盘。

从仓库根目录执行，曲线路径修订后使用新的批次目录：

```bash
bash scripts/collect_kitchen_dataset.sh \
  --output experiments/kitchen_platform_v3/collections/all_tasks_2each_curves_20261027
```

中断后使用相同命令加 `--resume`。旧 `all_tasks_2each_20261026` 批次已暂停；源码修改后不可直接续跑旧计划。若代码或模型再次改变，计划指纹会拒绝续跑，应更换新目录。不要修改原计划来绕过校验。全量尚未实采完成，成功数量以 `summary.json` 为准；组合衔接修复的代表性回归不等于全部随机用例验收。

默认设置：

- `--suite all --samples-per-case 2 --jobs 2`：两个采集进程；视频在采集后顺序导出。
- `--profile research`：沿用当前小范围、避免初始重叠的随机化，保持左水果、右主食。两个样本初始 seed 为 0、1；每个样本最多尝试三次，失败使用 2/4 或 3/5。不同任务可以复用 seed。
- `--case-timeout 1800`：每次专家执行的协作式时间预算，沿用现有接口检查；不是外部强杀进程的硬超时。
- MP4：`v3_front`，960×540，15fps，4 倍速。需要正常速度时首次运行加 `--speed 1`；计划创建后不更改参数。MP4 为独立回看文件，HDF5 不含图像。
- `--max-attempts 3`：失败不算完成；达到上限后列入缺失项。续跑不无限追加尝试。需要继续排查时，保留本批并用新目录开展实验。

查看参数：

```bash
bash scripts/collect_kitchen_dataset.sh --help
```

新计划可先加 `--plan-only`，只写计划、不启动仿真。随后使用相同参数并加 `--resume`。筛选调试可用 `--case-ids kitchen_expert_v2:R5:burger` 或 `--suite atomic`。

## 输出与续跑

每个批次独占一个 `collections/<批次名>/`：

| 路径 | 内容 |
| --- | --- |
| `plan.json` | 用例矩阵、采集/视频参数、源码与控制指纹 |
| `attempts/<用例>/sample_01/attempt_01_seed0/` | 单次报告、任务初始状态和压缩 HDF5 |
| `videos/<用例>/sample_01/render_001/trajectory.mp4` | 对应合格轨迹的视频 |
| `manifest.json` | 合格样本与轨迹、报告、视频的对应索引 |
| `summary.json` | 目标、合格数、视频数、缺失样本及视频错误 |

运行中以逐项日志和 attempt 报告查看进度；总表在本轮采集和渲染结束时更新。只有所有样本及视频齐全才返回退出码 0，否则返回 1。

中断后执行原命令加 `--resume`：检查报告/轨迹校验值，跳过合格样本和已校验的视频。未封存尝试保留，消耗一次尝试，再换 seed。已有失败视频目录不覆盖，后续使用新 `render_NNN`。同一个目录禁止两个协调进程同时运行。

失败样本不会进入合格索引；按当前项目策略保留紧凑诊断轨迹与三张失败截图。默认不保存完整物理/RPC调试日志，批量执行也不写全局开发 checkpoint，避免并行覆盖。

## 已验证范围

2026-10-26：34 项单元测试通过；汉堡 R5 在 research seed 0、1 下各采一条，均通过质量与实际控制重仿真，两个 MP4 生成成功；同参数续跑跳过仿真与渲染。全矩阵计划为 64 用例、128 样本。此结果验证采集管线，不代表其他 63 个用例在当前模型和随机化下全部成功。

历史运行保留原位置，避免破坏报告中的绝对路径及校验关联。目录索引见 `experiments/kitchen_platform_v3/workspace_inventory.json`；数据、视频和开发 checkpoint 已加入 Git 忽略规则。未删除原始研究数据。
