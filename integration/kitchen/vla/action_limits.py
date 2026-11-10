"""Summary-only diagnostics around the unchanged bounded joint controller."""
import numpy as np
from robot_arm.kitchen.limits import joint_control_limits


class ActionLimits:
    # Ignore sub-microradian float32 export/round-trip changes in event counts.
    command_epsilon_rad = 1e-5
    def __init__(self, scene):
        self.scene, self.frames = scene, 0
        self.bounds = {side: joint_control_limits(scene.model, arm) for side, arm in scene.arms.items()}
        self.rows = {side: {**dict.fromkeys(('joint_position_clipped_frames', 'joint_rate_limited_frames',
            'tcp_rate_limited_frames', 'gripper_range_clipped_frames', 'gripper_rate_limited_frames',
            'joint_ctrl_saturated_frames', 'joint_force_saturated_frames', 'gripper_force_saturated_frames',
            'measured_joint_outside_command_bounds_frames'), 0),
            'raw_target_step_peak_rad': 0., 'target_correction_peak_rad': 0.,
            'measured_joint_margin_min_rad': None} for side in self.bounds}

    def command(self, controller, raw_action, target, accepted):
        self.frames += 1
        for offset, side in ((0, 'left'), (8, 'right')):
            row = self.rows[side]
            q, previous = target[offset:offset+7], controller.previous[offset:offset+7]
            lo, hi = self.bounds[side]
            bounded = np.clip(q, lo, hi)
            delta = bounded-previous
            peak = float(np.abs(delta).max())
            rate_cap = controller.velocity_limit/controller.control_hz
            limited = previous+delta*min(1., rate_cap/peak) if peak else previous
            row['joint_position_clipped_frames'] += int(np.any(np.abs(q-bounded)>self.command_epsilon_rad))
            row['joint_rate_limited_frames'] += int(peak>rate_cap+self.command_epsilon_rad)
            row['tcp_rate_limited_frames'] += int(np.any(np.abs(limited-accepted[offset:offset+7])>self.command_epsilon_rad))
            row['gripper_range_clipped_frames'] += int(not 0 <= raw_action[offset+7] <= 1)
            row['gripper_rate_limited_frames'] += int(abs(target[offset+7]-controller.previous[offset+7])>.002+1e-8)
            row['raw_target_step_peak_rad'] = max(row['raw_target_step_peak_rad'], float(np.abs(q-previous).max()))
            row['target_correction_peak_rad'] = max(row['target_correction_peak_rad'], float(np.abs(q-accepted[offset:offset+7]).max()))

    def measured(self):
        m, d = self.scene.model, self.scene.data
        for side, arm in self.scene.arms.items():
            row = self.rows[side]
            q = d.qpos[list(arm.qpos)]
            lo, hi = self.bounds[side]
            margin = float(np.minimum(q-lo, hi-q).min())
            previous = row['measured_joint_margin_min_rad']
            row['measured_joint_margin_min_rad'] = margin if previous is None else min(previous, margin)
            row['measured_joint_outside_command_bounds_frames'] += int(margin < -1e-4)
            ids = list(arm.actuators)
            ctrl = d.ctrl[ids]; bounds = m.actuator_ctrlrange[ids]
            row['joint_ctrl_saturated_frames'] += int(np.any((ctrl<=bounds[:,0]+1e-8)|(ctrl>=bounds[:,1]-1e-8)))
            forces, limits = d.actuator_force[ids], m.actuator_forcerange[ids]
            saturated = (forces<=limits[:,0]+1e-6)|(forces>=limits[:,1]-1e-6)
            row['joint_force_saturated_frames'] += int(np.any(saturated & m.actuator_forcelimited[ids].astype(bool)))
            g = arm.gripper_actuator
            row['gripper_force_saturated_frames'] += int(bool(m.actuator_forcelimited[g]) and
                (d.actuator_force[g]<=m.actuator_forcerange[g,0]+1e-6 or d.actuator_force[g]>=m.actuator_forcerange[g,1]-1e-6))

    def report(self):
        return {'frames': self.frames, 'arms': self.rows, 'material_correction_threshold_rad': self.command_epsilon_rad,
                'scope': 'Counts are25Hz samples, not500Hz torque-peak certification. '
                         'Position/rate limits are existing command limits; force limits are actual model actuator limits.'}
