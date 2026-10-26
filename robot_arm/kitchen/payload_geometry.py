"""Rigid-payload scratch hypothesis reused from the approved V3 geometry audit.

Never attaches a runtime body. A geometry PASS cannot establish physical grip.
"""
import mujoco
import numpy as np

from .collision import CollisionGeometry
from .contact import object_geom_ids
from .control import tcp_pose
from .registry import geometry_bounds


class CarriedGeometry(CollisionGeometry):
    def __init__(self,scene,qgrasp,side,names):
        super().__init__(scene)
        self.arm=scene.arms[side]
        d=mujoco.MjData(self.m);d.qpos[:]=qgrasp
        mujoco.mj_kinematics(self.m,d)
        tcp,rotation=tcp_pose(d,self.arm)
        self.frames={}
        self.geoms=set().union(*(object_geom_ids(scene,n) for n in names))
        for name in names:
            body=self.m.body(name).id
            joint=int(self.m.body_jntadr[body])
            if self.m.jnt_type[joint]!=mujoco.mjtJoint.mjJNT_FREE:raise ValueError("Free payload required")
            self.frames[name]=(int(self.m.jnt_qposadr[joint]),rotation.T@(d.xpos[body]-tcp),rotation.T@d.xmat[body].reshape(3,3))
        for g in self.geoms:
            self.hinge[g]=0;self.slide[g]=0;self.free.pop(g,None)
            length=float(np.linalg.norm(d.geom_xpos[g]-tcp)+self.radius[g]+np.linalg.norm(self.arm.tcp_offset))
            b=self.arm.tcp_body
            while b:
                start,count=int(self.m.body_jntadr[b]),int(self.m.body_jntnum[b])
                for j in range(start,start+count):
                    if self.m.jnt_type[j]==mujoco.mjtJoint.mjJNT_HINGE:
                        self.hinge[g,int(self.m.jnt_qposadr[j])]=length+np.linalg.norm(self.m.jnt_pos[j])
                length+=float(np.linalg.norm(self.m.body_pos[b])+2*np.sum(np.linalg.norm(self.m.jnt_pos[start:start+count],axis=1)))
                b=int(self.m.body_parentid[b])

    def evaluated(self,q):
        d=mujoco.MjData(self.m);d.qpos[:]=q
        mujoco.mj_kinematics(self.m,d)
        position,rotation=tcp_pose(d,self.arm)
        result=q.copy()
        for adr,p,r in self.frames.values():
            result[adr:adr+3]=position+rotation@p
            mujoco.mju_mat2Quat(result[adr+3:adr+7],(rotation@r).ravel())
        return result

    def distances(self,q,pairs,cutoff=.3):
        return super().distances(self.evaluated(q),pairs,cutoff)

    def certificate(self,q0,q1,pairs=None,margin=-.003,intentional_pairs=(),**kwargs):
        pairs=self.pairs() if pairs is None else pairs
        internal=np.asarray([int(a) in self.geoms and int(b) in self.geoms for a,b in pairs],dtype=bool)
        invariant=pairs[internal]
        if len(invariant):
            allowed=self.intentional_ids(intentional_pairs)
            thresholds=np.asarray([0. if frozenset(map(int,pair)) in allowed else margin
                                   for pair in invariant])
            values=self.distances(q0,invariant)
            if np.any(values<thresholds-1e-9):
                return {"status":"RIGID_INTERNAL_PAYLOAD_COLLISION","pass":False,
                        **self._result(values,invariant,thresholds)}
        # These shapes share one rigid transform in this scratch hypothesis, so
        # their relative distance is invariant; an independent-motion bound is inappropriate.
        result=super().certificate(q0,q1,pairs[~internal],margin=margin,
                                   intentional_pairs=intentional_pairs,**kwargs)
        result["invariant_rigid_payload_pairs_checked"]=int(len(invariant))
        result["rigid_payload_hypothesis_not_physics"]=True
        return result

    def support_lift_proof(self,a,b,plane_z):
        start=mujoco.MjData(self.m);start.qpos[:]=self.evaluated(a)
        mujoco.mj_kinematics(self.m,start)
        if any(geometry_bounds(self.m,start,g)[0][2]<plane_z-1e-9 for g in self.geoms):
            return {"status":"INITIAL_SUPPORT_PENETRATION","pass":False}
        delta=b-a
        velocity=np.zeros(self.m.nv);velocity[list(self.arm.dofs)]=delta[list(self.arm.qpos)]
        angular_sum=float(np.abs(delta[list(self.arm.qpos)]).sum())
        speed_bounds=self.hinge@np.abs(delta)
        pending=[(0.,1.,0)];intervals=0
        while pending:
            lo,hi,depth=pending.pop()
            d=mujoco.MjData(self.m);d.qpos[:]=self.evaluated(a+(lo+hi)/2*delta)
            mujoco.mj_kinematics(self.m,d);mujoco.mj_comPos(self.m,d)
            lower=[]
            for g in self.geoms:
                jp=np.zeros((3,self.m.nv));jr=np.zeros((3,self.m.nv))
                mujoco.mj_jac(self.m,d,jp,jr,d.geom_xpos[g],self.arm.tcp_body)
                v,omega=jp@velocity,jr@velocity
                minimum=v[2]-self.radius[g]*np.linalg.norm(omega[:2])
                lower.append(float(minimum-4*angular_sum*speed_bounds[g]*(hi-lo)/2))
            if min(lower)>=0:intervals+=1
            elif depth>=16:return {"status":"UNRESOLVED","pass":False,"depth":depth}
            else:
                mid=(lo+hi)/2;pending.extend(((lo,mid,depth+1),(mid,hi,depth+1)))
        return {"status":"UPWARD_POINT_VELOCITY_BOUND_PASS","pass":True,"intervals":intervals,
                "plane_z_m":plane_z,"not_a_dynamic_support_claim":True}
