"""Versioned, explicit initial kitchen layout variants."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

_MANIFEST_PATH = Path(__file__).with_name("layouts.json")
_MANIFEST_BYTES = _MANIFEST_PATH.read_bytes()
_MANIFEST = json.loads(_MANIFEST_BYTES)
if _MANIFEST.get("schema") != 1 or not isinstance(_MANIFEST.get("layouts"), dict):
    raise RuntimeError("Invalid kitchen layout manifest")
_V3_PATH = Path(__file__).with_name("layouts_v3.json")
if _V3_PATH.exists():
    _V3 = json.loads(_V3_PATH.read_bytes())
    if _V3.get("schema") != 1 or set(_V3["layouts"]) & set(_MANIFEST["layouts"]):
        raise RuntimeError("Invalid or conflicting V3 layout manifest")
    _MANIFEST["layouts"].update(_V3["layouts"])
LAYOUT_IDS = tuple(sorted(_MANIFEST["layouts"]))
DEFAULT_PLATFORM_LAYOUT = "kitchen_v3_norice_candidate_005"


def layout_manifest(layout_id: str) -> dict:
    try:
        value = _MANIFEST["layouts"][layout_id]
    except KeyError as error:
        raise ValueError(f"Unknown kitchen layout: {layout_id}") from error
    return json.loads(json.dumps(value, sort_keys=True))


def layout_manifest_sha256(layout_id: str) -> str:
    canonical = json.dumps(layout_manifest(layout_id), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def layout_code_sha256() -> str:
    code = Path(__file__).read_bytes()
    extension = Path(__file__).with_name("layout_v3.py")
    if extension.exists():
        code += extension.read_bytes()
    return hashlib.sha256(code).hexdigest()


def apply_layout(spec, layout_id: str) -> None:
    layout = layout_manifest(layout_id)
    if layout_id == "canonical":
        return
    if layout.get("profile") == "kitchen_platform_v3":
        from .layout_v3 import apply_v3_layout
        apply_v3_layout(spec, layout)
        return
    microwave = spec.body("microwave")
    microwave.quat = layout["microwave_quat_wxyz"]
    for name, position in layout["body_positions"].items():
        spec.body(name).pos = position

