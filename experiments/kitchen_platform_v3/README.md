# Kitchen 生成数据目录

新批量数据统一放在 `collections/<批次名>/`。入口、续跑、参数及目录结构见[全任务两条采集说明](../../docs/kitchen_platform_v3_20261020/DATASET_COLLECTION_CN.md)。

- `collections/`：新采集计划、分样本尝试、紧凑状态/动作 HDF5、汇总；视频仅在手动请求时另行导出。
- `expert_data/`：之前清理时保留的历史成功数据，不等同于当前模型验收。
- `handle60_*/`：60 度把手模型的实验及回看；`handle60_acceptance_*.json` 是该轮证据索引。
- `simple_geometry_*/`、`linux_acceptance_*/`、`R1_*/`、`R3_*/`、`B1_*/`、`review_*/`：历史调试及验收，可能对应旧模型。
- `checkpoints/`：可选开发状态，现有中间 checkpoint 已清理，批量采集不写入这里。
- `workspace_inventory.json`：整理时按目录统计的文件数与字节数。
- `cleanup_summary.json`：之前获授权清理的历史摘要。

历史数据保留原位，避免报告绝对路径与证据校验失效。生成物不进入 Git；不要通过手改报告或模型指纹让旧轨迹冒充当前模型结果。

常规数据仅为压缩初始状态、关键状态和动作，不含训练图像。新失败尝试只保留简短报告；不再自动截图、保存检查过程或渲染视频。格式见[记录重放指南](../../docs/kitchen_platform_v3_20261020/STATE_RECORD_REPLAY_CN.md)。

2026-10-27 清理清单：`collections/cleanup_20261027.json`，403 个生成文件、1,663,306,372 字节。成功轨迹保留；旧失败报告原文保留，但其中已清理的诊断文件路径不再可用。当前 V3 字段与模块目录见[格式说明](../../docs/kitchen_platform_v3_20261020/DATASET_FORMAT_CN.md)和[代码结构](../../integration/kitchen/README.md)。
