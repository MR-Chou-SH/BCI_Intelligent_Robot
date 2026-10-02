"""Run M31 cases without exposing evaluation labels to DeepSeek."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from integration.semantic_intelligence import build_live_client
from integration.semantic_language_bridge import (
    M31_ENGINE_VERSION,
    M31_PROMPT_VERSION,
    M31_SCHEMA_VERSION,
    SemanticLanguageBridge,
    _has_color,
    _invented_color_mentions,
    _state_claim_errors,
)
from validate_m31_benchmark import validate


BENCHMARK = ROOT / "open_world_bridge_benchmark.json"
LOCK = ROOT / "open_world_bridge_benchmark_lock.json"
RESULTS = ROOT / "open_world_bridge_results.jsonl"
SUMMARY = ROOT / "open_world_bridge_summary.json"
PROMPT_FREEZE = ROOT / "m31_prompt_freeze.json"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_records():
    if not RESULTS.exists():
        return []
    output = []
    for line_number, line in enumerate(RESULTS.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            output.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError("invalid results JSONL at line {}".format(line_number)) from error
    return output


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 3)


_RELATION_STEP_FAMILIES = {
    "PLACE_IN": {"PLACE", "PUT", "STORE", "PLACE_IN", "STORE_IN"},
    "STORE_IN": {"PLACE", "PUT", "STORE", "PLACE_IN", "STORE_IN"},
    "PLACE_ON": {"PLACE", "PUT", "STORE", "PLACE_ON", "STORE_ON"},
    "STORE_ON": {"PLACE", "PUT", "STORE", "PLACE_ON", "STORE_ON"},
    "HAND_OVER": {"HAND_OVER", "GIVE"}, "CHARGE_WITH": {"CHARGE", "CHARGE_WITH", "USE"},
    "CUT_WITH": {"CUT", "CUT_WITH", "USE"}, "JUICE_WITH": {"JUICE", "JUICE_WITH", "USE"},
    "RINSE_WITH": {"RINSE", "RINSE_WITH", "WASH", "WASH_WITH", "CLEAN_WITH"},
    "WASH_WITH": {"WASH", "WASH_WITH", "CLEAN_WITH"},
    "FILL_FROM": {"FILL", "FILL_FROM", "POUR", "POUR_INTO"},
    "POUR_INTO": {"POUR", "POUR_INTO", "FILL"},
    "FASTEN_WITH": {"FASTEN", "FASTEN_WITH", "USE"},
    "PRESS": {"PRESS"}, "OPEN": {"OPEN"}, "CLOSE": {"CLOSE"},
    "DISCARD_IN": {"DISCARD_IN", "DISCARD", "PLACE_IN", "PUT"},
    "USE_WITH": {"USE", "USE_WITH", "CUSTOM"},
}
_RELATION_LANGUAGE_CUES = {
    "PLACE_IN": (("放进", "放入", "放到", "收纳", "装进"), (" into ", " in ", "inside", "place", "put ", "store")),
    "STORE_IN": (("放进", "放入", "放到", "收纳", "装进"), (" into ", " in ", "inside", "place", "put ", "store")),
    "PLACE_ON": (("放到", "放在", "放上", "摆在"), (" on ", " onto ", "place", "put ", "store")),
    "STORE_ON": (("放到", "放在", "放上", "摆在"), (" on ", " onto ", "place", "put ", "store")),
    "HAND_OVER": (("递给", "交给", "交到"), ("hand over", "give ", "pass ")),
    "CHARGE_WITH": (("充电", "充上电"), ("charge", "charging")),
    "CUT_WITH": (("切", "切开", "切碎"), ("cut", "slice")),
    "JUICE_WITH": (("榨汁", "榨成果汁"), ("juice", "juicer")),
    "RINSE_WITH": (("冲洗", "冲", "清洗", "洗"), ("rinse", "wash", "clean")),
    "WASH_WITH": (("清洗", "洗"), ("wash", "clean")),
    "FILL_FROM": (("接水", "装水", "注水", "灌水", "接满"), ("fill", "filling")),
    "POUR_INTO": (("倒入", "倒进", "倒到", "倾倒"), ("pour", "into")),
    "FASTEN_WITH": (("拧", "拧紧", "固定", "紧固"), ("fasten", "screw", "tighten")),
    "PRESS": (("按下", "按压", "按"), ("press", "push")),
    "OPEN": (("打开", "开启"), ("open",)),
    "CLOSE": (("关闭", "关上", "合上"), ("close",)),
    "DISCARD_IN": (("丢进", "扔进", "丢弃", "回收"), ("discard", "throw away", "recycle")),
    "USE_WITH": (("用", "使用"), (" use ", "use the", "with the")),
}


def _step_relation_aligned(result):
    if result.get("status") != "executable":
        return None
    relation = result.get("relation_type")
    allowed = _RELATION_STEP_FAMILIES.get(relation)
    if relation == "CUSTOM":
        allowed = {"CUSTOM"}
    if not allowed:
        return False
    return any(str(step.get("action", "")).upper() in allowed
               for step in result.get("ordered_high_level_steps", []))


def _bilingual_relation_cues_present(result):
    if result.get("status") != "executable":
        return None
    zh = result.get("vla_instruction_zh", "").casefold()
    en = " " + result.get("vla_instruction_en", "").casefold() + " "
    cues = _RELATION_LANGUAGE_CUES.get(result.get("relation_type"))
    if not cues:
        return bool(zh.strip() and en.strip())
    return any(term in zh for term in cues[0]) and any(term in en for term in cues[1])


def summarize(records, benchmark, primary_run_label=None):
    if primary_run_label is None:
        primary_run_label = "main"
        if PROMPT_FREEZE.exists():
            primary_run_label = json.loads(PROMPT_FREEZE.read_text(encoding="utf-8")).get("run_label", "main")
    primary = [record for record in records if record.get("run_label") == primary_run_label]
    case_map = {case["case_id"]: case for case in benchmark["cases"]}
    rows = []
    for record in primary:
        case = case_map.get(record.get("case_id"))
        if not case:
            continue
        expected = case["evaluation"]
        result = record.get("result", {})
        status = result.get("status", "invalid")
        relation = result.get("relation_type", "NONE")
        selected_ids = ["selected_{}".format(i) for i, _ in enumerate(case["input"]["selected_objects"])]
        allowed_ids = set(selected_ids) | {"context_{}".format(i) for i, _ in enumerate(case["input"]["scene_context_objects"])}
        referenced = result.get("referenced_object_ids", [])
        structural_refs = (
            result.get("selected_object_ids") == selected_ids
            and isinstance(referenced, list) and set(referenced).issubset(allowed_ids)
            and result.get("source_object_id") in allowed_ids | {None}
            and result.get("target_or_tool_object_id") in allowed_ids | {None}
            and all(step.get("object_id") in allowed_ids and step.get("target_object_id") in allowed_ids | {None}
                    for step in result.get("ordered_high_level_steps", []))
        )
        text = "{} {}".format(result.get("vla_instruction_zh", ""), result.get("vla_instruction_en", ""))
        provenance = result.get("provenance", {})
        diagnostics = provenance.get("diagnostics", [])
        flagged_errors = [error for item in diagnostics for error in item.get("validation_errors", [])]
        forbidden_mentions = [term for term in expected.get("forbidden_object_mentions", [])
                              if term.casefold() in text.casefold()]
        colors = [item.get("color") for item in expected.get("explicit_colors", [])]
        descriptor_objects = case["input"]["selected_objects"] + case["input"]["scene_context_objects"]
        input_color_names = set()
        from integration.semantic_language_bridge import parse_object_descriptor
        for index, descriptor in enumerate(descriptor_objects):
            parsed = parse_object_descriptor(descriptor, object_id="audit_{}".format(index))
            if parsed.get("color"):
                input_color_names.add(parsed["color"])
        inferred_colors = _invented_color_mentions(text, input_color_names,
                                                    {parse_object_descriptor(value, object_id="x")["object_name"] for value in descriptor_objects})
        state_errors = _state_claim_errors(text, [
            parse_object_descriptor(value, object_id="audit_{}".format(index))
            for index, value in enumerate(descriptor_objects)
        ])
        explicit_state_items = expected.get("explicit_states", [])
        state_text = text.casefold()
        step_actions = {str(step.get("action", "")).upper() for step in result.get("ordered_high_level_steps", [])}
        state_ack = []
        for item in explicit_state_items:
            state_name = item["state"]
            if state_name == "closed":
                state_ack.append(any(term in state_text for term in ("closed", "关闭", "关上")) or {"OPEN", "CLOSE"}.issubset(step_actions))
            elif state_name == "open":
                state_ack.append("open" in state_text or "打开" in state_text or "OPEN" in step_actions)
            elif state_name == "occupied":
                state_ack.append(result.get("status") == "invalid" or any(term in state_text for term in ("occupied", "已占用", "被占用")))
            else:
                state_ack.append(True)
        rows.append({
            "case_id": record["case_id"], "split": record["split"], "category": record["category"],
            "expected_status": expected["expected_status"], "actual_status": status,
            "status_correct": status == expected["expected_status"],
            "expected_relation": expected["acceptable_relations"], "actual_relation": relation,
            "relation_correct": status == "executable" and relation in expected["acceptable_relations"],
            "structural_references_correct": structural_refs,
            "hallucinated_reference_errors": sum(1 for value in flagged_errors if "reference" in value or "hallucinated" in value),
            "hallucinated_forbidden_mentions": forbidden_mentions,
            "explicit_color_fidelity": all(_has_color(result.get("vla_instruction_zh", ""), color)
                                             and _has_color(result.get("vla_instruction_en", ""), color)
                                             for color in colors) if colors else None,
            "unobserved_color_claims": inferred_colors,
            "unobserved_state_claims": state_errors,
            "explicit_state_acknowledgement": all(state_ack) if state_ack else None,
            "explicit_state_safe_outcome": not state_errors and not (
                any(item.get("state") == "occupied" for item in explicit_state_items)
                and status == "executable" and relation == "CHARGE_WITH"
            ) if explicit_state_items else None,
            "bilingual_instruction_present": bool(result.get("vla_instruction_zh") and result.get("vla_instruction_en")),
            "bilingual_relation_cues_present": _bilingual_relation_cues_present(result),
            "high_level_step_relation_aligned": _step_relation_aligned(result),
            "api_latency_ms": provenance.get("api_latency_ms"),
            "total_latency_ms": provenance.get("total_latency_ms"),
            "retries": provenance.get("retries", 0),
            "usage": provenance.get("usage", {}),
        })

    def rate(key, subset=None):
        values = [row[key] for row in (rows if subset is None else subset) if isinstance(row.get(key), bool)]
        return {"numerator": sum(values), "denominator": len(values),
                "rate": round(sum(values) / len(values), 4) if values else None}

    executable = [row for row in rows if row["expected_status"] == "executable"]
    actual_executable = [row for row in rows if row["actual_status"] == "executable"]
    ambiguous_or_invalid = [row for row in rows if row["expected_status"] in {"ambiguous", "invalid"}]
    explicit_state_rows = [row for row in rows if row.get("explicit_state_acknowledgement") is not None]
    latencies = [row["api_latency_ms"] for row in rows if isinstance(row["api_latency_ms"], (int, float))]
    retry_rows = [row for row in rows if (row["retries"] or 0) > 0]
    usage = {key: sum(int(row["usage"].get(key, 0) or 0) for row in rows)
             for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    families = {}
    for family in sorted({row["category"] for row in rows}):
        subset = [row for row in rows if row["category"] == family]
        families[family] = {
            "cases": len(subset), "status_accuracy": rate("status_correct", subset),
            "relation_accuracy_on_executable_labels": rate("relation_correct", [row for row in subset if row["expected_status"] == "executable"]),
            "reference_correctness": rate("structural_references_correct", subset),
            "hallucinated_forbidden_mentions": sum(len(row["hallucinated_forbidden_mentions"]) for row in subset),
        }
    repeats = {}
    by_repeat_key = {}
    for record in records:
        by_repeat_key.setdefault(record.get("case_id"), []).append(record)
    structure_fields = ("status", "selected_object_ids", "source_object_id", "target_or_tool_object_id",
                       "relation_type", "custom_relation", "ordered_high_level_steps", "referenced_object_ids")
    structure_matches, prose_matches, repeated_cases = [], [], []
    for case_id, group in by_repeat_key.items():
        main = next((item for item in group if item.get("run_label") == primary_run_label), None)
        repeat = next((item for item in group if item.get("run_label", "").startswith("repeat")), None)
        if main and repeat:
            a, b = main.get("result", {}), repeat.get("result", {})
            structure_matches.append(all(a.get(field) == b.get(field) for field in structure_fields))
            prose_matches.append((a.get("vla_instruction_zh"), a.get("vla_instruction_en")) ==
                                 (b.get("vla_instruction_zh"), b.get("vla_instruction_en")))
            repeated_cases.append(case_id)
    return {
        "schema_version": "m31-open-world-bridge-summary-v1", "updated_at_utc": _now(),
        "primary_run_label": primary_run_label,
        "benchmark_sha256": json.loads(LOCK.read_text(encoding="utf-8"))["canonical_sha256"],
        "case_count": len(rows), "split_counts": {split: sum(row["split"] == split for row in rows) for split in ("train", "dev", "heldout")},
        "status_accuracy": rate("status_correct"),
        "relation_accuracy_on_expected_executable_cases": rate("relation_correct", executable),
        "grounding_reference_correctness": rate("structural_references_correct"),
        "hallucinated_reference_validation_events": sum(row["hallucinated_reference_errors"] for row in rows),
        "manual_forbidden_object_mention_cases": sum(bool(row["hallucinated_forbidden_mentions"]) for row in rows),
        "explicit_color_fidelity": rate("explicit_color_fidelity"),
        "unobserved_color_claims": sum(len(row["unobserved_color_claims"]) for row in rows),
        "unobserved_state_claims": sum(len(row["unobserved_state_claims"]) for row in rows),
        "explicit_state_acknowledgement": rate("explicit_state_acknowledgement", explicit_state_rows),
        "explicit_state_safe_outcome": rate("explicit_state_safe_outcome", explicit_state_rows),
        "bilingual_instruction_presence_on_expected_executable_cases": rate("bilingual_instruction_present", executable),
        "bilingual_relation_cue_consistency": rate("bilingual_relation_cues_present", actual_executable),
        "high_level_step_relation_alignment": rate("high_level_step_relation_aligned", executable),
        "ambiguous_invalid_handling_accuracy": rate("status_correct", ambiguous_or_invalid),
        "api_latency_ms": {"mean": round(statistics.mean(latencies), 3) if latencies else None,
                           "median": round(statistics.median(latencies), 3) if latencies else None,
                           "p90": _percentile(latencies, .90), "p95": _percentile(latencies, .95),
                           "max": round(max(latencies), 3) if latencies else None},
        "retry_rate": {"numerator": len(retry_rows), "denominator": len(rows),
                       "rate": round(len(retry_rows) / len(rows), 4) if rows else None},
        "token_usage": usage,
        "repeatability": {"cases": repeated_cases,
                          "structural_exact_rate": round(sum(structure_matches) / len(structure_matches), 4) if structure_matches else None,
                          "prose_exact_rate": round(sum(prose_matches) / len(prose_matches), 4) if prose_matches else None},
        "by_category": families,
        "case_metrics": rows,
    }


def write_summary(records, benchmark, primary_run_label=None):
    summary = summarize(records, benchmark, primary_run_label)
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _freeze_prompt(records, benchmark, model_id, run_label):
    main_train_dev = [record for record in records if record.get("run_label") == run_label and record.get("split") in {"train", "dev"}]
    expected = benchmark["split_counts"]["train"] + benchmark["split_counts"]["dev"]
    seen = {record["case_id"] for record in main_train_dev}
    if len(seen) != expected:
        raise ValueError("cannot freeze prompt until all train/dev benchmark cases have recorded outcomes")
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    frozen = {
        "schema_version": "m31-prompt-freeze-v1", "frozen_at_utc": _now(),
        "benchmark_sha256": lock["canonical_sha256"], "model_id": model_id,
        "run_label": run_label,
        "prompt_version": M31_PROMPT_VERSION, "schema_version_used": M31_SCHEMA_VERSION,
        "engine_version": M31_ENGINE_VERSION, "temperature": 0.0,
        "train_dev_case_count": expected, "heldout_used_for_prompt_tuning": False,
    }
    PROMPT_FREEZE.write_text(json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return frozen


def run(args):
    validation = validate()
    benchmark = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    cases = [case for case in benchmark["cases"] if args.split == "all" or case["split"] == args.split]
    if args.case_id:
        wanted = set(args.case_id)
        cases = [case for case in cases if case["case_id"] in wanted]
        if wanted - {case["case_id"] for case in cases}:
            raise ValueError("requested case ID is not in the selected split")
    records = _load_records()
    if args.split == "heldout":
        if not PROMPT_FREEZE.exists():
            raise ValueError("held-out cases require a saved train/dev prompt freeze")
        frozen = json.loads(PROMPT_FREEZE.read_text(encoding="utf-8"))
        if (frozen.get("benchmark_sha256") != validation["benchmark_sha256"]
                or frozen.get("prompt_version") != M31_PROMPT_VERSION
                or frozen.get("engine_version") != M31_ENGINE_VERSION
                or frozen.get("run_label") != args.run_label):
            raise ValueError("current prompt/engine differs from the train/dev freeze")
    existing = {(record.get("run_label"), record.get("case_id")) for record in records}
    pending = [case for case in cases if (args.run_label, case["case_id"]) not in existing]
    if not pending:
        print("No pending cases for run label {} / split {}".format(args.run_label, args.split))
    client, model_id, _available, _configured = build_live_client()
    if args.split == "heldout":
        frozen = json.loads(PROMPT_FREEZE.read_text(encoding="utf-8"))
        if frozen.get("model_id") != model_id:
            raise ValueError("resolved live model differs from the train/dev prompt freeze")
    bridge = SemanticLanguageBridge(client, model_id)
    for index, case in enumerate(pending, 1):
        request = case["input"]
        result = bridge.generate_instruction(
            request["selected_objects"], scene_context=request["scene_context_objects"] or None,
            task_context=request["task_context"],
        )
        record = {
            "recorded_at_utc": _now(), "run_label": args.run_label,
            "case_id": case["case_id"], "split": case["split"], "category": case["category"],
            "input": request, "evaluation": case["evaluation"], "result": result,
        }
        with RESULTS.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
        records.append(record)
        print("{}/{} {} status={} relation={} latency_ms={} retries={}".format(
            index, len(pending), case["case_id"], result.get("status"), result.get("relation_type"),
            result.get("provenance", {}).get("api_latency_ms"), result.get("provenance", {}).get("retries"),
        ), flush=True)
    summary_label = args.run_label if not args.run_label.startswith("repeat") else None
    summary = write_summary(records, benchmark, primary_run_label=summary_label)
    if args.freeze_prompt:
        frozen = _freeze_prompt(records, benchmark, model_id, args.run_label)
        print("PROMPT_FROZEN={}".format(frozen["prompt_version"]))
    print("SUMMARY={} cases={} status_accuracy={} relation_accuracy={}".format(
        SUMMARY, summary["case_count"], summary["status_accuracy"]["rate"],
        summary["relation_accuracy_on_expected_executable_cases"]["rate"],
    ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("train", "dev", "heldout", "all"), required=True)
    parser.add_argument("--run-label", default="main")
    parser.add_argument("--case-id", action="append", help="run a selected case; can be repeated")
    parser.add_argument("--freeze-prompt", action="store_true", help="freeze current versions after complete train/dev recording")
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ValueError, RuntimeError) as error:
        print("M31 runner stopped: {}: {}".format(type(error).__name__, error))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
