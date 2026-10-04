#!/usr/bin/env python3
"""Final M36 source-integrity, compilation, artifact and OOF-verifier audit."""

import csv
import hashlib
import json
import py_compile
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
M34 = REPO / "research_analysis" / "m34_high_speed_ssvep_context_20261004" / "attempt-01"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    grouping = read_json(ROOT / "m36_grouping_audit.json")
    protocol_copy = ROOT / "M36_PROTOCOL.md"
    protocol_source = Path(grouping["protocolCopy"]["source"])
    assert sha256(protocol_copy) == grouping["protocolCopy"]["copySha256"] == grouping["protocolCopy"]["sourceSha256"]
    assert sha256(protocol_source) == grouping["protocolCopy"]["sourceSha256"]
    m34_manifest = M34 / "data_manifest.json"
    manifest_hash = sha256(m34_manifest)
    assert manifest_hash == grouping["sourceManifestSha256"]

    raw_checks = []
    for item in grouping["rawHashSnapshot"]:
        observed = sha256(item["rawPath"])
        raw_checks.append({
            "sessionKey": item["sessionKey"],
            "path": item["rawPath"],
            "expectedManifestSha256": item["manifestSha256"],
            "observedFinalSha256": observed,
            "matchesManifest": observed == item["manifestSha256"],
        })
        assert observed == item["manifestSha256"]

    compile_files = sorted(ROOT.glob("*.py"))
    for path in compile_files:
        py_compile.compile(str(path), doraise=True)

    verifier_modes = ["--folds-only", "--rules-only", "--logistic-only", "--view-guard-only",
                      "--results-only", "--diagnostics-only", "--presentation-only"]
    verification = []
    for mode in verifier_modes:
        proc = subprocess.run([sys.executable, str(ROOT / "verify_m36_artifacts.py"), mode], cwd=str(REPO),
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError("verifier {} failed: {}".format(mode, proc.stderr or proc.stdout))
        payload = json.loads(proc.stdout)
        assert payload["status"] == "PASS", (mode, payload)
        verification.append({"mode": mode, "exitCode": proc.returncode, "result": payload})

    source_integrity = read_json(ROOT / "m36_source_integrity_after_features.json")
    trajectory = read_json(ROOT / "trajectory_reproduction.json")
    rules = read_json(ROOT / "rule_evaluation_summary.json")
    logistic = read_json(ROOT / "logistic_evaluation_summary.json")
    view_guard = read_json(ROOT / "view_guard_logistic_summary.json")
    diagnostics = read_json(ROOT / "diagnostics_summary.json")
    figures = read_json(ROOT / "figures_manifest.json")
    report = (ROOT / "M36_FINAL_REPORT.md").read_text(encoding="utf-8")
    assert source_integrity["status"] == "PASS" and all(row["unchanged"] for row in source_integrity["rawFiles"])
    assert trajectory["status"] == "PASS" and trajectory["allRawInputsMatchFrozenManifestBeforeAndAfter"]
    assert rules["status"] == logistic["status"] == view_guard["status"] == "PASS"
    assert diagnostics["status"] == "PASS" and diagnostics["deepseekCalls"] == 0
    assert figures["status"] == "PASS" and figures["figureCount"] == 12
    for row in figures["figures"]:
        ET.parse(ROOT / row["file"])
    assert all("## {}.".format(n) in report for n in range(1, 23))
    tracks = read_json(ROOT / "data_track_summary.json")
    assert tracks["uniqueRealTrials"] == 118
    assert tracks["tracks"]["track1_5ch"]["realTrialCount"] == 88
    assert tracks["tracks"]["track2_common3"]["realTrialCount"] == 118

    result = {
        "status": "PASS",
        "runId": "m36-context-safe-scaling-20261004",
        "sourceRawEegReadOnly": True,
        "rawEegFiles": raw_checks,
        "m34ManifestSha256": manifest_hash,
        "m36ProtocolSha256": grouping["protocolCopy"]["sourceSha256"],
        "compiledPythonFiles": [p.name for p in compile_files],
        "verificationModes": verification,
        "uniqueRealTrials": tracks["uniqueRealTrials"],
        "track1Trials": tracks["tracks"]["track1_5ch"]["realTrialCount"],
        "track2Trials": tracks["tracks"]["track2_common3"]["realTrialCount"],
        "svgFigureCount": figures["figureCount"],
        "reportAnswers": 22,
        "deepseekCalls": diagnostics["deepseekCalls"],
        "hardwareTouched": False,
    }
    (ROOT / "final_audit.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "rawEegFiles": len(raw_checks), "compiledPythonFiles": len(compile_files),
                      "verifierModes": len(verification), "uniqueRealTrials": 118, "svgFigureCount": figures["figureCount"],
                      "finalAudit": "final_audit.json"}, sort_keys=True))


if __name__ == "__main__":
    main()
