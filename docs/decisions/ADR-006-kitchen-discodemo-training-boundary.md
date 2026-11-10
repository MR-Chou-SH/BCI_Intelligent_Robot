# ADR-006：厨房DiscoDemo训练与原CPU采集边界

日期：2026-11-09。状态：按用户指定方法开展的工程试验；训练副本尚未验证完整任务迁移。

## 背景

用户要求用DiscoDemo增加专家示范多样性，并用MuJoCo Warp训练状态策略，保持SmolVLA独立环境和原采集数据契约。上游训练器依赖Isaac，而厨房已有MuJoCo模型、控制器、随机初态和严格成功条件。

## 决定

- 从用户下载的DiscoDemo只读复制独立FlashSAC、混合经验buffer、逆向课程及METRA模块，逐文件SHA校验；不复制Isaac场景/运行器。
- `robot_arm/kitchen`负责状态/物理/限幅；`integration/kitchen/rl`负责任务/环境/缓存/CPU数据出口；`thirdparty/discodemo`持有算法适配与训练入口。
- RL可选依赖在项目`.venv`，VLA仍在独立环境。状态真值仅供RL，不进入视觉VLA观测。
- Warp3.12拒绝noslip，且当前圆柱底座接触近似使严格终态计时无法成立。仅在显式双开关下使用关闭noslip、苹果底座内接箱形接触代理的隔离训练副本，绑定两份模型SHA；不修改原MJCF、CPU模型或任务阈值。
- 正式生成从正常随机初态执行冻结策略，全部通过原CPU控制器实测guard、任务判定、独立replay才加入既有H5/标注/manifest。GPU成功不是CPU成功证据。
- 同初态不同latent共享split组，各有variant ID；禁止把同组变体拆到训练/验证两侧。

## 影响与验收限制

当前GPU环境仅L1 apple，设备清洗/加热逻辑未迁移。20示范×16world短程更新只能验证工程链路，不能代替完整任务成功率和多样性评估。接触代理引入物理域差异，若原CPU迁移失败，需继续改善训练物理或方法，而非降低导出门槛。

checkpoint保留replay、未提交n-step队列、UID/gate、课程/RNG/物理状态；GPU恢复时重算接触缓存，允许数值差异，原H5独立CPU重放仍保持原严格标准。

见[实施计划](../data_diversity/DISCODEMO_KITCHEN_IMPLEMENTATION_PLAN_CN.md)和[运行说明](../data_diversity/DISCODEMO_KITCHEN_RUNBOOK_CN.md)。
