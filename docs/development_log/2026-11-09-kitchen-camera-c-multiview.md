# C全景与双腕相机槽位

用户从正对预览中选择C，并要求三路槽位、左任务先关闭右腕。默认配置位于`integration/kitchen/vla/cameras_left.json`：全景C和左腕开启，右腕关闭；全景位置`[0,-2.10,2.20]m`、目标`[0,-0.18,0.90]m`、垂直视场50°，启用图像均512×512。

离线渲染、真正LeRobot导出、训练、checkpoint相机契约、在线RGB协议和MuJoCo观测均支持按启用键读取多视角。关闭视角不分配渲染器、不存PNG、不输入占位黑图。视角顺序规范化，缺少启用视角、包含关闭视角、相机姿态或图像尺寸不符均拒绝。相机姿态仅作用于渲染，不修改MJCF或物理模型；原H5不变。旧单视角sidecar/checkpoint保留兼容，但须显式使用匹配的旧相机配置，不能直接改用新默认输入。

本轮证据：`experiments/kitchen_platform_v3/vla/smolvla_c_left_20261109/verification.json`。

- 原L1 apple轨迹723个动作时刻，双视角各723张，源H5 SHA256未变；右腕无图像。
- 768分辨率实际Camera渲染与先前C预览逐像素一致；渲染前后live qpos/qvel/time及全局FOV不变。训练/在线512分辨率使用相同姿态和视场。
- 导出后真实LeRobot读取：state16、action chunk50×16、两路RGB各3×512×512，没有camera3 feature。
- RTX4060 CUDA两步训练完成，峰值1.9445GiB。loss从0.3058到5.9462；这是功能检查，样本过少且没有收敛或成功率证据。
- 重载新checkpoint，3s lockstep RTC运行75帧，报告2个已用推理块（审计计入最后未使用请求共3次），无保护触发、无过期块/下溢。任务未完成，75帧均限幅，不能据此证明策略效果、连续性或实时性能。第一次联调早于服务ready，`eval_smoke`保留启动时序错误；成功交换图像与动作的接口检查为`eval_smoke_ready`。临时服务已关闭。
- 全部129项Kitchen测试通过，日志`/tmp/kitchen_camera_c_tests.log`。

统一命令仍见[SmolVLA试点说明](../kitchen_platform_v3_20261020/SMOLVLA_PILOT_CN.md)。`render`、`rollout`默认C＋左腕，可通过`--camera-rig`指定配置；导出和训练继承源配置。三路同时启用的资源/效果尚未验证，不能套用单视角或本轮双视角测量。
