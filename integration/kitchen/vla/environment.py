"""Single-owner policy execution on existing bounded FR3/UMI control.

This environment owns a private KitchenPlatform, never the public server or real
hardware. Policy output goes through the existing controller's rate/position
limits, not IK or the expert's phase scheduler. No object state changes in step.
"""
from pathlib import Path
import numpy as np
from integration.kitchen.data.lerobot_export import unpack16, policy_episode
from integration.kitchen.data.retiming_experiment import restored_platform
from integration.kitchen.data.replay import read_compact
from integration.kitchen.validation.quality import ExpertQuality
from integration.kitchen.validation.success import validate_initial, validate_skill, validate_final
from robot_arm.kitchen.action_codec import decode
from .observation import robot_state
from .images import CameraRig
from .protocol import rgb_encode
from .action_limits import ActionLimits


class PolicyEnvironment:
    def __init__(self, trajectory, *, render=True, guard=True, camera_rig=None, initial_seed=None):
        import json
        trajectory = Path(trajectory)
        ep = policy_episode(trajectory)
        data = read_compact(trajectory)
        report = json.loads(trajectory.with_name('report.json').read_text())
        self.case = data['task']
        if self.case['task'] not in ('L1', 'R5'):
            raise ValueError('First policy pilot supports L1/R5; other task evaluators are not yet adapted')
        if initial_seed is not None and (self.case['task'] != 'L1'
                or report['initialization_profile'] != 'research'
                or type(initial_seed) is not int or not 0 <= initial_seed < 2**32):
            raise ValueError('Fresh initial seed currently requires L1, research profile and uint32 seed')
        self.platform = restored_platform(data, None, report['initialization_profile'], report['seed'])
        self.camera = None
        try:
            if initial_seed is not None:
                # L1 preparation is exactly the existing one-second initial settle;
                # no expert actions or future recorded states enter this evaluation.
                from integration.kitchen.runtime.client import LocalPlannerClient
                client = LocalPlannerClient(self.platform)
                for method, args in (('reset', {'seed': initial_seed}),
                                     ('step', {'n_substeps': 500, 'detail': 'execution'})):
                    reply = client.call(method, args, mutating=True)
                    if not reply['ok']:
                        raise RuntimeError(f'Policy initial preparation failed: {reply}')
            self.initial_seed = initial_seed
            self.action_limits = ActionLimits(self.platform.scene)
            validate_initial(self.platform.controller, self.case)
            self.quality = ExpertQuality(self.platform)
            self.quality.start(self.case['task'])
            self.prompt = ep['annotation']['coarse_instruction']
            self.camera = CameraRig(self.platform.scene, camera_rig) if render else None
            self.frame, self.rate_limited_frames = 0, 0
            self.start_tick = self.platform.controller.tick
            self.guard, self.guard_violations = guard, []
            self.last_success_error = None
        except BaseException:
            self.close()
            raise

    def observation(self):
        scene = self.platform.scene
        result = {'frame': self.frame, 'state': robot_state(scene).tolist(),
                  'prompt': self.prompt}
        if self.camera:
            result.update(images={key: rgb_encode(rgb) for key, rgb in self.camera.render_all().items()},
                          cameras=self.camera.contract)
        return result

    def step(self, action):
        controller = self.platform.controller
        if controller.stopped or controller.frame_step or self.platform.queue:
            raise RuntimeError('Policy environment not at an idle control boundary')
        joints, opening = unpack16(action)
        target = decode(joints, np.clip(opening, 0., 1.))
        accepted = controller.accept_joint_targets(target)
        self.action_limits.command(controller, action, target, accepted)
        if not np.allclose(target, accepted, atol=1e-8, rtol=0):
            self.rate_limited_frames += 1
        self.platform.advance(20, detail='execution')
        self.action_limits.measured()
        self.frame += 1
        if self.guard:
            violations = controller.contact_guard.inspect()
            if violations:
                self.guard_violations.extend(violations)
                controller.hold('VLA_MEASURED_GUARD')
        return self.success()

    def success(self):
        if self.guard_violations or self.platform.controller.stopped:
            return False
        try:
            validate_skill(self.quality, self.case['task'], self.case)
            validate_final(self.quality, self.case)
        except (ValueError, RuntimeError) as error:
            self.last_success_error = str(error)
            return False
        self.last_success_error = None
        return True

    def close(self):
        if self.camera:
            self.camera.close()
        self.platform.close()
