# M32 Manual Review Runbook

## Launch

From the repository root:

```powershell
& '.venv\Scripts\python.exe' -m integration.semantic_context_demo_cli
```

Enter a scene description, review parsed selectable targets, any context-only mentions, and explicitly stated color/state, then type `start`. Enter a display name, Chinese/English alias, or the number shown in the current candidate order. At each round, inspect the relational affordance, semantic score, global q, status, top mass, margin, entropy and DeepSeek latency.

Commands during selection: `help`, `scene`, `history`, `context`, `undo`, `reset`, `submit`/`done`/`end`, and `q`. Undo recomputes M30 Context from the restored ordered history. Submit ends the demo and never invokes VLA or robot execution.

## Ready-to-copy scenes and suggested paths

### 1. Laundry room

```text
这是一个洗衣房场景。可交互物品包括白色衬衫、蓝色牛仔裤、洗衣篮、洗衣机、洗衣液、晾衣架、关闭的储物柜和垃圾桶。
```

Try `白色衬衫 → 洗衣篮 → 洗衣机`, then undo once and inspect the changed Context.

### 2. Kitchen

```text
这是一个厨房场景。可交互物品包括红色苹果、水果刀、白色盘子、橙子、榨汁机、水槽、储物柜和垃圾桶。
```

Try `红色苹果 → 水果刀 → 白色盘子`; separately try `橙子 → 榨汁机`.

### 3. Study / office

```text
这是一个书房场景。可交互物品包括一本书、书架、蓝色钢笔、笔筒、文件、托盘、关闭的抽屉和垃圾桶。
```

Try `一本书 → 书架 → 蓝色钢笔 → 笔筒`.

### 4. Bedroom / clothing organization

```text
这是一个卧室和衣物整理区场景。可交互物品包括红色衬衫、蓝色外套、衣架、关闭的衣柜、洗衣篮、床、床头柜和抽屉。
```

Try `红色衬衫 → 衣架 → 关闭的衣柜`; compare with `衣架 → 红色衬衫`.

### 5. Electronics desk

```text
这是一个电子设备桌面场景。可交互物品包括黑色手机、无线充电座、USB线、笔记本电脑、杯子、书桌、抽屉和用户交接区。
```

Try `黑色手机 → 无线充电座 → USB线`; inspect whether the context reports alternatives and uncertainty.

### 6. Mixed ambiguous scene

```text
这是一个混合生活场景。可交互物品包括红色苹果、水果刀、白色盘子、手机、无线充电座、关闭的收纳盒、书架和垃圾桶。
```

Try `手机 → 无线充电座`, then inspect whether the closed box and shelf remain plausible without the display overstating certainty. Also try `无线充电座 → 手机`.

## Review checklist

- Is the highest-q candidate reasonable for the complete history?
- Are other plausible candidates still represented?
- Did q change sensibly after the next selection, rather than merely deleting one element?
- Did selected objects disappear from the candidate list and q dimension?
- Did the parser invent an object not present in the text?
- Did it invent a color or state? Unspecified facts must remain absent.
- Is uncertainty visible in an ambiguous scene?
- Does the result feel like open semantic reasoning rather than a fixed object-pair lookup?
- Do undo and reset recompute from their restored histories?

A bad result includes an ungrounded candidate, a selected object still in q, an incorrect q dimension/sum, fabricated observed color/state, an unsupported specific relation, stale ranking after undo, or a sharply peaked prior despite visible ambiguity.

## Scope

M32 uses no EEG, Quest, image input, or robot dispatch. This is a manually reviewable semantic Context CLI only.
