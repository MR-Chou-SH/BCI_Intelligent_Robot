# M31 manual review runbook

## Launch

From the repository root, with `DEEPSEEK_API_KEY` already configured in the process environment:

```powershell
& '.venv\Scripts\python.exe' -m integration.semantic_bridge_cli
```

The default is open-world semantic mode. Enter two to eight selected objects; press Enter at the third-object prompt to submit the pair. The bridge runs once after the object list is complete. It prints status, semantic relation, structured high-level intent, Chinese and English instructions, latency/retries, and an explicit `Dispatch: disabled` line.

For a one-shot example:

```powershell
& '.venv\Scripts\python.exe' -m integration.semantic_bridge_cli '红色苹果' '水果刀'
```

The optional M29 grounding validator is still available with `--mode strict-scene`. It is not the default and may reject nouns absent from its scene catalogue.

## Required manual examples

Try each pair independently; the bridge does not retain prior selection state between invocations.

1. `药盒` + `收纳盒`
2. `小药盒` + `收纳盒（关闭状态）`
3. `手机` + `无线充电座`
4. `苹果` + `水果刀`
5. `红色苹果` + `水果刀`
6. `橙子` + `榨汁机`
7. `书` + `书架`
8. `杯子` + `水龙头`
9. `衣服` + `洗衣篮`
10. `盘子` + `橱柜`
11. `笔` + `笔筒`
12. `螺丝` + `螺丝刀`
13. Reversed order: `水果刀` + `苹果`
14. Ambiguous: `手机` + `盒子`
15. Incompatible task: `--intent 'Use a book to charge the battery.' 书 电池`
16. English: `paintbrush` + `canvas`

For the explicit closed-state example, a high-level open/place/close sequence is reasonable. Unknown box state in example 1 must not by itself cause rejection. Example 5 may retain red in both languages; example 4 must not invent an apple color. The charging/storage pair must use a reasonable world-knowledge relation even if strict M29 does not know those descriptors.

## Review checklist

- Does the status distinguish executable, ambiguous, and invalid input?
- Is the relation semantically reasonable, including for the reversed-order example?
- Do both language instructions express the same relation?
- Are all structured object references among the selected objects or explicit scene context?
- Does the bridge avoid adding an unprovided third object to the instruction?
- Is an explicit color preserved, and is an unspecified color left unknown?
- Does explicit state affect the instruction without inventing state when absent?
- Does the output stay at high-level language and avoid coordinates, poses, joints, or trajectories?
- Does every result clearly say dispatch is disabled?

The manual review is software-only. It does not authorize or perform real VLA/robot execution.
