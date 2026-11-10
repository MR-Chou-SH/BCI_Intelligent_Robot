# 50条随机初态苹果示范：轨迹、replay、LeRobot v3与训练

2026-11-09。任务固定L1 apple：`Place the apple directly on the serving tray.`。50条均用于训练，本阶段不跑学习策略eval，不额外创建验证集。训练后用独立的新随机初态评估。以下命令由用户在项目根目录执行；本轮只运行单条既有轨迹的replay/导出/两步训练检查，没有自动启动50条正式采集或长训练。

## 1. 采集50条合格轨迹

```bash
export APPLE_RUN=experiments/kitchen_platform_v3/vla/apple50_c_left_20261109

bash scripts/collect_kitchen_dataset.sh \
  --output "$APPLE_RUN/collection" \
  --suite atomic --case-ids kitchen_expert_v3:L1:apple \
  --samples-per-case 50 --base-seed 0 --max-attempts 3 --jobs 2 \
  --profile research --motion-profile faster \
  --postprocess continuous --optimization-factors 3 5
```

沿用[共同采集流程](UNIFIED_COLLECTION_CN.md)：首次专家完整备份→离线连续优化候选→同初态物理执行检验→最快合格结果重新记录→独立动作重放；优化都失败则回退已经合格的首次备份。50条是最终合格轨迹数，备份、候选、失败尝试不重复计数。

research初态保持左水果/右主食；当前范围为对象组在参考位置附近XY各±8cm、yaw±5°，由既有桌面支持/间距/不重叠审计进一步约束，不是任意重排。第一轮seed0～49，失败有限重试使用50～99、100～149。轨迹成功检查与重放校验仍保留，属于专家数据资格检查，不是模型eval。

最终路径见`$APPLE_RUN/collection/manifest.json`，汇总见`summary.json`，必须`complete=true, qualified=50`。中断后同一采集命令追加`--resume`。不要在批次运行期间修改模型、规划、控制或采集代码。

## 2. 从初态真实replay，获取训练观测

```bash
bash scripts/kitchen_smolvla.sh replay \
  --manifest "$APPLE_RUN/collection/manifest.json" --expected-episodes 50 \
  --output "$APPLE_RUN/replay"
```

这一步与旧`render`不同：独立恢复完整初态，执行记录的关节/夹爪动作，以500Hz推进物理，每25Hz在下一动作执行前读取实测本体状态，并渲染同一时刻的C全景和左腕RGB。过程中不注入记录的中间qpos，不做状态纠偏，不调用规划器/学习策略。仅用H5关键状态对比重放误差及检查原专家任务是否仍成功。

原H5不含图像且不修改。派生内容位于`replay/<源H5哈希>/`：两路PNG、小型`policy.npz`（ticks、实测state、action）、封存的`episode.json`；全批次入口为`replay/images.json`。右腕关闭，不生成占位图。终态无后续动作，因此不作为额外训练帧。16维state/action顺序及量纲沿用[SmolVLA契约](SMOLVLA_PILOT_CN.md)。

中断后同命令追加`--resume`：已封存且哈希匹配的episode跳过，仅未封存派生内容可重建；损坏的封存数据或改变的来源/相机/实现会拒绝复用。不要递归扫描备份/优化候选作为新episode。

## 3. 导出真正的LeRobot v3.0

```bash
bash scripts/kitchen_smolvla.sh export \
  --manifest "$APPLE_RUN/collection/manifest.json" --expected-episodes 50 \
  --images "$APPLE_RUN/replay/images.json" \
  --output "$APPLE_RUN/dataset" --repo-id local/kitchen_l1_apple50
```

导出读取replay产生的state/action及图像，校验来源、tick、数组与文件哈希，使用本地真实LeRobot writer，强制检查`meta/info.json`的`codebase_version=v3.0`。图像使用LeRobot image feature，不使用审核MP4，也不把本项目H5 v3误称为LeRobot v3。

一个原始成功episode导出一个完整训练episode；25Hz、`observation.state`16维、`action`16维、`observation.images.camera1/camera2`两路512×512，英文task。输入只有机器人实测本体状态、图像和指令，不把物体真值状态作为模型输入。保留source_group和来源哈希供后续拆分审计。

`export`输出必须全新，目前不支持中途续写。若导出中断，保留轨迹/replay，换一个新dataset目录重试；训练命令对应改为新目录。

## 4. RTX4060训练，期间不做eval

```bash
bash scripts/kitchen_smolvla.sh train \
  --dataset "$APPLE_RUN/dataset" --repo-id local/kitchen_l1_apple50 \
  --output "$APPLE_RUN/train" --device cuda \
  --steps 10000 --batch-size 8 --learning-rate 0.0001 --save-every 5000
```

用独立`thirdparty/smolvla/.venv`，仿真仍用项目`.venv`。当前8GB起步配置为batch1、冻结VLM/视觉、训练action expert和state projection。10000步是首轮训练预算，不保证任务成功或收敛；先按固定预算完成，再做闭环评估，不按测试初态结果反复选择checkpoint。本入口不进行模型eval。完整模型/相机契约/处理器保存在`checkpoint_005000`、`checkpoint_010000`，训练记录在`training_report.json`；目前尚无optimizer断点恢复。

## 5. 训练完成后，单独评估

终端A（重新打开终端时重新设置APPLE_RUN）：

```bash
export APPLE_RUN=experiments/kitchen_platform_v3/vla/apple50_c_left_20261109
bash scripts/kitchen_smolvla.sh serve \
  --checkpoint "$APPLE_RUN/train/checkpoint_010000" --device cuda --rtc
```

待服务输出`ready: true`后，终端B：

```bash
export APPLE_RUN=experiments/kitchen_platform_v3/vla/apple50_c_left_20261109
SOURCE=$(.venv/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))[0]["trajectory"])' \
  "$APPLE_RUN/collection/manifest.json")

bash scripts/kitchen_smolvla.sh rollout --trajectory "$SOURCE" \
  --initial-seed 10000 --output "$APPLE_RUN/eval_seed10000" \
  --seconds 60 --clock realtime --video --audit
```

此处`--trajectory`提供匹配模型身份和任务定义；`--initial-seed`通过已有reset接口重新生成research初态，并完成与L1专家相同的1秒初始停稳，不执行专家动作，不使用该H5后续状态。seed10000不在采集重试的0～149内。目前新seed评估只开放L1，避免其他任务在未准备微波炉内容等前提下被误评。

`report.json`保存物理成功判定、初态seed/hash、保护触发、动作限幅和性能摘要；`rollout.mp4`为正常速度全景视频。退出码0为成功，1可能是任务失败或接口错误，需区分报告`POLICY_FAILED`与`POLICY_ERROR`。单个seed仅为首次观察；要估计成功率，应固定例如10000～10009共10个未见seed逐一运行，每次用独立输出目录，统计成功数和接口错误数。正式评测默认realtime，队列耗尽时沿当前目标保持并继续推进物理，不暂停等待模型；lockstep仅作为显式选择的调试模式。

### 批量评测：多个seed统一保存

终端A的`serve`命令保持不变。服务输出`ready: true`后，在终端B运行以下命令；批量脚本只连接现有服务，不启动或关闭GPU服务。

```bash
bash scripts/kitchen_smolvla.sh evaluate \
  --run experiments/kitchen_platform_v3/vla/apple50_c_left_20261109 \
  --seed-start 10000 --episodes 10 --seconds 60 --video \
  --output experiments/kitchen_platform_v3/vla/apple50_c_left_20261109/eval_batch_10seeds
```

默认连接`127.0.0.1:8001`，使用该训练目录的`train/checkpoint_010000`校验服务返回的模型身份；其他服务地址可通过`--host/--port`指定，其他权重可通过`--checkpoint`指定（服务仍需用户单独加载相同权重）。自动从collection manifest选择L1模板，并查询checkpoint对应训练数据的真实seed，拒绝把训练seed当未见初态。相机配置继承checkpoint。默认realtime、开启性能审计，按顺序执行；`--replan-remaining`默认40，即50步预测块每消耗10步（0.4秒）请求新观测预测。真实服务时间过长时实际请求频率会受限，summary并不把2.5Hz当作硬实时保证。

统一输出：

```text
eval_batch_10seeds/
  plan.json                    checkpoint/数据/代码身份、相机和评测参数
  camera_rig.json
  summary.json                 成功数、任务失败数、运行错误数、待测数与成功率
  seed_10000/
    result.json                本条摘要和report哈希
    attempt_01.log
    attempt_01/report.json
    attempt_01/rollout.mp4
  seed_10001/...
```

每条结束立即更新summary。任务失败继续；接口错误、模型不符、缺失报告或单条墙钟超时单独计为ERROR，不能混作任务失败。默认每条墙钟上限600秒，可通过`--episode-timeout`修改；只终止本脚本创建的仿真/ffmpeg进程组。正式batch退出码0表示所有条目处理完且无运行错误，即使任务成功率为0；有运行错误返回2。

省略`--output`会自动创建带时间戳的新批次。中断后原命令追加`--resume`，已封存结果验证report哈希后跳过；未封存尝试保留，并新建attempt继续。已封存的ERROR也保留，重测应使用新批次。改变checkpoint、代码、相机或参数需要新目录。恢复不承诺与不中断运行产生逐动作相同的策略随机采样序列；物理初态seed仍可复现。

本轮脚本验证连接已有CUDA服务，seed10020/10021各1秒、视频/报告均有效、无接口错误，另验证恢复时不重跑已封存结果；这是工具验证，1秒未完成任务不用于估计正式成功率。

2026-11-09后续诊断及默认实时切换见[实时执行与训练误差](../development_log/2026-11-09-smolvla-realtime-fitting-diagnosis.md)。新报告的`action_limits`分别记录位置裁剪、关节/TCP速度限制、开度裁剪、控制信号及力矩饱和；25Hz采样计数不等价于500Hz所有瞬时峰值。`performance.request_rate_wall_hz/request_rate_sim_hz`是实测请求频率，不能用GPU单次推理时间的倒数冒充实际闭环更新频率。

## 本轮工程验证

单条L1 apple：原H5保持不变；真实独立重放723帧、qpos/qvel最大误差0、原专家任务成功；断点恢复复用封存episode。真实LeRobot v3.0读回的state/action与replay NPZ逐元素一致、两路图像有效，右腕缺省。工程测试134项通过。本轮没有运行50条正式采集或模型eval，也没有声称学习任务成功。

验证目录：`experiments/kitchen_platform_v3/vla/smolvla_replay_export_smoke_20261109/`；代码边界、相机契约和历史模型结果见[SmolVLA试点](SMOLVLA_PILOT_CN.md)。
