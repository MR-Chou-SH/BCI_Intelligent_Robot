# Trial-006 Harm Forensics

- Context OFF: `target_right` at `4.0s`, correct=True, early=False
- Context ON: `target_center` at `4.0s`, correct=False, early=False
- Effect type: `TYPE_4_DECISION_HARM`

The table reports only algorithmic evidence; it does not infer a physiological cause.

| t | raw top | raw margin | Context preferred | Context strength | ON fused top | ON fused margin | strong/weak | ON fusion mode |
|---:|---|---:|---|---:|---|---:|---|---|
| 2.0 | target_right | 0.3561 | target_center | 0.3333 | target_right | 0.1393 | strong | eeg_strong_override |
| 2.2 | target_right | 0.6114 | target_center | 0.3333 | target_right | 0.2245 | strong | eeg_strong_override |
| 2.4 | target_right | 0.5857 | target_center | 0.3333 | target_right | 0.2278 | strong | eeg_strong_override |
| 2.6 | target_right | 0.4138 | target_center | 0.3333 | target_right | 0.1626 | strong | eeg_strong_override |
| 2.8 | target_right | 0.1843 | target_center | 0.3333 | target_right | 0.0733 | weak | context_conflict_weak_eeg |
| 3.0 | target_right | 0.2282 | target_center | 0.3333 | target_right | 0.0911 | strong | eeg_strong_override |
| 3.2 | target_right | 0.3578 | target_center | 0.3333 | target_right | 0.1452 | strong | eeg_strong_override |
| 3.4 | target_right | 0.2818 | target_center | 0.3333 | target_right | 0.1147 | strong | eeg_strong_override |
| 3.6 | target_right | 0.1746 | target_center | 0.3333 | target_right | 0.0333 | weak | context_conflict_weak_eeg |
| 3.8 | target_left | 0.0400 | target_center | 0.3333 | target_center | 0.1168 | weak | context_conflict_weak_eeg |
| 4.0 | target_right | 0.1436 | target_center | 0.3333 | target_center | 0.0655 | weak | context_conflict_weak_eeg |

## Mechanism

Context cannot override a unique raw EEG top when the raw margin is at least 0.20. In this paired trajectory, the trial remains below that strong-EEG gate at the relevant windows, so weak-evidence multiplicative fusion is permitted. The stopping state never reaches two qualifying windows; finalization therefore returns the last full-window evidence, which is the algorithmic source of the Context-ON class change. Context OFF uses the formal disabled (`None`) M12 path, which reduces to a neutral active prior and returns the raw EEG winner at the endpoint.
