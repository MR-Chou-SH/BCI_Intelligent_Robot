# M29 Semantic Bridge Demo Runbook

## Start the interactive demo

Open PowerShell at the repository root and run:

```powershell
& '.venv\Scripts\python.exe' -m integration.semantic_bridge_cli
```

The default scene catalog is the M20 assistive-desk snapshot frozen with this M29 attempt. Set `DEEPSEEK_API_KEY` in the process environment before launch. The demo does not write the key to disk.

To run one pair without entering the interactive loop:

```powershell
& '.venv\Scripts\python.exe' -m integration.semantic_bridge_cli '手机' '无线充电座'
```

Use an optional task instruction to disambiguate a pair:

Pass observed current object state with the --states-json option. For a known closed container, the validated plan can open, store, and close it; unknown state is never guessed.

```powershell
& '.venv\Scripts\python.exe' -m integration.semantic_bridge_cli '小药盒' '收纳盒' --intent '把小药盒放进收纳盒' --states-json '{"assist_storage_box":{"lid":"closed"}}'
```

Explore unlisted nouns in the explicitly unverified mode:

```powershell
& '.venv\Scripts\python.exe' -m integration.semantic_bridge_cli --mode free 'wristwatch' 'wooden tray'
```

Free mode marks inferred facts as unverified and never permits dispatch. Strict scene mode is the primary grounded mode.

## Suggested manual checks

1. `手机` + `无线充电座` — Chinese grounding and concise instruction.
2. `small medicine box` + `storage box`, with an intent to store — multi-step container plan; a known open/closed state is needed for an executable plan.
3. `phone` + `storage box`, with “put inside” — explicit relation.
4. `小药盒` + `USER ZONE`, with “放到用户区域” — placement on the user zone.
5. `medicine box` + “press” as the intent — invalid affordance; expect no actions.
6. `button` + `controller`, with no intent — review whether the system abstains or explains ambiguity.
7. `wireless charging pad` + `phone` — reverse noun order; plan must keep selected-object order in its grounding output.
8. `cell phone` + `charging pad` — English alias grounding.
9. `book` + `bookshelf` — held-out bookshelf scene:

   ```powershell
   & '.venv\Scripts\python.exe' -m integration.semantic_bridge_cli --scene 'research_analysis\m29_semantic_bridge_demo_20261002\attempt-01\future_book_pen_scene_snapshot.json' 'book' 'bookshelf' --intent 'Place the book on the bookshelf'
   ```

10. `cup` + `serving tray` — held-out kitchenware scene:

   ```powershell
   & '.venv\Scripts\python.exe' -m integration.semantic_bridge_cli --scene 'research_analysis\m29_semantic_bridge_demo_20261002\attempt-01\future_kitchenware_snapshot.json' 'cup' 'serving tray' --intent 'Place the cup on the serving tray'
   ```

11. Free mode: `wristwatch` + `wooden tray` — inspect unverified tags and ensure dispatch stays disabled.
12. Free mode: `ceramic mug` + `silicone mat` — judge the generated bilingual imperative separately from the provisional affordance facts.

For every manual check, verify the status, object IDs, action order, concise Chinese/English language, and the `dispatch disabled` line. Free-noun output is exploratory and must not be sent to a robot or VLA policy.
