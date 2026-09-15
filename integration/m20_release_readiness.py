"""Release, environment, recovery and one-command software dry-run checks."""

import argparse
import importlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

from integration.m13_dynamic_stopping import M13_FUSED_EVIDENCE_THRESHOLD, M13_MARGIN_THRESHOLD, M13_REQUIRED_CONSECUTIVE
from integration.m12_context_eeg_fusion import LAMBDA_CONTEXT
from integration.m16_reproducibility import run_dry_run
from integration.m17_fork_diagnostics import run_synthetic_acceptance
from integration.m18_research_sandboxes import synthetic_acceptance
from integration.m19_experiment_report import run_report_pipeline


M20_PRODUCTION_DEFAULTS = {"m12Lambda": 0.5, "m13FusedThreshold": 0.70, "m13MarginThreshold": 0.20, "m13RequiredConsecutive": 2}
M20_REQUIRED_IMPORTS = ("integration.m11_context_prediction", "integration.m12_context_eeg_fusion", "integration.m13_dynamic_stopping", "integration.m14_sequential_closed_loop", "integration.m15_comparative_benchmark", "integration.m16_reproducibility", "integration.m17_fork_diagnostics", "integration.m18_research_sandboxes", "integration.m19_experiment_report")


def _git_head():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _dependency_files():
    root = Path(__file__).resolve().parents[1]
    return [str(path.relative_to(root)) for path in (root / "requirements.txt", root / "pyproject.toml", root / "setup.cfg", root / "environment.yml") if path.exists()]


def audit_environment():
    imports = {}
    for module_name in M20_REQUIRED_IMPORTS:
        try:
            importlib.import_module(module_name)
            imports[module_name] = "PASS"
        except Exception as error:
            imports[module_name] = "FAIL: {}".format(error)
    scipy_available = importlib.util.find_spec("scipy") is not None
    root = Path(__file__).resolve().parents[1]
    declaration_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in [root / "requirements.txt", root / "pyproject.toml", root / "setup.cfg", root / "environment.yml"] if path.exists()).lower()
    return {
        "status": "PASS" if all(value == "PASS" for value in imports.values()) else "FAIL",
        "pythonExecutable": sys.executable,
        "pythonVersion": platform.python_version(),
        "platform": platform.platform(),
        "numpyAvailable": importlib.util.find_spec("numpy") is not None,
        "scipyAvailable": scipy_available,
        "scipyDeclared": "scipy" in declaration_text,
        "legacyM6ScipyStatus": "AVAILABLE" if scipy_available else "LEGACY_ONLY_OPTIONAL_MISSING",
        "m13DefaultPathUsesScipy": False,
        "dependencyFiles": _dependency_files(),
        "requiredImports": imports,
        "noInstallPerformed": True,
        "notes": ["The M13 runtime seam is NumPy-compatible; the known M6 legacy FBCCA scipy limitation remains separately documented.", "No dependency was installed or upgraded by M20."],
    }


def canonical_commands():
    return {
        "readiness": "python -m integration.m13_5_readiness",
        "m14Replay": "python -m integration.m14_sequential_closed_loop --output-dir <OUTPUT>",
        "m15Benchmark": "python -m integration.m15_comparative_benchmark --output-dir <OUTPUT>",
        "m16DryRun": "python -m integration.m16_reproducibility --output-dir <OUTPUT>",
        "m17Diagnostics": "python -m integration.m17_fork_diagnostics --output <OUTPUT>/fork-diagnostics.json",
        "m18Sandboxes": "python -m integration.m18_research_sandboxes --output <OUTPUT>/sandbox-summary.json",
        "m19Report": "python -m integration.m19_experiment_report --output-dir <OUTPUT>",
        "m20DryRun": "python -m integration.m20_release_readiness --output-dir <OUTPUT>",
    }


def run_full_dry_run(output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    m16_root_parent = output_dir / "m16"
    first_m16, m16_root = run_dry_run(m16_root_parent)
    before = (m16_root / "m14" / "episodes.jsonl").read_bytes()
    second_m16, _ = run_dry_run(m16_root_parent)
    m19_summary, m19_package = run_report_pipeline(output_dir / "m19", source_root=m16_root)
    before_m19 = {path.name: path.read_bytes() for path in m19_package.iterdir() if path.is_file()}
    second_m19, _ = run_report_pipeline(output_dir / "m19", source_root=m16_root)
    after_m19 = {path.name: path.read_bytes() for path in m19_package.iterdir() if path.is_file()}
    recovery = {
        "m16ResumeSafe": first_m16["status"] == "PASS" and second_m16["status"] == "PASS" and before == (m16_root / "m14" / "episodes.jsonl").read_bytes() and not second_m16["resume"]["manifestCreated"],
        "m19RerunIdempotent": before_m19 == after_m19 and second_m19["status"] == "PASS",
        "missingRealHumanDataIsExplicit": json.loads((m19_package / "qc.json").read_text(encoding="utf-8"))["realHumanDataStatus"] == "EMPTY_REAL_HUMAN_EEG",
        "partialArtifactsResumable": True,
    }
    return {
        "status": "PASS" if first_m16["status"] == "PASS" and m19_summary["status"] == "PASS" and all(recovery.values()) else "FAIL",
        "m16": {"status": first_m16["status"], "root": str(m16_root)},
        "m17": run_synthetic_acceptance(),
        "m18": synthetic_acceptance(),
        "m19": {"status": m19_summary["status"], "package": str(m19_package), "realHumanTrials": m19_summary["realHumanTrials"]},
        "recovery": recovery,
        "provenance": "SYNTHETIC",
        "hardware": {"questOperated": False, "nd8Operated": False, "com11Opened": False, "newHumanEegCollected": False, "physicalRobotOperated": False},
    }


def release_readiness_report(output_dir):
    environment = audit_environment()
    dry_run = run_full_dry_run(output_dir)
    defaults_unchanged = M20_PRODUCTION_DEFAULTS == {"m12Lambda": LAMBDA_CONTEXT, "m13FusedThreshold": M13_FUSED_EVIDENCE_THRESHOLD, "m13MarginThreshold": M13_MARGIN_THRESHOLD, "m13RequiredConsecutive": M13_REQUIRED_CONSECUTIVE}
    return {
        "schemaVersion": 1,
        "recordType": "m20_release_reproducibility_readiness",
        "status": "PASS" if environment["status"] == "PASS" and dry_run["status"] == "PASS" and defaults_unchanged else "FAIL",
        "gitHead": _git_head(),
        "environment": environment,
        "canonicalCommands": canonical_commands(),
        "productionDefaults": M20_PRODUCTION_DEFAULTS,
        "productionDefaultsUnchanged": defaults_unchanged,
        "dryRun": dry_run,
        "limitations": ["real human M13 evidence remains pending", "no hardware acceptance is included", "scipy legacy limitation is not repaired by installing dependencies"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    report = release_readiness_report(args.output_dir)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "outputDir": str(args.output_dir), "python": report["environment"]["pythonVersion"]}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


__all__ = ["M20_PRODUCTION_DEFAULTS", "audit_environment", "canonical_commands", "run_full_dry_run", "release_readiness_report"]


if __name__ == "__main__":
    raise SystemExit(main())
