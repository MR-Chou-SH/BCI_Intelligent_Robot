"""Dev-only key-state checkpoints.

Saving the full execution state at milestones (door opened, meal picked,
payload turned) lets planning/iteration resume from that state instead of
replaying the whole episode. These checkpoints are for development only; the
collected QUALIFIED_EXPERT data still runs and records from the initial state.
"""
from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np


def save(expert, path):
    platform = expert.platform
    data, controller = platform.scene.data, platform.controller
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, qpos=np.array(data.qpos), qvel=np.array(data.qvel),
             reference=np.array(controller.reference))
    meta = {"tick": int(controller.tick),
            "held": list(expert.held) if expert.held else None,
            "contract": expert.contract,
            "stage": expert.stage,
            "devices": controller.devices.snapshot()}
    Path(str(path) + ".json").write_text(
        json.dumps(meta, default=str, ensure_ascii=False), encoding="utf-8")
    return str(path)


def load(expert, path):
    platform = expert.platform
    model, data, controller = platform.scene.model, platform.scene.data, platform.controller
    stored = np.load(path)
    meta = json.loads(Path(str(path) + ".json").read_text(encoding="utf-8"))
    data.qpos[:] = stored["qpos"]
    data.qvel[:] = stored["qvel"]
    mujoco.mj_forward(model, data)
    controller.tick = int(meta["tick"])
    controller.reference = np.array(stored["reference"], dtype=float)
    controller.accepted = controller.reference.copy()
    controller.previous = controller.reference.copy()
    controller.stopped = False
    controller.stop_reason = None
    controller.stop_evidence = []
    controller.frame_step = 0
    controller.devices.restore(meta["devices"])
    expert.held = tuple(meta["held"]) if meta["held"] else None
    expert.contract = meta["contract"]
    expert.stage = meta["stage"]
    client = getattr(expert, "client", None)
    if client is not None:
        # The RPC token embeds the sim_tick; refresh it after restoring a state.
        client.token = platform.token()
    return expert
