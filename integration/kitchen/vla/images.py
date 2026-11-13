"""Shared camera contract for offline training frames and online inference."""
import numpy as np
import copy
import json
import math
from pathlib import Path

DEFAULT_CAMERA = {'camera': 'v3_oblique', 'width': 512, 'height': 512,
                  'zoom': 1., 'lookat': None}
DEFAULT_RIG = Path(__file__).with_name('cameras_left.json')


def camera_rig(value=None):
    """Canonical camera order, with legacy single-camera contract support."""
    if value is None:
        value = json.loads(DEFAULT_RIG.read_text())
    elif isinstance(value, (str, Path)):
        value = json.loads(Path(value).read_text())
    value = copy.deepcopy(value)
    if 'schema' not in value:
        value = {'schema': 'kitchen_vla_camera_rig_v1', 'views': [
            {'key': 'observation.images.camera1', 'role': 'global', 'enabled': True, 'camera': value}]}
    if value.get('schema') != 'kitchen_vla_camera_rig_v1':
        raise ValueError('Unknown camera rig schema')
    roles = {'observation.images.camera1': 'global', 'observation.images.camera2': 'left_wrist',
             'observation.images.camera3': 'right_wrist'}
    seen = set()
    for view in value['views']:
        key, c = view['key'], view['camera']
        if key in seen or key not in roles or view['role'] != roles[key] or type(view['enabled']) is not bool:
            raise ValueError('Invalid or duplicate camera slot')
        seen.add(key)
        if any(type(c[n]) is not int or not 1 <= c[n] <= 2048 for n in ('width', 'height')):
            raise ValueError('Invalid camera image size')
        if 'position_world_m' in c:
            if (np.asarray(c['position_world_m']).shape != (3,) or np.asarray(c['lookat']).shape != (3,)
                    or not np.isfinite([c['position_world_m'], c['lookat']]).all()
                    or np.linalg.norm(np.asarray(c['position_world_m'])-c['lookat']) < .001
                    or not 1 <= c['fovy_deg'] < 179):
                raise ValueError('Invalid explicit camera pose')
    value['views'].sort(key=lambda v: v['key'])
    if not value['views'] or value['views'][0]['role'] != 'global' or not value['views'][0]['enabled']:
        raise ValueError('Global camera must be enabled')
    return value


def active_views(rig):
    return [view for view in rig['views'] if view['enabled']]


def device_visual_contract():
    """Identify derived RGB appearance separately from the unchanged physics."""
    import hashlib
    source = Path(__file__).parents[3]/'robot_arm/kitchen/device_visuals.py'
    return {'schema': 'kitchen_device_visuals_v1',
            'implementation_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}


class Camera:
    def __init__(self, scene, config=None, *, devices=None):
        import mujoco
        from integration.kitchen.data.replay import _review_camera
        self.scene, self.config = scene, dict(config or DEFAULT_CAMERA)
        self.devices = devices
        c = self.config
        if 'position_world_m' in c:
            self.view = mujoco.MjvCamera()
            mujoco.mjv_defaultCamera(self.view)
            self.view.lookat[:] = c['lookat']
            offset = np.asarray(c['position_world_m'])-np.asarray(c['lookat'])
            self.view.distance = float(np.linalg.norm(offset))
            self.view.azimuth = math.degrees(math.atan2(offset[1], offset[0]))+180.
            self.view.elevation = -math.degrees(math.asin(float(offset[2]/self.view.distance)))
        else:
            self.view, _ = _review_camera(scene.model, c['camera'], zoom=c['zoom'], lookat=c['lookat'])
        scene.model.vis.quality.offsamples = 0
        self.renderer = mujoco.Renderer(scene.model, height=c['height'], width=c['width'])
        self.option = mujoco.MjvOption()
        self.scratch = mujoco.MjData(scene.model)

    def render(self, qpos=None, *, device_state=None):
        import mujoco
        from robot_arm.kitchen.device_visuals import add_device_visuals
        # Same-qpos kinematics without altering the live physics/warmstart.
        self.scratch.qpos[:] = self.scene.data.qpos if qpos is None else qpos
        mujoco.mj_kinematics(self.scene.model, self.scratch)
        mujoco.mj_camlight(self.scene.model, self.scratch)
        original_fovy = float(self.scene.model.vis.global_.fovy)
        try:
            if 'fovy_deg' in self.config:
                self.scene.model.vis.global_.fovy = self.config['fovy_deg']
            self.renderer.update_scene(self.scratch, camera=self.view, scene_option=self.option)
        finally:
            self.scene.model.vis.global_.fovy = original_fovy
        if device_state is None and self.devices is not None:
            device_state = self.devices.snapshot()
        add_device_visuals(self.renderer.scene, self.scene.model, self.scratch, device_state)
        for flag in (mujoco.mjtRndFlag.mjRND_SHADOW, mujoco.mjtRndFlag.mjRND_REFLECTION):
            self.renderer.scene.flags[flag] = False
        return np.asarray(self.renderer.render(), dtype=np.uint8).copy()

    def close(self):
        self.renderer.close()


class CameraRig:
    """Render only enabled views at the same qpos; disabled views allocate nothing."""
    def __init__(self, scene, config=None, *, devices=None):
        self.contract = camera_rig(config)
        self.devices = devices
        self.cameras = {}
        try:
            for view in active_views(self.contract):
                self.cameras[view['key']] = Camera(scene, view['camera'], devices=devices)
        except BaseException:
            self.close()
            raise
        self.global_camera = self.cameras['observation.images.camera1']
        self.config = self.global_camera.config

    def render_all(self, qpos=None, *, device_state=None):
        if device_state is None and self.devices is not None:
            device_state = self.devices.snapshot()
        return {key: camera.render(qpos, device_state=device_state)
                for key, camera in self.cameras.items()}

    def render(self, qpos=None, *, device_state=None):
        return self.global_camera.render(qpos, device_state=device_state)

    def close(self):
        for camera in self.cameras.values():
            camera.close()
