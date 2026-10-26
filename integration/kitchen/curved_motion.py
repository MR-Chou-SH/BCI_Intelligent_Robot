"""Small checked Bezier candidates and shape-preserving joint interpolation.

All kinematics/geometry operate on detached MuJoCo data. No runtime attachment,
object teleport, controller replacement or new dependency.
"""
from dataclasses import replace
import math
import mujoco
import numpy as np

from robot_arm.kitchen.collision import CollisionGeometry
from robot_arm.kitchen.payload_geometry import CarriedGeometry
from robot_arm.kitchen.contact import object_geom_ids
from robot_arm.kitchen.control import solve_ik, tcp_pose
from robot_arm.kitchen.registry import geometry_bounds, object_bounds


def placement_tcp_goal(scene,side,name,support,desired_root_xy):
    """Re-query the held shape after settling/lifting; preserve its XY slot."""
    tcp,_=tcp_pose(scene.data,scene.arms[side])
    root=scene.data.xpos[scene.objects[name]]
    low,_=object_bounds(scene,name)
    _,top=geometry_bounds(scene.model,scene.data,scene.model.geom(support).id)
    desired=np.r_[np.asarray(desired_root_xy),top[2]+root[2]-low[2]+.001]
    return desired-(root-tcp)


def joint_spline(points, speed, frames=None):
    """C1 shape-preserving Hermite path, C2 scalar start/stop timing at 25Hz.

    Sampling advances along the whole path, never lands/stops at every IK knot.
    Returns commands and their chord parameters, including the final endpoint.
    """
    points=np.asarray(points,dtype=float)
    keep=np.r_[True,np.max(np.abs(np.diff(points,axis=0)),axis=1)>1e-8]
    points=points[keep]
    if len(points)<2:return points[-1:],np.zeros(1),np.zeros(len(points))
    h=np.max(np.abs(np.diff(points,axis=0)),axis=1)
    knots=np.r_[0.,np.cumsum(h)];delta=np.diff(points,axis=0)/h[:,None]
    slopes=np.zeros_like(points);slopes[0]=delta[0];slopes[-1]=delta[-1]
    if len(points)>2:
        same=delta[:-1]*delta[1:]>0
        w1=(2*h[1:]+h[:-1])[:,None];w2=(h[1:]+2*h[:-1])[:,None]
        denominator=w1/np.where(same,delta[:-1],1.)+w2/np.where(same,delta[1:],1.)
        slopes[1:-1]=np.where(same,(w1+w2)/np.where(same,denominator,1.),0.)
    def evaluate(s, derivative=False):
        index=np.clip(np.searchsorted(knots,s,side='right')-1,0,len(h)-1)
        u=((s-knots[index])/h[index])[:,None];scale=h[index,None]
        a,b=points[index],points[index+1];ma,mb=slopes[index],slopes[index+1]
        if derivative:
            return ((6*u*u-6*u)*a+(-6*u*u+6*u)*b)/scale+(3*u*u-4*u+1)*ma+(3*u*u-2*u)*mb
        return (2*u**3-3*u*u+1)*a+(-2*u**3+3*u*u)*b+(u**3-2*u*u+u)*scale*ma+(u**3-u*u)*scale*mb
    alpha=.12;vmax=1/(1-alpha)
    peak=float(np.max(np.abs(evaluate(np.linspace(0,knots[-1],max(200,8*len(points))),True))))
    count=max(8,int(math.ceil(knots[-1]*vmax*peak/speed))) if frames is None else int(frames)
    t=np.arange(1,count+1,dtype=float)/count
    def ramp(x):return vmax*(x/2-alpha/(2*np.pi)*np.sin(np.pi*x/alpha))
    progress=np.where(t<alpha,ramp(t),np.where(t>1-alpha,1-ramp(1-t),vmax*(t-alpha/2)))
    s=np.clip(progress,0,1)*knots[-1];values=evaluate(s);values[-1]=points[-1]
    return values,s,knots


def bezier_positions(start, end, height, mode, spacing=.008,bend=0.):
    start,end=np.asarray(start),np.asarray(end)
    if mode=='approach':
        # Descend while translating. The terminal tangent aligns with the
        # vertical grasp; only the last 25mm use the acquire contract.
        pre=end+[0,0,.025]
        controls=np.array([start,start+.35*(pre-start)+[0,0,height],pre+[0,0,.045+height*.3],pre])
    else:
        controls=np.array([start,start+[0,bend*(end[1]-start[1]),height],end+[0,0,height],end])
    length=sum(np.linalg.norm(b-a) for a,b in zip(controls[:-1],controls[1:]))
    t=np.linspace(0,1,max(8,int(math.ceil(length/spacing)))+1)
    positions=((1-t[:,None])**3*controls[0]+3*(1-t[:,None])**2*t[:,None]*controls[1]
               +3*(1-t[:,None])*t[:,None]**2*controls[2]+t[:,None]**3*controls[3])
    join=len(positions)-1
    if mode=='approach':positions=np.vstack([positions,np.linspace(positions[-1],end,6)[1:]])
    return positions,join


class _CarriedScratch(CarriedGeometry):
    def evaluated(self,q):
        if not hasattr(self,'_payload_data'):self._payload_data=mujoco.MjData(self.m)
        d=self._payload_data;d.qpos[:]=q;mujoco.mj_kinematics(self.m,d)
        position,rotation=tcp_pose(d,self.arm);result=q.copy()
        for adr,p,r in self.frames.values():
            result[adr:adr+3]=position+rotation@p
            mujoco.mju_mat2Quat(result[adr+3:adr+7],(rotation@r).ravel())
        return result


def curve_candidates(scene, side, end, rotation, *, mode, payload=(), target=None, support=None,diagnostics=None):
    """Yield only IK/geometry-checked curved routes; no live motion is advanced."""
    arm=scene.arms[side];m=scene.model
    geometry=_CarriedScratch(scene,scene.data.qpos,side,payload) if payload else CollisionGeometry(scene)
    carried=set().union(*(object_geom_ids(scene,n) for n in payload)) if payload else set()
    active={g for g in geometry.robot if m.body(int(m.geom_bodyid[g])).name.startswith(side+'_')}
    hand={g for g in active if m.body(int(m.geom_bodyid[g])).name.startswith(side+'_umi')}
    fingers={int(m.geom_bodyid[g]) for g in arm.pad_geoms}
    pads={g for g in active if int(m.geom_bodyid[g]) in fingers}
    pairs=np.asarray([p for p in geometry.pairs(payload) if any(int(g) in active|carried for g in p)
        and not all(int(g) in pads for g in p)
        and not (any(int(g) in carried for g in p) and any(int(g) in hand for g in p))
        and not all(int(g) in carried for g in p)],dtype=int).reshape(-1,2)
    intentional=object_geom_ids(scene,target) if target else set()
    support_ids={m.geom(support).id} if support else set()
    end_pairs=np.asarray([p for p in pairs if not (
        (any(int(g) in intentional for g in p) and any(int(g) in hand for g in p)) or
        (any(int(g) in carried for g in p) and any(int(g) in support_ids for g in p)))],dtype=int).reshape(-1,2)
    start,initial=tcp_pose(scene.data,arm)
    qa,qb=np.empty(4),np.empty(4);mujoco.mju_mat2Quat(qa,initial.ravel());mujoco.mju_mat2Quat(qb,np.asarray(rotation).ravel())
    if qa@qb<0:qb=-qb
    def clearance(q, near_end):
        selected=end_pairs if near_end else pairs
        return float(np.min(geometry.distances(q,selected))) if len(selected) else 1.
    heights=(0.,.055,.11,.18) if mode=='approach' else (.07,.12,.18,.25,.30,.36)
    options=[(height,bend) for bend in ((0.,.7) if mode=='carry' else (0.,)) for height in heights]
    for height,bend in options:
        positions,join=bezier_positions(start,np.asarray(end),height,mode,bend=bend)
        data=mujoco.MjData(m);data.qpos[:]=scene.data.qpos
        view=replace(scene,data=data);path=[data.qpos[list(arm.qpos)].copy()];minimum=1.;good=True
        for i,position in enumerate(positions[1:],1):
            fraction=min(1.,i/max(1,join)*1.5)
            fraction=fraction*fraction*(3-2*fraction)
            quat=(1-fraction)*qa+fraction*qb;quat/=np.linalg.norm(quat)
            mat=np.empty(9);mujoco.mju_quat2Mat(mat,quat)
            result=solve_ik(view,side,position,mat.reshape(3,3),max_iterations=400,
                            position_tolerance=.0003,rotation_tolerance=.004)
            if not result.success:
                result=solve_ik(view,side,position,mat.reshape(3,3),max_iterations=700,
                                position_tolerance=.0015,rotation_tolerance=.008)
            if not result.success:
                if diagnostics is not None:diagnostics.append({'height':height,'index':i,'reason':'IK','position_error':result.position_error})
                good=False;break
            previous=data.qpos.copy();candidate=previous.copy();candidate[list(arm.qpos)]=result.q
            near=i>=join if mode=='approach' else np.linalg.norm(position-end)<.018
            samples=max(1,int(math.ceil(np.max(np.abs(result.q-path[-1]))/.025)))
            for alpha in np.linspace(0,1,samples+1):
                distance=clearance(previous+(candidate-previous)*alpha,near)
                minimum=min(minimum,distance)
                if distance<.001:
                    if diagnostics is not None:
                        selected=end_pairs if near else pairs
                        distances=geometry.distances(previous+(candidate-previous)*alpha,selected)
                        pair=selected[int(np.argmin(distances))]
                        diagnostics.append({'height':height,'index':i,'reason':'clearance','pair':[m.geom(int(g)).name for g in pair],'distance_m':distance})
                    good=False;break
            if not good:break
            data.qpos[:]=candidate;path.append(result.q.copy())
        if good:
            # Carry support contact is only intentional on the final approach.
            contact_index=join if mode=='approach' else max(1,len(path)-4)
            yield path,contact_index,clearance,{'mode':mode,'height_m':height,'bend':bend,'waypoints':len(path),'sampled_minimum_m':minimum}
