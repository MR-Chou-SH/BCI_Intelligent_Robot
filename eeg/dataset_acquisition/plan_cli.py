"""Create an M6.1b plan/session manifest without starting hardware."""

import argparse
import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .plan import GENERATOR_VERSION, generate_trial_plan, write_ground_truth


def main():
    parser = argparse.ArgumentParser(description="Create an M6.1b three-class ground-truth plan")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--session-id")
    args = parser.parse_args()
    session_id = args.session_id or "m6_1b-dataset-{}-{}".format(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:8])
    session = args.data_root / session_id
    session.mkdir(parents=True, exist_ok=False)
    plan = generate_trial_plan(session_id, args.seed)
    protocol = {"preparationSeconds": 13.0, "cueSeconds": 2.0, "preStimulusRestSeconds": 1.0,
                "stimulusSeconds": 4.0, "postStimulusRestSeconds": 2.0, "breakAfterTrials": [10, 20],
                "breakSeconds": 25.0, "rawWindow": "complete_formal_stimulation_context"}
    write_ground_truth(session / "trial-ground-truth.jsonl", plan, protocol)
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unavailable"
    manifest = {"recordType": "m6_1b_dataset_session", "sessionId": session_id,
                "createdUtc": datetime.now(timezone.utc).isoformat(), "experiment": "M6.1b",
                "gitCommit": commit, "randomSeed": args.seed, "generatorVersion": GENERATOR_VERSION,
                "trialCount": 30, "classBalance": {"target_left": 10, "target_center": 10, "target_right": 10},
                "nominalStimulusFrequenciesHz": {"target_left": 7.2, "target_center": 9.0, "target_right": 12.0},
                "protocol": protocol, "rawEvidenceBoundary": {"hardwareTimingVerified": False,
                "physicalOpticalTimingVerified": False, "sampleAnchor": "unverified"},
                "trialGroundTruthFile": "trial-ground-truth.jsonl", "status": "planned"}
    (session / "session-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(session)


if __name__ == "__main__":
    main()
