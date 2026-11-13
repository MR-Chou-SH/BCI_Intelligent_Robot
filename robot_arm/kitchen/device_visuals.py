"""Device-state visuals added to MjvScene, never to the physics model.

Call after each renderer.update_scene: that call clears the previous frame's
decorations. Heating must come from DeviceState at the same observation tick.
"""
import math

import mujoco
import numpy as np

DEVICE_VISUALS_VERSION = "kitchen_device_visuals_v1"


def _geom(scene, kind, size, position, color, rotation=None, emission=0.):
    if scene.ngeom >= scene.maxgeom:
        raise RuntimeError("No render geometry capacity for kitchen device visuals")
    geom = scene.geoms[scene.ngeom]
    mujoco.mjv_initGeom(geom, kind, np.asarray(size, dtype=float),
                      np.asarray(position, dtype=float),
                      np.eye(3).ravel() if rotation is None else np.asarray(rotation).ravel(),
                      np.asarray(color, dtype=np.float32))
    geom.category = mujoco.mjtCatBit.mjCAT_DECOR
    geom.objtype = mujoco.mjtObj.mjOBJ_UNKNOWN
    geom.objid = -1
    geom.emission = emission
    geom.specular = .6
    geom.shininess = .8
    scene.ngeom += 1
    return geom


def add_device_visuals(scene, model, data, device_state=None):
    """Draw faucet flow and an amber microwave lamp from measured/current state.

    Water uses the identical 40-degree joint threshold as DeviceState. No water
    particles, forces, clock, or inferred button-to-heating transition are used.
    A missing device snapshot means heating is unknown: the lamp remains dark.
    """
    switch = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "faucet_switch")
    spout = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "faucet_spout")
    floor = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "sink_floor")
    if min(switch, spout, floor) >= 0 and data.qpos[model.jnt_qposadr[switch]] >= math.radians(40):
        axis = data.geom_xmat[spout].reshape(3, 3)[:, 2]
        ends = data.geom_xpos[spout] + np.array([-1., 1.])[:, None] * model.geom_size[spout, 1] * axis
        base = data.xpos[model.geom_bodyid[spout]]
        # A capsule's local Z is along the spout; choose its free end, not stem.
        start = ends[np.argmax(np.linalg.norm(ends[:, :2] - base[:2], axis=1))].copy()
        start[2] -= model.geom_size[spout, 0] * .65
        end = start.copy()
        end[2] = data.geom_xpos[floor, 2] + model.geom_size[floor, 2] + .004
        if start[2] > end[2]:
            for radius, color in ((.008, [.40, .76, .96, .38]),
                                  (.0035, [.78, .94, 1., .72])):
                geom = _geom(scene, mujoco.mjtGeom.mjGEOM_CYLINDER, [radius, 1., 0.],
                             start, color, emission=.15)
                mujoco.mjv_connector(geom, mujoco.mjtGeom.mjGEOM_CYLINDER, radius, start, end)

    micro = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "microwave")
    button = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "microwave_button")
    if min(micro, button) >= 0:
        rotation = data.xmat[micro].reshape(3, 3)
        # Upper front panel, inward from the button so the overview can see it.
        # Use cabinet coordinates: button travel never moves the indicator.
        local = np.array([model.body_pos[button, 0] - .014,
                          model.body_pos[button, 1] * .25, .30])
        position = data.xpos[micro] + rotation @ local
        _geom(scene, mujoco.mjtGeom.mjGEOM_BOX, [.006, .023, .023],
              position, [.055, .06, .065, 1.], rotation)
        position = position + rotation @ [-.008, 0., 0.]
        heating = device_state is not None and device_state.get("heating") is not None
        _geom(scene, mujoco.mjtGeom.mjGEOM_SPHERE, [.017, 0., 0.], position,
              [1., .30, .025, 1.] if heating else [.12, .075, .035, 1.],
              emission=1. if heating else 0.)
