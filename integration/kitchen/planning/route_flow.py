"""Small checked Cartesian corner rounds; cavity entry remains straight."""
from dataclasses import replace
import math
import mujoco
import numpy as np
from .curved_motion import _CarriedScratch
from robot_arm.kitchen.contact import object_geom_ids
from robot_arm.kitchen.control import solve_ik,tcp_pose


def rounded_positions(points,radius=.015,spacing=.008,protected_tail=2):
    points=np.asarray(points,dtype=float);result=[points[0].copy()]
    def line(end):
        start=result[-1];count=max(1,math.ceil(np.linalg.norm(end-start)/spacing))
        result.extend(start+(end-start)*t for t in np.linspace(0,1,count+1)[1:])
    for i in range(1,len(points)-1):
        before,corner,after=points[i-1:i+2]
        first=np.linalg.norm(corner-before);second=np.linalg.norm(after-corner)
        if i>=len(points)-1-protected_tail or min(first,second)<1e-7:
            line(corner);continue
        distance=min(radius,first*.25,second*.25)
        enter=corner+(before-corner)*distance/first
        leave=corner+(after-corner)*distance/second
        line(enter)
        for t in np.linspace(0,1,max(4,math.ceil(2*distance/spacing))+1)[1:]:
            result.append((1-t)**2*enter+2*(1-t)*t*corner+t*t*leave)
    line(points[-1]);return np.asarray(result)


def rounded_oven_carry(scene,waypoints,rotation,payload):
    """Try a15mm corner round outside the cavity; fail back before moving."""
    m=scene.model;arm=scene.arms['right'];start=tcp_pose(scene.data,arm)[0]
    positions=rounded_positions([start,*waypoints],protected_tail=2)
    geometry=_CarriedScratch(scene,scene.data.qpos,'right',payload)
    carried=set().union(*(object_geom_ids(scene,n) for n in payload))
    hand={g for g in geometry.robot if m.body(int(m.geom_bodyid[g])).name.startswith('right_umi')}
    pairs=np.asarray([p for p in geometry.pairs(payload)
        if not all(int(g) in carried for g in p)
        and not (any(int(g) in carried for g in p) and any(int(g) in hand for g in p))],dtype=int).reshape(-1,2)
    bottom=m.geom('microwave_bottom').id
    data=mujoco.MjData(m);data.qpos[:]=scene.data.qpos;view=replace(scene,data=data)
    path=[];minimum=1.
    for position in positions[1:]:
        result=solve_ik(view,'right',position,rotation,max_iterations=500,
                        position_tolerance=.0003,rotation_tolerance=.004)
        if not result.success:return None
        previous=data.qpos.copy();candidate=previous.copy();candidate[list(arm.qpos)]=result.q
        selected=pairs
        if np.linalg.norm(position-positions[-1])<.018:
            selected=np.asarray([p for p in pairs if not (bottom in p and any(int(g) in carried for g in p))],dtype=int).reshape(-1,2)
        samples=max(1,math.ceil(np.max(np.abs(candidate-previous))/.025))
        for f in np.linspace(0,1,samples+1):
            distance=float(np.min(geometry.distances(previous+(candidate-previous)*f,selected)))
            minimum=min(minimum,distance)
            if distance<.001:return None
        data.qpos[:]=candidate;path.append(result.q.copy())
    return path,minimum
