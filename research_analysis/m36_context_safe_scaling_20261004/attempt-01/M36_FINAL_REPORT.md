# M36 最终报告：SSVEP 的安全 Context 提前停止

## 结论摘要

本次分析把 118 条历史 EEG trial 全部纳入嵌套、分组 OOF 评估，比较了冻结 M35 门控、多视图确定性规则、校准 Logistic 和 Logistic 加独立视图约束。所有模型在授权时仍输出当前 FBCCA raw top；Context 只改变停止时机。

多视图规则在 q=.95 下把 congruent Context 覆盖提高到 Track 1 的 22.7% 和 Track 2 的 12.7%，但两条 track 分别出现 1 和 3 个 Context 诱发的错误早停。Logistic 系列覆盖较低，也没有跨 track 的高精度证据。预先规定的 q sweep 中，q=.50 的多视图规则在两条 track 都没有授权任何错误 Context，但 congruent 覆盖只有 5.7% / 3.4%，其 wrong-context precision 因没有错误 Context 干预而无法估计。q=.55 将覆盖提高到 9.1% / 5.9%；Track 1 出现一次错误 Context 干预（0/1 正确），Track 2 没有错误 Context 干预。因此没有达到“显著提高覆盖且跨组保持可靠”的目标。

主要瓶颈是短时 EEG raw top 的跨采集组可靠性，以及只有 3 个主 outer acquisition groups 的历史样本量。现有数据足以说明 Context 可以加速一小部分 congruent raw-EEG 决策，但不足以支持“广泛介入并阻止 incongruent Context 覆盖可靠 EEG”的安全性主张。需要冻结门控后，以同步记录的新 acquisition sessions 做前瞻性验证。

本报告所有 latency 都指 EEG evidence duration。保留 M34/M35 的 0.5 s onset guard 以便比较；没有验证物理光学 onset 或真实设备端到端延迟。

## 1. 是否纳入全部 118 条 usable trial？

是。唯一真实 trial 共 118 条：A 30、B1 29、B2 29、S7 30。Track 1 使用 A/B1/B2 的 88 条及 `[2,3,4,5,7]` 五通道；Track 2 使用全部 118 条及公共通道 `[2,4,7]`。两条 track 有重叠，不是 206 条独立 trial。每条 trial 产生 9 个因果时间点，共 792 + 1,062 = 1,854 行特征和标签。类别映射固定为 slot 0/1/2 = 7.2/9/12 Hz。

## 2. 如何分组以避免泄漏？

主验证使用 3 个 acquisition campaign group：A、M6.4（B1+B2 合并）、S7。B1 和 B2 虽是不同 recording/session ID，但属于同一 M6.4 获取系列，间隔约 27 分 55 秒，因此主 outer split 保守地将它们放在同一组。敏感性分析另将 A、B1、B2、S7 作为 4 个 recording-session groups。

每个 outer test group 完全不参与相应门控的拟合、校准、阈值选择；所有同 trial 窗口留在一个 fold。可做 leave-group-out 时按完整组留出；训练只剩一个 group 时，使用连续 trial block 并在边界 embargo 一条 trial。每条适用 trial 在每个 grouping scheme 中只获得一次 OOF 预测。`verify_m36_artifacts.py --folds-only` 检查了 outer/inner trial 与 group 不重叠、embargo 和覆盖完整性。

## 3. Track 1：主 88-trial 五通道 cohort 结果

EEG-only accuracy 为 94.32%，平均 evidence duration 为 498.9 ms。q=.95 的 congruent Context 下，M35 规则覆盖 5/88（5.68%），总体平均 gain 8.52 ms；M36 多视图规则覆盖 20/88（22.73%），总体 gain 31.25 ms，应用 trial 平均 gain 137.5 ms。M36 多视图规则的 congruent accuracy 与 EEG-only 相同，但对两个错误 Context 场景授权两次，均选错，其中一次构成 Context 诱发早停。

Track 1 Logistic SAFE-STRICT 覆盖 4/88（4.55%），错误 Context 场景没有任何授权，所以其错误风险/precision 不可估；view-guarded Logistic 覆盖 6/88（6.82%），一次错误 Context 授权且结果错误。完整行见 [`primary_success_table.csv`](primary_success_table.csv)。

## 4. Track 2：118-trial 公共三通道 cohort 结果

EEG-only accuracy 为 86.44%，平均 evidence duration 为 416.9 ms。q=.95 下，M35 覆盖 6/118（5.08%），但两个错误 Context 授权都选错；M36 多视图规则覆盖 15/118（12.71%），总体 gain 24.58 ms、应用 trial 平均 gain 193.3 ms，错误 Context 授权 3/3 均选错，且造成 3 个诱发早停。Congruent 场景的 accuracy 比 EEG-only 高 0.85 个百分点，是早期 raw top 纠正了一个 baseline 错误；这不能抵消错误 Context 场景的安全失败。

Logistic SAFE-STRICT 覆盖 7/118（5.93%），两个错误 Context 授权中有一个诱发早停；view-guarded Logistic 覆盖 10/118（8.47%），两个授权均错误，其中一个诱发早停。两者都没有达到 95%/99% 的可支持精度。

## 5. 最好的安全 Context 覆盖是多少？

没有足够样本支持一个高覆盖安全门控。看预先规定的 q sweep，M36 多视图规则 q=.50 在两条 track 中分别授权 5/88（5.68%）和 4/118（3.39%）个 congruent 场景；两条 track 都没有任何错误 Context 授权。此时风险分母为零，不能把 observed zero error 写成零风险或 95%/99% 安全证明。

该规则 q=.55 的覆盖升至 8/88（9.09%）和 7/118（5.93%）。Track 1 有一次错误 Context 授权，precision 为 0/1；它没有额外降低 EEG-only baseline accuracy，所以 induced-error 数为 0，但这仍不是正确的 Context 干预。Track 2 在 q=.55 没有错误 Context 授权，precision 同样不可估。q=.55 是覆盖更高的描述性点，不是已确认安全的 operating point。

## 6. 相比 M35 的约 6.8% 有无提升？

M35 的 6.78% 是历史 A/B1 development 参考（4/59），不是 M36 OOF。M36 在 q=.95 下的 M35 OOF 基线分别为 5.68% / 5.08%。M36 多视图规则 q=.95 可把 coverage 提到 22.73% / 12.71%，但错误 Context 诱发早停为 1 / 3。共同 q=.50 的零错误授权点合并 coverage 为 9/206（4.37%）；q=.55 的零 induced-error 点 coverage 为 15/206（7.28%），但其中有一次错误 Context intervention。因此没有证据显示 M36 得到显著、可信的安全覆盖增益；达到 20% 目标的高覆盖点不安全。

## 7. Context intervention precision 是多少？

主 q=.95 operating point 下，所有至少有一个 wrong-context 应用的 M36 方法/track 组合，其错误 Context intervention precision 都是 0：多视图规则 T1 为 0/2、T2 为 0/3；view-guarded Logistic T1 为 0/1、T2 为 0/2；基础 Logistic T2 为 0/2。基础 Logistic T1 没有错误 Context 应用，precision 未定义。M35 在 T1 也没有错误 Context 应用，在 T2 为 0/2。样本量过小，不能声称 95% 或 99% precision。

## 8. 还剩多少 Context-induced wrong early stop？

在 primary acquisition grouping、q=.95 下：M36 多视图规则为 T1 1、T2 3；基础 Logistic 为 T1 0、T2 1；view-guarded Logistic 为 T1 1、T2 1。M35 对照为 T1 0、T2 2。两种错误 Context 目标都纳入。按最高覆盖的 q=.95 多视图规则，共有 4 个 induced wrong early-stop events，落在 B1 的一个 trial 和 S7 的三个 trial；这些并非独立于每条真实 EEG trial 的重复 seed。

## 9. 总体平均 evidence reduction 是多少？

q=.95 的 M36 多视图规则总体平均 gain 为 Track 1 31.25 ms、Track 2 24.58 ms。对应平均 evidence duration 从 498.9 ms 降到 467.6 ms，以及从 416.9 ms 降到 392.4 ms。该高覆盖点有 wrong-context 风险，不能作为安全部署结果。主表同时列出 Logistic、view-guarded Logistic 和 M35 对照。

## 10. 被应用 trial 的平均 gain 是多少？

q=.95 多视图规则的 applied-trial mean gain 为 Track 1 137.5 ms、Track 2 193.3 ms。Track 2 接近希望保留的约 200 ms 条件 gain，但该点有 3 个错误 Context 诱发早停。

## 11. 是否保留约 200 ms 条件 gain？

只有高覆盖多视图规则在 Track 2 上保留了约 193 ms 的应用 trial gain；M35 Track 2 为 275 ms，但应用很少且错误 Context 两次都错。Logistic 为 150 ms / 120 ms，view-guarded Logistic 为 175 ms / 120 ms。条件 gain 本身保留，并未解决错误 Context 授权。

## 12. 完成时间不超过 300 ms 的比例是多少？

q=.95 下，EEG-only 的 <=300 ms fraction 为 Track 1 34.09%、Track 2 54.24%。M36 多视图规则为 42.05% / 58.47%；M35 为 34.09% / 54.24%。这些是 evidence duration，含历史 onset guard 的采样对齐约定，不是物理刺激 onset 到决策的实测时间。

## 13. 是否跨 acquisition group 稳健？

不稳健。主 outer split 只有 A、M6.4、S7 三个 acquisition groups；错误集中在 B1/M6.4 和 S7 stress session。录制 session 敏感性分析在 q=.95 下也出现风险：Track 1 多视图规则覆盖 18.18%、1 个诱发错误；Track 2 多视图规则覆盖 12.71%、3 个诱发错误。Track 2 view-guarded Logistic 在 recording-session sensitivity split 中为 10.17% coverage、0 observed induced errors，但在更保守的 campaign grouping 中为 8.47% coverage、1 个 induced error。这种分组敏感性不支持稳健泛化。

数据清点找到 4 个 recording IDs，但没有 participant ID；不能据此声称 4 名独立参与者。B1/B2 在主验证中合并正是为了降低同一短期采集系列泄漏风险。

## 14. 哪些 reliability feature 有用？

Logistic 使用 36 个截至当前 evidence time 的分数几何、时间轨迹、滤波带、谐波和通道子集特征。只用 outer-train 重新拟合的标准化系数显示：Track 1 较大权重包括 `time_since_last_top_flip`、absolute/relative margin 和 score concentration；Track 2 包括 `consecutive_same_top`、谐波视图一致性、leave-one-channel-out 的 secondary margin/agreement 与 prior top flips。

这是 2 个 Track 1 和 3 个 Track 2 outer models 上的描述性标准化系数；特征相互相关，不能解释为独立因果重要性。模型也没有稳定地区分可安全授权的错误 Context。细目见 [`feature_effects_summary.json`](feature_effects_summary.json) 和 [`feature_coefficients_primary_campaign.csv`](feature_coefficients_primary_campaign.csv)。

## 15. Multi-view EEG agreement 是否区分了暂时性错误 top？

有时提供警告，但不足以成为可靠判别器。S7 `stress_online-trial-019` 在 250 ms 的 harmonic agreement 只有 1/3，而 band 与通道子集视图一致；基础 Logistic 仍以 0.884 reliability probability 授权错误 Context，view-guarded Logistic 也仍授权一次。A `m6_1b-trial-026` 的错误早停在 band、harmonic、secondary views 全部同意错误 top 时发生，说明简单多数共识无法覆盖这类错误。多视图规则提高 coverage 的同时也产生 B1/S7 错误。

## 16. Learned gate 是否优于确定性规则？

没有一个方法在两条 track 上同时占优。多视图规则 coverage 和总体 gain 最高，但错误最多；基础 Logistic coverage 较低；view guard 有时降低错误、但在 campaign OOF 的 Track 1 与 Track 2 仍各有诱发错误。Logistic 的校准和阈值只用嵌套训练组选择，规则、Logistic 与 view-guarded Logistic 都使用内层 groupwise 最大 induced risk / 最差 group accuracy 约束。约束能控制训练验证组，却不能保证被留出的 acquisition group。

小树模型没有运行：批准的本地 Python 环境没有 sklearn/scipy/tree 运行库；没有安装依赖。M36 已覆盖确定性规则、正则化 Logistic、Platt 校准和分视图约束，不再为了追逐指标扩大模型搜索。

## 17. 安全 risk–coverage frontier 是什么？

在 q=.95，高覆盖规则的 T1/T2 congruent coverage 为 22.7%/12.7%，但错误 Context intervention precision 为 0/2 与 0/3。Logistic 系列降低覆盖，但依然出现 0/2、0/1、0/2 这类低精度的小样本结果。

在预定 q sweep 中，M36 多视图规则的 q=.50 点两条 track 都无错误 Context 授权，但覆盖只有 5.68%/3.39%，wrong-context 风险没有应用分母。q=.55 点覆盖为 9.09%/5.93%，T1 有一项错误 Context intervention（0/1 正确），因此 precision 不能支持安全目标。所有风险—覆盖点见 [`q_sweep_safety_frontier.csv`](q_sweep_safety_frontier.csv) 和图 1–5。

## 18. M36 离 oracle 上限有多远？

多视图 oracle（用真实类别选取早期正确 raw EEG top，不可部署）mean gain 为 Track 1 110.8 ms、Track 2 62.7 ms。q=.95 M36 多视图规则 gain/oracle 比为 28.2% / 39.2%。这是未按 wrong-context 风险折减的 gain 比率；多视图规则不是安全点。q=.95 view-guarded Logistic 比率约 10.8% / 16.2%。

## 19. 历史 M33 Context 经 M36 gate 的表现如何？

仅作次要历史回放：读取 M34 保存的 `precomputed_before_eeg` 语义 Context，不调用 DeepSeek。覆盖 B2+S7 的 59 条 EEG trial，每条有 100 个 stored seed scenario，共 5,900 个场景。M36 view-guarded Logistic SAFE-STRICT 授权 178/5,900（3.02%）；在 1,784 个有可用 semantic context 的场景中为 9.98%。平均 overall gain 4.47 ms，应用场景平均 gain 148.3 ms，fallback 97.0%，stored scenarios 中 induced errors 为 0，accuracy 为 88.14%。

这 5,900 个是 seed replay 场景，不是新增 EEG trial，也不是 5,900 个独立样本。Stored semantic Context 没有随机生成两种错误目标，因此不能估计 incongruent safety precision；结果也不是 prospective online Context validation。

## 20. 主要瓶颈是什么？

Controlled congruent/incongruent 结果表明主要瓶颈是短时 EEG raw top 的可靠性和跨组漂移，不是 Context 替换分类器：错误仍发生在 Context 与暂时性错误 raw top 一致时。250 ms 的 session shift 中，Track 2 top-score 最大绝对 SMD 约 1.37，prior top flips 约 0.56；S7 的 harmonic agreement 为 0.633，低于 A/B1/B2 的约 0.701–0.747。Logistic 以 training-only 标准化缓解不了这些 group shift。历史 M33 回放的低应用率还受 semantic-context 可用性与保守授权共同限制。

## 21. 能否支持“Context 广泛加速且阻止错误 Context 覆盖可靠 EEG”的主张？

不能支持完整主张。可以描述性地说：在历史 OOF 回放里，Context 与 raw EEG top 一致时能加速一小部分 decisions，且 raw EEG winner 始终作为输出；但 q=.95 的错误 Context 仍造成早停，安全覆盖没有达到 20% 目标，q=.50 的无错误点也没有错误 Context intervention 的风险分母。这只是历史 retrospective mechanistic evidence，不是 fresh independent held-out、prospective online、population-level 或物理 latency 验证。

## 22. 下一步需要前瞻性采集什么？

先冻结 M36 的 EEG decoder 与候选 gate，不再用这 118 条数据挑新的 final operating point。新增多次独立 acquisition sessions，并在正式验证前按目标 induced-risk 精度做样本量规划；每类保持平衡，记录原始 EEG、真实 stimulus onset/sample anchor、设备时钟与数据质量元数据。同步保存每 trial 的完整 selection/task history、Context 状态/置信度/page projection、触发时刻及最终标签，使 context congruent / incongruent / neutral 可以按预注册方案评价。

至少保留一个完全新的 session/campaign 作冻结后的确认集；评估期间不按该确认集改阈值。只有前瞻性样本支持跨组 precision 与风险上限后，才主张 Context 的安全增益。

## 其他强制检查与产物

- `M36_PROTOCOL.md` 与来源合同 SHA-256 相同；冻结的 FBCCA-003 / 0.5 s onset guard / 3-band / H=3 / `E200_M175_S2` 未调整。
- Track 1、Track 2 分开报告，B1/B2 主 outer fold 合并；重复 B1/B2 trialId 使用 `session::trialId`。
- 36 个 runtime features 不含 truth/final-class 标签；标签单独存表，且只用于训练与审计。特征仅用当前/过去 EEG。raw EEG argmax 不变，neutral 和未应用 Context 都精确回退 EEG-only，无 Context-induced delay。
- 同一 trial 每个 q 都保留两个错误 Context 目标；scenario counts 与 real-trial counts 分列。5,900 个 M33 seed 场景从未当作独立人类样本。
- raw EEG 的初始、feature extraction 后以及最终审计 SHA-256 必须匹配 manifest；M36 没有写入 D:\EEG_Study。
- 至少 12 张所需图表均为可解析 SVG：[`figures_manifest.json`](figures_manifest.json)。代表性成功、拒绝和失败轨迹见图 8–10 及 [`representative_examples.json`](representative_examples.json)。
- 全部 OOF 聚合复算和诊断检查结果保存在 `statistical_summary.json`、`diagnostics_summary.json`、`final_audit.json`。
- 所有结果是历史回放和分组交叉验证；不是新的独立 held-out 证明。

### 未纳入 Git 的大型派生表

以下逐窗口/逐场景派生表保留在本地 M36 artifact 目录，未纳入 checkpoint，以避免提交大型机器生成 CSV。下列大小与 SHA-256 用于定位和校验；这些文件不含原始 EEG 波形。原始 EEG 仍位于 `D:/EEG_Study` 且未被修改。

| 相对路径 | 字节数 | SHA-256 |
|---|---:|---|
| `oof_predictions.csv` | 53367351 | `8df913d9ee311c2c9a4447e207f127c07751decb9313bbda8e3f1573d5b06506` |
| `view_guard_logistic_oof_predictions.csv` | 14855904 | `5074bddc01f1e5093d96236137267e08ae5a48f37dadf247efee95d1c576de7c` |
| `rule_oof_predictions.csv` | 14727842 | `4fa90ba1bba62ffa8333560934188772f294fae2b3f78d7f0b52c7f8f0331674` |
| `real_m33_secondary_per_seed.csv` | 14381161 | `a05c5c809718c4b2ef47eb9ed0bfdeec8442841fff294e28de6479e4d7f90596` |
| `logistic_oof_predictions.csv` | 12003720 | `c941d5c62837fdaa68baf6dfb49fd05827d39d1e3b5e34746d58e42f9f2cfdca` |
| `eeg_trajectory_features.csv` | 2235974 | `f80e6d36b4a4f9c0f270d0e29a12b58ad2d695a1270d0b89ab40989fa9e9ba70` |
| `inner_fold_assignments.csv` | 1844566 | `c442466c088404484f0cf6290954d8f5d876ba8cf8d85a77511f543a7235fe20` |
| `nested_cv_search.csv` | 1554032 | `a3e3a6aa922126dac25e9c9fa9f9b8ede13e28bae0edfad24b0e5b76167443a2` |
| `group_specific_results.csv` | 1308614 | `50157cfe18e00827ccfd787f1973d8857c008fd6ef29f788d1d4b56f1992580d` |
| `view_guard_nested_search.csv` | 1272186 | `76991a2f153b14a2ccd5df481af086e74f588164b384b7d417b3f7f034b8dd31` |

## M36 完成摘要

`TOTAL REAL EEG TRIALS USED:` 118

`TRACK 1 TRIALS:` 88（5 channels）

`TRACK 2 TRIALS:` 118（common 3 channels；与 Track 1 重叠）

`BEST GATE:` M36 multi-view rule 在 q=.95 时覆盖最高，但不安全；无可部署的高覆盖安全 gate

`BEST SAFE APPLICATION RATE:` 无可估计的 cross-track SAFE-95/99 operating point；共同 q=.50 无错误 Context 应用的观察覆盖为 5.68% / 3.39%，风险不可估。q=.55 提至 9.09% / 5.93%，但 T1 错误 Context precision 为 0/1

`M35 APPLICATION RATE BASELINE:` 历史开发集 4/59 = 6.78%；本次 q=.95 OOF 为 5.68% / 5.08%

`INTERVENTION PRECISION:` q=.95 多视图规则 T1 0/2、T2 0/3；数据不支持 95%/99%

`CONTEXT-INDUCED WRONG EARLY STOPS:` q=.95 多视图规则 T1 1、T2 3

`EEG-ONLY MEAN EVIDENCE:` T1 498.9 ms；T2 416.9 ms

`M36 CONTEXT MEAN EVIDENCE:` q=.95 多视图规则 T1 467.6 ms；T2 392.4 ms

`OVERALL MEAN GAIN:` T1 31.25 ms；T2 24.58 ms（未作风险折减）

`APPLIED-TRIAL MEAN GAIN:` T1 137.5 ms；T2 193.3 ms

`<=300MS EEG-ONLY FRACTION:` T1 34.09%；T2 54.24%

`<=300MS M36 FRACTION:` T1 42.05%；T2 58.47%（q=.95 多视图规则）

`ORACLE MEAN GAIN:` T1 110.8 ms；T2 62.7 ms（ground-truth upper bound）

`ORACLE EFFICIENCY:` 多视图规则 q=.95 gain/oracle 比 T1 28.2%；T2 39.2%，但存在错误 Context 早停

`REAL M33 APPLICATION RATE THROUGH M36 GATE:` 178/5,900 = 3.02% overall；178/1,784 = 9.98% semantic-available scenarios

`REAL M33 MEAN GAIN THROUGH M36 GATE:` 4.47 ms overall；148.3 ms among applied scenarios

`CROSS-SESSION ROBUST:` NO

`MAIN REMAINING BOTTLENECK:` early EEG reliability under acquisition/session shift and too few independent groups

`PROSPECTIVE DATA REQUIRED:` YES

`FINAL COMMIT:` will be reported with the final Git checkpoint after push

`REMOTE HASH VERIFIED:` pending final push audit
