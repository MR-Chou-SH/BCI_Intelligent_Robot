"""Shared expert constants and bounded reference-call helpers."""
from __future__ import annotations
import json
import math

import numpy as np

from integration.kitchen.planning.reference_trajectories import ASSETS, inspect

def rz(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0.], [s, c, 0.], [0., 0., 1.]])


def reference_calls_between(start,end):
    """Closed control-frame window, including a clipped last RPC batch."""
    if start%20 or end%20 or end<=start:raise ValueError("REFERENCE_CONTROL_FRAME_WINDOW")
    inspect(ASSETS)
    selected=[]
    for line in (ASSETS/"burger_public_calls.jsonl").read_text().splitlines():
        row=json.loads(line);tick=row["start_tick"]
        if row["method"] in ("reset","hold"):continue
        if not (start<=tick<end or tick==end and row["method"]=="end_contact"):continue
        if row["method"]=="execute_trajectory":
            row["args"]["waypoints"]=row["args"]["waypoints"][:(end-tick)//20]
        elif row["method"]=="step":
            row["args"]["n_substeps"]=min(row["args"]["n_substeps"],end-tick)
            row["end_tick"]=min(row["end_tick"],end)
        selected.append(row)
    return selected


class ExpertFailure(RuntimeError):
    pass



DOWN = np.array([[0., -1., 0.], [-1., 0., 0.], [0., 0., -1.]])
ENABLE_ELBOW_DOOR_CHECK = False
