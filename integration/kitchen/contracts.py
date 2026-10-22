"""Kitchen action/frame contracts, frozen only after actual MJCF calibration."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import numpy as np

MJCF = Path(__file__).resolve().parents[2] / "robot_arm" / "assets" / "fr3_umi_merged.xml"

SCHEMA_VERSION=1
INPUT_SOURCES={"programmatic_test","pc_human_teleop","quest_human_teleop"}
C=np.array([[1.,0,0],[0,0,1],[0,1,0]])

def validate_gripper_calibration(path):
    report=json.loads(Path(path).read_text(encoding="utf-8"))
    expected=hashlib.sha256(MJCF.read_bytes()).hexdigest()
    builder=Path(__file__).resolve().parents[2]/"robot_arm/kitchen/model.py"
    if report.get("geometry_definition")!="distal_collision_mesh_v1" or report.get("model_builder_sha256")!=hashlib.sha256(builder.read_bytes()).hexdigest():
        raise ValueError("Empirical distal TCP geometry calibration missing or stale")
    if report.get("status")!="PASS" or report.get("model_source_sha256")!=expected:
        raise ValueError("Gripper calibration missing, failing or stale")
    checks=report.get("checks",{})
    required={s+"_"+check for s in ("left","right") for check in ("direction","open_width","closed_width","independent")}
    if not required.issubset(checks) or not all(checks[k] is True for k in required):
        raise ValueError("Incomplete empirical gripper checks")
    for side in ("left","right"):
        if report["mapping"][side]["ctrlrange"]!=[0.,.05]: raise ValueError("Unexpected tendon range")
    return report

def action16(values):
    values=np.asarray(values,dtype=np.float64)
    if values.shape!=(16,) or not np.isfinite(values).all(): raise ValueError("Invalid action16")
    if np.any(values[[7,15]]<0) or np.any(values[[7,15]]>.05): raise ValueError("Invalid tendon position")
    return values

def unity_pose(position,rotation):
    return C@np.asarray(position), C@np.asarray(rotation)@C.T

def state_names():
    return ([f"left_q{i}_measured_rad" for i in range(1,8)]
            + ["left_mean_measured_finger_slide_qpos_m"]
            + [f"right_q{i}_measured_rad" for i in range(1,8)]
            + ["right_mean_measured_finger_slide_qpos_m"])

def action_names():
    return ([f"left_q{i}_absolute_target_rad" for i in range(1,8)]
            + ["left_tendon_target_m"]
            + [f"right_q{i}_absolute_target_rad" for i in range(1,8)]
            + ["right_tendon_target_m"])

def actual_ctrl_names():
    return ([f"left_q{i}_actuator_data_ctrl_rad" for i in range(1,8)]
            + ["left_gripper_actuator_data_ctrl_m"]
            + [f"right_q{i}_actuator_data_ctrl_rad" for i in range(1,8)]
            + ["right_gripper_actuator_data_ctrl_m"])

def policy_delta(actions,state):
    result=np.array(actions,dtype=float,copy=True)
    state=np.asarray(state,dtype=float)
    mask=np.array([True]*7+[False]+[True]*7+[False])
    result[...,:16]-=np.where(mask,state[:16],0)
    return result

def policy_absolute(actions,state):
    result=np.array(actions,dtype=float,copy=True)
    state=np.asarray(state,dtype=float)
    mask=np.array([True]*7+[False]+[True]*7+[False])
    result[...,:16]+=np.where(mask,state[:16],0)
    return result[...,:16]
