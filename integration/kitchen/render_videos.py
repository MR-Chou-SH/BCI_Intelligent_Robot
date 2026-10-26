"""Render one visual-review MP4 per recorded expert case.

Visual review only: playback uses the sealed state trajectory and the current
compiled model, so a trajectory recorded under a different model identity is
rejected by replay.scene_for instead of being silently re-rendered. MP4 files
are review artefacts, never image training data and never task-success evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .replay import video


def cases(run: Path):
    for report_path in sorted(run.glob("*/report.json")):
        report = json.loads(report_path.read_text())
        trajectory = report.get("trajectory")
        if not trajectory or not Path(trajectory).exists():
            yield report, None
            continue
        yield report, Path(trajectory)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path, help="collect_experts output directories")
    parser.add_argument("--output", type=Path, required=True, help="new directory for the MP4 review set")
    parser.add_argument("--camera", default="v3_front")
    parser.add_argument("--speed", type=float, default=4., help="playback speed only (0.25..8)")
    parser.add_argument("--fps", type=int, default=25)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=540)
    args = parser.parse_args(argv)
    if not .25 <= args.speed <= 8 or not 1 <= args.fps <= 60:
        parser.error("speed 0.25..8, fps 1..60")
    if args.output.exists():
        parser.error("Use a fresh output directory; existing videos are never overwritten")
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MUJOCO_GL", "egl")
    rows = []
    for run in args.runs:
        for report, trajectory in cases(run):
            case_id = report.get("case", {}).get("case_id", "unknown")
            status = report.get("status", "UNKNOWN")
            slug = case_id.replace(":", "_").replace("/", "_")
            base = f"{run.name}__{slug}"
            row = {"run": str(run), "case_id": case_id, "status": status,
                   "trajectory": str(trajectory) if trajectory else None}
            if trajectory is None:
                row["video"] = None
                row["skipped"] = "NO_SEALED_TRAJECTORY"
            else:
                output = args.output / (base + ".mp4")
                try:
                    result = video(trajectory, output, camera=args.camera, width=args.width,
                                   height=args.height, fps=args.fps, max_seconds=3600., speed=args.speed)
                    row.update(video=str(output), frames=result["frames"],
                               duration_s=result["duration_s"], source_duration_s=result["source_duration_s"])
                except Exception as error:  # keep rendering the remaining cases
                    row["video"] = None
                    row["error"] = repr(error)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    index = {"schema": "kitchen_expert_review_videos_v1", "runs": [str(r) for r in args.runs],
             "camera": args.camera, "speed": args.speed, "fps": args.fps,
             "images_included": False,
             "semantics": "visual review of sealed state trajectories; not training data, not task success",
             "cases": rows}
    (args.output / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                                            encoding="utf-8")
    rendered = sum(1 for row in rows if row.get("video"))
    print(json.dumps({"rendered": rendered, "total": len(rows), "output": str(args.output)}))
    return 0 if rendered == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
