"""Inspect/verify image-free trajectories; optional MuJoCo viewer or MP4 review.

Playback restores measured states in a detached model. verify independently
re-simulates applied controls; neither sends actions to a live server/hardware.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path


def read_compact(path):
    """Read compressed numeric arrays once; reject partial or malformed files."""
    import h5py
    import numpy as np
    try:
        with h5py.File(path,"r") as file:
            if file.attrs.get("schema")!="kitchen_state_actions_v2" or not file.attrs.get("sealed") or file.attrs.get("sampling_failed"):
                raise ValueError("Unsealed or unsupported state/action file")
            metadata=json.loads(file.attrs["metadata_json"])
            result={"metadata":metadata,"start_tick":int(file.attrs["start_tick"]),
                    "end_tick":int(file.attrs["end_tick"]),"state_spec":int(file.attrs["state_spec"])}
            for name in ("initial/integration_state","initial/ctrl","actions/ctrl","states/tick",
                         "states/qpos","states/qvel","states/object_pose","states/task_flags"):
                result[name]=file[name][:]
    except (OSError,KeyError) as error:
        raise ValueError("Damaged or incomplete state/action file") from error
    tick=result["states/tick"];actions=result["actions/ctrl"];start=result["start_tick"];end=result["end_tick"]
    expected=np.arange(start,end+1,20,dtype="i8")
    if len(expected) and expected[-1]!=end:expected=np.append(expected,end)
    if (start<0 or end<start or metadata.get("images_included") is not False or
            metadata.get("physics_hz")!=500 or metadata.get("state_hz")!=25 or
            metadata["identity"]["episode_id"]!=metadata["episode"]["episode_id"] or
            actions.shape!=(end-start,16) or not np.array_equal(tick,expected)):
        raise ValueError("State/action clock, shape or episode mismatch")
    count=len(tick);objects=len(metadata["object_ids"])
    if (result["states/object_pose"].shape!=(count,objects,7) or
            result["states/task_flags"].shape!=(count,objects,2)):
        raise ValueError("Object state shape mismatch")
    for name in ("states/qpos","states/qvel"):
        if result[name].ndim!=2 or result[name].shape[0]!=count:raise ValueError("Robot state shape mismatch")
    if any(not np.isfinite(value).all() for value in result.values() if isinstance(value,np.ndarray)):
        raise ValueError("Nonfinite state/action data")
    return result


def compact_report(path, data):
    with Path(path).open("rb") as stream:signature=hashlib.file_digest(stream,"sha256").hexdigest()
    return {"status":"SEALED_STATE_TRAJECTORY_PASS","schema":"kitchen_state_actions_v2",
            "path":str(path),"sha256":signature,"steps":len(data["actions/ctrl"]),
            "checkpoints":len(data["states/tick"])-1,"start_tick":data["start_tick"],
            "end_tick":data["end_tick"],"duration_s":len(data["actions/ctrl"])/500,
            "episode_id":data["metadata"]["episode"]["episode_id"],"images_included":False}


def verify(path, tolerance=1e-8):
    import mujoco
    import numpy as np
    data=read_compact(path);report=compact_report(path,data)
    scene=scene_for(data["metadata"])
    restore(scene,{"integration_state":data["initial/integration_state"],"state_spec":data["state_spec"]})
    if data["states/qpos"].shape[1]!=scene.model.nq or data["states/qvel"].shape[1]!=scene.model.nv:
        raise ValueError("Recorded state dimensions differ from model")
    indices=[index for side in ("left","right") for index in
             (*scene.arms[side].actuators,scene.arms[side].gripper_actuator)]
    position=velocity=0.;checkpoint=0
    def compare(index):
        return (float(np.max(np.abs(scene.data.qpos-data["states/qpos"][index]))),
                float(np.max(np.abs(scene.data.qvel-data["states/qvel"][index]))))
    position,velocity=compare(0)
    for tick,ctrl in enumerate(data["actions/ctrl"],data["start_tick"]+1):
        scene.data.ctrl[indices]=ctrl
        mujoco.mj_step(scene.model,scene.data)
        if checkpoint+1<len(data["states/tick"]) and tick==data["states/tick"][checkpoint+1]:
            checkpoint+=1;p,v=compare(checkpoint)
            position=max(position,p);velocity=max(velocity,v)
    return {**report,"status":"CONTROL_RESIMULATION_PASS" if max(position,velocity)<=tolerance else "CONTROL_RESIMULATION_DIVERGED",
            "maximum_qpos_error":position,"maximum_qvel_error":velocity,"tolerance":tolerance,
            "compared_checkpoints":checkpoint,"scope":"actual controls from initial state; no corrective checkpoints"}


def inspect_trajectory(path):
    return compact_report(path,read_compact(path))


def scene_for(header):
    from robot_arm.kitchen.model import build_scene
    from robot_arm.kitchen.scene_identity import model_identity
    scene = build_scene(layout_id=header["layout"], seed=header["base_seed"])
    if model_identity(scene, builder_sha256=scene.builder_sha256) != header["model_identity"]:
        raise ValueError("Recorded and current compiled model identity differ; use matching environment")
    return scene


def restore(scene, snap):
    import mujoco
    import numpy as np
    if "integration_state" not in snap:
        scene.data.qpos[:]=snap["qpos"];scene.data.qvel[:]=snap["qvel"]
        scene.data.time=snap["sim_time_s"]
        mujoco.mj_forward(scene.model,scene.data)
        return
    spec = snap["state_spec"]
    state = np.asarray(snap["integration_state"], dtype=float)
    if state.shape != (mujoco.mj_stateSize(scene.model, spec),) or not np.isfinite(state).all():
        raise ValueError("Replay integration state shape/nonfinite error")
    mujoco.mj_setState(scene.model, scene.data, state, spec)
    mujoco.mj_forward(scene.model, scene.data)
    # Restore solver warmstart after forward, while keeping visual caches valid.
    mujoco.mj_setState(scene.model, scene.data, state, spec)


def playback_states(path):
    data=read_compact(path);scene=scene_for(data["metadata"])
    states=[{"sim_tick":int(tick),"sim_time_s":int(tick)/500,
             "qpos":data["states/qpos"][index],"qvel":data["states/qvel"][index]}
            for index,tick in enumerate(data["states/tick"])]
    return scene,states


def video(path, output, *, camera="v3_front", width=960, height=540, fps=25, max_seconds=30., speed=1.):
    # Set before importing MuJoCo/PyOpenGL in this CLI process.
    os.environ.setdefault("MUJOCO_GL", "egl")
    import mujoco
    import shutil
    import subprocess
    scene, states = playback_states(path)
    output = Path(output)
    partial = output.with_name(output.stem + ".partial.mp4")
    log = output.with_suffix(".ffmpeg.log")
    metadata_path = output.with_suffix(".video.json")
    if output.exists() or partial.exists() or log.exists() or metadata_path.exists():
        raise FileExistsError("Use fresh MP4 and log paths")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required for optional MP4 export; state collection does not require it")
    if not width > 0 or not height > 0 or width % 2 or height % 2 or not 1 <= fps <= 60 or not 0 < max_seconds <= 3600 or not .25 <= speed <= 8:
        raise ValueError("Use positive even resolution, 1..60fps and max-seconds 0..3600")
    if mujoco.mj_name2id(scene.model, mujoco.mjtObj.mjOBJ_CAMERA, camera) < 0:
        raise ValueError("Unknown named MuJoCo camera")
    output.parent.mkdir(parents=True, exist_ok=True)
    source_duration = min(max_seconds, max(1 / fps, states[-1]["sim_time_s"] - states[0]["sim_time_s"]))
    frames = max(1, math.ceil(source_duration * fps / speed))
    renderer = mujoco.Renderer(scene.model, height=height, width=width)
    try:
        with log.open("xb") as errors:
            command = ["ffmpeg", "-nostdin", "-n", "-loglevel", "error", "-f", "rawvideo", "-pixel_format", "rgb24",
                       "-video_size", f"{width}x{height}", "-framerate", str(fps), "-i", "pipe:0",
                       "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(partial)]
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=errors)
            try:
                index = 0
                for frame in range(frames):
                    time_s = states[0]["sim_time_s"] + frame * speed / fps
                    while index + 1 < len(states) and states[index + 1]["sim_time_s"] <= time_s + 1e-9:
                        index += 1
                    restore(scene, states[index])
                    renderer.update_scene(scene.data, camera=camera)
                    process.stdin.write(renderer.render().tobytes())
                process.stdin.close()
                if process.wait(timeout=30):
                    raise RuntimeError("ffmpeg export failed; inspect " + str(log))
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
    finally:
        renderer.close()
    partial.rename(output)
    result = {"status": "MUJOCO_REVIEW_VIDEO_WRITTEN", "output": str(output), "frames": frames,
            "fps": fps, "duration_s": frames / fps, "camera": camera,
            "source_duration_s": source_duration, "playback_speed": speed,
            "trajectory_sha256": inspect_trajectory(path)["sha256"],
            "semantics": "recorded-state playback; visual review only, not image training data or new dynamics"}
    with metadata_path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
    return result


def viewer(path, max_seconds=30., speed=1.):
    import time
    import mujoco.viewer
    scene, states = playback_states(path)
    with mujoco.viewer.launch_passive(scene.model, scene.data) as handle:
        started = time.monotonic()
        for snap in states:
            elapsed = snap["sim_time_s"] - states[0]["sim_time_s"]
            if elapsed > max_seconds or not handle.is_running():
                break
            remaining = elapsed / speed - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
            with handle.lock():
                restore(scene, snap)
            handle.sync()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "verify", "video", "viewer"))
    parser.add_argument("trajectory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--camera", default="v3_front")
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=540)
    parser.add_argument("--fps", type=int, default=25)
    parser.add_argument("--max-seconds", type=float, default=30.)
    parser.add_argument("--speed", type=float, default=1., help="Viewing speed only (0.25..8); recording timestamps stay unchanged")
    args = parser.parse_args()
    if not 0 < args.max_seconds <= 3600 or not .25 <= args.speed <= 8:
        parser.error("max-seconds must be 0..3600 and speed 0.25..8")
    if args.action == "inspect":
        result = inspect_trajectory(args.trajectory)
    elif args.action == "verify":
        result = verify(args.trajectory)
    elif args.action == "video":
        if not args.output:
            parser.error("video requires --output")
        result = video(args.trajectory, args.output, camera=args.camera, width=args.width,
                       height=args.height, fps=args.fps, max_seconds=args.max_seconds, speed=args.speed)
    else:
        viewer(args.trajectory, args.max_seconds, args.speed)
        result = {"status": "VIEWER_CLOSED", "task_success": "NOT_INFERRED"}
    print(json.dumps(result, indent=2, allow_nan=False))
    if result["status"] == "CONTROL_RESIMULATION_DIVERGED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
