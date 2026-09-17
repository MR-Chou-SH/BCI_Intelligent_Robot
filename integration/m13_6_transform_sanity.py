"""Software-only sanity checks for M13.6 world-state trajectories.

This tool checks the values that would be applied by the Quest receiver.  It
does not operate Quest, ADB, ND8, COM11, or a physical robot and cannot replace
human visual acceptance inside the headset.
"""

import argparse
import json
import math
from pathlib import Path

from integration.m13_6_usb_visual_acceptance import _synthetic_frame
from integration.m13_6_visual_sync import RecordingStateSink, run_mujoco_pick_place_stream


ROOT = Path(__file__).resolve().parents[1]


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _distance(left, right):
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left, right)))


def _angular_distance(left, right):
    dot = abs(sum(float(a) * float(b) for a, b in zip(left, right)))
    dot = max(-1.0, min(1.0, dot))
    return 2.0 * math.acos(dot)


def analyze_frames(frames, provenance):
    if not frames:
        return {"status": "BLOCKED", "reason": "no_frames", "provenance": provenance}

    joint_values = [value for frame in frames for value in frame.joint_positions_radians]
    block_positions = [position for frame in frames for block in frame.blocks for position in [block.position_mujoco]]
    finite = all(
        _finite(value)
        for frame in frames
        for value in (
            list(frame.joint_positions_radians)
            + list(frame.robot_base_position_mujoco)
            + list(frame.robot_base_quaternion_mujoco_wxyz)
            + [frame.left_finger_position_meters, frame.right_finger_position_meters, frame.gripper_opening_meters]
            + [value for block in frame.blocks for value in block.position_mujoco + block.quaternion_mujoco_wxyz]
        )
    )
    quaternion_norms = [
        math.sqrt(sum(value * value for value in block.quaternion_mujoco_wxyz))
        for frame in frames for block in frame.blocks
    ] + [
        math.sqrt(sum(value * value for value in frame.robot_base_quaternion_mujoco_wxyz))
        for frame in frames
    ]
    max_position_jump = 0.0
    max_angular_jump = 0.0
    for previous, current in zip(frames, frames[1:]):
        max_position_jump = max(
            max_position_jump,
            _distance(previous.robot_base_position_mujoco, current.robot_base_position_mujoco),
        )
        max_angular_jump = max(
            max_angular_jump,
            _angular_distance(previous.robot_base_quaternion_mujoco_wxyz, current.robot_base_quaternion_mujoco_wxyz),
        )
        previous_blocks = {block.logical_block_id: block for block in previous.blocks}
        for block in current.blocks:
            old = previous_blocks[block.logical_block_id]
            max_position_jump = max(max_position_jump, _distance(old.position_mujoco, block.position_mujoco))
            max_angular_jump = max(max_angular_jump, _angular_distance(old.quaternion_mujoco_wxyz, block.quaternion_mujoco_wxyz))

    result = {
        "schemaVersion": 1,
        "recordType": "m13_6_transform_sanity",
        "provenance": provenance,
        "frameCount": len(frames),
        "sequenceMonotonic": all(left.sequence < right.sequence for left, right in zip(frames, frames[1:])),
        "finiteValues": finite,
        "quaternionNormMin": min(quaternion_norms),
        "quaternionNormMax": max(quaternion_norms),
        "quaternionNormValid": all(abs(value - 1.0) <= 1e-3 for value in quaternion_norms),
        "jointMinRadians": min(joint_values),
        "jointMaxRadians": max(joint_values),
        "maxPositionJumpMeters": max_position_jump,
        "maxAngularJumpRadians": max_angular_jump,
        "blockWorkspaceMinMujoco": [min(values[index] for values in block_positions) for index in range(3)],
        "blockWorkspaceMaxMujoco": [max(values[index] for values in block_positions) for index in range(3)],
    }
    result["status"] = "PASS" if all(
        (result[key] if isinstance(result[key], bool) else True)
        for key in ("finiteValues", "sequenceMonotonic", "quaternionNormValid")
    ) else "FAIL"
    return result


def _synthetic_frames(count):
    return [_synthetic_frame(index, index / 30.0) for index in range(count)]


def _mujoco_frames():
    sink = RecordingStateSink()
    summary = run_mujoco_pick_place_stream(sink=sink, rate_hz=30.0, max_steps=9000)
    return sink.frames, {"source": "mujoco", "planner": summary.get("planner", {})}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("synthetic", "mujoco"), default="synthetic")
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--output", default=str(ROOT / "artifacts/m13_6_transform_sanity.json"))
    args = parser.parse_args(argv)
    if args.source == "synthetic":
        frames = _synthetic_frames(args.frames)
        provenance = {"source": "synthetic", "frameCountRequested": args.frames}
    else:
        frames, provenance = _mujoco_frames()
    result = analyze_frames(frames, provenance)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output), "frameCount": result.get("frameCount", 0)}, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
