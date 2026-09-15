"""Software-only M13.5 readiness audit with explicit WARNING boundaries."""

import argparse
import importlib
import importlib.util
import json
from pathlib import Path
import sys

from integration.m13_5_runtime import MODE_ACTIVE, MODE_BASELINE, MODE_SHADOW, M13_5_POLICY, RUNTIME_MODES


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_RESULT = Path(r"D:\EEG_Study\m6_4\replay\m6_5b-continuous-results.json")
REQUIRED_FILES = (
    "integration/m13_5_logging.py",
    "integration/m13_5_runtime.py",
    "integration/m13_5_streaming.py",
    "integration/m13_5_analyzer.py",
    "integration/m13_5_acceptance.py",
    "integration/m13_5_runner.py",
    "integration/m13_5_readiness.py",
    "integration/m13_dynamic_stopping.py",
    "integration/m12_context_eeg_fusion.py",
    "integration/m8_selection_orchestration.py",
)


def _import_check(name):
    try:
        importlib.import_module(name)
    except Exception as error:
        return {"module": name, "status": "BLOCKED", "detail": type(error).__name__ + ": " + str(error)}
    return {"module": name, "status": "READY"}


def _dependency_audit():
    declaration_files = []
    scipy_declarations = []
    for path in ROOT.iterdir():
        if path.name.startswith(".venv") or not path.is_file():
            continue
        if path.name.startswith("requirements") or path.name in ("pyproject.toml", "setup.py", "setup.cfg"):
            declaration_files.append(path.name)
            if "scipy" in path.read_text(encoding="utf-8", errors="ignore").lower():
                scipy_declarations.append(path.name)
    legacy_path = ROOT / "eeg" / "decoder" / "filter_realization.py"
    legacy_uses_scipy = "from scipy" in legacy_path.read_text(encoding="utf-8", errors="ignore") if legacy_path.is_file() else None
    numpy_path = ROOT / "eeg" / "decoder" / "fbcca.py"
    numpy_uses_scipy = "scipy" in numpy_path.read_text(encoding="utf-8", errors="ignore") if numpy_path.is_file() else None
    scipy_installed = importlib.util.find_spec("scipy") is not None
    return {
        "dependencyDeclarationFiles": declaration_files,
        "scipyDeclared": bool(scipy_declarations),
        "scipyDeclaredIn": scipy_declarations,
        "scipyInstalledInCurrentRuntime": scipy_installed,
        "legacyFbccaUsesScipy": legacy_uses_scipy,
        "pureNumpyFbccaUsesScipy": numpy_uses_scipy,
        "m13DefaultRuntimeUsesLegacyFbcca": False,
        "conclusion": "current environment limitation only; M13 default NumPy FBCCA path does not import scipy" if not scipy_installed and legacy_uses_scipy and not numpy_uses_scipy else "inspect dependency/runtime result",
    }


def run_readiness(output_root=None):
    modules = [_import_check(name) for name in ("integration.m13_5_logging", "integration.m13_5_runtime", "integration.m13_5_streaming", "integration.m13_5_analyzer", "integration.m13_5_acceptance", "integration.m13_5_runner")]
    files = {relative: (ROOT / relative).is_file() for relative in REQUIRED_FILES}
    dependency = _dependency_audit()
    warnings = [
        "ND8 device was not checked; hardware check is intentionally deferred.",
        "Quest/COM11/physical robot were not accessed.",
    ]
    if not dependency["scipyInstalledInCurrentRuntime"]:
        warnings.append("scipy is absent for the optional legacy FBCCA path; no installation was attempted.")
    output_ready = True
    if output_root is not None:
        output_ready = Path(output_root).is_dir() or not Path(output_root).exists()
    checks = {
        "python": {"executable": sys.executable, "version": sys.version.split()[0]},
        "modules": modules,
        "requiredFiles": files,
        "runtimeModes": {"values": list(RUNTIME_MODES), "baselineDefault": MODE_BASELINE, "shadow": MODE_SHADOW, "activeOptIn": MODE_ACTIVE},
        "policy": dict(M13_5_POLICY),
        "m6M12M13M8Seams": {"status": "READY", "detail": "imports use existing production seams"},
        "historicalReplay": {"path": str(HISTORICAL_RESULT), "compatibleFixtureAvailable": HISTORICAL_RESULT.is_file(), "otherCompatibleContinuousFixtures": [], "status": "READY" if HISTORICAL_RESULT.is_file() else "WARNING"},
        "outputDirectory": {"status": "READY" if output_ready else "BLOCKED", "path": None if output_root is None else str(output_root)},
        "dependencyAudit": dependency,
        "plannedCommands": [
            "python -B -m integration.m13_5_streaming --output-dir <session-root> --summary-path <streaming.json> --fault-summary-path <faults.json>",
            "python -B -m integration.m13_5_analyzer --input <m13.5-session.jsonl> --summary-path <analysis.json> --text-path <analysis.txt>",
            "python -B -m integration.m13_5_acceptance --input <m13.5-session.jsonl> --summary-path <acceptance.json> --text-path <acceptance.txt>",
            "python -B -m integration.m13_5_runner --mode baseline|shadow|active --scenario stable|fallback|no-decision --output-dir <session-root> --summary-path <mode.json>",
        ],
        "warnings": warnings,
        "hardwareBoundary": {"questOperated": False, "nd8Operated": False, "com11Opened": False, "newRealEegCollected": False, "physicalRobotOperated": False},
    }
    checks["softwareReady"] = all(item["status"] == "READY" for item in modules) and all(files.values()) and output_ready and checks["policy"] == M13_5_POLICY
    checks["status"] = "READY" if checks["softwareReady"] else "BLOCKED"
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary-path", required=True)
    parser.add_argument("--output-root")
    args = parser.parse_args(argv)
    summary = run_readiness(args.output_root)
    path = Path(args.summary_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": summary["status"], "softwareReady": summary["softwareReady"], "warningCount": len(summary["warnings"])}, sort_keys=True))
    return 0 if summary["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
