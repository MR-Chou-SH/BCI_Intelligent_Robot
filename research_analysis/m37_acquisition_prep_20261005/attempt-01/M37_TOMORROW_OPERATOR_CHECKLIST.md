# M37 明日操作清单

1. Quest 已安装独立 M37 Research 包 `com.samples.passthroughcamera.m37research`，但当前安装的是旧 APK（SHA-256 `99f5926276b2c291b684347f5ffa607411cea713c6752cfed5a9462ef29606af`）。最新源代码包含 ready-on-connect 与 Quest 本地 stimulus-stop 时间戳修复；Unity Licensing Client 故障阻止了新 APK 构建。明早先在 Unity Hub 恢复登录/许可，然后用 `M37ResearchAcquisitionBuild.BuildQuestResearchAcquisition` 生成独立 APK，再通过无线 ADB `:5555` 安装并启动。正式运行不要求 USB、ADB 或 Unity Editor。
2. 确认 PC Wi-Fi 地址为 192.168.43.168，Quest 在同一 Wi-Fi/LAN。最新旧 APK 启动时界面正常、TCP peer 已连接，但 PC 未收到 `m19_research_ready`。新 APK 启动后必须先通过 Quest ready 握手。
3. 之前 `scripts\m37_preflight.ps1` 曾 PASS（COM11 得到 15 个连续 200×8 packets，Quest ready 收到），但最近一次重测未收到 ready，因此部署新 APK 后必须重跑：`powershell -ExecutionPolicy Bypass -File scripts\m37_preflight.ps1 -ComPort COM11 -QuestIp 192.168.43.110`。仅在最终输出 `READY FOR FORMAL ACQUISITION` 后继续。
4. 新 APK ready 握手及 preflight 都通过后，明早正式 session 前用真实 laser trigger 做 3–5 个 dummy trial（今晚未操作 laser）。确认每次 physical trigger 只进入一次共享 `accept_trigger` 路径、`trialId` 一致；核对 trigger 后 prep 1.5 s、onset anchor、stimulus +4 s 停止、epoch 每通道 4500 samples 和之后 6 s rest。失败时不要开始 formal session。今晚两个手动 gaze-offer 尝试没有确认实际 gaze 操作，不计入 physical-trigger 验收。
5. 任一触发丢失、重复或不同步时停止，不启动正式 session；保留该 dummy trial 的 partial/invalid 记录。
6. 只有 3–5 个 Quest-trigger dummy trials 全部通过后，才运行 `powershell -ExecutionPolicy Bypass -File scripts\m37_start_acquisition.ps1 -SessionNumber 1 -PhysicalTriggerPassed`。当前尚未创建 `formal\session_001`。
7. 固定映射为 slot 0 = 7.2 Hz、slot 1 = 9 Hz、slot 2 = 12 Hz。正式 schedule 已冻结于 `D:\EEG_Study\m37_prospective\formal\schedule.json`。
