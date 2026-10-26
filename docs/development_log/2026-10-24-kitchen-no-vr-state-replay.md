# 无VR状态查询、初态录制、控制重放与MuJoCo视频

在既有`feature/kitchen-linux-task-collection`分支继续开发，HEAD仍为`4815510`，保留上轮全部改动和用户原有未跟踪文件；未commit/push。用户明确不使用VR，暂不采图像，需要通过MP4或MuJoCo viewer检查轨迹、获取物体状态、保留完整初态并能够重放。

## 实现

- API1.3.0新增`get_object_states`、`get_replay_snapshot`、`start_state_recording`、`stop_state_recording`；原接口保留。read-only查询在MjData副本上forward，返回与当前qpos一致的位姿、速度、接触支撑、设备关节和模拟设备标记；支持主食及自身承载物的联合位置／加热字段。
- 连续状态轨迹与单次execution物理证据独立：完整初始`mjSTATE_INTEGRATION`、500Hz实际执行器输入、25Hz完整状态及物体查询、最终状态、封存SHA链。没有执行队列的等待也录入；录制期间拒绝reset。
- 初态区分reset时布局与实际开始录制的状态，保存seed、初态hash、模型身份、控制身份、软件版本；新录制按声明哈希封存控制源码副本。录制途中不因原子标注结束而中断。
- 重放有三条独立路径：只读审计；恢复保存状态后用viewer／EGL MP4观看；从完整初态按实际执行器输入重新mj_step并比较qpos/qvel。中间检查点不用于纠正重新仿真的状态。
- 新增`state_demo`公开API接入示例，自己的动态端口、本机服务、完整RPC日志和有界清理。小运动和fixed seed0历史汉堡调用序列的复验分开标记；不关闭历史精确身份检查，不继承任务PASS。
- 随机化新增全部36对可动物体的有符号碰撞几何距离审计；保持左水果／右主食，食物仅与自身承载物允许无穿透支撑接触。

## 验证

15项测试通过，包括：只读状态查询不改变live integration state；非法物体ID；非零开始tick；等待与动作间隙；非20步结尾；源码副本哈希；截断拒绝；重新计算合法哈希后改变控制输入仍被动力学重放检出；强制物体重叠反例；规划器异常保留与停止；旧资产完整性。`git diff --check`通过。

seed0–7的36对物体审计全部通过，报告保存在`experiments/kitchen_platform_v3/linux_acceptance_overlap_audit_20261024/report.json`。这是初始化几何证据，不是任意seed／任务成功保证。

小运动接口验收先后产生两个独立run，最终版为`linux_acceptance_state_final_20261024/report.json`，4200步／8.4秒／211检查点，qpos/qvel最大误差均0，自有服务退出码0，无图像。首个视频为`linux_acceptance_state_demo_20261024_retry/review.mp4`，ffprobe核验H.264、960×540、25fps、210帧、8.4秒；抽帧确认厨房、双臂和左右物体可见。EGL／ffmpeg使用已有系统组件，未安装系统依赖。交互viewer入口已实现，本轮未自动打开用户桌面。

## Linux兼容问题

首次汉堡复验在tick13420（26.84秒）被`INVALID_BOUNDED_NONROBOT_SUPPORT`拒绝。定位为`numpy.int32`与MuJoCo pybind枚举的集合成员比较不对称：显式相等比较为True，而tuple成员比较仍拒绝合法BOX。修复只将类型和值统一转普通int；允许形状、支撑关系和全部力／速度／净距阈值保持原值。回归测试验证合法箱体支撑通过、平面和自身几何仍拒绝。

失败数据保留在`linux_acceptance_burger_state_20261024/`，13420步／672检查点控制重放误差均0；失败任务与数据可重放性分开。修复后另启`linux_acceptance_burger_state_20261024_retry/`，其运行时控制源码已在修改后续只读查询字段前按对应control hash封存到`source_snapshot/`。该轮结果以其report为准。

## 边界

修复后的完整汉堡新运行已返回`PC_REFERENCE_RECORD_REPLAY_PASS`：1329次调用、64360真实物理步、128.72秒、3219个检查点，控制重放qpos/qvel误差均0，自有服务退出码0。模拟设备反馈heat_started tick39724、heat_complete tick42225，最终右爪双侧接触burger_container且在炉外；没有完成托盘放置或递送。报告及数据位于上述retry目录，视频导出使用相同状态轨迹与模型，4倍速不改原始数据时钟。

完整视频`burger_review_4x.mp4`已生成，ffprobe确认H.264／960×540／25fps／805帧／32.2秒，文件533616字节；来源轨迹SHA为`a94e91da52dac58cdaac494466ab9dd80b096d272b6e62d1f18687c9c7c9b849`，相同信息保存于`.video.json`。抽查14秒（源56秒）和31.8秒（源127.2秒）画面，可见炉内放置阶段和最终炉外持物阶段，未看到虚构托盘落放。另增只读`state_query`命令行入口并检查help，可直接查询／另存状态而不触发reset或动作。

MP4为状态回放查看产物，不进入采集数据，不是新的动力学证据。record/replay通过不代替接触任务成功判据。完整随机场景专家策略、全部原子、托盘放置及递送仍需后续逐项验收。没有操作Quest、ND8或实体机器人，没有恢复旧研究Goal。

操作指南：[状态采集与重放](../kitchen_platform_v3_20261020/STATE_RECORD_REPLAY_CN.md)。所有生成轨迹、接触日志和视频保留在本地被忽略的run目录中。
