"""Read-only physical predicates and explicitly simulated device timers."""
import math
import numpy as np
import mujoco
from .contact import object_geom_ids

SUPPORT_FORCE_THRESHOLD_N = .25
SUPPORT_NORMAL_MIN_UPWARD_Z = .5
SUPPORT_CENTER_CLEARANCE_M = .005
WASH_DURATION_S = 2.  # User-requested simulated wash dwell, 2026-11-06.
HEAT_DURATION_S = 2.  # Device-owned cycle; lamp goes out without a robot action.

class DeviceState:
    def __init__(self,scene):
        self.scene=scene
        m=scene.model
        self.joints={n:mujoco.mj_name2id(m,mujoco.mjtObj.mjOBJ_JOINT,n)
            for n in ("faucet_switch","microwave_hinge","microwave_start_slide")}
        self.enabled=all(j>=0 for j in self.joints.values())
        self.clean_seconds={n:0. for n in ("apple","pear","banana","tomato") if n in scene.objects}
        self.cleaned=set();self.heated=set();self.heating=None;self.heat_seconds=0.
        self.button_seconds=0.;self.button_latched=False;self.events=[]
        self.geom_sets={n:object_geom_ids(scene,n) for n in scene.objects}
        self.pads={g for a in scene.arms.values() for g in a.pad_geoms}
        self._pad_mask=np.zeros(m.ngeom,dtype=bool);self._pad_mask[list(self.pads)]=True
        self._geom_names=tuple(m.geom(i).name for i in range(m.ngeom))
        self._geom_ids={name:i for i,name in enumerate(self._geom_names) if name}
        self._object_masks={}
        for name,ids in self.geom_sets.items():
            mask=np.zeros(m.ngeom,dtype=bool);mask[list(ids)]=True
            self._object_masks[name]=mask
        self._support_groups={}
        from .layouts import layout_manifest
        self.version3=layout_manifest(scene.layout_id).get("profile")=="kitchen_platform_v3"
    def angle(self,name):
        return float(self.scene.data.qpos[self.scene.model.jnt_qposadr[self.joints[name]]])
    def geom(self,name):
        return self._geom_ids.get(name,-1)
    def support_evidence(self,name,support_names):
        """Return load-bearing upward-contact evidence for an object's support."""
        return self._support(name,support_names,details=True)
    def _support(self,name,support_names,*,details):
        m,d=self.scene.model,self.scene.data
        target=self.geom_sets[name]
        support_names=tuple(map(str,support_names))
        group=self._support_groups.get(support_names)
        if group is None:
            support_by_id={self.geom(n):n for n in support_names if self.geom(n)>=0}
            support_mask=np.zeros(m.ngeom,dtype=bool);support_mask[list(support_by_id)]=True
            group=support_by_id,support_mask
            if len(self._support_groups)>=64:self._support_groups.clear()
            self._support_groups[support_names]=group
        support_by_id,support_mask=group
        center=d.xpos[self.scene.objects[name]]
        contacts=[];max_upward_force=0.;max_qualifying_upward_force=0.;upward_forces=[]
        # Filter freshly measured contact IDs in NumPy before constructing any
        # Python evidence. Keep native order, forces and all support predicates.
        geoms=d.contact.geom
        target_mask=self._object_masks[name]
        matches=(target_mask[geoms[:,0]] & support_mask[geoms[:,1]]) | (
            target_mask[geoms[:,1]] & support_mask[geoms[:,0]])
        for i in np.flatnonzero(matches):
            c=d.contact[i];g1,g2=int(c.geom1),int(c.geom2)
            if g1 in target and g2 in support_by_id:
                target_geom,support_geom=g1,g2
                target_force_sign=-1.
            elif g2 in target and g1 in support_by_id:
                target_geom,support_geom=g2,g1
                target_force_sign=1.
            else:
                continue
            f=np.zeros(6);mujoco.mj_contactForce(m,d,i,f)
            # MuJoCo's normal points geom1 -> geom2; apply the equal/opposite
            # sign when the object is geom1 to recover force on the object.
            normal_on_object=target_force_sign*np.asarray(c.frame[:3],dtype=float)
            normal_force=float(f[0])
            vertical_force=normal_force*float(normal_on_object[2])
            below_center=float(c.pos[2])<float(center[2])+SUPPORT_CENTER_CLEARANCE_M
            # A capsule/mesh may distribute its load over several native
            # contacts. Test the same 0.25N threshold on their upward resultant,
            # rather than requiring a single arbitrarily discretized point to
            # carry it. Side/downward contacts never enter that resultant.
            qualifies=(normal_on_object[2]>SUPPORT_NORMAL_MIN_UPWARD_Z and
                vertical_force>0. and below_center)
            if qualifies:upward_forces.append(vertical_force)
            if not details:continue
            max_upward_force=max(max_upward_force,max(0.,vertical_force))
            if qualifies:
                max_qualifying_upward_force=max(max_qualifying_upward_force,vertical_force)
            contacts.append({
                "object_geom":self._geom_names[target_geom],
                "support_geom":support_by_id[support_geom],
                "normal_force_n":normal_force,
                "normal_on_object_world":normal_on_object.tolist(),
                "normal_force_world_z_n":vertical_force,
                "contact_position_world":np.asarray(c.pos,dtype=float).tolist(),
                "below_object_center":bool(below_center),
                "qualifies_as_upward_support":bool(qualifies),
            })
        # Preserve Python's float-sum semantics and native contact order in
        # both paths, including forces close to the acceptance threshold.
        total_upward_force=sum(upward_forces)
        if not details:return bool(total_upward_force>SUPPORT_FORCE_THRESHOLD_N)
        qualifying=sum(int(item["qualifies_as_upward_support"]) for item in contacts)
        return {
            "supported":bool(total_upward_force>SUPPORT_FORCE_THRESHOLD_N),
            "support_force_policy":"summed_upward_native_contacts_v2",
            "total_qualifying_upward_support_force_n":total_upward_force,
            "support_names":list(map(str,support_names)),
            "matched_contact_count":len(contacts),
            "support_contact_count":qualifying,
            "minimum_upward_force_n":SUPPORT_FORCE_THRESHOLD_N,
            "minimum_normal_vertical_component":SUPPORT_NORMAL_MIN_UPWARD_Z,
            "maximum_upward_normal_force_n":max_upward_force,
            "maximum_qualifying_upward_support_force_n":max_qualifying_upward_force,
            "contacts":contacts,
        }
    def supports(self,name,support_names):
        return self._support(name,support_names,details=False)
    def held(self,name):
        if self.version3:
            return self.grasp_evidence(name)["bilateral_contact"]
        target=self.geom_sets[name]
        return any((int(c.geom1) in target and int(c.geom2) in self.pads) or
                   (int(c.geom2) in target and int(c.geom1) in self.pads) for c in self.scene.data.contact)
    def grasp_evidence(self,name):
        target=self.geom_sets[name]
        by_arm={}
        for side,arm in self.scene.arms.items():
            forces=np.zeros(2)
            for i,c in enumerate(self.scene.data.contact):
                for k,pad in enumerate(arm.pad_geoms):
                    if ((int(c.geom1)==pad and int(c.geom2) in target) or
                        (int(c.geom2)==pad and int(c.geom1) in target)):
                        wrench=np.zeros(6)
                        mujoco.mj_contactForce(self.scene.model,self.scene.data,i,wrench)
                        forces[k]+=max(0.,float(wrench[0]))
            by_arm[side]={"pad_normal_forces_n":forces.tolist(),"bilateral_contact":bool(np.all(forces>=.5))}
        return {"bilateral_contact":any(v["bilateral_contact"] for v in by_arm.values()),
                "by_arm":by_arm,"not_a_lift_or_retention_proof":True}
    def touching_pads(self,name):
        geoms=self.scene.data.contact.geom;target=self._object_masks[name];pads=self._pad_mask
        return bool(np.any((target[geoms[:,0]] & pads[geoms[:,1]]) |
                           (target[geoms[:,1]] & pads[geoms[:,0]])))
    def _inside_local(self,name,body,lower,upper):
        from .registry import object_bounds
        lo,hi=object_bounds(self.scene,name,frame_body=body)
        return bool(np.all(lo>=np.asarray(lower)-.001) and np.all(hi<=np.asarray(upper)+.001))
    def in_basket(self,name):
        if self.version3:
            return self._inside_local(name,"basket",[-.12,-.11,.008],[.12,.11,.16]) and self.supports(name,["basket_bottom"])
        p=self.scene.data.xpos[self.scene.objects[name]]
        return abs(p[0]+.60)<.12 and abs(p[1]-.08)<.11 and .69<p[2]<.83 and self.supports(name,["basket_bottom"])
    def in_microwave(self,name):
        if self.version3:
            return (self._inside_local(name,"microwave",[-.20,-.156,.024],[.176,.156,.316]) and
                    self.supports(name,["microwave_bottom"]) and not self.touching_pads(name))
        p=self.scene.data.xpos[self.scene.objects[name]]
        return .46<p[0]<.80 and -.01<p[1]<.29 and .82<p[2]<1.10 and self.supports(name,["microwave_bottom"]) and not self.held(name)
    def meal_ready(self,container):
        if not self.in_microwave(container):return False
        food={"burger_container":"burger","hotdog_container":"hotdog","rice_container":None}.get(container)
        if food is None:return container=="rice_container" and container in self.geom_sets
        if food not in self.scene.objects or self.touching_pads(food):return False
        p=self.scene.data.xpos[self.scene.objects[food]]
        if self.version3:
            if not self._inside_local(food,"microwave",[-.20,-.156,.024],[.176,.156,.316]):return False
        elif not (.46<p[0]<.80 and -.01<p[1]<.29 and .82<p[2]<1.10):return False
        # Burger/hotdog are separate free bodies: require actual support on their own plate.
        plate_geoms=self.geom_sets[container]
        return self.supports(food,[self.scene.model.geom(g).name for g in plate_geoms])

    def event(self,kind,name=None):
        self.events.append({"event":kind,"object":name,"sim_time":float(self.scene.data.time),
            "sim_tick":int(round(self.scene.data.time/self.scene.model.opt.timestep)),"simulated_device_event":True})
    def update(self, dt=None):
        if not self.enabled:return
        dt=float(self.scene.model.opt.timestep) if dt is None else float(dt)
        if self.angle("faucet_switch")>=math.radians(40):
            for name in self.clean_seconds:
                if self.in_basket(name) and not self.held(name):
                    self.clean_seconds[name]+=dt
                    if self.clean_seconds[name]>=WASH_DURATION_S and name not in self.cleaned:
                        self.cleaned.add(name);self.event("wash_complete",name)
        pressed=self.angle("microwave_start_slide")>=.003
        button=self.geom("microwave_start_geom");force=0.
        if pressed:
            for i,c in enumerate(self.scene.data.contact):
                g1,g2=int(c.geom1),int(c.geom2)
                if (g1==button and g2 in self.pads) or (g2==button and g1 in self.pads):
                    f=np.zeros(6);mujoco.mj_contactForce(self.scene.model,self.scene.data,i,f);force+=max(0.,f[0])
        valid=pressed and force>=1
        self.button_seconds=self.button_seconds+dt if valid else 0.
        closed=self.angle("microwave_hinge")<=math.radians(5)
        if self.heating is not None:
            if not closed or not self.meal_ready(self.heating):
                self.event("heat_aborted",self.heating);self.heating=None;self.heat_seconds=0.
            else:
                self.heat_seconds+=dt
                if self.heat_seconds>=HEAT_DURATION_S:
                    self.heated.add(self.heating);self.event("heat_complete",self.heating);self.heating=None
        if self.button_seconds>=.05 and not self.button_latched:
            self.button_latched=True
            meals=[n for n in ("burger_container","hotdog_container","rice_container") if n in self.geom_sets and self.meal_ready(n)]
            if closed and len(meals)==1:
                self.heating=meals[0];self.heat_seconds=0.;self.event("heat_started",self.heating)
            else:self.event("heat_start_rejected")
        if not pressed:self.button_latched=False
    def snapshot(self):
        return {"water_on":self.enabled and self.angle("faucet_switch")>=math.radians(40),
            "clean_seconds":dict(self.clean_seconds),"cleaned":sorted(self.cleaned),
            "heating":self.heating,"heat_seconds":self.heat_seconds,"heated":sorted(self.heated),
            "button_seconds":self.button_seconds,"button_latched":self.button_latched,
            "simulated_device_events":True}

    def restore(self,snapshot):
        required={"clean_seconds","cleaned","heating","heat_seconds","heated","button_seconds","button_latched"}
        if not required.issubset(snapshot):raise ValueError("Incomplete device snapshot")
        known=set(self.clean_seconds)
        if set(snapshot["clean_seconds"])!=known or not set(snapshot["cleaned"]).issubset(known) or not set(snapshot["heated"]).issubset(self.scene.objects):
            raise ValueError("Device snapshot/model mismatch")
        self.clean_seconds={k:float(v) for k,v in snapshot["clean_seconds"].items()}
        if any(not math.isfinite(v) or v<0 for v in self.clean_seconds.values()):raise ValueError("Invalid clean timer")
        self.cleaned=set(snapshot["cleaned"]);self.heated=set(snapshot["heated"])
        self.heating=snapshot["heating"];self.heat_seconds=float(snapshot["heat_seconds"])
        self.button_seconds=float(snapshot["button_seconds"]);self.button_latched=bool(snapshot["button_latched"])
        if not all(math.isfinite(v) and v>=0 for v in (self.heat_seconds,self.button_seconds)):
            raise ValueError("Invalid device timer")
