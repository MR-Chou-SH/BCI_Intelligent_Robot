"""Measure gripper semantics from the actual collision meshes and dynamics."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
import mujoco
from .model import build_scene

def pad_vertices(model,data,geom):
    mesh=int(model.geom_dataid[geom])
    start=int(model.mesh_vertadr[mesh]); count=int(model.mesh_vertnum[mesh])
    vertices=model.mesh_vert[start:start+count]
    return vertices @ data.geom_xmat[geom].reshape(3,3).T + data.geom_xpos[geom]

def fingertip_gap(model,data,arm):
    rotation=data.xmat[arm.tcp_body].reshape(3,3)
    origin=data.xpos[arm.tcp_body]
    projections=[]
    counts=[]
    for geom in arm.pad_geoms:
        points=(pad_vertices(model,data,geom)-origin)@rotation
        # Distal pad working region measured from actual mesh extension.
        tip=points[(points[:,2]>=arm.tcp_offset[2]-.010)&(np.abs(points[:,1]-arm.tcp_offset[1])<.025)]
        if len(tip)<3: raise RuntimeError("No fingertip mesh vertices in calibrated working region")
        counts.append(len(tip)); projections.append(tip[:,0])
    projections.sort(key=lambda p:float(p.mean()))
    gap=float(projections[1].min()-projections[0].max())
    return {"inner_gap_m":gap,"mesh_vertex_counts":counts,
            "finger_joint_positions_m":[float(data.qpos[i]) for i in arm.fingers],
            "tendon_coordinate_m":float(np.mean(data.qpos[list(arm.fingers)]))}

def measure_semantics():
    scene=build_scene(include_kitchen=False)
    m,d=scene.model,scene.data
    # Calibration is an isolated actual-MJCF free-jaw probe, not a grasp test.
    m.opt.gravity[:]=0
    measurements=[]
    for command_left,command_right in ((0,0),(.025,0),(.05,0),(0,0),(0,.025),(0,.05),(0,0)):
        for side,command in (("left",command_left),("right",command_right)):
            d.ctrl[scene.arms[side].gripper_actuator]=command
        for _ in range(750): mujoco.mj_step(m,d)
        measurements.append({"targets":{"left":command_left,"right":command_right},
            "sides":{s:fingertip_gap(m,d,a) for s,a in scene.arms.items()}})
    opened=measurements[0]["sides"]
    left_mid=measurements[1]["sides"]; left_closed=measurements[2]["sides"]
    right_mid=measurements[4]["sides"]; right_closed=measurements[5]["sides"]
    checks={}
    for s,mid,closed in (("left",left_mid,left_closed),("right",right_mid,right_closed)):
        checks[s+"_direction"]=opened[s]["inner_gap_m"]>mid[s]["inner_gap_m"]>closed[s]["inner_gap_m"]
        checks[s+"_open_width"]=.06<opened[s]["inner_gap_m"]<.11
        checks[s+"_closed_width"]=-.002<closed[s]["inner_gap_m"]<.008
        other="right" if s=="left" else "left"
        checks[s+"_independent"]=abs(opened[other]["inner_gap_m"]-closed[other]["inner_gap_m"])<.001
    ranges={s:{"actuator":m.actuator(a.gripper_actuator).name,
        "ctrlrange":m.actuator_ctrlrange[a.gripper_actuator].tolist(),
        "pad_meshes":[m.mesh(int(m.geom_dataid[g])).name for g in a.pad_geoms]}
        for s,a in scene.arms.items()}
    return {"status":"PASS" if all(checks.values()) else "FAIL",
        "evidence_kind":"ACTUAL_MJCF_DYNAMIC_FREE_JAW_CALIBRATION",
        "not_a_grasp_or_human_teleop_test":True,
        "model_source_sha256":scene.source_sha256,"mujoco":mujoco.__version__,
        "geometry_definition":"distal_collision_mesh_v1",
        "tcp_offsets_m":{s:list(a.tcp_offset) for s,a in scene.arms.items()},
        "model_builder_sha256":__import__("hashlib").sha256(Path(__file__).with_name("model.py").read_bytes()).hexdigest(),
        "control_units":"tendon position meters; 0 open, increasing closes, confirmed by mesh spacing",
        "state_units":"mean of the two measured slide joint positions meters",
        "checks":checks,"mapping":ranges,"measurements":measurements}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): raise FileExistsError(args.output)
    result=measure_semantics()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps({"status":result["status"],"checks":result["checks"],"output":str(args.output)}))
    return 0 if result["status"]=="PASS" else 1

if __name__=="__main__": raise SystemExit(main())
