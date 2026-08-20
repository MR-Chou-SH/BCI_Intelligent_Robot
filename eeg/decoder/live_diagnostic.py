"""M6.6 diagnostic-live plans and evidence checks; no hardware actions."""
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


def generate_diagnostic_plan(session_id):
    """Return the explicit, fixed three-target plan accepted only by Quest diagnostic mode."""
    if not session_id:
        raise ValueError("session_id is required")
    targets = (("target_left", "left", 7.2), ("target_center", "center", 9.0),
               ("target_right", "right", 12.0))
    trials = []
    for index, (target_id, target_side, frequency) in enumerate(targets, 1):
        trials.append({"sessionId": session_id, "trialId": "{}-diagnostic-{:03d}".format(session_id, index),
                       "trialIndex": index, "targetId": target_id, "targetSide": target_side,
                       "nominalFrequencyHz": frequency, "expectedStimulusDurationSeconds": 4.0})
    return {"sessionId": session_id, "planMode": "diagnostic_live", "trials": trials}


def verify_diagnostic_evidence(root):
    """Small 3-trial evidence check; deliberately unrelated to the formal 30-trial verifier."""
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    required = {"quest-events.jsonl", "associations.jsonl", "predictions.jsonl", "decisions.jsonl"}
    if not required.issubset(set(manifest.get("files", []))):
        return {"status": "incomplete", "errors": ["required_evidence_files_missing_from_manifest"]}
    def records(name):
        return [json.loads(line) for line in (root / name).read_text(encoding="utf-8").splitlines() if line]
    events, associations, decisions = records("quest-events.jsonl"), records("associations.jsonl"), records("decisions.jsonl")
    starts = {r.get("originalQuestEvent", r).get("trialId") for r in events if r.get("originalQuestEvent", r).get("eventType") == "stimulus_started_software"}
    stops = {r.get("originalQuestEvent", r).get("trialId") for r in events if r.get("originalQuestEvent", r).get("eventType") == "stimulus_stopped_software"}
    valid = {(r.get("trialId"), r.get("stimulusEventType")) for r in associations if r.get("associationValid")}
    trial_ids = starts | stops
    errors = []
    if len(trial_ids) != 3 or starts != trial_ids or stops != trial_ids: errors.append("expected_three_complete_event_pairs")
    if any((trial_id, kind) not in valid for trial_id in trial_ids for kind in ("stimulus_started_software", "stimulus_stopped_software")): errors.append("missing_valid_association")
    if len({r.get("trialId") for r in decisions}) != 3: errors.append("missing_decision_or_explicit_no_decision")
    return {"status": "complete" if not errors else "incomplete", "errors": errors,
            "trialCount": len(trial_ids), "decisionCount": len(decisions), "groundTruthLeakage": manifest.get("groundTruthLeakage")}
