"""Verify the M13.6 APK artifact without rebuilding or changing Unity state."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from integration.m13_6_usb_visual_acceptance import DEFAULT_ADB, PACKAGE, _adb_path, _run_adb


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APK = ROOT / "m7_unity6000/Builds/M9_RoboArm/BCI_M9_2.apk"
SOURCE_PATHS = (
    ROOT / "m7_unity6000/Assets/PassthroughCameraApiSamples/MultiObjectDetection/DetectionManager/Scripts/M13_6VisualSyncReceiver.cs",
    ROOT / "m7_unity6000/Assets/PassthroughCameraApiSamples/MultiObjectDetection/M9VirtualManipulation.unity",
)


def _find_aapt():
    candidates = [
        Path(r"C:\Program Files\Unity\Hub\Editor\6000.0.66f2\Editor\Data\PlaybackEngines\AndroidPlayer\SDK\build-tools\36.0.0\aapt.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return Path(shutil.which("aapt") or "")


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aapt_metadata(apk):
    aapt = _find_aapt()
    if not aapt or not aapt.is_file():
        return {"status": "UNAVAILABLE", "aapt": str(aapt) if aapt else None}
    result = subprocess.run([str(aapt), "dump", "badging", str(apk)], capture_output=True, text=True, check=False)
    first = next((line for line in result.stdout.splitlines() if line.startswith("package:")), "")
    match = re.search(r"^package: name='([^']*)' versionCode='([^']*)' versionName='([^']*)'", first)
    values = {"name": match.group(1), "versionCode": match.group(2), "versionName": match.group(3)} if match else {}
    return {"status": "PASS" if result.returncode == 0 and match else "FAIL", "aapt": str(aapt), "raw": first, **values}


def verify_apk(apk_path=DEFAULT_APK):
    apk = Path(apk_path).resolve()
    result = {
        "schemaVersion": 1,
        "recordType": "m13_6_apk_verification",
        "apk": str(apk),
        "expectedPackage": PACKAGE,
        "sourcePaths": [str(path) for path in SOURCE_PATHS],
    }
    if not apk.is_file():
        result.update({"status": "MISSING_APK", "exists": False})
        return result

    apk_stat = apk.stat()
    source_stats = [{"path": str(path), "exists": path.is_file(), "mtime": path.stat().st_mtime if path.is_file() else None} for path in SOURCE_PATHS]
    source_mtime = max((item["mtime"] for item in source_stats if item["mtime"] is not None), default=0.0)
    metadata = _aapt_metadata(apk)
    package_matches = metadata.get("name") == PACKAGE
    source_newer = source_mtime > apk_stat.st_mtime
    result.update({
        "exists": True,
        "sizeBytes": apk_stat.st_size,
        "mtime": apk_stat.st_mtime,
        "sha256": _sha256(apk),
        "aapt": metadata,
        "sourceStats": source_stats,
        "sourceLatestMtime": source_mtime,
        "sourceNewerThanApk": source_newer,
        "packageMatches": package_matches,
        "status": "PASS" if metadata.get("status") == "PASS" and package_matches and not source_newer else ("STALE_SOURCE_BUILD" if package_matches and source_newer else "PACKAGE_OR_METADATA_MISMATCH"),
    })
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", default=str(DEFAULT_APK))
    parser.add_argument("--output", default=str(ROOT / "artifacts/m13_6_apk_verification.json"))
    args = parser.parse_args(argv)
    result = verify_apk(args.apk)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "apk": result["apk"], "packageMatches": result.get("packageMatches", False), "sourceNewerThanApk": result.get("sourceNewerThanApk")}, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
