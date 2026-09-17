"""Request one safe build from an already-open Unity Editor instance."""

import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUEST = ROOT / "m7_unity6000" / "Builds" / "M9_RoboArm" / "m13_6_build.request.json"


def write_request(path=DEFAULT_REQUEST):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "requestType": "m13_6_one_shot_editor_build",
        "requestedUtc": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "output": "Builds/M9_RoboArm/BCI_M9_2.apk",
        "scene": "Assets/PassthroughCameraApiSamples/MultiObjectDetection/M9VirtualManipulation.unity",
        "autoRunPlayer": True,
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, default=DEFAULT_REQUEST)
    args = parser.parse_args(argv)
    path = write_request(args.request)
    print(json.dumps({"status": "REQUEST_WRITTEN", "path": str(path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
