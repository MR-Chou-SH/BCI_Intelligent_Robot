# Kitchen V3 交接发布验收

日期：2026-10-23。本报告只记录这次有界交接，不恢复原 Long-run Goal（PAUSED），不晋升候选013，不把历史PASS改写为新代码完整PASS。首先阅读 [开发入口](VLA_PLANNER_HANDOFF_CN.md)。

## 1. 验收结果与边界

| 项目 | 结论 | 实际证据 / 边界 |
| --- | --- | --- |
| 独立解压目录PC启动 | VERIFIED | 从不在Codex worktree内的独立源码目录，经系统Windows PowerShell5.1调用真实启动器；PLATFORM_READY后连接Planner和World，无Quest |
| Research多回合 | VERIFIED | seed0→1→0→auto，87次RPC；完整形状/区域/支撑/间距/基础IK与20子步稳定检查；同0初始化hash一致且episode不同；非法seed和忙执行reset保护；读取并核验真实记录 |
| 同seed0初态指纹 | VERIFIED | `dc4f132967b448f3047daace1e747f0d042fc94da320bdc7704ef6d8666d26bf`；只在同模型/同初始化版本下比较 |
| 当前控制身份 | VERIFIED | `b6722bb912fddedf853d54925fda092b8ea2833cbcaeb904040ecde5965335d2`；独立目录编译与原工作树一致；未修改冻结控制/安全阈值 |
| 当前场景身份 | VERIFIED | `f52f3483a7d4863ead908a98aa2aae726f13b5f67e6468a1ed5323c5f3771e27`；nq84/nv75/body41/geom241/camera10 |
| 苹果轻量参考执行 | PARTIAL | 新加载器在真实内部控制器执行100帧/2000物理步，实测16维状态对封存源最大误差0；完整6294帧新复演NOT TESTED，完整公开API执行NOT VERIFIED |
| 汉堡轻量参考执行 | PARTIAL | 独立目录、真实PS5.1 fixed/seed0/manual服务，公开API执行100帧/2000步，41个成功写调用；新提取整段3218帧NOT TESTED |
| 历史完整物理任务 | VERIFIED，历史范围 | 苹果251.76s内部完整流程+声明水柱避水审计；汉堡公开run04完整流程终点仅炉外持物。不是新提取器整段复演、随机泛化或托盘递送PASS |
| 参考资产契约 | VERIFIED | SHA/时钟/6294苹果目标/3218汉堡目标/四类contact/旧新控制身份/不扩大历史结论；4条Golden Contract通过 |
| Research中文手册 | VERIFIED，源码层 | U01–U39共39项固定界面字段（含1项PC专用），动态错误另述；5份运行源码SHA与既有Research APK构建报告匹配 |
| 新Demo PC | VERIFIED | 完整苹果和原汉堡连续回放、逐body LERP/SLERP、来源拒收、面板/坐姿/倍速检查通过；不是Quest新包真机验收 |
| 新Demo APK | VERIFIED，构建/资源层 | Unity短路径导出+离线Gradle，签名和包身份通过；从实际APK的UnityFS解包核验完整苹果/汉堡/catalog/provenance，以及level0选择的新catalog |
| 远程分支与克隆 | VERIFIED | 非强制推送后回读GitHub实际HEAD，独立重新clone并通过Research、模型、参考数据、两条100帧真实前缀验收；详见第5节 |

PC独立部署实测Python3.12.14、MuJoCo3.12.0、NumPy2.5.3、h5py3.16.0；复用已安装解释器，**未声称另一台电脑全新下载依赖/GPU或Quest通过**。本轮临时服务均有限运行并在检查后关闭，不替用户保持生产服务。

## 2. 新Demo身份

| 字段 | 值 |
| --- | --- |
| 本机独立交付目录 | `D:\VLA_Kitchen_Data\KitchenDemo_APK\20261023_apple14_clear\KitchenDemo.apk`；仅原开发机路径，不是新电脑依赖 |
| package / version | `org.bci.kitchendemo.v3` / versionName `0.2.1` / versionCode `3` |
| APK SHA-256 | `cc519a07c3eba726c8223a3c8eb2e7252de4a4affd2e204d60ebdf7177fa7be3` |
| 签名 | Android debug证书，v2有效；证书SHA-256 `769d24a051f01da80579d20ae9f1b6df43349f63d1ba58ce113d61910b44bbc8`；不发布私钥，不当生产签名 |
| 苹果原始记录 | `4e7ccecda30014113c59df98b2bdab70507345c0f2e5dd79ed6ae4513dfe228c`，125880物理步，1x源时长251.76s |
| 包内苹果bytes | `a02dfdcdf062764a7f7a4ab513fdfc549b9e470e3cfc83022e07a580886a96da`，6296个显示采样，不等于6294个25Hz控制目标 |
| 包内汉堡bytes | `3a029675b79ab80ed42dc3f2ff088bc9addfffe6d9d2c8f5471f64d81c111f98`，与旧包来源文件字节一致，未重算汉堡轨迹 |
| 选择的catalog | `KitchenDemoV3Clear20261023/catalog`，SHA `b68aeb6962ec88995165dff80992d0ec7543eb779559c68597feeb103811d9b2` |
| 实际APK数据容器 | `assets/bin/Data/data.unity3d` SHA `c41d053bbdd38b1fe539b47f7e91c759754abb710e6d9f5a3f14a3f42424c252`，与本次Unity导出一致 |

1x编辑器完整苹果墙钟251.7669963s；持续检查37848帧/1551768个body pose。有限阶段截图采集曾产生Editor回调间隙，不能将此结果当Quest帧率/舒适度测量。汉堡128.3269175s/20360帧/834760个body检查。坐姿/近面板/呼出收起/1x2x4x保留；加速仅显示，不改原物理时间。

原Demo与Research APK、原始苹果及所有旧证据保留。Research UI/网络/API/随机化未修改，Research APK未重建。用户既有Research真机联机/STREAMING/reset验收保留；新Demo仍待Quest佩戴观看新水龙头路径、文字/射线操作和性能。

## 3. 公开内容与独立数据

只选择本次需要的PC源码、布局/任务配置、44份已引用机器人网格和既有许可、轻量参考/历史生成代码、Unity界面与构建源码、中文交接文档。模型资产已在既有公开基线；FR3 Apache-2.0 / UMI MIT许可继续保留。无新下载、许可不明的第三方美术资产。

不新提交APK、500Hz原始大记录、Unity Library/完整私有回放资源、缓存、虚拟环境、个人配置或旧实验目录。Unity源码是显示行为参考，**不是完整Quest资源交付包**；PC部署资产完整，重建Quest须另外索取封存资源。历史生成代码仅两处个人路径公开副本脱敏，原始/公开SHA分别在generator_sources中保留。

原始证据与APK在原开发机单独交付，参考metadata列出完整来源SHA与所需文件，不能因没有大日志就声称独立重审了全部接触。源码部署/有限参考执行不读取个人D盘。Git属性固定源字节和MJCF换行，避免克隆改变已批准身份。

本地完整验证报告、RPC、初态文件、物理记录和APK资源解包证明单独保存在维护交付目录；公开报告只发布脱敏事实和哈希。分支不合并main、不force push、不清理旧目录。

## 4. 明确未验收

- 任意随机场景下的完整抓放策略、两条新加载器整段任务复演、苹果公开API完整任务、双臂完整托盘递送。
- 新Demo包Quest实际帧率/舒适度、所有数字键盘/手柄使用细节、另一台电脑的新依赖安装和局域网配置。
- 公开RGB、相机内外参、图像与动作时间同步；现有相机数量/旧相机类不代表这些接口已实现。
- VLA模型、通用规划器、批量专家策略、自动成功标签和完整训练数据生产系统。

下一步由接手者按开发入口开始自己的原子任务；本交接结束后不自行启动上述研发。

## 5. GitHub发布与真实远程克隆补记

分支：`feature/kitchen-v3-vla-planner-handoff`。已验收源码提交：`64fae693e24965f7da392a890327c68dd98333d2`。GitHub回读分支HEAD与它一致；main仍为`61f949f6462a48961377d3a1e8623c4178a3e968`，未合并或强推。此后补记提交仅修改本文档；最终HEAD以远程分支为准。

从GitHub新克隆（非本机拷贝/本地Git链接）完成以下验收，解释器复用原机已安装环境，未依赖原工作树、Quest或旧Junction：

| 克隆中执行的入口 | 结果 |
| --- | --- |
| `research_deploy_probe --inventory --compile-model` | VERIFIED：模型source/compiled/scene/control身份均与封存身份一致；Git属性成功保护CRLF身份；依赖/44网格完整 |
| `platform_research_handoff_example --launch`，指定系统PS5.1 | VERIFIED：PLATFORM_READY、87次RPC、0→1→0→新seed、17对象查询/41body World、完整外形区域/支撑/间距检查、20步有限动作、反馈及封存记录一致 |
| 重置失败保护 | VERIFIED：非法种子不改当前局，过期token拒绝，重复请求幂等，运动忙时reset不破坏状态；相同0初态hash一致、episode_id不同 |
| `unittest integration.kitchen.tests.test_reference_trajectories` | VERIFIED：4条契约通过，包括生成器快照/轻量数据SHA、16维目标、时钟、contact与身份边界 |
| `reference_trajectories apple --max-frames 100` | PARTIAL：真实2000子步、状态最大误差0、证据封存；不是新完整流程PASS，也不经公开API |
| `reference_trajectories burger --max-frames 100` | PARTIAL：独立PS5.1 fixed服务、41次成功写RPC/2000步，正常有限退出；不是新完整流程PASS |
| 临时服务清理 | VERIFIED：只关闭本轮自建进程，Research与汉堡测试端口均不可再连接，未停止其它项目服务 |

克隆内证据位置相对源码根，原始日志单独保留，不新提交Git：

| 文件 | SHA-256 |
| --- | --- |
| `experiments/kitchen_platform_v3/remote_clone_smoke_01/client/report.json` | `772ae0fc7c95b91d882c7b019d967b777fdbd4a27dfec6a75a3ddff8f604bddb` |
| `experiments/kitchen_platform_v3/remote_apple_prefix_01/result.json` | `3115e39371fafb08b85e956fb2cdb2313a027e35dea09b73437d4d1dcead5c13` |
| `experiments/kitchen_platform_v3/burger_reference_smoke_01/wrapper_result.json` | `68ef4ed22008eb636c2a230fbeebff9764b873fc548bc7678f2ac041db92f8a2` |

实际暂存内容公开检查：94个文件约5.9MB；未发现新增个人Windows目录、内网地址、密钥/token值或大型原始记录。必要许可保留；历史已公开基线未改写。4段Python示例通过语法检查，10段PowerShell示例通过系统5.1解析，文档相对链接可解析。封存控制源的既有CRLF/尾空行与Unity生成meta空白保留，未为格式检查改变科研身份。公开内容不包括本机私有状态/完整Unity回放资源，边界见第3节。

本轮交接源码和关键Demo资源另有195文件/约53.6MB的本机新检查点，逐文件复制后SHA一致。旧APK哈希复核未变；正式Goal仍PAUSED。新Demo待用户Quest验收；不将这些PC结果升级成新头显或随机任务泛化PASS。
