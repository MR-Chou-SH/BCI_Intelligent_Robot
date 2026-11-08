# 将离线优化整理为所有任务的共同采集流程

用户要求统一此前改进，提供全任务各一条与统一视频命令。分支仍为 `feature/data-collection-improve`，HEAD `89768588655a6abda722af2db38113ef37611198`，保留既有未提交代码、数据及用户 `.gitignore` 修改；未切分支、提交、安装依赖或启动全量100条采集。

## 实现

- 新增 `data/optimized_collection.py`，常规 `collect_dataset` 默认 `--postprocess continuous`，优化请求倍率默认3、5。首次专家完整合格备份、动作候选/物理试跑、最短成功候选重新录制与独立重放、无可用优化则合格备份回退，成为所有case共用流程。
- 每attempt根部统一输出最终 `trajectory.h5/report.json/annotations.json`；backup、trial、optimized不重复计入manifest。报告记录OPTIMIZED/ORIGINAL_FALLBACK、原始/最终时长、备份与选中结果哈希、候选报告引用。summary区分优化与回退数，manifest写入原始备份source_group。
- 合格备份之后中断可继续同seed/attempt，复用完整候选和试跑；未封存半成品保留且不覆盖。首次备份未封存仍按原有有限换seed重试。恢复同时核验最终数据、备份和选中组件报告，不把损坏身份当成普通优化失败。
- `lerobot_export`沿用postprocessing中的原始轨迹group，避免备份/优化版本和粗细子段跨训练/验证集合。数据本身仍16D/25Hz，不增加图像或每步检查日志。
- 模型来源指纹增加 `contact_v4.py`，补齐梨底座/主食接触代理构造代码的批次身份绑定。
- 新增 `scripts/render_kitchen_dataset.sh` 和 `tools.render_dataset` CLI：严格读取最终manifest，验证封存收据，只渲染每case/sample一次，支持参数绑定的断点恢复；默认1倍速/25fps宽景，可选lightweight画面。既有递归 `render_videos` 仍可用于历史审核，但不推荐用于包含backup的新批次。
- 编写[统一操作说明](../kitchen_platform_v3_20261020/UNIFIED_COLLECTION_CN.md)，更新格式、转换、模块入口和项目上下文；旧专题文档保留历史证据并指向新入口。

## 验证与发现

完整 **112项测试通过**：新增优化成功选最快、全失败回退、封存结果恢复不重复执行、备份篡改拒绝、备份后中断复用同seed、派生数据沿用原始group、插值保持硬限位等回归。两个shell脚本语法检查通过。

原生批量接入的增量验证：

| 批次 | 用例 | 结果 |
| --- | --- | --- |
| `common_pipeline_smoke_20261106` | L2 pear seed1、R5 hotdog seed1 | 2/2优化合格；实际渲染最终manifest两条，视频/采集续跑均跳过已完成工作 |
| `common_pipeline_acceptance_20261106` | L2 pear、R5 hotdog、C04 hotdog、D06 apple/hotdog，均seed1 | 4/4合格，其中3优化、C04按规则回退；恢复返回4/4，无需重新仿真 |
| `common_pipeline_heat_ready_20261106` | C04 hotdog seed1 | 修正下述浮点边界问题后重新执行完整共同流程 |

acceptance中L2 pear89.52→51.60s、R5 hotdog30.32→23.00s、D06 apple/hotdog122.16→69.12s，均独立动作重放qpos/qvel误差0。D06包括真实载荷B1、最终食物保留、英文阶段重映射。它们不等于全目录验收。

### 浮点关节边界问题

C04两个候选被严格平台范围检查拒绝，初步通过合格备份回退206.64秒。随后定位候选的右臂关节5（0起始索引）原下限为0.8521rad，旧加权和插值在恒定值上产生0.8520999999999999，仅低1ULP（约1.11e-16rad）。

修改动作插值为delta形式，保持恒定限位精确值；输出仅将≤1e-9的数值边界舍入归一到原模型范围，超过此误差仍拒绝，不放宽硬限位或改变控制器。重新采集的C04两个候选均通过物理试跑，3倍168.28秒、5倍166.04秒；最终选择166.04秒录制，独立动作重放qpos/qvel误差0，resume跳过重采。此前失败与回退报告保留，不追改为成功。

### 计划与视频

`common_pipeline_all_plan_20261106` 仅plan-only，确认100实例、每实例一条、目标100，未启动全量实采。正式命令使用不同的新目录 `all_tasks_1each_optimized_20261106`，不混入历史数据或测试样本。

`videos_common_pipeline_smoke_20261106`只渲染manifest的梨和热狗两条，源报告/轨迹/标注与备份校验通过。视频为25fps、960×540、1倍速，帧数1290/575、时长51.60/23.00秒，编码日志无错误；`--resume`不再渲染。backup/trial/optimized未单独渲染。

常规视频入口只用于审核，H5不含图像，实际LeRobot writer/Unity补图仍待后续。统一采集已具备运行入口，但全100实例的失败率、回退分布和总体耗时应以新批次summary为准。
