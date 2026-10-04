# M37 明日操作清单

1. 佩戴 ND8；Quest 3 运行已安装的 Research Acquisition app，并确认 Quest 与 PC 在同一 Wi-Fi/LAN。运行时不需要 Unity Editor、USB 或 ADB。
2. 运行 powershell -ExecutionPolicy Bypass -File scripts\m37_preflight.ps1；只在输出 READY FOR FORMAL ACQUISITION 时继续。该命令做一次 Quest 握手和一次 3 秒 ND8 传输探测。
3. 正式 session 前，用真实 laser trigger 做 3–5 个 dummy trial。确认每次只触发一条、prep 1.5 s、Quest/PC trialId 一致、anchor 后继续 4 s、每通道 4500 样本；同时确认外部真实 trigger 能进入当前 Quest Research 的 m19_research_trigger 事件路径。
4. 当前代码中已确认的 trigger 入口是 Quest Research dwell 消息；没有发现独立 laser/TTL 接收器。若外部 laser 未通过现有桥接产生该 Quest 事件，或 trigger 丢失、重复、不同步，立即停止，不启动正式 session。
5. 通过后运行 powershell -ExecutionPolicy Bypass -File scripts\m37_start_acquisition.ps1 -SessionNumber 1 -PhysicalTriggerPassed。技术无效 trial 保留原始记录，修复后只用 -Resume 从安全边界继续。
6. 固定映射为 slot 0=7.2 Hz、slot 1=9 Hz、slot 2=12 Hz。正式 schedule 已冻结到 D:\EEG_Study\m37_prospective\formal\schedule.json；preflight 不会覆盖它。
7. 采集期间不拔 ND8、不切换 Quest 网络；disconnect 时停止并保留 partial 状态，不补造样本。
