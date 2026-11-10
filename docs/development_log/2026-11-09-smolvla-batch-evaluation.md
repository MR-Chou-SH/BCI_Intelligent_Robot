# SmolVLA多seed批量评测入口

用户要求将多个seed评测写成实际脚本，统一保存在一个目录，GPU推理服务继续单独启动。

新增`integration/kitchen/vla/evaluate.py`，入口为`bash scripts/kitchen_smolvla.sh evaluate --run <训练目录> --episodes 10 --video`。只连接用户已经运行的服务，不启动/终止该服务。每个seed启动独立仿真子进程、调用原rollout，串行运行并保存报告、视频及小型摘要，逐条更新统一summary。任务失败与运行错误分别计数；校验服务实际model SHA匹配指定checkpoint，并拒绝与checkpoint对应训练数据重叠的初态seed。

输出包含plan/相机契约/每seed封存result及report SHA；支持`--resume`，部分未封存尝试保留，已封存结果不重跑。改变checkpoint/数据/代码/相机/评测参数会拒绝混入同一批次。每条墙钟预算默认600s，超时只清理本脚本创建的仿真与ffmpeg进程组。推理服务仍由用户管理。模型随机采样序列未新增逐episode重置机制，因此恢复不保证与不中断运行逐动作相同。

验证：连接当前用户CUDA服务及其`checkpoint_010000`，seed10020/10021各执行1s，两条均得到25帧动作步进、正常视频和报告，无接口错误；由于测试预算只有1s，任务未完成不构成正式成功率结果。目录`experiments/kitchen_platform_v3/vla/apple50_c_left_20261109/eval_batch_script_smoke/`。恢复时`reused=2`，不再执行模型。完整139项Kitchen测试通过，日志`/tmp/kitchen_batch_evaluate_alltests.log`。

本次读取到正式50条训练报告：10000步、CUDA、5033.83s、末步loss0.01559；已有用户seed10000评测于4.92s因桌沿接触保护停止，未完成任务。此为现有报告观察，不代表本次完成正式10seed评测。运行命令与结果字段见[苹果训练流程](../kitchen_platform_v3_20261020/APPLE50_SMOLVLA_CN.md)。
