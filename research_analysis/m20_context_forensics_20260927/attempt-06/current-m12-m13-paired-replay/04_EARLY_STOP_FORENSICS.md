# Early-stop Forensics

The complete 11-point trajectory is shown even after Context ON reaches a terminal decision.

## A / m6_1b-trial-015

- Truth: `target_left`
- Context OFF: `target_left` at `4.0s`; early=False
- Context ON: `target_left` at `2.6s`; early=True
- OFF−ON delta: `1.4s`

| t | OFF raw margin | OFF fused margin | OFF top | OFF count | ON raw margin | ON fused margin | ON top | ON count |
|---:|---:|---:|---|---:|---:|---:|---|---:|
| 2.0 | 0.1862 | 0.0784 | target_left | 0 | 0.1862 | 0.5836 | target_left | 1 |
| 2.2 | 0.3661 | 0.1473 | target_left | 0 | 0.3661 | 0.1473 | target_left | 0 |
| 2.4 | 0.1937 | 0.0744 | target_left | 0 | 0.1937 | 0.5798 | target_left | 1 |
| 2.6 | 0.1368 | 0.0523 | target_left | 0 | 0.1368 | 0.5564 | target_left | 2 |
| 2.8 | 0.1931 | 0.0766 | target_left | 0 | 0.1931 | 0.5751 | target_left | 3 |
| 3.0 | 0.3283 | 0.1454 | target_left | 0 | 0.3283 | 0.1454 | target_left | 0 |
| 3.2 | 0.5386 | 0.2346 | target_left | 0 | 0.5386 | 0.2346 | target_left | 0 |
| 3.4 | 0.5040 | 0.2500 | target_left | 0 | 0.5040 | 0.2500 | target_left | 0 |
| 3.6 | 0.5659 | 0.2558 | target_left | 0 | 0.5659 | 0.2558 | target_left | 0 |
| 3.8 | 0.4687 | 0.2109 | target_left | 0 | 0.4687 | 0.2109 | target_left | 0 |
| 4.0 | 0.5603 | 0.2436 | target_left | 0 | 0.5603 | 0.2436 | target_left | 0 |

## B1 / m6_4-trial-001

- Truth: `target_left`
- Context OFF: `target_left` at `4.0s`; early=False
- Context ON: `target_left` at `2.4s`; early=True
- OFF−ON delta: `1.6s`

| t | OFF raw margin | OFF fused margin | OFF top | OFF count | ON raw margin | ON fused margin | ON top | ON count |
|---:|---:|---:|---|---:|---:|---:|---|---:|
| 2.0 | 0.0086 | 0.0037 | target_left | 0 | 0.0086 | 0.5123 | target_left | 0 |
| 2.2 | 0.1500 | 0.0705 | target_left | 0 | 0.1500 | 0.5776 | target_left | 1 |
| 2.4 | 0.1351 | 0.0618 | target_left | 0 | 0.1351 | 0.5796 | target_left | 2 |
| 2.6 | 0.2501 | 0.1043 | target_left | 0 | 0.2501 | 0.1043 | target_left | 0 |
| 2.8 | 0.5247 | 0.2134 | target_left | 0 | 0.5247 | 0.2134 | target_left | 0 |
| 3.0 | 0.4679 | 0.1930 | target_left | 0 | 0.4679 | 0.1930 | target_left | 0 |
| 3.2 | 0.5985 | 0.2380 | target_left | 0 | 0.5985 | 0.2380 | target_left | 0 |
| 3.4 | 0.4557 | 0.1748 | target_left | 0 | 0.4557 | 0.1748 | target_left | 0 |
| 3.6 | 0.4958 | 0.2098 | target_left | 0 | 0.4958 | 0.2098 | target_left | 0 |
| 3.8 | 0.4500 | 0.1928 | target_left | 0 | 0.4500 | 0.1928 | target_left | 0 |
| 4.0 | 0.3866 | 0.1585 | target_left | 0 | 0.3866 | 0.1585 | target_left | 0 |

## B2 / m6_4-trial-002

- Truth: `target_left`
- Context OFF: `target_left` at `4.0s`; early=False
- Context ON: `target_left` at `3.4s`; early=True
- OFF−ON delta: `0.6s`

| t | OFF raw margin | OFF fused margin | OFF top | OFF count | ON raw margin | ON fused margin | ON top | ON count |
|---:|---:|---:|---|---:|---:|---:|---|---:|
| 2.0 | 0.2586 | 0.1033 | target_left | 0 | 0.2586 | 0.1033 | target_left | 0 |
| 2.2 | 0.4764 | 0.1949 | target_left | 0 | 0.4764 | 0.1949 | target_left | 0 |
| 2.4 | 0.6369 | 0.2506 | target_left | 0 | 0.6369 | 0.2506 | target_left | 0 |
| 2.6 | 0.7519 | 0.3100 | target_left | 0 | 0.7519 | 0.3100 | target_left | 0 |
| 2.8 | 0.5684 | 0.2207 | target_left | 0 | 0.5684 | 0.2207 | target_left | 0 |
| 3.0 | 0.3380 | 0.1467 | target_left | 0 | 0.3380 | 0.1467 | target_left | 0 |
| 3.2 | 0.1804 | 0.0753 | target_left | 0 | 0.1804 | 0.5896 | target_left | 1 |
| 3.4 | 0.1634 | 0.0650 | target_left | 0 | 0.1634 | 0.5730 | target_left | 2 |
| 3.6 | 0.1757 | 0.0711 | target_left | 0 | 0.1757 | 0.5704 | target_left | 3 |
| 3.8 | 0.1346 | 0.0562 | target_left | 0 | 0.1346 | 0.5569 | target_left | 4 |
| 4.0 | 0.0638 | 0.0247 | target_left | 0 | 0.0638 | 0.5307 | target_left | 0 |
