# 2026-11-09：厨房DiscoDemo状态RL工程试验

基线`feature/data-collection-improve@395d8295e4669127f82722ae329e58ea261ae543`，保留已有VLA工作区。先写[详细实施计划](../data_diversity/DISCODEMO_KITCHEN_IMPLEMENTATION_PLAN_CN.md)，再实施；没有切分支、提交或改原始研究数据。

## 方法核对

本地PDF §4.1/Table2明确初态随机化，例如PnP香蕉/碗XY与香蕉yaw；固定场景图只用于观察同一场景的多种解法。参考用户下载的`.cache/upstream/DiscoDemo`，方法采用官方FlashSAC＋50%示范混合replay＋并行逆向课程＋每回合固定z＋成功门控METRA，替代此前RECAP/PPO设想。20条不同初态示范共同训练一个策略，不是每seed一个专家。

## 已实现内容

- 固定数值状态：12个可动对象/设备，位姿/线角速、设备q/dq/flags/计时、双臂q/dq/夹爪实宽/TCP/参考；RPC `get_privileged_state`和无JSON数组接口，同tick只读。无Torch依赖。
- CPU自动任务监视器沿既有100实例canonical chain推进，包含加热宏、准备顺序、送盘载荷保留；继续复用原物理成功条件。CPU/Warp交互首期仅L1 apple。
- CPU完整reset快照含integration state、bias、控制器、设备、任务计时/RNG；下一物理帧恢复逐值一致。课程新回合清零任务稳定计时，持物锚点不额外随机挪物体。
- 271维归一化状态，动作8维：左7关节增量≤0.7/25rad＋绝对夹爪开度，经过原关节/TCP/夹爪限幅，20子步/25Hz。GPU批量限幅和控制内核独立实现，原控制器未重写。VLA仍接收视觉和本体16维，不接收真值对象状态。
- 从原50条数据选20条初态分布较分散的苹果示范，逐条独立重放，保存每16控制帧完整锚点及离线经验，约5.5MB。原H5不变。
- 复用官方FlashSAC/课程/METRA；本机配置16world、batch128、replay50k、每交互1更新、非AMP/非全网compile；显式偏离论文资源配置。逆向采样以稀疏锚点为单位，预算按真实剩余帧+75。
- checkpoint含网络/优化器/表征/gate、在线replay、未提交n-step、控制/物理状态、课程/RNG/latent/UID。恢复重算GPU接触缓存，不保证bit相同。
- CPU冻结生成、guard、任务成功、H5/标注、独立replay及manifest隔离；同初态多z共享source_group，不同variant去重，保持划分边界。

## 实测

证据根目录`experiments/kitchen_platform_v3/rl/implementation_20261109/`，均为有界工程测试，未启动50M步或过夜训练。

| 测试 | 结果与边界 |
| --- | --- |
| SmolVLA batch1/8 | 同10k模型，2warmup＋8测量step；1.949/3.079GiB，1.709/2.401 samples/s，吞吐约+40.5%；默认改8，不声称成功率提升 |
| Warp近终点空动作 | 原圆柱脚仅关闭noslip仍摇晃，180帧/7.2s角速0.058–0.079，严格0.05判据失败；箱形训练代理后双world50帧/2s严格成功 |
| CPU/GPU初态与目标 | 271维状态最大差9.66e-6；8组饱和关节/夹爪命令限幅目标最大差2.25e-7；双world目标保持约86world控制帧/s，非全任务基准 |
| FlashSAC α0 | 128world transitions、50次更新、有限loss、近终点2回合成功；非正常初态成功 |
| DiscoDemo α0.3 | 128transition、50更新，gate由0到0.5；中间64transition checkpoint恢复到128，gate/优化器/replay等加载通过，数值不完全相同 |
| 20demo×16world | 2048transition、90次更新、32个结束回合中29个近终点成功，约247world控制帧/s；frontier仍在末段，尚无完整任务成功结论 |
| 原CPU数据出口 | 原专家动作经新8维适配执行444帧/17.76s，原成功条件通过；新H5独立qpos/qvel重放误差均0、16维动作可读回。这是动作传输/出口验证，不是RL成功 |
| 冻结短训actor出口 | 2新seed×2z×2帧超短预算，4次均按预期失败、manifest为空；验证失败隔离，不是正式成功率评测 |

依赖显式安装Torch2.11cu128、MuJoCo-Warp3.12、Warp-lang1.19及小型训练辅助库；原MuJoCo3.12、NumPy2.5.3、h5py3.16未升级。VLA独立环境未混入RL。首次Warp编译约210s，后续缓存明显更快。

最终`unittest discover -s integration/kitchen/tests`通过148项（30.9s），包含只读状态/精确CPU快照、100任务链逻辑、超时数据隔离与同初态不同latent分组。首次沙箱内运行仅HTTP socket测试被禁止，在允许本地socket的环境复跑通过；GPU验证也使用可访问CUDA的执行环境，没有以沙箱内CUDA不可见为由切CPU训练。

原模型noslip=2，Warp3.12不支持；3.15也拒绝noslip且依赖新版MuJoCo接口，故未升级原库。训练副本只在`--allow-noslip-variant --box-foot-proxy`下关闭noslip并用苹果底座内接箱形碰撞代理，保留原质量/惯量/底高；原MJCF/CPU保持不变。必须评估完整抓取的CPU迁移，不能把训练副本稳定视为解决所有物理差异。

当前验证范围不包含：完整RL任务收敛、跨初态泛化提升、有效多样性、VLA成功率改善、其他任务Warp训练。下一步应先延长α0基线，查看frontier是否持续回退及正常初态成功率，再比较α0.3的成功/多样性，最后才大规模生成训练数据。[命令与数据边界](../data_diversity/DISCODEMO_KITCHEN_RUNBOOK_CN.md)。
