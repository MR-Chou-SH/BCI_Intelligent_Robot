"""Common measured-contact guard for all continuous controller entry points."""
import numpy as np
import mujoco
from .contact import object_geom_ids

class ContactGuard:
    """Conservative software stop, not a physical robot safety certification.

    Pads may manipulate free kitchen objects up to 40 N measured normal force.
    Other moving robot contacts stop above 5 N. Fixed base support is exempt.
    Device interaction must explicitly whitelist its named contact geometry.
    """
    def __init__(self,scene):
        self.scene=scene
        from .mechanics import coupling_manifest
        self.mechanical_constraints=coupling_manifest(scene)
        m=scene.model
        self.robot=set();self.bases=set()
        self.pads={g for a in scene.arms.values() for g in a.pad_geoms}
        self.manipulable=set()
        for name in scene.objects:self.manipulable.update(object_geom_ids(scene,name))
        self.device_contacts=set()
        self.jp=np.zeros((3,m.nv));self.jr=np.zeros((3,m.nv))
        for g in range(m.ngeom):
            name=m.body(int(m.geom_bodyid[g])).name
            if name.startswith(("left_","right_")):
                self.robot.add(g)
                if name.endswith("fr3_link0"):self.bases.add(g)

    def allow_device_contact(self,geom_name):
        g=mujoco.mj_name2id(self.scene.model,mujoco.mjtObj.mjOBJ_GEOM,geom_name)
        if g<0:raise ValueError("Unknown interaction geometry: "+geom_name)
        if g in self.robot:raise ValueError("Robot geometry cannot be an interaction target")
        self.device_contacts.add(g)

    def inspect(self):
        m,d=self.scene.model,self.scene.data
        result=[]
        from .mechanics import coupling_sample
        for state in coupling_sample(self.scene,self.mechanical_constraints):
            if not state['active'] or abs(state['residual_m'])>.0005:
                result.append({"kind":"jaw_transmission","constraint":state})
        for spec in self.mechanical_constraints:
            for joint in spec['joint_ids']:
                q=float(d.qpos[m.jnt_qposadr[joint]])
                if not np.isfinite(q) or q<m.jnt_range[joint,0]-.0005 or q>m.jnt_range[joint,1]+.0005:
                    result.append({"kind":"finger_joint_limit","joint":m.joint(joint).name,"q_m":q})
        for i in range(d.ncon):
            c=d.contact[i];g1,g2=int(c.geom1),int(c.geom2)
            if not ({g1,g2}&self.robot):continue
            # Exempt only base/environment support, never base vs moving robot.
            if ((g1 in self.bases and g2 not in self.robot) or
                (g2 in self.bases and g1 not in self.robot)):continue
            intentional=((g1 in self.pads and g2 in self.manipulable|self.device_contacts) or
                         (g2 in self.pads and g1 in self.manipulable|self.device_contacts))
            limit=40. if intentional else 5.
            force=np.zeros(6);mujoco.mj_contactForce(m,d,i,force)
            if not np.isfinite(force).all() or force[0]>limit:
                result.append({"geom1":m.geom(g1).name,"geom2":m.geom(g2).name,
                    "normal_force_n":float(force[0]),"limit_n":limit,"distance_m":float(c.dist)})
        for side,arm in self.scene.arms.items():
            rotation=d.xmat[arm.tcp_body].reshape(3,3)
            point=d.xpos[arm.tcp_body]+rotation@np.asarray(arm.tcp_offset)
            mujoco.mj_jac(m,d,self.jp,self.jr,point,arm.tcp_body)
            speed=float(np.linalg.norm(self.jp@d.qvel))
            if not np.isfinite(speed) or speed>.15:
                result.append({"kind":"tcp_speed","arm":side,"speed_m_s":speed,"limit_m_s":.15})
        return result
