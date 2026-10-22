# Kitchen Research 中文使用说明

版本日期：2026-10-23。面向首次使用的本科生、VLA 与轨迹规划接手同学。

本说明以当前已安装 Research APK 对应的 Unity 源码和 PC 服务源码为准，不按按钮英文猜测功能。本轮只编写说明，不修改 Research UI、APK、网络、Planner API 或随机化。正式 Long-run Goal 保持 PAUSED，独立手动启动服务不等于恢复 Goal。

## 1. 先理解三个程序

| 程序 | 负责什么 | 不负责什么 |
| --- | --- | --- |
| Kitchen Demo（Quest 应用） | 离线播放已核验的真实 MuJoCo 实验记录；可选择苹果/汉堡、暂停、重播、倍速观看 | 不在 Quest 上重新进行机器人动力学实验；2x/4x 是播放速度，不是机器人实际执行速度 |
| Kitchen Research（Quest 应用） | 联机显示 PC 当前场景；发送新回合或指定种子重置请求；显示连接和回合身份 | 没有 Demo 苹果/汉堡播放按钮，不会自动执行厨房任务，不独立随机化，不充当物理引擎 |
| PC MuJoCo 服务 | 唯一物理权威；建立双 FR3 场景、随机初始化、接触与安全控制、推进仿真、记录物理证据 | 不替外部同学生成通用规划策略或训练数据算法 |
| Planner API（PC 的 11031 端口） | 外部程序查询状态、预检动作、提交受限动作、推进仿真、获取反馈；Research 也通过它请求 reset | 不是自动完成任务的智能规划器；接口存在不等于任意任务已经物理通过 |
| World 流（PC 的 11032 端口） | 向 Unity/Quest 持续发送完整场景状态 | 只读，不接受机器人控制；收到帧不等于物理时钟已前进 |

简单记忆：**PC 算物理，规划器给动作，Quest 看结果并可请求换一局。** Quest 不是 PC 服务启动的必需条件。

当前保留苹果、梨、香蕉、西红柿、汉堡、热狗及对应合理承载物；水果只在左侧合法区随机，主食组只在右侧合法区随机。没有米饭。随机初态检查完整物体形状、边界、支撑、间隔与基础可操作性；这不证明任意随机局都有完整连续抓放路径。

## 2. 本说明对应哪一个 Research

| 项目 | 当前交接身份 |
| --- | --- |
| 应用名 / 包名 | Kitchen Research / `org.bci.kitchenresearch.v3`，与 Demo 可共存 |
| APK 版本 | versionName `0.2.0`，versionCode `2` |
| 原开发机 APK | `D:\VLA_Kitchen_Data\KitchenResearch_APK\20261023_ps51_lan\KitchenResearch.apk` |
| APK SHA-256 | `6740030803e196c0714c1b4a4d8cdc93a8354a7b537b08b911a4889245dc44c3` |
| APK 内置 PC 目标 | 构建时指定的开发机 LAN IPv4（公开文档不披露内部地址），Planner `11031`、World `11032`；**没有自动发现新 IP**。具体已安装包地址见本机交付记录 |
| 场景 / API | `kitchen_v3_norice_candidate_013` / `1.2.1`；候选 013 未因此自动成为正式冻结布局 |
| 真机边界 | 用户已报告 Quest 3 联机、STREAMING、基本随机重置验收正常。本次文档核对不冒充新一轮真机测试 |

APK 内置地址不是永久配置。换 Wi-Fi、热点、路由器或电脑后要重新确认 IPv4；当前 APK 没有 IP 编辑框。若 PC 实际 IP 改变，需要按现有 Research 构建配置更新连接目标并重新构建，不能只修改启动命令就让旧 APK 自动改地址。本轮不改 Research 包。本文所有 `<PC_LAN_IPV4>` 均是须替换的占位符。

## 3. 面板逐项翻译

以下 U01–U39 是逐字段覆盖清单：**39 项可显示的固定原文、状态和错误提示，其中 U03 仅 PC 显示**。任意服务器错误、系统异常正文属于动态内容，另列格式与解读方法，不能有限穷举成固定按钮。斜线 `/` 是分隔符，不是按钮；界面不显示 FPS、运行 IP 或完整机器人关节数值。

### 3.1 标题、输入和按钮

| 编号 / 英文原文 | 中文与实际用途，为什么显示 | 真实来源 / 点击时机与结果 |
| --- | --- | --- |
| U01 `KITCHEN RESEARCH` | 厨房科研客户端；防止误把 Research 当离线 Demo | `KitchenResearchControls.BuildUI()` 固定标题，无操作 |
| U02 `LIVE PC MUJOCO STATE` | PC MuJoCo 实时状态；说明当前画面来自 PC，不是离线影片 | 同一函数固定说明。PC 指个人电脑；MuJoCo 是物理仿真引擎名称。仅这行文字不能证明已连接，要同时看 U19 |
| U03 `QUEST NOT VERIFIED` | Quest 尚未核验；**仅 PC 预览的固定历史边界提示** | 由 `if(!Quest)` 创建，不在 Quest 面板上显示；不是动态检测头显的结果。用户已报告本版基本真机通过，但本轮不改这段旧文字 |
| U04 `NEW EPISODE` | 新实验回合；方便不退出应用就换一局 | 点击后向 PC 发送不含 seed 的 `reset {}`。PC 生成新 seed、初始化新回合，成功后换 episode 身份；不是开始自动做饭。连接同步完成、无待处理 reset 时才能点 |
| U05 `RESET SEED` | 用输入框中的种子重置；用于复现实验初始摆放 | 先输入整数再点；本地按 uint32 解析，通过后发送 `reset {seed:数值}`。即使仍填相同数字，也重新创建一个回合。不会读取上次保存的运动轨迹 |
| U06 `HIDE` | 收起大面板；腾出厨房观察视野 | Quest 专有。变成右下侧小状态条，不会暂停 PC、断网、停止动作或结束实验 |
| U07 `RECALL` | 重新呼出并定位大面板；方便转头后找回界面 | Quest 专有。把展开面板一次性放到当前水平朝向前方，不重置回合，不移动厨房，不重新定位坐姿原点 |
| U08 `MENU` | 打开大面板；收起后的小条入口 | Quest 小状态条中的可点击按钮，调用与 RECALL 相同的函数。**不是要求按手柄系统菜单键** |
| U09 输入框初值 `0` | 下一次指定 reset 的种子输入值；让人选择要复现哪一局 | Unity `InputField`，整数内容类型、最多 10 个字符。输入不代表已执行；必须点击 RESET SEED。它与下方已生效的 SEED 独立，不会在 NEW EPISODE 后自动填成新种子 |

### 3.2 回合身份和数值

这些是“当前 PC 确认的回合”的身份，而不是按钮。哈希和 ID 没有米、秒等物理单位。

| 编号 / 原文示例 | 中文、用途及为什么显示 | 数据来源、单位、何时变化、怎样读 |
| --- | --- | --- |
| U10 `EPISODE 3   0123456789abcdef` | 第 3 回合及其身份前缀；区分同一摆放的不同实验 | `observe.result.episode.episode_index` 与 `.episode_id`。索引为当前服务内成功初始化序号，首次通常为 1，每次成功 reset 加 1；重启服务重新计数。ID 是新生成的唯一标识，只显示前 16 字符；完整值从 API/记录读 |
| U11 `SEED 0` | 当前生效随机种子；帮助复现初始场景 | `.episode.seed`，无单位，公开 API 范围 `0..4294967295`（32 位无符号整数 uint32）。成功 reset 后更新；连接初期默认 0 不能当成服务器已确认值 |
| U12 `RESEARCH PROFILE` | 正在使用科研随机初始化模式；防止误用固定 Demo 初态 | UI 固定后缀；客户端另核验 `.episode.initialization_profile == research` 才接受回复。不是当前任务、成功率或随机难度 |
| U13 `INIT 0123456789abcdef` | 初始化身份指纹；检查同 seed 的完整初态是否一致 | `.episode.initialization_hash` 前 16 字符。无单位；同模型/同初始化实现/同 seed 应一致。物体后续移动时不随当前姿态每帧变化；改模型或初始化实现后不能只靠 seed 比较 |
| U14 `CONTROL 0123456789abcdef` | 控制实现指纹；避免不同安全控制版本混作同一证据 | 回复 `token.control_hash` 前 16 字符。无单位；不是控制频率、控制器编号或动作进度。reset 通常不改它，控制源码版本变了可能改变 |
| U15 `--` | 尚无可显示的 ID/哈希；让空数据不被误认为有效身份 | `Short()` 对 null/空字符串的占位。连接前 INIT/CONTROL 常见；等待正常同步后应有值。前缀仅便于人工看，正式比对必须用 API 的完整字符串 |

**Seed 和 episode ID 的关键区别：** Seed 决定“怎样摆”，episode ID 决定“这是哪一次实验”。例如新启动 seed 0 是回合 1；随后点击 0、1、0 三次 reset 是回合 2、3、4。两次 reset 0 的 INIT 和初态应相同，但 ID、序号和 epoch 必须不同。不能为了“复现”要求 ID 相同。`runtime_session` 区分服务重启，`epoch` 区分同一次服务中的重新初始化；这两项参与同步/写操作校验，但面板没有独立显示它们。

### 3.3 连接、重置和安全状态

| 编号 / 英文 | 中文、正常与异常解读 | 真实来源与显示原因 |
| --- | --- | --- |
| U16 `CONNECTING` | 正在连接；刚启动时正常，长时间持续则检查服务/IP | Research 控制器的初值。后台线程正在连接 Planner；不是已连通证明 |
| U17 `DISCONNECTED` | 已断开/连接失败；画面可能仍停留在最后一帧 | Research Planner 通信异常触发，具体原因在橙色错误行。后台会自动重连，不需要连续按 reset |
| U18 `SYNCING` | Planner 身份已收到，正在等待对应 World 场景 | 两路通道尚未对齐时正常短暂出现。持续存在时查 11032、PC 当前场景和 Unity 日志；Planner 能回复不等于 World 已通过身份检查 |
| U19 `STREAMING` | 两路状态已对齐并在接收显示；正常观看所需状态 | Planner 回合校验通过，`View.ConnectionState` 为 STREAMING，且 Mirror 的 runtime_session/epoch 与 Planner token 相同。**不是机器人自动运动、帧率或任务成功的证明** |
| U20 `IDENTITY REJECTED` | 身份不匹配被拒收；不是普通等待状态 | 主线程检查研究模式、基础模型、episode/token 不一致时设置；同时给出 U34。不要绕过检查或强行 reset，先核对服务模式、布局与客户端版本 |
| U21 `RESETTING ON PC` | PC 正在处理重置；等待完成后再操作 | 大面板在本地 `busy` 为 true 时优先显示；包含排队和请求等待，不表示结果已经成功 |
| U22 `RESETTING` | 正在重置 | 小状态条对同一个 busy 的缩写；意义与 U21 相同 |
| U23 `SAFETY STOP` | 安全停止；优先检查 PC 报告的原因 | `observe.result.stopped` 为 true 时优先于普通连接/执行状态显示，原因取 `stop_reason`。不要反复 reset 掩盖失败；先保留证据，由规划器同学处理原因后再开新局 |

大面板通常显示 `STREAMING / IDLE` 这样的“连接 / 执行”组合，小条只显示连接状态；RESETTING 和 SAFETY STOP 有更高显示优先级。重置按钮仅在 **STREAMING 且无 busy/排队请求**时可点，并不以“没有 RUNNING/SAFETY STOP”为额外条件。因此按钮可点不代表当前服务器一定允许 reset；活跃执行可能拒绝，必须看回复。

当前实现没有显示“最后成功帧距今多少秒”的新鲜度字段。状态来自线程与帧处理，不是严格的端到端健康诊断；短暂网络故障期间可能保留先前文字/画面。开始重要实验前应从 PC API 重新读取当前回合，不能只凭某一张 STREAMING 截图证明整个会话始终在线。

### 3.4 执行状态（斜线右边）

| 编号 / 英文 | 中文与应该怎样理解 | 来源/变化条件、为何显示 |
| --- | --- | --- |
| U24 `IDLE` | 空闲；没有当前执行对象 | `execution.status` 缺失时客户端使用此默认值；manual 初始通常如此，正常 |
| U25 `RUNNING` | 已有执行正在进行/待推进 | PC 已接收外部动作。manual 中仍需外部调用 step 才推进，不是 Quest 发起的自动策略 |
| U26 `COMPLETED` | 这批路标已消耗完 | PC 队列结束；**不等于抓稳、不等于洗净、不等于任务完成**。正常终止原因可为 `WAYPOINTS_CONSUMED_NOT_TASK_SUCCESS`；另读物理/任务反馈 |
| U27 `FAILED` | 执行失败 | 当前 PC 可能因安全、环境变化或物理/证据异常设置。读取 `get_execution_status.reason`、`stopped/stop_reason` 与证据；UI 不保证把 FAILED 的所有 reason 自动抄到错误行 |
| U28 `CANCELLED` | 已取消 | 外部取消或执行期间写连接断开等；客户端可把 `execution.reason` 放到错误行。取消不等于成功 |
| U29 `HELD` | 被要求保持/停止 | 外部 `hold` 使控制器停止锁存；通常同时看到 SAFETY STOP。先确认停止原因，不把保持当作暂停影片 |
| U30 `ABORTED` | 中止（界面预留识别值） | UI 对此值有 reason 展示分支，当前 PC 核心未主动产生该 status；不是已验证的新能力或新增按钮 |
| U31 `REJECTED` | 拒绝（界面预留识别值） | 同上。当前请求拒绝通常表现为 `ok:false/error`，不一定改变 execution.status；不能只等这个单词判断动作是否被拒绝 |

### 3.5 错误行原文与动态内容

| 编号 / 原文 | 中文、何时出现及处理 | 实际来源/显示目的 |
| --- | --- | --- |
| U32 `INVALID SEED: uint32 required` | 种子不合法，须为 uint32；去掉负号/小数/空白异常输入并检查范围 | `ResetExplicitSeed()` 的 `uint.TryParse` 失败；**尚未向 PC 发出 reset**，原场景不变 |
| U33 `RESET FAILED` | 重置失败；检查 PC 日志及当前回合 | 收到 `ok:false` 且没有 error 正文的后备文案；不是服务器的具体失败分类 |
| U34 `Research token/episode mismatch` | 控制令牌与回合身份不匹配 | 主线程拒绝不一致的回复；对应 U20，不能忽略 |
| U35 `Research API connect timeout` | 连接 Planner 超时 | 初次 TCP 连接等待 3 秒超时的异常正文；可能显示 `TimeoutException:` 前缀。检查 PC 服务、目标 IP、可信 LAN 和端口 |
| U36 `Research observation rejected` | 观察请求被拒绝 | 后台 observe 回复 `ok` 不为 true；显示为 `InvalidDataException:` 加正文，需查 PC 返回信息/版本 |
| U37 `Research episode/base-model identity rejected` | 科研回合或基础模型身份被拒绝 | 后台发现不是 research、不是当前 013 基础模型，或 token 与 episode 不一致；会走异常/重连路径，不必然显示 U20 |
| U38 `Missing/oversized Research reply` | 回复缺失或超过允许长度 | 后台读取一行回复为空或超过 16 MiB 时抛 `IOException`；断线/错误服务均可能，不是本机已确认内存损坏 |
| U39 `...` | 错误正文被截短 | 原因超过 170 字符时仅显示前 167 字符加三个点；完整信息必须从 PC API/日志读，省略号不是另一种状态 |

错误行优先显示安全停止原因，否则显示本地/服务器 Failure；若都没有，且 status 为 ABORTED/REJECTED/CANCELLED，再显示执行 reason。成功 UI reset 会清除 Failure，但下一次 observe 的 `last_reset_failure` 可继续报告最近服务端重置失败。不能把错误行空白当作物理成功。

动态内容可能是 `IOException: ...`、`SocketException: ...`、`AggregateException: ...` 等系统异常，正文语言随 Windows/.NET 环境变化；也可能是服务器的 JSON，如 `{"code":"WRITER_OWNERSHIP_CONFLICT","detail":...}`，或者 `attempted_seed`、`retained_episode_id`、`cause`、`stage` 等失败详情。`code` 是错误类别，`detail/cause` 是原因，`retained_episode_id` 是保留的旧回合。示例错误包括 INVALID_SEED、RESET_PREPARATION_FAILED、EXECUTION_BUSY_CANCEL_FIRST、WRITER_OWNERSHIP_CONFLICT、STALE_OR_MISMATCHED_TOKEN；以**实际 reply**为准。它们不是按钮，也不是永远都会出现的固定英文标签。

World 通道的 `IDENTITY_OR_FRAME_REJECTED`、`World connection timeout`、`World stream closed` 等是 `KitchenPlatformView` 的内部状态/日志；Research 面板没有把这些字符串直接逐字展示。面板可能表现为持续 SYNCING，具体原因要查 Unity/Android 日志，不能虚构一个不存在的 World 专用错误框。

## 4. 在 Quest 上怎样操作

| 操作 | 当前真实行为 |
| --- | --- |
| 指向并点击 | 优先使用**被追踪的右手柄**射线，右手未追踪时才回退左手；食指扳机按下/松开用于 UI 点击。不是双手同时各有一条独立射线；左手能收面板不代表此时左射线已被选为点击器 |
| 右手 B / 左手 Y | 任一个按下都切换“展开大面板 ↔ 小状态条”；不是播放、reset 或系统菜单。展开/收起时按当前头部水平朝向重新放置面板 |
| 大面板 HIDE | 收起为小状态条 |
| 大面板 RECALL | 保持展开并移回当前面前 |
| 小状态条 MENU | 点击后展开并移回当前面前；不要按 Quest 系统 Meta/Menu 键代替它 |
| 看不到小状态条 | 先按 B 或 Y 展开到面前；不会停止当前 PC 实验 |

大面板初始在水平正前方约 **1.15 m**、相对眼高下移约 **0.06 m**；小条约前方 **0.85 m**、右侧 **0.32 m**、下方 **0.30 m**。这些都是 Unity 显示布局，不改变 MuJoCo 场景。切换之后面板固定在那次放置的世界位置，**不会每帧跟着头转**。

启动时等待头显追踪有效且稳定后，只调整 XR Rig/Origin 一次，将眼睛安排到场景坐姿位置（Unity 世界约 `(0,1.20,-1.16)` m），面向厨房；不逐帧覆盖被追踪的 Camera 位姿。RECALL/B/Y 是重新摆面板，不是重新校准坐姿。需要系统级重新居中时使用 Quest 自身系统功能，其菜单行为不由本项目定义。

种子输入框是 Unity InputField，源码没有另外实现一套数字键盘或自定义 Quest 键盘。射线可以选择输入框，但不能仅凭代码保证所有 Quest 系统版本都自动弹出可用键盘。用户已通过基本重置验收；**任意数字输入/软键盘的具体体验若未测，仍须真机确认**。若无法改数字，用 PC Planner API 指定 seed，再看头显同步；不是反复点 RESET SEED 就会切换 0/1。

## 5. 从安装到结束的完整流程

### 5.1 原开发电脑：安装与启动

以下绝对路径**仅适用于原开发机**。已安装当前版本且能联机时，不必重复安装。

```powershell
$ADB='C:\Program Files\Unity\Hub\Editor\6000.0.66f2\Editor\Data\PlaybackEngines\AndroidPlayer\SDK\platform-tools\adb.exe'
& $ADB install -r 'D:\VLA_Kitchen_Data\KitchenResearch_APK\20261023_ps51_lan\KitchenResearch.apk'
```

PC 和 Quest 使用能相互访问的可信网络；在 PC 用 `ipconfig` 核对 WLAN IPv4。不是 VPN 地址、127.0.0.1 或 0.0.0.0。当前安装包只连接构建时指定的 LAN 地址。按需要允许该可信网络上的 11031/11032 入站，不要关闭整个防火墙或向公网开放服务。

在**普通 Windows PowerShell 5.1**运行下列原机命令。不需要安装 PowerShell 7，保持此窗口运行：

```powershell
$W=(Get-Location).Path # 在当前源码根运行；不依赖原开发者个人目录
$Python='D:\VLA_Kitchen_Data\.envs\kitchen-sim\Scripts\python.exe'
$PcLanIP='<PC_LAN_IPV4>' # 替换为本机且与 APK 配置一致的地址
$FullOps=Join-Path $W 'experiments\kitchen_platform_v3\20261021T013529\fullops_20261021'
$link=Get-Item -LiteralPath $FullOps
if ($link.LinkType -ne 'Junction' -or [string]$link.Target -notmatch '^D:[\\/]') { throw 'D drive Junction check failed' }
$Run=Join-Path $FullOps ('research_user_'+[guid]::NewGuid().ToString('N'))
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$W\scripts\start_kitchen_platform.ps1" `
  -Layout kitchen_v3_norice_candidate_013 -InitializationProfile research -Mode manual -Seed 0 `
  -BindHost $PcLanIP -PlannerPort 11031 -WorldPort 11032 `
  -DataRoot "$Run\data" -ReadyFile "$Run\ready.json" -PythonExe $Python
```

每次生成新的 `$Run`，不要复用旧证据目录/ready 文件。原机 FullOps 是指向 D 盘的既有 Junction，不能删除或重新创建。启动脚本拒绝 DataRoot/ReadyFile 逃出当前源码树的 `experiments/kitchen_platform_v3/`，这是保护旧实验的限制，不要为方便去掉。

看到 `PLATFORM_READY` 才算两个监听器实际启动。`ready.json` 只是启动时快照，不随每次 reset 重写，不能用它判断当前回合。另开窗口可查看本次真实路径（把示例占位路径改为刚才 `$Run` 的输出）：

```powershell
Get-Content -Raw '本次Run的完整路径\ready.json' | ConvertFrom-Json | Format-List status,planner_host,planner_port,world_port,mode,initialization_profile
```

端口占用时先确认是否已有本人要使用的服务，不要盲杀其它 Python 或同时启动第二份正式服务。启动失败的文字/退出码比“脚本已配置”更有权威。

### 5.2 Quest 连接和查看

1. 打开 **Kitchen Research**，不是 Kitchen Demo。PC 服务可先于或后于应用启动，客户端会自动重连。
2. 等待 CONNECTING → SYNCING → STREAMING；正常可能显示 `STREAMING / IDLE`。两路分别连 Planner 11031 与 World 11032，面板没有单独“Connect”按钮。
3. 看下方 EPISODE、SEED、INIT、CONTROL。确认模式是 RESEARCH PROFILE，再用 HIDE/B/Y 收起大面板观察场景。
4. manual 模式下机器人原地不动是正常的：World 可每约 0.04 s（目标 25 Hz）发送状态，但没有外部 step 时 `sim_tick` 不变。
5. 断开 PC/网络时，最后一帧可能仍可见；不能把静止场景视为仍在线。重连后核对当前 episode，勿用旧 token 写动作。

### 5.3 做 seed 0 → 1 → 0 复现检查

此过程会**放弃当前动作并开始新回合**，只在没有运行中的规划器任务、没有活跃接触过程时做。先让规划器正确结束或取消并保留证据；RUNNING 时服务器会拒绝 reset，不要用断开来规避门禁。

| 顺序 | 操作 | 应检查 |
| --- | --- | --- |
| 1 | 输入 `0`，点 RESET SEED，等重新 STREAMING | SEED 为 0；记录完整 episode_id、initialization_hash、对象位姿（从 API 读取）；输入框 0 本身不是成功证据 |
| 2 | 输入 `1`，点 RESET SEED，再等同步 | SEED 为 1；新 episode ID/序号；物体仍在各自左右合法区，摆放通常不同 |
| 3 | 再输入 `0` 并 reset | 新 episode ID/序号，但同版本的 INIT 和完整初态对象位姿应回到步骤 1；比较前不要先 step/运动 |

仅看物体“大概摆回去”是人工初检。正式比对 API 的完整 `initialization_hash`、`qpos/qvel` 和对象位姿，不比较被截短的 ID；相同 seed 不等于已运动后的状态会倒放，也不保证跨版本逐位复现。

### 5.4 使用 NEW EPISODE

点击后 PC 产生新的 uint32 seed；实现保证不与当前回合 seed 相同，但不是保证永远不重复历史 seed。新回合通过全部初始化检查后才发布；同步完再读取 SEED。输入框可能仍是先前的 `0`，这是正常的。后续若点 RESET SEED，会使用输入框中的 0，而不是下方当前生效的随机 seed。

reset 失败时保留旧回合和状态并报告原因。成功创建的准备文件也不必然说明新回合已正式发布，实际生效身份以 observe/World 为准。频繁点击不会“增加成功概率”；按钮会在 busy 或非 STREAMING 时禁用。

### 5.5 结束实验与记录

1. 规划器完成当前有限动作后读取 `get_execution_status`、`get_task_feedback` 和实际终态。若需要中止，调用 cancel/hold，记录原因；这会锁存停止，后续执行须在处理原因后 reset。
2. 等待证据 end 封存，保存完整 episode/token/execution_id/seed 和本次数据根。不要只截一个 COMPLETED。
3. 关闭 Quest 应用或收起面板不会代替 PC 实验结束。确认写操作完成后，在 PC 服务窗口按 Ctrl+C 正常退出；不要直接关机或删目录。
4. 服务结束时会尝试封存仍打开的证据。若是强制关机、写盘失败或无 end 记录，必须标记不完整，不能补写一个假的成功结尾。

| 文件 / 位置 | 怎么读、不能误解成什么 |
| --- | --- |
| 本次 `ready.json` | 启动身份、端口、DataRoot 快照；不是当前 episode 的实时公告 |
| `data/session_<runtime_session>/initializations/<episode_id>.json` | 种子、准备初态、初始化与控制身份。含 PREPARED_ONLY 标记；以 observe 确认是否发布成功 |
| `data/session_<runtime_session>/reset_failures.jsonl` | 已记录的输入/准备/旧证据封存失败，含 attempted_seed、retained_episode_id、cause、stage；旧回合通常仍有效。身份/占用等前置拒绝不一定写入此文件，也须保存 RPC reply |
| `data/episode_<记录ID>/physics.jsonl` | 已录制执行的 metadata、每个真实物理子步、end；目录记录 ID 不一定等于 Research episode ID。用 metadata.manifest.episode 与 execution_id 绑定 |
| 外部示例的 `rpc.jsonl`、`report.json`、`records/` | 示例自身的 API 请求/响应和已封存证据副本，不是服务始终自动生成的统一数据集 |

physics 样本的 `sim_tick` 为物理子步计数、`sim_time_s` 为仿真秒，不是北京时间；500 Hz 每步 0.002 s。`requested_reference16`、`accepted_target16`、`actual_ctrl16` 分别说明请求/接受目标/实际控制，不能混用。读记录先检查 SHA-256 链、end、样本连续性与身份，再分析接触和任务终态。只观察/只 reset 不保证生成动作 physics.jsonl；没有动作记录不等于采集故障。

## 6. 给 VLA / 轨迹规划同学

### 6.1 你接的是环境，不是自动专家

`manual` 的 observe、query_objects、World 订阅都不调用物理 step。先提交动作，再由同一合法写客户端推进仿真。25 Hz 的目标帧对应 20 个 500 Hz 子步；仅提交 RUNNING 后一直 observe，机器人不会自动走完整条轨迹。`realtime` 是另一种 PC 自动时钟模式，不能与 manual step 混用；本交接使用 manual。

| 意图 | 公开接口 | 读结果时注意 |
| --- | --- | --- |
| 当前状态/回合 | `observe` | episode、token、sim_tick/time、qpos/qvel、robots、objects、stopped、execution；不是 RGB |
| 场景契约 | `get_scene_manifest` | 版本、坐标、对象与控制契约；不要跨场景复用整数 body ID |
| 查询物体 | `query_objects {ids:["apple","burger"]}` | 语义 ID、世界位置、wxyz 四元数、外形边界/物理属性 |
| 动作预检 | `check_motion {end_joint_targets:[16值]}` | 失败/预算不足不是 PASS；自由空间预检不证明持物、夹爪闭合或门扫掠一定安全 |
| 提交动作 | `submit_joint_targets` / `execute_trajectory` | 消费外部提供的有限关节目标，不生成通用路径；实际执行仍有受限控制和接触保护 |
| 推进物理 | `step {n_substeps:20}` | manual 的一次 0.04 s；合法范围 1..5000，读反馈确认没有停止 |
| 执行与任务反馈 | `get_execution_status` / `get_task_feedback` | COMPLETED 与 `task_success=NOT_VERIFIED` 可同时存在；真实成功需独立物理判据 |
| 换回合 | `reset {seed:0}` / `reset {}` | 成功会使旧 token 失效；必须接收新 episode/epoch/tick/身份 |

目标顺序是 **左 7 关节 rad、左夹爪 tendon m、右 7 关节 rad、右 tendon m**，共 16 维。tendon 范围 0..0.05 m，0 为开；它不是实际指间距，也不是夹持力。MuJoCo 世界右手系 `(x右,y远离用户,z上)`，位置 m、角度 rad、四元数 wxyz；Unity 仅显示映射 `(x,z,y)`，不能把 Unity 坐标直接当 PC 控制目标。

只有一个写控制者。`PlannerClient` 自动维护 token/request_id/writer_id，但不要多程序并发 reset/step。Research UI reset 完成后会关闭该次连接以释放写权限，约 2 s 后自动重连观察；若外部规划器正占写权限，Quest reset 可能收到 WRITER_OWNERSHIP_CONFLICT。先在 PC 正常结束/释放控制，不强抢权限。连接断开会取消其未完成执行/活跃接触。

### 6.2 最小接入示例（供接手者运行，本轮不执行动作）

先按第 5 节启动服务，再在第二个 PowerShell 窗口从源码根运行。以下是调用现有客户端的微小动作示例，不是机器人任务策略。动作预检/执行若拒绝，保留原因并停止，不能删安全检查硬跑。

```python
from integration.kitchen.planner_client import PlannerClient

def require(reply):
    if not reply.get("ok"):
        raise RuntimeError(reply)
    return reply["result"]

with PlannerClient(host="127.0.0.1", port=11031) as pc:  # PC 本机；远程改成服务实际 LAN IPv4
    state = require(pc.call("observe"))
    print(state["episode"], state["sim_tick"], state["stopped"])
    # 只在本客户端拥有实验、没有运行任务时开始新回合。
    state = require(pc.call("reset", {"seed": 0}, mutating=True))
    state = require(pc.call("observe"))
    print(require(pc.call("query_objects", {"ids": ["apple", "burger"]})))
    target = list(state["accepted_target16"])
    target[0] += 0.002  # 仅示范有界小动作，非抓取/规划算法。
    checked = require(pc.call("check_motion", {"end_joint_targets": target}))
    if not checked["pass"]:
        raise RuntimeError(checked)
    submitted = require(pc.call("submit_joint_targets", {
        "joint_targets": target, "source": "external_planner"}, mutating=True))
    after = require(pc.call("step", {"n_substeps": 20}, mutating=True))
    execution = require(pc.call("get_execution_status"))
    feedback = require(pc.call("get_task_feedback", {"object_ids": ["apple", "burger"]}))
    print(after["sim_tick"], after["stopped"], execution, feedback)
    print("物理记录路径：", execution.get("evidence_path"))
```

连接远程 PC 时将 host 改为**实际服务 PC 地址**，而不是规划器所在电脑地址。本例的路径/记录默认在服务端 PC，远程客户端不能直接把 `D:\...` 当本地文件打开；通过共享/复制合规传递后再核验哈希。生产接入详见 `PLANNER_API.md` 的显式接触契约；不要用普通自由运动接口冒充安全抓取。

项目已有可运行的完整有限接入示例 `integration/kitchen/platform_research_handoff_example.py`，支持启动服务、0→1→0、新 seed、只读比对、可选微小动作和证据读取。用当前 Python 运行 `-B -m integration.kitchen.platform_research_handoff_example --help` 查看真实参数；部署与已做的有限验收见 `RESEARCH_TECH_HANDOFF_20261022.md`、`RESEARCH_WINDOWS_DEPLOY_20261022.md`、`RESEARCH_PC_ACCEPTANCE_20261022.md`、`RESEARCH_QUEST_LAN_FIX_20261023.md`。本说明不再次运行随机化/运动实验来增加测试数量。

### 6.3 目前不能承诺的视觉训练能力

| 需求 | 当前公开 Research 是否具备经过验证的接口 |
| --- | --- |
| 数值物体/机器人状态、回合、种子、仿真 tick/time | 有；公开 API/World/物理证据各有相应字段 |
| 公开 RGB 图像观测 | **没有经过验证的公开接口**；observe 返回数值，不是摄像头图像 |
| 公开相机内参 K、外参、图像坐标契约 | **没有经过验证的 Research 接口** |
| 图像帧与动作/物理 tick 的一一同步账本 | **没有**；数值物理样本同 tick 不等于图像—动作同步 |
| 可直接训练 VLA 的完整数据集、批量专家轨迹、自动任务策略 | **没有，也不是本平台本轮职责** |

旧模块有可复用相机渲染/内参/采集代码，但当前 Research server 不调用它们，不能把旧 teleop 的能力标成 Research 已交付。Quest 录屏是用户视角视频，不等于标定 RGB 观测或训练数据。以后要另行定义相机 ID、分辨率、K/pose、episode/epoch/tick、pre/post-action、丢帧规则及公开导出接口。

## 7. 在另一台 Windows 电脑部署

不要复制原机整个虚拟环境、Unity Library、Gradle 缓存、原始大轨迹、私有凭据或其它项目。PC 环境使用源码/配置/必要模型资产，Quest 使用 APK，二者分开交接。

| 项目 | 接手者要做什么 |
| --- | --- |
| 源码版本 | 使用含最新 PowerShell 5.1 修复的源码快照，不是仅 checkout 旧 HEAD。2026-10-22 的旧 ZIP 不能当作已含 10-23 修复 |
| Python / 依赖 | 原机核验 CPython 3.12.14 x64、MuJoCo 3.12.0、NumPy 2.5.3、h5py 3.16.0；按 `robot_arm/requirements-kitchen-research.txt` 配置独立环境。另机尚未因此获得 PASS |
| 模型与许可 | 保留 robot_arm 厨房源码、layouts JSON、`assets/fr3_umi_merged.xml` 和其引用 meshes、FR3 Apache-2.0 / UMI MIT notice；不要只拷 Python 文件 |
| 路径 | 设置目标机 `$W` 与 `$Python`。DataRoot/ReadyFile 必须位于新树 `experiments/kitchen_platform_v3/`，用全新回合目录。原机的 FullOps Junction 检查不是要求对方凭空创建同名 Junction |
| 端口/IP | 11031/11032 保持两端一致；同 PC 规划器可用 127.0.0.1，Quest 必须使用 PC 可达 LAN IPv4。当前 APK 不支持改 IP 输入；新机器 IP 不同需维护者重新配置 Research 构建 |
| 依赖范围 | PC 核心不要求 Quest 在线、Unity、VLA 模型或训练库；运行核心不要求安装 PowerShell 7。源码 ZIP 打包器的单独 PowerShell 7 要求不是服务运行前提 |
| 安全 | 只有可信开发 LAN；无 TLS/身份认证，不开放公网，不关闭安全检查、不覆盖历史数据 |

目标机启动模板（先按目标机位置赋值，**不是原机实际路径**）：

```powershell
$W='D:\KitchenResearchSource'              # 改为目标机源码根
$Python='D:\Envs\kitchen\Scripts\python.exe' # 改为目标机解释器
$PcLanIP='目标机的实际IPv4'                 # Quest 使用这个地址
$Run=Join-Path $W ('experiments\kitchen_platform_v3\research_'+[guid]::NewGuid().ToString('N'))
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$W\scripts\start_kitchen_platform.ps1" `
  -Layout kitchen_v3_norice_candidate_013 -InitializationProfile research -Mode manual -Seed 0 `
  -BindHost $PcLanIP -PlannerPort 11031 -WorldPort 11032 `
  -DataRoot "$Run\data" -ReadyFile "$Run\ready.json" -PythonExe $Python
```

只有 PC 本机开发且不用 Quest 时可将 BindHost 设为 127.0.0.1。其它 Windows 电脑的安装、局域网、防火墙和真实 Quest 连接仍要在目标机实际验收，不能继承原机的通过结论。

## 8. 本次源码逐字段覆盖检查

| 核对源 | 已覆盖的真实行为/字段 | 结果 |
| --- | --- | --- |
| `KitchenResearchControls.BuildUI` | 标题 2、PC 限定提示 1、命令按钮 5、种子输入框初值，共 U01–U09 | 全覆盖；没有虚构 Connect、自动做饭、RGB、IP 设置按钮 |
| `UpdateLabels/Short` | EPISODE（序号+ID）、SEED、RESEARCH PROFILE、INIT、CONTROL、`--`，U10–U15；截短 `...` U39 | 全覆盖；ID/哈希前 16 字符、原因最多 170 字符已说明 |
| `Run/Update/QueueReset` | CONNECTING、DISCONNECTED、SYNCING、STREAMING、IDENTITY REJECTED、两种 RESETTING、SAFETY STOP，U16–U23；重置按钮门控、自动重连及 writer 释放 | 全覆盖；两路身份匹配不是单路 TCP 成功 |
| `ApplyOperationState` + PC `platform.py` | IDLE/RUNNING/COMPLETED/FAILED/CANCELLED/HELD，及 UI 预留 ABORTED/REJECTED，U24–U31 | 全覆盖；预留值和当前 PC 实际产生值分别标注；FAILED reason 显示局限已列出 |
| 种子校验、网络/身份异常 | 七种固定错误 U32–U38；动态系统异常与 JSON 失败详情 | 全覆盖；动态正文不可穷举，没有猜测其永远固定语言 |
| `KitchenVrPanel` | B/Y 展开切换、HIDE/RECALL/MENU、一次性坐姿定位与非头锁面板 | 全覆盖；不把 MENU 当物理系统菜单，不把 RECALL 当重置头部追踪 |
| `KitchenDemoRayInput`（Research 复用） | 右手追踪优先/左手回退、食指扳机、Research 最大 UI 射线距离 3 m | 已源码确认；不是两个独立控制器同时点选 |
| `KitchenPlatformView/KitchenSceneMirror` | 两路通道、模型/session/epoch、body 完整性、非有限数拒收、旧 tick/sequence 忽略 | 已源码确认；World 内部日志与面板直接文字分开说明 |
| PC reset/记录/入口 | seed uint32、新回合 ID/epoch、初始化失败保留、manual 时钟、文件与 token 绑定 | 已源码确认；正式交接另外执行了 PS5.1 独立目录烟雾测试，详见发布验收报告；未重跑完整物理任务 |

本次所读的下列五份运行时源码 SHA-256 **全部与最新 Research APK 构建报告 `verification.json.source_hashes` 一致**，不是拿另一版界面写说明：

| 文件（相对 `m7_unity6000`） | SHA-256 |
| --- | --- |
| `Assets/BCI/Kitchen/KitchenResearchControls.cs` | `f153eff66e747ecf35151e4e6c760f3e37ca5ce72adeebbed450bf2780646747` |
| `Assets/BCI/Kitchen/KitchenVrPanel.cs` | `ac0f02d7807e52feb0a62126483bae67a2afce2f4e7a240b26baae20f0750302` |
| `Assets/BCI/Kitchen/KitchenPlatformView.cs` | `e3f1fc903e5e3d5046a111cd87274f532dde41ae3494cc73cc25dc6962c4a136` |
| `Assets/BCI/Kitchen/KitchenSceneMirror.cs` | `548397e895c1a0a740ec1b6b16dcf3d744e94454f82819be4fdd55745a63dacf` |
| `Assets/BCI/Kitchen/KitchenDemoUI.cs`（含射线输入） | `ff1d0d5898c15e1c4257c300742b6bb9d4a597eb4efdf266d66fffba75ea7a81` |

核对依据还包括 `integration/kitchen/platform_server.py`、`planner_client.py`、`platform_research_handoff_example.py`、`robot_arm/kitchen/platform.py`、`research_initialization.py`、`PLANNER_API.md` 和既有交接/部署/10-23 修复报告。旧文档中的 Goal active、Quest 尚未测等历史语句不覆盖本说明的当前边界。

**仍需人工/目标机确认：** 具体 Quest 系统的任意种子键盘输入、B/Y 与实际手柄习惯、转头后呼出/坐姿舒适度、长时帧率，以及迁移后 PC/Quest 的联机。用户已报告的本版联机/STREAMING/基本随机重置 PASS 保留，不用新文档夸大成所有交互和 VLA 数据采集均已通过。
