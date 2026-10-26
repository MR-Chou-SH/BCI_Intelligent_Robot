# Kitchen Linux 迁移、局部随机化与采集分段

用户授权在新分支开发，保留左水果／右主食布局，仅做小范围初态随机化。基线：仓库`/home/firedust/brainInteract/BCI_Intelligent_Robot`，原分支`feature/kitchen-v3-vla-planner-handoff`，HEAD `4815510de05e5dc55d529b4b0bf40668e02afbe9`，上游同名origin分支。开始时已有未跟踪`robot_arm/MUJOCO_LOG.TXT`和`robot_arm/outputs/`，未清理或纳入本轮改动。新分支`feature/kitchen-linux-task-collection`，未commit/push。

## 实现

- 用已有CPython3.12.12创建项目`.venv`，安装原厨房requirements全部十个锁定包；系统Python未改变。
- 新增原生Python启动器`integration.kitchen.launch`，复用原服务，不依赖PowerShell；限制新run路径、防止覆盖和符号链接逃逸。接入烟雾脚本在Linux默认选择该启动器，Windows仍默认PowerShell。
- Research初态改成围绕每组名义位置x/y各±8 cm、yaw±5°，保留原完整形状、支撑、间隔、静态IK及净距检查，主食和承载物成组移动。算法和control/init身份随源码更新。
- 新增`integration.kitchen.collection`：16原子、12组合和图片8用例的可检查图；PlannerClient外层按真实sim_tick分段，记录RPC与设备事件，关联多个execution物理文件，校验封存链、子步连续性、episode和分段证据覆盖。异常退出保留未完成记录并hold。使用独立旁车schema，不赋予训练导出资格，不改变旧HDF5/LeRobot门禁。

## 验证与诊断

1. Linux实际模型编译和包清单成功：MuJoCo3.12.0、NumPy2.5.3、h5py3.16.0。Linux模型指纹与Windows历史参考不同，未修改旧metadata或加载器身份拒绝。
2. 小窗口诊断：seed0在±3 cm和±5 cm时，西红柿64次静态接近检查均失败；±8 cm通过。seed0–7均通过局部初始化，但不是完整随机任务成功率。一次临时诊断脚本错误地把机器人恢复到`model.qpos0`而不是build_scene配置的home，导致所有组失败；修正测试初态后上述8个seed通过，生产代码未因此放宽门禁。
3. 首次TCP烟雾受沙箱socket禁令阻止，失败数据留存。获得本机回环运行批准后，`linux_acceptance_20261024_retry/client/report.json`返回`PC_RESEARCH_HANDOFF_PASS`：0→1→0复现、非法seed原子保护、重复请求幂等、旧token拒绝、World同步、微小动作20真实子步、记录链读回、两个自有端口清理均通过。
4. ` .venv/bin/python -B -m unittest discover -s integration/kitchen/tests -v`：**11/11 PASS**，包括真实动力学跨两个文件40子步采集、文件篡改和错误episode拒绝、异常保留、随机初态边界/分组刚性/设备保持、采样失败不提交、启动路径和参数保护，以及历史参考资产完整性。
5. `git diff --check`通过。历史参考inspect通过，未执行旧长轨迹或操作硬件。完整原始烟雾数据保留在本地、由gitignore排除。

## 后续与边界

详见[Linux任务采集指南](../kitchen_platform_v3_20261020/LINUX_TASK_COLLECTION_CN.md)。本轮没有实现全套原子专家运动、任务专用初态准备、独立物理成功判据或RGB训练导出。下一步应先在新Linux身份下逐原子重建/验证目标相关的轨迹，再接图中分支与长任务；B01完整递送仍未证明。没有恢复旧Goal、没有Quest/ND8/物理机器人操作，没有新的研究结论。
