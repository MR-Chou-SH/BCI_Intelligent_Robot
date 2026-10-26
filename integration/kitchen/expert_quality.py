"""Task-specific control-frame measured predicates, separate from planner completion."""
import math
import numpy as np
from robot_arm.kitchen.registry import object_bounds


class ExpertQuality:
    def __init__(self, platform):
        self.platform=platform
        self.phase="preparation"
        self.samples=0
        self.current={}
        self.maximum={}
        self.failures=[]
        self.tray_tilt_peak=0.
        self.last_tick=platform.controller.tick
        platform.post_step_observers.append(self.sample)

    def sample(self, controller):
        stride=controller.substeps
        if controller.tick != self.last_tick+stride:
            raise RuntimeError("Expert quality sample gap")
        self.last_tick=controller.tick
        self.samples+=1
        d,m=controller.scene.data,controller.scene.model
        device=controller.devices
        predicates={}
        for name in controller.scene.objects:
            if name=="wood_tray":continue
            support=device.supports(name,["tray_bottom"])
            velocity=np.empty(6)
            import mujoco
            mujoco.mj_objectVelocity(m,d,mujoco.mjtObj.mjOBJ_BODY,controller.scene.objects[name],velocity,0)
            quiet=np.linalg.norm(velocity[3:])<=.02 and np.linalg.norm(velocity[:3])<=.05
            predicates[name+":tray"]=support and quiet and not device.touching_pads(name)
        angle=device.angle("microwave_hinge")
        j=device.joints["microwave_hinge"]
        speed=abs(float(d.qvel[m.jnt_dofadr[j]]))
        predicates["door:open"]=angle>=math.radians(70) and speed<=.05
        predicates["door:closed"]=angle<=math.radians(5) and speed<=.05
        tray=controller.scene.objects["wood_tray"]
        tilt=math.acos(float(np.clip(d.xmat[tray].reshape(3,3)[2,2],-1,1)))
        if self.phase=="B1":self.tray_tilt_peak=max(self.tray_tilt_peak,tilt)
        v=np.empty(6)
        import mujoco
        mujoco.mj_objectVelocity(m,d,mujoco.mjtObj.mjOBJ_BODY,tray,v,0)
        predicates["tray:delivered"]=(device.supports("wood_tray",["user_table"]) and
            not device.touching_pads("wood_tray") and np.linalg.norm(v[3:])<=.02 and np.linalg.norm(v[:3])<=.05 and tilt<=math.radians(10))
        for key,value in predicates.items():
            identity=self.phase+":"+key
            self.current[identity]=self.current.get(identity,0.)+stride*float(m.opt.timestep) if value else 0.
            self.maximum[identity]=max(self.maximum.get(identity,0.),self.current[identity])
        if controller.stopped and self.phase!="preparation":
            self.failures.append({"tick":controller.tick,"reason":controller.stop_reason})

    def start(self, phase):
        self.phase=phase

    def require(self, key, seconds):
        age=self.current.get(self.phase+":"+key,0.)
        if age+1e-9<seconds:
            raise RuntimeError(f"EXPERT_QUALITY_{key}: {age:.3f}s < {seconds}s")

    def tray_object(self, name):
        self.require(name+":tray",2.)
        # Actual full collision/visual envelope must be within tray's inner rim.
        lo,hi=object_bounds(self.platform.scene,name,frame_body="wood_tray")
        # Existing contact model allows <=1mm compliant support contact.
        if np.any(lo[:2]<[-.233,-.153]) or np.any(hi[:2]>[.233,.153]):
            raise RuntimeError("PAYLOAD_FULL_FOOTPRINT_OUTSIDE_TRAY: "+name)
        if name.endswith("_container"):
            food=name.removesuffix("_container")
            dev=self.platform.controller.devices
            geoms=[self.platform.scene.model.geom(g).name for g in dev.geom_sets[name]]
            if not dev.supports(food,geoms):raise RuntimeError("FOOD_NOT_SUPPORTED_BY_OWN_CARRIER")

    def report(self):
        return {"schema":"kitchen_expert_quality_v1","sample_hz":self.platform.controller.control_hz,"samples":self.samples,
                "last_tick":self.last_tick,"maximum_windows_s":self.maximum,
                "current_windows_s":self.current,"controller_failures":self.failures,
                "tray_tilt_peak_rad":self.tray_tilt_peak,"thermal_and_wash_are_simulated_device_markers":True}
