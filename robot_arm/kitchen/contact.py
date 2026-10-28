"""Measured bilateral contacts. This module never attaches or teleports objects."""
import numpy as np
import mujoco

def object_geom_ids(scene,object_name):
    body=scene.objects[object_name]
    # Body ancestry is fixed for a compiled scene. Return an independent set
    # to preserve the public mutable-set interface, not a shared cache entry.
    if getattr(scene,'_geom_cache_model',None) is not scene.model:
        scene._geom_cache_model=scene.model;scene._object_geom_cache={}
    if body in scene._object_geom_cache:return set(scene._object_geom_cache[body])
    result=set()
    for g in range(scene.model.ngeom):
        current=int(scene.model.geom_bodyid[g])
        while current:
            if current==body:result.add(g);break
            current=int(scene.model.body_parentid[current])
    scene._object_geom_cache[body]=frozenset(result)
    return result

def contact_evidence(scene,side,object_name):
    m,d=scene.model,scene.data
    arm=scene.arms[side];objects=object_geom_ids(scene,object_name)
    forces={g:0. for g in arm.pad_geoms}
    pairs=[]
    for i in range(d.ncon):
        c=d.contact[i]
        for pad in arm.pad_geoms:
            other=int(c.geom2) if int(c.geom1)==pad else int(c.geom1) if int(c.geom2)==pad else None
            if other not in objects:continue
            value=np.zeros(6);mujoco.mj_contactForce(m,d,i,value)
            forces[pad]+=max(0.,float(value[0]))
            pairs.append({"pad_geom":pad,"object_geom":other,"distance_m":float(c.dist),"normal_force_n":float(value[0])})
    return {"bilateral":all(f>=.5 for f in forces.values()),"normal_forces_n":list(forces.values()),"pairs":pairs}

class ContactTracker:
    def __init__(self,scene,side,object_name):
        self.scene=scene;self.side=side;self.object_name=object_name
        self.consecutive_seconds=0.;self.max_bilateral_seconds=0.
        self.latest=None;self.samples=0;self.ever_valid=False
        self.peak_force=0.
    def update(self,controller=None):
        self.latest=contact_evidence(self.scene,self.side,self.object_name)
        if self.latest["bilateral"]:self.consecutive_seconds+=self.scene.model.opt.timestep
        else:self.consecutive_seconds=0.
        self.max_bilateral_seconds=max(self.max_bilateral_seconds,self.consecutive_seconds)
        self.ever_valid|=self.consecutive_seconds>=.05
        self.samples+=1;self.peak_force=max(self.peak_force,max(self.latest["normal_forces_n"]))
    def summary(self):
        return {"ever_valid":self.ever_valid,"max_bilateral_seconds":self.max_bilateral_seconds,
            "peak_pad_normal_force_n":self.peak_force,"samples":self.samples,"last":self.latest,
            "grasp_mode":"pure_contact","constraint_enabled":False}

def unexpected_contacts(scene,allowed_object=None):
    m,d=scene.model,scene.data
    permitted=object_geom_ids(scene,allowed_object) if allowed_object else set()
    pads=set(g for arm in scene.arms.values() for g in arm.pad_geoms)
    results=[]
    for i in range(d.ncon):
        c=d.contact[i];g1,g2=int(c.geom1),int(c.geom2)
        b1,b2=m.body(int(m.geom_bodyid[g1])).name,m.body(int(m.geom_bodyid[g2])).name
        robot1=b1.startswith(("left_","right_"));robot2=b2.startswith(("left_","right_"))
        if not(robot1 or robot2):continue
        # Fixed robot base rests on the counter by construction.
        if (robot1 and b1.endswith("fr3_link0")) or (robot2 and b2.endswith("fr3_link0")):continue
        if (g1 in pads and g2 in permitted) or (g2 in pads and g1 in permitted):continue
        f=np.zeros(6);mujoco.mj_contactForce(m,d,i,f)
        if f[0]>5.:
            results.append({"body1":b1,"body2":b2,"force_n":float(f[0]),"distance_m":float(c.dist)})
    return results
