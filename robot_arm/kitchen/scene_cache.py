"""Per-process reuse of one compiled kitchen scene.

A collection worker builds the same scene several times for one sample (native
expert, each offline trial, independent replay). Compilation and identity
hashing are repeated work: keep the most recent compiled model as a private
template and hand out deep copies with fresh MjData, initialized exactly as
build_scene initializes it. Callers never receive the template itself.
"""
from __future__ import annotations

import copy
from dataclasses import replace

import mujoco

from .model import build_scene

_TEMPLATE: tuple | None = None  # (key, KitchenModel, qpos, ctrl); one entry bounds memory


def cached_build_scene(*, seed=0, include_kitchen=True, layout_id="canonical"):
    global _TEMPLATE
    key = (int(seed), bool(include_kitchen), str(layout_id))
    if _TEMPLATE is None or _TEMPLATE[0] != key:
        scene = build_scene(seed=seed, include_kitchen=include_kitchen, layout_id=layout_id)
        _TEMPLATE = (key, scene, scene.data.qpos.copy(), scene.data.ctrl.copy())
    _, template, qpos, ctrl = _TEMPLATE
    model = copy.deepcopy(template.model)
    data = mujoco.MjData(model)
    data.qpos[:] = qpos
    data.ctrl[:] = ctrl
    mujoco.mj_forward(model, data)
    return replace(template, model=model, data=data)


def clear_scene_cache():
    global _TEMPLATE
    _TEMPLATE = None
