"""Cached, same-qpos numeric state interface; no Torch/Warp dependency."""
import numpy as np
import mujoco
from .registry import SceneRegistry
from .control import tcp_pose
from .gripper import fingertip_gap

SCHEMA = 'kitchen_privileged_state_v1'


class StateTensor:
    def __init__(self, scene):
        self.model = scene.model
        self.scratch = mujoco.MjData(self.model)
        entries = SceneRegistry(scene).entries
        self.ids = sorted(name for name, item in entries.items() if item['movable'])
        self.bodies = []
        self.joints = []
        for name in self.ids:
            item = entries[name]
            joint = self.model.joint(item['joint_name']).id if item.get('joint_name') else None
            self.joints.append(joint)
            self.bodies.append(int(self.model.jnt_bodyid[joint]) if joint is not None else item['body_id'])
        self.fruits = ('apple', 'pear', 'banana', 'tomato')

    def schema(self):
        return {'schema': SCHEMA, 'object_ids': self.ids,
                'quaternion_convention':'native_body_xquat_wxyz_v1',
                'object_columns': ['x_m','y_m','z_m','qw','qx','qy','qz',
                    'vx_m_s','vy_m_s','vz_m_s','wx_rad_s','wy_rad_s','wz_rad_s'],
                'object_frames': [self.model.body(b).name for b in self.bodies],
                'joint_columns': ['position_rad_or_m', 'velocity_rad_s_or_m_s'],
                'robot_columns': [*[f'q{i}_rad' for i in range(7)],
                    *[f'dq{i}_rad_s' for i in range(7)], 'gripper_width_m',
                    'tcp_x_m','tcp_y_m','tcp_z_m','tcp_qw','tcp_qx','tcp_qy','tcp_qz'],
                'arm_order': ['left', 'right'], 'fruit_order': list(self.fruits),
                'controller_target_semantics': 'left7 rad/tendon_m/right7 rad/tendon_m',
                'scope': 'Privileged simulator state, not VLA input or a complete reset snapshot'}

    def query(self, scene, controller):
        if scene.model is not self.model:
            raise ValueError('StateTensor belongs to another model/reset; create a new reader')
        m, d = self.model, self.scratch
        mujoco.mj_copyData(d, m, scene.data)
        mujoco.mj_kinematics(m, d)
        mujoco.mj_comPos(m, d)
        mujoco.mj_comVel(m, d)
        objects = np.empty((len(self.ids), 13))
        joints = np.zeros((len(self.ids), 2))
        joint_mask = np.zeros(len(self.ids), dtype=bool)
        flags = np.zeros((len(self.ids), 2), dtype=bool)
        velocity = np.empty(6)
        for i, (name, body, joint) in enumerate(zip(self.ids, self.bodies, self.joints)):
            mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_BODY, body, velocity, 0)
            objects[i] = np.r_[d.xpos[body], d.xquat[body], velocity[3:], velocity[:3]]
            flags[i] = name in controller.devices.cleaned, name in controller.devices.heated
            if joint is not None:
                joints[i] = d.qpos[m.jnt_qposadr[joint]], d.qvel[m.jnt_dofadr[joint]]
                joint_mask[i] = True
        robots = []
        for side in ('left', 'right'):
            arm = scene.arms[side]
            point, _ = tcp_pose(d, arm)
            # Keep the native sign used by Warp and the object pose channels.
            # A matrix round-trip can return -q, a different network input.
            quaternion = d.xquat[arm.tcp_body].copy()
            width = max(0., fingertip_gap(m, d, arm)['inner_gap_m'])
            robots.append(np.r_[d.qpos[list(arm.qpos)], d.qvel[list(arm.dofs)], width, point, quaternion])
        result = {'tick': int(controller.tick), 'qpos': d.qpos.copy(), 'qvel': d.qvel.copy(),
                  'objects': objects, 'object_joint': joints, 'object_joint_mask': joint_mask,
                  'object_flags': flags, 'robot': np.asarray(robots),
                  'reference': controller.reference.copy(), 'previous': controller.previous.copy(),
                  'accepted': controller.accepted.copy(),
                  'clean_seconds': np.array([controller.devices.clean_seconds.get(n,0.) for n in self.fruits]),
                  'heat_seconds': float(controller.devices.heat_seconds),
                  'heating_object_index': self.ids.index(controller.devices.heating) if controller.devices.heating in self.ids else -1,
                  'button_latched': bool(controller.devices.button_latched)}
        if any(not np.isfinite(v).all() for v in result.values() if isinstance(v,np.ndarray)):
            raise ValueError('Nonfinite privileged state')
        return result

    def json(self, scene, controller):
        return {**self.schema(), 'state': {k:v.tolist() if isinstance(v,np.ndarray) else v
                                          for k,v in self.query(scene,controller).items()}}
