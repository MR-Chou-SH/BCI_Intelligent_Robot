# M37 明日操作清单

1. 如 Quest 尚未安装 M37 Research Acquisition：先在 Quest 的“设置 → 系统 → 开发者 → 无线调试”读取当前 IP:port，在 PC 运行 adb connect <IP:port>；随后运行 adb install -r D:\EEG_Study\m37_prospective\_preflight\quest_builds\m37_research_20261005.apk，再用 adb shell monkey -p com.samples.passthroughcamera.m37research 1 启动。若无线安装不可用，只临时接一次 USB 数据线安装；应用启动后拔线运行，正式运行不需要 USB/ADB 或 Unity Editor。
2. 确认 PC Wi-Fi 地址为 192.168.43.168，Quest 在同一 Wi-Fi/LAN。Research 应用应连 PC 11001 并发布 m19_research_ready。
3. 佩戴 ND8，检查电极接触质量，然后运行 powershell -ExecutionPolicy Bypass -File scripts\m37_preflight.ps1 -ComPort COM11 -QuestIp 192.168.43.110。仅在最终输出 READY FOR FORMAL ACQUISITION 时继续。
4. 在正式采集前使用真实 trigger 做 3–5 个 dummy trial：确认每次仅触发一次、trialId 一致、trigger 后 prep 1.5 s、onset anchor 存在、stimulus 到 +4 s 停止、epoch 每通道 4500 samples、之后 6 s rest。当前软件侧真实 laser/TTL 输入是否进入 Quest handler 仍未验证；若现场 trigger 未形成 m19_research_trigger，不要开始 formal session。
5. 任一真实触发丢失、重复或不同步时停止，不启动正式 session；保留该 dummy trial 的 partial/invalid 记录。
6. 通过后运行 powershell -ExecutionPolicy Bypass -File scripts\m37_start_acquisition.ps1 -SessionNumber 1 -PhysicalTriggerPassed。今晚不要创建或启动 formal\session_001。
7. 固定映射为 slot 0 = 7.2 Hz、slot 1 = 9 Hz、slot 2 = 12 Hz。正式 schedule 已冻结于 D:\EEG_Study\m37_prospective\formal\schedule.json。
