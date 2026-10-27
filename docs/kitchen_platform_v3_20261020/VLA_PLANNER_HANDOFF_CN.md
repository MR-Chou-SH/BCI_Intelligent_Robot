# Kitchen V3：VLA / 轨迹规划开发交接入口

日期：2026-10-23。**这是接手同学最先读的文件。** 仓库分支 `feature/kitchen-v3-vla-planner-handoff`，目标是在不依赖 Quest 的情况下运行现有环境、理解两条真物理参考，随后开发自己的原子任务。原 Long-run Goal 保持 PAUSED；本交接不是恢复科研自动运行。

## 1. 先认识交付边界

```text
外部规划器 / 未来 VLA -> PlannerClient -> TCP Planner API -> MuJoCo + 受限控制 + 物理记录
                                                        |
                                                        +-> World TCP -> Unity / Quest Research
已核验 MuJoCo 历史记录 -> 离线回放资产 -> Quest Demo（独立应用）
```

| 成果 | 当前边界 |
| --- | --- |
| MuJoCo 双 FR3 + UMI、厨房、水槽/篮子/水龙头、微波炉/门/按钮、中央托盘/轮椅桌板 | 候选013，真实接触、重力、摩擦、关节/速度/接触门禁；未擅自 canonical 晋升 |
| 最新苹果251.76s | 完整内部动力学 PASS，声明水柱全机器人几何避水审计 PASS；不是流体仿真，也不是完整公开API PASS |
| 汉堡128.32s来源 / 128.72s公开run04 | 旧2ff动作来源经当前b672 API执行，加200真实等待步；完整门/加热/取放 PASS，终点仅炉外持物，不是托盘释放/递送 |
| Research随机化 | 水果限定左侧，主食组限定右侧，完整形状/支撑/间隔/基础可操作性检查、seed复现、新episode和失败保护；不保证每个随机局有成功策略 |
| Quest | 用户已报告 Research 联机/STREAMING/随机重置通过。新Demo记录包仍须用户佩戴观看；PC检验不冒充真机性能验收 |
| 双臂托盘 | 既有抬升/承载组件成果保留；完整递送不能宣称已通过 |

先读 [Planner API 中文说明](PLANNER_API_GUIDE_CN.md)，再读 [两条参考轨迹](../../reference/kitchen_v3_trajectories/README.md)。Quest 操作另见 [用户手册](RESEARCH_USER_GUIDE_CN.md)，它不是主要开发文档。

## 2. 新 Windows 电脑部署

需要 Windows x64、Python **3.12.14 x64**（实测版本）、Windows PowerShell **5.1**、Git。核心固定 MuJoCo **3.12.0**、NumPy **2.5.3**、h5py **3.16.0**，完整闭包见 `robot_arm/requirements-kitchen-research.txt`。无需 Unity、Quest、Torch、LeRobot 或 PowerShell7。另一机器和不同GPU还须实际验证，不能继承原机硬件PASS。

```powershell
git clone --branch feature/kitchen-v3-vla-planner-handoff --single-branch https://github.com/MR-Chou-SH/BCI_Intelligent_Robot.git KitchenResearch
Set-Location KitchenResearch
py -3.12 -m venv .venv
$Python=Join-Path (Get-Location).Path '.venv\Scripts\python.exe'
& $Python -m pip install -r robot_arm\requirements-kitchen-research.txt
& $Python -B -m integration.kitchen.research_deploy_probe --inventory --compile-model
& $Python -B -m integration.kitchen.planning.reference_trajectories inspect
```

Python版本请用 `& $Python --version` 确认，不要在原开发者 D 盘目录寻找虚拟环境。依赖安装需网络；模型资产已随仓库提供，运行不从网络下载机器人。MJCF 引用的44份网格与模型、FR3 Apache-2.0 / UMI MIT许可须一起保留。`robot_arm/kitchen` 和 `integration/kitchen` 是核心，不使用旧 M9 自动策略代替厨房接口。

### 正式启动（PS5.1可直接运行）

在克隆根运行；已存在环境时可将 `$Python` 改为你自己的解释器。新目录自动生成，不覆盖旧数据。

```powershell
$W=(Get-Location).Path
$Python=Join-Path $W '.venv\Scripts\python.exe'
$Run=Join-Path $W ('experiments\kitchen_platform_v3\research_'+[guid]::NewGuid().ToString('N'))
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$W\scripts\start_kitchen_platform.ps1" `
  -PythonExe $Python -Layout kitchen_v3_norice_candidate_013 `
  -InitializationProfile research -Mode manual -Seed 0 -BindHost 127.0.0.1 `
  -PlannerPort 11031 -WorldPort 11032 -DataRoot "$Run\data" -ReadyFile "$Run\ready.json"
```

保持服务窗口运行，实际出现 `PLATFORM_READY` 才算两个端口已绑定。启动器历史默认005，本说明必须显式013；未修改旧默认。DataRoot/ReadyFile必须位于**本克隆** `experiments/kitchen_platform_v3/` 之下，不能为方便去掉路径/链接保护。普通新克隆不需要旧worktree或旧Junction；大记录可使用经过检查的专用D盘部署目录，不复制个人旧实验目录。原机既有Junction不可删除/重建。

只在要连接Quest时，将BindHost换为本机可达LAN IPv4，且Research APK目标地址/11031/11032与服务一致。IP不是永久值，应用当前无IP输入框，变更网络需重新配置Research客户端。仅用PC的规划器一直可用127.0.0.1。端口占用先识别已有服务，不盲杀其它项目Python。

### 一个命令完成实际接入烟雾测试

不先启动上面的服务也可运行下式。脚本自行通过PS5.1启动新临时服务/动态端口，跑0→1→0、World、状态/对象/预检、小幅动作和20物理步、反馈与记录校验，然后只停止自己创建的进程。

```powershell
$Python=Join-Path (Get-Location).Path '.venv\Scripts\python.exe'
$Tag='handoff_'+[guid]::NewGuid().ToString('N')
& $Python -B -m integration.kitchen.platform_research_handoff_example --launch `
  --powershell "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" --python $Python `
  --seeds 0 1 0 --settle-substeps 20 --audit-geometry --read-records `
  --data-root "experiments/kitchen_platform_v3/$Tag/data" `
  --ready-file "experiments/kitchen_platform_v3/$Tag/ready.json" `
  --output "experiments/kitchen_platform_v3/$Tag/client"
```

退出码0、`PC_RESEARCH_HANDOFF_PASS`才算完成。结果的 task_success 仍NOT_VERIFIED，因为烟雾动作不是厨房任务。若失败先读该目录report/stdout/stderr，不通过删旧记录重跑“消除”失败。当前实际验收见 [发布检查](HANDOFF_RELEASE_VERIFICATION_CN.md)。

## 3. 第一条请求与新原子任务

第二个PowerShell窗口可执行 `& $Python -B -m integration.kitchen.planner_client --host 127.0.0.1 --port 11031`，这是会 reset0、预检、执行左q1+0.002rad、推进20步并hold的**有限例子**；会终止当前局，仅在没有其它规划器写操作时使用。只读连接代码与所有写接口见API说明。

实际开发顺序：读manifest/observe并核对身份 → query_objects → 目标位姿/夹持/支撑设计 → IK与连续碰撞 → 25Hz路标 → contact有限预演 → 小批执行/step → 持续接触与终态判据 → 封存证据。不要直接写qpos、门关节或夹爪附着。manual场景静止是正常的：没有step就没有机器人自动执行。

设计第三个原子任务时，先做一个小而独立的目标，如“固定局抓起热狗承载物并持续持有”，不一开始复制整套组合任务。使用独立任务ID/判据版本，不改旧16个原子/12个组合任务编号；米饭已在新版本移除，旧目录保留但明确unsupported。任务语言、规划、成功标签和批量策略属于接手者开发范围。

## 4. 参考资产到底包含什么

| 路径 | 内容 / 如何使用 |
| --- | --- |
| `reference/kitchen_v3_trajectories/manifest.json` | 公开轻量文件的长度和SHA；加载前检查 |
| `apple_controls_25hz.jsonl` | 6294个真实接受目标，每帧16维、20步、预期测量状态和允许设备接触几何；不含1.9GB原始接触日志 |
| `apple_metadata.json` | 物理来源哈希、11项判据、阶段、避水合同和原始控制身份 |
| `burger_public_calls.jsonl` | run04成功写RPC的method/args和时钟，已去token/个人路径；含实际contact契约和有限等待，不是仅关节表 |
| `burger_metadata.json` | 源2ff/目标b672身份、物理窗口、阶段、200步等待时钟映射、终点范围 |
| `integration/kitchen/planning/reference_trajectories.py` | inspect、不依赖个人路径的有限前缀运行器、显式外部原始文件的提取器 |
| `reference/kitchen_v3_trajectories/historical_source/` | 实际动作生成/控制思路及来源SHA；仅阅读，不执行其依赖旧Goal/旧目录的CLI；新交接入口不依赖历史D路径 |

**先用inspect，再按参考README运行100帧前缀。** 两条都只适用fixed/seed0，不可直接对Research随机局发送。苹果完整新加载器复演和汉堡整段新提取RPC复演本轮NOT TESTED；历史PASS与新代码有限前缀PASS分开记录。真正复现全流程后，还须逐步重新审计原判据，不能只看脚本退出码。

## 5. 记录与交付清单

核心源码：`robot_arm/kitchen/`、`integration/kitchen/platform_server.py`、`planner_client.py`、接入/参考示例；配置：layouts及requirements、任务catalog；模型：`robot_arm/assets/fr3_umi_merged.xml`及被引用网格；许可：`robot_arm/licenses/`。Unity界面/构建源码供理解显示行为，**不是PC依赖**；本分支不包含带原机路径的完整Demo回放资源和Unity模型导出包，从克隆重新构建Quest应用需另行获取封存资源，不能宣称仅克隆即可重建APK。APK、原始500Hz轨迹、Unity Library、缓存、虚拟环境、个人日志、凭据与旧科研实验不进入本分支新提交。

`.gitattributes`固定厨房源文件字节，并将已批准MJCF明确设为CRLF，防止Git全局autocrlf改变模型/控制身份。不要手工换行或格式化冻结源码后仍引用旧control_hash；新克隆自检须与已批准身份一致。

APK与完整原始证据另行向维护者申请私下交付。用参考metadata中的SHA核验，不凭文件名。汉堡公开完整证据由多个episode组成，应使用完整raw manifest；不要只交最后一个physics.jsonl。未拿到原始大日志仍可运行核心、查看轻量轨迹和有限执行，但不能宣称已独立复核全部历史接触窗口。

每局保存episode_id、seed、init/model/control hash、任务版本/指令、RPC及500Hz证据、独立成功/失败判据与原因。不要将COMPLETED自动写成成功标签。正常结束先读反馈、封存，再Ctrl+C关服务；未封存记录标不完整。旧大数据与旧Goal工作树不可清理。

## 6. 第一周任务清单

| 顺序 | 有界任务 | 结束条件 |
| --- | --- | --- |
| 1 | 新克隆安装、PS5.1自检、0→1→0、20步真实动作 | 保存烟雾报告、模型身份与自己的机器环境；不需要Quest |
| 2 | 阅读两个参考的阶段/16维/接触/终态，跑短前缀 | 正确区分来源、公开API、内部控制器和未验证全段 |
| 3 | 设计一个独立原子任务与物理判据 | 固定局的小规模真实接触证据，不改共享安全门禁 |
| 4 | 把该任务目标改成由query_objects计算 | 在有限随机seed验证并记录失败，不声称任意泛化 |
| 5 | 定义只读视觉观测扩展与采样对齐 | RGB/相机标定/图像动作同步规范、独立验收计划；不修改当前接口语义 |

尚无经过验证的公开RGB/内外参/图像—动作同步、通用规划器、批量专家数据生成或VLA训练。不要因为已有旧相机类或Quest画面就称完整VLA数据平台。冻结接口/控制源参与control_hash；变更应独立版本、审批和新证据，不能为跑通参考而改阈值或冒用旧hash。
