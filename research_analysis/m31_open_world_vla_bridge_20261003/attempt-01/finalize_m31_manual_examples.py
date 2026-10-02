"""Attach actual frozen-prompt DeepSeek outputs to the required examples."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MANUAL = ROOT / "required_manual_examples.json"
RESULTS = ROOT / "open_world_bridge_results.jsonl"
FREEZE = ROOT / "m31_prompt_freeze.json"


def main():
    label = json.loads(FREEZE.read_text(encoding="utf-8"))["run_label"]
    manual = json.loads(MANUAL.read_text(encoding="utf-8"))
    records = [json.loads(line) for line in RESULTS.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {record["case_id"]: record for record in records if record.get("run_label") == label}
    missing = []
    for example in manual["examples"]:
        record = by_id.get(example["case_id"])
        if record is None:
            missing.append(example["case_id"])
            continue
        result = record["result"]
        object_names = {item["object_id"]: item["descriptor"]
                        for item in [*result.get("selected_objects", []), *result.get("scene_context_objects", [])]}
        example["actual_result"] = {
            "status": result.get("status"),
            "relation_type": result.get("relation_type"),
            "custom_relation": result.get("custom_relation"),
            "source_object": object_names.get(result.get("source_object_id")),
            "target_or_tool_object": object_names.get(result.get("target_or_tool_object_id")),
            "ordered_high_level_steps": result.get("ordered_high_level_steps", []),
            "ambiguity_reason": result.get("ambiguity_reason", ""),
            "vla_instruction_zh": result.get("vla_instruction_zh", ""),
            "vla_instruction_en": result.get("vla_instruction_en", ""),
            "dispatch_allowed": result.get("dispatch_allowed", False),
            "api_latency_ms": result.get("provenance", {}).get("api_latency_ms"),
            "retries": result.get("provenance", {}).get("retries", 0),
            "usage": result.get("provenance", {}).get("usage", {}),
        }
    if missing:
        raise ValueError("manual examples missing frozen run results: {}".format(", ".join(missing)))
    manual["prompt_version"] = json.loads(FREEZE.read_text(encoding="utf-8"))["prompt_version"]
    manual["run_label"] = label
    MANUAL.write_text(json.dumps(manual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("manual_examples_attached={}".format(len(manual["examples"])))


if __name__ == "__main__":
    main()
