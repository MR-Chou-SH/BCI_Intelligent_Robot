# 随机苹果50条训练命令与真实重放导出

用户要求先用原专家规划/优化逻辑采集50条随机初态拿苹果轨迹，再通过独立replay获得本体状态、观测图像和动作，导出LeRobot v3，训练结束后才评估。

改动：采集器每case样本上限从20放宽至1000；增加`kitchen_smolvla.sh replay --manifest`，以现有`data/replay.verify`只读checkpoint观察回调派生同步state/RGB。完整物理初态只恢复一次，之后实际执行原动作，不注入中间qpos；保留原成功/重放检查。派生数组为小NPZ而非新H5，图像为C全景/左腕PNG。按episode封存SHA凭据，支持恢复未完成批次；已封存数据损坏不覆盖。导出从派生数组读取state/action，并检查真实writer产物`meta/info.json`必须是v3.0。

在线与replay共用`vla/observation.py`读取机器人实测状态。L1增加`rollout --initial-seed`，通过现有reset接口生成research初态并停稳1秒；只在开始重置，不使用专家后续动作。这允许训练后使用10000等未见seed评估，不必为测试初态另跑脚本专家。其他任务尚不开放此新seed准备方式。

验证：已有research L1 apple源H5独立重放723帧，qpos/qvel误差0且任务成功；两个启用视角同tick，源H5哈希不变。恢复明确复用封存episode。真实LeRobot v3.0读回state16/action chunk50×16与replay NPZ逐元素一致，两路图像3×512×512，右腕不存在。RTX4060完成两步训练，峰值1.9445GiB；仅功能检查，loss0.3058→5.9462不支持收敛结论。新seed reset测试证明同seed可复现、不同seed改变qpos，读取机器人状态不改变MuJoCo integration state。全部134项Kitchen测试通过（`/tmp/kitchen_apple50_tests.log`）。

证据在`experiments/kitchen_platform_v3/vla/smolvla_replay_export_smoke_20261109/verification.json`。本轮没有启动正式50条采集、长训练或学习策略eval。用户执行命令见[50条苹果流程](../kitchen_platform_v3_20261020/APPLE50_SMOLVLA_CN.md)；规划、速度优化和物理成功阈值均沿用原实现。
