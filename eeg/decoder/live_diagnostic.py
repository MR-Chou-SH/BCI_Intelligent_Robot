"""M6.6 diagnostic-live evidence manifest preparation; no hardware actions."""
import json, platform
from pathlib import Path

FILES = ("raw-eeg.jsonl", "packet-metadata.jsonl", "continuity-gate.jsonl", "quest-events.jsonl",
         "synchronization.jsonl", "associations.jsonl", "predictions.jsonl", "decisions.jsonl", "session-events.jsonl")

def prepare_manifest(root, session_id, git_commit, channels):
    root=Path(root); root.mkdir(parents=True,exist_ok=False)
    manifest={"sessionId":session_id,"mode":"diagnostic_live","gitCommit":git_commit,
      "pythonRuntime":platform.python_version(),"decoder":"numpy_fbcca","frequenciesHz":[7.2,9.0,12.0],
      "guardSeconds":.5,"windowSeconds":1.5,"stepSeconds":.2,"stabilizer":"2-Consecutive",
      "sampleRateHz":1000,"selectedChannels":list(channels),"status":"prepared","packetCount":0,
      "predictionCount":0,"finalDecisionCount":0,"continuityStatus":"not_started",
      "groundTruthLeakage":False,"hardwareTimingVerified":False,"physicalOpticalTimingVerified":False,
      "hardwareSampleAnchorVerified":False,"files":list(FILES)}
    (root/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    for name in FILES: (root/name).touch(exist_ok=False)
    return manifest
