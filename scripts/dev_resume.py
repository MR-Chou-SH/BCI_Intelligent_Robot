"""Dev-only: resume expert planning from a key-state checkpoint.

Example:
    .venv/bin/python -B scripts/dev_resume.py \
        experiments/kitchen_platform_v3/checkpoints/picked_burger_container.npz \
        --action place --label oven
"""
from __future__ import annotations

import argparse
import subprocess

import mujoco

from robot_arm.kitchen.platform import KitchenPlatform
from integration.kitchen.checkpoint import load
from integration.kitchen.expert import KitchenExpert, LocalPlannerClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint")
    parser.add_argument("--action", default="place")
    parser.add_argument("--label", default="oven")
    parser.add_argument("--render", default=None, help="PNG prefix rendered at failure")
    args = parser.parse_args()
    platform = KitchenPlatform(layout="kitchen_v3_norice_candidate_013", seed=0,
                               initialization_profile="fixed")
    try:
        expert = KitchenExpert(platform, LocalPlannerClient(platform), wall_seconds=1800)
        load(expert, args.checkpoint)
        print("RESUME_STATE", expert.stage, expert.held, flush=True)
        try:
            if args.action == "place":
                expert.place(args.label)
            elif args.action == "orient":
                view = expert.view()
                body = view.model.body("microwave").id
                expert.orient_payload(view.data.xmat[body].reshape(3, 3).copy())
            elif args.action == "close":
                expert.close_door()
            elif args.action == "push":
                expert.push_door(0.)
            else:
                raise SystemExit("unknown action: " + args.action)
            print("RESUME_OK", expert.stage, flush=True)
        except Exception as error:
            print("RESUME_FAILED", str(error)[:300], flush=True)
            if args.render:
                for camera in ("v3_oblique", "v3_front", "v3_top"):
                    renderer = mujoco.Renderer(platform.scene.model, 720, 1280)
                    renderer.update_scene(platform.scene.data, camera=camera)
                    image = renderer.render()
                    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "rawvideo",
                                    "-pixel_format", "rgb24", "-video_size", "1280x720", "-i", "pipe:0",
                                    "-frames:v", "1", f"{args.render}_{camera}.png"],
                                   input=image.tobytes(), check=True)
                    print("RENDERED", camera, flush=True)
            raise
    finally:
        platform.close()


if __name__ == "__main__":
    main()
