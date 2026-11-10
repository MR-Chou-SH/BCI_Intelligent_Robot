"""Measured robot-only proprioception shared by replay and policy execution."""
import numpy as np
from integration.kitchen.data.lerobot_export import pack16


def robot_state(scene):
    import mujoco
    from robot_arm.kitchen.gripper import fingertip_gap
    scratch = mujoco.MjData(scene.model)
    scratch.qpos[:] = scene.data.qpos
    mujoco.mj_kinematics(scene.model, scratch)
    indices = [i for side in ('left', 'right') for i in scene.arms[side].qpos]
    widths = [max(0., fingertip_gap(scene.model, scratch, scene.arms[side])['inner_gap_m'])
              for side in ('left', 'right')]
    state = pack16(scene.data.qpos[indices], widths)
    if not np.isfinite(state).all():
        raise ValueError('Nonfinite measured robot state')
    return state
