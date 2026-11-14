"""Native MuJoCo device preview; direct fixtures, no recording/expert actions."""
import argparse
import json
import math
from pathlib import Path
import queue
import time
import mujoco
import mujoco.viewer
import numpy as np
from robot_arm.kitchen.model import build_scene
from robot_arm.kitchen.devices import DeviceState, HEAT_DURATION_S
from robot_arm.kitchen.device_visuals import add_device_visuals


def prepare():
    scene = build_scene(layout_id="kitchen_v3_norice_candidate_013")
    m, d = scene.model, scene.data
    oven = m.body("microwave").id
    origin, rotation = d.xpos[oven].copy(), d.xmat[oven].reshape(3, 3).copy()
    basket = d.xpos[m.body("basket").id].copy()
    for name, position, quat in (
        ("apple", basket + [0., 0., .044], [1., 0., 0., 0.]),
        ("burger_container", origin + rotation @ [-.02, 0., .032], d.xquat[oven].copy()),
        ("burger", origin + rotation @ [-.02, 0., .069], d.xquat[oven].copy())):
        address = m.jnt_qposadr[m.joint(name + "_free").id]
        d.qpos[address:address + 7] = [*position, *quat]
    mujoco.mj_forward(m, d)
    mujoco.mj_step(m, d, nstep=200)
    return scene, DeviceState(scene)


def set_view(camera, scene, name):
    if name == "overview":
        position, target = np.array([0., -2.5, 2.2]), np.array([0., -.05, .9])
    elif name == "faucet":
        target = scene.data.xpos[scene.model.body("basket").id] + [0., 0., .18]
        position = target + [-.48, -.55, .40]
    else:
        oven = scene.model.body("microwave").id
        rotation = scene.data.xmat[oven].reshape(3, 3)
        origin = scene.data.xpos[oven]
        target = origin + rotation @ [-.20, 0., .22]
        position = origin + rotation @ [-.58, -.17, .46]
    offset = position - target
    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    camera.lookat[:] = target
    camera.distance = np.linalg.norm(offset)
    camera.azimuth = math.degrees(math.atan2(offset[1], offset[0])) + 180.
    camera.elevation = -math.degrees(math.asin(offset[2] / camera.distance))


def run(status_file=None, auto=False):
    scene, devices = prepare()
    m, d = scene.model, scene.data
    keys = queue.SimpleQueue()
    switch = m.joint("faucet_switch").id
    epoch, last_phase, next_status = time.monotonic(), None, 0.

    def water(enabled):
        d.qpos[m.jnt_qposadr[switch]] = math.radians(60) if enabled else 0.
        d.qvel[m.jnt_dofadr[switch]] = 0.
        mujoco.mj_forward(m, d)

    def heat():
        if not devices.heating and devices.meal_ready("burger_container") and devices.angle("microwave_hinge") <= math.radians(5):
            # Preview shortcut only; the real DeviceState owns completion/abort.
            devices.heated.discard("burger_container")
            devices.heating, devices.heat_seconds = "burger_container", 0.
            devices.event("preview_heat_started", "burger_container")

    water(True)
    with mujoco.viewer.launch_passive(m, d, key_callback=keys.put,
            show_left_ui=False, show_right_ui=False) as viewer:
        with viewer.lock():
            set_view(viewer.cam, scene, "faucet")
        print(json.dumps({"event": "DEVICE_PREVIEW_READY", "meal_ready": devices.meal_ready("burger_container")}), flush=True)
        while viewer.is_running():
            started = time.monotonic()
            with viewer.lock():
                while not keys.empty():
                    key = keys.get()
                    if key in (49, 50, 51):
                        set_view(viewer.cam, scene, {49: "overview", 50: "faucet", 51: "microwave"}[key])
                    elif key == 297:  # F8; avoid MuJoCo's W wireframe shortcut.
                        auto = False
                        water(not devices.snapshot()["water_on"])
                    elif key == 298:  # F9
                        auto = False
                        set_view(viewer.cam, scene, "microwave")
                        heat()
                    elif key == 299:  # F10
                        auto, epoch, last_phase = not auto, started, None
                if auto:
                    elapsed = (started - epoch) % 12.
                    phase = 0 if elapsed < 1. else 1 if elapsed < 5. else 2 if elapsed < 6. else 3 if elapsed < 9. else 4
                    if phase != last_phase:
                        last_phase = phase
                        if phase in (0, 1, 2):
                            set_view(viewer.cam, scene, "faucet")
                            water(phase == 1)
                        elif phase == 3:
                            set_view(viewer.cam, scene, "microwave")
                            heat()
                mujoco.mj_step(m, d, nstep=20)
                devices.update(20 * float(m.opt.timestep))
                viewer.user_scn.ngeom = 0
                add_device_visuals(viewer.user_scn, m, d, devices.snapshot())
                viewer.user_scn.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = False
                viewer.user_scn.flags[mujoco.mjtRndFlag.mjRND_REFLECTION] = False
                water_text = "ON" if devices.snapshot()["water_on"] else "OFF"
                heat_text = f"HEATING {max(0., HEAT_DURATION_S - devices.heat_seconds):.2f}s" if devices.heating else "OFF"
            # set_texts and sync acquire the viewer mutex internally.
            viewer.set_texts([(mujoco.mjtFontScale.mjFONTSCALE_150, mujoco.mjtGridPos.mjGRID_TOPLEFT,
                "DEVICE PREVIEW (not recorded)\n1 overview | 2 faucet | 3 microwave\nF8 water | F9 heat 2s | F10 auto demo",
                f"Water: {water_text}\nMicrowave: {heat_text}\nAuto: {auto}")])
            viewer.sync()
            if status_file and started >= next_status:
                status_file.parent.mkdir(parents=True, exist_ok=True)
                status_file.write_text(json.dumps({"ready": True, "auto": auto,
                    "devices": devices.snapshot(), "sim_time_s": float(d.time),
                    "scope": "unrecorded_fixture_preview"}), encoding="utf-8")
                next_status = started + .5
            time.sleep(max(0., .04 - (time.monotonic() - started)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--auto", action="store_true")
    args = parser.parse_args()
    run(args.status_file, args.auto)


if __name__ == "__main__":
    main()
